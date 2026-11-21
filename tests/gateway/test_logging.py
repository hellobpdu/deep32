"""Logging: plain one-line records (not the MCP SDK's rich handler), and one line per request that reached deep32-engine,
with the edge's time and each core call in it."""

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import logging
import re
import unittest

import httpx

from support import HEALTH, SEARCH, TOKEN, FakeCore, make_app, rpc, serve
from rag_edge import LOG_FORMAT, Core, Guard, build_server, configure_logging, note_core_call

LINE = re.compile(r"^POST /mcp -> 200 in \d+ ms \(core POST /v2/search -> 200 in \d+ ms\)$")
# the regex the latency profile pairs journal lines with (peak/latency-profile/tools/pair_edge_log.js)
PAIRING = re.compile(r"core POST /v2/search -> (\d+) in (\d+)")


class RootLoggerCase(unittest.TestCase):
    def setUp(self):
        root = logging.getLogger()
        self.saved = (root.handlers[:], root.level)
        self.addCleanup(self.restore)

    def restore(self):
        root = logging.getLogger()
        root.handlers[:], level = self.saved
        root.setLevel(level)


class ConfigureLoggingTest(RootLoggerCase):
    def test_a_plain_handler_when_the_root_logger_has_none(self):
        root = logging.getLogger()
        root.handlers[:] = []
        configure_logging()
        self.assertEqual(len(root.handlers), 1)
        handler = root.handlers[0]
        self.assertIs(type(handler), logging.StreamHandler)
        self.assertEqual(handler.formatter._fmt, LOG_FORMAT)
        self.assertEqual(root.level, logging.INFO)
        record = logging.LogRecord("rag_edge", logging.INFO, __file__, 1, "core %s -> %d in %s ms",
                                   ("POST /v2/search", 200, "161"), None)
        self.assertEqual(handler.format(record), "INFO rag_edge: core POST /v2/search -> 200 in 161 ms")

    def test_the_mcp_sdk_then_adds_no_rich_handler(self):
        root = logging.getLogger()
        root.handlers[:] = []
        configure_logging()
        build_server(FakeCore().core())  # MCPServer runs the SDK's logging.basicConfig with a RichHandler
        self.assertEqual([type(h).__name__ for h in root.handlers], ["StreamHandler"])

    def test_a_configured_root_logger_is_left_alone(self):
        root = logging.getLogger()
        mine = logging.NullHandler()
        root.handlers[:] = [mine]
        root.setLevel(logging.WARNING)
        configure_logging()
        self.assertEqual(root.handlers, [mine])
        self.assertEqual(root.level, logging.WARNING)


class RequestLineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake = FakeCore({("POST", "/v2/search"): SEARCH, ("GET", "/health"): HEALTH})

    async def test_one_line_per_tool_call_with_the_core_call_in_it(self):
        with self.assertLogs("rag_edge", "INFO") as logs:
            async with serve(make_app(self.fake)) as http:
                r = await rpc(http, "tools/call", {"name": "search_rag", "arguments": {"query": "NSSA"}})
        self.assertFalse(r.json()["result"]["isError"])
        lines = [rec.getMessage() for rec in logs.records]
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], LINE)
        self.assertTrue(PAIRING.search(lines[0]))

    async def test_health_gets_its_line(self):
        with self.assertLogs("rag_edge", "INFO") as logs:
            async with serve(make_app(self.fake)) as http:
                self.assertEqual((await http.get("/health")).status_code, 200)
        self.assertEqual(len(logs.records), 1)
        self.assertRegex(logs.records[0].getMessage(), r"^GET /health -> 200 in \d+ ms \(core GET /health -> 200 "
                                                       r"in \d+ ms\)$")

    async def test_requests_without_a_core_call_log_nothing(self):
        async with serve(make_app(self.fake)) as http:
            with self.assertNoLogs("rag_edge", "INFO"):
                self.assertEqual((await rpc(http, "tools/list")).status_code, 200)

    async def test_every_core_call_of_a_request_is_in_its_line(self):
        # get_outline under= against a deep32-engine that ignores `under` asks twice
        outline = {"doc_key": "d", "title": "D", "sections": [{"section_id": "d#a", "level": 1, "heading": "A"}]}
        self.fake.routes[("GET", "/v2/outline")] = outline
        with self.assertLogs("rag_edge", "INFO") as logs:
            async with serve(make_app(self.fake)) as http:
                await rpc(http, "tools/call", {"name": "get_outline", "arguments": {"doc": "d", "under": "a"}})
        self.assertEqual(len(logs.records), 1)
        self.assertRegex(logs.records[0].getMessage(), r"^POST /mcp -> 200 in \d+ ms \(core GET /v2/outline -> 200 "
                                                       r"in \d+ ms, core GET /v2/outline -> 200 in \d+ ms\)$")

    async def test_a_failed_core_call_is_in_the_line(self):
        def refuse(request):
            raise httpx.ConnectError("refused", request=request)
        core = Core("http://core.internal.test:8765", "t" * 40, transport=httpx.MockTransport(refuse))
        with self.assertLogs("rag_edge", "INFO") as logs:
            async with serve(make_app(self.fake, core=core)) as http:
                r = await rpc(http, "tools/call", {"name": "search_rag", "arguments": {"query": "NSSA"}})
        self.assertTrue(r.json()["result"]["isError"])
        self.assertRegex(logs.records[0].getMessage(), r"^POST /mcp -> 200 in \d+ ms \(core POST /v2/search -> "
                                                       r"ConnectError after \d+ ms\)$")

    async def test_a_core_used_outside_a_request_logs_each_call(self):
        core = self.fake.core()
        with self.assertLogs("rag_edge", "INFO") as logs:
            await core.request("GET", "/health")
        await core.aclose()
        self.assertRegex(logs.records[0].getMessage(), r"^core GET /health -> 200 in \d+ ms$")


class DegradedNoteTest(unittest.IsolatedAsyncioTestCase):
    """SRV-01 (audit 2026-11-05): a deep32-engine answer that says it is degraded (BM25 only while the GPU host is down
    or in gaming mode) says so on the edge's request line, after the core call's time."""

    async def test_a_degraded_search_says_so_on_its_line(self):
        fake = FakeCore({("POST", "/v2/search"): {**SEARCH, "degraded": ["dense", "reranker"]}})
        with self.assertLogs("rag_edge", "INFO") as logs:
            async with serve(make_app(fake)) as http:
                r = await rpc(http, "tools/call", {"name": "search_rag", "arguments": {"query": "NSSA"}})
        self.assertFalse(r.json()["result"]["isError"])
        self.assertEqual(len(logs.records), 1)
        line = logs.records[0].getMessage()
        self.assertRegex(line, r"^POST /mcp -> 200 in \d+ ms \(core POST /v2/search -> 200 in \d+ ms "
                               r"degraded=dense,reranker\)$")
        self.assertTrue(PAIRING.search(line))  # the latency profile still pairs it

    async def test_an_answer_with_nothing_degraded_keeps_the_line_as_before(self):
        fake = FakeCore({("POST", "/v2/search"): SEARCH})  # "degraded": []
        with self.assertLogs("rag_edge", "INFO") as logs:
            async with serve(make_app(fake)) as http:
                await rpc(http, "tools/call", {"name": "search_rag", "arguments": {"query": "NSSA"}})
        self.assertRegex(logs.records[0].getMessage(), LINE)
        self.assertNotIn("degraded", logs.records[0].getMessage())

    async def test_status_outside_a_request_logs_its_degraded(self):
        fake = FakeCore({("GET", "/v2/status"): {"ok": True, "degraded": ["dense", "reranker"]}})
        core = fake.core()
        with self.assertLogs("rag_edge", "INFO") as logs:
            await core.request("GET", "/v2/status")
        await core.aclose()
        self.assertRegex(logs.records[0].getMessage(),
                         r"^core GET /v2/status -> 200 in \d+ ms degraded=dense,reranker$")

    async def test_a_malformed_answer_is_noted_without_a_suffix_and_still_fails(self):
        from rag_edge import CoreError
        fake = FakeCore({("POST", "/v2/search"): lambda request: httpx.Response(200, json=["not", "an", "object"])})
        core = fake.core()
        with self.assertLogs("rag_edge", "INFO") as logs, self.assertRaisesRegex(CoreError, "malformed"):
            await core.request("POST", "/v2/search", json={"query": "x"})
        await core.aclose()
        self.assertRegex(logs.records[0].getMessage(), r"^core POST /v2/search -> 200 in \d+ ms$")

    def test_names_are_short_tokens_that_cannot_forge_a_line(self):
        from rag_edge import degraded_note
        self.assertEqual(degraded_note({"degraded": []}), "")
        self.assertEqual(degraded_note({}), "")
        self.assertEqual(degraded_note({"degraded": None}), "")
        self.assertEqual(degraded_note({"degraded": ["dense"]}), " degraded=dense")
        self.assertEqual(degraded_note({"degraded": {"dense": "circuit breaker open after 3 failures"}}),
                         " degraded=dense")
        forged = degraded_note({"degraded": ["dense\nINFO rag_edge: forged ✓", "x" * 100, ""]})
        self.assertNotIn("\n", forged)
        self.assertTrue(forged.isascii())
        self.assertEqual(forged, " degraded=dense_INFO_rag_edge:_forged_," + "x" * 40 + ",_")
        many = degraded_note({"degraded": [f"leg{i}" for i in range(11)]})
        self.assertEqual(many, " degraded=" + ",".join(f"leg{i}" for i in range(8)) + ",+3")


class GuardLineTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_path_cannot_forge_a_second_line(self):
        async def app(scope, receive, send):  # stands in for a route that calls deep32-engine
            note_core_call("core GET /health -> 200 in 1 ms")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def send(message):
            pass

        guard = Guard(app, tokens=(TOKEN,), networks=())
        scope = {"type": "http", "method": "GET", "path": "/mcp\nINFO rag_edge: forged ✓",
                 "headers": [(b"authorization", f"Bearer {TOKEN}".encode())]}
        with self.assertLogs("rag_edge", "INFO") as logs:
            await guard(scope, None, send)
        message = logs.records[0].getMessage()
        self.assertNotIn("\n", message)
        self.assertTrue(message.startswith(r"GET /mcp\nINFO rag_edge: forged \u2713 -> 200 in "), message)


if __name__ == "__main__":
    unittest.main()
