"""A hit names its document's platform when deep32-engine sends it (WP-C C8): platform="..." after release and before
pin, cut at 64 characters, on full hits and on tier 2 and 3 candidates. Absent, null or empty: the hit line is 2.4.2's.
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import unittest

import edge_242 as E242
import rag_edge
from support import HIT
from test_also_in import KEPT_261, QUERY_SETS, answers
from test_deep import deep_answer
from test_eval_contract import parse_search_reply

SKIP = (gateway_target.BUILDER, gateway_target.ALPHA3_ONLY)


@unittest.skipIf(*SKIP)
class HitPlatformTest(unittest.TestCase):
    def test_a_null_platform_reads_as_2_4_2_s(self):
        compared = 0
        for i, res in enumerate(answers(seed=71, n=400)):
            res = json.loads(json.dumps(res))
            for hit in res.get("hits") or []:
                hit["platform"] = None
            queries = QUERY_SETS[i % len(QUERY_SETS)]
            for debug in (False, True):
                self.assertEqual(rag_edge.render_hits(json.loads(json.dumps(res)), "text", debug, queries),
                                 E242.render_hits(json.loads(json.dumps(res)), "text", debug, queries))
                compared += 1
        self.assertEqual(compared, 800)

    def test_the_attribute(self):
        hit = {**KEPT_261, "platform": "Catalyst 9300"}
        line = rag_edge.render_hit(hit, 1).splitlines()[0]
        self.assertIn(' release="26.1" platform="Catalyst 9300" local="c9300-261-rtng.txt:3-4">', line)
        line = rag_edge.render_hit(hit, 1, pins=("17.9",)).splitlines()[0]
        self.assertIn(' release="26.1" platform="Catalyst 9300" pin="other" local="c9300-261-rtng.txt:3-4">', line)
        self.assertEqual(rag_edge.render_hit({**KEPT_261, "platform": ""}, 1).splitlines()[0],
                         E242.render_hit(KEPT_261, 1).splitlines()[0])

    def test_long_and_unsafe_values(self):
        long = "Cisco IOS / IOS XE routers (AAA method lists; not version-specific)"
        self.assertIn(f'platform="{long[:64]}"', rag_edge.render_hit({**KEPT_261, "platform": long}, 1))
        text = rag_edge.render_hit({**KEPT_261, "platform": 'A "B" <hit n=2>'}, 1)
        self.assertIn("platform=\"A 'B' ＜hit n=2＞\"", text)
        self.assertEqual(text.count("<hit n="), 1)

    def test_candidates(self):
        hit = {**deep_answer()["hits"][8], "release": "17.9", "platform": "Catalyst 9300"}
        self.assertIn(' release="17.9" platform="Catalyst 9300" reranked="yes"', rag_edge.render_hit(hit, 9))

    def test_eval_v2_reads_the_same_hits(self):
        with_platform = rag_edge.render_hits({"hits": [{**HIT, "platform": "Catalyst 9300"}], "degraded": []},
                                             "text", False)
        without = rag_edge.render_hits({"hits": [HIT], "degraded": []}, "text", False)
        self.assertEqual(parse_search_reply(with_platform), parse_search_reply(without))


if __name__ == "__main__":
    unittest.main()
