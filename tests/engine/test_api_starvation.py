"""/health and /v2/status answer while searches hold the admission slots and queue (rag_core.api).

Sync endpoints and sync dependencies run in anyio's worker threads (40 by default). A search
waiting for an admission slot holds one of them, so enough waiting searches used to leave none
for /health, whose sync auth dependency needed a thread too: health checks timed out under
load. Now auth and /health run on the event loop, /v2/status probes under its own limiter,
and at most max_queued_searches searches wait (the rest get 503 at once).
"""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import anyio
from fastapi.testclient import TestClient

from rag_core import api

TOKEN = "s" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class BlockingOrchestrator:
    """Searches block until the test opens the gate; enough attributes for StatusService."""
    retrievers = {"bm25": None}
    enabled = ["bm25"]
    breakers: dict = {}
    reranker = None
    degraded: dict = {}

    gate = threading.Event()
    running = 0
    lock = threading.Lock()

    def __init__(self, paths):
        pass

    def capabilities(self):
        return {"bm25": True}

    def search(self, query, mode="normal", limit=8):
        with BlockingOrchestrator.lock:
            BlockingOrchestrator.running += 1
        BlockingOrchestrator.gate.wait(20)
        return {"query": query, "candidates": []}


class StarvationTests(unittest.TestCase):

    def setUp(self):
        BlockingOrchestrator.gate = threading.Event()
        BlockingOrchestrator.running = 0
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        serving = Path(d.name) / "serving.json"
        serving.write_text(json.dumps({"max_concurrent_searches": 1, "search_queue_timeout_seconds": 8,
                                       "max_queued_searches": 2}))
        env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        env.update(RAG_CORE_API_TOKEN=TOKEN, RAG_ROOT=str(Path(d.name) / "root"),
                   RAG_SERVING_CONFIG=str(serving))
        for patch in (mock.patch.dict(os.environ, env, clear=True),
                      mock.patch.object(api, "RetrievalOrchestrator", BlockingOrchestrator)):
            patch.start()
            self.addCleanup(patch.stop)

    def wait_for(self, predicate, seconds=5.0):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("condition not reached")
            time.sleep(0.01)

    def test_health_and_status_answer_while_searches_hold_every_worker_thread(self):
        with TestClient(api.app) as c, ThreadPoolExecutor(8) as ex:
            async def shrink(tokens):
                anyio.to_thread.current_default_thread_limiter().total_tokens = tokens

            async def borrowed():
                return anyio.to_thread.current_default_thread_limiter().borrowed_tokens

            c.portal.call(shrink, 4)
            slots = c.app.state.search_slots
            try:
                def search(i):
                    return c.post("/search", json={"query": f"q{i}"}, headers=AUTH)

                running = ex.submit(search, 0)                           # holds the one slot
                self.wait_for(lambda: BlockingOrchestrator.running == 1)
                queued = [ex.submit(search, i) for i in (1, 2)]          # wait for it: 2 threads
                self.wait_for(lambda: slots.waiting == 2)

                # the queue is full: the next search is refused at once, not after the 8 s timeout
                started = time.monotonic()
                refused = search(3)
                self.assertEqual((refused.status_code, refused.headers.get("retry-after")), (503, "2"))
                self.assertLess(time.monotonic() - started, 2.0)
                self.assertEqual(slots.refused, 1)

                # now take every worker thread: 3 are held by searches, 1 is left; leave none
                self.assertEqual(c.portal.call(borrowed), 3)
                c.portal.call(shrink, 3)

                for path in ("/health", "/v2/status"):
                    started = time.monotonic()
                    answer = ex.submit(c.get, path, headers=AUTH).result(timeout=6)
                    self.assertEqual(answer.status_code, 200, path)
                    self.assertLess(time.monotonic() - started, 3.0, path)
                self.assertEqual(ex.submit(c.get, "/health").result(timeout=6).status_code, 401)
                self.assertEqual(c.get("/v2/status", headers=AUTH).json()["generation"], "root")
            finally:
                BlockingOrchestrator.gate.set()
                c.portal.call(shrink, 40)
            self.assertEqual(running.result(timeout=10).status_code, 200)
            self.assertEqual([f.result(timeout=10).status_code for f in queued], [200, 200])
            self.assertEqual(slots.waiting, 0)


class AdmissionUnitTests(unittest.TestCase):

    def test_waiters_are_capped_and_counted(self):
        admission = api.Admission(slots=1, max_waiting=1)
        self.assertTrue(admission.acquire(blocking=False))
        self.assertFalse(admission.acquire(blocking=False))       # non-blocking never queues
        self.assertEqual(admission.waiting, 0)

        results = []
        waiter = threading.Thread(target=lambda: results.append(admission.acquire(timeout=5)))
        waiter.start()
        deadline = time.monotonic() + 5
        while admission.waiting != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(admission.waiting, 1)
        started = time.monotonic()
        self.assertFalse(admission.acquire(timeout=5))            # the queue is full: refused at once
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(admission.refused, 1)
        admission.release()                                       # the waiter gets the slot
        waiter.join(5)
        self.assertEqual((results, admission.waiting), ([True], 0))
        admission.release()
        with self.assertRaises(ValueError):                       # bounded, as before
            admission.release()

    def test_a_waiter_that_times_out_leaves_the_queue(self):
        admission = api.Admission(slots=1, max_waiting=1)
        admission.acquire()
        self.assertFalse(admission.acquire(timeout=0.05))
        self.assertEqual(admission.waiting, 0)
        self.assertFalse(admission.acquire(timeout=0.05))         # the freed place can be taken again
        zero = api.Admission(slots=1, max_waiting=0)
        zero.acquire()
        self.assertFalse(zero.acquire(timeout=5))                 # 0: nobody waits

    def test_queue_limit_setting(self):
        repo = json.loads(api.SERVING_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(repo["max_queued_searches"], api.DEFAULT_MAX_QUEUED_SEARCHES)
        self.assertEqual(api.queue_limit({}), api.DEFAULT_MAX_QUEUED_SEARCHES)
        self.assertEqual(api.queue_limit({"max_queued_searches": 0}), 0)
        for bad in (-1, "4", 2.5, True, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                api.queue_limit({"max_queued_searches": bad})


if __name__ == "__main__":
    unittest.main()
