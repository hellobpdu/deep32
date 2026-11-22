"""Offline regressions for eval paths passed across different working directories."""
from contextlib import redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve()
KIT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location('eval_path_builder', KIT / 'containers/builder/builder.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class EvalPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='eval-paths-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.caller = self.root / 'caller'
        self.caller.mkdir()
        self.engine = self.root / 'engine'
        self.engine.mkdir()
        self.eval_root = self.caller / 'operator-eval'
        self.selected = self.eval_root / 'sets' / 'synthetic-set'
        self.selected.mkdir(parents=True)
        self.served = self.root / 'served-one'
        self.served.mkdir()
        (self.served / 'BUILD_REPORT.json').write_text(
            json.dumps({'status': 'complete', 'failed_steps': [], 'documents': {'failed': 0}}),
            encoding='ascii')
        self.args = SimpleNamespace(
            generation='build-one', served_generation='served-one', engine_root=str(self.engine),
            build_root=str(self.root / 'build-one'), served_root=str(self.served),
            eval_root=Path('operator-eval'), kept_manifest=str(self.caller / 'kept.json'),
            python=sys.executable, corpus_repo='synthetic-corpus', corpus_ref='synthetic-ref',
            expect_fingerprint='synthetic-fingerprint', export_dir=str(self.root / 'export'),
            recipient='synthetic-recipient')
        self.previous = Path.cwd()
        os.chdir(self.caller)
        self.addCleanup(os.chdir, self.previous)

    def run_build(self, supplied):
        validations = []

        def runner(argv, **kwargs):
            if Path(argv[3]).name == 'validate_root.py' and '--eval-root' in argv:
                root = argv[argv.index('--eval-root') + 1]
                selected = argv[argv.index('--golden') + 1]
                self.assertTrue(Path(root).is_absolute(), 'eval root depends on child cwd')
                self.assertTrue(Path(selected).is_absolute(), 'eval set depends on child cwd')
                child = subprocess.run([sys.executable, '-I', '-B', '-c',
                    'import json,pathlib,sys; print(json.dumps([str(pathlib.Path(p).resolve()) for p in sys.argv[1:]]))',
                    root, selected], cwd=kwargs['cwd'], capture_output=True, text=True, check=True)
                validations.append(json.loads(child.stdout))
            return SimpleNamespace(returncode=0)

        with patch.object(builder, 'supplied_sets', supplied):
            result = builder.build(self.args, runner=runner, environ={})
        return result, validations

    def build_with(self, supplied, *, doc_keys=lambda path: {'synthetic-doc'}, **extra):
        for key, value in extra.items():
            setattr(self.args, key, value)
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0)

        with patch.object(builder, 'supplied_sets', supplied), patch.object(builder, 'doc_keys', doc_keys):
            result = builder.build(self.args, runner=runner, environ={})
        return result, [argv for argv in calls if Path(argv[3]).name == 'validate_root.py']

    def expected_validation(self, name='synthetic-set'):
        return [sys.executable, '-I', '-B', str(self.engine / 'scripts' / 'validate_root.py'),
                '--root', str(self.served), '--port', '18765', '--eval-root', str(self.eval_root.resolve()),
                '--golden', str((self.eval_root / 'sets' / name).resolve()),
                '--expect-generation', 'served-one', '--expect-fingerprint', 'synthetic-fingerprint',
                '--expect-opt-in', 'powershell,system', '--report-name', 'VALIDATION-' + name + '.json']

    def baseline(self, *names):
        root = self.root / 'served-zero'
        root.mkdir()
        for name in names:
            (root / ('VALIDATION-' + name + '.json')).write_text('{}', encoding='ascii')
        return root

    def supplied(self, root, manifest):
        return [self.selected.resolve()], []

    def test_relative_inputs_are_absolute_in_engine_child(self):
        def supplied(root, manifest):
            self.assertEqual(root, Path('operator-eval'))
            self.assertFalse(root.is_absolute(), 'preflight must inspect the supplied path first')
            return [Path('operator-eval/sets/synthetic-set')], []

        result, validations = self.run_build(supplied)
        self.assertEqual(validations, [[str(self.eval_root.resolve()), str(self.selected.resolve())]])
        self.assertEqual(result['quality_validation']['status'], 'passed')

    def test_absolute_inputs_stay_absolute(self):
        self.args.eval_root = self.eval_root.resolve()
        result, validations = self.run_build(lambda root, manifest: ([self.selected.resolve()], []))
        self.assertEqual(validations, [[str(self.eval_root.resolve()), str(self.selected.resolve())]])
        self.assertEqual(result['eval_sets'], ['synthetic-set'])

    def test_invalid_input_stops_before_build_children(self):
        def reject(root, manifest):
            raise builder.EvalError('synthetic invalid input')

        calls = []
        with patch.object(builder, 'supplied_sets', reject):
            with self.assertRaises(builder.EvalError):
                builder.build(self.args, runner=lambda *args, **kwargs: calls.append(args), environ={})
        self.assertEqual(calls, [])

    def test_no_eval_still_runs_structural_validation(self):
        self.args.eval_root = None
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0)

        with patch.object(builder, 'supplied_sets', side_effect=AssertionError('unexpected preflight')):
            result = builder.build(self.args, runner=runner, environ={})
        self.assertTrue(any('--no-eval' in argv for argv in calls))
        self.assertFalse(any('--eval-root' in argv for argv in calls))
        self.assertEqual(result['quality_validation']['status'], 'skipped')

    def test_without_baseline_validation_is_unchanged(self):
        result, validations = self.build_with(self.supplied, eval_root=self.eval_root.resolve())
        self.assertEqual(validations, [self.expected_validation()])
        self.assertEqual(result['quality_validation'], {'status': 'passed', 'eval_sets': ['synthetic-set']})

    def test_baseline_report_adds_comparison_and_limits(self):
        result, validations = self.build_with(self.supplied, eval_root=self.eval_root.resolve(),
                                              baseline_root=self.baseline('synthetic-set'))
        report = (self.root / 'served-zero' / 'VALIDATION-synthetic-set.json').resolve()
        self.assertEqual(validations, [self.expected_validation() + [
            '--baseline', str(report), '--max-drop', 'hit@1=0', '--max-drop', 'hit@8=0',
            '--max-drop', 'delivery@8=0.005']])
        self.assertEqual(result['quality_validation']['baseline'], {
            'generation': 'served-zero', 'compared': ['synthetic-set'], 'not_compared': [],
            'max_drop': {'hit@1': 0.0, 'hit@8': 0.0, 'delivery@8': 0.005}})

    def test_set_without_baseline_report_is_not_compared(self):
        result, validations = self.build_with(self.supplied, eval_root=self.eval_root.resolve(),
                                              baseline_root=self.baseline())
        self.assertEqual(validations, [self.expected_validation()])
        self.assertEqual(result['quality_validation']['baseline']['compared'], [])
        self.assertEqual(result['quality_validation']['baseline']['not_compared'], ['synthetic-set'])

    def test_baseline_needs_eval_root(self):
        self.args.eval_root = None
        self.args.baseline_root = self.baseline()
        calls = []
        with patch.object(builder, 'supplied_sets', side_effect=AssertionError('unexpected preflight')):
            with self.assertRaises(builder.BuilderError):
                builder.build(self.args, runner=lambda argv, **kwargs: calls.append(argv), environ={})
        self.assertEqual(calls, [])

    def test_invalid_baseline_root_stops_before_children(self):
        self.args.eval_root = self.eval_root.resolve()
        other = self.root / 'other-parent'
        other.mkdir()
        same_name = other / 'served-one'
        same_name.mkdir()
        bad_name = self.root / 'Bad Name'
        bad_name.mkdir()
        for root in (same_name, bad_name, self.root / 'missing-served'):
            self.args.baseline_root = root
            calls = []
            with self.subTest(root=root.name), patch.object(builder, 'supplied_sets', self.supplied):
                with self.assertRaises(builder.BuilderError):
                    builder.build(self.args, runner=lambda argv, **kwargs: calls.append(argv), environ={})
                self.assertEqual(calls, [])

    def test_probe_set_is_not_compared(self):
        probe = self.eval_root / 'sets' / 'golden_x_probe'
        probe.mkdir()
        result, validations = self.build_with(lambda root, manifest: ([probe.resolve()], []),
                                              doc_keys=lambda path: set(), eval_root=self.eval_root.resolve(),
                                              baseline_root=self.baseline('golden_x_probe'))
        self.assertEqual(validations, [self.expected_validation('golden_x_probe') +
                                       ['--min', 'hit@1=off', '--min', 'hit@8=off']])
        self.assertEqual(result['quality_validation']['baseline']['compared'], [])
        self.assertEqual(result['quality_validation']['baseline']['not_compared'], ['golden_x_probe'])

    def test_parser_accepts_baseline_root_for_build_only(self):
        build = ['build', '--served-generation', 'served-one', '--expect-fingerprint', 'synthetic-fingerprint',
                 '--corpus-repo', 'synthetic-corpus', '--corpus-ref', 'synthetic-ref', '--build-root', 'build-one',
                 '--served-root', 'served-one', '--generation', 'build-one', '--kept-manifest', 'kept.json',
                 '--export-dir', 'export', '--recipient', 'synthetic-recipient']
        option = ['--baseline-root', '/var/lib/deep32-forge/rag-data/old-served']
        self.assertIsNone(builder.parser().parse_args(build).baseline_root)
        self.assertEqual(builder.parser().parse_args(build + option).baseline_root,
                         Path('/var/lib/deep32-forge/rag-data/old-served'))
        bundle = ['bundle', '--served-generation', 'served-one', '--expect-fingerprint', 'synthetic-fingerprint',
                  '--export-input', 'export.age', '--unpack-dir', 'unpack', '--bundle-dir', 'bundles',
                  '--compat', 'synthetic-compat', '--created', 'synthetic-created', '--signer-id', 'synthetic',
                  '--block', 'block.json', '--signer-type', 'external', '--sign-cmd', '["sign"]',
                  '--verify-cmd', '["verify"]']
        self.assertEqual(builder.parser().parse_args(bundle).verb, 'bundle')
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            builder.parser().parse_args(bundle + option)


if __name__ == '__main__':
    unittest.main()
