"""Invented, offline J10 format and exact delivered-passage scoring tests."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "diagnostics"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep32_diag.selftest_contract import (
    ContractError, METRICS, QUESTIONS_MAX_BYTES, REFERENCE_MAX_BYTES,
    aggregate, aggregate_numerators, canonical_bytes, first_rank, load_bundle, passes,
)
from selftest_support import bundle_bytes, encode


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.qbytes, self.rbytes = bundle_bytes()
        self.q = json.loads(self.qbytes)
        self.r = json.loads(self.rbytes)

    def bundle(self, q=None, r=None, *, fixture_harness=True):
        raw = encode(self.q if q is None else q)
        reference = deepcopy(self.r if r is None else r)
        reference["questions_sha256"] = hashlib.sha256(raw).hexdigest()
        return load_bundle(raw, encode(reference), fixture_harness=fixture_harness)

    def reject_reference(self, path, value):
        reference = deepcopy(self.r)
        cursor = reference
        for item in path[:-1]:
            cursor = cursor[item]
        cursor[path[-1]] = value
        with self.assertRaises(ContractError):
            self.bundle(r=reference)

    def test_valid_bundle_bindings_and_exact_reference(self):
        bundle = load_bundle(self.qbytes, self.rbytes, fixture_harness=True)
        self.assertEqual(bundle.bundle_id, "fixture-beacons-v1")
        self.assertEqual(bundle.release, "0.1.0-alpha.2")
        self.assertTrue(bundle.fixture_only)
        self.assertEqual(bundle.questions_sha256, hashlib.sha256(self.qbytes).hexdigest())
        self.assertEqual(bundle.reference["reference"].exact["mrr_at_10"], Fraction(3, 4))
        self.assertEqual(json.loads(json.dumps(bundle.reference["reference"])), self.r["reference"])

    def test_invented_fixtures_require_python_harness(self):
        with self.assertRaisesRegex(ContractError, "test harness"):
            load_bundle(self.qbytes, self.rbytes)
        raw, reference = bundle_bytes(fixture_only=False)
        self.assertFalse(load_bundle(raw, reference).fixture_only)
        with self.assertRaises(ContractError):
            load_bundle(self.qbytes, self.rbytes, fixture_harness=1)

    def test_bundle_is_recursively_immutable(self):
        bundle = self.bundle()
        for operation in (
            lambda: bundle.questions[0].__setitem__("id", "changed"),
            lambda: bundle.reference.__setitem__("generation", "changed"),
            lambda: bundle.reference["settings"]["reranker"].__setitem__("window", "changed"),
            lambda: bundle.reference["reference"].update(hit_at_1=0),
            lambda: bundle.reference["reference"].exact.__setitem__("hit_at_1", 0),
        ):
            with self.subTest(operation=operation), self.assertRaises((AttributeError, TypeError)):
                operation()

    def test_fixed_profile_refuses_changes_or_extra_filters(self):
        for field, value in (("mode", "bm25"), ("rerank", "off"), ("max_chars", 2401),
                             ("deep", False), ("debug", False), ("format", "text"),
                             ("max_chars", True), ("limit", 32), ("docs", ["fixture"])):
            with self.subTest(field=field):
                self.reject_reference(("request", field), value)

    def test_policy_cannot_relax_tolerance(self):
        for path, value in ((("policy", "tolerance"), 0.021),
                            (("policy", "tolerance"), True),
                            (("policy", "kind"), "relative-drop")):
            self.reject_reference(path, value)

    def test_binding_fields_must_match_exact_question_bytes(self):
        for field, value in (("bundle_id", "other-bundle"), ("release", "other-release"),
                             ("fixture_only", False), ("question_count", 1)):
            with self.subTest(field=field):
                self.reject_reference((field,), value)
        for raw in (self.qbytes + b"\n", self.qbytes.replace(b"amber", b"green")):
            with self.assertRaises(ContractError):
                load_bundle(raw, self.rbytes, fixture_harness=True)

    def test_question_duplicate_ids_and_labels_rejected(self):
        q = deepcopy(self.q)
        q["questions"][1]["id"] = q["questions"][0]["id"]
        with self.assertRaises(ContractError):
            self.bundle(q=q)
        q = deepcopy(self.q)
        q["questions"][0]["labels"] *= 2
        with self.assertRaises(ContractError):
            self.bundle(q=q)

    def test_unknown_or_missing_fields_and_unsafe_id_rejected(self):
        for field, value in (("extra", 1), ("questions_path", "../other"),
                             ("schema_version", True), ("bundle_id", "../other"),
                             ("release", "value\n"), ("fixture_only", 1)):
            q = deepcopy(self.q)
            q[field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.bundle(q=q)
        q = deepcopy(self.q)
        del q["questions"][0]["collection"]
        with self.assertRaises(ContractError):
            self.bundle(q=q)
        self.reject_reference(("questions_path",), "../other")

    def test_question_lengths_empty_labels_and_array_limits(self):
        cases = (("text", ""), ("text", "a" * 2001), ("id", "a" * 97),
                 ("id", True), ("collection", "a" * 65), ("labels", []),
                 ("labels", [{"doc_key": "a", "exact_span": "a"}] * 33))
        for key, value in cases:
            q = deepcopy(self.q)
            q["questions"][0][key] = value
            with self.subTest(key=key, length=len(value) if hasattr(value, "__len__") else 0), self.assertRaises(ContractError):
                self.bundle(q=q)
        for labels in ([{"doc_key": "x" * 257, "exact_span": "x"}],
                       [{"doc_key": "x", "exact_span": "x" * 4001}],
                       [{"doc_key": "x", "exact_span": ""}]):
            q = deepcopy(self.q)
            q["questions"][0]["labels"] = labels
            with self.assertRaises(ContractError):
                self.bundle(q=q)
        for questions in ([], self.q["questions"] * 33):
            q = deepcopy(self.q)
            q["questions"] = questions
            with self.assertRaises(ContractError):
                self.bundle(q=q)

    def test_exact_schema_boundary_lengths_and_null_collection(self):
        question = deepcopy(self.q["questions"][0])
        question["text"] = "a" * 2000
        question["collection"] = None
        question["labels"] = [{"doc_key": "d" * 256,
                               "exact_span": str(index) + "x" * (4000 - len(str(index)))}
                              for index in range(32)]
        qbytes, rbytes = bundle_bytes(questions=[question], ranks=[None])
        self.assertEqual(len(load_bundle(qbytes, rbytes, fixture_harness=True).questions), 1)
        questions = [dict(question, id=f"fixture-{index}", labels=[question["labels"][0]]) for index in range(64)]
        qbytes, rbytes = bundle_bytes(questions=questions)
        self.assertEqual(len(load_bundle(qbytes, rbytes, fixture_harness=True).questions), 64)

    def test_settings_canonical_hash_uses_ascii_no_lf_and_key_sort(self):
        settings = {"z": "caf\u00e9", "a": [1, False, None]}
        raw = canonical_bytes(settings)
        self.assertEqual(raw, b'{"a":[1,false,null],"z":"caf\\u00e9"}')
        self.assertFalse(raw.endswith(b"\n"))
        reference = deepcopy(self.r)
        reference["settings"] = settings
        reference["settings_sha256"] = hashlib.sha256(raw).hexdigest()
        self.bundle(r=reference)
        reference["settings_sha256"] = hashlib.sha256(raw + b"\n").hexdigest()
        with self.assertRaises(ContractError):
            self.bundle(r=reference)
        self.reject_reference(("settings", "fold_identical"), True)

    def test_model_and_hash_schema_values(self):
        for path, value in ((("models", "embedding", "revision"), "f" * 39),
                            (("models", "reranker", "revision"), "A" * 40),
                            (("models", "embedding", "model"), ""),
                            (("corpus_fingerprint",), "0" * 63),
                            (("generation",), ""),
                            (("settings",), []),
                            (("schema_version",), 2)):
            with self.subTest(path=path):
                self.reject_reference(path, value)

    def test_reference_count_metric_and_reciprocal_consistency(self):
        for path, value in ((("numerators", "hit_at_1"), 3),
                            (("numerators", "hit_at_3"), 0),
                            (("numerators", "hit_at_8"), 1),
                            (("numerators", "hit_at_1"), True),
                            (("reference", "hit_at_1"), 0.5000000000000001),
                            (("reference", "mrr_at_10"), 0.74),
                            (("numerators", "reciprocal_rank_sum_at_10"), 1.75),
                            (("numerators", "reciprocal_rank_sum_at_10"), 1.5000000000000002)):
            with self.subTest(path=path):
                self.reject_reference(path, value)

    def test_rational_rank_lattice_is_valid_and_exact(self):
        for ranks in ([1, 3], [4, 7], [9, 10], [None, None],
                      [1, 3, 8, 10, None], list(range(1, 11)) * 6 + [None] * 4):
            q = [dict(self.q["questions"][0], id=f"fixture-{index}") for index in range(len(ranks))]
            qbytes, rbytes = bundle_bytes(questions=q, ranks=ranks)
            reference = load_bundle(qbytes, rbytes, fixture_harness=True).reference["reference"]
            self.assertEqual(reference.exact, aggregate(ranks).exact)

    def test_impossible_reciprocal_sum_refused_even_if_metric_recomputes(self):
        reference = deepcopy(self.r)
        reference["numerators"]["reciprocal_rank_sum_at_10"] = 1.75
        reference["reference"]["mrr_at_10"] = 0.875
        with self.assertRaises(ContractError):
            self.bundle(r=reference)

    def test_calibration_requires_three_identical_complete_runs_and_ordered_timings(self):
        for path, value in ((("measurement", "complete_runs"), 2),
                            (("measurement", "complete_runs"), True),
                            (("measurement", "identical_ranks"), False),
                            (("measurement", "search_p95_ms"), 0),
                            (("measurement", "search_p50_ms"), -1),
                            (("measurement", "gateway_version"), "")):
            with self.subTest(path=path):
                self.reject_reference(path, value)

    def test_duplicate_json_keys_at_every_level_refused(self):
        for raw in (self.qbytes.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'),
                    self.qbytes.replace(b'"collection":"fixture"', b'"collection":"fixture","collection":null', 1),
                    self.qbytes.replace(b'"doc_key":"fixture-beacon-manual"', b'"doc_key":"fixture-beacon-manual","doc_key":"other"', 1)):
            with self.assertRaises(ContractError):
                load_bundle(raw, self.rbytes, fixture_harness=True)
        reference = self.rbytes.replace(b'"complete_runs":3', b'"complete_runs":3,"complete_runs":3')
        with self.assertRaises(ContractError):
            load_bundle(self.qbytes, reference, fixture_harness=True)

    def test_nonfinite_overflow_and_invalid_json_refused(self):
        for token in (b"NaN", b"Infinity", b"-Infinity", b"1e9999"):
            reference = self.rbytes.replace(b'"search_p50_ms":1', b'"search_p50_ms":' + token)
            with self.subTest(token=token), self.assertRaises(ContractError):
                load_bundle(self.qbytes, reference, fixture_harness=True)
        for raw in (b"", b"null", b"[]", b"{}", b"\xff", b"\xef\xbb\xbf" + self.qbytes,
                    self.qbytes[:-1], self.qbytes + b"junk", b'{"bad":"\\ud800"}'):
            with self.subTest(raw=raw[:20]), self.assertRaises(ContractError):
                load_bundle(raw, self.rbytes, fixture_harness=True)

    def test_byte_depth_node_and_integer_parse_limits(self):
        for raw, reference in ((b" " * (QUESTIONS_MAX_BYTES + 1), self.rbytes),
                               (self.qbytes, b" " * (REFERENCE_MAX_BYTES + 1)),
                               ((b"[" * 1100) + (b"]" * 1100), self.rbytes),
                               (b'[' + b'0,' * 21000 + b'0]', self.rbytes),
                               (b'{"a":' + b'9' * 5000 + b'}', self.rbytes)):
            with self.assertRaises(ContractError):
                load_bundle(raw, reference, fixture_harness=True)
        with self.assertRaises(ContractError):
            load_bundle(bytearray(self.qbytes), self.rbytes, fixture_harness=True)

    def test_validation_and_scoring_have_no_io_or_services(self):
        def denied(*args, **kwargs):
            raise AssertionError("Unexpected side effect")
        with patch("builtins.open", denied), patch.object(socket, "socket", denied), \
                patch.object(subprocess, "run", denied), patch.object(subprocess, "Popen", denied):
            bundle = self.bundle()
            rank = first_rank(bundle.questions[0], [{"doc_key": "fixture-beacon-manual", "text": "The sample beacon is amber."}])
            self.assertEqual(rank, 1)
            self.assertTrue(all(passes(aggregate([rank, 2]), bundle.reference["reference"]).values()))


class ScoringTests(unittest.TestCase):
    def question(self, span="Amber beacon.\nCaf\u00e9 kite."):
        return {"labels": [{"doc_key": "fixture-labelled", "exact_span": span}]}

    def hit(self, text="Amber beacon.\nCaf\u00e9 kite.", doc_key="fixture-labelled", **extra):
        return {"doc_key": doc_key, "text": text, **extra}

    def test_exact_literal_unicode_newline_and_surrounding_text(self):
        self.assertEqual(first_rank(self.question(), [self.hit("prefix Amber beacon.\nCaf\u00e9 kite. suffix")]), 1)

    def test_wrong_doc_case_whitespace_punctuation_and_unicode_do_not_match(self):
        for hit in (self.hit(doc_key="fixture-other"), self.hit("amber beacon.\nCaf\u00e9 kite."),
                    self.hit("Amber beacon. Caf\u00e9 kite."), self.hit("Amber beacon!\nCaf\u00e9 kite."),
                    self.hit("Amber beacon.\r\nCaf\u00e9 kite."), self.hit("Amber beacon.\nCafe\u0301 kite."),
                    self.hit("Amber  beacon.\nCaf\u00e9 kite."), self.hit("Caf\u00e9 kite.")):
            with self.subTest(hit=hit):
                self.assertIsNone(first_rank(self.question(), [hit]))

    def test_alias_and_section_metadata_do_not_prove_delivered_body(self):
        self.assertIsNone(first_rank(self.question(), [self.hit(doc_key="fixture-other", also_in=["fixture-labelled"])]))
        self.assertIsNone(first_rank(self.question(), [self.hit("No body proof.", section="Amber beacon.\nCaf\u00e9 kite.")]))

    def test_multiple_labels_are_ored_for_actual_delivered_doc(self):
        question = self.question()
        question["labels"].append({"doc_key": "fixture-copy", "exact_span": "Actual copy."})
        self.assertEqual(first_rank(question, [self.hit("Actual copy.", "fixture-copy")]), 1)

    def test_ranks_1_3_8_10_11_and_delivered_order(self):
        for rank in (1, 3, 8, 10, 11):
            hits = [self.hit("irrelevant", "fixture-other", rank=1)] * (rank - 1) + [self.hit(rank=999)]
            with self.subTest(rank=rank):
                self.assertEqual(first_rank(self.question(), hits), rank if rank <= 10 else None)

    def test_short_empty_lists_and_later_relevant_hit(self):
        self.assertIsNone(first_rank(self.question(), []))
        self.assertIsNone(first_rank(self.question(), [self.hit("No proof.")]))
        self.assertEqual(first_rank(self.question(), [self.hit("No proof."), self.hit()]), 2)

    def test_aggregate_counts_and_rational_mrr(self):
        ranks = [1, 3, 8, 10, None]
        measured = aggregate(ranks)
        self.assertEqual(measured.exact["hit_at_1"], Fraction(1, 5))
        self.assertEqual(measured.exact["hit_at_3"], Fraction(2, 5))
        self.assertEqual(measured.exact["hit_at_8"], Fraction(3, 5))
        self.assertEqual(measured.exact["mrr_at_10"], Fraction(187, 600))
        self.assertEqual(aggregate_numerators(ranks), {
            "hit_at_1": 1, "hit_at_3": 2, "hit_at_8": 3,
            "reciprocal_rank_sum_at_10": float(Fraction(187, 120))})
        self.assertEqual(set(json.loads(json.dumps(measured))), set(METRICS))

    def test_misses_and_repeated_aggregation_deterministic(self):
        self.assertEqual(aggregate([None] * 64), dict.fromkeys(METRICS, 0.0))
        self.assertEqual(aggregate([1, 3, 8, 10, None]).exact,
                         aggregate([1, 3, 8, 10, None]).exact)

    def test_fixed_tolerance_boundary_compares_unrounded_fractions(self):
        measured = aggregate([1] * 14 + [None] * 36)
        reference = aggregate([1] * 15 + [None] * 35)
        self.assertTrue(all(passes(measured, reference).values()))
        self.assertFalse(any(passes(aggregate([1] * 13 + [None] * 37), reference).values()))
        exact = dict.fromkeys(METRICS, Fraction(1, 3))
        boundary = dict.fromkeys(METRICS, Fraction(1, 3) - Fraction(1, 50))
        self.assertTrue(all(passes(boundary, exact).values()))
        boundary["mrr_at_10"] -= Fraction(1, 10 ** 20)
        self.assertEqual(passes(boundary, exact), dict.fromkeys(METRICS, True) | {"mrr_at_10": False})

    def test_plain_decimal_mapping_boundary_and_each_metric_independent(self):
        measured = dict.fromkeys(METRICS, 0.28)
        reference = dict.fromkeys(METRICS, 0.3)
        self.assertTrue(all(passes(measured, reference).values()))
        measured["hit_at_3"] = 0.27999999999999997
        self.assertEqual(passes(measured, reference), dict.fromkeys(METRICS, True) | {"hit_at_3": False})

    def test_invalid_ranks_and_policy_are_errors(self):
        for ranks in ([], [None] * 65, [True], [0], [11], [float("inf")], ["1"]):
            with self.subTest(ranks=ranks[:2]), self.assertRaises(ContractError):
                aggregate(ranks)
        for tolerance in (0.03, 0, True, None):
            with self.assertRaises(ContractError):
                passes(aggregate([1]), aggregate([1]), tolerance=tolerance)
        for values in ({}, dict.fromkeys(METRICS, float("nan")), dict.fromkeys(METRICS, True)):
            with self.assertRaises(ContractError):
                passes(values, aggregate([1]))


if __name__ == "__main__":
    unittest.main()
