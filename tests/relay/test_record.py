"""The optional answer record: what answered, on which index, the tool calls' ids and the check's counts; no text.

Synthetic turns (answer_fixtures.py) and a fake-security owned folder; no agent, no network."""
import json
import time
from pathlib import Path
import sys
from unittest import mock

from support import Agent, Scratch, request
from test_footer import Play, USAGE
from test_models_r17 import OBSERVED, initialise, encoded
from test_native_r15 import MemoryProxy, claude_control, claude_init
import answer_fixtures as fx
from deep32_relay import answer_check, native, record, settings
from deep32_relay.sessions import Supervisor
from deep32_relay.state import OwnedRoot, OWNER_SCHEMA, StateError

NOW = 1795000000                    # a fixed wall clock
STAMP = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(NOW))
ABOUT = dict(version="2.1.326", model="claude-opus-synthetic-9", effort="default")


class Records(Play):
    def setUp(self):
        super().setUp()
        from test_install import FakeSecurity
        self.owner = OwnedRoot(self.root / "owned", security=FakeSecurity())
        self.owner.create()
        self.owner.write_json("owner.json", dict(schema=OWNER_SCHEMA, install_id="1" * 32))
        self.install = dict(agent="claude", release_id="2" * 64)
        self.relay_agent = type("Stand", (), dict(instruction_sha256="3" * 64))()

    def records(self, **options):
        return record.AnswerRecords(self.owner, self.install, self.relay_agent, wall_clock=lambda: NOW, **options)

    def files(self):
        folder = self.owner.root / "logs/answers"
        return sorted(item.name for item in folder.iterdir()) if folder.is_dir() else []

    def turn(self, on_answer, answer, tool_events, **options):
        self.agent, self.events = Agent(), []
        self.agent.start_hook = lambda handle: setattr(handle, "describe", lambda: dict(ABOUT))
        supervisor = Supervisor(self.agent, self.gateway, lambda stream, event: self.events.append(event),
                                lambda ident: Scratch(self.root, ident), clock=self.clock, on_answer=on_answer,
                                **options)
        supervisor.open(1, "synthetic-1", dict(request()))
        supervisor.start(1, fx.QUESTION)
        session = supervisor.sessions[1]
        self.play(session.capability, tool_events)
        for event in (dict(type="delta", text=answer), dict(type="final", text=answer), USAGE):
            supervisor.output(1, session.epoch, event)
        self.gateway.capabilities.clear()
        return supervisor

    def test_a_record_says_what_answered_and_holds_no_text(self):
        supervisor = self.turn(self.records().write, fx.FLAGGED_ANSWER, fx.flagged_events())
        self.assertEqual(supervisor.last_result["status"], "completed")
        self.assertEqual(self.files(), [STAMP + "-0001.json"])
        raw = (self.owner.root / "logs/answers" / self.files()[0]).read_bytes()
        value = json.loads(raw)
        expected_check = answer_check.redact(answer_check.check(fx.FLAGGED_ANSWER, fx.flagged_events(),
                                                               question=fx.QUESTION))
        self.assertEqual(value, {
            "schema": "deep32.relay-answer.v1",
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW)),
            "relay": {"version": "0.1.0", "release_id": "2" * 64},
            "agent": {"name": "claude", "version": "2.1.326", "model": "claude-opus-synthetic-9", "effort": "default",
                      "model_choice": "synthetic-model", "effort_choice": "synthetic-effort"},
            "instruction_sha256": "3" * 64,
            "index": {"generation": "g-synthetic-1", "corpus_fingerprint": "abc123def456"},
            "usage": {"input_tokens": 3, "output_tokens": 5},
            "calls": answer_check.summarize(fx.flagged_events()),
            "check": expected_check,
        })
        self.assertEqual((value["check"]["to_check"], len(value["calls"])), (10, 8))
        self.assertEqual(value["check"]["findings"][0], {"check": "quotations", "kind": "label-overstates", "line": 5})
        for fragment in (b"750", b"KEEPALIVE", b"hold timer", b"example timers", b"dampening", b"192.0.2.77",
                         b"default hold", b"excerpt"):
            self.assertNotIn(fragment, raw)

    def test_the_check_runs_for_the_record_even_when_the_footer_is_off(self):
        self.turn(self.records().write, fx.CLEAN_ANSWER, fx.clean_events())
        self.assertEqual([event["op"] for event in self.events],
                         ["session.opened", "agent.delta", "agent.final", "turn.completed"])
        value = json.loads((self.owner.root / "logs/answers" / self.files()[0]).read_bytes())
        self.assertEqual((value["check"]["status"], value["check"]["to_check"]), ("ok", 0))

    def test_footer_and_record_together(self):
        self.turn(self.records().write, fx.CLEAN_ANSWER, fx.clean_events(), checks="footer")
        self.assertEqual([event["op"] for event in self.events],
                         ["session.opened", "agent.delta", "agent.final", "agent.delta", "turn.completed"])
        self.assertEqual(len(self.files()), 1)

    def test_a_turn_without_rag_status_has_no_index(self):
        self.turn(self.records().write, "Answer.\n", [fx.verify(fx.Q_HOLD, "exact")])
        value = json.loads((self.owner.root / "logs/answers" / self.files()[0]).read_bytes())
        self.assertEqual((value["index"], value["calls"]),
                         (None, [dict(tool="verify_quote", ids=["rfc9999#3.1"], result="exact",
                                      local="rfc9999.txt:118")]))

    def test_the_newest_records_are_kept(self):
        clock = iter(range(NOW, NOW + 10))
        records = record.AnswerRecords(self.owner, self.install, self.relay_agent, wall_clock=lambda: next(clock),
                                       keep=3)
        names = [records.write(dict(calls=[], check=None)) for _ in range(5)]
        self.assertTrue(all(record.NAME.fullmatch(Path(name).name) for name in names))
        self.assertEqual(self.files(), [Path(name).name for name in names[2:]])
        self.assertEqual(record.KEEP, 200)

    def test_only_its_own_files_are_ever_deleted(self):
        self.owner.mkdir("logs/answers")
        self.owner.write("logs/answers/notes.txt", b"synthetic\n")
        records = self.records(keep=1)
        records.write(dict(calls=[]))
        records.write(dict(calls=[]))
        self.assertEqual(self.files(), [STAMP + "-0002.json", "notes.txt"])

    def test_a_record_that_cannot_be_written_never_fails_the_answer(self):
        records = self.records()
        self.assertIsNone(records.write(dict(calls=[dict(tool="x", ids=["i" * 2000000])])))     # over the file limit
        self.assertIsNone(records.write(dict(calls=object())))
        self.assertIsNone(records.write(None))
        with mock.patch.object(self.owner, "write_json", side_effect=StateError("synthetic")):
            supervisor = self.turn(records.write, fx.CLEAN_ANSWER, fx.clean_events())
        self.assertEqual((supervisor.last_result["status"], self.files()), ("completed", []))

        def broken(turn):
            raise RuntimeError("synthetic")
        supervisor = self.turn(broken, fx.CLEAN_ANSWER, fx.clean_events())
        self.assertEqual(supervisor.last_result["status"], "completed")

    def test_nothing_is_kept_or_written_without_the_setting(self):
        supervisor = self.turn(None, fx.CLEAN_ANSWER, fx.clean_events())
        self.assertEqual((supervisor.capture, self.files()), (False, []))
        self.assertFalse((self.owner.root / "logs").exists())

    def test_the_setting(self):
        self.assertFalse(settings.load({}).answer_record)
        self.assertTrue(settings.load({"DEEP32_RELAY_ANSWER_RECORD": "on"}).answer_record)
        self.assertFalse(settings.load({"DEEP32_RELAY_ANSWER_RECORD": "off"}).answer_record)
        for text in ("1", "true", "On", "yes"):
            with self.subTest(text=text), self.assertRaisesRegex(StateError, "^invalid-relay-setting$"):
                settings.load({"DEEP32_RELAY_ANSWER_RECORD": text})


class Describe(Play):
    def session(self, protocol):
        plan = native.LaunchPlan((str(Path(sys.executable).resolve()),), self.root, {})
        return native.NativeSession(plan, protocol, object(), MemoryProxy(), live=lambda: True)

    def test_claude_reports_the_model_its_init_event_named(self):
        protocol = native.ClaudeProtocol(self.root, "default", "", effort="high")
        initialise(protocol, OBSERVED)
        protocol.replies()
        protocol.attest(encoded(claude_control("relay-mcp-status", dict(mcpServers=[dict(name="deep32",
                                                                                         status="connected")]))))
        protocol.turn("synthetic question", ())
        self.assertEqual(self.session(protocol).describe(), dict(version="2.1.326", model="default", effort="high"))
        event = claude_init(self.root)
        event["model"] = "claude-opus-synthetic-9"
        protocol.attest_turn(encoded(event))
        self.assertEqual(self.session(protocol).describe(),
                         dict(version="2.1.326", model="claude-opus-synthetic-9", effort="high"))

    def test_codex_reports_the_model_its_thread_started_with(self):
        protocol = native.CodexProtocol(self.root, None, None, "")
        protocol.version, protocol.resolved_model, protocol.effort = "synthetic-codex/999", "gpt-synthetic-9", "medium"
        self.assertEqual(self.session(protocol).describe(),
                         dict(version="synthetic-codex/999", model="gpt-synthetic-9", effort="medium"))

    def test_a_protocol_that_knows_nothing_gives_empty_strings(self):
        self.assertEqual(self.session(object()).describe(), dict(version="", model="", effort=""))
