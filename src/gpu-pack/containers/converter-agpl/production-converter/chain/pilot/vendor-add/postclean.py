#!/usr/bin/env python3
"""Remove non-content from converter v2 output before it is published to pilot/cisco-md-v3.

usage: postclean.py <in.txt> <out.txt>

Fenced blocks are never edited; fences follow rag_core.ingest (a line indented at most 3 spaces that starts
with ``` or ~~~ opens a fence, and only a run of the same character at least as long closes it). Outside them:
1. C1 control characters U+0080..U+009F and U+FFFD are removed (in fences too). Converter v2 strips C0
   controls only; some TechNotes carry stray C1 bytes (e.g. U+009D left over from a mis-encoded closing quote)
   and some Cisco pages already contain U+FFFD where quotes were lost upstream (sec-cr-s4.html).
2. The in-document table of contents is removed: a heading whose text is exactly 'Contents' or 'Table of
   Contents' plus its lines up to the next heading. TechNotes and PDF books start with one; the entries only
   repeat the real headings and attract keyword hits. The same goes for a book's back-of-book index, a heading
   'Index' (or 'INDEX') followed by a list of command names (the command references' last chapter).
3. PDF mini-TOC lines ('* Finding Feature Information, on page 1') are removed.
4. A table row whose first cell starts with '#' ('# of SIDs Downloaded | Number of SIDs ...') gets one leading
   space, so it is no longer read as an H1 heading (rag_core's HEADING regex is anchored at column 0).
5. The Feature Navigator boilerplate paragraph ('Use Cisco Feature Navigator to find information about
   platform ...'), repeated in every feature chapter, is removed.
Every other line is kept byte for byte. Prints a one-line summary.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

C1 = re.compile(r"[\x80-\x9f�]")
HEAD = re.compile(r"#{1,6} (.*)$")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")          # rag_core.ingest.FENCE
TOC_TITLE = re.compile(r"(?i:(?:table of )?contents)|Index|INDEX")
MINI_TOC = re.compile(r"^\W{0,3}\S.*,\s+on page \d+\s*$")
HASH_ROW = re.compile(r"^# (?:.*? )?\| ")
NAVIGATOR = re.compile(r"^Use Cisco Feature Navigator to find information about platform")


def fence_step(fence: str | None, line: str) -> tuple[str | None, bool]:
    """rag_core.ingest's fence rule: (open fence marker after this line, whether the line is a fence line)."""
    f = FENCE.match(line)
    if not f:
        return fence, False
    if fence is None:
        return f.group(1), True
    if f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
        return None, True
    return fence, True


def clean(lines: list[str]) -> tuple[list[str], dict]:
    out: list[str] = []
    n = {"c1": 0, "toc_lines": 0, "mini_toc": 0, "hash_rows": 0, "navigator": 0}
    fence: str | None = None
    in_toc = False
    for line in lines:
        line, k = C1.subn("", line)
        n["c1"] += k
        fence, is_fence = fence_step(fence, line)
        if is_fence:
            in_toc = False
            out.append(line)
            continue
        if not fence:
            if HASH_ROW.match(line):
                n["hash_rows"] += 1
                line = " " + line
            h = HEAD.match(line)
            if h:
                in_toc = bool(TOC_TITLE.fullmatch(h.group(1).strip()))
                if in_toc:
                    n["toc_lines"] += 1
                    continue
            elif in_toc:
                n["toc_lines"] += 1
                continue
            elif MINI_TOC.match(line):
                n["mini_toc"] += 1
                continue
            elif NAVIGATOR.match(line):
                n["navigator"] += 1
                continue
        out.append(line)
    return out, n


def clean_text(text: str) -> tuple[str, dict]:
    lines, n = clean(text.split("\n"))
    out = "\n".join(lines)
    if text.endswith("\n") and not out.endswith("\n"):     # a removed last section took the final newline
        out += "\n"
    return out, n


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    out, n = clean_text(src.read_text(encoding="utf-8"))
    dst.write_text(out, encoding="utf-8")
    print(f"postclean: -{n['c1']} C1/U+FFFD chars, -{n['toc_lines']} TOC lines, -{n['mini_toc']} mini-TOC lines, "
          f"{n['hash_rows']} '#' table rows indented, -{n['navigator']} Feature Navigator lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
