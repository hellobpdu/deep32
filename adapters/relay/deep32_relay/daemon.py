"""Owned relay reactor. Transport pumping never waits for native startup.

Dependencies are explicit so offline tests exercise the same composition without
opening a socket, registering a service or starting a native agent.
"""
from __future__ import annotations

from collections import deque
import os
import queue
import threading
import time

from .runtime import Scratch
from .service import make_bridge
from .state import StateError
from .status import (REASONS, choice_id, process_identity, publish_status,
                     unavailable_snapshot, validate_snapshot)
from .wire import Error, ProtocolError


class RelayReactor:
    def __init__(self, listener, agent, scratch_factory, *, clock=time.monotonic,
                 wall_clock=time.time, publish=None, install=None,
                 identity_observer=process_identity, settings=None, on_turn=None, on_answer=None):
        self.listener, self.agent = listener, agent
        self.scratch_factory = scratch_factory
        self.settings = settings        # settings.Settings of the service; None keeps the supervisor's defaults
        self.on_turn = on_turn          # told each finished turn's numbers (the service's log)
        self.on_answer = on_answer      # given each finished answer's record, when the operator asked for records
        self.clock, self.wall_clock = clock, wall_clock
        self.publish = publish or (lambda value: None)
        self.install = install
        self.identity_observer = identity_observer
        self.bridge = None
        self.turns = deque()
        self.pending = {}
        self.lock = threading.RLock()
        self.jobs = queue.Queue(maxsize=4)
        self.stopping = threading.Event()
        self.worker = None
        self.cleanup_failed = False
        self.last_result = {"state": "none", "reason": "none", "observed_at": 0}
        self.last_seen_result = None
        self.last_published = None
        self.published_at = float("-inf")
        self.started = False
        self.closed = False
        listener.on_link = self.attach

    def start(self):
        if self.started or self.closed or not self.agent.ready:
            raise StateError("native-agent-unqualified")
        self.listener.start()
        self.started = True
        self.worker = threading.Thread(target=self._work, name="deep32-turn", daemon=True)
        self.worker.start()
        self._publish(force=True)

    def attach(self, link):
        with self.lock:
            if self.closed or self.stopping.is_set():
                raise ProtocolError(Error.RELAY_LOST)
            old = self.bridge
            if self.pending:
                # The old revoked startup must finish cleanup before a new
                # connection can receive any application capability.
                raise ProtocolError(Error.SETTINGS_UNVERIFIED)
        if old is not None:
            old.disconnect()
            self._remember_result(old.supervisor)
            if old.supervisor.cleanup_failed:
                self.cleanup_failed = True
                raise ProtocolError(Error.SETTINGS_UNVERIFIED)
        bridge = make_bridge(link, self.agent, self.scratch_factory, clock=self.clock, settings=self.settings,
                             on_turn=self.on_turn, on_answer=self.on_answer)
        # A reconnect does not reset the installation's hourly quota.
        bridge.supervisor.turns = self.turns
        with self.lock:
            self.bridge = bridge
        bind = getattr(self.agent, "bind_gateway", None)
        if bind is not None:
            bind(bridge.supervisor.gateway)
        link.on_message = lambda stream, value: self.message(bridge, stream, value)
        original_close = link.on_close
        def close(stream, reason):
            with self.lock:
                self.pending.pop((id(bridge), stream), None)
            original_close(stream, reason)
        link.on_close = close

    def message(self, bridge, stream, value):
        if isinstance(value, dict) and value.get("op") == "turn.start":
            reject = False
            with self.lock:
                key = (id(bridge), stream)
                if (self.closed or self.stopping.is_set() or bridge is not self.bridge
                        or key in self.pending):
                    self.pending.pop(key, None)
                    reject = True
                else:
                    ticket = object()
                    self.pending[key] = ticket
                    try:
                        self.jobs.put_nowait((bridge, stream, value, ticket))
                    except queue.Full:
                        self.pending.pop(key, None)
                        reject = True
            if reject:
                bridge.supervisor.close(stream)
            return
        # Interrupt/close bypass the startup worker; revocation can therefore
        # happen while an engine is still verifying or returning its handle.
        if isinstance(value, dict) and value.get("op") in ("turn.interrupt", "session.close"):
            with self.lock:
                self.pending.pop((id(bridge), stream), None)
        bridge.message(stream, value)

    def _work(self):
        while not self.stopping.is_set():
            try:
                bridge, stream, value, ticket = self.jobs.get(timeout=.05)
            except queue.Empty:
                continue
            key = (id(bridge), stream)
            try:
                with self.lock:
                    live = (not self.stopping.is_set() and bridge is self.bridge
                            and self.pending.get(key) is ticket)
                if live:
                    if set(value) != {"op", "text"}:
                        bridge.supervisor.close(stream)
                    else:
                        def guard():
                            with self.lock:
                                return (not self.stopping.is_set() and bridge is self.bridge
                                        and self.pending.get(key) is ticket)
                        bridge.supervisor.start(stream, value["text"], guard=guard)
            except Exception:
                bridge.supervisor.close(stream)
            finally:
                with self.lock:
                    if self.pending.get(key) is ticket:
                        self.pending.pop(key, None)
                self.jobs.task_done()

    def step(self, timeout=.1):
        if not self.started or self.closed or not 0 <= timeout <= 1:
            raise StateError("relay-not-running")
        self.listener.poll(timeout=timeout)
        bridge = self.bridge
        if bridge is not None:
            bridge.supervisor.tick()
            self._remember_result(bridge.supervisor)
            if bridge.supervisor.cleanup_failed:
                self.cleanup_failed = True
                self.close()
                return
        self._publish()

    def _remember_result(self, supervisor):
        result = dict(supervisor.last_result)
        if result != self.last_seen_result and result["status"] != "none":
            reason = result["reason"] if result["reason"] in REASONS else "protocol-error"
            self.last_result = {"state": result["status"], "reason": reason,
                                "observed_at": self.wall_clock()}
            # The session's advertised model and effort (never prompt or answer text).
            if (result.get("model") in self.agent.models and result.get("effort") in self.agent.efforts
                    and choice_id(result["model"]) and choice_id(result["effort"])):
                self.last_result.update(model=result["model"], effort=result["effort"])
        self.last_seen_result = result

    def snapshot(self):
        if self.install is None:
            return None
        item = self.install
        value = unavailable_snapshot(item["install_id"], item["agent"], item["release_id"],
                                     agent_found=True, now=self.wall_clock())
        identity = self.identity_observer(os.getpid())
        running = self.started and not self.closed and not self.cleanup_failed and identity is not None
        # An uncertain shutdown cannot publish the pid-zero acknowledgement used
        # by removal to authorise deleting runtime/package files.
        observed = identity is not None and (running or self.cleanup_failed)
        value.update(pid=os.getpid() if observed else 0,
                     instance_id=identity if observed else "unknown", running=running,
                     box_state="qualified" if self.agent.ready else "unqualified",
                     capability_id=item.get("capability_id", "unqualified"),
                     last_session_result=dict(self.last_result))
        peer = getattr(self.listener, "peer", None)
        value.update(paired_workstation=peer is not None,
                     workstation_alias="workstation" if peer is not None else "none")
        if self.closed or not self.started:
            value["link_state"] = "error" if self.cleanup_failed else "off"
        elif self.bridge is None:
            value["link_state"] = "listening"
        else:
            link = self.bridge.link
            value["link_state"] = "connected" if link.state == "active" else "disconnected"
            supervisor = self.bridge.supervisor
            value["queue_depth"] = len(supervisor.queue)
            session = supervisor.sessions.get(supervisor.admitted)
            value["session_state"] = session.state if session is not None else "none"
        return validate_snapshot(value)

    def _publish(self, force=False):
        value = self.snapshot()
        if value is None:
            return
        signature = {key: item for key, item in value.items() if key != "observed_at"}
        elapsed = self.clock() - self.published_at
        if force or elapsed >= 5 or (elapsed >= 1 and signature != self.last_published):
            self.publish(value)
            self.last_published, self.published_at = signature, self.clock()

    def close(self):
        if self.closed:
            return not self.cleanup_failed
        self.closed = True
        self.stopping.set()
        with self.lock:
            self.pending.clear()
        try:
            if self.bridge is not None:
                self.bridge.disconnect()
                self._remember_result(self.bridge.supervisor)
                self.cleanup_failed |= self.bridge.supervisor.cleanup_failed
        except Exception:
            self.cleanup_failed = True
        finally:
            try:
                self.listener.close()
            except Exception:
                self.cleanup_failed = True
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)
            self.cleanup_failed |= self.worker.is_alive()
        if self.bridge is not None:
            self.cleanup_failed |= bool(self.bridge.supervisor.sessions)
        self._publish(force=True)
        return not self.cleanup_failed


def stop_requested(owner, install_id):
    """Read an owned cooperative stop request; never signal an observed PID."""
    try:
        value = owner.read_json("runtime/stop-request.json", 1024)
    except FileNotFoundError:
        return False
    if value != {"schema": "deep32.relay-stop.v1", "install_id": install_id}:
        raise StateError("invalid-stop-request")
    return True


def turn_logger(owner):
    """The service's turn log, logs/events.jsonl in the relay's own folder: one line of counts per turn
    (runtime.log_turn). A log that cannot be set up or written is skipped; it never stops the relay or a turn."""
    from .runtime import EventLedger, log_turn
    try:
        owner.mkdir("logs")
        ledger = EventLedger(owner.path("logs/events.jsonl"), validate_owner=owner.validate)
    except Exception:
        return None
    return lambda numbers: log_turn(ledger, numbers)


def run_owned(owner, *, stop_event=None):
    """Run only a fully qualified, already paired, explicit installation.

    There is no paid startup probe, automatic pairing or receipt-based override.
    Missing native/provider artefacts are fixed-code failures before bind.
    """
    from .connector import validate_receipt, _verify_files
    from .engine import native_from_owned
    from .identity import IdentityStore
    from .tls import load_provider
    from .transport import Listener
    receipt = validate_receipt(owner)
    _verify_files(owner, receipt)
    config = owner.read_json("config.json", 4096)
    if receipt["state"] == "removed" or not config["intake_enabled"]:
        raise StateError("relay-intake-disabled")
    # Operator settings come from this process's environment, never from the recorded configuration above.
    from .settings import load
    settings = load(os.environ, receipt["agent"])
    agent = native_from_owned(owner, receipt)
    if not agent.ready:
        raise StateError("native-agent-unqualified")
    # What the default model and effort choice of a conversation runs (DEEP32_RELAY_MODEL, DEEP32_RELAY_EFFORT).
    agent.pin_model, agent.pin_effort = settings.model, settings.effort
    provider = load_provider()
    store = IdentityStore(owner)
    with owner.lock():
        identity = store.load_or_create(provider)
        peer = store.load_peer()
    if peer is None:
        raise StateError("pairing-required")
    records = None
    if settings.answer_record:
        from .record import AnswerRecords
        records = AnswerRecords(owner, receipt, agent)
    profiles = ({"name": "research", "models": list(agent.models),
                 "efforts": list(agent.efforts)},)
    listener = Listener(identity, peer, provider=provider, port=config["port"], profiles=profiles)
    reactor = RelayReactor(listener, agent,
        lambda ident: Scratch(owner.path("runtime/sessions"), validate_owner=owner.validate),
        publish=lambda value: publish_status(owner, value), install=receipt, settings=settings,
        on_turn=turn_logger(owner), on_answer=records.write if records is not None else None)
    stopped = stop_event or threading.Event()
    try:
        reactor.start()
        while not stopped.is_set() and not stop_requested(owner, receipt["install_id"]):
            reactor.step()
            if reactor.closed:
                break
    finally:
        clean = reactor.close()
    if not clean:
        raise StateError("cleanup-unconfirmed")
    return 0
