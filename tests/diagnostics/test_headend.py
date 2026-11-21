"""Invented selected profiles and fake status transports; no native host probes."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'diagnostics'))
from deep32_diag import headend as h, mcp, profile
from deep32_diag.models import State
from deep32_diag.report import build_report
import test_profile as pf
import test_mcp as mf


class HeadendTests(unittest.TestCase):
    def setUp(self):
        root = os.environ.get('DEEP32_DIAG_TEST_ROOT')
        if not root:
            raise RuntimeError('Set DEEP32_DIAG_TEST_ROOT to the suite scratch')
        self.temp = tempfile.TemporaryDirectory(prefix='headend-', dir=root)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.fixtures = pf.ProfileTests()
        self.fixtures.folder = self.folder

    def fixture(self, agent='codex'):
        return self.fixtures.fixture(agent)

    def read(self, home, agent='codex', **kwargs):
        return h.read_headend_profile(agent, home, windows=True,
            acl_getter=pf.acl, decrypt=lambda raw: pf.SYNTHETIC,
            launcher_origin=(str(self.folder / 'python.exe'),
                             str(self.folder / 'adapters/connect.py')), **kwargs)

    def selected(self, agent='codex'):
        return self.read(self.fixture(agent)[0], agent).profile

    def agent(self, selected, **changes):
        value = h.AgentObservation(selected.agent, selected.state_path,
            selected.executable, True, True, '1.2.3', True)
        return replace(value, **changes)

    def status(self, **changes):
        value = mf.status_value()
        value.update(changes)
        return mcp._status({'content': [{'type': 'text', 'text': json.dumps(value)}]})

    def gateway(self, selected, **changes):
        value = h.GatewayObservation(selected.state_path, selected.url, True, True, self.status())
        return replace(value, **changes)

    def evidence(self, selected, **changes):
        value = h.HeadendEvidence(selected, agent=self.agent(selected),
            gateway=self.gateway(selected), pairing_committed=True)
        return replace(value, **changes)

    def rows(self, evidence):
        return {item.id: item for item in h.headend_rows(evidence)}

    def report(self, evidence):
        return build_report('headend', 'windows', 'synthetic-node',
            '2026-11-17T00:00:00Z', h.headend_rows(evidence))

    def boundary(self, selected, reader, **changes):
        value = h.NativeAgentBoundary(selected.agent, selected.executable,
            selected.state_path.parents[2], '1.2.3',
            (selected.agent + ' 1.2.3\n').encode(), reader, True)
        return replace(value, **changes)

    def snapshot(self):
        result = []
        for path in sorted(self.folder.rglob('*')):
            info = path.lstat()
            result.append((str(path.relative_to(self.folder)), info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_uid, info.st_gid, info.st_nlink,
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None))
        return result

    def test_two_independent_profiles_read_only_selected_and_no_ordinary_home(self):
        first = self.fixture('codex')[0]
        second = self.fixture('claude')[0]
        before = self.snapshot()
        for home, agent in ((first, 'codex'), (second, 'claude')):
            with self.subTest(agent=agent), mock.patch.dict(os.environ,
                    {'CODEX_HOME': str(second), 'CLAUDE_CONFIG_DIR': str(first)}), pf.read_only(home) as reads:
                evidence = self.read(home, agent)
                report = self.report(self.evidence(evidence.profile))
            self.assertTrue(reads)
            self.assertEqual(evidence.profile.agent, agent)
            self.assertEqual(report['exit_code'], 0)
            public = json.dumps(report)
            for private in (str(first), str(second), pf.SYNTHETIC.decode().strip(),
                            evidence.profile.url, evidence.profile.peer_id):
                self.assertNotIn(private, public)
        self.assertEqual(before, self.snapshot())

    def test_missing_wrong_profile_and_agent_fail_without_creating_anything(self):
        missing = self.folder / 'missing'
        with pf.read_only():
            evidence = self.read(missing)
            result = self.report(evidence)
        self.assertFalse(missing.exists())
        self.assertEqual(result['exit_code'], 2)
        self.assertEqual(result['checks'][0]['id'], 'agent-profile')
        self.assertTrue(all(item['blocked_by'] == ['agent-profile'] for item in result['checks'][1:]))
        home = self.fixture()[0]
        with pf.read_only(home):
            wrong = self.read(home, 'claude')
        self.assertIsNone(wrong.profile)
        self.assertIs(self.rows(wrong)['agent-profile'].state, State.FAIL)

    def test_owned_literal_token_missing_entry_and_launcher_get_named_connector_fix(self):
        home, _, state, _, _, launcher = self.fixture()
        config = home / 'config.toml'
        original = config.read_bytes()
        for raw in (original + b'bearer_token = "SYNTHETIC_LITERAL"\n', b'[mcp_servers.other]\nurl="https://other.test/mcp"\n'):
            config.write_bytes(raw)
            with pf.read_only(home) as reads:
                evidence = self.read(home)
            rows = self.rows(evidence)
            self.assertIs(rows['agent-connector'].state, State.FAIL)
            self.assertIn('Rerun Connect', self.report(evidence)['checks'][1]['fix'])
            self.assertFalse(any(path.name == 'mcp-token' for path in reads))
        config.write_bytes(original)
        launcher.write_bytes(launcher.read_bytes() + b'Write-Output SYNTHETIC\n')
        with pf.read_only(home):
            evidence = self.read(home)
        self.assertEqual(evidence.profile_error, 'profile-launcher')
        self.assertIs(self.rows(evidence)['agent-connector'].state, State.FAIL)

    def test_unsafe_credential_getter_has_gateway_fix_and_no_native_calls(self):
        home = self.fixture()[0]
        def broken(*args):
            raise RuntimeError(pf.CANARY)
        with pf.read_only(home):
            evidence = h.read_headend_profile('codex', home, windows=True,
                acl_getter=pf.acl, decrypt=broken)
        result = self.report(evidence)
        gateway = next(item for item in result['checks'] if item['id'] == 'gateway-auth')
        self.assertEqual(gateway['state'], 'FAIL')
        self.assertIn('d32-pairdesk -headend', gateway['fix'])
        self.assertNotIn(pf.CANARY, json.dumps(result))
        self.assertNotIn(str(home), repr(evidence))

    def test_unconfirmed_launcher_is_connector_failure_and_blocks_gateway(self):
        selected = replace(self.selected(), launcher_origin_confirmed=False)
        rows = self.rows(self.evidence(selected))
        self.assertIs(rows['agent-connector'].state, State.FAIL)
        self.assertIs(rows['gateway-auth'].state, State.SKIP)
        self.assertEqual(rows['gateway-auth'].blocked_by, ('agent-connector',))

    def test_unqualified_native_boundary_never_calls_reader(self):
        selected = self.selected()
        reader = mock.Mock(side_effect=AssertionError('unqualified reader called'))
        for boundary in (None, self.boundary(selected, reader, qualified=False)):
            with pf.read_only():
                native = h.observe_agent(selected, boundary=boundary)
            rows = self.rows(self.evidence(selected, agent=native))
            self.assertIs(rows['agent-installed'].state, State.WARN)
            self.assertIs(rows['agent-signin'].state, State.WARN)
        reader.assert_not_called()

    def test_windows_default_never_activates_unqualified_native_acl_or_dpapi(self):
        home = self.fixture()[0]
        with pf.read_only(home) as reads, mock.patch.object(profile, 'native_windows_acl',
                side_effect=AssertionError('unqualified native ACL')), mock.patch.object(profile,
                'native_dpapi_decrypt', side_effect=AssertionError('unqualified native decrypt')):
            evidence = h.read_headend_profile('codex', home, windows=True)
        self.assertEqual(reads, [])
        self.assertEqual(evidence.profile_error, 'credential-unavailable')
        self.assertIs(self.rows(evidence)['gateway-auth'].state, State.FAIL)

    def test_expired_native_deadline_never_invokes_reader(self):
        selected = self.selected()
        reader = mock.Mock(side_effect=AssertionError('expired reader'))
        native = h.observe_agent(selected, boundary=self.boundary(selected, reader),
            clock=lambda: 10.0, deadline=10.0)
        reader.assert_not_called()
        self.assertFalse(native.present)

    def test_qualified_native_status_exact_profile_fixed_argv_and_no_bearer(self):
        for agent in ('codex', 'claude'):
            selected = self.selected(agent)
            requests = []
            def reader(request):
                requests.append(request)
                return h.AgentResult(0, (agent + ' 1.2.3\n').encode()
                    if request.operation is h.AgentOperation.VERSION else b'SYNTHETIC_ACCOUNT_MUST_NOT_PRINT')
            with pf.read_only():
                native = h.observe_agent(selected, boundary=self.boundary(selected, reader), clock=lambda: 10.0)
            self.assertTrue(native.signed_in)
            self.assertEqual([request.argv for request in requests], [
                (selected.executable, '--version'), (selected.executable,
                *(('login', 'status') if agent == 'codex' else ('auth', 'status')))])
            variable = 'CODEX_HOME' if agent == 'codex' else 'CLAUDE_CONFIG_DIR'
            for request in requests:
                self.assertEqual(dict(request.profile_environment),
                    {variable: str(selected.state_path.parents[2])})
                self.assertEqual(request.deadline, 20.0)
                self.assertNotIn(pf.SYNTHETIC.decode().strip(), repr(request))
            self.assertNotIn('SYNTHETIC_ACCOUNT', json.dumps(self.report(self.evidence(selected, agent=native))))

    def test_wrong_native_binding_or_version_drift_never_reports_signin_ok(self):
        selected = self.selected()
        reader = mock.Mock(return_value=h.AgentResult(0, b'codex 9.9.9\n'))
        native = h.observe_agent(selected, boundary=self.boundary(selected, reader,
            profile_dir=self.folder / 'other'))
        reader.assert_not_called()
        self.assertFalse(native.qualified)
        native = h.observe_agent(selected, boundary=self.boundary(selected, reader))
        self.assertFalse(native.present)
        rows = self.rows(self.evidence(selected, agent=native))
        self.assertIs(rows['agent-installed'].state, State.FAIL)
        self.assertIs(rows['agent-signin'].state, State.SKIP)

    def test_native_signed_out_failure_and_timeout_are_bounded(self):
        selected = self.selected()
        for mode in ('signed-out', 'timeout', 'oversized'):
            def reader(request):
                if request.operation is h.AgentOperation.VERSION:
                    return h.AgentResult(0, b'codex 1.2.3\n')
                if mode == 'timeout':
                    raise TimeoutError(pf.CANARY)
                return h.AgentResult(1, pf.CANARY.encode() * (500 if mode == 'oversized' else 1))
            native = h.observe_agent(selected, boundary=self.boundary(selected, reader))
            rows = self.rows(self.evidence(selected, agent=native))
            self.assertIs(rows['agent-installed'].state, State.OK)
            self.assertIs(rows['agent-signin'].state, State.FAIL)
            self.assertNotIn(pf.CANARY, json.dumps(self.report(self.evidence(selected, agent=native))))

    def test_foreign_profile_native_and_gateway_evidence_cannot_be_reused(self):
        first, second = self.selected('codex'), self.selected('claude')
        rows = self.rows(self.evidence(first, agent=self.agent(second), gateway=self.gateway(second)))
        self.assertIs(rows['agent-signin'].state, State.WARN)
        self.assertIs(rows['gateway-auth'].state, State.FAIL)
        self.assertIs(rows['mcp-rag-status'].state, State.SKIP)

    def test_gateway_json_and_sse_share_exact_scope_and_anonymous_denial(self):
        selected = self.selected()
        for sse in (False, True):
            transport = mf.FakeTransport(mf.FakeResponse(status=401), mf.initial(sse=sse),
                mf.notified(), mf.tool_list(), mf.status_reply())
            with pf.read_only():
                observed = h.observe_gateway(selected, transport=transport, clock=lambda: 10.0)
            self.assertTrue(observed.authenticated)
            self.assertTrue(observed.tools_valid)
            self.assertTrue(observed.status.ready)
            self.assertEqual({request.deadline for request in transport.requests}, {20.0})
            for index, request in enumerate(transport.requests):
                self.assertEqual(request.endpoint.url, selected.url)
                self.assertFalse(request.use_proxies)
                self.assertFalse(request.allow_redirects)
                self.assertEqual('Authorization' in request.headers, index != 0)
                self.assertNotIn(selected.url, repr(request))
            self.assertTrue(all(response.closed for response in transport.responses))

    def test_anonymous_access_revocation_tls_and_redirect_fail_auth(self):
        selected = self.selected()
        scenarios = (
            mf.FakeTransport(mf.initial()),
            mf.FakeTransport(mf.FakeResponse(status=401), mf.FakeResponse(status=401)),
            mf.FakeTransport(mf.FakeResponse(status=401), mf.initial(), mf.notified(),
                mf.FakeResponse(status=401)),
            mf.FakeTransport(RuntimeError(pf.CANARY)),
            mf.FakeTransport(mf.FakeResponse(status=302, headers={'Location': 'https://SYNTHETIC_PRIVATE.test/'})),
        )
        for transport in scenarios:
            with self.subTest(), pf.read_only():
                gateway = h.observe_gateway(selected, transport=transport)
            rows = self.rows(self.evidence(selected, gateway=gateway))
            self.assertIs(rows['gateway-auth'].state, State.FAIL)
            self.assertIs(rows['mcp-rag-status'].state, State.SKIP)
            self.assertNotIn(pf.CANARY, repr(gateway))

    def test_contradictory_profile_and_auth_evidence_cannot_pass(self):
        selected = self.selected()
        evidence = self.evidence(selected, profile_error='profile-state')
        self.assertIs(self.rows(evidence)['agent-profile'].state, State.FAIL)
        self.assertIs(h.headend_pairing_row(evidence).state, State.FAIL)
        evidence = self.evidence(selected, gateway=self.gateway(selected, error='mcp-auth'))
        self.assertIs(self.rows(evidence)['gateway-auth'].state, State.FAIL)
        self.assertIs(h.headend_pairing_row(evidence).state, State.FAIL)

    def test_wrong_tools_and_malformed_or_legacy_status_fail_without_false_auth_failure(self):
        selected = self.selected()
        replies = (
            [mf.tool_list(['rag_status'])],
            [mf.tool_list(), mf.rpc({'content': [{'type': 'text', 'text': '{broken'}]})],
            [mf.tool_list(), mf.rpc({'content': [{'type': 'text', 'text': '{"ok":true}'}]})],
        )
        for suffix in replies:
            gateway = h.observe_gateway(selected, transport=mf.FakeTransport(
                mf.FakeResponse(status=401), mf.initial(), mf.notified(), *suffix))
            rows = self.rows(self.evidence(selected, gateway=gateway))
            self.assertIs(rows['gateway-auth'].state, State.OK)
            self.assertIs(rows['mcp-rag-status'].state, State.FAIL)

    def test_backend_outage_optional_provider_and_missing_identity_never_full_ok(self):
        selected = self.selected()
        scenarios = []
        for field in ('dense', 'reranker', 'kb', 'structure'):
            value = mf.status_value()
            value['backends'][field]['ok'] = False
            value['degraded'] = [field]
            scenarios.append((value, State.WARN if field in ('dense', 'reranker') else State.FAIL))
        value = mf.status_value()
        value['backends']['dense'] = {'enabled': False, 'ok': None}
        scenarios.append((value, State.WARN))
        value = mf.status_value()
        value['degraded'] = ['colbert']
        scenarios.append((value, State.WARN))
        for field in ('generation', 'corpus_fingerprint'):
            value = mf.status_value()
            value[field] = None
            scenarios.append((value, State.WARN))
        value = mf.status_value()
        value['ok'] = False
        value['backends']['bm25']['ok'] = False
        scenarios.append((value, State.FAIL))
        for value, state in scenarios:
            gateway = self.gateway(selected, status=mcp._status({'content': [
                {'type': 'text', 'text': json.dumps(value)}]}))
            self.assertIs(self.rows(self.evidence(selected, gateway=gateway))['mcp-rag-status'].state, state)

    def test_unqualified_gateway_no_io_and_no_pairing_inference_from_bearer(self):
        selected = self.selected()
        with pf.read_only():
            gateway = h.observe_gateway(selected)
        self.assertEqual(gateway.error, 'mcp-transport-unqualified')
        self.assertIs(self.rows(self.evidence(selected, gateway=gateway))['gateway-auth'].state, State.FAIL)
        evidence = self.evidence(selected, pairing_committed=None)
        self.assertIs(h.headend_pairing_row(evidence).state, State.FAIL)
        self.assertIs(h.headend_pairing_row(replace(evidence, pairing_committed=True)).state, State.OK)

    def test_gateway_global_peer_deadline_cannot_expand_per_exchange(self):
        selected = self.selected()
        clock = mf.Clock()
        def advance():
            clock.now += 3.0
        transport = mf.FakeTransport(mf.FakeResponse(status=401), mf.initial(),
            mf.notified(), mf.tool_list(), mf.status_reply(), before_open=advance)
        gateway = h.observe_gateway(selected, transport=transport, clock=clock)
        self.assertEqual(gateway.error, 'mcp-timeout')
        self.assertTrue(all(request.deadline == 110.0 for request in transport.requests))
        self.assertLessEqual(len(transport.requests), 4)


if __name__ == '__main__':
    unittest.main()
