"""Loopback-only TLS listener and bounded D32U pump; no route operations."""
import codecs
import errno
import os
import hmac
import secrets
import select
import socket
import sys
import time
from .identity import IdentityStore
from .link import Link
from .pairing import PairingWindow, PinRecord, _words
from .state import StateError
from .tls import PeerPolicy, certificate_pin, load_provider
from .wire import Error, Frame, ProtocolError, Type, u32


class ReconnectBackoff:
    """Source-derived Workstation schedule, for adapters that own a dialler.

    The relay itself listens; it neither dials the Workstation nor replays work.
    Only an already stored pin authorises automatic reconnect.
    """
    def __init__(self, *, randbelow=secrets.randbelow):
        self.attempt = 0
        self.randbelow = randbelow

    def delay(self, peer):
        if not isinstance(peer, PinRecord):
            raise ProtocolError(Error.AUTH)
        seconds = (1, 2, 4, 8, 16, 30)[min(self.attempt, 5)]
        self.attempt += 1
        return seconds * (800 + self.randbelow(401)) / 1000

    def active(self):
        self.attempt = 0


class TransportConnection:
    def __init__(self, raw_socket, identity, policy, provider, *, clock=time.monotonic,
                 profiles=(), persist_peer=None, on_link=None, wait=select.select):
        self.raw, self.identity, self.policy, self.provider = raw_socket, identity, policy, provider
        self.clock, self.profiles = clock, profiles
        self.persist_peer, self.on_link = persist_peer, on_link
        self.wait = wait
        self.link = None
        self.closed, self.last_error = False, None
        self.deadline = clock() + 10
        self.tx = b""
        self.tls_retry = None
        self.handshake_wants_write = False
        try:
            raw_socket.setblocking(False)
            self.policy.begin_attempt()
            self.tls = provider.connection(provider.context(identity, policy), raw_socket)
        except BaseException:
            raw_socket.close()
            self.closed = True
            raise

    def _handshake(self):
        try:
            self.tls.do_handshake()
        except self.provider.wants() as error:
            self.handshake_wants_write = isinstance(error, self.provider.SSL.WantWriteError)
            return False
        der = self.provider.peer_der(self.tls)
        if not self.policy.accept_der(der):
            raise ProtocolError(Error.AUTH)
        peer = self.policy.peer
        window = self.policy.window
        if peer is not None and window is not None and window.consumed:
            window = None
        self.link = Link(self.identity.local_id, peer.peer_id if peer else None,
                         certificate_pin(der), der, authenticated=True,
                         profiles=self.profiles, clock=self.clock, local_pin=self.identity.pin,
                         pairing_window=window, persist_peer=self.persist_peer)
        # Install the supervisor before any HELLO/session bytes are processed.
        if self.on_link is not None:
            self.on_link(self.link)
        return True

    def pump(self, timeout=0.1):
        if not 0 <= timeout <= 1:
            raise ValueError("invalid-poll-timeout")
        if self.closed:
            return None
        try:
            if self.link is None:
                if self.clock() >= self.deadline:
                    raise ProtocolError(Error.TIMEOUT)
                if not self._handshake():
                    self.wait([] if self.handshake_wants_write else [self.raw],
                              [self.raw] if self.handshake_wants_write else [], [], timeout)
                    return None
            self.link.tick()
            if self.link.state == "closed":
                self.close(Error.TIMEOUT)
                return self.link
            self._check_tls_retry()
            with self.link.condition:
                wants_write = bool(self.tx or self.link.outgoing)
            readable, writable, _ = self.wait([self.raw], [self.raw] if wants_write else [], [], timeout)
            if writable:
                # Keep no unstarted data outside Link: a capability can be
                # revoked while select waits for this socket to become writable.
                for _ in range(224):
                    with self.link.condition:
                        if not self.tx and not self.link.outgoing:
                            break
                        before = self.link.output_count
                        try:
                            self.tx = self.link.transmit(self._send, self.tx)
                        except self.provider.wants():
                            break
                        if not self.tx and before == self.link.output_count:
                            break
                self._check_tls_retry()
            # OpenSSL may hold plaintext after the socket stops being readable.
            pending = self.tls.pending() if hasattr(self.tls, "pending") else 0
            if readable or pending:
                for _ in range(16):
                    try:
                        data = self.tls.recv(65536)
                    except self.provider.wants():
                        break
                    if not data:
                        raise ProtocolError(Error.RELAY_LOST)
                    self.link.feed(data)
                    if self.link.state == "closed":
                        break
            self._check_tls_retry()
            if self.link.state == "closed":
                self.close()
            return self.link
        except ProtocolError as error:
            self.close(error.code)
            return self.link
        except Exception:
            # Provider errors can contain certificates and paths. Never expose
            # their text through logs/status or to the peer.
            self.close(Error.AUTH if self.link is None else Error.RELAY_LOST)
            return self.link

    def _check_tls_retry(self):
        if self.tls_retry is None:
            return
        with self.link.condition:
            if self.tx:
                candidate = self.tx
            elif self.link.outgoing:
                frame, candidate = self.link.outgoing[0]
                stream = self.link.streams.get(frame.stream)
                if (frame.type in (Type.OPEN, Type.DATA) and stream is not None
                        and stream.channel == 2 and not self.link._guard_live(stream.guard)):
                    raise ProtocolError(Error.CANCELLED)
            else:
                raise ProtocolError(Error.CANCELLED)
            if not hmac.compare_digest(candidate, self.tls_retry):
                raise ProtocolError(Error.CANCELLED)

    def _send(self, data):
        if self.tls_retry is not None and not hmac.compare_digest(data, self.tls_retry):
            raise ProtocolError(Error.CANCELLED)
        # Retry the exact same bytes object after either SSL WANT condition.
        attempted = self.tls_retry if self.tls_retry is not None else data
        try:
            sent = self.tls.send(attempted)
        except self.provider.wants():
            self.tls_retry = attempted
            raise
        self.tls_retry = None
        return sent

    def close(self, reason=Error.RELAY_LOST):
        if self.closed:
            return
        self.closed, self.last_error = True, Error(reason)
        try:
            if self.link is not None and self.link.state != "closed":
                if self.tls_retry is None:
                    try:
                        self.tls.send(Frame(Type.ERROR, payload=u32(reason)).encode())
                    except Exception:
                        pass
                self.link.disconnect(reason)
        finally:
            self.tx = b""
            self.tls_retry = None
            try:
                self.raw.close()
            except OSError:
                pass


# A closed connection keeps its local port in TIME_WAIT, for 60 seconds on Linux. After
# the previous relay closed an open Workstation link, its port stays busy that long, so
# a restart waits for it with margin. Only "address in use" waits; every other error
# fails at once.
BIND_WAIT_SECONDS = 90
BIND_RETRY_FIRST, BIND_RETRY_MAX = 0.25, 2.0


class Listener:
    def __init__(self, identity, peer, *, provider, port, pairing_window=None,
                 persist_peer=None, on_link=None, profiles=(), socket_factory=socket.socket,
                 clock=time.monotonic, wait=select.select, sleep=time.sleep, windows=None, linux=None):
        if type(port) is not int or not 1 <= port <= 65535:
            raise StateError("invalid-relay-port")
        self.identity, self.peer, self.provider, self.port = identity, peer, provider, port
        self.window, self.persist_peer, self.on_link = pairing_window, persist_peer, on_link
        self.profiles, self.socket_factory = profiles, socket_factory
        self.clock, self.wait, self.sleep = clock, wait, sleep
        self.windows = os.name == "nt" if windows is None else windows
        self.linux = sys.platform.startswith("linux") if linux is None else linux
        self.on_bind_wait = None
        self.socket = self.connection = None
        self.last_error = None
        # A stored pin refused a different Workstation's certificate.
        self.other_peer_refused = False

    def start(self):
        if self.socket is not None:
            raise StateError("listener-already-running")
        deadline = self.clock() + BIND_WAIT_SECONDS
        delay, waited = BIND_RETRY_FIRST, False
        while True:
            try:
                self.socket = self._bind()
                return self
            except OSError as error:
                if error.errno != errno.EADDRINUSE or self.clock() >= deadline:
                    raise
            if not waited and self.on_bind_wait is not None:
                self.on_bind_wait()
            waited = True
            self.sleep(max(0.0, min(delay, deadline - self.clock())))
            delay = min(delay * 2, BIND_RETRY_MAX)

    def _bind(self):
        listener = self.socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if self.windows:
                # Windows SO_REUSEADDR permits another process to hijack the port.
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            elif self.linux:
                # Linux: a restart may bind over this relay's own TIME_WAIT connections.
                # Linux refuses it while any other socket listens on the port, so nothing
                # can share or take over the relay's port.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # macOS and other BSD systems: no SO_REUSEADDR. There it would let this
            # loopback bind succeed beside another program listening on every address of
            # the same port; the bounded wait in start() covers TIME_WAIT instead.
            listener.bind(("127.0.0.1", self.port))
            listener.listen(1)
            listener.setblocking(False)
        except BaseException:
            listener.close()
            raise
        return listener

    def poll(self, timeout=0.1):
        if self.socket is None:
            raise StateError("listener-not-running")
        if self.connection is not None and self.connection.closed:
            self._closed()
            self.connection = None
        if self.connection is not None:
            link = self.connection.pump(timeout)
            if self.connection.closed:
                self._closed()
            return link
        readable, _, _ = self.wait([self.socket], [], [], timeout)
        if not readable:
            return None
        try:
            raw_socket, address = self.socket.accept()
        except BlockingIOError:
            return None
        try:
            if address[0] != "127.0.0.1":
                raise ProtocolError(Error.AUTH)
            policy = PeerPolicy(self.peer, self.window, clock=self.clock)
            self.connection = TransportConnection(raw_socket, self.identity, policy, self.provider,
                clock=self.clock, profiles=self.profiles, persist_peer=self._persist,
                on_link=self.on_link, wait=self.wait)
            return self.connection.pump(0)
        except Exception as error:
            raw_socket.close()
            self.last_error = error.code if isinstance(error, ProtocolError) else Error.AUTH
            return None

    def _closed(self):
        self.last_error = self.connection.last_error
        if getattr(getattr(self.connection, "policy", None), "other_peer", False):
            self.other_peer_refused = True

    def _persist(self, record):
        if not callable(self.persist_peer):
            raise ProtocolError(Error.AUTH)
        self.persist_peer(record)
        self.peer = record

    def close(self):
        try:
            if self.connection is not None:
                self.connection.close()
        finally:
            if self.socket is not None:
                self.socket.close()
                self.socket = None


# The longest list word has eight letters; anything longer is a typing slip.
_WORD_MAX = 24


class _WordEntry:
    """Echoed entry of the Workstation's ten words, checked word by word.

    The ten words are a short authentication string, not a secret: this console
    already shows the words it expects, so saying "ok" for each one tells the
    person here nothing the screen does not, and no frame leaves this PC until
    the sequence is submitted once through Ceremony.confirm.

    Re-prompting is only safe for input that can never be a Workstation word.
    A typed word that is not one of the 2048 list words is a typing slip and is
    asked for again. A different list word is what an intercepted link looks
    like: entry ends there and the words so far go to Ceremony.confirm as the
    one attempt the protocol allows, which fails closed. Retrying it until this
    screen's word was accepted would let a mismatch be approved after all.
    """
    def __init__(self, code, write):
        expected = code.split() if isinstance(code, str) else []
        if len(expected) != 10:
            raise ProtocolError(Error.AUTH)
        self.expected, self.write = expected, write
        self.listed = frozenset(word.upper() for word in _words())
        self.words, self.current, self.escape = [], "", None
        self.finished = False
        self.write(self._prompt())

    def _prompt(self):
        return "word %d of 10: " % (len(self.words) + 1)

    def feed(self, char):
        """Take one key; return the line to submit once entry has ended, else None."""
        if self.finished:
            return None
        if self.escape is not None:
            # Swallow cursor and function keys: ESC [ ... final byte, ESC O x, ESC x.
            if self.escape == "" and char in ("[", "O"):
                self.escape = char
            elif self.escape != "[" or "\x40" <= char <= "\x7e":
                # The sequence ends here; CSI parameter bytes keep it open.
                self.escape = None
            return None
        if char == "\x1b":
            self.escape = ""
        elif char in ("\r", "\n", " ", "\t"):
            return self._word()
        elif char in ("\b", "\x7f"):
            if self.current:
                self.current = self.current[:-1]
                self.write("\b \b")
        elif char in ("\x15", "\x17"):
            # Ctrl+U and Ctrl+W clear the word being typed.
            self.write("\b \b" * len(self.current))
            self.current = ""
        elif char.isprintable() and len(self.current) < _WORD_MAX:
            self.current += char
            self.write(char)
        return None

    def _word(self):
        typed, self.current = self.current.upper(), ""
        if not typed:
            return None
        number = len(self.words) + 1
        if typed == self.expected[number - 1]:
            self.words.append(typed)
            if len(self.words) < 10:
                self.write("  ok\n" + self._prompt())
                return None
            self.finished = True
            self.write("  ok\nAll ten words match this PC's words; waiting for the Workstation to confirm.\n")
            return " ".join(self.words)
        if typed not in self.listed:
            hint = " (type the words only, not their numbers)" if typed.isdigit() else ""
            self.write("  that is not word %d - type it again%s\n%s" % (number, hint, self._prompt()))
            return None
        self.finished = True
        self.write("  that is not word %d, and it is another pairing word: the two consoles disagree.\n"
                   "Pairing stops here and nothing is stored. If you misread the word, run pair again "
                   "for new words.\nIf the Workstation really shows this word, do not pair over this link.\n"
                   % number)
        return " ".join(self.words + [typed])


class _ConsoleInput:
    """Echoed, deadline-compatible local console input, without reader threads.

    Keys are read one at a time and checked by _WordEntry; this program does the
    echo. POSIX: non-canonical mode with terminal echo off and ISIG kept, so
    Ctrl+C still interrupts. Windows: msvcrt.getwch. The terminal settings are
    restored on every exit path, including Ctrl+C and the pairing deadline.
    """
    def __init__(self, *, windows=None, stdin=None, stdout=None):
        self.windows = os.name == "nt" if windows is None else windows
        self.stdin = sys.stdin if stdin is None else stdin
        self.stdout = sys.stdout if stdout is None else stdout
        self.attributes = self.entry = None
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def __enter__(self):
        if not self.stdin.isatty() or not self.stdout.isatty():
            raise StateError("pair-console-required")
        if not self.windows:
            import termios
            self.termios = termios
            fd = self.stdin.fileno()
            attributes = termios.tcgetattr(fd)
            keys = list(attributes)
            keys[6] = list(attributes[6])
            keys[3] &= ~(termios.ECHO | termios.ICANON)
            keys[6][termios.VMIN], keys[6][termios.VTIME] = 1, 0
            # Recorded first, so a failed change below is still undone on exit.
            self.attributes = attributes
            termios.tcsetattr(fd, termios.TCSANOW, keys)
        return self

    def write(self, text):
        self.stdout.write(text)
        self.stdout.flush()

    def expect(self, code):
        """Start word entry once the ten words are on screen; earlier keys are dropped."""
        if self.windows:
            import msvcrt
            while msvcrt.kbhit():
                if msvcrt.getwch() == "\x03":
                    raise KeyboardInterrupt()
        else:
            self.termios.tcflush(self.stdin.fileno(), self.termios.TCIFLUSH)
        self.entry = _WordEntry(code, self.write)

    def _keys(self):
        if self.windows:
            import msvcrt
            keys = []
            while len(keys) < 64 and msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ("\x00", "\xe0"):
                    # Arrow and function keys arrive as a prefix and one more code.
                    if msvcrt.kbhit():
                        msvcrt.getwch()
                    continue
                if char != "\x1b":
                    # Esc alone; this console sends no escape sequences.
                    keys.append(char)
            return keys
        fd = self.stdin.fileno()
        if not select.select([fd], [], [], 0)[0]:
            return ()
        data = os.read(fd, 64)
        if not data:
            raise ProtocolError(Error.AUTH)
        return self.decoder.decode(data)

    def poll(self):
        if self.entry is None:
            return None
        for char in self._keys():
            if char == "\x03":
                raise KeyboardInterrupt()
            if char == "\x04":
                # End of input, as with the line reader before: fail closed.
                raise ProtocolError(Error.AUTH)
            typed = self.entry.feed(char)
            if typed is not None:
                return typed
        return None

    def __exit__(self, *unused):
        try:
            if self.entry is not None and not self.entry.finished:
                # Leave the half-typed word's line so later messages start clean.
                self.write("\n")
        finally:
            self.entry = None
            if self.attributes is not None:
                attributes, self.attributes = self.attributes, None
                self.termios.tcsetattr(self.stdin.fileno(), self.termios.TCSANOW, attributes)


def pair_console(owner, *, listener=None, input_fn=None, output_fn=None, stop=None):
    """Foreground pairing; no remote arm request, code in argv or code journal."""
    owner.validate()
    config = owner.read_json("config.json")
    clock = listener.clock if listener is not None else time.monotonic
    window = PairingWindow(clock())
    store = IdentityStore(owner)
    built = listener is None
    if listener is None:
        # Reject non-console callers before binding or creating identity files.
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise StateError("pair-console-required")
        provider = load_provider()
        identity = store.load_or_create(provider)
        peer = store.load_peer()
        listener = Listener(identity, peer, provider=provider, port=config["port"],
                            pairing_window=window, persist_peer=store.save_peer)
    else:
        listener.window = window
    output = output_fn or print
    if built:
        listener.on_bind_wait = lambda: output(
            "The relay port is still in use; waiting up to %d seconds for it." % BIND_WAIT_SECONDS)
    console = None
    shown = submitted = False
    try:
        if input_fn is None:
            console = _ConsoleInput()
            console.__enter__()
        listener.start()
        output("Pairing window open for five minutes and one unpaired attempt.")
        while clock() < window.deadline and not (stop and stop.is_set()):
            link = listener.poll(0.1)
            if getattr(listener, "other_peer_refused", False):
                # A stored pin admits only its own Workstation. Pairing a
                # different one needs an explicit local unpair first.
                raise StateError("peer-revocation-required")
            if listener.last_error is not None and window.consumed:
                raise ProtocolError(Error.AUTH)
            if link is None:
                continue
            code = link.pairing_code
            if code and not shown:
                output("Compare these ten words with the Workstation console:")
                output(code)
                if console is None:
                    output("Type the Workstation's ten words here, then press Enter.")
                else:
                    output("Type the Workstation's ten words here. Space or Enter ends a word; "
                           "each word is checked as you go.")
                    console.expect(code)
                shown = True
            if shown and not submitted:
                typed = input_fn() if input_fn is not None else console.poll()
                if typed is not None:
                    link.confirm_pairing(typed)
                    submitted = True
                    typed = None
            if link.state == "active":
                if not submitted:
                    raise StateError("peer-already-paired")
                connection = getattr(listener, "connection", None)
                with link.condition:
                    waiting = bool(link.outgoing)
                if waiting or (connection is not None and (connection.tx or connection.tls_retry)):
                    # A remote COMMIT can arrive before our queued COMMIT has
                    # reached TLS. Do not close the console's link before it.
                    continue
                output("Both consoles confirmed; the Workstation pin is stored.")
                return PinRecord(link.peer_id, link.peer_pin)
            if link.state == "closed":
                raise ProtocolError(Error.AUTH)
        raise ProtocolError(Error.TIMEOUT)
    finally:
        try:
            listener.close()
        finally:
            if console is not None:
                console.__exit__()
