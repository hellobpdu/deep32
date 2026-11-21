"""retrieval.json reranker.search_many_candidates (rag_core.rerank, rag_core.api_v2.rerank_head;
the T4 trial arms): /v2/search_many's head, so a deeper /v2/search can keep search_many's head where it was. Null (the
default) takes candidates, as before. The tiny root of tests/v2_root.py, BM25 only, a recording reranker."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest
from unittest import mock

from rag_core.rerank import MAX_SERVICE_DOCUMENTS, Reranker

from test_api_v2 import AUTH, RootClient, WordReranker

URL = "http://127.0.0.1:9/v1/rerank"
QUERIES = [{"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
           {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25"},
           {"text": "NSSA ABR elected translator", "mode": "bm25"}]


class SettingTests(unittest.TestCase):

    def reranker(self, **cfg):
        r = Reranker({"url": URL, **cfg})
        self.addCleanup(r.close)
        return r

    def test_null_takes_candidates(self):
        self.assertEqual(self.reranker().search_many_candidates, 10)
        self.assertEqual(self.reranker(candidates=20).search_many_candidates, 20)
        self.assertEqual(self.reranker(candidates=20, search_many_candidates=None).search_many_candidates, 20)

    def test_a_number_sets_the_head_up_to_the_service_limit(self):
        r = self.reranker(candidates=15, search_many_candidates=10)
        self.assertEqual((r.candidates, r.search_many_candidates), (15, 10))
        self.assertEqual(self.reranker(search_many_candidates=1).search_many_candidates, 1)
        self.assertEqual(self.reranker(search_many_candidates=100).search_many_candidates, MAX_SERVICE_DOCUMENTS)

    def test_other_values_are_refused(self):
        for bad in (0, -1, True, "10", 1.5, [10]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "search_many_candidates"):
                Reranker({"url": URL, "search_many_candidates": bad})


class Recording(WordReranker):
    """WordReranker with a search_many head of its own."""

    def __init__(self, search_many_candidates):
        super().__init__()
        self.search_many_candidates = search_many_candidates


class HeadTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient()
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def post(self, path, body):
        r = self.c.post(path, json=body, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def sent(self, reranker, mode="per_query"):
        with mock.patch.object(self.orch, "reranker", reranker), \
                mock.patch.dict(self.orch.config, {"search_many_rerank": mode}):
            self.post("/v2/search_many", {"queries": QUERIES, "limit": 8, "rerank": "on"})
        return {cid for _, ids in reranker.calls for cid in ids}

    def test_the_search_many_head_is_search_many_candidates(self):
        fused = [h["id"] for h in self.post("/v2/search_many", {"queries": QUERIES, "limit": 30, "rerank": "off"})["hits"]]
        self.assertGreater(len(fused), 3)
        today = self.sent(WordReranker())                           # no setting: candidates, as before
        self.assertTrue(3 < len(today) <= WordReranker.candidates, today)
        for mode in ("per_query", "best_query"):
            with self.subTest(mode=mode):
                self.assertEqual(len(self.sent(Recording(3), mode)), 3)
                self.assertEqual(self.sent(Recording(10), mode), today)

    def test_search_keeps_candidates(self):
        reranker = Recording(2)
        with mock.patch.object(self.orch, "reranker", reranker):
            self.post("/v2/search", {"query": "NSSA Type-7 translator", "mode": "bm25", "rerank": "on", "limit": 8})
        (_, ids), = reranker.calls
        self.assertGreater(len(ids), 2)


if __name__ == "__main__":
    unittest.main()
