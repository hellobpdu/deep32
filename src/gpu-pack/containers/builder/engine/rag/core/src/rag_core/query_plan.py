from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_core import kb
from rag_core.retrieval_types import (
    QueryVariant,
)


TECH_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]"
    r"[A-Za-z0-9_.:/-]*"
)


def _id(
    purpose: str,
    text: str,
) -> str:
    digest = hashlib.sha256(
        (
            purpose
            + "\0"
            + text
        ).encode()
    ).hexdigest()[:12]

    return (
        f"{purpose}-{digest}"
    )


def _make(
    purpose: str,
    text: str,
    weight: float,
    retrievers: tuple[str, ...] | None = None,
) -> QueryVariant:
    return QueryVariant(
        variant_id=_id(
            purpose,
            text,
        ),
        purpose=purpose,
        text=text.strip(),
        weight=weight,
        retrievers=retrievers,
    )


TECHNICAL_KEYS = ("weight", "tokens")
TECH_TOKEN_RULES = ("v1", "v2")         # technical_variant.tokens; the first is the default
DEFAULT_TECHNICAL_WEIGHT = 0.95
LONG_WORD = 9                           # a plain word of this many characters is "long"
VOCABULARY_WORD = re.compile(r"[a-z][a-z0-9]*")


@dataclass(frozen=True)
class TechnicalVariantSettings:
    """retrieval.json "technical_variant": {...}: the keyword-only copy of the question that build_query_plan adds
    as the variant "technical" (routed by technical_variant_retrievers, BM25 only on a built root). Without the
    object the plan is the one before.

        "weight": 0.95
            The variant's weight in the fusion, a number from 0 to 1 (the primary variant has 1.0). 0: the
            variant is not planned at all: no second BM25 query runs and no channel of it enters the fusion.
            (An empty technical_variant_retrievers list cannot say that: it means every retriever.)
        "tokens": "v1" | "v2"
            Which words of the question the copy keeps (technical_terms). v1 (default): a token with a digit,
            in capitals, with one of . _ : / - in it, or of 9 characters or more. v2: the token first loses
            any . : / - at its end, a single letter is dropped, and a plain word of 9 characters or more is
            kept only when the knowledge base's command index or glossary has it (technical_vocabulary).
    """
    weight: float = DEFAULT_TECHNICAL_WEIGHT
    tokens: str = TECH_TOKEN_RULES[0]


def technical_variant_settings(config: dict[str, Any]) -> TechnicalVariantSettings:
    """The settings in a retrieval config. ValueError names a value they cannot take; the orchestrator checks
    them when it starts, and scripts/derive_root.py before it writes a derived root."""
    value = config.get("technical_variant")
    if value is None:
        return TechnicalVariantSettings()
    if not isinstance(value, dict) or set(value) - set(TECHNICAL_KEYS):
        raise ValueError(f"retrieval.json technical_variant must be an object of {list(TECHNICAL_KEYS)}, "
                         f"not {value!r}")
    weight = value.get("weight", DEFAULT_TECHNICAL_WEIGHT)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 1:
        raise ValueError(f"retrieval.json technical_variant.weight must be a number from 0 to 1, not {weight!r}")
    tokens = value.get("tokens", TECH_TOKEN_RULES[0])
    if tokens not in TECH_TOKEN_RULES:
        raise ValueError(f"retrieval.json technical_variant.tokens must be one of {list(TECH_TOKEN_RULES)}, "
                         f"not {tokens!r}")
    return TechnicalVariantSettings(weight=float(weight), tokens=tokens)


def technical_vocabulary(kb_path: Path) -> frozenset[str]:
    """The long words (LONG_WORD characters or more, lower case) of the knowledge base's command index and
    glossary: every run of letters and digits that begins with a letter in cli.keywords and in glossary_key.key
    of kb.sqlite. Read once, when an orchestrator starts with technical_variant.tokens "v2" (about 0.2 s and
    1,600 words on the 150,917 command rows of d32kb-20261115cr); a question then costs one set lookup per long
    word. frozenset() when the root has no kb.sqlite: v2 then keeps no plain long word."""
    db = kb.open_kb(kb_path)
    if db is None:
        return frozenset()
    try:
        words: set[str] = set()
        for sql in ("SELECT keywords FROM cli", "SELECT key FROM glossary_key"):
            for (text,) in db.execute(sql):
                words.update(w for w in VOCABULARY_WORD.findall((text or "").casefold()) if len(w) >= LONG_WORD)
        return frozenset(words)
    finally:
        db.close()


def technical_terms(
    query: str,
    tokens: str = TECH_TOKEN_RULES[0],
    vocabulary: frozenset[str] = frozenset(),
) -> str:
    """The words of the question the technical variant keeps, by rule `tokens` (TechnicalVariantSettings), each
    once, in order. `vocabulary` (technical_vocabulary) is read by rule v2 only."""
    found = TECH_TOKEN_RE.findall(
        query
    )

    selected = []

    for token in found:
        if tokens == TECH_TOKEN_RULES[1]:
            token = token.rstrip(".:/-")
            if len(token) == 1 and token.isalpha():
                continue
            special = (
                any(c.isdigit() for c in token)
                or token.isupper()
                or any(ch in token for ch in "._:/-")
                or len(token) >= LONG_WORD and token.casefold() in vocabulary
            )
        else:
            special = (
                any(
                    c.isdigit()
                    for c in token
                )
                or token.isupper()
                or any(
                    ch in token
                    for ch in "._:/-"
                )
                or len(token) >= LONG_WORD
            )

        if special:
            selected.append(token)

    return " ".join(
        dict.fromkeys(selected)
    )


def build_query_plan(
    query: str,
    mode: str,
    extra_variants:
        list[QueryVariant]
        | None = None,
    technical_retrievers:
        list[str]
        | tuple[str, ...]
        | None = None,
    technical: TechnicalVariantSettings = TechnicalVariantSettings(),
    vocabulary: frozenset[str] = frozenset(),
) -> list[QueryVariant]:
    """technical_retrievers restricts the keyword-bag "technical" variant
    (e.g. to ("bm25",)); a bag of identifiers is a poor query for the
    semantic retrievers and costs an extra embedding and ColBERT encode.
    `technical` (retrieval.json technical_variant) sets that variant's weight
    and token rule, `vocabulary` (technical_vocabulary) serves rule v2; a
    weight of 0 leaves the variant out of the plan."""

    mode = mode.lower()

    if mode not in {
        "fast",
        "normal",
        "deep",
        "forensic",
    }:
        raise ValueError(
            f"unknown search mode: "
            f"{mode}"
        )

    variants = [
        _make(
            "primary",
            query,
            1.0,
        )
    ]

    tech = technical_terms(
        query,
        technical.tokens,
        vocabulary,
    )

    if (
        mode in {
            "normal",
            "deep",
            "forensic",
        }
        and technical.weight > 0
        and tech
        and tech.casefold()
            != query.casefold()
    ):
        variants.append(
            _make(
                "technical",
                tech,
                technical.weight,
                tuple(technical_retrievers)
                if technical_retrievers
                else None,
            )
        )

    if mode in {
        "deep",
        "forensic",
    }:
        variants.append(
            _make(
                "support",
                (
                    query
                    + " requirement "
                    + "prerequisite "
                    + "behavior operation"
                ),
                0.85,
            )
        )

        variants.append(
            _make(
                "exception",
                (
                    query
                    + " exception except "
                    + "unless limitation "
                    + "caveat"
                ),
                0.70,
            )
        )

    if mode == "forensic":
        variants.append(
            _make(
                "contradiction",
                (
                    query
                    + " not unsupported "
                    + "does not difference "
                    + "contrary"
                ),
                0.65,
            )
        )

        variants.append(
            _make(
                "version",
                (
                    query
                    + " Cisco IOS XE "
                    + "release version "
                    + "platform"
                ),
                0.75,
            )
        )

    if extra_variants:
        variants.extend(
            extra_variants
        )

    output = []
    seen = set()

    for variant in variants:
        key = (
            variant.purpose,
            variant.text.casefold(),
        )

        if key in seen:
            continue

        seen.add(key)
        output.append(variant)

    return output
