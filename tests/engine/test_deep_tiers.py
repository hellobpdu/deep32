"""deep=true on /v2/search and /v2/search_many (rag_core.api_v2 DEEP_TIERS): 32 hits in three tiers.

Off (deep absent or false), every response is byte for byte the one rag-stack 6afc29b sent: tests/data/
deep_off_6afc29b.json pins the bodies of tests/fold_root.py's request shapes on its fold fixture, without and with
retrieval.json fold_identical (captured from 6afc29b by fold_root.capture, timing values masked). On, the first 8
hits are the request's hits with limit 8, and the rest are tiered, labelled and narrower (DeepTests, on a fixture
with enough distinct documents for 32 hits).
"""
from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rag_core import api_v2
from rag_core.api_v2 import DEEP_HITS, DeepShare, HitBuilder, SubQuery, deep_share, deep_tier
from rag_core.db import connect
from rag_core.reader import query_terms

import fold_root
from fold_root import AUTH, REQUESTS, WordReranker, alpha2_form, post
from test_api_v2 import RootClient
from v2_root import add_document, chunk_id

PINNED = Path(__file__).resolve().parent / "data" / "deep_off_6afc29b.json"
LABELS = ("tier", "get_section")       # what only a deep hit carries ("reranked" is on every hit)
N_DEEP_DOCS = 40


def deep_text(n: int) -> str:
    """A Cisco-like guide of one section: four lines (two chunks), each about 330 characters, each naming the NSSA
    translator and words of its own, so every document is a passage of its own."""
    own = " ".join(f"w{n}x{j}" for j in range(36))
    lines = [f"Translator note {n}.{k}: the NSSA translator election on router R{n} compares bit Nt, then the "
             f"router ID. {own} k{k}." for k in range(4)]
    return "\n".join([f"# Deep guide {n}", f"## Translator election {n}", *lines, ""])


def deep_prepare(paths, kb_src) -> None:
    for n in range(N_DEEP_DOCS):
        add_document(paths, f"deep-{n:02d}", "cisco", deep_text(n), f"Deep guide {n}")


def without_labels(hits):
    return [{k: v for k, v in h.items() if k not in LABELS} for h in hits]


def capture_with(client, extra) -> dict[str, str]:
    """fold_root.capture with `extra` merged into every request body."""
    out = {}
    for name, (path, body, rerank) in REQUESTS.items():
        r = post(client, path, {**body, **extra}, rerank)
        if r.status_code != 200:
            raise AssertionError(f"{name}: {r.status_code} {r.text}")
        out[name] = alpha2_form(r.content, {**body, **extra})
    return out


class OffFixture:
    retrieval = None
    pinned_key = "bodies"

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=fold_root.prepare, retrieval=cls.retrieval)
        cls.c = cls.root.client
        cls.pinned = json.loads(PINNED.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def test_the_pin_is_6afc29b_s(self):
        self.assertEqual(self.pinned["captured_from"], "6afc29b")
        self.assertEqual(set(self.pinned[self.pinned_key]), set(REQUESTS))

    def test_without_deep_every_body_is_6afc29b_s(self):
        self.assertEqual(capture_with(self.c, {}), self.pinned[self.pinned_key])

    def test_deep_false_is_the_same_body(self):
        self.assertEqual(capture_with(self.c, {"deep": False}), self.pinned[self.pinned_key])

    def test_no_hit_is_labelled(self):
        for name, body in capture_with(self.c, {}).items():
            for hit in json.loads(body)["hits"]:
                self.assertFalse(set(LABELS) & set(hit), name)


class OffTests(OffFixture, unittest.TestCase):
    """retrieval.json without fold_identical (production's)."""


class OffFoldTests(OffFixture, unittest.TestCase):
    """retrieval.json fold_identical true."""
    retrieval = {"fold_identical": True}
    pinned_key = "bodies_fold_on"


SEARCHES = {
    "default": {"query": "NSSA translator election"},
    "bm25_wide": {"query": "NSSA translator election router ID", "mode": "bm25", "rerank": "off",
                  "max_chars": 4000},
    "cisco": {"query": "translator election bit Nt", "filters": {"doc_type": "cisco"}, "max_chars": 2400},
}
MANY = {
    "two": {"queries": [{"text": "NSSA translator election", "mode": "bm25"},
                        {"text": "router ID bit Nt"}]},
    "weighted": {"queries": [{"text": "translator election", "mode": "bm25"},
                             {"text": "Type-7 LSAs", "weight": 0.5},
                             {"text": "show ip ospf neighbor", "weight": 2}], "max_chars": 2400},
}


class DeepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=deep_prepare)
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def call(self, path, body, rerank=False):
        r = post(self.c, path, body, rerank)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def assert_tiered(self, hits, max_chars):
        self.assertEqual(len(hits), DEEP_HITS)
        for n, hit in enumerate(hits):
            tier = deep_tier(n)
            self.assertEqual(hit["tier"], 1 if n < 8 else 2 if n < 16 else 3)
            self.assertEqual(hit["tier"], tier)
            self.assertIsInstance(hit["reranked"], bool)
            cap = {1: max_chars, 2: min(800, max_chars), 3: min(250, max_chars)}[tier]
            self.assertLessEqual(len(hit["text"]), cap, (n, hit["text"]))
            if tier == 1:
                self.assertNotIn("get_section", hit)
            else:
                for key in ("doc_key", "section_id", "release", "cite"):
                    self.assertIn(key, hit)
                self.assertEqual(hit["get_section"], hit["section_id"])
                r = self.c.get("/v2/section", params={"ref": hit["get_section"]}, headers=AUTH)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["doc_key"], hit["doc_key"])

    def test_search_tier_1_is_the_limit_8_answer(self):
        for name, body in SEARCHES.items():
            with self.subTest(name):
                today = self.call("/v2/search", {**body, "limit": 8})
                deep = self.call("/v2/search", {**body, "deep": True})
                self.assertEqual(without_labels(deep["hits"][:8]), today["hits"])
                self.assert_tiered(deep["hits"], body.get("max_chars", 1200))
                self.assertEqual(deep["degraded"], today["degraded"])

    def test_tiers_2_and_3_are_narrower_and_centred(self):
        deep = self.call("/v2/search", {"query": "NSSA translator election", "max_chars": 4000, "deep": True})
        ours = [(n, h) for n, h in enumerate(deep["hits"]) if h["doc_key"].startswith("deep-")]
        self.assertGreaterEqual(len(ours), 28)
        for n, hit in ours:
            width = len(hit["text"])
            if n < 8:
                self.assertGreater(width, 800)               # tier 1: the whole section of the guide
            elif n < 16:
                self.assertTrue(300 < width <= 800, (n, width))
            else:                                            # one line, cut around the matching words
                self.assertLessEqual(width, 250)
                self.assertIn("translator", hit["text"].lower())

    def test_limit_is_not_used(self):
        a = self.call("/v2/search", {"query": "NSSA translator election", "deep": True})
        b = self.call("/v2/search", {"query": "NSSA translator election", "deep": True, "limit": 3})
        self.assertEqual(without_labels(a["hits"]), without_labels(b["hits"]))

    def test_a_small_max_chars_caps_every_tier(self):
        deep = self.call("/v2/search", {"query": "NSSA translator election", "max_chars": 200, "deep": True})
        self.assert_tiered(deep["hits"], 200)

    def test_reranked_is_whether_the_reranker_scored_it(self):
        body = {"query": "NSSA translator election", "rerank": "on"}
        reranker = WordReranker()
        with mock.patch.object(self.c.app.state.orchestrator, "reranker", reranker):
            deep = self.c.post("/v2/search", json={**body, "deep": True}, headers=AUTH).json()
            today = self.c.post("/v2/search", json={**body, "limit": 8}, headers=AUTH).json()
        scored = set(reranker.calls[0][1])
        self.assertEqual(len(scored), WordReranker.candidates)
        self.assertEqual([h["reranked"] for h in deep["hits"]], [h["id"] in scored for h in deep["hits"]])
        self.assertTrue(deep["hits"][0]["reranked"])
        self.assertFalse(any(h["reranked"] for h in deep["hits"][16:]))
        self.assertEqual(without_labels(deep["hits"][:8]), today["hits"])

    def test_search_many_tier_1_is_the_limit_8_answer(self):
        for name, body in MANY.items():
            with self.subTest(name):
                today = self.call("/v2/search_many", {**body, "limit": 8})
                deep = self.call("/v2/search_many", {**body, "deep": True, "limit": 5})
                self.assertEqual(without_labels(deep["hits"][:8]), today["hits"])
                self.assert_tiered(deep["hits"], body.get("max_chars", 1200))
                self.assertEqual(len({h["id"] for h in deep["hits"]}), DEEP_HITS)

    def test_search_many_rerank_on(self):
        body = {**MANY["two"], "rerank": "on"}
        today = self.call("/v2/search_many", {**body, "limit": 8}, rerank=True)
        deep = self.call("/v2/search_many", {**body, "deep": True}, rerank=True)
        self.assertEqual(without_labels(deep["hits"][:8]), today["hits"])
        self.assertTrue(any(h["reranked"] for h in deep["hits"]))
        self.assertFalse(any(h["reranked"] for h in deep["hits"][16:]))

    def test_deep_share_caps(self):
        self.assertEqual([deep_share([None] * n).cap for n in range(1, 9)], [24, 12, 8, 6, 5, 4, 4, 3])

    def test_the_share_holds_a_query_to_its_cap_then_fills(self):
        """Two queries: 8 head candidates, 20 more of query 1's, then 10 of query 2's. Past tier 1 query 1 takes 12
        places (its cap), query 2 its 10, and the 2 places left go to query 1's next candidates, in order."""
        queries = [SubQuery(text="a"), SubQuery(text="b")]
        ids = [chunk_id(f"deep-{n:02d}", 0) for n in range(N_DEEP_DOCS)]
        cands = [{"chunk_id": cid, "fused_score": 1.0 - n / 100, "retrievers": ["bm25"],
                  "per_query_ranks": {"1" if 28 <= n < 38 else "0": n + 1}} for n, cid in enumerate(ids)]
        request = SimpleNamespace(app=self.c.app)
        terms = query_terms("translator")
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            hits = HitBuilder(request, None, 1200, set(), deep=True).build(
                conn, cands, lambda _: terms, DEEP_HITS, share=deep_share(queries))
            plain = HitBuilder(request, None, 1200, set(), deep=True).build(
                conn, cands, lambda _: terms, DEEP_HITS)
        want = ids[:8] + ids[8:20] + ids[28:38] + ids[20:22]
        self.assertEqual([h["id"] for h in hits], want)
        self.assertEqual([h["id"] for h in plain], ids[:32])
        self.assertIsInstance(deep_share(queries), DeepShare)

    def test_search_many_deep_shares_the_deep_places(self):
        """search_many deep with two queries: past tier 1, at most 12 hits count for either query, unless the other
        has no more candidates (then the rest fill in rank order)."""
        body = {"queries": [{"text": "translator election router", "mode": "bm25"},
                            {"text": "ospf neighbor nssa", "mode": "bm25"}], "deep": True}
        deep = self.call("/v2/search_many", body)
        owners = [api_v2.best_query({"per_query_ranks": h["per_query_ranks"]},
                                    [SubQuery(**q) for q in body["queries"]]) for h in deep["hits"][8:]]
        counts = {o: owners.count(o) for o in set(owners)}
        if len(counts) == 2:
            self.assertLessEqual(min(counts.values()), 12)
        self.assertEqual(len(deep["hits"]), DEEP_HITS)


class DeepFoldTests(unittest.TestCase):
    """deep with retrieval.json fold_identical: the fold fixture's copies and the deep guides."""

    def test_tier_1_is_the_limit_8_answer_and_every_hit_has_also_in(self):
        root = RootClient(prepare=lambda p, k: (fold_root.prepare(p, k), deep_prepare(p, k)),
                          retrieval={"fold_identical": True})
        try:
            for body in ({"query": "NSSA ABR elected translator Type-7 LSAs"},
                         {"queries": [{"text": "NSSA translator", "mode": "bm25"}, {"text": "show ip ospf neighbor"}]}):
                path = "/v2/search" if "query" in body else "/v2/search_many"
                today = post(root.client, path, {**body, "limit": 8}).json()
                deep = post(root.client, path, {**body, "deep": True}).json()
                self.assertEqual(without_labels(deep["hits"][:8]), today["hits"])
                self.assertEqual(len(deep["hits"]), DEEP_HITS)
                self.assertTrue(all("also_in" in h and "tier" in h for h in deep["hits"]))
                self.assertTrue(any(h["also_in"] for h in deep["hits"]))
        finally:
            root.close()


COPY_BODY = "\n".join(
    [f"Copy line {k}: the NSSA translator election compares bit Nt, then the router ID; "
     + " ".join(f"c{k}y{j}" for j in range(60)) for k in range(4)])


def copies_prepare(paths, kb_src) -> None:
    """deep_prepare plus two documents whose section (four lines of about 450 characters, longer than tier 2's and
    tier 3's windows) is the same text under another title line."""
    deep_prepare(paths, kb_src)
    for key in ("copy-a", "copy-b"):
        add_document(paths, key, "cisco", f"# Copy guide {key}\n## Translator copy\n{COPY_BODY}\n", f"Copy {key}")


class DeepFoldCopyTests(unittest.TestCase):
    """deep with fold_identical: a copy of a tier 1 hit that ranks after 8 hits folds into it as it does without
    deep (the fold compares max_chars windows whatever the tier), so tier 1 keeps its also_in and the copy is not
    delivered again in tier 2 or 3."""

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=copies_prepare, retrieval={"fold_identical": True})

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def test_a_late_copy_folds_into_its_tier_1_hit(self):
        request = SimpleNamespace(app=self.root.client.app)
        terms = query_terms("NSSA translator election")
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            pairs = [(chunk_id("copy-a", n), chunk_id("copy-b", n)) for n in range(4)]
            rows = api_v2.chunk_rows(conn, [c for p in pairs for c in p])
            a, b = next((x, y) for x, y in pairs if x in rows and y in rows and rows[x]["text"] == rows[y]["text"]
                        and "translator" in rows[x]["text"])
            ids = [a] + [chunk_id(f"deep-{n:02d}", 0) for n in range(N_DEEP_DOCS)]
            ids.insert(10, b)                       # after 8 distinct hits: a tier 2 place
            cands = [{"chunk_id": cid, "fused_score": 1.0 - n / 100, "retrievers": ["bm25"]}
                     for n, cid in enumerate(ids)]
            today = HitBuilder(request, None, 1200, set()).build(conn, cands[:24], lambda _: terms, 8)
            deep = HitBuilder(request, None, 1200, set(), deep=True).build(conn, cands, lambda _: terms, DEEP_HITS)
        self.assertEqual([m["id"] for m in today[0]["also_in"]], [b])
        self.assertEqual(without_labels(deep[:8]), today)
        self.assertNotIn(b, [h["id"] for h in deep])
        self.assertEqual(len(deep), DEEP_HITS)


class DeepShortTests(unittest.TestCase):
    """deep on short candidate lists (filters, few documents): fewer than 32 hits, tiered by rank, no error."""

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=deep_prepare)
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def test_filters_that_leave_few_or_no_documents(self):
        for path, body in (
            ("/v2/search", {"query": "NSSA translator election", "filters": {"docs": ["deep-00", "deep-01"]}}),
            ("/v2/search", {"query": "NSSA translator election", "filters": {"doc_type": "rfc"}}),
            ("/v2/search", {"query": "zzqx nothing matches", "mode": "bm25"}),
            ("/v2/search_many", {"queries": [{"text": "NSSA translator", "mode": "bm25"},
                                             {"text": "router ID", "mode": "bm25"}],
                                 "filters": {"docs": ["deep-03"]}}),
            ("/v2/search_many", {"queries": [{"text": "zzqx", "mode": "bm25"}]}),
        ):
            with self.subTest(path=path, body=body):
                r = post(self.c, path, {**body, "deep": True}, False)
                self.assertEqual(r.status_code, 200, r.text)
                hits = r.json()["hits"]
                self.assertLess(len(hits), DEEP_HITS)
                self.assertEqual([h["tier"] for h in hits], [deep_tier(n) for n in range(len(hits))])
                self.assertTrue(all(("get_section" in h) == (h["tier"] > 1) for h in hits))


if __name__ == "__main__":
    unittest.main()
