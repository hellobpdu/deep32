#!/usr/bin/env python3

import argparse
import json

from rag_core.dense import (
    index_dense,
)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--doc-id",
        default=None,
    )

    args = parser.parse_args()

    result = index_dense(
        doc_id=args.doc_id,
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
