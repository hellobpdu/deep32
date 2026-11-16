from __future__ import annotations

from typing import Protocol

from rag_core.retrieval_types import (
    QueryVariant,
    SearchHit,
)


class Retriever(Protocol):
    name: str

    def search(
        self,
        variant: QueryVariant,
        *,
        limit: int,
    ) -> list[SearchHit]:
        ...
