"""Repo configuration with a per-root overlay.

config/<name>.json in the repo holds the defaults. A data root may carry
$RAG_ROOT/config/<name>.json; its top-level keys replace the repo's. A root
built by scripts/build_root.py uses this to ship its own Qdrant collection
(dense.json), retriever set, technical-variant routing and rerank settings
(retrieval.json), and admission limit and generation id (serving.json).
The passage classes a search moves behind the other candidates
("passage_class": {"demote"}, none unless set:
rag_core.fusion.passage_class_demote), the
order of equal BM25 scores ("bm25": {"tie_order"}, "engine" unless a
root says "rowid": rag_core.retrievers.lexical.TIE_ORDERS) and the
technical variant's weight and token rule ("technical_variant":
{"weight", "tokens"}, 0.95 and "v1" unless set:
rag_core.query_plan.TechnicalVariantSettings) live in retrieval.json too. So
do two additions to what the reranker reads of a candidate ("reranker":
{"title", "covered_titles"}, each off unless true: rag_core.rerank). The
dense leg's Qdrant search settings ("dense": {"exact", "hnsw_ef"}, off unless
set: rag_core.retrievers.dense.DenseSearchSettings) live in retrieval.json too,
and so does the identical-text fold of the hits ("fold_identical", off unless
true: rag_core.api_v2.fold_identical), and the size of the top-tier evidence
window and the two fields a hit may carry besides its text ("evidence":
{"tier1_chars", "covered_titles", "table_context"}, each off unless a root
sets it: rag_core.api_v2.EvidenceSettings), and the rule /v2/verify_quote applies
to a quotation with omissions ("verify": {"ellipsis", "max_gap_chars"}, "any"
unless a root says "bounded": rag_core.api_v2.VerifySettings).

search_many's settings ("search_many_parallel", "search_many_rerank" and the
object "search_many": {"rerank_isolation"}, off unless true:
rag_core.api_v2.SearchManySettings) live in retrieval.json as well.

Precedence, lowest first:
  1. repo config/<name>.json
  2. $RAG_ROOT/config/<name>.json, key by key
  3. a file named by the loader's env var (RAG_RETRIEVAL_CONFIG,
     RAG_SERVING_CONFIG, RAG_LATE_INTERACTION_CONFIG, RAG_CHUNKING_CONFIG). It
     replaces 1 and 2 entirely, as before the overlay existed, so an eval arm
     stays exactly the file it names.
  4. per-key env vars (RAG_DENSE_COLLECTION, RAG_MAX_CONCURRENT_SEARCHES, ...),
     applied by each loader on top of what load_config() returns.

OVERLAY_NAMES are the serving configs a root ships (derive_root copies and
--sets them). BUILD_CONFIG_NAMES are read while a root is built: "chunking"
(rag_core.chunker.chunking_settings); build_root passes its own setting.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


REPO_CONFIG_DIR = (
    Path(__file__).resolve().parents[2]
    / "config"
)

OVERLAY_NAMES = frozenset({
    "dense",
    "retrieval",
    "late_interaction",
    "serving",
})

BUILD_CONFIG_NAMES = frozenset({
    "chunking",
})


def _read_object(
    path: Path,
) -> dict[str, Any]:
    data = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(data, dict):
        raise ValueError(
            f"{path} must hold a JSON object"
        )

    return data


def overlay_path(
    name: str,
    root: Path | str | None = None,
) -> Path | None:
    """$RAG_ROOT/config/<name>.json (root defaults to $RAG_ROOT), or None."""
    if name not in OVERLAY_NAMES | BUILD_CONFIG_NAMES:
        raise ValueError(
            f"no per-root overlay for config {name!r}"
        )

    if root is None:
        root = os.environ.get("RAG_ROOT") or None

    if root is None:
        return None

    return (
        Path(root).expanduser()
        / "config"
        / f"{name}.json"
    )


def load_config(
    name: str,
    *,
    root: Path | str | None = None,
    env_var: str | None = None,
    repo_dir: Path = REPO_CONFIG_DIR,
) -> dict[str, Any]:
    if name not in OVERLAY_NAMES | BUILD_CONFIG_NAMES:
        raise ValueError(
            f"no per-root overlay for config {name!r}"
        )

    if env_var and os.environ.get(env_var):
        return _read_object(
            Path(os.environ[env_var])
        )

    config = _read_object(
        repo_dir / f"{name}.json"
    )

    overlay = overlay_path(
        name,
        root,
    )

    if overlay is not None and overlay.is_file():
        config.update(
            _read_object(overlay)
        )

    return config
