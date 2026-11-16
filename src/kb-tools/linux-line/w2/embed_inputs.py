#!/usr/bin/env python3
"""Export every stored embedding text through cpu_sample.select, without embedding.

Usage: embed_inputs.py --root ROOT --out inputs.jsonl [--metadata inputs.json]
       [--force-read] [--cpu-sample ../s3-post/cpu_sample.py]

The root must be quiescent. Dense state in the report, SQLite or a flat export
refuses the export unless --force-read is explicit. Outputs must not exist.
Hash embedding_text as its exact UTF-8 bytes, before stripping or normalising it.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path

MODEL_ID = "jinaai/jina-embeddings-v5-text-small-retrieval"
MODEL_REVISION = "6856e76bb72982e58de0620458a4e8b3614da340"
HERE = Path(__file__).resolve().parent


class Refused(ValueError):
    pass


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1 << 20), b""):
            h.update(data)
    return h.hexdigest()


def embedding_text_sha256(text: str) -> str:
    if not isinstance(text, str):
        raise Refused("embedding_text is not a string")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ro(root: Path) -> sqlite3.Connection:
    path = Path(root) / "sqlite/rag.db"
    if not path.is_file() or path.is_symlink():
        raise Refused("root has no regular sqlite/rag.db")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise Refused("root is not quiescent: a non-empty SQLite sidecar exists")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)


def read_root(root: Path) -> dict[str, dict]:
    """Chunk id -> doc_id, ordinal and exact stored embedding_text (read-only)."""
    db = ro(Path(root))
    try:
        rows = db.execute("SELECT chunk_id, doc_id, ordinal, embedding_text FROM chunks ORDER BY chunk_id")
        result = {}
        for chunk_id, doc_id, ordinal, text in rows:
            if not isinstance(chunk_id, str) or not isinstance(doc_id, str) or not isinstance(text, str):
                raise Refused("root has a non-string chunk id, document id or embedding text")
            if chunk_id in result or not isinstance(ordinal, int) or ordinal < 0:
                raise Refused("root has duplicate chunk ids or an invalid ordinal")
            result[chunk_id] = {"doc_id": doc_id, "ordinal": ordinal, "embedding_text": text}
        if not result:
            raise Refused("root has no chunks")
        return result
    finally:
        db.close()


def root_metadata(root: Path) -> dict:
    report = json.loads((Path(root) / "BUILD_REPORT.json").read_bytes())
    generation = report.get("generation")
    corpus = report.get("corpus") or {}
    fingerprint = corpus.get("fingerprint") or corpus.get("manifest_fingerprint")
    if not isinstance(generation, str) or not generation or not isinstance(fingerprint, str) or not fingerprint:
        raise Refused("root report lacks a generation or corpus fingerprint")
    if report.get("failed_steps"):
        raise Refused("root report has failed steps")
    if report.get("status") != "complete":
        raise Refused("root report is not complete")
    return {"generation": generation, "corpus_fingerprint": fingerprint,
            "model_id": MODEL_ID, "model_revision": MODEL_REVISION}


def dense_present(root: Path) -> bool:
    report = json.loads((Path(root) / "BUILD_REPORT.json").read_bytes())
    dense = report.get("dense") or {}
    if any(dense.get(k, 0) for k in ("points", "chunks_indexed_this_run", "embedded_this_run", "reused_this_run")):
        return True
    if (dense.get("index") or {}).get("points", 0):
        return True
    for rel in ("dense/vectors.f32", "dense/points.bin"):
        path = Path(root) / rel
        if path.is_file() and path.stat().st_size:
            return True
    db = ro(Path(root))
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_state'").fetchone():
            return bool(db.execute("SELECT COUNT(*) FROM index_state WHERE dense_indexed = 1").fetchone()[0])
        return False
    finally:
        db.close()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    if spec is None or spec.loader is None:
        raise Refused("cannot load the required helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def export_inputs(root: Path, out: Path, metadata: Path | None = None, *, force_read: bool = False,
                  cpu_sample: Path | None = None, model_id: str = MODEL_ID,
                  model_revision: str = MODEL_REVISION) -> dict:
    root, out = Path(root), Path(out)
    metadata = Path(metadata) if metadata else out.with_name("inputs.json")
    if out.resolve() == metadata.resolve() or out.exists() or metadata.exists():
        raise Refused("output or metadata already exists, or the paths coincide")
    if out.parent.resolve() != metadata.parent.resolve():
        raise Refused("output and metadata must share a folder")
    if not model_id or not model_revision:
        raise Refused("expected model id and revision are required")
    report_before = sha_file(root / "BUILD_REPORT.json")
    meta = root_metadata(root)
    texts = read_root(root)
    dense = dense_present(root)
    if dense and not force_read:
        raise Refused("root already has dense vectors; use --force-read only for an intentional read")
    helper = Path(cpu_sample) if cpu_sample else HERE.parent / "s3-post/cpu_sample.py"
    cpu = load_module("w2_cpu_sample", helper)
    before = sha_file(root / "sqlite/rag.db")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="w2-inputs-", dir=out.parent) as scratch:
        staged = Path(scratch) / "inputs.jsonl"
        # This is the existing sample's complete extraction path. n == all rows
        # selects every chunk; the helper then sorts the selected rows by id.
        selected = cpu.select(root, len(texts), staged)
        # cpu_sample's write_text uses the host newline. Make the shipped JSONL
        # ASCII/LF on Windows too; do not change any decoded embedding text.
        rows = [json.loads(line) for line in staged.read_text(encoding="utf-8").splitlines()]
        if len(rows) != len(texts) or selected.get("sampled") != len(texts):
            raise Refused("cpu_sample did not export every root chunk")
        if [r.get("chunk_id") for r in rows] != sorted(texts):
            raise Refused("cpu_sample output ids differ from the root's ordered chunk ids")
        if any(r.get("embedding_text") != texts[r["chunk_id"]]["embedding_text"] for r in rows):
            raise Refused("cpu_sample output text differs from the root")
        staged.write_bytes(("".join(json.dumps(r, ensure_ascii=True) + "\n" for r in rows)).encode("ascii"))
        if sha_file(root / "sqlite/rag.db") != before or sha_file(root / "BUILD_REPORT.json") != report_before:
            raise Refused("root database or report changed during export")
        meta.update(format="deep32-w2-inputs", format_version=1, rows=len(rows), sha256=sha_file(staged),
                    model_id=model_id, model_revision=model_revision,
                    model={"id": model_id, "revision": model_revision},
                    embedding_text_hash="sha256:utf-8:exact", root_db_sha256=before,
                    root_report_sha256=report_before,
                    cpu_sample_sha256=sha_file(helper), force_read=force_read, dense_present=dense)
        m = Path(scratch) / "inputs.json"
        m.write_bytes((json.dumps(meta, sort_keys=True, indent=1, ensure_ascii=True) + "\n").encode("ascii"))
        os.replace(staged, out)
        os.replace(m, metadata)
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--metadata")
    ap.add_argument("--force-read", action="store_true")
    ap.add_argument("--cpu-sample")
    ap.add_argument("--model-id", default=MODEL_ID)
    ap.add_argument("--model-revision", default=MODEL_REVISION)
    try:
        a = ap.parse_args(argv)
        rec = export_inputs(Path(a.root), Path(a.out), Path(a.metadata) if a.metadata else None,
                            force_read=a.force_read, cpu_sample=Path(a.cpu_sample) if a.cpu_sample else None,
                            model_id=a.model_id, model_revision=a.model_revision)
    except Refused as exc:
        print("embed_inputs: REFUSED " + str(exc), file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError, sqlite3.Error, ImportError):
        print("embed_inputs: REFUSED invalid or unreadable input; no embedding text printed", file=sys.stderr)
        return 3
    print(json.dumps(rec, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
