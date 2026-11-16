from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

from typing import Any


@dataclass(frozen=True)
class QueryVariant:
    variant_id: str
    purpose: str
    text: str
    weight: float = 1.0
    # Retrievers this variant runs on; None means all of them.
    retrievers: tuple[str, ...] | None = None


@dataclass
class SearchHit:
    retriever: str
    variant_id: str
    purpose: str

    chunk_id: str
    doc_id: str

    rank: int
    score: float

    text: str

    heading_path: list[str] = (
        field(
            default_factory=list
        )
    )

    page_start: int | None = None
    page_end: int | None = None

    metadata: dict[str, Any] = (
        field(
            default_factory=dict
        )
    )
