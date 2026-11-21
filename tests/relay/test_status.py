"""Passive doctor snapshots and redaction: no network or native process starts."""
import copy
import json
import math
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace
import ctypes

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'adapters/relay'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep32_relay.state import OWNER_SCHEMA, OwnedRoot, StateError, strict_json
from deep32_relay.status import (FIELDS, SCHEMA, publish_status, read_status,
                                 unavailable_snapshot, validate_snapshot)
from test_install import FakeSecurity, SCRATCH
from deep32_relay import status as status_module


class Status(unittest.TestCase):
    def setUp(self):
        scratch = SCRATCH.resolve()
        if REPO in (scratch, *scratch.parents) or not scratch.is_dir():
            raise RuntimeError('explicit relay test scratch required')
        self.temporary = tempfile.TemporaryDirectory(prefix='status-', dir=scratch)
        self.security = FakeSecurity()
        self.owner = OwnedRoot(Path(self.temporary.name) / 'owned', self.security)
        self.owner.create()
        self.owner.write_json('owner.json', {'schema': OWNER_SCHEMA, 'install_id': 'a' * 32})
        self.owner.mkdir('runtime')
        self.value = unavailable_snapshot('a' * 32, 'codex', 'b' * 64, agent_found=True, now=100)

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, value=None):
        publish_status(self.owner, self.value if value is None else value)

    def running(self):
        value = copy.deepcopy(self.value)
        value.update(running=True, pid=1234, instance_id='windows-0000000000000001',
                     paired_workstation=True, workstation_alias='workstation', link_state='connected')
        return value

    def test_missing_snapshot_is_missing_and_creates_nothing(self):
        before = sorted(str(path) for path in self.owner.root.rglob('*'))
        self.assertEqual(read_status(self.owner, now=100), {'state': 'missing', 'snapshot': None})
        self.assertEqual(before, sorted(str(path) for path in self.owner.root.rglob('*')))

    def test_missing_root_is_not_created(self):
        missing = OwnedRoot(Path(self.temporary.name) / 'missing', self.security)
        self.assertEqual(read_status(missing, now=100)['state'], 'missing')
        self.assertFalse(missing.root.exists())

    def test_unavailable_does_not_claim_running(self):
        self.save()
        result = read_status(self.owner, now=100)
        self.assertEqual(result['state'], 'unavailable')
        self.assertFalse(result['snapshot']['running'])
        self.assertEqual(result['snapshot']['box_state'], 'unqualified')

    def test_live_observer_must_match_process_start_identity(self):
        self.save(self.running())
        observed = []
        def observe(pid):
            observed.append(pid)
            return 'windows-0000000000000001'
        result = read_status(self.owner, now=100, observer=observe)
        self.assertEqual(result['state'], 'ok')
        self.assertTrue(result['snapshot']['running'])
        self.assertEqual(observed, [1234])

    def test_reused_pid_and_unknown_observer_fail_closed(self):
        self.save(self.running())
        for identity in ('windows-0000000000000002', None):
            with self.subTest(identity=identity):
                result = read_status(self.owner, now=100, observer=lambda pid: identity)
                self.assertEqual(result['state'], 'unavailable')
                self.assertFalse(result['snapshot']['running'])

    def test_stale_and_future_snapshots_never_report_running(self):
        self.save(self.running())
        for now in (116, 98):
            with self.subTest(now=now):
                result = read_status(self.owner, now=now, observer=lambda pid: 'windows-0000000000000001')
                self.assertEqual(result['state'], 'stale')
                self.assertFalse(result['snapshot']['running'])

    def test_arbitrary_secret_fields_and_values_rejected(self):
        for change in ({'token': 'SYNTHETIC_SECRET_CANARY'},
                       {'profile_alias': 'SYNTHETIC_PRIVATE_PATH_CANARY'},
                       {'link_state': 'SYNTHETIC_PEER_TEXT_CANARY'},
                       {'capability_id': 'SYNTHETIC_TOKEN_HASH_CANARY'},
                       {'instance_id': 'SYNTHETIC_ARGV_CANARY'}):
            value = dict(self.value, **change)
            with self.subTest(change=change), self.assertRaises(StateError):
                validate_snapshot(value)

    def test_strict_numbers_booleans_bounds(self):
        for key, bad in (('pid', True), ('pid', -1), ('queue_depth', 3), ('queue_depth', False),
                         ('observed_at', float('nan')), ('observed_at', float('inf')),
                         ('running', 1), ('paired_workstation', 'yes')):
            value = dict(self.value, **{key: bad})
            with self.subTest(key=key, bad=bad), self.assertRaises(StateError):
                validate_snapshot(value)

    def test_last_result_uses_fixed_reason_enum(self):
        value = copy.deepcopy(self.value)
        for reason in ('cancelled', 'turn-timeout', 'invalid-agent-output', 'cleanup-failed', 'link-lost'):
            value['last_session_result'] = {'state': 'failed', 'reason': reason, 'observed_at': 99}
            self.assertEqual(validate_snapshot(value)['last_session_result']['reason'], reason)
        value['last_session_result']['reason'] = 'SYNTHETIC_STDERR_CANARY'
        with self.assertRaises(StateError):
            self.save(value)

    def test_unverifiable_running_identity_cannot_be_published(self):
        value = self.running()
        for change in ({'pid': 0}, {'instance_id': 'unknown'}):
            with self.subTest(change=change), self.assertRaises(StateError):
                self.save(dict(value, **change))

    def test_duplicate_keys_invalid_utf8_nonfinite_and_oversize_rejected(self):
        cases = (b'{"schema":1,"schema":2}', b'\xff', b'{"observed_at":NaN}', b'x' * 8193)
        for raw in cases:
            self.owner.write('runtime/status.json', raw)
            with self.subTest(length=len(raw)):
                self.assertEqual(read_status(self.owner, now=100), {'state': 'unavailable', 'snapshot': None})

    def test_wrong_install_binding_and_owner_refused(self):
        wrong = dict(self.value, install_id='c' * 32)
        with self.assertRaisesRegex(StateError, 'ownership-mismatch'):
            self.save(wrong)
        self.owner.write_json('runtime/status.json', wrong)
        self.assertEqual(read_status(self.owner, now=100)['state'], 'unavailable')

    def test_unauthorised_status_file_is_unavailable(self):
        self.save()
        self.security.denied.add(str(self.owner.root / 'runtime/status.json'))
        self.assertEqual(read_status(self.owner, now=100), {'state': 'unavailable', 'snapshot': None})

    def test_hardlinked_status_is_unavailable(self):
        self.save()
        os.link(self.owner.root / 'runtime/status.json', Path(self.temporary.name) / 'outside-link')
        self.assertEqual(read_status(self.owner, now=100)['state'], 'unavailable')

    def test_repeated_read_has_no_file_or_api_mutations(self):
        self.save()
        def snapshot():
            return {path.relative_to(self.owner.root).as_posix():
                    (path.stat().st_mtime_ns, path.stat().st_mode, path.read_bytes() if path.is_file() else None)
                    for path in (self.owner.root, *self.owner.root.rglob('*'))}
        before = snapshot()
        writes = self.security.writes
        with patch('subprocess.run', side_effect=AssertionError('spawn')), patch('socket.socket', side_effect=AssertionError('socket')):
            for _ in range(10):
                self.assertEqual(read_status(self.owner, now=100)['state'], 'unavailable')
        self.assertEqual(before, snapshot())
        self.assertEqual(writes, self.security.writes)

    def test_atomic_publish_during_concurrent_reads(self):
        self.save()
        errors = []
        def writer():
            try:
                for counter in range(20):
                    value = dict(self.value, observed_at=100 + counter % 2)
                    self.save(value)
            except BaseException as exc:
                errors.append(type(exc).__name__)
        thread = threading.Thread(target=writer)
        thread.start()
        while thread.is_alive():
            result = read_status(self.owner, now=101)
            if result['snapshot'] is not None:
                self.assertEqual(set(result['snapshot']), FIELDS)
                self.assertIn(result['snapshot']['observed_at'], (100, 101))
        thread.join()
        self.assertEqual(errors, [])

    def test_public_snapshot_contains_only_aliases_and_fixed_reason(self):
        self.save()
        encoded = json.dumps(read_status(self.owner, now=100), sort_keys=True)
        for forbidden in (str(self.owner.root), 'password', 'token', 'SYNTHETIC_', 'argv', 'environment', 'prompt', 'answer'):
            self.assertNotIn(forbidden, encoded)
        self.assertIn('relay-codex', encoded)

    def test_macos_birth_identity_is_checked_by_passive_reader(self):
        value = dict(self.running(), instance_id='macos-1234567890-000123')
        self.save(value)
        self.assertEqual(read_status(self.owner, now=100,
            observer=lambda pid: 'macos-1234567890-000123')['state'], 'ok')
        self.assertEqual(read_status(self.owner, now=100,
            observer=lambda pid: 'macos-1234567891-000123')['state'], 'unavailable')


class MacOSObserver(unittest.TestCase):
    def observe(self, changes=None, returned=136):
        changes = changes or {}
        def read(pid, flavor, arg, output, size):
            self.assertEqual((pid, flavor, arg, size), (1234, 3, 0, 136))
            value = output._obj
            fields = dict(pid=1234, uid=501, status=2, start_sec=1234567890, start_usec=123)
            fields.update(changes)
            for key, item in fields.items():
                setattr(value, key, item)
            return returned
        return SimpleNamespace(proc_pidinfo=Mock(side_effect=read))

    def test_fixed_width_abi_layout(self):
        self.assertEqual(ctypes.sizeof(status_module._ProcBSDInfo), 136)
        self.assertEqual(status_module._ProcBSDInfo.start_sec.offset, 120)
        self.assertEqual(status_module._ProcBSDInfo.start_usec.offset, 128)

    def test_birth_time_and_uid_are_returned_without_process_start(self):
        library = self.observe()
        with patch('subprocess.run', side_effect=AssertionError('spawn')):
            self.assertEqual(status_module._macos_identity(1234, library=library, uid=501),
                             'macos-1234567890-000123')
        library.proc_pidinfo.assert_called_once()

    def test_short_or_failed_native_observation_is_unavailable(self):
        for returned in (0, -1, 135, 137):
            with self.subTest(returned=returned):
                self.assertIsNone(status_module._macos_identity(1234,
                    library=self.observe(returned=returned), uid=501))

    def test_owner_pid_zombie_and_invalid_time_fail_closed(self):
        for change in ({'uid': 502}, {'pid': 4321}, {'status': 5}, {'status': 0},
                       {'start_sec': 0}, {'start_usec': 1000000}):
            with self.subTest(change=change):
                self.assertIsNone(status_module._macos_identity(1234,
                    library=self.observe(change), uid=501))

    def test_darwin_dispatch_and_library_failure_are_passive(self):
        with patch.object(status_module.os, 'name', 'posix'), patch.object(sys, 'platform', 'darwin'):
            with patch.object(status_module, '_macos_identity', return_value='macos-1-000001') as read:
                self.assertEqual(status_module.process_identity(1234), 'macos-1-000001')
                read.assert_called_once_with(1234)
            with patch.object(status_module.ctypes, 'CDLL', side_effect=OSError('synthetic')):
                self.assertIsNone(status_module.process_identity(1234))


if __name__ == '__main__':
    unittest.main()
