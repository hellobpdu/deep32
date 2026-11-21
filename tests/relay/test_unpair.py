"""Relay unpair: explicit local removal of the paired Workstation and nothing else.

Synthetic owned roots, fake TLS and recorder wrappers only; no socket, service,
native agent, provider or model is used.
"""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_install as existing
import test_login_wrappers_r16 as wrappers
import test_transport as fake_tls
from deep32_relay import connector, status
from deep32_relay.identity import IdentityStore
from deep32_relay.pairing import PairingWindow, PinRecord
from deep32_relay.state import OwnedRoot, StateError
from deep32_relay.tls import OpenSSLProvider, PeerPolicy, certificate_pin
from deep32_relay.transport import Listener, pair_console

OLD = PinRecord('synthetic-old-workstation', b'O' * 32)
NEW = PinRecord('synthetic-new-workstation', b'N' * 32)
IDENT = 'a' * 32


class KeyProtector:
    """Unpair never reads the relay key; keep DPAPI out of the unit test."""
    def protect(self, value):
        return b'synthetic-wrapped:' + value

    def unprotect(self, value):
        return value[len(b'synthetic-wrapped:'):]


class Unpair(unittest.TestCase):
    setUp = existing.Installation.setUp
    tearDown = existing.Installation.tearDown
    install = existing.Installation.install

    def store(self):
        return IdentityStore(self.root, KeyProtector())

    def paired(self):
        """An installed relay with its own identity, a signed-in profile and a stored pin."""
        self.install()
        for name, raw in (('private/link/node.key', b'SYNTHETIC_RELAY_KEY_KEEP'),
                          ('private/link/node.crt', b'SYNTHETIC_RELAY_CERT_KEEP'),
                          ('private/link/node.id', b'relay-' + b'0' * 32 + b'\n'),
                          ('profiles/codex/synthetic-account', b'SYNTHETIC_SIGN_IN_KEEP')):
            self.root.write(name, raw)
        self.store().save_peer(OLD)

    def files(self):
        # Lock files are lease markers, never state.
        return {path.relative_to(self.root.root).as_posix(): path.read_bytes()
                for path in sorted(self.root.root.rglob('*'))
                if path.is_file() and path.suffix != '.lock'}

    def publish(self, **fields):
        receipt = connector.validate_receipt(self.root)
        snapshot = dict(status.unavailable_snapshot(receipt['install_id'], receipt['agent'],
                                                    receipt['release_id']), **fields)
        status.publish_status(self.root, snapshot)
        return snapshot

    def gone_service(self, **fields):
        # systemctl stop ends the relay with SIGTERM: its last snapshot keeps a pid,
        # running=true and the paired link; that pid is no longer the relay.
        live = status.process_identity(os.getpid())
        other = {'windows': 'windows-' + '0' * 16, 'linux': 'linux-1', 'macos': 'macos-1-000000'}[live.split('-')[0]]
        return self.publish(pid=os.getpid(), instance_id=other, running=True, paired_workstation=True,
                            workstation_alias='workstation', link_state='connected',
                            observed_at=time.time(), **fields)

    def test_unpair_removes_the_pin_and_keeps_identity_profile_and_receipt(self):
        self.paired()
        snapshot = self.gone_service()
        before = self.files()
        result = connector.unpair(self.root)
        self.assertEqual((result['paired'], result['changed'], result['install_id']), (False, True, IDENT))
        self.assertNotIn(OLD.peer_id, json.dumps(result))
        self.assertFalse((self.root.root / 'private/link/peer.pin').exists())
        self.assertIsNone(self.store().load_peer())
        after = self.files()
        kept = {name: raw for name, raw in before.items()
                if name not in ('private/link/peer.pin', 'runtime/status.json')}
        self.assertEqual({name: raw for name, raw in after.items() if name != 'runtime/status.json'}, kept)
        for name in ('owner.json', 'install.json', 'config.json', 'private/link/node.key',
                     'private/link/node.crt', 'private/link/node.id', 'profiles/codex/synthetic-account',
                     'private/qualification/codex.json'):
            self.assertIn(name, kept)
        # Only the pairing fields of the saved observation change.
        self.assertEqual(self.root.read_json('runtime/status.json'),
                         dict(snapshot, paired_workstation=False, workstation_alias='none'))
        observed = connector.status(self.root)
        self.assertIn('pairing-required', observed['unavailable_reasons'])
        self.assertFalse(observed['supervisor']['snapshot']['paired_workstation'])
        self.assertEqual(observed['supervisor']['snapshot']['workstation_alias'], 'none')

    def test_unpair_refuses_while_the_service_runs(self):
        self.paired()
        self.publish(pid=os.getpid(), instance_id=status.process_identity(os.getpid()), running=True,
                     paired_workstation=True, workstation_alias='workstation', observed_at=time.time())
        before = self.files()
        with self.assertRaisesRegex(StateError, 'relay-stop-required'):
            connector.unpair(self.root)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.store().load_peer(), OLD)

    def test_unpair_refuses_while_the_service_lease_is_held(self):
        # A hung service can leave a stale snapshot; its lease is still the guard.
        self.paired()
        self.gone_service()
        before = self.files()
        with self.root.lock('service.lock'):
            with self.assertRaisesRegex(StateError, 'relay-stop-required'):
                connector.unpair(self.root)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.store().load_peer(), OLD)

    def test_unpair_refuses_a_pending_install_or_removal(self):
        self.paired()
        self.root.write_json('transaction.json', {'synthetic': True})
        with self.assertRaisesRegex(StateError, 'transaction-recovery-required'):
            connector.unpair(self.root)
        self.assertEqual(self.store().load_peer(), OLD)

    def test_unpair_is_idempotent(self):
        self.install()
        before = self.files()
        for _ in range(2):
            result = connector.unpair(self.root)
            self.assertEqual((result['paired'], result['changed']), (False, False))
        self.assertEqual(self.files(), before)
        self.assertFalse((self.root.root / 'runtime/status.json').exists())
        self.store().save_peer(OLD)
        self.assertTrue(connector.unpair(self.root)['changed'])
        once = self.files()
        self.assertFalse(connector.unpair(self.root)['changed'])
        self.assertEqual(self.files(), once)

    def test_after_unpair_the_pin_store_accepts_a_new_workstation(self):
        self.paired()
        with self.assertRaisesRegex(StateError, 'peer-revocation-required'):
            self.store().save_peer(NEW)
        self.assertEqual(self.store().load_peer(), OLD)
        connector.unpair(self.root)
        self.store().save_peer(NEW)
        self.assertEqual(self.store().load_peer(), NEW)

    def test_unpair_after_remove_keeps_the_removal_receipt(self):
        # Removal keeps pins by design; the relay can forget its Workstation
        # before the next install.
        self.paired()
        self.assertEqual(connector.remove(self.root)['state'], 'removed')
        receipt = self.root.read('install.json')
        self.assertTrue(connector.unpair(self.root)['changed'])
        self.assertIsNone(self.store().load_peer())
        self.assertEqual(self.root.read('install.json'), receipt)
        self.assertEqual(self.root.read('private/link/node.crt'), b'SYNTHETIC_RELAY_CERT_KEEP')

    def cli(self, *args, executable=None):
        output, error = io.StringIO(), io.StringIO()
        fixture_security = self.security
        class FixtureRoot(OwnedRoot):
            def __init__(self, path, security=None):
                super().__init__(path, security or fixture_security)
        with ExitStack() as stack:
            stack.enter_context(patch.object(connector, 'state_base', return_value=self.base))
            stack.enter_context(patch.object(connector, 'OwnedRoot', FixtureRoot))
            if executable is not None:
                stack.enter_context(patch('sys.executable', executable))
            for target in ('socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
                           'subprocess.Popen', 'subprocess.run'):
                stack.enter_context(patch(target, side_effect=AssertionError('offline effect refused')))
            stack.enter_context(patch('sys.stdout', output))
            stack.enter_context(patch('sys.stderr', error))
            code = connector.main(list(args))
        return code, output.getvalue(), error.getvalue()

    def cli_unpair_twice(self, executable):
        # Pair and selftest re-run in the owned provider; unpair needs no provider
        # and runs where it is started. subprocess.run is refused in cli().
        self.paired()
        self.gone_service()
        code, output, error = self.cli('unpair', '--relay-id', IDENT, '--agent', 'codex',
                                       executable=executable)
        self.assertEqual((code, output, error), (0, 'Relay is no longer paired with a Workstation.\n', ''))
        self.assertIsNone(self.store().load_peer())
        self.assertFalse(self.root.read_json('runtime/status.json')['paired_workstation'])
        code, output, error = self.cli('unpair', '--relay-id', IDENT, executable=executable)
        self.assertEqual((code, output, error),
                         (0, 'Relay was not paired with a Workstation; nothing changed.\n', ''))

    def test_cli_unpair_from_the_source_kit_interpreter(self):
        self.assertNotEqual(os.path.normcase(os.path.abspath(sys.executable)),
                            os.path.normcase(os.path.abspath(self.python)))
        self.cli_unpair_twice(None)

    def test_cli_unpair_from_the_installed_interpreter(self):
        self.cli_unpair_twice(str(self.python))

    def test_cli_unpair_json_and_implicit_selection(self):
        self.paired()
        code, output, error = self.cli('unpair', '--agent', 'codex', '--json')
        self.assertEqual((code, error), (0, ''))
        value = json.loads(output)
        self.assertEqual((value['schema'], value['paired'], value['changed'], value['install_id']),
                         ('deep32.relay-unpair-result.v1', False, True, IDENT))
        self.assertNotIn(OLD.peer_id, output)
        self.assertNotIn(OLD.pin.hex(), output)

    def test_cli_unpair_refusal_names_the_stop(self):
        self.paired()
        self.publish(pid=os.getpid(), instance_id=status.process_identity(os.getpid()), running=True,
                     observed_at=time.time())
        code, output, error = self.cli('unpair', '--relay-id', IDENT, '--agent', 'codex')
        self.assertEqual((code, output), (10, ''))
        self.assertIn('(code: relay-stop-required)', error)
        self.assertIn('Stop the relay service, then run this command again.', error)
        self.assertEqual(self.store().load_peer(), OLD)

    def test_cli_unpair_refuses_install_options(self):
        self.paired()
        for options in (('--relay-transport', 'loopback'), ('--no-autostart',), ('--live',),
                        ('--agent-binary', str(self.agent))):
            with self.subTest(options=options):
                code, _, _ = self.cli('unpair', '--relay-id', IDENT, *options)
                self.assertEqual(code, 10)
                self.assertEqual(self.store().load_peer(), OLD)

    def test_pairing_refusals_name_the_unpair_fix_and_keep_their_codes(self):
        self.install()
        for code_name, first in (('peer-already-paired', 'that Workstation connected while pairing was open'),
                                 ('peer-revocation-required', 'still paired with another Workstation')):
            with self.subTest(code=code_name), \
                    patch.object(connector, 'pair', side_effect=StateError(code_name)):
                # The owned interpreter runs pair itself; no re-execution here.
                code, output, error = self.cli('pair', '--relay-id', IDENT, '--agent', 'codex',
                                               executable=str(self.python))
                self.assertEqual((code, output), (10, ''))
                self.assertIn('(code: ' + code_name + ')', error)
                self.assertIn(first, error)
                self.assertIn('d32-chatctl unpair', error)
                self.assertIn('or turn it off', error)
                self.assertIn('connect --relay unpair --relay-id ' + IDENT + ' --agent codex and pair again.', error)

    def test_pair_console_ends_when_the_stored_pin_refuses_another_workstation(self):
        self.install()
        clock = fake_tls.Clock()
        class Raw(fake_tls.Raw):
            def accept(self):
                if not self.accepts:
                    raise BlockingIOError()
                return self.accepts.pop(0)
        listening, accepted = Raw(), Raw()
        listening.accepts = [(accepted, ('127.0.0.1', 12345))]
        library = fake_tls.SSLLibrary(b'synthetic new Workstation DER')
        stored = PinRecord('synthetic-old-workstation', certificate_pin(b'synthetic old Workstation DER'))
        saved, output = [], []
        def wait(read, write, errors, timeout):
            # Each poll costs one second, so a console that ignores the refusal
            # times out after its five minutes instead of looping forever.
            clock.now += 1
            return fake_tls.ready_wait(read, write, errors, timeout)
        listener = Listener(fake_tls.identity(), stored, provider=OpenSSLProvider(library, fake_tls.CRYPTO),
                            port=17452, persist_peer=saved.append, socket_factory=lambda *a: listening,
                            clock=clock, wait=wait)
        with self.assertRaisesRegex(StateError, 'peer-revocation-required'):
            pair_console(self.root, listener=listener, input_fn=lambda: None, output_fn=output.append)
        self.assertTrue(listener.other_peer_refused)
        self.assertTrue(accepted.closed)
        self.assertTrue(listening.closed)
        self.assertEqual(saved, [])
        self.assertLess(clock(), listener.window.deadline)
        self.assertFalse(listener.window.consumed)

    def test_peer_policy_marks_only_a_refused_different_certificate(self):
        clock = fake_tls.Clock()
        stored = PinRecord('synthetic-old-workstation', certificate_pin(b'synthetic old DER'))
        policy = PeerPolicy(stored, PairingWindow(clock()), clock=clock)
        policy.begin_attempt()
        self.assertTrue(policy.accept_der(b'synthetic old DER'))
        self.assertFalse(policy.other_peer)
        self.assertFalse(policy.accept_der(b'synthetic new DER'))
        self.assertTrue(policy.other_peer)
        unpaired = PeerPolicy(None, PairingWindow(clock()), clock=clock)
        unpaired.begin_attempt()
        self.assertTrue(unpaired.accept_der(b'synthetic new DER'))
        self.assertFalse(unpaired.other_peer)


class UnpairWrappers(unittest.TestCase):
    """Linux and Windows wrappers forward the unpair action literally."""
    setUp = wrappers.LoginWrappers.setUp
    record = wrappers.LoginWrappers.record

    def test_shell_forwards_literal_unpair(self):
        bash = Path(os.environ.get('D32_GIT_BASH', 'C:/Program Files/Git/bin/bash.exe'))
        if not bash.is_absolute() or not bash.is_file():
            self.skipTest('explicit Git Bash unavailable')
        self.env['PATH'] = os.pathsep.join((str(bash.parent.parent / 'usr/bin'),
            str(bash.parent), self.env['PATH']))
        fake = self.folder / 'synthetic-python.sh'
        fake.write_text('#!/bin/sh\nexec ' + shlex.quote(Path(sys.executable).as_posix()) +
            ' -I -B ' + shlex.quote(self.recorder.as_posix()) + ' ' +
            shlex.quote(self.output.as_posix()) + ' "$@"\n', encoding='utf-8')
        fake.chmod(0o700)
        self.env['DEEP32_CONNECT_PYTHON'] = fake.as_posix()
        completed = subprocess.run([str(bash), '--noprofile', '--norc',
            str(wrappers.ROOT / 'adapters/connect.sh'), '--relay', 'unpair', '--relay-id', IDENT, '--agent', 'claude'],
            cwd=self.folder, env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(completed.returncode, 0, 'shell unpair wrapper failed')
        self.assertEqual(self.record(), ['--relay', 'unpair', '--relay-id', IDENT, '--agent', 'claude'])

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell wrapper')
    def test_powershell_accepts_and_forwards_unpair(self):
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        fake = self.folder / 'synthetic-python.ps1'
        fake.write_text('& ' + quote(sys.executable) + ' -I -B ' + quote(self.recorder) +
            ' ' + quote(self.output) + ' @args\nexit $LASTEXITCODE\n', encoding='utf-8')
        self.env['DEEP32_CONNECT_PYTHON'] = str(fake)
        powershell = Path(self.env['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        completed = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File', str(wrappers.ROOT / 'adapters/connect.ps1'),
            '-Relay', 'unpair', '-RelayId', IDENT, '-Agent', 'codex'], cwd=self.folder, env=self.env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(completed.returncode, 0, 'PowerShell unpair wrapper failed')
        self.assertEqual(self.record(), ['--relay', 'unpair', '--relay-id', IDENT, '--agent', 'codex'])


if __name__ == '__main__':
    unittest.main()
