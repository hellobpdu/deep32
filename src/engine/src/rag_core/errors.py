"""Error text that is safe to hand to API clients.

httpx and qdrant-client put the full request URL into their messages, so a
raw repr(exc) in a response leaked internal URLs and host names (the GPU
host's tailnet name) into the client's context. error_kind() gives a short,
stable category for breakers and status; public_error() gives the exception
class plus its first message line with URLs, IP addresses and host names
replaced.
"""
from __future__ import annotations

import re

import httpx


MAX_MESSAGE_CHARS = 200

_URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>()]+")
_IPV6 = re.compile(r"\[[0-9A-Fa-f:.%]+\](?::\d+)?")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
# dotted names (host.example.net, deep32-engine.lan); over-matching a dotted
# identifier in an error message only costs a little detail
_HOST = re.compile(r"\b(?:[A-Za-z0-9_-]+\.)+[A-Za-z][A-Za-z0-9_-]*\b")


def scrub(
    text: str,
) -> str:
    text = _URL.sub("<url>", text)
    text = _IPV6.sub("<host>", text)
    text = _IPV4.sub("<host>", text)
    return _HOST.sub("<host>", text)


def _chain(
    exc: BaseException,
) -> list[BaseException]:
    # qdrant-client wraps transport errors (ResponseHandlingException.source)
    found: list[BaseException] = []
    todo: list[BaseException | None] = [exc]
    while todo and len(found) < 8:
        err = todo.pop(0)
        if err is None or err in found:
            continue
        found.append(err)
        todo += [
            err.__cause__,
            err.__context__,
            getattr(err, "source", None),
        ]
    return [
        err
        for err in found
        if isinstance(err, BaseException)
    ]


def error_kind(
    exc: BaseException,
) -> str:
    """timeout | connect | transport | http_<status> | <exception class>"""
    for err in _chain(exc):
        if isinstance(err, (httpx.TimeoutException, TimeoutError)):
            return "timeout"
        if isinstance(err, httpx.HTTPStatusError):
            return f"http_{err.response.status_code}"
        if isinstance(err, (httpx.ConnectError, ConnectionError)):
            return "connect"
        if isinstance(err, httpx.TransportError):
            return "transport"
        status = getattr(err, "status_code", None)
        if isinstance(status, int):
            return f"http_{status}"
    return type(exc).__name__


def public_error(
    exc: BaseException,
) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        message = f"HTTP {exc.response.status_code}"
    else:
        lines = str(exc).strip().splitlines()
        message = scrub(lines[0] if lines else "")
    message = message[:MAX_MESSAGE_CHARS]
    name = type(exc).__name__
    return f"{name}: {message}" if message else name
