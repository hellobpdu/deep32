from __future__ import annotations

import hashlib
import re

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


def technical_terms(
    query: str,
) -> str:
    tokens = TECH_TOKEN_RE.findall(
        query
    )

    selected = []

    for token in tokens:
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
            or len(token) >= 9
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
) -> list[QueryVariant]:
    """technical_retrievers restricts the keyword-bag "technical" variant
    (e.g. to ("bm25",)); a bag of identifiers is a poor query for the
    semantic retrievers and costs an extra embedding and ColBERT encode."""

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
        query
    )

    if (
        mode in {
            "normal",
            "deep",
            "forensic",
        }
        and tech
        and tech.casefold()
            != query.casefold()
    ):
        variants.append(
            _make(
                "technical",
                tech,
                0.95,
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
