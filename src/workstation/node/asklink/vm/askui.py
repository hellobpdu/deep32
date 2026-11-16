#!/usr/bin/env python3
"""Test stand-in for the Deep32 Library Ask room (a UI process with no network): it speaks only
d32-chatd's local uplink.sock API.  Prints every event as one timestamped JSON line.

  askui.py SOCK status
  askui.py SOCK turn TEXT            open a session, one turn, close the session
  askui.py SOCK channels             ask for seal, lib and sync on the relay link
  askui.py SOCK hold SECONDS         open a session and print events for SECONDS
  askui.py SOCK interrupt TEXT       open, start a turn, interrupt it at once
"""
import base64
import json
import socket
import sys
import time

T0 = time.time()


def out(tag, obj):
    if isinstance(obj, dict) and isinstance(obj.get("text"), str) and len(obj["text"]) > 2000:
        obj = dict(obj, text="<%d chars, first 40: %s>" % (len(obj["text"]), obj["text"][:40]))
    print("%7.2f %s %s" % (time.time() - T0, tag, json.dumps(obj, sort_keys=True)), flush=True)


class Ui:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.buf = b""

    def send(self, obj):
        out(">>", obj)
        self.s.sendall(json.dumps(obj).encode() + b"\n")

    def recv(self, timeout):
        end = time.time() + timeout
        while b"\n" not in self.buf:
            left = end - time.time()
            if left <= 0:
                return None
            self.s.settimeout(left)
            try:
                b = self.s.recv(65536)
            except socket.timeout:
                return None
            if not b:
                raise SystemExit("chatd closed the socket")
            self.buf += b
        line, self.buf = self.buf.split(b"\n", 1)
        ev = json.loads(line)
        out("<<", ev)
        return ev

    def until(self, pred, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            ev = self.recv(end - time.time())
            if ev is None:
                break
            if pred(ev):
                return ev
        raise SystemExit("timeout waiting for event")


def arg_text(a):
    # "b64:..." lets a harness pass any text through ssh and su -c quoting
    return base64.b64decode(a[4:]).decode() if a.startswith("b64:") else a


def main():
    path, cmd = sys.argv[1], sys.argv[2]
    ui = Ui(path)
    if cmd == "status":
        ui.send({"op": "status"})
        ui.until(lambda e: e["op"] == "status", 5)
    elif cmd in ("turn", "interrupt"):
        ui.send({"op": "session.open"})
        ev = ui.until(lambda e: e["op"] in ("session.opened", "error"), 30)
        if ev["op"] == "error":
            raise SystemExit(1)
        sid = ev["session"]
        ui.send({"op": "turn.start", "session": sid, "text": arg_text(sys.argv[3])})
        if cmd == "interrupt":
            time.sleep(1.5)     # let the relay bind the turn id before interrupting
            ui.send({"op": "turn.interrupt", "session": sid})
        ev = ui.until(lambda e: e["op"] in ("turn.completed", "error") and e.get("session") == sid, 120)
        ui.send({"op": "session.close", "session": sid})
        ui.until(lambda e: e["op"] == "session.closed" and e.get("session") == sid, 30)
        ui.send({"op": "status"})
        ui.until(lambda e: e["op"] == "status", 5)
        if ev["op"] == "error":
            raise SystemExit(1)
    elif cmd == "channels":
        for ch in ("seal", "lib", "sync"):
            ui.send({"op": "channel.open", "channel": ch})
            ui.until(lambda e: e["op"] in ("channel.refused", "error"), 5)
        ui.send({"op": "status"})
        ui.until(lambda e: e["op"] == "status", 5)
    elif cmd == "hold":
        ui.send({"op": "session.open"})
        ui.until(lambda e: e["op"] in ("session.opened", "error"), 30)
        end = time.time() + float(sys.argv[3])
        while time.time() < end:
            ui.recv(end - time.time())
    elif cmd == "open":
        # open a session and keep it while reading stdin-less for SECONDS (fake-relay tests)
        ui.send({"op": "session.open"})
        ui.until(lambda e: e["op"] in ("session.opened", "error"), 30)
        end = time.time() + float(sys.argv[3])
        while time.time() < end:
            ui.recv(end - time.time())
    else:
        raise SystemExit("unknown command")


if __name__ == "__main__":
    main()
