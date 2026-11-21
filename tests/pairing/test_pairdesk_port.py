"""Offline CLI plumbing only: existing pairing dialogue, mocked local helpers."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'src/workstation/distrib/site/usr/local/d32/sbin/d32-pairdesk'
BASH = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else Path('/bin/bash')
SCRATCH = None


def shell_path(path):
    path = Path(path).resolve()
    if os.name == 'nt':
        return '/' + path.drive[0].lower() + '/' + path.as_posix().split(':/', 1)[1]
    return str(path)


class PairdeskPortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-pairdesk-port-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lib = self.root / 'usr/local/d32/libexec'
        self.lib.mkdir(parents=True)
        self.audit = self.root / 'synthetic-audit.txt'
        self.join = self.root / 'synthetic-join.json'
        (self.lib / 'd32-ui-lib.sh').write_text('''d32ui_fmt() { D32UI_T=$1; }
d32ui_text() { D32UI_T=$1; }
d32ui_say() { :; }
d32ui_code() { D32UI_CODE=ABCDEABCDE; return 0; }
''', encoding='ascii', newline='\n')
        self.executable('d32-uijson', 'cat\n')
        self.executable('d32-forge-enrol', 'exit 0\n')
        self.executable('clear', 'exit 0\n')
        self.executable('synthetic-pair', '''printf '%s\n' "$*" >> "$SYNTHETIC_PAIR_AUDIT"
case $1 in
identity) printf 'ok|yes\n' ;;
discover)
    shift
    port=7453
    while [ $# -gt 0 ]; do
        if [ "$1" = --port ]; then shift; port=$1; fi
        shift
    done
    printf 'status|ok\ncand|synthetic-forge|yes|tailnet|100.64.0.9|%s|synthetic-forge.example.ts.net\n' "$port"
    ;;
join) cat > "$SYNTHETIC_PAIR_JOIN"; printf 'ok|yes\npeer_id|forge-synthetic\n' ;;
*) exit 91 ;;
esac
''')

    def executable(self, name, body):
        path = self.lib / name
        path.write_text('#!/bin/sh\n' + body, encoding='ascii', newline='\n')
        path.chmod(0o755)

    def run_pairdesk(self, args, answers='y\nsynthetic code\n\n'):
        env = dict(os.environ, D32_ROOT=shell_path(self.root), D32_PATH=shell_path(self.lib),
                   D32_PAIR_BIN=shell_path(self.lib / 'synthetic-pair'),
                   SYNTHETIC_PAIR_AUDIT=shell_path(self.audit), SYNTHETIC_PAIR_JOIN=shell_path(self.join),
                   TMPDIR=shell_path(SCRATCH))
        return subprocess.run([str(BASH), '--noprofile', '--norc', shell_path(SCRIPT), *args],
                              env=env, input=answers, capture_output=True, text=True, timeout=10, check=False)

    def events(self):
        return self.audit.read_text(encoding='ascii').splitlines() if self.audit.exists() else []

    def test_explicit_forge_port_reaches_discovery_and_selected_join(self):
        result = self.run_pairdesk(['-forge', '--port', '17453'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('discover --want forge --transport tailnet --port 17453', self.events())
        self.assertEqual(json.loads(self.join.read_text())['port'], 17453)

    def test_default_forge_discovery_is_unchanged_and_dialogue_matches_override(self):
        default = self.run_pairdesk(['-forge'])
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertIn('discover --want forge --transport tailnet', self.events())
        self.assertFalse(any('--port' in event for event in self.events()))
        self.assertEqual(json.loads(self.join.read_text())['port'], 7453)
        explicit = self.run_pairdesk(['-forge', '--port', '17453'])
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual((default.stdout, default.stderr), (explicit.stdout, explicit.stderr))

    def test_explicit_port_bounds(self):
        for port in ('1', '65535'):
            with self.subTest(port=port):
                result = self.run_pairdesk(['-forge', '--port', port])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(self.join.read_text())['port'], int(port))

    def test_invalid_ports_refuse_before_pairing(self):
        for port in ('0', '00', '01', 'true', 'false', 'True', 'False', 'yes', '1.0', '+1', '-1',
                     '65536', '12345678901234567890', '1 2', '', '12x'):
            with self.subTest(port=port):
                result = self.run_pairdesk(['-forge', '--port', port])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual((result.stdout, result.stderr), ('', ''))
                self.assertEqual(self.events(), [])

    def test_other_modes_and_malformed_arguments_refuse_before_pairing(self):
        for args in (['-relay', '--port', '17453'], ['-headend', '--port', '17453'],
                     ['--port', '17453'], ['-forge', '--port'], ['-forge', '17453'],
                     ['-forge', '--port', '17453', 'extra'], ['-forge', '--port=17453']):
            with self.subTest(args=args):
                result = self.run_pairdesk(args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual((result.stdout, result.stderr), ('', ''))
                self.assertEqual(self.events(), [])

    def test_explicit_forge_port_also_reaches_loopback_join(self):
        config = self.root / 'etc/d32/node.conf'
        config.parent.mkdir(parents=True)
        config.write_text('transport=loopback\n', encoding='ascii')
        result = self.run_pairdesk(['-forge', '--port', '17453'], answers='synthetic code\n\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(event.startswith('discover ') for event in self.events()))
        self.assertEqual(json.loads(self.join.read_text())['port'], 17453)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tmp', type=Path, required=True)
    args, rest = parser.parse_known_args()
    SCRATCH = args.tmp.resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[__file__, *rest])
