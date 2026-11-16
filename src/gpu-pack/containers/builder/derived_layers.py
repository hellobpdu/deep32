#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import re
import shlex
from pathlib import Path

KEPT = ("rfc", "cisco", "powershell", "system")
CUT_COLLECTIONS = ()
CUT_PLATFORMS = ()
LAYERS = ("rfc_index", "errata", "iana", "glossary", "cli_index", "feature_history")
DEFAULT_CATEGORIES = (            # the bundle tool kb-bundle/kb_rules.json glossary_categories (the C3 positive list)
    "automation", "bgp", "eigrp", "fhrp", "ip", "ipv6", "ise-message", "isis", "l2", "mpls", "multicast", "ops",
    "ospf", "policy", "qos", "rip", "routing", "sda", "sdwan", "security", "services", "vpn")
REF_FORMS = (r"^doc:[a-z0-9][a-z0-9-]*$", '^RFC [0-9]+(?: \xa7[A-Za-z0-9._-]+)?$', r"^(IEEE|ISO/IEC) \S.*$")



GOV_TEXT = None


class Bad(Exception):
    pass


def load_rules(path: str | None) -> tuple[set, list]:
    if not path:
        return set(DEFAULT_CATEGORIES), [re.compile(p) for p in REF_FORMS]
    obj = json.loads(Path(path).read_bytes())
    return set(obj["glossary_categories"]), [re.compile(p) for p in obj["glossary_ref_patterns"]]


def load_manifest(path: Path) -> dict[str, str]:
    entries = json.loads(path.read_bytes())
    kept = {}
    for e in entries:
        if e.get("doc_type") not in KEPT:
            raise Bad(f"{path}: a manifest entry of collection {e.get('doc_type')!r}: the manifest must be the kept one")
        kept[e["doc_key"]] = e["doc_type"]
    return kept


def load_cut_keys(path: str | None) -> set[str]:
    if not path:
        return set()
    out = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def read_layer(kb: Path, name: str) -> list | None:
    p = kb / f"{name}.json"
    if not p.is_file():
        return None
    rows = json.loads(p.read_bytes())
    if not isinstance(rows, list):
        raise Bad(f"{p}: not a list")
    return rows


def doc_refs(row: dict) -> list[str]:
    return [r[4:] for r in row.get("refs") or [] if isinstance(r, str) and r.startswith("doc:")]


def row_findings(name: str, row, kept: dict, cut: set, cats: set, forms: list) -> list[str]:
    """Rule names this row breaks (empty: clean)."""
    if not isinstance(row, dict):
        return ["not-an-object"]
    found = []
    key = row.get("doc_key")
    coll = row.get("doc_type") or row.get("collection")
    if coll is not None and coll not in KEPT:
        found.append("cut-collection")
    if name in ("cli_index", "feature_history"):
        if key in cut:
            found.append("cut-key")
        elif key not in kept:
            found.append("document-not-kept")
        if row.get("platform") and row.get("platform") not in ('cat9300', 'cat9400', 'cat9500', 'cat9600', 'cat9k', 'sd-access', 'sdwan', 'catalyst-center', 'iosxe-router', 'iosxr', 'nxos', 'iosxe', 'ise', 'ios', 'cisco'):
            found.append("cut-platform")
    if name == "glossary":
        refs = row.get("refs") or []
        if row.get("category") not in cats:
            found.append("category-not-allowed")
        if not refs:
            found.append("no-citation")
        for r in refs:
            if not isinstance(r, str) or not any(f.match(r) for f in forms):
                found.append("citation-form")
                break
    if name in ("glossary", "iana"):
        for k in doc_refs(row):
            if k in cut:
                found.append("cut-key")
            elif k not in kept:
                found.append("document-not-kept")
    return sorted(set(found))


def check(kb: Path, manifest: Path, rules: str | None, cut_keys: str | None) -> dict:
    kept, cut = load_manifest(manifest), load_cut_keys(cut_keys)
    cats, forms = load_rules(rules)
    report = {"format": "deep32-derived-check", "format_version": 1, "files": {}, "refused": 0, "examples": []}
    for name in LAYERS:
        rows = read_layer(kb, name)
        if rows is None:
            report["files"][name] = {"present": False}
            continue
        by_rule: dict[str, int] = {}
        for i, row in enumerate(rows):
            for rule in row_findings(name, row, kept, cut, cats, forms):
                by_rule[rule] = by_rule.get(rule, 0) + 1
                if len(report["examples"]) < 20:
                    report["examples"].append({"file": f"{name}.json", "row": i, "rule": rule})
        report["files"][name] = {"present": True, "rows": len(rows), "refused_by_rule": by_rule}
        report["refused"] += sum(by_rule.values())
    report["status"] = "pass" if report["refused"] == 0 else "refused"
    return report


def dumps_like(original: bytes, value) -> bytes:
    """Re-serialise as the builders do (one-space indent, UTF-8, final newline) unless the file used another
    indent; the bytes of an unchanged file are never rewritten."""
    indent = 2 if original.startswith(b"[\n  ") or original.startswith(b"{\n  ") else 1
    return (json.dumps(value, indent=indent, ensure_ascii=False) + "\n").encode("utf-8")


def prune(kb: Path, manifest: Path, out: Path, rules: str | None, cut_keys: str | None) -> dict:
    kept, cut = load_manifest(manifest), load_cut_keys(cut_keys)
    cats, forms = load_rules(rules)
    if out.exists() and any(out.iterdir()):
        raise Bad(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    report = {"format": "deep32-derived-prune", "format_version": 1, "files": {}}
    for name in LAYERS:
        src = kb / f"{name}.json"
        if not src.is_file():
            continue
        raw = src.read_bytes()
        rows = json.loads(raw)
        kept_rows, stats = [], {"in": len(rows), "dropped": {}, "trimmed": 0}

        def drop(reason):
            stats["dropped"][reason] = stats["dropped"].get(reason, 0) + 1

        for row in rows:
            if name in ("cli_index", "feature_history"):
                if row.get("doc_key") not in kept or row.get("doc_key") in cut:
                    drop("document-not-kept")
                    continue
                if row.get("platform") and row.get("platform") not in ('cat9300', 'cat9400', 'cat9500', 'cat9600', 'cat9k', 'sd-access', 'sdwan', 'catalyst-center', 'iosxe-router', 'iosxr', 'nxos', 'iosxe', 'ise', 'ios', 'cisco'):
                    drop("cut-platform")
                    continue
            if name == "glossary":
                if row.get("category") not in cats:
                    drop("category-not-allowed")
                    continue
                refs = [r for r in row.get("refs") or [] if isinstance(r, str) and any(f.match(r) for f in forms)
                        and not (r.startswith("doc:") and (r[4:] not in kept or r[4:] in cut))]
                if not refs:
                    drop("no-kept-citation")
                    continue
                if refs != row.get("refs"):
                    row = dict(row, refs=refs)
                    stats["trimmed"] += 1
            if name == "iana" and doc_refs(row):
                refs = [r for r in row.get("refs") or []
                        if not (isinstance(r, str) and r.startswith("doc:") and (r[4:] not in kept or r[4:] in cut))]
                if refs != row.get("refs"):
                    row = dict(row, refs=refs)
                    stats["trimmed"] += 1
            kept_rows.append(row)
        stats["out"] = len(kept_rows)
        data = raw if kept_rows == rows else dumps_like(raw, kept_rows)
        (out / f"{name}.json").write_bytes(data)
        meta_src = kb / f"{name}.meta.json"
        if meta_src.is_file():
            meta = json.loads(meta_src.read_bytes())
            if data != raw or any(isinstance(s, dict) and s.get("doc_key") not in kept for s in meta.get("sources") or []):
                meta["sha256"] = hashlib.sha256(data).hexdigest()
                if isinstance(meta.get("sources"), list):
                    meta["sources"] = [s for s in meta["sources"]
                                       if not isinstance(s, dict) or s.get("doc_key") in kept]
                meta["pruned"] = {"rule": "C3: rows cite kept documents only", "rows_in": stats["in"],
                                  "rows_out": stats["out"], "trimmed": stats["trimmed"], "dropped": stats["dropped"]}
                (out / f"{name}.meta.json").write_bytes(dumps_like(meta_src.read_bytes(), meta))
            else:
                (out / f"{name}.meta.json").write_bytes(meta_src.read_bytes())
        report["files"][name] = stats
    (out / "PRUNE.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="ascii")
    return report


def script(a) -> str:
    q = shlex.quote
    kit, corpus, raw, base, sty, work = (q(x) for x in (a.kit, a.corpus, a.raw, a.base, a.styles, a.work))
    if not re.fullmatch(r"[0-9a-f]{16,64}", a.base_sha256):
        raise Bad("--base-sha256 must be 16 to 64 lower-case hex digits")
    ise = (f"--ise-messages {corpus}/cisco/{q(a.ise_messages)}.txt " if a.ise_messages else "")
    lines = [
        "#!/bin/bash",
        "# Generated by derived_layers.py script: lookup layers from the approved public corpus.",
        "# Identifier terms come from the Cisco ISE message source. The offline raw cache is",
        "# filled and pinned before this (the build procedure S3-P3).",
        "set -eu -o pipefail",
        f"export KB_RAW_DIR={raw}",
        f"OUT={work}/out; FULL={work}/cli-full; IDS={work}/glossary_ids.txt",
        f"for d in rfc cisco powershell system; do [ -d {corpus}/$d ] || {{ echo \"missing {a.corpus}/$d\"; exit 2; }}; done",
        f"for d in {corpus}/*/; do case \"$(basename \"$d\")\" in rfc|cisco|powershell|system|kb) ;; *) echo \"REFUSE: unapproved source directory\"; exit 2;; esac; done",

        f"rm -rf \"$OUT\" \"$FULL\" {work}/pruned; mkdir -p \"$OUT\" \"$FULL\"",
        # The KB builders read production's manifest layouts (vendor_common.load_manifest: {"cisco": {...}} or
        # {"documents": [{doc_key, collection, ...}]}); the corpus manifest is the engine's list form. The v2 layout
        # is the list with collection = doc_type (S3-P4 lab run, 2026-11-15).
        f"python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); "
        f"json.dump({{\"documents\": [dict(e, collection=e[\"doc_type\"]) for e in d]}}, open(sys.argv[2], \"w\"))' "
        f"{corpus}/manifest.json {work}/kb-manifest.json",
        # build_glossary.py always reads glossary_terms.txt from its own folder (KB_DIR), so the builders run from a
        # per-run copy of the kit with the runtime kept term list (the shipped kit has no source lists).
        f"python3 -I -B {q(a.tools)}/derived_layers.py terms --manifest {corpus}/manifest.json "
        f"--terms {q(a.terms)} --out {work}/glossary_terms.kept.txt --json",
        f"rm -rf {work}/kit-run; cp -R {kit} {work}/kit-run; cp {work}/glossary_terms.kept.txt {work}/kit-run/glossary_terms.txt",
        f"cd {work}/kit-run",
        f"python3 -B -E -s build_features.py --src {corpus}/cisco --manifest {work}/kb-manifest.json --out \"$OUT\"",
        f"python3 -B -E -s build_cli.py --src {corpus}/cisco --manifest {work}/kb-manifest.json --rules v3 --styles {sty} --out \"$FULL\"",
        f"python3 -B -E -s layer_cli.py --base {base} --base-sha256 {a.base_sha256} --build \"$FULL\" --out \"$OUT\"",
        f"python3 -B -E -s build_rfc.py --corpus-dir {corpus}/rfc --out-dir \"$OUT\"",
        f"python3 -B -E -s gen_id_terms.py {ise}--out \"$IDS\"",
        f"python3 -B -E -s build_glossary.py --cisco-dir {corpus}/cisco --doc-dir {corpus}/rfc --doc-dir {corpus}/powershell "
        f"--doc-dir {corpus}/system --terms \"$IDS\" --out-dir \"$OUT\"",
        f"python3 -B -E -s build_iana.py --offline --out-dir \"$OUT\"",
        f"python3 -I -B {q(a.tools)}/derived_layers.py prune --kb \"$OUT\" --manifest {corpus}/manifest.json --out {work}/pruned --json",
        f"python3 -I -B {q(a.tools)}/derived_layers.py check --kb {work}/pruned --manifest {corpus}/manifest.json --json",
        f"echo \"derived layers ready: {a.work}/pruned (copy into the corpus package as kb/)\"",
    ]
    return "\n".join(lines) + "\n"


def kept_terms(manifest: Path, terms: Path, out: Path, extras: list | None = None) -> dict:
    """Public knowledge-base source helper."""
    keep = {e["doc_key"] for e in json.loads(manifest.read_bytes())}
    lines_out, changed, refs_dropped = [], [], 0
    for n, line in enumerate(terms.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            lines_out.append(line)
            continue
        f = line.split("|")
        if len(f) < 5:
            raise Bad(f"{terms} line {n}: not a 5-field term line")
        refs = [r.strip() for r in f[3].split(";") if r.strip()]
        left = [r for r in refs if not (r.startswith("doc:") and r[4:] not in keep)]
        if len(left) != len(refs):
            if not left:
                raise Bad(f"{terms} line {n}: {f[0].strip()!r} cites only documents outside the corpus")
            refs_dropped += len(refs) - len(left)
            changed.append(f[0].strip())
            f[3] = " " + "; ".join(left) + " "
            line = "|".join(f)
        lines_out.append(line)
    extra_kept, extra_dropped = 0, {"category": 0, "no-kept-standard-ref": 0, "government-text": 0, "duplicate": 0}
    seen = {l.split("|")[0].strip().lower() for l in lines_out if l.strip() and not l.lstrip().startswith("#")}
    for extra in extras or []:
        lines_out.append(f"# kept slice of {extra.name} (left-out audit C9, lead ruling 2026-11-15): allowed category, "
                         "at least one kept RFC/IEEE/ISO ref, no government wording, no duplicate term")
        for n, line in enumerate(extra.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            f = line.split("|")
            if len(f) < 5:
                raise Bad(f"{extra} line {n}: not a 5-field term line")
            term, cat = f[0].strip(), f[4].strip()
            if cat not in set(DEFAULT_CATEGORIES):
                extra_dropped["category"] += 1
                continue
            refs = [r.strip() for r in f[3].split(";") if r.strip()]
            left = [r for r in refs if not (r.startswith("doc:") and r[4:] not in keep)]
            if not any(re.match(r"^(RFC [0-9]+|IEEE |ISO/IEC )", r) for r in left):
                extra_dropped["no-kept-standard-ref"] += 1
                continue
            if term.lower() in seen:
                extra_dropped["duplicate"] += 1
                continue
            seen.add(term.lower())
            f[3] = " " + "; ".join(left) + " "
            lines_out.append("|".join(f))
            extra_kept += 1
    out.write_text("\n".join(lines_out) + "\n", encoding="utf-8", newline="\n")
    return {"status": "written", "terms_changed": len(changed), "refs_dropped": refs_dropped, "out": str(out),
            "extra_kept": extra_kept, "extra_dropped": extra_dropped}


def pins(raw: Path, record: Path, verify: bool) -> dict:
    files = {p.relative_to(raw).as_posix(): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                              "bytes": p.stat().st_size}
             for p in sorted(raw.rglob("*")) if p.is_file() and not p.name.endswith(".fetch.json")}
    if verify:
        want = json.loads(record.read_bytes())["files"]
        if want != files:
            changed = sorted(k for k in set(want) | set(files) if want.get(k) != files.get(k))
            raise Bad(f"raw cache differs from {record}: {len(changed)} files ({changed[:5]})")
        return {"status": "pass", "files": len(files)}
    record.write_text(json.dumps({"format": "deep32-raw-pins", "format_version": 1, "files": files},
                                 indent=1, sort_keys=True) + "\n", encoding="ascii")
    return {"status": "recorded", "files": len(files)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="derived_layers.py", allow_abbrev=False)
    sub = ap.add_subparsers(dest="verb", required=True)
    s = sub.add_parser("script", allow_abbrev=False)
    for f in ("kit", "corpus", "raw", "base", "base-sha256", "styles", "work", "tools"):
        s.add_argument("--" + f, required=True)
    s.add_argument("--terms", required=True,
                   help="runtime kept term list; never a source list baked into the image")
    s.add_argument("--ise-messages")
    s.add_argument("--out")
    for verb in ("prune", "check"):
        p = sub.add_parser(verb, allow_abbrev=False)
        p.add_argument("--kb", required=True)
        p.add_argument("--manifest", required=True)
        p.add_argument("--rules")
        p.add_argument("--cut-keys")
        p.add_argument("--json", action="store_true")
        if verb == "prune":
            p.add_argument("--out", required=True)
    p = sub.add_parser("terms", allow_abbrev=False)
    p.add_argument("--manifest", required=True)
    p.add_argument("--terms", required=True)
    p.add_argument("--extra", action="append", default=[])
    p.add_argument("--out", required=True)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("pins", allow_abbrev=False)
    p.add_argument("--raw", required=True)
    p.add_argument("--record", required=True)
    p.add_argument("--verify", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if a.verb == "script":
            text = script(a)
            if a.out:
                Path(a.out).write_text(text, encoding="utf-8", newline="\n")
            else:
                sys.stdout.write(text)
            return 0
        if a.verb == "terms":
            rep_ = kept_terms(Path(a.manifest), Path(a.terms), Path(a.out), [Path(x) for x in a.extra])
            print(json.dumps(rep_, sort_keys=True) if a.json else f"terms written to {a.out}")
            return 0
        if a.verb == "pins":
            print(json.dumps(pins(Path(a.raw), Path(a.record), a.verify)))
            return 0
        if a.verb == "prune":
            rep = prune(Path(a.kb), Path(a.manifest), Path(a.out), a.rules, a.cut_keys)
            print(json.dumps(rep, sort_keys=True) if a.json else f"pruned into {a.out}")
            return 0
        rep = check(Path(a.kb), Path(a.manifest), a.rules, a.cut_keys)
        print(json.dumps(rep, sort_keys=True) if a.json else f"{rep['status']}: {rep['refused']} refused")
        return 0 if rep["status"] == "pass" else 3
    except (Bad, OSError, ValueError, KeyError) as exc:
        print(f"derived_layers: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
