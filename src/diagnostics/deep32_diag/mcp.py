"""Bounded MCP observations through an enrolled, qualified HTTPS capability.

This module never opens a socket, reads a credential file, or discovers a URL.
The owning platform must inject a transport qualified for direct TLS, no proxy
or redirects, and interruptible DNS/TLS/body reads.  The default fails closed.
Response text stays private until the status allow-list selects typed values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import base64
import hashlib
import ipaddress
import json
import math
import re
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, TypeVar
from urllib.parse import urlsplit


MAX_BYTES = 4 * 1024 * 1024
MAX_SECONDS = 10.0
TOOLS = frozenset((
    "rag_status", "search_rag", "search_many", "get_section", "get_outline",
    "lookup_id", "lookup_rfc", "find_command", "grep_corpus",
    "list_documents", "verify_quote",
))
_CODES = frozenset((
    "mcp-endpoint", "mcp-credential", "mcp-timeout", "mcp-limit",
    "mcp-transport", "mcp-transport-unqualified", "mcp-redirect", "mcp-auth",
    "mcp-anonymous", "mcp-http", "mcp-headers", "mcp-encoding", "mcp-json",
    "mcp-envelope", "mcp-rpc", "mcp-sse", "mcp-session", "mcp-version",
    "mcp-initialise", "mcp-tools", "mcp-tool", "mcp-status", "mcp-state",
))


class MCPError(Exception):
    """A fixed reason code; no transport exception or response is retained."""

    def __init__(self, code: str):
        self.code = code if code in _CODES else "mcp-transport"
        super().__init__(self.code)


def _guarded(method):
    """Drop raw exception context before an error crosses the public seam."""
    def wrapped(*args, **kwargs):
        code = "mcp-transport"
        try:
            return method(*args, **kwargs)
        except MCPError as error:
            code = error.code
        except (TimeoutError,):
            code = "mcp-timeout"
        except Exception:
            pass
        raise MCPError(code)
    wrapped.__name__ = method.__name__
    wrapped.__doc__ = method.__doc__
    return wrapped


@dataclass(frozen=True, repr=False)
class EnrolledEndpoint:
    """Authority supplied by the selected profile, never a CLI probe URL.

    Both the host and service port must agree with that enrolled authority.
    Only canonical HTTPS DNS /mcp endpoints are accepted.
    """

    url: str = field(repr=False)
    enrolled_host: str = field(repr=False)
    enrolled_port: int = 443

    @_guarded
    def __post_init__(self):
        if (type(self.url) is not str or len(self.url) > 2048 or
                type(self.enrolled_host) is not str or
                type(self.enrolled_port) is not int or
                not 1 <= self.enrolled_port <= 65535):
            raise MCPError("mcp-endpoint")
        if any(ord(char) <= 32 or ord(char) >= 127 for char in self.url):
            raise MCPError("mcp-endpoint")
        parsed, host, port = None, None, -1
        try:
            parsed = urlsplit(self.url)
            host = parsed.hostname
            port = parsed.port if parsed.port is not None else 443
        except ValueError:
            pass
        if (parsed is None or parsed.scheme != "https" or not host or parsed.username is not None or
                parsed.password is not None or parsed.path != "/mcp" or
                parsed.query or parsed.fragment or port != self.enrolled_port or
                host != self.enrolled_host or host != host.lower() or
                len(host) > 253 or "." not in host or
                any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in host.split("."))):
            raise MCPError("mcp-endpoint")
        is_ip = False
        try:
            ipaddress.ip_address(host)
            is_ip = True
        except ValueError:
            pass
        authority = host + (":" + str(port) if ":" in parsed.netloc else "")
        if is_ip or self.url != "https://" + authority + "/mcp":
            raise MCPError("mcp-endpoint")

    def __repr__(self):
        return "<EnrolledEndpoint redacted>"


class Credential(Protocol):
    def authorization(self) -> str:
        """Return a protected in-memory Bearer header, never a path or argv."""


@dataclass(frozen=True, repr=False)
class Request:
    endpoint: EnrolledEndpoint = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    deadline: float
    max_bytes: int
    method: str = "POST"
    allow_redirects: bool = False
    use_proxies: bool = False

    def __repr__(self):
        return "<MCPRequest redacted>"


class Response(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(self, size: int, *, deadline: float) -> bytes:
        """Read at most size bytes, interrupted at the absolute deadline."""

    def close(self) -> None:
        """Release the owned observation; never persist a cookie or session."""


class Transport(Protocol):
    def open(self, request: Request) -> Response:
        """Use only request.endpoint and honour all its security limits.

        Qualification must include bounded name resolution, TLS and reads,
        trusted certificate verification, no proxies, redirects or key logs,
        no cookies/cache files, and no persistent local/admin mutation.
        """


class UnqualifiedTransport:
    def open(self, request: Request) -> Response:
        raise MCPError("mcp-transport-unqualified")


@dataclass(frozen=True)
class BackendStatus:
    enabled: bool
    ok: bool | None
    breaker: str | None = None


@dataclass(frozen=True)
class StatusObservation:
    ok: bool
    generation: str | None
    corpus_fingerprint: str | None
    bm25: BackendStatus
    dense: BackendStatus
    reranker: BackendStatus
    kb: BackendStatus
    structure: BackendStatus
    degraded: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return (self.ok and self.generation is not None and
                self.corpus_fingerprint is not None and not self.degraded and
                all(row.enabled and row.ok is True and
                    row.breaker in (None, "closed") for row in
                    (self.bm25, self.dense, self.reranker, self.kb, self.structure)))


def _json(raw: bytes | str) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MCPError("mcp-json")
            result[key] = value
        return result

    def constant(_):
        raise MCPError("mcp-json")

    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "strict")
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise MCPError("mcp-json") from None
    if type(value) is not dict:
        raise MCPError("mcp-envelope")
    return value


def _status(result: dict[str, Any]) -> StatusObservation:
    if result.get("isError", False) is not False:
        raise MCPError("mcp-tool")
    # A single JSON text block is the existing rag_status(format=json) seam.
    content = result.get("content")
    if (type(content) is not list or len(content) != 1 or
            type(content[0]) is not dict or content[0].get("type") != "text" or
            type(content[0].get("text")) is not str):
        raise MCPError("mcp-status")
    value = _json(content[0]["text"])
    if type(value.get("ok")) is not bool:
        raise MCPError("mcp-status")
    generation = value.get("generation")
    if generation is not None and (type(generation) is not str or
            not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", generation)):
        raise MCPError("mcp-status")
    fingerprint = value.get("corpus_fingerprint")
    if fingerprint is not None and (type(fingerprint) is not str or
            not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
        raise MCPError("mcp-status")
    backends = value.get("backends")
    degraded = value.get("degraded")
    if (type(backends) is not dict or type(degraded) is not list or
            len(degraded) > 6 or len(set(item for item in degraded if type(item) is str)) != len(degraded) or
            any(type(item) is not str or item not in
                ("bm25", "dense", "reranker", "kb", "structure", "colbert")
                for item in degraded)):
        raise MCPError("mcp-status")

    def backend(name, optional=False):
        row = backends.get(name)
        if type(row) is not dict:
            raise MCPError("mcp-status")
        enabled = row.get("enabled") if optional else row.get("enabled", True)
        ok = row.get("ok")
        breaker = row.get("breaker")
        if (type(enabled) is not bool or (type(ok) is not bool and ok is not None) or
                (enabled and ok is None) or (not optional and not enabled) or
                breaker not in (None, "closed", "open", "half-open", "half_open")):
            raise MCPError("mcp-status")
        return BackendStatus(enabled, ok, breaker)

    rows = (backend("bm25"), backend("dense", True), backend("reranker", True),
            backend("kb"), backend("structure"))
    if value["ok"] != rows[0].ok:
        raise MCPError("mcp-status")
    # Recompute degradation as well: an empty remote list is not readiness.
    failed = {name for name, row in zip(
        ("bm25", "dense", "reranker", "kb", "structure"), rows)
        if row.enabled and (not row.ok or row.breaker not in (None, "closed"))}
    failed.update(degraded)
    return StatusObservation(value["ok"], generation, fingerprint, *rows, tuple(sorted(failed)))


T = TypeVar("T")


class MCPClient:
    """One non-concurrent, in-memory MCP session, without retries or discovery.

    Every public operation shares a ten-second absolute deadline across all of
    its exchanges.  An optional enclosing deadline can only shorten it.  The
    transport must be independently qualified; Python cannot preempt an
    arbitrary injected callable which ignores its capability deadline.
    """

    @_guarded
    def __init__(self, endpoint: EnrolledEndpoint, credential: Credential, *,
                 transport: Transport | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 timeout: float = MAX_SECONDS, max_bytes: int = MAX_BYTES,
                 deadline: float | None = None,
                 protocol_versions: tuple[str, ...] = ("2024-11-05",)):
        if (not isinstance(endpoint, EnrolledEndpoint) or type(timeout) not in (int, float) or
                not math.isfinite(timeout) or not 0 < timeout <= MAX_SECONDS or
                type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES or
                (deadline is not None and (type(deadline) not in (int, float) or
                                          not math.isfinite(deadline))) or
                type(protocol_versions) is not tuple or not protocol_versions or
                len(protocol_versions) > 4 or any(type(v) is not str or
                    not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", v)
                    for v in protocol_versions)):
            raise MCPError("mcp-state")
        self._endpoint, self._credential = endpoint, credential
        self._transport = transport if transport is not None else UnqualifiedTransport()
        self._clock, self._timeout, self._max_bytes = clock, float(timeout), max_bytes
        self._outer_deadline, self._versions = deadline, protocol_versions
        self._version, self._session = None, None
        self._initialised, self._next_id = False, 1
        self._private_values = frozenset()

    def __repr__(self):
        return "<MCPClient redacted>"

    def _deadline(self):
        end = self._clock() + self._timeout
        return min(end, self._outer_deadline) if self._outer_deadline is not None else end

    def _check(self, end):
        now = self._clock()
        if not math.isfinite(now) or now >= end:
            raise MCPError("mcp-timeout")

    def _authorization(self):
        header = None
        try:
            header = self._credential.authorization()
        except Exception:
            pass
        if type(header) is not str or not re.fullmatch(r"Bearer [0-9a-f]{64}", header):
            raise MCPError("mcp-credential")
        token = header[7:]
        raw = token.encode("ascii")
        # Only compare in memory. None of these values, including token-derived
        # hashes, is an observation or an error field.
        self._private_values = frozenset((
            token, token.upper(), header,
            base64.b64encode(raw).decode("ascii"),
            base64.b64encode(raw).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
            hashlib.sha256(raw).hexdigest(), hashlib.sha512(raw).hexdigest(),
        ))
        return header

    def _safe_status(self, result):
        observed = _status(result)
        for value in (observed.generation, observed.corpus_fingerprint):
            if value is not None and any(private in value for private in self._private_values):
                raise MCPError("mcp-status")
        return observed

    def _headers(self, response):
        raw = response.headers
        if not isinstance(raw, Mapping) or len(raw) > 64:
            raise MCPError("mcp-headers")
        headers, size = {}, 0
        for key, value in raw.items():
            if (type(key) is not str or type(value) is not str or
                    not re.fullmatch(r"[A-Za-z0-9-]{1,80}", key) or
                    any(ord(char) < 32 or ord(char) >= 127 for char in value) or
                    key.lower() in headers):
                raise MCPError("mcp-headers")
            headers[key.lower()] = value
            size += len(key) + len(value)
        if size > 16384:
            raise MCPError("mcp-limit")
        length = headers.get("content-length")
        if length is not None and (not re.fullmatch(r"[0-9]{1,12}", length) or
                                   int(length) > self._max_bytes):
            raise MCPError("mcp-limit")
        if headers.get("content-encoding", "identity").lower() != "identity":
            raise MCPError("mcp-encoding")
        return headers

    def _chunks(self, response, end):
        total = 0
        while True:
            self._check(end)
            size = min(65536, self._max_bytes + 1 - total)
            chunk = response.read(size, deadline=end)
            self._check(end)
            if type(chunk) is not bytes:
                raise MCPError("mcp-transport")
            if len(chunk) > size or total + len(chunk) > self._max_bytes:
                raise MCPError("mcp-limit")
            if not chunk:
                return
            total += len(chunk)
            yield chunk

    def _envelope(self, value, ident):
        if (type(value) is not dict or value.get("jsonrpc") != "2.0" or
                type(value.get("id")) is not int or value["id"] != ident or
                set(value) - {"jsonrpc", "id", "result", "error"} or
                ("result" in value) == ("error" in value)):
            raise MCPError("mcp-envelope")
        if "error" in value:
            raise MCPError("mcp-rpc")
        if type(value["result"]) is not dict:
            raise MCPError("mcp-envelope")
        return value["result"]

    def _sse(self, response, end, ident):
        # An SSE response can stay open after its result. Stop at the matching
        # complete event, release the response, and never reconnect/resume.
        pending, data, event, events = b"", [], "", 0

        def line(raw):
            nonlocal data, event, events
            try:
                text = raw.decode("utf-8", "strict")
            except UnicodeError:
                raise MCPError("mcp-sse") from None
            if not text:
                if not data:
                    event = ""
                    return None
                events += 1
                if events > 128 or event not in ("", "message"):
                    raise MCPError("mcp-sse")
                value = _json("\n".join(data))
                data, event = [], ""
                if "id" in value:
                    return self._envelope(value, ident)
                if (value.get("jsonrpc") != "2.0" or
                        type(value.get("method")) is not str or
                        not value["method"].startswith("notifications/") or
                        set(value) - {"jsonrpc", "method", "params"} or
                        ("params" in value and type(value["params"]) is not dict)):
                    raise MCPError("mcp-envelope")
                return None
            if text.startswith(":"):
                return None
            name, _, value = text.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if name == "data":
                data.append(value)
            elif name == "event":
                event = value
            elif name not in ("id", "retry"):
                raise MCPError("mcp-sse")
            return None

        for chunk in self._chunks(response, end):
            pending += chunk
            while True:
                match = re.search(rb"\r\n|\r|\n", pending)
                if match is None:
                    break
                # A CR at a chunk boundary might be half a CRLF pair.
                if match.group() == b"\r" and match.end() == len(pending):
                    break
                result = line(pending[:match.start()])
                pending = pending[match.end():]
                if result is not None:
                    return result
        # A terminated event is required; EOF never invents a delimiter.
        if pending.endswith(b"\r"):
            result = line(pending[:-1])
            if result is not None:
                return result
        raise MCPError("mcp-sse")

    def _exchange(self, method, params, end, *, anonymous=False, notification=False,
                  initialising=False):
        self._check(end)
        ident = None if notification else self._next_id
        if not notification:
            if self._next_id > 2147483647:
                raise MCPError("mcp-state")
            self._next_id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = ident
        if params is not None:
            payload["params"] = params
        body = json.dumps(payload, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode("ascii")
        if len(body) > self._max_bytes:
            raise MCPError("mcp-limit")
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": self._version or self._versions[0]}
        if not anonymous:
            header = self._authorization()
            headers["Authorization"] = header
            if self._session is not None and not initialising:
                headers["Mcp-Session-Id"] = self._session
        request = Request(self._endpoint, MappingProxyType(headers), body, end, self._max_bytes)
        self._check(end)
        response = self._transport.open(request)
        try:
            self._check(end)
            status = response.status
            if type(status) is not int:
                raise MCPError("mcp-http")
            if 300 <= status <= 399:
                raise MCPError("mcp-redirect")
            if anonymous:
                if status in (401, 403):
                    return None
                if 200 <= status <= 299:
                    raise MCPError("mcp-anonymous")
                raise MCPError("mcp-http")
            if status in (401, 403):
                raise MCPError("mcp-auth")
            if status == 404 and self._session is not None:
                raise MCPError("mcp-session")
            if not 200 <= status <= 299:
                raise MCPError("mcp-http")
            received = self._headers(response)
            session = received.get("mcp-session-id")
            if session is not None and (not 1 <= len(session) <= 256 or
                                       any(ord(c) < 33 or ord(c) > 126 for c in session)):
                raise MCPError("mcp-session")
            if session is not None and not initialising and session != self._session:
                raise MCPError("mcp-session")
            if notification:
                if status not in (202, 204) or b"".join(self._chunks(response, end)):
                    raise MCPError("mcp-envelope")
                return None
            if status != 200:
                raise MCPError("mcp-http")
            kind = received.get("content-type", "").split(";", 1)[0].strip().lower()
            if kind == "application/json":
                result = self._envelope(_json(b"".join(self._chunks(response, end))), ident)
            elif kind == "text/event-stream":
                result = self._sse(response, end, ident)
            else:
                raise MCPError("mcp-encoding")
            if initialising:
                self._session = session
            return result
        finally:
            response.close()
            self._check(end)

    def _initialise(self, end):
        if self._initialised:
            return
        result = self._exchange("initialize", {
            "protocolVersion": self._versions[0], "capabilities": {},
            "clientInfo": {"name": "deep32-diagnostics", "version": "1"},
        }, end, initialising=True)
        version = result.get("protocolVersion")
        if version not in self._versions:
            raise MCPError("mcp-version")
        if (type(result.get("capabilities")) is not dict or
                type(result["capabilities"].get("tools")) is not dict or
                type(result.get("serverInfo")) is not dict or
                type(result["serverInfo"].get("name")) is not str or
                type(result["serverInfo"].get("version")) is not str):
            raise MCPError("mcp-initialise")
        self._version = version
        self._exchange("notifications/initialized", None, end, notification=True)
        self._initialised = True

    def _tools(self, end):
        self._initialise(end)
        result = self._exchange("tools/list", {}, end)
        rows = result.get("tools")
        if type(rows) is not list or len(rows) != len(TOOLS) or result.get("nextCursor") is not None:
            raise MCPError("mcp-tools")
        names = [row.get("name") if type(row) is dict else None for row in rows]
        if any(type(name) is not str for name in names) or set(names) != TOOLS:
            raise MCPError("mcp-tools")
        return tuple(sorted(TOOLS))

    @_guarded
    def initialise(self) -> None:
        self._initialise(self._deadline())

    @_guarded
    def list_tools(self) -> tuple[str, ...]:
        return self._tools(self._deadline())

    @_guarded
    def probe_authentication(self) -> None:
        """One anonymous initialise attempt must receive 401 or 403."""
        self._exchange("initialize", {
            "protocolVersion": self._versions[0], "capabilities": {},
            "clientInfo": {"name": "deep32-diagnostics", "version": "1"},
        }, self._deadline(), anonymous=True)

    @_guarded
    def _extract_tool(self, name: str, arguments: dict[str, Any],
                      extractor: Callable[[dict[str, Any]], T]) -> T:
        """Pass a private tool result directly to its reviewed typed extractor.

        The caller owns that extractor's allow-list and must not log raw data.
        This seam lets selftest share transport without exposing a raw RPC API.
        """
        if type(name) is not str or name not in TOOLS or type(arguments) is not dict:
            raise MCPError("mcp-tool")
        end = self._deadline()
        self._initialise(end)
        result = self._exchange("tools/call", {"name": name, "arguments": arguments}, end)
        if result.get("isError", False) is not False:
            raise MCPError("mcp-tool")
        extracted = extractor(result)
        self._check(end)
        return extracted

    @_guarded
    def status(self) -> StatusObservation:
        return self._extract_tool("rag_status", {"format": "json"}, self._safe_status)

    @_guarded
    def observe(self) -> StatusObservation:
        """Anonymous denial, negotiated session, exact tools and typed status."""
        end = self._deadline()
        self._exchange("initialize", {
            "protocolVersion": self._versions[0], "capabilities": {},
            "clientInfo": {"name": "deep32-diagnostics", "version": "1"},
        }, end, anonymous=True)
        self._initialise(end)
        self._tools(end)
        observed = self._safe_status(self._exchange("tools/call", {
            "name": "rag_status", "arguments": {"format": "json"}}, end))
        self._check(end)
        return observed
