"""Each agent gets its own research instruction, and gets it once.

Synthetic receipts and protocol events only: no agent, no network. RESEARCH.md is the Claude Code text and
RESEARCH-codex.md the Codex one (adapters/relay/make_research.py)."""
import hashlib
import json
from unittest import mock

from support import OfflineCase, ROOT
from test_native_r15 import codex_config
from deep32_relay import connector, engine, native, selftest, service
from deep32_relay.state import OwnedRoot, OWNER_SCHEMA, StateError

RELEASE = '2' * 64


def install(agent, **files):
    owned = {'packages/' + RELEASE + '/' + name: digest for name, digest in files.items()}
    owned['packages/' + RELEASE + '/deep32_relay/native.py'] = '4' * 64
    return dict(agent=agent, install_id='1' * 32, release_id=RELEASE, profile_alias='relay-' + agent,
                owned_files=owned)


class PackagedInstruction(OfflineCase):
    def test_the_source_package_carries_both_instructions(self):
        release, files = connector.source_package()
        self.assertIn('RESEARCH.md', files)
        self.assertIn('RESEARCH-codex.md', files)
        self.assertNotEqual(files['RESEARCH.md'], files['RESEARCH-codex.md'])
        self.assertEqual(len(release), 64)
        self.assertEqual(len(service.package_digest()), 64)

    def test_the_source_package_carries_the_answer_check(self):
        # every deep32_relay/*.py is packaged, so an installed relay has the script the README names
        _, files = connector.source_package()
        self.assertIn('deep32_relay/answer_check.py', files)
        self.assertNotIn(b'from .', files['deep32_relay/answer_check.py'])      # it must run as a plain script
        self.assertNotIn(b'deep32_relay import', files['deep32_relay/answer_check.py'])

    def test_a_package_without_the_codex_text_is_still_a_package(self):
        source = self.root / 'source'
        (source / 'deep32_relay').mkdir(parents=True)
        (source / 'run_relay.py').write_text('synthetic\n', encoding='ascii')
        (source / 'deep32_relay/__init__.py').write_text('synthetic\n', encoding='ascii')
        (source / 'RESEARCH.md').write_text('Synthetic research instruction.\n', encoding='ascii')
        first, files = connector.source_package(source)
        self.assertEqual(sorted(files), ['RESEARCH.md', 'deep32_relay/__init__.py', 'run_relay.py'])
        (source / 'RESEARCH-codex.md').write_text('Synthetic Codex instruction.\n', encoding='ascii')
        second, files = connector.source_package(source)
        self.assertIn('RESEARCH-codex.md', files)
        self.assertNotEqual(first, second)          # a new instruction is a new release of the package
        (source / 'RESEARCH.md').unlink()
        with self.assertRaises(StateError):
            connector.source_package(source)

    def test_each_agent_is_bound_to_its_own_file(self):
        prefix = 'packages/' + RELEASE + '/'
        both = dict({'RESEARCH.md': '3' * 64}, **{'RESEARCH-codex.md': '5' * 64})
        self.assertEqual(selftest.instruction_file(install('claude', **both)), prefix + 'RESEARCH.md')
        self.assertEqual(selftest.instruction_file(install('codex', **both)), prefix + 'RESEARCH-codex.md')
        # a package made before the Codex text existed: its one instruction serves both agents
        self.assertEqual(selftest.instruction_file(install('codex', **{'RESEARCH.md': '3' * 64})),
                         prefix + 'RESEARCH.md')
        with self.assertRaises(StateError):
            selftest.instruction_file(dict(agent='codex', release_id='short', owned_files={}))

    def test_the_binding_follows_the_agents_file(self):
        both = {'RESEARCH.md': '3' * 64, 'RESEARCH-codex.md': '5' * 64}
        for agent, own, other in (('claude', 'RESEARCH.md', 'RESEARCH-codex.md'),
                                  ('codex', 'RESEARCH-codex.md', 'RESEARCH.md')):
            with self.subTest(agent=agent):
                base = selftest.install_binding(install(agent, **both))
                self.assertNotEqual(base, selftest.install_binding(install(agent, **dict(both, **{own: '6' * 64}))))
                self.assertEqual(base, selftest.install_binding(install(agent, **dict(both, **{other: '6' * 64}))))

    def owned(self, agent, texts, recorded=None):
        from test_install import FakeSecurity
        owner = OwnedRoot(self.root / ('owned-' + agent), security=FakeSecurity())
        owner.create()
        owner.write_json('owner.json', dict(schema=OWNER_SCHEMA, install_id='1' * 32))
        owner.mkdir('packages/' + RELEASE)
        owner.mkdir('profiles/' + agent)
        digests = {}
        for name, text in texts.items():
            owner.write('packages/' + RELEASE + '/' + name, text.encode('utf-8'))
            digests[name] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        digests.update(recorded or {})
        owner.mkdir('private/qualification')
        owner.write_json('private/qualification/%s.json' % agent, dict(synthetic=True))
        return owner, dict(install(agent, **digests), agent_binary='synthetic')

    def from_owned(self, owner, receipt):
        # the qualification record and its status are stand-ins: this test is about which file is read
        record = dict(schema=selftest.ATTEST_SCHEMA, state='qualified', model='synthetic-model', effort='default',
                      version='synthetic-1')
        with mock.patch.object(selftest, 'validate_attestation', return_value=record), \
                mock.patch.object(selftest, 'qualification_status', return_value=dict(state='qualified')):
            return engine.native_from_owned(owner, receipt)

    def test_the_relay_gives_each_agent_its_own_text(self):
        texts = {'RESEARCH.md': 'Synthetic Claude Code instruction.\n', 'RESEARCH-codex.md': 'Synthetic Codex instruction.\n'}
        for agent, expected in (('claude', texts['RESEARCH.md']), ('codex', texts['RESEARCH-codex.md'])):
            with self.subTest(agent=agent):
                owner, receipt = self.owned(agent, texts)
                native_agent = self.from_owned(owner, receipt)
                self.assertEqual(native_agent.instruction, expected)
                # the answer record names the instruction by this hash
                self.assertEqual(native_agent.instruction_sha256, hashlib.sha256(expected.encode('utf-8')).hexdigest())
        owner, receipt = self.owned('codex', {'RESEARCH.md': texts['RESEARCH.md']})
        self.assertEqual(self.from_owned(owner, receipt).instruction, texts['RESEARCH.md'])

    def test_a_changed_instruction_file_leaves_the_agent_unready(self):
        texts = {'RESEARCH.md': 'Synthetic Claude Code instruction.\n', 'RESEARCH-codex.md': 'Synthetic Codex instruction.\n'}
        owner, receipt = self.owned('codex', texts, recorded={'RESEARCH-codex.md': '7' * 64})
        agent = self.from_owned(owner, receipt)
        self.assertEqual((agent.ready, agent.instruction), (False, ''))


class GivenOnce(OfflineCase):
    INSTRUCTION = 'SYNTHETIC-INSTRUCTION line one\nline two'

    def test_claude_reads_the_instruction_then_the_question(self):
        protocol = native.ClaudeProtocol(self.root, 'synthetic-model', self.INSTRUCTION)
        protocol.stage = 'pre-turn'
        value = json.loads(protocol.turn('synthetic question', ()))
        self.assertEqual(value['message']['content'], self.INSTRUCTION + '\n\nsynthetic question')

    def test_codex_has_the_instruction_as_its_base_instructions_and_not_in_the_turn(self):
        protocol = native.CodexProtocol(self.root, None, None, self.INSTRUCTION)
        protocol.attest(json.dumps(dict(id=1, result=dict(userAgent='synthetic-codex/999'))).encode())
        protocol.replies()
        protocol.attest(json.dumps(dict(id=2, result=dict(config=codex_config(), layers=[]))).encode())
        features = [dict(name=name, enabled=False) for name in native.CODEX_DISABLED]
        protocol.attest(json.dumps(dict(id=3, result=dict(data=features))).encode())
        started = [json.loads(raw) for raw in protocol.replies()]
        self.assertEqual([item['method'] for item in started], ['thread/start'])
        self.assertEqual(started[0]['params']['baseInstructions'], self.INSTRUCTION)
        protocol.thread = 'synthetic-thread'
        turn = protocol.turn('synthetic question', ())
        self.assertNotIn(b'SYNTHETIC-INSTRUCTION', turn)
        self.assertEqual(json.loads(turn)['params']['input'], [dict(type='text', text='synthetic question')])

    def test_the_real_instructions_are_not_repeated_in_a_codex_turn(self):
        text = (ROOT / 'adapters/relay/RESEARCH-codex.md').read_bytes().decode('utf-8')
        protocol = native.CodexProtocol(self.root, None, None, text)
        protocol.thread = 'synthetic-thread'
        self.assertLess(len(protocol.turn('synthetic question', ())), 1024)
