"""Offline C6 byte-boundary checks using synthetic status details only."""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = None
SCRATCH = None
SHELL = None


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shell_path(path):
    value = Path(path).resolve().as_posix()
    return '/' + value[0].lower() + value[2:] if value[1:2] == ':' else value


class Limits(unittest.TestCase):
    def modules(self):
        for index, relative in enumerate(('src/gpu-pack/firstboot/status.py',
                                         'src/gpu-pack/import/vendor/status.py')):
            yield relative, load(ROOT / relative, 'c6_limits_' + str(index))

    def state(self, **fields):
        return dict(boot='1234abcd', stage='firstboot', state='running', **fields)

    def test_forge_boundary_and_truncation(self):
        for relative, module in self.modules():
            with self.subTest(relative=relative):
                base = module.encode(1, 'forge', self.state()).removesuffix('\n')
                available = 510 - len(base) - len(' msg=')
                for delta in (-1, 0, 1, 1000):
                    line = module.encode(1, 'forge', self.state(msg='X' * (available + delta)))
                    self.assertLessEqual(len(line.encode('ascii')), 511)
                    self.assertLessEqual(len(line.replace('\n', '\r\n').encode('ascii')), 512)
                    self.assertEqual(line.endswith('[truncated]\n'), delta > 0)
                    if delta >= 0:
                        self.assertEqual(len(line.replace('\n', '\r\n')), 512)

    def test_forge_rejects_non_ascii_and_line_injection(self):
        for relative, module in self.modules():
            for detail in ('caf\N{LATIN SMALL LETTER E WITH ACUTE}', 'first\nsecond', 'first\rsecond', '\x1b'):
                with self.subTest(relative=relative, detail=repr(detail)), self.assertRaises(ValueError):
                    module.encode(1, 'forge', self.state(msg=detail))

    def test_forge_preserves_structured_fields(self):
        for relative, module in self.modules():
            with self.subTest(relative=relative):
                state = dict(boot='1234abcd', stage='pairing', state='waiting', pair='KLMNP23456',
                             pair_with='workstation', expires=300, msg='X' * 4096)
                line = module.encode(99, 'forge', state)
                for field in ('boot=1234abcd', 'stage=pairing', 'pair=KLMNP23456',
                              'pair_with=workstation', 'expires=300'):
                    self.assertIn(field, line)
                self.assertTrue(line.endswith('[truncated]\n'))

    def test_forge_structured_overflow_is_refused(self):
        for relative, module in self.modules():
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                module.encode(1, 'forge', self.state(version='v' * 1024))

    def test_forge_full_signin_fields_keep_the_short_truncation_marker(self):
        fields = dict(boot='1234abcd', stage='signin', state='waiting', version='v' * 64,
                      name='n' * 63, signin='https://login.tailscale.com/' + 'z' * 256)
        for relative, module in self.modules():
            for node in ('forge', 'workstation'):
                with self.subTest(relative=relative, node=node):
                    line = module.encode(999999999, node, dict(fields, msg='X' * 4096))
                    self.assertEqual(len(line.replace('\n', '\r\n').encode('ascii')), 512)
                    self.assertTrue(line.endswith('~\n'))
                    self.assertNotIn('[truncated]', line)
                    for key, value in fields.items():
                        self.assertIn(' ' + key + '=' + value, line)

    def test_forge_marker_fits_each_small_message_budget(self):
        for relative, module in self.modules():
            base = len(module.encode(1, 'forge', self.state()).rstrip('\n'))
            for available in range(1, len('[truncated]') + 1):
                with self.subTest(relative=relative, available=available):
                    version = 'v' * (510 - len(' msg=') - available - base - len(' version='))
                    line = module.encode(1, 'forge', self.state(version=version, msg='X' * 4096))
                    marker = '[truncated]' if available == len('[truncated]') else '~'
                    self.assertTrue(line.endswith(marker + '\n'))
                    self.assertEqual(len(line.replace('\n', '\r\n').encode('ascii')), 512)

    def test_forge_empty_message_and_no_message_structured_boundaries(self):
        for relative, module in self.modules():
            base = len(module.encode(1, 'forge', self.state()).rstrip('\n'))
            for body_size in (505, 506, 510, 511):
                version = 'v' * (body_size - base - len(' version='))
                state = self.state(version=version)
                with self.subTest(relative=relative, body_size=body_size):
                    if body_size <= 510:
                        line = module.encode(1, 'forge', state)
                        self.assertLessEqual(len(line.replace('\n', '\r\n').encode('ascii')), 512)
                    else:
                        with self.assertRaises(ValueError):
                            module.encode(1, 'forge', state)
                    if body_size == 505:
                        line = module.encode(1, 'forge', dict(state, msg=''))
                        self.assertEqual(len(line.replace('\n', '\r\n').encode('ascii')), 512)
                    else:
                        with self.assertRaises(ValueError):
                            module.encode(1, 'forge', dict(state, msg=''))
                    with self.assertRaises(ValueError):
                        module.encode(1, 'forge', dict(state, msg='X' * 4096))

    def test_forge_all_written_channels_fit_with_line_endings(self):
        for relative, module in self.modules():
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
                directory = Path(temp)
                console = directory / 'synthetic-console'
                console.touch()
                status = module.Status(directory / 'state/status', consoles=(str(console),))
                output = io.StringIO()
                with mock.patch.object(module.os, 'O_NOCTTY', 0, create=True), \
                        mock.patch.object(module.os, 'O_NONBLOCK', 0, create=True), redirect_stdout(output):
                    status.update('firstboot', 'running', msg='X' * 4096)
                for data in ((directory / 'state/status').read_bytes(),
                             console.read_bytes(), output.getvalue().encode('ascii')):
                    self.assertLessEqual(len(data), 512)
                    self.assertTrue(data.isascii())
                    self.assertIn(b'[truncated]', data)
                self.assertTrue(console.read_bytes().endswith(b'\r\n'))
                self.assertEqual(len(console.read_bytes()), 512)

    def shell(self, *fields, locale='C'):
        script = 'source "$1"; shift; c6_encode "$@"'
        env = dict(os.environ, LC_ALL=locale)
        return subprocess.run([SHELL, '-c', script, 'c6-limits',
                               shell_path(ROOT / 'src/workstation/distrib/site/usr/local/d32/libexec/d32-c6.sh'),
                               *fields], capture_output=True, env=env, timeout=15)

    def test_workstation_message_boundary(self):
        fields = ['999999999', 'workstation', 'signin', 'waiting', 'boot=1234abcd',
                  'version=' + 'v' * 64, 'name=' + 'n' * 63,
                  'signin=https://login.tailscale.com/' + 'z' * 200]
        base = self.shell(*fields)
        self.assertEqual(base.returncode, 0, base.stderr)
        available = 511 - len(base.stdout) - 5
        self.assertGreaterEqual(available, len('[truncated]'))
        self.assertLessEqual(available, 240)
        for delta in (-1, 0, 1, 1000):
            with self.subTest(delta=delta):
                result = self.shell(*fields, 'msg=' + 'X' * (available + delta))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLessEqual(len(result.stdout) + 1, 512)
                self.assertEqual(result.stdout.endswith(b'[truncated]'), delta > 0)
                if delta >= 0:
                    self.assertEqual(len(result.stdout) + 1, 512)

    def test_workstation_message_limit_is_truncated(self):
        result = self.shell('1', 'workstation', 'firstboot', 'running',
                            'boot=1234abcd', 'msg=' + 'X' * 4096)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.split(b' msg=')[1]), 240)
        self.assertTrue(result.stdout.endswith(b'[truncated]'))

    def test_workstation_full_signin_fields_keep_the_short_truncation_marker(self):
        fields = ['999999999', 'workstation', 'signin', 'waiting', 'boot=1234abcd',
                  'version=' + 'v' * 64, 'name=' + 'n' * 63,
                  'signin=https://login.tailscale.com/' + 'z' * 256]
        base = self.shell(*fields)
        self.assertEqual(base.returncode, 0, base.stderr)
        self.assertEqual(len(base.stdout) + 1, 504)
        empty = self.shell(*fields, 'msg=')
        self.assertNotEqual(empty.returncode, 0)
        self.assertEqual(empty.stdout, b'')
        for length in (1, 3, 4, 4096):
            with self.subTest(length=length):
                result = self.shell(*fields, 'msg=' + 'X' * length)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLessEqual(len(result.stdout) + 1, 512)
                self.assertEqual(result.stdout.endswith(b'~'), length > 3)
                for field in fields[4:]:
                    self.assertIn(b' ' + field.encode('ascii'), result.stdout)
                if length >= 3:
                    self.assertEqual(len(result.stdout) + 1, 512)

    def test_workstation_successful_replay_preserves_bounded_line_bytes(self):
        fields = ['999999999', 'workstation', 'signin', 'waiting', 'boot=1234abcd',
                  'version=' + 'v' * 64, 'name=' + 'n' * 63,
                  'signin=https://login.tailscale.com/' + 'z' * 256, 'msg=' + 'X' * 4096]
        encoded = self.shell(*fields)
        self.assertEqual(encoded.returncode, 0, encoded.stderr)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            status, serial = Path(temp) / 'status', Path(temp) / 'serial'
            status.write_bytes(encoded.stdout + b'\n')
            result = subprocess.run([SHELL, '-c',
                'source "$1"; C6_DIR=$2; C6_SERIAL_READY=1; exec 9>"$3"; c6_repeat', 'c6-limits',
                shell_path(ROOT / 'src/workstation/distrib/site/usr/local/d32/libexec/d32-c6.sh'),
                shell_path(temp), shell_path(serial)], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b'')
            self.assertEqual(serial.read_bytes(), encoded.stdout + b'\n')
            self.assertEqual(len(serial.read_bytes()), 512)
            self.assertTrue(serial.read_bytes().isascii())

    def test_workstation_ascii_is_locale_independent(self):
        for locale in ('C', 'C.UTF-8'):
            for detail in ('caf\N{LATIN SMALL LETTER E WITH ACUTE}', 'first\nsecond', '\x1b'):
                with self.subTest(locale=locale, detail=repr(detail)):
                    result = self.shell('1', 'workstation', 'firstboot', 'running',
                                        'boot=1234abcd', 'msg=' + detail, locale=locale)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, b'')

    def test_workstation_replay_refuses_invalid_current_line(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp:
            status = Path(temp) / 'status'
            prefix = b'D32S/1 1 workstation boot=1234abcd stage=firstboot state=running msg='
            for detail in (b'X' * 512, b'caf\xc3\xa9', b'first\x1bsecond'):
                status.write_bytes(prefix + detail + b'\n')
                result = subprocess.run([SHELL, '-c',
                    'source "$1"; C6_DIR=$2; c6_current', 'c6-limits',
                    shell_path(ROOT / 'src/workstation/distrib/site/usr/local/d32/libexec/d32-c6.sh'),
                    shell_path(temp)], capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 3)
                self.assertEqual(result.stdout, b'')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--scratch', required=True, type=Path)
    parser.add_argument('--shell', required=True)
    args, rest = parser.parse_known_args()
    ROOT, SCRATCH, SHELL = args.repo.resolve(), args.scratch.resolve(), args.shell
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *rest])
