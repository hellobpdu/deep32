"""Synthetic owner inventory and plan qualification without Docker."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / 'src/release-tools'
BUILD = REPO / 'src/gpu-pack/build'
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(BUILD))
from sbom import owner_inputs as owner
from sbom.common import canonical, digest, load_json, public_path
import alpha2_disc3 as producer
import alpha2_metadata as metadata
import alpha2_rebuild as rebuild
from image_binding import archive_binding
from provenance import members

EPOCH = 946684800
REVISION = '1' * 40
VERSIONS = {'synthetic-deb': '1:2.3-4', 'synthetic-pre': '1.0~rc1-1',
            'synthetic-epoch': '1!2.0', 'synthetic-local': '2.1+cpu'}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value), encoding='ascii', newline='\n')


def saved_image(path, label):
    config = canonical(dict(os='linux', architecture='amd64', label=label,
                            rootfs=dict(type='layers', diff_ids=[]))).encode('ascii')
    config_name = hashlib.sha256(config).hexdigest() + '.json'
    manifest = canonical([dict(Config=config_name, Layers=[], RepoTags=[])]).encode('ascii')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in ((config_name, config), ('manifest.json', manifest)):
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o644
            entry.mtime = EPOCH
            archive.addfile(entry, io.BytesIO(data))
    return dict(archive_binding(path), sha256=digest(path))


def dependencies(application=False):
    return dict(format='deep32-installed-dependencies/1',
                debian=['synthetic-deb\t1:2.3-4\tamd64',
                        'synthetic-pre\t1.0~rc1-1\tall'],
                python=[dict(name=name, version=VERSIONS[name],
                             files=[dict(path=name.replace('-', '_') + '/module.py',
                                         size=3, sha256=checksum)])
                        for name, checksum in [('synthetic-epoch', 'a' * 64),
                                               ('synthetic-local', 'b' * 64)]],
                application=([dict(path='/app/synthetic.py', size=7, sha256='c' * 64)]
                             if application else []))


class Fixture:
    def __init__(self, base):
        self.work = base / 'work'
        self.rows = {}
        self.commands = []
        self.probe = dependencies()
        self.probe_mode = 'python'
        self.wrong_inspection = False
        write(self.work / 'SOURCE.json', dict(revision=REVISION))
        for component in producer.COMPONENTS:
            stage = self.stage(component)
            roles = (('door', 'reranker') if component == 'core' else
                     ('builder', 'converter', 'qdrant') if component == 'ingestion' else
                     ('tei-' + component.removeprefix('embedder-'),))
            rows = []
            for role in roles:
                archive = stage / 'payload/images' / (role + '.tar')
                row = dict(saved_image(archive, role), role=role, path='images/' + role + '.tar')
                self.rows[row['config_digest']] = row
                rows.append(row)
            write(stage / 'payload/images/index.json', sorted(rows, key=lambda row: row['role']))
            source = stage / 'payload/source/public.txt'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'Synthetic public component input.\n')
            write(stage / 'pins.json', {'synthetic': True})
            write(stage / 'RUNTIME.json', {'synthetic': component})
            self.reseal(component)
            image = self.image(component)
            image.parent.mkdir(parents=True)
            image.write_bytes(b'Synthetic frozen image.\n')
            write(image.parent / 'IMAGE.json', dict(path=image.name, size=image.stat().st_size,
                                                   sha256=digest(image)))

    def stage(self, component):
        return self.work / 'stages' / component

    def media(self, component):
        return self.work / 'components' / component / 'media'

    def image(self, component):
        return self.work / 'sbom-input-images' / component / producer.image_name(component)

    def reseal(self, component):
        stage, media = self.stage(component), self.media(component)
        write(stage / 'PAYLOAD.json', members(stage / 'payload'))
        shutil.copytree(stage / 'payload', media / 'deep32/payload', dirs_exist_ok=True)
        for name in ('PAYLOAD.json', 'RUNTIME.json'):
            shutil.copyfile(stage / name, media / 'deep32' / name)
        shutil.copyfile(stage / 'pins.json', media / 'pins.json')
        (media / 'SHA256SUMS').write_bytes(b'Synthetic generated checksum fixture.\n')

    def runner(self, command, **kwargs):
        self.commands.append((list(command), kwargs))
        if command[:3] == ['docker', 'image', 'load']:
            archive = Path(command[command.index('--input') + 1])
            assert archive.is_file()
            return SimpleNamespace(stdout='', returncode=0)
        if command[:3] == ['docker', 'image', 'inspect']:
            identity = command[3]
            assert identity in self.rows
            value = [{'Id': 'sha256:' + 'f' * 64 if self.wrong_inspection else identity,
                      'Os': 'linux', 'Architecture': 'amd64'}]
            return SimpleNamespace(stdout=canonical(value), returncode=0)
        if command[:2] == ['docker', 'run']:
            entrypoint = command[command.index('--entrypoint') + 1]
            identity = command[command.index('--entrypoint') + 2]
            assert identity in self.rows
            if entrypoint == 'python':
                assert command == rebuild.probe_command(identity)
                if self.probe_mode in {'native', 'python3', 'failure'}:
                    raise subprocess.CalledProcessError(127, command)
                raw = '{"format":' if self.probe_mode == 'invalid-json' else canonical(self.probe)
            else:
                assert entrypoint == '/bin/sh'
                original = rebuild.probe_command(identity)
                boundary = original.index('--entrypoint')
                assert command[:boundary] == original[:boundary]
                assert command[boundary:boundary + 3] == ['--user', '0:0', '--cap-add=DAC_READ_SEARCH']
                assert command[boundary + 6] == '-ec'
                assert command[-1] == rebuild.DEPENDENCY_PROBE
                if self.probe_mode == 'failure':
                    raise subprocess.CalledProcessError(21, command)
                assert self.probe_mode in {'native', 'python3'}
                raw = ('deep32-owner-native-dependencies/1\n' +
                       '\n'.join(self.probe['debian']) + '\n' if self.probe_mode == 'native'
                       else canonical(self.probe))
            return SimpleNamespace(stdout=raw, returncode=0)
        raise AssertionError('Unexpected external command in the synthetic runner.')


class OwnerInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owner-inputs-tests-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fixture = Fixture(self.base)
        self.inventories = self.fixture.work / 'owner-inventories'

    def inventories_for(self, fixture=None, out=None):
        fixture = fixture or self.fixture
        return owner.disc3_inventories(fixture.work, out or self.inventories,
                                       epoch=EPOCH, runner=fixture.runner)

    def plans(self, output='plans', **kwargs):
        return owner.disc3_plans(self.fixture.work, self.base / output, epoch=EPOCH, **kwargs)

    def assert_public_tree(self, folder):
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                public_path(path.relative_to(folder).as_posix())
                text = path.read_text(encoding='ascii')
                self.assertNotIn(str(self.base), text)
                self.assertNotIn(self.base.as_posix(), text)

    def test_inventory_retains_every_exact_version_and_file_hash(self):
        before = members(self.fixture.work)
        result = self.inventories_for()
        self.assertEqual(result['format'], 'deep32-owner-inventories/1')
        self.assertEqual({row['component'] for row in result['components']}, set(producer.COMPONENTS))
        expected_probes = len(self.fixture.rows)
        self.assertEqual(sum(row['images'] for row in result['components']), expected_probes)
        self.assertEqual(sum(row['components'] for row in result['components']), expected_probes * 4)
        for component in producer.COMPONENTS:
            folder = self.inventories / component
            seal = load_json(folder / 'SEAL.json')
            measured = [row for row in members(folder) if row['path'] != 'SEAL.json']
            self.assertEqual(seal['files'], measured)
            for record in seal['images']:
                inventory = load_json(folder / record['inventory'])
                self.assertEqual(inventory['format'], 'deep32-sbom-image-inventory/1')
                self.assertEqual(inventory['config_digest'], record['binding']['config_digest'])
                self.assertEqual(inventory['coverage'], 'complete')
                rows = {item['name']: item for item in inventory['components']}
                self.assertEqual({name: item['version'] for name, item in rows.items()}, VERSIONS)
                self.assertEqual({name: item['kind'] for name, item in rows.items()},
                                 {name: 'deb' if name in {'synthetic-deb', 'synthetic-pre'}
                                  else 'base-package' for name in VERSIONS})
                for item in rows.values():
                    self.assertEqual(len(item['files']), 1)
                    ref = item['files'][0]
                    self.assertEqual(ref['root'], 'installed')
                    evidence = load_json(folder / ref['path'])
                    self.assertEqual(evidence['format'], 'deep32-installed-component/1')
                    self.assertEqual(evidence['config_digest'], inventory['config_digest'])
                    self.assertEqual(evidence['measurement']['version'], item['version'])
                    if evidence['ecosystem'] == 'python':
                        original = next(row for row in self.fixture.probe['python']
                                        if row['name'] == item['name'])
                        self.assertEqual(evidence['measurement'], original)
        commands = [row[0] for row in self.fixture.commands]
        probes = [command for command in commands if command[:2] == ['docker', 'run']]
        self.assertEqual(len(probes), expected_probes)
        for command in probes:
            self.assertEqual(command[command.index('--network') + 1], 'none')
            for flag in ('--read-only', '--pull=never', '--cap-drop=ALL',
                         '--security-opt=no-new-privileges'):
                self.assertIn(flag, command)
        for _, kwargs in self.fixture.commands:
            self.assertTrue(kwargs['check'])
            self.assertEqual(kwargs['env']['SOURCE_DATE_EPOCH'], str(EPOCH))
        self.assertEqual(before, [row for row in members(self.fixture.work)
                                  if not row['path'].startswith('owner-inventories/')])
        self.assert_public_tree(self.inventories)

    def test_all_nine_plans_pass_the_actual_component_emitter(self):
        self.inventories_for()
        before = members(self.fixture.work)
        result = self.plans()
        self.assertEqual(set(result['plans']), {name + '.json' for name in producer.COMPONENTS})
        self.assertEqual({path.stem for path in (self.base / 'plans').glob('*.json')},
                         set(producer.COMPONENTS))
        for name in producer.COMPONENTS:
            with self.subTest(component=name):
                plan_path = self.base / 'plans' / (name + '.json')
                plan = load_json(plan_path)
                self.assertEqual(set(plan), {'format', 'manifest', 'contract', 'roots'})
                self.assertEqual(plan['format'], 'deep32-alpha2-sbom-plan/1')
                self.assertEqual(plan['manifest']['source_revision'], REVISION)
                self.assertEqual(plan['manifest']['source_date_epoch'], EPOCH)
                self.assertEqual(plan['manifest']['generator_sha256'], metadata.generator_sha256())
                for row in plan['roots']:
                    if row['path'] != '.':
                        public_path(row['path'])
                output = self.base / ('emitted-' + name)
                emitted = metadata.emit_component_sbom(
                    metadata.REPO, self.fixture.image(name), self.fixture.media(name),
                    self.fixture.stage(name), output, EPOCH, REVISION, inventory_path=plan_path)
                roles = {row['role'] for row in load_json(
                    self.fixture.stage(name) / 'payload/images/index.json')}
                self.assertEqual(set(emitted['selected_containers']), roles)
                self.assertEqual(emitted['component_coverage'], 'complete')
                versions = {(row['name'], row.get('versionInfo'))
                            for path in (output / 'metadata').glob('*.spdx.json')
                            for row in load_json(path)['packages']}
                self.assertTrue(set(VERSIONS.items()) <= versions)
        self.assertEqual(members(self.fixture.work), before)
        self.assert_public_tree(self.base / 'plans')

    def test_frozen_inventory_keeps_native_debian_name_and_receipt(self):
        self.fixture.probe['debian'].insert(0, 'g++\t4:12.2.0-3\tamd64')
        self.inventories_for()
        before = members(self.inventories)
        self.plans()
        self.assertEqual(members(self.inventories), before)

        plans = self.base / 'plans'
        plan = load_json(plans / 'core.json')
        installed = plans / 'inventories/core'
        frozen = {row['id']: row for row in plan['contract']['components'] if row['name'] == 'g++'}
        self.assertTrue(frozen)
        for container in (item for item in plan['manifest']['subjects'] if item['kind'] == 'container'):
            ref = container['image']['inventory']
            inventory = installed / ref['path']
            package = next(row for row in load_json(inventory)['components'] if row['name'] == 'g++')
            receipt = installed / package['files'][0]['path']
            self.assertIn('g++', receipt.name)
            self.assertEqual(frozen[package['id']]['version'], '4:12.2.0-3')
            self.assertEqual(frozen[package['id']]['sha256'], digest(receipt))
            self.assertEqual(frozen[package['id']]['base_inventory'],
                             dict(path='installed/' + ref['path'], sha256=digest(inventory)))

        output = self.base / 'emitted-with-native-names'
        metadata.emit_component_sbom(metadata.REPO, self.fixture.image('core'),
                                     self.fixture.media('core'), self.fixture.stage('core'),
                                     output, EPOCH, REVISION, inventory_path=plans / 'core.json')
        versions = {(row['name'], row.get('versionInfo'))
                    for path in (output / 'metadata').glob('*.spdx.json')
                    for row in load_json(path)['packages']}
        self.assertIn(('g++', '4:12.2.0-3'), versions)

    def test_deterministic_outputs_across_distinct_operator_roots(self):
        self.fixture.probe = dependencies(application=True)
        self.inventories_for()
        self.plans()
        second = Fixture(self.base / 'different-operator-root')
        second.probe = dependencies(application=True)
        other_inventories = second.work / 'owner-inventories'
        self.inventories_for(second, other_inventories)
        other_plans = self.base / 'other-plans'
        owner.disc3_plans(second.work, other_plans, epoch=EPOCH, inventories=other_inventories)
        for first, other in [(self.inventories, other_inventories),
                             (self.base / 'plans', other_plans)]:
            self.assertEqual(members(first), members(other))
            self.assert_public_tree(first)
            self.assert_public_tree(other)
        probe = load_json(self.inventories / 'core/door.probe.json')
        self.assertEqual(probe['application'], self.fixture.probe['application'])

    def test_missing_saved_archive_refuses_before_running_external_command(self):
        (self.fixture.stage('core') / 'payload/images/door.tar').unlink()
        with self.assertRaises((ValueError, OSError)):
            self.inventories_for()
        self.assertEqual(self.fixture.commands, [])
        self.assertFalse(self.inventories.exists())

    def test_saved_archive_digest_mismatch_refuses(self):
        index = self.fixture.stage('core') / 'payload/images/index.json'
        rows = load_json(index)
        rows[0]['sha256'] = 'f' * 64
        write(index, rows)
        self.fixture.reseal('core')
        with self.assertRaises(ValueError):
            self.inventories_for()
        self.assertEqual(self.fixture.commands, [])
        self.assertFalse(self.inventories.exists())

    def test_absolute_public_archive_paths_refuse(self):
        index = self.fixture.stage('core') / 'payload/images/index.json'
        for value in ('/operator/door.tar', 'C:/operator/door.tar'):
            with self.subTest(path=value):
                rows = load_json(index)
                rows[0]['path'] = value
                write(index, rows)
                self.fixture.reseal('core')
                with self.assertRaises(ValueError):
                    self.inventories_for()
                self.assertFalse(self.inventories.exists())
        self.assertEqual(self.fixture.commands, [])

    def test_wrong_loaded_immutable_config_refuses_before_probe(self):
        self.fixture.wrong_inspection = True
        with self.assertRaises(ValueError):
            self.inventories_for()
        self.assertFalse(any(row[0][:2] == ['docker', 'run'] for row in self.fixture.commands))
        self.assertFalse(self.inventories.exists())

    def test_incomplete_probe_refuses_without_publishing_inventory(self):
        self.fixture.probe['debian'] = []
        with self.assertRaises(ValueError):
            self.inventories_for()
        self.assertFalse(self.inventories.exists())

    def test_native_only_fallback_keeps_debian_versions_and_passes_emitter(self):
        self.fixture.probe_mode = 'native'
        result = self.inventories_for()
        self.assertEqual(sum(row['components'] for row in result['components']),
                         len(self.fixture.rows) * 2)
        probe = load_json(self.inventories / 'core/door.probe.json')
        self.assertEqual(probe['python'], [])
        self.assertEqual(probe['application'], [])
        self.assertEqual(probe['debian'], self.fixture.probe['debian'])
        self.plans()
        output = self.base / 'emitted-native'
        emitted = metadata.emit_component_sbom(
            metadata.REPO, self.fixture.image('core'), self.fixture.media('core'),
            self.fixture.stage('core'), output, EPOCH, REVISION,
            inventory_path=self.base / 'plans/core.json')
        self.assertEqual(emitted['component_coverage'], 'complete')
        commands = [row[0] for row in self.fixture.commands if row[0][:2] == ['docker', 'run']]
        self.assertEqual(len(commands), len(self.fixture.rows) * 2)
        for command in commands:
            self.assertEqual(command[command.index('--network') + 1], 'none')
            self.assertIn('--read-only', command)
            self.assertIn('--pull=never', command)
            self.assertIn('--security-opt=no-new-privileges', command)

    def test_python3_fallback_retains_full_python_file_measurements(self):
        self.fixture.probe_mode = 'python3'
        self.inventories_for()
        probe = load_json(self.inventories / 'core/door.probe.json')
        self.assertEqual(probe, self.fixture.probe)
        self.plans()
        commands = [row[0] for row in self.fixture.commands if row[0][:2] == ['docker', 'run']]
        self.assertEqual(sum('/bin/sh' in command for command in commands), len(self.fixture.rows))

    def test_default_probe_and_fallback_failure_refuse_publication(self):
        self.fixture.probe_mode = 'failure'
        with self.assertRaises(subprocess.CalledProcessError):
            self.inventories_for()
        self.assertFalse(self.inventories.exists())
        commands = [row[0] for row in self.fixture.commands if row[0][:2] == ['docker', 'run']]
        self.assertEqual(len(commands), 2)
        self.assertIn('/bin/sh', commands[-1])

    def test_malformed_successful_json_never_falls_back(self):
        self.fixture.probe_mode = 'invalid-json'
        with self.assertRaises(ValueError):
            self.inventories_for()
        self.assertFalse(self.inventories.exists())
        commands = [row[0] for row in self.fixture.commands if row[0][:2] == ['docker', 'run']]
        self.assertEqual(len(commands), 1)
        self.assertNotIn('/bin/sh', commands[0])

    def test_tampered_or_missing_installed_inventory_refuses_plans(self):
        self.inventories_for()
        folder = self.inventories / 'core'
        targets = [folder / 'door.inventory.json', folder / 'door.probe.json',
                   next((folder / 'records').glob('*.json'))]
        for number, target in enumerate(targets):
            with self.subTest(asset=target.name):
                original = target.read_bytes()
                target.write_bytes(original + b'\n')
                with self.assertRaises(ValueError):
                    self.plans('tampered-' + str(number))
                self.assertFalse((self.base / ('tampered-' + str(number))).exists())
                target.write_bytes(original)
        missing = folder / 'door.inventory.json'
        missing.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.plans('missing')
        self.assertFalse((self.base / 'missing').exists())

    def test_frozen_image_mismatch_refuses_plans(self):
        self.inventories_for()
        self.fixture.image('core').write_bytes(b'Changed synthetic frozen image.\n')
        with self.assertRaises(ValueError):
            self.plans()
        self.assertFalse((self.base / 'plans').exists())

    def test_omitted_distribution_refuses_even_with_rewritten_seal_hashes(self):
        self.inventories_for()
        folder = self.inventories / 'core'
        path = folder / 'door.inventory.json'
        inventory = load_json(path)
        inventory['components'].pop()
        write(path, inventory)
        seal = load_json(folder / 'SEAL.json')
        row = next(item for item in seal['images'] if item['role'] == 'door')
        row['inventory_sha256'] = digest(path)
        row['components'] = len(inventory['components'])
        seal['files'] = [item for item in members(folder) if item['path'] != 'SEAL.json']
        write(folder / 'SEAL.json', seal)
        with self.assertRaisesRegex(ValueError, 'omits or changes a measured distribution'):
            self.plans()
        self.assertFalse((self.base / 'plans').exists())

    def test_wrong_inventory_config_refuses_even_with_rewritten_seal_hashes(self):
        self.inventories_for()
        folder = self.inventories / 'core'
        path = folder / 'door.inventory.json'
        inventory = load_json(path)
        inventory['config_digest'] = 'sha256:' + 'f' * 64
        write(path, inventory)
        seal = load_json(folder / 'SEAL.json')
        row = next(item for item in seal['images'] if item['role'] == 'door')
        row['inventory_sha256'] = digest(path)
        seal['files'] = [item for item in members(folder) if item['path'] != 'SEAL.json']
        write(folder / 'SEAL.json', seal)
        with self.assertRaises(ValueError):
            self.plans()
        self.assertFalse((self.base / 'plans').exists())

    def test_cli_refusal_does_not_echo_operator_paths(self):
        command = [sys.executable, '-I', '-B', str(TOOLS / 'sbom/owner_inputs.py'),
                   'disc3-plans', '--work', str(self.base / 'missing-operator-root'),
                   '--out', str(self.base / 'output'), '--epoch', str(EPOCH)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Owner SBOM input generation refused (', result.stderr)
        self.assertNotIn(str(self.base), result.stderr)
        self.assertNotIn(self.base.as_posix(), result.stderr)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
