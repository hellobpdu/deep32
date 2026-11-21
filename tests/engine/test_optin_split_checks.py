"""C16 review fixes, on the twin roots of tests/test_optin_fts.py.

- An unsearchable document (an incomplete build) is a fact of the BM25 table that holds its chunks: an opt-in one
  leaves a default search on p1.4's RANK_SQL over chunk_fts, as on the twin without the opt-in documents.
- build_root's bm25 step checks a split table's index alone (rag_core.db.INDEX_CHECK_SQL): each table holds part of
  chunks, and a check that compares it with chunks reports it corrupt (rank 1 everywhere; every form on SQLite
  3.33.0). Where no such check exists (fts5_checks_index_alone), the rows are proved and the report says so.
- scripts/chunk_order.py: whether two roots hold their shared chunks in the same rowid order, the order in which
  BM25 breaks an exact tie."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import contextlib
import importlib.util
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_core.db import (
    DEFAULT_FTS,
    INDEX_CHECK_SQL,
    OPTIN_FTS,
    check_optin_split,
    connect,
    fts5_checks_index_alone,
)
from rag_core.paths import RAGPaths
from rag_core.retrievers import lexical
from rag_core.retrievers.lexical import LexicalRetriever, merge_ranked

from test_build_root import REPO
from test_optin_fts import (
    OPT_IN_TYPES,
    P14_JOINED_SQL,
    P14_RANK_SQL,
    QUERIES,
    SCRIPT_MODULE,
    Recording,
    bm25_step,
    doc_ids_of,
    key,
    make_root,
    ranked,
    recorded,
    variant,
)


def copy_root(paths: RAGPaths, base: Path, name: str) -> RAGPaths:
    target = base / name / "root"
    shutil.copytree(paths.root, target)
    return RAGPaths(target)


def set_status(paths: RAGPaths, doc_id: str, status: str) -> None:
    with connect(paths.sqlite / "rag.db") as conn:
        conn.execute("UPDATE documents SET status = ? WHERE doc_id = ?", (status, doc_id))


def matching(statements) -> list[str]:
    return [sql for sql, _ in statements if "MATCH" in sql]


class UnsearchableDocumentPerTableTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.plain = make_root(cls.base, "plain", optin=False, split=False)
        cls.split = make_root(cls.base, "split")
        cls.optin = make_root(cls.base, "optin", default=False, split=False)
        cls.excluded = doc_ids_of(cls.split, OPT_IN_TYPES)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_failed_opt_in_document_leaves_a_default_search_on_p14_s_rank_sql(self):
        failed = copy_root(self.split, self.base, "failed-optin")
        system = sorted(doc_ids_of(failed, ["system"]))[0]
        set_status(failed, system, "FAILED")
        for prune in (True, False):
            options = {"prune_high_df": prune}
            for text in QUERIES:
                for limit in (1, 8, 50):
                    with self.subTest(text=text, limit=limit, prune=prune):
                        hits, statements = recorded(LexicalRetriever(failed, options), variant(text), limit=limit,
                                                    exclude_doc_ids=self.excluded)
                        plain_hits, plain_sql = recorded(LexicalRetriever(self.plain, options), variant(text),
                                                         limit=limit)
                        self.assertEqual(key(hits), key(plain_hits))
                        self.assertEqual(statements, plain_sql)
                        self.assertEqual(set(matching(statements)) or {P14_RANK_SQL}, {P14_RANK_SQL})
        bm25 = LexicalRetriever(failed)
        bm25.warm()
        facts = bm25.facts._values
        self.assertEqual((facts["all_searchable"], facts[f"all_searchable:{DEFAULT_FTS}"],
                          facts[f"all_searchable:{OPTIN_FTS}"]), (False, True, False))
        # an opt-in search leaves the failed document out, as the joined SQL always did
        hits, statements = recorded(bm25, variant("Get-NetIPAddress interface router"), limit=50)
        self.assertNotIn(system, {h.doc_id for h in hits})
        self.assertEqual({sql for sql in matching(statements) if "chunk_fts_optin" in sql},
                         {P14_JOINED_SQL.replace("chunk_fts", "chunk_fts_optin").format(only="")})

    def test_a_failed_default_document_leaves_chunk_fts_optin_on_its_rank_sql(self):
        # a root served with no opt-in doc type left out asks both tables: chunk_fts as the plain twin with the same
        # failed document does (the joined SQL), chunk_fts_optin as the opt-in twin does (rank-only)
        failed = copy_root(self.split, self.base, "failed-default")
        plain_failed = copy_root(self.plain, self.base, "plain-failed")
        rfc = sorted(doc_ids_of(failed, ["rfc"]))[0]
        for paths in (failed, plain_failed):
            set_status(paths, rfc, "FAILED")
        for text in QUERIES:
            with self.subTest(text=text):
                hits, statements = recorded(LexicalRetriever(failed), variant(text), limit=8)
                merged = merge_ranked([ranked(LexicalRetriever(plain_failed).search(variant(text), limit=8)),
                                       ranked(LexicalRetriever(self.optin).search(variant(text), limit=8))], 8)
                self.assertEqual(ranked(hits), merged)
                self.assertNotIn(rfc, {h.doc_id for h in hits})
                optin_sql = {sql for sql in matching(statements) if "chunk_fts_optin MATCH" in sql}
                self.assertEqual(optin_sql or {lexical.for_table(P14_RANK_SQL, OPTIN_FTS)},
                                 {lexical.for_table(P14_RANK_SQL, OPTIN_FTS)})
                self.assertEqual({sql for sql in matching(statements) if "chunk_fts MATCH" in sql},
                                 {P14_JOINED_SQL.format(only="")})

    def test_a_complete_split_root_computes_the_whole_root_fact_only(self):
        bm25 = LexicalRetriever(self.split)
        bm25.warm()
        self.assertTrue(bm25.facts._values["all_searchable"])
        self.assertFalse([name for name in bm25.facts._values if name.startswith("all_searchable:")])


class Emulated333(Recording):
    """A connection of an SQLite that compares every FTS5 integrity-check with the content table (3.33.0): a split
    table, which holds part of chunks, fails each form."""

    def execute(self, sql, params=()):
        if "integrity-check" in sql:
            self.statements.append((sql, tuple(params)))
            raise sqlite3.DatabaseError("database disk image is malformed")
        return super().execute(sql, params)


class SplitIntegrityCheckTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.p15 = make_root(cls.base, "p15", split=False)             # one chunk_fts holding every chunk

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def step(self, name, opt_in, wrapper=Recording, index_check=None):
        """build_root's bm25 step on a copy of the p15 root, with every statement it runs."""
        paths = copy_root(self.p15, self.base, name)
        statements = []
        real = SCRIPT_MODULE.connect
        patches = [mock.patch.object(SCRIPT_MODULE, "connect", lambda path: wrapper(real(path), statements))]
        if index_check is not None:
            patches.append(mock.patch.object(SCRIPT_MODULE, "fts5_checks_index_alone", lambda: index_check))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            report = bm25_step(paths, opt_in)
        return paths, report, [sql for sql, _ in statements if "integrity-check" in sql]

    def test_this_sqlite_checks_an_index_alone(self):
        # verified: 3.39.4, 3.45.1 and 3.53.1 do; 3.33.0 compares every form with the content table
        if sqlite3.sqlite_version_info >= (3, 39, 4):
            self.assertTrue(fts5_checks_index_alone())

    def test_a_split_root_is_checked_by_its_index_alone(self):
        if not fts5_checks_index_alone():
            self.skipTest(f"SQLite {sqlite3.sqlite_version} has no check of an index alone")
        paths, report, checks = self.step("split", OPT_IN_TYPES)
        self.assertEqual(checks, [INDEX_CHECK_SQL.format(table=t) for t in (DEFAULT_FTS, OPTIN_FTS)])
        with connect(paths.sqlite / "rag.db") as conn:
            self.assertEqual(report, {"fts_integrity": "ok", "chunks": report["chunks"],
                                      "tables": check_optin_split(conn), "opt_in_documents": 3,
                                      "sqlite": sqlite3.sqlite_version})
            # the premise: a check that compares a split table with chunks reports it corrupt
            for table in (DEFAULT_FTS, OPTIN_FTS):
                with self.assertRaisesRegex(sqlite3.DatabaseError, "malformed"):
                    conn.execute(f"INSERT INTO {table}({table}, rank) VALUES ('integrity-check', 1)")

    def test_an_sqlite_that_compares_every_check_gets_the_row_proof(self):
        paths, report, checks = self.step("old", OPT_IN_TYPES, wrapper=Emulated333, index_check=False)
        self.assertEqual(checks, [])
        self.assertEqual(report["fts_integrity"], "skipped")
        self.assertIn(f"SQLite {sqlite3.sqlite_version} compares", report["fts_integrity_skipped"])
        with connect(paths.sqlite / "rag.db") as conn:
            self.assertEqual(report["tables"], check_optin_split(conn))
        self.assertEqual(report["sqlite"], sqlite3.sqlite_version)
        # with a check of the index alone claimed, that SQLite stops the step loudly, never silently
        with self.assertRaisesRegex(sqlite3.DatabaseError, "malformed"):
            self.step("old-claimed", OPT_IN_TYPES, wrapper=Emulated333, index_check=True)

    def test_a_root_without_the_split_keeps_p14_s_check_and_report(self):
        _, report, checks = self.step("plain", None)
        self.assertEqual(checks, ["INSERT INTO chunk_fts(chunk_fts) VALUES ('integrity-check')"])
        self.assertEqual(set(report), {"fts_integrity", "chunks"})
        self.assertEqual(report["fts_integrity"], "ok")


def load_chunk_order():
    spec = importlib.util.spec_from_file_location("chunk_order", REPO / "scripts" / "chunk_order.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHUNK_ORDER = load_chunk_order()


def run_chunk_order(*roots) -> tuple[int, dict | None, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = CHUNK_ORDER.main([str(r) for r in roots])
    return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()


def rechunk(paths: RAGPaths, doc_id: str) -> int:
    """The document's chunks deleted and inserted again, as a resumed build re-chunks it: later rowids."""
    conn = sqlite3.connect(paths.sqlite / "rag.db")
    try:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(chunks)") if r[1] != "rowid"]
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM chunks WHERE doc_id = ? ORDER BY rowid",
                            (doc_id,)).fetchall()
        with conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.executemany(f"INSERT INTO chunks ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                             rows)
        return len(rows)
    finally:
        conn.close()


class ChunkOrderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.plain = make_root(cls.base, "plain", optin=False, split=False)
        cls.split = make_root(cls.base, "split")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def count(self, paths, sql="SELECT count(*) FROM chunks", params=()):
        with connect(paths.sqlite / "rag.db") as conn:
            return conn.execute(sql, params).fetchone()[0]

    def test_twins_hold_their_shared_chunks_in_the_same_order(self):
        code, summary, _ = run_chunk_order(self.plain.root, self.split.root)
        self.assertEqual(code, 0)
        n_plain, n_split = self.count(self.plain), self.count(self.split)
        self.assertEqual(summary["chunks"], {"a": n_plain, "b": n_split, "shared": n_plain, "only_a": 0,
                                             "only_b": n_split - n_plain})
        self.assertGreater(summary["chunks"]["only_b"], 0)
        self.assertEqual((summary["same_order"], summary["first_difference"], summary["moved"], summary["changed"]),
                         (True, None, 0, 0))

    def test_a_re_chunked_document_is_reported_moved(self):
        resumed = copy_root(self.plain, self.base, "resumed")
        with connect(resumed.sqlite / "rag.db") as conn:
            docs = [r[0] for r in conn.execute(
                "SELECT doc_id FROM chunks GROUP BY doc_id HAVING count(*) > 1 ORDER BY min(rowid)")]
            first_rowid = conn.execute("SELECT min(rowid) FROM chunks WHERE doc_id = ?", (docs[0],)).fetchone()[0]
            position = conn.execute("SELECT count(*) FROM chunks WHERE rowid < ?", (first_rowid,)).fetchone()[0]
        moved = rechunk(resumed, docs[0])
        code, summary, _ = run_chunk_order(resumed.root, self.split.root)
        self.assertEqual(code, 1)
        self.assertFalse(summary["same_order"])
        self.assertEqual(summary["moved_by_document"], {docs[0]: moved})
        self.assertEqual(summary["moved"], moved)
        self.assertEqual(summary["first_difference"]["position"], position)
        self.assertEqual(summary["chunks"]["shared"], self.count(self.plain))
        # the same rows with the same scores: only an exact tie can come the other way
        for text in QUERIES:
            with self.subTest(text=text):
                self.assertEqual(
                    {(h.chunk_id, h.score) for h in LexicalRetriever(resumed).search(variant(text), limit=1000)},
                    {(h.chunk_id, h.score) for h in LexicalRetriever(self.plain).search(variant(text), limit=1000)})

    def test_a_changed_chunk_is_counted(self):
        changed = copy_root(self.plain, self.base, "changed")
        with connect(changed.sqlite / "rag.db") as conn:
            conn.execute("UPDATE chunks SET chunk_sha256 = 'other' WHERE rowid = (SELECT min(rowid) FROM chunks)")
        code, summary, _ = run_chunk_order(changed.root, self.plain.root)
        self.assertEqual((code, summary["same_order"], summary["changed"]), (0, True, 1))

    def test_a_root_without_a_database(self):
        code, summary, err = run_chunk_order(self.base / "nowhere", self.plain.root)
        self.assertEqual((code, summary), (2, None))
        self.assertIn("no rag.db", err)

    def test_the_longest_common_order(self):
        self.assertEqual(CHUNK_ORDER.in_order([]), set())
        kept = CHUNK_ORDER.in_order([3, 1, 2, 5, 4, 6])
        self.assertEqual(len(kept), 4)
        values = [[3, 1, 2, 5, 4, 6][i] for i in sorted(kept)]
        self.assertEqual(values, sorted(values))


if __name__ == "__main__":
    unittest.main()
