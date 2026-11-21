"""Literal --relay forwarding through both shipped wrappers, offline."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'adapters/relay'))
from deep32_relay import connector


class RelayCLI(unittest.TestCase):
    def test_python_accepts_flag_and_legacy_syntax(self):
        spec = importlib.util.spec_from_file_location('relay_connector_r15', ROOT / 'adapters/connect.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for prefix in ('--relay', 'relay'):
            with patch.object(connector, 'main', return_value=0) as called:
                self.assertEqual(module.main([prefix, 'status', '--agent', 'claude']), 0)
                called.assert_called_once_with(['status', '--agent', 'claude'])

    def test_wrapper_flags_are_literal(self):
        scratch = Path(os.environ['DEEP32_RELAY_TEST_ROOT'])
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            folder = Path(temporary)
            output = folder / 'record.json'
            recorder = folder / 'recorder.py'
            recorder.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]),encoding="utf-8")\n', encoding='ascii')
            # The profile folders let Windows PowerShell 5.1 use its module-analysis cache; without them a cold host
            # (GitHub's runner) can rescan modules at start-up and pass a short timeout. Forwarding is what is tested.
            env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP',
                                                    'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'ProgramData')
                   if key in os.environ}
            args = ['--relay', 'selftest', '--agent', 'claude', '--relay-id', 'a' * 32, '--live']
            bash = Path(os.environ.get('D32_GIT_BASH', 'C:/Program Files/Git/bin/bash.exe'))
            if not bash.is_file():
                self.skipTest('explicit Git Bash unavailable')
            import shlex
            fake = folder / 'fake-python.sh'
            fake.write_text('#!/bin/sh\nexec ' + shlex.quote(Path(sys.executable).as_posix()) + ' -I -B ' + shlex.quote(recorder.as_posix()) + ' ' + shlex.quote(output.as_posix()) + ' "$@"\n', encoding='utf-8')
            fake.chmod(0o700)
            env['DEEP32_CONNECT_PYTHON'] = fake.as_posix()
            completed = subprocess.run([str(bash), '--noprofile', '--norc', str(ROOT / 'adapters/connect.sh'), *args], env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            self.assertEqual(completed.returncode, 0, 'shell wrapper failed')
            self.assertEqual(json.loads(output.read_text())[3:], args)
            if os.name == 'nt':
                ps = folder / 'fake-python.ps1'
                def quote(value): return "'" + str(value).replace("'", "''") + "'"
                ps.write_text('& ' + quote(sys.executable) + ' -I -B ' + quote(recorder) + ' ' + quote(output) + ' @args\nexit $LASTEXITCODE\n', encoding='utf-8')
                env['DEEP32_CONNECT_PYTHON'] = str(ps)
                output.unlink()
                powershell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
                completed = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                    '-File', str(ROOT / 'adapters/connect.ps1'), '-Relay', 'selftest', '-Agent', 'claude',
                    '-RelayId', 'a' * 32, '-Live'], env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
                self.assertEqual(completed.returncode, 0, 'PowerShell wrapper failed')
                self.assertTrue(output.exists(), (completed.stdout + completed.stderr).decode('utf-8', errors='replace'))
                forwarded = json.loads(output.read_text())[3:]
                self.assertEqual(forwarded[:2], ['--relay', 'selftest'])
                self.assertCountEqual(forwarded[2:], ['--relay-id', 'a' * 32, '--agent', 'claude', '--live'])

    def test_live_flag_refused_on_status(self):
        self.assertEqual(connector.main(['status', '--relay-id', 'a' * 32, '--live']), 10)
