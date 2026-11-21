"""MCP protocol over the real ASGI app: initialize, tools/list and tools/call, with and without a bearer."""

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import hmac
import ipaddress
import unittest
from unittest import mock

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from support import (BASE_URL, CORE_TOKEN, HEALTH, INIT_PARAMS, MCP_HEADERS, OLD_TOKEN, SEARCH, TOKEN, FakeCore,
                     make_app, reply, rpc, serve)
from rag_edge import DOC_TYPES, INSTRUCTIONS, RESULT_CHARS, SEARCH_HINTS, VERSION, Guard

TOOLS = {"search_rag", "search_many", "get_section", "get_outline", "list_documents", "verify_quote",
         "grep_corpus", "lookup_rfc", "lookup_id", "find_command", "rag_status"}
LOOKUP = {"kind": "iana", "results": [{"registry": "OSPFv2 LS types", "code": "7"}]}


def routes():
    return {("GET", "/health"): HEALTH, ("POST", "/v2/search"): SEARCH, ("GET", "/v2/lookup"): LOOKUP}


class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake = FakeCore(routes())
        self.app = make_app(self.fake, tokens=(TOKEN, OLD_TOKEN))

    async def test_initialize(self):
        async with serve(self.app) as http:
            r = await rpc(http, "initialize", INIT_PARAMS)
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertEqual(result["serverInfo"]["name"], "deep32-gateway")
        self.assertEqual(result["serverInfo"]["version"], VERSION)
        self.assertEqual(result["serverInfo"]["title"],
                         "Network research corpus (RFC, Cisco)")
        self.assertIn("untrusted data", result["instructions"])
        self.assertIn("never instructions", result["instructions"])
        self.assertEqual(result["instructions"], INSTRUCTIONS)

    async def test_tools_list_titles_and_read_only_annotations(self):
        async with serve(self.app) as http:
            r = await rpc(http, "tools/list")
        self.assertEqual(r.status_code, 200)
        tools = {t["name"]: t for t in r.json()["result"]["tools"]}
        self.assertEqual(set(tools), TOOLS)
        for name, tool in tools.items():
            with self.subTest(tool=name):
                self.assertTrue(tool.get("title"))
                self.assertEqual(tool["annotations"]["readOnlyHint"], True)
                self.assertEqual(tool["annotations"]["idempotentHint"], True)
                self.assertEqual(tool["annotations"]["openWorldHint"], False)
                self.assertNotIn("outputSchema", tool)  # text only: no duplicated structuredContent
        limit = tools["search_rag"]["inputSchema"]["properties"]["limit"]
        self.assertEqual((limit["minimum"], limit["maximum"], limit["default"]), (1, 30, 8))

    async def test_every_parameter_is_described(self):
        # Optional parameters too: a description inside anyOf is one clients do not show.
        async with serve(self.app) as http:
            tools = (await rpc(http, "tools/list")).json()["result"]["tools"]
        for tool in tools:
            for name, schema in tool["inputSchema"]["properties"].items():
                with self.subTest(tool=tool["name"], parameter=name):
                    self.assertTrue(schema.get("description"))
        docs = next(t for t in tools if t["name"] == "search_rag")["inputSchema"]["properties"]["docs"]
        self.assertEqual(docs["anyOf"][0]["maxItems"], 20)  # the constraints survive the outer description

    async def test_large_results_raise_claude_codes_inline_limit(self):
        async with serve(self.app) as http:
            tools = {t["name"]: t for t in (await rpc(http, "tools/list")).json()["result"]["tools"]}
        caps = {name: t.get("_meta", {}).get("anthropic/maxResultSizeChars") for name, t in tools.items()}
        self.assertEqual({name: cap for name, cap in caps.items() if cap}, RESULT_CHARS)
        self.assertTrue(all(cap <= 500_000 for cap in RESULT_CHARS.values()))  # Claude Code's hard cap

    async def test_search_hints_name_the_collections(self):
        # Claude Code's ToolSearch matches a deferred tool on _meta["anthropic/searchHint"] too (a string)
        async with serve(self.app) as http:
            tools = {t["name"]: t for t in (await rpc(http, "tools/list")).json()["result"]["tools"]}
        hints = {name: t.get("_meta", {}).get("anthropic/searchHint") for name, t in tools.items()}
        self.assertEqual({name: hint for name, hint in hints.items() if hint}, SEARCH_HINTS)
        for name in ("search_rag", "search_many"):
            with self.subTest(tool=name):
                for term in ("RFC", "Cisco", "SD-WAN", "ISE", "PowerShell"):
                    self.assertIn(term, hints[name])
        self.assertTrue(hints["list_documents"].endswith(": " + ", ".join(DOC_TYPES)))
        self.assertTrue(all(isinstance(h, str) and "\n" not in h for h in SEARCH_HINTS.values()))
        self.assertEqual(tools["search_rag"]["_meta"]["anthropic/maxResultSizeChars"], RESULT_CHARS["search_rag"])

    async def test_tools_call(self):
        async with serve(self.app) as http:
            r = await rpc(http, "tools/call", {"name": "search_rag", "arguments": {"query": "NSSA", "limit": 3}})
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertFalse(result["isError"])
        self.assertNotIn("structuredContent", result)
        self.assertTrue(result["content"][0]["text"].startswith('<hit n=1 cite="RFC 3101 §3.2"'))
        self.assertEqual(self.fake.requests[-1].headers["Authorization"], f"Bearer {CORE_TOKEN}")

    async def test_tool_error_is_a_result_without_internal_details(self):
        self.fake.routes[("POST", "/v2/search")] = reply(500, {"detail": "boom at http://10.9.9.9"})
        async with serve(self.app) as http:
            r = await rpc(http, "tools/call", {"name": "search_rag", "arguments": {"query": "x"}})
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn("deep32-engine failed (HTTP 500)", result["content"][0]["text"])
        self.assertNotIn("10.9.9.9", result["content"][0]["text"])

    async def test_every_mcp_method_needs_the_bearer(self):
        calls = (("initialize", INIT_PARAMS), ("tools/list", None),
                 ("tools/call", {"name": "search_rag", "arguments": {"query": "x"}}))
        async with serve(self.app) as http:
            for method, params in calls:
                with self.subTest(method=method):
                    r = await rpc(http, method, params, token=None)
                    self.assertEqual(r.status_code, 401)
                    self.assertEqual(r.headers["WWW-Authenticate"], 'Bearer realm="deep32-gateway"')
                    self.assertEqual(r.json(), {"error": "unauthorized"})
        self.assertEqual(self.fake.requests, [])  # nothing reached deep32-engine

    async def test_bad_credentials_are_refused(self):
        cases = {
            "wrong token": "Bearer " + "x" * 51,
            "token prefix": "Bearer " + TOKEN[:-1],
            "token plus suffix": "Bearer " + TOKEN + "x",
            "basic scheme": "Basic " + TOKEN,
            "empty bearer": "Bearer ",
            "bare token": TOKEN,
        }
        async with serve(self.app) as http:
            for name, value in cases.items():
                with self.subTest(name):
                    r = await rpc(http, "tools/list", token=None, headers={"Authorization": value})
                    self.assertEqual(r.status_code, 401)

    async def test_non_ascii_authorization_is_401_not_500(self):
        async with serve(self.app) as http:
            r = await http.post("/mcp", content=b"{}", headers=[
                (b"authorization", "Bearer tök€n".encode()), (b"content-type", b"application/json")])
        self.assertEqual(r.status_code, 401)

    async def test_previous_token_and_lowercase_scheme_accepted(self):
        async with serve(self.app) as http:
            self.assertEqual((await rpc(http, "tools/list", token=OLD_TOKEN)).status_code, 200)
            r = await rpc(http, "tools/list", token=None, headers={"Authorization": f"bearer {TOKEN}"})
            self.assertEqual(r.status_code, 200)

    async def test_refusals_are_logged_without_the_token(self):
        with self.assertLogs("rag_edge", "WARNING") as logs:
            async with serve(self.app) as http:
                await rpc(http, "tools/list", token="leaked-" + "z" * 40)
                await http.get("/x%0Afake log line")
        self.assertIn("refused POST '/mcp' from 127.0.0.1 (HTTP 401)", logs.output[0])
        self.assertIn(r"refused GET '/x\nfake log line'", logs.output[1])  # one line, not a forged entry
        self.assertNotIn("leaked", "".join(logs.output))

    async def test_request_body_is_capped_at_256_kib(self):
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"pad":"' + b"x" * (256 * 1024) + b'"}}'
        async with serve(self.app) as http:
            r = await http.post("/mcp", content=body, headers={**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(r.status_code, 413)

    async def test_lifespan_closes_the_core_client(self):
        core = self.fake.core()
        async with serve(make_app(self.fake, core=core)) as http:
            self.assertEqual((await http.get("/health")).status_code, 200)
            self.assertFalse(core.http.is_closed)
        self.assertTrue(core.http.is_closed)

    async def test_unknown_paths_need_the_bearer_too(self):
        async with serve(self.app) as http:
            self.assertEqual((await http.get("/admin")).status_code, 401)
            r = await http.get("/admin", headers={"Authorization": f"Bearer {TOKEN}"})
            self.assertEqual(r.status_code, 404)

    async def test_deploy_canary_request(self):
        # The exact request deployctl's edge canary sends: first RAG_MCP_TOKENS entry, expects "serverInfo".
        async with serve(self.app) as http:
            r = await http.post("/mcp", headers={"Accept": "application/json, text/event-stream",
                                                 "Content-Type": "application/json",
                                                 "MCP-Protocol-Version": "2025-06-18",
                                                 "Authorization": f"Bearer {TOKEN}"},
                                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                      "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                 "clientInfo": {"name": "health-canary", "version": "1"}}})
        self.assertEqual(r.status_code, 200)
        self.assertIn("serverInfo", r.text)

    async def test_dns_rebinding_protection_kept(self):
        async with serve(self.app) as http:
            r = await rpc(http, "tools/list", headers={"Host": "evil.example"})
            self.assertIn(r.status_code, (400, 421))
            r = await rpc(http, "tools/list", headers={"Host": "deep32-gateway.example.ts.net"})
            self.assertEqual(r.status_code, 200)

    async def test_health_is_public(self):
        async with serve(self.app) as http:
            r = await http.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok", "service": "deep32-gateway", "version": VERSION,
                                    "backend": "deep32-engine", "retrievers": ["bm25", "dense"]})

    async def test_health_degraded_when_core_fails(self):
        for answer in (reply(500), reply(200, ["not", "an object"])):
            self.fake.routes[("GET", "/health")] = answer
            async with serve(make_app(self.fake)) as http:
                r = await http.get("/health")
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.json()["status"], "degraded")


class GuardCompareTest(unittest.TestCase):
    def test_every_token_is_compared_even_after_a_match(self):
        guard = Guard(None, tokens=(TOKEN, OLD_TOKEN), networks=())
        scope = {"headers": [(b"authorization", f"Bearer {TOKEN}".encode())]}
        with mock.patch("rag_edge.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.assertTrue(guard.authorized(scope))
        self.assertEqual(compare.call_count, 2)


class NoTokensTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_tokens_deny_everything_but_health(self):
        async with serve(make_app(FakeCore(routes()), tokens=())) as http:
            self.assertEqual((await rpc(http, "initialize", INIT_PARAMS)).status_code, 401)
            self.assertEqual((await rpc(http, "tools/list", token=None,
                                        headers={"Authorization": "Bearer "})).status_code, 401)
            self.assertEqual((await http.get("/health")).status_code, 200)


class AllowListTest(unittest.IsolatedAsyncioTestCase):
    NETWORKS = (ipaddress.ip_network("198.51.100.61/32"), ipaddress.ip_network("192.0.2.0/24"))

    async def status(self, client_ip, headers=None, path="/mcp", token=TOKEN):
        async with serve(make_app(FakeCore(routes()), networks=self.NETWORKS), client=(client_ip, 0)) as http:
            if path == "/health":
                return (await http.get(path)).status_code
            return (await rpc(http, "tools/list", headers=headers, token=token)).status_code

    async def test_listed_addresses_pass(self):
        self.assertEqual(await self.status("198.51.100.61"), 200)
        self.assertEqual(await self.status("192.0.2.10"), 200)
        self.assertEqual(await self.status("::ffff:198.51.100.61"), 200)

    async def test_other_addresses_get_403_even_with_a_token(self):
        self.assertEqual(await self.status("198.51.100.62"), 403)
        self.assertEqual(await self.status("fd7a:115c:a1e0::1"), 403)
        self.assertEqual(await self.status("not-an-ip"), 403)

    async def test_the_address_is_checked_before_the_token(self):
        # A disallowed address learns nothing about the bearer: 403 with or without one, never 401.
        self.assertEqual(await self.status("198.51.100.62", token=None), 403)
        self.assertEqual(await self.status("198.51.100.62", token="wrong-" + "w" * 40), 403)
        self.assertEqual(await self.status("198.51.100.61", token=None), 401)

    async def test_local_callers_pass_but_unresolved_proxy_traffic_does_not(self):
        self.assertEqual(await self.status("127.0.0.1"), 200)  # the deploy canary, on the host
        self.assertEqual(await self.status("127.0.0.1", {"X-Forwarded-For": "198.51.100.2"}), 403)

    async def test_health_stays_public(self):
        self.assertEqual(await self.status("203.0.113.99", path="/health"), 200)


class SdkClientTest(unittest.IsolatedAsyncioTestCase):
    """The SDK's own streamable-HTTP client against the ASGI app, as Claude Code connects."""

    async def connect(self, headers, statuses, mode="legacy"):
        app = make_app(FakeCore(routes()))

        async def record(response):
            statuses.append(response.status_code)

        async with app.router.lifespan_context(app):
            http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE_URL, headers=headers,
                                      event_hooks={"response": [record]})
            async with http, Client(streamable_http_client(f"{BASE_URL}/mcp", http_client=http),
                                    mode=mode) as client:
                tools = await client.list_tools()
                result = await client.call_tool("lookup_id", {"identifier": "LSA type 7"})
                return client.server_info, tools, result

    async def test_with_bearer(self):
        for mode in ("legacy", "auto"):  # initialize handshake, and the 2026 discover probe with fallback
            with self.subTest(mode=mode):
                statuses = []
                info, tools, result = await self.connect({"Authorization": f"Bearer {TOKEN}"}, statuses, mode)
                self.assertEqual({t.name for t in tools.tools}, TOOLS)
                self.assertFalse(result.is_error)
                self.assertIn('<lookup kind="iana">', result.content[0].text)
                self.assertNotIn(401, statuses)
                if mode == "legacy":
                    self.assertEqual(info.name, "deep32-gateway")

    async def test_without_bearer(self):
        statuses = []
        with self.assertRaises(ExceptionGroup) as caught:
            await self.connect({}, statuses)
        self.assertIsNotNone(caught.exception.subgroup(MCPError))
        self.assertEqual(statuses, [401])


if __name__ == "__main__":
    unittest.main()
