"""deep=true on search_rag and search_many (deep32-engine deep32: 32 hits in three tiers).

Without deep, every request the edge sends and every reply it renders is edge 2.4.2's: tests/gateway/edge_242.py is
a verbatim copy of rag_edge.py 2.4.2, built into a server
beside the edge and fed the same deep32-engine answers. With deep, the edge sends "deep": true and renders a header line
before each tier, tier 2 hits compactly (cite, id, doc, section to pass to get_section, release, reranked), tier 3
hits more so (doc, section, release, reranked: cited only from their section).
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import unittest

import edge_242 as E242
from support import HIT, FakeCore
from rag_edge import SubQuery, TIER_HEADERS, build_server, render_hits
from test_also_in import QUERY_SETS, answers
from test_eval_contract import parse_search_reply

QUERIES = [{"text": "Nt bit translator", "mode": "bm25"}, {"text": "which ABR", "mode": "dense"},
           {"text": "nssa lsa", "weight": 0.5}]
# tool arguments without deep (or deep=false), each rendered by the edge and by edge 2.4.2. Round B: a call that
# gives neither limit nor deep is deep by default (test_deep_default), so each of these gives limit or deep=false.
CALLS = [
    ("search_rag", {"query": "x", "limit": 8}),
    ("search_rag", {"query": "x", "debug": True, "deep": False}),
    ("search_rag", {"query": "x", "format": "json", "limit": 8}),
    ("search_rag", {"query": "ospf", "limit": 30, "mode": "bm25", "rerank": "off", "max_chars": 4000,
                    "doc_type": "cisco", "release_max": "17.9", "include_obsolete": False}),
    ("search_rag", {"query": "x", "deep": False}),
    ("search_many", {"queries": QUERIES[:1], "limit": 8}),
    ("search_many", {"queries": QUERIES, "debug": True, "deep": False}),
    ("search_many", {"queries": QUERIES, "format": "json", "limit": 5, "max_chars": 600, "docs": ["rfc3101"]}),
    ("search_many", {"queries": QUERIES, "deep": False}),
]


def deep_answer(n_hits=32, reranked=15):
    """A deep32-engine deep answer: hits 1-8 tier 1, 9-16 tier 2, 17-32 tier 3; the first `reranked` reranked."""
    hits = []
    for i in range(n_hits):
        tier = 1 if i < 8 else 2 if i < 16 else 3
        h = {**HIT, "id": f"chk_{i}", "section_id": f"rfc3101#s{i}", "local": f"rfc3101.txt:{10 * i}-{10 * i + 5}",
             "text": f"passage {i} " + "x" * (2000 if tier == 1 else 790 if tier == 2 else 240),
             "per_query_ranks": {"0": i + 1}, "tier": tier, "reranked": i < reranked}
        if tier > 1:
            h["get_section"] = h["section_id"]
        hits.append(h)
    return {"hits": hits, "degraded": [], "generation": "g", "timings_ms": {"total": 9}}


class ToolCase(unittest.IsolatedAsyncioTestCase):
    def serve(self, routes):
        self.fake = FakeCore(routes)
        self.mcp = build_server(self.fake.core())
        self.old_fake = FakeCore(routes)
        self.old = E242.build_server(self.old_fake.core())

    async def call(self, mcp, name, arguments):
        result = await mcp.call_tool(name, arguments)
        self.assertFalse(result.is_error, result.content)
        return result.content[0].text


class Unchanged242Test(ToolCase):
    async def test_every_call_without_deep_sends_and_renders_as_2_4_2(self):
        n = 0
        for res in answers(seed=32, n=60):
            if not res["hits"]:
                continue                    # no hits: the opt-in lookup follows (test_tools pins that text)
            routes = {("POST", "/v2/search"): res, ("POST", "/v2/search_many"): res}
            self.serve(routes)
            for name, arguments in CALLS:
                with self.subTest(tool=name, arguments=arguments):
                    new = await self.call(self.mcp, name, arguments)
                    old = await self.call(self.old, name, {k: v for k, v in arguments.items() if k != "deep"})
                    self.assertEqual(new, old)
                    self.assertEqual(self.fake.body(), self.old_fake.body())
                    self.assertNotIn("deep", self.fake.body())
                    n += 1
        self.assertGreater(n, 300)

    def test_answers_without_tiers_render_as_2_4_2(self):
        compared = 0
        for i, res in enumerate(answers(seed=5, n=800)):
            queries = QUERY_SETS[i % len(QUERY_SETS)]
            for fmt, debug in (("text", False), ("text", True), ("json", False), ("json", True)):
                self.assertEqual(render_hits(json.loads(json.dumps(res)), fmt, debug, queries),
                                 E242.render_hits(json.loads(json.dumps(res)), fmt, debug, queries))
                compared += 1
        self.assertEqual(compared, 3200)

    async def test_the_schema_adds_only_deep(self):
        self.serve({})
        new = {t.name: t.input_schema for t in await self.mcp.list_tools()}
        old = {t.name: t.input_schema for t in await self.old.list_tools()}
        self.assertEqual(set(new), set(old))
        for name in new:
            props = dict(new[name]["properties"])
            if name in ("search_rag", "search_many"):
                deep = props.pop("deep")
                self.assertEqual((deep["type"], deep["default"]), ("boolean", False))
                props.pop("prefer_release")   # round B merge: relpref's property, pinned by test_prefer_release
            self.assertEqual({**new[name], "properties": props}, old[name], name)


class DeepTest(ToolCase):
    async def test_deep_is_sent_to_rag_core(self):
        self.serve({("POST", "/v2/search"): deep_answer(), ("POST", "/v2/search_many"): deep_answer()})
        await self.call(self.mcp, "search_rag", {"query": "nssa", "deep": True})
        self.assertEqual(self.fake.body(), {"query": "nssa", "limit": 8, "mode": "hybrid", "rerank": "auto",
                                            "max_chars": 2400, "filters": {"include_obsolete": True}, "deep": True})
        await self.call(self.mcp, "search_many", {"queries": QUERIES, "deep": True})
        self.assertEqual(self.fake.body(), {"queries": [SubQuery(**q).model_dump() for q in QUERIES], "limit": 8,
                                            "max_chars": 2400, "rerank": "auto", "deep": True})

    async def test_text_has_a_header_per_tier_and_compact_candidates(self):
        self.serve({("POST", "/v2/search"): deep_answer()})
        text = await self.call(self.mcp, "search_rag", {"query": "nssa", "deep": True})
        lines = text.split("\n")
        headers = [ln for ln in lines if ln.startswith("tier ")]
        self.assertEqual(headers, [TIER_HEADERS[1].format(first=1, last=8), TIER_HEADERS[2].format(first=9, last=16),
                                   TIER_HEADERS[3].format(first=17, last=32)])
        self.assertEqual(lines[0], headers[0])
        self.assertLess(lines.index(headers[1]), lines.index(next(ln for ln in lines if ln.startswith("<hit n=9 "))))
        hit1 = next(ln for ln in lines if ln.startswith("<hit n=1 "))
        self.assertIn('id="chk_0"', hit1)
        self.assertIn('local="rfc3101.txt:0-5"', hit1)
        self.assertIn('reranked="yes"', hit1)
        hit16 = next(ln for ln in lines if ln.startswith("<hit n=16 "))
        self.assertEqual(hit16, '<hit n=16 cite="RFC 3101 §3.2" id="chk_15" doc="rfc3101" section="rfc3101#s15" '
                                'reranked="no" q="1:16">')
        hit9 = next(ln for ln in lines if ln.startswith("<hit n=9 "))
        self.assertIn('reranked="yes"', hit9)
        self.assertNotIn("local=", hit9)
        self.assertNotIn("status=", hit9)
        hit17 = next(ln for ln in lines if ln.startswith("<hit n=17 "))
        self.assertEqual(hit17, '<hit n=17 doc="rfc3101" section="rfc3101#s16" reranked="no" q="1:17">')

    async def test_eval_v2_reads_32_hits_and_their_sections(self):
        self.serve({("POST", "/v2/search"): deep_answer()})
        hits, degraded = parse_search_reply(await self.call(self.mcp, "search_rag", {"query": "nssa", "deep": True}))
        self.assertEqual([h.id for h in hits], [f"chk_{i}" for i in range(16)] + [""] * 16)   # tier 3: no id
        self.assertEqual({h.doc_key for h in hits}, {"rfc3101"})
        self.assertEqual([h.extra["section"] for h in hits], [f"rfc3101#s{i}" for i in range(32)])
        self.assertEqual(degraded, [])

    async def test_json_carries_the_tier_fields(self):
        self.serve({("POST", "/v2/search_many"): deep_answer()})
        res = json.loads(await self.call(self.mcp, "search_many", {"queries": QUERIES, "deep": True,
                                                                   "format": "json"}))
        self.assertEqual([h["tier"] for h in res["hits"]], [1] * 8 + [2] * 8 + [3] * 16)
        self.assertEqual([h["reranked"] for h in res["hits"]], [True] * 15 + [False] * 17)
        self.assertTrue(all("get_section" in h for h in res["hits"][8:]))
        self.assertNotIn("score", res["hits"][0])

    async def test_a_short_answer_names_the_hits_it_has(self):
        self.serve({("POST", "/v2/search_many"): deep_answer(n_hits=11)})
        text = await self.call(self.mcp, "search_many", {"queries": QUERIES[:2], "deep": True})
        headers = [ln for ln in text.split("\n") if ln.startswith("tier ")]
        self.assertEqual(headers, [TIER_HEADERS[1].format(first=1, last=8), TIER_HEADERS[2].format(first=9, last=11)])
        self.assertTrue(text.rstrip().split("\n")[-1].startswith("per query: "))

    def test_a_forged_tier_is_ignored(self):
        for tier in (True, 0, 4, "2", None):
            res = {"hits": [{**HIT, "tier": tier, "reranked": True}], "degraded": []}
            old = E242.render_hits({"hits": [HIT], "degraded": []}, "text", False)
            self.assertEqual(render_hits(res, "text", False), old, tier)


if __name__ == "__main__":
    unittest.main()
