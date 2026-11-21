"""Offline B-01 fixtures; pass --scratch or D32_RUNTIME_TEST_SCRATCH.

All credentials are labelled synthetic test material generated inside that task folder.
No platform executable, socket, model, service or network is used.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
SCRATCH = os.environ.get('D32_RUNTIME_TEST_SCRATCH')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


renderer = module('runtime_c7', KIT / 'autoinstall/render-settings.py')
host = module('runtime_host', KIT / 'forms/host-requirements.py')
provision = module('runtime_provision', KIT / 'forms/provision.py')
thin = module('runtime_thin', KIT / 'build/thin_forms.py')
channel = module('runtime_channel', KIT / 'firstboot/status.py')
with mock.patch.dict(sys.modules, {'pwd': types.ModuleType('pwd'), 'status': channel}):
    boot = module('runtime_boot', KIT / 'firstboot/firstboot.py')


def choices():
    return dict(format='deep32-settings', format_version=1, role='forge', node_name='forge-test',
                user_name='operator', time_zone='Etc/UTC', keyboard='gb', profile='two-machines',
                disk_encryption=False, transport='tailnet', publish='serve-https')


def signed_in(**extra):
    value = dict(BackendState='Running', Self=dict(HostName='synthetic-forge', Online=True))
    value.update(extra)
    return subprocess.CompletedProcess([], 0, json.dumps(value), '')


class Status:
    def __init__(self):
        self.state, self.updates, self.repeats = {}, [], 0

    def update(self, stage, state, **values):
        self.state = dict(stage=stage, state=state, **values)
        self.updates.append(self.state)

    def repeat(self):
        self.repeats += 1


@unittest.skipUnless(SCRATCH, 'Set D32_RUNTIME_TEST_SCRATCH to the task fixture folder')
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='synthetic-runtime-', dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.work = Path(scratch.name)
        self.root = self.work / 'target'
        self.settings = self.root / 'etc/deep32-forge/settings.json'
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps(choices()), encoding='ascii')
        self.settings.chmod(0o600)
        self.kit = self.work / 'kit'
        for name, source in thin.runtime_members(KIT).items():
            destination = self.kit / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        self.payload = self.work / 'media/payload'
        self.payload.mkdir(parents=True)
        (self.payload / 'synthetic.txt').write_bytes(b'synthetic verified payload\n')
        self.inventory()
        self.calls = []
        for patcher in (mock.patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')),
                        mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('process forbidden')),
                        mock.patch.object(subprocess, 'run', side_effect=AssertionError('process forbidden'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def inventory(self):
        rows = [dict(path=path.relative_to(self.payload).as_posix(), size=path.stat().st_size,
                     sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                for path in sorted(self.payload.rglob('*')) if path.is_file()]
        (self.payload.parent / 'PAYLOAD.json').write_text(json.dumps(rows), encoding='ascii')
        (self.payload.parent / 'PAYLOAD.SHA256').write_text(
            ''.join(row['sha256'] + '  ' + row['path'] + '\n' for row in rows), encoding='ascii')

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def run_stub(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '', '')

    def install(self, form='docker', **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return provision.provision(form, self.payload, self.kit, self.root, self.run_stub,
                                       host_check=lambda: None, **kwargs)

    def test_valid_shared_settings(self):
        self.assertEqual(renderer.load(self.settings), choices())

    def test_clone_settings_handoff_and_runtime_journals_coexist(self):
        handoff = module('runtime_settings_handoff', KIT.parent / 'release-tools/deep32_settings.py')
        plan = handoff.make_plan(choices(), 'a' * 64, self.work / 'downloads',
                                 'J2', 'wsl', 'windows', 'forge', handoff.validator().profiles)
        self.settings.unlink()
        handoff.protect(self.settings.parent, directory=True)
        self.assertEqual(handoff.install(plan, plan['digest'], self.settings), 'written')
        self.assertTrue(self.install())
        self.assertEqual(handoff.install(plan, plan['digest'], self.settings), 'unchanged')
        self.assertFalse(self.install())
        plan_record = json.loads((self.settings.parent / 'settings-deployment.json').read_text())
        runtime_record = json.loads((self.settings.parent / 'deployment.json').read_text())
        self.assertEqual(plan_record['schema'], 'deep32.deployment.v1')
        self.assertEqual(plan_record['plan_digest'], plan['digest'])
        self.assertEqual(runtime_record['schema'], 'deep32-forge-install/1')

    def test_interpreter_caches_are_never_shipped_runtime_or_identity(self):
        before = provision.kit_identity(self.kit)
        cached = self.kit / 'firstboot/__pycache__/synthetic-test.pyc'
        cached.parent.mkdir()
        cached.write_bytes(b'synthetic interpreter cache, not executable test material')
        self.assertEqual(provision.kit_identity(self.kit), before)
        self.assertFalse(any('__pycache__' in name or name.endswith('.pyc') for name in thin.runtime_members(self.kit)))

    def test_wrong_unknown_secret_and_types_refused_without_writes(self):
        mutations = [dict(extra='choice'), dict(written_by='tskey-auth-synthetic'),
                     dict(node_name='a' * 40), dict(written_by='Bearer synthetic'),
                     dict(written_by='https://login.tailscale.com/synthetic'),
                     dict(written_by='KLMNP23456'), dict(profile='gpu'), dict(format_version=True),
                     dict(transport=[]), dict(features=dict(token='synthetic')),
                     dict(size=dict(threads=True, mem_gb=4, disk_gb=80))]
        for values in mutations:
            with self.subTest(values=values):
                data = choices() | values
                self.settings.write_text(json.dumps(data), encoding='ascii')
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, '^' + renderer.REFUSAL.replace('.', r'\.') + '$'):
                    self.install()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.calls, [])

    def test_duplicate_keys_both_input_modes(self):
        self.settings.write_text(json.dumps(choices())[:-1] + ',"role":"forge"}', encoding='ascii')
        for installed in (True, False):
            with self.subTest(installed=installed), self.assertRaisesRegex(ValueError, 'non-secret C7'):
                renderer.load(self.settings, installed=installed)

    def test_nested_duplicate_keys_refused(self):
        self.settings.write_text(json.dumps(choices())[:-1] + ',"features":{"web_switch":true,"web_switch":false}}', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'non-secret C7'):
            renderer.load(self.settings)

    def test_seed_cli_rejects_duplicate_input_before_output(self):
        self.settings.write_text(json.dumps(choices())[:-1] + ',"role":"forge"}', encoding='ascii')
        output = self.work / 'not-created-seed'
        with mock.patch.object(sys, 'argv', ['render-settings.py', str(self.settings), '--disk-serial',
                'SYNTHETIC-DISK', '--output', str(output)]), contextlib.redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as exited:
                renderer.main()
        self.assertEqual(exited.exception.code, 2)
        self.assertEqual(error.getvalue().strip(), renderer.REFUSAL)
        self.assertFalse(output.exists())

    def test_absent_settings_is_immediate_and_write_free(self):
        self.settings.unlink()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'non-secret C7'):
            self.install()
        self.assertEqual(self.snapshot(), before)

    def test_non_object_non_ascii_and_oversized_json_refused(self):
        for data in (b'[]', b'null', b'\xff', b' ' * 65537):
            self.settings.write_bytes(data)
            with self.subTest(data=data[:8]), self.assertRaisesRegex(ValueError, 'non-secret C7'):
                renderer.load(self.settings)

    def test_hardlinked_settings_refused(self):
        os.link(self.settings, self.work / 'synthetic-settings-link')
        with self.assertRaisesRegex(ValueError, 'non-secret C7'):
            renderer.load(self.settings)

    def test_preflight_failure_does_not_copy_or_create_markers(self):
        before = self.snapshot()
        def failed():
            raise host.PreflightError('tailscale-signed-out', 'Run tailscale login on this host and retry.')
        with self.assertRaisesRegex(ValueError, 'tailscale login'):
            provision.provision('docker', self.payload, self.kit, self.root, self.run_stub, failed)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.calls, [])

    def test_corrupt_payload_refused_before_host_or_writes(self):
        (self.payload / 'synthetic.txt').write_bytes(b'changed')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'payload hash'):
            self.install()
        self.assertEqual(self.snapshot(), before)

    def test_docker_replay_resume_stop_and_status_preserve_identity(self):
        self.assertTrue(self.install())
        record = json.loads((self.settings.parent / 'deployment.json').read_text())
        before = self.snapshot()
        count = len(self.calls)
        self.assertFalse(self.install())
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.install(resume=True))
        (self.settings.parent / 'compose.yaml').write_text('synthetic compose\n', encoding='ascii')
        with contextlib.redirect_stdout(io.StringIO()):
            provision.lifecycle('stop', 'docker', self.root, self.run_stub)
            provision.lifecycle('stop', 'docker', self.root, self.run_stub)
        firstboots = lambda: sum(argv[0] == 'python3' and argv[1].endswith('firstboot.py')
                                 for argv, _ in self.calls)
        prior = firstboots()
        self.assertTrue(self.install())
        self.assertEqual(firstboots(), prior + 1)
        self.assertTrue(self.install(resume=True))
        self.assertEqual(firstboots(), prior + 2)
        self.assertEqual(json.loads((self.settings.parent / 'deployment.json').read_text()), record)
        argv = [call[0] for call in self.calls]
        self.assertFalse(any('systemctl' in call for call in argv))
        self.assertFalse(any('load' in call for call in argv))
        self.assertFalse(any('-v' in call or 'down' in call for call in argv))
        before = self.snapshot()
        state = provision.lifecycle('status', 'docker', self.root, self.run_stub)
        self.assertEqual(state['install_id'], record['install_id'])
        self.assertEqual(self.snapshot(), before)

    def test_status_strips_code_login_and_arbitrary_messages(self):
        self.install()
        path = self.root / 'run/deep32-forge/status'
        path.write_text('D32S/1 7 forge stage=pairing state=waiting pair=KLMNP23456 signin=https://login.tailscale.com/synthetic msg=synthetic-secret\n')
        output = json.dumps(provision.lifecycle('status', 'docker', self.root, self.run_stub))
        for value in ('KLMNP23456', 'https://', 'synthetic-secret', 'signin', 'pair='):
            self.assertNotIn(value, output)

    def test_interrupted_firstboot_resumes_existing_state(self):
        def interrupted(argv, **kwargs):
            self.calls.append((argv, kwargs))
            if argv[0] == 'python3' and argv[1].endswith('firstboot.py'):
                private = self.root / 'var/lib/deep32-forge/private'
                private.mkdir(parents=True)
                (private / 'forge-token').write_text('synthetic-test-identity', encoding='ascii')
                raise subprocess.TimeoutExpired(argv, 1800)
            return subprocess.CompletedProcess(argv, 0)
        with self.assertRaises(subprocess.TimeoutExpired), contextlib.redirect_stdout(io.StringIO()):
            provision.provision('docker', self.payload, self.kit, self.root, interrupted, lambda: None)
        identity = self.root / 'var/lib/deep32-forge/private/forge-token'
        self.assertFalse((self.settings.parent / 'provisioned.json').exists())
        self.assertTrue(self.install(resume=True))
        self.assertEqual(identity.read_text(), 'synthetic-test-identity')

    def test_interrupted_payload_and_kit_file_copy_resumes(self):
        calls = 0
        original = shutil.copyfile
        def interrupted(source, target, *args, **kwargs):
            nonlocal calls
            if str(target).endswith('.deep32-new'):
                calls += 1
                if calls == 4:
                    Path(target).write_bytes(b'synthetic interrupted copy')
                    raise OSError('synthetic interruption')
            return original(source, target, *args, **kwargs)
        with mock.patch.object(shutil, 'copyfile', side_effect=interrupted), self.assertRaises(OSError):
            self.install()
        self.assertTrue(self.install(resume=True))

    def test_changed_media_settings_or_kit_cannot_resume(self):
        self.install()
        for field in ('settings', 'kit', 'payload'):
            with self.subTest(field=field):
                path = {'settings': self.settings, 'kit': self.kit / 'firstboot/status.py',
                        'payload': self.payload / 'synthetic.txt'}[field]
                previous = path.read_bytes()
                if field == 'settings':
                    path.write_text(json.dumps(choices() | dict(node_name='another-node')), encoding='ascii')
                else:
                    path.write_bytes(previous + b'\n')
                if field == 'payload':
                    self.inventory()
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'same verified media'):
                    self.install(resume=True)
                self.assertEqual(self.snapshot(), before)
                path.write_bytes(previous)
                self.inventory()

    def test_wsl_dispatches_systemd_and_host_status_uses_windows_only(self):
        self.assertTrue(self.install('wsl'))
        self.assertTrue(any(call[0][:2] == ['systemctl', 'enable'] for call in self.calls))
        run = mock.Mock(return_value=signed_in())
        self.assertEqual(host.tailscale_status('wsl', run)['name'], 'synthetic-forge')
        self.assertEqual(run.call_args.args[0], ['tailscale.exe', 'status', '--json'])
        self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_tailscale_failure_reasons_and_redaction(self):
        cases = [(FileNotFoundError(), 'tailscale-missing'),
                 (subprocess.TimeoutExpired(['synthetic'], 10), 'tailscale-timeout'),
                 (subprocess.CompletedProcess([], 1, 'synthetic-secret', ''), 'tailscale-daemon'),
                 (subprocess.CompletedProcess([], 0, 'synthetic-secret', ''), 'tailscale-status-invalid'),
                 (signed_in(BackendState='NeedsLogin', AuthURL='synthetic-secret'), 'tailscale-signed-out'),
                 (signed_in(BackendState='Stopped'), 'tailscale-daemon'),
                 (signed_in(BackendState='NeedsMachineAuth'), 'tailscale-approval'),
                 (signed_in(Self=dict(HostName='synthetic', Online=False)), 'tailscale-offline'),
                 (signed_in(Self=dict(HostName='synthetic', Online=True, Expired=True)), 'tailscale-key-expired')]
        for result, reason in cases:
            run = mock.Mock(side_effect=result) if isinstance(result, Exception) else mock.Mock(return_value=result)
            with self.subTest(reason=reason), self.assertRaises(host.PreflightError) as caught:
                host.tailscale_status('docker', run)
            self.assertEqual(caught.exception.reason, reason)
            self.assertNotIn('synthetic-secret', str(caught.exception))
            self.assertEqual(run.call_args.args[0], ['tailscale', 'status', '--json'])

    def test_relayed_status_is_running_and_changes_no_settings(self):
        run = mock.Mock(return_value=signed_in(Peer={'synthetic': {'CurAddr': '', 'Relay': 'synthetic-region'}}))
        self.assertEqual(host.tailscale_status('docker', run)['state'], 'Running')
        self.assertEqual(run.call_count, 1)

    def test_platform_preflight_is_read_only_for_docker_and_wsl(self):
        config = json.loads((self.kit / 'pins.json').read_text())
        config['runtime'] = dict(mineru_minimum_driver='500.0', tei_minimum_driver='500.0',
            cuda_minimum_driver='500.0', windows_cuda13_driver_build='500.0',
            docker_minimum_version='25.0.0', compose_minimum_version='2.30.0')
        (self.kit / 'pins.json').write_text(json.dumps(config), encoding='ascii')
        kernel = self.root / 'proc/sys/kernel/osrelease'
        kernel.parent.mkdir(parents=True)
        kernel.write_text('synthetic-microsoft-wsl2', encoding='ascii')
        (self.root / 'dev').mkdir()
        (self.root / 'dev/dxg').touch()
        class Probe:
            def bind(self, address):
                pass
            def close(self):
                pass
        for form in ('docker', 'wsl'):
            argv = []
            def run(args, **kwargs):
                argv.append(args)
                if args[0] in ('tailscale', 'tailscale.exe'):
                    return signed_in()
                text = {'nvidia-smi': '999.0', 'docker': '25.0.0'}[args[0]]
                if args[:3] == ['docker', 'compose', 'version']:
                    text = '2.30.0'
                elif args[:2] == ['docker', 'info']:
                    text = json.dumps(dict(Runtimes={'nvidia': {}}, DockerRootDir='/synthetic/docker', OSType='linux'))
                return subprocess.CompletedProcess(args, 0, text, '')
            before = self.snapshot()
            result = host.check(form, self.payload, self.kit, self.root, run,
                disk_usage=lambda path: types.SimpleNamespace(free=10**15), socket_factory=lambda *args: Probe())
            self.assertGreater(result['free_bytes_required'], 0)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(argv[0], ['tailscale.exe' if form == 'wsl' else 'tailscale', 'status', '--json'])
            self.assertFalse(any('up' in row or 'login' in row or 'set' in row for row in argv))

    def test_firstboot_settings_refuses_immediately(self):
        with mock.patch.object(boot, 'KIT', self.kit), mock.patch.object(boot.time, 'sleep') as sleep:
            with self.assertRaisesRegex(boot.StageError, 'non-secret C7'):
                boot.settings(Status(), 'docker', self.work / 'absent-settings')
        sleep.assert_not_called()

    def test_firstboot_main_preflight_refusal_creates_no_status_or_state(self):
        form = self.work / 'synthetic-form'
        form.write_text('docker\n', encoding='ascii')
        before = self.snapshot()
        path = lambda name: form if name == '/etc/deep32-forge/form' else Path(name)
        with mock.patch.object(boot, 'Path', side_effect=path), \
                mock.patch.object(boot, 'Status', side_effect=lambda: channel.Status(self.root / 'run/status', ())), \
                mock.patch.object(boot, 'settings', side_effect=boot.StageError('forge-settings-invalid', renderer.REFUSAL)), \
                mock.patch.object(boot.os, 'umask'), mock.patch.object(sys, 'argv', ['firstboot.py']), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as result:
                boot.main()
        self.assertEqual(result.exception.code, 1)
        self.assertEqual(error.getvalue().strip(), renderer.REFUSAL)
        self.assertEqual(self.snapshot(), before)

    def test_firstboot_read_only_host_signin_uses_actual_host_name(self):
        with mock.patch.object(boot, 'KIT', self.kit), mock.patch.object(subprocess, 'run', return_value=signed_in()) as run:
            self.assertEqual(boot.signin(Status(), 'wsl', choices()), 'synthetic-forge')
        self.assertEqual(run.call_args.args[0], ['tailscale.exe', 'status', '--json'])

    def test_c6_stdout_redacts_but_private_status_keeps_live_code(self):
        path = self.work / 'status/current'
        status = channel.Status(path, ())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status.update('pairing', 'waiting', pair='KLMNP23456', expires=300, msg='Pair on the Workstation before expiry')
        self.assertNotIn('KLMNP23456', output.getvalue())
        self.assertIn('pair=KLMNP23456', path.read_text())

    def test_wait_timeout_kills_child_and_reports_reason(self):
        process = mock.Mock()
        process.poll.side_effect = [None, None]
        with mock.patch.object(boot.time, 'monotonic', side_effect=[0, 1]), self.assertRaisesRegex(boot.StageError, 'Resume this installation'):
            boot.wait_process(process, Status(), 0, 'forge-test-timeout', 'Resume this installation.')
        process.terminate.assert_called_once()

    def test_model_credential_denial_fails_immediately_without_log_value(self):
        token = self.work / 'synthetic-test-token'
        token.write_text(hashlib.sha256(b'T-0189 synthetic test token').hexdigest(), encoding='ascii')
        opener = mock.Mock()
        opener.open.side_effect = boot.urllib.error.HTTPError('http://127.0.0.1:8081/health', 401,
                                                           'synthetic-private-error', None, None)
        status = Status()
        with mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                mock.patch.object(boot.urllib.request, 'build_opener', return_value=opener), \
                mock.patch.object(boot.time, 'sleep') as sleep:
            with self.assertRaises(boot.StageError) as caught:
                boot.start_models(status, token)
        self.assertEqual(caught.exception.reason, 'forge-model-auth-denied')
        self.assertNotIn('synthetic-private-error', str(caught.exception))
        self.assertNotIn(token.read_text(), str(caught.exception))
        sleep.assert_not_called()

    def test_identity_interruption_and_repeat_preserve_committed_token_and_keys(self):
        state = self.work / 'synthetic-identities'
        calls, fail = [], [True]
        def key_tool(*args, **kwargs):
            calls.append(args)
            output = Path(args[args.index('-out') + 1])
            output.write_text('SYNTHETIC TEST KEY MATERIAL\n', encoding='ascii')
            if fail[0]:
                fail[0] = False
                raise subprocess.TimeoutExpired(['synthetic-openssl'], 30)
        with mock.patch.object(boot, 'STATE', state), mock.patch.object(boot, 'run', side_effect=key_tool):
            with self.assertRaises(subprocess.TimeoutExpired):
                boot.make_identities()
            token = state / 'private/forge-token'
            previous = token.read_bytes()
            boot.make_identities()
            count = len(calls)
            boot.make_identities()
            self.assertEqual(token.read_bytes(), previous)
            self.assertEqual(len(calls), count)
            token.unlink()
            with self.assertRaisesRegex(boot.StageError, 'Restore'):
                boot.make_identities()
            self.assertFalse(token.exists())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch', required=True)
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch
    Path(SCRATCH).mkdir(parents=True, exist_ok=True)
    # unittest evaluates skipUnless while the class is created.
    RuntimeTests.__unittest_skip__ = False
    unittest.main(argv=[sys.argv[0], *remaining])
