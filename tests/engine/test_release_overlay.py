"""0.1.0-alpha.3, WP-D item D8: RAG_CONFIG_OVERLAY_DIR (rag_core.config precedence 2b), the settings a release
ships beside the code: merged recursively over the repository defaults and the root's settings, replaced like them
by a file an env var names. The serving engine only: the Forge builder's copy writes and validates a generation's
own settings and does not read the variable. Then the overlay 0.1.0-alpha.3 ships on the Workstation."""
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

from rag_core import config as config_mod
from rag_core.config import load_config

SERVING_ONLY = unittest.skipUnless(engine_target.NAME == "engine",
                                   "RAG_CONFIG_OVERLAY_DIR is read by src/engine only")
CONFIG_ENV = ("RAG_ROOT", "RAG_RETRIEVAL_CONFIG", "RAG_SERVING_CONFIG", "RAG_LATE_INTERACTION_CONFIG",
              "RAG_CHUNKING_CONFIG", "RAG_CONFIG_OVERLAY_DIR", "RAG_DENSE_COLLECTION", "RAG_MAX_CONCURRENT_SEARCHES",
              "RAG_SEARCH_QUEUE_TIMEOUT")
SHIPPED = engine_target.ROOT / "src" / "workstation" / "ship" / "site" / "usr" / "local" / "d32" / "etc" / \
    "engine-overlay"
# the retrieval settings of the knowledge base 0.1.0-alpha.2 shipped (d32kb-20261115cr root/config/retrieval.json)
BUNDLE = {
    "retrievers": ["bm25", "dense"], "retriever_weights": {"bm25": 1.0, "dense": 1.0},
    "technical_variant_retrievers": ["bm25"], "dense_breaker": {"failures": 3, "open_seconds": 30},
    "reranker": {"enabled": True, "url": "http://127.0.0.1:8082/v1/rerank", "candidates": 15, "timeout_seconds": 15,
                 "breaker": {"failures": 3, "open_seconds": 30}, "max_chars": 1600, "window": "centred",
                 "search_many_candidates": 10},
    "opt_in_doc_types": ["powershell", "system"], "grep": {"regex_engine": True}, "fold_identical": True,
}


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in CONFIG_ENV}
    return mock.patch.dict(os.environ, {**env, **extra}, clear=True)


class Layers(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)
        self.repo, self.root, self.overlay = self.tmp / "repo", self.tmp / "root", self.tmp / "overlay"
        self.repo.mkdir()
        (self.root / "config").mkdir(parents=True)
        self.overlay.mkdir()
        self.write(self.repo / "retrieval.json", {"rrf_k": 60, "fused_limit": 30, "retriever_weights": {"bm25": 1.0}})
        self.write(self.repo / "serving.json", {"generation": None, "max_concurrent_searches": 1})
        self.write(self.repo / "chunking.json", {"table_header_repeat": False})

    def write(self, path, data):
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def load(self, name="retrieval", **kw):
        return load_config(name, repo_dir=self.repo, root=self.root, **kw)


@SERVING_ONLY
class OverlayDirTests(Layers):

    def test_the_order_is_repository_then_root_then_overlay_then_the_named_file(self):
        self.write(self.root / "config" / "retrieval.json", {"fused_limit": 8, "rrf_k": 61, "a": "root"})
        self.write(self.overlay / "retrieval.json", {"rrf_k": 62, "b": "overlay"})
        arm = self.write(self.tmp / "arm.json", {"fused_limit": 5})
        with clean_env(RAG_CONFIG_OVERLAY_DIR=str(self.overlay)):
            self.assertEqual(self.load(), {"rrf_k": 62, "fused_limit": 8, "retriever_weights": {"bm25": 1.0},
                                           "a": "root", "b": "overlay"})
        # the bench case: a file named by the env var replaces everything, the overlay included
        with clean_env(RAG_CONFIG_OVERLAY_DIR=str(self.overlay), RAG_RETRIEVAL_CONFIG=str(arm)):
            self.assertEqual(self.load(env_var="RAG_RETRIEVAL_CONFIG"), {"fused_limit": 5})
            # a loader that names no such variable still gets the overlay (dense has none)
            self.assertEqual(self.load()["rrf_k"], 62)

    def test_objects_merge_key_by_key_and_other_values_replace(self):
        self.write(self.root / "config" / "retrieval.json", BUNDLE)
        self.write(self.overlay / "retrieval.json", {
            "reranker": {"title": True, "breaker": {"failures": 5}},
            "evidence": {"tier1_chars": 3200},
            "opt_in_doc_types": ["system"],
            "fold_identical": None,
        })
        with clean_env(RAG_CONFIG_OVERLAY_DIR=str(self.overlay)):
            merged = self.load()
        self.assertEqual(merged["reranker"], {**BUNDLE["reranker"], "title": True,
                                              "breaker": {"failures": 5, "open_seconds": 30}})
        self.assertEqual(merged["evidence"], {"tier1_chars": 3200})         # a new object is set whole
        self.assertEqual(merged["opt_in_doc_types"], ["system"])            # a list replaces
        self.assertIsNone(merged["fold_identical"])                         # and so does null
        self.assertEqual(merged["retrievers"], ["bm25", "dense"])           # the root's other keys stay
        # an object over a value that is no object replaces it, and the other way round
        self.write(self.overlay / "retrieval.json", {"fold_identical": {"x": 1}, "grep": False})
        with clean_env(RAG_CONFIG_OVERLAY_DIR=str(self.overlay)):
            merged = self.load()
        self.assertEqual((merged["fold_identical"], merged["grep"]), ({"x": 1}, False))

    def test_the_root_overlay_still_replaces_top_level_keys_whole(self):
        self.write(self.root / "config" / "retrieval.json", {"retriever_weights": {"dense": 2.0}})
        with clean_env(RAG_CONFIG_OVERLAY_DIR=str(self.overlay)):
            self.assertEqual(self.load()["retriever_weights"], {"dense": 2.0})

    def test_absent_is_the_settings_before(self):
        self.write(self.root / "config" / "retrieval.json", BUNDLE)
        with clean_env():
            before = self.load()
        self.assertEqual(list(before), ["rrf_k", "fused_limit", "retriever_weights", *[k for k in BUNDLE
                                                                                     if k != "retriever_weights"]])
        for extra in ({"RAG_CONFIG_OVERLAY_DIR": ""},                                  # empty
                      {"RAG_CONFIG_OVERLAY_DIR": str(self.tmp / "missing")},            # no such directory
                      {"RAG_CONFIG_OVERLAY_DIR": str(self.overlay)},                    # a directory without the file
                      {"RAG_CONFIG_OVERLAY_DIR": str(self.repo / "retrieval.json")}):   # a file, not a directory
            with self.subTest(extra=extra), clean_env(**extra):
                now = self.load()
                self.assertEqual((now, list(now)), (before, list(before)))
                self.assertEqual(json.dumps(now), json.dumps(before))

    def test_every_serving_config_takes_it_and_the_build_config_does_not(self):
        self.write(self.overlay / "serving.json", {"pipeline": {"rerank_gate": 1}})
        self.write(self.overlay / "chunking.json", {"table_header_repeat": True})
        with clean_env(RAG_CONFIG_OVERLAY_DIR=str(self.overlay)):
            self.assertEqual(self.load("serving")["pipeline"], {"rerank_gate": 1})
            self.assertEqual(self.load("chunking"), {"table_header_repeat": False})
            self.assertIsNone(config_mod.release_overlay_path("chunking"))
            self.assertEqual(config_mod.release_overlay_path("dense"), self.overlay / "dense.json")

    def test_a_malformed_file_stops_the_load_as_a_malformed_root_overlay_does(self):
        for text, error in (("[1, 2]", "must hold a JSON object"), ("{not json", "")):
            with self.subTest(text=text):
                self.write(self.root / "config" / "retrieval.json", text)
                with clean_env(), self.assertRaises(ValueError) as in_root:
                    self.load()
                self.write(self.root / "config" / "retrieval.json", "{}")
                self.write(self.overlay / "retrieval.json", text)
                with clean_env(RAG_CONFIG_OVERLAY_DIR=str(self.overlay)), self.assertRaises(ValueError) as in_overlay:
                    self.load()
                self.assertIs(type(in_overlay.exception), type(in_root.exception))
                self.assertIn(error, str(in_overlay.exception))

    def test_merge_into(self):
        base = {"a": {"b": 1, "c": {"d": 2}}, "e": [1]}
        config_mod.merge_into(base, {"a": {"c": {"f": 3}, "g": 4}, "e": [2], "h": {"i": 5}})
        self.assertEqual(base, {"a": {"b": 1, "c": {"d": 2, "f": 3}, "g": 4}, "e": [2], "h": {"i": 5}})


class BuilderCopyTests(Layers):

    @unittest.skipUnless(engine_target.NAME == "builder", "the Forge builder's copy")
    def test_the_builder_s_copy_does_not_read_the_variable(self):
        self.write(self.overlay / "retrieval.json", {"rrf_k": 62})
        with clean_env(RAG_CONFIG_OVERLAY_DIR=str(self.overlay)):
            self.assertEqual(self.load()["rrf_k"], 60)
        self.assertFalse(hasattr(config_mod, "OVERLAY_DIR_ENV"))


@SERVING_ONLY
class ShippedOverlayTests(Layers):
    """src/workstation/ship/site/usr/local/d32/etc/engine-overlay: what the Workstation of 0.1.0-alpha.3 serves with,
    on the knowledge base 0.1.0-alpha.2 shipped."""

    def merged(self, name, root_overlay):
        self.write(self.root / "config" / f"{name}.json", root_overlay)
        with clean_env(RAG_CONFIG_OVERLAY_DIR=str(SHIPPED)):
            return load_config(name, root=self.root)        # the repository's own defaults

    def test_the_files_are_lf_json_objects(self):
        self.assertEqual(sorted(p.name for p in SHIPPED.iterdir()), ["retrieval.json", "serving.json"])
        for path in SHIPPED.iterdir():
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertTrue(raw.endswith(b"}\n"))
            self.assertIsInstance(json.loads(raw), dict)

    def test_the_engine_starts_with_the_merged_retrieval_settings(self):
        from rag_core import api_v2
        merged = self.merged("retrieval", BUNDLE)
        self.assertEqual({k: merged[k] for k in BUNDLE}, BUNDLE)            # nothing of the knowledge base's moves
        self.assertEqual(api_v2.evidence_settings(merged), api_v2.EvidenceSettings(3200, True, True))
        self.assertEqual(api_v2.verify_settings(merged), api_v2.VerifySettings("bounded", 300))
        self.assertIs(api_v2.search_many_settings(merged).rerank_isolation, True)
        self.assertEqual(api_v2.search_many_settings(merged).rerank, "per_query")
        api_v2.opt_in_doc_types(merged), api_v2.grep_settings(merged), api_v2.fold_identical(merged)
        added = set(merged) - set(BUNDLE) - {"rrf_k", "candidate_limit_per_query", "fused_limit"}
        self.assertEqual(added, {"evidence", "verify", "search_many"})

    def test_the_rerank_gate_fits_the_admission_the_knowledge_base_ships(self):
        from rag_core import api
        from rag_core.pipeline import pipeline_settings
        merged = self.merged("serving", {"generation": "d32kb-20261115cr", "max_concurrent_searches": 2,
                                         "search_queue_timeout_seconds": 30})
        self.assertEqual(merged["pipeline"], {"rerank_gate": 1})
        slots, _ = api.admission_settings(merged)
        settings = pipeline_settings(merged, slots=slots, max_queued=api.queue_limit(merged))
        self.assertEqual((slots, api.queue_limit(merged), settings.rerank_gate, settings.rerank_on_timeout),
                         (2, 16, 1, "wait"))

    def test_the_workstation_sets_and_unveils_the_directory(self):
        hook = (engine_target.ROOT / "src/workstation/ship/site/etc/d32/site.d/66-ship-node.sh").read_text(
            encoding="ascii")
        env = hook[hook.index("cat >/etc/d32/core.env <<EOT"):hook.index("cat >/etc/d32/edge.env")]
        self.assertIn("\nRAG_CONFIG_OVERLAY_DIR=/usr/local/d32/etc/engine-overlay\nEOT\n", env)
        launcher = (engine_target.ROOT / "src/workstation/node/shims/py/serve_core.py").read_text(encoding="utf-8")
        self.assertIn('"RAG_CHUNKING_CONFIG",\n                "RAG_CONFIG_OVERLAY_DIR"):', launcher)


if __name__ == "__main__":
    unittest.main()
