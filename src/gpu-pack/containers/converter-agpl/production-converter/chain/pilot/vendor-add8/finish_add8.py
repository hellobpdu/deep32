#!/usr/bin/env python3
# Copied from ../cisco-add7/finish_add7.py (round add7) for round add8 by make_tooling_add8.py; the history notes below are add7's and add6's.
# Copied from ../cisco-add6/finish_add6.py (round add6) for round add8 by make_tooling_add8.py; the history notes below (r1, r2, fixes) are add6's.
"""Round add8 text rules, applied to the v2.1 pipeline's output (after postclean_v21.py). Every rule except the
skipped-level fix runs only for the documents whose catalog entry or fetch override asks for it; a text with
structured headings, a heading on line 1 and no rule is returned byte for byte.

usage: finish_add8.py <in.txt> <out.txt> <rules.json>      (or finish(text, rules) from Python)

Rules (keys of the dict convert_add8.rules_for() builds from the catalog entry and fetch-overrides-add8.json), applied
in this order, all of them outside ``` fences only (postclean.fence_step, rag_core's fence rule) except unfence_tail
and rejoin_ifnames, which work on fence lines:
- dedupe_article {start, end?}: a page that holds its whole article several times (cisco.com htmlblob repeats). The
  article starts at the heading whose title is `start`; when that heading occurs more than once, the first copy is
  kept, from its start heading to the end of its `end` section (the next heading at the `end` heading's level or
  above, or the next copy's start; without `end`, up to the next copy's start), and everything from there to the end
  of the last copy is dropped. A later copy that repeats the first one line for line ends where the first one ends,
  so lines before the first copy and after the last one are kept. A later copy whose lines differ from the first
  copy's by more than 10% stops the rule (nothing dropped; a problem). The record gives the copies found, the lines
  dropped and how many dropped lines do not occur in the kept text.
- keep_sections [spec, ...]: only these sections are kept. A spec is a heading title, or 'A > B' for a heading B with
  an ancestor heading A (any depth). Titles compare case-insensitively with runs of white space as one space. A kept
  section is its heading line and everything up to the next heading at its level or above (subsections included). The
  heading lines of its ancestors are kept too (heading line only), so the outline keeps its levels. A spec that
  matches no heading is a problem.
- keep_rows {section spec: [first-cell prefix, ...]}: in that section (kept as if listed in keep_sections), a table
  row (' | ' line) other than the first row of its table is kept only when its first cell starts with one of the
  prefixes (case-insensitive). Other lines of the section are kept. A prefix that matches no row is a problem.
- section_lines {section spec: [line prefix, ...]} (add8): that section is kept (as if listed in keep_sections) with
  only its heading line and the lines, outside fences, that start with one of the prefixes; its other lines and
  subsections are dropped. A prefix that starts no line of the section is a problem. For a document that serves
  a few lines of a long section (the Catalyst Center Assurance legend rows: one DITA step row per legend).
- keep_lines [line, ...]: with keep_sections, plain lines equal to one of these (surrounding white space ignored) are
  kept wherever they are (a chapter's lead sentence under its H1, which keep_sections drops because it keeps only the
  ancestor heading lines: 'This chapter describes how to configure ... on Cisco NX-OS devices.'). A listed line that
  is not found is a problem.
- drop_sections [spec, ...]: the sections these heading specs name (keep_sections' spec syntax) are dropped, heading
  line and subsections included (Feature Navigator boilerplate: 'Finding Feature Information'). A spec that matches
  no heading is a problem.
- drop_from: a heading title; that heading (its first occurrence) and every line after it are dropped (page chrome
  after the article, such as the security advisory side panel printed a second time after 'Feedback').
- drop_blocks [{from, to, max_lines}]: the lines from a line equal to `from` through the next line equal to `to`
  (at most max_lines lines, default 40) are dropped; each block must be found exactly once (a stray figure's labels
  that a PDF page carries: the LSP-ping drawing on page 2 of the 15.0S static-backup module). add8: {from, before,
  max_lines} drops from the `from` line up to, not including, the next line equal to `before` (outside fences): for
  a block whose last line is a fence's closing ``` (a PDF page that opens with the tail of the previous procedure).
- replace_lines [{line, with: [line, ...]} or {lines: [line, ...], with: [...]}]: a line, or a run of consecutive
  lines (exactly once, outside fences), is replaced by the given lines. Only the line breaks, ``` fence lines,
  heading markers and punctuation around words may differ: the replacement must hold the same words in the same order
  (words: white-space separated tokens with their leading and trailing punctuation stripped; tokens with no letter or
  digit, such as '#', '|' or a bullet glyph, do not count), or it is refused (a problem). For PDF blocks that run CLI
  lines and the next paragraph into one line, a flattened PDF table rebuilt as ' | ' rows, a table caption that
  should be a heading, or a header line that should not be one.
- unfence_tail [{line, join?}]: inside a fence, a line equal to `line` (exactly once inside fences) and every line
  after it up to the fence's closing ``` leave the fence: the fence closes before it, and the moved lines become one
  prose line joined with single spaces (join: false keeps them as separate lines). For PDF blocks that run a sentence
  after the sample output into the same monospace block ('The table below describes the significant fields ...').
- drop_figures: '[image: ...]' lines and figure caption lines ('Figure 3. ...', 'Figure 10.', 'Figure 2-1') are
  dropped; a sentence that starts with a figure reference ('Figure 20-2 shows ...') is kept. A bare caption
  ('Figure 10.') that follows an image line takes the next line with it when that line repeats the image's alt text
  (Word exports set the caption text in its own paragraph after 'Figure N.').
- drop_lines [line, ...]: lines equal to one of these (surrounding white space ignored) are dropped. A listed line
  that is not found is a problem.
- rejoin_ifnames [name, ...]: inside fences, '<name> <digit>' becomes '<name><digit>' ('interface Tunnel 10' ->
  'interface Tunnel10'). Converter v2.1's join_spans puts a space between two PDF spans when the font changes, and
  the CVDs set the interface number in bold right after the type: the PDF text layer has 'Tunnel10'. Use it only for
  a document whose PDF has no spaced form of these names.
- dehyphenate: words that the PDF broke at a line end and the converter glued back with the hyphen ('con-figuration')
  lose the hyphen. The words come from pdf_subset.py's record (hyphen_breaks: the line-final 'xxx-' + 'yyy' pairs of
  the kept pages whose joined word occurs unbroken elsewhere in the whole PDF, so a compound such as 'remote-site'
  stays); convert_add8.py passes them in as the rule 'hyphen_breaks'. Each occurrence of 'xxx-yyy' as a whole token
  outside fences becomes 'xxxyyy'.
- context_heading: a heading title put first, at level 1 (pdf_subset.py's context: the outline section that holds
  the first page of a page subset when that page starts no outline entry of its own).
- promote_lines [regex, ...]: a plain line that fully matches one becomes a heading one level below the nearest
  heading above it (CVD 'Procedure 7 Configure EIGRP' lines in a PDF page subset).
- title_line: the catalog title becomes line 1 ('# <title>') and every other heading moves one level down (at most
  level 6). Without the rule, a text whose line 1 is not a heading gets the title line the same way. title_line
  "replace": when line 1 is a level-1 heading, its title is replaced by the catalog title and no heading moves (a
  text that already uses level 6, where a shift would merge levels). title_text (round r2): the title line's text
  instead of the catalog title, for a catalog title that QA would flag as a suspect heading (over 200 characters:
  'Cisco Catalyst Center Second-Generation Appliance Installation Guide, Release 2.3.7.x (chapters: ...)').
- fix_skipped_levels (on unless false): a heading more than one level below the heading it falls under is raised to
  one level below it, and its subsections with it (the source's own markup skips: '# Title' then '### Advisory ID').
- expand_tabs (on unless false): CONVERT-SPEC's 'no tabs'. In a fence a tab expands to 8-column tab stops (as
  local-add5/cisco26_finish.py does); elsewhere a tab and the spaces around it become one space ('1.<TAB>Configure').
Round add8 fix pass (label round-fix, 2026-10-24):
- keep_lines_once (true): with keep_lines, only the first occurrence of each listed line outside the kept sections is
  kept (an advisory page that prints its CVE/CWE header block twice for its responsive layout).
- relayout [{lines: [line, ...], with: [line, ...]}]: a run of consecutive lines, anywhere (fence lines included; the
  run must occur exactly once), is replaced. The replacement must hold the same non-white-space characters in the same
  order, not counting ``` fence lines and heading markers, and the fence state after the run must not change: only
  line breaks, spaces, fences and heading levels move. For PDF sample output the converter merged into prose
  ('Router# debug eigrp fsm DUAL: ... The following output ...'), digits a PDF line wrap split with a space
  ('42949 67295'), and show-output tables set one token per line.
- insert_headings [{before: line, headings: ['## title', ...]}]: the headings are put before the line (outside fences,
  exactly once), after context_heading: source headings of a page subset's context that the subset does not carry
  (the PDF outline's 'Enabling DMVPN Backup on a Remote-Site Router' above Procedure 6). check (convert_add8.py)
  verifies that every inserted title occurs in the source.
- dehyphenate_extra [{hyphenated, word}]: reviewed line-end breaks: ones pdf_subset.py found but kept as compounds
  (hyphen_kept) because the joined word does not occur unbroken in the PDF ('neigh-bors'), or record breaks that are
  followed by a hyphen in the text ('redistrib-ute-internal' -> 'redistribute-internal'; the record's own breaks keep
  add6's rule there, 'con-figuration-x' stays). Each must be one of the record's breaks or hyphen_kept
  (convert_add8.py passes 'hyphen_kept'), and its word the token without its hyphen; else a problem.
- merge_fences (true): a closing ``` line straight followed by an opening ``` line is removed with it, so adjacent
  one-line fences become one block (legacy pages that set every CLI line as its own <pre>, PDF CVDs).
- line_separators (on unless false): U+2028 and U+2029 (LINE and PARAGRAPH SEPARATOR, invisible) are removed at the
  start and end of a line and become a line break inside it.
The record (counts and problems) is returned with the text; convert_add8.py writes it to add8-work/finish/<key>.json.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor-add"))
from postclean import fence_step  # noqa: E402

HEAD = re.compile(r"^(#{1,6}) (.*\S)\s*$")
IMAGE = re.compile(r"^\[image: .*\]$")
CAPTION = re.compile(r"^(?:Figure|Fig\.)\s+\d+(?:[-.]\d+)*(?:[.:](?:\s.*)?)?$")   # not 'Figure 20-2 shows ...'
BARE_CAPTION = re.compile(r"^(?:Figure|Fig\.)\s+\d+(?:[-.]\d+)*[.:]?$")                # 'Figure 10.'
COPY_DIFF_MAX = 0.10


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def fence_flags(lines: list[str]) -> list[bool]:
    flags, fence = [], None
    for line in lines:
        fence, is_fence = fence_step(fence, line)
        flags.append(is_fence or fence is not None)
    return flags


def headings(lines: list[str], flags: list[bool] | None = None) -> list[tuple[int, int, str]]:
    """(line index, level, title) of every ATX heading outside fences."""
    flags = fence_flags(lines) if flags is None else flags
    return [(i, len(m.group(1)), m.group(2)) for i, line in enumerate(lines)
            if not flags[i] and (m := HEAD.match(line))]


def section_end(heads: list[tuple[int, int, str]], k: int, n_lines: int) -> int:
    """Index after the section of heads[k] (the next heading at its level or above, else the end)."""
    lvl = heads[k][1]
    return next((j for j, l2, _ in heads[k + 1:] if l2 <= lvl), n_lines)


def ancestors(heads: list[tuple[int, int, str]], k: int) -> list[int]:
    """Positions in heads of the ancestors of heads[k], nearest first."""
    out, lvl = [], heads[k][1]
    for j in range(k - 1, -1, -1):
        if heads[j][1] < lvl:
            out.append(j)
            lvl = heads[j][1]
    return out


def match_spec(heads: list[tuple[int, int, str]], spec: str) -> list[int]:
    """Positions in heads of the headings a 'A > B' (or 'B') spec names."""
    parts = [norm(p) for p in spec.split(" > ")]
    out = []
    for k, (_, _, title) in enumerate(heads):
        if norm(title) != parts[-1]:
            continue
        want = parts[-2::-1]                      # nearest ancestor first
        pos = 0
        for t in (norm(heads[j][2]) for j in ancestors(heads, k)):
            if pos < len(want) and t == want[pos]:
                pos += 1
        if pos == len(want):
            out.append(k)
    return out


def dedupe_article(lines: list[str], start: str, end: str | None, rec: dict) -> list[str]:
    heads = headings(lines)
    occ = [k for k, (_, _, t) in enumerate(heads) if norm(t) == norm(start)]
    rec["article_copies"] = len(occ)
    if not occ:
        rec["problems"].append(f"dedupe_article: start heading {start!r} not found")
        return lines
    if len(occ) == 1:
        return lines

    def copy_end(c: int) -> int:
        nxt = heads[occ[c + 1]][0] if c + 1 < len(occ) else len(lines)
        if end is None:
            return nxt
        ends = [k for k in range(occ[c], len(heads)) if heads[k][0] < nxt and norm(heads[k][2]) == norm(end)]
        return min(section_end(heads, ends[0], len(lines)), nxt) if ends else nxt

    spans = [(heads[o][0], copy_end(c)) for c, o in enumerate(occ)]
    first = lines[spans[0][0]:spans[0][1]]
    # a later copy that repeats the first one line for line ends where the first one does (the last copy's `end`
    # section would otherwise run on into whatever follows the article)
    spans = spans[:1] + [(s, s + len(first)) if lines[s:s + len(first)] == first else (s, e) for s, e in spans[1:]]
    base = Counter(first)
    for s, e in spans[1:]:
        other = Counter(lines[s:e])
        diff = sum((base - other).values()) + sum((other - base).values())
        if diff > COPY_DIFF_MAX * max(len(first), e - s):
            rec["problems"].append(f"dedupe_article: copy at line {s + 1} differs from the first in {diff} lines; "
                                   "nothing dropped")
            return lines
    cut_from, cut_to = spans[0][1], spans[-1][1]
    kept = lines[:cut_from] + lines[cut_to:]
    have = set(kept)
    dropped = lines[cut_from:cut_to]
    rec["article_lines_dropped"] = len(dropped)
    rec["article_dropped_lines_not_kept"] = sum(1 for x in dropped if x.strip() and x not in have)
    return kept


def keep_sections(lines: list[str], specs: list[str], rows: dict, rec: dict,
                  keep_lines: list[str] | None = None, section_lines: dict | None = None,
                  keep_lines_once: bool = False) -> list[str]:
    flags = fence_flags(lines)
    heads = headings(lines, flags)
    keep = [False] * len(lines)
    specs = list(specs) + [s for s in section_lines or {} if s not in specs]
    wanted = {x.strip() for x in keep_lines or []}
    found_lines = set()
    for i, line in enumerate(lines):
        if not flags[i] and line.strip() in wanted and not HEAD.match(line):
            if keep_lines_once and line.strip() in found_lines:     # add8: a repeat stays out
                rec["keep_lines_repeats_dropped"] = rec.get("keep_lines_repeats_dropped", 0) + 1
                continue
            keep[i] = True
            found_lines.add(line.strip())
    rec["problems"] += [f"keep_lines: {x[:60]!r} not found" for x in sorted(wanted - found_lines)]
    if wanted:
        rec["lines_kept_outside_sections"] = sum(keep)
    row_rules: list[tuple[int, int, list[str]]] = []
    for spec in list(specs) + [s for s in rows if s not in specs]:
        found = match_spec(heads, spec)
        if not found:
            rec["problems"].append(f"keep_sections: no heading matches {spec!r}")
            continue
        rec.setdefault("sections_kept", []).append(spec)
        for k in found:
            s, e = heads[k][0], section_end(heads, k, len(lines))
            for i in range(s, e):
                keep[i] = True
            for j in ancestors(heads, k):
                keep[heads[j][0]] = True
            if spec in rows:
                row_rules.append((s, e, rows[spec]))
    for s, e, prefixes in row_rules:
        used, first_row = set(), True
        for i in range(s, e):
            if flags[i] or " | " not in lines[i]:
                first_row = True
                continue
            if first_row:
                first_row = False
                continue
            cell = norm(lines[i].split(" | ")[0])
            hit = next((p for p in prefixes if cell.startswith(norm(p))), None)
            if hit is None:
                keep[i] = False
                rec["rows_dropped"] = rec.get("rows_dropped", 0) + 1
            else:
                used.add(hit)
        rec["problems"] += [f"keep_rows: no row starts with {p!r}" for p in prefixes if p not in used]
    for spec, prefixes in (section_lines or {}).items():         # add8: only these lines of the section
        for k in match_spec(heads, spec):
            s, e = heads[k][0], section_end(heads, k, len(lines))
            used = set()
            for i in range(s + 1, e):
                hit = None if flags[i] else next((p for p in prefixes if lines[i].startswith(p)), None)
                keep[i] = hit is not None
                if hit:
                    used.add(hit)
            rec["problems"] += [f"section_lines: no line of {spec!r} starts with {p[:50]!r}"
                                for p in prefixes if p not in used]
        rec.setdefault("section_lines_kept", []).append(spec)
    out = [line for i, line in enumerate(lines) if keep[i]]
    rec["section_lines_dropped"] = len(lines) - len(out)
    return out


def drop_sections(lines: list[str], specs: list[str], rec: dict) -> list[str]:
    heads = headings(lines)
    drop = [False] * len(lines)
    for spec in specs:
        found = match_spec(heads, spec)
        if not found:
            rec["problems"].append(f"drop_sections: no heading matches {spec!r}")
        for k in found:
            for i in range(heads[k][0], section_end(heads, k, len(lines))):
                drop[i] = True
    rec["section_lines_dropped_by_spec"] = sum(drop)
    return [line for i, line in enumerate(lines) if not drop[i]]


def drop_from(lines: list[str], title: str, rec: dict) -> list[str]:
    heads = headings(lines)
    at = next((i for i, _, t in heads if norm(t) == norm(title)), None)
    if at is None:
        rec["problems"].append(f"drop_from: heading {title!r} not found")
        return lines
    rec["lines_dropped_from"] = len(lines) - at
    return lines[:at]


def drop_blocks(lines: list[str], blocks: list[dict], rec: dict) -> list[str]:
    flags = fence_flags(lines)
    drop: set[int] = set()
    for b in blocks:
        # add8: {"from", "before"} drops up to, not including, the 'before' line (a block that ends in a fence)
        excl = "before" in b
        first, last, most = b["from"].strip(), (b["before"] if excl else b["to"]).strip(), int(b.get("max_lines", 40))
        found = []
        for i, line in enumerate(lines):
            if flags[i] or line.strip() != first:
                continue
            j = next((j for j in range(i + excl, min(len(lines), i + most)) if not flags[j]
                      and lines[j].strip() == last), None)
            if j is not None:
                found.append((i, j - excl))
        if len(found) != 1:
            rec["problems"].append(f"drop_blocks: {first!r} .. {last!r} found {len(found)} times (want 1)")
            continue
        drop.update(range(found[0][0], found[0][1] + 1))
    rec["block_lines_dropped"] = len(drop)
    return [line for i, line in enumerate(lines) if i not in drop]


WORD_EDGE = re.compile(r"^[^\w]+|[^\w]+$")


def words(lines: list[str]) -> list[str]:
    """replace_lines' words: white-space separated tokens without their leading and trailing punctuation; tokens with
    no letter or digit ('#', '|', '```', a bullet glyph) do not count."""
    out = []
    for x in lines:
        for tok in x.split():
            w = WORD_EDGE.sub("", tok)
            if w:
                out.append(w)
    return out


def replace_lines(lines: list[str], items: list[dict], rec: dict) -> list[str]:
    flags = fence_flags(lines)
    out: list[str | None] = list(lines)
    n = 0
    for it in items:
        old = list(it["lines"]) if "lines" in it else [it["line"]]
        k = len(old)
        at = [i for i in range(len(lines) - k + 1)
              if lines[i:i + k] == old and not any(flags[i:i + k])]
        if len(at) != 1:
            rec["problems"].append(f"replace_lines: {old[0][:60]!r} found {len(at)} times (want 1)")
            continue
        if words(old) != words(it["with"]):
            rec["problems"].append(f"replace_lines: the replacement for {old[0][:60]!r} changes its words")
            continue
        i = at[0]
        if any(x is None for x in out[i:i + k]) or out[i:i + k] != old:
            rec["problems"].append(f"replace_lines: {old[0][:60]!r} overlaps another replacement")
            continue
        out[i] = "\n".join(it["with"])
        for j in range(i + 1, i + k):
            out[j] = None
        n += k
    rec["lines_replaced"] = n
    return "\n".join(x for x in out if x is not None).split("\n")


def unfence_tail(lines: list[str], items: list[dict], rec: dict) -> list[str]:
    flags = fence_flags(lines)
    out = list(lines)
    moved = 0
    for it in items:
        at = [i for i, line in enumerate(lines) if flags[i] and line == it["line"] and not line.startswith("```")]
        if len(at) != 1:
            rec["problems"].append(f"unfence_tail: {it['line'][:60]!r} found {len(at)} times in fences (want 1)")
            continue
        i = at[0]
        end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("```")), None)
        if end is None or any(out[j] != lines[j] for j in range(i, end + 1)):
            rec["problems"].append(f"unfence_tail: {it['line'][:60]!r}: no closing fence, or overlaps another tail")
            continue
        tail = lines[i:end]
        opened = i > 0 and lines[i - 1].startswith("```")       # the tail is the whole fence: drop the fence
        prose = [" ".join(x.strip() for x in tail)] if it.get("join", True) else tail
        if opened:
            out[i - 1] = None
            out[i:end + 1] = prose + [None] * (end + 1 - i - len(prose))
        else:
            out[i:end + 1] = ["```"] + prose + [None] * (end - i - len(prose))
        moved += len(tail)
    rec["fence_lines_unfenced"] = moved
    return [x for x in out if x is not None]


FENCE_LINE = re.compile(r"^```")


def _chars(lines: list[str]) -> str:
    """relayout's comparison: the non-white-space characters of the lines, without ``` lines and heading markers."""
    out = []
    for x in lines:
        if FENCE_LINE.match(x.strip()):
            continue
        m = HEAD.match(x)
        out.append(re.sub(r"\s+", "", m.group(2) if m else x))
    return "".join(out)


def _fence_after(lines: list[str], fence) -> object:
    for line in lines:
        fence, _ = fence_step(fence, line)
    return fence


def relayout(lines: list[str], items: list[dict], rec: dict) -> list[str]:
    """add8 rule relayout (module docstring)."""
    out: list[str | None] = list(lines)
    states, fence = [], None                 # fence state before each line
    for line in lines:
        states.append(fence)
        fence, _ = fence_step(fence, line)
    states.append(fence)
    n = 0
    for it in items:
        old, new = list(it["lines"]), list(it["with"])
        k = len(old)
        at = [i for i in range(len(lines) - k + 1) if lines[i:i + k] == old]
        if len(at) != 1:
            rec["problems"].append(f"relayout: {old[0][:60]!r} (+{k - 1} lines) found {len(at)} times (want 1)")
            continue
        i = at[0]
        if _chars(old) != _chars(new):
            rec["problems"].append(f"relayout: the replacement for {old[0][:60]!r} changes characters, not only "
                                   "line breaks, spaces, fences and heading markers")
            continue
        if _fence_after(new, states[i]) != states[i + k]:
            rec["problems"].append(f"relayout: the replacement for {old[0][:60]!r} changes the fence state after it")
            continue
        if any(x is None for x in out[i:i + k]) or out[i:i + k] != old:
            rec["problems"].append(f"relayout: {old[0][:60]!r} overlaps another replacement")
            continue
        out[i] = "\n".join(new)
        for j in range(i + 1, i + k):
            out[j] = None
        n += k
    rec["lines_relaid"] = n
    return "\n".join(x for x in out if x is not None).split("\n")


def insert_headings(lines: list[str], items: list[dict], rec: dict) -> list[str]:
    """add8 rule insert_headings (module docstring)."""
    flags = fence_flags(lines)
    adds: dict[int, list[str]] = {}
    for it in items:
        bad = [h for h in it["headings"] if not HEAD.match(h)]
        if bad:
            rec["problems"].append(f"insert_headings: not a heading: {bad[0][:60]!r}")
            continue
        at = [i for i, line in enumerate(lines) if not flags[i] and line == it["before"]]
        if len(at) != 1:
            rec["problems"].append(f"insert_headings: {it['before'][:60]!r} found {len(at)} times (want 1)")
            continue
        adds.setdefault(at[0], []).extend(it["headings"])
        rec.setdefault("inserted_headings", []).extend(HEAD.match(h).group(2) for h in it["headings"])
    out = []
    for i, line in enumerate(lines):
        out += adds.get(i, []) + [line]
    return out


def merge_fences(lines: list[str], rec: dict) -> list[str]:
    """add8 rule merge_fences (module docstring): a plain closing ``` straight followed by a plain opening ```."""
    out, n, i, fence = [], 0, 0, None
    while i < len(lines):
        line = lines[i]
        if fence == "```" and line == "```" and i + 1 < len(lines) and lines[i + 1] == "```":
            n += 1
            i += 2                                  # the fence stays open: both marker lines go
            continue
        fence, _ = fence_step(fence, line)
        out.append(line)
        i += 1
    rec["fences_merged"] = n
    return out


SEPARATORS = re.compile("[  ]")


def line_separators(lines: list[str], rec: dict) -> list[str]:
    """add8 rule line_separators (module docstring)."""
    out, n = [], 0
    for line in lines:
        if SEPARATORS.search(line):
            n += len(SEPARATORS.findall(line))
            line = re.sub("^[  ]+|[  ]+$", "", line)
            out += SEPARATORS.split(line)
            continue
        out.append(line)
    rec["line_separators"] = n
    return out


def drop_figures(lines: list[str], rec: dict) -> list[str]:
    flags = fence_flags(lines)
    drop: set[int] = set()
    alt, repeats = None, 0
    for i, line in enumerate(lines):
        if flags[i]:
            alt = None
            continue
        s = line.strip()
        m = IMAGE.match(s)
        if m:
            drop.add(i)
            alt = s[len("[image: "):-1]
            continue
        if CAPTION.match(s):
            drop.add(i)
            if alt is not None and BARE_CAPTION.match(s):
                nxt = next((j for j in range(i + 1, len(lines)) if lines[j].strip()), None)
                if nxt is not None and not flags[nxt] and norm(lines[nxt]) == norm(alt):
                    drop.add(nxt)
                    repeats += 1
            alt = None
            continue
        if s:
            alt = None
    rec["figure_lines_dropped"] = len(drop)
    if repeats:
        rec["figure_caption_repeats_dropped"] = repeats
    return [line for i, line in enumerate(lines) if i not in drop]


def rejoin_ifnames(lines: list[str], names: list[str], rec: dict) -> list[str]:
    rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r") (?=\d)")
    flags = fence_flags(lines)
    out, n = [], 0
    for line, f in zip(lines, flags):
        if f and not line.startswith("```"):
            line, k = rx.subn(r"\1", line)
            n += k
        out.append(line)
    rec["ifnames_rejoined"] = n
    return out


def dehyphenate(lines: list[str], breaks: list[dict], rec: dict, extra: list[dict] | None = None,
                kept: list[str] | None = None) -> list[str]:
    if not breaks and not extra:
        rec["problems"].append("dehyphenate: no hyphen_breaks (the rule needs pdf_subset.py's record)")
        return lines
    table = {b["hyphenated"]: b["word"] for b in breaks}
    found = set(table) | set(kept or [])
    reviewed: dict[str, str] = {}
    for b in extra or []:                                       # add8: reviewed line-end breaks, one by one
        if b["hyphenated"] not in found:
            rec["problems"].append(f"dehyphenate_extra: {b['hyphenated']!r} is not a line-end break of the kept pages "
                                   "(pdf_subset hyphen_kept)")
            continue
        if b["hyphenated"].replace("-", "") != b["word"]:
            rec["problems"].append(f"dehyphenate_extra: {b['word']!r} is not {b['hyphenated']!r} without its hyphen")
            continue
        reviewed[b["hyphenated"]] = b["word"]
    flags = fence_flags(lines)
    out, n = [], 0
    # a reviewed break also joins when a hyphen follows ('redistrib-ute-internal' -> 'redistribute-internal'); the
    # record's own breaks keep add6's rule (a following hyphen makes the token a compound: 'con-figuration-x' stays)
    passes = [(t, end) for t, end in ((reviewed, r"(?!\w)"), (table, r"(?![\w-])")) if t]
    rxs = [(re.compile(r"(?<![\w-])(" + "|".join(re.escape(h) for h in sorted(t, key=len, reverse=True)) + ")" + end),
            t) for t, end in passes]
    for line, f in zip(lines, flags):
        if not f:
            for rx, t in rxs:
                line, k = rx.subn(lambda m, t=t: t[m.group(1)], line)
                n += k
        out.append(line)
    rec["words_dehyphenated"] = n
    return out


def drop_where(lines: list[str], pred, rec: dict, name: str) -> list[str]:
    flags = fence_flags(lines)
    out = [line for i, line in enumerate(lines) if flags[i] or not pred(line)]
    rec[name] = len(lines) - len(out)
    return out


def promote(lines: list[str], patterns: list[str], rec: dict) -> list[str]:
    rx = [re.compile(p) for p in patterns]
    flags = fence_flags(lines)
    out, level, n = [], 0, 0
    for i, line in enumerate(lines):
        m = None if flags[i] else HEAD.match(line)
        if m:
            level = len(m.group(1))
        elif not flags[i] and line.strip() and any(r.fullmatch(line.strip()) for r in rx):
            line = "#" * min(6, level + 1) + " " + line.strip()
            n += 1
        out.append(line)
    rec["lines_promoted"] = n
    return out


def shift_headings(lines: list[str], by: int) -> list[str]:
    flags = fence_flags(lines)
    out = []
    for i, line in enumerate(lines):
        m = None if flags[i] else HEAD.match(line)
        out.append("#" * max(1, min(6, len(m.group(1)) + by)) + " " + m.group(2) if m else line)
    return out


def fix_skipped_levels(lines: list[str], rec: dict) -> list[str]:
    flags = fence_flags(lines)
    stack: list[tuple[int, int]] = []                       # (source level, new level) of the open headings
    out, n = [], 0
    for i, line in enumerate(lines):
        m = None if flags[i] else HEAD.match(line)
        if not m:
            out.append(line)
            continue
        lvl = len(m.group(1))
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        new = lvl if not stack else min(lvl, stack[-1][1] + 1)
        stack.append((lvl, new))
        if new != lvl:
            n += 1
            line = "#" * new + " " + m.group(2)
        out.append(line)
    rec["skipped_levels_fixed"] = n
    return out


def expand_tabs(lines: list[str], rec: dict) -> list[str]:
    flags = fence_flags(lines)
    out, n = [], 0
    for line, in_fence in zip(lines, flags):
        if "\t" in line:
            n += line.count("\t")
            line = line.expandtabs(8) if in_fence else re.sub(r"[ \t]*\t[ \t]*", " ", line)
        out.append(line)
    rec["tabs_expanded"] = n
    return out


def finish(text: str, rules: dict) -> tuple[str, dict]:
    rec: dict = {"problems": []}
    final_nl = text.endswith("\n")
    lines = text[:-1].split("\n") if final_nl else text.split("\n")
    rec["lines_in"] = len(lines)
    if rules.get("line_separators", True):                     # add8 fix pass
        lines = line_separators(lines, rec)
        if not rec["line_separators"]:
            del rec["line_separators"]
    if rules.get("dedupe_article"):
        d = rules["dedupe_article"]
        lines = dedupe_article(lines, d["start"], d.get("end"), rec)
    if rules.get("keep_sections") or rules.get("keep_rows") or rules.get("section_lines"):
        lines = keep_sections(lines, rules.get("keep_sections") or [], rules.get("keep_rows") or {}, rec,
                              rules.get("keep_lines"), rules.get("section_lines"), rules.get("keep_lines_once", False))
    elif rules.get("keep_lines"):
        rec["problems"].append("keep_lines: needs keep_sections or keep_rows")
    if rules.get("drop_sections"):
        lines = drop_sections(lines, rules["drop_sections"], rec)
    if rules.get("drop_from"):
        lines = drop_from(lines, rules["drop_from"], rec)
    if rules.get("drop_blocks"):
        lines = drop_blocks(lines, rules["drop_blocks"], rec)
    if rules.get("replace_lines"):
        lines = replace_lines(lines, rules["replace_lines"], rec)
    if rules.get("unfence_tail"):
        lines = unfence_tail(lines, rules["unfence_tail"], rec)
    if rules.get("relayout"):                                  # add8 fix pass
        lines = relayout(lines, rules["relayout"], rec)
    if rules.get("drop_figures"):
        lines = drop_figures(lines, rec)
    if rules.get("drop_lines"):
        wanted = {x.strip() for x in rules["drop_lines"]}
        present = {x.strip() for x, f in zip(lines, fence_flags(lines)) if not f}
        rec["problems"] += [f"drop_lines: {x!r} not found" for x in sorted(wanted - present)]
        lines = drop_where(lines, lambda x: x.strip() in wanted, rec, "lines_dropped")
    if rules.get("rejoin_ifnames"):
        lines = rejoin_ifnames(lines, rules["rejoin_ifnames"], rec)
    if rules.get("dehyphenate"):
        lines = dehyphenate(lines, rules.get("hyphen_breaks") or [], rec, rules.get("dehyphenate_extra"),
                            rules.get("hyphen_kept"))
    if rules.get("merge_fences"):                              # add8 fix pass
        lines = merge_fences(lines, rec)
    if rules.get("context_heading"):
        lines = [f"# {rules['context_heading']}"] + lines
        rec["context_heading"] = rules["context_heading"]
    if rules.get("insert_headings"):                           # add8 fix pass
        lines = insert_headings(lines, rules["insert_headings"], rec)
    if rules.get("promote_lines"):
        lines = promote(lines, rules["promote_lines"], rec)
    first_is_head = bool(lines) and bool(HEAD.match(lines[0]))
    title = rules.get("title_text") or rules.get("title")
    if title and rules.get("title_line") == "replace":
        if lines and re.match(r"# \S", lines[0]):
            lines = [f"# {title}"] + lines[1:]
            rec["title_line"] = title
        else:
            rec["problems"].append("title_line 'replace': line 1 is not a level-1 heading")
    elif title and (rules.get("title_line") or not first_is_head):
        lines = [f"# {title}"] + shift_headings(lines, 1)
        rec["title_line"] = title
    if rules.get("fix_skipped_levels", True):
        lines = fix_skipped_levels(lines, rec)
    if rules.get("expand_tabs", True):
        lines = expand_tabs(lines, rec)
    rec["lines_out"] = len(lines)
    return "\n".join(lines) + ("\n" if final_nl else ""), rec


def main(argv: list[str]) -> int:
    src, dst, rules = Path(argv[0]), Path(argv[1]), json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    out, rec = finish(src.read_text(encoding="utf-8"), rules)
    dst.write_text(out, encoding="utf-8")
    print(json.dumps(rec))
    return 1 if rec["problems"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
