#!/usr/bin/env python3
"""Whether two roots hold their shared chunks in the same rowid order: the precondition of a BM25 twin gate.

usage: chunk_order.py ROOT_A ROOT_B

BM25 ranks by raw score alone (rag_core.retrievers.lexical.RANK_SQL), and FTS5 yields a match in rowid order, so two
chunks of exactly the same score come in rowid order. Two roots whose BM25 tables hold the same rows with the same
statistics (a split root's chunk_fts and its twin built without the opt-in documents, rag_core.db.split_optin_fts)
then answer every query alike only if their shared chunks also come in the same order: a root whose build re-chunked a
document on a resume gave that document later rowids, and a tie with one of its chunks can break the other way in a
fresh build, with no difference in scoring. Run this on both roots before such a gate: when the order differs, a
diff on the gate whose hits include a moved chunk may be a tie broken the other way, not a ranking regression.

Each root's sqlite/rag.db is opened read-only. Chunks are matched by chunk_id; "moved" are the fewest shared chunks
whose removal leaves the two sequences equal (the complement of their longest common order), counted per document;
"changed" are shared chunk_ids whose chunk_sha256 differs. Prints a JSON summary.

Exit status: 0 when the shared chunks come in the same order, 1 when they do not, 2 when a root has no rag.db.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

CHUNKS_SQL = "SELECT chunk_id, doc_id, chunk_sha256 FROM chunks ORDER BY rowid"


def read_chunks(root: Path) -> list[tuple[str, str, str]]:
    """(chunk_id, doc_id, chunk_sha256) of every chunk of `root`, in rowid order."""
    db = root / "sqlite" / "rag.db"
    if not db.is_file():
        raise FileNotFoundError(db)
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [tuple(row) for row in conn.execute(CHUNKS_SQL)]
    finally:
        conn.close()


def in_order(positions: list[int]) -> set[int]:
    """Indexes into `positions` of one longest increasing subsequence (patience sorting, O(n log n))."""
    tails: list[int] = []           # tails[k]: the smallest last value of an increasing run of length k + 1
    tail_at: list[int] = []         # the index of that value
    parent = [-1] * len(positions)
    for i, value in enumerate(positions):
        k = bisect.bisect_left(tails, value)
        if k == len(tails):
            tails.append(value)
            tail_at.append(i)
        else:
            tails[k], tail_at[k] = value, i
        parent[i] = tail_at[k - 1] if k else -1
    kept, i = set(), tail_at[-1] if tail_at else -1
    while i >= 0:
        kept.add(i)
        i = parent[i]
    return kept


def compare(a: list[tuple[str, str, str]], b: list[tuple[str, str, str]]) -> dict:
    """The summary of chunk_order.py for the chunks of two roots (read_chunks)."""
    in_b = {chunk_id: (k, sha) for k, (chunk_id, _, sha) in enumerate(b)}
    in_a = {chunk_id for chunk_id, _, _ in a}
    shared = [(chunk_id, doc_id, sha) for chunk_id, doc_id, sha in a if chunk_id in in_b]
    order_b = [chunk_id for chunk_id, _, _ in b if chunk_id in in_a]
    kept = in_order([in_b[chunk_id][0] for chunk_id, _, _ in shared])
    moved = Counter(doc_id for k, (_, doc_id, _) in enumerate(shared) if k not in kept)
    first = next((k for k, (x, y) in enumerate(zip(shared, order_b)) if x[0] != y), None)
    return {
        "chunks": {"a": len(a), "b": len(b), "shared": len(shared), "only_a": len(a) - len(shared),
                   "only_b": len(b) - len(shared)},
        "same_order": first is None,
        "first_difference": None if first is None else {"position": first, "a": shared[first][0],
                                                        "b": order_b[first]},
        "moved": sum(moved.values()),
        "moved_by_document": dict(sorted(moved.items())),
        "changed": sum(1 for chunk_id, _, sha in shared if in_b[chunk_id][1] != sha),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root_a", type=Path)
    parser.add_argument("root_b", type=Path)
    args = parser.parse_args(argv)
    try:
        a, b = read_chunks(args.root_a), read_chunks(args.root_b)
    except FileNotFoundError as exc:
        print(f"no rag.db: {exc}", file=sys.stderr)
        return 2
    summary = {"a": str(args.root_a), "b": str(args.root_b), **compare(a, b)}
    print(json.dumps(summary, indent=1))
    return 0 if summary["same_order"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
