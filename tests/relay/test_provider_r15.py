"""Approved-lock installation fakes and actual R12 memory-BIO handshakes."""
import hashlib
import os
from pathlib import Path
import ssl
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'adapters/relay'))
from deep32_relay import provider
from deep32_relay.pairing import PairingWindow, PinRecord
from deep32_relay.state import OwnedRoot, OWNER_SCHEMA, StateError
from deep32_relay.tls import load_provider, PeerPolicy, certificate_pin, provider_readiness
from test_install import FakeSecurity


class ProviderLock(unittest.TestCase):
    def test_approved_exact_lock_and_hash_count(self):
        record, lock = provider.approved_lock()
        self.assertEqual(record['hashes'], 165)
        self.assertEqual(hashlib.sha256(lock.read_bytes()).hexdigest(), record['requirements_sha256'])

    def test_installed_versions_are_required(self):
        with patch('importlib.metadata.version', side_effect=lambda name: provider.PACKAGES[name]):
            self.assertTrue(provider_readiness())
        with patch('importlib.metadata.version', return_value='0.0'):
            self.assertFalse(provider_readiness())

    def test_modified_lock_refused(self):
        with tempfile.TemporaryDirectory(dir=os.environ['DEEP32_RELAY_TEST_ROOT']) as folder:
            base = Path(folder)
            (base / 'tls-provider-lock.json').write_bytes(provider.LOCK.read_bytes())
            (base / 'requirements.lock').write_bytes(b'foreign==0\n')
            with self.assertRaises(StateError):
                provider.approved_lock(base / 'tls-provider-lock.json')

    def test_owned_venv_hashed_binary_install_and_exact_removal(self):
        with tempfile.TemporaryDirectory(dir=os.environ['DEEP32_RELAY_TEST_ROOT']) as folder:
            owner = OwnedRoot(Path(folder) / ('d' * 32), FakeSecurity())
            owner.create()
            owner.write_json('owner.json', {'schema': OWNER_SCHEMA, 'install_id': 'd' * 32})
            calls = []
            def run(argv):
                calls.append(argv)
                if 'venv' in argv:
                    binary = owner.root / 'provider' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                    binary.parent.mkdir(parents=True)
                    binary.write_bytes(b'synthetic executable')
            executable = provider.install_provider(owner, run=run)
            self.assertEqual(len(calls), 3)
            self.assertIn('--copies', calls[0])
            self.assertIn('--require-hashes', calls[1])
            self.assertEqual(calls[1][calls[1].index('--only-binary') + 1], ':all:')
            self.assertEqual(executable, provider.install_provider(owner, run=run))
            self.assertEqual(len(calls), 3)
            owner.mkdir('private/link')
            owner.write('private/link/synthetic-pin', b'keep')
            self.assertEqual(provider.remove_provider(owner), [])
            self.assertFalse(owner.path('provider').exists())
            self.assertEqual(owner.read('private/link/synthetic-pin'), b'keep')

    def test_provider_modification_refuses_removal(self):
        with tempfile.TemporaryDirectory(dir=os.environ['DEEP32_RELAY_TEST_ROOT']) as folder:
            owner = OwnedRoot(Path(folder) / ('e' * 32), FakeSecurity())
            owner.create(); owner.write_json('owner.json', {'schema': OWNER_SCHEMA, 'install_id': 'e' * 32})
            def run(argv):
                if 'venv' in argv:
                    target = owner.path('provider') / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                    target.parent.mkdir(parents=True); target.write_bytes(b'synthetic')
            provider.install_provider(owner, run=run)
            (owner.path('provider') / 'foreign').write_bytes(b'foreign')
            with self.assertRaises(StateError): provider.remove_provider(owner)

    def test_partial_provider_removal_readback_and_retry(self):
        with tempfile.TemporaryDirectory(dir=os.environ['DEEP32_RELAY_TEST_ROOT']) as folder:
            owner = OwnedRoot(Path(folder) / ('f' * 32), FakeSecurity())
            owner.create(); owner.write_json('owner.json', {'schema': OWNER_SCHEMA, 'install_id': 'f' * 32})
            def run(argv):
                if 'venv' in argv:
                    binary = owner.path('provider') / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                    binary.parent.mkdir(parents=True); binary.write_bytes(b'synthetic')
                    (binary.parent / 'other').write_bytes(b'synthetic other')
            provider.install_provider(owner, run=run)
            unlink = Path.unlink
            def ignored(path, *args, **kwargs):
                if path.name == 'other': return None
                return unlink(path, *args, **kwargs)
            with patch.object(Path, 'unlink', ignored):
                remaining = provider.remove_provider(owner)
            self.assertEqual(len(remaining), 1)
            self.assertTrue(remaining[0].endswith('/other'))
            self.assertTrue(owner.path(provider.RECEIPT).exists())
            self.assertEqual(provider.remove_provider(owner), [])
            self.assertFalse(owner.path('provider').exists())


@unittest.skipUnless(provider_readiness(), 'approved provider absent from this interpreter; run under approved venv')
class ActualR12(unittest.TestCase):
    def setUp(self):
        self.provider = load_provider()
        self.relay = self.provider.generate_identity()
        self.node = self.provider.generate_identity()

    def connect(self, policy, *, certificate=True, tls12=False):
        from types import SimpleNamespace
        SSL, crypto = self.provider.SSL, self.provider.crypto
        policy.begin_attempt()
        identity = SimpleNamespace(certificate_pem=self.relay[0], private_key_pem=self.relay[1])
        server_context = self.provider.context(identity, policy)
        client_context = SSL.Context(SSL.TLS_CLIENT_METHOD)
        version = SSL.TLS1_2_VERSION if tls12 else SSL.TLS1_3_VERSION
        client_context.set_min_proto_version(version); client_context.set_max_proto_version(version)
        client_context.set_verify(SSL.VERIFY_NONE, lambda *_: True)
        if certificate:
            client_context.use_certificate(crypto.load_certificate(crypto.FILETYPE_PEM, self.node[0]))
            client_context.use_privatekey(crypto.load_privatekey(crypto.FILETYPE_PEM, self.node[1]))
        server, client = SSL.Connection(server_context, None), SSL.Connection(client_context, None)
        server.set_accept_state(); client.set_connect_state()
        done = [False, False]
        for _ in range(100):
            for index, connection in enumerate((server, client)):
                if not done[index]:
                    try: connection.do_handshake(); done[index] = True
                    except (SSL.WantReadError, SSL.WantWriteError): pass
            for source, destination in ((server, client), (client, server)):
                try: destination.bio_write(source.bio_read(65536))
                except SSL.WantReadError: pass
            if all(done):
                der = self.provider.peer_der(server)
                self.assertTrue(policy.accept_der(der))
                return server, client
        raise AssertionError('handshake incomplete')

    def test_armed_unknown_accepted_tls13(self):
        server, _ = self.connect(PeerPolicy(window=PairingWindow(time.monotonic())))
        self.assertEqual(server.get_protocol_version_name(), 'TLSv1.3')

    def test_unarmed_unknown_refused_before_handshake(self):
        from deep32_relay.wire import ProtocolError
        with self.assertRaises(ProtocolError): self.connect(PeerPolicy())

    def peer(self, pin=None):
        from deep32_relay.tls import pem_der
        return PinRecord('1' * 32, pin or certificate_pin(pem_der(self.node[0])))

    def test_pinned_accepted(self):
        self.connect(PeerPolicy(self.peer()))

    def test_wrong_pin_refused_even_when_armed(self):
        with self.assertRaises(self.provider.SSL.Error):
            self.connect(PeerPolicy(self.peer(b'X' * 32), PairingWindow(time.monotonic())))

    def test_missing_client_certificate_refused(self):
        with self.assertRaises(self.provider.SSL.Error):
            self.connect(PeerPolicy(window=PairingWindow(time.monotonic())), certificate=False)

    def test_tls12_refused(self):
        with self.assertRaises(self.provider.SSL.Error):
            self.connect(PeerPolicy(window=PairingWindow(time.monotonic())), tls12=True)
