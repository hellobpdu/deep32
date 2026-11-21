"""Per-leg timings: every retrieval leg is timed, the dense leg splits its time into TEI embed, Qdrant query and
SQLite load, and /v2/search shows them in timings_ms. The dense leg reads only chunk_id from a Qdrant point."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rag_core import orchestrator
from rag_core.api_v2 import leg_timings
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.retrieval_types import QueryVariant, SearchHit
from rag_core.retrievers.dense import DenseRetriever

VARIANT = QueryVariant("primary-x", "primary", "NSSA Type-7 translator")


def hit(name, cid, rank=1):
    return SearchHit(retriever=name, variant_id="v", purpose="primary", chunk_id=cid, doc_id="d", rank=rank,
                     score=1.0, text=cid)


class Plain:
    """A retriever that takes no timings."""
    name = "bm25"

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50, doc_ids=None):
        return [hit(self.name, "p-1")]


class Timed:
    """A retriever that reports its own split."""
    name = "dense"

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50, doc_ids=None, timings=None):
        timings.update(embed_ms=3.0, qdrant_ms=2.0, load_ms=1.0)
        return [hit(self.name, "t-1")]


class Failing(Timed):
    def search(self, variant, *, limit=50, doc_ids=None, timings=None):
        timings["embed_ms"] = 1.5
        raise TimeoutError("TEI did not answer")


class DenseTimerTests(unittest.TestCase):

    def dense(self, points):
        dense = DenseRetriever.__new__(DenseRetriever)
        dense.config = SimpleNamespace(collection="c")
        dense.embedder = SimpleNamespace(embed=lambda texts: ([[0.0]], "m"))
        self.calls = []
        dense.client = SimpleNamespace(
            query_points=lambda **kw: self.calls.append(kw) or SimpleNamespace(points=points))
        dense._load_chunks = lambda ids: {
            cid: {"doc_id": "d", "text": cid, "heading_path_json": "[]", "metadata_json": "{}", "page_start": None,
                  "page_end": None, "embedding_model": "m", "embedding_revision": None}
            for cid in ids
        }
        return dense

    def test_the_dense_leg_reports_its_split_and_reads_only_chunk_ids(self):
        dense = self.dense([SimpleNamespace(id="p1", score=0.9, payload={"chunk_id": "c1"})])
        timings = {}
        hits = dense.search(VARIANT, limit=5, timings=timings)
        self.assertEqual([h.chunk_id for h in hits], ["c1"])
        self.assertEqual(set(timings), {"embed_ms", "qdrant_ms", "load_ms"})
        self.assertTrue(all(isinstance(v, float) and v >= 0 for v in timings.values()))
        self.assertEqual(self.calls[0]["with_payload"], ["chunk_id"])
        self.assertIs(self.calls[0]["with_vectors"], False)

    def test_timings_are_optional(self):
        dense = self.dense([])
        self.assertEqual(dense.search(VARIANT, limit=5), [])


class OrchestratorLegTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Path(self.tmp.name) / "retrieval.json"
        cfg.write_text(json.dumps({
            "rrf_k": 60, "candidate_limit_per_query": 10, "fused_limit": 10,
            "retriever_weights": {}, "retrievers": ["bm25", "dense"],
        }))
        for p in (mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"}),
                  mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, {"bm25": Plain, "dense": Timed}, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def orch(self):
        return RetrievalOrchestrator(paths=mock.Mock(logs=Path(self.tmp.name) / "logs"))

    def test_every_leg_is_timed_and_a_timed_retriever_adds_its_split(self):
        result = self.orch().search("q", mode="fast")
        legs = {leg["retriever"]: leg for leg in result["legs"]}
        self.assertEqual(set(legs), {"bm25", "dense"})
        self.assertEqual(set(legs["bm25"]), {"retriever", "purpose", "ms"})
        self.assertEqual({k: legs["dense"][k] for k in ("embed_ms", "qdrant_ms", "load_ms")},
                         {"embed_ms": 3.0, "qdrant_ms": 2.0, "load_ms": 1.0})
        self.assertTrue(all(leg["ms"] >= 0 for leg in result["legs"]))

    def test_a_failed_leg_keeps_its_time(self):
        with mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, {"dense": Failing}):
            result = self.orch().search("q", mode="fast")
        dense = next(leg for leg in result["legs"] if leg["retriever"] == "dense")
        self.assertEqual(dense["embed_ms"], 1.5)
        self.assertIn("ms", dense)
        self.assertEqual(result["degraded"], ["dense"])


class TimingsMsTests(unittest.TestCase):

    def test_the_slowest_leg_of_each_retriever_and_the_dense_split(self):
        legs = [
            {"retriever": "bm25", "purpose": "primary", "ms": 12.0},
            {"retriever": "bm25", "purpose": "technical", "ms": 30.5},
            {"retriever": "dense", "purpose": "primary", "ms": 110.0, "embed_ms": 60.0, "qdrant_ms": 40.0,
             "load_ms": 5.0},
        ]
        self.assertEqual(leg_timings(legs), {"bm25": 30.5, "dense": 110.0, "dense_embed": 60.0,
                                             "dense_qdrant": 40.0, "dense_load": 5.0})
        self.assertEqual(leg_timings(None), {})
        self.assertEqual(leg_timings([{"retriever": "dense", "purpose": "primary"}]), {})



class KeepaliveTests(unittest.TestCase):
    """The query embedder keeps idle TEI connections for query_keepalive_seconds (default 300 s), the bulk embedder
    for 60 s; the reranker for keepalive_seconds (default 300 s). Checked on the transport's pool: httpx.Client
    ignores its own limits when it is given a transport, so before this the pools kept connections 5 s (httpx's
    default), not the 60 s the code asked for."""

    def pool_expiry(self, client):
        return client._transport._pool._keepalive_expiry

    def test_defaults_and_overrides(self):
        import tempfile
        from rag_core.dense import JinaEmbeddingClient, load_dense_config
        from rag_core.rerank import Reranker
        from rag_core.retrievers.dense import DenseRetriever

        # no Qdrant client: making one asks the server for its version (a connect to 127.0.0.1:6333)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ),                 mock.patch("rag_core.retrievers.dense.make_qdrant_client", return_value=mock.Mock()):
            for key in [k for k in os.environ if k.startswith("RAG_")]:
                del os.environ[key]
            root = Path(d) / "root"
            (root / "config").mkdir(parents=True)
            (root / "config" / "dense.json").write_text(json.dumps({"collection": "rag_dense_x"}))
            config = load_dense_config(root=root)
            self.assertEqual(config.query_keepalive_seconds, 300.0)
            dense = DenseRetriever.__new__(DenseRetriever)
            dense.paths = mock.Mock(root=root)
            DenseRetriever.__init__(dense, mock.Mock(root=root))
            self.assertEqual(self.pool_expiry(dense.embedder._client), 300.0)
            bulk = JinaEmbeddingClient(config)
            self.assertEqual(self.pool_expiry(bulk._client), 60.0)
            self.assertEqual(bulk._client._transport._pool._max_connections, 32)
            bulk.close()
            dense.embedder.close()
            dense.client.close()
            (root / "config" / "dense.json").write_text(json.dumps({"collection": "rag_dense_x",
                                                                    "query_keepalive_seconds": 30}))
            self.assertEqual(load_dense_config(root=root).query_keepalive_seconds, 30.0)
        r = Reranker({"url": "https://gpu-host.tailnet-example.ts.net:8443/v1/rerank"})
        self.assertEqual(self.pool_expiry(r._client), 300.0)
        r.close()
        r = Reranker({"url": "https://gpu-host.tailnet-example.ts.net:8443/v1/rerank", "keepalive_seconds": 5})
        self.assertEqual(self.pool_expiry(r._client), 5.0)
        r.close()
        with self.assertRaises(ValueError):
            Reranker({"url": "https://x/v1/rerank", "keepalive_seconds": -1})


if __name__ == "__main__":
    unittest.main()
