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

from fastapi.testclient import TestClient

from rag_core import api

TOKEN = "t" * 40

# RAG_ROOT too: $RAG_ROOT/config/serving.json overlays the repo file
ADMISSION_ENV = ("RAG_MAX_CONCURRENT_SEARCHES", "RAG_SEARCH_QUEUE_TIMEOUT", "RAG_SERVING_CONFIG", "RAG_ROOT")


def without_admission_env(**extra):
    """os.environ minus the admission overrides (a developer shell may set them), plus extra."""
    env = {k: v for k, v in os.environ.items() if k not in ADMISSION_ENV}
    return mock.patch.dict(os.environ, {**env, **extra}, clear=True)


class SlowOrchestrator:
    retrievers = {"bm25": None}
    active = 0
    peak = 0
    lock = threading.Lock()

    def __init__(self, paths):
        pass

    def capabilities(self):
        return {}

    def search(self, query, mode="normal", limit=8):
        with SlowOrchestrator.lock:
            SlowOrchestrator.active += 1
            SlowOrchestrator.peak = max(SlowOrchestrator.peak, SlowOrchestrator.active)
        time.sleep(0.3)
        with SlowOrchestrator.lock:
            SlowOrchestrator.active -= 1
        return {"query": query, "candidates": []}


class AdmissionTests(unittest.TestCase):

    def client(self, slots, timeout):
        SlowOrchestrator.active = SlowOrchestrator.peak = 0
        env = {"RAG_CORE_API_TOKEN": TOKEN, "RAG_ROOT": "/tmp/none",
               "RAG_MAX_CONCURRENT_SEARCHES": str(slots), "RAG_SEARCH_QUEUE_TIMEOUT": str(timeout)}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.orch = mock.patch.object(api, "RetrievalOrchestrator", SlowOrchestrator)
        self.orch.start()
        return TestClient(api.app)

    def tearDown(self):
        self.orch.stop()
        self.env.stop()

    def post(self, c, q):
        return c.post("/search", json={"query": q}, headers={"Authorization": f"Bearer {TOKEN}"})

    def test_searches_run_concurrently_up_to_the_limit(self):
        with self.client(slots=4, timeout=30) as c:
            t = time.time()
            with ThreadPoolExecutor(4) as ex:
                codes = [r.status_code for r in ex.map(lambda i: self.post(c, f"q{i}"), range(4))]
            elapsed = time.time() - t
        self.assertEqual(codes, [200] * 4)
        self.assertEqual(SlowOrchestrator.peak, 4)
        self.assertLess(elapsed, 1.0)          # serialized would take >= 1.2 s

    def test_saturation_returns_503_with_retry_after(self):
        with self.client(slots=1, timeout=0.05) as c:
            with ThreadPoolExecutor(3) as ex:
                rs = list(ex.map(lambda i: self.post(c, f"q{i}"), range(3)))
        codes = sorted(r.status_code for r in rs)
        self.assertIn(200, codes)
        self.assertIn(503, codes)
        self.assertEqual(next(r for r in rs if r.status_code == 503).headers.get("retry-after"), "2")
        self.assertEqual(SlowOrchestrator.peak, 1)


class DefaultAdmissionTests(unittest.TestCase):

    def test_default_admits_one_search_at_a_time(self):
        # an earlier change silently raised the engine from 1 (global lock) to 4 concurrent searches
        SlowOrchestrator.active = SlowOrchestrator.peak = 0
        with without_admission_env(RAG_CORE_API_TOKEN=TOKEN, RAG_ROOT="/tmp/none"), \
                mock.patch.object(api, "RetrievalOrchestrator", SlowOrchestrator), \
                TestClient(api.app) as c:
            auth = {"Authorization": f"Bearer {TOKEN}"}
            with ThreadPoolExecutor(2) as ex:
                codes = [r.status_code for r in ex.map(
                    lambda i: c.post("/search", json={"query": f"q{i}"}, headers=auth), range(2))]
        self.assertEqual(codes, [200, 200])
        self.assertEqual(SlowOrchestrator.peak, 1)

    def test_invalid_settings_stop_startup_before_loading_retrievers(self):
        loaded = mock.Mock(side_effect=SlowOrchestrator)
        with without_admission_env(RAG_CORE_API_TOKEN=TOKEN, RAG_ROOT="/tmp/none",
                                   RAG_MAX_CONCURRENT_SEARCHES="0"), \
                mock.patch.object(api, "RetrievalOrchestrator", loaded):
            with self.assertRaises(ValueError):
                with TestClient(api.app):
                    pass
        loaded.assert_not_called()


class AdmissionSettingsTests(unittest.TestCase):

    def settings(self, config=None, **env):
        with tempfile.TemporaryDirectory() as d:
            if config is not None:
                path = Path(d) / "serving.json"
                path.write_text(json.dumps(config))
                env["RAG_SERVING_CONFIG"] = str(path)
            with without_admission_env(**env):
                return api.admission_settings()

    def test_repo_config_default_is_explicit_and_one(self):
        repo = json.loads(api.SERVING_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(repo["max_concurrent_searches"], 1)
        self.assertEqual(repo["search_queue_timeout_seconds"], 30)
        self.assertEqual(self.settings(), (1, 30.0))

    def test_serving_config_then_env_override(self):
        cfg = {"max_concurrent_searches": 2, "search_queue_timeout_seconds": 5}
        self.assertEqual(self.settings(cfg), (2, 5.0))
        self.assertEqual(self.settings(cfg, RAG_MAX_CONCURRENT_SEARCHES="4"), (4, 5.0))
        self.assertEqual(self.settings(cfg, RAG_SEARCH_QUEUE_TIMEOUT="90"), (2, 90.0))

    def test_invalid_values_are_rejected_not_clamped(self):
        for env in ({"RAG_MAX_CONCURRENT_SEARCHES": "0"}, {"RAG_MAX_CONCURRENT_SEARCHES": "four"},
                    {"RAG_SEARCH_QUEUE_TIMEOUT": "-1"}, {"RAG_SEARCH_QUEUE_TIMEOUT": "nan"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.settings(**env)
        with self.assertRaises(ValueError):
            self.settings({"max_concurrent_searches": None})


if __name__ == "__main__":
    unittest.main()
