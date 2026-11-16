"""Section structure of a source text file: stable section ids and line ranges.

Section ids are "<doc_key>#<anchor>" (BUILD-P1 section 3):

    RFC    the section number: "3.2" ("1.0" is written "1"), "appendix-a", "appendix-a.1";
           an unnumbered heading ("Abstract", "Security Considerations") becomes a slug
    Cisco  a slug of the heading text (Markdown ATX headings outside ``` fences)
    both   text before the first heading is "preamble"; a repeated anchor gets "-2", "-3" ...
           in document order

slug: lowercase; each run of characters other than a-z and 0-9 becomes "-"; trimmed to 60 chars.

Line numbers are 1-based and count "\n"-separated lines of the file exactly as stored: a form
feed does not end a line (str.splitlines() would split on it). The KB producers (cli_index,
feature_history) count lines the same way.

A section runs from its heading line to the line before the next heading of the same or a higher
level, so it includes its subsections. RFC nesting comes from the section number, not from the
layout: "Abstract" and "1.0 Overview" are both top level.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


PREAMBLE = "preamble"

RFC_PAGE_HEADER = re.compile(r"^\f?RFC \d{1,5}\s{2,}(?:.*\s{2,})?\S.*\d{4}\s*$")
RFC_PAGE_FOOTER = re.compile(r"\[Page \d+\]\s*$")
# table-of-contents entry: dot leaders, or a wide gap before the page number
TOC_LINE = re.compile(r"(\.\s?){4,}\s*\d+\s*$|\S {3,}[ivx\d]+\s*$")
CENTERED = 10       # indentation of the centered headings of old RFCs ("1.  INTRODUCTION")

RFC_NUMBERED = re.compile(r"^( *)(\d{1,2}(?:\.\d{1,3})*)\.?\s+(\S.*)$")
RFC_LETTERED = re.compile(r"^( *)([A-Z])\.((?:\d{1,3}\.?)*)\s+(\S.*)$")
# "Appendix F", "APPENDIX B:", and a subsection of one, "Appendix F.1." (RFC 4271)
RFC_APPENDIX = re.compile(
    r"^( *)(?:Appendix|APPENDIX|Annex|ANNEX)\s+([A-Z]|[IVX]{1,4}|\d{1,2})((?:\.\d{1,3})*)\b[.:]?\s*(.*)$"
)
RFC_UNNUMBERED = re.compile(r"^[A-Z][A-Za-z0-9 ,'()/&-]{2,70}$")
TITLE_START = re.compile(r"[A-Za-z\"'(\[]")
INDENTED_LEVELS = 3     # an indented subsection heading is numbered at least this deep (_indented_heading)

MD_HEADING = re.compile(r"^(#{1,6}) (.*)$")
FENCE = "```"


@dataclass
class Section:
    anchor: str
    heading: str | None     # heading text as printed, stripped; None for the preamble
    level: int              # 0 = preamble, 1 = top level
    line_start: int         # 1-based heading line (preamble: first line)
    line_end: int = 0       # last line, subsections included
    parent: int | None = None   # index of the parent in the section list


def split_lines(text: str) -> list[str]:
    """Lines as the KB producers count them: split on "\n" only, "\r" and a byte-order mark
    dropped (neither changes the line count)."""
    lines = text.removeprefix('\ufeff').split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.rstrip("\r") for line in lines]


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60].strip("-") or "section"


def page_furniture(lines: list[str]) -> set[int]:
    """0-based indexes of RFC page headers ("RFC 3101   title   January 2003", plus a title
    line directly under a header, as in RFC 959) and footers ("Murphy ...   [Page 16]")."""
    found: set[int] = set()
    for i, line in enumerate(lines):
        if RFC_PAGE_FOOTER.search(line):
            found.add(i)
        elif RFC_PAGE_HEADER.match(line):
            found.add(i)
            if i + 1 < len(lines) and lines[i + 1].strip() and _indent(lines[i + 1]) == 0:
                found.add(i + 1)
    return found


def _sentence(title: str) -> bool:
    return bool(re.search(r"[.,;:]$", title)) and len(title.split()) > 8


def _continues(last: tuple, order: tuple) -> bool:
    """`order` comes next in the numbering after `last` (order keys of _rfc_heading): the first
    child of `last` (2.1 -> 2.1.1), or the next sibling of `last` or of one of its ancestors
    (2.1.1 -> 2.1.2, 12.4.1.5 -> 12.4.2; D.4.3 -> D.5)."""
    if len(order) == len(last) + 1:
        return order[:-1] == last and order[-1] == 1
    k = len(order) - 1
    return (
        k < len(last)
        and order[:k] == last[:k]
        and isinstance(order[k], int)
        and isinstance(last[k], int)
        and order[k] == last[k] + 1
    )


def _numbered(line: str) -> tuple[str, int, tuple, str, str] | None:
    """(anchor, level, order key, number as printed, title) of a numbered ("2.1.1.  Title") or
    lettered ("A.1  Title", an appendix subsection) line."""
    m = RFC_LETTERED.match(line)
    if m:
        parts = [p for p in m.group(3).split(".") if p]
        anchor = "appendix-" + ".".join([m.group(2).lower(), *parts])
        return anchor, 1 + len(parts), (1, m.group(2), *map(int, parts)), "", m.group(4)
    m = RFC_NUMBERED.match(line)
    if m:
        parts = m.group(2).split(".")
        if len(parts) == 2 and parts[1] == "0":
            parts = parts[:1]            # "1.0  Overview" is section 1
        return ".".join(parts), len(parts), (0, *map(int, parts)), m.group(2), m.group(3)
    return None


def _indented_heading(
    line: str,
    standalone: bool,
    last: tuple,
) -> tuple[str, int, tuple] | None:
    """A subsection heading indented like the body text: RFC 2328 "        2.1.1.  Representation
    of non-broadcast networks" (body at column 8), RFC 959 "      3.1.1.  DATA TYPES". Taken only
    when it stands alone, is numbered at least INDENTED_LEVELS deep (an appendix letter counts:
    "D.4.1"), has no closing punctuation and comes next in the numbering (_continues): indented
    lists number "1." to "n.", and an indented "4.2.1" elsewhere is a reference or a value."""
    found = _numbered(line) if standalone else None
    if found is None:
        return None
    anchor, level, order, _, title = found
    if (
        level < INDENTED_LEVELS
        or not TITLE_START.match(title)
        or re.search(r"[.,;:]$", title)
        or not _continues(last, order)
    ):
        return None
    return anchor, level, order


def _rfc_heading(
    line: str,
    max_indent: int,
    standalone: bool,
    last: tuple = (0,),
) -> tuple[str, int, tuple | None] | None:
    """(anchor, level, order key) when `line` has the shape of an RFC heading. `last` is the
    order key of the last numbered heading taken.

    The order key places numbered sections before appendices; unnumbered headings have none.
    """
    indent = _indent(line)
    if TOC_LINE.search(line) or len(line) > 120:
        return None
    centered = standalone and indent >= CENTERED
    found = None
    if indent <= max_indent or centered:
        found = _placed_heading(line, max_indent, standalone, last)
    if found is None and indent > max_indent:
        found = _indented_heading(line, standalone, last)
    return found


def _placed_heading(
    line: str,
    max_indent: int,
    standalone: bool,
    last: tuple,
) -> tuple[str, int, tuple | None] | None:
    """A heading at a heading's place: at most max_indent deep, or centered."""
    indent = _indent(line)
    heading = line.strip()

    m = RFC_APPENDIX.match(line)
    if m:
        label = m.group(2).upper()
        parts = [p for p in m.group(3).split(".") if p]
        anchor = "appendix-" + ".".join([label.lower(), *parts])
        return anchor, 1 + len(parts), (1, label, *map(int, parts))

    # a centered numbered heading is upper case ("1.  INTRODUCTION"); a centered
    # "3. The LSA with the higher router ID." is an indented list item
    if indent > max_indent and not heading.isupper():
        return None

    found = _numbered(line)
    if found is None:
        if (
            indent == 0
            and standalone
            and RFC_UNNUMBERED.match(heading)
            and len(heading.split()) <= 10
        ):
            return slugify(heading), 1, None
        return None

    anchor, level, order, number, title = found
    if order[0] == 1:
        if level == 1 and indent:
            return None                  # "A. foo" is an appendix only at column 0
    elif not TITLE_START.match(title):
        return None
    elif indent > max_indent and "." not in heading[: len(number) + 1]:
        # a centered chapter number prints its dot ("1.  INTRODUCTION"); a centered
        # "4 CMAC-AES-192 [NIST]" is a value list item (RFC 6218 section 3.3)
        return None
    # a long title that ends in punctuation is a numbered paragraph, unless it is the next
    # subsection in the numbering: RFC 3168 "9.1.2.  Changes to the ECN Field within an IP
    # Tunnel." after 9.1.1
    if _sentence(title) and not (level > 1 and _continues(last, order)):
        return None
    return anchor, level, order


def parse_rfc(lines: list[str]) -> list[Section]:
    """Sections of an RFC. A heading line follows a blank line and stands alone (a numbered one
    may run straight into a more indented body). It is placed at a heading's indent (column 0,
    or up to column 4 when the body sits at column 6 or deeper), centered and upper case, or
    indented like the body when it is the next subsection in the numbering (_indented_heading).
    Numbers only move forward."""
    furniture = page_furniture(lines)
    content = [
        i for i, line in enumerate(lines)
        if line.strip() and i not in furniture
    ]
    indents = Counter(_indent(lines[i]) for i in content)
    body_indent = indents.most_common(1)[0][0] if indents else 3
    # RFC 2328-era layout: body at column 8, subsection headings at column 4
    max_indent = 4 if body_indent >= 6 else 0

    # the header block (Network Working Group, Request for Comments: ...) is never a heading
    header_end = content[0] if content else 0
    while header_end + 1 < len(lines) and lines[header_end + 1].strip():
        header_end += 1

    sections: list[Section] = []
    last: tuple = (0,)
    # indent -> number of the last indented "n." line passed over since the last heading taken
    items: dict[int, int] = {}
    for n, i in enumerate(content):
        if i <= header_end or not _gap(lines, content[n - 1], i):
            continue
        nxt = content[n + 1] if n + 1 < len(content) else None
        standalone = nxt is None or _gap(lines, i, nxt)
        found = _rfc_heading(lines[i], max_indent, standalone, last)
        item = _list_number(lines[i])
        if found is not None:
            anchor, level, order = found
            # a numbered heading may run straight into its (more indented) body
            body_follows = nxt == i + 1 and _indent(lines[nxt]) > _indent(lines[i])
            # numbers only move forward: a field list ("7 CHECKSUM - ...") after
            # section 8, or a stray "12 bytes" line after section 2, is not a heading
            jump = (
                order is not None and order[0] == 0 and len(last) > 1 and last[0] == 0
                and order[1] > last[1] + 5
            )
            if (
                not (standalone or (order and body_follows))
                or (order is not None and (order <= last or jump))
                # an indented list goes on: RFC 4188 section 3 lists criteria "1." to "6."
                # after "3.  Overview", and "4." to "6." are not sections 4 to 6
                or (item is not None and items.get(item[0]) == item[1] - 1)
            ):
                found = None
        if found is None:
            if item is not None:
                items[item[0]] = item[1]
            continue
        items.clear()
        if order is not None:
            last = order
        sections.append(Section(anchor, lines[i].strip(), level, i + 1))
    return _finish(lines, sections)


def _list_number(line: str) -> tuple[int, int] | None:
    """(indent, n) of an indented "n." line, the shape of a numbered list item."""
    m = RFC_NUMBERED.match(line)
    if m and m.group(1) and "." not in m.group(2):
        return len(m.group(1)), int(m.group(2))
    return None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _gap(lines: list[str], a: int, b: int) -> bool:
    """A blank line separates line a from line b > a (page furniture does not count)."""
    return any(not lines[k].strip() for k in range(a + 1, b))


def rfc_title(lines: list[str]) -> str | None:
    """The centered title under an RFC's header block ("The OSPF Not-So-Stubby Area (NSSA)
    Option"); None when the layout differs."""
    i, n = 0, len(lines)
    for want_text in (False, True, False):      # blanks, header block, blanks
        while i < n and bool(lines[i].strip()) == want_text:
            i += 1
    title = []
    while i < n and lines[i].strip() and _indent(lines[i]) > 0:
        title.append(lines[i].strip())
        i += 1
    return " ".join(title) or None


def parse_markdown(lines: list[str]) -> list[Section]:
    sections: list[Section] = []
    fence = False
    for i, line in enumerate(lines):
        if line.startswith(FENCE):
            fence = not fence
            continue
        m = None if fence else MD_HEADING.match(line)
        if m:
            heading = m.group(2).strip()
            sections.append(Section(slugify(heading), heading, len(m.group(1)), i + 1))
    return _finish(lines, sections)


def parse_sections(text: str, doc_type: str) -> tuple[list[str], list[Section]]:
    lines = split_lines(text)
    parser = parse_rfc if doc_type == "rfc" else parse_markdown
    return lines, parser(lines)


def _finish(lines: list[str], sections: list[Section]) -> list[Section]:
    """Add the preamble, de-duplicate anchors, set parents and line ranges."""
    first = sections[0].line_start if sections else len(lines) + 1
    if any(line.strip() for line in lines[: first - 1]):
        sections.insert(0, Section(PREAMBLE, None, 0, 1))

    # a repeat takes the next free "-N": never another heading's own anchor ("Foo 2" is foo-2
    # wherever it stands), so the ids stay unique
    taken = {sec.anchor for sec in sections}
    first: set[str] = set()
    for sec in sections:
        if sec.anchor not in first:
            first.add(sec.anchor)
            continue
        n = 2
        while f"{sec.anchor}-{n}" in taken:
            n += 1
        sec.anchor = f"{sec.anchor}-{n}"
        taken.add(sec.anchor)

    stack: list[int] = []
    for index, sec in enumerate(sections):
        # the preamble (level 0) ends at the first heading and is nobody's parent
        while stack and sections[stack[-1]].level in (0, *range(sec.level, 7)):
            sections[stack.pop()].line_end = sec.line_start - 1
        sec.parent = stack[-1] if stack and sec.level > 0 else None
        stack.append(index)
    for index in stack:
        sections[index].line_end = len(lines)
    return sections


def section_path(sections: list[Section], index: int) -> list[str]:
    """Headings from the top-level ancestor down to sections[index]."""
    path: list[str] = []
    current: int | None = index
    while current is not None:
        sec = sections[current]
        if sec.heading:
            path.append(sec.heading)
        current = sec.parent
    return path[::-1]
