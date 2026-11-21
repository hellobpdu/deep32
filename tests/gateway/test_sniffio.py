"""skip_absent_sniffio: httpcore's per-lock `import sniffio` fails at once instead of searching sys.path each time."""

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import asyncio
import importlib.util
import sys
import unittest
from unittest import mock

from rag_edge import Core, skip_absent_sniffio


class RecordingFinder:
    """A meta path finder that only records which modules the import system searched for."""

    def __init__(self):
        self.searched = []

    def find_spec(self, name, path=None, target=None):
        self.searched.append(name)
        return None


class SniffioTest(unittest.TestCase):
    def test_absent_sniffio_is_not_searched_for_again(self):
        with mock.patch.dict(sys.modules), mock.patch.object(importlib.util, "find_spec", return_value=None):
            sys.modules.pop("sniffio", None)
            self.assertTrue(skip_absent_sniffio())
            finder = RecordingFinder()
            with mock.patch.object(sys, "meta_path", [finder, *sys.meta_path]):
                for _ in range(3):
                    with self.assertRaises(ModuleNotFoundError):
                        import sniffio  # noqa: F401
            self.assertEqual(finder.searched, [])
            self.assertFalse(skip_absent_sniffio())  # idempotent

    def test_httpcore_still_runs_on_asyncio(self):
        from httpcore._synchronization import current_async_library
        with mock.patch.dict(sys.modules), mock.patch.object(importlib.util, "find_spec", return_value=None):
            sys.modules.pop("sniffio", None)
            skip_absent_sniffio()
            self.assertEqual(current_async_library(), "asyncio")

    def test_an_installed_sniffio_is_left_alone(self):
        with mock.patch.dict(sys.modules), mock.patch.object(importlib.util, "find_spec", return_value=object()):
            sys.modules.pop("sniffio", None)
            self.assertFalse(skip_absent_sniffio())
            self.assertNotIn("sniffio", sys.modules)

    def test_core_sets_it_up(self):
        with mock.patch.dict(sys.modules), mock.patch.object(importlib.util, "find_spec", return_value=None):
            sys.modules.pop("sniffio", None)
            core = Core("http://core.internal.test:8765", "t" * 40)
            asyncio.run(core.aclose())
            self.assertIn("sniffio", sys.modules)
            self.assertIsNone(sys.modules["sniffio"])


if __name__ == "__main__":
    unittest.main()
