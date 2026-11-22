"""scripts/validate_root.py: the degraded gates do not count a backend the validated root is configured without
(configured_off, degraded_answers). 0.1.0-alpha.3 names such a backend in every search answer's "degraded", and a
keyword-only build (--no-dense) or a reranker-off arm is validated as what it is. The gates are plain functions over
the evaluator's report; no evaluator and no server is needed here."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

if engine_target.HAS_SCRIPTS:
    sys.path.insert(0, str(engine_target.REPO / "scripts"))
    import validate_root as vr

FULL = {"backends": {"dense": {"enabled": True, "ok": True}, "reranker": {"enabled": True, "ok": True}}}
BM25_ONLY = {"backends": {"dense": {"enabled": False, "ok": None}, "reranker": {"enabled": False, "ok": None}}}
NO_RERANKER = {"backends": {"dense": {"enabled": True, "ok": True}, "reranker": {"enabled": False, "ok": None}}}


def report(*degraded, error=False):
    rows = [{"qid": f"q{n}", "degraded": list(d)} for n, d in enumerate(degraded)]
    if error:
        rows.append({"qid": "qe", "degraded": [], "error": "HTTP 500"})
    answered = [r for r in rows if "error" not in r]
    return {"overall": {"n_errors": len(rows) - len(answered),
                        "n_degraded": sum(1 for r in answered if r["degraded"])},
            "errors": [{"error": "HTTP 500"}] if error else [], "results": rows,
            "relevance_basis": "chunk", "store_coverage": {}}


@engine_target.SCRIPTS_SKIP
class DegradedGateTests(unittest.TestCase):

    def gate(self, rep, status):
        gates = vr.Gates()
        vr.eval_gates(gates, rep, {}, vr.configured_off(status))
        return next((g["ok"], g["detail"]) for g in gates.items if g["name"] == "eval.degraded")

    def test_configured_off_reads_the_status(self):
        self.assertEqual(vr.configured_off(FULL), frozenset())
        self.assertEqual(vr.configured_off(BM25_ONLY), frozenset({"dense", "reranker"}))
        self.assertEqual(vr.configured_off(NO_RERANKER), frozenset({"reranker"}))
        self.assertEqual(vr.configured_off({}), frozenset())

    def test_a_full_root_is_gated_as_before(self):
        self.assertEqual(self.gate(report([], []), FULL), (True, "0 answers with degraded set"))
        self.assertEqual(self.gate(report([], ["dense"]), FULL), (False, "1 answers with degraded set"))
        self.assertEqual(self.gate(report(["reranker"], ["dense"]), FULL), (False, "2 answers with degraded set"))

    def test_a_keyword_only_root_passes_and_still_fails_on_anything_else(self):
        every = report(["dense", "reranker"], ["dense", "reranker"], ["reranker"])
        ok, detail = self.gate(every, BM25_ONLY)
        self.assertTrue(ok)
        self.assertIn("0 answers with degraded set beyond ['dense', 'reranker']", detail)
        self.assertIn("(3 name those)", detail)
        self.assertEqual(self.gate(report(["dense", "reranker"], ["dense", "kb", "reranker"]), BM25_ONLY)[0], False)
        self.assertEqual(self.gate(report(["dense", "reranker"], ["structure"]), BM25_ONLY)[0], False)

    def test_a_reranker_off_arm_still_fails_on_dense(self):
        self.assertEqual(self.gate(report(["reranker"], ["reranker"]), NO_RERANKER)[0], True)
        self.assertEqual(self.gate(report(["reranker"], ["dense", "reranker"]), NO_RERANKER)[0], False)

    def test_a_failed_query_is_not_a_degraded_answer(self):
        self.assertEqual(self.gate(report(["dense", "reranker"], error=True), BM25_ONLY)[0], True)

    def test_rows_that_do_not_name_what_was_degraded_keep_the_old_gate(self):
        silent = report(["dense", "reranker"], ["dense", "reranker"])
        for row in silent["results"]:
            del row["degraded"]             # an evaluator whose rows carry no "degraded"
        ok, detail = self.gate(silent, BM25_ONLY)
        self.assertFalse(ok)
        self.assertIn("2 answers with degraded set, 0 of them named in the rows", detail)

    def test_the_default_argument_is_the_old_gate(self):
        gates = vr.Gates()
        vr.eval_gates(gates, report(["dense"]), {})
        self.assertEqual([(g["name"], g["ok"]) for g in gates.items][:2], [("eval.errors", True),
                                                                           ("eval.degraded", False)])


if __name__ == "__main__":
    unittest.main()
