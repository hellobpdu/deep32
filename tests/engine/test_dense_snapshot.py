"""derive_root --dense-clone snapshot and build_root --dense-base snapshot: a root's collection made by Qdrant from a
snapshot of another collection (its segments, HNSW graph included), checked; the defaults (copy, upsert) unchanged.
Qdrant's in-process local mode has no snapshots: SnapshotQdrant plays Qdrant 1.19's snapshot calls on top of it
(tests/test_dense_snapshot_live.py runs the real ones against a local server). No network."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import copy
import gc
import hashlib
import json
import os
import random
import shutil
import sqlite3
import tempfile
import unittest
import uuid
import warnings
from pathlib import Path
from unittest import mock

from qdrant_client import QdrantClient, models

import test_build_root as tbr
import test_derive_root as tdr
import test_reuse_dense as trd

SIZE = trd.SIZE
SNAPSHOTS = "/qdrant/snapshots"


def text_vector(text: str) -> list[float]:
    """A direction of its own for every text (the suite's Embedder gives all chunks one direction: every search ties)."""
    rnd = random.Random(hashlib.sha256(text.encode()).digest())
    vector = [rnd.uniform(-1.0, 1.0) for _ in range(SIZE)]
    norm = sum(v * v for v in vector) ** 0.5
    return [v / norm for v in vector]                   # unit length, as TEI returns them


class TextEmbedder:
    def __init__(self, fail_first=0):
        self.fail_first, self.calls, self.texts = fail_first, 0, []

    def embed(self, texts):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise ValueError("TEI is down")              # not retried (rag_core.errors: not a transport error)
        self.texts += texts
        return [text_vector(t) for t in texts], "test-embedder"

    def close(self):
        pass


class QdrantError(Exception):
    """What qdrant_client raises for an error response (UnexpectedResponse), as far as these scripts care."""


class SnapshotQdrant(trd.SharedClient):
    """The in-memory client with Qdrant 1.19's snapshot calls: create_snapshot keeps a copy of the collection as
    local mode holds it (its stored vectors, payloads and their order: what a segment snapshot carries) under a new
    name with a sha256 checksum, recover_snapshot makes the target from a file:// location
    <snapshots_dir>/<collection>/<name> with that checksum and priority snapshot (403 outside the directory, 400 for
    a file it does not have or a wrong checksum), delete_snapshot drops it (404 when absent). `mangle(client,
    collection)` changes a recovered collection; `fail_delete` makes that many deletes fail; `fail_recover` leaves
    an empty target behind and fails."""

    def __init__(self, client, snapshots_dir=SNAPSHOTS, mangle=None, fail_delete=0, fail_recover=False):
        super().__init__(client)
        self.snapshots_dir, self.mangle = snapshots_dir, mangle
        self.fail_delete, self.fail_recover = fail_delete, fail_recover
        self.snapshots, self.calls, self.n = {}, [], 0
        self._local = client._client                    # qdrant_client.local.QdrantLocal: its collections by name

    def create_snapshot(self, collection_name, wait=True):
        self.calls.append(("create", collection_name, wait))
        held = copy.deepcopy(self._local.collections[collection_name])
        self.n += 1
        name = f"{collection_name}-{self.n}-2026-11-02-10-00-00.snapshot"
        checksum = hashlib.sha256(name.encode()).hexdigest()
        self.snapshots[(collection_name, name)] = (held, checksum)
        return models.SnapshotDescription(name=name, size=4096 * len(held.ids), creation_time=None,
                                          checksum=checksum)

    def recover_snapshot(self, collection_name, location, api_key=None, checksum=None, priority=None, wait=True):
        self.calls.append(("recover", collection_name, location, priority, wait))
        prefix = f"file://{self.snapshots_dir}/"
        if not location.startswith(prefix):
            raise QdrantError(f"403: Snapshot file path must be inside the snapshots directory {self.snapshots_dir}")
        source, _, name = location[len(prefix):].partition("/")
        if (source, name) not in self.snapshots:
            raise QdrantError(f"400: Snapshot file {location} does not exist")
        held, expected = self.snapshots[(source, name)]
        if checksum != expected:
            raise QdrantError("400: Snapshot checksum mismatch")
        if priority != models.SnapshotPriority.SNAPSHOT:
            raise AssertionError(f"priority {priority}: a new collection needs priority snapshot (vendor docs)")
        if self.fail_recover:
            self.client.create_collection(collection_name=collection_name, vectors_config=held.config.vectors)
            raise QdrantError("500: recovery failed half way")
        self._local.collections[collection_name] = copy.deepcopy(held)
        if self.mangle:
            self.mangle(self.client, collection_name)
        return True

    def delete_snapshot(self, collection_name, snapshot_name, wait=True):
        self.calls.append(("delete", collection_name, snapshot_name, wait))
        if self.fail_delete:
            self.fail_delete -= 1
            raise QdrantError("503: try again")
        if self.snapshots.pop((collection_name, snapshot_name), None) is None:
            raise QdrantError(f"404: Snapshot {snapshot_name} not found")
        return True

    def list_snapshots(self, collection_name):
        return [models.SnapshotDescription(name=name, size=0, creation_time=None, checksum=c)
                for (collection, name), (_, c) in self.snapshots.items() if collection == collection_name]

    def made(self, what):
        return [c for c in self.calls if c[0] == what]


class Yellow(trd.SharedClient):
    """`name` is still being optimized (status yellow), as Qdrant reports a collection it is indexing."""

    def __init__(self, client, name):
        super().__init__(client)
        self.name = name

    def get_collection(self, collection_name):
        info = self.client.get_collection(collection_name=collection_name)
        if collection_name == self.name:
            return info.model_copy(update={"status": models.CollectionStatus.YELLOW})
        return info


def all_points(client, collection):
    points, _ = client.scroll(collection_name=collection, limit=10_000, with_payload=True, with_vectors=True)
    return points


def nudge(client, collection):
    """Every vector a little off: a recovery that does not search as its source does."""
    client.upsert(collection_name=collection, wait=True, points=[
        models.PointStruct(id=p.id, vector=[v * (1.0 + 0.05 * (i % 3)) for i, v in enumerate(p.vector)],
                           payload=p.payload) for p in all_points(client, collection)])


def drop_one(client, collection):
    client.delete(collection_name=collection, wait=True,
                  points_selector=models.PointIdsList(points=[all_points(client, collection)[0].id]))


def logged(*mocks):
    return "\n".join(str(c.args[0]) for m in mocks for c in m.call_args_list)


def points_of(client, name):
    """{point id: (vector, payload)} of a collection."""
    out, offset = {}, None
    while True:
        points, offset = client.scroll(collection_name=name, limit=500, offset=offset, with_payload=True,
                                       with_vectors=True)
        out.update({str(p.id): (p.vector, p.payload) for p in points})
        if offset is None:
            return out


class SamePoints:
    def assertSamePoints(self, a, b):
        """The same ids and payloads, and vectors equal to 1e-6: Qdrant's local mode normalizes a collection's
        stored vectors in place at its first search, which moves their last bits (the real server's bit-for-bit
        equality is tests/test_dense_snapshot_live.py's)."""
        self.assertEqual(sorted(a), sorted(b))
        for pid, (vector, payload) in a.items():
            other, other_payload = b[pid]
            self.assertEqual(payload, other_payload, pid)
            self.assertEqual(len(vector), len(other), pid)
            self.assertLess(max(abs(x - y) for x, y in zip(vector, other)), 1e-6, pid)


class DeriveSnapshotTests(SamePoints, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        trd.ReuseDenseTests.setUpClass.__func__(cls)          # the --no-dense template build
        cls.derive = tdr.load_derive()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.data = Path(d.name) / "rag-data"
        self.client = QdrantClient(location=":memory:")
        self.addCleanup(self.client.close)
        self.snap = SnapshotQdrant(self.client)
        self.enterContext(mock.patch.dict(os.environ))
        for key in [k for k in os.environ if k.startswith("RAG_")]:
            del os.environ[key]
        self.enterContext(mock.patch.object(self.derive, "make_qdrant_client",
                                            lambda config: trd.SharedClient(self.client)))
        self.enterContext(mock.patch.object(self.derive.br, "snapshot_client", lambda config: self.snap))
        self.br_log = self.enterContext(mock.patch.object(self.derive.br, "log"))
        self.log = self.enterContext(mock.patch.object(self.derive, "log"))
        self.enterContext(warnings.catch_warnings())
        warnings.filterwarnings("ignore", "Payload indexes have no effect", UserWarning)
        # the source: a complete build with dense (one direction per chunk text), as build_root leaves it
        self.source = self.data / "gen-a"
        shutil.copytree(self.template, self.source)
        base = self.script.load_dense_config(tbr.REPO / "config" / "dense.json")
        config = self.script.DenseConfig(**{**base.__dict__, "collection": "rag_dense_gen-a", "batch_size": 4,
                                            "embedding_revision": None})
        with mock.patch.object(self.script, "log"):
            self.script.index_dense_resumable(self.source / "sqlite" / "rag.db", config, TextEmbedder(), self.client,
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
        gc.collect()
        conn = sqlite3.connect(self.source / "sqlite" / "rag.db")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        self.n = report["chunks"]
        self.q = min(self.derive.br.CLONE_CHECK_SAMPLE, self.n)                # stored vectors searched

    def run_derive(self, *args):
        return self.derive.main(["--from", str(self.source), *args])

    def report(self, name="gen-rr"):
        return json.loads((self.data / name / "BUILD_REPORT.json").read_text())

    def collections(self):
        return {c.name for c in self.client.get_collections().collections}

    def marked(self, name="gen-rr"):
        with sqlite3.connect(self.data / name / "sqlite" / "rag.db") as conn:
            return conn.execute("SELECT COUNT(*) FROM index_state WHERE dense_indexed = 1").fetchone()[0]

    def dense_lines(self):
        return [line for line in logged(self.log).splitlines() if line.startswith("dense: ") and " points in " in line]

    # --- the clone

    def test_a_snapshot_clone_holds_the_source_points_and_searches_as_it_does(self):
        files, points = tdr.tree_digest(self.source), points_of(self.client, "rag_dense_gen-a")
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 0)
        report = self.report()
        self.assertEqual(report["status"], "complete")
        dense = report["dense"]
        self.assertEqual((dense["collection"], dense["points"], dense["reuse"]["reused"], dense["reuse"]["embedded"]),
                         ("rag_dense_gen-rr", self.n, self.n, 0))
        self.assertEqual((dense["chunks_indexed_this_run"], dense["collection_created"]), (self.n, True))
        clone = dense["clone"]
        self.assertEqual((clone["mode"], clone["complete"], clone["points"]),
                         ("snapshot", True, {"points": self.n, "strays_deleted": 0}))
        restored = clone["restored"]
        self.assertEqual((restored["from_collection"], restored["points"]), ("rag_dense_gen-a", self.n))
        self.assertEqual(restored["check"]["searches"], {"unfiltered": {"queries": self.q, "differ": 0}})
        self.assertNotIn("pending_snapshot", clone)
        # the very points of the source (ids, vectors, payloads), the source untouched, the snapshot deleted
        self.assertSamePoints(points_of(self.client, "rag_dense_gen-rr"), points)
        self.assertSamePoints(points_of(self.client, "rag_dense_gen-a"), points)
        self.assertEqual(tdr.tree_digest(self.source), files)
        self.assertEqual(self.snap.snapshots, {})
        [(_, name, wait)] = self.snap.made("create")
        self.assertEqual((name, wait), ("rag_dense_gen-a", True))
        [(_, target, location, priority, wait)] = self.snap.made("recover")
        snapshot = restored["snapshot"]["name"]
        self.assertEqual((target, location, priority, wait),
                         ("rag_dense_gen-rr", f"file:///qdrant/snapshots/rag_dense_gen-a/{snapshot}",
                          models.SnapshotPriority.SNAPSHOT, True))
        self.assertEqual(self.marked(), self.n)
        with sqlite3.connect(self.data / "gen-rr" / "sqlite" / "rag.db") as conn:
            self.assertEqual(conn.execute("SELECT DISTINCT embedding_model FROM index_state").fetchall(),
                             [("test-embedder",)])
        # the line kb-refresh-p9's derive_checked parses, as the copy path prints it
        self.assertEqual(self.dense_lines(),
                         [f"dense: {self.n} points in rag_dense_gen-rr, {self.n} copied from rag_dense_gen-a, "
                          "0 embedded"])

    def test_a_resume_copies_nothing_again_and_prints_the_same_line(self):
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 0)
        points = points_of(self.client, "rag_dense_gen-rr")
        self.log.reset_mock()
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot", "--resume"), 0)
        dense = self.report()["dense"]
        self.assertEqual((dense["chunks_indexed_this_run"], dense["reused_this_run"], dense["collection_created"]),
                         (0, 0, False))
        self.assertEqual(len(self.snap.made("create")), 1)                     # one snapshot, in the first run
        self.assertSamePoints(points_of(self.client, "rag_dense_gen-rr"), points)
        self.assertEqual(self.dense_lines(),
                         [f"dense: {self.n} points in rag_dense_gen-rr, {self.n} copied from rag_dense_gen-a, "
                          "0 embedded"])

    def test_the_default_is_the_copy_path_unchanged(self):
        def no_snapshots(config):
            raise AssertionError("the copy path made a snapshot client")
        with mock.patch.object(self.derive.br, "snapshot_client", no_snapshots):
            self.assertEqual(self.run_derive("--to", "gen-rr"), 0)
            self.assertEqual(self.run_derive("--to", "gen-cp", "--dense-clone", "copy"), 0)
        for name in ("gen-rr", "gen-cp"):
            dense = self.report(name)["dense"]
            self.assertNotIn("clone", dense)
            self.assertEqual(sorted(dense), ["chunks_indexed_this_run", "collection", "collection_created",
                                             "collection_owned", "embedded_this_run", "embedding_model",
                                             "embedding_revision", "index", "points", "reuse", "reused_this_run"])
        self.assertEqual(self.snap.calls, [])

    def test_a_resume_in_the_other_mode_is_refused(self):
        self.assertEqual(self.run_derive("--to", "gen-rr"), 0)
        self.assertEqual(self.run_derive("--to", "gen-rr", "--resume", "--dense-clone", "snapshot"), 2)
        self.assertIn("started with --dense-clone copy", logged(self.log))
        self.assertEqual(self.run_derive("--to", "gen-sn", "--dense-clone", "snapshot"), 0)
        self.assertEqual(self.run_derive("--to", "gen-sn", "--resume"), 2)
        self.assertIn("started with --dense-clone snapshot", logged(self.log))
        self.assertEqual(self.run_derive("--to", "gen-sn", "--resume", "--dense-clone", "snapshot"), 0)

    def test_the_default_filter_is_checked_when_the_root_has_opt_in_documents(self):
        self.assertEqual(self.run_derive("--to", "gen-oi", "--dense-clone", "snapshot", "--set",
                                         'retrieval.opt_in_doc_types=["config_guide"]'), 0)
        searches = self.report("gen-oi")["dense"]["clone"]["restored"]["check"]["searches"]
        self.assertEqual(searches, {"unfiltered": {"queries": self.q, "differ": 0},
                                    "default_filter": {"queries": self.q, "differ": 0}})

    def test_another_snapshots_directory(self):
        self.snap.snapshots_dir = "/srv/qdrant/snaps"
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 1)    # not where it is
        self.assertIn("403", self.report()["failed_steps"][0]["error"])
        self.assertNotIn("rag_dense_gen-rr", self.collections())
        self.assertEqual(self.run_derive("--to", "gen-rr", "--resume", "--dense-clone", "snapshot",
                                         "--qdrant-snapshots-dir", "/srv/qdrant/snaps"), 0)
        self.assertTrue(self.snap.made("recover")[-1][2].startswith("file:///srv/qdrant/snaps/rag_dense_gen-a/"))
        self.assertEqual(self.run_derive("--to", "gen-x", "--dense-clone", "snapshot",
                                         "--qdrant-snapshots-dir", "snaps"), 2)                 # not absolute
        self.assertFalse((self.data / "gen-x").exists())

    # --- failures: nothing unchecked is left behind, and --resume starts again

    def assert_nothing_left(self, name="gen-rr"):
        self.assertNotIn(f"rag_dense_{name}", self.collections())
        self.assertEqual(self.snap.snapshots, {})
        self.assertEqual(self.marked(name), 0)
        clone = self.report(name)["dense"]["clone"]
        self.assertEqual(set(clone) - {"mode"}, set())

    def test_a_recovery_that_searches_differently_is_deleted(self):
        self.snap.mangle = nudge
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 1)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("does not search as rag_dense_gen-a does", report["failed_steps"][0]["error"])
        self.assert_nothing_left()
        self.snap.mangle = None
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot", "--resume"), 0)
        self.assertSamePoints(points_of(self.client, "rag_dense_gen-rr"),
                              points_of(self.client, "rag_dense_gen-a"))

    def test_a_recovery_with_a_point_missing_is_deleted(self):
        self.snap.mangle = drop_one
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 1)
        self.assertIn("differs from rag_dense_gen-a in its points", self.report()["failed_steps"][0]["error"])
        self.assert_nothing_left()

    def test_a_recovery_that_fails_half_way_is_deleted(self):
        self.snap.fail_recover = True
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 1)
        self.assertIn("recovery failed half way", self.report()["failed_steps"][0]["error"])
        self.assert_nothing_left()

    def test_a_snapshot_that_cannot_be_deleted_fails_the_step_and_goes_next_run(self):
        self.snap.fail_delete = 1
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 1)
        report = self.report()
        self.assertIn("could not be deleted", report["failed_steps"][0]["error"])
        self.assertNotIn("rag_dense_gen-rr", self.collections())
        pending = report["dense"]["clone"]["pending_snapshot"]
        self.assertEqual(list(self.snap.snapshots), [(pending["collection"], pending["name"])])     # on disk, named
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot", "--resume"), 0)
        self.assertEqual(self.snap.snapshots, {})
        self.assertNotIn("pending_snapshot", self.report()["dense"]["clone"])
        self.assertEqual(len(self.snap.made("create")), 2)

    def test_a_snapshot_request_that_fails_here_leaves_no_snapshot_in_qdrant(self):
        # Qdrant goes on with a request its client gave up on (a timeout, a dropped connection: observed on 1.19.1)
        real, keep = self.snap.create_snapshot, ("rag_dense_gen-a", "rag_dense_gen-a-0-2026-11-01-09-00-00.snapshot")
        self.snap.snapshots[keep] = (None, "x")                     # someone else's, listed before: never deleted

        def made_then_lost(collection_name, wait=True):
            real(collection_name, wait)
            raise QdrantError("timed out")

        def lost(collection_name, wait=True):
            raise QdrantError("timed out")

        with mock.patch.object(self.snap, "create_snapshot", made_then_lost):
            self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 1)
        self.assertEqual(list(self.snap.snapshots), [keep])           # deleted in the same run
        pending = self.report()["dense"]["clone"]["pending_snapshot"]
        self.assertEqual((pending["collection"], pending["name"], pending["before"]),
                         ("rag_dense_gen-a", None, [keep[1]]))         # kept: Qdrant may make it later
        self.assertNotIn("rag_dense_gen-rr", self.collections())
        self.assertEqual(self.marked(), 0)
        with mock.patch.object(self.snap, "create_snapshot", lost):
            self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot", "--resume"), 1)
        self.assertIn("pending_snapshot", self.report()["dense"]["clone"])
        real("rag_dense_gen-a")                                       # ... and Qdrant makes it after the run
        self.assertEqual(len(self.snap.snapshots), 2)
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot", "--resume"), 0)
        self.assertEqual(list(self.snap.snapshots), [keep])           # the late one deleted by the next run
        self.assertNotIn("pending_snapshot", self.report()["dense"]["clone"])
        self.assertIn("Qdrant may still make it", logged(self.br_log))

    def test_the_source_collection_is_never_the_target(self):
        state = {"target": self.data / "gen-rr", "collection": "rag_dense_gen-a", "source_collection": "rag_dense_gen-a"}
        with mock.patch.object(self.derive, "load_dense_config",
                               lambda root: mock.Mock(collection="rag_dense_gen-a")), \
                self.assertRaisesRegex(RuntimeError, "never deleted or recovered into"):
            self.derive.step_dense_snapshot(state, {}, lambda: None)
        self.assertIn("rag_dense_gen-a", self.collections())
        self.assertEqual(self.snap.calls, [])

    def test_a_missing_vector_fails_and_leaves_nothing(self):
        with sqlite3.connect(self.source / "sqlite" / "rag.db") as conn:
            gone = conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id LIMIT 1").fetchone()[0]
        self.client.delete(collection_name="rag_dense_gen-a", points_selector=models.PointIdsList(
            points=[self.script.point_id_for_chunk(gone)]), wait=True)
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 1)
        self.assertIn("no reusable vector", self.report()["failed_steps"][0]["error"])
        self.assert_nothing_left()

    def test_a_stray_point_is_not_cloned(self):
        with sqlite3.connect(self.source / "sqlite" / "rag.db") as conn:
            chunk_id, doc_id = conn.execute("SELECT chunk_id, doc_id FROM chunks LIMIT 1").fetchone()
        self.client.upsert(collection_name="rag_dense_gen-a", wait=True, points=[models.PointStruct(
            id=str(uuid.uuid4()), vector=text_vector("stray"), payload={"chunk_id": chunk_id, "doc_id": doc_id})])
        self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 0)
        dense = self.report()["dense"]
        self.assertEqual((dense["points"], dense["clone"]["points"]["strays_deleted"]), (self.n, 1))
        self.assertEqual(self.client.count(collection_name="rag_dense_gen-rr", exact=True).count, self.n)

    def test_a_source_qdrant_is_still_optimizing_is_refused(self):
        yellow = Yellow(self.client, "rag_dense_gen-a")
        with mock.patch.object(self.derive, "make_qdrant_client", lambda config: yellow):
            self.assertEqual(self.run_derive("--to", "gen-rr", "--dense-clone", "snapshot"), 2)
            self.assertIn("is yellow, not green", logged(self.log))
            self.assertEqual(self.run_derive("--to", "gen-rr"), 0)               # the copy path does not ask
        self.assertFalse(self.snap.calls)


class BuildSnapshotBaseTests(SamePoints, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        trd.ReuseDenseTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.data = Path(d.name) / "rag-data"
        self.client = QdrantClient(location=":memory:")
        self.addCleanup(self.client.close)
        self.snap = SnapshotQdrant(self.client)
        self.log = self.enterContext(mock.patch.object(self.script, "log"))
        self.enterContext(warnings.catch_warnings())
        warnings.filterwarnings("ignore", "Payload indexes have no effect", UserWarning)
        self.enterContext(mock.patch.dict(os.environ))
        for key in [k for k in os.environ if k.startswith("RAG_")]:
            del os.environ[key]
        self.enterContext(mock.patch.object(self.script, "make_qdrant_client",
                                            lambda config: trd.SharedClient(self.client)))
        self.enterContext(mock.patch.object(self.script, "snapshot_client", lambda config: self.snap))
        self.base = self.script.load_dense_config(tbr.REPO / "config" / "dense.json")
        self.source = self.copy("gen-a")
        config = self.script.DenseConfig(**{**self.base.__dict__, "collection": "rag_dense_gen-a", "batch_size": 2,
                                            "embedding_revision": None})
        self.script.index_dense_resumable(self.source / "sqlite" / "rag.db", config, TextEmbedder(), self.client,
                                          sleep=lambda s: None)
        report = json.loads((self.source / "BUILD_REPORT.json").read_text())
        report["generation"] = "gen-a"
        report["dense"] = {"collection": "rag_dense_gen-a", "collection_owned": True}
        (self.source / "BUILD_REPORT.json").write_text(json.dumps(report))
        (self.source / "config" / "dense.json").write_text(json.dumps({"collection": "rag_dense_gen-a"}))

    def copy(self, name):
        root = self.data / name
        shutil.copytree(self.template, root)
        (root / "config" / "dense.json").write_text(json.dumps({"collection": f"rag_dense_{name}"}))
        return root

    def change(self, root):
        """A content round: one document's chunks get a new embedding text (a new title), one chunk is new, one is
        gone, one keeps its text but moves (its payload changes)."""
        conn = sqlite3.connect(root / "sqlite" / "rag.db")
        # the document with the most chunks loses one and moves one; the smallest gets the new title
        docs = [r[0] for r in conn.execute("SELECT doc_id FROM chunks GROUP BY doc_id ORDER BY COUNT(*) DESC, doc_id")]
        big, other, renamed = docs[0], docs[1], docs[-1]
        changed = conn.execute("UPDATE chunks SET embedding_text = 'Document: renamed' || substr(embedding_text, 10) "
                               "WHERE doc_id = ?", (renamed,)).rowcount
        conn.execute("INSERT INTO chunks(chunk_id, chunk_sha256, doc_id, ordinal, heading_path_json, text, "
                     "embedding_text, token_count, char_count) VALUES ('chk_new', 'x', ?, 999, '[]', 'brand new', "
                     "'Document: t\nSection: s\n\nbrand new', 2, 9)", (other,))
        last, moved = [r[0] for r in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ? ORDER BY ordinal",
                                                  (big,))][-2:]
        conn.execute("DELETE FROM chunks WHERE chunk_id = ?", (last,))
        conn.execute("UPDATE chunks SET heading_path_json = '[\"moved\"]' WHERE chunk_id = ?", (moved,))
        conn.commit()
        conn.close()
        return changed

    def step(self, root, base, embedder=None, **kw):
        report = {}
        embedder = embedder or TextEmbedder()
        with mock.patch.object(self.script, "JinaEmbeddingClient", lambda config: embedder):
            self.script.step_dense(report, root, root / "sqlite" / "rag.db", f"rag_dense_{root.name}",
                                   reuse_from="gen-a", dense_base=base, **kw)
        return report, embedder

    def chunks(self, root):
        with sqlite3.connect(root / "sqlite" / "rag.db") as conn:
            return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def test_the_snapshot_base_ends_with_the_upsert_paths_points_writing_only_what_changed(self):
        source_points = points_of(self.client, "rag_dense_gen-a")
        upsert, snapshot = self.copy("gen-b"), self.copy("gen-c")
        changed = self.change(upsert)
        self.assertEqual(self.change(snapshot), changed)
        n = self.chunks(snapshot)
        report_b, embedder_b = self.step(upsert, "upsert")
        with mock.patch.object(self.client, "upsert", wraps=self.client.upsert) as upserts:
            report_c, embedder_c = self.step(snapshot, "snapshot")
        # the same final points as the upsert path: ids, vectors and payloads
        self.assertSamePoints(points_of(self.client, "rag_dense_gen-c"),
                              points_of(self.client, "rag_dense_gen-b"))
        self.assertSamePoints(points_of(self.client, "rag_dense_gen-a"), source_points)     # only read
        self.assertEqual(embedder_c.texts, embedder_b.texts)                  # the same chunks embedded
        dense_b, dense_c = report_b["dense"], report_c["dense"]
        self.assertNotIn("base", dense_b)
        self.assertEqual((dense_c["reuse"], dense_c["points"], dense_c["embedding_model"]),
                         (dense_b["reuse"], dense_b["points"], dense_b["embedding_model"]))
        self.assertEqual(dense_c["reuse"]["embedded"], changed + 1)
        base = dense_c["base"]
        self.assertEqual((base["mode"], base["from_collection"]), ("snapshot", "rag_dense_gen-a"))
        self.assertEqual(base["pruned"], {"points": n, "deleted": changed + 1, "kept": n - changed - 1})
        self.assertEqual((base["kept"], base["rewritten"]), (n - changed - 2, 1))
        self.assertEqual(base["restored"]["check"]["searches"]["unfiltered"]["differ"], 0)
        self.assertEqual(self.snap.snapshots, {})
        # written: the embedded chunks and the moved one, never a kept point
        written = [p.id for c in upserts.call_args_list for p in c.kwargs["points"]]
        self.assertEqual(len(written), changed + 2)
        log = logged(self.log)
        self.assertIn(f"dense: {n} points in rag_dense_gen-c (test-embedder; {n - changed - 1} reused from gen-a, "
                      f"{changed + 1} embedded)", log)
        self.assertIn("final points checked against the upsert path's", log)

    def test_the_upsert_path_is_unchanged(self):
        def no_snapshots(config):
            raise AssertionError("the upsert path made a snapshot client")
        target = self.copy("gen-b")
        with mock.patch.object(self.script, "snapshot_client", no_snapshots):
            report, _ = self.step(target, "upsert")
        self.assertEqual(sorted(report["dense"]), ["chunks_indexed_this_run", "collection", "collection_created",
                                                   "collection_owned", "embedded_this_run", "embedding_model",
                                                   "embedding_revision", "index", "points", "reuse", "reused_this_run"])

    def test_a_stopped_run_resumes_on_its_base(self):
        upsert, snapshot = self.copy("gen-b"), self.copy("gen-c")
        self.change(upsert)
        self.change(snapshot)
        self.step(upsert, "upsert")
        report = {}
        failing = TextEmbedder(fail_first=1)
        with mock.patch.object(self.script, "JinaEmbeddingClient", lambda config: failing), \
                self.assertRaisesRegex(ValueError, "TEI is down"):
            self.script.step_dense(report, snapshot, snapshot / "sqlite" / "rag.db", "rag_dense_gen-c",
                                   reuse_from="gen-a", dense_base="snapshot")
        resumed = json.loads((snapshot / "BUILD_REPORT.json").read_text())       # what a --resume run reads
        self.assertEqual(set(resumed["dense"]["base"]), {"mode", "from_collection", "restored", "pruned"})
        with mock.patch.object(self.script, "JinaEmbeddingClient", lambda config: TextEmbedder()):
            self.script.step_dense(resumed, snapshot, snapshot / "sqlite" / "rag.db", "rag_dense_gen-c",
                                   reuse_from="gen-a", dense_base="snapshot")
        self.assertEqual(len(self.snap.made("create")), 1)                     # the base was made once
        self.assertSamePoints(points_of(self.client, "rag_dense_gen-c"),
                              points_of(self.client, "rag_dense_gen-b"))
        self.assertEqual(resumed["dense"]["points"], self.chunks(snapshot))

    def test_a_base_that_does_not_search_as_its_source_is_deleted(self):
        self.snap.mangle = nudge
        target = self.copy("gen-c")
        with self.assertRaisesRegex(RuntimeError, "does not search as rag_dense_gen-a does"):
            self.step(target, "snapshot")
        self.assertNotIn("rag_dense_gen-c", {c.name for c in self.client.get_collections().collections})
        self.assertEqual(self.snap.snapshots, {})
        base = json.loads((target / "BUILD_REPORT.json").read_text())["dense"]["base"]
        self.assertEqual(base, {"mode": "snapshot"})
        self.snap.mangle = None
        report, embedder = self.step(target, "snapshot")
        self.assertEqual((report["dense"]["base"]["kept"], embedder.calls), (self.chunks(target), 0))

    def test_a_point_the_collection_should_not_hold_fails_the_check(self):
        target = self.copy("gen-c")
        original = self.script.prune_base

        def prune_nothing(client, collection, db_path, reuse, **kw):
            result = original(client, collection, db_path, reuse, **kw)
            client.upsert(collection_name=collection, wait=True, points=[models.PointStruct(
                id=str(uuid.uuid4()), vector=text_vector("extra"), payload={"chunk_id": "x", "doc_id": "y"})])
            return result
        with mock.patch.object(self.script, "prune_base", prune_nothing), \
                self.assertRaisesRegex(RuntimeError, "dense index incomplete"):
            self.step(target, "snapshot")

    # --- preflight

    def main(self, root, *args):
        return self.script.main(["--corpus-ref", "corpus-v1", "--corpus-repo", "/nonexistent", "--root", str(root),
                                 *args])

    def test_refusals(self):
        root = self.data / "gen-c"
        self.assertEqual(self.main(root, "--dense-base", "snapshot"), 2)
        self.assertIn("--dense-base snapshot starts the collection from the --reuse-dense-from", logged(self.log))
        self.assertEqual(self.main(root, "--reuse-dense-from", "gen-a", "--dense-base", "snapshot",
                                   "--qdrant-snapshots-dir", "relative/dir"), 2)
        self.assertIn("is not an absolute path", logged(self.log))
        self.assertFalse(root.exists())
        # a resume in the other mode, once the dense step has started
        for started, other in ((None, "snapshot"), ({"mode": "snapshot"}, "upsert")):
            self.log.reset_mock()
            with self.subTest(started=started):
                shutil.rmtree(root, ignore_errors=True)
                root.mkdir(parents=True)
                dense = {"collection": "rag_dense_gen-c", "collection_owned": True}
                if started:
                    dense["base"] = started
                (root / "BUILD_REPORT.json").write_text(json.dumps({
                    "builder": "scripts/build_root.py", "generation": "gen-c", "status": "incomplete",
                    "dense": dense}))
                self.assertEqual(self.main(root, "--resume", "--reuse-dense-from", "gen-a", "--dense-base", other), 2)
                self.assertIn(f"started with --dense-base {(started or {}).get('mode', 'upsert')}", logged(self.log))

    def test_preflight_refuses_a_source_qdrant_is_still_optimizing(self):
        import argparse
        args = argparse.Namespace(no_dense=False, reuse_dense_from="gen-a", dense_base="snapshot")
        target = self.copy("gen-c")
        with mock.patch.object(self.script, "make_qdrant_client", lambda config: Yellow(self.client,
                                                                                        "rag_dense_gen-a")):
            with self.assertRaisesRegex(self.script.Refused, "is yellow, not green"):
                self.script.check_dense_source(args, target, "rag_dense_gen-c")
            self.script.check_dense_source(argparse.Namespace(no_dense=False, reuse_dense_from="gen-a"), target,
                                           "rag_dense_gen-c")                 # upsert does not ask


class SnapshotHelperTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.script = tbr.load_script()

    def test_snapshot_location(self):
        loc = self.script.snapshot_location
        self.assertEqual(loc("/qdrant/snapshots", "rag_dense_p81r-20261025",
                             "rag_dense_p81r-20261025-89930597-2026-11-02-13-20-18.snapshot"),
                         "file:///qdrant/snapshots/rag_dense_p81r-20261025/"
                         "rag_dense_p81r-20261025-89930597-2026-11-02-13-20-18.snapshot")
        for bad in ("../x.snapshot", "a/b.snapshot", "x.tar", "", None):
            with self.subTest(bad=bad), self.assertRaisesRegex(RuntimeError, "not a file name"):
                loc("/qdrant/snapshots", "c", bad)

    def test_search_filters(self):
        conn = sqlite3.connect(":memory:")
        self.assertEqual(self.script.search_filters(conn, ["powershell"]), {"unfiltered": None})   # no structure
        for table in ("doc_sources(doc_id, doc_type)", "doc_sections(x)", "chunk_spans(x)"):
            conn.execute(f"CREATE TABLE {table}")
        conn.executemany("INSERT INTO doc_sources VALUES (?, ?)", [("d1", "rfc"), ("d2", "powershell"),
                                                                    ("d3", "system")])
        self.assertEqual(self.script.search_filters(conn, []), {"unfiltered": None})
        self.assertEqual(self.script.search_filters(conn, ["config_guide"]), {"unfiltered": None})  # none of them
        filters = self.script.search_filters(conn, ["system", "powershell"])
        self.assertEqual(filters["default_filter"], models.Filter(must_not=[models.FieldCondition(
            key="doc_id", match=models.MatchAny(any=["d2", "d3"]))]))

    def test_a_pending_snapshot_is_deleted_or_forgotten(self):
        client = QdrantClient(location=":memory:")
        self.addCleanup(client.close)
        snap = SnapshotQdrant(client)
        record, writes = {"pending_snapshot": {"collection": "c", "name": "c-1.snapshot"}}, []
        self.assertIsNone(self.script.drop_pending_snapshot(snap, record, lambda: writes.append(1)))
        self.assertEqual((record, snap.made("delete"), len(writes)), ({}, [], 1))        # not listed: forgotten
        snap.snapshots[("c", "c-2.snapshot")] = (None, "x")
        record["pending_snapshot"] = {"collection": "c", "name": "c-2.snapshot"}
        snap.fail_delete = 1
        self.assertIn("503", self.script.drop_pending_snapshot(snap, record, lambda: None))
        self.assertIn("pending_snapshot", record)
        self.assertIsNone(self.script.drop_pending_snapshot(snap, record, lambda: None))
        self.assertEqual((record, snap.snapshots), ({}, {}))

    def test_what_a_failed_snapshot_request_may_have_made(self):
        def snap(name, made):
            return models.SnapshotDescription(name=name, creation_time=made, size=1)
        listed = [snap("a.snapshot", "2026-11-02T14:09:10"),          # listed before the request
                  snap("b.snapshot", "2026-11-02T14:09:14"),          # made by it
                  snap("c.snapshot", "2026-11-02T16:00:00"),          # long after its timeout: someone else's
                  snap("d.snapshot", "2026-11-02T14:08:00"),          # before it
                  snap("e.snapshot", None)]                           # no time: counted
        snapper = mock.Mock(list_snapshots=lambda collection_name: listed)
        pending = {"collection": "c", "name": None, "since": "2026-11-02T14:09:12+00:00", "before": ["a.snapshot"]}
        self.assertEqual(self.script.unnamed_snapshots(snapper, pending), ["b.snapshot", "e.snapshot"])
        with self.assertRaisesRegex(RuntimeError, "no start time"):
            self.script.unnamed_snapshots(snapper, {**pending, "since": None})

    def test_a_collection_is_never_recovered_into_itself(self):
        client, snapper = mock.Mock(), mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "from a snapshot of itself"):
            self.script.restore_from_snapshot(client, snapper, "rag_dense_a", "rag_dense_a", {}, lambda: None,
                                              snapshots_dir="/qdrant/snapshots", chunk_ids=["x"],
                                              filters={"unfiltered": None})
        self.assertEqual((client.mock_calls, snapper.mock_calls), ([], []))


if __name__ == "__main__":
    unittest.main()
