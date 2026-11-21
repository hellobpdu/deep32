"""R2 provider removal regressions; all provider files and metadata are synthetic."""
import errno
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import test_install as existing
from deep32_relay import provider, state
from deep32_relay.state import OWNER_SCHEMA, PosixSecurity, StateError


class PosixShapedSecurity(PosixSecurity):
    """Run the real POSIX checks with portable, fixture-only stat metadata."""
    def __init__(self, provider_root):
        self.provider_root = provider_root

    def verify(self, path, directory=False):
        original = Path.lstat
        info = original(path)
        values = list(info)
        # A normal venv creates 0755 directories under its private 0700 root.
        mode = (0o755 if directory and self.provider_root in path.parents else
                0o700 if directory else 0o600)
        values[0] = stat.S_IFMT(info.st_mode) | mode
        shaped = os.stat_result(values)
        def fixture_stat(candidate, *args, **kwargs):
            return shaped if candidate == path else original(candidate, *args, **kwargs)
        with patch.object(state, 'os', SimpleNamespace(geteuid=lambda: info.st_uid)), \
             patch.object(Path, 'lstat', fixture_stat):
            super().verify(path, directory=directory)


class ProviderRemovalR17(unittest.TestCase):
    setUp = existing.Installation.setUp
    tearDown = existing.Installation.tearDown

    def populated(self):
        self.root.create()
        self.root.write_json('owner.json', {'schema': OWNER_SCHEMA, 'install_id': 'a' * 32})
        def run(argv):
            if 'venv' not in argv:
                return
            self.binary = self.root.root / 'provider' / (
                'Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            self.binary.parent.mkdir(parents=True)
            self.binary.write_bytes(b'SYNTHETIC_PROVIDER_BINARY')
            module = self.root.root / 'provider/lib/python-test/site-packages/example.py'
            module.parent.mkdir(parents=True)
            module.write_bytes(b'SYNTHETIC_PROVIDER_MODULE')
        provider.install_provider(self.root, run=run)
        return self.root.root / 'provider'

    def test_normal_0755_venv_directories_remove_inside_private_root(self):
        directory = self.populated()
        self.root.mkdir('profiles/claude')
        self.root.write('profiles/claude/synthetic-account', b'SYNTHETIC_SIGN_IN_KEEP')
        self.root.mkdir('private/link')
        self.root.write('private/link/synthetic-pin', b'SYNTHETIC_PIN_KEEP')
        owner_before = self.root.read('owner.json')
        self.root.security = PosixShapedSecurity(directory)
        self.assertEqual(provider.remove_provider(self.root), [])
        self.assertFalse(directory.exists())
        self.assertFalse((self.root.root / provider.RECEIPT).exists())
        self.assertEqual(self.root.read('owner.json'), owner_before)
        self.assertEqual(self.root.read('profiles/claude/synthetic-account'), b'SYNTHETIC_SIGN_IN_KEEP')
        self.assertEqual(self.root.read('private/link/synthetic-pin'), b'SYNTHETIC_PIN_KEEP')

    def test_changed_recorded_file_refuses_before_any_deletion(self):
        self.populated()
        self.binary.write_bytes(b'SYNTHETIC_CHANGED_KEEP')
        with self.assertRaisesRegex(StateError, '^modified-provider-install$'):
            provider.remove_provider(self.root)
        self.assertEqual(self.binary.read_bytes(), b'SYNTHETIC_CHANGED_KEEP')
        self.assertTrue((self.root.root / provider.RECEIPT).exists())
        self.assertTrue((self.root.root / 'provider/lib/python-test/site-packages/example.py').exists())

    def test_receipt_traversal_and_absolute_paths_refuse_before_deletion(self):
        self.populated()
        record = self.root.read_json(provider.RECEIPT)
        outside = self.base / 'synthetic-outside-keep'
        outside.write_bytes(b'SYNTHETIC_OUTSIDE_KEEP')
        for relative in ('../synthetic-outside-keep', str(outside), 'lib/../../synthetic-outside-keep'):
            with self.subTest(relative=relative):
                changed = dict(record, files=dict(record['files']))
                changed['files'][relative] = hashlib.sha256(outside.read_bytes()).hexdigest()
                self.root.write_json(provider.RECEIPT, changed)
                with self.assertRaisesRegex(StateError, '^invalid-provider-receipt$'):
                    provider.remove_provider(self.root)
                self.assertTrue(self.binary.exists())
                self.assertEqual(outside.read_bytes(), b'SYNTHETIC_OUTSIDE_KEEP')

    def test_disallowed_lib64_alias_refuses_before_any_deletion(self):
        directory = self.populated()
        alias = directory / 'lib64'
        alias.write_bytes(b'SYNTHETIC_ALIAS')
        original = Path.lstat
        values = list(alias.lstat())
        values[0] = stat.S_IFLNK | 0o777
        linked = os.stat_result(values)
        def fixture_stat(candidate, *args, **kwargs):
            return linked if candidate == alias else original(candidate, *args, **kwargs)
        with patch.object(Path, 'lstat', fixture_stat), \
             patch.object(provider.os, 'readlink', return_value='../synthetic-outside-keep'):
            with self.assertRaisesRegex(StateError, '^linked-state$'):
                provider.remove_provider(self.root)
        self.assertTrue(self.binary.exists())
        self.assertTrue(alias.exists())

    def test_permission_failure_names_exact_remaining_file_and_reason(self):
        self.populated()
        relative = 'provider/' + self.binary.relative_to(self.root.root / 'provider').as_posix()
        original = Path.unlink
        def blocked(candidate, *args, **kwargs):
            if candidate == self.binary:
                raise PermissionError(errno.EACCES, 'SYNTHETIC_PRIVATE_DIAGNOSTIC', str(candidate))
            return original(candidate, *args, **kwargs)
        details = {}
        with patch.object(Path, 'unlink', blocked):
            self.assertEqual(provider.remove_provider(self.root, details=details), [relative])
        self.assertEqual(details, {'code': 'local-permission-denied', 'paths': [relative]})
        self.assertTrue(self.binary.exists())
        self.assertTrue((self.root.root / provider.RECEIPT).exists())
        self.assertNotIn('SYNTHETIC_PRIVATE_DIAGNOSTIC', repr(details))
        self.assertNotIn(str(self.base), repr(details))
        self.assertEqual(provider.remove_provider(self.root), [])

    def test_non_dict_receipt_refused_by_install_without_native_launch(self):
        self.populated()
        self.root.write_json(provider.RECEIPT, ['SYNTHETIC_INVALID_RECEIPT'])
        with self.assertRaisesRegex(StateError, '^modified-provider-install$'):
            provider.install_provider(self.root, run=lambda argv: self.fail('provider launched'))
        self.assertEqual(self.binary.read_bytes(), b'SYNTHETIC_PROVIDER_BINARY')

    def test_non_dict_receipt_refused_by_remove_before_any_deletion(self):
        self.populated()
        self.root.write_json(provider.RECEIPT, ['SYNTHETIC_INVALID_RECEIPT'])
        with self.assertRaisesRegex(StateError, '^invalid-provider-receipt$'):
            provider.remove_provider(self.root)
        self.assertEqual(self.binary.read_bytes(), b'SYNTHETIC_PROVIDER_BINARY')

    def test_final_provider_directory_failure_names_root_and_retries(self):
        directory = self.populated()
        original = Path.rmdir
        def blocked(candidate, *args, **kwargs):
            if candidate == directory:
                raise PermissionError(errno.EACCES, 'SYNTHETIC_PRIVATE_DIAGNOSTIC', str(candidate))
            return original(candidate, *args, **kwargs)
        details = {}
        with patch.object(Path, 'rmdir', blocked):
            self.assertEqual(provider.remove_provider(self.root, details=details), ['provider'])
        self.assertEqual(details, {'code': 'local-permission-denied', 'paths': ['provider']})
        self.assertTrue(directory.is_dir())
        self.assertFalse(list(directory.iterdir()))
        self.assertTrue((self.root.root / provider.RECEIPT).exists())
        self.assertEqual(provider.remove_provider(self.root), [])
        self.assertFalse(directory.exists())
        self.assertFalse((self.root.root / provider.RECEIPT).exists())

    def test_receipt_unlink_failure_names_receipt_and_retries(self):
        directory = self.populated()
        receipt_path = self.root.root / provider.RECEIPT
        original = Path.unlink
        def blocked(candidate, *args, **kwargs):
            if candidate == receipt_path:
                raise PermissionError(errno.EACCES, 'SYNTHETIC_PRIVATE_DIAGNOSTIC', str(candidate))
            return original(candidate, *args, **kwargs)
        details = {}
        with patch.object(Path, 'unlink', blocked):
            self.assertEqual(provider.remove_provider(self.root, details=details), [provider.RECEIPT])
        self.assertEqual(details, {'code': 'local-permission-denied', 'paths': [provider.RECEIPT]})
        self.assertFalse(directory.exists())
        self.assertTrue(receipt_path.exists())
        self.assertEqual(provider.remove_provider(self.root), [])
        self.assertFalse(receipt_path.exists())
