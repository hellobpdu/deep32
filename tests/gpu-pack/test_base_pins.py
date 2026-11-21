"""Offline checks for measured runtime base pins. All external work is stubbed."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from kit_support import KIT, module, prov

fetch = module('fetch_base_pins', 'build/fetch-inputs.py')
IMAGE_ID = 'sha256:' + 'a' * 64
ARCHIVE_HASH = 'b' * 64
BINDING = dict(image_id=IMAGE_ID, config_digest=IMAGE_ID, index_digest='sha256:' + 'c' * 64,
               manifest_digest=None, archive_format='classic')


def config():
    return {'runtime': {'base_image': 'example.invalid/runtime@sha256:' + 'c' * 64,
                        'base_image_id': IMAGE_ID, 'base_archive_sha256': ARCHIVE_HASH,
                        'base_image_binding': copy.deepcopy(BINDING)}}


class BasePinTests(unittest.TestCase):
    def setUp(self):
        # These tests isolate pin recording; test_runtime_binding measures real
        # archives and exercises this API without mocking its archive verifier.
        verifier = patch.object(fetch.image_binding, 'archive_binding', return_value=copy.deepcopy(BINDING))
        verifier.start()
        self.addCleanup(verifier.stop)

    def test_pinned_equal_passes_without_rewriting_stage_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            c = config()
            fetch.write_stage_pins(c, stage)
            before = (stage / 'pins.json').read_bytes()
            with patch.object(fetch.prov, 'digest', return_value=ARCHIVE_HASH) as digest, \
                    patch.object(fetch, 'write_stage_pins', side_effect=AssertionError('unexpected write')):
                fetch.record_base(c, stage, IMAGE_ID)
            digest.assert_called_once_with(stage / 'base/runtime.tar')
            self.assertEqual((stage / 'pins.json').read_bytes(), before)

    def assert_mismatch(self, field, expected, measured):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            c = config()
            c['runtime'][field] = expected
            fetch.write_stage_pins(c, stage)
            before = (stage / 'pins.json').read_bytes()
            with patch.object(fetch.prov, 'digest', return_value=ARCHIVE_HASH):
                with self.assertRaises(ValueError) as refusal:
                    fetch.record_base(c, stage, IMAGE_ID)
            for value in ('runtime.' + field, expected, measured):
                self.assertIn(value, str(refusal.exception))
            self.assertEqual((stage / 'pins.json').read_bytes(), before)

    def test_pinned_image_id_mismatch_names_expected_and_measured(self):
        self.assert_mismatch('base_image_id', 'sha256:' + 'd' * 64, IMAGE_ID)

    def test_pinned_archive_hash_mismatch_names_expected_and_measured(self):
        self.assert_mismatch('base_archive_sha256', 'e' * 64, ARCHIVE_HASH)

    def test_to_pin_records_stage_only_and_preserves_source_bytes(self):
        source = KIT / 'pins.json'
        before = source.read_bytes()
        before_hash = hashlib.sha256(before).hexdigest()
        c = config()
        c['runtime'].update(base_image_id='TO-PIN', base_archive_sha256='TO-PIN: measure archive')
        original = copy.deepcopy(c)
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            staged = dict(c, later_evidence='preserved')
            fetch.write_stage_pins(staged, stage)
            output = io.StringIO()
            with patch.object(fetch, 'pull_save', return_value=IMAGE_ID) as pull, \
                    patch.object(fetch.prov, 'digest', return_value=ARCHIVE_HASH) as digest, \
                    contextlib.redirect_stdout(output):
                fetch.record_base(c, stage, fetch.pull_save(c['runtime']['base_image'], stage / 'base/runtime.tar'))
            pull.assert_called_once_with(c['runtime']['base_image'], stage / 'base/runtime.tar')
            digest.assert_called_once_with(stage / 'base/runtime.tar')
            recorded = json.loads((stage / 'pins.json').read_text(encoding='ascii'))
            self.assertEqual(recorded['runtime']['base_image_id'], IMAGE_ID)
            self.assertEqual(recorded['runtime']['base_archive_sha256'], ARCHIVE_HASH)
            self.assertEqual(recorded['later_evidence'], 'preserved')
            self.assertEqual(output.getvalue().splitlines(), [
                'Recorded runtime.base_image_id = ' + IMAGE_ID,
                'Recorded runtime.base_archive_sha256 = ' + ARCHIVE_HASH])
        self.assertEqual(c, original)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before_hash)

    def test_mixed_pins_record_only_unpinned_field_and_refuse_mismatch(self):
        for field in ('base_image_id', 'base_archive_sha256'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                stage = Path(directory)
                c = config()
                c['runtime'][field] = 'TO-PIN'
                fetch.write_stage_pins(c, stage)
                with patch.object(fetch.prov, 'digest', return_value=ARCHIVE_HASH), \
                        contextlib.redirect_stdout(io.StringIO()):
                    fetch.record_base(c, stage, IMAGE_ID)
                self.assertEqual(json.loads((stage / 'pins.json').read_text()), config())
                pinned = 'base_archive_sha256' if field == 'base_image_id' else 'base_image_id'
                c['runtime'][pinned] = 'wrong pin'
                fetch.write_stage_pins(c, stage)
                before = (stage / 'pins.json').read_bytes()
                with patch.object(fetch.prov, 'digest', return_value=ARCHIVE_HASH):
                    with self.assertRaisesRegex(ValueError, 'runtime.' + pinned):
                        fetch.record_base(c, stage, IMAGE_ID)
                self.assertEqual((stage / 'pins.json').read_bytes(), before)

    def test_preflight_still_refuses_empty_and_to_set_base_values(self):
        for field in ('base_image_id', 'base_archive_sha256'):
            for value in ('', 'TO-SET'):
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                    c = config()
                    c['runtime'][field] = value
                    stage = Path(directory) / 'stage'
                    with self.assertRaisesRegex(ValueError, 'runtime.' + field):
                        fetch.preflight(c, stage)
                    self.assertFalse(stage.exists())

    def test_dry_run_prints_base_with_no_commands_or_pin_changes(self):
        for unpinned in (False, True):
            with self.subTest(unpinned=unpinned), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                c = json.loads((KIT / 'pins.json').read_text(encoding='ascii'))
                if not unpinned:
                    c['runtime'].update(base_image_id=IMAGE_ID, base_archive_sha256=ARCHIVE_HASH)
                pins = root / 'pins.json'
                pins.write_text(prov.canonical(c), encoding='ascii')
                stage = root / 'stage'
                before = prov.members(root)
                output = io.StringIO()
                with patch.object(sys, 'argv', ['fetch-inputs.py', '--pins', str(pins), '--stage', str(stage), '--dry-run']), \
                        patch.object(fetch, 'pull_save', side_effect=AssertionError('external command')), \
                        patch.object(fetch.prov, 'digest', side_effect=AssertionError('measurement')), \
                        patch.object(fetch.urllib.request, 'urlopen', side_effect=AssertionError('network')), \
                        patch.object(fetch.subprocess, 'run', side_effect=AssertionError('external command')), \
                        patch.object(fetch.subprocess, 'check_output', side_effect=AssertionError('external command')), \
                        contextlib.redirect_stdout(output):
                    fetch.main()
                self.assertEqual(prov.members(root), before)
                self.assertFalse(stage.exists())
                text = output.getvalue()
                self.assertIn('docker pull --platform linux/amd64 ' + c['runtime']['base_image'], text)
                self.assertIn(fetch.shlex.join(['docker', 'image', 'save', '--output', str(stage / 'base/runtime.tar'),
                                               c['runtime']['base_image']]), text)
                for field in ('base_image_id', 'base_archive_sha256'):
                    self.assertIn('PIN runtime.' + field + ' = ' + json.dumps(c['runtime'][field]), text)
