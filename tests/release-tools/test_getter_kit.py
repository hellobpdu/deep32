"""Offline extracted-getter closure tests with invented bytes and fake GPG."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

TOOLS = Path(__file__).resolve().parents[2] / 'src/release-tools'
SCRATCH = None


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


kit = load('getter_export', TOOLS / 'getter_kit.py')
trust = load('getter_kit_trust_fakes', Path(__file__).with_name('test_whole_trust.py'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


class GetterKit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kit-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = kit.archive_bytes()
        self.extracted = self.root / 'extracted'
        with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
            archive.extractall(self.extracted)
        self.tools = self.extracted / 'src/release-tools'

    def test_deterministic_exact_closure_and_inventory(self):
        self.assertEqual(self.data, kit.archive_bytes())
        with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted([*kit.FILES, 'GETTER-KIT.json']))
            inventory = json.loads(archive.read('GETTER-KIT.json'))
            for row in inventory['files']:
                data = archive.read(row['path'])
                self.assertEqual((len(data), sha(data)), (row['size'], row['sha256']))
            for entry in archive.infolist():
                self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
                self.assertEqual((entry.external_attr >> 16) & 0o777,
                                 0o755 if entry.filename.endswith('.sh') else 0o644)

    def test_exclusive_output_preserves_existing_bytes(self):
        output = self.root / 'kit.zip'
        self.assertEqual(kit.build(output), len(self.data))
        self.assertEqual(output.read_bytes(), self.data)
        with self.assertRaises(FileExistsError):
            kit.build(output)
        self.assertEqual(output.read_bytes(), self.data)

    def test_missing_source_refused(self):
        with self.assertRaises(FileNotFoundError):
            kit.archive_bytes(self.root / 'absent')

    def test_extracted_isolated_helper_help(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(self.tools / 'deep32_get_whole.py'), '--help'],
                                cwd=self.root, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'--from', result.stdout)

    def test_extracted_windows_pairing_uses_the_complete_signed_runtime(self):
        adapter = load('extracted_windows_pairing', self.tools / 'forge_pair_windows.py')
        pack = self.extracted / 'src/gpu-pack'
        original_pack = TOOLS.parent / 'gpu-pack'
        forms = ('host_security.py', 'installed_runtime.py', 'forge_publication.py')
        for name in forms:
            self.assertEqual((pack / 'forms' / name).read_bytes(),
                             (original_pack / 'forms' / name).read_bytes())
        pairing_files = load('pairing_members', original_pack / 'build/thin_forms.py').pairing_files(original_pack)
        expected = {path.relative_to(original_pack).as_posix() for path in pairing_files
                    if path.name != 'deep32-pair'}
        actual = {path.relative_to(pack).as_posix() for path in (pack / 'pairing').rglob('*') if path.is_file()}
        self.assertEqual(actual, expected)
        self.assertEqual(adapter.trusted_pairing(original_pack), pack / 'pairing')

    def test_extracted_strict_mirror_schema(self):
        path = self.root / 'synthetic-mirrors.json'
        value = json.loads((TOOLS / 'contracts/fixtures/valid/mirrors.json').read_bytes())
        args = [sys.executable, '-I', '-B', str(self.tools / 'contract_validate.py'), 'mirrors', str(path)]
        path.write_text(json.dumps(value), encoding='ascii')
        self.assertEqual(subprocess.run(args, capture_output=True, timeout=20).returncode, 0)
        value['unexpected'] = 'refuse'
        path.write_text(json.dumps(value), encoding='ascii')
        self.assertEqual(subprocess.run(args, capture_output=True, timeout=20).returncode, 2)

    def test_extracted_capability_authentication_and_class_refusal(self):
        cap = load('extracted_capabilities', self.tools / 'capabilities.py')
        raw = (TOOLS / 'contracts/fixtures/capabilities/manifest.json').read_bytes()
        path = self.root / 'synthetic-capabilities.json'
        path.write_bytes(raw)
        value = cap.read_authenticated(path, sha(raw))
        getter = load('extracted_capability_getter', self.tools / 'deep32_get_whole.py')
        self.assertEqual([row['id'] for row in getter.select_bundles(value, 'ada')],
                         ['core', 'embedder-ada'])
        with self.assertRaises(getter.manifest.Refusal):
            getter.select_bundles(value, 'unsupported')
        path.write_bytes(raw + b' ')
        with self.assertRaises(ValueError):
            cap.read_authenticated(path, sha(raw))

    def test_extracted_local_image_verification_and_tamper(self):
        getter = load('extracted_local_getter', self.tools / 'deep32_get_whole.py')
        source = self.root / 'synthetic-local-media'
        source.mkdir()
        value = json.loads((TOOLS / 'contracts/fixtures/valid/mirrors.json').read_bytes())
        sums = {}
        for disc in value['discs']:
            raw = ('SYNTHETIC KIT IMAGE ' + disc['role'] + '\n').encode('ascii')
            disc.update(size=len(raw), sha256=sha(raw), chunk_sha256=[sha(raw)])
            (source / disc['file']).write_bytes(raw)
            sums[disc['file']] = sha(raw)
        raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode('ascii')
        (source / 'MIRRORS.json').write_bytes(raw)
        sums['MIRRORS.json'] = sha(raw)
        (source / 'SHA256SUMS').write_bytes(''.join('%s  %s\n' % (v, k) for k, v in sorted(sums.items())).encode('ascii'))
        (source / 'SHA256SUMS.sig').write_bytes(b'SYNTHETIC detached signature\n')
        key = self.root / 'synthetic-public.gpg'
        key.write_bytes(b'SYNTHETIC PUBLIC TEST KEY\n')
        args = getter.parser().parse_args(['disc2', '--from', str(source), '--key', str(key), '--fingerprint', trust.FINGERPRINT])
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
        fake = trust.FakeGpg()
        with mock.patch.object(getter.manifest, 'gpg_executable', return_value=key), \
             mock.patch.object(getter.manifest.subprocess, 'Popen', side_effect=fake.popen), \
             contextlib.redirect_stderr(io.StringIO()):
            records = getter.get(args, opener=mock.Mock(side_effect=AssertionError('Local mode contacted transport')))
            self.assertEqual(len(records), 1)
            self.assertEqual(Path(records[0][0]), source / value['discs'][1]['file'])
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()})
            Path(records[0][0]).write_bytes(b'corrupt synthetic image')
            with self.assertRaises((ValueError, getter.manifest.Refusal, getter.transfer.DownloadError)):
                getter.get(args)

    def test_extracted_shared_core_and_exact_class_in_place(self):
        getter = load('extracted_shared_getter', self.tools / 'deep32_get_whole.py')
        contracts = getter.manifest.contract_module()
        source = self.root / 'synthetic-shared-media'
        source.mkdir()
        value = json.loads((TOOLS / 'contracts/fixtures/valid/mirrors.json').read_bytes())
        old_gpu = next(row for row in value['discs'] if row['role'] == 'disc3')
        value['discs'] = [row for row in value['discs'] if row['role'] != 'disc3']
        value['format_version'] = 2
        cap_raw = (TOOLS / 'contracts/fixtures/capabilities/manifest.json').read_bytes()
        capability = json.loads(cap_raw)
        cap_name = 'CAPABILITIES.json'
        value['capability_manifest'] = {'file': cap_name, 'sha256': sha(cap_raw)}
        (source / cap_name).write_bytes(cap_raw)
        sums = {cap_name: sha(cap_raw)}
        value['bundles'] = []
        for bundle in capability['bundles']:
            if bundle['kind'] == 'corresponding-source-build':
                continue
            row = {key: copy.deepcopy(item) for key, item in old_gpu.items()
                   if key not in ('role', 'kind')}
            row['bundle_id'] = bundle['id']
            row['bundle_sha256'] = sha(contracts.canonical(bundle))
            row['file'] = 'deep32-0.1.0-alpha.3-disc3-gpu-pack-' + bundle['id'] + '.iso'
            for mirror in row['mirrors']:
                mirror['url'] = mirror['url'].rsplit('/', 1)[0] + '/' + row['file']
            value['bundles'].append(row)
        for row in [*value['discs'], *value['bundles']]:
            data = ('SYNTHETIC KIT SUBJECT ' + row.get('bundle_id', row.get('role')) + '\n').encode('ascii')
            row.update(size=len(data), sha256=sha(data), chunk_sha256=[sha(data)])
            (source / row['file']).write_bytes(data)
            sums[row['file']] = sha(data)
        raw = contracts.canonical(value)
        (source / 'MIRRORS.json').write_bytes(raw)
        sums['MIRRORS.json'] = sha(raw)
        requirements = load('extracted_requirements', self.tools / 'forge_requirements.py')
        qualification = {
            'format': 'deep32-forge-prerequisites/1', 'release': capability['release'],
            'capability_sha256': sha(cap_raw), 'pins_sha256': capability['pins_sha256'],
            'recipes': [{'id': 'synthetic-accepted', 'accepted_on': '2026-11-18', 'os_id': 'ubuntu',
                         'os_version': '24.04', 'architecture': 'amd64', 'journey': 'J2',
                         'recipe_sha256': 'c' * 64, 'acceptance_sha256': 'b' * 64}],
            'driver_floors': {'tei_minimum_driver': '800.1', 'cuda_minimum_driver': '810.2',
                              'mineru_minimum_driver': '805.0'},
            'minimum_vram_mib': 10752, 'vram_policy': 'C1-install-matrix-tier-11',
        }
        qualified_raw = contracts.canonical(qualification)
        (source / requirements.ASSET).write_bytes(qualified_raw)
        sums[requirements.ASSET] = sha(qualified_raw)
        (source / 'SHA256SUMS').write_bytes(''.join('%s  %s\n' % (v, k)
            for k, v in sorted(sums.items())).encode('ascii'))
        (source / 'SHA256SUMS.sig').write_bytes(b'SYNTHETIC detached signature\n')
        key = self.root / 'synthetic-shared-public.gpg'
        key.write_bytes(b'SYNTHETIC PUBLIC TEST KEY\n')
        args = getter.parser().parse_args(['disc3', '--from', str(source), '--gpu-class', 'turing',
            '--key', str(key), '--fingerprint', trust.FINGERPRINT])
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
        fake = trust.FakeGpg()
        with mock.patch.object(getter.manifest, 'gpg_executable', return_value=key), \
             mock.patch.object(getter.manifest.subprocess, 'Popen', side_effect=fake.popen), \
             contextlib.redirect_stderr(io.StringIO()):
            records = getter.get(args, opener=mock.Mock(side_effect=AssertionError('Local mode contacted transport')))
            args.ingestion = True
            args.forge_data_root = str(self.root / 'synthetic-new-forge')
            docker_info = json.dumps({'OSType': 'linux', 'Driver': 'overlay2', 'DriverStatus': [],
                                      'DockerRootDir': '/var/lib/docker'}).encode('ascii')

            def fixture_volume(path, *, existing):
                target = str(self.root) if path == '/var/lib/docker' else path
                return getter.observed_volume(target, existing=existing)
            with mock.patch.object(getter.transfer, 'open_http', side_effect=AssertionError('Storage contacted transport')), \
                    mock.patch.object(getter.transfer, 'verify_local_image', side_effect=AssertionError('Budget rehashed ISOs')):
                volumes = getter.linux_forge_storage(args, docker_info, observe=fixture_volume)
            self.assertEqual(len(volumes), 2)
            self.assertEqual(volumes[0]['filesystem_id'], volumes[1]['filesystem_id'])
            selected = [bundle for bundle in capability['bundles']
                        if bundle['id'] in {'core', 'embedder-turing', 'ingestion'}]
            for index, name in enumerate(('data', 'docker')):
                expected = sum(domain['peak_bytes'] for bundle in selected
                               for budget in bundle['storage_budgets'] if budget['backend'] == 'docker-engine'
                               for domain in budget['domains'] if domain['domain'] == name)
                self.assertEqual(volumes[index]['needed_bytes'], expected)
            self.assertFalse(Path(args.forge_data_root).exists())
            # Exercise the packaged evidence module through real release metadata
            # and C7 plan validation. The signature subprocess and host tools
            # see only synthetic inputs.
            preflight = load('extracted_forge_preflight', self.tools / 'forge_preflight.py')
            settings = preflight.settings
            tailnet = 'synthetic-forge-net.ts.net'
            choices = {'format': 'deep32-settings', 'format_version': 1, 'role': 'forge',
                       **settings.DEFAULTS, 'forge_accept_ct': True}
            media = [dict(component=row['bundle_id'], size=row['size'], sha256=row['sha256'])
                     for row in value['bundles'] if row['bundle_id'] in {'core', 'embedder-turing', 'ingestion'}]
            media.sort(key=lambda row: ('core', 'embedder-turing', 'ingestion').index(row['component']))
            plan = settings.make_plan(choices, media[0]['sha256'], source, 'J2', 'docker', 'linux',
                                      'forge', settings.validator().profiles, args.forge_data_root,
                                      media_components=media, tailnet_binding=settings.tailnet_digest(tailnet))
            plan_path = self.root / 'synthetic-consented-plan.json'
            plan_path.write_bytes(settings.canonical(plan))
            args.plan, args.accept_plan, args.journey = str(plan_path), plan['digest'], 'J2'
            args.os_id, args.os_version, args.architecture = 'ubuntu', '24.04', 'x86_64'
            observations = {'docker': json.loads(docker_info), 'tailscale': {
                'BackendState': 'Running',
                'Self': {'Online': True, 'DNSName': 'synthetic-forge.' + tailnet},
                'CurrentTailnet': {'MagicDNSSuffix': tailnet},
            }}
            hardware = subprocess.CompletedProcess(preflight.GPU_QUERY, 0, '810.2, 7.5, 10752\n')
            with mock.patch.object(preflight.getter.manifest, 'gpg_executable', return_value=key), \
                    mock.patch.object(preflight.getter.manifest.subprocess, 'Popen',
                                      side_effect=trust.FakeGpg().popen), \
                    mock.patch.object(preflight.ports, 'check_linux_ports') as port_check:
                result = preflight.collect(args, observations, run=mock.Mock(return_value=hardware),
                                           observe=fixture_volume)
            expected_volumes = [(row['filesystem_id'], row['needed_bytes']) for row in volumes]
            observed_volumes = [(row['filesystem_id'], row['needed_bytes']) for row in result['storage_plan']]
            self.assertEqual(expected_volumes, observed_volumes)
            self.assertTrue(all(row['free_bytes'] > 0 for row in result['storage_plan']))
            self.assertEqual(set(result['prerequisites']), {
                'docker.storage', 'platform.recipe', 'host.membership-confirmed', 'gpu.release-envelope',
                'gpu.driver', 'gpu.memory', 'gpu.capability', 'gpu.ports',
            })
            self.assertTrue(all(result['prerequisites'].values()))
            port_check.assert_called_once()
            (source / cap_name).write_bytes(cap_raw + b' ')
            try:
                with self.assertRaises(getter.manifest.Refusal):
                    getter.linux_forge_storage(args, docker_info, observe=fixture_volume)
            finally:
                (source / cap_name).write_bytes(cap_raw)
                os.utime(source / cap_name, ns=(before[cap_name][1], before[cap_name][1]))
        self.assertEqual([record[1] for record in records], [
            'deep32-0.1.0-alpha.3-disc3-gpu-pack-core.iso',
            'deep32-0.1.0-alpha.3-disc3-gpu-pack-embedder-turing.iso'])
        self.assertTrue(all(Path(record[0]).parent == source for record in records))
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()})

    def test_archive_tamper_fails_expected_release_digest(self):
        manifest = load('kit_digest_check', self.tools / 'deep32_get_manifest.py')
        path = self.root / 'synthetic-kit.zip'
        path.write_bytes(self.data)
        manifest.check_file(path, len(self.data), sha(self.data))
        path.write_bytes(self.data[:-1] + bytes([self.data[-1] ^ 1]))
        with self.assertRaises(manifest.Refusal):
            manifest.check_file(path, len(self.data), sha(self.data))

    def test_extracted_install_and_vm_support_paths(self):
        settings = load('extracted_getter_settings', self.tools / 'deep32_settings.py')
        self.assertIn('headless', settings.validator().profiles)
        self.assertTrue((self.extracted / 'src/workstation/release/tools/raw2vhd.py').is_file())
        self.assertTrue((self.extracted / 'install/deep32-inventory.sh').is_file())
        self.assertTrue((self.extracted / 'install/deep32-inventory.ps1').is_file())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', type=Path, required=True)
    args = parser.parse_args()
    SCRATCH = args.scratch_root
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if any(SCRATCH.iterdir()):
        parser.error('scratch root must be empty')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(GetterKit))
    raise SystemExit(not result.wasSuccessful())
