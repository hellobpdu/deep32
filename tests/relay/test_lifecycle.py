"""Owned lifecycle native-command fakes; no scheduler, Serve, agent or socket."""
import base64
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_install as existing
from deep32_relay import connector, status
from deep32_relay.platform import (NativePlatformAPI, OwnedServeRoute, PlatformAdapter,
                                   ServeAPI, ServiceSpec, render)
from deep32_relay.state import StateError, json_bytes, strict_json


class FakeServe:
    def __init__(self, table=None):
        self.table = table or {}
        self.calls = []
        self.change_unrelated = False

    def inspect(self):
        return strict_json(json_bytes(self.table))

    def add(self, port):
        self.calls.append(('add', port))
        self.table.setdefault('TCP', {})[str(port)] = {'TCPForward': '127.0.0.1:' + str(port)}
        if self.change_unrelated:
            self.table['Web'] = {'unexpected': {}}

    def remove(self, port):
        self.calls.append(('remove', port))
        self.table.get('TCP', {}).pop(str(port), None)


class Lifecycle(unittest.TestCase):
    setUp = existing.Installation.setUp
    tearDown = existing.Installation.tearDown
    install = existing.Installation.install

    def test_explicit_loopback_config_is_strict_and_has_no_route(self):
        self.install(transport='loopback', port=17452)
        config = self.root.read_json('config.json')
        self.assertEqual((config['listener'], config['port'], config['transport']),
                         ('127.0.0.1', 17452, 'loopback'))
        self.assertTrue(config['intake_enabled'])
        self.assertFalse((self.root.root / OwnedServeRoute.RECORD).exists())
        self.assertFalse(connector.check(self.root)['ready'])
        with self.assertRaisesRegex(StateError, 'install-binding-mismatch'):
            self.install(transport='loopback', port=17453)

    def test_invalid_listener_policies_fail_before_creating_root(self):
        for options in ({'transport': 'public'}, {'port': 80}, {'port': True}, {'transport': 'tailnet'}):
            with self.subTest(options=options), self.assertRaises(StateError):
                self.install(**options)
            self.assertFalse(self.root.root.exists())

    def test_install_records_no_usage_qualification_and_binary_hash(self):
        self.install()
        result = self.root.read_json('private/qualification/codex.json')
        self.assertEqual(result['state'], 'unqualified')
        self.assertEqual(result['model_usage'], 'none')
        self.assertEqual(len(result['binary_sha256']), 64)
        self.assertEqual(connector.status(self.root)['qualification']['state'], 'unqualified')

    def test_pair_before_autostart_then_idempotent_registration(self):
        self.install()
        calls = []
        result = connector.pair(self.root, pair_console=lambda root: calls.append(root) or object())
        self.assertTrue(result['paired'])
        self.assertEqual(calls, [self.root])
        self.assertNotIn('pin', json.dumps(result))
        self.install(platform_api=self.api)
        self.assertTrue(connector.validate_receipt(self.root)['startup_registered'])
        self.assertEqual(sum(row[0] == 'register' for row in self.api.calls), 1)

    def _publish_running(self, instance_id):
        receipt = connector.validate_receipt(self.root)
        snapshot = dict(status.unavailable_snapshot(receipt['install_id'], receipt['agent'], receipt['release_id']),
                        pid=os.getpid(), instance_id=instance_id, running=True)
        status.publish_status(self.root, snapshot)

    def test_pair_refuses_while_the_service_process_runs(self):
        self.install()
        self._publish_running(status.process_identity(os.getpid()))
        with self.assertRaisesRegex(StateError, 'relay-stop-required'):
            connector.pair(self.root, pair_console=lambda owner: self.fail('armed'))

    def test_pair_proceeds_after_the_service_stopped_and_left_its_pid(self):
        # systemctl stop ends the relay with SIGTERM, so its last snapshot keeps a
        # pid and running=true. A process that is gone, or a pid now used by a
        # different process, is not the relay.
        self.install()
        live = status.process_identity(os.getpid())
        other = {'windows': 'windows-' + '0' * 16, 'linux': 'linux-1', 'macos': 'macos-1-000000'}[live.split('-')[0]]
        self._publish_running(other)
        calls = []
        result = connector.pair(self.root, pair_console=lambda owner: calls.append(owner) or object())
        self.assertTrue(result['paired'])
        self.assertEqual(calls, [self.root])

    def test_pair_refuses_pending_mutation_without_arming_console(self):
        self.install()
        self.root.write_json('transaction.json', {'synthetic': True})
        with self.assertRaisesRegex(StateError, 'pairing-install-unavailable'):
            connector.pair(self.root, pair_console=lambda owner: self.fail('armed'))

    def test_pair_json_is_refused_before_loading_console_or_native_api(self):
        with patch('deep32_relay.connector.state_base', side_effect=AssertionError('native')):
            output = io.StringIO()
            with patch('sys.stdout', output):
                code = connector.main(['pair', '--relay-id', 'a' * 32, '--json'])
        self.assertEqual(code, 10)
        self.assertEqual(json.loads(output.getvalue())['reason'], 'relay-operation-unavailable')

    def test_route_round_trip_preserves_unrelated_data(self):
        self.install()
        foreign = {'TCP': {'8443': {'HTTPS': True}}, 'Web': {'synthetic:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:8082'}}}}}
        fake = FakeServe(strict_json(json_bytes(foreign)))
        route = OwnedServeRoute(self.root, fake)
        self.assertEqual(route.publish(), {'state': 'ready', 'owned': True})
        self.assertEqual(route.publish()['state'], 'ready')
        self.assertEqual(fake.calls, [('add', 7452)])
        self.assertEqual(route.remove(), {'state': 'off', 'owned': False})
        self.assertEqual(fake.table, foreign)

    def test_matching_but_unowned_route_is_never_adopted_or_removed(self):
        self.install()
        fake = FakeServe({'TCP': {'7452': {'TCPForward': '127.0.0.1:7452'}}})
        route = OwnedServeRoute(self.root, fake)
        for operation in (route.publish, route.remove, route.check):
            with self.assertRaisesRegex(StateError, 'foreign-serve-route'):
                operation()
        self.assertFalse(fake.calls)
        self.assertFalse((self.root.root / OwnedServeRoute.RECORD).exists())

    def test_changed_owned_target_is_never_removed(self):
        self.install()
        fake = FakeServe()
        route = OwnedServeRoute(self.root, fake)
        route.publish()
        fake.table['TCP']['7452'] = {'TCPForward': '127.0.0.1:9999'}
        with self.assertRaisesRegex(StateError, 'foreign-serve-route'):
            route.remove()
        self.assertEqual(fake.calls, [('add', 7452)])

    def test_pending_add_can_resume_but_pending_remove_is_not_ready(self):
        self.install()
        fake = FakeServe()
        route = OwnedServeRoute(self.root, fake)
        route._save('adding')
        self.assertEqual(route.publish()['state'], 'ready')
        route._save('removing')
        self.assertEqual(route.check()['state'], 'unavailable')
        with self.assertRaisesRegex(StateError, 'route-recovery-required'):
            route.publish()
        self.assertEqual(route.remove()['state'], 'off')

    def test_unrelated_mutation_keeps_explicit_pending_journal(self):
        self.install()
        fake = FakeServe()
        fake.change_unrelated = True
        route = OwnedServeRoute(self.root, fake)
        with self.assertRaisesRegex(StateError, 'serve-verification-failed'):
            route.publish()
        self.assertEqual(self.root.read_json(OwnedServeRoute.RECORD)['phase'], 'adding')

    def test_funnel_or_malformed_table_does_not_mutate(self):
        self.install()
        for table in ({'AllowFunnel': {'synthetic:7452': True}}, {'TCP': []}):
            fake = FakeServe(table)
            with self.assertRaises(StateError):
                OwnedServeRoute(self.root, fake).publish()
            self.assertFalse(fake.calls)

    def test_stop_intent_only_clears_with_owned_clean_snapshot(self):
        self.install()
        request = {'schema': 'deep32.relay-stop.v1', 'install_id': 'a' * 32}
        self.root.write_json('runtime/stop-request.json', request)
        with self.assertRaisesRegex(StateError, 'cleanup-unconfirmed'):
            self.install()
        receipt = connector.validate_receipt(self.root)
        value = status.unavailable_snapshot('a' * 32, 'codex', receipt['release_id'])
        status.publish_status(self.root, value)
        self.install()
        self.assertFalse((self.root.root / 'runtime/stop-request.json').exists())

    def test_doctor_missing_and_present_snapshots_are_read_only(self):
        self.install()
        before = {str(path): path.read_bytes() for path in self.root.root.rglob('*') if path.is_file()}
        writes = self.security.writes
        with patch('subprocess.run', side_effect=AssertionError('native')), patch('socket.socket', side_effect=AssertionError('network')):
            result = status.doctor_status(self.root)
        self.assertEqual(result['state'], 'missing')
        self.assertEqual(writes, self.security.writes)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.root.rglob('*') if path.is_file()})

    def test_package_copies_only_named_provider_lock(self):
        (self.source / 'tls-provider-lock.json').write_text('{"synthetic":true}')
        (self.source / 'unreviewed.json').write_text('{"synthetic":true}')
        _, files = connector.source_package(self.source)
        self.assertIn('tls-provider-lock.json', files)
        self.assertNotIn('unreviewed.json', files)

    def test_remove_reports_startup_readback_leftover_without_deleting_code(self):
        self.install(platform_api=self.api)
        with patch.object(self.api, 'unregister', return_value=None):
            result = connector.remove(self.root, platform_api=self.api)
        self.assertEqual(result['removal'], {'complete': False, 'leftovers': ['startup']})
        self.assertTrue(self.root.path('config.json').exists())
        self.assertEqual(self.root.read_json('transaction.json')['phase'], 'removing')
        self.assertTrue(connector.remove(self.root, platform_api=self.api)['removal']['complete'])

    def test_remove_reads_back_each_file_and_resumes_partial_removal(self):
        self.install(platform_api=self.api)
        retained = self.root.path('config.json')
        original = Path.unlink
        def unlink(path, *args, **kw):
            if path != retained:
                return original(path, *args, **kw)
        with patch.object(Path, 'unlink', unlink):
            result = connector.remove(self.root, platform_api=self.api)
        self.assertEqual(result['removal'], {'complete': False, 'leftovers': ['config.json']})
        self.assertTrue(retained.exists())
        self.assertTrue(connector.remove(self.root, platform_api=self.api)['removal']['complete'])
        self.assertFalse(retained.exists())

    def test_remove_reads_back_owned_serve_route_and_preserves_other_routes(self):
        fake = FakeServe({'TCP': {'8443': {'HTTPS': True}}})
        self.install(transport='tailnet', route_api=fake, platform_api=self.api)
        with patch.object(fake, 'remove', return_value=None):
            result = connector.remove(self.root, platform_api=self.api, route_api=fake)
        self.assertEqual(result['removal'], {'complete': False, 'leftovers': ['serve-route']})
        self.assertIn('7452', fake.table['TCP'])
        self.assertTrue(connector.remove(self.root, platform_api=self.api, route_api=fake)['removal']['complete'])
        self.assertEqual(fake.table, {'TCP': {'8443': {'HTTPS': True}}})

    def test_remove_manual_service_requires_released_lifetime_lease(self):
        self.install()
        with self.root.lock('service.lock'):
            result = connector.remove(self.root)
        self.assertEqual(result['removal'], {'complete': False, 'leftovers': ['service']})
        self.assertTrue(self.root.path('config.json').exists())
        self.assertTrue(connector.remove(self.root)['removal']['complete'])

    def test_registered_install_also_retains_code_until_service_lease_released(self):
        self.install(platform_api=self.api)
        with self.root.lock('service.lock'):
            result = connector.remove(self.root, platform_api=self.api)
        self.assertEqual(result['removal'], {'complete': False, 'leftovers': ['service']})
        self.assertTrue(self.root.path('config.json').exists())
        self.assertTrue(connector.remove(self.root, platform_api=self.api)['removal']['complete'])

    def test_service_lease_is_held_during_exact_file_unlinks(self):
        self.install(platform_api=self.api)
        original = Path.unlink
        retained = self.root.path('config.json')
        def unlink(path, *args, **kw):
            if path == retained:
                with self.assertRaisesRegex(StateError, 'operation-pending'):
                    with self.root.lock('service.lock'):
                        self.fail('service lease released before file removal')
            return original(path, *args, **kw)
        with patch.object(Path, 'unlink', unlink):
            self.assertTrue(connector.remove(self.root, platform_api=self.api)['removal']['complete'])

    def test_provider_failure_is_reported_and_can_be_retried(self):
        self.install(platform_api=self.api)
        self.root.write_json('private/provider-install.json', {'synthetic': True})
        with patch('deep32_relay.provider.remove_provider', side_effect=OSError('SYNTHETIC_PRIVATE_CANARY')):
            result = connector.remove(self.root, platform_api=self.api)
        self.assertEqual(result['removal'], {'complete': False, 'leftovers': ['provider']})
        with patch('deep32_relay.provider.remove_provider', return_value=[]):
            self.assertTrue(connector.remove(self.root, platform_api=self.api)['removal']['complete'])


class NativeCommands(unittest.TestCase):
    setUp = existing.Installation.setUp
    tearDown = existing.Installation.tearDown
    install = existing.Installation.install

    def native_api(self, platform, runner):
        if platform == 'windows':
            spec = ServiceSpec('a' * 32, 'C:/synthetic/python.exe', 'C:/synthetic/run.py', 'C:/synthetic/state', platform)
        else:
            spec = ServiceSpec('a' * 32, '/synthetic/python', '/synthetic/run.py', '/synthetic/state', platform)
        return spec, NativePlatformAPI(self.root, spec, render(spec)[1], runner=runner,
            home=self.base / 'synthetic-home', uid=12345, stopped=lambda: True)

    def test_linux_registration_and_removal_exact_commands(self):
        self.install()
        calls = []
        spec, api = self.native_api('linux', lambda argv, **kw: calls.append(tuple(argv)) or (0, b''))
        adapter = PlatformAdapter(api)
        self.assertTrue(adapter.register(spec, render(spec)[1]))
        self.assertEqual(calls, [('/usr/bin/systemctl', '--user', 'daemon-reload'),
            ('/usr/bin/systemctl', '--user', 'enable', spec.name + '.service')])
        self.assertTrue(adapter.start(spec, render(spec)[1]))
        self.assertEqual(calls[2:], [('/usr/bin/systemctl', '--user', 'reset-failed', spec.name + '.service'),
            ('/usr/bin/systemctl', '--user', 'start', spec.name + '.service')])
        adapter.remove(spec, render(spec)[1])
        self.assertEqual(calls[-2:], [('/usr/bin/systemctl', '--user', 'disable', spec.name + '.service'),
                                    ('/usr/bin/systemctl', '--user', 'daemon-reload')])
        self.assertFalse(api.location.exists())

    def test_macos_literal_launchagent_scope(self):
        self.install()
        calls = []
        spec, api = self.native_api('macos', lambda argv, **kw: calls.append(tuple(argv)) or (0, b''))
        PlatformAdapter(api).register(spec, render(spec)[1])
        PlatformAdapter(api).remove(spec, render(spec)[1])
        self.assertEqual([row[:3] for row in calls], [('/bin/launchctl', 'bootstrap', 'gui/12345'),
                                                    ('/bin/launchctl', 'bootout', 'gui/12345')])

    def test_windows_uses_fixed_com_not_secret_or_shell_interpolation(self):
        self.install()
        scripts, payloads = [], []
        current = [None]
        def runner(argv, **kw):
            self.assertIn('-NonInteractive', argv)
            script = base64.b64decode(argv[-1]).decode('utf-16le')
            scripts.append(script)
            payloads.append(kw.get('input'))
            if 'ReadToEnd' in script:
                current[0] = kw['input']
                return 0, b''
            if 'DeleteTask' in script:
                current[0] = None
                return 0, b''
            return 0, current[0] if current[0] is not None else b'ABSENT\n'
        spec, api = self.native_api('windows', runner)
        self.assertTrue(PlatformAdapter(api).register(spec, render(spec)[1]))
        PlatformAdapter(api).remove(spec, render(spec)[1])
        combined = '\n'.join(scripts)
        self.assertIn('InteractiveToken', render(spec)[1].decode())
        self.assertIn('RegisterTask($n,$x,2,$null,$null,3,$null)', combined)
        self.assertNotIn('SYNTHETIC_ACCOUNT_CANARY', combined)
        self.assertTrue(any(item == render(spec)[1] for item in payloads))

    def test_foreign_startup_file_is_not_overwritten_or_removed(self):
        self.install()
        calls = []
        spec, api = self.native_api('linux', lambda argv, **kw: calls.append(argv) or (0, b''))
        api.location.parent.mkdir(parents=True)
        api.location.write_bytes(b'SYNTHETIC_FOREIGN_ENTRY')
        for operation in (lambda: PlatformAdapter(api).register(spec, render(spec)[1]),
                          lambda: PlatformAdapter(api).remove(spec, render(spec)[1])):
            with self.assertRaisesRegex(StateError, 'foreign-startup-entry'):
                operation()
        self.assertFalse(calls)
        self.assertEqual(api.location.read_bytes(), b'SYNTHETIC_FOREIGN_ENTRY')

    def test_serve_commands_have_one_owned_port_and_no_reset(self):
        calls = []
        executable = str(self.base / 'synthetic-tailscale.exe')
        def runner(argv, **kw):
            calls.append(tuple(argv))
            return 0, b'{}'
        api = ServeAPI(executable, runner)
        self.assertEqual(api.inspect(), {})
        api.add(17452)
        api.remove(17452)
        self.assertEqual(calls, [(executable, 'serve', 'status', '--json'),
            (executable, 'serve', '--bg', '--tcp=17452', 'tcp://127.0.0.1:17452'),
            (executable, 'serve', '--tcp=17452', 'off')])

    def test_native_stop_uses_exact_unit_readback_without_scope_observer(self):
        self.install()
        calls = []
        def runner(argv, **kw):
            calls.append(tuple(argv))
            return (3, b'inactive\n') if 'is-active' in argv else (0, b'')
        spec, api = self.native_api('linux', runner)
        api.stopped = None
        with patch.object(api, '_cooperative_stop', return_value=True):
            self.assertTrue(api.stop(spec.name))
        self.assertEqual(calls, [('/usr/bin/systemctl', '--user', 'stop', spec.name + '.service'),
            ('/usr/bin/systemctl', '--user', 'is-active', spec.name + '.service')])

    def test_native_stop_running_unit_fails_readback(self):
        self.install()
        spec, api = self.native_api('linux', lambda argv, **kw: (0, b'active\n'))
        api.stopped = None
        with patch.object(api, '_cooperative_stop', return_value=False):
            with self.assertRaisesRegex(StateError, 'startup-stop-verification-failed'):
                api.stop(spec.name)

    def test_macos_stop_reads_exact_launchagent_absence(self):
        self.install()
        calls = []
        def runner(argv, **kw):
            calls.append(tuple(argv))
            return (113, b'') if 'print' in argv else (0, b'')
        spec, api = self.native_api('macos', runner)
        api.stopped = None
        with patch.object(api, '_cooperative_stop', return_value=False):
            self.assertTrue(api.stop(spec.name))
        target = 'gui/12345/org.deep32.distribution.' + spec.name
        self.assertEqual(calls, [('/bin/launchctl', 'bootout', target),
                                 ('/bin/launchctl', 'print', target)])


if __name__ == '__main__':
    unittest.main()
