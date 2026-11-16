#!/usr/bin/env python3
"""fetch_add3.py with a nesting-aware cut of the TechNote support TOC (fetch_add.py and fetch_add3.py stay unchanged).

usage: fetch_add5.py <catalog.json> <rfcs.txt> <pilot-dir> [--only key1,key2] [--force]    (the CLI of fetch_add3.py)

fetch_add.article_html() removes the in-page TOC with the regex '<div id="support-toc".*?</div>\\s*(?=<h2)': it ends
at the first </div> that is followed by an <h2>. On TechNote 225882 (cisco-tn-ise-aaa-dead-detection) the TOC holds
nested toc-h2 divs and its closing </div> is followed by '</div><ul ...>', so the regex ran on to the </div> before
the 'Reference' h2 and deleted 22,655 characters of article: the HTML pipeline kept 16 body words, and the document
had to be fetched as a PDF (ISE fidelity audit, verified defect 6). article_html() here removes the support-toc div
up to ITS matching </div> (drop_div), and is otherwise the same function. Every fetch mode and record is
fetch_add3's: main() runs fetch_add3.main() with this article_html in place of fetch_add's.

One catalog "fetch" value is added (the ISE fix pass, local-add5/ise-fix):
- article    the TechNote's HTML article (this article_html), else its PDF; never the ePub. For TechNotes whose ePub
             is worse than the page: 222247 (cisco-tn-ise-cli-show-logging), whose ePub collapses the line breaks of
             its <code style="white-space: pre-wrap"> CLI blocks, and 222201 (cisco-tn-ise-sxp-catalyst), whose
             ePub OPF has an XML-undefined '&ndash;' that stops the converter. The record's method is 'article' or
             'pdf', as fetch_add.fetch_book writes it. technote, book and the other values are unchanged.
"""
from __future__ import annotations

import re
import sys
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "vendor-add3"))
import fetch_add as fa  # noqa: E402
import fetch_add3 as fa3  # noqa: E402

DIV = re.compile(r"(?i)<div\b|</div\s*>")


def drop_div(html: str, start: str = '<div id="support-toc"') -> str:
    """html without the first div that begins with `start`, up to its matching </div> (nested divs counted)."""
    i = html.find(start)
    if i < 0:
        return html
    depth = 0
    for m in DIV.finditer(html, i):
        if m.group(0).lower().startswith("<div"):
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return html[:i] + html[m.end():]
    return html                                           # unbalanced: leave the page as it is


def article_html(page: bytes, title: str) -> bytes:
    """The TechNote/book article body of a cisco.com page (drops site chrome and the in-page TOC)."""
    t = fa.page_text(page)
    i = t.find('id="eot-doc-wrapper"')
    if i < 0:
        raise ValueError("no eot-doc-wrapper article in page")
    end = min((j for j in (t.find("Was this Document Helpful", i), t.find('class="col narrow-v2"', i)) if j > 0),
              default=len(t))
    body = drop_div(t[t.rfind("<div", 0, i):end])
    return f"<html><body><h1>{title}</h1>\n{body}\n</body></html>\n".encode()


@contextmanager
def nesting_aware_toc_cut():
    saved = fa.article_html
    fa.article_html = article_html
    try:
        yield
    finally:
        fa.article_html = saved


_FETCH_PLAN = fa.fetch_plan


def fetch_plan(c: dict) -> list[tuple[str, str]]:
    """fetch_add.fetch_plan, plus fetch=article: the HTML article, else the PDF (pdf_url, or derived from html_url)."""
    if c.get("fetch") != "article":
        return _FETCH_PLAN(c)
    html_url = c.get("html_url", "")
    pdf = c.get("pdf_url") or (html_url[:-5] + ".pdf" if html_url.endswith(".html") else "")
    return [("article", html_url), ("pdf", pdf)]


@contextmanager
def article_mode():
    """fetch_add.fetch_book (fetch_add3's default mode) with this fetch_plan, so fetch=article is understood."""
    saved = fa.fetch_plan
    fa.fetch_plan = fetch_plan
    try:
        yield
    finally:
        fa.fetch_plan = saved


def main() -> int:
    with nesting_aware_toc_cut(), article_mode():
        return fa3.main()


if __name__ == "__main__":
    sys.exit(main())
