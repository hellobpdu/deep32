"""Read side of a root for the v2 API: documents, sections and source text (rag_core.structure).

The structure tables are loaded once; source files are read on first use and kept (a root's
sources/ is written once by its build and never changes while it is served). A root without the
structure tables loads as an empty corpus: search still answers, with text from the chunks.
"""
from __future__ import annotations

import bisect
import contextlib
import json
import math
import re
import sqlite3
import threading
import time
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag_core.db import DEFAULT_FTS, OPTIN_DOCS, OPTIN_FTS, connect, is_split
from rag_core.kb import version_key
from rag_core.paths import RAGPaths
from rag_core.retrievers.lexical import STOPWORDS, for_table
from rag_core.sections import page_furniture, slugify
from rag_core.structure import display_lines, has_structure


QUERY_TOKEN = re.compile(r"[A-Za-z0-9_./:%-]+")
# the number of a heading: "4.0 ", "9.1.2.  ", "A.1 ", "Appendix A: " (rag_core.sections)
HEADING_NUMBER = re.compile(
    r"(?i)^(?:(?:appendix|annex)\s+[a-z0-9]{1,4}|[a-z]\.(?:\d+\.?)*|\d+(?:\.\d+)*)[.:]?\s+"
)
QUOTE_CHARS = str.maketrans({
    '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"', '\xa0': " ",
    '\u2010': "-", '\u2011': "-", '\u2012': "-", '\u2013': "-", '\u2014': "-",
})
ELLIPSIS = re.compile('\\s*(?:\\[\\.\\.\\.\\]|\\.\\.\\.|\u2026)\\s*')
CANON_DROP = re.compile(r"[\s-]+")
# verify_quote, a quotation with omissions ("..."): retrieval.json verify.ellipsis (rag_core.api_v2.verify_settings)
VERIFY_ELLIPSIS = ("any", "bounded")        # the first is the default
DEFAULT_MAX_GAP_CHARS = 600                 # verify.max_gap_chars
# bounded_spans gives up (the quotation does not pass) past these: more parts than any quotation needs, or parts so
# short that they occur this many times in one document
BOUNDED_MAX_PARTS = 16
BOUNDED_MAX_OCCURRENCES = 50_000
GREP_LINE_CHARS = 300
MAX_REGEX_SIZE = 10_000     # parse-tree nodes a grep regex may unroll to (compile_regex)
# a word as the BM25 index's FTS5 unicode61 tokenizer cuts one: a run of letters and digits
FTS_WORD = re.compile(r"[^\W_]+")
# the documents with a chunk the FTS5 query matches (the IN form: the join made SQLite read the
# phrase "of the" for 1.9 s instead of 0.1 s on the 55k-chunk corpus-v4 root)
DOCS_MATCHING = """
SELECT DISTINCT doc_id FROM chunks
WHERE rowid IN (SELECT rowid FROM chunk_fts WHERE chunk_fts MATCH ?)
"""
# the most of a grep's budget scan_order may spend on those queries (at most a quarter of it): a
# phrase of common words ("of the", "in the router") reads 2-7 s of FTS5 pages from a cold cache
SCAN_ORDER_SECONDS = 0.5
PROGRESS_STEPS = 1000       # SQLite VM steps between two looks at scan_order's clock
# the fewest characters a second a literal scan is taken to read: deep32-engine reads the whole corpus (123 M)
# within the 3 s budget when idle, and busy it read fewer than 28 M a second (the budget ran out before
# 84.9 M). Documents holding no more than budget x this many are all read in time, in any order, so grep
# reads them in the order given; a scoped grep of a few guides then skips scan_order's FTS5 queries
# (20-250 ms warm for "the router" or "is a" on the corpus-v5 texts, more when cold)
SCAN_CHARS_PER_SECOND = 4_000_000
# what stdlib re's IGNORECASE matches to an ASCII letter and the regex module's does not (EngineScan): U+0130 to
# I and U+0131 to i, measured over every code point and every printable ASCII character (CPython 3.12.14, regex
# 2026.9.10); both match U+017F to s and U+212A to k
DOTTED_I = ("\u0130", "\u0131")
# evidence(): display lines prepared around the passage first, on each side; widened (x4) only
# when the window reaches a cut that is not the real edge of its range
NEAR_LINES = 64

# headed(): identifiers a section heading can name (api_v2.lookup's fallback). A PowerShell cmdlet is
# Verb-Noun with one of PowerShell's verbs (Get-Verb, and the core cmdlets' own ForEach, Sort, Tee
# and Where), so a networking term of the same shape (IS-IS, VRF-Lite, Route-Map) is none; an about
# topic is about_<Topic>, a dot or hyphen allowed inside (about_Windows_PowerShell_5.1,
# about_Built-in_Functions, about_WS-Management_Cmdlets); an HTTP API operation is
# "METHOD /path" (a heading may go on with a summary).
PS_VERBS = frozenset(verb.casefold() for verb in """
Add Clear Close Copy Enter Exit Find Format Get Hide Join Lock Move New Open Optimize Pop Push Redo Remove
Rename Reset Resize Search Select Set Show Skip Split Step Switch Undo Unlock Watch Connect Disconnect Read
Receive Send Write Backup Checkpoint Compare Compress Convert ConvertFrom ConvertTo Dismount Edit Expand
Export Group Import Initialize Limit Merge Mount Out Publish Restore Save Sync Unpublish Update Debug
Measure Ping Repair Resolve Test Trace Approve Assert Build Complete Confirm Deny Deploy Disable Enable
Install Invoke Register Request Restart Resume Start Stop Submit Suspend Uninstall Unregister Wait Block
Grant Protect Revoke Unblock Unprotect Use ForEach Sort Tee Where
""".split())
CMDLET = re.compile(r"([A-Za-z]+)-[A-Za-z][A-Za-z0-9]*")
ABOUT_TOPIC = re.compile(r"(?i)about_[a-z0-9_]+(?:[.-][a-z0-9_]+)*")
API_OPERATION = re.compile(r"(?i)(GET|POST|PUT|PATCH|DELETE)\s+(/\S*)")
API_HEADING = re.compile(r"(?i)(GET|POST|PUT|PATCH|DELETE)\s+(/\S*)(?:\s.*)?")
API_PATH = re.compile(r"/\S*")
PATH_PARAMETER = re.compile(r"\{[^{}/]+\}")
HEADED_MAX = 10


@dataclass
class Sec:
    section_id: str
    doc_key: str
    ordinal: int
    level: int
    anchor: str
    heading: str | None
    parent: "Sec | None"
    line_start: int
    line_end: int
    n_chars: int

    @property
    def path(self) -> list[str]:
        path: list[str] = []
        sec: Sec | None = self
        while sec is not None:
            if sec.heading:
                path.append(sec.heading)
            sec = sec.parent
        return path[::-1]


@dataclass
class Doc:
    doc_id: str
    doc_key: str
    doc_type: str
    title: str | None
    release: str | None
    platform: str | None
    source_url: str | None
    path: Path
    sections: list[Sec] = field(default_factory=list)
    starts: list[int] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return f"{self.doc_key}{self.path.suffix}"

    @property
    def rfc(self) -> int | None:
        m = re.fullmatch(r"rfc(\d+)", self.doc_key)
        return int(m.group(1)) if self.doc_type == "rfc" and m else None

    def section_at(self, line: int) -> Sec | None:
        """The deepest section containing `line`."""
        i = bisect.bisect_right(self.starts, line) - 1
        return self.sections[i] if i >= 0 else None

    def local(self, first: int, last: int) -> str:
        return f"{self.filename}:{first}-{last}"

    def by_title(self, slug: str) -> Sec | None:
        """The one section whose heading, its number left out, slugifies to `slug`:
        "security-considerations" names "4.0 Security Considerations". None when none or
        several do."""
        found = [
            s for s in self.sections
            if s.heading and slugify(HEADING_NUMBER.sub("", s.heading)) == slug
        ]
        return found[0] if len(found) == 1 else None

    def url(self, sec: Sec | None) -> str | None:
        'RFC Editor HTML with the section anchor ("\u2026/rfc3101.html#section-3.2",\n        "#appendix-A.1"); the source URL for other documents.'
        if self.rfc is None:
            return self.source_url
        base = f"https://www.rfc-editor.org/rfc/rfc{self.rfc}.html"
        if sec is None:
            return base
        if sec.anchor[0].isdigit():
            return f"{base}#section-{sec.anchor.split('-')[0]}"
        m = re.fullmatch(r"appendix-([a-z0-9]+)((?:\.\d+)*)", sec.anchor)
        return f"{base}#appendix-{m.group(1).upper()}{m.group(2)}" if m else base

    def cite(self, sec: Sec | None) -> str:
        '"RFC 3101 \xa73.2", "RFC 3101 Appendix A", "RFC 3101 \u203a Abstract",\n        "<doc title> \u203a <heading path>".'
        if self.rfc is not None:
            base = f"RFC {self.rfc}"
            if sec is None or sec.heading is None:
                return base
            if sec.anchor[0].isdigit():
                return f"{base} \xa7{sec.anchor.split('-')[0]}"
            m = re.fullmatch(r"appendix-([a-z0-9]+)((?:\.\d+)*)(?:-\d+)?", sec.anchor)
            if m:
                return f"{base} Appendix {m.group(1).upper()}{m.group(2)}"
            return f"{base} \u203a {sec.heading}"
        path = sec.path if sec else []
        if path and self.title and path[0].casefold() == self.title.casefold():
            path = path[1:]             # a tech note's top heading is its title
        return ' \u203a '.join([self.title or self.doc_key, *path])


class SourceFile:
    """A source file's text with line offsets (1-based lines, "\n"-separated)."""

    def __init__(self, path: Path, doc_type: str):
        self.text = path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")
        self.starts = array("I", [0])
        for m in re.finditer("\n", self.text):
            self.starts.append(m.end())
        self.end = len(self.text)
        if self.text.endswith("\n"):
            self.starts.pop()           # a final newline ends the last line, it opens none
            self.end -= 1
        self.n_lines = len(self.starts)
        self.furniture = page_furniture(self.lines(1, self.n_lines)) if doc_type == "rfc" else set()

    @property
    def dotted_i(self) -> bool:
        """Whether the text holds U+0130 or U+0131 (DOTTED_I; EngineScan), found on its first use and kept: a text
        of Latin-1 characters answers at once, without a scan."""
        found = self.__dict__.get("_dotted_i")
        if found is None:
            found = self._dotted_i = any(c in self.text for c in DOTTED_I)
        return found

    def line_at(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)

    def line(self, n: int) -> str:
        end = self.starts[n] - 1 if n < self.n_lines else self.end
        return self.text[self.starts[n - 1]:end]

    def lines(self, first: int, last: int) -> list[str]:
        return [self.line(n) for n in range(max(1, first), min(last, self.n_lines) + 1)]

    def shown(self, first: int, last: int) -> list[tuple[int, str]]:
        """(line, text) of lines first..last as a reader sees them (structure.display_lines)."""
        first, last = max(1, first), min(last, self.n_lines)
        furniture = {i - first + 1 for i in self.furniture if first - 1 <= i < last}
        return [(first + i - 1, t) for i, t in display_lines(self.lines(first, last), furniture)]


def query_terms(*queries: str) -> list[str]:
    terms = []
    for query in queries:
        for token in QUERY_TOKEN.findall(query):
            term = token.lower().strip(".:/-")
            if len(term) >= 2 and term not in STOPWORDS:
                terms.append(term)
    return list(dict.fromkeys(terms))


def _term_hits(text: str, terms: list[str]) -> set[int]:
    """Indexes of the terms that start a word in `text` (case-insensitive)."""
    low = text.lower()
    hits = set()
    for i, term in enumerate(terms):
        pos = low.find(term)
        while pos >= 0:
            if pos == 0 or not low[pos - 1].isalnum():
                hits.add(i)
                break
            pos = low.find(term, pos + 1)
    return hits


def best_line(pairs: list[tuple[int, str]], terms: list[str]) -> int | None:
    """Index in `pairs` of the centre of the passage that covers the most (rarest) query terms;
    None when no term occurs."""
    if not pairs or not terms:
        return None
    hits = [_term_hits(text, terms) for _, text in pairs]
    df = [sum(1 for h in hits if i in h) for i in range(len(terms))]
    weight = [math.log(1 + len(pairs) / d) if d else 0.0 for d in df]

    def score(k: int) -> float:
        around = set().union(*hits[max(0, k - 1):k + 2])
        return sum(weight[i] for i in around) + 0.5 * sum(weight[i] for i in hits[k])

    best = max(range(len(pairs)), key=lambda k: (score(k), -k))
    return best if score(best) > 0 else None


def cut_line(text: str, terms: list[str], max_chars: int) -> str:
    """A line longer than max_chars, cut to a window around its first query term."""
    low = text.lower()
    found = [p for p in (low.find(t) for t in terms) if p >= 0]
    centre = min(found) if found else 0
    start = max(0, min(centre - max_chars // 3, len(text) - max_chars))
    if start:
        start = text.find(" ", start) + 1 or start
    piece = text[start:start + max_chars - 2]
    if start + len(piece) < len(text):
        piece = piece[: piece.rfind(" ")] if " " in piece else piece
        piece += ' \u2026'
    return ('\u2026 ' if start else "") + piece


def grow(
    pairs: list[tuple[int, str]],
    centre: int,
    max_chars: int,
) -> tuple[int, int, bool, bool]:
    """window()'s growth: from pairs[centre], one line at a time on each side while the text
    stays within max_chars. Returns (lo, hi, stopped at the start of `pairs`, stopped at its
    end): a growth that never met an end of `pairs` is the same in any longer list."""
    lo = hi = centre
    size = len(pairs[centre][1])
    at_start = at_end = False
    grew = True
    while grew:
        grew = False
        for side in (1, -1):
            k = hi + 1 if side == 1 else lo - 1
            if not 0 <= k < len(pairs):
                if side == 1:
                    at_end = True
                else:
                    at_start = True
                continue
            if size + 1 + len(pairs[k][1]) <= max_chars:
                size += 1 + len(pairs[k][1])
                hi, lo = (k, lo) if side == 1 else (hi, k)
                grew = True
    return lo, hi, at_start, at_end


def body_of(pairs: list[tuple[int, str]], lo: int, hi: int) -> tuple[str, int, int]:
    """pairs[lo..hi] without blank lines at either end, joined; (text, first line, last line)."""
    while lo < hi and not pairs[lo][1]:
        lo += 1
    while hi > lo and not pairs[hi][1]:
        hi -= 1
    body = "\n".join(t for _, t in pairs[lo:hi + 1])
    return body, pairs[lo][0], pairs[hi][0]


def window(
    pairs: list[tuple[int, str]],
    centre: int,
    max_chars: int,
    terms: list[str],
) -> tuple[str, int, int]:
    """Text of at most max_chars around pairs[centre], growing one line at a time on each side;
    returns (text, first line, last line)."""
    text = pairs[centre][1]
    if len(text) > max_chars:
        return cut_line(text, terms, max_chars), pairs[centre][0], pairs[centre][0]
    lo, hi, _, _ = grow(pairs, centre, max_chars)
    return body_of(pairs, lo, hi)


def chunk_window(text: str, terms: list[str], max_chars: int) -> str:
    """Evidence window from the chunk text itself (a root without the structure index)."""
    pairs = list(enumerate(text.split("\n"), 1))
    return window(pairs, best_line(pairs, terms) or 0, max_chars, terms)[0]


def release_spellings(release: str | None) -> set[str]:
    """How a doc_key may spell a release: its first two or more dot-separated parts run together, lower case.
    "17.9" -> {"179"}; "17.18.x" -> {"1718", "1718x"}; "26.x" -> {"26x"}; "2.3.7.9" -> {"23", "237", "2379"}.
    A release of one part ("17") has none: one number is not enough to tell it from any other token."""
    parts = [p for p in re.split(r"[.\s]+", (release or "").strip().lower()) if p]
    return {"".join(parts[:n]) for n in range(2, len(parts) + 1)}


def book_family(doc: "Doc") -> tuple[str, str] | None:
    """(doc_type, the doc_key with the token that names the release replaced by "*"): the same for the books that
    are one book at several releases (c9300-179-rtng, c9300-1718-rtng and c9300-26x-rtng are "c9300-*-rtng";
    ise31-admin-pxgrid and ise34-admin-pxgrid are "ise*-admin-pxgrid"). The token is found from what the
    structure index stores, doc_key and release (doc_sources): the one "-"-separated token after the first that
    is a spelling of the release (release_spellings), else a first token of letters followed by such a spelling.
    None when it cannot be decided: no stored release, a release of one part, no such token, or two of them."""
    spellings = release_spellings(doc.release)
    if not spellings:
        return None
    tokens = doc.doc_key.lower().split("-")
    at = [i for i, token in enumerate(tokens) if i and token in spellings]
    if len(at) == 1:
        tokens[at[0]] = "*"
    elif not at and (m := re.fullmatch(r"([a-z]+)(\d[\dx]*)", tokens[0])) and m.group(2) in spellings:
        tokens[0] = m.group(1) + "*"
    else:
        return None
    return doc.doc_type, "-".join(tokens)


def heading_key(heading: str | None) -> str:
    """A heading for comparison: case and runs of whitespace do not tell two apart."""
    return " ".join((heading or "").casefold().split())


def ref_candidates(ref: str) -> list[str]:
    """Section ids a ref may name, the literal one first: as given, lowercased, and without a
    "section-" anchor prefix (RFC Editor URL style, "rfc3101#section-3.2"). The literal id
    comes first because a heading slug may itself start with "section-"."""
    ids = []
    for candidate in (ref, ref.lower()):
        key, _, anchor = candidate.partition("#")
        ids += [f"{key}#{anchor}", f"{key}#{anchor.removeprefix('section-')}"]
    return list(dict.fromkeys(ids))


def canonical(text: str) -> str:
    """Quote comparison form: typographic quotes and dashes made ASCII, then every run of
    whitespace and hyphens removed (joins hyphenation breaks and re-wrapped lines)."""
    return CANON_DROP.sub("", text.translate(QUOTE_CHARS))


def in_order(text: str, parts: list[str]) -> list[tuple[int, int]] | None:
    """Where the parts of a quotation stand in `text` (both in canonical form) when each is taken at its first
    place after the part before it, however far: [(start, end)], or None when a part does not follow. The rule
    of verify.ellipsis "any", and of a quotation without omissions (one part)."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for part in parts:
        found = text.find(part, pos)
        if found < 0:
            return None
        pos = found + len(part)
        spans.append((found, pos))
    return spans


def widest_gap(spans: list[tuple[int, int]]) -> int:
    """The most canonical characters between two neighbouring parts; 0 for one part."""
    return max((b[0] - a[1] for a, b in zip(spans, spans[1:])), default=0)


def bounded_spans(
    text: str,
    parts: list[str],
    max_gap: int,
    section_of,
) -> list[tuple[int, int]] | None:
    """verify.ellipsis "bounded": the parts placed in order so that each starts at most `max_gap` canonical
    characters after the end of the part before it and all of them lie in one section (`section_of(offset)`: the
    deepest section of the line that holds the canonical offset; a part that itself runs from one section into
    another has no place). [(start, end)] of the leftmost such placement (the first part as far left as any
    placement allows, then each next part as far left as the rest allows), or None when there is none.

    When the places in_order() finds satisfy the bound, they are the answer: the leftmost placement is then the
    same one. Every way of placing the parts is considered, not only that one: a part that occurs twice may have
    to take its second place to stay near the next part.

    The work is bounded: more than BOUNDED_MAX_PARTS parts, or parts that occur more than
    BOUNDED_MAX_OCCURRENCES times in all, are not placed (None)."""
    if len(parts) > BOUNDED_MAX_PARTS:
        return None
    budget = BOUNDED_MAX_OCCURRENCES
    starts: dict[str, list[int]] = {}
    for part in dict.fromkeys(parts):
        found: list[int] = []
        at = text.find(part)
        while at >= 0:
            budget -= 1
            if budget < 0:
                return None
            if section_of(at) is section_of(at + len(part) - 1):
                found.append(at)
            at = text.find(part, at + 1)
        if not found:
            return None
        starts[part] = found
    # feasible[k]: the starts of part k from which every later part can still be placed, ascending
    feasible: list[list[int]] = [starts[parts[-1]]]
    for k in range(len(parts) - 2, -1, -1):
        following, size, keep = feasible[0], len(parts[k]), []
        for start in starts[parts[k]]:
            end = start + size
            i = bisect.bisect_left(following, end)
            while i < len(following) and following[i] - end <= max_gap:
                if section_of(following[i]) is section_of(start):
                    keep.append(start)
                    break
                i += 1
        if not keep:
            return None
        feasible.insert(0, keep)
    spans = [(feasible[0][0], feasible[0][0] + len(parts[0]))]
    home = section_of(spans[0][0])
    for k in range(1, len(parts)):
        i = bisect.bisect_left(feasible[k], spans[-1][1])
        while section_of(feasible[k][i]) is not home:
            i += 1
        spans.append((feasible[k][i], feasible[k][i] + len(parts[k])))
    return spans


def regex_size(pattern: str, flags: int = 0) -> int:
    """Nodes the regex module's compiler builds for `pattern`: its parse tree with every repeat's
    body counted once per repetition (at the upper count, or the lower one when unbounded).

    The tree comes from the module's own parser, set up as regex.compile() sets it up. Parsing
    takes microseconds and expands nothing, and it follows every syntax the module accepts
    (verbose mode skips spaces and comments inside "{1 000}", for one). Raises regex.error."""
    import regex as rx
    from regex import _regex_core as core

    while True:
        source = core.Source(pattern)
        info = core.Info(flags, source.char_type)
        info.guess_encoding = rx.UNICODE
        source.ignore_space = bool(info.flags & rx.VERBOSE)
        try:
            tree = core._parse_pattern(source, info)
            break
        except core._UnscopedFlagSet:       # a global flag late in the pattern: parse again
            flags = info.global_flags
    if not source.at_end():
        raise rx.error("unbalanced parenthesis", pattern, source.pos)

    def size(node: Any) -> int:
        children = [getattr(node, name, None) for name in ("subpattern", "yes_item", "no_item")]
        children += [*getattr(node, "items", ()), *getattr(node, "branches", ())]
        n = 1 + sum(size(child) for child in children if child is not None)
        if isinstance(node, core.GreedyRepeat):         # LazyRepeat, PossessiveRepeat too
            n *= max(1, node.min_count, node.max_count or 0)
        return n

    return size(tree)


def compile_regex(pattern: str, ignore_case: bool):
    """`pattern` compiled by the regex module (its matches take a timeout), not cached.

    Compiling runs in C with the GIL held and copies a repeat's body once per repetition, so
    one short pattern could stall or exhaust the service: (?:(?:a{1000}){1000}){10} took
    3.9 s and 2.9 GB, and 120 dots repeated {9999} times 158 MB. A pattern whose regex_size
    exceeds MAX_REGEX_SIZE is refused (measured: about 0.3 KB and 0.4 us per node). The
    module would also keep every compiled pattern in its cache."""
    import regex as rx      # third-party but pinned in the deep32-engine venv

    flags = rx.IGNORECASE if ignore_case else 0
    try:
        size = regex_size(pattern, flags)
        if size > MAX_REGEX_SIZE:
            raise ValueError(f"pattern unrolls to {size} nodes (limit {MAX_REGEX_SIZE}); "
                             "use smaller repeat counts")
        return rx.compile(pattern, flags, cache_pattern=False)
    except rx.error as exc:
        raise ValueError(str(exc)) from None


class EngineScan:
    """A grep pattern run by the regex module with concurrent=True (retrieval.json "grep": {"regex_engine": true},
    api_v2.grep_settings; default off). Without it a literal grep is searched by stdlib re, which holds the GIL for
    the whole scan of a document (575 ms for 100 M characters: every thread beside it waits that long) and has no
    fast search for a case-insensitive literal; the regex module releases the GIL while it matches a str, which is
    safe (immutable), and finds such a literal 6-44x sooner (937 ms against 55 ms over the 150 M-character local
    corpus). A regex grep already ran on the module without the GIL (its default for a str); concurrent=True only
    makes that explicit.

    The matches are the ones RootReader.grep finds without it, in the same order. A regex is compiled as
    compile_regex compiles it (the same module; its timeout is honoured as before). A literal is escaped and searched
    as stdlib re searches it, except where the two engines fold case differently: under IGNORECASE stdlib re matches
    U+0130 to I and U+0131 to i and the regex module neither (DOTTED_I). So a case-insensitive literal with an i or I
    is searched by stdlib re in a document that holds one of them (SourceFile.dotted_i), and one with a non-ASCII
    character, whose folding was not compared, in every document. A literal takes no timeout, as before: the budget
    is looked at between documents and matches (the module ignores a timeout on its fast literal search anyway)."""

    def __init__(self, pattern: str, *, regex: bool, ignore_case: bool):
        import regex as rx      # rag_core.api_v2.grep_settings refuses the setting at start without it

        self.regex = regex
        self.exact = None               # stdlib re, for the documents where the engines may differ
        self.everywhere = False         # ... every document, not only those holding DOTTED_I
        if regex:
            self.compiled = compile_regex(pattern, ignore_case)
            return
        self.compiled = rx.compile(rx.escape(pattern), rx.IGNORECASE if ignore_case else 0, cache_pattern=False)
        if ignore_case and (not pattern.isascii() or "i" in pattern.lower()):
            self.exact = re.compile(re.escape(pattern), re.IGNORECASE)
            self.everywhere = not pattern.isascii()

    def finditer(self, source: SourceFile, remaining: float):
        if self.exact is not None and (self.everywhere or source.dotted_i):
            return self.exact.finditer(source.text)
        if self.regex:
            return self.compiled.finditer(source.text, timeout=remaining, concurrent=True)
        return self.compiled.finditer(source.text, concurrent=True)


def named_identifier(text: str) -> str | None:
    """The key (casefolded) of a PowerShell cmdlet name or about topic; None for any other text."""
    text = text.strip()
    m = CMDLET.fullmatch(text)
    if m and m.group(1).casefold() in PS_VERBS or ABOUT_TOPIC.fullmatch(text):
        return text.casefold()
    return None


def path_segments(path: str) -> tuple[str, ...]:
    """An HTTP path's segments, casefolded ("/ers/config/" -> ("ers", "config"))."""
    return tuple(path.casefold().rstrip("/").split("/")[1:])


def segments_match(asked: tuple[str, ...], heading: tuple[str, ...]) -> bool:
    """Whether a path asked for names the operation a heading's path template describes: segment by segment the
    same text, or a template segment ({id}) of the heading, which stands for any one segment. A template segment
    asked for matches only a template segment."""
    return len(asked) == len(heading) and all(
        a == h or PATH_PARAMETER.fullmatch(h) is not None for a, h in zip(asked, heading)
    )


def docs_matching(conn, query: str, table: str = DEFAULT_FTS) -> list[str]:
    """doc_id of each document with a chunk the FTS5 query matches in `table`: all of them, or
    sqlite3.Error (an interrupted query answers nothing)."""
    sql = DOCS_MATCHING if table == DEFAULT_FTS else for_table(DOCS_MATCHING, table)
    return [doc_id for (doc_id,) in conn.execute(sql, (query,))]


def fts_tables(conn, docs: list[Doc]) -> tuple[str, ...]:
    """The BM25 tables that hold chunks of `docs`: chunk_fts; in a root that keeps the opt-in doc
    types' chunks apart (rag_core.db.split_optin_fts), chunk_fts_optin for the opt-in documents,
    each table only when some of `docs` are its own (a default grep asks chunk_fts alone, as
    before)."""
    if not is_split(conn):
        return (DEFAULT_FTS,)
    optin = {doc_id for (doc_id,) in conn.execute(f"SELECT doc_id FROM {OPTIN_DOCS}")}
    return tuple(
        table
        for table, wanted in (
            (DEFAULT_FTS, any(doc.doc_id not in optin for doc in docs)),
            (OPTIN_FTS, any(doc.doc_id in optin for doc in docs)),
        )
        if wanted
    )


class RootReader:
    def __init__(self, paths: RAGPaths):
        self.paths = paths
        self.docs: dict[str, Doc] = {}
        self.by_id: dict[str, Doc] = {}
        self.sections: dict[str, Sec] = {}
        self.n_chunks: dict[str, int] = {}
        self._files: dict[str, SourceFile] = {}
        self._lock = threading.Lock()
        self._of_types: dict[frozenset[str], frozenset[str]] = {}
        self._headings: tuple[dict, list] | None = None
        self._families: dict[tuple[str, str], list[Doc]] | None = None
        self._heading_counts: dict[str, dict[str, int]] = {}
        self.structured = False

        sources = paths.root / "sources"
        with connect(paths.sqlite / "rag.db") as conn:
            self.n_chunks = dict(conn.execute("SELECT doc_id, COUNT(*) FROM chunks GROUP BY doc_id"))
            if not has_structure(conn):
                return
            self.structured = True
            for r in conn.execute("SELECT * FROM doc_sources ORDER BY doc_key"):
                doc = Doc(
                    r["doc_id"], r["doc_key"], r["doc_type"], r["title"], r["release"],
                    r["platform"], r["source_url"], sources / r["rel_path"],
                )
                self.docs[doc.doc_key] = doc
                self.by_id[doc.doc_id] = doc
            rows = conn.execute("SELECT * FROM doc_sections ORDER BY doc_id, ordinal")
            for r in rows:
                doc = self.by_id.get(r["doc_id"])
                if doc is None:
                    continue
                sec = Sec(
                    r["section_id"], doc.doc_key, r["ordinal"], r["level"], r["anchor"],
                    r["heading"], self.sections.get(r["parent_section_id"] or ""),
                    r["line_start"], r["line_end"], r["n_chars"],
                )
                doc.sections.append(sec)
                doc.starts.append(sec.line_start)
                self.sections[sec.section_id] = sec

    def doc_ids_of(self, doc_types: frozenset[str]) -> frozenset[str]:
        """doc_id of every document of these doc types (api_v2.filter_docs' opt-in exclusion), made
        once per set: the corpus does not change while it is served."""
        found = self._of_types.get(doc_types)
        if found is None:
            found = self._of_types[doc_types] = frozenset(
                doc.doc_id for doc in self.docs.values() if doc.doc_type in doc_types
            )
        return found

    def file(self, doc: Doc) -> SourceFile:
        """The document's source, read on first use and kept. Reading and parsing happen outside
        the lock, so one request's first touch of a large guide does not hold up the others; two
        first touches of the same file may both parse it, and the first one stored wins."""
        loaded = self._files.get(doc.doc_key)
        if loaded is not None:
            return loaded
        made = SourceFile(doc.path, doc.doc_type)
        with self._lock:
            return self._files.setdefault(doc.doc_key, made)

    def preload(self, *, stop: threading.Event | None = None) -> int:
        """Read every document's source now (a background thread at service start), so no
        search pays for a first touch. Returns the number of files loaded; a file that cannot be
        read is left for its first use to report."""
        loaded = 0
        for doc in list(self.docs.values()):
            if stop is not None and stop.is_set():
                break
            if doc.doc_key in self._files:
                continue
            try:
                self.file(doc)
                loaded += 1
            except OSError:
                continue
        return loaded

    # --- sections -------------------------------------------------------------------------

    def resolve(self, ref: str) -> tuple[Doc, Sec] | None:
        """A section id ("rfc3101#3.2"; "section-3.2" and case are tolerated) or a chunk id."""
        ref = ref.strip()
        if ref.startswith("chk_"):
            with connect(self.paths.sqlite / "rag.db") as conn:
                row = conn.execute(
                    "SELECT section_id FROM chunk_spans WHERE chunk_id = ?", (ref,)
                ).fetchone()
            ref = row["section_id"] if row and row["section_id"] else ""
        for candidate in ref_candidates(ref):
            sec = self.sections.get(candidate)
            if sec:
                return self.docs[sec.doc_key], sec
        return None

    def heading_index(self) -> tuple[dict[str, list[tuple[Doc, Sec]]], list[tuple]]:
        """The sections headed by an identifier, made on first use: {named_identifier key: [(doc, section)]}, and
        (method, path segments, doc, section) for each heading "METHOD /path ...". In doc_key order, then
        document order; only such headings are kept (references to the sections already loaded)."""
        index = self._headings
        if index is None:
            names: dict[str, list[tuple[Doc, Sec]]] = {}
            operations: list[tuple[str, tuple[str, ...], Doc, Sec]] = []
            for doc in self.docs.values():
                for sec in doc.sections:
                    if not sec.heading:
                        continue
                    key = named_identifier(sec.heading)
                    if key is not None:
                        names.setdefault(key, []).append((doc, sec))
                        continue
                    m = API_HEADING.fullmatch(sec.heading)
                    if m:
                        operations.append((m.group(1).upper(), path_segments(m.group(2)), doc, sec))
            index = self._headings = (names, operations)
        return index

    def headed(self, identifier: str, limit: int = HEADED_MAX) -> list[tuple[Doc, Sec]]:
        """The sections whose heading is the identifier (api_v2.lookup's fallback), at most `limit`, by doc_key and
        then document order: a PowerShell cmdlet name or about topic, the heading equal to it in any case; an HTTP
        API operation "METHOD /path", the headings of that method whose path template it fits (segments_match);
        a bare "/path", those of every method. [] for any other identifier."""
        text = identifier.strip()
        names, operations = self.heading_index()
        key = named_identifier(text)
        if key is not None:
            found = names.get(key, [])
        else:
            m = API_OPERATION.fullmatch(text)
            if m is None and not API_PATH.fullmatch(text):
                return []
            method, asked = (m.group(1).upper(), path_segments(m.group(2))) if m else (None, path_segments(text))
            found = [
                (doc, sec) for op, segments, doc, sec in operations
                if (method is None or op == method) and segments_match(asked, segments)
            ]
        return sorted(found, key=lambda f: (f[0].doc_key, f[1].ordinal))[:limit]

    def chunk_lines(self, chunk_id: str) -> tuple[str, int | None, int | None] | None:
        """(doc_id, first line, last line) of a chunk in its source, the lines None when the chunk was not located;
        None for an unknown chunk id."""
        with connect(self.paths.sqlite / "rag.db") as conn:
            row = conn.execute(
                "SELECT doc_id, line_start, line_end FROM chunk_spans WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
        return (row["doc_id"], row["line_start"], row["line_end"]) if row else None

    def line_offset(self, doc: Doc, sec: Sec, line: int) -> int:
        """The character offset in the section's display text (section_text) at which the first display line at
        or after source line `line` begins; when no display line follows it (blank lines or page furniture to
        the section's end), the one where the last display line begins."""
        offset = begins = 0
        for at, text in self.file(doc).shown(sec.line_start, sec.line_end):
            begins = offset
            if at >= line:
                return offset
            offset += len(text) + 1
        return begins

    def heading_count(self, doc: Doc, heading: str | None) -> int:
        """How many sections of the document carry this heading (heading_key), counted once per document."""
        counts = self._heading_counts.get(doc.doc_key)
        if counts is None:
            counts = {}
            for s in doc.sections:
                if s.heading:
                    key = heading_key(s.heading)
                    counts[key] = counts.get(key, 0) + 1
            self._heading_counts[doc.doc_key] = counts
        return counts.get(heading_key(heading), 0)

    def same_section_in(self, doc: Doc, sec: Sec) -> list[dict[str, Any]]:
        """The same section in the same book at other releases: [{"doc_key", "section_id", "release"}], oldest
        release first (kb.version_key order, then doc_key). A document of the same book family (book_family) and
        another release that has a section with the same anchor and the same heading; when that heading occurs
        more than once in either document ("Examples", "Restrictions": their anchors end in -2, -3 in document
        order, which another release may order differently), the headings above the two sections, below each
        book's top heading, must be equal too. [] when the family cannot be decided, for the preamble, and for
        a document without siblings."""
        family = book_family(doc)
        if family is None or sec.heading is None:
            return []
        if self._families is None:
            families: dict[tuple[str, str], list[Doc]] = {}
            for other in self.docs.values():
                key = book_family(other)
                if key is not None:
                    families.setdefault(key, []).append(other)
            self._families = families
        found = []
        for other in self._families.get(family, ()):
            if other is doc or other.release == doc.release:
                continue
            twin = self.sections.get(f"{other.doc_key}#{sec.anchor}")
            if twin is None or heading_key(twin.heading) != heading_key(sec.heading):
                continue
            once = self.heading_count(doc, sec.heading) == 1 and self.heading_count(other, twin.heading) == 1
            if not once and [heading_key(h) for h in twin.path[1:]] != [heading_key(h) for h in sec.path[1:]]:
                continue
            found.append((version_key(other.release) or (), other.doc_key, twin.section_id, other.release))
        return [{"doc_key": key, "section_id": section_id, "release": release}
                for _, key, section_id, release in sorted(found)]

    def neighbours(self, doc: Doc, sec: Sec) -> tuple[Sec | None, Sec | None]:
        """(previous, next) section outside `sec`'s own subtree, in document order."""
        prev = next(
            (s for s in reversed(doc.sections[:sec.ordinal]) if s.line_end < sec.line_start),
            None,
        )
        nxt = next((s for s in doc.sections[sec.ordinal + 1:] if s.line_start > sec.line_end), None)
        return prev, nxt

    def section_text(
        self,
        doc: Doc,
        sec: Sec,
        offset: int,
        max_chars: int,
    ) -> dict[str, Any]:
        """A page of the section's display text (subsections included)."""
        pairs = self.file(doc).shown(sec.line_start, sec.line_end)
        text = "\n".join(t for _, t in pairs)
        page = text[offset:offset + max_chars]
        end = offset + len(page)
        # lines covered by this page, from the cumulative line offsets
        ends = []
        total = 0
        for line, t in pairs:
            total += len(t) + 1
            ends.append(total)
        first = pairs[min(len(pairs) - 1, bisect.bisect_right(ends, offset))][0] if pairs else sec.line_start
        last = pairs[min(len(pairs) - 1, bisect.bisect_left(ends, end))][0] if pairs else sec.line_end
        return {
            "text": page,
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "total_chars": len(text),
            "local": doc.local(first, max(first, last)),
        }

    # --- evidence windows -----------------------------------------------------------------

    def evidence(
        self,
        doc: Doc,
        span: tuple[int, int],
        terms: list[str],
        max_chars: int,
    ) -> dict[str, Any]:
        """Window of at most max_chars of the source around the passage of the chunk span that
        best matches the query terms; it may grow into the rest of the passage's section."""
        source = self.file(doc)
        pairs = source.shown(*span)
        best = best_line(pairs, terms)
        centre_line = pairs[best][0] if best is not None else span[0]
        sec = doc.section_at(centre_line)
        # a window of max_chars never reaches further than max_chars lines; with no query
        # term in the chunk (a dense-only match) it starts where the chunk starts
        lo = span[0] if best is None else max(
            centre_line - max_chars, min(span[0], sec.line_start if sec else span[0])
        )
        hi = min(centre_line + max_chars, max(span[1], sec.line_end if sec else span[1]))
        near = near_window(source, lo, hi, centre_line, max_chars, terms)
        if near is not None:
            text, first, last = near
            return {"text": text, "section": sec, "local": doc.local(first, last)}
        around = source.shown(lo, hi)
        if not around:
            return {"text": "", "section": sec, "local": doc.local(*span)}
        centre = next((i for i, (line, _) in enumerate(around) if line >= centre_line), 0)
        text, first, last = window(around, centre, max_chars, terms)
        return {"text": text, "section": sec, "local": doc.local(first, last)}

    # --- verify_quote ---------------------------------------------------------------------

    def verify_quote(
        self,
        doc: Doc,
        sec: Sec | None,
        quote: str,
        *,
        ellipsis: str = VERIFY_ELLIPSIS[0],
        max_gap_chars: int = DEFAULT_MAX_GAP_CHARS,
    ) -> dict[str, Any]:
        """Find `quote` in the section, else anywhere in the document.

        exact       the quote occurs verbatim in the source
        normalized  it occurs once typographic quotes/dashes, whitespace, line wrapping,
                    hyphenation breaks and RFC page headers/footers are disregarded; "..."
                    marks an omission (the parts must occur in order)

        `ellipsis` (retrieval.json verify.ellipsis) is the rule for a quotation with omissions. "any" (the
        default): the parts only have to occur in order, at any distance (in_order). "bounded": they must also
        lie in one section, each at most `max_gap_chars` canonical characters after the part before it
        (bounded_spans); a quotation whose parts do occur in order but in no such way is not_found with
        "reason": "parts-too-far" and the widest gap of the in-order places as "gap_chars". A quotation
        without omissions is checked the same under both.

        Every answer also says where the match lies: "scope" (only when a section was cited: "section" when the
        matched lines are inside it, "document" when the quotation was found elsewhere in the document),
        "sections" (the ids of the sections the matched lines are in, in document order; [] for not_found) and
        "gap_chars" (the most canonical characters, that is characters other than whitespace and hyphens,
        outside RFC page headers and footers, between two neighbouring parts; 0 without an omission).
        """
        not_found = {"result": "not_found", "section_id": sec.section_id if sec else None,
                     "local": None, "match_text": None, "sections": [], "gap_chars": 0}
        needle = quote.strip()
        if not needle:
            return not_found            # "" occurs everywhere: nothing is verified
        source = self.file(doc)
        scopes = [(sec.line_start, sec.line_end)] if sec else []
        scopes.append((1, source.n_lines))

        for first, last in scopes:
            start, end = source.starts[first - 1], (
                source.starts[last] if last < source.n_lines else len(source.text)
            )
            pos = source.text.find(needle, start, end)
            if pos >= 0:
                a, b = source.line_at(pos), source.line_at(pos + len(needle) - 1)
                return self._quote_result("exact", doc, sec, a, b, needle)

        parts = [canonical(p) for p in ELLIPSIS.split(quote.translate(QUOTE_CHARS))]
        parts = [p for p in parts if p]
        bounded = ellipsis == VERIFY_ELLIPSIS[1] and len(parts) > 1
        far: int | None = None      # bounded: the widest gap where the parts stand in order, but not near enough
        if parts:
            for first, last in scopes:
                text, ends, lines = self._canonical_text(source, first, last)
                spans = in_order(text, parts)
                if spans is None:
                    continue
                if bounded:
                    near = bounded_spans(
                        text, parts, max_gap_chars,
                        lambda at: doc.section_at(lines[bisect.bisect_right(ends, at)]),
                    )
                    if near is None:
                        far = widest_gap(spans) if far is None else far
                        continue            # the cited section does not hold it so: the whole document may
                    spans = near
                a = lines[bisect.bisect_right(ends, spans[0][0])]
                b = lines[bisect.bisect_left(ends, spans[-1][1])]
                shown = "\n".join(t for _, t in source.shown(a, b))
                return self._quote_result("normalized", doc, sec, a, b, shown, widest_gap(spans))
        if far is not None:
            return {**not_found, "gap_chars": far, "reason": "parts-too-far"}
        return not_found

    def _canonical_text(
        self,
        source: SourceFile,
        first: int,
        last: int,
    ) -> tuple[str, list[int], list[int]]:
        """Lines first..last in the quote comparison form, joined (page furniture and lines that are empty in
        that form left out), with, per kept line, the offset its text ends at and its line number."""
        pieces: list[str] = []
        ends: list[int] = []
        lines: list[int] = []
        total = 0
        for n in range(first, last + 1):
            if n - 1 in source.furniture:
                continue
            piece = canonical(source.line(n))
            if not piece:
                continue
            pieces.append(piece)
            total += len(piece)
            ends.append(total)
            lines.append(n)
        return "".join(pieces), ends, lines

    def _quote_result(
        self,
        result: str,
        doc: Doc,
        cited: Sec | None,
        a: int,
        b: int,
        text: str,
        gap: int = 0,
    ) -> dict[str, Any]:
        """A match on lines a..b. `cited`: the section the caller named, if any."""
        sec = doc.section_at(a)
        # every section a line of a..b is in: the one that holds line a, then each that starts up to line b
        spanned = ([sec] if sec else []) + [
            s for s in doc.sections[bisect.bisect_right(doc.starts, a):] if s.line_start <= b
        ]
        return {
            "result": result,
            "section_id": sec.section_id if sec else None,
            "local": doc.local(a, b),
            "match_text": text[:2000],
            **({"scope": "section" if cited.line_start <= a and b <= cited.line_end else "document"}
               if cited is not None else {}),
            "sections": [s.section_id for s in spanned],
            "gap_chars": gap,
        }

    # --- grep -----------------------------------------------------------------------------

    def scan_order(
        self,
        pattern: str,
        docs: list[Doc],
        seconds: float = SCAN_ORDER_SECONDS,
    ) -> list[Doc]:
        """`docs` in the order a literal grep visits them: first the documents with a chunk where the
        pattern's words stand together (an FTS5 phrase query on the BM25 index: the words in order,
        the last one a prefix when the pattern ends inside a word), then those with a chunk that holds
        every word, then the rest; each group in the order given.

        A scan of the whole corpus in doc_key order can spend the grep budget on a busy CPU before it
        reaches the late keys: an unscoped grep for "RADIUS DTLS server", which ise31-compat and
        ise31-admin-aaa-trustsec hold, answered no matches, truncated. Words are FTS5's (runs of
        letters and digits, any case); a match that starts inside a word ("TLS server" in "DTLS
        server") is in a later group. A pattern without words keeps the order, and so does a root
        whose index cannot answer.

        The queries get `seconds` in all: SQLite interrupts one that runs longer, and the groups of
        the queries done by then stand (none: the order given). Such a phrase is made of common
        words, which a scan in the order given meets early."""
        words = FTS_WORD.findall(pattern.lower())
        if not words or len(docs) < 2:
            return docs
        prefix = " *" if pattern[-1].isalnum() else ""
        queries = ['"' + " ".join(words) + '"' + prefix]
        if len(words) > 1:
            queries.append(" AND ".join(f'"{w}"' for w in words) + prefix)
        end = time.monotonic() + seconds
        group: dict[str, int] = {}
        try:
            with contextlib.closing(connect(self.paths.sqlite / "rag.db")) as conn:
                conn.set_progress_handler(lambda: time.monotonic() > end, PROGRESS_STEPS)
                tables = fts_tables(conn, docs)
                for n, query in enumerate(queries):
                    for table in tables:
                        found = docs_matching(conn, query) if table == DEFAULT_FTS else \
                            docs_matching(conn, query, table)
                        for doc_id in found:
                            group.setdefault(doc_id, n)
        except sqlite3.Error:
            pass                        # interrupted, or no index: the groups found stand
        if not group:
            return docs
        return sorted(docs, key=lambda doc: group.get(doc.doc_id, len(queries)))

    def scan_may_run_out(self, docs: list[Doc], seconds: float) -> bool:
        """Whether a literal scan of `docs` may not reach the last of them in `seconds`: their texts
        hold more than seconds x SCAN_CHARS_PER_SECOND characters (a file read counts its text,
        another its size on disk, one that cannot be read nothing)."""
        limit = seconds * SCAN_CHARS_PER_SECOND
        total = 0
        for doc in docs:
            loaded = self._files.get(doc.doc_key)
            if loaded is not None:
                total += len(loaded.text)
            else:
                with contextlib.suppress(OSError):
                    total += doc.path.stat().st_size
            if total > limit:
                return True
        return False

    def grep(
        self,
        pattern: str,
        *,
        regex: bool,
        docs: list[Doc],
        max_matches: int,
        ignore_case: bool,
        budget_seconds: float,
        regex_engine: bool = False,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Matching lines, at most one per line and max_matches in all; (matches, truncated).

        A literal pattern is escaped (linear-time search). A regex is bounded when it compiles
        (compile_regex) and runs with a timeout taken from the remaining budget; running out of
        budget truncates. A literal scan that may not reach every document within the budget
        (scan_may_run_out: the whole corpus, not a few guides) visits first the documents whose BM25
        index holds its words (scan_order, which may spend SCAN_ORDER_SECONDS of the budget, at most
        a quarter of it); the matches come back in the order of `docs` either way.

        regex_engine (retrieval.json grep.regex_engine): the pattern runs on the regex module
        without holding the GIL (EngineScan), the same matches sooner; the order of the scan and
        the budget are the same."""
        if regex_engine:
            compiled = EngineScan(pattern, regex=regex, ignore_case=ignore_case)
        elif regex:
            compiled = compile_regex(pattern, ignore_case)
        else:
            compiled = re.compile(re.escape(pattern), re.IGNORECASE if ignore_case else 0)

        deadline = time.monotonic() + budget_seconds
        ordering = min(SCAN_ORDER_SECONDS, budget_seconds / 4)
        if regex or not self.scan_may_run_out(docs, budget_seconds):
            order = docs
        else:
            order = self.scan_order(pattern, docs, ordering)
        matches, truncated = self._scan(compiled, regex, order, max_matches, deadline)
        if order is not docs:
            rank = {doc.doc_key: n for n, doc in enumerate(docs)}
            matches.sort(key=lambda m: rank[m["doc_key"]])      # stable: each document's lines in order
        return matches, truncated

    def _scan(
        self,
        compiled,
        regex: bool,
        docs: list[Doc],
        max_matches: int,
        deadline: float,
    ) -> tuple[list[dict[str, Any]], bool]:
        matches: list[dict[str, Any]] = []
        for doc in docs:
            source = self.file(doc)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return matches, True
            if isinstance(compiled, EngineScan):
                found = compiled.finditer(source, remaining)
            else:
                found = compiled.finditer(source.text, timeout=remaining) if regex else \
                    compiled.finditer(source.text)
            seen: set[int] = set()
            try:
                for m in found:
                    line = source.line_at(m.start())
                    if line in seen or line - 1 in source.furniture:
                        continue
                    seen.add(line)
                    if len(matches) == max_matches:
                        return matches, True
                    sec = doc.section_at(line)
                    text = source.line(line).strip()
                    if len(text) > GREP_LINE_CHARS:
                        text = cut_line(text, [m.group(0).lower()], GREP_LINE_CHARS)
                    matches.append({
                        "doc_key": doc.doc_key,
                        "section_id": sec.section_id if sec else None,
                        "line": line,
                        "text": text,
                    })
                    if time.monotonic() > deadline:
                        return matches, True
            except TimeoutError:
                return matches, True
        return matches, False


def near_window(
    source: SourceFile,
    lo: int,
    hi: int,
    centre_line: int,
    max_chars: int,
    terms: list[str],
) -> tuple[str, int, int] | None:
    """What window(source.shown(lo, hi), <first pair at or after centre_line>, ...) returns,
    from display lines near centre_line only; None when that cannot be shown to be the same
    (the caller then prepares the whole range).

    A window of max_chars reaches at most max_chars lines to each side, so evidence() prepared
    every line of lo..hi (up to ~4,800 display lines for a long section) to keep a few dozen.
    Display lines are the same in a shorter range from its first non-blank line on (only a
    leading blank run is dropped) up to its end, so the window is the same when the range
    [plo, phi] around centre_line holds the centre pair and the growth never reached a cut
    (plo > lo or phi < hi). Otherwise the range widens x4, up to lo..hi."""
    lo, hi = max(lo, 1), min(hi, source.n_lines)       # as shown() clamps them
    reach = NEAR_LINES
    while centre_line - reach > lo or centre_line + reach < hi:
        plo, phi = max(lo, centre_line - reach), min(hi, centre_line + reach)
        near = source.shown(plo, phi)
        if near and near[0][0] <= centre_line <= near[-1][0]:
            centre = next(i for i, (line, _) in enumerate(near) if line >= centre_line)
            text = near[centre][1]
            if len(text) > max_chars:
                return cut_line(text, terms, max_chars), near[centre][0], near[centre][0]
            a, b, at_start, at_end = grow(near, centre, max_chars)
            if not (at_start and plo > lo) and not (at_end and phi < hi):
                return body_of(near, a, b)
        reach *= 4
    return None


def chunk_rows(conn, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Chunk text and heading path, with the source span when the root has one."""
    if not chunk_ids:
        return {}
    marks = ",".join("?" for _ in chunk_ids)
    spans = has_structure(conn)
    rows = conn.execute(
        f"""
        SELECT c.chunk_id, c.doc_id, c.text, c.heading_path_json
               {", s.line_start, s.line_end, s.section_id AS span_section" if spans else ""}
        FROM chunks c
        {"LEFT JOIN chunk_spans s ON s.chunk_id = c.chunk_id" if spans else ""}
        WHERE c.chunk_id IN ({marks})
        """,
        chunk_ids,
    ).fetchall()
    out = {}
    for r in rows:
        row = dict(r)
        row["heading_path"] = json.loads(row.pop("heading_path_json") or "[]")
        out[row["chunk_id"]] = row
    return out
