"""SRV-01 (audit 2026-11-05): breaker state changes reach the journal, and rag_core's own log lines have a handler.

- A breaker is named ("dense", "reranker") and logs WARNING on a state change only: one OPEN line, at most one
  "still open" line per STILL_OPEN_LOG_SECONDS while failed half-open trials keep re-opening it, and one CLOSED
  line with how long it was open. Never a line per call. The line is logged after the lock is released.
- api.configure_logging gives the rag_core logger an INFO stderr handler (uvicorn configures only its own
  loggers), and the lifespan calls it first, so the startup lines are seen.
No network: fakes, an injected clock and an in-memory stream only."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import contextlib
import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from rag_core import api, orchestrator
from rag_core.breaker import STILL_OPEN_LOG_SECONDS, CircuitBreaker, _span
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.retrieval_types import SearchHit

QUERY = "Why is the NSSA ABR not translating type-7 LSA to type-5 in OSPF?"
TEI_URL = "https://gpu-host.tailnet-example.ts.net/v1/embeddings"
SERVE_URL = "https://gpu-host.tailnet-example.ts.net:8443/v1/rerank"
TOKEN = "fake-token-" + "0" * 32

OPEN_LINE = "breaker dense OPEN after 3 consecutive failures (last: connect); calls skip it, one trial call per 30 s"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def messages(logs):
    return [record.getMessage() for record in logs.records]


class BreakerLogLineTests(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.b = CircuitBreaker(failures=3, open_seconds=30, clock=self.clock, name="dense")

    def open_it(self, kind="connect"):
        for _ in range(3):
            self.b.record_failure(kind)

    def test_no_line_while_closed_or_below_the_threshold(self):
        with self.assertNoLogs("rag_core.breaker"):
            for _ in range(50):
                self.assertTrue(self.b.allow())
                self.b.record_success()
            self.b.record_failure("timeout")
            self.b.record_failure("timeout")
            self.b.record_success()                 # resets the count: no transition, no line
            self.b.record_failure("timeout")
            self.b.record_failure("timeout")
        self.assertEqual(self.b.state, "closed")

    def test_one_open_line_names_the_breaker_and_the_last_kind(self):
        with self.assertLogs("rag_core.breaker", "WARNING") as logs:
            self.open_it()
        self.assertEqual(messages(logs), [OPEN_LINE])
        self.assertEqual(logs.records[0].levelno, logging.WARNING)

    def test_skipped_calls_and_a_straggler_failure_while_open_log_nothing(self):
        self.open_it()
        with self.assertNoLogs("rag_core.breaker"):
            for _ in range(20):
                self.assertFalse(self.b.allow())    # skipped: no line per call
            self.clock.now += 10
            self.b.record_failure("timeout")        # a straggler from before it opened: restarts the interval
        self.assertEqual(self.b.state, "open")

    def test_closed_line_has_the_open_duration_and_failed_trials(self):
        self.open_it()
        for _ in range(4):                          # four failed trials, 30 s apart
            self.clock.now += 30
            self.assertTrue(self.b.allow())
            self.b.record_failure("http_502")
        self.clock.now += 30
        self.assertTrue(self.b.allow())
        with self.assertLogs("rag_core.breaker", "WARNING") as logs:
            self.b.record_success()
        self.assertEqual(messages(logs), ["breaker dense CLOSED after 2 min 30 s open (4 failed trials)"])
        with self.assertNoLogs("rag_core.breaker"):
            self.b.record_success()                 # already closed: nothing

    def test_a_straggler_success_while_open_closes_it_and_says_so(self):
        self.open_it()
        self.clock.now += 5
        with self.assertLogs("rag_core.breaker", "WARNING") as logs:
            self.b.record_success()
        self.assertEqual(messages(logs), ["breaker dense CLOSED after 5 s open (0 failed trials)"])
        self.assertEqual(self.b.state, "closed")

    def test_a_long_outage_is_one_open_line_a_still_open_line_per_ten_minutes_and_one_closed_line(self):
        # the 11-04/05 gaming window: 12 h 40 min with the GPU host down and a search every 30 s, so a failed
        # half-open trial re-opens the breaker 1519 times. Logged per transition that was 1521 lines; now 77.
        self.assertEqual(STILL_OPEN_LOG_SECONDS, 600.0)
        with self.assertLogs("rag_core.breaker", "WARNING") as logs:
            self.open_it()
            start = self.clock.now
            while self.clock.now - start < 45600 - 30:
                self.clock.now += 30
                self.assertTrue(self.b.allow())     # the half-open trial
                self.b.record_failure("connect")
            self.clock.now += 30
            self.assertTrue(self.b.allow())
            self.b.record_success()
        lines = messages(logs)
        self.assertEqual(len(lines), 77)
        self.assertEqual(lines[0], OPEN_LINE)
        self.assertEqual(lines[1], "breaker dense still open after 10 min 0 s (20 failed trials, last: connect)")
        self.assertEqual(lines[2], "breaker dense still open after 20 min 0 s (40 failed trials, last: connect)")
        self.assertEqual(lines[-2], "breaker dense still open after 12 h 30 min (1500 failed trials, last: connect)")
        self.assertEqual(lines[-1], "breaker dense CLOSED after 12 h 40 min open (1519 failed trials)")
        self.assertEqual(sum("still open" in line for line in lines), 75)
        self.assertEqual(self.b.snapshot()["opens"], 1520)          # the counter /v2/status shows is unchanged

    def test_a_new_outage_after_closing_starts_its_own_lines(self):
        self.open_it()
        self.clock.now += 30
        self.b.allow()
        self.b.record_success()
        self.clock.now += 1000
        with self.assertLogs("rag_core.breaker", "WARNING") as logs:
            self.open_it("timeout")
            self.clock.now += 30
            self.b.allow()
            self.b.record_success()
        self.assertEqual(messages(logs), [
            "breaker dense OPEN after 3 consecutive failures (last: timeout); calls skip it, one trial call per 30 s",
            "breaker dense CLOSED after 30 s open (0 failed trials)",
        ])

    def test_the_line_is_logged_after_the_lock_is_released(self):
        seen = []

        class Probe(logging.Handler):
            def emit(probe, record):
                free = self.b._lock.acquire(blocking=False)
                if free:
                    self.b._lock.release()
                seen.append(free)

        log = logging.getLogger("rag_core.breaker")
        probe = Probe()
        log.addHandler(probe)
        self.addCleanup(log.removeHandler, probe)
        self.open_it()
        self.clock.now += 30
        self.b.allow()
        self.b.record_success()
        self.assertEqual(seen, [True, True])        # OPEN and CLOSED, each with the lock free

    def test_snapshot_and_state_are_as_before(self):
        self.open_it()
        self.assertEqual(set(self.b.snapshot()), {"state", "consecutive_failures", "last_error_kind",
                                                  "retry_in_seconds", "opens"})
        self.assertEqual(CircuitBreaker().name, "unnamed")

    def test_span(self):
        self.assertEqual([_span(s) for s in (0, 0.4, 41, 60, 754, 3599, 3600, 45600, 90061)],
                         ["0 s", "0 s", "41 s", "1 min 0 s", "12 min 34 s", "59 min 59 s", "1 h 0 min",
                          "12 h 40 min", "25 h 1 min"])


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


class OrchestratorBreakerNameTests(unittest.TestCase):

    def orch(self, extra=None):
        FakeDense.fail, FakeDense.calls = None, 0
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "retrieval.json"
        cfg.write_text(json.dumps({
            "rrf_k": 60, "candidate_limit_per_query": 10, "fused_limit": 5,
            "retriever_weights": {"bm25": 1.0, "dense": 1.0}, "retrievers": ["bm25", "dense"],
            "technical_variant_retrievers": ["bm25"], **(extra or {}),
        }))
        for p in (mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"}),
                  mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, {"bm25": BM25, "dense": FakeDense}, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("RAG_RERANK_URL", None)
        return RetrievalOrchestrator(paths=mock.Mock(logs=Path(tmp.name) / "logs"))

    def test_the_breakers_are_named(self):
        orch = self.orch({"reranker": {"enabled": True, "url": SERVE_URL}})
        self.addCleanup(orch.reranker.close)
        self.assertEqual(orch.breakers["dense"].name, "dense")
        self.assertEqual(orch.reranker.breaker.name, "reranker")

    def test_searches_through_an_outage_log_one_open_and_one_closed_line(self):
        orch = self.orch()
        clock = Clock()
        orch.breakers["dense"]._clock = clock       # the orchestrator's own (named) breaker, on a test clock
        FakeDense.fail = httpx.ConnectError("refused", request=httpx.Request("POST", TEI_URL))
        with self.assertLogs("rag_core.breaker", "WARNING") as logs:
            for _ in range(10):                     # 3 failures open it, 7 searches skip dense
                self.assertEqual(orch.search(QUERY)["degraded"], ["dense"])
            clock.now += 30
            orch.search(QUERY)                      # a failed trial: counted, not logged
            clock.now += 40
            FakeDense.fail = None
            self.assertEqual(orch.search(QUERY)["degraded"], [])
        self.assertEqual(FakeDense.calls, 5)
        self.assertEqual(messages(logs), [OPEN_LINE, "breaker dense CLOSED after 1 min 10 s open (1 failed trials)"])


class StubOrchestrator:
    retrievers = {"bm25": None}

    def __init__(self, paths):
        pass

    def capabilities(self):
        return {"bm25": True}


class ConfigureLoggingTests(unittest.TestCase):

    def setUp(self):
        root, package = logging.getLogger(), logging.getLogger("rag_core")
        saved = (root.handlers[:], package.handlers[:], package.level, package.propagate)

        def restore():
            root.handlers[:], package.handlers[:], level, propagate = saved
            package.setLevel(level)
            package.propagate = propagate

        self.addCleanup(restore)
        root.handlers[:] = []
        package.handlers[:] = []
        package.setLevel(logging.NOTSET)
        package.propagate = True

    def test_an_info_handler_on_rag_core_once(self):
        package = logging.getLogger("rag_core")
        self.assertTrue(api.configure_logging())
        self.assertFalse(api.configure_logging())   # idempotent
        self.assertEqual(len(package.handlers), 1)
        handler = package.handlers[0]
        self.assertIs(type(handler), logging.StreamHandler)
        self.assertEqual(handler.get_name(), api.LOG_HANDLER_NAME)
        self.assertEqual(handler.formatter._fmt, api.LOG_FORMAT)
        self.assertEqual((package.level, package.propagate), (logging.INFO, False))
        self.assertEqual(logging.getLogger().handlers, [])     # root untouched

        stream = io.StringIO()
        handler.setStream(stream)
        logging.getLogger("rag_core.api").info("search admission: %d concurrent", 2)
        logging.getLogger("rag_core.api").debug("not shown")
        b = CircuitBreaker(failures=1, name="dense")
        b.record_failure("connect")
        self.assertEqual(stream.getvalue().splitlines(), [
            "INFO rag_core.api: search admission: 2 concurrent",
            "WARNING rag_core.breaker: breaker dense OPEN after 1 consecutive failures (last: connect); "
            "calls skip it, one trial call per 30 s",
        ])

    def test_a_configured_root_logger_is_left_alone(self):
        mine = logging.NullHandler()
        logging.getLogger().handlers[:] = [mine]
        self.assertFalse(api.configure_logging())
        self.assertEqual(logging.getLogger("rag_core").handlers, [])
        self.assertEqual(logging.getLogger().handlers, [mine])

    def test_the_lifespan_configures_it_first_and_the_startup_lines_are_seen(self):
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"RAG_CORE_API_TOKEN": TOKEN, "RAG_ROOT": "/tmp/none"}), \
                mock.patch.object(api, "RetrievalOrchestrator", StubOrchestrator), \
                contextlib.redirect_stderr(stderr), TestClient(api.app):
            pass
        handlers = logging.getLogger("rag_core").handlers
        self.assertEqual([h.get_name() for h in handlers], [api.LOG_HANDLER_NAME])
        self.assertRegex(stderr.getvalue(), r"(?m)^INFO rag_core\.api: search admission: \d+ concurrent, "
                                            r"\d+ queued at most, queue timeout [\d.]+ s$")

    def test_the_lifespan_calls_it_before_anything_else(self):
        order = []
        with mock.patch.dict(os.environ, {"RAG_CORE_API_TOKEN": TOKEN, "RAG_ROOT": "/tmp/none"}), \
                mock.patch.object(api, "RetrievalOrchestrator", StubOrchestrator), \
                mock.patch.object(api, "configure_logging", side_effect=lambda: order.append("logging")), \
                mock.patch.object(api, "_expected_token", side_effect=lambda: order.append("token") or TOKEN), \
                TestClient(api.app):
            pass
        self.assertEqual(order[:2], ["logging", "token"])


if __name__ == "__main__":
    unittest.main()
