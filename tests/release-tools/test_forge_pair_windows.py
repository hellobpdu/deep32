#!/usr/bin/env python3
"""Offline Windows C8 adapter checks with synthetic state and process doubles."""

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

TREE = Path(__file__).resolve().parents[2]
SOURCE = TREE / 'src/release-tools/forge_pair_windows.py'
SPEC = importlib.util.spec_from_file_location('forge_pair_windows_under_test', SOURCE)
pair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pair)
SCRATCH = None
HOST = 'forge.synthetic-test.ts.net'
ID = 'a' * 16
PORT = 17453
ENDPOINTS = {'embeddings': f'https://{HOST}', 'reranker': f'https://{HOST}:8443'}
PIN = 'b' * 64
SETTINGS = {'transport': 'tailnet', 'publish': 'serve-https', 'forge_accept_ct': True,
            'forge_ports': {'pairing': PORT}}


class Pipe(io.BytesIO):
    def close(self):
        self.bytes_written = self.getvalue()
        super().close()


class Helper:
    def __init__(self, *, failed=False, timeout=False, ready=True):
        self.stdin = Pipe()
        self.failed = failed
        self.timeout = timeout
        self.ready = ready
        self.done = False
        self.terminated = False
        self.polls = 0

    def poll(self):
        self.polls += 1
        if not self.done and self.ready and hasattr(self.stdin, 'bytes_written'):
            request = json.loads(self.stdin.bytes_written)
            path = Path(request['ready_file'])
            if not path.exists():
                path.write_text(json.dumps({'port': PORT, 'pin': PIN}), encoding='ascii')
        return int(self.failed) if self.done else None

    def wait(self, timeout):
        if self.timeout and not self.terminated:
            raise subprocess.TimeoutExpired('synthetic-helper', timeout)
        self.done = True
        return int(self.failed)

    def terminate(self):
        self.terminated = True


class WindowsForgePairing(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=SCRATCH, prefix='synthetic-windows-c8-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.records = []
        for name in ('status', 'private', 'pairing', 'config'):
            (self.root / name).mkdir()
        for name in ('forge-token', 'kb-public.pem'):
            (self.root / 'private' / name).write_text('synthetic fixture only', encoding='ascii')
        self.window = self.root / 'status' / pair.STATUS_FILE
        self.installed = {'install_id': ID, 'settings': SETTINGS}
        self.publication = pair.module('_sealed_publication_fixture', TREE / 'src/gpu-pack/forms/forge_publication.py')

    def acl(self, path, *, verify=False, directory=False):
        self.assertEqual(directory, path.is_dir())
        self.records.append((path, verify, directory))

    def helper(self, child):
        def launch(args, **kwargs):
            self.assertEqual(['-I', '-B', '-c'], args[1:4])
            self.assertIn('SO_EXCLUSIVEADDRUSE', args[4])
            self.assertIn('class InheritedPath', args[4])
            self.assertEqual(subprocess.PIPE, kwargs['stdin'])
            self.assertEqual(subprocess.DEVNULL, kwargs['stdout'])
            self.assertEqual(subprocess.DEVNULL, kwargs['stderr'])
            self.assertNotIn('TS_SOCKET', kwargs['env'])
            return child
        return launch

    def respond(self, child, emit, **kwargs):
        pair.respond(self.root, TREE / 'src/gpu-pack/pairing', self.root / 'pairing',
                     PORT, ENDPOINTS, 'synthetic-openssl.exe', self.acl,
                     popen=self.helper(child), emit=emit, **kwargs)

    def test_protected_machine_only_code_after_readiness_and_cleanup(self):
        child = Helper()
        shown = []
        self.window.write_text('obsolete synthetic fixture', encoding='ascii')

        def ready(message):
            shown.append(message)
            request = json.loads(child.stdin.bytes_written)
            self.assertEqual(request['code'] + '\n', self.window.read_text(encoding='ascii'))
            self.assertEqual(300, request['expires_seconds'])
            self.assertEqual(1, request['attempts'])
            self.assertEqual(ENDPOINTS, request['model_endpoints'])
            self.assertNotIn(request['code'], message)
            self.assertIn((self.window, True, False), self.records)

        self.respond(child, ready)
        self.assertFalse(self.window.exists())
        self.assertEqual([], list((self.root / 'status').iterdir()))
        self.assertEqual(['The protected Windows Forge pairing window is ready.'], shown)

    def test_failed_commit_and_timeout_both_remove_the_window(self):
        for child, error in ((Helper(failed=True), pair.Refusal), (Helper(timeout=True), subprocess.TimeoutExpired)):
            with self.subTest(error=error), self.assertRaises(error):
                self.respond(child, lambda _: self.assertTrue(self.window.exists()))
            self.assertFalse(self.window.exists())
            self.assertEqual([], list((self.root / 'status').iterdir()))
        self.assertTrue(child.terminated)

    def test_missing_listener_never_discloses_a_window(self):
        child = Helper(ready=False)
        samples = iter((0, 126))
        with self.assertRaisesRegex(pair.Refusal, 'listener could not open'):
            self.respond(child, lambda _: self.fail('unready response published'),
                         clock=lambda: next(samples), sleep=lambda _: None)
        self.assertTrue(child.terminated)
        self.assertFalse(self.window.exists())

    def routed(self, routes=None):
        routes = self.publication.plan(HOST, SETTINGS) if routes is None else routes
        config = {}
        client = self.publication.Publication(SimpleNamespace(), self.root, 'windows', None)
        for row in routes:
            for key, values in client.expected(HOST, row).items():
                config.setdefault(key, {}).update(values)
        return routes, config

    def journal(self, routes, owner=ID):
        content = {'schema': 'deep32.forge-publication/1', 'install_id': owner, 'host': HOST,
                   'routes': routes, 'added': [0, 1, 2], 'pending': None, 'removing': None, 'phase': 'ready'}
        (self.root / 'config/forge-publication.json').write_text(json.dumps(content), encoding='ascii')

    def observed(self, config):
        def run(argv, **kwargs):
            self.assertIn(argv, (['synthetic-tailscale.exe', 'status', '--json'],
                                 ['synthetic-tailscale.exe', 'serve', 'status', '--json']))
            status = {'BackendState': 'Running', 'Self': {'DNSName': HOST + '.', 'Online': True},
                      'CurrentTailnet': {'MagicDNSEnabled': True}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(status if argv[1] == 'status' else config).encode())
        return run

    def test_only_exact_committed_routes_are_accepted(self):
        routes, config = self.routed()
        self.journal(routes)
        result = pair.published(self.root, self.installed, self.publication, self.acl,
                                'synthetic-tailscale.exe', self.observed(config))
        self.assertEqual((PORT, ENDPOINTS), result)
        self.journal(routes, owner='c' * 16)
        with self.assertRaisesRegex(pair.Refusal, 'same ready'):
            pair.published(self.root, self.installed, self.publication, self.acl,
                           'synthetic-tailscale.exe', self.observed(config))
        self.journal(routes)
        config['TCP'].pop(str(routes[2]['port']))
        with self.assertRaisesRegex(pair.Refusal, 'exact owned'):
            pair.published(self.root, self.installed, self.publication, self.acl,
                           'synthetic-tailscale.exe', self.observed(config))

    def test_signed_pairing_bytes_must_match_retained_kit(self):
        source = self.root / 'signed'
        kit = self.root / 'kit'
        for folder in (source, kit / 'pairing'):
            (folder / 'deep32net').mkdir(parents=True)
            (folder / 'bin').mkdir()
            (folder / 'bin/pairing-helper').write_bytes(b'synthetic helper\n')
            (folder / 'deep32net/cli.py').write_bytes(b'synthetic module\n')
        self.assertEqual(source, pair.trusted_pairing(kit, source))
        (kit / 'pairing/deep32net/cli.py').write_bytes(b'different module\n')
        with self.assertRaisesRegex(pair.Refusal, 'differs from the signed'):
            pair.trusted_pairing(kit, source)

    @unittest.skipUnless(os.name == 'nt', 'native NTFS ACL test')
    def test_native_acl_inheritance_preserves_owner_and_system(self):
        security = pair.module('_native_security_fixture', TREE / 'src/gpu-pack/forms/host_security.py')
        acl = pair.restrict_acl(security)
        protected = self.root / 'acl-protected'
        protected.mkdir()
        acl(protected, directory=True)
        inherited = protected / 'c8-state' / 'nested'
        helper = pair.EXCLUSIVE_HELPER.rsplit('raise SystemExit', 1)[0]
        helper += '\nfiles.private_dir(sys.argv[2])\nfiles.atomic_write(files.safe(sys.argv[2]) / "fixture", b"synthetic")'
        subprocess.run([sys.executable, '-I', '-B', '-c', helper, str(TREE / 'src/gpu-pack/pairing'),
                        str(inherited)], check=True, timeout=15, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        for path in (protected, inherited.parent, inherited):
            pair.protected(acl, path, directory=True)
        pair.protected(acl, inherited / 'fixture')

    def test_accepts_only_durable_matching_c8_handover(self):
        state = self.root / 'pairing'
        (state / 'identity').mkdir()
        record = {'kind': 'forge', 'side': 'joined', 'peer_role': 'workstation',
                  'own_pin': PIN, 'peer_pin': 'd' * 64, 'transport': 'tailnet', 'publish': 'serve-https',
                  'received': [], 'given': ['forge-token', 'kb-public.pem', 'model-endpoints.json']}
        claim = SimpleNamespace(in_progress=0)
        modules = SimpleNamespace(store=SimpleNamespace(list_peers=lambda _: [record]),
                                  pairlock=SimpleNamespace(load=lambda _: claim),
                                  identity=SimpleNamespace(ensure_identity=lambda _, kind: SimpleNamespace(pin=PIN)))
        with self.assertRaisesRegex(pair.Refusal, 'incomplete'):
            pair.committed(state, modules)
        (state / 'pairing').mkdir()
        (state / 'pairing/forge-joined.pairlock').write_text('synthetic fixture only', encoding='ascii')
        self.assertTrue(pair.committed(state, modules))
        claim.in_progress = 1
        with self.assertRaisesRegex(pair.Refusal, 'incomplete'):
            pair.committed(state, modules)
        claim.in_progress = 0
        record['own_pin'] = 'c' * 64
        with self.assertRaisesRegex(pair.Refusal, 'peer record differs'):
            pair.committed(state, modules)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', required=True, type=Path)
    options, remaining = parser.parse_known_args()
    SCRATCH = options.scratch_root.resolve()
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
