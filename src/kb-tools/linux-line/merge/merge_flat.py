#!/usr/bin/env python3
"""Merge the flat vectors of per-block partial generations into one flat set for the full generation (lever 10:
s3-dense on two cards). Part of the merge step whose last move is build_root.py --reuse-dense-from (the build procedure
step X5); this file only joins and checks, it never embeds or talks to a server.

usage: merge_flat.py --out DIR [--expect-root ROOT] [--json] PART [PART ...]

PART   an unpacked generation export (export_generation.py unpack) or a C3 bundle of one per-block partial
       generation: dense/vectors.f32, dense/points.bin, dense/EXPORT.json and root/sqlite/rag.db
--out  a new folder: dense/vectors.f32 and dense/points.bin (records in point-id byte order, as the export
       writes them), dense/EXPORT.json ({"dim", "points_count"}) and MERGE.json (what went in, hashes, counts)
--expect-root  the full generation's root (the --no-dense root that will take the vectors): its rag.db must
       hold exactly the merged chunk ids, each with the same document id and the same embedding text as in the
       part that supplied its vector (a vector is only valid for the text it was embedded from)

Refused (exit 3, nothing written): dimensions differ; a part mixes embedding models or revisions, or two parts
differ in them; a chunk id in two parts with different vectors (bit-identical duplicates count once); with
--expect-root, any chunk missing, extra, or with another document or embedding text. Exit 0 merged, 2 usage.
Standard library only.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

RECORD = 96
FORMAT = "deep32-flat-merge"


class Refused(Exception):
    pass


def ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)


def read_part(path: Path) -> dict:
    dense = path / "dense"
    for name in ("vectors.f32", "points.bin"):
        if not (dense / name).is_file():
            raise Refused(f"{path}: dense/{name} missing")
    db = path / "root/sqlite/rag.db"
    if not db.is_file():
        raise Refused(f"{path}: root/sqlite/rag.db missing (needed for the embedding texts)")
    points = (dense / "points.bin").read_bytes()
    vectors = (dense / "vectors.f32").read_bytes()
    if len(points) % RECORD or not points:
        raise Refused(f"{path}: points.bin size {len(points)}")
    n = len(points) // RECORD
    if len(vectors) % (4 * n):
        raise Refused(f"{path}: vectors.f32 does not hold {n} rows")
    dim = len(vectors) // (4 * n)
    rows = {}
    for i in range(n):
        rec = points[i * RECORD:(i + 1) * RECORD]
        chunk = "chk_" + rec[16:48].hex()
        if uuid.UUID(bytes=rec[:16]) != uuid.uuid5(uuid.NAMESPACE_URL, "rag-core:dense:v1:" + chunk):
            raise Refused(f"{path}: record {i}: point id is not its chunk's")
        rows[chunk] = (rec, vectors[i * dim * 4:(i + 1) * dim * 4])
    c = ro(db)
    try:
        models = c.execute("SELECT DISTINCT embedding_model, embedding_revision FROM index_state "
                           "WHERE dense_indexed = 1").fetchall()
        texts = dict(c.execute("SELECT chunk_id, embedding_text FROM chunks").fetchall())
        docs = dict(c.execute("SELECT chunk_id, doc_id FROM chunks").fetchall())
    finally:
        c.close()
    if len(models) != 1:
        raise Refused(f"{path}: {len(models)} embedding model/revision pairs in index_state, expected 1")
    missing = [ch for ch in rows if ch not in texts]
    if missing:
        raise Refused(f"{path}: {len(missing)} vectors have no chunk in the part's own rag.db")
    return {"path": path, "dim": dim, "rows": rows, "model": list(models[0]), "texts": texts, "docs": docs,
            "vectors_sha256": hashlib.sha256(vectors).hexdigest(),
            "points_sha256": hashlib.sha256(points).hexdigest()}


def merge(parts: list[Path], out: Path, expect_root: Path | None) -> dict:
    if out.exists():
        raise Refused(f"{out} exists")
    loaded = [read_part(p) for p in parts]
    dims = {p["dim"] for p in loaded}
    if len(dims) != 1:
        raise Refused(f"dimensions differ: {sorted(dims)}")
    models = {tuple(p["model"]) for p in loaded}
    if len(models) != 1:
        raise Refused(f"the parts were embedded by different models or revisions: {sorted(models)}")
    merged, origin, duplicates = {}, {}, 0
    for p in loaded:
        for chunk, (rec, vec) in p["rows"].items():
            if chunk in merged:
                if merged[chunk][1] != vec:
                    raise Refused(f"chunk {chunk} in two parts with different vectors")
                duplicates += 1
                continue
            merged[chunk], origin[chunk] = (rec, vec), p
    if expect_root is not None:
        db = expect_root / "sqlite/rag.db"
        if not db.is_file():
            raise Refused(f"{expect_root}: sqlite/rag.db missing")
        c = ro(db)
        try:
            full = {r[0]: (r[1], r[2]) for r in c.execute("SELECT chunk_id, doc_id, embedding_text FROM chunks")}
        finally:
            c.close()
        if set(full) != set(merged):
            raise Refused(f"the merged ids are not the root's chunks: {len(set(full) - set(merged))} without a "
                          f"vector, {len(set(merged) - set(full))} not in the root")
        for chunk, (doc, text) in full.items():
            p = origin[chunk]
            if p["docs"][chunk] != doc or ("doc_" + merged[chunk][0][48:80].hex()) != doc:
                raise Refused(f"chunk {chunk}: document differs between {p['path']} and the root")
            if p["texts"][chunk] != text:
                raise Refused(f"chunk {chunk}: embedding text differs between {p['path']} and the root")
    order = sorted(merged, key=lambda ch: merged[ch][0][:16])
    points = b"".join(merged[ch][0] for ch in order)
    vectors = b"".join(merged[ch][1] for ch in order)
    tmp = out.with_name(out.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "dense").mkdir(parents=True)
    (tmp / "dense/points.bin").write_bytes(points)
    (tmp / "dense/vectors.f32").write_bytes(vectors)
    dim = dims.pop()
    (tmp / "dense/EXPORT.json").write_text(json.dumps({"dim": dim, "points_count": len(order)}, sort_keys=True)
                                           + "\n", encoding="ascii")
    record = {
        "format": FORMAT, "format_version": 1, "dim": dim, "rows": len(order), "duplicates": duplicates,
        "embedding_model": loaded[0]["model"][0], "embedding_revision": loaded[0]["model"][1],
        "vectors_sha256": hashlib.sha256(vectors).hexdigest(), "points_sha256": hashlib.sha256(points).hexdigest(),
        "expect_root_checked": expect_root is not None,
        "parts": [{"path": str(p["path"]), "rows": len(p["rows"]), "vectors_sha256": p["vectors_sha256"],
                   "points_sha256": p["points_sha256"]} for p in loaded],
    }
    (tmp / "MERGE.json").write_text(json.dumps(record, sort_keys=True, indent=1) + "\n", encoding="ascii")
    tmp.rename(out)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="merge_flat.py", allow_abbrev=False)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect-root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("parts", nargs="+")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        rec = merge([Path(p) for p in a.parts], Path(a.out), Path(a.expect_root) if a.expect_root else None)
    except (Refused, OSError, sqlite3.Error) as exc:
        print(f"merge_flat: REFUSED {exc}", file=sys.stderr)
        return 3
    print(json.dumps(rec, sort_keys=True) if a.json else
          f"merged {rec['rows']} vectors ({rec['dim']}-d) from {len(rec['parts'])} parts into {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
