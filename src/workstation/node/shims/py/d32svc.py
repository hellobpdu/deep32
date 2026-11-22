"""The steps every Deep32 node service launcher shares (PYTHON-ON-OPENBSD 7.2-7.3, HARDENING 5.8, the final design C6).

A service is started by rc.d as root through d32-sockexec, which binds its listeners, opens its environment file
(root 0400), drops to the service user and execs `<venv>/bin/python3.x -I -B -X utf8 <launcher>`. The launcher
then calls, in this order:

  boot()                 8 MiB thread stacks; the environment rebuilt from scratch (login-class keys + the
                         environment file); the inherited listeners; refuse to run as root
  ... imports ...        the app and everything it loads lazily (pre-import)
  ConfinedServer.run()   uvicorn on the pre-bound sockets; after the app's lifespan start-up (all files, databases
                         and clients opened) it unveils each path, locks unveil and pledges: confine()

Test-only switches in the environment file (a release image never carries them; release lint refuses them):
  D32_PLEDGE_ERROR=1   pledge with "error" (violations return ENOSYS and show in ktrace -t p) for the pre-flight
  D32_AUDIT=1          a sys.addaudithook that prints, once each, every file open, mkdir, remove, rename, socket
                       connect/bind and import after confinement ("d32-audit ...", on stderr): G-split's check
"""
from __future__ import annotations

import logging
import os
import pwd
import re
import socket
import stat
import sys
import threading

THREAD_STACK = 8 << 20
ENV_MAX_BYTES = 64 * 1024
KEY = re.compile(r"[A-Z_][A-Z0-9_]*\Z")

log = logging.getLogger("d32svc")
log.setLevel(logging.INFO)
log.propagate = False  # never through the root logger: production's own log configuration stays as it is
_h = logging.StreamHandler(sys.stderr)
_h.setFormatter(logging.Formatter("d32svc: %(message)s"))
log.addHandler(_h)


def configure_threads(size: int = THREAD_STACK) -> None:
    """Every thread started after this gets `size` bytes of stack (pyrag lane, STATUS 6: OpenBSD's default thread
    stack is 512 KiB, CPython's C recursion limit assumes Linux's 8 MiB; a deep json/repr in a worker thread would
    SIGSEGV where production raises RecursionError)."""
    threading.stack_size(size)


def parse_env(data: bytes, source: str = "environment file") -> dict[str, str]:
    """KEY=VALUE lines, '#' comments, blank lines; a value in matching single or double quotes loses them.
    No expansion, no export, no continuation: the file is data, never shell."""
    out: dict[str, str] = {}
    for n, raw in enumerate(data.decode("utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not KEY.match(key):
            raise SystemExit(f"{source} line {n}: not KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


class Service:
    """What boot() found: the service's options (D32_* keys), listeners by name, and its user."""

    def __init__(self, name: str, opts: dict[str, str], listeners: dict[str, socket.socket], user: str) -> None:
        self.name = name
        self.opts = opts
        self.listeners = listeners
        self.user = user

    def flag(self, key: str) -> bool:
        return self.opts.get(key, "").strip().lower() in ("1", "yes", "on", "true")

    @property
    def preflight(self) -> bool:
        return self.flag("D32_PLEDGE_ERROR")


def boot(name: str, class_keys: tuple[str, ...] = (), required: tuple[str, ...] = ()) -> Service:
    """Thread stacks, environment, listeners. `class_keys` are set by the service's login class (a schg file) and
    are kept from the inherited environment; the environment file may not set them. Every other inherited
    variable is dropped, so no PYTHON*, proxy, SSL_CERT_* or locale setting reaches the app unless the
    environment file names it."""
    configure_threads()
    if os.getuid() == 0 or os.geteuid() == 0:
        raise SystemExit(f"{name}: refusing to run as root (start it through rc.d and d32-sockexec)")
    user = pwd.getpwuid(os.getuid()).pw_name
    inherited = dict(os.environ)

    file_env: dict[str, str] = {}
    env_fd = inherited.get("D32_ENV_FD")
    if env_fd:
        with os.fdopen(int(env_fd), "rb", closefd=True) as f:
            data = f.read(ENV_MAX_BYTES + 1)
        if len(data) > ENV_MAX_BYTES:
            raise SystemExit(f"{name}: environment file larger than {ENV_MAX_BYTES} bytes")
        file_env = parse_env(data)
    clash = sorted(k for k in file_env if k in class_keys)
    if clash:
        raise SystemExit(f"{name}: the environment file may not set {', '.join(clash)} (the login class does)")
    missing = sorted(k for k in class_keys if not inherited.get(k))
    if missing:
        raise SystemExit(f"{name}: the login class lost {', '.join(missing)}; refusing to start")

    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": "/var/empty", "USER": user, "LOGNAME": user}
    env.update({k: inherited[k] for k in class_keys})
    opts = {k: v for k, v in file_env.items() if k.startswith("D32_")}
    env.update({k: v for k, v in file_env.items() if not k.startswith("D32_")})
    missing = sorted(k for k in required if not env.get(k))
    if missing:
        raise SystemExit(f"{name}: missing required environment: {', '.join(missing)}")

    listeners: dict[str, socket.socket] = {}
    for item in filter(None, inherited.get("D32_LISTEN_FDS", "").split(",")):
        fd, _, lname = item.partition("=")
        listeners[lname] = socket.socket(fileno=int(fd))

    os.environ.clear()
    os.environ.update(env)
    sys.stdout.reconfigure(line_buffering=True)  # rc.d pipes stdout to syslog: one line, one record
    return Service(name, opts, listeners, user)


def realpath_dir(path: str) -> str:
    """The resolved path: unveil and sys.path must name the same vnode chain (no symlink in between)."""
    return os.path.realpath(path)


def read_credential(path: str, group: str, name: str) -> str:
    """One 64-digit credential from its protected file, read before confinement; never shows the value.

    The single-token sibling of serve_edge.load_mcp_tokens: no symlink in the path, a parent owned by root with mode
    0711, the file opened without following a link, a regular file owned by root, group `group`, mode 0440 (the
    owner and mode checks on POSIX only), at most 65 bytes: exactly 64 lower-case hex digits and one newline.
    """
    message = name + ": engine credential unavailable; run gateway enrolment"
    try:
        target = os.path.abspath(path)
        part = target
        while True:
            if os.path.islink(part) or getattr(os.path, "isjunction", lambda _: False)(part):
                raise ValueError()
            parent = os.path.dirname(part)
            if parent == part:
                break
            part = parent
        gid = None
        if os.name == "posix":
            import grp
            gid = grp.getgrnam(group).gr_gid
            info = os.stat(os.path.dirname(target))
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o711:
                raise ValueError()
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode):
                raise ValueError()
            if os.name == "posix" and (meta.st_uid != 0 or meta.st_gid != gid or stat.S_IMODE(meta.st_mode) != 0o440):
                raise ValueError()
            raw = stream.read(66)
        if len(raw) != 65 or re.fullmatch(rb"[0-9a-f]{64}\n", raw) is None:
            raise ValueError()
        return raw[:64].decode("ascii")
    except (OSError, ValueError, KeyError, UnicodeError):
        raise SystemExit(message) from None


def confine(svc: Service, unveils: list[tuple[str, str]], promises: str) -> None:
    """unveil each (path, perms), lock, pledge. A path that does not exist (and whose directory does not either)
    is skipped with a log line: unveil cannot name it, and nothing can be opened there anyway."""
    import pledge  # the shim; OpenBSD only

    seen: set[str] = set()
    for path, perms in unveils:
        if (path, perms) in seen:
            continue
        seen.add((path, perms))
        try:
            pledge.unveil(path, perms)
        except FileNotFoundError:
            log.warning("unveil skipped (absent): %s", path)
    pledge.unveil_lock()
    final = promises + (" error" if svc.preflight else "")
    pledge.pledge(final, None)
    log.warning("%s confined: pledge %r, %d unveiled paths%s", svc.name, final, len(seen),
                " (PRE-FLIGHT: violations return ENOSYS)" if svc.preflight else "")
    if svc.flag("D32_AUDIT"):
        start_audit(svc.name)


AUDIT_EVENTS = {"open", "os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.chmod", "os.chown", "os.truncate",
                "socket.connect", "socket.bind", "import", "subprocess.Popen", "os.exec", "os.system", "os.fork",
                "ctypes.dlopen", "shutil.copyfile"}


def start_audit(name: str) -> None:
    """After confinement: print each distinct file/socket/import event once (no data, names and paths only)."""
    seen: set[tuple] = set()
    lock = threading.Lock()

    def hook(event: str, args: tuple) -> None:
        if event not in AUDIT_EVENTS:
            return
        if event == "open":
            key = (event, str(args[0]), str(args[1]))
        elif event == "import":
            key = (event, str(args[0]))
        elif event.startswith("socket."):
            key = (event, repr(args[1])[:120])
        else:
            key = (event, repr(args)[:160])
        with lock:
            if key in seen:
                return
            seen.add(key)
        sys.stderr.write(f"d32-audit {name} {' '.join(key)}\n")

    sys.addaudithook(hook)


def make_server(config, svc: Service, confine_cb):
    """A uvicorn.Server that confines itself once its start-up is complete: after the app's lifespan has opened
    everything and the servers listen on the inherited sockets, before the first request is read."""
    import uvicorn

    class ConfinedServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.should_exit:
                return
            confine_cb()
            log.warning("%s serving on %s", svc.name, ", ".join(sorted(svc.listeners)))

    return ConfinedServer(config)


def base_unveils() -> list[tuple[str, str]]:
    """Read-only paths every Python service needs after confinement: the interpreter prefix and venv (lazy imports,
    .pyc), the shared libraries and ld.so hints (a late dlopen under prot_exec), time zone data, and this directory."""
    import sysconfig

    here = realpath_dir(os.path.dirname(os.path.abspath(__file__)))
    std = sysconfig.get_paths(vars={"base": sys.base_prefix, "platbase": sys.base_prefix})
    paths = [realpath_dir(std["stdlib"]), realpath_dir(std["platstdlib"]), realpath_dir(sys.prefix), here,
             "/usr/lib", "/usr/local/lib", "/var/run/ld.so.hints", "/etc/localtime", "/usr/share/zoneinfo"]
    return [(p, "r") for p in paths]
