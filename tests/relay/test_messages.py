import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "adapters" / "relay"))
from deep32_relay import messages as m
from deep32_relay.wire import ProtocolError


class JSONTests(unittest.TestCase):
    def test_strict_rejections(self):
        values = [b'\xef\xbb\xbf{}', b'{"a":1,"a":2}', b'{"a":1,"\\u0061":2}',
                  b'"\\ud800"', b'"\\udc00"', b'"\x00"', b'"\xff"', b'{}x',
                  b'NaN', b'Infinity', b'[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]']
        for value in values:
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                m.loads(value)

    def test_surrogate_pair_and_escaped_nul(self):
        self.assertEqual(m.loads(b'"\\ud83d\\ude00"'), "\U0001f600")
        self.assertEqual(m.loads(b'"\\u0000"'), "\0")

    def test_non_integer_numeric_lexemes_match_c_jnum(self):
        for number in (b"1.0", b"1e0", b"1e999999999999999999999", b"9223372036854775808", b"9" * 5000):
            value = m.loads(number)
            self.assertFalse(m.integer(value))
            self.assertEqual(m.dumps(value), number)

    def test_depth_boundary(self):
        self.assertEqual(m.loads(b"[" * 32 + b"0" + b"]" * 32), self.nested(32))
        with self.assertRaises(ProtocolError):
            m.loads(b"[" * 33 + b"0" + b"]" * 33)

    @staticmethod
    def nested(count):
        value = 0
        for _ in range(count):
            value = [value]
        return value


class SchemaTests(unittest.TestCase):
    def test_hello_exact_and_optional(self):
        hello = m.hello("relay", bytes(range(32)), [{"name": "research", "models": ["qualified"], "efforts": ["one"]}])
        self.assertEqual(m.validate_hello(hello), hello)
        for key, value in (("version", True), ("version", 1.0), ("versions", [True]),
                           ("nonce", "A" * 64), ("codex_version", "new"), ("engine", "codex"),
                           ("features", ["research-v1", "pairing-v2"]), ("sessions", ["old"]), ("role", "node")):
            bad = copy.deepcopy(hello)
            bad[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ProtocolError):
                m.validate_hello(bad)

    def test_ack_integer_and_boolean(self):
        valid = {"version": 1, "features": ["research-v1"], "paired": False}
        self.assertEqual(m.validate_ack(valid), valid)
        for value in (0, "false", None):
            with self.assertRaises(ProtocolError):
                m.validate_ack(dict(valid, paired=value))

    def test_identifiers(self):
        for value in ("a", "A-z_09", "x" * 64):
            self.assertTrue(m.identifier(value))
        for value in ("", "x" * 65, "a/b", "é", "a\0", 3):
            self.assertFalse(m.identifier(value))

    def test_requests_and_unknown_keys(self):
        self.assertEqual(m.validate_request({"op": "turn.start", "text": "hello"})["text"], "hello")
        for request in ({"op": "turn.start", "text": ""}, {"op": "turn.start", "text": "a\0"},
                        {"op": "turn.start", "text": "x", "session": "s1"}, {"op": "ask.state"},
                        {"op": "turn.start", "text": "\n" * 40000}):
            with self.assertRaises(ProtocolError):
                m.validate_request(request)

    def test_events_and_usage_int64(self):
        for value in (True, -1, 1.0, 2**63):
            with self.assertRaises(ProtocolError):
                m.validate_event({"op": "turn.completed", "session": "s1", "status": "completed",
                                  "usage": {"input_tokens": value, "output_tokens": 0}})
        m.validate_event({"op": "agent.delta", "session": "s1", "text": ""})
        with self.assertRaises(ProtocolError):
            m.validate_event({"op": "error", "session": "s1", "code": 8, "message": "extra"})

    def test_mcp_separation_and_header_injection(self):
        for envelope in ({"body": [], "mcp_session_id": None}, {"body": None, "mcp_session_id": "x\r\nX:y"},
                         {"body": None, "mcp_session_id": None, "token": "synthetic"},
                         {"body": None, "mcp_session_id": None, "protocol_version": None}):
            with self.assertRaises(ProtocolError):
                m.validate_mcp_request(envelope)
        m.validate_mcp_request({"body": None, "mcp_session_id": None})

    def test_exact_tool_method_allowlist(self):
        for tool in m.TOOLS:
            m.validate_mcp_request({"body": {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool}}, "mcp_session_id": None})
        for body in ({"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
                     {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "shell"}},
                     {"jsonrpc": "2.0", "id": 1, "method": "notifications/initialized"},
                     {"jsonrpc": "2.0", "method": "tools/list"}):
            with self.assertRaises(ProtocolError):
                m.validate_mcp_request({"body": body, "mcp_session_id": None})


if __name__ == "__main__":
    unittest.main()
