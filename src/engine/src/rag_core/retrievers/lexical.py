from __future__ import annotations

import heapq
import itertools
import json
import os
import re
import threading

from rag_core.db import DEFAULT_FTS, OPTIN_DOCS, OPTIN_FTS, connect, is_split
from rag_core.paths import RAGPaths

from rag_core.retrieval_types import (
    QueryVariant,
    SearchHit,
)


TOKEN_RE = re.compile(
    r"[A-Za-z0-9_./:%-]+",
    re.UNICODE,
)

# English function words that turn an OR query into "match almost every
# chunk". Only all-lowercase alphabetic tokens are dropped, so acronyms and
# identifiers (AS, IS, OR, IS-IS, 65535:666, ip.ospf) always survive.
STOPWORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having
he her here hers herself him himself his how i if in into is it its itself just let me more most my myself
no nor of off on once only or other ought our ours ourselves out over own same she should so some such than
that the their theirs them themselves then there these they this those through to too under until up very
was we were what when where which while who whom why will with would you your yours yourself yourselves
get gets got happen happens happening use used using way ways need needs want wants tell explain describe
""".split())

SEARCHABLE_STATUSES = ("CHUNKED", "INDEXING", "READY")

# Rank in FTS5 alone, then read only the rows that made the cut. A natural-language OR query
# matches ~99% of a 43k-chunk corpus; joining chunks and documents for every match before the
# sort was most of the BM25 time, and so of /v2/search (about 0.5-0.9 s on the production CPU).
RANK_SQL = """
SELECT rowid, bm25(chunk_fts, 1.0, 2.0) AS raw_score
FROM chunk_fts
WHERE chunk_fts MATCH ?
ORDER BY raw_score ASC
LIMIT ?
"""

ROWS_SQL = """
SELECT rowid, chunk_id, doc_id, text, heading_path_json, page_start, page_end, metadata_json
FROM chunks
WHERE rowid IN ({marks})
"""

# The fast path with some documents left out (API v2's opt-in doc types): their chunks' rowids, read
# once from idx_chunks_doc_ordinal (a covering index, so no chunk row is read), are dropped before the
# sort. The list names the documents left out, never the whole corpus as an IN list would.
EXCLUDING_RANK_SQL = """
SELECT rowid, bm25(chunk_fts, 1.0, 2.0) AS raw_score
FROM chunk_fts
WHERE chunk_fts MATCH ?
    AND rowid NOT IN (SELECT rowid FROM chunks WHERE doc_id IN ({marks}))
ORDER BY raw_score ASC
LIMIT ?
"""

# The joined shape: a document filter (API v2), or a root where some chunk belongs to no
# searchable document (a build in progress, a failed document). Same scores, same order.
JOINED_SQL = """
SELECT
    chunks.rowid AS rowid,
    chunks.chunk_id,
    chunks.doc_id,
    chunks.text,
    chunks.heading_path_json,
    chunks.page_start,
    chunks.page_end,
    chunks.metadata_json,
    bm25(chunk_fts, 1.0, 2.0) AS raw_score
FROM chunk_fts
JOIN chunks ON chunks.rowid = chunk_fts.rowid
JOIN documents ON documents.doc_id = chunks.doc_id
WHERE
    chunk_fts MATCH ?
    AND documents.status IN ('CHUNKED', 'INDEXING', 'READY')
    {only}
ORDER BY raw_score ASC
LIMIT ?
"""

# Some chunk the joined SQL would leave out: its document is not searchable, or is missing.
UNSEARCHABLE_SQL = """
SELECT 1 FROM chunks
WHERE doc_id NOT IN (
    SELECT doc_id FROM documents WHERE status IN ('CHUNKED', 'INDEXING', 'READY')
)
LIMIT 1
"""

# The same for one BM25 table of a split root (rag_core.db.split_optin_fts): only the chunks that table holds, so an
# unsearchable opt-in document (an incomplete build) never sends a default search to the joined SQL, nor a default
# one an opt-in search; per table, the fact is that of a root built with that table's documents alone.
TABLE_UNSEARCHABLE_SQL = {
    table: f"""
SELECT 1 FROM chunks
WHERE doc_id NOT IN (
    SELECT doc_id FROM documents WHERE status IN ('CHUNKED', 'INDEXING', 'READY')
)
    AND doc_id {op} (SELECT doc_id FROM {OPTIN_DOCS})
LIMIT 1
"""
    for table, op in ((DEFAULT_FTS, "NOT IN"), (OPTIN_FTS, "IN"))
}


def for_table(sql: str, table: str) -> str:
    """One of the statements above over another BM25 table of the same definition (rag_core.db.OPTIN_FTS)."""
    return sql if table == DEFAULT_FTS else sql.replace(DEFAULT_FTS, table)


# (RANK_SQL, EXCLUDING_RANK_SQL, JOINED_SQL) per BM25 table: chunk_fts's are the statements above, unchanged
STATEMENTS = {
    table: (for_table(RANK_SQL, table), for_table(EXCLUDING_RANK_SQL, table), for_table(JOINED_SQL, table))
    for table in (DEFAULT_FTS, OPTIN_FTS)
}

# the documents of a split root's chunk_fts_optin (rag_core.db.split_optin_fts) that have chunks
OPTIN_CHUNKED_SQL = """
SELECT DISTINCT doc_id FROM chunks WHERE doc_id IN (SELECT doc_id FROM chunk_fts_optin_docs)
"""


class Split:
    """A root that keeps the opt-in doc types' chunks in chunk_fts_optin (rag_core.db.split_optin_fts): `docs`,
    the documents recorded there, and `chunked`, those of them with chunks."""

    def __init__(self, docs: frozenset[str], chunked: frozenset[str]) -> None:
        self.docs = docs
        self.chunked = chunked


def read_split(conn) -> Split | None:
    """The root's Split; None when it has one BM25 table (every root before C16, and one built without opt-in
    doc types)."""
    if not is_split(conn):
        return None
    return Split(
        frozenset(r[0] for r in conn.execute(f"SELECT doc_id FROM {OPTIN_DOCS}")),
        frozenset(r[0] for r in conn.execute(OPTIN_CHUNKED_SQL)),
    )


def query_tokens(
    text: str,
    *,
    drop_stopwords: bool = False,
) -> list[str]:
    """The distinct tokens (first spelling, case-insensitive) that fts_query ORs together."""
    seen = set()
    unique = []

    for token in TOKEN_RE.findall(text):
        key = token.casefold()

        if key in seen:
            continue

        seen.add(key)
        unique.append(token)

    if drop_stopwords:
        kept = [
            t for t in unique
            if not (t.isalpha() and t.islower() and t in STOPWORDS)
        ]
        if kept:
            unique = kept

    return unique


def match_expression(tokens: list[str]) -> str:
    """FTS5 MATCH text: each token a quoted phrase, ORed."""
    return " OR ".join(
        '"'
        + token.replace(
            '"',
            '""',
        )
        + '"'
        for token in tokens
    )


def fts_query(
    text: str,
    *,
    drop_stopwords: bool = False,
) -> str:
    return match_expression(
        query_tokens(text, drop_stopwords=drop_stopwords)
    )


def without_high_df(tokens: list[str], high_df: frozenset[str]) -> list[str]:
    """`tokens` minus the single index terms found in at least half of the rows.

    FTS5's bm25() gives a phrase the IDF log((N - n + 0.5) / (n + 0.5)) and floors it at 1e-6
    when that is <= 0, i.e. when n >= N/2: such a term adds at most a few 1e-6 to a score and
    only widens the OR match set (corpus-v1: 25 terms, the/is/of/1/2/command/configuration...).
    A token with punctuation is a multi-term phrase in FTS5 and always stays. Returns [] when
    nothing would be left."""
    return [
        t for t in tokens
        if not (t.isascii() and t.isalnum() and t.lower() in high_df)
    ]


def read_high_df_terms(conn, table: str = DEFAULT_FTS) -> frozenset[str]:
    """Index terms in at least half of the rows of `table` (a TEMP fts5vocab table): chunk_fts, or a split
    root's chunk_fts_optin, each with its own rows."""
    vocab = "bm25_vocab" if table == DEFAULT_FTS else f"bm25_vocab_{table}"
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS temp.{vocab} "
        f"USING fts5vocab(main, {table}, row)"
    )
    n = conn.execute(f"SELECT count(*) FROM {table}_docsize").fetchone()[0]
    if not n:
        return frozenset()
    return frozenset(
        row[0]
        for row in conn.execute(
            f"SELECT term FROM temp.{vocab} WHERE 2 * doc >= ?", (n,)
        )
    )


def merge_ranked(parts: list[list[tuple]], limit: int) -> list[tuple]:
    """The ranked (row, raw_score) lists of a split root's two BM25 tables as one: by raw score, the
    lower (better) first; a tie goes to the earlier list (chunk_fts before chunk_fts_optin), then to
    the order within its list; the top `limit`. Each list is in raw-score order (ORDER BY raw_score),
    so this is deterministic. Each table scores with its own N, document frequencies and average
    length, so scores of the two compare only roughly: a search asks both only when docs names
    documents of both kinds, or the root serves fewer opt-in doc types than it was built with."""
    return list(itertools.islice(heapq.merge(*parts, key=lambda pair: pair[1]), limit))


class IndexFacts:
    """Facts about one version of rag.db, computed once and dropped when the file changes (its
    inode, size or mtime, or those of a WAL that holds frames): a served root never changes, a root
    being built does."""

    def __init__(self, db_path) -> None:
        self.db_path = db_path          # a path, or a callable returning one
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._values: dict[str, object] = {}

    def _signature(self) -> tuple:
        """rag.db's (inode, mtime, size), and the WAL's only when it holds frames. SQLite deletes the
        WAL when the last connection closes (CPython closes one when it is collected) and the next
        connection makes a new, empty one; an empty or missing WAL holds no change. A write leaves
        frames in the WAL, and a checkpoint writes rag.db, so every real change still shows."""
        sig = []
        path = self.db_path() if callable(self.db_path) else self.db_path
        for suffix in ("", "-wal"):
            try:
                st = os.stat(f"{path}{suffix}")
            except FileNotFoundError:
                sig.append(None)
                continue
            if suffix and not st.st_size:
                sig.append(None)
            else:
                sig.append((st.st_ino, st.st_mtime_ns, st.st_size))
        return tuple(sig)

    def get(self, name: str, compute):
        """The cached value of `name` for the current version of the file; compute() otherwise."""
        key = self._signature()
        with self._lock:
            if key != self._key:
                self._values = {}
                self._key = key
            if name not in self._values:
                self._values[name] = compute()
            return self._values[name]


class LexicalRetriever:
    name = "bm25"

    def __init__(
        self,
        paths: RAGPaths,
        options: dict | None = None,
    ):
        self.paths = paths
        options = options or {}
        # retrieval.json "bm25": {"drop_stopwords": true}
        self.drop_stopwords = bool(
            options.get("drop_stopwords", False)
        )
        # retrieval.json "bm25": {"prune_high_df": false} ranks the full OR query every time
        self.prune_high_df = bool(
            options.get("prune_high_df", True)
        )
        self.facts = IndexFacts(lambda: self.db_path)

    @property
    def db_path(self):
        return self.paths.sqlite / "rag.db"

    def all_searchable(self, conn, table: str | None = None) -> bool:
        """True when every chunk belongs to a searchable document, so ranking in FTS5 alone
        returns exactly what the joined SQL returns. `table`, on a split root: every chunk that
        table holds (TABLE_UNSEARCHABLE_SQL), asked only when some chunk of the root is not."""
        every = self.facts.get(
            "all_searchable",
            lambda: conn.execute(UNSEARCHABLE_SQL).fetchone() is None,
        )
        if every or table is None or self.split(conn) is None:
            return every
        return self.facts.get(
            f"all_searchable:{table}",
            lambda: conn.execute(TABLE_UNSEARCHABLE_SQL[table]).fetchone() is None,
        )

    def high_df_terms(self, conn, table: str = DEFAULT_FTS) -> frozenset[str]:
        """Terms in at least half of the rows of `table`, read once per index version; none (no
        pruning) when the index cannot say."""

        def compute() -> frozenset[str]:
            try:
                return read_high_df_terms(conn, table)
            except Exception:           # e.g. no fts5vocab: rank the full query
                return frozenset()

        return self.facts.get("high_df" if table == DEFAULT_FTS else f"high_df:{table}", compute)

    def split(self, conn) -> Split | None:
        """The root's opt-in BM25 table (read_split), read once per index version."""
        return self.facts.get("split", lambda: read_split(conn))

    def warm(self) -> None:
        """Compute the per-index facts now (service start) instead of in the first search."""
        if not self.db_path.is_file():
            return
        with connect(self.db_path) as conn:
            self.all_searchable(conn)
            split = self.split(conn)
            if split is not None:
                for table in (DEFAULT_FTS, OPTIN_FTS):
                    self.all_searchable(conn, table)
            if self.prune_high_df:
                self.high_df_terms(conn)
                if split is not None:
                    self.high_df_terms(conn, OPTIN_FTS)

    def _ranked_rows(
        self,
        conn,
        tokens: list[str],
        limit: int,
        exclude: frozenset[str] | None = None,
        table: str = DEFAULT_FTS,
    ) -> list[tuple] | None:
        """(row, raw_score) of the top `limit` chunks of `table`: FTS5 ranks, then only those rows
        are read. None when a ranked rowid has no chunk row (an index out of step with its table):
        the joined SQL answers then, as it always did. `exclude`: documents whose chunks are left
        out (EXCLUDING_RANK_SQL), in the pruned query and the full one alike. The high-df terms are
        those of `table`."""
        rank_sql, excluding_sql, _ = STATEMENTS[table]
        sql, left_out = rank_sql, ()
        if exclude:
            sql = excluding_sql.format(marks=",".join("?" for _ in exclude))
            left_out = tuple(sorted(exclude))

        ranked = None

        if self.prune_high_df:
            kept = without_high_df(tokens, self.high_df_terms(conn, table))
            if kept and len(kept) < len(tokens):
                ranked = conn.execute(sql, (match_expression(kept), *left_out, limit)).fetchall()
                if len(ranked) < limit:
                    # fewer rows than asked for without the high-df terms: the full query pads
                    # the list with rows that match only them, so it answers exactly as before
                    ranked = None

        if ranked is None:
            ranked = conn.execute(sql, (match_expression(tokens), *left_out, limit)).fetchall()
        return self._rows_for(conn, ranked)

    def _table_rows(
        self,
        conn,
        tokens: list[str],
        limit: int,
        table: str,
        only: frozenset[str] | None,
        exclude: frozenset[str] | None,
    ) -> list[tuple]:
        """(row, raw_score) of the top `limit` chunks of one BM25 table, as p1.5 ranked chunk_fts:
        all of its documents, or all but `exclude`, ranked in FTS5 alone (_ranked_rows) when every
        chunk it holds is searchable; else, and for `only` (these documents), the joined SQL."""
        ordered = None
        if only is None and self.all_searchable(conn, table):
            ordered = self._ranked_rows(conn, tokens, limit, exclude, table)
        if ordered is None:
            # only these documents (API v2 filters), all but these (opt-in doc types), or a
            # root with unsearchable documents
            listed = only if only is not None else exclude
            restriction = (
                ""
                if listed is None
                else ("AND chunks.doc_id IN (" if only is not None else "AND chunks.doc_id NOT IN (")
                + ",".join("?" for _ in listed)
                + ")"
            )
            ordered = [
                (row, row["raw_score"])
                for row in conn.execute(
                    STATEMENTS[table][2].format(only=restriction),
                    (
                        match_expression(tokens),
                        *sorted(listed or ()),
                        limit,
                    ),
                ).fetchall()
            ]
        return ordered

    @staticmethod
    def _split_parts(
        split: Split,
        doc_ids: frozenset[str] | None,
        exclude: frozenset[str] | None,
    ) -> list[tuple[str, frozenset[str] | None, frozenset[str] | None]]:
        """The BM25 tables a search of a split root asks, each as (table, only, left out).

        doc_ids: those chunk_fts holds, then those chunk_fts_optin holds, each table only when
        some are its own (a doc_type or docs naming opt-in documents only: chunk_fts_optin alone).
        Otherwise every document but `exclude`: chunk_fts, leaving out those of `exclude` it holds,
        which are none when `exclude` is the opt-in documents (a default search: p1.4's statements,
        no exclusion clause); and chunk_fts_optin only when it holds chunks of a document not left
        out (a root served with fewer opt-in doc types than it was built with)."""
        if doc_ids is not None:
            parts = [(DEFAULT_FTS, doc_ids - split.docs, None), (OPTIN_FTS, doc_ids & split.docs, None)]
            return [part for part in parts if part[1]]
        left_out = exclude or frozenset()
        parts = [(DEFAULT_FTS, None, (left_out - split.docs) or None)]
        if split.chunked - left_out:
            parts.append((OPTIN_FTS, None, (left_out & split.docs) or None))
        return parts

    @staticmethod
    def _rows_for(conn, ranked: list) -> list[tuple] | None:
        rowids = [r[0] for r in ranked]
        if not rowids:
            return []
        rows = {
            r["rowid"]: r
            for r in conn.execute(
                ROWS_SQL.format(marks=",".join("?" for _ in rowids)), rowids
            )
        }
        if len(rows) != len(set(rowids)):
            return None
        return [(rows[rowid], score) for rowid, score in ranked]

    def search(
        self,
        variant: QueryVariant,
        *,
        limit: int = 50,
        doc_ids: frozenset[str] | None = None,
        exclude_doc_ids: frozenset[str] | None = None,
    ) -> list[SearchHit]:
        """doc_ids: only these documents (API v2 filters); exclude_doc_ids: not these (API v2's
        opt-in doc types), which keeps the rank-only fast path (EXCLUDING_RANK_SQL).

        A root that keeps the opt-in doc types' chunks in chunk_fts_optin (Split) answers from the
        table that holds the documents asked for (_split_parts): a search that leaves exactly them
        out runs p1.4's statements over chunk_fts, which holds exactly the other chunks."""

        tokens = query_tokens(
            variant.text,
            drop_stopwords=self.drop_stopwords,
        )

        exclude = exclude_doc_ids or None
        if doc_ids is not None and exclude is not None:
            doc_ids, exclude = doc_ids - exclude, None      # one restriction

        if not tokens or doc_ids is not None and not doc_ids:
            return []

        with connect(self.db_path) as conn:
            split = self.split(conn)
            if split is None:
                ordered = self._table_rows(conn, tokens, limit, DEFAULT_FTS, doc_ids, exclude)
            else:
                parts = [
                    self._table_rows(conn, tokens, limit, table, only, left_out)
                    for table, only, left_out in self._split_parts(split, doc_ids, exclude)
                ]
                ordered = parts[0] if len(parts) == 1 else merge_ranked(parts, limit)

        hits = []

        for rank, (row, raw_score) in enumerate(
            ordered,
            start=1,
        ):
            try:
                heading_path = json.loads(
                    row[
                        "heading_path_json"
                    ]
                    or "[]"
                )
            except Exception:
                heading_path = []

            try:
                metadata = json.loads(
                    row["metadata_json"]
                    or "{}"
                )
            except Exception:
                metadata = {}

            hits.append(
                SearchHit(
                    retriever=self.name,
                    variant_id=(
                        variant.variant_id
                    ),
                    purpose=(
                        variant.purpose
                    ),
                    chunk_id=(
                        row["chunk_id"]
                    ),
                    doc_id=(
                        row["doc_id"]
                    ),
                    rank=rank,
                    score=-float(
                        raw_score
                    ),
                    text=row["text"],
                    heading_path=(
                        heading_path
                    ),
                    page_start=(
                        row["page_start"]
                    ),
                    page_end=(
                        row["page_end"]
                    ),
                    metadata=metadata,
                )
            )

        return hits
