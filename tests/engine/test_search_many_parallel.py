"""retrieval.json search_many_parallel (api_v2.retrieve_each): search_many retrieves its queries at once, in a
bounded pool, after one TEI call has embedded every dense query; the answers are the ones it gives one query after
another. The tiny root of tests/v2_root.py with BM25 and fake dense retrievers; no network."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import inspect
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from rag_core import api, api_v2, orchestrator
from rag_core.breaker import CircuitBreaker
from rag_core.db import connect
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.retrieval_types import QueryVariant, SearchHit
from rag_core.retrievers.dense import DenseRetriever

import test_api_v2
from test_api_v2 import AUTH, FlakyDense, RootClient     # not ApiV2Tests: it would run here too
from v2_root import chunk_id

# the one ApiV2Tests test left out of the parity run: its fake service fails its first call, and which query's
# call comes first depends on timing once the queries run at once (test_a_service_failing_one_call_fails_one_query)
FIRST_CALL_FAILS = "test_search_many_falls_back_to_bm25_per_query"


def search_many_tests() -> list[str]:
    """The tests of test_api_v2.ApiV2Tests that call /v2/search_many."""
    tests = test_api_v2.ApiV2Tests
    return [name for name in unittest.TestLoader().getTestCaseNames(tests)
            if "/v2/search_many" in inspect.getsource(getattr(tests, name)) and name != FIRST_CALL_FAILS]


def doc_of(root, chunk):
    with connect(root.paths.sqlite / "rag.db") as conn:
        return conn.execute("SELECT doc_id FROM chunks WHERE chunk_id = ?", (chunk,)).fetchone()[0]


def dense_hit(variant, doc_id, chunk):
    return SearchHit(retriever="dense", variant_id=variant.variant_id, purpose=variant.purpose, chunk_id=chunk,
                     doc_id=doc_id, rank=1, score=0.9, text="")


class ParityTests(unittest.TestCase):
    """The search_many tests of test_api_v2, run with the queries one after another and at once: every
    /v2/search_many answer they get is the same, timings aside, and their own assertions hold both ways."""

    def run_tests(self, parallel):
        answers, threads = [], []
        post, retrieve = TestClient.post, api_v2.retrieve

        def recording(client, url, *args, **kwargs):
            response = post(client, url, *args, **kwargs)
            if url == "/v2/search_many":
                body = response.json()
                answers.append((kwargs.get("json"), response.status_code,
                                {k: v for k, v in body.items() if k != "timings_ms"}))
            return response

        def spy(*args, **kwargs):
            threads.append(threading.current_thread().name)
            return retrieve(*args, **kwargs)

        suite = unittest.TestSuite(test_api_v2.ApiV2Tests(name) for name in search_many_tests())
        result = unittest.TestResult()
        with mock.patch.dict(test_api_v2.CONFIG, {"search_many_parallel": parallel}), \
                mock.patch.object(TestClient, "post", recording), mock.patch.object(api_v2, "retrieve", spy):
            suite.run(result)
        self.assertTrue(result.wasSuccessful(), [str(f[1]) for f in result.failures + result.errors])
        self.assertEqual(result.testsRun, suite.countTestCases())
        return answers, threads

    def test_the_answers_are_the_same_one_after_another_and_at_once(self):
        self.assertGreaterEqual(len(search_many_tests()), 7)
        sequential, one_by_one = self.run_tests(False)
        parallel, at_once = self.run_tests(True)
        self.assertGreater(len(sequential), 10)
        self.assertEqual(len(parallel), len(sequential))
        for before, after in zip(sequential, parallel):
            self.assertEqual(after, before)
        # one after another, every query ran in the request's thread; at once, in the pool
        self.assertFalse([t for t in one_by_one if t.startswith("ThreadPoolExecutor")])
        self.assertTrue([t for t in at_once if t.startswith("ThreadPoolExecutor")])


class BarrierDense:
    """A dense retriever whose searches each wait until `parties` searches wait with it (one after another, the
    barrier would break); it counts how many ran at once."""
    name = "dense"

    def __init__(self, parties, doc_id, chunk, pause=0.0):
        self.barrier = threading.Barrier(parties, timeout=5) if parties else None
        self.doc_id, self.chunk, self.pause = doc_id, chunk, pause
        self.lock = threading.Lock()
        self.running = self.most = 0

    def search(self, variant, *, limit=50, doc_ids=None):
        with self.lock:
            self.running += 1
            self.most = max(self.most, self.running)
        try:
            if self.barrier is not None:
                self.barrier.wait()
            time.sleep(self.pause)
        finally:
            with self.lock:
                self.running -= 1
        return [dense_hit(variant, self.doc_id, self.chunk)]


class VectorDense:
    """A dense retriever with the real one's two ways to a query vector: embed_queries (one call, several texts)
    and search(embedded=...). A text's vector picks the chunk it finds, so a leg given another text's vector
    answers differently."""
    name = "dense"

    def __init__(self, chunks):
        self.chunks = chunks            # [(doc_id, chunk_id)]
        self.batches, self.own = [], []
        self.lock = threading.Lock()

    @staticmethod
    def key(text):
        return f"Query: {text.strip()}"

    def vector(self, key):
        return [float(sum(map(ord, key)) % len(self.chunks))]

    def embed_queries(self, texts):
        with self.lock:
            self.batches.append(list(texts))
        return {self.key(t): (self.vector(self.key(t)), "m") for t in texts}

    def search(self, variant, *, limit=50, doc_ids=None, timings=None, embedded=None):
        if doc_ids is not None and not doc_ids:
            return []
        key = self.key(variant.text)
        if embedded and key in embedded:
            vector = embedded[key][0]
        else:
            with self.lock:
                self.own.append(variant.text)
            vector = self.vector(key)
        doc_id, chunk = self.chunks[int(vector[0])]
        return [dense_hit(variant, doc_id, chunk)]


class ParallelSearchManyTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient()
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def post(self, body):
        r = self.c.post("/v2/search_many", json=body, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def parallel(self, value=True):
        return mock.patch.dict(self.orch.config, {"search_many_parallel": value})

    def test_the_queries_run_at_once(self):
        target = chunk_id("rfc3101", 3)
        dense = BarrierDense(3, doc_of(self.root, target), target)
        queries = [{"text": text, "mode": "dense"} for text in ("Type-7 translation", "NSSA", "P-bit")]
        with mock.patch.dict(self.orch.retrievers, {"dense": dense}), self.parallel():
            body = self.post({"queries": queries})
        self.assertEqual(dense.most, 3)                     # all three legs waited together
        self.assertEqual(body["degraded"], ["reranker"])      # rerank auto on a root without a reranker
        self.assertEqual(body["hits"][0]["id"], target)
        self.assertEqual(body["hits"][0]["per_query_ranks"], {"0": 1, "1": 1, "2": 1})

    def test_the_pool_is_bounded(self):
        target = chunk_id("rfc3101", 3)
        queries = [{"text": f"Type-7 query {n}", "mode": "dense"} for n in range(4)]
        for value, most in ((2, 2), (1, 1), (True, 4)):
            dense = BarrierDense(0, doc_of(self.root, target), target, pause=0.2)
            with self.subTest(search_many_parallel=value), \
                    mock.patch.dict(self.orch.retrievers, {"dense": dense}), self.parallel(value):
                self.assertEqual(self.post({"queries": queries})["degraded"], ["reranker"])
            self.assertEqual(dense.most, most)

    def test_the_dense_queries_are_embedded_in_one_call(self):
        chunks = [(doc_of(self.root, c), c) for c in (chunk_id("rfc3101", 1), chunk_id("rfc4271", 2),
                                                      chunk_id("rfc1587", 1))]
        queries = [{"text": "Type-7 LSAs NSSA translator"},                 # hybrid: BM25 and dense
                   {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25"},
                   {"text": "elected translator restrictions", "mode": "dense"}]

        def run(parallel):
            dense = VectorDense(chunks)
            with mock.patch.dict(self.orch.retrievers, {"dense": dense}), self.parallel(parallel):
                return dense, self.post({"queries": queries, "limit": 8})

        one_by_one, sequential = run(False)
        batched, parallel = run(True)
        # one TEI call for the two dense queries' texts (the technical variant goes to BM25 only)
        self.assertEqual(batched.batches, [["Type-7 LSAs NSSA translator", "elected translator restrictions"]])
        self.assertEqual(batched.own, [])
        self.assertEqual((one_by_one.batches, sorted(one_by_one.own)),
                         ([], ["Type-7 LSAs NSSA translator", "elected translator restrictions"]))
        self.assertIn("dense_embed", parallel["timings_ms"])
        self.assertNotIn("dense_embed", sequential["timings_ms"])
        self.assertEqual({k: v for k, v in parallel.items() if k != "timings_ms"},
                         {k: v for k, v in sequential.items() if k != "timings_ms"})
        self.assertTrue(any("dense" in h["matched"] for h in parallel["hits"]))

    def test_without_a_dense_query_nothing_is_embedded(self):
        dense = VectorDense([(doc_of(self.root, chunk_id("rfc3101", 1)), chunk_id("rfc3101", 1))])
        with mock.patch.dict(self.orch.retrievers, {"dense": dense}), self.parallel():
            body = self.post({"queries": [{"text": "NSSA", "mode": "bm25"}, {"text": "Type-7", "mode": "bm25"}]})
            # no document to search: two queries, so they run at once (a single query runs one after another)
            nothing = self.post({"queries": [{"text": "NSSA"}, {"text": "Type-7", "mode": "dense"}],
                                 "filters": {"docs": []}})
        self.assertEqual((dense.batches, dense.own), ([], []))
        self.assertNotIn("dense_embed", body["timings_ms"])
        self.assertEqual(nothing["hits"], [])
        self.assertNotIn("dense_embed", nothing["timings_ms"])

    def test_a_single_query_runs_as_it_always_did(self):
        # nothing to run beside it, nothing to batch it with: its leg embeds its own text, beside its BM25 leg, in the
        # request's thread (a TEI call made first would only hold up the BM25 leg)
        chunks = [(doc_of(self.root, chunk_id("rfc3101", 1)), chunk_id("rfc3101", 1))]
        query = {"queries": [{"text": "Type-7 LSAs NSSA translator"}], "limit": 8}
        threads, retrieve = [], api_v2.retrieve

        def spy(*args, **kwargs):
            threads.append(threading.current_thread().name)
            return retrieve(*args, **kwargs)

        answers = {}
        for parallel in (False, True):
            dense = VectorDense(chunks)
            with mock.patch.dict(self.orch.retrievers, {"dense": dense}), self.parallel(parallel), \
                    mock.patch.object(api_v2, "retrieve", spy):
                answers[parallel] = self.post(query)
            self.assertEqual((dense.batches, dense.own), ([], ["Type-7 LSAs NSSA translator"]), parallel)
        self.assertNotIn("dense_embed", answers[True]["timings_ms"])
        self.assertEqual(len(threads), 2)
        self.assertFalse([t for t in threads if t.startswith("ThreadPoolExecutor")])
        self.assertEqual({k: v for k, v in answers[True].items() if k != "timings_ms"},
                         {k: v for k, v in answers[False].items() if k != "timings_ms"})
        self.assertTrue(any("dense" in h["matched"] for h in answers[True]["hits"]))

    def test_a_breaker_that_is_not_closed_keeps_the_queries_one_after_another(self):
        # a half-open breaker lets one trial call through: it goes to the first query, as it always did
        target = chunk_id("rfc3101", 3)
        clock = [0.0]
        breaker = CircuitBreaker(failures=1, open_seconds=30, clock=lambda: clock[0])
        breaker.record_failure("timeout")
        clock[0] = 31.0                                         # half-open now
        dense = BarrierDense(0, doc_of(self.root, target), target)
        queries = [{"text": "Type-7 translation", "mode": "dense"}, {"text": "NSSA", "mode": "dense"}]
        with mock.patch.dict(self.orch.retrievers, {"dense": dense}), \
                mock.patch.dict(self.orch.breakers, {"dense": breaker}), self.parallel():
            body = self.post({"queries": queries})
        self.assertEqual(dense.most, 1)
        self.assertEqual(breaker.state, "closed")               # the first query's leg was the trial, and passed
        self.assertEqual(body["degraded"], ["reranker"])
        self.assertEqual(body["hits"][0]["per_query_ranks"], {"0": 1, "1": 1})

    def test_a_service_failing_one_call_fails_one_query(self):
        # as test_api_v2's per-query fallback test, but at once: whichever query's dense call fails falls back to
        # BM25, the other keeps its dense hit
        target = chunk_id("rfc3101", 3)
        dense = FlakyDense(doc_of(self.root, target), target)
        queries = [{"text": "Type-7 translation", "mode": "dense"}, {"text": "Type-7 translation", "mode": "dense"}]
        with mock.patch.dict(self.orch.retrievers, {"dense": dense}), \
                mock.patch.dict(self.orch.breakers, {"dense": CircuitBreaker(failures=3, open_seconds=30)}), \
                self.parallel():
            body = self.post({"queries": queries})
        self.assertEqual(dense.calls, 2)
        self.assertEqual(body["degraded"], ["dense", "reranker"])
        hit = next(h for h in body["hits"] if h["id"] == target)
        self.assertIn("dense", hit["matched"])
        self.assertIn(1, hit["per_query_ranks"].values())                  # the dense query ranked it first
        self.assertTrue(all(set(h["per_query_ranks"]) <= {"0", "1"} for h in body["hits"]))


class SettingsTests(unittest.TestCase):

    def test_search_many_parallel(self):
        workers = api_v2.SEARCH_MANY_WORKERS
        for config, expected in (({}, 0), ({"search_many_parallel": False}, 0), ({"search_many_parallel": None}, 0),
                                 ({"search_many_parallel": True}, workers), ({"search_many_parallel": 2}, 2),
                                 ({"search_many_parallel": 16}, 16)):
            self.assertEqual(api_v2.search_many_settings(config).workers, expected, config)
        for bad in ("yes", "true", 0, -1, 1.5, [2], {"workers": 2}):
            with self.assertRaisesRegex(ValueError, "search_many_parallel"):
                api_v2.search_many_settings({"search_many_parallel": bad})

    def test_search_many_rerank(self):
        self.assertEqual(api_v2.search_many_settings({}).rerank, "per_query")
        for mode in ("per_query", "best_query"):
            self.assertEqual(api_v2.search_many_settings({"search_many_rerank": mode}).rerank, mode)
        for bad in ("best", "BEST_QUERY", None, True, ["best_query"]):
            with self.assertRaisesRegex(ValueError, "search_many_rerank"):
                api_v2.search_many_settings({"search_many_rerank": bad})

    def test_a_value_it_cannot_take_stops_the_start(self):
        class Orchestrator:
            retrievers = {"bm25": None}

            def __init__(self, paths):
                self.config = dict(config)

            def capabilities(self):
                return {}

        env = {**{k: v for k, v in os.environ.items() if not k.startswith("RAG_")},
               "RAG_CORE_API_TOKEN": "t" * 40, "RAG_ROOT": "/tmp/none", "RAG_TRACE": "off"}
        for config, starts in (({"search_many_parallel": "yes"}, False), ({"search_many_rerank": "best"}, False),
                               ({"search_many_parallel": 3, "search_many_rerank": "best_query"}, True)):
            with self.subTest(config=config), mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(api, "RetrievalOrchestrator", Orchestrator):
                if starts:
                    with TestClient(api.app) as c:
                        self.assertEqual(c.get("/health", headers={"Authorization": f"Bearer {'t' * 40}"})
                                         .status_code, 200)
                else:
                    with self.assertRaisesRegex(ValueError, "search_many_"), TestClient(api.app):
                        pass


class Embedding(SimpleNamespace):
    """A TEI client that records each call's texts."""

    def embed(self, texts, timeout=None):
        self.calls.append(list(texts))
        if self.fail:
            raise TimeoutError("TEI did not answer")
        return [[float(len(t))] for t in texts], "m"


class DenseBatchTests(unittest.TestCase):
    """DenseRetriever.embed_queries and search(embedded=...), as test_leg_timings builds the retriever."""

    def dense(self):
        dense = DenseRetriever.__new__(DenseRetriever)
        dense.config = SimpleNamespace(collection="c")
        dense.embedder = Embedding(calls=[], fail=False)
        self.queried = []
        dense.client = SimpleNamespace(
            query_points=lambda **kw: self.queried.append(kw["query"]) or SimpleNamespace(points=[
                SimpleNamespace(id="p1", score=0.9, payload={"chunk_id": "c1"})]))
        dense._load_chunks = lambda ids: {
            cid: {"doc_id": "d", "text": cid, "heading_path_json": "[]", "metadata_json": "{}", "page_start": None,
                  "page_end": None, "embedding_model": "m", "embedding_revision": None}
            for cid in ids
        }
        return dense

    def test_one_call_for_several_texts_keyed_as_search_embeds_them(self):
        dense = self.dense()
        vectors = dense.embed_queries(["NSSA", " NSSA ", "query: as typed", "  ", "BGP"])
        self.assertEqual(dense.embedder.calls, [["Query: NSSA", "query: as typed", "Query: BGP"]])
        self.assertEqual(vectors, {"Query: NSSA": ([11.0], "m"), "query: as typed": ([15.0], "m"),
                                   "Query: BGP": ([10.0], "m")})
        self.assertEqual(dense.embed_queries(["", " "]), {})
        self.assertEqual(len(dense.embedder.calls), 1)

    def test_a_leg_with_its_vector_does_not_call_tei(self):
        dense = self.dense()
        timings = {}
        hits = dense.search(QueryVariant("p", "primary", "NSSA"), limit=5, timings=timings,
                            embedded={"Query: NSSA": ([7.0], "batch-model")})
        self.assertEqual(dense.embedder.calls, [])
        self.assertEqual(self.queried, [[7.0]])
        self.assertEqual(hits[0].metadata["dense"]["query_model"], "batch-model")
        self.assertEqual(set(timings), {"qdrant_ms", "load_ms"})              # nothing embedded here
        # another text's vector is not used
        dense.search(QueryVariant("p", "primary", "BGP"), limit=5, embedded={"Query: NSSA": ([7.0], "m")})
        self.assertEqual((dense.embedder.calls, self.queried[-1]), ([["Query: BGP"]], [10.0]))


class Batching:
    """A dense retriever for the orchestrator: embed_queries records its texts, search records `embedded`."""
    name = "dense"
    batches, seen = [], []
    fail = False

    def __init__(self, paths):
        pass

    def embed_queries(self, texts):
        Batching.batches.append(list(texts))
        if Batching.fail:
            raise TimeoutError("TEI did not answer")
        return {f"Query: {t}": ([1.0], "m") for t in texts}

    def search(self, variant, *, limit=50, doc_ids=None, timings=None, embedded=None):
        Batching.seen.append(embedded)
        return []


class Plain:
    name = "bm25"
    seen = []

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50, doc_ids=None):
        return []


class QueryVectorTests(unittest.TestCase):
    """RetrievalOrchestrator.query_vectors (search_many's one TEI call) and search(embedded=...)."""

    def setUp(self):
        Batching.batches, Batching.seen, Batching.fail = [], [], False
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.factories = mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, {"bm25": Plain, "dense": Batching},
                                         clear=True)
        self.factories.start()
        self.addCleanup(self.factories.stop)

    def orch(self, **config):
        cfg = Path(self.tmp.name) / "retrieval.json"
        cfg.write_text(json.dumps({"rrf_k": 60, "candidate_limit_per_query": 10, "fused_limit": 10,
                                   "retriever_weights": {}, "retrievers": ["bm25", "dense"], **config}))
        with mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"}):
            return RetrievalOrchestrator(paths=mock.Mock(logs=Path(self.tmp.name) / "logs"))

    def test_the_texts_follow_each_query_plan(self):
        # the technical variant ("NSSA type-7") goes to dense too unless technical_variant_retrievers says not
        queries = ["how does NSSA type-7 work", "show ip ospf neighbor"]
        self.orch(technical_variant_retrievers=["bm25"]).query_vectors(queries)
        self.orch().query_vectors(queries)
        self.assertEqual(Batching.batches, [queries, [queries[0], "NSSA type-7", queries[1]]])

    def test_no_batch_without_a_closed_breaker_or_a_retriever_that_takes_vectors(self):
        orch = self.orch()
        clock = [0.0]
        orch.breakers["dense"] = CircuitBreaker(failures=1, open_seconds=30, clock=lambda: clock[0])
        orch.breakers["dense"].record_failure("timeout")
        self.assertIsNone(orch.query_vectors(["NSSA"]))                     # open
        clock[0] = 31.0
        self.assertIsNone(orch.query_vectors(["NSSA"]))                     # half-open: the trial is a search's
        self.assertEqual(Batching.batches, [])
        self.assertTrue(orch.breakers["dense"].allow())                     # and was not used up
        orch = self.orch()
        orch.retrievers["dense"] = Plain(None)
        self.assertIsNone(orch.query_vectors(["NSSA"]))
        self.assertIsNone(self.orch(retrievers=["bm25"]).query_vectors(["NSSA"]))
        self.assertIsNone(self.orch().query_vectors([" "]))

    def test_a_failed_batch_leaves_each_leg_to_embed_its_own(self):
        Batching.fail = True
        orch = self.orch()
        with self.assertLogs("rag_core.orchestrator", "WARNING"):
            self.assertIsNone(orch.query_vectors(["NSSA"]))
        self.assertEqual(orch.breakers["dense"].state, "closed")            # the legs' calls count, not this one

    def test_search_hands_the_vectors_to_the_retriever_that_takes_them(self):
        orch = self.orch()
        vectors = orch.query_vectors(["NSSA"])
        self.assertEqual(vectors, {"Query: NSSA": ([1.0], "m")})
        orch.search("NSSA", mode="fast", embedded=vectors)
        orch.search("NSSA", mode="fast")
        self.assertEqual(Batching.seen, [vectors, None])


if __name__ == "__main__":
    unittest.main()
