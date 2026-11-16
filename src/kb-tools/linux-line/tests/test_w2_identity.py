#!/usr/bin/env python3
"""Synthetic W2 identity and integrity checks; Python 3.12+, standard library.

Run from an installed lane, or set D32_W2_TEST_LANE to a staged lane and
D32_W2_TEST_SOURCE_LANE to the lane containing unchanged helpers. Set
D32_W2_F32 to the directory containing f32_sse.py when it is not installed at
../kb-bundle. On Windows set D32_W2_TEST_BASH to Git Bash explicitly; the
tests never select a Windows bash executable implicitly. Scratch follows the
runner's TMPDIR (and its TMP/TEMP settings).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


LANE = Path(os.environ.get("D32_W2_TEST_LANE", Path(__file__).resolve().parents[1]))
SOURCE = Path(os.environ.get("D32_W2_TEST_SOURCE_LANE", LANE))
F32 = Path(os.environ.get("D32_W2_F32", os.environ.get(
    "D32_W2_TEST_F32", LANE.parent / "kb-bundle")))
SERVED = "jina-embeddings-v5-text-small"
DIM = 1024


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii"))


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_bytes("".join(json.dumps(r, ensure_ascii=True, sort_keys=True) + "\n"
                             for r in rows).encode("ascii"))


def shell_executable() -> str | None:
    explicit = os.environ.get("D32_W2_TEST_BASH")
    return explicit if explicit else (None if os.name == "nt" else shutil.which("bash"))


class W2IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flat = load("identity_test_flat", LANE / "w2/flat_from_jsonl.py")
        cls.identity = load("identity_test_identity", LANE / "w2/embedding_identity.py")
        cls.donor = load("identity_test_donor", LANE / "merge/make_donor.py")
        helper_path = LANE / "w2/embed_inputs.py"
        if not helper_path.is_file():
            helper_path = SOURCE / "w2/embed_inputs.py"
        cls.inputs_helper = load("identity_test_inputs", helper_path)
        cls.helper_path = helper_path
        cls.merge_script = (LANE / "w2/merge-w2.sh").read_text(encoding="ascii")
        cls.model = cls.inputs_helper.MODEL_ID
        cls.revision = cls.inputs_helper.MODEL_REVISION
        if not (F32 / "f32_sse.py").is_file():
            raise RuntimeError("set D32_W2_F32 to the f32_sse.py helper directory")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="w2-identity-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.scratch = Path(self.temp.name)
        # A minimal staged overlay has no unchanged embed_inputs.py beside it.
        # Redirect only that import location; exercise the real helper itself.
        self.location = patch.object(self.flat, "helper", return_value=self.inputs_helper)
        self.location.start()
        self.addCleanup(self.location.stop)
        self.fixture = self.make_fixture(self.scratch / "fixture", 4)

    def make_fixture(self, base: Path, count: int):
        root = base / "testgen"
        (root / "sqlite").mkdir(parents=True)
        chunks, inputs_rows, vectors = [], [], []
        for number in range(count):
            chunk = "chk_" + hashlib.sha256(f"synthetic-chunk-{number}".encode("ascii")).hexdigest()
            doc = "doc_" + hashlib.sha256(f"synthetic-doc-{number // 2}".encode("ascii")).hexdigest()
            text = f" synthetic fixture {number} \\n exact text \u03b1 "
            chunks.append((chunk, doc, number, text))
            inputs_rows.append({"chunk_id": chunk, "embedding_text": text})
            vector = [0.0] * DIM
            vector[number % DIM] = 1.0
            vectors.append({"chunk_id": chunk, "embedding_text_sha256": hashlib.sha256(
                text.encode("utf-8")).hexdigest(), "vector": vector})
        chunks.sort()
        inputs_rows.sort(key=lambda r: r["chunk_id"])
        vectors.sort(key=lambda r: r["chunk_id"])
        with closing(sqlite3.connect(root / "sqlite/rag.db")) as db:
            db.execute("CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT, ordinal INTEGER, "
                       "embedding_text TEXT)")
            db.execute("CREATE TABLE index_state (chunk_id TEXT PRIMARY KEY, dense_indexed INTEGER, "
                       "embedding_model TEXT, embedding_revision TEXT, indexed_at TEXT)")
            db.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?)", chunks)
            # Include existing unindexed rows to exercise the conflict update.
            db.executemany("INSERT INTO index_state VALUES (?, 0, ?, ?, ?)",
                           [(r[0], "prior-name", "prior-revision", "prior-time") for r in chunks])
            db.commit()
        report = {"generation": root.name, "status": "complete", "failed_steps": [],
                  "corpus": {"fingerprint": hashlib.sha256(b"synthetic-corpus").hexdigest()},
                  "dense": {"points": 0, "index": {"points": 0}}}
        write_json(root / "BUILD_REPORT.json", report)
        input_jsonl = base / "inputs.jsonl"
        write_rows(input_jsonl, inputs_rows)
        meta = {"format": "deep32-w2-inputs", "format_version": 1, "generation": root.name,
                "corpus_fingerprint": report["corpus"]["fingerprint"], "rows": count,
                "model_id": self.model, "model_revision": self.revision, "sha256": digest(input_jsonl)}
        inputs = base / "inputs.json"
        write_json(inputs, meta)
        compat = base / "compat.json"
        write_json(compat, {"embedding": {"model_id": self.model, "revision": self.revision,
                                          "served_name": SERVED}})
        vector_file = base / "vectors.jsonl"
        write_rows(vector_file, vectors)
        return SimpleNamespace(base=base, root=root, inputs=inputs, compat=compat, meta=meta,
                               input_jsonl=input_jsonl, input_rows=inputs_rows, vectors=vectors,
                               vector_file=vector_file, out=base / "flat", chunks=chunks)

    def convert(self, fixture=None, **kwargs):
        f = fixture or self.fixture
        return self.flat.convert(f.root, f.inputs, [f.vector_file], f.out,
                                 f32_dir=F32, compat=f.compat, **kwargs)

    def verify(self, fixture=None):
        f = fixture or self.fixture
        return self.flat.verify_flat(f.root, f.inputs, f.out, f32_dir=F32, compat=f.compat)

    def make_donor(self, path: Path, fixture=None):
        f = fixture or self.fixture
        return self.donor.make(f.root, f.out, path, inputs=f.inputs, compat=f.compat)

    def no_output(self, path: Path):
        self.assertFalse(path.exists(), str(path))
        self.assertFalse(path.with_name(path.name + ".partial").exists(), str(path))

    def rewrite_merge(self, record):
        write_json(self.fixture.out / "MERGE.json", record)

    def heredoc(self, marker: str) -> str:
        tail = self.merge_script[self.merge_script.index(marker):]
        return tail.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0] + "\n"

    def run_python(self, source: str, *args: Path | str):
        return subprocess.run([sys.executable, "-I", "-B", "-", *map(str, args)], input=source,
                              text=True, capture_output=True, timeout=60, check=False)

    def test_served_name_and_repository_model_are_written_separately(self):
        f = self.fixture
        before = digest(f.root / "sqlite/rag.db")
        record = self.convert()
        self.assertNotEqual(self.model, SERVED)
        self.assertEqual(record["model_id"], self.model)
        self.assertEqual(record["embedding_model"], SERVED)
        self.assertEqual(record["embedding_revision"], self.revision)
        self.assertEqual(json.loads((f.out / "MERGE.json").read_bytes()), record)
        self.assertEqual(self.verify()[3], record)
        self.assertEqual(digest(f.root / "sqlite/rag.db"), before)
        self.assertEqual(json.loads(f.inputs.read_bytes()), f.meta)

    def test_every_donor_state_row_and_report_use_served_name(self):
        f = self.fixture
        self.convert()
        before = digest(f.root / "sqlite/rag.db")
        donor = f.base / "donor"
        result = self.make_donor(donor)
        with closing(sqlite3.connect(donor / "sqlite/rag.db")) as db:
            rows = db.execute("SELECT chunk_id, dense_indexed, embedding_model, embedding_revision "
                              "FROM index_state ORDER BY chunk_id").fetchall()
        self.assertEqual(rows, [(r[0], 1, SERVED, self.revision) for r in f.chunks])
        self.assertEqual(result["marked"], len(f.chunks))
        self.assertEqual(result["embedding_model"], SERVED)
        report = json.loads((donor / "BUILD_REPORT.json").read_bytes())
        self.assertEqual(report["dense"]["embedding_model"], SERVED)
        self.assertEqual(report["dense"]["embedding_revision"], self.revision)
        self.assertEqual(digest(f.root / "sqlite/rag.db"), before)
        self.assertFalse((f.root / "sqlite/rag.db-wal").exists())
        self.assertFalse((f.root / "sqlite/rag.db-shm").exists())

    def test_compat_pin_mismatches_refuse_before_conversion_or_donor_write(self):
        f = self.fixture
        self.convert()
        # Preserve one valid merge for donor checks, but use a fresh output for
        # conversion. A pin error must precede even the float32 helper import.
        before = digest(f.root / "sqlite/rag.db")
        for field in ("model_id", "revision"):
            with self.subTest(field=field):
                compat = {"embedding": {"model_id": self.model, "revision": self.revision,
                                         "served_name": SERVED}}
                compat["embedding"][field] = "different-pin"
                write_json(f.compat, compat)
                out, donor = f.base / ("bad-" + field), f.base / ("donor-" + field)
                with patch.object(self.flat, "f32_helper", side_effect=AssertionError("too late")):
                    with self.assertRaisesRegex(ValueError, "differs from inputs manifest"):
                        self.flat.convert(f.root, f.inputs, [f.vector_file], out,
                                          f32_dir=F32, compat=f.compat)
                with self.assertRaisesRegex(ValueError, "differs from inputs manifest"):
                    self.make_donor(donor)
                self.no_output(out)
                self.no_output(donor)
                self.assertEqual(digest(f.root / "sqlite/rag.db"), before)

    def test_empty_malformed_or_missing_compat_identity_refuses(self):
        f = self.fixture
        good = {"embedding": {"model_id": self.model, "revision": self.revision, "served_name": SERVED}}
        documents = [b"", b"{", b"[]", b"null", b"{}", b'{"embedding": []}']
        for field in ("model_id", "revision", "served_name"):
            for bad in (None, "", "   ", 0):
                value = copy.deepcopy(good)
                value["embedding"][field] = bad
                documents.append(json.dumps(value).encode("ascii"))
        for number, document in enumerate(documents):
            with self.subTest(case=number):
                f.compat.write_bytes(document)
                with self.assertRaises(ValueError):
                    self.convert()
                self.no_output(f.out)
        f.compat.unlink()
        with self.assertRaises(OSError):
            self.convert()
        self.no_output(f.out)

    def test_old_model_id_merge_is_refused_by_verifier_and_donor(self):
        record = self.convert()
        record["embedding_model"] = self.model
        self.rewrite_merge(record)
        with self.assertRaisesRegex(ValueError, "embedding_model"):
            self.verify()
        donor = self.fixture.base / "old-name-donor"
        with self.assertRaisesRegex(ValueError, "embedding_model"):
            self.make_donor(donor)
        self.no_output(donor)

    def test_merge_repository_pin_and_revision_remain_required(self):
        good = self.convert()
        for field in ("model_id", "embedding_revision"):
            for action in ("missing", "changed"):
                with self.subTest(field=field, action=action):
                    bad = copy.deepcopy(good)
                    if action == "missing":
                        bad.pop(field)
                    else:
                        bad[field] = "different-pin"
                    self.rewrite_merge(bad)
                    with self.assertRaisesRegex(ValueError, field):
                        self.verify()
                    donor = self.fixture.base / f"donor-{field}-{action}"
                    with self.assertRaisesRegex(ValueError, field):
                        self.make_donor(donor)
                    self.no_output(donor)

    def test_donor_requires_w2_inputs_and_their_exact_hash(self):
        f = self.fixture
        self.convert()
        donor = f.base / "donor"
        with self.assertRaisesRegex(self.donor.Refused, "requires --inputs"):
            self.donor.make(f.root, f.out, donor, compat=f.compat)
        self.no_output(donor)
        f.inputs.write_bytes(f.inputs.read_bytes() + b" ")
        with self.assertRaisesRegex(self.donor.Refused, "hash differs"):
            self.make_donor(donor)
        self.no_output(donor)

    def test_explicit_vector_identity_accepts_served_name(self):
        f = self.fixture
        for row in f.vectors:
            row.update(model_id=self.model, model_revision=self.revision,
                       embedding_model=SERVED, embedding_revision=self.revision)
        write_rows(f.vector_file, f.vectors)
        self.assertEqual(self.convert()["embedding_model"], SERVED)
        self.verify()

    def test_explicit_vector_identity_refuses_old_name_and_wrong_pins(self):
        f = self.fixture
        for field, bad in (("embedding_model", self.model), ("model_id", SERVED),
                           ("model_revision", "wrong"), ("embedding_revision", "wrong")):
            with self.subTest(field=field):
                vectors = copy.deepcopy(f.vectors)
                vectors[0][field] = bad
                write_rows(f.vector_file, vectors)
                with self.assertRaisesRegex(ValueError, field):
                    self.convert()
                self.no_output(f.out)

    def test_legacy_generic_donor_remains_supported_without_w2_compat(self):
        f = self.fixture
        record = self.convert()
        record.update(producer="merge_flat.py", embedding_model="legacy-engine-name",
                      embedding_revision="legacy-engine-revision")
        record.pop("model_id")
        self.rewrite_merge(record)
        f.compat.unlink()
        donor = f.base / "generic-donor"
        result = self.donor.make(f.root, f.out, donor)
        self.assertEqual(result["embedding_model"], "legacy-engine-name")
        with closing(sqlite3.connect(donor / "sqlite/rag.db")) as db:
            self.assertEqual(db.execute("SELECT DISTINCT embedding_model, embedding_revision "
                                        "FROM index_state").fetchall(),
                             [("legacy-engine-name", "legacy-engine-revision")])

    def test_vector_integrity_failures_write_no_flat_output(self):
        f = self.fixture
        cases = (("text hash", "embedding_text_sha256", "0" * 64),
                 ("dimension", "vector", [1.0] + [0.0] * (DIM - 2)),
                 ("norm", "vector", [2.0] + [0.0] * (DIM - 1)),
                 ("non-number", "vector", [True] + [0.0] * (DIM - 1)),
                 ("non-finite", "vector", [float("inf")] + [0.0] * (DIM - 1)))
        for label, field, value in cases:
            with self.subTest(case=label):
                vectors = copy.deepcopy(f.vectors)
                vectors[0][field] = value
                write_rows(f.vector_file, vectors)
                with self.assertRaises(ValueError):
                    self.convert()
                self.no_output(f.out)

    def test_missing_hash_still_requires_exact_legacy_inputs(self):
        f = self.fixture
        for row in f.vectors:
            row.pop("embedding_text_sha256")
        write_rows(f.vector_file, f.vectors)
        with self.assertRaisesRegex(ValueError, "legacy inputs required"):
            self.convert()
        self.no_output(f.out)
        record = self.convert(legacy_inputs=f.input_jsonl)
        self.assertEqual(record["legacy_hash_rows"], len(f.chunks))
        self.verify()

    def test_duplicate_vector_rows_and_incomplete_coverage_refuse(self):
        f = self.fixture
        write_rows(f.vector_file, f.vectors + f.vectors[:1])
        with self.assertRaisesRegex(ValueError, "present twice"):
            self.convert()
        self.no_output(f.out)
        write_rows(f.vector_file, f.vectors[:-1])
        with self.assertRaisesRegex(ValueError, "missing a vector"):
            self.convert()
        self.no_output(f.out)

    def test_subset_is_never_validated_or_used_as_a_donor(self):
        f = self.fixture
        write_rows(f.vector_file, f.vectors[:-1])
        record = self.convert(subset=True)
        self.assertTrue(record["subset"])
        self.assertFalse(record["expect_root_checked"])
        with self.assertRaisesRegex(ValueError, "subset"):
            self.verify()
        donor = f.base / "subset-donor"
        with self.assertRaisesRegex(self.donor.Refused, "without --expect-root"):
            self.make_donor(donor)
        self.no_output(donor)

    def test_flat_file_hashes_are_still_checked(self):
        f = self.fixture
        self.convert()
        for name in ("points.bin", "vectors.f32"):
            with self.subTest(file=name):
                path = f.out / "dense" / name
                good = path.read_bytes()
                path.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
                with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
                    self.verify()
                path.write_bytes(good)
        self.verify()

    def build_report(self, rows: int):
        return {"status": "complete", "failed_steps": [],
                "dense": {"points": rows, "index": {"points": rows},
                          "embedding_model": SERVED, "embedding_revision": self.revision,
                          "reuse": {"from_generation": "donor", "reused": rows, "embedded": 0}}}

    def run_build_check(self, report, record=None):
        f = self.fixture
        if record is not None:
            self.rewrite_merge(record)
        report_path, out = f.base / "joined-report.json", f.base / "build-check.json"
        write_json(report_path, report)
        out.unlink(missing_ok=True)
        source = self.heredoc('"$PY" -I -B - "$ROOT/BUILD_REPORT.json"')
        result = self.run_python(source, report_path, f.out / "MERGE.json", "donor", out,
                                 f.inputs, f.compat, LANE / "w2/embedding_identity.py")
        return result, out

    def test_build_check_accepts_served_name_and_keeps_repository_pin(self):
        record = self.convert()
        result, out = self.run_build_check(self.build_report(record["rows"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        checked = json.loads(out.read_bytes())
        self.assertEqual(checked["model_id"], self.model)
        self.assertEqual(checked["embedding_model"], SERVED)
        self.assertEqual(checked["embedding_revision"], self.revision)
        self.assertEqual(checked["reuse"]["embedded"], 0)

    def test_build_check_refuses_old_merge_form_or_incompatible_report(self):
        good = self.convert()
        report = self.build_report(good["rows"])
        old = copy.deepcopy(good)
        old["embedding_model"] = self.model
        result, out = self.run_build_check(report, old)
        self.assertNotEqual(result.returncode, 0)
        self.no_output(out)
        self.rewrite_merge(good)
        paths = (("status",), ("failed_steps",), ("dense", "points"),
                 ("dense", "index", "points"), ("dense", "reuse", "from_generation"),
                 ("dense", "reuse", "reused"), ("dense", "reuse", "embedded"),
                 ("dense", "embedding_model"), ("dense", "embedding_revision"))
        values = ("incomplete", ["synthetic-failure"], 3, 3, "wrong-donor", 3, 1,
                  self.model, "wrong-revision")
        for path, value in zip(paths, values):
            with self.subTest(field=".".join(path)):
                bad = copy.deepcopy(report)
                target = bad
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result, out = self.run_build_check(bad)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.no_output(out)

    def test_build_check_also_refuses_compat_pin_mismatch(self):
        record = self.convert()
        write_json(self.fixture.compat, {"embedding": {"model_id": self.model,
                   "served_name": SERVED, "revision": "wrong-revision"}})
        result, out = self.run_build_check(self.build_report(record["rows"]))
        self.assertNotEqual(result.returncode, 0)
        self.no_output(out)

    def test_live_work_mapping_accepts_only_a_child_of_the_mounted_tree(self):
        base = self.scratch / "mounted"
        base.mkdir()
        source = self.heredoc('CONTAINER_WORK=$("$PY" -I -B -')
        result = self.run_python(source, base, base / "fresh/nested")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "/srv/d32/s3/fresh/nested")
        for work in (base, self.scratch / "outside", base / "../outside"):
            with self.subTest(path=str(work)):
                result = self.run_python(source, base, work)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("REFUSE live work folder", result.stderr)

    def test_live_work_mapping_refuses_symlink_escape(self):
        base, outside = self.scratch / "mounted", self.scratch / "outside"
        base.mkdir()
        outside.mkdir()
        link = base / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("the runner cannot create directory symlinks")
        source = self.heredoc('CONTAINER_WORK=$("$PY" -I -B -')
        result = self.run_python(source, base, link / "new-work")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REFUSE live work folder", result.stderr)

    def test_shell_syntax_and_after_w2_invalid_arguments(self):
        bash = shell_executable()
        if not bash:
            self.skipTest("set D32_W2_TEST_BASH to Git Bash on Windows")
        scripts = (LANE / "w2/merge-w2.sh", LANE / "s3-post/r1-after-w2.sh")
        for script in scripts:
            with self.subTest(script=script.name):
                result = subprocess.run([bash, "-n", script.as_posix()], capture_output=True,
                                        text=True, timeout=60, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
        for args in (("invalid/generation",), ("UpperCase",), ("a" * 35,), ("one", "two")):
            with self.subTest(args=args):
                # These cases are refused before the wrapper sources build environment settings.
                result = subprocess.run([bash, scripts[1].as_posix(), *args], capture_output=True,
                                        text=True, timeout=60, check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertRegex(result.stderr, "REFUSE invalid W2 generation|usage:")

    def test_dry_run_checks_500_cross_rows_without_donor_or_services(self):
        bash = shell_executable()
        if not bash:
            self.skipTest("set D32_W2_TEST_BASH to Git Bash on Windows")
        f = self.make_fixture(self.scratch / "dry-fixture", 502)
        friends, home = f.input_rows[:-1], f.input_rows[-1:]
        friend_ids = {row["chunk_id"] for row in friends}
        friend_vectors = [row for row in f.vectors if row["chunk_id"] in friend_ids]
        home_vectors = [row for row in f.vectors if row["chunk_id"] not in friend_ids]
        selected = set(sorted(friend_ids, key=lambda ch: hashlib.sha256(
            ("deep32-w2-cross/1:" + ch).encode("ascii")).digest())[:500])
        friend, fv, hv, cross, split = (f.base / name for name in
            ("friend.jsonl", "friend-vectors.jsonl", "home-vectors.jsonl", "cross.jsonl", "split.json"))
        write_rows(friend, friends)
        write_rows(fv, friend_vectors)
        write_rows(hv, home_vectors)
        write_rows(cross, [row for row in friend_vectors if row["chunk_id"] in selected])
        record = {"format": "deep32-w2-split", "format_version": 1,
                  "generation": f.meta["generation"], "corpus_fingerprint": f.meta["corpus_fingerprint"],
                  "model_id": self.model, "model_revision": self.revision,
                  "inputs_sha256": digest(f.input_jsonl), "inputs_manifest_sha256": digest(f.inputs),
                  "input_rows": 502, "friend": {"rows": len(friends), "sha256": digest(friend)},
                  "home": {"rows": len(home)}, "cross": {"rows": 500}}
        write_json(split, record)
        self.flat.convert(f.root, f.inputs, [fv, hv], f.out, f32_dir=F32, compat=f.compat)
        # The transient lane includes only code required by the dry-run branch.
        lane = self.scratch / "dry-lane"
        (lane / "w2").mkdir(parents=True)
        for name in ("flat_from_jsonl.py", "embedding_identity.py", "merge-w2.sh"):
            shutil.copyfile(LANE / "w2" / name, lane / "w2" / name)
        shutil.copyfile(self.helper_path, lane / "w2/embed_inputs.py")
        compare = LANE / "compare-vectors.py"
        if not compare.is_file():
            compare = SOURCE / "compare-vectors.py"
        shutil.copyfile(compare, lane / "compare-vectors.py")
        work = f.base / "dry-work"
        before = digest(f.root / "sqlite/rag.db")
        result = subprocess.run([bash, (lane / "w2/merge-w2.sh").as_posix(), "--dry-run",
                                 "--root", f.root.as_posix(), "--work", work.as_posix(),
                                 "--python", Path(sys.executable).as_posix(), "--compat", f.compat.as_posix(),
                                 "--f32-sse", F32.as_posix(), f.root.name, f.out.as_posix(),
                                 f.inputs.as_posix(), split.as_posix(), friend.as_posix(), fv.as_posix(),
                                 cross.as_posix()], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads((work / "PLAN.json").read_bytes())
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["network_calls"], 0)
        self.assertEqual(plan["cross_rows"], 500)
        self.assertEqual(json.loads((work / "cross-device.json").read_bytes())["verdict"], "identical")
        checked = [json.loads(line) for line in (work / "cross-checked.jsonl").read_text().splitlines()]
        self.assertEqual({row["chunk_id"] for row in checked}, selected)
        self.assertEqual(digest(f.root / "sqlite/rag.db"), before)
        self.no_output(f.base / (f.root.name + "-donor"))
        self.assertFalse((work / "donor.json").exists())
        self.assertFalse((work / "build-check.json").exists())


if __name__ == "__main__":
    unittest.main()
