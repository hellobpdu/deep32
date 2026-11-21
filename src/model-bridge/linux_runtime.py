"""Linux launch adapter for the existing engine and gateway application code.

Credentials stay in private memory behind each application's existing API seam.
The adapter is container-internal; it is not a second installation command.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import importlib
import json
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from linux_bridge import CORE_TOKEN, MCP_TOKENS, CredentialError, CredentialGuard, protected_tokens, load_config, CONFIG

EMBEDDING_URL = "http://model-bridge:9081/v1/embeddings"
RERANK_URL = "http://model-bridge:9082/v1/rerank"
CORE_URL = "http://engine:8765"
CONTENT_ROOT = "/var/lib/deep32/content/kb/root"
RUNTIME_ERROR = "Full role settings are invalid; restore the recorded internal service configuration."
REMOTE_METHODS = frozenset({"initialize", "notifications/initialized", "ping", "tools/list", "tools/call"})
REMOTE_TOOLS = frozenset({"search_rag", "search_many", "get_section", "get_outline", "list_documents",
    "verify_quote", "grep_corpus", "lookup_rfc", "lookup_id", "find_command", "rag_status"})


class RemoteGate:
    """Workstation RemoteGate policy applied to every Linux gateway request.

Only the MCP route is exposed here. Gateway diagnostics use rag_status over MCP.
The host publication adapter separately enforces the exact external route table.
"""
    def __init__(self, app):
        self.app = app

    @staticmethod
    def check(raw):
        try:
            msg = json.loads(raw)
        except ValueError:
            return "", None  # The existing MCP implementation owns parse errors.
        if isinstance(msg, list):
            return "batch", None
        if not isinstance(msg, dict):
            return "", None
        rid = msg.get("id")
        rid = rid if isinstance(rid, (str, int)) and not isinstance(rid, bool) else None
        method = msg.get("method")
        if not isinstance(method, str) or method not in REMOTE_METHODS:
            return "method", rid
        if method == "tools/call":
            params = msg.get("params")
            if not isinstance(params, dict) or params.get("name") not in REMOTE_TOOLS:
                return "tool", rid
        return "", rid

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if (scope.get("method"), scope.get("path")) != ("POST", "/mcp") or scope.get("query_string"):
            return await reply(send, 404, {"error": "not found"})
        raw = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            raw.extend(message.get("body", b""))
            if len(raw) > 256 * 1024:
                return await reply(send, 413, {"error": "request too large"})
            if not message.get("more_body"):
                break
        verdict, rid = self.check(raw)
        if verdict:
            return await reply(send, 200, {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": "Method not available to remote callers"}})
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(raw), "more_body": False}
            return await receive()
        return await self.app(scope, replay, send)


async def reply(send, status, payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    await send({"type": "http.response.start", "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]})
    await send({"type": "http.response.body", "body": raw})


class CredentialBoundary:
    """Enrol/revoke changes take effect before the next request, including pools."""
    def __init__(self, app, core_guard, *, token_path=None):
        self.app, self.core_guard, self.token_path = app, core_guard, token_path
        self.digest = self.registry_digest() if token_path else b""

    def registry_digest(self):
        values = protected_tokens(self.token_path, multiple=True)
        return hashlib.sha256("\n".join(values).encode("ascii")).digest()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            try:
                current = self.core_guard.current() and (not self.token_path or
                    hmac.compare_digest(self.digest, self.registry_digest()))
            except CredentialError:
                current = False
            if not current:
                return await reply(send, 503, {"error": "Credentials changed; complete enrolment and restart the full role."})
        return await self.app(scope, receive, send)


def prepare_engine(env, api, guard):
    """Adapt the legacy token getter in memory, before lifespan/authentication use."""
    if (env.get("RAG_EMBEDDING_URL") != EMBEDDING_URL or env.get("RAG_RERANK_URL") != RERANK_URL or
            env.get("RAG_ROOT") != CONTENT_ROOT or env.get("RAG_TRACE") != "off" or
            env.get("RAG_QDRANT_URL") != "http://qdrant:6333"):
        raise ValueError(RUNTIME_ERROR)
    # api._expected_token is the existing single seam used by both startup and
    # require_token. A bearer is never assigned to os.environ or to a file.
    api._expected_token = lambda: guard.token
    return CredentialBoundary(api.app, guard)


def validate_content(env, *, root=None, model_config=None):
    """Bind the imported C3 bundle to the real engine root and model contract.

The importer verifies signatures and hashes before this point. This check
prevents starting the runtime against the bundle container directory, a missing
database, a different export collection or an incompatible signed model.
"""
    try:
        root = Path(CONTENT_ROOT) if root is None else Path(root)
        config = load_config(CONFIG) if model_config is None else model_config
        bundle = json.loads((root.parent / "BUNDLE.json").read_bytes())
        dense = json.loads((root / "config/dense.json").read_bytes())
        export = json.loads((root.parent / "dense/EXPORT.json").read_bytes())
        for name in ("sqlite/rag.db", "kb/kb.sqlite", "config/retrieval.json", "config/serving.json"):
            path = root / name
            if not path.is_file() or path.is_symlink():
                raise ValueError()
        embedding = bundle["embedding"]
        revision = env.get("RAG_EMBEDDING_REVISION")
        if (bundle.get("format") != "deep32-kb-bundle" or bundle.get("format_version") != 1 or
                embedding["model_id"] != config["GPU_EXT_EMBED_MODEL_ID"] or not revision or
                embedding["revision"] != revision or
                (config.get("GPU_EXT_EMBED_REVISION") and config["GPU_EXT_EMBED_REVISION"] != revision) or
                type(embedding["dimension"]) is not int or embedding["dimension"] <= 0 or
                dense.get("vector_size") != embedding["dimension"] or
                export.get("dim") != embedding["dimension"] or not dense.get("collection") or
                dense["collection"] != export.get("collection")):
            raise ValueError()
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Imported content does not match the signed model; restore the compatible content disc and model configuration.") from None


def prepare_gateway(env, edge, guard, token_path=MCP_TOKENS):
    if env.get("RAG_CORE_URL") != CORE_URL or not env.get("TAILSCALE_DNS_NAME"):
        raise ValueError(RUNTIME_ERROR)
    tokens = protected_tokens(token_path, multiple=True)
    cfg = edge.Config(core_url=CORE_URL, core_token=guard.token,
                      tailscale_host=env["TAILSCALE_DNS_NAME"], mcp_tokens=tokens)
    return CredentialBoundary(RemoteGate(edge.create_app(cfg)), guard, token_path=token_path)


def check_gateway(token_path=MCP_TOKENS):
    """Probe the real authenticated MCP route, never a public diagnostics URL."""
    connection = None
    try:
        token = protected_tokens(token_path, multiple=True)[0]
        connection = http.client.HTTPConnection("127.0.0.1", 8766, timeout=5)
        raw = json.dumps({"jsonrpc": "2.0", "id": "health", "method": "tools/list", "params": {}}).encode()
        connection.request("POST", "/mcp", body=raw, headers={"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream", "Authorization": "Bearer " + token})
        response = connection.getresponse()
        data = response.read(262145)
        if response.status != 200 or len(data) > 262144:
            return False
        result = json.loads(data)
        tools = result.get("result", {}).get("tools")
        return (isinstance(tools, list) and len(tools) == len(REMOTE_TOOLS) and
                {tool.get("name") for tool in tools if isinstance(tool, dict)} == REMOTE_TOOLS)
    except (CredentialError, OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
        return False
    finally:
        if connection is not None:
            connection.close()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--check", "gateway"]:
        return 0 if check_gateway() else 1
    if args not in (["engine"], ["gateway"]):
        raise SystemExit("Select the installed engine or gateway service command.")
    env = dict(os.environ)
    if any(key in env for key in ("RAG_CORE_API_TOKEN", "RAG_MCP_TOKENS", "GPU_EXT_TOKEN")):
        raise SystemExit("Literal credentials are refused; complete enrolment using the protected credential files.")
    try:
        guard = CredentialGuard(CORE_TOKEN)
        if args == ["engine"]:
            sys.path.insert(0, "/opt/deep32/engine/src")
            os.chdir("/opt/deep32/engine")
            validate_content(env)
            app = prepare_engine(env, importlib.import_module("rag_core.api"), guard)
            # Reuse the installed Workstation layer, copied from its canonical
            # source by the image builder. It preserves read pragmas and never
            # creates a database, WAL or SHM beside signed immutable content.
            importlib.import_module("node_layers").apply_immutable_db()
            port = 8765
        else:
            sys.path.insert(0, "/opt/deep32/gateway")
            app = prepare_gateway(env, importlib.import_module("rag_edge"), guard)
            port = 8766
    except (CredentialError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except (ImportError, OSError):
        raise SystemExit("The full role application payload is incomplete; rebuild it from the pinned offline inputs.") from None
    import uvicorn
    # Access logs could contain query strings; application logs already report
    # fixed request status and degradation without query/passage text.
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, access_log=False)


if __name__ == "__main__":
    raise SystemExit(main())
