"""/v2/status "settings" (rag_core.status): the reranker as it started, search_many's and grep's
settings and the admission, so the trials chain can check that a switched root serves its arm (trials-p9.sh
settings_ok, flags_off). The fakes and tiny root of tests/test_status.py; the served app through its lifespan."""
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

from fastapi.testclient import TestClient

from rag_core import api
from rag_core.paths import RAGPaths
from rag_core.rerank import Reranker
from rag_core.status import StatusService

from test_status import TOKEN, Clock, Dense, Orchestrator, make_root

URL = "http://127.0.0.1:9/v1/rerank"


class SettingsTests(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        make_root(self.root)

    def status(self, orch, **kw):
        svc = StatusService(orch, RAGPaths(self.root), generation="gen7", clock=Clock(), **kw)
        self.addCleanup(svc.close)
        return svc.status()

    def reranker(self, **cfg):
        r = Reranker({"url": URL, **cfg})
        self.addCleanup(r.close)
        return r

    def test_a_root_as_production_serves_it(self):
        # p8r's overlay: the reranker on with its defaults, nothing else set
        orch = Orchestrator(Dense(), reranker=self.reranker())
        orch.config = {"reranker": {"enabled": True, "url": URL}}
        settings = self.status(orch, serving={"max_concurrent_searches": 1, "max_queued_searches": 16})["settings"]
        self.assertEqual(settings, {
            "reranker": {"candidates": 10, "search_many_candidates": 10, "max_chars": 2400, "window": "head"},
            "search_many": {"parallel": 0, "rerank": "per_query"},
            "grep": {"regex_engine": False},
            "serving": {"max_concurrent_searches": 1, "max_queued_searches": 16},
        })

    def test_an_arm_s_sets_are_shown(self):
        orch = Orchestrator(Dense(), reranker=self.reranker(candidates=15, max_chars=1200, window="centred",
                                                           search_many_candidates=10))
        orch.config = {"search_many_parallel": True, "search_many_rerank": "best_query", "grep": {"regex_engine": True}}
        status = self.status(orch, serving={"max_concurrent_searches": 4, "max_queued_searches": 12})
        settings = status["settings"]
        self.assertEqual(settings["reranker"], {"candidates": 15, "search_many_candidates": 10, "max_chars": 1200,
                                                "window": "centred"})
        self.assertEqual(settings["search_many"], {"parallel": 4, "rerank": "best_query"})
        self.assertEqual(settings["grep"], {"regex_engine": True})
        self.assertEqual(settings["serving"], {"max_concurrent_searches": 4, "max_queued_searches": 12})
        # as the chain reads it: rag_status JSON with the spaces taken out, every set's leaf=value inside "settings"
        text = json.dumps(status).replace(" ", "")
        block = text[text.index('"settings":') + len('"settings":'):]
        for shown in ('"candidates":15', '"search_many_candidates":10', '"max_chars":1200', '"window":"centred"',
                      '"rerank":"best_query"', '"regex_engine":true', '"max_concurrent_searches":4',
                      '"max_queued_searches":12'):
            self.assertIn(shown, block)
        self.assertRegex(block, r'"parallel":[1-9]')                      # search_many_parallel=true
        self.assertNotIn('"pipeline":{', text)                            # no rerank gate runs

    def test_no_reranker_and_no_config(self):
        orch = Orchestrator(Dense())
        orch.config = {"search_many_parallel": 3}
        settings = self.status(orch)["settings"]
        self.assertIsNone(settings["reranker"])
        self.assertEqual(settings["search_many"]["parallel"], 3)
        self.assertEqual(settings["serving"], {"max_concurrent_searches": None, "max_queued_searches": None})
        # an orchestrator without a retrieval config (only a stand-in has none) reports no settings
        self.assertNotIn("settings", self.status(Orchestrator(Dense())))


class SettingsEndpointTests(unittest.TestCase):

    def test_the_served_status_carries_the_admission_it_started_with(self):
        with tempfile.TemporaryDirectory() as d:
            make_root(Path(d))
            (Path(d) / "config").mkdir()
            (Path(d) / "config" / "serving.json").write_text(json.dumps({"max_concurrent_searches": 3,
                                                                         "max_queued_searches": 7}))
            orch = Orchestrator(Dense())
            orch.config = {"grep": {"regex_engine": False}}
            env = {k: v for k, v in os.environ.items()
                   if k not in ("RAG_SERVING_CONFIG", "RAG_MAX_CONCURRENT_SEARCHES", "RAG_SEARCH_QUEUE_TIMEOUT")}
            env.update(RAG_CORE_API_TOKEN=TOKEN, RAG_ROOT=d)
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(api, "RetrievalOrchestrator", lambda paths: orch), \
                    TestClient(api.app) as c:
                body = c.get("/v2/status", headers={"Authorization": f"Bearer {TOKEN}"}).json()
        self.assertEqual(body["settings"]["serving"], {"max_concurrent_searches": 3, "max_queued_searches": 7})
        self.assertEqual(body["settings"]["grep"], {"regex_engine": False})
        self.assertEqual(list(body)[-1], "settings")                    # after checked_at; no pipeline here


if __name__ == "__main__":
    unittest.main()
