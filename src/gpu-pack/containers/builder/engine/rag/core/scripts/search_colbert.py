#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json

from rag_core.paths import RAGPaths
from rag_core.retrieval_types import (
    QueryVariant,
)
from rag_core.retrievers.colbert import (
    ColBERTRetriever,
)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "query"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
    )

    args = parser.parse_args()

    retriever = ColBERTRetriever(
        RAGPaths.from_env()
    )

    variant = QueryVariant(
        variant_id="direct-colbert",
        purpose="primary",
        text=args.query,
        weight=1.0,
    )

    hits = retriever.search(
        variant,
        limit=args.limit,
    )

    for hit in hits:
        print(
            json.dumps(
                {
                    "rank":
                        hit.rank,

                    "score":
                        hit.score,

                    "chunk_id":
                        hit.chunk_id,

                    "doc_id":
                        hit.doc_id,

                    "heading_path":
                        hit.heading_path,

                    "page_start":
                        hit.page_start,

                    "page_end":
                        hit.page_end,

                    "snippet":
                        hit.text[:400],

                    "metadata":
                        hit.metadata.get(
                            "colbert"
                        ),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
