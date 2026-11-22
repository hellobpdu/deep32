"""verify_quote checks up to 16 quotations in one call and says where each match lies (WP-C C5). Each {ref, quote} goes
through deep32-engine's /v2/verify_quote in order; a 404 or 422 is that quotation's own error, any other failure stops
the loop. deep32-engine's scope and reason are printed when sent ("found outside <ref>: cite <section>", the
parts-too-far note); a single quotation on an engine without them reads as 2.4.2's.
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import unittest

import httpx
from mcp.server.mcpserver.exceptions import ToolError

import rag_edge
from test_also_in import ToolCase

SKIP = (gateway_target.BUILDER, gateway_target.ALPHA3_ONLY)
STRUCTURE = "structure index missing (serve a root built by scripts/build_root.py)"
QUOTE = "The suggested default value"


@unittest.skipIf(*SKIP)
class VerifyManyTest(ToolCase):
    ANSWERS = {
        "rfc4271#10": {"result": "exact", "scope": "section", "sections": ["rfc4271#10"], "gap_chars": 0,
                       "section_id": "rfc4271#10", "local": "rfc4271.txt:5008-5009", "match_text": "x",
                       "degraded": []},
        "rfc4271#4.4": {"result": "normalized", "scope": "document", "sections": ["rfc4271#10"], "gap_chars": 0,
                        "section_id": "rfc4271#10", "local": "rfc4271.txt:5008-5009", "match_text": "x",
                        "degraded": []},
    }

    def engine(self, request):
        ref = json.loads(request.content)["ref"]
        if ref in self.ANSWERS:
            return httpx.Response(200, json=self.ANSWERS[ref])
        if ref == "broken#1":
            return httpx.Response(503, json={"detail": STRUCTURE})
        return httpx.Response(404, json={"detail": f"unknown reference: {ref}"})

    async def test_one_line_per_quotation_in_order(self):
        self.serve({("POST", "/v2/verify_quote"): self.engine})
        quotes = [{"ref": "rfc4271#10", "quote": QUOTE}, {"ref": "rfc4271#4.4", "quote": QUOTE},
                  {"ref": "nosuch#1", "quote": "abc"}]
        text = await self.call("verify_quote", quotes=quotes)
        self.assertEqual([json.loads(r.content) for r in self.fake.requests], quotes)
        self.assertEqual(text.splitlines(), [
            '<verify n=1 ref="rfc4271#10" result="exact" scope="section" section="rfc4271#10" '
            'local="rfc4271.txt:5008-5009"/>',
            '<verify n=2 ref="rfc4271#4.4" result="normalized" scope="document" section="rfc4271#10" '
            'local="rfc4271.txt:5008-5009"/> found outside rfc4271#4.4: cite rfc4271#10',
            '<verify n=3 ref="nosuch#1" error="unknown reference: nosuch#1"/>'])

    async def test_an_engine_failure_stops_the_loop(self):
        self.serve({("POST", "/v2/verify_quote"): self.engine})
        quotes = [{"ref": ref, "quote": QUOTE} for ref in ("rfc4271#10", "broken#1", "rfc4271#4.4")]
        lines = (await self.call("verify_quote", quotes=quotes)).splitlines()
        self.assertEqual(len(self.fake.requests), 2)
        self.assertEqual(lines[1], f'<verify n=2 ref="broken#1" error="deep32-engine cannot answer (HTTP 503): '
                                   f'{STRUCTURE}; check rag_status before going on"/>')
        self.assertEqual(lines[2], '<verify n=3 ref="rfc4271#4.4" error="not checked: the engine failed on '
                                   'quotation 2"/>')

    async def test_json_lists_each_result(self):
        self.serve({("POST", "/v2/verify_quote"): self.engine})
        quotes = [{"ref": "rfc4271#10", "quote": QUOTE}, {"ref": "rfc4271#4.4", "quote": QUOTE}]
        text = await self.call("verify_quote", quotes=quotes, format="json")
        self.assertEqual(json.loads(text), {"results": [{"n": 1, "ref": "rfc4271#10", **self.ANSWERS["rfc4271#10"]},
                                                        {"n": 2, "ref": "rfc4271#4.4",
                                                         **self.ANSWERS["rfc4271#4.4"]}],
                                            "degraded": []})

    async def test_degraded_is_the_union(self):
        self.serve({("POST", "/v2/verify_quote"): {**self.ANSWERS["rfc4271#10"], "degraded": ["structure"]}})
        quotes = [{"ref": ref, "quote": QUOTE} for ref in ("rfc4271#10", "rfc4271#4.4", "rfc3101#3.2")]
        lines = (await self.call("verify_quote", quotes=quotes)).splitlines()
        self.assertEqual(lines[0], "degraded: structure")
        self.assertEqual(len(lines), 1 + len(quotes))

    async def test_one_quotation(self):
        cases = (
            ({"result": "normalized", "section_id": "rfc3101#3.2", "local": "rfc3101.txt:917-918",
              "match_text": "Only installed"}, "rfc3101#3.2",
             '<verify result="normalized" section="rfc3101#3.2" local="rfc3101.txt:917-918">\nOnly installed\n'
             '</verify>'),
            ({**self.ANSWERS["rfc4271#4.4"], "match_text": "The suggested default value for the HoldTime is 90\n"
                                                           "seconds."}, "rfc4271#4.4",
             '<verify result="normalized" scope="document" section="rfc4271#10" local="rfc4271.txt:5008-5009">\n'
             'The suggested default value for the HoldTime is 90\nseconds.\n</verify>\n'
             'found outside rfc4271#4.4: cite rfc4271#10'),
            ({"result": "not_found", "reason": "parts-too-far", "scope": "section", "section_id": "rfc4271#10",
              "local": None, "match_text": None}, "rfc4271#10",
             '<verify result="not_found" reason="parts-too-far" scope="section" section="rfc4271#10"/>\n'
             'the parts around "..." are too far apart to be one quotation: quote them as separate quotations'),
        )
        for answer, ref, want in cases:
            with self.subTest(ref=ref, result=answer["result"]):
                self.serve({("POST", "/v2/verify_quote"): answer})
                self.assertEqual(await self.call("verify_quote", ref=ref, quote=QUOTE), want)
                self.assertEqual(self.fake.body(), {"ref": ref, "quote": QUOTE})

    async def test_arguments_the_tool_refuses(self):
        self.serve({("POST", "/v2/verify_quote"): self.engine})
        one = {"ref": "rfc4271#10", "quote": QUOTE}
        prefix = "Error executing tool verify_quote: "
        for arguments, message in (
                ({**one, "quotes": [one]}, "give ref and quote for one quotation, or quotes for several, not both"),
                ({}, "give ref and quote, or quotes (a list of up to 16 {ref, quote})"),
                ({"ref": "rfc4271#10"}, "give ref and quote, or quotes (a list of up to 16 {ref, quote})"),
                ({"quote": QUOTE}, "give ref and quote, or quotes (a list of up to 16 {ref, quote})")):
            with self.subTest(arguments=sorted(arguments)), self.assertRaises(ToolError) as caught:
                await self.mcp.call_tool("verify_quote", arguments)
            self.assertEqual(str(caught.exception), prefix + message)
        for quotes in ([one] * 17, [], [{"ref": "rfc4271#10", "quote": "x" * 2001}],
                       [{"ref": "rfc4271#10", "quote": "ab"}], [{"quote": QUOTE}]):
            with self.subTest(quotes=len(quotes)), self.assertRaises(ToolError):
                await self.mcp.call_tool("verify_quote", {"quotes": quotes})
        self.assertEqual(self.fake.requests, [])

    async def test_the_schema(self):
        self.serve({})
        tool = {t.name: t for t in await self.mcp.list_tools()}["verify_quote"]
        schema = tool.input_schema
        self.assertNotIn("required", schema)
        self.assertEqual((schema["properties"]["quotes"]["anyOf"][0]["minItems"],
                          schema["properties"]["quotes"]["anyOf"][0]["maxItems"]), (1, 16))
        self.assertEqual(schema["$defs"]["QuoteItem"]["properties"]["quote"]["maxLength"], 2000)
        self.assertEqual(schema["properties"]["quote"]["anyOf"][0]["maxLength"], 4000)
        description = " ".join(tool.description.split())
        self.assertIn("the cited section first, then the rest of the same document", description)
        self.assertIn("quotes=[{ref, quote}, ...], up to 16", description)
        self.assertNotIn("really appears in a section", description)
        self.assertEqual(schema["properties"]["quotes"]["description"], rag_edge.QUOTES)


if __name__ == "__main__":
    unittest.main()
