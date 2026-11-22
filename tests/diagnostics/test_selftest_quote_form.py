"""The selftest scorer compares spans in the engine's quotation-check form (rag_core.reader.canonical).

Reads both engine copies' reader.py as text through ast; imports nothing from the engine.
"""
import ast
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "diagnostics"))
from deep32_diag.selftest_contract import _CANON_DROP, _QUOTE_CHARS, quote_form

READERS = (ROOT / "src/engine/src/rag_core/reader.py",
           ROOT / "src/gpu-pack/containers/builder/engine/rag/core/src/rag_core/reader.py")


def engine_tables(path):
    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            if node.targets[0].id == "QUOTE_CHARS":
                found["QUOTE_CHARS"] = str.maketrans(ast.literal_eval(node.value.args[0]))
            elif node.targets[0].id == "CANON_DROP":
                found["CANON_DROP"] = ast.literal_eval(node.value.args[0])
    return found


class QuoteFormTests(unittest.TestCase):
    def test_tables_equal_both_engine_copies(self):
        for path in READERS:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                tables = engine_tables(path)
                self.assertEqual(tables["QUOTE_CHARS"], _QUOTE_CHARS)
                self.assertEqual(tables["CANON_DROP"], _CANON_DROP.pattern)
                self.assertEqual(_CANON_DROP.flags & re.ASCII, 0)

    def test_examples(self):
        self.assertEqual(quote_form("The Hold Time MUST be either zero or at least\n   three seconds."),
                         "TheHoldTimeMUSTbeeitherzerooratleastthreeseconds.")
        self.assertEqual(quote_form("\u201cquoted\u201d \u2018x\u2019 a\u2013b a\u00a0b re-\nfresh"),
                         '"quoted"\'x\'ababrefresh')
        self.assertEqual(quote_form("- \n"), "")


if __name__ == "__main__":
    unittest.main()
