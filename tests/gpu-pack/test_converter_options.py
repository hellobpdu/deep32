"""Exercise the deployed shell option parser without Docker or system paths."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import unittest


KIT = Path(__file__).resolve().parents[2] / 'src' / 'gpu-pack'
SCRIPT = KIT / 'containers/convert-job.sh'
BOUNDARY = '# Converter option validation ends here; no state has changed.'
PAGE_CHECK = '      valid_page_range "$page_range" || exit 2\n'


def shell():
    if os.name == 'nt':
        path = Path('C:/Program Files/Git/bin/bash.exe')
        if not path.is_file():
            raise unittest.SkipTest('Git Bash is required for the deployed shell parser')
        return str(path)
    return '/bin/bash'


def run_options(args, source=None):
    source = SCRIPT.read_text(encoding='ascii') if source is None else source
    if source.count(BOUNDARY) != 1:
        raise AssertionError('converter option boundary missing or repeated')
    # Run the exact deployed parser, ending before its first access to the
    # deployed configuration, state directories, service manager or Docker.
    # Feed quoted arguments as shell source: Windows native argv normalises LF.
    code = 'set -- ' + ' '.join(shlex.quote(value) for value in args) + '\n'
    code += 'python3() { "$DEEP32_TEST_PYTHON" "$@"; }\n'
    code += source.partition(BOUNDARY)[0]
    code += '\nprintf "%s\\n" "$job_id" "$backend" "$tier" "$page_range"\n'
    code += 'printf "<%s>\\n" "${converter_options[@]}"\n'
    return subprocess.run([shell(), '--noprofile', '--norc', '-s'],
                          input=code.encode('utf-8'), capture_output=True, timeout=15,
                          env=dict(os.environ, DEEP32_TEST_PYTHON=sys.executable))


class ConverterOptionsTests(unittest.TestCase):
    def assert_invalid_pages(self, source=None):
        for value in ('', '-1', '1-', '-1-2', '1--2', '1,2', '1:2', '1 2',
                      '1-2-3', 'a', '1a', ' 1', '1 ', '1\n2', '1\n', '3-7\n',
                      '-3', '3-', '3 - 7', '+2', '3-7-9', '3,4', '$(id)', ';id'):
            with self.subTest(page_range=value):
                result = run_options(['neutral-job', 'mineru', '--page-range', value], source)
                self.assertEqual(result.returncode, 2, result.stderr.decode('ascii', errors='replace'))

    def test_valid_tiers_and_page_ranges(self):
        for tier in ('standard', 'advanced'):
            for value in ('1', '12', '1-2', '5-25'):
                with self.subTest(tier=tier, page_range=value):
                    result = run_options(['neutral-job', 'mineru', '--tier', tier, '--page-range', value])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.decode('ascii').splitlines(),
                                     ['neutral-job', 'mineru', tier, value,
                                      '<--tier>', '<' + tier + '>', '<--page-range>', '<' + value + '>'])

    def test_exact_arbiter_fullmatch_including_unicode_and_newlines(self):
        production = re.compile(r'^\d+(?:-\d+)?$')
        values = ('1', '12', '3-7', '0', '00', '7-3',
                  '\u0661', '\u0967-\u096f', '\uff11\uff12', '1-\u0663', '\U0001d7d9',
                  '', '-3', '3-', '3 - 7', '+2', '3-7-9', 'a', '3,4',
                  '1\n', '3-7\n', '1\r\n', '\n1', '\u00b2', '\u2161', '\u2460')
        for value in values:
            with self.subTest(page_range=ascii(value)):
                expected = 0 if production.fullmatch(value) else 2
                result = run_options(['neutral-job', 'mineru', '--page-range', value])
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected == 0:
                    self.assertIn(('<' + value + '>').encode('utf-8'), result.stdout)

    def test_default_tier_and_unrestricted_document_are_unchanged(self):
        result = run_options(['neutral-job', 'mineru'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode('ascii').splitlines(),
                         ['neutral-job', 'mineru', 'advanced', '', '<--tier>', '<advanced>'])

    def test_invalid_page_ranges(self):
        self.assert_invalid_pages()

    def test_invalid_tiers(self):
        for value in ('', 'STANDARD', 'fast', 'advanced ', '--page-range'):
            with self.subTest(tier=value):
                self.assertEqual(run_options(['neutral-job', 'mineru', '--tier', value]).returncode, 2)

    def test_missing_values_and_unknown_options(self):
        for options in (['--tier'], ['--page-range'], ['--unknown'], ['extra'], ['--tier=standard']):
            with self.subTest(options=options):
                self.assertEqual(run_options(['neutral-job', 'mineru', *options]).returncode, 2)

    def test_non_mineru_backends_keep_existing_arguments(self):
        for backend in ('pymupdf', 'production'):
            result = run_options(['neutral-job', backend])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.decode('ascii').splitlines(),
                             ['neutral-job', backend, 'advanced', '', '<>'])
            for option in (['--tier', 'standard'], ['--page-range', '1-2']):
                self.assertEqual(run_options(['neutral-job', backend, *option]).returncode, 2)

    def test_invalid_jobs_and_backends(self):
        for args in ([], ['neutral-job'], ['../escape', 'mineru'], ['neutral-job', 'other'],
                     ['A', 'mineru'], ['a' * 41, 'mineru']):
            with self.subTest(args=args):
                self.assertEqual(run_options(args).returncode, 2)

    def test_dry_run_is_retained_and_still_validates_options(self):
        for args in (['--dry-run'], ['neutral-job', 'mineru', '--dry-run'],
                     ['neutral-job', 'mineru', '--tier', 'standard', '--page-range', '1-2', '--dry-run']):
            result = run_options(args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'Downloads: none.', result.stdout)
        self.assertEqual(run_options(['neutral-job', 'mineru', '--page-range', 'invalid', '--dry-run']).returncode, 2)

    def test_converter_command_passes_the_validated_array(self):
        source = SCRIPT.read_text(encoding='ascii')
        self.assertIn('--output "/jobs/$job_id/output" "${extra[@]}" "${converter_options[@]}"', source)
        self.assertLess(source.index(BOUNDARY), source.index('mkdir -p /run/deep32-forge'))

    def test_removing_the_page_range_check_is_detected(self):
        source = SCRIPT.read_text(encoding='ascii')
        self.assertEqual(source.count(PAGE_CHECK), 1)
        mutated = source.replace(PAGE_CHECK, '', 1)
        # The same rejection assertion fails against the single-line mutation.
        # No altered production file is written.
        with self.assertRaises(AssertionError):
            result = run_options(['neutral-job', 'mineru', '--page-range', 'invalid'], mutated)
            self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
