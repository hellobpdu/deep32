"""RAG_EDGE_DEFAULT_PREFER (WP-C C1): with "pins", a search_rag or search_many call that gives no prefer_release is
sent with the release pins (RAG_EDGE_RELEASE_PINS, as numbers) as its prefer_release. Off (unset, empty or "off"),
every request and reply is 2.4.2's. deep32-engine's prefer_release changes which identical copy a hit is shown as and
nothing else: tests/engine/test_prefer_release.py FoldOnTests, whose PINS["default"] are these pins (LAB).
"""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import os
import subprocess
import unittest
from unittest import mock

import httpx

import rag_edge
from support import HIT
from test_also_in import COPY_179, KEPT_261, answers
from test_deep import CALLS, ToolCase as Case242
from test_eval_contract import parse_search_reply

LAB = ["17.9", "20.9", "2.3", "3.1"]   # tests/engine/test_prefer_release.py:37, PINS["default"]
SKIP = (gateway_target.BUILDER, gateway_target.ALPHA3_ONLY)


@unittest.skipIf(*SKIP)
class PinsTest(unittest.TestCase):
    def test_the_release_numbers_of_a_pins_sentence(self):
        self.assertEqual(rag_edge.pin_releases(rag_edge.DEFAULT_RELEASE_PINS), tuple(LAB))
        for text, want in (("IOS XE 17.9.x, ISE 3.1 patch 7", ("17.9", "3.1")),
                           ("Catalyst 9300 IOS XE 17.9.4a", ("17.9.4a",)),
                           ("IOS XE 17.9, IOS XE 17.9", ("17.9",)),
                           ("", ()), ("no numbers here", ()),
                           (", ".join(f"X {n}.1" for n in range(1, 11)), tuple(f"{n}.1" for n in range(1, 9)))):
            with self.subTest(text=text):
                self.assertEqual(rag_edge.pin_releases(text), want)

    def test_the_switch(self):
        for env, want in (({}, ()), ({"RAG_EDGE_DEFAULT_PREFER": ""}, ()), ({"RAG_EDGE_DEFAULT_PREFER": "off"}, ()),
                          ({"RAG_EDGE_DEFAULT_PREFER": " OFF "}, ()), ({"RAG_EDGE_DEFAULT_PREFER": "pins"}, tuple(LAB)),
                          ({"RAG_EDGE_DEFAULT_PREFER": " Pins "}, tuple(LAB)),
                          ({"RAG_EDGE_DEFAULT_PREFER": "pins", "RAG_EDGE_RELEASE_PINS": "IOS XE 17.12"}, ("17.12",))):
            with self.subTest(env=env):
                self.assertEqual(rag_edge.default_prefer(env), want)

    def test_a_bad_value_stops_start_up(self):
        for value in ("on", "yes", "17.9", "pins,off"):
            with self.subTest(value=value), self.assertRaises(RuntimeError) as caught:
                rag_edge.default_prefer({"RAG_EDGE_DEFAULT_PREFER": value})
            self.assertIn("RAG_EDGE_DEFAULT_PREFER", str(caught.exception))

    def test_pins_that_name_no_release_prefer_nothing(self):
        with self.assertLogs("rag_edge", "WARNING") as logs:
            self.assertEqual(rag_edge.default_prefer({"RAG_EDGE_DEFAULT_PREFER": "pins",
                                                      "RAG_EDGE_RELEASE_PINS": ""}), ())
        self.assertIn("names no release", logs.output[0])


@unittest.skipIf(*SKIP)
class InstructionsTest(unittest.TestCase):
    """The switch is read at import, so each case imports rag_edge in a fresh interpreter."""
    CODE = ("import sys; sys.path.insert(0, sys.argv[1]); import rag_edge; "
            "sys.stdout.buffer.write(rag_edge.INSTRUCTIONS.encode('utf-8'))")

    def instructions(self, **env):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("RAG_EDGE_")}
        done = subprocess.run([sys.executable, "-I", "-B", "-c", self.CODE, str(gateway_target.REPO)],
                              env={**clean, **env}, capture_output=True, timeout=120, check=True)
        return done.stdout.decode("utf-8")

    def test_the_sentence_comes_with_the_switch(self):
        off, on = self.instructions(), self.instructions(RAG_EDGE_DEFAULT_PREFER="pins")
        self.assertEqual(len(off), 1351)                       # d84ce2a's instructions, unchanged with the switch off
        self.assertNotIn(rag_edge.PREFER_NOTE, off)
        self.assertEqual(on, off.replace(" ISE 3.1.", " ISE 3.1." + rag_edge.PREFER_NOTE, 1))
        self.assertEqual(len(on), 1479)
        self.assertLessEqual(len(on), 2048)                    # Claude Code's cut (test_collections.CLAUDE_CODE_LIMIT)
        self.assertNotIn(rag_edge.PREFER_NOTE,
                         self.instructions(RAG_EDGE_DEFAULT_PREFER="pins", RAG_EDGE_RELEASE_PINS=""))


@unittest.skipIf(*SKIP)
class SwitchAbsentTest(Case242):
    async def test_every_search_is_2_4_2_s_with_the_switch_absent(self):
        n = 0
        with mock.patch.object(rag_edge, "DEFAULT_PREFER", ()):
            for res in answers(seed=61, n=40):
                if not res["hits"]:
                    continue                    # no hits: the opt-in lookup follows (test_tools pins that text)
                self.serve({("POST", "/v2/search"): res, ("POST", "/v2/search_many"): res})
                for name, arguments in CALLS:
                    with self.subTest(tool=name, arguments=arguments):
                        new = await self.call(self.mcp, name, arguments)
                        old = await self.call(self.old, name, {k: v for k, v in arguments.items() if k != "deep"})
                        self.assertEqual(new, old)
                        self.assertEqual(self.fake.body(), self.old_fake.body())
                        self.assertNotIn("prefer_release", self.fake.body())
                        n += 1
        self.assertGreater(n, 200)


@unittest.skipIf(*SKIP)
class SwitchOnTest(Case242):
    async def test_a_call_without_prefer_release_sends_the_pins(self):
        body = {"hits": [HIT], "degraded": []}
        self.serve({("POST", "/v2/search"): body, ("POST", "/v2/search_many"): body})
        with mock.patch.object(rag_edge, "DEFAULT_PREFER", tuple(LAB)):
            by_default = await self.call(self.mcp, "search_rag", {"query": "x", "limit": 8})
            explicit = await self.call(self.mcp, "search_rag", {"query": "x", "limit": 8, "prefer_release": LAB})
            many_default = await self.call(self.mcp, "search_many", {"queries": [{"text": "x"}], "limit": 8})
            many_explicit = await self.call(self.mcp, "search_many", {"queries": [{"text": "x"}], "limit": 8,
                                                                      "prefer_release": LAB})
            await self.call(self.mcp, "search_rag", {"query": "x"})              # no limit: deep by default
        self.assertEqual(self.fake.requests[0].content, self.fake.requests[1].content)  # the same bytes on the wire
        self.assertEqual(self.fake.body(0)["prefer_release"], LAB)
        self.assertEqual(list(self.fake.body(0))[-1], "prefer_release")
        self.assertEqual(by_default, explicit)
        self.assertEqual(self.fake.requests[2].content, self.fake.requests[3].content)
        self.assertEqual(many_default, many_explicit)
        self.assertEqual(list(self.fake.body(4))[-2:], ["prefer_release", "deep"])

    async def test_the_calls_own_prefer_release_wins(self):
        self.serve({("POST", "/v2/search"): {"hits": [HIT], "degraded": []}})
        with mock.patch.object(rag_edge, "DEFAULT_PREFER", tuple(LAB)):
            await self.call(self.mcp, "search_rag", {"query": "x", "limit": 8, "prefer_release": ["17.12"]})
        self.assertEqual(self.fake.body()["prefer_release"], ["17.12"])

    async def test_ranks_and_texts_are_the_same_with_and_without_the_pins(self):
        # deep32-engine's answers to the same request without and with prefer_release (HitBuilder.prefer, as
        # tests/engine/test_prefer_release.py pins it): the 26.1 hit is shown as its identical 17.9 copy, in the same
        # place with the same text, and the hit it was moves into also_in
        off = {"hits": [{**KEPT_261, "also_in": [COPY_179]}, HIT], "degraded": []}
        swapped = {**KEPT_261, **COPY_179, "local": "c9300-179-rtng.txt:3-4",
                   "also_in": [{k: KEPT_261[k] for k in ("id", "doc_key", "release", "section_id", "cite")}]}
        on = {"hits": [swapped, HIT], "degraded": []}

        def engine(request):
            return httpx.Response(200, json=on if "prefer_release" in json.loads(request.content) else off)

        self.serve({("POST", "/v2/search"): engine})
        with mock.patch.object(rag_edge, "DEFAULT_PREFER", ()):
            without = await self.call(self.mcp, "search_rag", {"query": "nssa", "limit": 8})
        with mock.patch.object(rag_edge, "DEFAULT_PREFER", tuple(LAB)):
            pinned = await self.call(self.mcp, "search_rag", {"query": "nssa", "limit": 8})
        a, b = parse_search_reply(without)[0], parse_search_reply(pinned)[0]
        self.assertEqual([h.text for h in b], [h.text for h in a])          # same texts in the same places
        self.assertEqual([h.id for h in a], ["chk_261a", HIT["id"]])
        self.assertEqual([h.id for h in b], ["chk_179a", HIT["id"]])        # only who hit 1 is changed
        self.assertNotIn('pin="other"', pinned)                             # 17.9 is a pin
        self.assertIn("also in (same text as hit 1): c9300-261-rtng [c9300-261-rtng#restrictions-for-ospf-nssa] "
                      "release 26.1", pinned)


if __name__ == "__main__":
    unittest.main()
