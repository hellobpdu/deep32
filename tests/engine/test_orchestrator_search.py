import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_core import orchestrator
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.retrieval_types import SearchHit

QUERY = "Why is the NSSA ABR not translating type-7 LSA to type-5 in OSPF?"

CONFIG = {
    "rrf_k": 60,
    "candidate_limit_per_query": 10,
    "fused_limit": 5,
    "retriever_weights": {"bm25": 1.0, "dense": 1.0},
    "retrievers": ["bm25", "dense"],
    "technical_variant_retrievers": ["bm25"],
}

# /search response keys production clients (deep32-gateway) rely on; new keys may be added, none removed
RESPONSE_KEYS = {"run_id", "query", "mode", "capabilities", "active_retrievers", "task_errors",
                 "query_variants", "candidates", "challenge_candidates", "trace_path"}


class Stub:
    name = ""
    ranking: list[str] = []
    calls: list[str] = []

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50):
        type(self).calls.append(variant.purpose)
        return [SearchHit(retriever=self.name, variant_id=variant.variant_id, purpose=variant.purpose,
                          chunk_id=cid, doc_id="rfc3101", rank=i + 1, score=1.0 / (i + 1),
                          text=f"text of {cid}", heading_path=["RFC 3101", "3.2"])
                for i, cid in enumerate(self.ranking[:limit])]


class BM25(Stub):
    name, ranking = "bm25", ["a", "b", "c", "d", "f", "g"]


class Dense(Stub):
    name, ranking = "dense", ["c", "a", "e"]


class DenseDown(Stub):
    name = "dense"

    def search(self, variant, *, limit=50):
        raise ConnectionError("embedding service unreachable")


class ReverseReranker:
    fail = False

    def __init__(self, cfg):
        self.candidates = int(cfg["candidates"])

    def rerank(self, query, items):
        if ReverseReranker.fail:
            raise TimeoutError("rerank timed out")
        assert all("text" in i for i in items), "the reranker needs the chunk text"
        return list(reversed(items))


class OrchestratorSearchTests(unittest.TestCase):
    """RetrievalOrchestrator.search() had no test; the rerank path is what the per-root overlay turns on."""

    def search(self, config=None, factories=None, limit=None):
        BM25.calls, Dense.calls = [], []
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "retrieval.json"
            cfg.write_text(json.dumps({**CONFIG, **(config or {})}))
            with mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"}), \
                    mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, factories or {"bm25": BM25, "dense": Dense},
                                    clear=True), \
                    mock.patch.object(orchestrator, "Reranker", ReverseReranker):
                orch = RetrievalOrchestrator(paths=mock.Mock(logs=Path(d) / "logs"))
                return orch.search(QUERY, limit=limit)

    def ids(self, result):
        return [c["chunk_id"] for c in result["candidates"]]

    def test_response_contract_and_fusion(self):
        result = self.search()
        self.assertLessEqual(RESPONSE_KEYS, set(result))
        self.assertIsNone(result["rerank"])
        self.assertEqual(result["task_errors"], [])
        self.assertEqual(len(result["candidates"]), 5)
        self.assertIn(self.ids(result)[0], {"a", "c"})          # found by both retrievers
        for c in result["candidates"]:
            self.assertNotIn("text", c)
            self.assertTrue(c["snippet"].startswith("text of"))

    def test_technical_variant_runs_on_bm25_only(self):
        self.search()
        self.assertEqual(sorted(BM25.calls), ["primary", "technical"])
        self.assertEqual(Dense.calls, ["primary"])

    def test_a_failing_retriever_is_reported_and_bm25_still_answers(self):
        result = self.search(factories={"bm25": BM25, "dense": DenseDown})
        self.assertEqual({e["retriever"] for e in result["task_errors"]}, {"dense"})
        self.assertEqual(self.ids(result), BM25.ranking[:5])

    def test_rerank_reorders_the_pool_then_cuts_to_the_limit(self):
        fused = self.ids(self.search(limit=5))
        result = self.search({"reranker": {"enabled": True, "url": "http://127.0.0.1:9/x", "candidates": 4}}, limit=3)
        self.assertEqual(self.ids(result), list(reversed(fused[:4]))[:3])
        self.assertEqual(result["rerank"]["applied"], True)
        self.assertEqual(result["rerank"]["candidates"], 4)
        self.assertTrue(result["capabilities"]["reranker"])
        for c in result["candidates"]:
            self.assertNotIn("text", c)

    def test_rerank_failure_keeps_the_fused_order(self):
        fused = self.ids(self.search(limit=3))
        ReverseReranker.fail = True
        try:
            result = self.search({"reranker": {"enabled": True, "url": "http://127.0.0.1:9/x", "candidates": 4}},
                                 limit=3)
        finally:
            ReverseReranker.fail = False
        self.assertEqual(self.ids(result), fused)
        self.assertEqual(result["rerank"]["applied"], False)
        self.assertIn("TimeoutError", result["rerank"]["error"])
        self.assertIn("reranker", {e["retriever"] for e in result["task_errors"]})


if __name__ == "__main__":
    unittest.main()
