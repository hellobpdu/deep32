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
import socket
import socketserver
import ssl
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

import d32svc  # noqa: E402

log = logging.getLogger("d32svc")

ROUTES = {"embed": "/v1/embeddings", "rerank": "/v1/rerank"}
FORWARD_REQUEST_HEADERS = ("content-type", "accept", "authorization", "user-agent")
FORWARD_RESPONSE_HEADERS = ("content-type",)
MAX_BODY = 8 * 1024 * 1024


def _yes(value: str) -> bool:
    return value.strip().lower() in ("1", "yes", "on", "true")


class Settings:
    def __init__(self, env: dict[str, str]) -> None:
        self.host = env.get("GPU_EXT_HOST", "")
        self.addr = env.get("GPU_EXT_ADDR", "")
        self.ports = {"embed": int(env.get("GPU_EXT_EMBED_PORT", "443")),
                      "rerank": int(env.get("GPU_EXT_RERANK_PORT", "8443"))}
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


def setting_on(path: str) -> bool:
    try:
        with open(path, encoding="ascii") as f:
            return f.read(16).strip().lower() == "on"
    except OSError:
        return False


class Upstream:
    """A small pool of kept-alive TLS connections to one hub port, connecting by address, verifying by name."""

    POOL = 8

    def __init__(self, s: Settings, ctx: ssl.SSLContext, port: int) -> None:
        self.s, self.ctx, self.port = s, ctx, port
        self.idle: list[http.client.HTTPConnection] = []
        self.lock = threading.Lock()
        self.host_header = s.host if port == 443 else f"{s.host}:{port}"

    def _connect(self) -> http.client.HTTPConnection:
        conn = http.client.HTTPConnection(self.s.addr, self.port, timeout=self.s.read_timeout)
        conn.auto_open = 0  # never a silent plain-text reconnect to the hub: a closed connection is an error
        # a numeric address only: no resolver, so no /etc/resolv.conf read and no "dns" promise after pledge
        family, stype, proto, _, sockaddr = socket.getaddrinfo(self.s.addr, self.port, type=socket.SOCK_STREAM,
                                                               flags=socket.AI_NUMERICHOST)[0]
        raw = socket.socket(family, stype, proto)
        raw.settimeout(self.s.connect_timeout)
        try:
            raw.connect(sockaddr)
        except BaseException:
            raw.close()
            raise
        raw.settimeout(self.s.read_timeout)
        conn.sock = self.ctx.wrap_socket(raw, server_hostname=self.s.host)
        return conn

    def _take(self) -> tuple[http.client.HTTPConnection, bool]:
        with self.lock:
            if self.idle:
                return self.idle.pop(), False
        return self._connect(), True

    def _give(self, conn: http.client.HTTPConnection) -> None:
        with self.lock:
            if len(self.idle) < self.POOL:
                self.idle.append(conn)
                return
        conn.close()

    def request(self, method: str, path: str, body: bytes | None, headers: dict[str, str]):
        """(status, headers, body). A reused connection the hub closed is retried once on a new one; every call
        here is a read (an embedding or a ranking), so repeating it changes nothing."""
        for attempt in (0, 1):
            conn, fresh = self._take()
            try:
                conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
                conn.putheader("Host", self.host_header)
                for k, v in headers.items():
                    conn.putheader(k, v)
                if body is not None:
                    conn.putheader("Content-Length", str(len(body)))
                conn.endheaders(body)
                resp = conn.getresponse()
                data = resp.read()
            except (http.client.RemoteDisconnected, http.client.NotConnected, BrokenPipeError,
                    ConnectionResetError) as exc:
                conn.close()
                if fresh or attempt:
                    raise
                log.info("hub closed a kept-alive connection (%s); retrying once", type(exc).__name__)
                continue
            except BaseException:
                conn.close()
                raise
            if resp.will_close or conn.sock is None:
                conn.close()
            else:
                self._give(conn)
            return resp.status, resp.headers, data
        raise RuntimeError("unreachable")


class Attestor:
    """The identity check, before first use and every `recheck` seconds after."""

    def __init__(self, s: Settings, up: dict[str, Upstream]) -> None:
        self.s, self.up = s, up
        self.lock = threading.Lock()
        self.state = {k: {"verified": False, "reason": "not checked yet", "checked_at": None} for k in ROUTES}
        self.enabled = False
        self.rerank_probe = None
        if s.rerank_probe_file:
            try:
                with open(s.rerank_probe_file, encoding="utf-8") as f:  # read before confinement
                    self.rerank_probe = json.load(f)
            except FileNotFoundError:
                log.warning("GPU_EXT: no reranker golden probe at %s (record it: tools/record_gpu_probes.py)",
                            s.rerank_probe_file)

    def ok(self, kind: str) -> tuple[bool, str]:
        with self.lock:
            if not self.enabled:
                return False, "GPU_EXT is off"
            st = self.state[kind]
            return st["verified"], st["reason"]

    def snapshot(self) -> dict:
        with self.lock:
            return {"gpu_ext": "on" if self.enabled else "off", "hub": self.s.host,
                    **{k: dict(v) for k, v in self.state.items()}}

    def _get_json(self, kind: str, path: str):
        status, _, data = self.up[kind].request("GET", path, None, {"Accept": "application/json"})
        if status != 200:
            raise ValueError(f"GET {path} answered {status}")
        return json.loads(data)

    def _post_json(self, kind: str, path: str, payload: dict):
        body = json.dumps(payload).encode()
        status, _, data = self.up[kind].request("POST", path, body, {"Content-Type": "application/json",
                                                                       "Accept": "application/json"})
        if status != 200:
            raise ValueError(f"POST {path} answered {status}")
        return json.loads(data)

    def check_embed(self) -> tuple[bool, str]:
        s = self.s
        info = self._get_json("embed", "/info")
        if info.get("model_id") != s.embed_model:
            return False, f"embedder model {info.get('model_id')!r}, expected {s.embed_model!r}"
        reported = info.get("model_sha")
        if reported:
            if s.embed_revision and reported != s.embed_revision:
                return False, f"embedder revision {reported!r}, expected {s.embed_revision!r}"
            if s.embed_revision:
                return True, f"model and revision {reported[:12]} as expected"
        if s.embed_probe_input and s.embed_probe_sha256:
            payload = {"input": [s.embed_probe_input]}
            if s.embed_probe_model:
                payload["model"] = s.embed_probe_model
            out = self._post_json("embed", ROUTES["embed"], payload)
            vec = out["data"][0]["embedding"]
            digest = hashlib.sha256(struct.pack(f"<{len(vec)}f", *vec)).hexdigest()
            if digest != s.embed_probe_sha256:
                return False, f"embedder golden probe differs (sha256 {digest[:16]}...)"
            return True, f"model as expected; golden probe equal ({len(vec)} dims)"
        if s.require_revision:
            return False, "embedder revision neither reported nor proven by a golden probe"
        return True, "model as expected; revision NOT checked (GPU_EXT_REQUIRE_REVISION=no)"

    def check_rerank(self) -> tuple[bool, str]:
        s = self.s
        health = self._get_json("rerank", "/health")
        if health.get("status") != "ok":
            return False, f"reranker status {health.get('status')!r}"
        if health.get("model") != s.rerank_model:
            return False, f"reranker model {health.get('model')!r}, expected {s.rerank_model!r}"
        reported = health.get("revision")
        if reported and s.rerank_revision:
            if reported != s.rerank_revision:
                return False, f"reranker revision {reported!r}, expected {s.rerank_revision!r}"
            return True, f"model and revision {reported[:12]} as expected"
        p = self.rerank_probe
        if p:
            out = self._post_json("rerank", ROUTES["rerank"], {"model": p["model"], "query": p["query"],
                                                               "documents": p["documents"],
                                                               "top_n": len(p["documents"])})
            got = [(int(r["index"]), float(r["relevance_score"])) for r in out["results"]]
            want = [(int(i), float(sc)) for i, sc in zip(p["order"], p["scores"])]
            tol = float(p.get("tolerance", 1e-4))
            if [i for i, _ in got] != [i for i, _ in want]:
                return False, "reranker golden probe: a different order"
            worst = max((abs(a - b) for (_, a), (_, b) in zip(got, want)), default=0.0)
            if not worst <= tol or any(math.isnan(a) for _, a in got):
                return False, f"reranker golden probe: a score differs by {worst:.2e} (tolerance {tol:.0e})"
            return True, f"model as expected; golden probe equal (max diff {worst:.1e})"
        if s.require_revision:
            return False, "reranker revision neither reported nor proven by a golden probe"
        return True, "model as expected; revision NOT checked (GPU_EXT_REQUIRE_REVISION=no)"

    def run_once(self) -> None:
        enabled = setting_on(self.s.state_file)
        results = {}
        for kind, fn in (("embed", self.check_embed), ("rerank", self.check_rerank)):
            if not enabled:
                results[kind] = (False, "GPU_EXT is off")
                continue
            t0 = time.monotonic()
            try:
                ok, reason = fn()
            except Exception as exc:  # unreachable hub, TLS failure, bad JSON: not verified
                ok, reason = False, f"check failed: {type(exc).__name__}: {str(exc)[:160]}"
            results[kind] = (ok, reason + f" [{(time.monotonic() - t0) * 1000:.0f} ms]")
        with self.lock:
            changed = enabled != self.enabled
            self.enabled = enabled
            for kind, (ok, reason) in results.items():
                st = self.state[kind]
                if st["verified"] != ok or changed:
                    log.warning("GPU_EXT %s: %s (%s)", kind, "VERIFIED" if ok else "REFUSED", reason)
                st.update(verified=ok, reason=reason, checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.run_once()
            stop.wait(self.s.recheck)


def make_handler(kind: str, att: Attestor, up: Upstream):
    route = ROUTES[kind]

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "d32accel"
        sys_version = ""
        timeout = 330  # idle kept-alive connection from deep32-engine (it keeps them up to 300 s)

        def address_string(self) -> str:
            return "local"

        def log_message(self, fmt, *args):  # never the request line's query or any body
            pass

        def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode())

        def do_GET(self):
            if self.path == "/d32/status":
                self._json(200, att.snapshot())
            elif self.path == route:
                self._json(405, {"error": "method not allowed"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            t0 = time.monotonic()
            if self.path != route:
                self._drain()
                self._json(404, {"error": "not found"})
                return
            length = self.headers.get("Content-Length")
            if length is None or self.headers.get("Transfer-Encoding"):
                self._json(411, {"error": "Content-Length required"})
                self.close_connection = True
                return
            n = int(length)
            if n < 0 or n > MAX_BODY:
                self._json(413, {"error": "request too large"})
                self.close_connection = True
                return
            body = self.rfile.read(n)
            ok, reason = att.ok(kind)
            if not ok:
                self._json(503, {"error": "GPU_EXT not available", "reason": reason})
                log.info("%s %s -> 503 (not verified) in %.0f ms", kind, route, (time.monotonic() - t0) * 1000)
                return
            headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARD_REQUEST_HEADERS}
            try:
                status, rheaders, data = up.request("POST", route, body, headers)
            except (socket.timeout, TimeoutError):
                self._json(504, {"error": "hub timed out"})
                return
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                self._json(502, {"error": f"hub unreachable: {type(exc).__name__}"})
                return
            self.send_response(status)
            for k in FORWARD_RESPONSE_HEADERS:
                v = rheaders.get(k)
                if v:
                    self.send_header(k.title(), v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            log.info("%s %s -> %d, %d bytes in %.0f ms", kind, route, status, len(data),
                     (time.monotonic() - t0) * 1000)

        def _drain(self):
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= MAX_BODY:
                self.rfile.read(n)

    return Handler


class Server(socketserver.ThreadingMixIn, socketserver.BaseServer):
    """A threaded HTTP server on an inherited, already listening socket (UNIX or TCP)."""
    daemon_threads = True

    def __init__(self, sock: socket.socket, handler) -> None:
        super().__init__(sock.getsockname(), handler)
        self.socket = sock
        sock.setblocking(True)

    def fileno(self) -> int:
        return self.socket.fileno()

    def get_request(self):
        conn, addr = self.socket.accept()
        return conn, addr or ("local", 0)

    def shutdown_request(self, request):
        try:
            request.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        request.close()

    def close_request(self, request):
        request.close()


def main() -> None:
    svc = d32svc.boot("d32accel")
    s = Settings(dict(os.environ))
    missing = [k for k in ROUTES if k not in svc.listeners]
    if missing:
        raise SystemExit(f"d32accel: listeners {missing} not inherited (start it through rc.d/d32accel)")
    if not s.addr:  # resolve once, before confinement (no "dns" promise afterwards)
        s.addr = socket.getaddrinfo(s.host, s.ports["embed"], socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
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
