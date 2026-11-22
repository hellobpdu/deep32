"""get_section prints the citation, release and standing, opens at a passage and names the same section in other
releases (WP-C C3). deep32-engine sends cite, release, status (an RFC's standing), same_section_in and echoes around;
the gateway prints each only when it is sent, so an answer without them reads as 2.4.2's. around= is a hit's id or a
file line; an engine without around ignores it and the reply says so.
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import unittest

from mcp.server.mcpserver.exceptions import ToolError

import edge_242 as E242
import rag_edge
from test_also_in import ToolCase
from test_tools import SECTION

SKIP = (gateway_target.BUILDER, gateway_target.ALPHA3_ONLY)

NEW = {**SECTION, "cite": "RFC 3101 §3.2", "release": None, "status": "Proposed Standard; obsoletes RFC 1587",
       "degraded": []}
CISCO = {"doc_key": "c9300-179-rtng", "section_id": "c9300-179-rtng#restrictions-for-ospf-nssa",
         "title": "Restrictions for OSPF NSSA",
         "cite": "Routing Configuration Guide, 17.9 › Configuring OSPF › Restrictions for OSPF NSSA",
         "section_path": ["Configuring OSPF", "Restrictions for OSPF NSSA"],
         "text": "An NSSA ABR translates Type-7 LSAs into Type-5 LSAs only when it is the elected translator.",
         "offset": 1200, "next_offset": 1291, "total_chars": 4000, "local": "c9300-179-rtng.txt:40-41",
         "prev_section_id": "c9300-179-rtng#prerequisites-for-ospf-nssa",
         "next_section_id": "c9300-179-rtng#information-about-ospf-nssa", "release": "17.9.x", "status": None,
         "around": "chk_179a",
         "same_section_in": [
             {"doc_key": "c9300-1718-rtng", "section_id": "c9300-1718-rtng#restrictions-for-ospf-nssa",
              "release": "17.18.x"},
             {"doc_key": "c9300-26x-rtng", "section_id": "c9300-26x-rtng#restrictions-for-ospf-nssa",
              "release": "26.x"}],
         "degraded": []}

# the "after" block of WP-C C3, exactly
CISCO_REPLY = [
    '<section section="c9300-179-rtng#restrictions-for-ospf-nssa" cite="Routing Configuration Guide, 17.9 › '
    'Configuring OSPF › Restrictions for OSPF NSSA" doc="c9300-179-rtng" release="17.9.x" title="Restrictions for '
    'OSPF NSSA" path="Configuring OSPF › Restrictions for OSPF NSSA" local="c9300-179-rtng.txt:40-41" '
    'chars="1200-1291/4000" prev="c9300-179-rtng#prerequisites-for-ospf-nssa" '
    'next="c9300-179-rtng#information-about-ospf-nssa">',
    "An NSSA ABR translates Type-7 LSAs into Type-5 LSAs only when it is the elected translator.",
    "</section>",
    "same section in other releases: c9300-1718-rtng [c9300-1718-rtng#restrictions-for-ospf-nssa] release 17.18.x; "
    "c9300-26x-rtng [c9300-26x-rtng#restrictions-for-ospf-nssa] release 26.x",
    '[more: get_section(ref="c9300-179-rtng#restrictions-for-ospf-nssa", offset=1291)]',
]
SECTION_FIRST = ('<section section="rfc3101#3.2" doc="rfc3101" title="The OSPF NSSA Option" '
                 'path="3 Border Router Behavior › 3.2 Translating Type-7 LSAs" local="rfc3101.txt:900-940" '
                 'chars="0-48/96" prev="rfc3101#3.1" next="rfc3101#3.3">')


@unittest.skipIf(*SKIP)
class SectionViewTest(ToolCase):
    async def test_cite_and_standing(self):
        self.serve({("GET", "/v2/section"): NEW})
        text = await self.call("get_section", ref="rfc3101#3.2")
        self.assertEqual(text.splitlines(), [
            '<section section="rfc3101#3.2" cite="RFC 3101 §3.2" doc="rfc3101" status="Proposed Standard; obsoletes '
            'RFC 1587" title="The OSPF NSSA Option" path="3 Border Router Behavior › 3.2 Translating Type-7 LSAs" '
            'local="rfc3101.txt:900-940" chars="0-48/96" prev="rfc3101#3.1" next="rfc3101#3.3">',
            "Section text </section_> with a fake closing tag.", "</section>",
            '[more: get_section(ref="rfc3101#3.2", offset=48)]'])

    async def test_release_and_the_same_section_in_other_releases(self):
        self.serve({("GET", "/v2/section"): CISCO})
        text = await self.call("get_section", ref=CISCO["section_id"], around="chk_179a")
        self.assertEqual(text.splitlines(), CISCO_REPLY)
        self.assertEqual(self.fake.params(), {"ref": "c9300-179-rtng#restrictions-for-ospf-nssa", "offset": "0",
                                              "max_chars": "20000", "around": "chk_179a"})

    async def test_around_is_sent_as_a_chunk_id_or_a_line(self):
        self.serve({("GET", "/v2/section"): CISCO})
        for around, sent in (("chk_179a", "chk_179a"), (" 900 ", "900"), ("c9300-179-rtng.txt:40", "40"),
                             ("c9300-179-rtng.txt:40-41", "40"), ("C9300-179-RTNG.txt:40", "40")):
            with self.subTest(around=around):
                await self.call("get_section", ref=CISCO["section_id"], around=around)
                self.assertEqual(self.fake.params()["around"], sent)
        await self.call("get_section", ref="chk_179a", around="rfc4271.txt:900")
        self.assertEqual(self.fake.params()["around"], "900")           # no document to compare

    async def test_around_refusals(self):
        self.serve({("GET", "/v2/section"): CISCO})
        for arguments, message in (
                ({"ref": "rfc3101#3.2", "around": "rfc4271.txt:900"},
                 "around is a line of rfc4271.txt, but the section is in rfc3101"),
                ({"ref": "rfc3101#3.2", "around": "page 9"},
                 'around takes a passage id (chk_...) or a file line (900, or "rfc3101.txt:900-940")'),
                ({"ref": "rfc3101#3.2", "around": "chk_179a", "offset": 48}, "give around or offset, not both")):
            with self.subTest(arguments=arguments), self.assertRaises(ToolError) as caught:
                await self.mcp.call_tool("get_section", arguments)
            self.assertEqual(str(caught.exception), f"Error executing tool get_section: {message}")
        self.assertEqual(self.fake.requests, [])

    async def test_around_on_an_engine_without_it(self):
        self.serve({("GET", "/v2/section"): SECTION})
        text = await self.call("get_section", ref="rfc3101#3.2", around="900")
        self.assertEqual(text.splitlines(), [
            SECTION_FIRST, "Section text </section_> with a fake closing tag.", "</section>",
            "[around not used: this deep32-engine opens a section at its start; page with offset]",
            '[more: get_section(ref="rfc3101#3.2", offset=48)]'])

    def test_an_answer_without_the_new_fields_reads_as_2_4_2_s(self):
        self.assertEqual(rag_edge.render_section(SECTION), E242.render_section(SECTION))
        self.assertEqual(rag_edge.render_section(SECTION, around_sent=False), E242.render_section(SECTION))

    async def test_json_is_the_engine_answer(self):
        self.serve({("GET", "/v2/section"): CISCO})
        text = await self.call("get_section", ref=CISCO["section_id"], format="json")
        self.assertEqual(json.loads(text), CISCO)

    async def test_the_description_names_around_and_the_line(self):
        self.serve({})
        tool = {t.name: t for t in await self.mcp.list_tools()}["get_section"]
        description = " ".join(tool.description.split())
        self.assertIn("around= (a hit's id, or a file line such as 900) starts the page at that passage", description)
        self.assertIn('A "same section in other releases" line', description)
        self.assertEqual(tool.input_schema["properties"]["around"]["description"], rag_edge.AROUND)


if __name__ == "__main__":
    unittest.main()
