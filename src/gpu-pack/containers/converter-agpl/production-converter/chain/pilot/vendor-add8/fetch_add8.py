#!/usr/bin/env python3
# Copied from ../cisco-add7/fetch_add7.py (round add7) for round add8 by make_tooling_add8.py; the history notes below are add7's and add6's.
# Copied from ../cisco-add6/fetch_add6.py (round add6) for round add8 by make_tooling_add8.py; the history notes below (r1, r2, fixes) are add6's.
"""Download the corpus-v8 additions (round add8): ../vendor-add/fetch_add5.py (itself fetch_add3.py with the
nesting-aware TechNote TOC cut and fetch=article) plus the add6 additions (points 1-4) and one add8 addition (point 5).
fetch_add.py, fetch_add3.py and fetch_add5.py stay unchanged.

usage: fetch_add8.py <catalog-add8.json> <rfcs-add8-all.txt> <pilot-add8-dir> [--overrides <fetch-overrides-add8.json>]
                     [--only key1,key2] [--force]
       (the CLI of fetch_add3.py plus --overrides; default: fetch-overrides-add8.json next to the catalog, if present)

1. Held entries. An entry is held when it has a non-empty "held" field (a reason string, fetch_add3's rule) or
   "status": "held". Held entries are not fetched; an earlier fetch record of a now-held entry is forgotten and its
   file removed (fetch_add3.main forgets every doc_key that is not in the catalog it is given).
2. Overrides. The catalog is read-only for this round (the gap rounds append to it while fetching runs), so fetch-time
   fixes live in fetch-overrides-add8.json: {"docs": {doc_key: {"catalog": {field: value, ...}, ...}}}. The "catalog"
   fields replace the entry's fields before the fetch (a corrected URL or fetch mode); the other keys of an override
   are conversion rules (convert_add8.py). An override for a doc_key that is not in the catalog is reported, never
   fetched. The effective catalog (held entries out, overrides in) is written to <pilot-add8>/catalog-effective.json
   and handed to fetch_add3.main, so every fetch mode, the cache (an ok record whose file still has its sha256 is not
   downloaded again), the honest user agent and fetch-log.json are fetch_add3's.
3. Chapter headings in upper case. fetch_add.chapter_html normalizes DITA headings (topictitleN -> hN, a sectiontitle
   one level below its topic) with a case-sensitive regex, so a chapter written with <H3 ...> tags (the Nexus 7000 8.x
   MPLS chapter) kept its source levels. chapter_html() here matches the tags in any case and writes them in lower
   case; on a page whose heading tags are lower case its output is fetch_add.chapter_html's, byte for byte.
4. Chapter pages in cisco.com's newer td/docs template (round r2, 2026-10-10: the Catalyst 8500 software configuration
   guide's 'Management Ethernet Interface' chapter). The page has no chapterContent: the chapter title is the
   <h1 id="document-content-heading"> above the body, and the body is <div class="doc-s-markdown"
   id="document-content-body"> up to the scroll-to-top button (or the content footer). Each topic is a plain <hN id=...>
   and its section titles are <h2 class="title sectiontitle">, so a section title goes one level below the topic it
   follows (as fetch_add.chapter_html does for topictitleN/sectiontitle). The page's chrome (toolbar, 'On this page'
   TOC, the AI-summary control, feedback and footer) is outside the body and is not kept. Only a page without
   chapterContent takes this path; a page with neither marker is still a parse error.
5. Plain-text MIB files (round add8: doc_type "mib", the commit-pinned raw.githubusercontent.com/cisco/cisco-mibs
   files). fetch_add3's page mode wants an HTML page with an <h1>, so a mib entry goes to fetch_mib instead: every
   distinct URL of html_url and chapter_urls, in that order, is downloaded as is (honest UA, fa.get) and must be text
   (no NUL byte, UTF-8 or ASCII, no '<html'). The cached original is one JSON container <doc_key>.mib.json,
   {"files": [{"name", "url", "bytes", "sha256", "text"}]}, so the fetch log's sha256 covers every file and each file's
   own sha256 is kept (the catalog's line ranges refer to these exact bytes). The text of a file is its bytes decoded,
   with nothing changed.
6. Chapter pages in cisco.com's pre-2010 /en/US/docs template (round add8: the Catalyst 2960-XR 15.0(2)EX1 file
   management chapter). The page has neither chapterContent nor document-content-body: the chapter is the content of
   <div class="contentMargins"><span class="content"> up to the page footer (<div class="footer">), without the
   closing </span></div>. Its FrameMaker headings (<h2 class="pCT_ChapTitle">, p_H_Head1 ... as <h2>/<h3>) keep
   their tags here; the levels are set by prep_add8's heading_levels rule in the document's override, as for the
   add6 FrameMaker chapters. Only a page with neither newer marker takes this path.
7. fetch=page with "page_end" (a catalog field, set in the document's override; round add8: the CoPP white paper).
   fetch_add3.page_html ends the body at the first footer marker or '</body>' after the first <h1; a page built from
   nested htmlblob documents (each with its own '<!doctype html><html><head>...</head><body>...</body></html>') is cut
   at the first nested '</body>'. With page_end, the body runs from the first <h1 to the first occurrence of the
   page_end text after it (exclusive), and the nested document wrappers inside it (doctype, <html>, <head>...</head>,
   <body>, </body>, </html>) are removed; script/style/header/footer/nav blocks are dropped as page_html does. A
   page_end that is not found is a fetch failure. Entries without page_end are fetched by page_html unchanged.
pdf_pages page subsets, section trims and article de-duplication are conversion steps (convert_add8.py): the cached
original is always the file as cisco.com served it, so its fetch-log sha256 is the provenance.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "vendor-add"))
sys.path.insert(0, str(HERE.parent / "vendor-add3"))
import fetch_add as fa  # noqa: E402
import fetch_add3 as fa3  # noqa: E402
import fetch_add5 as fa5  # noqa: E402

OVERRIDES = "fetch-overrides-add8.json"
EFFECTIVE = "catalog-effective.json"
HEADING = re.compile(r"(?is)<h([1-6])(\b[^>]*)>(.*?)</h\1\s*>")


def is_held(c: dict) -> bool:
    return bool(c.get("held")) or str(c.get("status", "")).lower() == "held"


def read_json(path: Path, tries: int = 5) -> dict:
    """A JSON file that another process may be rewriting: retry a parse error a few times."""
    for i in range(tries):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            if i == tries - 1:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def load_overrides(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    return read_json(path).get("docs") or {}


def effective_catalog(catalog: dict, overrides: dict) -> tuple[list[dict], list[str], list[str]]:
    """(the non-held entries with their override "catalog" fields applied, held doc_keys, override doc_keys that are
    not catalog entries)."""
    docs, held = [], []
    for c in catalog.get("docs") or []:
        if is_held(c):
            held.append(c["doc_key"])
            continue
        o = (overrides.get(c["doc_key"]) or {}).get("catalog") or {}
        docs.append({**c, **o})
    known = {c["doc_key"] for c in catalog.get("docs") or []}
    return docs, held, sorted(k for k in overrides if k not in known)


MD_BODY = 'id="document-content-body"'
MD_ENDS = ('<div class="fw-l-document__scroll-to-top"', '<section class="doc-c-content-footer')
MD_TITLE = re.compile(r'(?is)<h1\b[^>]*\bid="document-content-heading"[^>]*>(.*?)</h1\s*>')


def markdown_chapter_html(t: str) -> str:
    """The chapter of a page in the newer td/docs template (module docstring, point 4)."""
    i, m = t.find(MD_BODY), MD_TITLE.search(t)
    ends = [j for j in (t.find(e, i) for e in MD_ENDS) if j > 0] if i >= 0 else []
    if i < 0 or not m or not ends:
        raise ValueError("no chapterContent in chapter page (and no complete document-content-body)")
    body = t[t.index(">", i) + 1:min(ends)]
    level = 1

    def fix(h):
        nonlocal level
        attrs = h.group(2)
        if "sectiontitle" in attrs:
            n = min(6, level + 1)
        else:
            level = n = int(h.group(1))
        return f"<h{n}{attrs}>{h.group(3)}</h{n}>"
    return f'<h1 class="title topictitle1">{m.group(1).strip()}</h1>\n' + HEADING.sub(fix, body)


LEGACY_BODY = '<div class="contentMargins">'
LEGACY_END = '<div class="footer">'


def legacy_chapter_html(t: str) -> str:
    """The chapter of a page in cisco.com's pre-2010 /en/US/docs template (module docstring, point 6)."""
    i, j = t.find(LEGACY_BODY), t.find(LEGACY_END)
    if i < 0 or j < i:
        raise ValueError("no chapterContent in chapter page (and no complete legacy contentMargins body)")
    body = t[i + len(LEGACY_BODY):j]
    k = body.rfind("</span></div>")
    return body[:k] if k > 0 else body


def chapter_html(page: bytes) -> str:
    """fetch_add.chapter_html, matching the heading tags in any case (see the module docstring, point 3); a page in
    the newer template goes to markdown_chapter_html (point 4), one in the legacy template to legacy_chapter_html
    (point 6)."""
    t = fa.page_text(page)
    i = t.find('id="chapterContent"')
    if i < 0:
        if MD_BODY in t:
            return markdown_chapter_html(t)
        if LEGACY_BODY in t:
            return legacy_chapter_html(t)
        raise ValueError("no chapterContent in chapter page")
    j = t.find('<div class="col narrow-v2"', i)
    body = t[t.index(">", i) + 1:j if j > 0 else len(t)]
    level = 1

    def fix(m):
        nonlocal level
        attrs = m.group(2)
        topic = re.search(r"topictitle(\d)", attrs)
        if topic:
            level = n = min(6, int(topic.group(1)))
        elif "sectiontitle" in attrs:
            n = min(6, level + 1)
        else:
            n = int(m.group(1))
        return f"<h{n}{attrs}>{m.group(3)}</h{n}>"
    return HEADING.sub(fix, body)


def mib_text(data: bytes) -> str:
    """A fetched MIB file as text (module docstring, point 5), or ValueError."""
    if b"\0" in data:
        raise ValueError("NUL byte in a MIB file")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"MIB file is not UTF-8: {e}") from None
    if "<html" in text[:4096].lower():
        raise ValueError("an HTML page, not a MIB file")
    return text


def fetch_mib(c: dict, out: Path) -> dict:
    urls = list(dict.fromkeys([c["html_url"], *(c.get("chapter_urls") or [])]))
    files, parts = [], []
    for u in urls:
        st, data = fa.get(u)
        parts.append({"url": u, "status": st, "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest() if data else None})
        if st != "ok":
            return {"status": "failed", "url": urls[0], "parts": parts, "tried": [f"mib: {st} {u}"]}
        try:
            text = mib_text(data)
        except ValueError as e:
            return {"status": "failed", "url": urls[0], "parts": parts, "tried": [f"mib: {e} {u}"]}
        files.append({"name": u.rsplit("/", 1)[-1], "url": u, "bytes": len(data), "sha256": parts[-1]["sha256"],
                      "text": text})
        time.sleep(0.3)
    body = (json.dumps({"files": files}, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    return fa.record(out / f"{c['doc_key']}.mib.json", body, urls[0], "mib-json", method="mib", parts=parts,
                     tried=["mib: ok"])


NESTED_DOC = re.compile(r"(?is)<!doctype[^>]*>|</?html\b[^>]*>|<head\b.*?</head\s*>|</?body\b[^>]*>")


def page_html_until(page: bytes, title: str, end: str) -> bytes:
    """The page body from the first <h1 to the page_end text (module docstring, point 7)."""
    t = fa3.DROP_BLOCKS.sub("", fa.page_text(page))
    i = t.find("<h1")
    j = t.find(end, i) if i >= 0 else -1
    if i < 0 or j < 0:
        raise ValueError("no <h1> in page" if i < 0 else f"page_end not found: {end[:60]!r}")
    body = NESTED_DOC.sub("\n", t[i:j])
    if len(re.sub(r"<[^>]+>", " ", body).split()) < 300:
        raise ValueError("page body under 300 words")
    return f"<html><body><!-- {title} -->\n{body}\n</body></html>\n".encode()


def fetch_page_until(c: dict, out: Path) -> dict:
    url = c["html_url"]
    st, data = fa.get(url)
    if st != "ok":
        return {"status": "failed", "url": url, "tried": [f"page: {st}"]}
    try:
        body = page_html_until(data, c["title"], c["page_end"])
    except ValueError as e:
        return {"status": "failed", "url": url, "tried": [f"page: {e}"]}
    return fa.record(out / f"{c['doc_key']}.html", body, url, "html", method="page", page_end=c["page_end"],
                     raw_bytes=len(data), raw_sha256=hashlib.sha256(data).hexdigest(), tried=["page (page_end): ok"])


@contextmanager
def mib_page_mode():
    """fetch=page for a doc_type mib entry goes to fetch_mib (module docstring, point 5), one with page_end to
    fetch_page_until (point 7)."""
    saved = fa3.fetch_page

    def page_or_mib(c: dict, out: Path) -> dict:
        if c.get("doc_type") == "mib":
            return fetch_mib(c, out)
        return fetch_page_until(c, out) if c.get("page_end") else saved(c, out)
    fa3.fetch_page = page_or_mib
    try:
        yield
    finally:
        fa3.fetch_page = saved


@contextmanager
def any_case_chapter_headings():
    saved = fa.chapter_html
    fa.chapter_html = chapter_html
    try:
        yield
    finally:
        fa.chapter_html = saved


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ov_path = None
    if "--overrides" in argv:
        i = argv.index("--overrides")
        ov_path = Path(argv[i + 1])
        del argv[i:i + 2]
    cat_path, rfcs, root = Path(argv[0]), argv[1], Path(argv[2]).expanduser()
    ov_path = ov_path or (cat_path.parent / OVERRIDES)
    docs, held, stray = effective_catalog(read_json(cat_path), load_overrides(ov_path))
    root.mkdir(parents=True, exist_ok=True)
    eff = root / EFFECTIVE
    eff.write_text(json.dumps({"from": str(cat_path), "overrides": str(ov_path) if ov_path.exists() else None,
                               "held": held, "docs": docs}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"catalog: {len(docs)} to fetch, held (skipped): {held or 'none'}"
          + (f"; overrides for doc_keys not in the catalog (ignored): {stray}" if stray else ""), flush=True)
    saved = sys.argv
    sys.argv = [saved[0], str(eff), rfcs, str(root), *argv[3:]]
    try:
        with fa5.nesting_aware_toc_cut(), fa5.article_mode(), any_case_chapter_headings(), mib_page_mode():
            return fa3.main()
    finally:
        sys.argv = saved


if __name__ == "__main__":
    sys.exit(main())
