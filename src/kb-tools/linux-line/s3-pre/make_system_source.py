#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
from pathlib import Path


class Bad(Exception):
    pass


def slug(text: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def key_for(name: str, rel: str, used: set[str]) -> str:
    # The vendor tree keeps production's own doc keys (ext-<name>), so eval sets and references that name them match
    # (left-out audit R3/C8, lead ruling 2026-11-15).
    stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if name == "vendor" and re.fullmatch(r"ext-[a-z0-9][a-z0-9-]{0,80}", stem):
        if stem in used:
            raise Bad(f"doc_key collision for {name}/{rel}")
        used.add(stem)
        return stem
    base = "sys-" + slug(f"{name}-{rel}")
    key = base
    if len(key) > 90 or key in used:
        key = base[:81].rstrip("-") + "-" + hashlib.sha256(f"{name}/{rel}".encode()).hexdigest()[:8]
    if key in used:
        raise Bad(f"doc_key collision for {name}/{rel}")
    used.add(key)
    return key


def matches(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def title_of(name: str, rel: str, text: str) -> str:
    if rel.lower().endswith(".md"):
        for line in text.splitlines():
            if line.startswith("# "):
                return f"{line[2:].strip()} ({name}/{rel})"
    return f"{name}/{rel}"


def make(trees: list[tuple[str, Path]], sel: dict, out: Path) -> dict:
    if out.exists():
        raise Bad(f"{out} exists")
    docs_sfx, code_sfx = tuple(sel["doc_suffixes"]), tuple(sel["code_suffixes"])
    limit = int(sel.get("max_bytes", 64000000))
    entries, used, skipped = [], set(), {"not-selected": 0, "not-text": 0, "too-large": 0, "unknown-kind": 0, "empty": 0}
    tmp = out.with_name(out.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "system").mkdir(parents=True)
    try:
        for name, root in trees:
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name):
                raise Bad(f"tree name {name!r}")
            for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix()):
                if not p.is_file() or p.is_symlink():
                    continue
                rel = p.relative_to(root).as_posix()
                full = f"{name}/{rel}"
                if not matches(full, sel["include"]) or matches(full, sel["exclude"]):
                    skipped["not-selected"] += 1
                    continue
                kind = ("documentation" if rel.lower().endswith(docs_sfx) else
                        "source" if rel.lower().endswith(code_sfx) else None)
                if kind is None:
                    skipped["unknown-kind"] += 1
                    continue
                data = p.read_bytes()
                if len(data) > limit:
                    skipped["too-large"] += 1
                    continue
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    skipped["not-text"] += 1
                    continue
                if "\x00" in text:
                    skipped["not-text"] += 1
                    continue
                if not text.strip():
                    # an empty file (an empty __init__.py) has nothing to index: the chunker makes zero chunks and
                    # build_root marks the generation incomplete (S3-P6 lab run, 2026-11-15)
                    skipped["empty"] += 1
                    continue
                key = key_for(name, rel, used)
                (tmp / "system" / f"{key}.txt").write_bytes(data)
                entries.append({
                    "doc_key": key, "title": title_of(name, rel, text), "doc_type": "system",
                    "path": f"system/{key}.txt", "source_url": None, "platform": None, "release": None,
                    "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "doc_kind": kind,
                    "page_url": None, "release_note": None,
                    "license": (sel["licence_vendor"] if name == "vendor" else
                                sel["licence_docs"] if kind == "documentation" else sel["licence_code"]),
                    "terms_url": None, "redistribute": True, "near_duplicate_of": [],
                })
        if not entries:
            raise Bad("no file selected")
        entries.sort(key=lambda e: e["doc_key"])
        (tmp / "manifest.json").write_bytes((json.dumps(entries, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    kinds = {}
    for e in entries:
        kinds[e["doc_kind"]] = kinds.get(e["doc_kind"], 0) + 1
    return {"out": str(out), "documents": len(entries), "bytes": sum(e["bytes"] for e in entries),
            "by_kind": kinds, "skipped": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="make_system_source.py", allow_abbrev=False)
    ap.add_argument("--out", required=True)
    ap.add_argument("--select", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("trees", nargs="+")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        trees = []
        for t in a.trees:
            name, sep, d = t.partition("=")
            if not sep or not Path(d).is_dir():
                raise Bad(f"{t!r}: expected NAME=DIR with an existing folder")
            trees.append((name, Path(d)))
        rec = make(trees, json.loads(Path(a.select).read_bytes()), Path(a.out))
    except (Bad, OSError, ValueError, KeyError) as exc:
        print(f"make_system_source: REFUSED {exc}", file=sys.stderr)
        return 3
    print(json.dumps(rec, sort_keys=True) if a.json else f"{rec['documents']} documents into {rec['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
