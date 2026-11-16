"""Offline regressions for eval paths passed across different working directories."""
import importlib.util
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


if __name__ == '__main__':
    unittest.main()
