#!/usr/bin/env python3
"""Compare two flat vector sets for the same chunk ids: per-id cosine and largest absolute difference, then
top-k order and score differences at a tolerance (contract C3, the bundle tool C3-PROPOSALS.md: 1e-5 by default).

Used for: CPU against GPU (the 2,000-chunk sample on 901 against W1's vectors), and one card against another
(the 2080 Ti against the friend's 3080) before any W1 leg moves to the second card.

usage: compare-vectors.py A B [--tolerance 1e-5] [--component-tolerance X] [--top-k 10] [--probes 32]
                              [--subset] [--json] [--report FILE]

A and B are each either
  - a folder holding dense/vectors.f32 and dense/points.bin (an unpacked generation export or a C3 bundle;
    the dimension comes from dense/EXPORT.json, or from the file sizes), or
  - a JSON-lines file, one object per line: {"chunk_id": "chk_<64 hex>", "vector": [numbers]}.
Rows are matched by chunk id. Without --subset both sets must hold exactly the same ids; with --subset A's ids
must all be in B and only those are compared (the CPU sample against the full W1 export).

What is measured (all arithmetic in double precision on the stored float32 values; the comparison is of data,
not of a search kernel's operation order):
  per id    cosine(a, b); self drift = |1 - cosine|; largest absolute component difference; bit-identical rows
  top-k     probes = the A vectors of --probes compared ids chosen by SHA-256 rank of "deep32-compare/1:<chunk id>"
            (no randomness); each probe is scored by dot product against every compared row in A and in B
            (both sets are unit vectors after Qdrant's cosine preprocessing); then, over the union of the two
            top-k lists: score difference |sA - sB| per id; an id in one top-k but not the other must score
            within the tolerance of that list's cut (k-th score); two ids whose order differs must be within
            the tolerance of each other in both lists.
Verdict
  identical          every compared row bit-identical (exit 0)
  within-tolerance   worst self drift and worst top-k score difference <= tolerance (and the largest component
                     difference <= --component-tolerance when given), no inclusion or order violation (exit 0)
  outside-tolerance  anything else (exit 1)
The boundary counts as inside: a figure equal to the tolerance passes.
Exit: 0 identical or within, 1 outside, 2 usage error, 3 unreadable or mismatched input.
Standard library only; Python 3.12 or later (math.sumprod).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import math
import struct
import uuid
from array import array
from pathlib import Path

FORMAT = "deep32-vector-compare"
SEED = "deep32-compare/1"
RECORD = 96


class InputError(Exception):
    pass


def dot(a, b) -> float:
    return math.sumprod(a, b) if hasattr(math, "sumprod") else math.fsum(x * y for x, y in zip(a, b))


class VectorSet:
    """chunk id -> row index, and one flat float32 array (row-major)."""

    def __init__(self, path: Path):
        self.path = path
        if path.is_dir():
            self._load_dir(path)
        elif path.is_file():
            self._load_jsonl(path)
        else:
            raise InputError(f"{path}: not a folder or a file")

    def _load_dir(self, path: Path) -> None:
        vec, pts = path / "dense/vectors.f32", path / "dense/points.bin"
        if not vec.is_file() or not pts.is_file():
            raise InputError(f"{path}: dense/vectors.f32 and dense/points.bin are needed")
        raw = pts.read_bytes()
        if len(raw) % RECORD:
            raise InputError(f"{pts}: size {len(raw)} is not a multiple of {RECORD}")
        n = len(raw) // RECORD
        self.ids = []
        for i in range(n):
            rec = raw[i * RECORD:(i + 1) * RECORD]
            chunk = "chk_" + rec[16:48].hex()
            pid = uuid.UUID(bytes=rec[:16])
            if pid != uuid.uuid5(uuid.NAMESPACE_URL, "rag-core:dense:v1:" + chunk):
                raise InputError(f"{pts}: record {i} point id does not match its chunk id")
            self.ids.append(chunk)
        size = vec.stat().st_size
        if n == 0 or size % (4 * n):
            raise InputError(f"{vec}: {size} bytes do not hold {n} float32 rows")
        self.dim = size // (4 * n)
        export = path / "dense/EXPORT.json"
        if export.is_file():
            declared = json.loads(export.read_bytes()).get("dim")
            if declared is not None and int(declared) != self.dim:
                raise InputError(f"{export}: dim {declared}, the files hold {self.dim}")
        self.data = array("f")
        self.data.frombytes(vec.read_bytes())
        if sys.byteorder != "little":
            self.data.byteswap()
        self.source = "flat-folder"
        self._index()

    def _load_jsonl(self, path: Path) -> None:
        self.ids, self.data, self.dim = [], array("f"), None
        with path.open("r", encoding="utf-8") as f:
            for no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    chunk, vector = obj["chunk_id"], obj["vector"]
                except (ValueError, KeyError, TypeError) as exc:
                    raise InputError(f"{path}:{no}: {type(exc).__name__}") from None
                if not isinstance(chunk, str) or not chunk.startswith("chk_") or len(chunk) != 68:
                    raise InputError(f"{path}:{no}: bad chunk_id")
                if not isinstance(vector, list) or not vector:
                    raise InputError(f"{path}:{no}: bad vector")
                if self.dim is None:
                    self.dim = len(vector)
                elif len(vector) != self.dim:
                    raise InputError(f"{path}:{no}: dimension {len(vector)}, expected {self.dim}")
                self.ids.append(chunk)
                self.data.extend(float(v) for v in vector)
        if not self.ids:
            raise InputError(f"{path}: no rows")
        self.source = "jsonl"
        self._index()

    def _index(self) -> None:
        self.row = {}
        for i, c in enumerate(self.ids):
            if c in self.row:
                raise InputError(f"{self.path}: chunk id {c} appears twice")
            self.row[c] = i
        if any(not math.isfinite(v) for v in self.data):
            raise InputError(f"{self.path}: a vector holds a value that is not finite")

    def vec(self, chunk: str):
        i = self.row[chunk]
        return self.data[i * self.dim:(i + 1) * self.dim]


def rank_key(chunk: str) -> str:
    return hashlib.sha256(f"{SEED}:{chunk}".encode("ascii")).hexdigest()


def compare(a: VectorSet, b: VectorSet, tol: float, comp_tol: float | None, k: int, probes: int,
            subset: bool) -> dict:
    if a.dim != b.dim:
        raise InputError(f"dimensions differ: {a.dim} and {b.dim}")
    ids_a, ids_b = set(a.ids), set(b.ids)
    if subset:
        missing = sorted(ids_a - ids_b)
        if missing:
            raise InputError(f"{len(missing)} ids of A are not in B (first {missing[0]})")
    elif ids_a != ids_b:
        raise InputError(f"the id sets differ: {len(ids_a - ids_b)} only in A, {len(ids_b - ids_a)} only in B")
    ids = sorted(ids_a)
    va = {c: a.vec(c) for c in ids}
    vb = {c: b.vec(c) for c in ids}

    identical = 0
    min_cos, max_drift, max_abs = 1.0, 0.0, 0.0
    per_id = []
    for c in ids:
        x, y = va[c], vb[c]
        if x.tobytes() == y.tobytes():
            identical += 1
            per_id.append((0.0, 0.0, c, 1.0))
            continue
        nx, ny = math.sqrt(dot(x, x)), math.sqrt(dot(y, y))
        cos = dot(x, y) / (nx * ny) if nx and ny else 0.0
        drift = abs(1.0 - cos)
        diff = max(abs(p - q) for p, q in zip(x, y))
        min_cos, max_drift, max_abs = min(min_cos, cos), max(max_drift, drift), max(max_abs, diff)
        per_id.append((drift, diff, c, cos))
    over = sum(1 for d, m, _, _ in per_id if d > tol or (comp_tol is not None and m > comp_tol))
    worst_ids = [{"chunk_id": c, "cosine": cos, "self_drift": d, "max_abs_diff": m}
                 for d, m, c, cos in sorted(per_id, key=lambda r: (-r[0], -r[1], r[2]))[:10] if d or m]

    k = min(k, len(ids))
    chosen = sorted(ids, key=rank_key)[:min(probes, len(ids))]
    max_score_diff = 0.0
    inclusion, order, worst_probe = [], [], []
    for p in chosen:
        q = va[p]
        sa = {c: dot(q, va[c]) for c in ids}
        sb = {c: dot(q, vb[c]) for c in ids}
        top_a = sorted(ids, key=lambda c: (-sa[c], c))[:k]
        top_b = sorted(ids, key=lambda c: (-sb[c], c))[:k]
        cut_a, cut_b = sa[top_a[-1]], sb[top_b[-1]]
        union = set(top_a) | set(top_b)
        diff = max(abs(sa[c] - sb[c]) for c in union)
        max_score_diff = max(max_score_diff, diff)
        worst_probe.append((diff, p))
        for c in set(top_a) - set(top_b):
            if sa[c] > cut_a + tol:
                inclusion.append({"probe": p, "chunk_id": c, "in": "A only", "score": sa[c], "cut": cut_a})
        for c in set(top_b) - set(top_a):
            if sb[c] > cut_b + tol:
                inclusion.append({"probe": p, "chunk_id": c, "in": "B only", "score": sb[c], "cut": cut_b})
        both = [c for c in top_a if c in set(top_b)]
        pos_b = {c: i for i, c in enumerate(top_b)}
        for i, x in enumerate(both):
            for y in both[i + 1:]:
                if pos_b[x] > pos_b[y]:                  # x before y in A, after y in B
                    if abs(sa[x] - sa[y]) > tol or abs(sb[x] - sb[y]) > tol:
                        order.append({"probe": p, "pair": [x, y], "gap_a": sa[x] - sa[y], "gap_b": sb[x] - sb[y]})
    worst = max(max_drift, max_score_diff)
    passed = (max_drift <= tol and max_score_diff <= tol and not inclusion and not order
              and (comp_tol is None or max_abs <= comp_tol))
    verdict = "identical" if identical == len(ids) else ("within-tolerance" if passed else "outside-tolerance")
    return {
        "format": FORMAT, "format_version": 1,
        "a": {"path": str(a.path), "source": a.source, "rows": len(a.ids), "dim": a.dim},
        "b": {"path": str(b.path), "source": b.source, "rows": len(b.ids), "dim": b.dim},
        "subset": subset, "compared": len(ids), "tolerance": tol, "component_tolerance": comp_tol,
        "per_id": {"bit_identical_rows": identical, "min_cosine": min_cos, "max_self_drift": max_drift,
                   "max_abs_diff": max_abs, "over_tolerance": over, "worst": worst_ids},
        "topk": {"k": k, "probes": len(chosen), "max_score_diff": max_score_diff,
                 "inclusion_violations": len(inclusion), "order_violations": len(order),
                 "examples": (inclusion + order)[:10],
                 "worst_probes": [{"probe": p, "max_score_diff": d}
                                  for d, p in sorted(worst_probe, key=lambda r: (-r[0], r[1]))[:5]]},
        "worst": worst, "verdict": verdict, "exit": 0 if verdict != "outside-tolerance" else 1,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="compare-vectors.py", allow_abbrev=False,
                                 description="Compare two flat vector sets (see the module docstring).")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--tolerance", type=float, default=1e-5)
    ap.add_argument("--component-tolerance", type=float)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--probes", type=int, default=32)
    ap.add_argument("--subset", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not (args.tolerance >= 0 and math.isfinite(args.tolerance)) or args.top_k < 1 or args.probes < 1 or (
            args.component_tolerance is not None and not args.component_tolerance >= 0):
        print("compare-vectors: tolerance must be >= 0, --top-k and --probes >= 1", file=sys.stderr)
        return 2
    try:
        result = compare(VectorSet(Path(args.a)), VectorSet(Path(args.b)), args.tolerance,
                         args.component_tolerance, args.top_k, args.probes, args.subset)
    except (InputError, OSError, struct.error) as exc:
        print(f"compare-vectors: {exc}", file=sys.stderr)
        return 3
    text = json.dumps(result, sort_keys=True, indent=1) + "\n"
    if args.report:
        Path(args.report).write_text(text, encoding="ascii")
    if args.json:
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    else:
        print(f"{result['verdict']}: {result['compared']} ids, {result['per_id']['bit_identical_rows']} bit-identical, "
              f"min cosine {result['per_id']['min_cosine']:.9f}, max |a-b| {result['per_id']['max_abs_diff']:.3e}, "
              f"top-{result['topk']['k']} max score diff {result['topk']['max_score_diff']:.3e} over "
              f"{result['topk']['probes']} probes, tolerance {result['tolerance']:g}")
    return result["exit"]


if __name__ == "__main__":
    sys.exit(main())
