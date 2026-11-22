"""0.1.0-alpha.3, WP-D item D7 (a bench switch, default unchanged): retrieval.json bm25.tie_order
(rag_core.retrievers.lexical). "rowid" orders rows of equal BM25 score by row id, so the order no longer depends on
the SQLite build; "engine", the default, runs the statements of before, unchanged."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import tempfile
import unittest
from pathlib import Path

from rag_core.db import connect
from rag_core.retrieval_types import QueryVariant
from rag_core.retrievers import lexical
from rag_core.retrievers.lexical import LexicalRetriever, bm25_tie_order, merge_ranked

import test_fold_identical
from test_api_v2 import RootClient
from v2_root import add_document, build_root

# the three statements as d84ce2a has them, each ending its ORDER BY at the score
ORDER_BY = "ORDER BY raw_score ASC\nLIMIT ?\n"


class EngineIsTheBodyBefore(test_fold_identical.OffTests):
    """bm25.tie_order "engine", the default, written out: the bodies 4f4fe78 sent."""
    retrieval = {"bm25": {"tie_order": "engine"}}


def variant(text):
    return QueryVariant(variant_id="v", purpose="primary", text=text, weight=1.0)


class StatementTests(unittest.TestCase):

    def test_the_default_runs_the_statements_of_before(self):
        for sql in (lexical.RANK_SQL, lexical.EXCLUDING_RANK_SQL, lexical.JOINED_SQL):
            self.assertTrue(sql.endswith(ORDER_BY), sql)
        with tempfile.TemporaryDirectory() as d:
            paths = build_root(Path(d), with_kb_sources=False)
            for options in (None, {}, {"tie_order": "engine"}, {"drop_stopwords": True}):
                self.assertIs(LexicalRetriever(paths, options).statements, lexical.STATEMENTS)
            self.assertIs(LexicalRetriever(paths, {"tie_order": "rowid"}).statements, lexical.ROWID_STATEMENTS)

    def test_rowid_adds_the_row_id_after_the_score_and_nothing_else(self):
        for table, statements in lexical.STATEMENTS.items():
            for before, after in zip(statements, lexical.ROWID_STATEMENTS[table]):
                order = "chunks.rowid" if "JOIN chunks" in before else "rowid"
                self.assertEqual(after, before.replace("ORDER BY raw_score ASC\n", f"ORDER BY raw_score ASC, {order} ASC\n"))
                self.assertEqual(after.count("ORDER BY"), 1)

    def test_values(self):
        self.assertEqual((bm25_tie_order(None), bm25_tie_order({}), bm25_tie_order({"tie_order": "rowid"})),
                         ("engine", "engine", "rowid"))
        for bad in ("sqlite", None, True, 1):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "tie_order"):
                bm25_tie_order({"tie_order": bad})
        with self.assertRaisesRegex(ValueError, "tie_order"):
            RootClient(retrieval={"bm25": {"tie_order": "id"}})

    def test_merge_ranked_breaks_a_tie_by_row_id_only_when_asked(self):
        a = [({"rowid": 9}, -2.0), ({"rowid": 12}, -1.0)]
        b = [({"rowid": 3}, -2.0), ({"rowid": 4}, -1.0)]
        self.assertEqual([r["rowid"] for r, _ in merge_ranked([a, b], 4)], [9, 3, 12, 4])
        self.assertEqual([r["rowid"] for r, _ in merge_ranked([a, b], 4, True)], [3, 9, 4, 12])


class TieTests(unittest.TestCase):
    """Copies of one text score the same: with "rowid" they come in the order they were written."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = build_root(Path(cls.tmp.name), with_kb_sources=False)
        # six guides that differ in their title line only: the passage under it is the same text in each
        text = "The quagga zebra daemon redistributes kernel routes.\nA second line of the copy.\n"
        for n in range(6):
            add_document(cls.paths, f"copy-{n}", "cisco", f"# Copy {n}\n{text}", f"Copy {n}")
        with connect(cls.paths.sqlite / "rag.db") as conn:
            cls.rowid = dict(conn.execute("SELECT chunk_id, rowid FROM chunks"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def hits(self, options, **kw):
        return LexicalRetriever(self.paths, options).search(variant("quagga zebra daemon"), limit=50, **kw)

    def test_equal_scores_are_in_row_order(self):
        hits = self.hits({"tie_order": "rowid"})
        self.assertEqual(len(hits), 6)
        self.assertEqual(len({h.score for h in hits}), 1)                   # six copies, one score
        rows = [self.rowid[h.chunk_id] for h in hits]
        self.assertEqual(rows, sorted(rows))
        self.assertEqual([h.rank for h in hits], [1, 2, 3, 4, 5, 6])

    def test_the_same_rows_and_scores_as_the_default(self):
        engine, rowid = self.hits(None), self.hits({"tie_order": "rowid"})
        self.assertEqual(sorted((h.chunk_id, h.score) for h in engine), sorted((h.chunk_id, h.score) for h in rowid))
        # where no two scores are equal the order is the default's
        engine = LexicalRetriever(self.paths).search(variant("NSSA translator Type-7 LSAs election"), limit=50)
        rowid = LexicalRetriever(self.paths, {"tie_order": "rowid"}).search(
            variant("NSSA translator Type-7 LSAs election"), limit=50)
        if len({h.score for h in engine}) == len(engine):
            self.assertEqual([h.chunk_id for h in rowid], [h.chunk_id for h in engine])

    def test_the_filtered_and_the_excluding_statements_too(self):
        with connect(self.paths.sqlite / "rag.db") as conn:
            docs = dict(conn.execute("SELECT original_filename, doc_id FROM documents"))
        some = frozenset(docs[f"copy-{n}.pdf"] for n in (5, 1, 3))
        only = self.hits({"tie_order": "rowid"}, doc_ids=some)
        self.assertEqual([self.rowid[h.chunk_id] for h in only], sorted(self.rowid[h.chunk_id] for h in only))
        self.assertEqual({h.doc_id for h in only}, set(some))
        without = self.hits({"tie_order": "rowid"}, exclude_doc_ids=some)
        self.assertEqual([self.rowid[h.chunk_id] for h in without], sorted(self.rowid[h.chunk_id] for h in without))
        self.assertEqual(len(without), 3)


if __name__ == "__main__":
    unittest.main()
