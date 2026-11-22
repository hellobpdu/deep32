"""list_documents narrows by doc_key prefix, release and platform, shows a platform column when deep32-engine reports
each document's platform, and declares its result size (WP-C C7). The filters run in the gateway on the engine's list;
nothing new is sent to the engine. Without filters and without a platform on any document the reply is 2.4.2's.
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import unittest

from mcp.server.mcpserver.exceptions import ToolError

import rag_edge
from support import FakeCore, make_app, rpc, serve
from test_also_in import ToolCase

SKIP = (gateway_target.BUILDER, gateway_target.ALPHA3_ONLY)

# handoff/scratch-WP-C/cases.py, section C7
DOCS = {"degraded": [], "opt_in": {"powershell": 241, "system": 85}, "documents": [
    {"doc_key": "c9300-179-rtng", "title": "Routing Configuration Guide, 17.9", "doc_type": "cisco",
     "release": "17.9.x", "platform": "Catalyst 9300", "status": None, "n_sections": 900, "n_chunks": 1200,
     "source_url": "https://x"},
    {"doc_key": "iosxe17-rtr-iprouting-cg", "title": "IP Routing Configuration Guide", "doc_type": "cisco",
     "release": "17.x", "platform": "Cisco IOS XE routers (Catalyst 8000 / ISR 4000 / ASR 1000)", "status": None,
     "n_sections": 4560, "n_chunks": 9000, "source_url": "https://y"},
    {"doc_key": "rfc3101", "title": "The OSPF NSSA Option", "doc_type": "rfc", "release": None, "platform": None,
     "status": "Proposed Standard; obsoletes RFC 1587", "n_sections": 40, "n_chunks": 61, "source_url": "https://z"}]}

COLUMNS = "doc_key | type | release | platform | status | sections/chunks | title"
ROW_179 = "c9300-179-rtng | cisco | 17.9.x | Catalyst 9300 | - | 900/1200 | Routing Configuration Guide, 17.9"
ROW_RTR = ("iosxe17-rtr-iprouting-cg | cisco | 17.x | Cisco IOS XE routers (Catalyst 8000 / ISR 4000 / ASR 1000) | - | "
           "4560/9000 | IP Routing Configuration Guide")
ROW_RFC = "rfc3101 | rfc | - | - | Proposed Standard; obsoletes RFC 1587 | 40/61 | The OSPF NSSA Option"
OPT_IN = "[opt-in, not listed: powershell 241 documents, system 85 documents; list one with doc_type=]"

# handoff/scratch-WP-C/cases.out.txt, section C7
FILTERED = [
    ({"prefix": "C9300-"}, [f'1 of 3 documents match prefix="C9300-": {COLUMNS}', ROW_179]),
    ({"release": "17.9"}, [f'1 of 3 documents match release="17.9": {COLUMNS}', ROW_179]),
    ({"release": "17", "platform": "ROUTER"}, [f'1 of 3 documents match release="17", platform="ROUTER": {COLUMNS}',
                                              ROW_RTR]),
    ({"prefix": "nosuch"}, [f'0 of 3 documents match prefix="nosuch": {COLUMNS}']),
]


def tagged(lines):
    return ["<documents>", *lines, OPT_IN, "</documents>"]


@unittest.skipIf(*SKIP)
class DocumentFilterTest(ToolCase):
    async def test_the_platform_column_comes_with_the_engine(self):
        self.serve({("GET", "/v2/documents"): DOCS})
        text = await self.call("list_documents")
        self.assertEqual(text.splitlines(), tagged([f"3 documents: {COLUMNS}", ROW_179, ROW_RTR, ROW_RFC]))
        self.assertEqual(self.fake.params(), {})

    async def test_filters(self):
        self.serve({("GET", "/v2/documents"): DOCS})
        for arguments, lines in FILTERED:
            with self.subTest(arguments=arguments):
                self.assertEqual((await self.call("list_documents", **arguments)).splitlines(), tagged(lines))
                self.assertEqual(self.fake.params(), {})
                await self.call("list_documents", doc_type="cisco", **arguments)
                self.assertEqual(self.fake.params(), {"doc_type": "cisco"})

    async def test_release_matches_by_numbers(self):
        docs = [{"doc_key": f"d{i}", "doc_type": "cisco", "release": r} for i, r in
                enumerate(["17.9.x", "17.9.4a", "17.18.x", "17.x", None])]
        self.serve({("GET", "/v2/documents"): {"documents": docs, "degraded": []}})
        for release, keys in (("17.9", ["d0", "d1"]), ("17", ["d0", "d1", "d2", "d3"]), ("17.18", ["d2"])):
            with self.subTest(release=release):
                answer = json.loads(await self.call("list_documents", release=release, format="json"))
                self.assertEqual([d["doc_key"] for d in answer["documents"]], keys)

    async def test_platform_on_an_engine_without_it(self):
        docs = [{k: v for k, v in d.items() if k != "platform"} for d in DOCS["documents"]]
        self.serve({("GET", "/v2/documents"): {**DOCS, "documents": docs}})
        with self.assertRaises(ToolError) as caught:
            await self.mcp.call_tool("list_documents", {"platform": "9300"})
        self.assertEqual(str(caught.exception), f"Error executing tool list_documents: {rag_edge.NO_PLATFORMS}")
        lines = (await self.call("list_documents", prefix="rfc")).splitlines()
        self.assertEqual(lines[1], "1 of 3 documents match prefix=\"rfc\": doc_key | type | release | status | "
                                   "sections/chunks | title")

    async def test_json(self):
        self.serve({("GET", "/v2/documents"): DOCS})
        self.assertEqual(json.loads(await self.call("list_documents", format="json")), DOCS)
        self.assertEqual(json.loads(await self.call("list_documents", release="17.9", format="json")),
                         {**DOCS, "documents": [DOCS["documents"][0]]})

    async def test_result_size(self):
        async with serve(make_app(FakeCore())) as http:
            tools = {t["name"]: t for t in (await rpc(http, "tools/list")).json()["result"]["tools"]}
        self.assertEqual(tools["list_documents"]["_meta"]["anthropic/maxResultSizeChars"], 200_000)
        docs = [{"doc_key": f"c9300-1718-rtng-{i:06d}", "doc_type": "cisco", "release": "17.18.x", "platform": "P" * 40,
                 "status": "S" * 20, "n_sections": 4560, "n_chunks": 9000, "title": "T" * 70} for i in range(854)]
        self.serve({("GET", "/v2/documents"): {"documents": docs, "degraded": []}})
        text = await self.call("list_documents")
        self.assertLess(len(text), rag_edge.RESULT_CHARS["list_documents"])

    async def test_the_description(self):
        self.serve({})
        tool = {t.name: t for t in await self.mcp.list_tools()}["list_documents"]
        self.assertIn("Pass doc_type to list one collection: the whole list is over 85k chars. prefix, release and "
                      "platform narrow it further.", " ".join(tool.description.split()))


if __name__ == "__main__":
    unittest.main()
