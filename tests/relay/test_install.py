"""Offline lifecycle/template evidence; no host scheduler or account is used."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'adapters/relay'))
from deep32_relay import connector
from deep32_relay.platform import PlatformAdapter, ServiceSpec, render
from deep32_relay.state import OwnedRoot, StateError, strict_json

_scratch_value = os.environ.get('DEEP32_RELAY_TEST_ROOT')
if not _scratch_value or not Path(_scratch_value).is_absolute():
    raise RuntimeError('explicit relay test scratch required')
SCRATCH = Path(_scratch_value)


class FakeSecurity:
    """Filesystem-shape checks plus an explicit fake ACL owner decision."""
    def __init__(self):
        self.denied = set()
        self.writes = 0

    def mkdir(self, path):
        self.writes += 1
        os.mkdir(path, 0o700)

    def protect_file(self, path):
        self.writes += 1

    def verify(self, path, directory=False):
        info = path.lstat()
        if (str(path) in self.denied or stat.S_ISLNK(info.st_mode) or
                getattr(info, 'st_file_attributes', 0) & 0x400 or
                not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
                (not directory and info.st_nlink != 1)):
            raise StateError('unsafe-permissions')


class FakeAPI:
    qualified = True  # Private test seam, never a CLI/native capability claim.
    def __init__(self):
        self.entries = {}
        self.calls = []
        self.fail_register = False
        self.stop_ok = True

    def inspect(self, name):
        self.calls.append(('inspect', name))
        return self.entries.get(name)

    def register(self, name, definition):
        self.calls.append(('register', name))
        if self.fail_register:
            raise StateError('startup-registration-failed')
        self.entries[name] = definition

    def start(self, name):
        self.calls.append(('start', name))

    def stop(self, name):
        self.calls.append(('stop', name))
        return self.stop_ok

    def unregister(self, name):
        self.calls.append(('unregister', name))
        self.entries.pop(name, None)


class Installation(unittest.TestCase):
    def setUp(self):
        # The task runner sets every temp/test-root variable to this task's C: scratch.
        scratch = SCRATCH.resolve()
        if REPO in (scratch, *scratch.parents) or not scratch.is_dir():
            raise RuntimeError('explicit relay test scratch required')
        self.temporary = tempfile.TemporaryDirectory(prefix='install-', dir=scratch)
        self.base = Path(self.temporary.name)
        self.security = FakeSecurity()
        self.root = OwnedRoot(self.base / ('a' * 32), self.security)
        self.source = self.base / 'source'
        (self.source / 'deep32_relay').mkdir(parents=True)
        (self.source / 'run_relay.py').write_text('# synthetic service\n', encoding='ascii')
        (self.source / 'RESEARCH.md').write_text('Synthetic research instruction.\n', encoding='ascii')
        (self.source / 'deep32_relay/__init__.py').write_text('', encoding='ascii')
        (self.source / 'deep32_relay/module.py').write_text('VALUE = 1\n', encoding='ascii')
        self.agent = self.base / 'synthetic-agent.exe'
        self.agent.write_bytes(b'synthetic executable, never run')
        self.python = self.base / 'synthetic-python.exe'
        self.python.write_bytes(b'synthetic interpreter, never run')
        self.api = FakeAPI()

    def tearDown(self):
        self.temporary.cleanup()

    def install(self, **kw):
        options = dict(source=self.source, install_id='a' * 32,
                       python_binary=str(self.python), platform='windows' if os.name == 'nt' else 'linux')
        options.update(kw)
        return connector.install(self.root, 'codex', str(self.agent), **options)

    def test_install_unavailable_no_host_registration_or_status(self):
        result = self.install()
        self.assertFalse(result['ready'])
        self.assertFalse(result['startup_registered'])
        self.assertFalse((self.root.root / 'runtime/status.json').exists())
        self.assertTrue((self.root.root / 'profiles/codex').is_dir())
        self.assertEqual(list((self.root.root / 'profiles/codex').iterdir()), [])
        self.assertEqual(result['profile_alias'], 'relay-codex')
        self.assertFalse(self.api.calls)

    def test_source_manifest_includes_research(self):
        result = self.install()
        receipt = connector.validate_receipt(self.root)
        key = 'packages/' + result['release_id'] + '/RESEARCH.md'
        self.assertIn(key, receipt['owned_files'])
        self.assertEqual(self.root.read(key), (self.source / 'RESEARCH.md').read_bytes())

    def test_fake_registration_reads_back_exact_definition(self):
        result = self.install(platform_api=self.api)
        self.assertTrue(result['startup_registered'])
        self.assertFalse(result['ready'])
        self.assertEqual(sum(call[0] == 'register' for call in self.api.calls), 1)

    def test_idempotent_install_preserves_files_and_profile(self):
        self.install(platform_api=self.api)
        profile = self.root.root / 'profiles/codex/synthetic-account'
        profile.write_bytes(b'SYNTHETIC_ACCOUNT_CANARY')
        receipt_before = self.root.read('install.json')
        calls_before = len(self.api.calls)
        self.install(platform_api=self.api)
        self.assertEqual(self.root.read('install.json'), receipt_before)
        self.assertEqual(sum(row[0] == 'register' for row in self.api.calls[calls_before:]), 0)
        self.assertEqual(profile.read_bytes(), b'SYNTHETIC_ACCOUNT_CANARY')

    def test_profile_and_private_state_survive_upgrade(self):
        first = self.install()
        profile = self.root.root / 'profiles/codex/synthetic-account'
        profile.write_bytes(b'SYNTHETIC_ACCOUNT_CANARY')
        pin = self.root.root / 'private/link/synthetic-pin'
        pin.write_bytes(b'SYNTHETIC_LINK_CANARY')
        (self.source / 'deep32_relay/module.py').write_text('VALUE = 2\n', encoding='ascii')
        with self.assertRaisesRegex(StateError, 'remove-before-install'):
            self.install()
        connector.remove(self.root)
        second = self.install()
        self.assertNotEqual(first['release_id'], second['release_id'])
        receipt = connector.validate_receipt(self.root)
        self.assertEqual(receipt['previous_release_id'], first['release_id'])
        self.assertEqual(profile.read_bytes(), b'SYNTHETIC_ACCOUNT_CANARY')
        self.assertEqual(pin.read_bytes(), b'SYNTHETIC_LINK_CANARY')
        self.assertTrue((self.root.root / 'packages' / first['release_id']).is_dir())
        self.assertFalse(second['ready'])

    def test_upgrade_stops_owned_scope_and_switches_fake_definition(self):
        self.install(platform_api=self.api)
        (self.source / 'deep32_relay/module.py').write_text('VALUE = 3\n', encoding='ascii')
        with self.assertRaisesRegex(StateError, 'remove-before-install'):
            self.install(platform_api=self.api)
        connector.remove(self.root, platform_api=self.api)
        self.install(platform_api=self.api)
        operations = [row[0] for row in self.api.calls]
        self.assertIn('stop', operations)
        self.assertIn('unregister', operations)
        self.assertEqual(operations.count('register'), 2)

    def test_removed_install_reuses_identity_per_r15(self):
        self.install()
        connector.remove(self.root)
        self.assertEqual(self.install()['install_id'], 'a' * 32)

    def test_removal_retains_profile_and_identity(self):
        self.install(platform_api=self.api)
        profile = self.root.root / 'profiles/codex/synthetic-account'
        pin = self.root.root / 'private/link/synthetic-pin'
        profile.write_bytes(b'SYNTHETIC_ACCOUNT_CANARY')
        pin.write_bytes(b'SYNTHETIC_LINK_CANARY')
        removed = connector.remove(self.root, platform_api=self.api)
        self.assertEqual(removed['state'], 'removed')
        self.assertEqual(removed['remote_revocation'], 'not-performed')
        self.assertEqual(profile.read_bytes(), b'SYNTHETIC_ACCOUNT_CANARY')
        self.assertEqual(pin.read_bytes(), b'SYNTHETIC_LINK_CANARY')
        self.assertFalse(self.api.entries)
        self.assertEqual(connector.remove(self.root)['state'], 'removed')

    def test_modified_config_refused_before_removal(self):
        self.install()
        self.root.write_json('config.json', {'token': 'SYNTHETIC_CONFIG_CANARY'})
        with self.assertRaises(StateError):
            connector.remove(self.root)
        self.assertTrue((self.root.root / 'install.json').exists())

    def test_modified_package_refused(self):
        result = self.install()
        self.root.write('packages/' + result['release_id'] + '/run_relay.py', b'changed')
        with self.assertRaisesRegex(StateError, 'modified-owned-file'):
            connector.check(self.root)

    def test_unrecorded_package_file_refused(self):
        result = self.install()
        self.root.write('packages/' + result['release_id'] + '/extra.py', b'foreign')
        with self.assertRaisesRegex(StateError, 'unrecorded-owned-file'):
            connector.remove(self.root)

    def test_foreign_startup_record_retained(self):
        result = self.install(platform_api=self.api)
        name = 'deep32-relay-' + result['install_id']
        self.api.entries[name] = b'FOREIGN_STARTUP_CANARY'
        result = connector.remove(self.root, platform_api=self.api)
        self.assertEqual(result['removal'], {'complete': False, 'leftovers': ['startup']})
        self.assertEqual(self.api.entries[name], b'FOREIGN_STARTUP_CANARY')
        self.assertTrue((self.root.root / 'config.json').exists())

    def test_stop_failure_never_deletes_files(self):
        self.install(platform_api=self.api)
        self.api.stop_ok = False
        result = connector.remove(self.root, platform_api=self.api)
        self.assertEqual(result['removal'], {'complete': False, 'leftovers': ['startup']})
        self.assertTrue((self.root.root / 'config.json').exists())
        self.assertEqual(self.root.read_json('transaction.json')['phase'], 'removing')

    def test_registration_failure_keeps_explicit_pending_state(self):
        self.api.fail_register = True
        with self.assertRaises(StateError):
            self.install(platform_api=self.api)
        self.assertFalse((self.root.root / 'install.json').exists())
        self.assertEqual(self.root.read_json('transaction.json')['phase'], 'preparing')
        self.assertFalse(self.api.entries)
        self.api.fail_register = False
        self.assertFalse(self.install(platform_api=self.api)['ready'])

    def test_receipt_failure_rolls_back_fake_registration(self):
        write = self.root.write_json
        def fail(relative, value):
            if relative == 'install.json':
                raise OSError('SYNTHETIC_EXCEPTION_CANARY')
            return write(relative, value)
        with patch.object(self.root, 'write_json', side_effect=fail):
            with self.assertRaises(OSError):
                self.install(platform_api=self.api)
        self.assertFalse(self.api.entries)
        self.assertTrue((self.root.root / 'transaction.json').exists())

    def test_foreign_nonempty_root_refused(self):
        self.root.create()
        self.root.write('foreign', b'foreign')
        with self.assertRaisesRegex(StateError, 'foreign-state-root'):
            self.install()

    def test_wrong_install_owner_refused(self):
        self.install()
        self.root.write_json('owner.json', {'schema': 'deep32.relay-owner.v1', 'install_id': 'b' * 32})
        with self.assertRaisesRegex(StateError, 'ownership-mismatch'):
            self.install()

    def test_wrong_security_owner_refused(self):
        self.install()
        self.security.denied.add(str(self.root.root))
        with self.assertRaises(StateError):
            connector.check(self.root)

    def test_hardlinked_owned_file_refused(self):
        self.install()
        os.link(self.root.root / 'config.json', self.base / 'synthetic-outside-link')
        with self.assertRaises(StateError):
            connector.check(self.root)

    def test_parent_traversal_and_relative_binary_refused(self):
        self.install()
        for relative in ('../outside', '/absolute', 'private/../outside', 'private\\..\\outside'):
            with self.subTest(relative=relative), self.assertRaises(StateError):
                self.root.path(relative)
        with self.assertRaisesRegex(StateError, 'absolute-binary-required'):
            connector.install(self.root, 'codex', 'relative')

    def test_invalid_agent_and_missing_package_preflight(self):
        with self.assertRaises(StateError):
            connector.install(self.root, 'both', str(self.agent))
        (self.source / 'run_relay.py').unlink()
        with self.assertRaisesRegex(StateError, 'relay-package-missing'):
            self.install()
        self.assertFalse(self.root.root.exists())

    def test_running_interpreter_resolves_only_default_selection(self):
        actual = Path(sys.executable).resolve(strict=True)
        result = connector.install(self.root, 'codex', str(self.agent), source=self.source,
                                   install_id='a' * 32,
                                   platform='windows' if os.name == 'nt' else 'linux')
        self.assertFalse(result['ready'])
        self.assertEqual(connector.validate_receipt(self.root)['python_binary'], str(actual))

    def test_read_only_status_check_no_launch_or_mutation(self):
        self.install()
        before = {p.relative_to(self.root.root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
                  for p in self.root.root.rglob('*') if p.is_file()}
        writes = self.security.writes
        with patch('subprocess.run', side_effect=AssertionError('launch')), patch('socket.socket', side_effect=AssertionError('network')):
            for _ in range(3):
                self.assertFalse(connector.status(self.root)['ready'])
                self.assertFalse(connector.check(self.root)['ready'])
        after = {p.relative_to(self.root.root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
                 for p in self.root.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(writes, self.security.writes)

    def test_public_output_excludes_paths_and_synthetic_private_values(self):
        result = self.install()
        (self.root.root / 'profiles/codex/synthetic-account').write_bytes(b'SYNTHETIC_ACCOUNT_CANARY')
        output = json.dumps(connector.status(self.root), sort_keys=True)
        for forbidden in ('SYNTHETIC_ACCOUNT_CANARY', str(self.base), str(self.agent), 'token', 'password'):
            self.assertNotIn(forbidden, output)
        self.assertEqual(result['unavailable_reasons'][0], 'native-agent-unqualified')


class StartupTemplates(unittest.TestCase):
    def test_windows_xml_literal_paths_and_least_privilege(self):
        spec = ServiceSpec('c' * 32, 'C:\\Program Files\\Python\\python.exe',
                           'C:\\relay & literal\\run_relay.py', 'C:\\relay & literal\\state', 'windows')
        filename, raw = render(spec)
        task = ET.fromstring(raw)
        ns = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        self.assertEqual(task.find('.//t:Command', ns).text, spec.python)
        self.assertEqual(task.find('.//t:WorkingDirectory', ns).text, spec.root)
        self.assertEqual(task.find('.//t:RunLevel', ns).text, 'LeastPrivilege')
        self.assertEqual(task.find('.//t:LogonType', ns).text, 'InteractiveToken')
        self.assertEqual(task.find('.//t:MultipleInstancesPolicy', ns).text, 'IgnoreNew')
        self.assertIsNotNone(task.find('.//t:LogonTrigger', ns))
        self.assertNotIn(b'Password', raw)
        self.assertTrue(filename.endswith('.xml'))

    def test_linux_unit_escapes_specifiers_dollars_and_quotes(self):
        spec = ServiceSpec('d' * 32, '/opt/Python path/python', '/opt/relay $x%/run.py', '/tmp/state "literal"', 'linux')
        filename, raw = render(spec)
        text = raw.decode()
        self.assertIn('$$x%%', text)
        self.assertIn('\nWorkingDirectory=/tmp/state "literal"\n', text)  # systemd takes the path literally
        self.assertIn('KillMode=control-group', text)
        self.assertIn('WantedBy=default.target', text)
        self.assertNotIn('/bin/sh', text)
        self.assertTrue(filename.endswith('.service'))

    def test_macos_plist_uses_literal_argument_array(self):
        spec = ServiceSpec('e' * 32, '/Applications/Python path/python', '/opt/relay $x/run.py', '/tmp/relay state', 'macos')
        filename, raw = render(spec)
        value = plistlib.loads(raw)
        self.assertEqual(value['ProgramArguments'], list(spec.argv))
        self.assertEqual(value['WorkingDirectory'], spec.root)
        self.assertEqual(value['Umask'], 0o077)
        self.assertTrue(filename.endswith('.plist'))

    def test_control_characters_and_relative_paths_rejected(self):
        for value in ('relative', '/tmp/new\ncommand', '/tmp/tab\tpath'):
            with self.subTest(value=value), self.assertRaises(StateError):
                ServiceSpec('e' * 32, value, '/service.py', '/state', 'linux')

    def test_unqualified_api_has_zero_effects(self):
        spec = ServiceSpec('f' * 32, '/python', '/service.py', '/state', 'linux')
        self.assertFalse(PlatformAdapter().register(spec, render(spec)[1]))
        with self.assertRaisesRegex(StateError, 'autostart-unqualified'):
            PlatformAdapter().remove(spec, render(spec)[1])


if __name__ == '__main__':
    unittest.main()
