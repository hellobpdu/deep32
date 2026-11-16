from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from rag_core.config import load_config
from rag_core.db import connect
from rag_core.identity import chunk_id, sha256_text
from rag_core.paths import RAGPaths
from rag_core.canonical import derive_source_page_map


CHUNKER_VERSION = "17c-v2"

# Chunking policies:
#   v2 (default): every heading change and every atomic unit (table, code,
#       CLI/config) starts a new chunk.
#   v3 (context merge): atomic units are kept with the prose around them
#       (split at line boundaries only when larger than hard_tokens), sections
#       smaller than min_tokens are merged into neighbours under their common
#       parent heading, overlap-only drafts are never emitted, and no chunk
#       exceeds hard_tokens.
CHUNKER_VERSIONS = {"v2": CHUNKER_VERSION, "v3": "17c-v3.1"}


def chunker_policy(policy: str | None = None) -> str:
    policy = (policy or os.environ.get("RAG_CHUNKER_POLICY") or "v2").strip()
    if policy not in CHUNKER_VERSIONS:
        raise ValueError(f"unknown chunker policy {policy!r}")
    return policy


def chunking_settings(root: str | None = None) -> dict[str, Any]:
    """The "chunking" config (rag_core.config: config/chunking.json, the root's overlay, or the
    file RAG_CHUNKING_CONFIG names), checked: {"table_header_repeat": bool}, default false."""
    config = load_config("chunking", root=root, env_var="RAG_CHUNKING_CONFIG")
    value = config.get("table_header_repeat", False)
    if not isinstance(value, bool):
        raise ValueError(f"chunking.table_header_repeat must be true or false, not {value!r}")
    return {"table_header_repeat": value}


NOISE_TYPES = {
    "header",
    "footer",
    "page_header",
    "page_footer",
    "page_number",
}

HEADING_TYPES = {
    "doc_title",
    "paragraph_title",
    "section_title",
    "title",
    "heading",
}

ATOMIC_TYPES = {
    "table",
    "code",
    "code_block",
    "formula",
    "equation",
    "image",
    "figure",
}

FOOTNOTE_TYPES = {
    "page_footnote",
    "footnote",
}

TOKEN_RE = re.compile(
    r"[A-Za-z0-9_./:%-]+|[^\s]",
    re.UNICODE,
)

SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])"
)

CLI_PROMPT_RE = re.compile(
    r"(?m)^\s*"
    r"[A-Za-z0-9_.:/-]+"
    r"(?:\([^)]+\))?"
    r"[#>]\s+\S+"
)

CONFIG_LINE_RE = re.compile(
    r"(?mi)^\s*(?:"
    r"interface|"
    r"router\s+\S+|"
    r"address-family|"
    r"neighbor\s+\S+|"
    r"network\s+\S+|"
    r"ip\s+route|"
    r"ipv6\s+route|"
    r"route-map|"
    r"policy-map|"
    r"class-map|"
    r"access-list|"
    r"ip\s+prefix-list|"
    r"redistribute|"
    r"passive-interface|"
    r"default-information|"
    r"mpls\s+|"
    r"crypto\s+|"
    r"tunnel\s+|"
    r"vrf\s+definition|"
    r"router-id|"
    r"maximum-paths|"
    r"distance\s+"
    r")\b"
)


@dataclass(frozen=True)
class HeadingContext:
    level: int
    text: str
    page: int
    block_id: str
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class Unit:
    kind: str
    text: str
    page: int
    block_id: str
    heading_path: tuple[str, ...]
    heading_context: tuple[HeadingContext, ...] = ()
    bbox: list[float] | None = None
    atomic: bool = False
    source_type: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class Draft:
    heading_path: tuple[str, ...]
    units: list[Unit]
    atomic: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(
            u.text.strip()
            for u in self.units
            if u.text.strip()
        ).strip()

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def estimate_tokens(text: str) -> int:
    return len(TOKEN_RE.findall(text))


def _bbox(
    block: dict[str, Any],
) -> list[float] | None:
    value = block.get("bbox")

    if (
        isinstance(value, list)
        and len(value) == 4
        and all(
            isinstance(v, (int, float))
            for v in value
        )
    ):
        return [float(v) for v in value]

    return None


def content_to_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, (int, float, bool)):
        return str(value)

    if isinstance(value, list):
        parts = [
            content_to_text(item)
            for item in value
        ]

        return "\n".join(
            part
            for part in parts
            if part
        ).strip()

    if isinstance(value, dict):
        preferred = (
            "text",
            "content",
            "markdown",
            "caption",
            "latex",
            "html",
            "value",
        )

        parts: list[str] = []

        for key in preferred:
            if key not in value:
                continue

            text = content_to_text(
                value[key]
            )

            if text:
                parts.append(text)

        if parts:
            return "\n".join(
                dict.fromkeys(parts)
            ).strip()

        for key, child in value.items():
            if key in {
                "bbox",
                "type",
                "level",
                "id",
                "page_idx",
            }:
                continue

            text = content_to_text(child)

            if text:
                parts.append(text)

        return "\n".join(parts).strip()

    return ""


COPYRIGHT_TRAILER_RE = re.compile(
    r"""
    (?:
        \s+
        (?:
            ©
            |
            \(c\)
            |
            copyright
            |
            q(?=\s+(?:19|20)\d{2}\b)
        )?
        \s*
        (?:19|20)\d{2}
        \s+
        [^\n]{0,200}?
        (?:
            all\s+rights\s+reserved
            |
            rights\s+reserved
        )
        \.?
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def clean_retrieval_text(
    text: str,
) -> str:
    """
    Remove publisher/copyright boilerplate only when it occurs
    at the trailing edge of an otherwise useful MinerU block.

    The immutable MinerU artifacts remain untouched; this only
    affects normalized retrieval/chunk text.
    """
    text = text.strip()

    previous = None

    while text != previous:
        previous = text
        text = COPYRIGHT_TRAILER_RE.sub(
            "",
            text,
        ).rstrip()

    return text


def looks_like_cli_or_config(
    text: str,
) -> bool:
    if "```" in text:
        return True

    if CLI_PROMPT_RE.search(text):
        return True

    lines = [
        line
        for line in text.splitlines()
        if line.strip()
    ]

    if len(lines) < 2:
        return False

    hits = sum(
        1
        for line in lines
        if CONFIG_LINE_RE.search(line)
    )

    return (
        hits >= 2
        and hits / len(lines) >= 0.35
    )


def classify_block(
    block_type: str,
    text: str,
) -> tuple[str, bool]:
    block_type = block_type.lower()

    if block_type in FOOTNOTE_TYPES:
        return "footnote", True

    if block_type in ATOMIC_TYPES:
        return block_type, True

    if "table" in block_type:
        return "table", True

    if any(
        term in block_type
        for term in (
            "code",
            "formula",
            "equation",
        )
    ):
        return block_type, True

    if looks_like_cli_or_config(text):
        return "cli-config", True

    if block_type in {
        "list",
        "list_item",
    }:
        return "list", False

    return "prose", False


def heading_level(
    block: dict[str, Any],
    block_type: str,
) -> int:
    level = block.get("level")

    if (
        isinstance(level, int)
        and level > 0
    ):
        return min(level, 6)

    if block_type == "doc_title":
        return 1

    if block_type == "paragraph_title":
        return 2

    return 2


def _update_heading_stack(
    stack: list[HeadingContext],
    heading: HeadingContext,
) -> list[HeadingContext]:
    retained = [
        item
        for item in stack
        if item.level < heading.level
    ]

    retained.append(heading)

    return retained


def extract_units(
    structured: dict[str, Any],
    page_map: dict[int, int] | None = None,
) -> list[Unit]:
    units: list[Unit] = []
    heading_stack: list[
        HeadingContext
    ] = []

    for page in structured.get(
        "pages",
        [],
    ):
        page_idx = int(
            page.get("page_idx", 0)
        )

        page_number = (
            (page_map or {}).get(
                page_idx,
                page_idx + 1,
            )
        )

        for block_index, block in enumerate(
            page.get("blocks", [])
        ):
            if not isinstance(block, dict):
                continue

            block_type = str(
                block.get("type")
                or block.get("block_type")
                or block.get("kind")
                or "text"
            ).strip().lower()

            text = clean_retrieval_text(
                content_to_text(
                    block.get("content")
                )
            )

            block_id = (
                f"p{page_number:04d}"
                f"-b{block_index:04d}"
            )

            bbox = _bbox(block)

            if block_type in NOISE_TYPES:
                continue

            if block_type in HEADING_TYPES:
                if not text:
                    continue

                raw_bbox = (
                    tuple(bbox)
                    if bbox is not None
                    else None
                )

                heading_stack = (
                    _update_heading_stack(
                        heading_stack,
                        HeadingContext(
                            level=heading_level(
                                block,
                                block_type,
                            ),
                            text=text,
                            page=page_number,
                            block_id=block_id,
                            bbox=raw_bbox,
                        ),
                    )
                )

                continue

            if not text:
                continue

            kind, atomic = classify_block(
                block_type,
                text,
            )

            units.append(
                Unit(
                    kind=kind,
                    text=text,
                    page=page_number,
                    block_id=block_id,
                    heading_path=tuple(
                        h.text
                        for h in heading_stack
                    ),
                    heading_context=tuple(
                        heading_stack
                    ),
                    bbox=bbox,
                    atomic=atomic,
                    source_type=block_type,
                )
            )

    return units


def _copy_unit(
    unit: Unit,
    text: str,
    **metadata: Any,
) -> Unit:
    return Unit(
        kind=unit.kind,
        text=text,
        page=unit.page,
        block_id=unit.block_id,
        heading_path=unit.heading_path,
        heading_context=(
            unit.heading_context
        ),
        bbox=unit.bbox,
        atomic=unit.atomic,
        source_type=unit.source_type,
        metadata={
            **unit.metadata,
            **metadata,
        },
    )


def _split_words(
    text: str,
    max_tokens: int,
) -> list[str]:
    words: list[str] = []

    for word in text.split():
        if estimate_tokens(word) <= max_tokens:
            words.append(word)
            continue

        # A run without whitespace (a divider line, an encoded blob) that
        # alone exceeds the cap is cut at token boundaries; the pieces
        # re-tokenize exactly, since no cut falls inside a token.
        tokens = TOKEN_RE.findall(word)
        words.extend(
            "".join(tokens[i:i + max_tokens])
            for i in range(0, len(tokens), max_tokens)
        )

    output: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = " ".join(
            current + [word]
        )

        if (
            current
            and estimate_tokens(candidate)
            > max_tokens
        ):
            output.append(
                " ".join(current)
            )
            current = []

        current.append(word)

    if current:
        output.append(
            " ".join(current)
        )

    return [
        item
        for item in output
        if item.strip()
    ]


def split_long_prose(
    unit: Unit,
    hard_tokens: int,
) -> list[Unit]:
    if unit.tokens <= hard_tokens:
        return [unit]

    paragraphs = [
        p.strip()
        for p in re.split(
            r"\n\s*\n",
            unit.text,
        )
        if p.strip()
    ]

    raw_parts: list[str] = []

    for paragraph in paragraphs:
        if (
            estimate_tokens(paragraph)
            <= hard_tokens
        ):
            raw_parts.append(paragraph)
            continue

        sentences = [
            s.strip()
            for s in SENTENCE_SPLIT_RE.split(
                paragraph
            )
            if s.strip()
        ]

        current: list[str] = []

        for sentence in sentences:
            if (
                estimate_tokens(sentence)
                > hard_tokens
            ):
                if current:
                    raw_parts.append(
                        " ".join(current)
                    )
                    current = []

                raw_parts.extend(
                    _split_words(
                        sentence,
                        hard_tokens,
                    )
                )

                continue

            candidate = " ".join(
                current + [sentence]
            )

            if (
                current
                and estimate_tokens(
                    candidate
                )
                > hard_tokens
            ):
                raw_parts.append(
                    " ".join(current)
                )
                current = []

            current.append(sentence)

        if current:
            raw_parts.append(
                " ".join(current)
            )

    packed: list[str] = []
    current: list[str] = []

    for part in raw_parts:
        candidate = "\n\n".join(
            current + [part]
        )

        if (
            current
            and estimate_tokens(candidate)
            > hard_tokens
        ):
            packed.append(
                "\n\n".join(current)
            )
            current = []

        current.append(part)

    if current:
        packed.append(
            "\n\n".join(current)
        )

    return [
        _copy_unit(
            unit,
            text,
            split_from_source_block=True,
            split_part=index,
            split_parts=len(packed),
        )
        for index, text in enumerate(
            packed
        )
        if text.strip()
    ]


def split_atomic_by_lines(
    unit: Unit,
    atomic_hard_tokens: int,
) -> list[Unit]:
    if (
        unit.tokens
        <= atomic_hard_tokens
    ):
        return [unit]

    lines = unit.text.splitlines()

    pieces: list[str] = []
    current: list[str] = []

    for line in lines:
        if (
            estimate_tokens(line)
            > atomic_hard_tokens
        ):
            if current:
                pieces.append(
                    "\n".join(current)
                )
                current = []

            pieces.extend(
                _split_words(
                    line,
                    atomic_hard_tokens,
                )
            )

            continue

        candidate = "\n".join(
            current + [line]
        )

        if (
            current
            and estimate_tokens(candidate)
            > atomic_hard_tokens
        ):
            pieces.append(
                "\n".join(current)
            )
            current = []

        current.append(line)

    if current:
        pieces.append(
            "\n".join(current)
        )

    return [
        _copy_unit(
            unit,
            text,
            split_atomic_block=True,
            split_part=index,
            split_parts=len(pieces),
        )
        for index, text in enumerate(
            pieces
        )
        if text.strip()
    ]


# chunking.table_header_repeat (config/chunking.json, default false). A " | " table (the text
# adapter's "table" block) longer than the atomic cap is split at line boundaries, and only its
# first piece holds the header row; the caption line before it may sit in another chunk. With the
# layer on, every later piece starts with the table's caption line and header row
# (table_context). A chunk that starts with them carries their length as metadata
# "context_chars": the structure index places it in the source without them
# (rag_core.structure.located_text), and quotes are verified against the source text alone.
TABLE_CAPTION = re.compile(r"Table \d+[.:]?\s+\S")
CONTEXT_SHARE = 4        # the context may take at most 1/CONTEXT_SHARE of a piece


def table_caption(unit: Unit | None, table: Unit) -> str | None:
    """The caption line of `table`: the prose unit right before it in the same section, one line
    that reads like a caption: "Table 39. show radius table attributes Field Descriptions", or a
    short title without closing punctuation ("show ip mroute Field Descriptions")."""
    if unit is None or unit.kind != "prose" or unit.heading_path != table.heading_path:
        return None
    text = unit.text.strip()
    if not text or "\n" in text:
        return None
    if TABLE_CAPTION.match(text):
        return text
    if len(text) <= 100 and len(text.split()) <= 12 and not re.search(r"[.:;,!?]$", text):
        return text
    return None


def header_row(line: str) -> bool:
    """A table's first line reads like column names: two or more cells, each with a letter, at
    most 6 words and 48 characters, none closing with punctuation. "Field | Description" does;
    the first rows of "protocol | Name or number of an Internet protocol. ...", "Step 1 | In the
    Cisco ISE GUI, click ..." and "1 | 3 | 4 | 6" do not."""
    cells = [cell.strip() for cell in line.split(" | ")]
    return len(cells) >= 2 and all(
        re.search(r"[A-Za-z]", cell) and len(cell) <= 48 and len(cell.split()) <= 6
        and not re.search(r"[.:;,!?]$", cell)
        for cell in cells
    )


def table_context(units: list[Unit], index: int) -> list[str]:
    """The caption line (table_caption) and header row (header_row) of the table units[index], the
    ones it has, in that order."""
    table = units[index]
    caption = table_caption(units[index - 1] if index else None, table)
    first = table.text.strip().split("\n", 1)[0].strip()
    return [line for line in (caption, first if header_row(first) else None) if line]


def split_table_with_context(
    unit: Unit,
    atomic_hard_tokens: int,
    context: list[str],
) -> list[Unit]:
    """split_atomic_by_lines, every piece after the first starting with `context` (table_context)
    and its length as metadata "context_chars". The rows are split at the cap less the context's
    tokens, so a piece stays within the cap. A table within the cap, no context, or one longer
    than 1/CONTEXT_SHARE of the cap splits as split_atomic_by_lines does."""
    text = "\n".join(context)
    tokens = estimate_tokens(text)
    if (
        not context
        or unit.tokens <= atomic_hard_tokens
        or tokens * CONTEXT_SHARE > atomic_hard_tokens
    ):
        return split_atomic_by_lines(unit, atomic_hard_tokens)
    pieces = split_atomic_by_lines(unit, atomic_hard_tokens - tokens)
    for piece in pieces[1:]:
        piece.text = f"{text}\n{piece.text}"
        piece.metadata["context_chars"] = len(text) + 1
    return pieces


def _tail_excerpt(
    unit: Unit,
    max_tokens: int,
) -> Unit | None:
    words = unit.text.split()

    if not words:
        return None

    selected: list[str] = []

    for word in reversed(words):
        candidate = " ".join(
            reversed(
                selected + [word]
            )
        )

        if (
            selected
            and estimate_tokens(candidate)
            > max_tokens
        ):
            break

        selected.append(word)

    text = " ".join(
        reversed(selected)
    ).strip()

    if not text:
        return None

    clone = _copy_unit(
        unit,
        text,
        overlap_excerpt=True,
    )

    clone.atomic = False

    return clone


def trailing_overlap(
    units: list[Unit],
    max_tokens: int,
) -> list[Unit]:
    if max_tokens <= 0:
        return []

    carry: list[Unit] = []
    total = 0

    for unit in reversed(units):
        if unit.atomic:
            break

        if unit.kind not in {
            "prose",
            "list",
        }:
            break

        if (
            total + unit.tokens
            <= max_tokens
        ):
            carry.insert(0, unit)
            total += unit.tokens
            continue

        remaining = (
            max_tokens - total
        )

        if (
            not carry
            and remaining > 0
        ):
            excerpt = _tail_excerpt(
                unit,
                remaining,
            )

            if excerpt is not None:
                carry.insert(
                    0,
                    excerpt,
                )

        break

    return carry


def build_drafts(
    source_units: list[Unit],
    *,
    target_tokens: int = 650,
    hard_tokens: int = 950,
    overlap_tokens: int = 100,
    atomic_hard_tokens: int = 1800,
    policy: str = "v2",
    min_tokens: int = 200,
    table_header_repeat: bool = False,
) -> list[Draft]:
    if target_tokens <= 0:
        raise ValueError(
            "target_tokens must be > 0"
        )

    if hard_tokens < target_tokens:
        raise ValueError(
            "hard_tokens must be >= "
            "target_tokens"
        )

    # v2 gives every atomic unit a chunk of its own, up to atomic_hard_tokens.
    # v3 packs atomic units together with prose, so hard_tokens caps them too:
    # no v3 chunk may exceed hard_tokens.
    atomic_cap = (
        min(atomic_hard_tokens, hard_tokens)
        if policy == "v3"
        else atomic_hard_tokens
    )

    expanded: list[Unit] = []

    for index, unit in enumerate(source_units):
        if unit.atomic and table_header_repeat and unit.kind == "table":
            # a continuation piece starts its draft (v2: every atomic piece does; v3: the
            # packer flushes before it and never merges it into the draft before), so the
            # caption and header row lead the chunk (chunk_document)
            expanded.extend(
                split_table_with_context(unit, atomic_cap, table_context(source_units, index))
            )
        elif unit.atomic:
            expanded.extend(
                split_atomic_by_lines(
                    unit,
                    atomic_cap,
                )
            )
        else:
            expanded.extend(
                split_long_prose(
                    unit,
                    hard_tokens,
                )
            )

    if policy == "v3":
        return _build_drafts_context(
            expanded,
            target_tokens=target_tokens,
            hard_tokens=hard_tokens,
            overlap_tokens=overlap_tokens,
            min_tokens=min_tokens,
        )

    drafts: list[Draft] = []
    buffer: list[Unit] = []

    current_heading: (
        tuple[str, ...] | None
    ) = None

    def current_tokens() -> int:
        return estimate_tokens(
            "\n\n".join(
                u.text
                for u in buffer
            )
        )

    def flush(
        keep_overlap: bool = False,
    ) -> None:
        nonlocal buffer

        if not buffer:
            return

        drafts.append(
            Draft(
                heading_path=(
                    current_heading
                    or tuple()
                ),
                units=list(buffer),
                atomic=False,
            )
        )

        if keep_overlap:
            buffer = trailing_overlap(
                buffer,
                overlap_tokens,
            )
        else:
            buffer = []

    for unit in expanded:
        if current_heading is None:
            current_heading = (
                unit.heading_path
            )

        if (
            unit.heading_path
            != current_heading
        ):
            flush(False)
            current_heading = (
                unit.heading_path
            )

        if unit.atomic:
            flush(False)

            drafts.append(
                Draft(
                    heading_path=(
                        unit.heading_path
                    ),
                    units=[unit],
                    atomic=True,
                )
            )

            continue

        candidate = "\n\n".join(
            [u.text for u in buffer]
            + [unit.text]
        )

        if (
            buffer
            and estimate_tokens(candidate)
            > target_tokens
        ):
            flush(True)

        candidate = "\n\n".join(
            [u.text for u in buffer]
            + [unit.text]
        )

        if (
            buffer
            and estimate_tokens(candidate)
            > hard_tokens
        ):
            flush(False)

        buffer.append(unit)

        if current_tokens() >= hard_tokens:
            flush(True)

    flush(False)

    return [
        draft
        for draft in drafts
        if draft.text
    ]


def _common_prefix(
    a: tuple[str, ...],
    b: tuple[str, ...],
) -> tuple[str, ...]:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return a[:n]


def _same_scope(
    a: tuple[str, ...],
    b: tuple[str, ...],
) -> bool:
    """Merging is allowed inside one top-level section (or in a
    document without headings), never across top-level sections."""
    return bool(_common_prefix(a, b)) or (not a and not b)


def _heading_unit(
    unit: Unit,
    titles: tuple[str, ...],
    depth: int,
) -> Unit:
    """Synthetic unit that keeps an absorbed sub-heading's words in the
    chunk text (the chunk's heading_path becomes the common parent)."""
    context = unit.heading_context[-1] if unit.heading_context else None
    return Unit(
        kind="heading",
        text="\n".join(
            "#" * min(6, depth + i + 1) + " " + title
            for i, title in enumerate(titles)
        ),
        page=unit.page,
        block_id=(
            context.block_id
            if context is not None
            else unit.block_id
        ),
        heading_path=unit.heading_path,
        heading_context=unit.heading_context,
        bbox=None,
        atomic=False,
        source_type="heading",
    )


def _build_drafts_context(
    units: list[Unit],
    *,
    target_tokens: int,
    hard_tokens: int,
    overlap_tokens: int,
    min_tokens: int,
) -> list[Draft]:
    drafts: list[Draft] = []
    buffer: list[Unit] = []
    fresh = 0                      # units in buffer that are not carried overlap
    draft_path: tuple[str, ...] = ()
    section: tuple[str, ...] | None = None

    def tokens_of(us: list[Unit]) -> int:
        return estimate_tokens("\n\n".join(u.text for u in us))

    def flush(keep_overlap: bool) -> None:
        nonlocal buffer, fresh
        if fresh:
            drafts.append(
                Draft(
                    heading_path=draft_path,
                    units=list(buffer),
                    atomic=False,
                )
            )
        buffer = (
            trailing_overlap(buffer, overlap_tokens)
            if keep_overlap and fresh
            else []
        )
        fresh = 0

    for unit in units:
        path = unit.heading_path

        if section is None:
            section = draft_path = path

        if path != section:
            common = _common_prefix(draft_path, path)
            titles = path[len(common):]
            heading = (
                [_heading_unit(unit, titles, len(common))]
                if titles
                else []
            )
            if (
                fresh
                and tokens_of(buffer) < min_tokens
                and _same_scope(draft_path, path)
                # the unit must fit as well: a flush right after absorbing
                # would end a micro-chunk on the dangling new heading
                and tokens_of(buffer + heading + [unit]) <= target_tokens
            ):
                # small section: absorb the next one under the common parent
                draft_path = common
                buffer.extend(heading)
                fresh += len(heading)
            else:
                flush(False)           # overlap-only buffers are dropped, not emitted
                draft_path = path
            section = path

        # a continuation piece of a table split with its context (table_header_repeat) leads
        # its chunk: after a short piece (the end of a row longer than the cap, cut by words)
        # it would fit the same chunk and repeat the caption and header row inside it
        if buffer and unit.metadata.get("context_chars"):
            flush(False)
            draft_path = path

        if fresh and tokens_of(buffer + [unit]) > target_tokens:
            flush(True)
            draft_path = path

        if buffer and tokens_of(buffer + [unit]) > hard_tokens:
            flush(False)
            draft_path = path

        buffer.append(unit)
        fresh += 1

        if tokens_of(buffer) >= hard_tokens:
            flush(True)
            draft_path = path

    flush(False)

    # post-pass: a draft still under min_tokens joins its predecessor when both
    # sit in the same top-level section and the result stays within hard_tokens
    merged: list[Draft] = []
    for draft in drafts:
        if not draft.text:
            continue
        if merged:
            prev = merged[-1]
            if (
                (draft.tokens < min_tokens or prev.tokens < min_tokens)
                and _same_scope(prev.heading_path, draft.heading_path)
                # nor here: a table's continuation piece stays at the start of its chunk
                and not any(u.metadata.get("context_chars") for u in draft.units)
            ):
                common = _common_prefix(prev.heading_path, draft.heading_path)
                seen = {(u.block_id, u.text) for u in prev.units}
                prev_blocks = {u.block_id for u in prev.units}
                rest = [                       # drop overlap carried from prev
                    u for u in draft.units
                    if (u.block_id, u.text) not in seen
                    and not (
                        u.metadata.get("overlap_excerpt")
                        and u.block_id in prev_blocks
                    )
                ]
                if not rest:                   # nothing that prev lacks
                    continue
                titles = draft.heading_path[len(common):]
                prev_headings = {
                    u.text for u in prev.units if u.kind == "heading"
                }
                heading = _heading_unit(rest[0], titles, len(common))
                extra = (
                    [heading]
                    if titles
                    and rest[0].kind != "heading"
                    and heading.text not in prev_headings
                    else []
                )
                candidate = Draft(
                    heading_path=common,
                    units=prev.units + extra + rest,
                    atomic=False,
                )
                # measured as merged: the synthetic heading adds tokens
                if candidate.tokens <= hard_tokens:
                    merged[-1] = candidate
                    continue
        merged.append(draft)
    return merged


def build_section_map(
    conn,
    doc_id: str,
) -> dict[
    tuple[str, ...],
    str,
]:
    result = {}

    rows = conn.execute(
        """
        SELECT
            section_id,
            heading_path_json
        FROM sections
        WHERE doc_id = ?
        ORDER BY ordinal
        """,
        (doc_id,),
    )

    for row in rows:
        try:
            path = tuple(
                json.loads(
                    row[
                        "heading_path_json"
                    ]
                )
            )
        except Exception:
            path = tuple()

        result[path] = (
            row["section_id"]
        )

    return result


def resolve_section(
    heading_path: tuple[str, ...],
    mapping: dict[
        tuple[str, ...],
        str,
    ],
) -> str | None:
    candidate = heading_path

    while candidate:
        if candidate in mapping:
            return mapping[candidate]

        candidate = candidate[:-1]

    return mapping.get(tuple())


def make_provenance(
    units: list[Unit],
) -> dict[str, Any]:
    pages = [
        unit.page
        for unit in units
    ]

    source_block_ids: list[str] = []
    heading_block_ids: list[str] = []

    bboxes: list[
        dict[str, Any]
    ] = []

    heading_bboxes: list[
        dict[str, Any]
    ] = []

    for unit in units:
        if (
            unit.block_id
            not in source_block_ids
        ):
            source_block_ids.append(
                unit.block_id
            )

        if unit.bbox is not None:
            x0, y0, x1, y1 = (
                unit.bbox
            )

            item = {
                "page": unit.page,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "source_block_id":
                    unit.block_id,
            }

            if item not in bboxes:
                bboxes.append(item)

        for heading in (
            unit.heading_context
        ):
            if (
                heading.block_id
                not in heading_block_ids
            ):
                heading_block_ids.append(
                    heading.block_id
                )

            if heading.bbox is not None:
                x0, y0, x1, y1 = (
                    heading.bbox
                )

                item = {
                    "page":
                        heading.page,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "source_block_id":
                        heading.block_id,
                    "heading":
                        heading.text,
                    "level":
                        heading.level,
                }

                if (
                    item
                    not in heading_bboxes
                ):
                    heading_bboxes.append(
                        item
                    )

    return {
        "page_start": min(pages),
        "page_end": max(pages),
        "source_block_ids":
            source_block_ids,
        "heading_block_ids":
            heading_block_ids,
        "bboxes": bboxes,
        "heading_bboxes":
            heading_bboxes,
    }


def _context_meta(draft: Draft) -> dict[str, Any]:
    """{"context_chars": n} when the draft's text starts with a table's repeated caption and
    header row (split_table_with_context), else {}."""
    first = draft.units[0] if draft.units else None
    chars = first.metadata.get("context_chars") if first is not None else None
    if chars and draft.text.startswith(first.text[:chars]):
        return {"context_chars": chars}
    return {}


def chunk_document(
    doc_id: str,
    *,
    target_tokens: int = 650,
    hard_tokens: int = 950,
    overlap_tokens: int = 100,
    atomic_hard_tokens: int = 1800,
    policy: str | None = None,
    min_tokens: int = 200,
    table_header_repeat: bool | None = None,
) -> dict[str, Any]:
    """Chunk one CANONICALIZED (or CHUNKED) document into its chunks rows. `table_header_repeat`
    None reads chunking.table_header_repeat (chunking_settings)."""
    policy = chunker_policy(policy)
    version = CHUNKER_VERSIONS[policy]
    paths = RAGPaths.from_env()
    if table_header_repeat is None:
        table_header_repeat = chunking_settings()["table_header_repeat"]
    db = paths.sqlite / "rag.db"

    with connect(db) as conn:
        doc = conn.execute(
            """
            SELECT *
            FROM documents
            WHERE doc_id = ?
            """,
            (doc_id,),
        ).fetchone()

        if doc is None:
            raise KeyError(
                f"unknown document: "
                f"{doc_id}"
            )

        if doc["status"] not in {
            "CANONICALIZED",
            "CHUNKED",
        }:
            raise RuntimeError(
                "document status "
                f"{doc['status']!r} "
                "is not chunkable"
            )

        section_map = (
            build_section_map(
                conn,
                doc_id,
            )
        )

        doc_row = dict(doc)

    structured_path = (
        paths.normalized
        / doc_id
        / "structured_content.json"
    )

    if not structured_path.is_file():
        raise FileNotFoundError(
            structured_path
        )

    structured = json.loads(
        structured_path.read_text(
            encoding="utf-8"
        )
    )

    model_output_path = (
        paths.normalized
        / doc_id
        / "model_output.json"
    )

    model_output = {}

    if model_output_path.is_file():
        model_output = json.loads(
            model_output_path.read_text(
                encoding="utf-8"
            )
        )

    page_map = (
        derive_source_page_map(
            structured,
            model_output,
        )
    )

    units = extract_units(
        structured,
        page_map=page_map,
    )

    drafts = build_drafts(
        units,
        target_tokens=target_tokens,
        hard_tokens=hard_tokens,
        overlap_tokens=overlap_tokens,
        atomic_hard_tokens=(
            atomic_hard_tokens
        ),
        policy=policy,
        min_tokens=min_tokens,
        table_header_repeat=table_header_repeat,
    )

    if not drafts:
        raise RuntimeError(
            "chunker produced zero chunks"
        )

    document_meta = (
        structured
        .get("metadata", {})
        .get("document", {})
    )

    title = (
        doc_row.get("title")
        or document_meta.get("title")
        or (
            drafts[0].heading_path[0]
            if drafts[0].heading_path
            else doc_row[
                "original_filename"
            ]
        )
    )

    rows: list[
        dict[str, Any]
    ] = []

    for ordinal, draft in enumerate(
        drafts
    ):
        text = draft.text

        provenance = make_provenance(
            draft.units
        )

        heading_path = list(
            draft.heading_path
        )

        section_id = resolve_section(
            draft.heading_path,
            section_map,
        )

        section_label = (
            " > ".join(heading_path)
            if heading_path
            else "Document body"
        )

        embedding_text = (
            f"Document: {title}\n"
            f"Section: "
            f"{section_label}\n\n"
            f"{text}"
        )

        cid = chunk_id(
            doc_row["sha256"],
            ordinal,
            text,
        )

        rows.append(
            {
                "chunk_id": cid,
                "chunk_sha256":
                    sha256_text(text),
                "doc_id": doc_id,
                "section_id":
                    section_id,
                "ordinal":
                    ordinal,
                "heading_path_json":
                    json.dumps(
                        heading_path,
                        ensure_ascii=False,
                    ),
                "text": text,
                "embedding_text":
                    embedding_text,
                "token_count":
                    estimate_tokens(text),
                "char_count":
                    len(text),
                "page_start":
                    provenance[
                        "page_start"
                    ],
                "page_end":
                    provenance[
                        "page_end"
                    ],
                "provenance_json":
                    json.dumps(
                        provenance,
                        ensure_ascii=False,
                    ),
                "metadata_json":
                    json.dumps(
                        {
                            "chunker_version":
                                version,
                            "atomic":
                                draft.atomic,
                            "unit_types":
                                sorted(
                                    {
                                        u.kind
                                        for u
                                        in draft.units
                                    }
                                ),
                            "source_types":
                                sorted(
                                    {
                                        u.source_type
                                        for u
                                        in draft.units
                                    }
                                ),
                            "token_count_kind":
                                "lexical-estimate-v1",
                            **_context_meta(draft),
                        },
                        ensure_ascii=False,
                    ),
            }
        )

    for index, row in enumerate(rows):
        row["prev_chunk_id"] = (
            rows[index - 1][
                "chunk_id"
            ]
            if index > 0
            else None
        )

        row["next_chunk_id"] = (
            rows[index + 1][
                "chunk_id"
            ]
            if index + 1 < len(rows)
            else None
        )


    with connect(db) as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        conn.execute(
            """
            DELETE FROM chunks
            WHERE doc_id = ?
            """,
            (doc_id,),
        )

        for row in rows:
            conn.execute(
                """
                INSERT INTO chunks (
                    chunk_id,
                    chunk_sha256,
                    doc_id,
                    section_id,
                    ordinal,
                    heading_path_json,
                    text,
                    embedding_text,
                    token_count,
                    char_count,
                    page_start,
                    page_end,
                    prev_chunk_id,
                    next_chunk_id,
                    provenance_json,
                    metadata_json
                )
                VALUES (
                    :chunk_id,
                    :chunk_sha256,
                    :doc_id,
                    :section_id,
                    :ordinal,
                    :heading_path_json,
                    :text,
                    :embedding_text,
                    :token_count,
                    :char_count,
                    :page_start,
                    :page_end,
                    :prev_chunk_id,
                    :next_chunk_id,
                    :provenance_json,
                    :metadata_json
                )
                """,
                row,
            )

            conn.execute(
                """
                INSERT INTO index_state (
                    chunk_id,
                    lexical_indexed,
                    dense_indexed,
                    late_interaction_indexed
                )
                VALUES (?, 1, 0, 0)
                """,
                (
                    row["chunk_id"],
                ),
            )

        conn.execute(
            """
            UPDATE documents
            SET
                status = 'CHUNKED',
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE doc_id = ?
            """,
            (doc_id,),
        )

        conn.commit()

    manifest = {
        "doc_id": doc_id,
        "chunker_version":
            version,
        "target_tokens":
            target_tokens,
        "hard_tokens":
            hard_tokens,
        "overlap_tokens":
            overlap_tokens,
        "atomic_hard_tokens":
            atomic_hard_tokens,
        "source_units":
            len(units),
        "chunks": [
            {
                "chunk_id":
                    row["chunk_id"],
                "ordinal":
                    row["ordinal"],
                "page_start":
                    row["page_start"],
                "page_end":
                    row["page_end"],
                "token_count":
                    row["token_count"],
                "heading_path":
                    json.loads(
                        row[
                            "heading_path_json"
                        ]
                    ),
            }
            for row in rows
        ],
    }

    manifest_path = (
        paths.metadata
        / doc_id
        / "chunks.json"
    )

    manifest_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "doc_id": doc_id,
        "status": "CHUNKED",
        "chunker_version":
            version,
        "source_units":
            len(units),
        "chunks":
            len(rows),
        "manifest":
            str(manifest_path),
    }
