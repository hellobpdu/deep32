#!/usr/bin/env python3
"""Both disc 1 ramdisks must get the power-off patch, not only the TEST one.

Until 0.1.0-alpha.2 r15, mkimage-ship.sh applied install.sub-halt.diff inside `if [ $v = test ]`, so the guided
medium kept OpenBSD's `exec reboot` and came back to the installer after a finished install.
"""
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'src/workstation/ship/tools/mkimage-ship.sh'


class MkimagePowerOff(unittest.TestCase):
    def test_halt_patch_is_applied_before_the_test_only_branch(self):
        lines = SCRIPT.read_text(encoding='ascii').splitlines()
        patch = [i for i, line in enumerate(lines) if 'install.sub-halt.diff' in line and 'patch' in line]
        branch = [i for i, line in enumerate(lines) if line.strip() == 'if [ $v = test ]; then']
        self.assertEqual(len(patch), 1, patch)
        self.assertEqual(len(branch), 1, branch)
        self.assertLess(patch[0], branch[0], 'the halt patch must apply to both ramdisks')
        self.assertTrue(any("grep -q 'd32-halt-after-install'" in line for line in lines[patch[0]:branch[0]]))

    def test_script_has_unix_line_endings(self):
        self.assertNotIn(b'\r', SCRIPT.read_bytes())


if __name__ == '__main__':
    unittest.main()
