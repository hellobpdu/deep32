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
    message_start: int = 0          # where the agent's latest message begins in delta_text
    break_pending: bool = False     # that message's first text gets a paragraph break before it
    check_state: str = "off"        # this turn's answer check: off, done or failed


class Supervisor:
    """One admitted session, two queued opens; engines launch only on turn.start.

    Agent.verify(scratch,capability) must attest startup before start receives the
    question. start returns a handle with poll() -> iterable normalised events and
    cancel() -> bool (true only once the entire owned tree is known empty).
    scratch_factory(session) returns an owned context with .path and .cleanup().
    """
    def __init__(self, agent, gateway, emit, scratch_factory, *, clock=time.monotonic,
                 turn_limit=3600, queue_limit=2, queue_wait=30, idle_limit=60,
                 lifetime=3600, on_close=None, on_turn=None, checks="off", on_answer=None, on_save=None):
        # An idle conversation may be kept for up to 15 minutes (the service passes DEEP32_RELAY_IDLE_SECONDS,
        # settings.py); the default here stays the 60 seconds every earlier release had.
        if (not 0 < turn_limit <= 3600 or not 0 <= queue_limit <= 2 or
            not 0 < queue_wait <= 30 or not 0 < idle_limit <= 900 or not 0 < lifetime <= 3600
                or checks not in ("footer", "off")):
            raise SessionError("invalid-policy")
        # "footer": every finished answer is checked by answer_check.py and its footer is shown after the answer.
        self.checks = checks
        # on_answer(turn) writes the optional answer record (record.py): what answered, on what, and the counts.
        self.on_answer = on_answer
        # on_save(turn) is given each finished answer's text to save in the operator's vault (vault.py).
        self.on_save = on_save
        # keep each turn's tool calls for them
        self.capture = checks == "footer" or on_answer is not None or on_save is not None
        self.agent, self.gateway, self.emit, self.scratch_factory = agent, gateway, emit, scratch_factory
        self.clock = clock
        self.lock = threading.RLock()
        self.accepting = True
        self.on_close = on_close or (lambda stream: None)
        # on_turn(numbers) is told how each turn ended: counts for the service's log, never text.
        self.on_turn = on_turn or (lambda numbers: None)
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

    @staticmethod
    def _tell(hook, value):
        """Call one of the service's hooks (the turn log, the answer record). A hook that fails never fails a
        turn: the result only says whether it ran."""
        try:
            hook(value)
            return True
        except Exception:
            return False

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
        s.message_start, s.break_pending, s.check_state = 0, False, "off"
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
                s.capability.capture = self.capture
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
                if op == "delta" and s.break_pending and text:
                    # The first text of a further assistant message: a paragraph break goes out before it.
                    gap = "" if s.delta_text.endswith("\n\n") else "\n" if s.delta_text.endswith("\n") else "\n\n"
                    s.message_start, s.break_pending = len(s.delta_text) + len(gap), False
                    text = gap + text
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
            elif op == "break" and set(event) == {"type"}:
                # A further assistant message begins (the agent spoke, called tools, speaks again).
                if s.final_seen:
                    raise SessionError("invalid-agent-output")
                s.message_start, s.break_pending = len(s.delta_text), bool(s.delta_text)
            elif op == "completed" and set(event) == {"type", "usage"}:
                if s.answer_bytes == 0:
                    raise SessionError("empty-output")
                usage = event["usage"]
                if (not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens"}
                    or any(type(v) is not int or v < 0 or v >= 2**63 for v in usage.values())):
                    raise SessionError("usage-unavailable")
                s.usage = usage.copy()
                self._after_answer(s)
                self._finish(s, "completed", "none")
            else:
                raise SessionError("invalid-agent-output")
        except Exception:
            self._finish(s, "failed", "invalid-agent-output")
            return False
        return True

    def _after_answer(self, s):
        """The answer is complete and its tool calls are still at hand: check it by script and show the footer.

        Never raises, never changes the answer, calls no model. The footer goes to the Ask window only: it is no
        part of the answer the session remembers for a follow-up question.
        """
        if self.checks != "footer" and self.on_answer is None and self.on_save is None:
            return
        result = events = None
        if self.checks == "footer" or self.on_answer is not None:
            try:
                from . import answer_check
                with self.gateway.lock:
                    events = list(getattr(s.capability, "events", None) or ())
                # Claude's final text is the answer. Codex has none: its answer is its last message.
                answer = s.final_text if s.final_seen else (s.delta_text[s.message_start:] or s.delta_text)
                result = answer_check.check(answer, events, question=s.question)
                text = answer_check.footer(result)
                s.check_state = "done" if text else "failed"
                if text and self.checks == "footer":
                    self._footer(s, text)
            except Exception:
                s.check_state = "failed"
        if self.on_save is not None:
            self._save(s, events)
        if self.on_answer is None:
            return
        try:
            # The record gets what answered, the ids the tools returned and the check's counts: never any text.
            from . import answer_check
            describe = getattr(s.process, "describe", None)
            about = dict(describe() if callable(describe) else {})
            about.update(model_choice=s.model, effort_choice=s.effort)
            turn = {"agent": about, "usage": dict(s.usage or {}),
                    "index": answer_check.index(events or ()),
                    "calls": answer_check.summarize(events or ()),
                    "check": answer_check.redact(result) if isinstance(result, dict) else None}
        except Exception:
            turn = None         # a record that cannot be made never fails an answer
        if turn is not None:
            self._tell(self.on_answer, turn)

    def _save(self, s, events):
        """Hand the finished answer to the vault (on_save, vault.AnswerVault.submit, which only queues it): the
        question, the answer as the Ask window received it (the streamed text, or the final text when nothing
        streamed; the Checks footer is not part of it), what answered and the turn's tool calls."""
        try:
            if events is None:
                with self.gateway.lock:
                    events = list(getattr(s.capability, "events", None) or ())
            describe = getattr(s.process, "describe", None)
            about = dict(describe() if callable(describe) else {})
            turn = {"question": s.question, "answer": s.delta_text or s.final_text,
                    "model": str(about.get("model") or s.model), "effort": str(about.get("effort") or s.effort),
                    "events": events}
        except Exception:
            return              # an answer that cannot be handed over is not saved; the turn goes on
        self._tell(self.on_save, turn)

    def _footer(self, s, text):
        """Send the footer as answer text after everything the agent wrote. The Ask window shows streamed text and
        ignores the final message once any streamed text came, so this is the one path it displays."""
        text = "\n\n" + text + "\n"
        text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32 and not 127 <= ord(c) <= 159)
        size = len(text.encode("utf-8"))
        if s.answer_bytes + size > MAX_ANSWER:
            return              # the answer used its whole bound: it goes out without the footer
        s.answer_bytes += size
        for start in range(0, len(text), 8192):
            self._event(s, "agent.delta", text=text[start:start + 8192])

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
        try:
            # How the turn went, for the service's log: tool calls, refused calls, the agent's output bytes, time.
            numbers = {"calls": int(getattr(s.capability, "calls", 0) or 0),
                       "refused": int(getattr(s.capability, "refused", 0) or 0),
                       "output_bytes": int(getattr(s.process, "output_bytes", 0) or 0),
                       "seconds": max(0.0, self.clock() - (s.deadline - self.turn_limit)),
                       "check": s.check_state}
        except Exception:
            numbers = {"calls": 0, "refused": 0, "output_bytes": 0, "seconds": 0.0, "check": "off"}
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
        self._tell(self.on_turn, dict(numbers, status=status, reason=reason))
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
            elif s.state == "idle" and now - s.idle_at >= (min(self.idle_limit, 60) if self.queue
                                                           else self.idle_limit):
                # One session is admitted at a time. While another one waits, an idle session keeps the slot
                # for a minute, as it always did; with nobody waiting it keeps its context for idle_limit.
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
