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


class Ok:
    def __init__(self, paths):
        self.paths = paths


class Broken:
    def __init__(self, paths):
        raise FileNotFoundError("pylate/current missing")


class RetrieverSelectionTests(unittest.TestCase):

    def orch(self, config, factories):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "retrieval.json"
            cfg.write_text(json.dumps(config))
            paths = mock.Mock(logs=Path(d) / "logs")
            with mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "off"}), \
                    mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, factories, clear=True):
                return RetrievalOrchestrator(paths=paths)

    def test_colbert_failure_degrades_instead_of_crashing(self):
        o = self.orch({"rrf_k": 60}, {"bm25": Ok, "dense": Ok, "colbert": Broken})
        self.assertEqual(sorted(o.retrievers), ["bm25", "dense"])
        caps = o.capabilities()
        self.assertFalse(caps["colbert"])
        self.assertIn("pylate/current missing", caps["degraded"]["colbert"])

    def test_bm25_failure_is_fatal(self):
        with self.assertRaises(FileNotFoundError):
            self.orch({"rrf_k": 60}, {"bm25": Broken, "dense": Ok, "colbert": Ok})

    def test_config_selects_retrievers(self):
        o = self.orch({"rrf_k": 60, "retrievers": ["bm25", "dense"]},
                      {"bm25": Ok, "dense": Ok, "colbert": Broken})
        self.assertEqual(sorted(o.retrievers), ["bm25", "dense"])
        self.assertEqual(o.capabilities()["degraded"], {})

    def test_unknown_retriever_rejected(self):
        with self.assertRaises(ValueError):
            self.orch({"rrf_k": 60, "retrievers": ["bm25", "splade"]},
                      {"bm25": Ok, "dense": Ok, "colbert": Ok})


if __name__ == "__main__":
    unittest.main()
