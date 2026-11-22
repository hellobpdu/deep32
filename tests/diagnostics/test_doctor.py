"""Invented end-to-end dispatcher observations; no native host certification."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import builtins
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'diagnostics'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep32_diag import cli, doctor, headend, peer_time, probes, profile, report, storage, workstation
from deep32_diag.models import State
from deep32_diag.runner import Budget, ExistingFiles, FileSnapshot
from deep32_diag.trust import InstallReceipt
import test_forge as forge_fixtures
import test_probes as common_fixtures
import test_workstation as ws_fixtures

STAMP = '2026-11-17T12:00:00Z'
NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp()
PRIVATE = 'synthetic-private-dispatcher-sentinel'


def samples():
    result = []
    for index in range(3):
        nonce = format(index, '032x')
        peer = datetime.fromtimestamp(NOW + index + .05, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        result.append(peer_time.ClockSample(NOW + index, NOW + index + .1,
            10 + index, 10 + index + .1, peer, nonce, nonce, True, True))
    return tuple(result)


def common(role):
    peer_role = 'forge' if role == 'workstation' else 'workstation'
    state = common_fixtures.status()
    journal = common_fixtures.forge_journal() if role == 'forge' else common_fixtures.ws_journal()
    live = common_fixtures.forge_live() if role == 'forge' else common_fixtures.ws_live()
    alias = 'data' if role == 'forge' else 'content'
    return doctor.CommonEvidence(tailscale_status=state, tailscale_installed=True,
        tailscale_version='1.80.2', peer_resolved=True, peer_tls_valid=True,
        publication=json.dumps(journal).encode('ascii'), serve_status=live,
        current_host=common_fixtures.HOST, pairing_records=(common_fixtures.record(role),),
        pairing_authority=common_fixtures.authority(),
        peers=(probes.PeerEvidence(peer_role, peer_role, 'enrolled', True, True, 401),),
        clock_samples={peer_role: samples()},
        c6=b'D32S/1 1 workstation boot=12345678 stage=ready state=done version=0.1.0-alpha.3\n'
            if role == 'workstation' else None,
        first_start_completed=True, lifecycle_complete=True, pairing_committed=True, c6_fresh=True,
        storage_budgets=(storage.StorageBudget(alias, 'synthetic-volume', 10, 20),),
        storage_readings={'synthetic-volume': storage.StorageReading(100)})


def install_storage(receipt, alias):
    inventory = dict(receipt.inventory)
    inventory['filesystems'] = ({'alias': alias, 'required_bytes': 20, 'safe_running_bytes': 10},)
    return replace(receipt, inventory=inventory)


def workstation_evidence():
    status = workstation.project_engine_status(ws_fixtures.encode(ws_fixtures.status()))
    return doctor.WorkstationEvidence(engine=status,
        gateway=workstation.project_gateway_status(status.status),
        content=workstation.ContentObservation(ws_fixtures.GENERATION, ws_fixtures.MANIFEST,
                                              ws_fixtures.FINGERPRINT),
        expectation=workstation.ContentExpectation(ws_fixtures.MANIFEST, 5, 50, True),
        auth=workstation.project_forge_auth(endpoints_match=True, tls_valid=True,
            credential_valid=True, embed_http_status=200, rerank_http_status=200),
        attestation=workstation.project_attestor(ws_fixtures.snapshot(), ws_fixtures.MODELS,
                                                now=NOW, maximum_age=60),
        engine_service=True, gateway_service=True)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(os.environ['DEEP32_DIAG_TEST_ROOT'])
        if not self.base.is_absolute() or not self.base.is_dir() or self.base.is_symlink():
            raise AssertionError('explicit existing task scratch required')
        self.snapshots = doctor.SnapshotSet(ExistingFiles({'marker': self.base / 'invented-marker'}),
            (('marker', FileSnapshot(b'invented', (), 'a' * 64)),), True)
        self.unchanged = patch.object(ExistingFiles, 'unchanged', return_value=True).start()
        self.addCleanup(patch.stopall)

    def evidence(self, role, platform=None):
        if role == 'forge':
            platform = platform or 'linux'
            receipt = install_storage(forge_fixtures.receipt(platform), 'data')
            return doctor.DoctorEvidence(receipt=receipt, common=common(role), snapshots=self.snapshots,
                forge=doctor.ForgeEvidence(forge_fixtures.policy(), forge_fixtures.observation(platform),
                                          forge_fixtures.RULES, platform == 'windows'))
        if role == 'workstation':
            receipt = InstallReceipt(role, 'openbsd', '0.1.0-alpha.3', ws_fixtures.MANIFEST,
                {'models': ws_fixtures.MODELS}, ws_fixtures.GENERATION, ws_fixtures.FINGERPRINT)
            return doctor.DoctorEvidence(receipt=install_storage(receipt, 'content'), common=common(role),
                                         workstation=workstation_evidence(), snapshots=self.snapshots)
        selected = profile.SelectedProfile('codex', profile.Credential(b'3' * 64 + b'\n'),
            'https://workstation.synthetic.ts.net/mcp', str(self.base / 'codex.exe'),
            self.base / 'selected' / '.deep32-connect' / 'codex' / 'state.json',
            'synthetic-peer', 'deep32', 'DEEP32_SYNTHETIC_TOKEN', True)
        observed = headend.HeadendEvidence(profile=selected,
            agent=headend.AgentObservation('codex', selected.state_path, selected.executable,
                                           True, True, '1.2.3', True),
            gateway=headend.GatewayObservation(selected.state_path, selected.url, True, True,
                                              workstation_evidence().engine.status), pairing_committed=True)
        receipt = InstallReceipt(role, platform or 'linux', '0.1.0-alpha.3', 'b' * 64, {})
        return doctor.DoctorEvidence(receipt=receipt, common=common(role), headend=observed,
                                     snapshots=self.snapshots)

    def run_role(self, role='forge', *, evidence=None, platform=None, **kwargs):
        evidence = self.evidence(role, platform) if evidence is None else evidence
        budget = kwargs.pop('budget', Budget(clock=lambda: 100))
        return doctor.run_doctor(role, evidence=evidence,
            platform=platform or evidence.receipt.platform, hostname='synthetic-node', checked_at=STAMP,
            budget=budget, **kwargs)

    def rows(self, result):
        return {item['id']: item for item in result['checks']}

    def test_all_supported_role_platform_pairs_pass_with_qualified_fakes(self):
        for role, platform in sorted(doctor.PAIRS):
            with self.subTest(role=role, platform=platform):
                result = self.run_role(role, platform=platform)
                self.assertTrue(result['complete'])
                self.assertEqual(result['exit_code'], 0, report.render_json(result))
                self.assertEqual(result['summary']['fail'], 0)
                self.assertEqual(result['summary']['warn'], 0)

    def test_role_rows_have_fixed_order(self):
        forge = [item['id'] for item in self.run_role()['checks']]
        self.assertLess(forge.index('gpu-visible.embed'), forge.index('container.embeddings'))
        self.assertLess(forge.index('container.embeddings'), forge.index('container.reranker'))
        self.assertLess(forge.index('container.reranker'), forge.index('container.door'))
        self.assertLess(forge.index('port.pairing'), forge.index('disk.data'))
        self.assertLess(forge.index('disk.data'), forge.index('gpu-hold'))

    def test_warn_and_fail_precedence_and_dependency(self):
        evidence = self.evidence('workstation')
        relay = common('workstation')
        relay.tailscale_status['Peer']['enrolled']['CurAddr'] = ''
        result = self.run_role('workstation', evidence=replace(evidence, common=relay))
        self.assertEqual(result['exit_code'], 1)
        self.assertEqual(self.rows(result)['tailnet-path.forge']['state'], 'WARN')
        broken = replace(evidence, common=replace(relay, pairing_authority=None))
        result = self.run_role('workstation', evidence=broken)
        self.assertEqual(result['exit_code'], 2)
        self.assertEqual(self.rows(result)['forge-auth']['blocked_by'], ['pairing'])

    def test_missing_receipt_fails_without_reading_supplied_path(self):
        with patch.object(builtins, 'open', side_effect=AssertionError(PRIVATE)), \
             patch.object(Path, 'open', side_effect=AssertionError(PRIVATE)):
            result = doctor.run_doctor('forge', platform='linux', hostname='synthetic', checked_at=STAMP,
                data_root=str(self.base / 'missing'), budget=Budget(clock=lambda: 0))
        self.assertEqual([item['id'] for item in result['checks']], ['installation'])
        self.assertEqual(result['exit_code'], 2)
        self.assertTrue(result['complete'])

    def test_unsupported_platform_or_wrong_receipt_is_installation_failure(self):
        result = self.run_role(platform='openbsd', evidence=self.evidence('forge'))
        self.assertIsNone(result['role'])
        self.assertIsNone(result['platform'])
        self.assertEqual(result['exit_code'], 2)
        result = self.run_role('forge', evidence=self.evidence('workstation'), platform='linux')
        self.assertEqual([item['id'] for item in result['checks']], ['installation'])

    def test_missing_peer_has_failed_reachability_and_blocked_clock(self):
        evidence = self.evidence('forge')
        result = self.run_role(evidence=replace(evidence, common=replace(evidence.common, peers=())))
        rows = self.rows(result)
        self.assertEqual(rows['peer-reachability.workstation']['state'], 'FAIL')
        self.assertEqual(rows['peer-clock.workstation']['blocked_by'], ['peer-reachability.workstation'])

    def test_absent_tailscale_dependency_resolves(self):
        evidence = self.evidence('forge')
        result = self.run_role(evidence=replace(evidence, common=replace(evidence.common,
            tailscale_installed=False, tailscale_status=None)))
        rows = self.rows(result)
        self.assertTrue(result['complete'])
        for key in ('tailscale-running', 'tailscale-signin', 'magicdns', 'https-available'):
            self.assertEqual(rows[key]['blocked_by'], ['tailscale-installed'])

    def test_missing_snapshot_capability_never_all_ok(self):
        evidence = self.evidence('forge')
        for snapshots in (None, replace(self.snapshots, qualified=False), replace(self.snapshots, slots=())):
            result = self.run_role(evidence=replace(evidence, snapshots=snapshots))
            self.assertEqual(self.rows(result)['snapshot-changed']['state'], 'WARN')
            self.assertEqual(result['exit_code'], 1)

    def test_changed_snapshot_warns_and_rereads_every_slot(self):
        evidence = self.evidence('forge')
        snapshots = replace(self.snapshots, slots=self.snapshots.slots +
            (('second', FileSnapshot(b'second', (), 'b' * 64)),))
        with patch.object(ExistingFiles, 'unchanged', side_effect=(False, True)) as read:
            result = self.run_role(evidence=replace(evidence, snapshots=snapshots))
        self.assertEqual(read.call_count, 2)
        self.assertEqual(self.rows(result)['snapshot-changed']['state'], 'WARN')

    def test_storage_budget_cannot_be_lowered_from_receipt(self):
        evidence = self.evidence('forge')
        altered = replace(evidence.common, storage_budgets=(storage.StorageBudget('data', 'volume', 0, 1),))
        result = self.run_role(evidence=replace(evidence, common=altered))
        self.assertEqual(self.rows(result)['disk.data']['state'], 'FAIL')

    def test_exception_and_interrupt_are_bounded_incomplete_reports(self):
        for error in (RuntimeError(PRIVATE), KeyboardInterrupt(PRIVATE)):
            with patch.object(probes, 'tailscale_rows', side_effect=error):
                result = self.run_role()
            self.assertFalse(result['complete'])
            self.assertEqual(self.rows(result)['doctor']['state'], 'FAIL')
            self.assertEqual(result['exit_code'], 2)
            self.assertNotIn(PRIVATE, report.render_json(result) + report.render_text(result))

    def test_global_budget_stops_aggregation(self):
        clock = iter((0, 0, 86))
        result = self.run_role(budget=Budget(clock=lambda: next(clock)))
        self.assertFalse(result['complete'])
        self.assertEqual(result['exit_code'], 2)

    def test_invalid_checked_at_has_bounded_fallback(self):
        result = doctor.run_doctor('forge', evidence=self.evidence('forge'), platform='linux',
            hostname='synthetic', checked_at=PRIVATE, budget=Budget(clock=lambda: 0))
        self.assertFalse(result['complete'])
        self.assertNotIn(PRIVATE, report.render_json(result))

    def test_control_hostname_uses_fixed_alias(self):
        result = doctor.run_doctor('forge', platform='linux', hostname=PRIVATE + '\n\x1b[0m',
                                   checked_at=STAMP, budget=Budget(clock=lambda: 0))
        self.assertEqual(result['hostname'], 'local-node')

    def test_mocked_aggregation_calls_no_process_socket_or_mutator(self):
        fixtures = [(role, self.evidence(role)) for role in ('workstation', 'forge', 'headend')]
        with ExitStack() as stack:
            for owner, name in ((builtins, 'open'), (Path, 'open'), (Path, 'mkdir'),
                    (Path, 'write_bytes'), (os, 'unlink'), (os, 'rename'), (os, 'chmod'),
                    (subprocess, 'Popen'), (socket, 'socket'), (socket, 'gethostname')):
                stack.enter_context(patch.object(owner, name, side_effect=AssertionError(PRIVATE)))
            for role, evidence in fixtures:
                self.assertEqual(self.run_role(role, evidence=evidence)['exit_code'], 0)

    def test_forge_data_root_selection_binding(self):
        evidence = replace(self.evidence('forge'), data_root=str(self.base / 'forge-a'))
        self.assertEqual(self.run_role(evidence=evidence, data_root=evidence.data_root)['exit_code'], 0)
        result = self.run_role(evidence=evidence, data_root=str(self.base / 'forge-b'))
        self.assertEqual([item['id'] for item in result['checks']], ['installation'])

    def test_headend_agent_profile_selection_binding(self):
        evidence = self.evidence('headend')
        root = str(evidence.headend.profile.state_path.parents[2])
        for agent, profile_dir in (('claude', root), ('codex', str(self.base / 'other'))):
            result = self.run_role('headend', evidence=evidence, agent=agent, profile_dir=profile_dir)
            self.assertEqual([item['id'] for item in result['checks']], ['installation'])
        self.assertEqual(self.run_role('headend', evidence=evidence,
            agent='codex', profile_dir=root)['exit_code'], 0)

    def test_json_stdout_is_one_document_and_same_semantics(self):
        evidence = self.evidence('forge')
        output = io.StringIO()
        result = self.run_role(evidence=evidence)
        code = cli.main(['doctor', '--role', 'forge', '--json'], stdout=output, evidence=evidence,
            platform='linux', hostname='synthetic-node', checked_at=STAMP, budget=Budget(clock=lambda: 100))
        self.assertEqual(code, result['exit_code'])
        self.assertEqual(json.loads(output.getvalue()), result)
        self.assertEqual(output.getvalue().count('\n'), 1)

    def test_text_stdout_is_same_fixed_renderer(self):
        evidence = self.evidence('forge')
        output = io.StringIO()
        result = self.run_role(evidence=evidence)
        code = cli.main(['doctor', '--role', 'forge'], stdout=output, evidence=evidence,
            platform='linux', hostname='synthetic-node', checked_at=STAMP, budget=Budget(clock=lambda: 100))
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), report.render_text(result))

    def test_bad_arguments_are_json_when_requested_and_never_echoed(self):
        cases = ([], ['doctor'], ['doctor', '--role', 'full'], ['doctor', '--role', 'headend'],
            ['doctor', '--role', 'forge', '--token', PRIVATE], ['doctor', '--role', 'forge', '--repair'],
            ['doctor', '--role', 'forge', '--url', PRIVATE], ['doctor', '--role', 'forge', '--role', 'forge'],
            ['doctor', '--role', 'workstation', '--data-root', str(self.base)],
            ['doctor', '--role', 'forge', '--agent', 'codex'],
            ['doctor', '--role', 'headend', '--agent', 'codex', '--profile-dir', 'relative'])
        for args in cases:
            with self.subTest(args=args):
                output = io.StringIO()
                self.assertEqual(cli.main([*args, '--json'], stdout=output), 2)
                value = json.loads(output.getvalue())
                self.assertEqual(value['checks'][0]['id'], 'arguments')
                self.assertNotIn(PRIVATE, output.getvalue())

    def test_json_invalid_explicit_value_still_has_schema_report(self):
        output = io.StringIO()
        self.assertEqual(cli.main(['doctor', '--json=true'], stdout=output), 2)
        self.assertEqual(json.loads(output.getvalue())['schema'], 'deep32.doctor-output.v1')

    def test_interrupted_argument_parser_produces_incomplete_doctor_failure(self):
        for error in (KeyboardInterrupt(PRIVATE), RuntimeError(PRIVATE)):
            output = io.StringIO()
            with patch.object(cli, 'parse_arguments', side_effect=error):
                self.assertEqual(cli.main(['doctor', '--json'], stdout=output), 2)
            result = json.loads(output.getvalue())
            self.assertFalse(result['complete'])
            self.assertIn('doctor', self.rows(result))
            self.assertNotIn(PRIVATE, output.getvalue())

    def test_installed_evidence_role_can_supply_missing_role(self):
        output = io.StringIO()
        evidence = self.evidence('forge')
        self.assertEqual(cli.main(['doctor', '--json'], stdout=output, evidence=evidence,
            platform='linux', hostname='synthetic-node', checked_at=STAMP,
            budget=Budget(clock=lambda: 100)), 0)
        self.assertEqual(json.loads(output.getvalue())['role'], 'forge')

    def test_argument_parser_passes_exact_selected_root_and_agent(self):
        args = cli.parse_arguments(['doctor', '--role', 'headend', '--agent', 'codex',
                                    '--profile-dir', str(self.base)])
        self.assertEqual(args.profile_dir, str(self.base))
        self.assertEqual(args.agent, 'codex')


if __name__ == '__main__':
    unittest.main()
