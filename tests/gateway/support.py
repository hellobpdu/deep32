"""Shared test fixtures: a fake deep32-engine on httpx.MockTransport and an in-process ASGI client. No network."""

from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx


from rag_edge import Config, Core, create_app  # noqa: E402

# Quiet by default (the SDK installs a rich INFO handler only when the root logger has none); assertLogs still works.
logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()])

# Fake credentials, used only in tests.
TOKEN = "test-token-" + "a" * 40
OLD_TOKEN = "test-token-" + "b" * 40
CORE_TOKEN = "fake-core-token-" + "c" * 32
CORE_URL = "http://core.internal.test:8765"
BASE_URL = "http://127.0.0.1:8766"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}
INIT_PARAMS = {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "tests", "version": "1"}}

HIT = {
    "id": "chk_3101a", "doc_id": "doc_3101", "doc_key": "rfc3101", "title": "The OSPF NSSA Option",
    "doc_type": "rfc", "cite": "RFC 3101 §3.2", "section_id": "rfc3101#3.2",
    "section_path": ["3 Border Router Behavior", "3.2 Translating Type-7 LSAs"],
    "status": "Proposed Standard; obsoletes RFC 1587", "release": None, "score": 0.81234,
    "matched": ["bm25", "dense"], "text": "If the Type-7 LSA has the P-bit clear, then do nothing.",
    "local": "rfc3101.txt:900-940",
}
SEARCH = {"hits": [HIT], "degraded": [], "timings_ms": {"bm25": 12, "total": 40}, "generation": "g-test-1"}
HEALTH = {"status": "ok", "service": "deep32-engine", "active_retrievers": ["bm25", "dense"]}


def reply(status: int = 200, body: Any = None, headers: dict[str, str] | None = None):
    """A responder building a fresh httpx.Response per request."""
    return lambda request: httpx.Response(status, json=body, headers=headers)


class FakeCore:
    """deep32-engine stand-in: routes (METHOD, path) to canned answers and records every request.

    An answer is a dict (200 JSON), a responder from `reply`, or a list of those served in order (the last one
    repeats). Unknown routes get FastAPI's 404 {"detail": "Not Found"}.
    """

    def __init__(self, routes: dict[tuple[str, str], Any] | None = None) -> None:
        self.routes = dict(routes or {})
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.now = 0.0  # Core.clock: a test moves it on to expire the opt-in status cache

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get((request.method, request.url.path), reply(404, {"detail": "Not Found"}))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        return answer(request) if callable(answer) else httpx.Response(200, json=answer)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def core(self) -> Core:
        return Core(CORE_URL, CORE_TOKEN, transport=httpx.MockTransport(self.handler), sleep=self.sleep,
                    clock=lambda: self.now)

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)

    def params(self, index: int = -1) -> dict[str, str]:
        return dict(self.requests[index].url.params)


def make_app(fake: FakeCore, tokens: tuple[str, ...] = (TOKEN,), networks: tuple = (), core: Core | None = None):
    config = Config(core_url=CORE_URL, core_token=CORE_TOKEN, tailscale_host="deep32-gateway.example.ts.net",
                    mcp_tokens=tokens, allowed_networks=networks)
    return create_app(config, core=core or fake.core())


@asynccontextmanager
async def serve(app, client: tuple[str, int] = ("127.0.0.1", 50000)):
    """Run the app's lifespan and yield an httpx client wired to it in-process."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            yield http


async def rpc(http: httpx.AsyncClient, method: str, params: dict | None = None, *, token: str | None = TOKEN,
              headers: dict[str, str] | None = None) -> httpx.Response:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        message["params"] = params
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    return await http.post("/mcp", json=message, headers={**MCP_HEADERS, **auth, **(headers or {})})
