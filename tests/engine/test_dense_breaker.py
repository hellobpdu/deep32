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

import httpx

from rag_core import orchestrator
from rag_core.breaker import CircuitBreaker
from rag_core.dense import load_dense_config
from rag_core.errors import error_kind, public_error
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.paths import RAGPaths
from rag_core.retrieval_types import SearchHit
from rag_core.retrievers.dense import DenseRetriever

QUERY = "Why is the NSSA ABR not translating type-7 LSA to type-5 in OSPF?"
TEI_URL = "https://gpu-host.tailnet-example.ts.net/v1/embeddings"
CONFIG = {
    "rrf_k": 60, "candidate_limit_per_query": 10, "fused_limit": 5,
    "retriever_weights": {"bm25": 1.0, "dense": 1.0},
    "retrievers": ["bm25", "dense"],
    "technical_variant_retrievers": ["bm25"],          # one dense call per normal-mode search
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def http_error(status):
    request = httpx.Request("POST", TEI_URL)
    return httpx.HTTPStatusError(f"Server error '{status}' for url '{TEI_URL}'", request=request,
                                 response=httpx.Response(status, request=request))


class BreakerStateTests(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.breaker = CircuitBreaker(failures=3, open_seconds=30, clock=self.clock)

    def test_opens_after_consecutive_failures_only(self):
        b = self.breaker
        b.record_failure("timeout")
        b.record_failure("timeout")
        b.record_success()                          # resets the count
        b.record_failure("timeout")
        b.record_failure("timeout")
        self.assertEqual(b.state, "closed")
        self.assertTrue(b.allow())
        b.record_failure("connect")
        self.assertEqual(b.state, "open")
        self.assertFalse(b.allow())
        self.assertEqual(b.snapshot()["last_error_kind"], "connect")
        self.assertEqual(b.snapshot()["retry_in_seconds"], 30.0)

    def test_half_open_admits_one_trial_then_closes_on_success(self):
        b = self.breaker
        for _ in range(3):
            b.record_failure("timeout")
        self.clock.now += 29.9
        self.assertFalse(b.allow())
        self.clock.now += 0.1
        self.assertEqual(b.state, "half-open")
        self.assertTrue(b.allow())                  # the trial
        self.assertFalse(b.allow())                 # everyone else waits for it
        b.record_success()
        self.assertEqual(b.state, "closed")
        self.assertTrue(b.allow())
        self.assertEqual(b.snapshot()["consecutive_failures"], 0)

    def test_failed_trial_reopens_for_a_full_interval(self):
        b = self.breaker
        for _ in range(3):
            b.record_failure("timeout")
        self.clock.now += 30
        self.assertTrue(b.allow())
        b.record_failure("http_502")
        self.assertEqual(b.state, "open")
        self.clock.now += 29
        self.assertFalse(b.allow())
        self.clock.now += 1
        self.assertTrue(b.allow())

    def test_a_lost_trial_does_not_block_forever(self):
        b = self.breaker
        for _ in range(3):
            b.record_failure("timeout")
        self.clock.now += 30
        self.assertTrue(b.allow())                  # trial never reports back
        self.clock.now += 29
        self.assertFalse(b.allow())
        self.clock.now += 1
        self.assertTrue(b.allow())

    def test_snapshot_counts_how_often_it_opened(self):
        # scripts/bench_pipeline.py's breaker_opens (/v2/status pipeline): a failed trial opens it again, a straggler
        # that fails while it is open only restarts the interval
        b = self.breaker
        self.assertEqual(b.snapshot()["opens"], 0)
        for _ in range(3):
            b.record_failure("timeout")
        self.assertEqual(b.snapshot()["opens"], 1)
        b.record_failure("timeout")                 # a straggler, still open
        self.assertEqual((b.state, b.snapshot()["opens"]), ("open", 1))
        self.clock.now += 30
        self.assertTrue(b.allow())
        b.record_failure("http_502")                # the trial failed
        self.assertEqual((b.state, b.snapshot()["opens"]), ("open", 2))
        self.clock.now += 30
        self.assertTrue(b.allow())
        b.record_success()
        for _ in range(2):
            b.record_failure("timeout")
        self.assertEqual((b.state, b.snapshot()["opens"]), ("closed", 2))
        b.record_failure("timeout")
        self.assertEqual(b.snapshot()["opens"], 3)

    def test_invalid_settings(self):
        for kw in ({"failures": 0}, {"open_seconds": 0}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                CircuitBreaker(**kw)


class BM25:
    name = "bm25"

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50):
        return [SearchHit(retriever="bm25", variant_id=variant.variant_id, purpose=variant.purpose,
                          chunk_id=c, doc_id="rfc3101", rank=i + 1, score=1.0, text=c)
                for i, c in enumerate(["a", "b", "c"])]


class FakeDense:
    name = "dense"
    fail: Exception | None = None
    calls = 0

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50):
        FakeDense.calls += 1
        if FakeDense.fail is not None:
            raise FakeDense.fail
        return [SearchHit(retriever="dense", variant_id=variant.variant_id, purpose=variant.purpose,
                          chunk_id="d", doc_id="rfc3101", rank=1, score=0.9, text="d")]


class EarlyReturnDense(FakeDense):
    """Like DenseRetriever: with nothing to search it answers [] without calling its service."""

    def search(self, variant, *, limit=50, doc_ids=None):
        if doc_ids is not None and not doc_ids or not variant.text.strip():
            return []
        return super().search(variant, limit=limit)


class OrchestratorBreakerTests(unittest.TestCase):

    def setUp(self):
        FakeDense.fail, FakeDense.calls = None, 0
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cfg = Path(d.name) / "retrieval.json"
        cfg.write_text(json.dumps(CONFIG))
        env = mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"})
        factories = mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, {"bm25": BM25, "dense": FakeDense},
                                    clear=True)
        env.start()
        factories.start()
        self.addCleanup(env.stop)
        self.addCleanup(factories.stop)
        self.orch = RetrievalOrchestrator(paths=mock.Mock(logs=Path(d.name) / "logs"))
        self.clock = Clock()
        self.orch.breakers["dense"] = CircuitBreaker(failures=3, open_seconds=30, clock=self.clock)

    def search(self):
        return self.orch.search(QUERY)

    def test_healthy_search_reports_nothing_degraded(self):
        result = self.search()
        self.assertEqual(result["degraded"], [])
        self.assertEqual(result["active_retrievers"], ["bm25", "dense"])
        self.assertEqual(self.orch.breakers["dense"].state, "closed")

    def test_three_failures_open_the_breaker_and_bm25_keeps_answering(self):
        FakeDense.fail = httpx.ConnectTimeout("timed out", request=httpx.Request("POST", TEI_URL))
        for _ in range(3):
            result = self.search()
            self.assertEqual(result["degraded"], ["dense"])
            self.assertEqual([e["kind"] for e in result["task_errors"]], ["timeout"])
            self.assertEqual(result["active_retrievers"], ["bm25"])
            self.assertEqual([c["chunk_id"] for c in result["candidates"]], ["a", "b", "c"])
        self.assertEqual(FakeDense.calls, 3)
        self.assertEqual(self.orch.breakers["dense"].state, "open")

        result = self.search()                       # skipped: no call, no error, still degraded
        self.assertEqual(FakeDense.calls, 3)
        self.assertEqual(result["task_errors"], [])
        self.assertEqual(result["degraded"], ["dense"])
        self.assertEqual(result["candidates"][0]["chunk_id"], "a")
        caps = result["capabilities"]
        self.assertFalse(caps["dense"])
        self.assertIn("circuit breaker open", caps["degraded"]["dense"])

    def test_recovers_through_half_open(self):
        FakeDense.fail = http_error(502)
        for _ in range(3):
            self.search()
        self.clock.now += 30
        FakeDense.fail = None
        result = self.search()                       # the half-open trial succeeds
        self.assertEqual(FakeDense.calls, 4)
        self.assertEqual(result["degraded"], [])
        self.assertEqual(self.orch.breakers["dense"].state, "closed")
        self.assertTrue(self.orch.capabilities()["dense"])

    def test_a_search_with_nothing_to_search_leaves_the_breaker_alone(self):
        # DenseRetriever answers [] without calling its service when no document passes the
        # filters or the query is blank; counted as a success, that closed an open breaker (or
        # reset the failure count) while dense was still down
        self.orch.retrievers["dense"] = EarlyReturnDense(None)
        breaker = self.orch.breakers["dense"]
        FakeDense.fail = http_error(502)
        self.search()
        self.search()                                # two failures: one more opens it
        self.orch.search(QUERY, doc_ids=frozenset())
        self.orch.search("   ")
        self.assertEqual(breaker.snapshot()["consecutive_failures"], 2)
        self.search()
        self.assertEqual((FakeDense.calls, breaker.state), (3, "open"))

        self.clock.now += 30                         # half-open
        result = self.orch.search(QUERY, doc_ids=frozenset())
        self.assertEqual((breaker.state, result["degraded"]), ("half-open", []))
        FakeDense.fail = None
        self.search()                                # its trial is still available, and succeeds
        self.assertEqual((FakeDense.calls, breaker.state), (4, "closed"))

    def test_settings_come_from_retrieval_json(self):
        self.assertEqual((self.orch.breakers["dense"].failures, self.orch.breakers["dense"].open_seconds),
                         (3, 30.0))
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "retrieval.json"
            cfg.write_text(json.dumps({**CONFIG, "dense_breaker": {"failures": 5, "open_seconds": 60}}))
            with mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg)}):
                orch = RetrievalOrchestrator(paths=mock.Mock(logs=Path(d) / "logs"))
        self.assertEqual((orch.breakers["dense"].failures, orch.breakers["dense"].open_seconds), (5, 60.0))

    def test_errors_carry_no_url_or_host(self):
        FakeDense.fail = http_error(502)
        result = self.search()
        text = json.dumps(result)
        for secret in ("gpu-host", "tailnet-example", "ts.net", "https://"):
            self.assertNotIn(secret, text)
        self.assertEqual(result["task_errors"][0]["error"], "HTTPStatusError: HTTP 502")
        self.assertEqual(result["task_errors"][0]["kind"], "http_502")


class QueryTimeoutTests(unittest.TestCase):
    """The breaker counts timeouts; the query timeout itself is dense.json "query_timeout_seconds"."""

    def config(self, overlay=None, **env):
        with tempfile.TemporaryDirectory() as d:
            if overlay is not None:
                (Path(d) / "config").mkdir()
                (Path(d) / "config" / "dense.json").write_text(json.dumps(overlay))
            clean = {k: v for k, v in os.environ.items()
                     if k not in ("RAG_ROOT", "RAG_DENSE_COLLECTION", "RAG_EMBED_QUERY_TIMEOUT")}
            with mock.patch.dict(os.environ, {**clean, **env}, clear=True), \
                    mock.patch("rag_core.retrievers.dense.make_qdrant_client"):   # no Qdrant version check
                config = load_dense_config(root=d)
                retriever = DenseRetriever(RAGPaths(Path(d)))
                self.addCleanup(retriever.embedder.close)
                return config, retriever

    def test_repo_default_root_overlay_then_env(self):
        config, retriever = self.config()
        self.assertEqual(config.query_timeout_seconds, 15.0)                   # production unchanged
        config, retriever = self.config({"collection": "rag_dense_gen7", "query_timeout_seconds": 1.5})
        self.assertEqual(config.query_timeout_seconds, 1.5)
        self.assertEqual(retriever.embedder._client.timeout.read, 1.5)
        self.assertEqual(retriever.embedder._client.timeout.connect, 1.5)
        config, _ = self.config({"collection": "rag_dense_gen7", "query_timeout_seconds": 1.5},
                                RAG_EMBED_QUERY_TIMEOUT="4")
        self.assertEqual(config.query_timeout_seconds, 4.0)


class ErrorTextTests(unittest.TestCase):

    def test_kinds(self):
        request = httpx.Request("GET", "http://10.0.0.5:6333/x")
        cases = [
            (httpx.ReadTimeout("t", request=request), "timeout"),
            (TimeoutError(), "timeout"),
            (httpx.ConnectError("refused", request=request), "connect"),
            (ConnectionRefusedError(111, "Connection refused"), "connect"),
            (http_error(503), "http_503"),
            (RuntimeError("embedding count mismatch"), "RuntimeError"),
        ]
        for exc, kind in cases:
            with self.subTest(exc=exc):
                self.assertEqual(error_kind(exc), kind)

    def test_wrapped_transport_error(self):
        # qdrant-client raises ResponseHandlingException(source=<httpx error>)
        class Wrapped(Exception):
            def __init__(self, source):
                super().__init__(str(source))
                self.source = source
        inner = httpx.ConnectError("[Errno 111] Connection refused", request=httpx.Request("GET", "http://x/"))
        self.assertEqual(error_kind(Wrapped(inner)), "connect")

    def test_public_error_scrubs_addresses(self):
        exc = ConnectionError("HTTPConnectionPool(host='192.0.2.20', port=8765): refused by "
                              "deep32-engine.lan via http://192.0.2.20:8765/search and [fd7a::1]:443\nsecond line")
        text = public_error(exc)
        for secret in ("192.0.2", "deep32-engine.lan", "http://", "fd7a", "second line"):
            self.assertNotIn(secret, text)
        self.assertTrue(text.startswith("ConnectionError: HTTPConnectionPool(host='<host>'"))
        self.assertEqual(public_error(RuntimeError("")), "RuntimeError")
        self.assertEqual(public_error(RuntimeError("Qdrant vector size mismatch: collection=768 expected=1024")),
                         "RuntimeError: Qdrant vector size mismatch: collection=768 expected=1024")


if __name__ == "__main__":
    unittest.main()
