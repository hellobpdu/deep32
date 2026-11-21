"""build_root --reuse-dense-from: vectors of unchanged chunks come from the earlier generation's collection
(fetched by point id, read-only), only new or changed chunks are embedded; per-document resume; the preflight
refusals. Qdrant's in-process local mode, a fake embedder; no network."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
import warnings
from pathlib import Path
from unittest import mock

from qdrant_client import QdrantClient, models

from test_build_root import REPO, SCRIPT, Embedder, load_script, make_corpus

SIZE = 1024


def db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


class SharedClient:
    """The in-memory client for code that closes its client when done."""

    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        return getattr(self.client, name)

    def close(self):
        pass


def collection_digest(client, name):
    """Every point of a collection: id, vector and payload, hashed."""
    rows, offset = [], None
    while True:
        points, offset = client.scroll(collection_name=name, limit=500, offset=offset, with_payload=True,
                                       with_vectors=True)
        rows += [json.dumps([str(p.id), p.vector, p.payload], sort_keys=True) for p in points]
        if offset is None:
            break
    return len(rows), hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()


class ReuseDenseTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        make_corpus(tmp / "corpus")
        cls.template = tmp / "template"
        env = engine_target.minimal_env(PYTHONPATH=str(REPO / "src"))
        subprocess.run([sys.executable, str(SCRIPT), "--corpus-ref", "corpus-v1", "--corpus-repo",
                        str(tmp / "corpus"), "--root", str(cls.template), "--no-dense"],
                       check=True, capture_output=True, env=env, timeout=300)
        cls.script = load_script()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.data = Path(d.name) / "rag-data"
        self.client = QdrantClient(location=":memory:")
        self.addCleanup(self.client.close)
        self.enterContext(mock.patch.object(self.script, "log"))
        self.enterContext(warnings.catch_warnings())
        warnings.filterwarnings("ignore", "Payload indexes have no effect", UserWarning)
        base = self.script.load_dense_config(REPO / "config" / "dense.json")
        self.base = base
        # the earlier generation: a complete build with its own collection
        self.source = self.copy("gen-a")
        self.index(self.source, "rag_dense_gen-a", Embedder(SIZE))
        report = json.loads((self.source / "BUILD_REPORT.json").read_text())
        report["dense"] = {"collection": "rag_dense_gen-a", "collection_owned": True}
        (self.source / "BUILD_REPORT.json").write_text(json.dumps(report))
        (self.source / "config" / "dense.json").write_text(json.dumps({"collection": "rag_dense_gen-a"}))
        self.target = self.copy("gen-b")

    def copy(self, name):
        root = self.data / name
        shutil.copytree(self.template, root)
        return root

    def config(self, collection):
        return self.script.DenseConfig(**{**self.base.__dict__, "collection": collection, "batch_size": 2,
                                          "embedding_revision": None})

    def dense_source(self):
        return self.script.DenseSource(self.source, "rag_dense_gen-a", self.client, SIZE)

    def index(self, root, collection, embedder, reuse=None, sleeps=None):
        return self.script.index_dense_resumable(root / "sqlite" / "rag.db", self.config(collection), embedder,
                                                 self.client, sleep=(sleeps if sleeps is not None else []).append,
                                                 reuse=reuse)

    def chunks(self, root):
        with db(root / "sqlite" / "rag.db") as conn:
            return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def vectors(self, collection):
        out, offset = {}, None
        while True:
            points, offset = self.client.scroll(collection_name=collection, limit=500, offset=offset,
                                                with_payload=["chunk_id"], with_vectors=True)
            out.update({p.payload["chunk_id"]: p.vector for p in points})
            if offset is None:
                return out

    # --- the reuse path

    def test_an_unchanged_generation_is_copied_without_embedding_anything(self):
        before = collection_digest(self.client, "rag_dense_gen-a")
        embedder = Embedder(SIZE)
        source = self.dense_source()
        result = self.index(self.target, "rag_dense_gen-b", embedder, reuse=source)
        source.close()
        n = self.chunks(self.target)
        self.assertEqual(embedder.calls, 0)
        self.assertEqual((result["reused_this_run"], result["embedded_this_run"], result["points"]), (n, 0, n))
        self.assertEqual(result["embedding_model"], "test-embedder")
        self.assertEqual(self.vectors("rag_dense_gen-b"), self.vectors("rag_dense_gen-a"))
        self.assertEqual(collection_digest(self.client, "rag_dense_gen-a"), before)     # read-only
        with db(self.target / "sqlite" / "rag.db") as conn:
            marked = conn.execute("SELECT COUNT(*), MIN(embedding_model) FROM index_state "
                                  "WHERE dense_indexed = 1").fetchone()
        self.assertEqual(tuple(marked), (n, "test-embedder"))

    def test_changed_and_new_chunks_are_embedded_the_rest_reused(self):
        with db(self.target / "sqlite" / "rag.db") as conn:
            doc = conn.execute("SELECT doc_id FROM chunks ORDER BY doc_id LIMIT 1").fetchone()[0]
            # a new manifest title changes the embedding text, not the chunk id
            changed = conn.execute("UPDATE chunks SET embedding_text = 'Document: renamed' || substr(embedding_text, 10) "
                                   "WHERE doc_id = ?", (doc,)).rowcount
            other = conn.execute("SELECT doc_id FROM chunks WHERE doc_id != ? LIMIT 1", (doc,)).fetchone()[0]
            conn.execute("INSERT INTO chunks(chunk_id, chunk_sha256, doc_id, ordinal, heading_path_json, text, "
                         "embedding_text, token_count, char_count) VALUES ('chk_new', 'x', ?, 999, '[]', "
                         "'brand new', 'Document: t\nSection: s\n\nbrand new', 2, 9)", (other,))
        embedder = Embedder(SIZE)
        result = self.index(self.target, "rag_dense_gen-b", embedder, reuse=self.dense_source())
        n = self.chunks(self.target)
        self.assertEqual(result["embedded_this_run"], changed + 1)
        self.assertEqual(result["reused_this_run"], n - changed - 1)
        self.assertEqual(result["points"], n)
        new, old = self.vectors("rag_dense_gen-b"), self.vectors("rag_dense_gen-a")
        with db(self.target / "sqlite" / "rag.db") as conn:
            same = [r[0] for r in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id NOT IN (?) "
                                               "AND chunk_id != 'chk_new'", (doc,))]
        self.assertTrue(same)
        self.assertTrue(all(new[c] == old[c] for c in same))
        self.assertIn("chk_new", new)

    def first_chunks(self, n):
        with db(self.source / "sqlite" / "rag.db") as conn:
            return [tuple(r) for r in conn.execute("SELECT chunk_id, doc_id FROM chunks ORDER BY chunk_id LIMIT ?",
                                                   (n,))]

    def test_a_stray_point_that_carries_the_chunk_id_is_not_copied(self):
        [(chunk_id, doc_id)] = self.first_chunks(1)
        self.client.delete(collection_name="rag_dense_gen-a", points_selector=models.PointIdsList(
            points=[self.script.point_id_for_chunk(chunk_id)]), wait=True)
        stray = [1.0] + [0.0] * (SIZE - 1)                   # a direction no fake embedding has
        self.client.upsert(collection_name="rag_dense_gen-a", wait=True, points=[models.PointStruct(
            id=str(uuid.uuid4()), vector=stray, payload={"chunk_id": chunk_id, "doc_id": doc_id})])
        result = self.index(self.target, "rag_dense_gen-b", Embedder(SIZE), reuse=self.dense_source())
        self.assertEqual((result["embedded_this_run"], result["points"]), (1, self.chunks(self.target)))
        vector = self.vectors("rag_dense_gen-b")[chunk_id]
        self.assertEqual(len({round(v, 6) for v in vector}), 1, "the stray point's vector was copied")

    def test_a_point_whose_payload_names_another_chunk_or_document_is_not_copied(self):
        (c1, _), (c2, _) = self.first_chunks(2)
        pid = self.script.point_id_for_chunk
        self.client.set_payload(collection_name="rag_dense_gen-a", payload={"chunk_id": "chk_other"},
                                points=[pid(c1)], wait=True)
        self.client.set_payload(collection_name="rag_dense_gen-a", payload={"doc_id": "doc_other"},
                                points=[pid(c2)], wait=True)
        result = self.index(self.target, "rag_dense_gen-b", Embedder(SIZE), reuse=self.dense_source())
        self.assertEqual(result["embedded_this_run"], 2)
        self.assertEqual(result["reused_this_run"], self.chunks(self.target) - 2)

    def test_points_are_fetched_by_id_in_pages(self):
        source = self.dense_source()
        with mock.patch.object(self.script.DenseSource, "PAGE", 2), \
                mock.patch.object(self.client, "scroll", side_effect=AssertionError("scrolled")), \
                mock.patch.object(self.client, "retrieve", wraps=self.client.retrieve) as spy:
            result = self.index(self.target, "rag_dense_gen-b", Embedder(SIZE), reuse=source)
        source.close()
        n = self.chunks(self.target)
        self.assertEqual((result["reused_this_run"], result["embedded_this_run"]), (n, 0))
        self.assertTrue(all(len(c.kwargs["ids"]) <= 2 for c in spy.call_args_list))
        self.assertGreaterEqual(spy.call_count, n // 2)
        self.assertEqual(self.vectors("rag_dense_gen-b"), self.vectors("rag_dense_gen-a"))

    def test_point_ids_compare_in_canonical_form(self):
        spelled = str(uuid.uuid4())
        self.assertEqual(self.script.canonical_point_id(spelled.upper().replace("-", "")), spelled)
        self.assertEqual(self.script.canonical_point_id(uuid.UUID(spelled)), spelled)
        self.assertEqual(self.script.canonical_point_id(42), "42")

    def test_the_build_step_reuses_then_waits_for_the_index(self):
        (self.target / "config" / "dense.json").write_text(json.dumps({"collection": "rag_dense_gen-b"}))
        embedder = Embedder(SIZE)
        embedder.close = lambda: None
        report = {}
        with mock.patch.dict(os.environ), \
                mock.patch.object(self.script, "make_qdrant_client", lambda config: SharedClient(self.client)), \
                mock.patch.object(self.script, "JinaEmbeddingClient", lambda config: embedder):
            for key in [k for k in os.environ if k.startswith("RAG_")]:
                del os.environ[key]
            self.script.step_dense(report, self.target, self.target / "sqlite" / "rag.db", "rag_dense_gen-b",
                                   reuse_from="gen-a")
        n = self.chunks(self.target)
        self.assertEqual((report["dense"]["reuse"]["reused"], report["dense"]["reuse"]["embedded"]), (n, 0))
        self.assertEqual(embedder.calls, 0)
        self.assertEqual((report["dense"]["index"]["status"], report["dense"]["index"]["points"]), ("green", n))

    def test_a_point_missing_from_the_source_collection_is_embedded(self):
        with db(self.source / "sqlite" / "rag.db") as conn:
            gone = conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id LIMIT 2").fetchall()
        self.client.delete(collection_name="rag_dense_gen-a", points_selector=models.PointIdsList(
            points=[self.script.point_id_for_chunk(r[0]) for r in gone]), wait=True)
        result = self.index(self.target, "rag_dense_gen-b", Embedder(SIZE), reuse=self.dense_source())
        self.assertEqual(result["embedded_this_run"], 2)
        self.assertEqual(result["points"], self.chunks(self.target))

    def test_a_stopped_run_resumes_per_document(self):
        with db(self.target / "sqlite" / "rag.db") as conn:
            conn.execute("UPDATE chunks SET embedding_text = embedding_text || ' v2'")    # every chunk changed
        failing = Embedder(SIZE, fail_first=99)
        with self.assertRaises(Exception):
            self.index(self.target, "rag_dense_gen-b", failing, reuse=self.dense_source())
        result = self.index(self.target, "rag_dense_gen-b", Embedder(SIZE), reuse=self.dense_source())
        self.assertEqual((result["reused_this_run"], result["points"]), (0, self.chunks(self.target)))
        again = self.index(self.target, "rag_dense_gen-b", Embedder(SIZE), reuse=self.dense_source())
        self.assertEqual(again["chunks_indexed_this_run"], 0)

    def test_without_an_embedder_a_missing_vector_fails_the_step(self):
        with db(self.target / "sqlite" / "rag.db") as conn:
            conn.execute("UPDATE chunks SET embedding_text = embedding_text || ' v2' WHERE rowid = "
                         "(SELECT MAX(rowid) FROM chunks)")
        with self.assertRaisesRegex(RuntimeError, "no reusable vector"):
            self.index(self.target, "rag_dense_gen-b", None, reuse=self.dense_source())

    def test_a_root_embedded_by_another_model_does_not_take_these_vectors(self):
        with db(self.target / "sqlite" / "rag.db") as conn:
            cid = conn.execute("SELECT chunk_id FROM chunks LIMIT 1").fetchone()[0]
            conn.execute("INSERT INTO index_state(chunk_id, dense_indexed, embedding_model) VALUES (?, 1, 'other') "
                         "ON CONFLICT(chunk_id) DO UPDATE SET dense_indexed = 1, embedding_model = 'other'", (cid,))
        with self.assertRaisesRegex(RuntimeError, "embedded by"):
            self.index(self.target, "rag_dense_gen-b", Embedder(SIZE), reuse=self.dense_source())

    # --- preflight

    def args(self, **kw):
        return argparse.Namespace(**{"no_dense": False, "reuse_dense_from": "gen-a", **kw})

    def check(self, **kw):
        with mock.patch.object(self.script, "make_qdrant_client", lambda config: SharedClient(self.client)):
            self.script.check_dense_source(self.args(**kw), self.target, "rag_dense_gen-b")

    def test_preflight_accepts_a_complete_generation(self):
        self.check()
        self.check(reuse_dense_from=str(self.source))                   # a path works too

    def test_preflight_refusals(self):
        Refused = self.script.Refused
        cases = {
            "--no-dense": dict(no_dense=True),
            "the root being built": dict(reuse_dense_from="gen-b"),
            "no sqlite/rag.db": dict(reuse_dense_from="gen-missing"),
            "not a generation id": dict(reuse_dense_from="Gen A"),
        }
        for why, kw in cases.items():
            with self.subTest(why), self.assertRaisesRegex(Refused, why.replace("--", "")):
                self.check(**kw)
        with self.assertRaisesRegex(Refused, "the one this build writes"):
            with mock.patch.object(self.script, "make_qdrant_client", lambda config: SharedClient(self.client)):
                self.script.check_dense_source(self.args(), self.target, "rag_dense_gen-a")

    def test_preflight_refuses_an_unfinished_or_mixed_source(self):
        Refused = self.script.Refused
        report_path = self.source / "BUILD_REPORT.json"
        report = json.loads(report_path.read_text())
        for change, why in (({"status": "running"}, "not complete"), ({"dense": {"skipped": "--no-dense"}},
                                                                       "no dense index")):
            report_path.write_text(json.dumps({**report, **change}))
            with self.subTest(why), self.assertRaisesRegex(Refused, why):
                self.check()
        report_path.write_text(json.dumps(report))
        self.client.delete_collection("rag_dense_gen-a")
        with self.assertRaisesRegex(Refused, "no collection"):
            self.check()

    def test_preflight_refuses_other_embedding_revisions(self):
        Refused = self.script.Refused
        with db(self.source / "sqlite" / "rag.db") as conn:
            conn.execute("UPDATE index_state SET embedding_revision = 'r2' WHERE rowid = (SELECT MIN(rowid) "
                         "FROM index_state)")
        with self.assertRaisesRegex(Refused, "mixes embedding models"):
            self.check()
        with db(self.source / "sqlite" / "rag.db") as conn:
            conn.execute("UPDATE index_state SET embedding_revision = 'r2'")
        with self.assertRaisesRegex(Refused, "embedding revision 'r2'"):
            self.check()



class CommandLineRefusalTests(unittest.TestCase):

    def run_script(self, root, *args):
        env = engine_target.minimal_env(PYTHONPATH=str(REPO / "src"))
        return subprocess.run([sys.executable, str(SCRIPT), "--corpus-ref", "corpus-v1", "--corpus-repo", "/nonexistent",
                               "--root", str(root), *args], capture_output=True, text=True, env=env, timeout=120)

    def test_refused_before_anything_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "rag-data" / "gen-c"
            run = self.run_script(root, "--no-dense", "--reuse-dense-from", "gen-a")
            self.assertEqual(run.returncode, 2, run.stdout)
            self.assertIn("cannot go with --no-dense", run.stdout)
            run = self.run_script(root, "--no-dense", "--rerank-url", "ftp://gpu/v1/rerank")
            self.assertEqual(run.returncode, 2, run.stdout)
            self.assertIn("not an http(s) URL", run.stdout)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
