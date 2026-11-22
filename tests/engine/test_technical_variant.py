"""0.1.0-alpha.3, WP-D item D7 (bench switches, defaults unchanged): retrieval.json technical_variant.weight and
technical_variant.tokens (rag_core.query_plan). Without the object the plan is the one before: the same variants,
ids, texts and weights."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

from rag_core.query_plan import (
    TechnicalVariantSettings, build_query_plan, technical_terms, technical_variant_settings, technical_vocabulary,
)

import test_fold_identical
from test_api_v2 import RootClient

QUESTIONS = [
    "Why does OSPF stay in EXSTART with MTU 1500?",
    "Router R1 and R2 are neighbors. Which statement explains why I cannot reach them.",
    "show ip ospf neighbor",
    "What is the difference between redistribute and summarization on area-id 0.0.0.1:",
    "translator",
]


def plan(question, **kw):
    return [(v.variant_id, v.purpose, v.text, v.weight, v.retrievers)
            for v in build_query_plan(question, "normal", technical_retrievers=["bm25"], **kw)]


class DefaultIsThePlanBefore(unittest.TestCase):

    def test_v1_terms_are_the_terms_before(self):
        # written out from the code at d84ce2a (query_plan.technical_terms)
        self.assertEqual([technical_terms(q) for q in QUESTIONS], [
            "OSPF EXSTART MTU 1500",
            "R1 R2 neighbors. statement I them.",
            "",
            "difference redistribute summarization area-id 0.0.0.1:",
            "translator",
        ])
        for q in QUESTIONS:
            self.assertEqual(technical_terms(q, "v1", frozenset({"anything"})), technical_terms(q))

    def test_the_default_settings_give_the_plan_before(self):
        for q in QUESTIONS:
            with self.subTest(q=q):
                before = plan(q)
                self.assertEqual(plan(q, technical=TechnicalVariantSettings()), before)
                self.assertEqual(plan(q, technical=technical_variant_settings({"technical_variant": {
                    "weight": 0.95, "tokens": "v1"}})), before)
        first = plan(QUESTIONS[0])
        self.assertEqual([(p, t, w, r) for _, p, t, w, r in first], [
            ("primary", QUESTIONS[0], 1.0, None), ("technical", "OSPF EXSTART MTU 1500", 0.95, ("bm25",))])


class AbsentIsTheBodyBefore(test_fold_identical.OffTests):
    """technical_variant written out with its defaults: the bodies 4f4fe78 sent."""
    retrieval = {"technical_variant": {"weight": 0.95, "tokens": "v1"}}


class SettingTests(unittest.TestCase):

    def test_values(self):
        for config in ({}, {"technical_variant": None}, {"technical_variant": {}}):
            self.assertEqual(technical_variant_settings(config), TechnicalVariantSettings(0.95, "v1"))
        self.assertEqual(technical_variant_settings({"technical_variant": {"weight": 0, "tokens": "v2"}}),
                         TechnicalVariantSettings(0.0, "v2"))
        self.assertEqual(technical_variant_settings({"technical_variant": {"weight": 1}}).weight, 1.0)

    def test_other_values_are_refused(self):
        for bad in (0.5, [], {"weight": -0.1}, {"weight": 1.5}, {"weight": "0.5"}, {"weight": True},
                    {"tokens": "v3"}, {"tokens": 2}, {"rule": "v2"}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "technical_variant"):
                technical_variant_settings({"technical_variant": bad})

    def test_the_start_refuses_a_bad_value(self):
        with self.assertRaisesRegex(ValueError, "technical_variant.weight"):
            RootClient(retrieval={"technical_variant": {"weight": 2}})


class WeightTests(unittest.TestCase):

    def test_a_weight_is_the_variant_s_weight(self):
        q = QUESTIONS[0]
        half = plan(q, technical=TechnicalVariantSettings(0.5, "v1"))
        self.assertEqual([(p, w) for _, p, _, w, _ in half], [("primary", 1.0), ("technical", 0.5)])
        self.assertEqual([i for i, *_ in half], [i for i, *_ in plan(q)])       # the ids do not move with it

    def test_weight_0_leaves_the_variant_out_of_the_plan(self):
        for q in QUESTIONS:
            self.assertEqual([p for _, p, *_ in plan(q, technical=TechnicalVariantSettings(0.0, "v1"))], ["primary"])
        # an empty retriever list is not that: it routes the variant to every retriever
        every = build_query_plan(QUESTIONS[0], "normal", technical_retrievers=[])
        self.assertEqual([(v.purpose, v.retrievers) for v in every], [("primary", None), ("technical", None)])


class TokenTests(unittest.TestCase):
    VOCABULARY = frozenset({"redistribute", "neighbors", "summarization", "translator"})

    def v2(self, q, vocabulary=None):
        return technical_terms(q, "v2", self.VOCABULARY if vocabulary is None else vocabulary)

    def test_v2_strips_a_trailing_mark_drops_single_letters_and_asks_the_vocabulary(self):
        self.assertEqual([self.v2(q) for q in QUESTIONS], [
            "OSPF EXSTART MTU 1500",
            "R1 R2 neighbors",                      # "neighbors." keeps its word; "statement", "I", "them." go
            "",
            "redistribute summarization area-id 0.0.0.1",     # "difference" is no command or glossary word
            "translator",
        ])
        self.assertEqual(self.v2("the hold-time. of AS/ BGP: is 180- and a B C", frozenset()),
                         "hold-time AS BGP 180")
        self.assertEqual(self.v2("configuration troubleshoot", frozenset()), "")
        self.assertEqual(self.v2("Redistribute REDISTRIBUTE"), "Redistribute REDISTRIBUTE")    # case kept, both once

    def test_the_plan_uses_the_rule_and_the_vocabulary(self):
        q = QUESTIONS[1]
        got = plan(q, technical=TechnicalVariantSettings(0.95, "v2"), vocabulary=self.VOCABULARY)
        self.assertEqual([(p, t) for _, p, t, _, _ in got], [("primary", q), ("technical", "R1 R2 neighbors")])
        # a question whose v2 terms are empty has no technical variant
        self.assertEqual([p for _, p, *_ in plan("Which statement explains the difference?",
                                                 technical=TechnicalVariantSettings(0.95, "v2"))], ["primary"])


class ServedTests(unittest.TestCase):
    """The orchestrator reads the vocabulary from the root's kb.sqlite once and plans with it."""

    def test_v2_on_the_tiny_root(self):
        root = RootClient(retrieval={"technical_variant": {"tokens": "v2"}})
        self.addCleanup(root.close)
        orch = root.client.app.state.orchestrator
        self.assertEqual(orch.technical, TechnicalVariantSettings(0.95, "v2"))
        # the fixture's command index: "show ip ospf neighbor ...", "area ... nssa translate type7 ...",
        # "show ip ospf database nssa-external"; its glossary: NSSA, ABR and their expansions
        self.assertEqual(orch.vocabulary, technical_vocabulary(root.paths.root / "kb" / "kb.sqlite"))
        self.assertEqual(orch.vocabulary, frozenset({"translate"}))
        result = orch.search("please translate the redistribution for OSPF.", retrievers=frozenset({"bm25"}),
                             rerank=False)
        self.assertEqual([(v["purpose"], v["text"], v["weight"]) for v in result["query_variants"]],
                         [("primary", "please translate the redistribution for OSPF.", 1.0),
                          ("technical", "translate OSPF", 0.95)])

    def test_weight_0_runs_one_bm25_query_and_the_default_two(self):
        for settings, purposes in (({"technical_variant": {"weight": 0}}, ["primary"]),
                                   ({}, ["primary", "technical"])):
            root = RootClient(retrieval=settings)
            try:
                result = root.client.app.state.orchestrator.search("Why does the OSPF NSSA translator change",
                                                                   rerank=False)
                self.assertEqual(sorted(leg["purpose"] for leg in result["legs"]), purposes)
                self.assertEqual([v["purpose"] for v in result["query_variants"]], purposes)
            finally:
                root.close()

    def test_without_a_kb_v2_keeps_no_plain_long_word(self):
        root = RootClient(kb=False, retrieval={"technical_variant": {"tokens": "v2"}})
        self.addCleanup(root.close)
        orch = root.client.app.state.orchestrator
        self.assertEqual(orch.vocabulary, frozenset())
        result = orch.search("please translate OSPF", rerank=False)
        self.assertEqual([v["text"] for v in result["query_variants"]], ["please translate OSPF", "OSPF"])


if __name__ == "__main__":
    unittest.main()
