"""retrieval.json "dense" (rag_core.retrievers.dense.DenseSearchSettings, tech radar V1): exact and hnsw_ef, off by
default. Unset, the Qdrant requests are byte for byte the ones the engine sent before these settings (captured from that version by a local
stand-in Qdrant and pinned below) and the /v2/status settings block is p81r's exact string; set, every serve-time
search (unfiltered, filtered, opt-in exclusion, both, search_many's batched vectors, the status probe) carries the
search params, the orchestrator refuses a value before anything starts, the status block gains "dense" last, and
derive_root sets them per root the way it sets retrieval.reranker.*."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rag_core import orchestrator
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.paths import RAGPaths
from rag_core.retrieval_types import QueryVariant
from rag_core.retrievers.dense import DenseRetriever, DenseSearchSettings, dense_search_settings
from rag_core.status import StatusService

import test_derive_root
from test_derive_root import SERVE_URL
from test_derive_trials import SAFE_ARG
from test_status import Clock, make_root

VARIANT = QueryVariant("v1", "primary", "ospf nssa")
QUERY = "/collections/rag_dense_x/points/query"
# POST bodies DenseRetriever sent before these settings (captured with this file's stand-in Qdrant, qdrant-client 1.19.1)
BASE = {
    "unfiltered": b'{"query":{"nearest":[0.25,-0.5,1.0]},"limit":50,"with_vector":false,"with_payload":["chunk_id"]}',
    "doc_ids": b'{"query":{"nearest":[0.25,-0.5,1.0]},"filter":{"must":[{"key":"doc_id","match":{"any":["d1","d2"]}}]},'
               b'"limit":50,"with_vector":false,"with_payload":["chunk_id"]}',
    "exclude": b'{"query":{"nearest":[0.25,-0.5,1.0]},"filter":{"must_not":[{"key":"doc_id","match":{"any":["x1","x2"]}}]},'
               b'"limit":50,"with_vector":false,"with_payload":["chunk_id"]}',
    "both": b'{"query":{"nearest":[0.25,-0.5,1.0]},"filter":{"must":[{"key":"doc_id","match":{"any":["d1"]}}],'
            b'"must_not":[{"key":"doc_id","match":{"any":["x1"]}}]},"limit":50,"with_vector":false,'
            b'"with_payload":["chunk_id"]}',
    "embedded": b'{"query":{"nearest":[0.5,0.5,0.5]},"limit":50,"with_vector":false,"with_payload":["chunk_id"]}',
    "probe": b'{"query":{"nearest":[0.25,-0.5,1.0]},"limit":1,"with_vector":false,"with_payload":false}',
}
# ops/kb-refresh-p9.sh P81R_SETTINGS: the block the production chain compares as a string
P81R_SETTINGS = ('{"reranker":{"candidates":15,"search_many_candidates":10,"max_chars":1600,"window":"centred"},'
                 '"search_many":{"parallel":0,"rerank":"per_query"},"grep":{"regex_engine":true},'
                 '"serving":{"max_concurrent_searches":2,"max_queued_searches":16}}')
P81R_RETRIEVAL = {"grep": {"regex_engine": True},
                  "reranker": {"enabled": True, "url": "https://gpu-host.tailnet-example.ts.net:8443/v1/rerank",
                               "candidates": 15, "max_chars": 1600, "window": "centred",
                               "search_many_candidates": 10}}


class _Qdrant(BaseHTTPRequestHandler):
    """A stand-in Qdrant: answers the client's version check and every points query (no points), and keeps each
    POST's path and body bytes."""
    protocol_version = "HTTP/1.1"
    seen: list = []

    def answer(self, obj):
        out = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        self.answer({"title": "qdrant - vector search engine", "version": "1.19.1"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).seen.append((self.path, body))
        self.answer({"result": {"points": []}, "status": "ok", "time": 0.0})

    def log_message(self, *a):
        pass


def serve(test):
    _Qdrant.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Qdrant)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    return f"http://127.0.0.1:{srv.server_address[1]}"


def every_search(dense):
    """Each serve-time search shape of the dense leg, in BASE's order."""
    dense.search(VARIANT, limit=50)
    dense.search(VARIANT, limit=50, doc_ids=frozenset({"d2", "d1"}))
    dense.search(VARIANT, limit=50, exclude_doc_ids=frozenset({"x2", "x1"}))
    dense.search(VARIANT, limit=50, doc_ids=frozenset({"d1"}), exclude_doc_ids=frozenset({"x1"}))
    dense.search(VARIANT, limit=50, embedded={"Query: ospf nssa": ([0.5, 0.5, 0.5], "m")})     # search_many's batch
    dense.probe(2.0)


class RequestTests(unittest.TestCase):
    """The requests a real DenseRetriever (the orchestrator's construction: DenseRetriever(paths), or with the
    options of retrieval.json "dense") sends through a real QdrantClient."""

    def requests(self, *options):
        url = serve(self)
        env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        env.update(RAG_QDRANT_URL=url, RAG_DENSE_COLLECTION="rag_dense_x")
        with mock.patch.dict(os.environ, env, clear=True):
            dense = DenseRetriever(SimpleNamespace(root=None, sqlite=Path("/nonexistent")), *options)
        dense.embedder.close()
        dense.embedder = SimpleNamespace(embed=lambda texts, timeout=None: ([[0.25, -0.5, 1.0] for _ in texts], "m"))
        self.addCleanup(dense.client.close)
        every_search(dense)
        self.assertEqual({path for path, _ in _Qdrant.seen}, {QUERY})
        return [body for _, body in _Qdrant.seen]

    def test_unset_the_requests_are_byte_for_byte_the_earlier_ones(self):
        for options in ((), ({},), ({"exact": False},), ({"hnsw_ef": None},), ({"exact": False, "hnsw_ef": None},)):
            with self.subTest(options=options):
                self.assertEqual(self.requests(*options), list(BASE.values()))

    def test_set_every_search_carries_the_params_and_nothing_else_changes(self):
        for options, params in (({"exact": True}, {"exact": True}), ({"hnsw_ef": 256}, {"hnsw_ef": 256})):
            with self.subTest(options=options):
                sent = self.requests(options)
                self.assertEqual(len(sent), len(BASE))
                for body, base in zip(sent, BASE.values()):
                    got = json.loads(body)
                    # the client sends the key that is set and nothing else: {"exact":true} / {"hnsw_ef":256}
                    self.assertEqual(got.pop("params"), params)
                    self.assertEqual(got, json.loads(base))


class SettingsTests(unittest.TestCase):

    def test_defaults_and_values(self):
        for options in (None, {}, {"exact": False}, {"hnsw_ef": None}):
            settings = dense_search_settings(options)
            self.assertEqual(settings, DenseSearchSettings())
            self.assertIsNone(settings.search_params())
            self.assertIsNone(settings.shown())
        exact = dense_search_settings({"exact": True})
        self.assertIs(exact.search_params().exact, True)
        self.assertEqual(exact.shown(), {"exact": True})
        ef = dense_search_settings({"hnsw_ef": 512})
        self.assertEqual(ef.search_params().hnsw_ef, 512)
        self.assertEqual(ef.shown(), {"hnsw_ef": 512})

    def test_values_rag_core_cannot_take_are_refused(self):
        for options in ({"exact": 1}, {"exact": "true"}, {"hnsw_ef": 0}, {"hnsw_ef": True}, {"hnsw_ef": 256.0},
                        {"hnsw_ef": "256"}, {"exact": True, "hnsw_ef": 256}, {"timeout": 5}, ["exact"], True):
            with self.subTest(options=options), self.assertRaises(ValueError) as caught:
                dense_search_settings(options)
            self.assertIn("dense", str(caught.exception))

    def test_a_retriever_made_without_init_searches_as_before(self):
        dense = DenseRetriever.__new__(DenseRetriever)
        self.assertEqual(dense._search_params(), {})


class OrchestratorTests(unittest.TestCase):

    def orch(self, config, client=None):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cfg = Path(d.name) / "retrieval.json"
        cfg.write_text(json.dumps({"rrf_k": 60, **config}))
        paths = mock.Mock(root=None, logs=Path(d.name) / "logs", sqlite=Path(d.name))
        built = mock.Mock(return_value=client or mock.Mock())
        env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        env.update(RAG_RETRIEVAL_CONFIG=str(cfg), RAG_TRACE="off")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("rag_core.retrievers.dense.make_qdrant_client", built):
            o = RetrievalOrchestrator(paths=paths)
        if "dense" in o.retrievers:
            self.addCleanup(o.retrievers["dense"].embedder.close)
        return o, built

    def test_the_dense_options_reach_the_retriever(self):
        o, _ = self.orch({"retrievers": ["bm25", "dense"], "dense": {"exact": True}})
        self.assertEqual(o.retrievers["dense"].search_settings, DenseSearchSettings(exact=True))
        self.assertEqual(o.capabilities()["degraded"], {})
        o, _ = self.orch({"retrievers": ["bm25", "dense"]})
        self.assertEqual(o.retrievers["dense"].search_settings, DenseSearchSettings())
        self.assertNotIn("search_settings", vars(o.retrievers["dense"]))         # the class default, untouched

    def test_a_bad_value_stops_the_start_before_any_retriever_is_made(self):
        bm25 = mock.Mock()
        with mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, {"bm25": bm25}):
            for bad in ({"exact": "yes"}, {"hnsw_ef": -1}, {"exact": True, "hnsw_ef": 64}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    self.orch({"retrievers": ["bm25", "dense"], "dense": bad})
        bm25.assert_not_called()


class StatusTests(unittest.TestCase):
    """The settings block as the production chain reads it (json without spaces), from a real DenseRetriever."""

    def block(self, options):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        make_root(root)
        from rag_core.rerank import Reranker
        reranker = Reranker(P81R_RETRIEVAL["reranker"])
        self.addCleanup(reranker.close)
        env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("rag_core.retrievers.dense.make_qdrant_client", return_value=mock.Mock()):
            dense = DenseRetriever(RAGPaths(root), *options)
        self.addCleanup(dense.embedder.close)
        orch = SimpleNamespace(enabled=["bm25", "dense"], retrievers={"bm25": object(), "dense": dense},
                               breakers={}, reranker=reranker, config=P81R_RETRIEVAL)
        svc = StatusService(orch, RAGPaths(root), generation="p81r-20261025", clock=Clock(),
                            serving={"max_concurrent_searches": 2, "max_queued_searches": 16})
        self.addCleanup(svc.close)
        svc._probe = lambda: {"corpus": (None, None), "kb": (None, None), "dense": (None, None),
                              "reranker": (None, None), "checked_at": "t"}
        return json.dumps(svc.status()["settings"], separators=(",", ":"))

    def test_unset_the_block_is_p81r_s_string(self):
        for options in ((), ({},), ({"exact": False, "hnsw_ef": None},)):
            with self.subTest(options=options):
                self.assertEqual(self.block(options), P81R_SETTINGS)

    def test_set_dense_comes_last_with_the_keys_that_are_set(self):
        self.assertEqual(self.block(({"exact": True},)), P81R_SETTINGS[:-1] + ',"dense":{"exact":true}}')
        self.assertEqual(self.block(({"hnsw_ef": 256},)), P81R_SETTINGS[:-1] + ',"dense":{"hnsw_ef":256}}')


@engine_target.SCRIPTS_SKIP
class DeriveTests(unittest.TestCase):
    # test_derive_root's fixture, as test_derive_arms borrows it (a TestCase named here would run here too)
    setUpClass = classmethod(test_derive_root.DeriveRootTests.setUpClass.__func__)
    tearDownClass = classmethod(test_derive_root.DeriveRootTests.tearDownClass.__func__)
    setUp = test_derive_root.DeriveRootTests.setUp
    run_derive = test_derive_root.DeriveRootTests.run_derive
    report = test_derive_root.DeriveRootTests.report
    config = test_derive_root.DeriveRootTests.config
    collections = test_derive_root.DeriveRootTests.collections
    refusal = test_derive_root.DeriveRootTests.refusal

    def test_exact_and_hnsw_ef_are_set_per_root_like_the_reranker_keys(self):
        from rag_core.config import load_config
        for name, sets, served in (
            ("gen-ex", ["retrieval.dense.exact=true"], DenseSearchSettings(exact=True)),
            ("gen-ef", ["retrieval.dense.hnsw_ef=256"], DenseSearchSettings(hnsw_ef=256)),
        ):
            args = [a for s in sets for a in ("--set", s)]
            with self.subTest(name):
                self.assertTrue(all(SAFE_ARG.match(a) for a in args), args)
                self.assertEqual(self.run_derive("--to", name, "--rerank-url", SERVE_URL, *args), 0)
                retrieval = load_config("retrieval", root=self.data / name)
                self.assertEqual(dense_search_settings(retrieval.get("dense")), served)
                self.assertEqual(self.report(name)["config"]["overrides"][:len(sets)],
                                 [f"{s.split('=')[0]}={json.dumps(json.loads(s.split('=')[1]))}" for s in sets])
                # the derived root, started as rag_core.api starts it, searches with them
                with mock.patch.dict(os.environ, {"RAG_TRACE": "off"}), \
                        mock.patch("rag_core.retrievers.dense.make_qdrant_client", return_value=mock.Mock()):
                    orch = RetrievalOrchestrator(RAGPaths(self.data / name))
                self.addCleanup(orch.retrievers["dense"].embedder.close)
                self.assertEqual(orch.retrievers["dense"].search_settings, served)
                self.assertEqual(orch.retrievers["dense"]._search_params(), {"search_params": served.search_params()})

    def test_values_rag_core_would_not_start_with_are_refused_before_anything_is_written(self):
        before = self.collections()
        for sets in (["retrieval.dense.exact=1"], ["retrieval.dense.hnsw_ef=0"], ["retrieval.dense.timeout=5"],
                     ["retrieval.dense.exact=true", "retrieval.dense.hnsw_ef=256"]):
            self.derive.log.reset_mock()
            with self.subTest(sets=sets):
                args = [a for s in sets for a in ("--set", s)]
                self.assertEqual(self.run_derive("--to", "gen-bad", *args), 2)
                self.assertIn("deep32-engine would not start with it", self.refusal())
                self.assertIn("dense", self.refusal())
                self.assertFalse((self.data / "gen-bad").exists())
        self.assertEqual(self.collections(), before)


if __name__ == "__main__":
    unittest.main()
