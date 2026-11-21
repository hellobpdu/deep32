"""Literal relay-login wrapper forwarding through generated recorders only."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class LoginWrappers(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ['DEEP32_RELAY_TEST_ROOT']).resolve(strict=True)
        if scratch == ROOT or ROOT in scratch.parents:
            raise AssertionError('fixtures must remain outside the clone')
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-login-wrapper-', dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.output = self.folder / 'synthetic-record.json'
        self.recorder = self.folder / 'synthetic-recorder.py'
        self.recorder.write_text(
            'import json,sys\nfrom pathlib import Path\n'
            'value={"argv":sys.argv[2:],"cwd":str(Path.cwd())}\n'
            'Path(sys.argv[1]).write_text(json.dumps(value),encoding="utf-8")\n', encoding='ascii')
        # These are public Windows runtime selectors, never native agent homes
        # or inherited credentials. All mutable native locations are fixtures.
        self.env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC')
                    if key in os.environ}
        self.env.update({key: str(self.folder) for key in ('HOME', 'USERPROFILE', 'TEMP',
            'TMP', 'TMPDIR', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME')})
        self.env['PATHEXT'] = '.COM;.EXE;.BAT;.CMD;.PS1'
        system = Path(self.env.get('SystemRoot', 'C:/Windows')) / 'System32'
        self.env['PATH'] = str(system)

    def record(self):
        value = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(Path(value['cwd']).resolve(strict=True), self.folder)
        self.assertEqual(value['argv'][:2], ['-I', '-B'])
        self.assertEqual(Path(value['argv'][2]).resolve(strict=True), ROOT / 'adapters/connect.py')
        return value['argv'][3:]

    def test_shell_forwards_literal_login_and_claude_selection(self):
        bash = Path(os.environ.get('D32_GIT_BASH', 'C:/Program Files/Git/bin/bash.exe'))
        if not bash.is_absolute() or not bash.is_file():
            self.skipTest('explicit Git Bash unavailable')
        self.env['PATH'] = os.pathsep.join((str(bash.parent.parent / 'usr/bin'),
            str(bash.parent), self.env['PATH']))
        fake = self.folder / 'synthetic-python.sh'
        fake.write_text('#!/bin/sh\nexec ' + shlex.quote(Path(sys.executable).as_posix()) +
            ' -I -B ' + shlex.quote(self.recorder.as_posix()) + ' ' +
            shlex.quote(self.output.as_posix()) + ' "$@"\n', encoding='utf-8')
        fake.chmod(0o700)
        self.env['DEEP32_CONNECT_PYTHON'] = fake.as_posix()
        completed = subprocess.run([str(bash), '--noprofile', '--norc',
            str(ROOT / 'adapters/connect.sh'), '--relay', 'login', '--agent', 'claude'],
            cwd=self.folder, env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(completed.returncode, 0, 'shell login wrapper failed')
        self.assertEqual(self.record(), ['--relay', 'login', '--agent', 'claude'])

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell wrapper')
    def test_powershell_forwards_literal_login_and_codex_selection(self):
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        fake = self.folder / 'synthetic-python.ps1'
        fake.write_text('& ' + quote(sys.executable) + ' -I -B ' + quote(self.recorder) +
            ' ' + quote(self.output) + ' @args\nexit $LASTEXITCODE\n', encoding='utf-8')
        self.env['DEEP32_CONNECT_PYTHON'] = str(fake)
        powershell = Path(self.env['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        completed = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'adapters/connect.ps1'),
            '-Relay', 'login', '-Agent', 'codex'], cwd=self.folder, env=self.env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(completed.returncode, 0, 'PowerShell login wrapper failed')
        self.assertEqual(self.record(), ['--relay', 'login', '--agent', 'codex'])
