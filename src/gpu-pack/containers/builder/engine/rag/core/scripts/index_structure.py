#!/usr/bin/env python3
"""Index the source structure of RAG_ROOT: sections, line ranges and chunk spans (rag_core.structure).

usage: index_structure.py [--sources DIR] [--force]

Adds the doc_sources / doc_sections / chunk_spans tables to an existing root if needed (existing
tables are not altered) and indexes every document with a source file under $RAG_ROOT/sources
(or --sources). Idempotent: unchanged documents are skipped. Prints a JSON summary.

Exit status: 0 when at least one document is indexed; 1 when none has a source file; 2 when the
source directory is missing (nothing is written). A root built by scripts/build_root.py has
its sources and is indexed by the build.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from rag_core.paths import RAGPaths
from rag_core.structure import index_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    paths = RAGPaths.from_env()
    sources = args.sources or paths.root / "sources"
    if not sources.is_dir():
        print(f"no source directory {sources}; build the root with scripts/build_root.py",
              file=sys.stderr)
        return 2
    counts = index_root(paths, sources=sources, force=args.force)
    print(json.dumps(counts, indent=1))
    if not counts["indexed"] + counts["unchanged"]:
        print(f"no document has a source file under {sources}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
