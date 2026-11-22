"""lookup_rfc names the end of the obsoleted-by chain and the RFC's date (WP-C C2). deep32-engine's /v2/rfc answer
carries `current` (kb.current_rfcs: each end of the chain with the RFCs it runs through) and `date`; the status line
names the end and tells the model to cite it, never an RFC that is itself obsolete. An answer without `current` or
`date` (a deep32-engine before them) reads as 2.4.2's.
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
from test_also_in import ToolCase

SKIP = (gateway_target.BUILDER, gateway_target.ALPHA3_ONLY)


def rfc(n, obsoleted_by=(), current=None, status="Proposed Standard", date="1994-03"):
    """A /v2/rfc answer as api_v2.rfc builds it from kb.rfc_info."""
    answer = {"rfc": n, "title": f"Test RFC {n}", "status": status, "date": date, "in_corpus": True,
              "obsoletes": [], "obsoleted_by": list(obsoleted_by), "updates": [], "updated_by": [], "also": [],
              "errata": [], "errata_rejected": 0, "standing": "x", "doc_key": f"rfc{n}", "degraded": []}
    if current is not None:
        answer["current"] = current
    return answer


# a chain of three, 1001 -> 1002 -> 1003, each answer as kb.current_rfcs computes it
CHAIN = {1001: rfc(1001, [1002], [{"rfc": 1003, "via": [1002]}]),
         1002: rfc(1002, [1003], [{"rfc": 1003, "via": []}]),
         1003: rfc(1003, status="Internet Standard", current=[])}


@unittest.skipIf(*SKIP)
class ChainTest(ToolCase):
    async def lookup(self, answer):
        self.serve({("GET", f"/v2/rfc/{answer['rfc']}"): answer})
        return await self.call("lookup_rfc", rfc=answer["rfc"])

    async def test_every_rfc_of_a_chain_of_three_names_its_end(self):
        texts = {n: await self.lookup(a) for n, a in CHAIN.items()}
        self.assertEqual(texts[1001].splitlines(), [
            "<rfc>", "RFC 1001: Test RFC 1001", "published: 1994-03",
            "status: Proposed Standard; OBSOLETED by RFC 1002; current: RFC 1003 (via 1002): cite RFC 1003 for "
            "current behaviour",
            "obsoletes: - | updates: - | updated by: -", "corpus: rfc1001", "errata: 0", "</rfc>"])
        self.assertEqual(texts[1002].splitlines()[3],
                         "status: Proposed Standard; OBSOLETED by RFC 1003: cite RFC 1003 for current behaviour")
        self.assertEqual(texts[1003].splitlines()[3], "status: Internet Standard")
        for n, text in texts.items():
            with self.subTest(rfc=n):
                for wrong in ("cite RFC 1001", "cite RFC 1002", "cite the newer RFC"):
                    self.assertNotIn(wrong, text)

    async def test_the_shapes_the_engine_can_send(self):
        cases = {
            "two successors, one end": (rfc(5246, [8446, 9846], [{"rfc": 9846, "via": []}]),
                "status: Proposed Standard; OBSOLETED by RFC 8446, RFC 9846; current: RFC 9846: cite RFC 9846 for "
                "current behaviour"),
            "two ends": (rfc(100, [200, 300], [{"rfc": 300, "via": []}, {"rfc": 400, "via": [200]}]),
                "status: Proposed Standard; OBSOLETED by RFC 200, RFC 300; current: RFC 300, RFC 400 (via 200): cite "
                "the one that covers the behaviour"),
            "cycle": (rfc(10, [11], []),
                "status: Proposed Standard; OBSOLETED by RFC 11; the end of its obsoleted-by chain is not known"),
            "long": (rfc(5000, [5001], [{"rfc": 5040, "via": list(range(5001, 5040))}]),
                "status: Proposed Standard; OBSOLETED by RFC 5001; current: RFC 5040 (via 5001, 5002, 5003 +36 "
                "more): cite RFC 5040 for current behaviour"),
            "malformed current": (rfc(1587, [3101], [{"rfc": "3101", "via": []}]),
                "status: Proposed Standard; OBSOLETED by RFC 3101: cite the newer RFC for current behaviour"),
            "current not a list": (rfc(1587, [3101], "RFC 3101"),
                "status: Proposed Standard; OBSOLETED by RFC 3101: cite the newer RFC for current behaviour"),
        }
        for name, (answer, line) in cases.items():
            with self.subTest(name):
                self.assertEqual((await self.lookup(answer)).splitlines()[3], line)

    def test_an_answer_without_current_or_date_reads_as_2_4_2_s(self):
        for answer in (rfc(1587, [3101], date=None), rfc(4271, date=None), {**rfc(1587, [3101], date=None),
                       "errata": [{"id": 1, "status": "Verified", "type": "Technical", "section": "3",
                                   "summary": "Fix."}]}):
            with self.subTest(rfc=answer["rfc"]):
                self.assertEqual(rag_edge.render_rfc(answer), E242.render_rfc(answer))
        dated = rfc(4271)
        self.assertEqual(rag_edge.render_rfc(dated),
                         E242.render_rfc(dated).replace("RFC 4271: Test RFC 4271\n",
                                                        "RFC 4271: Test RFC 4271\npublished: 1994-03\n", 1))

    async def test_json_is_the_engine_answer(self):
        self.serve({("GET", "/v2/rfc/1001"): CHAIN[1001]})
        self.assertEqual(json.loads(await self.call("lookup_rfc", rfc=1001, format="json")), CHAIN[1001])


if __name__ == "__main__":
    unittest.main()
