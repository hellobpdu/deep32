#!/usr/bin/env python3
"""Partition W2 inputs with the unchanged friend sample and clean-input gates.

Usage: split_friend.py --inputs inputs.jsonl --root ROOT --block BLOCK [--block BLOCK...]
       --gate-dir FRIEND_GPU_LEGS --out DIR [--terms DIR] [--inputs-manifest inputs.json]

Each row is checked by allowlist-build.Builder.sample and clean-inputs.check,
using the same kept blocks and term loader as the existing sample leg. Refused
rows stay in home.jsonl. The complete friend.jsonl is checked again as a sample
and as destination w2-inputs.jsonl. friend-allowlist.json is ready for the existing
clean-inputs.py --stage invocation. The report contains counts and hashes only.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import collections
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("required gate helper unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


E = load("w2_split_inputs", HERE / "embed_inputs.py")


def load_gates(folder: Path):
    folder = Path(folder)
    lib = load("gatelib", folder / "gatelib.py")
    prior = sys.modules.get("gatelib")
    sys.modules["gatelib"] = lib
    original_path = list(sys.path)
    try:
        builder = load("w2_allowlist_build", folder / "allowlist-build.py")
        cleaner = load("w2_clean_inputs", folder / "clean-inputs.py")
    finally:
        sys.path[:] = original_path
        if prior is None:
            sys.modules.pop("gatelib", None)
        else:
            sys.modules["gatelib"] = prior
    return SimpleNamespace(lib=lib, builder=builder, cleaner=cleaner)


def write_rows(path: Path, rows: list[dict]) -> None:
    with Path(path).open("w", encoding="ascii", newline="\n") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=True) + "\n")


def allowlist_record(b) -> dict:
    return {"format": "deep32-friend-allowlist", "format_version": 1,
            "terms": dict(sorted(b.terms.files.items())),
            "sources": sorted(b.sources, key=lambda s: (s["kind"], s["path"])),
            "entries": {k: b.entries[k] for k in sorted(b.entries)},
            "digests": {k: {"from": sorted(set(v["from"]))} for k, v in sorted(b.digests.items())}}


def split(inputs: Path, root: Path, blocks: list[Path], gate_dir: Path, out: Path, *,
          inputs_manifest: Path | None = None, terms_dir: Path | None = None,
          cross_rows: int = 500, gates=None, terms=None) -> dict:
    inputs, root, gate_dir, out = map(Path, (inputs, root, gate_dir, out))
    if out.exists() or not blocks or cross_rows < 0:
        raise E.Refused("output exists, no kept blocks supplied, or negative cross count")
    manifest = Path(inputs_manifest) if inputs_manifest else inputs.with_name("inputs.json")
    meta = json.loads(manifest.read_bytes())
    root_meta = E.root_metadata(root)
    input_sha = E.sha_file(inputs)
    if meta.get("format") != "deep32-w2-inputs" or meta.get("sha256") != input_sha:
        raise E.Refused("inputs manifest format or hash differs")
    for key in ("generation", "corpus_fingerprint"):
        if meta.get(key) != root_meta[key]:
            raise E.Refused("inputs manifest does not name this root")
    db_sha = E.sha_file(root / "sqlite/rag.db")
    if meta.get("root_db_sha256") != db_sha:
        raise E.Refused("inputs manifest root database hash differs")
    report_sha = E.sha_file(root / "BUILD_REPORT.json")
    if meta.get("root_report_sha256") != report_sha:
        raise E.Refused("inputs manifest root report hash differs")
    rows = []
    with inputs.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise E.Refused("blank input line")
            row = json.loads(line)
            if not isinstance(row, dict) or set(row) != {"chunk_id", "embedding_text"}:
                raise E.Refused("input row does not have the exact input schema")
            if not isinstance(row["chunk_id"], str) or not isinstance(row["embedding_text"], str):
                raise E.Refused("input row has a non-string id or text")
            rows.append(row)
    ids = [r["chunk_id"] for r in rows]
    if len(rows) != meta.get("rows") or ids != sorted(set(ids)):
        raise E.Refused("input count, order or uniqueness differs")
    if set(ids) != set(E.read_root(root)):
        raise E.Refused("inputs do not cover the root's chunks exactly")
    gates = gates or load_gates(gate_dir)
    terms = terms or gates.lib.load_terms(str(terms_dir) if terms_dir else gates.lib.TERMS_DIR)
    b = gates.builder.Builder(terms)
    kept = set()
    for block in blocks:
        try:
            kept |= b.block(str(block))
        except gates.builder.Refused:
            raise E.Refused("kept block gate refused; no split written") from None
    if b.findings or not kept:
        raise E.Refused("kept block gate refused; no split written")
    source_ok = b.check_path(str(inputs), "none")
    global_findings = list(b.findings)
    if os.path.islink(inputs) or not os.path.isfile(inputs):
        source_ok = False
        global_findings.insert(0, {"refused": "not a regular file"})
    b.findings.clear()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="w2-split-", dir=out.parent) as scratch:
        work = Path(scratch)
        candidate = work / "candidate.jsonl"
        friend, home = [], []
        reasons = collections.Counter()
        for row in rows:
            # The existing sample gate is all-or-nothing for a file. Giving it
            # one row identifies that row's unchanged verdict for the split.
            write_rows(candidate, [row])
            one = gates.builder.Builder(terms)
            if source_ok:
                one.sample(str(candidate), str(root), kept)
                findings = one.findings
                if not findings:
                    check = gates.cleaner.check((str(candidate), "w2-inputs.jsonl"), one.entries,
                                                terms, 256 * 1024 * 1024)
                    findings = [check] if "refused" in check else []
            else:
                findings = global_findings
            if findings:
                home.append(row)
                # Builder.sample also supplies an aggregate count message;
                # only the row verdict is useful as a stable refusal reason.
                reason = str(findings[0].get("refused", "gate refused"))
                reasons[reason] += 1
            else:
                friend.append(row)
        friend_path, home_path = work / "friend.jsonl", work / "home.jsonl"
        write_rows(friend_path, friend)
        write_rows(home_path, home)
        # Repeat the exact sample-leg allowlist + clean check on the complete
        # outgoing file; per-row checks can never replace this final check.
        if friend:
            b.sample(str(friend_path), str(root), kept)
            if b.findings:
                raise E.Refused("combined friend sample gate refused")
            check = gates.cleaner.check((str(friend_path), "w2-inputs.jsonl"), b.entries,
                                        terms, 256 * 1024 * 1024)
            if "refused" in check:
                raise E.Refused("combined friend clean-inputs gate refused")
        cross = sorted(friend, key=lambda r: hashlib.sha256(
            ("deep32-w2-cross/1:" + r["chunk_id"]).encode("utf-8")).hexdigest())[:cross_rows]
        cross.sort(key=lambda r: r["chunk_id"])
        write_rows(work / "cross.jsonl", cross)
        # The gate records its input file path as provenance. Name the final
        # output, rather than a temporary path, in the persistent allowlist.
        al = allowlist_record(b)
        for source in al["sources"]:
            if source["kind"] == "sample":
                source["path"] = gates.builder.rel(str(out / "friend.jsonl"))
        for entry in al["entries"].values():
            entry["sources"] = [gates.builder.rel(str(out / "friend.jsonl"))
                                if s == gates.builder.rel(str(friend_path)) else s for s in entry["sources"]]
        (work / "friend-allowlist.json").write_bytes(
            (json.dumps(al, ensure_ascii=True, sort_keys=True, indent=1) + "\n").encode("ascii"))
        record = {"format": "deep32-w2-split", "format_version": 1,
                  "generation": meta["generation"], "corpus_fingerprint": meta["corpus_fingerprint"],
                  "model_id": meta["model_id"], "model_revision": meta["model_revision"],
                  "embedding_text_hash": "sha256:utf-8:exact",
                  "input_rows": len(rows), "inputs_sha256": input_sha,
                  "inputs_manifest_sha256": E.sha_file(manifest), "root_db_sha256": db_sha,
                  "root_report_sha256": report_sha,
                  "friend": {"rows": len(friend), "sha256": E.sha_file(friend_path)},
                  "home": {"rows": len(home), "sha256": E.sha_file(home_path)},
                  "cross": {"rows": len(cross), "sha256": E.sha_file(work / "cross.jsonl"),
                            "selection": "sha256:deep32-w2-cross/1:<chunk_id>; ascending hash; then id order"},
                  "refusals": dict(sorted(reasons.items())),
                  "gate": {name: E.sha_file(gate_dir / name) for name in
                           ("clean-inputs.py", "allowlist-build.py", "gatelib.py")},
                  "terms": dict(sorted(terms.files.items())),
                  "friend_allowlist_sha256": E.sha_file(work / "friend-allowlist.json")}
        if (E.sha_file(root / "sqlite/rag.db") != db_sha or E.sha_file(inputs) != input_sha
                or E.sha_file(root / "BUILD_REPORT.json") != report_sha):
            raise E.Refused("root or input changed during split")
        candidate.unlink()
        (work / "split.json").write_bytes(
            (json.dumps(record, ensure_ascii=True, sort_keys=True, indent=1) + "\n").encode("ascii"))
        # TemporaryDirectory is an empty shell after this rename; its cleanup
        # tolerates the source path having gone away.
        os.rename(work, out)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--inputs-manifest")
    ap.add_argument("--root", required=True)
    ap.add_argument("--block", action="append", required=True)
    ap.add_argument("--gate-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--terms")
    ap.add_argument("--cross-rows", type=int, default=500)
    try:
        a = ap.parse_args(argv)
        rec = split(Path(a.inputs), Path(a.root), [Path(p) for p in a.block], Path(a.gate_dir), Path(a.out),
                    inputs_manifest=Path(a.inputs_manifest) if a.inputs_manifest else None,
                    terms_dir=Path(a.terms) if a.terms else None, cross_rows=a.cross_rows)
    except E.Refused as exc:
        print("split_friend: REFUSED " + str(exc), file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError, sqlite3.Error):
        print("split_friend: REFUSED invalid input or friend gate refusal; no text printed", file=sys.stderr)
        return 3
    print(json.dumps(rec, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
