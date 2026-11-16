#!/usr/bin/env python3
"""Public knowledge-base source helper."""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from kbcommon import CISCO_CORPUS_DIRS, KB_DIR, default_dirs, load_json, sha256_bytes, utcnow, write_output

SOURCE = KB_DIR / "glossary_terms.txt"
GENERATOR = "staging/kb/build_glossary.py"
CATEGORIES = set(['automation', 'bgp', 'eigrp', 'fhrp', 'ip', 'ipv6', 'ise-message', 'isis', 'l2', 'mpls', 'multicast', 'ops', 'ospf', 'policy', 'qos', 'rip', 'routing', 'sda', 'sdwan', 'security', 'services', 'vpn'])
ID_CATEGORIES = {'ise-message': re.compile(r'ISE \d{4,6}')} 
RFC_REF = re.compile('RFC (\\d+)(?: \xa7[\\w.-]+)?')
DOC_REF = re.compile(r"doc:([a-z0-9][a-z0-9-]*)")
STD_REF = re.compile(r"(IEEE|ISO/IEC) \S.*")


def split_list(field: str) -> list:
    return [x.strip() for x in field.split(";") if x.strip()]


def parse_terms(text: str, seen: dict = None, source: str = "") -> tuple:
    """-> (entries, errors). Entries keep file order. `seen` (casefolded term -> where) carries the uniqueness check
    across files; `source` names a --terms file in the messages and in each entry's "_file"."""
    entries, errors = [], []
    seen = {} if seen is None else seen
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        where = f"{source} line {lineno}" if source else f"line {lineno}"
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 5:
            errors.append(f"{where}: expected 5 '|'-separated fields, got {len(parts)}")
            continue
        term, expansion, aliases, refs, category = parts
        if not term or not expansion:
            errors.append(f"{where}: empty term or expansion")
        if category not in CATEGORIES:
            errors.append(f"{where}: unknown category {category!r}")
        elif category in ID_CATEGORIES and not ID_CATEGORIES[category].fullmatch(term):
            errors.append(f"{where}: {term!r} is not a {category} identifier")
        key = term.casefold()
        if key in seen:
            errors.append(f"{where}: duplicate term {term!r} (first on {seen[key]})")
        seen[key] = where
        alias_list = [a for a in dict.fromkeys(split_list(aliases)) if a.casefold() != key]
        entry = {"term": term, "expansion": expansion, "aliases": alias_list, "refs": split_list(refs),
                 "category": category, "_line": lineno}
        if source:
            entry["_file"] = source
        entries.append(entry)
    return entries, errors


def check_refs(entries: list, rfc_index: dict, doc_keys: set, excluded: set = frozenset()) -> tuple:
    """-> (errors, warnings, ref_counts). A doc: ref must name a doc in doc_keys that is not `excluded`."""
    errors, warnings, counts = [], [], Counter()
    for e in entries:
        where = f"{e['_file']} line {e['_line']}" if "_file" in e else f"line {e['_line']}"
        for ref in e["refs"]:
            if m := RFC_REF.fullmatch(ref):
                counts["rfc"] += 1
                rec = rfc_index.get(int(m.group(1)))
                if rec is None:
                    errors.append(f"{where} {e['term']!r}: {ref} is not in rfc_index.json")
                elif rec["obsoleted_by"]:
                    warnings.append(f"{e['term']!r}: {ref} is obsoleted by "
                                    + ", ".join(f"RFC {n}" for n in rec["obsoleted_by"]))
            elif m := DOC_REF.fullmatch(ref):
                counts["doc"] += 1
                if m.group(1) in excluded:
                    errors.append(f"{where} {e['term']!r}: {ref} is a retired doc (--exclude)")
                elif m.group(1) not in doc_keys:
                    errors.append(f"{where} {e['term']!r}: {ref} is not a corpus doc")
            elif STD_REF.fullmatch(ref):
                counts["standard"] += 1
            else:
                errors.append(f"{where} {e['term']!r}: unrecognised ref {ref!r}")
        if not e["refs"]:
            errors.append(f"{where} {e['term']!r}: no refs")
    return errors, warnings, counts


def shared_aliases(entries: list) -> dict:
    """Alias/term strings that point at more than one entry (intended for ambiguous acronyms)."""
    owners = defaultdict(set)
    for e in entries:
        for name in [e["term"]] + e["aliases"]:
            owners[name.casefold()].add(e["term"])
    return {k: sorted(v) for k, v in sorted(owners.items()) if len(v) > 1}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=str(KB_DIR))
    ap.add_argument("--cisco-dir", action="append", help="Cisco corpus dir with <doc_key>.txt (repeatable)")
    ap.add_argument("--doc-dir", action="append",
                    help="another approved public corpus directory with <doc_key>.txt (repeatable)")
    ap.add_argument("--exclude", action="append", default=[], metavar="DOC_KEY",
                    help="a retired doc_key that doc: refs may not name although a dir still has it (repeatable)")
    ap.add_argument("--terms", action="append", default=[], metavar="FILE",
                    help="more term files in the same format, read after glossary_terms.txt (gen_id_terms.py output)")
    ap.add_argument("--report", action="store_true", help="print each RFC ref with its title")
    args = ap.parse_args(argv)

    raw = SOURCE.read_bytes()
    seen = {}
    entries, errors = parse_terms(raw.decode("utf-8"), seen)
    extra = []                                           # (path, bytes, entries) of each --terms file
    for path in args.terms:
        data = Path(path).read_bytes()
        more, more_errors = parse_terms(data.decode("utf-8"), seen, Path(path).name)
        entries += more
        errors += more_errors
        extra.append((path, data, len(more)))
    index_path = Path(args.out_dir) / "rfc_index.json"
    if not index_path.exists():
        sys.exit(f"{index_path} missing: run build_rfc.py first")
    rfc_index = {r["rfc"]: r for r in load_json(index_path)}
    doc_dirs = (args.cisco_dir or []) + (args.doc_dir or []) or default_dirs(CISCO_CORPUS_DIRS)
    doc_keys = {p.stem for d in doc_dirs for p in Path(d).glob("*.txt")}
    unknown = sorted(set(args.exclude) - doc_keys)       # as in build_cli / build_features: a typo never passes
    if unknown:
        errors.append(f"--exclude names doc_keys that no doc dir has: {', '.join(unknown)}")
    ref_errors, warnings, ref_counts = check_refs(entries, rfc_index, doc_keys, set(args.exclude))
    errors += ref_errors
    if args.report:
        for e in entries:
            titles = [f"{r} = {rfc_index[int(m.group(1))]['title']}" for r in e["refs"]
                      if (m := RFC_REF.fullmatch(r)) and int(m.group(1)) in rfc_index]
            print(f"{e['term']:34} {' || '.join(titles)}")
    if errors:
        sys.exit("glossary errors:\n  " + "\n  ".join(errors))
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    records = [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]
    shared = shared_aliases(records)
    counts = {"terms": len(records), "aliases": sum(len(r["aliases"]) for r in records),
              "by_category": dict(Counter(r["category"] for r in records)), "refs": dict(ref_counts),
              "refs_to_obsoleted_rfcs": warnings, "shared_aliases": shared}
    if extra:                                            # only with the options: else the meta is as before them
        counts["terms_files"] = {Path(p).name: n for p, _, n in extra}
    if args.exclude:
        counts["excluded_doc_keys"] = sorted(set(args.exclude))
    sources = [{"url": "staging/kb/glossary_terms.txt (curated)", "fetched_at": None, "bytes": len(raw), "sha256": sha256_bytes(raw)},
               {"url": "staging/kb/rfc_index.json (ref validation)", "fetched_at": None,
                "sha256": sha256_bytes(index_path.read_bytes()), "checked_at": utcnow()}]
    sources += [{"url": f"{Path(p).name} (generated, --terms)", "fetched_at": None, "bytes": len(d),
                 "sha256": sha256_bytes(d)} for p, d, _ in extra]
    write_output("glossary", records, sources, GENERATOR, counts, args.out_dir)
    print(f"glossary.json: {len(records)} terms, {counts['aliases']} aliases, refs {dict(ref_counts)}, "
          f"{len(warnings)} obsoleted-RFC warnings, {len(shared)} shared aliases")


if __name__ == "__main__":
    main()
