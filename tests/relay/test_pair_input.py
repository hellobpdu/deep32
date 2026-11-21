"""Echoed, word-by-word entry of the ten pairing words at the relay console.

Offline and synthetic: a real Link and Ceremony with a scripted Workstation,
no socket or TLS, and fake termios/msvcrt modules for the two console paths.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adapters/relay"))
from deep32_relay import messages, transport
from deep32_relay.link import Link
from deep32_relay.pairing import Ceremony, _words, verify_typed
from deep32_relay.state import OwnedRoot, OWNER_SCHEMA, StateError
from deep32_relay.tls import certificate_pin
from deep32_relay.transport import _ConsoleInput, _WordEntry, pair_console
from deep32_relay.wire import Error, Frame, FrameDecoder, ProtocolError, Type

CODE = "ABANDON ABILITY ABLE ABOUT ABOVE ABSENT ABSORB ABSTRACT ABSURD ABUSE"
LISTED = frozenset(word.upper() for word in _words())


def other_word(word):
    """A different list word: what an intercepted link's Workstation would show."""
    return "ZOO" if word != "ZOO" else "ABANDON"


def typo(word):
    """Longer than any list word, so never a word a Workstation can show."""
    return word.lower() + "xq"


def run_entry(entry, text):
    results = [result for result in (entry.feed(char) for char in text) if result is not None]
    assert len(results) <= 1, results
    return results[0] if results else None


class WordEntryTests(unittest.TestCase):
    def entry(self, code=CODE):
        shown = []
        return _WordEntry(code, shown.append), shown

    def test_each_word_is_echoed_checked_and_counted(self):
        entry, shown = self.entry()
        line = run_entry(entry, CODE.lower() + "\r")
        text = "".join(shown)
        self.assertEqual(line, CODE)
        self.assertTrue(verify_typed(line, CODE))
        for number in range(1, 11):
            self.assertIn("word %d of 10: " % number, text)
        self.assertEqual(text.count("  ok\n"), 10)
        self.assertIn("word 1 of 10: abandon  ok\nword 2 of 10: ability  ok\n", text)
        self.assertTrue(text.endswith("All ten words match this PC's words; waiting for the Workstation to confirm.\n"))
        self.assertTrue(entry.finished)

    def test_typo_is_asked_for_again_without_restarting(self):
        entry, shown = self.entry()
        self.assertIsNone(run_entry(entry, "abandon " + typo("ability") + " "))
        text = "".join(shown)
        self.assertIn("  that is not word 2 - type it again\nword 2 of 10: ", text)
        self.assertEqual(entry.words, ["ABANDON"])
        self.assertFalse(entry.finished)
        line = run_entry(entry, " ".join(CODE.split()[1:]) + "\n")
        self.assertEqual(line, CODE)
        self.assertEqual("".join(shown).count("word 1 of 10: "), 1)

    def test_numbers_are_refused_with_a_hint(self):
        entry, shown = self.entry()
        self.assertIsNone(run_entry(entry, "01 "))
        self.assertIn("that is not word 1 - type it again (type the words only, not their numbers)", "".join(shown))
        self.assertEqual(entry.words, [])

    def test_case_and_extra_spaces_are_tolerated(self):
        entry, shown = self.entry()
        line = run_entry(entry, "  AbAnDoN \t\tability\r\nABLE   about\nabove absent absorb abstract absurd ABUSE ")
        self.assertEqual(line, CODE)
        self.assertNotIn("that is not", "".join(shown))

    def test_backspace_clear_and_cursor_keys(self):
        entry, shown = self.entry()
        line = run_entry(entry, "abx\x7fandon abil\x1b[Dity \x1bOAable wrong\x15about wrong\x17above"
                                " absent absorb abstract absurd abz\buse\r")
        text = "".join(shown)
        self.assertEqual(line, CODE)
        self.assertIn("\b \b", text)
        self.assertNotIn("[D", text)
        self.assertNotIn("OA", text)
        self.assertNotIn("that is not", text)

    def test_overlong_word_is_capped_and_asked_for_again(self):
        entry, shown = self.entry()
        self.assertIsNone(run_entry(entry, "a" * 40 + " "))
        self.assertIn("a" * 24 + "  that is not word 1", "".join(shown))
        self.assertNotIn("a" * 25, "".join(shown))

    def test_another_list_word_ends_entry_as_the_one_failing_attempt(self):
        entry, shown = self.entry()
        line = run_entry(entry, "abandon ability zoo ")
        self.assertEqual(line, "ABANDON ABILITY ZOO")
        self.assertIn("ZOO", LISTED)
        self.assertFalse(verify_typed(line, CODE))
        self.assertTrue(entry.finished)
        text = "".join(shown)
        self.assertIn("that is not word 3, and it is another pairing word", text)
        self.assertIn("nothing is stored", text)
        before = len(shown)
        self.assertIsNone(run_entry(entry, "able about\r"))
        self.assertEqual(len(shown), before)

    def test_malformed_code_is_refused(self):
        with self.assertRaises(ProtocolError):
            _WordEntry("ABANDON ABILITY", [].append)


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class FakeTTY:
    def __init__(self):
        self.shown = []

    def isatty(self):
        return True

    def fileno(self):
        return 97

    def write(self, text):
        self.shown.append(text)

    def flush(self):
        pass

    @property
    def text(self):
        return "".join(self.shown)


def hello():
    value = messages.hello("node", b"N" * 32)
    value["role"] = "node"
    return Frame(Type.HELLO, payload=messages.dumps(value)).encode()


def ack():
    return Frame(Type.HELLO_ACK, payload=messages.dumps(dict(version=1, features=["research-v1"], paired=False))).encode()


class ScriptedListener:
    """One synthetic Workstation: confirms and commits once this console has confirmed."""
    last_error = connection = None

    def __init__(self, clock, saved, keyboard, step=0.0):
        self.clock, self.saved, self.keyboard, self.step = clock, saved, keyboard, step
        self.link, self.closed, self.sent = None, False, []

    def start(self):
        pass

    def close(self):
        self.closed = True

    def poll(self, timeout):
        self.clock.now += self.step
        if self.link is None:
            self.window.consume(self.clock())
            der = b"synthetic console node DER"
            self.link = Link("relay", None, certificate_pin(der), der, authenticated=True,
                             local_pin=b"L" * 32, clock=self.clock, pairing_window=self.window,
                             persist_peer=self.saved.append)
            self.link.feed(hello() + ack())
        elif self.link.state == "pair" and self.link.ceremony.local_confirm:
            self.link.feed(Frame(Type.PAIR_CONFIRM).encode() + Frame(Type.PAIR_COMMIT).encode())
        self.sent.extend(FrameDecoder().feed(self.link.take_output()))
        if self.link.state == "pair":
            self.keyboard.release(self.link.pairing_code)
        return self.link


class Keyboard:
    """Keys typed between polls; script(code) returns one batch per poll."""
    def __init__(self, script, early=""):
        self.script, self.batches, self.pending = script, None, list(early)

    def release(self, code):
        if self.batches is None:
            self.batches = [list(batch) for batch in self.script(code)]
        elif self.batches:
            self.pending.extend(self.batches.pop(0))


try:
    import msvcrt as REAL_MSVCRT
except ImportError:
    REAL_MSVCRT = None


class FakeMsvcrt:
    """Console keys come from the script; the relay's file calls reach the real module."""
    def __init__(self, keyboard):
        self.keyboard = keyboard

    def __getattr__(self, name):
        if REAL_MSVCRT is None:
            raise AttributeError(name)
        return getattr(REAL_MSVCRT, name)

    def kbhit(self):
        return bool(self.keyboard.pending)

    def getwch(self):
        return self.keyboard.pending.pop(0)


class FakeTermios:
    ECHO, ICANON, ISIG = 0o10, 0o2, 0o1
    VMIN, VTIME = 6, 5
    TCSANOW, TCIFLUSH = 0, 0

    def __init__(self):
        self.original = [0o2400, 0o5, 0o277, self.ECHO | self.ICANON | self.ISIG | 0o40000, 0o17, 0o17,
                         [b"\x03"] * 32]
        self.mode = self.copy(self.original)
        self.sets, self.flushes = [], 0

    @staticmethod
    def copy(value):
        return list(value[:6]) + [list(value[6])]

    def tcgetattr(self, fd):
        assert fd == 97
        return self.copy(self.mode)

    def tcsetattr(self, fd, when, attributes):
        assert fd == 97 and when == self.TCSANOW
        self.sets.append(self.copy(attributes))
        self.mode = self.copy(attributes)

    def tcflush(self, fd, queue):
        assert fd == 97 and queue == self.TCIFLUSH
        self.flushes += 1


class PairConsoleTests(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ["DEEP32_RELAY_TEST_ROOT"]).resolve()
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-pair-input-", dir=base)
        self.addCleanup(self.temp.cleanup)

        class Security:
            def mkdir(self, path): os.mkdir(path, 0o700)
            def protect_file(self, path): pass
            def verify(self, path, directory=False): pass
        self.owner = OwnedRoot(Path(self.temp.name) / "owned", Security())
        self.owner.create()
        self.owner.write_json("owner.json", dict(schema=OWNER_SCHEMA, install_id="1" * 32))
        self.owner.write_json("config.json", {"port": 17452})
        self.clock, self.saved, self.tty = Clock(), [], FakeTTY()

    def windows(self, script, *, early="", step=0.0):
        keyboard = Keyboard(script, early)
        listener = ScriptedListener(self.clock, self.saved, keyboard, step)
        console = lambda: _ConsoleInput(windows=True, stdin=self.tty, stdout=self.tty)
        patches = (mock.patch.dict(sys.modules, {"msvcrt": FakeMsvcrt(keyboard)}),
                   mock.patch.object(transport, "_ConsoleInput", console))
        return listener, patches

    def run_pair(self, listener, patches):
        with patches[0], patches[1]:
            return pair_console(self.owner, listener=listener, output_fn=self.tty.shown.append)

    def assert_failed_closed(self, listener):
        self.assertEqual(self.saved, [])
        self.assertTrue(listener.closed)
        self.assertNotIn(Type.PAIR_CONFIRM, [frame.type for frame in listener.sent])
        self.assertNotIn(Type.PAIR_COMMIT, [frame.type for frame in listener.sent])

    def test_windows_console_pairs_with_echo_typos_and_special_keys(self):
        def script(code):
            words = code.split()
            # Arrow key (prefix + code), Esc and backspace are handled; a typo is asked again.
            return [words[0].lower()[:3], "\xe0K" + words[0].lower()[3:] + " ",
                    typo(words[1]) + " ", "\x1b" + words[1] + "x\b ",
                    " ".join(words[2:]) + "\r"]
        listener, patches = self.windows(script, early="early keys ")
        with mock.patch.object(Ceremony, "confirm", autospec=True, side_effect=Ceremony.confirm) as confirm:
            record = self.run_pair(listener, patches)
        self.assertEqual(self.saved, [record])
        self.assertEqual(confirm.call_count, 1)
        self.assertIn(Type.PAIR_CONFIRM, [frame.type for frame in listener.sent])
        text = self.tty.text
        self.assertIn("that is not word 2 - type it again", text)
        self.assertEqual(text.count("  ok\n"), 10)
        self.assertNotIn("early", text)
        self.assertTrue(text.endswith("Both consoles confirmed; the Workstation pin is stored."))
        self.assertTrue(listener.closed)

    def test_windows_ctrl_c_stops_without_pin(self):
        listener, patches = self.windows(lambda code: ["abc", "\x03"])
        with self.assertRaises(KeyboardInterrupt):
            self.run_pair(listener, patches)
        self.assert_failed_closed(listener)
        self.assertTrue(self.tty.text.endswith("abc\n"))

    def test_wrong_but_complete_sequence_at_the_console_fails_closed(self):
        def script(code):
            words = code.split()
            return [" ".join(words[:9]) + " ", other_word(words[9]).lower() + "\r"]
        listener, patches = self.windows(script)
        with self.assertRaises(ProtocolError) as result:
            self.run_pair(listener, patches)
        self.assertEqual(result.exception.code, Error.AUTH)
        self.assert_failed_closed(listener)
        self.assertEqual(listener.link.state, "closed")
        self.assertIn("that is not word 10, and it is another pairing word", self.tty.text)

    def test_wrong_but_complete_sequence_by_line_fails_closed(self):
        listener = ScriptedListener(self.clock, self.saved, Keyboard(lambda code: []))
        def typed():
            words = listener.link.pairing_code.split()
            return " ".join(words[:9] + [other_word(words[9])])
        with self.assertRaises(ProtocolError) as result:
            pair_console(self.owner, listener=listener, input_fn=typed, output_fn=self.tty.shown.append)
        self.assertEqual(result.exception.code, Error.AUTH)
        self.assert_failed_closed(listener)

    def test_deadline_expiry_mid_entry_stores_nothing_and_ends_the_line(self):
        listener, patches = self.windows(lambda code: ["ab", "an"], step=60.0)
        with self.assertRaises(ProtocolError) as result:
            self.run_pair(listener, patches)
        self.assertEqual(result.exception.code, Error.TIMEOUT)
        self.assert_failed_closed(listener)
        self.assertGreaterEqual(self.clock.now, 400)
        self.assertTrue(self.tty.text.endswith("word 1 of 10: aban\n"))

    def posix(self, script, reads=None, step=0.0):
        keyboard = Keyboard(script)
        listener = ScriptedListener(self.clock, self.saved, keyboard, step)
        termios = FakeTermios()

        def ready(read, write, error, timeout):
            self.assertEqual((read, timeout), ([97], 0))
            return (read if keyboard.pending else []), [], []

        def read(fd, size):
            self.assertEqual(fd, 97)
            if reads is not None:
                reads(keyboard)  # a signal or a closed terminal during the read
            data, keyboard.pending[:] = "".join(keyboard.pending[:size]), keyboard.pending[size:]
            return data.encode("utf-8")
        console = lambda: _ConsoleInput(windows=False, stdin=self.tty, stdout=self.tty)
        patches = (mock.patch.dict(sys.modules, {"termios": termios}),
                   mock.patch.object(transport, "_ConsoleInput", console))
        io = (mock.patch.object(transport.select, "select", ready),
              mock.patch.object(transport.os, "read", read))
        return listener, patches, io, termios

    def run_posix(self, listener, patches, io):
        with io[0], io[1]:
            return self.run_pair(listener, patches)

    def test_posix_console_pairs_and_restores_the_terminal(self):
        listener, patches, io, termios = self.posix(lambda code: [code[:5].lower(), code[5:] + "\n"])
        record = self.run_posix(listener, patches, io)
        self.assertEqual(self.saved, [record])
        self.assertEqual(termios.flushes, 1)
        changed, restored = termios.sets[0], termios.sets[-1]
        self.assertEqual(changed[3] & (FakeTermios.ECHO | FakeTermios.ICANON), 0)
        self.assertTrue(changed[3] & FakeTermios.ISIG)
        self.assertEqual((changed[6][FakeTermios.VMIN], changed[6][FakeTermios.VTIME]), (1, 0))
        self.assertEqual(restored, termios.original)
        self.assertEqual(termios.mode, termios.original)
        self.assertEqual(self.tty.text.count("  ok\n"), 10)

    def test_posix_ctrl_c_signal_restores_the_terminal(self):
        def interrupt(keyboard):
            if keyboard.pending == ["!"]:
                raise KeyboardInterrupt()
        listener, patches, io, termios = self.posix(lambda code: ["aba", "!"], reads=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.run_posix(listener, patches, io)
        self.assert_failed_closed(listener)
        self.assertEqual(termios.mode, termios.original)
        self.assertTrue(self.tty.text.endswith("aba\n"))

    def test_posix_ctrl_c_key_and_end_of_input_restore_the_terminal(self):
        for key, error in (("\x03", KeyboardInterrupt), ("\x04", ProtocolError)):
            with self.subTest(key=key):
                self.saved.clear()
                listener, patches, io, termios = self.posix(lambda code: ["ab", key])
                with self.assertRaises(error):
                    self.run_posix(listener, patches, io)
                self.assert_failed_closed(listener)
                self.assertEqual(termios.mode, termios.original)

    def test_posix_closed_input_fails_closed_and_restores(self):
        def closed(keyboard):
            if keyboard.pending == ["!"]:
                keyboard.pending.clear()
        listener, patches, io, termios = self.posix(lambda code: ["ab", "!"], reads=closed)
        with self.assertRaises(ProtocolError) as result:
            self.run_posix(listener, patches, io)
        self.assertEqual(result.exception.code, Error.AUTH)
        self.assert_failed_closed(listener)
        self.assertEqual(termios.mode, termios.original)

    def test_restore_happens_even_if_closing_the_listener_fails(self):
        listener, patches, io, termios = self.posix(lambda code: ["\x03"])
        def broken_close():
            listener.closed = True
            raise OSError("synthetic close failure")
        listener.close = broken_close
        with self.assertRaises(OSError):
            self.run_posix(listener, patches, io)
        self.assertEqual(termios.mode, termios.original)

    def test_console_is_required(self):
        class Pipe(FakeTTY):
            def isatty(self):
                return False
        with self.assertRaises(StateError):
            _ConsoleInput(windows=True, stdin=Pipe(), stdout=FakeTTY()).__enter__()


if __name__ == "__main__":
    unittest.main()
