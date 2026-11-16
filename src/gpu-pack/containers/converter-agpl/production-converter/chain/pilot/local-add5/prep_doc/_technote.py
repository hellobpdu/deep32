"""Source rules for cisco.com TechNote articles (fetch_add5 'article' pages and TechNote ePub members), used by the
doc-scoped modules of this package. Not a fix module itself (the registry skips names that start with '_').

- sptable_pre: Cisco's one-cell code boxes. A <table class="sptable"> with exactly one <tr> and one <td> and no
  nested table is a box of CLI, show output or log lines set in <p>/<br>, which converter v2/v2.1 would write as one
  table row (every <br> line joined into one field). It becomes <pre>: each <br> and each paragraph boundary is a
  line break, runs of source whitespace collapse to one space (as a browser renders the <p>), tags are dropped and
  their text kept ('[i<strong>p=10...' -> '[ip=10...'), entities stay encoded for the converter's parser, a
  non-breaking space is a space (leading ones indent a line, trailing ones are dropped), and the blank lines at
  either end are dropped (inner blank lines stay). Multi-cell sptables stay tables. need: a pattern the box's text
  must match (cisco-tn-ise-repository: an ISE prompt), else the box stays a table.
- cmdname_pre: Cisco's CLI blocks <code class="cCN_CmdName" style="white-space: pre-wrap">. One that holds a line
  break or an ISE prompt (host/user#) is CLI output, not an inline command name; it becomes <pre> with its text as
  written (pre-wrap keeps the line breaks). Inline command names in a sentence stay <code>.
- caption_block: an image caption <span class="lia-inline-image-caption" style="display: block"> is a block in the
  browser; written as <p>, it is its own line instead of being glued to the sentence before it ('Save.Setting
  Component').
- drop_empty_spoilers: a community-page spoiler box <div class="lia-spoiler-container"> whose content is empty
  leaves only its link text 'Spoiler' (cisco-tn-ise-repository's last line); the box is dropped up to its matching
  </div>. Empty means the box's <div class="lia-spoiler-content"> (any other attributes, nested divs counted) holds
  no text but whitespace and non-breaking spaces. A spoiler with content, or one without a content box this rule
  can find, stays.
"""
from __future__ import annotations

import re

SPTABLE = re.compile(r'(?s)<table\b[^>]*\bclass="sptable"[^>]*>(.*?)</table>')
TAG = re.compile(r"<[^>]+>")
BR = re.compile(r"(?i)<br\s*/?>")
PARA_BREAK = re.compile(r"(?i)</p>\s*<p\b[^>]*>")
CMDNAME = re.compile(r'(?s)<code\b([^>]*\bclass="cCN_CmdName"[^>]*)>(.*?)</code>')
ISE_PROMPT = re.compile(r"(?m)^\s*[A-Za-z0-9][\w.-]*/[\w.-]+#")
CAPTION = re.compile(r'(?s)<span\b([^>]*\bclass="lia-inline-image-caption"[^>]*)>(.*?)</span>')
NBSP = re.compile(r"&nbsp;|&#160;|&#xa0;|\xa0", re.I)
DIV = re.compile(r"(?i)<div\b|</div\s*>")
SPOILER = '<div class="lia-spoiler-container"'
SPOILER_CONTENT = re.compile(r'(?i)<div\b[^>]*\bclass="lia-spoiler-content"[^>]*>')


def _box_text(cell: str) -> str:
    s = re.sub(r"[ \t\r\n]+", " ", cell)
    s = PARA_BREAK.sub("\n", s)
    s = BR.sub("\n", s)
    s = TAG.sub("", s)
    lines = [NBSP.sub(" ", line.strip(" ")).rstrip() for line in s.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def sptable_pre(html: str, need: re.Pattern | None = None) -> tuple[str, int]:
    n = 0

    def rep(m: re.Match) -> str:
        nonlocal n
        inner = m.group(1)
        if re.search(r"(?i)<table\b", inner) or len(re.findall(r"(?i)<tr\b", inner)) != 1 \
                or len(re.findall(r"(?i)<t[dh]\b", inner)) != 1:
            return m.group(0)
        cell = re.search(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>", inner)
        text = _box_text(cell.group(1)) if cell else ""
        if not text or (need is not None and not need.search(text)):
            return m.group(0)
        n += 1
        return f"<pre>{text}</pre>"

    return SPTABLE.sub(rep, html), n


def cmdname_pre(html: str) -> tuple[str, int]:
    n = 0

    def rep(m: re.Match) -> str:
        nonlocal n
        body = m.group(2)
        text = TAG.sub("", body)
        if re.search(r"(?i)<code\b", body) or not text.strip():
            return m.group(0)
        if "\n" not in text.strip("\n ") and not ISE_PROMPT.search(text):
            return m.group(0)
        n += 1
        return f"<pre>{body}</pre>"

    return CMDNAME.sub(rep, html), n


def caption_block(html: str) -> tuple[str, int]:
    return CAPTION.subn(lambda m: f"<p>{m.group(2)}</p>", html)


def _div_end(html: str, start: int) -> int | None:
    """Index just after the </div> that closes the div opening at start (nested divs counted)."""
    depth = 0
    for m in DIV.finditer(html, start):
        depth += 1 if m.group(0).lower().startswith("<div") else -1
        if depth == 0:
            return m.end()
    return None


def drop_empty_spoilers(html: str) -> tuple[str, int]:
    n, i = 0, 0
    while (i := html.find(SPOILER, i)) >= 0:
        end = _div_end(html, i)
        if end is None:
            break
        content = SPOILER_CONTENT.search(html, i, end)
        c_end = _div_end(html, content.start()) if content else None
        if c_end is None or c_end > end or NBSP.sub("", TAG.sub("", html[content.end():c_end])).strip():
            i = end                                       # a spoiler with content (or no content box found) stays
            continue
        html = html[:i] + html[end:]
        n += 1
    return html, n
