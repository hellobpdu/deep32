"""Source-defined wire vectors; native-C execution is a separate open gate."""
import hashlib
from pathlib import Path
import struct
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adapters" / "relay"))
from deep32_relay import messages
from deep32_relay.link import Link
from deep32_relay.wire import (Frame, FrameDecoder, Type, Error, ProtocolError,
                               HEADER, MAX_PAYLOAD, WINDOW, END_MESSAGE, decode_frame, u32)


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now


def frames(data):
    return FrameDecoder().feed(data)


def paired(**kwargs):
    der = b"synthetic-transport-fixture-not-a-certificate"
    link = Link("relay", "node", hashlib.sha256(der).digest(), der,
                authenticated=True, nonce=bytes(range(32)), **kwargs)
    hello = messages.hello("node", bytes(reversed(range(32))))
    hello["role"] = "node"
    link.feed(Frame(Type.HELLO, payload=messages.dumps(hello)).encode())
    link.take_output()
    link.feed(Frame(Type.HELLO_ACK, payload=messages.dumps({"version": 1, "features": ["research-v1"], "paired": True})).encode())
    return link


def open_session(link, sid=1, session="s1"):
    link.feed(Frame(Type.OPEN, sid, 1, messages.dumps({"session": session})).encode())
    return frames(link.take_output())


class CodecTests(unittest.TestCase):
    def test_source_matches_reviewed_baseline(self):
        # Source drift invalidates these source-defined vectors; this is not a C oracle.
        expected = {
            "d32u.h": "2498040eb9ef32f8aab0f05f4a524a59fa8e62a41134f3f16f93880de72f4dea",
            "frame.c": "39160c7985c144d4c6db240062817b5ade4d1da4e683cf8d2a0cc0c17e31d987",
            "sas.c": "7b8c3d428af41cc7a462531161b91ed3c1c56eb43a3684d537d377d3ced5c97a",
            "util.c": "0e88284644400907434a6d81511a114afaba84e59a0a5bba7c9333f7fd9cede9",
            "ident.c": "b810082a6b94378cc6653555e57eaf163d39a25c55f2cd51a54411c278287d9a",
            "msg.c": "639fedc926520a81c3974bff5037276bb6cb0c3f24245613556af21ae43cdc9a",
            "link.c": "8d18b630eac527c4696b91f8b0c0c7b80ca0e26754f1e3631dd6925a4ec6c85f",
        }
        source = ROOT / "src" / "workstation" / "node" / "asklink" / "src"
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((source / name).read_bytes()).hexdigest(), digest, name)

    def test_exact_big_endian_vector(self):
        frame = Frame(Type.DATA, 0x01020304, 1, b"abc", END_MESSAGE)
        self.assertEqual(frame.encode().hex(), "000000030102030405010001616263")
        self.assertEqual(decode_frame(frame.encode()), frame)

    def test_all_thirteen_types(self):
        for kind in Type:
            payload = b""
            if kind in (Type.HELLO, Type.HELLO_ACK, Type.OPEN, Type.DATA):
                payload = b"{}"
            if kind in (Type.WINDOW_UPDATE, Type.RESET, Type.ERROR):
                payload = u32(1)
            if kind in (Type.PING, Type.PONG):
                payload = b"12345678"
            control = kind in (Type.HELLO, Type.HELLO_ACK, Type.PING, Type.PONG, Type.ERROR, Type.PAIR_CONFIRM, Type.PAIR_COMMIT)
            frame = Frame(kind, 0 if control else 1, 0 if control else 1, payload)
            self.assertEqual(decode_frame(frame.encode()), frame)

    def test_malformed_headers(self):
        cases = [(0, 0, 0, 0, 0), (0, 0, 14, 0, 0), (0, 1, 1, 0, 0),
                 (0, 0, 3, 1, 0), (1, 1, 5, 1, 2), (0, 1, 5, 1, 0),
                 (1, 0, 12, 0, 0), (0, 0, 13, 0, 1), (3, 1, 6, 1, 0)]
        for header in cases:
            with self.subTest(header=header), self.assertRaises(ProtocolError) as cm:
                decode_frame(HEADER.pack(*header) + b"x" * header[0])
            self.assertEqual(cm.exception.code, Error.PROTOCOL)

    def test_limit_checked_before_type(self):
        with self.assertRaises(ProtocolError) as cm:
            decode_frame(HEADER.pack(MAX_PAYLOAD + 1, 0, 255, 0, 0))
        self.assertEqual(cm.exception.code, Error.LIMIT)

    def test_control_limit(self):
        with self.assertRaises(ProtocolError) as cm:
            Frame(Type.HELLO, payload=b"x" * 16385).encode()
        self.assertEqual(cm.exception.code, Error.LIMIT)

    def test_split_and_coalesced(self):
        expected = [Frame(Type.PING, payload=b"12345678"), Frame(Type.PAIR_CONFIRM)]
        encoded = b"".join(f.encode() for f in expected)
        for split in range(len(encoded) + 1):
            decoder = FrameDecoder()
            self.assertEqual(decoder.feed(encoded[:split]) + decoder.feed(encoded[split:]), expected)
        decoder = FrameDecoder()
        actual = []
        for byte in encoded:
            actual.extend(decoder.feed(bytes([byte])))
        self.assertEqual(actual, expected)

    def test_exact_one_frame(self):
        for data in (b"", Frame(Type.PAIR_COMMIT).encode()[:-1], Frame(Type.PAIR_COMMIT).encode() + b"x"):
            with self.assertRaises(ProtocolError):
                decode_frame(data)


class LinkTests(unittest.TestCase):
    def test_authentication_seam_required(self):
        with self.assertRaises(ProtocolError):
            Link("relay", "node", b"x" * 32, b"fixture")

    def test_handshake_order_and_identity(self):
        der = b"fixture"
        link = Link("relay", "node", hashlib.sha256(der).digest(), der, authenticated=True)
        with self.assertRaises(ProtocolError):
            link.feed(Frame(Type.HELLO_ACK, payload=b"{}").encode())
        self.assertEqual(link.state, "closed")

    def test_open_and_callback(self):
        seen = []
        link = paired(on_session_open=lambda *args: seen.append(args))
        self.assertEqual(open_session(link)[0].type, Type.OPEN_ACK)
        self.assertEqual(seen, [(1, "s1")])

    def test_forbidden_channel_precedes_body(self):
        link = paired()
        link.feed(Frame(Type.OPEN, 2, 7, b"bad json").encode())
        self.assertEqual(frames(link.take_output())[0].payload, u32(Error.CHANNEL_FORBIDDEN))
        self.assertEqual(link.state, "active")

    def test_stream_parity_and_reuse(self):
        for stream in (2, 1):
            link = paired()
            if stream == 1:
                open_session(link)
                link.feed(Frame(Type.CLOSE, 1, 1).encode())
                link.take_output()
            with self.assertRaises(ProtocolError):
                open_session(link, stream)

    def test_request_fragment_and_credit(self):
        seen = []
        link = paired(on_message=lambda *args: seen.append(args))
        open_session(link)
        payload = messages.dumps({"op": "turn.start", "text": "hello"})
        link.feed(Frame(Type.DATA, 1, 1, payload[:5]).encode())
        self.assertEqual(seen, [])
        link.feed(Frame(Type.DATA, 1, 1, payload[5:], END_MESSAGE).encode())
        returned = frames(link.take_output())
        self.assertEqual([f.type for f in returned], [Type.WINDOW_UPDATE, Type.WINDOW_UPDATE])
        self.assertEqual(seen, [(1, {"op": "turn.start", "text": "hello"})])

    def test_outbound_credit_fragmentation(self):
        link = paired()
        open_session(link)
        link.send_message(1, {"op": "agent.delta", "session": "s1", "text": "x" * (WINDOW + 30)})
        sent = frames(link.take_output())
        self.assertEqual(sum(len(f.payload) for f in sent), WINDOW)
        self.assertTrue(all(len(f.payload) <= 65536 for f in sent))
        self.assertTrue(all(not f.flags for f in sent))
        link.feed(Frame(Type.WINDOW_UPDATE, 1, 1, u32(WINDOW)).encode())
        rest = frames(link.take_output())
        self.assertEqual(rest[-1].flags, END_MESSAGE)

    def test_invalid_window(self):
        for increment in (0, 1, WINDOW + 1):
            link = paired()
            open_session(link)
            with self.assertRaises(ProtocolError) as cm:
                link.feed(Frame(Type.WINDOW_UPDATE, 1, 1, u32(increment)).encode())
            self.assertEqual(cm.exception.code, Error.FLOW_CONTROL)

    def test_close_bilateral_and_late_frames(self):
        seen = []
        link = paired(on_close=lambda *args: seen.append(args))
        open_session(link)
        link.feed(Frame(Type.CLOSE, 1, 1).encode())
        self.assertEqual(frames(link.take_output())[0].type, Type.CLOSE)
        link.feed(Frame(Type.CLOSE, 1, 1).encode())
        link.feed(Frame(Type.WINDOW_UPDATE, 1, 1, u32(1)).encode())
        self.assertEqual(len(seen), 1)
        with self.assertRaises(ProtocolError):
            link.feed(Frame(Type.DATA, 1, 1, b"{}").encode())

    def test_lifetime_session_limit(self):
        link = paired()
        for i in range(16):
            open_session(link, i * 2 + 1, "s" + str(i))
            link.feed(Frame(Type.CLOSE, i * 2 + 1, 1).encode())
            link.take_output()
        refused = open_session(link, 33, "s17")
        self.assertEqual(refused[0].type, Type.RESET)
        self.assertEqual(refused[0].payload, u32(Error.LIMIT))

    def test_liveness_and_partial_timeout(self):
        clock = Clock()
        link = paired(clock=clock)
        clock.now = 15
        link.tick()
        ping = frames(link.take_output())[0]
        self.assertEqual(ping.type, Type.PING)
        link.feed(Frame(Type.PONG, payload=ping.payload).encode())
        clock.now = 16
        link.feed(b"\x00")
        clock.now = 26
        link.tick()
        self.assertEqual(link.state, "closed")

    def test_handshake_timeout(self):
        clock = Clock()
        der = b"fixture"
        link = Link("relay", "node", hashlib.sha256(der).digest(), der, authenticated=True, clock=clock)
        clock.now = 10
        link.tick()
        self.assertEqual(link.state, "closed")

    def test_received_rate_limit(self):
        link = paired(clock=Clock())
        with self.assertRaises(ProtocolError) as cm:
            link.feed(Frame(Type.PING, payload=b"12345678").encode() * 257)
        self.assertEqual(cm.exception.code, Error.LIMIT)

    def test_reverse_response_waits_for_ack_and_closes(self):
        link = paired()
        open_session(link)
        link.send_message(1, {"op": "session.opened", "session": "s1"})
        link.take_output()
        results = []
        def request():
            results.append(link.reverse_request("s1", {"body": None, "mcp_session_id": None}, timeout=2))
        thread = threading.Thread(target=request)
        thread.start()
        until = time.monotonic() + 1
        while not link.outgoing and time.monotonic() < until:
            time.sleep(0.001)
        opened = frames(link.take_output())
        self.assertEqual([f.type for f in opened], [Type.OPEN])
        link.feed(Frame(Type.OPEN_ACK, 2, 2).encode())
        self.assertEqual(frames(link.take_output())[0].type, Type.DATA)
        response = {"body": None, "mcp_session_id": None, "http_status": 202}
        link.feed(Frame(Type.DATA, 2, 2, messages.dumps(response), END_MESSAGE).encode())
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [response])
        self.assertEqual([f.type for f in frames(link.take_output())], [Type.WINDOW_UPDATE, Type.CLOSE])

    def test_disconnect_callback_failure_does_not_leave_streams(self):
        def fail(stream, reason):
            raise ValueError("synthetic private exception")
        link = paired(on_close=fail)
        open_session(link)
        open_session(link, 3, "s2")
        link.disconnect()
        self.assertEqual(link.streams, {})
        self.assertEqual(link.take_output(), b"")

    def test_reverse_before_session_opened_refused(self):
        link = paired()
        open_session(link)
        with self.assertRaises(ProtocolError) as cm:
            link.reverse_request("s1", {"body": None, "mcp_session_id": None})
        self.assertEqual(cm.exception.code, Error.SESSION)

    def test_credit_stall_timeout_retires_session(self):
        clock = Clock()
        link = paired(clock=clock)
        open_session(link)
        link.send_message(1, {"op": "agent.delta", "session": "s1", "text": "x" * (WINDOW + 100)})
        link.take_output()
        clock.now = 179
        link.feed(Frame(Type.PING, payload=b"12345678").encode())
        link.tick()
        link.take_output()
        self.assertIn(1, link.streams)
        clock.now = 180
        link.tick()
        reset = frames(link.take_output())
        self.assertEqual(reset[-1].type, Type.RESET)
        self.assertEqual(reset[-1].payload, u32(Error.TIMEOUT))
        self.assertNotIn(1, link.streams)

    def test_partial_byte_trickle_never_refreshes_deadline(self):
        clock = Clock()
        link = paired(clock=clock)
        link.feed(b"\x00")
        clock.now = 9
        link.feed(b"\x00")
        self.assertEqual(link.partial_since, 0)
        clock.now = 10
        link.tick()
        self.assertEqual(link.state, "closed")

    def test_supervisor_callback_error_is_fixed_and_closes(self):
        def fail(stream, message):
            raise ValueError("synthetic private callback detail")
        link = paired(on_message=fail)
        open_session(link)
        with self.assertRaises(ProtocolError) as cm:
            link.feed(Frame(Type.DATA, 1, 1, messages.dumps({"op": "turn.start", "text": "hello"}), END_MESSAGE).encode())
        self.assertEqual(str(cm.exception), "SETTINGS_UNVERIFIED")
        self.assertEqual(link.streams, {})

    def admitted(self, **kwargs):
        link = paired(**kwargs)
        open_session(link)
        link.send_message(1, {"op": "session.opened", "session": "s1"})
        link.take_output()
        return link

    def reverse_thread(self, link, guard=None):
        errors = []
        def invoke():
            try:
                link.reverse_request("s1", {"body": None, "mcp_session_id": None}, timeout=3, guard=guard)
            except ProtocolError as error:
                errors.append(error.code)
        thread = threading.Thread(target=invoke)
        thread.start()
        until = time.monotonic() + 1
        while not link.outgoing and thread.is_alive() and time.monotonic() < until:
            time.sleep(0.001)
        return thread, errors

    def test_reverse_guard_refusal_has_zero_open(self):
        link = self.admitted()
        with self.assertRaises(ProtocolError) as cm:
            link.reverse_request("s1", {"body": None, "mcp_session_id": None}, guard=lambda: False)
        self.assertEqual(cm.exception.code, Error.CANCELLED)
        self.assertEqual(link.take_output(), b"")
        self.assertEqual(set(link.streams), {1})

    def test_revoke_promptly_resets_and_wakes_reverse_waiter(self):
        link = self.admitted()
        thread, errors = self.reverse_thread(link)
        self.assertEqual(frames(link.take_output())[0].type, Type.OPEN)
        link.cancel_requests("s1")
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [Error.CANCELLED])
        reset = frames(link.take_output())
        self.assertEqual([f.type for f in reset], [Type.RESET])
        self.assertEqual(reset[0].payload, u32(Error.CANCELLED))

    def test_revoke_before_open_publication_removes_open(self):
        link = self.admitted()
        thread, errors = self.reverse_thread(link)
        link.cancel_requests("s1")
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [Error.CANCELLED])
        self.assertEqual(link.take_output(), b"")

    def test_revoked_guard_before_delayed_ack_sends_zero_data(self):
        live = [True]
        link = self.admitted()
        thread, errors = self.reverse_thread(link, lambda: live[0])
        self.assertEqual(frames(link.take_output())[0].type, Type.OPEN)
        live[0] = False
        link.feed(Frame(Type.OPEN_ACK, 2, 2).encode())
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [Error.CANCELLED])
        self.assertEqual([f.type for f in frames(link.take_output())], [Type.RESET])

    def test_revoked_guard_removes_already_queued_request_data(self):
        live = [True]
        link = self.admitted()
        thread, errors = self.reverse_thread(link, lambda: live[0])
        link.take_output()
        link.feed(Frame(Type.OPEN_ACK, 2, 2).encode())
        live[0] = False
        self.assertEqual([f.type for f in frames(link.take_output())], [Type.RESET])
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [Error.CANCELLED])

    def test_callbacks_never_hold_transport_condition(self):
        supervisor_lock = threading.Lock()
        entered, disconnected = threading.Event(), threading.Event()
        def callback(stream, value):
            entered.set()
            with supervisor_lock:
                pass
        link = self.admitted(on_message=callback)
        supervisor_lock.acquire()
        feeder = threading.Thread(target=lambda: link.feed(Frame(Type.DATA, 1, 1, messages.dumps({"op": "turn.interrupt"}), END_MESSAGE).encode()))
        feeder.start()
        self.assertTrue(entered.wait(1))
        disconnector = threading.Thread(target=lambda: (link.disconnect(), disconnected.set()))
        disconnector.start()
        try:
            self.assertTrue(disconnected.wait(0.5))
        finally:
            supervisor_lock.release()
            feeder.join(1)
            disconnector.join(1)
        self.assertFalse(feeder.is_alive())
        self.assertFalse(disconnector.is_alive())

    def test_callback_queue_is_bounded_and_retired_questions_skip(self):
        seen = []
        link = paired(on_message=lambda *args: seen.append(args))
        open_session(link)
        message = Frame(Type.DATA, 1, 1, messages.dumps({"op": "turn.start", "text": "synthetic question"}), END_MESSAGE).encode()
        with self.assertRaises(ProtocolError) as cm:
            link.feed(message * 65)
        self.assertEqual(cm.exception.code, Error.LIMIT)
        self.assertEqual(seen, [])
        self.assertEqual(link.state, "closed")


if __name__ == "__main__":
    unittest.main()
