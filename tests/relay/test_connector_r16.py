"""R2 console/retry/login regressions; native effects are scripted fakes."""
import io
import os
from pathlib import Path
import subprocess
import socket
from unittest.mock import patch
import unittest

import test_install as existing
from test_lifecycle import FakeServe
from deep32_relay import connector, engine
from deep32_relay.platform import CommandRunner, OwnedServeRoute
from deep32_relay.state import OwnedRoot, StateError


class ConnectorR16(unittest.TestCase):
    def setUp(self):
        existing.Installation.setUp(self)
        guard = patch.object(socket, 'socket', side_effect=AssertionError('offline socket refused'))
        guard.start()
        self.addCleanup(guard.stop)
    tearDown = existing.Installation.tearDown
    install = existing.Installation.install

    def test_login_both_agents_selects_only_relay_profile_then_no_model_check(self):
        for agent in ('codex', 'claude'):
            with self.subTest(agent=agent):
                # Each agent has a separate generated installation.
                ident = ('a' if agent == 'codex' else 'b') * 32
                owner = OwnedRoot(self.base / ident, self.security)
                connector.install(owner, agent, str(self.agent), source=self.source,
                    python_binary=str(self.python), install_id=ident,
                    platform='windows' if os.name == 'nt' else 'linux')
                seen = []
                def run(argv, **kwargs):
                    seen.append((argv, kwargs))
                    self.assertEqual(argv[1:], ['login'] if agent == 'codex' else ['auth', 'login'])
                    self.assertEqual(kwargs['env']['CODEX_HOME' if agent == 'codex' else 'CLAUDE_CONFIG_DIR'],
                                     str(owner.root / 'profiles' / agent))
                    self.assertNotIn('ANTHROPIC_API_KEY', kwargs['env'])
                    self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
                    self.assertNotIn('PATH', kwargs['env'])
                    self.assertTrue(Path(kwargs['cwd']).is_relative_to(owner.root / 'runtime/sessions'))
                    self.assertFalse(kwargs['shell'])
                    self.assertNotIn('stdout', kwargs)
                    return subprocess.CompletedProcess(argv, 0)
                with patch.object(engine, 'qualify_install', return_value={'state': 'qualified'}) as checked:
                    connector.login(owner, runner=run)
                self.assertEqual(len(seen), 1)
                checked.assert_called_once()
                self.assertEqual(checked.call_args.kwargs['executable'], str(self.agent))
                self.assertFalse(Path(seen[0][1]['cwd']).exists())

    def test_failed_login_does_not_check_or_change_profile(self):
        self.install()
        with patch.object(engine, 'qualify_install') as checked:
            with self.assertRaisesRegex(StateError, 'agent-login-failed'):
                connector.login(self.root, runner=lambda *a, **k: subprocess.CompletedProcess(a, 1))
        checked.assert_not_called()
        self.assertEqual(list(self.root.path('profiles/codex').iterdir()), [])

    def test_login_refuses_running_service_before_launch(self):
        self.install()
        with self.root.lock('service.lock'):
            with self.assertRaises(StateError):
                connector.login(self.root, runner=lambda *a, **k: self.fail('login launched'))

    def test_login_refuses_unsafe_profile_before_native_auth(self):
        self.install()
        self.security.denied.add(str(self.root.path('profiles/codex')))
        with self.assertRaisesRegex(StateError, 'unsafe-permissions'):
            connector.login(self.root, runner=lambda *a, **k: self.fail('login launched'))

    def test_signed_out_check_names_the_native_login_command(self):
        result = {'agent': 'claude', 'profile_alias': 'relay-claude', 'state': 'installed-unavailable',
                  'install_id': 'a' * 32, 'ready': False,
                  'qualification': {'state': 'unqualified', 'reason': 'claude-sign-in-required'}}
        output = io.StringIO()
        with patch.object(connector, 'state_base', return_value=self.base), \
             patch.object(connector, 'validate_receipt', return_value={'agent': 'claude'}), \
             patch.object(connector, 'check', return_value=result), \
             patch('sys.stdout', io.StringIO()), patch('sys.stderr', output):
            self.assertEqual(connector.main(['check', '--relay-id', 'a' * 32, '--agent', 'claude']), 10)
        self.assertIn('claude-sign-in-required', output.getvalue())
        self.assertIn('connect --relay login --agent claude', output.getvalue())

    def test_failed_provider_install_implicit_retry_reuses_root(self):
        base = self.base / 'state'
        base.mkdir()
        real_root = OwnedRoot
        roots = []
        def factory(path):
            return real_root(path, self.security)
        def provider(owner):
            roots.append(owner.root)
            if len(roots) == 1:
                raise StateError('synthetic-provider-failure')
            return str(self.python)
        result = {'agent': 'codex', 'profile_alias': 'relay-codex', 'state': 'installed',
                  'install_id': 'a' * 32, 'qualification': {'state': 'qualified'}, 'ready': False}
        with patch.object(connector, 'state_base', return_value=base), \
             patch.object(connector, 'OwnedRoot', side_effect=factory), \
             patch('deep32_relay.provider.install_provider', side_effect=provider), \
             patch.object(connector, 'install', return_value=result), \
             patch.object(connector, 'validate_receipt', return_value={}), \
             patch('sys.stdout', io.StringIO()), patch('sys.stderr', io.StringIO()):
            args = ['install', '--agent', 'codex', '--agent-binary', str(self.agent), '--no-autostart']
            self.assertEqual(connector.main(args), 10)
            self.assertEqual(connector.main(args), 0)
        self.assertEqual(roots[0], roots[1])
        self.assertEqual(len(list(base.iterdir())), 1)

    def test_concurrent_initial_selectors_choose_the_same_public_id(self):
        base = self.base / 'new-state'
        with patch.object(connector, 'state_base', return_value=base):
            first = connector._select_existing('claude', for_install=True)
            self.assertEqual(first, connector._select_existing('claude', for_install=True))
            self.assertNotEqual(first, connector._select_existing('codex', for_install=True))

    def test_failed_route_install_retry_reuses_owned_route(self):
        route = FakeServe({'TCP': {'8443': {'HTTPS': True}}})
        self.api.fail_register = True
        with self.assertRaisesRegex(StateError, 'startup-registration-failed'):
            self.install(platform_api=self.api, transport='tailnet', route_api=route)
        self.assertEqual(self.root.read_json(OwnedServeRoute.RECORD)['phase'], 'ready')
        self.api.fail_register = False
        self.install(platform_api=self.api, transport='tailnet', route_api=route)
        self.assertEqual(route.calls, [('add', 7452)])
        self.assertEqual(route.table['TCP']['8443'], {'HTTPS': True})

    def test_failed_account_install_is_selected_again(self):
        self.install()
        with patch.object(connector, 'state_base', return_value=self.base), \
             patch.object(connector, 'OwnedRoot', side_effect=lambda path: OwnedRoot(path, self.security)):
            self.assertEqual(connector._select_existing('codex', for_install=True), 'a' * 32)

    def test_local_console_has_fixed_state_code_and_linux_fix(self):
        stderr = io.StringIO()
        with patch.object(connector, 'state_base', side_effect=StateError('tailscale-operator-required')), \
             patch('sys.stderr', stderr):
            self.assertEqual(connector.main(['status', '--agent', 'claude']), 10)
        self.assertIn('code: tailscale-operator-required', stderr.getvalue())
        self.assertIn('Run this one-time Linux fix: sudo tailscale set --operator=$USER', stderr.getvalue())

    def test_login_requires_agent_and_local_console(self):
        for args in (['login', '--relay-id', 'a' * 32],
                     ['login', '--relay-id', 'a' * 32, '--agent', 'claude', '--json']):
            with patch('sys.stdout', io.StringIO()), patch('sys.stderr', io.StringIO()):
                self.assertEqual(connector.main(args), 10)

    def test_serve_denial_only_on_linux_and_serve_commands(self):
        for system, command, expected in (
            ('linux', 'serve', 'tailscale-operator-required'),
            ('win32', 'serve', 'platform-operation-failed'),
            ('linux', 'status', 'platform-operation-failed')):
            with self.subTest(system=system, command=command):
                completed = subprocess.CompletedProcess([], 1, stdout=b'',
                    stderr=b'Access denied: serve config denied\nSYNTHETIC_PRIVATE_DIAGNOSTIC')
                with patch('deep32_relay.platform.sys.platform', system), \
                     patch('deep32_relay.platform.subprocess.run', return_value=completed):
                    with self.assertRaisesRegex(StateError, '^' + expected + '$'):
                        CommandRunner()(('/synthetic/tailscale', command, '--bg'))
