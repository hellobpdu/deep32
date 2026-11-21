import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "adapters" / "relay"))
from deep32_relay.pairing import (Ceremony, PairingWindow, PinRecord, sas_code,
                                  sas_digest, sas_indices, verify_typed, _words)
from deep32_relay.wire import Error, Frame, ProtocolError, Type


class SASTests(unittest.TestCase):
    def setUp(self):
        self.a = bytes(range(32))
        self.b = bytes(range(32, 64))
        self.n = bytes(range(64, 96))
        self.r = bytes(range(96, 128))

    def test_wordlist_pin(self):
        self.assertEqual(len(_words()), 2048)
        self.assertEqual(_words()[0], "abandon")
        self.assertEqual(_words()[-1], "zoo")

    def test_pin_sort_and_nonce_direction(self):
        expected = hashlib.sha256(self.a + self.b + self.n + self.r).digest()
        self.assertEqual(sas_digest(self.a, self.b, self.n, self.r), expected)
        self.assertEqual(sas_digest(self.b, self.a, self.n, self.r), expected)
        self.assertNotEqual(sas_digest(self.a, self.b, self.r, self.n), expected)
        with self.assertRaises(ProtocolError):
            sas_digest(self.a, self.a, self.n, self.r)

    def test_msb_indices(self):
        self.assertEqual(sas_indices(bytes(32)), (0,) * 10)
        self.assertEqual(sas_indices(b"\xff" * 32), (2047,) * 10)
        self.assertEqual(sas_indices(b"\x80" + bytes(31)), (1024,) + (0,) * 9)

    def test_typed_normalisation(self):
        code = sas_code(self.a, self.b, self.n, self.r)
        self.assertEqual(len(code.split()), 10)
        self.assertTrue(verify_typed(" \t" + "\v\f\r\n".join(code.lower().split()) + " ", code))
        for typed in ("01 " + code, code + " extra", code.replace(" ", "\u00a0"), "", "A" * 257):
            self.assertFalse(verify_typed(typed, code))

    def test_pin_record_exact_format(self):
        record = PinRecord("node-1", self.a)
        self.assertEqual(PinRecord.decode(record.encode()), record)
        for bad in (record.encode() + b"\n", record.encode().replace(b"node-1", b"../x"),
                    record.encode().replace(b"0a", b"0A"), b"gateway-token synthetic"):
            with self.assertRaises(ProtocolError):
                PinRecord.decode(bad)

    def ceremony(self, persist):
        return Ceremony(self.a, PinRecord("node", self.b), self.n, self.r, deadline=300, persist=persist)

    def test_persist_only_after_both_confirms_then_commit(self):
        saved = []
        ceremony = self.ceremony(saved.append)
        self.assertEqual([f.type for f in ceremony.confirm(ceremony.code, 1)], [Type.PAIR_CONFIRM])
        self.assertEqual(saved, [])
        output = ceremony.receive(Frame(Type.PAIR_CONFIRM), 2)
        self.assertEqual([f.type for f in output], [Type.PAIR_COMMIT])
        self.assertEqual(saved, [ceremony.peer])
        self.assertFalse(ceremony.active)
        ceremony.receive(Frame(Type.PAIR_COMMIT), 3)
        self.assertTrue(ceremony.active)

    def test_remote_confirm_first(self):
        saved = []
        ceremony = self.ceremony(saved.append)
        self.assertEqual(ceremony.receive(Frame(Type.PAIR_CONFIRM), 1), [])
        self.assertEqual([f.type for f in ceremony.confirm(ceremony.code, 2)], [Type.PAIR_CONFIRM, Type.PAIR_COMMIT])
        self.assertEqual(len(saved), 1)

    def test_commit_loss_preserves_pin(self):
        saved = []
        ceremony = self.ceremony(saved.append)
        ceremony.confirm(ceremony.code, 1)
        ceremony.receive(Frame(Type.PAIR_CONFIRM), 2)
        # Simulated transport loss has no rollback/delete hook.
        del ceremony
        self.assertEqual(len(saved), 1)

    def test_duplicate_early_and_wrong_frames(self):
        for frame in (Frame(Type.PAIR_COMMIT), Frame(Type.PING, payload=b"12345678"), Frame(Type.PAIR_CONFIRM, payload=b"x")):
            with self.assertRaises(ProtocolError):
                self.ceremony(lambda record: None).receive(frame, 1)
        ceremony = self.ceremony(lambda record: None)
        ceremony.receive(Frame(Type.PAIR_CONFIRM), 1)
        with self.assertRaises(ProtocolError):
            ceremony.receive(Frame(Type.PAIR_CONFIRM), 2)

    def test_wrong_words_expiry_and_failed_save(self):
        saved = []
        ceremony = self.ceremony(saved.append)
        with self.assertRaises(ProtocolError):
            ceremony.confirm("wrong", 1)
        self.assertEqual(saved, [])
        with self.assertRaises(ProtocolError):
            self.ceremony(saved.append).confirm(ceremony.code, 300)
        def fail(record):
            raise OSError("synthetic private failure")
        ceremony = self.ceremony(fail)
        ceremony.confirm(ceremony.code, 1)
        with self.assertRaises(ProtocolError) as cm:
            ceremony.receive(Frame(Type.PAIR_CONFIRM), 2)
        self.assertEqual(str(cm.exception), "AUTH")
        self.assertFalse(ceremony.local_commit)

    def test_window_one_attempt(self):
        window = PairingWindow(1)
        window.consume(2)
        with self.assertRaises(ProtocolError):
            window.consume(3)
        with self.assertRaises(ProtocolError):
            PairingWindow(1).consume(301)


if __name__ == "__main__":
    unittest.main()
