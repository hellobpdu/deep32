#!/usr/bin/env python3
"""Offline enrolment tests; all generated credentials are synthetic scratch data."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

REPO = Path(__file__).resolve().parents[2]
RUNTIME = REPO / 'src/workstation/distrib/site/usr/local/d32'
sys.path.insert(0, str(RUNTIME))
from deep32net import enrol, store
from deep32net.errors import StateError
from deep32net.tokens import write_secret

# Windows has no pwd. The launcher isn't started; only its stdlib file loader runs.
if os.name != 'posix':
    sys.modules.setdefault('pwd', types.ModuleType('pwd'))
spec = importlib.util.spec_from_file_location('enrol_edge', REPO / 'src/workstation/node/shims/py/serve_edge.py')
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)
PUBLIC = '-----BEGIN PUBLIC KEY-----\n' + base64.b64encode(bytes.fromhex('302a300506032b6570032100') + b'\x11' * 32).decode() + '\n-----END PUBLIC KEY-----\n'


class EnrolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-enrol-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'var/db/d32/net'
        self.state.parent.mkdir(parents=True)
        self.etc = self.root / 'etc/d32'
        (self.etc / 'state').mkdir(parents=True)
        self.local = '12' * 32  # synthetic test credential
        write_secret(self.etc / 'state/mcp.token', self.local)

    def forge(self, identity='forge-aaaaaaaaaaaa', token='34' * 32, endpoints=None):
        row = store.record('forge', 'joiner', identity, 'forge', 'ab' * 32, 'cd' * 32,
                           'tailnet', 1700000000, peer_address='100.64.0.10', peer_port=7453,
                           peer_dns_name='deep32-forge.synthetic.ts.net',
                           received=['forge-token', 'kb-public.pem'])
        handover = {'kind': 'forge', 'forge_token': token, 'kb_public_key': PUBLIC, 'publish': 'serve-https'}
        if endpoints is not None:
            handover['model_endpoints'] = endpoints
            row['received'].append('model-endpoints.json')
        store.save_received(self.state, 'forge', identity, handover)
        store.save_record(self.state, row)
        return identity

    def gateway(self, identity, token):
        row = store.record('gateway', 'joined', identity, 'headend', 'ab' * 32, 'cd' * 32,
                           'loopback', 1700000000, given=['mcp-token'])
        store.save_record(self.state, row)
        write_secret(self.state / 'private/mcp-tokens' / identity, token)

    def test_forge_enrol_preserves_model_pins_and_writes_only_path(self):
        identity = self.forge()
        (self.etc / 'accel.env').write_text('GPU_EXT_EMBED_MODEL_ID=synthetic-model\nGPU_EXT_CA=/etc/ssl/cert.pem\n')
        result = enrol.enrol_forge(self.state, identity, self.root)
        settings = (self.etc / 'accel.env').read_text()
        self.assertIn('GPU_EXT_EMBED_MODEL_ID=synthetic-model\n', settings)
        self.assertIn('GPU_EXT_TOKEN_FILE=/etc/d32/private/forge.token\n', settings)
        self.assertNotIn('34' * 32, settings + json.dumps(result))
        self.assertTrue((self.etc / 'state/forge-enrolment.json').is_file())
        self.assertEqual((self.etc / 'state/forge-kb-public.pem').read_text(), PUBLIC)
        self.assertFalse((self.etc / 'state/forge-embedding').exists())  # no fabricated attestation

    def test_bad_peer_has_no_service_credential(self):
        with self.assertRaises(StateError):
            enrol.enrol_forge(self.state, '../escape', self.root)
        self.assertFalse((self.etc / 'private/forge.token').exists())

    def test_authenticated_endpoint_handover_supplies_actual_ports_and_base_urls(self):
        endpoints = {'embeddings': 'https://deep32-forge.synthetic.ts.net:18443',
                     'reranker': 'https://deep32-forge.synthetic.ts.net:19443'}
        identity = self.forge(endpoints=endpoints)
        result = enrol.enrol_forge(self.state, identity, self.root)
        settings = (self.etc / 'accel.env').read_text()
        for line in ('GPU_EXT_EMBED_PORT=18443', 'GPU_EXT_RERANK_PORT=19443',
                     'GPU_EXT_EMBED_URL=' + endpoints['embeddings'],
                     'GPU_EXT_RERANK_URL=' + endpoints['reranker']):
            self.assertIn(line + '\n', settings)
        self.assertNotIn('34' * 32, settings + json.dumps(result))

    def test_endpoint_dns_mismatch_fails_before_any_service_writes(self):
        endpoints = {'embeddings': 'https://other.synthetic.ts.net:18443',
                     'reranker': 'https://other.synthetic.ts.net:19443'}
        identity = self.forge(endpoints=endpoints)
        with self.assertRaises(StateError):
            enrol.enrol_forge(self.state, identity, self.root)
        self.assertFalse((self.etc / 'private/forge.token').exists())

    def test_legacy_handover_removes_stale_endpoint_settings(self):
        identity = self.forge()
        (self.etc / 'accel.env').write_text('GPU_EXT_EMBED_URL=https://old.synthetic.ts.net:18443\nGPU_EXT_RERANK_URL=https://old.synthetic.ts.net:19443\n')
        enrol.enrol_forge(self.state, identity, self.root)
        settings = (self.etc / 'accel.env').read_text()
        self.assertIn('GPU_EXT_EMBED_PORT=443\n', settings)
        self.assertIn('GPU_EXT_RERANK_PORT=8443\n', settings)
        self.assertNotIn('_URL=', settings)

    def test_uncommitted_handover_is_refused(self):
        identity = self.forge()
        store.record_path(self.state, 'forge', 'joiner', identity).unlink()
        with self.assertRaises(StateError):
            enrol.enrol_forge(self.state, identity, self.root)
        self.assertFalse((self.etc / 'private/forge.token').exists())

    def test_wrong_received_file_set_is_refused(self):
        identity = self.forge()
        path = store.record_path(self.state, 'forge', 'joiner', identity)
        row = json.loads(path.read_bytes())
        row['received'] = ['forge-token']
        path.write_text(json.dumps(row))
        with self.assertRaises(StateError):
            enrol.enrol_forge(self.state, identity, self.root)

    def test_distinct_headends_local_token_and_revocation_refresh(self):
        self.gateway('headend-aaaaaaaaaaaa', '56' * 32)
        self.gateway('headend-bbbbbbbbbbbb', '78' * 32)
        (self.etc / 'edge.env').write_text('RAG_CORE_URL=http://localhost\nRAG_MCP_TOKENS=' + self.local + '\n')
        result = enrol.enrol_gateway(self.state, 'headend-aaaaaaaaaaaa', self.root)
        path = self.etc / 'private/mcp-tokens'
        self.assertEqual(result['headends'], 2)
        self.assertEqual(len(edge.load_mcp_tokens(path).split(',')), 3)
        settings = (self.etc / 'edge.env').read_text()
        self.assertNotIn(self.local, settings)
        self.assertIn('RAG_MCP_TOKENS_FILE=/etc/d32/private/mcp-tokens\n', settings)
        store.remove_peer(self.state, 'gateway', 'joined', 'headend-aaaaaaaaaaaa')
        enrol.enrol_gateway(self.state, root=self.root)
        values = edge.load_mcp_tokens(path).split(',')
        self.assertEqual(len(values), 2)
        self.assertTrue(self.local in values and '78' * 32 in values)
        self.assertFalse('56' * 32 in values)
        self.assertTrue((self.etc / 'state/mcp.token').read_text().strip() == self.local)

    def test_orphan_or_missing_headend_token_fails_closed(self):
        write_secret(self.state / 'private/mcp-tokens/headend-aaaaaaaaaaaa', '56' * 32)
        with self.assertRaises(StateError):
            enrol.enrol_gateway(self.state, root=self.root)
        self.assertFalse((self.etc / 'private/mcp-tokens').exists())

    def test_gateway_malformed_and_oversized_credentials_fail_without_values(self):
        path = self.etc / 'private/mcp-tokens'
        path.parent.mkdir()
        for content in (b'', b'bad\n', b'12' * 32 + b'\n' + b'12' * 32 + b'\n', b'f' * 65537):
            path.write_bytes(content)
            with self.assertRaises(SystemExit) as caught:
                edge.load_mcp_tokens(path)
            self.assertEqual(str(caught.exception), 'deep32-gateway: paired credentials unavailable; run gateway enrolment')

    def engine_env(self, core_lines, edge_lines):
        (self.etc / 'core.env').write_text('\n'.join(core_lines) + '\n', encoding='ascii', newline='\n')
        (self.etc / 'edge.env').write_text('\n'.join(edge_lines) + '\n', encoding='ascii', newline='\n')

    def test_engine_token_moves_to_one_file_per_service_and_is_kept_after(self):
        token = 'ab' * 32  # synthetic test credential
        self.engine_env(['RAG_ROOT=/synthetic', 'RAG_CORE_API_TOKEN=' + token],
                        ['RAG_CORE_URL=http://localhost', 'RAG_CORE_API_TOKEN=' + token])
        self.assertEqual(enrol.enrol_engine(self.root), {'engine_token': 'moved'})
        for name, other, target in (('core.env', 'RAG_ROOT=/synthetic', 'engine-api.token'),
                                    ('edge.env', 'RAG_CORE_URL=http://localhost', 'engine-client.token')):
            self.assertEqual((self.etc / 'private' / target).read_text(encoding='ascii'), token + '\n')
            lines = (self.etc / name).read_text(encoding='ascii').splitlines()
            self.assertIn('RAG_CORE_API_TOKEN_FILE=/etc/d32/private/' + target, lines)
            self.assertIn(other, lines)
            self.assertFalse(any(line.startswith('RAG_CORE_API_TOKEN=') for line in lines))
        self.assertEqual(enrol.enrol_engine(self.root), {'engine_token': 'kept'})

    def test_engine_token_mismatch_or_missing_fails_closed(self):
        first, second = 'ab' * 32, 'cd' * 32  # synthetic test credentials
        cases = {
            'different': (['RAG_CORE_API_TOKEN=' + first], ['RAG_CORE_API_TOKEN=' + second]),
            'neither': (['RAG_ROOT=/synthetic'], ['RAG_CORE_URL=http://localhost']),
            'empty': (['RAG_CORE_API_TOKEN='], ['RAG_CORE_API_TOKEN=']),
            'other path': (['RAG_CORE_API_TOKEN_FILE=/etc/d32/private/engine-client.token'],
                           ['RAG_CORE_API_TOKEN=' + first]),
            'one moved': (['RAG_CORE_API_TOKEN_FILE=/etc/d32/private/engine-api.token'],
                          ['RAG_CORE_API_TOKEN=' + first]),
        }
        private = self.etc / 'private'
        for case, (core_lines, edge_lines) in cases.items():
            with self.subTest(case=case):
                if case == 'one moved':
                    private.mkdir(exist_ok=True)
                    write_secret(private / 'engine-api.token', first)
                before = sorted(path.name for path in private.iterdir()) if private.exists() else []
                self.engine_env(core_lines, edge_lines)
                with self.assertRaises(StateError):
                    enrol.enrol_engine(self.root)
                after = sorted(path.name for path in private.iterdir()) if private.exists() else []
                self.assertEqual(after, before)

    def test_engine_credential_reader_returns_the_token_or_one_fixed_refusal(self):
        reader = edge.d32svc.read_credential
        path = self.root / 'engine-client.token'
        path.write_bytes(b'ef' * 32 + b'\n')
        self.assertEqual(reader(str(path), '_d32edge', 'deep32-gateway'), 'ef' * 32)
        for content in (b'', b'ef' * 32 + b'\n' + b'ef' * 32 + b'\n', b'e' * 63 + b'\n', b'ef' * 32, b'EF' * 32 + b'\n'):
            path.write_bytes(content)
            with self.subTest(content=content[:8]), self.assertRaises(SystemExit) as caught:
                reader(str(path), '_d32edge', 'deep32-gateway')
            self.assertEqual(str(caught.exception), 'deep32-gateway: engine credential unavailable; run gateway enrolment')
        path.write_bytes(b'ef' * 32 + b'\n')
        link = self.root / 'engine-link.token'
        try:
            os.symlink(path, link)
        except (OSError, NotImplementedError):
            return  # this account may not create symbolic links (Windows without the privilege)
        with self.assertRaises(SystemExit) as caught:
            reader(str(link), '_d32core', 'deep32-engine')
        self.assertEqual(str(caught.exception), 'deep32-engine: engine credential unavailable; run gateway enrolment')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', required=True, type=Path)
    args, rest = parser.parse_known_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    SCRATCH = args.scratch
    unittest.main(argv=[sys.argv[0], *rest])
