"""The GPU reranker behind Tailscale Serve: URL and timeout configuration, a circuit breaker like dense's (a
reranker that is down costs at most `failures` calls, then searches skip it until a trial succeeds), a reranker
that cannot start leaves the service up and says so, and /v2/status reports the breaker. No network: httpx
MockTransport and fakes only."""
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
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.paths import RAGPaths
from rag_core.rerank import Reranker
from rag_core.status import StatusService

from test_api_v2 import AUTH, RootClient, WordReranker
from test_search_restrictions import Filtering

SERVE_URL = "https://gpu-host.tailnet-example.ts.net:8443/v1/rerank"


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def items(n):
    return [{"chunk_id": f"c{i}", "text": f"text {i}", "heading_path": ["H"], "fused_score": 1.0 / (i + 1)}
            for i in range(n)]


class Service:
    """A MockTransport reranker: 'ok' reverses the order, 'timeout' raises a read timeout."""

    def __init__(self):
        self.mode, self.calls = "ok", []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        body = json.loads(request.content)
        n = len(body["documents"])
        return httpx.Response(200, json={"results": [{"index": i, "relevance_score": float(i)}
                                                     for i in reversed(range(n))]})


class RerankerConfigTests(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("RAG_RERANK_URL", None)

    def test_the_serve_url_and_the_measured_defaults(self):
        r = Reranker({"url": SERVE_URL})
        self.assertEqual(r.url, SERVE_URL)
        self.assertEqual((r.candidates, r.timeout_seconds, r.connect_timeout_seconds), (10, 15.0, 5.0))
        self.assertEqual((r.breaker.failures, r.breaker.open_seconds), (3, 30.0))
        self.assertEqual((r._client.timeout.read, r._client.timeout.connect), (15.0, 5.0))
        r.close()

    def test_timeouts_retries_and_breaker_are_configurable(self):
        r = Reranker({"url": SERVE_URL, "timeout_seconds": 4, "connect_timeout_seconds": 1.5, "connect_retries": 0,
                      "breaker": {"failures": 2, "open_seconds": 10}})
        self.assertEqual((r._client.timeout.read, r._client.timeout.connect), (4.0, 1.5))
        self.assertEqual((r.breaker.failures, r.breaker.open_seconds), (2, 10.0))
        r.close()
        r = Reranker({"url": SERVE_URL, "timeout_seconds": 2})
        self.assertEqual(r.connect_timeout_seconds, 2.0)       # never longer than the whole call
        r.close()

    def test_bad_settings_are_refused(self):
        for cfg in ({}, {"url": None}, {"url": "ftp://x/y"}, {"url": "gpu-host:8443/v1/rerank"},
                    {"url": SERVE_URL, "timeout_seconds": 0}, {"url": SERVE_URL, "connect_timeout_seconds": -1},
                    {"url": SERVE_URL, "connect_retries": -1}, {"url": SERVE_URL, "breaker": 3},
                    {"url": SERVE_URL, "breaker": {"failures": 0}}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                Reranker(cfg)

    def test_the_env_url_still_wins(self):
        os.environ["RAG_RERANK_URL"] = "http://127.0.0.1:9/v1/rerank"
        r = Reranker({"url": SERVE_URL})
        self.assertEqual(r.url, "http://127.0.0.1:9/v1/rerank")
        r.close()


class RerankBreakerTests(unittest.TestCase):

    def setUp(self):
        self.service = Service()
        self.r = Reranker({"url": SERVE_URL, "breaker": {"failures": 3, "open_seconds": 30}},
                          transport=httpx.MockTransport(self.service))
        self.clock = Clock()
        self.r.breaker = CircuitBreaker(failures=3, open_seconds=30, clock=self.clock)
        self.addCleanup(self.r.close)

    def rerank(self):
        degraded, errors = set(), []
        main, info = RetrievalOrchestrator._rerank(self.r, "q", items(4), degraded, errors)
        return [m["chunk_id"] for m in main], info, degraded, errors

    def test_a_down_reranker_costs_three_calls_then_is_skipped_until_a_trial_succeeds(self):
        ids, info, degraded, _ = self.rerank()
        self.assertEqual((ids, info["applied"], degraded), (["c3", "c2", "c1", "c0"], True, set()))
        self.service.mode = "timeout"
        for n in range(3):
            ids, info, degraded, errors = self.rerank()
            self.assertEqual(ids, ["c0", "c1", "c2", "c3"])          # the fused order stands
            self.assertEqual((degraded, errors[0]["kind"]), ({"reranker"}, "timeout"))
        self.assertEqual(self.r.breaker.state, "open")
        calls = len(self.service.calls)
        ids, info, degraded, errors = self.rerank()
        self.assertEqual(len(self.service.calls), calls)            # no call, no wait
        self.assertEqual((info["skipped"], degraded, errors), ("circuit breaker open", {"reranker"}, []))
        self.service.mode = "ok"
        self.clock.now += 31                                         # half-open: one trial goes
        ids, info, degraded, _ = self.rerank()
        self.assertEqual((ids[0], info["applied"], self.r.breaker.state), ("c3", True, "closed"))

    def test_a_failed_trial_opens_it_again(self):
        self.service.mode = "timeout"
        for _ in range(3):
            self.rerank()
        self.clock.now += 31
        self.rerank()
        self.assertEqual(self.r.breaker.state, "open")
        self.assertEqual(self.r.breaker.snapshot()["last_error_kind"], "timeout")


class OrchestratorRerankTests(unittest.TestCase):

    def orch(self, reranker_cfg):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "retrieval.json"
        cfg.write_text(json.dumps({"rrf_k": 60, "candidate_limit_per_query": 10, "fused_limit": 10,
                                   "retriever_weights": {}, "retrievers": ["bm25"], "reranker": reranker_cfg}))
        for p in (mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"}),
                  mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, {"bm25": Filtering}, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("RAG_RERANK_URL", None)
        return RetrievalOrchestrator(paths=mock.Mock(logs=Path(tmp.name) / "logs"))

    def test_a_reranker_that_cannot_start_leaves_the_service_up_and_says_so(self):
        with self.assertLogs("rag_core.orchestrator", "WARNING"):
            orch = self.orch({"enabled": True, "url": "not a url"})
        self.assertIsNone(orch.reranker)
        self.assertTrue(orch.rerank_enabled)
        self.assertIn("reranker", orch.runtime_degraded())
        self.assertEqual(orch.search("q", mode="fast")["degraded"], ["reranker"])
        self.assertEqual(orch.search("q", mode="fast", rerank=False)["degraded"], [])

    def test_an_open_breaker_shows_in_capabilities(self):
        orch = self.orch({"enabled": True, "url": SERVE_URL})
        for _ in range(3):
            orch.reranker.breaker.record_failure("timeout")
        self.assertIn("reranker", orch.capabilities()["degraded"])
        result = orch.search("q", mode="fast")
        self.assertEqual((result["degraded"], result["rerank"]["skipped"]), (["reranker"], "circuit breaker open"))
        orch.reranker.close()


class StatusRerankTests(unittest.TestCase):

    def status(self, orch):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        svc = StatusService(orch, RAGPaths(Path(tmp.name)), generation="g")
        self.addCleanup(svc.close)
        return svc.status()

    def test_breaker_state_and_startup_failure(self):
        class Probe:
            def __init__(self):
                self.breaker = CircuitBreaker()

            def probe(self, timeout):
                return {"latency_ms": 7.0}

        class Orch:
            enabled, retrievers, breakers, rerank_enabled = ["bm25"], {"bm25": object()}, {}, True

        orch = Orch()
        orch.reranker = Probe()
        status = self.status(orch)
        self.assertEqual({k: status["backends"]["reranker"][k] for k in ("enabled", "ok", "breaker")},
                         {"enabled": True, "ok": True, "breaker": "closed"})
        self.assertNotIn("reranker", status["degraded"])
        for _ in range(3):
            orch.reranker.breaker.record_failure("timeout")
        status = self.status(orch)
        self.assertEqual((status["backends"]["reranker"]["breaker"],
                          status["backends"]["reranker"]["last_error_kind"]), ("open", "timeout"))
        self.assertIn("reranker", status["degraded"])
        orch.reranker = None                      # enabled, but it could not start
        status = self.status(orch)
        self.assertEqual({k: status["backends"]["reranker"][k] for k in ("enabled", "ok", "last_error_kind")},
                         {"enabled": True, "ok": False, "last_error_kind": "startup"})
        self.assertIn("reranker", status["degraded"])


class SearchManyBreakerTests(unittest.TestCase):

    def test_search_many_skips_a_reranker_whose_breaker_is_open_and_records_its_calls(self):
        root = RootClient()
        self.addCleanup(root.close)
        orch = root.client.app.state.orchestrator
        reranker = WordReranker(fail=True)
        reranker.breaker = CircuitBreaker(failures=2, open_seconds=30)
        body = {"queries": [{"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
                            {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25"}], "limit": 5, "rerank": "on"}
        with mock.patch.object(orch, "reranker", reranker):
            for _ in range(2):
                r = root.client.post("/v2/search_many", json=body, headers=AUTH).json()
                self.assertEqual(r["degraded"], ["reranker"])
            self.assertEqual(reranker.breaker.state, "open")
            reranker.fail = False
            r = root.client.post("/v2/search_many", json=body, headers=AUTH).json()
        self.assertEqual(r["degraded"], ["reranker"])
        self.assertEqual(reranker.calls, [])                        # skipped: never called
        self.assertIsNone(r["timings_ms"]["rerank"])


if __name__ == "__main__":
    unittest.main()
