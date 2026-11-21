"""Offline portable Forge role regressions; all state is synthetic test material."""
import argparse
import base64
from contextlib import ExitStack
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
spec = importlib.util.spec_from_file_location('tested_forge_docker', KIT / 'forms/forge_docker_role.py')
forge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forge)
common = forge.common
spec = importlib.util.spec_from_file_location('tested_forge_provision', KIT / 'forms/provision.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)
SCRATCH = None


def settings(profile='two-machines'):
    return dict(format='deep32-settings', format_version=1, role='forge', node_name='synthetic-forge',
                user_name='forge', time_zone='Etc/UTC', keyboard='us', profile=profile,
                disk_encryption=False, transport='tailnet', publish='serve-https')


def pins(builder=False):
    value = dict(models={'tei': {'revision': 'a' * 40}}, tei={'selected': 'ampere86'},
                 runtime={'reranker_dtype': 'float16', 'full_image_id': 'TO-PIN',
                          'model_bridge_image_id': 'TO-PIN'}, full={'deliberately_unused': 'TO-PIN'})
    if builder:
        value['builder'] = {'synthetic': True}
    return value


def images(builder=False):
    keys = ['TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE', 'CONVERTER_IMAGE']
    if builder:
        keys += ['BUILDER_IMAGE', 'QDRANT_IMAGE']
    return {key: 'sha256:' + str(index) * 64 for index, key in enumerate(keys, 1)}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='ascii')


class ForgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-forge-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.root = self.work / 'installation'
        self.kit = self.work / 'kit'
        self.payload = self.work / 'payload'
        self.payload.mkdir()
        self.kit.mkdir()
        for name in ('autoinstall/render-settings.py', 'containers/builder-state.py'):
            path = self.kit / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((KIT / name).read_bytes())
        write_json(self.kit / 'pins.json', pins())
        self.settings_file = self.work / 'synthetic-settings.json'
        write_json(self.settings_file, settings())
        self.calls, self.messages = [], []
        self.subject = forge.Lifecycle(self.root, self.kit, run=self.command, emit=self.messages.append)
        self.addCleanup(patch.stopall)
        patch.object(subprocess, 'Popen', side_effect=AssertionError('real process forbidden')).start()
        patch.object(subprocess, 'run', side_effect=AssertionError('real process forbidden')).start()

    def command(self, argv, reason, fix, timeout=120, emit=print):
        self.calls.append([str(value) for value in argv])
        if 'ps' in argv:
            return json.dumps([dict(Service=name, State='running', Health='healthy',
                                    Error='synthetic-hidden-value', Labels='synthetic-hidden-value')
                               for name in forge.SERVICES])
        return ''

    def module(self, name, path):
        if name in ('forge_provision', 'forge_start_provision'):
            def copy(source, target):
                if source != target:
                    shutil.copytree(source, target, dirs_exist_ok=True)
            return SimpleNamespace(verified_payload=lambda payload: 'b' * 64,
                                   digest=lambda path: 'c' * 64, copy_kit=copy)
        if name == 'forge_release_profile':
            return SimpleNamespace(unresolved=lambda value: [key for key, child in
                self.leaves(value) if isinstance(child, str) and 'TO-PIN' in child])
        return self.original_module(name, path)

    def leaves(self, value, prefix=''):
        if isinstance(value, dict):
            for key, child in value.items():
                yield from self.leaves(child, prefix + '.' + key)
        else:
            yield prefix, value

    def fixture_runtime(self):
        stack = ExitStack()
        # These pre-existing fixtures exercise the explicit legacy-media path.
        # Retained-media orchestration has its own complete inventory fixtures.
        prepare = self.subject.prepare
        stack.enter_context(patch.object(self.subject, 'prepare',
            side_effect=lambda *args, **kwargs: prepare(*args, retain_runtime=False, **kwargs)))
        self.original_module = common.module
        stack.enter_context(patch.object(common, 'module', side_effect=self.module))
        stack.enter_context(patch.object(self.subject, 'host', return_value='synthetic-forge.synthetic.ts.net'))
        stack.enter_context(patch.object(self.subject, 'images', return_value=images()))
        stack.enter_context(patch.object(self.subject, 'daemon_storage'))
        stack.enter_context(patch.object(forge, 'identities'))
        stack.enter_context(patch.object(self.subject, 'pair_local'))
        stack.enter_context(patch.object(self.subject, 'preflight_publication'))
        stack.enter_context(patch.object(self.subject, 'publication_controller', return_value=SimpleNamespace(
            publish=unittest.mock.Mock(), remove=unittest.mock.Mock(), load=lambda: None)))
        return stack

    def dispatch(self, role=None, platform='windows'):
        adapter = SimpleNamespace(main=lambda argv: self.calls.append(argv))
        paths = []
        def selected(name, path):
            paths.append(Path(path).name)
            return SimpleNamespace(loader=SimpleNamespace(exec_module=lambda value: None))
        with patch.object(provision.importlib.util, 'spec_from_file_location', side_effect=selected), \
                patch.object(provision.importlib.util, 'module_from_spec', return_value=adapter):
            args = ['--form', 'docker', '--platform', platform, '--kit', str(self.kit),
                    '--payload', str(self.payload), '--settings', str(self.settings_file), '--data-root', str(self.root)]
            if role:
                args += ['--docker-role', role]
            provision.main(args)
        return paths

    def test_default_linux_and_windows_route_to_portable_forge(self):
        for platform in ('linux', 'windows'):
            with self.subTest(platform=platform):
                self.assertEqual(self.dispatch(platform=platform), ['forge_docker_role.py'])
                self.assertIn(platform, self.calls[-1])
                self.assertNotIn('--content', self.calls[-1])

    def test_full_is_explicit_and_keeps_its_adapter_on_both_platforms(self):
        for platform in ('linux', 'windows'):
            self.assertEqual(self.dispatch('full', platform), ['docker_role.py'])

    def test_forge_does_not_accept_full_content_input(self):
        with self.assertRaises(SystemExit) as result:
            provision.main(['--form', 'docker', '--content', str(self.work)])
        self.assertEqual(result.exception.code, 10)
        self.assertFalse(self.root.exists())

    def test_forge_settings_keep_workstation_profiles_and_https_boundary(self):
        for profile in ('two-machines', 'win-nvidia', 'linux-nvidia', 'headless'):
            self.assertEqual(self.subject.settings(settings(profile))['profile'], profile)
        for key, bad in (('transport', 'loopback'), ('publish', 'tailnet-plain')):
            value = settings()
            value[key] = bad
            with self.assertRaises(common.Refusal):
                self.subject.settings(value)

    def test_render_preserves_models_converter_builder_and_no_engine_gateway(self):
        result = forge.prepare_config(self.kit, self.payload, self.root, images(True), pins(True))
        self.assertEqual(set(result['services']), {'embeddings', 'reranker', 'door', 'converter', 'builder', 'qdrant'})
        self.assertEqual(result['services']['door']['ports'], ['127.0.0.1:8081:8081', '127.0.0.1:8082:8082'])
        self.assertEqual(result['services']['door']['secrets'], ['forge-token'])
        self.assertEqual(set(result['secrets']), {'forge-token'})
        self.assertEqual(result['services']['converter']['network_mode'], 'none')
        self.assertEqual(result['services']['converter']['profiles'], ['conversion'])
        self.assertEqual(result['services']['builder']['profiles'], ['builder'])
        self.assertEqual(result['services']['qdrant']['profiles'], ['builder'])
        self.assertFalse(any('ports' in service for name, service in result['services'].items() if name != 'door'))
        for service in result['services'].values():
            self.assertEqual(service['pull_policy'], 'never')
            self.assertNotIn('build', service)
            self.assertNotIn('privileged', service)
        self.assertNotIn('mcp', json.dumps(result))

    def test_missing_or_wrong_image_and_revision_refused_without_state(self):
        for change in ('missing', 'wrong-image', 'wrong-revision'):
            selected, config = images(), pins()
            if change == 'missing':
                selected.pop('DOOR_IMAGE')
            elif change == 'wrong-image':
                selected['DOOR_IMAGE'] = 'mutable:latest'
            else:
                config['models']['tei']['revision'] = 'main'
            with self.subTest(change=change), self.assertRaises(ValueError):
                forge.prepare_config(self.kit, self.payload, self.root, selected, config)
        self.assertFalse(self.root.exists())

    def test_image_selection_excludes_full_and_bridge_and_keeps_jobs(self):
        self.assertEqual(self.subject.image_roles('ampere86', pins()),
                         {'tei-ampere86', 'door', 'reranker', 'converter'})
        self.assertEqual(self.subject.image_roles('ampere86', pins(True)),
                         {'tei-ampere86', 'door', 'reranker', 'converter', 'builder', 'qdrant'})
        self.assertEqual(self.subject.storage_image(images()), images()['DOOR_IMAGE'])
        self.assertIn('full', common.Lifecycle(self.root, self.kit).image_roles('ampere86', pins()))

    def test_prepare_start_stop_resume_and_status_for_linux_and_windows(self):
        for platform in ('linux', 'windows'):
            with self.subTest(platform=platform), self.fixture_runtime():
                self.subject.root = self.work / ('install-' + platform)
                self.subject.record = self.subject.root / 'install.json'
                self.subject.kit = self.kit
                self.subject.platform = platform
                self.subject.docker_storage_path = self.work / 'synthetic-desktop-storage'
                data = self.subject.prepare(self.payload, self.settings_file, platform)
                self.assertEqual(data['schema'], forge.SCHEMA)
                self.assertNotIn('content', data)
                self.assertFalse(any(name in json.dumps(self.calls) for name in ('importer', 'gateway', 'model-bridge')))
                up = [argv for argv in self.calls if 'up' in argv][-1]
                self.assertEqual(up[-3:], list(forge.SERVICES))
                if platform == 'windows':
                    self.assertIn(common.DESKTOP_PIPE, up)
                result = self.subject.status()
                self.assertTrue(result['local_services_healthy'])
                self.assertFalse(result['ready'])
                self.assertEqual(result['pairing'], 'requires-workstation-enrolment')
                self.assertNotIn('synthetic-hidden-value', json.dumps(result))
                self.subject.stop()
                self.assertEqual(self.subject.state()['stage'], 'stopped')
                count = len(self.calls)
                self.subject.resume()
                self.assertEqual(len(self.calls), count)
                self.subject.start()
                self.assertEqual(self.subject.state()['stage'], 'published-pairing-required')

    def test_start_keeps_local_pairing_after_authenticated_model_health(self):
        for platform in ('linux', 'windows'):
            with self.subTest(platform=platform), self.fixture_runtime():
                self.subject.root = self.work / ('pair-order-' + platform)
                self.subject.record = self.subject.root / 'install.json'
                self.subject.kit = self.kit
                self.subject.docker_storage_path = self.work / 'synthetic-desktop-storage'
                seen = []
                def pair(data):
                    self.assertEqual(data['platform'], platform)
                    self.assertIn('up', self.calls[-1])
                    self.assertIn('--wait', self.calls[-1])
                    self.assertEqual(self.calls[-1][-3:], list(forge.SERVICES))
                    seen.append(data['stage'])
                with patch.object(self.subject, 'pair_local', side_effect=pair):
                    self.subject.prepare(self.payload, self.settings_file, platform)
                    self.subject.start()
                self.assertEqual(len(seen), 2)
                self.assertFalse(self.subject.status()['ready'])

    def test_linux_local_pairing_reuses_retained_firstboot_and_private_c6(self):
        statuses, modules = [], []
        status = object()
        def c6(path):
            statuses.append(path)
            return status
        firstboot = SimpleNamespace(pair=unittest.mock.Mock())
        def selected(name, path):
            modules.append((name, path))
            return SimpleNamespace(Status=c6) if name == 'forge_portable_c6' else firstboot
        previous = sys.modules.get('status')
        with patch.object(common, 'module', side_effect=selected):
            self.subject.pair_local(dict(platform='linux', settings=settings()))
        self.assertIs(sys.modules.get('status'), previous)
        self.assertEqual(modules, [('forge_portable_c6', self.kit / 'firstboot/status.py'),
                                  ('forge_portable_firstboot', self.kit / 'firstboot/firstboot.py')])
        self.assertEqual(statuses, [self.root / 'status/status'])
        self.assertEqual((firstboot.KIT, firstboot.STATE, firstboot.HELPER),
                         (self.kit, self.root, self.kit / 'pairing/bin/pairing-helper'))
        firstboot.pair.assert_called_once_with(status, 'docker', settings(), self.root / 'private/forge-token',
                                               self.root / 'private/kb-public.pem')
        self.assertFalse(self.messages)

    def test_local_pairing_failure_is_mapped_without_protected_output(self):
        firstboot = SimpleNamespace(pair=unittest.mock.Mock(side_effect=RuntimeError('SYNTHETIC_PROTECTED_CODE')))
        with patch.object(common, 'module', side_effect=[SimpleNamespace(Status=lambda **kw: object()), firstboot]):
            with self.assertRaisesRegex(common.Refusal, 'protected console') as result:
                self.subject.pair_local(dict(platform='linux', settings=settings()))
        self.assertNotIn('SYNTHETIC_PROTECTED_CODE', str(result.exception))
        self.assertFalse(self.messages)

    def test_windows_local_pairing_keeps_protected_console_adapter_gate(self):
        with patch.object(common, 'module', side_effect=AssertionError('No unqualified Windows pairing console')):
            self.subject.pair_local(dict(platform='windows', settings=settings('win-nvidia')))
        self.assertEqual(len(self.messages), 1)
        self.assertIn('protected Windows host-console adapter', self.messages[0])

    def test_payload_refusal_happens_before_host_calls_or_state_writes(self):
        original = common.module
        def selected(name, path):
            if name == 'forge_provision':
                def refuse(payload):
                    raise ValueError('synthetic damaged payload')
                return SimpleNamespace(verified_payload=refuse)
            return original(name, path)
        with patch.object(common, 'module', side_effect=selected), patch.object(self.subject, 'host') as host:
            with self.assertRaises(ValueError):
                self.subject.prepare(self.payload, self.settings_file)
            host.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_recovery_and_gpu_hold_stop_prepare_upgrade_start_and_resume(self):
        for relative, expected in (('conversion-recovery.json', 3), ('status/gpu-hold', 4)):
            marker = self.root / relative
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(b'SYNTHETIC RETAINED JOB STATE\n')
            for action in ('prepare', 'upgrade', 'start', 'resume'):
                with self.subTest(marker=relative, action=action), \
                        patch.object(self.subject, 'host') as host, \
                        patch.object(self.subject, 'compose') as compose, \
                        patch.object(self.subject, 'state') as state:
                    with self.assertRaises(common.Refusal) as result:
                        if action in ('prepare', 'upgrade'):
                            self.subject.prepare(self.payload, self.settings_file, upgrade=action == 'upgrade')
                        else:
                            getattr(self.subject, action)()
                    self.assertEqual(result.exception.code, expected)
                    host.assert_not_called()
                    compose.assert_not_called()
                    state.assert_not_called()
                    self.assertEqual(marker.read_bytes(), b'SYNTHETIC RETAINED JOB STATE\n')
                    self.assertFalse(self.subject.record.exists())
                    self.assertFalse((self.root / 'owner.json').exists())
            marker.unlink()
        self.assertFalse(self.calls)

    def test_forge_and_full_cannot_reuse_each_others_state(self):
        self.root.mkdir()
        if os.name == 'nt':
            common.windows_security(self.root, directory=True)
        common.atomic_json(self.subject.record, dict(schema=common.SCHEMA, install_id='1' * 16,
            platform='linux', release='2' * 64, content=str(self.payload), stage='prepared', settings=settings('headless')))
        with self.assertRaises(common.Refusal):
            self.subject.state()

    def test_publication_request_has_only_authenticated_model_routes(self):
        request = self.subject.publication('1' * 16, 'synthetic-forge.synthetic.ts.net')
        self.assertFalse(any(row['path'] == '/mcp' or '8766' in row['upstream'] for row in request['routes']))
        self.assertEqual({row['upstream'] for row in request['routes']}, {'http://127.0.0.1:8081', 'http://127.0.0.1:8082'})
        self.assertEqual(request['consent'], 'required')
        self.assertEqual(request['conflicts'], 'refuse')
        self.assertEqual(request['unknown_paths'], 'deny')

    def test_alternate_c7_ports_reach_compose_and_pairing_handover(self):
        value = settings()
        value['forge_ports'] = dict(embeddings=18081, reranker=18082, pairing=17453,
                                  embeddings_https=18443, reranker_https=19443, pairing_tcp=17454)
        value['forge_accept_ct'] = True
        self.assertEqual(self.subject.settings(value), value)
        rendered = forge.prepare_config(self.kit, self.payload, self.root, images(), pins(), value)
        self.assertEqual(rendered['services']['door']['ports'],
                         ['127.0.0.1:18081:8081', '127.0.0.1:18082:8082'])
        request = self.subject.publication('1' * 16, 'synthetic-forge.synthetic.ts.net', value)
        self.assertEqual(request['pairing_port'], 17454)
        self.assertEqual(request['model_endpoints'], {
            'embeddings': 'https://synthetic-forge.synthetic.ts.net:18443',
            'reranker': 'https://synthetic-forge.synthetic.ts.net:19443'})
        self.assertEqual({row['https_port'] for row in request['routes']}, {18443, 19443})

    def test_c7_ports_and_ct_cannot_contain_credentials_or_wrong_types(self):
        for bad in ({'embeddings': '18443'}, {'embeddings': True}, {'embeddings': 0},
                    {'embeddings': 65536}, {'password': 12345}, {'pairing': 8081}, {'reranker_https': 443}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.subject.settings(dict(settings(), forge_ports=bad))
        for bad in ('true', 1, 'SYNTHETIC-SECRET-NOT-ALLOWED'):
            with self.assertRaises(ValueError):
                self.subject.settings(dict(settings(), forge_accept_ct=bad))

    def test_port_changes_with_retained_peers_require_explicit_unpair(self):
        with self.fixture_runtime():
            self.subject.prepare(self.payload, self.settings_file)
            self.subject.stop()
            peers = self.root / 'pairing/peers/forge-joined'
            peers.mkdir(parents=True)
            (peers / 'synthetic-peer.json').write_text('{}', encoding='ascii')
            write_json(self.settings_file, dict(settings(), forge_ports={'embeddings_https': 18443}))
            with self.assertRaisesRegex(common.Refusal, 'Revoke and unpair'):
                self.subject.prepare(self.payload, self.settings_file)

    def test_owned_busy_ports_need_actual_install_project_and_loopback_bindings(self):
        data = dict(install_id='1' * 16, release='2' * 64, settings=settings())
        label = {'com.docker.compose.project': 'deep32-' + data['install_id'],
                 'com.docker.compose.service': 'door',
                 'com.docker.compose.project.config_files': str(self.root / 'releases' / data['release'] / 'compose.json')}
        info = dict(Config={'Labels': label}, State={'Running': True}, HostConfig={'PortBindings': {
            '8081/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8081'}],
            '8082/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8082'}]}})
        with patch.object(self.subject, 'compose', return_value='a' * 64), \
                patch.object(self.subject, 'call', side_effect=lambda *a, **kw: json.dumps([info])):
            self.assertEqual(self.subject.owned_local_ports(data, settings()), (8081, 8082))
            label['com.docker.compose.project'] = 'deep32-unrelated'
            with self.assertRaises(common.Refusal):
                self.subject.owned_local_ports(data, settings())
            label['com.docker.compose.project'] = 'deep32-' + data['install_id']
            info['HostConfig']['PortBindings']['8081/tcp'][0]['HostIp'] = '0.0.0.0'
            with self.assertRaises(common.Refusal):
                self.subject.owned_local_ports(data, settings())

    def test_upgrade_start_accepts_the_running_door_of_the_previous_release(self):
        data = dict(install_id='1' * 16, release='2' * 64, settings=settings())
        label = {'com.docker.compose.project': 'deep32-' + data['install_id'],
                 'com.docker.compose.service': 'door',
                 'com.docker.compose.project.config_files': str(self.root / 'releases' / ('3' * 64) / 'compose.json')}
        info = dict(Config={'Labels': label}, State={'Running': True}, HostConfig={'PortBindings': {
            '8081/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8081'}],
            '8082/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8082'}]}})
        with patch.object(self.subject, 'compose', return_value='a' * 64), \
                patch.object(self.subject, 'call', side_effect=lambda *a, **kw: json.dumps([info])):
            self.assertEqual(self.subject.owned_local_ports(data, settings()), (8081, 8082))
            for outside in (self.root / 'elsewhere' / ('3' * 64) / 'compose.json',
                            self.root / 'releases' / 'not-a-release' / 'compose.json',
                            self.root / 'releases' / ('3' * 64) / 'other.json'):
                with self.subTest(outside=outside):
                    label['com.docker.compose.project.config_files'] = str(outside)
                    with self.assertRaises(common.Refusal):
                        self.subject.owned_local_ports(data, settings())

    def test_coexistence_refusal_precedes_any_installation_write_or_docker_load(self):
        write_json(self.settings_file, dict(settings(), forge_accept_ct=True))
        with self.fixture_runtime(), patch.object(self.subject, 'preflight_publication',
                side_effect=common.Refusal('Host Serve port 443 already has an unowned route; change C7 forge_ports.embeddings_https.')), \
                patch.object(self.subject, 'images') as image_load:
            with self.assertRaisesRegex(common.Refusal, '443'):
                self.subject.prepare(self.payload, self.settings_file)
            image_load.assert_not_called()
            self.assertFalse(self.root.exists())

    def test_uninstall_removes_only_owned_routes_hook_and_keeps_private_data(self):
        with self.fixture_runtime():
            self.subject.prepare(self.payload, self.settings_file)
            retained = self.root / 'private-retained-synthetic'
            retained.write_text('SYNTHETIC RETAINED DATA', encoding='ascii')
            self.subject.uninstall()
            self.subject.publication_controller().remove.assert_called_once()
            self.assertEqual(retained.read_text(), 'SYNTHETIC RETAINED DATA')
            self.assertFalse((self.root / 'startup-hook-v1.ps1').exists())
            self.assertFalse((self.root / 'startup-hook-v1.sh').exists())
            self.assertEqual(self.subject.state()['stage'], 'stopped')

    def test_each_selected_archive_is_verified_before_any_docker_load(self):
        roles = ['tei-ampere86', 'reranker', 'door', 'converter', 'full', 'model_bridge']
        rows = [dict(role=role, config_digest='sha256:' + str(index) * 64,
                     path='images/synthetic-' + role + '.tar') for index, role in enumerate(roles, 1)]
        write_json(self.payload / 'images/index.json', rows)
        verified = []
        def verify(config, row, binding):
            verified.append(row['role'])
            if row['role'] == 'converter':
                raise ValueError('synthetic final archive is damaged')
            return row
        modules = {'docker_installed_inputs': SimpleNamespace(CAPABILITIES={'8.6': 'ampere86'}, verified_image=verify),
                   'docker_image_binding': SimpleNamespace()}
        with patch.object(common, 'module', side_effect=lambda name, path: modules[name]), \
                patch.object(self.subject, 'call', return_value='8.6\n') as command:
            with self.assertRaises(ValueError):
                self.subject.images(self.payload, pins())
        self.assertEqual(verified, roles[:4])
        self.assertTrue(all(args.args[0][0] != 'docker' for args in command.call_args_list))

    def test_windows_image_loading_uses_selected_forge_pins_and_fixed_desktop_pipe(self):
        rows = [dict(role=role, config_digest='sha256:' + str(index) * 64, path='images/synthetic-' + role + '.tar')
                for index, role in enumerate(['tei-ampere86', 'reranker', 'door', 'converter'], 1)]
        write_json(self.payload / 'images/index.json', rows)
        inputs = SimpleNamespace(CAPABILITIES={'8.6': 'ampere86'}, verified_image=lambda config, row, binding: row)
        modules = {'docker_installed_inputs': inputs, 'docker_image_binding': SimpleNamespace()}
        calls = []
        def observe(argv, *args, **kwargs):
            calls.append(argv)
            if argv[0] == 'nvidia-smi.exe':
                return '8.6\n'
            self.assertEqual(argv[:3], ['docker', '--host', common.DESKTOP_PIPE])
            self.assertEqual(argv[3:5], ['image', 'inspect'])
            return json.dumps([dict(Id=argv[-1], Os='linux', Architecture='amd64')])
        self.subject.platform = 'windows'
        self.subject.run = observe
        with patch.object(common, 'module', side_effect=lambda name, path: modules[name]):
            result = self.subject.images(self.payload, pins())
        self.assertEqual(result, images())
        self.assertEqual(len(calls), 5)

    def test_windows_host_gate_has_forge_ports_and_no_full_content_budget(self):
        backing = self.work / 'synthetic-desktop-storage'
        backing.mkdir()
        self.subject.docker_storage_path = backing
        observed = []
        def check(*args, **kwargs):
            observed.append((args, kwargs))
            return {'free_bytes_required': 4096}
        def host_command(argv, *args, **kwargs):
            if argv[0] == 'tailscale.exe':
                return json.dumps(dict(BackendState='Running', Self=dict(DNSName='synthetic-forge.synthetic.ts.net.')))
            self.assertEqual(argv, ['openssl', 'version'])
            return 'synthetic OpenSSL\n'
        self.subject.run = host_command
        with patch.object(common, 'module', return_value=SimpleNamespace(check=check)):
            self.assertEqual(self.subject.host(self.payload, 'windows'), 'synthetic-forge.synthetic.ts.net')
        self.assertEqual(observed[0][1]['host_platform'], 'windows')
        self.assertEqual(observed[0][1]['docker_storage_path'], backing)
        self.assertNotIn('extra_storage_bytes', observed[0][1])
        self.assertNotIn('loopback_ports', observed[0][1])
        self.assertEqual(self.subject.storage_required, 4096)

    def test_forge_identity_is_reused_and_missing_or_changed_authority_refuses(self):
        common.private_directory(self.root)
        helpers = common.module('synthetic_forge_identity_fixture', KIT / 'containers/full/local_enrol.py')
        _, tokens, _ = helpers._pairing(KIT)
        public = ('-----BEGIN PUBLIC KEY-----\n' + base64.b64encode(
            bytes.fromhex('302a300506032b6570032100') + bytes([7]) * 32).decode('ascii') + '\n-----END PUBLIC KEY-----\n')
        def keys(private):
            for name, value in (('kb-private.pem', 'SYNTHETIC-TEST-ONLY-PRIVATE-KEY\n'), ('kb-public.pem', public)):
                path = private / name
                if not path.exists():
                    path.write_text(value, encoding='ascii', newline='\n')
                    path.chmod(0o600)
            return private / 'kb-public.pem'
        with patch.object(tokens, 'make_token', return_value='5c' * 32):
            forge.identities(self.root, KIT, key_factory=keys)
        before = {p.name: p.read_bytes() for p in (self.root / 'private').iterdir()}
        forge.identities(self.root, KIT, committed=True, key_factory=keys)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / 'private').iterdir()})
        token = self.root / 'private/forge-token'
        token.write_text('6d' * 32 + '\n', encoding='ascii', newline='\n')
        with self.assertRaisesRegex(common.Refusal, 'original Forge identity'):
            forge.identities(self.root, KIT, committed=True, key_factory=keys)
        token.unlink()
        with self.assertRaisesRegex(common.Refusal, 'Restore all Forge identity'):
            forge.identities(self.root, KIT, committed=True, key_factory=keys)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *remaining])
