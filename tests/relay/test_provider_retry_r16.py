"""Recorded incomplete provider installs recover without broad deletion."""
import os
from unittest.mock import patch
import unittest
import test_install as existing
from deep32_relay import provider
from deep32_relay.state import OWNER_SCHEMA, StateError


class ProviderRetry(unittest.TestCase):
    setUp = existing.Installation.setUp
    tearDown = existing.Installation.tearDown

    def partial(self):
        self.root.create()
        self.root.write_json('owner.json', {'schema': OWNER_SCHEMA, 'install_id': 'a' * 32})
        self.calls = []
        def fail(argv):
            self.calls.append(argv)
            binary = self.root.path('provider') / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'synthetic partial provider')
            raise StateError('synthetic-pip-failure')
        with self.assertRaisesRegex(StateError, 'synthetic-pip-failure'):
            provider.install_provider(self.root, run=fail)
        self.assertFalse(self.root.read_json(provider.RECEIPT)['complete'])

    def test_recorded_failed_install_is_removed_read_back_then_rebuilt(self):
        self.partial()
        self.root.mkdir('private/link')
        self.root.write('private/link/synthetic-pin', b'SYNTHETIC_PIN_KEEP')
        calls = []
        def run(argv):
            calls.append(argv)
            if 'venv' in argv:
                self.assertFalse(self.root.path('provider').joinpath('Scripts/python.exe' if os.name == 'nt' else 'bin/python').exists())
                binary = self.root.path('provider') / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b'synthetic completed provider')
        provider.install_provider(self.root, run=run)
        self.assertEqual(len(calls), 3)
        self.assertTrue(self.root.read_json(provider.RECEIPT)['complete'])
        self.assertEqual(self.root.read('private/link/synthetic-pin'), b'SYNTHETIC_PIN_KEEP')

    def test_changed_partial_files_are_preserved_and_retry_refused(self):
        self.partial()
        foreign = self.root.path('provider/foreign')
        foreign.write_bytes(b'SYNTHETIC_FOREIGN_KEEP')
        with self.assertRaisesRegex(StateError, 'modified-provider-install'):
            provider.install_provider(self.root, run=lambda argv: self.fail('provider launched'))
        self.assertEqual(foreign.read_bytes(), b'SYNTHETIC_FOREIGN_KEEP')

    def test_incomplete_removal_does_not_run_installer(self):
        self.partial()
        with patch.object(provider, 'remove_provider', return_value=['provider/remaining']):
            with self.assertRaisesRegex(StateError, 'provider-cleanup-incomplete'):
                provider.install_provider(self.root, run=lambda argv: self.fail('provider launched'))
        self.assertFalse(self.root.read_json(provider.RECEIPT)['complete'])
