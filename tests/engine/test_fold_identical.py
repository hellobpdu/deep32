"""retrieval.json fold_identical (rag_core.api_v2.HitBuilder.build, the X2 replay's identical-text arm A_exact_nopin:
staging/improve-2026-11/X2-REPLAY.md, x2/x2lib.py deliver_fold), off by default.

Unset (or false or null), every response is the one rag-stack 4f4fe78 sent: tests/data/fold_off_4f4fe78.json holds
the bodies of tests/fold_root.py's request shapes on its fold fixture (sibling copies of a Cisco guide), captured
from 4f4fe78 and compared byte for byte (timing values masked), and the /v2/status settings block is p81r's exact
string (ops/kb-refresh-p9.sh P81R_SETTINGS). Set true: a candidate whose chunk text AND delivered window are each
identical (fold_key) to a hit's folds into that hit (also_in), the next candidate takes its place, and the walk is
the replay's own with the window rule (checked against a copy of x2lib.deliver_fold's exact/no-pin walk with the
window rule, staging/fold/replay_window.py, on many candidate orders); a copy whose window differs is delivered as
off delivers it, so a fold never hides text; the block gains "fold_identical":true last; the start refuses a value
it cannot take; derive_root sets it per root."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import random
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rag_core import api, api_v2
from rag_core.api_v2 import HitBuilder, fold_identical, fold_key
from rag_core.db import connect
from rag_core.paths import RAGPaths
from rag_core.reader import chunk_rows, chunk_window, query_terms
from rag_core.status import StatusService

import fold_root
import test_derive_root
from fold_root import AUTH, REQUESTS, alpha2_form, capture, load_pinned, post
from test_api_v2 import HIT_KEYS, RootClient
from test_derive_trials import SAFE_ARG
from test_status import Clock, make_root
from v2_root import chunk_id

PINNED = Path(__file__).resolve().parent / "data" / "fold_off_4f4fe78.json"
# ops/kb-refresh-p9.sh P81R_SETTINGS (:119): the block the production chain compares as a string
P81R_SETTINGS = ('{"reranker":{"candidates":15,"search_many_candidates":10,"max_chars":1600,"window":"centred"},'
                 '"search_many":{"parallel":0,"rerank":"per_query"},"grep":{"regex_engine":true},'
                 '"serving":{"max_concurrent_searches":2,"max_queued_searches":16}}')
P81R_RETRIEVAL = {"grep": {"regex_engine": True},
                  "reranker": {"enabled": True, "url": "https://gpu-host.tailnet-example.ts.net:8443/v1/rerank",
                               "candidates": 15, "max_chars": 1600, "window": "centred",
                               "search_many_candidates": 10}}
FOLD_ON = {"fold_identical": True}
ALSO_IN_KEYS = ["id", "doc_key", "release", "section_id", "cite"]


def replay_tokens(text):
    """x2lib.tokens (the replay's copy rule, algorithms-tools/dups.py), copied: the [a-z0-9]+ runs of the
    NFKC-lower-cased text."""
    import unicodedata
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text or "").lower()))


def replay_walk(order, window, text, limit, same_window=True):
    """x2lib.deliver_fold(mode="exact", pin=False, seed=None), the walk the X2 replay ran for A_exact_nopin, with
    the window rule (staging/fold/replay_window.py deliver_fold_window: a copy is the same chunk tokens AND the same
    delivered-window tokens), copied with its own window-overlap rule (l1/windows.py _lr, overlaps): `window(cid)`
    is (pieces, local) or (None, None) for a candidate without a row, `text(cid)` its chunk text. [{"rep",
    "members"}] in rank order. same_window=False: the chunk tokens alone (the earlier rule, the replay's own)."""
    def lr(local):
        m = re.fullmatch(r"(.+):(\d+)-(\d+)", local or "")
        return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None

    def overlaps(a, b):
        if a is None or b is None or a[0] != b[0]:
            return False
        shared = min(a[2], b[2]) - max(a[1], b[1]) + 1
        return shared * 2 >= min(a[2] - a[1], b[2] - b[1]) + 1

    def copy(a, b):                 # Texts.relation(mode "exact") is "same", and so are the delivered windows
        ta, tb = replay_tokens(text(a)), replay_tokens(text(b))
        if not (ta and tb and ta == tb):
            return False
        if not same_window:
            return True
        wa, wb = (replay_tokens(" ".join(window(c)[0])) for c in (a, b))
        return bool(wa) and bool(wb) and wa == wb

    slots = []
    for cid in order:
        pieces, local = window(cid)
        if pieces is None:
            continue
        s = next((s for s in slots if any(copy(cid, m) for m in s["members"])), None)
        if s is not None:
            if cid not in s["members"]:
                s["members"].append(cid)
            continue
        if len(slots) >= limit:
            continue
        where = lr(local)
        if where and any(overlaps(where, o["where"]) for o in slots if o["where"]):
            continue
        slots.append({"rep": cid, "members": [cid], "where": where})
    return slots


def production_walk(order, window, limit):
    """l1/score.py Scorer.deliver (the replay's control, equal to production's HitBuilder on 696/696), copied:
    window-overlap dedupe, the first `limit` kept."""
    def lr(local):
        m = re.fullmatch(r"(.+):(\d+)-(\d+)", local or "")
        return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None

    def overlaps(a, b):
        if a[0] != b[0]:
            return False
        shared = min(a[2], b[2]) - max(a[1], b[1]) + 1
        return shared * 2 >= min(a[2] - a[1], b[2] - b[1]) + 1

    out, shown = [], []
    for cid in order:
        pieces, local = window(cid)
        if pieces is None:
            continue
        where = lr(local)
        if where and any(overlaps(where, o) for o in shown):
            continue
        if where:
            shown.append(where)
        out.append(cid)
        if len(out) == limit:
            break
    return out


class Fixture:
    """One fold fixture root served once per class (RootClient with fold_root.prepare)."""
    retrieval = None

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=fold_root.prepare, retrieval=cls.retrieval)
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def body(self, name):
        """The answer in its alpha.2 form (fold_root.alpha2_form): the pinned bodies are compared with it."""
        path, body, rerank = REQUESTS[name]
        r = post(self.c, path, body, rerank)
        self.assertEqual(r.status_code, 200, r.text)
        return json.loads(alpha2_form(r.content, body))


class OffTests(Fixture, unittest.TestCase):
    """Unset: the bodies 4f4fe78 sent."""

    def test_the_pinned_bodies_are_4f4fe78_s_and_hold_copies(self):
        pinned = json.loads(PINNED.read_text(encoding="utf-8"))
        self.assertEqual(pinned["captured_from"], "4f4fe78")
        self.assertEqual(set(pinned["bodies"]), set(REQUESTS))
        # the pin is worth something: 4f4fe78 delivered identical-text copies on most shapes
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            copies = 0
            for text in pinned["bodies"].values():
                ids = [h["id"] for h in json.loads(text)["hits"]]
                keys = [fold_key(r["text"]) for r in map(chunk_rows(conn, ids).get, ids)]
                copies += len(keys) - len(set(keys))
        self.assertGreaterEqual(copies, 10)

    def test_unset_every_response_is_byte_for_byte_4f4fe78_s(self):
        self.assertEqual(capture(self.c), load_pinned(PINNED))

    def test_no_hit_carries_also_in(self):
        for name in REQUESTS:
            with self.subTest(name):
                for hit in self.body(name)["hits"]:
                    self.assertNotIn("also_in", hit)
                    self.assertLessEqual(set(hit), HIT_KEYS | {"per_query_ranks"})


class FalseTests(OffTests):
    """false is the default: the same bodies."""
    retrieval = {"fold_identical": False}


class NullTests(OffTests):
    """null (derive_root --set retrieval.fold_identical=null) too."""
    retrieval = {"fold_identical": None}


class OnTests(Fixture, unittest.TestCase):
    """Set: the copies whose chunk text and delivered window are both identical fold into the hit they copy, and the
    next candidates refill; a copy whose window differs is delivered as off delivers it."""
    retrieval = FOLD_ON

    def setUp(self):
        self.off = {k: json.loads(v) for k, v in load_pinned(PINNED).items()}

    def keys_of(self, ids):
        """The chunk text fold_key of each chunk id."""
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            rows = chunk_rows(conn, ids)
        return [fold_key(rows[i]["text"]) for i in ids]

    def pairs_of(self, hits):
        """(chunk text fold_key, delivered text fold_key) of each hit: what the fold compares."""
        return list(zip(self.keys_of([h["id"] for h in hits]), [fold_key(h["text"]) for h in hits]))

    def test_no_two_hits_share_a_text_and_the_count_is_kept(self):
        refilled = folds = 0
        for name, (_, req, _) in REQUESTS.items():
            with self.subTest(name):
                on, off = self.body(name), self.off[name]
                limit = req.get("limit", 8)
                ids = [h["id"] for h in on["hits"]]
                pairs = self.pairs_of(on["hits"])
                self.assertEqual(len(set(pairs)), len(pairs))
                # a member has its hit's chunk text, and off delivered it (when it did) with the very text its hit
                # delivers on: the fold hides no text off showed
                off_text = {h["id"]: h["text"] for h in off["hits"]}
                for h, (key, _) in zip(on["hits"], pairs):
                    for m in h["also_in"]:
                        self.assertEqual(self.keys_of([m["id"]]), [key])
                        if m["id"] in off_text:
                            self.assertEqual(fold_key(off_text[m["id"]]), fold_key(h["text"]))
                            folds += 1
                # every hit off delivered is delivered on, or folded into a hit on (also_in)
                folded = {m["id"] for h in on["hits"] for m in h["also_in"]}
                self.assertLessEqual({h["id"] for h in off["hits"]}, set(ids) | folded)
                # a full list stays full (this fixture's pools hold enough other passages); a list the candidates
                # cut short loses at most its copies
                off_ids = [h["id"] for h in off["hits"]]
                off_pairs = self.pairs_of(off["hits"])
                copies = len(off_pairs) - len(set(off_pairs))
                if len(off_ids) == limit:
                    self.assertEqual(len(ids), limit)
                else:
                    self.assertGreaterEqual(len(ids), len(off_ids) - copies)
                self.assertLessEqual(len(ids), limit)
                refilled += len(set(ids) - set(off_ids))
                self.assertEqual(on["degraded"], off["degraded"])
        self.assertGreaterEqual(refilled, 10)
        self.assertGreaterEqual(folds, 10)

    def test_search_default_folds_the_sibling_whose_window_is_the_same_and_refills(self):
        on, off = self.body("search_default"), self.off["search_default"]
        first = on["hits"][0]
        # the best-ranked copy is kept as it was (no swap); the 26.1 sibling, whose window is the same, is named
        self.assertEqual({k: v for k, v in first.items() if k != "also_in"}, off["hits"][0])
        self.assertEqual(list(first), list(off["hits"][0]) + ["also_in"])
        self.assertEqual([m["id"] for m in first["also_in"]], [off["hits"][1]["id"]])
        self.assertEqual(fold_key(off["hits"][1]["text"]), fold_key(first["text"]))
        self.assertEqual([list(m) for m in first["also_in"]], [ALSO_IN_KEYS])
        self.assertEqual(first["also_in"][0], {
            "id": chunk_id("c9300-261-rtng", 0), "doc_key": "c9300-261-rtng", "release": "26.1",
            "section_id": "c9300-261-rtng#restrictions-for-ospf-nssa",
            "cite": "Routing Configuration Guide, 26.1 › Configuring OSPF (Cisco IOS XE 26.1) › "
                    "Restrictions for OSPF NSSA"})
        # the 17.12 copy has the same chunk text (case, punctuation, spacing and fullwidth letters aside), but its
        # window shows other text here: it is not folded, it is the hit off delivered
        second = on["hits"][1]
        self.assertEqual(second["id"], chunk_id("c9300-1712-rtng", 0))
        self.assertEqual(self.keys_of([second["id"]]), self.keys_of([first["id"]]))
        self.assertNotEqual(fold_key(second["text"]), fold_key(first["text"]))
        self.assertEqual({k: v for k, v in second.items() if k != "also_in"}, off["hits"][2])
        self.assertEqual(second["also_in"], [])
        # the freed place goes to the next candidates: off's other hits, in order, then a new one
        rest_off = [h["id"] for h in off["hits"][2:]]
        ids = [h["id"] for h in on["hits"]]
        self.assertEqual(len(ids), 8)
        self.assertEqual(ids[1:7], rest_off)
        self.assertTrue(set(ids) - {h["id"] for h in off["hits"]})        # a passage off did not deliver

    def test_a_copy_whose_window_differs_is_delivered_as_off_delivers_it(self):
        # chunk 2 of the 26.1 guide is chunk 2 of 17.9's word for word, but its section goes on with other text
        # (the "show ip ospf neighbor (again)" lines), so its window differs: both are hits, each as off has it
        on, off = self.body("search_cisco"), self.off["search_cisco"]
        a, b = chunk_id("c9300-179-rtng", 2), chunk_id("c9300-261-rtng", 2)
        self.assertEqual(self.keys_of([a]), self.keys_of([b]))
        by_on, by_off = {h["id"]: h for h in on["hits"]}, {h["id"]: h for h in off["hits"]}
        self.assertNotEqual(fold_key(by_on[a]["text"]), fold_key(by_on[b]["text"]))
        for cid in (a, b):
            self.assertEqual({k: v for k, v in by_on[cid].items() if k != "also_in"}, by_off[cid])
            self.assertEqual(by_on[cid]["also_in"], [])
        # the 26.1 chunk 0, whose window is 17.9's, still folds in this request
        first = by_on[chunk_id("c9300-179-rtng", 0)]
        self.assertEqual([m["id"] for m in first["also_in"]], [chunk_id("c9300-261-rtng", 0)])

    def test_the_17_12_copy_folds_where_its_window_is_the_same(self):
        # this query centres every window on the first line: 17.12's chunk 0 then shows 17.9's words exactly
        # (only case, punctuation, spacing and fullwidth letters differ), so it folds after the 26.1 copy
        on, off = self.body("search_docs"), self.off["search_docs"]
        first = on["hits"][0]
        self.assertEqual(first["id"], chunk_id("c9300-179-rtng", 0))
        self.assertEqual([m["id"] for m in first["also_in"]],
                         [chunk_id("c9300-261-rtng", 0), chunk_id("c9300-1712-rtng", 0)])
        self.assertEqual(first["also_in"][1]["release"], "17.12")
        off_text = {h["id"]: h["text"] for h in off["hits"]}
        for m in first["also_in"]:
            self.assertEqual(fold_key(off_text[m["id"]]), fold_key(first["text"]))
        self.assertNotEqual(off_text[chunk_id("c9300-1712-rtng", 0)], first["text"])     # not byte for byte

    def test_a_near_copy_is_not_folded(self):
        # chunk 1 of the 26.1 guide differs from 17.9's in one word ("the"): two hits, each with its own text
        on = self.body("search_cisco")
        ids = [h["id"] for h in on["hits"]]
        self.assertIn(chunk_id("c9300-179-rtng", 1), ids)
        self.assertIn(chunk_id("c9300-261-rtng", 1), ids)
        for hit in on["hits"]:
            if hit["id"] in (chunk_id("c9300-179-rtng", 1), chunk_id("c9300-261-rtng", 1)):
                self.assertEqual(hit["also_in"], [])

    def test_a_copy_past_the_last_place_is_still_listed(self):
        # limit 3: the 26.1 guide's chunk 4 (chunk 3 again, same document) ranks after the third hit
        on = self.body("search_limit3")
        self.assertEqual(len(on["hits"]), 3)
        self.assertEqual([m["id"] for m in on["hits"][0]["also_in"]],
                         [chunk_id("c9300-261-rtng", 3), chunk_id("c9300-261-rtng", 4)])

    def test_search_many_folds_with_each_copy_s_query_ranks(self):
        for name in ("many_two", "many_weighted", "many_rerank"):
            with self.subTest(name):
                on = self.body(name)
                members = [m for h in on["hits"] for m in h["also_in"]]
                self.assertTrue(members)
                for m in members:
                    self.assertEqual(list(m), ALSO_IN_KEYS + ["per_query_ranks"])
                    self.assertTrue(m["per_query_ranks"])

    def test_the_status_block_shows_it_last(self):
        settings = self.c.get("/v2/status", headers=AUTH).json()["settings"]
        self.assertEqual(list(settings)[-1], "fold_identical")
        self.assertIs(settings["fold_identical"], True)


class OffStatusTests(Fixture, unittest.TestCase):

    def test_the_status_block_does_not_name_it(self):
        settings = self.c.get("/v2/status", headers=AUTH).json()["settings"]
        self.assertNotIn("fold_identical", settings)


class ReplayWalkTests(Fixture, unittest.TestCase):
    """HitBuilder.build with the fold on is the X2 replay's A_exact_nopin walk with the window rule (replay_walk, a
    copy of x2lib.deliver_fold with replay_window.py's copy test) on many candidate orders of the fixture, and with
    it off production's walk."""

    retrieval = FOLD_ON

    def builder(self, fold, max_chars):
        request = SimpleNamespace(app=self.c.app)
        hb = HitBuilder(request, None, max_chars, set())
        self.assertIs(hb.fold, True)             # the root serves fold_identical (FOLD_ON)
        hb.fold = fold
        return hb

    def test_the_walk_is_the_replay_s(self):
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            every = [r["chunk_id"] for r in conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id")]
            rows = chunk_rows(conn, every)
            corp = api_v2.root_reader(self.c.app.state)
            rng = random.Random(20261102)
            runs = folded = changed = windowed = chunk_only = 0
            for query in ("NSSA ABR elected translator Type-7 LSAs", "show ip ospf neighbor", "OSPF", "Pat Murphy"):
                terms = query_terms(query)
                for max_chars in (200, 1200, 2400):
                    windows = {}

                    def window(cid, terms=terms, max_chars=max_chars, windows=windows):
                        if cid not in windows:
                            row = rows.get(cid)
                            doc = corp.by_id.get(row["doc_id"]) if row else None
                            if row is None:
                                windows[cid] = None, None
                            elif doc is not None and row.get("line_start"):
                                ev = corp.evidence(doc, (row["line_start"], row["line_end"]), terms, max_chars)
                                windows[cid] = [ev["text"]], ev["local"]
                            else:
                                windows[cid] = [chunk_window(row["text"], terms, max_chars)], None
                        return windows[cid]

                    for trial in range(8):
                        order = list(every)
                        rng.shuffle(order)
                        order = order[: rng.randint(4, len(order))]
                        if trial % 2 == 0:              # a chunk listed twice, and one without a row
                            order.insert(rng.randint(0, len(order)), order[0])
                            order.insert(rng.randint(0, len(order)), "chk_" + "0" * 64)
                        cands = [{"chunk_id": c, "fused_score": 1.0 / (n + 1), "retrievers": ["bm25"]}
                                 for n, c in enumerate(order)]
                        for limit in (1, 3, 8, 30):
                            want = replay_walk(order, window, lambda c: rows[c]["text"], limit)
                            on = self.builder(True, max_chars).build(conn, cands, lambda _: terms, limit)
                            self.assertEqual([h["id"] for h in on], [s["rep"] for s in want])
                            self.assertEqual([[m["id"] for m in h["also_in"]] for h in on],
                                             [s["members"][1:] for s in want])
                            # each member's own window (what it would be delivered with) is its hit's text
                            for h in on:
                                for m in h["also_in"]:
                                    self.assertEqual(replay_tokens(window(m["id"])[0][0]), replay_tokens(h["text"]))
                                    windowed += 1
                            # off: production's walk (the replay's mode "none"), and no also_in anywhere
                            off = self.builder(False, max_chars).build(conn, cands, lambda _: terms, limit)
                            self.assertEqual([h["id"] for h in off], production_walk(order, window, limit))
                            self.assertFalse(any("also_in" in h for h in off))
                            # a hit is the same object either way, also_in aside
                            by_id = {h["id"]: h for h in off}
                            for h in on:
                                if h["id"] in by_id:
                                    self.assertEqual({k: v for k, v in h.items() if k != "also_in"}, by_id[h["id"]])
                            runs += 1
                            folded += sum(len(h["also_in"]) for h in on)
                            chunk_only += sum(len(s["members"]) - 1 for s in
                                              replay_walk(order, window, lambda c: rows[c]["text"], limit,
                                                          same_window=False))
                            changed += [h["id"] for h in on] != [h["id"] for h in off]
        self.assertEqual(runs, 4 * 3 * 8 * 4)
        self.assertGreater(folded, 100)
        self.assertEqual(windowed, folded)
        self.assertGreater(changed, 50)
        # the window rule matters on these orders: under the chunk text alone (the earlier rule), more would fold
        self.assertGreater(chunk_only, folded)

    def test_empty_texts_are_never_copies(self):
        a, b = chunk_id("punct-a", 0), chunk_id("punct-b", 0)
        with connect(self.root.paths.sqlite / "rag.db") as conn:
            rows = chunk_rows(conn, [a, b])
            self.assertEqual(rows[a]["text"], rows[b]["text"])
            self.assertEqual(fold_key(rows[a]["text"]), "")
            cands = [{"chunk_id": c, "fused_score": 0.5, "retrievers": ["bm25"]} for c in (a, b)]
            hits = self.builder(True, 1200).build(conn, cands, lambda _: [], 8)
        self.assertEqual([h["id"] for h in hits], [a, b])
        self.assertEqual([h["also_in"] for h in hits], [[], []])


class FoldKeyTests(unittest.TestCase):

    def test_the_replay_s_rule(self):
        for text in ("OSPF is a link-state protocol.", "ospf   IS a link state, protocol!", "ＯＳＰＦ"
                     " is a link‑state protocol", "", None, "— · —", "ﬁle ① ½ Ⅻ", "Straße ÉCOLE"):
            with self.subTest(text=text):
                self.assertEqual(fold_key(text), replay_tokens(text))
        self.assertEqual(fold_key("OSPF is a link-state protocol."), "ospf is a link state protocol")
        self.assertEqual(fold_key("ospf   IS a link state, protocol!"), fold_key("OSPF is a link-state protocol."))
        self.assertNotEqual(fold_key("OSPF is a link-state protocol"), fold_key("OSPF is the link-state protocol"))
        self.assertNotEqual(fold_key("a b"), fold_key("b a"))
        self.assertEqual(fold_key("— · —"), "")


class SettingsTests(unittest.TestCase):

    def test_values(self):
        for config, want in (({}, False), ({"fold_identical": None}, False), ({"fold_identical": False}, False),
                             ({"fold_identical": True}, True)):
            with self.subTest(config=config):
                self.assertIs(fold_identical(config), want)
        for bad in (1, 0, "true", "yes", [], {}, {"match": "exact"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as caught:
                fold_identical({"fold_identical": bad})
            self.assertIn("fold_identical must be true or false", str(caught.exception))
        repo = json.loads((Path(api.SERVING_CONFIG_PATH).parent / "retrieval.json").read_text(encoding="utf-8"))
        self.assertNotIn("fold_identical", repo)        # the repo default is off

    @staticmethod
    def start(retrieval):
        """test_grep_engine.StartTests.start: deep32-engine started on a tiny root with these retrieval settings and
        stopped; the ValueError its start raised, or None."""
        made = []
        real_init = RootClient.__init__

        def init(root, **kw):
            made.append(root)
            real_init(root, **kw)

        try:
            with mock.patch.object(RootClient, "__init__", init):
                RootClient(retrieval=retrieval).close()
        except ValueError as exc:
            for root in made:
                root.env.stop()
                root.tmp.cleanup()
            return exc
        return None

    def test_a_bad_value_fails_the_start(self):
        self.assertIn("fold_identical must be true or false", str(self.start({"fold_identical": "yes"})))
        self.assertIn("fold_identical must be true or false", str(self.start({"fold_identical": 1})))
        self.assertIsNone(self.start({"fold_identical": True}))
        self.assertIsNone(self.start({}))


class StatusBlockTests(unittest.TestCase):
    """The settings block as the production chain reads it (json without spaces), p81r's retrieval config."""

    def block(self, extra, dense_options=()):
        import tempfile
        from rag_core.rerank import Reranker
        from rag_core.retrievers.dense import DenseRetriever
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        make_root(root)
        reranker = Reranker(P81R_RETRIEVAL["reranker"])
        self.addCleanup(reranker.close)
        env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("rag_core.retrievers.dense.make_qdrant_client", return_value=mock.Mock()):
            dense = DenseRetriever(RAGPaths(root), *dense_options)
        self.addCleanup(dense.embedder.close)
        orch = SimpleNamespace(enabled=["bm25", "dense"], retrievers={"bm25": object(), "dense": dense},
                               breakers={}, reranker=reranker, config={**P81R_RETRIEVAL, **extra})
        svc = StatusService(orch, RAGPaths(root), generation="p81r-20261025", clock=Clock(),
                            serving={"max_concurrent_searches": 2, "max_queued_searches": 16})
        self.addCleanup(svc.close)
        svc._probe = lambda: {"corpus": (None, None), "kb": (None, None), "dense": (None, None),
                              "reranker": (None, None), "checked_at": "t"}
        return json.dumps(svc.status()["settings"], separators=(",", ":"))

    def test_unset_false_or_null_the_block_is_p81r_s_string(self):
        for extra in ({}, {"fold_identical": False}, {"fold_identical": None}):
            with self.subTest(extra=extra):
                self.assertEqual(self.block(extra), P81R_SETTINGS)

    def test_set_fold_identical_comes_last(self):
        self.assertEqual(self.block(FOLD_ON), P81R_SETTINGS[:-1] + ',"fold_identical":true}')
        self.assertEqual(self.block(FOLD_ON, ({"exact": True},)),
                         P81R_SETTINGS[:-1] + ',"dense":{"exact":true},"fold_identical":true}')


@engine_target.SCRIPTS_SKIP
class DeriveTests(unittest.TestCase):
    # test_derive_root's fixture, as test_dense_search_params borrows it (a TestCase named here would run here too)
    setUpClass = classmethod(test_derive_root.DeriveRootTests.setUpClass.__func__)
    tearDownClass = classmethod(test_derive_root.DeriveRootTests.tearDownClass.__func__)
    setUp = test_derive_root.DeriveRootTests.setUp
    run_derive = test_derive_root.DeriveRootTests.run_derive
    report = test_derive_root.DeriveRootTests.report
    config = test_derive_root.DeriveRootTests.config
    collections = test_derive_root.DeriveRootTests.collections
    refusal = test_derive_root.DeriveRootTests.refusal

    def test_it_is_set_per_root_like_the_other_retrieval_keys(self):
        from rag_core.config import load_config
        from test_derive_root import SERVE_URL
        args = ["--set", "retrieval.fold_identical=true"]
        self.assertTrue(all(SAFE_ARG.match(a) for a in args), args)
        self.assertEqual(self.run_derive("--to", "gen-fold", "--rerank-url", SERVE_URL, *args), 0)
        retrieval = load_config("retrieval", root=self.data / "gen-fold")
        self.assertIs(retrieval["fold_identical"], True)
        self.assertIs(fold_identical(retrieval), True)
        self.assertEqual(self.report("gen-fold")["config"]["overrides"][:1], ["retrieval.fold_identical=true"])

    def test_values_rag_core_would_not_start_with_are_refused_before_anything_is_written(self):
        before = self.collections()
        for item in ("retrieval.fold_identical=1", "retrieval.fold_identical=yes", "retrieval.fold_identical.x=1"):
            self.derive.log.reset_mock()
            with self.subTest(item=item):
                self.assertEqual(self.run_derive("--to", "gen-bad", "--set", item), 2)
                self.assertIn("deep32-engine would not start with it", self.refusal())
                self.assertIn("fold_identical", self.refusal())
                self.assertFalse((self.data / "gen-bad").exists())
        self.assertEqual(self.collections(), before)


if __name__ == "__main__":
    unittest.main()
