"""serving.json "preload_sources": the service loads the structure index and every source file in a background
thread at start, and stops it at shutdown; off, sources load on first use as before."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import threading
import time
import unittest
from unittest import mock

from rag_core import api, api_v2
from rag_core.reader import RootReader

from test_api_v2 import AUTH, RootClient


def wait_for(predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class SourcePreloadTests(unittest.TestCase):

    def test_on_every_source_is_loaded_without_a_request(self):
        root = RootClient(serving={"preload_sources": True})
        try:
            state = api.app.state
            self.assertTrue(wait_for(lambda: getattr(state, "reader", None) is not None
                                     and len(state.reader._files) == len(state.reader.docs)))
            self.assertGreater(len(state.reader.docs), 0)
            # a search after the preload uses the same reader
            r = root.client.post("/v2/search", json={"query": "NSSA translator", "limit": 2}, headers=AUTH)
            self.assertEqual(r.status_code, 200)
            self.assertIs(api_v2.root_reader(state), state.reader)
        finally:
            root.close()
        self.assertFalse(any(t.name == "source-preload" for t in threading.enumerate()))

    def test_off_nothing_is_loaded_before_a_request(self):
        root = RootClient(serving={"preload_sources": False})
        try:
            time.sleep(0.2)
            state = api.app.state           # module-level app: a reader of an earlier root may linger
            loaded = getattr(state, "reader", None)
            self.assertTrue(loaded is None or loaded.paths != state.paths)
            self.assertFalse(any(t.name == "source-preload" for t in threading.enumerate()))
        finally:
            root.close()

    def test_shutdown_stops_a_preload_in_progress(self):
        started, release = threading.Event(), threading.Event()
        seen = {}

        def slow_preload(self, *, stop=None):
            started.set()
            release.wait(5)
            seen["stop_set"] = stop.is_set()
            return 0

        with mock.patch.object(RootReader, "preload", slow_preload):
            root = RootClient(serving={"preload_sources": True})
            try:
                self.assertTrue(started.wait(5))
            finally:
                threading.Timer(0.2, release.set).start()
                root.close()
        self.assertEqual(seen, {"stop_set": True})

    def test_a_failed_preload_is_logged_and_the_service_still_answers(self):
        with mock.patch.object(RootReader, "preload", side_effect=OSError("disk gone")), \
                self.assertLogs("rag_core.api", "WARNING") as logs:
            root = RootClient(serving={"preload_sources": True})
            try:
                self.assertTrue(wait_for(lambda: any("source preload failed" in m for m in logs.output)))
                r = root.client.post("/v2/search", json={"query": "NSSA translator", "limit": 2}, headers=AUTH)
                self.assertEqual(r.status_code, 200)
            finally:
                root.close()

    @engine_target.POSIX_ONLY
    def test_a_root_without_a_database_gets_no_preload_and_no_database(self):
        root = RootClient(serving={"preload_sources": True})
        db = root.paths.sqlite / "rag.db"
        root.close()
        # the same app over a root that has only its config: no thread, nothing created
        import os
        import tempfile
        from pathlib import Path
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as d:
            bare = Path(d) / "bare"
            (bare / "config").mkdir(parents=True)
            (bare / "config" / "serving.json").write_text('{"preload_sources": true}')
            env = {"RAG_ROOT": str(bare), "RAG_CORE_API_TOKEN": "v" * 40, "RAG_TRACE": "off",
                   "RAG_RETRIEVAL_CONFIG": str(Path(d) / "retrieval.json")}
            (Path(d) / "retrieval.json").write_text('{"rrf_k": 60, "candidate_limit_per_query": 5, '
                                                    '"fused_limit": 5, "retriever_weights": {}, '
                                                    '"retrievers": ["bm25"]}')
            with mock.patch.dict(os.environ, env), TestClient(api.app):
                time.sleep(0.2)
                self.assertFalse(any(t.name == "source-preload" for t in threading.enumerate()))
            self.assertFalse((bare / "sqlite" / "rag.db").exists())
        self.assertFalse(db.exists())       # the first root's temp dir is gone too


if __name__ == "__main__":
    unittest.main()
