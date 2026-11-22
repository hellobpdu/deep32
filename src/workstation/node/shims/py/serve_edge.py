"""deep32-gateway launcher for Deep32 First light (PYTHON-ON-OPENBSD 7.2-7.3, HARDENING 5.8, the final design 0 and C6).

rc.d/d32edge runs, as root:
  d32-sockexec -u _d32edge -e /etc/d32/edge.env -l edge=unix:/var/run/d32/edge.sock:root:_d32users:0660 \
      [-l front=tcp:127.0.0.1:8766] \
      -- /usr/local/d32/venv/deep32-gateway/bin/python3.14 -I -B -X utf8 /usr/local/d32/node/shims/serve_edge.py

Listeners (the final design 0: one pre-bound UNIX socket per caller class, no TCP at First light):
  edge   /var/run/d32/edge.sock 0660 root:_d32users: the desktop user's local apps (class "local", every tool)
  front  127.0.0.1:8766, ONLY while /etc/d32/state/edge_front is "on": the relayd(8) TLS front's backend for remote
         callers. Requests on it pass the RemoteGate below (queries only), and the pledge gains "inet" (accept(2) on
         an AF_INET socket needs it); pf keeps _d32edge from opening any connection of its own.
  (edge-airgap.sock is NOT created yet: its class needs the edge's class layer, the final design C5; until then an
   air-gapped caller could reach a GPU_EXT-backed search, which would break the air-gap promise of ISO 10.5.)

deep32-gateway reaches deep32-engine through its public API with no code change (PYTHON-ON-OPENBSD 7.2):
  create_app(Config.from_env(), core=Core(url, token, transport=AsyncHTTPTransport(uds=core.sock, limits=...)))
The limits are Core's own (8 connections, 4 kept alive, 4 s expiry): httpx ignores the client's limits when it is
given a transport, so they are repeated on the transport to keep production's connection behaviour.

Environment file: RAG_CORE_URL (http://localhost: only Host: comes from it), RAG_CORE_API_TOKEN_FILE (the engine
token's own file, root:_d32edge 0440, read before confinement), TAILSCALE_DNS_NAME (the node's name; the DNS-rebinding allow-list), RAG_MCP_TOKENS_FILE, optional RAG_MCP_ALLOWED_IPS
(leave it EMPTY on the node: a UNIX-socket caller has no address and would get 403). Options:
  D32_EDGE_CODE   code tree (default /usr/local/d32/deep32-gateway/current)
  D32_CORE_SOCK   deep32-engine's socket (default /var/run/d32/core.sock)

Update 15: a longer wait for the status behind the opt-in note (OPT_IN_STATUS_TIMEOUT below), set here at start,
launcher-side: rag_edge.py is not changed. It goes with the longer status probe limit in serve_core_ship.py.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import stat
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

import d32svc  # noqa: E402

REQUIRED = ("RAG_CORE_URL", "RAG_CORE_API_TOKEN_FILE", "TAILSCALE_DNS_NAME")
ENGINE_TOKEN_FILE = "/etc/d32/private/engine-client.token"  # nosec B105
# (uvicorn's loop and protocol classes are imported by Config.load(), before the start-up and so before pledge;
# production's edge venv has neither uvloop nor httptools, so uvicorn's "auto" picks asyncio and h11 there too)
PRE_IMPORT = ("rag_edge", "httpx", "httpcore._async.connection_pool", "httpcore._backends.anyio", "anyio",
              "anyio._backends._asyncio", "mcp.server.streamable_http", "mcp.server.streamable_http_manager",
              "pydantic", "starlette.responses", "starlette.routing", "uvicorn.lifespan.on",
              "encodings.idna", "encodings.unicode_escape", "json.decoder", "email.parser", "email.feedparser")

# What a remote caller may ask (ISO-DESIGN 13: "remotely it can only make queries"): MCP's own handshake and
# listing, and the 11 read tools. Everything else answers JSON-RPC -32601, as ISO-DESIGN 3.4 specifies.
REMOTE_METHODS = frozenset({"initialize", "notifications/initialized", "ping", "tools/list", "tools/call"})
REMOTE_TOOLS = frozenset({"search_rag", "search_many", "get_section", "get_outline", "list_documents",
                          "verify_quote", "grep_corpus", "lookup_rfc", "lookup_id", "find_command", "rag_status"})
MAX_BODY = 256 * 1024  # deep32-gateway's own max_request_body_size

log = logging.getLogger("d32svc")


def publication_dns(root=Path('/'), *, active=True, expected_gid=None):
    """Load the non-secret service-readable name before pledge/unveil.

    sockexec has already dropped to _d32edge: the root-only journal cannot be
    read here. The controller materialises this separate 0440 file only while
    publication is requested; the signed credential-bearing settings stay put.
    """
    if not active:
        return None
    directory = Path(root) / 'etc/d32/private'
    path = directory / 'publication-dns'
    try:
        for part in (path, *path.parents):
            if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
                raise ValueError()
        if not path.exists():
            return None
        if os.name == 'posix':
            import grp
            gid = grp.getgrnam('_d32edge').gr_gid if expected_gid is None else expected_gid
            parent = directory.stat()
            if parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) != 0o711:
                raise ValueError()
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode):
                raise ValueError()
            if os.name == 'posix' and (meta.st_uid != 0 or meta.st_gid != gid or stat.S_IMODE(meta.st_mode) != 0o440):
                raise ValueError()
            raw = stream.read(256)
        if not raw.endswith(b'\n') or raw.count(b'\n') != 1:
            raise ValueError()
        host = raw[:-1].decode('ascii')
        if (len(host) > 253 or not host.endswith('.ts.net') or len(host.split('.')) < 4
                or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                       for label in host.split('.'))):
            raise ValueError()
        return host
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise SystemExit('deep32-gateway: publication state is invalid; run d32-publish resume') from None


def load_mcp_tokens(path, *, expected_gid=None):
    """Read the dedicated allow-list before confinement; never expose its values."""
    message = 'deep32-gateway: paired credentials unavailable; run gateway enrolment'
    try:
        path = Path(os.path.abspath(path))
        for part in (path, *path.parents):
            if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
                raise ValueError()
        if os.name == 'posix':
            import grp
            gid = grp.getgrnam('_d32edge').gr_gid if expected_gid is None else expected_gid
            parent = path.parent.stat()
            if parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) != 0o711:
                raise ValueError()
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode):
                raise ValueError()
            if os.name == 'posix' and (meta.st_uid != 0 or meta.st_gid != gid or stat.S_IMODE(meta.st_mode) != 0o440):
                raise ValueError()
            raw = stream.read(65537)
        if len(raw) > 65536 or not raw.endswith(b'\n'):
            raise ValueError()
        values = raw.decode('ascii').splitlines()
        if not values or len(values) != len(set(values)) or any(not re.fullmatch('[0-9a-f]{64}', v) for v in values):
            raise ValueError()
        return ','.join(values)
    except (OSError, ValueError, KeyError, UnicodeError):
        raise SystemExit(message) from None

# Update 15: how long deep32-gateway waits for deep32-engine's GET /v2/status when an answer without hits wants to name the
# opt-in collections the search left out (rag_edge.opt_in_unsearched). deep32-gateway's own 3 s covers production's slowest
# status: probes of 2 s plus 0.5 s grace. The node's deep32-engine now lets a probe take 6 s (serve_core_ship.py
# STATUS_PROBE_TIMEOUT), so its slowest status is 6.5 s; at 3 s a no-hits answer would lose that note whenever a
# status takes longer than 3 s, as it can over a slow link. 6 + 0.5 + the same 0.5 s margin; change both together.
OPT_IN_STATUS_TIMEOUT = 7.0


def longer_opt_in_status(edge) -> str:
    """Raise rag_edge.OPT_IN_STATUS_TIMEOUT, which opt_in_unsearched reads at every call, to OPT_IN_STATUS_TIMEOUT.
    Returns the line for the start-up log. A deep32-gateway without that number, or with one that is not shorter, is left
    as it is, and the line says so."""
    was = getattr(edge, "OPT_IN_STATUS_TIMEOUT", None)
    if isinstance(was, bool) or not isinstance(was, (int, float)) or was >= OPT_IN_STATUS_TIMEOUT:
        return f"opt-in note status limit: UNCHANGED (rag_edge.OPT_IN_STATUS_TIMEOUT is {was!r})"
    edge.OPT_IN_STATUS_TIMEOUT = OPT_IN_STATUS_TIMEOUT
    return f"opt-in note status limit: {OPT_IN_STATUS_TIMEOUT:g} s (deep32-gateway default: {was:g} s)"


class RemoteGate:
    """Pure-ASGI layer for the front listener only (scope["server"] == the front's address). The relayd front
    already lets only POST /mcp and GET /health through; this checks the JSON-RPC inside, so even a request that
    reached the port some other way gets only the read tools. Requests on the UNIX sockets pass untouched."""

    def __init__(self, app, front: tuple[str, int]) -> None:
        self.app = app
        self.front = front

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or tuple(scope.get("server") or ()) != self.front:
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        if (method, path) == ("GET", "/health"):
            await self.app(scope, receive, send)
            return
        if (method, path) != ("POST", "/mcp"):
            await self._plain(send, 404, b"not found")
            log.warning("remote: refused %s (not a query path)", method)
            return
        chunks, size = [], 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body = msg.get("body", b"")
            size += len(body)
            if size > MAX_BODY:
                await self._plain(send, 413, b"request too large")
                return
            chunks.append(body)
            if not msg.get("more_body"):
                break
        raw = b"".join(chunks)
        verdict, rid = self.check(raw)
        if verdict:
            log.warning("remote: refused a JSON-RPC call (%s)", verdict)
            payload = {"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32601, "message": "Method not available to remote callers"}}
            await self._json(send, 200, payload)
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    def check(raw: bytes) -> tuple[str, object]:
        """('', id) to pass, or (category, id) to refuse. Unparseable bodies pass: deep32-gateway answers -32700."""
        try:
            msg = json.loads(raw)
        except ValueError:
            return "", None
        if isinstance(msg, list):
            return "batch", None
        if not isinstance(msg, dict):
            return "", None
        rid = msg.get("id")
        rid = rid if isinstance(rid, (str, int)) and not isinstance(rid, bool) else None
        method = msg.get("method")
        if not isinstance(method, str):
            return "not a request", rid
        if method not in REMOTE_METHODS:
            return "method", rid
        if method == "tools/call":
            params = msg.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            if name not in REMOTE_TOOLS:
                return "tool", rid
        return "", rid

    @staticmethod
    async def _plain(send, status: int, body: bytes) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _json(send, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def main() -> None:
    svc = d32svc.boot("deep32-gateway", required=REQUIRED)
    if "edge" not in svc.listeners:
        raise SystemExit("deep32-gateway: no 'edge' listener inherited (start it through rc.d/d32edge)")
    code = d32svc.realpath_dir(svc.opts.get("D32_EDGE_CODE", "/usr/local/d32/deep32-gateway/current"))
    core_sock = svc.opts.get("D32_CORE_SOCK", "/var/run/d32/core.sock")
    sys.path.insert(0, code)
    os.chdir(code)

    import importlib

    for name in PRE_IMPORT:
        importlib.import_module(name)
    import httpx
    import uvicorn
    import rag_edge
    from rag_edge import Config, Core, create_app

    log.warning("node layer: %s", longer_opt_in_status(rag_edge))
    credential_path = os.environ.get('RAG_MCP_TOKENS_FILE', '')
    if credential_path != '/etc/d32/private/mcp-tokens' or 'RAG_MCP_TOKENS' in os.environ:
        raise SystemExit('deep32-gateway: paired credentials unavailable; run gateway enrolment')
    # Config's old interface takes a string. Supply it only in this private
    # in-process mapping; no bearer is added to the process environment.
    config_env = dict(os.environ)
    published_host = publication_dns(active='front' in svc.listeners)
    if published_host:
        config_env['TAILSCALE_DNS_NAME'] = published_host
    config_env['RAG_MCP_TOKENS'] = load_mcp_tokens(credential_path)
    engine_path = config_env.pop('RAG_CORE_API_TOKEN_FILE', '')
    if engine_path != ENGINE_TOKEN_FILE or 'RAG_CORE_API_TOKEN' in os.environ:
        raise SystemExit('deep32-gateway: engine credential unavailable; run gateway enrolment')
    config_env['RAG_CORE_API_TOKEN'] = d32svc.read_credential(engine_path, '_d32edge', 'deep32-gateway')
    cfg = Config.from_env(config_env)
    config_env.clear()
    transport = httpx.AsyncHTTPTransport(
        uds=core_sock,
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=4.0))
    app = create_app(cfg, core=Core(cfg.core_url, cfg.core_token, transport=transport))

    front = svc.listeners.get("front")
    promises = "stdio rpath unix prot_exec"
    if front is not None:
        addr = front.getsockname()
        app = RemoteGate(app, (str(addr[0]), int(addr[1])))
        promises += " inet"
        log.warning("deep32-gateway: front listener %s:%d (relayd backend, queries only)", addr[0], addr[1])

    unveils = d32svc.base_unveils() + [(code, "r"), (core_sock, "rw")]
    # the edge allow-list's symlinks (d32-syspick.pth) point into the system site-packages
    for path in sys.path:
        if path and os.path.isdir(path):
            unveils.append((d32svc.realpath_dir(path), "r"))

    # production: `uvicorn app:app --host 127.0.0.1 --port 8766`; Config's defaults are the CLI's
    config = uvicorn.Config(app)
    server = d32svc.make_server(config, svc, lambda: d32svc.confine(svc, unveils, promises))
    server.run(sockets=[svc.listeners["edge"]] + ([front] if front is not None else []))


if __name__ == "__main__":
    main()
