#!/usr/bin/env python3
"""postclean v2.1: postclean.py (imported unchanged) plus the cleanups the ISE fidelity audit validated
(staging/pilot/local-add5/ise-fix/ise-audit-results.json).

usage: postclean_v21.py <in.txt> <out.txt> [--doc-key K] [--v21-only]

After postclean.clean(), outside fences (rag_core's rule, postclean.fence_step):
1. The Cisco copyright footer is dropped when it is the last line: 'Copyright © 2026, Cisco Systems, Inc. All
   rights reserved.' (also '© 2017 Cisco Systems, Inc. ...'). The rule never looks at other lines: the OpenSSL and
   Eric Young licence lines of iosxe17-rtr-sysmgmt are content.
2. ' icon ()' becomes ' icon': an inline icon image without alt text leaves 'click the Menu icon () and choose'.
3. A line that starts '**Note: ' starts 'Note: ' (Cisco's literal '**' in three Blast-RADIUS notes).
4. A last line '### Learn more' (cisco.com page chrome of CVDs and white papers) is dropped.
5. Doc-scoped, for the doc_keys in DEDUP_SECTIONS only: a top-level section (the shallowest heading level after the
   title line) that is identical to an earlier one once 'Table N'/'Figure N' numbers are masked is dropped, with its
   subsections. ise31-admin-asset-visibility: Cisco's chapter page prints three topic groups twice.
--doc-key: the document's key (default: the output file's stem). --v21-only: rules 1-5 only, for a text whose
round never ran postclean.py (pilot/cisco-md-v2). Every other line is kept byte for byte.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import postclean as pc  # noqa: E402

COPYRIGHT = re.compile(r"(?:Copyright )?© \d{4}(?:-\d{4})?,? Cisco Systems, Inc\. All rights reserved\.")
LEARN_MORE = "### Learn more"
ICON = re.compile(r" icon \(\)")
NOTE = re.compile(r"\*\*Note: ")
MASK = re.compile(r"\b(Table|Figure) \d+\b")
DEDUP_SECTIONS = {"ise31-admin-asset-visibility"}


def fence_flags(lines: list[str]) -> list[bool]:
    """Per line: True when it is a fence line or inside a fence."""
    flags, fence = [], None
    for line in lines:
        fence, is_fence = pc.fence_step(fence, line)
        flags.append(is_fence or fence is not None)
    return flags


def drop_repeated_sections(lines: list[str]) -> tuple[list[str], int]:
    """Rule 5: (lines without the repeated top-level sections, number of lines dropped)."""
    flags = fence_flags(lines)
    heads = [(i, len(m.group(1))) for i, line in enumerate(lines)
             if not flags[i] and (m := re.match(r"(#{1,6}) \S", line))]
    levels = [lvl for i, lvl in heads if i > 0]
    if not levels:
        return lines, 0
    top = min(levels)
    starts = [i for i, lvl in heads if i > 0 and lvl == top]
    end = len(lines) - 1 if lines[-1] == "" else len(lines)   # a final '' is the text's final newline
    bounds = []
    for s in starts:
        e = next((i for i, lvl in heads if i > s and lvl <= top), end)
        bounds.append((s, e))
    seen, drop = set(), set()
    for s, e in bounds:
        body = "\n".join(lines[s:e]).rstrip("\n")
        key = MASK.sub(r"\1 N", body)
        if key in seen:
            drop.update(range(s, e))
        seen.add(key)
    return [line for i, line in enumerate(lines) if i not in drop], len(drop)


def clean_v21(lines: list[str], dedup: bool = False) -> tuple[list[str], dict]:
    """Rules 1-5 on the lines of a text (a final '' is the text's final newline)."""
    n = {"icon": 0, "note": 0, "copyright": 0, "learn_more": 0, "repeated_section_lines": 0}
    flags = fence_flags(lines)
    out = []
    for line, in_fence in zip(lines, flags):
        if not in_fence:
            line, k = ICON.subn(" icon", line)
            n["icon"] += k
            if NOTE.match(line):
                line = "Note: " + line[len("**Note: "):]
                n["note"] += 1
        out.append(line)
    last = len(out) - 1                                   # the last line with text
    while last >= 0 and not out[last].strip():
        last -= 1
    while last >= 0 and not flags[last]:
        if COPYRIGHT.fullmatch(out[last].strip()) and not n["copyright"]:
            n["copyright"] += 1
        elif out[last] == LEARN_MORE and not n["learn_more"]:
            n["learn_more"] += 1
        else:
            break
        end = last
        last -= 1
        while last >= 0 and not out[last].strip():
            last -= 1
        del out[last + 1:end + 1], flags[last + 1:end + 1]   # the footer and the blank lines before it
    if dedup:
        out, n["repeated_section_lines"] = drop_repeated_sections(out)
    return out, n


def clean_text(text: str, doc_key: str = "", v21_only: bool = False) -> tuple[str, dict]:
    base = {}
    if not v21_only:
        text, base = pc.clean_text(text)
    lines, n = clean_v21(text.split("\n"), dedup=doc_key in DEDUP_SECTIONS)
    out = "\n".join(lines)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out, {**base, **n}


def main() -> int:
    args = sys.argv[1:]
    v21_only = "--v21-only" in args
    args = [a for a in args if a != "--v21-only"]
    key = None
    if "--doc-key" in args:
        i = args.index("--doc-key")
        key = args[i + 1]
        del args[i:i + 2]
    src, dst = Path(args[0]), Path(args[1])
    out, n = clean_text(src.read_text(encoding="utf-8"), key or dst.stem, v21_only)
    dst.write_text(out, encoding="utf-8")
    base = "" if v21_only else (f"postclean: -{n['c1']} C1/U+FFFD chars, -{n['toc_lines']} TOC lines, "
                                f"-{n['mini_toc']} mini-TOC lines, {n['hash_rows']} '#' table rows indented, "
                                f"-{n['navigator']} Feature Navigator lines; ")
    print(f"{base}v2.1: {n['icon']} 'icon ()' fixed, {n['note']} '**Note:' fixed, -{n['copyright']} copyright footer, "
          f"-{n['learn_more']} 'Learn more', -{n['repeated_section_lines']} repeated-section lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
