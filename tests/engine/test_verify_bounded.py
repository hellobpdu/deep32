"""0.1.0-alpha.3, WP-D item D2: /v2/verify_quote says where a match lies ("scope", "sections", "gap_chars"), and
with retrieval.json verify.ellipsis "bounded" a quotation with omissions passes only when its parts lie in one
section, each at most verify.max_gap_chars after the part before it (reader.bounded_spans); otherwise it is
not_found with "reason": "parts-too-far".

Without the setting every answer is the one before, plus the new keys: RECORDED holds the bodies 0.1.0-alpha.2
(d84ce2a) sent for these requests on the tiny root of tests/v2_root.py, byte for byte."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import unittest
from unittest import mock

from rag_core import reader
from rag_core.api_v2 import VerifySettings, verify_settings
from rag_core.reader import bounded_spans, canonical, in_order, widest_gap

from test_api_v2 import AUTH, RootClient
from v2_root import add_document, chunk_id

ADDED = ("scope", "sections", "gap_chars", "reason")       # what 0.1.0-alpha.3 adds to an answer
BOUNDED = {"verify": {"ellipsis": "bounded"}}
# (ref; "CHUNK:<doc_key>:<ordinal>" stands for that chunk's id, quote, status, body) as d84ce2a answered
RECORDED = [
    ('rfc3101#3.1', 'has bit Nt set or', 200, '{"result":"exact","section_id":"rfc3101#3.1","local":"rfc3101.txt:33-33","match_text":"has bit Nt set or","degraded":[]}'),
    ('rfc3101#3.2', 'Only installed Type-7 LSAs should be examined.', 200, '{"result":"normalized","section_id":"rfc3101#3.2","local":"rfc3101.txt:44-50","match_text":"   Only installed\\n\\n   Type-7 LSAs should be examined.  The transla-","degraded":[]}'),
    ('rfc3101#3.2', 'The translation is skipped when the P\u2011bit is clear', 200, '{"result":"normalized","section_id":"rfc3101#3.2","local":"rfc3101.txt:50-51","match_text":"   Type-7 LSAs should be examined.  The transla-\\n   tion is skipped when the P-bit is clear.","degraded":[]}'),
    ('rfc3101#3.2', 'If the Type-7 LSA has the P-bit clear ... do nothing', 200, '{"result":"normalized","section_id":"rfc3101#3.2","local":"rfc3101.txt:53-54","match_text":"      (1) If the Type-7 LSA has the P-bit clear, or its forwarding\\n          address is set to 0.0.0.0, then do nothing with this Type-7","degraded":[]}'),
    ('rfc3101#3.2', 'NSSAs are similar to stub areas.', 200, '{"result":"exact","section_id":"rfc3101#1","local":"rfc3101.txt:26-26","match_text":"NSSAs are similar to stub areas.","degraded":[]}'),
    ('CHUNK:rfc3101:2', 'defaults to 40 seconds', 200, '{"result":"normalized","section_id":"rfc3101#3.1","local":"rfc3101.txt:34-35","match_text":"   a higher router ID.  The TranslatorStabilityInterval defaults to 40\\n   seconds.","degraded":[]}'),
    ('rfc3101', 'Pat Murphy', 200, '{"result":"exact","section_id":"rfc3101#author-s-addresses","local":"rfc3101.txt:65-65","match_text":"Pat Murphy","degraded":[]}'),
    ('rfc3101#3.2', 'the P-bit is set', 200, '{"result":"not_found","section_id":"rfc3101#3.2","local":null,"match_text":null,"degraded":[]}'),
    ('rfc3101#3.1', 'defaults to 40 seconds ... This step is performed', 200, '{"result":"normalized","section_id":"rfc3101#3.1","local":"rfc3101.txt:34-43","match_text":"   a higher router ID.  The TranslatorStabilityInterval defaults to 40\\n   seconds.\\n\\n3.2 Translating Type-7 LSAs into Type-5 LSAs\\n\\n   This step is performed as part of the NSSA\'s Dijkstra calculation.","degraded":[]}'),
    ('rfc3101', 'NSSAs are similar ... The N/P-bit is used', 200, '{"result":"normalized","section_id":"rfc3101#1","local":"rfc3101.txt:26-61","match_text":"   NSSAs are similar to stub areas.  Type-7 LSAs carry external\\n   routing information within an NSSA.\\n\\n3.0 Intra-AS Implementation Details\\n\\n3.1 Type-7 Translator Election\\n\\n   A border router defers when another border router has bit Nt set or\\n   a higher router ID.  The TranslatorStabilityInterval defaults to 40\\n   seconds.\\n\\n3.2 Translating Type-7 LSAs into Type-5 LSAs\\n\\n   This step is performed as part of the NSSA\'s Dijkstra calculation.\\n   Only installed\\n\\n   Type-7 LSAs should be examined.  The transla-\\n   tion is skipped when the P-bit is clear.\\n\\n      (1) If the Type-7 LSA has the P-bit clear, or its forwarding\\n          address is set to 0.0.0.0, then do nothing with this Type-7\\n          LSA.\\n\\n                 4. The LSA with the higher router ID.\\n\\nAppendix A: The Options Field\\n\\n   The N/P-bit is used in Type-7 LSAs.","degraded":[]}'),
    ('c9300-179-rtng#examples', 'area 1 nssa [...] always', 200, '{"result":"normalized","section_id":"c9300-179-rtng#examples","local":"c9300-179-rtng.txt:9-9","match_text":"Router(config-router)# area 1 nssa translate type7 always","degraded":[]}'),
    ('rfc3101#3.2', ' ', 200, '{"result":"not_found","section_id":"rfc3101#3.2","local":null,"match_text":null,"degraded":[]}'),
]


def filler(tag, n):
    """n lines of about 60 characters, every word the line's own."""
    return [" ".join(f"{tag}{i}w{j}" for j in range(8)) + "." for i in range(n)]


QUOTE_GUIDE = "\n".join([
    "# Quote guide",
    "## Timers",
    "HoldTime is a mandatory attribute. The suggested default value for the HoldTime is 90 seconds.",
    "During some portions of the state machine the HoldTimer is set to a large value of 4 minutes.",
    *filler("t", 8),
    "The suggested default value for the advertisement interval on external connections is 30 seconds.",
    "## Long section",
    "Alpha value is 90 seconds.",
    *filler("l", 30),
    "Omega timer is 30 seconds.",
    "## Repeats",
    "A marks the start of a run.",
    *filler("r", 20),
    "A marks the start of a run. B closes the run.",
    "## Twice",
    "First part here. Middle token.",
    *filler("m", 4),
    "Middle token.",
    *filler("n", 3),
    "Last part there.",
    "## Words",
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen",
    "",
])


def prepare(paths, kb_src):
    add_document(paths, "quote-guide", "cisco", QUOTE_GUIDE, "Quote guide")


def gap_between(text, *parts):
    """The canonical characters between consecutive parts where each first follows the one before, counted on the
    raw text beside the engine."""
    flat = canonical(text)
    gaps, pos = [], 0
    for n, part in enumerate(parts):
        at = flat.index(canonical(part), pos)
        if n:
            gaps.append(at - pos)
        pos = at + len(canonical(part))
    return max(gaps)


class Served(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=prepare)
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def verify(self, ref, quote, settings=None):
        with mock.patch.dict(self.orch.config, settings or {}):
            r = self.c.post("/v2/verify_quote", json={"ref": ref, "quote": quote}, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def both(self, ref, quote, **verify):
        """(the answer under "any", the answer under "bounded" with these verify keys)."""
        return self.verify(ref, quote), self.verify(ref, quote, {"verify": {"ellipsis": "bounded", **verify}})


class AbsentTests(Served):
    """Without the setting (and with "any"): the bodies d84ce2a sent, once the added keys are taken out."""

    def answers(self, settings=None):
        out = []
        for ref, quote, _, _ in RECORDED:
            if ref.startswith("CHUNK:"):
                _, key, ordinal = ref.split(":")
                ref = chunk_id(key, int(ordinal))
            with mock.patch.dict(self.orch.config, settings or {}):
                r = self.c.post("/v2/verify_quote", json={"ref": ref, "quote": quote}, headers=AUTH)
            out.append((r.status_code, r.content.decode("utf-8")))
        return out

    def alpha2(self, text):
        body = json.loads(text)
        return json.dumps({k: v for k, v in body.items() if k not in ADDED}, ensure_ascii=False,
                          allow_nan=False, indent=None, separators=(",", ":"))

    def test_every_recorded_body_comes_back(self):
        for settings in (None, {"verify": None}, {"verify": {}}, {"verify": {"ellipsis": "any"}},
                         {"verify": {"ellipsis": "any", "max_gap_chars": 0}}):
            with self.subTest(settings=settings):
                got = self.answers(settings)
                self.assertEqual([(status, self.alpha2(text)) for status, text in got],
                                 [(status, body) for _, _, status, body in RECORDED])

    def test_the_added_keys_stand_after_match_text_and_before_degraded(self):
        orders = {tuple(json.loads(text)) for _, text in self.answers()}
        self.assertEqual(orders, {
            ("result", "section_id", "local", "match_text", "scope", "sections", "gap_chars", "degraded"),
            ("result", "section_id", "local", "match_text", "sections", "gap_chars", "degraded"),
        })

    def test_bounded_changes_only_a_quotation_with_omissions(self):
        any_, bounded = self.answers(), self.answers(BOUNDED)
        changed = [RECORDED[n][1] for n, (a, b) in enumerate(zip(any_, bounded)) if a != b]
        self.assertEqual(changed, ["defaults to 40 seconds ... This step is performed",
                                   "NSSAs are similar ... The N/P-bit is used"])


class WhereTests(Served):

    def test_a_contiguous_quotation_in_the_cited_section(self):
        r = self.verify("rfc3101#3.1", "has bit Nt set or")
        self.assertEqual((r["result"], r["scope"], r["sections"], r["gap_chars"]),
                         ("exact", "section", ["rfc3101#3.1"], 0))
        self.assertNotIn("reason", r)

    def test_a_contiguous_quotation_in_another_section(self):
        for settings in (None, BOUNDED):
            r = self.verify("rfc3101#3.2", "NSSAs are similar to stub areas.", settings)
            self.assertEqual((r["result"], r["section_id"], r["scope"], r["sections"]),
                             ("exact", "rfc3101#1", "document", ["rfc3101#1"]))

    def test_a_document_ref_has_no_scope(self):
        r = self.verify("rfc3101", "Pat Murphy")
        self.assertNotIn("scope", r)
        self.assertEqual(r["sections"], ["rfc3101#author-s-addresses"])

    def test_a_chunk_ref_is_judged_against_the_chunk_s_section(self):
        # chunk 2 of rfc3101 holds the text of 3.1 and the start of 3.2; its section is 3.1 (most of its lines)
        ref = chunk_id("rfc3101", 2)
        self.assertEqual(self.verify(ref, "defaults to 40 seconds")["scope"], "section")
        r = self.verify(ref, "This step is performed as part of")
        self.assertEqual((r["section_id"], r["scope"]), ("rfc3101#3.2", "document"))

    def test_a_parent_section_holds_its_subsections(self):
        r = self.verify("rfc3101#3", "This step is performed as part of")
        self.assertEqual((r["section_id"], r["scope"], r["sections"]), ("rfc3101#3.2", "section", ["rfc3101#3.2"]))

    def test_a_contiguous_quotation_across_a_heading_spans_two_sections(self):
        for settings in (None, BOUNDED):            # no omission: both rules pass it
            r = self.verify("rfc3101#3", "seconds. 3.2 Translating Type-7 LSAs into Type-5 LSAs This step", settings)
            self.assertEqual((r["result"], r["scope"], r["sections"], r["gap_chars"]),
                             ("normalized", "section", ["rfc3101#3.1", "rfc3101#3.2"], 0))

    def test_not_found_has_no_scope_and_no_sections(self):
        r = self.verify("rfc3101#3.2", "the P-bit is set")
        self.assertEqual(r, {"result": "not_found", "section_id": "rfc3101#3.2", "local": None, "match_text": None,
                             "sections": [], "gap_chars": 0, "degraded": []})


class BoundedTests(Served):

    def test_an_omission_inside_one_paragraph_passes(self):
        quote = "If the Type-7 LSA has the P-bit clear ... do nothing"
        any_, bounded = self.both("rfc3101#3.2", quote)
        self.assertEqual(bounded, any_)
        self.assertEqual((bounded["result"], bounded["scope"], bounded["sections"]),
                         ("normalized", "section", ["rfc3101#3.2"]))
        self.assertEqual(bounded["gap_chars"], len(canonical(", or its forwarding address is set to 0.0.0.0, then")))

    def test_an_omission_across_a_page_break_passes_and_the_furniture_is_not_counted(self):
        any_, bounded = self.both("rfc3101#3.2", "Only installed ... should be examined.")
        self.assertEqual(bounded, any_)
        self.assertEqual((bounded["result"], bounded["gap_chars"]), ("normalized", len("Type7LSAs")))
        self.assertIn("[Page 3]", "\n".join(self.root.source_lines(bounded["local"])))
        self.assertNotIn("[Page", bounded["match_text"])
        self.assertEqual(self.verify("rfc3101#3.2", "Only installed ... should be examined.",
                                     {"verify": {"ellipsis": "bounded", "max_gap_chars": 9}})["result"], "normalized")
        self.assertEqual(self.verify("rfc3101#3.2", "Only installed ... should be examined.",
                                     {"verify": {"ellipsis": "bounded", "max_gap_chars": 8}})["reason"],
                         "parts-too-far")

    def test_parts_in_two_sections_do_not_pass(self):
        quote = "defaults to 40 seconds ... This step is performed"
        for ref in ("rfc3101#3.1", "rfc3101#3", "rfc3101"):
            with self.subTest(ref=ref):
                any_, bounded = self.both(ref, quote)
                self.assertEqual((any_["result"], any_["sections"]), ("normalized", ["rfc3101#3.1", "rfc3101#3.2"]))
                self.assertEqual(any_.get("scope"), {"rfc3101#3.1": "document", "rfc3101#3": "section"}.get(ref))
                self.assertEqual(bounded, {
                    "result": "not_found", "section_id": None if ref == "rfc3101" else ref, "local": None,
                    "match_text": None, "sections": [], "gap_chars": any_["gap_chars"], "reason": "parts-too-far",
                    "degraded": []})
                self.assertLess(any_["gap_chars"], 100)         # near enough: it is the heading between them

    def test_parts_far_apart_in_one_long_section_do_not_pass(self):
        quote = "Alpha value is ... 30 seconds."
        any_, bounded = self.both("quote-guide#long-section", quote)
        gap = gap_between(QUOTE_GUIDE, "Alpha value is", "30 seconds.")
        self.assertGreater(gap, 600)
        self.assertEqual((any_["result"], any_["scope"], any_["sections"], any_["gap_chars"]),
                         ("normalized", "section", ["quote-guide#long-section"], gap))
        self.assertEqual((bounded["result"], bounded["reason"], bounded["gap_chars"], bounded["local"]),
                         ("not_found", "parts-too-far", gap, None))
        near = self.verify("quote-guide#long-section", quote, {"verify": {"ellipsis": "bounded",
                                                                           "max_gap_chars": gap}})
        self.assertEqual(near, any_)
        self.assertEqual(self.verify("quote-guide#long-section", quote,
                                     {"verify": {"ellipsis": "bounded", "max_gap_chars": gap - 1}})["result"],
                         "not_found")

    def test_the_stitched_default_passes_or_fails_with_the_limit(self):
        # the audit's example in small: a value of a later paragraph stitched onto an earlier sentence
        quote = "The suggested default value for the HoldTime is ... 30 seconds."
        gap = gap_between(QUOTE_GUIDE, "The suggested default value for the HoldTime is", "30 seconds.")
        self.assertTrue(300 < gap < 600, gap)
        self.assertEqual(self.verify("quote-guide#timers", quote)["result"], "normalized")
        self.assertEqual(self.verify("quote-guide#timers", quote, BOUNDED)["result"], "normalized")      # 600
        strict = self.verify("quote-guide#timers", quote, {"verify": {"ellipsis": "bounded", "max_gap_chars": 300}})
        self.assertEqual((strict["result"], strict["reason"], strict["gap_chars"]), ("not_found", "parts-too-far", gap))
        # the neighbouring sentence is too near for any limit that still lets an omission through
        self.assertEqual(self.verify("quote-guide#timers", "The suggested default value for the HoldTime is ... "
                                     "4 minutes.", {"verify": {"ellipsis": "bounded", "max_gap_chars": 300}})["result"],
                         "normalized")

    def test_a_repeated_part_takes_the_place_that_is_near_enough(self):
        quote = "A marks the start of a run ... B closes the run."
        any_, bounded = self.both("quote-guide#repeats", quote)
        self.assertGreater(any_["gap_chars"], 600)              # in order from the first "A marks": far
        self.assertEqual((bounded["result"], bounded["gap_chars"]), ("normalized", 1))      # the "." between them
        first, last = (int(n) for n in any_["local"].split(":")[1].split("-"))
        self.assertLess(first, last)
        self.assertEqual(bounded["local"], f"quote-guide.txt:{last}-{last}")
        self.assertEqual(bounded["match_text"], "A marks the start of a run. B closes the run.")

    def test_a_middle_part_takes_its_second_place_when_the_first_leaves_the_last_too_far(self):
        quote = "First part here ... Middle token ... Last part there."
        limit = {"verify": {"ellipsis": "bounded", "max_gap_chars": 200}}
        any_ = self.verify("quote-guide#twice", quote)
        bounded = self.verify("quote-guide#twice", quote, limit)
        self.assertGreater(any_["gap_chars"], 200)              # first "Middle token", then far to the last part
        self.assertEqual(bounded["result"], "normalized")
        self.assertLessEqual(bounded["gap_chars"], 200)
        self.assertEqual(bounded["local"], any_["local"])       # the same first and last lines

    def test_more_parts_than_the_check_places_do_not_pass(self):
        words = ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                 "sixteen seventeen").split()
        sixteen, seventeen = " ... ".join(words[:16]), " ... ".join(words)
        self.assertEqual(self.verify("quote-guide#words", sixteen, BOUNDED)["result"], "normalized")
        self.assertEqual(self.verify("quote-guide#words", seventeen)["result"], "normalized")
        r = self.verify("quote-guide#words", seventeen, BOUNDED)
        self.assertEqual((r["result"], r["reason"]), ("not_found", "parts-too-far"))

    def test_a_quotation_that_is_nowhere_has_no_reason(self):
        r = self.verify("rfc3101#3.2", "Only installed ... never written", BOUNDED)
        self.assertEqual((r["result"], r["gap_chars"]), ("not_found", 0))
        self.assertNotIn("reason", r)

    def test_the_status_shows_the_rule_only_when_bounded(self):
        status = self.c.get("/v2/status", headers=AUTH).json()
        self.assertNotIn("verify", status["settings"])
        with mock.patch.dict(self.orch.config, {"verify": {"ellipsis": "any", "max_gap_chars": 100}}):
            self.c.app.state.status._probed = None
            self.assertNotIn("verify", self.c.get("/v2/status", headers=AUTH).json()["settings"])
        with mock.patch.dict(self.orch.config, BOUNDED):
            self.c.app.state.status._probed = None
            status = self.c.get("/v2/status", headers=AUTH).json()
        self.assertEqual(status["settings"]["verify"], {"ellipsis": "bounded", "max_gap_chars": 600})


class SettingTests(unittest.TestCase):

    def test_values(self):
        for config in ({}, {"verify": None}, {"verify": {}}):
            self.assertEqual(verify_settings(config), VerifySettings("any", 600))
        self.assertEqual(verify_settings({"verify": {"ellipsis": "bounded"}}), VerifySettings("bounded", 600))
        self.assertEqual(verify_settings({"verify": {"ellipsis": "bounded", "max_gap_chars": 0}}).max_gap_chars, 0)
        self.assertEqual((reader.VERIFY_ELLIPSIS, reader.DEFAULT_MAX_GAP_CHARS), (("any", "bounded"), 600))

    def test_other_values_are_refused(self):
        for bad in ("bounded", [], {"ellipsis": "strict"}, {"ellipsis": True}, {"max_gap_chars": -1},
                    {"max_gap_chars": 600.0}, {"max_gap_chars": "600"}, {"max_gap_chars": True}, {"gap": 600}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "verify"):
                verify_settings({"verify": bad})

    def test_the_start_refuses_a_bad_value(self):
        with self.assertRaisesRegex(ValueError, "ellipsis"):
            RootClient(retrieval={"verify": {"ellipsis": "near"}})


class PlacementTests(unittest.TestCase):
    """reader.in_order, widest_gap and bounded_spans on plain strings (one section: section_of answers one object)."""

    @staticmethod
    def one(at):
        return PlacementTests

    def test_in_order_takes_each_part_at_its_first_place_after_the_one_before(self):
        self.assertEqual(in_order("abXabYab", ["ab", "ab"]), [(0, 2), (3, 5)])
        self.assertEqual(in_order("abXcd", ["cd", "ab"]), None)
        self.assertEqual(widest_gap([(0, 2), (3, 5), (20, 22)]), 15)
        self.assertEqual(widest_gap([(4, 9)]), 0)

    def test_the_places_in_order_are_the_answer_when_they_are_near_enough(self):
        text = "aa....bb..cc"
        self.assertEqual(bounded_spans(text, ["aa", "bb", "cc"], 4, self.one), in_order(text, ["aa", "bb", "cc"]))
        self.assertIsNone(bounded_spans(text, ["aa", "bb", "cc"], 3, self.one))

    def test_every_placement_is_considered(self):
        # the first part twice: its second place is the one near the next part
        text = "aa" + "." * 9 + "aa.bb.cc"
        self.assertEqual(widest_gap(in_order(text, ["aa", "bb", "cc"])), 12)
        self.assertEqual(bounded_spans(text, ["aa", "bb", "cc"], 10, self.one), [(11, 13), (14, 16), (17, 19)])
        # a middle part twice: its first place is in order but leaves the last part too far
        text = "aa.bb" + "." * 4 + "bb" + "." * 4 + "cc"
        self.assertEqual(in_order(text, ["aa", "bb", "cc"]), [(0, 2), (3, 5), (15, 17)])
        self.assertEqual(bounded_spans(text, ["aa", "bb", "cc"], 8, self.one), [(0, 2), (9, 11), (15, 17)])
        self.assertIsNone(bounded_spans(text, ["aa", "bb", "cc"], 6, self.one))

    def test_a_part_may_not_overlap_the_one_before(self):
        self.assertIsNone(bounded_spans("abab", ["aba", "bab"], 5, self.one))
        self.assertEqual(bounded_spans("ababab", ["ab", "ab"], 0, self.one), [(0, 2), (2, 4)])

    def test_all_parts_lie_in_one_section(self):
        section = lambda at: at // 10                           # noqa: E731  two "sections" of ten characters
        text = "aa......bb" + "aa..bb...."
        self.assertEqual(bounded_spans(text, ["aa", "bb"], 8, lambda at: text), [(0, 2), (8, 10)])
        ints = {0: "first", 1: "second"}
        self.assertEqual(bounded_spans(text, ["aa", "bb"], 8, lambda at: ints[section(at)]), [(0, 2), (8, 10)])
        self.assertEqual(bounded_spans("aa.......b" + "b........." + "aa.bb.....", ["aa", "bb"], 9,
                                       lambda at: ints.get(section(at), "third")), [(20, 22), (23, 25)])

    def test_the_work_is_bounded(self):
        with mock.patch.object(reader, "BOUNDED_MAX_OCCURRENCES", 5):
            self.assertIsNone(bounded_spans("a" * 6 + "b", ["a", "b"], 10, self.one))
            self.assertEqual(bounded_spans("a" * 4 + "b", ["a", "b"], 10, self.one), [(0, 1), (4, 5)])
        many = ["a"] * (reader.BOUNDED_MAX_PARTS + 1)
        self.assertIsNone(bounded_spans("a" * 40, many, 10, self.one))
        self.assertIsNotNone(bounded_spans("a" * 40, many[:-1], 10, self.one))


if __name__ == "__main__":
    unittest.main()
