"""scripts/derive_root.py --set for the trial arms (T2 search_many_parallel, T3 best_query, the T4
rerank length and depth arms with search_many_candidates, a combined W): each derives on a root whose reranker is on
(as p8r's overlay has it), serves the values it sets, and passes deployctl's job argument rule; a value deep32-engine
would not start with is refused before anything is written. The derivation fixture of test_derive_root."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

import test_derive_root
from test_derive_root import SERVE_URL
from test_derive_trials import SAFE_ARG

R = "retrieval.reranker."
ARMS = {
    "T2": ["retrieval.search_many_parallel=true"],
    "T23": ["retrieval.search_many_parallel=true", "retrieval.search_many_rerank=best_query"],
    "B": [f"{R}max_chars=1200", f"{R}window=centred"],
    "BHEAD": [f"{R}max_chars=1200"],
    "D": [f"{R}candidates=15", f"{R}max_chars=1600", f"{R}window=centred", f"{R}search_many_candidates=10"],
    "H": [f"{R}candidates=15", f"{R}max_chars=1200", f"{R}window=centred", f"{R}search_many_candidates=10"],
    "C": [f"{R}candidates=20", f"{R}max_chars=1200", f"{R}window=centred", f"{R}search_many_candidates=10"],
    "E": [f"{R}candidates=30", f"{R}max_chars=800", f"{R}window=centred", f"{R}search_many_candidates=10"],
    "W": ["retrieval.grep.regex_engine=true", "retrieval.search_many_parallel=true",
          "retrieval.search_many_rerank=best_query", f"{R}max_chars=1200", f"{R}window=centred"],
}
# (candidates, search_many_candidates, max_chars, window) each arm serves; today's 10 x 2400 head where it sets none
SERVED = {"T2": (10, 10, 2400, "head"), "T23": (10, 10, 2400, "head"), "B": (10, 10, 1200, "centred"),
          "BHEAD": (10, 10, 1200, "head"), "D": (15, 10, 1600, "centred"), "H": (15, 10, 1200, "centred"),
          "C": (20, 10, 1200, "centred"), "E": (30, 10, 800, "centred"), "W": (10, 10, 1200, "centred")}


class ArmTests(unittest.TestCase):
    # test_derive_root's fixture (only through its module: a TestCase named here would run here too)
    setUpClass = classmethod(test_derive_root.DeriveRootTests.setUpClass.__func__)
    tearDownClass = classmethod(test_derive_root.DeriveRootTests.tearDownClass.__func__)
    setUp = test_derive_root.DeriveRootTests.setUp
    run_derive = test_derive_root.DeriveRootTests.run_derive
    report = test_derive_root.DeriveRootTests.report
    config = test_derive_root.DeriveRootTests.config
    collections = test_derive_root.DeriveRootTests.collections
    refusal = test_derive_root.DeriveRootTests.refusal

    def test_every_arm_is_settable_served_and_passes_the_job_argument_rule(self):
        from rag_core.api_v2 import search_many_settings
        from rag_core.config import load_config
        from rag_core.rerank import Reranker

        for name, sets in ARMS.items():
            args = [a for s in sets for a in ("--set", s)]
            with self.subTest(arm=name):
                # trials-p9 runs an arm as derive_root --from X --to Y --resume plus its sets: 24 arguments at most
                self.assertLessEqual(len(["--from", "x", "--to", "y", "--resume", *args]), 24)
                self.assertTrue(all(SAFE_ARG.match(a) for a in args), args)
                to = f"gen-{name.lower()}"
                self.assertEqual(self.run_derive("--to", to, "--rerank-url", SERVE_URL, *args), 0)
                served = load_config("retrieval", root=self.data / to)
                reranker = Reranker(served["reranker"])
                self.addCleanup(reranker.close)
                self.assertEqual((reranker.candidates, reranker.search_many_candidates, reranker.max_chars,
                                  reranker.window), SERVED[name])
                many = search_many_settings(served)
                self.assertEqual((many.workers, many.rerank),
                                 (4 if name in ("T2", "T23", "W") else 0,
                                  "best_query" if name in ("T23", "W") else "per_query"))
                self.assertEqual(len(self.report(to)["config"]["overrides"]), len(sets) + 1)    # + --rerank-url
        self.assertEqual(self.config("gen-d", "retrieval")["reranker"]["search_many_candidates"], 10)

    def test_values_rag_core_would_not_start_with_are_refused_before_anything_is_written(self):
        cases = {
            "search_many_candidates must be null or an integer of 1 or more": [f"{R}search_many_candidates=0"],
            "search_many_candidates must be null or an integer": [f"{R}search_many_candidates=ten"],
            "reranker window must be one of": [f"{R}window=middle"],
            "search_many_rerank must be one of": ["retrieval.search_many_rerank=best"],
            "search_many_parallel must be false, true or a number": ["retrieval.search_many_parallel=0"],
        }
        before = self.collections()
        for why, sets in cases.items():
            self.derive.log.reset_mock()
            with self.subTest(why):
                args = [a for s in sets for a in ("--set", s)]
                self.assertEqual(self.run_derive("--to", "gen-bad", "--rerank-url", SERVE_URL, *args), 2)
                self.assertIn(why, self.refusal())
                self.assertFalse((self.data / "gen-bad").exists())
        self.assertEqual(self.collections(), before)


if __name__ == "__main__":
    unittest.main()
