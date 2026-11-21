"""Offline R12 policy/pump/SAS tests with an explicitly fake TLS backend.

No socket, provider install, native agent or model request is performed. Actual
pyOpenSSL handshakes remain a separate gate until its locked wheels arrive.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adapters/relay"))
from deep32_relay import messages
from deep32_relay.identity import Identity, IdentityStore, WindowsKeyProtector
from deep32_relay.link import Link, Stream
from deep32_relay.pairing import PairingWindow, PinRecord, sas_code
from deep32_relay.state import OwnedRoot, StateError, OWNER_SCHEMA
from deep32_relay.tls import (OpenSSLProvider, PeerPolicy, TLSUnavailable, load_provider,
                              provider_readiness, certificate_pin)
from deep32_relay.transport import Listener, TransportConnection, ReconnectBackoff, pair_console
from deep32_relay.wire import Error, Frame, FrameDecoder, ProtocolError, Type


class Clock:
    now = 100.0
    def __call__(self):
        return self.now


class WantRead(Exception):
    pass


class WantWrite(Exception):
    pass


class Context:
    def __init__(self, library):
        self.library, self.values = library, {}
    def set_min_proto_version(self, value): self.values["min"] = value
    def set_max_proto_version(self, value): self.values["max"] = value
    def set_options(self, value): self.values["options"] = value
    def set_session_cache_mode(self, value): self.values["cache"] = value
    def use_certificate(self, value): self.values["cert"] = value
    def use_privatekey(self, value): self.values["key"] = value
    def check_privatekey(self): pass
    def set_verify(self, flags, callback):
        self.values["verify"] = flags
        self.callback = callback


class Raw:
    def __init__(self):
        self.closed = False
        self.accepts = []
    def setblocking(self, value): self.blocking = value
    def close(self): self.closed = True
    def setsockopt(self, *args): pass
    def bind(self, value): self.bound = value
    def listen(self, backlog): self.backlog = backlog
    def accept(self): return self.accepts.pop(0)


class FakeTLS:
    def __init__(self, library, context):
        self.library, self.context = library, context
        self.inputs, self.output = list(library.inputs), bytearray()
        self.max_send = 10000000
        self.handshakes = 0
    def set_accept_state(self): pass
    def do_handshake(self):
        self.handshakes += 1
        if self.library.wait_handshake:
            raise WantRead()
        if not self.context.callback(self, self.library.der, 99, 0, False):
            raise ValueError("synthetic TLS certificate rejected")
    def get_peer_certificate(self): return self.library.der
    def get_protocol_version_name(self): return self.library.version
    def send(self, data):
        count = min(len(data), self.max_send)
        self.output.extend(data[:count])
        return count
    def recv(self, maximum):
        if not self.inputs:
            raise WantRead()
        return self.inputs.pop(0)
    def pending(self): return len(self.inputs)


class SSLLibrary:
    TLS_SERVER_METHOD, TLS1_3_VERSION, OP_NO_TICKET, SESS_CACHE_OFF = 1, 0x304, 2, 0
    VERIFY_PEER, VERIFY_FAIL_IF_NO_PEER_CERT = 1, 2
    WantReadError, WantWriteError = WantRead, WantWrite
    def __init__(self, der=b"synthetic node DER"):
        self.der, self.version = der, "TLSv1.3"
        self.inputs = []
        self.wait_handshake = False
    def Context(self, method):
        return Context(self)
    def Connection(self, context, raw):
        self.connection = FakeTLS(self, context)
        return self.connection


CRYPTO = SimpleNamespace(FILETYPE_PEM=1, FILETYPE_ASN1=2,
    load_certificate=lambda kind, data: data,
    load_privatekey=lambda kind, data: data,
    dump_certificate=lambda kind, data: data)


def identity():
    return Identity("relay", b"synthetic certificate", b"synthetic private key", b"L" * 32)


def hello(ident="node"):
    value = messages.hello(ident, b"N" * 32)
    value["role"] = "node"
    return Frame(Type.HELLO, payload=messages.dumps(value)).encode()


def ack(paired):
    return Frame(Type.HELLO_ACK, payload=messages.dumps(dict(version=1, features=["research-v1"], paired=paired))).encode()


def ready_wait(read, write, errors, timeout):
    return read, write, []


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.der = b"synthetic DER certificate"
        self.peer = PinRecord("node", certificate_pin(self.der))

    def test_unarmed_unknown_never_reserves_or_accepts(self):
        policy = PeerPolicy(clock=self.clock)
        self.assertFalse(policy.accept_der(self.der))
        with self.assertRaises(ProtocolError): policy.begin_attempt()
        self.assertFalse(policy.reserved)

    def test_armed_one_attempt_and_original_deadline(self):
        window = PairingWindow(self.clock())
        policy = PeerPolicy(window=window, clock=self.clock)
        policy.begin_attempt()
        self.assertTrue(policy.accept_der(self.der))
        with self.assertRaises(ProtocolError): policy.begin_attempt()
        with self.assertRaises(ProtocolError): PeerPolicy(window=window, clock=self.clock).begin_attempt()
        self.clock.now += 300
        self.assertFalse(policy.accept_der(self.der))

    def test_pinned_exact_only_even_in_armed_window(self):
        policy = PeerPolicy(self.peer, PairingWindow(self.clock()), clock=self.clock)
        policy.begin_attempt()
        self.assertTrue(policy.accept_der(self.der))
        self.assertFalse(policy.accept_der(b"synthetic other DER"))
        self.assertFalse(policy.window.consumed)
        self.clock.now += 500
        self.assertTrue(policy.accept_der(self.der))

    def test_callback_ignores_chain_time_but_requests_certificate_and_tls13(self):
        library = SSLLibrary(self.der)
        provider = OpenSSLProvider(library, CRYPTO)
        policy = PeerPolicy(self.peer, clock=self.clock)
        policy.begin_attempt()
        context = provider.context(identity(), policy)
        self.assertEqual(context.values["min"], 0x304)
        self.assertEqual(context.values["max"], 0x304)
        self.assertEqual(context.values["verify"], 3)
        self.assertEqual(context.values["cache"], 0)
        self.assertTrue(context.callback(None, self.der, 10, 0, False))
        self.assertFalse(context.callback(None, b"other", 0, 0, True))
        self.assertTrue(context.callback(None, b"untrusted chain", 19, 1, False))

    def test_armed_callback_any_leaf_only_until_expiry(self):
        provider = OpenSSLProvider(SSLLibrary(), CRYPTO)
        policy = PeerPolicy(window=PairingWindow(self.clock()), clock=self.clock)
        policy.begin_attempt()
        context = provider.context(identity(), policy)
        self.assertTrue(context.callback(None, b"synthetic unknown", 18, 0, False))
        self.clock.now += 300
        self.assertFalse(context.callback(None, b"synthetic unknown", 18, 0, False))

    def test_dependency_gate_is_closed_without_installed_approved_versions(self):
        # R15 supplies the authoritative wheels/lock. An interpreter without
        # its exact selected versions must still refuse the provider.
        from unittest.mock import patch
        import importlib.metadata
        with patch('importlib.metadata.version', side_effect=importlib.metadata.PackageNotFoundError):
            self.assertFalse(provider_readiness())
            with self.assertRaises(TLSUnavailable) as result: load_provider()
            self.assertEqual(str(result.exception), "tls-provider-unqualified")

    def test_reconnect_schedule_pins_only_and_active_reset(self):
        backoff = ReconnectBackoff(randbelow=lambda count: 200)
        self.assertEqual([backoff.delay(self.peer) for _ in range(8)], [1, 2, 4, 8, 16, 30, 30, 30])
        backoff.active()
        self.assertEqual(backoff.delay(self.peer), 1)
        with self.assertRaises(ProtocolError): backoff.delay(None)


class PairingLinkTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.der = b"synthetic DER certificate"
        self.saved = []
        self.window = PairingWindow(self.clock())
        self.window.consume(self.clock())
        self.link = Link("relay", None, certificate_pin(self.der), self.der, authenticated=True,
            clock=self.clock, nonce=b"R" * 32, local_pin=b"L" * 32,
            pairing_window=self.window, persist_peer=self.saved.append)

    def begin(self):
        self.link.feed(hello())
        frames = FrameDecoder().feed(self.link.take_output())
        self.assertFalse(messages.loads(frames[1].payload)["paired"])
        self.link.feed(ack(False))
        self.assertEqual(self.link.state, "pair")

    def test_bilateral_source_sas_and_commits_required(self):
        self.begin()
        self.assertEqual(self.link.pairing_code, sas_code(b"L"*32, certificate_pin(self.der), b"N"*32, b"R"*32))
        self.link.confirm_pairing(self.link.pairing_code.lower())
        self.assertEqual(self.saved, [])
        self.link.feed(Frame(Type.PAIR_CONFIRM).encode())
        self.assertEqual(self.saved, [PinRecord("node", certificate_pin(self.der))])
        self.assertEqual(self.link.state, "pair")
        self.link.feed(Frame(Type.PAIR_COMMIT).encode())
        self.assertEqual(self.link.state, "active")
        self.assertIsNone(self.link.pairing_code)

    def test_bad_words_close_without_pin(self):
        self.begin()
        with self.assertRaises(ProtocolError): self.link.confirm_pairing("WRONG WORDS")
        self.assertEqual(self.link.state, "closed")
        self.assertEqual(self.saved, [])
        self.assertIsNone(self.link.pairing_code)

    def test_unarmed_unknown_constructor_is_refused(self):
        with self.assertRaises(ProtocolError):
            Link("relay", None, certificate_pin(self.der), self.der, authenticated=True)

    def test_pinned_hello_id_must_match(self):
        link = Link("relay", "node", certificate_pin(self.der), self.der, authenticated=True)
        with self.assertRaises(ProtocolError) as result: link.feed(hello("changed-node"))
        self.assertEqual(result.exception.code, Error.AUTH)

    def test_original_window_not_reset_by_hello_or_words(self):
        self.clock.now += 280
        self.begin()
        self.assertEqual(self.link.deadline, 400)
        self.clock.now = 400
        with self.assertRaises(ProtocolError): self.link.confirm_pairing(self.link.pairing_code)
        self.assertEqual(self.saved, [])

    def test_pairing_rejects_application_frames(self):
        self.begin()
        with self.assertRaises(ProtocolError): self.link.feed(Frame(Type.PING, payload=b"12345678").encode())
        self.assertEqual(self.saved, [])

    def test_peer_close_discards_unstarted_data_but_keeps_close_reply(self):
        link = Link("relay", "node", certificate_pin(self.der), self.der, authenticated=True, clock=self.clock)
        link.feed(hello()+ack(True))
        link.take_output()
        link.streams[1] = Stream(1, "synthetic-session", acked=True, published=True)
        link._emit(Frame(Type.DATA, 1, 1, b"synthetic pending response"))
        link.feed(Frame(Type.CLOSE, 1, 1).encode())
        frames = FrameDecoder().feed(link.take_output())
        self.assertEqual([frame.type for frame in frames], [Type.CLOSE])
        self.assertEqual(link.outgoing_bytes, 0)


class PumpTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.library = SSLLibrary()
        self.provider = OpenSSLProvider(self.library, CRYPTO)
        self.peer = PinRecord("node", certificate_pin(self.library.der))
        self.raw = Raw()

    def connection(self, **kwargs):
        return TransportConnection(self.raw, identity(), PeerPolicy(self.peer, clock=self.clock),
            self.provider, clock=self.clock, wait=ready_wait, **kwargs)

    def test_callback_precedes_any_received_frame_and_short_writes(self):
        observed = []
        self.library.inputs = [hello() + ack(True)]
        connection = self.connection(on_link=lambda link: observed.append(link.state))
        link = connection.pump(0)
        self.assertEqual(observed, ["hello"])
        self.assertEqual(link.state, "active")
        connection.tls.max_send = 3
        for _ in range(200): connection.pump(0)
        frames = FrameDecoder().feed(connection.tls.output)
        self.assertEqual([frame.type for frame in frames], [Type.HELLO, Type.HELLO_ACK])

    def test_handshake_deadline_no_reset_from_partial_activity(self):
        self.library.wait_handshake = True
        connection = self.connection()
        connection.pump(0)
        self.clock.now += 10
        connection.pump(0)
        self.assertTrue(self.raw.closed)
        self.assertEqual(connection.last_error, Error.TIMEOUT)

    def test_post_handshake_pin_and_version_rechecked(self):
        self.library.version = "TLSv1.2"
        connection = self.connection()
        connection.pump(0)
        self.assertTrue(connection.closed)
        self.assertEqual(connection.last_error, Error.AUTH)

    def test_eof_disconnects_sessions_and_clears_queued_output(self):
        self.library.inputs = [hello() + ack(True), b""]
        connection = self.connection()
        connection.pump(0)
        self.assertTrue(connection.closed)
        self.assertEqual(connection.link.state, "closed")
        self.assertEqual(connection.tx, b"")

    def test_revocation_during_wait_discards_unstarted_channel2_frames(self):
        self.library.inputs = [hello()+ack(True)]
        connection = self.connection()
        link = connection.pump(0)
        connection.pump(0)
        connection.tls.output.clear()
        live = [True]
        link.streams[2] = Stream(2, "synthetic-session", guard=lambda: live[0])
        link._emit(Frame(Type.OPEN, 2, 2, messages.dumps({"session":"synthetic-session"})))
        def revoke(read, write, errors, timeout):
            live[0] = False
            return [], write, []
        connection.wait = revoke
        connection.pump(0)
        self.assertEqual(connection.tls.output, b"")
        self.assertNotIn(2, link.streams)

    def test_rate_is_charged_at_first_actual_write(self):
        self.library.inputs = [hello()+ack(True)]
        connection = self.connection()
        link = connection.pump(0)
        connection.wait = lambda read, write, errors, timeout: ([], [], [])
        for _ in range(230): link._emit(Frame(Type.PING, payload=b"12345678"))
        connection.pump(0)
        self.assertEqual(link.output_count, 0)
        self.clock.now += 1
        connection.wait = ready_wait
        connection.pump(0)
        self.assertEqual(link.output_count, 224)
        self.assertGreater(len(link.outgoing), 0)

    def test_tls_want_retry_uses_identical_object_and_revocation_drops_link(self):
        self.library.inputs = [hello()+ack(True)]
        connection = self.connection()
        link = connection.pump(0)
        connection.pump(0)
        live = [True]
        link.streams[2] = Stream(2, "synthetic-session", guard=lambda: live[0])
        link._emit(Frame(Type.OPEN, 2, 2, messages.dumps({"session":"synthetic-session"})))
        attempts = []
        def blocked(data):
            attempts.append(data)
            raise WantWrite()
        connection.tls.send = blocked
        connection.pump(0)
        self.assertIsNotNone(connection.tls_retry)
        connection.pump(0)
        self.assertIs(attempts[0], attempts[1])
        live[0] = False
        connection.pump(0)
        self.assertTrue(connection.closed)
        self.assertEqual(len(attempts), 2)  # No RESET/ERROR/new buffer reaches TLS.
        self.assertEqual(connection.last_error, Error.CANCELLED)

    def test_listener_loopback_only_and_failed_unknown_attempt_consumed(self):
        listening, accepted = Raw(), Raw()
        listening.accepts = [(accepted, ("127.0.0.1", 12345))]
        window = PairingWindow(self.clock())
        listener = Listener(identity(), None, provider=self.provider, port=17452,
            pairing_window=window, socket_factory=lambda *a: listening, clock=self.clock, wait=ready_wait)
        listener.start()
        self.assertEqual(listening.bound, ("127.0.0.1", 17452))
        self.library.version = "TLSv1.2"
        listener.poll(0)
        self.assertTrue(window.consumed)
        self.assertTrue(accepted.closed)
        listener.close()
        self.assertTrue(listening.closed)


class FakeSecurity:
    def mkdir(self, path): os.mkdir(path, 0o700)
    def protect_file(self, path): pass
    def verify(self, path, directory=False):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (not directory and info.st_nlink != 1):
            raise StateError("unsafe-state")


class KeyProtector:
    def protect(self, value): return b"synthetic-wrapped:" + value[::-1]
    def unprotect(self, value):
        if not value.startswith(b"synthetic-wrapped:"): raise StateError("identity-protection-required")
        return value[len(b"synthetic-wrapped:"):][::-1]


class IdentityTests(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ["DEEP32_RELAY_TEST_ROOT"]).resolve()
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-identity-", dir=base)
        self.addCleanup(self.temp.cleanup)
        self.owner = OwnedRoot(Path(self.temp.name)/"owned", FakeSecurity())
        self.owner.create()
        self.owner.write_json("owner.json", dict(schema=OWNER_SCHEMA, install_id="1"*32))
        self.store = IdentityStore(self.owner, KeyProtector())
        self.provider = SimpleNamespace(generate_identity=lambda: (b"synthetic-cert", b"synthetic-key"),
            validate_identity=lambda cert, key: b"synthetic DER" if (cert,key)==(b"synthetic-cert",b"synthetic-key") else (_ for _ in ()).throw(ValueError()))

    def test_identity_generation_protected_key_stable_restart_and_repr(self):
        first = self.store.load_or_create(self.provider)
        self.assertEqual(first, self.store.load_or_create(self.provider))
        self.assertNotIn(b"synthetic-key", self.owner.read("private/link/node.key"))
        self.assertNotIn("synthetic-key", repr(first))
        self.assertEqual(first.pin, certificate_pin(b"synthetic DER"))

    def test_incomplete_identity_refused_not_rotated(self):
        self.owner.mkdir("private/link")
        self.owner.write("private/link/node.key", b"synthetic-partial")
        with self.assertRaises(StateError) as result: self.store.load_or_create(self.provider)
        self.assertEqual(str(result.exception), "identity-incomplete")

    def test_missing_identity_with_durable_pin_is_never_regenerated(self):
        self.owner.mkdir("private/link")
        self.owner.write("private/link/peer.pin", PinRecord("node", b"P"*32).encode())
        with self.assertRaises(StateError) as result: self.store.load_or_create(self.provider)
        self.assertEqual(str(result.exception), "identity-missing-with-peer")

    def test_asklink_pin_format_persistence_and_explicit_revocation(self):
        self.store.load_or_create(self.provider)
        record = PinRecord("node", b"P"*32)
        self.store.save_peer(record)
        self.assertEqual(self.owner.read("private/link/peer.pin"), record.encode())
        self.assertEqual(self.store.load_peer(), record)
        with self.assertRaises(StateError): self.store.save_peer(PinRecord("changed", b"Q"*32))
        self.store.revoke_peer()
        self.assertIsNone(self.store.load_peer())

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_actual_dpapi_synthetic_fixture_roundtrip_only(self):
        protector = WindowsKeyProtector()
        value = b"synthetic-test-private-key-not-a-real-credential"
        try:
            protected = protector.protect(value)
        except StateError:
            self.skipTest("CurrentUser DPAPI unavailable under managed task token (Windows error 2 observed); no host qualification")
        self.assertNotIn(value, protected)
        self.assertEqual(protector.unprotect(protected), value)
        with self.assertRaises(StateError): protector.unprotect(value)

    def test_dpapi_failure_refuses_identity_without_plaintext_fallback(self):
        class DeniedProtector:
            def protect(self, value): raise StateError("identity-protection-failed")
        store = IdentityStore(self.owner, DeniedProtector())
        with self.assertRaises(StateError) as result: store.load_or_create(self.provider)
        self.assertEqual(str(result.exception), "identity-protection-failed")
        self.assertFalse(self.owner.path("private/link/node.key").exists())

    def test_malformed_provider_lock_is_unqualified(self):
        lock = Path(self.temp.name)/"synthetic-lock.json"
        lock.write_text('{"schema":"x","schema":"y"}', encoding="ascii")
        self.assertFalse(provider_readiness(lock))

    def test_console_flushes_local_commit_before_success_and_close(self):
        self.owner.write_json("config.json", {"port":17452})
        clock, saved, output = Clock(), [], []
        class ConsoleListener:
            last_error, connection = None, None
            def __init__(self):
                self.clock, self.link, self.polls, self.closed = clock, None, 0, False
                self.sent = []
            def start(self): pass
            def close(self): self.closed = True
            def poll(self, timeout):
                self.polls += 1
                if self.link is None:
                    self.window.consume(clock())
                    der = b"synthetic console node DER"
                    self.link = Link("relay",None,certificate_pin(der),der,authenticated=True,
                        local_pin=b"L"*32, clock=clock, pairing_window=self.window,persist_peer=saved.append)
                    self.link.feed(hello()+ack(False))
                    self.sent.extend(FrameDecoder().feed(self.link.take_output()))
                elif self.link.state == "pair":
                    self.link.feed(Frame(Type.PAIR_CONFIRM).encode()+Frame(Type.PAIR_COMMIT).encode())
                else:
                    self.sent.extend(FrameDecoder().feed(self.link.take_output()))
                return self.link
        listener = ConsoleListener()
        record = pair_console(self.owner, listener=listener,
            input_fn=lambda: listener.link.pairing_code, output_fn=output.append)
        self.assertEqual(saved, [record])
        self.assertTrue(listener.closed)
        self.assertEqual(listener.polls, 3)
        self.assertIn(Type.PAIR_COMMIT, [frame.type for frame in listener.sent])
        self.assertEqual(sum(len(line.split()) == 10 and not line.startswith("Pairing window") for line in output), 1)

    def test_console_window_timeout_closes_listener_without_pin(self):
        self.owner.write_json("config.json", {"port":17452})
        clock = Clock()
        class Waiting:
            last_error = None
            def __init__(self): self.clock, self.closed = clock, False
            def start(self): pass
            def poll(self, timeout): clock.now += 100
            def close(self): self.closed = True
        listener = Waiting()
        with self.assertRaises(ProtocolError) as result:
            pair_console(self.owner,listener=listener,input_fn=lambda: "",output_fn=lambda value:None)
        self.assertEqual(result.exception.code, Error.TIMEOUT)
        self.assertTrue(listener.closed)


if __name__ == "__main__":
    unittest.main()
