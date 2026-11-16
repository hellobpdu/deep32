#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import ipaddress
import json
import struct
import urllib.error
import urllib.parse
import urllib.request
import uuid
from array import array
from pathlib import Path

RECORD = 96
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Refused(Exception):
    pass


def check_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or parts.path not in ("", "/") or parts.query:
        raise Refused(f"{url!r}: expected http://<loopback>:<port>")
    host = parts.hostname
    if host != "localhost":
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise Refused(f"{host!r}: only a loopback address is accepted") from None
        if not ip.is_loopback:
            raise Refused(f"{host}: only a loopback address is accepted")
    return url.rstrip("/")


def call(base: str, method: str, path: str, body=None, absent_ok=False):
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=120) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        if absent_ok and exc.code == 404:
            return None
        raise Refused(f"{method} {path}: HTTP {exc.code} {exc.read()[:300]!r}") from None
    except urllib.error.URLError as exc:
        raise Refused(f"{method} {path}: {exc.reason}") from None


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def load(merged: Path, collection: str, base: str, batch: int, readback: str) -> dict:
    raw = (merged / "dense/points.bin").read_bytes()
    vec = array("f")
    vec.frombytes((merged / "dense/vectors.f32").read_bytes())
    if sys.byteorder != "little":
        vec.byteswap()
    n = len(raw) // RECORD
    if not n or len(raw) % RECORD or len(vec) % n:
        raise Refused("merged files are not a flat set")
    dim = len(vec) // n
    rows = []
    for i in range(n):
        rec = raw[i * RECORD:(i + 1) * RECORD]
        rows.append((str(uuid.UUID(bytes=rec[:16])), "chk_" + rec[16:48].hex(), "doc_" + rec[48:80].hex()))
    if call(base, "GET", f"/collections/{collection}", absent_ok=True) is not None:
        raise Refused(f"collection {collection} exists: a new one only")
    call(base, "PUT", f"/collections/{collection}", {"vectors": {"size": dim, "distance": "Cosine"}})
    batches = 0
    for start in range(0, n, batch):
        points = [{"id": pid, "vector": [float(v) for v in vec[i * dim:(i + 1) * dim]],
                   "payload": {"chunk_id": chunk, "doc_id": doc}}
                  for i, (pid, chunk, doc) in enumerate(rows[start:start + batch], start)]
        call(base, "PUT", f"/collections/{collection}/points?wait=true", {"points": points})
        batches += 1
    count = call(base, "POST", f"/collections/{collection}/points/count", {"exact": True})["result"]["count"]
    if count != n:
        raise Refused(f"collection holds {count} points, expected {n}")
    mismatches = []
    if readback == "all":
        for start in range(0, n, batch):
            part = rows[start:start + batch]
            got = call(base, "POST", f"/collections/{collection}/points",
                       {"ids": [p[0] for p in part], "with_vector": True, "with_payload": True})["result"]
            by_id = {str(uuid.UUID(str(g["id"]))): g for g in got}
            for i, (pid, chunk, doc) in enumerate(part, start):
                g = by_id.get(pid)
                want = vec[i * dim:(i + 1) * dim]
                if (g is None or g.get("payload") != {"chunk_id": chunk, "doc_id": doc}
                        or array("f", (f32(v) for v in g.get("vector") or [])).tobytes() != want.tobytes()):
                    mismatches.append(chunk)
    return {"collection": collection, "points": n, "dim": dim, "batches": batches, "count": count,
            "readback": readback, "mismatches": len(mismatches), "first_mismatches": mismatches[:5]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="load_flat.py", allow_abbrev=False)
    ap.add_argument("--merged", required=True)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--qdrant-url", required=True)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--readback", choices=("all", "none"), default="all")
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not 1 <= a.batch <= 4096 or not a.collection.startswith("rag_dense_"):
        print("load_flat: --batch 1..4096 and a rag_dense_<name> collection", file=sys.stderr)
        return 2
    try:
        out = load(Path(a.merged), a.collection, check_url(a.qdrant_url), a.batch, a.readback)
    except (Refused, OSError, ValueError, KeyError) as exc:
        print(f"load_flat: REFUSED {exc}", file=sys.stderr)
        return 3
    print(json.dumps(out, sort_keys=True) if a.json else
          f"{out['collection']}: {out['count']} points, read-back mismatches {out['mismatches']}")
    return 1 if out["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
