"""C16: the opt-in doc types' chunks in a BM25 table of their own, chunk_fts_optin (rag_core.db.split_optin_fts,
build_root --opt-in-doc-types), so that a default search runs p1.4's statements over a chunk_fts holding exactly the
default chunks: the rows, N, document frequencies, average lengths and high-df terms of a root built without the
opt-in documents at all.

Twin roots of tests/v2_root.py: "plain" (the default documents only), "split" (the same documents with the same
rowids, then the opt-in ones in chunk_fts_optin), "optin" (the opt-in documents only, in the same order) and "p15"
(all of them in one chunk_fts, as p1.5 built them). Each goes through build_root's bm25 step."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import contextlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_core.db import (
    DEFAULT_FTS,
    OPTIN_DOCS,
    OPTIN_FTS,
    check_optin_split,
    connect,
    has_table,
    initialize,
    is_split,
)
from rag_core.paths import RAGPaths
from rag_core.retrieval_types import QueryVariant
from rag_core.retrievers import lexical
from rag_core.retrievers.lexical import LexicalRetriever, merge_ranked, read_split
from rag_core.status import corpus_stats

import test_opt_in_collections as opt_in_api
from test_api_v2 import AUTH, RootClient
from test_build_root import DOCS as BUILD_DOCS, REPO, SCRIPT, load_script, make_corpus
from v2_root import OPT_IN_DOCS, add_document, build_root

OPT_IN_TYPES = ["system", "powershell"]
DEFAULT_EXTRA = [d for d in OPT_IN_DOCS if d[1] not in OPT_IN_TYPES]       # the ISE API guide: a cisco document
OPTIN = [d for d in OPT_IN_DOCS if d[1] in OPT_IN_TYPES]

QUERIES = [
    "NSSA Type-7 translator", "IP address interface", "the ospf area", "BGP decision process tie breaking",
    "how does a border router elect the translator", "Get-NetIPAddress", "P-bit forwarding address 0.0.0.0",
    "network device ERS API", "splatting hashtable parameters", "Type-7", "area 1 nssa translate type7 always",
    "what is the default TranslatorStabilityInterval", "LSA", "the of and",
]

# p1.4's BM25 statements (rag_core.retrievers.lexical in p1.4), byte for byte
P14_RANK_SQL = """
SELECT rowid, bm25(chunk_fts, 1.0, 2.0) AS raw_score
FROM chunk_fts
WHERE chunk_fts MATCH ?
ORDER BY raw_score ASC
LIMIT ?
"""
P14_JOINED_SQL = """
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

SCRIPT_MODULE = load_script()


def bm25_step(paths: RAGPaths, opt_in=None) -> dict:
    """build_root's bm25 step on a fixture root (a fixture document's doc type is its source name)."""
    report = {}
    with mock.patch.object(SCRIPT_MODULE, "log"):
        SCRIPT_MODULE.step_bm25(report, paths.sqlite / "rag.db", opt_in)
    return report["bm25"]


def make_root(base: Path, name: str, *, default=True, optin=True, split=True) -> RAGPaths:
    """A twin root: the default documents (v2_root's DOCS and the ISE guide) unless not `default`, then the opt-in
    ones if `optin`, through build_root's bm25 step (with --opt-in-doc-types if `split`)."""
    if default:
        paths = build_root(base / name, with_kb_sources=False)
        for doc in DEFAULT_EXTRA:
            add_document(paths, *doc)
    else:
        paths = RAGPaths(base / name / "root")
        initialize(paths)
    for doc in OPTIN if optin else ():
        add_document(paths, *doc)
    bm25_step(paths, OPT_IN_TYPES if split else None)
    return paths


def doc_ids_of(paths: RAGPaths, doc_types) -> frozenset[str]:
    marks = ",".join("?" for _ in doc_types)
    with connect(paths.sqlite / "rag.db") as conn:
        return frozenset(r[0] for r in conn.execute(
            f"SELECT doc_id FROM documents WHERE source_name IN ({marks})", list(doc_types)))


def key(hits) -> list[tuple]:
    return [(h.chunk_id, h.doc_id, h.rank, h.score, h.text) for h in hits]


def ranked(hits) -> list[tuple]:
    """The hits as merge_ranked takes them: (row, raw_score), ranks left out."""
    return [((h.chunk_id, h.doc_id), -h.score) for h in hits]


class Recording:
    """A rag.db connection that records every statement it executes, with its parameters."""

    def __init__(self, conn, statements):
        self.conn, self.statements = conn, statements

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        return self.conn.execute(sql, params)

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self.conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self.conn, name)


def recorded(bm25: LexicalRetriever, *args, **kwargs):
    """(hits, statements) of one search, the per-index facts computed beforehand (warm)."""
    bm25.warm()
    statements = []
    real = lexical.connect
    with mock.patch.object(lexical, "connect", lambda path: Recording(real(path), statements)):
        hits = bm25.search(*args, **kwargs)
    return hits, statements


def variant(text: str) -> QueryVariant:
    return QueryVariant("primary-x", "primary", text)


class TwinRootTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.plain = make_root(base, "plain", optin=False, split=False)
        cls.split = make_root(base, "split")
        cls.optin = make_root(base, "optin", default=False, split=False)
        cls.p15 = make_root(base, "p15", split=False)
        cls.excluded = doc_ids_of(cls.split, OPT_IN_TYPES)          # what api_v2.filter_docs leaves out

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def db(self, paths):
        conn = connect(paths.sqlite / "rag.db")
        self.addCleanup(conn.close)
        return conn

    def test_the_twins_share_the_default_chunks_and_their_rowids(self):
        def rowids(paths):
            return dict(self.db(paths).execute("SELECT chunk_id, rowid FROM chunks WHERE doc_id NOT IN "
                                               f"({','.join('?' for _ in self.excluded)})", sorted(self.excluded)))

        self.assertEqual(rowids(self.split), rowids(self.plain))
        self.assertEqual(len(self.excluded), 3)
        split = read_split(self.db(self.split))
        self.assertEqual((split.docs, split.chunked), (self.excluded, self.excluded))
        self.assertIsNone(read_split(self.db(self.plain)))
        self.assertIsNone(read_split(self.db(self.p15)))

    def test_chunk_fts_optin_has_chunk_fts_s_definition(self):
        conn = self.db(self.split)
        ddl = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE name IN (?, ?)", (DEFAULT_FTS, OPTIN_FTS)))
        self.assertEqual(ddl[OPTIN_FTS], ddl[DEFAULT_FTS].replace("chunk_fts", "chunk_fts_optin"))
        self.assertIn("content='chunks'", ddl[OPTIN_FTS])
        self.assertIn("tokenize='unicode61 remove_diacritics 2'", ddl[OPTIN_FTS])
        # only a root with opt-in doc types has the table
        for paths in (self.plain, self.p15, self.optin):
            self.assertFalse(has_table(self.db(paths), OPTIN_FTS))
            self.assertFalse(is_split(self.db(paths)))

    def test_each_table_holds_exactly_the_chunks_of_its_documents(self):
        conn = self.db(self.split)
        counts = check_optin_split(conn)
        n_plain = self.db(self.plain).execute("SELECT count(*) FROM chunks").fetchone()[0]
        n_optin = self.db(self.optin).execute("SELECT count(*) FROM chunks").fetchone()[0]
        self.assertEqual(counts, {DEFAULT_FTS: n_plain, OPTIN_FTS: n_optin})
        default_rows = {r[0] for r in conn.execute(f"SELECT id FROM {DEFAULT_FTS}_docsize")}
        optin_rows = {r[0] for r in conn.execute(f"SELECT id FROM {OPTIN_FTS}_docsize")}
        self.assertFalse(default_rows & optin_rows)                               # never both
        self.assertEqual(default_rows | optin_rows, {r[0] for r in conn.execute("SELECT rowid FROM chunks")})
        # FTS5's averages record (N and the token totals): chunk_fts's is the plain twin's
        averages = "SELECT block FROM {}_data WHERE id = 1"
        self.assertEqual(conn.execute(averages.format(DEFAULT_FTS)).fetchone()[0],
                         self.db(self.plain).execute(averages.format(DEFAULT_FTS)).fetchone()[0])
        self.assertEqual(conn.execute(averages.format(OPTIN_FTS)).fetchone()[0],
                         self.db(self.optin).execute(averages.format(DEFAULT_FTS)).fetchone()[0])

    def test_every_default_search_is_the_plain_twin_s_with_p14_s_statements(self):
        compared = 0
        for prune in (True, False):
            for stop in (False, True):
                options = {"prune_high_df": prune, "drop_stopwords": stop}
                for text in QUERIES:
                    for limit in (1, 3, 8, 50):
                        with self.subTest(text=text, limit=limit, **options):
                            plain_hits, plain_sql = recorded(LexicalRetriever(self.plain, options), variant(text),
                                                             limit=limit)
                            split_hits, split_sql = recorded(LexicalRetriever(self.split, options), variant(text),
                                                             limit=limit, exclude_doc_ids=self.excluded)
                            self.assertEqual(key(split_hits), key(plain_hits))
                            # the same statements with the same parameters (rowids included), p1.4's rank-only one
                            self.assertEqual(split_sql, plain_sql)
                            matching = [sql for sql, _ in split_sql if "MATCH" in sql]
                            self.assertTrue(matching)
                            self.assertEqual(set(matching), {P14_RANK_SQL})
                            self.assertFalse([sql for sql, _ in split_sql if "chunk_fts_optin" in sql])
                            compared += bool(plain_hits)
        self.assertGreater(compared, 150)

    def test_the_high_df_terms_are_each_table_s_own(self):
        bm25 = LexicalRetriever(self.split)
        conn = self.db(self.split)
        default, optin = bm25.high_df_terms(conn), bm25.high_df_terms(conn, OPTIN_FTS)
        self.assertEqual(default, LexicalRetriever(self.plain).high_df_terms(self.db(self.plain)))
        self.assertEqual(optin, LexicalRetriever(self.optin).high_df_terms(self.db(self.optin)))
        self.assertNotEqual(default, optin)
        bm25.warm()
        self.assertLessEqual({"split", "high_df", f"high_df:{OPTIN_FTS}"}, set(bm25.facts._values))

    def test_an_opt_in_search_asks_chunk_fts_optin_alone(self):
        powershell = doc_ids_of(self.split, ["powershell"])
        for text in QUERIES:
            for docs in (powershell, self.excluded, doc_ids_of(self.split, ["system"])):
                with self.subTest(text=text, docs=sorted(docs)):
                    hits, statements = recorded(LexicalRetriever(self.split), variant(text), limit=8, doc_ids=docs)
                    # the scores of a root holding the opt-in documents only
                    self.assertEqual(key(hits), key(LexicalRetriever(self.optin).search(variant(text), limit=8,
                                                                                         doc_ids=docs)))
                    self.assertLessEqual({h.doc_id for h in hits}, docs)
                    only = "AND chunks.doc_id IN (" + ",".join("?" for _ in docs) + ")"
                    matching = [sql for sql, _ in statements if "MATCH" in sql]
                    self.assertEqual(matching, [P14_JOINED_SQL.replace("chunk_fts", "chunk_fts_optin")
                                                .format(only=only)])

    def test_a_filter_of_default_documents_asks_chunk_fts_alone(self):
        # release_max or include_obsolete alone: filter_docs names default documents only, p1.4's joined statement
        docs = frozenset(r[0] for r in self.db(self.plain).execute("SELECT doc_id FROM documents"))
        for text in QUERIES[:6]:
            with self.subTest(text=text):
                hits, statements = recorded(LexicalRetriever(self.split), variant(text), limit=8, doc_ids=docs)
                plain_hits, plain_sql = recorded(LexicalRetriever(self.plain), variant(text), limit=8, doc_ids=docs)
                self.assertEqual(key(hits), key(plain_hits))
                self.assertEqual(statements, plain_sql)
                only = "AND chunks.doc_id IN (" + ",".join("?" for _ in docs) + ")"
                self.assertEqual([sql for sql, _ in statements if "MATCH" in sql], [P14_JOINED_SQL.format(only=only)])

    def test_docs_of_both_kinds_merge_the_two_tables_lists_by_score(self):
        rfc = doc_ids_of(self.split, ["rfc"])
        system = doc_ids_of(self.split, ["system"])
        found_both = 0
        for text in QUERIES:
            for limit in (2, 8, 50):
                with self.subTest(text=text, limit=limit):
                    hits, statements = recorded(LexicalRetriever(self.split), variant(text), limit=limit,
                                                doc_ids=rfc | system)
                    default_part = LexicalRetriever(self.plain).search(variant(text), limit=limit, doc_ids=rfc)
                    optin_part = LexicalRetriever(self.optin).search(variant(text), limit=limit, doc_ids=system)
                    merged = merge_ranked([ranked(default_part), ranked(optin_part)], limit)
                    self.assertEqual(ranked(hits), merged)
                    self.assertEqual([h.rank for h in hits], list(range(1, len(hits) + 1)))
                    self.assertEqual(len([sql for sql, _ in statements if "MATCH" in sql]), 2)
                    kinds = {h.doc_id in system for h in hits}
                    found_both += kinds == {True, False}
        self.assertGreater(found_both, 5)

    def test_the_merge_rule(self):
        a = [("a1", -3.0), ("a2", -2.0), ("a3", -2.0)]
        b = [("b1", -2.5), ("b2", -2.0), ("b3", -1.0)]
        self.assertEqual(merge_ranked([a, b], 10), [("a1", -3.0), ("b1", -2.5), ("a2", -2.0), ("a3", -2.0),
                                                    ("b2", -2.0), ("b3", -1.0)])
        self.assertEqual(merge_ranked([a, b], 3), [("a1", -3.0), ("b1", -2.5), ("a2", -2.0)])
        self.assertEqual(merge_ranked([[], b], 2), b[:2])

    def test_a_root_served_with_fewer_opt_in_types_ranks_both_tables(self):
        # opt_in_doc_types [] on a split root (derive_root --set): nothing left out, both tables asked and merged,
        # each pruned with its own high-df terms
        for prune in (True, False):
            for text in QUERIES:
                with self.subTest(text=text, prune=prune):
                    options = {"prune_high_df": prune}
                    hits = LexicalRetriever(self.split, options).search(variant(text), limit=8)
                    merged = merge_ranked([ranked(LexicalRetriever(self.plain, options).search(variant(text), limit=8)),
                                           ranked(LexicalRetriever(self.optin, options).search(variant(text), limit=8))],
                                          8)
                    self.assertEqual(ranked(hits), merged)
        # and one opt-in doc type served (system): powershell is searched, system left out of chunk_fts_optin
        system = doc_ids_of(self.split, ["system"])
        hits, statements = recorded(LexicalRetriever(self.split), variant("IP address interface NSSA"), limit=50,
                                    exclude_doc_ids=system)
        self.assertIn("powershell", self.doc_types(self.split, hits))
        self.assertFalse({h.doc_id for h in hits} & system)
        self.assertIn("rowid NOT IN", " ".join(sql for sql, _ in statements if "chunk_fts_optin MATCH" in sql))
        self.assertIn(P14_RANK_SQL, [sql for sql, _ in statements])

    def doc_types(self, paths, hits):
        ids = {h.doc_id for h in hits}
        rows = self.db(paths).execute("SELECT doc_id, source_name FROM documents").fetchall()
        return {r["source_name"] for r in rows if r["doc_id"] in ids}

    def test_a_root_without_the_split_keeps_p15_s_exclusion_path(self):
        for text in QUERIES[:6]:
            with self.subTest(text=text):
                hits, statements = recorded(LexicalRetriever(self.p15), variant(text), limit=8,
                                            exclude_doc_ids=self.excluded)
                self.assertFalse({h.doc_id for h in hits} & self.excluded)
                marks = ",".join("?" for _ in self.excluded)
                matching = [(sql, params) for sql, params in statements if "MATCH" in sql]
                self.assertTrue(matching)
                for sql, params in matching:
                    self.assertEqual(sql, lexical.EXCLUDING_RANK_SQL.format(marks=marks))
                    self.assertEqual(params[1:-1], tuple(sorted(self.excluded)))
                # and without an exclusion, p1.4's statement
                _, plain = recorded(LexicalRetriever(self.p15), variant(text), limit=8)
                self.assertEqual({sql for sql, _ in plain if "MATCH" in sql}, {P14_RANK_SQL})

    def test_the_triggers_route_each_chunk_to_its_table(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = Path(tmp.name) / "root"
        shutil.copytree(self.split.root, target)
        conn = connect(target / "sqlite" / "rag.db")
        self.addCleanup(conn.close)
        before = check_optin_split(conn)
        system = sorted(doc_ids_of(RAGPaths(target), ["system"]))[0]
        rfc = sorted(doc_ids_of(RAGPaths(target), ["rfc"]))[0]
        insert = ("INSERT INTO chunks (chunk_id, chunk_sha256, doc_id, ordinal, heading_path_json, text, "
                  "embedding_text, token_count, char_count) VALUES (?, 'x', ?, ?, '[]', ?, ?, 2, 10)")
        with conn:
            conn.execute(insert, ("chk_new_sys", system, 900, "zebra crossing", "zebra crossing"))
            conn.execute(insert, ("chk_new_rfc", rfc, 900, "zebra stripes", "zebra stripes"))
        self.assertEqual(check_optin_split(conn), {DEFAULT_FTS: before[DEFAULT_FTS] + 1,
                                                   OPTIN_FTS: before[OPTIN_FTS] + 1})
        with conn:
            conn.execute("UPDATE chunks SET text = 'yak shaving' WHERE chunk_id = 'chk_new_sys'")
            conn.execute("UPDATE chunks SET text = 'yak hair' WHERE chunk_id = 'chk_new_rfc'")
        match = "SELECT count(*) FROM {0} WHERE {0} MATCH ?"
        self.assertEqual([conn.execute(match.format(t), ('"yak"',)).fetchone()[0] for t in (DEFAULT_FTS, OPTIN_FTS)],
                         [1, 1])
        self.assertEqual([conn.execute(match.format(t), ('"zebra"',)).fetchone()[0] for t in (DEFAULT_FTS, OPTIN_FTS)],
                         [0, 0])
        with conn:
            conn.execute("DELETE FROM chunks WHERE chunk_id IN ('chk_new_sys', 'chk_new_rfc')")
        self.assertEqual(check_optin_split(conn), before)
        for table in (DEFAULT_FTS, OPTIN_FTS):
            conn.execute(f"INSERT INTO {table}({table}) VALUES ('integrity-check')")

    def test_status_reports_the_bm25_tables(self):
        counts = check_optin_split(self.db(self.split))
        self.assertEqual(corpus_stats(self.split.sqlite / "rag.db")["bm25_tables"], counts)
        n = self.db(self.p15).execute("SELECT count(*) FROM chunks").fetchone()[0]
        self.assertEqual(corpus_stats(self.p15.sqlite / "rag.db")["bm25_tables"], {DEFAULT_FTS: n})

    def test_the_split_is_redone_from_any_state(self):
        # a resumed build: chunk_fts holding everything again (as the plain triggers fill it), another doc list
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = Path(tmp.name) / "root"
        shutil.copytree(self.p15.root, target)
        paths = RAGPaths(target)
        self.assertEqual(bm25_step(paths, ["powershell"])["tables"][OPTIN_FTS],
                         len([1 for _ in self.db(paths).execute(
                             "SELECT 1 FROM chunks JOIN documents USING (doc_id) WHERE source_name = 'powershell'")]))
        again = bm25_step(paths, OPT_IN_TYPES)
        self.assertEqual(again, bm25_step(self.split, OPT_IN_TYPES))
        self.assertEqual(again["opt_in_documents"], 3)
        self.assertEqual({r[0] for r in self.db(paths).execute(f"SELECT doc_id FROM {OPTIN_DOCS}")}, self.excluded)
        for text in QUERIES[:5]:
            self.assertEqual(key(LexicalRetriever(paths).search(variant(text), limit=8, exclude_doc_ids=self.excluded)),
                             key(LexicalRetriever(self.plain).search(variant(text), limit=8)))


def split_documents(paths: RAGPaths, kb_src) -> None:
    """RootClient's prepare: the opt-in documents, then build_root's bm25 step with --opt-in-doc-types."""
    opt_in_api.add_opt_in_documents(paths)
    bm25_step(paths, OPT_IN_TYPES)


class SplitRootApiTests(opt_in_api.OptInCollectionTests):
    """Every opt-in API test (search, search_many, dense, filters, grep, documents, status) on a split root."""

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(retrieval={"opt_in_doc_types": opt_in_api.OPT_IN}, prepare=split_documents)
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator
        cls.corp = opt_in_api.api_v2.root_reader(cls.c.app.state)

    def test_the_root_is_split(self):
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            self.assertTrue(is_split(conn))
        tables = self.get("/v2/status")["backends"]["bm25"]["tables"]
        self.assertEqual(set(tables), {DEFAULT_FTS, OPTIN_FTS})
        self.assertGreater(tables[OPTIN_FTS], 0)

    def test_a_default_search_runs_p14_s_statement_over_chunk_fts(self):
        statements = []
        real = lexical.connect
        with mock.patch.object(lexical, "connect", lambda path: Recording(real(path), statements)):
            for path, body in (("/v2/search", {"query": opt_in_api.OSPF}),
                               ("/v2/search", {"query": opt_in_api.HOST, "mode": "bm25"}),
                               ("/v2/search_many", {"queries": [{"text": opt_in_api.OSPF},
                                                                {"text": "Type-7", "mode": "bm25"}]}),
                               ("/search", {"query": opt_in_api.OSPF})):
                self.post(path, body)
        matching = [sql for sql, _ in statements if "MATCH" in sql]
        self.assertGreaterEqual(len(matching), 5)
        self.assertEqual(set(matching), {P14_RANK_SQL})
        self.assertFalse([sql for sql, _ in statements if "rowid NOT IN" in sql or "JOIN documents" in sql])

    def test_a_named_opt_in_doc_type_runs_over_chunk_fts_optin(self):
        statements = []
        real = lexical.connect
        with mock.patch.object(lexical, "connect", lambda path: Recording(real(path), statements)):
            hits = self.search(opt_in_api.HOST, mode="bm25", filters={"doc_type": "powershell"})
        self.assertEqual(self.types(hits), {"powershell"})
        matching = [sql for sql, _ in statements if "MATCH" in sql]
        self.assertTrue(matching)
        self.assertTrue(all("chunk_fts_optin MATCH" in sql for sql in matching))

    def test_a_grep_of_opt_in_documents_orders_by_chunk_fts_optin(self):
        corp = self.corp
        docs = [corp.docs[k] for k in ("rfc1587", "ps-about-splatting", "ps-nettcpip", "c9300-179-rtng")]
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            from rag_core.reader import fts_tables
            self.assertEqual(fts_tables(conn, docs), (DEFAULT_FTS, OPTIN_FTS))
            self.assertEqual(fts_tables(conn, docs[:1]), (DEFAULT_FTS,))
            self.assertEqual(fts_tables(conn, docs[1:3]), (OPTIN_FTS,))
        order = [d.doc_key for d in corp.scan_order("IP address", docs)]
        self.assertEqual(order[0], "ps-nettcpip")


class BuildAndDeriveTests(unittest.TestCase):
    """build_root --opt-in-doc-types against a build of the default documents alone; derive_root of the split."""

    OPT_IN_SOURCES = {
        "system/sys-test-notes.txt": ("deep32-engine test notes", "system",
                                      "# deep32-engine test notes\n## NSSA\nThe translator of Type-7 LSAs is elected; "
                                      "a border router with the Nt bit set always translates.\n"),
        "powershell/ps-nettcpip.txt": ("NetTCPIP", "powershell",
                                       "# NetTCPIP\n## Get-NetIPAddress\nGets the IP address configuration of an "
                                       "interface, IPv4 and IPv6, and the router of the address.\n"),
    }

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.data = base / "rag-data"
        make_corpus(base / "plain-corpus")
        make_corpus(base / "split-corpus", docs={**BUILD_DOCS, **cls.OPT_IN_SOURCES})     # the opt-in ones last
        cls.plain = cls.data / "gen-plain"
        cls.split = cls.data / "gen-split"
        cls.runs = {
            "plain": cls.build(base / "plain-corpus", cls.plain),
            "split": cls.build(base / "split-corpus", cls.split, "--opt-in-doc-types", "system,powershell"),
        }

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def build(corpus, root, *args):
        import subprocess
        import sys
        clean = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        clean.update(PYTHONPATH=str(REPO / "src"))
        return subprocess.run([sys.executable, str(SCRIPT), "--corpus-ref", "corpus-v1", "--corpus-repo",
                               str(corpus), "--root", str(root), "--no-dense", *args],
                              capture_output=True, text=True, env=clean, timeout=300)

    def report(self, root):
        return json.loads((root / "BUILD_REPORT.json").read_text(encoding="utf-8"))

    def db(self, root):
        conn = sqlite3.connect(root / "sqlite" / "rag.db")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def optin_docs(self, root):
        return frozenset(r[0] for r in self.db(root).execute(
            "SELECT doc_id FROM documents WHERE json_extract(metadata_json, '$.corpus.doc_type') IN "
            "('system', 'powershell')"))

    def assert_default_searches_match(self, root):
        excluded = self.optin_docs(self.split)
        for text in QUERIES + ["translator election Nt bit", "show ip ospf"]:
            for prune in (True, False):
                with self.subTest(root=root.name, text=text, prune=prune):
                    options = {"prune_high_df": prune}
                    plain = LexicalRetriever(RAGPaths(self.plain), options).search(variant(text), limit=8)
                    split, statements = recorded(LexicalRetriever(RAGPaths(root), options), variant(text), limit=8,
                                                 exclude_doc_ids=excluded)
                    self.assertEqual(key(split), key(plain))
                    self.assertEqual({sql for sql, _ in statements if "MATCH" in sql} or {P14_RANK_SQL},
                                     {P14_RANK_SQL})

    def test_the_build_splits_the_index_and_reports_it(self):
        for name, run in self.runs.items():
            self.assertEqual(run.returncode, 0, name + run.stdout + run.stderr)
        plain, split = self.report(self.plain), self.report(self.split)
        self.assertEqual(plain["bm25"], {"fts_integrity": "ok", "chunks": plain["chunks"]})     # as before
        self.assertFalse(has_table(self.db(self.plain), OPTIN_FTS))
        tables = split["bm25"]["tables"]
        self.assertEqual(tables, {DEFAULT_FTS: plain["chunks"],
                                  OPTIN_FTS: split["chunks"] - plain["chunks"]})
        self.assertGreater(tables[OPTIN_FTS], 0)
        self.assertEqual(split["bm25"]["opt_in_documents"], 2)
        self.assertEqual(check_optin_split(self.db(self.split)), tables)
        self.assertIn(f"{tables[OPTIN_FTS]} of 2 opt-in documents in chunk_fts_optin", self.runs["split"].stdout)
        self.assert_default_searches_match(self.split)
        # an opt-in search reaches them
        hits = LexicalRetriever(RAGPaths(self.split)).search(variant("Get-NetIPAddress interface"), limit=8,
                                                              doc_ids=self.optin_docs(self.split))
        self.assertTrue(hits)

    def test_a_resume_keeps_the_split(self):
        work = Path(self.tmp.name) / "resume" / "gen-split"
        shutil.copytree(self.split, work)
        before = self.report(work)["bm25"]
        conn = self.db(work)
        conn.execute("PRAGMA foreign_keys = ON")
        system = conn.execute("SELECT doc_id FROM documents WHERE source_id = 'sys-test-notes'").fetchone()[0]
        with conn:                                          # as if stopped after ingest: chunked again on resume
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (system,))
            conn.execute("UPDATE documents SET status = 'CANONICALIZED' WHERE doc_id = ?", (system,))
        self.assertEqual(check_optin_split(conn)[DEFAULT_FTS], before["tables"][DEFAULT_FTS])   # the triggers routed
        conn.close()
        corpus = Path(self.tmp.name) / "split-corpus"
        run = self.build(corpus, work, "--resume", "--opt-in-doc-types", "powershell,system")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("sys-test-notes", run.stdout)
        report = self.report(work)
        self.assertEqual(report["bm25"], before)
        self.assertEqual(check_optin_split(self.db(work)), before["tables"])
        self.assert_default_searches_match(work)
        # the refusal of another opt-in list stays
        refused = self.build(corpus, work, "--resume", "--opt-in-doc-types", "powershell")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("resume it the same way", refused.stdout)

    def test_a_derived_root_keeps_both_tables(self):
        spec_derive = __import__("test_derive_root").load_derive()
        env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(spec_derive, "log"), \
                mock.patch.object(spec_derive.br, "log"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(spec_derive.main(["--from", str(self.split), "--to", "gen-split-d"]), 0)
        derived = self.data / "gen-split-d"
        self.addCleanup(shutil.rmtree, derived, True)
        conn = self.db(derived)
        self.assertTrue(is_split(conn))
        self.assertEqual(check_optin_split(conn), self.report(self.split)["bm25"]["tables"])
        self.assertEqual(self.report(derived)["bm25"], self.report(self.split)["bm25"])
        triggers = "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
        self.assertEqual(conn.execute(triggers).fetchall(), self.db(self.split).execute(triggers).fetchall())
        self.assert_default_searches_match(derived)


if __name__ == "__main__":
    unittest.main()
