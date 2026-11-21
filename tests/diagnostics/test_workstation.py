"""Synthetic J06 policy contracts; no native OpenBSD/service qualification."""

from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import builtins
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
sys.path.insert(0, str(ROOT / 'src' / 'gpu-pack' / 'import'))
import imp_journal
from deep32_diag import mcp, report, workstation as ws
from deep32_diag.errors import DiagError
from deep32_diag.models import State
from deep32_diag.platform.openbsd import OpenBSDObservations, Operation
from deep32_diag.runner import Budget
from deep32_diag.trust import InstallReceipt

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp()
STAMP = '2026-11-17T12:00:00Z'
GENERATION = 'public-demo-v1'
FINGERPRINT = 'a' * 64
MANIFEST = 'b' * 64
SENTINEL = 'synthetic-private-sentinel-k59'
MODELS = ({'alias': 'embeddings', 'id': 'public-demo/embeddings', 'revision': '1' * 40},
          {'alias': 'reranker', 'id': 'public-demo/reranker', 'revision': '2' * 40})


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


def status():
    return {'ok': True, 'generation': GENERATION, 'corpus_fingerprint': FINGERPRINT,
            'n_docs': 5, 'n_chunks': 50, 'checked_at': SENTINEL,
            'backends': {'bm25': {'ok': True},
                         'dense': {'enabled': True, 'ok': True, 'breaker': 'closed', 'last_error_kind': None},
                         'reranker': {'enabled': True, 'ok': True, 'breaker': 'closed', 'last_error_kind': None},
                         'kb': {'ok': True},
                         'structure': {'ok': True, 'documents': 5, 'sections': 20}},
            'degraded': [], 'settings': {'credential': SENTINEL}}


def snapshot():
    return {'gpu_ext': 'on', 'hub': SENTINEL,
            **{kind: {'verified': True, 'reason': SENTINEL, 'checked_at': STAMP,
                      'model_id': model['id'], 'revision': model['revision'],
                      'measurement_verified': True, 'device_kind': 'gpu'}
               for kind, model in zip(('embed', 'rerank'), MODELS)}}


def registry():
    return {'format': 'deep32-import-installed', 'format_version': 1, 'node': 'workstation',
            'active': {'manifest_sha256': MANIFEST, 'pointers': {'kb': '.kb-' + GENERATION + '/' + GENERATION}},
            'previous': None,
            'generations': [{'target': '.kb-' + GENERATION, 'unit_kind': 'kb',
                             'generation': GENERATION, 'fingerprint': FINGERPRINT,
                             'units': [{'id': 'kb', 'generation': GENERATION, 'release': None}]}]}


class WorkstationTests(unittest.TestCase):
    def setUp(self):
        # Every fixture is in memory.  Any suite scratch is still explicitly
        # constrained to the task's C: work root by the surrounding runner.
        self.scratch = Path(os.environ['DEEP32_DIAG_TEST_ROOT'])
        if not self.scratch.is_absolute() or not self.scratch.is_dir() or self.scratch.is_symlink():
            raise AssertionError('Expected an existing absolute synthetic test root')
        self.receipt = InstallReceipt('workstation', 'openbsd', '0.1.0-alpha.2', MANIFEST,
                                      {'models': MODELS}, GENERATION, FINGERPRINT)
        self.options = {
            'engine': ws.project_engine_status(encode(status())),
            'gateway': ws.project_gateway_status(ws.project_engine_status(encode(status())).status),
            'content': ws.project_import_state(encode(registry()), '.kb-' + GENERATION + '/' + GENERATION,
                                               registry_validator=imp_journal._validate_registry),
            'expectation': ws.ContentExpectation(MANIFEST, 5, 50, True),
            'auth': ws.project_forge_auth(endpoints_match=True, tls_valid=True, credential_valid=True,
                                           embed_http_status=200, rerank_http_status=200),
            'attestation': ws.project_attestor(snapshot(), MODELS, now=NOW, maximum_age=60),
            'engine_service': True, 'gateway_service': True,
        }

    def rows(self, **changes):
        options = {**self.options, **changes}
        return {item.id: item for item in ws.workstation_checks(self.receipt, **options)}

    def engine(self, value):
        return ws.project_engine_status(encode(value))

    def attestation(self, value, **kw):
        return ws.project_attestor(value, MODELS, now=NOW, maximum_age=kw.get('maximum_age', 60))

    def test_all_required_workstation_rows_pass(self):
        rows = self.rows()
        self.assertEqual(set(rows), {'engine', 'gateway', 'index-generation', 'index-fingerprint', 'forge-auth',
                                    'model-attestation.embeddings', 'model-attestation.reranker',
                                    'dense', 'reranker', 'index-health'})
        self.assertTrue(all(item.state is State.OK for item in rows.values()))
        result = report.build_report('workstation', 'openbsd', 'demo-node', STAMP, rows.values())
        self.assertEqual(result['exit_code'], 0)

    def test_missing_observations_never_claim_health(self):
        rows = {item.id: item for item in ws.workstation_checks(self.receipt)}
        self.assertEqual(rows['engine'].state, State.FAIL)
        self.assertEqual(rows['gateway'].state, State.FAIL)
        self.assertEqual(rows['forge-auth'].state, State.FAIL)
        self.assertEqual(rows['model-attestation.embeddings'].state, State.WARN)
        for name in ('index-generation', 'index-fingerprint', 'dense', 'reranker', 'index-health'):
            self.assertEqual(rows[name].state, State.SKIP)
            self.assertEqual(rows[name].blocked_by, ('engine',))
        self.assertEqual(report.build_report('workstation', 'openbsd', 'demo', STAMP,
                                             rows.values())['exit_code'], 2)

    def test_process_failure_is_not_overridden_by_status(self):
        for service in ('engine_service', 'gateway_service'):
            with self.subTest(service=service):
                name = service.split('_')[0]
                self.assertEqual(self.rows(**{service: False})[name].state, State.FAIL)
                self.assertEqual(self.rows(**{service: None})[name].state, State.FAIL)

    def test_bm25_ok_is_not_full_readiness(self):
        value = status()
        value['backends']['dense']['breaker'] = 'open'
        value['backends']['reranker']['ok'] = False
        rows = self.rows(engine=self.engine(value))
        self.assertEqual(rows['engine'].state, State.OK)
        self.assertEqual(rows['dense'].state, State.WARN)
        self.assertEqual(rows['reranker'].state, State.WARN)

    def test_breakers_errors_and_disabled_required_models_warn(self):
        for name in ('dense', 'reranker'):
            for changes in ({'breaker': 'open'}, {'breaker': 'half-open'}, {'breaker': 'half_open'},
                            {'breaker': None}, {'last_error_kind': SENTINEL},
                            {'enabled': False, 'ok': None}):
                with self.subTest(name=name, changes=changes):
                    value = status()
                    value['backends'][name].update(changes)
                    self.assertEqual(self.rows(engine=self.engine(value))[name].state, State.WARN)

    def test_gateway_backend_failure_is_visible(self):
        value = status()
        value['backends']['dense']['ok'] = False
        gateway = ws.project_gateway_status(self.engine(value).status)
        rows = self.rows(gateway=gateway)
        self.assertEqual(rows['dense'].state, State.WARN)
        self.assertEqual(rows['gateway'].state, State.WARN)

    def test_unmapped_degraded_backend_never_disappears(self):
        value = status()
        value['degraded'] = ['colbert']
        gateway = ws.project_gateway_status(self.engine(value).status)
        self.assertEqual(self.rows(gateway=gateway)['gateway'].state, State.WARN)

    def test_generation_receipt_registry_gateway_and_compatibility(self):
        self.assertEqual(self.rows(content=None)['index-generation'].state, State.FAIL)
        for content in (replace(self.options['content'], generation='different'),
                        replace(self.options['content'], manifest_sha256='c' * 64)):
            self.assertEqual(self.rows(content=content)['index-generation'].state, State.FAIL)
        self.assertEqual(self.rows(expectation=replace(self.options['expectation'], release_compatible=False))
                         ['index-generation'].state, State.FAIL)
        value = status()
        value['generation'] = 'gateway-stale'
        self.assertEqual(self.rows(gateway=ws.project_gateway_status(self.engine(value).status))
                         ['index-generation'].state, State.FAIL)
        self.receipt = replace(self.receipt, generation=None)
        self.assertEqual(self.rows()['index-generation'].state, State.FAIL)

    def test_fingerprint_mismatch_is_private_and_legacy_warns(self):
        value = status()
        value['corpus_fingerprint'] = 'c' * 64
        rows = self.rows(engine=self.engine(value))
        self.assertEqual(rows['index-fingerprint'].state, State.FAIL)
        self.assertNotIn('c' * 64, str(dict(rows['index-fingerprint'].observed_items)))
        self.receipt = replace(self.receipt, fingerprint=None)
        self.assertEqual(self.rows()['index-fingerprint'].state, State.WARN)

    def test_import_parser_requires_existing_validated_committed_state(self):
        pointer = '.kb-' + GENERATION + '/' + GENERATION
        with self.assertRaises(DiagError):
            ws.project_import_state(encode(registry()), pointer)
        for change in ('no-active', 'wrong-node', 'unowned-target', 'no-generations', 'duplicate-target'):
            value = registry()
            if change == 'no-active':
                value['active'] = None
            elif change == 'wrong-node':
                value['node'] = 'forge'
            elif change == 'unowned-target':
                value['active']['pointers']['kb'] = '.kb-other/other'
            elif change == 'no-generations':
                value['generations'] = []
            else:
                value['generations'] *= 2
            with self.subTest(change=change), self.assertRaises(DiagError):
                ws.project_import_state(encode(value), pointer, registry_validator=imp_journal._validate_registry)

    def test_import_pointer_traversal_refused(self):
        for pointer in ('/outside', '../outside', '.kb-other/../outside', '.kb-other/else',
                        '.kb-other\\other', '.kb-other/other\n', SENTINEL):
            with self.subTest(pointer=pointer), self.assertRaises(DiagError):
                ws.project_import_state(encode(registry()), pointer, registry_validator=imp_journal._validate_registry)

    def test_duplicate_json_and_invalid_counts_fail(self):
        raw = encode(status())[:-1] + b',"ok":true}'
        with self.assertRaises(DiagError):
            ws.project_engine_status(raw)
        for count in (-1, True, 2**63, 1.2, SENTINEL):
            value = status()
            value['n_chunks'] = count
            with self.subTest(count=count), self.assertRaises(DiagError):
                self.engine(value)

    def test_missing_count_or_probe_field_is_not_fabricated(self):
        value = status()
        del value['n_docs']
        with self.assertRaises(DiagError):
            self.engine(value)
        value = status()
        del value['backends']['dense']['last_error_kind']
        with self.assertRaises(DiagError):
            self.engine(value)

    def test_index_readiness_counts_and_manifest_contradictions(self):
        for name in ('bm25', 'kb', 'structure'):
            value = status()
            value['backends'][name]['ok'] = False
            if name == 'bm25':
                value['ok'] = False
            self.assertEqual(self.rows(engine=self.engine(value))['index-health'].state, State.FAIL)
        for name in ('n_docs', 'n_chunks'):
            for count in (0, None, 6):
                value = status()
                value[name] = count
                self.assertEqual(self.rows(engine=self.engine(value))['index-health'].state, State.FAIL)
        self.assertEqual(self.rows(expectation=None)['index-health'].state, State.FAIL)
        for documents, sections in ((0, 20), (5, 0), (6, 20)):
            value = status()
            value['backends']['structure'].update(documents=documents, sections=sections)
            self.assertEqual(self.rows(engine=self.engine(value))['index-health'].state, State.FAIL)

    def test_auth_failure_and_pairing_dependency(self):
        for change in ({'endpoints_match': False}, {'tls_valid': False}, {'credential_valid': False},
                       {'embed_http_status': 401}, {'rerank_http_status': 403}):
            with self.subTest(change=change):
                auth = replace(self.options['auth'], **change)
                self.assertEqual(self.rows(auth=auth)['forge-auth'].state, State.FAIL)
        rows = self.rows(pairing_failed=True)
        self.assertEqual(rows['forge-auth'].state, State.SKIP)
        self.assertEqual(rows['forge-auth'].blocked_by, ('pairing',))

    def test_attestor_legacy_or_missing_freshness_warns(self):
        for change in ('legacy', 'stale', 'future', 'missing-time', 'off'):
            value = snapshot()
            if change == 'legacy':
                for kind in ('embed', 'rerank'):
                    for key in ('model_id', 'revision', 'measurement_verified', 'device_kind'):
                        del value[kind][key]
            elif change == 'stale':
                value['embed']['checked_at'] = '2026-11-17T11:58:00Z'
            elif change == 'future':
                value['embed']['checked_at'] = '2026-11-17T12:01:00Z'
            elif change == 'missing-time':
                value['embed']['checked_at'] = None
            else:
                value['gpu_ext'] = 'off'
            with self.subTest(change=change):
                rows = self.rows(attestation=self.attestation(value))
                self.assertEqual(rows['model-attestation.embeddings'].state, State.WARN)

    def test_attestor_explicit_identity_measurement_or_auth_rejection_fails(self):
        for changes in ({'model_id': SENTINEL}, {'revision': '3' * 40}, {'measurement_verified': False},
                        {'verified': False, 'reason': 'forge-auth-rejected ' + SENTINEL}, {'device_kind': 'cpu'}):
            value = snapshot()
            value['embed'].update(changes)
            with self.subTest(changes=changes):
                rows = self.rows(attestation=self.attestation(value))
                self.assertEqual(rows['model-attestation.embeddings'].state, State.FAIL)

    def test_missing_attestation_cannot_pass_forge_auth(self):
        self.assertEqual(self.rows(attestation=None)['forge-auth'].state, State.FAIL)
        value = snapshot()
        value['embed'].update(verified=False, reason='forge-auth-rejected ' + SENTINEL)
        self.assertEqual(self.rows(attestation=self.attestation(value))['forge-auth'].state, State.FAIL)

    def test_missing_model_expectations_warn_individually(self):
        self.receipt = replace(self.receipt, inventory={'models': ()})
        rows = self.rows()
        self.assertEqual(rows['model-attestation.embeddings'].state, State.WARN)
        self.assertEqual(rows['model-attestation.reranker'].state, State.WARN)

    def test_output_and_representations_do_not_reveal_raw_fields(self):
        rows = self.rows()
        encoded = report.encode_report('workstation', 'openbsd', 'demo-node', STAMP, rows.values())
        self.assertNotIn(SENTINEL, encoded)
        for value in self.options.values():
            self.assertNotIn(SENTINEL, repr(value))
        failed = report.build_report('workstation', 'openbsd', 'demo-node', STAMP,
                                     self.rows(engine_service=False).values())
        engine = next(item for item in failed['checks'] if item['id'] == 'engine')
        self.assertIn('doas rcctl restart d32core', engine['fix'])
        self.assertTrue(any('StatusService.status:203' in source for source in engine['source']))

    def test_policy_has_no_file_process_network_or_listener_writes(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('A forbidden side effect was attempted')
        before = sorted(str(path) for path in self.scratch.rglob('*'))
        with ExitStack() as stack:
            for owner, name in ((builtins, 'open'), (os, 'open'), (os, 'mkdir'), (os, 'remove'),
                                (os, 'replace'), (subprocess, 'Popen'), (subprocess, 'run'), (socket, 'socket')):
                stack.enter_context(patch.object(owner, name, forbidden))
            self.assertTrue(all(item.state is State.OK for item in self.rows().values()))
            self.engine(status())
            self.attestation(snapshot())
        self.assertEqual(before, sorted(str(path) for path in self.scratch.rglob('*')))

    def test_openbsd_defaults_to_unqualified_and_denies_arbitrary_operations(self):
        with self.assertRaises(DiagError):
            OpenBSDObservations().observe(Operation.CORE_RUNNING)
        for operation in ('rcctl restart d32core', ('check', 'd32core'), 'ENGINE_STATUS'):
            with self.subTest(operation=operation), self.assertRaises(DiagError):
                OpenBSDObservations(lambda _: True, qualified=True).observe(operation)

    def test_openbsd_fixed_operations_and_type_caps(self):
        requests = []
        def observer(request):
            requests.append(request)
            return True if request.operation is Operation.CORE_RUNNING else encode(status())
        adapter = OpenBSDObservations(observer, qualified=True)
        self.assertTrue(adapter.observe(Operation.CORE_RUNNING))
        self.assertEqual(adapter.observe(Operation.ENGINE_STATUS), encode(status()))
        self.assertTrue(all(request.deadline > 0 and request.max_bytes == 1024 * 1024 for request in requests))
        with self.assertRaises(DiagError):
            OpenBSDObservations(lambda _: b'x' * (1024 * 1024 + 1), qualified=True).observe(Operation.ENGINE_STATUS)
        with self.assertRaises(DiagError):
            OpenBSDObservations(lambda _: 'raw private process output', qualified=True).observe(Operation.CORE_RUNNING)

    def test_openbsd_deadline_and_exception_redaction(self):
        clock = [0.0]
        def late(request):
            clock[0] = 11.0
            return True
        with self.assertRaises(DiagError) as caught:
            OpenBSDObservations(late, qualified=True, budget=Budget(lambda: clock[0])).observe(Operation.CORE_RUNNING)
        self.assertEqual(str(caught.exception), 'deadline')
        def denied(request):
            raise PermissionError(SENTINEL)
        with self.assertRaises(DiagError) as caught:
            OpenBSDObservations(denied, qualified=True).observe(Operation.IMPORT_REGISTRY)
        self.assertNotIn(SENTINEL, repr(caught.exception))
        self.assertIsNone(caught.exception.__context__)


if __name__ == '__main__':
    unittest.main()
