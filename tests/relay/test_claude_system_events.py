"""Finding 22: unsolicited system events never bypass either Claude gate."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest

from support import OfflineCase
from test_native_r15 import MemoryProxy, claude_control, claude_init, claude_stage1
from test_native_r16 import initialisation
from deep32_relay import native, process, selftest


def encoded(value):
    return json.dumps(value).encode() + b'\n'


SYSTEM_EVENTS = (
    dict(type='system', subtype='ui_invalidate'),
    dict(type='system', subtype='status', status='synthetic'),
    dict(type='system', subtype='compact_boundary'),
    dict(type='system'),
    dict(type='system', subtype=None),
)
PREMATURE_EVENTS = tuple(dict(type=kind) for kind in ('assistant', 'user', 'result', 'tool'))


class Protocol(OfflineCase):
    def protocol(self):
        return native.ClaudeProtocol(self.root, 'synthetic-model', '')

    def test_system_events_before_initialize_leave_gate_and_replies_unchanged(self):
        protocol = self.protocol()
        for event in SYSTEM_EVENTS:
            with self.subTest(event=event):
                self.assertIsNone(protocol.attest(encoded(event)))
                self.assertEqual(protocol.stage, 'initialize')
                self.assertEqual(protocol.replies(), [])
                self.assertEqual((protocol.version, protocol.model), ('', 'synthetic-model'))
        with self.assertRaises(native.NativeError):
            protocol.turn('synthetic question', ())

    def test_system_events_between_control_responses_preserve_pending_reply(self):
        protocol = self.protocol()
        self.assertIsNone(protocol.attest(encoded(claude_control('relay-init', initialisation()))))
        for event in SYSTEM_EVENTS:
            self.assertIsNone(protocol.attest(encoded(event)))
            self.assertEqual(protocol.stage, 'mcp-status')
        self.assertEqual([json.loads(line)['request']['subtype'] for line in protocol.replies()], ['mcp_status'])
        self.assertTrue(protocol.attest(encoded(claude_control('relay-mcp-status', dict(
            mcpServers=[dict(name='deep32', status='connected')])))))

    def test_system_events_before_init_leave_stage_two_closed(self):
        protocol = self.protocol()
        claude_stage1(protocol)
        protocol.turn('synthetic question', ())
        for event in SYSTEM_EVENTS:
            self.assertIsNone(protocol.attest_turn(encoded(event)))
            self.assertEqual(protocol.stage, 'await-init')
        self.assertTrue(protocol.attest_turn(encoded(claude_init(self.root))))
        self.assertEqual(protocol.stage, 'running')

    def test_non_system_turn_events_are_refused_before_each_control_response(self):
        for after_initialize in (False, True):
            for event in PREMATURE_EVENTS:
                with self.subTest(after_initialize=after_initialize, kind=event['type']):
                    protocol = self.protocol()
                    if after_initialize:
                        protocol.attest(encoded(claude_control('relay-init', initialisation())))
                    protocol.attest(encoded(SYSTEM_EVENTS[0]))
                    with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
                        protocol.attest(encoded(event))

    def test_non_system_turn_events_are_refused_before_init(self):
        for event in PREMATURE_EVENTS:
            with self.subTest(kind=event['type']):
                protocol = self.protocol()
                claude_stage1(protocol)
                protocol.turn('synthetic question', ())
                protocol.attest_turn(encoded(SYSTEM_EVENTS[0]))
                with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
                    protocol.attest_turn(encoded(event))

    def test_init_is_still_refused_before_both_control_responses(self):
        for after_initialize in (False, True):
            with self.subTest(after_initialize=after_initialize):
                protocol = self.protocol()
                if after_initialize:
                    protocol.attest(encoded(claude_control('relay-init', initialisation())))
                protocol.attest(encoded(SYSTEM_EVENTS[0]))
                with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
                    protocol.attest(encoded(claude_init(self.root)))

    def test_ignored_system_event_does_not_weaken_exact_init_validation(self):
        changes = (
            ('tools', lambda event: event['tools'].append('Bash')),
            ('missing-tool', lambda event: event['tools'].pop()),
            ('duplicate-tool', lambda event: event['tools'].__setitem__(-1, event['tools'][0])),
            ('server', lambda event: event['mcp_servers'].append(dict(name='other', status='connected'))),
            ('status', lambda event: event['mcp_servers'][0].__setitem__('status', 'failed')),
            ('model', lambda event: event.__setitem__('model', 'synthetic-other')),
            ('cwd', lambda event: event.__setitem__('cwd', str(self.root.parent))),
        )
        for name, change in changes:
            with self.subTest(change=name):
                protocol = self.protocol()
                claude_stage1(protocol)
                protocol.turn('synthetic question', ())
                protocol.attest_turn(encoded(SYSTEM_EVENTS[0]))
                event = copy.deepcopy(claude_init(self.root))
                change(event)
                with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
                    protocol.attest_turn(encoded(event))
                self.assertEqual(protocol.stage, 'await-init')

    def test_stage_two_system_events_are_refused_in_the_wrong_stage(self):
        for stage in ('initialize', 'pre-turn', 'running'):
            with self.subTest(stage=stage):
                protocol = self.protocol()
                if stage != 'initialize':
                    claude_stage1(protocol)
                if stage == 'running':
                    protocol.turn('synthetic question', ())
                    protocol.attest_turn(encoded(claude_init(self.root)))
                with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
                    protocol.attest_turn(encoded(SYSTEM_EVENTS[0]))

    def test_oversized_ignored_system_output_still_trips_the_reader_bound(self):
        plan = native.LaunchPlan((str(Path(sys.executable).resolve()),), self.root, {})
        session = native.NativeSession(plan, self.protocol(), object(), MemoryProxy(), live=lambda: True)
        raw = encoded(dict(type='system', subtype='ui_invalidate', text='x' * native.MAX_RESPONSE))
        session._read(io.BytesIO(raw), False)
        self.assertEqual(session.failure, 'native-output-failed')
        self.assertGreater(session.output_bytes, native.MAX_RESPONSE)
        self.assertTrue(session.lines.empty())
        self.assertFalse(session.attested)


SCRIPT = '''import json,sys,time
from pathlib import Path
mode=MODE
systems=SYSTEMS
premature=PREMATURE
def send(value):print(json.dumps(value),flush=True)
def unsolicited():
    for event in systems:send(event)
def record(value):
    with Path('synthetic-order').open('a') as stream:stream.write(value+'\\n')
def repeat():
    while True:
        send(systems[0]);time.sleep(.002)
unsolicited()
if mode=='stage1-timeout':repeat()
value=json.loads(sys.stdin.readline());record(value['request']['subtype'])
assert value['request']['subtype']=='initialize'
if mode.startswith('stage1-') and mode!='stage1-timeout':
    send(premature);time.sleep(60)
send(dict(type='control_response',response=dict(subtype='success',request_id=value['request_id'],response=INITIALISATION)))
unsolicited()
value=json.loads(sys.stdin.readline());record(value['request']['subtype'])
assert value['request']['subtype']=='mcp_status'
send(dict(type='control_response',response=dict(subtype='success',request_id=value['request_id'],response=dict(mcpServers=[dict(name='deep32',status='connected')]))))
line=sys.stdin.readline()
if not line:sys.exit(0)
value=json.loads(line);record(value['type'])
assert value['type']=='user'
Path('synthetic-model-turn').write_text('sent')
unsolicited()
if mode=='stage2-timeout':repeat()
if mode.startswith('stage2-') and mode!='stage2-timeout':
    send(premature);time.sleep(60)
event=INIT
event['cwd']=str(Path.cwd())
send(event)
send(dict(type='result',subtype='success',is_error=False,result='synthetic answer',usage=dict(input_tokens=2,output_tokens=3)))
'''


@unittest.skipUnless(os.name == 'nt', 'scripted native processes use the owned Windows Job')
class Executables(OfflineCase):
    def fake(self, mode='normal', premature=None):
        script = self.root / ('synthetic-system-' + mode + '.py')
        script.write_text(SCRIPT.replace('MODE', repr(mode)).replace('SYSTEMS', repr(SYSTEM_EVENTS))
                          .replace('PREMATURE', repr(premature)).replace('INITIALISATION', repr(initialisation()))
                          .replace('INIT', repr(claude_init(self.root))), encoding='utf-8')
        binary = Path(sys.executable).resolve(strict=True)
        trees = []
        def factory(platform):
            tree = process.WindowsJob()
            trees.append(tree)
            start = tree.start
            tree.start = lambda argv, **kwargs: start((str(binary), '-I', '-B', str(script), *argv[1:]), **kwargs)
            return tree
        profile = self.root / 'profile'
        profile.mkdir(exist_ok=True)
        return binary, profile, factory, trees

    def session(self, mode='normal', premature=None):
        binary, profile, factory, trees = self.fake(mode, premature)
        endpoint = MemoryProxy()
        opened = []
        plan, protocol = native.ClaudeCodeLauncher().prepare(binary, self.root, profile,
            endpoint=endpoint.url, bearer='SYNTHETIC-BEARER', model='synthetic-model', platform='windows')
        session = native.NativeSession(plan, protocol, factory('windows'), endpoint,
                                       live=lambda: True, on_attested=lambda: opened.append('stage-two'))
        self.addCleanup(session.cancel)
        return session, opened

    def test_fake_unsolicited_system_events_pass_both_gates_before_answer(self):
        session, opened = self.session()
        self.assertTrue(session.initialise())
        self.assertEqual(opened, [])
        self.assertFalse((self.root / 'synthetic-model-turn').exists())
        self.assertIs(session.release('synthetic question', ()), session)
        self.assertEqual(opened, ['stage-two'])
        self.assertEqual(session.protocol.stage, 'running')
        events = []
        end = time.monotonic() + 5
        while not session.completed and time.monotonic() < end:
            events += session.poll()
            time.sleep(.01)
        self.assertEqual(events[-1], dict(type='completed', usage=dict(input_tokens=2, output_tokens=3)))
        self.assertEqual((self.root / 'synthetic-order').read_text().splitlines(), ['initialize', 'mcp_status', 'user'])
        self.assertTrue(session.cancel())

    def test_fake_install_check_ignores_system_events_without_user_message(self):
        binary, profile, factory, trees = self.fake()
        result = selftest.check_startup('claude', binary, profile, self.root,
                                       tree_factory=factory, proxy_factory=MemoryProxy)
        self.assertEqual(result, dict(version='synthetic-999.1', model='synthetic-model', effort='default'))
        self.assertEqual((self.root / 'synthetic-order').read_text().splitlines(), ['initialize', 'mcp_status'])
        self.assertFalse((self.root / 'synthetic-model-turn').exists())
        self.assertTrue(trees[0].closed)

    def test_fake_stage_one_still_refuses_each_premature_turn_event(self):
        for event in PREMATURE_EVENTS:
            with self.subTest(kind=event['type']):
                scratch = self.root / event['type']
                scratch.mkdir()
                binary, profile, factory, trees = self.fake('stage1-' + event['type'], event)
                endpoint = MemoryProxy()
                plan, protocol = native.ClaudeCodeLauncher().prepare(binary, scratch, profile,
                    endpoint=endpoint.url, bearer='SYNTHETIC-BEARER', model='synthetic-model', platform='windows')
                opened = []
                session = native.NativeSession(plan, protocol, factory('windows'), endpoint,
                    live=lambda: True, on_attested=lambda: opened.append(True))
                self.addCleanup(session.cancel)
                with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
                    session.initialise()
                self.assertEqual(opened, [])
                self.assertFalse((scratch / 'synthetic-model-turn').exists())
                self.assertTrue(session.cleanup_complete)
                self.assertTrue(endpoint.closed)

    def test_fake_stage_two_still_refuses_each_premature_turn_event(self):
        for event in PREMATURE_EVENTS:
            with self.subTest(kind=event['type']):
                scratch = self.root / event['type']
                scratch.mkdir()
                binary, profile, factory, trees = self.fake('stage2-' + event['type'], event)
                endpoint = MemoryProxy()
                plan, protocol = native.ClaudeCodeLauncher().prepare(binary, scratch, profile,
                    endpoint=endpoint.url, bearer='SYNTHETIC-BEARER', model='synthetic-model', platform='windows')
                opened = []
                session = native.NativeSession(plan, protocol, factory('windows'), endpoint,
                    live=lambda: True, on_attested=lambda: opened.append(True))
                self.addCleanup(session.cancel)
                self.assertTrue(session.initialise())
                with self.assertRaisesRegex(native.NativeError, '^native-startup-unverified$'):
                    session.release('synthetic question', ())
                self.assertEqual(opened, [])
                self.assertTrue(session.cleanup_complete)
                self.assertTrue(endpoint.closed)

    def test_fake_system_events_do_not_extend_stage_one_deadline(self):
        session, opened = self.session('stage1-timeout')
        with self.assertRaisesRegex(native.NativeError, '^native-startup-timeout$'):
            session.initialise(timeout=.2)
        self.assertEqual(opened, [])
        self.assertFalse((self.root / 'synthetic-model-turn').exists())
        self.assertTrue(session.cleanup_complete)

    def test_fake_system_events_do_not_extend_stage_two_deadline(self):
        session, opened = self.session('stage2-timeout')
        self.assertTrue(session.initialise())
        with self.assertRaisesRegex(native.NativeError, '^native-startup-timeout$'):
            session.release('synthetic question', (), timeout=.2)
        self.assertEqual(opened, [])
        self.assertTrue(session.cleanup_complete)


if __name__ == '__main__':
    unittest.main()
