#!/usr/bin/env python3
"""Join W2 JSONL vectors into the donor's flat form, without a server.

Write: --root ROOT --inputs inputs.json --out FLAT vectors.jsonl [...]
       [--f32-sse ../kb-bundle] [--compat /srv/d32/s3/compat.json]
Legacy rows without a text hash also need --legacy-inputs inputs.jsonl; its
SHA-256 must be the inputs manifest's hash and its exact texts must match ROOT.
--allow-subset is for fixture inspection only: its result cannot make a donor.
Verify: --root ROOT --inputs inputs.json --verify-flat FLAT
Cross: add --split-manifest split.json --friend-inputs friend.jsonl
       --friend-vectors friend/vectors.jsonl --cross-vectors cross/vectors.jsonl
       --cross-out checked-cross.jsonl (exactly the deterministic 500 rows).

Exit 0 checked, 2 usage, 3 refused. Python 3.12+, standard library only.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import json
import math
import re
import shutil
import sqlite3
import struct
import uuid
from pathlib import Path

DIM = 1024
RECORD = struct.Struct("<16s32s32sB15x")
CROSS_SEED = "deep32-w2-cross/1"
CHUNK = re.compile(r"chk_[0-9a-f]{64}\Z")
DOC = re.compile(r"doc_[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
DEFAULT_F32 = Path(__file__).resolve().parents[2] / "kb-bundle"
DEFAULT_COMPAT = Path("/srv/d32/s3/compat.json")


class Refused(ValueError):
    pass


def helper():
    spec = importlib.util.spec_from_file_location("w2_embed_inputs", Path(__file__).with_name("embed_inputs.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def identity_helper():
    spec = importlib.util.spec_from_file_location("w2_embedding_identity", Path(__file__).with_name("embedding_identity.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def f32_helper(directory: Path | None = None):
    path = (directory if directory is not None else DEFAULT_F32) / "f32_sse.py"
    spec = importlib.util.spec_from_file_location("w2_f32_sse", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, sha(path)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=1) + "\n").encode("ascii")


def objects(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for no, line in enumerate(f, 1):
            if not line.strip():
                raise Refused(f"{path.name}:{no}: blank line")
            try:
                obj = json.loads(line)
            except ValueError:
                raise Refused(f"{path.name}:{no}: invalid JSON") from None
            if not isinstance(obj, dict):
                raise Refused(f"{path.name}:{no}: expected an object")
            yield no, obj


def root_context(root: Path, inputs: Path, compat: Path = DEFAULT_COMPAT):
    mod = helper()
    rows = mod.read_root(root)
    meta = mod.root_metadata(root)
    expected = json.loads(inputs.read_bytes())
    if not rows:
        raise Refused("root has no chunks")
    for ch, row in rows.items():
        if not CHUNK.fullmatch(ch) or not DOC.fullmatch(row["doc_id"]):
            raise Refused("root has an invalid chunk or document id")
        if not isinstance(row["embedding_text"], str):
            raise Refused("root has a non-text embedding_text")
    for key in ("generation", "corpus_fingerprint"):
        if not expected.get(key) or expected[key] != meta[key]:
            raise Refused(f"inputs manifest {key} does not match root")
    if type(expected.get("rows")) is not int or expected["rows"] != len(rows):
        raise Refused("inputs manifest row count does not match root")
    for key in ("model_id", "model_revision"):
        if not isinstance(expected.get(key), str) or not expected[key]:
            raise Refused(f"inputs manifest {key} missing")
        if meta.get(key) and expected[key] != meta[key]:
            raise Refused(f"inputs manifest {key} does not match root")
    embedding = identity_helper().from_inputs(expected, compat)
    expected["embedding_model"] = embedding["served_name"]
    signature = hashlib.sha256()
    for ch in sorted(rows):
        signature.update(canonical([ch, rows[ch]["doc_id"], mod.embedding_text_sha256(rows[ch]["embedding_text"])]))
    return rows, expected, signature.hexdigest(), mod


def input_rows(path: Path, root: dict, mod) -> dict:
    rows = {}
    for no, obj in objects(path):
        ch = obj.get("chunk_id")
        if not isinstance(ch, str) or ch not in root:
            raise Refused(f"{path.name}:{no}: input chunk not in root")
        if ch in rows:
            raise Refused(f"{path.name}:{no}: duplicate input chunk")
        text = obj.get("embedding_text")
        if text != root[ch]["embedding_text"]:
            raise Refused(f"{path.name}:{no}: input embedding text differs from root")
        rows[ch] = mod.embedding_text_sha256(text)
    if not rows:
        raise Refused(f"{path.name}: no input rows")
    return rows


def legacy_hashes(path: Path | None, root: dict, meta: dict, mod) -> dict:
    if path is None:
        return {}
    if sha(path) != meta.get("sha256"):
        raise Refused("legacy inputs JSONL hash differs from inputs manifest")
    rows = input_rows(path, root, mod)
    if set(rows) != set(root):
        raise Refused("legacy inputs JSONL does not cover root exactly")
    return rows


def vector_bytes(vector, label: str) -> tuple[bytes, float]:
    if not isinstance(vector, list) or len(vector) != DIM:
        raise Refused(f"{label}: dimension != {DIM}")
    if any(type(v) not in (int, float) for v in vector):
        raise Refused(f"{label}: vector has a non-number")
    try:
        finite = all(math.isfinite(v) for v in vector)
    except OverflowError:
        finite = False
    if not finite:
        raise Refused(f"{label}: vector has a non-finite or oversized value")
    try:
        raw = struct.pack("<" + "f" * DIM, *vector)
    except (OverflowError, struct.error):
        raise Refused(f"{label}: value does not fit finite float32") from None
    vals = struct.unpack("<" + "f" * DIM, raw)
    if any(not math.isfinite(v) for v in vals):
        raise Refused(f"{label}: value does not fit finite float32")
    norm = math.sqrt(math.fsum(v * v for v in vals))
    if not 0.999 <= norm <= 1.001:
        raise Refused(f"{label}: norm {norm:.9g} outside 0.999..1.001")
    return raw, norm


def read_vectors(paths: list[Path], root: dict, meta: dict, mod, legacy: dict, f32):
    rows, sources, norms, legacy_count, changed = {}, [], [], 0, 0
    for path in paths:
        count = 0
        for no, obj in objects(path):
            label = f"{path.name}:{no}"
            ch = obj.get("chunk_id")
            if not isinstance(ch, str) or ch not in root:
                raise Refused(f"{label}: chunk not in root")
            if ch in rows:
                raise Refused(f"{label}: chunk present twice across vector sets")
            digest = obj.get("embedding_text_sha256")
            want = mod.embedding_text_sha256(root[ch]["embedding_text"])
            if digest is None:
                if legacy.get(ch) != want:
                    raise Refused(f"{label}: embedding_text_sha256 missing; verified legacy inputs required")
                legacy_count += 1
            elif not isinstance(digest, str) or not SHA.fullmatch(digest) or digest != want:
                raise Refused(f"{label}: embedding text hash differs from root")
            for field, expected in (("model_id", meta["model_id"]), ("model_revision", meta["model_revision"]),
                                    ("embedding_model", meta["embedding_model"]),
                                    ("embedding_revision", meta["model_revision"])):
                if field in obj and obj[field] != expected:
                    raise Refused(f"{label}: {field} differs from verified embedding identity")
            raw, norm = vector_bytes(obj.get("vector"), label)
            # Match cpu_sample.embed and Qdrant's stored SSE representation.
            # Keep raw input hashes and raw norm checks; never alter the sets.
            values = struct.unpack("<" + "f" * DIM, raw)
            processed = struct.pack("<" + "f" * DIM, *f32.cosine_preprocess(values))
            changed += processed != raw
            rows[ch] = processed
            norms.append(norm)
            count += 1
        if not count:
            raise Refused(f"{path.name}: no vectors")
        sources.append({"name": path.name, "rows": count, "sha256": sha(path)})
    return rows, sources, norms, legacy_count, changed


def convert(root: Path, inputs: Path, paths: list[Path], out: Path, subset=False, legacy_inputs=None,
            f32_dir: Path | None = None, compat: Path = DEFAULT_COMPAT) -> dict:
    if out.exists() or out.with_name(out.name + ".partial").exists():
        raise Refused("output or partial output exists")
    full, meta, signature, mod = root_context(root, inputs, compat)
    if mod.dense_present(root):
        raise Refused("root already has dense vectors; conversion requires the no-dense root")
    f32, f32_sha = f32_helper(f32_dir)
    legacy = legacy_hashes(legacy_inputs, full, meta, mod)
    rows, sources, norms, legacy_count, changed = read_vectors(paths, full, meta, mod, legacy, f32)
    missing = len(set(full) - set(rows))
    if missing and not subset:
        raise Refused(f"{missing} root chunks missing a vector")
    order = sorted(rows, key=lambda ch: uuid.uuid5(uuid.NAMESPACE_URL, "rag-core:dense:v1:" + ch).bytes)
    tmp = out.with_name(out.name + ".partial")
    try:
        (tmp / "dense").mkdir(parents=True)
        with (tmp / "dense/points.bin").open("wb") as pts, (tmp / "dense/vectors.f32").open("wb") as vec:
            for ch in order:
                pid = uuid.uuid5(uuid.NAMESPACE_URL, "rag-core:dense:v1:" + ch)
                pts.write(RECORD.pack(pid.bytes, bytes.fromhex(ch[4:]), bytes.fromhex(full[ch]["doc_id"][4:]), 63))
                vec.write(rows[ch])
        export = {"dim": DIM, "points_count": len(rows), "dtype": "float32", "byte_order": "little"}
        (tmp / "dense/EXPORT.json").write_bytes(canonical(export))
        record = {
            "format": "deep32-flat-merge", "format_version": 1, "producer": "flat_from_jsonl.py",
            "dim": DIM, "rows": len(rows), "root_rows": len(full), "missing": missing, "duplicates": 0,
            "generation": meta["generation"], "corpus_fingerprint": meta["corpus_fingerprint"],
            "model_id": meta["model_id"],
            "embedding_model": meta["embedding_model"], "embedding_revision": meta["model_revision"],
            "expect_root_checked": not subset and not missing, "subset": subset,
            "root_content_sha256": signature, "inputs_manifest_sha256": sha(inputs),
            "embedding_text_hash": "sha256:utf-8:exact", "legacy_hash_rows": legacy_count,
            "legacy_inputs_sha256": sha(legacy_inputs) if legacy_inputs else None,
            "norm_min": min(norms), "norm_max": max(norms), "parts": sources,
            "normalisation": {"method": "f32_sse.cosine_preprocess", "helper_sha256": f32_sha,
                              "changed_rows": changed, "raw_sets_unchanged": True},
            "points_sha256": sha(tmp / "dense/points.bin"), "vectors_sha256": sha(tmp / "dense/vectors.f32"),
        }
        (tmp / "MERGE.json").write_bytes(canonical(record))
        tmp.rename(out)
    except BaseException:
        if tmp.exists():
            shutil.rmtree(tmp)
        raise
    return record


def verify_flat(root: Path, inputs: Path, merged: Path, f32_dir: Path | None = None,
                compat: Path = DEFAULT_COMPAT):
    full, meta, signature, mod = root_context(root, inputs, compat)
    rec = json.loads((merged / "MERGE.json").read_bytes())
    identity_helper().check_merge(rec, meta, compat)
    f32, f32_sha = f32_helper(f32_dir)
    if ((rec.get("normalisation") or {}).get("helper_sha256") != f32_sha
            or (rec.get("normalisation") or {}).get("method") != "f32_sse.cosine_preprocess"):
        raise Refused("flat normalisation helper differs from the recorded helper")
    if rec.get("expect_root_checked") is not True or rec.get("subset") is not False:
        raise Refused("flat set is a subset or was not checked against a full root")
    for key, want in (("root_content_sha256", signature), ("inputs_manifest_sha256", sha(inputs)),
                      ("model_id", meta["model_id"]), ("embedding_model", meta["embedding_model"]),
                      ("embedding_revision", meta["model_revision"]),
                      ("rows", len(full)), ("dim", DIM)):
        if rec.get(key) != want:
            raise Refused(f"flat manifest {key} differs from root or inputs")
    for name, key in (("points.bin", "points_sha256"), ("vectors.f32", "vectors_sha256")):
        if sha(merged / "dense" / name) != rec.get(key):
            raise Refused(f"flat {name} SHA-256 differs from manifest")
    raw = (merged / "dense/points.bin").read_bytes()
    if len(raw) != len(full) * RECORD.size:
        raise Refused("flat points size differs from root")
    previous, seen = b"", set()
    with (merged / "dense/vectors.f32").open("rb") as vectors:
        for off in range(0, len(raw), RECORD.size):
            pid, chraw, docraw, flags = RECORD.unpack(raw[off:off + RECORD.size])
            ch = "chk_" + chraw.hex()
            if ch not in full or ch in seen or pid <= previous:
                raise Refused("flat ids foreign, duplicated or unsorted")
            if (pid != uuid.uuid5(uuid.NAMESPACE_URL, "rag-core:dense:v1:" + ch).bytes
                    or docraw != bytes.fromhex(full[ch]["doc_id"][4:]) or flags != 63
                    or any(raw[off + 81:off + 96])):
                raise Refused("flat point record differs from root")
            previous = pid
            seen.add(ch)
            data = vectors.read(DIM * 4)
            if len(data) != DIM * 4:
                raise Refused("flat vector file truncated")
            vector_bytes(list(struct.unpack("<" + "f" * DIM, data)), "flat vector")
            if struct.pack("<" + "f" * DIM, *f32.cosine_preprocess(struct.unpack("<" + "f" * DIM, data))) != data:
                raise Refused("flat vector is not stable under Qdrant SSE preprocessing")
        if vectors.read(1):
            raise Refused("flat vector file has trailing bytes")
    return full, meta, mod, rec


def cross_check(root: Path, inputs: Path, merged: Path, friend: Path, cross: Path, out: Path,
                split_manifest: Path, friend_vectors: Path, count=500, f32_dir: Path | None = None,
                compat: Path = DEFAULT_COMPAT):
    full, meta, mod, rec = verify_flat(root, inputs, merged, f32_dir, compat)
    f32, f32_sha = f32_helper(f32_dir)
    split = json.loads(split_manifest.read_bytes())
    if split.get("format") != "deep32-w2-split" or split.get("format_version") != 1:
        raise Refused("split manifest format differs")
    for key in ("generation", "corpus_fingerprint", "model_id", "model_revision"):
        if split.get(key) != meta[key]:
            raise Refused(f"split manifest {key} differs from inputs")
    if (split.get("inputs_sha256") != meta.get("sha256")
            or split.get("inputs_manifest_sha256") != sha(inputs)
            or split.get("input_rows") != len(full)):
        raise Refused("split manifest is not bound to the full inputs manifest")
    friends = input_rows(friend, full, mod)
    if (split.get("friend", {}).get("sha256") != sha(friend)
            or split.get("friend", {}).get("rows") != len(friends)
            or split.get("home", {}).get("rows") != len(full) - len(friends)
            or split.get("cross", {}).get("rows") != count):
        raise Refused("friend input hash or split counts differ from split manifest")
    if len(friends) < count:
        raise Refused(f"friend inputs hold {len(friends)} rows, need {count} cross rows")
    selected = sorted(friends, key=lambda ch: hashlib.sha256(f"{CROSS_SEED}:{ch}".encode("ascii")).digest())[:count]
    supplied, friend_sources, _, _, friend_changed = read_vectors([friend_vectors], full, meta, mod, {}, f32)
    if set(supplied) != set(friends):
        raise Refused("friend vector coverage differs from friend inputs")
    friend_sha = friend_sources[0]["sha256"]
    if not any(p.get("sha256") == friend_sha and p.get("rows") == len(friends) for p in rec.get("parts", [])):
        raise Refused("friend vector set was not a recorded source of the flat join")
    points = (merged / "dense/points.bin").read_bytes()
    offsets = {"chk_" + points[i + 16:i + 48].hex(): i // RECORD.size
               for i in range(0, len(points), RECORD.size)}
    with (merged / "dense/vectors.f32").open("rb") as vectors_file:
        for ch in selected:
            vectors_file.seek(offsets[ch] * DIM * 4)
            if vectors_file.read(DIM * 4) != supplied[ch]:
                raise Refused("selected flat vector differs from recorded friend vector set")
    vectors, sources, norms, _, cross_changed = read_vectors([cross], full, meta, mod, {}, f32)
    if set(vectors) != set(selected):
        raise Refused("cross vector ids differ from deterministic friend subset")
    if out.exists():
        raise Refused("checked cross output exists")
    with out.open("wb") as f:
        for ch in sorted(selected):
            f.write((json.dumps({"chunk_id": ch, "vector": struct.unpack("<" + "f" * DIM, vectors[ch])},
                                sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii"))
    return {"cross_rows": count, "selection": CROSS_SEED, "friend_inputs_sha256": sha(friend),
            "split_manifest_sha256": sha(split_manifest), "friend_vectors_sha256": friend_sha,
            "normalisation_helper_sha256": f32_sha, "friend_normalised_rows": friend_changed,
            "cross_normalised_rows": cross_changed,
            "cross_vectors_sha256": sources[0]["sha256"], "checked_cross_sha256": sha(out),
            "flat_vectors_sha256": rec["vectors_sha256"], "norm_min": min(norms), "norm_max": max(norms)}


def main(argv=None):
    ap = argparse.ArgumentParser(allow_abbrev=False, description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--inputs", type=Path, required=True)
    ap.add_argument("--compat", type=Path, default=DEFAULT_COMPAT)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--allow-subset", action="store_true")
    ap.add_argument("--legacy-inputs", type=Path)
    ap.add_argument("--f32-sse", type=Path)
    ap.add_argument("--verify-flat", type=Path)
    ap.add_argument("--friend-inputs", type=Path)
    ap.add_argument("--split-manifest", type=Path)
    ap.add_argument("--friend-vectors", type=Path)
    ap.add_argument("--cross-vectors", type=Path)
    ap.add_argument("--cross-out", type=Path)
    ap.add_argument("vectors", nargs="*", type=Path)
    a = ap.parse_args(argv)
    cross = (a.friend_inputs, a.cross_vectors, a.cross_out, a.split_manifest, a.friend_vectors)
    if a.verify_flat:
        if a.out or a.vectors or a.allow_subset or a.legacy_inputs or (any(cross) and not all(cross)):
            ap.error("cross requires --friend-inputs, --friend-vectors, --split-manifest, --cross-vectors and --cross-out")
    elif not a.out or not a.vectors or any(cross):
        ap.error("conversion needs --out and vector sets")
    try:
        if a.verify_flat and all(cross):
            rec = cross_check(a.root, a.inputs, a.verify_flat, *cross, f32_dir=a.f32_sse, compat=a.compat)
        elif a.verify_flat:
            rec = verify_flat(a.root, a.inputs, a.verify_flat, a.f32_sse, a.compat)[3]
        else:
            rec = convert(a.root, a.inputs, a.vectors, a.out, a.allow_subset, a.legacy_inputs, a.f32_sse, a.compat)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, ImportError) as exc:
        print(f"flat_from_jsonl: REFUSED {exc}", file=sys.stderr)
        return 3
    sys.stdout.write(json.dumps(rec, sort_keys=True, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
