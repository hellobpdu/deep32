from __future__ import annotations

import hmac
import logging
import os
import threading

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    status,
)
from pydantic import (
    BaseModel,
    Field,
)

from rag_core import api_v2
from rag_core.config import load_config
from rag_core.orchestrator import (
    RetrievalOrchestrator,
)
from rag_core.paths import RAGPaths
from rag_core.pipeline import StageGate, pipeline_settings
from rag_core.status import StatusService


logger = logging.getLogger(__name__)

# rag_core's records on stderr, one plain line each; journald adds the time and the unit
# (the format deep32-gateway's configure_logging uses)
LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"
LOG_HANDLER_NAME = "rag_core.stderr"


def configure_logging(
    level: int = logging.INFO,
) -> bool:
    """Give the rag_core logger a stderr handler at INFO, so the startup lines (search
    admission, rerank gate, source preload) and the breaker transitions reach the journal.
    uvicorn configures only its own loggers, so rag_core's INFO records went nowhere and its
    warnings reached stderr bare, through logging's last-resort handler.

    A process whose root logger has handlers (an embedding app, uvicorn --log-config with a
    root section), or whose rag_core logger already has one, is left alone. The handler does
    not propagate, so a root handler added later never prints a line twice. True if it was
    added."""
    package = logging.getLogger("rag_core")
    if package.handlers or logging.getLogger().handlers:
        return False
    handler = logging.StreamHandler()
    handler.set_name(LOG_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    package.addHandler(handler)
    package.setLevel(level)
    package.propagate = False
    return True


SERVING_CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "serving.json"
)


def serving_config() -> dict[str, Any]:
    """config/serving.json with the root's overlay on top
    ($RAG_ROOT/config/serving.json); RAG_SERVING_CONFIG names a file that
    replaces both."""
    return load_config(
        "serving",
        env_var="RAG_SERVING_CONFIG",
        repo_dir=SERVING_CONFIG_PATH.parent,
    )


def admission_settings(
    config: dict[str, Any] | None = None,
) -> tuple[int, float]:
    """Search admission: (concurrent searches, seconds a search may queue).

    Read from serving_config(): max_concurrent_searches and
    search_queue_timeout_seconds. The env vars RAG_MAX_CONCURRENT_SEARCHES
    and RAG_SEARCH_QUEUE_TIMEOUT override them. The repo default is one
    search at a time, as production ran behind one lock; a root or a
    deployment raises it deliberately, never by default.
    """
    if config is None:
        config = serving_config()

    slots = (
        os.environ.get("RAG_MAX_CONCURRENT_SEARCHES")
        or config.get("max_concurrent_searches", 1)
    )
    timeout = (
        os.environ.get("RAG_SEARCH_QUEUE_TIMEOUT")
        or config.get("search_queue_timeout_seconds", 30)
    )

    try:
        slots, timeout = int(slots), float(timeout)
    except (TypeError, ValueError):
        raise ValueError(
            "search admission settings must be numbers: "
            f"max_concurrent_searches={slots!r} "
            f"search_queue_timeout_seconds={timeout!r}"
        ) from None

    if slots < 1 or not timeout > 0:
        raise ValueError(
            "max_concurrent_searches must be >= 1 and "
            "search_queue_timeout_seconds > 0"
        )

    return slots, timeout


# Searches allowed to wait for a slot when serving.json does not say. Every waiter holds
# one of the worker threads that run the sync endpoints (anyio's default limiter: 40), so
# the cap keeps threads free for the reading endpoints however many searches arrive.
DEFAULT_MAX_QUEUED_SEARCHES = 16

# /v2/status runs its probes in threads of its own limiter, never behind searches.
STATUS_THREADS = 2

# seconds shutdown waits for the source preload thread to notice it should stop
PRELOAD_JOIN_SECONDS = 5.0


def start_source_preload(
    app: FastAPI,
    stop: threading.Event,
) -> threading.Thread:
    """Load the root's structure index and every source file in the background (serving.json
    "preload_sources"), so no search pays for a first touch of a guide: about 10 ms per large
    file on the i7 and ~5x that on the production CPU, and +160 MB for corpus-v1's 403 files.
    Searches that arrive first load what they need themselves."""

    def run() -> None:
        try:
            loaded = api_v2.root_reader(app.state).preload(stop=stop)
            logger.info("source preload: %d files", loaded)
        except Exception as exc:
            logger.warning("source preload failed: %r", exc)

    thread = threading.Thread(
        target=run,
        name="source-preload",
        daemon=True,
    )
    thread.start()
    return thread


def queue_limit(
    config: dict[str, Any] | None = None,
) -> int:
    """How many searches may wait for an admission slot: max_queued_searches in
    serving_config() (default DEFAULT_MAX_QUEUED_SEARCHES; 0 = none wait). A search
    arriving when that many already wait answers 503 at once."""
    if config is None:
        config = serving_config()

    value = config.get(
        "max_queued_searches",
        DEFAULT_MAX_QUEUED_SEARCHES,
    )

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            "max_queued_searches must be an integer >= 0: "
            f"{value!r}"
        )

    return value


class Admission:
    """Bounded search admission with a bounded queue.

    `slots` searches run at once. Up to `max_waiting` more wait for a slot, each for at
    most the queue timeout its caller passes; a search that finds the queue full is
    refused at once (acquire returns False: 503 + Retry-After). A waiter blocks the
    worker thread it runs in, so without this cap a burst of searches could take every
    thread of the pool and starve the endpoints that need one.

    acquire() and release() follow threading.Semaphore; only blocking acquires queue."""

    def __init__(
        self,
        slots: int,
        max_waiting: int,
    ) -> None:
        self._slots = threading.BoundedSemaphore(
            slots
        )
        self._lock = threading.Lock()
        self.max_waiting = max_waiting
        self.waiting = 0
        self.refused = 0

    def acquire(
        self,
        blocking: bool = True,
        timeout: float | None = None,
    ) -> bool:
        if self._slots.acquire(blocking=False):
            return True

        if not blocking:
            return False

        with self._lock:
            if self.waiting >= self.max_waiting:
                self.refused += 1
                return False
            self.waiting += 1

        try:
            return self._slots.acquire(
                timeout=timeout
            )
        finally:
            with self._lock:
                self.waiting -= 1

    def release(self) -> None:
        self._slots.release()


class SearchRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=16384,
    )

    mode: str = Field(
        default="normal",
        min_length=1,
        max_length=32,
    )

    limit: int = Field(
        default=8,
        ge=1,
        le=50,
    )


def _expected_token() -> str:
    token = os.environ.get(
        "RAG_CORE_API_TOKEN",
        "",
    )

    if len(token) < 32:
        raise RuntimeError(
            "RAG_CORE_API_TOKEN is missing "
            "or unacceptably short"
        )

    return token


def require_auth(
    authorization: str | None = Header(
        default=None,
    ),
) -> None:
    expected = (
        "Bearer "
        + _expected_token()
    )

    supplied = authorization or ""

    # Compare bytes: compare_digest() raises TypeError on str with non-ASCII
    # characters, and Starlette decodes header bytes 0x80-0xFF as Latin-1,
    # so such a header used to answer 500 instead of 401.
    if not hmac.compare_digest(
        supplied.encode("utf-8", "surrogateescape"),
        expected.encode("utf-8", "surrogateescape"),
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail="unauthorized",
            headers={
                "WWW-Authenticate":
                    "Bearer"
            },
        )


async def authorized(
    authorization: str | None = Header(
        default=None,
    ),
) -> None:
    """require_auth as the endpoints' dependency. FastAPI runs a plain `def` dependency
    in a worker thread; this one runs on the event loop (the check is a constant-time
    compare), so a request is authorized even while searches hold every worker thread."""
    require_auth(authorization)


@asynccontextmanager
async def lifespan(
    app: FastAPI,
):
    # rag_core's log lines on stderr before anything below logs (configure_logging)
    configure_logging()
    # Validate security and admission configuration first.
    _expected_token()
    serving = serving_config()
    slots, queue_timeout = admission_settings(
        serving
    )
    max_queued = queue_limit(
        serving
    )
    # serving.json "pipeline" (rag_core.pipeline): the rerank gate, off
    # unless a root sets it
    pipeline = pipeline_settings(
        serving,
        slots=slots,
        max_queued=max_queued,
    )

    paths = RAGPaths.from_env()

    # This loads the persistent retrieval stack once:
    # BM25 + dense/Qdrant + ColBERT/FastPLAID.
    orchestrator = RetrievalOrchestrator(
        paths
    )
    # /v2/search_many's settings in the retrieval config: a value they
    # cannot take fails the start, not a request
    api_v2.search_many_settings(
        getattr(orchestrator, "config", None) or {}
    )
    # and so do its opt-in doc types (api_v2.opt_in_doc_types)
    api_v2.opt_in_doc_types(
        getattr(orchestrator, "config", None) or {}
    )
    # and /v2/grep's settings, the regex module too when they need it
    api_v2.grep_settings(
        getattr(orchestrator, "config", None) or {}
    )
    # and the identical-text fold of the hits (api_v2.fold_identical)
    api_v2.fold_identical(
        getattr(orchestrator, "config", None) or {}
    )
    # every rerank of this process passes the gate, when there is one
    gate = StageGate.from_settings(pipeline)
    if gate is not None:
        orchestrator.rerank_gate = gate
        logger.info(
            "pipeline: rerank gate %d, %d waiting at most, "
            "overdue after %.1f s: %s",
            gate.slots,
            gate.max_waiting,
            gate.wait_seconds,
            gate.on_timeout,
        )
    # per-index facts (BM25 fast path) now, not in the first search
    warm = getattr(orchestrator, "warm", None)
    if warm is not None:
        warm()

    app.state.paths = paths
    app.state.orchestrator = (
        orchestrator
    )

    # The index generation /v2 answers carry: the root overlay's serving.json
    # names it (scripts/build_root.py); an older root is known by its
    # directory name.
    app.state.generation = str(
        serving.get("generation")
        or paths.root.name
    )

    # Bounded admission instead of one global lock: a client issues
    # several searches at once, and a single lock queued each behind the
    # previous one. Retrievers are safe to run concurrently (BM25 opens a
    # SQLite connection per search, dense uses a pooled thread-safe client,
    # ColBERT serializes itself). A search that waits longer than the queue
    # timeout answers 503 + Retry-After instead of queueing without bound,
    # and so does one that finds max_queued searches already waiting;
    # callers (deep32-gateway) must retry on 503.
    app.state.search_slots = Admission(
        slots,
        max_queued,
    )
    app.state.search_queue_timeout = (
        queue_timeout
    )

    logger.info(
        "search admission: %d concurrent, %d queued at most, "
        "queue timeout %.1f s",
        slots,
        max_queued,
        queue_timeout,
    )

    # /v2/status: live probes, cached briefly, outside search admission so
    # it still answers while searches queue; its probes run in threads of
    # their own limiter, so it never waits for a thread behind searches.
    app.state.status = StatusService(
        orchestrator,
        paths,
        generation=app.state.generation,
        serving={
            "max_concurrent_searches": slots,
            "max_queued_searches": max_queued,
        },
    )
    app.state.status_threads = anyio.CapacityLimiter(
        STATUS_THREADS
    )

    # (never on a root without rag.db: opening one would create an empty database)
    stop_preload = threading.Event()
    preload = (
        start_source_preload(app, stop_preload)
        if serving.get("preload_sources")
        and (paths.sqlite / "rag.db").is_file()
        else None
    )

    try:
        yield
    finally:
        stop_preload.set()
        if preload is not None:
            preload.join(PRELOAD_JOIN_SECONDS)
        app.state.status.close()


app = FastAPI(
    title="deep32-engine",
    version="1.0.0",
    lifespan=lifespan,

    # Core API is private infrastructure.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# API v2 (rag_core.api_v2): additive, same bearer token; /search and /health are unchanged.
app.include_router(
    api_v2.router,
    dependencies=[Depends(authorized)],
)


# /health and /v2/status must answer while searches queue (a monitor that times out
# there reports an outage that is only load). Both are async and so need no worker
# thread for themselves or their auth: /health reads in-memory state on the event
# loop, /v2/status runs its probes under its own thread limiter (STATUS_THREADS).
# Searches are kept from taking every worker thread by the admission queue cap.
@app.get("/health")
async def health(
    request: Request,
    _: None = Depends(
        authorized
    ),
) -> dict[str, Any]:
    orchestrator = (
        request.app.state.orchestrator
    )

    return {
        "status": "ok",
        "service": "deep32-engine",
        "capabilities":
            orchestrator.capabilities(),
        "active_retrievers":
            sorted(
                orchestrator.retrievers
            ),
    }


@app.post("/search")
def search(
    payload: SearchRequest,
    request: Request,
    _: None = Depends(
        authorized
    ),
) -> dict[str, Any]:
    orchestrator = (
        request.app.state.orchestrator
    )

    slots = request.app.state.search_slots

    if not slots.acquire(
        timeout=request.app.state.search_queue_timeout
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="search capacity exhausted, retry shortly",
            headers={"Retry-After": "2"},
        )

    try:
        # the documents of the opt-in doc types are left out, as in a /v2/search without filters
        excluded = legacy_exclusion(request)
        result = orchestrator.search(
            payload.query,
            mode=payload.mode,
            limit=payload.limit,
            **({"exclude_doc_ids": excluded} if excluded else {}),
        )
    finally:
        slots.release()

    return result


def legacy_exclusion(
    request: Request,
) -> frozenset[str] | None:
    """What /search leaves out: the documents of the root's opt-in doc types
    (api_v2.opt_in_doc_types), known from the structure index; None when the
    root has none of them, or none are configured (the root is not read)."""
    opt_in = api_v2.opt_in(request)
    if not opt_in:
        return None
    return api_v2.reader(request).doc_ids_of(opt_in) or None


@app.get("/v2/status")
async def status_v2(
    request: Request,
    _: None = Depends(
        authorized
    ),
) -> dict[str, Any]:
    state = request.app.state
    return await anyio.to_thread.run_sync(
        state.status.status,
        limiter=state.status_threads,
    )
