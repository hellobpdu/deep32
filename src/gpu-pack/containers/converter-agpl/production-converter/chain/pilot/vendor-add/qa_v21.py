#!/usr/bin/env python3
"""QA v2.1: qa_text.py (imported unchanged) plus the checks the ISE fidelity audit asked for
(staging/pilot/local-add5/ise-fix/ise-audit-results.json: aaa-dead-detection, ise-perf-scale, ise31-compat,
trustsec-ndac).

usage: qa_v21.py <out.json> <file.txt> [<file.txt> ...] [--lexicon <dir-of-txt> ...]      (the CLI of qa_text.py)

Every qa_text metric and verdict rule is kept; 'radius' joins the CLI prompt keywords ('Switch(config)#radius server
RAD1' counts as a prompt line, inside or outside fences). Added metrics, all WARN-level:
- table_shape:         rows whose field count differs from their table's header row. A table is a run of ' | '
                       lines outside fences; a fence inside it (a <pre> in a row) does not end it, and the lines
                       next to that fence are parts of a cut row, so they are not counted; admonition lines
                       ('Note | ...') neither count nor end it. ' | ' inside {...} or [...] (Cisco command syntax)
                       does not separate fields. WARN when > 0.
- empty_sections:      (qa_text's metric) WARN when >= 3.
- long_unfenced:       lines outside fences longer than 2,000 characters (CLI output or a table glued into one
                       line). WARN when > 0.
- unfenced_cli:        no fence at all while cli_unfenced >= 1 (the PDF route lost the monospace blocks). WARN.
- image_only_sections: best effort from the text: sections without subsections whose body is empty or holds only
                       figure captions and '[image: ...]' lines (converter v2.1 writes meaningful alt text that
                       way; a screenshot without alt text leaves nothing). WARN when >= IMAGE_ONLY_WARN.
"""
from __future__ import annotations

import json
import re
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_text as qt  # noqa: E402
from postclean import fence_step  # noqa: E402

PROMPT21 = re.compile(qt.PROMPT.pattern.replace(r"|copy|write)\b", r"|copy|write|radius)\b"))
assert "radius" in PROMPT21.pattern, "qa_text.PROMPT changed: update qa_v21.PROMPT21"
ADMONITION = re.compile(r"^(?:Note|Restriction|Caution|Warning|Important|Tip|Attention|Danger|Remember)\s\|")
CAPTION = re.compile(r"^(?:Figure|Fig\.)\s+\d+[\w.-]*[.:]?\s")
IMAGE = re.compile(r"^\[image: .*\]$")
LONG_LINE = 2000
EMPTY_WARN = 3
IMAGE_ONLY_WARN = 2


@contextmanager
def prompt21():
    saved = qt.PROMPT
    qt.PROMPT = PROMPT21
    try:
        yield
    finally:
        qt.PROMPT = saved


def field_count(line: str) -> int:
    """Fields of a ' | ' row; separators inside {...} or [...] belong to a cell's command syntax."""
    depth, n, i = 0, 1, 0
    while i < len(line):
        ch = line[i]
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif depth == 0 and line.startswith(" | ", i):
            n += 1
            i += 3
            continue
        i += 1
    return n if depth == 0 else line.count(" | ") + 1


def table_shape(lines: list[str]) -> list[tuple[int, int, int]]:
    """(line number, fields, header fields) of every row that does not match its header. Rows next to a fence are
    parts of a row a <pre> cut (or the row after one): they stay in the table but are not compared."""
    fence, block, out, closed = None, None, [], -2
    for i, line in enumerate(lines):
        opening = fence is None
        fence, is_fence = fence_step(fence, line)
        if is_fence or fence:
            if is_fence and opening and block is not None and block["rows"] and block["rows"][-1] == i - 1:
                block["cut"].add(i - 1)
            if is_fence and fence is None:
                closed = i
            continue
        if ADMONITION.match(line):
            continue
        if " | " not in line:
            if block is not None:
                out.extend(_check(block, lines))
            block = None
            continue
        if block is None:
            block = {"rows": [], "cut": set()}
        block["rows"].append(i)
        if closed == i - 1:
            block["cut"].add(i)
    if block is not None:
        out.extend(_check(block, lines))
    return out


def _check(block: dict, lines: list[str]) -> list[tuple[int, int, int]]:
    rows = [i for i in block["rows"] if i not in block["cut"]]
    if len(rows) < 2:
        return []
    head = field_count(lines[rows[0]])
    return [(i + 1, n, head) for i in rows[1:] if (n := field_count(lines[i])) != head]


def image_only_sections(lines: list[str]) -> list[str]:
    """Sections without subsections whose body is empty or only figure captions / '[image: ...]' lines."""
    fence, heads = None, []
    for i, line in enumerate(lines):
        fence, is_fence = fence_step(fence, line)
        if not fence and not is_fence and (m := qt.HEAD.match(line)):
            heads.append((i, len(m.group(1))))
    out = []
    for k, (i, lvl) in enumerate(heads):
        if i == 0:
            continue                                      # the title
        nxt = heads[k + 1] if k + 1 < len(heads) else (len(lines), 0)
        if nxt[0] < len(lines) and nxt[1] > lvl:
            continue                                      # has subsections
        body = [line for line in lines[i + 1:nxt[0]] if line.strip()]
        if all(CAPTION.match(line) or IMAGE.match(line) for line in body):
            out.append(f"{i + 1}: {lines[i][:100]}")
    return out


def qa_file(p: Path, lex) -> dict:
    with prompt21():
        m = qt.qa_file(p, lex)
    lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    shape = table_shape(lines)
    fence, long_lines = None, []
    for i, line in enumerate(lines):
        fence, is_fence = fence_step(fence, line)
        if not fence and not is_fence and len(line) > LONG_LINE:
            long_lines.append(i + 1)
    image_only = image_only_sections(lines)
    m.update(table_shape=len(shape), table_shape_examples=[f"L{i}: {n}/{h} fields" for i, n, h in shape[:8]],
             long_unfenced=len(long_lines), long_unfenced_examples=long_lines[:8],
             unfenced_cli=bool(m["fences"] == 0 and m["cli_unfenced"] >= 1),
             image_only_sections=len(image_only), image_only_examples=image_only[:8])
    warn = []
    if shape:
        warn.append(f"{len(shape)} table rows off their header's field count")
    if m["empty_sections"] >= EMPTY_WARN:
        warn.append(f"{m['empty_sections']} empty sections")
    if long_lines:
        warn.append(f"{len(long_lines)} unfenced lines over {LONG_LINE:,} chars")
    if m["unfenced_cli"]:
        warn.append(f"no fences but {m['cli_unfenced']} CLI prompt lines")
    if len(image_only) >= IMAGE_ONLY_WARN:
        warn.append(f"{len(image_only)} image-only or empty sections")
    m["reasons"] = m["reasons"] + warn
    if m["verdict"] == "PASS" and warn:
        m["verdict"] = "WARN"
    return m


def main() -> int:
    args = sys.argv[1:]
    out = Path(args.pop(0))
    lex_dirs = []
    while "--lexicon" in args:
        i = args.index("--lexicon")
        lex_dirs.append(Path(args[i + 1]))
        del args[i:i + 2]
    files = [Path(a) for a in args]
    lex = qt.lexicon(files + [p for d in lex_dirs for p in sorted(d.glob("*.txt"))])
    report = {}
    for p in files:
        r = qa_file(p, lex)
        report[p.stem] = r
        print(f"{r['verdict']:4} {p.stem:36} {r['bytes'] // 1024:6} KiB h={r['headings']:5} fences={r['fences']:6} "
              f"rows={r['table_rows']:5} shape={r['table_shape']:4} glue={r['glue']:3} cli_out={r['cli_unfenced']:4} "
              f"| {'; '.join(r['reasons'])}")
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{sum(r['verdict'] == 'PASS' for r in report.values())} PASS, "
          f"{sum(r['verdict'] == 'WARN' for r in report.values())} WARN, "
          f"{sum(r['verdict'] == 'FAIL' for r in report.values())} FAIL of {len(report)}")
    return 1 if any(r["verdict"] == "FAIL" for r in report.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
