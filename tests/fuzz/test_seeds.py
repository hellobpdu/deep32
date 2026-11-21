# SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
# SPDX-License-Identifier: Apache-2.0
"""Checks of the fuzz targets that run on any system, without clang: the committed seed corpora are exactly what
make_seeds.py writes, and every target has a harness and a job in .github/workflows/fuzz.yml.

    python3 -B -m unittest tests/fuzz/test_seeds.py

The fuzzing itself needs clang on Linux (tests/fuzz/README.md).
"""

import importlib.util
from pathlib import Path
import re
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load_make_seeds():
    spec = importlib.util.spec_from_file_location('d32_make_seeds', HERE / 'make_seeds.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Seeds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generators = load_make_seeds().GENERATORS

    def test_corpus_matches_make_seeds(self):
        for target, make in self.generators.items():
            with self.subTest(target=target):
                made = {f'seed-{name}': data for name, data in make().items()}
                folder = HERE / 'corpus' / target
                committed = {path.name: path.read_bytes() for path in folder.glob('seed-*')}
                self.assertEqual(sorted(committed), sorted(made), 'run python3 -I -B tests/fuzz/make_seeds.py')
                for name, data in made.items():
                    self.assertEqual(committed[name], data, f'{target}/{name} differs from what make_seeds.py writes')

    def test_every_target_has_a_harness(self):
        harnesses = sorted(path.stem for path in (HERE / 'targets').glob('*.c'))
        self.assertEqual(harnesses, sorted(self.generators))

    def test_every_target_runs_in_ci(self):
        workflow = (ROOT / '.github/workflows/fuzz.yml').read_text(encoding='utf-8')
        match = re.search(r'target: \[([^\]]*)\]', workflow)
        self.assertIsNotNone(match, 'no target matrix in fuzz.yml')
        listed = sorted(name.strip() for name in match.group(1).split(','))
        self.assertEqual(listed, sorted(self.generators))


if __name__ == '__main__':
    unittest.main()
