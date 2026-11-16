"""deep32-gateway smoke client: MCP initialize, tools/list and one tools/call (search_rag) over streamable HTTP.

Usage: python -I -B mcp_smoke.py BASE_URL TOKEN QUERY
Stdlib only (urllib), so it runs with any interpreter. Prints the server's name/version, the tool count and the
first lines of the search_rag answer; exit 1 on any protocol error.
"""
import json
import sys
import urllib.request

base, token, query = sys.argv[1:4]
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "MCP-Protocol-Version": "2025-06-18", "Authorization": f"Bearer {token}"}


def rpc(method, params=None, rid=1):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    req = urllib.request.Request(base + "/mcp", data=json.dumps(msg).encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read())
    if "error" in body:
        raise SystemExit(f"{method}: {body['error']}")
    return body["result"]


init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "d32-smoke", "version": "1"}})
print("initialize:", init["serverInfo"]["name"], init["serverInfo"]["version"], init["protocolVersion"])
tools = rpc("tools/list", rid=2)["tools"]
print("tools/list:", len(tools), "tools:", ", ".join(sorted(t["name"] for t in tools)))
res = rpc("tools/call", {"name": "search_rag", "arguments": {"query": query, "limit": 3}}, rid=3)
text = "".join(c.get("text", "") for c in res["content"])
print("tools/call search_rag isError=%s, %d chars:" % (res.get("isError"), len(text)))
print("\n".join(text.splitlines()[:8]))
sys.exit(1 if res.get("isError") else 0)
