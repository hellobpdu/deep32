#!/usr/bin/env python3
"""Portable job continuity tests; synthetic state and local command stand-ins."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tested_forge_jobs', KIT / 'forms/forge_jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)
common = jobs.common
SCRATCH = None


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-jobs-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'install'
        common.private_directory(self.root)
        self.data = dict(install_id='1' * 16, release='2' * 64, stage='local-ready-publication-required', settings={})
        self.calls = []
        self.running = 'embeddings\ndoor\n'
        self.container = ''
        self.owner = 'deep32-' + self.data['install_id']
        self.fail_job = self.fail_restore = False
        self.life = jobs.forge.Lifecycle(self.root, KIT, run=self.command, emit=lambda value: None)
        self.subject = jobs.Jobs(self.life)
        self.state = patch.object(self.life, 'state', return_value=self.data)
        self.state.start()
        self.addCleanup(self.state.stop)
        self.runtime = patch.object(self.subject, 'runtime', return_value=(self.data, {'builder': True}))
        self.runtime_mock = self.runtime.start()
        self.addCleanup(self.runtime.stop)
        (self.root / 'jobs/synthetic').mkdir(parents=True)
        (self.root / 'jobs/synthetic/input.pdf').write_bytes(b'SYNTHETIC TEST INPUT ONLY\n')

    def command(self, argv, reason, fix, timeout=120, emit=print):
        self.calls.append(list(argv))
        if 'compose' in argv:
            if 'ps' in argv:
                return self.running
            if 'run' in argv and 'converter' in argv and self.fail_job:
                raise common.Refusal('synthetic converter failure', 1)
            if 'up' in argv and self.fail_restore:
                raise common.Refusal('synthetic model restore failure', 3)
        if 'container' in argv:
            if 'ls' in argv:
                return self.container
            if 'inspect' in argv:
                return self.owner
        return ''

    def saved(self, services=('embeddings',)):
        common.atomic_json(self.subject.record, dict(schema=jobs.RECOVERY_SCHEMA,
            install_id=self.data['install_id'], job='synthetic', services=list(services)))

    def test_conversion_restores_exact_running_models_on_linux_and_windows(self):
        for platform in ('linux', 'windows'):
            with self.subTest(platform=platform):
                self.life.platform = platform
                self.calls.clear()
                self.subject.convert(['synthetic', 'pymupdf'])
                self.assertFalse(self.subject.record.exists())
                self.assertTrue(any(row[-3:] == ['stop', 'embeddings', 'reranker'] for row in self.calls))
                execution = next(row for row in self.calls if 'run' in row and 'converter' in row)
                self.assertIn('conversion', execution)
                self.assertIn('--no-deps', execution)
                self.assertEqual(execution[-6:], ['--backend', 'pymupdf', '--input', '/jobs/synthetic/input.pdf',
                                                 '--output', '/jobs/synthetic/output'])
                restore = next(row for row in self.calls if 'up' in row)
                self.assertEqual(restore[-1], 'embeddings')
                self.assertNotIn('reranker', restore)
                self.assertNotIn('engine', ' '.join(' '.join(row) for row in self.calls))
                if platform == 'windows':
                    self.assertTrue(all(row[:3] == ['docker', '--host', common.DESKTOP_PIPE] for row in self.calls))

    def test_failed_job_restores_models_before_reporting_failure(self):
        self.fail_job = True
        with self.assertRaisesRegex(common.Refusal, 'converter failure'):
            self.subject.convert(['synthetic', 'pymupdf'])
        self.assertFalse(self.subject.record.exists())
        self.assertTrue(any('up' in row and row[-1] == 'embeddings' for row in self.calls))

    def test_failed_restore_retains_recovery_and_manual_recovery_completes(self):
        self.fail_restore = True
        with self.assertRaisesRegex(common.Refusal, 'restore failure'):
            self.subject.convert(['synthetic', 'pymupdf'])
        self.assertTrue(self.subject.record.exists())
        self.fail_restore = False
        self.subject.recover([])
        self.assertFalse(self.subject.record.exists())

    def test_owned_converter_stops_before_model_restore_and_foreign_one_is_retained(self):
        self.saved()
        self.container = 'deep32-convert-' + self.data['install_id'] + '-synthetic'
        self.subject.recover([])
        removed = next(index for index, row in enumerate(self.calls) if 'container' in row and 'rm' in row)
        restored = next(index for index, row in enumerate(self.calls) if 'compose' in row and 'up' in row)
        self.assertLess(removed, restored)
        self.calls.clear()
        self.saved()
        self.owner = 'another-project'
        with self.assertRaisesRegex(common.Refusal, 'another project'):
            self.subject.recover([])
        self.assertTrue(self.subject.record.exists())
        self.assertFalse(any('rm' in row or 'up' in row for row in self.calls))

    def test_gpu_hold_and_pending_recovery_refuse_before_stopping_models(self):
        hold = self.root / 'status/gpu-hold'
        hold.parent.mkdir()
        hold.write_text('synthetic operator hold\n', encoding='ascii')
        with self.assertRaises(common.Refusal) as refused:
            self.subject.convert(['synthetic', 'pymupdf'])
        self.assertEqual(refused.exception.code, 4)
        self.assertFalse(self.calls)
        with self.assertRaises(common.Refusal) as refused:
            self.subject.builder('build', ['--synthetic'])
        self.assertEqual(refused.exception.code, 4)
        self.assertFalse(self.calls)
        hold.unlink()
        self.saved()
        for verb in ('convert', 'build'):
            with self.subTest(verb=verb), self.assertRaises(common.Refusal):
                if verb == 'convert':
                    self.subject.convert(['synthetic', 'pymupdf'])
                else:
                    self.subject.builder('build', ['--synthetic'])
        self.assertFalse(self.calls)

    def test_invalid_inputs_options_and_output_refuse_without_runtime(self):
        for arguments in (['../bad', 'pymupdf'], ['synthetic', 'pymupdf', '--tier', 'advanced'],
                          ['synthetic', 'mineru', '--page-range', '1-2\n']):
            with self.subTest(arguments=arguments), self.assertRaises(common.Refusal):
                self.subject.convert(arguments)
        self.runtime_mock.assert_not_called()
        (self.root / 'jobs/synthetic/output').mkdir()
        with self.assertRaises(common.Refusal):
            self.subject.convert(['synthetic', 'pymupdf'])
        self.assertFalse(self.calls)

    def test_mineru_options_and_production_inputs_reach_original_converter_api(self):
        self.subject.convert(['synthetic', 'mineru', '--tier', 'standard', '--page-range', '2-5'])
        execution = next(row for row in self.calls if 'run' in row and 'converter' in row)
        self.assertEqual(execution[-4:], ['--tier', 'standard', '--page-range', '2-5'])
        self.calls.clear()
        job = self.root / 'jobs/synthetic'
        (job / 'input.pdf').unlink()
        (job / 'input.html').write_text('synthetic test input', encoding='ascii')
        (job / 'job.json').write_text('{}\n', encoding='ascii')
        (job / 'lexicon').mkdir()
        self.subject.convert(['synthetic', 'production'])
        execution = next(row for row in self.calls if 'run' in row and 'converter' in row)
        self.assertIn('/jobs/synthetic/input.html', execution)
        self.assertEqual(execution[-4:], ['--job', '/jobs/synthetic/job.json', '--lexicon', '/jobs/synthetic/lexicon'])

    def test_corrupt_record_cannot_name_extra_services_or_another_install(self):
        self.saved(('gateway',))
        with self.assertRaises(common.Refusal):
            self.subject.recover([])
        self.assertFalse(self.calls)
        self.runtime_mock.assert_not_called()
        self.saved()
        value = common.read_json(self.subject.record)
        value['install_id'] = '3' * 16
        common.atomic_json(self.subject.record, value)
        with self.assertRaises(common.Refusal):
            self.subject.recover([])
        self.assertFalse(self.calls)
        self.runtime_mock.assert_not_called()

    def test_job_and_lifecycle_share_the_same_exclusive_lock(self):
        with common.installation_lock(self.root):
            with self.assertRaises(common.Refusal) as refused:
                self.subject.convert(['synthetic', 'pymupdf'])
            self.assertEqual(refused.exception.code, 11)
        self.assertFalse(self.calls)
        self.assertFalse(self.subject.record.exists())

    def test_changed_installation_during_job_preflight_refuses_before_write(self):
        self.life.state.return_value = dict(self.data, release='4' * 64)
        with self.assertRaises(common.Refusal) as refused:
            self.subject.convert(['synthetic', 'pymupdf'])
        self.assertEqual(refused.exception.code, 11)
        self.assertFalse(self.calls)
        self.assertFalse(self.subject.record.exists())

    def test_build_and_bundle_preserve_builder_cli_and_container_mount_paths(self):
        for verb in ('build', 'bundle'):
            with self.subTest(verb=verb):
                self.calls.clear()
                arguments = ['--served-generation', 'synthetic', '--bundle-dir', '/var/lib/deep32-forge/bundles']
                self.subject.builder(verb, arguments)
                execution = next(row for row in reversed(self.calls) if 'run' in row and 'builder' in row)
                self.assertEqual(execution[-(len(arguments) + 2):], ['builder', verb, *arguments])
                self.assertIn('--no-deps', execution)
                self.assertEqual(any('up' in row for row in self.calls), verb == 'build')
                if verb == 'build':
                    started = next(row for row in self.calls if 'up' in row)
                    self.assertEqual(started[-3:], ['qdrant', 'embeddings', 'reranker'])
                    self.assertTrue(any(row[-1] == '/opt/deep32/builder/wait_services.py' for row in self.calls))

    def test_runtime_rechecks_media_compose_authority_and_does_not_load_images(self):
        self.runtime.stop()
        self.data['settings'] = {'forge_ports': {'embeddings': 18081, 'reranker': 18082}}
        identity, pin_digest = 'a' * 64, 'b' * 64
        self.data['release'] = hashlib.sha256((identity + pin_digest).encode('ascii')).hexdigest()
        release = self.root / 'releases' / self.data['release']
        kit = release / 'kit'
        kit.mkdir(parents=True)
        payload = self.root.parent / 'synthetic-payload'
        payload.mkdir()
        config = dict(models={'tei': {'revision': 'c' * 40}}, runtime={'reranker_dtype': 'float16'})
        selected = {key: 'sha256:' + str(index) * 64 for index, key in enumerate(
            ('TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE', 'CONVERTER_IMAGE'), 1)}
        common.atomic_json(kit / 'pins.json', config)
        common.atomic_json(release / 'media.json', dict(payload=str(payload), identity=identity))
        expected = jobs.forge.prepare_config(kit, payload, self.root, selected, config, self.data['settings'])
        self.assertEqual(expected['services']['door']['ports'],
                         ['127.0.0.1:18081:8081', '127.0.0.1:18082:8082'])
        common.atomic_json(release / 'compose.json', expected)
        inputs = SimpleNamespace(verified_payload=lambda path: identity, digest=lambda path: pin_digest)
        original_module = common.module
        with patch.object(common, 'module', side_effect=lambda name, path:
                inputs if name == 'forge_start_provision' else original_module(name, path)), \
                patch.object(self.life, 'images', return_value=selected) as images, \
                patch.object(jobs.forge, 'identities') as authority:
            self.assertEqual(self.subject.runtime(), (self.data, config))
            images.assert_called_once_with(payload, config, allow_load=False)
            authority.assert_called_once_with(self.root, kit, committed=True)
            self.assertTrue(any(row[-2:] == ['config', '--quiet'] for row in self.calls))
            self.calls.clear()
            expected['services']['door']['ports'] = ['0.0.0.0:8081:8081']
            common.atomic_json(release / 'compose.json', expected)
            with self.assertRaisesRegex(common.Refusal, 'retained Forge Compose'):
                self.subject.runtime()
            self.assertFalse(self.calls)

    def test_missing_job_image_refuses_without_a_docker_load(self):
        payload = self.root.parent / 'synthetic-image-payload'
        (payload / 'images').mkdir(parents=True)
        rows = [dict(role=role, path='images/synthetic-' + role + '.tar',
                     config_digest='sha256:' + str(index) * 64) for index, role in enumerate(
                         ('tei-ampere86', 'reranker', 'door', 'converter'), 1)]
        (payload / 'images/index.json').write_text(json.dumps(rows), encoding='ascii')
        modules = dict(docker_installed_inputs=SimpleNamespace(CAPABILITIES={'8.6': 'ampere86'},
                           verified_image=lambda config, row, binding: row), docker_image_binding=SimpleNamespace())
        calls = []
        def command(argv, *args, **kwargs):
            calls.append(argv)
            if argv[0] in ('nvidia-smi', 'nvidia-smi.exe'):
                return '8.6\n'
            raise common.Refusal('Synthetic pinned image is missing.', 1)
        with patch.object(common, 'module', side_effect=lambda name, path: modules[name]), \
                patch.object(self.life, 'run', side_effect=command):
            with self.assertRaisesRegex(common.Refusal, 'pinned image is missing'):
                self.life.images(payload, {'tei': {'selected': 'ampere86'}}, allow_load=False)
        self.assertTrue(any('inspect' in row for row in calls))
        self.assertFalse(any('load' in row for row in calls))

    def test_recovery_repairs_missing_converter_or_builder_only_under_validated_lock(self):
        self.runtime.stop()
        self.data['settings'] = {'forge_ports': {'embeddings': 18081, 'reranker': 18082}}
        identity, pin_digest = 'a' * 64, 'b' * 64
        self.data['release'] = hashlib.sha256((identity + pin_digest).encode('ascii')).hexdigest()
        release = self.root / 'releases' / self.data['release']
        kit = release / 'kit'
        kit.mkdir(parents=True)
        payload = self.root.parent / 'synthetic-repair-payload'
        (payload / 'images').mkdir(parents=True)
        roles = ('tei-ampere86', 'reranker', 'door', 'converter', 'builder', 'qdrant')
        rows = [dict(role=role, path='images/synthetic-' + role + '.tar', config_digest='sha256:' + str(index) * 64)
                for index, role in enumerate(roles, 1)]
        (payload / 'images/index.json').write_text(json.dumps(rows), encoding='ascii')
        config = dict(models={'tei': {'revision': 'c' * 40}}, tei={'selected': 'ampere86'},
                      runtime={'reranker_dtype': 'float16'}, builder={'synthetic': True})
        selected = {key: row['config_digest'] for key, row in zip(
            ('TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE', 'CONVERTER_IMAGE', 'BUILDER_IMAGE', 'QDRANT_IMAGE'), rows)}
        common.atomic_json(kit / 'pins.json', config)
        common.atomic_json(release / 'media.json', dict(payload=str(payload), identity=identity))
        expected = jobs.forge.prepare_config(kit, payload, self.root, selected, config, self.data['settings'])
        self.assertEqual(expected['services']['door']['ports'],
                         ['127.0.0.1:18081:8081', '127.0.0.1:18082:8082'])
        common.atomic_json(release / 'compose.json', expected)
        original_module = common.module
        for platform in ('linux', 'windows'):
            for role in ('converter', 'builder'):
                with self.subTest(platform=platform, missing=role):
                    self.life.platform = platform
                    self.saved()
                    events, loads = [], []
                    missing = {next(row['config_digest'] for row in rows if row['role'] == role)}
                    def locked():
                        with self.assertRaises(common.Refusal) as refused:
                            with common.installation_lock(self.root):
                                self.fail('recovery released the shared lifecycle lock')
                        self.assertEqual(refused.exception.code, 11)
                    def verify_payload(path):
                        locked()
                        events.append('payload')
                        return identity
                    def authority(*args, **kwargs):
                        locked()
                        events.append('authority')
                    def verify_image(config, row, binding):
                        locked()
                        events.append('verify:' + row['role'])
                        return row
                    modules = dict(forge_start_provision=SimpleNamespace(verified_payload=verify_payload,
                                       digest=lambda path: pin_digest),
                                   docker_installed_inputs=SimpleNamespace(CAPABILITIES={'8.6': 'ampere86'},
                                       verified_image=verify_image), docker_image_binding=SimpleNamespace())
                    def command(argv, *args, **kwargs):
                        locked()
                        if argv[0] in ('nvidia-smi', 'nvidia-smi.exe'):
                            return '8.6\n'
                        if platform == 'windows':
                            self.assertEqual(argv[:3], ['docker', '--host', common.DESKTOP_PIPE])
                        if 'image' in argv and 'inspect' in argv:
                            if argv[-1] in missing:
                                raise common.Refusal('Synthetic image absent.', 1)
                            return json.dumps([dict(Id=argv[-1], Os='linux', Architecture='amd64')])
                        if 'image' in argv and 'load' in argv:
                            self.assertEqual(events[:2], ['payload', 'authority'])
                            self.assertEqual([item for item in events if item.startswith('verify:')],
                                             ['verify:' + item for item in roles])
                            loads.append(argv[-1])
                            missing.clear()
                        return ''
                    with patch.object(common, 'module', side_effect=lambda name, path:
                            modules[name] if name in modules else original_module(name, path)), \
                            patch.object(jobs.forge, 'identities', side_effect=authority), \
                            patch.object(self.life, 'run', side_effect=command):
                        self.subject.recover([])
                    self.assertEqual(loads, [str(payload / ('images/synthetic-' + role + '.tar'))])
                    self.assertFalse(self.subject.record.exists())

    def test_recovery_validation_or_repair_failure_retains_record(self):
        self.saved()
        for failure in (ValueError('synthetic damaged payload'), common.Refusal('Synthetic archive check failed.', 1),
                        common.Refusal('Synthetic image loading failed.', 1)):
            with self.subTest(failure=str(failure)), \
                    patch.object(self.subject, 'runtime', side_effect=failure) as runtime:
                with self.assertRaises(type(failure)):
                    self.subject.recover([])
                runtime.assert_called_once_with(allow_load=True)
            self.assertTrue(self.subject.record.exists())
            self.assertFalse(self.calls)


class WrapperTests(unittest.TestCase):
    def test_real_wrappers_dispatch_portable_root_and_preserve_legacy_dry_run(self):
        bash = Path('C:/Program Files/Git/bin/bash.exe')
        self.assertTrue(bash.is_file())
        with tempfile.TemporaryDirectory(prefix='synthetic-wrapper-', dir=SCRATCH) as temporary:
            work = Path(temporary)
            bindir = work / 'bin'
            bindir.mkdir()
            capture = work / 'arguments.txt'
            (bindir / 'python3').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$D32_CAPTURE"\n',
                                           encoding='ascii', newline='\n')
            retained = work / 'install/releases' / ('a' * 64) / 'kit/containers'
            retained.mkdir(parents=True)
            def shell_path(path):
                return '/' + path.drive[0].lower() + path.as_posix()[2:]
            environment = dict(os.environ, D32_JOB_BIN=shell_path(bindir), D32_CAPTURE=capture.as_posix(),
                               MSYS_NO_PATHCONV='1')
            for name, action, arguments in (('convert-job.sh', 'convert', ['synthetic', 'pymupdf']),
                                            ('recover-job.sh', 'recover', []),
                                            ('builder-job.sh', 'build', ['build', '--generation', 'synthetic'])):
                source = KIT / 'containers' / name
                installed = retained / name
                installed.write_bytes(source.read_bytes())
                for wrapper, prefix in ((source, ['--data-root', 'C:/synthetic-install']), (installed, [])):
                    result = subprocess.run([str(bash), '--noprofile', '--norc', '-c',
                        'export PATH="$D32_JOB_BIN:/usr/bin:/bin"; exec /bin/bash "$@"',
                        'synthetic-wrapper', wrapper.as_posix(), *prefix, *arguments],
                        env=environment, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    observed = capture.read_text(encoding='ascii').splitlines()
                    self.assertEqual(observed[:2], ['-I', '-B'])
                    self.assertTrue(observed[2].endswith('/forms/forge_jobs.py'))
                    self.assertEqual(observed[3], '--data-root')
                    self.assertEqual(observed[5], action)
                    self.assertEqual(observed[6:], arguments[1:] if action == 'build' else arguments)
                result = subprocess.run([str(bash), '--noprofile', '--norc', source.as_posix(), '--dry-run'],
                                        env=environment, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Downloads: none.', result.stdout)
                capture.unlink()
                result = subprocess.run([str(bash), '--noprofile', '--norc', source.as_posix(),
                                         '--data-root', '', *arguments], env=environment,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(capture.exists(), 'an empty explicit root fell back to another installation')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *remaining])
