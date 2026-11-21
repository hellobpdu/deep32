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

from rag_core import api, orchestrator
from rag_core.config import load_config
from rag_core.dense import load_dense_config
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.paths import RAGPaths
from rag_core.retrieval_types import SearchHit

# env that changes which config is loaded; a developer shell may set any of these
CONFIG_ENV = ("RAG_ROOT", "RAG_RETRIEVAL_CONFIG", "RAG_SERVING_CONFIG", "RAG_LATE_INTERACTION_CONFIG",
              "RAG_DENSE_COLLECTION", "RAG_EMBED_QUERY_TIMEOUT", "RAG_MAX_CONCURRENT_SEARCHES",
              "RAG_SEARCH_QUEUE_TIMEOUT")


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in CONFIG_ENV}
    return mock.patch.dict(os.environ, {**env, **extra}, clear=True)


class OverlayTests(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)
        self.repo = self.tmp / "repo"
        self.root = self.tmp / "root"
        self.repo.mkdir()
        (self.root / "config").mkdir(parents=True)
        self.write(self.repo / "retrieval.json", {"rrf_k": 60, "fused_limit": 30, "retriever_weights": {"bm25": 1.0}})

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def load(self, **kw):
        return load_config("retrieval", repo_dir=self.repo, **kw)

    def test_repo_file_alone_without_root_or_overlay(self):
        with clean_env():
            self.assertEqual(self.load()["fused_limit"], 30)
            self.assertEqual(self.load(root=self.root)["fused_limit"], 30)      # root without an overlay file

    def test_root_overlay_replaces_top_level_keys(self):
        self.write(self.root / "config" / "retrieval.json",
                   {"fused_limit": 8, "retriever_weights": {"dense": 2.0}, "retrievers": ["bm25", "dense"]})
        with clean_env():
            config = self.load(root=self.root)
        self.assertEqual(config["rrf_k"], 60)                                  # kept from the repo
        self.assertEqual(config["fused_limit"], 8)
        self.assertEqual(config["retriever_weights"], {"dense": 2.0})         # replaced whole, not merged
        self.assertEqual(config["retrievers"], ["bm25", "dense"])

    def test_root_defaults_to_rag_root(self):
        self.write(self.root / "config" / "retrieval.json", {"fused_limit": 8})
        with clean_env(RAG_ROOT=str(self.root)):
            self.assertEqual(self.load()["fused_limit"], 8)

    def test_env_named_file_replaces_repo_and_overlay(self):
        self.write(self.root / "config" / "retrieval.json", {"fused_limit": 8})
        arm = self.write(self.tmp / "arm.json", {"fused_limit": 5})
        with clean_env(RAG_ROOT=str(self.root), RAG_RETRIEVAL_CONFIG=str(arm)):
            self.assertEqual(self.load(env_var="RAG_RETRIEVAL_CONFIG"), {"fused_limit": 5})

    def test_overlay_must_be_an_object(self):
        self.write(self.root / "config" / "retrieval.json", ["bm25"])
        with clean_env(), self.assertRaises(ValueError):
            self.load(root=self.root)
        with self.assertRaises(ValueError):
            load_config("chunker", root=self.root)                            # no overlay for other configs


class DenseOverlayTests(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        (self.root / "config").mkdir()

    def overlay(self, data):
        (self.root / "config" / "dense.json").write_text(json.dumps(data), encoding="utf-8")

    def test_root_collection(self):
        with clean_env():
            repo = load_dense_config(root=self.root)
        self.assertEqual(repo.collection, "rag_dense_v1")
        self.overlay({"collection": "rag_dense_gen7"})
        with clean_env():
            config = load_dense_config(root=self.root)
        self.assertEqual(config.collection, "rag_dense_gen7")
        self.assertEqual(config.vector_size, repo.vector_size)                # rest from the repo file

    def test_env_var_overrides_the_overlay_with_a_warning(self):
        self.overlay({"collection": "rag_dense_gen7"})
        with clean_env(RAG_DENSE_COLLECTION="rag_dense_other"), self.assertLogs("rag_core.dense", "WARNING"):
            self.assertEqual(load_dense_config(root=self.root).collection, "rag_dense_other")

    def test_overlay_without_its_own_collection_is_rejected(self):
        # the root would silently share the repo's collection with another corpus
        self.overlay({"batch_size": 8})
        with clean_env(), self.assertRaises(ValueError):
            load_dense_config(root=self.root)

    def test_explicit_path_ignores_the_overlay(self):
        self.overlay({"collection": "rag_dense_gen7"})
        repo_file = Path(orchestrator.__file__).resolve().parents[2] / "config" / "dense.json"
        with clean_env(RAG_ROOT=str(self.root)):
            self.assertEqual(load_dense_config(repo_file).collection, "rag_dense_v1")


class ServingOverlayTests(unittest.TestCase):

    def test_root_admission_then_env(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config").mkdir()
            (Path(d) / "config" / "serving.json").write_text(
                json.dumps({"generation": "gen7", "max_concurrent_searches": 2}), encoding="utf-8")
            with clean_env(RAG_ROOT=d):
                self.assertEqual(api.serving_config()["generation"], "gen7")
                self.assertEqual(api.admission_settings(), (2, 30.0))       # timeout from the repo file
            with clean_env(RAG_ROOT=d, RAG_MAX_CONCURRENT_SEARCHES="3"):
                self.assertEqual(api.admission_settings(), (3, 30.0))
            with clean_env():
                self.assertIsNone(api.serving_config()["generation"])
                self.assertEqual(api.admission_settings(), (1, 30.0))


class Stub:
    name = ""
    calls: list = []

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50):
        type(self).calls.append(variant.purpose)
        return [SearchHit(retriever=self.name, variant_id=variant.variant_id, purpose=variant.purpose,
                          chunk_id=f"{self.name}-1", doc_id="d", rank=1, score=1.0, text="t")]


class BM25(Stub):
    name = "bm25"


class Dense(Stub):
    name = "dense"


class Colbert(Stub):
    name = "colbert"


class OrchestratorOverlayTests(unittest.TestCase):

    def test_root_overlay_selects_retrievers_and_routes_the_technical_variant(self):
        BM25.calls, Dense.calls, Colbert.calls = [], [], []
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config").mkdir()
            (root / "config" / "retrieval.json").write_text(json.dumps({
                "retrievers": ["bm25", "dense"], "technical_variant_retrievers": ["bm25"]}), encoding="utf-8")
            with clean_env(RAG_TRACE="off"), \
                    mock.patch.dict(orchestrator.RETRIEVER_FACTORIES,
                                    {"bm25": BM25, "dense": Dense, "colbert": Colbert}, clear=True):
                orch = RetrievalOrchestrator(RAGPaths(root))
                result = orch.search("NSSA type-7 LSA translation in OSPF")
        self.assertEqual(sorted(orch.retrievers), ["bm25", "dense"])
        self.assertEqual(sorted(BM25.calls), ["primary", "technical"])
        self.assertEqual(Dense.calls, ["primary"])
        self.assertEqual(Colbert.calls, [])
        self.assertEqual(result["active_retrievers"], ["bm25", "dense"])
        self.assertEqual(orch.config["rrf_k"], 60)                               # repo keys kept


class LateInteractionOverlayTests(unittest.TestCase):

    def test_root_overlay_then_env_file(self):
        from rag_core.late_interaction import CONFIG_PATH, load_late_interaction_config
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config").mkdir()
            (Path(d) / "config" / "late_interaction.json").write_text(
                json.dumps({"search_batch_size": 2048}), encoding="utf-8")
            with clean_env():
                self.assertEqual(load_late_interaction_config(root=d).search_batch_size, 2048)
                self.assertEqual(load_late_interaction_config().search_batch_size, 1024)
            with clean_env(RAG_LATE_INTERACTION_CONFIG=str(CONFIG_PATH)):
                self.assertEqual(load_late_interaction_config(root=d).search_batch_size, 1024)


if __name__ == "__main__":
    unittest.main()
