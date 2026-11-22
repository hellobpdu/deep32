"""What a tool says when deep32-engine fails (WP-C C4a): the engine's own detail, a retry only for a busy 503 that
says when to come back (Retry-After), and, when the engine itself failed, "check rag_status before going on".
rag_status and /health report the failure without that hint.
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import unittest
from unittest import mock

import httpx
from mcp.server.mcpserver.exceptions import ToolError

import edge_242 as E242
import rag_edge
from support import FakeCore, make_app, reply, serve
from test_also_in import QUERY_SETS, ToolCase, answers
from test_eval_contract import DEGRADED, HITS, QUERIES, parse_search_reply

SKIP = (gateway_target.BUILDER, gateway_target.ALPHA3_ONLY)
STRUCTURE = "structure index missing (serve a root built by scripts/build_root.py)"


def raising(exc_type, message):
    """A responder that fails the way httpx does when deep32-engine does not answer."""
    def responder(request):
        raise exc_type(message, request=request)
    return responder


class FailureCase(ToolCase):
    async def error(self, name, **arguments):
        """The text of the ToolError the tool raises, without mcp's "Error executing tool <name>: " in front."""
        with self.assertRaises(ToolError) as caught:
            await self.mcp.call_tool(name, arguments)
        text, prefix = str(caught.exception), f"Error executing tool {name}: "
        self.assertTrue(text.startswith(prefix), text)
        return text[len(prefix):]


@unittest.skipIf(*SKIP)
class FailureTextTest(FailureCase):
    async def test_a_503_without_retry_after_is_final_and_says_why(self):
        self.serve({("GET", "/v2/rfc/4271"): reply(503, {"detail": "knowledge base not built"}),
                    ("GET", "/v2/section"): reply(503, {"detail": STRUCTURE}),
                    ("POST", "/v2/search"): reply(503)})
        self.assertEqual(await self.error("lookup_rfc", rfc=4271),
                         "deep32-engine cannot answer (HTTP 503): knowledge base not built; "
                         "check rag_status before going on")
        self.assertEqual((len(self.fake.requests), self.fake.sleeps), (1, []))
        self.assertEqual(await self.error("get_section", ref="rfc3101#3.2"),
                         f"deep32-engine cannot answer (HTTP 503): {STRUCTURE}; check rag_status before going on")
        self.assertEqual(await self.error("search_rag", query="x"),
                         "deep32-engine cannot answer (HTTP 503); check rag_status before going on")
        self.assertEqual(self.fake.sleeps, [])

    async def test_a_busy_503_is_retried_once_and_says_when(self):
        self.serve({("POST", "/v2/search"): reply(503, {"detail": "search capacity exhausted, retry shortly"},
                                                  {"Retry-After": "2"})})
        self.assertEqual(await self.error("search_rag", query="x"),
                         "deep32-engine is busy (HTTP 503): search capacity exhausted, retry shortly; retry after 2 s")
        self.assertEqual((len(self.fake.requests), self.fake.sleeps), (2, [2.0]))

    async def test_engine_failures_say_to_check_the_status(self):
        for responder, text in (
                (raising(httpx.ReadTimeout, "timed out"),
                 "deep32-engine did not answer in time; check rag_status before going on"),
                (raising(httpx.ConnectError, "refused"),
                 "deep32-engine is unreachable (ConnectError); check rag_status before going on"),
                (reply(500, {"detail": "boom"}), "deep32-engine failed (HTTP 500); check rag_status before going on"),
                (reply(200, ["not", "an object"]),
                 "deep32-engine returned a malformed response; check rag_status before going on")):
            with self.subTest(text=text):
                self.serve({("POST", "/v2/search"): responder})
                self.assertEqual(await self.error("search_rag", query="x"), text)

    async def test_client_errors_are_unchanged(self):
        self.serve({("GET", "/v2/section"): reply(404, {"detail": "unknown section or chunk: x#1"})})
        self.assertEqual(await self.error("get_section", ref="x#1"), "unknown section or chunk: x#1")

    async def test_rag_status_and_health_report_the_failure_without_the_hint(self):
        timeout = raising(httpx.ReadTimeout, "timed out")
        self.serve({("GET", "/v2/status"): timeout})
        lines = (await self.call("rag_status")).splitlines()
        self.assertEqual(lines[0:2], ["ok: False", "degraded: deep32-engine: deep32-engine did not answer in time"])
        fake = FakeCore({("GET", "/health"): timeout})
        async with serve(make_app(fake)) as http:
            r = await http.get("/health")
        self.assertEqual((r.status_code, r.json()["error"]), (503, "deep32-engine did not answer in time"))

    def test_retry_delay(self):
        self.assertIsNone(rag_edge.retry_delay(httpx.Response(503)))
        self.assertIsNone(rag_edge.retry_delay(httpx.Response(503, headers={"Retry-After": ""})))
        self.assertEqual(rag_edge.retry_delay(httpx.Response(503, headers={"Retry-After": "0.5"})), 0.5)


DENSE_ADVICE = ("degraded: dense is down or off: use keyword queries (mode=bm25), grep_corpus and the lookups "
                "(lookup_id, lookup_rfc, find_command)")
RERANKER_ADVICE = "degraded: reranker is down or off: the order is the fusion's, so read past the third hit"


@unittest.skipIf(*SKIP)
class DegradedAdviceTest(ToolCase):
    """RAG_EDGE_DEGRADED_ADVICE (WP-C C4b): one line of advice after "degraded:" for dense and the reranker."""

    def test_the_switch(self):
        name = "RAG_EDGE_DEGRADED_ADVICE"
        for env in ({}, {name: "off"}, {name: " OFF "}):
            with self.subTest(env=env):
                self.assertIs(rag_edge.degraded_advice(env), False)
        for env in ({name: "on"}, {name: " On "}):
            with self.subTest(env=env):
                self.assertIs(rag_edge.degraded_advice(env), True)
        for value in ("yes", "pins"):
            with self.subTest(value=value), self.assertRaises(RuntimeError) as caught:
                rag_edge.degraded_advice({name: value})
            self.assertIn(name, str(caught.exception))

    async def test_switch_absent_every_reply_is_2_4_2_s(self):
        compared = 0
        with mock.patch.object(rag_edge, "ADVISE_DEGRADED", False):
            for i, res in enumerate(answers(seed=5, n=800)):
                queries = QUERY_SETS[i % len(QUERY_SETS)]
                for fmt, debug in (("text", False), ("text", True), ("json", False)):
                    self.assertEqual(rag_edge.render_hits(json.loads(json.dumps(res)), fmt, debug, queries),
                                     E242.render_hits(json.loads(json.dumps(res)), fmt, debug, queries))
                    compared += 1
            answer = {"hits": HITS, "degraded": DEGRADED}
            self.serve({("POST", "/v2/search"): answer, ("POST", "/v2/search_many"): answer})
            self.assertEqual(await self.call("search_rag", query="x", limit=8),
                             E242.render_hits(answer, "text", False))
            self.assertEqual(await self.call("search_many", queries=QUERIES, limit=8),
                             E242.render_hits(answer, "text", False, [E242.SubQuery(**q) for q in QUERIES]))
        self.assertEqual(compared, 2400)

    async def test_advice_follows_the_degraded_line(self):
        self.assertEqual(rag_edge.DEGRADED_ADVICE, {"dense": DENSE_ADVICE, "reranker": RERANKER_ADVICE})
        with mock.patch.object(rag_edge, "ADVISE_DEGRADED", True):
            self.serve({("POST", "/v2/search"): {"hits": HITS, "degraded": ["dense: breaker open", "reranker",
                                                                             "dense"]}})
            lines = (await self.call("search_rag", query="x", limit=8)).splitlines()
            self.assertEqual(lines[:3], ["degraded: dense: breaker open; reranker; dense", DENSE_ADVICE,
                                         RERANKER_ADVICE])
            self.assertTrue(lines[3].startswith("<hit n=1 "), lines[3])
            self.serve({("POST", "/v2/search"): {"hits": HITS, "degraded": ["kb", "structure"]}})
            lines = (await self.call("search_rag", query="x", limit=8)).splitlines()
            self.assertEqual(lines[0], "degraded: kb; structure")
            self.assertTrue(lines[1].startswith("<hit n=1 "), lines[1])

    async def test_eval_v2_reads_the_same_hits_and_note(self):
        for answer in ({"hits": HITS, "degraded": DEGRADED}, {"hits": [], "degraded": DEGRADED}):
            for name, arguments in (("search_rag", {"query": "x", "limit": 8}),
                                    ("search_many", {"queries": QUERIES, "limit": 8})):
                with self.subTest(hits=len(answer["hits"]), tool=name):
                    replies = []
                    for advise in (True, False):
                        self.serve({("POST", "/v2/search"): answer, ("POST", "/v2/search_many"): answer,
                                    ("GET", "/v2/status"): {"ok": True, "opt_in_doc_types": []}})
                        with mock.patch.object(rag_edge, "ADVISE_DEGRADED", advise):
                            replies.append(parse_search_reply(await self.call(name, **arguments)))
                    self.assertEqual(replies[0], replies[1])
                    self.assertEqual(replies[0][1], ["dense: breaker open; reranker"])


if __name__ == "__main__":
    unittest.main()
