import ast
import unittest
from support import SHIP

class TestCompat(unittest.TestCase):
    def test_python_312_and_ascii(self):
        for path in sorted(SHIP.rglob('*.py')):
            source = path.read_bytes().decode('ascii')
            ast.parse(source,feature_version=(3,12))

    def test_no_long_dashes(self):
        for path in sorted(p for p in SHIP.rglob('*') if p.is_file()):
            raw = path.read_bytes()
            self.assertNotIn(chr(0x2013).encode(),raw)
            self.assertNotIn(chr(0x2014).encode(),raw)
