#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import html
import re
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import convert_docs as cd  # noqa: E402

CELL_SEP = "; "                         # paragraphs inside one table cell
NEST_SEP = " / "                        # cells of a nested table row folded into its outer cell (as converter v2)
# Cisco task-topic labels (convert_docs.main() writes them as 'Label:' lines); inside a table row they are cell text
LABEL = re.compile(r"(Procedure|Examples?:?|Before you begin|What to do next|SUMMARY STEPS|DETAILED STEPS)\s*", re.I)
FN_NUM = re.compile(r"\[[^\[\]\s]{1,4}\]")            # '[1]': a footnote number on its own
DASHES = re.compile(r"([‐-―])")
# alt texts that say nothing: placeholders, icon names, Word's generated descriptions, generic words
ALT_JUNK = re.compile(r"alt-tag-for-image|related image, diagram or screenshot|.*description automatically generated.*"
                      r"|(?:image|icon|screenshot|figure|graphic|picture|logo|spacer|blank|photo)s?|page\d+image\d+"
                      r"|(?:note|tip|caution|warning|important|danger|attention|fastpath|info|restriction)"
                      r"(?:[-_ ]?(?:icon|image|gif|png|svg))?"
                      r"|(?:fig(?:ure)?\.?|image|img|pic(?:ture)?|screen ?shot|graphic)[\s_-]*\d+[a-z]?"
                      r"|screen ?shot \d{4}-\d\d-\d\d.*", re.I)
FILE_NAME = re.compile(r"\S*\.(?:png|jpe?g|gif|svg|bmp|tiff?|webp|e?ps|emf|wmf)", re.I)
# a one-word alt is a file or figure name ('132715.ps', 'eirgp_8', '26634-bgp-toc11', 'Topology'), except an LDAP-style
# expression or attribute name that a screenshot shows (cisco-tn-ise-active-directory: 'sAMAccountName=anos')
ONE_WORD_KEPT = re.compile(r"\S*=\S*|[a-z]+[A-Z][A-Za-z0-9]*")
FIG_LABEL = re.compile(r"^(?:Figure|Fig\.?)\s+[\w.-]+[.:]?\s*", re.I)
IMAGE_LINE = re.compile(r"\[image: (.*)\]")
IMG_ALT = re.compile(r"""<img\b[^>]*?\salt\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""", re.I)
ALT_REPEAT = 3                          # images of one file sharing an alt text that make it a placeholder (rule 5)
# words that do not make an alt text say more than the line next to it
STOP = frozenset("a an the of in on to for and or with by at from is are be this that these those it its as into via "
                 "figure fig image".split())


def clean_pre(s: str) -> str:
    """convert_docs.clean() for <pre> text, without its dash mapping: code keeps U+2010-U+2015 as written."""
    parts = DASHES.split(s)
    return "".join(p if i % 2 else cd.clean(p) for i, p in enumerate(parts))


def _words(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def _content(s: str) -> set[str]:
    return {w for w in _words(s).split() if w not in STOP}


def repeats(alt: str, line: str) -> bool:
    """True when the line next to an image already says what its alt text says: the same words, or a caption, label
    or short paragraph (at most 2n+2 content words) that holds 3/4 of the alt's n content words. 'Small network
    deployment of Cisco ISE nodes' / 'A small network deployment of two Cisco ISE nodes.' (ise31-install Figure 1),
    'Dedicated PSN' / 'Dedicated PSN' (ise-perf-scale persona icons)."""
    line = FIG_LABEL.sub("", re.sub(r"^#{1,6} ", "", line))
    a, o = _content(alt), _content(line)
    if not a or _words(alt) == _words(line):
        return True
    return len(o) <= 2 * len(a) + 2 and len(a & o) >= 0.75 * len(a)


def meaningful_alt(alt: str | None, caption: str = "") -> str | None:
    """The alt text worth keeping as '[image: ...]', or None (see rule 5)."""
    a = re.sub(r"\s+", " ", cd.clean(alt or "")).strip()
    if len(a) < 2 or not re.search(r"[A-Za-z]", a) or not _content(a) or ALT_JUNK.fullmatch(a) \
            or FILE_NAME.fullmatch(a) or (" " not in a and not ONE_WORD_KEPT.fullmatch(a)):
        return None
    if caption and repeats(a, caption):
        return None                                   # the figure caption already says it
    return a


def _span(v: str | None) -> int:
    try:
        return max(1, int(str(v).strip()))
    except (TypeError, ValueError):
        return 1


def _alpha(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s or "0"


def _roman(n: int) -> str:
    if n <= 0:
        return str(n)
    out = ""
    for v, r in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
                 (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out, n = out + r, n - v
    return out


def ordinal(n: int, kind: str | None) -> str:
    """The marker an <ol type=kind> prints for item n."""
    if kind in ("a", "A"):
        s = _alpha(n)
    elif kind in ("i", "I"):
        s = _roman(n)
    else:
        return str(n)
    return s.upper() if kind in ("A", "I") else s


class _Table:
    """An open <table>: its rowspan carries and its grid width."""
    __slots__ = ("carry", "cols", "width")

    def __init__(self):
        self.carry: dict[int, list] = {}     # grid column -> [rows still covered, text]
        self.cols = 0                        # columns declared by <col span>
        self.width = 0                       # widest row so far, in grid columns


class _Row:
    """An open <tr>."""
    __slots__ = ("table", "fields", "paras", "col", "span", "split", "fences", "pending", "carried")

    def __init__(self, table: _Table):
        self.table = table
        self.fields: list[list] = []         # [text, colspan, kind]; kind: own | carry | loose (text outside a cell)
        self.paras: list[str] | None = None  # paragraphs of the open <td>/<th>
        self.col = 0                         # grid column of the next cell
        self.span = (1, 1)                   # (rowspan, colspan) of the open cell
        self.split = False                   # the open cell was cut by a <pre>
        self.fences: list[list[str]] = []    # <pre> blocks written after the row line (nested rows, cell-final code)
        self.pending: list[list[str]] = []   # <pre> blocks of the open cell that no text has followed yet
        self.carried: set[int] = set()       # carry columns already used by this row


class X2T21(cd.X2T):
    """convert_docs.X2T with rules 1-5 of the module docstring."""

    def __init__(self, base_level: int | None = None):
        super().__init__(base_level=base_level)
        self.row: _Row | None = None
        self.rows: list[_Row] = []           # enclosing rows while inside a nested table
        self.tables: list[_Table] = []
        self.pres: list[str] = []            # per open <pre>: 'out' (fence written in place) | 'row' (held: cut the
                                             # row if text follows it in its cell) | 'nested' (after the outer row)
        self.head_in_row = False
        self.lists: list[dict] = []          # per open <ol>/<ul>: {tag, n, type, li}
        self.prefix = ""                     # '1. ', '- ' or '[n] ' for the next paragraph
        self.prefix_owner: object = None     # the <li> (or 'fn') that set it
        self.anchors: list[str | None] = []  # per open <a>: 'ref' | 'body' (DITA footnote anchors) | None
        self.fn_seen: str | None = None      # a footnote number is in buf ('ref' | 'body')
        self.images: list[str] = []          # '[image: ...]' lines waiting for the end of their paragraph
        self.caption = ""                    # the open <figure>'s caption
        self.in_caption = 0
        self.closing = False                 # in handle_endtag: a footnote number flushed now has an empty body
        self.alt_uses: Counter = Counter()   # meaningful alt text -> images of the fed file that carry it

    def feed(self, data):
        """HTMLParser.feed, after counting the file's meaningful alt texts (rule 5 drops one ALT_REPEAT images share)."""
        for m in IMG_ALT.finditer(data):
            alt = meaningful_alt(html.unescape(next(g for g in m.groups() if g is not None)))
            if alt:
                self.alt_uses[alt] += 1
        super().feed(data)

    # ---- text routing -----------------------------------------------------------------------------------------
    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        fn, self.fn_seen = self.fn_seen, None
        if t:
            self._text(t, fn)
        if self.images and not self.head:
            images, self.images = self.images, []
            for line in images:
                self._para(line)

    def _text(self, t: str, fn: str | None):
        if self.head and not self.head_in_row:
            if self.prefix_owner == "fn":                 # footnote numbers whose body never came: their own line,
                self.out.append(self.prefix.strip())      # never the number of text under this heading
                self.prefix, self.prefix_owner = "", None
            if not re.fullmatch(r"<[^>]*>", t):           # "<#root>"-style pseudo headings from HTML exports
                self.out.append("#" * self.head + " " + t)
            return
        if self.head:                                     # a heading inside a table row is cell text
            if re.fullmatch(r"<[^>]*>", t):
                return
            m = LABEL.fullmatch(t)
            if m:
                t = m.group(1).rstrip(":") + ":"
        if self.in_caption:
            self.caption = t
        if fn == "body" and FN_NUM.fullmatch(t) and not self.closing:   # the number of a footnote body: it starts
            self.prefix += t + " "                                        # the next paragraph (closing: empty body)
            if self.prefix_owner is None:
                self.prefix_owner = "fn"
            return
        if fn == "ref" and FN_NUM.fullmatch(t) and self._attach(t):
            return
        self._para(t)

    def _para(self, t: str):
        if self.prefix:
            t, self.prefix, self.prefix_owner = self.prefix + t, "", None
        r = self.row
        if r is not None and r.paras is not None and r.pending:
            self._cut_row()                               # text follows a <pre> in its own cell
        seq = self.out if r is None else r.paras
        if seq is None:
            r.fields.append([t, 1, "loose"])
            return
        prev = seq[-1] if seq else ""
        img = IMAGE_LINE.fullmatch(t)
        if img:
            if prev and repeats(img.group(1), prev):
                return                                    # the line before already says it (rule 5)
        elif prev and (m := IMAGE_LINE.fullmatch(prev)) and repeats(m.group(1), t):
            seq.pop()                                     # this line says what the image before it said
        seq.append(t)

    def _attach(self, t: str) -> bool:
        """A footnote reference left alone after its paragraph closed ('<p>Command </p><a ...>1</a>')."""
        r = self.row
        if r is not None:
            if r.paras:
                r.paras[-1] += " " + t
                return True
            return False
        last = self.out[-1] if self.out else ""
        if last and not last.startswith(("#", "```", "<!-- page ")) and " | " not in last and not self.pres:
            self.out[-1] = last + " " + t
            return True
        return False

    # ---- parser hooks -------------------------------------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in cd.SKIP or self.skip:
            return super().handle_starttag(tag, attrs)
        a = dict(attrs)
        if tag == "pre" or (tag == "code" and (a.get("class") or "").find("block") >= 0):
            self.flush()
            self.pre += 1
            if self.row is not None:                      # nested table: after the outer row, as in v2; else held
                self.pres.append("nested" if self.rows else "row")   # until text follows it in its cell
                return
            self.pres.append("out")
            self.out.append("```")
            return
        if re.fullmatch(r"h[1-6]", tag):
            super().handle_starttag(tag, attrs)           # flush; level relative to the file's first heading
            self.head_in_row = self.row is not None
            return
        if tag == "table":
            self.tables.append(_Table())
            return
        if tag == "col":
            if self.tables:
                self.tables[-1].cols += _span(a.get("span"))
            return
        if tag == "tr":
            self.flush()
            if self.row is not None:
                self.rows.append(self.row)
            self.row = _Row(self.tables[-1] if self.tables else _Table())
            return
        if tag in ("td", "th") and self.row is not None and not self.pre:
            self.flush()
            if self.row.paras is not None:
                self._close_cell()
            self._carry()
            self.row.paras, self.row.split = [], False
            self.row.span = (_span(a.get("rowspan")), _span(a.get("colspan")))
            return
        if tag == "a":
            href = a.get("href") or ""
            self.anchors.append("ref" if href.startswith("#fntarg_") else "body" if href.startswith("#fnsrc_") else None)
            return
        if tag == "img":
            self._image(a.get("alt"))
            return
        if tag in ("ol", "ul"):
            start = a.get("start")
            self.lists.append({"tag": tag, "n": int(start) if start and start.strip().isdigit() else 1,
                               "type": a.get("type"), "li": None})
            return
        if tag == "li":
            super().handle_starttag(tag, attrs)           # flush the text before the item
            self._li_start(a)
            return
        if tag == "figure":
            self.caption = ""
        if tag == "figcaption":
            super().handle_starttag(tag, attrs)
            self.in_caption += 1
            return
        return super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.closing = True                               # what an end tag flushes is complete (rule 2)
        try:
            return self._endtag(tag)
        finally:
            self.closing = False

    def _endtag(self, tag):
        if tag in cd.SKIP or self.skip:
            return super().handle_endtag(tag)
        if tag == "pre" and self.pre:
            self._end_pre()
            return
        if re.fullmatch(r"h[1-6]", tag) and self.head:
            super().handle_endtag(tag)
            self.head_in_row = False
            return
        if tag == "tr" and self.row is not None:
            self._end_row()
            return
        if tag in ("td", "th") and self.row is not None and not self.pre:
            self.flush()
            if self.row.paras is not None:
                self._close_cell()
            return
        if tag == "table":
            if self.tables:
                self.tables.pop()
            return
        if tag == "a":
            if self.anchors:
                self.anchors.pop()
            return
        if tag in ("ol", "ul"):
            if self.lists and self.lists[-1]["tag"] == tag:
                self.lists.pop()
            return
        if tag == "li":
            super().handle_endtag(tag)
            if self.lists and self.lists[-1]["li"] is not None:
                if self.prefix_owner is self.lists[-1]["li"]:   # an item without text: its number goes unused
                    self.prefix, self.prefix_owner = "", None
                self.lists[-1]["li"] = None
            return
        if tag == "figcaption":
            super().handle_endtag(tag)
            self.in_caption = max(0, self.in_caption - 1)
            return
        if tag == "figure":
            super().handle_endtag(tag)
            self.caption = ""
            return
        return super().handle_endtag(tag)

    def handle_data(self, data):
        if self.skip:
            return
        fn = self.anchors[-1] if self.anchors else None
        n = data.strip()
        if fn and n and not self.pre and len(n) <= 4:      # a footnote number
            n = cd.clean(n)
            self.buf.append(f" [{n}]" if fn == "ref" else f"[{n}] ")
            self.fn_seen = fn
            return
        super().handle_data(data)

    # ---- rules ----------------------------------------------------------------------------------------------------
    def _image(self, alt: str | None):
        if self.pre or self.head:
            return
        text = meaningful_alt(alt, self.caption)
        if not text or self.alt_uses[text] >= ALT_REPEAT:
            return                                        # (an alt many images share is a placeholder)
        line = f"[image: {text}]"
        if "".join(self.buf).strip():
            self.images.append(line)                      # inside running text: after its paragraph
        else:
            self.flush()
            self._para(line)

    def _li_start(self, a: dict):
        if not self.lists:
            return
        lst = self.lists[-1]
        if lst["tag"] == "ol":
            v = a.get("value")
            if v and v.strip().isdigit():
                lst["n"] = int(v)
            marker = ordinal(lst["n"], lst["type"]) + ". "
            lst["n"] += 1
        elif any(x["tag"] == "ol" for x in self.lists[:-1]):
            marker = "- "
        else:
            return
        token = object()
        if self.prefix and (self.prefix_owner == "fn" or any(self.prefix_owner is x["li"] for x in self.lists[:-1])):
            marker = self.prefix + marker                 # the item of an enclosing list has no text of its own yet
        self.prefix, self.prefix_owner = marker, token
        lst["li"] = token

    def _end_pre(self):
        txt = "".join(self.buf).rstrip("\n")
        self.buf = []
        if txt.startswith("\r\n"):
            txt = txt[2:]
        elif txt.startswith("\n"):
            txt = txt[1:]
        self.pre -= 1
        lines = clean_pre(txt).split("\n")
        kind = self.pres.pop() if self.pres else "out"
        if kind != "out" and self.row is not None:
            (self.row.fences if kind == "nested" else self.row.pending).append(lines)
            return
        self.out.extend(lines)
        self.out.append("```")

    def _carry(self):
        """Insert the rowspan cells that cover the next grid columns of the row."""
        r = self.row
        carry = r.table.carry
        while r.col in carry:
            c = carry[r.col]
            r.fields.append([c[1], 1, "carry"])
            r.carried.add(r.col)
            c[0] -= 1
            if c[0] <= 0:
                del carry[r.col]
            r.col += 1

    def _close_cell(self):
        r = self.row
        paras, r.paras = r.paras, None
        rs, cs = r.span
        code = bool(r.pending)
        r.fences.extend(r.pending)                        # code that ends its cell: after the row line, as in v2
        r.pending = []
        if r.split or (code and not paras):              # no '-' for the rest of a cut cell or a code-only cell
            if paras:
                r.fields.append([CELL_SEP.join(paras), 1, "own"])
        else:
            text = CELL_SEP.join(paras) if paras else "-"
            r.fields.append([text, cs, "own"])
            if rs > 1:
                for j in range(cs):
                    r.table.carry[r.col + j] = [rs - 1, text]
                    r.carried.add(r.col + j)
        r.col += cs
        r.split = False

    def _cut_row(self):
        """Text follows a <pre> in its own cell: write the row so far and the code held so far, so the code stays
        next to its lead-in; the rest of the row goes on the next line."""
        r = self.row
        fields = r.fields + ([[CELL_SEP.join(r.paras), 1, "own"]] if r.paras else [])
        line = self._render(fields, r.table, cut=True)
        if line:
            self.out.append(line)
        r.fields = [] if line else [f for f in r.fields if f[2] == "carry"]   # a rowspan label still heads the rest
        for f in r.fences + r.pending:
            self.out.extend(["```", *f, "```"])
        r.fences, r.pending = [], []
        r.paras, r.split = [], True

    def _end_row(self):
        self.flush()
        r = self.row
        if r.paras is not None:
            self._close_cell()
        r.fences.extend(r.pending)                        # code held outside any cell
        r.pending = []
        self._carry()
        carry = r.table.carry
        for c in sorted(carry):                           # carries the row's own cells did not reach
            if c in r.carried:
                continue
            if c >= r.col:
                while r.col < c:
                    r.fields.append(["-", 1, "own"])
                    r.col += 1
                self._carry()
            else:                                         # overlapped by a colspan cell: the row still uses it up
                carry[c][0] -= 1
                if carry[c][0] <= 0:
                    del carry[c]
        self.row = self.rows.pop() if self.rows else None
        if self.row is not None:                          # nested table: fold the row into the enclosing cell
            cells = [f[0] for f in r.fields if f[0] != "-"]
            if cells:
                self._para_in(NEST_SEP.join(cells))
            self.row.fences.extend(r.fences)
            return
        line = self._render(r.fields, r.table)
        r.table.width = max(r.table.width, r.col)
        if line:
            self.out.append(line)
        for f in r.fences:
            self.out.extend(["```", *f, "```"])

    def _para_in(self, t: str):
        """A folded nested-table row as a paragraph of the enclosing cell."""
        r = self.row
        if r.paras is not None:
            if r.pending:
                self._cut_row()                           # it follows a <pre> of the enclosing cell
            r.paras.append(t)
        else:
            r.fields.append([t, 1, "loose"])

    @staticmethod
    def _render(fields: list[list], table: _Table, cut: bool = False) -> str:
        if not any(f[0] != "-" for f in fields if f[2] != "carry"):
            return ""                                     # no text of its own (converter v2 wrote nothing)
        if (not cut and len(fields) == 1 and fields[0][2] == "own" and fields[0][1] > 1
                and fields[0][1] >= (table.width or table.cols)):
            return fields[0][0]                           # one cell across the whole table: a group line
        out: list[str] = []
        for text, span, _ in fields:
            out.extend([text] * span)
        return " | ".join(out)


# ---- PDF -----------------------------------------------------------------------------------------------------------
MONO21 = re.compile(r"courier|mono|consol|menlo|typewriter", re.I)
GLYPH = re.compile(r"[•●◦▪]")                            # a bullet glyph alone on its line
GLYPH_GAP = 18.0     # points between a glyph and its item's text: 1,834 of 1,873 corpus glyphs; the rest are tables
GLUE = re.compile(r"[A-Za-z0-9]-$")                       # a line break after this joins without a space
ATX = re.compile(r"#{1,6}\s+\S")                          # what rag_core.ingest.HEADING reads as a heading


def not_a_heading(para: str) -> str:
    """A paragraph that would read as an ATX heading gets one leading space, the convention of postclean.py rule 4
    for '#' table rows (rag_core's HEADING regex is anchored at column 0). Used where the glyph rule starts a new
    paragraph: an item such as '• # of sources' then begins with its own '#'."""
    return " " + para if ATX.match(para) else para


def _text(ln: dict) -> str:
    return "".join(sp["text"] for sp in ln["spans"]).strip()


def place_glyphs(lines: list[dict]) -> list[dict]:
    """A block's lines in PyMuPDF's order, except that a line holding only a bullet glyph is moved to just before
    the text line it introduces: the nearest line to its right, at most GLYPH_GAP points away (a bullet indent), whose
    vertical extent overlaps the glyph's by at least half the smaller height. PyMuPDF puts the glyph after its item's
    text (sxp-catalyst and aaa-dead-detection: glyph x=54, item x=66, same baseline), and a glyph in a smaller font
    has a lower top than its text (sdwan-design-cvd p3: '●' Arial 7pt y0 444.2, text 10pt y0 441.0), so neither the
    stream order nor a y0 sort puts it first. A glyph with no such line keeps its place: the gap limit keeps a
    table's left-column glyph away from the right column's text (iosxe17-rtr-secvpn-cg p196: 220pt)."""
    glyphs = {i for i, ln in enumerate(lines) if GLYPH.fullmatch(_text(ln))}
    if not glyphs:
        return lines
    before: dict[int, list[int]] = {}
    for g in sorted(glyphs):
        gx, gy0, gy1 = lines[g]["bbox"][0], lines[g]["bbox"][1], lines[g]["bbox"][3]
        best = None
        for i, ln in enumerate(lines):
            x0, y0, y1 = ln["bbox"][0], ln["bbox"][1], ln["bbox"][3]
            if i in glyphs or x0 <= gx or x0 - lines[g]["bbox"][2] > GLYPH_GAP:
                continue
            if min(gy1, y1) - max(gy0, y0) >= 0.5 * max(0.1, min(gy1 - gy0, y1 - y0)) \
                    and (best is None or x0 < lines[best]["bbox"][0]):
                best = i
        if best is not None:
            before.setdefault(best, []).append(g)
    moved = {g for gs in before.values() for g in gs}
    out = []
    for i, ln in enumerate(lines):
        if i in moved:
            continue
        out.extend(lines[g] for g in before.get(i, ()))
        out.append(ln)
    return out


def wrap_join(prev: str, nxt: str) -> str:
    """Join a wrapped line: no space only after a letter or digit plus '-' ('cisco-av-' + 'pair' -> 'cisco-av-pair',
    ':256 -' + 'MasterDbListener' -> ':256 - MasterDbListener')."""
    return prev + nxt if GLUE.search(prev) else prev + " " + nxt


def paragraphs(lines: list[str]) -> list[str]:
    """convert_docs' paragraph join: a bullet or 'Step n'/'n.' line starts a paragraph, the other lines of a block are
    joined (wrap_join). A line that is only a bullet glyph ends the paragraph before it and starts the next item,
    '• text' (v2 already wrote a glyph that comes before its text that way); a glyph that no text follows is dropped."""
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if GLYPH.fullmatch(s):
            out.append(s)
        elif out and not cd.BULLET.match(s):
            out[-1] = wrap_join(out[-1], s)
        else:
            out.append(s)
    return [para for para in out if not GLYPH.fullmatch(para)]


def head_style(b: dict, toc: list, body: float, furniture: set, small_repeats: set):
    """(size, bold) style of a heading-like block, or None (convert_docs.pdf_to_lines' head_style, with the edge
    rule: an edge block is rejected only when it is furniture or a small repeat)."""
    text = re.sub(r"\s+", " ", " ".join(b["lines"])).strip()
    words = len(text.split())
    edge = b["edge"] and (b["key"] in furniture or b["key"] in small_repeats)
    if toc or b["mono"] or b["white"] or edge or len(text) > 150 or words > 20 or not re.search(r"[A-Za-z]", text):
        return None
    if text.endswith(":") or (text.endswith(".") and words > 6) \
            or re.fullmatch(r"(Note|Tip|Tech tip|Caution|Warning|Important)\W*", text, re.I) \
            or re.search(r"\b(Figure|Table) \d+\.?$", text):
        return None
    if b["size"] >= body + 1.5 or (b["bold"] and b["size"] >= body - 1.0):
        return (b["size"], b["bold"])
    return None


def pdf_to_lines21(p: Path) -> list[str]:
    """convert_docs.pdf_to_lines with rule 6 (module docstring); the rest of the logic is unchanged."""
    import pymupdf as fitz  # PyMuPDF
    from collections import Counter
    doc = fitz.open(p)
    toc = doc.get_toc(simple=True)   # [level, title, page]
    by_page: dict[int, list[tuple[int, str]]] = {}
    for lvl, title, page in toc:
        by_page.setdefault(page, []).append((lvl, cd.clean(title).strip()))
    pages: list[list[dict]] = []
    size_chars: Counter = Counter()
    furn: Counter = Counter()
    for page in doc:
        h = page.rect.height or 1
        blocks = []
        seen_keys = set()
        for b in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
            if b.get("type") != 0:
                continue
            lines, total, mono, bold, sizes, colors = [], 0, 0, 0, Counter(), Counter()
            glyph_block = any(GLYPH.fullmatch(_text(ln)) for ln in b["lines"])
            for ln in place_glyphs(b["lines"]):                              # v2.1: glyph placement
                t = cd.clean(cd.join_spans(ln["spans"])).rstrip()
                for s in ln["spans"]:
                    n = len(s["text"].strip())
                    total += n
                    is_mono = bool(MONO21.search(s["font"]))                # v2.1: 'typewriter'
                    mono += n if is_mono else 0
                    bold += n if (s["flags"] & 16 or "bold" in s["font"].lower()) else 0
                    sizes[round(s["size"], 1)] += n
                    colors[s["color"]] += n
                    if not is_mono and not (s["flags"] & 16 or "bold" in s["font"].lower()):
                        size_chars[round(s["size"], 1)] += n      # body size = most common plain proportional text
                if t.strip():
                    lines.append(t)
            if not lines or not total:
                continue
            size = sizes.most_common(1)[0][0]
            edge = b["bbox"][3] < 0.09 * h or b["bbox"][1] > 0.91 * h
            key = cd._key(" ".join(lines))
            blk = {"lines": lines, "mono": mono / total >= 0.6, "bold": bold / total >= 0.9, "size": size,
                   "white": colors.most_common(1)[0][0] == 0xFFFFFF, "edge": edge, "key": key, "glyphs": glyph_block}
            blocks.append(blk)
            if edge and key not in seen_keys:
                furn[key] += 1
                seen_keys.add(key)
        pages.append(blocks)
    body = size_chars.most_common(1)[0][0] if size_chars else 10.0
    # document-wide running headers/footers and page numbers
    furniture = {k for k, c in furn.items() if c >= max(3, 0.15 * len(doc)) or re.fullmatch(r"[#ivxlc ]{1,8}", k)}
    # chapter/section running headers ("EIGRP EIGRP Stub Routing"): body-sized edge text that repeats, or that ends
    # with an outline title after a prefix. A real heading equals its title exactly and is set larger, so it stays.
    small_repeats = {k for k, c in furn.items() if c >= 3 and len(k) <= 120}
    toc_by_tail: dict[str, list[str]] = {}
    for _, t, _ in toc:
        n = cd._norm(t)
        if len(n) >= 8:
            toc_by_tail.setdefault(n[-8:], []).append(n)

    def is_furniture(b: dict) -> bool:
        if not b["edge"]:
            return False
        if b["key"] in furniture:
            return True
        if b["size"] > body + 0.5 or b["mono"]:
            return False
        if b["key"] in small_repeats:
            return True
        n = cd._norm(" ".join(b["lines"]))
        return len(n) <= 160 and any(n != t and n.endswith(t) for t in toc_by_tail.get(n[-8:], ()))

    def style(b: dict):
        return head_style(b, toc, body, furniture, small_repeats)                # v2.1: edge rule

    style_use = Counter(s for bl in pages for b in bl if (s := style(b)))
    head_styles = sorted((s for s, n in style_use.items() if n >= 3), reverse=True)[:6]   # one-off cover-page styles are not headings

    def last_prose(ls: list[str]) -> int | None:
        """Index of the last emitted prose line, looking back over page markers only."""
        for j in range(len(ls) - 1, -1, -1):
            line = ls[j]
            if line.startswith("<!-- page "):
                continue
            if line.startswith(("#", "```")) or " | " in line:
                return None
            return j
        return None

    lines: list[str] = []
    for i, blocks in enumerate(pages, start=1):
        pending = list(by_page.get(i, []))
        lines.extend(cd.page_marker(i))
        first_text = True
        skip_next = False
        body_blocks = [b for b in blocks if not is_furniture(b)]
        for bi, b in enumerate(body_blocks):
            if skip_next:
                skip_next = False
                continue
            joined = re.sub(r"\s+", " ", " ".join(b["lines"])).strip()
            nj = cd._norm(joined)
            hit = None
            if pending and nj:
                hit = next((k for k, (_, t) in enumerate(pending) if cd._norm(t) == nj), None)
                if hit is None and bi + 1 < len(body_blocks) and len(nj) >= 6:
                    # a title wrapped into two blocks
                    nxt = cd._norm(" ".join(body_blocks[bi + 1]["lines"]))
                    hit = next((k for k, (_, t) in enumerate(pending) if cd._norm(t) == nj + nxt), None)
                    skip_next = hit is not None
            if hit is not None:
                for lvl, title in pending[: hit + 1]:       # emit outline entries up to and including this one
                    lines.append("#" * min(lvl, 6) + " " + title)
                del pending[: hit + 1]
                first_text = False
                continue
            st = style(b)
            if st in head_styles:
                lines.append("#" * (head_styles.index(st) + 1) + " " + joined)
                first_text = False
                continue
            if b["mono"]:
                lines.append("```")
                lines.extend(line.rstrip() for line in b["lines"])
                lines.append("```")
                first_text = False
                continue
            paras = paragraphs(b["lines"])                                       # v2.1: glyph and glue rules
            if first_text and paras and paras[0][:1].islower():
                j = last_prose(lines)                         # a sentence continued across the page break
                if j is not None and not re.search(r"[.!?:;)\]\"']\s*$", lines[j]):
                    lines[j] = wrap_join(lines[j], paras[0])                     # v2.1: glue rule
                    paras = paras[1:]
            first_text = False
            lines.extend(not_a_heading(para) if b["glyphs"] else para for para in paras)     # v2.1
        for lvl, title in pending:                        # outline entries not matched on the page
            lines.append("#" * min(lvl, 6) + " " + title)
    return lines


# ---- entry points --------------------------------------------------------------------------------------------------
@contextmanager
def v21():
    """Run convert_docs' own epub/html/main code with X2T21 and pdf_to_lines21 in place of X2T and pdf_to_lines."""
    saved = cd.X2T, cd.pdf_to_lines
    cd.X2T, cd.pdf_to_lines = X2T21, pdf_to_lines21
    try:
        yield
    finally:
        cd.X2T, cd.pdf_to_lines = saved


def epub_to_lines(p: Path) -> list[str]:
    with v21():
        return cd.epub_to_lines(p)


def html_to_lines(p: Path) -> list[str]:
    with v21():
        return cd.html_to_lines(p)


def main() -> int:
    with v21():
        return cd.main()


if __name__ == "__main__":
    sys.exit(main())
