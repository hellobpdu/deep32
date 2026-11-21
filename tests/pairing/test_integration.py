#!/usr/bin/env python3
"""Synthetic cross-node pairing, protected enrolment and forwarding conformance.

Usage: python -I -B tests/pairing/test_integration.py --tmp SCRATCH
Only the accepted suite's guarded loopback TLS transport is permitted. The
forwarded HTTP connection is a local in-process stand-in, with no socket.
"""
import argparse
import base64
import hmac
import importlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
WS_ROOT = REPO / 'src/workstation/distrib/site/usr/local/d32'
FORGE_ROOT = REPO / 'src/gpu-pack/pairing'
sys.path.insert(0, str(HERE / 'reference/tests'))
from guard import install
install()


def package(name, path):
    spec = importlib.util.spec_from_file_location(name, path / '__init__.py',
                                                submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return types.SimpleNamespace(**{key: importlib.import_module(name + '.' + key)
                                  for key in ('pairing', 'store', 'cli', 'window', 'errors')})


WORKSTATION = package('_synthetic_ws_pairing', WS_ROOT / 'deep32net')
FORGE = package('_synthetic_forge_pairing', FORGE_ROOT / 'deep32net')
CODE = 'KLMNP23456'
WRONG_CODE = 'KLMNP23457'


class Clock:
    def __init__(self):
        self.now = 1700000000

    def time(self):
        return self.now

    def monotonic(self):
        return time.monotonic()


class Response:
    status = 200
    headers = {'content-type': 'application/json'}
    will_close = False

    def read(self):
        return b'{}'


class DoorConnection:
    """Capture the exact outgoing HTTP headers without printing credentials."""
    sock = object()

    def __init__(self):
        self.requests = []

    def putrequest(self, method, path, **kwargs):
        self.current = {'method': method, 'path': path, 'headers': []}
        self.requests.append(self.current)

    def putheader(self, name, value):
        self.current['headers'].append((name, value))

    def endheaders(self, body):
        self.current['body'] = body

    def getresponse(self):
        return Response()

    def close(self):
        self.sock = None


def forwarder_module():
    # Service boot/confinement is an OpenBSD facility; HTTP forwarding remains
    # the actual shipped module and is exercised without calling service boot.
    path = REPO / 'src/workstation/node/shims/py/serve_accel.py'
    spec = importlib.util.spec_from_file_location('_synthetic_accel_integration', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'d32svc': types.ModuleType('d32svc')}):
        spec.loader.exec_module(module)
    return module


class CrossNodeIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-cross-node-', dir=os.environ['TMP'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.forge_state = self.root / 'forge'
        self.ws_state = self.root / 'workstation'
        self.token = self.root / 'synthetic-forge-token'
        self.token.write_text('7b' * 32 + '\n', encoding='ascii', newline='\n')
        self.public = self.root / 'synthetic-kb-public.pem'
        der = bytes.fromhex('302a300506032b6570032100') + bytes([17]) * 32
        self.public.write_text('-----BEGIN PUBLIC KEY-----\n' + base64.b64encode(der).decode('ascii')
                               + '\n-----END PUBLIC KEY-----\n', encoding='ascii', newline='\n')
        self.openssl = os.environ.get('DEEP32_OPENSSL') or shutil.which('openssl')
        self.assertIsNotNone(self.openssl, 'openssl is required for synthetic local identities')
        self.clock = Clock()

    def exchange(self, code=CODE, success=True, model_endpoints=None):
        window = FORGE.window.PairingWindow(self.forge_state, 'forge-joined', seconds=30, clock=self.clock)
        server = FORGE.pairing.JoinedSide('forge', self.forge_state, CODE, window=window,
                                        transport='loopback', port=0, token_path=self.token,
                                        kb_public_path=self.public, openssl=self.openssl,
                                        model_endpoints=model_endpoints)
        ready, box = threading.Event(), {}
        server.on_ready = lambda port: ready.set()

        def serve():
            try:
                box['result'] = server.serve()
            except BaseException as exc:
                box['error'] = exc
            finally:
                ready.set()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        def cleanup():
            if thread.is_alive():
                server.window.deadline = server.window.clock.monotonic()
                thread.join(11)
            self.assertFalse(thread.is_alive(), 'pairing listener did not stop')

        self.addCleanup(cleanup)
        self.assertTrue(ready.wait(10), 'pairing listener did not become ready')
        self.assertIsNotNone(server.bound_port, 'pairing listener failed before binding')
        window_b = WORKSTATION.window.PairingWindow(self.ws_state, 'forge-joiner', seconds=30, clock=self.clock)
        joiner = WORKSTATION.pairing.Joiner('forge', self.ws_state, code, window=window_b,
                                         transport='loopback', port=server.bound_port,
                                         openssl=self.openssl)
        if success:
            result = joiner.join()
        else:
            with self.assertRaises(WORKSTATION.errors.NetError) as caught:
                joiner.join()
            self.assertEqual(caught.exception.exit_code, 5)
            result = None
        thread.join(15)
        self.assertFalse(thread.is_alive(), 'pairing listener did not finish')
        if success:
            if 'error' in box:
                raise box['error']
            self.assertEqual(result['peer_id'], server.identity.id)
            a = FORGE.store.list_peers(self.forge_state)[0]
            b = WORKSTATION.store.list_peers(self.ws_state)[0]
            self.assertEqual(a['peer_pin'], b['own_pin'])
            self.assertEqual(b['peer_pin'], a['own_pin'])
        else:
            self.assertIsInstance(box.get('error'), FORGE.errors.NetError)
            self.assertEqual(box['error'].exit_code, 5)
        return server, joiner, result

    def enrol_and_forward(self, peer_id):
        enrol = importlib.import_module('_synthetic_ws_pairing.enrol')
        system = self.root / 'synthetic-system-root'
        system.mkdir(exist_ok=True)
        result = enrol.enrol_forge(self.ws_state, peer_id, root=system)
        received = system / 'etc/d32/private/forge.token'
        self.assertTrue(hmac.compare_digest(received.read_bytes().strip(), self.token.read_bytes().strip()),
                        'enrolled credential differs from authenticated handover')
        self.assertEqual((system / 'etc/d32/state/forge-kb-public.pem').read_bytes(), self.public.read_bytes())
        self.assertFalse(self.token.read_text().strip() in json.dumps(result), 'public enrolment result leaks bearer')
        accel = forwarder_module()
        settings = {'GPU_EXT_EMBED_MODEL_ID': 'synthetic-embed',
                    'GPU_EXT_RERANK_MODEL_ID': 'synthetic-rerank'}
        for line in (system / 'etc/d32/accel.env').read_text(encoding='ascii').splitlines():
            if line and not line.startswith('#'):
                key, value = line.split('=', 1)
                settings[key] = value
        config = accel.Settings(settings, token_path=str(received))
        self.assertEqual(config.host, 'localhost')
        self.assertEqual(config.addr, '127.0.0.1')
        self.assertEqual(config.ports, {'embed': 8081, 'rerank': 8082})
        self.assertEqual(settings['GPU_EXT_TRANSPORT'], 'loopback')
        connections = []
        upstreams = {}
        for kind in ('embed', 'rerank'):
            upstream = accel.Upstream(config, None, config.ports[kind])
            connection = DoorConnection()
            upstream._connect = lambda connection=connection: connection
            upstreams[kind] = upstream
            connections.append(connection)
        attestor = accel.Attestor(config, upstreams)
        capture = io.StringIO()
        handler = logging.StreamHandler(capture)
        accel.log.addHandler(handler)
        try:
            for kind, route in accel.ROUTES.items():
                self.assertEqual(attestor._get_json(kind, '/info' if kind == 'embed' else '/health'), {})
                self.assertEqual(attestor._post_json(kind, route, {'synthetic': True}), {})
                status, _, _ = upstreams[kind].request('POST', route, b'{}',
                                                     {'aUtHoRiZaTiOn': 'Bearer synthetic-client-wrong',
                                                      'Proxy-Authorization': 'synthetic-proxy-wrong'})
                self.assertEqual(status, 200)
        finally:
            accel.log.removeHandler(handler)
        expected = 'Bearer ' + self.token.read_text().strip()
        for connection in connections:
            self.assertEqual(len(connection.requests), 3)
            for request in connection.requests:
                auth = [value for name, value in request['headers'] if name.lower() == 'authorization']
                self.assertEqual(len(auth), 1, 'exactly one enrolled Authorization header is required')
                self.assertTrue(hmac.compare_digest(auth[0], expected), 'enrolled bearer was not attached')
                self.assertFalse(any(name.lower() == 'proxy-authorization'
                                     for name, _ in request['headers']), 'proxy credential was forwarded')
        self.assertFalse(self.token.read_text().strip() in capture.getvalue(), 'forwarder log leaks bearer')

    def test_pair_enrol_and_authenticated_forward(self):
        _, _, result = self.exchange()
        self.enrol_and_forward(result['peer_id'])

    def test_endpoint_handover_is_authenticated_persisted_and_removed_on_unpair(self):
        endpoints = {'embeddings': 'https://forge.synthetic.ts.net:18443',
                     'reranker': 'https://forge.synthetic.ts.net:19443'}
        server, _, result = self.exchange(model_endpoints=endpoints)
        received = WORKSTATION.store.received_dir(self.ws_state, 'forge', result['peer_id'])
        self.assertEqual(json.loads((received / 'model-endpoints.json').read_bytes()), endpoints)
        self.assertIn('model-endpoints.json', WORKSTATION.store.list_peers(self.ws_state)[0]['received'])
        self.assertIn('model-endpoints.json', FORGE.store.list_peers(self.forge_state)[0]['given'])
        WORKSTATION.cli.dispatch('unpair', ['--state-dir', str(self.ws_state), '--kind', 'forge',
                                           '--side', 'joiner', '--peer-id', server.identity.id])
        self.assertFalse(received.exists())

    def test_wrong_code_three_windows_persistent_lockout(self):
        for _ in range(3):
            self.exchange(code=WRONG_CODE, success=False)
        self.assertEqual(WORKSTATION.store.list_peers(self.ws_state), [])
        self.assertEqual(FORGE.store.list_peers(self.forge_state), [])
        for node, state, side in ((WORKSTATION, self.ws_state, 'joiner'),
                                  (FORGE, self.forge_state, 'joined')):
            with self.assertRaises(node.errors.NetError) as caught:
                node.window.PairingWindow(state, 'forge-' + side, clock=self.clock).open()
            self.assertEqual(caught.exception.exit_code, 3)
            self.assertEqual(caught.exception.fields['locked_seconds'], 900)
        self.clock.now += 901
        _, _, result = self.exchange()
        self.enrol_and_forward(result['peer_id'])

    def test_unpair_rotate_and_pair_again_uses_new_bearer(self):
        server, joiner, result = self.exchange()
        self.enrol_and_forward(result['peer_id'])
        before = self.token.read_bytes()
        result_a = FORGE.cli.dispatch('unpair', ['--state-dir', str(self.forge_state), '--kind', 'forge',
                                               '--side', 'joined', '--peer-id', joiner.identity.id,
                                               '--token-path', str(self.token)])
        self.assertTrue(result_a['rotated'])
        self.assertEqual(result_a['restart_required'], ['door'])
        self.assertFalse(hmac.compare_digest(before, self.token.read_bytes()), 'Forge bearer did not rotate')
        WORKSTATION.cli.dispatch('unpair', ['--state-dir', str(self.ws_state), '--kind', 'forge',
                                            '--side', 'joiner', '--peer-id', server.identity.id])
        self.assertFalse(WORKSTATION.store.received_dir(self.ws_state, 'forge', server.identity.id).exists())
        _, _, result = self.exchange()
        self.enrol_and_forward(result['peer_id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tmp', required=True, type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    scratch = args.tmp.resolve()
    if scratch == Path(scratch.anchor) or scratch == REPO or REPO in scratch.parents:
        parser.error('--tmp must be a separate scratch directory outside the repository')
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ.update(TMP=str(scratch), TEMP=str(scratch), TMPDIR=str(scratch))
    tempfile.tempdir = str(scratch)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CrossNodeIntegration)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    data = {'tests_run': result.testsRun, 'tests_failed': len(result.failures),
            'tests_errors': len(result.errors), 'tests_skipped': len(result.skipped),
            'transport': 'guarded-loopback-tls-and-in-process-http-stand-in'}
    if args.report:
        args.report.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='ascii')
    return int(not result.wasSuccessful())


if __name__ == '__main__':
    raise SystemExit(main())
