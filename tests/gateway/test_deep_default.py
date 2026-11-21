"""Round B (edge 2.4.3, 2026-11-02): deep-32 is on by default at the edge.

A search_rag or search_many call that gives neither limit nor deep gets deep's 32 tiered passages; an explicit limit
is honoured exactly as in 2.4.2, so every eval and gate call, each of which gives limit, sends the same
deep32-engine body and gets the same reply; deep=false gives 2.4.2's default (limit 8); deep=true is deep32's. "Gives" is
read from the raw tools/call arguments (EdgeServer.call_tool), not the validated ones, which always hold limit=8.

tests/gateway/edge_242.py is rag_edge.py 2.4.2, verbatim; test_deep.ToolCase builds it beside the edge and feeds both the
same deep32-engine answers.
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import asyncio
import json
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from support import HIT, FakeCore, make_app, rpc, serve
from rag_edge import _CALL_ARGS, TIER_HEADERS, SubQuery, deep_wanted
from test_also_in import answers
from test_deep import QUERIES, ToolCase, deep_answer

LAB = ["17.9", "20.9", "2.3", "3.1"]
SEARCH_DEFAULTS = {"limit": 8, "mode": "hybrid", "rerank": "auto", "max_chars": 2400,
                   "filters": {"include_obsolete": True}}
MANY_DEFAULTS = {"limit": 8, "max_chars": 2400, "rerank": "auto"}

# Calls that give limit: the shapes the eval and gate callers send through the edge (eval_v2 McpBackend.search and
# search_many always send {"query" or "queries", "limit"} plus --mcp-args; ops/n4/run.sh's on30/off30/dense30/bm25-30/
# fused24 add rerank, mode, max_chars and debug), and the edge's other options. Each must be 2.4.2's, byte for byte.
WITH_LIMIT = [
    ("search_rag", {"query": "x", "limit": 8}),                               # eval_v2 --backend mcp (prod-eval, smoke)
    ("search_rag", {"query": "x", "limit": 8, "debug": True}),
    ("search_rag", {"query": "x", "limit": 8, "format": "json"}),
    ("search_rag", {"query": "x", "limit": 30, "rerank": "on", "max_chars": 2400, "debug": True}),     # n4 on30
    ("search_rag", {"query": "x", "limit": 30, "rerank": "off", "max_chars": 2400, "debug": True}),    # n4 off30
    ("search_rag", {"query": "x", "limit": 30, "mode": "dense", "rerank": "off", "max_chars": 200,
                    "debug": True}),                                                                 # n4 dense30
    ("search_rag", {"query": "x", "limit": 24, "rerank": "off", "max_chars": 200, "debug": True}),     # n4 fused24
    ("search_rag", {"query": "x", "limit": 3}),                                 # a small health-check query
    ("search_rag", {"query": "x", "limit": 1}),                                 # warm-up query
    ("search_rag", {"query": "ospf", "limit": 30, "mode": "bm25", "rerank": "off", "max_chars": 4000,
                    "doc_type": "cisco", "release_max": "17.9", "include_obsolete": False}),
    ("search_many", {"queries": [{"text": "x"}], "limit": 8}),                  # eval_v2 --mcp-tool search_many
    ("search_many", {"queries": QUERIES, "limit": 8}),                          # eval_v2 --many-plan (n4 many)
    ("search_many", {"queries": QUERIES, "limit": 5, "max_chars": 600, "format": "json", "docs": ["rfc3101"]}),
    ("search_many", {"queries": QUERIES, "limit": 30, "debug": True, "include_obsolete": False}),
]
# deep=false without limit: 2.4.2's call without limit (its default, 8)
DEEP_FALSE = [
    ("search_rag", {"query": "x", "deep": False}),
    ("search_rag", {"query": "x", "deep": False, "debug": True}),
    ("search_rag", {"query": "x", "deep": False, "format": "json"}),
    ("search_rag", {"query": "x", "deep": False, "max_chars": 1200, "mode": "bm25", "doc_type": "rfc"}),
    ("search_many", {"queries": QUERIES[:1], "deep": False}),
    ("search_many", {"queries": QUERIES, "deep": False, "debug": True}),
]
# neither limit nor deep: deep by default, whatever else the call gives
NO_LIMIT = [
    ("search_rag", {"query": "nssa"}),
    ("search_rag", {"query": "nssa", "debug": True}),
    ("search_rag", {"query": "nssa", "format": "json"}),
    ("search_rag", {"query": "nssa", "max_chars": 1200, "mode": "bm25", "rerank": "on", "doc_type": "cisco",
                    "release_max": "17.9", "include_obsolete": False}),
    ("search_rag", {"query": "nssa", "prefer_release": LAB}),
    ("search_many", {"queries": QUERIES}),
    ("search_many", {"queries": QUERIES[:1], "format": "json", "docs": ["rfc3101"]}),
    ("search_many", {"queries": QUERIES, "max_chars": 600, "rerank": "off", "debug": True}),
]


def old_arguments(arguments):
    """The same call to edge 2.4.2, which has neither deep nor prefer_release."""
    return {k: v for k, v in arguments.items() if k not in ("deep", "prefer_release")}


class ExplicitLimitIs242Test(ToolCase):
    async def test_a_call_with_limit_sends_and_renders_as_2_4_2(self):
        n = 0
        for res in answers(seed=33, n=60):
            if not res["hits"]:
                continue                    # no hits: the opt-in lookup follows (test_tools pins that text)
            routes = {("POST", "/v2/search"): res, ("POST", "/v2/search_many"): res}
            self.serve(routes)
            for name, arguments in WITH_LIMIT:
                with self.subTest(tool=name, arguments=arguments):
                    new = await self.call(self.mcp, name, arguments)
                    old = await self.call(self.old, name, arguments)
                    self.assertEqual(new, old)
                    self.assertEqual(self.fake.requests[-1].content, self.old_fake.requests[-1].content)
                    self.assertNotIn("deep", self.fake.body())
                    n += 1
        self.assertGreater(n, 500)

    async def test_deep_false_is_2_4_2_s_default(self):
        n = 0
        for res in answers(seed=34, n=40):
            if not res["hits"]:
                continue
            self.serve({("POST", "/v2/search"): res, ("POST", "/v2/search_many"): res})
            for name, arguments in DEEP_FALSE:
                with self.subTest(tool=name, arguments=arguments):
                    new = await self.call(self.mcp, name, arguments)
                    old = await self.call(self.old, name, old_arguments(arguments))   # 2.4.2 without limit
                    self.assertEqual(new, old)
                    self.assertEqual(self.fake.requests[-1].content, self.old_fake.requests[-1].content)
                    self.assertEqual(self.fake.body()["limit"], 8)
                    self.assertNotIn("deep", self.fake.body())
                    n += 1
        self.assertGreater(n, 150)

    async def test_a_client_that_fills_in_the_schema_defaults_gets_2_4_2_s_call(self):
        # limit's schema is 2.4.2's (default 8) and deep's default is false: a client that sends every default
        # sends limit, so it gets 2.4.2's answer, not deep's
        plain = {"hits": [HIT], "degraded": []}
        self.serve({("POST", "/v2/search"): plain, ("POST", "/v2/search_many"): plain})
        schemas = {t.name: t.input_schema for t in await self.mcp.list_tools()}
        old_schemas = {t.name: t.input_schema for t in await self.old.list_tools()}
        for name, required in (("search_rag", {"query": "x"}), ("search_many", {"queries": QUERIES})):
            with self.subTest(tool=name):
                props = schemas[name]["properties"]
                self.assertEqual(props["limit"], old_schemas[name]["properties"]["limit"])
                self.assertEqual((props["limit"]["default"], props["deep"]["default"]), (8, False))
                self.assertIn("On by default: a call that gives neither limit nor deep gets these 32",
                              props["deep"]["description"])
                filled = {k: p["default"] for k, p in props.items() if "default" in p} | required
                self.assertIn("limit", filled)
                new = await self.call(self.mcp, name, filled)
                old = await self.call(self.old, name, old_arguments(filled))
                self.assertEqual(new, old)
                self.assertNotIn("deep", self.fake.body())


class NoLimitGetsDeepTest(ToolCase):
    async def test_a_call_without_limit_or_deep_is_a_deep_call(self):
        for name, arguments in NO_LIMIT:
            with self.subTest(tool=name, arguments=arguments):
                self.serve({("POST", "/v2/search"): deep_answer(), ("POST", "/v2/search_many"): deep_answer()})
                by_default = await self.call(self.mcp, name, arguments)
                default_body = self.fake.requests[-1].content
                asked = await self.call(self.mcp, name, {**arguments, "deep": True})
                self.assertEqual(by_default, asked)                     # the reply deep=true gets
                self.assertEqual(default_body, self.fake.requests[-1].content)
                self.assertIs(self.fake.body()["deep"], True)
                self.assertEqual(self.fake.body()["limit"], 8)          # sent as before; deep32-engine ignores it with deep

    async def test_the_default_bodies(self):
        self.serve({("POST", "/v2/search"): deep_answer(), ("POST", "/v2/search_many"): deep_answer()})
        await self.call(self.mcp, "search_rag", {"query": "nssa"})
        self.assertEqual(self.fake.body(), {"query": "nssa", **SEARCH_DEFAULTS, "deep": True})
        await self.call(self.mcp, "search_many", {"queries": QUERIES})
        self.assertEqual(self.fake.body(), {"queries": [SubQuery(**q).model_dump() for q in QUERIES],
                                            **MANY_DEFAULTS, "deep": True})
        await self.call(self.mcp, "search_rag", {"query": "nssa", "prefer_release": LAB})
        self.assertEqual(self.fake.body(), {"query": "nssa", **SEARCH_DEFAULTS, "prefer_release": LAB, "deep": True})

    async def test_text_has_the_tiers(self):
        self.serve({("POST", "/v2/search"): deep_answer(), ("POST", "/v2/search_many"): deep_answer()})
        for name, arguments in (("search_rag", {"query": "nssa"}), ("search_many", {"queries": QUERIES})):
            with self.subTest(tool=name):
                lines = (await self.call(self.mcp, name, arguments)).split("\n")
                self.assertEqual([ln for ln in lines if ln.startswith("tier ")],
                                 [TIER_HEADERS[1].format(first=1, last=8), TIER_HEADERS[2].format(first=9, last=16),
                                  TIER_HEADERS[3].format(first=17, last=32)])
                self.assertEqual(sum(ln.startswith("<hit n=") for ln in lines), 32)

    async def test_deep_true_with_limit_is_deep32_s(self):
        self.serve({("POST", "/v2/search"): deep_answer(), ("POST", "/v2/search_many"): deep_answer()})
        await self.call(self.mcp, "search_rag", {"query": "nssa", "limit": 3, "deep": True})
        self.assertEqual(self.fake.body(), {"query": "nssa", **SEARCH_DEFAULTS, "limit": 3, "deep": True})
        await self.call(self.mcp, "search_many", {"queries": QUERIES, "limit": 12, "deep": True})
        self.assertEqual(self.fake.body()["limit"], 12)
        self.assertIs(self.fake.body()["deep"], True)

    async def test_against_a_rag_core_without_deep_the_reply_is_today_s(self):
        # a deep32-engine without deep32 (7474a71) drops the unknown "deep" key and answers its limit-8 list without tiers:
        # the edge renders it as 2.4.2 renders that answer
        for res in answers(seed=35, n=30):
            if not res["hits"]:
                continue
            self.serve({("POST", "/v2/search"): res, ("POST", "/v2/search_many"): res})
            for name, arguments in (("search_rag", {"query": "x"}), ("search_many", {"queries": QUERIES})):
                with self.subTest(tool=name):
                    self.assertEqual(await self.call(self.mcp, name, arguments),
                                     await self.call(self.old, name, arguments))


class RawArgumentsTest(ToolCase):
    async def test_over_the_mcp_transport(self):
        # the HTTP tools/call path (the SDK's handler -> EdgeServer.call_tool) sees the raw arguments too
        fake = FakeCore({("POST", "/v2/search"): deep_answer(), ("POST", "/v2/search_many"): deep_answer()})
        app = make_app(fake)
        calls = [("search_rag", {"query": "x"}, True), ("search_rag", {"query": "x", "limit": 8}, False),
                 ("search_rag", {"query": "x", "deep": False}, False),
                 ("search_many", {"queries": [{"text": "x"}]}, True),
                 ("search_many", {"queries": [{"text": "x"}], "limit": 8}, False)]
        async with serve(app) as http:
            for name, arguments, deep in calls:
                with self.subTest(tool=name, arguments=arguments):
                    r = await rpc(http, "tools/call", {"name": name, "arguments": arguments})
                    self.assertFalse(r.json()["result"]["isError"])
                    self.assertEqual("deep" in fake.body(), deep)

    async def test_each_call_sees_its_own_arguments(self):
        self.serve({("POST", "/v2/search"): deep_answer(), ("POST", "/v2/search_many"): deep_answer()})
        self.assertIsNone(_CALL_ARGS.get())
        await self.call(self.mcp, "search_rag", {"query": "a"})
        self.assertIsNone(_CALL_ARGS.get())                        # reset after the call
        await self.call(self.mcp, "search_rag", {"query": "b", "limit": 8})
        self.assertNotIn("deep", self.fake.body())
        # concurrent calls in one event loop
        await asyncio.gather(*[self.call(self.mcp, "search_rag", {"query": f"q{i}", **({"limit": 8} if i % 2 else {})})
                               for i in range(10)])
        bodies = {b["query"]: b for b in map(json.loads, (r.content for r in self.fake.requests[-10:]))}
        self.assertEqual({q: "deep" in b for q, b in bodies.items()}, {f"q{i}": i % 2 == 0 for i in range(10)})

    async def test_a_tool_error_resets_the_arguments(self):
        self.serve({("POST", "/v2/search"): deep_answer()})
        with self.assertRaises(ToolError):                         # limit 99: refused by the schema
            await self.mcp.call_tool("search_rag", {"query": "x", "limit": 99})
        self.assertIsNone(_CALL_ARGS.get())
        self.assertEqual(self.fake.requests, [])

    def test_without_raw_arguments_the_argument_is_used_as_given(self):
        self.assertIsNone(_CALL_ARGS.get())
        self.assertIs(deep_wanted(False), False)
        self.assertIs(deep_wanted(True), True)
        for sent, deep, want in ((frozenset({"query"}), False, True), (frozenset({"query", "limit"}), False, False),
                                 (frozenset({"query", "deep"}), False, False), (frozenset({"query", "deep"}), True, True),
                                 (frozenset({"query", "limit", "deep"}), True, True)):
            token = _CALL_ARGS.set(sent)
            try:
                self.assertIs(deep_wanted(deep), want, (sent, deep))
            finally:
                _CALL_ARGS.reset(token)


class PinOnCandidatesTest(ToolCase):
    """The round B merge: with prefer_release, a deep candidate (tier 2 or 3) from another Cisco release says
    pin="other" after its release, as a tier 1 hit does (relpref/edge-label)."""

    async def test_candidates_keep_the_pin_cue(self):
        res = deep_answer()
        for i in (2, 10, 20):
            res["hits"][i] |= {"doc_type": "cisco", "doc_key": "c9300-1718-rtng", "release": "17.18"}
        for i in (3, 11, 21):
            res["hits"][i] |= {"doc_type": "cisco", "doc_key": "c9300-179-rtng", "release": "17.9"}
        self.serve({("POST", "/v2/search"): res})
        text = await self.call(self.mcp, "search_rag", {"query": "nssa", "prefer_release": LAB})
        lines = {int(ln.split()[1][2:]): ln for ln in text.split("\n") if ln.startswith("<hit n=")}
        self.assertEqual(sorted(n for n, ln in lines.items() if ' pin="other"' in ln), [3, 11, 21])
        self.assertIn('release="17.18" pin="other" local=', lines[3])
        self.assertIn('release="17.18" pin="other" reranked="yes"', lines[11])
        self.assertEqual(lines[21], '<hit n=21 doc="c9300-1718-rtng" section="rfc3101#s20" release="17.18" '
                                    'pin="other" reranked="no" q="1:21">')
        plain = await self.call(self.mcp, "search_rag", {"query": "nssa"})
        self.assertNotIn("pin=", plain)
        self.assertEqual(plain, text.replace(' pin="other"', ""))


if __name__ == "__main__":
    unittest.main()
