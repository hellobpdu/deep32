import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_core import orchestrator
from rag_core.orchestrator import RetrievalOrchestrator


class NoOptions:
    def __init__(self, paths):
        self.paths = paths


class WithOptions:
    def __init__(self, paths, options=None):
        self.options = options


class RetrieverOptionsTests(unittest.TestCase):
    """A dict under a retriever's name used to raise a swallowed TypeError and drop that retriever."""

    def orch(self, config, factories=None):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "retrieval.json"
            cfg.write_text(json.dumps({"rrf_k": 60, **config}))
            paths = mock.Mock(logs=Path(d) / "logs")
            with mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"}), \
                    mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, factories or {}):
                return RetrievalOrchestrator(paths=paths)

    def test_options_for_the_real_dense_and_colbert_retrievers_fail_loudly(self):
        # the real classes are only inspected, never constructed: the check runs before startup
        for name in ("dense", "colbert"):
            with self.subTest(retriever=name):
                with self.assertRaises(ValueError) as caught:
                    self.orch({"retrievers": ["bm25", name], name: {"timeout": 5}})
                self.assertIn(name, str(caught.exception))

    def test_nothing_is_constructed_when_options_are_invalid(self):
        built = mock.Mock(side_effect=NoOptions)
        with self.assertRaises(ValueError):
            self.orch({"retrievers": ["bm25", "dense"], "dense": {"timeout": 5}},
                      {"bm25": built, "dense": NoOptions})
        built.assert_not_called()

    def test_non_object_options_are_rejected(self):
        with self.assertRaises(ValueError):
            self.orch({"retrievers": ["bm25"], "bm25": True})

    def test_real_bm25_takes_its_options(self):
        o = self.orch({"retrievers": ["bm25"], "bm25": {"drop_stopwords": True}})
        self.assertTrue(o.retrievers["bm25"].drop_stopwords)
        self.assertEqual(o.capabilities()["degraded"], {})

    def test_options_reach_a_retriever_that_takes_them(self):
        o = self.orch({"retrievers": ["bm25", "dense"], "dense": {"k": 1}},
                      {"bm25": NoOptions, "dense": WithOptions})
        self.assertEqual(o.retrievers["dense"].options, {"k": 1})
        self.assertEqual(sorted(o.retrievers), ["bm25", "dense"])


if __name__ == "__main__":
    unittest.main()
