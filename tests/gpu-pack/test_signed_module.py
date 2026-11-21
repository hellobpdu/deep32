"""Offline signed-module checks with process stubs found on the test PATH."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


KIT = Path(__file__).resolve().parents[2] / 'src' / 'gpu-pack'
SPEC = importlib.util.spec_from_file_location('signed_module_inputs', KIT / 'firstboot/installed-inputs.py')
installed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installed)
ABI = '6.8.0-149-generic'
PACKAGE = 'linux-modules-nvidia-580-server-' + ABI
SIGNER = 'Canonical Ltd. Kernel Module Signing'
VERSION = '580.178.04'
PACKAGE_VERSION = '6.8.0-149.149'


STUB = '''import json, os, sys
from pathlib import Path
data = json.loads(Path(os.environ['D32_MODULE_TEST_DATA']).read_text(encoding='ascii'))
name = Path(sys.argv[0]).stem
args = sys.argv[1:]
with Path(os.environ['D32_MODULE_TEST_LOG']).open('a', encoding='ascii') as log:
    log.write(json.dumps([name, *args]) + '\\n')
if name == 'modinfo':
    row = data['modules'].get(args[-1], {})
    value = row.get(args[1], '')
    print(value)
    sys.exit(0 if value else 1)
if args[:3] != ['-W', '-f', '${Status} ${Version}']:
    sys.exit(96)
if args[3] != data['package']:
    sys.exit(97)
print(data['status'])
sys.exit(data.get('package_exit', 0))
'''


class SignedModuleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='module-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.modules = self.root / 'modules'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('modinfo', 'dpkg-query'):
            (self.bin / (name + '.py')).write_text(STUB, encoding='ascii', newline='\n')
        self.log = self.root / 'commands.jsonl'
        self.data = {'modules': {}, 'package': PACKAGE,
                     'status': 'install ok installed ' + PACKAGE_VERSION}
        self.config = {'ubuntu': {'modules_package': 'linux-modules-nvidia-580-server-generic=1',
                                  'module_signer': SIGNER, 'module_version': VERSION},
                       'fetch': {'repositories': {'ubuntu-noble': {'packages': [
                           {'name': PACKAGE, 'version': PACKAGE_VERSION}]}}}}
        self.real_run = subprocess.run

    def add_module(self, relative=None, signer=SIGNER, version=VERSION):
        relative = relative or ABI + '/kernel/nvidia-580srv/nvidia.ko'
        path = self.modules / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic module')
        self.data['modules'][str(path)] = {'signer': signer, 'version': version}
        return path

    def real_layout(self):
        self.add_module()
        for folder in ('580', '580srv', '595', '595srv'):
            self.add_module(ABI + '/kernel/nvidia-' + folder + '/bits/nvidia.ko.sig', '', '')

    def command(self, args, **kwargs):
        # Execute Python stubs from PATH on every platform, including Windows,
        # where CreateProcess cannot execute extensionless Linux commands.
        self.assertIn(args[0], ('modinfo', 'dpkg-query'))
        stub = next((Path(folder) / (args[0] + '.py')
                     for folder in os.environ['PATH'].split(os.pathsep)
                     if (Path(folder) / (args[0] + '.py')).is_file()), None)
        self.assertIsNotNone(stub)
        return self.real_run([sys.executable, '-I', '-B', str(stub), *args[1:]], **kwargs)

    def check(self):
        data = self.root / 'fixture.json'
        data.write_text(json.dumps(self.data), encoding='ascii', newline='\n')
        environment = {'PATH': str(self.bin) + os.pathsep + os.environ.get('PATH', ''),
                       'D32_MODULE_TEST_DATA': str(data), 'D32_MODULE_TEST_LOG': str(self.log)}
        with patch.dict(os.environ, environment), patch.object(installed.subprocess, 'run', side_effect=self.command):
            return installed.signed_module(self.config, self.modules)

    def test_real_five_match_layout_passes_without_file_ownership(self):
        self.real_layout()
        row = self.check()
        self.assertEqual(row['file'], '/lib/modules/' + ABI + '/kernel/nvidia-580srv/nvidia.ko')
        commands = [json.loads(line) for line in self.log.read_text(encoding='ascii').splitlines()]
        self.assertEqual(sum(command[0] == 'modinfo' for command in commands), 10)
        self.assertEqual([command for command in commands if command[0] == 'dpkg-query'],
                         [['dpkg-query', '-W', '-f', '${Status} ${Version}', PACKAGE]])

    def test_zst_passes(self):
        self.add_module(ABI + '/kernel/nvidia-580srv/nvidia.ko.zst')
        self.assertTrue(self.check()['file'].endswith('.ko.zst'))

    def test_dkms_module_refuses(self):
        self.add_module(ABI + '/updates/dkms/nvidia.ko', 'Local test signer')
        with self.assertRaisesRegex(ValueError, 'exactly one.*do not substitute DKMS'):
            self.check()

    def test_wrong_version_refuses_with_all_five_diagnostics(self):
        self.real_layout()
        path = str(self.modules / ABI / 'kernel/nvidia-580srv/nvidia.ko')
        self.data['modules'][path]['version'] = '595.1'
        with self.assertRaises(ValueError) as refusal:
            self.check()
        matches = json.loads(str(refusal.exception).split('; matches: ', 1)[1])
        self.assertEqual(len(matches), 5)
        self.assertTrue(all(set(row) == {'file', 'signer', 'version'} for row in matches))

    def test_wrong_signer_refuses(self):
        self.add_module(signer='Local test signer')
        with self.assertRaisesRegex(ValueError, 'archive signer differs'):
            self.check()

    def test_empty_signer_refuses(self):
        self.add_module(signer='')
        with self.assertRaisesRegex(ValueError, 'archive signer differs'):
            self.check()

    def test_package_not_installed_refuses(self):
        self.add_module()
        self.data['status'] = 'deinstall ok config-files ' + PACKAGE_VERSION
        with self.assertRaisesRegex(ValueError, 'installed package status or version differs'):
            self.check()

    def test_package_query_failure_refuses(self):
        self.add_module()
        self.data['package_exit'] = 1
        with self.assertRaisesRegex(ValueError, 'installed package status or version differs'):
            self.check()

    def test_wrong_package_version_refuses(self):
        self.add_module()
        self.data['status'] = 'install ok installed wrong'
        with self.assertRaisesRegex(ValueError, 'installed package status or version differs'):
            self.check()

    def test_two_exact_files_refuse_even_with_equal_identity(self):
        self.add_module()
        self.add_module(ABI + '/kernel/nvidia-580srv/nvidia.ko.zst')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.check()

    def test_abi_package_pin_in_updates_or_security_passes(self):
        self.add_module()
        repositories = self.config['fetch']['repositories']
        repo = repositories.pop('ubuntu-noble')
        for name in ('ubuntu-updates', 'ubuntu-security'):
            with self.subTest(repository=name):
                repositories.clear()
                repositories[name] = repo
                self.assertIn('/' + ABI + '/', self.check()['file'])

    def test_explicit_matching_abi_package_pin_passes(self):
        self.add_module()
        self.config['ubuntu']['modules_package'] = PACKAGE + '=' + PACKAGE_VERSION
        self.assertIn('/' + ABI + '/', self.check()['file'])

    def test_explicit_other_abi_package_pin_refuses(self):
        self.add_module()
        self.config['ubuntu']['modules_package'] = 'linux-modules-nvidia-580-server-6.8.0-148-generic=6.8.0-148.148'
        with self.assertRaisesRegex(ValueError, 'pinned module package does not match target kernel'):
            self.check()

    def test_missing_package_pin_refuses(self):
        self.add_module()
        self.config['fetch']['repositories']['ubuntu-noble']['packages'] = []
        with self.assertRaisesRegex(ValueError, 'missing or conflicting pinned package version'):
            self.check()

    def test_newest_numeric_kernel_with_module_is_checked(self):
        self.add_module('6.8.0-99-generic/updates/dkms/nvidia.ko', 'Old local signer')
        self.add_module()
        (self.modules / '6.8.0-999-generic').mkdir()
        self.assertIn('/' + ABI + '/', self.check()['file'])

    def test_newer_dkms_kernel_cannot_fall_back_to_valid_old_kernel(self):
        self.add_module()
        self.add_module('6.8.0-150-generic/updates/dkms/nvidia.ko', 'Local test signer')
        with self.assertRaises(ValueError):
            self.check()

    def test_newer_unsupported_compressed_module_cannot_fall_back(self):
        for suffix in ('xz', 'gz'):
            with self.subTest(suffix=suffix):
                self.add_module()
                path = self.add_module('6.8.0-150-generic/kernel/nvidia-580srv/nvidia.ko.' + suffix)
                with self.assertRaises(ValueError):
                    self.check()
                path.unlink()

    def test_only_signatures_refuse(self):
        self.add_module(ABI + '/kernel/nvidia-580srv/bits/nvidia.ko.sig', '', '')
        with self.assertRaisesRegex(ValueError, 'No Ubuntu signed NVIDIA module found; do not substitute DKMS'):
            self.check()


if __name__ == '__main__':
    unittest.main(verbosity=2)
