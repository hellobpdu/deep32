#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

from rag_core.ingest import ingest_pdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", required=True)
    ap.add_argument("--result-dir", required=True)
    ap.add_argument("--source-name", default="mineru")
    ap.add_argument("--source-uri")
    args = ap.parse_args()

    result = ingest_pdf(
        Path(args.original),
        Path(args.result_dir),
        source_name=args.source_name,
        source_uri=args.source_uri,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
