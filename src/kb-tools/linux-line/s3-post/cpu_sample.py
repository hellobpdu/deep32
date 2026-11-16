#!/usr/bin/env python3
"""The 2,000-chunk processor sample after W1 (the processor comparison procedure section 3; the build schedule item 14): the same chunks
embedded on 901's processors through a TEI CPU server, compared with W1's card vectors by compare-vectors.py.
Processor-mode figures only: these vectors never enter a knowledge base.

usage:
  cpu_sample.py select --root ROOT [--n 2000] --out SAMPLE.jsonl
  cpu_sample.py embed  --sample SAMPLE.jsonl --root ROOT --f32-sse KB_BUNDLE_DIR --out VECTORS.jsonl [--batch 8]

select  (standard library) the n chunks of the root's rag.db with the smallest SHA-256 of
        "deep32-cpu-sample/1:<chunk id>" (no randomness): {"chunk_id", "embedding_text"} per line, sorted by id.
embed   (lab only: runs in the engine's environment on 901, imports rag_core.dense) embeds every sampled text
        with the engine's own JinaEmbeddingClient and the root's dense configuration (RAG_EMBEDDING_URL points
        at the CPU TEI on loopback), then applies Qdrant's cosine preprocessing in its SSE order (the bundle tool's
        f32_sse.cosine_preprocess) so the vectors are comparable with the exported, stored W1 vectors; writes
        {"chunk_id", "vector"} per line, records the model the server answered with and the wall time.
        RAG_EMBEDDING_URL must be set (the engine's default is production's port 8081 in the forge's shared
        network namespace); production's ports and the RAG network and tailnet addresses are refused.
Then: compare-vectors.py VECTORS.jsonl <W1 export or bundle> --subset --report cpu-vs-gpu.json
Exit 0 done, 2 usage, 3 bad input or server error.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
import sqlite3
import time
import urllib.parse
from pathlib import Path

# production's model, Qdrant and gateway ports: in the forge (one network namespace with production) the
# engine's default embedding address, port 8081 on loopback, is production's TEI
PRODUCTION_PORTS = frozenset({8081, 8082, 8083, 8091, 6333, 6334})
REFUSED_NETS = []


def embedding_url() -> str:
    """RAG_EMBEDDING_URL, which embed needs set explicitly (never the engine's default): http(s), not one of
    production's ports, not an address of the RAG network or the tailnet (IPv4 or IPv6)."""
    url = os.environ.get("RAG_EMBEDDING_URL", "")
    if not url:
        raise ValueError("RAG_EMBEDDING_URL is not set: name the CPU TEI on 901 or the forge's own TEI")
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError(f"RAG_EMBEDDING_URL {url!r} is not an http(s) address")
    if parts.port in PRODUCTION_PORTS:
        raise ValueError(f"RAG_EMBEDDING_URL uses port {parts.port}, one of production's")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if host != "localhost" and (ip is None or not ip.is_loopback):
        raise ValueError("RAG_EMBEDDING_URL must name a loopback address")
    return url


def select(root: Path, n: int, out: Path) -> dict:
    db = sqlite3.connect(root.joinpath("sqlite/rag.db").resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        rows = db.execute("SELECT chunk_id, embedding_text FROM chunks").fetchall()
    finally:
        db.close()
    pick = sorted(rows, key=lambda r: hashlib.sha256(f"deep32-cpu-sample/1:{r[0]}".encode()).hexdigest())[:n]
    pick.sort()
    out.write_text("".join(json.dumps({"chunk_id": c, "embedding_text": t}) + "\n" for c, t in pick), "utf-8")
    return {"chunks_in_root": len(rows), "sampled": len(pick), "out": str(out)}


def embed(sample: Path, root: Path, f32_dir: Path, out: Path, batch: int) -> dict:
    embedding_url()                     # refuses before anything is loaded or sent
    spec = importlib.util.spec_from_file_location("f32_sse", f32_dir / "f32_sse.py")
    f32 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f32)
    os.environ["RAG_ROOT"] = str(root)
    from rag_core.dense import JinaEmbeddingClient, load_dense_config   # the engine's environment on 901
    config = load_dense_config(root=root)
    client = JinaEmbeddingClient(config)
    rows = [json.loads(line) for line in sample.read_text(encoding="utf-8").splitlines() if line.strip()]
    started, models, written = time.monotonic(), set(), []
    try:
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            vectors, model = client.embed([str(r["embedding_text"]).strip() for r in part])
            models.add(model)
            for r, v in zip(part, vectors, strict=True):
                written.append({"chunk_id": r["chunk_id"],
                                "vector": f32.cosine_preprocess([f32.f32(x) for x in v])})
    finally:
        client.close()
    seconds = time.monotonic() - started
    out.write_text("".join(json.dumps(w) + "\n" for w in written), encoding="utf-8")
    return {"sampled": len(written), "models": sorted(models), "seconds": round(seconds, 1),
            "chunks_per_second": round(len(written) / seconds, 3) if seconds else None, "out": str(out)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cpu_sample.py", allow_abbrev=False)
    sub = ap.add_subparsers(dest="verb", required=True)
    s = sub.add_parser("select", allow_abbrev=False)
    s.add_argument("--root", required=True)
    s.add_argument("--n", type=int, default=2000)
    s.add_argument("--out", required=True)
    e = sub.add_parser("embed", allow_abbrev=False)
    e.add_argument("--sample", required=True)
    e.add_argument("--root", required=True)
    e.add_argument("--f32-sse", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--batch", type=int, default=8)
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if a.verb == "select":
            rec = select(Path(a.root), a.n, Path(a.out))
        else:
            rec = embed(Path(a.sample), Path(a.root), Path(a.f32_sse), Path(a.out), a.batch)
    except (OSError, ValueError, KeyError, sqlite3.Error, RuntimeError, ImportError) as exc:
        print(f"cpu_sample: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(rec, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
