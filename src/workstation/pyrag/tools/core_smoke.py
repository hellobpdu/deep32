"""deep32-engine smoke client: /health, /v2/status, and /v2/search in bm25 and hybrid mode, against a running service.

Usage: python -I -B core_smoke.py BASE_URL TOKEN QUERY
Stdlib only. Prints status codes, generation, degraded lists and the top hits; exit 1 if a call fails or the
bm25 search finds nothing.
"""
import json
import sys
import urllib.request

base, token, query = sys.argv[1:4]
AUTH = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def call(method, path, body=None):
    req = urllib.request.Request(base + path, method=method, headers=AUTH,
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read())


ok = True
s, h = call("GET", "/health")  # deep32-engine's /health needs the bearer
print(f"GET /health -> {s} {json.dumps(h)[:200]}")
s, st = call("GET", "/v2/status")
print(f"GET /v2/status -> {s} keys={sorted(st)[:12]}")
for mode in ("bm25", "hybrid"):
    s, r = call("POST", "/v2/search", {"query": query, "mode": mode, "limit": 3})
    hits = r.get("hits") or []
    print(f"POST /v2/search mode={mode} -> {s}: {len(hits)} hits, degraded={r.get('degraded')}, "
          f"generation={r.get('generation')!r}")
    for hit in hits:
        print(f"   {hit.get('cite')!s:28} score={hit.get('score')} {hit.get('id')}")
    ok &= s == 200 and (mode != "bm25" or bool(hits))
sys.exit(0 if ok else 1)
