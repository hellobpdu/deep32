#!/usr/bin/env python3

import argparse
import json

from rag_core.chunker import (
    chunk_document,
)


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--doc-id",
        required=True,
    )

    ap.add_argument(
        "--target-tokens",
        type=int,
        default=650,
    )

    ap.add_argument(
        "--hard-tokens",
        type=int,
        default=950,
    )

    ap.add_argument(
        "--overlap-tokens",
        type=int,
        default=100,
    )

    ap.add_argument(
        "--atomic-hard-tokens",
        type=int,
        default=1800,
    )

    args = ap.parse_args()

    result = chunk_document(
        args.doc_id,
        target_tokens=(
            args.target_tokens
        ),
        hard_tokens=(
            args.hard_tokens
        ),
        overlap_tokens=(
            args.overlap_tokens
        ),
        atomic_hard_tokens=(
            args.atomic_hard_tokens
        ),
    )

    print(
        json.dumps(
            result,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
