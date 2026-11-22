"""Portable authenticated model forwarding and attestation, shared by both node launchers.

Only the platform adapters select transports, approved paths, credentials and listeners.
No request body, vector, passage or credential is logged.
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

log = logging.getLogger("d32svc")

ROUTES = {"embed": "/v1/embeddings", "rerank": "/v1/rerank"}
FORWARD_REQUEST_HEADERS = ("content-type", "accept", "user-agent")
FORWARD_RESPONSE_HEADERS = ("content-type",)
MAX_BODY = 8 * 1024 * 1024

class ForgeAuthRejected(ValueError):
    pass

def setting_on(path: str) -> bool:
    try:
        with open(path, encoding="ascii") as f:
            return f.read(16).strip().lower() == "on"
    except OSError:
        return False


class Upstream:
    """Kept-alive connections: verified TLS to the tailnet, or HTTP to the validated local door."""

    POOL = 8

    def __init__(self, s, ctx: ssl.SSLContext | None, port: int) -> None:
        if s.transport == "loopback" and port not in (8081, 8082):
            raise ValueError("d32accel: loopback requires local door ports 8081 and 8082")
        self.s, self.ctx, self.port = s, ctx, port
        self.idle: list[http.client.HTTPConnection] = []
        self.lock = threading.Lock()
        self.host_header = s.host if port == 443 else f"{s.host}:{port}"

    def _connect(self) -> http.client.HTTPConnection:
        if self.s.transport == "tailnet" and self.ctx is None:
            raise RuntimeError("d32accel: tailnet transport requires verified TLS")
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
        if self.s.transport == "loopback":
            conn.sock = raw
        else:
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
                    if k.lower() not in ("authorization", "proxy-authorization"):
                        conn.putheader(k, v)
                conn.putheader("Authorization", "Bearer " + self.s.forge_token)
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

    def __init__(self, s, up: dict[str, Upstream]) -> None:
        self.s, self.up = s, up
        self.lock = threading.Lock()
        self.state = {k: {"verified": False, "reason": "not checked yet", "checked_at": None} for k in ROUTES}
        self.identity = {k: {} for k in ROUTES}
        self.pending_identity = {k: {} for k in ROUTES}
        self.enabled = False
        self.is_enabled = lambda: setting_on(self.s.state_file)
        # GPU_EXT_PROBE_ALWAYS: per kind, the reported identity the last passed long probe stands for (None: none yet)
        self.long_passed = {k: None for k in ROUTES}
        self.rerank_probe = None
        if s.rerank_probe_file:
            try:
                with open(s.rerank_probe_file, encoding="utf-8") as f:  # read before confinement
                    self.rerank_probe = json.load(f)
            except FileNotFoundError:
                log.warning("GPU_EXT: no reranker golden probe at %s (record it: src/model-bridge/record_gpu_probes.py)",
                            s.rerank_probe_file)

    def ok(self, kind: str) -> tuple[bool, str]:
        with self.lock:
            if not self.enabled:
                return False, "GPU_EXT is off"
            st = self.state[kind]
            return st["verified"], st["reason"]

    def invalidate(self, reason="forge-auth-rejected") -> None:
        with self.lock:
            for state in self.state.values():
                state.update(verified=False, reason=reason)
            for kind in ROUTES:
                self.identity[kind] = {}
                self.pending_identity[kind] = {}
                self.long_passed[kind] = None

    def snapshot(self) -> dict:
        with self.lock:
            return {"gpu_ext": "on" if self.enabled else "off", "hub": self.s.host,
                    **{k: dict(v) for k, v in self.state.items()}}

    def diagnostic_snapshot(self) -> dict:
        """Copy only existing checks, with no network, settings reads or inference.

        Legacy /d32/status is retained. This narrow in-process getter has no hub,
        free-form reason, probe input, address or credential. It does not refresh.
        A qualified local owner RPC still has to expose this getter to doctor.
        """
        with self.lock:
            result = {"gpu_ext": "on" if self.enabled else "off"}
            for kind in ROUTES:
                state = self.state[kind]
                result[kind] = {"verified": state["verified"], "checked_at": state["checked_at"],
                                **self.identity[kind]}
                if not state['verified']:
                    result[kind]['reason'] = ('forge-auth-rejected' if
                        state['reason'].startswith('forge-auth-rejected') else 'identity-unconfirmed')
            return result

    def _remember_identity(self, kind, info, model_key, revision_key):
        """Allowlist service-origin fields from the attestor's existing request."""
        expected_model = self.s.embed_model if kind == 'embed' else self.s.rerank_model
        expected_revision = self.s.embed_revision if kind == 'embed' else self.s.rerank_revision
        value = {}
        if (type(expected_model) is str and len(expected_model) <= 128
                and re.fullmatch(r'[A-Za-z0-9._-]+/[A-Za-z0-9._-]+', expected_model)
                and all(part not in ('.', '..') for part in expected_model.split('/'))
                and type(expected_revision) is str and re.fullmatch(r'[0-9a-f]{40}', expected_revision)
                and info.get(model_key) == expected_model and info.get(revision_key) == expected_revision):
            value.update(model_id=expected_model, revision=expected_revision)
            device = info.get('device_kind')
            if type(device) is str and device in ('gpu', 'cpu', 'unknown'):
                value['device_kind'] = device
            if type(info.get('measurement_verified')) is bool:
                value['measurement_verified'] = info['measurement_verified']
        with self.lock:
            self.pending_identity[kind] = value

    def _get_json(self, kind: str, path: str):
        status, _, data = self.up[kind].request("GET", path, None, {"Accept": "application/json"})
        if status == 401:
            raise ForgeAuthRejected("forge-auth-rejected")
        if status != 200:
            raise ValueError(f"GET {path} answered {status}")
        return json.loads(data)

    def _post_json(self, kind: str, path: str, payload: dict):
        body = json.dumps(payload).encode()
        status, _, data = self.up[kind].request("POST", path, body, {"Content-Type": "application/json",
                                                                       "Accept": "application/json"})
        if status == 401:
            raise ForgeAuthRejected("forge-auth-rejected")
        if status != 200:
            raise ValueError(f"POST {path} answered {status}")
        return json.loads(data)

    def _cosine_probe(self, ref: dict, name: str) -> tuple[bool, str]:
        """POST ref's input to the embedder and compare the vector with ref["vector"] by cosine similarity: each GPU
        class runs its own TEI build, so vectors agree closely across cards but never bit for bit. name ("golden
        probe" or "long probe") goes into the reason."""
        s = self.s
        payload = {"input": [str(ref.get("input", s.embed_probe_input))]}
        if ref.get("model") or s.embed_probe_model:
            payload["model"] = str(ref.get("model") or s.embed_probe_model)
        out = self._post_json("embed", ROUTES["embed"], payload)
        vec = out["data"][0]["embedding"]
        want = ref["vector"]
        if (not vec or len(vec) != len(want) or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                or not math.isfinite(v) for v in list(vec) + list(want))):
            return False, f"forge-model-mismatch: embedder {name} is invalid"
        dot = sum(a * b for a, b in zip(vec, want))
        norm = math.sqrt(sum(a * a for a in vec)) * math.sqrt(sum(b * b for b in want))
        cosine = dot / norm if norm else 0.0
        if not cosine >= float(ref.get("min_cosine", 0.999)):
            return False, f"forge-model-mismatch: embedder {name} differs"
        return True, f"model as expected; {name} within tolerance (cosine {cosine:.6f}, {len(vec)} dims)"

    def _long(self, kind: str) -> dict | None:
        """The probe file's long probe of this kind ("rerank" or "embed"), or None."""
        long = self.rerank_probe.get("long") if isinstance(self.rerank_probe, dict) else None
        ref = long.get(kind) if isinstance(long, dict) else None
        return ref if isinstance(ref, dict) else None

    @staticmethod
    def _reported_identity(info: dict, model_key: str, revision_key: str) -> tuple:
        """The model identity a service reports (model, revision, device kind: the fields _remember_identity keeps),
        which a passed long probe stands for until it changes."""
        return tuple(info.get(key) for key in (model_key, revision_key, "device_kind"))

    def _probes_always(self, kind: str, identity: tuple, short: tuple[bool, str] | None, run_long,
                       revision_matched: bool) -> tuple[bool, str]:
        """GPU_EXT_PROBE_ALWAYS, once the short probe (when configured) has passed. The long probe runs at the first
        check after the bridge starts (a start, or the restart a pairing makes), again after GPU_EXT was off or the
        credential was refused, whenever the service reports another identity than at the last long probe that
        passed, and at every check until it passes. Between those the periodic check keeps the short probe: a
        15-passage rerank on every check would compete with live searches for the Forge's GPU (lead ruling F-op2)."""
        with self.lock:
            due = self.long_passed[kind] != identity
            if due:
                self.long_passed[kind] = None
        if due:
            result = run_long()
            if result[0]:
                with self.lock:
                    self.long_passed[kind] = identity
            return result
        note = "; long probe passed for this identity"
        if short is not None:
            return short[0], short[1] + note
        if revision_matched:
            return True, "model and revision as expected" + note
        return True, "model as expected" + note

    def _embed_short(self) -> tuple[bool, str] | None:
        """The short embedder probe: the probe file's "embed", else GPU_EXT_EMBED_PROBE_INPUT and _SHA256; None when
        neither is configured."""
        s = self.s
        ref = self.rerank_probe.get("embed") if isinstance(self.rerank_probe, dict) else None
        if isinstance(ref, dict) and isinstance(ref.get("vector"), list) and ref["vector"]:
            return self._cosine_probe(ref, "golden probe")
        if s.embed_probe_input and s.embed_probe_sha256:
            payload = {"input": [s.embed_probe_input]}
            if s.embed_probe_model:
                payload["model"] = s.embed_probe_model
            out = self._post_json("embed", ROUTES["embed"], payload)
            vec = out["data"][0]["embedding"]
            if not vec or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in vec):
                return False, "forge-model-mismatch: embedder golden probe is invalid"
            digest = hashlib.sha256(struct.pack(f"<{len(vec)}f", *vec)).hexdigest()
            if digest != s.embed_probe_sha256:
                return False, "forge-model-mismatch: embedder golden probe differs"
            return True, f"model as expected; golden probe equal ({len(vec)} dims)"
        return None

    def _embed_long(self) -> tuple[bool, str]:
        ref = self._long("embed")
        if (ref is None or not isinstance(ref.get("input"), str) or not ref["input"]
                or not isinstance(ref.get("vector"), list) or not ref["vector"]):
            return False, "embedder long probe not recorded (GPU_EXT_PROBE_ALWAYS=yes)"
        return self._cosine_probe(ref, "long probe")

    def check_embed(self) -> tuple[bool, str]:
        s = self.s
        info = self._get_json("embed", "/info")
        self._remember_identity('embed', info, 'model_id', 'model_sha')
        if info.get("model_id") != s.embed_model:
            return False, "forge-model-mismatch: embedder model differs"
        always = getattr(s, "probe_always", False) is True
        reported = info.get("model_sha")
        if reported:
            if s.embed_revision and reported != s.embed_revision:
                return False, "forge-model-mismatch: embedder revision differs"
            if s.embed_revision and not always:
                return True, "model and revision as expected"
        short = self._embed_short()
        if always:     # GPU_EXT_PROBE_ALWAYS: a matching revision is necessary, and the probes decide
            if short is not None and not short[0]:
                return short
            return self._probes_always("embed", self._reported_identity(info, "model_id", "model_sha"), short,
                                       self._embed_long, bool(reported and s.embed_revision))
        if short is not None:
            return short
        if s.require_revision:
            return False, "embedder revision neither reported nor proven by a golden probe"
        return True, "model as expected; revision NOT checked (GPU_EXT_REQUIRE_REVISION=no)"

    def _rerank_short(self) -> tuple[bool, str] | None:
        """The short reranker probe (the probe file's top level), or None without a probe file."""
        p = self.rerank_probe
        if not p:
            return None
        out = self._post_json("rerank", ROUTES["rerank"], {"model": p["model"], "query": p["query"],
                                                           "documents": p["documents"],
                                                           "top_n": len(p["documents"])})
        got = [(int(r["index"]), float(r["relevance_score"])) for r in out["results"]]
        want = [(int(i), float(sc)) for i, sc in zip(p["order"], p["scores"])]
        tol = float(p.get("tolerance", 1e-4))
        if len(p["order"]) != len(p["scores"]) or [i for i, _ in got] != [i for i, _ in want]:
            return False, "forge-model-mismatch: reranker golden probe order differs"
        worst = max((abs(a - b) for (_, a), (_, b) in zip(got, want)), default=0.0)
        if not worst <= tol or any(not math.isfinite(a) for _, a in got):
            return False, "forge-model-mismatch: reranker golden probe score differs"
        return True, f"model as expected; golden probe equal (max diff {worst:.1e})"

    def _rerank_long(self) -> tuple[bool, str]:
        p = self._long("rerank")
        if (p is None or not isinstance(p.get("documents"), list) or not p["documents"]
                or not isinstance(p.get("order"), list) or not isinstance(p.get("scores"), list)):
            return False, "reranker long probe not recorded (GPU_EXT_PROBE_ALWAYS=yes)"
        docs = p["documents"]
        out = self._post_json("rerank", ROUTES["rerank"], {"model": p["model"], "query": p["query"],
                                                           "documents": docs, "top_n": len(docs)})
        # passage by passage: two near-equal scores may swap places between card generations
        got = {int(r["index"]): float(r["relevance_score"]) for r in out["results"]}
        want = {int(i): float(sc) for i, sc in zip(p["order"], p["scores"])}
        every = set(range(len(docs)))
        if (len(out["results"]) != len(docs) or len(p["order"]) != len(p["scores"])
                or set(got) != every or set(want) != every):
            return False, "forge-model-mismatch: reranker long probe answer is incomplete"
        worst = max(abs(got[i] - want[i]) for i in every)
        # max() skips a NaN that is not first, so every score is also checked for finiteness
        if not worst <= float(p.get("tolerance", 0.005)) or any(
                not math.isfinite(v) for v in (*got.values(), *want.values())):
            return False, "forge-model-mismatch: reranker long probe score differs"
        return True, f"model as expected; long probe within tolerance (max diff {worst:.1e}, {len(docs)} passages)"

    def check_rerank(self) -> tuple[bool, str]:
        s = self.s
        health = self._get_json("rerank", "/health")
        self._remember_identity('rerank', health, 'model', 'revision')
        if health.get("status") != "ok":
            return False, "forge-model-mismatch: reranker is not healthy"
        if health.get("model") != s.rerank_model:
            return False, "forge-model-mismatch: reranker model differs"
        always = getattr(s, "probe_always", False) is True
        reported = health.get("revision")
        if reported and s.rerank_revision:
            if reported != s.rerank_revision:
                return False, "forge-model-mismatch: reranker revision differs"
            if not always:
                return True, "model and revision as expected"
        short = self._rerank_short()
        if always:     # GPU_EXT_PROBE_ALWAYS: a matching revision is necessary, and the probes decide
            if short is not None and not short[0]:
                return short
            return self._probes_always("rerank", self._reported_identity(health, "model", "revision"), short,
                                       self._rerank_long, bool(reported and s.rerank_revision))
        if short is not None:
            return short
        if s.require_revision:
            return False, "reranker revision neither reported nor proven by a golden probe"
        return True, "model as expected; revision NOT checked (GPU_EXT_REQUIRE_REVISION=no)"

    def run_once(self) -> None:
        enabled = self.is_enabled()
        results = {}
        for kind, fn in (("embed", self.check_embed), ("rerank", self.check_rerank)):
            with self.lock:
                self.pending_identity[kind] = {}
                if not enabled:  # the long probes run again at the next check with GPU_EXT on
                    self.long_passed[kind] = None
            if not enabled:
                results[kind] = (False, "GPU_EXT is off")
                continue
            t0 = time.monotonic()
            try:
                ok, reason = fn()
            except ForgeAuthRejected:
                ok, reason = False, "forge-auth-rejected"
            except Exception as exc:  # unreachable hub, TLS failure, bad JSON: not verified
                ok, reason = False, f"check failed: {type(exc).__name__}"
            results[kind] = (ok, reason + f" [{(time.monotonic() - t0) * 1000:.0f} ms]")
        with self.lock:
            changed = enabled != self.enabled
            self.enabled = enabled
            for kind, (ok, reason) in results.items():
                st = self.state[kind]
                if st["verified"] != ok or changed:
                    log.warning("GPU_EXT %s: %s (%s)", kind, "VERIFIED" if ok else "REFUSED", reason)
                st.update(verified=ok, reason=reason, checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                self.identity[kind] = dict(self.pending_identity[kind])

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
            try:
                n = int(length)
            except (TypeError, ValueError):
                self._json(400, {"error": "invalid Content-Length"})
                self.close_connection = True
                return
            if n < 0 or n > MAX_BODY:
                self._json(413, {"error": "request too large"})
                self.close_connection = True
                return
            body = self.rfile.read(n)
            if len(body) != n:
                self._json(400, {"error": "incomplete request"})
                self.close_connection = True
                return
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
            if status == 401:
                if hasattr(att, "invalidate"):
                    att.invalidate()
                self._json(401, {"error": "forge-auth-rejected"})
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
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self.close_connection = True
                return
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
