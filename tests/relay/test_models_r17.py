"""R17: each Ask session chooses its model and effort from the lists the relay advertises.

The model list is the one Claude Code 2.1.326 returned at initialize on the lab headend (2026-11-20) for the relay
profile, with its descriptions and display names left out; the values, resolved models and effort fields are as
observed. Claude Code 2.1.326 --help lists --effort low, medium, high, xhigh and max.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import unittest

from support import OfflineCase, Agent, Scratch, request, ROOT
from test_native_r15 import MemoryProxy, claude_control
from deep32_relay import engine, messages, native, selftest, status
from deep32_relay.daemon import RelayReactor
from deep32_relay.link import Link
from deep32_relay.sessions import Supervisor
from deep32_relay.state import StateError
from deep32_relay.wire import Error, Frame, Type, END_MESSAGE

LEVELS = ['low', 'medium', 'high', 'xhigh', 'max']
RESOLVED = dict(default='claude-opus-5-5', opus='claude-opus-5-5', fable='claude-fable-5-2',
                sonnet='claude-sonnet-5-5', haiku='claude-haiku-5-5')
OBSERVED = [dict(value=name, resolvedModel=resolved, supportsEffort=True, supportedEffortLevels=list(LEVELS),
                 supportsAdaptiveThinking=True) for name, resolved in RESOLVED.items()]
MODELS = ('default', 'opus', 'fable', 'sonnet', 'haiku')
EFFORTS = ('default', 'low', 'medium', 'high', 'xhigh', 'max')


def load_bridge():
    """The r16 d32-ask bridge (disc 1, unchanged here): the rules it applies to a relay's advertised lists."""
    path = ROOT / 'src/workstation/ask-client/bridge/d32-ask'
    loader = importlib.machinery.SourceFileLoader('relay_models_bridge', str(path))
    spec = importlib.util.spec_from_loader('relay_models_bridge', loader)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


BRIDGE = load_bridge()


def encoded(value):
    return json.dumps(value).encode()


def initialisation(models):
    return dict(claude_code_version='2.1.326', current_permission_mode='dontAsk',
                account=dict(uuid='synthetic-account', organization='synthetic'), models=models)


def initialise(protocol, models=OBSERVED):
    return protocol.attest(encoded(claude_control('relay-init', initialisation(models))))


def record(**changes):
    value = dict(schema=selftest.ATTEST_SCHEMA, agent='claude', platform=selftest.platform_name(),
                 binary_sha256='1' * 64, version='2.1.326', binding_sha256='2' * 64, state='qualified',
                 reason='none', model_usage='none', model='default', effort='default',
                 models=list(MODELS), efforts=list(EFFORTS))
    value.update(changes)
    return value


class Advertisement(OfflineCase):
    def test_initialize_lists_the_aliases_and_the_effort_levels(self):
        protocol = native.ClaudeProtocol(self.root, None, '', install_check=True)
        self.assertIsNone(initialise(protocol))
        self.assertEqual((protocol.model, protocol.effort), ('default', 'default'))
        self.assertEqual(protocol.choices, (MODELS, EFFORTS))
        self.assertNotIn('synthetic-account', repr(protocol.choices))

    def test_only_short_aliases_and_levels_every_offered_model_lists(self):
        models = [dict(item) for item in OBSERVED]
        models[4]['supportedEffortLevels'] = ['low', 'medium', 'high', 'ultra']
        for item in models:
            item['supportedEffortLevels'] = item['supportedEffortLevels'] + ['ultra']
        models += [dict(value='opus[1m]', supportsEffort=True, supportedEffortLevels=LEVELS),
                   dict(value='claude-opus-5-5', supportsEffort=True, supportedEffortLevels=LEVELS),
                   dict(value='Sonnet', supportsEffort=True, supportedEffortLevels=LEVELS)]
        protocol = native.ClaudeProtocol(self.root, None, '', install_check=True)
        initialise(protocol, models)
        # A level Claude Code's --effort does not take (ultra) is never offered; haiku limits the rest.
        self.assertEqual(protocol.choices, (MODELS, ('default', 'low', 'medium', 'high')))

    def test_a_model_without_effort_support_leaves_the_default_effort_only(self):
        for change in (dict(supportsEffort=False), dict(supportedEffortLevels=None), dict(supportedEffortLevels='max')):
            models = [dict(item) for item in OBSERVED]
            models[2].update(change)
            protocol = native.ClaudeProtocol(self.root, None, '', install_check=True)
            initialise(protocol, models)
            with self.subTest(change=change):
                self.assertEqual(protocol.choices, (MODELS, ('default',)))

    def test_an_unusual_first_model_keeps_the_qualified_pair_alone(self):
        models = [dict(OBSERVED[1], value='claude-opus-5-5')] + OBSERVED[1:]
        protocol = native.ClaudeProtocol(self.root, None, '', install_check=True)
        initialise(protocol, models)
        self.assertEqual(protocol.model, 'claude-opus-5-5')
        self.assertIsNone(protocol.choices)

    def test_the_lists_stop_at_sixteen(self):
        names = ['default'] + ['model' + chr(ord('a') + index) for index in range(20)]
        models = [dict(OBSERVED[0], value=name) for name in names]
        protocol = native.ClaudeProtocol(self.root, None, '', install_check=True)
        initialise(protocol, models)
        self.assertEqual(protocol.choices[0], tuple(names[:16]))
        self.assertEqual(protocol.choices[1], EFFORTS)

    def test_attestation_record_carries_valid_lists_only(self):
        self.assertEqual(selftest.validate_attestation(record())['models'], list(MODELS))
        plain = record()
        del plain['models'], plain['efforts']
        selftest.validate_attestation(plain)
        for changes in (dict(agent='codex'), dict(models=['opus', 'default']), dict(efforts=['low', 'default']),
                        dict(efforts=list(EFFORTS) + ['ultra']), dict(models=list(MODELS) + ['opus']),
                        dict(models=['default'] + ['model' + chr(ord('a') + i) for i in range(16)]),
                        dict(models=['default', 'Opus']), dict(models=['default', 'opus[1m]']),
                        dict(models=[]), dict(efforts='default'), dict(efforts=['default', 7]),
                        dict(state='unqualified', reason='native-startup-unverified')):
            with self.subTest(changes=changes), self.assertRaises(StateError):
                selftest.validate_attestation(record(**changes))
        half = record()
        del half['efforts']
        with self.assertRaises(StateError):
            selftest.validate_attestation(half)

    def install(self, evidence):
        from test_install import FakeSecurity
        from deep32_relay.state import OwnedRoot, OWNER_SCHEMA
        owner = OwnedRoot(self.root / 'owned', security=FakeSecurity())
        owner.create()
        owner.write_json('owner.json', dict(schema=OWNER_SCHEMA, install_id='1' * 32))
        owner.mkdir('profiles/claude')
        release = '2' * 64
        install = dict(agent='claude', install_id='1' * 32, release_id=release, profile_alias='relay-claude',
                       agent_binary=str(Path(sys.executable).resolve()),
                       owned_files={'packages/' + release + '/RESEARCH.md': '3' * 64,
                                    'packages/' + release + '/deep32_relay/native.py': '4' * 64})
        result = selftest.qualify_install(owner, install, executable=sys.executable,
                                          checker=lambda *args, **kwargs: dict(evidence))
        return owner, install, result

    def test_install_check_records_the_lists_and_status_reports_them(self):
        owner, install, result = self.install(dict(version='2.1.326', model='default', effort='default',
                                                   models=list(MODELS), efforts=list(EFFORTS)))
        self.assertEqual((result['state'], result['models'], result['efforts']), ('qualified', list(MODELS), list(EFFORTS)))
        self.assertEqual(owner.read_json('private/qualification/claude.json'), result)
        self.assertEqual(selftest.advertised_choices(owner, install),
                         dict(models=list(MODELS), efforts=list(EFFORTS)))

    def test_install_check_without_lists_offers_the_qualified_pair(self):
        owner, install, result = self.install(dict(version='2.1.326', model='default', effort='default'))
        self.assertNotIn('models', result)
        self.assertEqual(selftest.advertised_choices(owner, install), dict(models=['default'], efforts=['default']))

    def test_install_check_with_bad_lists_is_unqualified(self):
        owner, install, result = self.install(dict(version='2.1.326', model='default', effort='default',
                                                   models=['default', 'opus'], efforts=['default', 'ultra']))
        self.assertEqual((result['state'], result['model']), ('unqualified', ''))
        self.assertNotIn('models', result)
        self.assertIsNone(selftest.advertised_choices(owner, install))

    def test_native_agent_advertises_the_recorded_lists(self):
        binary = Path(sys.executable).resolve()
        agent = engine.NativeAgent('claude', self.root, executable=binary, qualification=record())
        self.assertEqual((agent.models, agent.efforts), (MODELS, EFFORTS))
        self.assertTrue(agent.accepts('haiku', 'xhigh'))
        self.assertFalse(agent.accepts('claude-haiku-5-5', 'xhigh'))
        self.assertFalse(agent.accepts('haiku', 'ultra'))
        plain = record()
        del plain['models'], plain['efforts']
        agent = engine.NativeAgent('claude', self.root, executable=binary, qualification=plain)
        self.assertEqual((agent.models, agent.efforts), (('default',), ('default',)))
        codex = record(agent='codex', model='gpt-synthetic', effort='medium')
        agent = engine.NativeAgent('codex', self.root, executable=binary, qualification=codex)
        self.assertEqual((agent.models, agent.efforts), (('gpt-synthetic',), ('medium',)))
        agent = engine.NativeAgent('claude', self.root, executable=binary, qualification=record(state='unqualified'))
        self.assertEqual((agent.ready, agent.models, agent.efforts), (False, (), ()))

    def test_every_advertised_pair_fits_the_workstation(self):
        agent = engine.NativeAgent('claude', self.root, executable=Path(sys.executable).resolve(),
                                   qualification=record())
        profile = dict(name='research', models=list(agent.models), efforts=list(agent.efforts))
        hello = messages.hello('synthetic-relay', bytes(32), (profile,))
        self.assertLessEqual(len(messages.dumps(hello)), 16384)
        # asklink msg.c keeps 16 entries of at most 64 bytes; the r16 window takes efforts of at most 8.
        self.assertLessEqual(max(len(agent.models), len(agent.efforts)), 16)
        rows = list(dict.fromkeys(('relay', model, effort) for model in agent.models for effort in agent.efforts
                                  if BRIDGE.relay_choice('relay', model, effort)))[:128]
        self.assertEqual(len(rows), len(MODELS) * len(EFFORTS))
        # The bridge marks its first row preferred: the qualified pair stays preselected.
        self.assertEqual(rows[0], ('relay', 'default', 'default'))


class Choosing(Agent):
    models = MODELS
    efforts = ('default', 'low', 'high')


class SessionChoice(OfflineCase):
    def supervisor(self, agent):
        self.events = []
        return Supervisor(agent, self.gateway, lambda stream, event: self.events.append(event),
                          lambda ident: Scratch(self.root, ident), clock=self.clock)

    def complete(self, supervisor, agent, stream):
        agent.handles[-1].events = [dict(type='delta', text='synthetic answer'),
                                    dict(type='completed', usage=dict(input_tokens=2, output_tokens=3))]
        supervisor.tick()
        self.assertEqual(supervisor.sessions[stream].state, 'idle')

    def test_a_listed_pair_reaches_the_agent_for_every_turn(self):
        agent = Choosing()
        supervisor = self.supervisor(agent)
        self.assertTrue(supervisor.open(1, 'synthetic-1', dict(request(), model='haiku', effort='high')))
        for turn in range(2):
            supervisor.start(1, 'synthetic question')
            self.complete(supervisor, agent, 1)
        self.assertEqual(agent.choices, [('haiku', 'high'), ('haiku', 'high')])
        self.assertEqual(supervisor.last_result, dict(status='completed', reason='none', usage_known=True,
                                                      model='haiku', effort='high'))

    def test_each_session_keeps_its_own_pair(self):
        agent = Choosing()
        supervisor = self.supervisor(agent)
        supervisor.open(1, 'synthetic-1', dict(request(), model='opus', effort='low'))
        supervisor.start(1, 'synthetic question')
        self.complete(supervisor, agent, 1)
        supervisor.close(1)
        supervisor.open(3, 'synthetic-3', dict(request(), model='sonnet', effort='default'))
        supervisor.start(3, 'synthetic question')
        self.assertEqual(agent.choices, [('opus', 'low'), ('sonnet', 'default')])

    def test_the_first_advertised_pair_is_the_default(self):
        # asklink link.c sends the first advertised model and effort when the Ask client names none.
        agent = Choosing()
        supervisor = self.supervisor(agent)
        supervisor.open(1, 'synthetic-1', dict(request(), model=agent.models[0], effort=agent.efforts[0]))
        supervisor.start(1, 'synthetic question')
        self.assertEqual(agent.choices, [('default', 'default')])

    def test_an_unlisted_pair_is_refused_before_any_agent_work(self):
        for model, effort in (('claude-opus-5-5', 'high'), ('opus', 'xhigh'), ('opus', 'ultra'),
                              ('OPUS', 'high'), ('opus', ''), ('', 'default')):
            agent = Choosing()
            supervisor = self.supervisor(agent)
            with self.subTest(model=model, effort=effort):
                self.assertFalse(supervisor.open(1, 'synthetic-1', dict(request(), model=model, effort=effort)))
                self.assertEqual(self.events, [dict(op='error', session='synthetic-1', code=int(Error.PROTOCOL))])
                self.assertEqual((supervisor.sessions, agent.starts, agent.verifies), ({}, [], []))
        for missing in ('model', 'effort'):
            agent = Choosing()
            supervisor = self.supervisor(agent)
            value = request()
            del value[missing]
            with self.subTest(missing=missing):
                self.assertFalse(supervisor.open(1, 'synthetic-1', value))
                self.assertEqual(self.events[0]['code'], int(Error.PROTOCOL))


class AgentLaunch(OfflineCase):
    def plan(self, model, effort, name):
        scratch = self.root / name
        scratch.mkdir()
        profile = self.root / 'profile'
        profile.mkdir(exist_ok=True)
        return native.ClaudeCodeLauncher().prepare(sys.executable, scratch, profile, endpoint=MemoryProxy.url,
            bearer='SYNTHETIC-BEARER', model=model, effort=effort, platform=selftest.platform_name())

    def test_argv_carries_the_chosen_model_and_effort_and_nothing_else_changes(self):
        base, _ = self.plan('default', 'default', 'base')
        chosen, protocol = self.plan('haiku', 'xhigh', 'chosen')
        self.assertEqual(base.argv[-2:], ('--model', 'default'))
        self.assertNotIn('--effort', base.argv)
        self.assertEqual(chosen.argv[-4:], ('--model', 'haiku', '--effort', 'xhigh'))
        # The tool sandbox is the same argv; only the scratch paths differ.
        strip = lambda plan: tuple(arg.replace(str(plan.cwd), '<scratch>') for arg in plan.argv[:-2])
        self.assertEqual(strip(base), strip(chosen)[:-2])
        self.assertEqual((protocol.model, protocol.effort), ('haiku', 'xhigh'))
        self.assertEqual(set(base.environment), set(chosen.environment))
        self.assertFalse(any('haiku' in value or 'xhigh' in value for value in chosen.environment.values()))
        plain, protocol = self.plan('default', None, 'plain')
        self.assertNotIn('--effort', plain.argv)
        self.assertEqual(protocol.effort, 'default')

    def test_an_effort_the_cli_does_not_take_never_reaches_argv(self):
        for effort in ('ultra', 'High', 'max ', 'minimal'):
            with self.subTest(effort=effort), self.assertRaisesRegex(native.NativeError, '^native-plan-incomplete$'):
                self.plan('opus', effort, 'bad-' + hashlib.sha256(effort.encode()).hexdigest()[:8])

    def test_turn_startup_refuses_an_effort_the_model_no_longer_lists(self):
        models = [dict(item) for item in OBSERVED]
        models[4]['supportedEffortLevels'] = ['low', 'medium', 'high']
        protocol = native.ClaudeProtocol(self.root, 'haiku', '', effort='max')
        with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
            initialise(protocol, models)
        protocol = native.ClaudeProtocol(self.root, 'opus', '', effort='max')
        self.assertIsNone(initialise(protocol, models))
        self.assertEqual(protocol.stage, 'mcp-status')

    def test_turn_init_must_report_the_chosen_model_family(self):
        protocol = native.ClaudeProtocol(self.root, 'haiku', '', effort='low')
        initialise(protocol)
        protocol.replies()
        self.assertTrue(protocol.attest(encoded(claude_control('relay-mcp-status', dict(
            mcpServers=[dict(name='deep32', status='connected')])))))
        protocol.turn('synthetic question', ())
        from test_native_r15 import claude_init
        event = claude_init(self.root)
        event['model'] = 'claude-opus-5-5'
        with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
            protocol.attest_turn(encoded(event))

    def agent(self, launches):
        binary = Path(sys.executable).resolve()
        profile = self.root / 'profile'
        profile.mkdir(exist_ok=True)
        class Tree:
            def start(inner, argv, **kwargs):
                launches.append((tuple(argv), dict(kwargs['env'])))
                raise RuntimeError('synthetic launch stop')
            def cancel(inner):
                return True
        return engine.NativeAgent('claude', profile, executable=binary,
            qualification=record(binary_sha256=selftest.binary_digest(binary)), gateway=self.gateway,
            tree_factory=lambda platform: Tree(), proxy_factory=MemoryProxy)

    def test_native_agent_launches_with_the_session_choice(self):
        launches = []
        agent = self.agent(launches)
        for index, (choice, flags) in enumerate((
                (dict(model='fable', effort='medium'), ('--model', 'fable', '--effort', 'medium')),
                (dict(), ('--model', 'default')))):
            scratch = self.root / ('turn-%d' % index)
            scratch.mkdir()
            cap = self.active_cap('synthetic-%d' % index)
            with self.subTest(choice=choice), self.assertRaisesRegex(engine.EngineError, '^native-startup-unverified$'):
                agent.start('synthetic question', (), scratch, cap, **choice)
            argv, environment = launches[-1]
            self.assertEqual(argv[-len(flags):], flags)
            self.assertEqual(argv.count('--effort'), 1 if 'effort' in choice else 0)
            self.assertNotIn('synthetic question', ' '.join(argv) + json.dumps(environment))
        self.assertEqual(len(launches), 2)

    def test_native_agent_refuses_an_unlisted_pair_before_launch(self):
        launches = []
        agent = self.agent(launches)
        for model, effort in (('claude-opus-5-5', 'high'), ('opus', 'ultra'), ('opus', None)):
            scratch = self.root / ('refused-' + str(model) + '-' + str(effort))
            scratch.mkdir()
            cap = self.active_cap('synthetic-refused')
            with self.subTest(model=model, effort=effort), self.assertRaisesRegex(engine.EngineError, '^native-agent-unqualified$'):
                agent.start('synthetic question', (), scratch, cap, model=model, effort=effort if effort else '')
            self.gateway.capabilities.clear()
        self.assertEqual(launches, [])


class Listener:
    peer = object()
    def start(self):
        pass
    def poll(self, timeout):
        pass
    def close(self):
        pass


class StatusRecord(OfflineCase):
    def snapshot(self, result):
        value = status.unavailable_snapshot('a' * 32, 'claude', 'b' * 64, now=100)
        value['last_session_result'] = result
        return value

    def test_status_keeps_the_last_sessions_model_and_effort(self):
        status.validate_snapshot(self.snapshot(dict(state='completed', reason='none', observed_at=99,
                                                    model='haiku', effort='xhigh')))
        for result in (dict(state='completed', reason='none', observed_at=99, model='haiku'),
                       dict(state='completed', reason='none', observed_at=99, model='haiku please', effort='low'),
                       dict(state='completed', reason='none', observed_at=99, model='x' * 65, effort='low'),
                       dict(state='completed', reason='none', observed_at=99, model='haiku', effort=3),
                       dict(state='none', reason='none', observed_at=0, model='haiku', effort='low')):
            with self.subTest(result=result), self.assertRaises(StateError):
                status.validate_snapshot(self.snapshot(result))

    def test_reactor_publishes_the_chosen_pair_after_a_turn(self):
        agent = Choosing()
        snapshots = []
        reactor = RelayReactor(Listener(), agent, lambda ident: Scratch(self.root, ident), clock=self.clock,
            wall_clock=self.clock, publish=snapshots.append, identity_observer=lambda pid: 'windows-' + '1' * 16,
            install=dict(install_id='a' * 32, agent='claude', release_id='b' * 64, capability_id='unqualified'))
        self.addCleanup(reactor.close)
        reactor.start()
        der = b'synthetic certificate, no TLS socket'
        link = Link('synthetic-relay', 'synthetic-node', hashlib.sha256(der).digest(), der,
                    authenticated=True, clock=self.clock)
        reactor.listener.on_link(link)
        def feed(kind, value=None, stream=0, channel=0):
            payload = messages.dumps(value) if value is not None else b''
            link.feed(Frame(kind, stream, channel, payload, END_MESSAGE if kind == Type.DATA else 0).encode())
        feed(Type.HELLO, dict(proto='D32U/1', id='synthetic-node', version=1, versions=[1], role='node',
                              features=['research-v1'], nonce='11' * 32, sessions=[]))
        feed(Type.HELLO_ACK, dict(version=1, features=['research-v1'], paired=True))
        feed(Type.OPEN, {'session': 'synthetic-session'}, 1, 1)
        feed(Type.DATA, dict(request(), model='sonnet', effort='low'), 1, 1)
        feed(Type.DATA, {'op': 'turn.start', 'text': 'synthetic question'}, 1, 1)
        reactor.jobs.join()
        agent.handles[0].events = [dict(type='delta', text='synthetic answer'),
                                   dict(type='completed', usage=dict(input_tokens=2, output_tokens=3))]
        reactor.step(0)
        reactor._publish(force=True)
        result = snapshots[-1]['last_session_result']
        self.assertEqual((result['state'], result['model'], result['effort']), ('completed', 'sonnet', 'low'))
        self.assertNotIn('synthetic', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
