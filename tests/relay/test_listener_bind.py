"""Listener.start waits out "address in use" for a bounded time; every other error fails at once.

Live evidence (Linux headend, 19 November 2026): after `--relay remove` with a Workstation link open, `ss` showed the
old relay's connection in TIME_WAIT on 127.0.0.1:7452. The reinstalled service could not bind, failed three times and
systemd stopped it; with no TIME_WAIT socket the same install left it running. Linux keeps TIME_WAIT for 60 seconds.
"""
import errno
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adapters/relay"))
from deep32_relay import transport
from deep32_relay.platform import ServiceSpec, render
from deep32_relay.state import StateError
from deep32_relay.transport import BIND_RETRY_MAX, BIND_WAIT_SECONDS, Listener, pair_console


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeSocket:
    def __init__(self, outcomes, made):
        self.outcomes, self.options, self.closed = outcomes, [], False
        made.append(self)

    def setsockopt(self, level, option, value):
        self.options.append((level, option, value))

    def bind(self, address):
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            raise outcome
        self.bound = address

    def listen(self, backlog):
        pass

    def setblocking(self, value):
        pass

    def close(self):
        self.closed = True


def in_use():
    return OSError(errno.EADDRINUSE, "synthetic address in use")


class BindRetry(unittest.TestCase):
    def listener(self, outcomes, **kw):
        self.clock, self.slept, self.made = Clock(), [], []
        def sleep(seconds):
            self.slept.append(seconds)
            self.clock.now += seconds
        return Listener(None, None, provider=None, port=7452, clock=self.clock, sleep=sleep,
                        socket_factory=lambda *a: FakeSocket(outcomes, self.made), **kw)

    def test_address_in_use_waits_until_the_port_is_released(self):
        listener = self.listener([in_use(), in_use(), in_use()])
        calls = []
        listener.on_bind_wait = lambda: calls.append(self.clock())
        self.assertIs(listener.start(), listener)
        self.assertEqual(listener.socket.bound, ("127.0.0.1", 7452))
        self.assertEqual(self.slept, [0.25, 0.5, 1.0])
        self.assertEqual(calls, [1000.0])
        self.assertTrue(all(item.closed for item in self.made[:-1]))
        self.assertFalse(self.made[-1].closed)

    def test_address_held_past_the_window_fails(self):
        listener = self.listener([in_use() for _ in range(1000)])
        with self.assertRaises(OSError) as caught:
            listener.start()
        self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
        self.assertAlmostEqual(self.clock.now - 1000.0, BIND_WAIT_SECONDS)
        self.assertGreaterEqual(BIND_WAIT_SECONDS, 60 + 20)
        self.assertLessEqual(max(self.slept), BIND_RETRY_MAX)
        self.assertLess(len(self.made), 60)
        self.assertIsNone(listener.socket)
        self.assertTrue(all(item.closed for item in self.made))

    def test_other_errors_fail_at_once(self):
        for error in (OSError(errno.EACCES, "synthetic"), OSError(errno.EADDRNOTAVAIL, "synthetic"),
                      PermissionError(errno.EPERM, "synthetic"), OSError("synthetic, no errno"),
                      StateError("synthetic-state"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__ + ":" + str(getattr(error, "errno", ""))):
                listener = self.listener([error])
                with self.assertRaises(type(error)):
                    listener.start()
                self.assertEqual(self.slept, [])
                self.assertEqual(len(self.made), 1)
                self.assertTrue(self.made[0].closed)
                self.assertIsNone(listener.socket)

    def test_linux_sets_reuseaddr(self):
        listener = self.listener([], windows=False, linux=True)
        listener.start()
        options = self.made[0].options
        self.assertIn((socket.SOL_SOCKET, socket.SO_REUSEADDR, 1), options)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.assertNotIn((socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1), options)

    def test_macos_sets_no_socket_options(self):
        # BSD SO_REUSEADDR would let the loopback bind succeed beside another program
        # listening on every address of the port; the bounded wait covers TIME_WAIT.
        listener = self.listener([], windows=False, linux=False)
        listener.start()
        self.assertEqual(self.made[0].options, [])

    def test_windows_sets_no_reuseaddr(self):
        listener = self.listener([], windows=True)
        listener.start()
        options = self.made[0].options
        self.assertFalse([item for item in options if item[1] == socket.SO_REUSEADDR])
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.assertEqual(options, [(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)])

    def test_host_default_matches_the_platform(self):
        listener = Listener(None, None, provider=None, port=7452)
        self.assertEqual(listener.windows, os.name == "nt")
        self.assertEqual(listener.linux, sys.platform.startswith("linux"))


class RealLoopbackPort(unittest.TestCase):
    """This host's own socket layer, on an ephemeral loopback port."""
    def hold(self):
        held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(held.close)
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        return held, held.getsockname()[1]

    def test_occupied_port_binds_once_released_within_the_window(self):
        held, port = self.hold()
        released = []
        def sleep(seconds):
            if not released:
                held.close()
                released.append(seconds)
            time.sleep(0.01)
        listener = Listener(None, None, provider=None, port=port, sleep=sleep)
        self.addCleanup(listener.close)
        listener.start()
        self.assertEqual(released, [0.25])
        self.assertEqual(listener.socket.getsockname(), ("127.0.0.1", port))

    def test_stop_ends_a_service_waiting_for_the_port(self):
        # systemd stops a unit with SIGTERM (TimeoutStopSec=5, then SIGKILL); Task Scheduler terminates the process.
        _, port = self.hold()
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from deep32_relay.transport import Listener; "
                "print('waiting', flush=True); Listener(None, None, provider=None, port=int(sys.argv[2])).start(); "
                "print('bound', flush=True)")
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(ROOT / "adapters/relay"), str(port)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.addCleanup(child.stdout.close)
        self.assertEqual(child.stdout.readline().strip(), b"waiting")
        time.sleep(1.5)
        self.assertIsNone(child.poll(), "the service stopped waiting for the occupied port")
        started = time.monotonic()
        child.terminate()
        child.wait(timeout=5)
        self.assertLess(time.monotonic() - started, 5)
        self.assertNotIn(b"bound", child.stdout.read())
        if os.name != "nt":
            self.assertEqual(child.returncode, -signal.SIGTERM)

    def test_linux_unit_lets_a_waiting_service_run_and_stop(self):
        text = render(ServiceSpec("a" * 32, "/synthetic/python", "/synthetic/run.py", "/synthetic/state",
                                  "linux"))[1].decode("utf-8").splitlines()
        # Type=simple is active from exec: the wait is a running service, never a start timeout.
        for line in ("Type=simple", "TimeoutStopSec=5", "KillMode=control-group", "Restart=on-failure",
                     "RestartSec=10"):
            self.assertIn(line, text)
        self.assertFalse([line for line in text if line.split("=")[0] in (
            "TimeoutStartSec", "TimeoutSec", "WatchdogSec", "RuntimeMaxSec")])
        # A bind that still fails after the wait restarts more than 60 seconds later, so the
        # three-in-60-seconds start limit cannot stop the unit.
        self.assertGreater(BIND_WAIT_SECONDS + 10, 60)


class PairingConsoleWait(unittest.TestCase):
    def test_pairing_console_says_it_is_waiting_for_the_port(self):
        outputs = []
        class Recording(Listener):
            def start(self):
                self.on_bind_wait()
                raise StateError("synthetic-stop")
        owner = SimpleNamespace(validate=lambda: "a" * 32, read_json=lambda name: {"port": 7452})
        store = SimpleNamespace(load_or_create=lambda provider: None, load_peer=lambda: None, save_peer=None)
        with patch.object(transport, "Listener", Recording), \
             patch.object(transport, "IdentityStore", lambda owner: store), \
             patch.object(transport, "load_provider", lambda: object()), \
             patch("sys.stdin", SimpleNamespace(isatty=lambda: True)), \
             patch("sys.stdout", SimpleNamespace(isatty=lambda: True)):
            with self.assertRaisesRegex(StateError, "synthetic-stop"):
                pair_console(owner, input_fn=lambda: None, output_fn=outputs.append)
        self.assertEqual(outputs, ["The relay port is still in use; waiting up to %d seconds for it."
                                   % BIND_WAIT_SECONDS])


if __name__ == "__main__":
    unittest.main()
