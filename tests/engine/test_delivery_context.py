"""What a hit can carry besides its text (WP-E E5): reader.covered_titles (the headings of the sections a merged
passage covers, from chunk_spans.covered_json) and reader.table_context (the caption and header row above a window
that starts inside a pipe table). WP-D D9 puts them on hits behind evidence.covered_titles and evidence.table_context
(tests/engine/test_delivery_fields.py); this module tests the functions on synthetic text, and the real merged
passage of the fixture through the whole path. Serving engine only: the builder's copy has no such functions."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import tempfile
import unittest
from pathlib import Path

from rag_core import reader

ENGINE_ONLY = unittest.skipUnless(engine_target.NAME == "engine", "E5's functions are in src/engine only")

WIDGETS = """# Widgets
Table 3. Widget states
State | Meaning
up | The widget passes frames.
down | The widget drops frames.

Step 1 | Enable the widget.
Step 2 | Verify the widget.
speed | The widget speed in frames per second, from 1 to 1000. The default is 10.
"""


def source(text):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "widgets.txt"
    path.write_bytes(text.encode("utf-8"))
    made = reader.SourceFile(path, "cisco")
    tmp.cleanup()
    return made


def sec(section_id, heading):
    return reader.Sec(section_id, "a", 0, 1, section_id.split("#")[-1], heading, None, 1, 1, 0)


@ENGINE_ONLY
class TableContextTests(unittest.TestCase):
    def test_the_rule(self):
        src = source(WIDGETS)
        self.assertEqual(reader.table_context(src, 5), {"caption": "Table 3. Widget states", "header": "State | Meaning"})
        self.assertIsNone(reader.table_context(src, 3))      # the window starts at the header
        self.assertIsNone(reader.table_context(src, 2))      # no pipes
        self.assertIsNone(reader.table_context(src, 8))      # walk stops at the blank line; line 7 is a step row
        self.assertIsNone(reader.table_context(src, 9))      # the same walk: a data row of a headerless table
        self.assertEqual(reader.table_context(src, 5, floor=4),
                         {"caption": None, "header": "up | The widget passes frames."})

    def test_a_sentence_is_not_a_header(self):
        src = source(WIDGETS.replace("up | The widget passes frames.",
                                     "up | The widget passes frames. It also counts them."))
        self.assertIsNone(reader.table_context(src, 5, floor=4))

    def test_the_walk_cap(self):
        src = source("Name | Value\n" + "".join(f"row{i} | {i}\n" for i in range(2001)) + "last | x\n")
        self.assertIsNone(reader.table_context(src, 2003))
        self.assertEqual(reader.table_context(src, 2001)["header"], "Name | Value")   # 2,000 rows walked: found

    def test_pipes_inside_braces_are_one_cell(self):
        src = source("[no] anyconnect modules {none | value string}\n[no] anyconnect modules {none | value string}\n")
        self.assertIsNone(reader.table_context(src, 2))

    def test_out_of_range(self):
        src = source(WIDGETS)
        self.assertIsNone(reader.table_context(src, 0))
        self.assertIsNone(reader.table_context(src, 99))


@ENGINE_ONLY
class CoveredTitlesTests(unittest.TestCase):
    def test_the_rule(self):
        sections = {"a#x": sec("a#x", "X"), "a#y": sec("a#y", "Y"), "a#n": sec("a#n", None), "a#b": sec("a#b", "  ")}
        self.assertEqual(reader.covered_titles(["a#x", "a#y"], sections), ["X", "Y"])
        self.assertIsNone(reader.covered_titles(["a#x"], sections))
        self.assertEqual(reader.covered_titles(["a#x", "a#n", "a#b", "a#missing", "a#y"], sections), ["X", "Y"])
        self.assertIsNone(reader.covered_titles(["a#n", "a#b"], sections))
        self.assertIsNone(reader.covered_titles([], sections))


@ENGINE_ONLY
class ChunkRowsTests(unittest.TestCase):
    def test_every_row_says_what_it_covers(self):
        from rag_core.db import connect
        from rag_core.structure import index_root

        from v2_root import build_root
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_root(Path(tmp))
            conn = connect(paths.sqlite / "rag.db")
            try:
                ids = [r[0] for r in conn.execute("SELECT chunk_id FROM chunks")]
                self.assertTrue(all("covered" not in row for row in reader.chunk_rows(conn, ids).values()))
            finally:
                conn.close()
            index_root(paths)
            conn = connect(paths.sqlite / "rag.db")
            try:
                rows = reader.chunk_rows(conn, ids)
                self.assertTrue(rows)
                self.assertTrue(all(isinstance(row["covered"], list) for row in rows.values()))
                corp = reader.RootReader(paths)
                merged = [row for row in rows.values() if len(row["covered"]) >= 2]
                self.assertTrue(merged)
                for row in merged:
                    titles = corp.covered_titles(row["covered"])
                    want = [corp.sections[s].heading for s in row["covered"]
                            if s in corp.sections and (corp.sections[s].heading or "").strip()]
                    self.assertEqual(titles, want or None)
            finally:
                conn.close()


@ENGINE_ONLY
class ServedTests(unittest.TestCase):
    """The whole path (WP-D D9 with E5): a root served with evidence.covered_titles on gives each hit of a merged
    passage its covered titles, and no other hit the key."""

    @classmethod
    def setUpClass(cls):
        from test_api_v2 import AUTH, RootClient
        cls.auth = AUTH
        cls.root = RootClient(retrieval={"evidence": {"covered_titles": True, "table_context": True}})

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def test_merged_passages_carry_their_titles(self):
        from rag_core.db import connect
        r = self.root.client.post("/v2/search", json={"query": "NSSA translator Type-7 LSA", "limit": 30,
                                                      "mode": "bm25"}, headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        hits = r.json()["hits"]
        conn = connect(self.root.paths.sqlite / "rag.db")
        try:
            rows = reader.chunk_rows(conn, [h["id"] for h in hits])
        finally:
            conn.close()
        corp = reader.RootReader(self.root.paths)
        seen = 0
        for hit in hits:
            want = corp.covered_titles(rows[hit["id"]]["covered"])
            if want is None:
                self.assertNotIn("covered_titles", hit)
            else:
                self.assertEqual(hit["covered_titles"], want)
                seen += 1
        self.assertGreater(seen, 0)


if __name__ == "__main__":
    unittest.main()
