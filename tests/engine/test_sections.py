import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

from rag_core.sections import (
    page_furniture,
    parse_markdown,
    parse_rfc,
    parse_sections,
    rfc_title,
    section_path,
    slugify,
    split_lines,
)

from v2_root import CISCO_179, RFC3101, RFC4271


def by_anchor(sections):
    return {s.anchor: s for s in sections}


class SlugAndLineTests(unittest.TestCase):

    def test_slug_rules(self):
        self.assertEqual(slugify("Restrictions for OSPF NSSA"), "restrictions-for-ospf-nssa")
        self.assertEqual(slugify("  show ip ospf -- neighbor (detail)!"), "show-ip-ospf-neighbor-detail")
        self.assertEqual(len(slugify("x" * 80)), 60)
        self.assertEqual(slugify("a" * 59 + " b"), "a" * 59)     # no trailing "-" after the cut
        self.assertEqual(slugify("!!!"), "section")

    def test_lines_are_newline_separated_only(self):
        # a form feed does not end a line (str.splitlines would split there), nor does a BOM
        # or CRLF change the count: the KB producers count lines the same way
        self.assertEqual(split_lines("﻿a\r\nb\fc\nd\n"), ["a", "b\fc", "d"])
        self.assertEqual(split_lines("a\n\nb"), ["a", "", "b"])

    def test_page_furniture(self):
        lines = split_lines(RFC3101)
        found = [lines[i] for i in sorted(page_furniture(lines))]
        self.assertEqual(len(found), 4)                  # two footers, two headers
        self.assertTrue(all("[Page" in f or f.startswith("\fRFC 3101") for f in found))
        # RFC 959 prints the title on a second header line
        two_line = ["RFC 959                                                     October 1985",
                    "File Transfer Protocol", "", "   text"]
        self.assertEqual(page_furniture(two_line), {0, 1})


class RfcSectionTests(unittest.TestCase):

    def setUp(self):
        self.lines, self.sections = parse_sections(RFC3101, "rfc")
        self.by = by_anchor(self.sections)

    def test_anchors_and_nesting(self):
        self.assertEqual(
            [s.anchor for s in self.sections],
            ["preamble", "status-of-this-memo", "abstract", "table-of-contents", "1", "3",
             "3.1", "3.2", "appendix-a", "author-s-addresses"],
        )
        # "1.0  Overview" is section 1 and a sibling of "Abstract", not nested under it
        self.assertEqual(self.by["1"].level, 1)
        self.assertIsNone(self.by["1"].parent)
        self.assertEqual(section_path(self.sections, self.sections.index(self.by["3.2"])),
                         ["3.0 Intra-AS Implementation Details",
                          "3.2 Translating Type-7 LSAs into Type-5 LSAs"])

    def test_line_ranges_follow_the_file(self):
        sec = self.by["3.2"]
        self.assertEqual(self.lines[sec.line_start - 1],
                         "3.2 Translating Type-7 LSAs into Type-5 LSAs")
        self.assertEqual(sec.line_end, self.by["appendix-a"].line_start - 1)
        # a section includes its subsections
        self.assertEqual(self.by["3"].line_end, sec.line_end)
        self.assertEqual(self.by["preamble"].line_end, self.by["status-of-this-memo"].line_start - 1)
        self.assertEqual(self.sections[-1].line_end, len(self.lines))

    def test_toc_lines_and_indented_list_items_are_not_headings(self):
        headings = [s.heading for s in self.sections]
        self.assertNotIn("4. The LSA with the higher router ID.", headings)
        self.assertEqual(sum(1 for h in headings if h and h.startswith("3.2")), 1)

    def test_numbered_headings_only_move_forward(self):
        text = "\n".join([
            "Header block", "", "1.  Introduction", "", "   text", "", "2.  References", "",
            "   text", "", "1 CHECKSUM - Checksum reported in LSP.", "", "   more", "",
            "12 bytes of padding follow", "", "   more", "",
        ])
        # a field list going back to 1, and a jump from 2 to 12, are body text
        self.assertEqual([s.anchor for s in parse_rfc(split_lines(text))],
                         ["preamble", "1", "2"])

    def test_a_centered_value_list_is_not_numbered_sections(self):
        # RFC 6218 section 3.3: the MAC Type values stand alone at column 12, upper case to the
        # parser (digits and brackets are uncased), and 4 and 5 move the numbering forward; they
        # took the anchors of the real sections 4 and 5, which "numbers only move forward" then
        # dropped. A centered chapter number prints its dot ("1.  INTRODUCTION").
        text = "\n".join([
            "Header block", "", "3.  Attributes", "", "3.3.  Message-Authentication-Code", "",
            "      MAC Type", "",
            "            3 CMAC-AES-128 [NIST]", "",
            "            4 CMAC-AES-192 [NIST]", "",
            "            5 CMAC-AES-256 [NIST]", "",
            "         Implementations MUST support MAC Type 0 (HMAC-SHA-1).", "",
            "4.  Security Considerations", "", "   text", "",
            "5.  Contributors", "", "   text", "",
        ])
        self.assertEqual([(s.anchor, s.heading) for s in parse_rfc(split_lines(text))],
                         [("preamble", None), ("3", "3.  Attributes"),
                          ("3.3", "3.3.  Message-Authentication-Code"),
                          ("4", "4.  Security Considerations"), ("5", "5.  Contributors")])
        # one value standing alone (no list before it) is no section either
        lone = "\n".join(["Header block", "", "3.  Attributes", "", "   text", "",
                          "            4 CMAC-AES-192 [NIST]", "", "   text", ""])
        self.assertEqual([s.anchor for s in parse_rfc(split_lines(lone))], ["preamble", "3"])
        # a centered chapter heading with its dot is still one
        centered = "\n".join(["RFC:  791", "", "                            1.  INTRODUCTION", "",
                              "  text", "", "                            2.  OVERVIEW", "", "  text"])
        self.assertEqual([s.anchor for s in parse_rfc(split_lines(centered))], ["preamble", "1", "2"])

    def test_subsection_headings_indented_like_the_body(self):
        # RFC 2328: body at column 8, level 2 at column 4, levels 3 and 4 at columns 8 and 12
        text = "\n".join([
            "Header block", "",
            "12.  LSA formats", "", "        text", "",
            "    12.4.  Originating LSAs", "", "        text", "",
            "        12.4.1.  Router-LSAs", "", "        text", "",
            "            12.4.1.1.  Describing point-to-point interfaces", "", "        text", "",
            "            12.4.1.2.  Describing broadcast and NBMA interfaces", "", "        text", "",
            "        12.4.2.  Network-LSAs", "", "        text", "",
            "        See also", "",
            "        12.4.9.  Not next in the numbering: a reference", "", "        text", "",
            "        12.4.3.  Ends in punctuation:", "", "        text", "",
            "        12.4.3.  Runs into its body", "            text", "",
            "        12.5.  Two levels deep is a list item", "", "        text", "",
            "Appendix D.  Authentication", "", "        text", "",
            "    D.4  Message generation", "", "        text", "",
            "        D.4.1 Generating Null authentication", "", "        text", "",
        ])
        sections = parse_rfc(split_lines(text))
        self.assertEqual([(s.anchor, s.level) for s in sections],
                         [("preamble", 0), ("12", 1), ("12.4", 2), ("12.4.1", 3), ("12.4.1.1", 4),
                          ("12.4.1.2", 4), ("12.4.2", 3), ("appendix-d", 1), ("appendix-d.4", 2),
                          ("appendix-d.4.1", 3)])
        by = by_anchor(sections)
        self.assertEqual(sections[by["12.4.1.2"].parent].anchor, "12.4.1")
        self.assertEqual(by["12.4.1"].line_end, by["12.4.2"].line_start - 1)
        self.assertEqual(by["12.4"].line_end, by["appendix-d"].line_start - 1)
        # RFC 959: body at column 6, upper-case subsections at columns 6 and 9
        old = "\n".join([
            "Header block", "", "   3.1.  DATA REPRESENTATION AND STORAGE", "", "      text", "",
            "      3.1.1.  DATA TYPES", "", "         text", "",
            "         3.1.1.1.  ASCII TYPE", "", "            text", "",
            "      4.2.1  Reply Codes by Function Groups", "", "         text", "",
        ])
        self.assertEqual([s.anchor for s in parse_rfc(split_lines(old))],
                         ["preamble", "3.1", "3.1.1", "3.1.1.1"])

    def test_a_subsection_title_may_read_like_a_sentence(self):
        # RFC 3168 "9.1.2.  Changes to the ECN Field within an IP Tunnel." comes next in the
        # numbering; a long numbered sentence that does not is still a paragraph
        text = "\n".join([
            "Header block", "", "9.  Tunnels", "", "9.1.  IP in IP", "",
            "9.1.1.  The Options", "", "   text", "",
            "9.1.2.  Changes to the ECN Field within an IP Tunnel.", "", "   text", "",
            "9.1.4.  The ingress then copies the ECN field into the outer header.", "", "   text", "",
            "10. The decapsulator then copies the ECN field back into the inner header.", "",
            "   text", "",
            "A.3 Scenario 3: 8 (or fewer) BAs, Aggregate Traffic Engineering,", "",
            "   text", "",
        ])
        self.assertEqual([s.anchor for s in parse_rfc(split_lines(text))],
                         ["preamble", "9", "9.1", "9.1.1", "9.1.2"])

    def test_an_indented_numbered_list_does_not_become_sections(self):
        # RFC 4188 section 3: its MIB module puts the body indent at 11, so list items at column 3
        # had a heading's place, and "4." to "6." moved the numbering on past "3.  Overview"
        text = "\n".join([
            "Header block", "", "3.  Overview", "", "   criteria:", "",
            "   1. Start with a small set of essential objects.", "",
            "   2. Require that objects be essential.", "",
            "   3. Consider evidence of current use and/or utility.", "",
            "   4. Limit the total number of objects.", "",
            "   5. Exclude objects that are simply derivable from others in this or",
            "      other MIB modules.", "",
            "3.1  Structure of the MIB Module", "", "   text", "",
            "4.  Definitions", "",
            *[f"           dot1dObject{k} OBJECT IDENTIFIER ::= {{ dot1dBridge {k} }}" for k in range(12)],
            "", "5.  Security Considerations", "", "   text", "",
        ])
        self.assertEqual([s.anchor for s in parse_rfc(split_lines(text))],
                         ["preamble", "3", "3.1", "4", "5"])

    def test_appendix_subsections(self):
        # RFC 4271 "Appendix F.1.  Multiple Networks Per Message": appendix-f.1, under appendix-f
        text = "\n".join([
            "Header block", "", "Appendix F.  Implementation Recommendations", "", "   text", "",
            "Appendix F.1.  Multiple Networks Per Message", "", "   text", "",
            "Appendix F.2.  Reducing Route Flapping", "", "   text", "",
            "Appendix G.  Other", "", "   text", "",
        ])
        sections = parse_rfc(split_lines(text))
        self.assertEqual([(s.anchor, s.level) for s in sections],
                         [("preamble", 0), ("appendix-f", 1), ("appendix-f.1", 2), ("appendix-f.2", 2),
                          ("appendix-g", 1)])
        self.assertEqual(sections[sections[2].parent].anchor, "appendix-f")

    def test_old_layouts(self):
        text = "\n".join([
            "RFC:  791", "", "                            1.  INTRODUCTION", "",
            "1.1.  Motivation", "", "  The Internet Protocol is designed for use.", "",
            "APPENDIX A:  Examples & Scenarios", "", "  Example 1:", "",
            "A.1 Encapsulation", "", "  text", "",
        ])
        sections = parse_rfc(split_lines(text))
        self.assertEqual([(s.anchor, s.level) for s in sections],
                         [("preamble", 0), ("1", 1), ("1.1", 2), ("appendix-a", 1),
                          ("appendix-a.1", 2)])
        self.assertEqual(sections[4].parent, 3)

    def test_deep_numbering(self):
        _, sections = parse_sections(RFC4271, "rfc")
        by = by_anchor(sections)
        self.assertEqual(by["9.1.2.2"].level, 4)
        self.assertEqual(sections[by["9.1.2.2"].parent].anchor, "9.1.2")

    def test_title(self):
        self.assertEqual(rfc_title(split_lines(RFC3101)), "The OSPF Not-So-Stubby Area (NSSA) Option")
        self.assertEqual(rfc_title(split_lines(RFC4271)), "A Border Gateway Protocol 4 (BGP-4)")


class MarkdownSectionTests(unittest.TestCase):

    def test_headings_outside_fences_with_duplicate_suffixes(self):
        lines = split_lines(CISCO_179)
        sections = parse_markdown(lines)
        self.assertEqual(
            [s.anchor for s in sections],
            ["configuring-ospf", "restrictions-for-ospf-nssa", "examples",
             "configuring-an-nssa-abr-as-a-forced-nssa-lsa-translator", "examples-2",
             "show-ip-ospf-neighbor"],
        )
        self.assertEqual([s.level for s in sections], [1, 2, 2, 3, 2, 3])
        forced = sections[3]
        self.assertEqual(sections[forced.parent].anchor, "examples")
        self.assertEqual(forced.line_end, sections[4].line_start - 1)

    def test_a_repeat_suffix_never_takes_another_headings_anchor(self):
        # "Foo 2" slugs to foo-2, the suffix the second "Foo" used to get: a duplicate section
        # id, which made the structure index step fail on its primary key
        for headings, anchors in ((["Foo 2", "Foo", "Foo"], ["foo-2", "foo", "foo-3"]),
                                  (["Foo", "Foo", "Foo 2"], ["foo", "foo-3", "foo-2"]),
                                  (["Foo", "Foo", "Foo"], ["foo", "foo-2", "foo-3"])):
            sections = parse_markdown([f"# {h}" for h in headings])
            self.assertEqual([s.anchor for s in sections], anchors, headings)
        text = "\n".join(["Network Working Group", "Request for Comments: 1", "", "", "Example 2",
                          "", "   body", "", "Example", "", "   body", "", "Example", "", "   body"])
        self.assertEqual([s.anchor for s in parse_rfc(split_lines(text))],
                         ["preamble", "example-2", "example", "example-3"])

    def test_preamble_only_when_there_is_text_before_the_first_heading(self):
        self.assertEqual(parse_markdown(["", "# A", "x"])[0].anchor, "a")
        self.assertEqual([s.anchor for s in parse_markdown(["intro", "# A"])], ["preamble", "a"])


if __name__ == "__main__":
    unittest.main()
