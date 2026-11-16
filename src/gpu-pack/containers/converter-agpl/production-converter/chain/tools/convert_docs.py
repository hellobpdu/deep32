#!/usr/bin/env python3
"""Convert Cisco documentation (ePub preferred, PDF fallback, HTML) into normalized text for authoring/validation.

Output format (one file per document, <out>/<doc_key>.txt):
    #..###### <heading title>           <- Markdown ATX heading (level 1..6), parsed by rag_core.ingest.parse_markdown_sections
    <paragraph text, one paragraph per line>
    ```                                  <- fenced verbatim blocks (CLI, show output, config) keep whitespace
    ...
    ```
Usage: convert_docs.py <input-file> <doc_key> <out-dir> [--page-markers]
--page-markers: also emit "<!-- page N -->" lines (PDF page, ePub spine item) for the ingest adapter's page provenance.
ePub: spine order, XHTML headings h1-h6, <pre>/<code> blocks kept verbatim, tables flattened row-wise ("a | b | c").
PDF: PyMuPDF text with the PDF outline (TOC) used to insert heading markers at the right pages/lines.
"""
from __future__ import annotations

import html
import re
import sys
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

BLOCK = {"p", "div", "li", "dd", "dt", "tr", "br", "section", "article", "figcaption", "caption", "blockquote", "td", "th"}
SKIP = {"script", "style", "head", "nav", "noscript"}
PAGE_MARKERS = False


def page_marker(n: int) -> list[str]:
    return [f"<!-- page {n} -->"] if PAGE_MARKERS else []


def clean(s: str) -> str:
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", unicodedata.normalize("NFKC", s)).replace("\u00ad", "").replace("\u200b", "")
    s = re.sub(r"[‐-―]", "-", s).replace(" ", " ")
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    return s


class X2T(HTMLParser):
    """XHTML -> lines. base_level: the file's depth in the ePub TOC (Cisco ePubs put every topic in its own file
    with a single <h1>, so tag levels are flat and the hierarchy lives only in toc.ncx); the file's first heading
    gets base_level and later headings keep their tag offset from it."""

    def __init__(self, base_level: int | None = None):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.buf: list[str] = []
        self.skip = 0
        self.pre = 0
        self.head: int | None = None
        self.cells: list[str] | None = None
        self.row_stack: list[tuple[list[str], list[list[str]]]] = []   # nested tables
        self.row_fences: list[list[str]] = []   # <pre> blocks inside the current row, emitted after the row line
        self.pre_in_row = False
        self.base_level = base_level
        self.first_tag: int | None = None

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if not t:
            return
        if self.head:
            if re.fullmatch(r"<[^>]*>", t):             # "<#root>"-style pseudo headings from HTML exports
                return
            self.out.append("#" * self.head + " " + t)
        elif self.cells is not None:
            self.cells.append(t)
        else:
            self.out.append(t)

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("pre",) or (tag == "code" and dict(attrs).get("class", "").find("block") >= 0):
            self.flush()
            self.pre += 1
            if self.cells is not None:
                self.pre_in_row = True
            else:
                self.out.append("```")
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.flush()
            t = int(tag[1])
            if self.base_level is not None:
                if self.first_tag is None:
                    self.first_tag = t
                self.head = min(6, max(1, self.base_level + t - self.first_tag))
            else:
                self.head = t
            return
        if tag == "tr":
            self.flush()
            if self.cells is not None:
                self.row_stack.append((self.cells, self.row_fences))
            self.cells, self.row_fences = [], []
            return
        if tag in BLOCK and not self.pre:
            self.flush()
        if tag == "br" and self.pre:
            self.buf.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "pre" and self.pre:
            txt = "".join(self.buf).rstrip("\n")
            self.buf = []
            self.pre -= 1
            if self.pre_in_row and self.cells is not None:
                self.row_fences.append(clean(txt).split("\n"))
                self.pre_in_row = False
                return
            self.out.extend(clean(txt).split("\n"))
            self.out.append("```")
            return
        if re.fullmatch(r"h[1-6]", tag) and self.head:
            self.flush()
            self.head = None
            return
        if tag == "tr" and self.cells is not None:
            self.flush()
            row, fences = self.cells, self.row_fences
            self.cells, self.row_fences = self.row_stack.pop() if self.row_stack else (None, [])
            if self.cells is not None:                  # nested table: fold the inner row into the outer cell
                if row:
                    self.cells.append(" / ".join(row))
                self.row_fences.extend(fences)
                return
            if row:
                self.out.append(" | ".join(row))
            for f in fences:
                self.out.extend(["```", *f, "```"])
            return
        if tag in ("td", "th") and self.cells is not None:
            self.flush()
            return
        if tag in BLOCK and not self.pre:
            self.flush()

    def handle_data(self, data):
        if self.skip:
            return
        self.buf.append(data if self.pre else clean(data))


def epub_toc_depths(z: zipfile.ZipFile, base: str, manifest) -> dict[str, int]:
    """href (relative to the OPF, no fragment) -> 1-based depth of its first entry in toc.ncx, or in the EPUB3 nav."""
    def read(href):
        return z.read(f"{base}/{href}" if base not in ("", ".") else href)

    depths: dict[str, int] = {}
    ncx = next((i.get("href") for i in manifest if i.get("media-type") == "application/x-dtbncx+xml"), None)
    if ncx:
        root = ET.fromstring(read(ncx))
        ns = {"n": "http://www.daisy.org/z3986/2005/ncx/"}

        def walk(node, d):
            for np_ in node.findall("n:navPoint", ns):
                c = np_.find("n:content", ns)
                if c is not None and c.get("src"):
                    depths.setdefault(c.get("src").split("#")[0], d)
                walk(np_, d + 1)
        nav_map = root.find("n:navMap", ns)
        if nav_map is not None:
            walk(nav_map, 1)
    if not depths:
        nav = next((i.get("href") for i in manifest if "nav" in (i.get("properties") or "").split()), None)
        if nav:
            x = ET.fromstring(read(nav))
            h = "{http://www.w3.org/1999/xhtml}"

            def walk_ol(ol, d):
                for li in ol.findall(f"{h}li"):
                    a = li.find(f"{h}a")
                    if a is not None and a.get("href"):
                        depths.setdefault(a.get("href").split("#")[0], d)
                    for sub in li.findall(f"{h}ol"):
                        walk_ol(sub, d + 1)
            for navel in x.iter(f"{h}nav"):
                for ol in navel.findall(f"{h}ol"):
                    walk_ol(ol, 1)
                break
    return depths


def epub_to_lines(p: Path) -> list[str]:
    z = zipfile.ZipFile(p)
    container = ET.fromstring(z.read("META-INF/container.xml"))
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    opf_path = container.find(".//c:rootfile", ns).get("full-path")
    opf = ET.fromstring(z.read(opf_path))
    ons = {"o": "http://www.idpf.org/2007/opf"}
    base = str(Path(opf_path).parent).replace("\\", "/")
    manifest = opf.findall(".//o:manifest/o:item", ons)
    items = {i.get("id"): i.get("href") for i in manifest}
    depth = epub_toc_depths(z, base, manifest)
    lines: list[str] = []
    last_depth = 1
    for ref in opf.findall(".//o:spine/o:itemref", ons):
        href = items.get(ref.get("idref"))
        if not href:
            continue
        name = f"{base}/{href}" if base not in ("", ".") else href
        try:
            data = z.read(name).decode("utf-8", "replace")
        except KeyError:
            continue
        if depth:
            d = depth.get(href.split("#")[0])
            level = d if d is not None else min(6, last_depth + 1)   # files missing from the TOC nest under the last entry
            if d is not None:
                last_depth = d
        else:
            level = None
        x = X2T(base_level=level)
        x.feed(data)
        x.flush()
        if x.out:
            lines.extend(page_marker(1 + sum(1 for l in lines if l.startswith("<!-- page "))))
        lines.extend(x.out)
    return lines


MONO = re.compile(r"courier|mono|consol|menlo", re.I)
BULLET = re.compile(r"^\s*(?:[•●◦▪–]|\d{1,2}\.|Step \d+)\s")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def join_spans(spans: list[dict]) -> str:
    """Concatenate a PDF line's spans, restoring the spaces Cisco's PDFs drop between differently styled runs.

    The runs abut exactly ("copy"[bold] "source-url"[italic] "running-config"[bold] "command"[regular] with
    x1 == next x0), so a space is added when there is a visible gap, or when the font changes between two
    word characters (bold = command keyword, italic = argument: a style change inside a word does not occur).
    """
    out, prev = "", None
    for s in spans:
        txt = s["text"]
        if prev is not None and out and txt and not out[-1].isspace() and not txt[0].isspace():
            gap = s["bbox"][0] - prev["bbox"][2]
            style_change = s["font"] != prev["font"] and (out[-1].isalnum() or out[-1] in ")]}") \
                and (txt[0].isalnum() or txt[0] in "([{<")
            if gap > 0.15 * max(s["size"], prev["size"]) or style_change:
                out += " "
        out += txt
        prev = s
    return out


def _key(s: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", s.strip().lower()))


def pdf_to_lines(p: Path) -> list[str]:
    """PyMuPDF block/span extraction.

    - paragraphs: the lines of one text block are joined with spaces (no space after a trailing hyphen); a bullet
      or "Step n"/"n." line starts a new paragraph
    - monospace blocks (Courier etc.) become fenced blocks with their line structure kept (CLI, show output, config)
    - page furniture: blocks in the top/bottom 9% of the page whose digit-normalized text repeats on >= 15% of
      pages (running headers/footers, page numbers) are dropped
    - headings: from the PDF outline when it has one (matched to the block text on the outline's page, unmatched
      entries emitted at the top of that page); otherwise from font size (larger than the body size, short, not
      monospace), ranked into levels by size
    """
    import pymupdf as fitz  # PyMuPDF
    from collections import Counter
    doc = fitz.open(p)
    toc = doc.get_toc(simple=True)   # [level, title, page]
    by_page: dict[int, list[tuple[int, str]]] = {}
    for lvl, title, page in toc:
        by_page.setdefault(page, []).append((lvl, clean(title).strip()))
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
            for ln in b["lines"]:
                t = clean(join_spans(ln["spans"])).rstrip()
                for s in ln["spans"]:
                    n = len(s["text"].strip())
                    total += n
                    is_mono = bool(MONO.search(s["font"]))
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
            key = _key(" ".join(lines))
            blk = {"lines": lines, "mono": mono / total >= 0.6, "bold": bold / total >= 0.9, "size": size,
                   "white": colors.most_common(1)[0][0] == 0xFFFFFF, "edge": edge, "key": key}
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
        n = _norm(t)
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
        n = _norm(" ".join(b["lines"]))
        return len(n) <= 160 and any(n != t and n.endswith(t) for t in toc_by_tail.get(n[-8:], ()))
    def head_style(b: dict):
        """(size, bold) style of a heading-like block, or None. Only used when the PDF has no outline."""
        text = re.sub(r"\s+", " ", " ".join(b["lines"])).strip()
        words = len(text.split())
        if toc or b["mono"] or b["white"] or b["edge"] or len(text) > 150 or words > 20 or not re.search(r"[A-Za-z]", text):
            return None
        if text.endswith(":") or (text.endswith(".") and words > 6) \
                or re.fullmatch(r"(Note|Tip|Tech tip|Caution|Warning|Important)\W*", text, re.I) \
                or re.search(r"\b(Figure|Table) \d+\.?$", text):
            return None
        if b["size"] >= body + 1.5 or (b["bold"] and b["size"] >= body - 1.0):
            return (b["size"], b["bold"])
        return None

    style_use = Counter(s for bl in pages for b in bl if (s := head_style(b)))
    head_styles = sorted((s for s, n in style_use.items() if n >= 3), reverse=True)[:6]   # one-off cover-page styles are not headings

    def paragraphs(lines: list[str]) -> list[str]:
        out: list[str] = []
        for l in lines:
            s = l.strip()
            if out and not BULLET.match(s):
                out[-1] = out[-1] + s if out[-1].endswith("-") else out[-1] + " " + s
            else:
                out.append(s)
        return out

    def last_prose(ls: list[str]) -> int | None:
        """Index of the last emitted prose line, looking back over page markers only."""
        for j in range(len(ls) - 1, -1, -1):
            l = ls[j]
            if l.startswith("<!-- page "):
                continue
            if l.startswith(("#", "```")) or " | " in l:
                return None
            return j
        return None

    lines: list[str] = []
    for i, blocks in enumerate(pages, start=1):
        pending = list(by_page.get(i, []))
        lines.extend(page_marker(i))
        first_text = True
        skip_next = False
        body_blocks = [b for b in blocks if not is_furniture(b)]
        for bi, b in enumerate(body_blocks):
            if skip_next:
                skip_next = False
                continue
            joined = re.sub(r"\s+", " ", " ".join(b["lines"])).strip()
            nj = _norm(joined)
            hit = None
            if pending and nj:
                hit = next((k for k, (_, t) in enumerate(pending) if _norm(t) == nj), None)
                if hit is None and bi + 1 < len(body_blocks) and len(nj) >= 6:
                    # a title wrapped into two blocks
                    nxt = _norm(" ".join(body_blocks[bi + 1]["lines"]))
                    hit = next((k for k, (_, t) in enumerate(pending) if _norm(t) == nj + nxt), None)
                    skip_next = hit is not None
            if hit is not None:
                for lvl, title in pending[: hit + 1]:       # emit outline entries up to and including this one
                    lines.append("#" * min(lvl, 6) + " " + title)
                del pending[: hit + 1]
                first_text = False
                continue
            style = head_style(b)
            if style in head_styles:
                lines.append("#" * (head_styles.index(style) + 1) + " " + joined)
                first_text = False
                continue
            if b["mono"]:
                lines.append("```")
                lines.extend(l.rstrip() for l in b["lines"])
                lines.append("```")
                first_text = False
                continue
            paras = paragraphs(b["lines"])
            if first_text and paras and paras[0][:1].islower():
                j = last_prose(lines)                         # a sentence continued across the page break
                if j is not None and not re.search(r"[.!?:;)\]\"']\s*$", lines[j]):
                    lines[j] = lines[j] + " " + paras[0]
                    paras = paras[1:]
            first_text = False
            lines.extend(paras)
        for lvl, title in pending:                        # outline entries not matched on the page
            lines.append("#" * min(lvl, 6) + " " + title)
    return lines


def html_to_lines(p: Path) -> list[str]:
    x = X2T()
    x.feed(p.read_text(encoding="utf-8", errors="replace"))
    x.flush()
    return x.out


def main() -> int:
    global PAGE_MARKERS
    src, key, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    PAGE_MARKERS = "--page-markers" in sys.argv[4:]
    ext = src.suffix.lower()
    lines = epub_to_lines(src) if ext == ".epub" else pdf_to_lines(src) if ext == ".pdf" else html_to_lines(src)
    # Cisco task-topic labels ("Procedure", "Example:", "Before you begin", ...) are marked up as headings; as headings
    # they cut every task into label-titled micro-sections, so they become plain label lines under the real heading.
    label = re.compile(r"#{1,6} (Procedure|Examples?:?|Before you begin|What to do next|SUMMARY STEPS|DETAILED STEPS)\s*$", re.I)
    # drop consecutive duplicate lines (running headers) and empty fences
    outl: list[str] = []
    fence = False
    for l in lines:
        if l.startswith("```"):
            fence = not fence
        elif not fence:
            m = label.match(l)
            if m:
                l = m.group(1).rstrip(":") + ":"
        if outl and l == outl[-1] and not fence and not l.startswith("```") and not l.startswith("<!-- page "):
            continue
        if l.startswith("<!-- page ") and fence:
            continue                                    # a marker never lands inside a fence (PDF fences are per block)
        if l.startswith("```") and not fence and outl and outl[-1].startswith("```"):
            outl.pop()                                  # empty fenced block
            continue
        outl.append(l)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{key}.txt").write_text("\n".join(outl) + "\n", encoding="utf-8")
    heads, fence, hash_in_fence = 0, False, 0
    for l in outl:
        if l.startswith("```"):
            fence = not fence
        elif fence and l.startswith("#"):
            hash_in_fence += 1       # rag_core parse_markdown_sections has no fence tracking: these would split sections
        elif not fence and re.match(r"#{1,6} ", l):
            heads += 1
    print(f"{key}: {len(outl)} lines, {heads} headings, {hash_in_fence} #-lines in fences, {sum(len(l) for l in outl) // 1024} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
