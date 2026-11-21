"""Invented PIN fixtures; never reads a tree, private input, or build output."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('check_reuse', HERE / 'check-reuse.py')
reuse = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reuse)


def pin(entries):
    return ''.join(hashlib.sha256(value.encode()).hexdigest() + '  ' + name + '\n'
                   for name, value in sorted(entries.items())).encode()


def fixture():
    return {name: name for name in [
        'pyrag/build-py312.sh', 'pyrag/build-wheels.sh', 'pyrag/make-venvs.sh',
        'pyrag/d32log/d32log.c', 'pyrag/locks/requirements-core.lock', 'pyrag/node/serve_core.py',
        'library/Makefile', 'node/asklink/Makefile', 'ship/tools/stage.sh',
        'ship/u10/bundle/build-native.sh', 'ship/u10/src/lib/core/d32_io.c',
        'ship/u14/apps/build.sh', 'ship/u2/hardening/build.sh', 'ship/u2/src/loopback/source.c',
        'ask-client/window/Makefile', 'ask-client/ui/view.c', 'distrib/ignored']}


class Reuse(unittest.TestCase):
    def rows(self, old=None, new=None):
        old = fixture() if old is None else old
        new = old if new is None else new
        result = reuse.compare(pin(old), pin(new))
        self.assertFalse(result['reuse_authorized'])
        return {row['stage'][:2]: row for row in result['stages']}

    def test_identical_sources_are_candidates_only(self):
        rows = self.rows()
        self.assertTrue(all(row['pinned_sources_equal'] for row in rows.values()))
        self.assertTrue(all(not row['reuse_authorized'] for row in rows.values()))
        self.assertTrue(all('existing-output-integrity' in row['required_external_checks']
                            for row in rows.values()))

    def test_unrelated_product_change_keeps_source_candidates(self):
        new = fixture() | {'distrib/ignored': 'changed', 'diagnostics/deep32_doctor.py': 'added'}
        self.assertTrue(all(row['pinned_sources_equal'] for row in self.rows(new=new).values()))

    def test_entire_pyrag_subtree_covers_runtime_wheels_and_venvs(self):
        for name in ['pyrag/d32log/d32log.c', 'pyrag/locks/new.lock', 'pyrag/src/new-source']:
            with self.subTest(name=name):
                rows = self.rows(new=fixture() | {name: 'changed'})
                self.assertEqual([stage for stage, row in rows.items() if not row['pinned_sources_equal']],
                                 ['20', '30', '40'])

    def test_stage_script_change_requires_library_rebuild(self):
        row = self.rows(new=fixture() | {'ship/tools/stage.sh': 'node-only-change'})['50']
        self.assertFalse(row['pinned_sources_equal'])
        self.assertEqual(row['changed'][0]['path'], 'ship/tools/stage.sh')

    def test_update_closure_covers_shared_makefile_inputs(self):
        for name in ['ship/u2/src/loopback/source.c', 'ship/u10/src/lib/core/d32_io.c',
                     'ask-client/ui/view.c', 'ask-client/window/Makefile', 'ship/u14/apps/build.sh']:
            with self.subTest(name=name):
                self.assertFalse(self.rows(new=fixture() | {name: 'changed'})['55']['pinned_sources_equal'])

    def test_add_delete_modify_reported_in_path_order(self):
        old = fixture()
        new = dict(old)
        del new['library/Makefile']
        new['library/new.c'] = 'added'
        new['node/asklink/Makefile'] = 'modified'
        row = self.rows(old, new)['50']
        self.assertEqual([item['change'] for item in row['changed']], ['deleted', 'added', 'modified'])
        self.assertFalse(row['pinned_sources_equal'])

    def test_incomplete_or_empty_dependency_set_never_passes(self):
        raw = pin({'distrib/only-subset': 'subset'})
        self.assertTrue(all(not row['pinned_sources_equal'] for row in reuse.compare(raw, raw)['stages']))
        old = fixture()
        del old['pyrag/node/serve_core.py']
        self.assertFalse(self.rows(old)['40']['pinned_sources_equal'])

    def test_exact_pin_format_and_safe_sorted_paths(self):
        digest = 'a' * 64
        bad = [b'', (digest + '  a').encode(), (digest.upper() + '  a\n').encode(),
               (digest + ' a\n').encode(), (digest + '   a\n').encode(),
               (digest + '  a\r\n').encode(), (digest + '  ./a\n').encode(),
               (digest + '  ../a\n').encode(), (digest + '  /a\n').encode(),
               (digest + '  a//b\n').encode(), (digest + '  a\\b\n').encode(),
               (digest + '  C:/a\n').encode(), (digest + '  a\n' + digest + '  a\n').encode(),
               (digest + '  b\n' + digest + '  a\n').encode()]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(reuse.PinError):
                reuse.parse_pin(raw)

    def test_deterministic_report_and_output_hazards(self):
        raw = pin(fixture())
        one = json.dumps(reuse.compare(raw, raw), sort_keys=True)
        self.assertEqual(one, json.dumps(reuse.compare(raw, raw), sort_keys=True))
        result = json.loads(one)
        self.assertIn('stage-10-removes-pyrag-wheelhouse', result['output_hazards']['30-wheels'])
        self.assertIn('input-pins-authentic-and-complete', result['unverified'])

    def test_cli_assert_selected_stages_and_invalid_pin(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-reuse-', dir=HERE) as temp:
            old, new = Path(temp) / 'old.sha256', Path(temp) / 'new.sha256'
            old.write_bytes(pin(fixture()))
            new.write_bytes(pin(fixture() | {'ship/tools/stage.sh': 'changed'}))
            args = ['--old-pin', str(old), '--new-pin', str(new)]
            for require, expected in [('20,30,40,55', 0), ('50', 1)]:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(reuse.main(args + ['--require', require]), expected)
                self.assertEqual(json.loads(output.getvalue())['schema'], 'deep32.build-source-reuse.v1')
            new.write_bytes(b'invalid')
            with contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(reuse.main(args), 2)
            self.assertNotIn(str(new), output.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
