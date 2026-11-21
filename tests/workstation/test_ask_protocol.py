"""Ask's requests to the broker it really talks to: alpha.2's d32-chatd, and a broker with the window password.

alpha.2's broker (src/workstation/node/asklink/src/ui.c and link.c) takes session.open with op, link, model and
effort only, and refuses every other request, ask.* included, with error 1. The relay picks the engine (contract C9).
Owner decision 2026-11-20: against that broker the Ask window opens without a password of its own; a broker with the
window password (lanes/u12-ask/session-followup/node-ui/ui.c) keeps the unlock flow. Each test drives the real
bridge over one end of a socket pair; the other end is a small fake broker that follows those sources' rules.
"""
import base64
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import queue
import socket
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "src/workstation/ask-client/bridge/d32-ask"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


ASK = load("ask_protocol_test", BRIDGE)
QUESTION = "What is the default OSPF hello interval on a broadcast network?"
ANSWER = "Ten seconds on a broadcast network."
PASSWORD = b"FixtureOnly42pass"
# The window-password broker's offer: engine, model, effort, billing, preferred.
CHOICES = [("codex", "model-a", "high", "", True), ("claude", "model-b", "max", "plan", True)]


class FakeBroker(threading.Thread):
    """The broker end of the uplink. Every request is recorded in order, as parsed JSON."""

    def __init__(self, end, dialect, state="ACTIVE", models=("default",), efforts=("default",), ask_link="work"):
        super().__init__(daemon=True)
        self.end, self.dialect = end, dialect
        self.state, self.models, self.efforts = state, list(models), list(efforts)
        self.ask_link = ask_link
        self.requests, self.sessions = [], set()
        self.bound = self.unlocked = False
        self.lock = threading.Lock()

    def send(self, **event):
        with self.lock:
            self.end.sendall((json.dumps(event) + "\n").encode("utf-8"))

    def info(self):
        active = self.state == "ACTIVE"
        return {"link": "work", "state": self.state, "paired": True, "relay": "relay-fixture", "sessions": 0,
                "signed_in": None, "codex_version": None, "models": self.models if active else [],
                "efforts": self.efforts if active else [], "turns": 0, "mcp_calls": 0, "mcp_refused": 0,
                "connects": 1}

    def link_up(self):
        self.state = "ACTIVE"
        self.send(op="link", info=self.info())

    def run(self):
        pending = b""
        try:
            while chunk := self.end.recv(65536):
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    self.handle(json.loads(line))
        except OSError:
            return

    def refuse_open(self, code):
        session = "s%04d" % len(self.requests)
        self.send(op="error", code=code, session=session)
        self.send(op="session.closed", session=session)

    def handle(self, request):
        self.requests.append(request)
        op, keys = request.get("op"), set(request)
        window = self.dialect == "window-auth"
        if op == "status" and keys == {"op"}:
            self.send(op="status", links=[self.info()])
        elif op == "session.open":
            allowed = {"op", "link", "model", "effort"} | ({"engine"} if window else set())
            if (not keys <= allowed or request.get("link", "work") != "work" or
                    any(not isinstance(request.get(key, "x"), str) for key in ("model", "effort"))):
                self.send(op="error", code=1)
            elif window and self.bound and not (self.unlocked and all(request.get(key) for key in
                                                                     ("engine", "model", "effort"))):
                self.send(op="error", code=15)
            elif self.state != "ACTIVE":
                self.refuse_open(11)
            elif not window and (request.get("model", self.models[0]) not in self.models or
                                 request.get("effort", self.efforts[0]) not in self.efforts):
                self.refuse_open(7)
            else:
                session = "s%04d" % len(self.requests)
                self.sessions.add(session)
                self.send(op="session.opened", session=session, model=request.get("model", self.models[0]))
        elif op == "turn.start" and keys == {"op", "session", "text"} and request["session"] in self.sessions:
            self.send(op="agent.delta", session=request["session"], text=ANSWER)
            self.send(op="turn.completed", session=request["session"], status="completed")
        elif op == "session.close" and keys == {"op", "session"} and request["session"] in self.sessions:
            self.sessions.discard(request["session"])
            self.send(op="session.closed", session=request["session"])
        elif op in ("turn.start", "session.close", "turn.interrupt"):
            self.send(op="error", code=7)
        elif window and op and op.startswith("ask."):
            self.window_request(request)
        else:
            self.send(op="error", code=1)

    def window_request(self, request):
        op = request["op"]
        if request.get("link", self.ask_link) != self.ask_link:
            self.send(op="error", code=1)
        elif op == "ask.status":
            self.bound = True
            self.send(op="ask.state", lock="unlocked" if self.unlocked else "locked", codex="ready",
                      claude="ready", reason="none")
        elif not self.bound:
            self.send(op="error", code=15)
        elif op == "ask.unlock":
            if request.get("password_b64") == base64.b64encode(PASSWORD).decode("ascii"):
                self.unlocked = True
                self.send(op="ask.unlocked")
            else:
                self.send(op="ask.state", lock="locked", codex="ready", claude="ready", reason="denied")
        elif op == "ask.capabilities" and self.unlocked:
            self.send(op="ask.capabilities", tuples=[dict(zip(("engine", "model", "effort", "billing", "preferred"),
                                                              row)) for row in CHOICES])
        elif op == "ask.close":
            self.send(op="ask.closed")
        else:
            self.send(op="error", code=15)


class Plug:
    """What Uplink takes for its socket: one end of a socket pair, already connected."""

    def __init__(self, end):
        self.end = end

    def settimeout(self, value):
        self.end.settimeout(value)

    def connect(self, path):
        pass

    def sendall(self, data):
        self.end.sendall(data)

    def recv(self, size):
        return self.end.recv(size)

    def shutdown(self, how):
        self.end.shutdown(how)

    def close(self):
        self.end.close()


class WindowOut:
    """d32-ask's standard output as the native window reads it: one record per line."""

    def __init__(self):
        self.records, self.partial, self.seen = queue.Queue(), "", []

    def write(self, text):
        *lines, self.partial = (self.partial + text).split("\n")
        for line in lines:
            self.records.put(line)

    def flush(self):
        pass

    def expect(self, prefix, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                line = self.records.get(timeout=max(0.01, end - time.monotonic()))
            except queue.Empty:
                break
            self.seen.append(line)
            if line.startswith(prefix):
                return line
        raise AssertionError(f"no {prefix!r} record; the window saw {self.seen}")

    def rest(self):
        while True:
            try:
                self.seen.append(self.records.get_nowait())
            except queue.Empty:
                return self.seen


class NoMaps:
    notice = ""

    def reload(self):
        pass

    def resolve(self, cite):
        return {"cite": cite[0], "entry": None, "fallback": False}


class AskProtocol(unittest.TestCase):
    def connect(self, dialect, **options):
        near, far = socket.socketpair()
        broker = FakeBroker(far, dialect, **options)
        broker.start()
        fake = SimpleNamespace(socket=lambda *args: Plug(near), AF_UNIX=1, SOCK_STREAM=socket.SOCK_STREAM,
                               SHUT_RDWR=socket.SHUT_RDWR)
        with patch.object(ASK, "socket", fake):
            uplink = ASK.Uplink("fixture-uplink.sock")
        self.addCleanup(far.close)
        self.addCleanup(uplink.close)
        return uplink, broker

    @staticmethod
    def args(**values):
        base = dict(link=None, engine=None, model=None, effort=None, window_auth=False)
        return SimpleNamespace(**dict(base, **values))

    def terminal(self, dialect, lines, **values):
        uplink, broker = self.connect(dialect)
        out = io.StringIO()
        history = SimpleNamespace(save=lambda *args: None, close=lambda: None)
        ask = ASK.Ask(uplink, self.args(**values), out=out, history=history, cite_maps=NoMaps())
        ask.run([(line, None) for line in lines])
        return out.getvalue(), broker

    def window(self, dialect, link=None, **options):
        uplink, broker = self.connect(dialect, **options)
        out = WindowOut()
        sink = ASK.EventSink(out, window_auth=True)
        sink.start = lambda: None  # the test is the window: it puts the records d32-ask reads from its stdin
        history = SimpleNamespace(save=lambda *args: None, close=lambda: None)
        ask = ASK.Ask(uplink, self.args(link=link, window_auth=True), out=out, history=history, cite_maps=NoMaps(), sink=sink)
        failure = []

        def run():
            try:
                ask.run()
            except Exception as error:  # reported by the test, not lost in the thread
                failure.append(error)
        runner = threading.Thread(target=run, daemon=True)
        runner.start()

        def stop():
            sink.inbox.put(("Q",))
            runner.join(10)
            self.assertFalse(runner.is_alive(), "d32-ask did not leave on Q")
            self.assertEqual(failure, [])
        self.addCleanup(stop)
        return out, broker, (lambda raw: sink.inbox.put(sink.parse(raw)))

    def opens(self, broker):
        return [request for request in broker.requests if request["op"] == "session.open"]

    def test_terminal_session_open_names_no_engine(self):
        text, broker = self.terminal("shipped", ["/engine codex", QUESTION], link="work", engine="claude")
        self.assertEqual(self.opens(broker), [{"op": "session.open", "link": "work"}])
        self.assertIn(ANSWER, text)
        self.assertNotIn("Error ", text)
        self.assertIn("engine: claude", text)  # --engine labels the header only
        self.assertIn("The relay on the paired PC picks the engine.", text)

    def test_terminal_default_link_and_relay_settings(self):
        text, broker = self.terminal("shipped", [QUESTION], model="default", effort="default")
        self.assertEqual(self.opens(broker), [{"op": "session.open", "link": "work", "model": "default",
                                               "effort": "default"}])
        self.assertIn("Ask Deep32 | work: ACTIVE, paired | engine: relay", text)
        self.assertIn(ANSWER, text)
        self.assertNotIn("Error ", text)

    def test_window_on_alpha2_broker_opens_without_password_and_answers(self):
        out, broker, window = self.window("shipped")
        self.assertEqual(out.expect("K\t"), "K\tunlocked\tunavailable\tunavailable\tnone")
        self.assertEqual(out.expect("M\t"), "M\trelay\tdefault\tdefault\t\t1")
        out.expect("J")
        window(b"D\trelay\tdefault\tdefault\n")  # the window's preferred profile, as show_capabilities sends it
        window(b"L\t" + QUESTION.encode() + b"\n")
        out.expect("Q\t1\t")
        self.assertEqual(out.expect("F\t1\t"), "F\t1\tcompleted")
        seen = out.rest()
        self.assertTrue(any(line.startswith("R\t") and ANSWER in line for line in seen), seen)
        self.assertFalse([line for line in seen if line.startswith("N\terror")], seen)
        self.assertEqual(self.opens(broker), [{"op": "session.open", "link": "work", "model": "default",
                                               "effort": "default"}])
        ops = [request["op"] for request in broker.requests]
        self.assertEqual(ops[:3], ["status", "ask.status", "ask.capabilities"])
        self.assertFalse({"ask.unlock", "ask.enroll", "ask.close"} & set(ops), ops)
        self.assertFalse([request for request in broker.requests if "engine" in request])

    def test_window_on_alpha2_broker_with_link_down_stays_open(self):
        out, broker, window = self.window("shipped", state="CONNECTING")
        self.assertEqual(out.expect("K\t"), "K\tunlocked\tunavailable\tunavailable\tnone")
        self.assertEqual(out.expect("M\t"), "M\trelay\tdefault\tdefault\t\t1")
        out.expect("J")
        window(b"L\t" + QUESTION.encode() + b"\n")
        self.assertTrue(out.expect("N\terror\t").startswith("N\terror\tError 11 RELAY_LOST"))
        broker.link_up()
        out.expect("H\twork\tACTIVE")
        window(b"L\t" + QUESTION.encode() + b"\n")
        self.assertEqual(out.expect("F\t1\t"), "F\t1\tcompleted")
        seen = out.rest()
        self.assertFalse([line for line in seen if line.startswith("K\t") and not line.startswith("K\tunlocked")])
        self.assertEqual(self.opens(broker), [{"op": "session.open", "link": "work"},
                                              {"op": "session.open", "link": "work", "model": "default",
                                               "effort": "default"}])

    def test_window_with_password_broker_keeps_the_unlock_flow(self):
        # A guard: the same flow as before this change (the link is named, as the old bridge needed).
        out, broker, window = self.window("window-auth", link="work")
        self.assertEqual(out.expect("K\t"), "K\tlocked\tready\tready\tnone")
        window(b"L\t" + QUESTION.encode() + b"\n")
        self.assertEqual(out.expect("N\terror\t"),
                         "N\terror\tUnlock Ask and choose an available engine, model and strength.")
        self.assertEqual(self.opens(broker), [])
        window(b"U\tunlock\t" + PASSWORD.hex().encode() + b"\n")
        out.expect("K\tunlocked")
        self.assertEqual(out.expect("M\t"), "M\tcodex\tmodel-a\thigh\t\t1")
        out.expect("J")
        window(b"D\tcodex\tmodel-a\thigh\n")
        window(b"L\t" + QUESTION.encode() + b"\n")
        self.assertEqual(out.expect("F\t1\t"), "F\t1\tcompleted")
        self.assertEqual(self.opens(broker), [{"op": "session.open", "link": "work", "engine": "codex",
                                               "model": "model-a", "effort": "high"}])
        ops = [request["op"] for request in broker.requests]
        self.assertEqual(ops[:6], ["status", "ask.status", "ask.unlock", "ask.status", "ask.capabilities",
                                   "session.open"])

    def test_window_with_password_broker_refusing_the_link_stays_locked(self):
        # Error 1 to ask.status here means a link that broker does not serve, never "no window password".
        out, broker, window = self.window("window-auth", link="work", ask_link="other")
        self.assertTrue(out.expect("N\terror\t").startswith("N\terror\tError 1 PROTOCOL"))
        window(b"L\t" + QUESTION.encode() + b"\n")
        self.assertEqual(out.expect("N\terror\t"),
                         "N\terror\tUnlock Ask and choose an available engine, model and strength.")
        seen = out.rest()
        self.assertFalse([line for line in seen if line.startswith(("K\t", "M\t", "Q\t"))], seen)
        self.assertEqual(self.opens(broker), [])
        self.assertFalse({"ask.unlock", "ask.enroll"} & {request["op"] for request in broker.requests})


if __name__ == "__main__":
    unittest.main()
