"""Synthetic shared role policies; no native command, socket or owner effect."""
from dataclasses import replace
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'diagnostics'))
from deep32_diag import probes, report
from deep32_diag.errors import DiagError
from deep32_diag.models import State, TAILNET_FIX
from deep32_diag.profile import PeerRecord

HOST = 'node.synthetic.ts.net'
PRIVATE = 'synthetic-private-value-never-print'


def status():
    return {'BackendState': 'Running', 'Self': {'Online': True, 'DNSName': HOST + '.',
        'KeyExpired': False}, 'CurrentTailnet': {'MagicDNSEnabled': True}, 'CertDomains': [HOST],
        'AuthURL': PRIVATE, 'Peer': {'enrolled': {'Online': True, 'CurAddr': '100.64.0.1:4000', 'Relay': 'derp'}}}


def forge_journal():
    return {'schema': 'deep32.forge-publication/1', 'install_id': 'a' * 16, 'host': HOST,
        'routes': [dict(setting='embeddings_https', port=443, kind='https', target='http://127.0.0.1:8081'),
                   dict(setting='reranker_https', port=8443, kind='https', target='http://127.0.0.1:8082'),
                   dict(setting='pairing_tcp', port=7453, kind='tcp', target='tcp://127.0.0.1:7453')],
        'added': [0, 1, 2], 'pending': None, 'removing': None, 'phase': 'ready'}


def forge_live():
    return {'TCP': {'443': {'HTTPS': True}, '8443': {'HTTPS': True},
                    '7453': {'TCPForward': '127.0.0.1:7453'}},
        'Web': {HOST + ':443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:8081'}}},
                HOST + ':8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:8082'}}}}}


def ws_journal():
    return {'format': 'deep32-publication', 'version': 1, 'host': HOST,
            'desired': True, 'previous_front': 'off', 'phase': 'ready'}


def ws_live():
    return {'TCP': {'443': {'HTTPS': True}, '7454': {'TCPForward': '127.0.0.1:7454'}},
            'Web': {HOST + ':443': {'Handlers': {'/mcp': {'Proxy': 'http://127.0.0.1:8766/mcp'}}}}}


def authority():
    return probes.PairingAuthority('a' * 64, 'synthetic-peer', 'b' * 64, 'tailnet', 'serve-https',
                                   True, True, False, True, False)


def record(role='forge'):
    return PeerRecord('forge', 'joined' if role == 'forge' else 'joiner',
        'workstation' if role == 'forge' else 'forge', 'tailnet',
        () if role == 'forge' else ('forge-token', 'kb-public.pem'),
        ('forge-token', 'kb-public.pem') if role == 'forge' else (),
        'synthetic-peer', 'b' * 64, 'a' * 64, '100.64.0.1', HOST, 7453, 'serve-https')


class SharedProbes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = Path(os.environ['DEEP32_DIAG_TEST_ROOT'])
        if not base.is_absolute() or not base.is_dir() or base.is_symlink():
            raise AssertionError('explicit existing synthetic test root required')

    def test_tailscale_all_pass_and_redact(self):
        rows = probes.tailscale_rows(status(), role='forge', installed=True, version='1.80.2')
        self.assertEqual([item.state for item in rows], [State.OK] * 3)
        raw = report.encode_report('forge', 'linux', 'synthetic', '2026-11-17T00:00:00Z', rows)
        for value in (PRIVATE, HOST, '100.64.0.1', 'enrolled'):
            self.assertNotIn(value, raw)

    def test_missing_executable_blocks_daemon(self):
        rows = probes.tailscale_rows(None, role='workstation', installed=False)
        self.assertEqual([item.state for item in rows], [State.FAIL, State.SKIP, State.SKIP])
        self.assertEqual(rows[1].blocked_by, ('tailscale-installed',))

    def test_missing_executable_evidence_is_unsupported_warning(self):
        rows = probes.tailscale_rows(status(), role='forge')
        self.assertEqual(rows[0].state, State.WARN)
        self.assertEqual(rows[0].reason, report.Reason.UNSUPPORTED)
        self.assertNotIn('present', dict(rows[0].observed_items))

    def test_invalid_daemon_json_and_missing(self):
        for raw in (None, b'{', b'{"BackendState":"Running","BackendState":"Stopped"}', [], {}):
            with self.subTest(raw=repr(raw)):
                rows = probes.tailscale_rows(raw, role='forge')
                self.assertEqual(rows[1].state, State.FAIL)
                self.assertEqual(rows[2].blocked_by, ('tailscale-running',))

    def test_signed_out_daemon_is_observed(self):
        for backend in ('NeedsLogin', 'NeedsMachineAuth', 'Stopped', 'NoState'):
            rows = probes.tailscale_rows({'BackendState': backend}, role='forge')
            self.assertEqual(rows[1].state, State.OK)
            self.assertEqual(rows[2].state, State.FAIL)

    def test_online_and_expiry(self):
        for mutation in ({'Online': False}, {'KeyExpired': True}, {'Expired': True}):
            raw = status()
            raw['Self'].update(mutation)
            self.assertEqual(probes.tailscale_rows(raw, role='forge')[2].state, State.FAIL)

    def test_missing_or_invalid_expiry_is_unknown_not_unexpired(self):
        for expiry in (None, 'false', 0):
            raw = status()
            if expiry is None:
                del raw['Self']['KeyExpired']
            else:
                raw['Self']['KeyExpired'] = expiry
            observed = probes.tailscale_rows(raw, role='forge')[2]
            self.assertEqual(observed.state, State.WARN)
            self.assertNotIn('expired', dict(observed.observed_items))

    def test_dns_tls_requires_observation(self):
        self.assertEqual([item.state for item in probes.dns_https_rows(status(), role='forge')],
                         [State.WARN, State.WARN])
        self.assertEqual([item.state for item in probes.dns_https_rows(status(), role='forge',
            resolved=True, tls_valid=True)], [State.OK, State.OK])

    def test_dns_disabled_or_wrong_peer_blocks_https(self):
        raw = status()
        raw['CurrentTailnet']['MagicDNSEnabled'] = False
        for value, resolved in ((raw, True), (status(), False)):
            rows = probes.dns_https_rows(value, role='workstation', resolved=resolved, tls_valid=True)
            self.assertEqual(rows[0].state, State.FAIL)
            self.assertEqual(rows[1].blocked_by, ('magicdns',))

    def test_cert_domain_exact_and_headend_selected_peer(self):
        raw = status()
        raw['CertDomains'] = ['wrong.synthetic.ts.net']
        self.assertEqual(probes.dns_https_rows(raw, role='forge', resolved=True,
            tls_valid=True)[1].state, State.FAIL)
        # Headend does not publish its own hostname or certificate. The
        # selected enrolled Workstation's resolution and TLS are required.
        raw['Self'].pop('DNSName')
        self.assertEqual([item.state for item in probes.dns_https_rows(raw,
            role='headend', resolved=True, tls_valid=True)], [State.OK, State.OK])
        self.assertEqual(probes.dns_https_rows(raw, role='headend', resolved=True,
            tls_valid=False)[1].state, State.FAIL)
        self.assertEqual(probes.dns_https_rows(status(), role='forge', resolved=True,
            tls_valid=False)[1].state, State.FAIL)

    def publication(self, role, journal, live, **kwargs):
        return probes.publication_row(json.dumps(journal).encode('ascii'), live,
                                       role=role, current_host=HOST, **kwargs)

    def test_forge_exact_routes_and_independent_host(self):
        self.assertEqual(self.publication('forge', forge_journal(), forge_live()).state, State.OK)
        self.assertEqual(probes.publication_row(json.dumps(forge_journal()).encode('ascii'),
            forge_live(), role='forge').state, State.FAIL)
        self.assertEqual(self.publication('forge', forge_journal(), forge_live(),
            owner_check=lambda journal, live: False).state, State.FAIL)

    def test_forge_missing_changed_foreign_funnel_and_foreground_routes(self):
        variants = []
        value = forge_live(); del value['TCP']['443']; variants.append(value)
        value = forge_live(); value['Web'][HOST + ':443']['Handlers']['/']['Proxy'] += '/wrong'; variants.append(value)
        value = forge_live(); value['Web']['foreign.synthetic.ts.net:443'] = value['Web'][HOST + ':443']; variants.append(value)
        value = forge_live(); value['AllowFunnel'] = {HOST + ':443': True}; variants.append(value)
        value = forge_live(); value['Foreground'] = {'foreign': forge_live()}; variants.append(value)
        for live in variants:
            self.assertEqual(self.publication('forge', forge_journal(), live).state, State.FAIL)

    def test_pending_removing_off_and_unowned_journals_fail(self):
        for updates in ({'pending': 1, 'added': [0, 2]}, {'removing': 1}, {'phase': 'removing'},
                        {'phase': 'publishing'}, {'phase': 'off'}, {'added': [0, 1]}):
            journal = forge_journal(); journal.update(updates)
            self.assertEqual(self.publication('forge', journal, forge_live()).state, State.FAIL)

    def test_workstation_ownership_and_overlap(self):
        self.assertEqual(self.publication('workstation', ws_journal(), ws_live()).state, State.OK)
        for path in ('/', '/mcp/extra'):
            live = ws_live(); live['Web'][HOST + ':443']['Handlers'][path] = {'Proxy': 'http://127.0.0.1:9'}
            self.assertEqual(self.publication('workstation', ws_journal(), live).state, State.FAIL)
        live = ws_live(); live['AllowFunnel'] = {HOST + ':443': True}
        self.assertEqual(self.publication('workstation', ws_journal(), live).state, State.FAIL)

    def test_workstation_pairing_port_rejects_other_host_web_and_funnel(self):
        for host in (HOST, 'foreign.synthetic.ts.net'):
            live = ws_live()
            live['Web'][host + ':7454'] = {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:9'}}}
            before = copy.deepcopy(live)
            self.assertEqual(self.publication('workstation', ws_journal(), live).state, State.FAIL)
            self.assertEqual(live, before)
            for port in ('443', '7454'):
                live = ws_live()
                live['AllowFunnel'] = {host + ':' + port: True}
                before = copy.deepcopy(live)
                self.assertEqual(self.publication('workstation', ws_journal(), live).state, State.FAIL)
                self.assertEqual(live, before)

    def test_publication_invalid_is_failure_headend_outside_this_job(self):
        self.assertEqual(probes.publication_row(b'{', {}, role='forge').state, State.FAIL)
        with self.assertRaises(DiagError):
            probes.publication_row(None, None, role='headend')

    def test_pairing_requires_independent_authority(self):
        self.assertEqual(probes.pairing_row((record(),), role='forge').state, State.FAIL)
        for role in ('forge', 'workstation'):
            self.assertEqual(probes.pairing_row((record(role),), role=role, authority=authority()).state, State.OK)

    def test_pairing_missing_inprogress_revoked_identity_handover_fail(self):
        for updates in ({'claim_present': False}, {'claim_in_progress': True}, {'revoked': True},
                        {'identity_verified': False}, {'handover_verified': False}, {'peer_pin': 'c' * 64},
                        {'transport': 'loopback'}, {'peer_id': 'different'}):
            self.assertEqual(probes.pairing_row((record(),), role='forge',
                authority=replace(authority(), **updates)).state, State.FAIL)

    def test_pairing_record_drift_and_marker_never_sufficient(self):
        for updates in ({'own_pin': 'c' * 64}, {'received': ('mcp-token',)}, {'given': ()},
                        {'publish': 'off'}, {'side': 'joiner'}):
            self.assertEqual(probes.pairing_row((replace(record(), **updates),), role='forge',
                authority=authority()).state, State.FAIL)
        self.assertEqual(probes.pairing_row((), role='forge', authority=authority()).state, State.FAIL)

    def test_forge_pairing_rejects_every_unrelated_record(self):
        for changes in ({'kind': 'gateway'}, {'side': 'joiner'}, {'peer_role': 'headend'}):
            unrelated = replace(record(), **changes)
            self.assertEqual(probes.pairing_row((record(), unrelated), role='forge',
                authority=authority()).state, State.FAIL)

    def test_first_start_completed_failed_waiting_stale(self):
        done = b'D32S/1 1 forge boot=12345678 stage=ready state=done version=0.1.0-alpha.2\n'
        self.assertEqual(probes.first_start_row(done, role='forge', completed=True, fresh=True).state, State.OK)
        self.assertEqual(probes.first_start_row(done, role='forge', completed=True, fresh=False).state, State.WARN)
        self.assertEqual(probes.first_start_row(done, role='forge').state, State.WARN)
        failed = b'D32S/1 2 forge boot=12345678 stage=pairing state=failed error=forge-pairing-failed\n'
        self.assertEqual(probes.first_start_row(failed, role='forge', completed=True).state, State.FAIL)

    def test_first_start_reboot_only_durable_forge_completion(self):
        self.assertEqual(probes.first_start_row(None, role='forge', completed=True,
            lifecycle_complete=True, pairing_committed=True).state, State.OK)
        self.assertEqual(probes.first_start_row(None, role='forge', completed=True).state, State.WARN)
        self.assertEqual(probes.first_start_row(b'private raw failure', role='workstation').state, State.WARN)

    def test_first_start_foreign_node_cannot_prove_local_completion(self):
        raw = b'D32S/1 1 workstation boot=12345678 stage=ready state=done\n'
        self.assertEqual(probes.first_start_row(raw, role='forge', completed=True, fresh=True).state, State.WARN)

    def test_peer_relay_is_reachable_and_path_warn_with_required_fix(self):
        raw = status(); raw['Peer']['enrolled']['CurAddr'] = ''
        rows = probes.peer_rows((probes.PeerEvidence('forge', 'forge', 'enrolled', True, True, 401),),
                                 raw, role='workstation')
        self.assertEqual([item.state for item in rows], [State.OK, State.WARN])
        value = report.build_report('workstation', 'openbsd', 'synthetic', '2026-11-17T00:00:00Z', rows)
        self.assertEqual(value['checks'][1]['fix'], TAILNET_FIX)

    def test_peer_direct_unknown_tls_and_unenrolled_never_scanned(self):
        peers = (probes.PeerEvidence('forge', 'forge', 'enrolled', True, True, 200),)
        self.assertEqual(probes.peer_rows(peers, status(), role='workstation')[1].state, State.OK)
        self.assertEqual(probes.peer_rows(peers, {'Peer': {}}, role='workstation')[1].state, State.WARN)
        peer = replace(peers[0], tls_valid=False)
        self.assertEqual(probes.peer_rows((peer,), status(), role='workstation')[0].state, State.FAIL)
        self.assertEqual(probes.peer_rows((), status(), role='forge'), ())

    def test_peer_alias_is_matching_role_with_bounded_numeric_suffix(self):
        for alias in ('forge', 'forge-1', 'forge-99'):
            peer = probes.PeerEvidence(alias, 'forge', 'enrolled', True, True, 200)
            self.assertEqual(probes.peer_rows((peer,), status(), role='workstation')[0].state, State.OK)
        for alias in ('synthetic-hostname', 'workstation', 'forge-0', 'forge-01', 'forge-100'):
            peer = probes.PeerEvidence(alias, 'forge', 'enrolled', True, True, 200)
            with self.assertRaises(DiagError):
                probes.peer_rows((peer,), status(), role='workstation')

    def test_functions_do_not_open_write_command_or_connect(self):
        with patch('builtins.open', side_effect=AssertionError('open forbidden')), \
             patch('os.open', side_effect=AssertionError('os.open forbidden')), \
             patch('subprocess.run', side_effect=AssertionError('command forbidden')), \
             patch('socket.socket', side_effect=AssertionError('socket forbidden')):
            self.assertEqual(probes.tailscale_rows(status(), role='forge')[2].state, State.OK)
            self.assertEqual(self.publication('forge', forge_journal(), forge_live()).state, State.OK)
            self.assertEqual(probes.pairing_row((record(),), role='forge', authority=authority()).state, State.OK)


if __name__ == '__main__':
    unittest.main()
