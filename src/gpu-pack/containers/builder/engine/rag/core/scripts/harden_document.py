#!/usr/bin/env python3

import argparse
import json

from rag_core.canonical import (
    harden_document,
)


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--doc-id",
        required=True,
    )

    args = ap.parse_args()

    result = harden_document(
        args.doc_id
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
