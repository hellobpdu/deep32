#!/usr/bin/env python3
"""Normalize an ePub or HTML source before converter v2 reads it. Two source defects are fixed:

1. Encoding: Cisco TechNote ePubs ship a cp1252-encoded content.opf (e.g. a raw 0xAE byte for the registered
   sign) without an encoding declaration, which XML parsers reject. Bytes that are not valid UTF-8 are read as
   cp1252 (latin-1 for the 5 bytes cp1252 leaves undefined) and re-encoded as UTF-8.
2. Run-together command words: Cisco's DITA markup puts inline code elements back to back or against text,
   e.g. <span class="kwd">match</span><span class="kwd">input</span><span class="kwd">vlan</span>,
   <span class="var">packets-in-burst</span>keyword or {a | b}<span class="kwd">port</span>, which the
   converter joins into 'matchinputvlan'. A space is inserted between such an element and an adjacent element,
   word or closing '}'/']' (only before a word of 2+ letters there: 'd[Ctrl-V]g', '[0-9]:' stay as they are).
   <pre> blocks are not touched.
   The same markup also starts or ends a code element in the middle of a word ('t<span class="kwd">imers',
   '<span class="var">a</span>rgument', 'radiu<span class="kwd">s</span>'). So between an element and a word
   no space goes in when the two letter runs it would separate join into a word the document uses (>= 3
   times) and one of them is not a word the document uses on its own (<= 1 time). The counts come from the
   document's text with the tags removed (all markup members of an ePub). Two adjacent elements are always
   separate words ('<kwd>username</kwd><kwd>privilege</kwd>').

usage: prep_source.py <in.epub|in.html> <out>
Writes <out> only when something changed, and prints what changed (or 'clean'). ePub members are copied byte
for byte unless changed, with 'mimetype' first and stored uncompressed as the OCF container spec requires.
"""
from __future__ import annotations

import codecs
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

TEXT = (".opf", ".ncx", ".xhtml", ".html", ".htm", ".xml")
MARKUP = (".xhtml", ".html", ".htm")
CODE = r'<span class="[^"]*\b(?:kwd|var|synph|cmdname|parmname|option)\b[^"]*">'
# element + element, unless the first ends in a joiner ('spanning-'+'tree', '{'+'id', 'ip['+'route')
ADJACENT = re.compile(r"(?<![-/{\[(])(</span>)(?=(?:<span>)?" + CODE + ")")
AFTER = re.compile(r"(" + CODE + r"[^<]*</span>(?:</span>)*)(?=[A-Za-z])")           # element + word
BEFORE = re.compile(r"(?<=[A-Za-z])(?=(?:<span>)?" + CODE + ")")                    # word + element
CLOSER = re.compile(r"(?<=[}\]])(?=(?:<span>)?" + CODE + ")")                       # '}' or ']' + element
PRE = re.compile(r"(?is)(<pre\b.*?</pre>)")
TAG = re.compile(r"<[^>]*>")
WORD = re.compile(r"[a-z]+")
LEFT_RUN = re.compile(r"([A-Za-z]*)(?:<[^>]*>)*$")      # letters just before a point, skipping adjacent tags
RIGHT_RUN = re.compile(r"(?:<[^>]*>)*([A-Za-z]*)")       # letters just after a point, skipping adjacent tags
JOINED_MIN, PART_MAX = 3, 1


def _cp1252_bytes(err: UnicodeDecodeError) -> tuple[str, int]:
    bad = err.object[err.start:err.end]
    return "".join(bytes([b]).decode("cp1252", errors="ignore") or chr(b) for b in bad), err.end


codecs.register_error("cp1252-fallback", _cp1252_bytes)


def to_utf8(data: bytes) -> tuple[str, bool]:
    """(text, repaired): each byte that is not valid UTF-8 read as cp1252; valid UTF-8 sequences kept."""
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="cp1252-fallback"), True


def lexicon(htmls: list[str]) -> Counter:
    """Lowercase word counts of the text with the tags removed (a word split by tags counts as one word)."""
    lex: Counter = Counter()
    for h in htmls:
        lex.update(WORD.findall(TAG.sub("", h).lower()))
    return lex


def splits_word(s: str, i: int, lex: Counter) -> bool:
    """Would a space at s[i] cut one word in two? True when the letter runs on both sides join into a word the
    document uses and one side is not a word the document uses on its own."""
    left = LEFT_RUN.search(s, max(0, i - 400), i).group(1).lower()
    right = RIGHT_RUN.match(s, i).group(1).lower()
    return bool(left and right) and lex[left + right] >= JOINED_MIN and min(lex[left], lex[right]) <= PART_MAX


def space_inline_code(html: str, lex: Counter | None = None) -> tuple[str, int]:
    """Insert the missing spaces around inline code elements outside <pre>; returns (html, insertions).
    lex: word counts of the whole document (default: of this html)."""
    lex = lexicon([html]) if lex is None else lex
    total = 0
    parts = PRE.split(html)
    for i in range(0, len(parts), 2):                 # even indexes are outside <pre>
        s = parts[i]
        for rx in (ADJACENT, AFTER, BEFORE, CLOSER):
            out, last = [], 0
            for m in rx.finditer(s):
                if rx in (AFTER, BEFORE) and splits_word(s, m.end(), lex):
                    continue
                if rx is CLOSER and len(RIGHT_RUN.match(s, m.end()).group(1)) < 2:
                    continue
                out.append(s[last:m.end()] + " ")
                last = m.end()
                total += 1
            s = "".join(out) + s[last:]
        parts[i] = s
    return "".join(parts), total


def prep_member(name: str, data: bytes, lex: Counter | None = None) -> tuple[bytes, list[str]]:
    text, repaired = to_utf8(data)
    notes = ["re-encoded"] if repaired else []
    if name.lower().endswith(MARKUP):
        text, n = space_inline_code(text, lex)
        if n:
            notes.append(f"{n} spaces")
    return (text.encode("utf-8") if notes else data), notes


def prep(src: Path, dst: Path) -> list[str]:
    if src.suffix.lower() != ".epub":
        out, notes = prep_member(src.name, src.read_bytes())
        if notes:
            dst.write_bytes(out)
        return [f"{src.name}: {', '.join(notes)}"] if notes else []
    changed: dict[str, bytes] = {}
    log: list[str] = []
    with zipfile.ZipFile(src) as zin:
        lex = lexicon([to_utf8(zin.read(i))[0] for i in zin.infolist() if i.filename.lower().endswith(MARKUP)])
        for i in zin.infolist():
            if i.filename.lower().endswith(TEXT):
                out, notes = prep_member(i.filename, zin.read(i), lex)
                if notes:
                    changed[i.filename] = out
                    log.append(f"{i.filename}: {', '.join(notes)}")
        if not changed:
            return []
        with zipfile.ZipFile(dst, "w") as zout:
            for i in sorted(zin.infolist(), key=lambda i: i.filename != "mimetype"):
                method = zipfile.ZIP_STORED if i.filename == "mimetype" else zipfile.ZIP_DEFLATED
                zout.writestr(zipfile.ZipInfo(i.filename, i.date_time), changed.get(i.filename, zin.read(i)),
                              compress_type=method)
    return log


def main() -> int:
    log = prep(Path(sys.argv[1]), Path(sys.argv[2]))
    reenc = sum("re-encoded" in l for l in log)
    spaces = sum(int(m.group(1)) for l in log for m in [re.search(r"(\d+) spaces", l)] if m)
    print(f"prep: {reenc} members re-encoded, {spaces} spaces inserted" if log else "prep: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
