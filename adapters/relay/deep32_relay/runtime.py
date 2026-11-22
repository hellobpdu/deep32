"""Private scratch and fixed-code operational logging; no transcript persistence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat
import time


class RuntimeErrorCode(ValueError):
    pass


def _link(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _regular(path):
    st = path.lstat()
    if _link(path) or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise RuntimeErrorCode("unsafe-file")
    return st


class Scratch:
    """Only removes the exact newly created directory beneath an owned root.

    validate_owner must check the existing installation's platform ownership/ACL.
    This is storage hygiene, never a claim that same-user children are sandboxed.
    """
    def __init__(self, parent, *, validate_owner):
        self.parent = Path(parent)
        self.validate_owner = validate_owner
        self.validate_owner()
        if _link(self.parent) or not self.parent.is_dir():
            raise RuntimeErrorCode("unsafe-scratch-root")
        self.parent_real = self.parent.resolve(strict=True)
        self.path = self.parent / secrets.token_hex(16)
        self.path.mkdir(mode=0o700)
        st = self.path.stat()
        self.identity = (st.st_dev, st.st_ino)
        self.closed = False

    def cleanup(self):
        if self.closed:
            return True
        self.validate_owner()
        if (_link(self.parent) or _link(self.path)
            or self.parent.resolve(strict=True) != self.parent_real
            or self.path.resolve(strict=True).parent != self.parent_real):
            return False
        st = self.path.stat()
        if (st.st_dev, st.st_ino) != self.identity:
            return False
        files, directories = [], []
        for base, dirs, names in os.walk(self.path, followlinks=False):
            for name in dirs:
                path = Path(base) / name
                if _link(path):
                    return False
                directories.append(path)
            for name in names:
                path = Path(base) / name
                try:
                    _regular(path)
                except (OSError, RuntimeErrorCode):
                    return False
                files.append(path)
            if len(files) + len(directories) > 4096:
                return False
        # Verify resolved deletion targets stay within this exact scratch first.
        scratch_real = self.path.resolve(strict=True)
        for path in files + directories:
            if not path.resolve(strict=True).is_relative_to(scratch_real):
                return False
        try:
            for path in files:
                _regular(path)
                path.unlink()
            for path in reversed(directories):
                path.rmdir()
            self.path.rmdir()
        except OSError:
            return False
        self.closed = True
        return True


EVENTS = frozenset(("service-check", "link-open", "link-close", "session-open",
                   "session-close", "turn-start", "turn-end", "cleanup-failed", "answer-check"))
REASONS = frozenset(("none", "unavailable", "cancelled", "timeout", "protocol",
                    "auth", "limit", "cleanup"))


TURN_REASONS = {"turn-timeout": "timeout", "cleanup-failed": "cleanup", "agent-sign-in-required": "auth"}


def log_turn(ledger, numbers):
    """Append one turn-end line for a finished turn: how it ended, its MCP requests (count), the calls the relay
    refused, the bytes the agent wrote to its standard output and the turn's duration. Counts only, no text.
    Returns False instead of raising: a log that cannot be written must never fail a turn."""
    try:
        status = numbers.get("status")
        reason = ("none" if status == "completed" else "cancelled" if status == "interrupted"
                  else TURN_REASONS.get(numbers.get("reason"), "unavailable"))
        ledger.append("turn-end", reason, count=int(numbers.get("calls", 0)),
                      refused=int(numbers.get("refused", 0)), bytes=int(numbers.get("output_bytes", 0)),
                      duration_ms=int(numbers.get("seconds", 0) * 1000))
        if numbers.get("check") == "failed":
            # The answer check itself broke: the answer went out without a footer, and this line says so.
            ledger.append("answer-check", "unavailable")
        return True
    except Exception:
        return False


class EventLedger:
    def __init__(self, path, *, validate_owner, clock=time.time, max_bytes=1048576):
        self.path = Path(path)
        self.validate_owner, self.clock = validate_owner, clock
        if not 1024 <= max_bytes <= 1048576:
            raise RuntimeErrorCode("invalid-log-limit")
        self.max_bytes, self.sequence = max_bytes, 0

    def append(self, event, reason="none", **counts):
        if event not in EVENTS or reason not in REASONS or set(counts) - {"count", "bytes", "duration_ms", "refused"}:
            raise RuntimeErrorCode("invalid-log-event")
        if any(type(v) is not int or not 0 <= v < 2**63 for v in counts.values()):
            raise RuntimeErrorCode("invalid-log-value")
        self.validate_owner()
        for path in [self.path] + [self.path.with_suffix(f".jsonl.{i}") for i in range(1, 4)]:
            if path.exists() or _link(path):
                _regular(path)
        self.sequence += 1
        data = (json.dumps({"time": int(self.clock()), "sequence": self.sequence,
             "event": event, "reason": reason, **counts}, separators=(",", ":")) + "\n").encode("ascii")
        if self.path.exists() and self.path.stat().st_size + len(data) > self.max_bytes:
            for index in range(3, 0, -1):
                dst = self.path.with_suffix(f".jsonl.{index}")
                src = self.path if index == 1 else self.path.with_suffix(f".jsonl.{index-1}")
                if src.exists():
                    src.replace(dst)
        fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
                raise RuntimeErrorCode("unsafe-file")
            os.write(fd, data)
        finally:
            os.close(fd)
