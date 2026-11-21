import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'adapters/relay'))
from deep32_relay.engine import NativeAgent, EngineError, clean_environment
from deep32_relay.runtime import Scratch, EventLedger, RuntimeErrorCode
from deep32_relay.service import assess, package_digest
from deep32_relay.proxy import Gateway, ProxyError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ['DEEP32_RELAY_TEST_ROOT'])
        self.root = Path(self.temporary.name)
        self.calls = 0

    def tearDown(self):
        self.temporary.cleanup()

    def owner(self):
        self.calls += 1

    def test_scratch_exact_cleanup_and_idempotence(self):
        scratch = Scratch(self.root, validate_owner=self.owner)
        (scratch.path / 'nested').mkdir()
        (scratch.path / 'nested/file').write_bytes(b'synthetic')
        self.assertTrue(scratch.cleanup())
        self.assertTrue(scratch.cleanup())
        self.assertFalse(scratch.path.exists())
        self.assertTrue(self.root.exists())

    def test_scratch_hardlink_refused_before_delete(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'synthetic sentinel')
        scratch = Scratch(self.root, validate_owner=self.owner)
        os.link(outside, scratch.path / 'hardlink')
        self.assertFalse(scratch.cleanup())
        self.assertEqual(outside.read_bytes(), b'synthetic sentinel')
        (scratch.path / 'hardlink').unlink()
        self.assertTrue(scratch.cleanup())

    def test_scratch_replaced_directory_refused(self):
        scratch = Scratch(self.root, validate_owner=self.owner)
        old = scratch.path.with_name('retained')
        scratch.path.rename(old)
        scratch.path.mkdir()
        (scratch.path / 'foreign').write_bytes(b'sentinel')
        self.assertFalse(scratch.cleanup())
        self.assertEqual((scratch.path / 'foreign').read_bytes(), b'sentinel')

    def test_clean_environment_drops_parent_secrets(self):
        with patch.dict(os.environ, {'SECRET_SYNTHETIC': 'sentinel', 'HTTP_PROXY': 'sentinel',
                                     'PATH': 'untrusted', 'CODEX_HOME': 'untrusted'}):
            env = clean_environment(self.root, platform='linux')
        for key in ('SECRET_SYNTHETIC', 'HTTP_PROXY', 'PATH', 'CODEX_HOME'):
            self.assertNotIn(key, env)
        for key in ('HOME', 'TMP', 'TEMP', 'XDG_CONFIG_HOME', 'APPDATA'):
            self.assertEqual(env[key], str(self.root.resolve()))

    def test_native_engines_never_launch(self):
        for agent in ('codex', 'claude'):
            native = NativeAgent(agent, self.root)
            self.assertFalse(native.ready)
            with self.assertRaisesRegex(EngineError, '^native-agent-unqualified$'):
                native.start('synthetic', (), self.root, None)

    def test_unicode_authorisation_has_fixed_error_and_no_effect(self):
        def refused(*args):
            self.fail('unexpected upstream effect')
        gateway = Gateway(refused, clock=lambda: 1)
        cap = gateway.grant('synthetic-session', 10)
        gateway.activate(cap)
        with self.assertRaisesRegex(ProxyError, '^unauthorised$'):
            gateway.http_request(cap, method='POST', path='/mcp', host='127.0.0.1:7452',
                authorisation='Bearer \u00e9', content_type='application/json', body=b'{}')

    def test_service_has_no_local_readiness_override(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            result = assess()
        self.assertFalse(result['ready'])
        self.assertEqual(result['listeners'], 0)
        self.assertEqual(result['agent_launches'], 0)
        self.assertIn('pairing-bootstrap-unavailable', result['reasons'])
        self.assertEqual(result['package_sha256'], package_digest())

    def test_ledger_refuses_content_and_rotates(self):
        path = self.root / 'events.jsonl'
        ledger = EventLedger(path, validate_owner=self.owner, max_bytes=1024, clock=lambda: 1)
        for index in range(100):
            ledger.append('turn-end', count=index)
        self.assertLessEqual(len(list(self.root.glob('events.jsonl*'))), 4)
        for item in self.root.glob('events.jsonl*'):
            self.assertLessEqual(item.stat().st_size, 1024)
            for line in item.read_text().splitlines():
                self.assertLessEqual(set(json.loads(line)), {'time','sequence','event','reason','count'})
        for args in (('synthetic secret', 'none'), ('turn-end', 'synthetic secret')):
            with self.assertRaises(RuntimeErrorCode):
                ledger.append(*args)
        with self.assertRaises(RuntimeErrorCode):
            ledger.append('turn-end', prompt='synthetic secret')

    def test_ledger_hardlink_preserves_sentinel(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'sentinel')
        log = self.root / 'events.jsonl'
        os.link(outside, log)
        with self.assertRaises(RuntimeErrorCode):
            EventLedger(log, validate_owner=self.owner).append('turn-start')
        self.assertEqual(outside.read_bytes(), b'sentinel')


if __name__ == '__main__':
    unittest.main()
