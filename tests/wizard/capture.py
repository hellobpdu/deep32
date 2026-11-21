"""Drive a setup wizard's --demo with a key script at a fixed size and keep its screens (standard library only).

The wizard writes its full-screen output to a file instead of a terminal (DEEP32_WIZARD_FORCE_TTY=1), takes its
size from DEEP32_WIZARD_COLS/ROWS and its keys from DEEP32_WIZARD_KEYS, and marks each `snap:NAME` in the stream.
vtscreen.py replays that stream and writes NAME.txt and NAME.html for each mark.

    python3 -I -B tests/wizard/capture.py --shell bash --script forge --theme dark OUTDIR
    python3 -I -B tests/wizard/capture.py --shell powershell --script workstation --cols 80 --rows 24 OUTDIR

Only the demo runs here: no network, no Docker, no Tailscale. On Windows bash means Git for Windows' bash by its
full path, never a bare `bash` (that can start WSL).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vtscreen  # noqa: E402

GIT_BASH = Path(r'C:\Program Files\Git\bin\bash.exe')
POWERSHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
DIGEST = '3f2a9c417be021d5c0de86b19a4471f2e53b0c9d8a6e1f37b24c95d0e8a1f6c3'

SCRIPTS = {
    # Every person-step of the Forge: the check, a fix with its question, the Tailscale sign-in, the role, the
    # settings, the download, the plan digest, the install and the pairing code.
    'forge': ['waitms:700', 'snap:intro', 'enter', 'until:checks', 'waitms:300', 'snap:checks', 'enter', 'until:ask',
              'snap:ask', 'y', 'until:signin', 'wait:2.5', 'snap:signin', 'until:checks', 'waitms:300',
              'snap:checks-ready', 'enter', 'until:parts', 'right', 'waitms:100', 'snap:parts', 'enter', 'until:role',
              'snap:role', 'enter', 'until:form', 'snap:form', 'enter',
              'until:runintro', 'snap:runintro', 'enter', 'until:download', 'wait:12.5', 'snap:download', 'until:plan',
              'waitms:300', 'snap:plan', 'text:' + DIGEST[:22], 'char:0', 'waitms:100', 'snap:plan-typo', 'bs',
              'text:' + DIGEST[22:], 'waitms:200', 'snap:plan-typed', 'enter', 'until:install', 'wait:3',
              'snap:install', 'until:pair', 'waitms:600', 'snap:pair', 'enter', 'until:done', 'waitms:200',
              'snap:done', '?', 'snap:help', 'esc', 'l', 'snap:log', 'esc', 'n', 'until:checks', 'waitms:300',
              'snap:continue', 'enter', 'until:role', 'enter', 'until:form', 'enter', 'until:runintro', 'enter',
              'until:done', 'waitms:300', 'snap:next-agent', 'q'],
    # The Workstation host: a download that stops half way, the failure screen, the retry that resumes, the VM guide's
    # first screen (put off with Later), done, Send disc 2 from the Done screen (S) and back.
    'workstation': ['waitms:700', 'enter', 'until:checks', 'right', 'enter', 'until:parts', 'left', 'down', 'right',
                    'down', 'right', 'waitms:100', 'snap:parts-ws', 'enter', 'until:role', 'snap:role-ws', 'enter',
                    'until:form', 'enter', 'until:checks', 'waitms:200', 'enter', 'until:form', 'snap:form-ws', 'enter',
                    'until:runintro', 'snap:runintro-ws', 'enter',
                    'until:download', 'wait:6', 'snap:download-ws', 'until:fail', 'waitms:300', 'snap:fail', 'enter',
                    'until:download', 'wait:4', 'snap:retry', 'until:hv', 'waitms:300', 'snap:hv-after-role', 'right',
                    'enter', 'until:done', 'waitms:300', 'snap:done-ws',
                    's', 'until:sending', 'waitms:300', 'snap:send-ws', 'enter', 'until:sent', 'waitms:200', 'snap:sent-ws',
                    'esc', 'until:done', 'waitms:200',
                    'pgdn', 'pgdn', 'pgdn', 'waitms:200', 'snap:done-ws-more', 'size:72x20', 'waitms:200', 'snap:small', 'q'],
    # The agent PC: settings, the hand-over to Connect, done.
    'headend': ['waitms:700', 'enter', 'until:checks', 'right', 'enter', 'until:parts', 'right', 'down', 'right', 'right',
                'right', 'waitms:100', 'snap:parts-headend', 'enter', 'until:role', 'enter', 'until:form',
                'snap:form-headend', 'enter', 'until:checks', 'waitms:200', 'enter', 'until:form', 'enter',
                'until:runintro', 'snap:runintro-headend', 'enter', 'until:done',
                'waitms:300', 'snap:done-headend', 'q'],
    # The Workstation VM guide (--vm-guide): the hypervisor, the network, Tailscale's notes, the settings, then each kind
    # of step: a kit check, a confirmation, the Proxmox API with its pinned certificate and secret, an error and its
    # re-check, a skip, the tailnet wait, the summary and Done.
    'vm': ['waitms:700', 'snap:vm-hv', 'up', 'up', 'up', 'waitms:100', 'snap:vm-hv-proxmox', 'enter', 'until:net',
           'waitms:200', 'snap:vm-net', 'enter', 'until:tsnote', 'snap:vm-tsnote', 'enter', 'until:vmform', 'down', 'down',
           'down', 'down', 'down', 'down', 'text:192.168.1.10', 'down', 'text:root@pam!deep32-audit', 'snap:vm-form', 'enter',
           'until:vmstep', 'snap:vm-step-image', 'enter', 'waitms:200', 'snap:vm-step-ok', 'enter', 'waitms:100', 'enter',
           'until:vmconfirm', 'snap:vm-confirm', 'y', 'waitms:100', 'snap:vm-confirmed', 'enter', 'waitms:100', 'enter',
           'until:vmpin', 'snap:vm-pin', 'y', 'until:vmsecret', 'text:demo-token-secret', 'snap:vm-secret', 'enter',
           'waitms:300', 'snap:vm-error', 'enter', 'waitms:300', 'snap:vm-create-ok', 'enter', 'waitms:100', 's',
           'waitms:100', 'snap:vm-skipped', 'enter', 'waitms:100', 'enter', 'waitms:200', 'enter', 'waitms:100', 'enter',
           'waitms:400', 'snap:vm-wait', 'wait:2.5', 'snap:vm-joined', 'enter', 'waitms:100', 'enter', 'until:vmconfirm',
           'y', 'waitms:100', 'enter', 'until:vmsum', 'waitms:100', 'snap:vm-sum', 'enter', 'until:done', 'waitms:200',
           'snap:vm-done', 'q'],
    # Send disc 2 (--send-disc2): the checks, the code, the log and the quit question over it, then Enter stops.
    'send': ['waitms:600', 'snap:send-checking', 'until:sending', 'waitms:300', 'snap:send-code', 'l', 'waitms:100',
             'snap:send-log', 'esc', 'q', 'waitms:100', 'snap:send-quit', 'esc', 'enter', 'until:sent', 'waitms:200',
             'snap:send-stopped', 'q'],
}

ARGS = {'vm': (['--vm-guide'], ['-VmGuide']),   # scripts that start somewhere other than the first screen
        'send': (['--send-disc2'], ['-SendDisc2'])}


# On Windows the alpha.2 Forge is by hand: its run shows the two commands and the role then continues elsewhere.
# The Workstation host there pairs the two endings: a same-machine continue (the agent PC) and a hand-off (GPU PC).
SCRIPTS_PS = {
    'forge': ['waitms:700', 'snap:intro', 'enter', 'until:checks', 'waitms:300', 'snap:checks', 'enter', 'until:ask',
              'snap:ask', 'y', 'until:signin', 'wait:2.5', 'snap:signin', 'until:checks', 'waitms:300',
              'snap:checks-ready', 'enter', 'until:parts', 'right', 'waitms:100', 'snap:parts', 'enter', 'until:role',
              'up', 'up', 'snap:role', 'enter', 'until:form', 'snap:form', 'enter', 'until:runintro', 'waitms:200', 'snap:byhand', 'pgdn', 'waitms:200',
              'snap:byhand-more', '?', 'snap:help', 'esc', 'l',
              'snap:log', 'esc', 'q', 'right', 'enter'],
    'workstation': ['waitms:700', 'enter', 'until:checks', 'right', 'enter', 'until:parts', 'left', 'down', 'right',
                    'waitms:100', 'snap:parts-ws', 'enter', 'until:role', 'snap:role-ws', 'enter',
                    'until:form', 'enter', 'until:checks', 'waitms:200', 'enter', 'until:form', 'snap:form-ws', 'enter',
                    'until:runintro', 'snap:runintro-ws', 'enter', 'until:download', 'wait:6', 'snap:download-ws',
                    'until:fail', 'waitms:300', 'snap:fail', 'enter', 'until:download', 'wait:4', 'snap:retry',
                    'until:hv', 'waitms:300', 'snap:hv-after-role', 'right', 'enter', 'until:done', 'waitms:300', 'snap:done-ws',
                    's', 'until:sending', 'waitms:300', 'snap:send-ws', 'enter', 'until:sent', 'waitms:200', 'snap:sent-ws',
                    'esc', 'until:done', 'waitms:200',
                    'pgdn', 'pgdn', 'pgdn', 'waitms:200', 'snap:done-ws-more', 'n', 'until:checks', 'waitms:300', 'snap:continue',
                    'enter', 'until:role', 'enter', 'until:form', 'enter', 'until:runintro', 'enter', 'until:done',
                    'waitms:300', 'snap:next-agent', 'size:72x20', 'waitms:200', 'snap:small', 'q'],
}


def run(shell, script, theme, cols, rows, outdir, color='truecolor', glyphs=None, prefix=''):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix='wizard-home-'))
    env = dict(os.environ, DEEP32_WIZARD_FORCE_TTY='1', DEEP32_WIZARD_COLS=str(cols), DEEP32_WIZARD_ROWS=str(rows),
               DEEP32_WIZARD_THEME=theme, DEEP32_WIZARD_COLOR=color,
               DEEP32_WIZARD_KEYS=','.join((SCRIPTS_PS if shell == 'powershell' else {}).get(script, SCRIPTS[script])),
               TERM='xterm-256color', HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / 'AppData/Local'),
               XDG_STATE_HOME=str(home / '.local/state'))
    env.pop('NO_COLOR', None)
    if glyphs:
        env['DEEP32_WIZARD_GLYPHS'] = glyphs
    if shell == 'bash':
        cmd = [str(GIT_BASH), str(ROOT / 'install/deep32-wizard.sh'), '--demo'] + ARGS.get(script, ([], []))[0]
    else:
        env['DEEP32_WIZARD_OS'] = 'windows'
        cmd = [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'install/deep32-wizard.ps1'), '-Demo'] + ARGS.get(script, ([], []))[1]
    capture = outdir / (prefix + script + '.ansi')
    with capture.open('wb') as out:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.PIPE, env=env, timeout=600)
    data = capture.read_bytes().decode('utf-8', errors='replace')
    screen = vtscreen.replay(data, cols, rows)
    title = ('bash  install/deep32-wizard.sh --demo' if shell == 'bash'
             else 'Windows PowerShell  install\\deep32-wizard.ps1 -Demo')
    names = []
    for name, text, grid in screen.snaps:
        (outdir / (prefix + name + '.html')).write_text(vtscreen.to_html(grid, theme, title), encoding='utf-8')
        (outdir / (prefix + name + '.txt')).write_text('\n'.join(text) + '\n', encoding='utf-8')
        names.append(prefix + name)
    return result.returncode, result.stderr.decode('utf-8', errors='replace'), names


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('outdir')
    ap.add_argument('--shell', choices=('bash', 'powershell'), default='bash')
    ap.add_argument('--script', choices=sorted(SCRIPTS), default='forge')
    ap.add_argument('--theme', choices=('dark', 'light'), default='dark')
    ap.add_argument('--cols', type=int, default=100)
    ap.add_argument('--rows', type=int, default=30)
    ap.add_argument('--color', choices=('truecolor', '256', '16'), default='truecolor')
    ap.add_argument('--glyphs', choices=('rich', 'safe', 'ascii'))
    ap.add_argument('--prefix', default='')
    args = ap.parse_args()
    code, err, names = run(args.shell, args.script, args.theme, args.cols, args.rows, args.outdir, args.color,
                           args.glyphs, args.prefix)
    print('exit', code, 'snaps', ' '.join(names))
    if err.strip():
        print(err[-3000:], file=sys.stderr)
    return 0 if code == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
