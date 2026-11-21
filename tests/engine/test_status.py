import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from rag_core import api
from rag_core.breaker import CircuitBreaker
from rag_core.corpus import corpus_fingerprint
from rag_core.db import connect, initialize
from rag_core.paths import RAGPaths
from rag_core.status import StatusService
from rag_core.structure import ensure_schema

TOKEN = "t" * 40
TEI_URL = "https://gpu-host.tailnet-example.ts.net/v1/embeddings"
SHAS = ["a" * 64, "b" * 64]


def make_root(root: Path, chunks: int = 2, structure: bool = True) -> None:
    db = initialize(RAGPaths(root))
    with connect(db) as conn:
        for i, sha in enumerate(SHAS):
            conn.execute("INSERT INTO documents (doc_id, sha256, source_name, original_filename, status) "
                         "VALUES (?, ?, 'rfc', ?, 'CHUNKED')", (f"doc_{i}", sha, f"rfc{i}.txt"))
        for i in range(chunks):
            conn.execute("INSERT INTO chunks (chunk_id, chunk_sha256, doc_id, ordinal, text, embedding_text, "
                         "token_count, char_count) VALUES (?, 'x', 'doc_0', ?, 'the rfc text', 'e', 3, 12)",
                         (f"chk_{i}", i))
        if structure:
            ensure_schema(conn)
            conn.execute("INSERT INTO doc_sources (doc_id, doc_key, doc_type, rel_path, sha256, n_lines, "
                         "structure_version) VALUES ('doc_0', 'rfc0', 'rfc', 'rfc/rfc0.txt', ?, 9, 'p1-v1')",
                         (SHAS[0],))
            conn.executemany("INSERT INTO doc_sections (section_id, doc_id, ordinal, level, anchor, "
                             "line_start, line_end, n_chars) VALUES (?, 'doc_0', ?, 1, ?, ?, ?, 10)",
                             [("rfc0#1", 0, "1", 1, 4), ("rfc0#2", 1, "2", 5, 9)])


class Dense:
    def __init__(self):
        self.calls = 0
        self.fail: Exception | None = None
        self.delay = 0.0

    def probe(self, timeout):
        self.calls += 1
        time.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return {"latency_ms": 12.5}


class Reranker:
    def probe(self, timeout):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", "http://192.0.2.10:8082/v1/rerank"))


class Orchestrator:
    def __init__(self, dense=None, reranker=None, enabled=("bm25", "dense")):
        self.enabled = list(enabled)
        self.retrievers = {"bm25": object()}
        if dense is not None:
            self.retrievers["dense"] = dense
        self.breakers = {"dense": CircuitBreaker()} if dense is not None else {}
        self.reranker = reranker


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class StatusTests(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.clock = Clock()

    def service(self, orch, **kw):
        svc = StatusService(orch, RAGPaths(self.root), generation="gen7", clock=self.clock, **kw)
        self.addCleanup(svc.close)
        return svc

    def test_healthy_counts_fingerprint_and_generation(self):
        make_root(self.root)
        status = self.service(Orchestrator(Dense())).status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["generation"], "gen7")
        self.assertEqual((status["n_docs"], status["n_chunks"]), (2, 2))
        self.assertEqual(status["corpus_fingerprint"], corpus_fingerprint(reversed(SHAS)))
        dense = status["backends"]["dense"]
        self.assertEqual((dense["ok"], dense["latency_ms"], dense["breaker"]), (True, 12.5, "closed"))
        self.assertEqual(status["backends"]["reranker"], {"enabled": False, "ok": None, "latency_ms": None,
                                                          "last_error_kind": None})
        self.assertEqual(status["degraded"], ["kb"])        # no kb.sqlite in this root
        self.assertEqual(status["backends"]["structure"], {"ok": True, "documents": 1, "sections": 2})

    def test_failing_dense_is_reported_by_kind_without_its_url(self):
        make_root(self.root)
        dense = Dense()
        request = httpx.Request("POST", TEI_URL)
        dense.fail = httpx.HTTPStatusError(f"Bad gateway for url '{TEI_URL}'", request=request,
                                           response=httpx.Response(502, request=request))
        status = self.service(Orchestrator(dense, reranker=Reranker())).status()
        self.assertTrue(status["ok"])                        # BM25 still answers
        self.assertEqual(status["backends"]["dense"]["ok"], False)
        self.assertEqual(status["backends"]["dense"]["last_error_kind"], "http_502")
        self.assertEqual(status["backends"]["reranker"]["last_error_kind"], "connect")
        self.assertEqual(status["degraded"], ["dense", "reranker", "kb"])
        text = json.dumps(status)
        for secret in ("gpu-host", "tailnet-example", "192.0.2", "http://", "https://", "8082"):
            self.assertNotIn(secret, text)

    def test_open_breaker_is_degraded_even_when_the_probe_answers(self):
        make_root(self.root)
        orch = Orchestrator(Dense())
        for _ in range(3):
            orch.breakers["dense"].record_failure("timeout")
        status = self.service(orch).status()
        dense = status["backends"]["dense"]
        self.assertEqual((dense["ok"], dense["breaker"], dense["last_error_kind"]), (True, "open", "timeout"))
        self.assertIn("dense", status["degraded"])

    def test_breaker_is_read_live_between_probe_rounds(self):
        make_root(self.root)
        dense = Dense()
        orch = Orchestrator(dense)
        svc = self.service(orch)
        self.assertEqual(svc.status()["degraded"], ["kb"])
        for _ in range(3):                                   # searches open it inside the cache window
            orch.breakers["dense"].record_failure("timeout")
        status = svc.status()
        self.assertEqual(dense.calls, 1)                     # still the cached probe round
        self.assertEqual(status["backends"]["dense"]["breaker"], "open")
        self.assertEqual(status["backends"]["dense"]["last_error_kind"], "timeout")
        self.assertEqual(status["degraded"], ["kb", "dense"])
        orch.breakers["dense"].record_success()
        self.assertEqual(svc.status()["degraded"], ["kb"])

    def test_root_without_structure_index(self):
        make_root(self.root, structure=False)
        status = self.service(Orchestrator(Dense())).status()
        self.assertTrue(status["ok"])                        # search still works
        self.assertEqual(status["backends"]["structure"], {"ok": False, "documents": 0, "sections": 0})
        self.assertEqual(status["degraded"], ["kb", "structure"])

    def test_hanging_probe_times_out(self):
        make_root(self.root)
        dense = Dense()
        dense.delay = 1.0
        svc = self.service(Orchestrator(dense), probe_timeout=0.1)
        started = time.monotonic()
        status = svc.status()
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertEqual(status["backends"]["dense"]["last_error_kind"], "timeout")

    def test_probes_are_cached_briefly(self):
        make_root(self.root)
        dense = Dense()
        svc = self.service(Orchestrator(dense))
        svc.status()
        self.clock.now += 4.9
        svc.status()
        self.assertEqual(dense.calls, 1)
        self.clock.now += 0.1
        svc.status()
        self.assertEqual(dense.calls, 2)
        threads = [threading.Thread(target=svc.status) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(dense.calls, 2)                     # concurrent callers share one round

    def test_kb_tables_and_counts(self):
        make_root(self.root)
        (self.root / "kb").mkdir()
        with sqlite3.connect(self.root / "kb" / "kb.sqlite") as conn:
            conn.execute("CREATE TABLE rfc_meta (rfc INTEGER)")
            conn.executemany("INSERT INTO rfc_meta VALUES (?)", [(1,), (2,)])
            conn.execute("CREATE TABLE glossary (term TEXT)")
        status = self.service(Orchestrator(Dense())).status()
        self.assertEqual(status["backends"]["kb"]["tables"], {"glossary": 0, "rfc_meta": 2})
        self.assertEqual(status["degraded"], [])

    def test_empty_or_missing_root_is_not_ok(self):
        status = self.service(Orchestrator(None, enabled=("bm25",))).status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["backends"]["bm25"]["last_error_kind"], "FileNotFoundError")
        self.assertEqual(status["backends"]["dense"]["enabled"], False)
        self.assertEqual(status["degraded"], ["bm25", "kb", "structure"])
        make_root(self.root, chunks=0)
        self.clock.now += 10
        self.assertFalse(self.service(Orchestrator(None, enabled=("bm25",))).status()["ok"])

    def test_opt_in_doc_types_are_reported(self):
        make_root(self.root)
        orch = Orchestrator(Dense())
        self.assertEqual(self.service(orch).status()["opt_in_doc_types"], [])          # no config at all
        orch.config = {"opt_in_doc_types": ["system", "powershell", "system"]}
        self.assertEqual(self.service(orch).status()["opt_in_doc_types"], ["powershell", "system"])

    def test_dense_that_failed_to_start(self):
        make_root(self.root)
        status = self.service(Orchestrator(None, enabled=("bm25", "dense"))).status()
        self.assertEqual(status["backends"]["dense"]["last_error_kind"], "startup")
        self.assertIn("dense", status["degraded"])


class StatusEndpointTests(unittest.TestCase):

    def test_v2_status_requires_auth_and_reports_live_state(self):
        with tempfile.TemporaryDirectory() as d:
            make_root(Path(d))
            dense = Dense()
            env = {k: v for k, v in os.environ.items()
                   if k not in ("RAG_SERVING_CONFIG", "RAG_MAX_CONCURRENT_SEARCHES", "RAG_SEARCH_QUEUE_TIMEOUT")}
            env.update(RAG_CORE_API_TOKEN=TOKEN, RAG_ROOT=d)
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(api, "RetrievalOrchestrator", lambda paths: Orchestrator(dense)), \
                    TestClient(api.app) as c:
                self.assertEqual(c.get("/v2/status").status_code, 401)
                r = c.get("/v2/status", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(set(body), {"ok", "generation", "corpus_fingerprint", "n_docs", "n_chunks", "opt_in_doc_types",
                                     "backends", "degraded", "checked_at"})
        self.assertEqual(set(body["backends"]), {"bm25", "dense", "reranker", "kb", "structure"})
        self.assertTrue(body["ok"])
        self.assertEqual(body["opt_in_doc_types"], [])        # none configured: always present, empty
        # no generation in the repo's serving.json: the root is known by its directory name,
        # as in /v2/search (a built root's overlay names it; tests/test_api_v2.py)
        self.assertEqual(body["generation"], Path(d).name)


if __name__ == "__main__":
    unittest.main()
