"""The tool calls a turn keeps, the paragraph break between an agent's messages, and the Checks footer in the
stream the Ask window shows. Synthetic agents and tool results (answer_fixtures.py); no agent, no network."""
import json
from unittest import mock

from support import OfflineCase, Agent, Dispatch, Scratch, request, rpc
import answer_fixtures as fx
from test_answer_check import CLEAN_FOOTER, FLAGGED_FOOTER
from deep32_relay import answer_check, messages, native, proxy, runtime, settings
from deep32_relay.sessions import MAX_ANSWER, SessionError, Supervisor

USAGE = {"type": "completed", "usage": {"input_tokens": 3, "output_tokens": 5}}


class Play(OfflineCase):
    """Sends fixture tool events through the gateway, the way an agent's MCP client would."""
    def setUp(self):
        super().setUp()
        self.results = {}

        def answer(session, envelope, deadline):
            body = envelope["body"]
            if body["method"] != "tools/call":
                return Dispatch()(session, envelope, deadline)
            value = self.results[body["id"]]
            return dict(body=dict(jsonrpc="2.0", id=body["id"], **value), http_status=200,
                        mcp_session_id="synthetic-mcp-session")
        self.dispatch.hook = answer

    def play(self, cap, events, first=10):
        if not cap.initialised:
            self.initialise(cap)
        for ident, event in enumerate(events, first):
            self.results[ident] = dict(result=dict(content=[dict(type="text", text=event["result"])]))
            self.gateway.call(cap, rpc("tools/call", ident=ident, name=event["tool"], arguments=event["arguments"]))


class Capture(Play):
    def test_nothing_is_kept_unless_the_turn_asks(self):
        cap = self.active_cap()
        self.play(cap, fx.clean_events())
        self.assertEqual((cap.events, cap.captured), ([], 0))

    def test_calls_are_kept_in_the_form_the_check_reads(self):
        cap = self.active_cap()
        cap.capture = True
        events = fx.clean_events()
        self.play(cap, events)
        self.assertEqual(cap.events, events)
        self.assertEqual(sorted(cap.events[0]), ["arguments", "is_error", "result", "tool"])
        self.assertEqual(cap.captured, sum(len(event["result"]) for event in events))
        json.dumps(cap.events)

    def test_parts_errors_and_failed_tools(self):
        cap = self.active_cap()
        cap.capture = True
        self.initialise(cap)
        self.results[20] = dict(result=dict(content=[dict(type="text", text="first"), dict(type="image", data="x"),
                                                     dict(type="text", text="second")]))
        self.results[21] = dict(error=dict(code=-32603, message="Edge unavailable"))
        self.results[22] = dict(result=dict(content=[dict(type="text", text="no such section")], isError=True))
        self.results[23] = dict(result=dict(content=[]))
        for ident, tool in ((20, "get_section"), (21, "search_rag"), (22, "get_section"), (23, "rag_status")):
            self.gateway.call(cap, rpc("tools/call", ident=ident, name=tool, arguments={"n": ident}))
        self.assertEqual([(event["tool"], event["result"], event["is_error"]) for event in cap.events],
                         [("get_section", "first\n[image]\nsecond", False), ("search_rag", "Edge unavailable", True),
                          ("get_section", "no such section", True), ("rag_status", "", False)])
        self.assertEqual(cap.events[0]["arguments"], {"n": 20})

    def test_past_the_limit_a_result_keeps_its_head_and_says_so(self):
        cap = self.active_cap()
        cap.capture = True
        with mock.patch.object(proxy, "CAPTURE_MAX", 10000), mock.patch.object(proxy, "CAPTURE_HEAD", 100):
            self.play(cap, [fx.call("get_section", "a" * 6000), fx.call("get_section", "b" * 6000),
                            fx.call("verify_quote", "c" * 50)])
        self.assertEqual([(len(event["result"]), event.get("truncated", False)) for event in cap.events],
                         [(6000, False), (100, True), (50, False)])
        result = answer_check.check("It waits 750 seconds.\n", cap.events)
        self.assertEqual(result["checks"]["specifics"], dict(status="unavailable", reason="capture-limit", flagged=0))

    def test_a_refused_call_is_not_a_call(self):
        cap = self.active_cap()
        cap.capture = True
        self.initialise(cap)
        cap.inflight = 4
        with self.assertRaisesRegex(proxy.ProxyError, "^request-limit$"):
            self.gateway.call(cap, rpc("tools/call", ident=30, name="rag_status", arguments={}))
        self.assertEqual((cap.events, cap.refused), ([], 1))


def stream(kind, **value):
    return json.dumps(dict(type="stream_event", event=dict(type=kind, **value))).encode()


def text_delta(text):
    return stream("content_block_delta", delta=dict(type="text_delta", text=text))


class Break(OfflineCase):
    def claude(self):
        protocol = native.ClaudeProtocol(self.root, "synthetic-model", "")
        protocol.stage = "running"
        return protocol

    def test_claude_marks_each_further_message(self):
        protocol = self.claude()
        self.assertEqual(protocol.events(stream("message_start", message={})), [])      # nothing said yet
        self.assertEqual(protocol.events(text_delta("Checking.")), [dict(type="delta", text="Checking.")])
        self.assertEqual(protocol.events(stream("content_block_stop", index=0)), [])
        self.assertEqual(protocol.events(stream("message_start", message={})), [dict(type="break")])
        self.assertEqual(protocol.events(text_delta("The answer.")), [dict(type="delta", text="The answer.")])

    def test_claude_without_partial_messages(self):
        protocol = self.claude()
        def assistant(text):
            return json.dumps(dict(type="assistant", message=dict(content=[dict(type="text", text=text)]))).encode()
        self.assertEqual(protocol.events(assistant("Checking.")), [dict(type="delta", text="Checking.")])
        self.assertEqual(protocol.events(assistant("The answer.")),
                         [dict(type="break"), dict(type="delta", text="The answer.")])
        tool = json.dumps(dict(type="assistant", message=dict(content=[
            dict(type="tool_use", name="mcp__deep32__rag_status")]))).encode()
        self.assertEqual(protocol.events(tool), [])

    def test_codex_marks_each_further_agent_message(self):
        protocol = native.CodexProtocol(self.root, None, None, "")
        protocol.thread = "synthetic-thread"
        def note(method, **params):
            return json.dumps(dict(method=method, params=dict(threadId="synthetic-thread", **params))).encode()
        started = note("item/started", item=dict(type="agentMessage"))
        self.assertEqual(protocol.events(started), [])
        self.assertEqual(protocol.events(note("item/agentMessage/delta", delta="Checking.")),
                         [dict(type="delta", text="Checking.")])
        self.assertEqual(protocol.events(note("item/completed", item=dict(type="agentMessage"))), [])
        self.assertEqual(protocol.events(note("item/started", item=dict(type="mcpToolCall", server="deep32",
                                                                    tool="rag_status"))), [])
        self.assertEqual(protocol.events(started), [dict(type="break")])


class Footer(Play):
    def supervisor(self, **options):
        self.agent, self.events, self.turns = Agent(), [], []
        supervisor = Supervisor(self.agent, self.gateway, lambda stream, event: self.events.append(event),
                                lambda ident: Scratch(self.root, ident), clock=self.clock,
                                on_turn=self.turns.append, **options)
        supervisor.open(1, "synthetic-1", request())
        return supervisor

    def turn(self, supervisor, tool_events, outputs, question=fx.QUESTION):
        supervisor.start(1, question)
        session = supervisor.sessions[1]
        self.play(session.capability, tool_events)
        for event in outputs:
            supervisor.output(1, session.epoch, event)
        return session

    def shown(self):
        return "".join(event["text"] for event in self.events if event["op"] == "agent.delta")

    def ops(self):
        return [event["op"] for event in self.events]

    def test_the_policy_names_two_values(self):
        self.assertEqual(self.supervisor(checks="footer").capture, True)
        self.assertEqual((self.supervisor().checks, self.supervisor().capture), ("off", False))
        with self.assertRaisesRegex(SessionError, "^invalid-policy$"):
            self.supervisor(checks="on")

    def test_off_is_the_stream_as_it_was(self):
        supervisor = self.supervisor()
        session = self.turn(supervisor, fx.clean_events(), [dict(type="delta", text=fx.CLEAN_ANSWER),
                                                           dict(type="final", text=fx.CLEAN_ANSWER), USAGE])
        self.assertEqual(self.ops(), ["session.opened", "agent.delta", "agent.final", "turn.completed"])
        self.assertEqual(self.shown(), fx.CLEAN_ANSWER)
        self.assertEqual(self.turns[0]["check"], "off")
        self.assertEqual(session.history, [fx.QUESTION, fx.CLEAN_ANSWER])

    def test_the_footer_follows_the_answer_in_the_stream_the_window_shows(self):
        supervisor = self.supervisor(checks="footer")
        session = self.turn(supervisor, fx.clean_events(), [dict(type="delta", text=fx.CLEAN_ANSWER),
                                                           dict(type="final", text=fx.CLEAN_ANSWER), USAGE])
        self.assertEqual(self.ops(), ["session.opened", "agent.delta", "agent.final", "agent.delta",
                                      "turn.completed"])
        self.assertEqual(self.shown(), fx.CLEAN_ANSWER + "\n\n" + CLEAN_FOOTER + "\n")
        final = [event["text"] for event in self.events if event["op"] == "agent.final"]
        self.assertEqual(final, [fx.CLEAN_ANSWER])                         # the answer itself is untouched
        self.assertEqual(session.history, [fx.QUESTION, fx.CLEAN_ANSWER])   # and so is what a follow-up is told
        self.assertEqual((self.turns[0]["check"], self.turns[0]["status"]), ("done", "completed"))
        for event in self.events:
            messages.validate_event(event, "synthetic-1")

    def test_flags_are_shown_and_nothing_is_held_back(self):
        supervisor = self.supervisor(checks="footer")
        self.turn(supervisor, fx.flagged_events(), [dict(type="delta", text=fx.FLAGGED_ANSWER),
                                                    dict(type="final", text=fx.FLAGGED_ANSWER), USAGE])
        self.assertEqual(self.shown(), fx.FLAGGED_ANSWER + "\n\n" + FLAGGED_FOOTER + "\n")
        self.assertEqual(supervisor.last_result["status"], "completed")

    def test_a_break_is_a_paragraph_break_before_the_next_text_only(self):
        supervisor = self.supervisor()
        self.turn(supervisor, [], [dict(type="break"), dict(type="delta", text="Checking the timers."),
                                   dict(type="break"), dict(type="break"), dict(type="delta", text="The "),
                                   dict(type="delta", text="answer."), dict(type="break"), USAGE])
        self.assertEqual(self.shown(), "Checking the timers.\n\nThe answer.")
        self.assertEqual(supervisor.sessions[1].history[-1], "Checking the timers.\n\nThe answer.")

    def test_a_break_adds_only_the_line_feeds_that_are_missing(self):
        for first, expected in (("One.\n", "One.\n\nTwo."), ("One.\n\n", "One.\n\nTwo."), ("One.", "One.\n\nTwo.")):
            with self.subTest(first=first):
                supervisor = self.supervisor()
                self.turn(supervisor, [], [dict(type="delta", text=first), dict(type="break"),
                                           dict(type="delta", text="Two."), USAGE])
                self.assertEqual(self.shown(), expected)
                self.gateway.capabilities.clear()

    def test_codex_has_no_final_text_and_its_last_message_is_the_answer(self):
        # the progress line names a value no tool returned; it is not part of the answer and is not checked
        supervisor = self.supervisor(checks="footer")
        self.turn(supervisor, fx.clean_events(), [dict(type="delta", text="I will wait 750 seconds for the tools."),
                                                 dict(type="break"), dict(type="delta", text=fx.CLEAN_ANSWER),
                                                 USAGE])
        self.assertEqual(self.shown(), "I will wait 750 seconds for the tools.\n\n" + fx.CLEAN_ANSWER + "\n\n"
                         + CLEAN_FOOTER + "\n")

    def test_an_answer_without_tool_calls_says_so(self):
        supervisor = self.supervisor(checks="footer")
        self.turn(supervisor, [], [dict(type="delta", text="Deep32 search is unavailable."), USAGE])
        self.assertTrue(self.shown().endswith("\n\n---\nChecks (by script, no model):\n- No tool call was recorded "
                                              "for this answer.\n- It contains no quotation, RFC number or other "
                                              "specific, so there was nothing to check.\n"))

    def test_a_check_that_breaks_leaves_the_answer_alone_and_one_log_line(self):
        supervisor = self.supervisor(checks="footer")
        for broken in (mock.patch.object(answer_check, "check", side_effect=RuntimeError("synthetic")),
                       mock.patch.object(answer_check, "check", return_value=dict(version=1, status="failed")),
                       mock.patch.object(answer_check, "footer", side_effect=RuntimeError("synthetic"))):
            self.events.clear()
            with broken:
                self.turn(supervisor, fx.clean_events(), [dict(type="delta", text=fx.CLEAN_ANSWER), USAGE])
            self.assertEqual(self.ops(), ["agent.delta", "turn.completed"])
            self.assertEqual(self.shown(), fx.CLEAN_ANSWER)
            self.assertEqual((self.turns[-1]["check"], self.turns[-1]["status"]), ("failed", "completed"))
        path = self.root / "events.jsonl"
        ledger = runtime.EventLedger(path, validate_owner=lambda: None, clock=lambda: 1)
        self.assertTrue(runtime.log_turn(ledger, self.turns[-1]))
        self.assertEqual([json.loads(line)["event"] for line in path.read_text(encoding="ascii").splitlines()],
                         ["turn-end", "answer-check"])

    def test_a_footer_that_would_pass_the_answer_bound_is_left_out(self):
        supervisor = self.supervisor(checks="footer")
        self.turn(supervisor, fx.clean_events(), [dict(type="delta", text="x" * (MAX_ANSWER - 100)), USAGE])
        self.assertEqual(self.ops(), ["session.opened"] + ["agent.delta"] * 128 + ["turn.completed"])
        self.assertEqual(supervisor.last_result["status"], "completed")
        self.assertEqual(self.turns[0]["check"], "done")

    def test_no_footer_for_a_turn_that_did_not_complete(self):
        supervisor = self.supervisor(checks="footer")
        self.turn(supervisor, fx.clean_events(), [dict(type="delta", text=fx.CLEAN_ANSWER)])
        supervisor.interrupt(1)
        self.assertEqual(self.ops(), ["session.opened", "agent.delta", "turn.completed"])
        self.assertEqual(self.turns[0]["check"], "off")

    def test_each_turn_keeps_its_own_calls(self):
        supervisor = self.supervisor(checks="footer")
        self.turn(supervisor, fx.clean_events(), [dict(type="delta", text=fx.CLEAN_ANSWER), USAGE])
        self.events.clear()
        self.turn(supervisor, [], [dict(type="delta", text=fx.CLEAN_ANSWER), USAGE], question="A follow-up.")
        self.assertIn("- No tool call was recorded for this answer, so none of it was retrieved or checked in this "
                      "turn.", self.shown())


class Setting(OfflineCase):
    def test_checks_are_off_until_the_package_switches_them_on(self):
        self.assertEqual(settings.load({}).checks, settings.CHECKS_DEFAULT)
        self.assertEqual(settings.load({"DEEP32_RELAY_CHECKS": "footer"}).checks, "footer")
        self.assertEqual(settings.load({"DEEP32_RELAY_CHECKS": "off"}).checks, "off")
        for text in ("on", "Footer", "FOOTER", "1", "true", "footer,off"):
            with self.subTest(text=text), self.assertRaises(Exception):
                settings.load({"DEEP32_RELAY_CHECKS": text})

    def test_the_bridge_passes_the_setting_on(self):
        import hashlib
        from deep32_relay.link import Link
        from deep32_relay.service import make_bridge
        der = b"synthetic certificate, no TLS socket"
        link = Link("synthetic-relay", "synthetic-node", hashlib.sha256(der).digest(), der, authenticated=True,
                    clock=self.clock)
        bridge = make_bridge(link, Agent(), lambda ident: Scratch(self.root, ident), clock=self.clock,
                             settings=settings.Settings(checks="footer"))
        self.assertEqual((bridge.supervisor.checks, bridge.supervisor.capture), ("footer", True))
