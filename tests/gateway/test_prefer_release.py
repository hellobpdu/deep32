"""prefer_release (edge 2.4.3): search_rag and search_many pass the release pins to deep32-engine (its HitBuilder.prefer shows a
hit as the pinned copy among the identical-text copies folded into it), and the text output marks a Cisco hit from
another release pin="other". Without prefer_release every request body and every reply is 2.4.2's byte for byte
(test_also_in pins 2.4.1's rendering; here 2.4.2's own render_hits without pins); with it, a reply differs from the
one without only by the pin="other" attributes, which eval_v2's parser does not read.
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import random
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from support import HIT
from rag_edge import SubQuery, other_release, release_key, render_hit, render_hits
from test_also_in import COPY_179, COPY_1712, KEPT_261, QUERY_SETS, ToolCase, answers
from test_eval_contract import parse_search_reply

LAB = ["17.9", "20.9", "2.3", "3.1"]
CISCO_1718 = {**KEPT_261, "id": "chk_1718a", "doc_key": "c9300-1718-rtng", "release": "17.18",
              "cite": "Routing Configuration Guide, 17.18 › Restrictions", "local": "c9300-1718-rtng.txt:3-4"}
LABEL = ' pin="other"'


def cisco_answers(seed, n):
    """answers() with every other hit made a Cisco one, from a pinned or another release."""
    rng = random.Random(seed)
    for res in answers(seed=seed, n=n):
        for h in res["hits"]:
            if rng.random() < 0.5:
                h["doc_type"] = "cisco"
                h["release"] = rng.choice(["17.9", "17.9.4a", "17.18", "26.1", "20.9.1", "20.15", "2.3.7.9", "3.4",
                                           None, "", "n/a"])
            if rng.random() < 0.3:
                h["also_in"] = rng.choice([[], [COPY_179], [COPY_179, COPY_1712]])
        yield res


class RenderTest(unittest.TestCase):

    def test_without_pins_every_reply_is_the_one_before(self):
        n = 0
        for res in cisco_answers(seed=3, n=1500):
            for queries in QUERY_SETS:
                for fmt in ("text", "json"):
                    for debug in (False, True):
                        a = render_hits(json.loads(json.dumps(res)), fmt, debug, queries)
                        b = render_hits(json.loads(json.dumps(res)), fmt, debug, queries, (), ())
                        self.assertEqual(a, b)
                        n += 1
        self.assertEqual(n, 1500 * len(QUERY_SETS) * 4)

    def test_with_pins_only_the_label_is_added(self):
        labelled = 0
        for res in cisco_answers(seed=5, n=1500):
            for queries in QUERY_SETS:
                for debug in (False, True):
                    before = render_hits(json.loads(json.dumps(res)), "text", debug, queries)
                    now = render_hits(json.loads(json.dumps(res)), "text", debug, queries, (), tuple(LAB))
                    self.assertEqual(now.replace(LABEL, ""), before)
                    want = sum(other_release(h, tuple(LAB)) for h in res["hits"])
                    self.assertEqual(now.count(LABEL), want)
                    labelled += want
                    # format="json" is deep32-engine's payload, pins or not
                    self.assertEqual(render_hits(json.loads(json.dumps(res)), "json", debug, queries, (), tuple(LAB)),
                                     render_hits(json.loads(json.dumps(res)), "json", debug, queries))
                    # eval_v2 reads the same hits, copies and degraded notes
                    self.assertEqual(parse_search_reply(now), parse_search_reply(before))
        self.assertGreater(labelled, 1000)

    def test_which_hits_are_labelled(self):
        lab = tuple(LAB)
        for release in ("17.18", "17.18.x", "17.12.1", "26.1", "26.x", "20.15", "3.4", "2.2", "16.12", "16.x", "15.2"):
            self.assertTrue(other_release({"doc_type": "cisco", "release": release}, lab), release)
        # a pin, no numbered release, or a rolling book of a pin's own line (17.x, 20.x, 2.x, 3.x)
        for release in ("17.9", "17.9.x", "17.9.4a", "20.9", "20.9.1", "2.3", "2.3.7.9", "3.1", None, "", "n/a",
                        "17.x", "17", "20.x", "2.x", "3.x"):
            self.assertFalse(other_release({"doc_type": "cisco", "release": release}, lab), release)
        self.assertTrue(other_release({"doc_type": "cisco", "release": "17.x"}, ("20.9",)))
        for doc_type in ("rfc", "system", "powershell", None):
            self.assertFalse(other_release({"doc_type": doc_type, "release": "17.18"}, lab), doc_type)
        self.assertFalse(other_release({"doc_type": "cisco", "release": "17.18"}, ()))
        self.assertTrue(other_release({"doc_type": "cisco", "release": "17.9.3"}, ("17.9.4",)))
        self.assertEqual(release_key("17.11.1a"), (17, 11, 1))
        self.assertEqual(release_key("17.9(3)"), (17, 9))

    def test_the_label_stands_after_release(self):
        hit = render_hit(CISCO_1718, 1, pins=tuple(LAB))
        self.assertIn(' release="17.18" pin="other" local="c9300-1718-rtng.txt:3-4">', hit)
        self.assertEqual(hit.replace(LABEL, ""), render_hit(CISCO_1718, 1))
        self.assertEqual(render_hit(KEPT_261, 1, pins=("26.1",)), render_hit(KEPT_261, 1))


class ToolTest(ToolCase):

    async def test_without_prefer_release_the_bodies_are_2_4_2_s(self):
        self.serve({("POST", "/v2/search"): {"hits": [HIT], "degraded": []},
                    ("POST", "/v2/search_many"): {"hits": [HIT], "degraded": []}})
        # round B: deep=false (a call without limit is deep by default), so these are 2.4.2's default bodies
        await self.call("search_rag", query="x", deep=False)
        await self.call("search_many", queries=[{"text": "x"}], deep=False)
        await self.call("search_many", queries=[{"text": "x"}], release_max="17.9", deep=False)
        self.assertEqual(self.fake.body(0), {"query": "x", "limit": 8, "mode": "hybrid", "rerank": "auto",
                                             "max_chars": 2400, "filters": {"include_obsolete": True}})
        self.assertEqual(self.fake.body(1), {"queries": [{"text": "x", "mode": "hybrid", "weight": 1.0}], "limit": 8,
                                             "max_chars": 2400, "rerank": "auto"})
        self.assertEqual(self.fake.body(2)["filters"], {"release_max": "17.9"})
        self.assertNotIn("prefer_release", self.fake.body(2))

    async def test_prefer_release_is_passed_on(self):
        body = {"hits": [{**CISCO_1718, "also_in": [COPY_1712]}, {**KEPT_261, "release": "17.9"}, HIT], "degraded": []}
        self.serve({("POST", "/v2/search"): body, ("POST", "/v2/search_many"): body})
        text = await self.call("search_rag", query="x", prefer_release=LAB, limit=8)   # round B: limit, so not deep
        self.assertEqual(self.fake.body(0)["prefer_release"], LAB)
        self.assertEqual(list(self.fake.body(0))[-1], "prefer_release")
        self.assertEqual(text.count(LABEL), 1)                       # the 17.18 hit only
        self.assertIn('n=1 cite="Routing Configuration Guide, 17.18', text.splitlines()[0])
        self.assertIn(LABEL, text.splitlines()[0])
        many = await self.call("search_many", queries=[{"text": "x"}, {"text": "y", "mode": "bm25"}],
                               prefer_release=["17.9"], release_max="17.9")
        self.assertEqual(self.fake.body(1)["prefer_release"], ["17.9"])
        self.assertEqual(self.fake.body(1)["filters"], {"release_max": "17.9"})
        self.assertEqual(many.count(LABEL), 1)
        # format="json" is deep32-engine's payload as before (without debug: no score or matched retrievers)
        got = json.loads(await self.call("search_rag", query="x", prefer_release=LAB, format="json"))
        self.assertEqual(got["hits"], [{k: v for k, v in h.items() if k not in ("score", "matched")}
                                       for h in body["hits"]])

    async def test_values_the_tools_refuse(self):
        self.serve({("POST", "/v2/search"): {"hits": [], "degraded": []}})
        for value in ([], ["abc"], ["17.9"] * 9, ["17.9 x"]):
            for name, args in (("search_rag", {"query": "x"}), ("search_many", {"queries": [{"text": "x"}]})):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ToolError):
                        await self.mcp.call_tool(name, {**args, "prefer_release": value})
        self.assertEqual(self.fake.requests, [])

    async def test_the_tool_schema_names_it(self):
        self.serve({})
        listed = {t.name: t for t in await self.mcp.list_tools()}
        for name in ("search_rag", "search_many"):
            prop = listed[name].input_schema["properties"]["prefer_release"]
            self.assertIn('pin="other"', prop["description"])


if __name__ == "__main__":
    unittest.main()
