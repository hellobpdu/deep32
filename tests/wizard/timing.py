"""Time the PowerShell wizard: start-up, first frame, one demo step and per-frame cost (standard library only).

    python3 -I -B tests/wizard/timing.py [--wizard PATH] [--cols 200 --rows 60] [--console]

Without --console the wizard writes to a pipe, as in the screenshot captures. With --console it runs in its own
hidden console window (conhost), so frame writes go through the console's VT parser as they do for a person; the
wizard's DEEP32_WIZARD_PROFILE file gives the per-frame build and write times there. Only the demo runs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
KEYS = 'snap:intro,enter,until:checks,snap:checks,waitms:1500,snap:idle,q,right,enter'


def measure(wizard=None, cols=100, rows=30, console=False, keys=KEYS):
    wizard = Path(wizard or ROOT / 'install/deep32-wizard.ps1')
    home = Path(tempfile.mkdtemp(prefix='wizard-timing-'))
    prof = home / 'profile.txt'
    env = dict(os.environ, DEEP32_WIZARD_FORCE_TTY='1', DEEP32_WIZARD_COLS=str(cols), DEEP32_WIZARD_ROWS=str(rows),
               DEEP32_WIZARD_KEYS=keys, DEEP32_WIZARD_PROFILE=str(prof), LOCALAPPDATA=str(home), DEEP32_WIZARD_OS='windows')
    env.pop('NO_COLOR', None)
    env.pop('WT_SESSION', None)
    cmd = [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(wizard), '-Demo']
    marks = {}
    t0 = time.perf_counter()
    if console:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # hidden: conhost still parses every byte
        line = 'mode con: cols=%d lines=%d && %s' % (cols, rows, subprocess.list2cmdline(cmd))
        p = subprocess.Popen(['cmd.exe', '/c', line], env=env, startupinfo=si, creationflags=subprocess.CREATE_NEW_CONSOLE)
        p.wait(timeout=300)
        total = time.perf_counter() - t0
    else:
        p = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        buf = b''
        while True:
            chunk = p.stdout.read1(65536) if hasattr(p.stdout, 'read1') else p.stdout.read(4096)
            if not chunk:
                break
            now = time.perf_counter() - t0
            if not buf:
                marks['first-byte'] = now
            buf += chunk
            for name in ('intro', 'checks', 'idle'):
                if name not in marks and (b'7777;snap;' + name.encode()) in buf:
                    marks[name] = now
        p.wait(timeout=300)
        total = time.perf_counter() - t0
    frames, profile = [], {}
    if prof.exists():
        for line in prof.read_text(encoding='utf-8').splitlines():
            parts = line.split()
            if parts[0] == 'frame':
                frames.append((float(parts[3]), float(parts[5])))
            else:
                profile.setdefault(' '.join(parts[:-1]), float(parts[-1].replace(',', '')))
    build = sorted(f[0] for f in frames) or [0]
    write = sorted(f[1] for f in frames) or [0]
    return {
        'size': '%dx%d' % (cols, rows), 'console': console, 'total_s': round(total, 2),
        'marks_s': {k: round(v, 2) for k, v in marks.items()},
        'profile_ms': profile,
        'frames': len(frames),
        'build_ms_median': round(build[len(build) // 2], 1), 'build_ms_max': round(build[-1], 1),
        'write_ms_median': round(write[len(write) // 2], 1), 'write_ms_max': round(write[-1], 1),
        'builds': [f[0] for f in frames[1:]],   # the first frame aside: it carries PowerShell's first-call compilation
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--wizard')
    ap.add_argument('--cols', type=int, default=100)
    ap.add_argument('--rows', type=int, default=30)
    ap.add_argument('--console', action='store_true')
    args = ap.parse_args()
    print(json.dumps(measure(args.wizard, args.cols, args.rows, args.console), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
