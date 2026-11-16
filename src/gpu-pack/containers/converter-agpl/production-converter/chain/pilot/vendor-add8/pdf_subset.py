#!/usr/bin/env python3
# Copied from ../cisco-add7/pdf_subset.py (round add7) for round add8 by make_tooling_add8.py; the history notes below are add7's and add6's.
# Copied from ../cisco-add6/pdf_subset.py (round add6) for round add8 by make_tooling_add8.py; the history notes below (r1, r2, fixes) are add6's.
"""Page subsets of a fetched PDF (round add8): the pages a catalog entry's "pdf_pages" names, as a new PDF that the
converter reads in place of the whole book, with optional page-edge clipping and reading-order bands. The fetched
original is never changed.

usage (a Python that has PyMuPDF installed):
  pdf_subset.py <in.pdf> <pages|all> <out.pdf> [--outline keep|none] [--clip TOP,BOTTOM] [--bands JSON]

<pages> is the catalog's pdf_pages: comma-separated 1-based PDF page numbers and ranges ('49-51,75-78,206-216'); text
after the list (the catalog's '(printed pages 79-83 and 120-124)') is a note and is ignored. Pages come out in book
order, each once. 'all' keeps every page (for a whole PDF that only needs --clip or --bands).
--outline keep (default): the outline entries that point into the kept pages, re-pointed to their new page numbers.
  Their levels are re-based so the first is level 1 and no entry is more than one level below the entry before it
  (what PyMuPDF set_toc() requires): an entry whose parent was cut becomes a child of the nearest kept entry above
  it. With no kept entry the subset has no outline, and converter v2/v2.1 then takes headings from font sizes.
--outline none: no outline at all (font-size headings).
--clip TOP,BOTTOM (points from the top of the page): the text above TOP and below BOTTOM is removed from every kept
  page (PyMuPDF redaction, text only), so the running header and footer bands are gone. Converter v2.1 matches an
  outline title to the first block on its page with the same text, and a running header that repeats the section
  title ('How to Configure CoPP' at y=33 on the cat6k chapter's page 5, the real heading at y=293) takes the
  heading's place; a header that is not repeated often enough to count as furniture also stays in the text
  ('backup (NetFlow SCTP) through ip route-cache flow ip flow').
--bands {"page": [y, ...]}: reading order for pages whose content stream does not run top to bottom. Each listed page
  (original PDF page number) is split at the given y values (points from the top, in a gap between text blocks) into
  bands; each band becomes its own page, the same size, holding only that band's text at its original position, in
  top-to-bottom band order. Converter v2.1 keeps PyMuPDF's stream order on a page (its docstring, rule 6), and
  FrameMaker modules write a page's new task (heading, SUMMARY STEPS, 'DETAILED STEPS') before the step-table rows
  carried over from the page before, which are printed above it; within a band the stream order is kept, so a
  table's left and right cells are not spliced. Outline entries on a split page go to the band that holds their
  title (else the band of their destination point).
When no outline entry starts on the subset's first page, the record names the outline section that holds it
(context): convert_add8.py puts that title first in the text, as a level-1 heading (finish_add8 context_heading).
The record also lists the hyphen breaks of the kept pages (hyphen_breaks): a line that ends in 'xxx-' followed, in the
same text block, by a line that starts with a lowercase 'yyy', where 'xxxyyy' occurs unbroken elsewhere in the whole
PDF (a broken compound such as 'remote-' + 'site' is listed under hyphen_kept instead); finish_add8's dehyphenate rule
uses them.
Prints one line: pages kept, outline entries kept, the printed page labels of the first and last page, clip, bands and
context; writes the same record as JSON next to the subset (<out>.json).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PAGES = re.compile(r"^\s*(\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*)")
WORD = re.compile(r"[A-Za-z]+")


def parse_pages(spec: str, page_count: int | None = None) -> list[int]:
    """1-based page numbers from a pdf_pages spec, in order, each once; ValueError when there is none or one is out
    of range. 'all' needs page_count and gives every page."""
    if str(spec).strip().lower() == "all" and page_count:
        return list(range(1, page_count + 1))
    m = PAGES.match(str(spec or ""))
    if not m:
        raise ValueError(f"pdf_pages {spec!r}: no page list")
    out: set[int] = set()
    for part in m.group(1).split(","):
        a, _, b = part.strip().partition("-")
        lo, hi = int(a), int(b or a)
        if lo < 1 or hi < lo:
            raise ValueError(f"pdf_pages {spec!r}: bad range {part.strip()!r}")
        out.update(range(lo, hi + 1))
    pages = sorted(out)
    if page_count is not None and pages[-1] > page_count:
        raise ValueError(f"pdf_pages {spec!r}: page {pages[-1]} beyond the {page_count} pages of the PDF")
    return pages


def subset_toc(toc: list, pages: list[int], band_of=None, nbands: dict | None = None) -> list[list]:
    """The outline entries [level, title, page, ...] that point into pages, re-pointed to the subset's page numbers,
    with levels re-based (see the module docstring). With bands, nbands {page: number of bands} and band_of(entry) ->
    the entry's band index on its page."""
    nbands = nbands or {}
    first, n = {}, 1
    for p in pages:
        first[p] = n
        n += nbands.get(p, 1)
    kept = [(e[0], e[1], first[e[2]] + (band_of(e) if band_of and e[2] in nbands else 0))
            for e in toc if e[2] in first]
    out: list[list] = []
    stack: list[int] = []                       # original levels of the kept ancestors of the next entry
    for lvl, title, p in kept:
        while stack and stack[-1] >= lvl:
            stack.pop()
        stack.append(lvl)
        out.append([len(stack), title, p])
    return out


def context(toc: list, pages: list[int]) -> str | None:
    """The title of the outline section that holds the subset's first page when no outline entry starts on that page:
    the last entry, in outline order, that starts on an earlier page."""
    if any(e[2] == pages[0] for e in toc):
        return None
    before = [e[1] for e in toc if 0 < e[2] < pages[0]]
    return before[-1] if before else None


def band_index(y: float, cuts: list[float]) -> int:
    return sum(1 for c in cuts if y >= c)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def hyphen_breaks(doc, pages: list[int]) -> tuple[list[dict], list[str]]:
    """(confirmed breaks, kept compounds) of the given 1-based pages; see the module docstring."""
    vocab: set[str] = set()
    for page in doc:
        vocab.update(w.lower() for w in WORD.findall(page.get_text()))
    found, kept = {}, set()
    for p in pages:
        for b in doc[p - 1].get_text("dict")["blocks"]:
            lines = ["".join(s["text"] for s in ln["spans"]) for ln in b.get("lines", [])]
            for a, nxt in zip(lines, lines[1:]):
                m1 = re.search(r"(?<![\w-])([A-Za-z]+)-\s*$", a)
                m2 = re.match(r"\s*([a-z]+)", nxt)
                if not (m1 and m2):
                    continue
                hyph, word = f"{m1.group(1)}-{m2.group(1)}", m1.group(1) + m2.group(1)
                if word.lower() in vocab:
                    found[hyph] = word
                else:
                    kept.add(hyph)
    return [{"hyphenated": h, "word": w} for h, w in sorted(found.items())], sorted(kept)


def _redact(page, keep_top: float, keep_bottom: float) -> None:
    """Remove the text above keep_top and below keep_bottom (points from the top) from the page."""
    import pymupdf
    r = page.rect
    if keep_top > r.y0:
        page.add_redact_annot(pymupdf.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, keep_top))
    if keep_bottom < r.y1:
        page.add_redact_annot(pymupdf.Rect(r.x0 - 1, keep_bottom, r.x1 + 1, r.y1 + 1))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)


def subset(src: Path, spec: str, dst: Path, outline: str = "keep", clip: tuple | None = None,
           bands: dict | None = None) -> dict:
    """Write the subset PDF to dst and its record to dst with '.json': pages, outline entries kept, printed page labels
    of the first and last page, clip, bands, the context title (context(); None with --outline none) and the hyphen
    breaks."""
    import pymupdf
    doc = pymupdf.open(src)
    pages = parse_pages(spec, doc.page_count)
    toc = doc.get_toc(simple=False)
    labels = (doc[pages[0] - 1].get_label(), doc[pages[-1] - 1].get_label())
    breaks, compounds = hyphen_breaks(doc, pages)
    cuts = {int(p): sorted(float(y) for y in ys) for p, ys in (bands or {}).items()}
    bad = sorted(p for p in cuts if p not in pages)
    if bad:
        raise ValueError(f"bands for pages {bad} that are not kept")

    def band_of(e) -> int:
        """The band of outline entry e: the band holding a block that starts with its title, else its destination."""
        p, title = e[2], _norm(e[1])
        page = doc[p - 1]
        for b in page.get_text("dict")["blocks"]:
            text = _norm(" ".join("".join(s["text"] for s in ln["spans"]) for ln in b.get("lines", [])))
            if title and text.startswith(title):
                y = b["bbox"][1]
                if not clip or clip[0] <= y < clip[1]:
                    return band_index(y, cuts[p])
        dest = e[3] if len(e) > 3 and isinstance(e[3], dict) else {}
        to = dest.get("to")
        return band_index(page.rect.height - to.y, cuts[p]) if to is not None else 0

    nbands = {p: len(c) + 1 for p, c in cuts.items()}
    new_toc = subset_toc(toc, pages, band_of, nbands) if outline == "keep" else []
    ctx = context(toc, pages) if outline == "keep" else None
    doc.select([p - 1 for p in pages])
    top, bottom = (float(clip[0]), float(clip[1])) if clip else (None, None)
    if clip:
        for page in doc:
            _redact(page, top, bottom)
    for i in range(len(pages) - 1, -1, -1):                    # split from the back: indices before i stay put
        p = pages[i]
        if p not in cuts:
            continue
        edges = [-1e9] + cuts[p] + [1e9]
        for _ in cuts[p]:
            doc.fullcopy_page(i, i + 1 if i + 1 < doc.page_count else -1)
        for k in range(len(edges) - 1):
            _redact(doc[i + k], edges[k], edges[k + 1])
    doc.set_toc(new_toc)
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dst, garbage=3, deflate=True)
    rec = {"pages": pages, "outline": len(new_toc), "labels": labels, "context": ctx,
           "clip": [top, bottom] if clip else None, "bands": {str(p): c for p, c in sorted(cuts.items())},
           "page_count": doc.page_count, "hyphen_breaks": breaks, "hyphen_kept": compounds}
    dst.with_suffix(".json").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return rec


def main(argv: list[str]) -> int:
    outline, clip, bands = "keep", None, None
    if "--outline" in argv:
        i = argv.index("--outline")
        outline = argv[i + 1]
        del argv[i:i + 2]
        if outline not in ("keep", "none"):
            raise SystemExit("--outline keep|none")
    if "--clip" in argv:
        i = argv.index("--clip")
        clip = tuple(float(x) for x in argv[i + 1].split(","))
        del argv[i:i + 2]
        if len(clip) != 2 or clip[0] >= clip[1]:
            raise SystemExit("--clip TOP,BOTTOM (points from the top, TOP < BOTTOM)")
    if "--bands" in argv:
        i = argv.index("--bands")
        bands = json.loads(argv[i + 1])
        del argv[i:i + 2]
    src, spec, dst = Path(argv[0]), argv[1], Path(argv[2])
    r = subset(src, spec, dst, outline, clip, bands)
    print(f"pdf_pages: {len(r['pages'])} pages kept ({spec.split('(')[0].strip()}), {r['outline']} outline entries, "
          f"printed {r['labels'][0] or '-'}..{r['labels'][1] or '-'}"
          + (f", clip {r['clip'][0]:g}..{r['clip'][1]:g} pt" if r["clip"] else "")
          + (f", {len(r['bands'])} pages split into bands ({r['page_count']} pages)" if r["bands"] else "")
          + (f", {len(r['hyphen_breaks'])} hyphen breaks" if r["hyphen_breaks"] else "")
          + (f", context {r['context']!r}" if r["context"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
