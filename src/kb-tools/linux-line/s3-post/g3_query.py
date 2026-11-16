#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import sqlite3
from pathlib import Path

KEPT = ("rfc", "cisco", "powershell", "system")


def lines(path: str | None, minimum: int = 1) -> list[str]:
    if not path:
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and len(line) >= minimum:
            out.append(line)
    return out


def run(d: Path, cut_keys: str | None, markings: str | None, expect: str | None) -> dict:
    root = d if (d / "sqlite/rag.db").is_file() else d / "root"
    db = sqlite3.connect((root / "sqlite/rag.db").resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        by_type = dict(db.execute("SELECT doc_type, COUNT(*) FROM doc_sources GROUP BY doc_type").fetchall())
        chunks = dict(db.execute("SELECT s.doc_type, COUNT(*) FROM chunks c JOIN doc_sources s "
                                 "ON s.doc_id = c.doc_id GROUP BY s.doc_type").fetchall())
        keys = {r[0] for r in db.execute("SELECT doc_key FROM doc_sources")}
        docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        marks = lines(markings, 3)
        marked = 0
        if marks:
            for (text,) in db.execute("SELECT text FROM chunks"):
                if any(m in text for m in marks):
                    marked += 1
    finally:
        db.close()
    manifest = json.loads((root / "sources/manifest.json").read_bytes())
    man_types: dict[str, int] = {}
    for e in manifest:
        man_types[e["doc_type"]] = man_types.get(e["doc_type"], 0) + 1
    keys |= {e["doc_key"] for e in manifest}
    cut = set(lines(cut_keys))
    failures = []
    other = sorted((set(by_type) | set(man_types)) - set(KEPT))
    if other:
        failures.append(f"collections outside the kept four: {len(other)}")
    if by_type != man_types:
        failures.append("doc_sources and sources/manifest.json count collections differently")
    if docs != sum(by_type.values()):
        failures.append("documents and doc_sources differ in number")
    found_cut = len(keys & cut)
    if found_cut:
        failures.append(f"{found_cut} cut keys present")
    if marked:
        failures.append(f"{marked} chunks hold a marking string")
    if expect:
        want = {k: int(v) for k, v in (p.split("=") for p in expect.split(","))}
        if {k: by_type.get(k, 0) for k in want} != want:
            failures.append("collection counts differ from --expect")
    return {"format": "deep32-g3-query", "dir": str(d), "documents_by_collection": by_type,
            "manifest_by_collection": man_types, "chunks_by_collection": chunks,
            "cut_keys_checked": len(cut), "cut_keys_found": found_cut, "marking_strings_checked": len(marks),
            "chunks_with_marking": marked, "failures": failures, "status": "pass" if not failures else "fail"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="g3_query.py", allow_abbrev=False)
    ap.add_argument("dir")
    ap.add_argument("--cut-keys")
    ap.add_argument("--markings")
    ap.add_argument("--expect")
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        rec = run(Path(a.dir), a.cut_keys, a.markings, a.expect)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f"g3_query: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(rec, sort_keys=True) if a.json else
          f"G3 query {rec['status']}: {rec['documents_by_collection']} {'; '.join(rec['failures'])}")
    return 0 if rec["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
