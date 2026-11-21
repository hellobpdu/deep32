"""Finding 21: the real connector lifecycle with synthetic provider/agent seams."""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import patch

import test_install as existing
import test_login_wrappers_r16 as wrappers
from test_lifecycle import FakeServe
from deep32_relay import connector, engine, provider
from deep32_relay.state import OwnedRoot, StateError


class ReinstallR17(unittest.TestCase):
    setUp = existing.Installation.setUp
    tearDown = existing.Installation.tearDown
    install = existing.Installation.install

    def fake_provider(self, argv):
        if 'venv' in argv:
            directory = Path(argv[-1])
            binary = directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'SYNTHETIC_PROVIDER_BINARY')
            self.provider_builds += 1

    def cli(self, action, *options, implicit=False):
        real_source = connector.source_package
        real_provider = provider.install_provider
        output, error = io.StringIO(), io.StringIO()
        fixture_security = self.security
        class FixtureRoot(OwnedRoot):
            def __init__(self, path, security=None):
                super().__init__(path, security or fixture_security)
        args = [action, '--agent', 'codex']
        if not implicit:
            args += ['--relay-id', 'a' * 32]
        if action == 'install':
            args += ['--agent-binary', str(self.agent), '--no-autostart']
        args += list(options)
        with ExitStack() as stack:
            stack.enter_context(patch.object(connector, 'state_base', return_value=self.base))
            stack.enter_context(patch.object(connector, 'OwnedRoot', FixtureRoot))
            stack.enter_context(patch.object(connector, 'source_package', side_effect=lambda source=None: real_source(self.source)))
            stack.enter_context(patch.object(provider, 'install_provider', side_effect=lambda owner: real_provider(owner, run=self.fake_provider)))
            stack.enter_context(patch.object(engine, 'qualify_install', return_value={}))
            stack.enter_context(patch.object(engine, 'qualification_status', return_value={'state': 'qualified', 'reason': 'none'}))
            for target in ('socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
                           'subprocess.Popen', 'subprocess.run'):
                stack.enter_context(patch(target, side_effect=AssertionError('offline effect refused')))
            stack.enter_context(patch('sys.stdout', output))
            stack.enter_context(patch('sys.stderr', error))
            code = connector.main(args)
        return code, output.getvalue(), error.getvalue()

    def first_install(self, *options):
        self.provider_builds = 0
        code, output, error = self.cli('install', '--json', *options)
        self.assertEqual(code, 0, output + error)
        self.root.write('profiles/codex/synthetic-account', b'SYNTHETIC_SIGN_IN_KEEP')
        self.root.write('private/link/synthetic-identity', b'SYNTHETIC_IDENTITY_KEEP')
        self.root.write('private/link/synthetic-pin', b'SYNTHETIC_PIN_KEEP')
        self.kept = {name: self.root.read(name) for name in (
            'owner.json', 'profiles/codex/synthetic-account',
            'private/link/synthetic-identity', 'private/link/synthetic-pin')}

    def assert_preserved(self):
        for name, raw in self.kept.items():
            self.assertEqual(self.root.read(name), raw)

    def partially_remove(self):
        with patch.object(provider, 'remove_provider', side_effect=PermissionError('SYNTHETIC_PRIVATE_DIAGNOSTIC')):
            code, output, _ = self.cli('remove', '--json')
        self.assertEqual(code, 10)
        result = json.loads(output)
        self.assertEqual(result['removal']['provider']['code'], 'local-permission-denied')
        self.assertFalse(self.root.path('config.json').exists())
        self.assertEqual(self.root.read_json('transaction.json')['phase'], 'removing')

    def test_install_remove_install_same_id_keeps_sign_in_identity_pins_and_config(self):
        self.first_install('--relay-port', '7468')
        saved = self.root.read_json('config.json')
        self.assertEqual(self.cli('remove', '--json')[0], 0)
        self.assertFalse(self.root.path('provider').exists())
        self.assertFalse(self.root.path(provider.RECEIPT).exists())
        self.assertFalse(self.root.path('config.json').exists())
        self.assertEqual(self.cli('install', '--json')[0], 0)
        self.assertEqual(self.provider_builds, 2)
        self.assertEqual(self.root.read_json('config.json'), saved)
        self.assert_preserved()

    def test_half_removed_root_fresh_install_adopts_same_id_and_finishes_removal_first(self):
        self.first_install('--relay-port', '7468')
        self.partially_remove()
        code, output, _ = self.cli('install', '--json', implicit=True)
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)['install_id'], 'a' * 32)
        self.assertEqual(self.provider_builds, 2)
        self.assertFalse(self.root.path('transaction.json').exists())
        self.assertEqual(self.root.read_json('config.json')['port'], 7468)
        self.assert_preserved()

    def test_half_removed_root_explicit_install_resumes(self):
        self.first_install()
        self.partially_remove()
        self.assertEqual(self.cli('install', '--json')[0], 0)
        self.assert_preserved()

    def test_blocked_removal_refuses_before_provider_rebuild(self):
        self.first_install()
        self.partially_remove()
        with patch.object(provider, 'remove_provider', side_effect=PermissionError('SYNTHETIC_PRIVATE_DIAGNOSTIC')):
            code, output, _ = self.cli('install', '--json', implicit=True)
        self.assertEqual(code, 10)
        self.assertEqual(json.loads(output)['code'], 'relay-removal-incomplete')
        self.assertEqual(self.provider_builds, 1)
        self.assert_preserved()

    def test_missing_config_without_removal_journal_is_not_silently_repaired(self):
        self.first_install()
        self.root.path('config.json').unlink()
        code, output, _ = self.cli('install', '--json', implicit=True)
        self.assertEqual(code, 10)
        self.assertEqual(json.loads(output)['code'], 'relay-file-missing')
        self.assertEqual(self.provider_builds, 1)

    def test_invalid_removal_journal_refuses_before_rebuild(self):
        self.first_install()
        self.partially_remove()
        record = self.root.read_json('transaction.json')
        self.root.write_json('transaction.json', dict(record, install_id='b' * 32))
        code, output, _ = self.cli('install', '--json')
        self.assertEqual(code, 10)
        self.assertEqual(json.loads(output)['code'], 'transaction-recovery-required')
        self.assertEqual(self.provider_builds, 1)

    def test_changed_package_message_gives_remove_then_same_id_install_commands(self):
        self.first_install()
        (self.source / 'deep32_relay/module.py').write_bytes(b'VALUE = 2\n')
        code, _, error = self.cli('install')
        self.assertEqual(code, 10)
        self.assertIn('code: remove-before-install', error)
        self.assertIn('connect --relay remove --relay-id ' + 'a' * 32, error)
        self.assertIn('connect --relay install --agent codex --relay-id ' + 'a' * 32, error)
        self.assert_preserved()

    def test_legacy_receipt_snapshots_config_before_remove(self):
        self.first_install('--relay-port', '7468')
        record = self.root.read_json('install.json')
        del record['config']
        self.root.write_json('install.json', record)
        self.assertEqual(self.cli('remove', '--json')[0], 0)
        self.assertEqual(self.root.read_json('install.json')['config']['port'], 7468)
        self.assertEqual(self.cli('install', '--json')[0], 0)
        self.assertEqual(self.root.read_json('config.json')['port'], 7468)

    def test_removed_legacy_receipt_recreates_config_from_binding_and_explicit_options(self):
        self.first_install()
        self.assertEqual(self.cli('remove', '--json')[0], 0)
        record = self.root.read_json('install.json')
        del record['config']
        self.root.write_json('install.json', record)
        self.assertEqual(self.cli('install', '--json', '--relay-port', '7468')[0], 0)
        self.assertEqual(self.root.read_json('config.json')['port'], 7468)
        self.assert_preserved()

    def test_explicit_default_port_overrides_retained_nondefault_port(self):
        self.first_install('--relay-port', '7468')
        self.assertEqual(self.cli('remove', '--json')[0], 0)
        self.assertEqual(self.cli('install', '--json', '--relay-port', '7452')[0], 0)
        self.assertEqual(self.root.read_json('config.json')['port'], 7452)

    def test_removed_tailnet_route_can_change_port_without_touching_other_routes(self):
        fake = FakeServe({'TCP': {'8443': {'HTTPS': True}}})
        self.install(transport='tailnet', port=7468, route_api=fake)
        self.assertTrue(connector.remove(self.root, route_api=fake)['removal']['complete'])
        self.install(transport='tailnet', port=7452, route_api=fake)
        self.assertEqual(fake.calls, [('add', 7468), ('remove', 7468), ('add', 7452)])
        self.assertEqual(fake.table['TCP']['8443'], {'HTTPS': True})
        self.assertNotIn('7468', fake.table['TCP'])

    def test_removed_route_record_cannot_claim_a_foreign_route_on_reinstall(self):
        fake = FakeServe({'TCP': {'8443': {'HTTPS': True}}})
        self.install(transport='tailnet', port=7468, route_api=fake)
        self.assertTrue(connector.remove(self.root, route_api=fake)['removal']['complete'])
        fake.table['TCP']['7468'] = {'TCPForward': '127.0.0.1:9999'}
        before = list(fake.calls)
        with self.assertRaisesRegex(StateError, 'foreign-serve-route'):
            self.install(transport='tailnet', port=7452, route_api=fake)
        self.assertEqual(fake.calls, before)
        self.assertEqual(fake.table['TCP']['7468'], {'TCPForward': '127.0.0.1:9999'})

    def test_crash_after_removed_receipt_before_journal_unlink_recovers(self):
        self.first_install()
        self.assertEqual(self.cli('remove', '--json')[0], 0)
        record = self.root.read_json('install.json')
        self.root.write_json('transaction.json', {'schema': 'deep32.relay-transaction.v1',
            'install_id': record['install_id'], 'release_id': record['release_id'], 'phase': 'removing'})
        self.assertEqual(self.cli('install', '--json')[0], 0)
        self.assertFalse(self.root.path('transaction.json').exists())
        self.assert_preserved()

    def test_changed_package_interrupted_prepare_then_retry(self):
        self.first_install()
        self.assertEqual(self.cli('remove', '--json')[0], 0)
        (self.source / 'deep32_relay/module.py').write_bytes(b'VALUE = 2\n')
        with patch.object(connector, '_adapter', side_effect=StateError('synthetic-registration-failure')):
            self.assertEqual(self.cli('install', '--json')[0], 10)
        self.assertEqual(self.root.read_json('transaction.json')['phase'], 'preparing')
        self.assertEqual(self.cli('install', '--json')[0], 0)
        self.assert_preserved()

    def test_named_local_io_codes_never_expose_exception_text(self):
        for exception, expected in ((FileNotFoundError, 'relay-file-missing'),
                (PermissionError, 'local-permission-denied'), (FileExistsError, 'relay-file-exists'),
                (NotADirectoryError, 'local-path-not-directory'), (IsADirectoryError, 'local-path-is-directory'),
                (TimeoutError, 'local-timeout')):
            with self.subTest(exception=exception):
                with patch.object(connector, 'state_base', side_effect=exception('SYNTHETIC_PRIVATE_DIAGNOSTIC')), \
                     patch('sys.stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(connector.main(['status', '--relay-id', 'a' * 32, '--json']), 10)
                self.assertEqual(json.loads(output.getvalue())['code'], expected)
                self.assertNotIn('SYNTHETIC_PRIVATE_DIAGNOSTIC', output.getvalue())


class WrapperPortR17(unittest.TestCase):
    setUp = wrappers.LoginWrappers.setUp
    record = wrappers.LoginWrappers.record

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell wrapper')
    def test_omitted_port_is_preserved_but_explicit_default_is_forwarded(self):
        import sys
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        fake = self.folder / 'synthetic-python.ps1'
        fake.write_text('& ' + quote(sys.executable) + ' -I -B ' + quote(self.recorder) +
            ' ' + quote(self.output) + ' @args\nexit $LASTEXITCODE\n', encoding='utf-8')
        self.env['DEEP32_CONNECT_PYTHON'] = str(fake)
        powershell = Path(self.env['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        for extra, forwarded in (([], []), (['-RelayPort', '7452'], ['--relay-port', '7452']),
                                 (['-RelayPort', '7468'], ['--relay-port', '7468'])):
            with self.subTest(extra=extra):
                completed = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive',
                    '-ExecutionPolicy', 'Bypass', '-File', str(wrappers.ROOT / 'adapters/connect.ps1'),
                    '-Relay', 'install', '-Agent', 'codex', *extra], cwd=self.folder, env=self.env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                self.assertEqual(completed.returncode, 0, 'PowerShell install wrapper failed')
                self.assertEqual(self.record(), ['--relay', 'install', '--agent', 'codex', *forwarded])
