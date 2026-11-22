"""serving.json "pipeline" (rag_core.pipeline; default off): several searches admitted at once, their reranks
through one FIFO gate. The GPU sees one search's rerank job at a time, in arrival order, while the others
retrieve; an open breaker skips the gate without waiting, and a half-open one recovers through it; a search that
waits past rerank_wait_seconds keeps its place (on_timeout "wait", the default) or goes unreranked ("skip");
without the setting nothing changes. The tiny root of tests/v2_root.py, BM25 and fake rerankers; no network."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import inspect
import json
import os
import statistics
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from rag_core import api, api_v2, pipeline
from rag_core.breaker import CircuitBreaker
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.pipeline import PipelineSettings, StageGate, pipeline_settings

import test_api_v2
import test_rerank_breaker
from test_api_v2 import AUTH, RootClient

# the pipeline trial's arm
SERVING_ON = {"max_concurrent_searches": 4, "max_queued_searches": 12, "pipeline": {"rerank_gate": 1}}
# queries of the tiny root, each a request of its own (a reranker call is known by its query)
QUERIES = ["NSSA translator election", "Type-7 LSAs P-bit", "BGP ORIGINATOR_ID Identifier",
           "show ip ospf neighbor", "forwarding address 0.0.0.0", "Decision Process routes",
           "area nssa translate type7 always", "UPDATE message Established state"]
MANY = [["Type-7 LSAs NSSA translator", "ORIGINATOR_ID BGP Identifier", "NSSA ABR elected translator"],
        ["OSPFv3 Type-7 LSAs", "MULTI_EXIT_DISC attributes", "Phase 2 decision function"]]
# the tiny root is configured without a dense retriever: a hybrid /v2/search (PipelineCase.search) says so; a
# search_many of bm25 queries (PipelineCase.search_many) and the legacy /search do not
OFF = ["dense"]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TrackingReranker:
    """A reranker that records every call (query, started, ended), how many ran at once and the order they
    started in. With `hold` (an Event) the calls for the queries in `held` (all, when None) wait for it; the
    queries in `fail` (or every one, when True) raise."""
    candidates = 10

    def __init__(self, pause=0.03, breaker=None):
        self.pause, self.breaker = pause, breaker
        self.lock = threading.Lock()
        self.running = self.peak = 0
        self.calls, self.order = [], []
        self.hold, self.held = None, None
        self.fail = set()

    def failing(self, query):
        return self.fail is True or query in self.fail

    def rerank(self, query, items):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.order.append(query)
        started = time.perf_counter()
        try:
            if self.hold is not None and not self.failing(query) and (self.held is None or query in self.held):
                self.hold.wait(20)
            time.sleep(self.pause)
            if self.failing(query):
                raise TimeoutError("rerank timed out")
            for n, item in enumerate(items, 1):
                item["rerank_score"] = round(1.0 / n, 6)
                item["fused_rank"] = n
            return list(items)
        finally:
            with self.lock:
                self.running -= 1
                self.calls.append((query, started, time.perf_counter()))

    def probe(self, timeout):                # /v2/status
        return {"latency_ms": 1.0}


def overlapping(calls, request_of):
    """Pairs of calls of two different requests that ran at the same time."""
    found = []
    for i, (qa, sa, ea) in enumerate(calls):
        for qb, sb, eb in calls[i + 1:]:
            if request_of[qa] != request_of[qb] and sa < eb and sb < ea:
                found.append((qa, qb))
    return found


def wait_for(predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.005)


def barrier_retrieve(parties):
    """api_v2.retrieve that waits until `parties` retrievals run with it: they cannot run one after another."""
    barrier = threading.Barrier(parties, timeout=10)
    real = api_v2.retrieve

    def retrieve(*args, **kwargs):
        barrier.wait()
        return real(*args, **kwargs)

    return mock.patch.object(api_v2, "retrieve", retrieve)


class PipelineCase(unittest.TestCase):

    def root(self, serving=SERVING_ON, reranker=None):
        """A TestClient on a fresh tiny root with this serving overlay, its reranker replaced; searches may queue
        10 s for admission (RootClient's env sets 0.2 s)."""
        root = RootClient(serving=serving)
        self.addCleanup(root.close)
        state = root.client.app.state
        state.search_queue_timeout = 10.0
        if reranker is not None:
            patch = mock.patch.object(state.orchestrator, "reranker", reranker)
            patch.start()
            self.addCleanup(patch.stop)
        return root

    @staticmethod
    def search(root, query, **kw):
        return root.client.post("/v2/search", json={"query": query, "limit": 5, "rerank": "on", **kw}, headers=AUTH)

    @staticmethod
    def search_many(root, texts, **kw):
        return root.client.post("/v2/search_many", json={
            "queries": [{"text": t, "mode": "bm25"} for t in texts], "limit": 5, "rerank": "on", **kw},
            headers=AUTH)

    def executor(self, n):
        ex = ThreadPoolExecutor(n)
        self.addCleanup(ex.shutdown)
        return ex


# --- the gate ----------------------------------------------------------------------------------

class StageGateTests(unittest.TestCase):

    def queue(self, gate, n):
        """n threads that take the gate, each queued before the next starts; the order they got it in."""
        order, threads = [], []
        for i in range(n):
            def run(i=i):
                if gate.acquire():
                    order.append(i)
                    time.sleep(0.01)
                    gate.release()
            t = threading.Thread(target=run)
            t.start()
            threads.append(t)
            wait_for(lambda: gate.snapshot()["waiting"] == i + 1)
        return order, threads

    def test_waiters_are_served_in_arrival_order(self):
        gate = StageGate(1, 8, wait_seconds=10)
        self.assertTrue(gate.acquire())
        order, threads = self.queue(gate, 6)
        gate.release()
        for t in threads:
            t.join(10)
        self.assertEqual(order, list(range(6)))
        snap = gate.snapshot()
        self.assertEqual({k: snap[k] for k in ("busy", "waiting", "passed", "refused", "timed_out", "overdue")},
                         {"busy": 0, "waiting": 0, "passed": 7, "refused": 0, "timed_out": 0, "overdue": 0})

    def test_a_newcomer_never_passes_the_queue(self):
        # the slot is free for a moment while the first waiter wakes up: a newcomer queues behind it all the same
        gate = StageGate(1, 8, wait_seconds=10)
        gate.acquire()
        order, threads = self.queue(gate, 3)
        gate.release()
        if gate.acquire():
            order.append("late")
            gate.release()
        for t in threads:
            t.join(10)
        self.assertEqual(order, [0, 1, 2, "late"])

    def test_a_full_queue_refuses_at_once(self):
        gate = StageGate(1, 2, wait_seconds=10)
        gate.acquire()
        _, threads = self.queue(gate, 2)
        started = time.monotonic()
        self.assertFalse(gate.acquire())
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(gate.snapshot()["refused"], 1)
        gate.release()
        for t in threads:
            t.join(10)
        zero = StageGate(1, 0, wait_seconds=1)              # 0: nobody waits
        zero.acquire()
        self.assertFalse(zero.acquire())

    def test_skip_gives_up_after_the_wait_and_the_next_in_line_moves_up(self):
        gate = StageGate(1, 8, wait_seconds=0.1, on_timeout="skip")
        gate.acquire()
        started = time.monotonic()
        self.assertFalse(gate.acquire())
        self.assertGreaterEqual(time.monotonic() - started, 0.1)
        self.assertEqual((gate.snapshot()["timed_out"], gate.snapshot()["waiting"]), (1, 0))
        gate.release()
        # the first in line gives up; the one behind it, still within its wait, gets the gate next
        gate = StageGate(1, 8, wait_seconds=1.0, on_timeout="skip")
        gate.acquire()
        results = {}
        first = threading.Thread(target=lambda: results.__setitem__("first", gate.acquire()))
        first.start()
        wait_for(lambda: gate.snapshot()["waiting"] == 1)
        time.sleep(0.5)
        second = threading.Thread(target=lambda: results.__setitem__("second", gate.acquire()))
        second.start()
        wait_for(lambda: gate.snapshot()["waiting"] == 2)
        first.join(5)
        self.assertEqual((results, gate.snapshot()["waiting"]), ({"first": False}, 1))
        gate.release()
        second.join(5)
        self.assertEqual(results, {"first": False, "second": True})
        gate.release()

    def test_wait_keeps_the_place_of_an_overdue_waiter(self):
        gate = StageGate(1, 8, wait_seconds=0.05)           # on_timeout "wait", the default
        self.assertEqual(gate.on_timeout, "wait")
        gate.acquire()
        got = []
        t = threading.Thread(target=lambda: got.append(gate.acquire()))
        t.start()
        wait_for(lambda: gate.snapshot()["overdue"] == 1)
        time.sleep(0.1)
        self.assertEqual((got, gate.snapshot()["waiting"]), ([], 1))    # still in line, past wait_seconds
        gate.release()
        t.join(5)
        self.assertEqual((got, gate.snapshot()["timed_out"]), ([True], 0))
        gate.release()

    def test_hold_reports_the_wait_and_gives_the_slot_back(self):
        gate = StageGate(1, 8, wait_seconds=10)
        with gate.hold() as (ok, waited):
            self.assertTrue(ok)
            self.assertLess(waited, 5.0)
            self.assertEqual(gate.snapshot()["busy"], 1)
        self.assertEqual(gate.snapshot()["busy"], 0)
        with self.assertRaises(RuntimeError):
            with gate.hold():
                raise RuntimeError("the stage failed")
        self.assertEqual(gate.snapshot()["busy"], 0)         # given back all the same
        with self.assertRaises(ValueError):
            gate.release()                                  # bounded
        full = StageGate(1, 0, wait_seconds=10)
        full.acquire()
        with full.hold() as (ok, _):
            self.assertFalse(ok)
        self.assertEqual(full.snapshot()["busy"], 1)         # a refusal gives back nothing it did not take

    def test_bad_sizes_are_refused(self):
        for args, kw in (((0, 1), {}), ((1, -1), {}), ((True, 1), {}), ((1, 1), {"wait_seconds": 0}),
                         ((1, 1), {"on_timeout": "drop"})):
            with self.subTest(args=args, kw=kw), self.assertRaises(ValueError):
                StageGate(*args, **{"wait_seconds": 1, **kw})

    def test_an_uncontended_hold_costs_microseconds(self):
        gate = StageGate(1, 8, wait_seconds=10)
        started = time.perf_counter()
        for _ in range(10_000):
            with gate.hold():
                pass
        self.assertLess(time.perf_counter() - started, 1.0)


class SettingsTests(unittest.TestCase):

    def settings(self, value, slots=4, max_queued=12):
        return pipeline_settings({} if value is None else {"pipeline": value}, slots=slots, max_queued=max_queued)

    def test_absent_is_off_and_the_repo_sets_nothing(self):
        self.assertEqual(self.settings(None), PipelineSettings(rerank_gate=0))
        self.assertIsNone(StageGate.from_settings(self.settings(None)))
        self.assertIsNone(StageGate.from_settings(self.settings({"rerank_gate": 0})))
        repo = json.loads(api.SERVING_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("pipeline", repo)
        self.assertEqual(repo["max_concurrent_searches"], 1)

    def test_the_plan_keys(self):
        on = self.settings({"rerank_gate": 1})
        self.assertEqual(on, PipelineSettings(rerank_gate=1, rerank_wait_seconds=10.0, rerank_max_waiting=8,
                                              rerank_on_timeout="wait"))
        gate = StageGate.from_settings(on)
        self.assertEqual((gate.slots, gate.max_waiting, gate.wait_seconds, gate.on_timeout), (1, 8, 10.0, "wait"))
        self.assertEqual(self.settings({"rerank_gate": 1, "rerank_wait_seconds": 30, "rerank_max_waiting": 3,
                                        "rerank_on_timeout": "skip"}), PipelineSettings(1, 30.0, 3, "skip"))
        self.assertEqual(self.settings({"rerank_gate": 1, "rerank_wait_seconds": 2.5}).rerank_wait_seconds, 2.5)

    def test_bad_values_are_refused(self):
        for bad in ([], "on", {"rerank_gate": 2}, {"rerank_gate": True}, {"rerank_gate": "1"}, {"rerank_gate": 1.0},
                    {"rerank_wait_seconds": 0}, {"rerank_wait_seconds": -1}, {"rerank_wait_seconds": "10"},
                    {"rerank_wait_seconds": float("nan")}, {"rerank_wait_seconds": float("inf")},
                    {"rerank_wait_seconds": True}, {"rerank_max_waiting": -1}, {"rerank_max_waiting": 1.5},
                    {"rerank_max_waiting": True}, {"rerank_on_timeout": "drop"}, {"rerank_on_timeout": None},
                    {"rerank_gates": 1}, {"grep_slots": 2}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.settings(bad)

    def test_the_gate_needs_admission_that_leaves_threads_and_waiting_room(self):
        self.assertEqual(self.settings({"rerank_gate": 1}, slots=4, max_queued=16).rerank_gate, 1)
        self.assertEqual(self.settings({"rerank_gate": 1}, slots=8, max_queued=16).rerank_gate, 1)   # 24 of 40
        with self.assertRaisesRegex(ValueError, "worker threads"):
            self.settings({"rerank_gate": 1, "rerank_max_waiting": 8}, slots=9, max_queued=16)
        with self.assertRaisesRegex(ValueError, "rerank_max_waiting"):
            self.settings({"rerank_gate": 1, "rerank_max_waiting": 2}, slots=4, max_queued=12)
        # off, any admission starts as it always did
        self.assertEqual(self.settings({"rerank_gate": 0}, slots=30, max_queued=16).rerank_gate, 0)
        self.assertEqual(self.settings(None, slots=30, max_queued=16).rerank_gate, 0)
        # the slots come from admission_settings, so an env override is checked too (validate_root strips it,
        # production does not: /etc/deep32-engine.env must not raise it unseen)
        with mock.patch.dict(os.environ, {"RAG_MAX_CONCURRENT_SEARCHES": "20"}):
            slots, _ = api.admission_settings({"max_concurrent_searches": 4})
        with self.assertRaisesRegex(ValueError, "worker threads"):
            self.settings({"rerank_gate": 1, "rerank_max_waiting": 20}, slots=slots, max_queued=12)


# --- start ------------------------------------------------------------------------------------------

class StartTests(PipelineCase):

    def test_without_the_setting_there_is_no_gate_and_nothing_new_in_the_answers(self):
        never = mock.Mock(side_effect=AssertionError("gated code ran without the setting"))
        reranker = TrackingReranker(pause=0)
        with mock.patch.object(pipeline.StageGate, "__init__", never), \
                mock.patch.object(RetrievalOrchestrator, "_gated_rerank", never), \
                mock.patch.object(api_v2, "gated_rerank_head", never):
            root = self.root(serving=None, reranker=reranker)
            self.assertIsNone(root.client.app.state.orchestrator.rerank_gate)
            body = self.search(root, QUERIES[0]).json()
            many = self.search_many(root, MANY[0]).json()
            legacy = root.client.post("/search", json={"query": QUERIES[1]}, headers=AUTH).json()
            status = root.client.get("/v2/status", headers=AUTH).json()
        never.assert_not_called()
        self.assertEqual(set(body["timings_ms"]), {"retrieve", "rerank", "assemble", "total", "bm25"})
        self.assertIsNotNone(body["timings_ms"]["rerank"])
        self.assertEqual(set(many["timings_ms"]), {"retrieve", "rerank", "assemble", "total"})
        self.assertEqual(set(legacy["rerank"]), {"applied", "candidates", "ms"})
        self.assertNotIn("pipeline", status)
        self.assertEqual(len(reranker.calls), 1 + 3 + 1)

    def test_with_it_every_rerank_passes_the_gate_and_status_shows_it(self):
        reranker = TrackingReranker(pause=0)
        root = self.root(reranker=reranker)
        orch = root.client.app.state.orchestrator
        self.assertIsInstance(orch.rerank_gate, StageGate)
        body = self.search(root, QUERIES[0]).json()
        many = self.search_many(root, MANY[0]).json()
        legacy = root.client.post("/search", json={"query": QUERIES[1]}, headers=AUTH).json()
        self.assertEqual((body["degraded"], many["degraded"]), (OFF, []))
        self.assertIn("rerank_wait", body["timings_ms"])
        self.assertIn("rerank_wait", many["timings_ms"])
        self.assertEqual(set(legacy["rerank"]), {"applied", "candidates", "ms", "wait_ms"})
        status = root.client.get("/v2/status", headers=AUTH).json()
        snap = status["pipeline"]["rerank_gate"]
        self.assertEqual(snap["passed"], 3)                 # a search_many passes once for its three calls
        self.assertEqual({k: snap[k] for k in ("slots", "busy", "waiting", "max_waiting", "wait_seconds",
                                               "on_timeout")},
                         {"slots": 1, "busy": 0, "waiting": 0, "max_waiting": 8, "wait_seconds": 10.0,
                          "on_timeout": "wait"})

    def test_a_value_rag_core_cannot_take_fails_the_start_before_any_retriever_loads(self):
        loaded = mock.Mock(side_effect=AssertionError("retrievers loaded"))
        for serving, why in (({"pipeline": {"rerank_gate": 2}}, "rerank_gate must be 0 or 1"),
                             ({"max_concurrent_searches": 30, "pipeline": {"rerank_gate": 1, "rerank_max_waiting": 40}},
                              "worker threads"),
                             ({"pipeline": {"rerank_gate": 1, "rerank_on_timeout": "drop"}}, "rerank_on_timeout")):
            with self.subTest(serving=serving), tempfile.TemporaryDirectory() as d:
                path = Path(d) / "serving.json"
                path.write_text(json.dumps(serving))
                env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
                env.update(RAG_CORE_API_TOKEN=test_api_v2.TOKEN, RAG_ROOT=str(Path(d) / "root"),
                           RAG_SERVING_CONFIG=str(path))
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(api, "RetrievalOrchestrator", loaded):
                    with self.assertRaisesRegex(ValueError, why):
                        with TestClient(api.app):
                            pass
        loaded.assert_not_called()


# --- bursts -----------------------------------------------------------------------------------------

class BurstTests(PipelineCase):

    def test_four_searches_retrieve_at_once_and_rerank_one_at_a_time(self):
        reranker = TrackingReranker(pause=0.05)
        root = self.root(reranker=reranker)
        with barrier_retrieve(4):
            answers = [r.json() for r in self.executor(4).map(lambda q: self.search(root, q), QUERIES[:4])]
        self.assertEqual([a["degraded"] for a in answers], [OFF] * 4)
        self.assertTrue(all(a["timings_ms"]["rerank"] is not None for a in answers))
        self.assertEqual(reranker.peak, 1)
        self.assertEqual(sorted(q for q, _, _ in reranker.calls), sorted(QUERIES[:4]))
        waits = [a["timings_ms"]["rerank_wait"] for a in answers]
        self.assertGreater(sum(waits), 50)              # they queued for each other's reranks of 50 ms
        snap = root.client.app.state.orchestrator.rerank_gate.snapshot()
        self.assertEqual((snap["passed"], snap["timed_out"], snap["refused"], snap["busy"]), (4, 0, 0, 0))

    def test_a_burst_of_searches_and_search_many_never_overlaps_two_requests_reranks(self):
        reranker = TrackingReranker(pause=0.03)
        root = self.root(reranker=reranker)
        request_of = {q: f"search {q}" for q in QUERIES}
        for n, texts in enumerate(MANY):
            request_of.update({t: f"many {n}" for t in texts})
        jobs = [("search", q) for q in QUERIES] + [("many", texts) for texts in MANY]

        def run(job):
            kind, arg = job
            return (self.search(root, arg) if kind == "search" else self.search_many(root, arg)).json()

        answers = list(self.executor(len(jobs)).map(run, jobs))
        self.assertEqual([a["degraded"] for a in answers], [OFF if kind == "search" else [] for kind, _ in jobs])
        self.assertTrue(all(a["timings_ms"]["rerank"] is not None for a in answers))
        self.assertEqual(len(reranker.calls), len(QUERIES) + sum(len(t) for t in MANY))
        self.assertEqual(overlapping(reranker.calls, request_of), [])
        snap = root.client.app.state.orchestrator.rerank_gate.snapshot()
        self.assertEqual((snap["passed"], snap["timed_out"], snap["refused"]), (len(jobs), 0, 0))
        # without the gate four searches at once do rerank at once (what the gate is for)
        free = TrackingReranker(pause=0.05)
        orch = root.client.app.state.orchestrator
        with mock.patch.object(orch, "rerank_gate", None), mock.patch.object(orch, "reranker", free), \
                barrier_retrieve(4):
            list(self.executor(4).map(lambda q: self.search(root, q), QUERIES[:4]))
        self.assertGreater(free.peak, 1)
        self.assertTrue(overlapping(free.calls, request_of))

    def test_reranks_go_in_the_order_the_searches_reached_the_gate(self):
        reranker = TrackingReranker(pause=0)
        root = self.root(reranker=reranker)
        gate = root.client.app.state.orchestrator.rerank_gate
        gate.acquire()                                  # the test holds the GPU
        ex = self.executor(4)
        futures = []
        arrivals = [QUERIES[2], QUERIES[0], MANY[1], QUERIES[3]]
        try:
            for n, job in enumerate(arrivals):
                futures.append(ex.submit(self.search_many if isinstance(job, list) else self.search, root, job))
                wait_for(lambda: gate.snapshot()["waiting"] == n + 1)
        finally:
            gate.release()
        self.assertEqual([f.result(10).status_code for f in futures], [200] * 4)
        got = reranker.order[:2] + sorted(reranker.order[2:5]) + reranker.order[5:]
        self.assertEqual(got, [QUERIES[2], QUERIES[0], *sorted(MANY[1]), QUERIES[3]])


# --- the breaker through the gate ---------------------------------------------------------------

class BreakerTests(PipelineCase):

    def setUp(self):
        self.clock = Clock()
        self.breaker = CircuitBreaker(failures=3, open_seconds=30, clock=self.clock)
        self.allows = 0
        real_allow = self.breaker.allow

        def allow():
            self.allows += 1
            return real_allow()

        self.breaker.allow = allow
        self.reranker = TrackingReranker(pause=0.02, breaker=self.breaker)
        self.r = self.root(reranker=self.reranker)
        self.gate = self.r.client.app.state.orchestrator.rerank_gate

    def open_it(self):
        self.reranker.fail = True
        for q in QUERIES[:3]:
            self.assertEqual(self.search(self.r, q).json()["degraded"], OFF + ["reranker"])
        self.assertEqual(self.breaker.state, "open")
        self.reranker.fail = set()

    def test_an_open_breaker_skips_the_gate_without_waiting(self):
        self.open_it()
        calls, allows = len(self.reranker.calls), self.allows
        self.gate.acquire()                             # the gate is busy: a search that queued would hang here
        try:
            started = time.monotonic()
            body = self.search(self.r, QUERIES[4]).json()
            many = self.search_many(self.r, MANY[0]).json()
            legacy = self.r.client.post("/search", json={"query": QUERIES[5]}, headers=AUTH).json()
            self.assertLess(time.monotonic() - started, 3.0)
        finally:
            self.gate.release()
        self.assertEqual((body["degraded"], many["degraded"], legacy["degraded"]),
                         (OFF + ["reranker"], ["reranker"], ["reranker"]))
        self.assertEqual((body["timings_ms"]["rerank"], body["timings_ms"]["rerank_wait"]), (None, 0.0))
        self.assertEqual((many["timings_ms"]["rerank"], many["timings_ms"]["rerank_wait"]), (None, 0.0))
        self.assertEqual(legacy["rerank"]["skipped"], "circuit breaker open")
        self.assertEqual((len(self.reranker.calls), self.allows), (calls, allows))      # no call, no allow()

    def test_status_counts_how_often_the_breaker_opened(self):
        def status():
            return self.r.client.get("/v2/status", headers=AUTH).json()["pipeline"]

        self.assertEqual(set(status()), {"rerank_gate", "reranker_breaker_opens"})
        self.assertEqual(status()["reranker_breaker_opens"], 0)
        self.open_it()
        self.assertEqual(status()["reranker_breaker_opens"], 1)

    def test_a_half_open_breaker_recovers_through_the_gate(self):
        # with allow() as the pre-check the trial would be taken before the gate and refused inside it: 0 of
        # 120 searches reranked over 10 minutes, the breaker left half-open (challenge-pipelining 2)
        self.open_it()
        self.clock.now += 31
        self.assertEqual(self.breaker.state, "half-open")
        allows = self.allows
        with barrier_retrieve(3):
            answers = [r.json() for r in self.executor(3).map(lambda q: self.search(self.r, q), QUERIES[3:6])]
        self.assertEqual([a["degraded"] for a in answers], [OFF] * 3)            # the trial, then two more
        self.assertEqual(self.breaker.state, "closed")
        self.assertEqual(self.allows - allows, 3)       # one allow() each, inside the gate
        self.assertEqual(self.search_many(self.r, MANY[1]).json()["degraded"], [])

    def test_search_many_recovers_from_half_open_too(self):
        self.open_it()
        self.clock.now += 31
        allows = self.allows
        many = self.search_many(self.r, MANY[0]).json()
        self.assertEqual((many["degraded"], self.breaker.state, self.allows - allows), ([], "closed", 1))
        self.assertIsNotNone(many["timings_ms"]["rerank"])

    def test_a_failed_trial_opens_it_again_and_one_opened_while_a_search_waited_is_honoured(self):
        self.open_it()
        self.clock.now += 31
        self.reranker.fail = True
        self.assertEqual(self.search(self.r, QUERIES[6]).json()["degraded"], OFF + ["reranker"])
        self.assertEqual(self.breaker.state, "open")
        self.reranker.fail = set()
        self.clock.now += 31                            # half-open: the pre-check lets a search queue
        self.gate.acquire()
        try:
            future = self.executor(1).submit(self.search, self.r, QUERIES[7])
            wait_for(lambda: self.gate.snapshot()["waiting"] == 1)
            self.breaker.record_failure("timeout")      # meanwhile another trial failed: open again
            self.assertEqual(self.breaker.state, "open")
        finally:
            self.gate.release()
        calls = len(self.reranker.calls)
        self.assertEqual(future.result(10).json()["degraded"], OFF + ["reranker"])
        self.assertEqual(len(self.reranker.calls), calls)       # inside the gate allow() refused it


# --- waits past rerank_wait_seconds ------------------------------------------------------------

class QueueTimeoutTests(PipelineCase):

    def test_skip_answers_without_the_rerank_and_records_no_breaker_failure(self):
        breaker = CircuitBreaker(failures=1, open_seconds=30)
        reranker = TrackingReranker(pause=0, breaker=breaker)
        root = self.root(serving={**SERVING_ON, "pipeline": {"rerank_gate": 1, "rerank_wait_seconds": 0.1,
                                                              "rerank_on_timeout": "skip"}}, reranker=reranker)
        gate = root.client.app.state.orchestrator.rerank_gate
        gate.acquire()
        try:
            started = time.monotonic()
            body = self.search(root, QUERIES[0]).json()
            many = self.search_many(root, MANY[0]).json()
            legacy = root.client.post("/search", json={"query": QUERIES[1]}, headers=AUTH).json()
            took = time.monotonic() - started
        finally:
            gate.release()
        self.assertLess(took, 5.0)
        for answer in (body, many):
            self.assertEqual(answer["degraded"], (OFF if answer is body else []) + ["reranker"])
            self.assertIsNone(answer["timings_ms"]["rerank"])
            self.assertGreaterEqual(answer["timings_ms"]["rerank_wait"], 100)
        self.assertEqual((legacy["degraded"], legacy["rerank"]["skipped"]), (["reranker"], "rerank queue"))
        self.assertEqual(reranker.calls, [])
        # one failure would open this breaker: the queue is not the service failing
        self.assertEqual((breaker.state, breaker.snapshot()["consecutive_failures"]), ("closed", 0))
        self.assertEqual(gate.snapshot()["timed_out"], 3)
        self.assertEqual(self.search(root, QUERIES[2]).json()["degraded"], OFF)  # the gate free: reranked again

    def test_wait_the_default_keeps_the_search_in_line_and_reranks_it(self):
        reranker = TrackingReranker(pause=0)
        root = self.root(serving={**SERVING_ON, "pipeline": {"rerank_gate": 1, "rerank_wait_seconds": 0.1}},
                         reranker=reranker)
        gate = root.client.app.state.orchestrator.rerank_gate
        ex = self.executor(2)
        gate.acquire()
        try:
            search = ex.submit(self.search, root, QUERIES[0])
            many = ex.submit(self.search_many, root, MANY[0])
            wait_for(lambda: gate.snapshot()["overdue"] == 2)
            time.sleep(0.2)
            self.assertFalse(search.done() or many.done())
        finally:
            gate.release()
        for answer, off in ((search.result(10).json(), OFF), (many.result(10).json(), [])):
            self.assertEqual(answer["degraded"], off)
            self.assertIsNotNone(answer["timings_ms"]["rerank"])
            self.assertGreaterEqual(answer["timings_ms"]["rerank_wait"], 300)
        self.assertEqual(gate.snapshot()["timed_out"], 0)

    def test_a_full_gate_is_a_skip_at_once(self):
        # one search at a time and nobody may wait: only something outside the searches (the test) can fill it
        reranker = TrackingReranker(pause=0)
        root = self.root(serving={"max_concurrent_searches": 1, "pipeline": {"rerank_gate": 1,
                                                                             "rerank_max_waiting": 0}},
                         reranker=reranker)
        gate = root.client.app.state.orchestrator.rerank_gate
        gate.acquire()
        try:
            body = self.search(root, QUERIES[0]).json()
        finally:
            gate.release()
        self.assertEqual((body["degraded"], gate.snapshot()["refused"], reranker.calls), (OFF + ["reranker"], 1, []))


class FirstExceptionTests(PipelineCase):

    def test_a_failed_call_releases_the_gate_while_the_others_still_run(self):
        reranker = TrackingReranker(pause=0)
        reranker.hold, reranker.held = threading.Event(), set(MANY[0])
        self.addCleanup(reranker.hold.set)
        reranker.fail = {MANY[0][1]}                    # fails at once; the other two hang until released
        rerank = reranker.rerank

        def rerank_once_the_others_run(query, items):
            # the failure comes once the other two calls are at the service, as the scenario has it: on a slow or
            # busy host it can come before their threads start, and search_many then cancels them unstarted
            if reranker.failing(query):
                wait_for(lambda: reranker.running == 2)
            return rerank(query, items)

        reranker.rerank = rerank_once_the_others_run
        root = self.root(reranker=reranker)
        gate = root.client.app.state.orchestrator.rerank_gate
        started = time.monotonic()
        many = self.search_many(root, MANY[0]).json()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(many["degraded"], ["reranker"])
        self.assertEqual(gate.snapshot()["busy"], 0)    # released at the first failure
        wait_for(lambda: reranker.running == 2)         # the other two calls are (still) at the service
        body = self.search(root, QUERIES[0]).json()    # the next search does not wait for them
        self.assertEqual(body["degraded"], OFF)
        self.assertLess(body["timings_ms"]["rerank_wait"], 100)
        self.assertEqual(reranker.running, 2)
        reranker.hold.set()
        wait_for(lambda: reranker.running == 0)


class StarvationTests(PipelineCase):
    """Every lane full: searches admitted (one reranking, one at the gate), the admission queue full and a
    newcomer refused; /health and /v2/status still answer, and every search in line is reranked in the end."""

    def test_health_and_status_answer_with_every_lane_full(self):
        reranker = TrackingReranker(pause=0)
        reranker.hold = threading.Event()
        self.addCleanup(reranker.hold.set)
        root = self.root(serving={"max_concurrent_searches": 2, "max_queued_searches": 2,
                                  "pipeline": {"rerank_gate": 1}}, reranker=reranker)
        state = root.client.app.state
        gate = state.orchestrator.rerank_gate
        ex = self.executor(6)
        running = [ex.submit(self.search, root, q) for q in QUERIES[:2]]
        wait_for(lambda: reranker.running == 1 and gate.snapshot()["waiting"] == 1)
        queued = [ex.submit(self.search, root, q) for q in QUERIES[2:4]]
        wait_for(lambda: state.search_slots.waiting == 2)
        started = time.monotonic()
        refused = self.search(root, QUERIES[4])
        self.assertEqual((refused.status_code, refused.headers.get("retry-after")), (503, "2"))
        self.assertLess(time.monotonic() - started, 2.0)
        for path in ("/health", "/v2/status"):
            started = time.monotonic()
            answer = ex.submit(root.client.get, path, headers=AUTH).result(timeout=6)
            self.assertEqual(answer.status_code, 200, path)
            self.assertLess(time.monotonic() - started, 3.0, path)
        snap = root.client.get("/v2/status", headers=AUTH).json()["pipeline"]["rerank_gate"]
        self.assertEqual((snap["busy"], snap["waiting"]), (1, 1))
        reranker.hold.set()
        answers = [f.result(20).json() for f in running + queued]
        self.assertEqual([a["degraded"] for a in answers], [OFF] * 4)
        self.assertEqual(reranker.peak, 1)


# --- the same answers, the same c1 latency ------------------------------------------------------

def search_tests():
    """(TestCase class, names) of the tests that search through /v2/search or /v2/search_many."""
    out = []
    for cls in (test_api_v2.ApiV2Tests, test_rerank_breaker.SearchManyBreakerTests):
        names = [n for n in unittest.TestLoader().getTestCaseNames(cls)
                 if "/v2/search" in inspect.getsource(getattr(cls, n))]
        out.append((cls, names))
    return out


class ParityTests(unittest.TestCase):
    """The search tests of test_api_v2 and test_rerank_breaker, run without the pipeline and with it (four
    searches, the gate): every answer is the same, timings aside, and their own assertions hold both ways.
    Without the setting no gated code runs at all."""

    def run_tests(self, serving):
        answers = []
        post, init = TestClient.post, RootClient.__init__

        def recording(client, url, *args, **kwargs):
            response = post(client, url, *args, **kwargs)
            if url in ("/v2/search", "/v2/search_many"):
                body = response.json()
                answers.append((url, kwargs.get("json"), response.status_code,
                                {k: v for k, v in body.items() if k != "timings_ms"},
                                sorted(body.get("timings_ms") or {})))
            return response

        default = serving

        def with_serving(root, structure=True, kb=True, serving=None, retrieval=None, prepare=None):
            init(root, structure, kb, default if serving is None else serving, retrieval, prepare)

        suite = unittest.TestSuite(cls(name) for cls, names in search_tests() for name in names)
        result = unittest.TestResult()
        with mock.patch.object(TestClient, "post", recording), mock.patch.object(RootClient, "__init__", with_serving):
            suite.run(result)
        self.assertTrue(result.wasSuccessful(), [str(f[1]) for f in result.failures + result.errors])
        self.assertEqual(result.testsRun, suite.countTestCases())
        return answers

    def test_the_answers_are_the_same_with_the_pipeline(self):
        self.assertGreaterEqual(sum(len(names) for _, names in search_tests()), 15)
        never = mock.Mock(side_effect=AssertionError("gated code ran without the setting"))
        with mock.patch.object(pipeline.StageGate, "__init__", never), \
                mock.patch.object(RetrievalOrchestrator, "_gated_rerank", never), \
                mock.patch.object(api_v2, "gated_rerank_head", never):
            before = self.run_tests(None)
        never.assert_not_called()
        after = self.run_tests(SERVING_ON)
        self.assertGreater(len(before), 30)
        self.assertEqual(len(after), len(before))
        gated = 0
        for (url, body, code, answer, timings), (url2, body2, code2, answer2, timings2) in zip(before, after):
            self.assertEqual((url2, body2, code2, answer2), (url, body, code, answer))
            self.assertNotIn("rerank_wait", timings)
            self.assertLessEqual(set(timings2) - set(timings), {"rerank_wait"})
            gated += "rerank_wait" in timings2
        self.assertGreater(gated, 3)                    # the reranked ones did pass the gate


class C1LatencyTests(PipelineCase):
    """One search at a time gains nothing from the pipeline and must lose nothing: a fake reranker of 20 ms, the
    same searches with the gate and without it, interleaved in one process (so load drift hits both alike)."""

    def test_c1_latency_is_unchanged(self):
        root = self.root(reranker=TrackingReranker(pause=0.02))
        orch = root.client.app.state.orchestrator
        gate = orch.rerank_gate

        def timings(query, gated):
            with mock.patch.object(orch, "rerank_gate", gate if gated else None):
                return self.search(root, query).json()["timings_ms"]

        for q in QUERIES[:2]:
            timings(q, True)                                # warm
        on, off, waits = [], [], []
        for n in range(3):
            for i, q in enumerate(QUERIES):
                first = (n + i) % 2 == 0
                for gated in (first, not first):
                    t = timings(q, gated)
                    (on if gated else off).append(t["total"])
                    if gated:
                        waits.append(t["rerank_wait"])
                    else:
                        self.assertNotIn("rerank_wait", t)
        self.assertLess(max(waits), 5.0)                    # nobody ahead: the gate is passed at once
        self.assertLess(statistics.median(on), statistics.median(off) + 10.0, (off, on))


if __name__ == "__main__":
    unittest.main()
