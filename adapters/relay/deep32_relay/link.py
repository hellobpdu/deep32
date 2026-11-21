"""Bounded transport-independent relay half of shipped D32U/1.

The transport owner verifies certificate possession and the pin/window policy.
Unknown peers remain in the bilateral SAS state until both commits complete.
"""
from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
import secrets
import threading
import time
from . import messages
from .pairing import Ceremony, PinRecord, PairingWindow
from .wire import (Error, Frame, FrameDecoder, ProtocolError, Type, END_MESSAGE,
                   FRAGMENT, REQUEST_MAX, RESPONSE_MAX, WINDOW, u32, read_u32)


@dataclass
class Stream:
    channel: int
    session: str = ""
    acked: bool = False
    closing: bool = False
    credit: int = WINDOW
    incoming: bytearray = field(default_factory=bytearray)
    outgoing: deque = field(default_factory=deque)
    offset: int = 0
    stalled: float | None = None
    response: object = None
    error: object = None
    requested: bool = False
    opened: bool = False
    published: bool = False
    guard: object = None


class Link:
    def __init__(self, local_id, peer_id, peer_pin, peer_der, *, authenticated=False,
                 nonce=None, profiles=(), clock=time.monotonic,
                 on_session_open=None, on_message=None, on_close=None,
                 local_pin=None, pairing_window=None, persist_peer=None):
        unpaired = peer_id is None
        if (authenticated is not True or not messages.identifier(local_id) or (not unpaired and not messages.identifier(peer_id))
                or not isinstance(peer_pin, bytes) or len(peer_pin) != 32 or not isinstance(peer_der, bytes)
                or not hmac.compare_digest(hashlib.sha256(peer_der).digest(), peer_pin)):
            raise ProtocolError(Error.AUTH)
        if unpaired and (not isinstance(pairing_window, PairingWindow) or not pairing_window.consumed
                         or clock() >= pairing_window.deadline or not callable(persist_peer)
                         or not isinstance(local_pin, bytes) or len(local_pin) != 32):
            raise ProtocolError(Error.AUTH)
        self.local_id, self.peer_id = local_id, peer_id
        self.peer_pin, self.local_pin = peer_pin, local_pin
        self.paired_local = not unpaired
        self.pairing_window, self.persist_peer = pairing_window, persist_peer
        self.ceremony = None
        self.nonce = secrets.token_bytes(32) if nonce is None else nonce
        self.local_hello = messages.hello(local_id, self.nonce, profiles)
        self.clock = clock
        self.on_session_open = on_session_open or (lambda stream, session: None)
        self.on_message = on_message or (lambda stream, message: None)
        self.on_close = on_close or (lambda stream, reason: None)
        self.state = "hello"
        self.decoder = FrameDecoder()
        self.streams = {}
        self.last_remote = 0
        self.next_local = 2
        self.used_sessions = 0
        self.outgoing = deque()
        self.outgoing_bytes = 0
        self.last_frame = self.last_ping = clock()
        self.deadline = self.last_frame + 10
        self.partial_since = None
        self.pings = deque(maxlen=4)
        self.rate_bucket = int(self.last_frame)
        self.rate_count = 0
        self.output_bucket = int(self.last_frame)
        self.output_count = 0
        self.condition = threading.Condition(threading.RLock())
        self.callbacks = deque()
        self.callback_runner = threading.Lock()

    def _emit(self, frame):
        encoded = frame.encode()
        if self.outgoing_bytes + len(encoded) > 4 * RESPONSE_MAX:
            raise ProtocolError(Error.LIMIT)
        self.outgoing.append((frame, encoded))
        self.outgoing_bytes += len(encoded)

    @staticmethod
    def _guard_live(guard):
        if guard is None:
            return True
        try:
            return guard() is True
        except Exception:
            return False

    def _callback(self, callback, stream_id, *args, critical=False):
        if critical and len(self.callbacks) >= 64:
            raise ProtocolError(Error.LIMIT)
        self.callbacks.append((callback, stream_id, args, critical))

    def _drain_callbacks(self):
        # Never wait for a runner that may itself be waiting for supervisor state.
        if not self.callback_runner.acquire(blocking=False):
            return False
        failed = False
        running = True
        try:
            while True:
                with self.condition:
                    if not self.callbacks:
                        # Atomic with appending under condition: no callback can
                        # be stranded between the empty check and runner release.
                        self.callback_runner.release()
                        running = False
                        return failed
                    callback, stream_id, args, critical = self.callbacks.popleft()
                    stream = self.streams.get(stream_id)
                    if critical and (self.state != "active" or stream is None or stream.closing):
                        continue
                try:
                    callback(stream_id, *args)
                except Exception:
                    if critical:
                        failed = True
                        with self.condition:
                            self._disconnect_locked(Error.SETTINGS_UNVERIFIED)
        finally:
            if running:
                self.callback_runner.release()
        return failed

    def take_output(self):
        with self.condition:
            bucket = int(self.clock())
            if bucket != self.output_bucket:
                self.output_bucket, self.output_count = bucket, 0
            parts = []
            # All frames share this budget, leaving margin below the peer's 256.
            while self.outgoing and self.output_count < 224:
                frame, data = self.outgoing[0]
                stream = self.streams.get(frame.stream)
                if frame.type in (Type.OPEN, Type.DATA) and stream is not None and stream.channel == 2 and not self._guard_live(stream.guard):
                    self._cancel_request(frame.stream)
                    continue
                self.outgoing.popleft()
                self.outgoing_bytes -= len(data)
                self.output_count += 1
                if frame.type == Type.OPEN and stream is not None:
                    stream.published = True
                parts.append(data)
            return b"".join(parts)

    def transmit(self, sender, pending=b""):
        """Send at most one frame under the guard/stream lock.

        Only a frame whose first bytes have reached TLS may live outside the
        queue. Rate is charged at that instant, including every control frame.
        """
        with self.condition:
            if pending:
                sent = sender(pending)
                if not 0 < sent <= len(pending):
                    raise ProtocolError(Error.RELAY_LOST)
                return pending[sent:]
            if self.state == "closed":
                return b""
            bucket = int(self.clock())
            if bucket != self.output_bucket:
                self.output_bucket, self.output_count = bucket, 0
            while self.outgoing and self.output_count < 224:
                frame, data = self.outgoing[0]
                stream = self.streams.get(frame.stream)
                if (frame.type in (Type.OPEN, Type.DATA) and stream is not None
                        and stream.channel == 2 and not self._guard_live(stream.guard)):
                    self._cancel_request(frame.stream)
                    continue
                sent = sender(data)
                if not 0 < sent <= len(data):
                    raise ProtocolError(Error.RELAY_LOST)
                self.outgoing.popleft()
                self.outgoing_bytes -= len(data)
                self.output_count += 1
                if frame.type == Type.OPEN and stream is not None:
                    stream.published = True
                return data[sent:]
            return b""

    def feed(self, data):
        failure = None
        with self.condition:
            if self.state == "closed":
                raise ProtocolError(Error.RELAY_LOST)
            try:
                frames = self.decoder.feed(data)
                for frame in frames:
                    now = self.clock()
                    bucket = int(now)
                    if bucket != self.rate_bucket:
                        self.rate_bucket, self.rate_count = bucket, 0
                    self.rate_count += 1
                    if self.rate_count > 256:
                        raise ProtocolError(Error.LIMIT)
                    self._receive(frame)
                    self.last_frame = now
                    self.partial_since = now if self.decoder.buffer else None
                if self.decoder.buffer and self.partial_since is None:
                    self.partial_since = self.clock()
                self.condition.notify_all()
            except ProtocolError as error:
                self._disconnect_locked(error.code)
                failure = error
            except Exception:
                self._disconnect_locked(Error.SETTINGS_UNVERIFIED)
                failure = ProtocolError(Error.SETTINGS_UNVERIFIED)
        callback_failed = self._drain_callbacks()
        if failure is not None:
            raise failure
        if callback_failed:
            raise ProtocolError(Error.SETTINGS_UNVERIFIED)

    def _receive(self, frame):
        kind = frame.type
        if kind == Type.ERROR:
            self._disconnect_locked(Error.PROTOCOL)
            return
        if self.state == "hello":
            if kind != Type.HELLO:
                raise ProtocolError()
            peer = messages.validate_hello(messages.loads(frame.payload), role="node")
            if self.peer_id is not None and peer["id"] != self.peer_id:
                raise ProtocolError(Error.AUTH)
            self.peer_id = peer["id"]
            self.peer_nonce = bytes.fromhex(peer["nonce"])
            self._emit(Frame(Type.HELLO, payload=messages.dumps(self.local_hello)))
            self._emit(Frame(Type.HELLO_ACK, payload=messages.dumps({"version": 1, "features": ["research-v1"], "paired": self.paired_local})))
            self.state, self.deadline = "ack", self.clock() + 10
            return
        if self.state == "ack":
            if kind != Type.HELLO_ACK:
                raise ProtocolError()
            ack = messages.validate_ack(messages.loads(frame.payload))
            if not (self.paired_local and ack["paired"]):
                window = self.pairing_window
                if (not isinstance(window, PairingWindow) or not callable(self.persist_peer)
                        or not isinstance(self.local_pin, bytes) or len(self.local_pin) != 32
                        or self.clock() >= window.deadline):
                    raise ProtocolError(Error.AUTH)
                if not window.consumed:
                    window.consume(self.clock())
                self.ceremony = Ceremony(self.local_pin, PinRecord(self.peer_id, self.peer_pin),
                                        self.peer_nonce, self.nonce, deadline=window.deadline,
                                        persist=self.persist_peer)
                self.state, self.deadline = "pair", window.deadline
                return
            self._go_active()
            return
        if self.state == "pair":
            for response in self.ceremony.receive(frame, self.clock()):
                self._emit(response)
            if self.ceremony.active:
                self._go_active()
            return
        if self.state != "active":
            raise ProtocolError()
        if kind in (Type.PAIR_CONFIRM, Type.PAIR_COMMIT):
            # A completed ceremony cannot be restarted over an active link.
            raise ProtocolError()
        if kind == Type.PING:
            self._emit(Frame(Type.PONG, payload=frame.payload))
            return
        if kind == Type.PONG:
            if frame.payload not in self.pings:
                raise ProtocolError()
            self.pings.remove(frame.payload)
            return
        if kind == Type.OPEN:
            self._open(frame)
            return
        stream = self.streams.get(frame.stream)
        if stream is None:
            retired = (frame.stream % 2 and frame.stream <= self.last_remote) or (not frame.stream % 2 and frame.stream < self.next_local)
            if retired and kind in (Type.CLOSE, Type.RESET, Type.WINDOW_UPDATE):
                return
            raise ProtocolError()
        if stream.channel != frame.channel:
            raise ProtocolError()
        if kind == Type.OPEN_ACK:
            if stream.acked or frame.stream % 2:
                raise ProtocolError()
            stream.acked = True
            stream.published = True
            self._flush(frame.stream, stream)
        elif kind == Type.WINDOW_UPDATE:
            increment = read_u32(frame.payload)
            if not stream.acked or not 1 <= increment <= WINDOW or stream.credit + increment > WINDOW:
                raise ProtocolError(Error.FLOW_CONTROL)
            stream.credit += increment
            self._flush(frame.stream, stream)
        elif kind == Type.DATA:
            if not stream.acked or stream.closing:
                raise ProtocolError()
            if len(frame.payload) > WINDOW:
                raise ProtocolError(Error.FLOW_CONTROL)
            limit = RESPONSE_MAX if stream.channel == 2 else REQUEST_MAX
            if len(stream.incoming) + len(frame.payload) > limit:
                self._reset(frame.stream, Error.LIMIT)
                return
            stream.incoming.extend(frame.payload)
            self._emit(Frame(Type.WINDOW_UPDATE, frame.stream, stream.channel, u32(len(frame.payload))))
            if frame.flags & END_MESSAGE:
                value = messages.loads(stream.incoming, limit)
                stream.incoming.clear()
                if stream.channel == 1:
                    self._callback(self.on_message, frame.stream, messages.validate_request(value), critical=True)
                elif stream.channel == 2:
                    if stream.response is not None:
                        raise ProtocolError()
                    stream.response = messages.validate_mcp_response(value)
                    self.close_stream(frame.stream)
                else:
                    messages.keys(value, ("code",))
                    if value["code"] not in messages.STATUS:
                        raise ProtocolError()
        elif kind == Type.CLOSE:
            if not stream.closing:
                self._emit(Frame(Type.CLOSE, frame.stream, stream.channel))
            self._retire(frame.stream, Error.CANCELLED)
        elif kind == Type.RESET:
            code = read_u32(frame.payload)
            self._retire(frame.stream, Error(code) if 1 <= code <= 11 else Error.PROTOCOL)
        else:
            raise ProtocolError()

    def _go_active(self):
        self.state = "active"
        self.last_frame = self.last_ping = self.clock()
        if self.ceremony is not None:
            self.ceremony.code = ""
            self.ceremony = None

    @property
    def pairing_code(self):
        with self.condition:
            return self.ceremony.code if self.state == "pair" and self.ceremony else None

    def confirm_pairing(self, typed):
        failure = None
        with self.condition:
            if self.state != "pair" or self.ceremony is None:
                raise ProtocolError(Error.AUTH)
            try:
                for frame in self.ceremony.confirm(typed, self.clock()):
                    self._emit(frame)
                if self.ceremony.active:
                    self._go_active()
            except ProtocolError as error:
                self._disconnect_locked(error.code)
                failure = error
        self._drain_callbacks()
        if failure:
            raise failure

    def _open(self, frame):
        # Native peer checks forbidden channel before ownership/body parsing.
        if frame.channel not in (1, 2, 4):
            self._emit(Frame(Type.RESET, frame.stream, frame.channel, u32(Error.CHANNEL_FORBIDDEN)))
            return
        if frame.channel == 2 or not frame.stream % 2 or frame.stream <= self.last_remote:
            raise ProtocolError()
        self.last_remote = frame.stream
        value = messages.validate_open(messages.loads(frame.payload), frame.channel)
        if len(self.streams) >= 64 or (frame.channel == 1 and self.used_sessions >= 16):
            self._emit(Frame(Type.RESET, frame.stream, frame.channel, u32(Error.LIMIT)))
            return
        session = value.get("session", "")
        if session and any(s.session == session and s.channel == 1 for s in self.streams.values()):
            raise ProtocolError()
        stream = Stream(frame.channel, session, acked=True, published=True)
        self.streams[frame.stream] = stream
        self._emit(Frame(Type.OPEN_ACK, frame.stream, frame.channel))
        if frame.channel == 1:
            self.used_sessions += 1
            self._callback(self.on_session_open, frame.stream, session, critical=True)
        else:
            self._send(frame.stream, {"code": "CONNECTED"}, REQUEST_MAX)

    def _send(self, stream_id, value, limit):
        stream = self.streams.get(stream_id)
        if stream is None or stream.closing:
            raise ProtocolError(Error.SESSION)
        payload = messages.dumps(value, limit)
        if sum(len(p) for p in stream.outgoing) + len(payload) > 2 * RESPONSE_MAX:
            raise ProtocolError(Error.LIMIT)
        stream.outgoing.append(payload)
        self._flush(stream_id, stream)

    def _flush(self, stream_id, stream):
        if not stream.acked or stream.closing:
            return
        while stream.outgoing and stream.credit:
            if not self._guard_live(stream.guard):
                self._cancel_request(stream_id)
                return
            payload = stream.outgoing[0]
            count = min(FRAGMENT, stream.credit, len(payload) - stream.offset)
            end = stream.offset + count == len(payload)
            self._emit(Frame(Type.DATA, stream_id, stream.channel, payload[stream.offset:stream.offset + count], END_MESSAGE if end else 0))
            stream.credit -= count
            stream.offset += count
            if end:
                stream.outgoing.popleft()
                stream.offset = 0
        if stream.outgoing and not stream.credit:
            if stream.stalled is None:
                stream.stalled = self.clock()
        else:
            stream.stalled = None

    def send_message(self, stream_id, event):
        with self.condition:
            stream = self.streams.get(stream_id)
            if self.state != "active" or stream is None or stream.channel != 1:
                raise ProtocolError(Error.SESSION)
            messages.validate_event(event, stream.session)
            if event["op"] == "session.opened":
                if stream.opened:
                    raise ProtocolError()
            self._send(stream_id, event, RESPONSE_MAX)
            if event["op"] == "session.opened":
                stream.opened = True

    def close_stream(self, stream_id):
        with self.condition:
            stream = self.streams.get(stream_id)
            if stream is not None and not stream.closing:
                stream.closing = True
                stream.outgoing.clear()
                self._emit(Frame(Type.CLOSE, stream_id, stream.channel))

    def _retire(self, stream_id, reason):
        stream = self.streams.pop(stream_id)
        stream.error = reason
        # A peer CLOSE/RESET can arrive while output is waiting for socket
        # writability. Never publish unstarted application bytes on its retired
        # id; retain the CLOSE reply and ignorable late control frames.
        self.outgoing = deque((frame, data) for frame, data in self.outgoing
                              if frame.stream != stream_id or frame.type not in (Type.OPEN, Type.DATA))
        self.outgoing_bytes = sum(len(data) for _, data in self.outgoing)
        if stream.channel == 1:
            self._callback(self.on_close, stream_id, reason)
            for sid, other in list(self.streams.items()):
                if sid in self.streams and other.channel == 2 and other.session == stream.session:
                    if self.state == "closed":
                        self._retire(sid, reason)
                    else:
                        self._cancel_request(sid)
        self.condition.notify_all()

    def _reset(self, stream_id, reason):
        stream = self.streams[stream_id]
        if stream.channel == 2:
            self._cancel_request(stream_id, reason)
            return
        self._emit(Frame(Type.RESET, stream_id, stream.channel, u32(reason)))
        self._retire(stream_id, reason)

    def _cancel_request(self, stream_id, reason=Error.CANCELLED):
        stream = self.streams[stream_id]
        retained = deque((frame, data) for frame, data in self.outgoing if frame.stream != stream_id)
        self.outgoing = retained
        self.outgoing_bytes = sum(len(data) for _, data in retained)
        stream.outgoing.clear()
        # An unsent OPEN is unknown to the peer: sending RESET would be fatal.
        if stream.published:
            try:
                self._emit(Frame(Type.RESET, stream_id, 2, u32(reason)))
            except ProtocolError:
                self._disconnect_locked(Error.LIMIT)
                return
        self._retire(stream_id, reason)

    def cancel_requests(self, session_id):
        with self.condition:
            for stream_id, stream in list(self.streams.items()):
                if stream.channel == 2 and stream.session == session_id:
                    self._cancel_request(stream_id)
        self._drain_callbacks()

    def reverse_request(self, session_id, envelope, timeout=170, guard=None):
        if not 0 < timeout <= 170:
            raise ProtocolError(Error.TIMEOUT)
        messages.validate_mcp_request(envelope)
        with self.condition:
            if self.state != "active" or not any(s.channel == 1 and s.session == session_id and s.opened and not s.closing for s in self.streams.values()):
                raise ProtocolError(Error.SESSION)
            if len(self.streams) >= 64 or sum(s.channel == 2 and s.session == session_id for s in self.streams.values()) >= 4:
                raise ProtocolError(Error.LIMIT)
            if not self._guard_live(guard):
                raise ProtocolError(Error.CANCELLED)
            stream_id = self.next_local
            if stream_id > 0xfffffffe:
                raise ProtocolError(Error.LIMIT)
            self.next_local += 2
            stream = Stream(2, session_id, requested=True, guard=guard)
            self.streams[stream_id] = stream
            self._emit(Frame(Type.OPEN, stream_id, 2, messages.dumps({"session": session_id})))
            self._send(stream_id, envelope, REQUEST_MAX)
            deadline = time.monotonic() + timeout
            while stream.response is None and stream.error is None and self.state != "closed":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._reset(stream_id, Error.TIMEOUT)
                    raise ProtocolError(Error.TIMEOUT)
                self.condition.wait(remaining)
            if stream.response is None:
                raise ProtocolError(stream.error or Error.RELAY_LOST)
            return stream.response

    def tick(self):
        try:
            self._tick()
        except ProtocolError as error:
            self.disconnect(error.code)
            raise
        finally:
            self._drain_callbacks()

    def _tick(self):
        with self.condition:
            now = self.clock()
            if self.state == "closed":
                return
            if self.state != "active":
                if now >= self.deadline:
                    self._disconnect_locked(Error.TIMEOUT)
                return
            if now - self.last_frame >= 45 or (self.partial_since is not None and now - self.partial_since >= 10):
                self._disconnect_locked(Error.TIMEOUT)
                return
            if now - self.last_ping >= 15:
                if len(self.pings) == 4:
                    self._disconnect_locked(Error.TIMEOUT)
                    return
                ping = secrets.token_bytes(8)
                self.pings.append(ping)
                self._emit(Frame(Type.PING, payload=ping))
                self.last_ping = now
            for stream_id, stream in list(self.streams.items()):
                if stream.stalled is not None and now - stream.stalled >= 180:
                    self._reset(stream_id, Error.TIMEOUT)

    def disconnect(self, reason=Error.RELAY_LOST):
        with self.condition:
            self._disconnect_locked(reason)
        self._drain_callbacks()

    def _disconnect_locked(self, reason):
        self.state = "closed"
        if self.ceremony is not None:
            self.ceremony.failed = True
            self.ceremony.code = ""
            self.ceremony = None
        self.callbacks = deque(entry for entry in self.callbacks if not entry[3])
        self.outgoing.clear()
        self.outgoing_bytes = 0
        for stream_id in list(self.streams):
            if stream_id in self.streams:
                self._retire(stream_id, reason)
        self.decoder.buffer.clear()
        self.condition.notify_all()
