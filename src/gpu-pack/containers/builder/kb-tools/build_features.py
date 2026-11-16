#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from vendor_common import (DEFAULT_MANIFESTS, DEFAULT_SRC, KB_DIR, CorpusError, Doc, Release, discover,
                          doc_platforms, find_releases, load_manifest, preferred_release, read_doc, source_record)
from kbcommon import write_output

GENERATOR = "staging/kb/build_features.py v1"
TITLE = re.compile(r"^(New )?Feature (History|Information)\b", re.I)
TABLE_CAPTION = re.compile(r"^Table \d+:\s.*\bFeature (History|Information)\b", re.I)
BOILER = re.compile(r"^(This table (provides|describes|lists)|These features are available|The following table "
                    r"(provides|lists)|Use (the )?Cisco Feature Navigator|Table \d+:|Lists the development|"
                    r"An account on Cisco\.com)", re.I)
HEADER = re.compile(r"^(?:(?:Feature|Name|FeatureName|Information|Configuration|Software|Releases?|Description|Platform)"
                    r"\s*)+$", re.I)
END_OF_TABLE = re.compile(r"^((\d+|[a-z])\. |Note\b|Figure \d+|Perform |Before you begin|Procedure|Example:|SUMMARY STEPS)")
PROSE_AFTER_RELEASE = re.compile(r"^([,:]|(and|or) (earlier|later|higher)|onwards|and later)", re.I)
PIPE_HEADER = re.compile(r"^(?:Release|Releases|Feature(?: Name)?|Feature Information|Description)"
                         r"(?: \| (?:Release|Releases|Feature(?: Name)?|Feature Information|Description))+$", re.I)
REGION_END = re.compile(r"^C H A P T E R\b|^#{1,6} ")
MAX_INFO = 300


@dataclass
class Row:
    feature: str
    found: list[tuple[Release, int]]   # each release of the row with the line it is on
    info: str

    @property
    def releases(self) -> list[Release]:
        return [r for r, _ in self.found]

    def record(self, doc_key: str) -> dict | None:
        best = preferred_release(self.releases)
        if best is None:
            return None
        info = self.info
        if len({(r.train, r.version) for r in self.releases}) > 1:
            info = "Releases: " + "; ".join(dict.fromkeys(r.version for r in self.releases)) + ". " + info
        line = next(ln for r, ln in self.found if r is best)
        return {"doc_key": doc_key, "feature": self.feature, "release": best.version,
                "info": clip(info, MAX_INFO), "line": line}


@dataclass
class Table:
    start: int                     # first line of the table region
    end: int                       # last line of the region
    scope: tuple[int, int]         # line range of the chapter the table describes
    rows: list[Row] = field(default_factory=list)


def clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[: n - 1].rsplit(" ", 1)[0]
    return cut + "\u2026"


NOT_A_NAME = re.compile(
    r"^(This|These|The following|In|From|Starting|Support (for|was)|No commands|Note|Tip|Caution|Warning|Important"
    r"|Cisco IOS|Cisco (ASR|ISR|CSR|Catalyst \d|Cloud Services|Nexus|Integrated|Aggregation|\d{3,4}))\b"
    r"|^[\d.()]+[A-Z]{0,4}\d?$"                                  # a release fragment ("12.2", "3.2SG")
    r"|^(S|T|M|E|SG|ST|SE|SY\d?|SB\d?|SX[A-J]?\d?|SR[A-E]?\d?|XE|EA)$")   # a train suffix split off its release


def looks_like_name(text: str) -> bool:
    """A feature-name cell: short, not a sentence, a bullet, a platform list or a release fragment."""
    words = text.split()
    if not words or len(words) > 16 or len(text) > 140 or text.endswith((".", ":", ",")):
        return False
    return (text[0].isupper() or text[0].isdigit()) and not NOT_A_NAME.match(text)


def split_name(text: str) -> tuple[str, str]:
    """(sentence part, name part) of a cell where PDF extraction glued an info sentence before the name."""
    head, sep, tail = text.rpartition(". ")
    return (head + ".", tail.strip()) if sep and looks_like_name(tail.strip()) else ("", text)


def leading_releases(text: str, rels: list[Release]) -> tuple[list[Release], str]:
    """Releases that start the line back to back, and the rest of the line after them."""
    lead, pos = [], 0
    for r in rels:
        if text[pos:r.start].strip(" ,;/") not in ("", "and"):
            break
        lead.append(r)
        pos = r.end
    return lead, text[pos:].strip(" ,;") if lead else text


def clean_name(text: str) -> str:
    text = re.sub(r"^(Release|and)\b\s*", "", text.strip(" ,;")).strip()
    return re.sub(r"\s+", " ", text)


def parse_pdf_rows(items: list[tuple[int, str]], name_after: bool) -> list[Row]:
    rows: list[Row] = []
    info: list[str] = []
    rels: list[tuple[Release, int]] = []
    last_name = ""

    def emit(name: str):
        nonlocal info, rels, last_name
        name = clean_name(name) or last_name
        if name and rels:
            rows.append(Row(name, rels, " ".join(info)))
            last_name = name
        info, rels = [], []

    for ln, text in items:
        if rows and END_OF_TABLE.match(text):
            break                                      # body text after a table that has no heading after it
        found = find_releases(text)
        lead, rest = leading_releases(text, found) if found and not text[:found[0].start].strip() else ([], text)
        if lead and PROSE_AFTER_RELEASE.match(rest):
            lead = []                                  # "Cisco vManage Release 20.6.1 and earlier: ..." is prose
        if lead:
            rels.extend((r, ln) for r in lead)
            if rest and looks_like_name(clean_name(rest)):
                emit(rest)
            elif not name_after and info:
                head, name = split_name(info[-1])
                if looks_like_name(name):
                    info[-1:] = [head] if head else []
                    emit(name)
            continue
        trailing = found and not text[found[-1].end:].strip(" .") and not text.rstrip().endswith(".")
        if trailing:
            first = found[-1]
            for r in reversed(found[:-1]):             # a run of releases at the end of the line
                if text[r.end:first.start].strip(" ,;/") in ("", "and"):
                    first = r
                else:
                    break
            head, name = split_name(text[:first.start].strip())
            if looks_like_name(name):
                if rels:
                    emit(last_name)
                if head:
                    info.append(head)
                rels = [(r, ln) for r in found if r.start >= first.start]
                emit(name)
                continue
        if rels:                                       # releases seen: this line names them, or starts a new row
            if name_after and looks_like_name(text):
                emit(text)
                continue
            emit(last_name)
        info.append(text)
    if rels:
        emit(last_name)
    return rows


def parse_pipe_rows(items: list[tuple[int, str]]) -> list[Row]:
    rows: list[Row] = []
    last: list[Release] = []
    for ln, text in items:
        cells = [c.strip() for c in text.split(" | ")]
        if len(cells) < 2:
            continue
        idx = next((i for i, c in enumerate(cells) if (f := find_releases(c)) and f[0].start == 0), None)
        if idx is not None:
            last = find_releases(cells[idx])
            rest = cells[:idx] + cells[idx + 1:]
        elif last:
            rest = cells                               # rowspan: the release cell belongs to the row above
        else:
            continue
        if rest and last:
            rows.append(Row(clean_name(rest[0]), [(r, ln) for r in last], " ".join(rest[1:])))
    return rows


def table_regions(doc: Doc) -> list[tuple[int, int]]:
    """(first, last) line of each feature table: from its "Feature History|Information ..." heading or its
    "Table N: Feature ..." caption to the next heading / chapter marker."""
    regions, n, i = [], len(doc.lines), 1
    while i <= n:
        text = doc.lines[i - 1]
        heading = not doc.in_fence[i - 1] and text.startswith("#")
        title = text.lstrip("#").strip() if heading else ""
        if (heading and TITLE.match(title)) or (not doc.in_fence[i - 1] and TABLE_CAPTION.match(text)):
            j = i + 1
            while j <= n and (doc.in_fence[j - 1] or not REGION_END.match(doc.lines[j - 1])):
                j += 1
            regions.append((i, j - 1))
            i = j
        else:
            i += 1
    return regions


def scope_for(doc: Doc, start: int) -> tuple[int, int]:
    """The chapter a table describes: the parent section of the table's own heading."""
    h = doc.heading_before(start)
    if h is None:
        return 1, len(doc.lines)
    own_level = h[1] if TITLE.match(h[2]) else h[1] + 1
    parent = next((x for x in reversed(doc.headings) if x[0] <= start and x[1] < own_level), None)
    if parent is None:
        return 1, len(doc.lines)
    end = next((x[0] - 1 for x in doc.headings if x[0] > parent[0] and x[1] <= parent[1]), len(doc.lines))
    return parent[0], end


def parse_doc(doc: Doc) -> list[Table]:
    tables = []
    for start, end in table_regions(doc):
        items, pipe, name_after, seen_rows = [], False, True, False
        for ln in range(start + 1, end + 1):
            text = doc.lines[ln - 1].strip()
            if doc.in_fence[ln - 1] or not text:
                continue
            if PIPE_HEADER.match(text):
                pipe = True
                continue
            if HEADER.match(text):
                name_after = not re.search(r"Releases?$", text, re.I)
                continue
            if BOILER.match(text):
                if seen_rows and re.match(r"Use (the )?Cisco Feature Navigator", text, re.I):
                    break                              # ePub/C9300 tables end with the Navigator note
                continue
            seen_rows = True
            items.append((ln, text))
        rows = parse_pipe_rows(items) if pipe or any(" | " in t for _, t in items[:3]) \
            else parse_pdf_rows(items, name_after)
        if rows:
            tables.append(Table(start, end, scope_for(doc, start), rows))
    return tables


def build(src_dirs: list[Path], manifests: list[Path], out_dir: Path, exclude: Iterable[str] = ()) -> dict:
    manifest = load_manifest(manifests)
    exclude = sorted(set(exclude))
    found = discover(src_dirs, exclude)                 # CorpusError (bad exclude, unknown platform) before parsing
    platforms = doc_platforms(found, manifest)
    records, sources = [], []
    per_doc, per_platform, per_train = Counter(), Counter(), Counter()
    for key, path in found.items():
        doc = read_doc(key, path)
        sources.append(source_record(doc, manifest))
        platform = platforms[key]
        for t in parse_doc(doc):
            for row in t.rows:
                rec = row.record(key)
                if rec:
                    records.append(rec)
                    per_doc[key] += 1
                    per_platform[platform] += 1
                    per_train[preferred_release(row.releases).train] += 1
    counts = {"records": len(records), "docs_with_tables": len(per_doc), "docs_scanned": len(sources),
              "by_platform": dict(per_platform.most_common()), "by_train": dict(per_train.most_common()),
              "by_doc": dict(sorted(per_doc.items())), **({"excluded_doc_keys": exclude} if exclude else {})}
    write_output("feature_history", records, sources, GENERATOR, counts, out_dir)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build feature_history.json from the Cisco docs.")
    ap.add_argument("--src", action="append", type=Path, help="corpus dir(s); later dirs override earlier ones")
    ap.add_argument("--manifest", action="append", type=Path)
    ap.add_argument("--out", type=Path, default=KB_DIR)
    ap.add_argument("--exclude", action="append", default=[], metavar="DOC_KEY",
                    help="leave this doc_key out wherever it is (a retired book; repeatable)")
    args = ap.parse_args(argv)
    try:
        counts = build(args.src or DEFAULT_SRC, args.manifest or DEFAULT_MANIFESTS, args.out, args.exclude)
    except CorpusError as e:
        sys.exit(f"build_features: {e}")
    print(f"feature_history: {counts['records']} records from {counts['docs_with_tables']} of "
          f"{counts['docs_scanned']} docs; by train {counts['by_train']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
