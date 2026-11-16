from __future__ import annotations

from rag_core.retrieval_types import (
    SearchHit,
)


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
