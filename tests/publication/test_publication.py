#!/usr/bin/env python3
"""Offline publication fixtures; no Tailscale, service or network command runs.

python -I -B tests/publication/test_publication.py --tmp SCRATCH
"""
import argparse
import asyncio
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
RUNTIME = REPO / 'src/workstation/distrib/site/usr/local/d32'
sys.path.insert(0, str(RUNTIME))
import publication as pub

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
SCRATCH = None


def fixture(name):
    return json.loads((FIXTURES / (name + '.json')).read_text(encoding='ascii'))


class FakePlatform:
    def __init__(self, root):
        self.root = root
        self.node = fixture('signed-in')
        self.config = fixture('serve-empty')
        self.events = []
        self.fail = None

    def status(self):
        self.events.append('status')
        return deepcopy(self.node)

    def serve_status(self):
        self.events.append('serve-status')
        return deepcopy(self.config)

    def start_gateway(self, host):
        self.events.append('gateway-start')
        (self.root / 'etc/d32/state/edge_front').write_bytes(b'on\n')

    def stop_gateway_front(self, previous):
        self.events.append('gateway-stop-front')
        (self.root / 'etc/d32/state/edge_front').write_bytes((previous + '\n').encode('ascii'))

    def verify_gateway(self, host, address):
        self.events.append(('verify', host, address))
        if self.fail in ('gateway', 'gateway-once'):
            if self.fail == 'gateway-once':
                self.fail = None
            pub.refuse('gateway-unverified', 'The synthetic gateway is unavailable; restore it and run d32-publish resume.')

    def serve(self, args):
        self.events.append(tuple(args))
        if self.fail == 'command':
            pub.refuse('platform-unavailable', 'The synthetic Serve command failed; restore it and run d32-publish resume.')
        host = self.node['Self']['DNSName'].rstrip('.')
        if '--set-path=/mcp' in args:
            table = self.config.setdefault('Web', {}).setdefault(host + ':443', {}).setdefault('Handlers', {})
            if args[-1] == 'off':
                table.pop('/mcp', None)
                if not table:
                    self.config['Web'].pop(host + ':443')
                    self.config.get('TCP', {}).pop('443', None)
            else:
                self.config.setdefault('TCP', {})['443'] = {'HTTPS': True}
                table['/mcp'] = deepcopy(pub.PROXY)
                if self.fail == 'strip-prefix':
                    table['/mcp']['Proxy'] = 'http://127.0.0.1:8766'
        else:
            if args[-1] == 'off':
                self.config.get('TCP', {}).pop('7454', None)
            else:
                self.config.setdefault('TCP', {})['7454'] = deepcopy(pub.PAIR)
        if self.fail == 'unrelated':
            self.config['Web'][host + ':443']['Handlers'].pop('/unrelated', None)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-publication-', dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        state = self.root / 'etc/d32/state'
        state.mkdir(parents=True, mode=0o700)
        (state / 'edge_front').write_bytes(b'off\n')
        self.messages = []
        self.platform = FakePlatform(self.root)
        self.controller = pub.Publication(self.platform, self.root, self.messages.append)

    def refused(self, reason, function, *args, **kwargs):
        with self.assertRaises(pub.Refusal) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.reason, reason)
        self.assertNotIn('\n', str(error.exception))
        return error.exception

    def test_publish_actual_dns_exact_path_and_pairing_forwarder(self):
        result = self.controller.publish(accept_ct=True)
        self.assertTrue(result['published'])
        self.assertEqual(result['url'], 'https://workstation.fixture.ts.net/mcp')
        self.assertEqual(result['tools'], 11)
        self.assertIn('--workstation ' + result['url'], result['connect'])
        self.assertEqual(result['pairing_port'], 7454)
        self.assertIn(('--bg', '--https=443', '--set-path=/mcp', 'http://127.0.0.1:8766/mcp'), self.platform.events)
        self.assertIn(('--bg', '--tcp=7454', 'tcp://127.0.0.1:7454'), self.platform.events)
        self.assertEqual(len(self.messages), 1)
        self.assertIn('Certificate Transparency', self.messages[0])

    def test_status_is_read_only_and_does_not_publish_fresh_host(self):
        before = sorted(self.root.rglob('*'))
        self.assertEqual(self.controller.status()['state'], 'off')
        self.assertEqual(self.controller.publish(resume=True)['state'], 'off')
        self.assertEqual(before, sorted(self.root.rglob('*')))
        self.assertEqual(self.platform.events, [])

    def test_ct_decline_notice_once_no_route_or_gateway_mutation(self):
        for _ in range(2):
            self.refused('ct-consent-required', self.controller.publish)
        self.assertEqual(len(self.messages), 1)
        self.assertNotIn('gateway-start', self.platform.events)
        self.assertEqual(self.platform.config, fixture('serve-empty'))
        self.assertFalse(self.controller.load()['desired'])
        self.controller.publish(accept_ct=True)
        self.controller.publish(accept_ct=True)
        self.assertEqual(len(self.messages), 1)

    def test_signed_out_magicdns_and_https_off_fail_before_any_side_effect(self):
        for name, reason, fix in (
                ('signed-out', 'signin-required', 'd32-link login'),
                ('magicdns-off', 'magicdns-disabled', 'DNS > MagicDNS'),
                ('https-off', 'https-disabled', 'DNS > HTTPS Certificates')):
            with self.subTest(name=name):
                self.platform.node = fixture(name)
                error = self.refused(reason, self.controller.publish, accept_ct=True)
                self.assertIn(fix, str(error))
                self.assertFalse(self.controller.path.exists())
                self.assertNotIn('gateway-start', self.platform.events)

    def test_signed_out_and_expired_status_never_ready(self):
        self.controller.publish(accept_ct=True)
        original = self.controller.path.read_bytes()
        self.platform.node = fixture('signed-out')
        self.refused('signin-required', self.controller.status)
        self.platform.node = fixture('signed-in')
        self.platform.node['Self']['KeyExpired'] = True
        self.refused('signin-required', self.controller.publish, resume=True)
        self.assertEqual(self.controller.path.read_bytes(), original)

    def test_relayed_status_uses_same_owned_https_route_without_direct_claim(self):
        self.platform.node = fixture('relayed')
        result = self.controller.publish(accept_ct=True)
        self.assertTrue(result['published'])
        self.assertNotIn('direct', json.dumps(result))
        self.assertEqual(result['model_readiness'], 'not-checked')

    def test_exact_dns_and_certificate_domain_validation(self):
        for value in ('bad value.ts.net', '127.0.0.1', 'workstation.example.net', '*.fixture.ts.net', 'a.ts.net'):
            with self.subTest(value=value):
                self.platform.node = fixture('signed-in')
                self.platform.node['Self']['DNSName'] = value
                self.refused('dns-name-unavailable', self.controller.publish, accept_ct=True)
        self.platform.node = fixture('signed-in')
        self.platform.node['CertDomains'] = ['other.fixture.ts.net']
        self.refused('https-disabled', self.controller.publish, accept_ct=True)

    def test_unknown_status_fails_closed(self):
        for status in ({}, None, {'BackendState': 'Running'}, {'BackendState': 'NeedsMachineAuth'}):
            with self.subTest(status=status):
                self.platform.node = status
                self.refused('signin-required', self.controller.publish, accept_ct=True)

    def test_root_nested_mcp_and_foreign_exact_route_refused(self):
        for name in ('serve-root', 'serve-foreign', 'serve-nested'):
            with self.subTest(name=name):
                self.platform.config = fixture(name)
                self.refused('route-conflict', self.controller.publish, accept_ct=True)
                self.assertFalse(self.controller.path.exists())

    def test_foreign_identical_route_is_not_adopted(self):
        self.platform.config = fixture('serve-owned')
        self.refused('route-conflict', self.controller.publish, accept_ct=True)
        self.assertFalse(self.controller.path.exists())

    def test_existing_pairing_port_and_raw_443_refused(self):
        for config in ({'TCP': {'7454': deepcopy(pub.PAIR)}}, {'TCP': {'443': {'TCPForward': '127.0.0.1:9999'}}}):
            self.platform.config = config
            self.refused('route-conflict', self.controller.publish, accept_ct=True)

    def test_funnel_refused(self):
        self.platform.config = {'AllowFunnel': {'workstation.fixture.ts.net:443': True}}
        self.refused('public-route-refused', self.controller.publish, accept_ct=True)

    def test_unrelated_routes_survive_publish_status_resume_and_unpublish(self):
        before = fixture('serve-unrelated')
        self.platform.config = deepcopy(before)
        self.controller.publish(accept_ct=True)
        self.assertTrue(self.controller.status()['published'])
        mutations = [x for x in self.platform.events if isinstance(x, tuple) and x[0] != 'verify' or x == 'gateway-start']
        self.controller.publish(resume=True)
        self.assertEqual(mutations, [x for x in self.platform.events if isinstance(x, tuple) and x[0] != 'verify' or x == 'gateway-start'])
        result = self.controller.unpublish()
        self.assertFalse(result['published'])
        self.assertEqual(pub._normal(self.platform.config), pub._normal(before))
        self.assertEqual(self.controller.load()['phase'], 'off')
        self.assertEqual((self.root / 'etc/d32/state/edge_front').read_bytes(), b'off\n')

    def test_repeated_publish_and_unpublish_are_idempotent(self):
        self.controller.publish(accept_ct=True)
        calls = self.platform.events.count('gateway-start')
        self.controller.publish(resume=True)
        self.assertEqual(self.platform.events.count('gateway-start'), calls)
        self.controller.unpublish()
        calls = self.platform.events.count('gateway-stop-front')
        self.controller.unpublish()
        self.assertEqual(self.platform.events.count('gateway-stop-front'), calls)
        self.controller.publish(accept_ct=True)
        self.assertEqual(len(self.messages), 1)

    def test_interrupted_publish_and_missing_route_resume(self):
        self.platform.fail = 'command'
        self.refused('platform-unavailable', self.controller.publish, accept_ct=True)
        self.assertEqual(self.controller.load()['phase'], 'publishing')
        self.platform.fail = None
        self.assertTrue(self.controller.publish(resume=True)['published'])
        self.platform.config.get('TCP', {}).pop('7454')
        self.refused('route-missing', self.controller.status)
        self.assertTrue(self.controller.publish(resume=True)['published'])

    def test_gateway_failure_never_records_ready_and_recovers(self):
        self.platform.fail = 'gateway'
        self.refused('gateway-unverified', self.controller.publish, accept_ct=True)
        self.assertEqual(self.controller.load()['phase'], 'publishing')
        self.platform.fail = None
        self.assertTrue(self.controller.publish(resume=True)['published'])

    def test_resume_repairs_gateway_even_when_serve_routes_survived(self):
        self.controller.publish(accept_ct=True)
        self.platform.fail = 'gateway-once'
        before = self.platform.events.count('gateway-start')
        self.assertTrue(self.controller.publish(resume=True)['published'])
        self.assertEqual(self.platform.events.count('gateway-start'), before + 1)

    def test_changed_dns_or_route_is_never_overwritten(self):
        self.controller.publish(accept_ct=True)
        self.platform.node['Self']['DNSName'] = 'renamed.fixture.ts.net.'
        self.platform.node['CertDomains'] = ['renamed.fixture.ts.net']
        self.refused('identity-changed', self.controller.publish, resume=True)
        self.platform.node = fixture('signed-in')
        pub.handlers(self.platform.config, 'workstation.fixture.ts.net')['/mcp'] = {'Proxy': 'http://127.0.0.1:9999'}
        self.refused('route-conflict', self.controller.publish, resume=True)
        self.refused('route-conflict', self.controller.unpublish)
        self.assertEqual(pub.handlers(self.platform.config, 'workstation.fixture.ts.net')['/mcp']['Proxy'], 'http://127.0.0.1:9999')

    def test_interrupted_unpublish_resume_completes_only_removal(self):
        self.controller.publish(accept_ct=True)
        self.platform.fail = 'command'
        self.refused('platform-unavailable', self.controller.unpublish)
        self.assertEqual(self.controller.load()['phase'], 'removing')
        self.assertFalse(self.controller.load()['desired'])
        self.refused('removal-incomplete', self.controller.status)
        self.refused('removal-incomplete', self.controller.publish, accept_ct=True)
        self.platform.fail = None
        self.assertEqual(self.controller.publish(resume=True)['state'], 'off')

    def test_lost_upstream_path_and_unrelated_deletion_fail_closed(self):
        self.platform.fail = 'strip-prefix'
        self.refused('route-conflict', self.controller.publish, accept_ct=True)
        self.assertEqual(self.controller.load()['phase'], 'publishing')
        self.platform.config = fixture('serve-unrelated')
        self.platform.fail = 'unrelated'
        self.refused('unrelated-route-changed', self.controller.publish, resume=True)

    def test_malformed_serve_state_and_journal_have_actionable_refusals(self):
        for config in (None, [], {'Web': []}, {'Web': {'workstation.fixture.ts.net:443': {'Handlers': []}}}):
            with self.subTest(config=config):
                self.platform.config = config
                self.refused('serve-state-invalid', self.controller.publish, accept_ct=True)
        self.controller.path.write_text('{}', encoding='ascii')
        self.refused('journal-invalid', self.controller.status)

    def test_preexisting_loopback_front_is_not_taken_over(self):
        (self.root / 'etc/d32/state/edge_front').write_bytes(b'on\n')
        self.refused('front-already-enabled', self.controller.publish, accept_ct=True)

    def test_platform_commands_are_bounded_and_never_change_tailnet_settings(self):
        messages = []
        platform = pub.Platform(self.root, messages.append)
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{}', b'')) as call:
            platform.status()
            platform.serve_status()
            platform.serve(['--bg', '--https=443', '--set-path=/mcp', pub.PROXY['Proxy']])
        for invocation in call.call_args_list:
            self.assertEqual(invocation.kwargs['timeout'], 10)
            self.assertNotIn('up', invocation.args[0])
            self.assertNotIn('set', invocation.args[0])
            self.assertNotIn('funnel', invocation.args[0])
        self.assertEqual(len(messages), 3)
        self.assertTrue(all('Waiting' in value and 'resume' in value for value in messages))
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('synthetic', 10)):
            self.refused('platform-unavailable', platform.status)
        self.assertNotIn('synthetic', str(messages))

    def test_platform_gateway_start_only_enables_loopback_and_refuses_legacy_front(self):
        platform = pub.Platform(self.root)
        with patch.object(platform, 'command', side_effect=[1, 0]) as command:
            platform.start_gateway('workstation.fixture.ts.net')
            self.assertEqual(command.call_args_list[-1].args[0], ['rcctl', '-f', 'restart', 'd32edge'])
        with patch.object(platform, 'command', return_value=0):
            self.refused('legacy-front-active', platform.start_gateway, 'workstation.fixture.ts.net')

    def test_gateway_probe_uses_private_credential_and_tls_name_not_settings(self):
        token = bytes(range(32)).hex()
        token_file = self.root / 'etc/d32/state/mcp.token'
        token_file.write_text(token + '\n', encoding='ascii', newline='\n')
        token_file.chmod(0o600)
        events = []
        class HTTPFixture:
            def __init__(self, host, port, **kwargs):
                events.append(('connect', host, port, kwargs))
                self.sock = None
            def request(self, method, path, body, headers):
                self.path, self.headers = path, headers
                events.append(('request', method, path, headers))
            def getresponse(self):
                code = 404 if self.path != '/mcp' else 200 if 'Authorization' in self.headers else 401
                raw = io.BytesIO(json.dumps({'result': {'tools': [{'name': name} for name in sorted(pub.TOOLS)]}}).encode('ascii'))
                return types.SimpleNamespace(status=code, read1=raw.read1)
            def close(self):
                pass
        with patch.object(pub.http.client, 'HTTPConnection', HTTPFixture), patch.object(pub.socket, 'create_connection', side_effect=AssertionError('network forbidden')):
            pub.Platform(self.root).verify_gateway('workstation.fixture.ts.net', '100.64.0.9')
        requests = [event for event in events if event[0] == 'request']
        self.assertNotIn('Authorization', requests[0][3])
        self.assertEqual(requests[1][3]['Authorization'], 'Bearer ' + token)
        self.assertEqual([event[2] for event in requests], ['/mcp', '/mcp', '/mcp/', '/admin', '/health', '/v1/embeddings'])
        self.assertTrue(all(event[1:3] == ('127.0.0.1', 8766) for event in events if event[0] == 'connect'))
        self.assertTrue(all(event[3]['Host'] == 'workstation.fixture.ts.net' for event in requests))

    def test_cli_refusals_are_one_sentence_and_never_execute_commands(self):
        for args, uid in ((['status'], 1000), (['--bad-argument'], 0), (['status', '--accept-ct'], 0)):
            output = io.StringIO()
            with patch.object(os, 'geteuid', return_value=uid, create=True), patch.object(subprocess, 'run', side_effect=AssertionError('platform command forbidden')), contextlib.redirect_stderr(output):
                self.assertNotEqual(pub.main(args), 0)
            self.assertEqual(len(output.getvalue().splitlines()), 1)


class GatewayPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = REPO / 'src/workstation/node/shims/py/serve_edge.py'
        spec = importlib.util.spec_from_file_location('publication_edge_fixture', path)
        cls.edge = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'d32svc': types.ModuleType('d32svc')}):
            spec.loader.exec_module(cls.edge)

    def test_active_dns_override_and_inactive_unchanged(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-dns-', dir=SCRATCH) as scratch:
            root = Path(scratch)
            directory = root / 'etc/d32/private'
            directory.mkdir(parents=True, mode=0o711)
            path = directory / 'publication-dns'
            path.write_bytes(b'workstation.fixture.ts.net\n')
            path.chmod(0o440 if os.name == 'posix' else 0o600)
            self.assertEqual(self.edge.publication_dns(root, expected_gid=os.getgid() if os.name == 'posix' else None), 'workstation.fixture.ts.net')
            self.assertIsNone(self.edge.publication_dns(root, active=False))
            path.chmod(0o600)
            path.write_bytes(b'bad name\n')
            with self.assertRaises(SystemExit):
                self.edge.publication_dns(root)

    def test_remote_gate_denies_trailing_mcp_admin_and_engine_paths(self):
        async def check(path, method='POST', call='tools/list'):
            delivered, sent = [], []
            async def app(scope, receive, send):
                delivered.append(scope['path'])
                await send({'type': 'http.response.start', 'status': 401})
            gate = self.edge.RemoteGate(app, ('127.0.0.1', 8766))
            async def receive():
                return {'type': 'http.request', 'body': json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': call}).encode('ascii')}
            async def send(event):
                sent.append(event)
            await gate({'type': 'http', 'server': ('127.0.0.1', 8766), 'path': path, 'method': method}, receive, send)
            return delivered, sent[0]['status']
        self.assertEqual(asyncio.run(check('/mcp')), (['/mcp'], 401))
        for path in ('/mcp/', '/admin', '/v1/embeddings', '/mcp/health', '/mcpX'):
            self.assertEqual(asyncio.run(check(path)), ([], 404))
        self.assertEqual(asyncio.run(check('/mcp', call='admin/reload')), ([], 200))

    def test_ship_site_and_boot_resume_include_publication_entrypoint(self):
        wrapper = RUNTIME / 'sbin/d32-publish'
        self.assertTrue(wrapper.is_file())
        source = (REPO / 'src/workstation/ship/tools/mksite-ship.sh').read_text(encoding='ascii')
        self.assertIn('cp -Rp $S/distrib/site/. $st/', source)
        self.assertIn('$st/usr/local/d32/sbin/*', source)
        rc = (REPO / 'src/workstation/distrib/site/etc/rc.local').read_text(encoding='ascii')
        self.assertIn('/usr/local/d32/sbin/d32-publish resume', rc)
        self.assertNotIn('d32-publish publish', rc)

    def test_actual_site_copy_and_mode_recipe_carries_publication_files(self):
        bash = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else Path('/bin/bash')
        def shell_path(path):
            path = Path(path).resolve()
            return '/' + path.drive[0].lower() + '/' + path.as_posix().split(':/', 1)[1] if os.name == 'nt' else str(path)
        with tempfile.TemporaryDirectory(prefix='synthetic-pack-', dir=SCRATCH) as scratch:
            root = Path(scratch)
            source, stage = root / 'src', root / 'stage'
            tree = source / 'distrib/site'
            for relative in ('usr/local/d32/publication.py', 'usr/local/d32/sbin/d32-publish',
                             'usr/local/bin/deep32', 'etc/rc.local'):
                target = tree / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / 'src/workstation/distrib/site' / relative, target)
            shutil.copytree(RUNTIME / 'libexec', tree / 'usr/local/d32/libexec',
                            ignore=shutil.ignore_patterns('__pycache__'))
            (tree / 'etc/rc.d').mkdir()
            (tree / 'etc/rc.d/d32fixture').write_bytes(b'fixture\n')
            (tree / 'install.site').write_bytes(b'fixture\n')
            stage.mkdir()
            recipe = (REPO / 'src/workstation/ship/tools/mksite-ship.sh').read_text(encoding='ascii')
            start = recipe.index('find $st -type d -exec chmod')
            end = recipe.index('chmod 0600 $st/etc/pf.conf', start)
            script = root / 'synthetic-pack.sh'
            audit = root / 'synthetic-launcher-modes.tsv'
            script.write_text('set -e\nS="$1"\nst="$2"\naudit="$3"\n'
                'chmod() { mode=$1; shift; /usr/bin/chmod "$mode" "$@"; '
                'for target do printf \'%s\\t%s\\n\' "$mode" "$target" >> "$audit"; done; }\n'
                'cp -Rp $S/distrib/site/. $st/\n' + recipe[start:end]
                + '\ntest -x "$st/usr/local/d32/sbin/d32-publish"\n'
                + 'test -x "$st/usr/local/bin/deep32"\n', encoding='ascii', newline='\n')
            result = subprocess.run([str(bash), '--noprofile', '--norc', shell_path(script), shell_path(source),
                shell_path(stage), shell_path(audit)], capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            modes = {target: mode for mode, target in
                     (line.split('\t', 1) for line in audit.read_text(encoding='ascii').splitlines())}
            self.assertEqual(modes[shell_path(stage / 'usr/local/bin/deep32')], '0555')
            self.assertEqual(modes[shell_path(stage / 'usr/local/d32/sbin/d32-publish')], '0555')
            for relative in ('usr/local/d32/publication.py', 'usr/local/d32/sbin/d32-publish',
                             'usr/local/bin/deep32', 'etc/rc.local'):
                self.assertEqual((tree / relative).read_bytes(), (stage / relative).read_bytes())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tmp', required=True)
    args, rest = parser.parse_known_args()
    SCRATCH = Path(args.tmp).resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *rest])
