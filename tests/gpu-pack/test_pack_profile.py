"""Synthetic offline profile checks; no host, registry or repository access."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from kit_support import KIT, module, prov, validator

profile = module('test_release_profile', 'build/release_profile.py')
fetch = module('test_pack_fetch', 'build/fetch-inputs.py')
installed = module('test_pack_installed', 'firstboot/installed-inputs.py')


def minimal(which='pack'):
    return {'format': 'deep32-forge-pins/1', 'release_profile': which,
            'ubuntu': {'release': '24.04.5', 'kernel_package': 'linux-generic=' + profile.DEFERRED,
                       'modules_package': 'linux-modules-nvidia-580-server-generic=' + profile.DEFERRED,
                       'module_signer': profile.DEFERRED, 'module_version': profile.DEFERRED,
                       'driver_packages': ['libnvidia-compute-580-server=' + profile.DEFERRED]},
            'packages': ['docker.io=1', 'kmod=' + profile.DEFERRED],
            'fetch': {'iso_signing_fingerprint': profile.DEFERRED, 'files': [
                {'path': 'iso/base.iso', 'sha256': profile.DEFERRED, 'url': profile.DEFERRED},
                {'path': 'payload/keyrings/ubuntu-release.gpg', 'sha256': profile.DEFERRED, 'url': profile.DEFERRED},
                {'path': 'payload/bin/pairing-helper', 'sha256': 'a' * 64, 'url': 'file:///synthetic/helper'}],
                'repositories': {}, 'locks': {}},
            'models': {}, 'tei': {'variants': {}},
            'runtime': {'base_image': 'example.invalid/base@sha256:' + 'b' * 64,
                        'pairing_helper_sha256': 'a' * 64}}


class ReleaseProfile(unittest.TestCase):
    def test_missing_profile_is_legacy_iso_and_unknown_profile_refused(self):
        self.assertEqual(profile.release_profile({}), 'iso')
        for value in ('PACK', '', None, False, {}, []):
            with self.assertRaises(ValueError):
                profile.release_profile({'release_profile': value})

    def test_host_requirement_floors_cannot_be_weakened(self):
        for field, bad, good in (('docker_minimum_version', '19.2.9', '19.3'),
                                 ('compose_minimum_version', '2.29.9', '2.30.0')):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'must be at least'):
                    validator.validate_runtime_requirements({'runtime': {field: bad}})
                validator.validate_runtime_requirements({'runtime': {field: good}})
                with self.assertRaisesRegex(ValueError, 'invalid runtime'):
                    validator.validate_runtime_requirements({'runtime': {field: 'latest'}})

    def test_pack_deferred_inputs_are_ignored_and_iso_requires_them(self):
        pins = minimal()
        self.assertEqual(profile.unresolved(pins), [])
        self.assertEqual(prov.unresolved(pins), [])
        pins['release_profile'] = 'iso'
        for key in ('ubuntu.module_signer', 'ubuntu.kernel_package', 'fetch.iso_signing_fingerprint',
                    'fetch.files.0.url', 'packages.1'):
            self.assertIn(key, profile.unresolved(pins))
            self.assertIn(key, prov.unresolved(pins))

    def test_pack_still_requires_shared_and_driver_threshold_pins(self):
        pins = minimal()
        pins['runtime']['tei_minimum_driver'] = 'TO-PIN'
        pins['runtime']['cuda_minimum_driver'] = 'TO-PIN'
        pins['packages'][0] = 'docker.io=TO-PIN'
        self.assertEqual(profile.unresolved(pins), ['packages.0', 'runtime.cuda_minimum_driver', 'runtime.tei_minimum_driver'])

    def test_validate_pack_reaches_payload_and_iso_refuses_deferred_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            for which in ('pack', 'iso'):
                (stage / 'pins.json').write_text(json.dumps(minimal(which)), encoding='ascii')
                with mock.patch.object(validator, 'verify_manifest', side_effect=OSError('PAYLOAD.json')) as verify:
                    if which == 'pack':
                        with self.assertRaisesRegex(OSError, 'PAYLOAD.json'):
                            validator.validate(stage, KIT)
                        verify.assert_called_once()
                    else:
                        with self.assertRaisesRegex(ValueError, 'TO-PIN:'):
                            validator.validate(stage, KIT)
                        verify.assert_not_called()

    def test_toolkit_allowed_but_all_driver_families_refused(self):
        for name in ('nvidia-container-toolkit', 'nvidia-container-toolkit-base',
                     'libnvidia-container1', 'libnvidia-container-tools', 'docker.io'):
            self.assertFalse(profile.package_is_iso_only(name), name)
        for name in ('linux-generic', 'linux-modules-nvidia-580-server-generic', 'nvidia-utils-580-server',
                     'libnvidia-compute-580-server', 'libnvidia-gl-580-server', 'nvidia-firmware-580-server',
                     'xserver-xorg-video-nvidia-580-server', 'cuda-drivers', 'dkms', 'kmod', 'ubuntu-drivers-common'):
            self.assertTrue(profile.package_is_iso_only(name), name)

    def test_pack_repository_refuses_transitive_gl_driver_package(self):
        pins = minimal()
        pins['fetch']['repositories']['ubuntu'] = {'packages': [{'name': 'libnvidia-gl-580-server'}]}
        with self.assertRaisesRegex(ValueError, 'libnvidia-gl'):
            profile.require_package_closure(pins)
        pins['release_profile'] = 'iso'
        profile.require_package_closure(pins)

    def test_dry_run_has_no_iso_download_or_signature_plan(self):
        pins = json.loads((KIT / 'pins.json').read_text(encoding='ascii'))
        pins['release_profile'] = 'pack'
        before = copy.deepcopy(pins)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(fetch.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            fetch.plan(pins, Path('synthetic-stage'))
        lines = output.getvalue().splitlines()
        self.assertTrue(any(line.startswith('DEFERRED ') for line in lines))
        self.assertFalse(any(line.startswith('FETCH ') and ('/iso/' in line or 'ubuntu-release.gpg' in line) for line in lines))
        self.assertEqual(pins, before)

    def test_execute_fetches_shared_file_and_skips_iso_signature(self):
        pins = minimal()
        pins['tei']['variants']['synthetic'] = {'digest': 'sha256:' + 'c' * 64}
        def fake_fetch(url, path, expected):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic')
        def fake_pull(image, path):
            from runtime_fixtures import make_image
            return make_image(path, source_digest=image.rsplit('@', 1)[1])['image_id']
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / 'new-stage'
            with mock.patch.object(fetch, 'preflight'), mock.patch.object(fetch, 'fetch_file', side_effect=fake_fetch) as download, \
                    mock.patch.object(fetch, 'verify_iso', side_effect=AssertionError('ISO verification in pack')), \
                    mock.patch.object(fetch, 'pull_save', side_effect=fake_pull), \
                    mock.patch.object(fetch, 'record_base'), mock.patch.object(fetch.prov, 'digest', return_value='a' * 64), \
                    contextlib.redirect_stdout(io.StringIO()):
                fetch.execute(pins, stage)
            self.assertEqual(download.call_count, 1)
            self.assertTrue((stage / 'payload/bin/pairing-helper').is_file())
            self.assertFalse((stage / 'iso').exists())
            self.assertFalse((stage / 'payload/keyrings/ubuntu-release.gpg').exists())
            self.assertEqual(json.loads((stage / 'pins.json').read_text()), pins)

    def test_installed_wsl_packages_omit_deferred_iso_utilities(self):
        output = io.StringIO()
        with mock.patch.object(installed, 'pins', return_value=minimal()), \
                mock.patch.object(installed.sys, 'argv', ['installed-inputs.py', 'packages', 'wsl']), \
                contextlib.redirect_stdout(output):
            installed.main()
        self.assertEqual(output.getvalue(), 'docker.io=1\n')


if __name__ == '__main__':
    unittest.main()
