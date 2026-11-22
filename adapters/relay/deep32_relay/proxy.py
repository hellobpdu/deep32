"""A bounded, revocable MCP bridge; no generic HTTP or network forwarding."""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

TOOLS = frozenset(("rag_status", "search_rag", "search_many", "get_section",
    "get_outline", "lookup_id", "lookup_rfc", "find_command", "grep_corpus",
    "list_documents", "verify_quote"))
METHODS = frozenset(("initialize", "notifications/initialized", "tools/list", "tools/call"))
MAX_REQUEST = 65536
MAX_RESPONSE = 1048576
IN_FLIGHT = 4           # tool calls of one turn that may be at the Workstation at once (its own limit is 4 as well)
CALL_BUDGET = 300       # MCP requests of one turn
# The session endpoint accepts more connections than calls may be in flight, so that a call over IN_FLIGHT can be
# read and answered with a text the agent can act on; a connection over this number is closed unanswered.
CONNECTIONS = 8
# What the agent reads when the relay itself does not run one of its tool calls: the fixed reason, and what to do.
REFUSALS = {
    "request-limit": "Deep32 relay: at most 4 tool calls can run at once, and this one was not run. "
                     "Send this call again after one of the others has returned.",
    "call-budget": "Deep32 relay: this answer has used all 300 of its tool calls, so this call was not run and no "
                   "later one will be. Write the answer from what you have and say which points are still open.",
}


# The tool calls of a turn are kept in memory, until the turn ends, for its answer check and answer record: at most
# CAPTURE_MAX characters of result text in all; past that a result keeps only its first CAPTURE_HEAD characters.
CAPTURE_MAX = 8 * 1024 * 1024
CAPTURE_HEAD = 4096


class ProxyError(ValueError):
    """Only fixed, non-secret reason codes cross this boundary."""


def keep_call(cap, body, result):
    """Keep one answered tools/call in the form answer_check.py reads: {"tool", "arguments", "result",
    "is_error"}, the result being the text parts of the MCP result joined by line feeds (another kind of part is
    written as [its type]), or the error's message. Never raises: a call that cannot be kept is left out."""
    try:
        params = body.get("params", {})
        failed = "error" in result
        if failed:
            error = result["error"]
            text = str(error.get("message", "")) if isinstance(error, dict) else ""
        else:
            value = result["result"]
            blocks = value.get("content") if isinstance(value, dict) else None
            text = "\n".join(
                block["text"] if isinstance(block, dict) and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                else "[%s]" % (block.get("type") if isinstance(block, dict) else "unknown")
                for block in (blocks if isinstance(blocks, list) else ()))
            failed = isinstance(value, dict) and value.get("isError") is True
        event = {"tool": params.get("name"), "arguments": params.get("arguments", {}), "result": text,
                 "is_error": failed}
        if cap.captured + len(text) > CAPTURE_MAX:
            event["result"], event["truncated"] = text[:CAPTURE_HEAD], True
        cap.captured += len(event["result"])
        cap.events.append(event)
        return True
    except Exception:
        return False


def refusal(reason, request):
    """The answer to a tools/call that the relay refuses for a reason the agent can act on: an MCP tool result
    marked isError whose text says what to do. None for any other refusal, which stays a bare HTTP 400."""
    if (reason not in REFUSALS or not isinstance(request, dict) or request.get("method") != "tools/call"
            or type(request.get("id")) not in (str, int)):
        return None
    return encoded({"jsonrpc": "2.0", "id": request["id"], "result": {
        "content": [{"type": "text", "text": REFUSALS[reason]}], "isError": True}}, MAX_REQUEST)


def encoded(value, limit):
    from .messages import dumps
    try:
        data = dumps(value, limit)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProxyError("invalid-json") from None
    if len(data) > limit:
        raise ProxyError("message-limit")
    return data


def token(value, limit):
    return (isinstance(value, str) and 0 < len(value) <= limit
            and all(33 <= ord(c) <= 126 for c in value))


def request_ok(body):
    if not isinstance(body, dict) or set(body) - {"jsonrpc", "id", "method", "params"}:
        raise ProxyError("method-unavailable")
    if body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str) or body["method"] not in METHODS:
        raise ProxyError("method-unavailable")
    method = body["method"]
    params = body.get("params", {})
    if not isinstance(params, dict):
        raise ProxyError("invalid-params")
    if method == "notifications/initialized":
        if "id" in body or set(params):
            raise ProxyError("invalid-notification")
    elif ("id" not in body or type(body["id"]) not in (str, int)
          or isinstance(body["id"], str) and len(body["id"].encode("utf-8")) > 128
          or type(body["id"]) is int and not -(2**63) <= body["id"] < 2**63):
        raise ProxyError("invalid-request-id")
    if method == "tools/call":
        if (set(params) - {"name", "arguments", "_meta"} or not isinstance(params.get("name"), str)
                or params["name"] not in TOOLS):
            raise ProxyError("tool-unavailable")
        if not isinstance(params.get("arguments", {}), dict):
            raise ProxyError("invalid-arguments")
    return method


@dataclass(repr=False)
class Capability:
    session: str
    deadline: float
    bearer: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    active: bool = False
    inflight: int = 0
    calls: int = 0
    refused: int = 0            # calls not run because of IN_FLIGHT or CALL_BUDGET (the turn log counts them)
    capture: bool = False       # keep this turn's tool calls (the supervisor sets it for the answer check or record)
    events: list = field(default_factory=list, repr=False)
    captured: int = 0           # characters of result text kept so far
    mcp_session_id: str | None = None
    protocol_version: str | None = None
    initialised: bool = False
    tools_verified: bool = False
    generation: int = 0


class Gateway:
    """Transport seam used only by a verified turn's private MCP endpoint.

    dispatch(session, envelope, deadline) must honour the monotonic deadline and
    cancellation via revoke. It is an owned D32U channel-2 transport, never a URL.
    """
    def __init__(self, dispatch: Callable, *, clock=time.monotonic,
                 guarded_dispatch=None, on_revoke=None):
        self.dispatch = dispatch
        self.guarded_dispatch = guarded_dispatch
        self.on_revoke = on_revoke or (lambda session: None)
        self.clock = clock
        self.lock = threading.RLock()
        self.capabilities = {}
        self.pending_revocations = set()

    def grant(self, session, deadline):
        with self.lock:
            if session in self.capabilities or session in self.pending_revocations or deadline <= self.clock():
                raise ProxyError("inactive-turn")
            cap = Capability(session, deadline)
            self.capabilities[session] = cap
            return cap

    def revoke(self, session):
        with self.lock:
            cap = self.capabilities.pop(session, None)
            if cap:
                cap.active = False
                cap.generation += 1
                cap.bearer = ""
                cap.mcp_session_id = None
                cap.tools_verified = False
                self.pending_revocations.add(session)
            retry = session in self.pending_revocations
        # Never acquire the transport lock while holding the capability lock.
        if retry:
            try:
                self.on_revoke(session)
            except Exception:
                raise ProxyError("revocation-failed") from None
            with self.lock:
                self.pending_revocations.discard(session)

    def activate(self, cap):
        with self.lock:
            if (self.capabilities.get(cap.session) is not cap or cap.active
                    or self.clock() >= cap.deadline):
                raise ProxyError("inactive-turn")
            cap.active = True

    def _live(self, cap):
        if (not cap.active or self.capabilities.get(cap.session) is not cap
                or self.clock() >= cap.deadline):
            raise ProxyError("inactive-turn")

    def call(self, cap, body):
        method = request_ok(body)
        # Validate before invoking upstream; serialisation also rejects surrogate
        # code points, non-finite numbers and oversized nested tool arguments.
        with self.lock:
            self._live(cap)
            # Two refusals with their own reasons: the budget lasts for the rest of the turn, the other one passes.
            if cap.calls >= CALL_BUDGET:
                cap.refused += 1
                raise ProxyError("call-budget")
            if cap.inflight >= IN_FLIGHT:
                cap.refused += 1
                raise ProxyError("request-limit")
            if method != "initialize" and not cap.initialised:
                raise ProxyError("not-initialised")
            if method == "tools/call" and not cap.tools_verified:
                raise ProxyError("tools-unverified")
            envelope = {"body": body, "mcp_session_id": cap.mcp_session_id}
            if cap.protocol_version:
                envelope["protocol_version"] = cap.protocol_version
            encoded(envelope, MAX_REQUEST)
            generation = cap.generation
            cap.inflight += 1
            cap.calls += 1
        try:
            def guard():
                with self.lock:
                    self._live(cap)
                    if cap.generation != generation:
                        raise ProxyError("inactive-turn")
                    return True
            if self.guarded_dispatch is not None:
                response = self.guarded_dispatch(cap.session, envelope, cap.deadline, guard)
            else:
                # Pure in-memory stand-ins only. The live transport must use
                # guarded_dispatch and check this guard under its send lock.
                guard()
                response = self.dispatch(cap.session, envelope, cap.deadline)
            with self.lock:
                self._live(cap)
                if generation != cap.generation:
                    raise ProxyError("inactive-turn")
                encoded(response, MAX_RESPONSE)
                if not isinstance(response, dict) or set(response) != {"body", "mcp_session_id", "http_status"}:
                    raise ProxyError("invalid-response")
                sid, status, result = response["mcp_session_id"], response["http_status"], response["body"]
                if sid is not None and not token(sid, 128):
                    raise ProxyError("invalid-session-id")
                if cap.mcp_session_id is not None and sid not in (None, cap.mcp_session_id):
                    raise ProxyError("invalid-session-id")
                if type(status) is not int or status not in (200, 202):
                    raise ProxyError("upstream-unavailable")
                if method == "notifications/initialized":
                    if status != 202 or result is not None:
                        raise ProxyError("invalid-notification-response")
                    cap.mcp_session_id = sid or cap.mcp_session_id
                    return response
                if (status != 200 or not isinstance(result, dict)
                    or result.get("jsonrpc") != "2.0" or type(result.get("id")) is not type(body["id"])
                    or result.get("id") != body["id"]
                    or ("result" in result) == ("error" in result)):
                    raise ProxyError("invalid-response")
                if method == "tools/call" and cap.capture:
                    keep_call(cap, body, result)
                if "error" in result:
                    return response
                if method == "initialize":
                    value = result["result"]
                    version = value.get("protocolVersion") if isinstance(value, dict) else None
                    if not token(version, 64):
                        raise ProxyError("invalid-protocol-version")
                    # The agent sees a tools-only server. The Deep32 gateway also advertises prompts, resources and
                    # experimental (all empty); those are removed from the answer rather than refused, and their
                    # methods get JSON-RPC method-not-found here.
                    capabilities = value.get("capabilities", {})
                    if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
                        raise ProxyError("capability-unavailable")
                    value["capabilities"] = {"tools": capabilities["tools"]}
                    cap.protocol_version = version
                    cap.initialised = True
                if method == "tools/list":
                    value = result["result"]
                    if not isinstance(value, dict) or set(value) != {"tools"} or not isinstance(value["tools"], list):
                        raise ProxyError("invalid-tools")
                    tools = value["tools"]
                    names = [t.get("name") if isinstance(t, dict) else None for t in tools]
                    if len(names) != len(TOOLS) or len(set(names)) != len(TOOLS) or set(names) != TOOLS:
                        raise ProxyError("invalid-tools")
                    for tool in tools:
                        if not isinstance(tool.get("inputSchema"), dict) or tool["inputSchema"].get("type") != "object":
                            raise ProxyError("invalid-tool-schema")
                    cap.tools_verified = True
                cap.mcp_session_id = sid or cap.mcp_session_id
                return response
        except ProxyError:
            raise
        except Exception:
            raise ProxyError("upstream-unavailable") from None
        finally:
            with self.lock:
                cap.inflight -= 1

    def http_request(self, cap, *, method, path, host, authorisation, content_type, body):
        # Kept independent of the HTTP parser so all transports enforce the same
        # authenticated, session-scoped policy before upstream dispatch.
        if (method != "POST" or path != "/mcp" or not isinstance(host, str) or len(host) > 16
            or not host.startswith("127.0.0.1:")
            or not host[10:].isdigit() or not 1 <= int(host[10:]) <= 65535
            or content_type != "application/json"):
            raise ProxyError("invalid-http-request")
        if (not isinstance(authorisation, str) or not authorisation.isascii()
                or not hmac.compare_digest(authorisation, "Bearer " + cap.bearer)):
            raise ProxyError("unauthorised")
        if len(body) > MAX_REQUEST:
            raise ProxyError("message-limit")
        from .messages import loads
        try:
            value = loads(body)
        except (ValueError, UnicodeError):
            raise ProxyError("invalid-json") from None
        return self.call(cap, value)


class HTTPProxy:
    """Private HTTP MCP endpoint for one capability, never an HTTP forwarder.

    Creating this object has no socket effects. start() binds an ephemeral IPv4
    loopback port; the caller closes it before deleting session scratch. The
    session bearer belongs in the native private environment/config, not argv.
    Every request still passes Gateway's active-turn and revocation checks.
    """
    def __init__(self, gateway, cap, *, timeout=2.0, server_factory=None):
        if not 0 < timeout <= 5:
            raise ProxyError("invalid-http-policy")
        self.gateway, self.cap = gateway, cap
        self.timeout = timeout
        self.server_factory = server_factory
        self.server = self.thread = None
        self.lock = threading.RLock()
        self.closed = False
        self.cleanup_complete = False

    @property
    def url(self):
        if self.server is None or self.closed:
            raise ProxyError("proxy-unavailable")
        return "http://127.0.0.1:%d/mcp" % self.server.server_address[1]

    def handler(self):
        from http.server import BaseHTTPRequestHandler
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "Deep32"
            sys_version = ""

            def setup(self):
                super().setup()
                self.connection.settimeout(owner.timeout)

            def log_message(self, *_):
                pass

            def send_error(self, code, message=None, explain=None):
                # No reflected path/header/native error enters an HTML page.
                self.respond(code, b'{"error":"invalid-http-request"}')

            def respond(self, code, raw=b"", sid=None):
                self.send_response_only(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.send_header("Cache-Control", "no-store")
                if sid is not None:
                    self.send_header("Mcp-Session-Id", sid)
                self.end_headers()
                self.close_connection = True
                if raw:
                    self.wfile.write(raw)

            def do_POST(self):
                peek = None
                try:
                    # Reject ambiguous framing, duplicate credential/session
                    # headers and HTTP upgrades before reading the body.
                    one = ("Host", "Authorization", "Content-Type", "Content-Length")
                    if (any(len(self.headers.get_all(name, [])) != 1 for name in one)
                            or self.headers.get_all("Transfer-Encoding")
                            or self.headers.get_all("Expect")
                            or self.headers.get_all("Upgrade")
                            or len(self.headers.get_all("Mcp-Session-Id", [])) > 1
                            or len(self.headers.get_all("MCP-Protocol-Version", [])) > 1
                            or sum(len(k) + len(v) for k, v in self.headers.items()) > 16384):
                        raise ProxyError("invalid-http-request")
                    expected_host = "127.0.0.1:%d" % self.server.server_address[1]
                    if self.headers["Host"] != expected_host:
                        raise ProxyError("invalid-http-request")
                    length = self.headers["Content-Length"]
                    if not length.isascii() or not length.isdigit() or len(length) > 7:
                        raise ProxyError("invalid-http-request")
                    size = int(length)
                    if not 0 < size <= MAX_REQUEST:
                        raise ProxyError("message-limit")
                    sid = self.headers.get("Mcp-Session-Id")
                    version = self.headers.get("MCP-Protocol-Version")
                    with owner.gateway.lock:
                        owner.gateway._live(owner.cap)
                        if (sid is not None and sid != owner.cap.mcp_session_id
                                or version is not None and version != owner.cap.protocol_version):
                            raise ProxyError("invalid-session-id")
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        raise ProxyError("invalid-http-request")
                    # MCP clients probe optional methods (Claude Code sends server/discover first, then prompts/list and
                    # resources/list). The spec answer is JSON-RPC "method not found", not an HTTP error, which a client
                    # treats as a dead server. Nothing outside METHODS ever reaches the gateway.
                    import json as _json
                    try:
                        peek = _json.loads(raw)
                    except ValueError:
                        peek = None
                    if (isinstance(peek, dict) and peek.get("jsonrpc") == "2.0" and isinstance(peek.get("method"), str)
                            and peek["method"] not in METHODS):
                        # Same bearer check as every forwarded request: no reply without the session credential.
                        with owner.gateway.lock:
                            owner.gateway._live(owner.cap)
                            if not owner.cap.bearer or not hmac.compare_digest(
                                    self.headers.get("Authorization") or "", "Bearer " + owner.cap.bearer):
                                raise ProxyError("unauthorised")
                        if "id" in peek and type(peek["id"]) in (str, int) and not isinstance(peek["id"], bool):
                            reply = _json.dumps({"jsonrpc": "2.0", "id": peek["id"],
                                                 "error": {"code": -32601, "message": "Method not found"}}).encode()
                            self.respond(200, reply, owner.cap.mcp_session_id)
                        else:
                            self.respond(202)
                        return
                    value = owner.gateway.http_request(owner.cap, method="POST", path=self.path,
                        host=self.headers["Host"], authorisation=self.headers["Authorization"],
                        content_type=self.headers["Content-Type"], body=raw)
                    result = b"" if value["body"] is None else encoded(value["body"], MAX_RESPONSE)
                    self.respond(value["http_status"], result, value["mcp_session_id"])
                except ProxyError as exc:
                    # Too many calls at once, or the turn's budget spent: the bearer was accepted before either
                    # is raised (Gateway.http_request), and the agent gets a text that says what to do.
                    told = refusal(str(exc), peek)
                    if told is not None:
                        self.respond(200, told, owner.cap.mcp_session_id)
                    else:
                        code = 401 if str(exc) == "unauthorised" else 400
                        self.respond(code, b'{"error":"request-refused"}')
                except (OSError, ValueError, TimeoutError):
                    self.close_connection = True

            def do_GET(self):
                self.respond(405, b'{"error":"method-unavailable"}')

            do_DELETE = do_PUT = do_PATCH = do_OPTIONS = do_HEAD = do_GET

        return Handler

    def start(self):
        from http.server import ThreadingHTTPServer
        with self.lock:
            if self.closed or self.server is not None:
                raise ProxyError("proxy-unavailable")
            class Server(ThreadingHTTPServer):
                daemon_threads = True
                allow_reuse_address = False
                block_on_close = False
                request_queue_size = 4

                def server_bind(self):
                    # HTTPServer.server_bind calls getfqdn/gethostbyaddr. This
                    # endpoint has a literal loopback identity and needs no DNS.
                    from socketserver import TCPServer
                    TCPServer.server_bind(self)
                    self.server_name = "127.0.0.1"
                    self.server_port = self.server_address[1]

                def __init__(self, *args, **kwargs):
                    self.slots = threading.BoundedSemaphore(CONNECTIONS)
                    self.worker_lock = threading.RLock()
                    self.workers = {}
                    super().__init__(*args, **kwargs)

                def process_request(self, request, address):
                    if not self.slots.acquire(blocking=False):
                        self.shutdown_request(request)
                        return
                    worker = threading.Thread(target=self.owned_request,
                        args=(request, address), name="deep32-proxy-request", daemon=True)
                    with self.worker_lock:
                        self.workers[request] = worker
                    try:
                        worker.start()
                    except Exception:
                        with self.worker_lock:
                            self.workers.pop(request, None)
                        self.slots.release()
                        self.shutdown_request(request)

                def owned_request(self, request, address):
                    try:
                        self.finish_request(request, address)
                    except Exception:
                        pass
                    finally:
                        try:
                            self.shutdown_request(request)
                        finally:
                            with self.worker_lock:
                                self.workers.pop(request, None)
                            self.slots.release()

                def active_workers(self):
                    with self.worker_lock:
                        return list(self.workers.items())

                def handle_error(self, *_):
                    # stdlib's default prints peer exceptions and request data.
                    pass

                def verify_request(self, request, address):
                    return address[0] == "127.0.0.1"
            factory = self.server_factory or Server
            self.server = factory(("127.0.0.1", 0), self.handler())
            if self.server.server_address[0] != "127.0.0.1":
                self.server.server_close()
                self.server = None
                raise ProxyError("invalid-http-bind")
            self.thread = threading.Thread(target=self.server.serve_forever,
                kwargs={"poll_interval": 0.05}, name="deep32-session-proxy", daemon=True)
            self.thread.start()
            return self

    def close(self):
        with self.lock:
            if self.cleanup_complete:
                return True
            self.closed = True
            server, thread = self.server, self.thread
        good = True
        try:
            self.gateway.revoke(self.cap.session)
        except Exception:
            good = False
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                good = False
            try:
                server.server_close()
            except Exception:
                good = False
            import socket
            workers = server.active_workers()
            for connection, _ in workers:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection.close()
                except OSError:
                    good = False
            end = time.monotonic() + self.timeout + 0.5
            for _, worker in workers:
                worker.join(timeout=max(0, end - time.monotonic()))
            good = good and not any(worker.is_alive() for _, worker in workers)
        if thread is not None:
            thread.join(timeout=self.timeout + 0.5)
            good = good and not thread.is_alive()
        self.cleanup_complete = good
        return good
