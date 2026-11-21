"""scripts/derive_root.py: a new generation from a built one, same index, another serving configuration, with
its own Qdrant collection copied through the reuse path (nothing embedded, the source collection only read).
Qdrant's in-process local mode; no network."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import argparse
import gc
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from qdrant_client import QdrantClient, models

from rag_core.structure import STRUCTURE_VERSION
from test_build_root import REPO, Embedder, load_script
from test_reuse_dense import SIZE, ReuseDenseTests, SharedClient, collection_digest

SERVE_URL = "https://gpu-host.tailnet-example.ts.net:8443/v1/rerank"


def load_derive():
    spec = importlib.util.spec_from_file_location("derive_root", REPO / "scripts" / "derive_root.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.endswith(("-shm", "-wal"))):
        h.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


class DeriveRootTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        ReuseDenseTests.setUpClass.__func__(cls)            # the --no-dense template build
        cls.derive = load_derive()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.data = Path(d.name) / "rag-data"
        self.client = QdrantClient(location=":memory:")
        self.addCleanup(self.client.close)
        self.enterContext(mock.patch.dict(os.environ))
        for key in [k for k in os.environ if k.startswith("RAG_")]:
            del os.environ[key]
        self.enterContext(mock.patch.object(self.derive, "make_qdrant_client",
                                            lambda config: SharedClient(self.client)))
        self.enterContext(mock.patch.object(self.derive.br, "log"))
        self.enterContext(mock.patch.object(self.derive, "log"))
        self.enterContext(warnings.catch_warnings())
        warnings.filterwarnings("ignore", "Payload indexes have no effect", UserWarning)
        # the source: a complete build with dense, as build_root leaves it
        self.source = self.data / "gen-a"
        shutil.copytree(self.template, self.source)
        base = self.script.load_dense_config(REPO / "config" / "dense.json")
        config = self.script.DenseConfig(**{**base.__dict__, "collection": "rag_dense_gen-a", "batch_size": 4,
                                            "embedding_revision": None})
        with mock.patch.object(self.script, "log"), warnings.catch_warnings():
            warnings.filterwarnings("ignore", "Payload indexes have no effect", UserWarning)
            self.script.index_dense_resumable(self.source / "sqlite" / "rag.db", config, Embedder(SIZE), self.client,
                                              sleep=lambda s: None)
        report = json.loads((self.source / "BUILD_REPORT.json").read_text())
        report["generation"] = "gen-a"
        report["dense"] = {"collection": "rag_dense_gen-a", "collection_owned": True, "points": report["chunks"]}
        (self.source / "BUILD_REPORT.json").write_text(json.dumps(report))
        (self.source / "config" / "dense.json").write_text(json.dumps({"collection": "rag_dense_gen-a",
                                                                       "query_timeout_seconds": 1.5}))
        retrieval = json.loads((self.source / "config" / "retrieval.json").read_text())
        retrieval["retrievers"] = ["bm25", "dense"]
        (self.source / "config" / "retrieval.json").write_text(json.dumps(retrieval))
        serving = json.loads((self.source / "config" / "serving.json").read_text())
        serving["generation"] = "gen-a"
        (self.source / "config" / "serving.json").write_text(json.dumps(serving))
        # the setup's own connections: closed and checkpointed, so the source's bytes stay put from here on
        gc.collect()
        conn = sqlite3.connect(self.source / "sqlite" / "rag.db")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

    def run_derive(self, *args):
        return self.derive.main(["--from", str(self.source), *args])

    def report(self, name="gen-rr"):
        return json.loads((self.data / name / "BUILD_REPORT.json").read_text())

    def config(self, name, which):
        return json.loads((self.data / name / "config" / f"{which}.json").read_text())

    def test_a_derived_root_has_the_index_its_own_collection_and_the_new_configuration(self):
        before = (tree_digest(self.source), collection_digest(self.client, "rag_dense_gen-a"))
        source_report = json.loads((self.source / "BUILD_REPORT.json").read_text())
        rc = self.run_derive("--to", "gen-rr", "--rerank-url", SERVE_URL, "--set", "serving.max_concurrent_searches=3",
                             "--set", "retrieval.bm25.prune_high_df=false")
        self.assertEqual(rc, 0)
        report = self.report()
        n = source_report["chunks"]
        self.assertEqual((report["builder"], report["derived"], report["status"], report["generation"]),
                         ("scripts/derive_root.py", True, "complete", "gen-rr"))
        self.assertEqual(report["derived_from"]["root"], str(self.source))
        self.assertEqual(report["derived_from"]["collection"], "rag_dense_gen-a")
        for key in ("corpus", "documents", "chunks", "structure", "chunker"):
            self.assertEqual(report[key], source_report[key], key)
        dense = report["dense"]
        self.assertEqual((dense["collection"], dense["points"], dense["reuse"]["reused"], dense["reuse"]["embedded"]),
                         ("rag_dense_gen-rr", n, n, 0))
        self.assertEqual(len(report["config"]["overrides"]), 3)

        retrieval = self.config("gen-rr", "retrieval")
        self.assertEqual(retrieval["reranker"], {"enabled": True, "url": SERVE_URL, "candidates": 10,
                                                 "timeout_seconds": 15, "breaker": {"failures": 3, "open_seconds": 30}})
        self.assertEqual(retrieval["bm25"], {"prune_high_df": False})
        self.assertEqual(retrieval["retrievers"], ["bm25", "dense"])
        self.assertEqual(self.config("gen-rr", "serving")["generation"], "gen-rr")
        self.assertEqual(self.config("gen-rr", "serving")["max_concurrent_searches"], 3)
        self.assertEqual(self.config("gen-rr", "dense"), {"collection": "rag_dense_gen-rr", "query_timeout_seconds": 1.5})

        # the copy: same vectors in its own collection, the source untouched
        def vectors(name):
            points, _ = self.client.scroll(collection_name=name, limit=10_000, with_payload=["chunk_id"],
                                           with_vectors=True)
            return {p.payload["chunk_id"]: p.vector for p in points}
        self.assertEqual(vectors("rag_dense_gen-rr"), vectors("rag_dense_gen-a"))
        self.assertEqual((tree_digest(self.source), collection_digest(self.client, "rag_dense_gen-a")), before)
        target = self.data / "gen-rr"
        with sqlite3.connect(target / "sqlite" / "rag.db") as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM index_state WHERE dense_indexed = 1").fetchone()[0], n)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM doc_sections").fetchone()[0] > 0, True)
        manifest = Path("sources") / "manifest.json"
        self.assertEqual(os.stat(self.source / manifest).st_ino, os.stat(target / manifest).st_ino)   # linked

        # validate_root reads the derived report like a build's
        spec = importlib.util.spec_from_file_location("validate_root", REPO / "scripts" / "validate_root.py")
        validate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validate)
        args = argparse.Namespace(expect_generation=None, expect_fingerprint=None, expect_docs=None,
                                  expect_chunks=None)
        expected, _ = validate.expectations(args, target)
        self.assertEqual((expected["generation"], expected["n_chunks"], expected["dense_built"]), ("gen-rr", n, True))
        self.assertEqual(expected["corpus_fingerprint"], source_report["corpus"]["fingerprint"])

    def test_resume_refusals_and_owned_keys(self):
        self.assertEqual(self.run_derive("--to", "gen-rr"), 0)
        self.assertEqual(self.run_derive("--to", "gen-rr"), 2)                       # exists: needs --resume
        self.assertEqual(self.run_derive("--to", "gen-rr", "--resume"), 0)
        self.assertEqual(self.report()["dense"]["chunks_indexed_this_run"], 0)
        self.assertEqual(self.report()["runs"], 2)
        for bad in (["--set", "dense.collection=rag_dense_v1"], ["--set", "serving.generation=x"],
                    ["--set", "bogus.key=1"], ["--set", "retrieval=1"], ["--rerank-url", "ftp://gpu/rerank"],
                    ["--set", "retrieval.reranker.enabled=true", "--set", "retrieval.reranker.url=null"],
                    ["--set", "dense.collection.x=1"]):
            with self.subTest(bad=bad):
                self.assertEqual(self.run_derive("--to", "gen-bad", *bad), 2)
                self.assertFalse((self.data / "gen-bad").exists())
        (self.data / "foreign").mkdir()
        (self.data / "foreign" / "x").write_text("x")
        self.assertEqual(self.run_derive("--to", "foreign"), 2)
        self.assertEqual(self.run_derive("--to", str(self.source / "inner")), 2)    # inside the source

    def test_the_served_root_is_a_source_never_a_target(self):
        os.environ["RAG_ROOT"] = str(self.source)
        self.assertEqual(self.derive.main(["--from", "gen-a", "--to", "gen-b"]), 0)  # ids resolve beside RAG_ROOT
        self.assertEqual(self.report("gen-b")["derived_from"]["root"], str(self.source))
        os.environ["RAG_ROOT"] = str(self.data / "gen-c")
        self.assertEqual(self.run_derive("--to", "gen-c"), 2)

    def test_an_incomplete_source_is_refused(self):
        report = json.loads((self.source / "BUILD_REPORT.json").read_text())
        (self.source / "BUILD_REPORT.json").write_text(json.dumps({**report, "status": "running"}))
        self.assertEqual(self.run_derive("--to", "gen-x"), 2)
        (self.source / "BUILD_REPORT.json").write_text(json.dumps({**report, "failed_steps": [{"step": "dense"}]}))
        self.assertEqual(self.run_derive("--to", "gen-x"), 2)
        self.assertFalse((self.data / "gen-x").exists())

    def test_a_structure_an_older_parser_indexed_is_indexed_again_in_the_derived_root_only(self):
        # what a root indexed before STRUCTURE_VERSION p1-v2 holds: another version, and sections that parser
        # missed (here rfc9999's section 2)
        conn = sqlite3.connect(self.source / "sqlite" / "rag.db")
        conn.execute("UPDATE doc_sources SET structure_version = 'p1-v1'")
        conn.execute("DELETE FROM doc_sections WHERE section_id = 'rfc9999#2'")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        source_report = json.loads((self.source / "BUILD_REPORT.json").read_text())
        before = tree_digest(self.source)

        self.assertEqual(self.run_derive("--to", "gen-st"), 0)
        report = self.report("gen-st")
        n_docs = source_report["structure"]["documents"]
        self.assertEqual((report["structure"]["indexed_this_run"], report["structure"]["sections"]),
                         (n_docs, source_report["structure"]["sections"]))
        self.assertTrue(report["structure_reindexed"])
        self.assertIn("structure", report["steps"])
        with sqlite3.connect(self.data / "gen-st" / "sqlite" / "rag.db") as derived:
            self.assertEqual(derived.execute("SELECT DISTINCT structure_version FROM doc_sources").fetchall(),
                             [(STRUCTURE_VERSION,)])
            self.assertIsNotNone(derived.execute(
                "SELECT 1 FROM doc_sections WHERE section_id = 'rfc9999#2'").fetchone())
        self.assertEqual(tree_digest(self.source), before)                          # the source only read

        # a resumed run has nothing to index again and keeps the derived root's own facts
        self.assertEqual(self.run_derive("--to", "gen-st", "--resume"), 0)
        again = self.report("gen-st")["structure"]
        self.assertEqual((again["indexed_this_run"], again["unchanged"], again["sections"]),
                         (0, n_docs, source_report["structure"]["sections"]))

    def test_a_bm25_only_source_derives_without_a_collection(self):
        report = json.loads((self.source / "BUILD_REPORT.json").read_text())
        report["dense"] = {"skipped": "--no-dense"}
        (self.source / "BUILD_REPORT.json").write_text(json.dumps(report))
        retrieval = json.loads((self.source / "config" / "retrieval.json").read_text())
        (self.source / "config" / "retrieval.json").write_text(json.dumps({**retrieval, "retrievers": ["bm25"]}))
        collections = {c.name for c in self.client.get_collections().collections}
        self.assertEqual(self.run_derive("--to", "gen-b25"), 0)
        self.assertEqual(self.report("gen-b25")["dense"], {"skipped": "the source root serves no dense index"})
        self.assertEqual(self.config("gen-b25", "dense")["collection"], "rag_dense_gen-b25")    # never the source's
        self.assertEqual({c.name for c in self.client.get_collections().collections}, collections)
        # asking a dense-less source for dense is refused
        self.assertEqual(self.run_derive("--to", "gen-d", "--set", "retrieval.retrievers=[\"bm25\",\"dense\"]"), 2)

    def test_a_missing_vector_leaves_the_derivation_incomplete_and_resumable(self):
        with sqlite3.connect(self.source / "sqlite" / "rag.db") as conn:
            gone = conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id LIMIT 1").fetchone()[0]
        from qdrant_client import models
        self.client.delete(collection_name="rag_dense_gen-a", points_selector=models.PointIdsList(
            points=[self.script.point_id_for_chunk(gone)]), wait=True)
        self.assertEqual(self.run_derive("--to", "gen-rr"), 1)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("no reusable vector", report["failed_steps"][0]["error"])

    def test_build_root_refuses_to_build_on_a_derived_root(self):
        self.assertEqual(self.run_derive("--to", "gen-rr"), 0)
        for resume in (False, True):
            with self.subTest(resume=resume), \
                    self.assertRaisesRegex(self.derive.br.Refused, "was not written by scripts/build_root.py"):
                self.derive.br.open_report(self.data / "gen-rr", resume)

    # --- serving arms without code edits: the search_many and reranker window keys

    def test_the_search_many_and_rerank_window_keys_are_settable(self):
        from rag_core.api_v2 import SearchManySettings, search_many_settings
        from rag_core.config import load_config
        from rag_core.rerank import Reranker

        sets = ["retrieval.search_many_parallel=true", "retrieval.search_many_rerank=best_query",
                "retrieval.reranker.max_chars=1200", "retrieval.reranker.window=centred"]
        self.assertEqual(self.run_derive("--to", "gen-arm", "--rerank-url", SERVE_URL,
                                         *[a for s in sets for a in ("--set", s)]), 0)
        retrieval = self.config("gen-arm", "retrieval")
        self.assertEqual((retrieval["search_many_parallel"], retrieval["search_many_rerank"]), (True, "best_query"))
        self.assertEqual({k: retrieval["reranker"][k] for k in ("enabled", "url", "candidates", "max_chars", "window")},
                         {"enabled": True, "url": SERVE_URL, "candidates": 10, "max_chars": 1200, "window": "centred"})
        self.assertLessEqual({"retrieval.search_many_parallel=true", 'retrieval.search_many_rerank="best_query"',
                              "retrieval.reranker.max_chars=1200", 'retrieval.reranker.window="centred"'},
                             set(self.report("gen-arm")["config"]["overrides"]))
        # what deep32-engine makes of the derived root's retrieval config (the repo file under the overlay)
        served = load_config("retrieval", root=self.data / "gen-arm")
        self.assertEqual(search_many_settings(served), SearchManySettings(workers=4, rerank="best_query"))
        reranker = Reranker(served["reranker"])
        self.addCleanup(reranker.close)
        self.assertEqual((reranker.max_chars, reranker.window, reranker.candidates), (1200, "centred", 10))
        # a pool size, and the defaults set explicitly
        self.assertEqual(self.run_derive("--to", "gen-arm2", "--set", "retrieval.search_many_parallel=2",
                                         "--set", "retrieval.search_many_rerank=per_query"), 0)
        self.assertEqual(search_many_settings(load_config("retrieval", root=self.data / "gen-arm2")),
                         SearchManySettings(workers=2, rerank="per_query"))

    def test_values_rag_core_would_not_start_with_are_refused_before_anything_is_written(self):
        cases = {
            "search_many_parallel": ["--set", "retrieval.search_many_parallel=yes"],
            "search_many_rerank": ["--set", "retrieval.search_many_rerank=best"],
            "reranker window": ["--rerank-url", SERVE_URL, "--set", "retrieval.reranker.window=centered"],
            "invalid literal": ["--rerank-url", SERVE_URL, "--set", "retrieval.reranker.max_chars=big"],
        }
        before = self.collections()
        for why, extra in cases.items():
            self.derive.log.reset_mock()
            with self.subTest(why):
                self.assertEqual(self.run_derive("--to", "gen-bad", *extra), 2)
                self.assertIn(why, self.refusal())
                self.assertFalse((self.data / "gen-bad").exists())
        self.assertEqual(self.collections(), before)

    def test_the_opt_in_doc_types_key_is_settable_and_checked(self):
        from rag_core.api_v2 import opt_in_doc_types
        from rag_core.config import load_config

        self.assertEqual(self.run_derive("--to", "gen-oi", "--set",
                                         'retrieval.opt_in_doc_types=["powershell","system"]'), 0)
        self.assertEqual(self.config("gen-oi", "retrieval")["opt_in_doc_types"], ["powershell", "system"])
        self.assertIn('retrieval.opt_in_doc_types=["powershell", "system"]',
                      self.report("gen-oi")["config"]["overrides"])
        self.assertEqual(opt_in_doc_types(load_config("retrieval", root=self.data / "gen-oi")),
                         frozenset({"powershell", "system"}))
        # a value deep32-engine would not start with is refused before anything is written
        before = self.collections()
        for bad in ("retrieval.opt_in_doc_types=powershell", "retrieval.opt_in_doc_types=[1]",
                    "retrieval.opt_in_doc_types=null"):
            self.derive.log.reset_mock()
            with self.subTest(bad=bad):
                self.assertEqual(self.run_derive("--to", "gen-bad", "--set", bad), 2)
                self.assertIn("opt_in_doc_types", self.refusal())
                self.assertFalse((self.data / "gen-bad").exists())
        self.assertEqual(self.collections(), before)

    # --- vectors that do not fit: refused in preflight, before the clone writes anything

    def collections(self):
        return {c.name for c in self.client.get_collections().collections}

    def refusal(self):
        return " ".join(str(c.args[0]) for c in self.derive.log.call_args_list if "REFUSED" in str(c.args[0]))

    def test_vectors_that_do_not_fit_the_derived_config_are_refused_before_anything_is_written(self):
        before = self.collections()
        cases = {
            "configured for 768-d": (["--set", "dense.vector_size=768"], {}),
            "RAG_EMBEDDING_REVISION": ([], {"RAG_EMBEDDING_REVISION": "r9"}),
            "would not load": (["--set", "dense.vector_size=\"big\""], {}),
        }
        for why, (extra, env) in cases.items():
            self.derive.log.reset_mock()
            with self.subTest(why), mock.patch.dict(os.environ, env):
                self.assertEqual(self.run_derive("--to", "gen-x", *extra), 2)
                self.assertIn(why, self.refusal())
                self.assertFalse((self.data / "gen-x").exists())
                self.assertEqual(self.collections(), before)
        with sqlite3.connect(self.source / "sqlite" / "rag.db") as conn:
            conn.execute("UPDATE index_state SET embedding_revision = 'r2' "
                         "WHERE rowid = (SELECT MIN(rowid) FROM index_state)")
        conn.close()
        self.derive.log.reset_mock()
        self.assertEqual(self.run_derive("--to", "gen-x"), 2)
        self.assertIn("mixes embedding models or revisions", self.refusal())
        self.assertFalse((self.data / "gen-x").exists())
        # one revision, the one this process writes: derived
        with sqlite3.connect(self.source / "sqlite" / "rag.db") as conn:
            conn.execute("UPDATE index_state SET embedding_revision = 'r2'")
        conn.close()
        with mock.patch.dict(os.environ, {"RAG_EMBEDDING_REVISION": "r2"}):
            self.assertEqual(self.run_derive("--to", "gen-x"), 0)
        self.assertEqual(self.report("gen-x")["dense"]["embedding_revision"], "r2")

    # --- the copy's index: waited for, so that validate_root does not measure a background HNSW build

    def test_the_dense_step_waits_until_qdrant_has_indexed_the_copy(self):
        indexing = Indexing(self.client, "rag_dense_gen-rr", [models.CollectionStatus.YELLOW] * 2)
        with mock.patch.object(self.derive, "make_qdrant_client", lambda config: indexing), \
                mock.patch.object(self.derive.br, "INDEXED_POLL_SECONDS", 0.0):
            self.assertEqual(self.run_derive("--to", "gen-rr"), 0)
        self.assertEqual(indexing.polls, 3)
        dense = self.report()["dense"]
        self.assertEqual((dense["index"]["status"], dense["index"]["points"]), ("green", dense["points"]))

    def test_a_copy_still_indexing_at_the_timeout_is_reported_not_failed(self):
        indexing = Indexing(self.client, "rag_dense_gen-rr", [models.CollectionStatus.YELLOW] * 100)
        with mock.patch.object(self.derive, "make_qdrant_client", lambda config: indexing), \
                mock.patch.object(self.derive.br, "INDEXED_POLL_SECONDS", 0.0), \
                mock.patch.object(self.derive.br, "INDEXED_WAIT_SECONDS", 0.0):
            self.assertEqual(self.run_derive("--to", "gen-rr"), 0)
        self.assertEqual(self.report()["dense"]["index"]["status"], "yellow")
        logged = " ".join(str(c.args[0]) for c in self.derive.br.log.call_args_list)
        self.assertIn("still indexing", logged)

    def test_wait_until_indexed(self):
        wait = self.derive.br.wait_until_indexed

        class Fake:
            def __init__(self, statuses):
                self.statuses = list(statuses)

            def get_collection(self, collection_name):
                return SimpleNamespace(status=self.statuses.pop(0), points_count=5, indexed_vectors_count=4)

        now = [0.0]

        def run(statuses, timeout):
            now[0] = 0.0
            return wait(Fake(statuses), "c", timeout=timeout, sleep=lambda s: now.__setitem__(0, now[0] + 10),
                        clock=lambda: now[0])

        self.assertEqual(run(["yellow", "yellow", "green"], 100),
                         {"status": "green", "seconds": 20.0, "points": 5, "indexed_vectors": 4})
        self.assertEqual(run([models.CollectionStatus.YELLOW] * 9, 30)["status"], "yellow")
        self.assertEqual(now[0], 30.0)                                  # gave up at the timeout
        self.assertEqual(run([models.CollectionStatus.GREY], 30)["seconds"], 0.0)   # pending, not running
        self.assertEqual(run(["red"], 30)["status"], "red")


class Indexing(SharedClient):
    """The shared in-memory client, except that `name` reports the given statuses (then green), as Qdrant does
    while it builds a collection's index."""

    def __init__(self, client, name, statuses):
        super().__init__(client)
        self.name, self.statuses, self.polls = name, list(statuses), 0

    def get_collection(self, collection_name):
        info = self.client.get_collection(collection_name=collection_name)
        if collection_name != self.name:
            return info
        self.polls += 1
        status = self.statuses.pop(0) if self.statuses else models.CollectionStatus.GREEN
        return info.model_copy(update={"status": status})


if __name__ == "__main__":
    unittest.main()
