"""d32accel: the GPU_EXT-direct forwarder (the final design E1, PYTHON-ON-OPENBSD 7.3 "d32gpu", HARDENING 5.8 item 5).

deep32-engine sends its query embeddings and reranks, unchanged, to two local listeners; this process forwards each
request over the tailnet to the hub's TEI (https://<hub>:443/v1/embeddings) and reranker
(https://<hub>:8443/v1/rerank), the Tailscale Serve endpoints production deep32-engine uses today, and hands the
answer back byte for byte. It is OFF unless the GPU_EXT setting is on (rc.d refuses to start it unless /etc/d32/state/gpu_ext = "on"; it
re-reads the published copy /var/d32/run/gpu_ext at every re-check and forwards nothing once it says "off"), and even
then it forwards nothing until the hub has passed the identity check, which it repeats every
GPU_EXT_RECHECK_SECONDS:

  embedder  GET /info: model_id must equal GPU_EXT_EMBED_MODEL_ID. Revision: TEI reports "model_sha" (null on
            the hub today [m], hub-probe 2026-11-05); if it reports one it must equal GPU_EXT_EMBED_REVISION; if
            not, the golden probe decides: POST the recorded probe input, take the vector as float32
            little-endian, and its SHA-256 must equal GPU_EXT_EMBED_PROBE_SHA256.
  reranker  GET /health: status "ok" and model == GPU_EXT_RERANK_MODEL_ID. Revision: a "revision" field if the
            service reports one (it does not today), else the golden probe file GPU_EXT_RERANK_PROBE (query,
            documents, the recorded order and scores): same order, every score within its tolerance.
  With GPU_EXT_REQUIRE_REVISION=yes (the default) a model whose revision is neither reported nor proven by a
  probe is refused, not warned about (INTEGRATIONS 2.4 step 5).

Until verified, or while the setting is off, every request gets 503 with a JSON reason: deep32-engine's breaker then
degrades dense or the reranker, which /v2/status and every search answer report.

Listeners come from d32-sockexec (D32_LISTEN_FDS "3=embed,4=rerank"): UNIX sockets 0660 root:_d32core by default
(deep32-engine then needs no inet), or loopback TCP ports. Only POST /v1/embeddings (embed) and POST /v1/rerank (rerank)
are forwarded; GET /d32/status answers the verification state; anything else is 404 or 405.

Runs as _d32gpu with the system python3.14, stdlib only; pledge "stdio rpath inet unix prot_exec" after start-up
(rpath: the setting file and lazy imports; inet: the hub; unix: accept on the sockets). The hub is reached by
address (GPU_EXT_ADDR, the tailnet IP) with the TLS name GPU_EXT_HOST checked against GPU_EXT_CA, so no DNS.
GPU_EXT_TRANSPORT=loopback instead reaches the authenticated local doors over HTTP at 127.0.0.1:8081/8082;
only GPU_EXT_HOST=localhost and that exact address and port pair are accepted in loopback mode (C8).
No query text, vector or passage is ever logged: one line per request with path, status, milliseconds and bytes.
"""
from __future__ import annotations

import hashlib
import http.client
import http.server
import json
import logging
import math
import os
import re
import socket
import socketserver
import ssl
import stat
import struct
import sys
import threading
import time
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

import d32svc  # noqa: E402

log = logging.getLogger("d32svc")

ROUTES = {"embed": "/v1/embeddings", "rerank": "/v1/rerank"}
FORWARD_REQUEST_HEADERS = ("content-type", "accept", "user-agent")
FORWARD_RESPONSE_HEADERS = ("content-type",)
MAX_BODY = 8 * 1024 * 1024
FORGE_TOKEN_FILE = "/etc/d32/private/forge.token"
UNPAIRED = "d32accel: Forge is not paired; pair this Workstation with a Forge"
BAD_CREDENTIAL = "d32accel: Forge credential is unavailable; re-pair this Workstation with a Forge"


class ForgeCredentialError(ValueError):
    """A fixed, nonsecret diagnostic suitable for the service's startup error."""


class ForgeAuthRejected(ValueError):
    pass


def _not_link(st) -> bool:
    return not (stat.S_ISLNK(st.st_mode) or
                getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _credential_permissions(parent, token, gpu_gid: int, wheel_gid: int) -> bool:
    """The shared private directory permits traversal; each credential has its own service group."""
    return (parent.st_uid == token.st_uid == 0 and token.st_gid == gpu_gid and
            stat.S_IMODE(token.st_mode) == 0o440 and
            (parent.st_gid, stat.S_IMODE(parent.st_mode)) in ((gpu_gid, 0o750), (wheel_gid, 0o711)))


def load_forge_token(path: str) -> str:
    """Read one bounded credential before confinement. Never include its value or path in an error.

    Production calls this only with FORGE_TOKEN_FILE. A Python caller may supply a synthetic test path;
    environment settings cannot change the approved installed location.
    """
    parent_fd = token_fd = None
    try:
        parent_path, name = os.path.split(path)
        parent = os.lstat(parent_path)
        before = os.lstat(path)
        if not _not_link(parent) or not stat.S_ISDIR(parent.st_mode) or not _not_link(before):
            raise ForgeCredentialError(BAD_CREDENTIAL)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        if os.name == "posix":
            import grp  # read the group database before confinement

            gpu_gid = grp.getgrnam("_d32gpu").gr_gid
            wheel_gid = grp.getgrnam("wheel").gr_gid
            # The private directory is 0711 for its consumers: they may traverse it but not open or list it, so the
            # credential is opened by path (O_NOFOLLOW) and the directory is checked with lstat.
            token_fd = os.open(path, flags | os.O_NONBLOCK)
            parent = os.lstat(parent_path)
            if not _not_link(parent) or not stat.S_ISDIR(parent.st_mode):
                raise ForgeCredentialError(BAD_CREDENTIAL)
        else:  # offline source tests on platforms without POSIX owner/group/mode semantics
            token_fd = os.open(path, flags | getattr(os, "O_BINARY", 0))
        token = os.fstat(token_fd)
        if (not stat.S_ISREG(token.st_mode) or not _not_link(token) or
                (before.st_dev, before.st_ino) != (token.st_dev, token.st_ino) or
                token.st_size not in (64, 65)):
            raise ForgeCredentialError(BAD_CREDENTIAL)
        if os.name == "posix" and not _credential_permissions(parent, token, gpu_gid, wheel_gid):
            raise ForgeCredentialError(BAD_CREDENTIAL)
        data = os.read(token_fd, 66)
        if re.fullmatch(rb"[0-9a-f]{64}\n?", data) is None:
            raise ForgeCredentialError(BAD_CREDENTIAL)
        return data.rstrip(b"\n").decode("ascii")
    except FileNotFoundError:
        raise ForgeCredentialError(UNPAIRED) from None
    except (OSError, KeyError, ValueError) as exc:
        if isinstance(exc, ForgeCredentialError):
            raise
        raise ForgeCredentialError(BAD_CREDENTIAL) from None
    finally:
        if token_fd is not None:
            os.close(token_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _yes(value: str) -> bool:
    return value.strip().lower() in ("1", "yes", "on", "true")


class Settings:
    def __init__(self, env: dict[str, str], *, token_path: str | None = None) -> None:
        self.transport = env.get("GPU_EXT_TRANSPORT", "tailnet")
        if self.transport not in ("tailnet", "loopback"):
            raise SystemExit("d32accel: GPU_EXT_TRANSPORT must be tailnet or loopback")
        self.host = env.get("GPU_EXT_HOST", "")
        self.addr = env.get("GPU_EXT_ADDR", "")
        default_ports = {"embed": 8081, "rerank": 8082} if self.transport == "loopback" else {"embed": 443, "rerank": 8443}
        try:
            self.ports = {kind: int(env.get(f"GPU_EXT_{kind.upper()}_PORT", str(port)))
                          for kind, port in default_ports.items()}
        except ValueError:
            raise SystemExit("d32accel: Forge ports must be integers from 1 to 65535") from None
        if any(not 1 <= port <= 65535 for port in self.ports.values()):
            raise SystemExit("d32accel: Forge ports must be integers from 1 to 65535")
        self.model_endpoints = {}
        endpoint_keys = {"embed": "GPU_EXT_EMBED_URL", "rerank": "GPU_EXT_RERANK_URL"}
        if any(key in env for key in endpoint_keys.values()):
            try:
                if self.transport != "tailnet" or any(key not in env for key in endpoint_keys.values()):
                    raise ValueError()
                for kind, key in endpoint_keys.items():
                    value = env[key]
                    match = re.fullmatch(r'https://([a-z0-9](?:[a-z0-9.-]{0,245}[a-z0-9])?\.ts\.net)(?::([1-9][0-9]{0,4}))?', value)
                    if (not match or match[1] != self.host or
                            any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                for label in match[1].split('.'))):
                        raise ValueError()
                    port = urlsplit(value).port or 443
                    if (not 1 <= port <= 65535 or
                            f"GPU_EXT_{kind.upper()}_PORT" in env and port != self.ports[kind]):
                        raise ValueError()
                    self.model_endpoints[kind] = value
                    self.ports[kind] = port
            except (TypeError, ValueError):
                raise SystemExit("d32accel: Forge endpoints must be path-free HTTPS URLs on the paired Forge DNS name") from None
        if self.transport == "loopback" and (self.host != "localhost" or self.addr != "127.0.0.1" or
                                             self.ports != default_ports):
            raise SystemExit("d32accel: loopback requires localhost at 127.0.0.1 on ports 8081 and 8082")
        self.ca = env.get("GPU_EXT_CA", "/etc/ssl/cert.pem")
        self.state_file = env.get("GPU_EXT_STATE", "/var/d32/run/gpu_ext")
        self.recheck = float(env.get("GPU_EXT_RECHECK_SECONDS", "60"))
        self.connect_timeout = float(env.get("GPU_EXT_CONNECT_TIMEOUT", "5"))
        self.read_timeout = float(env.get("GPU_EXT_READ_TIMEOUT", "120"))
        self.require_revision = _yes(env.get("GPU_EXT_REQUIRE_REVISION", "yes"))
        self.embed_model = env.get("GPU_EXT_EMBED_MODEL_ID", "")
        self.embed_revision = env.get("GPU_EXT_EMBED_REVISION", "")
        self.embed_probe_input = env.get("GPU_EXT_EMBED_PROBE_INPUT", "")
        self.embed_probe_model = env.get("GPU_EXT_EMBED_PROBE_MODEL", "")
        self.embed_probe_sha256 = env.get("GPU_EXT_EMBED_PROBE_SHA256", "").lower()
        self.rerank_model = env.get("GPU_EXT_RERANK_MODEL_ID", "")
        self.rerank_revision = env.get("GPU_EXT_RERANK_REVISION", "")
        self.rerank_probe_file = env.get("GPU_EXT_RERANK_PROBE", "")
        if not self.host or not self.embed_model or not self.rerank_model:
            raise SystemExit("d32accel: GPU_EXT_HOST, GPU_EXT_EMBED_MODEL_ID and GPU_EXT_RERANK_MODEL_ID are required")
        self.token_file = env.get("GPU_EXT_TOKEN_FILE", "")
        if not self.token_file:
            raise SystemExit(UNPAIRED)
        if self.token_file != FORGE_TOKEN_FILE or "GPU_EXT_TOKEN" in env:
            raise SystemExit(BAD_CREDENTIAL)
        try:
            self.forge_token = load_forge_token(self.token_file if token_path is None else token_path)
        except ForgeCredentialError as exc:
            raise SystemExit(str(exc)) from None


# Installed beside this wrapper. Source-only tests use the repository's canonical module.
shared_source = os.path.realpath(os.path.join(HERE, "../../../../model-bridge"))
if os.path.isfile(os.path.join(shared_source, "d32_model_bridge.py")):
    sys.path.insert(0, shared_source)
from d32_model_bridge import (Upstream, Attestor as PortableAttestor, Server, make_handler,
                              ForgeAuthRejected, setting_on)


class Attestor(PortableAttestor):
    def __init__(self, s, up):
        super().__init__(s, up)
        self.is_enabled = lambda: setting_on(self.s.state_file)


def main() -> None:
    svc = d32svc.boot("d32accel")
    s = Settings(dict(os.environ))
    missing = [k for k in ROUTES if k not in svc.listeners]
    if missing:
        raise SystemExit(f"d32accel: listeners {missing} not inherited (start it through rc.d/d32accel)")
    if not s.addr:  # resolve once, before confinement (no "dns" promise afterwards)
        s.addr = socket.getaddrinfo(s.host, s.ports["embed"], socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    ctx = None
    if s.transport == "tailnet":
        ctx = ssl.create_default_context(cafile=s.ca)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    up = {k: Upstream(s, ctx, s.ports[k]) for k in ROUTES}
    att = Attestor(s, up)
    servers = {k: Server(svc.listeners[k], make_handler(k, att, up[k])) for k in ROUTES}

    unveils = d32svc.base_unveils() + [(s.state_file, "r")]
    d32svc.confine(svc, unveils, "stdio rpath inet unix prot_exec")

    stop = threading.Event()
    threading.Thread(target=att.loop, args=(stop,), name="attest", daemon=True).start()
    threads = [threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, name=f"serve-{k}",
                                daemon=True) for k, srv in servers.items()]
    for t in threads:
        t.start()
    log.warning("d32accel: forwarding %s to %s (%s), GPU_EXT setting %s", ", ".join(sorted(ROUTES)), s.host,
                s.addr, s.state_file)

    import signal

    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.wait(1.0):
        pass
    for srv in servers.values():
        srv.shutdown()


if __name__ == "__main__":
    main()
