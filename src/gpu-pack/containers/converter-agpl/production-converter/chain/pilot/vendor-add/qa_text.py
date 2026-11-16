#!/usr/bin/env python3
"""QA for converted corpus text (converter v2 output: ATX headings, ``` fences, 'a | b' table rows).

usage: qa_text.py <out.json> <file.txt> [<file.txt> ...] [--lexicon <dir-of-txt> ...]

Per file it measures:
- ctrl:        C0/C1 control characters other than tab/newline, NUL, U+FFFD  (FAIL if > 0)
- fences:      fence lines, tracked with rag_core.ingest's rule (postclean.fence_step: ``` or ~~~ indented at
               most 3 spaces; only the same character, at least as long, closes); unbalanced = a fence still
               open at the end (FAIL); fence_mismatch = first line where rag_core's fence state differs from
               the converter's plain '```' toggle (FAIL: rag_core would read the document differently);
               hash_in_fence = '#' lines inside a fence (info only: they do not split sections)
- headings:    count per level, skipped levels (e.g. H1 -> H3), suspect headings (CLI prompt lines,
               '#='/'#!' comments, > 200 chars, sentence-like), empty sections (heading directly followed by a
               heading of the same or higher level) (WARN when suspect > 0)
- cli_unfenced: prompt lines ('Device(config)# ...', 'Router# ...') outside fences (WARN if > 1% of prompt lines)
- tables:      'a | b' rows; flattened PDF step tables ('Command or Action Purpose' header lines)
- glue:        run-together words: a rare token (<= 2 uses in the lexicon) of >= 10 letters that splits into two
               common words (>= 20 uses each, >= 3 letters) and is not a known closed-compound form (non-,
               multi-, -able ...). The lexicon is built from all files given plus --lexicon dirs.
               (WARN if > 5 per 100 KB)
- toc_noise:   lines in a '# Contents' or back-of-book '# Index' section, ', on page N' mini-TOC lines,
               'Finding Feature Information' headings, Feature Navigator boilerplate (WARN)
- injection:   instruction-like lines (scan_injection.py) (FAIL: a person reviews them), invisible format
               characters (WARN)
Writes {file: {metrics..., verdict, reasons}} to out.json and prints a one-line summary per file.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from postclean import TOC_TITLE, fence_step
from scan_injection import scan_text

CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f�]")
HEAD = re.compile(r"^(#{1,6}) (.*)$")
PROMPT = re.compile(r"^\s*(?:Device|Router|Switch|R\d+|SW\d+|Hub\d*|Spoke\d*|PE\d*|CE\d*|vEdge\d*|cEdge\d*|"
                    r"[A-Za-z][\w.-]{0,30})(?:\((?:config|cfg)[^)]*\))?[#>] ?(?:show|config|interface|router|ip|ipv6|no|"
                    r"debug|clear|ping|traceroute|end|exit|crypto|tunnel|neighbor|network|address-family|"
                    r"policy-map|class|match|set|route-map|access-list|event|action|monitor|flow|track|"
                    r"aaa|snmp-server|logging|username|line|telemetry|netconf|restconf|guestshell|copy|write)\b")
# a heading that is really a CLI prompt line, a script comment or a pseudo tag (command NAMES are legitimate
# headings in command references and TechNotes, so they are not flagged)
CLI_HEAD = re.compile(r"^(?:[\w.-]+(?:\([\w-]+\))?[#>]\s*\S|[=!#*<])")
WORD = re.compile(r"\b[a-z]{3,}\b")
# legitimate closed compounds: prefixes/suffixes that Cisco text writes solid
COMPOUND = re.compile(r"^(?:non|multi|auto|pre|sub|inter|intra|over|under|re|un|super|cross|out|back|down|up|with|"
                      r"trans|self|any|every|some|with|where|there|here|time|work|net|web|hand|name|key|file|data|"
                      r"line|pass|user|host|end|mid|bi|co|de|dis|mis|semi|anti|counter|post|ultra)|"
                      r"(?:able|ability|ably|less|ness|ment|ments|wise|ward|wards|like|ship|based|side|way|ways)$")
MINI_TOC = re.compile(r",\s+on page \d+\s*$")
NAVIGATOR = re.compile(r"Use Cisco Feature Navigator to find information about platform")
# a flattened step-table header (the PDF path); a real table keeps its cells: 'Command or Action | Purpose'
STEP_HDR = re.compile(r"^(?:Command or Action Purpose|Purpose Command or Action)\b")


def lexicon(paths: list[Path]) -> Counter:
    lex: Counter = Counter()
    for p in paths:
        fence = None
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            fence, is_fence = fence_step(fence, line)
            if not fence and not is_fence:
                lex.update(WORD.findall(line.lower()))
    return lex


def glued(tok: str, lex: Counter) -> bool:
    if len(tok) < 10 or lex[tok] > 2 or COMPOUND.search(tok):
        return False
    return any(lex[tok[:i]] >= 20 and lex[tok[i:]] >= 20 for i in range(3, len(tok) - 2))


def qa_file(p: Path, lex: Counter) -> dict:
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    m: dict = {"bytes": len(text.encode()), "lines": len(lines), "ctrl": len(CTRL.findall(text))}
    fence, plain_fence, fences, hash_in_fence, fence_mismatch = None, False, 0, 0, None
    levels: Counter = Counter()
    skipped, suspect, empty, prev_level, last_head_idx = 0, [], 0, 0, None
    prompts_in, prompts_out, rows, step_hdr, toc, mini_toc, navigator, ffi = 0, 0, 0, 0, 0, 0, 0, 0
    glue: Counter = Counter()
    in_contents = False
    for i, line in enumerate(lines):
        fence, is_fence = fence_step(fence, line)
        if line.startswith("```"):
            plain_fence = not plain_fence
        if fence_mismatch is None and bool(fence) != plain_fence:
            fence_mismatch = i + 1
        if is_fence:
            fences += 1
            continue
        if fence:
            if line.startswith("#"):
                hash_in_fence += 1
            if PROMPT.match(line):
                prompts_in += 1
            continue
        h = HEAD.match(line)
        if h:
            lvl, title = len(h.group(1)), h.group(2).strip()
            levels[lvl] += 1
            if prev_level and lvl > prev_level + 1:
                skipped += 1
            if last_head_idx is not None and last_head_idx == i - 1 and lvl <= prev_level:
                empty += 1
            if CLI_HEAD.match(title) or len(title) > 200 or (title.endswith(".") and len(title.split()) > 8):
                suspect.append(f"{i + 1}: {line[:100]}")
            if title == "Finding Feature Information":
                ffi += 1
            in_contents = bool(TOC_TITLE.fullmatch(title))
            prev_level, last_head_idx = lvl, i
            continue
        if in_contents and line.strip():
            toc += 1
        if MINI_TOC.search(line):
            mini_toc += 1
        if NAVIGATOR.search(line):
            navigator += 1
        if PROMPT.match(line):
            prompts_out += 1
        if " | " in line:
            rows += 1
        if STEP_HDR.match(line):
            step_hdr += 1
        for tok in WORD.findall(line.lower()):
            if glued(tok, lex):
                glue[tok] += 1
    inj = scan_text(text)
    m.update(fences=fences, unbalanced=fence is not None, fence_mismatch=fence_mismatch,
             hash_in_fence=hash_in_fence, headings=sum(levels.values()),
             levels={f"h{k}": levels[k] for k in sorted(levels)}, skipped_levels=skipped, empty_sections=empty,
             suspect_headings=len(suspect), suspect_examples=suspect[:8], prompts_fenced=prompts_in,
             cli_unfenced=prompts_out, table_rows=rows, flat_step_tables=step_hdr, toc_lines=toc,
             mini_toc_lines=mini_toc, navigator_boilerplate=navigator, finding_feature_info=ffi,
             glue=sum(glue.values()), glue_examples=[w for w, _ in glue.most_common(12)],
             injection=inj["hits"], injection_examples=inj["examples"], invisible=inj["invisible"],
             invisible_examples=inj["invisible_examples"])
    fail, warn = [], []
    if m["ctrl"]:
        fail.append(f"{m['ctrl']} control chars")
    if m["unbalanced"]:
        fail.append("unbalanced fences")
    if fence_mismatch:
        fail.append(f"fence state differs from rag_core from line {fence_mismatch}")
    if m["injection"]:
        fail.append(f"{m['injection']} instruction-like lines (review)")
    if not m["headings"]:
        fail.append("no headings")
    if m["bytes"] < 2000:
        fail.append("under 2 KB")
    if suspect:
        warn.append(f"{len(suspect)} suspect headings")
    total_prompts = prompts_in + prompts_out
    if total_prompts and prompts_out > max(2, 0.01 * total_prompts):
        warn.append(f"{prompts_out}/{total_prompts} CLI prompt lines unfenced")
    if m["glue"] > 5 * max(1, m["bytes"] / 102400):
        warn.append(f"{m['glue']} run-together words")
    if toc + mini_toc + navigator + ffi:
        warn.append(f"TOC/boilerplate lines {toc + mini_toc + navigator + ffi}")
    if step_hdr:
        warn.append(f"{step_hdr} flattened step tables")
    if m["invisible"]:
        warn.append(f"{m['invisible']} invisible format characters")
    m["verdict"] = "FAIL" if fail else "WARN" if warn else "PASS"
    m["reasons"] = fail + warn
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
    lex = lexicon(files + [p for d in lex_dirs for p in sorted(d.glob("*.txt"))])
    report = {}
    for p in files:
        r = qa_file(p, lex)
        report[p.stem] = r
        print(f"{r['verdict']:4} {p.stem:36} {r['bytes'] // 1024:6} KiB h={r['headings']:5} fences={r['fences']:6} "
              f"rows={r['table_rows']:5} glue={r['glue']:3} cli_out={r['cli_unfenced']:4} | {'; '.join(r['reasons'])}")
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{sum(r['verdict'] == 'PASS' for r in report.values())} PASS, "
          f"{sum(r['verdict'] == 'WARN' for r in report.values())} WARN, "
          f"{sum(r['verdict'] == 'FAIL' for r in report.values())} FAIL of {len(report)}")
    return 1 if any(r["verdict"] == "FAIL" for r in report.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
