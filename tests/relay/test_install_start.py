"""The installed service starts only after the install is committed and its lock released.

Live evidence (Linux headend, 19 November 2026): `--relay install` after `--relay remove` left the systemd user
unit failed. Registration ran `systemctl --user enable --now` inside the install window, so the service's own
startup checks met the removed receipt, a missing receipt or the held operation lock and exited 10; systemd stopped
after StartLimitBurst=3. These fakes model systemd and Task Scheduler: a start request runs the real service entry
point (service.main) once, synchronously, against the owned root, with its native agent, TLS provider, pairing and
socket edges replaced by a qualified, paired stand-in. A failed start stays failed: systemd's 10 second restarts are
collapsed, which is the live case of an install window that outlasts them; Task Scheduler never retries.
"""
import base64
from contextlib import ExitStack, redirect_stdout
import io
import os
import unittest
from unittest.mock import patch

import test_install as existing
from deep32_relay import connector, daemon, engine, service
from deep32_relay import platform as relay_platform
from deep32_relay.platform import ServiceSpec
from deep32_relay.state import OwnedRoot, StateError


class ReadyAgent:
    ready = True
    models = ('synthetic-model',)
    efforts = ('default',)


class PairedIdentity:
    def __init__(self, owner):
        pass

    def load_or_create(self, provider):
        return object()

    def load_peer(self):
        return object()


class Listener:
    def __init__(self, *args, **kw):
        pass


class Reactor:
    """Reached only after every startup check passed; one step, then a clean close."""
    def __init__(self, *args, **kw):
        self.closed = False

    def start(self):
        pass

    def step(self):
        self.closed = True

    def close(self):
        return True


class FakeSystemd:
    """`systemctl --user` for one user manager, with CommandRunner's exit-code rule."""
    def __init__(self, start_service):
        self.start_service = start_service
        self.calls = []
        self.state = {}
        self.enabled = set()
        self.started = []
        self.fail_start = False

    def __call__(self, argv, *, input=None, allowed=(0,)):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv[:2] != ('/usr/bin/systemctl', '--user'):
            raise AssertionError('unexpected native command')
        verb, unit, code, output = argv[2], argv[-1], 0, b''
        if verb == 'enable':
            self.enabled.add(unit)
            if '--now' in argv:
                self._start(unit)
        elif verb == 'start':
            if self.fail_start:
                code = 1
            else:
                self._start(unit)
        elif verb == 'reset-failed':
            if self.state.get(unit) == 'failed':
                self.state[unit] = 'inactive'
        elif verb == 'stop':
            self.state[unit] = 'inactive'
        elif verb == 'is-active':
            value = self.state.get(unit, 'inactive')
            code, output = (0 if value == 'active' else 3), value.encode('ascii') + b'\n'
        elif verb == 'disable':
            self.enabled.discard(unit)
        elif verb != 'daemon-reload':
            raise AssertionError('unexpected systemctl verb')
        if code not in allowed:
            raise StateError('platform-operation-failed')
        return code, output

    def _start(self, unit):
        if self.state.get(unit) == 'active':
            return
        self.started.append(unit)
        self.state[unit] = 'active' if self.start_service() else 'failed'


class FakeScheduler:
    """Task Scheduler behind NativePlatformAPI's fixed PowerShell scripts."""
    def __init__(self, start_service):
        self.start_service = start_service
        self.task = None
        self.running = False
        self.started = 0
        self.scripts = []

    def __call__(self, argv, *, input=None, allowed=(0,)):
        script = base64.b64decode(argv[-1]).decode('utf-16le')
        self.scripts.append(script)
        if 'RegisterTask(' in script:
            self.task = input
            if '.Run(' in script:
                self._run()
            return 0, b''
        if "'STARTED'" in script:
            if not self.running:
                self._run()
            return 0, b'STARTED\n'
        if '.Stop(0)' in script:
            self.running = False
            return 0, b'STOPPED\n'
        if 'DeleteTask(' in script:
            self.task = None
            return 0, b''
        if '$t.Xml' in script:
            return 0, self.task if self.task is not None else b'ABSENT\n'
        raise AssertionError('unexpected scheduler script')

    def _run(self):
        # MultipleInstancesPolicy IgnoreNew and no restart setting: one attempt.
        self.started += 1
        self.running = self.start_service()


def command_branch(spec, platform):
    """The connector's spec for this host, routed to the modelled platform's command branch.

    ServiceSpec refuses a Windows path for a Linux unit, so on a Windows host the Linux branch gets the same
    fields without that validation. The fake writes and reads back the connector's definition bytes unchanged.
    """
    if spec.platform == platform:
        return spec
    value = object.__new__(ServiceSpec)
    for name in ('install_id', 'python', 'script', 'root'):
        object.__setattr__(value, name, getattr(spec, name))
    object.__setattr__(value, 'platform', platform)
    return value


class InstallStartsServiceAfterCommit(unittest.TestCase):
    install = existing.Installation.install

    def setUp(self):
        existing.Installation.setUp(self)
        self.addCleanup(existing.Installation.tearDown, self)
        self.runs = []
        fixture_security = self.security

        class FixtureRoot(OwnedRoot):
            def __init__(self, path, security=None):
                super().__init__(path, security or fixture_security)

        stack = ExitStack()
        self.addCleanup(stack.close)
        for target, value in (
                ('deep32_relay.state.OwnedRoot', FixtureRoot),
                ('deep32_relay.engine.qualification_status', lambda *a, **k: {'state': 'qualified', 'reason': 'none'}),
                ('deep32_relay.engine.native_from_owned', lambda *a, **k: ReadyAgent()),
                ('deep32_relay.tls.provider_readiness', lambda *a, **k: True),
                ('deep32_relay.tls.load_provider', lambda: object()),
                ('deep32_relay.identity.IdentityStore', PairedIdentity),
                ('deep32_relay.transport.Listener', Listener)):
            stack.enter_context(patch(target, value))
        stack.enter_context(patch.object(daemon, 'RelayReactor', Reactor))
        self.qualify = stack.enter_context(patch.object(engine, 'qualify_install', return_value={}))
        for target in ('socket.socket', 'socket.create_connection', 'subprocess.Popen', 'subprocess.run'):
            stack.enter_context(patch(target, side_effect=AssertionError('offline effect refused')))

    def start_service(self):
        """One service start as the native manager would run it: service.main --root ROOT."""
        try:
            receipt = self.root.read_json('install.json')['state']
        except FileNotFoundError:
            receipt = None
        seen = {'receipt': receipt, 'journal': self.root.path('transaction.json').exists(),
                'attempt': self.root.path('install-attempt.json').exists()}
        output = io.StringIO()
        with redirect_stdout(output):
            seen['code'] = service.main(['--root', str(self.root.root)])
        self.runs.append(seen)
        return seen['code'] == 0

    def native(self, platform):
        test = self
        self.manager = FakeSystemd(self.start_service) if platform == 'linux' else FakeScheduler(self.start_service)

        class Fixture(relay_platform.NativePlatformAPI):
            def __init__(self, owner, spec, definition):
                super().__init__(owner, command_branch(spec, platform), definition, runner=test.manager,
                                 home=test.base / 'synthetic-home', uid=12345)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(relay_platform, 'NativePlatformAPI', Fixture))
        self.unit = 'deep32-relay-' + 'a' * 32 + '.service'

    def install_native(self):
        return self.install(platform_api='native', transport='loopback')

    def assert_started_after_commit(self):
        self.assertEqual(self.runs[-1], {'receipt': 'installed-unavailable', 'journal': False,
                                         'attempt': False, 'code': 0})

    def test_linux_remove_then_install_leaves_unit_active(self):
        self.native('linux')
        self.install_native()
        self.assertTrue(connector.remove(self.root, platform_api='native')['removal']['complete'])
        self.assertEqual(self.manager.state[self.unit], 'inactive')
        result = self.install_native()
        self.assertTrue(result['startup_registered'])
        self.assertEqual(self.manager.state[self.unit], 'active')
        self.assertIn(self.unit, self.manager.enabled)
        self.assert_started_after_commit()
        self.assertNotIn(('/usr/bin/systemctl', '--user', 'enable', '--now', self.unit), self.manager.calls)

    def test_linux_fresh_install_leaves_unit_active(self):
        self.root.create()
        self.root.write_json('owner.json', {'schema': 'deep32.relay-owner.v1', 'install_id': 'a' * 32})
        with self.root.lock():
            connector._record_attempt(self.root, 'a' * 32, 'codex', str(self.agent))
        self.native('linux')
        self.install_native()
        self.assertEqual(self.manager.state[self.unit], 'active')
        self.assertEqual(len(self.manager.started), 1)
        self.assert_started_after_commit()

    def test_linux_staged_then_autostart_install_leaves_unit_active(self):
        # Documented order: --no-autostart, pair, then repeat the install without it.
        self.install(transport='loopback')
        self.native('linux')
        result = self.install_native()
        self.assertTrue(result['startup_registered'])
        self.assertEqual(self.manager.state[self.unit], 'active')
        self.assert_started_after_commit()

    @unittest.skipUnless(os.name == 'nt', 'Task Scheduler definitions need Windows paths')
    def test_windows_remove_then_install_leaves_task_running(self):
        self.native('windows')
        self.install_native()
        self.assertTrue(connector.remove(self.root, platform_api='native')['removal']['complete'])
        self.assertFalse(self.manager.running)
        self.install_native()
        self.assertTrue(self.manager.running)
        self.assertEqual(self.manager.started, 2)
        self.assert_started_after_commit()
        self.assertFalse(any('RegisterTask(' in script and '.Run(' in script for script in self.manager.scripts))

    def test_failed_install_unregisters_without_starting(self):
        self.native('linux')
        self.qualify.side_effect = StateError('synthetic-qualification-failure')
        with self.assertRaisesRegex(StateError, 'synthetic-qualification-failure'):
            self.install_native()
        self.assertEqual(self.manager.started, [])
        self.assertNotIn(self.unit, self.manager.enabled)
        self.assertFalse((self.base / 'synthetic-home/.config/systemd/user' / self.unit).exists())
        self.assertEqual(self.root.read_json('transaction.json')['phase'], 'registered')

    def test_start_failure_keeps_committed_install_and_rerun_starts(self):
        self.native('linux')
        self.manager.fail_start = True
        with self.assertRaisesRegex(StateError, 'platform-operation-failed'):
            self.install_native()
        receipt = connector.validate_receipt(self.root)
        self.assertTrue(receipt['startup_registered'])
        self.assertFalse(self.root.path('transaction.json').exists())
        self.manager.fail_start = False
        self.install_native()
        self.assertEqual(self.manager.state[self.unit], 'active')
        self.assert_started_after_commit()

    def service(self, environ):
        """One start of service.main with these DEEP32_RELAY_* settings: (exit code, printed reasons, settings)."""
        import json
        seen = []

        self.agents = []

        class Recording(Reactor):
            def __init__(inner, *args, **kw):
                super().__init__(*args, **kw)
                seen.append(kw.get('settings'))
                self.agents.append(args[1])
                self.options = kw

        kept = {key: value for key, value in os.environ.items()
                if not key.startswith('DEEP32_RELAY_') or key == 'DEEP32_RELAY_TEST_ROOT'}
        output = io.StringIO()
        with patch.object(daemon, 'RelayReactor', Recording), patch.dict(os.environ, dict(kept, **environ), clear=True), \
                redirect_stdout(output):
            code = service.main(['--root', str(self.root.root)])
        printed = output.getvalue().strip()
        return code, (json.loads(printed)['reasons'] if printed else None), seen

    def test_the_service_reads_its_settings_from_the_environment_at_start(self):
        self.install(transport='loopback')
        code, reasons, seen = self.service({})
        self.assertEqual((code, reasons, [item.idle_seconds for item in seen]), (0, None, [900]))
        code, reasons, seen = self.service({'DEEP32_RELAY_IDLE_SECONDS': '300'})
        self.assertEqual((code, [item.idle_seconds for item in seen]), (0, [300]))
        # the receipt and the recorded configuration are what they were: a setting is not part of them
        self.assertEqual(self.root.read_json('config.json')['idle_timeout_seconds'], 60)
        connector.validate_receipt(self.root)

    def test_the_pin_reaches_the_agent_at_start(self):
        self.install(transport='loopback')          # this fixture installs a Codex relay
        code, reasons, seen = self.service({'DEEP32_RELAY_MODEL': 'gpt-synthetic-9', 'DEEP32_RELAY_EFFORT': 'high'})
        self.assertEqual((code, self.agents[0].pin_model, self.agents[0].pin_effort), (0, 'gpt-synthetic-9', 'high'))
        code, reasons, seen = self.service({})
        self.assertEqual((code, self.agents[0].pin_model, self.agents[0].pin_effort), (0, '', ''))
        code, reasons, seen = self.service({'DEEP32_RELAY_MODEL': '--model'})
        self.assertEqual((code, reasons, self.agents), (10, ['invalid-relay-setting'], []))

    def test_answer_records_are_written_only_when_asked_for(self):
        self.install(transport='loopback')
        self.service({})
        self.assertIsNone(self.options['on_answer'])
        self.assertTrue(callable(self.options['on_turn']))          # the turn log is always there
        self.service({'DEEP32_RELAY_ANSWER_RECORD': 'on'})
        self.assertEqual(self.options['on_answer'].__self__.__class__.__name__, 'AnswerRecords')
        self.assertFalse(self.root.path('logs/answers').exists())    # nothing is written before an answer

    def test_a_setting_that_is_not_valid_stops_the_service_and_is_named(self):
        self.install(transport='loopback')
        code, reasons, seen = self.service({'DEEP32_RELAY_IDLE_SECONDS': 'soon'})
        self.assertEqual((code, reasons, seen), (10, ['invalid-relay-setting'], []))
        output = io.StringIO()
        with patch.dict(os.environ, {'DEEP32_RELAY_IDLE_SECONDS': '9000'}), redirect_stdout(output):
            self.assertEqual(service.main(['--root', str(self.root.root), '--check']), 10)
        self.assertIn('invalid-relay-setting', output.getvalue())


if __name__ == '__main__':
    unittest.main()
