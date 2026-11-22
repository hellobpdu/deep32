#!/usr/bin/env python3
"""Offline regression coverage for native Windows Forge prerequisites."""
import argparse
import copy
from types import SimpleNamespace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[2] / 'src/release-tools'
spec = importlib.util.spec_from_file_location('windows_forge_c10_case', TOOLS / 'forge_preflight_windows.py')
windows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows)


def alpha2_recipes():
    """forge-recipes-0.1.0-alpha.2.json is alpha.2's signed waiver record, kept as history. Its recipes still apply,
    so the tests read it with every waiver's release set to the release under test, in memory only."""
    rows = json.loads((TOOLS / 'forge-recipes-0.1.0-alpha.2.json').read_bytes())
    for row in rows:
        if isinstance(row.get('waiver'), dict):
            row['waiver']['release'] = windows.preflight.requirements.RELEASE
    return rows


class WindowsForgePreflight(unittest.TestCase):
    def setUp(self):
        self.version = {'Platform': {'Name': 'Docker Desktop 4.99.0 (10001)'},
                        'Os': 'linux', 'Arch': 'amd64', 'Version': '29.9.3'}
        self.info = dict(OSType='linux', OperatingSystem='Docker Desktop', Architecture='x86_64',
                         KernelVersion='6.18.33.2-microsoft-standard-WSL2', ServerVersion='29.9.3',
                         Driver='overlay2', DriverStatus=[['Backing Filesystem', 'extfs']],
                         DockerRootDir='/var/lib/docker', Runtimes={'nvidia': {}})
        self.host = ('26200.9974', 'pro')
        self.recipe = {'os_id': 'windows', 'os_version': '26100+', 'architecture': 'amd64',
                       'journey': 'J2', 'desktop': {'backend': 'wsl2',
                       'editions': ['education', 'enterprise', 'home', 'pro'],
                       'version_min': '4.99.0', 'engine_version_min': '29.0.0'}}

    def test_local_classic_wsl2_recipe_floors(self):
        signed_row = dict(self.recipe, id='native-desktop', accepted_on='2026-11-18',
                          recipe_sha256='1' * 64, acceptance_sha256='2' * 64)
        windows.preflight.requirements.validate_recipes([signed_row])
        self.assertIs(windows.desktop_recipe(self.info, self.version, self.host, 'J2', [signed_row]), signed_row)
        for key, wrong in (('OperatingSystem', 'Docker Engine'), ('KernelVersion', '6.4-linuxkit'),
                           ('ServerVersion', '28.1.0'), ('Architecture', 'aarch64')):
            with self.subTest(key=key), self.assertRaises(windows.Refusal):
                windows.desktop_recipe(dict(self.info, **{key: wrong}), self.version, self.host, 'J2', [self.recipe])
        narrow = copy.deepcopy(self.recipe)
        narrow['desktop']['editions'] = ['pro']
        windows.desktop_recipe(self.info, self.version, self.host, 'J2', [narrow])
        with self.assertRaisesRegex(windows.Refusal, r'build 26100 or later \(Pro\)'):
            windows.desktop_recipe(self.info, self.version, ('26200.9974', 'home'), 'J2', [narrow])
        # An unvalidated row with a malformed or out-of-range floor never matches.
        for field, wrong in (('version_min', '4.96.x'), ('version_min', '4.100.0'), ('backend', 'hyperv'),
                             ('editions', ['core']), ('kernel_release', '6.18.33.2-microsoft-standard-WSL2')):
            with self.subTest(field=field, wrong=wrong), self.assertRaises(ValueError):
                recipe = copy.deepcopy(self.recipe)
                recipe['desktop'][field] = wrong
                windows.desktop_recipe(self.info, self.version, self.host, 'J2', [recipe])
        with self.assertRaises(windows.Refusal):
            windows.desktop_recipe(self.info, self.version, self.host, 'J4', [self.recipe])

    def test_default_containerd_is_refused_before_recipe_qualification(self):
        containerd = dict(self.info, Driver='overlayfs',
                          DriverStatus=[['driver-type', 'io.containerd.snapshotter.v1']])
        with self.assertRaises(windows.preflight.getter.DockerImageStoreRefusal) as refused:
            windows.desktop_recipe(containerd, self.version, self.host, 'J2', [self.recipe])
        self.assertEqual(refused.exception.code, 'docker-store-containerd')

    def test_physical_storage_preserves_host_volume_and_guest_domains(self):
        root = Path(__file__).parent
        identity = {'data_root': str(root / 'synthetic-target')}
        backing = root.parent / 'docker_data.vhdx'
        budget = {'data_bytes': 40, 'desktop-backing_bytes': 70, 'docker_bytes': 150}
        rows = windows.physical_storage(
            identity, budget, backing, 175, observe=lambda *a, **kw: ('same-device', 110))
        self.assertEqual(rows, [
            {'filesystem_id': 'desktop-guest', 'needed_bytes': 150, 'free_bytes': 175},
            {'filesystem_id': 'windows-same-device', 'needed_bytes': 40, 'free_bytes': 110},
            {'filesystem_id': 'windows-same-device', 'needed_bytes': 70, 'free_bytes': 110},
        ])

    def test_guest_space_requires_matching_daemon_id(self):
        identity = 'e003a88a-2961-4186-a5fb-1841d616faba'
        for encoding in ('utf-8', 'utf-16', 'utf-16-le'):
            seen = []

            def run(argv, **kwargs):
                seen.append(argv)
                self.assertNotIn('WSLENV', kwargs['env'])
                self.assertGreater(kwargs['timeout'], 0)
                if len(seen) == 1:
                    self.assertEqual(argv, ['wsl.exe', '--list', '--running', '--quiet'])
                    return SimpleNamespace(returncode=0, stdout='unrelated-distro\r\ndocker-desktop\r\n'.encode(encoding))
                self.assertEqual(argv, ['wsl.exe', '--distribution', 'docker-desktop', '--user', 'root',
                                        '--exec', '/bin/sh', '-c', windows.GUEST_STORAGE_PROBE,
                                        'forge-storage', identity])
                self.assertIn('[ "$name" = dockerd ]', argv[-3])
                self.assertIn('nsenter -t "$pid" -m', argv[-3])
                self.assertIn('[ "$engine" = "$1" ]', argv[-3])
                self.assertIn('stat -f -c "%a %S" "$root"', argv[-3])
                self.assertEqual(kwargs['stdout_limit'], 128)
                return SimpleNamespace(returncode=0, stdout=b'250155438 4096\n')

            with self.subTest(encoding=encoding), patch.dict(os.environ, {'WSLENV': 'SYNTHETIC/u'}):
                self.assertEqual(windows.guest_storage(dict(self.info, ID=identity.upper()), run=run),
                                 250155438 * 4096)
                self.assertEqual(len(seen), 2)

    def test_guest_probe_does_not_target_stopped_or_unconfirmed_distro(self):
        info = dict(self.info, ID='e003a88a-2961-4186-a5fb-1841d616faba')
        for rc, listed in ((0, b'docker-desktop-data\r\nunrelated-distro\r\n'),
                           (0, b'docker-desktop\0'), (1, b'docker-desktop\n'), (0, b'x' * 65537)):
            with self.subTest(rc=rc, sample_length=len(listed)):
                commands = []

                def run(argv, **kwargs):
                    commands.append(argv)
                    return SimpleNamespace(returncode=rc, stdout=listed)

                with self.assertRaises(windows.Refusal):
                    windows.guest_storage(info, run=run)
                self.assertEqual(commands, [['wsl.exe', '--list', '--running', '--quiet']])
        for invalid in (dict(info, ID='invalid'), dict(info, DockerRootDir='/tmp/docker')):
            with self.assertRaises(windows.Refusal):
                windows.guest_storage(invalid, run=lambda *a, **kw: self.fail('queried WSL with unconfirmed engine'))

    def test_guest_probe_rejects_failed_or_invalid_namespace_capacity(self):
        info = dict(self.info, ID='e003a88a-2961-4186-a5fb-1841d616faba')
        for rc, storage in ((3, b''), (0, b'100 -4096'), (0, b'100 0'), (0, b'100 4096\n123 4096'),
                            (0, b'9223372036854775808 4096'), (0, b'x' * 129), (0, '100 4096')):
            with self.subTest(rc=rc, storage=storage), self.assertRaises(windows.Refusal):
                responses = iter([SimpleNamespace(returncode=0, stdout=b'docker-desktop\n'),
                                  SimpleNamespace(returncode=rc, stdout=storage)])
                windows.guest_storage(info, run=lambda *a, **kw: next(responses))

    def test_bounded_output_and_timeout(self):
        def python(code, limit, timeout):
            return windows.bounded_stdout([sys.executable, '-I', '-B', '-c', code],
                                          timeout=timeout, stdout_limit=limit,
                                          stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self.assertEqual(python("print('4 4096')", 64, 5).stdout.split(), [b'4', b'4096'])
        with self.assertRaises(ValueError):
            python("print('x' * 1000000)", 64, 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            python('import time; time.sleep(10)', 64, 0.1)

    def test_native_wsl_uses_system_directory_and_docker_has_per_user_fallback(self):
        local = Path(__file__).resolve().parent
        cli = local / 'Programs/DockerDesktop/resources/bin/docker.exe'
        wsl = local / 'system32/wsl.exe'

        class Candidate:
            def __init__(self, path):
                self.path = path

            def __str__(self):
                return str(self.path)

            def is_file(self):
                return self.path in (cli, wsl)

        def system_directory(buffer, length):
            buffer.value = str(wsl.parent)
            return len(buffer.value)

        with (patch.dict(os.environ, {'PATH': 'relative', 'LOCALAPPDATA': str(local)}, clear=True),
              patch.object(windows.paths, 'plain', Candidate),
              patch.object(windows.ctypes, 'WinDLL',
                           return_value=SimpleNamespace(GetSystemDirectoryW=system_directory), create=True)):
            self.assertEqual(windows.native_tool('docker.exe'), str(cli))
            self.assertEqual(windows.native_tool('wsl.exe'), str(wsl))

    def test_empty_runtime_requires_positive_cdi_device(self):
        with self.assertRaises(windows.Refusal):
            windows.desktop_recipe(dict(self.info, Runtimes={}), self.version, self.host, 'J2', [self.recipe])
        info = dict(self.info, Runtimes={}, DiscoveredDevices=[{'ID': 'nvidia.com/gpu=0'}])
        windows.desktop_recipe(info, self.version, self.host, 'J2', [self.recipe])

    def test_desktop_signed_budget_includes_physical_backing(self):
        bundle_names = ['core', 'embedder-turing', 'ingestion']
        desktop = {'backend': 'docker-desktop', 'domains': [
            {'domain': 'data', 'peak_bytes': 11}, {'domain': 'docker', 'peak_bytes': 23},
            {'domain': 'desktop-backing', 'peak_bytes': 31}]}
        selected = [{'id': name, 'storage_budgets': [desktop]} for name in bundle_names]
        discs = [{'sha256': '1' * 64, 'size': 10} for _ in bundle_names]
        result = windows.preflight.getter.forge_budget_document(selected, discs, 'turing', backend='docker-desktop')
        self.assertEqual([item['component'] for item in result['media_components']], bundle_names)
        self.assertEqual(result['desktop-backing_bytes'], 93)
        with self.assertRaises(windows.preflight.getter.manifest.Refusal):
            windows.preflight.getter.forge_budget_document(selected, discs, 'turing')

    def test_docker_observation_does_not_capture_proxy_or_auth_fields(self):
        self.assertEqual(windows.DESKTOP[2], 'npipe:////./pipe/dockerDesktopLinuxEngine')
        for field in ('HttpProxy', 'HttpsProxy', 'RegistryConfig'):
            self.assertNotIn(field, windows.DOCKER_FORMAT)


PREFIX = 'synthetic-net.ts.net'
HOST = 'synthetic-forge.' + PREFIX


class WindowsOwnerWaiver(unittest.TestCase):
    """The whole Windows check under an owner waiver, with synthetic host observations."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-windows-waiver-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        backing = self.root / 'desktop-backing'
        backing.mkdir()
        self.host = ('26200.9974', 'pro')
        self.version = {'Platform': {'Name': 'Docker Desktop 4.99.0 (10001)'},
                        'Os': 'linux', 'Arch': 'amd64', 'Version': '29.9.3'}
        self.info = dict(OSType='linux', OperatingSystem='Docker Desktop', Architecture='x86_64',
                         KernelVersion='6.18.33.2-microsoft-standard-WSL2', ServerVersion='29.9.3',
                         Driver='overlay2', DriverStatus=[['Backing Filesystem', 'extfs']],
                         DockerRootDir='/var/lib/docker', Runtimes={'nvidia': {}},
                         ID='e003a88a-2961-4186-a5fb-1841d616faba')
        self.status = {'BackendState': 'Running', 'Self': {'Online': True, 'DNSName': HOST + '.'},
                       'CurrentTailnet': {'MagicDNSSuffix': PREFIX, 'MagicDNSEnabled': True},
                       'CertDomains': [HOST]}
        self.compose = '2.40.3'
        self.smi = '617.14, 7.5, 11264\n'
        requirements = windows.preflight.requirements
        rows = requirements.cite(
            alpha2_recipes(), {'prerequisites.json': 'a' * 64},
            {name: 'b' * 64 for name in ('LINUX-MINERU-R3-EVIDENCE.md', 'WINDOWS-RAW-QUALIFICATION.md')})
        requirements.validate_recipes(rows)
        self.recipe = next(row for row in rows if row['os_id'] == 'windows')
        self.envelope = dict(format='deep32-forge-prerequisites/1', release='0.1.0-alpha.3',
                             capability_sha256='7' * 64, pins_sha256='8' * 64, recipes=rows,
                             driver_floors=dict(tei_minimum_driver='575.51.03', cuda_minimum_driver='580.65.06',
                                                mineru_minimum_driver='580.65.06'),
                             minimum_vram_mib=10752, vram_policy='C1-install-matrix-tier-11',
                             windows_cuda13_driver_build='580.00', storage_evidence_sha256='9' * 64)
        settings = windows.preflight.settings
        media = [dict(component=name, size=size, sha256=digit * 64) for name, size, digit in
                 (('core', 11, 'a'), ('embedder-turing', 22, 'b'), ('ingestion', 33, 'c'))]
        choices = {'format': 'deep32-settings', 'format_version': 1, 'role': 'forge',
                   **settings.DEFAULTS, 'forge_accept_ct': True}
        data = self.root / 'forge-data'
        self.plan = settings.make_plan(choices, media[0]['sha256'], self.root, 'J1', 'docker', 'windows', 'forge',
                                       settings.validator().profiles, data, docker_storage_path=backing,
                                       media_components=media, tailnet_binding=settings.tailnet_digest(PREFIX),
                                       desktop_isolation_confirmed=True)
        plan = self.root / 'consented-plan.json'
        plan.write_bytes(settings.canonical(self.plan))
        self.args = argparse.Namespace(source=str(self.root), forge_data_root=str(data), plan=str(plan),
                                       accept_plan=self.plan['digest'], gpu_class='turing', journey='J1')
        self.budget = {'media_components': media, 'data_bytes': 40, 'docker_bytes': 150,
                       'desktop-backing_bytes': 70}
        self.rows = [{'filesystem_id': 'desktop-guest', 'needed_bytes': 150, 'free_bytes': 175}]
        self.ports = Mock()

    def run_tool(self, argv, **kwargs):
        desktop = argv[:3] == windows.DESKTOP
        if desktop and argv[3:] == ['info', '--format', windows.DOCKER_FORMAT]:
            stdout = json.dumps({key: value for key, value in self.info.items() if key != 'DiscoveredDevices'})
        elif desktop and argv[3:] == ['info', '--format', '{{json .DiscoveredDevices}}']:
            stdout = json.dumps(self.info.get('DiscoveredDevices'))
        elif desktop and argv[3:] == ['version', '--format', '{{json .Server}}']:
            stdout = json.dumps(self.version)
        elif desktop and argv[3:] == ['compose', 'version', '--short']:
            stdout = self.compose + '\n'
        elif argv == ['nvidia-smi.exe', *windows.preflight.GPU_QUERY[1:]]:
            stdout = self.smi
        elif argv == ['tailscale.exe', 'status', '--json']:
            stdout = json.dumps(self.status)
        else:
            self.fail('unexpected host query: ' + repr(argv))
        return SimpleNamespace(returncode=0, stdout=stdout)

    def collect(self, envelope=None):
        with patch.object(windows.paths, 'module'), patch.object(windows.paths, 'restrict_acl'), \
                patch.object(windows.paths, 'protected'), \
                patch.object(windows.preflight, 'signed_evidence',
                             return_value=(self.budget, self.envelope if envelope is None else envelope)) as signed:
            result = windows.collect(
                self.args, run=self.run_tool, host=lambda: self.host,
                probe_backing=lambda identity: Path(identity['docker_storage_path']) / 'docker_data.vhdx',
                probe_guest=lambda info, run: 175,
                disk_check=lambda identity, budget, backing, guest: self.rows, port_check=self.ports)
        self.assertEqual(signed.call_args.kwargs, {'backend': 'docker-desktop'})
        return result

    def widen(self):
        """Turn the evidence host into a later one that the alpha.2 floors admit without a run."""
        self.host = ('26300.1', 'home')
        self.version = dict(self.version, Platform={'Name': 'Docker Desktop 5.0.0 (20001)'}, Version='30.0.0')
        self.info = dict(self.info, ServerVersion='30.0.0', KernelVersion='7.0.1.1-microsoft-standard-WSL2')

    def test_alpha2_windows_recipe_accepts_the_named_host_with_one_plain_line(self):
        notice = ('Note: this host type is accepted for 0.1.0-alpha.3 under an owner waiver dated 2026-11-18 '
                  '(recipe windows-11-docker-desktop-wsl2-j1). I have not yet run a full install on it; '
                  'FORGE-PREREQUISITES.json lists what was not run.')
        result = self.collect()
        self.assertEqual(set(result), {'prerequisites', 'storage_plan', 'notice'})
        self.assertEqual(result['notice'], notice)
        self.assertRegex(notice, r'\A[ -~]{1,300}\Z')
        self.assertEqual(result['storage_plan'], self.rows)
        self.assertEqual(len(result['prerequisites']), 19)
        self.assertTrue(all(result['prerequisites'].values()))
        self.ports.assert_called_once()
        line = json.dumps(result, sort_keys=True, separators=(',', ':'))
        self.assertRegex(line, r'\A\{[ -~]*\}\Z')
        self.assertLess(len(line), 65535)
        # The same host under a passed lifecycle record gets no line and the same checks.
        passed = dict({key: value for key, value in self.recipe.items() if key != 'waiver'},
                      acceptance_sha256='c' * 64)
        self.envelope['recipes'] = [passed]
        windows.preflight.requirements.validate_recipes([passed])
        self.assertEqual(self.collect(), {key: value for key, value in result.items() if key != 'notice'})

    def engine(self, number):
        return {'version': dict(self.version, Version=number), 'info': dict(self.info, ServerVersion=number)}

    def desktop(self, number):
        return {'version': dict(self.version, Platform={'Name': 'Docker Desktop ' + number + ' (10002)'})}

    def kernel(self, release):
        return {'info': dict(self.info, KernelVersion=release)}

    def test_later_windows_desktop_engine_and_kernel_match_the_floors(self):
        expected = self.collect()
        cases = [{'host': host} for host in (('26200.10123', 'pro'), ('26300.1', 'pro'), ('26100.1', 'pro'),
                                             ('26200.9974', 'home'), ('26200.9974', 'enterprise'),
                                             ('26200.9974', 'education'), ('27000.0', 'home'))]
        cases += [self.desktop(number) for number in ('4.99.1', '4.100.0', '5.0.0')]
        cases += [self.engine(number) for number in ('29.0.0', '29.9.4', '29.10.0', '30.0.0')]
        cases += [self.kernel(release) for release in ('6.18.33.3-microsoft-standard-WSL2',
                                                       '6.20.1.1-microsoft-standard-WSL2+',
                                                       '7.0.1-microsoft-standard-wsl2')]

        def matched():
            # Everything equals the evidence host's result except the engine version it reports.
            result = self.collect()
            self.assertEqual(result['prerequisites']['docker.server'], {'state': 'met', 'version': self.version['Version']})
            for item in (result, expected):
                del item['prerequisites']['docker.server']
            self.assertEqual(result, expected)
            expected['prerequisites']['docker.server'] = {'state': 'met', 'version': '29.9.3'}

        for changes in cases:
            with self.subTest(changes=changes), patch.multiple(self, **changes):
                matched()
        self.widen()
        matched()
        self.assertEqual(self.ports.call_count, len(cases) + 2)

    def test_older_or_other_hosts_are_refused_by_the_floors(self):
        self.collect()
        floors = ('Use Windows 11 build 26100 or later (Home, Pro, Enterprise or Education), Docker Desktop 4.99.0 '
                  'or later and Docker Engine 29.0.0 or later on the WSL 2 backend.')
        below = [{'host': host} for host in (('26099.9999', 'pro'), ('22631.4890', 'pro'), ('22000.1', 'pro'),
                                             ('26200.9974', 'server'), ('26200.9974', 'core'))]
        below += [self.desktop(number) for number in ('4.98.9', '4.42.0', '3.100.0')]
        below += [self.engine(number) for number in ('28.5.0', '28.99.99', '20.10.24')]
        for changes in below:
            with self.subTest(changes=changes), patch.multiple(self, **changes), \
                    self.assertRaises(windows.Refusal) as refused:
                self.collect()
            self.assertEqual(str(refused.exception), floors)
        other = [self.kernel(release) for release in ('6.18.33.2-generic', '6.10.14-linuxkit', '6.18.33.2-WSL2',
                                                     '6.18.33.2-microsoft-standard')]
        other += [{'info': dict(self.info, OperatingSystem='Docker Engine')},
                  {'info': dict(self.info, ServerVersion='29.9.4')},
                  {'version': dict(self.version, Version='29.9.4')}]
        other += [self.engine(number) for number in ('29.9.3-rc.1', '29.09.3', '29.9', '30')]
        other += [self.desktop(number) for number in ('04.99.0', '4.99.0.1')]
        for changes in other:
            with self.subTest(changes=changes), patch.multiple(self, **changes), self.assertRaises(windows.Refusal):
                self.collect()
        self.recipe['journey'] = 'J2'
        with self.assertRaisesRegex(windows.Refusal, 'accepted signed Forge recipe for this Windows journey'):
            self.collect()
        self.ports.assert_called_once()

    def test_every_technical_gate_still_refuses_under_a_waiver(self):
        self.assert_gates_refuse()

    def test_every_technical_gate_still_refuses_for_a_later_host_under_the_floors(self):
        self.widen()
        self.assert_gates_refuse()

    def assert_gates_refuse(self):
        self.assertIn('notice', self.collect())
        self.ports.reset_mock()
        conflict = windows.preflight.ports.PortConflict('Port 8081 has an unverified local TCP listener.')
        store = dict(self.info, Driver='overlayfs', DriverStatus=[['driver-type', 'io.containerd.snapshotter.v1']])
        cases = (
            ('smi', '580.65.05, 7.5, 11264\n', windows.Refusal),                      # below the driver floor
            ('smi', '617.14, 7.5, 10751\n', windows.Refusal),                         # below the VRAM floor
            ('smi', '617.14, 8.6, 11264\n', windows.Refusal),                         # another GPU class
            ('smi', '617.14, 7.5, 11264\n580.00, 7.5, 11264\n', windows.Refusal),     # one card below the floor
            ('compose', '2.29.9', windows.Refusal),
            ('info', store, windows.preflight.getter.DockerImageStoreRefusal),
            ('info', dict(self.info, Runtimes={}, DiscoveredDevices=[]), windows.Refusal),
            ('status', dict(self.status, CurrentTailnet={'MagicDNSSuffix': 'other-synthetic.ts.net',
                                                         'MagicDNSEnabled': True},
                            Self={'Online': True, 'DNSName': 'synthetic-forge.other-synthetic.ts.net.'}),
             windows.Refusal),
            ('status', dict(self.status, CurrentTailnet={'MagicDNSSuffix': PREFIX, 'MagicDNSEnabled': False}),
             windows.Refusal),
            ('status', dict(self.status, CertDomains=[]), windows.Refusal),
            ('ports', Mock(side_effect=conflict), windows.preflight.ports.PortConflict),
        )
        for name, value, refusal in cases:
            with self.subTest(name=name, value=value), patch.object(self, name, value), \
                    self.assertRaises(refusal):
                self.collect()
        self.ports.assert_not_called()
        for envelope in (False, {key: value for key, value in self.envelope.items()
                                 if key != 'storage_evidence_sha256'},
                         {key: value for key, value in self.envelope.items()
                          if key != 'windows_cuda13_driver_build'}):
            with self.subTest(envelope=envelope), self.assertRaisesRegex(windows.Refusal, 'exact signed Windows'):
                self.collect(envelope)
        self.args.accept_plan = 'f' * 64
        with self.assertRaises(ValueError):
            self.collect()
        self.ports.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
