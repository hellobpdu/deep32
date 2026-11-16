#!/usr/bin/env python3
# Copied from ../cisco-add7/prep_add7.py (round add7) for round add8 by make_tooling_add8.py; the history notes below are add7's and add6's.
# Copied from ../cisco-add6/prep_add6.py (round add6) for round add8 by make_tooling_add8.py; the history notes below (r1, r2, fixes) are add6's.
"""Round add8 source rules for FrameMaker-exported cisco.com chapters, applied to the fetched HTML (a copy; the cached
original is never changed) before the v2.1 pipeline, for the documents whose fetch override asks for them.

usage: prep_add8.py <in.html> <out.html> <rules.json>      (or prep(html, rules) from Python)
Writes <out> only when a rule changed something, and prints what changed ('prep8: clean' otherwise).

Rules (keys of a fetch-overrides-add8.json entry):
- heading_levels {"classes": {class: level}, "labels": [title, ...]}: a heading element (<h1>-<h6>) whose class
  attribute is one of the classes gets that level. FrameMaker exports tag their heading levels in the class
  (p_H_Head1..p_H_Head4, pCT_ChapTitle, pCRCM_CmdRefCmdModes) and write most of them as <h3>, so converter v2/v2.1
  would read a chapter's sections and subsections as siblings. A heading whose text is one of the labels
  ('Prerequisites', 'SUMMARY STEPS', ...) is put one level below the last heading before it that is not a label: the
  export gives a task's labels the level of the task itself.
- pre_classes [class, ...]: a run of elements (<section>, <p> or <div>) with one of these classes, with nothing but
  white space between them, becomes one <pre> block, one line per element (tags removed, entities kept, a
  non-breaking space as a space, trailing spaces and the run's blank first and last lines dropped). FrameMaker writes
  each CLI line of an example as its own <section class="pEx1_Example1">. Elements inside a <table> are left alone
  (a CLI line in a step-table cell stays cell text, as converter v2.1 writes cells).
- pre_absorb [{"class", "regex"}]: with pre_classes, an element (<section>, <p> or <div>) of this class whose text
  matches the regex, with nothing but white space between it and the end of a pre_classes run, joins that run as its
  next line (the NX-OS PE2 example's last comment line '!- configure disable-peer-as-check at the CE' is a
  <p class="pB1_Body1"> right after its pEx1_Example1 run: the page's own markup slip). Elsewhere it stays as it is.
- pre_tables [[label, ...], ...]: a <table> whose first row's cells read exactly these labels and whose other cells
  hold nothing but pre_classes elements (and white space) becomes, per column, '<p>label</p>' and one <pre> with the
  column's lines (the NX-OS 'PE Configuration | CE Configuration' example tables, whose pEx2_Example2 lines converter
  v2.1 folds into one table row with '; ' separators). A table with any other cell content is left alone.
- p_headings {"classes": {class: level}, "require": text}: a <p> whose class attribute is one of the classes, and
  whose content holds the `require` text (when given), becomes a heading element of that level (Word exports set
  their third-level subheads as <p class="pSubhead3CMT"> with a Word TOC anchor '<a name="_Toc...">'; the same class
  without the anchor is a body sentence, as local-add5/prep_doc/wp_ise_c9800_gbp.py found).
- drop_html [regex, ...]: every match of each regex (dot matches newline) is removed from the page: page UI chrome
  inside the article markup, such as the security advisory's '<strong>Click Icon to Copy Verbose Score</strong>'
  tooltip that the text glues onto the CVSS score, or a DITA chapter's <ul> of <li class="olchildlink"> mini-TOC
  links. A regex that matches nothing is reported (counts["drop_html_unmatched"]); convert_add8.py treats that as an
  error.
- table_rows {"header", "keep", "placeholder"?, "cstring"?} (round r2: the IOS XE 17.9 System Message Guide, one 57 MB
  page with 20,702 message rows, of which the catalog keeps the facilities it names): in every <table> whose first
  row's first cell reads `header`, a later row is kept only when its first cell's text matches the regex `keep`
  (re.match); a kept row whose cells at the `placeholder` columns (0-based) all read '-' is dropped as well (the
  guide's placeholder duplicates of a message). The first row and every table with another first header cell are left
  alone, and a page with no such table is an error (counts["table_rows_unmatched"]). Run before the converter, so the
  chain reads the kept rows only. With cstring, the C-source escapes the page prints in the kept cells are normalised:
  a newline escape '\n' (with the line-continuation '\' that follows it, and the tab escapes '\t' right after it),
  together with the white space around it, becomes one space, and the cell is trimmed ('not properly\n\
  authenticated.' -> 'not properly authenticated.'); in a cell that is a list of concatenated C strings (its text
  starts with a double quote), each joint of a closing quote, white space and an opening quote becomes one space and
  an escaped quote '\"' becomes '"' ('"Enable AAA " "debug ..."' -> '"Enable AAA debug ..."'). A '\t' that does not
  follow a '\n' is text, not a tab ('\track\', '\timeout login response\': quotes the page lost), and every other
  character is unchanged.
- pre_paras [text, ...] (round r2 fixes): a <p> whose visible text (tags removed, white space collapsed) equals one of
  these texts becomes '<pre class="pre codeblock"><code>text</code></pre>', the DITA code block markup, inside a table
  cell too. A DITA step can write a standalone command as a keyword paragraph ('<p class="p"><span><span
  class="keyword kwd">sudo maglev-config update</span> </span></p>' in the Catalyst Center install guide's Reconfigure
  step 4) where other steps use a codeblock; converter v2.1 flattens the step row into one '; '-joined line, so the
  command is not fenced. With the codeblock markup the converter breaks the row around a fence, as it does for the
  NX-OS RA guard steps' Example codeblocks. Each text must match exactly one paragraph: a text that matches none or
  several is reported (counts["pre_paras_unmatched"]); convert_add8.py treats that as an error.
- img_text {src_substring: text} (round add8: the Catalyst Center Assurance legend swatches): every <img> whose src
  contains one of the substrings is replaced by that text, inline, so a legend line keeps its swatch as a word in
  place ('[image: red]: Poor network devices. ...'). For images with no alt text whose meaning is only their colour,
  read from the image files first-hand; converter v2.1 drops such images. A substring that matches no image is
  reported (counts["img_text_unmatched"]); convert_add8.py treats that as an error.
- br_pre [regex, ...] (round add8: the CoPP white paper): a <p> with <br> line breaks whose first line matches one
  of the regexes becomes one <pre>, a line per <br> (the page writes its configurations and show output as
  paragraphs, so converter v2.1 left them unfenced). A regex that matches no paragraph is reported
  (counts["br_pre_unmatched"]); convert_add8.py treats that as an error.
Order: img_text, drop_html, br_pre, table_rows, heading_levels, p_headings, pre_tables, pre_classes (with
pre_absorb), pre_paras.

Round add8 fix pass (label round-fix, 2026-10-24; each rule doc-scoped, reported by count, an unmatched rule is an error
in convert_add8.py; on an ePub source convert_add8.py applies prep() to every XHTML member and re-zips a copy):
- drop_childlinks (true): every <ul> whose items are all DITA child links (<li class="olchildlink"> or
  "ulchildlink", with or without <strong>) is removed: the chapter's in-page navigation lists, which the converter
  writes as plain lines (TOC lines that also name sections a trim drops). The 'Contents' list title that goes with
  them stays unless drop_html removes it.
- glue_space (true): a run of inline tags (a, strong, b, i, em, code, tt, span, kbd, var, cite, samp, u, font, sup)
  with a letter or digit (or one of ® ™ . , ; : ) ) right before it and a letter or digit (or '(' '%') right after it,
  and no white space at either side, gets one space: before the run when it starts with an opening tag, after it
  otherwise ('Use the<strong>access-list</strong>command' -> 'Use the <strong>access-list</strong> command'). Never
  inside <pre>, <script> or <style>; never between a '.' or ',' and a digit ('1.<b>2</b>'). For pages whose own
  markup has no white space at these boundaries, which a browser shows glued as well.
- unwrap_image_headings (true): an <h1>-<h6> that holds an <img> and whose visible text is empty or equals the alt
  text of one of its images or the text of its Khoros caption span (lia-inline-image-caption), trailing dots aside
  ('... within the FMC UI..' against the alt '... within the FMC UI.'), becomes a <p>: an image caption, not a
  section title.
- split_inline_captions (true): a 'lia-inline-image-display-wrapper' span inside a <p> with text before (or after)
  it gets its own <p>, so its caption is not glued onto the sentence ('drop-down list.Select Groups from Directory').
- colspan ("pad" | "collapse"): a <td>/<th> with colspan=N > 1 loses the attribute; "pad" adds N-1 empty cells after
  it (the converter writes '-'), "collapse" adds none. Converter v2.1 copies a spanning cell's text into every column
  it spans, which duplicates layout spans (a 'Debugs' cell over two columns, a Feature cell over a slack column).
- cell_pre {"table_class", "first_line"?: [regex, ...]}: a table of one row and one cell, whose class holds
  table_class, becomes one <pre>: a line per <br> and per <p> of the cell (entities kept, a non-breaking space a space,
  so '&nbsp;tracking enable' keeps its indentation); with first_line, only when its first line matches one of them.
- cell_pre_classes [class, ...]: a <p> of one of these classes, inside a table cell too, becomes the DITA code block
  '<pre class="pre codeblock"><code>...</code></pre>' (a line per <br>): the 15M&T step tables' 'Example:'
  commands, set as <p class="fake_codeblock">.
- cmd_tables {"header": [label, ...]}: a table whose first row reads exactly these labels and whose every other row's
  first cell holds nothing but <pre> blocks becomes, per row, one <pre> (the cell's blocks; a block that starts with
  '{', '[' or '|' continues the line before it, joined with one space: 'ip inspect inspection-name' + '{in | out}')
  followed by the row's other cells' content: the command first, then its purpose (converter v2.1 writes a code-only
  cell after the row's text, so the purpose came first and the command lost its column).
- nested_tables_out (true): a row of a top-level table that holds a nested table (not a Note box, class olh_note)
  is cut out of it: the outer table closes after that row, the nested table follows as a table of its own, and the
  outer table reopens for the rows after it (converter v2.1 folds a nested table into its cell as one ' / '-joined
  line). When the nested table sits inside a block of the cell (a DITA <section class="itemgroup info"> with the
  table's lead-in sentence and Note), that whole block moves with it, so the table follows its own introduction.
- sup_caret (true): '<sup>N</sup>' right after a digit becomes '^N' ('(2<sup>20</sup>-1)' -> '(2^20-1)', as
  local-add5's EXPONENT rule writes powers).
- soft_hyphen (text): every soft hyphen (U+00AD, &shy;, &#173;) becomes this text (the converter deletes it, which
  glues 'DNS lookup&shy;RTT' into 'lookupRTT'; its siblings read 'TCP Connect--RTT').
- double_escaped [name, ...]: '&amp;name;' (a double-escaped entity the page prints literally) becomes the character
  ('data&amp;colon; 20 bytes' -> 'data: 20 bytes').
- p_headings "nest": true, "nest_stop": [title, ...]: after a <p> made a heading of level N, the headings that
  follow at level N or below are moved one level down until the next made heading, a heading above level N, or a
  heading whose title is in nest_stop: a group title set as <p class="topictitle1"> between the task topics it
  groups.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import sys
from pathlib import Path

HEADING = re.compile(r'(?is)<h([1-6])\b([^>]*)>(.*?)</h\1\s*>')
CLASS_ATTR = re.compile(r'(?i)\bclass\s*=\s*"([^"]*)"')
TAG = re.compile(r"<[^>]+>")
BR = re.compile(r"(?i)<br\s*/?>")
NBSP = re.compile(r"&nbsp;|&#160;|&#xa0;|\xa0", re.I)
TABLE = re.compile(r"(?i)<table\b|</table\s*>")


def _text(inner: str) -> str:
    return re.sub(r"\s+", " ", NBSP.sub(" ", TAG.sub("", inner))).strip()


def heading_levels(html: str, classes: dict, labels: list[str]) -> tuple[str, int]:
    labels_n = {x.casefold() for x in labels}
    last, n = 1, 0

    def fix(m: re.Match) -> str:
        nonlocal last, n
        cls = CLASS_ATTR.search(m.group(2))
        cls = cls.group(1).strip() if cls else ""
        level = int(m.group(1))
        if cls in classes:
            level = int(classes[cls])
        if _text(m.group(3)).casefold() in labels_n:
            level = min(6, last + 1)
        else:
            last = level
        if level != int(m.group(1)):
            n += 1
        return f"<h{level}{m.group(2)}>{m.group(3)}</h{level}>"

    return HEADING.sub(fix, html), n


def table_spans(html: str) -> list[tuple[int, int]]:
    """(start, end) of every top-level <table> ... </table> (nested tables counted)."""
    spans, depth, start = [], 0, 0
    for m in TABLE.finditer(html):
        if m.group(0).lower().startswith("<table"):
            if depth == 0:
                start = m.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                spans.append((start, m.end()))
    return spans


def element_lines(inner: str) -> list[str]:
    """The CLI lines of one example element: the source's formatting white space around the element's content and
    its line breaks inside inline markup are dropped (a browser does not show them either), a <br> starts a new line,
    and the leading spaces inside the content stay (' password letmein': the configuration's indentation)."""
    text = TAG.sub("", BR.sub("\0", inner.strip()))
    text = re.sub(r"[ \t]*[\r\n]+[ \t]*", " ", text)
    return [NBSP.sub(" ", line).rstrip() for line in text.split("\0")]


def _elem_re(classes: list[str]) -> re.Pattern:
    names = "|".join(re.escape(c) for c in classes)
    return re.compile(r'(?is)<(section|p|div)\b[^>]*\bclass\s*=\s*"\s*(' + names + r')\s*"[^>]*>(.*?)</\1\s*>')


def pre_classes(html: str, classes: list[str], absorb: list[dict] | None = None) -> tuple[str, int, int]:
    """(html, <pre> blocks made, lines in them). absorb: the pre_absorb rule (module docstring)."""
    absorb = absorb or []
    elem = _elem_re(list(classes) + [a["class"] for a in absorb if a["class"] not in classes])
    absorb_rx: dict[str, list[re.Pattern]] = {}
    for a in absorb:
        absorb_rx.setdefault(a["class"], []).append(re.compile(a["regex"]))
    tables = table_spans(html)

    def in_table(i: int) -> bool:
        return any(s <= i < e for s, e in tables)

    out, last, run, blocks, lines_n = [], 0, [], 0, 0

    def flush():
        nonlocal run, blocks, lines_n
        if run:
            while run and not run[0].strip():
                run.pop(0)
            while run and not run[-1].strip():
                run.pop()
            if run:
                out.append("<pre>" + "\n".join(run) + "</pre>")
                blocks += 1
                lines_n += len(run)
            run = []

    for m in elem.finditer(html):
        if in_table(m.start()):
            continue
        gap = html[last:m.start()]
        if m.group(2).strip() not in classes:                   # a pre_absorb candidate
            if run and not gap.strip() and any(r.search(_text(m.group(3)))
                                               for r in absorb_rx.get(m.group(2).strip(), ())):
                run += element_lines(m.group(3))
                last = m.end()
            continue                                            # else left in place (copied with the next gap)
        if run and not gap.strip():
            gap = ""
        else:
            flush()
        out.append(gap)
        run += element_lines(m.group(3))
        last = m.end()
    flush()
    out.append(html[last:])
    return "".join(out), blocks, lines_n


ROW = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr\s*>")
CELL = re.compile(r"(?is)<t([hd])\b[^>]*>(.*?)</t\1\s*>")


def pre_tables(html: str, label_sets: list[list[str]], classes: list[str]) -> tuple[str, int]:
    """(html, tables replaced): the pre_tables rule (module docstring)."""
    elem = _elem_re(classes)
    wanted = [[_text(x) for x in labels] for labels in label_sets]
    out, last, n = [], 0, 0
    for s, e in table_spans(html):
        rows = [[c.group(2) for c in CELL.finditer(r.group(1))] for r in ROW.finditer(html[s:e])]
        if len(rows) < 2 or [_text(c) for c in rows[0]] not in wanted:
            continue
        cols: list[list[str]] = [[] for _ in rows[0]]
        ok = True
        for row in rows[1:]:
            if len(row) != len(cols):
                ok = False
                break
            for k, cell in enumerate(row):
                if _text(elem.sub("", cell)):                   # anything but example lines in the cell
                    ok = False
                cols[k] += [line for m in elem.finditer(cell) for line in element_lines(m.group(3))]
        if not ok:
            continue
        parts = []
        for label, lines in zip(rows[0], cols):
            while lines and not lines[0].strip():
                lines.pop(0)
            while lines and not lines[-1].strip():
                lines.pop()
            parts.append(f"<p>{_text(label)}</p>")
            if lines:
                parts.append("<pre>" + "\n".join(lines) + "</pre>")
        out.append(html[last:s] + "\n".join(parts))
        last = e
        n += 1
    out.append(html[last:])
    return "".join(out), n


MADE = ' data-add8-made="1"'


def p_headings(html: str, classes: dict, require: str | None, nest: bool = False,
               nest_stop: list[str] | None = None) -> tuple[str, int]:
    """(html, paragraphs made headings): the p_headings rule (module docstring), with add8's nest option."""
    names = "|".join(re.escape(c) for c in classes)
    para = re.compile(r'(?is)<p\b([^>]*?\bclass\s*=\s*"\s*(' + names + r')\s*"[^>]*)>(.*?)</p\s*>')
    n = 0

    def fix(m: re.Match) -> str:
        nonlocal n
        if require and require not in m.group(3):
            return m.group(0)
        n += 1
        lvl = int(classes[m.group(2).strip()])
        return f"<h{lvl}{m.group(1)}{MADE if nest else ''}>{m.group(3)}</h{lvl}>"

    html = para.sub(fix, html)
    if not nest:
        return html, n
    stop = {re.sub(r"\s+", " ", x).strip().casefold() for x in nest_stop or []}
    level: int | None = None                     # the level of the open made heading, None outside one

    def shift(m: re.Match) -> str:
        nonlocal level
        lvl, attrs, inner = int(m.group(1)), m.group(2), m.group(3)
        if MADE in attrs:
            level = lvl
            return f"<h{lvl}{attrs.replace(MADE, '')}>{inner}</h{lvl}>"
        if level is None:
            return m.group(0)
        if lvl < level or html_lib.unescape(_text(inner)).casefold() in stop:
            level = None
            return m.group(0)
        new = min(6, lvl + 1)
        return f"<h{new}{attrs}>{inner}</h{new}>"

    return HEADING.sub(shift, html), n


# ---- add8 fix pass (module docstring) -----------------------------------------------------------------------------
CHILDLINK_UL = re.compile(r'(?is)<ul\b[^>]*>(?:\s*<li\b[^>]*\bclass\s*=\s*"[^"]*\b(?:ol|ul)childlink\b[^"]*"[^>]*>'
                          r'(?:(?!</li\s*>).)*</li\s*>)+\s*</ul\s*>')


def drop_childlinks(html: str) -> tuple[str, int]:
    """(html, child-link lists removed): the drop_childlinks rule."""
    return CHILDLINK_UL.subn("", html)


INLINE_RUN = re.compile(r"(?i)(?:</?(?:a|strong|b|i|em|code|tt|span|kbd|var|cite|samp|u|font|sup)\b[^>]*>)+")
RAW_BLOCK = re.compile(r"(?is)<(pre|script|style)\b.*?</\1\s*>")
ENTITY_END = re.compile(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]*);$", re.I)
ENTITY_START = re.compile(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]*);", re.I)
GLUE_BEFORE = re.compile(r"[\w®™.,;:)]")
GLUE_AFTER = re.compile(r"[\w(%]")


def _char_before(html: str, i: int) -> str:
    m = ENTITY_END.search(html[max(0, i - 12):i])
    return html_lib.unescape(m.group(0)) if m else html[i - 1:i]


def _char_after(html: str, i: int) -> str:
    m = ENTITY_START.match(html, i)
    return html_lib.unescape(m.group(0))[:1] if m else html[i:i + 1]


def glue_space(html: str) -> tuple[str, int]:
    """(html, spaces inserted): the glue_space rule."""
    raw = [(m.start(), m.end()) for m in RAW_BLOCK.finditer(html)]
    out, last, n = [], 0, 0
    for m in INLINE_RUN.finditer(html):
        if any(s <= m.start() < e for s, e in raw):
            continue
        b, a = _char_before(html, m.start()), _char_after(html, m.end())
        if not (b and a and GLUE_BEFORE.fullmatch(b) and GLUE_AFTER.fullmatch(a)) or b.isspace():
            continue
        if b in ".,;:" and a.isdigit():
            continue
        at = m.start() if not m.group(0).startswith("</") else m.end()
        out += [html[last:at], " "]
        last = at
        n += 1
    out.append(html[last:])
    return "".join(out), n


IMG_ALT = re.compile(r'(?i)<img\b[^>]*?\balt\s*=\s*"([^"]*)"')
CAPTION_SPAN = re.compile(r'(?i)<span\b[^>]*\bclass\s*=\s*"[^"]*\blia-inline-image-caption\b[^"]*"[^>]*>')


def unwrap_image_headings(html: str) -> tuple[str, int]:
    """(html, headings made paragraphs): the unwrap_image_headings rule."""
    n = 0

    def fix(m: re.Match) -> str:
        nonlocal n
        inner = m.group(3)
        if not re.search(r"(?i)<img\b", inner):
            return m.group(0)
        def norm(s: str) -> str:                                # trailing dots do not count ('UI..' vs 'UI.')
            return html_lib.unescape(re.sub(r"\s+", " ", s)).strip().rstrip(".").strip()
        vis = norm(_text(inner))
        alts = {norm(a) for a in IMG_ALT.findall(inner)}
        captions = {norm(_text(inner[c.end():_span_end(inner, c.start()) - len("</span>")]))
                    for c in CAPTION_SPAN.finditer(inner)}
        if vis and vis not in alts and vis not in captions:
            return m.group(0)
        n += 1
        return f"<p>{inner}</p>"

    return HEADING.sub(fix, html), n


WRAPPER = re.compile(r'(?i)<span\b[^>]*\bclass\s*=\s*"[^"]*\blia-inline-image-display-wrapper\b[^"]*"[^>]*>')
SPAN_TAG = re.compile(r"(?i)<span\b[^>]*>|</span\s*>")


def _span_end(html: str, start: int) -> int:
    """The index after the </span> that closes the <span> opening at start."""
    depth = 0
    for m in SPAN_TAG.finditer(html, start):
        depth += -1 if m.group(0).startswith("</") else 1
        if depth == 0:
            return m.end()
    return len(html)


def split_inline_captions(html: str) -> tuple[str, int]:
    """(html, captions given their own paragraph): the split_inline_captions rule."""
    n = 0

    def fix(m: re.Match) -> str:
        nonlocal n
        inner = m.group(1)
        w = WRAPPER.search(inner)
        if not w:
            return m.group(0)
        end = _span_end(inner, w.start())
        before, wrap, after = inner[:w.start()], inner[w.start():end], inner[end:]
        if not _text(before) and not _text(after):
            return m.group(0)
        n += 1
        open_tag = m.group(0)[:m.group(0).index(">") + 1]
        parts = [f"{open_tag}{before}</p>"] if _text(before) else []
        parts.append(f"<p>{wrap}</p>")
        if _text(after):
            parts.append(f"<p>{after}</p>")
        return "".join(parts)

    return P_ELEM.sub(fix, html), n


CELL_OPEN = re.compile(r'(?i)<(t[hd])\b([^>]*?)\s*\bcolspan\s*=\s*"?(\d+)"?([^>]*)>')
CELL_CLOSE = re.compile(r"(?i)</t[hd]\s*>|<table\b")


def colspan(html: str, mode: str) -> tuple[str, int]:
    """(html, spanning cells rewritten): the colspan rule ('pad' or 'collapse')."""
    if mode not in ("pad", "collapse"):
        raise ValueError(f"colspan: mode {mode!r} is not 'pad' or 'collapse'")
    out, last, n = [], 0, 0
    for m in CELL_OPEN.finditer(html):
        span = int(m.group(3))
        if span < 2:
            continue
        c = CELL_CLOSE.search(html, m.end())
        if c is None or c.group(0).lower().startswith("<table"):
            continue                                            # a nested table in the cell: left alone
        out += [html[last:m.start()], f"<{m.group(1)}{m.group(2)}{m.group(4)}>", html[m.end():c.end()]]
        if mode == "pad":
            out.append(f"<{m.group(1)}></{m.group(1)}>" * (span - 1))
        last = c.end()
        n += 1
    out.append(html[last:])
    return "".join(out), n


def _cells_of_single_cell_table(t: str) -> str | None:
    rows = list(ROW.finditer(t))
    if len(rows) != 1:
        return None
    cells = list(CELL.finditer(rows[0].group(1)))
    return cells[0].group(2) if len(cells) == 1 else None


def cell_pre(html: str, rule: dict) -> tuple[str, int]:
    """(html, one-cell tables made <pre>): the cell_pre rule."""
    want = rule["table_class"]
    firsts = [re.compile(x) for x in rule.get("first_line") or []]
    out, last, n = [], 0, 0
    for s, e in table_spans(html):
        t = html[s:e]
        cls = CLASS_ATTR.search(t[:t.index(">")])
        if not cls or want not in cls.group(1).split():
            continue
        inner = _cells_of_single_cell_table(t)
        if inner is None or re.search(r"(?i)<(table|pre|img|ul|ol)\b", inner):
            continue
        paras = [m.group(1) for m in P_ELEM.finditer(inner)] or [inner]
        lines = [x for p in paras for x in element_lines(p)]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines or (firsts and not any(r.search(lines[0].strip()) for r in firsts)):
            continue
        out += [html[last:s], "<pre>" + "\n".join(lines) + "</pre>"]
        last = e
        n += 1
    out.append(html[last:])
    return "".join(out), n


def cell_pre_classes(html: str, classes: list[str]) -> tuple[str, int]:
    """(html, paragraphs made code blocks): the cell_pre_classes rule."""
    names = "|".join(re.escape(c) for c in classes)
    para = re.compile(r'(?is)<p\b[^>]*\bclass\s*=\s*"\s*(?:' + names + r')\s*"[^>]*>(.*?)</p\s*>')
    n = 0

    def fix(m: re.Match) -> str:
        nonlocal n
        lines = [x.strip() for x in element_lines(m.group(1))]
        lines = [x for x in lines if x]
        if not lines:
            return m.group(0)
        n += 1
        return '<pre class="pre codeblock"><code>' + "\n".join(lines) + "</code></pre>"

    return para.sub(fix, html), n


PRE_EL = re.compile(r"(?is)<pre\b[^>]*>(.*?)</pre\s*>")


def _pre_lines(inner: str) -> list[str]:
    text = TAG.sub("", inner)
    if text.startswith("\r\n"):
        text = text[2:]
    elif text.startswith("\n"):
        text = text[1:]
    return [x.rstrip() for x in NBSP.sub(" ", text).split("\n")]


def cmd_tables(html: str, rule: dict) -> tuple[str, int]:
    """(html, tables rewritten): the cmd_tables rule."""
    want = [_text(x) for x in rule["header"]]
    out, last, n = [], 0, 0
    for s, e in table_spans(html):
        t = html[s:e]
        if re.search(r"(?i)<table\b", t[1:]):
            continue                                            # a nested table: left alone
        rows = [[c.group(2) for c in CELL.finditer(r.group(1))] for r in ROW.finditer(t)]
        if len(rows) < 2 or [_text(c) for c in rows[0]] != want:
            continue
        if not all(row and PRE_EL.search(row[0]) and not _text(PRE_EL.sub("", row[0])) for row in rows[1:]):
            continue
        parts = []
        for row in rows[1:]:
            lines: list[str] = []
            for m in PRE_EL.finditer(row[0]):
                frag = [x for x in _pre_lines(m.group(1))]
                while frag and not frag[-1].strip():
                    frag.pop()
                if not frag:
                    continue
                if lines and frag[0].lstrip()[:1] in ("{", "[", "|"):
                    lines[-1] = lines[-1].rstrip() + " " + frag[0].strip()
                    frag = frag[1:]
                lines += frag
            parts.append("<pre>" + "\n".join(lines) + "</pre>")
            parts += [f"<div>{c}</div>" for c in row[1:]]
        out += [html[last:s], "\n".join(parts)]
        last = e
        n += 1
    out.append(html[last:])
    return "".join(out), n


TABLE_TOK = re.compile(r"(?i)<table\b[^>]*>|</table\s*>|<tr\b[^>]*>|</tr\s*>|<tbody\b[^>]*>|</tbody\s*>")
ANY_TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)\b[^>]*?(/?)>")
VOID = {"br", "img", "hr", "input", "meta", "link", "wbr", "col", "area", "source", "embed", "param", "track"}


def _block_around(row: str, a: int, b: int) -> tuple[int, int]:
    """The span of the cell child that holds row[a:b] (a nested table): the outermost element inside its <td>/<th>
    that contains it, so the table moves out with its lead-in paragraph and Note (a DITA 'itemgroup info' section);
    (a, b) itself when the table is a direct child of the cell or the markup cannot be followed."""
    start, tdepth = None, 0
    for m in re.finditer(r"(?i)<table\b[^>]*>|</table\s*>|<t[dh]\b[^>]*>", row):
        if m.start() >= a:
            break
        tok = m.group(0).lower()
        if tok.startswith("<table"):
            tdepth += 1
        elif tok.startswith("</table"):
            tdepth -= 1
        elif tdepth == 0:
            start = m.end()                                     # a cell of the row itself, not of a nested table
    if start is None:
        return a, b
    depth, top = 0, None
    for m in ANY_TAG.finditer(row, start):
        if m.start() >= a:
            break
        name = m.group(2).lower()
        if name in VOID or m.group(3):
            continue
        if m.group(1):
            depth -= 1
            if depth < 0:
                return a, b                                    # the cell closed before the table: give up
        else:
            if depth == 0:
                top = m.start()
            depth += 1
    if top is None or depth == 0:
        return a, b
    d = 0
    for m in ANY_TAG.finditer(row, top):
        name = m.group(2).lower()
        if name in VOID or m.group(3):
            continue
        d += -1 if m.group(1) else 1
        if d == 0:
            return (top, m.end()) if m.end() >= b else (a, b)
    return a, b


def nested_tables_out(html: str) -> tuple[str, int]:
    """(html, nested tables moved out of their row): the nested_tables_out rule."""
    out, last, n = [], 0, 0
    for s, e in table_spans(html):
        t = html[s:e]
        open_tag = t[:t.index(">") + 1]
        depth, row_start, in_tbody = 0, None, False
        pieces, pos = [], 0
        for m in TABLE_TOK.finditer(t):
            tok = m.group(0).lower()
            if tok.startswith("<table"):
                depth += 1
            elif tok.startswith("</table"):
                depth -= 1
            elif depth == 1 and tok.startswith("<tbody"):
                in_tbody = True
            elif depth == 1 and tok.startswith("</tbody"):
                in_tbody = False
            elif depth == 1 and tok.startswith("<tr"):
                row_start = m.start()
            elif depth == 1 and tok.startswith("</tr") and row_start is not None:
                row = t[row_start:m.end()]
                nested = [(a, b) for a, b in table_spans(row)
                          if "olh_note" not in (CLASS_ATTR.search(row[a:row.index(">", a)]) or [None, ""])[1]]
                blocks: list[tuple[int, int]] = []
                for a, b in nested:                                 # move each with the cell child around it
                    s2, e2 = _block_around(row, a, b)
                    if not blocks or s2 >= blocks[-1][1]:
                        blocks.append((s2, e2))
                nested = blocks
                if nested:
                    kept = "".join(row[x:y] for x, y in zip([0] + [b for _, b in nested], [a for a, _ in nested]
                                                                + [len(row)]))
                    moved = "\n".join(row[a:b] for a, b in nested)
                    close = "</tbody></table>" if in_tbody else "</table>"
                    reopen = open_tag + ("<tbody>" if in_tbody else "")
                    pieces += [t[pos:row_start], kept, close, "\n", moved, "\n", reopen]
                    pos = m.end()
                    n += len(nested)
                row_start = None
        if pos:
            pieces.append(t[pos:])
            out += [html[last:s], "".join(pieces)]
            last = e
    out.append(html[last:])
    return "".join(out), n


SUP_POWER = re.compile(r"(?i)(?<=\d)<sup>\s*(-?\d+)\s*</sup>")


def sup_caret(html: str) -> tuple[str, int]:
    """(html, powers rewritten): the sup_caret rule."""
    return SUP_POWER.subn(r"^\1", html)


def soft_hyphen(html: str, text: str) -> tuple[str, int]:
    """(html, soft hyphens replaced): the soft_hyphen rule."""
    return re.subn(r"­|&shy;|&#173;|&#xad;", lambda _m: text, html, flags=re.I)


def double_escaped(html: str, names: list[str]) -> tuple[str, int, list[str]]:
    """(html, entities decoded, names not found): the double_escaped rule."""
    n, missing = 0, []
    for name in names:
        html, k = re.subn("&amp;" + re.escape(name) + ";", html_lib.unescape(f"&{name};"), html)
        n += k
        if not k:
            missing.append(name)
    return html, n, missing


C_NEWLINE = re.compile(r"\s*(?:\\n(?:\\(?![nt]))?\s*(?:\\t\s*)*)+")   # '\n', '\n\', '\n\n\ ', '\n \t' + white space
C_JOINT = re.compile(r'\s*(?:"|&quot;)\s+(?:"|&quot;)\s*')        # '... " "...': two C string literals
C_QUOTE = re.compile(r'\\(?:"|&quot;)')


def cstring(inner: str) -> tuple[str, int]:
    """(cell content, escapes and joints normalised): the cstring part of table_rows (module docstring). Works on the
    cell's HTML; the text inside its <p> wrapper is what changes."""
    m = re.fullmatch(r"(?is)(\s*(?:<p\b[^>]*>)?)(.*?)((?:</p\s*>)?\s*)", inner)
    head, body, tail = m.group(1), m.group(2), m.group(3)
    n = 0
    new, k = C_NEWLINE.subn(" ", body)
    n += k
    new = new.strip()
    if re.match(r'(?:"|&quot;)', new):
        new, k1 = C_JOINT.subn(" ", new)
        new, k2 = C_QUOTE.subn('"', new)
        n += k1 + k2
    if new == body.strip():
        return inner, 0
    return head + new + tail, n


def table_rows(html: str, rule: dict) -> tuple[str, dict]:
    """(html, counts): the table_rows rule (module docstring)."""
    keep = re.compile(rule["keep"])
    want = _text(rule["header"])
    cols = list(rule.get("placeholder") or [])
    counts = {"table_rows_tables": 0, "table_rows_kept": 0, "table_rows_dropped": 0, "table_rows_placeholders": 0,
              "table_rows_cstring": 0}
    out, last = [], 0
    for s, e in table_spans(html):
        rows = list(ROW.finditer(html, s, e))
        first = CELL.search(rows[0].group(1)) if rows else None
        if not first or _text(first.group(2)) != want:
            continue
        counts["table_rows_tables"] += 1
        out.append(html[last:rows[1].start() if len(rows) > 1 else e])
        pos = rows[1].start() if len(rows) > 1 else e
        for r in rows[1:]:
            out.append(html[pos:r.start()])                     # white space (and any markup) between rows
            pos = r.end()
            cells = list(CELL.finditer(r.group(1)))
            texts = [_text(c.group(2)) for c in cells]
            if not texts or not keep.match(texts[0]):
                counts["table_rows_dropped"] += 1
                continue
            if cols and all(i < len(texts) and texts[i] == "-" for i in cols):
                counts["table_rows_placeholders"] += 1
                continue
            row = r.group(0)
            if rule.get("cstring"):
                parts, at, base = [], 0, r.start(1) - r.start()
                for c in cells:
                    new, k = cstring(c.group(2))
                    counts["table_rows_cstring"] += k
                    parts += [row[at:base + c.start(2)], new]
                    at = base + c.end(2)
                row = "".join(parts) + row[at:]
            out.append(row)
            counts["table_rows_kept"] += 1
        out.append(html[pos:e])
        last = e
    out.append(html[last:])
    if not counts["table_rows_tables"]:
        counts["table_rows_unmatched"] = rule["header"]
    return "".join(out), counts


PARA = re.compile(r"(?is)<p\b[^>]*>(.*?)</p\s*>")


def pre_paras(html: str, texts: list[str]) -> tuple[str, int, list[str]]:
    """(html, paragraphs made code blocks, texts not matched exactly once): the pre_paras rule (module docstring)."""
    want = {re.sub(r"\s+", " ", t).strip() for t in texts}
    hits = {t: [m for m in PARA.finditer(html) if _text(m.group(1)) == t] for t in want}
    bad = sorted(t for t, ms in hits.items() if len(ms) != 1)
    out, last, n = [], 0, 0
    for m in sorted((ms[0] for t, ms in hits.items() if len(ms) == 1), key=lambda m: m.start()):
        out += [html[last:m.start()], f'<pre class="pre codeblock"><code>{_text(m.group(1))}</code></pre>']
        last = m.end()
        n += 1
    out.append(html[last:])
    return "".join(out), n, bad


def drop_html(html: str, patterns: list[str]) -> tuple[str, int, list[str]]:
    """(html, matches removed, patterns that matched nothing): the drop_html rule (module docstring)."""
    n, unmatched = 0, []
    for pat in patterns:
        html, k = re.subn(pat, "", html, flags=re.S)
        n += k
        if not k:
            unmatched.append(pat)
    return html, n, unmatched


IMG_SRC = re.compile(r"""(?is)<img\b[^>]*?\ssrc\s*=\s*["']([^"']*)["'][^>]*>""")


def img_text(html: str, mapping: dict) -> tuple[str, int, list[str]]:
    """add8 rule img_text {src_substring: text}: every <img> whose src contains a listed substring is replaced by its
    text (a legend swatch with no alt text, whose colour was read from the image file). Returns (html, images
    replaced, substrings that matched no image)."""
    seen: set[str] = set()
    n = 0

    def rep(m: re.Match) -> str:
        nonlocal n
        key = next((k for k in mapping if k in m.group(1)), None)
        if key is None:
            return m.group(0)
        seen.add(key)
        n += 1
        return mapping[key]
    return IMG_SRC.sub(rep, html), n, sorted(set(mapping) - seen)


P_ELEM = re.compile(r"(?is)<p\b[^>]*>((?:(?!<p\b).)*?)</p\s*>")


def br_pre(html: str, patterns: list[str]) -> tuple[str, int, list[str]]:
    """add8 rule br_pre [regex, ...]: a <p> that holds <br> line breaks and whose first non-blank line (tags removed)
    matches one of the regexes (re.search) becomes one <pre>, a line per <br>: the CLI blocks of a web page that sets
    configurations and show output as paragraphs. Entities are kept, tags removed, a non-breaking space becomes a
    space, trailing white space and the block's blank first and last lines are dropped. Returns (html, paragraphs
    turned into <pre>, regexes that matched none)."""
    pats = [re.compile(p) for p in patterns]
    used: set[int] = set()
    n = 0

    def rep(m: re.Match) -> str:
        nonlocal n
        inner = m.group(1)
        if not BR.search(inner):
            return m.group(0)
        lines = [NBSP.sub(" ", TAG.sub("", x)).lstrip("\r\n").rstrip() for x in BR.split(inner)]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        hit = next((i for i, p in enumerate(pats) if lines and p.search(lines[0].strip())), None)
        if hit is None:
            return m.group(0)
        used.add(hit)
        n += 1
        return "<pre>" + "\n".join(lines) + "</pre>"
    return P_ELEM.sub(rep, html), n, [p for i, p in enumerate(patterns) if i not in used]


FIX_RULES = ("drop_childlinks", "double_escaped", "soft_hyphen", "sup_caret", "unwrap_image_headings",
             "split_inline_captions", "cell_pre", "cell_pre_classes", "cmd_tables", "nested_tables_out", "colspan",
             "glue_space")                                   # the add8 fix-pass rules (module docstring)


def prep(html: str, rules: dict) -> tuple[str, dict]:
    """Order: img_text, drop_html, drop_childlinks, double_escaped, soft_hyphen, sup_caret, unwrap_image_headings,
    split_inline_captions, br_pre, cell_pre, cell_pre_classes, cmd_tables, nested_tables_out, colspan, glue_space
    (after the rules that make <pre>, so CLI stays as written), table_rows, heading_levels, p_headings, pre_tables,
    pre_classes, pre_paras. A fix-pass rule that changes nothing is reported in counts['fix_unmatched']."""
    counts: dict = {}
    unmatched_fix: list[str] = []

    def fix(name: str, result: tuple) -> str:
        nonlocal html
        html, k = result[0], result[1]
        counts[name] = k
        if not k:
            unmatched_fix.append(name)
        return html

    if rules.get("img_text"):
        html, counts["img_text"], unmatched = img_text(html, rules["img_text"])
        if unmatched:
            counts["img_text_unmatched"] = unmatched
    if rules.get("drop_html"):
        html, counts["html_dropped"], unmatched = drop_html(html, rules["drop_html"])
        if unmatched:
            counts["drop_html_unmatched"] = unmatched
    if rules.get("drop_childlinks"):
        fix("drop_childlinks", drop_childlinks(html))
    if rules.get("double_escaped"):
        html, counts["double_escaped"], missing = double_escaped(html, rules["double_escaped"])
        unmatched_fix += [f"double_escaped {x}" for x in missing]
    if rules.get("soft_hyphen"):
        fix("soft_hyphen", soft_hyphen(html, rules["soft_hyphen"]))
    if rules.get("sup_caret"):
        fix("sup_caret", sup_caret(html))
    if rules.get("unwrap_image_headings"):
        fix("unwrap_image_headings", unwrap_image_headings(html))
    if rules.get("split_inline_captions"):
        fix("split_inline_captions", split_inline_captions(html))
    if rules.get("br_pre"):
        html, counts["br_pre"], unmatched = br_pre(html, rules["br_pre"])
        if unmatched:
            counts["br_pre_unmatched"] = unmatched
    if rules.get("cell_pre"):
        fix("cell_pre", cell_pre(html, rules["cell_pre"]))
    if rules.get("cell_pre_classes"):
        fix("cell_pre_classes", cell_pre_classes(html, rules["cell_pre_classes"]))
    if rules.get("cmd_tables"):
        fix("cmd_tables", cmd_tables(html, rules["cmd_tables"]))
    if rules.get("nested_tables_out"):
        fix("nested_tables_out", nested_tables_out(html))
    if rules.get("colspan"):
        fix("colspan", colspan(html, rules["colspan"]))
    if rules.get("glue_space"):
        fix("glue_space", glue_space(html))
    if unmatched_fix:
        counts["fix_unmatched"] = unmatched_fix
    if rules.get("table_rows"):
        html, tr = table_rows(html, rules["table_rows"])
        counts.update(tr)
    hl = rules.get("heading_levels")
    if hl:
        html, counts["headings_releveled"] = heading_levels(html, hl.get("classes") or {}, hl.get("labels") or [])
    ph = rules.get("p_headings")
    if ph:
        html, counts["p_headings"] = p_headings(html, ph.get("classes") or {}, ph.get("require"), ph.get("nest", False),
                                                ph.get("nest_stop"))
    if rules.get("pre_tables"):
        html, counts["pre_tables"] = pre_tables(html, rules["pre_tables"], rules.get("pre_classes") or [])
    if rules.get("pre_classes"):
        html, counts["pre_blocks"], counts["pre_lines"] = pre_classes(html, rules["pre_classes"],
                                                                      rules.get("pre_absorb"))
    if rules.get("pre_paras"):
        html, counts["pre_paras"], unmatched = pre_paras(html, rules["pre_paras"])
        if unmatched:
            counts["pre_paras_unmatched"] = unmatched
    return html, counts


def main(argv: list[str]) -> int:
    src, dst, rules = Path(argv[0]), Path(argv[1]), json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    html = src.read_text(encoding="utf-8")
    new, counts = prep(html, rules)
    if new == html:
        print("prep8: clean")
        return 0
    dst.write_text(new, encoding="utf-8")
    print("prep8: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
