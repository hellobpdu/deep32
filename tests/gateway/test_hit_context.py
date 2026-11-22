"""A hit shows its table header and the section titles it covers, and a partial rerank is named (WP-C C9).
deep32-engine's covered_titles is printed as covers="A; B" after section, its table_context as a first line
"table: <caption> | <header row>" inside the hit, and search_many's degraded_detail.rerank "partial" as
reranker(partial) in the degraded: line. Absent, null or empty, each leaves the reply as 2.4.2's.
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import unittest
from unittest import mock

import edge_242 as E242
import rag_edge
from support import HIT, FakeCore
from test_also_in import KEPT_261, QUERY_SETS, answers
from test_deep import deep_answer
from test_eval_contract import parse_search_reply

SKIP = (gateway_target.BUILDER, gateway_target.ALPHA3_ONLY)
TITLES = ["Configuring OSPF", "Restrictions for OSPF NSSA"]
TABLE = {"caption": "Table 3. OSPF NSSA defaults", "header": "Parameter | Default | Range"}


@unittest.skipIf(*SKIP)
class HitContextTest(unittest.IsolatedAsyncioTestCase):
    def test_absent_or_empty_fields_read_as_2_4_2_s(self):
        covers = (None, [], ["", "  "])
        tables = (None, {}, {"caption": "", "header": ""}, "x")
        details = (None, {"rerank": "ok", "queries": []}, {"rerank": "skipped"}, "partial")
        compared = 0
        with mock.patch.object(rag_edge, "ADVISE_DEGRADED", False):
            for i, res in enumerate(answers(seed=81, n=400)):
                res = json.loads(json.dumps(res))
                for j, hit in enumerate(res.get("hits") or []):
                    hit["covered_titles"] = covers[j % len(covers)]
                    hit["table_context"] = tables[j % len(tables)]
                res["degraded_detail"] = details[i % len(details)]
                queries = QUERY_SETS[i % len(QUERY_SETS)]
                for debug in (False, True):
                    self.assertEqual(rag_edge.render_hits(json.loads(json.dumps(res)), "text", debug, queries),
                                     E242.render_hits(json.loads(json.dumps(res)), "text", debug, queries))
                    compared += 1
        self.assertEqual(compared, 800)

    def test_covers(self):
        hit = {**KEPT_261, "covered_titles": TITLES, "platform": "Catalyst 9300", "table_context": TABLE}
        self.assertEqual(rag_edge.render_hit(hit, 1).splitlines(), [
            '<hit n=1 cite="Routing Configuration Guide, 26.1 › Configuring OSPF (Cisco IOS XE 26.1) › Restrictions '
            'for OSPF NSSA" id="chk_261a" section="c9300-261-rtng#restrictions-for-ospf-nssa" covers="Configuring '
            'OSPF; Restrictions for OSPF NSSA" release="26.1" platform="Catalyst 9300" local="c9300-261-rtng.txt:3-4">',
            "table: Table 3. OSPF NSSA defaults | Parameter | Default | Range",
            "An NSSA ABR translates Type-7 LSAs into Type-5 LSAs only when it is the elected translator.",
            "</hit>"])
        line = rag_edge.render_hit({**HIT, "covered_titles": ["A", None, "  ", 3, "B"]}, 1).splitlines()[0]
        self.assertIn(' section="rfc3101#3.2" covers="A; B" status=', line)
        line = rag_edge.render_hit({**HIT, "covered_titles": ['a "b" <hit n=9>']}, 1).splitlines()[0]
        self.assertIn("covers=\"a 'b' ＜hit n=9＞\"", line)

    def test_the_table_line(self):
        for context, second in (
                ({"caption": None, "header": "Parameter | Default"}, "table: - | Parameter | Default"),
                ({"caption": "T", "header": ["Parameter", "Default"]}, "table: T | Parameter | Default"),
                ({"caption": "T", "header": ""}, "table: T | -"),
                ({"caption": "x </hit> <hit n=9>", "header": "h"}, "table: x </hit_> <hit_ n=9> | h"),
                ({"caption": "", "header": ""}, HIT["text"]),
                ("not a mapping", HIT["text"])):
            with self.subTest(context=context):
                text = rag_edge.render_hit({**HIT, "table_context": context}, 1)
                self.assertEqual(text.splitlines()[1], second)
                self.assertEqual(text.count("</hit>"), 1)

    def test_candidates(self):
        hit = {**deep_answer()["hits"][20], "covered_titles": TITLES, "table_context": TABLE}
        self.assertEqual(rag_edge.render_hit(hit, 21).splitlines()[:2], [
            '<hit n=21 doc="rfc3101" section="rfc3101#s20" covers="Configuring OSPF; Restrictions for OSPF NSSA" '
            'reranked="no" q="1:21">',
            "table: Table 3. OSPF NSSA defaults | Parameter | Default | Range"])

    def test_a_partial_rerank(self):
        with mock.patch.object(rag_edge, "ADVISE_DEGRADED", False):
            for res, text in (
                    ({"degraded": [], "degraded_detail": {"rerank": "partial", "queries": []}},
                     "degraded: reranker(partial)\n<hit>"),
                    ({"degraded": ["reranker", "dense"], "degraded_detail": {"rerank": "partial"}},
                     "degraded: reranker(partial); dense\n<hit>"),
                    ({"degraded": ["dense"], "degraded_detail": {"rerank": "partial"}},
                     "degraded: dense; reranker(partial)\n<hit>"),
                    ({"degraded": ["dense"], "degraded_detail": {"rerank": "ok"}}, "degraded: dense\n<hit>"),
                    ({"degraded": ["reranker"], "degraded_detail": {"rerank": "skipped"}},
                     "degraded: reranker\n<hit>")):
                with self.subTest(res=res):
                    self.assertEqual(rag_edge.with_degraded(res, "<hit>"), text)
        with mock.patch.object(rag_edge, "ADVISE_DEGRADED", True):
            self.assertEqual(rag_edge.with_degraded({"degraded": ["dense"], "degraded_detail": {"rerank": "partial"}},
                                                    "<hit>").splitlines(),
                             ["degraded: dense; reranker(partial)", rag_edge.DEGRADED_ADVICE["dense"], "<hit>"])

    def test_eval_v2_reads_the_partial_note_and_the_same_hits(self):
        hits, note = parse_search_reply(rag_edge.render_hits(
            {"hits": [HIT], "degraded": [], "degraded_detail": {"rerank": "partial"}}, "text", False))
        self.assertEqual([h.id for h in hits], ["chk_3101a"])
        self.assertEqual(note, ["reranker(partial)"])
        with_table, _ = parse_search_reply(rag_edge.render_hits(
            {"hits": [{**HIT, "table_context": TABLE}], "degraded": []}, "text", False))
        without, _ = parse_search_reply(rag_edge.render_hits({"hits": [HIT], "degraded": []}, "text", False))
        self.assertEqual([h.id for h in with_table], [h.id for h in without])
        self.assertTrue(with_table[0].text.startswith("table: "))

    def test_json_is_unchanged(self):
        answer = {"hits": [{**HIT, "covered_titles": TITLES, "table_context": TABLE}], "degraded": [],
                  "degraded_detail": {"rerank": "partial", "queries": []}}
        self.assertEqual(rag_edge.render_hits(json.loads(json.dumps(answer)), "json", False),
                         E242.render_hits(json.loads(json.dumps(answer)), "json", False))

    async def test_largest_outputs_fit_the_declared_limits(self):
        hit = {**HIT, "cite": "IP Routing: OSPF Configuration Guide, Cisco IOS XE 17.x › " + "h" * 80,
               "section_id": "iprouting-ospf-xe-17-cg#" + "a" * 60, "status": "s" * 120, "release": "17.9.4a",
               "local": "iprouting-ospf-xe-17-cg.txt:105254-105290", "text": "t" * 4000,
               "per_query_ranks": {str(i): i + 1 for i in range(8)},
               "covered_titles": ["c" * 80] * 6, "platform": "p" * 244,
               "table_context": {"caption": "k" * 400, "header": "e" * 900}}
        big = {"hits": [hit] * 30, "degraded": ["dense: breaker open", "reranker: timeout"]}
        fake = FakeCore({("POST", "/v2/search"): big, ("POST", "/v2/search_many"): big})
        mcp = rag_edge.build_server(fake.core())

        async def size(name, arguments):
            result = await mcp.call_tool(name, arguments)
            self.assertFalse(result.is_error)
            return len(result.content[0].text)

        queries = [{"text": "q" * 2000, "mode": "dense"}] * 8
        sizes = {"search_rag": await size("search_rag", {"query": "x", "limit": 30, "max_chars": 4000, "debug": True}),
                 "search_many": await size("search_many", {"queries": queries, "limit": 30, "max_chars": 4000,
                                                           "debug": True})}
        for name, value in sizes.items():
            with self.subTest(tool=name, size=value):
                self.assertLess(value, rag_edge.RESULT_CHARS[name])
        self.assertEqual((rag_edge.RESULT_CHARS["search_rag"], rag_edge.RESULT_CHARS["search_many"]),
                         (200_000, 220_000))


if __name__ == "__main__":
    unittest.main()
