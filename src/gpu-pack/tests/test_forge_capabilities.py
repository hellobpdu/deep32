#!/usr/bin/env python3
"""Offline capability preflight fixtures; no host tools, network or models."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
KIT = Path(__file__).resolve().parents[1]
SCRATCH = None


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


jobs = module('capability_jobs', KIT / 'forms/forge_jobs.py')
host = module('capability_host', KIT / 'forms/host-requirements.py')
CORE = {'TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE'}
INGESTION = {'CONVERTER_IMAGE', 'BUILDER_IMAGE', 'QDRANT_IMAGE'}


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-capability-', dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'install'
        jobs.common.private_directory(self.root)
        self.calls = []
        self.data = dict(install_id='1' * 16, release='2' * 64,
                         stage='local-ready-publication-required', settings={})
        self.life = jobs.forge.Lifecycle(self.root, KIT, run=self.command, emit=lambda value: None)
        self.subject = jobs.Jobs(self.life)
        for patcher in (patch.object(self.life, 'state', return_value=self.data),
                        patch.object(subprocess, 'run', side_effect=AssertionError('real process forbidden'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def command(self, argv, *args, **kwargs):
        self.calls.append(list(argv))
        return ''

    def inputs(self, roles=CORE):
        return self.root / 'retained-payload', {'builder': True}, {
            key: 'sha256:' + 'a' * 64 for key in roles}

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def test_core_conversion_refuses_before_model_stop_or_any_job_write(self):
        job = self.root / 'jobs/synthetic'
        job.mkdir(parents=True)
        (job / 'input.pdf').write_bytes(b'SYNTHETIC TEST INPUT ONLY\n')
        before = self.snapshot()
        for platform in ('linux', 'windows'):
            with self.subTest(platform=platform), \
                    patch.object(self.life, 'runtime_inputs', return_value=self.inputs(), create=True), \
                    patch.object(jobs.forge, 'prepare_config') as prepare:
                self.life.platform = platform
                with self.assertRaisesRegex(jobs.common.Refusal, 'ingestion add-on'):
                    self.subject.convert(['synthetic', 'pymupdf'])
                prepare.assert_not_called()
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(self.calls)
                self.assertFalse((self.root / '.lifecycle-lock').exists())

    def test_build_and_bundle_require_both_verified_builder_and_qdrant(self):
        for verb in ('build', 'bundle'):
            for roles in (CORE, CORE | {'BUILDER_IMAGE'}, CORE | {'QDRANT_IMAGE'}):
                with self.subTest(verb=verb, roles=sorted(roles)), \
                        patch.object(self.life, 'runtime_inputs', return_value=self.inputs(roles), create=True):
                    before = self.snapshot()
                    with self.assertRaisesRegex(jobs.common.Refusal, 'ingestion add-on'):
                        self.subject.builder(verb, ['--synthetic'])
                    self.assertEqual(self.snapshot(), before)
                    self.assertFalse(self.calls)

    def test_runtime_uses_retained_inputs_and_compose_without_media(self):
        release = self.root / 'releases' / self.data['release']
        release.mkdir(parents=True)
        jobs.common.atomic_json(release / 'compose.json', {'synthetic': True})
        with patch.object(self.life, 'runtime_inputs', return_value=self.inputs(CORE | INGESTION), create=True) as inputs, \
                patch.object(jobs.forge, 'prepare_config', return_value={'synthetic': True}):
            self.assertEqual(self.subject.runtime(required_roles=('CONVERTER_IMAGE',)),
                             (self.data, {'builder': True}))
            inputs.assert_called_once_with(self.data, allow_load=False)
        self.assertFalse((release / 'media.json').exists())
        self.assertEqual(self.calls[0][-2:], ['config', '--quiet'])

    def test_invalid_installed_input_never_falls_back_to_external_media(self):
        for failure in (ValueError('synthetic changed model'), jobs.common.Refusal('synthetic missing image')):
            with self.subTest(failure=str(failure)), \
                    patch.object(self.life, 'runtime_inputs', side_effect=failure, create=True), \
                    patch.object(jobs.common, 'module') as modules:
                with self.assertRaises(type(failure)):
                    self.subject.runtime()
                modules.assert_not_called()
                self.assertFalse(self.calls)

    def test_recovery_can_restore_core_models_without_requiring_converter_role(self):
        release = self.root / 'releases' / self.data['release']
        release.mkdir(parents=True)
        jobs.common.atomic_json(release / 'compose.json', {'synthetic': True})
        jobs.common.atomic_json(self.subject.record, dict(schema=jobs.RECOVERY_SCHEMA,
            install_id=self.data['install_id'], job='synthetic', services=['embeddings']))
        with patch.object(self.life, 'runtime_inputs', return_value=self.inputs(), create=True) as inputs, \
                patch.object(jobs.forge, 'prepare_config', return_value={'synthetic': True}):
            self.subject.recover([])
            inputs.assert_called_once_with(self.data, allow_load=True)
        self.assertFalse(self.subject.record.exists())
        self.assertTrue(any(row[-1:] == ['embeddings'] and 'up' in row for row in self.calls))

    def test_host_adapter_keeps_selected_roles_and_original_inventory_on_both_platforms(self):
        images = self.inputs()[2]
        inventory = self.root / 'original-PAYLOAD.json'
        self.life.docker_storage_path = self.root / 'desktop-backing'
        status = json.dumps(dict(BackendState='Running', Self=dict(DNSName='synthetic-forge.example.ts.net.')))
        for platform in ('linux', 'windows'):
            check = Mock(return_value={'free_bytes_required': host.GIB})
            with self.subTest(platform=platform), \
                    patch.object(jobs.common, 'module', return_value=SimpleNamespace(check=check)), \
                    patch.object(self.life, 'run', return_value=status):
                self.assertEqual(self.life.host(self.root / 'retained-payload', platform,
                    selected_roles=images, installed_inventory=inventory), 'synthetic-forge.example.ts.net')
            options = check.call_args.kwargs
            self.assertEqual(options['selected_roles'], images)
            self.assertEqual(options['installed_inventory'], inventory)
            if platform == 'windows':
                self.assertEqual(options['host_platform'], 'windows')
                self.assertEqual(options['docker_storage_path'], self.life.docker_storage_path)


class DriverAndStorageTests(unittest.TestCase):
    def setUp(self):
        self.runtime = dict(mineru_minimum_driver='590.0', tei_minimum_driver='550.0',
                            cuda_minimum_driver='560.0', windows_cuda13_driver_build='580.0')

    def test_core_requires_search_and_shared_cuda_floors_only(self):
        self.assertEqual(host.minimum_driver(self.runtime, CORE), '560.0')
        self.assertEqual(host.minimum_driver(self.runtime, CORE, windows=True), '580.0')
        self.runtime.pop('mineru_minimum_driver')
        self.assertEqual(host.minimum_driver(self.runtime, CORE), '560.0')
        with self.assertRaises(ValueError):
            host.minimum_driver(self.runtime)

    def test_ingestion_and_legacy_selection_keep_every_driver_floor(self):
        for roles in (None, CORE | INGESTION, CORE | {'CONVERTER_IMAGE'}):
            self.assertEqual(host.minimum_driver(self.runtime, roles), '590.0')
            self.assertEqual(host.minimum_driver(self.runtime, roles, windows=True), '590.0')

    def test_invalid_or_partial_selection_cannot_lower_driver_gate(self):
        for roles in ((), [], 'TEI_IMAGE', 3, [None], [[]], CORE | {'FULL_IMAGE'},
                      CORE | {'BUILDER_IMAGE'}, CORE | {'CONVERTER_IMAGE', 'QDRANT_IMAGE'}):
            with self.subTest(roles=repr(roles)), self.assertRaises(ValueError):
                host.minimum_driver(self.runtime, roles)

    def test_core_unresolved_search_or_windows_floor_still_refuses(self):
        for name in ('tei_minimum_driver', 'cuda_minimum_driver', 'windows_cuda13_driver_build'):
            with self.subTest(name=name):
                changed = dict(self.runtime, **{name: 'TO-PIN'})
                with self.assertRaises(ValueError):
                    host.minimum_driver(changed, CORE, windows=True)

    def test_host_check_applies_selected_core_floor_on_both_platforms(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-driver-', dir=SCRATCH) as temporary:
            root = Path(temporary)
            payload, kit, backing = root / 'payload', root / 'kit', root / 'desktop-backing'
            for path in (payload, kit, backing):
                path.mkdir()
            (payload / 'synthetic-model').write_bytes(b'SYNTHETIC TEST MODEL ONLY\n')
            runtime = dict(self.runtime, docker_minimum_version='25.0.0', compose_minimum_version='2.30.0')
            (kit / 'pins.json').write_text(json.dumps({'runtime': runtime}), encoding='ascii')
            for platform, driver in (('linux', '560.0'), ('windows', '580.0')):
                calls = []
                def command(argv, **kwargs):
                    calls.append(argv)
                    if '--query-gpu=driver_version' in argv:
                        value = driver
                    elif 'info' in argv:
                        value = json.dumps(dict(OSType='linux', Runtimes={'nvidia': {}},
                            DockerRootDir='/var/lib/docker', OperatingSystem='Docker Desktop',
                            KernelVersion='6.6-microsoft-standard-WSL2'))
                    else:
                        value = '2.30.0' if 'compose' in argv else '25.0.0'
                    return subprocess.CompletedProcess(argv, 0, value, '')
                options = dict(root=root, run=command, transport='loopback',
                    host_platform=platform, docker_storage_path=backing, check_loopback_ports=False,
                    disk_usage=lambda path: SimpleNamespace(free=10**15))
                with self.subTest(platform=platform), \
                        patch.object(subprocess, 'run', side_effect=AssertionError('real process forbidden')):
                    self.assertEqual(host.check('docker', payload, kit, selected_roles=CORE,
                                                **options)['minimum_driver'], driver)
                    with self.assertRaisesRegex(ValueError, '590.0 or later'):
                        host.check('docker', payload, kit, **options)
                self.assertFalse(any('load' in row or 'pull' in row or 'run' in row for row in calls))

    def test_retained_inventory_preserves_original_conservative_storage_gate(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-budget-', dir=SCRATCH) as temporary:
            root = Path(temporary)
            payload = root / 'retained-payload'
            payload.mkdir()
            (payload / 'small-runtime').write_bytes(b'synthetic runtime')
            inventory = root / 'PAYLOAD.json'
            rows = [dict(path='images/synthetic.tar', size=8192, sha256='a' * 64),
                    dict(path='models/synthetic.bin', size=4096, sha256='b' * 64)]
            inventory.write_text(json.dumps(rows), encoding='ascii')
            expected = 8192 + 4096 + 2 * 8192 + host.GIB
            self.assertEqual(host.check_storage(payload, root, root,
                disk_usage=lambda path: SimpleNamespace(free=expected), installed_inventory=inventory), expected)
            with self.assertRaises(ValueError):
                host.check_storage(payload, root, root,
                    disk_usage=lambda path: SimpleNamespace(free=expected - 1), installed_inventory=inventory)
            for malformed in ([], rows + [rows[0]], [dict(rows[0], size=True)],
                              [dict(rows[0], path='../elsewhere')], [dict(rows[0], sha256='invalid')]):
                inventory.write_text(json.dumps(malformed), encoding='ascii')
                with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                    host.check_storage(payload, root, root,
                        disk_usage=lambda path: SimpleNamespace(free=10**15), installed_inventory=inventory)


class ComposeSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-compose-', dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def images(self, roles):
        return {role: 'sha256:' + 'a' * 64 for role in roles}

    def render(self, roles):
        config = dict(models={'tei': {'revision': 'b' * 40}},
                      runtime={'reranker_dtype': 'float16'}, builder={'synthetic': True})
        return jobs.forge.prepare_config(self.root / 'kit', self.root / 'payload', self.root / 'data',
                                         self.images(roles), config)

    def test_exact_core_or_complete_ingestion_roles_and_explicit_legacy_path(self):
        for roles in (CORE, CORE | INGESTION):
            self.assertEqual(jobs.forge.capability_roles(self.images(roles)), roles)
        old = CORE | {'CONVERTER_IMAGE'}
        with self.assertRaises(ValueError):
            jobs.forge.capability_roles(self.images(old))
        self.assertEqual(jobs.forge.capability_roles(self.images(old), legacy=True), old)
        for roles in (set(), CORE - {'DOOR_IMAGE'}, CORE | {'BUILDER_IMAGE'},
                      CORE | {'QDRANT_IMAGE'}, CORE | {'FULL_IMAGE'},
                      CORE | {'CONVERTER_IMAGE', 'BUILDER_IMAGE'}):
            with self.subTest(roles=sorted(roles)), self.assertRaises(ValueError):
                jobs.forge.capability_roles(self.images(roles), legacy=True)

    def test_core_compose_omits_ingestion_and_mineru_even_with_builder_pins(self):
        compose = self.render(CORE)
        self.assertEqual(set(compose['services']), {'embeddings', 'reranker', 'door'})
        self.assertNotIn('mineru', json.dumps(compose))
        for role in ('embeddings', 'reranker'):
            bind = compose['services'][role]['volumes'][0]
            self.assertTrue(bind['read_only'])
            self.assertFalse(bind['bind']['create_host_path'])
            self.assertEqual(compose['services'][role]['pull_policy'], 'never')
        self.assertTrue(compose['networks']['models']['internal'])

    def test_ingestion_omission_leaves_exact_search_stack_unchanged(self):
        complete, core = self.render(CORE | INGESTION), self.render(CORE)
        for name in ('embeddings', 'reranker', 'door'):
            self.assertEqual(complete['services'][name], core['services'][name])
        self.assertEqual(set(complete['services']),
                         {'embeddings', 'reranker', 'door', 'converter', 'builder', 'qdrant'})
        converter = complete['services']['converter']
        self.assertEqual(converter['network_mode'], 'none')
        self.assertTrue(converter['volumes'][0]['read_only'])
        self.assertEqual(converter['volumes'][1]['type'], 'tmpfs')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *remaining])
