#!/usr/bin/env python3

import argparse
import json

from dataclasses import asdict

from rag_core.paths import RAGPaths
from rag_core.retrieval_types import (
    QueryVariant,
)
from rag_core.retrievers.dense import (
    DenseRetriever,
)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "query",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    args = parser.parse_args()

    variant = QueryVariant(
        variant_id="dense-manual",
        purpose="primary",
        text=args.query,
        weight=1.0,
    )

    retriever = DenseRetriever(
        RAGPaths.from_env()
    )

    hits = retriever.search(
        variant,
        limit=args.limit,
    )

    print(
        json.dumps(
            [
                asdict(hit)
                for hit in hits
            ],
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
