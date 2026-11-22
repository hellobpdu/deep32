"""Offline storage and VM artefact tests; synthetic material only."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('manifest', ROOT / 'src/release-tools/deep32_get_manifest.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Helpers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-release-', dir=SCRATCH)
        self.root = Path(self.tmp.name)
        self.dest = self.root / 'output with spaces'
        self.cache = self.dest / 'cache'
        self.cache.mkdir(parents=True)
        self.image = ['image', 'synthetic-disc1.img', '1048576', hashlib.sha256(bytes(1048576)).hexdigest()]
        self.parts = [['asset', self.image[1], self.image[2], self.image[3]]]

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, style='sh', vm=''):
        with patch.object(m, 'selected_records', return_value=(self.image, self.parts)):
            return m.storage_plan('manifest', 'sums', 'disc1', 'v1', self.dest, self.cache, style, vm)

    def test_shared_filesystem_aggregates_and_boundary(self):
        for available, ready in ((2097151, False), (2097152, True), (2097153, True)):
            with self.subTest(available=available), patch.object(m.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(1, 1, available)):
                rows = self.plan('ps1')['filesystems']
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['needed'], 2097152)
                self.assertEqual(rows[0]['ready'], ready)

    def test_unsplit_bash_has_one_image_allocation(self):
        self.assertEqual(self.plan()['filesystems'][0]['needed'], 1048576)

    def test_split_backing_filesystems_are_budgeted_separately(self):
        original = Path.stat
        def split_stat(folder, *args, **kwargs):
            values = list(original(folder, *args, **kwargs))
            values[2] = 20 if folder == self.cache else 10
            real = original(folder, *args, **kwargs)
            extras = {name: getattr(real, name) for name in ('st_atime', 'st_mtime', 'st_ctime', 'st_atime_ns',
                      'st_mtime_ns', 'st_ctime_ns', 'st_file_attributes', 'st_reparse_tag') if hasattr(real, name)}
            return os.stat_result(values, extras)
        with patch.object(Path, 'stat', split_stat):
            rows = self.plan('ps1')['filesystems']
        self.assertEqual({r['filesystem']: r['needed'] for r in rows}, {'10': 1048576, '20': 1048576})

    def test_partial_and_verified_cache_reduce_additional_bytes(self):
        (self.cache / (self.image[1] + '.download')).write_bytes(bytes(99))
        self.assertEqual(self.plan('ps1')['filesystems'][0]['needed'], 2097152 - 99)
        (self.cache / self.image[1]).write_bytes(bytes(1048576))
        self.assertEqual(self.plan('ps1')['filesystems'][0]['needed'], 1048576)

    def test_corrupt_cache_is_refused(self):
        (self.cache / self.image[1]).write_bytes(b'bad')
        with self.assertRaises(m.Refusal):
            self.plan('ps1')

    def test_existing_verified_image_needs_no_download_allocation(self):
        (self.dest / self.image[1]).write_bytes(bytes(1048576))
        self.assertEqual(self.plan('ps1')['filesystems'], [])

    def test_vm_peak_includes_installer_copy_footer(self):
        self.assertEqual(self.plan('sh', 'hyper-v')['filesystems'][0]['needed'], 2097152 + 512)

    def test_real_release_arithmetic(self):
        # Authenticated public release metadata, not image contents.
        d1, d2, d3 = 1876951040, 19638284288, 61859102720
        self.assertEqual(d1 + 2*d2 + 2*d3, 164871725056)
        self.assertEqual(2*(d1+d2+d3), 166748676096)

    def test_hyperv_bytes_footer_no_overwrite_and_no_vm_process(self):
        source = self.root / self.image[1]
        source.write_bytes(bytes(1048576))
        with patch.object(m.subprocess, 'Popen', side_effect=AssertionError('VM process forbidden')):
            m.prepare_vm(source, self.image[2], self.image[3], 'hyper-v', self.dest)
        output = self.dest / (source.stem + '-hyper-v')
        content = (output / 'installer.vhd').read_bytes()
        self.assertEqual(len(content), 1048576 + 512)
        self.assertEqual(hashlib.sha256(content[:-512]).hexdigest(), self.image[3])
        self.assertEqual(content[-512:-504], b'conectix')
        footer = bytearray(content[-512:])
        checksum = int.from_bytes(footer[64:68], 'big')
        footer[64:68] = bytes(4)
        self.assertEqual(checksum, (~sum(footer)) & 0xffffffff)
        self.assertFalse(json.loads((output / 'recipe.json').read_text())['starts_vm'])
        with self.assertRaises(m.Refusal):
            m.prepare_vm(source, self.image[2], self.image[3], 'hyper-v', self.dest)

    def test_kvm_retains_raw_bytes_and_commands_only(self):
        source = self.root / self.image[1]
        source.write_bytes(bytes(1048576))
        m.prepare_vm(source, self.image[2], self.image[3], 'kvm', self.dest)
        output = self.dest / (source.stem + '-kvm')
        self.assertEqual((output / 'installer.img').read_bytes(), source.read_bytes())
        self.assertIn('--print-xml', (output / 'commands.txt').read_text())
        self.assertFalse((output / 'target.qcow2').exists())

    def test_bad_input_creates_no_vm_directory(self):
        source = self.root / self.image[1]
        source.write_bytes(b'wrong')
        with self.assertRaises(m.Refusal):
            m.prepare_vm(source, self.image[2], self.image[3], 'kvm', self.dest)
        self.assertEqual(list(self.dest.iterdir()), [self.cache])

    def test_filesystem_without_hard_links_is_actionable_before_payload(self):
        with patch.object(m.os, 'link', side_effect=OSError('synthetic unsupported filesystem')):
            with self.assertRaises(m.NeedsAction):
                m.publication_check(self.dest)
        self.assertEqual(list(self.dest.iterdir()), [self.cache])

    def test_corrupt_vm_copy_is_never_published(self):
        source = self.root / self.image[1]
        source.write_bytes(bytes(1048576))
        def broken_copy(src, dst):
            Path(dst).write_bytes(b'x' * 1048576)
        with patch.object(m.shutil, 'copyfile', side_effect=broken_copy):
            with self.assertRaises(m.Refusal):
                m.prepare_vm(source, self.image[2], self.image[3], 'kvm', self.dest)
        self.assertEqual(list(self.dest.iterdir()), [self.cache])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch_root
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *remaining])
