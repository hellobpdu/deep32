#!/usr/bin/env python3
"""Offline importer mount, early-error, and journal validation regressions."""

from contextlib import redirect_stderr
import copy
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
IMPORTER = ROOT / 'src/workstation/distrib/site/usr/local/d32/libexec/d32-import'
sys.path.insert(0, str(IMPORTER))
import d32import
import imp_disc
import imp_fs
import imp_index
import imp_journal
import imp_manifest
import imp_paths
import imp_switch
import imp_units


class ImporterMountFailure(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix='importer-mount-', dir=os.environ.get('D32_TEST_WORK'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = mock.patch.dict(os.environ, {
            'D32_ROOT': str(self.root), 'D32_TEST_CLOCK': '1800000000',
            'D32_TEST_RAND': '1234abcd', 'D32_TEST_FAULTS': '',
            'D32_TEST_CRASH': '', 'D32_TEST_SIGNAL_AT': '',
            'D32_TEST_LIVE_PIDS': '', 'D32_STUBS': str(self.root / 'stubs'),
        })
        env.start()
        self.addCleanup(env.stop)
        for name, value in (('_HOOKS', None), ('_CLOCK_READS', 0)):
            patch = mock.patch.object(imp_paths, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(imp_fs, '_SIGNALLED', False)
        patch.start()
        self.addCleanup(patch.stop)
        self.paths = imp_paths.workstation()

    def test_mounted_read_only_disc_survives_repeated_journal_saves(self):
        journal = imp_journal.Journal(self.paths, load=False)
        registry = imp_journal.Registry(self.paths, load=False)
        self.paths.mount.mkdir(parents=True)
        owner = imp_fs.set_owner_mode
        mount_checks = []

        def is_mount(path):
            mount_checks.append(path)
            return path == self.paths.mount

        def set_owner(path, *args):
            if path == self.paths.mount:
                raise OSError(errno.EROFS, 'synthetic read-only disc')
            return owner(path, *args)

        with mock.patch.object(Path, 'is_mount', is_mount), mock.patch.object(
                imp_fs, 'set_owner_mode', side_effect=set_owner):
            journal.new('a' * 64, 'cd0', '20270115T080000Z-1234abcd')
            journal.set_stage('disc', 'done')
            journal.set_stage('signature', 'running')
            journal.note('synthetic test record')
            journal.child('synthetic-tool', 0, 'synthetic result')
            registry.save()
        self.assertGreaterEqual(mount_checks.count(self.paths.mount), 6)
        stored = json.loads(self.paths.state_file.read_text(encoding='ascii'))
        self.assertEqual(stored['stages']['disc']['state'], 'done')
        self.assertEqual(stored['stages']['signature']['state'], 'running')
        self.assertEqual(stored['children'][0]['exit'], 0)
        self.assertTrue(self.paths.registry_file.is_file())
        ledger = (self.root / 'owners.tsv').read_text(encoding='ascii')
        self.assertNotIn(self.paths.mount.relative_to(self.root).as_posix() + '\t', ledger)
        self.assertIn(self.paths.journal.relative_to(self.root).as_posix() + '\troot\twheel\t0700', ledger)

    def test_prepare_skips_each_mount_point(self):
        for folder in (self.paths.state, self.paths.journal, self.paths.tmp, self.paths.mount):
            with self.subTest(folder=folder.name), mock.patch.object(
                    Path, 'is_mount', lambda path: path == folder), mock.patch.object(
                    imp_fs, 'set_owner_mode') as owner:
                imp_journal.prepare(self.paths)
                self.assertNotIn(folder, [call.args[0] for call in owner.call_args_list])
                self.assertEqual(owner.call_count, 3)

    def test_prepare_refuses_symlink_even_when_reported_as_mount(self):
        original = Path.is_symlink
        with mock.patch.object(Path, 'is_symlink', lambda path:
                               path == self.paths.mount or original(path)), mock.patch.object(
                Path, 'is_mount', lambda path: path == self.paths.mount), mock.patch.object(
                imp_fs, 'set_owner_mode') as owner:
            with self.assertRaises(imp_fs.ImportFailure) as caught:
                imp_journal.prepare(self.paths)
        self.assertEqual(caught.exception.code, 'IMPORT_WRITE')
        self.assertNotIn(self.paths.mount, [call.args[0] for call in owner.call_args_list])

    def test_prepare_refuses_non_directory(self):
        self.paths.state.mkdir(parents=True)
        self.paths.mount.write_bytes(b'synthetic regular file')
        with self.assertRaises(FileExistsError):
            imp_journal.prepare(self.paths)

    def test_import_state_machine_finishes_with_mounted_read_only_disc(self):
        self._import_state_machine_with_read_only_disc(start_mounted=False)

    def test_resume_starts_with_mounted_read_only_disc(self):
        self._import_state_machine_with_read_only_disc(start_mounted=True)

    def _import_state_machine_with_read_only_disc(self, start_mounted):
        # Payload stage owners are fixtures; disc, identity, journal, stage flow,
        # report, finalization, lock, and unmount execute their real code.
        for name in ('kb', 'corpus', 'library'):
            (self.paths.mount / name).mkdir(parents=True, exist_ok=True)
        (self.paths.mount / 'MANIFEST.json').write_bytes(b'{}\n')
        (self.paths.mount / 'MANIFEST.json.asc').write_bytes(b'synthetic signature\n')
        mounted = start_mounted
        if start_mounted:
            journal = imp_journal.Journal(self.paths, load=False)
            journal.new(None, 'cd0', '20270115T080000Z-1234abcd')
            (self.paths.journal / 'mounted').write_bytes(b'cd0\n')
        saves = []
        commands = []
        owner = imp_fs.set_owner_mode
        save = imp_journal.Journal.save

        def set_owner(path, *args):
            if mounted and path == self.paths.mount:
                raise imp_fs.ImportFailure('IMPORT_WRITE') from OSError(errno.EROFS, 'synthetic disc')
            return owner(path, *args)

        def save_journal(journal):
            if mounted:
                saves.append(copy.deepcopy(journal.data))
            return save(journal)

        def children(ctx):
            def command(name, args, timeout=600):
                nonlocal mounted
                commands.append((name, list(args)))
                if name == 'mount':
                    mounted = True
                elif name == 'umount':
                    mounted = False
                text = 'label: SYNTHETIC\n  c: 100 0 4.2BSD\n' if name == 'disklabel' else ''
                return subprocess.CompletedProcess([name] + list(args), 0, text, '')
            ctx.command = command
            ctx.run_child = lambda argv, timeout=600: subprocess.CompletedProcess(argv, 0, '', '')

        def signature(ctx):
            ctx.manifest = {'counts': {'bytes': 0}, 'members': []}
            ctx.report.data['source']['disc_version'] = 'synthetic-v1'
            ctx.journal.note('synthetic signature stage')

        def payload(ctx):
            ctx.journal.note('synthetic payload stage')

        with mock.patch.object(Path, 'is_mount', lambda path: mounted and path == self.paths.mount), \
                mock.patch.object(imp_fs, 'set_owner_mode', side_effect=set_owner), \
                mock.patch.object(imp_journal.Journal, 'save', save_journal), \
                mock.patch.object(d32import, '_children', children), \
                mock.patch.object(imp_fs, 'install_signal_handlers'), \
                mock.patch.object(imp_manifest, 'run_signature', signature), \
                mock.patch.object(d32import.imp_files, 'run_files', payload), \
                mock.patch.object(imp_switch, 'already_current', return_value=False), \
                mock.patch.object(d32import.imp_files, 'run_space', payload), \
                mock.patch.object(d32import.imp_files, 'run_kb', payload), \
                mock.patch.object(d32import.imp_files, 'run_library', payload),                 mock.patch.object(d32import.imp_files, 'run_switch', payload),                 mock.patch.object(d32import.imp_files, 'run_check', payload), \
                mock.patch.object(imp_switch, 'run_switch', payload), \
                mock.patch.object(imp_switch, 'run_check', payload), \
                mock.patch.object(imp_index, 'regenerate'):
            argv = ['--node', 'workstation'] + (['--resume'] if start_mounted else ['--device', 'cd0'])
            result = d32import.main(argv)
        self.assertEqual(result, 0)
        self.assertGreaterEqual(len(saves), 20)
        self.assertFalse(mounted)
        expected = (['umount'] if start_mounted else []) + ['disklabel', 'mount', 'umount']
        self.assertEqual([name for name, _ in commands], expected)
        report = json.loads(self.paths.report.read_text(encoding='ascii'))
        journal = json.loads(self.paths.state_file.read_text(encoding='ascii'))
        self.assertEqual(report['outcome'], 'imported')
        self.assertEqual(journal['outcome'], 'imported')
        self.assertEqual(report['errors'], [])
        self.assertFalse(self.paths.lock.exists())

    def test_workstation_device_rule_takes_optical_drives_and_usb_sticks(self):
        for device in ('cd0', 'cd9', 'cd12', 'sd0', 'sd1', 'sd12', 'vnd0', 'vnd3', 'vnd9'):
            with self.subTest(device=device):
                options = d32import.parse_args(['--node', 'workstation', '--device', device])
                self.assertEqual(options['device'], device)
        for device in ('cd', 'sd', 'cd123', 'sd123', 'sd1c', 'sd1\n', ' sd1', '/dev/sd1', 'rsd1', 'SD1',
                       'wd0', 'sr0', 'vnd', 'vnd10', 'vnd0c', 'rvnd0', 'svnd0', 'loop0', 'folder', 'network'):
            with self.subTest(device=device), self.assertRaises(d32import.UsageError):
                d32import.parse_args(['--node', 'workstation', '--device', device])
        # The Forge's rule is unchanged.
        self.assertEqual(d32import.parse_args(['--node', 'forge', '--device', 'sr0'])['device'], 'sr0')
        for device in ('sd1', 'cd0', 'sr10', 'vnd0'):
            with self.subTest(forge=device), self.assertRaises(d32import.UsageError):
                d32import.parse_args(['--node', 'forge', '--device', device])

    def _run_disc(self, device, label, disklabel_status=0):
        for name in ('kb', 'corpus', 'library'):
            (self.paths.mount / name).mkdir(parents=True, exist_ok=True)
        (self.paths.mount / 'MANIFEST.json').write_bytes(b'{}\n')
        (self.paths.mount / 'MANIFEST.json.asc').write_bytes(b'synthetic signature\n')
        (self.paths.journal / 'mounted').unlink(missing_ok=True)
        commands = []

        def command(name, args, timeout=600):
            commands.append((name, list(args)))
            status = disklabel_status if name == 'disklabel' else 0
            return subprocess.CompletedProcess([name] + list(args), status,
                                               label if name == 'disklabel' else '', '')
        ctx = SimpleNamespace(p=self.paths, options={'device': device}, journal=SimpleNamespace(data={}),
                              command=command)
        return ctx, commands

    def test_usb_stick_mounts_its_whole_disk_read_only_as_iso9660(self):
        # The label OpenBSD makes from an ISO 9660 image, a writer's DOS partition table, and a label with one data
        # partition: a USB stick holds the image from its first sector, so c is mounted whatever the label says.
        labels = {'iso9660': 'label: DEEP32_CONTENT\n  a:  100  0  ISO9660\n  c:  100  0  ISO9660\n',
                  'mbr': 'label: USB DISK\n  c:  100  0  unused\n  i:  8  64  MSDOS\n',
                  'one-partition': 'label: USB DISK\n  a:  100  0  4.2BSD\n  c:  100  0  unused\n'}
        for name, label in labels.items():
            with self.subTest(label=name):
                ctx, commands = self._run_disc('sd1', label)
                disc = imp_disc.run_disc(ctx)
                self.assertEqual(commands, [('disklabel', ['sd1']),
                                            ('mount', ['-t', 'cd9660', '-o', 'ro', '/dev/sd1c', str(self.paths.mount)])])
                self.assertEqual((disc.device, disc.mounted), ('sd1', True))
                self.assertEqual((self.paths.journal / 'mounted').read_bytes(), b'sd1\n')
        # An optical drive keeps its rule: c, else the only data partition.
        ctx, commands = self._run_disc('cd0', labels['one-partition'])
        imp_disc.run_disc(ctx)
        self.assertEqual(commands[-1], ('mount', ['-t', 'cd9660', '-o', 'ro', '/dev/cd0a', str(self.paths.mount)]))

    def test_usb_stick_taken_out_is_no_disc_and_nothing_is_mounted(self):
        ctx, commands = self._run_disc('sd1', '', disklabel_status=1)
        with self.assertRaises(imp_fs.ImportFailure) as caught:
            imp_disc.run_disc(ctx)
        self.assertEqual((caught.exception.code, caught.exception.arguments), ('IMPORT_NO_DISC', {'device': 'sd1'}))
        self.assertTrue(caught.exception.resumable)
        self.assertEqual([name for name, _ in commands], ['disklabel'])
        self.assertFalse((self.paths.journal / 'mounted').exists())

    def test_startup_import_failure_preserves_original_code(self):
        owner = imp_fs.set_owner_mode

        def set_owner(path, *args):
            if path == self.paths.tmp:
                raise imp_fs.ImportFailure('IMPORT_READ', stage='disc')
            return owner(path, *args)

        output = io.BytesIO()
        with redirect_stderr(io.TextIOWrapper(output, encoding='ascii', write_through=True)), \
                mock.patch.object(imp_fs, 'set_owner_mode', side_effect=set_owner):
            result = d32import.main(['--node', 'workstation', '--device', 'cd0'])
            message = output.getvalue()
        self.assertEqual(result, 2)
        self.assertEqual(message, b'd32-content-import: IMPORT_READ\n')
        self.assertNotIn(b'Traceback', message)
        self.assertFalse(self.paths.lock.exists())

    def test_lock_ownership_failure_has_original_code_without_traceback(self):
        owner = imp_fs.set_owner_mode

        def set_owner(path, *args):
            if path == self.paths.lock:
                raise imp_fs.ImportFailure('IMPORT_WRITE', stage='disc')
            return owner(path, *args)

        output = io.BytesIO()
        with redirect_stderr(io.TextIOWrapper(output, encoding='ascii', write_through=True)), \
                mock.patch.object(imp_fs, 'set_owner_mode', side_effect=set_owner):
            result = d32import.main(['--node', 'workstation', '--device', 'cd0'])
            message = output.getvalue()
        self.assertEqual(result, 2)
        self.assertEqual(message, b'd32-content-import: IMPORT_WRITE\n')
        self.assertNotIn(b'Traceback', message)
        self.assertFalse(self.paths.lock.exists())

    def test_startup_refuses_regular_file_directory_without_traceback(self):
        self.paths.journal.mkdir(parents=True)
        self.paths.tmp.write_bytes(b'synthetic regular file')
        output = io.BytesIO()
        with redirect_stderr(io.TextIOWrapper(output, encoding='ascii', write_through=True)):
            result = d32import.main(['--node', 'workstation', '--device', 'cd0'])
            message = output.getvalue()
        self.assertEqual(result, 2)
        self.assertEqual(message, b'd32-content-import: IMPORT_WRITE\n')
        self.assertNotIn(b'Traceback', message)
        self.assertFalse(self.paths.lock.exists())

    def test_startup_refuses_symlink_mount_before_changing_owners(self):
        symlink = Path.is_symlink
        owner = imp_fs.set_owner_mode
        touched = []

        def set_owner(path, *args):
            touched.append(path)
            return owner(path, *args)

        output = io.BytesIO()
        with redirect_stderr(io.TextIOWrapper(output, encoding='ascii', write_through=True)), \
                mock.patch.object(Path, 'is_symlink', lambda path:
                                  path == self.paths.mount or symlink(path)), \
                mock.patch.object(Path, 'is_mount', lambda path: path == self.paths.mount), \
                mock.patch.object(imp_fs, 'set_owner_mode', side_effect=set_owner):
            result = d32import.main(['--node', 'workstation', '--device', 'cd0'])
            message = output.getvalue()
        self.assertEqual(result, 2)
        self.assertEqual(message, b'd32-content-import: IMPORT_WRITE\n')
        self.assertNotIn(self.paths.mount, touched)
        self.assertFalse(self.paths.lock.exists())

    def test_failure_without_report_or_journal_preserves_error(self):
        for report, journal in ((None, None), (None, SimpleNamespace(data={})),
                                (object(), None), (object(), SimpleNamespace(data=None))):
            with self.subTest(report=report is None, journal=journal is None):
                ctx = SimpleNamespace(p=self.paths, report=report, journal=journal,
                                      current_stage='signature')
                output = io.BytesIO()
                with redirect_stderr(io.TextIOWrapper(output, encoding='ascii', write_through=True)), \
                        mock.patch.object(imp_disc, 'unmount', side_effect=OSError('synthetic cleanup fault')):
                    result = d32import._failure(ctx, imp_fs.ImportFailure('IMPORT_SIGNATURE'))
                    self.assertEqual(output.getvalue(), b'd32-content-import: IMPORT_SIGNATURE\n')
                self.assertEqual(result, 2)

    def test_main_failure_before_report_has_original_code_without_traceback(self):
        output = io.BytesIO()

        def children(ctx):
            ctx.run_child = lambda argv, timeout=600: subprocess.CompletedProcess(argv, 0, '', '')

        def disc(ctx):
            ctx.disc = imp_disc.Disc(self.paths.mount, 'cd0')
            ctx.manifest_sha256 = 'a' * 64

        with redirect_stderr(io.TextIOWrapper(output, encoding='ascii', write_through=True)), \
                mock.patch.object(d32import, '_children', children), \
                mock.patch.object(imp_fs, 'install_signal_handlers'), \
                mock.patch.object(imp_disc, 'run_disc', disc), \
                mock.patch.object(imp_journal.Journal, 'save', side_effect=imp_fs.ImportFailure(
                    'IMPORT_WRITE', stage='disc')):
            result = d32import.main(['--node', 'workstation', '--device', 'cd0'])
            message = output.getvalue()
        self.assertEqual(result, 2)
        self.assertEqual(message, b'd32-content-import: IMPORT_WRITE\n')
        self.assertNotIn(b'Traceback', message)
        self.assertFalse(self.paths.lock.exists())
        self.assertFalse(self.paths.report.exists())

    def test_resumed_pointerless_library_remains_current(self):
        journal = imp_journal.Journal(self.paths, load=False)
        journal.new('a' * 64, 'cd0', '20270115T080000Z-1234abcd')
        report = d32import.Report(self.paths, '20270115T080000Z-1234abcd', 'cd0')
        report.set_unit('lib-cisco', parts=1)
        registry = imp_journal.Registry(self.paths, load=False)
        registry.active = {'pointers': {}, 'targets': ['.cisco-papers-test']}
        registry.data['generations'] = [{'target': '.cisco-papers-test', 'content_key': 'test-key'}]
        desired = {'pointer': None, 'target': '.cisco-papers-test', 'unit_kind': 'library',
                   'content_key': 'test-key', 'bytes': 123, 'units': [{'id': 'lib-cisco'}]}
        ctx = SimpleNamespace(p=self.paths, registry=registry, journal=journal, report=report,
                              desired=[desired], options={'qdrant_url': None})
        d32import._carry_units(ctx, 'library', {})
        self.assertEqual(report.unit('lib-cisco')['state'], 'current')
        self.assertEqual(report.unit('lib-cisco')['parts_checked'], 1)
        self.assertEqual(report.stage('library')['done_bytes'], 0)
        self.assertEqual(report.data['resume']['units_done'], ['lib-cisco'])

    def test_active_targets_validate_and_legacy_active_remains_valid(self):
        for record in ({'pointers': {}}, {'pointers': {}, 'targets': ['.cisco-papers-test']}):
            imp_journal._validate_active(record)
        for targets in (None, 'text', ['../bad'], ['.good', '.good'], [None], [['.nested']]):
            with self.subTest(targets=targets), self.assertRaises(imp_fs.ImportFailure):
                imp_journal._validate_active({'pointers': {}, 'targets': targets})

    def test_pointerless_desired_is_restricted_to_library_target(self):
        journal = imp_journal.Journal(self.paths, load=False)
        journal.new('a' * 64, 'cd0', '20270115T080000Z-1234abcd')
        desired = {'target': '.cisco-papers-test', 'pointer': None,
                   'value': '.cisco-papers-test', 'unit_kind': 'library',
                   'units': [{'id': 'lib-cisco', 'generation': 'test', 'release': 'test'}]}
        journal.data['desired'] = [desired]
        imp_journal._validate_journal(journal.data)
        for key, value in (('unit_kind', 'kb'), ('value', '.other'), ('pointer', '../unsafe')):
            invalid = copy.deepcopy(journal.data)
            invalid['desired'][0][key] = value
            with self.subTest(key=key), self.assertRaises(imp_fs.ImportFailure):
                imp_journal._validate_journal(invalid)


if __name__ == '__main__':
    unittest.main()
