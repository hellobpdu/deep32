"""The late-interaction retriever is imported only when a root's "retrievers" names it (rag_core.orchestrator
colbert_retriever), and the repository's default retrieval settings name the retrievers and the technical variant's
routing a built root serves with, so a root served without its overlay runs the same legs."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import inspect
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_core import orchestrator
from rag_core.config import load_config
from rag_core.orchestrator import RetrievalOrchestrator

REPO = engine_target.REPO
HEAVY = ("rag_core.retrievers.colbert", "rag_core.late_interaction", "torch", "pylate")


class ImportTests(unittest.TestCase):

    def test_the_orchestrator_imports_without_torch_or_pylate(self):
        # no stand-ins on the path: only the engine's own src (a real torch or pylate in the environment may be
        # importable, but nothing may have imported it)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import rag_core.orchestrator; "
                "print(' '.join(m for m in sys.argv[2:] if m in sys.modules))")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        done = subprocess.run([sys.executable, "-B", "-c", code, str(REPO / "src"), *HEAVY],
                              capture_output=True, text=True, timeout=300, env=env)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, ""), done.stderr[-2000:])

    def test_the_factory_takes_no_options_and_is_not_the_class(self):
        factory = orchestrator.RETRIEVER_FACTORIES["colbert"]
        self.assertIs(factory, orchestrator.colbert_retriever)
        self.assertEqual(list(inspect.signature(factory).parameters), ["paths"])
        self.assertIsNone(getattr(factory, "check_options", None))
        self.assertFalse(hasattr(orchestrator, "ColBERTRetriever"))


class Ok:
    def __init__(self, paths):
        self.paths = paths


class StartTests(unittest.TestCase):

    def orch(self, config, factories):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "retrieval.json"
            cfg.write_text(json.dumps(config))
            paths = mock.Mock(logs=Path(d) / "logs")
            with mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"}), \
                    mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, factories):
                return RetrievalOrchestrator(paths=paths)

    def test_a_root_that_names_bm25_and_dense_never_calls_the_factory(self):
        called = []
        with mock.patch.dict(orchestrator.RETRIEVER_FACTORIES,
                             {"colbert": lambda paths: called.append(paths)}):
            o = self.orch({"rrf_k": 60, "retrievers": ["bm25", "dense"]}, {"bm25": Ok, "dense": Ok})
        self.assertEqual((sorted(o.retrievers), called, o.capabilities()["degraded"]), (["bm25", "dense"], [], {}))

    def test_a_host_without_the_module_degrades_colbert_alone(self):
        # the real factory, its import failing as on a host with neither pylate nor a stand-in
        with mock.patch.dict(sys.modules, {"rag_core.retrievers.colbert": None}):
            o = self.orch({"rrf_k": 60, "retrievers": ["bm25", "dense", "colbert"]}, {"bm25": Ok, "dense": Ok})
        self.assertEqual(sorted(o.retrievers), ["bm25", "dense"])
        self.assertIn("colbert", o.capabilities()["degraded"])


class RepoDefaultTests(unittest.TestCase):

    def test_the_repository_file_names_the_served_legs(self):
        repo = json.loads((REPO / "config" / "retrieval.json").read_text(encoding="utf-8"))
        self.assertEqual(repo["retrievers"], ["bm25", "dense"])
        self.assertEqual(repo["technical_variant_retrievers"], ["bm25"])

    def test_a_root_overlay_as_build_root_writes_it_merges_to_the_same_settings_as_before(self):
        # scripts/build_root.py step_config writes both keys into every root's overlay, and an overlay replaces
        # the repository's top-level keys (rag_core.config): the merged settings of such a root do not move
        overlay = {"retrievers": ["bm25", "dense"], "retriever_weights": {"bm25": 1.0, "dense": 1.0},
                   "technical_variant_retrievers": ["bm25"], "dense_breaker": {"failures": 3, "open_seconds": 30},
                   "reranker": {"enabled": False, "url": None, "candidates": 10, "timeout_seconds": 15,
                                "breaker": {"failures": 3, "open_seconds": 30}}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config").mkdir()
            (root / "config" / "retrieval.json").write_text(json.dumps(overlay), encoding="utf-8")
            before = Path(d) / "before"
            before.mkdir()
            repo = json.loads((REPO / "config" / "retrieval.json").read_text(encoding="utf-8"))
            old = {k: v for k, v in repo.items() if k not in ("retrievers", "technical_variant_retrievers")}
            (before / "retrieval.json").write_text(json.dumps(old), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k not in ("RAG_ROOT", "RAG_RETRIEVAL_CONFIG")}
            with mock.patch.dict(os.environ, env, clear=True):
                now = load_config("retrieval", root=root)
                was = load_config("retrieval", root=root, repo_dir=before)
        self.assertEqual(now, was)
        self.assertEqual(list(now), list(was))


if __name__ == "__main__":
    unittest.main()
