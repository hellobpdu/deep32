"""What counts against a turn's output, what the agent is told when the relay refuses a call, and the turn log.

Memory-only streams and the real HTTP handler on a memory socket; no agent, no network."""
import io
import json
from pathlib import Path
import sys
from unittest import mock

from support import OfflineCase, Agent, Scratch, request, rpc
from test_native import MemorySocket
from test_native_r15 import MemoryProxy
from deep32_relay import daemon, native, proxy, runtime
from deep32_relay.sessions import Supervisor
from deep32_relay.state import OwnedRoot, OWNER_SCHEMA


class Verbose:
    """A protocol like Claude Code's verbose stream: "echo" lines are discarded, "text" lines are the answer."""
    def events(self, line):
        value = json.loads(line)
        if value["kind"] == "echo":
            return []
        if value["kind"] == "done":
            return [{"type": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]
        return [{"type": "delta", "text": value["text"]}]


class Exited:
    def poll(self):
        return 0


def line(kind, size=0):
    return json.dumps({"kind": kind, "text": "x" * size}).encode() + b"\n"


class Output(OfflineCase):
    def session(self):
        plan = native.LaunchPlan((str(Path(sys.executable).resolve()),), self.root, {})
        session = native.NativeSession(plan, Verbose(), object(), MemoryProxy(), live=lambda: True)
        session.released, session.process = True, Exited()
        return session

    def test_the_bounds(self):
        self.assertEqual(native.MAX_EVENT_OUTPUT, 2 * 1024 * 1024)
        self.assertEqual(native.MAX_OUTPUT, 64 * 1024 * 1024)
        self.assertEqual(native.MAX_RESPONSE, 1024 * 1024)

    def test_discarded_lines_do_not_end_a_finished_answer(self):
        session = self.session()
        stream = b"".join([line("echo", 900000)] * 4 + [line("text", 5000), line("done")])
        session._read(io.BytesIO(stream), False)
        self.assertIsNone(session.failure)
        self.assertGreater(session.output_bytes, 3 * 1024 * 1024)     # past the old 2 MiB total
        events = session.poll()
        self.assertEqual([event["type"] for event in events], ["delta", "completed"])
        self.assertLess(session.event_bytes, 6000)
        self.assertTrue(session.completed)

    def test_lines_that_carry_the_answer_are_still_bounded(self):
        session = self.session()
        session._read(io.BytesIO(b"".join([line("text", 900000)] * 3)), False)
        self.assertIsNone(session.failure)
        with self.assertRaisesRegex(native.NativeError, "^native-output-limit$"):
            session.poll()
        self.assertGreater(session.event_bytes, native.MAX_EVENT_OUTPUT)

    def test_everything_else_has_a_backstop(self):
        session = self.session()
        with mock.patch.object(native, "MAX_OUTPUT", 3000):
            session._read(io.BytesIO(b"".join([line("echo", 1000)] * 4)), False)
        self.assertEqual(session.failure, "native-output-failed")
        with self.assertRaisesRegex(native.NativeError, "^native-output-failed$"):
            session.poll()

    def test_one_line_over_the_line_bound_still_fails(self):
        session = self.session()
        session._read(io.BytesIO(line("echo", native.MAX_RESPONSE)), False)
        self.assertEqual(session.failure, "native-output-failed")


class Refusals(OfflineCase):
    def setUp(self):
        super().setUp()
        self.cap = self.active_cap()
        self.initialise(self.cap)
        self.endpoint = proxy.HTTPProxy(self.gateway, self.cap)
        self.before = len(self.dispatch.calls)

    def exchange(self, body, bearer=None):
        raw = json.dumps(body).encode()
        head = ("POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:23456\r\nAuthorization: Bearer %s\r\n"
                "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (bearer or self.cap.bearer, len(raw)))
        connection = MemorySocket(head.encode() + raw)
        self.endpoint.handler()(connection, ("127.0.0.1", 45678),
                                type("Server", (), {"server_address": ("127.0.0.1", 23456)})())
        head, _, body = bytes(connection.output).partition(b"\r\n\r\n")
        return "HTTP/1.1 " + head.split(b"\r\n")[0].decode().split(" ")[1], head, body

    def test_the_numbers(self):
        self.assertEqual((proxy.IN_FLIGHT, proxy.CALL_BUDGET, proxy.CONNECTIONS), (4, 300, 8))
        self.assertIn("at most 4 tool calls can run at once", proxy.REFUSALS["request-limit"])
        self.assertIn("Send this call again", proxy.REFUSALS["request-limit"])
        self.assertIn("used all 300 of its tool calls", proxy.REFUSALS["call-budget"])
        self.assertIn("Write the answer from what you have", proxy.REFUSALS["call-budget"])

    def test_a_fifth_call_in_flight_is_told_to_send_it_again(self):
        self.cap.inflight = 4
        status, head, body = self.exchange(rpc("tools/call", ident=7, name="verify_quote", arguments={}))
        self.assertEqual(status, "HTTP/1.1 200")
        self.assertIn(b"Mcp-Session-Id: synthetic-mcp-session", head)
        self.assertEqual(json.loads(body), {"jsonrpc": "2.0", "id": 7, "result": {
            "content": [{"type": "text", "text": proxy.REFUSALS["request-limit"]}], "isError": True}})
        self.assertEqual((len(self.dispatch.calls), self.cap.inflight, self.cap.refused), (self.before, 4, 1))

    def test_a_spent_budget_is_told_to_write_the_answer(self):
        self.cap.calls = 300
        status, _, body = self.exchange(rpc("tools/call", ident="call-9", name="search_rag", arguments={}))
        self.assertEqual(status, "HTTP/1.1 200")
        value = json.loads(body)
        self.assertEqual((value["id"], value["result"]["isError"]), ("call-9", True))
        self.assertEqual(value["result"]["content"][0]["text"], proxy.REFUSALS["call-budget"])
        self.assertEqual((len(self.dispatch.calls), self.cap.refused), (self.before, 1))

    def test_the_budget_text_wins_when_both_apply(self):
        self.cap.calls, self.cap.inflight = 300, 4
        _, _, body = self.exchange(rpc("tools/call", ident=1, name="rag_status", arguments={}))
        self.assertEqual(json.loads(body)["result"]["content"][0]["text"], proxy.REFUSALS["call-budget"])

    def test_every_other_refusal_stays_a_bare_error(self):
        self.cap.inflight = 4
        for body in (rpc("tools/list", ident=3), rpc("initialize", ident=4),
                     dict(jsonrpc="2.0", method="tools/call", id=True, params=dict(name="rag_status"))):
            with self.subTest(method=body["method"]):
                status, _, raw = self.exchange(body)
                self.assertEqual((status, raw), ("HTTP/1.1 400", b'{"error":"request-refused"}'))
        self.cap.inflight = 0
        status, _, raw = self.exchange(rpc("tools/call", ident=5, name="shell", arguments={}))
        self.assertEqual((status, raw), ("HTTP/1.1 400", b'{"error":"request-refused"}'))
        self.gateway.revoke(self.cap.session)
        status, _, raw = self.exchange(rpc("tools/call", ident=6, name="rag_status", arguments={}))
        self.assertEqual((status, raw), ("HTTP/1.1 400", b'{"error":"request-refused"}'))
        self.assertEqual(len(self.dispatch.calls), self.before)

    def test_no_text_without_the_session_bearer(self):
        self.cap.inflight = 4
        status, _, raw = self.exchange(rpc("tools/call", ident=7, name="rag_status", arguments={}), bearer="wrong")
        self.assertEqual((status, raw), ("HTTP/1.1 401", b'{"error":"request-refused"}'))
        self.assertEqual(self.cap.refused, 0)

    def test_a_call_within_the_limits_is_untouched(self):
        self.cap.inflight = 3
        status, _, body = self.exchange(rpc("tools/call", ident=8, name="rag_status", arguments={}))
        self.assertEqual(status, "HTTP/1.1 200")
        self.assertEqual(json.loads(body)["result"], {"content": [{"type": "text", "text": "synthetic tool result"}]})
        self.assertEqual((len(self.dispatch.calls), self.cap.refused), (self.before + 1, 0))


class TurnLog(OfflineCase):
    def supervisor(self, on_turn):
        self.agent = Agent()
        supervisor = Supervisor(self.agent, self.gateway, lambda stream, event: None,
                                lambda ident: Scratch(self.root, ident), clock=self.clock, on_turn=on_turn)
        supervisor.open(1, "synthetic-1", request())
        return supervisor

    def test_each_turn_is_reported_once_with_its_numbers(self):
        seen = []
        supervisor = self.supervisor(seen.append)
        supervisor.start(1, "synthetic question")
        session = supervisor.sessions[1]
        self.initialise(session.capability)
        self.gateway.call(session.capability, rpc("tools/call", ident=3, name="rag_status", arguments={}))
        session.capability.refused = 2
        self.agent.handles[0].output_bytes = 123456
        self.clock.advance(2.5)
        supervisor.output(1, session.epoch, {"type": "delta", "text": "synthetic answer"})
        supervisor.output(1, session.epoch, {"type": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}})
        self.assertEqual(seen, [dict(status="completed", reason="none", calls=4, refused=2, output_bytes=123456,
                                     seconds=2.5, check="off")])
        supervisor.start(1, "second question")
        supervisor.interrupt(1)
        supervisor.interrupt(1)
        self.assertEqual([(item["status"], item["reason"], item["calls"]) for item in seen[1:]],
                         [("interrupted", "cancelled", 0)])

    def test_a_log_that_fails_never_fails_the_turn(self):
        def broken(numbers):
            raise RuntimeError("synthetic")
        supervisor = self.supervisor(broken)
        supervisor.start(1, "synthetic question")
        session = supervisor.sessions[1]
        supervisor.output(1, session.epoch, {"type": "delta", "text": "synthetic answer"})
        supervisor.output(1, session.epoch, {"type": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}})
        self.assertEqual((supervisor.last_result["status"], session.state), ("completed", "idle"))

    def test_the_log_line_has_counts_and_no_text(self):
        path = self.root / "events.jsonl"
        ledger = runtime.EventLedger(path, validate_owner=lambda: None, clock=lambda: 1795000000)
        numbers = dict(status="completed", reason="none", calls=41, refused=1, output_bytes=1500000, seconds=212.75)
        self.assertTrue(runtime.log_turn(ledger, numbers))
        for status, reason, expected in (("interrupted", "cancelled", "cancelled"), ("failed", "turn-timeout", "timeout"),
                                         ("failed", "cleanup-failed", "cleanup"),
                                         ("failed", "agent-sign-in-required", "auth"),
                                         ("failed", "agent-output-failed", "unavailable")):
            self.assertTrue(runtime.log_turn(ledger, dict(numbers, status=status, reason=reason)))
        lines = [json.loads(item) for item in path.read_text(encoding="ascii").splitlines()]
        self.assertEqual(lines[0], dict(time=1795000000, sequence=1, event="turn-end", reason="none", count=41,
                                        refused=1, bytes=1500000, duration_ms=212750))
        self.assertEqual([item["reason"] for item in lines[1:]], ["cancelled", "timeout", "cleanup", "auth", "unavailable"])

    def test_a_broken_log_returns_false(self):
        class Broken:
            def append(self, *args, **counts):
                raise OSError("synthetic")
        self.assertFalse(runtime.log_turn(Broken(), dict(status="completed")))
        self.assertFalse(runtime.log_turn(None, None))

    def test_the_service_log_lives_in_the_relays_own_folder(self):
        from test_install import FakeSecurity
        owner = OwnedRoot(self.root / "owned", security=FakeSecurity())
        owner.create()
        owner.write_json("owner.json", dict(schema=OWNER_SCHEMA, install_id="1" * 32))
        log = daemon.turn_logger(owner)
        self.assertTrue(log(dict(status="completed", reason="none", calls=3, refused=0, output_bytes=10, seconds=1)))
        written = json.loads((owner.root / "logs/events.jsonl").read_text(encoding="ascii"))
        self.assertEqual((written["event"], written["count"], written["bytes"]), ("turn-end", 3, 10))
        self.assertIsNone(daemon.turn_logger(None))
