"""Second-stage reranking of fused candidates through the GPU reranker on the Forge.

retrieval.json:
    "reranker": {
        "enabled": true,
        "url": "https://<forge>.<tailnet>.ts.net:8443/v1/rerank",   (env RAG_RERANK_URL overrides)
        "model": "jina-reranker-v3.5",
        "candidates": 10,          fused candidates sent for reranking (the service accepts at most 64)
        "search_many_candidates": null,   /v2/search_many's head (rag_core.api_v2.rerank_head): null takes
                                   candidates; an integer of 1 or more (at most 64), so a deeper /v2/search
                                   can keep search_many's head where it was
        "max_chars": 2400,         per document, heading path + chunk text
        "window": "head",          which max_chars of a longer chunk: "head" (its start) or "centred"
                                   (around the lines that match the query best, as /v2 evidence windows)
        "title": false,            true: a first line with the document's title, and its release when the
                                   structure index stores one (title_lines), in front of the candidate and
                                   outside max_chars: the text the service reads of the passage does not shrink
        "covered_titles": false,   true: the headings of the sections a merged passage covers that its heading
                                   path lacks (covered_titles_of) are added to the heading line, which counts
                                   in max_chars as it always did; at most COVERED_TITLES_CHARS of them
        "timeout_seconds": 15,     one rerank call, end to end
        "connect_timeout_seconds": 5,   TCP + TLS connect (default: min(5, timeout_seconds))
        "connect_retries": 1,      new-connection attempts repeated on a connect failure
        "keepalive_seconds": 300,  how long an idle connection is kept (Serve keeps them > 330 s)
        "breaker": {"failures": 3, "open_seconds": 30},
        "token_env": null          env var holding a bearer token, if the service requires one
    }
The GPU reranker (127.0.0.1:8082 on the Forge) is reached from deep32-engine through
Tailscale Serve on :8443 of the Forge's tailnet name, as TEI is on :443. The defaults are the configuration the
A/B measured (arm v3-bd-tb-rr10: top 10, 15 s timeout). Larger pools were never measured and cost more per call,
so under a shorter timeout they would mostly fall back.

Any failure is a degradation: the fused order stands and the error is reported. Like dense, the reranker has a
circuit breaker (rag_core.breaker): after `failures` consecutive failed calls, searches skip the reranker for
`open_seconds` (degraded "reranker", no call and no wait), then one trial call decides. So a GPU host that is down,
in gaming mode or hung costs at most `failures` timeouts, not one per search.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from rag_core.breaker import CircuitBreaker
from rag_core.db import connect
from rag_core.reader import chunk_window, query_terms
from rag_core.structure import has_structure

MAX_SERVICE_DOCUMENTS = 64
COVERED_TITLES_CHARS = 300          # reranker.covered_titles: the most characters the added headings may take
WINDOWS = ("head", "centred")       # "window"; the first is the default
DEFAULT_CANDIDATES = 10
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_KEEPALIVE_SECONDS = 300.0
DEFAULT_BREAKER = {"failures": 3, "open_seconds": 30}


def title_lines(db_path) -> dict[str, str]:
    """reranker.title: {doc_id: the line that names the document}, from the structure index (doc_sources) of the
    root's rag.db: "RFC 4271: A Border Gateway Protocol 4 (BGP-4)" for an RFC (doc_type rfc, doc_key rfcNNNN),
    else the title (the doc_key when there is none), then " | release 17.9.x" when a release is stored.
    {} for a root without the structure index."""
    with connect(db_path) as conn:
        if not has_structure(conn):
            return {}
        rows = conn.execute("SELECT doc_id, doc_key, doc_type, title, release FROM doc_sources").fetchall()
    lines = {}
    for r in rows:
        key = r["doc_key"]
        line = r["title"] or key
        if r["doc_type"] == "rfc" and key.startswith("rfc") and key[3:].isdigit() and r["title"]:
            line = f"RFC {int(key[3:])}: {r['title']}"
        lines[r["doc_id"]] = f"{line} | release {r['release']}" if r["release"] else line
    return lines


def covered_titles_of(db_path, chunk_ids: list[str]) -> dict[str, list[str]]:
    """reranker.covered_titles: {chunk_id: the headings of the sections the passage covers, in document order},
    for the passages merged from two sections or more (chunk_spans.covered_json, doc_sections.heading; a
    section without a heading is left out). A chunk with fewer has no entry; {} without the structure index."""
    if not chunk_ids:
        return {}
    with connect(db_path) as conn:
        if not has_structure(conn):
            return {}
        marks = ",".join("?" for _ in chunk_ids)
        covered = {
            r["chunk_id"]: json.loads(r["covered_json"] or "[]")
            for r in conn.execute(f"SELECT chunk_id, covered_json FROM chunk_spans WHERE chunk_id IN ({marks})",  # nosec B608 - only "?" placeholders are interpolated; the values are bound
                                  chunk_ids)
        }
        wanted = sorted({s for ids in covered.values() if len(ids) >= 2 for s in ids})
        headings = {}
        for at in range(0, len(wanted), 500):
            part = wanted[at:at + 500]
            headings.update(conn.execute(
                f"SELECT section_id, heading FROM doc_sections WHERE section_id IN ({','.join('?' for _ in part)})",  # nosec B608 - only "?" placeholders are interpolated; the values are bound
                part).fetchall())
    out = {}
    for chunk_id, ids in covered.items():
        titles = [headings[s].strip() for s in ids if (headings.get(s) or "").strip()] if len(ids) >= 2 else []
        if titles:
            out[chunk_id] = titles
    return out


class Reranker:
    # reranker.title: {doc_id: title line} (title_lines), filled by the orchestrator when the setting is on
    titles: dict[str, str] = {}
    # reranker.covered_titles: chunk ids -> {chunk_id: headings} (covered_titles_of), set by the orchestrator
    covered = None

    def __init__(self, cfg: dict[str, Any], *, transport: httpx.BaseTransport | None = None) -> None:
        url = os.environ.get("RAG_RERANK_URL") or cfg.get("url")
        parts = urlsplit(str(url or ""))
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("reranker url must be an http:// or https:// URL")
        self.url = str(url)
        self.model = str(cfg.get("model", "jina-reranker-v3.5"))
        self.candidates = max(1, min(MAX_SERVICE_DOCUMENTS, int(cfg.get("candidates", DEFAULT_CANDIDATES))))
        many = cfg.get("search_many_candidates")
        if many is not None and (isinstance(many, bool) or not isinstance(many, int) or many < 1):
            raise ValueError(f"reranker search_many_candidates must be null or an integer of 1 or more, not {many!r}")
        self.search_many_candidates = self.candidates if many is None else min(MAX_SERVICE_DOCUMENTS, many)
        self.max_chars = max(200, int(cfg.get("max_chars", 2400)))
        self.window = cfg.get("window", WINDOWS[0])
        if self.window not in WINDOWS:
            raise ValueError(f"reranker window must be one of {list(WINDOWS)}, not {self.window!r}")
        for key in ("title", "covered_titles"):
            if not isinstance(cfg.get(key, False), bool):
                raise ValueError(f"reranker {key} must be true or false, not {cfg[key]!r}")
        self.title = cfg.get("title", False)
        self.covered_titles = cfg.get("covered_titles", False)
        timeout = float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        connect = float(cfg.get("connect_timeout_seconds", min(DEFAULT_CONNECT_TIMEOUT_SECONDS, timeout)))
        retries = int(cfg.get("connect_retries", 1))
        keepalive = float(cfg.get("keepalive_seconds", DEFAULT_KEEPALIVE_SECONDS))
        if not timeout > 0 or not connect > 0 or retries < 0 or keepalive < 0:
            raise ValueError("reranker timeout_seconds and connect_timeout_seconds must be > 0, "
                             "connect_retries and keepalive_seconds >= 0")
        self.timeout_seconds = timeout
        self.connect_timeout_seconds = connect
        breaker = cfg.get("breaker") or {}
        if not isinstance(breaker, dict):
            raise ValueError("reranker breaker must be an object {failures, open_seconds}")
        self.breaker = CircuitBreaker(
            failures=breaker.get("failures", DEFAULT_BREAKER["failures"]),
            open_seconds=breaker.get("open_seconds", DEFAULT_BREAKER["open_seconds"]),
        )
        token_env = cfg.get("token_env")
        token = os.environ.get(token_env, "") if token_env else ""
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        # the limits go to the transport: httpx.Client ignores its own when given one (default: 5 s keep-alive)
        limits = httpx.Limits(max_connections=16, max_keepalive_connections=8, keepalive_expiry=keepalive)
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=connect),
            limits=limits,
            transport=transport or httpx.HTTPTransport(retries=retries, limits=limits),
        )

    def close(self) -> None:
        self._client.close()

    def document(self, item: dict[str, Any], query: str | None = None, covered: list[str] | None = None) -> str:
        """What the service reads of a candidate: its heading path, then its text, max_chars in all.
        Window "head": the text's first characters. "centred", given the query: when the text does
        not fit, the lines that cover the most query terms and those around them
        (reader.chunk_window), so a long chunk is read where it matches, not only where it starts.

        `covered` (read only with reranker.covered_titles; rerank() passes it): the headings of the sections
        the passage covers. Those the heading path does not hold (compared without case) follow it on the heading line as
        " | covers: A; B", added one by one while they take at most COVERED_TITLES_CHARS characters in all;
        the line counts in max_chars as before. reranker.title: the document's line (self.titles, by the
        item's doc_id) comes first, on a line of its own, and is not counted in max_chars."""
        path = item.get("heading_path") or []
        heading = " > ".join(path)
        if covered and self.covered_titles:
            have = {h.casefold() for h in path}
            more: list[str] = []
            for title in covered:
                if title.casefold() in have:
                    continue
                if len("; ".join([*more, title])) > COVERED_TITLES_CHARS:
                    break
                more.append(title)
                have.add(title.casefold())
            if more:
                heading = f"{heading} | covers: {'; '.join(more)}" if heading else f"covers: {'; '.join(more)}"
        text = str(item.get("text") or item.get("snippet") or "")
        if self.window == "centred" and query:
            room = self.max_chars - (len(heading) + 1 if heading else 0)
            if 0 < room < len(text):
                text = chunk_window(text, query_terms(query), room)
        document = (f"{heading}\n{text}" if heading else text)[: self.max_chars]
        line = self.titles.get(item.get("doc_id")) if self.title else None
        return f"{line}\n{document}" if line else document

    def rerank(self, query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return `items` reordered by relevance; each gets "rerank_score" and "fused_rank"."""
        if not items:
            return []
        covered = self.covered([i["chunk_id"] for i in items]) if self.covered_titles and self.covered else {}
        response = self._client.post(
            self.url,
            headers=self._headers,
            json={
                "model": self.model,
                "query": query,
                "documents": [self.document(i, query, covered.get(i.get("chunk_id"))) for i in items],
                "top_n": len(items),
            },
        )
        response.raise_for_status()
        results = response.json().get("results")
        if not isinstance(results, list):
            raise RuntimeError("rerank response missing results[]")
        seen: set[int] = set()
        ordered: list[dict[str, Any]] = []
        for r in results:
            idx = int(r["index"])
            if not 0 <= idx < len(items) or idx in seen:
                raise RuntimeError(f"rerank response has invalid index {idx}")
            seen.add(idx)
            item = items[idx]
            item["fused_rank"] = idx + 1
            item["rerank_score"] = float(r["relevance_score"])
            ordered.append(item)
        for idx, item in enumerate(items):          # anything the service did not return keeps fused order after
            if idx not in seen:
                item["fused_rank"] = idx + 1
                ordered.append(item)
        return ordered

    def probe(self, timeout: float) -> dict[str, Any]:
        """Live check for /v2/status: rerank one short document. Raises on failure."""
        started = time.perf_counter()
        response = self._client.post(
            self.url,
            headers=self._headers,
            json={"model": self.model, "query": "status probe", "documents": ["status probe"], "top_n": 1},
            timeout=timeout,
        )
        response.raise_for_status()
        if not isinstance(response.json().get("results"), list):
            raise RuntimeError("rerank response missing results[]")
        return {"latency_ms": round((time.perf_counter() - started) * 1000, 1)}
