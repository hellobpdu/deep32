"""Live service status for GET /v2/status.

/health reports what started. This checks what answers now: BM25 runs a real
FTS query, dense embeds a query and searches its collection, the reranker
reranks one document, and the KB database and the structure index (section
line ranges, which the /v2 reading endpoints need) are opened. Probes run in
parallel with a short deadline, and their results are cached for a few
seconds so a burst of status calls costs one round of probes. The dense
circuit breaker lives in this process and is read on every call, so the
status changes the moment searches start skipping dense. Errors are reported
as kinds (timeout, connect, http_502, ...), never as messages that could
carry internal URLs or host names. "settings" says what the root serves with
(the reranker as it started, search_many, grep, admission, the dense
search settings when a root sets any, and fold_identical when a root sets it
true), so a switch to a root whose overlay
changes them can be checked.
"""
from __future__ import annotations

import sqlite3
import threading
import time

from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    TimeoutError as FutureTimeout,
)
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rag_core.api_v2 import (
    fold_identical,
    grep_settings,
    search_many_settings,
)
from rag_core.corpus import (
    corpus_fingerprint,
)
from rag_core.db import (
    DEFAULT_FTS,
    OPTIN_FTS,
    is_split,
)
from rag_core.errors import (
    error_kind,
)
from rag_core.paths import RAGPaths
from rag_core.retrievers.dense import (
    DenseSearchSettings,
)
from rag_core.structure import (
    has_structure,
)


CACHE_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 2.0
# a probe's own client timeout covers one request; allow a little more for the whole probe
PROBE_GRACE_SECONDS = 0.5


def _read_only(
    path: Path,
) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path.name} does not exist"
        )

    return sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=PROBE_TIMEOUT_SECONDS,
    )


def corpus_stats(
    db_path: Path,
) -> dict[str, Any]:
    """Counts and fingerprint of rag.db; also runs a real FTS query. "structure"
    counts the structure index (rag_core.structure), None when the root has none."""
    conn = _read_only(db_path)
    try:
        shas = [
            row[0]
            for row in conn.execute(
                "SELECT sha256 FROM documents"
            )
        ]
        n_chunks = conn.execute(
            "SELECT COUNT(*) FROM chunks"
        ).fetchone()[0]
        conn.execute(
            "SELECT rowid FROM chunk_fts WHERE chunk_fts MATCH ? LIMIT 1",
            ('"the" OR "rfc"',),
        ).fetchall()
        # the BM25 tables and their rows: chunk_fts, and chunk_fts_optin in a root that keeps
        # the opt-in doc types' chunks apart (rag_core.db.split_optin_fts)
        bm25_tables = {
            table: conn.execute(
                f"SELECT count(*) FROM {table}_docsize"
            ).fetchone()[0]
            for table in (
                (DEFAULT_FTS, OPTIN_FTS)
                if is_split(conn)
                else (DEFAULT_FTS,)
            )
        }
        structure = (
            {
                "documents": conn.execute(
                    "SELECT COUNT(*) FROM doc_sources"
                ).fetchone()[0],
                "sections": conn.execute(
                    "SELECT COUNT(*) FROM doc_sections"
                ).fetchone()[0],
            }
            if has_structure(conn)
            else None
        )
    finally:
        conn.close()

    return {
        "n_docs": len(shas),
        "n_chunks": int(n_chunks),
        "corpus_fingerprint": (
            corpus_fingerprint(shas)
            if shas
            else None
        ),
        "structure": structure,
        "bm25_tables": bm25_tables,
    }


def kb_stats(
    kb_path: Path,
) -> dict[str, int]:
    """Row count per table of kb.sqlite."""
    conn = _read_only(kb_path)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        return {
            name: conn.execute(
                f'SELECT COUNT(*) FROM "{name}"'
            ).fetchone()[0]
            for name in names
        }
    finally:
        conn.close()


class StatusService:

    def __init__(
        self,
        orchestrator,
        paths: RAGPaths,
        *,
        generation: str | None = None,
        serving: dict[str, Any] | None = None,
        cache_seconds: float = CACHE_SECONDS,
        probe_timeout: float = PROBE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.orchestrator = orchestrator
        self.paths = paths
        self.generation = generation
        # the admission rag_core.api started with: settings.serving
        self.serving = {
            "max_concurrent_searches": (serving or {}).get("max_concurrent_searches"),
            "max_queued_searches": (serving or {}).get("max_queued_searches"),
        }
        self.cache_seconds = cache_seconds
        self.probe_timeout = probe_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._probed: dict[str, Any] | None = None
        self._probed_at = 0.0
        # A probe stuck in a client without a hard deadline keeps its worker;
        # later probes then time out in the queue instead of piling up threads.
        self._pool = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="status-probe",
        )

    def close(self) -> None:
        self._pool.shutdown(
            wait=False,
            cancel_futures=True,
        )

    def status(self) -> dict[str, Any]:
        # One round of probes at a time; concurrent callers share it. The
        # report around it, breaker included, is built fresh on every call.
        with self._lock:
            if (
                self._probed is None
                or self._clock() - self._probed_at >= self.cache_seconds
            ):
                self._probed = self._probe()
                self._probed_at = self._clock()
            probed = self._probed
        return self._report(probed)

    def _outcome(
        self,
        future: Future | None,
    ) -> tuple[Any, str | None]:
        """(result, None) or (None, error kind)."""
        if future is None:
            return None, None
        try:
            return future.result(timeout=self.probe_timeout + PROBE_GRACE_SECONDS), None
        except FutureTimeout:
            future.cancel()
            return None, "timeout"
        except Exception as exc:
            return None, error_kind(exc)

    def _probe(self) -> dict[str, Any]:
        """One round of live probes: {probe: (result, error kind)}, and checked_at."""
        orch = self.orchestrator
        dense = orch.retrievers.get("dense")
        reranker = orch.reranker
        submit = self._pool.submit

        # all probes in flight at once: the round costs the slowest one
        jobs = {
            "corpus": submit(corpus_stats, self.paths.sqlite / "rag.db"),
            "kb": submit(kb_stats, self.paths.root / "kb" / "kb.sqlite"),
            "dense": (
                submit(dense.probe, self.probe_timeout)
                if dense is not None
                else None
            ),
            "reranker": (
                submit(reranker.probe, self.probe_timeout)
                if reranker is not None
                else None
            ),
        }
        probed: dict[str, Any] = {
            name: self._outcome(job)
            for name, job in jobs.items()
        }
        probed["checked_at"] = datetime.now(
            timezone.utc
        ).isoformat(
            timespec="seconds"
        )
        return probed

    def _report(
        self,
        probed: dict[str, Any],
    ) -> dict[str, Any]:
        orch = self.orchestrator
        started = orch.retrievers
        dense = started.get("dense")
        dense_enabled = "dense" in orch.enabled
        breaker = orch.breakers.get("dense")
        reranker = orch.reranker
        rerank_enabled = bool(getattr(orch, "rerank_enabled", reranker is not None))
        rerank_breaker = getattr(reranker, "breaker", None)
        rerank_breaker_state = (
            rerank_breaker.snapshot()
            if rerank_breaker is not None
            else {"state": None, "last_error_kind": None}
        )

        corpus, corpus_error = probed["corpus"]
        tables, kb_error = probed["kb"]
        dense_probe, dense_error = probed["dense"]
        rerank_probe, rerank_error = probed["reranker"]
        structure = (corpus or {}).get("structure") or {}

        breaker_state = (
            breaker.snapshot()
            if breaker is not None
            else {"state": None, "last_error_kind": None}
        )

        if dense is None and dense_enabled:
            dense_error = "startup"
        elif dense_error is None and breaker_state["state"] not in (None, "closed"):
            dense_error = breaker_state["last_error_kind"]

        if reranker is None and rerank_enabled:
            rerank_error = "startup"
        elif rerank_error is None and rerank_breaker_state["state"] not in (None, "closed"):
            rerank_error = rerank_breaker_state["last_error_kind"]

        backends: dict[str, Any] = {
            "bm25": {
                "ok": (
                    "bm25" in started
                    and corpus is not None
                    and corpus["n_chunks"] > 0
                ),
                "last_error_kind": corpus_error,
                # rows per BM25 table (corpus_stats); {} when the probe failed
                "tables": (corpus or {}).get("bm25_tables") or {},
            },
            "dense": {
                "enabled": dense_enabled,
                "ok": (
                    dense_probe is not None
                    if dense_enabled
                    else None
                ),
                "latency_ms": (
                    dense_probe or {}
                ).get("latency_ms"),
                "breaker": breaker_state["state"],
                "last_error_kind": dense_error,
            },
            "reranker": {
                "enabled": rerank_enabled,
                "ok": (
                    rerank_probe is not None
                    if rerank_enabled
                    else None
                ),
                "latency_ms": (
                    rerank_probe or {}
                ).get("latency_ms"),
                "last_error_kind": rerank_error,
                **(
                    {"breaker": rerank_breaker_state["state"]}
                    if rerank_breaker is not None
                    else {}
                ),
            },
            "kb": {
                "ok": bool(tables),
                "tables": tables or {},
                "last_error_kind": kb_error,
            },
            # section line ranges: /v2 section, outline, documents,
            # verify_quote and grep answer 503 without them
            "structure": {
                "ok": bool(structure.get("documents")),
                "documents": structure.get("documents", 0),
                "sections": structure.get("sections", 0),
            },
        }

        if "colbert" in orch.enabled:
            backends["colbert"] = {
                "enabled": True,
                "ok": "colbert" in started,
            }

        # what a search cannot use right now; a dense leg behind an open
        # breaker is skipped by searches even if its probe just answered
        degraded = [
            name
            for name, backend in backends.items()
            if backend.get("enabled", True)
            and not backend["ok"]
        ]
        if (
            "dense" not in degraded
            and breaker_state["state"] not in (None, "closed")
        ):
            degraded.append("dense")
        if (
            "reranker" not in degraded
            and rerank_breaker_state["state"] not in (None, "closed")
        ):
            degraded.append("reranker")

        # the doc types searches leave out unless named (retrieval.json opt_in_doc_types, checked
        # at start by rag_core.api_v2.opt_in_doc_types); [] when none
        config = getattr(orch, "config", None) or {}
        opt_in = sorted(set(config.get("opt_in_doc_types") or []))

        # serving.json pipeline (rag_core.pipeline): the rerank gate's counters, read
        # fresh, and how often the reranker's breaker opened since the start
        # (scripts/bench_pipeline.py); only when the root runs one
        gate = getattr(orch, "rerank_gate", None)
        pipeline = (
            {"pipeline": {
                "rerank_gate": gate.snapshot(),
                "reranker_breaker_opens": rerank_breaker_state.get("opens"),
            }}
            if gate is not None
            else {}
        )

        # what the root serves with, for an orchestrator that has a retrieval
        # config (every RetrievalOrchestrator does)
        settings = (
            {"settings": self._settings(reranker, config, dense)}
            if getattr(orch, "config", None) is not None
            else {}
        )

        return {
            "ok": backends["bm25"]["ok"],
            "generation": self.generation,
            "corpus_fingerprint": (corpus or {}).get("corpus_fingerprint"),
            "n_docs": (corpus or {}).get("n_docs"),
            "n_chunks": (corpus or {}).get("n_chunks"),
            "opt_in_doc_types": opt_in,
            "backends": backends,
            "degraded": degraded,
            "checked_at": probed["checked_at"],
            **settings,
            **pipeline,
        }

    def _settings(
        self,
        reranker,
        config: dict[str, Any],
        dense=None,
    ) -> dict[str, Any]:
        """The reranker as it started (null when none runs: off, or it could not
        start), search_many's and grep's settings as rag_core.api checked them at
        start (search_many.parallel: the queries retrieved at once, 0 one after
        another), and the admission. Then "dense": the dense retriever's search
        settings as it started (retrieval.json "dense", DenseSearchSettings: only
        the keys that are set), and only when one is set; last "fold_identical":
        true, only when retrieval.json sets it true (rag_core.api_v2.fold_identical).
        Without them the block is exactly the one before (the production chain
        compares it as a string)."""
        many = search_many_settings(config)
        candidates = getattr(reranker, "candidates", None)
        search = getattr(dense, "search_settings", None)
        shown = (
            search.shown()
            if isinstance(search, DenseSearchSettings)
            else None
        )
        return {
            "reranker": (
                {
                    "candidates": candidates,
                    "search_many_candidates": (
                        getattr(reranker, "search_many_candidates", None)
                        or candidates
                    ),
                    "max_chars": getattr(reranker, "max_chars", None),
                    "window": getattr(reranker, "window", None),
                }
                if reranker is not None
                else None
            ),
            "search_many": {
                "parallel": many.workers,
                "rerank": many.rerank,
            },
            "grep": {
                "regex_engine": grep_settings(config).regex_engine,
            },
            "serving": dict(self.serving),
            **({"dense": shown} if shown else {}),
            **({"fold_identical": True} if fold_identical(config) else {}),
        }
