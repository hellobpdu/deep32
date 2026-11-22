"""Offline tests for the setup wizard (install/deep32-wizard.sh and .ps1) and the opt-in event stream it reads
from install/deep32-setup.sh and .ps1. Standard library only.

Nothing here touches the network, Docker, Tailscale, Hyper-V or a real download: deep32-setup runs in a scratch copy
of the repository whose deep32-get, connect and machine tools (tailscale, nvidia-smi, docker, sudo) are stand-ins.
The wizards run only in --demo, --print-mode, --decode or library form. On Windows bash means Git for Windows'
bash by its full path, never a bare `bash` (that can start WSL).

    python3 -I -B tests/wizard/test_wizard.py           everything (the demo runs take a few minutes)
    DEEP32_WIZARD_QUICK=1 python3 -I -B tests/wizard/test_wizard.py     without the full demo runs
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import capture  # noqa: E402
import vtscreen  # noqa: E402

WINDOWS = os.name == 'nt'
BASH = Path(r'C:\Program Files\Git\bin\bash.exe') if WINDOWS else Path(shutil.which('bash') or '/bin/bash')
SYSROOT = Path(os.environ.get('SystemRoot', r'C:\Windows'))
POWERSHELL = SYSROOT / 'System32/WindowsPowerShell/v1.0/powershell.exe'
PWSH = shutil.which('pwsh')
BASE_DIR = ROOT / 'tests/wizard/fixtures/setup-base'   # the setup scripts as they were before the wizard's opt-in options (046f8f8)
QUICK = os.environ.get('DEEP32_WIZARD_QUICK') == '1'


def git_show(path):
    return (BASE_DIR / Path(path).name).read_bytes()   # a copy in the tree, so a clone without the old history tests too


def write(path, text, mode=0o755):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode('utf-8') if isinstance(text, str) else text)
    os.chmod(path, mode)


def posix(path):
    return str(path).replace('\\', '/')


def msys(path):
    """A path as Git for Windows' bash writes it in PATH (C:/x becomes /c/x)."""
    p = posix(path)
    return '/' + p[0].lower() + p[2:] if WINDOWS and re.match(r'[A-Za-z]:/', p) else p


# --- stand-ins for the machine and the deeper tools ------------------------------------------------------------------

SH_TAILSCALE = r'''#!/bin/sh
case "$1" in
  status)
    case "${FAKE_TS:-running}" in
      running) echo '{"BackendState":"Running","Self":{"DNSName":"'"${FAKE_TS_NAME:-deep32-forge}"'.tail1234.ts.net."},"CurrentTailnet":{"MagicDNSEnabled":true},"CertDomains":["tail1234.ts.net"]}' ;;
      nodns) echo '{"BackendState":"Running","Self":{"DNSName":"forge.tail1234.ts.net."},"CurrentTailnet":{"MagicDNSEnabled":false},"CertDomains":[]}' ;;
      *) echo '{"BackendState":"NeedsLogin"}' ;;
    esac ;;
  up) echo 'To authenticate, visit:' >&2; printf '\thttps://login.tailscale.com/a/test\n' >&2; exit 1 ;;
  *) exit 0 ;;
esac
'''
SH_NVIDIA = r'''#!/bin/sh
case "$*" in
  *compute_cap*) echo "${FAKE_CC:-8.9}" ;;
  *name*) echo 'NVIDIA GeForce RTX 4090' ;;
  *driver_version*) echo "${FAKE_DRIVER:-580.82.07}" ;;
  *memory.total*) echo 24564 ;;
esac
'''
SH_DOCKER = r'''#!/bin/sh
[ "${FAKE_DOCKER:-ok}" = down ] && exit 1
case "$*" in
  "compose version") echo 'Docker Compose version v2.29.1' ;;
  *Driver*) [ "${FAKE_DOCKER:-ok}" = containerd ] && echo 'overlayfs [["driver-type","io.containerd.snapshotter.v1"]]' || echo 'overlay2 [["Backing Filesystem","extfs"]]' ;;
  *Runtimes*) echo '{"nvidia":{"path":"nvidia-container-runtime"},"runc":{"path":"runc"}}' ;;
  info) echo ok ;;
esac
'''
SH_GET = r'''#!/bin/sh
echo "stub deep32-get $*"
echo 'Download refused: stand-in refusal' >&2
exit "${FAKE_GET_EXIT:-0}"
'''


def bash_sandbox(base, setup_bytes):
    """A scratch repository with install/ and stand-ins; returns (repo, bin)."""
    repo, bin_ = base / 'repo', base / 'bin'
    (repo / 'install').mkdir(parents=True)
    for name in ('matrix.json', 'deep32-wizard.sh', 'deep32-wizard-errors.tsv', 'deep32-wizard-demo.txt'):
        shutil.copy2(ROOT / 'install' / name, repo / 'install' / name)
    shutil.copy2(ROOT / 'VERSION', repo / 'VERSION')
    write(repo / 'install/deep32-setup.sh', setup_bytes)
    write(repo / 'install/forge-host-python.sh', '#!/bin/sh\nexit 0\n')
    write(repo / 'src/release-tools/deep32-get.sh', SH_GET)
    write(repo / 'adapters/connect.sh', '#!/bin/sh\necho "stub connect $*"\nexit "${FAKE_CONNECT_EXIT:-0}"\n')
    (base / 'data/status').mkdir(parents=True, exist_ok=True)
    (base / 'data/status/status').write_text('pair=ABCDEFGHJK\n', encoding='ascii')
    write(bin_ / 'tailscale', SH_TAILSCALE)
    write(bin_ / 'nvidia-smi', SH_NVIDIA)
    write(bin_ / 'docker', SH_DOCKER)
    write(bin_ / 'sudo', '#!/bin/sh\nwhile [ "${1#-}" != "$1" ]; do shift; done\n[ $# -gt 0 ] || exit 0\nexec "$@"\n')
    write(bin_ / 'gpgv', '#!/bin/sh\nexit 0\n')
    write(bin_ / 'curl', '#!/bin/sh\nprintf 401\n')
    write(bin_ / 'uname', '#!/bin/sh\n[ "$1" = -s ] && { echo Linux; exit 0; }\nexec /usr/bin/uname "$@"\n')
    write(bin_ / 'python3', '#!/bin/sh\nexec "%s" "$@"\n' % posix(sys.executable))
    return repo, bin_


BASH_CASES = [
    # (name, arguments, extra environment)
    ('help', ['--help'], {}),
    ('unknown argument', ['--bogus'], {}),
    ('check, all fine', ['check'], {}),
    ('check, signed out', ['check'], {'FAKE_TS': 'out'}),
    ('check, no MagicDNS and a bad name', ['check'], {'FAKE_TS': 'nodns'}),
    ('check, containerd store', ['check'], {'FAKE_DOCKER': 'containerd'}),
    ('check, Docker down, old driver', ['check'], {'FAKE_DOCKER': 'down', 'FAKE_DRIVER': '535.1'}),
    ('forge, installer stops', ['forge', '--data-root', '@DATA@'], {'FAKE_GET_EXIT': '1'}),
    ('forge, installed, pairing code', ['forge', '--data-root', '@DATA@'], {}),
    ('workstation, downloads', ['workstation'], {}),
    ('workstation, disc 1 stops', ['workstation'], {'FAKE_GET_EXIT': '1'}),
    ('headend, connect', ['headend', '--agent', 'claude', '--workstation', 'https://ws.tail1234.ts.net/mcp'], {}),
    ('headend, bad address', ['headend', '--agent', 'claude', '--workstation', 'http://nope'], {}),
    ('guessed role', [], {}),
]


def run_bash(repo, bin_, args, env_extra, script='install/deep32-setup.sh', stdin=b''):
    # Git for Windows' bash.exe puts its own folders first on PATH, so the stand-ins go first from inside it.
    env = {'SANDBOX_PATH': msys(bin_) + ':/usr/bin:/bin', 'HOME': posix(repo.parent), 'TERM': 'dumb', 'LANG': 'C',
           'SYSTEMROOT': os.environ.get('SystemRoot', ''), 'TMP': os.environ.get('TMP', ''), 'TEMP': os.environ.get('TEMP', '')}
    env.update(env_extra)
    data = posix(repo.parent / 'data')
    args = [a.replace('@DATA@', data) for a in args]
    # The script is read in by the shell that set PATH (a re-exec would get the launcher's PATH back); --help reads
    # its own file through $0, so it runs directly: it needs no stand-ins.
    if args == ['--help']:
        cmd = [str(BASH), script] + args
    else:
        cmd = [str(BASH), '-c', 'export PATH="$SANDBOX_PATH"; unset SANDBOX_PATH; s=$1; shift; . "$s"', 'bash', script] + args
    r = subprocess.run(cmd, cwd=repo, env=env, input=stdin, capture_output=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


# --- PowerShell sandbox --------------------------------------------------------------------------------------------

CMD_TAILSCALE = '@echo off\r\nif "%1"=="status" (\r\n  if "%FAKE_TS%"=="out" (echo {"BackendState":"NeedsLogin"}) else (echo {"BackendState":"Running","Self":{"DNSName":"deep32-forge.tail1234.ts.net."},"CurrentTailnet":{"MagicDNSEnabled":true},"CertDomains":["tail1234.ts.net"]})\r\n)\r\nexit /b 0\r\n'
CMD_NVIDIA = '@echo off\r\necho %* | findstr compute_cap >nul && (echo 8.9& exit /b 0)\r\necho %* | findstr driver_version >nul && (echo 581.57& exit /b 0)\r\necho %* | findstr memory.total >nul && (echo 24564& exit /b 0)\r\necho NVIDIA GeForce RTX 4090\r\n'
CMD_DOCKER = '@echo off\r\nif "%FAKE_DOCKER%"=="none" exit /b 1\r\necho %* | findstr OperatingSystem >nul && (echo Docker Desktop& exit /b 0)\r\necho %* | findstr Driver >nul && (echo overlay2 []& exit /b 0)\r\nexit /b 0\r\n'
PS_GET = "Write-Output ('stub deep32-get ' + ($args -join ' '))\n[Console]::Error.WriteLine('stand-in refusal')\nexit ([int]$env:FAKE_GET_EXIT)\n"


def ps_sandbox(base, setup_bytes):
    repo, bin_ = base / 'repo', base / 'bin'
    (repo / 'install').mkdir(parents=True)
    for name in ('matrix.json', 'deep32-wizard.ps1', 'deep32-wizard-errors.tsv', 'deep32-wizard-demo.txt'):
        shutil.copy2(ROOT / 'install' / name, repo / 'install' / name)
    shutil.copy2(ROOT / 'VERSION', repo / 'VERSION')
    write(repo / 'install/deep32-setup.ps1', setup_bytes)
    write(repo / 'src/release-tools/deep32-get.ps1', PS_GET)
    write(repo / 'adapters/connect.ps1', "Write-Output ('stub connect ' + ($args -join ' '))\nexit 0\n")
    write(bin_ / 'tailscale.cmd', CMD_TAILSCALE)
    write(bin_ / 'nvidia-smi.cmd', CMD_NVIDIA)
    write(bin_ / 'docker.cmd', CMD_DOCKER)
    return repo, bin_


PS_CASES = [
    ('check', ['check'], {}),
    ('check, signed out', ['check'], {'FAKE_TS': 'out'}),
    ('forge', ['forge'], {}),
    ('workstation', ['workstation'], {}),
    ('workstation, disc 1 stops', ['workstation'], {'FAKE_GET_EXIT': '1'}),
    ('headend', ['headend', '-Agent', 'claude', '-Workstation', 'https://ws.tail1234.ts.net/mcp'], {}),
    ('headend, bad address', ['headend', '-Agent', 'claude', '-Workstation', 'http://nope'], {}),
    ('unknown role', ['bogus'], {}),
]


def run_ps(repo, bin_, args, env_extra, script='install\\deep32-setup.ps1', host=None, stdin=b''):
    env = dict(os.environ)
    env['PATH'] = os.pathsep.join([str(bin_), str(SYSROOT / 'System32'), str(SYSROOT / 'System32/WindowsPowerShell/v1.0'), str(SYSROOT)])
    env['DEEP32_PYTHON'] = sys.executable
    env.pop('NO_COLOR', None)
    env.update(env_extra)
    r = subprocess.run([str(host or POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script] + args,
                       cwd=repo, env=env, input=stdin, capture_output=True, timeout=180)
    return r.returncode, r.stdout, r.stderr


def strip_events(data):
    return b''.join(line for line in data.splitlines(keepends=True) if not line.startswith(b'{"deep32":'))


@unittest.skipUnless(BASH.is_file(), 'bash is not available')
class SetupBashUnchanged(unittest.TestCase):
    """deep32-setup.sh prints the same bytes as before unless a wizard option is given."""

    def test_default_output_is_byte_identical(self):
        with tempfile.TemporaryDirectory(prefix='wizard setup sh ') as tmp:
            repo, bin_ = bash_sandbox(Path(tmp), git_show('install/deep32-setup.sh'))
            before = {name: run_bash(repo, bin_, args, env) for name, args, env in BASH_CASES}
            write(repo / 'install/deep32-setup.sh', (ROOT / 'install/deep32-setup.sh').read_bytes())
            for name, args, env in BASH_CASES:
                with self.subTest(case=name):
                    after = run_bash(repo, bin_, args, env)
                    self.assertEqual(after, before[name])
                    self.assertTrue(after[1] or after[2], 'the case printed nothing')

    def test_events_only_add_lines(self):
        with tempfile.TemporaryDirectory(prefix='wizard events sh ') as tmp:
            repo, bin_ = bash_sandbox(Path(tmp), (ROOT / 'install/deep32-setup.sh').read_bytes())
            for name, args, env in BASH_CASES:
                if name in ('help', 'unknown argument'):
                    continue
                with self.subTest(case=name):
                    plain = run_bash(repo, bin_, args, env)
                    events = run_bash(repo, bin_, args + ['--events'], env)
                    self.assertEqual(events[0], plain[0])
                    self.assertEqual(strip_events(events[1]), plain[1])
                    for line in events[1].splitlines():
                        if line.startswith(b'{"deep32":'):
                            self.assertIsInstance(json.loads(line), dict)

    def test_event_stream_names_steps_runs_and_the_pairing_code(self):
        with tempfile.TemporaryDirectory(prefix='wizard events forge ') as tmp:
            repo, bin_ = bash_sandbox(Path(tmp), (ROOT / 'install/deep32-setup.sh').read_bytes())
            code, out, _ = run_bash(repo, bin_, ['forge', '--events', '--data-root', '@DATA@', '--plan-digest', 'ab' * 32], {})
            events = [json.loads(l) for l in out.splitlines() if l.startswith(b'{"deep32":')]
            kinds = [e['deep32'] for e in events]
            self.assertEqual(code, 0)
            self.assertEqual([e['step'] for e in events if e['deep32'] == 'step'], ['check', 'download', 'pair'])
            self.assertIn({'deep32': 'exit', 'tool': 'get', 'status': 0}, events)
            self.assertIn({'deep32': 'pair', 'code': 'ABCDEFGHJK', 'peer': 'deep32-forge', 'minutes': 5}, events)
            self.assertIn(b'--accept-plan ' + b'ab' * 32, out)
            self.assertEqual(kinds.count('run'), 1)

    def test_questions_go_through_the_event_stream(self):
        with tempfile.TemporaryDirectory(prefix='wizard ask ') as tmp:
            repo, bin_ = bash_sandbox(Path(tmp), (ROOT / 'install/deep32-setup.sh').read_bytes())
            code, out, err = run_bash(repo, bin_, ['forge', '--checks-only', '--events'], {'FAKE_TS': 'out'}, stdin=b'y\n')
            ask = [json.loads(l) for l in out.splitlines() if l.startswith(b'{"deep32":"ask"')]
            self.assertEqual(ask, [{'deep32': 'ask', 'id': 'tailscale-signin', 'text': 'Start Tailscale sign-in now?'}])
            self.assertIn(b'{"deep32":"signin","role":"forge"}', out)
            self.assertIn(b'https://login.tailscale.com/a/test', err)
            code, out, _ = run_bash(repo, bin_, ['--guess-role', '--events'], {})
            self.assertEqual(json.loads(out.splitlines()[0])['role'], 'forge')

    def test_forge_journey_follows_a_local_workstation_vm(self):
        with tempfile.TemporaryDirectory(prefix='wizard journey ') as tmp:
            repo, bin_ = bash_sandbox(Path(tmp), (ROOT / 'install/deep32-setup.sh').read_bytes())
            log = Path(tmp) / 'virsh.log'
            write(bin_ / 'virsh', '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$VIRSH_LOG"\n'
                                  'case "$*" in *"dominfo Deep32Workstation"*) exit 0 ;; esac\nexit 1\n')
            env = {'VIRSH_LOG': posix(log)}
            code, out, _ = run_bash(repo, bin_, ['forge', '--data-root', '@DATA@'], env)
            self.assertEqual(code, 0)
            self.assertIn(b'--install --journey J4 ', out)
            self.assertIn('dominfo Deep32Workstation', log.read_text(encoding='ascii'))
            log.unlink()
            code, out, _ = run_bash(repo, bin_, ['forge', '--journey', 'J2', '--data-root', '@DATA@'], env)
            self.assertEqual(code, 0)
            self.assertIn(b'--install --journey J2 ', out)
            self.assertFalse(log.exists(), 'virsh was consulted although --journey was given')
            code, out, _ = run_bash(repo, bin_, ['forge', '--journey', 'J3'], env)
            self.assertEqual((code, out.strip()), (64, b'[fail] unknown argument: --journey J3 (try --help)'))


@unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
class SetupPowerShellUnchanged(unittest.TestCase):
    """deep32-setup.ps1 prints the same bytes as before unless a wizard option is given."""

    def test_default_output_is_byte_identical(self):
        with tempfile.TemporaryDirectory(prefix='wizard setup ps ') as tmp:
            repo, bin_ = ps_sandbox(Path(tmp), git_show('install/deep32-setup.ps1'))
            before = {name: run_ps(repo, bin_, args, env) for name, args, env in PS_CASES}
            write(repo / 'install/deep32-setup.ps1', (ROOT / 'install/deep32-setup.ps1').read_bytes())
            for name, args, env in PS_CASES:
                with self.subTest(case=name):
                    after = run_ps(repo, bin_, args, env)
                    self.assertEqual(after, before[name])
                    self.assertTrue(after[1] or after[2])

    def test_events_only_add_lines(self):
        with tempfile.TemporaryDirectory(prefix='wizard events ps ') as tmp:
            repo, bin_ = ps_sandbox(Path(tmp), (ROOT / 'install/deep32-setup.ps1').read_bytes())
            for name, args, env in PS_CASES:
                if name == 'unknown role':
                    continue
                with self.subTest(case=name):
                    plain = run_ps(repo, bin_, args, env)
                    events = run_ps(repo, bin_, args + ['-Events'], env)
                    self.assertEqual(events[0], plain[0])
                    self.assertEqual(strip_events(events[1]), plain[1])


def wizard_env(**extra):
    env = dict(os.environ)
    for k in ('NO_COLOR', 'DEEP32_WIZARD_FORCE_TTY', 'DEEP32_WIZARD_KEYS', 'DEEP32_WIZARD_COLS', 'DEEP32_WIZARD_ROWS'):
        env.pop(k, None)
    env.update({'TERM': 'xterm-256color'}, **extra)
    return env


@unittest.skipUnless(BASH.is_file(), 'bash is not available')
class WizardBash(unittest.TestCase):
    script = ROOT / 'install/deep32-wizard.sh'

    def mode(self, *args, **env):
        r = subprocess.run([str(BASH), str(self.script), '--print-mode', *args], env=wizard_env(**env),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        return r.stdout.strip()

    def test_fallback_selection(self):
        self.assertEqual(self.mode(), 'plain')                                    # not a terminal
        self.assertEqual(self.mode(DEEP32_WIZARD_FORCE_TTY='1'), 'tui')
        self.assertEqual(self.mode(DEEP32_WIZARD_FORCE_TTY='1', NO_COLOR='1'), 'plain')
        self.assertEqual(self.mode(DEEP32_WIZARD_FORCE_TTY='1', TERM='dumb'), 'plain')
        self.assertEqual(self.mode('--plain', DEEP32_WIZARD_FORCE_TTY='1'), 'plain')
        self.assertEqual(self.mode('--bogus', DEEP32_WIZARD_FORCE_TTY='1'), 'plain')  # deep32-setup reports it

    def test_plain_mode_is_deep32_setup_byte_for_byte(self):
        with tempfile.TemporaryDirectory(prefix='wizard plain sh ') as tmp:
            repo, bin_ = bash_sandbox(Path(tmp), (ROOT / 'install/deep32-setup.sh').read_bytes())
            for args, env in ((['check'], {}), (['check'], {'FAKE_TS': 'out'}), (['workstation'], {}), (['--bogus'], {}), (['--help'], {})):
                with self.subTest(args=args):
                    direct = run_bash(repo, bin_, args, env)
                    if args == ['--help']:
                        self.assertNotEqual(run_bash(repo, bin_, args, env, script='install/deep32-wizard.sh'), direct)
                        continue
                    self.assertEqual(run_bash(repo, bin_, args, env, script='install/deep32-wizard.sh'), direct)
                    self.assertEqual(run_bash(repo, bin_, ['--plain'] + args, env, script='install/deep32-wizard.sh'), direct)
                    tty_no_color = dict(env, DEEP32_WIZARD_FORCE_TTY='1', NO_COLOR='1')
                    self.assertEqual(run_bash(repo, bin_, args, tty_no_color, script='install/deep32-wizard.sh'),
                                     run_bash(repo, bin_, args, tty_no_color))

    def lib(self, body):
        prog = 'DEEP32_WIZARD_LIB=1 source "%s"\n%s\n' % (posix(self.script), body)
        r = subprocess.run([str(BASH), '-c', prog], env=wizard_env(LC_ALL='C.UTF-8'), capture_output=True, text=True,
                           encoding='utf-8', timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_progress_maths(self):
        out = self.lib(PROGRESS_BASH)
        self.assertEqual(out.splitlines(), progress_reference())

    def test_event_parsing(self):
        for line in EVENT_LINES:
            with self.subTest(line=line):
                quoted = line.replace("'", "'\\''")
                out = self.lib("json_get '%s' && for k in $(printf '%%s\\n' \"${!EV[@]}\" | sort); do printf '%%s=%%s\\0' \"$k\" \"${EV[$k]}\"; done" % quoted)
                self.assertEqual(out, event_reference(line))

    def test_crash_restores_the_terminal(self):
        env = wizard_env(DEEP32_WIZARD_FORCE_TTY='1', DEEP32_WIZARD_COLS='100', DEEP32_WIZARD_ROWS='30',
                         DEEP32_WIZARD_KEYS='waitms:300,crash,enter', HOME=tempfile.mkdtemp(prefix='wizard-home-'))
        r = subprocess.run([str(BASH), str(self.script), '--demo'], env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        out = r.stdout
        self.assertIn(b'\x1b[?1049h', out)
        self.assertGreater(out.rindex(b'\x1b[?1049l'), out.rindex(b'\x1b[?1049h'))
        self.assertGreater(out.rindex(b'\x1b[?25h'), out.rindex(b'\x1b[?25l'))
        self.assertIn(b'the test asked for an error', r.stderr)


@unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
class WizardPowerShell(unittest.TestCase):
    script = ROOT / 'install/deep32-wizard.ps1'
    hosts = [POWERSHELL] + ([Path(PWSH)] if PWSH else [])

    def mode(self, host, *args, **env):
        r = subprocess.run([str(host), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(self.script), '-PrintMode', *args],
                           env=wizard_env(**env), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        return r.stdout.strip()

    def test_fallback_selection(self):
        for host in self.hosts:
            with self.subTest(host=host.name):
                self.assertEqual(self.mode(host), 'plain')
                self.assertEqual(self.mode(host, DEEP32_WIZARD_FORCE_TTY='1'), 'tui')
                self.assertEqual(self.mode(host, DEEP32_WIZARD_FORCE_TTY='1', NO_COLOR='1'), 'plain')
                self.assertEqual(self.mode(host, DEEP32_WIZARD_FORCE_TTY='1', TERM='dumb'), 'plain')
                self.assertEqual(self.mode(host, '-Plain', DEEP32_WIZARD_FORCE_TTY='1'), 'plain')

    def test_plain_mode_is_deep32_setup_byte_for_byte(self):
        with tempfile.TemporaryDirectory(prefix='wizard plain ps ') as tmp:
            repo, bin_ = ps_sandbox(Path(tmp), (ROOT / 'install/deep32-setup.ps1').read_bytes())
            for args, env in ((['check'], {}), (['check'], {'FAKE_TS': 'out'}), (['workstation'], {}),
                              (['headend', '-Agent', 'codex', '-Workstation', 'https://ws.tail1234.ts.net/mcp'], {})):
                with self.subTest(args=args):
                    direct = run_ps(repo, bin_, args, env)
                    self.assertEqual(run_ps(repo, bin_, args, env, script='install\\deep32-wizard.ps1'), direct)
                    self.assertEqual(run_ps(repo, bin_, ['-Plain'] + args, env, script='install\\deep32-wizard.ps1'), direct)

    def lib(self, body, host=POWERSHELL):
        prog = "$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); . '%s'\n%s" % (self.script, body)
        r = subprocess.run([str(host), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', prog], env=wizard_env(),
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8', 'replace'))
        return r.stdout.decode('utf-8').replace('\r\n', '\n')

    def test_progress_maths(self):
        for host in self.hosts:
            with self.subTest(host=host.name):
                self.assertEqual(self.lib(PROGRESS_PS, host).splitlines(), progress_reference())

    def test_event_parsing(self):
        for line in EVENT_LINES:
            with self.subTest(line=line):
                quoted = line.replace("'", "''")
                out = self.lib("$h = ConvertFrom-EventLine '%s'; foreach ($k in ($h.Keys | Sort-Object { [string]$_ } -CaseSensitive)) { [Console]::Out.Write($k + '=' + $h[$k] + [char]0) }" % quoted)
                self.assertEqual(out, event_reference(line))

    def test_no_local_variable_hides_script_state(self):
        # PowerShell names are case-insensitive and scopes are dynamic: a local $st inside a screen function hides
        # the style table $St from every function it calls. Keep locals away from the script-wide names.
        text = self.script.read_text(encoding='utf-8')
        shared = {'g', 'f', 'st', 'gl', 'w', 'h', 'pw', 'cw', 'ch', 'rw', 'lb', 'lw', 'lbg', 'panel', 'rail', 'frame', 'top', 'spin'}
        callers = re.compile(r'\b(PL|PA|PE|PGap|PBlank|PLine|PSteps|PStep|PPara|PTitle|PButtons|Bx\w+|Build-\w+|View-\w+|Write-Frame)\b')
        for m in re.finditer(r'\nfunction ([\w-]+)(.*?)(?=\nfunction |\n# -{20})', text, re.S):
            name, body = m.group(1), m.group(2)
            if not callers.search(body):
                continue
            local = set(re.findall(r'(?<![:\w$])\$(\w+)\s*(?:[-+*/]?=(?!=)|\+\+|--)', body))
            local |= set(re.findall(r'foreach\s*\(\$(\w+)\s+in', body))
            local |= set(re.findall(r'\$(\w+)', body.split('{', 1)[0]))
            clash = sorted(v for v in local if v.lower() in shared and not re.search(r'\$script:' + v + r'\b', body))
            self.assertEqual(clash, [], name)


# --- real download progress: the signed mirror index and the growing .download files ---------------------------------

FIXTURE = ROOT / 'src/release-tools/contracts/fixtures/valid/mirrors-v2.json'


def progress_folder(base):
    """A download folder as deep32-get leaves it mid-way: one image done, one partial, one not started."""
    data = json.loads(FIXTURE.read_text(encoding='utf-8'))
    rev = base / 'dl/.deep32-v0.1.0-alpha.2' / ('revision-1-' + 'a' * 64)
    rev.mkdir(parents=True)
    (rev / 'MIRRORS.json').write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
    files = {b['bundle_id']: (b['file'], b['size']) for b in data['bundles']}
    (base / 'dl' / files['core'][0]).write_bytes(b'x' * files['core'][1])
    (base / 'dl' / (files['embedder-ada'][0] + '.download')).write_bytes(b'x' * 10)
    expect = sorted(['%s %d %d check' % (files['core'][0], files['core'][1], files['core'][1]),
                     '%s 10 %d down' % (files['embedder-ada'][0], files['embedder-ada'][1]),
                     '%s 0 %d wait' % (files['ingestion'][0], files['ingestion'][1])])
    return base / 'dl', expect


class DownloadProgress(unittest.TestCase):
    @unittest.skipUnless(BASH.is_file(), 'bash is not available')
    def test_bash_reads_the_mirror_index_and_the_folder(self):
        with tempfile.TemporaryDirectory(prefix='wizard dl ') as tmp:
            folder, expect = progress_folder(Path(tmp))
            prog = ('DEEP32_WIZARD_LIB=1 source "%s"; opt_demo=0; ROLE=workstation; wiz_state_init; DL_DIR="%s"; DL_DISC=disc3; '
                    'DL_CLASS=ada; dl_poll; for i in "${!DL_NAME[@]}"; do echo "${DL_NAME[i]} ${DL_HAVE[i]} ${DL_SIZE[i]} ${DL_STATE[i]}"; done'
                    % (posix(ROOT / 'install/deep32-wizard.sh'), posix(folder)))
            r = subprocess.run([str(BASH), '-c', prog], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(r.stdout.splitlines()), expect)

    @unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
    def test_powershell_reads_the_mirror_index_and_the_folder(self):
        with tempfile.TemporaryDirectory(prefix='wizard dl ') as tmp:
            folder, expect = progress_folder(Path(tmp))
            prog = (". '%s'; Initialize-State; $G.Role = 'workstation'; $G.DlDir = '%s'; $G.DlDisc = 'disc3'; $G.DlClass = 'ada'; "
                    "Update-DlPoll; foreach ($d in $G.Dl) { [Console]::Out.WriteLine(('{0} {1} {2} {3}' -f $d.Name, $d.Have, $d.Size, $d.State)) }"
                    % (ROOT / 'install/deep32-wizard.ps1', folder))
            r = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', prog], capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(r.stdout.splitlines()), expect)


# --- shared expectations ---------------------------------------------------------------------------------------------

BYTES = [0, 1, 999, 1000, 1499, 1500, 999499, 999500, 1000000, 64000000, 999999999, 1000000000, 1876951040,
         19638284288, 61859102720, 123718205440]
SECONDS = [0, 1, 59, 60, 61, 119, 3599, 3600, 3601, 5399, 7199, 7200, 86399]
BARS = [(0, 40), (1, 40), (12, 40), (13, 40), (500, 40), (987, 40), (999, 40), (1000, 40), (1200, 40), (-5, 10), (333, 1)]


def progress_reference():
    out = []
    for n in BYTES:
        if n >= 1000000000:
            v = (n + 50000000) // 100000000
            out.append('%d.%d GB' % (v // 10, v % 10))
        elif n >= 1000000:
            out.append('%d MB' % ((n + 500000) // 1000000))
        elif n >= 1000:
            out.append('%d kB' % ((n + 500) // 1000))
        else:
            out.append('%d bytes' % n)
    for s in SECONDS:
        if s < 60:
            out.append('less than a minute left')
        elif s < 3600:
            out.append('about %d min left' % ((s + 59) // 60))
        else:
            h, m = s // 3600, (s % 3600 + 59) // 60
            if m == 60:
                h, m = h + 1, 0
            out.append('about %d h left' % h if m == 0 else 'about %d h %d min left' % (h, m))
    for s in SECONDS:
        out.append('%d:%02d' % (s // 60, s % 60))
    for p, w in BARS:
        p = max(0, min(1000, p))
        halves = (p * w * 2 + 500) // 1000
        out.append('%d %d' % (halves // 2, halves % 2))
    for p in (0, 31, 32, 500, 999, 1000, 1500):
        out.append(str(max(0, min(1000, p)) * 32 // 1000))
    for a, b in ((0, 0), (5, 0), (1, 3), (2, 3), (3, 3), (4, 3), (123456789, 19638284288)):
        out.append(str(0 if b <= 0 else 1000 if a >= b else a * 1000 // b))
    return out


PROGRESS_BASH = '\n'.join(
    ['for n in %s; do fmt_bytes $n; echo "$FMT"; done' % ' '.join(map(str, BYTES)),
     'for s in %s; do fmt_eta $s; echo "$FMT"; done' % ' '.join(map(str, SECONDS)),
     'for s in %s; do fmt_clock $s; echo "$FMT"; done' % ' '.join(map(str, SECONDS)),
     'for pw in %s; do bar_cells ${pw%%:*} ${pw#*:}; echo "$BAR_FULL $BAR_HALF"; done' % ' '.join('%d:%d' % b for b in BARS),
     'for p in 0 31 32 500 999 1000 1500; do strip_cells $p; echo "$STRIP_LIT"; done',
     'for ab in 0:0 5:0 1:3 2:3 3:3 4:3 123456789:19638284288; do permille ${ab%:*} ${ab#*:}; echo "$PM"; done'])

PROGRESS_PS = '\n'.join(
    ['foreach ($n in @(%s)) { [Console]::Out.WriteLine((Format-Bytes $n)) }' % ','.join(map(str, BYTES)),
     'foreach ($s in @(%s)) { [Console]::Out.WriteLine((Format-Eta $s)) }' % ','.join(map(str, SECONDS)),
     'foreach ($s in @(%s)) { [Console]::Out.WriteLine((Format-Clock $s)) }' % ','.join(map(str, SECONDS)),
     'foreach ($pw in @(%s)) { $c = Get-BarCells $pw[0] $pw[1]; [Console]::Out.WriteLine(("{0} {1}" -f $c[0], $c[1])) }'
     % ','.join('@(%d,%d)' % b for b in BARS),
     'foreach ($p in @(0,31,32,500,999,1000,1500)) { [Console]::Out.WriteLine((Get-StripLit $p)) }',
     'foreach ($ab in @(@(0,0),@(5,0),@(1,3),@(2,3),@(3,3),@(4,3),@(123456789,19638284288))) { [Console]::Out.WriteLine((Get-Permille $ab[0] $ab[1])) }'])

EVENT_LINES = [
    '{"deep32":"step","step":"download"}',
    '{"deep32":"exit","tool":"get","status":10}',
    '{"deep32":"role","role":"forge","guessed":true,"os":"Linux","gpu":"ada"}',
    '{"deep32":"pair","code":"K7QPM4X2TA","peer":"deep32-forge","minutes":5}',
    '{"deep32":"ask","id":"tailscale-rename","text":"Rename this machine to deep32-forge on your tailnet?"}',
    '{"deep32":"run","tool":"get","disc":"disc1","dir":"/home/a b/deep32 \\"downloads\\"","note":"tab\\there\\\\back"}',
]


def event_reference(line):
    d = json.loads(line)
    return ''.join('%s=%s\0' % (k, ('true' if v is True else 'false' if v is False else v)) for k, v in sorted(d.items()))


# --- error decoding: both wizards read the one table the same way ------------------------------------------------------

DECODE_CASES = {
    'interrupted download': (['{"deep32":"run","tool":"get","disc":"disc2","dir":"/x"}',
                              'Download interrupted; verified chunks can be resumed.',
                              '{"deep32":"exit","tool":"get","status":1}', '[fail] disc 2 download stopped'], 'text'),
    'plan consent': (['Download refused: Plan consent required; rerun with --accept-plan and the displayed digest',
                      '{"deep32":"exit","tool":"get","status":1}',
                      '[fail] the Forge install stopped; its message above names the cause'], 'text'),
    'relay code': (['Relay operation is unavailable (code: relay-stop-required); preserve the selected installation for review.',
                    'Stop the relay service, then run this command again.'], 'code relay-stop-required'),
    'prerequisite id': (['Checking read-only prerequisites for this GPU host', 'host.magicdns=missing: MagicDNS',
                         '  Next: Turn on MagicDNS in the tailnet DNS settings.',
                         '{"deep32":"exit","tool":"get","status":10}'], 'code host.magicdns'),
    'connect enrolment': (['claude: waiting for headend enrolment; open its five-minute Workstation pairing window.',
                           'Gateway enrolment failed; open the Workstation headend pairing action and run Connect again.',
                           '{"deep32":"exit","tool":"connect","status":11}', '[fail] connecting the agent stopped'], 'text'),
    'Windows split install': (['Supply --confirm-desktop-isolation after reviewing Docker Desktop WSL integration for unrelated distributions.',
                               '{"deep32":"exit","tool":"get","status":1}'], 'text'),
    'nothing known': (['the moon is in the wrong phase'], 'exit setup:1'),
}


@unittest.skipUnless(BASH.is_file() and WINDOWS and POWERSHELL.is_file(), 'both shells are needed')
class DecodeParity(unittest.TestCase):
    def test_both_wizards_decode_alike(self):
        with tempfile.TemporaryDirectory(prefix='wizard decode ') as tmp:
            for name, (lines, first) in DECODE_CASES.items():
                for os_name in ('linux', 'windows'):
                    if name.startswith('Windows') and os_name != 'windows':
                        continue   # that message exists only in the Windows tools, so only the Windows row explains it
                    with self.subTest(case=name, os=os_name):
                        f = Path(tmp) / (re.sub(r'\W', '_', name) + '.txt')
                        f.write_text('\n'.join(lines) + '\n', encoding='utf-8')
                        env = wizard_env(DEEP32_WIZARD_OS=os_name, HOME=tmp, LOCALAPPDATA=tmp)
                        sh = subprocess.run([str(BASH), str(ROOT / 'install/deep32-wizard.sh'), '--decode', posix(f)],
                                            env=env, capture_output=True, text=True, timeout=120)
                        ps = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                             str(ROOT / 'install/deep32-wizard.ps1'), '-Decode', str(f)],
                                            env=env, capture_output=True, text=True, timeout=120)
                        rows_sh, rows_ps = sh.stdout.splitlines(), ps.stdout.replace('\r', '').splitlines()
                        self.assertTrue(rows_sh, sh.stderr)
                        self.assertEqual(rows_sh, rows_ps, ps.stderr)
                        self.assertTrue(rows_sh[0].startswith(first), rows_sh[0])
                        self.assertEqual(rows_sh[-1], 'text .')
                        self.assertIn('exit setup:1', rows_sh)


# --- the wizard driving the real deep32-setup in the stand-in sandbox (no demo) ----------------------------------------

def screens(data, cols=100, rows=30):
    return {name: '\n'.join(text) for name, text, _ in vtscreen.replay(data.decode('utf-8', 'replace'), cols, rows).snaps}


class WizardDrivesSetup(unittest.TestCase):
    @unittest.skipUnless(BASH.is_file(), 'bash is not available')
    def test_bash_forge_through_deep32_setup(self):
        with tempfile.TemporaryDirectory(prefix='wizard real sh ') as tmp:
            base = Path(tmp)
            repo, bin_ = bash_sandbox(base, (ROOT / 'install/deep32-setup.sh').read_bytes())
            state = base / 'state'
            keys = ['waitms:300', 'enter', 'until:checks', 'waitms:200', 'snap:checks', 'enter', 'until:parts', 'enter', 'until:role', 'enter',
                    'until:form', 'down', 'down', 'down', 'down', 'text:yes', 'snap:form', 'enter', 'until:runintro', 'enter',
                    'until:pair', 'waitms:300', 'snap:pair', 'enter', 'until:done', 'snap:done', 'q']
            env = dict(os.environ, SANDBOX_PATH=msys(bin_) + ':/usr/bin:/bin', DEEP32_WIZARD_FORCE_TTY='1', TERM='xterm-256color',
                       DEEP32_WIZARD_COLS='100', DEEP32_WIZARD_ROWS='30', DEEP32_WIZARD_KEYS=','.join(keys),
                       XDG_STATE_HOME=posix(state), HOME=posix(base))
            env.pop('NO_COLOR', None)
            cmd = [str(BASH), '-c', 'export PATH="$SANDBOX_PATH"; unset SANDBOX_PATH; s=$1; shift; . "$s"', 'bash',
                   'install/deep32-wizard.sh', '--data-root', msys(base / 'data')]
            r = subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
            shots = screens(r.stdout)
            self.assertIn('Ready for the Forge role', shots['checks'])
            self.assertIn('A B C D E', shots['pair'])
            self.assertIn('This machine is now the Forge', shots['done'])
            record = (state / 'deep32-setup/wizard.state').read_text(encoding='utf-8')
            self.assertIn('role=forge', record)
            self.assertIn('status=done', record)
            settings = json.loads((state / 'deep32-setup/forge-settings.json').read_text(encoding='utf-8'))
            self.assertEqual((settings['node_name'], settings['forge_accept_ct'], settings['role']), ('deep32-forge', True, 'forge'))
            log = next((state / 'deep32-setup').glob('wizard-*.log')).read_text(encoding='utf-8')
            self.assertIn('stub deep32-get disc3 --gpu-class ada --install --journey J2 --data-root', log)
            self.assertIn('--settings', log)
            self.assertIn('{"deep32":"pair","code":"ABCDEFGHJK"', log)

    @unittest.skipUnless(BASH.is_file(), 'bash is not available')
    def test_bash_failure_is_explained_and_resumable(self):
        with tempfile.TemporaryDirectory(prefix='wizard resume sh ') as tmp:
            base = Path(tmp)
            repo, bin_ = bash_sandbox(base, (ROOT / 'install/deep32-setup.sh').read_bytes())
            state = base / 'state'
            common = dict(os.environ, SANDBOX_PATH=msys(bin_) + ':/usr/bin:/bin', DEEP32_WIZARD_FORCE_TTY='1', TERM='xterm-256color',
                          DEEP32_WIZARD_COLS='100', DEEP32_WIZARD_ROWS='30', XDG_STATE_HOME=posix(state), HOME=posix(base),
                          FAKE_GET_EXIT='1')
            common.pop('NO_COLOR', None)
            cmd = [str(BASH), '-c', 'export PATH="$SANDBOX_PATH"; unset SANDBOX_PATH; s=$1; shift; . "$s"', 'bash',
                   'install/deep32-wizard.sh', 'workstation']
            keys = ['waitms:300', 'enter', 'until:checks', 'enter', 'until:parts', 'enter', 'until:role', 'enter', 'until:form', 'enter',
                    'until:runintro', 'enter', 'until:fail', 'waitms:300', 'snap:fail', 'q', 'right', 'enter']
            r = subprocess.run(cmd, cwd=repo, env=dict(common, DEEP32_WIZARD_KEYS=','.join(keys)), stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=300)
            self.assertNotEqual(r.returncode, 0)   # review 8: a failed setup does not exit 0
            fail = screens(r.stdout)['fail']
            self.assertIn('Download and verify stopped', fail)
            self.assertIn('Download refused: stand-in refusal', fail)
            self.assertIn('deep32-get exit 1', fail)
            self.assertIn('What to do', fail)
            self.assertIn('status=failed', (state / 'deep32-setup/wizard.state').read_text(encoding='utf-8'))
            r = subprocess.run(cmd, cwd=repo, env=dict(common, DEEP32_WIZARD_KEYS='waitms:300,snap:intro,q,right,enter'),
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
            intro = screens(r.stdout)['intro']
            self.assertIn('Last time the Workstation host setup stopped', intro)
            self.assertIn('Resume', intro)

    @unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
    def test_powershell_headend_hands_the_terminal_to_connect(self):
        with tempfile.TemporaryDirectory(prefix='wizard real ps ') as tmp:
            base = Path(tmp)
            repo, bin_ = ps_sandbox(base, (ROOT / 'install/deep32-setup.ps1').read_bytes())
            keys = ['waitms:300', 'enter', 'until:checks', 'enter', 'until:parts', 'enter', 'until:role', 'down', 'down', 'enter', 'until:form',
                    'text:https://ws.tail1234.ts.net/mcp', 'down', 'ctrl-u', 'text:claude', 'snap:form', 'enter', 'until:checks',
                    'waitms:200', 'snap:checks', 'enter', 'until:form', 'enter', 'until:runintro',
                    'enter', 'until:done', 'snap:done', 'q']
            env = dict(os.environ, DEEP32_WIZARD_FORCE_TTY='1', DEEP32_WIZARD_COLS='100', DEEP32_WIZARD_ROWS='30',
                       DEEP32_WIZARD_KEYS=','.join(keys), LOCALAPPDATA=str(base / 'local'), DEEP32_PYTHON=sys.executable)
            env['PATH'] = os.pathsep.join([str(bin_), str(SYSROOT / 'System32'), str(SYSROOT / 'System32/WindowsPowerShell/v1.0'), str(SYSROOT)])
            env.pop('NO_COLOR', None)
            r = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'install\\deep32-wizard.ps1'],
                               cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
            shots = screens(r.stdout)
            self.assertIn('Ready for the Agent PC role', shots['checks'])
            self.assertIn('Your agent is connected', shots['done'])
            self.assertIn(b'stub connect -Agent claude -Workstation https://ws.tail1234.ts.net/mcp', r.stdout)
            record = (base / 'local/Deep32-setup/wizard.state').read_text(encoding='utf-8')
            self.assertIn('role=headend', record)
            self.assertIn('status=done', record)


# --- the whole wizard, end to end, against the recorded setup ---------------------------------------------------------

EXPECT = {
    'forge': {'intro': 'What happens', 'checks': 'Tailscale is not signed in', 'ask': 'Start Tailscale sign-in now?',
              'signin': 'https://login.tailscale.com/a/1f4e7c29b03d', 'checks-ready': 'Ready for the Forge role',
              'role': 'suggested', 'form': 'Name in public logs', 'runintro': 'Nothing is installed until you type its digest',
              'parts': 'Your machines', 'download': 'files done', 'plan': '3f2a 9c41 7be0 21d5', 'plan-typo': 'does not match',
              'plan-typed': 'Install', 'install': 'so far', 'pair': 'K 7 Q P M',
              'done': 'press N to do this now, here', 'help': 'show everything the setup printed', 'log': 'deep32-setup exit 0',
              'continue': 'for the Agent PC role', 'next-agent': 'Nothing else is needed'},
    'workstation': {'parts-ws': 'Set it up on another machine', 'role-ws': 'Workstation host', 'form-ws': 'Hypervisor', 'runintro-ws': 'about 19.6 GB',
                    'download-ws': 'disc2-content.iso', 'fail': 'Download interrupted', 'retry': 'disc2-content.iso',
                    'hv-after-role': 'not boot-tested yet', 'done-ws': 'Workstation VM', 'done-ws-more': 'The VM guide shows these steps',
                    'small': 'Make the window bigger', 'send-ws': 'type R4NDQ-7KX2M', 'sent-ws': 'Sending stopped'},
    'vm': {'vm-hv': 'found on this PC', 'vm-hv-proxmox': 'boot-tested', 'vm-net': 'How the Workstation VM connects',
           'vm-tsnote': 'access rules (ACLs)', 'vm-form': 'API token (optional)', 'vm-step-image': 'Step 1 of 7',
           'vm-step-ok': 'Check again', 'vm-confirm': 'Look for:', 'vm-confirmed': 'YOU CONFIRMED',
           'vm-pin': '3A:9F:12:C4', 'vm-secret': 'API token secret', 'vm-error': 'ERROR VM_DISK_SMALL',
           'vm-create-ok': 'scsi1, 214.7 GB', 'vm-skipped': 'SKIPPED', 'vm-wait': 'Waiting for deep32',
           'vm-joined': 'deep32 is on your tailnet', 'vm-sum': 'Skipped, still to do', 'vm-done': 'steps you skipped'},
    'headend': {'parts-headend': 'Not using it', 'form-headend': 'Workstation address', 'runintro-headend': 'Start Connect',
                'done-headend': 'Nothing else is needed'},
    'send': {'send-checking': 'Send disc 2 to a Workstation', 'send-code': 'type R4NDQ-7KX2M', 'send-log': '[ok] Port 9447 is free here',
             'send-quit': 'Quitting stops sending', 'send-stopped': 'Runs as root, so Tailscale may serve the file'},
}


EXPECT_PS = {
    'forge': {'intro': 'What happens', 'checks': 'Tailscale is not signed in', 'ask': 'Not now',
              'signin': 'https://login.tailscale.com/a/1f4e7c29b03d', 'checks-ready': 'Ready for the Forge role',
              'parts': 'Your machines', 'role': 'suggested', 'form': 'Name in public logs',
              'byhand': 'two commands by hand', 'byhand-more': 'forge-by-hand.txt', 'help': 'show everything the setup printed', 'log': 'deep32-setup exit 0'},
    'workstation': {'parts-ws': 'Set it up on another machine', 'role-ws': 'Workstation host', 'form-ws': 'Hypervisor',
                    'runintro-ws': 'about 19.6 GB', 'download-ws': 'disc2-content.iso', 'fail': 'Download interrupted',
                    'retry': 'disc2-content.iso', 'hv-after-role': 'not boot-tested yet', 'done-ws': 'Workstation VM',
                    'done-ws-more': 'The VM guide shows these steps', 'continue': 'for the Agent PC role',
                    'next-agent': 'Create the Workstation VM', 'small': 'Make the window bigger',
                    'send-ws': 'type R4NDQ-7KX2M', 'sent-ws': 'Sending stopped'},
    'headend': EXPECT['headend'],
    'vm': EXPECT['vm'],
    'send': dict(EXPECT['send'], **{'send-stopped': 'Runs as administrator, so Tailscale may serve the file'}),
}


@unittest.skipIf(QUICK, 'DEEP32_WIZARD_QUICK=1 skips the full demo runs')
class DemoEndToEnd(unittest.TestCase):
    def run_demo(self, shell, script):
        with tempfile.TemporaryDirectory(prefix='wizard demo ') as tmp:
            code, err, names = capture.run(shell, script, 'dark', 100, 30, tmp)
            # The Windows Forge ends at its by-hand screen and the person quits: no Done, so not 0 (review 8).
            self.assertEqual(code, 1 if (shell, script) == ('powershell', 'forge') else 0, err[-2000:])
            for snap, needle in (EXPECT_PS if shell == 'powershell' else EXPECT)[script].items():
                text = (Path(tmp) / (snap + '.txt')).read_text(encoding='utf-8')
                self.assertIn(needle, text, '%s %s' % (shell, snap))
                if snap != 'small':
                    lines = text.splitlines()
                    self.assertEqual({len(l) for l in lines}, {100}, '%s %s: every row is exactly the width' % (shell, snap))
            data = (Path(tmp) / (script + '.ansi')).read_bytes()
            self.assertGreater(data.rindex(b'\x1b[?1049l'), data.rindex(b'\x1b[?1049h'))

    def test_bash(self):
        if not BASH.is_file():
            self.skipTest('bash is not available')
        for script in EXPECT:
            with self.subTest(script=script):
                self.run_demo('bash', script)

    def test_powershell(self):
        if not (WINDOWS and POWERSHELL.is_file()):
            self.skipTest('Windows PowerShell is not available')
        for script in EXPECT_PS:
            with self.subTest(script=script):
                self.run_demo('powershell', script)



# --- What's next: the same plan in both shells, in INSTALL.md's order, never offering what this machine cannot run -----

MACHINES = [  # (os, shell, gpu class, hypervisor, agent installed)
    ('linux', 'bash', 'ada', 'yes', 'yes'), ('linux', 'bash', '', 'no', 'no'), ('macos', 'bash', '', 'no', 'yes'),
    ('windows', 'ps', 'ada', 'yes', 'yes'), ('windows', 'ps', '', 'no', 'no'), ('windows', 'bash', 'ada', 'yes', 'yes'),
]
WS_VALUES = ('here', 'other', 'done', 'unknown')
OTHER_VALUES = ('here', 'other', 'done', 'none', 'unknown')
PLAN_CASES = [(role, m, ws, fg, ag) for role in ('forge', 'workstation', 'headend') for m in MACHINES
              for ws in WS_VALUES for fg in OTHER_VALUES for ag in OTHER_VALUES]


def can_run_reference(role, os_name, shell, gpu, hv, agent):
    """deep32-setup's role and OS rules: the wizard's Forge is Linux only in alpha.2; the Workstation VM
    needs KVM (Linux) or Hyper-V (Windows) and never runs from macOS; an agent PC needs Claude Code or Codex."""
    native = (os_name, shell) in (('linux', 'bash'), ('windows', 'ps'))
    if role == 'forge':   # deep32-setup.sh forge is Linux only; the alpha.2 Windows Forge is by hand (setup.ps1 cannot finish it)
        return bool(gpu) and (os_name, shell) == ('linux', 'bash')
    if role == 'workstation':
        return hv == 'yes' and native
    return agent == 'yes' and (native or (os_name, shell) == ('macos', 'bash'))


def plan_bash():
    body = ['DEEP32_WIZARD_LIB=1 source "%s"' % posix(ROOT / 'install/deep32-wizard.sh'), 'opt_demo=0']
    for role, (os_name, shell, gpu, hv, agent), ws, fg, ag in PLAN_CASES:
        body.append("CAP_OS=%s; CAP_SHELL=%s; GUESS_GPU='%s'; CAP_HV=%s; CAP_AGENT=%s; PART=([ws]=%s [forge]=%s [agent]=%s); "
                    "NP_ROLE=%s; NP_PAIRED=0; NP_PEER=deep32-forge; NP_VM=/v; next_plan; printf '%%s\\n' \"${NEXT[@]}\" '=='"
                    % (os_name, shell, gpu, hv, agent, ws, fg, ag, role))
    script = Path(tempfile.mkdtemp(prefix='wizard-plan-')) / 'plan.sh'
    script.write_text('\n'.join(body) + '\n', encoding='utf-8', newline='\n')
    r = subprocess.run([str(BASH), posix(script)], capture_output=True, text=True, encoding='utf-8', timeout=600)
    assert r.returncode == 0, r.stderr
    return r.stdout.split('==\n')[:-1]


def plan_ps():
    body = [". '%s'" % (ROOT / 'install/deep32-wizard.ps1'), 'Initialize-State', '$sb = New-Object System.Text.StringBuilder']
    for role, (os_name, shell, gpu, hv, agent), ws, fg, ag in PLAN_CASES:
        body.append("$script:CapOs = '%s'; $script:CapShell = '%s'; $G.GuessGpu = '%s'; $script:CapHv = '%s'; $script:CapAgent = '%s'; "
                    "foreach ($l in (Get-NextPlan '%s' @{ ws = '%s'; forge = '%s'; agent = '%s' } $false 'deep32-forge' '/v')) { [void]$sb.Append($l + \"`n\") }; [void]$sb.Append(\"==`n\")"
                    % (os_name, shell, gpu, hv, agent, role, ws, fg, ag))
    body.append('[Console]::Out.Write($sb.ToString())')
    script = Path(tempfile.mkdtemp(prefix='wizard-plan-')) / 'plan.ps1'
    script.write_text('\n'.join(body), encoding='utf-8-sig')
    r = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                       capture_output=True, timeout=900)
    assert r.returncode == 0, r.stderr.decode('utf-8', 'replace')
    return r.stdout.decode('utf-8').replace('\r\n', '\n').split('==\n')[:-1]


VM_GUIDE_CMDS = ('bash install/deep32-wizard.sh --vm-guide',
                 'powershell -NoProfile -ExecutionPolicy Bypass -File install\\deep32-wizard.ps1 -VmGuide')
SEND_COMMANDS = ('bash install/deep32-wizard.sh --send-disc2',
                 'powershell -NoProfile -ExecutionPolicy Bypass -File install\\deep32-wizard.ps1 -SendDisc2')
DOCS = '\n'.join((ROOT / name).read_text(encoding='utf-8') for name in
                 ('INSTALL.md', 'README.md', 'adapters/README.md', 'adapters/relay/README.md'))


class NextSteps(unittest.TestCase):
    def test_vm_steps_stay_after_going_on_here(self):
        # The Workstation host role writes the VM files only. After N and another part on the same machine, creating,
        # installing and importing are still to do, so the later list keeps them first, in both shells.
        want = 'step|This PC|Create the Workstation VM: read this file, change its 160 GB disk to 200 GB (the New-VHD or qemu-img line), then run its commands (Linux as root, Windows in PowerShell as administrator).|/v/commands.txt|'
        body = ('DEEP32_WIZARD_LIB=1 source "%s"\nopt_demo=0; CAP_OS=windows; CAP_SHELL=bash; GUESS_GPU=; CAP_HV=yes; CAP_AGENT=yes; '
                'PART=([ws]=done [forge]=other [agent]=here); NP_ROLE=headend; NP_PAIRED=0; NP_PEER=; NP_VM=; WS_HERE_VM=/v; next_plan; '
                'printf "%%s\\n" "${NEXT[@]}"; WS_HERE_VM=; next_plan; printf "==%%s\\n" "${NEXT[0]}"' % posix(ROOT / 'install/deep32-wizard.sh'))
        if BASH.is_file():
            out = subprocess.run([str(BASH), '-c', body], capture_output=True, text=True, encoding='utf-8', timeout=120).stdout.splitlines()
            self.assertEqual(out[0], want)
            self.assertTrue(out[-1].startswith('==') and 'Create the Workstation VM' not in out[-1], out[-1])
        if WINDOWS and POWERSHELL.is_file():
            prog = (". '%s'\nInitialize-State; $script:CapOs = 'windows'; $script:CapShell = 'ps'; $script:CapHv = 'yes'; $script:CapAgent = 'yes'\n"
                    "$p = Get-NextPlan 'headend' @{ ws = 'done'; forge = 'other'; agent = 'here' } $false '' '' '/v'; [Console]::Out.WriteLine($p[0])\n"
                    "$p = Get-NextPlan 'headend' @{ ws = 'done'; forge = 'other'; agent = 'here' } $false '' ''; [Console]::Out.WriteLine('==' + $p[0])") % (ROOT / 'install/deep32-wizard.ps1')
            script = Path(tempfile.mkdtemp(prefix='wizard-ps-')) / 't.ps1'
            script.write_text(prog, encoding='utf-8-sig')
            out = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                 capture_output=True, text=True, timeout=120).stdout.splitlines()
            self.assertEqual(out[0], want)
            self.assertTrue(out[-1].startswith('==') and 'Create the Workstation VM' not in out[-1], out[-1])

    @classmethod
    def setUpClass(cls):
        cls.bash = plan_bash() if BASH.is_file() else None
        cls.ps = plan_ps() if (WINDOWS and POWERSHELL.is_file()) else None

    def plans(self):
        plans = self.bash or self.ps
        if plans is None:
            self.skipTest('no shell to plan with')
        self.assertEqual(len(plans), len(PLAN_CASES))
        return plans

    def test_both_shells_plan_alike(self):
        if not (self.bash and self.ps):
            self.skipTest('both shells are needed')
        for case, a, b in zip(PLAN_CASES, self.bash, self.ps):
            self.assertEqual(a, b, case)

    def test_order_follows_the_journeys(self):
        # INSTALL.md: install and start the Workstation and import its knowledge base, then the Forge and its pairing,
        # then publication, then the agent and its pairing; the relay pairs on its own afterwards.
        rank = [('commands.txt', 0), ('Workstation host', 0), ('knowledge base in use before you pair', 1), ('choose Forge:', 2),
                ('Set up the Forge here', 2), ('d32-pairdesk -forge', 3), ('d32-gpu-ext status', 4), ('d32-publish publish', 5),
                ('Agent PC and give it', 6), ('Set up the agent PC here', 6), ('Install Claude Code or Codex here', 6),
                ('d32-pairdesk -headend', 7), ('--relay pair', 8), ('d32-pairdesk -relay', 9)]
        for case, plan in zip(PLAN_CASES, self.plans()):
            seen = [r for line in plan.splitlines() for key, r in rank if key in line]
            self.assertEqual(seen, sorted(seen), case)
            text = plan
            if 'd32-pairdesk -headend' in text:
                self.assertIn('d32-publish publish', text, case)       # Connect needs the address publication prints
            if case[0] == 'forge':
                self.assertIn('d32-gpu-ext status', text, case)

    def test_offers_only_what_this_machine_can_run(self):
        for case, plan in zip(PLAN_CASES, self.plans()):
            role, machine = case[0], case[1]
            for line in plan.splitlines():
                kind, where, text, cmds, offered = line.split('|')
                if kind != 'offer':
                    continue
                self.assertTrue(can_run_reference(offered, *machine), (case, line))
                self.assertNotEqual(offered, role, case)
                self.assertEqual(where, 'This PC')
            if machine[0] == 'macos':
                self.assertNotIn('|forge', plan)
                self.assertNotIn('|workstation', plan)

    def test_same_machine_pairs_exist_in_the_matrix(self):
        # A Workstation VM and a Forge on one machine are the matrix's one-machine profiles: linux-nvidia, win-nvidia.
        matrix = json.loads((ROOT / 'install/matrix.json').read_text(encoding='utf-8'))
        one = {p['id'] for p in matrix['profiles'] if p.get('machines') == 1}
        self.assertTrue({'linux-nvidia', 'win-nvidia'} <= one)
        for case, plan in zip(PLAN_CASES, self.plans()):
            role, (os_name, shell, gpu, hv, agent) = case[0], case[1]
            here = {role} | {line.split('|')[4] for line in plan.splitlines() if line.startswith('offer|')}
            if {'forge', 'workstation'} <= here:
                self.assertIn({'linux': 'linux-nvidia', 'windows': 'win-nvidia'}.get(os_name), one, case)

    def test_commands_are_the_documented_ones(self):
        for case, plan in zip(PLAN_CASES, self.plans()):
            for line in plan.splitlines():
                for cmd in filter(None, line.split('|')[3].split('^')):
                    if cmd.endswith('commands.txt'):
                        continue
                    if 'deep32-wizard' in cmd:
                        if cmd in VM_GUIDE_CMDS or cmd in SEND_COMMANDS:   # the wizard's own options, in its --help (both shells)
                            continue
                        self.assertTrue(cmd.startswith('git clone https://github.com/hellobpdu/deep32.git'), cmd)
                        continue
                    self.assertIn(cmd, DOCS, cmd)

    def test_wizard_options_are_in_the_help(self):
        # --help prints lines 2-18 of either wizard: both the VM guide and Send disc 2 are there.
        sh = ''.join((ROOT / 'install/deep32-wizard.sh').read_text(encoding='utf-8').splitlines(True)[1:18])
        ps = ''.join((ROOT / 'install/deep32-wizard.ps1').read_text(encoding='utf-8').splitlines(True)[1:18])
        for cmd in VM_GUIDE_CMDS + SEND_COMMANDS:
            self.assertIn(cmd.split()[-1], sh if cmd.startswith('bash') else ps, cmd)

    def test_after_the_guide(self):
        # The guide finished: its VM steps leave the list; the steps it skipped come back as one step. Both shells alike.
        want = ("step|Workstation VM|Do the VM guide's steps you skipped (First boot and Tailscale; The disc 2 import). Each command "
                "is in its steps file in the VM folder, or run the guide again:|" + '^'.join(VM_GUIDE_CMDS) + '|')
        if BASH.is_file():
            body = ('DEEP32_WIZARD_LIB=1 source "%s"\nopt_demo=0; CAP_OS=linux; CAP_SHELL=bash; GUESS_GPU=; CAP_HV=yes; CAP_AGENT=no; '
                    'PART=([ws]=here [forge]=other [agent]=other); NP_ROLE=workstation; NP_PAIRED=0; NP_PEER=; NP_VM=/v; NP_VMDONE=1; '
                    'NP_VMSKIP="First boot and Tailscale; The disc 2 import"; next_plan; printf "%%s\\n" "${NEXT[@]}"; NP_VMSKIP=; next_plan; '
                    'printf "==%%s\\n" "${NEXT[0]}"' % posix(ROOT / 'install/deep32-wizard.sh'))
            out = subprocess.run([str(BASH), '-c', body], capture_output=True, text=True, encoding='utf-8', timeout=120).stdout.splitlines()
            self.assertEqual(out[0], want)
            self.assertNotIn('commands.txt', '\n'.join(out))
            self.assertTrue(out[-1].startswith('==step|GPU PC'), out[-1])
        if WINDOWS and POWERSHELL.is_file():
            prog = (". '%s'\nInitialize-State; $script:CapOs = 'windows'; $script:CapShell = 'ps'; $script:CapHv = 'yes'; $script:CapAgent = 'no'\n"
                    "$p = Get-NextPlan 'workstation' @{ ws = 'here'; forge = 'other'; agent = 'other' } $false '' '/v' '' $true 'First boot and Tailscale; The disc 2 import'; [Console]::Out.WriteLine($p[0])\n"
                    "$p = Get-NextPlan 'workstation' @{ ws = 'here'; forge = 'other'; agent = 'other' } $false '' '/v' '' $true ''; [Console]::Out.WriteLine('==' + $p[0])") % (ROOT / 'install/deep32-wizard.ps1')
            script = Path(tempfile.mkdtemp(prefix='wizard-ps-')) / 't.ps1'
            script.write_text(prog, encoding='utf-8-sig')
            out = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                 capture_output=True, text=True, timeout=120).stdout.splitlines()
            self.assertEqual(out[0], want)
            self.assertTrue(out[-1].startswith('==step|GPU PC'), out[-1])

    def test_send_disc2_follows_the_disc2_step_where_disc_2_is(self):
        # Disc 2 is on this machine once the Workstation host role ran here: right after the disc 2 step (a CD drive,
        # a USB stick or the network) the list offers to send it over the tailnet (S on the Done screen, or the
        # command later). Nowhere else.
        for case, plan in zip(PLAN_CASES, self.plans()):
            lines = plan.splitlines()
            sends = [i for i, l in enumerate(lines) if '--send-disc2' in l]
            if case[0] == 'workstation':
                self.assertEqual(len(sends), 1, case)
                self.assertTrue(lines[sends[0]].startswith('note|This PC|To send disc 2 from this PC'), case)
                self.assertIn('Give it disc 2 in one of three ways', lines[sends[0] - 1], case)
                self.assertEqual(lines[sends[0]].split('|')[3].split('^'), list(SEND_COMMANDS), case)
            else:
                self.assertEqual(sends, [], case)

    def test_nothing_left_is_said_plainly(self):
        plans = dict(zip(PLAN_CASES, self.plans()))
        done = plans[('headend', MACHINES[0], 'done', 'done', 'here')]
        self.assertFalse([l for l in done.splitlines() if not l.startswith('note|')])
        none = plans[('headend', MACHINES[0], 'done', 'none', 'here')]
        self.assertIn('note|Forge|No Forge', none)


# --- transfer maths: a moving 30-second window, time left only once it means something ------------------------------

RATE_CASES = [
    '', '1000:0', '1000:0 2000:500', '1000:0 4000:3000', '0:0 5000:5000000 10000:10000000',
    '0:0 20000:1000 40000:41000 60000:81000',          # the first 30 s fall out of the window
    '0:500 10000:400',                                  # a restart shrinks the file: no negative speed
    '0:0 3000:0 13000:0',                               # stalled
]
ETA_CASES = [(-1, 1000, 20000), (5000, -1, 20000), (5000, 0, 20000), (5000, 1000, 9999), (5000, 1000, 10000),
             (1, 1000, 10000), (0, 1000, 10000), (123456789, 1048576, 31000)]


def rate_reference(samples):
    pts = [tuple(int(x) for x in s.split(':')) for s in samples.split()]
    if not pts:
        return (-1, 0)
    t1, b1 = pts[-1]
    t0, b0 = next(p for p in pts if p[0] >= t1 - 30000)
    span = t1 - t0
    if span < 3000:
        return (-1, span)
    return (max(0, (b1 - b0) * 1000 // span), span)


def eta_reference(remain, rate, span):
    return -1 if remain < 0 or rate <= 0 or span < 10000 else (remain + rate - 1) // rate


EXPECT_MATHS = [('%d %d' % rate_reference(c)) for c in RATE_CASES] + [str(eta_reference(*c)) for c in ETA_CASES]


class TransferMaths(unittest.TestCase):
    @unittest.skipUnless(BASH.is_file(), 'bash is not available')
    def test_bash(self):
        body = ['DEEP32_WIZARD_LIB=1 source "%s"' % posix(ROOT / 'install/deep32-wizard.sh')]
        body += ['win_rate "%s"; echo "$RATE $SPAN"' % c for c in RATE_CASES]
        body += ['eta_of %d %d %d; echo "$ETA"' % c for c in ETA_CASES]
        r = subprocess.run([str(BASH), '-c', '\n'.join(body)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), EXPECT_MATHS)

    @unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
    def test_powershell(self):
        body = [". '%s'" % (ROOT / 'install/deep32-wizard.ps1')]
        body += ["$r = Get-WindowRate '%s'; [Console]::Out.WriteLine(('{0} {1}' -f $r[0], $r[1]))" % c for c in RATE_CASES]
        body += ['[Console]::Out.WriteLine((Get-EtaSeconds %d %d %d))' % c for c in ETA_CASES]
        r = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', '\n'.join(body)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), EXPECT_MATHS)


# --- PowerShell speed: the screen must keep up on Windows PowerShell 5.1 -----------------------------------------------

@unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
class PowerShellBudget(unittest.TestCase):
    """Budgets: the first frame under 1 s from the script's start (PowerShell's own start-up comes before it and is
    not the wizard's), a frame under 50 ms (90th percentile, the first frame aside) at 80x24 and 200x60, and a demo
    step that starts at once (no PowerShell started per step)."""

    def test_budget(self):
        import timing
        for cols, rows in ((80, 24), (200, 60)):
            with self.subTest(size='%dx%d' % (cols, rows)):
                # A timing on a shared machine can catch another job's spike: the better of two runs is judged.
                runs = [timing.measure(cols=cols, rows=rows) for _ in range(2)]
                first = min(m['profile_ms']['first-frame'] for m in runs)
                step = min(m['profile_ms']['run-start look'] - m['profile_ms']['run-start guess'] for m in runs)
                p90 = min(sorted(m['builds'])[int(len(m['builds']) * 0.9) - 1] for m in runs)
                self.assertLess(first, 1000, runs)
                self.assertLess(step, 1500, runs)   # the recorded guess plays 0.5 s; a PowerShell start per step was 1.2 s more
                self.assertLess(p90, 50, runs)


# --- the review of 3e0c50f: each test reproduces its defect on 3e0c50f and passes now ---------------------------------

PROMPTING_SUDO = r"""#!/bin/sh
# A sudo that asks for a password like the real one: without a cached ticket and without -n it "reads" one and logs
# what it was asked to run. A prompt inside a wizard step means the wizard let sudo read the keys it owns.
n=0; v=0; k=0
while [ "${1#-}" != "$1" ]; do case $1 in -n) n=1 ;; -v) v=1 ;; -k) k=1 ;; esac; shift; done
[ $k = 1 ] && { rm -f "$SUDO_CACHE"; exit 0; }
if [ ! -e "$SUDO_CACHE" ]; then
    [ $n = 1 ] && { echo 'sudo: a password is required' >&2; exit 1; }
    [ $# -gt 0 ] && echo "PROMPTED $*" >> "$SUDO_LOG"
    : > "$SUDO_CACHE"
fi
[ $# -gt 0 ] || exit 0
exec "$@"
"""


def wizard_in_sandbox(base, args, keys, extra=None, timeout=300):
    repo, bin_ = bash_sandbox(base, (ROOT / 'install/deep32-setup.sh').read_bytes())
    write(bin_ / 'sudo', PROMPTING_SUDO)
    env = dict(os.environ, SANDBOX_PATH=msys(bin_) + ':/usr/bin:/bin', DEEP32_WIZARD_FORCE_TTY='1', TERM='xterm-256color',
               DEEP32_WIZARD_COLS='100', DEEP32_WIZARD_ROWS='30', DEEP32_WIZARD_KEYS=','.join(keys),
               XDG_STATE_HOME=posix(base / 'state'), HOME=posix(base), SUDO_CACHE=posix(base / 'sudo.cache'),
               SUDO_LOG=posix(base / 'sudo.log'))
    env.pop('NO_COLOR', None)
    env.update(extra or {})
    cmd = [str(BASH), '-c', 'export PATH="$SANDBOX_PATH"; unset SANDBOX_PATH; s=$1; shift; . "$s"', 'bash',
           'install/deep32-wizard.sh'] + args
    return subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)


@unittest.skipUnless(BASH.is_file(), 'bash is not available')
class ReviewBash(unittest.TestCase):
    def test_r1_sudo_never_prompts_inside_a_step(self):
        with tempfile.TemporaryDirectory(prefix='wizard r1 ') as tmp:
            base = Path(tmp)
            r = wizard_in_sandbox(base, ['--data-root', msys(base / 'data')],
                                  ['waitms:300', 'enter', 'until:checks', 'waitms:300', 'snap:checks', 'q', 'right', 'enter'])
            log = (base / 'sudo.log').read_text(encoding='utf-8') if (base / 'sudo.log').exists() else ''
            self.assertEqual(log, '', 'sudo asked for a password inside a step that owns the screen')
            self.assertIn('Ready for the Forge role', screens(r.stdout)['checks'])

    def test_r3_yes_never_reaches_the_look(self):
        with tempfile.TemporaryDirectory(prefix='wizard r3 ') as tmp:
            base = Path(tmp)
            wizard_in_sandbox(base, ['--yes', '--data-root', msys(base / 'data')],
                              ['waitms:300', 'enter', 'until:checks', 'waitms:300', 'q', 'right', 'enter'],
                              {'FAKE_TS': 'out'})
            log = next((base / 'state/deep32-setup').glob('wizard-*.log')).read_text(encoding='utf-8')
            looks = [l for l in log.splitlines() if '--checks-only' in l]
            self.assertTrue(looks)
            self.assertFalse([l for l in looks if '--yes' in l], looks)

    def test_r5_quit_stops_everything_the_step_started(self):
        with tempfile.TemporaryDirectory(prefix='wizard r5 ') as tmp:
            base = Path(tmp)
            pidfile = base / 'sleeper.pid'
            repo_get = ('#!/bin/sh\ntrap "exit 130" INT\nsh -c \'echo $$ > "$FAKE_PIDFILE"; exec sleep 600\'\n')
            keys = ['waitms:300', 'enter', 'until:checks', 'enter', 'until:parts', 'enter', 'until:role', 'enter', 'until:form',
                    'enter', 'until:runintro', 'enter', 'until:download', 'wait:2', 'q', 'right', 'enter']
            repo, bin_ = bash_sandbox(base, (ROOT / 'install/deep32-setup.sh').read_bytes())
            write(repo / 'src/release-tools/deep32-get.sh', repo_get)
            env = dict(os.environ, SANDBOX_PATH=msys(bin_) + ':/usr/bin:/bin', DEEP32_WIZARD_FORCE_TTY='1', TERM='xterm-256color',
                       DEEP32_WIZARD_COLS='100', DEEP32_WIZARD_ROWS='30', DEEP32_WIZARD_KEYS=','.join(keys),
                       XDG_STATE_HOME=posix(base / 'state'), HOME=posix(base), FAKE_PIDFILE=posix(pidfile))
            env.pop('NO_COLOR', None)
            cmd = [str(BASH), '-c', 'export PATH="$SANDBOX_PATH"; unset SANDBOX_PATH; s=$1; shift; . "$s"', 'bash',
                   'install/deep32-wizard.sh', 'workstation']
            subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
            pid = pidfile.read_text().strip()
            alive = subprocess.run([str(BASH), '-c', 'sleep 1; kill -0 %s 2>/dev/null && echo alive; kill -KILL %s 2>/dev/null; true' % (pid, pid)],
                                   capture_output=True, text=True, timeout=60).stdout
            self.assertNotIn('alive', alive, 'the downloader child outlived Quit')

    def lib(self, body, env=None):
        prog = 'DEEP32_WIZARD_LIB=1 source "%s"\n%s\n' % (posix(ROOT / 'install/deep32-wizard.sh'), body)
        r = subprocess.run([str(BASH), '-c', prog], env=dict(wizard_env(LC_ALL='C.UTF-8'), **(env or {})),
                           capture_output=True, text=True, encoding='utf-8', timeout=120)
        return r

    def test_r2_handover_shows_a_prompt_before_the_answer(self):
        with tempfile.TemporaryDirectory(prefix='wizard r2 ') as tmp:
            body = ('S_B=; S_0=; S_MUTED=; REAL_TTY=0; STATE_DIR="%s"; LOG_FILE="%s/log"\n'
                    'handover title sh -c \'printf "Type merge to approve this file plan: "; sleep 2; read a; echo "got $a"\'\n'
                    'echo "status $HANDOVER_STATUS"' % (posix(tmp), posix(tmp)))
            prog = 'DEEP32_WIZARD_LIB=1 source "%s"\n%s\n' % (posix(ROOT / 'install/deep32-wizard.sh'), body)
            import time
            t0 = time.perf_counter()
            pr = subprocess.Popen([str(BASH), '-c', prog], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  env=wizard_env(LC_ALL='C.UTF-8'))
            pr.stdin.write(b'merge\n\n'); pr.stdin.flush()
            seen, buf = None, b''
            while True:
                chunk = pr.stdout.read1(4096)
                if not chunk:
                    break
                buf += chunk
                if seen is None and b'Type merge to approve this file plan: ' in buf:
                    seen = time.perf_counter() - t0
            pr.wait(timeout=60)
            total = time.perf_counter() - t0
            self.assertIsNotNone(seen)
            self.assertLess(seen, total - 1.5, 'the prompt appeared only with the answer')
            self.assertIn(b'got merge', buf)
            self.assertIn(b'status 0', buf)

    def test_r4_root_owned_download_folder(self):
        with tempfile.TemporaryDirectory(prefix='wizard r4 ') as tmp:
            base = Path(tmp)
            folder, expect = progress_folder(base)
            bin_ = base / 'bin'; write(bin_ / 'sudo', PROMPTING_SUDO)
            body = ('opt_demo=0; ROLE=forge; wiz_state_init; REAL_SUDO="%s"; DL_DIR="%s"; DL_DISC=disc3; DL_CLASS=ada; dl_poll; '
                    'echo "hidden ${DL_HIDDEN:-0}"; for i in "${!DL_NAME[@]}"; do echo "${DL_NAME[i]} ${DL_HAVE[i]} ${DL_SIZE[i]} ${DL_STATE[i]}"; done')
            body = body % (posix(bin_ / 'sudo'), posix(folder))
            env = {'DEEP32_WIZARD_TEST_UNREADABLE': '1', 'SUDO_CACHE': posix(base / 'cache'), 'SUDO_LOG': posix(base / 'log')}
            r = self.lib(body, env)                       # no sudo ticket: nothing readable, and it says so
            self.assertEqual(r.stdout.splitlines(), ['hidden 1'], r.stderr)
            (base / 'cache').write_text('')
            r = self.lib(body, env)                       # a ticket: the rows come through sudo -n
            self.assertEqual(r.stdout.splitlines()[0], 'hidden 0')
            self.assertEqual(sorted(r.stdout.splitlines()[1:]), expect)

    def test_r9_a_question_opens_on_not_now_and_waits_behind_a_dialog(self):
        ask = '{"deep32":"ask","id":"tailscale-rename","text":"Rename this machine?"}'
        r = self.lib("wiz_state_init; RUN_KIND=fix; on_line '%s'; echo \"$OVERLAY $FOCUS\"; "
                     "OVERLAY=quit; on_line '%s'; echo \"$OVERLAY ${ASK_PENDING:-0}\"" % (ask, ask))
        self.assertEqual(r.stdout.split(), ['ask', '1', 'quit', '1'], r.stderr)

    def test_r15_forge_names_the_installer_refuses(self):
        for name, ok in (('deep32-forge', True), ('deep32-token-box', False), ('deep32-' + 'a' * 33, False),
                         ('deep32-' + 'z' * 32, True), ('deep32-cookie-jar', False)):
            r = self.lib('ROLE=forge; F_ROOT=/opt/deep32; F_TZ=Etc/UTC; F_USER=deep32; F_CT=yes; F_NAME=%s; form_valid; echo "[$FORM_ERR]"' % name)
            self.assertEqual(r.stdout.strip() == '[]', ok, (name, r.stdout))

    def test_r23_event_values_never_reach_arithmetic(self):
        with tempfile.TemporaryDirectory(prefix='wizard-r23-') as tmp:
            mark = posix(Path(tmp) / 'pwned')
            r = self.lib("wiz_state_init; opt_demo=0; RUN_KIND=role; "
                         "on_line '{\"deep32\":\"checked\",\"problems\":\"b[$(echo x > %s)]\"}'; "
                         "on_line '{\"deep32\":\"demo-progress\",\"file\":\"x\",\"bytes\":\"a[$(echo x > %s)]\",\"size\":10}'; "
                         "on_line '{\"deep32\":\"pair\",\"code\":\"K7QPM4X2TA\",\"minutes\":\"m[$(echo x > %s)]\"}'; echo done" % (mark, mark, mark))
            self.assertIn('done', r.stdout, r.stderr)
            self.assertFalse(Path(tmp, 'pwned').exists())


@unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
class ReviewPowerShell(unittest.TestCase):
    def ps(self, body, timeout=120):
        prog = ". '%s'\n$script:St = @{ '0' = ''; B = ''; MUTED = '' }\n%s" % (ROOT / 'install/deep32-wizard.ps1', body)
        script = Path(tempfile.mkdtemp(prefix='wizard-ps-')) / 't.ps1'
        script.write_text(prog, encoding='utf-8-sig')
        return subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                              capture_output=True, text=True, timeout=timeout)

    def test_r2_handover_shows_a_prompt_before_the_answer(self):
        import time
        body = ("$script:Utf8 = [System.Text.UTF8Encoding]::new($false); $script:StdOut = [Console]::OpenStandardOutput(); $script:WorkDir = $PWD.Path\n"
                "$script:LogFile = ''; Initialize-State\n"
                "Invoke-Handover 'title' @('-NoProfile', '-Command', \"[Console]::Out.Write('Type merge to approve this file plan: '); [Console]::Out.Flush(); Start-Sleep -Seconds 3; [Console]::Out.WriteLine('done')\")\n"
                "[Console]::Out.WriteLine('status ' + $G.HandoverStatus)")
        prog = ". '%s'\n$script:St = @{ '0' = ''; B = ''; MUTED = '' }\n%s" % (ROOT / 'install/deep32-wizard.ps1', body)
        script = Path(tempfile.mkdtemp(prefix='wizard-ps-')) / 't.ps1'
        script.write_text(prog, encoding='utf-8-sig')
        t0 = time.perf_counter()
        pr = subprocess.Popen([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        seen = done = None
        buf = b''
        while True:
            chunk = pr.stdout.read1(4096)
            if not chunk:
                break
            buf += chunk
            now = time.perf_counter() - t0
            if seen is None and b'Type merge to approve this file plan: ' in buf:
                seen = now
            if done is None and b'done' in buf:
                done = now
        pr.wait(timeout=60)
        self.assertIsNotNone(seen, buf)
        self.assertIsNotNone(done, buf)
        self.assertGreater(done - seen, 2.0, 'the prompt appeared only with the rest of the line')
        self.assertIn(b'status 0', buf)

    def test_r6_a_finished_pairing_stays_finished_and_one_setup_runs_at_a_time(self):
        body = ("$script:Demo = $true; Initialize-State; $G.Role = 'forge'; $G.PairCode = 'ABCDEFGHJK'; $G.Paired = $true; $G.View = 'done'\n"
                "Complete-RoleRun 0; [Console]::Out.WriteLine('view ' + $G.View)\n"
                "$G.View = 'download'; $G.Paired = $true; Complete-RoleRun 0; [Console]::Out.WriteLine('view ' + $G.View)\n"
                "$old = Start-Process -FilePath ping.exe -ArgumentList '-n 60 127.0.0.1' -PassThru -WindowStyle Hidden\n"
                "$G.Proc = $old; $script:Demo = $true; $script:LogFile = ''; $G.CheckFor = 'forge'; Start-Run 'look' @()\n"
                "Start-Sleep -Milliseconds 500; [Console]::Out.WriteLine('old exited ' + $old.HasExited)\n"
                "if (-not $old.HasExited) { $old.Kill() }")
        r = self.ps(body)
        self.assertEqual(r.stdout.split(), ['view', 'done', 'view', 'done', 'old', 'exited', 'True'], r.stderr)

    def test_r7_windows_forge_is_shown_as_by_hand(self):
        body = ("Set-Glyphs; $script:Depth = 'truecolor'; $script:ThemeName = 'dark'; Set-Palette\n"
                "$script:VersionId = 'x'; $script:LogFile = ''; $script:StateDir = $env:TEMP; $script:WorkDir = 'C:\\x'\n"
                "$script:F = @{ Root = 'C:\\Deep32'; Name = 'deep32-forge'; Tz = 'Etc/UTC'; User = 'deep32'; Kb = 'us'; Profile = 'two-machines'; Ct = 'yes'; Hv = 'hyper-v'; Url = ''; Agent = 'auto' }\n"
                "Initialize-State; $G.Role = 'forge'; $G.GuessGpu = 'ada'; $G.View = 'runintro'; $script:W = 100; $script:H = 30; Set-Layout\n"
                "Build-Frame; $t = ($Frame -join \"`n\") -replace \"`e\\[[0-9;?]*[A-Za-z]\", ''\n"
                "[Console]::Out.WriteLine([int]$t.Contains('two commands by hand')); [Console]::Out.WriteLine([int]$t.Contains('--from FOLDER')); [Console]::Out.WriteLine([int]$t.Contains(' Start '))\n"
                "$script:CapOs = 'windows'; $script:CapShell = 'ps'; [Console]::Out.WriteLine([int](Test-CanRun 'forge'))")
        r = self.ps(body.replace('`e', '$([char]27)'))
        self.assertEqual(r.stdout.split(), ['1', '1', '0', '0'], r.stderr)

    def test_r9_and_r15(self):
        body = ("Initialize-State; $G.RunKind = 'fix'; Read-RunEvent @{ deep32 = 'ask'; id = 'x'; text = 'Q?' }; [Console]::Out.WriteLine($G.Overlay + ' ' + $G.Focus)\n"
                "$G.Overlay = 'quit'; Read-RunEvent @{ deep32 = 'ask'; id = 'x'; text = 'Q?' }; [Console]::Out.WriteLine($G.Overlay + ' ' + [int]$G.AskPending)\n"
                "$script:F = @{ Root = 'C:\\Deep32'; Tz = 'Etc/UTC'; User = 'deep32'; Ct = 'yes' }; $G.Role = 'forge'\n"
                "foreach ($n in 'deep32-forge', 'deep32-token-box', ('deep32-' + 'a' * 33), ('deep32-' + 'z' * 32)) { $F.Name = $n; [Console]::Out.WriteLine([int][bool](Test-Form)) }")
        r = self.ps(body)
        self.assertEqual(r.stdout.split(), ['ask', '1', 'quit', '1', '0', '1', '1', '0'], r.stderr)


# --- The Workstation VM guide: one data file, the same parsers and rules in both shells, nothing written to the image ---

HV_DATA = ROOT / 'install/deep32-wizard-hv.tsv'
TRANSCRIPTS = HERE / 'transcripts'
FIXTURES = HERE / 'fixtures'
HV_IDS = ['proxmox', 'esxi', 'vmware', 'virtualbox', 'hyper-v', 'kvm', 'other']
CHECKS = {'image', 'copy', 'vmdk', 'vdi', 'vm:create', 'vm:after', 'vm:create+look', 'vm:after+look', 'join', 'confirm'}
PLACEHOLDERS = {'image', 'imagename', 'sha256', 'bytes', 'sectors', 'mib', 'kit', 'copy', 'copyname', 'gethv', 'disc2',
                'disc2name', 'dl', 'sep', 'vdi', 'hvname', 'net', 'netlabel', 'nodeimage', 'ts-stall', 'vm', 'vmid',
                'storage', 'bridge', 'node', 'host', 'token', 'datastore', 'vmx', 'hostif', 'switch', 'wsname', 'cyl',
                'tool', 'vmxdefault'}


def hv_rows():
    rows = []
    for line in HV_DATA.read_bytes().decode('ascii').split('\n'):
        if line and not line.startswith('#'):
            rows.append([('' if f == '-' else f) for f in line.split('\t')])
    return rows


def table_codes():
    codes = set()
    for line in (ROOT / 'install/deep32-wizard-errors.tsv').read_text(encoding='ascii').splitlines():
        f = line.split('\t')
        if len(f) == 6 and f[0] == 'code':
            codes.add(f[1])
    return codes


class HvData(unittest.TestCase):
    """install/deep32-wizard-hv.tsv: the format both wizards read, and what it may say."""

    def test_format(self):
        raw = HV_DATA.read_bytes()
        self.assertTrue(raw.isascii() and b'\r' not in raw and raw.endswith(b'\n'))
        widths = {'hv': 8, 'field': 9, 'step': 11, 'rule': 7, 'fact': 3, 'fix': 4, 'text': 3}
        for f in hv_rows():
            self.assertIn(f[0], widths, f)
            self.assertEqual(len(f), widths[f[0]], f)
            # the one "!" is Proxmox's token ID separator (user@realm!tokenid); the text has no exclamation marks
            text = ' '.join(f[1:6] + f[7:]) if f[0] == 'field' else ' '.join(f[1:])
            self.assertNotIn('!', re.sub(r'[\w@.]+![\w-]+', '', text), f)

    def test_hypervisors_and_their_labels(self):
        hv = [f for f in hv_rows() if f[0] == 'hv']
        self.assertEqual([f[1] for f in hv], HV_IDS)
        self.assertEqual({f[1]: f[3] for f in hv}, {i: ('yes' if i == 'proxmox' else 'no') for i in HV_IDS})   # only Proxmox is boot-tested

    def test_steps_rules_and_fixes_are_complete(self):
        rows = hv_rows()
        codes = table_codes()
        facts = {f[1] for f in rows if f[0] == 'fact'}
        for hv in HV_IDS:
            steps = [f for f in rows if f[0] == 'step' and f[1] == hv]
            ids = [f[5] for f in steps]
            with self.subTest(hv=hv):
                self.assertEqual(ids[0], 'image')
                self.assertEqual(ids[-1], 'import')
                self.assertIn('firstboot', ids)
                self.assertIn('after', ids)
        for f in rows:
            if f[0] == 'step':
                self.assertIn(f[6], CHECKS, f)
                if f[6] in ('confirm',) or f[6].startswith('vm:'):
                    self.assertTrue(f[10], 'a step the person may have to confirm says what to look for: %s %s' % (f[1], f[5]))
                for name in re.findall(r'\{([a-z0-9-]+)\}', ' '.join(f[7:])):
                    self.assertIn(name, PLACEHOLDERS, f)
            if f[0] == 'rule':
                self.assertIn(f[6], codes, f)
                self.assertIn(f[3], facts, f)
            if f[0] == 'fix':
                self.assertIn(f[2], codes, f)
                self.assertLessEqual(len(f[3].split(' | ')), 5, f)

    def test_text_is_plain(self):
        text = ' '.join(' '.join(f[2:]) for f in hv_rows() if f[0] in ('step', 'text', 'fix', 'field'))
        for word in ('simply', 'just ', 'easy', 'seamless', 'powerful', 'blazing', 'robust'):
            self.assertNotIn(word, text.lower())
        self.assertNotRegex(text, r'\bnot [a-z]+, but\b')

    def test_getter_recipes_match_commands_txt(self):
        # Hyper-V and KVM use deep32-get's own recipe (src/release-tools/deep32_get_manifest.py, frozen). The steps
        # repeat its command lines with the target at 200 GB and the switch filled in; nothing else may differ.
        source = (ROOT / 'src/release-tools/deep32_get_manifest.py').read_text(encoding='utf-8')
        blocks = re.findall(r'commands = """(.*?)"""', source, re.S)
        self.assertEqual(len(blocks), 2)
        steps = {(f[1], f[5]): f[9] for f in hv_rows() if f[0] == 'step'}
        for hv, block in (('hyper-v', blocks[0]), ('kvm', blocks[1])):
            body = steps[(hv, 'create')] + ' ' + steps[(hv, 'install')]
            cmds = re.findall(r'`([^`]+)`', body)
            for line in block.strip().splitlines():
                if line.startswith('#') or 'Read-Host' in line:
                    continue
                want = (line.replace('160GB', '200GB').replace(' 160G', ' 200G').replace('-SwitchName $switch', "-SwitchName '{switch}'")
                        .replace('Deep32Workstation', '{vm}'))
                with self.subTest(hv=hv, line=line):
                    self.assertIn(want, cmds)
            self.assertIn('200', block)    # the getter writes 200 GB
            self.assertNotIn('still says 160', steps[(hv, 'create')])


VM_RULES_SH = r'''
hv_load; VM_HV=$1; kind=$3; phase=$4; VF=([disc2name]=deep32-0.1.0-alpha.2-disc2-content.iso)
mapfile -t L < "$2"
case $kind in pve) pve_facts "${L[@]}" ;; vbox) VB_CAP=(); for l in "${L[@]}"; do case $l in cap\ *) read -r _ cp cs <<<"$l"; VB_CAP[$cp]=$cs ;; esac; done; vbox_facts "${L[@]}" ;;
    vmx) vmx_facts "${L[@]}" ;; virsh) virsh_facts "${L[@]}" ;; esac
vm_rules "$phase"; printf '%s\n' "${RULE_OUT[@]}" "code=$RULE_CODE"
'''

VM_RULES_PS = r'''
param($hv, $file, $kind, $phase)
. '%s'
Import-HvData; Initialize-State; $G.VmHv = $hv; $script:VF = @{ disc2name = 'deep32-0.1.0-alpha.2-disc2-content.iso' }
$lines = [IO.File]::ReadAllLines($file)
switch ($kind) { 'pve' { Read-PveFacts $lines } 'vbox' { $cap = @{}; foreach ($l in $lines) { if ($l.StartsWith('cap ')) { $p = $l.Split(' '); $cap[$p[1]] = [long]$p[2] } }; Read-VboxFacts $lines $cap } 'vmx' { Read-VmxFacts $lines } 'hyperv' { Read-HyperVFacts $lines } }
Invoke-VmRules $phase
$sb = [System.Text.StringBuilder]::new(); foreach ($r in $RuleOut) { [void]$sb.Append($r + "`n") }; [void]$sb.Append('code=' + $RuleCode + "`n"); [Console]::Out.Write($sb.ToString())
'''


def rules_sh(hv, text, kind, phase):
    with tempfile.TemporaryDirectory(prefix='wizard-rules-') as tmp:
        f = Path(tmp) / 'in.txt'
        f.write_text(text, encoding='utf-8', newline='\n')
        prog = 'DEEP32_WIZARD_LIB=1 source "%s"\n%s' % (posix(ROOT / 'install/deep32-wizard.sh'), VM_RULES_SH)
        r = subprocess.run([str(BASH), '-c', prog, 'bash', hv, posix(f), kind, phase], capture_output=True, text=True,
                           encoding='utf-8', timeout=120, env=wizard_env(LC_ALL='C.UTF-8'))
        assert r.returncode == 0, r.stderr
        return r.stdout


def rules_ps(hv, text, kind, phase):
    with tempfile.TemporaryDirectory(prefix='wizard-rules-') as tmp:
        f = Path(tmp) / 'in.txt'
        f.write_text(text, encoding='utf-8', newline='\n')
        script = Path(tmp) / 'r.ps1'
        script.write_text(VM_RULES_PS % (ROOT / 'install/deep32-wizard.ps1'), encoding='utf-8-sig')
        r = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), hv, str(f), kind, phase],
                           capture_output=True, timeout=120)
        assert r.returncode == 0, r.stderr.decode('utf-8', 'replace')
        return r.stdout.decode('utf-8').replace('\r\n', '\n')


PVE_REFERENCE = (FIXTURES / 'pve-929.conf').read_text(encoding='ascii')
# (what changes in the reference configuration, the phase, the first code the rules report)
PVE_CASES = [
    ([], 'create', ''),
    ([('bios: seabios', 'bios: ovmf')], 'create', 'VM_FIRMWARE_UEFI'),
    ([('vga: std', 'vga: virtio')], 'create', 'VM_DISPLAY_VIRTIO'),
    ([('vga: std', 'vga: type=virtio-gl,memory=64')], 'create', 'VM_DISPLAY_VIRTIO'),
    ([('cores: 4', 'cores: 2')], 'create', 'VM_CPU_LOW'),
    ([('memory: 8192', 'memory: 4096')], 'create', 'VM_MEMORY_LOW'),
    ([('size=200G', 'size=160G')], 'create', 'VM_DISK_SMALL'),
    ([('size=200G', 'size=0.2T')], 'create', ''),
    ([('net0: virtio=', 'net0: rtl8139=')], 'create', 'VM_NIC_MODEL'),
    ([('net0: virtio=', 'net0: e1000=')], 'create', ''),
    ([('ostype: other', 'ostype: l26')], 'create', 'VM_OS_TYPE'),
    ([('cpu: host', 'cpu: x86-64-v2-AES')], 'create', 'VM_CPU_TYPE'),
    ([('scsihw: virtio-scsi-single', 'scsihw: lsi')], 'create', 'VM_DISK_CONTROLLER'),
    ([('boot: order=scsi0', 'boot: order=scsi1')], 'create', 'VM_BOOT_ORDER'),
    ([('scsi0: ssd-lvm:vm-929-disk-0,discard=on,iothread=1,size=1790M,ssd=1\n', '')], 'create', 'VM_INSTALLER_MISSING'),
    ([('bios: seabios\n', ''), ('vga: std\n', '')], 'create', ''),       # Proxmox's defaults: SeaBIOS and std
    ([('boot: order=scsi0', 'boot: order=scsi1'), ('scsihw:', 'sata1: local:iso/deep32-0.1.0-alpha.2-disc2-content.iso,media=cdrom\nscsihw:')], 'after', ''),
    ([('boot: order=scsi0', 'boot: order=scsi1')], 'after', 'VM_CDROM_MISSING'),
    ([('scsihw:', 'sata1: local:iso/deep32-0.1.0-alpha.2-disc2-content.iso,media=cdrom\nscsihw:')], 'after', 'VM_BOOT_ORDER'),
]


def pve_variant(changes):
    text = PVE_REFERENCE
    for old, new in changes:
        assert old in text, old
        text = text.replace(old, new, 1)
    return text


def demo_block(name):
    """A recorded output from the demo file (unix lines; the tests read the same recordings the demo plays)."""
    out, inside, skip = [], False, [False]
    for line in (ROOT / 'install/deep32-wizard-demo.txt').read_text(encoding='utf-8').splitlines():
        if line.startswith('=== '):
            if inside:
                break
            inside = line[4:] == name
            continue
        if not inside or line.startswith('#') or not line:
            continue
        if line in ('.if unix', '.if windows'):
            skip.append(skip[-1] or line != '.if unix')
            continue
        if line == '.endif':
            skip.pop()
            continue
        if not skip[-1]:
            out.append(line)
    return '\n'.join(out) + '\n'


class VmRules(unittest.TestCase):
    """The parsers turn each hypervisor's own output into the same facts, and the rules read them alike in both shells."""

    def both(self, hv, text, kind, phase):
        a = rules_sh(hv, text, kind, phase) if BASH.is_file() else None
        b = rules_ps(hv, text, kind, phase) if (WINDOWS and POWERSHELL.is_file()) else None
        if a is not None and b is not None:
            self.assertEqual(a, b)
        return a if a is not None else b

    def test_proxmox_reference_config(self):
        # The lab VM 929 configuration from WIZARD-HV-DESIGN.md (Proxmox VE 9.1.11; the design names no MAC address,
        # so the fixture's is a placeholder: the model is all the rules read).
        out = self.both('proxmox', PVE_REFERENCE, 'pve', 'create')
        lines = out.splitlines()
        self.assertEqual(lines[-1], 'code=')
        self.assertFalse([l for l in lines if l.startswith('err')], out)
        self.assertIn('ok\tTarget disk\tscsi1, 214.7 GB (214748364800 bytes)\t', lines)
        self.assertIn('ok\tInstaller disk\tscsi0, 1.9 GB\t', lines)
        self.assertEqual(len([l for l in lines if l.startswith('ok')]), 11)

    def test_proxmox_variants(self):
        for changes, phase, code in PVE_CASES:
            with self.subTest(changes=changes, phase=phase):
                out = self.both('proxmox', pve_variant(changes), 'pve', phase)
                self.assertEqual(out.splitlines()[-1], 'code=' + code, out)

    def test_virtualbox_vmware_and_libvirt_recordings(self):
        cap = 'cap /home/you/deep32-downloads/deep32-0.1.0-alpha.2-disc1-workstation-amd64-kvm/installer.vdi 1876951040\n' \
              'cap /home/you/deep32-downloads/deep32-0.1.0-alpha.2-disc1-workstation-amd64-kvm/target.vdi 214748364800\n'
        cases = [('virtualbox', 'vbox', 'vm-vbox-info-1', 'create', 'VM_FIRMWARE_UEFI', cap),
                 ('virtualbox', 'vbox', 'vm-vbox-info-2', 'create', '', cap),
                 ('virtualbox', 'vbox', 'vm-vbox-info-3', 'after', '', cap),
                 ('virtualbox', 'vbox', 'vm-vbox-info-2', 'after', 'VM_BOOT_ORDER', cap),
                 ('vmware', 'vmx', 'vm-vmx-1', 'create', 'VM_MEMORY_LOW', ''),
                 ('vmware', 'vmx', 'vm-vmx-2', 'create', '', ''),
                 ('vmware', 'vmx', 'vm-vmx-3', 'after', '', '')]
        for hv, kind, name, phase, code, extra in cases:
            with self.subTest(name=name, phase=phase):
                out = self.both(hv, demo_block(name) + extra, kind, phase)
                self.assertEqual(out.splitlines()[-1], 'code=' + code, out)
        if BASH.is_file():   # libvirt runs on Linux: the bash wizard reads its XML
            for name, phase, code in (('vm-virsh-xml-1', 'create', 'VM_DISPLAY_VIRTIO'), ('vm-virsh-xml-2', 'create', ''),
                                      ('vm-virsh-xml-3', 'after', '')):
                with self.subTest(name=name):
                    self.assertEqual(rules_sh('kvm', demo_block(name), 'virsh', phase).splitlines()[-1], 'code=' + code)

    @unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
    def test_hyperv_lines(self):
        for name, phase, code in (('vm-hyperv-1', 'create', 'VM_GENERATION'), ('vm-hyperv-2', 'create', ''), ('vm-hyperv-3', 'after', '')):
            with self.subTest(name=name):
                self.assertEqual(rules_ps('hyper-v', demo_block(name), 'hyperv', phase).splitlines()[-1], 'code=' + code)


def plain_guide(shell, answers, os_name):
    env = wizard_env(DEEP32_WIZARD_OS=os_name, TERM='dumb')
    if shell == 'bash':
        cmd = [str(BASH), str(ROOT / 'install/deep32-wizard.sh'), '--demo', '--vm-guide']
    else:
        cmd = [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'install/deep32-wizard.ps1'), '-Demo', '-VmGuide']
    r = subprocess.run(cmd, input=answers, capture_output=True, env=env, timeout=300)
    return r.returncode, r.stdout.decode('utf-8').replace('\r\n', '\n')


class VmPlainTranscripts(unittest.TestCase):
    """The plain guide (--vm-guide without the full screen) prints the same lines in both shells. The kept
    transcripts in tests/wizard/transcripts are the Proxmox path (the API token, the pinned certificate, an error and
    its re-check) and the VirtualBox path (the converted copy, an error, a skip), both against the demo's recordings."""

    def test_transcripts(self):
        shells = [s for s, ok in (('bash', BASH.is_file()), ('powershell', WINDOWS and POWERSHELL.is_file())) if ok]
        for name in ('proxmox', 'virtualbox'):
            answers = (TRANSCRIPTS / ('vm-guide-%s.answers' % name)).read_bytes()
            kept = (TRANSCRIPTS / ('vm-guide-%s.txt' % name)).read_text(encoding='utf-8')
            for shell in shells:
                with self.subTest(path=name, shell=shell):
                    code, out = plain_guide(shell, answers, 'linux')
                    self.assertEqual(code, 0)
                    self.assertEqual(out, kept)

    @unittest.skipUnless(BASH.is_file() and WINDOWS and POWERSHELL.is_file(), 'both shells are needed')
    def test_windows_paths_alike(self):
        for name in ('proxmox', 'virtualbox'):
            answers = (TRANSCRIPTS / ('vm-guide-%s.answers' % name)).read_bytes()
            with self.subTest(path=name):
                a, b = plain_guide('bash', answers, 'windows'), plain_guide('powershell', answers, 'windows')
                self.assertEqual(a, b)
                self.assertIn('C:\\Users\\you\\deep32-downloads', a[1])

    def test_secret_never_printed_or_logged(self):
        if not BASH.is_file():
            self.skipTest('bash is not available')
        answers = (TRANSCRIPTS / 'vm-guide-proxmox.answers').read_bytes()
        secret = 'demo-token-secret'
        self.assertIn(secret.encode(), answers)
        self.assertNotIn(secret, plain_guide('bash', answers, 'linux')[1])
        if WINDOWS and POWERSHELL.is_file():
            self.assertNotIn(secret, plain_guide('powershell', answers, 'linux')[1])


class VmKitFiles(unittest.TestCase):
    """The kit-folder checks against a real folder: the image is only read, the descriptor points at deep32-get's copy."""

    def kit(self, base, hv='kvm'):
        dl = base / 'deep32-downloads'
        img = dl / 'deep32-0.1.0-alpha.2-disc1-workstation-amd64.img'
        dl.mkdir(parents=True)
        data = bytes(range(256)) * 4096     # 1 MiB stands in for disc 1
        img.write_bytes(data)
        kitdir = dl / ('deep32-0.1.0-alpha.2-disc1-workstation-amd64-' + hv)
        kitdir.mkdir()
        (kitdir / 'installer.img').write_bytes(data)
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        (kitdir / 'recipe.json').write_text(json.dumps({'hypervisor': hv, 'installer_bytes': len(data), 'installer_sha256': digest},
                                                       indent=2, sort_keys=True) + '\n', encoding='ascii')
        (kitdir / 'commands.txt').write_text('# stand-in\n', encoding='ascii')
        return dl, img, kitdir, digest

    @unittest.skipUnless(BASH.is_file(), 'bash is not available')
    def test_bash_kit_checks_and_descriptor(self):
        import hashlib
        with tempfile.TemporaryDirectory(prefix='wizard-kit-') as tmp:
            dl, img, kitdir, digest = self.kit(Path(tmp))
            body = ('opt_demo=0; STATE_DIR="%s"; LOG_FILE=; LOG=(); hv_load; vm_os; hv_detect; VM_HV=vmware; hv_index vmware; VHI=$HI; '
                    'kit_find "%s" || echo nokit; vm_vars; VS_ID=(x y z); VS_CHECK=(image copy vmdk); VS_STATE=(p p p); VS_RES=(); VS_CODE=(); '
                    'for VM_I in 0 2; do vm_check; echo "$VM_I ${VS_STATE[VM_I]} ${VS_CODE[VM_I]}"; done') % (posix(tmp), posix(dl))
            prog = 'DEEP32_WIZARD_LIB=1 source "%s"\nDEEP32_WIZARD_HV_FOUND=""\n%s\n' % (posix(ROOT / 'install/deep32-wizard.sh'), body)
            r = subprocess.run([str(BASH), '-c', prog], capture_output=True, text=True, timeout=120, env=wizard_env(DEEP32_WIZARD_OS='linux'))
            self.assertEqual(r.stdout.split('\n')[:2], ['0 ok ', '2 ok '], r.stderr + r.stdout)
            vmdk = (kitdir / 'installer.vmdk').read_text(encoding='ascii')
            self.assertIn('createType="monolithicFlat"', vmdk)
            self.assertIn('RW 2048 FLAT "installer.img" 0', vmdk)
            self.assertEqual(hashlib.sha256(img.read_bytes()).hexdigest(), digest, 'the signed image changed')

    @unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
    def test_powershell_kit_checks_and_descriptor(self):
        import hashlib
        with tempfile.TemporaryDirectory(prefix='wizard-kit-') as tmp:
            dl, img, kitdir, digest = self.kit(Path(tmp), 'hyper-v')
            data = img.read_bytes()
            (kitdir / 'installer.img').unlink()
            (kitdir / 'installer.vhd').write_bytes(data + b'\0' * 512)
            prog = (". '%s'\n$env:DEEP32_WIZARD_HV_FOUND = ''; $script:StateDir = '%s'; $script:LogFile = ''\n"
                    "Initialize-State; $script:F = @{}; Import-HvData; Set-VmOs; Find-Hypervisors; $G.VmHv = 'hyper-v'; $script:Vhi = Get-HvIndex 'hyper-v'\n"
                    "if (-not (Find-Kit '%s')) { 'nokit' }; Set-VmVars\n"
                    "$G.Vs = [System.Collections.Generic.List[object]]::new(); foreach ($c in 'image', 'copy', 'vmdk') { $G.Vs.Add([pscustomobject]@{ Id = $c; Check = $c; State = 'pending'; Res = ''; Code = ''; Look = '' }) }\n"
                    "for ($i = 0; $i -lt 3; $i++) { $G.VmI = $i; Invoke-VmCheck; [Console]::Out.WriteLine(('{0} {1} {2}' -f $i, $G.Vs[$i].State, $G.Vs[$i].Code)) }") % (
                ROOT / 'install/deep32-wizard.ps1', tmp, dl)
            script = Path(tmp) / 't.ps1'
            script.write_text(prog, encoding='utf-8-sig')
            r = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)], capture_output=True, text=True,
                               timeout=120, env=wizard_env(DEEP32_WIZARD_OS='windows'))
            self.assertEqual(r.stdout.splitlines()[:3], ['0 ok ', '1 ok ', '2 ok '], r.stderr + r.stdout)
            vmdk = (kitdir / 'installer.vmdk').read_text(encoding='ascii')
            self.assertIn('RW 2048 FLAT "installer.vhd" 0', vmdk)
            self.assertEqual(hashlib.sha256(img.read_bytes()).hexdigest(), digest, 'the signed image changed')

    def test_no_command_changes_a_vm(self):
        # The wizards run only read-only hypervisor queries (and VBoxManage convertfromraw, which writes a file into the
        # kit folder); every create, start, stop or change is a command in a step, for the person to run.
        sh = (ROOT / 'install/deep32-wizard.sh').read_text(encoding='utf-8')
        sh = sh[sh.index('# 10b.'):sh.index('# 11. Keys.')]
        runs = {c for _, c in re.findall(r'(?m)^[^#\n]*?\bvm_tool (\S+) (\S+)', sh)}
        self.assertEqual(runs, {'config', 'showvminfo', 'showmediuminfo', '-r'})
        self.assertIn('vm_tool virsh-xml -r -c qemu:///system dumpxml', sh)
        self.assertEqual(set(re.findall(r'virsh (-\S+)', sh)), {'-r'})
        self.assertEqual(set(re.findall(r'"\$\{HV_CMD\[HI\]\}" (\S+)', sh)), {'list'})
        self.assertEqual(re.findall(r'"\$vbox" ([a-z]\w*)', sh), ['convertfromraw'])
        ps = (ROOT / 'install/deep32-wizard.ps1').read_text(encoding='utf-8')
        ps = ps[ps.index('# 10b.'):ps.index('# 11. Keys.')]
        self.assertEqual(set(re.findall(r'\b([A-Z][a-z]+)-(?:VM[A-Za-z]*|VHD|NetNat)\b', ps)), {'Get'})
        self.assertEqual(set(re.findall(r"Invoke-VmTool [^@\n]*@\('(\w+)'", ps)), {'showvminfo', 'showmediuminfo'})
        self.assertEqual(re.findall(r"@\('(convertfromraw)'", ps), ['convertfromraw'])


# --- Send disc 2 to a Workstation (--send-disc2 / -SendDisc2) ----------------------------------------------------------
# The wizard serves its verified disc 2 with `tailscale serve` while its screen is open. Nothing here runs a real
# Tailscale or curl: stand-ins on PATH answer `status --json`, `debug prefs`, `serve status --json`, the foreground serve
# (which records its process and sleeps until it is stopped) and its `off` form, and curl answers the self-check.

SEND_ISO = 'deep32-0.1.0-alpha.3-disc2-content.iso'   # the wizard names it from VERSION
SEND_SIZE = 4096
SEND_CODE_RE = re.compile(r'type ([A-Z2-7]{5})-([A-Z2-7]{5})$', re.M)

SEND_SH_TAILSCALE = r'''#!/bin/sh
echo "$*" >> "$FAKE_TS_LOG"
case "$1 $2" in
  "status --json")
    case "${FAKE_TS:-running}" in
      running|nocert)
        cert='["vmhost.tail1234.ts.net"]'; [ "${FAKE_TS:-running}" = nocert ] && cert=null
        printf '{\n  "BackendState": "Running",\n  "TailscaleIPs": ["100.64.0.7"],\n  "Self": {\n    "DNSName": "vmhost.tail1234.ts.net.",\n    "TailscaleIPs": ["100.64.0.7", "fd7a:115c:a1e0::7"]\n  },\n  "CertDomains": %s,\n  "Peer": {\n    "nodekey:1": {"DNSName": "deep32-ws.tail1234.ts.net.", "TailscaleIPs": ["100.64.0.9"]}\n  }\n}\n' "$cert" ;;
      *) printf '{\n  "BackendState": "NeedsLogin"\n}\n' ;;
    esac ;;
  "debug prefs") printf '{\n  "OperatorUser": "%s"\n}\n' "${FAKE_TS_OPERATOR-$(id -un)}" ;;
  "serve status")
    case "${FAKE_SERVE_STATUS:-empty}" in
      busy) echo '{"TCP":{"9447":{"HTTPS":true}},"Web":{"vmhost.tail1234.ts.net:9447":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:3000"}}}}}' ;;
      *) if [ -f "$FAKE_TS_DIR/serving" ]; then cat "$FAKE_TS_DIR/serving"; else echo '{}'; fi ;;
    esac ;;
  "serve --yes")
    case "$*" in *" off") rm -f "$FAKE_TS_DIR/serving"; exit 0 ;; esac
    case "${FAKE_SERVE:-ok}" in
      denied) echo 'Access denied: serve config denied' >&2; exit 1 ;;
      refused) echo 'error: the serve config could not be applied' >&2; exit 1 ;;
    esac
    [ "${FAKE_SERVE_STATUS:-empty}" = left ] && echo "$4" > "$FAKE_TS_DIR/serving"
    echo $$ > "$FAKE_TS_DIR/serve.pid"
    exec sleep 600 ;;
esac
'''
SEND_SH_CURL = r'''#!/bin/sh
echo "$*" >> "$FAKE_CURL_LOG"
case "${FAKE_CURL:-ok}" in
  short) printf 'HTTP/2 200\r\ncontent-length: 5\r\n\r\n' ;;
  *) printf 'HTTP/2 200\r\ncontent-type: application/octet-stream\r\ncontent-length: %s\r\n\r\n' "$FAKE_CURL_LEN" ;;
esac
'''
SEND_CMD_TAILSCALE = '\r\n'.join([
    '@echo off', 'setlocal', '>>"%FAKE_TS_LOG%" echo %*',
    'if "%~1"=="status" goto status',
    'if "%~1 %~2"=="serve status" goto servestatus',
    'echo %* | findstr /c:" off" >nul && (del "%FAKE_TS_DIR%\\serving" 2>nul & exit /b 0)',
    'if "%FAKE_SERVE%"=="denied" (echo must be a Windows local admin to serve a path 1>&2& exit /b 1)',
    'if "%FAKE_SERVE_STATUS%"=="left" (>"%FAKE_TS_DIR%\\serving" echo %*)',
    'ping -n 597 127.0.0.1 >nul', 'exit /b 0',
    ':status',
    'if "%FAKE_TS%"=="out" (echo {"BackendState":"NeedsLogin"}& exit /b 0)',
    'echo {"BackendState":"Running","Self":{"DNSName":"vmhost.tail1234.ts.net.","TailscaleIPs":["100.64.0.7","fd7a:115c:a1e0::7"]},"CertDomains":["vmhost.tail1234.ts.net"]}',
    'exit /b 0',
    ':servestatus',
    'if "%FAKE_SERVE_STATUS%"=="busy" (echo {"TCP":{"9447":{"HTTPS":true}}}& exit /b 0)',
    'if exist "%FAKE_TS_DIR%\\serving" (type "%FAKE_TS_DIR%\\serving") else (echo {})',
    'exit /b 0', ''])
SEND_CMD_CURL = '\r\n'.join([
    '@echo off', '>>"%FAKE_CURL_LOG%" echo %*',
    'if "%FAKE_CURL%"=="short" (echo HTTP/2 200& echo content-length: 5& exit /b 0)',
    'echo HTTP/2 200', 'echo content-length: %FAKE_CURL_LEN%', 'exit /b 0', ''])


def send_sandbox(base, shell):
    """A scratch repository with the wizard, its download folder holding a small disc 2, and the stand-ins."""
    repo, bin_ = base / 'repo', base / 'bin'
    (repo / 'install').mkdir(parents=True)
    script = 'deep32-wizard.sh' if shell == 'bash' else 'deep32-wizard.ps1'
    for name in (script, 'deep32-wizard-errors.tsv', 'deep32-wizard-demo.txt'):
        shutil.copy2(ROOT / 'install' / name, repo / 'install' / name)
    shutil.copy2(ROOT / 'VERSION', repo / 'VERSION')
    (base / 'deep32-downloads').mkdir()
    (base / 'deep32-downloads' / SEND_ISO).write_bytes(b'x' * SEND_SIZE)
    if shell == 'bash':
        write(bin_ / 'tailscale', SEND_SH_TAILSCALE)
        write(bin_ / 'curl', SEND_SH_CURL)
    else:
        write(bin_ / 'tailscale.cmd', SEND_CMD_TAILSCALE)
        write(bin_ / 'curl.cmd', SEND_CMD_CURL)
    return repo, bin_


def send_bash(base, extra=None, keys=None, stdin=b'\n', args=('--send-disc2', '--plain'), timeout=180):
    """Run the bash wizard in the sandbox: plain by default, the full screen with KEYS."""
    repo, bin_ = base / 'repo', base / 'bin'
    env = dict(os.environ, SANDBOX_PATH=msys(bin_) + ':/usr/bin:/bin', HOME=posix(base), FAKE_TS_LOG=posix(base / 'ts.log'),
               FAKE_TS_DIR=posix(base), FAKE_CURL_LOG=posix(base / 'curl.log'), FAKE_CURL_LEN=str(SEND_SIZE),
               DEEP32_WIZARD_OS='linux', DEEP32_WIZARD_TEST_ADMIN='1', TERM='xterm-256color', XDG_STATE_HOME=posix(base / 'state'))
    env.pop('NO_COLOR', None)
    for k in ('DEEP32_WIZARD_FORCE_TTY', 'DEEP32_WIZARD_KEYS', 'DEEP32_WIZARD_COLS', 'DEEP32_WIZARD_ROWS'):
        env.pop(k, None)
    if keys is not None:
        args = ('--send-disc2',)
        env.update(DEEP32_WIZARD_FORCE_TTY='1', DEEP32_WIZARD_COLS='100', DEEP32_WIZARD_ROWS='30', DEEP32_WIZARD_KEYS=','.join(keys))
        stdin = None
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    cmd = [str(BASH), '-c', 'export PATH="$SANDBOX_PATH"; unset SANDBOX_PATH; s=$1; shift; . "$s"', 'bash',
           'install/deep32-wizard.sh'] + list(args)
    if stdin is None:
        return subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    return subprocess.run(cmd, cwd=repo, env=env, input=stdin, capture_output=True, timeout=timeout)


def send_ps(base, extra=None, keys=None, stdin=b'\n', timeout=180):
    repo, bin_ = base / 'repo', base / 'bin'
    env = dict(os.environ, FAKE_TS_LOG=str(base / 'ts.log'), FAKE_TS_DIR=str(base), FAKE_CURL_LOG=str(base / 'curl.log'),
               FAKE_CURL_LEN=str(SEND_SIZE), DEEP32_WIZARD_TEST_ADMIN='1', LOCALAPPDATA=str(base / 'local'))
    env['PATH'] = os.pathsep.join([str(bin_), str(SYSROOT / 'System32'), str(SYSROOT / 'System32/WindowsPowerShell/v1.0'), str(SYSROOT)])
    for k in ('NO_COLOR', 'DEEP32_WIZARD_OS', 'DEEP32_WIZARD_FORCE_TTY', 'DEEP32_WIZARD_KEYS', 'DEEP32_WIZARD_COLS', 'DEEP32_WIZARD_ROWS'):
        env.pop(k, None)
    args = ['-SendDisc2', '-Plain']
    if keys is not None:
        args = ['-SendDisc2']
        env.update(DEEP32_WIZARD_FORCE_TTY='1', DEEP32_WIZARD_COLS='100', DEEP32_WIZARD_ROWS='30', DEEP32_WIZARD_KEYS=','.join(keys))
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    cmd = [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'install\\deep32-wizard.ps1'] + args
    if keys is not None:
        return subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    return subprocess.run(cmd, cwd=repo, env=env, input=stdin, capture_output=True, timeout=timeout)


def bash_alive(pidfile):
    """True when the stand-in serve recorded in PIDFILE still runs (and then it is stopped here)."""
    if not pidfile.exists():
        return False
    pid = pidfile.read_text().strip()
    out = subprocess.run([str(BASH), '-c', 'kill -0 %s 2>/dev/null && { echo alive; kill -KILL %s; }; true' % (pid, pid)],
                         capture_output=True, text=True, timeout=60).stdout
    return 'alive' in out


def ps_leftover_serves():
    """Stand-in serves (ping -n 597) still running after a PowerShell run; they are stopped here."""
    prog = ("$p = @(Get-CimInstance Win32_Process -Filter \"Name='PING.EXE'\" | Where-Object { $_.CommandLine -like '*-n 597 *' }); "
            "foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }; [Console]::Out.Write($p.Count)")
    r = subprocess.run([str(POWERSHELL), '-NoProfile', '-Command', prog], capture_output=True, text=True, timeout=120)
    return int(r.stdout.strip() or '0')


def plain_lines(data):
    return data.decode('utf-8', 'replace').replace('\r\n', '\n').splitlines()


# Shared expectations for the readers both wizards have: the status, the self-check headers, the serve status and
# what a stopped serve said. Each is written once here and compared with both shells.
SEND_STATUS = [
    ('{\n  "BackendState": "Running",\n  "TailscaleIPs": ["100.64.0.7"],\n  "Self": {\n    "DNSName": "vmhost.tail1234.ts.net.",\n'
     '    "TailscaleIPs": ["100.64.0.7", "fd7a:115c:a1e0::7"]\n  },\n  "CertDomains": ["vmhost.tail1234.ts.net"],\n'
     '  "Peer": {\n    "k": {"DNSName": "deep32-ws.tail1234.ts.net.", "TailscaleIPs": ["100.64.0.9"]}\n  }\n}\n',
     'Running|vmhost.tail1234.ts.net|vmhost|100.64.0.7|1'),
    ('{"BackendState":"NeedsLogin"}', 'NeedsLogin||||0'),
    ('{"BackendState":"Running","Self":{"DNSName":"pc.tail9.ts.net","TailscaleIPs":["fd7a::5","100.70.1.2"]},"CertDomains":null,'
     '"Peer":{"a":{"DNSName":"other.tail9.ts.net.","TailscaleIPs":["100.70.1.3"]}}}', 'Running|pc.tail9.ts.net|pc|100.70.1.2|0'),
    ('{"BackendState":"Stopped","Peer":{"a":{"DNSName":"x.tail9.ts.net."}}}', 'Stopped||||0'),
    ("failed to connect to local tailscaled; it doesn't appear to be running", '||||0'),
]
SEND_HEADS = [
    ('HTTP/2 200\r\ncontent-length: 4096\r\n\r\n', '0|HTTP 200 with Content-Length 4096; disc 2 has 4096 bytes'),
    ('HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 0-0/4096\r\nContent-Length: 1\r\n\r\n',
     '0|HTTP 206 for 4096 bytes in all; disc 2 has 4096 bytes'),
    ('HTTP/2 200\r\ncontent-length: 5\r\n\r\n', '1|HTTP 200 with Content-Length 5; disc 2 has 4096 bytes'),
    ('HTTP/2 200\r\n\r\n', '1|HTTP 200 with Content-Length none; disc 2 has 4096 bytes'),
    ('HTTP/2 404\r\ncontent-length: 19\r\n\r\n', '2|HTTP 404'),
    ('curl: (7) Failed to connect to vmhost.tail1234.ts.net port 9447 after 2 ms: Could not connect to server\n',
     '2|curl: (7) Failed to connect to vmhost.tail1234.ts.net port 9447 after 2 ms: Could not connect to server'),
    ('', '2|no answer'),
    ('HTTP/1.1 301 Moved\r\nLocation: /x\r\n\r\nHTTP/2 200\r\ncontent-length: 4096\r\n\r\n',
     '0|HTTP 200 with Content-Length 4096; disc 2 has 4096 bytes'),
]
SEND_BUSY = [
    ('{}', '1|'),
    ('{"TCP":{"443":{"HTTPS":true}},"Web":{"pc.tail9.ts.net:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:8080"}}}}}', '1|'),
    ('{"TCP":{"9447":{"HTTPS":true}}}', '0|Tailscale already serves something else on port 9447'),
    ('{"TCP":{"9447":{"HTTPS":true}},"Web":{"pc.tail9.ts.net:9447":{"Handlers":{"/d32-disc2-AAAAAAAAAA/x.iso":{"Path":"/x.iso"}}}}}',
     '0|Tailscale already serves a disc 2 on port 9447, from another Send disc 2'),
]
SEND_CLASSES = [
    ('Access denied: serve config denied', 'send-needs-root'),
    ("must be root, or be an operator and able to run 'sudo tailscale' to serve a path", 'send-needs-root'),
    ('must be a Windows local admin to serve a path', 'send-needs-root'),
    ('listen tcp 100.64.0.7:9447: bind: address already in use', 'send-port-busy'),
    ('Serve is not enabled on your tailnet. To enable, visit: https://login.tailscale.com/f/serve?node=x', 'send-serve-refused'),
    ('error: something else', 'send-serve-refused'),
]


class SendDisc2Readers(unittest.TestCase):
    """The pieces both wizards read alike: the status, the headers of the self-check, the serve status, the code."""

    def data(self, tmp):
        files = []
        for kind, cases in (('status', SEND_STATUS), ('head', SEND_HEADS), ('busy', SEND_BUSY), ('class', SEND_CLASSES)):
            for i, (text, _) in enumerate(cases):
                f = Path(tmp) / ('%s%d.txt' % (kind, i))
                f.write_bytes(text.encode('utf-8'))
                files.append((kind, f))
        return files

    def expected(self):
        return [want for cases in (SEND_STATUS, SEND_HEADS, SEND_BUSY, SEND_CLASSES) for _, want in cases]

    @unittest.skipUnless(BASH.is_file(), 'bash is not available')
    def test_bash(self):
        with tempfile.TemporaryDirectory(prefix='wizard send lib ') as tmp:
            body = ['DEEP32_WIZARD_LIB=1 source "%s"' % posix(ROOT / 'install/deep32-wizard.sh'), 'opt_demo=0']
            for kind, f in self.data(tmp):
                read = 't=$(cat "%s"); ' % posix(f)
                if kind == 'status':
                    body.append(read + 'send_ts_parse "$t"; echo "$SEND_STATE|$SEND_NAME|$SEND_SHORT|$SEND_IP|$SEND_CERT"')
                elif kind == 'head':
                    body.append(read + 'send_head_check "$t" %d; echo "$?|$SEND_GOT"' % SEND_SIZE)
                elif kind == 'busy':
                    body.append(read + 'SEND_WHY=; send_busy_in "$t"; echo "$?|$SEND_WHY"')
                else:
                    body.append(read + 'send_classify "$t"; echo "$SEND_CLASS"')
            body.append('for i in 1 2 3 4 5 6 7 8; do send_code; send_pretty "$SEND_CODE"; echo "code $SEND_CODE $SEND_SHOWN"; done')
            r = subprocess.run([str(BASH), '-c', '\n'.join(body)], capture_output=True, text=True, encoding='utf-8', timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = r.stdout.splitlines()
            self.assertEqual(lines[:-8], self.expected())
            self.check_codes(lines[-8:])

    @unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
    def test_powershell(self):
        with tempfile.TemporaryDirectory(prefix='wizard send lib ') as tmp:
            body = [". '%s'" % (ROOT / 'install/deep32-wizard.ps1'), 'Initialize-State', '$o = [Console]::Out']
            for kind, f in self.data(tmp):
                read = "$t = [IO.File]::ReadAllText('%s'); " % f
                if kind == 'status':
                    body.append(read + "$s = Read-SendStatus $t; $o.WriteLine(('{0}|{1}|{2}|{3}|{4}' -f $s.State, $s.Name, $s.Short, $s.Ip, [int]$s.Cert))")
                elif kind == 'head':
                    body.append(read + "$h2 = Test-SendHead $t %d; $o.WriteLine(('{0}|{1}' -f $h2[0], $h2[1]))" % SEND_SIZE)
                elif kind == 'busy':
                    body.append(read + "$why = Test-SendBusy $t; $o.WriteLine(('{0}|{1}' -f $(if ($why) { 0 } else { 1 }), $why))")
                else:
                    body.append(read + '$o.WriteLine((Get-SendClass $t))')
            body.append("for ($n = 0; $n -lt 8; $n++) { $c = New-SendCode; $o.WriteLine(('code {0} {1}' -f $c, (Format-SendCode $c))) }")
            script = Path(tmp) / 't.ps1'
            script.write_text('\n'.join(body), encoding='utf-8-sig')
            r = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                               capture_output=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8', 'replace'))
            lines = r.stdout.decode('utf-8').replace('\r\n', '\n').splitlines()
            self.assertEqual(lines[:-8], self.expected())
            self.check_codes(lines[-8:])

    def check_codes(self, lines):
        codes = set()
        for line in lines:
            _, code, shown = line.split(' ')
            self.assertRegex(code, r'^[A-Z2-7]{10}$')
            self.assertEqual(shown, code[:5] + '-' + code[5:])
            codes.add(code)
        self.assertGreater(len(codes), 6, 'codes repeat: they must come from a random source')


@unittest.skipUnless(BASH.is_file() and WINDOWS and POWERSHELL.is_file(), 'both shells are needed')
class SendDisc2DemoParity(unittest.TestCase):
    def test_plain_demo_is_the_same_in_both_wizards(self):
        for os_name, who in (('linux', 'Runs as root'), ('windows', 'Runs as administrator')):
            with self.subTest(os=os_name):
                env = wizard_env(DEEP32_WIZARD_OS=os_name)
                sh = subprocess.run([str(BASH), str(ROOT / 'install/deep32-wizard.sh'), '--demo', '--send-disc2', '--plain'],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
                ps = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                     str(ROOT / 'install/deep32-wizard.ps1'), '-Demo', '-SendDisc2', '-Plain'],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
                self.assertEqual((sh.returncode, ps.returncode), (0, 0), sh.stderr + ps.stderr)
                lines = plain_lines(sh.stdout)
                self.assertEqual(lines, plain_lines(ps.stdout))
                self.assertTrue(all(re.match(r'\[(ok|info|you|fail)\] ', l) for l in lines), lines)
                self.assertIn('[you] On the Workstation, choose Get Disc 2 over the Network, pick vmhost, type R4NDQ-7KX2M', lines)
                self.assertIn('[ok] %s, so Tailscale may serve the file' % who, lines)
                self.assertEqual(lines[-1], '[ok] Sending stopped; the serve is off and the code no longer works')


@unittest.skipUnless(BASH.is_file(), 'bash is not available')
class SendDisc2Bash(unittest.TestCase):
    """--send-disc2 against the stand-ins: what it serves, how it checks itself, and that the serve always ends."""

    def run_case(self, extra=None, prepare=None, **kw):
        tmp = tempfile.mkdtemp(prefix='wizard send sh ')
        self.addCleanup(shutil.rmtree, tmp, True)
        base = Path(tmp)
        send_sandbox(base, 'bash')
        if prepare:
            prepare(base)
        r = send_bash(base, extra, **kw)
        log = (base / 'ts.log').read_text(encoding='utf-8') if (base / 'ts.log').exists() else ''
        curl = (base / 'curl.log').read_text(encoding='utf-8') if (base / 'curl.log').exists() else ''
        self.assertFalse(bash_alive(base / 'serve.pid'), 'the serve outlived the wizard')
        return base, r, plain_lines(r.stdout), log, curl

    def test_sends_this_file_at_its_code_path_and_stops_on_enter(self):
        base, r, lines, log, curl = self.run_case()
        self.assertEqual(r.returncode, 0, r.stderr)
        m = SEND_CODE_RE.search('\n'.join(lines))
        self.assertIsNotNone(m, lines)
        code = m.group(1) + m.group(2)
        path = '/d32-disc2-%s/%s' % (code, SEND_ISO)
        serves = [l for l in log.splitlines() if l.startswith('serve --yes')]
        self.assertEqual(len(serves), 1, log)
        self.assertEqual(serves[0].split(' ')[:4], ['serve', '--yes', '--https=9447', '--set-path=' + path])
        self.assertTrue(serves[0].endswith('/deep32-downloads/' + SEND_ISO), serves[0])
        self.assertNotIn('--bg', log)
        self.assertNotIn('funnel', log)
        self.assertIn('-sS -I --max-time 20 --resolve vmhost.tail1234.ts.net:9447:100.64.0.7 https://vmhost.tail1234.ts.net:9447' + path, curl)
        self.assertEqual(lines[-1], '[ok] Sending stopped; the serve is off and the code no longer works')
        self.assertNotIn(' off', log)   # the serve ended with the wizard's child, so no off was needed

    def test_a_listed_path_is_turned_off_with_the_documented_form(self):
        base, r, lines, log, _ = self.run_case({'FAKE_SERVE_STATUS': 'left'})
        self.assertEqual(r.returncode, 0, r.stderr)
        off = [l for l in log.splitlines() if l.endswith(' off')]
        self.assertEqual(len(off), 1, log)
        self.assertRegex(off[0], r'^serve --yes --https=9447 --set-path=/d32-disc2-[A-Z2-7]{10}/%s off$' % re.escape(SEND_ISO))
        self.assertFalse((base / 'serving').exists())

    def test_refusals_name_their_code_and_start_nothing(self):
        def no_iso(base):
            (base / 'deep32-downloads' / SEND_ISO).unlink()

        def partial(base):
            (base / 'deep32-downloads' / SEND_ISO).rename(base / 'deep32-downloads' / (SEND_ISO + '.download'))

        def wrong_size(base):
            rev = base / 'deep32-downloads/.deep32-v0.1.0-alpha.3' / ('revision-1-' + 'a' * 64)
            rev.mkdir(parents=True)
            data = json.loads(FIXTURE.read_text(encoding='utf-8'))
            (rev / 'MIRRORS.json').write_text(json.dumps(data), encoding='utf-8')   # its disc 2 is 26 bytes

        def no_tailscale(base):
            (base / 'bin/tailscale').unlink()

        cases = [
            ('no disc 2', {}, no_iso, 'send-no-disc2', 'no %s in' % SEND_ISO),
            ('partial disc 2', {}, partial, 'send-no-disc2', 'only its partial download is there'),
            ('size differs from the signed release', {}, wrong_size, 'send-no-disc2', 'the signed release says 26'),
            ('no Tailscale', {}, no_tailscale, 'send-no-tailscale', 'tailscale: command not found'),
            ('signed out', {'FAKE_TS': 'out'}, None, 'send-signed-out', 'Tailscale says: NeedsLogin'),
            ('HTTPS certificates off', {'FAKE_TS': 'nocert'}, None, 'send-serve-refused', 'no HTTPS certificate domain'),
            ('not root, not the operator', {'DEEP32_WIZARD_TEST_ADMIN': None, 'FAKE_TS_OPERATOR': ''}, None, 'send-needs-root',
             'is not the Tailscale operator'),
            ('port taken', {'FAKE_SERVE_STATUS': 'busy'}, None, 'send-port-busy', 'Tailscale already serves something else on port 9447'),
        ]
        for name, extra, prepare, code, said in cases:
            with self.subTest(case=name):
                base, r, lines, log, _ = self.run_case(extra, prepare)
                self.assertEqual(r.returncode, 1, lines)
                self.assertTrue([l for l in lines if l.startswith('[info] ') and said in l], lines)
                fail = [l for l in lines if l.startswith('[fail] ')]
                self.assertEqual(len(fail), 1, lines)
                self.assertTrue(fail[0].endswith('(code: %s)' % code), fail)
                self.assertTrue([l for l in lines if l.startswith('[you] ')], lines)
                self.assertNotIn('serve --yes', log)
                if code == 'send-needs-root':
                    self.assertIn('[you] In the Deep32 folder on this machine, run the send as root: sudo bash install/deep32-wizard.sh --send-disc2', lines)
                    self.assertIn('sudo tailscale set --operator=$USER', '\n'.join(lines))

    def test_an_unknown_argument_is_refused_as_setup_refuses_it(self):
        _, r, lines, log, _ = self.run_case(args=('--send-disc2', '--plain', '--bogus'))
        self.assertEqual(r.returncode, 64)
        self.assertEqual(lines, ['[fail] unknown argument: --bogus (try --help)'])
        self.assertEqual(log, '')

    def test_the_tailscale_operator_may_send(self):
        _, r, lines, log, _ = self.run_case({'DEEP32_WIZARD_TEST_ADMIN': None})
        self.assertEqual(r.returncode, 0, lines)
        self.assertTrue([l for l in lines if l.startswith('[ok] Your user ') and 'operates Tailscale' in l], lines)
        self.assertIn('debug prefs', log)

    def test_a_serve_that_stops_or_answers_wrong_is_explained_and_ended(self):
        for name, extra, code, said in (
                ('serve denied', {'FAKE_SERVE': 'denied'}, 'send-needs-root', 'Access denied: serve config denied'),
                ('serve refused', {'FAKE_SERVE': 'refused'}, 'send-serve-refused', 'error: the serve config could not be applied'),
                ('short answer', {'FAKE_CURL': 'short'}, 'send-self-check', 'HTTP 200 with Content-Length 5; disc 2 has 4096 bytes')):
            with self.subTest(case=name):
                _, r, lines, log, _ = self.run_case(extra)
                self.assertEqual(r.returncode, 1, lines)
                self.assertIn('[info] ' + said, lines)
                self.assertTrue(lines[[i for i, l in enumerate(lines) if l.startswith('[fail]')][0]].endswith('(code: %s)' % code), lines)
                self.assertNotIn('[you] On the Workstation', '\n'.join(lines))
                self.assertIn('serve --yes', log)

    def test_screen_sends_and_enter_stops(self):
        keys = ['until:sending', 'waitms:300', 'snap:sending', 'enter', 'until:sent', 'waitms:200', 'snap:sent', 'q']
        base, r, _, log, _ = self.run_case(keys=keys)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        shots = screens(r.stdout)
        self.assertIn('On the Workstation, choose Get Disc 2 over the Network', shots['sending'])
        self.assertRegex(shots['sending'], r'[A-Z2-7]{5}-[A-Z2-7]{5}')
        self.assertIn('Sending stopped', shots['sent'])
        self.assertEqual(len([l for l in log.splitlines() if l.startswith('serve --yes')]), 1)
        self.assertGreater(r.stdout.rindex(b'\x1b[?1049l'), r.stdout.rindex(b'\x1b[?1049h'))
        state = base / 'state/deep32-setup'
        wlog = next(state.glob('wizard-*.log')).read_text(encoding='utf-8')
        self.assertIn('tailscale serve --yes --https=9447 --set-path=/d32-disc2-', wlog)
        self.assertIn('--- send disc 2: the serve ended', wlog)

    def test_screen_quit_and_crash_end_the_serve(self):
        for name, tail, status in (('quit', ['q', 'right', 'enter'], 0), ('crash', ['crash'], None)):
            with self.subTest(case=name):
                base, r, _, log, _ = self.run_case(keys=['until:sending', 'waitms:300'] + tail)
                if status is not None:
                    self.assertEqual(r.returncode, status, r.stderr[-2000:])
                else:
                    self.assertIn(b'the test asked for an error', r.stderr)
                self.assertTrue((base / 'serve.pid').exists(), log)
                self.assertGreater(r.stdout.rindex(b'\x1b[?1049l'), r.stdout.rindex(b'\x1b[?1049h'))

    def test_screen_explains_a_refusal(self):
        base, r, _, _, _ = self.run_case({'FAKE_SERVE_STATUS': 'busy'}, keys=['until:sendfail', 'waitms:300', 'snap:fail', 'q', 'right', 'enter'])
        self.assertEqual(r.returncode, 1)
        fail = screens(r.stdout)['fail']
        for needle in ('Send disc 2 stopped', 'What the tools said', 'Tailscale already serves something else on port 9447',
                       'code send-port-busy', 'What it means', 'Port 9447, the port Send disc 2 uses', 'What to do', 'Try again'):
            self.assertIn(needle, fail)


@unittest.skipUnless(WINDOWS and POWERSHELL.is_file(), 'Windows PowerShell 5.1 is not available')
class SendDisc2PowerShell(unittest.TestCase):
    def run_case(self, extra=None, prepare=None, **kw):
        tmp = tempfile.mkdtemp(prefix='wizard send ps ')
        self.addCleanup(shutil.rmtree, tmp, True)
        base = Path(tmp)
        send_sandbox(base, 'powershell')
        if prepare:
            prepare(base)
        r = send_ps(base, extra, **kw)
        log = (base / 'ts.log').read_text(encoding='utf-8', errors='replace') if (base / 'ts.log').exists() else ''
        curl = (base / 'curl.log').read_text(encoding='utf-8', errors='replace') if (base / 'curl.log').exists() else ''
        self.assertEqual(ps_leftover_serves(), 0, 'the serve outlived the wizard')
        return base, r, plain_lines(r.stdout), log, curl

    def test_sends_this_file_at_its_code_path_and_stops_on_enter(self):
        base, r, lines, log, curl = self.run_case()
        self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8', 'replace'))
        m = SEND_CODE_RE.search('\n'.join(lines))
        self.assertIsNotNone(m, lines)
        path = '/d32-disc2-%s%s/%s' % (m.group(1), m.group(2), SEND_ISO)
        serves = [l.strip() for l in log.splitlines() if l.startswith('serve --yes')]
        self.assertEqual(len(serves), 1, log)
        self.assertEqual(serves[0].split(' ')[:4], ['serve', '--yes', '--https=9447', '--set-path=' + path])
        self.assertTrue(serves[0].endswith(str(base / 'deep32-downloads' / SEND_ISO) + '"'), serves[0])
        self.assertIn('--resolve vmhost.tail1234.ts.net:9447:100.64.0.7 https://vmhost.tail1234.ts.net:9447' + path, curl)
        self.assertEqual(lines[-1], '[ok] Sending stopped; the serve is off and the code no longer works')

    def test_a_listed_path_is_turned_off(self):
        base, r, lines, log, _ = self.run_case({'FAKE_SERVE_STATUS': 'left'})
        self.assertEqual(r.returncode, 0, lines)
        self.assertEqual(len([l for l in log.splitlines() if l.strip().endswith(' off')]), 1, log)
        self.assertFalse((base / 'serving').exists())

    def test_refusals(self):
        def no_iso(base):
            (base / 'deep32-downloads' / SEND_ISO).unlink()

        cases = [('no disc 2', {}, no_iso, 'send-no-disc2'), ('signed out', {'FAKE_TS': 'out'}, None, 'send-signed-out'),
                 ('port taken', {'FAKE_SERVE_STATUS': 'busy'}, None, 'send-port-busy'),
                 ('serve denied', {'FAKE_SERVE': 'denied'}, None, 'send-needs-root'),
                 ('short answer', {'FAKE_CURL': 'short'}, None, 'send-self-check')]
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            cases.append(('not an Administrator console', {'DEEP32_WIZARD_TEST_ADMIN': None}, None, 'send-needs-root'))
        for name, extra, prepare, code in cases:
            with self.subTest(case=name):
                _, r, lines, log, _ = self.run_case(extra, prepare)
                self.assertEqual(r.returncode, 1, lines)
                fail = [l for l in lines if l.startswith('[fail] ')]
                self.assertEqual(len(fail), 1, lines)
                self.assertTrue(fail[0].endswith('(code: %s)' % code), fail)
                if name == 'not an Administrator console':
                    self.assertIn('[you] On this PC, open PowerShell as administrator (Start, type PowerShell, right-click, Run as administrator)', lines)
                    self.assertNotIn('serve --yes', log)

    def test_screen_sends_enter_stops_and_quit_and_crash_end_the_serve(self):
        for name, keys, status in (
                ('enter', ['until:sending', 'waitms:300', 'snap:sending', 'enter', 'until:sent', 'waitms:200', 'snap:sent', 'q'], 0),
                ('quit', ['until:sending', 'waitms:300', 'q', 'right', 'enter'], 0),
                ('crash', ['until:sending', 'waitms:300', 'crash'], None)):
            with self.subTest(case=name):
                _, r, _, log, _ = self.run_case(keys=keys)
                if status is None:
                    self.assertNotEqual(r.returncode, 0)
                else:
                    self.assertEqual(r.returncode, status, r.stderr.decode('utf-8', 'replace')[-2000:])
                self.assertEqual(len([l for l in log.splitlines() if l.startswith('serve --yes')]), 1, log)
                self.assertGreater(r.stdout.rindex(b'\x1b[?1049l'), r.stdout.rindex(b'\x1b[?1049h'))
                if name == 'enter':
                    shots = screens(r.stdout)
                    self.assertIn('On the Workstation, choose Get Disc 2 over the Network', shots['sending'])
                    self.assertIn('Sending stopped', shots['sent'])


if __name__ == '__main__':
    unittest.main()
