from __future__ import annotations

import json
import math
import os
import sqlite3
import threading

from typing import Any

import numpy as np
import torch

from pylate import retrieve

from rag_core.late_interaction import (
    load_late_interaction_config,
    make_colbert_model,
    open_late_interaction_index,
)
from rag_core.paths import RAGPaths
from rag_core.retrieval_types import (
    QueryVariant,
    SearchHit,
)


INSTRUCT_PREFIX = (
    "Instruct: Given a user's reasoning followed by a web search query, "
    "retrieve relevant passages that answer the query while incorporating the user's reasoning"
    "\\nQuery:"
)

INSTRUCT_TEXT = (
    "Instruct: Given a user's reasoning followed by a web search query, "
    "retrieve relevant passages that answer the query while incorporating the user's reasoning"
)


def build_query_text(
    text: str,
    template: str = "agent",
) -> str:
    """ColBERT query text for late_interaction.json "query_template":

    agent        the original bytes: INSTRUCT_PREFIX ends in a literal backslash-n, so the model saw
                 "...reasoning\\nQuery:Reasoning: Empty\\n\\nQuery: <q>" (default, unchanged behaviour)
    agent-fixed  the same instruction with a real newline and a single "Query:" line
    plain        the query alone; with "Reasoning: Empty" the fixed template supplied most of each
                 document's MaxSim score, so the leg carried little query-specific signal
    """
    text = text.strip()

    if not text:
        return ""

    if template == "plain":
        return text

    if template == "agent-fixed":
        return (
            f"{INSTRUCT_TEXT}\n"
            f"Reasoning: Empty\n\n"
            f"Query: {text}"
        )

    return (
        f"{INSTRUCT_PREFIX}"
        f"Reasoning: Empty\n\n"
        f"Query: {text}"
    )


class ColBERTRetriever:
    name = "colbert"

    def __init__(
        self,
        paths: RAGPaths,
    ) -> None:
        self.paths = paths

        self.config = (
            load_late_interaction_config(
                root=paths.root,
            )
        )

        self.model = make_colbert_model(
            self.config
        )

        # make_colbert_model applies num_threads (sized for index builds);
        # serving uses query_num_threads when configured.
        if self.config.query_num_threads is not None:
            torch.set_num_threads(
                self.config.query_num_threads
            )

        self.index = (
            open_late_interaction_index(
                paths,
                self.config,
            )
        )

        self.retriever = retrieve.ColBERT(
            index=self.index
        )

        # RetrievalOrchestrator fans query variants out concurrently.
        # Keep ColBERT serialized so the shared CPU model/index does not
        # oversubscribe the NUMA-local 12-thread worker.
        self._search_lock = threading.Lock()

    def _query_text(
        self,
        text: str,
    ) -> str:
        return build_query_text(
            text,
            self.config.query_template,
        )

    def _current_generation(
        self,
    ) -> str | None:
        current = (
            self.paths.pylate
            / "current"
        )

        if not current.is_symlink():
            return None

        return os.readlink(
            current
        )

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

        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row

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

                    s.late_interaction_indexed,
                    s.colbert_model,
                    s.colbert_revision

                FROM chunks AS c

                JOIN index_state AS s
                    ON s.chunk_id = c.chunk_id

                WHERE
                    c.chunk_id IN (
                        {placeholders}
                    )

                    AND
                    s.late_interaction_indexed = 1
                """,
                tuple(chunk_ids),
            ).fetchall()

        return {
            str(row["chunk_id"]):
                dict(row)

            for row in rows
        }

    def search(
        self,
        variant: QueryVariant,
        *,
        limit: int = 50,
    ) -> list[SearchHit]:
        with self._search_lock:
            return self._search_locked(
                variant,
                limit=limit,
            )

    def _search_locked(
        self,
        variant: QueryVariant,
        *,
        limit: int = 50,
    ) -> list[SearchHit]:
        if limit <= 0:
            return []

        query_text = self._query_text(
            variant.text
        )

        if not query_text:
            return []

        query_embeddings = (
            self.model.encode(
                [query_text],
                batch_size=(
                    self.config.query_batch_size
                ),
                is_query=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        )

        if len(query_embeddings) != 1:
            raise RuntimeError(
                "ColBERT query encoding "
                "returned unexpected count"
            )

        query_matrix = np.asarray(
            query_embeddings[0]
        )

        if query_matrix.ndim != 2:
            raise RuntimeError(
                "ColBERT query embedding "
                "must be a 2-D token matrix"
            )

        if (
            query_matrix.shape[1]
            != self.config.embedding_dimension
        ):
            raise RuntimeError(
                "ColBERT query dimension mismatch: "
                f"expected="
                f"{self.config.embedding_dimension} "
                f"actual="
                f"{query_matrix.shape[1]}"
            )

        if not np.isfinite(
            query_matrix
        ).all():
            raise RuntimeError(
                "ColBERT query embedding "
                "contains non-finite values"
            )

        batches = (
            self.retriever.retrieve(
                queries_embeddings=(
                    query_embeddings
                ),
                k=limit,
                device=self.config.device,
            )
        )

        if len(batches) != 1:
            raise RuntimeError(
                "ColBERT retrieval returned "
                "unexpected batch count"
            )

        results = batches[0]

        chunk_ids: list[str] = []

        for result in results:
            if not isinstance(
                result,
                dict,
            ):
                raise RuntimeError(
                    "ColBERT retrieval result "
                    "is not a dict"
                )

            chunk_id = result.get(
                "id"
            )

            score = result.get(
                "score"
            )

            if not isinstance(
                chunk_id,
                str,
            ):
                raise RuntimeError(
                    "ColBERT result has "
                    "invalid id"
                )

            try:
                score_value = float(
                    score
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise RuntimeError(
                    "ColBERT result has "
                    "invalid score"
                ) from exc

            if not math.isfinite(
                score_value
            ):
                raise RuntimeError(
                    "ColBERT result has "
                    "non-finite score"
                )

            chunk_ids.append(
                chunk_id
            )

        rows = self._load_chunks(
            chunk_ids
        )

        generation = (
            self._current_generation()
        )

        hits: list[SearchHit] = []

        for result in results:
            chunk_id = str(
                result["id"]
            )

            row = rows.get(
                chunk_id
            )

            if row is None:
                continue

            score = float(
                result["score"]
            )

            try:
                heading_path = (
                    json.loads(
                        row[
                            "heading_path_json"
                        ]
                        or "[]"
                    )
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

                "colbert": {
                    "score":
                        score,

                    "query_model":
                        self.config.model,

                    "indexed_model":
                        row[
                            "colbert_model"
                        ],

                    "indexed_revision":
                        row[
                            "colbert_revision"
                        ],

                    "generation":
                        generation,

                    "embedding_dimension":
                        (
                            self.config
                            .embedding_dimension
                        ),
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

                    doc_id=str(
                        row["doc_id"]
                    ),

                    rank=(
                        len(hits) + 1
                    ),

                    score=score,

                    text=str(
                        row["text"]
                    ),

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
