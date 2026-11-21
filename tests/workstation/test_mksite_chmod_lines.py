#!/usr/bin/env python3
"""Run every top-level chmod line of mksite-ship.sh against a synthetic site tree.

A malformed line (for example a broken line continuation that turns into an extra
argument) fails here instead of at disc 1 stage 80.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'src/workstation/ship/tools/mksite-ship.sh'
# On Windows use Git's bash by full path: a bare 'bash' can resolve to WSL.
BASH = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else (shutil.which('bash') or '/bin/sh')


def chmod_lines(text):
    """Top-level 'chmod MODE $st/...' commands, joined across line continuations."""
    joined = re.sub(r'\\\n\s*', ' ', text)
    return [line.strip() for line in joined.splitlines()
            if re.match(r'^chmod [0-7]{3,4} ', line) and '$st/' in line]


class MksiteChmodLines(unittest.TestCase):
    def test_every_chmod_line_runs_on_a_tree_holding_its_targets(self):
        lines = chmod_lines(SCRIPT.read_text(encoding='ascii'))
        self.assertTrue(any('appmanager/C/Deep32/D32Pairing' in line for line in lines))
        with tempfile.TemporaryDirectory(prefix='mksite-chmod-') as tmp:
            st = Path(tmp)
            for line in lines:
                for token in line.split()[2:]:
                    self.assertTrue(token.startswith(('$st/', '"$st/')), f'unexpected argument {token!r} in: {line}')
                    rel = token.strip('"')[len('$st/'):]
                    target = st / rel.replace('*', 'sample')
                    if rel.endswith('/*.sh'):
                        target = st / rel.replace('*.sh', 'sample.sh')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b'')
            script = 'set -eu\nst=' + st.as_posix() + '\n' + '\n'.join(lines) + '\n'
            result = subprocess.run([BASH, '-c', script], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
