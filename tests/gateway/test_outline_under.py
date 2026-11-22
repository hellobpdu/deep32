"""get_outline under= against deep32-engine release/p1.1, and still right against release/p1 (a rollback).

p1.1 cuts the subtree itself and echoes `under`; its 404 for an unknown section or document is final. p1 has no
such parameter (FastAPI drops unknown query parameters), so the edge cuts the subtree itself from the depth-6
outline, as edge v2 did; so it does after a 422 or a bare 404 for the request with `under`. That outline stops at
heading level 6, which a note says when it matters. core_p1 and core_p11 below answer as those releases do.
"""

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import unittest

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from support import FakeCore, reply
from rag_edge import DEEP_NOTE, OUTLINE_CHARS, PREAMBLE_NOTE, build_server

DOC, TITLE = "cr", "EIGRP Command Reference"
# (anchor, level, heading, n_chars) in document order. Level 0 is the text before the first heading, as deep32-engine
# stores it: section id "<doc>#preamble", heading null.
LAYOUT = [
    ("preamble", 0, None, 90),
    ("a-through-h", 1, "A through H", 1000),
    ("area", 2, "area", 400),
    ("s-through-v", 1, "S through V", 2000),
    ("eigrp-stub", 2, "eigrp stub", 900),
    ("syntax-description", 3, "Syntax Description", 300),
    ("examples", 4, "Examples", 100),
    ("example-output", 5, "Example output", 80),
    ("field-descriptions", 6, "Field descriptions", 60),
    ("summary-address", 2, "summary-address", 700),
    ("related-commands", 3, "Related Commands", 200),
    ("appendix", 1, "Appendix", 50),
]
SECTIONS = [{"section_id": f"{DOC}#{anchor}", "level": level, "heading": heading, "n_chars": n_chars,
             "line_start": 10 * i + 1, "line_end": 10 * i + 10}
            for i, (anchor, level, heading, n_chars) in enumerate(LAYOUT)]


def core_p1(request: httpx.Request, sections: list | None = None) -> httpx.Response:
    """GET /v2/outline as release/p1 answers it (api_v2.get_outline at d118e92): it has no `under` parameter."""
    sections = SECTIONS if sections is None else sections
    params = request.url.params
    if params.get("doc", "").lower() != DOC:
        return httpx.Response(404, json={"detail": f"unknown document: {params.get('doc')}"})
    depth = int(params.get("depth", 3))
    return httpx.Response(200, json={"doc_key": DOC, "title": TITLE, "degraded": [],
                                     "sections": [s for s in sections if s["level"] <= depth]})


def core_p11(request: httpx.Request, sections: list | None = None) -> httpx.Response:
    """GET /v2/outline as release/p1.1 answers it (api_v2.get_outline and api_v2.subtree at b953da3)."""
    sections = SECTIONS if sections is None else sections
    params = request.url.params
    if "under" not in params or params.get("doc", "").lower() != DOC:
        return core_p1(request, sections)
    depth, under = int(params.get("depth", 3)), params["under"].strip()
    ref = under if "#" in under else f"{DOC}#{under}"
    start = next((i for i, s in enumerate(sections) if s["section_id"] == ref), None)
    if start is None:
        return httpx.Response(404, json={"detail": f"unknown section in {DOC}: {under}"})
    top = sections[start]
    out = [top]
    if top["level"] > 0:  # the preamble is nobody's parent
        for s in sections[start + 1:]:
            if s["level"] <= top["level"]:
                break
            if s["level"] <= top["level"] + depth:
                out.append(s)
    return httpx.Response(200, json={"doc_key": DOC, "title": TITLE, "under": ref, "sections": out, "degraded": []})


def deep_note_expected(under: str, depth: int) -> bool:
    """Whether the edge's cut of core p1's depth-6 outline may miss deeper headings: the requested levels go past 6
    and the cut reaches level 6 (where headings of level 7 or more would hang)."""
    ref = under if "#" in under else f"{DOC}#{under}"
    start = next(i for i, s in enumerate(SECTIONS) if s["section_id"] == ref)
    top = SECTIONS[start]["level"]
    if top == 0 or top + depth <= 6:
        return False
    if top >= 6:
        return True
    for s in SECTIONS[start + 1:]:
        if s["level"] <= top:
            return False
        if s["level"] >= 6:
            return True
    return False


S_THROUGH_V_DEPTH_2 = [
    '<outline doc="cr" title="EIGRP Command Reference" under="cr#s-through-v">',
    "S through V [cr#s-through-v] 2000 chars",
    "  eigrp stub [cr#eigrp-stub] 900 chars",
    "    Syntax Description [cr#syntax-description] 300 chars",
    "  summary-address [cr#summary-address] 700 chars",
    "    Related Commands [cr#related-commands] 200 chars",
    "</outline>",
]


class ToolCase(unittest.IsolatedAsyncioTestCase):
    def serve(self, routes):
        self.fake = FakeCore(routes)
        self.mcp = build_server(self.fake.core())

    async def call(self, name, **arguments):
        result = await self.mcp.call_tool(name, arguments)
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.content), 1)
        return result.content[0].text

    async def assert_tool_error(self, name, fragment, **arguments):
        with self.assertRaises(ToolError) as caught:
            await self.mcp.call_tool(name, arguments)
        self.assertIn(fragment, str(caught.exception))


class OutlineUnderTest(ToolCase):
    def serve_outline(self, outline):
        self.serve({("GET", "/v2/outline"): outline})

    async def test_core_p11_cuts_the_subtree_in_one_request(self):
        self.serve_outline(core_p11)
        text = await self.call("get_outline", doc="cr", under="cr#s-through-v", depth=2)
        self.assertEqual(self.fake.paths(), ["GET /v2/outline"])
        self.assertEqual(self.fake.params(), {"doc": "cr", "depth": "2", "under": "cr#s-through-v"})
        self.assertEqual(text.splitlines(), S_THROUGH_V_DEPTH_2)

    async def test_an_anchor_goes_to_core_as_given(self):
        # deep32-engine resolves an anchor within the document under its own doc_key, whatever the case of doc=
        self.serve_outline(core_p11)
        text = await self.call("get_outline", doc="CR", under="eigrp-stub", depth=1)
        self.assertEqual(self.fake.params(), {"doc": "CR", "depth": "1", "under": "eigrp-stub"})
        self.assertEqual(text.splitlines(), ['<outline doc="cr" title="EIGRP Command Reference" under="cr#eigrp-stub">',
                                             "  eigrp stub [cr#eigrp-stub] 900 chars",
                                             "    Syntax Description [cr#syntax-description] 300 chars",
                                             "</outline>"])

    async def test_core_p1_ignores_under_so_the_edge_cuts_the_subtree(self):
        self.serve_outline(core_p1)
        text = await self.call("get_outline", doc="cr", under="cr#s-through-v", depth=2)
        self.assertEqual(self.fake.paths(), ["GET /v2/outline", "GET /v2/outline"])
        self.assertEqual(self.fake.params(0), {"doc": "cr", "depth": "2", "under": "cr#s-through-v"})
        self.assertEqual(self.fake.params(1), {"doc": "cr", "depth": "6"})
        self.assertEqual(text.splitlines(), S_THROUGH_V_DEPTH_2)
        # an anchor is completed with the doc_key deep32-engine answered, not with the case the caller typed
        text = await self.call("get_outline", doc="CR", under="eigrp-stub", depth=1)
        self.assertEqual(text.splitlines()[0],
                         '<outline doc="cr" title="EIGRP Command Reference" under="cr#eigrp-stub">')

    async def test_at_depth_6_core_p1s_answer_is_already_the_whole_outline(self):
        self.serve_outline(core_p1)
        text = await self.call("get_outline", doc="cr", under="s-through-v", depth=6)
        self.assertEqual(self.fake.paths(), ["GET /v2/outline"])
        self.assertIn("          Field descriptions [cr#field-descriptions] 60 chars", text)

    async def test_core_p1_and_p11_answer_alike(self):
        # The one difference: after cutting core p1's depth-6 outline the edge says when deeper headings may be
        # missing (DEEP_NOTE); core p1.1 lists them itself.
        servers = []
        for outline in (core_p1, core_p11):  # one server each: building one per call is slow
            fake = FakeCore({("GET", "/v2/outline"): outline})
            servers.append((fake, build_server(fake.core())))
        anchors = [a for a, *_ in LAYOUT] + ["cr#s-through-v", "cr#preamble"]
        noted = 0
        for fmt in ("text", "json"):
            for under in anchors:
                for depth in range(1, 7):
                    with self.subTest(format=fmt, under=under, depth=depth):
                        answers = []
                        for fake, mcp in servers:
                            result = await mcp.call_tool("get_outline", {"doc": "cr", "under": under, "depth": depth,
                                                                         "format": fmt})
                            self.assertFalse(result.is_error)
                            answers.append(result.content[0].text)
                        deep = deep_note_expected(under, depth)
                        noted += deep
                        self.assertNotIn(DEEP_NOTE.strip("[]"), answers[1])
                        if fmt == "json":
                            answers = [json.loads(a) for a in answers]
                            self.assertEqual(answers[1]["under"], under if "#" in under else f"cr#{under}")
                            notes = answers[0].get("notes", [])
                            self.assertEqual(DEEP_NOTE.strip("[]") in notes, deep)
                            if deep:
                                notes.remove(DEEP_NOTE.strip("[]"))
                                if not notes:
                                    del answers[0]["notes"]
                        else:
                            self.assertEqual(answers[0].endswith("\n" + DEEP_NOTE), deep)
                            answers[0] = answers[0].removesuffix("\n" + DEEP_NOTE)
                        self.assertEqual(answers[0], answers[1])
        # the cuts that reach level 6 while depth asks for more: 1 + 1 + 2 + 3 + 4 + 5 + 6 per format
        self.assertEqual(noted, 2 * 22)
        p1, p11 = (fake.requests for fake, _ in servers)
        self.assertEqual(len(p11), 2 * len(anchors) * 6)  # p1.1: one request each
        self.assertEqual(len(p1), 2 * len(anchors) * 11)  # p1: two, except at depth 6

    async def test_headings_below_level_6(self):
        # A level-7 heading: core p1.1 lists it, core p1's depth-6 outline cannot, and the edge says so.
        deep = SECTIONS[:9] + [{"section_id": f"{DOC}#fields-2", "level": 7, "heading": "More fields",
                                "n_chars": 40}] + SECTIONS[9:]
        for outline, shown in ((core_p11, True), (core_p1, False)):
            with self.subTest(outline=outline.__name__):
                self.serve_outline(lambda request, outline=outline: outline(request, deep))
                text = await self.call("get_outline", doc="cr", under="eigrp-stub", depth=6)
                self.assertEqual("More fields [cr#fields-2]" in text, shown)
                self.assertEqual(text.endswith("</outline>\n" + DEEP_NOTE), not shown)
                data = json.loads(await self.call("get_outline", doc="cr", under="eigrp-stub", depth=6,
                                                  format="json"))
                self.assertEqual(data.get("notes"), None if shown else [DEEP_NOTE.strip("[]")])
                if not shown:
                    await self.assert_tool_error("get_outline", "no section cr#fields-2 in this document; list its "
                                                 "chapters with depth=1 (this deep32-engine lists headings down to level "
                                                 "6 only)", doc="cr", under="fields-2")

    async def test_the_preamble_is_nobodys_parent(self):
        for outline in (core_p1, core_p11):
            with self.subTest(outline=outline.__name__):
                self.serve_outline(outline)
                text = await self.call("get_outline", doc="cr", under="preamble", depth=6)
                self.assertEqual(text.splitlines()[1:], ["(preamble) [cr#preamble] 90 chars", "</outline>",
                                                         PREAMBLE_NOTE])
                data = json.loads(await self.call("get_outline", doc="cr", under="cr#preamble", format="json"))
                self.assertEqual(data["notes"], [PREAMBLE_NOTE.strip("[]")])
                self.assertEqual([s["section_id"] for s in data["sections"]], ["cr#preamble"])
        self.assertIn("get_outline without under lists the chapters", PREAMBLE_NOTE)

    async def test_a_422_or_a_bare_404_for_under_falls_back_to_the_edge_cut(self):
        refusals = {"422": reply(422, {"detail": [{"loc": ["query", "under"], "msg": "Extra inputs are not permitted",
                                                   "type": "extra_forbidden"}]}),
                    "bare 404": reply(404, {"detail": "Not Found"})}
        for name, refusal in refusals.items():
            with self.subTest(refusal=name):
                self.serve({("GET", "/v2/outline"): [refusal, core_p1], ("GET", "/v2/status"): {"ok": True}})
                text = await self.call("get_outline", doc="cr", under="cr#s-through-v", depth=2)
                outline_requests = [r for r in self.fake.requests if r.url.path == "/v2/outline"]
                self.assertEqual([r.url.params.get("under") for r in outline_requests], ["cr#s-through-v", None])
                self.assertEqual(self.fake.params(), {"doc": "cr", "depth": "6"})
                self.assertEqual(text.splitlines(), S_THROUGH_V_DEPTH_2)

    async def test_core_p11s_404_for_an_unknown_section_is_final(self):
        # edge v2.1 fetched the whole depth-6 outline (up to ~920 KB) before giving the same answer
        self.serve_outline(core_p11)
        await self.assert_tool_error("get_outline", "no section cr#nope in this document; list its chapters with "
                                     "depth=1", doc="CR", under="nope")
        self.assertEqual(self.fake.paths(), ["GET /v2/outline"])
        self.assertEqual(self.fake.params(), {"doc": "CR", "depth": "3", "under": "nope"})
        await self.assert_tool_error("get_outline", "no section cr#nope in this document", doc="cr", under="cr#nope")
        self.assertEqual(len(self.fake.requests), 2)

    async def test_an_unknown_section(self):
        for outline, requests in ((core_p1, 2), (core_p11, 1)):
            with self.subTest(outline=outline.__name__):
                self.serve_outline(outline)
                with self.assertRaises(ToolError) as caught:
                    await self.mcp.call_tool("get_outline", {"doc": "cr", "under": "nope"})
                message = str(caught.exception)
                self.assertIn("no section cr#nope in this document; list its chapters with depth=1", message)
                self.assertEqual(message.endswith("(this deep32-engine lists headings down to level 6 only)"),
                                 outline is core_p1)
                self.assertEqual(len(self.fake.requests), requests)

    async def test_an_unknown_document(self):
        for outline in (core_p1, core_p11):
            with self.subTest(outline=outline.__name__):
                self.serve_outline(outline)
                await self.assert_tool_error("get_outline", "unknown document: nosuch", doc="nosuch", under="x")
                self.assertEqual(self.fake.paths(), ["GET /v2/outline"])

    async def test_other_failures_are_reported_without_the_fallback(self):
        cases = {"(HTTP 503)": reply(503, {"detail": "busy"}, {"Retry-After": "1"}),
                 "deep32-engine failed (HTTP 500)": reply(500, {"detail": "boom"}),
                 "deep32-engine rejected the request (HTTP 400)": reply(400)}
        for fragment, answer in cases.items():
            with self.subTest(fragment=fragment):
                self.serve_outline(answer)
                await self.assert_tool_error("get_outline", fragment, doc="cr", under="cr#s-through-v")
                self.assertTrue(self.fake.requests)
                self.assertTrue(all(r.url.params.get("under") == "cr#s-through-v" for r in self.fake.requests))

    async def test_a_core_without_api_v2(self):
        self.serve({})  # every route is FastAPI's bare 404, /v2/status included
        await self.assert_tool_error("get_outline", "this deep32-engine predates API v2", doc="cr", under="x")
        self.assertEqual(self.fake.paths(), ["GET /v2/outline", "GET /v2/status"])

    async def test_json_names_the_section(self):
        for outline in (core_p1, core_p11):
            with self.subTest(outline=outline.__name__):
                self.serve_outline(outline)
                data = json.loads(await self.call("get_outline", doc="cr", under="summary-address", depth=1,
                                                  format="json"))
                self.assertEqual(data["under"], "cr#summary-address")
                self.assertEqual([s["section_id"] for s in data["sections"]],
                                 ["cr#summary-address", "cr#related-commands"])
                self.assertNotIn("truncated", data)

    async def test_a_huge_subtree_from_core_is_cut_too(self):
        top = {"section_id": "g#top", "level": 1, "heading": "Top", "n_chars": 1}
        many = [{"section_id": f"g#h-{i}", "level": 2, "heading": "h" * 100, "n_chars": 5000} for i in range(2000)]
        self.serve_outline({"doc_key": "g", "title": "Guide", "under": "g#top", "sections": [top] + many})
        text = await self.call("get_outline", doc="g", under="top")
        self.assertEqual(self.fake.paths(), ["GET /v2/outline"])
        self.assertLess(len(text), OUTLINE_CHARS + 500)
        shown = text.count("[g#")
        self.assertTrue(text.endswith(f"[outline cut at {shown} of 2001 headings: use depth=1, then "
                                      "under=<section id> to list one part]"))
        data = json.loads(await self.call("get_outline", doc="g", under="top", format="json"))
        self.assertEqual(data["truncated"], {"shown": shown, "total": 2001})

    async def test_degraded_is_shown(self):
        answer = {"doc_key": DOC, "title": TITLE, "under": "cr#appendix", "degraded": ["structure"],
                  "sections": [SECTIONS[-1]]}
        self.serve_outline(answer)
        text = await self.call("get_outline", doc="cr", under="appendix")
        self.assertTrue(text.startswith('degraded: structure\n<outline doc="cr"'))


if __name__ == "__main__":
    unittest.main()
