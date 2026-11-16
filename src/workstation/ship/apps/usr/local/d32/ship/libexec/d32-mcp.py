"""d32-mcp.py: rag_status over deep32-gateway's UNIX socket, for the status lamp (ship lane, First light PREVIEW).

  /usr/local/bin/python3.14 -I -B /usr/local/d32/ship/libexec/d32-mcp.py [--uds PATH] [--token-file F] [--smoke]

Prints one JSON line: {"state": "ok"|"degraded"|"down", "degraded": [...], ...}. --smoke also runs every one of
the eleven read tools once and reports, per tool, only whether it answered (never any text). Only counts, ids and
states leave this program; never document text. Stdlib only. The token is read from a root-only file (never argv).
Exit 0 when deep32-gateway answered, 1 when it did not.

Update 1 (U07): deep32-gateway's rag_status answers in compact tagged text by default, one "key: value; key: value" line
per part ("ok: True; generation: ...; n_docs: N; n_chunks: N", "dense: DOWN; ...", "degraded: dense; reranker" or
"degraded: none"); the preview parsed only JSON, found nothing and said "ok" while search was degraded. The text is
read now; a JSON answer (format=json, or an older edge) still works.
"""
import argparse
import http.client
import json
import socket
import sys
import time


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=60.0):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.path)
        self.sock = s


def rpc(path, token, method, params, rid):
    c = UnixConnection(path)
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode()
    c.request("POST", "/mcp", body=body, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18", "Accept": "application/json, text/event-stream"})
    r = c.getresponse()
    data = r.read()
    c.close()
    text = data.decode("utf-8", "replace")
    if text.startswith("event:") or text.startswith("data:") or "\ndata:" in text:
        text = "\n".join(l[5:].strip() for l in text.splitlines() if l.startswith("data:"))
    return r.status, json.loads(text)


def walk(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = k.lower()
            if lk in ("degraded", "degraded_components") and isinstance(v, list):
                out["degraded"] = sorted({str(x) for x in v})
            elif lk in ("generation", "generation_id", "active_generation") and isinstance(v, str):
                out.setdefault("generation", v)
            elif lk in ("docs", "documents", "doc_count", "n_docs") and isinstance(v, int):
                out.setdefault("docs", v)
            elif lk in ("chunks", "chunk_count", "n_chunks") and isinstance(v, int):
                out.setdefault("chunks", v)
            elif lk == "ok" and isinstance(v, bool):
                out.setdefault("ok", v)
            walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, out)


# the parts of rag_status's text that are backends: "<name>: ok" or "<name>: DOWN; ..." (not the summary lines)
BACKENDS = ("bm25", "dense", "reranker", "kb", "structure")


def parse_text(text, out):
    """rag_status's tagged text -> out: ok, generation, docs, chunks, degraded (a sorted list). Values only from
    the status keys named here; nothing else of the text is kept."""
    seen_degraded = False
    down = set()
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("; ")]
        if not parts or ":" not in parts[0]:
            continue
        head, _, val = parts[0].partition(":")
        head, val = head.strip().lower(), val.strip()
        if head == "degraded":
            seen_degraded = True
            names = [] if val.lower() in ("", "none", "[]") else [x.strip() for x in val.replace(",", ";").split(";")]
            names += [p.strip() for p in parts[1:]]
            out["degraded"] = sorted({n for n in names if n and n.lower() != "none"})
            continue
        if head in BACKENDS and val.split()[0:1] and val.split()[0].lower() != "ok":
            down.add(head)
        for p in parts:
            k, _, v = p.partition(":")
            k, v = k.strip().lower(), v.strip()
            if k == "ok":
                out["ok"] = v.lower() == "true"
            elif k == "generation" and v:
                out.setdefault("generation", v)
            elif k in ("n_docs", "docs") and v.isdigit():
                out.setdefault("docs", int(v))
            elif k in ("n_chunks", "chunks") and v.isdigit():
                out.setdefault("chunks", int(v))
    if not seen_degraded and down:
        out["degraded"] = sorted(down)


def state_of(out):
    if out.get("ok") is False and not out.get("docs"):
        return "down"
    if out.get("degraded") or out.get("ok") is False:
        return "degraded"
    return "ok"


SMOKE = [
    ("search_rag", {"query": "OSPF NSSA type-7 translation", "limit": 3}),
    ("search_many", {"queries": [{"text": "BGP local preference"}, {"text": "HSRP preemption"}], "limit": 3}),
    ("get_outline", {"doc": "rfc3101", "depth": 1}),
    ("lookup_id", {"identifier": "RFC 3101"}),
    ("find_command", {"command": "show ip ospf neighbor"}),
    ("grep_corpus", {"pattern": "NSSA"}),
    ("verify_quote", {"ref": "rfc3101#1", "quote": "NSSA"}),
    ("lookup_rfc", {"rfc": 3101}),
    ("get_section", {"ref": "rfc3101#1", "max_chars": 400}),
    ("list_documents", {}),
    ("rag_status", {}),
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--uds", default="/var/run/d32/edge.sock")
    p.add_argument("--token-file", default="/etc/d32/state/mcp.token")
    p.add_argument("--smoke", action="store_true")
    a = p.parse_args()
    out = {"state": "down"}
    try:
        token = open(a.token_file).read().strip()
        st, init = rpc(a.uds, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                    "clientInfo": {"name": "d32-lamp", "version": "1"}}, 1)
        if st != 200 or "result" not in init:
            out["error"] = "initialize %s" % st
            print(json.dumps(out))
            return 1
        if a.smoke:
            st, tl = rpc(a.uds, token, "tools/list", {}, 2)
            out["tools_listed"] = len(tl.get("result", {}).get("tools", [])) if st == 200 else 0
            ok, ms = [], []
            for i, (name, args) in enumerate(SMOKE):
                t0 = time.monotonic()
                st, rep = rpc(a.uds, token, "tools/call", {"name": name, "arguments": args}, 10 + i)
                ms.append(round((time.monotonic() - t0) * 1000))
                good = st == 200 and "result" in rep and not rep["result"].get("isError", False)
                ok.append("%s=%s" % (name, "ok" if good else "FAIL(%s)" % st))
            out["tools"] = ok
            out["ms"] = ms
        st, rep = rpc(a.uds, token, "tools/call", {"name": "rag_status", "arguments": {}}, 3)
        if st != 200 or "result" not in rep:
            out["error"] = "rag_status %s" % st
            print(json.dumps(out))
            return 1
        res = rep["result"]
        payload = res.get("structuredContent")
        if payload is not None:
            walk(payload, out)
        else:
            for c in res.get("content", []):
                if c.get("type") != "text":
                    continue
                text = c.get("text", "")
                if text.lstrip().startswith("{"):
                    try:
                        walk(json.loads(text), out)
                    except ValueError:
                        parse_text(text, out)
                else:
                    parse_text(text, out)
                break
        if res.get("isError"):
            out["error"] = "rag_status isError"
            out["state"] = "down"
            print(json.dumps(out, sort_keys=True))
            return 1
        out["state"] = state_of(out)
        out.pop("ok", None)
        print(json.dumps(out, sort_keys=True))
        return 0
    except Exception as exc:  # the lamp must never crash on a down service
        out["error"] = type(exc).__name__
        print(json.dumps(out))
        return 1


if __name__ == "__main__":
    sys.exit(main())
