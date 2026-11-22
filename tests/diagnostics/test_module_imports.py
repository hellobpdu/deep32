"""Every deep32_diag module imports on the running Python (CI runs 3.12; the Workstation runs 3.14).

The headend doctor failed to import on 3.12 and 3.13 until alpha.2's cb3b871; this keeps that class of break visible.
"""
import importlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'src' / 'diagnostics' / 'deep32_diag'
sys.path.insert(0, str(PACKAGE.parent))


class ModuleImports(unittest.TestCase):
    def test_every_module_imports(self):
        # Every file, not pkgutil.walk_packages: platform/ has no __init__.py, and walk_packages skips such a folder.
        names = sorted('.'.join(('deep32_diag',) + path.relative_to(PACKAGE).with_suffix('').parts)
                       for path in PACKAGE.rglob('*.py') if path.stem not in ('__init__', '__main__'))
        self.assertGreaterEqual(len(names), 23)
        for name in names:
            with self.subTest(module=name):
                importlib.import_module(name)


if __name__ == '__main__':
    unittest.main()
