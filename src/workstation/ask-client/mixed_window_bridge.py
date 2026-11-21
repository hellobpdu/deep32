"""Mixed-version checks using the exact U12 bridge and inherited U4 broker.

Place beside the assembled native GUI tree. Run as root only in the isolated OpenBSD builder scratch to start the
predecessor broker with temporary sockets.

u13_gui_on_u12_bridge_denied: the window with the older bridge, which has no --window-auth, keeps its disabled
window and never reaches the broker.

u13_bridge_on_u12_broker_opens: owner decision of 2026-11-20. Against the broker without the window password (the
one alpha.2 ships), this bridge opens the window without a password of its own: the Workstation login and the broker
socket's mode (0660 root:_d32users) protect it, as they protect Ask in a terminal. The relay picks the engine, and a
question reaches the broker as a session.open without an engine field. Before that decision this check required the
window to stay locked here. A broker with the window password keeps the unlock flow.
"""

import grp
import hashlib
import os
from pathlib import Path
import pwd
import queue
import shlex
import socket
import subprocess as sp
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parent
U12_BRIDGE = "5869ef841bb34e9efca6d495eaa21cfc6811245667f134de501a8362aba150ae"
U4_BROKER = "cb44c72ee3688bb94cb7ccffe1fae6e72659e8dd1492c8f5b8d8195c1aa04c62"
U5_MODS = "8a35b11a0ed91562924e59d332af2f19b376fdb643858e053eb416a56e2aec1e"


def require(condition, description):
    if not condition:
        raise AssertionError(description)


def wait_until(probe, description):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if probe():
            return
        time.sleep(.08)
    raise AssertionError(description)


def digest(path, expected):
    require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, f"incorrect predecessor {path.name}")


def stop(process):
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=8)


class Records:
    """The bridge's window records, read on a thread so that every wait has a deadline."""

    def __init__(self, stream):
        self.lines, self.seen = queue.Queue(), []
        threading.Thread(target=self.read, args=(stream,), daemon=True).start()

    def read(self, stream):
        for line in stream:
            self.lines.put(line.rstrip("\n"))

    def expect(self, prefix, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=max(.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            self.seen.append(line)
            if line.startswith(prefix):
                return line
        raise AssertionError(f"no {prefix!r} record from the bridge: {self.seen}")


def gui_on_old_bridge(temp):
    path = temp / "guard.sock"
    shell = temp / "old-bridge-runner"
    target = ROOT / "predecessor/bridge/d32-ask"
    shell.write_text("#!/bin/sh\nexec " + " ".join(map(shlex.quote, (
        sys.executable, "-I", "-B", str(target), "--socket", str(path)))) + ' "$@"\n', encoding="ascii")
    shell.chmod(0o700)
    out = temp / "mix-build"
    sp.run(["/usr/bin/make", "-C", str(ROOT / "window"), "D32_SRC=../d32src", f"BUILD={out}",
            f'CPPFLAGS=-Icore -DD32_ASK_CHILD=\\"{shell}\\"'], check=True, timeout=40)
    read, write = os.pipe()
    display = sp.Popen(["/usr/X11R6/bin/Xvfb", "-displayfd", str(write), "-screen", "0", "960x640x24", "-nolisten",
                        "tcp", "-noreset"], pass_fds=(write,), stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    os.close(write)
    try:
        with os.fdopen(read) as port:
            number = port.readline().strip()
        require(number.isdecimal(), "Xvfb did not report its display")
        env = dict(os.environ, DISPLAY=":" + number, HOME=str(temp), LC_ALL="en_US.UTF-8",
                   XFILESEARCHPATH=str(ROOT / "window/app-defaults/%N"))
        log = temp / "old-bridge.log"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as guard, log.open("wb") as output:
            guard.bind(str(path))
            guard.listen(1)
            guard.settimeout(.5)
            gui = sp.Popen([str(out / "d32-askw")], env=env, stdout=output, stderr=output)
            try:
                wait_until(lambda: "unrecognized arguments: --window-auth" in log.read_text(),
                           "older bridge did not refuse window-auth")
                require(gui.poll() is None, "GUI did not retain its disabled window")
                try:
                    connection, _ = guard.accept()
                except socket.timeout:
                    pass
                else:
                    connection.close()
                    raise AssertionError("older bridge reached broker after rejecting window-auth")
            finally:
                stop(gui)
    finally:
        stop(display)
    print("PASS u13_gui_on_u12_bridge_denied", flush=True)


def bridge_on_old_broker(temp, chatctl):
    state = temp / "identity"
    state.mkdir(mode=0o700)
    ui = temp / "uplink.sock"
    config = temp / "chatd.conf"
    group = grp.getgrgid(pwd.getpwnam("d32build").pw_gid).gr_name
    config.write_text(f"node-id fixture-node\nstate-dir {state}\ncontrol-socket {temp / 'control.sock'}\n"
                      f"ui-socket {ui}\nui-group {group}\nui-user _d32chat\n"
                      "edge 127.0.0.1 1 /mcp\nlink home relay 127.0.0.1 1 user nobody class research\n",
                      encoding="ascii")
    sp.run([str(chatctl), "-d", str(state), "keygen"], check=True, stdout=sp.DEVNULL, timeout=10)
    target = ROOT / "predecessor/d32-chatd"
    with (temp / "old-broker.log").open("wb") as output:
        daemon = sp.Popen([str(target), "-d", "-f", str(config)], stdout=output, stderr=output)
        try:
            wait_until(ui.exists, "older broker did not open its fixture socket")
            bridge = sp.Popen([sys.executable, "-I", "-B", str(ROOT / "bridge/d32-ask"), "--socket", str(ui),
                               "--events", "--window-auth"], stdin=sp.PIPE, stdout=sp.PIPE, text=True)
            try:
                records = Records(bridge.stdout)
                require(records.expect("K\t") == "K\tunlocked\tunavailable\tunavailable\tnone",
                        "the window did not open without a password")
                require(records.expect("M\t") == "M\trelay\tdefault\tdefault\t\t1",
                        "the relay's default choice is missing")
                records.expect("J")
                # As the window does: its preferred profile, then a question.
                bridge.stdin.write("D\trelay\tdefault\tdefault\nL\tSynthetic question\n")
                bridge.stdin.flush()
                # The fixture relay at 127.0.0.1 port 1 never answers. The broker accepted the session.open, which has
                # no engine field, and its link refused it: RELAY_LOST (11). PROTOCOL (1) would mean a refused request.
                notice = records.expect("N\terror\t")
                require(notice.startswith("N\terror\tError 11 RELAY_LOST"), notice)
                bridge.stdin.write("L\t/quit\n")
                bridge.stdin.flush()
                require(bridge.wait(timeout=12) == 0, "the bridge did not leave cleanly")
                require(not any(line.startswith(("K\tlocked", "Q\t")) for line in records.seen), records.seen)
            finally:
                stop(bridge)
        finally:
            stop(daemon)
    print("PASS u13_bridge_on_u12_broker_opens", flush=True)


def main():
    require(os.geteuid() == 0 and len(sys.argv) == 2, "isolated builder root and synthetic key generator required")
    digest(ROOT / "predecessor/bridge/d32-ask", U12_BRIDGE)
    digest(ROOT / "predecessor/bridge/d32_ask_mods.py", U5_MODS)
    digest(ROOT / "predecessor/d32-chatd", U4_BROKER)
    # The system temporary folder (/tmp on OpenBSD) keeps the socket paths under sun_path's 104 bytes.
    with tempfile.TemporaryDirectory(prefix="ask-mixed-") as directory:
        temp = Path(directory)
        gui_on_old_bridge(temp)
        bridge_on_old_broker(temp, Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
