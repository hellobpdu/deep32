"""Synthetic Forge restarts from retained files; no real daemon or media."""
import argparse
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retention_fixture', Path(__file__).with_name('test_installed_runtime.py'))
unit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unit)
spec = importlib.util.spec_from_file_location('retained_forge_lifecycle', KIT / 'forms/forge_docker_role.py')
forge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forge)


class RetainedForgeTests(unittest.TestCase):
    # Reuse synthetic fixture construction without inheriting its unit cases.
    seal = unit.RetainedRuntimeTests.seal
    prepare = unit.RetainedRuntimeTests.prepare

    def setUp(self):
        unit.RetainedRuntimeTests.setUp(self)
        for name in ('forms/installed_runtime.py', 'forms/provision.py', 'forms/forge_publication.py',
                     'firstboot/installed-inputs.py', 'build/image_binding.py'):
            target = self.kit / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((KIT / name).read_bytes())
        unit.dump(self.kit / 'pins.json', dict(release_profile='pack',
                  models={'tei': {'revision': 'a' * 40}}, runtime={'reranker_dtype': 'float16'},
                  tei={'selected': 'turing'}))
        self.receipt = self.prepare()
        exact_release = self.release.with_name(self.receipt['release'])
        self.release.rename(exact_release)
        self.release = exact_release
        self.root = self.work / 'installation'
        (self.root / 'config').mkdir()
        self.data = dict(release=self.receipt['release'], platform='windows', install_id='b' * 16,
                         stage='stopped', settings={})
        self.calls = []
        self.subject = forge.Lifecycle(self.root, self.kit, run=self.command, emit=lambda value: None)
        self.subject.platform = 'windows'
        patch.object(forge, 'identities').start()
        # Ownership/ACL qualification belongs to the isolated host tests.
        patch.object(forge.common, 'windows_security').start()
        self.media.rename(self.work / 'ejected-media')
        self.kit.rename(self.work / 'ejected-kit')

    def command(self, argv, reason, fix, timeout=120, emit=print):
        values = [str(value) for value in argv]
        if values[1:3] == ['--host', forge.common.DESKTOP_PIPE]:
            values = [values[0], *values[3:]]
        self.calls.append(values)
        if '--query-gpu=compute_cap' in values:
            return '7.5\n'
        if values[:3] == ['docker', 'image', 'inspect']:
            return json.dumps([dict(Id=values[-1], Os='linux', Architecture='amd64')])
        raise AssertionError('Unexpected fake host command: ' + repr(values))

    def test_runtime_inputs_after_ejection_verify_retained_files_gpu_and_images(self):
        payload, config, images = self.subject.runtime_inputs(self.data)
        self.assertEqual(payload, self.release / 'payload')
        self.assertEqual(images, self.images)
        self.assertEqual(config['tei']['selected'], 'turing')
        self.assertEqual(self.subject.installed_inventory, self.release / 'installed-runtime/PAYLOAD.json')
        self.assertEqual(len([row for row in self.calls if row[:3] == ['docker', 'image', 'inspect']]), 3)
        self.assertFalse(any('load' in row or 'pull' in row for row in self.calls))

    def test_missing_receipt_refuses_legacy_downgrade(self):
        (self.release / 'installed-runtime/receipt.json').unlink()
        unit.dump(self.release / 'media.json', {'payload': str(self.payload), 'identity': self.receipt['identity']})
        with self.assertRaises(forge.common.Refusal):
            self.subject.runtime_inputs(self.data)
        self.assertEqual(self.calls, [])

    def test_missing_or_changed_installed_image_never_loads(self):
        original = self.subject.call
        def missing(argv, *args, **kwargs):
            if argv[:3] == ['docker', 'image', 'inspect']:
                self.calls.append(list(argv))
                return '[]'
            return original(argv, *args, **kwargs)
        with patch.object(self.subject, 'call', side_effect=missing), self.assertRaises(forge.common.Refusal):
            self.subject.runtime_inputs(self.data)
        self.assertFalse(any('load' in row or 'pull' in row for row in self.calls))

    def test_changed_gpu_class_refuses_before_image_inspection(self):
        with patch.object(self.subject, 'call', return_value='8.6\n'), self.assertRaises(forge.common.Refusal):
            self.subject.runtime_inputs(self.data)
        self.assertEqual(self.calls, [])

    def test_mutated_model_refuses_before_host_or_image_commands(self):
        (self.release / 'payload/models/tei/config.json').write_bytes(b'Changed synthetic model')
        with self.assertRaises(ValueError):
            self.subject.runtime_inputs(self.data)
        self.assertEqual(self.calls, [])

    def test_source_independent_start_renders_only_retained_model_binds(self):
        commands = []
        host = unittest.mock.Mock(return_value='synthetic-forge.synthetic.ts.net')
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.subject, 'job_guard'))
            stack.enter_context(patch.object(self.subject, 'host', host))
            stack.enter_context(patch.object(self.subject, 'preflight_publication'))
            stack.enter_context(patch.object(self.subject, 'daemon_storage'))
            stack.enter_context(patch.object(self.subject, 'pair_local'))
            stack.enter_context(patch.object(self.subject, 'publication_controller', return_value=SimpleNamespace(
                publish=unittest.mock.Mock())))
            stack.enter_context(patch.object(self.subject, 'compose', side_effect=lambda *args, **kw: commands.append(args)))
            self.subject.start(self.data)
        compose = json.loads((self.release / 'compose.json').read_text(encoding='ascii'))
        for service in ('embeddings', 'reranker'):
            sources = [row['source'] for row in compose['services'][service]['volumes']]
            self.assertTrue(all(str(self.release / 'payload').replace('\\', '/') in value for value in sources))
        self.assertEqual(set(compose['services']), {'embeddings', 'reranker', 'door'})
        self.assertEqual(self.data['stage'], 'published-pairing-required')
        self.assertTrue(any('up' in command for command in commands))
        self.assertFalse(any('load' in row or 'pull' in row for row in self.calls))
        self.assertEqual(host.call_args.kwargs['installed_inventory'], self.subject.installed_inventory)

    def test_default_prepare_commits_retained_models_without_saved_archives(self):
        source_kit = self.work / 'ejected-kit'
        payload = self.work / 'ejected-media/payload'
        fresh = self.work / 'fresh-installation'
        subject = forge.Lifecycle(fresh, source_kit, run=self.command, emit=lambda value: None,
                                  host_platform='windows')
        subject.selected_class = 'turing'
        settings = self.work / 'synthetic-settings.json'
        unit.dump(settings, {})
        original_module = forge.common.module
        def modules(name, path):
            if name == 'forge_release_profile':
                return SimpleNamespace(unresolved=lambda value: [])
            return original_module(name, path)
        with ExitStack() as stack:
            stack.enter_context(patch.object(forge.common, 'module', side_effect=modules))
            stack.enter_context(patch.object(forge.common, 'startup_hook'))
            stack.enter_context(patch.object(subject, 'job_guard'))
            stack.enter_context(patch.object(subject, 'settings', return_value={}))
            stack.enter_context(patch.object(subject, 'host', return_value='synthetic-forge.synthetic.ts.net'))
            stack.enter_context(patch.object(subject, 'preflight_publication'))
            stack.enter_context(patch.object(subject, 'images', return_value=self.images))
            stack.enter_context(patch.object(subject, 'daemon_storage'))
            started = stack.enter_context(patch.object(subject, 'start'))
            data = subject.prepare(payload, settings, platform='windows')
        release = fresh / 'releases' / data['release']
        receipt = unit.installed.verify(release, expected_release=data['release'])
        self.assertEqual(receipt['images'], self.images)
        self.assertEqual(receipt['selection'], 'turing')
        self.assertTrue((release / 'payload/models/tei/model.safetensors').is_file())
        self.assertFalse(any(path.suffix == '.tar' for path in release.rglob('*')))
        self.assertEqual(started.call_args.args, (data,))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    arguments, remaining = parser.parse_known_args()
    if not arguments.scratch.is_dir() or any(arguments.scratch.iterdir()):
        parser.error('--scratch must be an empty dedicated synthetic-test directory')
    unit.SCRATCH = arguments.scratch
    unittest.main(argv=[sys.argv[0], *remaining])
