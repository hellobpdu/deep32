"""Offline host refusals: no Docker, WSL, GPU, sockets or host settings."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'hardened_host', Path(__file__).resolve().parents[1] / 'forms/host-requirements.py')
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)


class HostPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('D32_RUNTIME_TEST_SCRATCH') or os.environ.get('TEMP'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.payload = self.root / 'payload'
        self.payload.mkdir()
        (self.payload / 'synthetic-data').write_bytes(b'synthetic release data')
        self.kit = self.root / 'kit'
        self.kit.mkdir()
        self.backing = self.root / 'desktop-disk'
        self.backing.mkdir()
        self.pins = dict(mineru_minimum_driver='550.0', tei_minimum_driver='560.0',
                         cuda_minimum_driver='570.0', windows_cuda13_driver_build='580.0',
                         docker_minimum_version='25.0.0', compose_minimum_version='2.30.0')
        self.save_pins()
        self.info = dict(OSType='linux', Runtimes={'nvidia': {}}, DockerRootDir='/var/lib/docker',
                         OperatingSystem='Docker Desktop', KernelVersion='6.6-microsoft-standard-WSL2')
        self.driver = '999.0'
        self.calls = []
        self.disks = mock.Mock(return_value=SimpleNamespace(free=10**15))
        for patcher in (mock.patch.object(host.subprocess, 'run', side_effect=AssertionError('real process forbidden')),
                        mock.patch.object(host.socket, 'socket', side_effect=AssertionError('real socket forbidden'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def save_pins(self):
        (self.kit / 'pins.json').write_text(json.dumps({'runtime': self.pins}), encoding='ascii')

    def run_stub(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] in ('tailscale', 'tailscale.exe'):
            value = json.dumps(dict(BackendState='Running', Self=dict(HostName='synthetic-host', Online=True)))
        elif '--query-gpu=driver_version' in argv:
            value = self.driver
        elif 'info' in argv:
            value = json.dumps(self.info)
        elif 'compose' in argv:
            value = '2.30.0'
        else:
            value = '25.0.0'
        return subprocess.CompletedProcess(argv, 0, value, '')

    def check(self, platform='linux', **extra):
        return host.check('docker', self.payload, self.kit, root=self.root,
                          run=self.run_stub, disk_usage=self.disks, check_loopback_ports=False,
                          host_platform=platform, docker_storage_path=extra.pop('backing', self.backing), **extra)

    def test_non_linux_or_unknown_engine_refused_before_storage(self):
        for value in ('windows', '', None, 1, True):
            with self.subTest(value=value):
                self.info['OSType'] = value
                with self.assertRaisesRegex(ValueError, 'Linux Docker engine'):
                    self.check()
                self.disks.assert_not_called()
        del self.info['OSType']
        with self.assertRaisesRegex(ValueError, 'Linux Docker engine'):
            self.check()

    def test_linux_engine_and_gpu_runtime_observed_without_mutation(self):
        result = self.check()
        self.assertEqual(result['minimum_driver'], '570.0')
        self.assertTrue(result['free_bytes_required'] > host.GIB)
        self.assertEqual(self.calls, [
            ['tailscale', 'status', '--json'],
            ['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'],
            ['docker', 'version', '--format', '{{.Server.Version}}'],
            ['docker', 'compose', 'version', '--short'],
            ['docker', 'info', '--format', '{{json .}}']])

    def test_containerd_store_is_measured_where_containerd_keeps_images(self):
        self.info.update(Driver='overlayfs', DriverStatus=[['driver-type', 'io.containerd.snapshotter.v1']],
                         Containerd={'Address': '/run/containerd/containerd.sock'})
        store = self.root / 'var/lib/containerd'
        store.mkdir(parents=True)
        self.check()
        self.assertIn(store, [call.args[0] for call in self.disks.call_args_list])
        self.disks.reset_mock()
        self.info['Containerd'] = {'Address': '/elsewhere.sock'}
        with self.assertRaisesRegex(ValueError, 'Install Docker Engine with its classic overlay2 store'):
            self.check()
        self.disks.assert_not_called()

    def test_every_pinned_driver_floor_is_enforced(self):
        for platform in ('linux', 'windows'):
            names = list(self.pins)[:3] + (['windows_cuda13_driver_build'] if platform == 'windows' else [])
            for name in names:
                previous = self.pins[name]
                with self.subTest(platform=platform, name=name):
                    self.pins[name] = '900.0'
                    self.save_pins()
                    self.driver = '900.0'
                    self.assertEqual(self.check(platform)['minimum_driver'], '900.0')
                    self.driver = '899.99'
                    with self.assertRaisesRegex(ValueError, '900.0 or later'):
                        self.check(platform)
                self.pins[name] = previous
                self.save_pins()

    def test_unresolved_floor_refused_before_gpu_query(self):
        self.pins['tei_minimum_driver'] = 'TO-PIN'
        self.save_pins()
        with self.assertRaisesRegex(ValueError, 'verified runtime.tei_minimum_driver pin'):
            self.check()
        self.assertEqual(self.calls, [['tailscale', 'status', '--json']])

    def test_all_observed_gpus_must_meet_floor(self):
        self.driver = '999.0\n569.99'
        with self.assertRaisesRegex(ValueError, '570.0 or later'):
            self.check()
        self.disks.assert_not_called()

    def test_windows_backing_must_exist_and_be_absolute_directory(self):
        file = self.root / 'not-directory'
        file.write_bytes(b'synthetic')
        for path in (Path('relative-backing'), self.root / 'misspelt-disk', file):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'existing Docker Desktop disk backing'):
                self.check('windows', backing=path)
        self.disks.assert_not_called()
        self.assertFalse((self.root / 'misspelt-disk').exists())

    def test_windows_backing_links_refused_before_disk_probe(self):
        original = Path.is_symlink
        with mock.patch.object(Path, 'is_symlink', lambda path: path == self.backing or original(path)):
            with self.assertRaisesRegex(ValueError, 'without links or reparse points'):
                self.check('windows')
        self.disks.assert_not_called()

    def test_windows_network_and_device_paths_refused_without_metadata_io(self):
        for path in ('//synthetic-host/share', '//?/UNC/synthetic-host/share', '//?/C:/synthetic'):
            with self.subTest(path=path), mock.patch.object(Path, 'is_dir',
                    side_effect=AssertionError('network metadata query forbidden')):
                with self.assertRaisesRegex(ValueError, 'network shares and device paths'):
                    self.check('windows', backing=Path(path))
        self.disks.assert_not_called()

    def test_windows_gpu_backend_and_fixed_linux_pipe_are_required(self):
        self.check('windows')
        docker = [argv for argv in self.calls if argv[0] == 'docker']
        self.assertTrue(docker)
        self.assertTrue(all(argv[1:3] == ['--host', host.DESKTOP_PIPE] for argv in docker))
        for field, value in (('KernelVersion', 'generic-linux'), ('Runtimes', {})):
            previous = self.info[field]
            self.info[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Docker Desktop'):
                self.check('windows')
            self.info[field] = previous


if __name__ == '__main__':
    unittest.main()
