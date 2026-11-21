"""scripts/derive_root.py --set for the trial arms (T1 grep on the regex engine, T5 the rerank
gate): the keys are settable, a value deep32-engine would not start with is refused before anything is written, and
every arm's --set argument passes deployctl's job argument rule. The derivation fixture of test_derive_root."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import re
import subprocess
import sys
import unittest
from pathlib import Path

import test_derive_root

REPO = engine_target.REPO

# deployctl's rule for a `run` job's arguments (remote/deployctl.py SAFE_ARG; at most 24 of them)
SAFE_ARG = re.compile(r"^[A-Za-z0-9_./:=,@%+-]{1,300}$")
ARMS = {
    "T1": ["retrieval.grep.regex_engine=true"],
    "T5": ["serving.max_concurrent_searches=4", "serving.max_queued_searches=12", "serving.pipeline.rerank_gate=1"],
    "P0": ["serving.max_concurrent_searches=4", "serving.max_queued_searches=12"],
    "T5-skip": ["serving.max_concurrent_searches=4", "serving.max_queued_searches=12",
                "serving.pipeline.rerank_gate=1", "serving.pipeline.rerank_wait_seconds=30",
                "serving.pipeline.rerank_on_timeout=skip"],
}


class TrialKeysTests(unittest.TestCase):
    # test_derive_root's fixture (only through its module: a TestCase named here would run here too)
    setUpClass = classmethod(test_derive_root.DeriveRootTests.setUpClass.__func__)
    tearDownClass = classmethod(test_derive_root.DeriveRootTests.tearDownClass.__func__)
    setUp = test_derive_root.DeriveRootTests.setUp
    run_derive = test_derive_root.DeriveRootTests.run_derive
    report = test_derive_root.DeriveRootTests.report
    config = test_derive_root.DeriveRootTests.config
    collections = test_derive_root.DeriveRootTests.collections
    refusal = test_derive_root.DeriveRootTests.refusal

    def test_every_arm_is_settable_and_passes_the_job_argument_rule(self):
        from rag_core.api import admission_settings, queue_limit
        from rag_core.api_v2 import grep_settings
        from rag_core.config import load_config
        from rag_core.pipeline import pipeline_settings

        for name, sets in ARMS.items():
            args = [a for s in sets for a in ("--set", s)]
            with self.subTest(arm=name):
                self.assertLessEqual(len(["--from", "x", "--to", "y", *args]), 24)
                self.assertTrue(all(SAFE_ARG.match(a) for a in args), args)
                to = f"gen-{name.lower()}"
                self.assertEqual(self.run_derive("--to", to, *args), 0)
                served = {which: load_config(which, root=self.data / to) for which in ("retrieval", "serving")}
                slots, _ = admission_settings(served["serving"])
                gate = pipeline_settings(served["serving"], slots=slots, max_queued=queue_limit(served["serving"]))
                self.assertEqual(grep_settings(served["retrieval"]).regex_engine, name == "T1")
                self.assertEqual(gate.rerank_gate, int(name.startswith("T5")))
                if name.startswith("T5") or name == "P0":
                    self.assertEqual((slots, queue_limit(served["serving"])), (4, 12))
                if name == "T5-skip":
                    self.assertEqual((gate.rerank_wait_seconds, gate.rerank_on_timeout), (30.0, "skip"))
        self.assertEqual(self.config("gen-t1", "retrieval")["grep"], {"regex_engine": True})
        self.assertEqual(self.config("gen-t5", "serving")["pipeline"], {"rerank_gate": 1})
        self.assertIn("serving.pipeline.rerank_gate=1", self.report("gen-t5")["config"]["overrides"])

    def test_values_rag_core_would_not_start_with_are_refused_before_anything_is_written(self):
        cases = {
            "grep.regex_engine must be true or false": ["--set", "retrieval.grep.regex_engine=yes"],
            "grep must be an object": ["--set", "retrieval.grep.regex_engin=true"],
            "rerank_gate must be 0 or 1": ["--set", "serving.pipeline.rerank_gate=2"],
            "rerank_on_timeout": ["--set", "serving.pipeline.rerank_gate=1",
                                  "--set", "serving.pipeline.rerank_on_timeout=drop"],
            "rerank_wait_seconds": ["--set", "serving.pipeline.rerank_gate=1",
                                    "--set", "serving.pipeline.rerank_wait_seconds=0"],
            "worker threads": ["--set", "serving.max_concurrent_searches=12", "--set", "serving.pipeline.rerank_gate=1",
                               "--set", "serving.pipeline.rerank_max_waiting=11"],
            "rerank_max_waiting 2 is less": ["--set", "serving.max_concurrent_searches=4",
                                             "--set", "serving.pipeline.rerank_gate=1",
                                             "--set", "serving.pipeline.rerank_max_waiting=2"],
            "max_concurrent_searches must be >= 1": ["--set", "serving.max_concurrent_searches=0",
                                                     "--set", "serving.pipeline.rerank_gate=1"],
        }
        before = self.collections()
        for why, extra in cases.items():
            self.derive.log.reset_mock()
            with self.subTest(why):
                self.assertEqual(self.run_derive("--to", "gen-bad", *extra), 2)
                self.assertIn(why, self.refusal())
                self.assertFalse((self.data / "gen-bad").exists())
        self.assertEqual(self.collections(), before)

    def test_without_a_pipeline_the_admission_keys_are_written_as_before(self):
        # derive_root checks admission only for a root with a pipeline: an arm without one derives as it did
        self.assertEqual(self.run_derive("--to", "gen-n30", "--set", "serving.max_concurrent_searches=30"), 0)
        self.assertEqual(self.config("gen-n30", "serving")["max_concurrent_searches"], 30)


class ImportTests(unittest.TestCase):

    def test_derive_root_imports_no_serving_stack(self):
        # rag_core.api imports the orchestrator and colbert (torch, pylate, transformers): ~9 s of every derive's
        # clock (trials-p9's resume limit) and ~430 MiB. It is imported only to check a root's serving.pipeline
        heavy = ("rag_core.api", "rag_core.orchestrator", "rag_core.retrievers.colbert", "torch", "pylate",
                 "transformers")
        code = ("import sys; sys.path[:0] = sys.argv[1:3]; import derive_root; "
                "print(' '.join(m for m in sys.argv[3:] if m in sys.modules))")
        done = subprocess.run([sys.executable, "-B", "-c", code, str(REPO / "scripts"), str(REPO / "src"), *heavy],
                              capture_output=True, text=True, timeout=300)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, ""), done.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
