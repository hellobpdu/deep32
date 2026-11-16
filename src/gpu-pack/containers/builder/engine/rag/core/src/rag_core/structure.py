"""Source structure index: each document's source file, its sections and each chunk's line span.

A root keeps its source texts under $RAG_ROOT/sources/<doc_type>/<doc_key>.<ext> (the corpus
package layout; sources/manifest.json is read when present). index_root() parses every source
(rag_core.sections), stores the sections with their line ranges, and locates every chunk of the
document in its source text. That repairs the chunk-to-section link, which the v3 chunker loses
when it merges small sections under a common parent heading.

Tables (added to rag.db; nothing existing is altered, so an older root keeps working and a root
indexed here still serves the old /search unchanged):

    doc_sources     one row per document with a source file
        doc_id PK -> documents, doc_key UNIQUE (the source file stem), doc_type ("rfc", "cisco",
        or another type of the corpus package), rel_path (under sources/), sha256 and n_lines of
        the file when indexed, title, release (the manifest's, else inferred from a Cisco doc_key),
        platform, source_url, structure_version, indexed_at
    doc_sections    one row per section, document order
        section_id PK "<doc_key>#<anchor>" (rag_core.sections), doc_id, ordinal (0-based),
        level (0 = preamble, 1 = top level), anchor, heading (as printed), parent_section_id,
        line_start (the heading line), line_end (last line, subsections included),
        n_chars (length of the section's display text, page furniture removed)
    chunk_spans     one row per chunk of an indexed document
        chunk_id PK -> chunks (ON DELETE CASCADE: re-chunking drops the spans, and the next
        index_root() run re-locates the chunks), doc_id, line_start / line_end (NULL when the
        chunk text was not found in the source; a table context the chunker repeated at the
        chunk's start is left out, located_text), section_id (the section holding most of the
        chunk's lines), covered_json (every section the span touches, in document order)

Line numbers are 1-based lines of the source file as stored (see rag_core.sections).
"""
from __future__ import annotations

import bisect
import json
import logging
import re
from array import array
from pathlib import Path
from typing import Any, Callable

from rag_core.db import connect
from rag_core.identity import sha256_file
from rag_core.paths import RAGPaths
from rag_core.sections import (
    Section,
    page_furniture,
    parse_sections,
    rfc_title,
)


# p1-v2: the RFC parser takes subsections indented like the body and appendix subsections, and
# no longer reads a numbered list as sections (rag_core.sections); index_root re-indexes p1-v1
STRUCTURE_VERSION = "p1-v2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_sources (
    doc_id TEXT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    doc_key TEXT NOT NULL UNIQUE,
    doc_type TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    n_lines INTEGER NOT NULL,
    title TEXT,
    release TEXT,
    platform TEXT,
    source_url TEXT,
    structure_version TEXT NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS doc_sections (
    section_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    level INTEGER NOT NULL,
    anchor TEXT NOT NULL,
    heading TEXT,
    parent_section_id TEXT,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    n_chars INTEGER NOT NULL,
    UNIQUE (doc_id, ordinal)
);

CREATE TABLE IF NOT EXISTS chunk_spans (
    chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    section_id TEXT,
    covered_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_chunk_spans_doc ON chunk_spans(doc_id);
"""

SOURCE_EXTENSIONS = (".txt", ".md")
HEADING_MARK = re.compile(r"#{1,6}")
KGRAM = 8               # words looked up to place a chunk in its source
TRIES = 12              # k-grams tried from each end of a chunk
OCCURRENCES = 32        # occurrences of one k-gram compared
RUN_CAP = 64            # a match running this many words is taken as the right place
LOCAL_WINDOW = 400_000  # chars searched forward from the previous chunk before searching everywhere
# a chunk's end is placed at most this many chunk lengths (in words) past its first word. Its text skips heading-
# only lines, fences and furniture of its stretch, never most of it: placed spans hold at most 5.5 source words
# per chunk word, except gross misplacements at 9 to 790 (corpus-v8.1 and corpus-v9 roots, 2026-11-01)
SPAN_SLACK = 8

logger = logging.getLogger(__name__)


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def has_structure(conn) -> bool:
    """The root has a structure index: the tables exist and hold at least one document. Empty
    tables (index_root over a root without source files) are no index; readers would answer
    with confident empty results."""
    tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('doc_sources', 'doc_sections', 'chunk_spans')"
    ).fetchone()[0]
    return tables == 3 and conn.execute("SELECT 1 FROM doc_sources LIMIT 1").fetchone() is not None


def corpus_entry(row: dict[str, Any]) -> dict[str, Any]:
    """The corpus manifest entry a root build stores under metadata_json["corpus"], if any."""
    try:
        entry = json.loads(row.get("metadata_json") or "{}").get("corpus")
    except (ValueError, AttributeError):
        entry = None
    return entry if isinstance(entry, dict) else {}


def doc_key_for(row: dict[str, Any]) -> str:
    """The manifest doc_key, else the original file's stem: "rfc3101" for rfc3101.txt,
    "c9300-179-rtng" for c9300-179-rtng.pdf."""
    return str(corpus_entry(row).get("doc_key") or Path(row["original_filename"]).stem)


def infer_release(doc_key: str) -> str | None:
    """Book release from a Cisco doc_key, as the KB builders infer it: c9300-1718-* -> 17.18,
    c9k-179-* -> 17.9, catc237-* -> 2.3.7. Rolling books (iosxe17-*) name no release.
    index_document asks only for doc_type "cisco": the keys of other document types carry years
    and versions that are no Cisco release (mn-175-osd would read 17.5, a 2019 edition 20.19)."""
    m = re.match(r"(?:catc|dnac)-?(\d{3,4})\b", doc_key)
    if m:
        return ".".join(m.group(1))
    for token in doc_key.split("-"):
        m = re.fullmatch(r"(1[5-9]|2\d)(\d{1,2})", token)
        if m:
            return f"{m.group(1)}.{m.group(2)}"
    return None


def display_lines(lines: list[str], furniture: set[int]) -> list[tuple[int, str]]:
    """(1-based line, text) of what a reader is shown: RFC page headers and footers dropped,
    form feeds removed, runs of blank lines collapsed to one."""
    out: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if i in furniture:
            continue
        text = line.replace("\f", "").rstrip()
        if not text and (not out or not out[-1][1]):
            continue
        out.append((i + 1, text))
    return out


def display_chars(pairs: list[tuple[int, str]]) -> int:
    return sum(len(text) + 1 for _, text in pairs) - (1 if pairs else 0)


class SourceText:
    """The source as one space-joined word string, to place chunk text in it by exact k-grams.

    Page furniture, ``` fence lines and bare heading marks are left out: the ingest adapter
    drops the first two, and chunk text writes absorbed headings with its own "#" count.
    """

    def __init__(self, lines: list[str], furniture: set[int]):
        self.words: list[str] = []
        self.line_of = array("I")
        self.offsets = array("I")
        position = 0
        for i, line in enumerate(lines):
            if i in furniture or line.startswith("```"):
                continue
            for word in line.split():
                if HEADING_MARK.fullmatch(word):
                    continue
                self.words.append(word)
                self.line_of.append(i + 1)
                self.offsets.append(position)
                position += len(word) + 1
        self.text = " ".join(self.words)

    def occurrences(self, gram: list[str], start: int, end: int | None = None) -> list[int]:
        """Word indexes (at most OCCURRENCES) where `gram` occurs as whole words, searching
        char offsets [start, end)."""
        needle = " ".join(gram)
        limit = len(self.text) if end is None else min(end, len(self.text))
        found: list[int] = []
        while len(found) < OCCURRENCES:
            pos = self.text.find(needle, start, limit)
            if pos < 0:
                break
            after = pos + len(needle)
            if (pos == 0 or self.text[pos - 1] == " ") and (
                after == len(self.text) or self.text[after] == " "
            ):
                found.append(bisect.bisect_right(self.offsets, pos) - 1)
            start = pos + 1
        return found

    def run(self, at: int, words: list[str], j: int, step: int) -> int:
        """Number of consecutive equal words from source word `at` and chunk word `j`,
        walking forward (step 1) or backward (step -1); capped at RUN_CAP."""
        n = 0
        while (
            n < RUN_CAP
            and 0 <= at < len(self.words)
            and 0 <= j < len(words)
            and self.words[at] == words[j]
        ):
            at, j, n = at + step, j + step, n + 1
        return n

    def first_word(self, words: list[str], cursor: int) -> int | None:
        """Source word index where `words` begins: the first occurrence, forward of char offset
        `cursor` (else anywhere), of an opening k-gram whose match keeps running for RUN_CAP
        words (or to the chunk's end); failing that, the longest-running one. Short runs repeat:
        packet-diagram bit rulers, boilerplate sentences."""
        for start, end, grams in ((cursor, cursor + LOCAL_WINDOW, TRIES), (0, None, 3)):
            best: tuple[int, int] | None = None
            for j in range(min(len(words), grams)):
                full = min(len(words) - j, RUN_CAP)
                for at in self.occurrences(words[j:j + KGRAM], start, end):
                    run = self.run(at, words, j, 1)
                    if run >= full:
                        return at
                    if best is None or run > best[0]:
                        best = (run, at)
            if best:
                return best[1]
        return None

    def full_match(self, words: list[str], at: int | None) -> bool:
        """Whether `at` (first_word's answer) is a match that runs for RUN_CAP words or to the chunk's end from
        one of its opening k-grams, not only the longest of shorter runs."""
        return at is not None and any(
            self.run(at, words, j, 1) >= min(len(words) - j, RUN_CAP)
            for j in range(min(len(words), TRIES))
        )

    def last_word(self, words: list[str], first: int) -> int | None:
        """Source word index where `words`, placed at `first`, ends: of the occurrences of a
        closing k-gram whose match runs back at least 2k words, the nearest to the expected
        end (chapter boilerplate and repeated sentences occur many times); failing that, the
        longest-running one. Either at most SPAN_SLACK chunk lengths past `first`: a chunk
        closed by a short boilerplate passage (chunker v3.3: a cmdlet's RELATED LINKS list
        after heading-only OUTPUTS lines the chunk does not print) matched it at the
        document's last cmdlet, 1,109 lines on (corpus-v9 pwsh-win-scheduledtasks)."""
        expected = first + len(words) - 1
        limit = first + SPAN_SLACK * len(words)
        fallback: tuple[int, int] | None = None
        for j in range(len(words) - 1, max(-1, len(words) - 1 - TRIES), -1):
            gram = words[max(0, j - KGRAM + 1):j + 1]
            good: list[int] = []
            for at in self.occurrences(gram, self.offsets[first]):
                end = at + len(gram) - 1
                if end > limit:
                    continue
                run = self.run(end, words, j, -1)
                if run >= min(j + 1, 2 * KGRAM):
                    good.append(end)
                elif fallback is None or run > fallback[0]:
                    fallback = (run, end)
            if good:
                return min(good, key=lambda e: abs(e - expected))
        return fallback[1] if fallback else None


LEADING_HEADINGS = re.compile(r"\A(?:#{1,6} [^\n]*\n+)+")    # the heading lines a chunk text opens with


def chunk_words(text: str) -> list[str]:
    return [w for w in text.split() if not HEADING_MARK.fullmatch(w)]


def located_text(text: str, metadata_json: str | None) -> str:
    """The part of a chunk's text that stands where the chunk is in its source: without the
    context the chunker put before it (metadata "context_chars", chunking.table_header_repeat: a
    table's caption line and header row at the start of a continuation chunk). Placed with them,
    the chunk could start where the table starts, above the rows of the chunk before it."""
    try:
        skip = json.loads(metadata_json or "{}").get("context_chars")
    except (ValueError, AttributeError):
        skip = None
    if isinstance(skip, int) and not isinstance(skip, bool) and 0 < skip < len(text):
        return text[skip:]
    return text


def locate_chunks(
    lines: list[str],
    furniture: set[int],
    texts: list[str],
) -> list[tuple[int, int] | None]:
    """(first line, last line) of each chunk text in the source, in chunk order; None if absent.

    Chunks are placed in order: each search starts at the previous chunk's start (a chunk may
    begin with the tail of its predecessor) and falls back to the whole document.
    """
    source = SourceText(lines, furniture)
    spans: list[tuple[int, int] | None] = []
    cursor = 0
    for text in texts:
        words = chunk_words(text)
        first = source.first_word(words, cursor) if words and source.text else None
        lead = LEADING_HEADINGS.match(text) if source.text else None
        if lead and not source.full_match(words, first):
            # chunker v3.2 opens a merged chunk with its own titles, which may stand far from the text after them
            # in the source. Unless the chunk matched in full (titles right above its text), its title words were
            # all it matched by, at a TOC line, a parent heading far above or a like-named section (corpus-v8.1,
            # v3.2: 86 spans over 1,000 lines against 1), or by nothing (12 words or more, TRIES). Placed by the
            # text after them, from the title words that stand right above it. A chunk that matched in full is
            # placed as before; on the v3.1 chunks of corpus-v8.1 no span changes.
            body = chunk_words(text[lead.end():])
            at = source.first_word(body, cursor) if body and len(body) < len(words) else None
            if at is not None:
                titles = len(words) - len(body)
                above = next(
                    (k for k in range(min(titles, at), 0, -1)
                     if source.words[at - k:at] == words[titles - k:titles]),
                    0,
                )
                words, first = words[titles - above:], at - above
        if first is None:
            spans.append(None)
            continue
        cursor = source.offsets[first]
        end = source.last_word(words, first)
        if end is None:
            end = min(len(source.line_of) - 1, first + len(words) - 1)
        spans.append((source.line_of[first], source.line_of[max(first, end)]))
    return spans


def span_sections(
    sections: list[Section],
    span: tuple[int, int],
) -> tuple[int, list[int]]:
    """(index of the section holding most of the span's lines, indexes of all sections touched)."""
    first, last = span
    starts = [s.line_start for s in sections]
    touched = [
        i for i, s in enumerate(sections)
        if first < s.line_start <= last
    ]
    holder = bisect.bisect_right(starts, first) - 1
    if holder >= 0:
        touched.insert(0, holder)
    if not touched:
        return 0, []

    def own_overlap(i: int) -> int:
        own_end = starts[i + 1] - 1 if i + 1 < len(starts) else last
        return max(0, min(own_end, last) - max(starts[i], first) + 1)

    best = max(touched, key=lambda i: (own_overlap(i), -i))
    return best, touched


def index_document(
    conn,
    doc: dict[str, Any],
    source: Path,
    *,
    rel_path: str,
    doc_key: str,
    doc_type: str,
    meta: dict[str, Any],
) -> dict[str, int]:
    """(Re)write doc_sources, doc_sections and chunk_spans for one document."""
    text = source.read_bytes().decode("utf-8", errors="replace")
    lines, sections = parse_sections(text, doc_type)
    furniture = page_furniture(lines) if doc_type == "rfc" else set()
    shown = display_lines(lines, furniture)
    shown_lines = [line for line, _ in shown]
    ids = [f"{doc_key}#{s.anchor}" for s in sections]

    chunks = conn.execute(
        "SELECT chunk_id, text, metadata_json FROM chunks WHERE doc_id = ? ORDER BY ordinal",
        (doc["doc_id"],),
    ).fetchall()
    spans = locate_chunks(
        lines, furniture, [located_text(c["text"], c["metadata_json"]) for c in chunks]
    )

    title = (
        meta.get("title")
        or doc.get("title")
        or (rfc_title(lines) if doc_type == "rfc" else None)
        or next((s.heading for s in sections if s.level == 1 and s.heading), None)
        or doc_key
    )
    # the manifest's release, else one inferred from a Cisco doc_key; other types never infer one
    release = meta.get("release") or (
        infer_release(doc_key) if doc_type == "cisco" else None
    )

    conn.execute("DELETE FROM doc_sections WHERE doc_id = ?", (doc["doc_id"],))
    conn.execute("DELETE FROM chunk_spans WHERE doc_id = ?", (doc["doc_id"],))
    conn.execute("DELETE FROM doc_sources WHERE doc_id = ?", (doc["doc_id"],))
    conn.execute(
        """
        INSERT INTO doc_sources (doc_id, doc_key, doc_type, rel_path, sha256, n_lines,
                                 title, release, platform, source_url, structure_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc["doc_id"], doc_key, doc_type, rel_path, sha256_file(source), len(lines),
            title, release, meta.get("platform"),
            meta.get("source_url") or doc.get("source_uri"), STRUCTURE_VERSION,
        ),
    )
    for ordinal, (sid, sec) in enumerate(zip(ids, sections)):
        lo = bisect.bisect_left(shown_lines, sec.line_start)
        hi = bisect.bisect_right(shown_lines, sec.line_end)
        conn.execute(
            """
            INSERT INTO doc_sections (section_id, doc_id, ordinal, level, anchor, heading,
                                      parent_section_id, line_start, line_end, n_chars)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid, doc["doc_id"], ordinal, sec.level, sec.anchor, sec.heading,
                ids[sec.parent] if sec.parent is not None else None,
                sec.line_start, sec.line_end, display_chars(shown[lo:hi]),
            ),
        )
    located = 0
    for chunk, span in zip(chunks, spans):
        best, touched = span_sections(sections, span) if span and sections else (0, [])
        located += span is not None
        conn.execute(
            """
            INSERT INTO chunk_spans (chunk_id, doc_id, line_start, line_end, section_id,
                                     covered_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                chunk["chunk_id"], doc["doc_id"],
                span[0] if span else None, span[1] if span else None,
                ids[best] if touched else None,
                json.dumps([ids[i] for i in touched]),
            ),
        )
    return {"sections": len(sections), "chunks": len(chunks), "located": located}


def load_manifest(sources: Path) -> dict[str, dict[str, Any]]:
    """doc_key -> manifest entry from sources/manifest.json (corpus package), if present."""
    path = sources / "manifest.json"
    if not path.is_file():
        return {}
    entries = json.loads(path.read_text(encoding="utf-8"))
    return {e["doc_key"]: e for e in entries if isinstance(e, dict) and e.get("doc_key")}


def find_source(
    sources: Path,
    doc_type: str,
    doc_key: str,
    entry: dict[str, Any],
) -> Path | None:
    candidates = [sources / entry["path"]] if entry.get("path") else []
    candidates += [sources / doc_type / f"{doc_key}{ext}" for ext in SOURCE_EXTENSIONS]
    return next((p for p in candidates if p.is_file()), None)


def index_root(
    paths: RAGPaths,
    *,
    sources: Path | None = None,
    force: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Index every document of the root that has a source file. Idempotent: a document whose
    source, structure version and chunk set are unchanged is skipped unless `force`."""
    sources = (sources or paths.root / "sources").resolve()
    manifest = load_manifest(sources)
    log = log or logger.info
    counts: dict[str, Any] = {
        "documents": 0, "indexed": 0, "unchanged": 0, "no_source": [], "duplicate_key": [],
        "sections": 0, "chunks": 0, "located": 0,
    }

    with connect(paths.sqlite / "rag.db") as conn:
        ensure_schema(conn)
        docs = [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY original_filename")]
        seen: set[str] = set()
        for n, doc in enumerate(docs, 1):
            counts["documents"] += 1
            doc_key = doc_key_for(doc)
            entry = manifest.get(doc_key) or corpus_entry(doc)
            doc_type = str(entry.get("doc_type") or doc.get("source_name") or "unknown")
            if doc_key in seen:
                counts["duplicate_key"].append(doc_key)
                continue
            seen.add(doc_key)
            source = find_source(sources, doc_type, doc_key, entry)
            if source is None:
                counts["no_source"].append(doc_key)
                continue
            if not force and _unchanged(conn, doc["doc_id"], source):
                counts["unchanged"] += 1
                continue
            conn.execute("BEGIN IMMEDIATE")
            result = index_document(
                conn, doc, source,
                rel_path=source.relative_to(sources).as_posix(),
                doc_key=doc_key, doc_type=doc_type, meta=entry,
            )
            conn.commit()
            counts["indexed"] += 1
            for key in ("sections", "chunks", "located"):
                counts[key] += result[key]
            if n % 25 == 0 or n == len(docs):
                log(f"structure: {n}/{len(docs)} documents, {counts['located']}/"
                    f"{counts['chunks']} chunks located")
    return counts


def _unchanged(conn, doc_id: str, source: Path) -> bool:
    row = conn.execute(
        "SELECT sha256, structure_version FROM doc_sources WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    if row is None or row["structure_version"] != STRUCTURE_VERSION:
        return False
    spans, chunks = conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM chunk_spans WHERE doc_id = ?),
               (SELECT COUNT(*) FROM chunks WHERE doc_id = ?)
        """,
        (doc_id, doc_id),
    ).fetchone()
    return spans == chunks and row["sha256"] == sha256_file(source)
