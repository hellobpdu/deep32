"""Synthetic alpha.2 producer rebuild checks; no Docker or host execution."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import tarfile

BUILD = Path(__file__).resolve().parents[1] / 'build'
SPEC = importlib.util.spec_from_file_location('alpha2_rebuild_under_test', BUILD / 'alpha2_rebuild.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def archive(path, label):
    config = json.dumps({'os': 'linux', 'architecture': 'amd64', 'label': label}, sort_keys=True).encode('ascii')
    config_name = hashlib.sha256(config).hexdigest() + '.json'
    manifest = json.dumps([{'Config': config_name, 'Layers': [], 'RepoTags': []}]).encode('ascii')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, 'w') as tar:
        for name, data in ((config_name, config), ('manifest.json', manifest)):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return dict(mod.archive_binding(path), sha256=mod.digest(path))


def dependencies():
    return {'format': 'deep32-installed-dependencies/1',
            'python': [{'name': 'synthetic-only', 'version': '1',
                        'files': [{'path': 'synthetic.py', 'size': 3, 'sha256': 'a' * 64}]}],
            'debian': ['synthetic-package\t1\tamd64'],
            'application': [{'path': '/app/synthetic.py', 'size': 3, 'sha256': 'c' * 64}]}


class Fixture:
    def __init__(self, root):
        self.stage, self.kit, self.out = root / 'stage', root / 'kit', root / 'output'
        self.kit.mkdir()
        self.before = []
        self.available = {}
        for role in mod.BASE_ROLES:
            path = self.stage / 'payload/images' / (role + '.tar')
            row = dict(archive(path, 'before-' + role), role=role, path='images/' + role + '.tar')
            self.before.append(row)
            self.available[row['config_digest']] = row['config_digest']
        mod.write_json(self.stage / 'payload/images/index.json', self.before)
        mod.write_json(self.stage / 'pins.json', {'runtime': {r['role'] + '_image_id': r['config_digest'] for r in self.before}})
        mod.write_json(self.stage / 'PAYLOAD.json', mod.members(self.stage / 'payload'))
        self.base = archive(self.stage / 'base/runtime.tar', 'runtime-base')
        self.builder = archive(self.stage / 'builder/base.tar', 'builder-base')
        for row in (self.base, self.builder):
            self.available[row['config_digest']] = row['config_digest']
        self.new = {}
        for role in mod.BASE_ROLES:
            path = root / 'saved' / (role + '.tar')
            self.new[role] = (path, archive(path, 'after-' + role))
        self.commands = []

    def context(self, stage, kit, output, role='builder'):
        output.mkdir(parents=True)
        (output / 'Dockerfile').write_text('SYNTHETIC ' + role, encoding='ascii')

    def run(self, command, **kwargs):
        self.commands.append(command)
        if command[:3] == ['docker', 'image', 'inspect']:
            key = command[3]
            identity = self.available.get(key)
            if identity is None:
                raise subprocess.CalledProcessError(1, command)
            value = [{'Id': identity, 'Os': 'linux', 'Architecture': 'amd64'}]
            return SimpleNamespace(stdout=json.dumps(value))
        if command[:2] == ['docker', 'tag']:
            self.available[command[3]] = self.available[command[2]]
        elif command[:2] == ['docker', 'build']:
            tag = command[command.index('--tag') + 1]
            role = tag.split('/')[1].split(':')[0]
            self.available[tag] = self.new[role][1]['config_digest']
        elif command[:3] == ['docker', 'image', 'save']:
            role = command[-1].split('/')[1].split(':')[0]
            Path(command[command.index('--output') + 1]).write_bytes(self.new[role][0].read_bytes())
        elif command[:2] == ['docker', 'run']:
            return SimpleNamespace(stdout=json.dumps(dependencies()))
        return SimpleNamespace(stdout='')

    def patches(self):
        return (patch.object(mod, 'validate_source'),
                patch.object(mod.images, 'checked_base', return_value=self.base),
                patch.object(mod.builder_inputs, 'checked_inputs', return_value={'base': self.builder}),
                patch.object(mod.builder_inputs, 'context', side_effect=self.context),
                patch.object(mod, '_application_context', side_effect=self.context))

    def build_pending(self, case):
        patches = self.patches()
        for item in patches:
            item.start()
            case.addCleanup(item.stop)
        with case.assertRaisesRegex(ValueError, 'qualification/gates.json'):
            mod.rebuild(self.stage, self.kit, self.out, 1, self.run)
        return mod.read_json(self.out / 'BUILD.json')

    def gates(self, receipt):
        path = self.out / 'qualification/evidence/synthetic.json'
        path.parent.mkdir()
        path.write_text('{"synthetic":true}\n', encoding='ascii')
        evidence = {'pass': True, 'path': 'evidence/synthetic.json', 'sha256': mod.digest(path)}
        rows = {}
        for row in receipt['dependency_equality']:
            checks = ['abi', 'imports', 'runtime_compilation', 'retained_inputs_absent', 'native_dependency_files_equal']
            if row['role'] == 'reranker':
                checks.append('exact_search_equal')
            rows[row['role']] = {'before_archive_sha256': row['before_archive_sha256'],
                                 'after_archive_sha256': row['after_archive_sha256'],
                                 'checks': {name: dict(evidence) for name in checks}}
        document = {'format': mod.GATE_FORMAT, 'roles': rows}
        mod.write_json(self.out / 'qualification/gates.json', document)
        return document


class PlanTests(unittest.TestCase):
    def test_every_loaded_image_is_inspected(self):
        commands = mod.plan(Path('/stage'), Path('/kit'), Path('/out'), 1)
        for index, command in enumerate(commands):
            if command[:3] == ['docker', 'image', 'load']:
                self.assertEqual(commands[index + 1][:3], ['docker', 'image', 'inspect'])

    def test_all_changed_base_recipes_and_no_qdrant_or_tei_build(self):
        commands = mod.plan(Path('/sealed'), Path('/kit'), Path('/output'), 123)
        builds = [c for c in commands if c[:2] == ['docker', 'build']]
        self.assertEqual(len(builds), 4)
        self.assertEqual({c[c.index('--tag') + 1] for c in builds},
                         {'deep32-local/' + role + ':locked' for role in mod.BASE_ROLES})
        for command in builds:
            self.assertIn('--pull=false', command)
            self.assertEqual(command[command.index('--network') + 1], 'none')
            self.assertIn('SOURCE_DATE_EPOCH=123', command)

    def test_full_build_once_with_two_saves(self):
        commands = mod.plan(Path('/stage'), Path('/kit'), Path('/output'), 1, mod.BASE_ROLES + mod.FULL_ROLES)
        self.assertEqual(len([c for c in commands if c[:2] == ['docker', 'build']]), 5)
        saves = [c for c in commands if c[:3] == ['docker', 'image', 'save']]
        self.assertEqual(len(saves), 6)
        self.assertIn(['docker', 'tag', 'deep32-local/full:locked', 'deep32-local/model_bridge:locked'], commands)

    def test_probes_cannot_fetch_write_or_load_gpu(self):
        command = mod.probe_command('sha256:' + 'a' * 64)
        for token in ('--read-only', '--pull=never', '--cap-drop=ALL', '--security-opt=no-new-privileges'):
            self.assertIn(token, command)
        self.assertEqual(command[command.index('--network') + 1], 'none')
        self.assertNotIn('--gpus', command)
        self.assertNotIn('import torch', command[-1])

    def test_half_full_role_refuses(self):
        rows = [{'role': role} for role in mod.BASE_ROLES + ('full',)]
        with self.assertRaisesRegex(ValueError, 'present together'):
            mod.selected_roles(rows)


class DependencyTests(unittest.TestCase):
    def test_application_changes_are_recorded_separately(self):
        after = dependencies()
        after['application'][0]['sha256'] = 'd' * 64
        receipt = mod.dependency_equality(dependencies(), after)
        self.assertEqual(len(receipt['application_file_differences']), 1)
        self.assertEqual(receipt['application_file_differences'][0]['path'], '/app/synthetic.py')

    def test_full_files_equal(self):
        self.assertEqual(mod.dependency_equality(dependencies(), dependencies())['python_distribution_files'], 1)

    def test_same_version_changed_file_refuses(self):
        after = dependencies()
        after['python'][0]['files'][0]['sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'versions or files differ'):
            mod.dependency_equality(dependencies(), after)

    def test_debian_version_changed_refuses(self):
        after = dependencies()
        after['debian'] = ['synthetic-package\t2\tamd64']
        with self.assertRaises(ValueError):
            mod.dependency_equality(dependencies(), after)

    def test_duplicate_distributions_refuse(self):
        value = dependencies()
        value['python'].append(copy.deepcopy(value['python'][0]))
        with self.assertRaises(ValueError):
            mod.validate_dependencies(value)

    def test_missing_file_manifest_refuses(self):
        value = dependencies()
        value['python'][0]['files'] = []
        with self.assertRaises(ValueError):
            mod.validate_dependencies(value)


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = Fixture(Path(self.tmp.name))

    def test_missing_native_gates_retains_build_leaves_source_unchanged(self):
        before = mod.members(self.fixture.stage)
        receipt = self.fixture.build_pending(self)
        self.assertEqual(mod.members(self.fixture.stage), before)
        self.assertEqual(receipt['status'], 'qualification-required')
        self.assertFalse((self.fixture.out / 'MERGE.json').exists())
        self.assertEqual(receipt['roles'], list(mod.BASE_ROLES))

    def test_gate_complete_merges_and_reseals(self):
        receipt = self.fixture.build_pending(self)
        self.fixture.gates(receipt)
        merged = mod.qualify_and_merge(self.fixture.stage, self.fixture.kit, self.fixture.out)
        self.assertEqual(merged['status'], 'qualified-and-merged')
        self.assertEqual(mod.read_json(self.fixture.stage / 'PAYLOAD.json'), mod.members(self.fixture.stage / 'payload'))
        self.assertEqual(mod.read_json(self.fixture.stage / 'payload/images/index.json'),
                         sorted(receipt['images'], key=lambda row: row['role']))

    def test_changed_archive_refuses_before_stage_writes(self):
        receipt = self.fixture.build_pending(self)
        self.fixture.gates(receipt)
        before = mod.members(self.fixture.stage)
        path = self.fixture.out / 'images/reranker.tar'
        path.write_bytes(path.read_bytes() + b'x')
        with self.assertRaisesRegex(ValueError, 'archive differs'):
            mod.qualify_and_merge(self.fixture.stage, self.fixture.kit, self.fixture.out)
        self.assertEqual(before, mod.members(self.fixture.stage))

    def test_wrong_before_archive_gate_refuses(self):
        receipt = self.fixture.build_pending(self)
        gates = self.fixture.gates(receipt)
        gates['roles']['door']['before_archive_sha256'] = '0' * 64
        mod.write_json(self.fixture.out / 'qualification/gates.json', gates)
        with self.assertRaisesRegex(ValueError, 'archive binding'):
            mod.qualify_and_merge(self.fixture.stage, self.fixture.kit, self.fixture.out)

    def test_external_gates_and_evidence_are_retained(self):
        receipt = self.fixture.build_pending(self)
        gates = self.fixture.gates(receipt)
        external = Path(self.tmp.name) / 'external'
        (external / 'evidence').mkdir(parents=True)
        (external / 'evidence/synthetic.json').write_bytes(
            (self.fixture.out / 'qualification/evidence/synthetic.json').read_bytes())
        mod.write_json(external / 'gates.json', gates)
        merged = mod.qualify_and_merge(self.fixture.stage, self.fixture.kit, self.fixture.out,
                                      gates=external / 'gates.json')
        self.assertEqual(merged['owner_gates_sha256'], mod.digest(external / 'gates.json'))

    def test_malformed_external_gate_refuses(self):
        receipt = self.fixture.build_pending(self)
        external = Path(self.tmp.name) / 'malformed.json'
        mod.write_json(external, [])
        with self.assertRaisesRegex(ValueError, 'format differs'):
            mod.check_owner_gates(self.fixture.out, receipt, gates=external)

    def test_missing_search_receipt_refuses(self):
        receipt = self.fixture.build_pending(self)
        gates = self.fixture.gates(receipt)
        del gates['roles']['reranker']['checks']['exact_search_equal']
        mod.write_json(self.fixture.out / 'qualification/gates.json', gates)
        with self.assertRaisesRegex(ValueError, 'check set'):
            mod.qualify_and_merge(self.fixture.stage, self.fixture.kit, self.fixture.out)

    def test_changed_dependency_receipt_refuses(self):
        receipt = self.fixture.build_pending(self)
        self.fixture.gates(receipt)
        path = self.fixture.out / 'qualification/door-after.json'
        data = mod.read_json(path)
        data['debian'][0] = 'synthetic-changed\t1\tamd64'
        mod.write_json(path, data)
        with self.assertRaisesRegex(ValueError, 'dependency evidence changed'):
            mod.qualify_and_merge(self.fixture.stage, self.fixture.kit, self.fixture.out)

    def test_build_environment_selects_local_buildkit(self):
        self.fixture.build_pending(self)
        for command in self.fixture.commands:
            if command[:2] == ['docker', 'build']:
                self.assertEqual(command[command.index('--network') + 1], 'none')
                self.assertIn('--pull=false', command)


if __name__ == '__main__':
    unittest.main()
