"""0.1.0-alpha.3, WP-D item D4: every hit and every /v2/documents row carries the document's platform as the
structure index stores it (doc_sources.platform), and "degraded" names a backend the root is configured without:
"dense" for a hybrid or dense query, "reranker" unless the request says rerank "off"."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest
from unittest import mock

from rag_core import api_v2
from rag_core.db import connect

import fold_root
from test_api_v2 import AUTH, RootClient, WordReranker

PLATFORMS = {"c9300-179-rtng": "Catalyst 9300", "c9300-1718-rtng": "Catalyst 9300 (IOS XE 17.18)"}


def with_platforms(paths, kb_src):
    """sources/manifest.json names a platform for the two Cisco guides (rag_core.structure.load_manifest), as the
    corpus package does; the RFCs have none."""
    import json
    entries = [{"doc_key": key, "doc_type": "cisco", "platform": platform} for key, platform in PLATFORMS.items()]
    (paths.root / "sources" / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")


class PlatformTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=with_platforms, retrieval={"fold_identical": True})
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def post(self, path, body):
        r = self.c.post(path, json=body, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_the_index_stores_what_the_manifest_names(self):
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            stored = dict(conn.execute("SELECT doc_key, platform FROM doc_sources"))
        self.assertEqual({k: v for k, v in stored.items() if v}, PLATFORMS)

    def test_every_hit_carries_it_after_release(self):
        for path, body in (("/v2/search", {"query": "NSSA Type-7 translator", "limit": 30}),
                           ("/v2/search", {"query": "NSSA Type-7 translator", "deep": True}),
                           ("/v2/search_many", {"queries": [{"text": "NSSA translator"}, {"text": "OSPFv3"}]})):
            with self.subTest(path=path, body=body):
                hits = self.post(path, body)["hits"]
                self.assertGreaterEqual({h["doc_key"] for h in hits}, {"rfc3101", "c9300-179-rtng", "c9300-1718-rtng"})
                for hit in hits:
                    keys = list(hit)
                    self.assertEqual(keys[keys.index("release") + 1], "platform")
                    self.assertEqual(hit["platform"], PLATFORMS.get(hit["doc_key"]))

    def test_documents_carry_it(self):
        docs = {d["doc_key"]: d for d in self.c.get("/v2/documents", headers=AUTH).json()["documents"]}
        self.assertEqual({k: d["platform"] for k, d in docs.items()},
                         {"rfc3101": None, "rfc4271": None, "rfc1587": None, **PLATFORMS})


class OtherRootTests(unittest.TestCase):
    """Roots of their own (one served root at a time: rag_core.api.app is one application)."""

    def test_a_root_without_the_structure_index_says_null(self):
        root = RootClient(structure=False, kb=False)
        self.addCleanup(root.close)
        hits = root.client.post("/v2/search", json={"query": "NSSA translator"}, headers=AUTH).json()["hits"]
        self.assertTrue(hits)
        self.assertEqual({h["platform"] for h in hits}, {None})

    def test_a_hit_shown_as_a_pinned_copy_names_the_copy_s_platform(self):
        def prepare(paths, kb_src):
            import json
            fold_root.prepare(paths, kb_src)
            entries = [{"doc_key": "c9300-179-rtng", "doc_type": "cisco", "platform": "Catalyst 9300, 17.9"},
                       {"doc_key": "c9300-261-rtng", "doc_type": "cisco", "platform": "Catalyst 9300, 26.1"}]
            (paths.root / "sources" / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")

        root = RootClient(prepare=prepare, retrieval={"fold_identical": True})
        self.addCleanup(root.close)
        body = {"query": "NSSA ABR elected translator Type-7 LSAs"}
        plain = root.client.post("/v2/search", json=body, headers=AUTH).json()["hits"][0]
        pinned = root.client.post("/v2/search", json={**body, "prefer_release": ["26.1"]},
                                  headers=AUTH).json()["hits"][0]
        self.assertEqual((plain["doc_key"], plain["release"], plain["platform"]),
                         ("c9300-179-rtng", "17.9", "Catalyst 9300, 17.9"))
        self.assertEqual((pinned["doc_key"], pinned["release"], pinned["platform"]),
                         ("c9300-261-rtng", "26.1", "Catalyst 9300, 26.1"))
        self.assertEqual((list(pinned), pinned["text"]), (list(plain), plain["text"]))


class ConfiguredOffTests(unittest.TestCase):
    """The tiny root serves BM25 only, without a reranker (test_api_v2.CONFIG)."""

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient()
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def degraded(self, **body):
        path = "/v2/search_many" if "queries" in body else "/v2/search"
        r = self.c.post(path, json=body if "queries" in body else {"query": "NSSA translator", **body}, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["degraded"]

    def test_search_names_what_the_root_is_configured_without(self):
        for body, expected in (
            ({}, ["dense", "reranker"]),                                    # hybrid, rerank auto: the defaults
            ({"mode": "hybrid", "rerank": "on"}, ["dense", "reranker"]),
            ({"mode": "hybrid", "rerank": "off"}, ["dense"]),               # the caller asked for no rerank
            ({"mode": "dense"}, ["dense", "reranker"]),                     # as before for dense; BM25 answers
            ({"mode": "bm25"}, ["reranker"]),                               # the caller asked for keywords only
            ({"mode": "bm25", "rerank": "off"}, []),                        # nothing it asked for is missing
        ):
            with self.subTest(body=body):
                self.assertEqual(self.degraded(**body), expected)

    def test_search_many_names_them_per_request(self):
        bm25 = [{"text": "NSSA", "mode": "bm25"}, {"text": "Type-7", "mode": "bm25"}]
        self.assertEqual(self.degraded(queries=bm25), ["reranker"])
        self.assertEqual(self.degraded(queries=bm25, rerank="off"), [])
        self.assertEqual(self.degraded(queries=bm25 + [{"text": "translator"}], rerank="off"), ["dense"])
        self.assertEqual(self.degraded(queries=[{"text": "NSSA", "mode": "dense"}]), ["dense", "reranker"])

    def test_a_configured_backend_is_not_named(self):
        # a reranker that runs, and a dense retriever that started: nothing is configured off
        class Dense:
            name = "dense"

            def search(self, variant, *, limit=50, doc_ids=None):
                return []

        with mock.patch.object(self.orch, "reranker", WordReranker()), \
                mock.patch.dict(self.orch.retrievers, {"dense": Dense()}):
            self.assertEqual(self.degraded(), [])
            self.assertEqual(self.degraded(queries=[{"text": "NSSA"}, {"text": "Type-7", "mode": "dense"}]), [])

    def test_a_backend_that_failed_to_start_is_named_as_before(self):
        with mock.patch.dict(self.orch.degraded, {"dense": "ConnectError: refused"}), \
                mock.patch.object(self.orch, "reranker", WordReranker()):
            self.assertEqual((self.degraded(), self.degraded(mode="bm25")), (["dense"], []))
        with mock.patch.object(self.orch, "rerank_error", "ValueError: reranker url", create=True):
            self.assertEqual(self.degraded(mode="bm25"), ["reranker"])
            self.assertEqual(self.degraded(mode="bm25", rerank="off"), [])

    def test_the_legacy_search_and_the_status_are_unchanged(self):
        # /search answers what the orchestrator did (it names no backend the root is configured without), and
        # /v2/status keeps "degraded" for a backend that is enabled and not answering
        legacy = self.c.post("/search", json={"query": "NSSA translator"}, headers=AUTH).json()
        status = self.c.get("/v2/status", headers=AUTH).json()
        self.assertEqual((legacy["degraded"], status["degraded"]), ([], []))
        self.assertEqual((status["backends"]["dense"]["enabled"], status["backends"]["reranker"]["enabled"]),
                         (False, False))

    def test_plan_reads_only_what_every_orchestrator_has(self):
        orch = mock.Mock(degraded={"colbert": "missing index"}, retrievers={"bm25": 1})
        request = mock.Mock()
        request.app.state.orchestrator = orch
        self.assertEqual(api_v2.plan(request, "hybrid"), (None, {"colbert", "dense"}))
        orch.retrievers["dense"] = 1
        self.assertEqual(api_v2.plan(request, "hybrid"), (None, {"colbert"}))
        self.assertEqual(api_v2.plan(request, "bm25"), (frozenset({"bm25"}), set()))


if __name__ == "__main__":
    unittest.main()
