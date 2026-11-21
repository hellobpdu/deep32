"""Opt-in collections (retrieval.json opt_in_doc_types, api_v2.filter_docs): a search, search_many, grep, /search or
document listing that names neither a doc_type nor docs leaves the opt-in doc types out; naming them reaches them.

The tiny root of tests/v2_root.py with its opt-in documents (OPT_IN_DOCS: system notes sharing the OSPF query terms,
two PowerShell documents sharing the Cisco guides' terms, an ISE API guide), BM25, and for dense an in-memory Qdrant."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import os
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from rag_core import api, api_v2
from rag_core.breaker import CircuitBreaker
from rag_core.db import connect
from rag_core.reader import Doc, RootReader
from rag_core.retrievers.dense import DenseRetriever

from test_api_v2 import AUTH, RootClient
from v2_root import add_opt_in_documents

OPT_IN = ["system", "powershell"]
HIDDEN = {"system", "powershell"}
OSPF = "NSSA Type-7 translator"             # the system notes hold every word
HOST = "IP address interface"               # the NetTCPIP module holds every word


def opt_in_root(opt_in=OPT_IN) -> RootClient:
    """The tiny root with OPT_IN_DOCS, serving opt_in_doc_types `opt_in` (None: the key absent)."""
    return RootClient(retrieval=None if opt_in is None else {"opt_in_doc_types": opt_in},
                      prepare=lambda paths, kb_src: add_opt_in_documents(paths))


def qdrant_dense(paths) -> DenseRetriever:
    """A DenseRetriever over an in-memory Qdrant holding every chunk of the root (payload chunk_id and doc_id, as
    build_root writes them), all at about the same distance from any query: a search returns every chunk it may."""
    dense = DenseRetriever.__new__(DenseRetriever)
    dense.paths = paths
    dense.config = SimpleNamespace(collection="c")
    dense.embedder = SimpleNamespace(embed=lambda texts, timeout=None: ([[1.0, 0.0] for _ in texts], "m"))
    client = QdrantClient(location=":memory:")
    client.create_collection("c", vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    with connect(paths.sqlite / "rag.db") as conn:
        rows = conn.execute("SELECT chunk_id, doc_id FROM chunks ORDER BY rowid").fetchall()
    client.upsert("c", points=[
        models.PointStruct(id=n, vector=[1.0, n / 1000], payload={"chunk_id": r["chunk_id"], "doc_id": r["doc_id"]})
        for n, r in enumerate(rows)
    ])
    dense.client = client

    def load(chunk_ids):
        marks = ",".join("?" for _ in chunk_ids)
        with connect(paths.sqlite / "rag.db") as conn:
            found = conn.execute(
                "SELECT chunk_id, doc_id, text, heading_path_json, page_start, page_end, metadata_json, "
                f"NULL AS embedding_model, NULL AS embedding_revision FROM chunks WHERE chunk_id IN ({marks})",
                chunk_ids).fetchall()
        return {r["chunk_id"]: dict(r) for r in found}

    dense._load_chunks = load
    return dense


class OptInCollectionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = opt_in_root()
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator
        cls.corp = api_v2.root_reader(cls.c.app.state)

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def post(self, path, body):
        r = self.c.post(path, json=body, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def get(self, path, **params):
        r = self.c.get(path, params=params, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def types(self, hits):
        return {h["doc_type"] for h in hits}

    def search(self, query=OSPF, **body):
        return self.post("/v2/search", {"query": query, "limit": 30, **body})["hits"]

    def many(self, *queries, **body):
        return self.post("/v2/search_many", {"queries": [{"text": q, "mode": m} for q, m in queries],
                                             "limit": 30, **body})["hits"]

    def without_opt_in(self):
        return mock.patch.dict(self.orch.config, {"opt_in_doc_types": []})

    def dense(self):
        dense = qdrant_dense(self.root.paths)
        self.addCleanup(dense.client.close)
        return mock.patch.multiple(self.orch, retrievers={**self.orch.retrievers, "dense": dense},
                                   breakers={"dense": CircuitBreaker()})

    # --- search and search_many -------------------------------------------------------------

    def test_a_default_search_never_returns_an_opt_in_document(self):
        for query in (OSPF, HOST):
            for mode in ("hybrid", "bm25"):
                with self.subTest(query=query, mode=mode):
                    hits = self.search(query, mode=mode)
                    self.assertTrue(hits)
                    self.assertFalse(self.types(hits) & HIDDEN)
        many = self.many((OSPF, "bm25"), (HOST, "hybrid"))
        self.assertTrue(many)
        self.assertFalse(self.types(many) & HIDDEN)
        # the same searches find them where nothing is opt-in: the documents are there to be left out
        with self.without_opt_in():
            self.assertIn("system", self.types(self.search(OSPF)))
            self.assertIn("powershell", self.types(self.search(HOST)))
            self.assertEqual(self.types(self.many((OSPF, "bm25"), (HOST, "hybrid"))) & HIDDEN, HIDDEN)

    def test_dense_leaves_them_out_in_qdrant(self):
        with self.dense():
            for mode in ("dense", "hybrid"):
                with self.subTest(mode=mode):
                    hits = self.search(HOST, mode=mode)
                    self.assertIn("dense", {m for h in hits for m in h["matched"]})
                    self.assertFalse(self.types(hits) & HIDDEN)
            many = self.many((HOST, "dense"), (OSPF, "dense"))
            self.assertIn("dense", {m for h in many for m in h["matched"]})
            self.assertFalse(self.types(many) & HIDDEN)
            # named, dense reaches them
            self.assertEqual(self.types(self.search(HOST, mode="dense", filters={"doc_type": "powershell"})),
                             {"powershell"})
            with self.without_opt_in():
                self.assertEqual(self.types(self.search(HOST, mode="dense")) & HIDDEN, HIDDEN)

    def test_a_doc_type_names_one_collection_opt_in_or_not(self):
        for doc_type, query in (("system", OSPF), ("powershell", HOST), ("cisco", HOST)):
            with self.subTest(doc_type=doc_type):
                self.assertEqual(self.types(self.search(query, filters={"doc_type": doc_type})), {doc_type})
                self.assertEqual(self.types(self.many((query, "bm25"), filters={"doc_type": doc_type})), {doc_type})

    def test_docs_name_exactly_those_documents(self):
        hits = self.search(OSPF, filters={"docs": ["sys-test-notes", "rfc3101"]})
        self.assertEqual({h["doc_key"] for h in hits}, {"sys-test-notes", "rfc3101"})
        hits = self.many((HOST, "bm25"), filters={"docs": ["ps-nettcpip"]})
        self.assertEqual({h["doc_key"] for h in hits}, {"ps-nettcpip"})

    def test_release_max_or_include_obsolete_alone_leave_them_out(self):
        for filters in ({"release_max": "17.9"}, {"include_obsolete": False}):
            with self.subTest(filters=filters):
                hits = self.search(OSPF, filters=filters)
                self.assertTrue(hits)
                self.assertFalse(self.types(hits) & HIDDEN)
                # with a doc_type that names them, the other filter still applies on top
                named = self.search(OSPF, filters={**filters, "doc_type": "system"})
                self.assertEqual({h["doc_key"] for h in named}, {"sys-test-notes"})
        self.assertNotIn("c9300-1718-rtng", {h["doc_key"] for h in self.search(OSPF, filters={"release_max": "17.9"})})

    def test_an_unknown_doc_type_is_still_a_404(self):
        detail = "unknown doc_type: 'ietf' (corpus has ['cisco', 'powershell', 'rfc', 'system'])"
        for path, body in (("/v2/search", {"query": OSPF}), ("/v2/search_many", {"queries": [{"text": OSPF}]})):
            r = self.c.post(path, json={**body, "filters": {"doc_type": "ietf"}}, headers=AUTH)
            self.assertEqual((r.status_code, r.json()["detail"]), (404, detail), path)
        r = self.c.post("/v2/grep", json={"pattern": "Type-7", "doc_type": "ietf"}, headers=AUTH)
        self.assertEqual((r.status_code, r.json()["detail"]), (404, detail))

    def test_legacy_search_leaves_them_out(self):
        def doc_types(body):
            ids = [c["chunk_id"] for c in body["candidates"]]
            marks = ",".join("?" for _ in ids)
            with connect(self.root.paths.sqlite / "rag.db") as conn:
                docs = {r[0] for r in conn.execute(f"SELECT doc_id FROM chunks WHERE chunk_id IN ({marks})", ids)}
            return {self.corp.by_id[d].doc_type for d in docs}

        self.assertFalse(doc_types(self.post("/search", {"query": OSPF, "limit": 50})) & HIDDEN)
        with self.without_opt_in():
            self.assertIn("system", doc_types(self.post("/search", {"query": OSPF, "limit": 50})))

    # --- grep, documents, status ------------------------------------------------------------

    def test_grep_scope(self):
        def keys(**body):
            return {m["doc_key"] for m in self.post("/v2/grep", {"pattern": "Type-7", **body})["matches"]}

        self.assertTrue(keys())
        self.assertNotIn("sys-test-notes", keys())
        self.assertEqual(keys(doc_type="system"), {"sys-test-notes"})
        self.assertEqual(keys(docs=["sys-test-notes", "rfc1587"]), {"sys-test-notes", "rfc1587"})
        self.assertEqual(keys(docs=["sys-test-notes", "rfc1587"], doc_type="rfc"), {"rfc1587"})
        address = {m["doc_key"] for m in self.post("/v2/grep", {"pattern": "IP address"})["matches"]}
        self.assertFalse({"ps-nettcpip", "ps-about-splatting"} & address)
        self.assertIn("ps-nettcpip", {m["doc_key"] for m in self.post(
            "/v2/grep", {"pattern": "IP address", "doc_type": "powershell"})["matches"]})
        with self.without_opt_in():
            self.assertIn("sys-test-notes", keys())

    def test_documents_list_the_default_collections_and_count_the_opt_in_ones(self):
        body = self.get("/v2/documents")
        self.assertEqual({d["doc_key"] for d in body["documents"]},
                         {"rfc3101", "rfc4271", "rfc1587", "c9300-179-rtng", "c9300-1718-rtng",
                          "ise31-ers-networkdevice"})
        self.assertEqual(body["opt_in"], {"powershell": 2, "system": 1})
        self.assertEqual(list(body), ["documents", "opt_in", "degraded"])
        system = self.get("/v2/documents", doc_type="system")
        self.assertEqual(([d["doc_key"] for d in system["documents"]], "opt_in" in system),
                         (["sys-test-notes"], False))
        self.assertEqual({d["doc_key"] for d in self.get("/v2/documents", doc_type="powershell")["documents"]},
                         {"ps-nettcpip", "ps-about-splatting"})
        with self.without_opt_in():
            everything = self.get("/v2/documents")
        self.assertEqual((len(everything["documents"]), "opt_in" in everything), (9, False))

    def test_status_reports_the_opt_in_doc_types(self):
        self.assertEqual(self.get("/v2/status")["opt_in_doc_types"], ["powershell", "system"])


class NoOptInDocumentsTests(unittest.TestCase):
    """opt_in_doc_types set, but the corpus holds none of those documents: every answer is as without the setting."""

    def test_the_answers_are_those_without_the_setting(self):
        answers = {}
        for opt_in in (None, [], ["system", "powershell"]):
            root = RootClient(retrieval=None if opt_in is None else {"opt_in_doc_types": opt_in})
            c = root.client

            def post(path, body):
                r = c.post(path, json=body, headers=AUTH)
                self.assertEqual(r.status_code, 200, r.text)
                return {k: v for k, v in r.json().items() if k not in ("timings_ms", "run_id", "trace_path")}

            try:
                answers[str(opt_in)] = [
                    post("/v2/search", {"query": OSPF, "limit": 30}),
                    post("/v2/search_many", {"queries": [{"text": OSPF}, {"text": HOST, "mode": "bm25"}]}),
                    post("/v2/grep", {"pattern": "Type-7"}),
                    c.get("/v2/documents", headers=AUTH).json(),
                    [x["chunk_id"] for x in post("/search", {"query": OSPF})["candidates"]],
                ]
                self.assertEqual(c.get("/v2/status", headers=AUTH).json()["opt_in_doc_types"],
                                 sorted(opt_in or []))
            finally:
                root.close()
        self.assertEqual(answers["[]"], answers["None"])
        self.assertEqual(answers["['system', 'powershell']"], answers["None"])


class FilterDocsTests(unittest.TestCase):
    """api_v2.filter_docs on a corpus of three doc types, two of them releases of one line."""

    def setUp(self):
        corp = RootReader.__new__(RootReader)
        corp.structured, corp._of_types = True, {}
        corp.docs = {}
        for key, doc_type, release in (("rfc1", "rfc", None), ("c179", "cisco", "17.9"), ("c1718", "cisco", "17.18"),
                                       ("sys1", "system", None), ("ps1", "powershell", None)):
            corp.docs[key] = Doc(f"doc_{key}", key, doc_type, None, release, None, None, Path(f"{key}.txt"))
        corp.by_id = {d.doc_id: d for d in corp.docs.values()}
        self.corp = corp

    def run_filter(self, opt_in, **filters):
        return api_v2.filter_docs(self.corp, None, api_v2.Filters(**filters), set(), frozenset(opt_in))

    def test_no_filter(self):
        self.assertEqual(self.run_filter(()), (None, None))
        self.assertEqual(self.run_filter(("system",)), (None, frozenset({"doc_sys1"})))
        self.assertEqual(self.run_filter(("system", "powershell")), (None, frozenset({"doc_sys1", "doc_ps1"})))

    def test_opt_in_types_the_corpus_does_not_have_are_no_restriction(self):
        self.assertEqual(self.run_filter(("manual", "paper")), (None, None))
        # and an unstructured root (no documents known) is none either: a default search still answers
        self.corp.structured, self.corp.docs, self.corp._of_types = False, {}, {}
        self.assertEqual(self.run_filter(("system",)), (None, None))

    def test_named_and_other_filters(self):
        opt_in = ("system", "powershell")
        self.assertEqual(self.run_filter(opt_in, doc_type="system"), (frozenset({"doc_sys1"}), None))
        self.assertEqual(self.run_filter(opt_in, docs=["ps1", "rfc1"]), (frozenset({"doc_ps1", "doc_rfc1"}), None))
        self.assertEqual(self.run_filter(opt_in, release_max="17.9"), (frozenset({"doc_rfc1", "doc_c179"}), None))
        self.assertEqual(self.run_filter((), release_max="17.9"),
                         (frozenset({"doc_rfc1", "doc_c179", "doc_sys1", "doc_ps1"}), None))
        self.assertEqual(self.run_filter(opt_in, doc_type="system", release_max="17.9"), (frozenset({"doc_sys1"}), None))
        self.assertEqual(self.run_filter(opt_in, docs=[]), (frozenset(), None))


class ConfigTests(unittest.TestCase):

    def test_the_setting(self):
        self.assertEqual(api_v2.opt_in_doc_types({}), frozenset())
        self.assertEqual(api_v2.opt_in_doc_types({"opt_in_doc_types": []}), frozenset())
        self.assertEqual(api_v2.opt_in_doc_types({"opt_in_doc_types": ["system", "powershell", "system"]}),
                         frozenset({"system", "powershell"}))
        for bad in ("powershell", None, True, [1], [["system"]], {"system": True}, ["system", None]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "opt_in_doc_types"):
                api_v2.opt_in_doc_types({"opt_in_doc_types": bad})

    def test_a_value_it_cannot_take_stops_the_start(self):
        class Orchestrator:
            retrievers = {"bm25": None}

            def __init__(self, paths):
                self.config = dict(config)

            def capabilities(self):
                return {}

        env = {**{k: v for k, v in os.environ.items() if not k.startswith("RAG_")},
               "RAG_CORE_API_TOKEN": "t" * 40, "RAG_ROOT": "/tmp/none", "RAG_TRACE": "off"}
        for config, starts in (({"opt_in_doc_types": "powershell"}, False), ({"opt_in_doc_types": [3]}, False),
                               ({"opt_in_doc_types": ["powershell"]}, True), ({}, True)):
            with self.subTest(config=config), mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(api, "RetrievalOrchestrator", Orchestrator), warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                if starts:
                    with TestClient(api.app) as c:
                        self.assertEqual(c.get("/health", headers={"Authorization": f"Bearer {'t' * 40}"})
                                         .status_code, 200)
                else:
                    with self.assertRaisesRegex(ValueError, "opt_in_doc_types"), TestClient(api.app):
                        pass


if __name__ == "__main__":
    unittest.main()
