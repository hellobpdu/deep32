"""Invented in-memory J10 root-bundle tests; no native verifier or service."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'diagnostics'))
from deep32_diag import selftest_trust as subject
from deep32_diag import selftest_contract
from deep32_diag.runner import CommandOutput, FileSnapshot
from deep32_diag.trust import PIN, RELEASE, VerifierQualification


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def digest(value):
    return hashlib.sha256(value).hexdigest()


class FakeBudget:
    def __init__(self):
        self.now = 100.0
        self.requests = []

    def clock(self):
        return self.now

    def deadline(self):
        return self.now + 10

    def observe(self, observer, request):
        self.requests.append(request)
        return observer(request)


class MemoryFiles:
    """Exact-slot fake with replacement detection and an auditable read order."""
    def __init__(self, data, paths):
        self.data, self.paths, self.reads = data, paths, []

    def matches_slots(self, paths):
        return paths == self.paths

    def read_existing_regular(self, slot, *, maximum):
        self.reads.append(slot)
        raw = self.data[slot]
        if len(raw) > maximum:
            raise ValueError('synthetic-private-error')
        return FileSnapshot(raw, (len(raw), digest(raw)), digest(raw))

    def unchanged(self, slot, snapshot):
        return digest(self.data[slot]) == snapshot.digest


class TrustTests(unittest.TestCase):
    def setUp(self):
        # Paths are fake capabilities, never opened or executed by these tests.
        base = Path(os.environ['D32_SUITE_SCRATCH'])
        self.paths = {slot: base / ('synthetic-' + slot) for slot in subject._SLOTS}
        self.data = {'signature': b'synthetic detached signature',
                     'keyring': b'synthetic public keyring', 'verifier': b'synthetic verifier'}
        settings = {'reranker': {'candidates': 15, 'max_chars': 1600, 'window': 'match'}}
        questions = {'schema_version': 1, 'bundle_id': 'fixture-lantern-v1',
                     'release': RELEASE, 'fixture_only': True, 'questions': [
                         {'id': 'fixture-lantern', 'text': 'What colour is the invented lantern?',
                          'collection': None, 'labels': [
                              {'doc_key': 'fixture-lantern-guide', 'exact_span': 'The lantern is amber.'}]}]}
        self.data['questions'] = encoded(questions)
        reference = {'schema_version': 1, 'bundle_id': questions['bundle_id'], 'release': RELEASE,
                     'fixture_only': True, 'questions_sha256': digest(self.data['questions']),
                     'question_count': 1, 'generation': 'fixture-generation',
                     'corpus_fingerprint': '0' * 64,
                     'models': {'embedding': {'model': 'fixture-embedding', 'revision': '1' * 40},
                                'reranker': {'model': 'fixture-reranker', 'revision': '2' * 40}},
                     'request': {'mode': 'hybrid', 'rerank': 'auto', 'max_chars': 2400,
                                 'deep': True, 'format': 'json', 'debug': True},
                     'settings': settings,
                     'settings_sha256': digest(json.dumps(settings, sort_keys=True, separators=(',', ':'),
                                                          ensure_ascii=True, allow_nan=False).encode('utf-8')),
                     'reference': {'hit_at_1': 1, 'hit_at_3': 1, 'hit_at_8': 1, 'mrr_at_10': 1},
                     'numerators': {'hit_at_1': 1, 'hit_at_3': 1, 'hit_at_8': 1,
                                    'reciprocal_rank_sum_at_10': 1},
                     'policy': {'kind': 'maximum-absolute-drop', 'tolerance': 0.02},
                     'measurement': {'complete_runs': 3, 'identical_ranks': True,
                                     'hardware_class': 'invented fixture', 'gateway_version': 'fixture-only',
                                     'search_p50_ms': 1, 'search_p95_ms': 2}}
        self.data['reference'] = encoded(reference)
        for slot, (name, _) in subject._ASSETS.items():
            if slot.endswith('_schema'):
                self.data[slot] = (ROOT / 'src' / 'diagnostics' / 'schemas' / name).read_bytes()
        self.asset_map = {'format': 'deep32-asset-map', 'format_version': 1,
                          'release': 'v' + RELEASE, 'entries': [
                              {'asset': name, 'media_paths': [{'disc': 'disc1', 'path': path}],
                               'install': [{'role': 'workstation', 'path': path},
                                           {'role': 'headend', 'path': path}]}
                              for name, path in subject._ASSETS.values()]}
        self.data['asset_map'] = encoded(self.asset_map)
        self.seal_root()
        self.files = MemoryFiles(self.data, self.paths)
        self.budget = FakeBudget()
        self.qualification = VerifierQualification(
            str(self.paths['verifier']), str(base / 'synthetic-readonly-home'),
            digest(self.data['verifier']), digest(self.data['keyring']), 'synthetic-qualification', True, True)
        self.called = 0

    def seal_root(self, extra=None):
        names = {'ASSET-MAP.json': digest(self.data['asset_map'])}
        names.update({name: digest(self.data[slot]) for slot, (name, _) in subject._ASSETS.items()})
        names.update(extra or {})
        self.data['manifest'] = ''.join(value + '  ' + name + '\n'
                                        for name, value in sorted(names.items())).encode('ascii')

    def good(self, request):
        self.called += 1
        self.assertFalse(any(slot in self.files.reads for slot in subject._ASSETS))
        self.assertNotIn('asset_map', self.files.reads)
        self.assertEqual(request.argv[0], str(self.paths['verifier']))
        self.assertEqual(request.argv[-2:], (str(self.paths['signature']), str(self.paths['manifest'])))
        return CommandOutput(0, ('[GNUPG:] GOODSIG A078C5F25D025DD6 Synthetic fixture\n'
            '[GNUPG:] VALIDSIG ' + PIN + ' 2026-11-17 1794916800 0 4 0 1 10 00 ' + PIN + '\n').encode('ascii'))

    def authenticate(self, **kwargs):
        options = {'qualification': self.qualification, 'observer': self.good,
                   'budget': self.budget, 'fixture_harness': True}
        options.update(kwargs)
        return subject.authenticate_bundle(self.files, self.paths, **options)

    def assert_refused(self, function=None, code=None, **kwargs):
        with self.assertRaises(subject.TrustError) as caught:
            (function or self.authenticate)(**kwargs)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn('synthetic-private-error', str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_valid_bundle_is_sealed_and_readonly(self):
        before = dict(self.data)
        binding = self.authenticate()
        self.assertIs(subject.require_bundle(binding, fixture_harness=True), binding.bundle)
        binding.recheck()
        self.assertEqual(before, self.data)
        self.assertEqual(self.called, 1)
        self.assertNotIn('lantern', repr(binding))

    def test_fixture_requires_explicit_harness_at_authentication(self):
        self.assert_refused(fixture_harness=False)

    def test_fixture_cannot_cross_release_runner_boundary(self):
        binding = self.authenticate()
        self.assert_refused(lambda: subject.require_bundle(binding), code='fixture-only')

    def test_raw_contract_and_unsealed_wrapper_cannot_run(self):
        raw = selftest_contract.load_bundle(self.data['questions'], self.data['reference'], fixture_harness=True)
        self.assert_refused(lambda: subject.require_bundle(raw, fixture_harness=True))
        binding = self.authenticate()
        self.assert_refused(lambda: subject.require_bundle(replace(binding), fixture_harness=True))

    def test_signature_failure_precedes_metadata_read(self):
        self.assert_refused(observer=lambda _: CommandOutput(1, b'synthetic-private-error'), code='bad-signature')
        self.assertFalse(any(slot in self.files.reads for slot in subject._ASSETS))
        self.assertNotIn('asset_map', self.files.reads)

    def test_wrong_signer_refused(self):
        output = CommandOutput(0,
            ('[GNUPG:] GOODSIG 111 Synthetic\n[GNUPG:] VALIDSIG ' + '1' * 40 +
             ' 2026-11-17 1794916800 0 4 0 1 10 00 ' + '1' * 40 + '\n').encode('ascii'))
        self.assert_refused(observer=lambda _: output, code='bad-signature')

    def test_unqualified_verifier_never_observed(self):
        for change in ({'no_persistent_writes': False}, {'bounded_process_tree': False},
                       {'binary_sha256': '0' * 64}, {'keyring_sha256': '0' * 64}):
            with self.subTest(change=change):
                self.assert_refused(qualification=replace(self.qualification, **change), code='unqualified-verifier')
        self.assertEqual(self.called, 0)

    def test_missing_extra_duplicate_or_relative_slots_refused(self):
        for variant in ('missing', 'extra', 'duplicate', 'relative'):
            with self.subTest(variant=variant):
                paths = dict(self.paths)
                if variant == 'missing':
                    paths.pop('questions')
                elif variant == 'extra':
                    paths['unrelated'] = paths['questions']
                elif variant == 'duplicate':
                    paths['questions'] = paths['reference']
                else:
                    paths['questions'] = Path('relative')
                self.assert_refused(lambda: subject.authenticate_bundle(self.files, paths,
                    qualification=self.qualification, observer=self.good, budget=self.budget, fixture_harness=True))
        self.assertEqual(self.called, 0)

    def test_tampered_each_bundle_member_refused(self):
        for slot in ('asset_map', *subject._ASSETS):
            with self.subTest(slot=slot):
                raw = self.data[slot]
                self.data[slot] += b' '
                self.files.reads.clear()
                self.assert_refused(code='bundle-hash')
                self.data[slot] = raw

    def test_signed_tampered_schema_still_refused(self):
        self.data['questions_schema'] += b' '
        self.seal_root()
        self.assert_refused(code='bundle-schema')

    def test_root_missing_asset_or_map_refused(self):
        for name in ('ASSET-MAP.json', 'selftest-questions.json'):
            with self.subTest(name=name):
                original = self.data['manifest']
                self.data['manifest'] = b''.join(line for line in original.splitlines(True)
                                                if not line.endswith(b'  ' + name.encode('ascii') + b'\n'))
                self.files.reads.clear()
                self.assert_refused(code='bundle-hash')
                self.data['manifest'] = original

    def test_root_unsafe_duplicate_case_and_self_hash_refused(self):
        for name in ('../escape', 'selftest-QUESTIONS.json', 'SHA256SUMS', 'SHA256SUMS.sig'):
            with self.subTest(name=name):
                original = self.data['manifest']
                self.data['manifest'] += b'0' * 64 + b'  ' + name.encode('ascii') + b'\n'
                self.files.reads.clear()
                self.assert_refused(code='bundle-hash')
                self.data['manifest'] = original

    def test_root_strict_ascii_lf_and_spacing(self):
        original = self.data['manifest']
        for raw in (original[:-1], original.replace(b'\n', b'\r\n'), original.replace(b'  ', b' *'),
                    original + b'\xff\n', original + b'\n'):
            with self.subTest(raw=raw[-3:]):
                self.data['manifest'] = raw
                self.files.reads.clear()
                self.assert_refused(code='bundle-hash')

    def test_map_unknown_destinations_traversal_wrong_role_refused(self):
        for change in ('traversal', 'wrong-path', 'forge', 'duplicate', 'unknown', 'wrong-release'):
            with self.subTest(change=change):
                value = json.loads(encoded(self.asset_map))
                if change == 'traversal':
                    value['entries'][0]['install'][0]['path'] = 'selftest/../escape'
                elif change == 'wrong-path':
                    value['entries'][0]['media_paths'][0]['path'] = 'selftest/reference.json'
                elif change == 'forge':
                    value['entries'][0]['install'][0]['role'] = 'forge'
                elif change == 'duplicate':
                    value['entries'].append(value['entries'][0])
                elif change == 'unknown':
                    value['entries'][0]['extra'] = True
                else:
                    value['release'] = 'v0.0.0-other'
                self.data['asset_map'] = encoded(value)
                self.seal_root()
                self.files.reads.clear()
                self.assert_refused(code='asset-map')

    def test_signed_duplicate_json_key_refused(self):
        self.data['asset_map'] = self.data['asset_map'].replace(b'{', b'{"format":"duplicate",', 1)
        self.seal_root()
        self.assert_refused()

    def test_safe_unopened_sbom_coexistence(self):
        value = json.loads(encoded(self.asset_map))
        value['entries'].append({'asset': 'fixture.spdx.json', 'media_paths': [
            {'disc': 'disc3', 'path': 'sbom/fixture.spdx.json'}], 'install': []})
        self.data['asset_map'] = encoded(value)
        self.seal_root({'fixture.spdx.json': '0' * 64})
        self.authenticate()
        self.assertNotIn('fixture.spdx.json', self.files.reads)

    def test_post_verification_replacement_refused(self):
        def replaced(request):
            output = self.good(request)
            self.data['signature'] += b'changed'
            return output
        self.assert_refused(observer=replaced, code='snapshot-changed')

    def test_post_run_any_member_change_refused(self):
        binding = self.authenticate()
        for slot in subject._SLOTS:
            with self.subTest(slot=slot):
                raw = self.data[slot]
                self.data[slot] += b'changed'
                self.assert_refused(binding.recheck, code='snapshot-changed')
                self.data[slot] = raw

    def test_late_verifier_refused(self):
        def late(request):
            result = self.good(request)
            self.budget.now = request.deadline
            return result
        self.assert_refused(observer=late, code='deadline')

    def test_private_exception_is_suppressed_and_no_output(self):
        output, errors = io.StringIO(), io.StringIO()
        def failed(_):
            raise RuntimeError('synthetic-private-error')
        with redirect_stdout(output), redirect_stderr(errors):
            self.assert_refused(observer=failed)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), '')

    def integrated_run(self, *, change_after_search=False):
        """Use the real trust seal and runner with the existing wire-only fake."""
        import test_mcp as wire
        from deep32_diag import selftest
        from datetime import datetime, timezone
        clock = wire.Clock()
        self.budget = selftest.SelftestBudget(clock)
        expected = selftest_contract.load_bundle(self.data['questions'], self.data['reference'],
                                                fixture_harness=True)
        status = wire.status_value()
        status.update(generation=expected.reference['generation'],
                      corpus_fingerprint=expected.reference['corpus_fingerprint'],
                      settings=json.loads(selftest_contract.canonical_bytes(expected.reference['settings'])))
        label = expected.questions[0]['labels'][0]
        def tool(value):
            return wire.rpc({'content': [{'type': 'text', 'text': json.dumps(value)}]})
        transport = wire.FakeTransport(wire.FakeResponse(status=401), wire.initial(session='fixture-session'),
            wire.notified(), wire.tool_list(), tool(status), tool({
                'generation': expected.reference['generation'], 'degraded': [], 'hits': [
                    {'id': 'fixture-delivered-hit', 'doc_key': label['doc_key'],
                     'text': label['exact_span'], 'reranked': True}]}), tool(status))
        events = []
        stamp = '2026-11-17T12:00:00Z'
        now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp()
        def verified(request):
            events.append('signature')
            return self.good(request)
        def authenticate(budget):
            self.assertIs(budget, self.budget)
            return self.authenticate(observer=verified)
        def open_session(budget):
            events.append('credential')
            self.assertEqual(events, ['signature', 'credential'])
            client = selftest.SelftestMCPClient(
                selftest.WorkstationEndpoint('http://127.0.0.1:9090/mcp', '127.0.0.1', 9090),
                wire.TestCredential(), transport=transport, budget=budget)
            def attest(deadline):
                snapshot = {'gpu_ext': 'on'}
                for kind, name in (('embed', 'embedding'), ('rerank', 'reranker')):
                    model = expected.reference['models'][name]
                    snapshot[kind] = {'verified': True, 'measurement_verified': True,
                        'device_kind': 'gpu', 'checked_at': stamp,
                        'model_id': model['model'], 'revision': model['revision']}
                return snapshot
            return selftest.SelftestSession(client, attest, lambda deadline: True, lambda: now)
        if change_after_search:
            def change():
                if len(transport.requests) == 7:
                    self.data['reference'] += b'changed after search'
            transport.before_open = change
        result = selftest.run_selftest(evidence=selftest.SelftestEvidence('workstation', authenticate, open_session),
            budget=self.budget, fixture_harness=True, details=True, checked_at=stamp)
        self.assertEqual(events, ['signature', 'credential'])
        self.assertTrue(all(response.closed for response in transport.responses))
        return result

    def test_real_trust_seal_to_runner_integration(self):
        result = self.integrated_run()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['measured']['mrr_at_10'], 1)
        self.assertEqual(result['details'], [{'id': 'fixture-lantern', 'first_relevant_rank': 1}])

    def test_real_trust_post_search_change_discards_metrics(self):
        result = self.integrated_run(change_after_search=True)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['exit_code'], 2)
        self.assertIsNone(result['measured'])
        self.assertEqual(result['details'], [])
        self.assertEqual(result['completed_questions'], 0)


if __name__ == '__main__':
    unittest.main()
