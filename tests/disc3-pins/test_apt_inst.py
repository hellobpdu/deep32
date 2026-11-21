"""test_apt_inst.py: the apt-get -s 'Inst' line parser (INST) against the line forms seen on VM 901."""
import importlib.util
import os
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get('D32_RESOLVER', HERE.parents[1] / 'src' / 'build' / 'disc3-pins' / 'resolve-pins.py'))
SPEC = importlib.util.spec_from_file_location('apt_inst_resolver', SOURCE)
resolver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)


class AptInst(unittest.TestCase):
    def parse(self, line):
        m = resolver.INST.match(line)
        return m.groups() if m else None

    def test_plain_line(self):
        self.assertEqual(self.parse('Inst zlib1g (1:1.3.dfsg-3.1ubuntu2.1 Ubuntu:24.04/noble-updates [amd64])'),
                         ('zlib1g', '1:1.3.dfsg-3.1ubuntu2.1', 'amd64'))

    def test_two_suites_and_trailing_brackets(self):
        # the exact line that stopped apt/closure on 901 at 2026-11-15T08:23:51Z
        line = ('Inst libc6 (2.39-0ubuntu8.12 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64]) []')
        self.assertEqual(self.parse(line), ('libc6', '2.39-0ubuntu8.12', 'amd64'))

    def test_upgrade_form_with_old_version(self):
        self.assertEqual(self.parse('Inst libc6 [2.39-0ubuntu8.4] (2.39-0ubuntu8.12 Ubuntu:24.04/noble-updates [amd64])'),
                         ('libc6', '2.39-0ubuntu8.12', 'amd64'))

    def test_trailing_brackets_with_content(self):
        self.assertEqual(self.parse('Inst libgcc-s1 (14.2.0-4ubuntu2~24.04 Ubuntu:24.04/noble-updates [amd64]) [libc6:amd64 ]'),
                         ('libgcc-s1', '14.2.0-4ubuntu2~24.04', 'amd64'))

    def test_garbage_after_line_refused(self):
        self.assertIsNone(self.parse('Inst libc6 (2.39 Ubuntu:24.04/noble [amd64]) trailing words'))
        self.assertIsNone(self.parse('Conf libc6 (2.39 Ubuntu:24.04/noble [amd64])'))


if __name__ == '__main__':
    unittest.main()
