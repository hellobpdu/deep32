"""0.1.0-alpha.3, WP-D item D7 (bench switches, defaults unchanged): retrieval.json reranker.title and
reranker.covered_titles (rag_core.rerank). Without them the document the service reads is the one before, byte for
byte (tests/test_rerank.py pins those)."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from rag_core.db import connect
from rag_core.rerank import COVERED_TITLES_CHARS, Reranker, covered_titles_of, title_lines
from rag_core.structure import index_root

from test_api_v2 import RootClient
from v2_root import build_root, chunk_id

URL = "http://127.0.0.1:9/v1/rerank"
ITEM = {"chunk_id": "c1", "doc_id": "d1", "heading_path": ["Configuring OSPF", "NSSA"],
        "text": "line one\n" + "x" * 3000}


def recording(seen):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"results": [{"index": i, "relevance_score": 1.0 - i / 10}
                                                     for i in range(len(body["documents"]))]})
    return httpx.MockTransport(handler)


class DocumentTests(unittest.TestCase):

    def reranker(self, **cfg):
        r = Reranker({"url": URL, **cfg})
        self.addCleanup(r.close)
        return r

    def test_off_the_document_is_the_one_before(self):
        plain = self.reranker(max_chars=1600, window="head")
        for cfg in ({"title": False}, {"covered_titles": False}, {"title": False, "covered_titles": False}):
            r = self.reranker(max_chars=1600, window="head", **cfg)
            r.titles = {"d1": "A title | release 17.9"}             # there, but the setting is off
            self.assertEqual(r.document(ITEM, "line one"), plain.document(ITEM, "line one"))
            self.assertEqual(r.document(ITEM, "line one", ["Another section"]),
                             plain.document(ITEM, "line one"))
        self.assertEqual(len(plain.document(ITEM, "line one")), 1600)
        self.assertEqual((plain.title, plain.covered_titles, Reranker.titles, Reranker.covered), (False, False, {}, None))

    def test_the_title_line_comes_first_and_is_outside_the_budget(self):
        plain = self.reranker(max_chars=1600, window="head")
        r = self.reranker(max_chars=1600, window="head", title=True)
        r.titles = {"d1": "Routing Guide | release 17.9"}
        got = r.document(ITEM, "line one")
        self.assertEqual(got, "Routing Guide | release 17.9\n" + plain.document(ITEM, "line one"))
        self.assertEqual(len(got), 1600 + len("Routing Guide | release 17.9\n"))
        # a candidate of a document without a line (no structure index, another root) is sent as before
        self.assertEqual(r.document({**ITEM, "doc_id": "other"}, "line one"), plain.document(ITEM, "line one"))
        self.assertEqual(r.document({k: v for k, v in ITEM.items() if k != "doc_id"}, "line one"),
                         plain.document(ITEM, "line one"))

    def test_covered_titles_join_the_heading_line_inside_the_budget(self):
        r = self.reranker(max_chars=1600, window="head", covered_titles=True)
        got = r.document(ITEM, None, ["NSSA", "Restrictions", "Defaults"])
        self.assertTrue(got.startswith("Configuring OSPF > NSSA | covers: Restrictions; Defaults\nline one\n"))
        self.assertEqual(len(got), 1600)                    # the line is paid for by the text, as the path is
        self.assertEqual(r.document({**ITEM, "heading_path": []}, None, ["Restrictions"])[:29],
                         "covers: Restrictions\nline one")
        # nothing new to add: the document before
        plain = self.reranker(max_chars=1600, window="head")
        self.assertEqual(r.document(ITEM, None, ["nssa", "Configuring OSPF"]), plain.document(ITEM))
        self.assertEqual(r.document(ITEM, None, []), plain.document(ITEM))
        self.assertEqual(r.document(ITEM, None, None), plain.document(ITEM))

    def test_the_added_headings_are_capped(self):
        r = self.reranker(max_chars=1600, window="head", covered_titles=True)
        titles = [f"Section {n:02d} " + "t" * 50 for n in range(10)]        # 61 characters each
        line = r.document(ITEM, None, titles).split("\n")[0]
        added = line.split(" | covers: ")[1]
        self.assertEqual(added.split("; "), titles[:4])                      # 4 fit in 300 with their "; "
        self.assertLessEqual(len(added), COVERED_TITLES_CHARS)

    def test_rerank_asks_for_the_covered_titles_of_its_candidates_once(self):
        seen, asked = [], []
        r = Reranker({"url": URL, "covered_titles": True, "title": True, "max_chars": 400}, transport=recording(seen))
        self.addCleanup(r.close)
        r.titles = {"d1": "Guide"}
        r.covered = lambda ids: asked.append(ids) or {"c2": ["Restrictions"]}
        items = [dict(ITEM), {**ITEM, "chunk_id": "c2"}]
        r.rerank("line one", items)
        self.assertEqual(asked, [["c1", "c2"]])
        first, second = seen[0]["documents"]
        self.assertTrue(first.startswith("Guide\nConfiguring OSPF > NSSA\n"))
        self.assertTrue(second.startswith("Guide\nConfiguring OSPF > NSSA | covers: Restrictions\n"))

    def test_other_values_are_refused(self):
        for key in ("title", "covered_titles"):
            for bad in (1, "true", None, []):
                with self.subTest(key=key, bad=bad), self.assertRaisesRegex(ValueError, key):
                    Reranker({"url": URL, key: bad})


class RootTests(unittest.TestCase):
    """title_lines and covered_titles_of on the tiny root."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = build_root(Path(cls.tmp.name), with_kb_sources=False)
        cls.db = cls.paths.sqlite / "rag.db"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_before_the_structure_index_there_is_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            paths = build_root(Path(d), with_kb_sources=False)
            self.assertEqual(title_lines(paths.sqlite / "rag.db"), {})
            self.assertEqual(covered_titles_of(paths.sqlite / "rag.db", [chunk_id("rfc3101", 2)]), {})

    def test_the_lines_and_the_covered_headings(self):
        index_root(self.paths)
        with connect(self.db) as conn:
            ids = dict(conn.execute("SELECT doc_key, doc_id FROM doc_sources"))
        lines = title_lines(self.db)
        self.assertEqual({key: lines[doc_id] for key, doc_id in ids.items()}, {
            "rfc3101": "RFC 3101: The OSPF Not-So-Stubby Area (NSSA) Option",
            "rfc4271": "RFC 4271: A Border Gateway Protocol 4 (BGP-4)",
            "rfc1587": "RFC 1587: The OSPF NSSA Option",
            "c9300-179-rtng": "Routing Configuration Guide, 17.9 | release 17.9",
            "c9300-1718-rtng": "Routing Configuration Guide, 17.18 | release 17.18",
        })
        # chunk 2 of rfc3101 holds the text of 3.1 and the start of 3.2; chunk 1 lies in one section
        merged, single = chunk_id("rfc3101", 2), chunk_id("rfc3101", 1)
        self.assertEqual(covered_titles_of(self.db, [merged, single, "chk_unknown"]), {
            merged: ["3.1 Type-7 Translator Election", "3.2 Translating Type-7 LSAs into Type-5 LSAs"]})
        self.assertEqual(covered_titles_of(self.db, []), {})


class ServedTests(unittest.TestCase):

    def start(self, **reranker):
        root = RootClient(retrieval={"reranker": {"enabled": True, "url": URL, **reranker}})
        self.addCleanup(root.close)
        return root.client.app.state.orchestrator, root

    def test_the_orchestrator_fills_them_only_when_set(self):
        orch, _ = self.start()
        self.assertEqual((orch.reranker.title, orch.reranker.titles, orch.reranker.covered), (False, {}, None))
        orch, root = self.start(title=True, covered_titles=True)
        self.assertEqual(len(orch.reranker.titles), 5)
        self.assertIn("RFC 3101: The OSPF Not-So-Stubby Area (NSSA) Option", orch.reranker.titles.values())
        merged = chunk_id("rfc3101", 2)
        self.assertEqual(list(orch.reranker.covered([merged])), [merged])
        status = root.client.get("/v2/status", headers={"Authorization": "Bearer " + "v" * 40}).json()
        self.assertEqual(status["settings"]["reranker"], {
            "candidates": 10, "search_many_candidates": 10, "max_chars": 2400, "window": "head",
            "title": True, "covered_titles": True})

    def test_a_bad_value_leaves_the_engine_up_without_the_reranker(self):
        # as any reranker setting it cannot start with: reported, not fatal (rerank_error)
        orch, _ = self.start(title="yes")
        self.assertIsNone(orch.reranker)
        self.assertIn("title", orch.rerank_error)

    def test_search_and_search_many_send_the_title_line(self):
        orch, root = self.start(title=True)
        seen = []
        orch.reranker._client = httpx.Client(transport=recording(seen))
        headers = {"Authorization": "Bearer " + "v" * 40}
        root.client.post("/v2/search", json={"query": "NSSA translator election", "mode": "bm25"}, headers=headers)
        root.client.post("/v2/search_many", json={"queries": [{"text": "BGP ORIGINATOR_ID", "mode": "bm25"}]},
                         headers=headers)
        self.assertEqual(len(seen), 2)
        known = set(orch.reranker.titles.values())
        for body in seen:
            self.assertTrue(body["documents"])
            for document in body["documents"]:
                self.assertIn(document.split("\n")[0], known)


if __name__ == "__main__":
    unittest.main()
