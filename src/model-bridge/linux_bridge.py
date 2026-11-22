"""Internal Linux adapter for the shared authenticated model bridge.

The engine has access to this listener, never to the raw model network. Its
Forge credential is an enrolment-owned, read-only file, not an environment value.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import http.server
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
import d32_model_bridge as shared

CONFIG = "/etc/deep32/model-bridge.json"
FORGE_TOKEN = "/run/secrets/forge-token"
CORE_TOKEN = "/run/secrets/core-token"
MCP_TOKENS = "/run/secrets/mcp-tokens"
PORTS = {"embed": 9081, "rerank": 9082}
IMAGE_PROBES = "/opt/deep32/model-bridge/gpu-probes.json"  # in the model-bridge image
CREDENTIAL_ERROR = "Credentials are unavailable; complete enrolment and restart the full role."
CONFIG_ERROR = "Model identity settings are invalid; restore the signed model configuration."
ALLOWED = frozenset({"schema", "GPU_EXT_EMBED_MODEL_ID", "GPU_EXT_EMBED_REVISION",
    "GPU_EXT_RERANK_MODEL_ID", "GPU_EXT_RERANK_REVISION", "GPU_EXT_EMBED_PROBE_INPUT",
    "GPU_EXT_EMBED_PROBE_SHA256", "GPU_EXT_EMBED_PROBE_MODEL", "GPU_EXT_RERANK_PROBE",
    "GPU_EXT_RECHECK_SECONDS", "GPU_EXT_CONNECT_TIMEOUT", "GPU_EXT_READ_TIMEOUT", "GPU_EXT_PROBE_ALWAYS"})


class CredentialError(ValueError):
    pass


def protected_tokens(path: str, *, multiple: bool = False) -> tuple[str, ...]:
    """Bounded, descriptor-checked credential read with fixed, non-secret errors.

Production callers supply fixed mount paths. Alternate paths are used only by
offline fixtures. Root owns the file; the service's group may read it.
"""
    fd = None
    try:
        candidate = Path(os.path.abspath(path))
        for part in (candidate, *candidate.parents):
            meta = part.lstat()
            if stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise CredentialError(CREDENTIAL_ERROR)
        before = candidate.lstat()
        parent = candidate.parent.stat()
        if os.name == "posix" and (parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) & 0o022):
            raise CredentialError(CREDENTIAL_ERROR)
        fd = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                     getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or
                (meta.st_dev, meta.st_ino) != (before.st_dev, before.st_ino)):
            raise CredentialError(CREDENTIAL_ERROR)
        if os.name == "posix" and (meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) not in (0o400, 0o440) or
                (stat.S_IMODE(meta.st_mode) == 0o440 and meta.st_gid not in (os.getegid(), *os.getgroups()))):
            raise CredentialError(CREDENTIAL_ERROR)
        limit = 65536 if multiple else 65
        if not 0 < meta.st_size <= limit:
            raise CredentialError(CREDENTIAL_ERROR)
        raw = os.read(fd, limit + 1)
        if len(raw) > limit or (multiple and not raw.endswith(b"\n")):
            raise CredentialError(CREDENTIAL_ERROR)
        if not multiple and re.fullmatch(rb"[0-9a-f]{64}\n?", raw) is None:
            raise CredentialError(CREDENTIAL_ERROR)
        values = raw.decode("ascii").splitlines()
        if (not values or (not multiple and len(values) != 1) or len(values) != len(set(values)) or
                any(re.fullmatch("[0-9a-f]{64}", value) is None for value in values) or b"\r" in raw):
            raise CredentialError(CREDENTIAL_ERROR)
        return tuple(values)
    except (OSError, ValueError, UnicodeError):
        raise CredentialError(CREDENTIAL_ERROR) from None
    finally:
        if fd is not None:
            os.close(fd)


class CredentialGuard:
    """A changed/deleted/revoked credential cannot inherit old attestation.

Rotation is deliberately restart-only, matching the door and enrolment lifecycle.
Rechecking the protected file before each outgoing request closes the pooled
connection window; no automatic credential reload silently changes trust.
"""
    def __init__(self, path: str = FORGE_TOKEN):
        self.path = path
        self.token = protected_tokens(path)[0]
        self.digest = hashlib.sha256(self.token.encode("ascii")).digest()

    def current(self) -> bool:
        try:
            value = protected_tokens(self.path)[0]
            return hmac.compare_digest(hashlib.sha256(value.encode("ascii")).digest(), self.digest)
        except CredentialError:
            return False


class Settings:
    def __init__(self, config: dict, guard: CredentialGuard):
        try:
            if (not isinstance(config, dict) or set(config) - ALLOWED or
                    config.get("schema") != "deep32.model-bridge.v1"):
                raise ValueError()
            if any(not isinstance(v, str) for k, v in config.items() if k != "schema"):
                raise ValueError()
            self.transport, self.host, self.addr = "container", "door", "door"
            self.ports = {"embed": 8081, "rerank": 8082}
            self.state_file = ""  # The Linux role is enabled only while its container runs.
            self.recheck = float(config.get("GPU_EXT_RECHECK_SECONDS", "60"))
            self.connect_timeout = float(config.get("GPU_EXT_CONNECT_TIMEOUT", "5"))
            self.read_timeout = float(config.get("GPU_EXT_READ_TIMEOUT", "120"))
            if any(not math.isfinite(v) or v <= 0 or v > 300 for v in
                   (self.recheck, self.connect_timeout, self.read_timeout)):
                raise ValueError()
            self.require_revision = True
            self.embed_model = config.get("GPU_EXT_EMBED_MODEL_ID", "")
            self.embed_revision = config.get("GPU_EXT_EMBED_REVISION", "")
            self.embed_probe_input = config.get("GPU_EXT_EMBED_PROBE_INPUT", "")
            self.embed_probe_model = config.get("GPU_EXT_EMBED_PROBE_MODEL", "")
            self.embed_probe_sha256 = config.get("GPU_EXT_EMBED_PROBE_SHA256", "")
            self.rerank_model = config.get("GPU_EXT_RERANK_MODEL_ID", "")
            self.rerank_revision = config.get("GPU_EXT_RERANK_REVISION", "")
            self.rerank_probe_file = config.get("GPU_EXT_RERANK_PROBE", "")
            always = config.get("GPU_EXT_PROBE_ALWAYS", "no")
            if always not in ("yes", "no"):
                raise ValueError()
            self.probe_always = always == "yes"
            if (not self.embed_model or not self.rerank_model or
                    (not self.embed_revision and not (self.embed_probe_input and re.fullmatch("[0-9a-f]{64}", self.embed_probe_sha256))) or
                    (not self.rerank_revision and not self.rerank_probe_file) or
                    (self.probe_always and not self.rerank_probe_file)):
                raise ValueError()
            if self.rerank_probe_file and self.rerank_probe_file != IMAGE_PROBES:
                path = PurePosixPath(self.rerank_probe_file)
                if (not self.rerank_probe_file.startswith("/etc/deep32/") or
                        str(path) != self.rerank_probe_file or ".." in path.parts):
                    raise ValueError()
            self.forge_token = guard.token
        except (TypeError, ValueError):
            raise ValueError(CONFIG_ERROR) from None


class Door(shared.Upstream):
    def __init__(self, settings, port, guard):
        super().__init__(settings, None, port)
        self.guard = guard

    def _connect(self):
        # Docker service DNS is confined to the internal access network. The
        # adapter cannot select another upstream, raw model port or TLS bypass.
        return http.client.HTTPConnection("door", self.port, timeout=self.s.read_timeout)

    def request(self, method, path, body, headers):
        if not self.guard.current():
            return 401, {}, b""
        return super().request(method, path, body, headers)


class Attestor(shared.Attestor):
    def __init__(self, settings, upstreams, guard):
        super().__init__(settings, upstreams)
        self.guard = guard
        self.is_enabled = lambda: True

    def ok(self, kind):
        if not self.guard.current():
            self.invalidate("forge-credential-changed: complete enrolment and restart the full role")
        return super().ok(kind)

    def snapshot(self):
        self.ok("embed")
        snapshot = super().snapshot()
        snapshot["ready"] = all(self.ok(kind)[0] for kind in shared.ROUTES)
        return snapshot


def load_config(path: str = CONFIG) -> dict:
    try:
        with open(path, "rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        return json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        raise ValueError(CONFIG_ERROR) from None


def check() -> int:
    try:
        for port in PORTS.values():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", "/d32/status")
                response = conn.getresponse()
                if response.status != 200 or not json.loads(response.read(65537)).get("ready"):
                    return 1
            finally:
                conn.close()
        return 0
    except (OSError, ValueError, http.client.HTTPException):
        return 1


def main() -> int:
    if sys.argv[1:] == ["--check"]:
        return check()
    if sys.argv[1:]:
        raise SystemExit("Use the installed model bridge command without arguments.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        guard = CredentialGuard()
        settings = Settings(load_config(), guard)
    except (CredentialError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    upstreams = {kind: Door(settings, port, guard) for kind, port in settings.ports.items()}
    attestor = Attestor(settings, upstreams, guard)
    servers = {kind: http.server.ThreadingHTTPServer(("0.0.0.0", port), shared.make_handler(kind, attestor, upstreams[kind]))
               for kind, port in PORTS.items()}
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logging.info("Waiting for authenticated model identity; restore the signed model configuration and enrolled credential if refused.")
    threading.Thread(target=attestor.loop, args=(stop,), daemon=True).start()
    for server in servers.values():
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while not stop.wait(10):
            if not attestor.snapshot()["ready"]:
                logging.info("Waiting for authenticated model identity; check model health and complete enrolment before restarting the full role.")
    finally:
        for server in servers.values():
            server.shutdown()
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
