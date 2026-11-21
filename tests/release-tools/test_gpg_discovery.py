"""Offline native GnuPG discovery and argument tests; synthetic paths only."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('tested_gpg_manifest', ROOT / 'src/release-tools/deep32_get_manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)
SCRATCH = None
PIN = '17' * 20


class GpgTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-gpg-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.programs = self.root / 'Program Files'
        self.programs_x86 = self.root / 'Program Files (x86)'
        environment = {'ProgramFiles': str(self.programs), 'ProgramFiles(x86)': str(self.programs_x86)}
        # Keep pathlib on the real host; only the helper's platform decision changes.
        self.platform = SimpleNamespace(name='nt', environ=environment, path=os.path, chmod=os.chmod)
        self.addCleanup(patch.stopall)
        patch.object(manifest, 'os', self.platform).start()
        self.which = patch.object(manifest.shutil, 'which', return_value=None).start()
        self.version = patch.object(manifest.subprocess, 'run', side_effect=AssertionError('real GPG forbidden')).start()
        self.process = patch.object(manifest.subprocess, 'Popen', side_effect=AssertionError('real GPG forbidden')).start()

    def file(self, relative):
        result = self.root / relative
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_bytes(b'SYNTHETIC EXECUTABLE STAND-IN; NEVER RUN\n')
        return result

    def working(self):
        self.version.side_effect = None
        self.version.return_value = subprocess.CompletedProcess([], 0, b'gpg (GnuPG) synthetic-test\n', b'')

    def test_known_native_gnupg_precedes_path_and_omits_git_probe(self):
        known = self.file('Program Files/GnuPG/bin/gpg.exe')
        other = self.file('native PATH/gpg.exe')
        self.which.return_value = str(other)
        self.working()
        self.assertEqual(manifest.gpg_executable(), known)
        self.assertEqual(self.version.call_args.args[0], [str(known), '--no-options', '--version'])

    def test_gpg4win_locations_work_in_both_program_files_trees(self):
        for base in ('Program Files', 'Program Files (x86)'):
            with self.subTest(base=base):
                selected = self.file(base + '/Gpg4win/bin/gpg.exe')
                self.working()
                self.assertEqual(manifest.gpg_executable(), selected)
                selected.unlink()

    def test_native_path_executable_is_kept(self):
        selected = self.file('native PATH with spaces/gpg.exe')
        self.which.return_value = str(selected)
        self.working()
        self.assertEqual(manifest.gpg_executable(), selected)

    def test_automatic_msys_path_is_skipped_even_with_renamed_git(self):
        for directory in ('Git', 'Renamed Git tools', 'custom-portable'):
            with self.subTest(directory=directory):
                selected = self.file(directory + '/usr/bin/gpg.exe')
                self.which.return_value = str(selected)
                with self.assertRaises(manifest.NeedsAction):
                    manifest.gpg_executable()
                self.version.assert_not_called()

    def test_automatic_msys_path_matching_normalises_slashes_and_case(self):
        self.which.return_value = 'X:\\Portable Tools\\USR\\BIN\\GPG.EXE'
        with self.assertRaises(manifest.NeedsAction):
            manifest.gpg_executable()
        self.version.assert_not_called()

    def test_explicit_msys_executable_is_operator_selected_without_fallback(self):
        selected = self.file('Git/usr/bin/gpg.exe')
        self.file('Program Files/GnuPG/bin/gpg.exe')
        self.working()
        self.assertEqual(manifest.gpg_executable(str(selected)), selected)
        self.which.assert_not_called()
        self.version.assert_called_once()

    def test_failed_explicit_version_never_falls_back(self):
        selected = self.file('explicit gpg.exe')
        self.file('Program Files/GnuPG/bin/gpg.exe')
        self.version.side_effect = None
        self.version.return_value = subprocess.CompletedProcess([], 1, b'not GnuPG', b'')
        with self.assertRaisesRegex(manifest.Refusal, 'not a working GnuPG'):
            manifest.gpg_executable(str(selected))
        self.which.assert_not_called()
        self.version.assert_called_once()

    def test_explicit_non_executable_extension_is_refused(self):
        selected = self.file('not-native/gpg.cmd')
        with self.assertRaisesRegex(manifest.Refusal, 'native .exe'):
            manifest.gpg_executable(str(selected))
        self.version.assert_not_called()

    def test_missing_windows_gnupg_names_install_and_reopen_steps(self):
        with self.assertRaises(manifest.NeedsAction) as result:
            manifest.gpg_executable()
        self.assertIn('install GnuPG', str(result.exception))
        self.assertIn("this host's package manager", str(result.exception))
        self.assertIn('vendor download', str(result.exception))
        self.assertNotIn('winget', str(result.exception))
        self.assertIn('reopen PowerShell', str(result.exception))
        self.version.assert_not_called()

    def test_posix_path_and_generic_missing_package_instruction(self):
        self.platform.name = 'posix'
        with self.assertRaises(manifest.NeedsAction) as result:
            manifest.gpg_executable()
        self.assertIn('install GnuPG', str(result.exception))
        self.assertIn("this host's package manager", str(result.exception))
        self.assertIn('vendor download', str(result.exception))
        self.assertNotRegex(str(result.exception), r'\bapt(?:-get)?\b')
        self.version.assert_not_called()
        selected = self.file('native-posix/gpg')
        self.which.return_value = str(selected)
        self.working()
        self.assertEqual(manifest.gpg_executable(), selected)

    def authenticate(self, listing):
        cache = self.root / 'cache with spaces'
        cache.mkdir()
        (cache / 'SHA256SUMS').write_bytes(b'0' * 64 + b'  SYNTHETIC.txt\n')
        (cache / 'SHA256SUMS.sig').write_bytes(b'SYNTHETIC SIGNATURE; GPG IS MOCKED\n')
        key = self.file('public key with spaces.asc')
        chosen = self.file('Git/usr/bin/gpg.exe')
        calls = []
        output = [listing, ('[GNUPG:] IMPORT_OK 1 ' + PIN + '\n').encode(),
                  ('[GNUPG:] VALIDSIG ' + PIN + ' 2026-11-17 0 0 4 0 22 8 00 ' + PIN + '\n').encode()]
        class Child:
            returncode = 0
            def __init__(self, result):
                self.result = result
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def communicate(self, timeout):
                return self.result, b''
        def launch(argv, **kwargs):
            calls.append((argv, kwargs))
            return Child(output[len(calls) - 1])
        self.process.side_effect = launch
        with patch.object(manifest, 'gpg_executable', return_value=chosen) as discovery:
            manifest.authenticate(cache, key, PIN, str(chosen))
        discovery.assert_called_once_with(str(chosen))
        return calls, cache, key, chosen

    def test_explicit_msys_receives_unchanged_native_paths_and_isolated_home(self):
        listing = ('pub:::::::::\nfpr:::::::::' + PIN + ':\n').encode()
        calls, cache, key, chosen = self.authenticate(listing)
        self.assertEqual(len(calls), 3)
        home = Path(calls[0][0][calls[0][0].index('--homedir') + 1])
        self.assertEqual(home.parent, cache)
        self.assertFalse(home.exists())
        for argv, options in calls:
            self.assertEqual(argv[0], str(chosen))
            self.assertEqual(argv[argv.index('--homedir') + 1], str(home))
            self.assertEqual(options['env']['GNUPGHOME'], str(home))
            self.assertIn('--no-options', argv)
            self.assertIn('--no-autostart', argv)
            self.assertIn('--no-auto-key-retrieve', argv)
            self.assertEqual(argv[argv.index('--auto-key-locate') + 1], 'clear')
        self.assertEqual(calls[0][0][-1], str(key))
        self.assertEqual(calls[1][0][-1], str(key))
        self.assertEqual(calls[2][0][-2:], [str(cache / 'SHA256SUMS.sig'), str(cache / 'SHA256SUMS')])

    def test_secret_key_listing_still_refuses_before_import(self):
        with self.assertRaisesRegex(manifest.Refusal, 'Secret-key input'):
            self.authenticate(b'sec:::::::::\n')
        self.process.assert_called_once()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch_root
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *remaining])
