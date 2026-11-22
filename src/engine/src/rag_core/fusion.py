from __future__ import annotations

from typing import Any

from rag_core.retrieval_types import (
    SearchHit,
)

# retrieval.json "passage_class": {"demote": [...]}: the classes of rag.db's passage_class table (chunk_id, class)
PASSAGE_CLASSES = ("toc", "legal", "acknowledgments", "references", "bcp14", "lab-disclaimer", "content")
PASSAGE_CLASS_KEYS = ("demote",)


def passage_class_demote(config: dict[str, Any]) -> tuple[str, ...]:
    """retrieval.json "passage_class": {"demote": ["toc", "legal", ...]}: the passage classes a search moves
    behind every other candidate after the fusion (rag_core.orchestrator: before the reranker's candidates are
    taken). [] or unset (the default): nothing moves. ValueError names a value it cannot take; the orchestrator
    checks it when it starts, and scripts/derive_root.py before it writes a derived root. On a root whose rag.db
    has no passage_class table the setting does nothing."""
    value = config.get("passage_class")
    if value is None:
        return ()
    if not isinstance(value, dict) or set(value) - set(PASSAGE_CLASS_KEYS):
        raise ValueError(f"retrieval.json passage_class must be an object of {list(PASSAGE_CLASS_KEYS)}, "
                         f"not {value!r}")
    demote = value.get("demote", [])
    if not isinstance(demote, list) or not all(isinstance(c, str) and c in PASSAGE_CLASSES for c in demote):
        raise ValueError(f"retrieval.json passage_class.demote must be a list of {list(PASSAGE_CLASSES)}, "
                         f"not {demote!r}")
    return tuple(dict.fromkeys(demote))


def demoted(candidates: list[dict], low: set[str]) -> list[dict]:
    """`candidates` with those whose chunk_id is in `low` moved behind the others; each group keeps its order."""
    return [c for c in candidates if c["chunk_id"] not in low] + [c for c in candidates if c["chunk_id"] in low]


def rrf_fuse(
    hits: list[SearchHit],
    *,
    retriever_weights:
        dict[str, float],
    variant_weights:
        dict[str, float],
    active_retrievers:
        list[str],
    k: int = 60,
    limit: int = 30,
    keep_text: bool = False,
) -> list[dict]:

    merged = {}

    for hit in hits:
        rweight = (
            retriever_weights.get(
                hit.retriever,
                1.0,
            )
        )

        vweight = (
            variant_weights.get(
                hit.variant_id,
                1.0,
            )
        )

        contribution = (
            rweight
            * vweight
            / (k + hit.rank)
        )

        item = merged.setdefault(
            hit.chunk_id,
            {
                "chunk_id":
                    hit.chunk_id,
                "doc_id":
                    hit.doc_id,
                "fused_score":
                    0.0,
                "text":
                    hit.text,
                "heading_path":
                    hit.heading_path,
                "page_start":
                    hit.page_start,
                "page_end":
                    hit.page_end,
                "retrievers":
                    set(),
                "variants":
                    set(),
                "purposes":
                    set(),
                "channels":
                    {},
            },
        )

        item[
            "fused_score"
        ] += contribution

        item[
            "retrievers"
        ].add(
            hit.retriever
        )

        item[
            "variants"
        ].add(
            hit.variant_id
        )

        item[
            "purposes"
        ].add(
            hit.purpose
        )

        channel = (
            f"{hit.retriever}:"
            f"{hit.variant_id}"
        )

        item["channels"][
            channel
        ] = {
            "rank":
                hit.rank,
            "raw_score":
                hit.score,
            "rrf_contribution":
                contribution,
        }

    # Deterministic order: fused score, then the best rank any channel gave
    # the chunk, then chunk_id (equal scores used to come out in arbitrary
    # order, so identical queries could return different top-k lists).
    result = sorted(
        merged.values(),
        key=lambda item: (
            -item["fused_score"],
            min(
                (
                    ch["rank"]
                    for ch in item["channels"].values()
                ),
                default=0,
            ),
            item["chunk_id"],
        ),
    )

    count = len(
        active_retrievers
    )

    for item in result:
        retrievers = sorted(
            item["retrievers"]
        )

        variants = sorted(
            item["variants"]
        )

        purposes = sorted(
            item["purposes"]
        )

        item["retrievers"] = (
            retrievers
        )

        item["variants"] = (
            variants
        )

        item["purposes"] = (
            purposes
        )

        item["agreement"] = {
            "retriever_count":
                len(retrievers),
            "active_retrievers":
                count,
            "consensus_signal":
                (
                    len(retrievers)
                    / count
                    if count >= 2
                    else None
                ),
            "variant_count":
                len(variants),
        }

        item["snippet"] = (
            item["text"]
            .replace("\n", " ")
            [:360]
        )

        if not keep_text:
            del item["text"]

    return result[:limit]
