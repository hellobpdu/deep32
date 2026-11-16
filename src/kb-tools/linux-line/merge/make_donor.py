#!/usr/bin/env python3
"""Make the donor root that build_root.py --reuse-dense-from reads in the two-card merge (lever 10).

build_root.py takes ONE --reuse-dense-from generation: a complete root whose rag.db marks which chunks were
embedded from which text by which model, and whose Qdrant collection holds the vectors (build_root.py
DenseSource, dense_source_settings). Two cards give two partial generations, so this script makes one donor from
the full --no-dense root (which already holds every chunk with its final embedding text) and the merged flat
vectors (merge_flat.py, checked with --expect-root against the same root):

  <donor>/sqlite/rag.db       a copy of the full root's rag.db (SQLite backup API), with index_state marked
                              dense_indexed = 1, the parts' embedding model and revision, for every merged chunk
                              (the same INSERT ... ON CONFLICT statement as rag_core.dense.mark_dense_indexed)
  <donor>/BUILD_REPORT.json   generation <donor name>, status complete, no failed steps, dense.collection
                              rag_dense_<donor name>, the counts
  <donor>/config/dense.json   {"collection": "rag_dense_<donor name>"}

load_flat.py then fills that collection from the merged files, and build_root.py --resume --root <full root>
--reuse-dense-from <donor> takes every vector from it and embeds nothing (the build procedure step X5 runs it with the
embedding address pointed at a closed loopback port, so an attempt to embed fails loudly). The donor is a build
record only: never served, never shipped, deleted after the merge.

usage: make_donor.py --root FULL_ROOT --merged MERGED_DIR --donor DONOR_DIR [--json]
       W2 JSONL joins also require --inputs inputs.json;
       --compat defaults to /srv/d32/s3/compat.json. The repository pin must
       match inputs, and MERGE.json must carry the compat served name.

Refused (exit 3, nothing left behind): the donor exists; the donor name does not match ^[a-z0-9][a-z0-9._-]{0,39}$
or equals the root's; the merged ids are not exactly the root's chunks; MERGE.json was made without
--expect-root. Exit 0 made, 2 usage. Standard library only.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
from pathlib import Path

GENERATION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
MARK_SQL = """
INSERT INTO index_state (chunk_id, dense_indexed, embedding_model, embedding_revision, indexed_at)
VALUES (?, 1, ?, ?, ?)
ON CONFLICT(chunk_id) DO UPDATE SET dense_indexed = 1, embedding_model = excluded.embedding_model,
    embedding_revision = excluded.embedding_revision, indexed_at = excluded.indexed_at
"""
RECORD = 96
DEFAULT_COMPAT = Path("/srv/d32/s3/compat.json")


class Refused(Exception):
    pass


def make(root: Path, merged: Path, donor: Path, at: str = "1970-01-01 00:00:00",
         inputs: Path | None = None, compat: Path = DEFAULT_COMPAT) -> dict:
    name = donor.name
    if donor.exists():
        raise Refused(f"{donor} exists")
    if not GENERATION_RE.fullmatch(name) or name == root.name:
        raise Refused(f"donor name {name!r} is not a generation name, or is the root's own")
    rec = json.loads((merged / "MERGE.json").read_bytes())
    if not rec.get("expect_root_checked"):
        raise Refused("MERGE.json was made without --expect-root: run merge_flat.py with the full root first")
    # The legacy block merge already inherits engine-recorded identities. W2
    # JSONL joins must prove the repository pin and served name before marking.
    if rec.get("producer") == "flat_from_jsonl.py" or inputs is not None:
        if inputs is None:
            raise Refused("W2 donor requires --inputs and --compat for its embedding identity check")
        data = Path(inputs).read_bytes()
        if hashlib.sha256(data).hexdigest() != rec.get("inputs_manifest_sha256"):
            raise Refused("donor inputs manifest hash differs from MERGE.json")
        path = Path(__file__).resolve().parents[1] / "w2/embedding_identity.py"
        spec = importlib.util.spec_from_file_location("donor_embedding_identity", path)
        identity = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(identity)
        identity.check_merge(rec, json.loads(data), compat)
    raw = (merged / "dense/points.bin").read_bytes()
    ids = ["chk_" + raw[i + 16:i + 48].hex() for i in range(0, len(raw), RECORD)]
    # immutable: the root is only read, and a plain read-only open of a WAL database would leave -wal and -shm
    # files beside it (the root is then no longer what its build wrote)
    src = sqlite3.connect(root.joinpath("sqlite/rag.db").resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        chunks = {r[0] for r in src.execute("SELECT chunk_id FROM chunks")}
        if chunks != set(ids):
            raise Refused(f"the merged ids are not this root's chunks ({len(chunks ^ set(ids))} differ)")
        tmp = donor.with_name(donor.name + ".partial")
        if tmp.exists():
            shutil.rmtree(tmp)
        (tmp / "sqlite").mkdir(parents=True)
        (tmp / "config").mkdir()
        dst = sqlite3.connect(tmp / "sqlite/rag.db")
        try:
            src.backup(dst)
            dst.executemany(MARK_SQL, [(c, rec["embedding_model"], rec["embedding_revision"], at) for c in ids])
            dst.commit()
            marked = dst.execute("SELECT COUNT(*) FROM index_state WHERE dense_indexed = 1").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    collection = "rag_dense_" + name
    report = {
        "builder": "make_donor.py (two-card merge, not a build)", "generation": name, "status": "complete",
        "failed_steps": [], "root": str(donor),
        "dense": {"collection": collection, "embedding_model": rec["embedding_model"],
                  "embedding_revision": rec["embedding_revision"], "points": len(ids),
                  "source": "merged flat vectors", "merge_vectors_sha256": rec["vectors_sha256"]},
        "full_root": str(root),
    }
    (tmp / "BUILD_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (tmp / "config/dense.json").write_text(json.dumps({"collection": collection}) + "\n", encoding="utf-8")
    tmp.rename(donor)
    return {"donor": str(donor), "collection": collection, "chunks": len(ids), "marked": marked,
            "embedding_model": rec["embedding_model"], "embedding_revision": rec["embedding_revision"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="make_donor.py", allow_abbrev=False)
    ap.add_argument("--root", required=True)
    ap.add_argument("--merged", required=True)
    ap.add_argument("--donor", required=True)
    ap.add_argument("--inputs", type=Path)
    ap.add_argument("--compat", type=Path, default=DEFAULT_COMPAT)
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        out = make(Path(a.root), Path(a.merged), Path(a.donor), inputs=a.inputs, compat=a.compat)
    except (Refused, OSError, sqlite3.Error, ValueError, KeyError, TypeError, ImportError) as exc:
        print(f"make_donor: REFUSED {exc}", file=sys.stderr)
        return 3
    print(json.dumps(out, sort_keys=True) if a.json else
          f"donor {out['donor']}: {out['marked']} chunks marked, collection {out['collection']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
