"""Invented full-role transcripts and independent presentation contracts.

These are diagnostic observations, never retrieval questions or host receipts.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'diagnostics'))
from deep32_diag.errors import DiagError
from deep32_diag.models import State, TAILNET_FIX
from deep32_diag.report import (Reason, build_report, render_json, render_text,
                                row, sanitise_hostname, validate_report)

FIXTURES = Path(__file__).with_name('fixtures')
NOW = '2026-11-17T12:00:00Z'
SENTINEL = 'synthetic-private-sentinel-7f1892'


def invented_report(role, platform=None):
    specification = json.loads((FIXTURES / ('invented-' + role + '-input.json')).read_text(encoding='utf-8'))
    rows = []
    for item in specification['rows']:
        state = State(item['state'])
        reason = (Reason.PASSED if state is State.OK else Reason.BLOCKED if item.get('blocked_by')
                  else Reason.INAPPLICABLE if state is State.SKIP else Reason.UNAVAILABLE)
        rows.append(row(item['id'], state, reason, item.get('observed', {}),
                        blocked_by=item.get('blocked_by', ()),
                        public_identities=frozenset(specification['public_identities']),
                        policy=role + '.' + item['id'].split('.')[0]))
    return build_report(role, platform or specification['platform'], role + '-demo', NOW, rows)


class RendererTests(unittest.TestCase):
    def test_invented_transcripts_and_json_are_exact(self):
        for role in ('workstation', 'forge', 'headend'):
            with self.subTest(role=role):
                value = invented_report(role)
                self.assertEqual(render_text(value), (FIXTURES / ('invented-' + role + '.txt')).read_text(encoding='ascii'))
                self.assertEqual(render_json(value), (FIXTURES / ('invented-' + role + '.json')).read_text(encoding='ascii'))

    def test_finite_json_contract_summary_and_fix_order(self):
        schema = json.loads((ROOT / 'src/diagnostics/schemas/doctor-output.schema.json').read_text(encoding='utf-8'))
        for role in ('workstation', 'forge', 'headend'):
            value = invented_report(role)
            output = render_json(value)
            decoded = json.loads(output)
            self.assertEqual(set(decoded), set(schema['required']))
            self.assertEqual(decoded['schema'], schema['properties']['schema']['const'])
            self.assertEqual(set(decoded['summary']), {'ok', 'warn', 'fail', 'skip'})
            self.assertLessEqual(len(output.encode('ascii')), 256 * 1024)
            self.assertEqual(len({x['id'] for x in decoded['checks']}), len(decoded['checks']))
            fixes = [line for line in render_text(value).splitlines() if line.startswith('FIX ')]
            expected_fixes = []
            counts = {key: 0 for key in ('ok', 'warn', 'fail', 'skip')}
            ids = {x['id']: x for x in decoded['checks']}
            for check in decoded['checks']:
                self.assertEqual(set(check), set(schema['$defs']['check']['required']))
                self.assertTrue(re.fullmatch(r'[ -~]*', check['detail']))
                self.assertLessEqual(len(check['detail']), 512)
                self.assertTrue(set(check['observed']) <= set(schema['$defs']['observed']['properties']))
                counts[check['state'].lower()] += 1
                if check['state'] == 'OK':
                    self.assertIsNone(check['fix'])
                    self.assertEqual(check['blocked_by'], [])
                else:
                    self.assertTrue(re.fullmatch(r'[ -~]+', check['fix']))
                    self.assertLessEqual(len(check['fix']), 768)
                    expected_fixes.append('FIX ' + str(len(expected_fixes) + 1) + ' [' + check['id'] + ']: ' + check['fix'])
                for dependency in check['blocked_by']:
                    self.assertEqual(ids[dependency]['state'], 'FAIL')
            self.assertEqual(counts, decoded['summary'])
            self.assertEqual(fixes, expected_fixes)
            self.assertEqual(decoded['exit_code'], 2 if counts['fail'] else 1 if counts['warn'] else 0)

    def test_fixed_columns_header_and_summary(self):
        value = invented_report('forge')
        lines = render_text(value).splitlines()
        self.assertEqual(lines[0], 'Deep32 doctor  forge  forge-demo  ' + NOW)
        self.assertEqual(lines[1], 'CHECK                     STATE  DETAIL')
        for check, line in zip(value['checks'], lines[2:]):
            self.assertEqual(line[:24].strip(), check['id'])
            self.assertEqual(line[24:26], '  ')
            self.assertEqual(line[26:31].strip(), check['state'])
            self.assertEqual(line[31:33], '  ')
            self.assertEqual(line[33:], check['detail'])
        self.assertEqual(lines[2 + len(value['checks'])], '')
        summary = value['summary']
        self.assertEqual(lines[-1], f'{summary["ok"]} OK, {summary["warn"]} WARN, {summary["fail"]} FAIL')

    def test_role_platform_matrix_and_all_exit_contributions(self):
        for role, platforms in (('workstation', ('openbsd',)), ('forge', ('linux', 'windows')),
                                ('headend', ('linux', 'windows', 'macos'))):
            for platform in platforms:
                result = build_report(role, platform, 'invented-node', NOW,
                    [row('installation', State.FAIL, Reason.UNAVAILABLE)])
                self.assertEqual(json.loads(render_json(result))['platform'], platform)
        for state, code in ((State.OK, 0), (State.WARN, 1), (State.FAIL, 2)):
            item = row('installation', state, Reason.PASSED if state is State.OK else Reason.UNAVAILABLE)
            report = build_report('headend', 'linux', 'demo', NOW,
                                  [item, row('first-start', State.SKIP, Reason.INAPPLICABLE)])
            self.assertEqual(report['exit_code'], code)
            self.assertEqual(report['summary']['skip'], 1)

    def test_seal_refuses_forged_or_changed_printable_data(self):
        original = invented_report('forge')
        with self.assertRaises(DiagError):
            render_json(dict(original))
        for mutation in (lambda x: x.__setitem__('hostname', SENTINEL),
                         lambda x: x['checks'][0].__setitem__('detail', SENTINEL),
                         lambda x: x['checks'][0]['observed'].__setitem__('token', SENTINEL),
                         lambda x: x['summary'].__setitem__('fail', 100)):
            value = invented_report('forge')
            mutation(value)
            for render in (validate_report, render_text, render_json):
                with self.assertRaises(DiagError) as caught:
                    render(value)
                self.assertEqual(str(caught.exception), 'invalid-report')
                self.assertNotIn(SENTINEL, repr(caught.exception))

    def test_control_and_unicode_hostname_uses_fixed_local_alias(self):
        for value in ('node\n' + SENTINEL, '\x1b[31mnode', 'node\rsecret', 'n\u200bode', '',
                      'node\t' + SENTINEL, 'a' * 254, None):
            self.assertEqual(sanitise_hostname(value), 'local-node')
        self.assertEqual(sanitise_hostname('invented.local-node'), 'invented.local-node')

    def test_every_skip_has_fix_and_cannot_hide_a_required_failure(self):
        value = invented_report('headend')
        checks = {x['id']: x for x in value['checks']}
        self.assertEqual(checks['gateway-auth']['state'], 'FAIL')
        self.assertEqual(checks['mcp-rag-status']['blocked_by'], ['gateway-auth'])
        self.assertEqual(value['exit_code'], 2)
        self.assertTrue(all(check['fix'] for check in value['checks'] if check['state'] == 'SKIP'))

    def test_l3_relay_fix_is_exact_and_contains_no_peer_identity(self):
        value = invented_report('forge')
        check = next(x for x in value['checks'] if x['id'].startswith('tailnet-path'))
        self.assertEqual(check['fix'], TAILNET_FIX)
        self.assertEqual(check['observed'], {'peer_role': 'workstation', 'state': 'relayed'})

    def test_long_id_and_detail_continue_without_shifting_columns(self):
        dependencies = ['port.' + 'x' * 22 + str(i).zfill(2) for i in range(16)]
        rows = [row(name, State.FAIL, Reason.UNAVAILABLE) for name in dependencies]
        rows.append(row('peer-clock', State.SKIP, Reason.BLOCKED, blocked_by=dependencies))
        value = build_report('headend', 'linux', 'demo', NOW, rows)
        lines = render_text(value).splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith('peer-clock '))
        self.assertEqual(lines[start][26:31], 'SKIP ')
        self.assertEqual(lines[start + 1][:33], ' ' * 33)
        detail = lines[start][33:] + ' ' + lines[start + 1][33:]
        self.assertEqual(detail, value['checks'][-1]['detail'])
        name = lines[2][:24].strip() + lines[3][:24].strip()
        self.assertEqual(name, dependencies[0])
        self.assertEqual(lines[3][26:31], ' ' * 5)

    def test_renderer_has_no_write_network_process_or_listener_capability(self):
        values = [invented_report(role) for role in ('workstation', 'forge', 'headend')]
        def denied(*args, **kwargs):
            raise AssertionError('renderer attempted an external side effect')
        with ExitStack() as stack:
            for target in ('builtins.open', 'pathlib.Path.open', 'os.mkdir', 'os.remove', 'os.rename',
                           'os.chmod', 'socket.socket', 'socket.create_connection',
                           'subprocess.Popen', 'subprocess.run', 'tempfile.mkstemp'):
                stack.enter_context(patch(target, denied))
            for value in values:
                self.assertEqual(json.loads(render_json(value))['exit_code'], value['exit_code'])
                self.assertTrue(render_text(value).startswith('Deep32 doctor  '))


if __name__ == '__main__':
    unittest.main()
