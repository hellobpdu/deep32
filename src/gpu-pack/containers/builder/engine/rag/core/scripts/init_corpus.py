#!/usr/bin/env python3

from rag_core.db import database_health, initialize
from rag_core.paths import RAGPaths


def main():
    paths = RAGPaths.from_env()

    db = initialize(paths)

    print(f"RAG root: {paths.root}")
    print(f"SQLite:  {db}")

    health = database_health(paths)

    print("Integrity:", health["integrity"])
    print("FTS5:", health["fts5"])
    print("Documents:", health["documents"])
    print("Chunks:", health["chunks"])


if __name__ == "__main__":
    main()
