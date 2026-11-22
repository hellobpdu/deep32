"""0.1.0-alpha.3, WP-D item D5: GET /v2/section says the document's release and platform and an RFC's standing,
takes `around` (a passage id or a line of the source file) to start the page at that place, and lists the same
section in the same book at other releases ("same_section_in": RootReader.same_section_in, reader.book_family).

Without `around` every answer is the one before, plus the new keys: RECORDED holds the bodies 0.1.0-alpha.2
(d84ce2a) sent for these requests on the tiny root of tests/v2_root.py, byte for byte."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import unittest
from pathlib import Path

from rag_core.reader import Doc, book_family, heading_key, release_spellings

from test_api_v2 import AUTH, RootClient
from v2_root import add_document, chunk_id, line_of

ADDED = ("release", "platform", "status", "around", "same_section_in")     # what 0.1.0-alpha.3 adds
# (the query; "CHUNK:<doc_key>:<ordinal>" stands for that chunk's id, status, body) as d84ce2a answered
RECORDED = [
    ({'ref': 'rfc3101#3.2', 'max_chars': 80}, 200, '{"doc_key":"rfc3101","section_id":"rfc3101#3.2","title":"3.2 Translating Type-7 LSAs into Type-5 LSAs","cite":"RFC 3101 \xa73.2","section_path":["3.0 Intra-AS Implementation Details","3.2 Translating Type-7 LSAs into Type-5 LSAs"],"text":"3.2 Translating Type-7 LSAs into Type-5 LSAs\\n\\n   This step is performed as part ","offset":0,"next_offset":80,"total_chars":437,"local":"rfc3101.txt:41-43","prev_section_id":"rfc3101#3.1","next_section_id":"rfc3101#appendix-a","degraded":[]}'),
    ({'ref': 'rfc3101#3.2', 'offset': 80, 'max_chars': 80}, 200, '{"doc_key":"rfc3101","section_id":"rfc3101#3.2","title":"3.2 Translating Type-7 LSAs into Type-5 LSAs","cite":"RFC 3101 \xa73.2","section_path":["3.0 Intra-AS Implementation Details","3.2 Translating Type-7 LSAs into Type-5 LSAs"],"text":"of the NSSA\'s Dijkstra calculation.\\n   Only installed\\n\\n   Type-7 LSAs should be ","offset":80,"next_offset":160,"total_chars":437,"local":"rfc3101.txt:43-50","prev_section_id":"rfc3101#3.1","next_section_id":"rfc3101#appendix-a","degraded":[]}'),
    ({'ref': 'rfc3101#3'}, 200, '{"doc_key":"rfc3101","section_id":"rfc3101#3","title":"3.0 Intra-AS Implementation Details","cite":"RFC 3101 \xa73","section_path":["3.0 Intra-AS Implementation Details"],"text":"3.0 Intra-AS Implementation Details\\n\\n3.1 Type-7 Translator Election\\n\\n   A border router defers when another border router has bit Nt set or\\n   a higher router ID.  The TranslatorStabilityInterval defaults to 40\\n   seconds.\\n\\n3.2 Translating Type-7 LSAs into Type-5 LSAs\\n\\n   This step is performed as part of the NSSA\'s Dijkstra calculation.\\n   Only installed\\n\\n   Type-7 LSAs should be examined.  The transla-\\n   tion is skipped when the P-bit is clear.\\n\\n      (1) If the Type-7 LSA has the P-bit clear, or its forwarding\\n          address is set to 0.0.0.0, then do nothing with this Type-7\\n          LSA.\\n\\n                 4. The LSA with the higher router ID.\\n","offset":0,"next_offset":null,"total_chars":661,"local":"rfc3101.txt:29-57","prev_section_id":"rfc3101#1","next_section_id":"rfc3101#appendix-a","degraded":[]}'),
    ({'ref': 'CHUNK:rfc3101:3'}, 200, '{"doc_key":"rfc3101","section_id":"rfc3101#3.2","title":"3.2 Translating Type-7 LSAs into Type-5 LSAs","cite":"RFC 3101 \xa73.2","section_path":["3.0 Intra-AS Implementation Details","3.2 Translating Type-7 LSAs into Type-5 LSAs"],"text":"3.2 Translating Type-7 LSAs into Type-5 LSAs\\n\\n   This step is performed as part of the NSSA\'s Dijkstra calculation.\\n   Only installed\\n\\n   Type-7 LSAs should be examined.  The transla-\\n   tion is skipped when the P-bit is clear.\\n\\n      (1) If the Type-7 LSA has the P-bit clear, or its forwarding\\n          address is set to 0.0.0.0, then do nothing with this Type-7\\n          LSA.\\n\\n                 4. The LSA with the higher router ID.\\n","offset":0,"next_offset":null,"total_chars":437,"local":"rfc3101.txt:41-57","prev_section_id":"rfc3101#3.1","next_section_id":"rfc3101#appendix-a","degraded":[]}'),
    ({'ref': 'rfc3101#preamble'}, 200, '{"doc_key":"rfc3101","section_id":"rfc3101#preamble","title":"The OSPF Not-So-Stubby Area (NSSA) Option","cite":"RFC 3101","section_path":[],"text":"Network Working Group                                          P. Murphy\\nRequest for Comments: 3101                          US Geological Survey\\nObsoletes: 1587                                             January 2003\\nCategory: Standards Track\\n\\n               The OSPF Not-So-Stubby Area (NSSA) Option\\n","offset":0,"next_offset":null,"total_chars":303,"local":"rfc3101.txt:2-8","prev_section_id":null,"next_section_id":"rfc3101#status-of-this-memo","degraded":[]}'),
    ({'ref': 'c9300-179-rtng#examples'}, 200, '{"doc_key":"c9300-179-rtng","section_id":"c9300-179-rtng#examples","title":"Examples","cite":"Routing Configuration Guide, 17.9 \u203a Configuring OSPF \u203a Examples","section_path":["Configuring OSPF","Examples"],"text":"## Examples\\n```\\nRouter(config)# router ospf 1\\n# a comment inside a fence is not a heading\\nRouter(config-router)# area 1 nssa translate type7 always\\n```\\n### Configuring an NSSA ABR as a Forced NSSA LSA Translator\\nUse the area nssa translate type7 always command to force translation.\\narea area-id nssa translate type7 [always]","offset":0,"next_offset":null,"total_chars":325,"local":"c9300-179-rtng.txt:5-13","prev_section_id":"c9300-179-rtng#restrictions-for-ospf-nssa","next_section_id":"c9300-179-rtng#examples-2","degraded":[]}'),
    ({'ref': 'c9300-1718-rtng#ospfv3-nssa'}, 200, '{"doc_key":"c9300-1718-rtng","section_id":"c9300-1718-rtng#ospfv3-nssa","title":"OSPFv3 NSSA","cite":"Routing Configuration Guide, 17.18 \u203a Configuring OSPFv3 \u203a OSPFv3 NSSA","section_path":["Configuring OSPFv3","OSPFv3 NSSA"],"text":"## OSPFv3 NSSA\\nIn Cisco IOS XE 17.18 the NSSA translator also handles OSPFv3 Type-7 LSAs.","offset":0,"next_offset":null,"total_chars":89,"local":"c9300-1718-rtng.txt:3-4","prev_section_id":null,"next_section_id":null,"degraded":[]}'),
    ({'ref': 'rfc1587#1'}, 200, '{"doc_key":"rfc1587","section_id":"rfc1587#1","title":"1.  Introduction","cite":"RFC 1587 \xa71","section_path":["1.  Introduction"],"text":"1.  Introduction\\n\\n   This document describes a new optional type of OSPF area, the NSSA.\\n   Type-7 LSAs are translated by the NSSA border router.","offset":0,"next_offset":null,"total_chars":145,"local":"rfc1587.txt:6-9","prev_section_id":"rfc1587#preamble","next_section_id":null,"degraded":[]}'),
]

BOOK_179 = "\n".join([
    "# OSPF Guide, 17.9",
    "## Configuring NSSA",
    "An NSSA carries Type-7 LSAs.",
    "### Restrictions",
    "An NSSA cannot be a transit area.",
    "### Examples",
    "area 1 nssa",
    "## Configuring Stub",
    "A stub area takes no external routes.",
    "### Restrictions",
    "A stub area cannot hold an ASBR.",
    "",
])
# the same book with its two chapters the other way round: "Restrictions" and "Restrictions 2" swap anchors
BOOK_1718 = "\n".join([
    "# OSPF Guide, 17.18",
    "## Configuring Stub",
    "A stub area takes no external routes.",
    "### Restrictions",
    "A stub area cannot hold an ASBR or a virtual link.",
    "## Configuring NSSA",
    "An NSSA carries Type-7 LSAs.",
    "### Restrictions",
    "An NSSA cannot be a transit area.",
    "### Examples",
    "area 1 nssa",
    "",
])
BOOK_26X = BOOK_179.replace("17.9", "26.x").replace("area 1 nssa", "area 1 nssa no-summary")
LONG = "\n".join(["# Long note", "## Rows"] + [f"Row {n:03d} of the long note." for n in range(1, 201)] + [""])


def prepare(paths, kb_src):
    add_document(paths, "book-179-ospf", "cisco", BOOK_179, "OSPF Guide, 17.9")
    add_document(paths, "book-1718-ospf", "cisco", BOOK_1718, "OSPF Guide, 17.18")
    add_document(paths, "book-26x-ospf", "cisco", BOOK_26X, "OSPF Guide, 26.x")
    add_document(paths, "long-note", "cisco", LONG, "Long note")
    # the release of a key that spells no number the engine infers (26x), and a platform, as the corpus manifest
    # names them (rag_core.structure.load_manifest)
    entries = [{"doc_key": "book-26x-ospf", "doc_type": "cisco", "release": "26.x", "platform": "Catalyst 9300"}]
    (paths.root / "sources" / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")


class Served(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=prepare)
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def section(self, status=200, **params):
        r = self.c.get("/v2/section", params=params, headers=AUTH)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()


class RecordedTests(Served):

    def test_every_recorded_body_comes_back_once_the_added_keys_are_taken_out(self):
        for params, status, body in RECORDED:
            with self.subTest(params=params):
                sent = dict(params)
                if sent["ref"].startswith("CHUNK:"):
                    _, key, ordinal = sent["ref"].split(":")
                    sent["ref"] = chunk_id(key, int(ordinal))
                r = self.c.get("/v2/section", params=sent, headers=AUTH)
                now = json.loads(r.content)
                kept = json.dumps({k: v for k, v in now.items() if k not in ADDED}, ensure_ascii=False,
                                  allow_nan=False, indent=None, separators=(",", ":"))
                self.assertEqual((r.status_code, kept), (status, body))
                self.assertEqual(list(now), [
                    "doc_key", "section_id", "title", "cite", "section_path", "release", "platform", "status",
                    "text", "offset", "next_offset", "total_chars", "local", "prev_section_id", "next_section_id",
                    "same_section_in", "degraded"])


class FieldTests(Served):

    def test_an_rfc_section_carries_its_standing_as_a_hit_does(self):
        hit = self.c.post("/v2/search", json={"query": "P-bit clear forwarding address 0.0.0.0", "limit": 1},
                          headers=AUTH).json()["hits"][0]
        got = self.section(ref="rfc3101#3.2")
        self.assertEqual(hit["section_id"], "rfc3101#3.2")
        self.assertEqual(got["status"], hit["status"])
        self.assertEqual(got["status"], "Proposed Standard; obsoletes RFC 1587; errata: 1 verified; "
                                        "errata on this section: EID 3001 (Verified)")
        self.assertEqual((got["release"], got["platform"], got["same_section_in"]), (None, None, []))
        self.assertEqual(self.section(ref="rfc1587#1")["status"], "Proposed Standard; OBSOLETED by RFC 3101")
        self.assertEqual(self.section(ref="rfc3101#1")["status"], "Proposed Standard; obsoletes RFC 1587; "
                                                                  "errata: 1 verified")

    def test_a_guide_section_carries_release_and_platform(self):
        got = self.section(ref="book-26x-ospf#configuring-nssa")
        self.assertEqual((got["release"], got["platform"], got["status"]), ("26.x", "Catalyst 9300", None))
        self.assertEqual(self.section(ref="book-179-ospf#configuring-nssa")["release"], "17.9")


class OtherRootTests(unittest.TestCase):
    """A root of its own (one served root at a time: rag_core.api.app is one application)."""

    def test_without_the_kb_the_standing_is_null_and_nothing_is_degraded(self):
        root = RootClient(kb=False)
        self.addCleanup(root.close)
        got = root.client.get("/v2/section", params={"ref": "rfc3101#3.2"}, headers=AUTH).json()
        self.assertEqual((got["status"], got["degraded"]), (None, []))


class AroundTests(Served):

    def test_a_line_starts_the_page_there(self):
        whole = self.section(ref="long-note#rows", max_chars=100000)
        line = line_of(LONG, "Row 150 of the long note.")
        got = self.section(ref="long-note#rows", around=str(line), max_chars=60)
        self.assertEqual(got["around"], str(line))
        self.assertTrue(got["text"].startswith("Row 150 of the long note.\nRow 151"))
        self.assertEqual(got["offset"], whole["text"].index("Row 150 of the long note."))
        self.assertEqual(got["local"].split(":")[1].split("-")[0], str(line))
        self.assertEqual(got["total_chars"], whole["total_chars"])
        # reading on is plain paging
        more = self.section(ref="long-note#rows", offset=got["next_offset"], max_chars=60)
        self.assertEqual(got["text"] + more["text"], whole["text"][got["offset"]:got["offset"] + 120])
        self.assertNotIn("around", more)

    def test_a_passage_id_starts_the_page_at_its_first_line(self):
        hit = self.c.post("/v2/search", json={"query": "Row 150 of the long note", "limit": 1, "mode": "bm25",
                                              "filters": {"docs": ["long-note"]}}, headers=AUTH).json()["hits"][0]
        got = self.section(ref=hit["section_id"], around=hit["id"], max_chars=80)
        first = got["text"].split("\n")[0]
        self.assertIn(first, hit["text"])
        self.assertIn("Row 150 of the long note.", got["text"])
        self.assertGreater(got["offset"], 3000)
        self.assertEqual(got["around"], hit["id"])
        # the chunk id as the ref too: its own section, started at the passage
        same = self.section(ref=hit["id"], around=hit["id"], max_chars=80)
        self.assertEqual((same["section_id"], same["offset"], same["text"]),
                         (got["section_id"], got["offset"], got["text"]))

    def test_the_section_s_first_line_is_offset_0_and_a_passage_from_above_starts_there(self):
        first = line_of(LONG, "## Rows")
        self.assertEqual(self.section(ref="long-note#rows", around=str(first))["offset"], 0)
        # chunk 2 of rfc3101 begins in 3.1 and reaches into 3.2: around it, 3.2 starts at its own first line
        got = self.section(ref="rfc3101#3.2", around=chunk_id("rfc3101", 2))
        self.assertEqual((got["offset"], got["text"][:3]), (0, "3.2"))

    def test_page_furniture_and_blank_lines_are_skipped_forward(self):
        # line 45 of the RFC fixture is the blank line before a page footer: the page starts at the next shown line
        from v2_root import RFC3101
        footer = line_of(RFC3101, "[Page 3]")
        got = self.section(ref="rfc3101#3.2", around=str(footer))
        self.assertTrue(got["text"].lstrip().startswith("Type-7 LSAs should be examined."), got["text"][:60])
        self.assertNotIn("[Page", got["text"])

    def test_what_is_refused(self):
        rows = "long-note#rows"
        for params, status, detail in (
            ({"ref": rows, "around": "150", "offset": 10}, 400, "give offset or around, not both"),
            ({"ref": rows, "around": "1"}, 400, "line 1 is outside long-note#rows (lines 2-202)"),
            ({"ref": rows, "around": "203"}, 400, "line 203 is outside long-note#rows (lines 2-202)"),
            ({"ref": rows, "around": "row-150"}, 400,
             "around must be a passage id (chk_...) or a line of the source file"),
            ({"ref": rows, "around": "long-note.txt:150-160"}, 400,
             "around must be a passage id (chk_...) or a line of the source file"),
            ({"ref": rows, "around": "chk_" + "0" * 64}, 404, "unknown passage: chk_" + "0" * 64),
            ({"ref": rows, "around": chunk_id("rfc3101", 3)}, 400,
             f"passage {chunk_id('rfc3101', 3)} is not in long-note#rows (lines 2-202)"),
            ({"ref": "rfc3101#1", "around": chunk_id("rfc3101", 3)}, 400,
             f"passage {chunk_id('rfc3101', 3)} is not in rfc3101#1 (lines 24-28)"),
        ):
            with self.subTest(params=params):
                r = self.c.get("/v2/section", params=params, headers=AUTH)
                self.assertEqual((r.status_code, r.json()["detail"]), (status, detail))
        self.assertEqual(self.c.get("/v2/section", params={"ref": rows, "around": ""}, headers=AUTH).status_code, 422)


class SameSectionTests(Served):

    def twins(self, ref):
        return self.section(ref=ref)["same_section_in"]

    def test_a_section_headed_once_is_found_by_its_anchor_oldest_release_first(self):
        self.assertEqual(self.twins("book-179-ospf#configuring-nssa"), [
            {"doc_key": "book-1718-ospf", "section_id": "book-1718-ospf#configuring-nssa", "release": "17.18"},
            {"doc_key": "book-26x-ospf", "section_id": "book-26x-ospf#configuring-nssa", "release": "26.x"}])
        self.assertEqual([t["doc_key"] for t in self.twins("book-26x-ospf#examples")],
                         ["book-179-ospf", "book-1718-ospf"])
        self.assertEqual([t["release"] for t in self.twins("book-1718-ospf#examples")], ["17.9", "26.x"])

    def test_a_repeated_heading_needs_the_same_headings_above_it(self):
        # "Restrictions" is the NSSA chapter's in 17.9 and 26.x and the stub chapter's in 17.18, by document order
        self.assertEqual([t["section_id"] for t in self.twins("book-179-ospf#restrictions")],
                         ["book-26x-ospf#restrictions"])
        self.assertEqual([t["section_id"] for t in self.twins("book-179-ospf#restrictions-2")],
                         ["book-26x-ospf#restrictions-2"])
        self.assertEqual(self.twins("book-1718-ospf#restrictions"), [])
        self.assertEqual(self.twins("book-1718-ospf#restrictions-2"), [])
        for twin in self.twins("book-179-ospf#restrictions"):
            self.assertIn("transit area", self.section(ref=twin["section_id"])["text"])

    def test_the_top_heading_names_the_release_and_is_not_a_twin(self):
        self.assertEqual(self.twins("book-179-ospf#ospf-guide-17-9"), [])

    def test_the_tiny_root_s_two_guides_share_no_anchor_and_documents_without_a_family_have_none(self):
        self.assertEqual(self.twins("c9300-179-rtng#examples"), [])
        self.assertEqual(self.twins("long-note#rows"), [])          # no release
        self.assertEqual(self.twins("rfc3101#3.2"), [])
        self.assertEqual(self.twins("rfc3101#preamble"), [])


class FamilyTests(unittest.TestCase):

    @staticmethod
    def doc(key, release, doc_type="cisco"):
        return Doc("d", key, doc_type, None, release, None, None, Path("x.txt"))

    def test_release_spellings(self):
        for release, expected in (("17.9", {"179"}), ("17.18.x", {"1718", "1718x"}), ("26.x", {"26x"}),
                                  ("3.1", {"31"}), ("9.24", {"924"}), ("2.3.7.9", {"23", "237", "2379"}),
                                  ("17", set()), ("", set()), (None, set()), ("17.x", {"17x"})):
            self.assertEqual(release_spellings(release), expected, release)

    def test_book_family(self):
        for key, release, expected in (
            ("c9300-179-rtng", "17.9.x", ("cisco", "c9300-*-rtng")),
            ("c9300-1718-rtng", "17.18.x", ("cisco", "c9300-*-rtng")),
            ("c9300-26x-rtng", "26.x", ("cisco", "c9300-*-rtng")),
            ("ise31-admin-pxgrid", "3.1", ("cisco", "ise*-admin-pxgrid")),
            ("ise34-admin-pxgrid", "3.4", ("cisco", "ise*-admin-pxgrid")),
            ("asa924-vpn-ra", "9.24", ("cisco", "asa*-vpn-ra")),
            ("iosxe-cr-bgp", "17.x", None),                      # no token spells the release
            ("cisco-tn-eigrp-path-selection", "17.9", None),
            ("c9300-179-rtng", None, None),                      # no stored release
            ("c9300-179-rtng", "17", None),                      # a release of one part
            ("lab-179-179", "17.9", None),                       # two tokens spell it
        ):
            self.assertEqual(book_family(self.doc(key, release)), expected, key)
        self.assertEqual(book_family(self.doc("rfc3101", None, "rfc")), None)

    def test_heading_key(self):
        self.assertEqual(heading_key("  Restrictions  for\tOSPF NSSA "), "restrictions for ospf nssa")
        self.assertEqual(heading_key(None), "")


if __name__ == "__main__":
    unittest.main()
