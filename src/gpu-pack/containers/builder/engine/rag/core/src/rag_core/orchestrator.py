from __future__ import annotations

import inspect
import logging
import time
import uuid

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)

from rag_core.breaker import (
    OPEN,
    CircuitBreaker,
)

from rag_core.config import (
    load_config,
)

from rag_core.errors import (
    error_kind,
    public_error,
)

from rag_core.db import (
    connect,
)

from rag_core.fusion import (
    demoted,
    passage_class_demote,
    rrf_fuse,
)

from rag_core.paths import (
    RAGPaths,
)

from rag_core.query_plan import (
    TechnicalVariantSettings,
    build_query_plan,
    technical_variant_settings,
    technical_vocabulary,
)

from rag_core.rerank import (
    Reranker,
    covered_titles_of,
    title_lines,
)

from rag_core.traces import (
    TraceWriter,
)

from rag_core.retrieval_types import (
    QueryVariant,
    SearchHit,
)

from rag_core.retrievers.dense import (
    DenseRetriever,
)
from rag_core.retrievers.lexical import (
    LexicalRetriever,
)


MAIN_PURPOSES = {
    "primary",
    "technical",
    "support",
    "version",
}

CHALLENGE_PURPOSES = {
    "exception",
    "contradiction",
}

def colbert_retriever(
    paths: RAGPaths,
):
    """The late-interaction retriever, made (and its module imported) only for a root whose
    "retrievers" names colbert: rag_core.retrievers.colbert imports torch and pylate at load,
    which a host that serves bm25 and dense does not need. A failed import is a failed start
    of this retriever alone: it is reported as degraded, like a missing index."""
    from rag_core.retrievers.colbert import (
        ColBERTRetriever,
    )

    return ColBERTRetriever(paths)


RETRIEVER_FACTORIES = {
    "bm25": LexicalRetriever,
    "dense": DenseRetriever,
    "colbert": colbert_retriever,
}

# Retrievers behind a remote service get a circuit breaker (rag_core.breaker).
BREAKER_RETRIEVERS = (
    "dense",
)

logger = logging.getLogger(__name__)


class RetrievalOrchestrator:

    # serving.json pipeline.rerank_gate (rag_core.pipeline): the FIFO gate every rerank of this
    # process passes, set by rag_core.api at start; None (the default): no gate, as before
    rerank_gate = None

    # retrieval.json technical_variant (rag_core.query_plan): the technical variant's weight and
    # token rule, and the long words rule v2 keeps; class defaults, so an orchestrator made
    # without __init__ (tests' stand-ins) plans as before
    technical = TechnicalVariantSettings()
    vocabulary: frozenset[str] = frozenset()

    # retrieval.json passage_class.demote (rag_core.fusion): the passage classes moved behind the
    # other candidates after the fusion; () (the default, and on a root without the table): none
    demote: tuple[str, ...] = ()

    def __init__(
        self,
        paths: RAGPaths,
    ):
        self.paths = paths

        # config/retrieval.json, overridden by $RAG_ROOT/config/retrieval.json;
        # RAG_RETRIEVAL_CONFIG names a file that replaces both (rag_core.config).
        self.config = load_config(
            "retrieval",
            root=paths.root,
            env_var="RAG_RETRIEVAL_CONFIG",
        )

        # "retrievers" in retrieval.json selects the engines (default: all).
        # BM25 is the floor and must start; dense or ColBERT failing to start
        # (index missing, model load error) degrades the service instead of
        # killing it, and the failure is reported in capabilities().
        enabled = list(
            self.config.get(
                "retrievers",
                list(RETRIEVER_FACTORIES),
            )
        )
        unknown = set(enabled) - set(RETRIEVER_FACTORIES)
        if unknown:
            raise ValueError(
                f"unknown retrievers in config: {sorted(unknown)}"
            )

        # An object under a retriever's name holds that retriever's options
        # (e.g. "bm25": {"drop_stopwords": true}, "dense": {"exact": true}).
        # Check them before starting anything: a retriever that takes no
        # options used to raise TypeError below, which was reported as
        # "degraded" and silently dropped it; one with a check_options
        # (dense: rag_core.retrievers.dense.dense_search_settings) refuses a
        # value it cannot take here, so the service does not start with it.
        for name in enabled:
            options = self.config.get(name)
            if options is None:
                continue
            if not isinstance(options, dict):
                raise ValueError(
                    f"retrieval.json {name!r} must be an object of "
                    f"{name} options"
                )
            takes_options = "options" in inspect.signature(
                RETRIEVER_FACTORIES[name]
            ).parameters
            if not takes_options:
                raise ValueError(
                    f"retrieval.json {name!r}: the {name} retriever takes "
                    "no options here (dense's service is configured in "
                    "dense.json, colbert in late_interaction.json)"
                )
            check = getattr(
                RETRIEVER_FACTORIES[name],
                "check_options",
                None,
            )
            if check is not None:
                check(options)

        # the technical variant's settings: a value they cannot take stops the start
        self.technical = technical_variant_settings(self.config)
        if self.technical.tokens == "v2":
            self.vocabulary = technical_vocabulary(paths.root / "kb" / "kb.sqlite")
            if not self.vocabulary:
                logger.warning(
                    "technical_variant.tokens v2: no command or glossary words in the knowledge "
                    "base, so no plain long word is kept"
                )

        # the passage classes to demote: a value the setting cannot take stops the start; a root
        # whose rag.db has no passage_class table has nothing to demote by
        self.demote = passage_class_demote(self.config)
        if self.demote:
            with connect(paths.sqlite / "rag.db") as conn:
                known = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'passage_class'"
                ).fetchone()
            if known is None:
                logger.warning(
                    "passage_class.demote %s: this root has no passage_class table, nothing is demoted",
                    list(self.demote),
                )
                self.demote = ()

        self.enabled = enabled
        self.retrievers = {}
        self.degraded: dict[str, str] = {}

        for name in enabled:
            try:
                factory = RETRIEVER_FACTORIES[name]
                options = self.config.get(name)
                self.retrievers[name] = (
                    factory(paths, options=options)
                    if options is not None
                    else factory(paths)
                )
            except Exception as exc:
                if name == "bm25":
                    raise
                self.degraded[name] = public_error(exc)
                logger.warning(
                    "retriever %s unavailable, continuing without it: %r",
                    name,
                    exc,
                )

        breaker_config = self.config.get("dense_breaker") or {}
        self.breakers = {
            name: CircuitBreaker(
                failures=breaker_config.get("failures", 3),
                open_seconds=breaker_config.get("open_seconds", 30),
                name=name,              # its OPEN / CLOSED log lines name it
            )
            for name in BREAKER_RETRIEVERS
            if name in self.retrievers
        }

        self.traces = TraceWriter(
            paths.logs / "retrieval"
        )

        # A reranker that is configured but cannot start (a bad URL, say) leaves the service
        # up without it: searches that would rerank say "reranker" in degraded, and
        # /v2/status reports it (rerank_error).
        rcfg = self.config.get("reranker") or {}
        self.rerank_enabled = bool(rcfg.get("enabled"))
        self.rerank_error: str | None = None
        self.reranker = None
        if self.rerank_enabled:
            try:
                self.reranker = Reranker(rcfg)
                # reranker.title and reranker.covered_titles read the root's structure index
                db_path = paths.sqlite / "rag.db"
                if self.reranker.title:
                    self.reranker.titles = title_lines(db_path)
                if self.reranker.covered_titles:
                    self.reranker.covered = lambda chunk_ids: covered_titles_of(db_path, chunk_ids)
            except Exception as exc:
                self.rerank_error = public_error(exc)
                logger.warning(
                    "reranker unavailable, continuing without it: %r",
                    exc,
                )
            # the reranker's breaker (rag_core.rerank) is named here, for its log lines
            rerank_breaker = getattr(self.reranker, "breaker", None)
            if isinstance(rerank_breaker, CircuitBreaker):
                rerank_breaker.name = "reranker"

    def warm(self) -> None:
        """Per-index work a retriever can do before the first search (the BM25 fast path's
        index facts). A failure here only costs the first search that time again."""
        for name, retriever in self.retrievers.items():
            warm = getattr(retriever, "warm", None)
            if warm is None:
                continue
            try:
                warm()
            except Exception as exc:
                logger.warning(
                    "retriever %s warm-up failed: %r",
                    name,
                    exc,
                )

    def runtime_degraded(
        self,
    ) -> dict[str, str]:
        """Retrievers that are configured but not answering right now: failed
        to start, or skipped by an open (or half-open) circuit breaker."""
        degraded = dict(self.degraded)

        breakers = dict(self.breakers)
        rerank_breaker = getattr(self.reranker, "breaker", None)
        if rerank_breaker is not None:
            breakers["reranker"] = rerank_breaker
        if getattr(self, "rerank_error", None):
            degraded["reranker"] = self.rerank_error

        for name, breaker in breakers.items():
            state = breaker.snapshot()
            if state["state"] != "closed":
                degraded[name] = (
                    f"circuit breaker {state['state']} after "
                    f"{state['consecutive_failures']} failures "
                    f"(last: {state['last_error_kind']})"
                )

        return degraded

    def capabilities(
        self,
    ) -> dict:
        names = set(
            self.retrievers
        )

        degraded = self.runtime_degraded()

        return {
            "degraded":
                degraded,
            "bm25":
                "bm25" in names,
            "dense":
                "dense" in names
                and "dense" not in degraded,
            "colbert":
                "colbert" in names,
            "reranker":
                self.reranker is not None,
            "cross_version_analysis":
                False,
            "calibrated_confidence":
                False,
        }

    def _run_one(
        self,
        retriever,
        variant:
            QueryVariant,
        limit: int,
        doc_ids: frozenset[str] | None = None,
        timings: dict | None = None,
        embedded: dict | None = None,
        exclude_doc_ids: frozenset[str] | None = None,
    ) -> list[SearchHit]:

        breaker = self.breakers.get(
            retriever.name
        )
        started = time.perf_counter()
        extra = (
            {"timings": timings}
            if timings is not None
            and "timings" in inspect.signature(retriever.search).parameters
            else {}
        )
        # query vectors embedded beforehand (query_vectors), for a retriever that takes them
        if embedded and "embedded" in inspect.signature(retriever.search).parameters:
            extra["embedded"] = embedded
        # documents left out (the opt-in doc types): pushed down to a retriever that takes them,
        # dropped afterwards from the hits of one that does not
        drop = None
        if exclude_doc_ids:
            if "exclude_doc_ids" in inspect.signature(retriever.search).parameters:
                extra["exclude_doc_ids"] = exclude_doc_ids
            else:
                drop = exclude_doc_ids

        try:
            if doc_ids is None:
                hits = retriever.search(
                    variant,
                    limit=limit,
                    **extra,
                )
            elif "doc_ids" in inspect.signature(retriever.search).parameters:
                hits = retriever.search(
                    variant,
                    limit=limit,
                    doc_ids=doc_ids,
                    **extra,
                )
            else:
                # a retriever that cannot filter (ColBERT/PLAID) is filtered afterwards
                hits = [
                    hit
                    for hit in retriever.search(variant, limit=limit)
                    if hit.doc_id in doc_ids
                ]
                drop = exclude_doc_ids or None
                for rank, hit in enumerate(hits, start=1):
                    hit.rank = rank
            if drop is not None:
                hits = [
                    hit
                    for hit in hits
                    if hit.doc_id not in drop
                ]
                for rank, hit in enumerate(hits, start=1):
                    hit.rank = rank
        except Exception as exc:
            if breaker is not None:
                breaker.record_failure(
                    error_kind(exc)
                )
            raise
        finally:
            if timings is not None:
                timings["ms"] = round((time.perf_counter() - started) * 1000, 1)

        if breaker is not None:
            breaker.record_success()

        return hits

    @staticmethod
    def _rerank(
        reranker,
        query: str,
        main: list[dict],
        degraded: set[str],
        task_errors: list[dict],
    ) -> tuple[list[dict], dict]:
        """The fused head reranked, and what happened. A failure, or a circuit breaker that is
        open (no call, no wait), leaves the fused order and adds "reranker" to degraded."""
        pool = main[: reranker.candidates]
        breaker = getattr(reranker, "breaker", None)
        if breaker is not None and not breaker.allow():
            degraded.add("reranker")
            return main, {
                "applied": False,
                "candidates": len(pool),
                "skipped": "circuit breaker open",
            }
        started = time.perf_counter()
        try:
            ranked = reranker.rerank(query, pool) + main[len(pool):]
        except Exception as exc:
            if breaker is not None:
                breaker.record_failure(error_kind(exc))
            # the fused order stands; the caller can see why
            degraded.add("reranker")
            task_errors.append(
                {
                    "retriever": "reranker",
                    "variant_id": None,
                    "purpose": "rerank",
                    "error": public_error(exc),
                    "kind": error_kind(exc),
                }
            )
            return main, {
                "applied": False,
                "candidates": len(pool),
                "error": public_error(exc),
            }
        if breaker is not None:
            breaker.record_success()
        return ranked, {
            "applied": True,
            "candidates": len(pool),
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    @staticmethod
    def _gated_rerank(
        gate,
        reranker,
        query: str,
        main: list[dict],
        degraded: set[str],
        task_errors: list[dict],
    ) -> tuple[list[dict], dict]:
        """_rerank behind the pipeline's rerank gate (rag_core.pipeline), and the time it waited
        there ("wait_ms"). An open breaker skips at once, without queueing: its state is read,
        never allow(), which would take a half-open breaker's one trial from the allow() _rerank
        makes inside the gate. A search the gate refuses, or one that waited past
        rerank_wait_seconds with rerank_on_timeout "skip", keeps the fused order: degraded
        "reranker", skipped "rerank queue", and no breaker failure (the service did not fail)."""
        pool = main[: reranker.candidates]
        breaker = getattr(reranker, "breaker", None)
        if breaker is not None and breaker.state == OPEN:
            degraded.add("reranker")
            return main, {
                "applied": False,
                "candidates": len(pool),
                "skipped": "circuit breaker open",
                "wait_ms": 0.0,
            }
        with gate.hold() as (admitted, wait_ms):
            if not admitted:
                degraded.add("reranker")
                return main, {
                    "applied": False,
                    "candidates": len(pool),
                    "skipped": "rerank queue",
                    "wait_ms": wait_ms,
                }
            ranked, info = RetrievalOrchestrator._rerank(
                reranker,
                query,
                main,
                degraded,
                task_errors,
            )
        return ranked, {**info, "wait_ms": wait_ms}

    def _classed(
        self,
        chunk_ids: list[str],
    ) -> set[str]:
        """Those of `chunk_ids` whose passage class (rag.db passage_class) is one of self.demote."""
        marks = ",".join("?" for _ in chunk_ids)
        classes = ",".join("?" for _ in self.demote)
        with connect(self.paths.sqlite / "rag.db") as conn:
            return {
                row[0]
                for row in conn.execute(
                    f"SELECT chunk_id FROM passage_class WHERE chunk_id IN ({marks}) AND class IN ({classes})",  # nosec B608 - only "?" placeholders are interpolated; the values are bound
                    [*chunk_ids, *self.demote],
                )
            }

    def query_vectors(
        self,
        queries: list[str],
    ) -> dict | None:
        """The dense query texts of several searches (each query's plan, as search() builds it)
        embedded in one TEI call, for search(embedded=...): API v2 search_many's batch.

        None when there is nothing to embed or the batch cannot be made now: no dense retriever,
        one that embeds only for itself, its breaker not closed (a half-open breaker's one trial
        call belongs to a search), or the call failed. Each dense leg then embeds its own text,
        as in a single search, and its own success or failure counts in the breaker."""
        dense = self.retrievers.get("dense")
        embed = getattr(dense, "embed_queries", None)
        breaker = self.breakers.get("dense")
        if embed is None or breaker is not None and breaker.state != "closed":
            return None

        texts = [
            variant.text
            for query in queries
            for variant in build_query_plan(
                query,
                "normal",           # the mode search() runs with by default
                technical_retrievers=self.config.get(
                    "technical_variant_retrievers"
                ),
                technical=self.technical,
                vocabulary=self.vocabulary,
            )
            if variant.text
            and (variant.retrievers is None or "dense" in variant.retrievers)
        ]
        if not texts:
            return None

        try:
            return embed(texts) or None
        except Exception as exc:
            logger.warning(
                "query embedding batch failed, each dense leg embeds its own: %r",
                exc,
            )
            return None

    def search(
        self,
        query: str,
        *,
        mode: str = "normal",
        limit: int | None = None,
        extra_variants:
            list[QueryVariant]
            | None = None,
        retrievers: frozenset[str] | None = None,
        doc_ids: frozenset[str] | None = None,
        rerank: bool = True,
        embedded: dict | None = None,
        exclude_doc_ids: frozenset[str] | None = None,
    ) -> dict:
        """retrievers: names to run (None = every active one); doc_ids: only these documents
        (None = all); rerank=False skips a configured reranker; embedded: query vectors made
        beforehand (query_vectors), which the dense leg uses instead of calling TEI;
        exclude_doc_ids: documents left out (the opt-in doc types of API v2 and /search; None =
        none). The defaults are /search on a root without opt-in doc types."""

        reranker = self.reranker if rerank else None

        variants = build_query_plan(
            query,
            mode,
            extra_variants,
            technical_retrievers=self.config.get(
                "technical_variant_retrievers"
            ),
            technical=self.technical,
            vocabulary=self.vocabulary,
        )

        per_query_limit = int(
            self.config[
                "candidate_limit_per_query"
            ]
        )

        fused_limit = (
            int(limit)
            if limit is not None
            else int(
                self.config[
                    "fused_limit"
                ]
            )
        )

        # What this request could not use: retrievers it asked for that failed
        # to start, were skipped by an open breaker, or failed; the reranker if
        # it failed.
        degraded = {
            name
            for name in self.degraded
            if retrievers is None
            or name in retrievers
        }
        if rerank and getattr(self, "rerank_error", None):
            degraded.add("reranker")            # configured, but it could not start

        tasks = []

        for retriever in self.retrievers.values():
            if retrievers is not None and retriever.name not in retrievers:
                continue            # not asked for: neither run nor degraded

            breaker = self.breakers.get(
                retriever.name
            )

            for variant in variants:
                if (
                    variant.retrievers is not None
                    and retriever.name not in variant.retrievers
                ):
                    continue

                # Nothing to search (no document passes the filters, or no
                # query text): the retriever would answer [] without calling
                # its service, and that "success" must not close the breaker,
                # reset its failure count or use up the half-open trial.
                if breaker is not None and (
                    doc_ids is not None and not doc_ids
                    or not variant.text.strip()
                ):
                    continue

                if breaker is not None and not breaker.allow():
                    degraded.add(retriever.name)
                    continue

                tasks.append(
                    (
                        retriever,
                        variant,
                    )
                )

        raw_hits: list[
            SearchHit
        ] = []

        task_errors: list[
            dict
        ] = []

        answered: set[str] = set()

        # per leg: {"retriever", "purpose", "ms"} and what the retriever itself measured
        legs: list[dict] = []

        workers = max(
            1,
            min(
                16,
                len(tasks),
            ),
        )

        with ThreadPoolExecutor(
            max_workers=workers
        ) as pool:

            futures = {}
            for retriever, variant in tasks:
                leg = {
                    "retriever": retriever.name,
                    "purpose": variant.purpose,
                }
                legs.append(leg)
                futures[
                    pool.submit(
                        self._run_one,
                        retriever,
                        variant,
                        per_query_limit,
                        doc_ids,
                        leg,
                        embedded,
                        exclude_doc_ids,
                    )
                ] = (
                    retriever.name,
                    variant,
                )

            for future in (
                as_completed(futures)
            ):
                (
                    retriever_name,
                    variant,
                ) = futures[future]

                try:
                    raw_hits.extend(
                        future.result()
                    )
                    answered.add(
                        retriever_name
                    )
                except Exception as exc:
                    degraded.add(
                        retriever_name
                    )
                    task_errors.append(
                        {
                            "retriever":
                                retriever_name,
                            "variant_id":
                                variant.variant_id,
                            "purpose":
                                variant.purpose,
                            "error":
                                public_error(exc),
                            "kind":
                                error_kind(exc),
                        }
                    )

        main_hits = [
            hit
            for hit in raw_hits
            if hit.purpose
            in MAIN_PURPOSES
        ]

        challenge_hits = [
            hit
            for hit in raw_hits
            if hit.purpose
            in CHALLENGE_PURPOSES
        ]

        variant_weights = {
            v.variant_id:
                v.weight
            for v in variants
        }

        retriever_weights = (
            self.config[
                "retriever_weights"
            ]
        )

        # Retrievers that answered this request; consensus is counted over
        # these, not over a leg that was down.
        active = sorted(
            answered
        )

        pool_size = (
            max(fused_limit, reranker.candidates)
            if reranker is not None
            else fused_limit
        )

        main = rrf_fuse(
            main_hits,
            retriever_weights=
                retriever_weights,
            variant_weights=
                variant_weights,
            active_retrievers=
                active,
            k=int(
                self.config[
                    "rrf_k"
                ]
            ),
            limit=pool_size,
            keep_text=reranker is not None,
        )

        # passage_class.demote: the candidates of those classes go behind the others, in the
        # fused list as it was cut, before the reranker takes its candidates from its front
        if self.demote and main:
            main = demoted(main, self._classed([c["chunk_id"] for c in main]))

        rerank_info = None

        if reranker is not None and main:
            main, rerank_info = self._rerank(
                reranker,
                query,
                main,
                degraded,
                task_errors,
            ) if self.rerank_gate is None else self._gated_rerank(
                self.rerank_gate,
                reranker,
                query,
                main,
                degraded,
                task_errors,
            )
            main = main[:fused_limit]
            for item in main:
                item.pop("text", None)

        challenges = rrf_fuse(
            challenge_hits,
            retriever_weights=
                retriever_weights,
            variant_weights=
                variant_weights,
            active_retrievers=
                active,
            k=int(
                self.config[
                    "rrf_k"
                ]
            ),
            limit=min(
                15,
                fused_limit,
            ),
        )

        run_id = (
            "qry_"
            + uuid.uuid4().hex
        )

        degraded = sorted(
            degraded
        )

        trace = {
            "run_id": run_id,
            "query": query,
            "mode": mode,
            "capabilities":
                self.capabilities(),
            "active_retrievers":
                active,
            "degraded":
                degraded,
            "task_errors":
                task_errors,
            "variants": [
                {
                    "variant_id":
                        v.variant_id,
                    "purpose":
                        v.purpose,
                    "weight":
                        v.weight,
                    "text":
                        v.text,
                }
                for v in variants
            ],
            "raw_hits": [
                {
                    "retriever":
                        h.retriever,
                    "variant_id":
                        h.variant_id,
                    "purpose":
                        h.purpose,
                    "chunk_id":
                        h.chunk_id,
                    "rank":
                        h.rank,
                    "score":
                        h.score,
                }
                for h in raw_hits
            ],
            "candidates":
                main,
            "challenge_candidates":
                challenges,
            "rerank":
                rerank_info,
        }

        # written off the request path, sampled and pruned (rag_core.traces)
        trace_path = self.traces.submit(
            run_id,
            trace,
        )

        return {
            "run_id":
                run_id,
            "query":
                query,
            "mode":
                mode,
            "capabilities":
                self.capabilities(),
            "active_retrievers":
                active,
            "degraded":
                degraded,
            "task_errors":
                task_errors,
            "query_variants": [
                {
                    "id":
                        v.variant_id,
                    "purpose":
                        v.purpose,
                    "text":
                        v.text,
                    "weight":
                        v.weight,
                }
                for v in variants
            ],
            "candidates":
                main,
            "challenge_candidates":
                challenges,
            "rerank":
                rerank_info,
            "legs":
                legs,
            "trace_path":
                trace_path,
        }
