"""Invented beacons and kites only; no live service or reference calibration."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import builtins
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'diagnostics'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep32_diag import cli, mcp, selftest, selftest_cli
from deep32_diag.selftest_contract import load_bundle, canonical_bytes, METRICS
import test_mcp as wire
from selftest_support import bundle_bytes

STAMP = '2026-11-17T12:00:00Z'
NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp()
PRIVATE = 'synthetic-private-selftest-sentinel'


class Binding:
    def __init__(self, bundle):
        self.bundle, self.checked = bundle, False

    def recheck(self):
        self.checked = True


def tool(value, **extra):
    return wire.rpc({'content': [{'type': 'text', 'text': json.dumps(value)}], **extra})


def healthy(bundle):
    status = wire.status_value()
    status.update(generation=bundle.reference['generation'],
        corpus_fingerprint=bundle.reference['corpus_fingerprint'],
        settings=json.loads(canonical_bytes(bundle.reference['settings'])))
    return status


def attestation(bundle):
    snapshot = {'gpu_ext': 'on'}
    for kind, name in (('embed', 'embedding'), ('rerank', 'reranker')):
        snapshot[kind] = {'verified': True, 'measurement_verified': True,
            'device_kind': 'gpu', 'checked_at': STAMP,
            'model_id': bundle.reference['models'][name]['model'],
            'revision': bundle.reference['models'][name]['revision']}
    return snapshot


def hits_for(question, rank):
    hits = [{'id': 'invented-miss-' + str(i), 'doc_key': 'invented-other',
             'text': 'An invented white paper lantern.', 'reranked': True}
            for i in range(1, (rank or 1) + 1)]
    if rank is not None:
        label = question['labels'][0]
        hits[rank - 1] = {'id': 'invented-hit', 'doc_key': label['doc_key'],
                         'text': label['exact_span'], 'reranked': True}
    return hits


class RunnerTests(unittest.TestCase):
    def prepare(self, *, fixture_only=True, role='workstation', ranks=None,
                status_edit=None, final_edit=None, search_edit=None, attestor=None,
                transport=None, endpoint=None):
        qraw, rraw = bundle_bytes(fixture_only=fixture_only)
        bundle = load_bundle(qraw, rraw, fixture_harness=fixture_only)
        self.bundle, self.binding = bundle, Binding(bundle)
        self.clock = wire.Clock()
        self.budget = selftest.SelftestBudget(self.clock)
        start, end = healthy(bundle), healthy(bundle)
        if status_edit:
            status_edit(start)
        if final_edit:
            final_edit(end)
        ranks = [1, 2] if ranks is None else ranks
        replies = [wire.FakeResponse(status=401), wire.initial(session='invented-session'),
                   wire.notified(), wire.tool_list(), tool(start)]
        for index, (question, rank) in enumerate(zip(bundle.questions, ranks)):
            value = {'generation': bundle.reference['generation'], 'degraded': [],
                     'hits': hits_for(question, rank)}
            if search_edit:
                search_edit(value, index)
            replies.append(tool(value))
        replies.append(tool(end))
        self.transport = transport or wire.FakeTransport(*replies)
        self.endpoint = endpoint or selftest.WorkstationEndpoint('http://127.0.0.1:9090/mcp', '127.0.0.1', 9090)
        self.client = selftest.SelftestMCPClient(self.endpoint, wire.TestCredential(),
            transport=self.transport, budget=self.budget)
        self.events = []
        def authenticate(budget):
            self.events.append('trust')
            return self.binding
        def session(budget):
            self.events.append('credential')
            return selftest.SelftestSession(self.client,
                attestor or (lambda deadline: attestation(bundle)),
                lambda deadline: True, lambda: NOW)
        evidence = selftest.SelftestEvidence(role, authenticate, session,
            'codex' if role == 'headend' else None,
            str(Path('C:/invented-selected-profile')) if role == 'headend' else None)
        return evidence

    def run_case(self, evidence, *, details=True, fixture_harness=True):
        def require(binding, *, fixture_harness=False):
            if binding is not self.binding or self.bundle.fixture_only and not fixture_harness:
                raise ValueError(PRIVATE)
            return binding.bundle
        with patch('deep32_diag.selftest.require_bundle', require):
            return selftest.run_selftest(evidence=evidence, budget=self.budget,
                details=details, fixture_harness=fixture_harness,
                agent=evidence.agent, profile_dir=evidence.profile_dir,
                hostname='sample-node', checked_at=STAMP)

    def assert_incomplete(self, result):
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['exit_code'], 2)
        self.assertIsNone(result['measured'])
        self.assertEqual(result['details'], [])
        self.assertEqual(result['completed_questions'], 0)
        self.assertNotIn(PRIVATE, selftest.render_json(result) + selftest.render_text(result))
        self.assertNotIn(wire.TEST_TOKEN, selftest.render_json(result) + selftest.render_text(result))

    def test_complete_profile_order_and_no_label_leak(self):
        evidence = self.prepare()
        result = self.run_case(evidence)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['measured']['mrr_at_10'], .75)
        self.assertEqual(self.events, ['trust', 'credential'])
        self.assertTrue(self.binding.checked)
        calls = [json.loads(r.body)['params'] for r in self.transport.requests
                 if json.loads(r.body)['method'] == 'tools/call']
        searches = [c['arguments'] for c in calls if c['name'] == 'search_rag']
        for question, sent in zip(self.bundle.questions, searches):
            expected = dict(self.bundle.reference['request'])
            expected['query'] = question['text']
            if question['collection'] is not None:
                expected['doc_type'] = question['collection']
            self.assertEqual(sent, expected)
            self.assertNotIn('labels', sent)
            self.assertNotIn('docs', sent)
            self.assertNotIn('limit', sent)
        self.assertTrue(all(response.closed for response in self.transport.responses))
        self.assertEqual([d['first_relevant_rank'] for d in result['details']], [1, 2])

    def test_complete_failure_and_short_zero_hit_lists(self):
        result = self.run_case(self.prepare(ranks=[None, None]))
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['exit_code'], 1)
        self.assertEqual(set(result['measured'].values()), {0.0})
        self.assertIn('0 OK, 0 WARN, 4 FAIL; FAIL', selftest.render_text(result))
        def empty(value, index):
            value['hits'] = []
        result = self.run_case(self.prepare(search_edit=empty))
        self.assertEqual(result['status'], 'FAIL')

    def test_each_backend_and_breaker_before_queries(self):
        edits = [lambda v: v['backends']['dense'].update(enabled=False, ok=None),
                 lambda v: v['backends']['reranker'].update(enabled=False, ok=None)]
        for name in ('bm25', 'dense', 'reranker', 'kb', 'structure'):
            edits.append(lambda v, n=name: v['backends'][n].update(ok=False))
        for name in ('dense', 'reranker'):
            for breaker in (None, 'open', 'half-open'):
                edits.append(lambda v, n=name, b=breaker: v['backends'][n].update(breaker=b))
        for edit in edits:
            with self.subTest(edit=edit):
                result = self.run_case(self.prepare(status_edit=edit))
                self.assert_incomplete(result)
                self.assertFalse(any(b'"search_rag"' in r.body for r in self.transport.requests))

    def test_each_degraded_reason_before_during_and_after(self):
        for reason in ('bm25', 'dense', 'reranker', 'kb', 'structure', 'colbert'):
            for where in ('before', 'during', 'after'):
                with self.subTest(reason=reason, where=where):
                    kwargs = {'status_edit' if where == 'before' else 'final_edit':
                        lambda v: v.update(degraded=[reason])} if where != 'during' else {
                        'search_edit': lambda v, i: v.update(degraded=[reason]) if i == 1 else None}
                    self.assert_incomplete(self.run_case(self.prepare(**kwargs)))

    def test_generation_fingerprint_settings_drift(self):
        for key, value in (('generation', 'invented-other-generation'),
                ('corpus_fingerprint', '12' * 32), ('settings', {'invented-changed': True})):
            for where in ('status_edit', 'final_edit'):
                with self.subTest(key=key, where=where):
                    self.assert_incomplete(self.run_case(self.prepare(**{
                        where: lambda v, k=key, x=value: v.update({k: x})})))
        self.assert_incomplete(self.run_case(self.prepare(search_edit=lambda v, i:
            v.update(generation='invented-changed') if i == 1 else None)))

    def test_search_protocol_failures_never_become_misses(self):
        edits = [lambda v: v.pop('hits'), lambda v: v.update(hits=None),
            lambda v: v['hits'][0].update(id=''), lambda v: v['hits'][0].update(doc_key=None),
            lambda v: v['hits'][0].update(text=None), lambda v: v['hits'][0].update(reranked=False),
            lambda v: v['hits'].append(dict(v['hits'][0])),
            lambda v: v['hits'][0].update(rank=2), lambda v: v['hits'][0].update(rank=True),
            lambda v: v['hits'][0].update(text='\ud800'),
            lambda v: v.update(hits=v['hits'] * 33)]
        for edit in edits:
            with self.subTest(edit=edit):
                self.assert_incomplete(self.run_case(self.prepare(search_edit=lambda v, i:
                    edit(v) if i == 1 else None)))

    def test_models_before_and_after_and_unmeasured_legacy(self):
        for kind in ('embed', 'rerank'):
            for key, value in (('verified', False), ('measurement_verified', False),
                    ('device_kind', 'cpu'), ('revision', 'ab' * 20), ('model_id', 'other/model'),
                    ('checked_at', '2000-01-01T00:00:00Z')):
                for final in (False, True):
                    with self.subTest(kind=kind, key=key, final=final):
                        calls = []
                        def attest(deadline):
                            snapshot = attestation(self.bundle)
                            calls.append(1)
                            if not final or len(calls) == 2:
                                snapshot[kind][key] = value
                            return snapshot
                        self.assert_incomplete(self.run_case(self.prepare(attestor=attest)))

    def test_http_auth_redirect_rate_limit_server_and_bad_json(self):
        for status in (401, 403, 302, 429, 500, 503):
            evidence = self.prepare()
            self.transport.replies[6] = wire.FakeResponse(PRIVATE.encode(), status=status)
            result = self.run_case(evidence)
            self.assert_incomplete(result)
            if status in (401, 403):
                self.assertIn('Gateway authentication failed', selftest.render_text(result))
        for raw in (b'{', b'{"jsonrpc":"2.0","id":7,"result":NaN}', b'{}'):
            evidence = self.prepare()
            self.transport.replies[6] = wire.FakeResponse(raw)
            self.assert_incomplete(self.run_case(evidence))

    def test_tool_error_missing_body_and_malformed_status(self):
        for replacement in (tool({}, isError=True), wire.rpc({'content': []}),
                            tool({'ok': True}), tool({'ok': False, 'degraded': [PRIVATE]})):
            evidence = self.prepare()
            self.transport.replies[4] = replacement
            self.assert_incomplete(self.run_case(evidence))

    def test_operation_and_global_deadlines(self):
        evidence = self.prepare()
        captured = []
        def opened():
            request = self.transport.requests[-1]
            captured.append((json.loads(request.body), request.deadline, request.connect_deadline))
        self.transport.before_open = opened
        self.assertEqual(self.run_case(evidence)['exit_code'], 0)
        for payload, end, connect in captured:
            seconds = 30 if payload.get('params', {}).get('name') == 'search_rag' else 10
            self.assertLessEqual(end, self.clock.now + seconds)
            self.assertLessEqual(connect, self.clock.now + 5)
            self.assertLessEqual(connect, end)
        for seconds in (30, 901):
            evidence = self.prepare()
            original = self.transport.replies[6]
            def late(request):
                self.clock.now += seconds
                return original(request)
            self.transport.replies[6] = late
            self.assert_incomplete(self.run_case(evidence))

    def test_interrupt_and_snapshot_changes_discard_subset(self):
        for error in (KeyboardInterrupt(), TimeoutError(PRIVATE), RuntimeError(PRIVATE)):
            evidence = self.prepare()
            self.transport.replies[6] = lambda request, e=error: (_ for _ in ()).throw(e)
            self.assert_incomplete(self.run_case(evidence))
            self.assertTrue(all(r.closed for r in self.transport.responses))
        evidence = self.prepare()
        original = evidence.open_session
        evidence = replace(evidence, open_session=lambda b: replace(original(b), unchanged=lambda d: False))
        self.assert_incomplete(self.run_case(evidence))
        evidence = self.prepare()
        self.binding.recheck = lambda: (_ for _ in ()).throw(ValueError(PRIVATE))
        self.assert_incomplete(self.run_case(evidence))

    def test_trust_always_precedes_credentials(self):
        evidence = self.prepare()
        evidence = replace(evidence, authenticate=lambda b: (_ for _ in ()).throw(ValueError(PRIVATE)))
        self.assert_incomplete(self.run_case(evidence))
        self.assertEqual(self.events, [])
        self.assertEqual(self.transport.requests, [])

    def test_fixture_requires_python_harness_and_cli_has_no_bypass(self):
        evidence = self.prepare()
        self.assert_incomplete(self.run_case(evidence, fixture_harness=False))
        self.assertEqual(self.events, ['trust'])
        output = io.StringIO()
        with patch('deep32_diag.selftest.require_bundle', side_effect=ValueError(PRIVATE)):
            self.assertEqual(cli.main(['selftest', '--json'], evidence=evidence, stdout=output,
                budget=self.budget), 2)
        self.assertIsNone(json.loads(output.getvalue())['measured'])

    def test_headend_uses_only_matching_profile_and_no_agent_process(self):
        endpoint = mcp.EnrolledEndpoint(wire.URL, 'workstation.example.test')
        evidence = self.prepare(role='headend', endpoint=endpoint)
        self.assertEqual(self.run_case(evidence)['exit_code'], 0)
        evidence = self.prepare(role='headend', endpoint=endpoint)
        with patch('deep32_diag.selftest.require_bundle', return_value=self.bundle):
            result = selftest.run_selftest(evidence=evidence, agent='claude',
                profile_dir=evidence.profile_dir, budget=self.budget)
        self.assert_incomplete(result)
        self.assertEqual(self.events, [])

    def test_forge_is_fixed_no_query(self):
        evidence = self.prepare(role='forge')
        result = self.run_case(evidence)
        self.assert_incomplete(result)
        self.assertEqual(selftest.render_text(result), selftest.FORGE_MESSAGE + '\n')
        self.assertEqual(self.events, [])

    def test_loopback_exact_literal_endpoints(self):
        for host, url in (('127.0.0.1', 'http://127.0.0.1:9090/mcp'),
                          ('::1', 'http://[::1]:9090/mcp')):
            selftest.WorkstationEndpoint(url, host, 9090)
        for url, host in ((wire.URL, 'localhost'), ('http://127.0.0.1:9090/mcp?x=1', '127.0.0.1'),
                ('http://127.0.0.2:9090/mcp', '127.0.0.2'), ('http://127.0.0.1:9090/mcp/', '127.0.0.1')):
            with self.assertRaises(mcp.MCPError):
                selftest.WorkstationEndpoint(url, host, 9090)

    def test_denied_writes_processes_network_and_secret_output(self):
        evidence = self.prepare()
        def forbidden(*args, **kwargs):
            raise AssertionError('Unexpected native operation')
        with ExitStack() as stack:
            for target in ('builtins.open', 'os.open', 'os.mkdir', 'os.remove', 'os.replace',
                           'subprocess.Popen', 'socket.socket'):
                stack.enter_context(patch(target, forbidden))
            result = self.run_case(evidence)
            output = selftest.render_text(result) + selftest.render_json(result)
        self.assertEqual(result['exit_code'], 0)
        for secret in (PRIVATE, wire.TEST_TOKEN, 'Bearer ' + wire.TEST_TOKEN):
            self.assertNotIn(secret, output)

    def test_repeated_ranks_and_render_only_ids(self):
        outputs = []
        for _ in range(2):
            outputs.append(self.run_case(self.prepare()))
        self.assertEqual(outputs[0], outputs[1])
        output = selftest.render_text(outputs[0])
        self.assertIn('QUESTION', output)
        self.assertIn('0.750', output)
        for question in self.bundle.questions:
            self.assertNotIn(question['text'], output)
            for label in question['labels']:
                self.assertNotIn(label['exact_span'], output)
                self.assertNotIn(label['doc_key'], output)

    def test_result_mutation_refused(self):
        result = self.run_case(self.prepare())
        result['status'] = 'FAIL'
        with self.assertRaises(selftest.SelftestError):
            selftest.render_json(result)
        result = self.run_case(self.prepare())
        result.hostname = PRIVATE
        with self.assertRaises(selftest.SelftestError):
            selftest.render_text(result)

    def test_cli_fixed_options_json_and_source_fail_closed(self):
        for option in ('--token', '--url', '--questions', '--reference', '--unsigned',
                       '--skip-verify', '--mode', '--tolerance', '--role', '--data-root',
                       '--fixture-harness', '--json --json'):
            output = io.StringIO()
            args = ['selftest', '--json'] + option.split() + ([] if ' ' in option else [PRIVATE])
            self.assertEqual(cli.main(args, stdout=output), 2)
            value = json.loads(output.getvalue())
            self.assertIsNone(value['measured'])
            self.assertNotIn(PRIVATE, output.getvalue())
        output = io.StringIO()
        self.assertEqual(cli.main(['selftest', '--json', '--details'], stdout=output), 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'INCOMPLETE')
        with self.assertRaises(Exception):
            selftest_cli.parse_arguments(['selftest', '--agent', 'codex', '--profile-dir', 'relative'])

    def test_cli_complete_json_and_fixed_transcript(self):
        evidence = self.prepare(fixture_only=False)
        output = io.StringIO()
        with patch('deep32_diag.selftest.require_bundle', return_value=self.bundle):
            self.assertEqual(cli.main(['selftest', '--json', '--details'], stdout=output,
                evidence=evidence, budget=self.budget, hostname='sample-node', checked_at=STAMP), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value['status'], 'PASS')
        self.assertEqual(len(value['details']), 2)
        self.assertEqual(set(value), {'schema_version', 'bundle_id', 'release', 'fixture_only',
            'status', 'exit_code', 'completed_questions', 'question_count', 'checks',
            'measured', 'reference', 'details'})
        result = self.run_case(self.prepare())
        text = selftest.render_text(result)
        self.assertTrue(text.startswith('Deep32 selftest  Workstation  sample-node  ' + STAMP + '\n'))
        self.assertIn('hit@1                     0.500     0.500 OK', text)
        self.assertIn('MRR@10                    0.750     0.750 OK', text)

    def test_serial_search_has_30_seconds_and_closes_each_response(self):
        evidence = self.prepare()
        original = self.transport.replies[5]
        def slow(request):
            self.clock.now += 20
            return original(request)
        self.transport.replies[5] = slow
        self.assertEqual(self.run_case(evidence)['exit_code'], 0)
        self.assertTrue(all(r.closed for r in self.transport.responses))

    def test_remote_messages_and_passages_are_never_output(self):
        evidence = self.prepare(search_edit=lambda v, i: v.update(
            debug=PRIVATE, untrusted_error=wire.TEST_TOKEN))
        result = self.run_case(evidence)
        output = selftest.render_json(result) + selftest.render_text(result)
        self.assertNotIn(PRIVATE, output)
        self.assertNotIn(wire.TEST_TOKEN, output)
        self.assertEqual(result['exit_code'], 0)

    def test_whole_run_deadline_includes_final_check(self):
        evidence = self.prepare()
        original = evidence.open_session
        def open_session(budget):
            session = original(budget)
            def changed(deadline):
                self.clock.now = budget.end
                return True
            return replace(session, unchanged=changed)
        self.assert_incomplete(self.run_case(replace(evidence, open_session=open_session)))

    def test_signal_handlers_restore(self):
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        with self.assertRaises(KeyboardInterrupt):
            with selftest.interruptions():
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertEqual(previous, {s: signal.getsignal(s) for s in previous})


if __name__ == '__main__':
    unittest.main()
