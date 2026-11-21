"""Offline setup matrices. Every home is disposable beneath the caller's TEMP."""
import importlib.util
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'adapters/setup_skills.py'
spec = importlib.util.spec_from_file_location('setup_skills', HELPER)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
BASH = Path('C:/Program Files/Git/bin/bash.exe')
POWERSHELL = Path('C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
URL = 'https://synthetic-gateway.example/mcp'


def snapshot(home):
    return {p.relative_to(home).as_posix(): p.read_bytes() for p in home.rglob('*') if p.is_file()}


class Setup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic adapter homes ', dir=os.environ.get('TEMP'))
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_setup(self, agent, home, shell='python', url=URL):
        kit = ROOT / 'adapters' / ('codex' if agent == 'codex' else 'claude-code')
        if shell == 'bash':
            if not BASH.is_file():
                self.skipTest('Explicit Git Bash is unavailable.')
            cmd = [str(BASH), str(kit / 'setup.sh'), '--' + ('codex-home' if agent == 'codex' else 'claude-home'), str(home)]
            if url:
                cmd += ['--workstation-url', url]
        elif shell == 'powershell':
            if not POWERSHELL.is_file():
                self.skipTest('Windows PowerShell 5.1 is unavailable.')
            cmd = [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(kit / 'setup.ps1'),
                   '-CodexHome' if agent == 'codex' else '-ClaudeHome', str(home)]
            if url:
                cmd += ['-WorkstationUrl', url]
        else:
            cmd = [sys.executable, '-I', '-B', str(HELPER), '--agent', agent, '--home', str(home)]
            if url:
                cmd += ['--workstation-url', url]
        env = dict(os.environ, DEEP32_PYTHON=sys.executable)
        return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=45)

    def test_shell_matrices_preserve_and_repeat(self):
        for shell in ('bash', 'powershell'):
            for agent in ('codex', 'claude'):
                with self.subTest(shell=shell, agent=agent):
                    home = self.base / (shell + ' ' + agent)
                    home.mkdir()
                    guidance = home / ('AGENTS.md' if agent == 'codex' else 'CLAUDE.md')
                    guidance.write_bytes(b'User instructions stay exactly.\r\n')
                    (home / 'skills/unrelated').mkdir(parents=True)
                    (home / 'skills/unrelated/SKILL.md').write_bytes(b'user skill')
                    if agent == 'codex':
                        config = home / 'config.toml'
                        config.write_bytes(b'model = "synthetic-model"\r\n[mcp_servers.other]\r\nurl = "https://unrelated.example/mcp"\r\n')
                    else:
                        config = home / 'deep32.mcp.json'
                        config.write_text(json.dumps({'mcpServers': {'other': {'command': 'synthetic-command'}}, 'user': True}), encoding='utf-8')
                    original = snapshot(home)
                    result = self.run_setup(agent, home, shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('Service, authentication and credential-mode compatibility have not been checked.', result.stdout)
                    self.assertTrue(guidance.read_bytes().startswith(original[guidance.name]))
                    self.assertEqual((home / 'skills/unrelated/SKILL.md').read_bytes(), b'user skill')
                    names = sorted(p.name for p in (home / 'skills').iterdir() if p.name != 'unrelated')
                    self.assertEqual(len(names), 6 if agent == 'codex' else 1)
                    for path in (home / 'skills').rglob('*.md'):
                        self.assertNotIn('${CODEX_HOME}', path.read_text(encoding='utf-8'))
                    if agent == 'codex':
                        self.assertTrue(config.read_bytes().startswith(original[config.name]))
                    else:
                        self.assertEqual(json.loads(config.read_bytes())['mcpServers']['other'], {'command': 'synthetic-command'})
                        self.assertIn('${DEEP32_MCP_TOKEN}', config.read_text(encoding='utf-8'))
                    self.assertTrue((home / '.deep32-backups').is_dir())
                    before = snapshot(home)
                    result = self.run_setup(agent, home, shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('0 changed files', result.stdout)
                    self.assertEqual(snapshot(home), before)
                    # An external config entry changed after setup must refuse before any skill update.
                    if agent == 'codex':
                        config.write_text(config.read_text(encoding='utf-8').replace(URL, 'https://different.example/mcp'), encoding='utf-8')
                    else:
                        body = json.loads(config.read_bytes())
                        body['mcpServers']['deep32']['url'] = 'https://different.example/mcp'
                        config.write_text(json.dumps(body), encoding='utf-8')
                    before = snapshot(home)
                    result = self.run_setup(agent, home, shell)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(snapshot(home), before)

    def test_shell_default_skills_only(self):
        for shell in ('bash', 'powershell'):
            for agent in ('codex', 'claude'):
                with self.subTest(shell=shell, agent=agent):
                    home = self.base / (shell + ' no config ' + agent)
                    result = self.run_setup(agent, home, shell, url='')
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse((home / ('config.toml' if agent == 'codex' else 'deep32.mcp.json')).exists())

    def test_untracked_local_edit_and_broken_marker_write_nothing(self):
        for agent in ('codex', 'claude'):
            home = self.base / agent
            result = self.run_setup(agent, home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            skill = home / 'skills/deep32/SKILL.md'
            original = skill.read_bytes()
            skill.write_bytes(original + b'local edit\n')
            before = snapshot(home)
            self.assertEqual(self.run_setup(agent, home).returncode, 1)
            self.assertEqual(snapshot(home), before)
            skill.write_bytes(original)
            guidance = home / ('AGENTS.md' if agent == 'codex' else 'CLAUDE.md')
            guidance.write_bytes(guidance.read_bytes().replace(b'<!-- deep32:end -->', b'broken end'))
            before = snapshot(home)
            self.assertEqual(self.run_setup(agent, home).returncode, 1)
            self.assertEqual(snapshot(home), before)

    def test_update_uses_hashes_and_backups(self):
        home = self.base / 'owned update'
        self.assertEqual(self.run_setup('codex', home).returncode, 0)
        path = home / 'skills/deep32/SKILL.md'
        previous_bytes = b'synthetic previous managed revision\n'
        path.write_bytes(previous_bytes)
        state_path = home / '.deep32-setup.json'
        state = json.loads(state_path.read_bytes())
        state['files']['skills/deep32/SKILL.md'] = setup.digest(previous_bytes)
        state_path.write_text(json.dumps(state), encoding='utf-8')
        result = self.run_setup('codex', home)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(path.read_bytes(), previous_bytes)
        backups = list((home / '.deep32-backups').glob('*/skills/deep32/SKILL.md'))
        self.assertEqual([p.read_bytes() for p in backups], [previous_bytes])

    def test_invalid_inputs_and_extra_files(self):
        for url in ('http://synthetic.example/mcp', 'https://synthetic.example/mcp?token=synthetic',
                    'https://synthetic.example:65536/mcp', 'https://user@synthetic.example/mcp'):
            home = self.base / 'must not exist'
            self.assertEqual(self.run_setup('codex', home, url=url).returncode, 2)
            self.assertFalse(home.exists())
        home = self.base / 'extra local file'
        (home / 'skills/deep32').mkdir(parents=True)
        (home / 'skills/deep32/local.txt').write_bytes(b'local')
        before = snapshot(home)
        self.assertEqual(self.run_setup('codex', home).returncode, 1)
        self.assertEqual(snapshot(home), before)

    def test_reparse_is_refused_and_rollback_preserves_original(self):
        home = self.base / 'rollback'
        home.mkdir()
        a = home / 'a.txt'
        a.write_bytes(b'original')
        actual_write = setup.atomic_write
        def fail_once(path, data):
            if path.name == 'z.txt':
                raise OSError('synthetic failure')
            return actual_write(path, data)
        with mock.patch.object(setup, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                setup.install(home, {'a.txt': (b'original', b'replacement'), 'z.txt': (None, b'new')})
        self.assertEqual(a.read_bytes(), b'original')
        self.assertFalse((home / 'z.txt').exists())
        fake = mock.Mock(st_mode=0, st_file_attributes=0x400)
        with mock.patch.object(Path, 'lstat', return_value=fake):
            with self.assertRaises(setup.Refusal):
                setup.safe_path(home)

    def test_launch_command_arguments_are_literal(self):
        home = self.base / "literal $(synthetic) ` $ ' home"
        result = self.run_setup('claude', home)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = home / 'deep32.mcp.json'
        for shell in ('posix', 'powershell'):
            command = setup.claude_launch_command(config, shell)
            if shell == 'posix':
                # Parse only. In particular, never invoke the printed command.
                self.assertEqual(shlex.split(command), ['claude', '--mcp-config', str(config)])
                # shlex.split alone also accepts expandable double quotes. Check
                # a literal-only grammar: single-quoted segments, with a fixed
                # double-quoted apostrophe between segments when needed.
                argument = command.removeprefix('claude --mcp-config ')
                literal, index = [], 0
                while index < len(argument):
                    if argument.startswith('"\'"', index):
                        literal.append("'")
                        index += 3
                    else:
                        self.assertEqual(argument[index], "'", 'Argument permits shell expansion')
                        end = argument.index("'", index + 1)
                        literal.append(argument[index + 1:end])
                        index = end + 1
                self.assertEqual(''.join(literal), str(config))
            else:
                if not POWERSHELL.is_file():
                    continue
                # ParseInput builds an AST without evaluating the input. Require
                # exactly one command and a literal single-quoted path argument.
                parser = r'''
$text = [Console]::In.ReadToEnd()
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)
if ($errors.Count) { exit 1 }
$commands = @($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst]}, $true))
if ($commands.Count -ne 1) { exit 2 }
$elements = $commands[0].CommandElements
if ($elements.Count -ne 3) { exit 3 }
$argument = $elements[2]
if ($argument -isnot [System.Management.Automation.Language.StringConstantExpressionAst] -or $argument.StringConstantType -ne 'SingleQuoted') { exit 4 }
[Console]::Out.Write($argument.Value)
'''
                parsed = subprocess.run([str(POWERSHELL), '-NoProfile', '-Command', parser],
                                        input=command, capture_output=True, text=True, timeout=20)
                self.assertEqual(parsed.returncode, 0, parsed.stderr)
                self.assertEqual(parsed.stdout, str(config))
        platform_shell = 'powershell' if os.name == 'nt' else 'posix'
        self.assertIn(setup.claude_launch_command(config, platform_shell), result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
