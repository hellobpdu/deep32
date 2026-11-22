"""The answer check (deep32_relay/answer_check.py): one synthetic answer and transcript per check, and the footers.

Invented text only (answer_fixtures.py): no corpus passage, no recorded answer, no network. One test starts a
Python child process to run the file as a script under -I -B, the way the documented command line does.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1] / "adapters" / "relay" / "deep32_relay" / "answer_check.py"
if REPOSITORY.is_file():                    # in the repository: tests/relay/test_answer_check.py
    sys.path.insert(0, str(REPOSITORY.parents[1]))
    from deep32_relay import answer_check as ac
    SCRIPT = REPOSITORY
else:                                       # in the spec writer's scratch folder: the prototype beside this file
    sys.path.insert(0, str(HERE))
    import answer_check as ac
    SCRIPT = HERE / "answer_check.py"
import answer_fixtures as fx

CLEAN_FOOTER = """---
Checks (by script, no model):
- Quotations: 3. Checked exact: 2. Checked normalized: 1.
- RFCs named: 1. Status looked up: 1.
- "Not covered" statements: none.
- Search: full strength (rag_status showed every backend ok).
- Tool calls: 8 (verify_quote 3, get_section 2, lookup_rfc 1, rag_status 1, search_many 1).
- Numbers, releases and commands outside quotations: 4. Found in the retrieved text: 4.
- Sentences with a specific in Answer, Evidence and RFC vs Cisco: 1. With their own citation: 1.
To check: nothing."""

FLAGGED_FOOTER = """---
Checks (by script, no model):
- Quotations: 3. Checked exact: 1. Checked normalized: 1. To check: 2.
  - to check: "A speaker MUST restart the hold timer when it receives a KEEPALIVE message." (labeled verified; the check said normalized)
  - to check: "The timer is never restarted by a peer." (verify_quote did not find it)
- RFCs named: 2. Status looked up: 2.
- "Not covered" statements: 1. With an outline read: 0. To check: 1.
  - to check: "The dampening default is not in corpus." (no get_outline call in this answer)
- Search: degraded (reranker), and the answer's first lines do not say so. To check: 1.
- Tool calls: 8 (verify_quote 3, lookup_rfc 2, get_section 1, rag_status 1, search_many 1). To check: 1.
  - to check: the answer says 12 calls; the script counted 8
- Numbers, releases and commands outside quotations: 5. Found in the retrieved text: 2. To check: 3.
  - to check: 750 seconds
  - to check: IOS XE 17.12
  - to check: 192.0.2.77
- Sentences with a specific in Answer, Evidence and RFC vs Cisco: 3. With their own citation: 1. To check: 2.
  - to check: "The keepalive is sent every 750 seconds on IOS XE 17.12." (no citation in or after it)
  - to check: "A neighbour at 192.0.2.77 keeps the session up." (no citation in or after it)
To check: 10. A flag means the script could not confirm the item, not that it is wrong."""

NO_EVENTS_FOOTER = """---
Checks (by script, no model):
- No tool call was recorded for this answer, so none of it was retrieved or checked in this turn.
- In the answer: quotations 3, RFC numbers 1, other specifics (numbers, releases, commands) 4.
To check: the whole answer."""

NOTHING_FOOTER = """---
Checks (by script, no model):
- No tool call was recorded for this answer.
- It contains no quotation, RFC number or other specific, so there was nothing to check."""

TOO_LARGE_LINE = ("- Numbers, releases and commands: not checked (the tool results of this answer were too large "
                  "to keep).")


def found(result, name):
    """The findings of one check, in order."""
    return [item for item in result["findings"] if item["check"] == name]


def kinds(result, name):
    return [item["kind"] for item in found(result, name)]


def excerpts(result, name):
    return [item["excerpt"] for item in found(result, name)]


def numbers(result, name):
    return {key: value for key, value in result["checks"][name].items() if key != "by_tool"}


class Quotations(unittest.TestCase):
    def test_exact_and_normalized_quotations_match_their_checks(self):
        result = ac.check(fx.CLEAN_ANSWER, fx.clean_events(), question=fx.QUESTION)
        self.assertEqual(numbers(result, "quotations"), dict(status="ok", total=3, exact=2, normalized=1,
                                                             labelled_unverified=0, flagged=0))
        self.assertEqual((result["to_check"], result["findings"]), (0, []))

    def test_typographic_quotes_and_dash_separators_are_read(self):
        # what a recorded answer wrote: curly quotes, an em dash for the middle dot, a footnote after the marker
        answer = ("- Default: \u201cThe hold timer defaults to 180 seconds.\u201d (verified \u2014 rfc9999.txt:118) [^1]\n"
                  "- Rule: \u201cA speaker MUST restart the hold timer when it receives a KEEPALIVE message.\u201d "
                  "(normalized - rfc9999.txt:118-119).\n\n[^1]: [RFC 9999 \u00a73.1]\n")
        result = ac.check(answer, [fx.verify(fx.Q_HOLD, "exact"), fx.verify(fx.Q_RESTART, "normalized")])
        self.assertEqual(numbers(result, "quotations"), dict(status="ok", total=2, exact=1, normalized=1,
                                                             labelled_unverified=0, flagged=0))

    def test_a_check_is_matched_in_the_engines_comparison_form(self):
        # the call had straight quotes, one line and a hyphen; the answer has a curly quote and a wrapped hyphen
        events = [fx.verify("The peer's hold-time is never zero in a re-started session.", "normalized")]
        answer = ('- "The peer\u2019s hold\u2011time is never zero in a re- started session." '
                  "(normalized \u00b7 rfc9999.txt:150)\n")
        self.assertEqual(ac.check(answer, events)["checks"]["quotations"]["normalized"], 1)

    def test_omission_marks_split_the_quotation_like_the_engine(self):
        events = [fx.verify("The hold timer defaults ... A speaker MUST restart the hold timer", "normalized")]
        answer = ('- "The hold timer defaults \u2026 A speaker MUST restart the hold timer" '
                  "(normalized \u00b7 rfc9999.txt:118)\n")
        self.assertEqual(ac.check(answer, events)["checks"]["quotations"]["normalized"], 1)

    def test_a_part_of_a_longer_checked_quotation_counts_as_checked(self):
        events = [fx.verify(fx.Q_HOLD + " " + fx.Q_RESTART, "exact")]
        result = ac.check('- "A speaker MUST restart the hold timer" (verified \u00b7 rfc9999.txt:118)\n', events)
        self.assertEqual((result["checks"]["quotations"]["exact"], result["findings"]), (1, []))

    def test_each_kind_of_flag_with_its_line(self):
        events = [fx.verify(fx.Q_HOLD, "normalized"),
                  fx.verify("It was never said in the document at all.", "not_found"),
                  fx.call("verify_quote", "engine error", is_error=True, ref="rfc9999#3.1",
                          quote="A failed check of this text.")]
        answer = ('- "The hold timer defaults to 180 seconds." (verified \u00b7 rfc9999.txt:118)\n'
                  '- "It was never said in the document at all." (verified \u00b7 rfc9999.txt:9)\n'
                  '- "A failed check of this text." (normalized \u00b7 rfc9999.txt:10)\n'
                  '- "No call was made for this sentence anywhere." (verified \u00b7 rfc9999.txt:11)\n'
                  '- "An honest label for a sentence nobody could check." (unverified \u00b7 the section was not served)\n')
        result = ac.check(answer, events)
        self.assertEqual([(item["kind"], item["line"]) for item in found(result, "quotations")],
                         [("label-overstates", 1), ("not-found", 2), ("check-failed", 3), ("no-check", 4)])
        self.assertEqual(found(result, "quotations")[1],
                         dict(check="quotations", kind="not-found", line=2, label="verified",
                              excerpt="It was never said in the document at all."))
        self.assertEqual(numbers(result, "quotations"), dict(status="ok", total=5, exact=0, normalized=1,
                                                             labelled_unverified=1, flagged=4))

    def test_a_long_quoted_string_with_no_marker_is_a_quotation_and_a_short_one_is_a_term(self):
        answer = ('The guide says "Use the example timers keepalive holdtime command to change the timers." and '
                  'calls the state "Idle".\n')
        result = ac.check(answer, [fx.verify(fx.Q_COMMAND, "exact")])
        self.assertEqual((result["checks"]["quotations"]["total"], result["checks"]["quotations"]["exact"],
                          kinds(result, "quotations")), (1, 1, ["no-label"]))
        self.assertEqual(kinds(ac.check(answer, []), "quotations"), ["no-check"])

    def test_the_users_own_words_quoted_back_are_not_a_quotation(self):
        answer = 'You asked "how do I change it on IOS XE 17.9" and the answer is below.\n'
        result = ac.check(answer, fx.clean_events(), question=fx.QUESTION)
        self.assertEqual(result["checks"]["quotations"]["total"], 0)

    def test_quotes_inside_code_are_not_quotations(self):
        answer = ('Send `{"text": "example timers", "mode": "bm25"}` and\n```\n'
                  'print("a quoted string in a code block")\n```\n')
        self.assertEqual(ac.check(answer, [])["checks"]["quotations"]["total"], 0)

    def test_the_json_form_of_a_verify_result_is_read(self):
        event = fx.call("verify_quote", json.dumps(dict(result="exact", section_id="rfc9999#3.1",
                                                         local="rfc9999.txt:118", match_text=fx.Q_HOLD)),
                        ref="rfc9999#3.1", quote=fx.Q_HOLD, format="json")
        answer = '- "The hold timer defaults to 180 seconds." (verified \u00b7 rfc9999.txt:118)\n'
        self.assertEqual(ac.check(answer, [event])["checks"]["quotations"]["exact"], 1)

    LIST_ANSWER = ('- "The hold timer defaults to 180 seconds." (verified | rfc9999.txt:118)\n'
                   '- "A speaker MUST restart the hold timer when it receives a KEEPALIVE message." '
                   '(normalized | rfc9999.txt:140-141)\n'
                   '- "Use the example timers keepalive holdtime command to change the timers." '
                   '(verified | exguide-1.txt:401)\n'
                   '- "The timer is never restarted by a peer." (verified | rfc9999.txt:120)\n')
    LIST_QUOTES = [dict(ref="rfc9999#3.1", quote=fx.Q_HOLD), dict(ref="rfc9999#3.1", quote=fx.Q_RESTART),
                   dict(ref="nosuch#1", quote=fx.Q_COMMAND),
                   dict(ref="rfc9999#3.1", quote="The timer is never restarted by a peer.")]

    def test_a_list_of_quotations_in_one_call_is_read_item_by_item(self):
        # a gateway after 0.1.0-alpha.2 checks up to 16 quotations in one call: one line per item, by its number
        reply = ('<verify n=1 ref="rfc9999#3.1" result="exact" scope="section" section="rfc9999#3.1" '
                 'local="rfc9999.txt:118"/>\n'
                 '<verify n=2 ref="rfc9999#3.1" result="normalized" scope="document" section="rfc9999#3.2" '
                 'local="rfc9999.txt:140-141"/> found outside rfc9999#3.1: cite rfc9999#3.2\n'
                 '<verify n=3 ref="nosuch#1" error="unknown reference: nosuch#1"/>\n'
                 '<verify n=4 ref="rfc9999#3.1" result="not_found" reason="parts-too-far" scope="section" '
                 'section="rfc9999#3.1"/>')
        event = fx.call("verify_quote", reply, quotes=self.LIST_QUOTES)
        result = ac.check(self.LIST_ANSWER, [event])
        self.assertEqual(numbers(result, "quotations"), dict(status="ok", total=4, exact=1, normalized=1,
                                                             labelled_unverified=0, flagged=2))
        self.assertEqual([(item["kind"], item["line"]) for item in result["findings"]
                          if item["check"] == "quotations"], [("check-failed", 3), ("not-found", 4)])
        self.assertEqual(result["checks"]["counts"]["by_tool"], dict(verify_quote=1))
        self.assertEqual(ac.summarize([event]), [dict(
            tool="verify_quote", ids=["rfc9999#3.1", "rfc9999#3.2", "rfc9999#3.1"],
            results=["exact", "normalized", "error", "not_found"])])

    def test_the_json_form_of_a_list_reply_is_read(self):
        reply = json.dumps(dict(results=[dict(n=1, ref="rfc9999#3.1", result="exact"),
                                         dict(n=2, ref="rfc9999#3.1", result="normalized"),
                                         dict(n=3, ref="nosuch#1", error="unknown reference: nosuch#1"),
                                         dict(n=4, ref="rfc9999#3.1", result="not_found")], degraded=[]))
        event = fx.call("verify_quote", reply, quotes=self.LIST_QUOTES, format="json")
        result = ac.check(self.LIST_ANSWER, [event])
        self.assertEqual(numbers(result, "quotations"), dict(status="ok", total=4, exact=1, normalized=1,
                                                             labelled_unverified=0, flagged=2))
        self.assertEqual(ac.summarize([event]), [dict(tool="verify_quote", ids=[],
                                                      results=["exact", "normalized", "error", "not_found"])])

    def test_a_list_call_that_failed_or_lost_a_line_checks_nothing_for_that_item(self):
        failed = fx.call("verify_quote", "give ref and quote, or quotes (a list of up to 16 {ref, quote})", True,
                         quotes=self.LIST_QUOTES)
        kinds = [item["kind"] for item in ac.check(self.LIST_ANSWER, [failed])["findings"]
                 if item["check"] == "quotations"]
        self.assertEqual(kinds, ["check-failed"] * 4)
        short = fx.call("verify_quote", '<verify n=1 ref="rfc9999#3.1" result="exact" section="rfc9999#3.1"/>',
                        quotes=self.LIST_QUOTES)
        result = ac.check(self.LIST_ANSWER, [short])
        self.assertEqual((result["checks"]["quotations"]["exact"], result["checks"]["quotations"]["flagged"]), (1, 3))
        # a list that is not a list of {ref, quote} items checks nothing and never raises
        odd = fx.call("verify_quote", "", quotes=["text", 5, dict(ref="rfc9999#3.1")])
        self.assertEqual(ac.check(self.LIST_ANSWER, [odd])["checks"]["quotations"]["flagged"], 4)


class Rfcs(unittest.TestCase):
    def test_an_rfc_with_no_lookup_is_flagged(self):
        result = ac.check("RFC 9999 and RFC 7777 both describe it.\n\nSee RFCs 6666 and 5555.\n",
                          [fx.call("lookup_rfc", fx.RFC_9999, rfc=9999)])
        self.assertEqual(numbers(result, "rfcs"), dict(status="ok", total=4, looked_up=1, flagged=3))
        # the excerpt is the mention as the answer wrote it; rfc is the number it stands for
        self.assertEqual([(item["rfc"], item["excerpt"], item["line"]) for item in found(result, "rfcs")],
                         [(7777, "RFC 7777", 1), (6666, "RFCs 6666 and 5555", 3), (5555, "RFCs 6666 and 5555", 3)])
        self.assertIn("  - to check: RFC 5555 (no lookup_rfc call in this answer)", ac.footer(result).split("\n"))

    def test_an_obsoleted_rfc_needs_its_successor_named(self):
        events = [fx.call("lookup_rfc", fx.RFC_8888, rfc="RFC 8888")]
        result = ac.check("RFC 8888 defines the timer.\n", events)
        self.assertEqual(found(result, "rfcs"), [dict(check="rfcs", kind="obsolete-without-successor", line=1,
                                                      excerpt="RFC 8888", rfc=8888, successors=[9999])])
        events.append(fx.call("lookup_rfc", fx.RFC_9999, rfc=9999))
        result = ac.check("RFC 8888 defined the timer [RFC 8888 \u00a72, obsoleted by RFC 9999].\n", events)
        self.assertEqual(found(result, "rfcs"), [])

    def test_file_names_section_ids_and_quoted_text_do_not_name_an_rfc(self):
        answer = ('Read: rfc7777#3, rfc6666.txt:12. The source says "as RFC 5555 requires in every case here." '
                  "(unverified \u00b7 not served)\n")
        self.assertEqual(ac.check(answer, [])["checks"]["rfcs"]["total"], 0)

    def test_an_rfc_named_only_in_a_footnote_counts(self):
        result = ac.check("The timer is restarted. [^1]\n\n[^1]: [RFC 7777 \u00a73]\n", [])
        self.assertEqual([(item["excerpt"], item["line"]) for item in found(result, "rfcs")], [("RFC 7777", 3)])

    def test_the_json_form_of_a_lookup_is_read(self):
        event = fx.call("lookup_rfc", json.dumps(dict(rfc=8888, status="Proposed Standard", obsoleted_by=[9999])),
                        rfc=8888, format="json")
        self.assertEqual(kinds(ac.check("RFC 8888 defines it.\n", [event]), "rfcs"), ["obsolete-without-successor"])


class Gaps(unittest.TestCase):
    OUTLINE = [fx.call("get_outline", fx.OUTLINE, doc="exguide-1", depth=1)]

    def test_a_gap_label_needs_the_outline_of_the_document_it_names(self):
        answer = "**Confidence and gaps.** Dampening: not in corpus (outline checked: exguide-1 > Example Protocol).\n"
        self.assertEqual(numbers(ac.check(answer, self.OUTLINE), "gaps"),
                         dict(status="ok", total=1, with_outline=1, budget=0, flagged=0))
        result = ac.check(answer.replace("exguide-1 >", "exguide-2 >"), self.OUTLINE)
        self.assertEqual([(item["kind"], item["docs"]) for item in found(result, "gaps")],
                         [("outline-not-read", ["exguide-2"])])

    def test_a_gap_with_no_outline_read_at_all_is_flagged(self):
        for sentence in ("The dampening default is not in corpus.", "The dampening default is not in the corpus.",
                         "I could not find a default for dampening.", "The documents do not say what the default is.",
                         "Dampening is lab-only (no document states it).",
                         "Dampening: release drift (only in exguide-2 17.12)."):
            with self.subTest(sentence=sentence):
                result = ac.check("Text.\n\n" + sentence + "\n", [])
                self.assertEqual([(item["kind"], item["line"], item["excerpt"]) for item in found(result, "gaps")],
                                 [("gap-without-outline", 3, sentence)])
                self.assertEqual(kinds(ac.check(sentence + "\n", self.OUTLINE), "gaps"), [])

    def test_the_budget_label_is_counted_and_not_flagged(self):
        # outline-check.md: the one label written without an outline read, and it says so
        result = ac.check("Text.\n\nDampening: not in corpus (outline not checked: budget).\n", [])
        self.assertEqual(numbers(result, "gaps"), dict(status="ok", total=1, with_outline=0, budget=1, flagged=0))
        self.assertIn('- "Not covered" statements: 1. With an outline read: 0. Labeled "outline not checked": 1.',
                      ac.footer(ac.check("Dampening: not in corpus (outline not checked: budget).\n",
                                         [fx.call("rag_status", fx.STATUS_OK)])).split("\n"))

    def test_the_inference_label_is_a_gap_only_under_confidence_and_gaps(self):
        label = "Dampening default: inference (from [RFC 9999 \u00a73.1] + [RFC 9999 \u00a73.2])."
        self.assertEqual(kinds(ac.check("**Confidence and gaps.** " + label + "\n", []), "gaps"),
                         ["gap-without-outline"])
        self.assertEqual(ac.check("**Analysis**\n\n" + label + "\n", [])["checks"]["gaps"]["total"], 0)

    def test_ordinary_prose_is_not_a_gap(self):
        answer = "The router could not find a route, and prefixes not covered by the aggregate are sent as they are.\n"
        self.assertEqual(ac.check(answer, [])["checks"]["gaps"]["total"], 0)


class Degraded(unittest.TestCase):
    def state(self, answer, events):
        result = ac.check(answer, events)
        value = result["checks"]["degraded"]
        return value["state"], value["backends"], kinds(result, "degraded")

    def test_a_degraded_reply_needs_the_wording_in_the_first_lines(self):
        events = [fx.call("rag_status", fx.STATUS_OK), fx.call("search_rag", "degraded: dense; reranker\n" + fx.SEARCH)]
        self.assertEqual(self.state("The hold timer is 180 seconds.\n", events),
                         ("not-stated", ["dense", "reranker"], ["degraded-not-stated"]))
        stated = ("Search degraded: dense; reranker (dense down = keyword-only recall).\n\n"
                  "The hold timer is 180 seconds.\n")
        self.assertEqual(self.state(stated, events), ("stated", ["dense", "reranker"], []))

    def test_a_backend_that_rag_status_shows_off_or_down_counts(self):
        self.assertEqual(self.state("Answer.\n", [fx.call("rag_status", fx.STATUS_RERANKER_OFF)])[:2],
                         ("not-stated", ["reranker"]))
        down = fx.STATUS_OK.replace("dense: ok; breaker: closed", "dense: DOWN; error: timeout")
        down = down.replace("degraded: none", "degraded: dense")
        self.assertEqual(self.state("Answer.\n", [fx.call("rag_status", down)])[:2], ("not-stated", ["dense"]))
        as_json = json.dumps(dict(ok=True, generation="g-synthetic-1", degraded=[],
                                  backends=dict(bm25=dict(ok=True), reranker=dict(ok=None, enabled=False))))
        self.assertEqual(self.state("Answer.\n", [fx.call("rag_status", as_json, format="json")])[:2],
                         ("not-stated", ["reranker"]))

    def test_full_strength_needs_a_rag_status_call(self):
        self.assertEqual(self.state("Answer.\n", [fx.call("rag_status", fx.STATUS_OK)]), ("none", [], []))
        self.assertEqual(self.state("Answer.\n", [fx.call("search_rag", fx.SEARCH)]), ("unknown", [], []))

    def test_the_index_is_read_from_rag_status(self):
        result = ac.check("Answer.\n", [fx.call("rag_status", fx.STATUS_OK)])
        self.assertEqual(result["index"], dict(generation="g-synthetic-1", corpus_fingerprint="abc123def456"))
        self.assertIsNone(ac.check("Answer.\n", [fx.call("search_rag", fx.SEARCH)])["index"])


class Counts(unittest.TestCase):
    def test_calls_are_counted_per_tool_and_a_wrong_count_in_the_answer_is_flagged(self):
        result = ac.check(fx.FLAGGED_ANSWER, fx.flagged_events(), question=fx.QUESTION)
        counts = result["checks"]["counts"]
        self.assertEqual(counts["total"], 8)
        self.assertEqual(counts["by_tool"], dict(rag_status=1, search_many=1, get_section=1, lookup_rfc=2,
                                                 verify_quote=3))
        self.assertEqual(found(result, "counts"), [dict(check="counts", kind="count-differs", line=11,
                                                        excerpt="RAG: 12 deep32 calls", said=12, counted=8)])
        right = fx.FLAGGED_ANSWER.replace("RAG: 12 deep32 calls", "RAG: 8 deep32 calls")
        self.assertEqual(found(ac.check(right, fx.flagged_events()), "counts"), [])

    def test_events_with_an_unknown_tool_or_a_wrong_shape_are_left_out(self):
        events = [fx.call("shell", "x"), "text", None, dict(tool=7),
                  dict(tool="mcp__deep32__rag_status", result=fx.STATUS_OK)]
        self.assertEqual(ac.check("Answer.\n", events)["checks"]["counts"]["by_tool"], dict(rag_status=1))


class Specifics(unittest.TestCase):
    def check(self, answer, events=None, question=""):
        return ac.check(answer, fx.clean_events() if events is None else events, question=question)

    def test_each_class_is_found_in_the_retrieved_text(self):
        answer = ("The hold timer is 180 seconds [RFC 9999 \u00a73.1] and the keepalive interval is 60-second on "
                  "IOS XE 17.9; use `example timers keepalive holdtime`, range to 65535.\n")
        result = self.check(answer)
        self.assertEqual(numbers(result, "specifics"), dict(status="ok", total=6, sourced=6, in_question=0,
                                                            labelled=0, flagged=0))

    def test_each_class_is_flagged_when_it_occurs_nowhere(self):
        answer = ("It waits 750 seconds, sends to TCP port 4999 from 192.0.2.77 on IOS XE 17.12, as RFC 9999 \u00a77.4 "
                  "says, with `example dampening half-life` and a hello interval of 4321.\n")
        result = self.check(answer)
        self.assertEqual(kinds(result, "specifics"),
                         ["quantity", "port", "address", "release", "rfc-section", "command", "timer"])
        self.assertEqual(excerpts(result, "specifics"),
                         ["750 seconds", "TCP port 4999", "192.0.2.77", "IOS XE 17.12", "RFC 9999 \u00a77.4",
                          "example dampening half-life", "hello interval of 4321"])
        self.assertEqual((result["checks"]["specifics"]["total"], result["checks"]["specifics"]["sourced"]), (7, 0))

    def test_a_unit_matches_any_spelling_of_its_family(self):
        events = [fx.call("get_section", "The timer is 30 s; the delay is 1,500 milliseconds; 90 % of peers.")]
        result = self.check("It is 30 seconds, then 1500 ms, for 90 percent.\n", events)
        self.assertEqual((result["checks"]["specifics"]["total"], result["checks"]["specifics"]["sourced"]), (3, 3))
        self.assertEqual(excerpts(self.check("It is 30 minutes.\n", events), "specifics"), ["30 minutes"])

    def test_a_value_from_the_question_is_not_flagged(self):
        result = self.check("With a 45 seconds timer it fails.\n", question="Why does it fail with a 45-second timer?")
        self.assertEqual((result["checks"]["specifics"]["in_question"], found(result, "specifics")), (1, []))

    def test_text_labelled_unsourced_or_inference_is_counted_apart(self):
        answer = ("Suppose the peer sends 999 packets. Inference from the rule above: after 888 seconds it stops.\n\n"
                  "A typical lab uses 777 seconds and 666 packets. (unsourced)\n")
        result = self.check(answer)
        self.assertEqual(excerpts(result, "specifics"), ["999 packets"])
        self.assertEqual((result["checks"]["specifics"]["total"], result["checks"]["specifics"]["labelled"]), (4, 3))

    def test_a_code_block_gives_one_finding_at_its_first_missing_line(self):
        answer = ("Configure it:\n\n```\nrouter example 65000\n example timers keepalive holdtime\n"
                  " example dampening half-life 15\n example dampening reuse 750\n! a comment\n```\n")
        result = self.check(answer)
        self.assertEqual((result["checks"]["specifics"]["total"], result["checks"]["specifics"]["sourced"]), (4, 1))
        self.assertEqual(found(result, "specifics"), [dict(check="specifics", kind="code", line=4, lines=3,
                                                           excerpt="router example 65000")])

    def test_placeholders_arrows_tool_names_and_bare_values_in_backticks_are_not_commands(self):
        answer = ("Use `example timers keepalive holdtime <seconds>` and read `a \u2192 b \u2192 c`, call "
                  "`verify_quote`, then `180` and `rfc9999#3.1`.\n")
        result = self.check(answer)
        self.assertEqual(numbers(result, "specifics"), dict(status="ok", total=1, sourced=1, in_question=0,
                                                            labelled=0, flagged=0))

    def test_the_answers_own_count_lines_and_quotations_are_left_out(self):
        answer = ('- "It waits 750 seconds before it stops." (unverified \u00b7 not served)\n\n'
                  "RAG: 750 deep32 calls; docs: rfc9999\n**Read.** rfc9999#3.1 (750 seconds)\n")
        self.assertEqual(self.check(answer)["checks"]["specifics"]["total"], 0)

    def test_a_finding_carries_the_line_of_the_value(self):
        answer = "Line one is plain.\nIt then waits\nfor 750 seconds\nand stops.\n\nLater: 760 seconds.\n"
        result = self.check(answer)
        self.assertEqual([(item["excerpt"], item["line"]) for item in found(result, "specifics")],
                         [("750 seconds", 3), ("760 seconds", 6)])

    def test_tool_results_over_the_capture_limit_make_the_check_unavailable(self):
        events = fx.clean_events()
        events[2] = dict(events[2], truncated=True)
        result = ac.check(fx.CLEAN_ANSWER, events, question=fx.QUESTION)
        self.assertEqual(numbers(result, "specifics"), dict(status="unavailable", reason="capture-limit", flagged=0))
        self.assertIn(TOO_LARGE_LINE, ac.footer(result).split("\n"))


class Citations(unittest.TestCase):
    def check(self, answer):
        return ac.check(answer, fx.clean_events())

    def test_a_sentence_with_a_specific_and_no_citation_is_flagged_in_the_cited_sections_only(self):
        answer = ("**Answer.** The hold timer is 180 seconds. It restarts on every KEEPALIVE.\n\n"
                  "**How it works.** The timer runs for 180 seconds.\n\n"
                  "**Evidence**\n- The default is 180 seconds.\n- The default is 180 seconds [RFC 9999 \u00a73.1].\n\n"
                  "**RFC vs Cisco.** IOS XE 17.9 uses 60 seconds.\n")
        result = self.check(answer)
        self.assertEqual(numbers(result, "citations"), dict(status="ok", sentences=4, own=1, later=0, flagged=3))
        self.assertEqual([(item["excerpt"], item["line"]) for item in found(result, "citations")],
                         [("The hold timer is 180 seconds.", 1), ("The default is 180 seconds.", 6),
                          ("IOS XE 17.9 uses 60 seconds.", 9)])

    def test_a_citation_later_in_the_paragraph_covers_the_sentences_before_it(self):
        answer = "**Answer.** The hold timer is 180 seconds. It is restarted. [RFC 9999 \u00a73.1] Then 60 seconds pass.\n"
        result = self.check(answer)
        self.assertEqual(numbers(result, "citations"), dict(status="ok", sentences=2, own=0, later=1, flagged=1))
        self.assertEqual(excerpts(result, "citations"), ["Then 60 seconds pass."])

    def test_every_citation_form_counts(self):
        for marker in ("[RFC 9999 \u00a73.1]", "[Example Guide IOS XE 17.9 \u203a Configuring Timers]", "rfc9999.txt:118",
                       "rfc9999#3.1", '"The hold timer defaults to 180 seconds." (verified \u00b7 rfc9999.txt:118)',
                       "[^1]"):
            answer = "The hold timer is 180 seconds %s.\n\n[^1]: [RFC 9999 \u00a73.1] rfc9999.txt:118\n" % marker
            with self.subTest(marker=marker):
                self.assertEqual(found(self.check(answer), "citations"), [])
        self.assertEqual(len(found(self.check("The hold timer is 180 seconds [^9].\n"), "citations")), 1)

    def test_a_sentence_labelled_inference_is_not_counted(self):
        result = self.check("Inference: the timer is 180 seconds here too.\n")
        self.assertEqual(result["checks"]["citations"]["sentences"], 0)


class Whole(unittest.TestCase):
    def test_golden_footer_all_clean(self):
        result = ac.check(fx.CLEAN_ANSWER, fx.clean_events(), question=fx.QUESTION)
        self.assertEqual(ac.footer(result), CLEAN_FOOTER)

    def test_golden_footer_with_flags(self):
        result = ac.check(fx.FLAGGED_ANSWER, fx.flagged_events(), question=fx.QUESTION)
        self.assertEqual(result["to_check"], 10)
        self.assertEqual(ac.footer(result), FLAGGED_FOOTER)

    def test_golden_footer_with_no_tool_events(self):
        result = ac.check(fx.CLEAN_ANSWER, [], question=fx.QUESTION)
        self.assertEqual(result["status"], "no-events")
        self.assertEqual(ac.footer(result), NO_EVENTS_FOOTER)
        self.assertEqual(ac.footer(ac.check("Deep32 search is unavailable right now.", [])), NOTHING_FOOTER)

    def test_the_result_object_has_the_documented_members(self):
        result = ac.check(fx.FLAGGED_ANSWER, fx.flagged_events(), question=fx.QUESTION)
        self.assertEqual(sorted(result), ["checks", "findings", "index", "status", "to_check", "version"])
        self.assertEqual(list(result["checks"]), list(ac.CHECKS))
        self.assertEqual((result["version"], result["status"], len(result["findings"])), (1, "ok", 10))
        for item in result["findings"]:
            self.assertLessEqual({"check", "kind", "line", "excerpt"}, set(item))
            self.assertIn(item["check"], ac.CHECKS)
            self.assertIsInstance(item["line"], int)
            self.assertGreaterEqual(item["line"], 1)
        self.assertEqual([(item["check"], item["line"]) for item in result["findings"]],
                         [("quotations", 5), ("quotations", 6), ("gaps", 9), ("degraded", 1), ("counts", 11),
                          ("specifics", 1), ("specifics", 1), ("specifics", 7), ("citations", 1), ("citations", 7)])

    def test_more_than_three_findings_are_summed_up(self):
        answer = "".join("It waits %d seconds.\n\n" % n for n in (701, 702, 703, 704, 705))
        text = ac.footer(ac.check(answer, [fx.call("rag_status", fx.STATUS_OK)]))
        self.assertIn("  - to check: 703 seconds\n  - and 2 more\n", text)

    def test_at_most_twenty_findings_per_check_are_kept_and_all_are_counted(self):
        answer = "".join("It waits %d seconds.\n\n" % n for n in range(700, 730))
        result = ac.check(answer, [fx.call("rag_status", fx.STATUS_OK)])
        self.assertEqual((len(found(result, "specifics")), result["checks"]["specifics"]["flagged"]), (20, 30))
        # each of the thirty sentences is also an uncited sentence of the Answer section: thirty more
        self.assertEqual((len(found(result, "citations")), result["checks"]["citations"]["flagged"]), (20, 30))
        self.assertEqual((len(result["findings"]), result["to_check"]), (40, 60))

    def test_an_answer_with_no_markers_at_all(self):
        result = ac.check("The session stays up as long as both peers answer.\n", fx.clean_events())
        self.assertEqual((result["status"], result["to_check"]), ("ok", 0))
        self.assertEqual(result["checks"]["quotations"]["total"], 0)
        self.assertTrue(ac.footer(result).endswith("To check: nothing."))

    def test_text_outside_ascii(self):
        answer = ("**Antwort.** Der Timer l\u00e4uft 750 Sekunden \u2013 \u00fcber \u00bbHold\u00ab hinaus. 180 seconds "
                  "\u201c\u30bf\u30a4\u30de\u30fc\u306f180\u79d2\u3067\u3059\u3002\u30bf\u30a4\u30de\u30fc\u3002\u201d "
                  "(verified \u00b7 rfc9999.txt:118)\n")
        result = ac.check(answer, fx.clean_events())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(kinds(result, "quotations"), ["no-check"])
        text = ac.footer(result)
        self.assertTrue(text.startswith("---\nChecks (by script, no model):\n"))
        self.assertIn("\u30bf\u30a4\u30de\u30fc", text)

    def test_a_failure_inside_one_check_leaves_the_others_and_never_raises(self):
        original = ac.check_rfcs
        ac.check_rfcs = lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic"))
        try:
            result = ac.check(fx.CLEAN_ANSWER, fx.clean_events(), question=fx.QUESTION)
        finally:
            ac.check_rfcs = original
        self.assertEqual(numbers(result, "rfcs"), dict(status="unavailable", reason="error:RuntimeError", flagged=0))
        self.assertEqual(result["checks"]["quotations"]["exact"], 2)
        self.assertIn("- RFCs: not checked (the check did not run).", ac.footer(result).split("\n"))
        # a check that did not run is never read as a clean result
        self.assertTrue(ac.footer(result).endswith("\nTo check: nothing was flagged. Not run: 1 of the 7 checks."))

    def test_a_failed_check_gives_a_result_and_no_footer(self):
        for answer, events in ((None, []), (b"bytes", []), (7, None)):
            result = ac.check(answer, events)
            self.assertEqual(result, dict(version=1, status="failed", error="TypeError"))
            self.assertEqual(ac.footer(result), "")
        self.assertEqual(ac.footer(None), "")
        self.assertEqual(ac.footer({"status": "ok"}), "")
        self.assertEqual(ac.check("x", "not a list")["status"], "no-events")

    def test_the_time_budget_stops_the_checks_that_have_not_run(self):
        result = ac.check(fx.CLEAN_ANSWER, fx.clean_events(), budget_seconds=-1)
        self.assertEqual({item["status"] for item in result["checks"].values()}, {"unavailable"})
        self.assertIn("- Quotations: not checked (the script ran out of time).", ac.footer(result))
        self.assertTrue(ac.footer(result).endswith("\nTo check: nothing was flagged. Not run: 7 of the 7 checks."))

    def test_flags_and_a_check_that_did_not_run_are_both_named_in_the_last_line(self):
        events = fx.flagged_events()
        events[2] = dict(events[2], truncated=True)
        result = ac.check(fx.FLAGGED_ANSWER, events, question=fx.QUESTION)
        self.assertEqual((result["to_check"], result["checks"]["specifics"]["status"]), (7, "unavailable"))
        self.assertTrue(ac.footer(result).endswith(
            "\nTo check: 7. A flag means the script could not confirm the item, not that it is wrong. "
            "Not run: 1 of the 7 checks."))

    def test_the_result_is_json_and_redact_removes_every_text_of_the_answer(self):
        result = ac.check(fx.FLAGGED_ANSWER, fx.flagged_events(), question=fx.QUESTION)
        json.dumps(result)
        bare = ac.redact(result)
        text = json.dumps(bare)
        for fragment in ("750", "192.0.2.77", "KEEPALIVE", "dampening", "17.12", "excerpt"):
            self.assertNotIn(fragment, text)
        self.assertEqual((bare["to_check"], bare["checks"], bare["index"]),
                         (10, result["checks"], result["index"]))
        self.assertEqual(bare["findings"][:2], [dict(check="quotations", kind="label-overstates", line=5),
                                                dict(check="quotations", kind="not-found", line=6)])
        self.assertEqual(result["findings"][0]["excerpt"], fx.Q_RESTART)

    def test_summarize_keeps_ids_and_outcomes_and_no_passage_or_query_text(self):
        summary = ac.summarize(fx.flagged_events())
        self.assertEqual([item["tool"] for item in summary],
                         ["rag_status", "search_many", "get_section", "lookup_rfc", "lookup_rfc", "verify_quote",
                          "verify_quote", "verify_quote"])
        self.assertEqual(summary[1], dict(tool="search_many", ids=["rfc9999:c0007", "exguide-1:c0031"],
                                          degraded=["reranker"]))
        self.assertEqual(summary[2], dict(tool="get_section", ids=["rfc9999#3.1"]))
        self.assertEqual(summary[4], dict(tool="lookup_rfc", ids=["rfc8888"]))
        self.assertEqual(summary[5], dict(tool="verify_quote", ids=["rfc9999#3.1"], result="exact",
                                          local="rfc9999.txt:118"))
        self.assertEqual(summary[7], dict(tool="verify_quote", ids=["rfc9999#3.1"], result="not_found"))
        text = json.dumps(summary)
        for fragment in ("hold timer", "example timers", "KEEPALIVE"):
            self.assertNotIn(fragment, text)


class CommandLine(unittest.TestCase):
    def files(self, folder, answer, events):
        answer_file, events_file = Path(folder, "answer.md"), Path(folder, "events.jsonl")
        answer_file.write_bytes(answer.encode("utf-8"))
        events_file.write_bytes("".join(json.dumps(event) + "\n" for event in events).encode("utf-8"))
        return ["--events", str(events_file), "--answer", str(answer_file)]

    def run_main(self, answer, events, *extra):
        with tempfile.TemporaryDirectory(dir=os.environ.get("DEEP32_RELAY_TEST_ROOT") or None) as folder:
            out = io.BytesIO()
            stream = io.TextIOWrapper(out, encoding="ascii")        # a console that cannot print every character
            with contextlib.redirect_stdout(stream):
                code = ac.main(self.files(folder, answer, events) + list(extra))
            stream.flush()
            return code, out.getvalue().decode("utf-8")

    def test_prints_the_footer_or_the_result(self):
        self.assertEqual(self.run_main(fx.CLEAN_ANSWER, fx.clean_events()), (0, CLEAN_FOOTER + "\n"))
        code, text = self.run_main(fx.FLAGGED_ANSWER, fx.flagged_events(), "--json")
        self.assertEqual((code, json.loads(text)["to_check"]), (0, 10))

    def test_the_question_file_keeps_its_values_from_being_flagged(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("DEEP32_RELAY_TEST_ROOT") or None) as folder:
            question = Path(folder, "question.txt")
            question.write_bytes(b"Why does it fail with a 45-second timer?")
            arguments = self.files(folder, "With a 45 seconds timer it fails.\n", fx.clean_events())
            out = io.BytesIO()
            stream = io.TextIOWrapper(out, encoding="utf-8")
            with contextlib.redirect_stdout(stream):
                code = ac.main(arguments + ["--question", str(question), "--json"])
            stream.flush()
        self.assertEqual((code, json.loads(out.getvalue().decode("utf-8"))["checks"]["specifics"]["in_question"]),
                         (0, 1))

    def test_text_outside_ascii_is_written_as_utf8_whatever_the_console(self):
        code, text = self.run_main('- "\u30bf\u30a4\u30de\u30fc\u306f180\u79d2\u3067\u3059\u3002\u30bf\u30a4\u30de\u30fc\u3002" '
                                   "(verified \u00b7 rfc9999.txt:118)\n", fx.clean_events())
        self.assertEqual(code, 0)
        self.assertIn("\u30bf\u30a4\u30de\u30fc", text)

    def test_a_missing_file_is_exit_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ac.main(["--events", "no-such-file", "--answer", "no-such-file"]), 2)

    def test_the_file_runs_as_a_script_in_isolated_mode_from_any_folder(self):
        # The documented command: python -I -B <path>/answer_check.py. In isolated mode neither the current folder
        # nor the script's folder is on sys.path, so this passes only while the file imports nothing of the package.
        with tempfile.TemporaryDirectory(dir=os.environ.get("DEEP32_RELAY_TEST_ROOT") or None) as folder:
            done = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT)] +
                                  self.files(folder, fx.CLEAN_ANSWER, fx.clean_events()),
                                  cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=120)
        self.assertEqual((done.returncode, done.stdout.decode("utf-8").replace("\r\n", "\n")),
                         (0, CLEAN_FOOTER + "\n"))


if __name__ == "__main__":
    unittest.main()
