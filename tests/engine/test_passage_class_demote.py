"""0.1.0-alpha.3, WP-D item D7 (a bench switch, default unchanged): retrieval.json passage_class.demote
(rag_core.fusion.passage_class_demote, rag_core.orchestrator). The candidates of the listed classes go behind the
others after the fusion, before the reranker takes its candidates. Without the setting, and on a root whose rag.db
has no passage_class table (every root until a knowledge base is built with one), searches are the ones before."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest
from unittest import mock

from rag_core.db import connect
from rag_core.fusion import PASSAGE_CLASSES, demoted, passage_class_demote

import test_fold_identical
from test_api_v2 import AUTH, RootClient, WordReranker

QUERY = "NSSA translator Type-7 LSAs"
DEMOTE = {"passage_class": {"demote": ["toc", "legal"]}}


def with_classes(paths, kb_src):
    """The table as the knowledge base build will write it (INTERFACES 3), empty here."""
    with connect(paths.sqlite / "rag.db") as conn:
        conn.execute("CREATE TABLE passage_class (chunk_id TEXT PRIMARY KEY, class TEXT NOT NULL)")
        conn.commit()


class SetWithoutTheTableIsTheBodyBefore(test_fold_identical.OffTests):
    """The setting on a root without the table: the bodies 4f4fe78 sent."""
    retrieval = DEMOTE


class EmptyIsTheBodyBefore(test_fold_identical.OffTests):
    retrieval = {"passage_class": {"demote": []}}


class SettingTests(unittest.TestCase):

    def test_values(self):
        for config in ({}, {"passage_class": None}, {"passage_class": {}}, {"passage_class": {"demote": []}}):
            self.assertEqual(passage_class_demote(config), ())
        self.assertEqual(passage_class_demote({"passage_class": {"demote": ["toc", "legal", "toc"]}}),
                         ("toc", "legal"))
        self.assertEqual(passage_class_demote({"passage_class": {"demote": list(PASSAGE_CLASSES)}}), PASSAGE_CLASSES)

    def test_other_values_are_refused(self):
        for bad in (["toc"], "toc", {"demote": "toc"}, {"demote": ["index"]}, {"demote": [1]}, {"classes": []}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "passage_class"):
                passage_class_demote({"passage_class": bad})
        with self.assertRaisesRegex(ValueError, "passage_class.demote"):
            RootClient(retrieval={"passage_class": {"demote": ["boilerplate"]}})

    def test_demoted_keeps_each_group_in_its_order(self):
        rows = [{"chunk_id": c} for c in "abcde"]
        self.assertEqual([c["chunk_id"] for c in demoted(rows, {"b", "d"})], list("acebd"))
        self.assertEqual(demoted(rows, set()), rows)

    def test_a_root_without_the_table_demotes_nothing_and_says_so_once(self):
        with self.assertLogs("rag_core.orchestrator", "WARNING") as logged:
            root = RootClient(retrieval=DEMOTE)
        self.addCleanup(root.close)
        self.assertEqual(root.client.app.state.orchestrator.demote, ())
        self.assertIn("no passage_class table", logged.output[0])


class DemoteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=with_classes, retrieval=DEMOTE)
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator
        # the classes go to the query's own candidates: its first is "toc", its third "legal", its second "content"
        cls.before = cls.fused(())
        cls.toc, cls.content, cls.legal = cls.before[0], cls.before[1], cls.before[2]
        with connect(cls.root.paths.sqlite / "rag.db") as conn:
            conn.executemany("INSERT INTO passage_class VALUES (?, ?)",
                             [(cls.toc, "toc"), (cls.content, "content"), (cls.legal, "legal")])
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    @classmethod
    def fused(cls, demote, **kw):
        with mock.patch.object(cls.orch, "demote", demote):
            return [c["chunk_id"] for c in cls.orch.search(QUERY, limit=24, rerank=False, **kw)["candidates"]]

    def test_the_start_found_the_table_and_keeps_the_setting(self):
        self.assertEqual(self.orch.demote, ("toc", "legal"))

    def test_the_listed_classes_go_behind_the_others(self):
        before = self.before
        self.assertGreater(len(before), 5)
        after = self.fused(("toc", "legal"))
        rest = [c for c in before if c not in (self.toc, self.legal)]
        self.assertEqual(after, rest + [self.toc, self.legal])              # each group in its order
        self.assertEqual(after[0], self.content)                            # "content" is not listed: it leads now
        # only the listed classes move
        self.assertEqual(self.fused(("legal",)), [c for c in before if c != self.legal] + [self.legal])
        self.assertEqual(self.fused(("content",)), [c for c in before if c != self.content] + [self.content])
        self.assertEqual(self.fused(("references",)), before)

    def test_the_reranker_takes_its_candidates_after_the_demotion(self):
        reranker = WordReranker()
        reranker.candidates = 3
        with mock.patch.object(self.orch, "reranker", reranker):
            body = self.c.post("/v2/search", json={"query": QUERY, "rerank": "on", "limit": 30},
                               headers=AUTH).json()
        (_, sent), = reranker.calls
        self.assertEqual(sent, self.fused(("toc", "legal"))[:3])
        self.assertFalse({self.toc, self.legal} & set(sent))
        self.assertEqual(self.before[:3], [self.toc, self.content, self.legal])     # without it, these three
        by_id = {h["id"]: h for h in body["hits"]}
        self.assertEqual([by_id[c]["reranked"] for c in (self.toc, self.legal) if c in by_id],
                         [False] * len([c for c in (self.toc, self.legal) if c in by_id]))

    def test_search_many_fuses_the_demoted_ranks(self):
        def ranks(demote):
            with mock.patch.object(self.orch, "demote", demote):
                hits = self.c.post("/v2/search_many", json={
                    "queries": [{"text": QUERY}], "limit": 30, "rerank": "off"}, headers=AUTH).json()["hits"]
            return {h["id"]: h["per_query_ranks"]["0"] for h in hits}

        before, after = ranks(()), ranks(("toc", "legal"))
        self.assertEqual((before[self.toc], before[self.content]), (1, 2))
        self.assertEqual(after[self.content], 1)
        self.assertEqual(after[self.toc], len(self.before) - 1)             # behind the others, before "legal"


if __name__ == "__main__":
    unittest.main()
