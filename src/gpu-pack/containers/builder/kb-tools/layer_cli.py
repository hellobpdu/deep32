#!/usr/bin/env python3
"""Layer a full cli_index build over a frozen base: every document the base indexes keeps the base's rows, verbatim
and in order; the documents only the build indexes take the build's rows.

Why: build_cli.py reads the evidence of all documents together (style-lexicon votes, syntax and task templates and
their modes, command-reference releases, the IOS command-word lexicon, mode transitions). A round that only adds
documents therefore re-normalizes example rows of documents that did not change. corpus-v7's 41 new texts changed 965
rows of 74 corpus-v6 documents, 187 of them for the worse: "class-map match-any <class-name>" became
"class-map match-any CS" because one new book sets "cs" in bold, and "master branch" became "master <ip-address>".
With the layer, adding documents changes no row of a document already indexed. build_cli.py is unchanged, so the base
stays reproducible from its own inputs.

Rules (each refusal exits 1 and writes nothing):
- The base and the build must hold what their metas say (sha256), and the base must be the pinned one when
  --base-sha256 is given (a prefix of at least 16 hex digits of the base cli_index.json's sha256).
- Both must have been built with the same parser rules (counts.rules); check_cli checks the result with them.
- Every base document must be in the build with the same text sha256. --rebuild DOC_KEY takes that document's rows
  from the build instead (a re-converted text, or an adopted fix); it must name a base document. A base document the
  build lacks is refused: retiring a document needs a new base.
- Output order is build_cli's (doc_key, line, kind, command), stable, so the base rows keep their order. Before
  writing, the record lines of the frozen documents are compared with the base file's, byte for byte.
- Meta: the build's sources (the corpus as built, one per document) and counts, with the row counts recomputed for the
  layered rows. counts.pipeline stays the full build's. counts.layers records the base, the build, which documents
  are frozen, new or rebuilt, and how many frozen documents the full build would have changed.

Usage: layer_cli.py --base KB_DIR --build DIR --out DIR [--base-sha256 HEX] [--rebuild DOC_KEY ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from kbcommon import dump_records, write_output

GENERATOR = "staging/kb/layer_cli.py v1"
NAME = "cli_index"


class LayerError(Exception):
    """The base and the build cannot be layered; nothing is written."""


def load(kb: Path, what: str) -> tuple[list[dict], dict, bytes]:
    body_path, meta_path = kb / f"{NAME}.json", kb / f"{NAME}.meta.json"
    for p in (body_path, meta_path):
        if not p.is_file():
            raise LayerError(f"{what}: {p} is missing")
    body = body_path.read_bytes()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("sha256") != hashlib.sha256(body).hexdigest():
        raise LayerError(f"{what}: {body_path} does not match the sha256 in its meta")
    return json.loads(body.decode("utf-8")), meta, body


def record_lines(body: bytes) -> list[str]:
    """The record lines of a dump_records() file, without the separating commas."""
    lines = body.decode("utf-8").split("\n")
    return [l[:-1] if l.endswith(",") else l for l in lines[1:-2]] if len(lines) > 3 else []


def layer(base_dir: Path, build_dir: Path, out_dir: Path, rebuild=(), base_sha256: str | None = None) -> dict:
    """Write the layered cli_index.json and its meta into out_dir; returns counts.layers."""
    base_rows, base_meta, base_body = load(base_dir, "base")
    build_rows, build_meta, _ = load(build_dir, "build")
    if base_sha256 is not None:
        pin = base_sha256.lower()
        if len(pin) < 16 or not base_meta["sha256"].startswith(pin):
            raise LayerError(f"base: {base_dir} is {base_meta['sha256'][:16]}, not the pinned {pin}")
    rules = (base_meta["counts"].get("rules"), build_meta["counts"].get("rules"))
    if base_rows and rules[0] != rules[1]:
        raise LayerError(f"base rules {rules[0]} differ from the build's {rules[1]}: the frozen rows would be checked "
                         "with rules they were not built with")
    base_src = {s["doc_key"]: s for s in base_meta["sources"]}
    build_src = {s["doc_key"]: s for s in build_meta["sources"]}
    rebuild = sorted(set(rebuild))
    problems = [f"--rebuild {k}: not a base document" for k in rebuild if k not in base_src]
    problems += [f"base document {k} is not in the build (retiring a document needs a new base)"
                 for k in sorted(set(base_src) - set(build_src))]
    problems += [f"base document {k}: its text changed ({base_src[k]['sha256'][:12]} -> "
                 f"{build_src[k]['sha256'][:12]}); name it with --rebuild to take the build's rows"
                 for k in sorted(set(base_src) & set(build_src))
                 if k not in rebuild and base_src[k]["sha256"] != build_src[k]["sha256"]]
    stray = sorted({r["doc_key"] for r in base_rows} - set(base_src))
    problems += [f"base rows of {k}, which the base's sources do not list" for k in stray]
    if problems:
        raise LayerError("; ".join(problems))
    frozen = set(base_src) - set(rebuild)
    new = set(build_src) - set(base_src)
    rows = [r for r in base_rows if r["doc_key"] in frozen] + [r for r in build_rows if r["doc_key"] not in frozen]
    rows.sort(key=lambda r: (r["doc_key"], r["line"], r["kind"], r["command"]))   # build_cli's order (stable)

    by_doc_base, by_doc_build = defaultdict(list), defaultdict(list)
    for r in base_rows:
        by_doc_base[r["doc_key"]].append(r)
    for r in build_rows:
        by_doc_build[r["doc_key"]].append(r)
    differs = sorted(k for k in frozen if by_doc_base[k] != by_doc_build[k])
    layers = {
        "base": {"dir": base_dir.name, "sha256": base_meta["sha256"], "docs": len(base_src), "rows": len(base_rows)},
        "build": {"dir": build_dir.name, "sha256": build_meta["sha256"], "docs": len(build_src),
                  "rows": len(build_rows), "generator": build_meta.get("generator")},
        "frozen_docs": len(frozen), "frozen_rows": sum(len(by_doc_base[k]) for k in frozen),
        "new_docs": len(new), "new_rows": sum(len(by_doc_build[k]) for k in new),
        "rebuilt": {k: {"text_changed": base_src[k]["sha256"] != build_src[k]["sha256"],
                        "base_rows": len(by_doc_base[k]), "build_rows": len(by_doc_build[k])} for k in rebuild},
        # what the full build would have changed: frozen documents whose build rows differ from the base's
        "frozen_docs_build_differs": len(differs),
        "frozen_rows_build": sum(len(by_doc_build[k]) for k in frozen),
        "pipeline": "counts.pipeline is the full build's",
    }
    per_doc = Counter(r["doc_key"] for r in rows)
    counts = dict(build_meta["counts"])
    counts.update({"records": len(rows), "unique_commands": len({r["command"] for r in rows}),
                   "by_kind": dict(Counter(r["kind"] for r in rows).most_common()),
                   "by_platform": dict(Counter(r["platform"] for r in rows).most_common()),
                   "by_platform_kind": dict(sorted(Counter(f"{r['platform']}/{r['kind']}" for r in rows).items())),
                   "with_mode": sum(1 for r in rows if r["mode"]),
                   "with_release": sum(1 for r in rows if r["release"]),
                   "by_doc": dict(sorted((k, per_doc[k]) for k in build_src if per_doc[k])),
                   "layers": layers})
    if not base_src:                     # nothing frozen: the build passes through unchanged
        counts = dict(build_meta["counts"])
    generator = build_meta["generator"] + (f" + {GENERATOR}" if base_src else "")

    # the guarantee, checked before writing: the frozen documents' record lines are the base's, byte for byte, in order
    body = dump_records(rows).encode("utf-8")
    out_lines = record_lines(body)
    kept = [l for l, r in zip(out_lines, rows) if r["doc_key"] in frozen]
    want = [l for l, r in zip(record_lines(base_body), base_rows) if r["doc_key"] in frozen]
    if len(out_lines) != len(rows) or kept != want:
        raise LayerError("the frozen documents' records would not be the base's")
    path = write_output(NAME, rows, build_meta["sources"], generator, counts, out_dir)
    if path.read_bytes() != body:
        raise LayerError(f"{path}: written bytes differ from the checked records (do not publish)")
    return layers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Layer a full cli_index build over a frozen base cli_index.")
    ap.add_argument("--base", type=Path, required=True, help="KB dir of the frozen base (kb-v6)")
    ap.add_argument("--build", type=Path, required=True, help="dir of the full build_cli.py build")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--base-sha256", help="pin: a prefix (16+ hex digits) of the base cli_index.json's sha256")
    ap.add_argument("--rebuild", action="append", default=[], metavar="DOC_KEY",
                    help="take this base document's rows from the build (a re-converted text; repeatable)")
    args = ap.parse_args(argv)
    if args.out.resolve() in (args.base.resolve(), args.build.resolve()):
        sys.exit("layer_cli: --out must differ from --base and --build")
    try:
        layers = layer(args.base, args.build, args.out, args.rebuild, args.base_sha256)
    except LayerError as e:
        sys.exit(f"layer_cli: {e}")
    print(f"cli_index layered: {layers['frozen_docs']} frozen docs ({layers['frozen_rows']} rows from "
          f"{layers['base']['dir']} {layers['base']['sha256'][:16]}), {layers['new_docs']} new docs "
          f"({layers['new_rows']} rows) and {len(layers['rebuilt'])} rebuilt from the build "
          f"{layers['build']['sha256'][:16]}; the build differs on {layers['frozen_docs_build_differs']} frozen docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
