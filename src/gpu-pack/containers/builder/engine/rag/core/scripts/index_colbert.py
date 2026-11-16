#!/usr/bin/env python3

from __future__ import annotations

import json

from rag_core.late_interaction import (
    rebuild_late_interaction_index,
)
from rag_core.paths import RAGPaths


def main() -> None:
    result = (
        rebuild_late_interaction_index(
            RAGPaths.from_env()
        )
    )

    print(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
