"""Synthetic offline stager and refusing licence-gate tests; no Docker calls."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[2] / 'src' / 'gpu-pack' / 'build'
sys.path.insert(0, str(BUILD))
import builder_stage as bs
import agpl_check


def sha(data):
    return hashlib.sha256(data).hexdigest()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='ascii', newline='\n')


def put_json(path, value):
    put(path, json.dumps(value, sort_keys=True, indent=2) + '\n')


def image_archive(path, role):
    config = json.dumps({'architecture': 'amd64', 'os': 'linux', 'role': role}, sort_keys=True).encode('ascii')
    config_name = sha(config) + '.json'
    manifest = json.dumps([{'Config': config_name, 'RepoTags': [], 'Layers': ['layer.tar']}]).encode('ascii')
    with tarfile.open(path, 'w') as out:
        for name, data in ((config_name, config), ('manifest.json', manifest), ('layer.tar', b'test layer')):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            out.addfile(member, io.BytesIO(data))
    return 'sha256:' + sha(config)


class BuilderStage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ctx = self.root / 'ctx'
        self.kit = self.root / 'kit'
        self.archives = self.root / 'archives'
        self.archives.mkdir()
        self.out = self.root / 'new-stage'
        self.refs = {'base': bs.PREFIX['base'] + '@sha256:' + '1' * 64,
                     'qdrant': bs.PREFIX['qdrant'] + '@sha256:' + '2' * 64}
        put(self.ctx / 'compose/engine.Dockerfile', 'FROM ' + self.refs['base'] + '\n')
        put_json(self.ctx / 'compose/compose.json', {'services': {
            'qdrant': {'image': self.refs['qdrant'].replace(':v1.19.1', '')},
            'qdrant-c3': {'image': self.refs['qdrant'].replace(':v1.19.1', '')}}})
        put(self.kit / 'containers/builder/requirements-engine.in', 'example==1.0\n')
        wheel = self.ctx / 'wheelhouse/engine/example-1.0-py3-none-any.whl'
        put(wheel, 'synthetic wheel\n')
        put(self.ctx / 'locks/engine.lock', 'example==1.0 --hash=sha256:' + sha(wheel.read_bytes()) + '\n')
        closure = []
        for package in ('age', 'git', 'gnupg', 'gpgv'):
            filename = package + '_1.0_amd64.deb'
            data = ('synthetic closure ' + package + '\n').encode('ascii')
            path = self.ctx / 'builder/debs' / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            closure.append({'package': package, 'version': '1.0', 'path': filename, 'sha256': sha(data)})
        put_json(self.ctx / 'builder/debs.json', closure)
        self.ids = {}
        for role in ('base', 'qdrant'):
            self.ids[role] = image_archive(self.archives / (role + '.tar'), role)
            put_json(self.archives / (role + '.inspect.json'), [{
                'Id': self.ids[role], 'Os': 'linux', 'Architecture': 'amd64',
                'RepoDigests': [bs.REPOSITORY[role] + '@' + self.refs[role].split('@')[1]],
                'RepoTags': [bs.PREFIX[role]], 'Config': {'Labels': {}}}])

    def run_stage(self):
        return bs.stage(self.ctx, self.out, self.kit, self.archives)

    def test_stages_all_measured_inputs_and_existing_verifier_accepts(self):
        with patch.object(bs.subprocess, 'run', side_effect=AssertionError('offline fixture must not spawn')):
            result = self.run_stage()
        config = json.loads((self.out / 'builder/pins.json').read_text(encoding='ascii'))
        self.assertEqual(config, bs.checked_inputs(self.out, self.kit))
        self.assertEqual(json.loads((self.out / 'builder-pins.json').read_text(encoding='ascii')), config)
        self.assertEqual((result['wheel_count'], result['deb_count'], result['inventory_count']), (1, 4, 10))
        self.assertNotIn('TO-PIN', json.dumps(config))
        self.assertEqual(config['base']['image_id'], self.ids['base'])
        self.assertEqual(config['qdrant']['image'], self.refs['qdrant'])
        for role in ('base', 'qdrant'):
            self.assertEqual(config[role]['archive_sha256'], sha((self.out / 'builder' / (role + '.tar')).read_bytes()))

    def test_deterministic_pins_and_inventory_for_identical_sources(self):
        self.run_stage()
        second = self.root / 'second'
        bs.stage(self.ctx, second, self.kit, self.archives)
        for relative in ('builder/pins.json', 'builder/INPUTS.json', 'builder-pins.json'):
            self.assertEqual((self.out / relative).read_bytes(), (second / relative).read_bytes())

    def test_never_opens_env_or_engine_tree(self):
        # The guard rejects excluded names even if they do not exist; do not
        # create or open an environment/evidence file in this fixture either.
        put(self.ctx / 'engine/unused.txt', 'UNREAD TEST SENTINEL\n')
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.suffix == '.env' or 'engine/unused' in path.as_posix() or path.name == 'permissions-evidence':
                raise AssertionError('stager opened an excluded path')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            self.run_stage()

    def test_refuses_unresolved_rendered_base_without_output(self):
        put(self.ctx / 'compose/engine.Dockerfile', 'FROM python:3.12-slim-trixie@sha256:TO-PIN-PYTHON\n')
        with self.assertRaisesRegex(ValueError, 'single pinned trixie'):
            self.run_stage()
        self.assertFalse(self.out.exists())

    def test_refuses_qdrant_digest_disagreement(self):
        path = self.ctx / 'compose/compose.json'
        value = json.loads(path.read_text())
        value['services']['qdrant-c3']['image'] = self.refs['qdrant']
        put_json(path, value)
        with self.assertRaisesRegex(ValueError, 'digests differ'):
            self.run_stage()

    def test_refuses_absent_debian_closure_before_writing(self):
        (self.ctx / 'builder/debs.json').unlink()
        with self.assertRaisesRegex(ValueError, 'regular input file'):
            self.run_stage()
        self.assertFalse(self.out.exists())

    def test_refuses_extra_and_changed_debs(self):
        put(self.ctx / 'builder/debs/unlisted.deb', 'not listed\n')
        with self.assertRaisesRegex(ValueError, 'unlisted'):
            self.run_stage()
        (self.ctx / 'builder/debs/unlisted.deb').unlink()
        put(self.ctx / 'builder/debs/git_1.0_amd64.deb', 'changed\n')
        with self.assertRaisesRegex(ValueError, 'hash differs: git'):
            self.run_stage()

    def test_refuses_missing_required_debian_package(self):
        manifest = self.ctx / 'builder/debs.json'
        rows = json.loads(manifest.read_text())
        put_json(manifest, [r for r in rows if r['package'] != 'gpgv'])
        with self.assertRaisesRegex(ValueError, 'include git, age, gnupg and gpgv'):
            self.run_stage()

    def test_refuses_wheel_hash_drift_and_lock_pin_drift(self):
        wheel = next((self.ctx / 'wheelhouse/engine').iterdir())
        put(wheel, 'changed\n')
        with self.assertRaisesRegex(ValueError, 'wheelhouse hash differs'):
            self.run_stage()
        put(self.kit / 'containers/builder/requirements-engine.in', 'other==1.0\n')
        with self.assertRaisesRegex(ValueError, 'lock differs'):
            self.run_stage()

    def test_refuses_inspect_digest_platform_version_or_id_mismatch(self):
        path = self.archives / 'qdrant.inspect.json'
        original = json.loads(path.read_text())
        for field, bad, message in (('RepoDigests', [], 'repository digest'),
                                    ('Architecture', 'arm64', 'linux/amd64'),
                                    ('RepoTags', [], 'establish Qdrant'),
                                    ('Id', 'sha256:' + 'f' * 64, 'image id differ')):
            with self.subTest(field=field):
                value = json.loads(json.dumps(original))
                value[0][field] = bad
                put_json(path, value)
                with self.assertRaisesRegex(ValueError, message):
                    self.run_stage()
                self.assertFalse(self.out.exists())
        put_json(path, original)

    def test_refuses_multiple_image_archive_and_traversal(self):
        path = self.archives / 'base.tar'
        with tarfile.open(path, 'a') as out:
            member = tarfile.TarInfo('../escape')
            member.size = 1
            out.addfile(member, io.BytesIO(b'x'))
        with self.assertRaisesRegex(ValueError, 'unsafe image archive'):
            self.run_stage()
        image_archive(path, 'base')
        with tarfile.open(path, 'a') as out:
            member = tarfile.TarInfo('manifest.json')
            data = b'[]'
            member.size = len(data)
            out.addfile(member, io.BytesIO(data))
        with self.assertRaisesRegex(ValueError, 'unsafe image archive'):
            self.run_stage()

    def test_refuses_existing_output_without_modifying_it(self):
        put(self.out / 'keep.txt', 'keep\n')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_stage()
        self.assertEqual((self.out / 'keep.txt').read_text(), 'keep\n')

    def test_local_docker_path_uses_only_inspect_and_save_by_id(self):
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ['image', 'inspect']:
                role = 'base' if argv[3] == self.refs['base'] else 'qdrant'
                return subprocess.CompletedProcess(argv, 0, stdout=(self.archives / (role + '.inspect.json')).read_text())
            self.assertEqual(argv[1:4], ['image', 'save', '--output'])
            role = next(role for role in self.ids if self.ids[role] == argv[-1])
            Path(argv[4]).write_bytes((self.archives / (role + '.tar')).read_bytes())
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(bs.subprocess, 'run', side_effect=fake_run):
            bs.stage(self.ctx, self.out, self.kit, docker='local-docker')
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(c[0] == 'local-docker' for c in calls))
        self.assertEqual([c[-1] for c in calls if c[2] == 'save'], [self.ids['base'], self.ids['qdrant']])

    def test_failed_local_save_removes_only_new_output(self):
        def fake_run(argv, **kwargs):
            if argv[2] == 'save':
                raise subprocess.CalledProcessError(1, argv, stderr=b'synthetic private metadata')
            role = 'base' if argv[3] == self.refs['base'] else 'qdrant'
            return subprocess.CompletedProcess(argv, 0, stdout=(self.archives / (role + '.inspect.json')).read_text())
        with patch.object(bs.subprocess, 'run', side_effect=fake_run):
            with self.assertRaises(subprocess.CalledProcessError):
                bs.stage(self.ctx, self.out, self.kit)
        self.assertFalse(self.out.exists())
        self.assertTrue((self.ctx / 'locks/engine.lock').is_file())

    def test_cli_offline_and_clean_error(self):
        command = [sys.executable, '-I', '-B', str(BUILD / 'builder_stage.py'), '--ctx', str(self.ctx),
                   '--out', str(self.out), '--kit', str(self.kit), '--archives', str(self.archives)]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['wheel_count'], 1)
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('REFUSE', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


class LicenceGate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'payload/licences'
        self.folder.mkdir(parents=True)
        self.pins = self.root / 'pins.json'

    def test_placeholder_refuses(self):
        put(self.folder / 'AGPL-3.0.txt', 'TO-PIN-AGPL-TEXT: REFUSING PLACEHOLDER\n')
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            agpl_check.check(self.root, self.pins)

    def synthetic_licence(self):
        # Generated fixture, not a licence or a substitute for the actual text.
        data = (b'GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007\n'
                + b'SYNTHETIC STRUCTURE TEST\n' * 1400 + b'END OF TERMS AND CONDITIONS\n')
        (self.folder / 'AGPL-3.0.txt').write_bytes(data)
        put_json(self.pins, {'fetch': {'files': [{'path': 'payload/licences/AGPL-3.0.txt',
                   'url': 'file:///synthetic/AGPL-3.0.txt', 'sha256': sha(data)}]}})
        return data

    def test_pinned_staged_text_accepts(self):
        data = self.synthetic_licence()
        self.assertEqual(agpl_check.check(self.root, self.pins), sha(data))

    def test_wrong_pin_or_changed_text_refuses(self):
        self.synthetic_licence()
        put_json(self.pins, {'fetch': {'files': [{'path': 'payload/licences/AGPL-3.0.txt', 'sha256': '4' * 64}]}})
        with self.assertRaisesRegex(ValueError, 'payload licence pin'):
            agpl_check.check(self.root, self.pins)
        (self.folder / 'AGPL-3.0.txt').write_bytes((self.folder / 'AGPL-3.0.txt').read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'payload licence pin'):
            agpl_check.check(self.root, self.pins)

    def test_network_source_and_truncation_refuse(self):
        self.synthetic_licence()
        pins = json.loads(self.pins.read_text(encoding='ascii'))
        pins['fetch']['files'][0]['url'] = 'https://example.invalid/AGPL-3.0.txt'
        put_json(self.pins, pins)
        with self.assertRaisesRegex(ValueError, 'local file source'):
            agpl_check.check(self.root, self.pins)
        (self.folder / 'AGPL-3.0.txt').write_bytes(b'GNU AFFERO GENERAL PUBLIC LICENSE')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            agpl_check.check(self.root, self.pins)


if __name__ == '__main__':
    unittest.main()
