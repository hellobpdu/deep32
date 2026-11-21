"""Offline NVIDIA module-selection fixtures; no containers, keys or network."""
import importlib.util
import json
import os
from pathlib import Path
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2] / 'src' / 'build' / 'disc3-pins'
SPEC = importlib.util.spec_from_file_location(
    'resolve_pins_module_identity', os.environ.get('D32_RESOLVER') or ROOT / 'resolve-pins.py')
resolver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)

PACKAGE = 'linux-modules-nvidia-580-server-6.8.0-149-generic'
PACKAGE_VERSION = '6.8.0-149.149'
REQUEST = PACKAGE + '=' + PACKAGE_VERSION
META_PACKAGE = 'linux-modules-nvidia-580-server-generic'
META_REQUEST = META_PACKAGE + '=' + PACKAGE_VERSION
MODULE_ROOT = '/lib/modules/6.8.0-149-generic/'
SERVER_ROOT = MODULE_ROOT + 'kernel/nvidia-580srv/'
SIGNER = 'Canonical Ltd. Kernel Module Signing'
VERSION = '580.178.04'
INSTALLED = PACKAGE + '\tinstall ok installed\t' + PACKAGE_VERSION


def module(path='kernel/nvidia-580srv/nvidia.ko', owner='', version=VERSION, signer=SIGNER):
    return {'file': path if path.startswith('/') else MODULE_ROOT + path,
            'owner': owner, 'version': version, 'signer': signer}


def output(rows, installed=INSTALLED):
    lines = []
    for row in rows:
        lines.extend('D32-MOD ' + key + '=' + row[key]
                     for key in ('file', 'version', 'signer', 'owner'))
    if installed:
        lines.extend('D32-PKG ' + line for line in installed.splitlines())
    return '\n'.join(lines) + '\n'


class ModuleIdentity(unittest.TestCase):
    def resolve(self, rows, installed=INSTALLED, driver_version=VERSION,
                module_version='TO-PIN', module_signer='TO-PIN', modules_package=REQUEST):
        repositories = {repo: {'packages': []} for repo in resolver.REPOSITORIES}
        repositories['ubuntu-noble']['packages'].append({'name': PACKAGE, 'version': PACKAGE_VERSION})
        repositories['ubuntu-noble']['packages'].append({'name': META_PACKAGE, 'version': PACKAGE_VERSION})
        doc = {'ubuntu': {'modules_package': modules_package,
                          'module_version': module_version, 'module_signer': module_signer,
                          'driver_packages': ['libnvidia-compute-580-server=' + driver_version + '-0ubuntu1']},
               'fetch': {'repositories': repositories}}
        with mock.patch.object(resolver, 'load_indexes', return_value=({}, {})), \
                mock.patch.object(resolver.AptClosure, 'apt_container',
                                  return_value=('sha256:' + 'a' * 64, output(rows, installed))) as apt:
            result = resolver.ModuleIdentity().resolve(types.SimpleNamespace(), doc)
        self.assertEqual(apt.call_args.args[3], [REQUEST])
        return result

    def assert_pinned(self, rows, selected, **kwargs):
        result = self.resolve(rows, **kwargs)
        self.assertEqual(result.updates[('ubuntu', 'module_version')], VERSION)
        self.assertEqual(result.updates[('ubuntu', 'module_signer')], SIGNER)
        self.assertEqual(len(result.records), 2)
        for record in result.records:
            evidence = record['authentication']
            self.assertEqual(evidence['package'], REQUEST)
            self.assertEqual(evidence['installed_package'],
                             {'name': PACKAGE, 'status': 'install ok installed', 'version': PACKAGE_VERSION})
            self.assertNotIn('owner', evidence)
            self.assertCountEqual(evidence['files'], selected)
            self.assertCountEqual(evidence['matches'], rows)
        return result

    def assert_refused_with_all_matches(self, rows, *words, **kwargs):
        with self.assertRaises(resolver.Refusal) as caught:
            self.resolve(rows, **kwargs)
        reason = str(caught.exception)
        for word in words:
            self.assertIn(word, reason)
        marker = '; matches: '
        self.assertIn(marker, reason)
        self.assertCountEqual(json.loads(reason.split(marker, 1)[1]), rows)
        return reason

    def test_single_requested_module_still_pins(self):
        rows = [module()]
        self.assert_pinned(rows, [rows[0]['file']])

    def test_resolved_meta_package_selects_the_abi_package_from_the_closure(self):
        rows = [module()]
        self.assert_pinned(rows, [rows[0]['file']], modules_package=META_REQUEST)

    def test_real_five_matches_unowned_module_and_four_signature_decoys(self):
        rows = [module()]
        for branch in ('580', '580srv', '595', '595srv'):
            rows.append(module('kernel/nvidia-' + branch + '/bits/nvidia.ko.sig',
                               owner='linux-signatures-nvidia-6.8.0-149-generic', version='', signer=''))
        self.assertEqual(rows[0]['owner'], '')
        self.assert_pinned(rows, [rows[0]['file']])

    def test_zstd_module_pins(self):
        rows = [module('kernel/nvidia-580srv/nvidia.ko.zst')]
        self.assert_pinned(rows, [rows[0]['file']])

    def test_ownership_is_ignored_for_selection(self):
        for owner in ('', PACKAGE, PACKAGE + '-extra', 'nvidia-dkms-580-server'):
            with self.subTest(owner=owner):
                rows = [module(owner=owner)]
                self.assert_pinned(rows, [rows[0]['file']])

    def test_other_abis_paths_and_signers_do_not_change_selection(self):
        rows = [module('/lib/modules/6.8.0-148-generic/kernel/nvidia-580srv/nvidia.ko',
                       version='580.82.09', signer='Older ABI signer'),
                module('updates/dkms/nvidia.ko', signer='DKMS signer'), module(),
                module('kernel/nvidia-580/nvidia.ko', signer='Other flavour signer'),
                module('kernel/nvidia-595srv/nvidia.ko', version='595.1.1', signer='Other branch signer')]
        self.assert_pinned(rows, [rows[2]['file']])

    def test_uncompressed_and_zstd_same_identity_are_ambiguous(self):
        rows = [module(), module('kernel/nvidia-580srv/nvidia.ko.zst')]
        self.assert_refused_with_all_matches(rows, 'exactly one', 'found 2')

    def test_two_paths_with_different_signers_are_ambiguous(self):
        rows = [module(), module('kernel/nvidia-580srv/nvidia.ko.zst', signer='Different signer')]
        self.assert_refused_with_all_matches(rows, 'exactly one', 'found 2')

    def test_two_paths_with_different_versions_are_ambiguous(self):
        rows = [module(), module('kernel/nvidia-580srv/nvidia.ko.zst', version='580.82.09')]
        self.assert_refused_with_all_matches(rows, 'exactly one', 'found 2')

    def test_duplicate_candidate_rows_are_ambiguous(self):
        self.assert_refused_with_all_matches([module(), module()], 'exactly one', 'found 2')

    def test_other_abi_is_refused_even_with_exact_owner(self):
        rows = [module('/lib/modules/6.8.0-148-generic/kernel/nvidia-580srv/nvidia.ko', owner=PACKAGE)]
        self.assert_refused_with_all_matches(rows, SERVER_ROOT, 'found 0')

    def test_only_the_exact_server_path_qualifies(self):
        for path in ('kernel/nvidia.ko', 'updates/dkms/nvidia.ko',
                     'kernel/nvidia-580/nvidia.ko', 'kernel/nvidia-595srv/nvidia.ko',
                     'kernel/nvidia-580srv/bits/nvidia.ko', 'kernel/nvidia-580srv/bits/nvidia.ko.sig',
                     'kernel/nvidia-580srv/nvidia.ko.sig', 'kernel/nvidia-580srv/nvidia.ko.xz',
                     '../6.8.0-148-generic/kernel/nvidia-580srv/nvidia.ko',
                     'kernel/nvidia-580srv/../nvidia-580srv/nvidia.ko', 'kernel//nvidia-580srv/nvidia.ko'):
            with self.subTest(path=path):
                self.assert_refused_with_all_matches([module(path)], 'exactly one', 'found 0')

    def test_no_matches_has_an_explicit_empty_match_list(self):
        self.assert_refused_with_all_matches([], 'exactly one', 'found 0')

    def test_missing_signer_refuses_with_every_candidate_field(self):
        self.assert_refused_with_all_matches([module(signer='')], 'carries no signer')

    def test_bootstrap_signer_must_be_canonical(self):
        self.assert_refused_with_all_matches([module(signer='DKMS signer')], 'module_signer',
                                             'does not match Canonical Ltd. Kernel Module Signing')

    def test_wrong_version_branch_refuses_with_full_match_list(self):
        self.assert_refused_with_all_matches([module(version='570.95.05')], 'not on the 580-server branch')

    def test_driver_version_mismatch_refuses_with_full_match_list(self):
        self.assert_refused_with_all_matches([module()], 'does not match module version', driver_version='580.82.09')

    def test_wrong_installed_package_version_refuses_with_full_match_list(self):
        self.assert_refused_with_all_matches([module()], 'not installed at the pinned version',
                                             installed=PACKAGE + '\tinstall ok installed\t6.8.0-149.148')

    def test_package_status_must_be_install_ok_installed(self):
        for status in ('deinstall ok config-files', 'install ok unpacked', 'install reinstreq installed'):
            with self.subTest(status=status):
                self.assert_refused_with_all_matches([module()], 'not installed at the pinned version',
                                                     installed=PACKAGE + '\t' + status + '\t' + PACKAGE_VERSION)

    def test_missing_installed_package_refuses(self):
        self.assert_refused_with_all_matches([module()], 'not installed at the pinned version', installed='')

    def test_package_name_must_match_exactly(self):
        self.assert_refused_with_all_matches([module()], 'not installed at the pinned version',
                                             installed=PACKAGE + '-extra\tinstall ok installed\t' + PACKAGE_VERSION)

    def test_legacy_package_line_without_status_refuses(self):
        self.assert_refused_with_all_matches([module()], 'not installed at the pinned version', installed=REQUEST)

    def test_duplicate_package_lines_refuse(self):
        self.assert_refused_with_all_matches([module()], 'not installed at the pinned version',
                                             installed=INSTALLED + '\n' + INSTALLED)

    def test_existing_module_pins_must_match(self):
        rows = [module()]
        self.assert_pinned(rows, [rows[0]['file']], module_version=VERSION, module_signer=SIGNER)
        self.assert_refused_with_all_matches(rows, 'module_version', 'does not match pin', module_version='580.95.05')
        self.assert_refused_with_all_matches(rows, 'module_signer', 'does not match pin', module_signer='Different signer')

    def test_apt_script_queries_status_and_version_without_file_ownership(self):
        self.assertIn('${Status}\\t${Version}', resolver.APT_SCRIPT)
        self.assertNotIn('dpkg-query -S', resolver.APT_SCRIPT)
        self.assertNotIn('D32-MOD owner=', resolver.APT_SCRIPT)


if __name__ == '__main__':
    unittest.main(verbosity=2)
