#!/usr/bin/env python3
"""Offline Forge local TCP and Tailscale Serve ownership regressions."""

import argparse
import copy
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'src/release-tools/forge_ports.py'
SPEC = importlib.util.spec_from_file_location('forge_ports_under_test', SOURCE)
ports = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ports)

SCRATCH = None
ENFORCE_PRIVATE = ports.private
HOST = 'forge.synthetic-test.ts.net'
CONTAINER = 'a' * 64
NATIVE_SERVE = ['tailscale', 'serve', 'status', '--json']
WINDOWS_SERVE = ['tailscale.exe', 'serve', 'status', '--json']
SOCKET_ARGUMENTS = ['docker', '--host', 'unix:///var/run/docker.sock']
SELECTED = {'embeddings': 19081, 'reranker': 19082, 'pairing': 19453,
            'embeddings_https': 18443, 'reranker_https': 18444, 'pairing_tcp': 17453}


class FakeSocket:
    def __init__(self, fixture):
        self.fixture = fixture
        self.closed = False
        self.exclusive = False

    def setsockopt(self, level, option, value):
        self.fixture.assertEqual((socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1), (level, option, value))
        if self.fixture.option_error:
            raise OSError(ports.WINDOWS_IN_USE, 'synthetic exclusive option refusal')
        self.exclusive = True

    def bind(self, endpoint):
        self.fixture.assertEqual(self.fixture.windows, self.exclusive)
        self.fixture.assertEqual('127.0.0.1', endpoint[0])
        port = endpoint[1]
        self.fixture.bound.append(port)
        if port in self.fixture.busy:
            raise OSError(errno.EADDRINUSE, 'synthetic occupation')
        if port in self.fixture.denied:
            status = ports.WINDOWS_ACCESS if self.fixture.windows else errno.EACCES
            raise OSError(status, 'synthetic refusal')

    def close(self):
        self.fixture.assertFalse(self.closed)
        self.closed = True
        self.fixture.closed += 1


class ForgePorts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-forge-port-', dir=SCRATCH)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'forge'
        self.install_id = hashlib.sha256(str(self.root).encode('utf-8')).hexdigest()[:16]
        self.settings = {'forge_ports': SELECTED.copy(), 'forge_accept_ct': True}
        self.busy, self.denied = set(), set()
        self.bound = []
        self.closed = 0
        self.calls = []
        self.command_errors = set()
        self.config = {}
        self.windows = False
        self.option_error = False
        self.native_listeners = {}
        self.checked_acl = []
        self.bad_acl = None
        self.backend = 413
        self.door = None
        self.ids = CONTAINER + '\n'
        if os.name == 'posix' and os.geteuid() != 0:
            def synthetic_root_owner(info, mode):
                ENFORCE_PRIVATE(SimpleNamespace(st_uid=0, st_mode=info.st_mode), mode)

            patch = mock.patch.object(ports, 'private', side_effect=synthetic_root_owner)
            patch.start()
            self.addCleanup(patch.stop)

    def socket(self, family, kind):
        self.assertEqual((socket.AF_INET, socket.SOCK_STREAM), (family, kind))
        return FakeSocket(self)

    def run_command(self, argv, **kwargs):
        self.calls.append(argv)
        self.assertEqual(30, kwargs['timeout'])
        self.assertIs(subprocess.DEVNULL, kwargs['stdin'])
        self.assertTrue(kwargs['capture_output'])
        for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CERT_PATH', 'DOCKER_CONFIG',
                    'DOCKER_AUTH_CONFIG', 'TS_SOCKET'):
            self.assertNotIn(key, kwargs['env'])
        if argv == (WINDOWS_SERVE if self.windows else NATIVE_SERVE):
            return self.response(argv, 'serve', self.config)
        self.assertEqual(ports.DESKTOP_DOCKER if self.windows else SOCKET_ARGUMENTS, argv[:3])
        if argv[3] == 'ps':
            self.assertEqual(['ps', '--no-trunc', '--quiet', '--filter', 'status=running',
                              '--filter', 'label=com.docker.compose.project=deep32-' + self.install_id,
                              '--filter', 'label=com.docker.compose.service=door'], argv[3:])
            return self.response(argv, 'ps', self.ids)
        self.assertEqual(['inspect', '--type', 'container', '--format', ports.DOOR_FORMAT, CONTAINER], argv[3:])
        self.assertNotIn('.Config.Env', argv[-2])
        return self.response(argv, 'inspect', self.door)

    def response(self, argv, name, value):
        if name in self.command_errors:
            return subprocess.CompletedProcess(argv, 1, '', 'synthetic private command diagnostic')
        text = value if isinstance(value, str) else json.dumps(value)
        return subprocess.CompletedProcess(argv, 0, text, '')

    def record(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        for directory in path.parents:
            if directory == self.root or directory.is_relative_to(self.root):
                directory.chmod(0o700)
        path.write_text(json.dumps(value), encoding='ascii')
        path.chmod(0o600)
        return path

    def install(self):
        self.record('owner.json', {'schema': ports.SCHEMA, 'install_id': self.install_id})
        self.record('install.json', {'schema': ports.SCHEMA, 'platform': 'linux', 'install_id': self.install_id,
                     'release': 'b' * 64, 'settings': self.settings, 'stage': 'stopped'})
        # A running door can belong to the immediately prior release after an upgrade.
        config = self.record('releases/' + ('c' * 64) + '/compose.json', {})
        bindings = {f'{container}/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(SELECTED[name])}]
                    for container, name in ((8081, 'embeddings'), (8082, 'reranker'))}
        self.door = {'id': CONTAINER, 'running': True, 'project': 'deep32-' + self.install_id, 'service': 'door',
                     'config': str(config), 'bindings': bindings, 'live': copy.deepcopy(bindings)}

    def routed(self, journal=True, pending=None):
        routes = [{'setting': 'embeddings_https', 'port': 18443, 'kind': 'https', 'target': 'http://127.0.0.1:19081'},
                  {'setting': 'reranker_https', 'port': 18444, 'kind': 'https', 'target': 'http://127.0.0.1:19082'},
                  {'setting': 'pairing_tcp', 'port': 17453, 'kind': 'tcp', 'target': 'tcp://127.0.0.1:19453'}]
        self.config = {'TCP': {'18443': {'HTTPS': True}, '18444': {'HTTPS': True},
                               '17453': {'TCPForward': '127.0.0.1:19453'}},
                       'Web': {HOST + ':18443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:19081'}}},
                               HOST + ':18444': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:19082'}}}}}
        if journal:
            return self.record('config/forge-publication.json', {
                'schema': 'deep32.forge-publication/1', 'install_id': self.install_id, 'host': HOST,
                'routes': routes, 'added': [row for row in (0, 1, 2) if row != pending],
                'pending': pending, 'removing': None, 'phase': 'publishing' if pending is not None else 'ready',
            })
        return None

    def check(self):
        with mock.patch.dict(os.environ, {'DOCKER_HOST': 'ssh://synthetic.invalid',
                                          'DOCKER_CONTEXT': 'synthetic-remote', 'TS_SOCKET': 'synthetic-remote'}):
            ports.check_linux_ports(self.root, self.settings, HOST, run=self.run_command, socket_factory=self.socket)

    def windows_install(self):
        self.install()
        backing = self.root / 'desktop-backing'
        backing.mkdir()
        record = json.loads((self.root / 'install.json').read_bytes())
        self.record('install.json', dict(record, platform='windows', docker_storage_path=str(backing)))

    def windows_check(self):
        self.windows = True

        def acl(path, *, verify, directory=False):
            self.assertTrue(verify)
            self.assertEqual(directory, path.is_dir())
            self.checked_acl.append(path)
            if path == self.bad_acl:
                raise ValueError('synthetic invalid ACL')

        def listeners(selected_ports):
            self.assertEqual([19081, 19082, 19453], selected_ports)
            return {port: self.native_listeners.get(port, []) for port in selected_ports}

        with mock.patch.object(socket, 'SO_EXCLUSIVEADDRUSE', -5, create=True), \
                mock.patch.object(ports, 'windows_acl', return_value=acl), \
                mock.patch.object(ports, 'windows_listeners', side_effect=listeners), \
                mock.patch.object(ports, 'desktop_backend', side_effect=lambda pid: pid == self.backend), \
                mock.patch.dict(os.environ, {'DOCKER_CONFIG': 'synthetic config',
                                            'DOCKER_AUTH_CONFIG': 'synthetic credential shape',
                                            'DOCKER_HOST': 'ssh://synthetic.invalid', 'TS_SOCKET': 'synthetic-remote'}):
            ports.check_windows_ports(self.root, self.settings, HOST, run=self.run_command, socket_factory=self.socket)

    def test_windows_only_runs_native_serve_and_requires_exclusive_loopback_bind(self):
        self.windows_check()
        self.assertEqual([WINDOWS_SERVE], self.calls)
        self.assertEqual([19081, 19082, 19453], self.bound)
        self.assertEqual(3, self.closed)
        self.option_error = True
        with self.assertRaisesRegex(ports.PortUnavailable, '19081.*exclusive Windows'):
            self.windows_check()
        self.assertEqual(4, self.closed)
        self.assertFalse(self.root.exists())

    def test_windows_same_desktop_proxy_and_exact_owned_door_can_reuse_busy_ports(self):
        self.windows_install()
        journal = self.routed()
        self.native_listeners = {port: [('127.0.0.1', self.backend)] for port in (19081, 19082)}
        self.busy.add(19081)
        self.denied.add(19082)  # Winsock can report access denied for an exclusive existing listener.
        before = journal.read_bytes()
        self.windows_check()
        self.assertEqual([WINDOWS_SERVE, ports.DESKTOP_DOCKER + self.calls[1][3:],
                          ports.DESKTOP_DOCKER + self.calls[2][3:]], self.calls)
        self.assertEqual(before, journal.read_bytes())
        for path in (self.root, self.root / 'owner.json', self.root / 'install.json', journal,
                     Path(self.door['config'])):
            self.assertIn(path, self.checked_acl)

    def test_windows_wildcard_pairing_conflict_and_foreign_model_listener_fail_closed(self):
        self.windows_install()
        self.routed()
        for port, rows in ((19453, [('0.0.0.0', self.backend)]), (19453, [('::', self.backend)]),
                           (19081, [('127.0.0.1', 414)]), (19081, [('127.0.0.1', self.backend),
                                                                   ('0.0.0.0', 414)])):
            with self.subTest(port=port, rows=rows):
                self.native_listeners = {port: rows}
                self.calls.clear()
                with self.assertRaisesRegex(ports.PortConflict, str(port) + '.*unverified TCP listener'):
                    self.windows_check()
                self.assertEqual([WINDOWS_SERVE], self.calls)

    def test_windows_unobserved_acl_and_reserved_port_fail_closed(self):
        self.windows_install()
        journal = self.routed()
        self.bad_acl = journal
        with self.assertRaisesRegex(ports.PortUnavailable, 'publication journal.*unverified'):
            self.windows_check()
        self.assertEqual([], self.calls)
        self.bad_acl = None
        self.denied.add(19453)
        with self.assertRaisesRegex(ports.PortUnavailable, '19453.*could not be observed'):
            self.windows_check()
        self.assertEqual([WINDOWS_SERVE], self.calls)

    def test_windows_tcp_table_handles_ipv4_ipv6_and_network_order(self):
        encoded = {
            socket.AF_INET: struct.pack('<II4s2s2x4s2s2xI', 1, 2, b'\x7f\0\0\1',
                                       (19081).to_bytes(2, 'big'), bytes(4), bytes(2), 413),
            socket.AF_INET6: struct.pack('<I16sI2s2x16sI2s2xII', 1, bytes(16), 0,
                                        (19453).to_bytes(2, 'big'), bytes(16), 0, bytes(2), 2, 414),
        }

        def capture(table, size, order, family, kind, reserved):
            self.assertFalse(order)
            self.assertEqual((3, 0), (kind, reserved))
            blob = encoded[family]
            size._obj.value = len(blob)
            if table is None:
                return ports.INSUFFICIENT_BUFFER
            ctypes.memmove(table, blob, len(blob))
            return 0

        with mock.patch.object(ports.ctypes, 'WinDLL', return_value=SimpleNamespace(
                GetExtendedTcpTable=capture), create=True):
            self.assertEqual({19081: [('127.0.0.1', 413)], 19453: [('::', 414)]},
                             ports.windows_listeners([19081, 19453]))

    def test_free_custom_ports_only_use_native_serve_status_and_close_all_sockets(self):
        self.check()
        self.assertEqual([NATIVE_SERVE], self.calls)
        self.assertEqual([19081, 19082, 19453], self.bound)
        self.assertEqual(3, self.closed)
        self.assertFalse(self.root.exists())

    def test_unrelated_busy_local_port_names_number_and_safe_owner_category(self):
        self.busy.add(19081)
        with self.assertRaisesRegex(ports.PortConflict, '19081.*unverified TCP listener.*embeddings'):
            self.check()
        self.assertEqual([NATIVE_SERVE], self.calls)
        self.assertEqual(3, self.closed)

    def test_same_installed_running_door_and_committed_serve_routes_pass_without_writes(self):
        self.install()
        journal = self.routed()
        before = journal.read_bytes(), journal.stat().st_mtime_ns
        self.busy.update((19081, 19082))
        self.check()
        self.assertEqual(['serve', 'ps', 'inspect'], [argv[1] if argv[0] == 'tailscale' else argv[3]
                                                      for argv in self.calls])
        self.assertEqual(before, (journal.read_bytes(), journal.stat().st_mtime_ns))
        self.assertEqual(3, self.closed)

    def test_pairing_listener_requires_independent_local_availability(self):
        self.install()
        self.routed()
        self.busy.add(19453)
        with self.assertRaisesRegex(ports.PortConflict, '19453.*unverified TCP listener.*pairing'):
            self.check()
        self.assertEqual([NATIVE_SERVE], self.calls)

    def test_door_id_scope_or_live_binding_mismatch_never_inherits_ownership(self):
        self.install()
        self.busy.add(19081)
        clean = copy.deepcopy(self.door)
        cases = [
            {'id': 'd' * 64}, {'project': 'a-foreign-project'}, {'service': 'other'},
            {'config': str(self.root / 'other' / ('c' * 64) / 'compose.json')}, {'running': False},
            {'live': {'8081/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '19081'}]}},
            {'bindings': {'8081/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '19081'}]}},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(ports.PortConflict, '19081'):
                self.door = {**clean, **changes}
                self.check()
        self.door = clean
        self.ids = ''
        with self.assertRaisesRegex(ports.PortConflict, '19081'):
            self.check()

    def test_bind_or_docker_errors_do_not_manufacture_port_evidence(self):
        self.denied.add(19082)
        with self.assertRaisesRegex(ports.PortUnavailable, '19082.*could not be observed'):
            self.check()
        self.assertEqual(2, self.closed)
        self.denied.clear()
        self.install()
        self.busy.add(19081)
        for stage in ('ps', 'inspect'):
            self.command_errors = {stage}
            with self.subTest(stage=stage), self.assertRaisesRegex(ports.PortUnavailable, '19081.*Docker owner'):
                self.check()

    def test_identical_serve_routes_without_journal_and_foreground_occupancy_refuse(self):
        self.routed(journal=False)
        with self.assertRaisesRegex(ports.PortConflict, '18443.*unowned route.*embeddings_https'):
            self.check()
        self.install()
        self.routed()
        self.config['Foreground'] = {'synthetic-session': {'TCP': {'17453': {'TCPForward': '127.0.0.1:19453'}}}}
        with self.assertRaisesRegex(ports.PortConflict, '17453.*unowned route.*pairing_tcp'):
            self.check()
        self.assertEqual([], self.bound)

    def test_pending_serve_effect_and_unreadable_status_fail_closed(self):
        self.install()
        self.routed(pending=0)
        with self.assertRaisesRegex(ports.PortUnavailable, '18443.*uncertain ownership'):
            self.check()
        self.command_errors.add('serve')
        with self.assertRaisesRegex(ports.PortUnavailable, r'route owner could not be observed.*18443, 18444, 17453'):
            self.check()

    def test_selected_serve_ports_are_named_when_the_saved_routes_differ(self):
        self.install()
        self.routed()
        self.settings['forge_ports']['embeddings_https'] = 18453
        with self.assertRaisesRegex(ports.PortUnavailable, r'unverified.*18453, 18444, 17453'):
            self.check()
        self.assertEqual([], self.calls)
        self.assertEqual([], self.bound)

    def test_unowned_data_root_and_invalid_records_refuse_before_probing(self):
        self.record('unrelated', {})
        with self.assertRaises(ports.PortUnavailable):
            self.check()
        self.install()
        self.record('owner.json', {'schema': ports.SCHEMA, 'install_id': 'd' * 16})
        with self.assertRaisesRegex(ports.PortUnavailable, 'owner record'):
            self.check()
        self.assertEqual([], self.calls)
        with mock.patch.object(ports.os, 'name', 'posix'):
            for uid, mode in ((1, 0o600), (0, 0o644)):
                with self.subTest(uid=uid, mode=mode), self.assertRaises(ports.PortUnavailable):
                    ENFORCE_PRIVATE(SimpleNamespace(st_uid=uid, st_mode=stat.S_IFREG | mode), 0o600)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', type=Path, required=True)
    options, remaining = parser.parse_known_args()
    SCRATCH = options.scratch_root.resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
