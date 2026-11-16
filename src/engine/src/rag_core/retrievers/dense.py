from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from qdrant_client import models

from rag_core.db import connect
from rag_core.dense import (
    JinaEmbeddingClient,
    load_dense_config,
    make_qdrant_client,
)
from rag_core.paths import RAGPaths
from rag_core.retrieval_types import (
    QueryVariant,
    SearchHit,
)


SEARCH_KEYS = ("exact", "hnsw_ef")


@dataclass(frozen=True)
class DenseSearchSettings:
    """retrieval.json "dense": {...} (a root's overlay, e.g. scripts/derive_root.py --set
    retrieval.dense.exact=true): how the dense leg's Qdrant searches run. They apply to every
    serve-time search of the collection: /search, /v2/search and /v2/search_many (with
    search_many's batched query vectors too), filtered (doc_ids) or not, with the opt-in
    exclusion (exclude_doc_ids) or not, and the /v2/status probe. Unset (the default), no
    search params are sent: every request is the one deep32-engine sent before these settings.

        "exact": false | true
            true: Qdrant scans every vector (SearchParams exact=true), the true nearest
            neighbours with no HNSW approximation, at the cost of a full scan per search
            (tech radar V1; scripts/ann_audit.py measures both).
        "hnsw_ef": null | N (an integer >= 1)
            the HNSW beam width of each search (SearchParams hnsw_ef=N); null: Qdrant's own
            default. Not together with exact true, which searches without the graph.

    /v2/status shows {"dense": {...}} in its settings, with the keys that are set, only when
    one is (rag_core.status); unset, the settings block is the one before.
    """
    exact: bool = False
    hnsw_ef: int | None = None

    def search_params(self) -> models.SearchParams | None:
        """The Qdrant SearchParams, None when nothing is set (no search_params are passed)."""
        if self.exact:
            return models.SearchParams(exact=True)
        if self.hnsw_ef is not None:
            return models.SearchParams(hnsw_ef=self.hnsw_ef)
        return None

    def shown(self) -> dict[str, Any] | None:
        """What /v2/status settings show: the keys that are set, None when none is."""
        out: dict[str, Any] = {}
        if self.exact:
            out["exact"] = True
        if self.hnsw_ef is not None:
            out["hnsw_ef"] = self.hnsw_ef
        return out or None


def dense_search_settings(
    options: Any,
) -> DenseSearchSettings:
    """retrieval.json "dense" -> DenseSearchSettings (None: the defaults). ValueError names a value
    they cannot take; the orchestrator checks them before any retriever starts, and
    scripts/derive_root.py before it writes a derived root."""
    if options is None:
        return DenseSearchSettings()
    if not isinstance(options, dict) or set(options) - set(SEARCH_KEYS):
        raise ValueError(
            f"retrieval.json dense must be an object of {list(SEARCH_KEYS)} (the dense "
            f"retriever's search settings; its service is configured in dense.json), not {options!r}"
        )
    exact = options.get("exact", False)
    if not isinstance(exact, bool):
        raise ValueError(f"retrieval.json dense.exact must be true or false, not {exact!r}")
    hnsw_ef = options.get("hnsw_ef")
    if hnsw_ef is not None and (
        isinstance(hnsw_ef, bool) or not isinstance(hnsw_ef, int) or hnsw_ef < 1
    ):
        raise ValueError(f"retrieval.json dense.hnsw_ef must be null or an integer of 1 or more, not {hnsw_ef!r}")
    if exact and hnsw_ef is not None:
        raise ValueError(
            "retrieval.json dense: exact true searches without the HNSW graph, so hnsw_ef "
            f"{hnsw_ef} would do nothing; set one of them"
        )
    return DenseSearchSettings(exact=exact, hnsw_ef=hnsw_ef)


def qdrant_filter(
    doc_ids: frozenset[str] | None,
    exclude_doc_ids: frozenset[str] | None,
) -> models.Filter | None:
    """The dense leg's Qdrant filter: only these documents (API v2 filters), not these (opt-in doc
    types); None for neither. doc_id has a keyword payload index (a point's payload names no
    doc_type). scripts/ann_audit.py builds the default search's filter with it."""
    if doc_ids is None and not exclude_doc_ids:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(
                key="doc_id",
                match=models.MatchAny(any=sorted(doc_ids)),
            )
        ] if doc_ids is not None else None,
        must_not=[
            models.FieldCondition(
                key="doc_id",
                match=models.MatchAny(any=sorted(exclude_doc_ids)),
            )
        ] if exclude_doc_ids else None,
    )


class DenseRetriever:
    name = "dense"

    # retrieval.json "dense" (DenseSearchSettings): the defaults unless the root sets them. A
    # class default, so a retriever made without __init__ (tests' stand-ins) searches as before
    search_settings = DenseSearchSettings()
    # the orchestrator checks the options with it before anything starts
    check_options = staticmethod(dense_search_settings)

    def __init__(
        self,
        paths: RAGPaths,
        options: dict | None = None,
    ) -> None:
        self.paths = paths
        if options is not None:
            self.search_settings = dense_search_settings(options)
        self.config = load_dense_config(
            root=paths.root,
        )

        # Query-time embedding is one short text: fail fast (and let fusion
        # degrade to the other retrievers) instead of waiting out the
        # 120 s bulk-indexing timeout. No connection retry either: an
        # unreachable GPU host would cost twice the timeout per query.
        self.embedder = JinaEmbeddingClient(
            self.config,
            timeout_seconds=self.config.query_timeout_seconds,
            retries=0,
            keepalive_seconds=self.config.query_keepalive_seconds,
        )

        self.client = make_qdrant_client(
            self.config
        )

    def _search_params(self) -> dict[str, Any]:
        """query_points' search_params when retrieval.json "dense" sets any; else nothing at all,
        so the request is exactly the one sent before these settings existed."""
        params = self.search_settings.search_params()
        return {"search_params": params} if params is not None else {}

    def probe(
        self,
        timeout: float,
    ) -> dict[str, Any]:
        """Live check for /v2/status: one query embedding and a one-point
        search of the collection, the path a real query takes. Raises when
        either backend fails."""
        started = time.perf_counter()

        vectors, _ = self.embedder.embed(
            [self._query_text("status probe")],
            timeout=timeout,
        )

        self.client.query_points(
            collection_name=self.config.collection,
            query=vectors[0],
            limit=1,
            with_payload=False,
            with_vectors=False,
            **self._search_params(),
        )

        return {
            "latency_ms": round(
                (time.perf_counter() - started) * 1000,
                1,
            ),
        }

    @staticmethod
    def _query_text(
        text: str,
    ) -> str:
        text = text.strip()

        if not text:
            return ""

        if text.lower().startswith(
            "query:"
        ):
            return text

        return f"Query: {text}"

    def embed_queries(
        self,
        texts: list[str],
    ) -> dict[str, tuple[list[float], str]]:
        """Several variant texts embedded in one TEI call (API v2
        search_many): {the text search() embeds: (vector, model)}, for
        search(embedded=...). Texts with nothing to embed are left out."""
        wanted = list(
            dict.fromkeys(
                query_text
                for query_text in map(
                    self._query_text,
                    texts,
                )
                if query_text
            )
        )

        if not wanted:
            return {}

        vectors, response_model = (
            self.embedder.embed(
                wanted
            )
        )

        if len(vectors) != len(wanted):
            raise RuntimeError(
                "dense query embedding "
                "returned unexpected count"
            )

        return {
            query_text: (vector, response_model)
            for query_text, vector in zip(
                wanted,
                vectors,
            )
        }

    def _load_chunks(
        self,
        chunk_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not chunk_ids:
            return {}

        placeholders = ",".join(
            "?"
            for _ in chunk_ids
        )

        db = (
            self.paths.sqlite
            / "rag.db"
        )

        with connect(db) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    c.chunk_id,
                    c.doc_id,
                    c.text,
                    c.heading_path_json,
                    c.page_start,
                    c.page_end,
                    c.metadata_json,
                    s.dense_indexed,
                    s.embedding_model,
                    s.embedding_revision
                FROM chunks AS c
                JOIN index_state AS s
                    ON s.chunk_id = c.chunk_id
                WHERE
                    c.chunk_id IN (
                        {placeholders}
                    )
                    AND s.dense_indexed = 1
                """,
                tuple(chunk_ids),
            ).fetchall()

        return {
            row["chunk_id"]: dict(row)
            for row in rows
        }

    def search(
        self,
        variant: QueryVariant,
        *,
        limit: int = 50,
        doc_ids: frozenset[str] | None = None,
        timings: dict[str, float] | None = None,
        embedded: dict[str, tuple[list[float], str]] | None = None,
        exclude_doc_ids: frozenset[str] | None = None,
    ) -> list[SearchHit]:
        """`timings`, when given, receives embed_ms (TEI), qdrant_ms and load_ms (SQLite): where
        the dense leg's time goes (the API shows them in timings_ms). `embedded` (embed_queries)
        may hold this query's vector already: then TEI is not called, and there is no embed_ms.
        `doc_ids` / `exclude_doc_ids`: only these documents / not these (API v2 filters, opt-in
        doc types), both in the Qdrant filter."""
        if limit <= 0 or doc_ids is not None and not doc_ids:
            return []

        query_text = self._query_text(
            variant.text
        )

        if not query_text:
            return []

        clock = time.perf_counter
        spent = timings if timings is not None else {}
        started = clock()

        if embedded and query_text in embedded:
            vector, response_model = embedded[query_text]
            vectors = [vector]
        else:
            vectors, response_model = (
                self.embedder.embed(
                    [query_text]
                )
            )
            spent["embed_ms"] = round((clock() - started) * 1000, 1)

        if len(vectors) != 1:
            raise RuntimeError(
                "dense query embedding "
                "returned unexpected count"
            )

        # only these documents (API v2 filters), not these (opt-in doc types)
        only = qdrant_filter(
            doc_ids,
            exclude_doc_ids,
        )

        started = clock()
        # only the chunk id is read from a point; the rest of the row comes from SQLite; the
        # search params only when retrieval.json "dense" sets them (DenseSearchSettings)
        response = (
            self.client.query_points(
                collection_name=(
                    self.config.collection
                ),
                query=vectors[0],
                query_filter=only,
                limit=limit,
                with_payload=["chunk_id"],
                with_vectors=False,
                **self._search_params(),
            )
        )
        spent["qdrant_ms"] = round((clock() - started) * 1000, 1)

        points = list(
            response.points
        )

        chunk_ids: list[str] = []

        for point in points:
            payload = point.payload or {}

            chunk_id = payload.get(
                "chunk_id"
            )

            if isinstance(
                chunk_id,
                str,
            ):
                chunk_ids.append(
                    chunk_id
                )

        started = clock()
        rows = self._load_chunks(
            chunk_ids
        )
        spent["load_ms"] = round((clock() - started) * 1000, 1)

        hits: list[SearchHit] = []

        for point in points:
            payload = point.payload or {}

            chunk_id = payload.get(
                "chunk_id"
            )

            if not isinstance(
                chunk_id,
                str,
            ):
                continue

            row = rows.get(
                chunk_id
            )

            if row is None:
                continue

            try:
                heading_path = json.loads(
                    row[
                        "heading_path_json"
                    ]
                    or "[]"
                )
            except Exception:
                heading_path = []

            try:
                metadata = json.loads(
                    row["metadata_json"]
                    or "{}"
                )
            except Exception:
                metadata = {}

            metadata = {
                **metadata,
                "dense": {
                    "score":
                        float(point.score),

                    "point_id":
                        str(point.id),

                    "query_model":
                        response_model,

                    "indexed_model":
                        row[
                            "embedding_model"
                        ],

                    "embedding_revision":
                        row[
                            "embedding_revision"
                        ],
                },
            }

            hits.append(
                SearchHit(
                    retriever=self.name,
                    variant_id=(
                        variant.variant_id
                    ),
                    purpose=(
                        variant.purpose
                    ),
                    chunk_id=chunk_id,
                    doc_id=row["doc_id"],
                    rank=len(hits) + 1,
                    score=float(
                        point.score
                    ),
                    text=row["text"],
                    heading_path=(
                        heading_path
                    ),
                    page_start=(
                        row["page_start"]
                    ),
                    page_end=(
                        row["page_end"]
                    ),
                    metadata=metadata,
                )
            )

        return hits
