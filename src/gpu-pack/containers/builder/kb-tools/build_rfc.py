#!/usr/bin/env python3
"""Build rfc_index.json and errata.json for the KB (BUILD-P1 section 4).

usage: build_rfc.py [--refresh] [--corpus-dir DIR ...] [--out-dir DIR]

Sources (fetched once, cached in $KB_RAW_DIR, default /var/lib/deep32-forge/kb-work/raw):
- https://www.rfc-editor.org/rfc-index.xml          -> every RFC: status, dates, obsoletes/updates, BCP/STD/FYI
- https://www.rfc-editor.org/api/v1/errata.json     -> the RFC Editor's errata database export; the site's
  long-standing https://www.rfc-editor.org/errata.json answers with a 302 to it
- https://www.rfc-editor.org/errata/rfcN            -> the official errata page of each RFC in CROSSCHECK_RFCS;
  every build checks that the feed has the same errata IDs, status and type (and writes nothing if not)
The errata scope is the corpus RFCs plus the RFCs that directly update or obsolete them.
`in_corpus` is set from the rfcNNNN.txt files in the corpus dirs (default: every existing
/var/lib/deep32-forge/corpus/rfc; pass the corpus package's rfc/ dir to build from the package).
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from kbcommon import KB_DIR, RFC_CORPUS_DIRS, default_dirs, fetch, write_output

RFC_INDEX_URL = "https://www.rfc-editor.org/rfc-index.xml"
ERRATA_URL = "https://www.rfc-editor.org/api/v1/errata.json"
ERRATA_ROOT_URL = "https://www.rfc-editor.org/errata.json"  # entry point; 302 -> ERRATA_URL
ERRATA_EID_URL = "https://www.rfc-editor.org/errata/eid{}"
ERRATA_PAGE_URL = "https://www.rfc-editor.org/errata/rfc{}"
CROSSCHECK_RFCS = (2328, 3209, 4271, 4760, 5340, 7761)  # corpus RFCs whose errata pages are compared with the feed
NS = "{https://www.rfc-editor.org/rfc-index}"
GENERATOR = "staging/kb/build_rfc.py"

STATUS = {
    "INTERNET STANDARD": "Internet Standard", "PROPOSED STANDARD": "Proposed Standard",
    "DRAFT STANDARD": "Draft Standard", "BEST CURRENT PRACTICE": "Best Current Practice",
    "INFORMATIONAL": "Informational", "EXPERIMENTAL": "Experimental", "HISTORIC": "Historic",
    "UNKNOWN": "Unknown",
}
MONTHS = {m: i for i, m in enumerate(("January February March April May June July August September "
                                       "October November December").split(), 1)}
ERRATA_STATUS = ("Verified", "Held for Document Update", "Reported", "Rejected")
ERRATA_TYPE = ("Technical", "Editorial")
SUMMARY_MAX = 300


def rfc_num(doc_id: str):
    """'RFC0791' -> 791; anything else -> None."""
    m = re.fullmatch(r"RFC0*(\d+)", doc_id.strip())
    return int(m.group(1)) if m else None


def series_label(doc_id: str):
    """'BCP0014' -> 'BCP 14', 'STD0008' -> 'STD 8', 'FYI0001' -> 'FYI 1'; else None."""
    m = re.fullmatch(r"(BCP|STD|FYI)0*(\d+)", doc_id.strip())
    return f"{m.group(1)} {m.group(2)}" if m else None


def corpus_rfcs(dirs) -> set:
    out = set()
    for d in dirs:
        for p in Path(d).glob("rfc*.txt"):
            m = re.fullmatch(r"rfc(\d+)\.txt", p.name)
            if m:
                out.add(int(m.group(1)))
    return out


def parse_rfc_index(xml_bytes: bytes, in_corpus: set) -> tuple:
    """Return (records sorted by rfc, stats) for every <rfc-entry> in rfc-index.xml."""
    root = ET.fromstring(xml_bytes)
    stats = Counter()
    records = []

    def ids(entry, tag):
        el = entry.find(NS + tag)
        return [d.text or "" for d in el.findall(NS + "doc-id")] if el is not None else []

    def rfc_list(entry, tag):
        nums = [rfc_num(d) for d in ids(entry, tag)]
        stats["non_rfc_relations_dropped"] += sum(1 for n in nums if n is None)
        return sorted({n for n in nums if n is not None})

    for entry in root.findall(NS + "rfc-entry"):
        n = rfc_num(entry.findtext(NS + "doc-id", ""))
        if n is None:
            stats["bad_doc_id"] += 1
            continue
        raw_status = (entry.findtext(NS + "current-status") or "").strip().upper()
        status = STATUS.get(raw_status, "Unknown")
        if raw_status not in STATUS:
            stats["unmapped_status"] += 1
        month = MONTHS.get((entry.findtext(f"{NS}date/{NS}month") or "").strip())
        year = (entry.findtext(f"{NS}date/{NS}year") or "").strip()
        records.append({
            "rfc": n,
            "title": " ".join((entry.findtext(NS + "title") or "").split()),
            "status": status,
            "date": f"{year}-{month:02d}" if year and month else (year or None),
            "obsoletes": rfc_list(entry, "obsoletes"),
            "obsoleted_by": rfc_list(entry, "obsoleted-by"),
            "updates": rfc_list(entry, "updates"),
            "updated_by": rfc_list(entry, "updated-by"),
            "also": [s for s in (series_label(d) for d in ids(entry, "is-also")) if s],
            "in_corpus": n in in_corpus,
        })
    stats["not_issued_skipped"] = len(root.findall(NS + "rfc-not-issued-entry"))
    records.sort(key=lambda r: r["rfc"])
    return records, stats


def errata_scope(index: list) -> dict:
    """{rfc: reason} for corpus RFCs and the RFCs that directly update or obsolete them."""
    scope = {}
    for r in index:
        if r["in_corpus"]:
            scope[r["rfc"]] = "corpus"
    for r in index:
        if r["in_corpus"]:
            for n in r["updated_by"]:
                scope.setdefault(n, "updates-corpus")
            for n in r["obsoleted_by"]:
                scope.setdefault(n, "obsoletes-corpus")
    return scope


# Where a bare section reference may start: the start of the text (optionally "In "), or after
# "Section(s) ", "section-", "Sec." or "\xa7".
_LEAD = '(?:^(?i:in\\s+)?|\\b(?i:sections?)[\\s-]+|\\b(?i:sec)\\.\\s*|\xa7\\s*)'
_SECTION_FORMS = (
    # 'Appendix A.1', 'Appendix A2', 'Annex B.1', 'Appendix B', 'APPENDIX II', 'Appendix 1'
    re.compile(r"(?i)\b(?:appendix|annex)\s+(?:(?P<al>[A-Z])(?:\.\s?|(?=\d))(?P<an>\d+(?:\.\d+)*)|(?P<ax>[IVX]+|[A-Z]|\d+))\b"),
    # 'A.5.1', 'In Section A.1:', 'B2.2', 'D2', 'A. 4.': a capital letter and a number is an appendix subsection
    re.compile(_LEAD + r"(?P<al>[A-Z])(?:\.\s?|(?=\d))(?P<an>\d+(?:\.\d+)*)"),
    # '9.1.2', 'Section 4.2', 'section-4.1', 'Sec. 4.2', '\xa73'
    re.compile(_LEAD + r"(?P<num>\d+(?:\.\d+)*)"),
    re.compile(r"(?i)\b(?P<abstract>abstract)\b"),
)


def normalize_section(raw: str):
    """Map the free-text errata 'section' to an RFC section anchor ('9.1.2', 'appendix-a.1', 'abstract') or None.

    When the text names several sections, the first one wins. Appendix anchors are 'appendix-<letter or
    numeral>[.n...]' as written in the erratum ('Appendix 1' stays 'appendix-1' even if the RFC uses 'I')."""
    s = " ".join((raw or "").split())
    if not s or s.upper() == "GLOBAL":
        return None
    best = None
    for form in _SECTION_FORMS:
        m = form.search(s)
        if m and (best is None or m.start() < best.start()):
            best = m
    if best is None:
        return None
    g = best.groupdict()
    if g.get("abstract"):
        return "abstract"
    if g.get("num"):
        return g["num"]
    if g.get("al"):
        return f"appendix-{g['al'].lower()}.{g['an']}"
    return f"appendix-{g['ax'].lower()}"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:.") + "\u2026"


def summarize(notes: str, correct_text: str, limit: int = SUMMARY_MAX) -> str:
    """Submitter notes (or the corrected text), plus the verifier's notes when present, within `limit` chars."""
    submitter, _, verifier = (notes or "").partition("--VERIFIER NOTES--")
    base = " ".join(submitter.split()) or (f"Corrected text: {' '.join(correct_text.split())}" if (correct_text or "").strip() else "")
    verifier = " ".join(verifier.split())
    if not verifier:
        return _clip(base or "(no description in the errata record)", limit)
    tail_prefix = "Verifier: "
    if not base:
        return _clip(tail_prefix + verifier, limit)
    head = _clip(base, min(len(base), limit * 3 // 5))
    return _clip(f"{head} {tail_prefix}{verifier}", limit)


def parse_errata(feed: list, scope: dict) -> tuple:
    records, stats = [], Counter()
    for e in feed:
        n = rfc_num(str(e.get("doc-id", "")))
        if n is None:
            stats["bad_doc_id"] += 1
            continue
        if n not in scope:
            continue
        status, etype = e.get("errata_status_code"), e.get("errata_type_code")
        if status not in ERRATA_STATUS or etype not in ERRATA_TYPE:
            stats["unknown_status_or_type"] += 1
            continue
        eid = int(e["errata_id"])
        records.append({
            "id": eid,
            "rfc": n,
            "status": status,
            "type": etype,
            "section": normalize_section(e.get("section") or ""),
            "summary": summarize(e.get("notes") or "", e.get("correct_text") or ""),
            "url": ERRATA_EID_URL.format(eid),
            "date": (e.get("submit_date") or None),
        })
    records.sort(key=lambda r: (r["rfc"], r["id"]))
    return records, stats


def errata_page(html: str) -> dict:
    """{errata id: (status, type)} from an official rfc-editor.org/errata/rfcN page (one card per erratum)."""
    text = " ".join(html.split())
    out = {}
    for card in re.split(r'Errata-ID: <a href="/eid', text)[1:]:
        eid = re.match(r"\d+", card)
        status = re.search(r"Status:</dt> <dd[^>]*> <span[^>]*>([^<]+)</span>", card)
        etype = re.search(r"Type:</dt> <dd[^>]*> <span[^>]*>([^<]+)</span>", card)
        if eid:
            out[int(eid.group(0))] = tuple(m.group(1).strip() if m else None for m in (status, etype))
    return out


def crosscheck_errata(feed: list, pages: dict) -> list:
    """Differences between the feed and the official errata pages ({rfc: html}); [] when they agree."""
    problems = []
    for n, html in sorted(pages.items()):
        page = errata_page(html)
        mine = {int(e["errata_id"]): (e.get("errata_status_code"), e.get("errata_type_code"))
                for e in feed if rfc_num(str(e.get("doc-id", ""))) == n}
        if not page:
            problems.append(f"RFC {n}: no errata parsed from its errata page (layout changed?)")
        elif page != mine:
            diff = sorted(k for k in page.keys() | mine.keys() if page.get(k) != mine.get(k))
            problems.append(f"RFC {n}: page lists {len(page)} errata, feed {len(mine)}; differing ids {diff[:10]}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="re-download sources instead of using the raw cache")
    ap.add_argument("--corpus-dir", action="append",
                    help="dir with rfcNNNN.txt (repeatable; default: /var/lib/deep32-forge/corpus/rfc)")
    ap.add_argument("--out-dir", default=str(KB_DIR))
    args = ap.parse_args(argv)
    corpus_dirs = args.corpus_dir or default_dirs(RFC_CORPUS_DIRS)
    in_corpus = corpus_rfcs(corpus_dirs)
    if not in_corpus:
        sys.exit(f"no rfcNNNN.txt files found in {corpus_dirs}")

    xml_bytes, idx_src = fetch(RFC_INDEX_URL, "rfc-index.xml", refresh=args.refresh)
    index, istats = parse_rfc_index(xml_bytes, in_corpus)
    known = {r["rfc"] for r in index}
    missing = sorted(in_corpus - known)
    if missing:
        print(f"WARNING: corpus RFCs not in rfc-index.xml: {missing}", file=sys.stderr)
    by_status = Counter(r["status"] for r in index)
    corpus_recs = [r for r in index if r["in_corpus"]]
    icounts = {
        "rfcs": len(index), "in_corpus": len(corpus_recs), "corpus_files": len(in_corpus),
        "corpus_dirs": [Path(d).name for d in corpus_dirs],
        "corpus_missing_from_index": missing, "corpus_obsoleted": sum(1 for r in corpus_recs if r["obsoleted_by"]),
        "with_also": sum(1 for r in index if r["also"]), "by_status": dict(sorted(by_status.items())),
        **dict(istats),
    }

    feed_bytes, err_src = fetch(ERRATA_URL, "errata.json", refresh=args.refresh)
    feed = json.loads(feed_bytes)
    pages, page_srcs = {}, []
    for n in CROSSCHECK_RFCS:
        data, src = fetch(ERRATA_PAGE_URL.format(n), f"errata-rfc{n}.html", refresh=args.refresh)
        pages[n] = data.decode("utf-8", "replace")
        page_srcs.append({**src, "role": "cross-check of the feed"})
    problems = crosscheck_errata(feed, pages)
    if problems:
        sys.exit("errata feed disagrees with the official errata pages (nothing written; try --refresh):\n  "
                 + "\n  ".join(problems))
    scope = errata_scope(index)
    errata, estats = parse_errata(feed, scope)
    ecounts = {
        "feed_records": len(feed), "errata": len(errata),
        "scope_rfcs": len(scope), "scope_by_reason": dict(Counter(scope.values())),
        "rfcs_with_errata": len({e["rfc"] for e in errata}),
        "by_status": dict(Counter(e["status"] for e in errata)),
        "by_status_type": {f"{s}/{t}": c for (s, t), c in sorted(Counter((e["status"], e["type"]) for e in errata).items())},
        "section_null": sum(1 for e in errata if e["section"] is None),
        "html_crosscheck": {f"RFC {n}": len(errata_page(pages[n])) for n in CROSSCHECK_RFCS},
        **dict(estats),
    }
    write_output("rfc_index", index, [idx_src], GENERATOR, icounts, args.out_dir)
    write_output("errata", errata, [{**err_src, "entry_point": ERRATA_ROOT_URL}] + page_srcs, GENERATOR, ecounts, args.out_dir)
    print(f"rfc_index.json: {len(index)} RFCs ({len(corpus_recs)} in corpus, {icounts['corpus_obsoleted']} of them obsoleted)")
    print(f"errata.json: {len(errata)} errata for {ecounts['rfcs_with_errata']} RFCs (scope {len(scope)} RFCs); {ecounts['by_status']}")


if __name__ == "__main__":
    main()
