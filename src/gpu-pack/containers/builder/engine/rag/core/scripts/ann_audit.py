#!/usr/bin/env python3
"""Audit a root's dense HNSW search against exact search, read-only (tech radar V1).

usage: ann_audit.py --root /var/lib/deep32/rag-data/<generation> [--mode stored|golden|both]
                    [--sample 500] [--seed 20261101] [--k 10,50] [--hnsw-ef 256,512]
                    [--golden known_items] [--queries N] [--limit 8] [--max-chars 2400]
                    [--pause-ms 0] [--report-name ANN-AUDIT.json]

On deep32-engine it runs as a job beside the served root, as validate_root and derive_root do:
    python3 scripts/ann_audit.py --root /var/lib/deep32/rag-data/p81r-20261025 --mode stored
The job journal is the report (a job's files are 0640, which the dispatcher cannot read): one "audit: ..." line per
measure, the qids that differ, and a verdict line; --report-name also writes every row as JSON into the root.

Modes:
  stored  zero GPU. --sample stored points of the root's collection (chunks rag.db marks dense-indexed, drawn by
          random.Random(--seed) from their sorted chunk ids; vectors fetched by point id), each point's own vector as
          the query. Per point, the top max(--k) by HNSW as the root serves it (its retrieval.json "dense" settings;
          none: Qdrant's default), by HNSW at each --hnsw-ef N, and by exact search; the point itself is left out of
          every list (one more is fetched). Twice when the root has opt-in doc types: unfiltered, and with the default
          search's filter (must_not their documents, as api_v2.filter_docs and retrievers.dense.qdrant_filter build it
          for a search without filters). Per variant and arm, recall@k against exact (tie-aware: an HNSW hit scoring
          at least exact's k-th counts; "strict": plain overlap): mean, min, p5 and the share at 1.0; and every
          search's latency p50/p95/max, exact's being the full-scan cost of one query
  golden  reaches TEI and the reranker, as validate_root does (never in gaming mode). Every query of --golden through
          rag_core.api_v2.search_v2 in this process, the server's own /v2/search (the query's own filters, --limit,
          --max-chars, hybrid, rerank auto), once per arm: as served, exact (retrieval.dense.exact=true) and each
          --hnsw-ef N, the arms' order alternating from query to query. TEI and the reranker answer each distinct
          request once (a cache in front of their HTTP clients), so the arms differ in the dense leg's Qdrant search
          alone. Per arm against exact: the dense candidates (each dense leg's chunk ids in rank order), the fused
          top 15 (RRF order, before the rerank) and the final top --limit ids, identical in order (and as sets): the
          share of the queries both arms answered undegraded, and the qids that differ; the dense legs' recall of
          exact's candidates; the slowest dense leg's Qdrant time (timings_ms dense_qdrant) p50/p95 per arm. Verdict
          (radar V1): keep ANN if the fused top 15 as served is identical on >= 99.5% of the queries
  both    stored, then golden

Read-only: rag_core.db.connect is replaced in this process by a read-only connect (SQLite mode=ro: a database is
never created or written), Qdrant is reached through a client that refuses every call but its reads (get_collection,
retrieve, query_points, count), traces are off and the root's files are never written; --report-name writes one file
into the root and is refused for the served root ($RAG_ROOT), and when RAG_ROOT is unset (deployctl passes it to a
job; without it the served root cannot be told). The env keys that override a root's config overlay
(validate_root.OVERRIDE_KEYS) are removed, so the root's own config/*.json decides, as in validate_root's child.
A root that serves ColBERT is refused in golden mode (its index loader opens rag.db read-write).

Exit status: 0 measured (nothing is gated: the verdict line is for the reader), 1 failed, 2 refused.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import validate_root as vr  # noqa: E402  (log, write_json, report_name, OVERRIDE_KEYS, check_build_report, eval_v2)
from rag_core.config import load_config  # noqa: E402
from rag_core.dense import load_dense_config, make_qdrant_client, point_id_for_chunk  # noqa: E402
from rag_core.paths import RAGPaths  # noqa: E402
from rag_core.retrievers.dense import DenseSearchSettings, dense_search_settings, qdrant_filter  # noqa: E402

ev = vr.ev
log = vr.log
Refused = vr.Refused

AUDITOR = "scripts/ann_audit.py"
DEFAULT_GOLDEN = "known_items"     # the generated known-item set (eval/golden/known_items)
DEFAULT_SAMPLE = 500
DEFAULT_SEED = 20261101
DEFAULT_KS = "10,50"
FUSED_DEPTH = 15                # the fused top 15: p81r's rerank pool (retrieval.reranker.candidates=15)
KEEP_ANN_SHARE = 0.995          # radar V1: keep ANN if the fused top 15 is identical on >= 99.5% of queries
SCORE_EPS = 1e-6                # cosine scores within this of exact's k-th are ties
FETCH_BATCH = 64                # point ids per Qdrant retrieve
PROGRESS_EVERY = 100
QIDS_PER_LINE = 12
MAX_QID_LINES = 5
# the QdrantClient methods the audit may call: reads only
READ_METHODS = frozenset({
    "get_collection", "get_collections", "collection_exists", "retrieve", "query_points", "count", "info", "close",
})
EXACT = DenseSearchSettings(exact=True)


# ---------------------------------------------------------------- read-only access


class ReadOnlyQdrant:
    """A QdrantClient that only reads: any method but READ_METHODS raises PermissionError before a request."""

    def __init__(self, client) -> None:
        self._client = client

    def __getattr__(self, name: str):
        if name not in READ_METHODS:
            raise PermissionError(f"ann_audit is read-only: QdrantClient.{name} is not one of its reads")
        return getattr(self._client, name)


def read_only_connect(db_path) -> sqlite3.Connection:
    """rag_core.db.connect for a reader: the file opened read-only (mode=ro: never created, never written), with
    connect's row factory and read pragmas (not journal_mode or synchronous, which are the writer's)."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist (ann_audit opens databases read-only and never creates one)")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _rag_core_modules():
    return [m for name, m in list(sys.modules.items()) if name == "rag_core" or name.startswith("rag_core.")]


@contextlib.contextmanager
def read_only_sqlite():
    """Inside: rag_core.db.connect, and every rag_core module's `connect` bound to it (those imported inside too),
    is read_only_connect; afterwards each is the writer's connect again."""
    import rag_core.db as db
    original = db.connect
    try:
        with contextlib.ExitStack() as stack:
            for module in _rag_core_modules():
                if getattr(module, "connect", None) is original:
                    stack.enter_context(mock.patch.object(module, "connect", read_only_connect))
            yield
    finally:
        # a module first imported inside bound read_only_connect itself
        if original is not read_only_connect:
            for module in _rag_core_modules():
                if getattr(module, "connect", None) is read_only_connect:
                    module.connect = original


class CachedPost:
    """An httpx.Client stand-in whose post() answers a request it has seen (same URL and JSON body) from memory, so
    TEI and the reranker are asked once per distinct request whatever the number of arms. Only 2xx are kept."""

    def __init__(self, client) -> None:
        self._client = client
        self._seen: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()
        self.calls = 0
        self.hits = 0

    def post(self, url, *args, **kwargs):
        if args or "json" not in kwargs:
            return self._client.post(url, *args, **kwargs)
        key = (str(url), json.dumps(kwargs["json"], sort_keys=True, ensure_ascii=False))
        with self._lock:
            if key in self._seen:
                self.hits += 1
                return self._seen[key]
        response = self._client.post(url, **kwargs)
        with self._lock:
            self.calls += 1
            if 200 <= response.status_code < 300:
                self._seen[key] = response
        return response

    def __getattr__(self, name: str):
        return getattr(self._client, name)


class TraceRecorder:
    """The orchestrator's traces for the audit: each search's trace kept in memory (its raw hits hold the dense
    legs' candidates), nothing written."""

    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []

    def submit(self, run_id: str, trace: dict[str, Any]) -> None:
        self.traces.append(trace)
        return None

    def flush(self, timeout: float = 5.0) -> None:
        pass


# ---------------------------------------------------------------- numbers


def percentile(values: list[float], p: float) -> float | None:
    """The p-th percentile (nearest rank), None without values."""
    xs = sorted(values)
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, math.ceil(p / 100 * len(xs)) - 1))]


def spread(values: list[float]) -> dict[str, Any]:
    r = (lambda v: None if v is None else round(v, 2))
    return {"n": len(values), "p50": r(percentile(values, 50)), "p95": r(percentile(values, 95)),
            "max": r(max(values) if values else None)}


def distribution(values: list[float]) -> dict[str, Any]:
    r = (lambda v: None if v is None else round(v, 4))
    return {"n": len(values), "mean": r(sum(values) / len(values) if values else None),
            "min": r(min(values) if values else None), "p5": r(percentile(values, 5)),
            "at_1": r(sum(v >= 1.0 for v in values) / len(values) if values else None)}


def recall(ann: list[tuple[str, float]], exact: list[tuple[str, float]], k: int) -> tuple[float, float] | None:
    """(tie-aware, strict) recall@k of an ANN list against the exact list, both [(point id, score)] best first; None
    when exact has nothing. Tie-aware: an ANN hit scoring at least exact's k-th (within SCORE_EPS) is a true top-k
    neighbour even when exact listed another point of the same score."""
    truth = exact[:k]
    if not truth:
        return None
    want = {pid for pid, _ in truth}
    kth = truth[-1][1]
    got = ann[:k]
    strict = sum(pid in want for pid, _ in got) / len(truth)
    tied = min(len(truth), sum(pid in want or score >= kth - SCORE_EPS for pid, score in got)) / len(truth)
    return tied, strict


def share(n: int, of: int) -> float | None:
    return round(n / of, 4) if of else None


def fmt(value: Any) -> str:
    return "-" if value is None else str(value)


def log_qids(what: str, qids: list[str]) -> None:
    """The qids that differ, QIDS_PER_LINE per journal line, at most MAX_QID_LINES lines (the report has all)."""
    log(f"audit: {what} ({len(qids)}){':' if qids else ''}")
    for start in range(0, min(len(qids), QIDS_PER_LINE * MAX_QID_LINES), QIDS_PER_LINE):
        log("audit:   " + ",".join(qids[start:start + QIDS_PER_LINE]))
    if len(qids) > QIDS_PER_LINE * MAX_QID_LINES:
        log(f"audit:   ... {len(qids) - QIDS_PER_LINE * MAX_QID_LINES} more (--report-name lists them all)")


# ---------------------------------------------------------------- the root


def served_root() -> Path | None:
    served = os.environ.get("RAG_ROOT")
    return Path(served).expanduser().resolve() if served else None


def collection_facts(client, collection: str) -> dict[str, Any]:
    """What Qdrant says of the collection (status, counts, HNSW and vector config); {} parts it does not report."""
    info = client.get_collection(collection_name=collection)
    config = getattr(info, "config", None)
    vectors = getattr(getattr(config, "params", None), "vectors", None)
    hnsw = getattr(config, "hnsw_config", None)
    value = (lambda x: getattr(x, "value", x))
    return {
        "name": collection,
        "status": value(getattr(info, "status", None)),
        "points": getattr(info, "points_count", None),
        "indexed_vectors": getattr(info, "indexed_vectors_count", None),
        "segments": getattr(info, "segments_count", None),
        "vector_size": getattr(vectors, "size", None),
        "distance": value(getattr(vectors, "distance", None)),
        "vectors_on_disk": getattr(vectors, "on_disk", None),
        "hnsw": {key: getattr(hnsw, key, None) for key in ("m", "ef_construct", "full_scan_threshold", "on_disk")},
        "quantization": getattr(config, "quantization_config", None) is not None,
    }


def default_exclusion(root: Path, retrieval: dict[str, Any]) -> frozenset[str] | None:
    """The documents a search without filters leaves out (the opt-in doc types'), as api_v2.filter_docs builds them;
    None when the root has none."""
    from rag_core import api_v2
    from rag_core.reader import RootReader
    opt_in = api_v2.opt_in_doc_types(retrieval)
    if not opt_in:
        return None
    with read_only_sqlite():
        _, excluded = api_v2.filter_docs(RootReader(RAGPaths(root)), None, api_v2.Filters(), set(), opt_in)
    return excluded


# ---------------------------------------------------------------- stored


def sample_points(db_path: Path, n: int, seed: int) -> tuple[list[tuple[str, str]], int]:
    """(chunk_id, doc_id) of n chunks rag.db marks dense-indexed, drawn by random.Random(seed) from their sorted
    chunk ids, and how many there are."""
    with contextlib.closing(read_only_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT c.chunk_id, c.doc_id FROM chunks AS c JOIN index_state AS s ON s.chunk_id = c.chunk_id "
            "WHERE s.dense_indexed = 1 ORDER BY c.chunk_id"
        ).fetchall()
    pool = [(r["chunk_id"], r["doc_id"]) for r in rows]
    return random.Random(seed).sample(pool, min(n, len(pool))), len(pool)


def fetch_vectors(client, collection: str, picked: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Each picked chunk's point id and stored vector (None when Qdrant has no such point)."""
    out = []
    for start in range(0, len(picked), FETCH_BATCH):
        batch = picked[start:start + FETCH_BATCH]
        ids = [point_id_for_chunk(chunk_id) for chunk_id, _ in batch]
        found = {str(p.id): p for p in client.retrieve(collection_name=collection, ids=ids, with_vectors=True,
                                                         with_payload=False)}
        for (chunk_id, doc_id), point_id in zip(batch, ids):
            vector = getattr(found.get(point_id), "vector", None)
            out.append({"chunk_id": chunk_id, "doc_id": doc_id, "point_id": point_id,
                        "vector": [float(x) for x in vector] if isinstance(vector, list) and vector else None})
    return out


def top(client, collection: str, vector: list[float], limit: int, flt, settings: DenseSearchSettings
        ) -> tuple[list[tuple[str, float]], float]:
    """One Qdrant search with the settings' search params (none when unset, as the dense leg sends it): [(point id,
    score)] best first, and its milliseconds."""
    params = settings.search_params()
    started = time.perf_counter()
    response = client.query_points(collection_name=collection, query=vector, query_filter=flt, limit=limit,
                                   with_payload=False, with_vectors=False,
                                   **({"search_params": params} if params is not None else {}))
    ms = (time.perf_counter() - started) * 1000
    return [(str(p.id), float(p.score)) for p in response.points], ms


def without(points: list[tuple[str, float]], point_id: str, k: int) -> list[tuple[str, float]]:
    return [p for p in points if p[0] != point_id][:k]


def stored_audit(client, collection: str, db_path: Path, *, sample: int, seed: int, ks: list[int],
                 arms: dict[str, DenseSearchSettings], excluded: frozenset[str] | None,
                 pause: float = 0.0) -> dict[str, Any]:
    """Stored points as queries: every ANN arm's top max(ks) against exact's (module docstring, stored)."""
    started = time.monotonic()
    kmax = max(ks)
    picked, indexed = sample_points(db_path, sample, seed)
    points = fetch_vectors(client, collection, picked)
    missing = [p["chunk_id"] for p in points if p["vector"] is None]
    variants: dict[str, Any] = {"unfiltered": None}
    if excluded:
        variants["default_filter"] = qdrant_filter(None, excluded)
    log(f"audit: stored {len(points)} of {indexed} dense-indexed chunks (seed {seed}), {len(missing)} without a "
        f"point; variants {','.join(variants)}; arms {','.join(arms)} vs exact; top {kmax}")
    order = [*arms, "exact"]
    latency = {v: {a: [] for a in order} for v in variants}
    recalls = {v: {a: {k: {"tied": [], "strict": []} for k in ks} for a in arms} for v in variants}
    rows = []
    for i, point in enumerate(points):
        if point["vector"] is None:
            continue
        row: dict[str, Any] = {"chunk_id": point["chunk_id"], "doc_id": point["doc_id"]}
        for vname, flt in variants.items():
            lists = {}
            # the order alternates, so neither side always runs on a cache the other warmed
            for arm in (order if i % 2 == 0 else order[::-1]):
                found, ms = top(client, collection, point["vector"], kmax + 1, flt,
                                EXACT if arm == "exact" else arms[arm])
                latency[vname][arm].append(ms)
                lists[arm] = without(found, point["point_id"], kmax)
            entry: dict[str, Any] = {"exact_n": len(lists["exact"])}
            for arm in arms:
                for k in ks:
                    got = recall(lists[arm], lists["exact"], k)
                    if got is None:
                        continue
                    recalls[vname][arm][k]["tied"].append(got[0])
                    recalls[vname][arm][k]["strict"].append(got[1])
                    entry[f"{arm}@{k}"] = round(got[0], 4)
            row[vname] = entry
        rows.append(row)
        if (i + 1) % PROGRESS_EVERY == 0:
            log(f"audit: stored {i + 1}/{len(points)} points ({time.monotonic() - started:.1f} s)")
        if pause:
            time.sleep(pause)
    summary: dict[str, Any] = {"sample": sample, "seed": seed, "indexed": indexed, "points": len(rows),
                               "missing": missing, "ks": ks, "variants": {}}
    for vname in variants:
        out = summary["variants"][vname] = {
            "recall": {arm: {f"@{k}": {"tied": distribution(recalls[vname][arm][k]["tied"]),
                                       "strict": distribution(recalls[vname][arm][k]["strict"])}
                             for k in ks} for arm in arms},
            "latency_ms": {arm: spread(latency[vname][arm]) for arm in order},
        }
        for arm in arms:
            for k in ks:
                t, s = out["recall"][arm][f"@{k}"]["tied"], out["recall"][arm][f"@{k}"]["strict"]
                log(f"audit: stored {vname} arm={arm} recall@{k} mean={fmt(t['mean'])} min={fmt(t['min'])} "
                    f"p5={fmt(t['p5'])} at_1={fmt(t['at_1'])} strict_mean={fmt(s['mean'])} "
                    f"strict_at_1={fmt(s['at_1'])} n={t['n']}")
        log(f"audit: stored {vname} latency " + "; ".join(
            f"{arm} p50={fmt(out['latency_ms'][arm]['p50'])} p95={fmt(out['latency_ms'][arm]['p95'])} "
            f"max={fmt(out['latency_ms'][arm]['max'])} ms" for arm in order))
    summary["seconds"] = round(time.monotonic() - started, 1)
    log(f"audit: stored done in {summary['seconds']} s")
    return {"summary": summary, "rows": rows}


# ---------------------------------------------------------------- golden


def app_state(orch, root: Path, generation: str) -> SimpleNamespace:
    """What api_v2.search_v2 reads of rag_core.api's app state: the root, the orchestrator, the generation and one
    admission slot (the audit searches one query at a time)."""
    return SimpleNamespace(paths=RAGPaths(root), orchestrator=orch, generation=generation, reader=None,
                           search_slots=threading.BoundedSemaphore(1), search_queue_timeout=30.0)


class Capture:
    """What one search did besides its answer: the main fusion's ids (the RRF order, before any rerank) and the
    dense legs' candidates (from the trace)."""

    def __init__(self, traces: TraceRecorder) -> None:
        self.traces = traces
        self.fused: list[list[str]] = []

    def reset(self) -> None:
        self.fused = []
        self.traces.traces = []

    def dense(self) -> dict[str, list[str]]:
        """{variant id: the dense leg's chunk ids in rank order} of the last orchestrator search."""
        if not self.traces.traces:
            return {}
        legs: dict[str, list[tuple[int, str]]] = {}
        for hit in self.traces.traces[-1].get("raw_hits") or []:
            if hit.get("retriever") == "dense":
                legs.setdefault(str(hit["variant_id"]), []).append((int(hit["rank"]), str(hit["chunk_id"])))
        return {variant: [cid for _, cid in sorted(hits)] for variant, hits in sorted(legs.items())}


@contextlib.contextmanager
def recording_fusion(capture: Capture):
    """rag_core.orchestrator.rrf_fuse recording the main fusion's order (the call that passes keep_text; the
    challenge list's does not)."""
    from rag_core import orchestrator
    real = orchestrator.rrf_fuse

    def fuse(hits, **kwargs):
        out = real(hits, **kwargs)
        if "keep_text" in kwargs:
            capture.fused.append([str(c["chunk_id"]) for c in out])
        return out

    with mock.patch.object(orchestrator, "rrf_fuse", fuse):
        yield


def search_once(request, payload, dense, settings: DenseSearchSettings, capture: Capture) -> dict[str, Any]:
    """One /v2/search of the query (api_v2.search_v2) with the dense leg on these settings."""
    from fastapi import HTTPException
    from rag_core import api_v2
    from rag_core.errors import error_kind
    capture.reset()
    dense.search_settings = settings
    started = time.perf_counter()
    try:
        response = api_v2.search_v2(payload, request)
    except HTTPException as exc:
        return {"error": f"http_{exc.status_code}", "ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 - recorded per query; the audit goes on
        return {"error": error_kind(exc), "ms": round((time.perf_counter() - started) * 1000, 1)}
    timings = response.get("timings_ms") or {}
    return {
        "final": [str(h["id"]) for h in response.get("hits") or []],
        "fused": (capture.fused[0] if capture.fused else [])[:FUSED_DEPTH],
        "dense": capture.dense(),
        "degraded": list(response.get("degraded") or []),
        "dense_qdrant_ms": timings.get("dense_qdrant"),
        "total_ms": timings.get("total"),
    }


def dense_recall(mine: dict[str, list[str]], exact: dict[str, list[str]]) -> float | None:
    """The share of exact's dense candidates this arm's legs found, over the legs exact ran."""
    want = sum(len(ids) for ids in exact.values())
    if not want:
        return None
    return sum(len(set(mine.get(v) or []) & set(ids)) for v, ids in exact.items()) / want


def compare_golden(rows: list[dict[str, Any]], arm: str, limit: int) -> dict[str, Any]:
    """Arm against exact over the queries both answered undegraded: identical shares and the qids that differ."""
    clean = [r for r in rows if all("error" not in r["arms"][a] and not r["arms"][a]["degraded"]
                                    for a in (arm, "exact"))]
    differ: dict[str, list[str]] = {"dense": [], "fused": [], "fused_set": [], "final": [], "final_set": []}
    recalls = []
    for r in clean:
        a, e = r["arms"][arm], r["arms"]["exact"]
        if a["dense"] != e["dense"]:
            differ["dense"].append(r["qid"])
        if a["fused"] != e["fused"]:
            differ["fused"].append(r["qid"])
        if set(a["fused"]) != set(e["fused"]):
            differ["fused_set"].append(r["qid"])
        if a["final"] != e["final"]:
            differ["final"].append(r["qid"])
        if set(a["final"]) != set(e["final"]):
            differ["final_set"].append(r["qid"])
        got = dense_recall(a["dense"], e["dense"])
        if got is not None:
            recalls.append(got)
    n = len(clean)
    compared = {id(r) for r in clean}
    return {
        "n": len(rows), "clean": n,
        "unclean": [r["qid"] for r in rows if id(r) not in compared],
        "identical": {
            "dense": share(n - len(differ["dense"]), n),
            f"fused{FUSED_DEPTH}": share(n - len(differ["fused"]), n),
            f"fused{FUSED_DEPTH}_as_set": share(n - len(differ["fused_set"]), n),
            f"final{limit}": share(n - len(differ["final"]), n),
            f"final{limit}_as_set": share(n - len(differ["final_set"]), n),
        },
        "dense_recall": distribution(recalls),
        "differ": differ,
    }


def golden_audit(orch, request, golden, *, arms: dict[str, DenseSearchSettings], limit: int, max_chars: int,
                 queries: int = 0, pause: float = 0.0) -> dict[str, Any]:
    """The golden queries through the server's /v2/search once per arm (module docstring, golden). `arms` holds
    "exact" and the arms compared with it."""
    from rag_core import api_v2
    started = time.monotonic()
    dense = orch.retrievers["dense"]
    dense.client = ReadOnlyQdrant(dense.client)
    tei = CachedPost(dense.embedder._client)
    dense.embedder._client = tei
    rerank = None
    if orch.reranker is not None:
        rerank = orch.reranker._client = CachedPost(orch.reranker._client)
    traces = orch.traces = TraceRecorder()
    capture = Capture(traces)
    chosen = golden.queries[:queries] if queries else golden.queries
    names = list(arms)
    log(f"audit: golden {golden.path.name}: {len(chosen)} of {len(golden.queries)} queries, arms "
        f"{','.join(names)}, /v2/search limit {limit} max_chars {max_chars}")
    rows = []
    with recording_fusion(capture):
        for i, q in enumerate(chosen):
            row: dict[str, Any] = {"qid": q["qid"], "arms": {}}
            try:
                payload = api_v2.SearchRequest(query=q["text"], limit=limit, max_chars=max_chars,
                                               filters=api_v2.Filters(**(q.get("filters") or {})))
            except ValueError as exc:
                row["arms"] = {a: {"error": f"invalid: {str(exc)[:80]}"} for a in names}
                rows.append(row)
                continue
            for arm in (names if i % 2 == 0 else names[::-1]):
                row["arms"][arm] = search_once(request, payload, dense, arms[arm], capture)
            rows.append(row)
            if (i + 1) % PROGRESS_EVERY == 0:
                log(f"audit: golden {i + 1}/{len(chosen)} queries ({time.monotonic() - started:.1f} s; TEI "
                    f"{tei.calls} calls, reranker {rerank.calls if rerank else 0})")
            if pause:
                time.sleep(pause)
    dense.search_settings = arms.get("served", dense.search_settings)
    summary: dict[str, Any] = {
        "golden": golden.meta(), "queries": len(chosen), "limit": limit, "max_chars": max_chars,
        "tei": {"calls": tei.calls, "cached": tei.hits},
        "reranker": {"calls": rerank.calls, "cached": rerank.hits} if rerank else None,
        "latency_ms": {}, "compare": {},
    }
    for arm in names:
        answered = [r["arms"][arm] for r in rows if "error" not in r["arms"][arm]]
        summary["latency_ms"][arm] = {
            "dense_qdrant": spread([a["dense_qdrant_ms"] for a in answered if a.get("dense_qdrant_ms") is not None]),
            "total": spread([a["total_ms"] for a in answered if a.get("total_ms") is not None]),
            "errors": sum("error" in r["arms"][arm] for r in rows),
            "degraded": sum(bool(a["degraded"]) for a in answered),
        }
    for arm in names:
        if arm == "exact":
            continue
        c = summary["compare"][arm] = compare_golden(rows, arm, limit)
        same = c["identical"]
        log(f"audit: golden arm={arm} vs exact: n={c['n']} clean={c['clean']} dense_identical={fmt(same['dense'])} "
            f"fused{FUSED_DEPTH}_identical={fmt(same[f'fused{FUSED_DEPTH}'])} "
            f"(as sets {fmt(same[f'fused{FUSED_DEPTH}_as_set'])}) final{limit}_identical={fmt(same[f'final{limit}'])} "
            f"(as sets {fmt(same[f'final{limit}_as_set'])}) dense_recall_mean={fmt(c['dense_recall']['mean'])} "
            f"min={fmt(c['dense_recall']['min'])}")
        log_qids(f"golden arm={arm} differ fused{FUSED_DEPTH}", c["differ"]["fused"])
        log_qids(f"golden arm={arm} differ final{limit}", c["differ"]["final"])
        log_qids(f"golden arm={arm} differ dense", c["differ"]["dense"])
        if c["unclean"]:
            log_qids(f"golden arm={arm} not compared (an error or degraded answer in either arm)", c["unclean"])
    log("audit: golden latency " + "; ".join(
        f"{arm} dense_qdrant p50={fmt(summary['latency_ms'][arm]['dense_qdrant']['p50'])} "
        f"p95={fmt(summary['latency_ms'][arm]['dense_qdrant']['p95'])} ms "
        f"(errors {summary['latency_ms'][arm]['errors']}, degraded {summary['latency_ms'][arm]['degraded']})"
        for arm in names))
    log(f"audit: golden TEI {tei.calls} calls ({tei.hits} answered from the cache), reranker "
        f"{rerank.calls if rerank else 0} calls ({rerank.hits if rerank else 0} from the cache)")
    summary["seconds"] = round(time.monotonic() - started, 1)
    log(f"audit: golden done in {summary['seconds']} s")
    return {"summary": summary, "rows": rows}


def verdict(golden: dict[str, Any] | None) -> dict[str, Any] | None:
    """Radar V1's rule on the served arm: keep ANN if its fused top 15 is identical to exact's on >= 99.5%."""
    if golden is None or "served" not in golden["summary"]["compare"]:
        return None
    c = golden["summary"]["compare"]["served"]
    value = c["identical"][f"fused{FUSED_DEPTH}"]
    if c["unclean"] or value is None:
        decision = (f"inconclusive: {len(c['unclean'])} of {c['n']} queries errored or were degraded in an arm "
                    "(GPU services, Qdrant); run again")
    elif value >= KEEP_ANN_SHARE:
        decision = "keep ANN"
    else:
        decision = "flag exact or hnsw_ef 256-512 through the strict gate"
    return {"fused_identical": value, "threshold": KEEP_ANN_SHARE, "unclean": len(c["unclean"]),
            "decision": decision}


# ---------------------------------------------------------------- main


def parse_ints(text: str, what: str) -> list[int]:
    try:
        values = [int(part) for part in text.split(",") if part.strip()]
    except ValueError:
        raise Refused(f"{what} {text!r}: expected comma-separated integers") from None
    if any(v < 1 for v in values):
        raise Refused(f"{what} {text!r}: every value must be 1 or more")
    return values


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True, help="the root to audit (the served one too: nothing is written)")
    ap.add_argument("--mode", choices=("stored", "golden", "both"), default="stored")
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help="stored points (default 500)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--k", default=DEFAULT_KS, help="recall depths, comma-separated (default 10,50)")
    ap.add_argument("--hnsw-ef", default="", help="more HNSW arms, comma-separated hnsw_ef values (e.g. 256,512)")
    ap.add_argument("--golden", default=DEFAULT_GOLDEN, help=f"vendored golden set (default {DEFAULT_GOLDEN})")
    ap.add_argument("--queries", type=int, default=0, help="only the first N golden queries (0: all)")
    ap.add_argument("--limit", type=int, default=8, help="/v2/search limit (the edge's 8)")
    ap.add_argument("--max-chars", type=int, default=2400, help="/v2/search max_chars (the edge's 2400)")
    ap.add_argument("--pause-ms", type=float, default=0.0, help="pause after each point or query")
    ap.add_argument("--report-name", help="also write the rows as JSON into the root (never the served root)")
    return ap.parse_args(argv)


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Every refusal, before anything is searched."""
    root = Path(args.root).expanduser().resolve()
    if not (root / "sqlite" / "rag.db").is_file():
        raise Refused(f"--root {root}: no sqlite/rag.db")
    report_path = root / vr.BUILD_REPORT
    if report_path.is_file():
        try:
            vr.check_build_report(json.loads(report_path.read_text(encoding="utf-8")))
        except ValueError as exc:
            raise Refused(f"{report_path} is not JSON: {exc}") from None
    served = served_root()
    is_served = served is not None and root == served
    name = vr.report_name(args.report_name) if args.report_name else None
    if name and is_served:
        raise Refused(f"--report-name: {root} is the served root (RAG_ROOT), which the audit only reads; the journal "
                      "carries the summary")
    if name and served is None:
        raise Refused("--report-name: RAG_ROOT is not set here, so the audit cannot tell whether --root is the served "
                      "root; run without --report-name (the journal carries the summary)")
    ks = parse_ints(args.k, "--k")
    efs = parse_ints(args.hnsw_ef, "--hnsw-ef") if args.hnsw_ef else []
    if not ks or args.sample < 1 or args.queries < 0 or args.pause_ms < 0:
        raise Refused("--k needs a depth, --sample 1 or more, --queries and --pause-ms 0 or more")
    if not 1 <= args.limit <= 30 or not 200 <= args.max_chars <= 4000:
        raise Refused("--limit 1-30 and --max-chars 200-4000 (what /v2/search takes)")
    # the root's own config decides, as in validate_root's child
    for key in vr.OVERRIDE_KEYS:
        os.environ.pop(key, None)
    os.environ["RAG_TRACE"] = "off"
    retrieval = load_config("retrieval", root=root)
    retrievers = retrieval.get("retrievers", ["bm25", "dense", "colbert"])
    if "dense" not in retrievers:
        raise Refused(f"{root} serves no dense retriever (retrievers {retrievers}): nothing to audit")
    if args.mode in ("golden", "both") and "colbert" in retrievers:
        raise Refused(f"{root} serves colbert, whose index loader opens rag.db read-write: golden mode audits "
                      "bm25 + dense roots")
    try:
        served_settings = dense_search_settings(retrieval.get("dense"))
    except ValueError as exc:
        raise Refused(f"{root}: {exc}") from None
    golden = None
    if args.mode in ("golden", "both"):
        try:
            golden = ev.load_golden(args.golden)
        except ev.GoldenError as exc:
            raise Refused(f"golden set {args.golden}: {exc}") from None
    arms = {"served": served_settings, **{f"ef{n}": DenseSearchSettings(hnsw_ef=n) for n in efs}}
    return {"root": root, "served": served, "is_served": is_served, "report_name": name, "ks": ks, "arms": arms,
            "retrieval": retrieval, "golden": golden}


def build_orchestrator(root: Path):
    """The root's retrieval stack as rag_core.api's lifespan starts it (BM25 warmed), with read-only SQLite."""
    from rag_core.orchestrator import RetrievalOrchestrator
    orch = RetrievalOrchestrator(RAGPaths(root))
    if "dense" not in orch.retrievers:
        raise RuntimeError(f"the dense retriever did not start: {orch.degraded.get('dense')}")
    orch.warm()
    return orch


def code_commit() -> str | None:
    with contextlib.suppress(Exception):
        out = subprocess.run(["git", "-C", str(vr.REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10)
        return out.stdout.strip() or None
    return None


def main(argv: list[str] | None = None,
         make_client: Callable[[Any], Any] = make_qdrant_client,
         make_orchestrator: Callable[[Path], Any] = build_orchestrator) -> int:
    args = parse_args(argv)
    try:
        state = preflight(args)
    except Refused as exc:
        log(f"REFUSED: {exc}")
        return 2
    root = state["root"]
    os.environ["RAG_ROOT"] = str(root)          # as validate_root's child: anything that reads RAG_ROOT reads this root
    served = state["served"]
    log(f"audit: root {root} ({'the served root, ' if state['is_served'] else ''}read-only; RAG_ROOT was "
        f"{served if served else 'unset'})")
    report: dict[str, Any] = {"auditor": AUDITOR, "root": str(root), "served_root": state["is_served"],
                              "started_at": vr.now(), "code_commit": code_commit(), "args": vars(args),
                              "arms": {name: s.shown() for name, s in state["arms"].items()}}
    try:
        with read_only_sqlite():
            config = load_dense_config(root=root)
            served_settings = state["arms"]["served"]
            log(f"audit: collection {config.collection} at {config.qdrant_url}; served dense search settings "
                f"{json.dumps(served_settings.shown()) if served_settings.shown() else 'none (Qdrant default hnsw_ef)'}")
            if args.mode in ("stored", "both"):
                client = ReadOnlyQdrant(make_client(config))
                try:
                    facts = report["collection"] = collection_facts(client, config.collection)
                    log(f"audit: collection {facts['status']} points={facts['points']} "
                        f"indexed_vectors={facts['indexed_vectors']} segments={facts['segments']} "
                        f"hnsw={json.dumps(facts['hnsw'], separators=(',', ':'))} "
                        f"quantization={facts['quantization']} vectors_on_disk={facts['vectors_on_disk']}")
                    excluded = default_exclusion(root, state["retrieval"])
                    report["default_filter_documents"] = len(excluded or ())
                    report["stored"] = stored_audit(
                        client, config.collection, root / "sqlite" / "rag.db", sample=args.sample, seed=args.seed,
                        ks=state["ks"], arms=state["arms"], excluded=excluded, pause=args.pause_ms / 1000)
                finally:
                    client.close()
            if args.mode in ("golden", "both"):
                orch = make_orchestrator(root)
                serving = load_config("serving", root=root)
                request = SimpleNamespace(app=SimpleNamespace(state=app_state(
                    orch, root, str(serving.get("generation") or root.name))))
                report["golden"] = golden_audit(
                    orch, request, state["golden"], arms={**state["arms"], "exact": EXACT}, limit=args.limit,
                    max_chars=args.max_chars, queries=args.queries, pause=args.pause_ms / 1000)
                decided = report["verdict"] = verdict(report["golden"])
                if decided is not None:
                    log(f"audit: verdict served fused{FUSED_DEPTH}_identical={fmt(decided['fused_identical'])} "
                        f"(threshold {KEEP_ANN_SHARE}, {decided['unclean']} not compared): {decided['decision']} "
                        "(radar V1)")
    except Exception as exc:  # noqa: BLE001 - reported; nothing was written
        log(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    report["finished_at"] = vr.now()
    if state["report_name"]:
        vr.write_json(root / state["report_name"], report)
        log(f"audit: report {root / state['report_name']}")
    log("audit: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
