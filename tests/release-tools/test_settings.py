#!/usr/bin/env python3
"""Offline C7 handoff tests. All generated data is synthetic and task-local."""
import argparse
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('deep32_settings', ROOT / 'src/release-tools/deep32_settings.py')
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)
SCRATCH = None


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-settings-', dir=SCRATCH)
        self.root = Path(self.temp.name)
        self.profiles = settings.validator().profiles
        self.data = {'format': 'deep32-settings', 'format_version': 1, 'role': 'forge', **settings.DEFAULTS}
        self.plan = settings.make_plan(self.data, 'a' * 64, self.root, 'J2', 'wsl', 'windows', 'forge', self.profiles)
        self.target = self.root / 'target/settings.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_input_writes_nothing(self):
        for field in settings.FIELDS:
            value = dict(self.data)
            del value[field]
            path = self.root / 'synthetic-input.json'
            path.write_bytes(settings.canonical(value))
            with self.assertRaises(ValueError): settings.collect(path, {}, self.profiles)
            self.assertFalse(self.target.parent.exists())

    def test_invalid_unknown_secret_and_conflict_write_nothing(self):
        cases = [('profile', 'gpu'), ('profile', 'no-such-profile'), ('node_name', 'tskey-SYNTHETIC'),
                 ('node_name', 'token=SYNTHETIC'), ('user_name', 'root'), ('publish', 'tailnet-plain'),
                 ('time_zone', 'https://synthetic.invalid'), ('unknown', False), ('token', 'synthetic-test-only')]
        cases += [('written_by', 'ghp_SYNTHETICTESTONLY'), ('written_by', 'eyJSYNTHETIC.payload.signature')]
        for field, value in cases:
            data = {**self.data, field: value}
            with self.assertRaises(ValueError): settings.make_plan(data, 'a' * 64, self.root, 'J2', 'docker', 'linux', 'forge', self.profiles)
            self.assertFalse(self.target.parent.exists())
        path = self.root / 'synthetic-input.json'
        path.write_bytes(settings.canonical(self.data))
        with self.assertRaises(ValueError): settings.collect(path, {'node_name': 'different'}, self.profiles)

    def test_guided_prompts_once_with_neutral_defaults(self):
        seen = []
        data = settings.collect(None, {}, self.profiles, True, lambda question: seen.append(question) or '')
        self.assertEqual(data, self.data)
        self.assertEqual(len(seen), len(settings.FIELDS))

    def test_atomic_protected_and_idempotent(self):
        self.assertEqual(settings.install(self.plan, self.plan['digest'], self.target), 'written')
        settings.protect(self.target, verify=True)
        settings.protect(self.target.parent, directory=True, verify=True)
        self.assertEqual(json.loads(self.target.read_bytes()), self.data)
        original = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.target.parent.iterdir()}
        self.assertEqual(settings.install(self.plan, self.plan['digest'], self.target), 'unchanged')
        self.assertEqual(original, {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.target.parent.iterdir()})
        self.assertEqual(sorted(original), ['settings-deployment.json', 'settings.json'])

    def test_bad_digest_and_changed_identity_refused(self):
        with self.assertRaises(ValueError): settings.install(self.plan, '0' * 64, self.target)
        self.assertFalse(self.target.parent.exists())
        settings.install(self.plan, self.plan['digest'], self.target)
        original = self.target.read_bytes()
        other = settings.make_plan(self.data, 'b' * 64, self.root, 'J2', 'wsl', 'windows', 'forge', self.profiles)
        with self.assertRaises(ValueError): settings.install(other, other['digest'], self.target)
        self.assertEqual(self.target.read_bytes(), original)

    def test_power_loss_record_resumes_same_install(self):
        real_atomic = settings.atomic
        def interrupted(path, content):
            if path.name == 'settings.json': raise OSError('synthetic interrupted write')
            return real_atomic(path, content)
        with patch.object(settings, 'atomic', interrupted):
            with self.assertRaises(OSError): settings.install(self.plan, self.plan['digest'], self.target)
        self.assertFalse(self.target.exists())
        self.assertTrue((self.target.parent / 'settings-deployment.json').exists())
        self.assertEqual(settings.install(self.plan, self.plan['digest'], self.target), 'written')

    def test_reparse_and_symlink_refused(self):
        real_lstat = Path.lstat
        def synthetic_reparse(path):
            info = real_lstat(path)
            if path == self.root:
                class Reparse:
                    st_mode = info.st_mode
                    st_file_attributes = 1024
                return Reparse()
            return info
        with patch.object(Path, 'lstat', synthetic_reparse):
            with self.assertRaises(ValueError): settings.install(self.plan, self.plan['digest'], self.target)
        self.assertFalse(self.target.parent.exists())

    def test_duplicate_json_refused(self):
        path = self.root / 'synthetic-duplicate.json'
        path.write_text('{"format":"a","format":"b"}', encoding='ascii')
        with self.assertRaises(ValueError): settings.read_json(path)

    def test_wsl_shared_writer_and_acl_stage(self):
        plan = settings.make_plan(self.data, 'a' * 64, self.root, 'J1', 'wsl', 'windows', 'forge', self.profiles)
        directory = self.root / 'protected-host-stage'
        settings.stage(plan, directory)
        for path in directory.iterdir(): settings.protect(path, verify=True)
        program = settings.wsl_program(plan)
        compile(program, '<synthetic-wsl-handoff>', 'exec')
        namespace = {'__name__': 'synthetic_wsl_test'}
        # Define the exact transported writer without running its final root-only
        # action. Exercise it against a fixture path, never a distribution.
        exec(program[:program.index('\nplan=json.loads(')], namespace)
        # The live guest uses POSIX modes. The Windows fixture supplies the
        # already-tested native ACL adapter for the same writer's file calls.
        namespace['windows_security'] = settings.windows_security
        transported = json.loads((directory / 'plan.json').read_bytes())
        guest = self.root / 'synthetic-guest/settings.json'
        self.assertEqual(namespace['install'](transported, transported['digest'], guest), 'written')
        self.assertEqual(guest.read_bytes(), (directory / 'settings.json').read_bytes())
        invalid = dict(self.data, unexpected='synthetic')
        for validate in (settings.validator().validate, namespace['validate']):
            with self.assertRaises(ValueError) as refusal:
                validate(invalid)
            self.assertEqual(str(refusal.exception), settings.validator().REFUSAL)

    def test_profile_list_cannot_be_forged(self):
        plan = copy.deepcopy(self.plan)
        plan['profiles'].append('invented')
        plan['digest'] = __import__('hashlib').sha256(settings.canonical({k: v for k, v in plan.items() if k != 'digest'})).hexdigest()
        with self.assertRaises(ValueError): settings.install(plan, plan['digest'], self.target)
        self.assertFalse(self.target.parent.exists())

    def test_invalid_roles_refused(self):
        for form, platform, role, journey in [('docker', 'unsupported', 'full', 'J3'), ('docker', 'linux', 'full', 'J2'), ('docker', 'linux', 'forge', 'J5')]:
            with self.assertRaises(ValueError): settings.make_plan(self.data, 'a' * 64, self.root, journey, form, platform, role, self.profiles)

    def test_full_docker_plan_binds_paths_on_both_hosts(self):
        choice = dict(self.data, profile='headless')
        for platform in ('linux', 'windows'):
            plan = settings.make_plan(choice, 'a' * 64, self.root, 'J3', 'docker', platform, 'full',
                                      self.profiles, self.root / 'data', self.root / 'content')
            self.assertEqual(settings.check_plan(plan, plan['digest']), plan)
            self.assertEqual(plan['identity']['target'], str(self.root / 'data/config/settings.json'))
            changed = copy.deepcopy(plan)
            changed['identity']['content'] = str(self.root / 'other')
            with self.assertRaises(ValueError): settings.check_plan(changed, plan['digest'])
            self.assertFalse((self.root / 'data').exists())

    def test_forge_docker_plan_binds_private_paths_on_both_hosts(self):
        for platform, journey in (('linux', 'J2'), ('windows', 'J1')):
            with self.assertRaisesRegex(ValueError, '--data-root'):
                settings.make_plan(self.data, 'a' * 64, self.root, journey, 'docker', platform, 'forge', self.profiles)
            plan = settings.make_plan(self.data, 'a' * 64, self.root, journey, 'docker', platform, 'forge',
                                      self.profiles, self.root / 'forge', docker_storage_path=self.root / 'backing')
            self.assertEqual(settings.check_plan(plan, plan['digest']), plan)
            self.assertEqual(plan['identity']['target'], str(self.root / 'forge/config/settings.json'))
            self.assertNotIn('content', plan['identity'])
            with self.assertRaisesRegex(ValueError, 'shared Docker lifecycle'):
                settings.install(plan, plan['digest'], self.target)
            for field in ('data_root', 'docker_storage_path', 'docker_role'):
                changed = copy.deepcopy(plan)
                changed['identity'][field] = 'changed'
                with self.assertRaises(ValueError): settings.check_plan(changed, plan['digest'])
        self.assertFalse((self.root / 'forge').exists())

    def test_split_docker_plan_binds_all_three_component_records(self):
        components = [
            {'component': 'core', 'size': 8, 'sha256': 'a' * 64},
            {'component': 'embedder-turing', 'size': 13, 'sha256': 'b' * 64},
            {'component': 'ingestion', 'size': 21, 'sha256': 'c' * 64},
        ]
        root = self.root / 'forge'
        backing = self.root / 'desktop-backing'
        backing.mkdir()
        non_ascii_backing = self.root / 'dock\u00e9r'
        non_ascii_backing.mkdir()

        choices = dict(self.data, forge_accept_ct=True)

        def make(rows, data=choices, tailnet_binding=None):
            return settings.make_plan(data, 'a' * 64, self.root, 'J2', 'docker', 'linux',
                                      'forge', self.profiles, root, media_components=rows,
                                      tailnet_binding=tailnet_binding)

        with self.assertRaisesRegex(ValueError, 'Certificate Transparency'):
            make(components, self.data)
        with self.assertRaisesRegex(ValueError, 'tailnet transport'):
            make(components, dict(choices, transport='loopback'))
        with self.assertRaisesRegex(ValueError, 'ASCII characters'):
            settings.make_plan(choices, 'a' * 64, self.root, 'J2', 'docker', 'linux',
                               'forge', self.profiles, self.root / 'forg\u00e9', media_components=components)
        with self.assertRaisesRegex(ValueError, 'ASCII characters'):
            settings.make_plan(choices, 'a' * 64, self.root, 'J1', 'docker', 'windows',
                               'forge', self.profiles, root,
                               docker_storage_path=non_ascii_backing, media_components=components,
                               desktop_isolation_confirmed=True)
        plan = make(components)
        self.assertNotIn('tailnet_sha256', plan['identity'])
        binding = settings.tailnet_digest('synthetic-tailnet.ts.net')
        confirmed = make(components, tailnet_binding=binding)
        self.assertEqual(settings.check_plan(confirmed, confirmed['digest']), confirmed)
        self.assertEqual(confirmed['identity']['tailnet_sha256'], binding)
        self.assertNotIn('synthetic-tailnet', settings.canonical(confirmed).decode('ascii'))
        other = make(components, tailnet_binding=settings.tailnet_digest('other-tailnet.ts.net'))
        with self.assertRaisesRegex(ValueError, 'plan consent digest'):
            settings.check_plan(other, confirmed['digest'])
        with self.assertRaisesRegex(ValueError, 'split Docker Forge'):
            make(components, tailnet_binding='A' * 64)
        with self.assertRaisesRegex(ValueError, 'Confirm the intended tailnet'):
            settings.make_plan(choices, 'a' * 64, self.root, 'J1', 'docker', 'windows',
                               'forge', self.profiles, root, media_components=components,
                               docker_storage_path=backing, desktop_isolation_confirmed=True)
        with self.assertRaisesRegex(ValueError, 'unrelated distributions'):
            settings.make_plan(choices, 'a' * 64, self.root, 'J1', 'docker', 'windows',
                               'forge', self.profiles, root, media_components=components,
                               docker_storage_path=backing, tailnet_binding=binding)
        with self.assertRaisesRegex(ValueError, '--docker-storage-path'):
            settings.make_plan(choices, 'a' * 64, self.root, 'J1', 'docker', 'windows',
                               'forge', self.profiles, root, media_components=components,
                               desktop_isolation_confirmed=True, tailnet_binding=binding)
        windows = settings.make_plan(choices, 'a' * 64, self.root, 'J1', 'docker', 'windows',
                                     'forge', self.profiles, root, media_components=components,
                                     docker_storage_path=backing, desktop_isolation_confirmed=True,
                                     tailnet_binding=binding)
        self.assertEqual(settings.check_plan(windows, windows['digest']), windows)
        self.assertNotIn('synthetic-tailnet', settings.canonical(windows).decode('ascii'))
        self.assertIs(windows['identity']['desktop_isolation_confirmed'], True)
        invalid = copy.deepcopy(windows)
        invalid['identity']['desktop_isolation_confirmed'] = 1
        with self.assertRaisesRegex(ValueError, 'unrelated distributions'):
            settings.check_plan(invalid, windows['digest'])
        del windows['identity']['tailnet_sha256']
        with self.assertRaisesRegex(ValueError, 'Confirm the intended tailnet'):
            settings.check_plan(windows, windows['digest'])
        unexpected = copy.deepcopy(self.plan)
        unexpected['identity']['tailnet_sha256'] = binding
        with self.assertRaisesRegex(ValueError, 'split Docker Forge'):
            settings.check_plan(unexpected, self.plan['digest'])
        self.assertFalse(root.with_name('forge.deep32-media').exists())
        for index, key, value in ((1, 'component', 'embedder-ada'), (2, 'size', 22),
                                  (1, 'sha256', 'e' * 64), (2, 'sha256', 'f' * 64)):
            changed = copy.deepcopy(components)
            changed[index][key] = value
            other = make(changed)
            with self.assertRaisesRegex(ValueError, 'plan consent digest'):
                settings.check_plan(other, plan['digest'])
        for wrong_order in (components[:2], [components[1], components[0], components[2]]):
            with self.assertRaisesRegex(ValueError, 'split media'):
                make(wrong_order)
        cache = settings.media_cache(settings.check_plan(plan, plan['digest']))
        self.assertEqual(cache, settings.media_cache(make(components)))
        self.assertEqual(cache.parent, root.with_name('forge.deep32-media'))
        settings.protect(cache.parent, directory=True, verify=True)
        self.assertFalse(root.exists())

    def test_tailnet_identity_rejects_untyped_or_mismatched_status(self):
        status = {
            'BackendState': 'Running',
            'CurrentTailnet': {'MagicDNSSuffix': 'Synthetic-Tailnet.TS.NET.'},
            'Self': {'Online': True, 'DNSName': 'Node.Synthetic-Tailnet.TS.NET.'},
        }
        self.assertEqual(settings.tailnet_identity(status),
                         ('synthetic-tailnet.ts.net', 'node.synthetic-tailnet.ts.net'))
        self.assertEqual(settings.tailnet_digest('Synthetic-Tailnet.TS.NET.'),
                         hashlib.sha256(b'synthetic-tailnet.ts.net').hexdigest())
        for key, field, value in (
            ('Self', 'Online', 1),
            ('Self', 'Online', False),
            ('Self', 'DNSName', 'synthetic-tailnet.ts.net'),
            ('Self', 'DNSName', 'node.other-tailnet.ts.net'),
            ('Self', 'DNSName', 'bad..synthetic-tailnet.ts.net'),
            ('CurrentTailnet', 'MagicDNSSuffix', None),
            ('CurrentTailnet', 'MagicDNSSuffix', 'synthetic_tailnet.ts.net'),
            ('CurrentTailnet', 'MagicDNSSuffix', 'synthetic-tailnet.invalid'),
            ('CurrentTailnet', 'MagicDNSSuffix', 'synthetic-tailnet.ts.net..'),
        ):
            with self.subTest(key=key, field=field, value=value):
                malformed = copy.deepcopy(status)
                malformed[key][field] = value
                with self.assertRaises(ValueError):
                    settings.tailnet_identity(malformed)
        for malformed in ([], dict(status, BackendState='Stopped'), dict(status, Self=None)):
            with self.assertRaises(ValueError):
                settings.tailnet_identity(malformed)

    def test_windows_tailnet_lookup_skips_ambiguous_paths(self):
        from unittest import mock
        for path in ('relative', r'C:relative', r'\\synthetic\share', r'\\?\C:\device',
                     r'C:\directory\..\branch', r'C:\NUL.iso', r'C:\data:stream'):
            with self.subTest(path=path):
                self.assertFalse(settings.windows_local_path(path))
        self.assertTrue(settings.windows_local_path(r'C:\ordinary folder\data'))
        if os.name != 'nt':
            return
        current = self.root / 'current'
        allowed = self.root / 'installed'
        for folder in (current, allowed):
            folder.mkdir()
            (folder / 'tailscale.exe').write_bytes(b'synthetic name only')
        entries = os.pathsep.join(('', 'relative', str(current), r'\\synthetic\share', str(allowed)))
        with mock.patch.dict(os.environ, {'PATH': entries, 'ProgramFiles': ''}), \
                mock.patch.object(settings.os, 'getcwd', return_value=str(current)):
            self.assertEqual(settings.windows_tailscale_executable(), str(allowed / 'tailscale.exe'))

    def test_tailnet_confirmation_uses_an_explicit_choice_once(self):
        from unittest import mock
        expected_exe = str(self.root / 'tailscale.exe') if os.name == 'nt' else 'tailscale'
        resolver = mock.patch.object(settings, 'windows_tailscale_executable', return_value=expected_exe)
        resolver.start()
        self.addCleanup(resolver.stop)
        status = json.dumps({
            'BackendState': 'Running',
            'CurrentTailnet': {'MagicDNSSuffix': 'synthetic-tailnet.ts.net.'},
            'Self': {'Online': True, 'DNSName': 'node.synthetic-tailnet.ts.net.'},
        }).encode('ascii')
        probes, prompts = [], []

        def run(argv, **options):
            probes.append((argv, options))
            return subprocess.CompletedProcess(argv, 0, stdout=status)

        def prompt(text):
            prompts.append(text)
            return 'Synthetic-Tailnet.ts.net.\n'

        binding = settings.tailnet_digest('synthetic-tailnet.ts.net')
        self.assertEqual(settings.confirm_tailnet(prompt=prompt, run=run), binding)
        self.assertEqual(len(prompts), 1)
        self.assertNotIn('synthetic-tailnet', prompts[0])
        self.assertEqual(settings.confirm_tailnet('synthetic-tailnet.ts.net', prompt=prompt, run=run), binding)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(len(probes), 2)
        self.assertEqual(probes[0][0], [expected_exe, 'status', '--json'])
        self.assertEqual(probes[0][1]['timeout'], 4)
        self.assertEqual(probes[0][1]['stderr'], subprocess.DEVNULL)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            settings.confirm_tailnet('other-tailnet.ts.net', run=run)
        with self.assertRaisesRegex(ValueError, '--confirm-tailnet'):
            settings.confirm_tailnet(run=run)

        def timeout(argv, **options):
            raise subprocess.TimeoutExpired(argv, options['timeout'])

        with self.assertRaisesRegex(ValueError, 'bounded local Tailscale status'):
            settings.confirm_tailnet('synthetic-tailnet.ts.net', run=timeout)

    def test_full_plan_cannot_reach_legacy_settings_writers(self):
        choice = dict(self.data, profile='headless')
        plan = settings.make_plan(choice, 'a' * 64, self.root, 'J3', 'docker', 'windows', 'full',
                                  self.profiles, self.root / 'data', self.root / 'content')
        with self.assertRaisesRegex(ValueError, 'shared Docker lifecycle'):
            settings.install(plan, plan['digest'], self.target)
        with self.assertRaisesRegex(ValueError, 'shared Docker lifecycle'):
            settings.wsl_program(plan)
        path = self.root / 'synthetic-full-plan.json'
        path.write_bytes(settings.canonical(plan))
        result = subprocess.run([sys.executable, '-I', '-B', str(settings.__file__), 'install',
            '--plan', str(path), '--accept-plan', plan['digest']], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn('shared Docker lifecycle', result.stderr)
        self.assertFalse(result.stdout)
        self.assertFalse(self.target.parent.exists())
        self.assertFalse((self.root / 'data').exists())

    def test_loader_output_is_withheld_and_failures_are_mapped(self):
        script = self.root / 'synthetic-loader.py'
        script.write_text("import sys\nprint('SYNTHETIC_PROTECTED_OUTPUT_ONLY')\nprint('SYNTHETIC_PROTECTED_ERROR_ONLY',file=sys.stderr)\nsys.exit(int(sys.argv[1]))\n", encoding='ascii')
        for child_code, expected in [('0', 0), ('37', 1)]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = settings.run_loader([sys.executable, '-I', '-B', str(script), child_code], 0.02, 5)
            self.assertEqual(result, expected)
            self.assertIn('verified disc3 loader', output.getvalue())
            self.assertNotIn('SYNTHETIC_PROTECTED', output.getvalue())

    def test_loader_deadline_kills_own_child_tree(self):
        descendant = self.root / 'synthetic-descendant.py'
        sentinel = self.root / 'synthetic-descendant-finished.txt'
        ready = self.root / 'synthetic-child-started.txt'
        descendant.write_text("import pathlib,sys,time\ntime.sleep(0.7)\npathlib.Path(sys.argv[1]).write_text('synthetic completed',encoding='ascii')\n", encoding='ascii')
        script = self.root / 'synthetic-parent.py'
        script.write_text("import pathlib,subprocess,sys,time\nsubprocess.Popen([sys.executable,'-I','-B',sys.argv[1],sys.argv[2]])\npathlib.Path(sys.argv[3]).write_text('synthetic child started',encoding='ascii')\ntime.sleep(5)\n", encoding='ascii')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = settings.run_loader([sys.executable, '-I', '-B', str(script), str(descendant), str(sentinel), str(ready)], 0.04, 0.3)
        self.assertEqual(result, 11)
        self.assertTrue(ready.exists(), 'fixture child must have spawned before the deadline')
        self.assertIn('reached its deadline', output.getvalue())
        self.assertIn('Waiting', output.getvalue())
        time.sleep(0.8)
        self.assertFalse(sentinel.exists(), 'the descendant escaped child containment')

    def test_loader_refuses_nonfinite_deadlines_and_credential_arguments(self):
        for heartbeat, deadline in [(0, 1), (11, 1), (1, 0), (float('nan'), 1), (1, float('inf'))]:
            with self.assertRaises(ValueError): settings.run_loader(['synthetic'], heartbeat, deadline)
        with self.assertRaises(ValueError): settings.run_loader(['synthetic', '--token', 'SYNTHETIC_ONLY'])

    def test_loader_interruption_is_resumable(self):
        script = self.root / 'synthetic-sleeper.py'
        script.write_text('import time\ntime.sleep(3)\n', encoding='ascii')
        handlers = {}
        def register(signum, handler):
            handlers[signum] = handler
            return settings.signal.SIG_DFL
        def interrupt_on_wait(seconds):
            handlers[settings.signal.SIGINT](settings.signal.SIGINT, None)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(settings.signal, 'signal', register), patch.object(settings.time, 'sleep', interrupt_on_wait):
            result = settings.run_loader([sys.executable, '-I', '-B', str(script)], 0.04, 5)
        self.assertEqual(result, 11)
        self.assertIn('was interrupted', output.getvalue())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', required=True, type=Path)
    args = parser.parse_args()
    SCRATCH = args.scratch_root.resolve(strict=True)
    unittest.main(argv=[sys.argv[0]], verbosity=2)
