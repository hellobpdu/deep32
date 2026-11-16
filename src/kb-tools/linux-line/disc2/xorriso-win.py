#!/usr/bin/env python3
"""xorriso-win.py: run MSYS2's xorriso with Windows paths rewritten to MSYS form (lead, 2026-11-16).

MSYS2 xorriso resolves "F:/x" as a path relative to its working directory. This wrapper rewrites every argument
that is, or ends after "=" in, a drive-letter absolute path ("F:/x" or "F:\\x") to "/f/x", passes everything else
unchanged, and returns xorriso's exit code. Used as disc2-config xorriso_argv[0..2]; no other behaviour.
"""
import re
import subprocess
import sys

XORRISO = 'C:/msys64/usr/bin/xorriso.exe'
DRIVE = re.compile(r'^(?P<pre>[^=]*=)?(?P<drive>[A-Za-z]):[/\\](?P<rest>.*)$', re.S)


def convert(arg):
    m = DRIVE.match(arg)
    if not m:
        return arg
    return (m['pre'] or '') + '/' + m['drive'].lower() + '/' + m['rest'].replace('\\', '/')


if __name__ == '__main__':
    sys.exit(subprocess.run([XORRISO] + [convert(a) for a in sys.argv[1:]]).returncode)
