"""Retrieval trace persistence off the request path, with retention.

Environment:
    RAG_TRACE                 "all" (default; also on/true/yes) | "off" (also false/no/none)
                              | a sampling fraction such as "0.1"; anything else logs a warning and means "all"
    RAG_TRACE_MAX_FILES       newest files kept (default 20000)
    RAG_TRACE_MAX_AGE_DAYS    older files are deleted (default 14)

Every search used to write a ~40-100 KiB indented JSON trace synchronously before answering, with no
retention. Traces are now written compactly by one background thread from a bounded queue; when the queue
is full the trace is dropped (counted in .dropped) rather than slowing a search.

A returned trace path may not exist yet: call TraceWriter.flush() (or flush_all()) before reading it.
Queued traces are flushed at interpreter exit, so short-lived scripts do not lose them.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import random
import threading
import time
import weakref
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# a .tmp this old is left over from a process that died mid-write; live writes take milliseconds
ORPHAN_TMP_AGE_S = 3600.0

_writers: weakref.WeakSet[TraceWriter] = weakref.WeakSet()


def sample_rate(mode: str) -> float:
    """RAG_TRACE value -> fraction of searches traced. A typo must not stop the service from starting."""
    mode = mode.strip().lower()
    if mode in {"off", "false", "no", "none"}:
        return 0.0
    if mode in {"all", "on", "true", "yes"}:
        return 1.0
    try:
        return min(1.0, max(0.0, float(mode)))
    except ValueError:
        logger.warning("RAG_TRACE=%r not understood; tracing every search", mode)
        return 1.0


def _mtimes(paths) -> list[tuple[float, Path]]:
    """(mtime, path) of each path that still exists: another process tracing to the same directory
    may rename a .tmp or prune a trace between listing and stat."""
    found = []
    for p in paths:
        try:
            found.append((p.stat().st_mtime, p))
        except FileNotFoundError:
            pass
    return found


def flush_all(timeout: float = 5.0) -> None:
    """Wait until every trace queued by any TraceWriter in this process has been written."""
    for writer in list(_writers):
        writer.flush(timeout)


atexit.register(flush_all)


class TraceWriter:
    def __init__(
        self,
        directory: Path,
        *,
        mode: str | None = None,
        max_files: int | None = None,
        max_age_days: float | None = None,
        queue_size: int = 256,
        prune_every: int = 200,
    ) -> None:
        self.directory = Path(directory)
        self.sample = sample_rate(mode or os.environ.get("RAG_TRACE") or "all")
        self.max_files = int(max_files if max_files is not None else os.environ.get("RAG_TRACE_MAX_FILES", "20000"))
        self.max_age_s = 86400.0 * float(
            max_age_days if max_age_days is not None else os.environ.get("RAG_TRACE_MAX_AGE_DAYS", "14"))
        self.prune_every = max(1, prune_every)
        self.dropped = 0
        self.written = 0
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        if self.sample > 0:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._thread = threading.Thread(target=self._run, name="trace-writer", daemon=True)
            self._thread.start()
            _writers.add(self)

    def submit(self, run_id: str, trace: dict[str, Any]) -> str | None:
        """Queue a trace; returns the path it will be written to, or None when not recorded."""
        if self.sample <= 0 or (self.sample < 1.0 and random.random() >= self.sample):
            return None
        path = self.directory / f"{run_id}.json"
        try:
            self._queue.put_nowait((path, trace))
        except queue.Full:
            self.dropped += 1
            return None
        return str(path)

    def flush(self, timeout: float = 5.0) -> None:
        """Wait (up to timeout) until every queued trace has been written."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    def _run(self) -> None:
        while True:
            path, trace = self._queue.get()
            try:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(trace, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                os.replace(tmp, path)
                self.written += 1
                if self.written % self.prune_every == 0:
                    self.prune()
            except Exception:  # noqa: BLE001 - tracing must never take the service down
                logger.exception("trace write failed: %s", path)
            finally:
                self._queue.task_done()

    def prune(self) -> int:
        """Delete traces beyond max_files (oldest first), traces older than max_age and orphaned
        .tmp files from interrupted writes; return the count removed."""
        files = sorted(_mtimes(self.directory.glob("*.json")), key=lambda x: x[0])
        now = time.time()
        excess = max(0, len(files) - self.max_files)
        doomed = [p for i, (mtime, p) in enumerate(files) if i < excess or now - mtime > self.max_age_s]
        doomed += [p for mtime, p in _mtimes(self.directory.glob("*.tmp")) if now - mtime > ORPHAN_TMP_AGE_S]
        removed = 0
        for p in doomed:
            try:
                p.unlink()
                removed += 1
            except FileNotFoundError:
                pass
        return removed
