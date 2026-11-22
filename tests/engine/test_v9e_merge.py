"""v9e package: deep32/tiers (deep=true) and relpref/prefer-release (prefer_release) together, with
retrieval.json fold_identical on (prefer_release does nothing without it).

Each alone is pinned by its own tests (test_deep_tiers: without deep, 6afc29b's bodies; test_prefer_release: without
prefer_release, 6afc29b's and 4f4fe78's bodies). Here: on a deep answer, prefer_release changes only who a hit is
(id, document, section, citation, standing, release, source lines, url, its read ref), never its place, score,
matched retrievers, text, tier or reranked flag, and the hit and its copies stay the same chunks; and a deep
candidate's get_section always names the section it is shown as (the v9e merge's one addition to prefer).
"""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import copy
import types
import unittest

from rag_core.api_v2 import DEEP_HITS, HitBuilder, pinned_release, release_pins

from fold_root import REQUESTS, masked, post
from test_prefer_release import PINS, Served

KEPT = ("score", "matched", "text", "tier", "reranked")


class DeepPreferTests(Served, unittest.TestCase):
    retrieval = {"fold_identical": True}

    def answers(self, extra):
        out = {}
        for name, (path, body, rerank) in REQUESTS.items():
            r = post(self.c, path, {**copy.deepcopy(body), **extra}, rerank)
            self.assertEqual(r.status_code, 200, (name, r.text))
            out[name] = r.json()
        return out

    def test_on_a_deep_answer_only_who_a_hit_is_changes(self):
        deep = self.answers({"deep": True})
        swapped = 0
        for tag, pins in PINS.items():
            got = self.answers({"deep": True, "prefer_release": pins})
            keys = release_pins(pins)
            for name in REQUESTS:
                with self.subTest(f"{name}+{tag}"):
                    before, now = deep[name], got[name]
                    self.assertEqual(list(now), list(before))
                    self.assertEqual(len(now["hits"]), len(before["hits"]))
                    self.assertLessEqual(len(now["hits"]), DEEP_HITS)
                    for a, b in zip(before["hits"], now["hits"]):
                        self.assertEqual(list(b), list(a))                  # the same keys, in the same order
                        self.assertIn("tier", b)
                        for key in KEPT:
                            self.assertEqual(b[key], a[key], key)
                        self.assertEqual({b["id"], *(m["id"] for m in b["also_in"])},
                                         {a["id"], *(m["id"] for m in a["also_in"])})
                        if "get_section" in b:
                            self.assertEqual(b["get_section"], b["section_id"] or b["id"])
                        if a["id"] == b["id"]:
                            self.assertEqual(b, a)
                            continue
                        swapped += 1
                        self.assertFalse(pinned_release(a["release"], keys))
                        self.assertTrue(pinned_release(b["release"], keys))
                        self.assertEqual(b["also_in"][0]["id"], a["id"])
        self.assertGreater(swapped, 0)

    def test_deep_false_with_prefer_release_is_the_answer_without_deep(self):
        for tag, pins in PINS.items():
            for name, (path, body, rerank) in REQUESTS.items():
                with self.subTest(f"{name}+{tag}"):
                    sent = {**copy.deepcopy(body), "prefer_release": pins}
                    self.assertEqual(masked(post(self.c, path, {**sent, "deep": False}, rerank).content),
                                     masked(post(self.c, path, sent, rerank).content))   # timings masked


class PreferReadRefTests(unittest.TestCase):
    """HitBuilder.prefer on a deep candidate (tier 2 or 3, which carries get_section): when its delivered window is
    a pinned copy's text byte for byte (a short passage, whose narrow window is the whole of it), the hit is shown as
    that copy, and its read ref follows: the copy's section, or its chunk id without a structure index."""

    @staticmethod
    def builder(sec):
        hb = HitBuilder.__new__(HitBuilder)          # prefer needs only the pins and the folded copies
        hb.pins = release_pins(["17.9"])
        hb.db = None
        doc = types.SimpleNamespace(doc_key="c9300-179-rtng", title="Routing 17.9", doc_type="cisco", release="17.9",
                                    platform="Catalyst 9300", rfc=None, cite=lambda s: "Routing 17.9 › NSSA",
                                    url=lambda s: None)
        row = {"chunk_id": "chk_179", "doc_id": "doc_179", "heading_path": ["NSSA"]}
        hb.copies = {"chk_179": (row, doc, ("short passage", sec, "c9300-179-rtng.txt:3-4"))}
        return hb

    @staticmethod
    def hit(**extra):
        return {"id": "chk_261", "doc_id": "doc_261", "doc_key": "c9300-261-rtng", "title": "Routing 26.1",
                "doc_type": "cisco", "cite": "Routing 26.1 › NSSA", "section_id": "c9300-261-rtng#nssa",
                "section_path": ["NSSA"], "status": None, "release": "26.1", "platform": None, "score": 0.5,
                "matched": ["bm25"],
                "text": "short passage", "local": "c9300-261-rtng.txt:3-4", "url": None,
                "also_in": [{"id": "chk_179", "doc_key": "c9300-179-rtng", "release": "17.9",
                             "section_id": "c9300-179-rtng#nssa", "cite": "Routing 17.9 › NSSA"}], **extra}

    def test_a_swapped_candidate_reads_its_new_section(self):
        sec = types.SimpleNamespace(section_id="c9300-179-rtng#nssa", path=["NSSA"], anchor="nssa")
        for tier in (2, 3):
            with self.subTest(tier=tier):
                hit = self.hit(tier=tier, reranked=False, get_section="c9300-261-rtng#nssa")
                keys = list(hit)
                self.builder(sec).prefer([hit])
                self.assertEqual((hit["id"], hit["release"], hit["platform"], hit["section_id"]),
                                 ("chk_179", "17.9", "Catalyst 9300", "c9300-179-rtng#nssa"))
                self.assertEqual(hit["get_section"], "c9300-179-rtng#nssa")
                self.assertEqual(list(hit), keys)
                self.assertEqual(hit["also_in"][0]["id"], "chk_261")

    def test_without_a_structure_index_the_read_ref_is_the_chunk_id(self):
        hit = self.hit(tier=3, reranked=False, get_section="chk_261")
        self.builder(None).prefer([hit])
        self.assertEqual((hit["id"], hit["section_id"], hit["get_section"]), ("chk_179", None, "chk_179"))

    def test_a_hit_without_a_read_ref_gets_none(self):
        hit = self.hit(reranked=False)                # without deep: no get_section, as before
        self.builder(types.SimpleNamespace(section_id="c9300-179-rtng#nssa", path=["NSSA"], anchor="nssa")).prefer(
            [hit])
        self.assertEqual(hit["id"], "chk_179")
        self.assertNotIn("get_section", hit)


if __name__ == "__main__":
    unittest.main()
