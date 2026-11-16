#!/usr/bin/env python3
"""Give class-styled headings and CLI lines of Cisco Word/FrameMaker HTML exports real HTML structure, so that
converter v2 (staging/tools/convert_docs.py, unchanged) turns them into ATX headings and ``` fences.

Some cisco.com pages are Word or FrameMaker exports: their headings are paragraphs with a style class, and each
CLI line is its own styled paragraph. Converter v2 only knows <h1>-<h6> and <pre>, so these pages come out
flat (the SD-Access macro segmentation CVD had 2 headings in 1,542 lines and none of its 825 CLI lines fenced).

Rules (CLASS_TAGS; the class must be the element's whole class attribute, whitespace ignored):
  p.pToC_Subhead1  -> h2   CVD process/part headings ("Deploy", "Process 5: Network Segmentation ...")
  p.pToC_Subhead2  -> h3   CVD section headings
  p.ProcedureHeading -> h4 CVD procedures ("Procedure 3. Establish BGP adjacencies ...")
  p.pTC_TableCap   -> h3   FrameMaker table captions ("Table B-3 Version 5 Header Format"): in the NetFlow export
                           format appendix every table is its own topic
  a run of consecutive p.MsoBodyTextIndent paragraphs (CVD CLI and configuration lines) -> one <pre> block,
                           one line per paragraph, inline markup removed, entities kept
Headings whose text is empty (spacer paragraphs) are dropped. Everything else is copied unchanged.

usage: prep_classes.py <in.html> <out.html>
Writes <out> only when a rule applied, and prints a one-line summary (or 'classes: clean').
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CLASS_TAGS = {"pToC_Subhead1": "h2", "pToC_Subhead2": "h3", "ProcedureHeading": "h4", "pTC_TableCap": "h3"}
PRE_CLASS = "MsoBodyTextIndent"
ELEMENT = re.compile(r'(?s)<p\b([^>]*?)\bclass="\s*([A-Za-z0-9_]+)\s*"([^>]*)>(.*?)</p>')
TAG = re.compile(r"<[^>]+>")
BR = re.compile(r"(?i)<br\s*/?>")
BLANK = re.compile(r"(?:\s|&nbsp;|&#160;|\xa0)*")


def text_of(inner: str) -> str:
    return TAG.sub("", inner)


def restructure(html: str) -> tuple[str, dict]:
    counts = {tag: 0 for tag in sorted(set(CLASS_TAGS.values()))}
    counts.update(pre_blocks=0, pre_lines=0, dropped_empty=0)
    out: list[str] = []
    last = 0
    pre_run: list[str] = []

    def flush_pre():
        nonlocal pre_run
        if pre_run:
            out.append("<pre>" + "\n".join(pre_run) + "</pre>")
            counts["pre_blocks"] += 1
            counts["pre_lines"] += len(pre_run)
            pre_run = []

    for m in ELEMENT.finditer(html):
        cls, inner = m.group(2), m.group(4)
        if cls != PRE_CLASS and cls not in CLASS_TAGS:
            continue
        gap = html[last:m.start()]
        if pre_run and BLANK.fullmatch(gap):
            gap = ""                                   # the run continues: only whitespace between the paragraphs
        else:
            flush_pre()
        out.append(gap)
        last = m.end()
        if cls == PRE_CLASS:
            pre_run.append(text_of(BR.sub("\n", inner)).rstrip())
            continue
        flush_pre()
        if BLANK.fullmatch(text_of(inner)):
            counts["dropped_empty"] += 1
            continue
        tag = CLASS_TAGS[cls]
        out.append(f"<{tag}>{inner}</{tag}>")
        counts[tag] += 1
    flush_pre()
    out.append(html[last:])
    return "".join(out), counts


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    html = src.read_text(encoding="utf-8")
    new, counts = restructure(html)
    if new == html:
        print("classes: clean")
        return 0
    dst.write_text(new, encoding="utf-8")
    print("classes: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
