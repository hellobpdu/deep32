"""Pipelined searches: several searches retrieve at once while their reranks pass one FIFO gate.

serving.json (the repo file or a root's overlay, e.g. set by scripts/derive_root.py --set serving.pipeline.KEY=VALUE):

    "max_concurrent_searches": 4,      searches admitted at once (rag_core.api.Admission; default 1)
    "max_queued_searches": 12,         and waiting for a slot
    "pipeline": {
        "rerank_gate": 0 | 1,          0 (default): no gate, as before. 1: every rerank job of this process passes
                                       one FIFO gate (StageGate): a /search or /v2/search's reranker call, or all of
                                       a /v2/search_many's calls at once (they stay one job, with one timeout)
        "rerank_wait_seconds": 10,     how long a search waits at the gate before it is overdue
        "rerank_max_waiting": 8,       searches that may wait there (at least max_concurrent_searches - 1)
        "rerank_on_timeout": "wait"    an overdue search: "wait" (default) keeps its place and is reranked, so the
                                       pipeline never changes an answer; "skip" is answered without the rerank,
                                       degraded "reranker" (rerank skipped: "rerank queue"), and records no breaker
                                       failure (the service did not fail)
    }

"wait" has no ceiling: an overdue search waits for its turn however long the holders ahead take (each up to the
reranker's timeout_seconds, 15 s on the roots build_root writes), so behind three slow reranks it can pass
deep32-gateway's 60 s read timeout with no 503 and no degraded answer. It is counted overdue, never timed_out, and shows
in max_wait_ms; scripts/bench_pipeline.py's gate_timeouts counts both modes. A ceiling for "wait" is the operator's choice.

Why a gate: the reranker reads all of a search's candidates in one forward pass that fills the GPU, so two reranks
cannot overlap; the service runs them one at a time behind a lock that is not FIFO. With several searches admitted
and no gate, their reranks queue at the service in any order, and that wait counts against the reranker client's
timeout and its circuit breaker. Behind the gate the GPU sees one search's rerank at a time, in arrival order, the
wait is visible (timings_ms.rerank_wait, /v2/status "pipeline") and never trips the breaker, while the other
searches retrieve (BM25, dense) beside it.

An open breaker skips the gate: no wait, no call. That check reads breaker.state and never calls allow(): allow()
takes a half-open breaker's one trial call, so the allow() made inside the gate would then refuse it, the search
would skip and record nothing, and reranking would stay off after every outage until a restart. Inside the gate
the search calls allow() as it always did, so a breaker that opened while it waited is honoured.

Without "pipeline" (or with rerank_gate 0) deep32-engine runs exactly as before: no gate is made, and the answers and
/v2/status carry nothing new.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator


PIPELINE_KEYS = ("rerank_gate", "rerank_wait_seconds", "rerank_max_waiting", "rerank_on_timeout")
ON_TIMEOUT = ("wait", "skip")           # rerank_on_timeout; the first is the default
# anyio's default thread limiter: the worker threads that run the sync endpoints. A search admitted or waiting for
# admission holds one; with the gate on, admission must leave READING_THREADS of them to the reading endpoints
ANYIO_THREADS = 40
READING_THREADS = 16
# how often a waiter looks again although nobody woke it (a lost wake-up costs at most this)
WAIT_SLICE_SECONDS = 1.0


@dataclass(frozen=True)
class PipelineSettings:
    rerank_gate: int = 0
    rerank_wait_seconds: float = 10.0
    rerank_max_waiting: int = 8
    rerank_on_timeout: str = ON_TIMEOUT[0]


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def pipeline_settings(config: dict[str, Any], *, slots: int, max_queued: int) -> PipelineSettings:
    """serving.json "pipeline", checked against the admission it runs with (rag_core.api.admission_settings and
    queue_limit: `slots` searches at once, `max_queued` waiting). ValueError names a value deep32-engine cannot take;
    rag_core.api checks them at start, before any retriever loads. Without "pipeline": PipelineSettings(), off."""
    value = config.get("pipeline")
    if value is None:
        return PipelineSettings()
    if not isinstance(value, dict) or set(value) - set(PIPELINE_KEYS):
        raise ValueError(f"serving.json pipeline must be an object of {list(PIPELINE_KEYS)}, not {value!r}")
    default = PipelineSettings()
    gate = value.get("rerank_gate", default.rerank_gate)
    wait = value.get("rerank_wait_seconds", default.rerank_wait_seconds)
    waiting = value.get("rerank_max_waiting", default.rerank_max_waiting)
    on_timeout = value.get("rerank_on_timeout", default.rerank_on_timeout)
    if not _integer(gate) or gate not in (0, 1):
        raise ValueError(f"serving.json pipeline.rerank_gate must be 0 or 1, not {gate!r}")
    if not (_integer(wait) or isinstance(wait, float)) or not (math.isfinite(wait) and wait > 0):
        raise ValueError(f"serving.json pipeline.rerank_wait_seconds must be a number of seconds > 0, not {wait!r}")
    if not _integer(waiting) or waiting < 0:
        raise ValueError(f"serving.json pipeline.rerank_max_waiting must be an integer >= 0, not {waiting!r}")
    if on_timeout not in ON_TIMEOUT:
        raise ValueError(f"serving.json pipeline.rerank_on_timeout must be one of {list(ON_TIMEOUT)}, "
                         f"not {on_timeout!r}")
    if gate:
        if slots + max_queued > ANYIO_THREADS - READING_THREADS:
            raise ValueError(
                f"serving.json pipeline.rerank_gate 1 with max_concurrent_searches {slots} and max_queued_searches "
                f"{max_queued}: searches could hold {slots + max_queued} of the {ANYIO_THREADS} worker threads, "
                f"leaving fewer than {READING_THREADS} to the reading endpoints"
            )
        if waiting < slots - 1:
            raise ValueError(
                f"serving.json pipeline.rerank_max_waiting {waiting} is less than max_concurrent_searches - 1 "
                f"({slots - 1}): an admitted search could find the rerank gate full and go unreranked"
            )
    return PipelineSettings(rerank_gate=gate, rerank_wait_seconds=float(wait), rerank_max_waiting=waiting,
                            rerank_on_timeout=on_timeout)


class StageGate:
    """`slots` holders at once, the others served in arrival order (a ticket queue; threading's locks do not
    promise FIFO), at most `max_waiting` of them waiting: one more is refused at once.

    hold() waits up to wait_seconds. Past it, on_timeout "skip" gives up (the caller goes without the stage) and
    "wait" keeps the waiter's place: it is counted overdue and served in its turn. Counters for /v2/status:
    passed, refused, timed_out, overdue and the longest wait."""

    def __init__(
        self,
        slots: int,
        max_waiting: int,
        *,
        wait_seconds: float,
        on_timeout: str = ON_TIMEOUT[0],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not _integer(slots) or slots < 1 or not _integer(max_waiting) or max_waiting < 0 \
                or not wait_seconds > 0 or on_timeout not in ON_TIMEOUT:
            raise ValueError("StageGate needs slots >= 1, max_waiting >= 0, wait_seconds > 0 and on_timeout in "
                             f"{list(ON_TIMEOUT)}")
        self.slots = slots
        self.max_waiting = max_waiting
        self.wait_seconds = float(wait_seconds)
        self.on_timeout = on_timeout
        self._clock = clock
        self._cond = threading.Condition()
        self._queue: deque[object] = deque()
        self._busy = 0
        self.passed = 0
        self.refused = 0
        self.timed_out = 0
        self.overdue = 0
        self.max_wait_ms = 0.0

    @classmethod
    def from_settings(cls, settings: PipelineSettings) -> "StageGate | None":
        """The rerank gate the settings ask for; None when rerank_gate is 0."""
        if not settings.rerank_gate:
            return None
        return cls(settings.rerank_gate, settings.rerank_max_waiting, wait_seconds=settings.rerank_wait_seconds,
                   on_timeout=settings.rerank_on_timeout)

    def acquire(self) -> bool:
        """Take a slot, in turn: True, or False when refused (the queue is full) or timed out (on_timeout skip)."""
        with self._cond:
            if self._busy < self.slots and not self._queue:
                self._busy += 1
                self.passed += 1
                return True
            if len(self._queue) >= self.max_waiting:
                self.refused += 1
                return False
            ticket = object()
            self._queue.append(ticket)
            deadline = self._clock() + self.wait_seconds
            late = False
            try:
                while not (self._queue[0] is ticket and self._busy < self.slots):
                    left = deadline - self._clock()
                    if left <= 0 and not late:
                        if self.on_timeout == "skip":
                            self.timed_out += 1
                            return False
                        late = True
                        self.overdue += 1
                    self._cond.wait(WAIT_SLICE_SECONDS if late else min(left, WAIT_SLICE_SECONDS))
                self._queue.popleft()
                self._busy += 1
                self.passed += 1
                return True
            finally:
                if ticket in self._queue:           # gave up (timed out, or interrupted)
                    self._queue.remove(ticket)
                self._cond.notify_all()             # the next in line may be first now

    def release(self) -> None:
        with self._cond:
            if self._busy < 1:
                raise ValueError("StageGate released more often than acquired")
            self._busy -= 1
            self._cond.notify_all()

    @contextmanager
    def hold(self) -> Iterator[tuple[bool, float]]:
        """(admitted, ms waited); the slot, when taken, is given back on leaving."""
        started = time.perf_counter()
        ok = self.acquire()
        waited = round((time.perf_counter() - started) * 1000, 1)
        with self._cond:
            self.max_wait_ms = max(self.max_wait_ms, waited)
        try:
            yield ok, waited
        finally:
            if ok:
                self.release()

    def snapshot(self) -> dict[str, Any]:
        with self._cond:
            return {
                "slots": self.slots,
                "busy": self._busy,
                "waiting": len(self._queue),
                "max_waiting": self.max_waiting,
                "wait_seconds": self.wait_seconds,
                "on_timeout": self.on_timeout,
                "passed": self.passed,
                "refused": self.refused,
                "timed_out": self.timed_out,
                "overdue": self.overdue,
                "max_wait_ms": self.max_wait_ms,
            }
