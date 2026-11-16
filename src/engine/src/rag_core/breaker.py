"""Circuit breaker for a retriever that depends on a remote service.

Dense retrieval needs the GPU host (TEI) and Qdrant. When the GPU host is
down, every search used to wait out the embedding timeout before fusing
without dense. The breaker skips the leg instead:

  closed     calls pass; `failures` consecutive failures open the breaker
  open       calls are skipped for `open_seconds`
  half-open  then one trial call passes: success closes the breaker,
             failure opens it for another `open_seconds`

retrieval.json "dense_breaker": {"failures": 3, "open_seconds": 30}

State changes go to the log (logger rag_core.breaker, WARNING), never single
calls: one OPEN line when a closed breaker opens; while failed half-open
trials keep re-opening it, at most one "still open" line per
STILL_OPEN_LOG_SECONDS; one CLOSED line, with how long it was open, when a
success closes it. The line is chosen under the lock and logged after it.
"""
from __future__ import annotations

import logging
import threading
import time

from typing import Any, Callable


CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half-open"

# A breaker left open for hours (the GPU host down, gaming mode) re-opens on every failed
# trial, one per open_seconds while searches arrive: about 120 an hour at 30 s. Those are
# summed into one "still open" line per this many seconds at most.
STILL_OPEN_LOG_SECONDS = 600.0

logger = logging.getLogger(__name__)


def _span(seconds: float) -> str:
    """41 s, 12 min 5 s, 12 h 40 min: how long a breaker was open, for the log."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


class CircuitBreaker:

    def __init__(
        self,
        *,
        failures: int = 3,
        open_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        name: str = "unnamed",
        still_open_log_seconds: float = STILL_OPEN_LOG_SECONDS,
    ) -> None:
        if int(failures) < 1 or not float(open_seconds) > 0:
            raise ValueError(
                "breaker failures must be >= 1 and open_seconds > 0"
            )

        self.failures = int(failures)
        self.open_seconds = float(open_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._consecutive = 0
        self._opened_at: float | None = None
        self._trial_started: float | None = None
        self._last_error_kind: str | None = None
        self._opens = 0         # how often it opened (a failed trial opens it again)
        # for the log only: what the breaker guards ("dense", "reranker"), when it last went
        # from closed to open, the failed trials since, and when an open line was last logged
        self.name = str(name)
        self.still_open_log_seconds = float(still_open_log_seconds)
        self._open_since: float | None = None
        self._failed_trials = 0
        self._logged_at: float | None = None

    def _state(
        self,
        now: float,
    ) -> str:
        if self._opened_at is None:
            return CLOSED
        if now - self._opened_at < self.open_seconds:
            return OPEN
        return HALF_OPEN

    @property
    def state(self) -> str:
        with self._lock:
            return self._state(self._clock())

    def allow(self) -> bool:
        """May a call go ahead? In half-open, admits one trial at a time."""
        with self._lock:
            now = self._clock()
            state = self._state(now)

            if state == CLOSED:
                return True

            if state == OPEN:
                return False

            # A trial whose caller never reported back cannot block the
            # breaker for good: after open_seconds another trial may go.
            if (
                self._trial_started is not None
                and now - self._trial_started < self.open_seconds
            ):
                return False

            self._trial_started = now
            return True

    def record_success(self) -> None:
        line = None
        with self._lock:
            if self._opened_at is not None:
                now = self._clock()
                since = self._open_since if self._open_since is not None else self._opened_at
                line = (
                    "breaker %s CLOSED after %s open (%d failed trials)",
                    self.name,
                    _span(now - since),
                    self._failed_trials,
                )
                self._open_since = None
                self._failed_trials = 0
                self._logged_at = None
            self._consecutive = 0
            self._opened_at = None
            self._trial_started = None
        if line is not None:
            logger.warning(*line)

    def record_failure(
        self,
        kind: str,
    ) -> None:
        line = None
        with self._lock:
            now = self._clock()
            self._consecutive += 1
            self._last_error_kind = kind

            if self._opened_at is not None:
                # a failed trial (or a straggler from before the breaker
                # opened) restarts the open interval
                if self._state(now) == HALF_OPEN:
                    self._opens += 1
                    self._failed_trials += 1
                    if (
                        self._logged_at is None
                        or now - self._logged_at >= self.still_open_log_seconds
                    ):
                        self._logged_at = now
                        since = self._open_since if self._open_since is not None else self._opened_at
                        line = (
                            "breaker %s still open after %s (%d failed trials, last: %s)",
                            self.name,
                            _span(now - since),
                            self._failed_trials,
                            kind,
                        )
                self._opened_at = now
                self._trial_started = None
            elif self._consecutive >= self.failures:
                self._opened_at = now
                self._opens += 1
                self._open_since = now
                self._failed_trials = 0
                self._logged_at = now
                line = (
                    "breaker %s OPEN after %d consecutive failures (last: %s); "
                    "calls skip it, one trial call per %g s",
                    self.name,
                    self._consecutive,
                    kind,
                    self.open_seconds,
                )
        if line is not None:
            logger.warning(*line)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            state = self._state(now)
            return {
                "state": state,
                "consecutive_failures": self._consecutive,
                "last_error_kind": self._last_error_kind,
                "retry_in_seconds": (
                    round(self.open_seconds - (now - self._opened_at), 1)
                    if state == OPEN
                    else None
                ),
                "opens": self._opens,
            }
