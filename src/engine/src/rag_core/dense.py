from __future__ import annotations

import json
import logging
import math
import os
import uuid

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx

from qdrant_client import QdrantClient
from qdrant_client import models

from rag_core.config import (
    load_config,
    overlay_path,
)
from rag_core.db import connect
from rag_core.paths import RAGPaths


logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dense.json"
)

# Query-time embedding timeout when neither RAG_EMBED_QUERY_TIMEOUT nor
# dense.json "query_timeout_seconds" sets one.
DEFAULT_QUERY_TIMEOUT_SECONDS = 15.0

# How long the query embedder keeps an idle connection to TEI (dense.json
# "query_keepalive_seconds"). A new TLS connection through Tailscale Serve cost
# 82-94 ms from deep32-engine; Serve kept idle connections open for 330 s and more
# (measured 2026-09-28), so a 60 s expiry made the first search after a minute
# idle pay that handshake again.
DEFAULT_QUERY_KEEPALIVE_SECONDS = 300.0
BULK_KEEPALIVE_SECONDS = 60.0

# Points per Qdrant upsert request: 1024-d vectors plus chunk payloads are
# roughly 10-25 KiB per point, so 256 stays far below the 32 MiB limit.
UPSERT_BATCH_POINTS = 256


@dataclass(frozen=True)
class DenseConfig:
    collection: str
    vector_size: int
    qdrant_url: str
    embedding_url: str
    embedding_request_model: str
    batch_size: int
    timeout_seconds: float
    embedding_revision: str | None = None
    query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS
    query_keepalive_seconds: float = DEFAULT_QUERY_KEEPALIVE_SECONDS


def _root_overlay(
    root: Path | str | None,
) -> dict[str, Any]:
    path = overlay_path(
        "dense",
        root,
    )

    if path is None or not path.is_file():
        return {}

    overlay = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    # A root's vectors live in its own collection: inheriting the repo's
    # shared collection would mix two corpora in one index.
    if not overlay.get("collection"):
        raise ValueError(
            f"{path} must name the root's own Qdrant collection"
        )

    return overlay


def load_dense_config(
    path: Path | None = None,
    *,
    root: Path | str | None = None,
) -> DenseConfig:
    """dense.json: `path` if given, else the repo file with the root's
    overlay ($RAG_ROOT/config/dense.json) on top. Env vars override both."""
    if path is not None:
        raw = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    else:
        overlay = _root_overlay(root)
        raw = load_config(
            "dense",
            root=root,
            repo_dir=DEFAULT_CONFIG_PATH.parent,
        )
        env_collection = os.environ.get(
            "RAG_DENSE_COLLECTION"
        )
        if overlay and env_collection and (
            env_collection != overlay["collection"]
        ):
            logger.warning(
                "RAG_DENSE_COLLECTION=%s overrides the root's collection %s",
                env_collection,
                overlay["collection"],
            )

    return DenseConfig(
        collection=os.environ.get(
            "RAG_DENSE_COLLECTION",
            raw["collection"],
        ),
        vector_size=int(
            raw["vector_size"]
        ),
        qdrant_url=os.environ.get(
            "RAG_QDRANT_URL",
            raw["qdrant_url"],
        ),
        embedding_url=os.environ.get(
            "RAG_EMBEDDING_URL",
            raw["embedding_url"],
        ),
        embedding_request_model=(
            os.environ.get(
                "RAG_EMBEDDING_MODEL",
                raw[
                    "embedding_request_model"
                ],
            )
        ),
        batch_size=int(
            os.environ.get(
                "RAG_EMBED_BATCH_SIZE",
                raw["batch_size"],
            )
        ),
        timeout_seconds=float(
            os.environ.get(
                "RAG_EMBED_TIMEOUT",
                raw["timeout_seconds"],
            )
        ),
        embedding_revision=(
            os.environ.get(
                "RAG_EMBEDDING_REVISION"
            )
            or None
        ),
        query_timeout_seconds=float(
            os.environ.get(
                "RAG_EMBED_QUERY_TIMEOUT",
                raw.get(
                    "query_timeout_seconds",
                    DEFAULT_QUERY_TIMEOUT_SECONDS,
                ),
            )
        ),
        query_keepalive_seconds=float(
            raw.get(
                "query_keepalive_seconds",
                DEFAULT_QUERY_KEEPALIVE_SECONDS,
            )
        ),
    )


def point_id_for_chunk(
    chunk_id: str,
) -> str:
    """
    Qdrant point IDs must be UUIDs or unsigned integers.

    Our canonical chunk IDs are SHA-derived strings, so map
    them deterministically to UUIDv5. Same chunk_id always
    produces the same Qdrant point ID.
    """
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"rag-core:dense:v1:{chunk_id}",
        )
    )


def batched(
    values: list[Any],
    batch_size: int,
) -> Iterable[list[Any]]:
    if batch_size <= 0:
        raise ValueError(
            "batch_size must be > 0"
        )

    for offset in range(
        0,
        len(values),
        batch_size,
    ):
        yield values[
            offset:
            offset + batch_size
        ]


class JinaEmbeddingClient:
    def __init__(
        self,
        config: DenseConfig,
        *,
        timeout_seconds: float | None = None,
        retries: int = 1,
        keepalive_seconds: float = BULK_KEEPALIVE_SECONDS,
    ) -> None:
        self.config = config
        timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else config.timeout_seconds
        )
        # One pooled, keep-alive client per embedder (httpx.Client is
        # thread-safe). A client per call paid a new TCP + TLS handshake to
        # the GPU host on every query embedding. `retries` repeats failed
        # connection attempts only. The limits go to the transport: httpx.Client
        # ignores its own `limits` when it is given a transport, and the
        # transport's default keeps an idle connection for 5 s only.
        limits = httpx.Limits(
            max_connections=32,
            max_keepalive_connections=16,
            keepalive_expiry=keepalive_seconds,
        )
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                timeout,
                connect=min(10.0, timeout),
            ),
            limits=limits,
            transport=httpx.HTTPTransport(
                retries=retries,
                limits=limits,
            ),
        )

    def close(self) -> None:
        self._client.close()

    def embed(
        self,
        texts: list[str],
        *,
        timeout: float | None = None,
    ) -> tuple[
        list[list[float]],
        str,
    ]:
        if not texts:
            return [], ""

        response = self._client.post(
            self.config.embedding_url,
            json={
                "input": texts,
                "model": (
                    self.config
                    .embedding_request_model
                ),
            },
            timeout=(
                timeout
                if timeout is not None
                else httpx.USE_CLIENT_DEFAULT
            ),
        )

        response.raise_for_status()

        body = response.json()

        data = body.get("data")

        if not isinstance(
            data,
            list,
        ):
            raise RuntimeError(
                "embedding response missing data[]"
            )

        data = sorted(
            data,
            key=lambda item:
                int(
                    item.get(
                        "index",
                        0,
                    )
                ),
        )

        if len(data) != len(texts):
            raise RuntimeError(
                "embedding count mismatch: "
                f"requested={len(texts)} "
                f"received={len(data)}"
            )

        vectors: list[
            list[float]
        ] = []

        for item in data:
            vector = item.get(
                "embedding"
            )

            if (
                not isinstance(
                    vector,
                    list,
                )
                or len(vector)
                != self.config.vector_size
            ):
                raise RuntimeError(
                    "invalid embedding dimension: "
                    f"expected="
                    f"{self.config.vector_size}, "
                    f"received="
                    f"{len(vector) if isinstance(vector, list) else 'invalid'}"
                )

            invalid_positions = [
                index
                for index, value in enumerate(vector)
                if (
                    isinstance(value, bool)
                    or not isinstance(
                        value,
                        (int, float),
                    )
                    or not math.isfinite(
                        float(value)
                    )
                )
            ]

            if invalid_positions:
                preview = invalid_positions[:8]

                raise RuntimeError(
                    "embedding contains invalid "
                    "non-finite/non-numeric values: "
                    f"count={len(invalid_positions)}, "
                    f"positions={preview}"
                )

            vectors.append([
                float(value)
                for value in vector
            ])

        model = str(
            body.get("model")
            or self.config.embedding_request_model
        )

        return vectors, model


def make_qdrant_client(
    config: DenseConfig,
) -> QdrantClient:
    return QdrantClient(
        url=config.qdrant_url,
        timeout=30,
    )


def ensure_collection(
    client: QdrantClient,
    config: DenseConfig,
) -> bool:
    """
    Returns True when a new collection was created.
    """

    if client.collection_exists(
        collection_name=config.collection
    ):
        info = client.get_collection(
            collection_name=config.collection
        )

        vectors = (
            info.config.params.vectors
        )

        if not isinstance(
            vectors,
            models.VectorParams,
        ):
            raise RuntimeError(
                "dense collection has unexpected "
                "named-vector configuration"
            )

        if int(vectors.size) != (
            config.vector_size
        ):
            raise RuntimeError(
                "Qdrant vector size mismatch: "
                f"collection={vectors.size} "
                f"expected={config.vector_size}"
            )

        if (
            vectors.distance
            != models.Distance.COSINE
        ):
            raise RuntimeError(
                "Qdrant distance mismatch: "
                f"{vectors.distance}"
            )

        return False

    client.create_collection(
        collection_name=config.collection,
        vectors_config=models.VectorParams(
            size=config.vector_size,
            distance=models.Distance.COSINE,
        ),
    )

    client.create_payload_index(
        collection_name=config.collection,
        field_name="doc_id",
        field_schema=(
            models.PayloadSchemaType.KEYWORD
        ),
        wait=True,
    )

    client.create_payload_index(
        collection_name=config.collection,
        field_name="chunk_id",
        field_schema=(
            models.PayloadSchemaType.KEYWORD
        ),
        wait=True,
    )

    return True


def load_chunks(
    db_path: Path,
    doc_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT
            chunk_id,
            chunk_sha256,
            doc_id,
            section_id,
            ordinal,
            heading_path_json,
            text,
            embedding_text,
            page_start,
            page_end,
            provenance_json,
            metadata_json
        FROM chunks
    """

    params: tuple[Any, ...] = ()

    if doc_id:
        sql += """
            WHERE doc_id = ?
        """

        params = (doc_id,)

    sql += """
        ORDER BY
            doc_id,
            ordinal
    """

    with connect(db_path) as conn:
        rows = conn.execute(
            sql,
            params,
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def payload_for_chunk(
    chunk: dict[str, Any],
) -> dict[str, Any]:
    return {
        "chunk_id":
            chunk["chunk_id"],

        "chunk_sha256":
            chunk["chunk_sha256"],

        "doc_id":
            chunk["doc_id"],

        "section_id":
            chunk["section_id"],

        "ordinal":
            int(chunk["ordinal"]),

        "heading_path":
            json.loads(
                chunk[
                    "heading_path_json"
                ]
                or "[]"
            ),

        "page_start":
            chunk["page_start"],

        "page_end":
            chunk["page_end"],
    }


def reset_dense_state(
    db_path: Path,
    chunk_ids: list[str],
) -> None:
    if not chunk_ids:
        return

    with connect(db_path) as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        conn.executemany(
            """
            UPDATE index_state

            SET
                dense_indexed = 0,
                embedding_model = NULL,
                embedding_revision = NULL,
                indexed_at = NULL

            WHERE chunk_id = ?
            """,
            [
                (chunk_id,)
                for chunk_id
                in chunk_ids
            ],
        )

        conn.commit()


def mark_dense_indexed(
    db_path: Path,
    chunk_ids: list[str],
    model_name: str,
    model_revision: str | None,
) -> None:
    if not chunk_ids:
        return

    with connect(db_path) as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        conn.executemany(
            """
            INSERT INTO index_state (
                chunk_id,
                dense_indexed,
                embedding_model,
                embedding_revision,
                indexed_at
            )
            VALUES (
                ?,
                1,
                ?,
                ?,
                CURRENT_TIMESTAMP
            )

            ON CONFLICT(chunk_id)
            DO UPDATE SET
                dense_indexed = 1,
                embedding_model =
                    excluded.embedding_model,
                embedding_revision =
                    excluded.embedding_revision,
                indexed_at =
                    CURRENT_TIMESTAMP
            """,
            [
                (
                    chunk_id,
                    model_name,
                    model_revision,
                )
                for chunk_id
                in chunk_ids
            ],
        )

        conn.commit()


def delete_doc_points(
    client: QdrantClient,
    config: DenseConfig,
    doc_id: str,
) -> None:
    client.delete(
        collection_name=config.collection,
        points_selector=(
            models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="doc_id",
                            match=(
                                models.MatchValue(
                                    value=doc_id
                                )
                            ),
                        )
                    ]
                )
            )
        ),
        wait=True,
    )


def index_dense(
    *,
    doc_id: str | None = None,
) -> dict[str, Any]:

    paths = RAGPaths.from_env()

    db_path = (
        paths.sqlite
        / "rag.db"
    )

    config = load_dense_config(
        root=paths.root,
    )

    chunks = load_chunks(
        db_path,
        doc_id=doc_id,
    )

    if not chunks:
        raise RuntimeError(
            "no chunks available for dense indexing"
        )

    embedder = JinaEmbeddingClient(
        config
    )

    client = make_qdrant_client(
        config
    )

    created = ensure_collection(
        client,
        config,
    )

    vectors_by_chunk: dict[
        str,
        list[float]
    ] = {}

    model_name: str | None = None

    for batch in batched(
        chunks,
        config.batch_size,
    ):
        texts = [
            str(
                chunk["embedding_text"]
            ).strip()
            for chunk in batch
        ]

        if any(
            not text
            for text in texts
        ):
            raise RuntimeError(
                "empty embedding_text encountered"
            )

        vectors, response_model = (
            embedder.embed(texts)
        )

        if model_name is None:
            model_name = response_model

        elif model_name != response_model:
            raise RuntimeError(
                "embedding model changed "
                "during indexing: "
                f"{model_name!r} -> "
                f"{response_model!r}"
            )

        for chunk, vector in zip(
            batch,
            vectors,
            strict=True,
        ):
            vectors_by_chunk[
                chunk["chunk_id"]
            ] = vector

    assert model_name is not None

    chunks_by_doc: dict[
        str,
        list[dict[str, Any]]
    ] = {}

    for chunk in chunks:
        chunks_by_doc.setdefault(
            chunk["doc_id"],
            [],
        ).append(chunk)

    indexed_count = 0

    for current_doc_id, doc_chunks in (
        chunks_by_doc.items()
    ):
        chunk_ids = [
            chunk["chunk_id"]
            for chunk in doc_chunks
        ]

        #
        # Cross-service safety:
        #
        # If Qdrant mutation fails after this point,
        # SQLite remains dense_indexed=0 instead of
        # falsely claiming an index exists.
        #
        reset_dense_state(
            db_path,
            chunk_ids,
        )

        delete_doc_points(
            client,
            config,
            current_doc_id,
        )

        points = []

        for chunk in doc_chunks:
            chunk_id = (
                chunk["chunk_id"]
            )

            points.append(
                models.PointStruct(
                    id=point_id_for_chunk(
                        chunk_id
                    ),
                    vector=(
                        vectors_by_chunk[
                            chunk_id
                        ]
                    ),
                    payload=(
                        payload_for_chunk(
                            chunk
                        )
                    ),
                )
            )

        # Qdrant rejects requests over its payload limit (32 MiB by default)
        # with HTTP 400; one request per document failed for large guides
        # (a 2,635-chunk Cisco guide), after the whole document had been
        # embedded. Upsert in bounded batches.
        for start in range(
            0,
            len(points),
            UPSERT_BATCH_POINTS,
        ):
            client.upsert(
                collection_name=config.collection,
                points=points[
                    start:
                    start + UPSERT_BATCH_POINTS
                ],
                wait=True,
            )

        mark_dense_indexed(
            db_path,
            chunk_ids,
            model_name,
            config.embedding_revision,
        )

        indexed_count += len(
            doc_chunks
        )

    count = client.count(
        collection_name=config.collection,
        exact=True,
    ).count

    return {
        "collection":
            config.collection,

        "collection_created":
            created,

        "vector_size":
            config.vector_size,

        "embedding_model":
            model_name,

        "embedding_revision":
            config.embedding_revision,

        "chunks_indexed":
            indexed_count,

        "qdrant_points":
            int(count),
    }
