"""API v2 end to end on a tiny root (tests/v2_root.py): FastAPI TestClient, BM25 only, no network."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from rag_core import api, api_v2
from rag_core.breaker import CircuitBreaker
from rag_core.reader import (
    MAX_REGEX_SIZE, Doc, RootReader, Sec, best_line, cut_line, ref_candidates, regex_size, window,
)
from rag_core.db import connect
from rag_core.retrieval_types import SearchHit
from rag_core.kb import build_kb
from rag_core.structure import index_root

from v2_root import CISCO_179, build_root, chunk_id, line_of

TOKEN = "v" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HIT_KEYS = {"id", "doc_id", "doc_key", "title", "doc_type", "cite", "section_id", "section_path",
            "status", "release", "platform", "score", "matched", "text", "local", "url", "reranked"}

CONFIG = {
    "rrf_k": 60,
    "candidate_limit_per_query": 50,
    "fused_limit": 30,
    "retriever_weights": {"bm25": 1.0},
    "retrievers": ["bm25"],
    "technical_variant_retrievers": ["bm25"],
}


class RootClient:
    """A TestClient serving a freshly built tiny root."""

    def __init__(self, structure=True, kb=True, serving=None, retrieval=None, prepare=None):
        """retrieval: keys on top of CONFIG; prepare(paths, kb_src): run before the structure and KB
        are built (more documents, KB records)."""
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.paths = build_root(base)
        if prepare is not None:
            prepare(self.paths, base / "kb-src")
        if structure:
            index_root(self.paths)
        if kb:
            build_kb(base / "kb-src", self.paths.root / "kb" / "kb.sqlite")
        if serving is not None:             # the root's config overlay (rag_core.config)
            (self.paths.root / "config").mkdir(exist_ok=True)
            (self.paths.root / "config" / "serving.json").write_text(json.dumps(serving))
        config = base / "retrieval.json"
        config.write_text(json.dumps({**CONFIG, **(retrieval or {})}))
        env = {k: v for k, v in os.environ.items()
               if k not in ("RAG_MAX_CONCURRENT_SEARCHES", "RAG_SEARCH_QUEUE_TIMEOUT",
                            "RAG_SERVING_CONFIG")}
        env.update({"RAG_ROOT": str(self.paths.root), "RAG_CORE_API_TOKEN": TOKEN,
                    "RAG_RETRIEVAL_CONFIG": str(config), "RAG_TRACE": "off",
                    "RAG_SEARCH_QUEUE_TIMEOUT": "0.2"})
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.client = TestClient(api.app)
        self.client.__enter__()

    def close(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def source_lines(self, local):
        name, first, last = re.fullmatch(r"(.+):(\d+)-(\d+)", local).groups()
        doc_type = "rfc" if name.startswith("rfc") else "cisco"
        lines = (self.paths.root / "sources" / doc_type / name).read_text().split("\n")
        return lines[int(first) - 1:int(last)]


class ApiV2Tests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient()
        cls.c = cls.root.client

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

    # --- search ---------------------------------------------------------------------------

    def test_search_hit_contract(self):
        body = self.post("/v2/search", {"query": "P-bit clear forwarding address 0.0.0.0",
                                        "limit": 3, "max_chars": 300})
        self.assertEqual(set(body), {"hits", "degraded", "timings_ms", "generation"})
        self.assertEqual(body["degraded"], ["dense", "reranker"])       # a keyword-only, unreranked root
        self.assertEqual(body["generation"], "root")
        # the stages, then the slowest leg of each retriever that ran (and the dense leg's split)
        self.assertEqual(set(body["timings_ms"]), {"retrieve", "rerank", "assemble", "total", "bm25"})
        self.assertGreaterEqual(body["timings_ms"]["retrieve"], body["timings_ms"]["bm25"])
        hit = body["hits"][0]
        self.assertEqual(set(hit), HIT_KEYS)
        self.assertEqual(hit["doc_key"], "rfc3101")
        self.assertEqual(hit["cite"], "RFC 3101 §3.2")
        self.assertEqual(hit["section_id"], "rfc3101#3.2")
        self.assertEqual(hit["section_path"], ["3.0 Intra-AS Implementation Details",
                                               "3.2 Translating Type-7 LSAs into Type-5 LSAs"])
        self.assertEqual(hit["title"], "The OSPF Not-So-Stubby Area (NSSA) Option")
        self.assertEqual(hit["status"], "Proposed Standard; obsoletes RFC 1587; errata: 1 verified; "
                                        "errata on this section: EID 3001 (Verified)")
        self.assertEqual(hit["matched"], ["bm25"])
        self.assertEqual(hit["url"], "https://www.rfc-editor.org/rfc/rfc3101.html#section-3.2")
        self.assertLessEqual(len(hit["text"]), 300)
        # the window is centred on the matching passage, and `local` names its source lines
        self.assertIn("P-bit clear, or its forwarding", hit["text"])
        shown = [line for line in self.root.source_lines(hit["local"]) if line.strip()]
        self.assertEqual(shown[0].strip(), hit["text"].split("\n")[0].strip())
        self.assertEqual(shown[-1].strip(), hit["text"].split("\n")[-1].strip())

    def test_window_leaves_out_page_furniture(self):
        body = self.post("/v2/search", {"query": "Only installed Type-7 LSAs should be examined",
                                        "limit": 1, "max_chars": 4000})
        text = body["hits"][0]["text"]
        self.assertIn("Only installed", text)
        self.assertNotIn("[Page", text)
        self.assertNotIn("January 2003", text)

    def test_cisco_hit(self):
        body = self.post("/v2/search", {"query": "elected translator", "limit": 1})
        hit = body["hits"][0]
        self.assertEqual(hit["cite"], "Routing Configuration Guide, 17.9 › Configuring OSPF › "
                                      "Restrictions for OSPF NSSA")
        self.assertEqual(hit["section_id"], "c9300-179-rtng#restrictions-for-ospf-nssa")
        self.assertEqual(hit["release"], "17.9")
        self.assertEqual(hit["url"], "https://example.test/c9300-179-rtng")
        self.assertIsNone(hit["status"])
        self.assertTrue(hit["local"].startswith("c9300-179-rtng.txt:"))

    def test_filters(self):
        def keys(filters, query="NSSA Type-7 translator"):
            hits = self.post("/v2/search", {"query": query, "limit": 30, "filters": filters})["hits"]
            return {h["doc_key"] for h in hits}

        everything = keys({})
        self.assertLessEqual({"rfc3101", "rfc1587", "c9300-179-rtng", "c9300-1718-rtng"}, everything)
        self.assertEqual(keys({"doc_type": "cisco"}), {"c9300-179-rtng", "c9300-1718-rtng"})
        self.assertEqual(keys({"docs": ["rfc1587"]}), {"rfc1587"})
        self.assertNotIn("rfc1587", keys({"include_obsolete": False}))
        self.assertNotIn("c9300-1718-rtng", keys({"release_max": "17.9"}))
        self.assertIn("c9300-1718-rtng", keys({"release_max": "17.18"}))
        self.assertEqual(keys({"docs": []}), set())

    def test_release_max_excludes_year_numbered_releases(self):
        # from 2026 Cisco numbers IOS XE and SD-WAN releases by year (26.x): later than any 17.x or 20.x cap,
        # though another major line used to pass the filter
        def keys(filters):
            hits = self.post("/v2/search", {"query": "NSSA Type-7 translator", "limit": 30, "filters": filters})["hits"]
            return {h["doc_key"] for h in hits}

        doc = api_v2.root_reader(self.c.app.state).docs["c9300-1718-rtng"]
        for release in ("26.1", "26.x"):
            with self.subTest(release=release), mock.patch.object(doc, "release", release):
                self.assertIn("c9300-1718-rtng", keys({}))
                for cap in ("17.9", "17.18", "20.9"):
                    self.assertNotIn("c9300-1718-rtng", keys({"release_max": cap}), cap)
                self.assertIn("c9300-1718-rtng", keys({"release_max": "26.1"}))
                capped = keys({"release_max": "17.9"})
                # a document of the cap's own line, and documents without a release, still pass
                self.assertLessEqual({"c9300-179-rtng", "rfc3101"}, capped)
        self.assertTrue(api_v2.newer_release("26.1", "20.9"))
        self.assertFalse(api_v2.newer_release("20.15", "17.9"))
        self.assertFalse(api_v2.newer_release(None, "17.9"))

    def test_unknown_filter_values_are_404_not_an_empty_answer(self):
        # as /v2/grep: a mistyped doc_key or doc_type must not silently narrow the search to nothing
        for path, body in (("/v2/search", {"query": "NSSA"}),
                           ("/v2/search_many", {"queries": [{"text": "NSSA"}]})):
            for filters, detail in (({"docs": ["nosuchdoc"]}, "unknown documents: ['nosuchdoc']"),
                                    ({"docs": ["rfc3101", "rfc3l01"]}, "unknown documents: ['rfc3l01']"),
                                    ({"doc_type": "ietf"}, "unknown doc_type: 'ietf' (corpus has ['cisco', 'rfc'])")):
                with self.subTest(path=path, filters=filters):
                    r = self.c.post(path, json={**body, "filters": filters}, headers=AUTH)
                    self.assertEqual((r.status_code, r.json()["detail"]), (404, detail))
        self.assertTrue(self.post("/v2/search", {"query": "NSSA", "filters": {"docs": ["rfc3101"]}})["hits"])

    def test_modes_and_rerank_degrade_honestly(self):
        body = self.post("/v2/search", {"query": "NSSA translator", "mode": "dense", "rerank": "on"})
        self.assertEqual(body["degraded"], ["dense", "reranker"])
        self.assertTrue(body["hits"])                      # BM25 still answers
        self.assertEqual({m for h in body["hits"] for m in h["matched"]}, {"bm25"})

    def test_dense_mode_is_answered_by_bm25_when_dense_is_down(self):
        orch = self.c.app.state.orchestrator
        dense = FailingDense()
        breaker = CircuitBreaker(failures=2, open_seconds=30)
        with mock.patch.dict(orch.retrievers, {"dense": dense}), \
                mock.patch.dict(orch.breakers, {"dense": breaker}):
            for expected_calls in (1, 2, 2):            # two failures open the breaker; then skipped
                body = self.post("/v2/search", {"query": "NSSA translator", "mode": "dense"})
                self.assertEqual(dense.calls, expected_calls)
                self.assertEqual(body["degraded"], ["dense", "reranker"])
                self.assertTrue(body["hits"])
                self.assertEqual({m for h in body["hits"] for m in h["matched"]}, {"bm25"})
            self.assertEqual(breaker.state, "open")
            hybrid = self.post("/v2/search", {"query": "NSSA translator"})
            self.assertEqual((hybrid["degraded"], bool(hybrid["hits"])), (["dense", "reranker"], True))
            many = self.post("/v2/search_many", {"queries": [{"text": "NSSA", "mode": "dense"},
                                                             {"text": "Type-7", "mode": "bm25"}]})
            self.assertEqual((many["degraded"], bool(many["hits"])), (["dense", "reranker"], True))
            # a bm25 search never asked for dense: nothing it needed was missing
            self.assertEqual(self.post("/v2/search", {"query": "NSSA", "mode": "bm25"})["degraded"], ["reranker"])
        self.assertEqual(dense.calls, 2)

    def test_search_many_falls_back_to_bm25_per_query(self):
        # the first query's dense leg fails; the second one's answers and must be kept
        orch = self.c.app.state.orchestrator
        target = chunk_id("rfc3101", 3)
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            doc_id = conn.execute("SELECT doc_id FROM chunks WHERE chunk_id = ?", (target,)).fetchone()[0]
        dense = FlakyDense(doc_id, target)
        with mock.patch.dict(orch.retrievers, {"dense": dense}), \
                mock.patch.dict(orch.breakers, {"dense": CircuitBreaker(failures=3, open_seconds=30)}):
            body = self.post("/v2/search_many", {"queries": [{"text": "Type-7 translation", "mode": "dense"},
                                                             {"text": "Type-7 translation", "mode": "dense"}]})
        self.assertEqual(dense.calls, 2)
        self.assertEqual(body["degraded"], ["dense", "reranker"])                  # the first query's failure
        by_id = {h["id"]: h for h in body["hits"]}
        self.assertIn("dense", by_id[target]["matched"])
        self.assertIn("1", by_id[target]["per_query_ranks"])          # found by the second query's dense leg

    def test_a_retriever_that_failed_to_start_is_degraded_only_where_asked_for(self):
        orch = self.c.app.state.orchestrator
        with mock.patch.dict(orch.degraded, {"dense": "ConnectError: refused"}):
            def degraded(mode):
                return self.post("/v2/search", {"query": "NSSA", "mode": mode})["degraded"]

            self.assertEqual((degraded("hybrid"), degraded("dense"), degraded("bm25")),
                             (["dense", "reranker"], ["dense", "reranker"], ["reranker"]))
            self.assertEqual(orch.search("NSSA", retrievers=frozenset({"bm25"}))["degraded"], [])
            self.assertEqual(orch.search("NSSA")["degraded"], ["dense"])         # /search: all of them

    def test_duplicate_passages_are_dropped(self):
        body = self.post("/v2/search", {"query": "Type-7", "limit": 30, "max_chars": 4000})
        seen = {}
        for hit in body["hits"]:
            name, first, last = re.fullmatch(r"(.+):(\d+)-(\d+)", hit["local"]).groups()
            for other in seen.get(name, []):
                self.assertFalse(int(first) >= other[0] and int(last) <= other[1], hit["local"])
            seen.setdefault(name, []).append((int(first), int(last)))

    def test_request_validation(self):
        for body in ({"query": ""}, {"query": "x", "limit": 31}, {"query": "x", "max_chars": 100},
                     {"query": "x", "mode": "colbert"}):
            self.assertEqual(self.c.post("/v2/search", json=body, headers=AUTH).status_code, 422)

    def test_search_many(self):
        def run(weight):
            return self.post("/v2/search_many", {
                "queries": [{"text": "translator election Nt bit", "mode": "bm25"},
                            {"text": "area nssa translate type7 always", "weight": weight}],
                "limit": 5,
            })

        body = run(1.0)
        self.assertEqual(set(body), {"hits", "degraded", "degraded_detail", "timings_ms", "generation"})
        ranks = [h["per_query_ranks"] for h in body["hits"]]
        self.assertTrue(all(set(r) <= {"0", "1"} and r for r in ranks))
        # each window is centred on the terms of the queries that found the hit
        nt = next(h for h in body["hits"] if "bit Nt set" in h["text"])
        self.assertEqual(nt["section_id"], "rfc3101#3.1")
        self.assertIn("0", nt["per_query_ranks"])
        forced = next(h for h in body["hits"] if "translate type7 always" in h["text"])
        self.assertEqual(forced["per_query_ranks"], {"1": 1})
        # a low weight pushes the second query's best hit out of the top 5
        light = run(0.1)["hits"]
        self.assertFalse(any(h["id"] == forced["id"] for h in light))
        self.assertEqual(self.c.post("/v2/search_many", json={"queries": []},
                                     headers=AUTH).status_code, 422)

    def test_search_many_keeps_the_best_hit_of_each_full_weight_query(self):
        # every candidate of the heavy query outranks the light query's best by fused score
        # (3/(60+r) > 1/61 for all r < 123), so without the guarantee it never shows
        def run(weight, limit=3):
            return self.post("/v2/search_many", {"queries": [
                {"text": "Type-7 LSAs NSSA translator", "mode": "bm25", "weight": 3},
                {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25", "weight": weight}],
                "limit": limit, "max_chars": 200})["hits"]

        for limit in (2, 3, 5):
            hits = run(1.0, limit)
            self.assertEqual(len(hits), limit)
            second = [h for h in hits if "1" in h["per_query_ranks"]]
            self.assertEqual([h["per_query_ranks"]["1"] for h in second], [1], limit)
            self.assertEqual(second[0]["doc_key"], "rfc4271")
            self.assertEqual(sum("0" in h["per_query_ranks"] for h in hits), limit - 1)
        # below weight 1 there is no place kept: the fused order stands
        self.assertFalse(any("1" in h["per_query_ranks"] for h in run(0.9)))
        # more full-weight queries than places: the heavier ones, then query order, keep theirs
        hits = self.post("/v2/search_many", {"queries": [
            {"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
            {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25", "weight": 3},
            {"text": "Pat Murphy", "mode": "bm25"}], "limit": 2, "max_chars": 200})["hits"]
        self.assertEqual([h["per_query_ranks"].get(i) for h, i in zip(hits, ("1", "0"))], [1, 1])

    def test_search_many_reranks_each_candidate_against_its_own_queries(self):
        orch = self.c.app.state.orchestrator
        reranker = WordReranker(boost={"Type-7 LSAs NSSA translator": 0.9, "NSSA ABR elected translator": 0.95})
        queries = [{"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
                   {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25"},
                   {"text": "NSSA ABR elected translator", "mode": "bm25"}]
        with mock.patch.object(orch, "reranker", reranker):
            body = self.post("/v2/search_many", {"queries": queries, "limit": 8, "rerank": "on"})
        self.assertEqual(body["degraded"], [])
        self.assertIsNotNone(body["timings_ms"]["rerank"])
        calls = dict(reranker.calls)
        self.assertEqual(set(calls), {q["text"] for q in queries})       # one call per query
        by_id = {h["id"]: h for h in body["hits"]}
        for text, ids in calls.items():
            i = next(str(n) for n, q in enumerate(queries) if q["text"] == text)
            for cid in ids:             # only candidates this query retrieved were sent with it
                if cid in by_id:
                    self.assertIn(i, by_id[cid]["per_query_ranks"])
        judged_twice = 0
        for hit in body["hits"]:
            judged = [reranker.score(text, hit["id"]) for text, ids in calls.items() if hit["id"] in ids]
            if judged:              # the best score over the queries that retrieved it
                self.assertEqual(hit["score"], round(max(judged), 6))
                judged_twice += len(set(judged)) > 1
        self.assertGreater(judged_twice, 0)         # a passage two queries found, scored differently
        # the BGP passage only the second query found is judged by it (1.0), not by the first (0)
        first = body["hits"][0]
        self.assertEqual((first["doc_key"], first["score"]), ("rfc4271", 1.0))
        scored = [h["score"] for h in body["hits"] if any(h["id"] in ids for ids in calls.values())]
        self.assertEqual(scored, sorted(scored, reverse=True))

    def test_search_many_best_query_reranks_each_candidate_once(self):
        # search_many_rerank "best_query": each head candidate is scored once, against the query that ranked it
        # highest; the hits keep every query's first-stage ranks, and every full-weight query keeps a place
        orch = self.c.app.state.orchestrator
        queries = [{"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
                   {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25"},
                   {"text": "NSSA ABR elected translator", "mode": "bm25"}]
        body = {"queries": queries, "limit": 8}
        fused = {h["id"]: h["per_query_ranks"]
                 for h in self.post("/v2/search_many", {**body, "limit": 30, "rerank": "off"})["hits"]}
        per_query, best = WordReranker(), WordReranker()
        with mock.patch.object(orch, "reranker", per_query):
            self.post("/v2/search_many", {**body, "rerank": "on"})
        with mock.patch.object(orch, "reranker", best), \
                mock.patch.dict(orch.config, {"search_many_rerank": "best_query"}):
            hits = self.post("/v2/search_many", {**body, "rerank": "on"})["hits"]
        sent = [cid for _, ids in best.calls for cid in ids]
        self.assertEqual(len(sent), len(set(sent)))                         # each candidate once
        self.assertEqual(set(sent), {cid for _, ids in per_query.calls for cid in ids})     # the same head
        self.assertGreater(sum(len(ids) for _, ids in per_query.calls), len(sent))
        judged_by = {cid: text for text, ids in best.calls for cid in ids}
        for cid, text in judged_by.items():
            if cid in fused:            # the query that ranked it highest (all weights 1: the earlier on a tie)
                ranks = fused[cid]
                self.assertEqual(text, queries[int(min(ranks, key=lambda i: (ranks[i], int(i))))]["text"])
        for hit in hits:
            self.assertEqual(hit["per_query_ranks"], fused.get(hit["id"], hit["per_query_ranks"]))
            if hit["id"] in judged_by:
                self.assertEqual(hit["score"], round(best.score(judged_by[hit["id"]], hit["id"]), 6))
        self.assertEqual({i for h in hits for i in h["per_query_ranks"]}, {"0", "1", "2"})

    def test_search_many_keeps_a_place_after_the_rerank(self):
        # the reranker scores the second query's candidates last; its best still gets a place
        orch = self.c.app.state.orchestrator
        reranker = WordReranker(boost={"Type-7 LSAs NSSA translator": 1.0,
                                       "ORIGINATOR_ID BGP Identifier": 0.01})
        body_queries = [{"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
                        {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25"}]
        with mock.patch.object(orch, "reranker", reranker):
            hits = self.post("/v2/search_many", {"queries": body_queries, "limit": 2, "rerank": "on"})["hits"]
            unfair = self.post("/v2/search_many", {"queries": [dict(q, weight=0.5) for q in body_queries],
                                                   "limit": 2, "rerank": "on"})["hits"]
        self.assertEqual([sorted(h["per_query_ranks"]) for h in hits][0], ["0"])
        self.assertEqual(hits[1]["doc_key"], "rfc4271")
        self.assertTrue(all("0" in h["per_query_ranks"] for h in unfair))

    def test_search_many_keeps_the_fused_order_when_the_reranker_fails(self):
        orch = self.c.app.state.orchestrator
        reranker = WordReranker(fail=True)
        body = {"queries": [{"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
                            {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25"}], "limit": 5}
        plain = self.post("/v2/search_many", {**body, "rerank": "off"})
        with mock.patch.object(orch, "reranker", reranker):
            failed = self.post("/v2/search_many", {**body, "rerank": "on"})
        self.assertEqual(failed["degraded"], ["reranker"])
        self.assertEqual([h["id"] for h in failed["hits"]], [h["id"] for h in plain["hits"]])

    # --- reading --------------------------------------------------------------------------

    def test_section_paging_and_neighbours(self):
        first = self.get("/v2/section", ref="rfc3101#3.2", max_chars=80)
        self.assertEqual(first["title"], "3.2 Translating Type-7 LSAs into Type-5 LSAs")
        self.assertEqual(first["cite"], "RFC 3101 §3.2")
        self.assertEqual(first["prev_section_id"], "rfc3101#3.1")
        self.assertEqual(first["next_section_id"], "rfc3101#appendix-a")
        self.assertEqual(first["next_offset"], 80)
        self.assertTrue(first["local"].startswith("rfc3101.txt:"))
        pages, offset = [first["text"]], first["next_offset"]
        while offset is not None:
            page = self.get("/v2/section", ref="rfc3101#3.2", offset=offset, max_chars=80)
            pages.append(page["text"])
            offset = page["next_offset"]
        text = "".join(pages)
        self.assertEqual(len(text), first["total_chars"])
        self.assertNotIn("[Page", text)
        self.assertIn("then do nothing with this Type-7", text)

        parent = self.get("/v2/section", ref="rfc3101#3")
        self.assertIn("3.2 Translating", parent["text"])          # subsections included
        self.assertEqual(parent["next_section_id"], "rfc3101#appendix-a")

    def test_section_refs(self):
        self.assertEqual(self.get("/v2/section", ref="RFC3101#Section-3.2")["section_id"], "rfc3101#3.2")
        by_chunk = self.get("/v2/section", ref=chunk_id("rfc3101", 3))
        self.assertEqual(by_chunk["section_id"], "rfc3101#3.2")
        for ref in ("rfc3101#9", "nope", chunk_id("nope", 0)):
            r = self.c.get("/v2/section", params={"ref": ref}, headers=AUTH)
            self.assertEqual(r.status_code, 404, ref)

    def test_outline(self):
        body = self.get("/v2/outline", doc="rfc3101", depth=1)
        self.assertEqual(body["title"], "The OSPF Not-So-Stubby Area (NSSA) Option")
        self.assertEqual([s["section_id"] for s in body["sections"]],
                         ["rfc3101#preamble", "rfc3101#status-of-this-memo", "rfc3101#abstract",
                          "rfc3101#table-of-contents", "rfc3101#1", "rfc3101#3",
                          "rfc3101#appendix-a", "rfc3101#author-s-addresses"])
        deep = self.get("/v2/outline", doc="c9300-179-rtng")
        self.assertIn("c9300-179-rtng#examples-2", [s["section_id"] for s in deep["sections"]])
        self.assertTrue(all(s["n_chars"] >= 0 for s in deep["sections"]))
        self.assertEqual(self.c.get("/v2/outline", params={"doc": "x"}, headers=AUTH).status_code, 404)

    def test_outline_under_a_section(self):
        def ids(**params):
            body = self.get("/v2/outline", **params)
            return [s["section_id"] for s in body["sections"]]

        top = "c9300-179-rtng#configuring-ospf"
        self.assertEqual(ids(doc="c9300-179-rtng", under=top, depth=1),
                         [top, "c9300-179-rtng#restrictions-for-ospf-nssa", "c9300-179-rtng#examples",
                          "c9300-179-rtng#examples-2"])
        self.assertEqual(ids(doc="c9300-179-rtng", under="examples", depth=1),       # the anchor alone
                         ["c9300-179-rtng#examples",
                          "c9300-179-rtng#configuring-an-nssa-abr-as-a-forced-nssa-lsa-translator"])
        body = self.get("/v2/outline", doc="rfc3101", under="RFC3101#Section-3", depth=6)
        self.assertEqual((body["under"], [s["section_id"] for s in body["sections"]]),
                         ("rfc3101#3", ["rfc3101#3", "rfc3101#3.1", "rfc3101#3.2"]))
        self.assertNotIn("under", self.get("/v2/outline", doc="rfc3101"))
        # the preamble is nobody's parent
        self.assertEqual(ids(doc="rfc3101", under="preamble", depth=6), ["rfc3101#preamble"])

        # the same cut deep32-gateway makes from a depth-6 outline (rag_edge.outline_subtree)
        def edge_subtree(sections, under, depth):
            start = next(i for i, s in enumerate(sections) if s["section_id"] == under)
            level = sections[start]["level"]
            out = [sections[start]]
            for s in sections[start + 1:]:
                if s["level"] <= level:
                    break
                if s["level"] <= level + depth:
                    out.append(s)
            return out

        for doc in ("c9300-179-rtng", "rfc3101", "rfc4271"):
            whole = self.get("/v2/outline", doc=doc, depth=6)["sections"]
            for s in whole:
                if s["level"] == 0:
                    continue
                for depth in (1, 2, 6):
                    with self.subTest(doc=doc, under=s["section_id"], depth=depth):
                        cut = self.get("/v2/outline", doc=doc, under=s["section_id"], depth=depth)
                        self.assertEqual(cut["sections"], edge_subtree(whole, s["section_id"], depth))

        for params in ({"doc": "rfc3101", "under": "rfc3101#9"},
                       {"doc": "rfc3101", "under": "rfc4271#9.1"},            # another document's section
                       {"doc": "rfc3101", "under": "nope"}):
            r = self.c.get("/v2/outline", params=params, headers=AUTH)
            self.assertEqual(r.status_code, 404, params)
            self.assertIn("unknown section in rfc3101", r.json()["detail"])

    def test_documents(self):
        body = self.get("/v2/documents")
        self.assertEqual(set(body), {"documents", "degraded"})     # no opt-in doc types: no "opt_in" counts
        docs = {d["doc_key"]: d for d in body["documents"]}
        self.assertEqual(len(docs), 5)
        self.assertEqual(docs["rfc1587"]["status"], "Proposed Standard; OBSOLETED by RFC 3101")
        self.assertEqual(docs["c9300-1718-rtng"]["release"], "17.18")
        self.assertEqual(list(docs["rfc3101"])[3:5], ["release", "platform"])
        self.assertIsNone(docs["rfc3101"]["platform"])          # this fixture's sources name no platform
        self.assertEqual(docs["rfc3101"]["n_chunks"], 5)
        self.assertEqual(docs["rfc3101"]["source_url"], "https://example.test/rfc3101")
        self.assertGreater(docs["rfc4271"]["n_sections"], 4)
        cisco = self.get("/v2/documents", doc_type="cisco")["documents"]
        self.assertEqual({d["doc_key"] for d in cisco}, {"c9300-179-rtng", "c9300-1718-rtng"})

    def test_verify_quote(self):
        def verify(ref, quote):
            return self.post("/v2/verify_quote", {"ref": ref, "quote": quote})

        exact = verify("rfc3101#3.1", "has bit Nt set or")
        self.assertEqual((exact["result"], exact["section_id"]), ("exact", "rfc3101#3.1"))
        self.assertEqual(self.root.source_lines(exact["local"])[0].strip()[:30],
                         "A border router defers when an")
        # split by a page footer and header
        broken = verify("rfc3101#3.2", "Only installed Type-7 LSAs should be examined.")
        self.assertEqual((broken["result"], broken["section_id"]), ("normalized", "rfc3101#3.2"))
        self.assertIn("[Page 3]", "\n".join(self.root.source_lines(broken["local"])))
        self.assertNotIn("[Page", broken["match_text"])
        # hyphenation break, curly quotes and an omission
        self.assertEqual(verify("rfc3101#3.2", "The translation is skipped when the P‑bit is clear")["result"],
                         "normalized")
        self.assertEqual(verify("rfc3101#3.2", "If the Type-7 LSA has the P-bit clear ... do nothing")["result"],
                         "normalized")
        # outside the referenced section: found in the document, with the right section
        elsewhere = verify("rfc3101#3.2", "NSSAs are similar to stub areas.")
        self.assertEqual((elsewhere["result"], elsewhere["section_id"]), ("exact", "rfc3101#1"))
        self.assertEqual(verify(chunk_id("rfc3101", 2), "defaults to 40 seconds")["result"], "normalized")
        self.assertEqual(verify("rfc3101", "Pat Murphy")["section_id"], "rfc3101#author-s-addresses")
        missing = verify("rfc3101#3.2", "the P-bit is set")
        self.assertEqual((missing["result"], missing["local"]), ("not_found", None))
        # a blank quote verifies nothing ("" occurs at the section start)
        for blank in (" ", "\n\t "):
            self.assertEqual(verify("rfc3101#3.2", blank)["result"], "not_found")
        self.assertEqual(self.c.post("/v2/verify_quote", json={"ref": "x#1", "quote": "a"},
                                     headers=AUTH).status_code, 404)

    def test_grep(self):
        body = self.post("/v2/grep", {"pattern": "TYPE-7 LSAs should"})
        self.assertEqual(body["truncated"], False)
        (match,) = body["matches"]
        self.assertEqual((match["doc_key"], match["section_id"]), ("rfc3101", "rfc3101#3.2"))
        self.assertIn("Type-7 LSAs should be examined", match["text"])
        # page footers are not matched
        self.assertEqual(self.post("/v2/grep", {"pattern": "[Page 2]"})["matches"], [])
        regex = self.post("/v2/grep", {"pattern": r"translate\s+type7\s+\[?always", "regex": True,
                                       "docs": ["c9300-179-rtng"]})
        self.assertEqual({m["section_id"] for m in regex["matches"]},
                         {"c9300-179-rtng#examples",
                          "c9300-179-rtng#configuring-an-nssa-abr-as-a-forced-nssa-lsa-translator"})
        capped = self.post("/v2/grep", {"pattern": "type-7", "max_matches": 2})
        self.assertEqual((len(capped["matches"]), capped["truncated"]), (2, True))
        # doc_type scopes a grep as it scopes a search
        cisco = self.post("/v2/grep", {"pattern": "type-7", "doc_type": "cisco"})["matches"]
        self.assertEqual({m["doc_key"] for m in cisco}, {"c9300-179-rtng", "c9300-1718-rtng"})
        self.assertEqual(self.c.post("/v2/grep", json={"pattern": "x", "doc_type": "ietf"},
                                     headers=AUTH).status_code, 404)
        self.assertEqual(self.c.post("/v2/grep", json={"pattern": "(", "regex": True},
                                     headers=AUTH).status_code, 400)
        self.assertEqual(self.c.post("/v2/grep", json={"pattern": "x", "docs": ["nope"]},
                                     headers=AUTH).status_code, 404)

    def test_grep_regex_is_bounded_in_time(self):
        # the regex module defuses classic backtracking blow-ups; fuzzy matching is still costly
        with mock.patch.object(api_v2, "GREP_BUDGET_SECONDS", 0.2):
            started = time.monotonic()
            body = self.post("/v2/grep", {"pattern": "(?:neighbor information use the show ip){e<=10}",
                                          "regex": True})
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(body["truncated"])

    def test_grep_regex_is_bounded_when_it_compiles(self):
        # compiling runs in C with the GIL held and no timeout: each of these took 0.2-0.4 s and
        # 160-330 MB (a 25-character sibling took 3.9 s and 2.9 GB); verbose mode reads "1 0 0 0"
        # as 1000
        for pattern in (r"(?:a{1000}){1000}", "(?x)(?:a{1 0 0 0}){1 0 0 0}",
                        "(?:" + "." * 120 + "){9999}"):
            r = self.c.post("/v2/grep", json={"pattern": pattern, "regex": True}, headers=AUTH)
            self.assertEqual(r.status_code, 400, pattern)
            self.assertIn("unrolls to", r.json()["detail"])

    def test_grep_regex_is_not_cached(self):
        # the regex module keeps each compiled pattern (up to 500): ~140 MB each for patterns
        # like the ones above
        from regex import _main as regex_main
        pattern = r"Type-?7\s+LSAs?\s+should"
        self.assertTrue(self.post("/v2/grep", {"pattern": pattern, "regex": True})["matches"])
        self.assertFalse([key for key in regex_main._cache if key[0] == pattern])

    # --- knowledge base -------------------------------------------------------------------

    def test_rfc(self):
        body = self.get("/v2/rfc/3101", section="3.2")
        self.assertEqual((body["title"], body["status"], body["obsoletes"], body["doc_key"]),
                         ("The OSPF Not-So-Stubby Area (NSSA) Option", "Proposed Standard", [1587],
                          "rfc3101"))
        self.assertEqual([e["id"] for e in body["errata"]], [3001])
        self.assertEqual(body["errata_rejected"], 1)
        section = body["section"]
        self.assertEqual((section["section_id"], section["in_corpus"]), ("rfc3101#3.2", True))
        self.assertEqual([e["id"] for e in section["errata"]], [3001])
        self.assertIn("Dijkstra", section["text"])
        # an RFC in the corpus without that section: in_corpus says the RFC is, found that the
        # section is not (in_corpus false used to read as "RFC not in the corpus")
        missing = self.get("/v2/rfc/3101", section="9.9")["section"]
        self.assertEqual(missing, {"section_id": "rfc3101#9.9", "in_corpus": True, "found": False})
        self.assertEqual(self.get("/v2/rfc/2119", section="1")["section"],
                         {"section_id": "rfc2119#1", "in_corpus": False, "found": False})
        # a numbered heading by its title
        for title, section_id in (("Overview", "rfc3101#1"), ("Intra-AS Implementation Details", "rfc3101#3"),
                                  ("the options field", "rfc3101#appendix-a")):
            section = self.get("/v2/rfc/3101", section=title)["section"]
            self.assertEqual((section["section_id"], section["found"]), (section_id, True), title)
        # a section as typed, as cited, or as emitted all name the same section
        for typed in ("§3.2", "Section 3.2", "section-3.2", "3.2."):
            section = self.get("/v2/rfc/3101", section=typed)["section"]
            self.assertEqual((section["section_id"], [e["id"] for e in section["errata"]]),
                             ("rfc3101#3.2", [3001]), typed)
        for typed, section_id in (("Abstract", "rfc3101#abstract"), ("abstract", "rfc3101#abstract"),
                                  ("Author's Addresses", "rfc3101#author-s-addresses")):
            section = self.get("/v2/rfc/3101", section=typed)["section"]
            self.assertEqual((section["section_id"], section["in_corpus"]), (section_id, True), typed)
        self.assertIsNone(self.get("/v2/rfc/2119")["doc_key"])
        # SRV-08: where the "obsoleted by" chain ends (kb.current_rfcs; RfcSuccessionTests walk longer chains)
        self.assertEqual(self.get("/v2/rfc/1587")["current"], [{"rfc": 3101, "via": []}])
        self.assertEqual(body["current"], [])
        self.assertEqual(self.c.get("/v2/rfc/9999", headers=AUTH).status_code, 404)
        # RFC numbers have at most 5 digits; 2**63 used to overflow SQLite (500)
        for number in ("0", "100000", "9223372036854775808", "99999999999999999999"):
            self.assertEqual(self.c.get(f"/v2/rfc/{number}", headers=AUTH).status_code, 422, number)

    def test_lookup(self):
        section = self.get("/v2/lookup", id="rfc 4271 §9.1.2")
        self.assertEqual(section["kind"], "rfc_section")
        result = section["results"][0]
        self.assertEqual((result["in_corpus"], result["found"], result["heading"], result["cite"]),
                         (True, True, "9.1.2.  Phase 2: Route Selection", "RFC 4271 §9.1.2"))
        self.assertTrue(result["local"].startswith("rfc4271.txt:"))
        missing = self.get("/v2/lookup", id="RFC 3101 section 9.9")["results"][0]
        self.assertEqual((missing["in_corpus"], missing["found"]), (True, False))
        self.assertEqual(self.get("/v2/lookup", id="RFC 3101")["results"][0]["doc_key"], "rfc3101")
        self.assertEqual(self.get("/v2/lookup", id="LSA type 7")["results"][0]["name"],
                         "NSSA AS-external LSA")
        cli = self.get("/v2/lookup", id="sh ip ospf nei")
        self.assertEqual(cli["results"][0]["section_id"], "c9300-179-rtng#show-ip-ospf-neighbor")
        self.assertEqual(self.get("/v2/lookup", id="zzz"),
                         {"kind": "unknown", "results": [], "degraded": []})

    def test_command(self):
        body = self.get("/v2/command", q="sh ip ospf nei")
        self.assertEqual(set(body), {"results", "degraded"})
        self.assertEqual(body["degraded"], [])
        first = body["results"][0]
        self.assertEqual(set(first), {"command", "mode", "doc_key", "section_id", "line", "kind",
                                      "release", "platform"})
        self.assertTrue(first["command"].startswith("show ip ospf neighbor"))
        self.assertEqual(first["line"], line_of(CISCO_179, "show ip ospf neighbor [interface"))
        self.assertEqual(first["section_id"], "c9300-179-rtng#show-ip-ospf-neighbor")
        self.assertEqual(self.get("/v2/command", q="sh ip ospf nei", platform="iosxe")["results"], [])

    # --- security and admission -----------------------------------------------------------

    def test_every_endpoint_requires_the_token(self):
        for method, path in (("post", "/v2/search"), ("post", "/v2/search_many"),
                             ("get", "/v2/section"), ("get", "/v2/outline"), ("get", "/v2/documents"),
                             ("post", "/v2/verify_quote"), ("post", "/v2/grep"),
                             ("get", "/v2/rfc/3101"), ("get", "/v2/lookup"), ("get", "/v2/command")):
            r = getattr(self.c, method)(path, headers={"Authorization": "Bearer wrong"})
            self.assertEqual(r.status_code, 401, path)

    def test_search_is_admitted_like_search_v1(self):
        slots = self.c.app.state.search_slots
        held = []
        while slots.acquire(blocking=False):
            held.append(1)
        try:
            r = self.c.post("/v2/search", json={"query": "NSSA"}, headers=AUTH)
            self.assertEqual((r.status_code, r.headers.get("retry-after")), (503, "2"))
        finally:
            for _ in held:
                slots.release()

    def test_every_response_carries_degraded(self):
        answers = {
            "search": self.post("/v2/search", {"query": "NSSA"}),
            "search_many": self.post("/v2/search_many", {"queries": [{"text": "NSSA"}]}),
            "section": self.get("/v2/section", ref="rfc3101#3.2"),
            "outline": self.get("/v2/outline", doc="rfc3101"),
            "documents": self.get("/v2/documents"),
            "verify_quote": self.post("/v2/verify_quote", {"ref": "rfc3101#3.1",
                                                           "quote": "has bit Nt set or"}),
            "grep": self.post("/v2/grep", {"pattern": "Type-7"}),
            "rfc": self.get("/v2/rfc/3101", section="3.2"),
            "lookup": self.get("/v2/lookup", id="RFC 3101"),
            "command": self.get("/v2/command", q="sh ip ospf nei"),
            "status": self.get("/v2/status"),
        }
        for name, body in answers.items():
            self.assertEqual(body["degraded"], ["dense", "reranker"] if name.startswith("search") else [], name)

    def test_v1_search_is_unchanged(self):
        body = self.post("/search", {"query": "NSSA translator"})
        self.assertIn("candidates", body)
        self.assertNotIn("hits", body)


class WordReranker:
    """A reranker that scores a document by the share of the query's words it contains (times a
    per-query factor), and records each call as (query, chunk ids)."""
    candidates = 10

    def __init__(self, boost=None, fail=False):
        self.boost, self.fail = boost or {}, fail
        self.calls = []
        self.texts = {}

    @staticmethod
    def words(text):
        return {w.strip(".,()").lower() for w in text.split() if len(w) > 2}

    def score(self, query, chunk_id):
        wanted = self.words(query)
        have = self.words(self.texts[chunk_id])
        return len(wanted & have) / len(wanted) * self.boost.get(query, 1.0)

    def rerank(self, query, items):
        if self.fail:
            raise TimeoutError("rerank timed out")
        self.calls.append((query, [i["chunk_id"] for i in items]))
        for n, item in enumerate(items, 1):
            self.texts[item["chunk_id"]] = item["text"]
            item["rerank_score"] = self.score(query, item["chunk_id"])
            item["fused_rank"] = n
        return sorted(items, key=lambda i: -i["rerank_score"])


class FailingDense:
    """A dense retriever whose embedding service times out."""
    name = "dense"

    def __init__(self):
        self.calls = 0

    def search(self, variant, *, limit=50, doc_ids=None):
        self.calls += 1
        raise TimeoutError("embedding timed out")


class FlakyDense:
    """A dense retriever whose first call times out; later calls return one chunk."""
    name = "dense"

    def __init__(self, doc_id, chunk):
        self.doc_id, self.chunk, self.calls = doc_id, chunk, 0

    def search(self, variant, *, limit=50, doc_ids=None):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("embedding timed out")
        return [SearchHit(retriever="dense", variant_id=variant.variant_id, purpose=variant.purpose,
                          chunk_id=self.chunk, doc_id=self.doc_id, rank=1, score=0.9, text="")]


class PartialRootTests(unittest.TestCase):
    """Roots missing the KB or the structure index say so in `degraded`."""

    def client(self, **kw):
        root = RootClient(**kw)
        self.addCleanup(root.close)
        return root.client

    def test_without_kb(self):
        c = self.client(kb=False)
        body = c.get("/v2/documents", headers=AUTH).json()
        self.assertEqual(body["degraded"], ["kb"])
        self.assertIsNone(next(d for d in body["documents"] if d["doc_key"] == "rfc1587")["status"])
        section = c.get("/v2/section", params={"ref": "rfc3101#3.2"}, headers=AUTH).json()
        self.assertEqual(section["degraded"], [])
        self.assertEqual(c.get("/v2/lookup", params={"id": "RFC 3101"}, headers=AUTH).status_code, 503)

    def test_without_structure(self):
        c = self.client(structure=False)
        for path, params in (("/v2/lookup", {"id": "RFC 3101"}), ("/v2/command", {"q": "sh ip ospf nei"}),
                             ("/v2/rfc/3101", {})):
            r = c.get(path, params=params, headers=AUTH)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.json()["degraded"], ["structure"], path)

    def test_empty_structure_tables_are_no_index(self):
        # index_structure.py on a root without sources/ creates the tables and indexes nothing;
        # they used to pass for an index: grep found nothing and documents listed none, degraded []
        root = RootClient(structure=False)
        self.addCleanup(root.close)
        self.assertEqual(index_root(root.paths, sources=root.paths.root / "missing")["indexed"], 0)
        c = root.client
        for method, path, kw in (
            ("post", "/v2/grep", {"json": {"pattern": "NSSA"}}),
            ("get", "/v2/documents", {}),
            ("get", "/v2/section", {"params": {"ref": "rfc3101#3.1"}}),
            ("post", "/v2/search", {"json": {"query": "NSSA", "filters": {"doc_type": "rfc"}}}),
        ):
            r = getattr(c, method)(path, headers=AUTH, **kw)
            self.assertEqual(r.status_code, 503, path)
            self.assertIn("scripts/build_root.py", r.json()["detail"])
        search = c.post("/v2/search", json={"query": "NSSA"}, headers=AUTH).json()
        self.assertEqual(search["degraded"], ["dense", "reranker", "structure"])
        status = c.get("/v2/status", headers=AUTH).json()
        self.assertFalse(status["backends"]["structure"]["ok"])
        self.assertIn("structure", status["degraded"])

    def test_generation_comes_from_the_root_overlay(self):
        c = self.client(serving={"generation": "p1-test"})
        search = c.post("/v2/search", json={"query": "NSSA"}, headers=AUTH).json()
        status = c.get("/v2/status", headers=AUTH).json()
        self.assertEqual((search["generation"], status["generation"]), ("p1-test", "p1-test"))


class RefTests(unittest.TestCase):

    def test_the_literal_id_is_tried_before_the_section_prefix_is_dropped(self):
        # a Cisco heading slug may itself start with "section-" (an empty heading's duplicate)
        self.assertEqual(ref_candidates("iosxe17-rtr-sysmgmt#section-2"),
                         ["iosxe17-rtr-sysmgmt#section-2", "iosxe17-rtr-sysmgmt#2"])
        self.assertEqual(ref_candidates("RFC3101#Section-3.2"),
                         ["RFC3101#Section-3.2", "rfc3101#section-3.2", "rfc3101#3.2"])


class RegexSizeTests(unittest.TestCase):
    """reader.regex_size bounds what compiling a grep regex costs; nothing here compiles."""

    def test_repeats_multiply_their_body_and_siblings_add(self):
        self.assertLess(regex_size("a{100}b{100}c{100}"), 1000)
        self.assertGreater(regex_size("(?:a{100}){100}"), 10_000)
        self.assertGreater(regex_size("(?:a{1000,}){2}"), 2000)         # unbounded: the lower count
        self.assertGreater(regex_size("(?:" + "." * 120 + "){100}"), 12_000)   # every node repeats

    def test_measured_bombs_are_over_the_limit_and_cheap_to_measure(self):
        for pattern in (r"(?:(?:a{1000}){1000}){10}", r"(?:[ab]{1000}){1000}", r"(?=a{1000}){1000}",
                        "(?:" + "(x)" * 60 + "){9999}", r"(?:(?:ab){300}){300}", r".{65535}",
                        "(?x)(?:a{1 0 0 0}){1 0 0 0}", "(?x)(?:a{1#}\n000}){1000}"):
            started = time.monotonic()
            self.assertGreater(regex_size(pattern), MAX_REGEX_SIZE, pattern)
            self.assertLess(time.monotonic() - started, 0.1)
        import regex                    # verbose as a flag, before any group (groups reset it)
        self.assertGreater(regex_size("a{1 0 0 0 0}", regex.VERBOSE), MAX_REGEX_SIZE)

    def test_research_patterns_are_under_the_limit(self):
        for pattern in (r"translate\s+type7\s+\[?always", r"\d{1,3}(\.\d{1,3}){3}",
                        r"[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){7}", r".{0,200}OSPF.{0,200}",
                        r"(?:[a-z]{2,40}\s){1,50}", r"(?i)show\s+ip\s+(?:ospf|eigrp|bgp)\s+neighbou?r",
                        r"(?:neighbor information use the show ip){e<=10}", r"\R", r"\p{Lu}{2,8}"):
            self.assertLessEqual(regex_size(pattern), MAX_REGEX_SIZE, pattern)

    def test_bad_patterns_raise(self):
        import regex
        for pattern in ("(", "a{2,1}", "a)"):
            with self.assertRaises(regex.error):
                regex_size(pattern)


class CiteTests(unittest.TestCase):

    def test_a_top_heading_equal_to_the_title_is_not_repeated(self):
        title = "What Does the EIGRP DUAL-3-SIA Error Message Mean? (Cisco TechNote 13676)"
        doc = Doc("d1", "cisco-tn-eigrp-sia", "cisco", title, None, None, None, Path("x.txt"))
        top = Sec("cisco-tn-eigrp-sia#top", doc.doc_key, 0, 1, "top", title, None, 1, 30, 100)
        sub = Sec("cisco-tn-eigrp-sia#causes", doc.doc_key, 1, 2, "causes", "What Causes It?", top, 5, 30, 50)
        self.assertEqual(doc.cite(sub), f"{title} › What Causes It?")
        self.assertEqual(doc.cite(top), title)
        self.assertEqual(sub.path, [title, "What Causes It?"])      # section_path keeps the structure


class ByTitleTests(unittest.TestCase):

    def test_a_title_names_one_heading_its_number_left_out(self):
        doc = Doc("d9", "rfc9", "rfc", None, None, None, None, Path("rfc9.txt"))
        headings = ["1.  Introduction", "2.1 Overview", "3.1 Overview", "4.0 Security Considerations",
                    "A.1 Encapsulation", "Appendix B.  Acknowledgments"]
        doc.sections = [Sec(f"rfc9#{i}", "rfc9", i, 1, str(i), h, None, i, i, 1)
                        for i, h in enumerate(headings)]
        for slug, heading in (("security-considerations", "4.0 Security Considerations"),
                              ("introduction", "1.  Introduction"), ("encapsulation", "A.1 Encapsulation"),
                              ("acknowledgments", "Appendix B.  Acknowledgments")):
            self.assertEqual(doc.by_title(slug).heading, heading)
        self.assertIsNone(doc.by_title("overview"))         # two sections have it
        self.assertIsNone(doc.by_title("4"))


class DegradedTests(unittest.TestCase):

    def test_everything_the_orchestrator_skipped_is_reported(self):
        orch = mock.Mock(degraded={"colbert": "missing index"}, retrievers={"bm25": 1, "dense": 1},
                         reranker=None)
        orch.search.return_value = {
            "candidates": [], "degraded": ["dense"],
            "task_errors": [{"retriever": "reranker"}],
        }
        request = mock.Mock()
        request.app.state.orchestrator = orch
        degraded = set()
        api_v2.retrieve(request, "q", mode="hybrid", rerank="on", pool=24, doc_ids=None,
                        degraded=degraded)
        self.assertEqual(degraded, {"colbert", "dense", "reranker"})
        self.assertEqual(orch.search.call_args.kwargs,
                         {"limit": 24, "retrievers": None, "doc_ids": None, "rerank": True})


class EvidenceWindowTests(unittest.TestCase):

    def test_window_grows_evenly_around_the_centre(self):
        pairs = [(n, f"line {n:02d} " + "x" * 42) for n in range(1, 11)]   # 50 chars each
        text, first, last = window(pairs, 4, 160, [])
        self.assertEqual((first, last), (4, 6))
        self.assertLessEqual(len(text), 160)
        self.assertEqual(best_line(pairs, ["07"]), 6)
        self.assertIsNone(best_line(pairs, ["absent"]))

    def test_an_overlong_line_is_cut_around_the_term(self):
        line = " ".join(f"word{i}" for i in range(300)) + " needle " + "tail " * 50
        text = cut_line(line, ["needle"], 200)
        self.assertLessEqual(len(text), 200)
        self.assertIn("needle", text)
        self.assertTrue(text.startswith("… ") and text.endswith(" …"))

    def test_without_matching_terms_the_window_starts_at_the_chunk(self):
        with tempfile.TemporaryDirectory() as d:
            paths = build_root(Path(d), with_kb_sources=False)
            index_root(paths)
            corp = RootReader(paths)
            doc = corp.docs["rfc3101"]
            with connect(paths.sqlite / "rag.db") as conn:
                row = conn.execute("SELECT line_start, line_end FROM chunk_spans WHERE chunk_id = ?",
                                   (chunk_id("rfc3101", 3),)).fetchone()
            ev = corp.evidence(doc, (row[0], row[1]), [], 4000)
            self.assertTrue(ev["text"].lstrip().startswith("Type-7 LSAs should be examined"))
            # with a match it is centred there and may grow into the rest of the section
            ev = corp.evidence(doc, (row[0], row[1]), ["examined"], 4000)
            self.assertTrue(ev["text"].startswith("3.2 Translating"))


class UnstructuredRootTests(unittest.TestCase):
    """A root indexed before the structure tables existed: search answers from chunk text."""

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(structure=False, kb=False)
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def test_search_degrades_and_reading_endpoints_say_why(self):
        r = self.c.post("/v2/search", json={"query": "NSSA translator"}, headers=AUTH)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["degraded"], ["dense", "kb", "reranker", "structure"])
        hit = body["hits"][0]
        self.assertIsNone(hit["section_id"])
        self.assertIsNone(hit["local"])
        self.assertTrue(hit["text"])
        for path, params in (("/v2/section", {"ref": "rfc3101#1"}), ("/v2/outline", {"doc": "rfc3101"}),
                             ("/v2/lookup", {"id": "RFC 3101"})):
            self.assertEqual(self.c.get(path, params=params, headers=AUTH).status_code, 503, path)
        r = self.c.post("/v2/search", json={"query": "x", "filters": {"doc_type": "rfc"}}, headers=AUTH)
        self.assertEqual(r.status_code, 503)


class RerankByQueryCallTests(unittest.TestCase):
    """api_v2.rerank_by_query runs every per-query call at once and returns on the first failure."""

    @staticmethod
    def inputs(n):
        queries = [api_v2.SubQuery(text=f"q{i}") for i in range(n)]
        head = [{"chunk_id": f"c{i}", "fused_score": 1.0 / (i + 1), "per_query_ranks": {str(i): 1}}
                for i in range(n)]
        rows = {c["chunk_id"]: {"text": f"text of {c['chunk_id']}", "heading_path": None} for c in head}
        return queries, head, rows

    def test_every_query_is_reranked_at_once(self):
        # each call waits until all 8 run: a pool narrower than the queries breaks the barrier
        barrier = threading.Barrier(8, timeout=5)

        class Reranker:
            def rerank(self, query, items):
                barrier.wait()
                return [dict(item, rerank_score=float(query[1:])) for item in items]

        queries, head, rows = self.inputs(8)
        out, scores = api_v2.rerank_by_query(Reranker(), queries, head, rows)
        self.assertEqual(scores, {str(i): {f"c{i}": float(i)} for i in range(8)})
        self.assertEqual([c["chunk_id"] for c in out], [f"c{i}" for i in range(7, -1, -1)])

    def test_best_query_sends_each_candidate_to_the_query_that_ranked_it_highest(self):
        class Reranker:
            def __init__(self):
                self.calls = []

            def rerank(self, query, items):
                self.calls.append((query, [item["chunk_id"] for item in items]))
                return [dict(item, rerank_score=float(len(query))) for item in items]

        queries = [api_v2.SubQuery(text="q0"), api_v2.SubQuery(text="q1 heavy", weight=2.0),
                   api_v2.SubQuery(text="q2")]
        head = [{"chunk_id": "a", "per_query_ranks": {"0": 1, "1": 3}},     # q0 ranked it higher
                {"chunk_id": "b", "per_query_ranks": {"0": 2, "1": 2}},     # a tie: the heavier q1
                {"chunk_id": "c", "per_query_ranks": {"2": 4, "0": 4}},     # equal weights: the earlier q0
                {"chunk_id": "d", "per_query_ranks": {"2": 1}}]
        rows = {c["chunk_id"]: {"text": f"text of {c['chunk_id']}", "heading_path": None} for c in head}
        best = Reranker()
        out, scores = api_v2.rerank_by_query(best, queries, head, rows, "best_query")
        self.assertEqual(sorted(best.calls), [("q0", ["a", "c"]), ("q1 heavy", ["b"]), ("q2", ["d"])])
        self.assertEqual(scores, {"0": {"a": 2.0, "c": 2.0}, "1": {"b": 8.0}, "2": {"d": 2.0}})
        self.assertEqual([(c["chunk_id"], c["rerank_score"]) for c in out], [("b", 8.0), ("a", 2.0), ("c", 2.0),
                                                                              ("d", 2.0)])
        self.assertEqual({c["chunk_id"]: c["per_query_ranks"] for c in out},
                         {c["chunk_id"]: c["per_query_ranks"] for c in head})         # first-stage ranks kept
        # per_query, the default: a candidate goes to every query that retrieved it
        every = Reranker()
        api_v2.rerank_by_query(every, queries, head, rows)
        self.assertEqual(sorted(every.calls), [("q0", ["a", "b", "c"]), ("q1 heavy", ["a", "b"]), ("q2", ["c", "d"])])

    def test_the_first_failure_returns_without_waiting_for_the_other_calls(self):
        release = threading.Event()
        finished = []

        class Reranker:
            def rerank(self, query, items):
                if query == "q1":
                    raise TimeoutError("rerank timed out")
                release.wait(15)                # a hung call, until the test lets it go
                finished.append(query)
                return items

        queries, head, rows = self.inputs(3)
        started = time.monotonic()
        try:
            with self.assertRaises(TimeoutError):
                api_v2.rerank_by_query(Reranker(), queries, head, rows)
            self.assertEqual(finished, [])      # it raised while the calls for q0 and q2 still ran
            self.assertLess(time.monotonic() - started, 5)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
