#!/usr/bin/env python3
"""Record the long known-input probes of a probe file on a Forge, and compare two recordings.

The probe file is the Workstation's /etc/d32/gpu-probes/rerank.json (src/workstation/ship/u4/etc/d32/gpu-probes/
rerank.json; the model-bridge image carries a byte-identical copy as gpu-probes.json). Its "long" object holds the
inputs: "rerank" (model, query, 15 documents, tolerance) and "embed" (model, input, min_cosine). Recording adds what
the Forge's models answer: the reranker's order and scores, the embedder's vector, and "recorded" (card, UTC date).

  record --probe FILE --embed-url URL --rerank-url URL --token-file FILE --card NAME [--out FILE]
      one POST to <rerank-url>/v1/rerank and one to <embed-url>/v1/embeddings, with the Forge token as a bearer;
      writes the probe file (in place, or to --out) with only the long probes' recorded values changed
  compare A B
      two recordings of the same inputs: the largest per-passage score difference against A's tolerance, whether
      the order is the same, and the embeddings' cosine against A's min_cosine

URLs are http:// or https:// with no path; HTTPS verifies with the system's certificate authorities. No proxy
setting applies. The token is never printed. Exit 0: done (compare: within both limits); 1: an answer was refused,
or compare is outside a limit; 2: bad usage or input files. Standard library only.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import http.client
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import tempfile
from urllib.parse import urlsplit

TIMEOUT = 120
MAX_ANSWER = 16 * 1024 * 1024


class UsageError(ValueError):
    """Bad arguments or input files: exit 2."""


class RecordError(ValueError):
    """A refused or malformed answer: exit 1, nothing written."""


def read_token(path) -> str:
    """The Forge token: a file of 64 lowercase hex characters and at most one newline."""
    try:
        raw = Path(path).read_bytes()[:66]
    except OSError:
        raise UsageError("cannot read the token file") from None
    if re.fullmatch(rb"[0-9a-f]{64}\n?", raw) is None:
        raise UsageError("the token file must hold 64 lowercase hex characters")
    return raw[:64].decode("ascii")


def check_url(url: str) -> str:
    """An http:// or https:// URL with a host and no path, query, fragment or user information."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise UsageError("URLs must be http:// or https:// with a host and no path") from None
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.path or parts.query
            or parts.fragment or parts.username is not None or parts.password is not None
            or port is not None and not 1 <= port <= 65535):
        raise UsageError("URLs must be http:// or https:// with a host and no path")
    return url


def post(base: str, path: str, body: dict, token: str) -> tuple[int, bytes]:
    """POST body as JSON to base + path with the bearer token; (status, answer bytes). The only network code."""
    parts = urlsplit(base)
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=TIMEOUT,
                                           context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=TIMEOUT)
    try:
        conn.request("POST", path, body=json.dumps(body).encode("utf-8"),
                     headers={"Content-Type": "application/json", "Accept": "application/json",
                              "Authorization": "Bearer " + token})
        response = conn.getresponse()
        return response.status, response.read(MAX_ANSWER + 1)
    finally:
        conn.close()


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _inputs(data) -> tuple[dict, dict]:
    """The long probes' input objects, checked."""
    long = data.get("long") if isinstance(data, dict) else None
    rerank = long.get("rerank") if isinstance(long, dict) else None
    embed = long.get("embed") if isinstance(long, dict) else None
    if (not isinstance(rerank, dict) or not isinstance(embed, dict)
            or not isinstance(rerank.get("model"), str) or not isinstance(rerank.get("query"), str)
            or not isinstance(rerank.get("documents"), list) or not rerank["documents"]
            or any(not isinstance(d, str) or not d for d in rerank["documents"])
            or not isinstance(embed.get("model"), str) or not isinstance(embed.get("input"), str)
            or not embed["input"]):
        raise UsageError("the probe file has no complete long probe inputs")
    return rerank, embed


def _answer(name: str, status: int, raw: bytes):
    if status != 200:
        raise RecordError(f"{name} answered HTTP {status}")
    if len(raw) > MAX_ANSWER:
        raise RecordError(f"{name} answer is too large")
    try:
        return json.loads(raw)
    except ValueError:
        raise RecordError(f"{name} answer is not JSON") from None


def record(data: dict, embed_url: str, rerank_url: str, token: str, card: str, today: str, post=post) -> dict:
    """Return a copy of data with the long probes' order, scores, vector and recorded filled from the Forge."""
    result = copy.deepcopy(data)
    rerank, embed = _inputs(result)
    docs = rerank["documents"]
    try:
        status, raw = post(rerank_url, "/v1/rerank", {"model": rerank["model"], "query": rerank["query"],
                                                      "documents": docs, "top_n": len(docs)}, token)
    except (OSError, http.client.HTTPException) as exc:
        raise RecordError(f"the reranker could not be reached ({type(exc).__name__})") from None
    answer = _answer("the reranker", status, raw)
    results = answer.get("results") if isinstance(answer, dict) else None
    if (not isinstance(results, list) or len(results) != len(docs)
            or any(not isinstance(r, dict) or type(r.get("index")) is not int or not _number(r.get("relevance_score"))
                   for r in results)
            or sorted(r["index"] for r in results) != list(range(len(docs)))):
        raise RecordError("the reranker's answer does not score every passage once with a finite score")
    try:
        status, raw = post(embed_url, "/v1/embeddings", {"input": [embed["input"]], "model": embed["model"]}, token)
    except (OSError, http.client.HTTPException) as exc:
        raise RecordError(f"the embedder could not be reached ({type(exc).__name__})") from None
    answer = _answer("the embedder", status, raw)
    try:
        vector = answer["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        vector = None
    if not isinstance(vector, list) or not vector or any(not _number(v) for v in vector):
        raise RecordError("the embedder's answer has no vector of finite numbers")
    recorded = f"{card}, {today}"
    rerank.update(order=[r["index"] for r in results], scores=[float(r["relevance_score"]) for r in results],
                  recorded=recorded)
    embed.update(vector=[float(v) for v in vector], recorded=recorded)
    return result


def _recording(data) -> tuple[dict, dict]:
    rerank, embed = _inputs(data)
    n = len(rerank["documents"])
    order, scores, vector = rerank.get("order"), rerank.get("scores"), embed.get("vector")
    if (not isinstance(order, list) or not isinstance(scores, list) or len(order) != n or len(scores) != n
            or sorted(i for i in order if type(i) is int) != list(range(n)) or any(not _number(s) for s in scores)
            or not isinstance(vector, list) or not vector or any(not _number(v) for v in vector)):
        raise UsageError("both files must hold a complete recording")
    return rerank, embed


def compare(a: dict, b: dict) -> dict:
    """The numbers of two recordings of the same inputs, judged by A's tolerance and min_cosine."""
    ra, ea = _recording(a)
    rb, eb = _recording(b)
    if ra["query"] != rb["query"] or ra["documents"] != rb["documents"] or ea["input"] != eb["input"]:
        raise UsageError("the two files record different inputs")
    if len(ea["vector"]) != len(eb["vector"]):
        raise UsageError("the two vectors have different lengths")
    sa, sb = dict(zip(ra["order"], ra["scores"])), dict(zip(rb["order"], rb["scores"]))
    diff = max(abs(sa[i] - sb[i]) for i in sa)
    dot = sum(x * y for x, y in zip(ea["vector"], eb["vector"]))
    norm = math.sqrt(sum(x * x for x in ea["vector"])) * math.sqrt(sum(y * y for y in eb["vector"]))
    cosine = dot / norm if norm else 0.0
    tolerance, min_cosine = float(ra.get("tolerance", 0.005)), float(ea.get("min_cosine", 0.999))
    return {"max_diff": diff, "tolerance": tolerance, "same_order": ra["order"] == rb["order"],
            "cosine": cosine, "min_cosine": min_cosine, "ok": diff <= tolerance and cosine >= min_cosine}


def _load(path) -> dict:
    try:
        return json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        raise UsageError(f"cannot read {Path(path).name} as JSON") from None


def _write(path: Path, data: dict) -> None:
    """Write through a temporary file in the same folder and os.replace; a replaced file keeps its mode."""
    raw = (json.dumps(data, indent=1, ensure_ascii=True) + "\n").encode("ascii")
    path = path.absolute()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".record-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        if path.is_file():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    except OSError:
        raise UsageError(f"cannot write {path.name}") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    verbs = ap.add_subparsers(dest="verb", required=True)
    rec = verbs.add_parser("record", allow_abbrev=False, help="record the long probes on a Forge")
    rec.add_argument("--probe", required=True, type=Path)
    rec.add_argument("--embed-url", required=True)
    rec.add_argument("--rerank-url", required=True)
    rec.add_argument("--token-file", required=True, type=Path)
    rec.add_argument("--card", required=True, help='the GPU, for example "RTX 2080 Ti"')
    rec.add_argument("--out", type=Path, help="write here instead of replacing --probe")
    cmp = verbs.add_parser("compare", allow_abbrev=False, help="compare two recordings of the same inputs")
    cmp.add_argument("a", type=Path)
    cmp.add_argument("b", type=Path)
    return ap


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.verb == "record":
            embed_url, rerank_url = check_url(args.embed_url), check_url(args.rerank_url)
            card = args.card.strip()
            if not card or len(card) > 64 or not card.isprintable():
                raise UsageError("--card names the GPU in at most 64 printable characters")
            token = read_token(args.token_file)
            data = _load(args.probe)
            _inputs(data)
            today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
            result = record(data, embed_url, rerank_url, token, card, today, post=post)
            _write(args.out or args.probe, result)
            return 0
        numbers = compare(_load(args.a), _load(args.b))
    except UsageError as exc:
        print("record_gpu_probes: " + str(exc), file=sys.stderr)
        return 2
    except RecordError as exc:
        print("record_gpu_probes: " + str(exc), file=sys.stderr)
        return 1
    print(f"max score difference {numbers['max_diff']:.2e} (tolerance {numbers['tolerance']:g})")
    print(f"same order {'yes' if numbers['same_order'] else 'no'}")
    print(f"cosine {numbers['cosine']:.9f} (min_cosine {numbers['min_cosine']:g})")
    return 0 if numbers["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
