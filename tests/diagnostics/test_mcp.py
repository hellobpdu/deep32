"""Offline synthetic MCP protocol and secret-boundary tests."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "diagnostics"))
from deep32_diag.mcp import (EnrolledEndpoint, MAX_BYTES, MCPClient, MCPError,
                             TOOLS)


TEST_TOKEN = "ab" * 32  # Synthetic test material; never a real credential.
URL = "https://workstation.example.test/mcp"


class TestCredential:
    def authorization(self):
        return "Bearer " + TEST_TOKEN

    def __repr__(self):
        return "<SyntheticCredential redacted>"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None, *, chunks=None,
                 before_read=None):
        self.status = status
        self.headers = {"Content-Type": "application/json"} if headers is None else headers
        self.body = body
        self.chunks = list(chunks) if chunks is not None else None
        self.before_read = before_read
        self.closed = False
        self.reads = []

    def read(self, size, *, deadline):
        self.reads.append((size, deadline))
        if self.before_read:
            self.before_read()
        if self.chunks is not None:
            if not self.chunks:
                return b""
            return self.chunks.pop(0)
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, *replies, before_open=None):
        self.replies = list(replies)
        self.requests = []
        self.responses = []
        self.before_open = before_open

    def open(self, request):
        self.requests.append(request)
        if self.before_open:
            self.before_open()
        if not self.replies:
            raise AssertionError("Unexpected synthetic request")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        response = reply(request) if callable(reply) else reply
        self.responses.append(response)
        return response


def rpc(result, *, session=None, content_type="application/json", sse=False):
    def answer(request):
        ident = json.loads(request.body)["id"]
        raw = json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}).encode()
        headers = {"Content-Type": content_type}
        if session is not None:
            headers["Mcp-Session-Id"] = session
        if sse:
            raw = b"event: message\ndata: " + raw + b"\n\n"
            headers["Content-Type"] = "text/event-stream"
        return FakeResponse(raw, headers=headers)
    return answer


def initial(*, version="2024-11-05", session=None, sse=False):
    return rpc({"protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "synthetic-gateway", "version": "1"}},
               session=session, sse=sse)


def notified():
    return FakeResponse(status=202, headers={})


def tool_list(names=None, **extra):
    return rpc({"tools": [{"name": name} for name in
                           (sorted(TOOLS) if names is None else names)], **extra})


def status_value():
    return {
        "ok": True, "generation": "synthetic-generation",
        "corpus_fingerprint": "13" * 32,
        "backends": {"bm25": {"ok": True},
                     "dense": {"enabled": True, "ok": True, "breaker": "closed"},
                     "reranker": {"enabled": True, "ok": True, "breaker": "closed"},
                     "kb": {"ok": True}, "structure": {"ok": True}},
        "degraded": [],
    }


def status_reply(value=None, **extra):
    return rpc({"content": [{"type": "text", "text": json.dumps(
        status_value() if value is None else value)}], **extra})


class MCPTests(unittest.TestCase):
    def client(self, *replies, **kwargs):
        self.transport = FakeTransport(*replies)
        self.clock = Clock()
        return MCPClient(EnrolledEndpoint(URL, "workstation.example.test"),
                         TestCredential(), transport=self.transport,
                         clock=self.clock, **kwargs)

    def fail_code(self, code, operation):
        with self.assertRaises(MCPError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(TEST_TOKEN, repr(caught.exception))

    def test_enrolled_endpoint_exact_authority(self):
        endpoint = EnrolledEndpoint(URL, "workstation.example.test")
        self.assertEqual(endpoint.url, URL)
        other = EnrolledEndpoint("https://workstation.example.test:8443/mcp",
                                 "workstation.example.test", 8443)
        self.assertEqual(other.enrolled_port, 8443)
        self.assertNotIn("example", repr(endpoint))

    def test_unsafe_endpoints_refused(self):
        bad = ["http://workstation.example.test/mcp", URL + "?x=1", URL + "?",
               URL + "#x", URL + "#", URL + "/", URL.replace("/mcp", "/health"),
               "https://user:password@workstation.example.test/mcp",
               "https://workstation.example.test:8443/mcp",
               "https://workstation.example.test:0/mcp",
               "https://workstation.example.test:broken/mcp",
               "https://workstation.example.test\\@foreign.example.test/mcp",
               "https://foreign.example.test/mcp", "https://127.0.0.1/mcp",
               "https://localhost/mcp", "https://[broken/mcp",
               URL + "\n", "https://workstation..example.test/mcp",
               "https://WORKSTATION.example.test/mcp"]
        for url in bad:
            with self.subTest(url=url):
                self.fail_code("mcp-endpoint", lambda: EnrolledEndpoint(
                    url, "workstation.example.test"))

    def test_default_transport_is_unqualified_no_socket(self):
        client = MCPClient(EnrolledEndpoint(URL, "workstation.example.test"), TestCredential())
        with patch("socket.socket", side_effect=AssertionError("No network")):
            self.fail_code("mcp-transport-unqualified", client.initialise)

    def test_json_initialisation_notification_and_session(self):
        client = self.client(initial(session="synthetic-session"), notified(), tool_list())
        self.assertEqual(client.list_tools(), tuple(sorted(TOOLS)))
        requests = self.transport.requests
        objects = [json.loads(request.body) for request in requests]
        self.assertEqual([obj["method"] for obj in objects],
                         ["initialize", "notifications/initialized", "tools/list"])
        self.assertNotIn("id", objects[1])
        self.assertNotIn("Mcp-Session-Id", requests[0].headers)
        for request in requests[1:]:
            self.assertEqual(request.headers["Mcp-Session-Id"], "synthetic-session")
        for request in requests:
            self.assertFalse(request.allow_redirects)
            self.assertFalse(request.use_proxies)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.max_bytes, MAX_BYTES)
            self.assertEqual(request.deadline, 110)
            self.assertNotIn(TEST_TOKEN, repr(request))
            self.assertTrue(self.transport.responses[requests.index(request)].closed)

    def test_observe_authentication_tools_and_readiness(self):
        client = self.client(FakeResponse(status=401), initial(), notified(),
                             tool_list(), status_reply())
        observed = client.observe()
        self.assertTrue(observed.ready)
        self.assertEqual(observed.generation, "synthetic-generation")
        self.assertNotIn("Authorization", self.transport.requests[0].headers)
        self.assertEqual([json.loads(r.body).get("id") for r in self.transport.requests],
                         [1, 2, None, 3, 4])

    def test_second_call_reuses_initialised_session(self):
        client = self.client(initial(), notified(), tool_list(), status_reply())
        client.list_tools()
        self.assertTrue(client.status().ready)
        self.assertEqual(len(self.transport.requests), 4)

    def test_anonymous_accepted_fails(self):
        client = self.client(FakeResponse(status=200))
        self.fail_code("mcp-anonymous", client.probe_authentication)
        self.assertEqual(len(self.transport.requests), 1)

    def test_anonymous_forbidden_passes_and_does_not_read_body(self):
        response = FakeResponse(TEST_TOKEN.encode(), status=403)
        client = self.client(response)
        client.probe_authentication()
        self.assertEqual(response.reads, [])
        self.assertTrue(response.closed)

    def test_http_auth_and_redirect_errors_are_constant(self):
        for status, code in ((401, "mcp-auth"), (403, "mcp-auth"),
                             (301, "mcp-redirect"), (302, "mcp-redirect"),
                             (307, "mcp-redirect"), (308, "mcp-redirect"),
                             (500, "mcp-http")):
            with self.subTest(status=status):
                response = FakeResponse(TEST_TOKEN.encode(), status=status,
                    headers={"Location": "https://foreign.example.test/" + TEST_TOKEN})
                client = self.client(response)
                self.fail_code(code, client.initialise)
                self.assertTrue(response.closed)
                self.assertEqual(response.reads, [])
                self.assertEqual(len(self.transport.requests), 1)

    def test_transport_errors_and_timeout_never_keep_secret_context(self):
        for error, code in ((OSError(TEST_TOKEN), "mcp-transport"),
                            (TimeoutError(TEST_TOKEN), "mcp-timeout")):
            client = self.client(error)
            self.fail_code(code, client.initialise)

    def test_credential_errors_and_header_injection(self):
        class Broken:
            def authorization(self):
                raise OSError(TEST_TOKEN)
        client = self.client()
        client._credential = Broken()
        self.fail_code("mcp-credential", client.initialise)
        self.assertEqual(self.transport.requests, [])
        for header in (TEST_TOKEN, "Bearer " + TEST_TOKEN + "\r\nX-Evil: yes", "Bearer short"):
            credential = type("Synthetic", (), {"authorization": lambda self, h=header: h})()
            client = self.client()
            client._credential = credential
            self.fail_code("mcp-credential", client.initialise)

    def test_version_negotiation_and_subsequent_header(self):
        client = self.client(initial(version="2025-03-26"), notified(),
            protocol_versions=("2024-11-05", "2025-03-26"))
        client.initialise()
        self.assertEqual(self.transport.requests[1].headers["MCP-Protocol-Version"], "2025-03-26")

    def test_unsupported_version_fails_before_notification(self):
        client = self.client(initial(version="2099-01-01"))
        self.fail_code("mcp-version", client.initialise)
        self.assertEqual(len(self.transport.requests), 1)

    def test_invalid_initialisation_capabilities(self):
        client = self.client(rpc({"protocolVersion": "2024-11-05", "capabilities": {},
                                 "serverInfo": {"name": "demo", "version": "1"}}))
        self.fail_code("mcp-initialise", client.initialise)

    def test_session_changes_and_expiry_no_retry(self):
        for reply in (rpc({"tools": []}, session="changed"), FakeResponse(status=404)):
            client = self.client(initial(session="original"), notified(), reply)
            self.fail_code("mcp-session", client.list_tools)
            self.assertEqual(len(self.transport.requests), 3)

    def test_invalid_session_headers(self):
        for session in ("", "x" * 257, "space in session", "bad\nheader"):
            client = self.client(initial(session=session))
            self.fail_code("mcp-headers" if "\n" in session else "mcp-session", client.initialise)

    def test_late_unsolicited_session_refused(self):
        client = self.client(initial(), notified(), rpc({"tools": []}, session="new"))
        self.fail_code("mcp-session", client.list_tools)

    def test_notification_must_have_empty_accepted_response(self):
        for response in (FakeResponse(b"{}", status=202), FakeResponse(status=200)):
            client = self.client(initial(), response)
            self.fail_code("mcp-envelope", client.initialise)

    def test_malformed_json_and_duplicate_keys(self):
        for raw in (b"not json", b"\xff", b"[]", b'{"id":1,"id":1}',
                    b'{"jsonrpc":"2.0","id":1,"result":{"x":NaN}}'):
            client = self.client(FakeResponse(raw))
            self.fail_code("mcp-envelope" if raw == b"[]" else "mcp-json", client.initialise)

    def test_wrong_rpc_envelopes(self):
        values = [
            {"jsonrpc": "2.0", "id": True, "result": {}},
            {"jsonrpc": "2.0", "id": 2, "result": {}},
            {"jsonrpc": "1.0", "id": 1, "result": {}},
            {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}},
            {"jsonrpc": "2.0", "id": 1},
            {"jsonrpc": "2.0", "id": 1, "result": []},
            {"jsonrpc": "2.0", "id": 1, "result": {}, "private": TEST_TOKEN},
        ]
        for value in values:
            client = self.client(FakeResponse(json.dumps(value).encode()))
            self.fail_code("mcp-envelope", client.initialise)

    def test_rpc_error_body_not_exposed(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 1,
                          "error": {"code": -1, "message": TEST_TOKEN}}).encode()
        client = self.client(FakeResponse(raw))
        self.fail_code("mcp-rpc", client.initialise)

    def test_wrong_tools_duplicates_and_pagination(self):
        for reply in (tool_list(["rag_status"]), tool_list(["rag_status"] * 11),
                      tool_list([*sorted(TOOLS)[:-1], "foreign_tool"]),
                      tool_list(nextCursor="more")):
            client = self.client(initial(), notified(), reply)
            self.fail_code("mcp-tools", client.list_tools)

    def test_sse_split_utf8_crlf_notifications_and_multiline_data(self):
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "synthetic-\u00e9", "version": "1"}}
        raw = (b": keepalive\r\n\r\ndata: " + json.dumps({"jsonrpc": "2.0",
            "method": "notifications/progress", "params": {}}).encode() + b"\r\n\r\n" +
            b"event: message\r\nid: optional\r\ndata: {\r\ndata: \"jsonrpc\":\"2.0\",\"id\":1,\"result\":" +
            json.dumps(result, ensure_ascii=False).encode() + b"}\r\n\r\n")
        response = FakeResponse(headers={"Content-Type": "text/event-stream; charset=utf-8"},
                                chunks=[raw[i:i+1] for i in range(len(raw))])
        client = self.client(response, notified())
        client.initialise()
        self.assertTrue(response.closed)

    def test_sse_returns_result_without_waiting_for_stream_eof(self):
        first = initial(sse=True)(type("Request", (), {"body": b'{"id":1}'})())
        first.chunks = [first.body]
        first.body = b""
        def next_read():
            if not first.chunks:
                raise AssertionError("Must stop at completed result")
        first.before_read = next_read
        client = self.client(first, notified())
        client.initialise()

    def test_sse_malformed_or_unterminated_events(self):
        raws = [b"data: {}\n\n", b"event: foreign\ndata: {}\n\n", b"data: {}\n",
                b"data: \xff\n\n", b"foreign: x\n\n",
                b'data: {"jsonrpc":"2.0","id":99,"result":{}}\n\n',
                b'data: {"jsonrpc":"2.0","id":null,"method":"tools/call"}\n\n']
        for raw in raws:
            client = self.client(FakeResponse(raw, headers={"Content-Type": "text/event-stream"}))
            with self.assertRaises(MCPError):
                client.initialise()

    def test_sse_event_count_bounded(self):
        raw = (b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n' * 129)
        client = self.client(FakeResponse(raw, headers={"Content-Type": "text/event-stream"}))
        self.fail_code("mcp-sse", client.initialise)

    def test_json_and_sse_caps_and_oversized_transport_chunk(self):
        for response in (FakeResponse(b"x" * 1025), FakeResponse(
                b":" + b"x" * 1024, headers={"Content-Type": "text/event-stream"}),
                FakeResponse(chunks=[b"x" * 1026])):
            client = self.client(response, max_bytes=1024)
            self.fail_code("mcp-limit", client.initialise)
            self.assertTrue(response.closed)

    def test_untrusted_headers_and_encoding_refused(self):
        cases = [({"Content-Type": "application/json", "Content-Length": str(MAX_BYTES + 1)}, "mcp-limit"),
                 ({"Content-Type": "application/json", "Content-Encoding": "gzip"}, "mcp-encoding"),
                 ({"Content-Type": "text/html"}, "mcp-encoding"),
                 ({"Content-Type": "application/json", "content-type": "application/json"}, "mcp-headers"),
                 ({"Content-Type": "application/json", "X-Raw": TEST_TOKEN + "\n"}, "mcp-headers")]
        for headers, code in cases:
            client = self.client(FakeResponse(b"{}", headers=headers))
            self.fail_code(code, client.initialise)

    def test_timeout_in_open_and_body_and_global_deadline(self):
        client = self.client(initial())
        self.transport.before_open = lambda: setattr(self.clock, "now", 111)
        self.fail_code("mcp-timeout", client.initialise)
        self.assertTrue(self.transport.responses[0].closed)
        response = FakeResponse(b"{}")
        client = self.client(response)
        response.before_read = lambda: setattr(self.clock, "now", 111)
        self.fail_code("mcp-timeout", client.initialise)
        self.assertTrue(response.closed)
        client = self.client(initial(), deadline=99)
        self.fail_code("mcp-timeout", client.initialise)
        self.assertEqual(self.transport.requests, [])

    def test_group_deadline_not_reset_per_exchange(self):
        client = self.client(FakeResponse(status=401), initial(), notified(), tool_list(), status_reply())
        def advance():
            self.clock.now += 3
        self.transport.before_open = advance
        self.fail_code("mcp-timeout", client.observe)
        self.assertTrue(all(r.deadline == 110 for r in self.transport.requests))
        self.assertEqual(len(self.transport.requests), 4)

    def test_cleanup_and_extractor_are_in_deadline(self):
        response = initial()(type("Request", (), {"body": b'{"id":1}'})())
        client = self.client(response)
        def slow_close():
            response.closed = True
            self.clock.now = 111
        response.close = slow_close
        self.fail_code("mcp-timeout", client.initialise)
        self.assertTrue(response.closed)
        client = self.client(initial(), notified(), status_reply())
        def slow_extractor(result):
            self.clock.now = 111
            return True
        self.fail_code("mcp-timeout", lambda: client._extract_tool(
            "rag_status", {"format": "json"}, slow_extractor))

    def test_absolute_deadline_shortens_entire_group(self):
        client = self.client(initial(), notified(), deadline=105)
        client.initialise()
        self.assertEqual([r.deadline for r in self.transport.requests], [105, 105])

    def test_full_four_mebibyte_limit_is_enforced(self):
        response = FakeResponse(b"x" * (MAX_BYTES + 1))
        client = self.client(response)
        self.fail_code("mcp-limit", client.initialise)
        self.assertTrue(response.closed)

    def test_failing_typed_extractor_cannot_expose_raw_result(self):
        client = self.client(initial(), notified(), status_reply())
        def bad_extractor(result):
            raise ValueError(TEST_TOKEN)
        self.fail_code("mcp-transport", lambda: client._extract_tool(
            "rag_status", {"format": "json"}, bad_extractor))

    def test_invalid_resource_configuration(self):
        for options in ({"timeout": 11}, {"timeout": float("nan")},
                        {"max_bytes": MAX_BYTES + 1}, {"max_bytes": True},
                        {"deadline": float("inf")}, {"protocol_versions": ()}):
            self.fail_code("mcp-state", lambda: self.client(**options))

    def test_degraded_backend_breaker_cannot_be_hidden_by_top_level_ok(self):
        value = status_value()
        value["backends"]["dense"]["breaker"] = "open"
        client = self.client(initial(), notified(), status_reply(value))
        observed = client.status()
        self.assertTrue(observed.ok)
        self.assertFalse(observed.ready)
        self.assertEqual(observed.degraded, ("dense",))

    def test_disabled_required_dense_and_reranker_are_not_ready(self):
        for name in ("dense", "reranker"):
            value = status_value()
            value["backends"][name] = {"enabled": False, "ok": None}
            client = self.client(initial(), notified(), status_reply(value))
            self.assertFalse(client.status().ready)

    def test_missing_generation_or_fingerprint_is_not_ready(self):
        for name in ("generation", "corpus_fingerprint"):
            value = status_value()
            value.pop(name)
            client = self.client(initial(), notified(), status_reply(value))
            self.assertFalse(client.status().ready)

    def test_generation_matches_doctor_schema_at_128_character_boundary(self):
        for generation in ("+", "public+generation", ".public-generation",
                           "_public-generation", "g" * 127 + "+"):
            with self.subTest(length=len(generation), plus="+" in generation):
                value = status_value()
                value["generation"] = generation
                client = self.client(initial(), notified(), status_reply(value))
                observed = client.status()
                self.assertEqual(observed.generation, generation)
                self.assertTrue(observed.ready)

    def test_generation_rejects_129_characters_and_non_schema_characters(self):
        for generation in ("g" * 128 + "+", "", "public/generation",
                           "public generation", "public\ngeneration", "public\u00e9generation"):
            with self.subTest(length=len(generation)):
                value = status_value()
                value["generation"] = generation
                client = self.client(initial(), notified(), status_reply(value))
                self.fail_code("mcp-status", client.status)

    def test_legacy_and_malformed_status_refused(self):
        values = [{"ok": True}, status_value(), status_value(), status_value(), status_value()]
        values[1]["backends"]["dense"]["breaker"] = TEST_TOKEN
        values[2]["backends"]["kb"]["ok"] = "true"
        values[3]["degraded"] = [TEST_TOKEN]
        values[4]["ok"] = False
        for value in values:
            client = self.client(initial(), notified(), status_reply(value))
            self.fail_code("mcp-status", client.status)

    def test_tool_errors_content_and_unapproved_name_refused(self):
        client = self.client(initial(), notified(), status_reply(isError=True))
        self.fail_code("mcp-tool", client.status)
        client = self.client(initial(), notified(), rpc({"content": []}))
        self.fail_code("mcp-status", client.status)
        client = self.client()
        self.fail_code("mcp-tool", lambda: client._extract_tool("admin/reset", {}, lambda v: v))
        self.assertEqual(self.transport.requests, [])

    def test_status_allowlist_drops_messages_urls_paths_and_settings(self):
        value = status_value()
        value.update({"message": TEST_TOKEN, "private_path": "C:/synthetic/private",
                      "signin": "https://signin.example.test/" + TEST_TOKEN,
                      "settings": {"Authorization": "Bearer " + TEST_TOKEN}})
        value["backends"]["dense"]["last_error_kind"] = TEST_TOKEN
        client = self.client(initial(), notified(), status_reply(value))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            observed = client.status()
        self.assertTrue(observed.ready)
        self.assertNotIn(TEST_TOKEN, repr(observed))
        self.assertNotIn("private", repr(observed))
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_token_variants_and_hash_cannot_masquerade_as_identifiers(self):
        variants = [TEST_TOKEN, TEST_TOKEN.upper(),
                    base64.b64encode(TEST_TOKEN.encode()).decode().rstrip("="),
                    hashlib.sha256(TEST_TOKEN.encode()).hexdigest()]
        for secret in variants:
            value = status_value()
            value["generation"] = secret
            client = self.client(initial(), notified(), status_reply(value))
            self.fail_code("mcp-status", client.status)
        value = status_value()
        value["corpus_fingerprint"] = TEST_TOKEN
        client = self.client(initial(), notified(), status_reply(value))
        self.fail_code("mcp-status", client.status)

    def test_no_file_writes_process_or_socket_calls(self):
        client = self.client(FakeResponse(status=401), initial(), notified(), tool_list(), status_reply())
        def denied(*args, **kwargs):
            raise AssertionError("Mutation or network outside injected capability")
        with patch("builtins.open", denied), patch("os.open", denied), \
                patch("socket.socket", denied), patch("subprocess.Popen", denied):
            self.assertTrue(client.observe().ready)


if __name__ == "__main__":
    unittest.main()
