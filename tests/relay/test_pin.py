"""A pinned model and effort: what the default choice of an Ask conversation runs when the operator sets
DEEP32_RELAY_MODEL and DEEP32_RELAY_EFFORT. Scripted initialize and init events; no agent is started."""
from pathlib import Path
import sys

from support import OfflineCase
from test_models_r17 import OBSERVED, MODELS, EFFORTS, initialise, encoded, record
from test_native_r15 import MemoryProxy, claude_control, claude_init
from deep32_relay import engine, native, selftest, settings
from deep32_relay.state import StateError

PIN = 'claude-synthetic-9-9'           # a full model id that Claude Code's initialize does not list by a short name


class Loading(OfflineCase):
    def test_a_full_model_id_and_an_effort_are_read(self):
        value = settings.load({'DEEP32_RELAY_MODEL': PIN, 'DEEP32_RELAY_EFFORT': 'high'}, 'claude')
        self.assertEqual((value.model, value.effort), (PIN, 'high'))
        self.assertEqual((settings.load({}, 'claude').model, settings.load({}, 'claude').effort), ('', ''))
        for model in ('claude-synthetic-9-9-20990101', 'claude-synthetic-9-9[1m]', 'gpt-synthetic-9.9-codex', 'us.vendor.synthetic-x:0',
                      'vendor/model-1'):
            with self.subTest(model=model):
                self.assertEqual(settings.load({'DEEP32_RELAY_MODEL': model}).model, model)

    def test_a_model_that_could_be_read_as_an_option_or_holds_a_space_is_refused(self):
        for model in ('-opus', '--model', 'two words', 'a' * 129, 'model;rm', 'model"x', '$(model)', 'mod\u00e8le'):
            with self.subTest(model=model), self.assertRaisesRegex(StateError, '^invalid-relay-setting$'):
                settings.load({'DEEP32_RELAY_MODEL': model})

    def test_the_effort_is_one_the_agent_takes(self):
        for effort in native.CLAUDE_EFFORTS:
            self.assertEqual(settings.load({'DEEP32_RELAY_EFFORT': effort}, 'claude').effort, effort)
        for effort in ('default', 'ultra', 'High', 'minimal', 'x-high', '3'):
            with self.subTest(effort=effort), self.assertRaisesRegex(StateError, '^invalid-relay-setting$'):
                settings.load({'DEEP32_RELAY_EFFORT': effort}, 'claude')
        self.assertEqual(settings.load({'DEEP32_RELAY_EFFORT': 'minimal'}, 'codex').effort, 'minimal')
        for effort in ('default', 'High', 'x-high', 'a' * 17):
            with self.subTest(effort=effort), self.assertRaisesRegex(StateError, '^invalid-relay-setting$'):
                settings.load({'DEEP32_RELAY_EFFORT': effort}, 'codex')


class Launch(OfflineCase):
    def agent(self, launches, **pin):
        binary = Path(sys.executable).resolve()
        profile = self.root / 'profile'
        profile.mkdir(exist_ok=True)

        class Tree:
            def start(inner, argv, **kwargs):
                launches.append(tuple(argv))
                raise RuntimeError('synthetic launch stop')
            def cancel(inner):
                return True
        agent = engine.NativeAgent('claude', profile, executable=binary,
                                   qualification=record(binary_sha256=selftest.binary_digest(binary)),
                                   gateway=self.gateway, tree_factory=lambda platform: Tree(), proxy_factory=MemoryProxy)
        agent.pin_model, agent.pin_effort = pin.get('model', ''), pin.get('effort', '')
        return agent

    def argv(self, agent, launches, name, **choice):
        scratch = self.root / name
        scratch.mkdir()
        cap = self.active_cap('synthetic-' + name)
        with self.assertRaisesRegex(engine.EngineError, '^native-startup-unverified$'):
            agent.start('synthetic question', (), scratch, cap, **choice)
        self.gateway.capabilities.clear()
        return launches[-1]

    def test_the_default_choice_runs_the_pin(self):
        launches = []
        agent = self.agent(launches, model=PIN, effort='high')
        argv = self.argv(agent, launches, 'default')
        self.assertEqual(argv[-4:], ('--model', PIN, '--effort', 'high'))
        argv = self.argv(agent, launches, 'named-default', model='default', effort='default')
        self.assertEqual(argv[-4:], ('--model', PIN, '--effort', 'high'))
        # the lists the Workstation is offered are what they were
        self.assertEqual((agent.models, agent.efforts), (MODELS, EFFORTS))

    def test_a_choice_made_by_name_keeps_its_meaning(self):
        launches = []
        agent = self.agent(launches, model=PIN, effort='high')
        self.assertEqual(self.argv(agent, launches, 'both', model='haiku', effort='low')[-4:],
                         ('--model', 'haiku', '--effort', 'low'))
        self.assertEqual(self.argv(agent, launches, 'model', model='haiku', effort='default')[-4:],
                         ('--model', 'haiku', '--effort', 'high'))
        self.assertEqual(self.argv(agent, launches, 'effort', model='default', effort='max')[-4:],
                         ('--model', PIN, '--effort', 'max'))

    def test_one_of_the_two_may_be_pinned_alone(self):
        launches = []
        argv = self.argv(self.agent(launches, model=PIN), launches, 'model-only')
        self.assertEqual(argv[-2:], ('--model', PIN))
        self.assertNotIn('--effort', argv)
        argv = self.argv(self.agent(launches, effort='xhigh'), launches, 'effort-only')
        self.assertEqual(argv[-4:], ('--model', 'default', '--effort', 'xhigh'))

    def test_without_a_pin_the_launch_is_what_it_was(self):
        launches = []
        argv = self.argv(self.agent(launches), launches, 'plain')
        self.assertEqual(argv[-2:], ('--model', 'default'))
        self.assertNotIn('--effort', argv)

    def test_a_pin_never_widens_what_a_session_may_ask_for(self):
        launches = []
        agent = self.agent(launches, model=PIN, effort='high')
        for model, effort in ((PIN, 'default'), ('default', 'ultra')):
            scratch = self.root / ('refused-' + effort)
            scratch.mkdir()
            cap = self.active_cap('synthetic-refused')
            with self.assertRaisesRegex(engine.EngineError, '^native-agent-unqualified$'):
                agent.start('synthetic question', (), scratch, cap, model=model, effort=effort)
            self.gateway.capabilities.clear()
        self.assertEqual(launches, [])


class Attestation(OfflineCase):
    def started(self, model, effort='default', pinned=True, models=OBSERVED):
        protocol = native.ClaudeProtocol(self.root, model, '', effort=effort, pinned=pinned)
        initialise(protocol, models)
        protocol.replies()
        protocol.attest(encoded(claude_control('relay-mcp-status', dict(mcpServers=[dict(name='deep32', status='connected')]))))
        protocol.turn('synthetic question', ())
        return protocol

    def init(self, model):
        event = claude_init(self.root)
        event['model'] = model
        return encoded(event)

    def test_a_pinned_id_need_not_be_listed_and_must_be_the_model_that_starts(self):
        protocol = self.started(PIN)
        self.assertEqual((protocol.model, protocol.choices), (PIN, None))
        self.assertTrue(protocol.attest_turn(self.init(PIN)))
        for other in ('claude-opus-synthetic-9', PIN + '-20990101', 'default', ''):
            with self.subTest(other=other), self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
                self.started(PIN).attest_turn(self.init(other))

    def test_an_unlisted_model_that_is_not_a_pin_is_still_refused(self):
        with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
            initialise(native.ClaudeProtocol(self.root, PIN, ''))

    def test_the_effort_is_checked_against_the_alias_that_resolves_to_the_pin(self):
        models = [dict(item) for item in OBSERVED]
        models[4]['supportedEffortLevels'] = ['low', 'medium', 'high']          # haiku: claude-haiku-5-5
        with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
            initialise(native.ClaudeProtocol(self.root, 'claude-haiku-5-5', '', effort='max', pinned=True), models)
        protocol = native.ClaudeProtocol(self.root, 'claude-haiku-5-5', '', effort='high', pinned=True)
        self.assertIsNone(initialise(protocol, models))
        self.assertEqual(protocol.stage, 'mcp-status')
        # an id that no alias resolves to has no list to check: Claude Code's own --effort levels are the limit
        protocol = native.ClaudeProtocol(self.root, PIN, '', effort='max', pinned=True)
        self.assertIsNone(initialise(protocol, models))

    def test_a_short_name_given_as_the_pin_is_treated_as_that_name(self):
        protocol = self.started('opus')
        self.assertEqual(protocol.choices[0][0], 'opus')
        self.assertTrue(protocol.attest_turn(self.init('claude-opus-synthetic-9')))

    def test_codex_takes_the_pin_through_its_configuration(self):
        (self.root / 'synthetic-agent.exe').write_bytes(b'synthetic executable')
        profile = self.root / 'profile'
        profile.mkdir()
        plan, protocol = native.CodexLauncher().prepare(self.root / 'synthetic-agent.exe', self.root, profile,
            endpoint=MemoryProxy.url, bearer='SYNTHETIC-BEARER', model='gpt-synthetic-9', effort='high',
            platform='windows', pinned=True)
        self.assertIn('model="gpt-synthetic-9"', plan.argv)
        self.assertIn('model_reasoning_effort="high"', plan.argv)
        self.assertEqual((protocol.model, protocol.effort), ('gpt-synthetic-9', 'high'))
