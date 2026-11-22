"""0.1.0-alpha.3, WP-D item D1: retrieval.json evidence.tier1_chars (rag_core.api_v2.evidence_tier1_chars,
HitBuilder.widen). The hits delivered with the request's full window are delivered with a wider one; which hits
there are, their order, the folds and also_in are decided on the request's own window, as without the setting.

Unset or null, every answer is the one before: the bodies pinned from 4f4fe78 (tests/data/fold_off_4f4fe78.json,
through fold_root.alpha2_form). Set, the same pinned bodies still come back, because none of fold_root.REQUESTS asks
for a window from 2400 to the setting (they ask for 200 to 1200, or 4000). The fixture here adds guides with a
section of 6,000 characters, where a window of 2400 and one of 3200 differ."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import re
import unittest
from unittest import mock

from rag_core import api_v2
from rag_core.api_v2 import DEEP_HEAD, evidence_tier1_chars
from rag_core.db import connect
from rag_core.reader import query_terms

import test_fold_identical
from test_api_v2 import AUTH, RootClient
from v2_root import add_document

ROWS = 60
MARKED = (10, 26, 42)       # the rows that hold the query's words: three passages 16 rows apart
VARIANT_ROW = 34            # the one row in which two sibling guides differ: past the 2400 window of row 10,
                            # inside its 3200 window
QUERY = "NSSA translator election marker"
WIDE = {"evidence": {"tier1_chars": 3200}}
SIBLINGS = ("wide-179-guide", "wide-261-guide")        # 17.9 and 26.1 (structure.infer_release)
OTHERS = ("wide-a-guide", "wide-b-guide", "wide-c-guide", "wide-d-guide")
DOCS = SIBLINGS + OTHERS


def wide_text(name, own_rows=False):
    """A guide of one section of ROWS rows of about 100 characters. The rows of MARKED hold the query's words. Two
    siblings differ only in row VARIANT_ROW; with own_rows every row is the guide's own."""
    tag = name.split("-")[1]
    lines = [f"# Wide guide {name}", "## Long translator section"]
    for n in range(1, ROWS + 1):
        who = tag if own_rows or n == VARIANT_ROW else "any"
        marker = " NSSA translator election marker" if n in MARKED else ""
        lines.append(f"Row {n:02d} for {who}:{marker} " + " ".join(f"r{n}x{j}{who if own_rows else ''}" for j in range(11)) + ".")
    return "\n".join(lines + [""])


def prepare(paths, kb_src):
    for name in SIBLINGS:
        add_document(paths, name, "cisco", wide_text(name), f"Wide guide {name}")
    for name in OTHERS:
        add_document(paths, name, "cisco", wide_text(name, own_rows=True), f"Wide guide {name}")


def span(local):
    name, first, last = re.fullmatch(r"(.+):(\d+)-(\d+)", local).groups()
    return name, int(first), int(last)


class UnsetIsTheBodyBefore(test_fold_identical.OffTests):
    """evidence null: the bodies 4f4fe78 sent."""
    retrieval = {"evidence": None}


class NullIsTheBodyBefore(test_fold_identical.OffTests):
    retrieval = {"evidence": {"tier1_chars": None}}


class SetLeavesOtherWindowSizesAlone(test_fold_identical.OffTests):
    """Set to 3200: a request for fewer than 2400 characters, or for 4000, is answered as before (every shape of
    fold_root.REQUESTS is one of these)."""
    retrieval = WIDE


class SettingTests(unittest.TestCase):

    def test_values(self):
        for config in ({}, {"evidence": None}, {"evidence": {}}, {"evidence": {"tier1_chars": None}}):
            self.assertIsNone(evidence_tier1_chars(config))
        for chars in (2400, 3200, 4000):
            self.assertEqual(evidence_tier1_chars({"evidence": {"tier1_chars": chars}}), chars)

    def test_other_values_are_refused(self):
        for bad in (3200, "3200", [], {"tier1_chars": 2399}, {"tier1_chars": 4001}, {"tier1_chars": 3200.0},
                    {"tier1_chars": True}, {"tier1_chars": "3200"}, {"tier1": 3200}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "evidence"):
                evidence_tier1_chars({"evidence": bad})

    def test_the_start_refuses_a_bad_value(self):
        with self.assertRaisesRegex(ValueError, "tier1_chars"):
            RootClient(retrieval={"evidence": {"tier1_chars": 1200}})


class WideTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=prepare, retrieval={"fold_identical": True})
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def post(self, path, body, wide=None):
        with mock.patch.dict(self.orch.config, wide or {}):
            r = self.c.post(path, json=body, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def pair(self, path, body, setting=WIDE):
        """(the answer without the setting, the answer with it)."""
        return self.post(path, body), self.post(path, body, setting)

    def assert_same_but_text_and_local(self, off, on, widened):
        """The answers are equal but for "text" and "local" of the first `widened` hits, whose text is longer
        and, in this fixture, shows more lines of the same file around the same ones."""
        self.assertEqual({k: v for k, v in on.items() if k not in ("hits", "timings_ms")},
                         {k: v for k, v in off.items() if k not in ("hits", "timings_ms")})
        self.assertEqual(len(on["hits"]), len(off["hits"]))
        for n, (a, b) in enumerate(zip(off["hits"], on["hits"])):
            self.assertEqual(list(b), list(a))
            if n >= widened:
                self.assertEqual(b, a, n)
                continue
            self.assertEqual({k: v for k, v in b.items() if k not in ("text", "local")},
                             {k: v for k, v in a.items() if k not in ("text", "local")}, n)
            (file_a, first_a, last_a), (file_b, first_b, last_b) = span(a["local"]), span(b["local"])
            self.assertEqual(file_b, file_a)
            self.assertLessEqual(first_b, first_a)
            self.assertGreaterEqual(last_b, last_a)
            self.assertGreater(len(b["text"]), len(a["text"]))
            self.assertIn(a["text"], b["text"])

    def expected_window(self, hit, terms, chars):
        """reader.evidence for the hit's chunk at `chars`: (text, local), computed beside the API."""
        corp = api_v2.root_reader(self.c.app.state)
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            row = conn.execute("SELECT line_start, line_end FROM chunk_spans WHERE chunk_id = ?",
                               (hit["id"],)).fetchone()
        ev = corp.evidence(corp.docs[hit["doc_key"]], (row[0], row[1]), terms, chars)
        return ev["text"], ev["local"]

    def test_the_fixture_is_one_where_the_window_size_decides_the_hits(self):
        # the reason for the second pass: asking for 3200 characters outright changes which passages are hits
        body = {"query": QUERY, "filters": {"docs": [OTHERS[0]]}, "limit": 8}
        at = {m: [h["id"] for h in self.post("/v2/search", {**body, "max_chars": m})["hits"]] for m in (2400, 3200)}
        self.assertEqual((len(at[2400]), len(at[3200])), (3, 2))

    def test_search_delivers_the_wider_window_of_the_same_hits(self):
        body = {"query": QUERY, "filters": {"docs": list(OTHERS)}, "limit": 8, "max_chars": 2400}
        off, on = self.pair("/v2/search", body)
        self.assertEqual(len(off["hits"]), 8)
        self.assert_same_but_text_and_local(off, on, widened=8)
        terms = query_terms(QUERY)
        for a, b in zip(off["hits"], on["hits"]):
            self.assertEqual((a["text"], a["local"]), self.expected_window(a, terms, 2400))
            self.assertEqual((b["text"], b["local"]), self.expected_window(b, terms, 3200))
            self.assertTrue(2400 < len(b["text"]) <= 3200, len(b["text"]))
        # two hits of one guide now show some of the same lines: the drop rule ran on the 2400 windows
        by_file = {}
        for hit in on["hits"]:
            name, first, last = span(hit["local"])
            by_file.setdefault(name, []).append((first, last))
        self.assertTrue(any(a[0] <= b[1] and b[0] <= a[1] and api_v2._overlaps((n, *a), (n, *b))
                            for n, spans in by_file.items() for a in spans for b in spans if a != b))

    def test_the_request_s_own_size_decides(self):
        body = {"query": QUERY, "filters": {"docs": list(OTHERS)}, "limit": 8}
        for m, widened in ((200, 0), (1200, 0), (2399, 0), (2400, 8), (2800, 8), (3200, 0), (4000, 0)):
            with self.subTest(max_chars=m):
                off, on = self.pair("/v2/search", {**body, "max_chars": m})
                if widened:
                    self.assert_same_but_text_and_local(off, on, widened)
                else:
                    self.assertEqual(on["hits"], off["hits"])
        off, on = self.pair("/v2/search", body)                        # the engine's default, 1200
        self.assertEqual(on["hits"], off["hits"])
        # one character below the setting the pass still runs: the window is the 3200 one (here the same lines fit)
        on = self.post("/v2/search", {**body, "max_chars": 3199}, WIDE)
        for hit in on["hits"]:
            self.assertEqual((hit["text"], hit["local"]), self.expected_window(hit, query_terms(QUERY), 3200))
        off, on = self.pair("/v2/search", {**body, "max_chars": 2400}, {"evidence": {"tier1_chars": 4000}})
        self.assertTrue(all(3200 < len(h["text"]) <= 4000 for h in on["hits"]))
        self.assert_same_but_text_and_local(off, on, 8)

    def test_limit_30_widens_every_hit(self):
        body = {"query": QUERY, "filters": {"docs": list(OTHERS)}, "limit": 30, "max_chars": 2400}
        off, on = self.pair("/v2/search", body)
        self.assertEqual(len(off["hits"]), 12)                          # 4 guides, 3 passages each
        self.assert_same_but_text_and_local(off, on, widened=12)

    def test_deep_widens_tier_1_only(self):
        body = {"query": QUERY, "filters": {"docs": list(OTHERS)}, "deep": True, "max_chars": 2400}
        off, on = self.pair("/v2/search", body)
        self.assertEqual([h["tier"] for h in off["hits"]], [1] * 8 + [2] * 4)
        self.assert_same_but_text_and_local(off, on, widened=DEEP_HEAD)
        self.assertTrue(all(len(h["text"]) <= 800 for h in on["hits"][DEEP_HEAD:]))
        # tier 1 is still the limit 8 answer, widened alike
        today = self.post("/v2/search", {**body, "deep": False, "limit": 8}, WIDE)
        self.assertEqual([{k: v for k, v in h.items() if k != "tier"} for h in on["hits"][:8]], today["hits"])

    def test_search_many_delivers_the_wider_window_of_the_same_hits(self):
        queries = [{"text": QUERY, "mode": "bm25"}, {"text": "Row 26 election", "mode": "bm25"}]
        for extra, widened in (({"limit": 8}, 8), ({"deep": True}, DEEP_HEAD), ({"limit": 8, "max_chars": 500}, 0)):
            with self.subTest(extra=extra):
                body = {"queries": queries, "filters": {"docs": list(OTHERS)}, "max_chars": 2400, **extra}
                off, on = self.pair("/v2/search_many", body)
                self.assertGreaterEqual(len(off["hits"]), 8)
                if widened:
                    self.assert_same_but_text_and_local(off, on, widened)
                else:
                    self.assertEqual(on["hits"], off["hits"])
        # each window is centred with the terms of the queries that found the hit, as its 2400 window was
        body = {"queries": queries, "filters": {"docs": list(OTHERS)}, "max_chars": 2400, "limit": 8}
        on = self.post("/v2/search_many", body, WIDE)
        for hit in on["hits"]:
            terms = list(dict.fromkeys(t for i in hit["per_query_ranks"] for t in query_terms(queries[int(i)]["text"])))
            self.assertEqual((hit["text"], hit["local"]), self.expected_window(hit, terms, 3200))

    def test_a_folded_copy_stays_folded_and_the_hit_is_widened_from_its_own_lines(self):
        body = {"query": QUERY, "filters": {"docs": list(SIBLINGS)}, "limit": 8, "max_chars": 2400}
        off, on = self.pair("/v2/search", body)
        self.assert_same_but_text_and_local(off, on, widened=len(off["hits"]))
        first = on["hits"][0]
        (copy,) = first["also_in"]                              # the sibling's passage of rows 9-10: same window
        self.assertEqual({first["doc_key"], copy["doc_key"]}, set(SIBLINGS))
        self.assertEqual(off["hits"][0]["also_in"], first["also_in"])
        own, other = first["doc_key"].split("-")[1], copy["doc_key"].split("-")[1]
        # the 2400 window is the same text in both guides; the 3200 window reaches the row in which they differ
        self.assertNotIn(f"Row {VARIANT_ROW} for", off["hits"][0]["text"])
        self.assertIn(f"Row {VARIANT_ROW} for {own}:", first["text"])
        self.assertNotIn(f"for {other}:", first["text"])
        self.assertTrue(first["local"].startswith(first["doc_key"] + ".txt:"))

    def test_a_hit_shown_as_a_pinned_copy_is_widened_as_that_copy(self):
        # words of row 10 alone: the two siblings' passages of rows 9-10 are the only candidates, so no other hit
        # of the pinned guide stands in the way of the swap (prefer refuses a copy whose lines overlap a hit's)
        QUERY = "r10x0 r10x1 r10x2"
        body = {"query": QUERY, "filters": {"docs": list(SIBLINGS)}, "limit": 8, "max_chars": 2400}
        plain = self.post("/v2/search", body)["hits"][0]
        pinned_release = plain["also_in"][0]["release"]
        body["prefer_release"] = [pinned_release]
        off, on = self.pair("/v2/search", body)
        self.assert_same_but_text_and_local(off, on, widened=len(off["hits"]))
        shown = on["hits"][0]
        self.assertEqual((shown["release"], shown["doc_key"]), (pinned_release, plain["also_in"][0]["doc_key"]))
        self.assertEqual(shown["also_in"][0]["id"], plain["id"])
        tag = shown["doc_key"].split("-")[1]
        self.assertIn(f"Row {VARIANT_ROW} for {tag}:", shown["text"])              # the pinned guide's own row
        self.assertTrue(shown["local"].startswith(shown["doc_key"] + ".txt:"))
        self.assertEqual((shown["text"], shown["local"]), self.expected_window(shown, query_terms(QUERY), 3200))
        # the quotation an agent takes from the delivered text verifies against the document the hit cites
        quote = next(line for line in shown["text"].split("\n") if line.startswith(f"Row {VARIANT_ROW} "))
        r = self.c.post("/v2/verify_quote", json={"ref": shown["section_id"], "quote": quote}, headers=AUTH).json()
        self.assertEqual((r["result"], r["section_id"]), ("exact", shown["section_id"]))

    def test_the_status_shows_it_only_when_set(self):
        status = self.c.get("/v2/status", headers=AUTH).json()
        self.assertNotIn("evidence", status["settings"])
        with mock.patch.dict(self.orch.config, WIDE):
            self.c.app.state.status._probed = None
            status = self.c.get("/v2/status", headers=AUTH).json()
        self.assertEqual(status["settings"]["evidence"], {"tier1_chars": 3200})
        self.assertEqual(list(status["settings"])[-1], "evidence")


class UnstructuredTests(unittest.TestCase):
    """A root without the structure index: the chunk's own window widens, there is no "local"."""

    def test_the_chunk_window_widens(self):
        root = RootClient(structure=False, kb=False, prepare=prepare)
        self.addCleanup(root.close)
        orch = root.client.app.state.orchestrator
        body = {"query": QUERY, "limit": 3, "max_chars": 2400}
        off = root.client.post("/v2/search", json=body, headers=AUTH).json()["hits"]
        with mock.patch.dict(orch.config, WIDE):
            on = root.client.post("/v2/search", json=body, headers=AUTH).json()["hits"]
        self.assertEqual([h["id"] for h in on], [h["id"] for h in off])
        self.assertEqual({h["local"] for h in on}, {None})
        self.assertEqual([h["text"] for h in on], [h["text"] for h in off])      # a chunk here is two rows: it fits


if __name__ == "__main__":
    unittest.main()
