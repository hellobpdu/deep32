"""Per-request retriever subsets, document restrictions and the rerank switch (API v2)."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rag_core import orchestrator
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.retrieval_types import QueryVariant, SearchHit
from rag_core.retrievers import lexical
from rag_core.retrievers.dense import DenseRetriever
from rag_core.retrievers.lexical import LexicalRetriever

from test_api_v2 import AUTH, RootClient
from v2_root import build_root

VARIANT = QueryVariant("primary-x", "primary", "NSSA Type-7 translator")

# p1.4's BM25 statement for a search without filters (rag_core.retrievers.lexical.RANK_SQL in p1.4), byte for byte
P14_RANK_SQL = """
SELECT rowid, bm25(chunk_fts, 1.0, 2.0) AS raw_score
FROM chunk_fts
WHERE chunk_fts MATCH ?
ORDER BY raw_score ASC
LIMIT ?
"""


def hit(name, doc, cid, rank):
    return SearchHit(retriever=name, variant_id="v", purpose="primary", chunk_id=cid,
                     doc_id=doc, rank=rank, score=1.0, text=cid)


class Filtering:
    """A retriever that honours doc_ids."""
    name = "bm25"
    seen = []

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50, doc_ids=None):
        Filtering.seen.append(doc_ids)
        hits = [hit(self.name, d, f"{d}-1", 0) for d in ("a", "b", "c")]
        hits = [h for h in hits if doc_ids is None or h.doc_id in doc_ids]
        for rank, h in enumerate(hits, 1):
            h.rank = rank
        return hits


class Excluding(Filtering):
    """A retriever that also leaves documents out itself."""

    def search(self, variant, *, limit=50, doc_ids=None, exclude_doc_ids=None):
        Filtering.seen.append(("exclude", exclude_doc_ids))
        return [h for h in super().search(variant, limit=limit, doc_ids=doc_ids)
                if not exclude_doc_ids or h.doc_id not in exclude_doc_ids]


class Unfiltered:
    """A retriever without a doc_ids parameter (ColBERT/PLAID)."""
    name = "colbert"

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50):
        return [hit(self.name, d, f"{d}-2", i) for i, d in enumerate(("c", "a", "b"), 1)]


class Reranker:
    calls = 0

    def __init__(self, cfg):
        self.candidates = 10

    def rerank(self, query, items):
        Reranker.calls += 1
        return list(reversed(items))


class OrchestratorRestrictionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Path(self.tmp.name) / "retrieval.json"
        cfg.write_text(json.dumps({
            "rrf_k": 60, "candidate_limit_per_query": 10, "fused_limit": 10,
            "retriever_weights": {}, "retrievers": ["bm25", "colbert"],
            "reranker": {"enabled": True, "url": "http://127.0.0.1:9/x"},
        }))
        patches = [
            mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"}),
            mock.patch.dict(orchestrator.RETRIEVER_FACTORIES,
                            {"bm25": Filtering, "colbert": Unfiltered}, clear=True),
            mock.patch.object(orchestrator, "Reranker", Reranker),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.orch = RetrievalOrchestrator(paths=mock.Mock(logs=Path(self.tmp.name) / "logs"))
        Filtering.seen, Reranker.calls = [], 0

    def tearDown(self):
        self.tmp.cleanup()

    def ids(self, **kwargs):
        return [c["chunk_id"] for c in self.orch.search("q", mode="fast", **kwargs)["candidates"]]

    def test_defaults_are_unchanged(self):
        result = self.orch.search("q", mode="fast")
        self.assertEqual(result["active_retrievers"], ["bm25", "colbert"])
        self.assertEqual(Filtering.seen, [None])
        self.assertEqual(Reranker.calls, 1)

    def test_retriever_subset(self):
        result = self.orch.search("q", mode="fast", retrievers=frozenset({"bm25"}), rerank=False)
        self.assertEqual(result["active_retrievers"], ["bm25"])
        self.assertTrue(all(c["retrievers"] == ["bm25"] for c in result["candidates"]))
        self.assertIsNone(result["rerank"])
        self.assertEqual(Reranker.calls, 0)

    def test_doc_ids_are_pushed_down_or_filtered_afterwards(self):
        ids = self.ids(doc_ids=frozenset({"a"}), rerank=False)
        self.assertEqual(Filtering.seen, [frozenset({"a"})])
        self.assertEqual(sorted(ids), ["a-1", "a-2"])
        colbert = self.orch.search("q", mode="fast", retrievers=frozenset({"colbert"}),
                                   doc_ids=frozenset({"a", "b"}), rerank=False)
        ranks = {c["chunk_id"]: c["channels"] for c in colbert["candidates"]}
        self.assertEqual(sorted(ranks), ["a-2", "b-2"])
        # ranks are renumbered after filtering: "a-2" was 2nd, now 1st
        self.assertEqual([ch["rank"] for ch in ranks["a-2"].values()], [1])

    def test_exclude_doc_ids_are_pushed_down_or_dropped_afterwards(self):
        # Filtering takes doc_ids but not exclude_doc_ids, ColBERT neither: their hits of "b" are dropped afterwards
        result = self.orch.search("q", mode="fast", exclude_doc_ids=frozenset({"b"}), rerank=False)
        channels = {c["chunk_id"]: c["channels"] for c in result["candidates"]}
        self.assertEqual(sorted(channels), ["a-1", "a-2", "c-1", "c-2"])
        self.assertEqual(Filtering.seen, [None])                # it was asked as without the exclusion
        self.assertEqual([ch["rank"] for ch in channels["c-1"].values()], [2])     # renumbered: "b-1" is gone
        self.assertEqual([ch["rank"] for ch in channels["a-2"].values()], [2])
        # a retriever that takes exclude_doc_ids gets them (and doc_ids beside them); ColBERT is still filtered
        Filtering.seen = []
        with mock.patch.dict(self.orch.retrievers, {"bm25": Excluding(None)}):
            ids = self.ids(doc_ids=frozenset({"a", "b"}), exclude_doc_ids=frozenset({"b"}), rerank=False)
        self.assertEqual(Filtering.seen, [("exclude", frozenset({"b"})), frozenset({"a", "b"})])
        self.assertEqual(sorted(ids), ["a-1", "a-2"])
        # none to leave out is no exclusion: the call is the one without it
        Filtering.seen = []
        self.ids(exclude_doc_ids=frozenset(), rerank=False)
        self.assertEqual(Filtering.seen, [None])


class OptInDefaultsTests(unittest.TestCase):
    """With opt_in_doc_types absent, [] or naming doc types the corpus does not have, a search runs as in p1.4: the
    orchestrator gets no doc_ids and no exclude_doc_ids, and BM25 ranks with p1.4's rank-only statement."""

    class Recording:
        """A rag.db connection that records every statement it executes, with its parameters."""

        def __init__(self, conn, statements):
            self.conn, self.statements = conn, statements

        def execute(self, sql, params=()):
            self.statements.append((sql, tuple(params)))
            return self.conn.execute(sql, params)

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *exc):
            return self.conn.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    def test_the_default_path_is_p14s(self):
        self.assertEqual(lexical.RANK_SQL, P14_RANK_SQL)
        for retrieval in (None, {"opt_in_doc_types": []}, {"opt_in_doc_types": ["system", "powershell"]}):
            with self.subTest(retrieval=retrieval):
                root = RootClient(retrieval=retrieval)
                c = root.client
                orch = c.app.state.orchestrator
                statements = []
                real = lexical.connect
                try:
                    with mock.patch.object(orch, "search", wraps=orch.search) as spy, \
                            mock.patch.object(lexical, "connect", lambda path: self.Recording(real(path), statements)):
                        for path, body in (("/v2/search", {"query": "NSSA Type-7 translator"}),
                                           ("/v2/search_many", {"queries": [{"text": "NSSA translator"},
                                                                            {"text": "Type-7", "mode": "bm25"}]}),
                                           ("/search", {"query": "NSSA Type-7 translator"})):
                            self.assertEqual(c.post(path, json=body, headers=AUTH).status_code, 200, path)
                finally:
                    root.close()
                self.assertEqual(len(spy.call_args_list), 4)
                for call in spy.call_args_list:
                    self.assertIsNone(call.kwargs.get("doc_ids"))
                    self.assertNotIn("exclude_doc_ids", call.kwargs)
                ranked = [(sql, params) for sql, params in statements if "chunk_fts MATCH" in sql]
                self.assertGreaterEqual(len(ranked), 4)
                for sql, params in ranked:
                    self.assertEqual(sql, P14_RANK_SQL)
                    self.assertEqual(len(params), 2)            # the match expression and the limit
                    self.assertIsInstance(params[1], int)
                self.assertFalse([sql for sql, _ in statements if "JOIN documents" in sql or "rowid NOT IN" in sql])


class RetrieverFilterTests(unittest.TestCase):

    def test_bm25_restricts_in_sql(self):
        with tempfile.TemporaryDirectory() as d:
            paths = build_root(Path(d), with_kb_sources=False)
            bm25 = LexicalRetriever(paths)
            everything = bm25.search(VARIANT, limit=50)
            docs = {h.doc_id for h in everything}
            self.assertGreater(len(docs), 2)
            one = sorted(docs)[0]
            only = bm25.search(VARIANT, limit=50, doc_ids=frozenset({one}))
            self.assertEqual({h.doc_id for h in only}, {one})
            self.assertEqual([h.rank for h in only], list(range(1, len(only) + 1)))
            self.assertEqual(bm25.search(VARIANT, limit=50, doc_ids=frozenset()), [])

    def test_dense_sends_a_qdrant_filter(self):
        dense = DenseRetriever.__new__(DenseRetriever)
        dense.config = SimpleNamespace(collection="c")
        dense.embedder = SimpleNamespace(embed=lambda texts: ([[0.0]], "m"))
        calls = []
        dense.client = SimpleNamespace(
            query_points=lambda **kw: calls.append(kw) or SimpleNamespace(points=[])
        )
        dense._load_chunks = lambda ids: {}
        dense.search(VARIANT, limit=5, doc_ids=frozenset({"doc_b", "doc_a"}))
        dense.search(VARIANT, limit=5)
        condition = calls[0]["query_filter"].must[0]
        self.assertEqual((condition.key, condition.match.any), ("doc_id", ["doc_a", "doc_b"]))
        self.assertIsNone(calls[0]["query_filter"].must_not)
        self.assertIsNone(calls[1]["query_filter"])
        self.assertEqual(dense.search(VARIANT, limit=5, doc_ids=frozenset()), [])
        self.assertEqual(len(calls), 2)                 # an empty restriction asks nothing
        # documents left out (opt-in doc types): must_not, alone or beside must
        dense.search(VARIANT, limit=5, exclude_doc_ids=frozenset({"doc_s", "doc_p"}))
        dense.search(VARIANT, limit=5, doc_ids=frozenset({"doc_a"}), exclude_doc_ids=frozenset({"doc_s"}))
        dense.search(VARIANT, limit=5, exclude_doc_ids=frozenset())
        alone, both, none = (calls[i]["query_filter"] for i in (2, 3, 4))
        self.assertIsNone(alone.must)
        self.assertEqual([(c.key, c.match.any) for c in alone.must_not], [("doc_id", ["doc_p", "doc_s"])])
        self.assertEqual([(c.key, c.match.any) for c in both.must], [("doc_id", ["doc_a"])])
        self.assertEqual([(c.key, c.match.any) for c in both.must_not], [("doc_id", ["doc_s"])])
        self.assertIsNone(none)                         # nothing to leave out: no filter, as without it


if __name__ == "__main__":
    unittest.main()
