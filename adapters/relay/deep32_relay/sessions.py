"""Offline-testable session policy for the shipped research-v1 link dialect."""
from __future__ import annotations

import collections
import functools
import threading
import time
from dataclasses import dataclass, field

from .proxy import encoded
from .wire import Error

MAX_TEXT = 65536
MAX_ANSWER = 1048576
MAX_HISTORY = 262144


class SessionError(ValueError):
    pass


def _failure(error, fallback):
    # A signed-out agent is the one turn failure the operator can repair (connect --relay login), so it keeps its
    # own fixed local reason. Every other native error stays behind the stage's fixed reason.
    return "agent-sign-in-required" if str(error) == "claude-sign-in-required" else fallback


def synchronised(method):
    @functools.wraps(method)
    def run(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return run


@dataclass(repr=False)
class Session:
    stream: int
    ident: str
    opened_at: float
    state: str = "opening"
    idle_at: float = 0
    deadline: float = 0
    epoch: int = 0
    process: object = None
    capability: object = None
    scratch: object = None
    answer_bytes: int = 0
    final_seen: bool = False
    final_text: str = ""
    delta_text: str = ""
    history: list = field(default_factory=list)
    question: str = ""
    usage: dict | None = None
    spawn_pending: bool = False
    pending_end: tuple | None = None
    close_requested: bool = False
    model: str = ""
    effort: str = ""


class Supervisor:
    """One admitted session, two queued opens; engines launch only on turn.start.

    Agent.verify(scratch,capability) must attest startup before start receives the
    question. start returns a handle with poll() -> iterable normalised events and
    cancel() -> bool (true only once the entire owned tree is known empty).
    scratch_factory(session) returns an owned context with .path and .cleanup().
    """
    def __init__(self, agent, gateway, emit, scratch_factory, *, clock=time.monotonic,
                 turn_limit=3600, queue_limit=2, queue_wait=30, idle_limit=60,
                 lifetime=3600, on_close=None):
        if (not 0 < turn_limit <= 3600 or not 0 <= queue_limit <= 2 or
            not 0 < queue_wait <= 30 or not 0 < idle_limit <= 60 or not 0 < lifetime <= 3600):
            raise SessionError("invalid-policy")
        self.agent, self.gateway, self.emit, self.scratch_factory = agent, gateway, emit, scratch_factory
        self.clock = clock
        self.lock = threading.RLock()
        self.accepting = True
        self.on_close = on_close or (lambda stream: None)
        self.turn_limit, self.queue_limit, self.queue_wait = turn_limit, queue_limit, queue_wait
        self.idle_limit, self.lifetime = idle_limit, lifetime
        self.sessions = {}
        self.queue = collections.deque()
        self.admitted = None
        self.opens = 0
        self.turns = collections.deque()
        self.last_result = {"status": "none", "reason": "none", "usage_known": False}
        self.cleanup_failed = False
        self.last_stream = 0

    def _event(self, s, op, **values):
        value = {"op": op, "session": s.ident, **values}
        encoded(value, MAX_ANSWER)
        self.emit(s.stream, value)

    def _error(self, s, code):
        self._event(s, "error", code=int(code))

    @synchronised
    def open(self, stream, session_id, request):
        if (type(stream) is not int or stream <= self.last_stream or stream % 2 != 1
                or type(session_id) is not str or not 1 <= len(session_id) <= 64
                or any(not(c.isascii() and (c.isalnum() or c in "_-")) for c in session_id)):
            raise SessionError("invalid-stream")
        self.last_stream = stream
        s = Session(stream, session_id, self.clock())
        required = {"op", "profile", "kind", "skill", "model", "effort", "caller_class"}
        if (not isinstance(request, dict) or set(request) != required
            or request.get("op") != "session.open" or request.get("profile") != "research"
            or request.get("kind") != "research" or request.get("skill") != "deep32"
            or request.get("caller_class") not in ("research", "research-relay")
            or request.get("model") not in self.agent.models or request.get("effort") not in self.agent.efforts):
            self._error(s, Error.PROTOCOL)
            return False
        # The Workstation fills an omitted choice with the first advertised pair, the qualified one. The
        # session keeps its own pair for every turn; the agent receives nothing outside the advertised lists.
        s.model, s.effort = request["model"], request["effort"]
        if not self.accepting or self.cleanup_failed or not self.agent.ready:
            self._error(s, Error.SETTINGS_UNVERIFIED if self.cleanup_failed else Error.CODEX_UNAVAILABLE)
            return False
        if self.opens >= 16 or any(v.ident == session_id for v in self.sessions.values()):
            self._error(s, Error.LIMIT)
            return False
        self.opens += 1
        if self.admitted is not None and len(self.queue) >= self.queue_limit:
            self._error(s, Error.LIMIT)
            return False
        self.sessions[stream] = s
        if self.admitted is None:
            self._admit(s)
        else:
            s.state = "queued"
            self.queue.append(stream)
        return True

    @synchronised
    def _admit(self, s):
        if self.cleanup_failed:
            return
        if s.state == "queued" and self.clock() - s.opened_at >= self.queue_wait:
            self._error(s, Error.TIMEOUT)
            self.sessions.pop(s.stream, None)
            self.on_close(s.stream)
            if self.queue:
                self._admit(self.sessions[self.queue.popleft()])
            return
        self.admitted = s.stream
        s.state, s.idle_at = "idle", self.clock()
        self._event(s, "session.opened")

    def request(self, stream, value):
        s = self.sessions.get(stream)
        if s is None or not isinstance(value, dict):
            raise SessionError("invalid-session")
        op = value.get("op")
        if op == "session.close" and set(value) == {"op"}:
            self.close(stream)
        elif op == "turn.interrupt" and set(value) == {"op"}:
            self.interrupt(stream)
        elif op == "turn.start" and set(value) == {"op", "text"}:
            self.start(stream, value["text"])
        else:
            raise SessionError("invalid-request")

    @synchronised
    def _reserve_start(self, stream, text, guard=None):
        # The transport may queue a turn away from its TLS pump. Cancellation
        # invalidates that ticket; check it under the reservation lock before
        # allocating scratch, a capability or a process.
        if guard is not None and guard() is not True:
            return None
        s = self.sessions.get(stream)
        if s is None or s.state != "idle" or self.cleanup_failed:
            raise SessionError("session-not-idle")
        if not isinstance(text, str) or not text or "\x00" in text:
            raise SessionError("invalid-question")
        try:
            if len(text.encode("utf-8")) > MAX_TEXT:
                raise SessionError("question-limit")
            encoded({"op": "turn.start", "text": text}, MAX_TEXT)
        except (UnicodeError, ValueError):
            raise SessionError("question-limit") from None
        now = self.clock()
        if now - s.opened_at >= self.lifetime or now - s.idle_at >= self.idle_limit:
            self.close(stream, "session-timeout")
            return None
        while self.turns and now - self.turns[0] >= 3600:
            self.turns.popleft()
        if len(self.turns) >= 30:
            self._error(s, Error.LIMIT)
            return None
        self.turns.append(now)
        s.state, s.deadline = "verifying", now + self.turn_limit
        s.epoch += 1
        s.answer_bytes, s.final_seen = 0, False
        s.delta_text = s.final_text = ""
        s.question, s.usage = text, None
        return s

    def start(self, stream, text, *, guard=None):
        s = self._reserve_start(stream, text, guard)
        if s is None:
            return
        try:
            with self.lock:
                if s.state != "verifying":
                    return
                s.scratch = self.scratch_factory(s.ident)
                s.capability = self.gateway.grant(s.ident, s.deadline)
                scratch, cap, epoch = s.scratch, s.capability, s.epoch
            # Native verification and spawn are outside the state lock. A
            # cancellation can revoke the capability immediately in either.
            if not self.agent.verify(scratch.path, cap):
                raise SessionError("startup-unverified")
            with self.lock:
                if self.clock() >= s.deadline or s.state != "verifying" or s.epoch != epoch:
                    raise SessionError("turn-timeout")
                self.gateway.activate(cap)
                s.spawn_pending = True
                history = tuple(s.history)
            # The qualified native spawn primitive must honour the live cap at
            # its OS creation boundary. No native primitive is enabled here.
            process = self.agent.start(text, history, scratch.path, cap, model=s.model, effort=s.effort)
            with self.lock:
                s.spawn_pending = False
                s.process = process
                if s.pending_end is not None:
                    status, reason = s.pending_end
                    s.pending_end = None
                    s.state = "verifying"
                    self._finish(s, status, reason)
                elif process is None:
                    self._finish(s, "failed", "agent-start-failed")
                elif self.clock() >= s.deadline:
                    self._finish(s, "failed", "turn-timeout")
                else:
                    # This assignment and the interrupt path share one lock.
                    s.state = "running"
        except Exception as error:
            with self.lock:
                s.spawn_pending = False
                if s.pending_end is not None:
                    status, reason = s.pending_end
                    s.pending_end = None
                    s.state = "verifying"
                    self._finish(s, status, reason)
                else:
                    self._finish(s, "failed", _failure(error, "agent-start-failed"))

    @synchronised
    def output(self, stream, epoch, event):
        s = self.sessions.get(stream)
        if s is None or s.state != "running" or s.epoch != epoch:
            return False
        if self.clock() >= s.deadline:
            self._finish(s, "failed", "turn-timeout")
            return False
        try:
            if not isinstance(event, dict):
                raise SessionError("invalid-agent-output")
            op = event.get("type")
            if op in ("delta", "final") and set(event) == {"type", "text"}:
                text = event["text"]
                if not isinstance(text, str) or s.final_seen:
                    raise SessionError("invalid-agent-output")
                text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32 and not 127 <= ord(c) <= 159)
                data = text.encode("utf-8")
                s.answer_bytes += len(data)
                if s.answer_bytes > MAX_ANSWER:
                    raise SessionError("answer-limit")
                if op == "final":
                    s.final_seen, s.final_text = True, text
                else:
                    s.delta_text += text
                # Bound JSON escaping overhead as well as raw UTF-8.
                parts = [text[i:i+8192] for i in range(0, len(text), 8192)] or [""]
                if op == "final":
                    # final is a complete answer, never several final fragments.
                    encoded({"op": "agent.final", "session": s.ident, "text": text}, MAX_ANSWER)
                    self._event(s, "agent.final", text=text)
                else:
                    for part in parts:
                        self._event(s, "agent.delta", text=part)
            elif op == "completed" and set(event) == {"type", "usage"}:
                if s.answer_bytes == 0:
                    raise SessionError("empty-output")
                usage = event["usage"]
                if (not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens"}
                    or any(type(v) is not int or v < 0 or v >= 2**63 for v in usage.values())):
                    raise SessionError("usage-unavailable")
                s.usage = usage.copy()
                self._finish(s, "completed", "none")
            else:
                raise SessionError("invalid-agent-output")
        except Exception:
            self._finish(s, "failed", "invalid-agent-output")
            return False
        return True

    @synchronised
    def _finish(self, s, status, reason):
        if s.state not in ("verifying", "running"):
            return
        s.state = "draining"
        revoked = True
        try:
            self.gateway.revoke(s.ident)
        except Exception:
            revoked = False
            self.cleanup_failed = True
        if s.spawn_pending:
            # Keep scratch and execution slot until the returned owned handle
            # can be cancelled. Never detach a late child or admit another turn.
            s.pending_end = (status, reason) if revoked else ("failed", "cleanup-failed")
            return
        clean = True
        if s.process is not None:
            try:
                clean = s.process.cancel() is True
            except Exception:
                clean = False
        s.process = None
        if s.scratch is not None and clean:
            try:
                clean = s.scratch.cleanup() is True
            except Exception:
                clean = False
        clean = clean and revoked
        if not clean:
            self.cleanup_failed = True
            status, reason = "failed", "cleanup-failed"
        if status == "completed":
            s.history.extend((s.question, s.final_text if s.final_seen else s.delta_text))
            while s.history and sum(len(x.encode("utf-8")) for x in s.history) > MAX_HISTORY:
                del s.history[:2]
        usage_known = s.usage is not None
        # Wire requires two integers even on failure. Zero on failed/interrupted
        # with usage_known=False is explicitly an unavailable sentinel, not usage.
        self._event(s, "turn.completed", status=status,
                    usage=s.usage or {"input_tokens": 0, "output_tokens": 0})
        self.last_result = {"status": status, "reason": reason, "usage_known": usage_known,
                            "model": s.model, "effort": s.effort}
        s.question = s.final_text = s.delta_text = ""
        s.capability = s.scratch = None
        s.state, s.idle_at = ("closing" if not clean else "idle"), self.clock()
        if s.close_requested:
            self.close(s.stream, reason)

    @synchronised
    def interrupt(self, stream):
        s = self.sessions.get(stream)
        if s and s.state in ("verifying", "running"):
            self._finish(s, "interrupted", "cancelled")

    @synchronised
    def close(self, stream, reason="session-closed"):
        s = self.sessions.get(stream)
        if not s:
            return
        if s.spawn_pending:
            s.close_requested = True
            self._finish(s, "interrupted", reason)
            return
        if s.state in ("verifying", "running"):
            self._finish(s, "interrupted", reason)
        s.history.clear()
        s.state = "closed"
        del self.sessions[stream]
        self.on_close(stream)
        try:
            self.queue.remove(stream)
        except ValueError:
            pass
        if self.admitted == stream:
            self.admitted = None
            if self.queue and not self.cleanup_failed:
                self._admit(self.sessions[self.queue.popleft()])

    def tick(self):
        now = self.clock()
        with self.lock:
            snapshot = list(self.sessions.items())
        for stream, s in snapshot:
            if stream not in self.sessions:
                continue
            if s.state == "queued" and now - s.opened_at >= self.queue_wait:
                self._error(s, Error.TIMEOUT)
                self.close(stream, "queue-timeout")
            elif now - s.opened_at >= self.lifetime:
                self.close(stream, "session-timeout")
            elif s.state in ("verifying", "running") and now >= s.deadline:
                self._finish(s, "failed", "turn-timeout")
            elif s.state == "idle" and now - s.idle_at >= self.idle_limit:
                self.close(stream, "idle-timeout")
            elif s.state == "running":
                try:
                    events = s.process.poll()
                    for index, event in enumerate(events):
                        if index >= 256:
                            raise SessionError("output-limit")
                        self.output(stream, s.epoch, event)
                except Exception as error:
                    self._finish(s, "failed", _failure(error, "agent-output-failed"))

    @synchronised
    def disconnect(self):
        # Remove queued entries first so closing the active session cannot admit
        # another session while link loss is being handled.
        self.queue.clear()
        self.accepting = False
        for stream in list(self.sessions):
            self.close(stream, "link-lost")
        if not self.sessions:
            self.admitted = None
