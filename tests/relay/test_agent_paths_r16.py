"""Finding 17: stable native entry points and version-bound startup checks.

All paths and binary bytes are synthetic. Windows hosts without symlink rights
also exercise target rotation through a narrow Path.resolve fixture.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from unittest import mock

from support import OfflineCase
from test_install import FakeSecurity
from deep32_relay import connector, engine, native, selftest
from deep32_relay.state import OwnedRoot, OWNER_SCHEMA, StateError, safe_path


class AgentPaths(OfflineCase):
    def setUp(self):
        super().setUp()
        self.first = self.root / 'synthetic-version-1.exe'
        self.second = self.root / 'synthetic-version-2.exe'
        self.first.write_bytes(b'synthetic native version 1; never executed')
        self.second.write_bytes(b'synthetic native version 2; never executed')
        self.entry = self.root / 'synthetic-agent.exe'

    @contextmanager
    def selected_target(self, target=None):
        """Model only the symlink lookup; all resolved files remain real."""
        selected = [target or self.first]
        original = Path.resolve
        entry = self.entry
        def resolve(path, strict=False):
            return original(selected[0] if path == entry else path, strict=strict)
        with mock.patch.object(Path, 'resolve', resolve):
            yield selected

    def install_record(self):
        owner = OwnedRoot(self.root / 'owned', security=FakeSecurity())
        owner.create()
        owner.write_json('owner.json', dict(schema=OWNER_SCHEMA, install_id='1' * 32))
        owner.mkdir('profiles/claude')
        release = '2' * 64
        receipt = dict(agent='claude', agent_binary=str(self.entry), install_id='1' * 32,
            release_id=release, profile_alias='relay-claude', owned_files={
                'packages/' + release + '/RESEARCH.md': '3' * 64,
                'packages/' + release + '/deep32_relay/native.py': '4' * 64})
        return owner, receipt

    def test_real_native_symlink_is_accepted_but_state_paths_stay_strict(self):
        try:
            self.entry.symlink_to(self.first)
        except OSError as error:
            if error.errno in (1, 13) or getattr(error, 'winerror', None) == 1314:
                self.skipTest('host does not permit task-local symbolic links')
            raise
        self.assertEqual(engine.resolve_agent_binary(self.entry), self.first)
        self.assertEqual(selftest.binary_digest(self.entry), selftest.binary_digest(self.first))
        self.assertTrue(connector.binary_found(self.entry))
        self.assertEqual(connector._absolute_binary(self.entry, agent=True), str(self.entry))
        with self.assertRaisesRegex(StateError, 'linked-state'):
            safe_path(self.entry)
        with self.assertRaisesRegex(StateError, 'linked-state'):
            connector._absolute_binary(self.entry)
        self.entry.unlink()
        self.entry.symlink_to(self.second)
        self.assertEqual(engine.resolve_agent_binary(self.entry), self.second)
        self.assertEqual(selftest.binary_digest(self.entry), selftest.binary_digest(self.second))

    def test_resolved_target_must_be_regular_and_available(self):
        directory = self.root / 'synthetic-directory'
        directory.mkdir()
        for path in ('relative-agent', self.root / 'missing', directory, str(self.first) + '\n'):
            with self.subTest(path=path), self.assertRaises(StateError):
                engine.resolve_agent_binary(path)
            self.assertFalse(connector.binary_found(path))
        hardlink = self.root / 'synthetic-hardlink.exe'
        os.link(self.first, hardlink)
        with self.assertRaises(StateError):
            engine.resolve_agent_binary(hardlink)
        self.assertFalse(connector.binary_found(hardlink))

    def test_agent_receipt_keeps_stable_selection_and_python_stays_no_link(self):
        with self.selected_target() as selected:
            self.assertEqual(connector._absolute_binary(self.entry, agent=True), str(self.entry))
            self.assertTrue(connector.binary_found(self.entry))
            selected[0] = self.second
            self.assertEqual(connector._absolute_binary(self.entry, agent=True), str(self.entry))
            self.assertEqual(engine.resolve_agent_binary(self.entry), self.second)
            self.assertEqual(selftest.binary_digest(self.entry), hashlib.sha256(self.second.read_bytes()).hexdigest())
        # An agent exception must never widen the state/interpreter validator.
        original = Path.lstat
        entry = self.entry
        def link_stat(path, *args, **kwargs):
            if path == entry:
                import stat
                return type('LinkInfo', (), dict(st_mode=stat.S_IFLNK, st_file_attributes=0))()
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'lstat', link_stat):
            with self.assertRaisesRegex(StateError, 'linked-state'):
                connector._absolute_binary(self.entry)
            with self.assertRaisesRegex(StateError, 'linked-state'):
                safe_path(self.entry / 'profile')

    def test_install_receipt_never_pins_the_version_target(self):
        source = self.root / 'synthetic-package'
        (source / 'deep32_relay').mkdir(parents=True)
        for relative, text in (('run_relay.py', '# synthetic service\n'),
                               ('RESEARCH.md', 'Synthetic research instructions.\n'),
                               ('deep32_relay/__init__.py', '')):
            (source / relative).write_text(text, encoding='ascii')
        owner = OwnedRoot(self.root / ('a' * 32), security=FakeSecurity())
        with self.selected_target(), mock.patch.object(engine, 'qualify_install', return_value={}):
            connector.install(owner, 'claude', str(self.entry), python_binary=str(self.second),
                source=source, install_id='a' * 32, platform=selftest.platform_name())
            receipt = connector.validate_receipt(owner)
            self.assertEqual(receipt['agent_binary'], str(self.entry))
            self.assertNotEqual(receipt['agent_binary'], str(self.first))

    def test_rotated_target_invalidates_attestation_and_recheck_records_new_version(self):
        owner, receipt = self.install_record()
        checked = []
        def checker(agent, executable, profile, scratch, **options):
            checked.append(executable)
            return dict(version='synthetic-1' if executable == self.first else 'synthetic-2',
                        model='synthetic-model', effort='default')
        with self.selected_target() as selected:
            first = selftest.qualify_install(owner, receipt, executable=self.entry, checker=checker)
            self.assertEqual(first['state'], 'qualified')
            self.assertEqual(first['version'], 'synthetic-1')
            self.assertEqual(checked, [self.first])
            self.assertEqual(selftest.qualification_status(owner, receipt, executable=self.entry)['state'], 'qualified')
            selected[0] = self.second
            self.assertEqual(selftest.qualification_status(owner, receipt, executable=self.entry),
                             dict(state='unqualified', reason='binary-changed'))
            second = selftest.qualify_install(owner, receipt, executable=self.entry, checker=checker)
            self.assertEqual(second['state'], 'qualified')
            self.assertEqual(second['version'], 'synthetic-2')
            self.assertEqual(second['model_usage'], 'none')
            self.assertNotEqual(first['binary_sha256'], second['binary_sha256'])
            self.assertEqual(checked, [self.first, self.second])
            self.assertEqual(receipt['agent_binary'], str(self.entry))
            self.assertEqual(selftest.qualification_status(owner, receipt, executable=self.entry)['state'], 'qualified')

    def test_target_rotation_during_install_check_never_qualifies(self):
        owner, receipt = self.install_record()
        # Even equal bytes cannot transfer an in-flight version observation to
        # a different resolution selected while that observation was collected.
        self.second.write_bytes(self.first.read_bytes())
        with self.selected_target() as selected:
            def checker(agent, executable, profile, scratch, **options):
                self.assertEqual(executable, self.first)
                selected[0] = self.second
                return dict(version='synthetic-1', model='synthetic-model', effort='default')
            result = selftest.qualify_install(owner, receipt, executable=self.entry, checker=checker)
            self.assertEqual(result['state'], 'unqualified')
            self.assertEqual(result['reason'], 'binary-changed')
            self.assertEqual(result['version'], '')

    def test_each_turn_rechecks_the_current_selected_target(self):
        owner, receipt = self.install_record()
        with self.selected_target() as selected:
            qualification = selftest.qualify_install(owner, receipt, executable=self.entry,
                checker=lambda *args, **kwargs: dict(version='synthetic-1', model='synthetic-model', effort='default'))
            agent = engine.NativeAgent('claude', owner.root / 'profiles/claude', executable=self.entry,
                qualification=qualification, gateway=self.gateway)
            capability = self.active_cap()
            self.assertTrue(agent.verify(self.root, capability))
            selected[0] = self.second
            with self.assertRaisesRegex(engine.EngineError, 'native-agent-unqualified'):
                agent.verify(self.root, capability)

    def test_each_launch_plan_uses_the_current_regular_target(self):
        profile = self.root / 'profile'
        profile.mkdir()
        with self.selected_target() as selected:
            for launcher in (native.ClaudeCodeLauncher(), native.CodexLauncher()):
                for target in (self.first, self.second):
                    selected[0] = target
                    scratch = self.root / (type(launcher).__name__ + target.stem)
                    scratch.mkdir()
                    plan, _ = launcher.prepare(self.entry, scratch, profile,
                        endpoint='http://127.0.0.1:34567/mcp', bearer='SYNTHETIC-BEARER', platform='windows')
                    self.assertEqual(plan.argv[0], str(target))

    def test_missing_account_reason_survives_no_model_check(self):
        owner, receipt = self.install_record()
        with self.selected_target():
            def checker(*args, **kwargs):
                raise StateError('claude-sign-in-required')
            result = selftest.install_check(owner, receipt, executable=self.entry, checker=checker)
            self.assertEqual(result['state'], 'unqualified')
            self.assertEqual(result['reason'], 'claude-sign-in-required')
            self.assertEqual(result['model_usage'], 'none')
