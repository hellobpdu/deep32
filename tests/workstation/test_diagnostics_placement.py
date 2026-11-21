"""Offline runtime-map and ksh-launcher contract checks; no host probes."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / 'src/workstation/diagnostics-map.json'
LAUNCHER = ROOT / 'src/workstation/distrib/site/usr/local/bin/deep32'
BASH = Path('C:/Program Files/Git/bin/bash.exe')


def posix(path):
    value = Path(path).resolve().as_posix()
    if len(value) > 2 and value[1] == ':':
        return '/' + value[0].lower() + value[2:]
    return value


class Placement(unittest.TestCase):
    def test_exact_mapping_and_required_members(self):
        contract = json.loads(MAP.read_text(encoding='utf-8'))
        self.assertEqual(contract['python'], '/usr/local/bin/python3.14')
        self.assertEqual(contract['python_flags'], ['-I', '-B'])
        self.assertEqual(contract['installed_root'], '/usr/local/d32/diagnostics')
        self.assertEqual(contract['launcher_installed'], '/usr/local/bin/deep32')
        self.assertEqual(contract['source_rules'], [
            {'source': 'src/diagnostics/deep32_doctor.py',
             'tree': 'diagnostics/deep32_doctor.py', 'kind': 'file'},
            {'source': 'src/diagnostics/deep32_diag',
             'tree': 'diagnostics/deep32_diag', 'kind': 'python-package'},
            {'source': 'src/diagnostics/schemas',
             'tree': 'diagnostics/schemas', 'kind': 'json-schemas'}])
        for member in contract['required_members'] + contract['selftest_required_members']:
            self.assertFalse(Path(member).is_absolute())
            self.assertNotIn('..', Path(member).parts)
            self.assertTrue(member.endswith(('.py', '.json')))
        self.assertIn('deep32_diag/cli.py', contract['required_members'])
        self.assertIn('schemas/doctor-output.schema.json', contract['required_members'])
        self.assertIn('deep32_diag/selftest_cli.py', contract['selftest_required_members'])
        self.assertEqual((contract['owner'], contract['group']), ('root', 'wheel'))
        self.assertEqual((contract['directory_mode'], contract['file_mode'],
                          contract['launcher_mode']), ('0755', '0444', '0555'))

    def test_fixed_runtime_and_no_environment_binding(self):
        text = LAUNCHER.read_text(encoding='utf-8')
        self.assertTrue(text.startswith('#!/bin/ksh\n'))
        self.assertIn('python=/usr/local/bin/python3.14\n', text)
        self.assertIn('entrypoint=/usr/local/d32/diagnostics/deep32_doctor.py\n', text)
        self.assertNotIn('PYTHONPATH', text)
        self.assertNotIn('DEEP32_DIAGNOSTIC_PYTHON', text)
        self.assertNotIn('venv', text)
        self.assertEqual(text.count('exec "$python" -I -B "$entrypoint"'), 2)
        self.assertIn('# fixed startup error; do not disclose installation paths.', text)


@unittest.skipUnless(BASH.is_file(), 'Full-path Git Bash is required on Windows.')
class Launcher(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-diag-', dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.entrypoint = self.root / 'installed diagnostics' / 'deep32_doctor.py'
        self.entrypoint.parent.mkdir()
        self.entrypoint.write_text('# synthetic sealed entrypoint\n', encoding='utf-8')
        self.python = self.root / 'sealed python'
        # This synthetic interpreter only records arguments and a fixed exit;
        # it does not run diagnostics or inspect an installed machine.
        self.python.write_text('#!/bin/sh\nprintf "%s\\0" "$@"\nexit 7\n', encoding='utf-8')
        self.script = self.root / 'deep32'
        text = LAUNCHER.read_text(encoding='utf-8')
        text = text.replace('python=/usr/local/bin/python3.14',
                            'python=' + shlex.quote(posix(self.python)), 1)
        text = text.replace('entrypoint=/usr/local/d32/diagnostics/deep32_doctor.py',
                            'entrypoint=' + shlex.quote(posix(self.entrypoint)), 1)
        self.script.write_text(text, encoding='utf-8', newline='\n')

    def run_launcher(self, args):
        # Git Bash parses the ksh-compatible script. Model only ksh's print
        # builtin for the fixed error branch; native ksh qualification is separate.
        harness = ('print() { [ "${1-}" != -r ] || shift; '
                   '[ "${1-}" != -- ] || shift; builtin printf "%s\\n" "$@"; }; '
                   'set -- ' + ' '.join(shlex.quote(value) for value in
                       [posix(self.script), *args]) + '; '
                   'script=$1; shift; . "$script"')
        env = dict(os.environ)
        env.update({'DEEP32_DIAGNOSTIC_PYTHON': '/synthetic/wrong-python',
                    'PYTHONPATH': '/synthetic/wrong-package',
                    'PYTHONHOME': '/synthetic/wrong-home'})
        return subprocess.run([str(BASH), '--noprofile', '--norc', '-c', harness],
                              env=env, capture_output=True, timeout=20)

    def arguments(self, result):
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stderr, b'')
        return result.stdout.rstrip(b'\0').decode('utf-8').split('\0')

    def test_doctor_role_is_bound_and_arguments_preserved(self):
        args = ['doctor', '--json', 'two words', '*', '; echo forbidden', '']
        result = self.run_launcher(args)
        # Split explicitly to retain the final empty argument.
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout.split(b'\0')[:-1], [part.encode() for part in
            ['-I', '-B', posix(self.entrypoint), 'doctor', '--role', 'workstation', *args[1:]]])

    def test_selftest_arguments_and_exit_unchanged(self):
        self.assertEqual(self.arguments(self.run_launcher(['selftest', '--json', '--details'])),
            ['-I', '-B', posix(self.entrypoint), 'selftest', '--json', '--details'])

    def test_missing_command_and_unknown_command_reach_dispatcher(self):
        self.assertEqual(self.arguments(self.run_launcher([])),
                         ['-I', '-B', posix(self.entrypoint)])
        self.assertEqual(self.arguments(self.run_launcher(['other', '--json'])),
                         ['-I', '-B', posix(self.entrypoint), 'other', '--json'])

    def test_explicit_role_cannot_replace_bound_workstation_role(self):
        self.assertEqual(self.arguments(self.run_launcher(['doctor', '--role', 'forge', '--json'])),
            ['-I', '-B', posix(self.entrypoint), 'doctor', '--role', 'workstation',
             '--role', 'forge', '--json'])

    def test_missing_runtime_has_fixed_bounded_failure(self):
        for missing in (self.python, self.entrypoint):
            with self.subTest(missing=missing.name):
                saved = missing.read_bytes()
                missing.unlink()
                try:
                    result = self.run_launcher(['doctor', '--json'])
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, b'')
                    self.assertEqual(result.stderr,
                                     b'The Python diagnostic runtime could not start.\n')
                finally:
                    missing.write_bytes(saved)

    def test_shell_parses(self):
        result = subprocess.run([str(BASH), '-n', str(LAUNCHER)], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
