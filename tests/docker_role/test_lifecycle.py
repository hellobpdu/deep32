#!/usr/bin/env python3
"""Offline lifecycle regressions; every subprocess/platform boundary is a stand-in.

Run with --scratch TASK_OWNED_DIRECTORY outside the repository. Test material is
synthetic. No Docker, NVIDIA, Tailscale, systemd, network or socket is contacted.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shlex
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
KIT = REPO / 'src/gpu-pack'
SPEC = importlib.util.spec_from_file_location('tested_docker_role', KIT / 'forms/docker_role.py')
life = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(life)
SCRATCH = None
SYNTHETIC_SECRET = 'synthetic-test-only-credential-not-for-use'
IDENTITY = 'a' * 64
OLD_RELEASE = hashlib.sha256((IDENTITY + 'c' * 64).encode('ascii')).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='ascii')


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob('*')) if p.is_file()}


def settings():
    return dict(format='deep32-settings', format_version=1, role='forge',
                node_name='synthetic-node', user_name='forge', time_zone='Etc/UTC',
                keyboard='us', profile='headless', disk_encryption=False,
                transport='tailnet', publish='serve-https')


def synthetic_pins():
    return dict(runtime={'full_image_id': 'sha256:' + 'd' * 64,
                         'model_bridge_image_id': 'sha256:' + 'e' * 64},
                tei={'selected': 'ampere86'},
                full={'model_bridge': {'schema': 'deep32.model-bridge.v1'},
                      'minimum_data_free_bytes': 1024})


class DockerStandIn:
    def __init__(self):
        self.calls = []
        self.status = [dict(Service=name, State='running', Health='healthy',
                            Labels=SYNTHETIC_SECRET, Error=SYNTHETIC_SECRET)
                       for name in life.SERVICES]
        self.fail_on = None
        self.missing_images = set()
        self.archive_ids = {}

    def __call__(self, argv, reason, fix, timeout=120, emit=print):
        argv = [str(value) for value in argv]
        self.calls.append((argv, reason, fix, timeout))
        if argv[0] not in ('docker', 'tailscale', 'tailscale.exe', 'nvidia-smi', 'nvidia-smi.exe', 'openssl'):
            raise AssertionError('unexpected host programme: ' + argv[0])
        if self.fail_on and self.fail_on in argv:
            raise life.Refusal('The selected synthetic platform check failed; retry with verified media.', 11)
        if argv[0] in ('tailscale', 'tailscale.exe'):
            return json.dumps(dict(BackendState='Running', Self=dict(DNSName='synthetic-node.synthetic.ts.net.')))
        if argv[0] in ('nvidia-smi', 'nvidia-smi.exe'):
            return '8.6\n' if '--query-gpu=compute_cap' in argv else '999.0\n'
        if 'ps' in argv:
            return json.dumps(self.status)
        if argv[:3] == ['docker', 'image', 'inspect']:
            if argv[-1] in self.missing_images:
                raise life.Refusal('The synthetic image is absent; reload its verified archive.', 1)
            return json.dumps([dict(Id=argv[-1], Os='linux', Architecture='amd64')])
        if argv[:3] == ['docker', 'image', 'load']:
            self.missing_images.discard(self.archive_ids[argv[-1]])
        return ''


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-', dir=SCRATCH)
        self.work = Path(self.temp.name)
        self.root = self.work / 'install'
        self.root.mkdir()
        if life.os.name == 'nt':
            life.windows_security(self.root, directory=True)
        self.payload = self.work / 'gpu-payload'
        self.payload.mkdir()
        self.content = self.work / 'content'
        self.content.mkdir()
        write_json(self.content / 'MANIFEST.json', {'synthetic': True})
        self.settings_file = self.work / 'settings.json'
        write_json(self.settings_file, settings())
        self.kit = self.work / 'kit'
        (self.kit / 'autoinstall').mkdir(parents=True)
        (self.kit / 'autoinstall/render-settings.py').write_bytes((KIT / 'autoinstall/render-settings.py').read_bytes())
        (self.kit / 'release').mkdir()
        # These are labelled synthetic public test bytes, never a real key.
        (self.kit / 'release/release-key.gpg').write_bytes(b'synthetic-test-public-key-placeholder\n')
        (self.kit / 'release/release-key.fingerprint').write_text('1' * 40 + '\n', encoding='ascii')
        write_json(self.kit / 'pins.json', synthetic_pins())
        self.messages = []
        self.gateway = DockerStandIn()
        self.subject = life.Lifecycle(self.root, self.kit, run=self.gateway, emit=self.messages.append)
        # Only host path representation is translated for Windows filesystem
        # fixtures. The real POSIX grammar is tested independently below.
        self.translation = mock.patch.object(life, 'absolute_path', side_effect=lambda value, **kwargs: Path(value))
        self.translation.start()
        self.addCleanup(self.translation.stop)
        self.lock = mock.patch.object(life, 'installation_lock', side_effect=lambda root: contextlib.nullcontext())
        self.lock.start()
        self.addCleanup(self.lock.stop)
        self.addCleanup(self.temp.cleanup)
        # Fail closed if the implementation bypasses the injected gateway.
        self.addCleanup(mock.patch.stopall)
        mock.patch('subprocess.Popen', side_effect=AssertionError('real process forbidden')).start()
        mock.patch('subprocess.run', side_effect=AssertionError('real process forbidden')).start()
        mock.patch('socket.socket', side_effect=AssertionError('socket forbidden')).start()

    def record(self, platform='linux', stage='prepared'):
        data = dict(schema=life.SCHEMA, install_id='0123456789abcdef', platform=platform,
                    release=OLD_RELEASE, content=str(self.content), stage=stage, settings=settings())
        write_json(self.root / 'install.json', data)
        write_json(self.root / 'releases' / OLD_RELEASE / 'media.json',
                   dict(payload=str(self.payload), identity=IDENTITY))
        write_json(self.root / 'releases' / OLD_RELEASE / 'compose.json', {'services': {}})
        release_kit = self.root / 'releases' / OLD_RELEASE / 'kit'
        (release_kit / 'autoinstall').mkdir(parents=True, exist_ok=True)
        (release_kit / 'autoinstall/render-settings.py').write_bytes((self.kit / 'autoinstall/render-settings.py').read_bytes())
        write_json(release_kit / 'pins.json', synthetic_pins())
        (self.root / 'private').mkdir(exist_ok=True)
        (self.root / 'private' / 'synthetic-credential').write_text(SYNTHETIC_SECRET, encoding='ascii')
        (self.root / 'data').mkdir(exist_ok=True)
        (self.root / 'data' / 'active-generation').write_text('keep-generation', encoding='ascii')
        (self.root / 'config').mkdir(exist_ok=True)
        return data

    @contextlib.contextmanager
    def modules(self, identity=IDENTITY, unresolved=None):
        original = life.module
        def copy_kit(source, destination):
            destination.mkdir(parents=True, exist_ok=True)
            for name in ('pins.json', 'autoinstall/render-settings.py'):
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((source / name).read_bytes())
        provision = SimpleNamespace(verified_payload=lambda payload: identity,
                                    digest=lambda path: 'c' * 64,
                                    copy_kit=copy_kit)
        runtime = SimpleNamespace(prepare_config=mock.Mock(return_value={'services': {'engine': {'image': 'sha256:' + 'd' * 64}}}))
        self.runtime_prepare = runtime.prepare_config
        profile = SimpleNamespace(unresolved=lambda config: unresolved or [])
        enrol = SimpleNamespace(local_enrol=lambda *args: None)
        def load(name, path):
            if Path(path).name == 'provision.py':
                return provision
            if Path(path).name == 'runtime.py':
                return runtime
            return {'docker_release_profile': profile, 'docker_full_runtime': runtime,
                    'docker_local_enrol': enrol}.get(name) or original(name, path)
        with mock.patch.object(life, 'module', side_effect=load), mock.patch.object(self.subject, 'images', return_value={}) as images:
            self.image_prepare = images
            yield

    def pin_reads(self):
        original = life.read_json
        config = synthetic_pins()
        return mock.patch.object(life, 'read_json', side_effect=lambda path:
                                 config if Path(path).name == 'pins.json' else original(path))


class LifecycleTests(Fixtures):
    @unittest.skipUnless(life.os.name == 'nt', 'native Windows locking fixture')
    def test_native_windows_lifecycle_lock_rejects_concurrent_owner_then_releases(self):
        self.lock.stop()
        with life.installation_lock(self.root):
            with self.assertRaisesRegex(life.Refusal, 'Another lifecycle operation'):
                with life.installation_lock(self.root):
                    self.fail('concurrent lock acquired')
        with life.installation_lock(self.root):
            pass

    def test_all_lifecycle_actions_ignore_systemd_presence(self):
        # A real fixture PATH differs only by a systemctl executable which must
        # never be queried or run. The injected Docker boundary is fail-closed.
        bins = self.work / 'systemd-present'
        bins.mkdir()
        (bins / 'systemctl').write_text('#!/bin/sh\nexit 99\n', encoding='ascii')
        (bins / 'systemctl.exe').write_bytes(b'SYNTHETIC EXECUTABLE STAND-IN')
        empty = self.work / 'systemd-absent'
        empty.mkdir()
        for action in ('prepare', 'start', 'resume', 'stop', 'status', 'upgrade'):
            observations = []
            for location in (empty, bins):
                with self.subTest(action=action, systemd=location == bins):
                    self.record()
                    self.subject.kit = self.kit
                    self.gateway.calls.clear()
                    self.messages.clear()
                    with mock.patch.dict(life.os.environ, {'PATH': str(location)}), self.modules(), \
                            mock.patch.object(self.subject, 'host', return_value='full.synthetic.ts.net'):
                        if action in ('prepare', 'upgrade'):
                            self.subject.prepare(self.payload, self.content, self.settings_file, 'linux', action == 'upgrade')
                        else:
                            getattr(self.subject, action)()
                    observations.append(([row[0] for row in self.gateway.calls], self.subject.state(), list(self.messages)))
                    self.assertTrue(all(row[0][0] == 'docker' for row in self.gateway.calls))
            self.assertEqual(observations[0], observations[1])

    def test_windows_daemon_capacity_probe_is_offline_confined_and_fail_closed(self):
        self.subject.platform = 'windows'
        self.subject.storage_required = 1234
        images = {'FULL_IMAGE': 'sha256:' + 'd' * 64}
        for free in ('1234\n', '1233\n', 'unknown', SYNTHETIC_SECRET):
            self.gateway.calls.clear()
            def run(argv, *args, **kwargs):
                self.gateway.calls.append((argv, args, kwargs))
                return free
            self.subject.run = run
            with self.subTest(free=free):
                if free == '1234\n':
                    self.subject.daemon_storage(images, 'windows')
                else:
                    with self.assertRaises(life.Refusal) as refused:
                        self.subject.daemon_storage(images, 'windows')
                    self.assertNotIn(SYNTHETIC_SECRET, str(refused.exception))
            argv = self.gateway.calls[-1][0]
            self.assertEqual(argv[:3], ['docker', '--host', life.DESKTOP_PIPE])
            self.assertIn('--read-only', argv)
            self.assertEqual(argv[argv.index('--network') + 1], 'none')
            self.assertEqual(argv[argv.index('--pull') + 1], 'never')
            self.assertIn(images['FULL_IMAGE'], argv)
            self.assertFalse(any(flag in argv for flag in ('--privileged', '-v', '--volume', '--mount')))

    def test_windows_paths_reject_remote_reserved_and_interpolated_names(self):
        self.translation.stop()
        with mock.patch.object(life, 'Path', PureWindowsPath), mock.patch.object(life, 'safe_path'), \
                mock.patch.object(life, 'os', SimpleNamespace(name='nt')):
            self.assertEqual(life.absolute_path('C:/Deep32 data/install'), PureWindowsPath('C:/Deep32 data/install'))
            self.assertEqual(life.absolute_path('C:\\Deep32\\install'), PureWindowsPath('C:/Deep32/install'))
            for value in ('C:/', 'C:relative', '//server/share/path', 'C:/deep32/../private', 'C:/Deep32/$HOME',
                          'C:/Deep32/config:stream', 'C:/Deep32/CON', 'C:/Deep32/a.', 'C:/Deep32//a'):
                with self.subTest(value=value), self.assertRaises(life.Refusal):
                    life.absolute_path(value)

    def test_windows_backing_directory_is_recorded_and_cannot_silently_change(self):
        data = self.record(platform='windows')
        data['docker_storage_path'] = str(self.work / 'docker-backing')
        write_json(self.root / 'install.json', data)
        self.subject.state()
        self.assertEqual(self.subject.docker_storage_path, self.work / 'docker-backing')
        self.subject.docker_storage_path = self.work / 'other-backing'
        with self.assertRaisesRegex(life.Refusal, 'recorded Docker Desktop'):
            self.subject.state()

    def test_start_runs_models_import_then_engine_with_no_pull_or_build(self):
        self.record()
        with self.modules(), mock.patch.object(self.subject, 'host', return_value='full.synthetic.ts.net'):
            self.subject.start()
            self.image_prepare.assert_called_once()
        commands = [call[0] for call in self.gateway.calls]
        self.assertEqual(len(commands), 4)
        self.assertIn('config', commands[0])
        self.assertIn('model-bridge', commands[1])
        self.assertIn('importer', commands[2])
        self.assertEqual(commands[3][-2:], ['engine', 'gateway'])
        for command in commands[1:]:
            self.assertEqual(command[command.index('--pull') + 1], 'never')
        for command in (commands[1], commands[3]):
            self.assertIn('--no-build', command)
            self.assertIn('--force-recreate', command)
            self.assertIn('--wait-timeout', command)
        self.assertTrue(all(command[:4] == ['docker', 'compose', '--project-name', 'deep32-0123456789abcdef'] for command in commands))
        self.assertFalse(self.subject.status()['ready'])
        self.assertEqual(life.read_json(self.root / 'install.json')['stage'], 'local-ready-publication-required')

    def test_import_failure_keeps_old_state_and_does_not_start_engine(self):
        data = self.record(stage='previous-stable-stage')
        before = tree_bytes(self.root)
        self.gateway.fail_on = 'importer'
        with self.modules(), mock.patch.object(self.subject, 'host', return_value='full.synthetic.ts.net'), self.assertRaises(life.Refusal):
            self.subject.start()
        after = tree_bytes(self.root)
        for name, value in before.items():
            if not name.endswith('/compose.json'):
                self.assertEqual(after[name], value)
        self.assertFalse(any(command[-2:] == ['engine', 'gateway'] for command, *_ in self.gateway.calls))
        self.assertEqual(self.subject.state()['install_id'], data['install_id'])

    def test_changed_media_refuses_before_platform_or_container_call(self):
        self.record()
        before = tree_bytes(self.root)
        with self.modules(identity='f' * 64), mock.patch.object(self.subject, 'host') as host:
            with self.assertRaisesRegex(life.Refusal, 'media changed'):
                self.subject.start()
            host.assert_not_called()
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(tree_bytes(self.root), before)

    def test_stop_retains_content_private_state_and_images(self):
        self.record()
        preserved = {name: raw for name, raw in tree_bytes(self.root).items() if name != 'install.json'}
        self.subject.stop()
        self.assertEqual(self.subject.state()['stage'], 'stopped')
        self.assertEqual({name: raw for name, raw in tree_bytes(self.root).items() if name != 'install.json'}, preserved)
        command = self.gateway.calls[0][0]
        self.assertEqual(command[-3:], ['stop', '--timeout', '30'])
        self.assertFalse(any(word in command for word in ('down', 'prune', 'rm', '--volumes', 'systemctl')))
        self.assertNotIn(SYNTHETIC_SECRET, '\n'.join(self.messages))

    def test_status_is_read_only_and_redacts_untrusted_docker_fields(self):
        self.record()
        before = tree_bytes(self.root)
        result = self.subject.status()
        self.assertTrue(result['local_services_healthy'])
        self.assertFalse(result['ready'])
        self.assertEqual(tree_bytes(self.root), before)
        self.assertNotIn(SYNTHETIC_SECRET, '\n'.join(self.messages))
        self.assertEqual(self.gateway.calls[0][0][-4:], ['ps', '--all', '--format', 'json'])

    def test_status_marks_missing_or_unhealthy_services_unready(self):
        self.record()
        for rows in [self.gateway.status[:-1], [dict(Service=name, State='running', Health='starting') for name in life.SERVICES]]:
            self.gateway.status = rows
            result = self.subject.status()
            self.assertFalse(result['local_services_healthy'])
            self.assertFalse(result['ready'])

    def test_status_accepts_raw_running_services_only_when_authenticated_checks_pass(self):
        self.record()
        for row in self.gateway.status:
            if row['Service'] in ('embeddings', 'reranker', 'qdrant'):
                row.pop('Health')
        self.assertTrue(self.subject.status()['local_services_healthy'])
        door = next(row for row in self.gateway.status if row['Service'] == 'door')
        door['Health'] = 'unhealthy'
        self.assertFalse(self.subject.status()['local_services_healthy'])

    def test_status_supports_compose_line_delimited_json_without_relaying_values(self):
        self.record()
        text = '\n'.join(json.dumps(row) for row in self.gateway.status)
        with mock.patch.object(self.subject, 'compose', return_value=text):
            self.assertTrue(self.subject.status()['local_services_healthy'])
        self.assertNotIn(SYNTHETIC_SECRET, '\n'.join(self.messages))

    def test_status_rejects_malformed_shape_with_fixed_diagnostic(self):
        self.record()
        for text in ['not json ' + SYNTHETIC_SECRET, '["' + SYNTHETIC_SECRET + '"]', '[null]']:
            with self.subTest(text=text), mock.patch.object(self.subject, 'compose', return_value=text):
                with self.assertRaises(life.Refusal) as failure:
                    self.subject.status()
                self.assertNotIn(SYNTHETIC_SECRET, str(failure.exception))

    def test_upgrade_retains_previous_release_identity_and_data(self):
        old = self.record()
        preserved = tree_bytes(self.root)
        with self.modules(identity='f' * 64), self.pin_reads(), mock.patch.object(self.subject, 'host', return_value='full.synthetic.ts.net'), \
                mock.patch.object(self.subject, 'images', return_value={}), mock.patch.object(self.subject, 'start'):
            result = self.subject.prepare(self.payload, self.content, self.settings_file, 'linux', upgrade=True)
        self.assertEqual(result['install_id'], old['install_id'])
        self.assertNotEqual(result['release'], old['release'])
        self.assertEqual(life.read_json(self.root / 'previous.json'), old)
        for name, raw in preserved.items():
            if name != 'install.json':
                self.assertEqual((self.root / name).read_bytes(), raw)

    def test_owned_startup_hook_and_compose_bind_versioned_installed_kit(self):
        with self.modules(), self.pin_reads(), mock.patch.object(self.subject, 'host', return_value='synthetic-node.synthetic.ts.net'), \
                mock.patch.object(self.subject, 'images', return_value={}), mock.patch.object(self.subject, 'start'):
            data = self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
        release = self.root / 'releases' / data['release']
        self.assertEqual(self.runtime_prepare.call_args.args[0], release / 'kit')
        hook = (self.root / ('startup-hook-v1.ps1' if life.os.name == 'nt' else 'startup-hook-v1.sh')).read_text(encoding='ascii')
        if life.os.name == 'nt':
            self.assertIn(str(release / 'kit/forms/docker_role.py'), hook)
            self.assertIn('resume --platform windows --data-root', hook)
        else:
            self.assertEqual(shlex.split(hook.splitlines()[2]), ['exec', 'python3', '-I', '-B',
                str(release / 'kit/forms/docker_role.py'), 'resume', '--data-root', str(self.root)])
        self.assertNotIn(SYNTHETIC_SECRET, hook)
        request = life.read_json(self.root / 'config/publication-request.json')
        self.assertEqual({name: value for name, value in request.items() if name != 'routes'}, {
            'schema': 'deep32.host-publication-request/1', 'install_id': data['install_id'],
            'dns_name': 'synthetic-node.synthetic.ts.net', 'consent': 'required',
            'conflicts': 'refuse', 'unknown_paths': 'deny'})
        self.assertEqual(request['routes'], [
            {'https_port': port, 'path': path, 'exact': True,
             'upstream': 'http://127.0.0.1:' + str(backend), 'preserve_path': True}
            for port, backend, path in [(443, 8766, '/mcp'), (443, 8081, '/v1/embeddings'),
                (443, 8081, '/info'), (443, 8081, '/health'), (8443, 8082, '/v1/rerank'),
                (8443, 8082, '/info'), (8443, 8082, '/health')]])
        self.assertFalse(self.subject.status()['ready'])
        self.assertNotIn(SYNTHETIC_SECRET, json.dumps(request))

    def test_startup_resume_keeps_deliberately_stopped_installation_stopped(self):
        self.record(stage='stopped')
        before = tree_bytes(self.root)
        with mock.patch.object(self.subject, 'start') as start:
            self.subject.resume()
            start.assert_not_called()
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(tree_bytes(self.root), before)

    def test_startup_resume_restarts_prepared_state_without_replacing_identity(self):
        data = self.record(stage='prepared')
        before = tree_bytes(self.root)
        with mock.patch.object(self.subject, 'start') as start:
            self.subject.resume()
            start.assert_called_once_with(data)
        self.assertEqual(tree_bytes(self.root), before)

    def test_different_media_requires_explicit_upgrade(self):
        self.record()
        before = tree_bytes(self.root)
        with self.modules(identity='f' * 64), self.pin_reads(), mock.patch.object(self.subject, 'host'), \
                mock.patch.object(self.subject, 'images', return_value={}), self.assertRaisesRegex(life.Refusal, 'use upgrade'):
            self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
        self.assertEqual(tree_bytes(self.root), before)

    def test_wrong_platform_upgrade_does_not_touch_existing_installation(self):
        self.record()
        before = tree_bytes(self.root)
        with self.modules(), self.pin_reads(), mock.patch.object(self.subject, 'host') as host:
            with self.assertRaisesRegex(life.Refusal, 'recorded platform'):
                self.subject.prepare(self.payload, self.content, self.settings_file, 'windows', upgrade=True)
            host.assert_not_called()
        self.assertEqual(tree_bytes(self.root), before)

    def test_unresolved_media_refuses_before_host_or_writes(self):
        before = tree_bytes(self.root)
        with self.modules(unresolved=['runtime.cuda_minimum_driver']), self.pin_reads(), mock.patch.object(self.subject, 'host') as host:
            with self.assertRaisesRegex(life.Refusal, 'all input pins resolved'):
                self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
            host.assert_not_called()
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(tree_bytes(self.root), before)

    def test_missing_disc2_manifest_refuses_before_settings_or_writes(self):
        (self.content / 'MANIFEST.json').unlink()
        before = tree_bytes(self.root)
        with mock.patch.object(self.subject, 'settings') as validate, self.assertRaisesRegex(life.Refusal, 'disc 2'):
            self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
        validate.assert_not_called()
        self.assertEqual(tree_bytes(self.root), before)

    def test_missing_public_release_verifier_refuses_without_host_or_writes(self):
        (self.kit / 'release/release-key.gpg').unlink()
        before = tree_bytes(self.root)
        with self.modules(), self.pin_reads(), mock.patch.object(self.subject, 'host') as host:
            with self.assertRaisesRegex(life.Refusal, 'public content verification key'):
                self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
            host.assert_not_called()
        self.assertEqual(tree_bytes(self.root), before)

    def test_invalid_settings_never_creates_installation_or_operation_lock(self):
        self.root.rmdir()
        write_json(self.settings_file, dict(settings(), token=SYNTHETIC_SECRET))
        with mock.patch.object(life, 'private_directory') as private, \
                mock.patch.object(life, 'installation_lock') as locked:
            with self.assertRaises(ValueError):
                self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
            private.assert_not_called()
            locked.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_equal_data_payload_paths_refuse_before_host_or_image_loading(self):
        before = tree_bytes(self.root)
        with self.modules(), self.pin_reads(), mock.patch.object(self.subject, 'host') as host, \
                mock.patch.object(self.subject, 'images', return_value={}) as images:
            with self.assertRaises(life.Refusal):
                self.subject.prepare(self.root, self.content, self.settings_file, 'linux')
            host.assert_not_called()
            images.assert_not_called()
        self.assertEqual(tree_bytes(self.root), before)

    def test_equal_data_content_paths_refuse_before_host_or_image_loading(self):
        write_json(self.root / 'MANIFEST.json', {'synthetic': True})
        before = tree_bytes(self.root)
        with self.modules(), self.pin_reads(), mock.patch.object(self.subject, 'host') as host, \
                mock.patch.object(self.subject, 'images', return_value={}) as images:
            with self.assertRaises(life.Refusal):
                self.subject.prepare(self.payload, self.root, self.settings_file, 'linux')
            host.assert_not_called()
            images.assert_not_called()
        self.assertEqual(tree_bytes(self.root), before)

    def test_kit_inside_data_root_refuses_before_settings_or_host(self):
        self.subject.kit = self.root / 'nested-kit'
        with mock.patch.object(self.subject, 'settings') as validate, mock.patch.object(self.subject, 'host') as host:
            with self.assertRaises(life.Refusal):
                self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
            validate.assert_not_called()
            host.assert_not_called()

    def test_exact_owned_release_kit_can_resume_path_validation(self):
        self.subject.kit = self.root / 'releases' / OLD_RELEASE / 'kit'
        with mock.patch.object(self.subject, 'settings', side_effect=ValueError('synthetic stop after safe path gate')) as validate:
            with self.assertRaisesRegex(ValueError, 'synthetic stop after safe path gate'):
                self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
            validate.assert_called_once()

    def test_unowned_nonempty_data_root_refuses_without_changes(self):
        (self.root / 'unrelated-data').write_text('retain unrelated data', encoding='ascii')
        before = tree_bytes(self.root)
        with mock.patch.object(self.subject, 'host') as host:
            with self.assertRaisesRegex(life.Refusal, 'empty dedicated data'):
                self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
            host.assert_not_called()
        self.assertEqual(tree_bytes(self.root), before)

    def test_corrupt_partial_owner_marker_refuses_before_host_or_changes(self):
        write_json(self.root / 'owner.json', {'schema': life.SCHEMA, 'install_id': '0' * 16})
        before = tree_bytes(self.root)
        with self.modules(), self.pin_reads(), mock.patch.object(self.subject, 'host') as host:
            with self.assertRaisesRegex(life.Refusal, 'owned installation marker'):
                self.subject.prepare(self.payload, self.content, self.settings_file, 'linux')
            host.assert_not_called()
        self.assertEqual(tree_bytes(self.root), before)

    def test_host_uses_injected_nvidia_and_tailscale_gateways_without_systemd(self):
        def check(form, payload, kit, run, **kwargs):
            self.assertEqual(form, 'docker')
            self.assertEqual(kwargs['storage_target'], self.root)
            self.assertEqual(kwargs['extra_storage_bytes'], 1024)
            self.assertEqual(run(['nvidia-smi', '--query-gpu=driver_version']).stdout, '999.0\n')
        original = life.module
        with mock.patch.object(life, 'module', side_effect=lambda name, path:
                               SimpleNamespace(check=check) if name == 'docker_host_requirements' else original(name, path)):
            self.subject.host(self.payload, 'linux')
        self.assertEqual([args[0] for args, *_ in self.gateway.calls][:2], ['nvidia-smi', 'tailscale'])
        self.assertTrue(all(args[0] != 'systemctl' for args, *_ in self.gateway.calls))

    def test_host_signed_out_is_actionable_and_does_not_leak_child_data(self):
        original = life.module
        self.subject.run = lambda *args, **kwargs: json.dumps(dict(BackendState='NeedsLogin', AuthURL=SYNTHETIC_SECRET))
        with mock.patch.object(life, 'module', side_effect=lambda name, path:
                               SimpleNamespace(check=lambda *args, **kwargs: None) if name == 'docker_host_requirements' else original(name, path)):
            with self.assertRaises(life.Refusal) as failure:
                self.subject.host(self.payload, 'linux')
        self.assertIn('sign-in', str(failure.exception))
        self.assertNotIn(SYNTHETIC_SECRET, str(failure.exception))

    def test_c7_unknown_secret_and_incompatible_modes_are_rejected(self):
        for extra in [{'token': SYNTHETIC_SECRET}, {'pairing_code': SYNTHETIC_SECRET},
                      {'role': 'headless'}, {'profile': 'gpu'}, {'transport': 'loopback'}, {'publish': 'tailnet-plain'}]:
            with self.subTest(extra=extra):
                value = dict(settings(), **extra)
                with self.assertRaises((ValueError, life.Refusal)) as failure:
                    self.subject.settings(value)
                self.assertNotIn(SYNTHETIC_SECRET, str(failure.exception))

    def test_json_duplicate_keys_and_unknown_persistent_fields_refuse(self):
        self.settings_file.write_text('{"format":1,"format":2}', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            life.read_json(self.settings_file)
        data = self.record()
        data['token'] = SYNTHETIC_SECRET
        write_json(self.root / 'install.json', data)
        with self.assertRaises(life.Refusal) as failure:
            self.subject.state()
        self.assertNotIn(SYNTHETIC_SECRET, str(failure.exception))

    def test_windows_private_acl_rejects_broad_null_and_other_owner(self):
        security = life.module('tested_windows_acl', KIT / 'forms/host_security.py')
        owner = 'S-1-5-21-1000'
        for dacl in ('D:', 'D:P(A;;FA;;;WD)', 'D:P(A;;FA;;;' + owner + ')',
                     'D:P(A;;FA;;;' + owner + ')(A;;FA;;;SY)(A;;FR;;;BU)'):
            with self.subTest(dacl=dacl), self.assertRaises(ValueError):
                security.validate_acl('O:' + owner + dacl, owner)
        with self.assertRaises(ValueError):
            security.validate_acl('O:BA' + 'D:P(A;;FA;;;' + owner + ')(A;;FA;;;SY)', owner)
        security.validate_acl('O:' + owner + 'D:P(A;;FA;;;' + owner + ')(A;;FA;;;SY)', owner)
        security.validate_acl('O:' + owner + 'D:P(A;;FA;;;' + owner + ')(A;;FA;;;SY)(A;;FA;;;BA)', owner)
        security.validate_acl('O:' + owner + 'D:AI(A;ID;FA;;;' + owner + ')(A;ID;FA;;;SY)', owner)
        security.validate_acl('O:' + owner + 'D:AI(A;OICIID;FA;;;' + owner + ')(A;OICIID;FA;;;SY)', owner, True)
        security.validate_acl('O:' + owner + 'D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)', owner, True)

    def test_private_filesystem_that_ignores_mode_change_is_refused(self):
        path = mock.Mock()
        path.stat.return_value = SimpleNamespace(st_uid=10032, st_mode=0o40777)
        with mock.patch.object(life, 'safe_path'), mock.patch.object(life, 'os', SimpleNamespace(name='posix', geteuid=lambda: 10032)):
            with self.assertRaisesRegex(life.Refusal, 'owner-only permissions'):
                life.private_directory(path)

    def test_application_paths_reject_shell_interpolation_and_parent_traversal(self):
        # Exercise the production grammar with Linux path semantics on Windows.
        self.translation.stop()
        with mock.patch.object(life, 'Path', PurePosixPath), mock.patch.object(life, 'safe_path'), mock.patch.object(life, 'os', SimpleNamespace(name='posix')):
            self.assertEqual(str(life.absolute_path('/srv/deep32-test')), '/srv/deep32-test')
            for path in ['/', 'relative', '/srv/../private', '/srv/$HOME', '/srv/path:other', '/srv/a\\b', '/srv/a\nb']:
                with self.subTest(path=path), self.assertRaises(life.Refusal):
                    life.absolute_path(path)


class ImageTests(Fixtures):
    def image_inputs(self, reject_role=None):
        roles = ('tei-ampere86', 'reranker', 'door', 'qdrant', 'full', 'model_bridge')
        rows = [dict(role=role, path='images/' + role + '.tar', config_digest='sha256:' + format(i + 1, '064x'))
                for i, role in enumerate(roles)]
        self.gateway.archive_ids = {str(self.payload / row['path']): row['config_digest'] for row in rows}
        write_json(self.payload / 'images/index.json', rows)
        checked = []
        def verify(config, row, binding):
            # Reject a later archive to prove no earlier archive was loaded.
            self.assertFalse(any(command[:3] == ['docker', 'image', 'load'] for command, *_ in self.gateway.calls))
            checked.append(row['role'])
            if row['role'] == reject_role:
                raise ValueError('synthetic corrupt signed archive')
            return row
        inputs = SimpleNamespace(CAPABILITIES={'8.6': 'ampere86', '7.5': 'turing'}, verified_image=verify)
        original = life.module
        def load(name, path):
            if name == 'docker_installed_inputs':
                return inputs
            if name == 'docker_image_binding':
                return SimpleNamespace()
            return original(name, path)
        return rows, checked, mock.patch.object(life, 'module', side_effect=load)

    def test_all_archives_verified_before_loading_selected_immutable_images(self):
        rows, checked, loading = self.image_inputs()
        self.gateway.missing_images = {row['config_digest'] for row in rows}
        with loading:
            images = self.subject.images(self.payload, {'tei': {'selected': 'ampere86'}})
        self.assertEqual(checked, [row['role'] for row in rows])
        self.assertEqual(set(images), {'TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE', 'QDRANT_IMAGE', 'FULL_IMAGE', 'MODEL_BRIDGE_IMAGE'})
        commands = [command for command, *_ in self.gateway.calls]
        self.assertEqual(commands[0], ['nvidia-smi', '--query-gpu=compute_cap', '--format=csv,noheader'])
        self.assertEqual(sum(command[:3] == ['docker', 'image', 'load'] for command in commands), 6)
        self.assertFalse(any('pull' in command or 'build' in command for command in commands))

    def test_existing_exact_image_ids_are_reused_without_archive_load(self):
        rows, checked, loading = self.image_inputs()
        with loading:
            images = self.subject.images(self.payload, {'tei': {'selected': 'ampere86'}})
        self.assertEqual(len(images), 6)
        self.assertEqual(checked, [row['role'] for row in rows])
        self.assertFalse(any(command[:3] == ['docker', 'image', 'load'] for command, *_ in self.gateway.calls))

    def test_only_missing_verified_image_is_loaded_again(self):
        rows, checked, loading = self.image_inputs()
        self.gateway.missing_images = {rows[-1]['config_digest']}
        with loading:
            self.subject.images(self.payload, {'tei': {'selected': 'ampere86'}})
        loads = [command for command, *_ in self.gateway.calls if command[:3] == ['docker', 'image', 'load']]
        self.assertEqual(loads, [['docker', 'image', 'load', '--input', str(self.payload / rows[-1]['path'])]])

    def test_later_corrupt_archive_prevents_every_image_load(self):
        rows, checked, loading = self.image_inputs(reject_role='model_bridge')
        with loading, self.assertRaises(ValueError):
            self.subject.images(self.payload, {'tei': {'selected': 'ampere86'}})
        self.assertEqual(checked, [row['role'] for row in rows])
        self.assertFalse(any(command[0] == 'docker' for command, *_ in self.gateway.calls))

    def test_missing_full_image_role_refuses_before_load(self):
        rows, checked, loading = self.image_inputs()
        write_json(self.payload / 'images/index.json', rows[:-1])
        with loading, self.assertRaises(life.Refusal):
            self.subject.images(self.payload, {'tei': {'selected': 'ampere86'}})
        self.assertEqual(checked, [])
        self.assertFalse(any(command[0] == 'docker' for command, *_ in self.gateway.calls))

    def test_mixed_unknown_and_override_mismatch_gpu_classes_refuse_before_load(self):
        for capabilities, selected in [('8.6\n7.5\n', 'ampere86'), ('99.0\n', 'ampere86'), ('8.6\n', 'turing')]:
            with self.subTest(capabilities=capabilities, selected=selected):
                self.gateway.calls.clear()
                rows, checked, loading = self.image_inputs()
                normal = self.subject.run
                self.subject.run = lambda argv, *args, **kwargs: capabilities if argv[0] in ('nvidia-smi', 'nvidia-smi.exe') else normal(argv, *args, **kwargs)
                try:
                    with loading, self.assertRaises(life.Refusal):
                        self.subject.images(self.payload, {'tei': {'selected': selected}})
                finally:
                    self.subject.run = normal
                self.assertEqual(checked, [])
                self.assertFalse(any(command[0] == 'docker' for command, *_ in self.gateway.calls))

    def test_loaded_identity_mismatch_never_enters_runtime_configuration(self):
        rows, checked, loading = self.image_inputs()
        normal = self.subject.run
        def wrong(argv, *args, **kwargs):
            if argv[:3] == ['docker', 'image', 'inspect']:
                return json.dumps([dict(Id='sha256:' + 'f' * 64, Os='linux', Architecture='amd64')])
            return normal(argv, *args, **kwargs)
        self.subject.run = wrong
        with loading, self.assertRaisesRegex(life.Refusal, 'differs from its signed identity'):
            self.subject.images(self.payload, {'tei': {'selected': 'ampere86'}})


class DispatcherTests(Fixtures):
    def test_explicit_full_passes_windows_host_choices(self):
        provision = life.module('tested_provision_dispatch', KIT / 'forms/provision.py')
        adapter = SimpleNamespace(main=mock.Mock())
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda value: None))
        before = tree_bytes(self.work)
        with mock.patch.object(provision.importlib.util, 'spec_from_file_location', return_value=spec), \
                mock.patch.object(provision.importlib.util, 'module_from_spec', return_value=adapter):
            provision.main(['--form', 'docker', '--docker-role', 'full', '--kit', str(KIT), '--platform', 'windows',
                            '--payload', str(self.payload), '--content', str(self.content),
                            '--settings', str(self.settings_file), '--data-root', str(self.root),
                            '--docker-storage-path', str(self.work / 'desktop-store')])
        args = adapter.main.call_args.args[0]
        self.assertEqual(args[:5], ['prepare', '--kit', str(KIT), '--platform', 'windows'])
        self.assertEqual(args[args.index('--docker-storage-path') + 1], str(self.work / 'desktop-store'))
        self.assertEqual(args[args.index('--data-root') + 1], str(self.root))
        self.assertEqual(tree_bytes(self.work), before)

    def test_explicit_full_lifecycle_status_needs_no_payload(self):
        provision = life.module('tested_provision_status_dispatch', KIT / 'forms/provision.py')
        adapter = SimpleNamespace(main=mock.Mock())
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda value: None))
        with mock.patch.object(provision.importlib.util, 'spec_from_file_location', return_value=spec), \
                mock.patch.object(provision.importlib.util, 'module_from_spec', return_value=adapter):
            provision.main(['--form', 'docker', '--docker-role', 'full', '--kit', str(KIT), '--platform', 'linux', '--action', 'status'])
        self.assertEqual(adapter.main.call_args.args[0], ['status', '--kit', str(KIT), '--platform', 'linux'])


@unittest.skipUnless(os.environ.get('D32_GIT_BASH'), 'set D32_GIT_BASH to the full Git Bash executable path')
class LoaderTests(unittest.TestCase):
    def test_shell_loader_forwards_all_actions_and_explicit_forge_resume(self):
        shell = Path(os.environ['D32_GIT_BASH'])
        self.assertTrue(shell.is_absolute())
        with tempfile.TemporaryDirectory(prefix='synthetic-loader-', dir=SCRATCH) as temporary:
            work = Path(temporary)
            capture = work / 'argv.txt'
            for systemd in (False, True):
                bindir = work / ('systemd-present' if systemd else 'systemd-absent')
                bindir.mkdir()
                (bindir / 'id').write_text('#!/bin/sh\nprintf "0\\n"\n', encoding='ascii')
                (bindir / 'python3').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$D32_CAPTURE"\n', encoding='ascii')
                if systemd:
                    (bindir / 'systemctl').write_text('#!/bin/sh\nexit 99\n', encoding='ascii')
                for path in bindir.iterdir():
                    path.chmod(0o755)
                git_bindir = '/' + bindir.drive[0].lower() + bindir.as_posix()[2:] if bindir.drive else bindir.as_posix()
                environment = dict(os.environ, D32_BINDIR=git_bindir,
                                   D32_CAPTURE=capture.as_posix(), MSYS_NO_PATHCONV='1')
                for action in ('start', 'stop', 'status', 'resume', 'prepare', 'upgrade'):
                    completed = subprocess.run([str(shell), '--noprofile', '--norc', '-c',
                        'export PATH="$D32_BINDIR:/usr/bin:/bin"; exec /bin/bash "$@"', 'synthetic-loader',
                        (KIT / 'forms/load-docker.sh').as_posix(), '--' + action, '--data-root', '/opt/synthetic'],
                        env=environment, capture_output=True, text=True, timeout=15)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    argv = capture.read_text(encoding='ascii').splitlines()
                    self.assertEqual(argv[-4:], ['--action', action, '--data-root', '/opt/synthetic'])
                    self.assertEqual(argv[2:4], ['--form', 'docker'])
                completed = subprocess.run([str(shell), '--noprofile', '--norc', '-c',
                    'export PATH="$D32_BINDIR:/usr/bin:/bin"; exec /bin/bash "$@"', 'synthetic-loader',
                    (KIT / 'forms/load-docker.sh').as_posix(), '--resume', '--docker-role', 'forge'],
                    env=environment, capture_output=True, text=True, timeout=15)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                argv = capture.read_text(encoding='ascii').splitlines()
                self.assertEqual(argv[-4:], ['--action', 'resume', '--docker-role', 'forge'])


class FirstbootTests(Fixtures):
    def test_docker_uses_existing_daemon_while_wsl_retains_systemd(self):
        class Status:
            def __init__(self):
                self.state = {}
            def update(self, stage, state, **kwargs):
                self.state = dict(stage=stage, state=state, **kwargs)
        specification = importlib.util.spec_from_file_location('tested_firstboot_lifecycle', KIT / 'firstboot/firstboot.py')
        firstboot = importlib.util.module_from_spec(specification)
        with mock.patch.dict(sys.modules, {'pwd': SimpleNamespace(),
                'status': SimpleNamespace(Status=Status, command=mock.Mock())}):
            specification.loader.exec_module(firstboot)
        form_file = self.work / 'form'
        original_path = Path
        def paths(value):
            return form_file if str(value) == '/etc/deep32-forge/form' else original_path(value)
        inputs = SimpleNamespace(pins=lambda: {}, verify_payload=lambda *args, **kwargs: IDENTITY,
                                 load_images=lambda *args: None)
        specification = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda value: value.__dict__.update(inputs.__dict__)))
        for form in ('docker', 'wsl'):
            with self.subTest(form=form):
                form_file.write_text(form, encoding='ascii')
                commands = []
                def run(argv, **kwargs):
                    commands.append(list(argv))
                    return SimpleNamespace(returncode=0)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(firstboot, 'Path', side_effect=paths))
                    stack.enter_context(mock.patch.object(firstboot, 'settings', return_value=settings()))
                    stack.enter_context(mock.patch.object(firstboot, 'make_identities', return_value=('synthetic-token-path', 'synthetic-public-path')))
                    stack.enter_context(mock.patch.object(firstboot, 'local_account'))
                    stack.enter_context(mock.patch.object(firstboot, 'signin', return_value='synthetic-node'))
                    stack.enter_context(mock.patch.object(firstboot, 'start_models'))
                    stack.enter_context(mock.patch.object(firstboot, 'pair'))
                    stack.enter_context(mock.patch.object(firstboot, 'firstboot_lock', return_value=contextlib.nullcontext()))
                    stack.enter_context(mock.patch.object(firstboot, 'command', side_effect=lambda argv, status, seconds: run(argv)))
                    stack.enter_context(mock.patch.object(firstboot, 'run', side_effect=lambda *args, **kwargs: run(args, **kwargs)))
                    stack.enter_context(mock.patch.object(firstboot.subprocess, 'run', side_effect=run))
                    stack.enter_context(mock.patch.object(firstboot.os, 'umask'))
                    stack.enter_context(mock.patch.object(firstboot.importlib.util, 'spec_from_file_location', return_value=specification))
                    stack.enter_context(mock.patch.object(firstboot.importlib.util, 'module_from_spec', return_value=SimpleNamespace()))
                    stack.enter_context(mock.patch.object(sys, 'argv', ['firstboot.py']))
                    firstboot.main()
                systemd = [command for command in commands if command[0] == 'systemctl']
                if form == 'docker':
                    self.assertEqual(systemd, [])
                    self.assertIn(['docker', 'info'], commands)
                    self.assertTrue(any(command[:2] == ['docker', 'compose'] for command in commands))
                else:
                    self.assertEqual(systemd, [['systemctl', 'start', 'docker.service']])


class CommandTests(Fixtures):
    def fake_process(self, returns, returncode=0):
        process = mock.Mock()
        process.__enter__ = mock.Mock(return_value=process)
        process.__exit__ = mock.Mock(return_value=False)
        process.returncode = returncode
        process.communicate.side_effect = returns
        return process

    def test_bounded_wait_heartbeats_then_returns_without_child_stderr(self):
        process = self.fake_process([subprocess.TimeoutExpired('synthetic', 10), ('done', SYNTHETIC_SECRET)])
        with mock.patch.object(life.subprocess, 'Popen', return_value=process), \
                mock.patch.object(life.time, 'monotonic', side_effect=[0, 0, 10]):
            result = life.command(['docker', 'version'], 'the synthetic daemon', 'restore it and retry.', timeout=25, emit=self.messages.append)
        self.assertEqual(result, 'done')
        self.assertEqual(len(self.messages), 2)
        self.assertTrue(all('Waiting for' in value and 'retry' in value for value in self.messages))
        self.assertEqual([call.kwargs['timeout'] for call in process.communicate.call_args_list], [10, 10])
        self.assertNotIn(SYNTHETIC_SECRET, '\n'.join(self.messages))

    def test_timeout_kills_owned_process_and_redacts_output(self):
        process = self.fake_process([subprocess.TimeoutExpired('synthetic', 10), (SYNTHETIC_SECRET, SYNTHETIC_SECRET)], returncode=None)
        with mock.patch.object(life.subprocess, 'Popen', return_value=process), \
                mock.patch.object(life.time, 'monotonic', side_effect=[0, 0, 26]):
            with self.assertRaises(life.Refusal) as failure:
                life.command(['docker', 'compose'], 'the synthetic daemon', 'restore it and retry.', timeout=25, emit=self.messages.append)
        self.assertEqual(failure.exception.code, 11)
        process.kill.assert_called_once_with()
        self.assertNotIn(SYNTHETIC_SECRET, str(failure.exception) + '\n'.join(self.messages))

    def test_timeout_cleanup_bounds_descendant_pipe_wait_and_owned_group_kill(self):
        process = self.fake_process([subprocess.TimeoutExpired('synthetic', 10),
                                     subprocess.TimeoutExpired('synthetic', 5)], returncode=None)
        process.pid = 12345
        process.wait.side_effect = subprocess.TimeoutExpired('synthetic', 5)
        group = mock.Mock()
        with mock.patch.object(life.subprocess, 'Popen', return_value=process) as opened, \
                mock.patch.object(life.time, 'monotonic', side_effect=[0, 0, 26]), \
                mock.patch.object(life, 'os', SimpleNamespace(name='posix', killpg=group)), \
                mock.patch.object(life, 'signal', SimpleNamespace(SIGKILL=9)):
            with self.assertRaises(life.Refusal) as failure:
                life.command(['docker', 'compose'], 'the synthetic daemon', 'restore it and retry.', timeout=25, emit=self.messages.append)
        self.assertEqual(failure.exception.code, 11)
        self.assertTrue(opened.call_args.kwargs['start_new_session'])
        group.assert_called_once_with(12345, 9)
        self.assertTrue(all(call.kwargs.get('timeout') is not None for call in process.communicate.call_args_list))
        process.wait.assert_called_once_with(timeout=5)
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()
        self.assertNotIn(SYNTHETIC_SECRET, '\n'.join(self.messages))

    def test_failed_child_and_missing_command_have_sanitised_fixes(self):
        process = self.fake_process([(SYNTHETIC_SECRET, SYNTHETIC_SECRET)], returncode=9)
        for failure in [None, OSError(SYNTHETIC_SECRET)]:
            with self.subTest(failure=failure), mock.patch.object(life.subprocess, 'Popen', return_value=process, side_effect=failure):
                with self.assertRaises(life.Refusal) as refused:
                    life.command(['docker', 'version'], 'the synthetic daemon', 'restore it and retry.', emit=self.messages.append)
                self.assertIn('retry', str(refused.exception))
                self.assertNotIn(SYNTHETIC_SECRET, str(refused.exception))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', required=True, type=Path)
    args, rest = parser.parse_known_args()
    scratch = args.scratch.resolve()
    if scratch == Path(scratch.anchor) or scratch == REPO or REPO in scratch.parents:
        parser.error('--scratch must be a dedicated task directory outside the repository')
    scratch.mkdir(parents=True, exist_ok=True)
    SCRATCH = str(scratch)
    unittest.main(argv=[sys.argv[0], *rest])
