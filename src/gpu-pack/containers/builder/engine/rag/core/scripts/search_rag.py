#!/usr/bin/env python3

import argparse
import json

from rag_core.orchestrator import (
    RetrievalOrchestrator,
)

from rag_core.paths import (
    RAGPaths,
)

from rag_core.retrieval_types import (
    QueryVariant,
)


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "query",
    )

    ap.add_argument(
        "--mode",
        choices=[
            "fast",
            "normal",
            "deep",
            "forensic",
        ],
        default="normal",
    )

    ap.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    ap.add_argument(
        "--variant",
        action="append",
        default=[],
        help=(
            "additional probe "
            "as purpose=text"
        ),
    )

    args = ap.parse_args()

    extras = []

    for index, raw in enumerate(
        args.variant
    ):
        if "=" not in raw:
            raise SystemExit(
                "--variant must be "
                "purpose=text"
            )

        purpose, text = (
            raw.split(
                "=",
                1,
            )
        )

        extras.append(
            QueryVariant(
                variant_id=(
                    f"user-{index}"
                ),
                purpose=(
                    purpose.strip()
                ),
                text=text.strip(),
                weight=1.0,
            )
        )

    engine = (
        RetrievalOrchestrator(
            RAGPaths.from_env()
        )
    )

    result = engine.search(
        args.query,
        mode=args.mode,
        limit=args.limit,
        extra_variants=extras,
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
