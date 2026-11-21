"""Synthetic owner qualification checks; no Docker, network or host probes."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
BUILD = HERE.parent / 'build'
SPEC = importlib.util.spec_from_file_location('alpha2_qualify_under_test', BUILD / 'alpha2_qualify.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
FIXTURE_SPEC = importlib.util.spec_from_file_location('alpha2_qualify_fixture', HERE / 'test_alpha2_rebuild.py')
fixture_mod = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(fixture_mod)
rebuild = fixture_mod.mod

REVISION = 'e8a93f33f0b22108f8c2364f8484ce3422552fbc'
CHECKS = {'abi', 'imports', 'runtime_compilation', 'retained_inputs_absent',
          'native_dependency_files_equal'}


def pending(case, root, include_full=False):
    """Make a real archive-bound rebuild awaiting its external owner gates."""
    producer = root / 'producer'
    producer.mkdir()
    fixture = fixture_mod.Fixture(producer)
    if include_full:
        for role in rebuild.FULL_ROLES:
            path = fixture.stage / 'payload/images' / (role + '.tar')
            row = dict(fixture_mod.archive(path, 'before-' + role), role=role,
                       path='images/' + role + '.tar')
            fixture.before.append(row)
            fixture.available[row['config_digest']] = row['config_digest']
        path = producer / 'saved/full.tar'
        replacement = (path, fixture_mod.archive(path, 'after-full'))
        fixture.new.update({role: replacement for role in rebuild.FULL_ROLES})
        for _, row in fixture.new.values():
            fixture.available[row['config_digest']] = row['config_digest']
        rebuild.write_json(fixture.stage / 'payload/images/index.json', fixture.before)
    pins = rebuild.read_json(fixture.stage / 'pins.json')
    pins['models'] = {'reranker': {'id': 'jinaai/jina-reranker-v3.5', 'revision': REVISION}}
    pins['runtime'].update({row['role'] + '_image_id': row['config_digest'] for row in fixture.before})
    rebuild.write_json(fixture.stage / 'pins.json', pins)
    snapshot = (fixture.stage / 'payload/models/reranker/models--jinaai--jina-reranker-v3.5'
                / 'snapshots' / REVISION)
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text('{"synthetic":true}\n', encoding='ascii')
    rebuild.write_json(fixture.stage / 'PAYLOAD.json', rebuild.members(fixture.stage / 'payload'))
    with ExitStack() as stack:
        for item in fixture.patches():
            stack.enter_context(item)
        if include_full:
            stack.enter_context(patch.object(rebuild.full_images, 'checked_inputs'))
            stack.enter_context(patch.object(rebuild.full_images, 'context', side_effect=fixture.context))
            stack.enter_context(patch.object(rebuild, '_full_input_stage', return_value=fixture.stage))
        with case.assertRaisesRegex(ValueError, 'qualification/gates.json'):
            rebuild.rebuild(fixture.stage, fixture.kit, fixture.out, 1, fixture.run)
    return fixture, rebuild.read_json(fixture.out / 'BUILD.json')


class FakeRunner:
    """Recognize only the exact image-load, qualification and cleanup operations."""
    def __init__(self, fixture, fail=None, unmeasurable=None):
        self.commands = []
        self.kwargs = []
        self.fail = fail
        self.unmeasurable = unmeasurable
        self.identities = {}
        for row in fixture.before:
            self.identities[row['config_digest']] = ('before', row['role'])
        for role, (_, row) in fixture.new.items():
            self.identities[row['config_digest']] = ('after', role)
        self.containers = {}
        self.refs = []
        self.snapshot_mounts = []

    @staticmethod
    def mount_fields(command):
        rows = []
        for index, token in enumerate(command[:-1]):
            if token == '--mount':
                parts = command[index + 1].split(',')
                row = {key: value for key, value in
                       (part.split('=', 1) for part in parts if '=' in part)}
                row['readonly'] = 'readonly' in parts or row.get('ro') == 'true'
                rows.append(row)
        return rows

    def __call__(self, command, **kwargs):
        command = list(command)
        self.commands.append(command)
        self.kwargs.append(kwargs)
        if command[:3] == ['docker', 'image', 'load']:
            if command[3] != '--input' or not Path(command[4]).is_file():
                raise AssertionError('load must name its saved archive')
            return SimpleNamespace(stdout='Loaded image ID: synthetic\n')
        if command[:3] == ['docker', 'image', 'inspect']:
            identity = command[3]
            if identity not in self.identities:
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(stdout=json.dumps(
                [{'Id': identity, 'Os': 'linux', 'Architecture': 'amd64'}]))
        if command[:2] == ['docker', 'run']:
            images = [item for item in command if item in self.identities]
            if len(images) != 1:
                raise AssertionError('run must use one measured immutable image identity')
            identity = images[0]
            side, role = self.identities[identity]
            if '--detach' in command:
                container = format(len(self.containers) + 1, '064x')
                self.containers[container] = (side, role)
                for row in self.mount_fields(command):
                    source = row.get('source', row.get('src'))
                    target = row.get('target', row.get('dst', row.get('destination', '')))
                    if target.endswith('/refs'):
                        folder = Path(source)
                        self.refs.append({p.name: p.read_bytes() for p in folder.iterdir()})
                    elif '/snapshots/' in target:
                        self.snapshot_mounts.append((source, target, row['readonly']))
                return SimpleNamespace(stdout=container + '\n')
            return SimpleNamespace(stdout=json.dumps(self.probe(command, side, role)))
        if command[:2] == ['docker', 'exec']:
            containers = [item for item in command if item in self.containers]
            if len(containers) != 1:
                raise AssertionError('exec must name the measured detached container')
            side, role = self.containers[containers[0]]
            return SimpleNamespace(stdout=json.dumps(self.probe(command, side, role)))
        if command[:2] in (['docker', 'stop'], ['docker', 'rm']):
            if command[-1] not in self.containers:
                raise AssertionError('cleanup must name the measured detached container')
            return SimpleNamespace(stdout='')
        raise AssertionError('unexpected external command: ' + repr(command))

    def probe(self, command, side, role):
        code = command[command.index('-c') + 1]
        target = side == 'after' and role == 'door'
        if self.unmeasurable == 'abi' and target and code == mod.ABI_PROBE:
            raise subprocess.CalledProcessError(1, command, stderr='synthetic probe unavailable')
        if self.unmeasurable == 'abi_timeout' and target and code == mod.ABI_PROBE:
            raise subprocess.TimeoutExpired(command, 1, output=b'synthetic partial output',
                                            stderr=b'synthetic timeout diagnostic')
        if code == mod.ABI_PROBE:
            value = {'version': [3, 12, 0], 'cache_tag': 'cpython-312',
                     'soabi': 'cpython-312-x86_64-linux-gnu',
                     'ext_suffix': '.cpython-312-x86_64-linux-gnu.so',
                     'libc': ['glibc', '2.36'], 'machine': 'x86_64', 'shared_objects': []}
            if target and self.fail == 'abi':
                value['version'] = [3, 12, 1]
            return value
        if code == mod.IMPORT_NAMES_PROBE:
            return ['synthetic']
        if code == mod.IMPORTS_PROBE:
            outcome = 'ModuleNotFoundError' if target and self.fail == 'imports' else 'ok'
            if target and self.fail == 'runtime_writes' and '--tmpfs' not in command:
                outcome = 'PermissionError'
            return {'outcomes': {'synthetic': outcome}, 'children': {}}
        if code == mod.COMPILER_PROBE:
            return ['gcc', 'ld'] if target and self.fail == 'runtime_compilation' else ['ld']
        if code == mod.RETAINED_PROBE:
            if target and self.fail == 'retained_inputs_absent':
                return [{'path': '/tmp/new.whl', 'sha256': 'f' * 64, 'reasons': ['wheel']}]
            return []
        if code == mod.HEALTH_PROBE:
            if self.unmeasurable == 'health':
                raise subprocess.CalledProcessError(1, command, stderr='synthetic health unavailable')
            return {'status': 'ok'}
        if code == mod.SEARCH_PROBE:
            if self.unmeasurable == 'search':
                raise subprocess.CalledProcessError(1, command, stderr='synthetic search unavailable')
            values = [{'model': 'jina-reranker-v3.5',
                       'results': [{'index': i, 'relevance_score': 1.0 / (i + 1),
                                    'document': mod.DOCUMENTS[i]} for i in range(5)]}
                      for _ in range(12)]
            if side == 'after' and self.fail == 'exact_search_equal':
                values[0]['results'][0]['relevance_score'] = 0.5
            return values
        raise AssertionError('unrecognized Python probe')


class QualifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture, self.receipt = pending(self, self.root)
        self.owner = self.root / 'owner'

    def qualify(self, runner, **kwargs):
        return mod.qualify(self.fixture.stage, self.fixture.out, self.owner, runner, **kwargs)

    def evidence(self, role, check):
        return rebuild.read_json(self.owner / 'evidence' / (role + '-' + check + '.json'))

    def assert_failed(self, result, check):
        self.assertFalse(result['pass'])
        self.assertTrue(any(check in reason for reason in result['failures']), result)
        self.assertFalse((self.owner / 'ALPHA2-REBUILD-GATES.json').exists())
        self.assertTrue(any(not self.evidence(role, check)['result']
                            for role in self.receipt['roles']
                            if (self.owner / 'evidence' / (role + '-' + check + '.json')).exists()))

    def test_all_pass_is_accepted_and_preserves_producer_inputs(self):
        runner = FakeRunner(self.fixture)
        stage_before = rebuild.members(self.fixture.stage)
        output_before = rebuild.members(self.fixture.out)
        result = self.qualify(runner)
        self.assertTrue(result['pass'], result)
        self.assertEqual(result['failures'], [])
        gates = self.owner / 'ALPHA2-REBUILD-GATES.json'
        self.assertEqual(Path(result['gates']), gates)
        self.assertEqual(rebuild.members(self.fixture.stage), stage_before)
        self.assertEqual(rebuild.members(self.fixture.out), output_before)
        document = rebuild.read_json(gates)
        self.assertEqual(document['format'], rebuild.GATE_FORMAT)
        comparisons = {row['role']: row for row in self.receipt['dependency_equality']}
        for role, entry in document['roles'].items():
            expected = CHECKS | ({'exact_search_equal'} if role == 'reranker' else set())
            self.assertEqual(set(entry['checks']), expected)
            for side in ('before', 'after'):
                self.assertEqual(entry[side + '_archive_sha256'], comparisons[role][side + '_archive_sha256'])
            for check, reference in entry['checks'].items():
                path = self.owner / reference['path']
                self.assertEqual(path, self.owner / 'evidence' / (role + '-' + check + '.json'))
                self.assertEqual(reference['sha256'], rebuild.digest(path))
                self.assertIs(reference['pass'], True)
                evidence = self.evidence(role, check)
                self.assertIs(evidence['result'], True)
                self.assertEqual(evidence['role'], role)
                self.assertEqual(evidence['check'], check)
                self.assertEqual(evidence['before_archive_sha256'], entry['before_archive_sha256'])
                self.assertEqual(evidence['after_archive_sha256'], entry['after_archive_sha256'])
                self.assertEqual(evidence['image_identities'], {
                    'before': next(row['config_digest'] for row in self.fixture.before if row['role'] == role),
                    'after': self.fixture.new[role][1]['config_digest']})
                self.assertIn('before', evidence['measured'])
                self.assertIn('after', evidence['measured'])
                self.assertTrue(evidence['commands'])
                for command in evidence['commands']:
                    self.assertIn(command['argv'], runner.commands)
                    self.assertIn('stdout', command)
                    self.assertIn('stderr', command)
                if check == 'native_dependency_files_equal':
                    for side in ('before', 'after'):
                        manifest = self.fixture.out / 'qualification' / (role + '-' + side + '.json')
                        self.assertEqual(evidence['measured'][side]['manifest_sha256'], rebuild.digest(manifest))
        self.assertEqual(rebuild.check_owner_gates(self.fixture.out, self.receipt, gates=gates),
                         rebuild.digest(gates))

    def test_each_failed_gate_has_nonzero_cli_and_keeps_evidence(self):
        for check in sorted(CHECKS | {'exact_search_equal'}):
            with self.subTest(check=check), tempfile.TemporaryDirectory() as name:
                fixture, receipt = pending(self, Path(name))
                owner = Path(name) / 'owner'
                runner = FakeRunner(fixture, fail=check)
                if check == 'native_dependency_files_equal':
                    path = fixture.out / 'qualification/door-after.json'
                    value = rebuild.read_json(path)
                    value['python'][0]['version'] = '2'
                    rebuild.write_json(path, value)
                    comparison = next(row for row in receipt['dependency_equality'] if row['role'] == 'door')
                    comparison['after_manifest_sha256'] = rebuild.digest(path)
                    rebuild.write_json(fixture.out / 'BUILD.json', receipt)
                stage_before = rebuild.members(fixture.stage)
                output_before = rebuild.members(fixture.out)
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(output):
                    status = mod.main(['--stage', str(fixture.stage), '--rebuild', str(fixture.out),
                                       '--owner-dir', str(owner)], runner)
                self.assertNotEqual(status, 0)
                self.assertIn('Qualification failed: ', output.getvalue())
                self.assertIn(check, output.getvalue())
                self.assertFalse((owner / 'ALPHA2-REBUILD-GATES.json').exists())
                role = 'reranker' if check == 'exact_search_equal' else 'door'
                failed = rebuild.read_json(owner / 'evidence' / (role + '-' + check + '.json'))
                self.assertIs(failed['result'], False)
                self.assertEqual(len(list((owner / 'evidence').glob('*.json'))),
                                 len(receipt['roles']) * len(CHECKS) + 1)
                self.assertEqual(rebuild.members(fixture.stage), stage_before)
                self.assertEqual(rebuild.members(fixture.out), output_before)

    def test_command_shapes_and_isolated_import_modes(self):
        runner = FakeRunner(self.fixture)
        # One worker keeps the global command order this sequence check reads; other tests run roles concurrently.
        self.assertTrue(self.qualify(runner, workers=1)['pass'])
        for command, kwargs in zip(runner.commands, runner.kwargs):
            self.assertIs(kwargs['check'], True)
            self.assertIs(kwargs['capture_output'], True)
            self.assertIs(kwargs['text'], True)
            self.assertEqual(kwargs['env']['LC_ALL'], 'C')
            self.assertEqual(kwargs['env']['TZ'], 'UTC')
            if command[:2] != ['docker', 'run']:
                continue
            for flag in ('--rm', '--pull=never', '--cap-drop=ALL',
                         '--security-opt=no-new-privileges', '--pids-limit=256'):
                self.assertIn(flag, command)
            self.assertEqual(command[command.index('--network') + 1], 'none')
            self.assertNotIn('--gpus', command)
            if '--detach' not in command:
                self.assertEqual(command[command.index('--entrypoint') + 1], 'python')
        imports = [command for command in runner.commands if '-c' in command
                   and command[command.index('-c') + 1] == mod.IMPORTS_PROBE]
        self.assertEqual(len(imports), len(self.receipt['roles']) * 4)
        for command in imports:
            self.assertIn('--read-only', command)
            self.assertIn('-I', command)
            self.assertIn('-B', command)
            if '--tmpfs' in command:
                self.assertTrue(command[command.index('--tmpfs') + 1].split(':')[0] == '/tmp')
                self.assertNotIn('--mount', command)
            else:
                for writable in ('--mount', '-v', '--volume'):
                    self.assertNotIn(writable, command)
                self.assertIn('HOME=/nonexistent', command)
                self.assertIn('PYTHONDONTWRITEBYTECODE=1', command)
        loads = [i for i, command in enumerate(runner.commands)
                 if command[:3] == ['docker', 'image', 'load']]
        self.assertEqual(len(loads), len(self.receipt['roles']) * 2)
        for index in loads:
            self.assertEqual(runner.commands[index + 1][:3], ['docker', 'image', 'inspect'])

    def test_search_uses_only_reranker_with_cpu_and_readonly_model_mounts(self):
        runner = FakeRunner(self.fixture)
        self.assertTrue(self.qualify(runner)['pass'])
        detached = [c for c in runner.commands if '--detach' in c]
        self.assertEqual(len(detached), 2)
        for command in detached:
            self.assertEqual([runner.identities[t][1] for t in command if t in runner.identities], ['reranker'])
            self.assertEqual(command[command.index('--entrypoint') + 1], 'python')
            for value in ('RERANKER_DEVICE=cpu', 'RERANKER_DTYPE=float32', 'OMP_NUM_THREADS=1',
                          'MKL_NUM_THREADS=1', 'HF_HOME=/models', 'HF_HUB_OFFLINE=1',
                          'TRANSFORMERS_OFFLINE=1'):
                self.assertIn(value, command)
            for mount in runner.mount_fields(command):
                self.assertTrue(mount['readonly'])
        self.assertEqual(runner.refs, [{'main': REVISION.encode('ascii'), REVISION: REVISION.encode('ascii')}] * 2)
        self.assertEqual(len(runner.snapshot_mounts), 2)
        for source, target, readonly in runner.snapshot_mounts:
            self.assertEqual(Path(source), self.fixture.stage / 'payload/models/reranker'
                             / 'models--jinaai--jina-reranker-v3.5/snapshots' / REVISION)
            self.assertEqual(target, '/models/hub/models--jinaai--jina-reranker-v3.5/snapshots/' + REVISION)
            self.assertTrue(readonly)
        for container in runner.containers:
            self.assertEqual(len([c for c in runner.commands if c[:2] == ['docker', 'stop'] and c[-1] == container]), 1)
            self.assertEqual(len([c for c in runner.commands if c[:2] == ['docker', 'rm'] and c[-1] == container]), 1)
        searches = [c for c in runner.commands if '-c' in c and c[c.index('-c') + 1] == mod.SEARCH_PROBE]
        self.assertEqual(len(searches), 2)
        for command in searches:
            offset = command.index('-c')
            self.assertEqual(json.loads(command[offset + 2]), list(mod.QUERIES))
            self.assertEqual(json.loads(command[offset + 3]), list(mod.DOCUMENTS))
            self.assertEqual(len(mod.QUERIES), 12)
            self.assertEqual(len(mod.DOCUMENTS), 5)

    def test_readonly_import_write_attempt_fails_runtime_compilation(self):
        result = self.qualify(FakeRunner(self.fixture, fail='runtime_writes'))
        self.assert_failed(result, 'runtime_compilation')

    def test_unmeasurable_probe_retains_failed_evidence_and_continues(self):
        result = self.qualify(FakeRunner(self.fixture, unmeasurable='abi'))
        self.assert_failed(result, 'abi')
        self.assertTrue((self.owner / 'evidence/reranker-exact_search_equal.json').is_file())

    def test_timeout_byte_output_is_retained_as_json_text(self):
        result = self.qualify(FakeRunner(self.fixture, unmeasurable='abi_timeout'))
        self.assert_failed(result, 'abi')
        evidence = self.evidence('door', 'abi')
        timeout = next(row for row in evidence['commands'] if row.get('error') == 'TimeoutExpired')
        self.assertEqual(timeout['stdout'], 'synthetic partial output')
        self.assertEqual(timeout['stderr'], 'synthetic timeout diagnostic')

    def test_identical_import_timeouts_are_unmeasurable(self):
        class TimeoutImports(FakeRunner):
            def probe(inner, command, side, role):
                if command[command.index('-c') + 1] == mod.IMPORTS_PROBE:
                    return {'outcomes': {'synthetic': 'TimeoutExpired'},
                            'children': {'synthetic': {'timeout_seconds': 120}}}
                return super().probe(command, side, role)
        result = self.qualify(TimeoutImports(self.fixture))
        self.assert_failed(result, 'imports')
        evidence = self.evidence('door', 'imports')
        for side in ('before', 'after'):
            self.assertEqual(evidence['measured'][side]['outcomes']['synthetic'], 'TimeoutExpired')

    def test_optional_full_roles_use_build_roles_without_additional_search(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture, receipt = pending(self, root, include_full=True)
            runner = FakeRunner(fixture)
            owner = root / 'owner'
            stage_before = rebuild.members(fixture.stage)
            output_before = rebuild.members(fixture.out)
            result = mod.qualify(fixture.stage, fixture.out, owner, runner)
            self.assertTrue(result['pass'], result)
            document = rebuild.read_json(owner / 'ALPHA2-REBUILD-GATES.json')
            self.assertEqual(set(document['roles']), set(rebuild.BASE_ROLES + rebuild.FULL_ROLES))
            self.assertEqual(set(document['roles']['full']['checks']), CHECKS)
            self.assertEqual(set(document['roles']['model_bridge']['checks']), CHECKS)
            self.assertEqual(len([c for c in runner.commands if '--detach' in c]), 2)
            self.assertEqual(rebuild.members(fixture.stage), stage_before)
            self.assertEqual(rebuild.members(fixture.out), output_before)
            self.assertEqual(rebuild.check_owner_gates(fixture.out, receipt, gates=Path(result['gates'])),
                             rebuild.digest(Path(result['gates'])))

    def test_search_failure_stops_and_removes_every_started_container(self):
        runner = FakeRunner(self.fixture, unmeasurable='search')
        result = self.qualify(runner)
        self.assert_failed(result, 'exact_search_equal')
        self.assertTrue(runner.containers)
        for container in runner.containers:
            self.assertTrue(any(c[:2] == ['docker', 'stop'] and c[-1] == container for c in runner.commands))
            self.assertTrue(any(c[:2] == ['docker', 'rm'] and c[-1] == container for c in runner.commands))

    def test_health_timeout_is_bounded_and_cleans_up(self):
        runner = FakeRunner(self.fixture, unmeasurable='health')
        clock = iter(range(0, 10000, 901))
        result = self.qualify(runner, sleep=lambda _: None, monotonic=lambda: next(clock))
        self.assert_failed(result, 'exact_search_equal')
        for container in runner.containers:
            self.assertTrue(any(c[:2] == ['docker', 'stop'] and c[-1] == container for c in runner.commands))
            self.assertTrue(any(c[:2] == ['docker', 'rm'] and c[-1] == container for c in runner.commands))

    def test_malformed_measurements_fail_closed(self):
        values = {
            'abi': (mod.ABI_PROBE, {'version': [3, 12, 0]}),
            'imports': (mod.IMPORTS_PROBE, {'outcomes': {}, 'children': {}}),
            'runtime_compilation': (mod.COMPILER_PROBE, ['ld', 'cc']),
            'retained_inputs_absent': (mod.RETAINED_PROBE, [{}]),
            'exact_search_equal': (mod.SEARCH_PROBE, [{}] * 11),
        }
        for check, (probe, malformed) in values.items():
            with self.subTest(check=check):
                class MalformedRunner(FakeRunner):
                    def probe(inner, command, side, role):
                        code = command[command.index('-c') + 1]
                        if side == 'after' and role == ('reranker' if check == 'exact_search_equal' else 'door') and code == probe:
                            return malformed
                        return super().probe(command, side, role)
                self.owner = self.root / ('owner-' + check)
                self.assert_failed(self.qualify(MalformedRunner(self.fixture)), check)

    def test_existing_retained_inputs_are_allowed_but_changed_bytes_fail(self):
        row = {'path': '/tmp/existing.whl', 'sha256': 'e' * 64, 'reasons': ['wheel']}
        for changed in (False, True):
            with self.subTest(changed=changed):
                class RetainedRunner(FakeRunner):
                    def probe(inner, command, side, role):
                        if command[command.index('-c') + 1] == mod.RETAINED_PROBE:
                            current = dict(row)
                            if changed and side == 'after' and role == 'door':
                                current['sha256'] = 'f' * 64
                            return [current]
                        return super().probe(command, side, role)
                self.owner = self.root / ('owner-retained-' + str(changed))
                result = self.qualify(RetainedRunner(self.fixture))
                if changed:
                    self.assert_failed(result, 'retained_inputs_absent')
                else:
                    self.assertTrue(result['pass'], result)

    def test_owner_directory_must_be_new_and_outside_producer(self):
        existing = self.root / 'existing-owner'
        existing.mkdir()
        for owner in (existing, self.fixture.stage.parent / 'owner'):
            with self.subTest(owner=str(owner)):
                runner = FakeRunner(self.fixture)
                with self.assertRaisesRegex(ValueError, 'owner'):
                    mod.qualify(self.fixture.stage, self.fixture.out, owner, runner)
                self.assertFalse(runner.commands)
                self.assertFalse((owner / 'ALPHA2-REBUILD-GATES.json').exists())

    def test_embedded_probes_are_valid_python(self):
        for name in ('ABI_PROBE', 'IMPORT_NAMES_PROBE', 'IMPORTS_PROBE', 'COMPILER_PROBE',
                     'RETAINED_PROBE', 'HEALTH_PROBE', 'SEARCH_PROBE'):
            with self.subTest(probe=name):
                compile(getattr(mod, name), '<' + name + '>', 'exec')


if __name__ == '__main__':
    unittest.main()
