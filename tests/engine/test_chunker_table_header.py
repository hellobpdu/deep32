"""chunking.table_header_repeat (default off): a " | " table the chunker splits repeats its caption line and header row
at the start of each continuation chunk; the chunk says how long that context is (metadata "context_chars")."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import itertools
import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_core import chunker
from rag_core.chunker import (
    Unit, build_drafts, chunking_settings, estimate_tokens, header_row, table_caption, table_context,
)

_ids = itertools.count(1)
PATH = ("Security Commands", "show radius table attributes")
V3 = dict(policy="v3", target_tokens=100, hard_tokens=150, overlap_tokens=20, min_tokens=40,
          atomic_hard_tokens=300)
V2 = dict(policy="v2", target_tokens=100, hard_tokens=150, overlap_tokens=20, atomic_hard_tokens=300)
CAPTION = "Table 39. show radius table attributes Field Descriptions"
HEADER = "Field | Description"
ROWS = [f"Attribute-{i} | The value of attribute {i} on the system. The format is String." for i in range(60)]


def unit(text, kind="prose", atomic=False, path=PATH):
    return Unit(kind=kind, text=text, page=1, block_id=f"p0001-b{next(_ids):04d}", heading_path=tuple(path),
                atomic=atomic, source_type="table" if kind == "table" else "text")


def table(rows=ROWS, header=HEADER):
    return unit("\n".join(([header] if header else []) + list(rows)), kind="table", atomic=True)


def doc(caption=CAPTION, header=HEADER, rows=ROWS):
    units = [unit("To display the RADIUS attributes, use the show radius table attributes command.")]
    if caption:
        units.append(unit(caption))
    return units + [table(rows, header), unit("The following example shows the attributes.")]


def table_drafts(drafts):
    return [d for d in drafts if any(u.kind == "table" for u in d.units)]


class TableHeaderRepeatTests(unittest.TestCase):

    def test_off_by_default(self):
        units = doc()
        for params in (V3, V2):
            with self.subTest(policy=params["policy"]):
                default = build_drafts(units, **params)
                off = build_drafts(units, **params, table_header_repeat=False)
                self.assertEqual([(d.text, [u.metadata for u in d.units]) for d in default],
                                 [(d.text, [u.metadata for u in d.units]) for d in off])
                self.assertFalse(any("context_chars" in u.metadata for d in default for u in d.units))
                # the continuation chunks start with a row: what the layer is for
                self.assertTrue(table_drafts(default)[1].text.startswith("Attribute-"))
        with mock.patch.dict(os.environ, {"RAG_ROOT": "", "RAG_CHUNKING_CONFIG": ""}):
            self.assertEqual(chunking_settings(), {"table_header_repeat": False})     # config/chunking.json

    def test_each_continuation_chunk_starts_with_the_caption_and_header_row(self):
        context = f"{CAPTION}\n{HEADER}\n"
        for params, cap in ((V3, 150), (V2, 300)):
            with self.subTest(policy=params["policy"]):
                drafts = build_drafts(doc(), **params, table_header_repeat=True)
                tables = table_drafts(drafts)
                self.assertGreater(len(tables), 2)
                self.assertTrue(tables[0].text.split("\n\n")[-1].startswith(HEADER))     # the first piece as before
                for d in tables[1:]:
                    self.assertTrue(d.text.startswith(context), d.text[:120])
                    self.assertEqual(d.units[0].metadata["context_chars"], len(context))
                    self.assertEqual(chunker._context_meta(d), {"context_chars": len(context)})
                self.assertEqual(chunker._context_meta(tables[0]), {})
                self.assertLessEqual(max(d.tokens for d in drafts), cap)
                # every row once, in order, the context left out
                rows = [line for d in tables for line in d.text[d.units[0].metadata.get("context_chars", 0):]
                        .split("\n") if line.startswith("Attribute-")]
                self.assertEqual(rows, ROWS)

    def test_what_is_repeated(self):
        first_row = "User-Name | The name of the user on the system. The format is String."
        for kw, context in (({"caption": None}, [HEADER]),                          # no caption line
                            ({"header": first_row}, [CAPTION]),                   # a row first: no header row
                            ({"caption": "Procedure:"}, [HEADER])):                # no caption either
            with self.subTest(**kw):
                units = doc(**kw)
                index = next(i for i, u in enumerate(units) if u.kind == "table")
                self.assertEqual(table_context(units, index), context)
                drafts = build_drafts(units, **V3, table_header_repeat=True)
                self.assertTrue(table_drafts(drafts)[1].text.startswith("\n".join(context) + "\nAttribute-"))
        # neither: the table splits as before
        units = doc(caption=None, header=None)
        self.assertEqual(table_context(units, 1), [])
        self.assertEqual([d.text for d in build_drafts(units, **V3, table_header_repeat=True)],
                         [d.text for d in build_drafts(units, **V3)])

    def test_a_table_within_the_cap_or_with_a_long_context_splits_as_before(self):
        for n in (5, 10):               # 10 rows: within the cap, not within the cap less the context
            small = doc(rows=ROWS[:n])
            self.assertLessEqual(estimate_tokens("\n".join([HEADER, *ROWS[:n]])), 150)
            self.assertEqual([d.text for d in build_drafts(small, **V3, table_header_repeat=True)],
                             [d.text for d in build_drafts(small, **V3)])
        self.assertGreater(estimate_tokens("\n".join([CAPTION, HEADER, *ROWS[:10]])), 150)
        # a context of more than a quarter of the cap (150 // 4 tokens) is not repeated
        wide = " | ".join(f"Column {i}" for i in range(14))
        self.assertTrue(header_row(wide))
        self.assertGreater(estimate_tokens(f"{CAPTION}\n{wide}") * chunker.CONTEXT_SHARE, 150)
        units = doc(header=wide)
        self.assertEqual([d.text for d in build_drafts(units, **V3, table_header_repeat=True)],
                         [d.text for d in build_drafts(units, **V3)])

    def test_caption_and_header_row_shapes(self):
        tbl = table()
        for text, caption in ((CAPTION, CAPTION),
                              ("show ip mroute Field Descriptions", "show ip mroute Field Descriptions"),
                              ("Table 4. Verified and supported cipher suites on Cisco ISE, Release 3.1 and later "
                               "with all of the patches applied", "Table 4. Verified and supported cipher suites "
                               "on Cisco ISE, Release 3.1 and later with all of the patches applied"),
                              ("Procedure:", None),
                              ("This table lists the TCP ports that NMAP uses for OS scanning.", None),
                              ("Before you begin, review the following list of the terms used in this table", None),
                              ("two\nlines", None)):
            with self.subTest(text=text):
                self.assertEqual(table_caption(unit(text), tbl), caption)
        self.assertIsNone(table_caption(unit(CAPTION, path=("Other",)), tbl))             # another section
        self.assertIsNone(table_caption(unit("Router# show run", kind="cli-config", atomic=True), tbl))
        self.assertIsNone(table_caption(None, tbl))
        for line in ("Field | Description", "Feature Name | Release | Feature Information",
                     "Category | Protocol | Type | WKP/IP Protocol | Description | Syntax | Cisco IOS XE Release"):
            self.assertTrue(header_row(line), line)
        for line in ("protocol | Name or number of an Internet protocol. It can be one of the keywords ahp, esp",
                     "Step 1 | In the Cisco ISE GUI, click the Menu icon and choose Policy > Policy Sets",
                     "1 | 3 | 4 | 6 | 7 | 9", "IE2000 | IE3000 | √ | √", "Field Descriptions",
                     "Note | Before you begin, ensure that the underlay network links are configured."):
            self.assertFalse(header_row(line), line)

    def test_a_continuation_piece_always_leads_its_chunk(self):
        # what lets the chunk carry context_chars: the piece before fills its own chunk up to the cap
        rng = random.Random(5)
        for _ in range(300):
            n_rows = rng.randint(1, 80)
            rows = [f"Attribute-{i} | " + " ".join(f"w{j}" for j in range(rng.randint(1, 25))) for i in range(n_rows)]
            units = ([unit(" ".join(f"p{j}" for j in range(rng.randint(0, 120))) or "Intro.")]
                     + [unit(CAPTION), table(rows)]
                     + [unit(" ".join(f"q{j}" for j in range(rng.randint(1, 120))))])
            for params in (V3, V2):
                drafts = build_drafts(units, **params, table_header_repeat=True)
                for d in drafts:
                    self.assertFalse(any("context_chars" in u.metadata for u in d.units[1:]))
                    meta = chunker._context_meta(d)
                    if meta:
                        self.assertTrue(d.text.startswith(f"{CAPTION}\n{HEADER}\n"))
                self.assertLessEqual(max(d.tokens for d in drafts), 150 if params is V3 else 300)
                text = "\n".join(d.text for d in drafts)
                self.assertTrue(all(row in text for row in rows))

    def test_a_continuation_piece_leads_its_chunk_after_a_row_longer_than_the_cap(self):
        # a row longer than the cap is cut by words, and the short end of it fits one chunk with the next piece or
        # joins the chunk before (under min_tokens): the caption and header row stood inside a chunk
        # (cisco-tn-ikev2-debug in corpus-v5, debug output in the cells: 5 chunks)
        context = f"{CAPTION}\n{HEADER}\n"
        rng = random.Random(11)
        for _ in range(300):
            rows = [f"Attribute-{i} | " + " ".join(
                        f"w{j}" for j in range(rng.randint(140, 400) if rng.random() < 0.15 else rng.randint(1, 25)))
                    for i in range(rng.randint(2, 30))]
            tbl = table(rows)
            units = [unit("Intro " + " ".join(f"p{j}" for j in range(rng.randint(0, 60)))), unit(CAPTION), tbl,
                     unit(" ".join(f"q{j}" for j in range(rng.randint(1, 60))))]
            for params in (V3, V2):
                drafts = build_drafts(units, **params, table_header_repeat=True)
                for d in drafts:
                    self.assertFalse(any("context_chars" in u.metadata for u in d.units[1:]))
                    self.assertEqual(d.text.count(context), 1 if chunker._context_meta(d) else 0)
                self.assertLessEqual(max(d.tokens for d in drafts), 150 if params is V3 else 300)
                # every word of the table once, in order, the contexts left out
                pieces = [u for d in drafts for u in d.units if u.kind == "table"]
                self.assertEqual(" ".join(u.text[u.metadata.get("context_chars", 0):] for u in pieces).split(),
                                 tbl.text.split())


class ChunkingSettingsTests(unittest.TestCase):

    def test_the_root_overlay_and_the_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config").mkdir()
            (root / "config" / "chunking.json").write_text(json.dumps({"table_header_repeat": True}))
            arm = root / "arm.json"
            arm.write_text(json.dumps({"table_header_repeat": False}))
            with mock.patch.dict(os.environ, {"RAG_ROOT": str(root), "RAG_CHUNKING_CONFIG": ""}):
                self.assertEqual(chunking_settings(), {"table_header_repeat": True})
            with mock.patch.dict(os.environ, {"RAG_ROOT": str(root), "RAG_CHUNKING_CONFIG": str(arm)}):
                self.assertEqual(chunking_settings(), {"table_header_repeat": False})
            arm.write_text(json.dumps({"table_header_repeat": "yes"}))
            with mock.patch.dict(os.environ, {"RAG_ROOT": "", "RAG_CHUNKING_CONFIG": str(arm)}), \
                    self.assertRaisesRegex(ValueError, "true or false"):
                chunking_settings()


if __name__ == "__main__":
    unittest.main()
