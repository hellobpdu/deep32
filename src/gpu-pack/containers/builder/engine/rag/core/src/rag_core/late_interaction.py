from __future__ import annotations

import functools
import json
import os
import shutil
import sqlite3
import uuid

from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import torch

from pylate import indexes, models

from rag_core.config import load_config
from rag_core.paths import RAGPaths


CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "late_interaction.json"
)


@dataclass(frozen=True)
class LateInteractionConfig:
    model: str
    revision: str | None
    embedding_dimension: int
    index_name: str
    device: str
    document_batch_size: int
    query_batch_size: int
    num_threads: int
    use_fast: bool
    nbits: int
    kmeans_niters: int
    max_points_per_centroid: int
    seed: int
    n_ivf_probe: int
    n_full_scores: int
    show_progress: bool
    keep_generations: int
    expected_pylate_version: str
    expected_fast_plaid_version: str
    # Torch intra-op threads while serving queries. Short queries are
    # latency-bound: on a 12-core host a 15-token query encoded in 948 ms
    # with 12 threads and 172 ms with 1. None keeps num_threads.
    query_num_threads: int | None = None
    # ColBERT query wording: "agent" (default), "agent-fixed" or "plain";
    # see rag_core.retrievers.colbert.build_query_text.
    query_template: str = "agent"
    # fast-plaid index-build knobs (None = library default): use_triton_kmeans
    # false for GPU builds on Turing; n_samples_kmeans caps the documents
    # sampled for k-means (default: all documents of a pilot-sized corpus).
    use_triton_kmeans: bool | None = None
    n_samples_kmeans: int | None = None
    # Build knobs pylate does not pass through (None = library default; see
    # _fast_plaid_build_knobs). The staging GPU build of the 30.7k-chunk corpus
    # stalled for 2 h with the defaults next to TEI and the reranker on an
    # 11 GB card (WDDM paged the working set through system RAM) and finished
    # in minutes with k-means blocks 8,192 x 4,096 and compression batches of
    # 2,048 documents.
    #   kmeans_chunk_size_data, kmeans_chunk_size_centroids: k-means distance
    #       block, points x centroids (library 51,200 x 10,240)
    #   compress_batch_size: documents per batch in fast-plaid's compression
    #       step (library 50,000, i.e. the whole corpus at once)
    kmeans_chunk_size_data: int | None = None
    kmeans_chunk_size_centroids: int | None = None
    compress_batch_size: int | None = None
    # PLAID search batch_size. pylate's default (1 << 18) let fast-plaid score
    # all ~30.7k candidates of the staging corpus as one fp16 tensor
    # (candidates x 3,945 doc tokens x query tokens, ~31 GiB): the serving
    # process was OOM-killed on its first queries. n_full_scores does not
    # help, it prunes only after that tensor is built. Bounded batches keep
    # one search in the hundreds of MiB.
    search_batch_size: int = 1024


def _optional_int(
    data: dict[str, Any],
    key: str,
) -> int | None:
    value = data.get(key)
    return int(value) if value is not None else None


def load_late_interaction_config(
    path: Path | None = None,
    *,
    root: Path | str | None = None,
) -> LateInteractionConfig:
    # RAG_LATE_INTERACTION_CONFIG lets a build use a different config (e.g.
    # device "cuda" for encoding) from the one the API serves with. Without
    # it, the root's overlay ($RAG_ROOT/config/late_interaction.json)
    # overrides the repo file key by key.
    if path is None:
        data = load_config(
            "late_interaction",
            root=root,
            env_var="RAG_LATE_INTERACTION_CONFIG",
            repo_dir=CONFIG_PATH.parent,
        )
    else:
        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    config = LateInteractionConfig(
        model=str(data["model"]),
        revision=data.get("revision"),
        embedding_dimension=int(
            data["embedding_dimension"]
        ),
        index_name=str(
            data["index_name"]
        ),
        device=str(
            data["device"]
        ),
        document_batch_size=int(
            data["document_batch_size"]
        ),
        query_batch_size=int(
            data["query_batch_size"]
        ),
        num_threads=int(
            data["num_threads"]
        ),
        use_fast=bool(
            data["use_fast"]
        ),
        nbits=int(
            data["nbits"]
        ),
        kmeans_niters=int(
            data["kmeans_niters"]
        ),
        max_points_per_centroid=int(
            data["max_points_per_centroid"]
        ),
        seed=int(
            data["seed"]
        ),
        n_ivf_probe=int(
            data["n_ivf_probe"]
        ),
        n_full_scores=int(
            data["n_full_scores"]
        ),
        show_progress=bool(
            data["show_progress"]
        ),
        keep_generations=int(
            data["keep_generations"]
        ),
        expected_pylate_version=str(
            data["expected_pylate_version"]
        ),
        expected_fast_plaid_version=str(
            data[
                "expected_fast_plaid_version"
            ]
        ),
        query_num_threads=(
            int(data["query_num_threads"])
            if data.get("query_num_threads") is not None
            else None
        ),
        query_template=str(
            data.get("query_template", "agent")
        ),
        use_triton_kmeans=(
            bool(data["use_triton_kmeans"])
            if data.get("use_triton_kmeans") is not None
            else None
        ),
        n_samples_kmeans=(
            int(data["n_samples_kmeans"])
            if data.get("n_samples_kmeans") is not None
            else None
        ),
        search_batch_size=int(
            data.get("search_batch_size", 1024)
        ),
        kmeans_chunk_size_data=_optional_int(
            data, "kmeans_chunk_size_data"
        ),
        kmeans_chunk_size_centroids=_optional_int(
            data, "kmeans_chunk_size_centroids"
        ),
        compress_batch_size=_optional_int(
            data, "compress_batch_size"
        ),
    )

    if config.search_batch_size <= 0:
        raise ValueError(
            "search_batch_size must be positive"
        )

    for name in (
        "kmeans_chunk_size_data",
        "kmeans_chunk_size_centroids",
        "compress_batch_size",
    ):
        value = getattr(config, name)
        if value is not None and value <= 0:
            raise ValueError(
                f"{name} must be positive or null"
            )

    if config.query_template not in {
        "agent",
        "agent-fixed",
        "plain",
    }:
        raise ValueError(
            "query_template must be agent, agent-fixed or plain"
        )

    if (
        config.query_num_threads is not None
        and config.query_num_threads <= 0
    ):
        raise ValueError(
            "query_num_threads must be positive"
        )

    if config.embedding_dimension <= 0:
        raise ValueError(
            "embedding_dimension must be positive"
        )

    if config.document_batch_size <= 0:
        raise ValueError(
            "document_batch_size must be positive"
        )

    if config.query_batch_size <= 0:
        raise ValueError(
            "query_batch_size must be positive"
        )

    if config.num_threads <= 0:
        raise ValueError(
            "num_threads must be positive"
        )

    if config.keep_generations < 2:
        raise ValueError(
            "keep_generations must be at least 2"
        )

    return config


def runtime_versions() -> dict[str, str]:
    return {
        "pylate": version("pylate"),
        "fast-plaid": version(
            "fast-plaid"
        ),
        "torch": version("torch"),
        "transformers": version(
            "transformers"
        ),
    }


def make_colbert_model(
    config: LateInteractionConfig,
) -> models.ColBERT:
    torch.set_num_threads(
        config.num_threads
    )

    kwargs: dict[str, Any] = {
        "model_name_or_path":
            config.model,
        "device":
            config.device,
    }

    if config.revision is not None:
        kwargs["revision"] = (
            config.revision
        )

    return models.ColBERT(
        **kwargs
    )


def _load_canonical_chunks(
    paths: RAGPaths,
) -> list[dict[str, Any]]:
    db = (
        paths.sqlite
        / "rag.db"
    )

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT
                c.chunk_id,
                c.doc_id,
                c.ordinal,
                c.embedding_text
            FROM chunks AS c
            JOIN index_state AS s
                ON s.chunk_id = c.chunk_id
            ORDER BY
                c.doc_id,
                c.ordinal
            """
        ).fetchall()

    result = [
        dict(row)
        for row in rows
    ]

    if not result:
        raise RuntimeError(
            "no canonical chunks available "
            "for late-interaction indexing"
        )

    for row in result:
        chunk_id = str(
            row["chunk_id"]
        )

        text = str(
            row["embedding_text"]
        )

        if not chunk_id:
            raise RuntimeError(
                "encountered empty chunk_id"
            )

        if not text.strip():
            raise RuntimeError(
                "chunk has empty embedding_text: "
                f"{chunk_id}"
            )

    return result


def _validate_embeddings(
    embeddings,
    *,
    expected_count: int,
    expected_dimension: int,
) -> None:
    if len(embeddings) != expected_count:
        raise RuntimeError(
            "late-interaction embedding "
            "count mismatch: "
            f"expected={expected_count} "
            f"actual={len(embeddings)}"
        )

    for index, embedding in enumerate(
        embeddings
    ):
        array = np.asarray(
            embedding
        )

        if array.ndim != 2:
            raise RuntimeError(
                "late-interaction embedding "
                "must be a 2-D token matrix: "
                f"index={index} "
                f"shape={array.shape}"
            )

        if (
            array.shape[1]
            != expected_dimension
        ):
            raise RuntimeError(
                "late-interaction dimension "
                "mismatch: "
                f"index={index} "
                f"expected="
                f"{expected_dimension} "
                f"actual={array.shape[1]}"
            )

        if not np.isfinite(
            array
        ).all():
            raise RuntimeError(
                "late-interaction embedding "
                "contains non-finite values: "
                f"index={index}"
            )


def _make_index(
    *,
    index_folder: Path,
    config: LateInteractionConfig,
    override: bool,
):
    return indexes.PLAID(
        index_folder=str(
            index_folder
        ),
        index_name=(
            config.index_name
        ),
        override=override,
        use_fast=config.use_fast,
        nbits=config.nbits,
        kmeans_niters=(
            config.kmeans_niters
        ),
        max_points_per_centroid=(
            config.max_points_per_centroid
        ),
        seed=config.seed,
        n_ivf_probe=(
            config.n_ivf_probe
        ),
        n_full_scores=(
            config.n_full_scores
        ),
        batch_size=(
            config.search_batch_size
        ),
        num_threads=(
            config.num_threads
        ),
        show_progress=(
            config.show_progress
        ),
        device=config.device,
        # None keeps the library defaults. On Turing GPUs (sm_75) fast-plaid's
        # auto-selected Triton k-means was 16x slower than its PyTorch path
        # (68 s vs 4.2 s per iteration for 300k points, k=32768).
        use_triton=config.use_triton_kmeans,
        n_samples_kmeans=config.n_samples_kmeans,
    )


@contextmanager
def _fast_plaid_build_knobs(
    index,
    config: LateInteractionConfig,
):
    """Apply the build knobs pylate 1.6.0 does not pass to fast-plaid while
    `index` is being built (see LateInteractionConfig), and undo them after.

    compress_batch_size binds batch_size on this index's own fast-plaid
    create(). The k-means block sizes replace fast-plaid's FastKMeans module
    global for the duration; index builds run in their own process
    (scripts/index_colbert.py), never beside a serving retriever.
    """
    blocks = {
        name: value
        for name, value in (
            ("chunk_size_data", config.kmeans_chunk_size_data),
            ("chunk_size_centroids", config.kmeans_chunk_size_centroids),
        )
        if value is not None
    }
    undo = []

    try:
        if config.compress_batch_size is not None:
            engine = getattr(
                getattr(index, "_index", None),
                "fast_plaid",
                None,
            )
            if engine is None:
                raise RuntimeError(
                    "compress_batch_size needs pylate's fast-plaid "
                    "backend (use_fast true)"
                )
            engine.create = functools.partial(
                engine.create,
                batch_size=config.compress_batch_size,
            )
            undo.append(lambda: delattr(engine, "create"))

        if blocks:
            from fast_plaid.search import fast_plaid as fast_plaid_search

            kmeans = fast_plaid_search.FastKMeans
            fast_plaid_search.FastKMeans = functools.partial(
                kmeans,
                **blocks,
            )
            undo.append(
                lambda: setattr(fast_plaid_search, "FastKMeans", kmeans)
            )

        yield

    finally:
        for step in reversed(undo):
            step()


def open_late_interaction_index(
    paths: RAGPaths,
    config: LateInteractionConfig
        | None = None,
):
    if config is None:
        config = (
            load_late_interaction_config(
                root=paths.root,
            )
        )

    current = (
        paths.pylate
        / "current"
    )

    if not current.is_symlink():
        raise RuntimeError(
            "late-interaction current "
            "index pointer is missing"
        )

    return _make_index(
        index_folder=current,
        config=config,
        override=False,
    )


def _update_sqlite_state(
    paths: RAGPaths,
    *,
    chunk_ids: list[str],
    config: LateInteractionConfig,
) -> None:
    db = (
        paths.sqlite
        / "rag.db"
    )

    with sqlite3.connect(db) as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        indexed_rows = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT chunk_id
                FROM index_state
                """
            ).fetchall()
        }

        canonical_ids = set(
            chunk_ids
        )

        if indexed_rows != canonical_ids:
            raise RuntimeError(
                "index_state/canonical "
                "chunk set mismatch"
            )

        conn.execute(
            """
            UPDATE index_state
            SET
                late_interaction_indexed = 0,
                colbert_model = NULL,
                colbert_revision = NULL
            """
        )

        conn.executemany(
            """
            UPDATE index_state
            SET
                late_interaction_indexed = 1,
                colbert_model = ?,
                colbert_revision = ?,
                indexed_at = CURRENT_TIMESTAMP
            WHERE chunk_id = ?
            """,
            [
                (
                    config.model,
                    config.revision,
                    chunk_id,
                )
                for chunk_id
                in chunk_ids
            ],
        )

        marked = conn.execute(
            """
            SELECT COUNT(*)
            FROM index_state
            WHERE
                late_interaction_indexed = 1
            """
        ).fetchone()

        if marked is None:
            raise RuntimeError(
                "could not verify "
                "late-interaction state"
            )

        if int(marked[0]) != len(
            chunk_ids
        ):
            raise RuntimeError(
                "late-interaction SQLite "
                "state verification failed"
            )

        conn.commit()


def _prune_generations(
    generations: Path,
    *,
    keep: int,
    protected: Path | None = None,
) -> None:
    candidates = [
        path
        for path in generations.iterdir()
        if (
            path.is_dir()
            and not path.name.startswith(".")
        )
    ]

    # Generations use random UUID names, so lexical ordering
    # has no relationship to creation order. Sort by filesystem
    # modification time instead.
    candidates.sort(
        key=lambda path: (
            path.stat().st_mtime_ns,
            path.name,
        ),
        reverse=True,
    )

    # Never prune the generation referenced by the active
    # current symlink, regardless of its timestamp/name.
    if protected is not None:
        protected_resolved = (
            protected.resolve(
                strict=False
            )
        )

        active = [
            path
            for path in candidates
            if (
                path.resolve(
                    strict=False
                )
                == protected_resolved
            )
        ]

        others = [
            path
            for path in candidates
            if (
                path.resolve(
                    strict=False
                )
                != protected_resolved
            )
        ]

        candidates = (
            active
            + others
        )

    for path in candidates[keep:]:
        shutil.rmtree(
            path,
            ignore_errors=True,
        )

def missing_index_files(
    staged_index: Path,
) -> list[str]:
    """Files a built PLAID index must contain, as reported missing.

    fast-plaid writes the raw-embedding cache embeddings.npy only for indexes
    of at most start_from_scratch (1,000) documents, so requiring it made every
    rebuild of a real corpus fail validation and roll back. Require the files
    every index has instead.
    """
    fp = staged_index / "fast_plaid_index"
    required = [
        staged_index / "documents_ids_to_plaid_ids.pkl",
        staged_index / "plaid_ids_to_documents_ids.pkl",
        fp / "metadata.json",
        fp / "centroids.npy",
        fp / "ivf.npy",
        fp / "ivf_lengths.npy",
    ]
    return [
        str(path)
        for path in required
        if not path.exists()
    ]


def rebuild_late_interaction_index(
    paths: RAGPaths,
) -> dict[str, Any]:
    paths.ensure()

    config = (
        load_late_interaction_config(
            root=paths.root,
        )
    )

    rows = _load_canonical_chunks(
        paths
    )

    chunk_ids = [
        str(row["chunk_id"])
        for row in rows
    ]

    documents = [
        str(row["embedding_text"])
        for row in rows
    ]

    model = make_colbert_model(
        config
    )

    embeddings = model.encode(
        documents,
        batch_size=(
            config.document_batch_size
        ),
        is_query=False,
        show_progress_bar=False,
        convert_to_numpy=True,
    )

    _validate_embeddings(
        embeddings,
        expected_count=len(
            documents
        ),
        expected_dimension=(
            config.embedding_dimension
        ),
    )

    generations = (
        paths.pylate
        / "generations"
    )

    generations.mkdir(
        parents=True,
        exist_ok=True,
    )

    generation_id = (
        "gen_"
        + uuid.uuid4().hex
    )

    staging = (
        generations
        / (
            "."
            + generation_id
            + ".staging"
        )
    )

    final_generation = (
        generations
        / generation_id
    )

    current = (
        paths.pylate
        / "current"
    )

    temporary_link = (
        paths.pylate
        / (
            ".current."
            + uuid.uuid4().hex
            + ".tmp"
        )
    )

    old_target: str | None = None
    switched = False

    if current.exists() and (
        not current.is_symlink()
    ):
        raise RuntimeError(
            "pylate/current exists "
            "but is not a symlink"
        )

    if current.is_symlink():
        old_target = os.readlink(
            current
        )

    shutil.rmtree(
        staging,
        ignore_errors=True,
    )

    staging.mkdir(
        parents=True,
        exist_ok=False,
    )

    try:
        index = _make_index(
            index_folder=staging,
            config=config,
            override=True,
        )

        with _fast_plaid_build_knobs(
            index,
            config,
        ):
            index.add_documents(
                documents_ids=chunk_ids,
                documents_embeddings=(
                    embeddings
                ),
            )

        staged_index = (
            staging
            / config.index_name
        )

        if not staged_index.is_dir():
            raise RuntimeError(
                "PLAID staged index "
                "was not created"
            )

        missing = missing_index_files(
            staged_index
        )

        if missing:
            raise RuntimeError(
                "PLAID staged index is "
                "incomplete: "
                + ", ".join(
                    missing
                )
            )

        # Prove the staged generation can
        # actually be reopened before it
        # can become current.
        _make_index(
            index_folder=staging,
            config=config,
            override=False,
        )

        staging.rename(
            final_generation
        )

        relative_target = (
            Path("generations")
            / generation_id
        )

        temporary_link.symlink_to(
            relative_target,
            target_is_directory=True,
        )

        os.replace(
            temporary_link,
            current,
        )

        switched = True

        try:
            _update_sqlite_state(
                paths,
                chunk_ids=chunk_ids,
                config=config,
            )

        except Exception:
            rollback_link = (
                paths.pylate
                / (
                    ".rollback."
                    + uuid.uuid4().hex
                    + ".tmp"
                )
            )

            if old_target is None:
                if current.is_symlink():
                    current.unlink()

            else:
                rollback_link.symlink_to(
                    old_target,
                    target_is_directory=True,
                )

                os.replace(
                    rollback_link,
                    current,
                )

            switched = False

            shutil.rmtree(
                final_generation,
                ignore_errors=True,
            )

            raise

        _prune_generations(
            generations,
            keep=(
                config.keep_generations
            ),
            protected=current,
        )

        return {
            "model":
                config.model,

            "revision":
                config.revision,

            "chunks":
                len(chunk_ids),

            "embedding_dimension":
                config.embedding_dimension,

            "generation":
                generation_id,

            "index_path":
                str(
                    final_generation
                    / config.index_name
                ),

            "current_path":
                str(current),

            "runtime_versions":
                runtime_versions(),
        }

    except Exception:
        if temporary_link.exists() or (
            temporary_link.is_symlink()
        ):
            temporary_link.unlink(
                missing_ok=True
            )

        if (
            not switched
            and staging.exists()
        ):
            shutil.rmtree(
                staging,
                ignore_errors=True,
            )

        raise
