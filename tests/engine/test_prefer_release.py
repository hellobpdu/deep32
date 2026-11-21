"""A search or search_many request's prefer_release (rag_core.api_v2.HitBuilder.prefer), on top of retrieval.json
fold_identical: among the identical-text copies folded into one hit, a pinned release becomes the visible
citation; no rank, no delivered text and no fold changes.

Pinned bodies: tests/data/fold_off_4f4fe78.json (fold off, the bodies before the fold) and
tests/data/fold_on_6afc29b.json (fold on, captured from rag-stack 6afc29b, the v9d package, before prefer_release;
6afc29b ignored the field, so a request with it gave the same body there). Every fold_root.REQUESTS shape is sent as it is and with prefer_release
added (PINS):
  - fold off (unset, false or null): every body is 4f4fe78's byte for byte, prefer_release or not;
  - fold on without prefer_release: every body is 6afc29b's byte for byte;
  - fold on with prefer_release: a body differs from 6afc29b's only where a hit swapped to a pinned copy, and there
    only in who the hit is (id, document, section, citation, standing, release, source lines, url, per_query_ranks)
    and in also_in, which names the hit it was first; place, score, matched and text are 6afc29b's, and the hit and
    its copies are the same chunks.
"""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import copy
import json
import unittest
from pathlib import Path

from rag_core.api_v2 import pinned_release, release_pins

import fold_root
from fold_root import REQUESTS, load_pinned, post
from test_api_v2 import RootClient
from v2_root import chunk_id

DATA = Path(__file__).resolve().parent / "data"
OFF = DATA / "fold_off_4f4fe78.json"
ON = DATA / "fold_on_6afc29b.json"
PINS = {"pin179": ["17.9"], "pin261": ["26.1"], "pin1712": ["17.12"], "default": ["17.9", "20.9", "2.3", "3.1"]}
# what a swap leaves as it was, and what it takes from the copy (the rest of a hit's keys)
KEPT = ("score", "matched", "text")
WHO = ("id", "doc_id", "doc_key", "title", "doc_type", "cite", "section_id", "section_path", "status", "release",
       "local", "url")
ALSO_IN_KEYS = ["id", "doc_key", "release", "section_id", "cite"]


def shapes():
    """(name, path, body, rerank) for every REQUESTS shape, then the shape with each PINS prefer_release."""
    for name, (path, body, rerank) in REQUESTS.items():
        yield name, path, body, rerank
        for tag, pins in PINS.items():
            yield f"{name}+{tag}", path, {**copy.deepcopy(body), "prefer_release": pins}, rerank


class Served:
    retrieval = None

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=fold_root.prepare, retrieval=cls.retrieval)
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    @classmethod
    def bodies(cls):
        """{shape name: the raw response body}."""
        out = {}
        for name, path, body, rerank in shapes():
            r = post(cls.c, path, body, rerank)
            if r.status_code != 200:
                raise AssertionError(f"{name}: {r.status_code} {r.text}")
            out[name] = r.content
        return out


class FoldOffTests(Served, unittest.TestCase):
    """Fold off: prefer_release changes nothing at all (there are no copies to pick from)."""

    def test_every_body_is_4f4fe78_s_byte_for_byte(self):
        pinned = load_pinned(OFF)
        for name, content in self.bodies().items():
            with self.subTest(name):
                self.assertEqual(fold_root.masked(content), pinned[name.split("+")[0]])


class FoldFalseTests(FoldOffTests):
    retrieval = {"fold_identical": False}


class FoldNullTests(FoldOffTests):
    retrieval = {"fold_identical": None}


class FoldOnTests(Served, unittest.TestCase):
    retrieval = {"fold_identical": True}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raw = cls.bodies()
        cls.got = {k: json.loads(v) for k, v in cls.raw.items()}

    def setUp(self):
        self.on = {k: json.loads(v) for k, v in load_pinned(ON).items()}
        self.off = {k: json.loads(v) for k, v in load_pinned(OFF).items()}

    def test_the_pin_is_6afc29b_s(self):
        pinned = json.loads(ON.read_text(encoding="utf-8"))
        self.assertEqual(pinned["captured_from"], "6afc29b")
        self.assertIs(pinned["fold_identical"], True)
        self.assertEqual(set(pinned["bodies"]), set(REQUESTS))

    def test_without_prefer_release_every_body_is_6afc29b_s_byte_for_byte(self):
        pinned = load_pinned(ON)
        for name in REQUESTS:
            with self.subTest(name):
                self.assertEqual(fold_root.masked(self.raw[name]), pinned[name])

    def swaps(self, name):
        """[(place, 6afc29b's hit, the hit now)] where the hit is another chunk now."""
        before, now = self.on[name.split("+")[0]]["hits"], self.got[name]["hits"]
        return [(n, a, b) for n, (a, b) in enumerate(zip(before, now)) if a["id"] != b["id"]]

    def test_only_who_a_hit_is_changes(self):
        swapped = 0
        for name, _, body, _ in shapes():
            if "+" not in name:
                continue
            with self.subTest(name):
                pins = release_pins(body["prefer_release"])
                before, now = self.on[name.split("+")[0]], self.got[name]
                self.assertEqual(list(now), list(before))
                self.assertEqual(now["degraded"], before["degraded"])
                self.assertEqual(len(now["hits"]), len(before["hits"]))
                for a, b in zip(before["hits"], now["hits"]):
                    self.assertEqual(list(b), list(a))                  # the same keys, in the same order
                    for key in KEPT:
                        self.assertEqual(b[key], a[key], key)           # place, score, matched, text: 6afc29b's
                    # the hit and its copies are the same chunks
                    self.assertEqual({b["id"], *(m["id"] for m in b["also_in"])},
                                     {a["id"], *(m["id"] for m in a["also_in"])})
                    if a["id"] == b["id"]:
                        self.assertEqual(b, a)
                        continue
                    swapped += 1
                    self.assertFalse(pinned_release(a["release"], pins))
                    self.assertTrue(pinned_release(b["release"], pins))
                    # the hit it was comes first in also_in, named as a copy is (with its query ranks in search_many)
                    was = {k: a[k] for k in ALSO_IN_KEYS}
                    if "per_query_ranks" in a:
                        was["per_query_ranks"] = a["per_query_ranks"]
                    self.assertEqual(b["also_in"][0], was)
                    self.assertEqual(b["also_in"][1:], [m for m in a["also_in"] if m["id"] != b["id"]])
                    # and the copy it now is was named in 6afc29b's also_in with this citation
                    named = next(m for m in a["also_in"] if m["id"] == b["id"])
                    self.assertEqual({k: b[k] for k in ("doc_key", "release", "section_id", "cite")},
                                     {k: named[k] for k in ("doc_key", "release", "section_id", "cite")})
                    if "per_query_ranks" in named:
                        self.assertEqual(b["per_query_ranks"], named["per_query_ranks"])
        self.assertGreaterEqual(swapped, 9)

    def test_a_swapped_hit_is_the_copy_as_off_delivers_it(self):
        # off delivers the 26.1 copy as a hit of its own: who it is there is who the swapped hit is, and its text
        # there is the swapped hit's text byte for byte
        b = self.got["search_default+pin261"]["hits"][0]
        off = next(h for h in self.off["search_default"]["hits"] if h["id"] == b["id"])
        self.assertEqual(b["id"], chunk_id("c9300-261-rtng", 0))
        self.assertEqual({k: b[k] for k in WHO}, {k: off[k] for k in WHO})
        self.assertEqual(b["text"], off["text"])
        self.assertEqual(b["release"], "26.1")
        self.assertEqual([m["id"] for m in b["also_in"]], [chunk_id("c9300-179-rtng", 0)])

    def test_a_pinned_hit_stays(self):
        # 17.9 is the hit's own release in every shape: the release pins and 17.9 change nothing
        for name in REQUESTS:
            for tag in ("pin179", "default"):
                with self.subTest(f"{name}+{tag}"):
                    self.assertEqual(fold_root.masked(self.raw[f"{name}+{tag}"]), load_pinned(ON)[name])

    def test_a_copy_whose_window_differs_in_a_byte_stays_in_also_in(self):
        # the 17.12 copy folds by fold_key (case, punctuation, spacing and fullwidth letters aside), but its window
        # is not the hit's text byte for byte: never shown in the hit's place
        for name in REQUESTS:
            with self.subTest(name):
                self.assertEqual(fold_root.masked(self.raw[f"{name}+pin1712"]), load_pinned(ON)[name])
        first = self.got["search_docs+pin1712"]["hits"][0]
        self.assertIn(chunk_id("c9300-1712-rtng", 0), [m["id"] for m in first["also_in"]])

    def test_a_copy_overlapping_another_hit_s_lines_stays_in_also_in(self):
        # limit 3: the 26.1 copies of hit 1 lie inside hit 3's lines of the 26.1 guide
        now = self.got["search_limit3+pin261"]["hits"]
        self.assertEqual(now, self.on["search_limit3"]["hits"])
        self.assertEqual(now[2]["doc_key"], "c9300-261-rtng")
        self.assertEqual({m["doc_key"] for m in now[0]["also_in"]}, {"c9300-261-rtng"})

    def test_search_many_swaps_with_the_copy_s_query_ranks(self):
        for name in ("many_two", "many_weighted", "many_rerank"):
            with self.subTest(name):
                swaps = self.swaps(f"{name}+pin261")
                self.assertEqual(len(swaps), 1)
                _, a, b = swaps[0]
                self.assertEqual(b["also_in"][0]["per_query_ranks"], a["per_query_ranks"])

    def test_the_status_block_is_6afc29b_s(self):
        settings = self.c.get("/v2/status", headers=fold_root.AUTH).json()["settings"]
        self.assertEqual(list(settings)[-1], "fold_identical")
        self.assertNotIn("prefer_release", json.dumps(settings))


class RequestTests(Served, unittest.TestCase):
    retrieval = {"fold_identical": True}

    def test_values_it_refuses(self):
        for value in ([], ["17.9"] * 9, ["abc"], ["17.9 "], ["v17.9"], "17.9", [17.9], ["1.2.3.4.5"], ["x" * 40]):
            for path, body in (("/v2/search", {"query": "OSPF"}),
                               ("/v2/search_many", {"queries": [{"text": "OSPF"}]})):
                with self.subTest(path=path, value=value):
                    r = post(self.c, path, {**body, "prefer_release": value})
                    self.assertEqual(r.status_code, 422, r.text)

    def test_values_it_takes(self):
        for value in (None, ["17.9"], ["17.9", "20.9", "2.3", "3.1"], ["17.9.4a"], ["2.3.7.9"], ["17.9"] * 8):
            with self.subTest(value=value):
                r = post(self.c, "/v2/search", {"query": "OSPF", "prefer_release": value})
                self.assertEqual(r.status_code, 200, r.text)


class PinTests(unittest.TestCase):

    def test_release_pins(self):
        self.assertEqual(release_pins(None), ())
        self.assertEqual(release_pins(["17.9", "20.9", "2.3", "3.1"]), ((17, 9), (20, 9), (2, 3), (3, 1)))
        self.assertEqual(release_pins(["17.9", "17.9", "17.9.4a"]), ((17, 9), (17, 9, 4)))

    def test_pinned_release(self):
        pins = release_pins(["17.9", "20.9", "2.3", "3.1"])
        for release in ("17.9", "17.9.4a", "17.9.1", "20.9", "20.9.1", "2.3", "2.3.7.9", "3.1", "3.1.0.518"):
            self.assertTrue(pinned_release(release, pins), release)
        for release in (None, "", "17", "17.18", "17.12.1", "26.1", "20.15", "2.2", "3.4", "16.9", "1.79"):
            self.assertFalse(pinned_release(release, pins), release)
        self.assertFalse(pinned_release("17.9", ()))
        self.assertTrue(pinned_release("17.9.4a", release_pins(["17.9.4"])))
        self.assertFalse(pinned_release("17.9.3", release_pins(["17.9.4"])))


if __name__ == "__main__":
    unittest.main()
