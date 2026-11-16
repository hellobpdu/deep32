#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import shutil
import signal
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import validate_root as vr  # noqa: E402  (its child server, readiness wait and report helpers)

ev = vr.ev
BENCHER = "scripts/bench_pipeline.py"
REPORT_NAME = "BENCH-PIPELINE.json"
DEFAULT_PORT = 18766
SEARCH_LIMIT = 8
MAX_CHARS = 2400
# rerank_wait_seconds: at least this many times the p99 a search_many holds the gate, and at least the floor
WAIT_FACTOR = 3
WAIT_FLOOR_SECONDS = 10.0
RERANKED_ROUTES = ("/v2/search", "/v2/search_many")
# rag_core.api's admission defaults (admission_settings, DEFAULT_MAX_QUEUED_SEARCHES), read here without importing
# the serving stack; the repo's serving.json sets both anyway
DEFAULT_SLOTS = 1
DEFAULT_QUEUED = 16


def percentile(values: list[float], p: float) -> float | None:
    """The p-th percentile (nearest rank) of the values, None without any."""
    xs = sorted(values)
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, math.ceil(p / 100 * len(xs)) - 1))]


def spread(values: list[float]) -> dict[str, float | None]:
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95),
            "p99": percentile(values, 99), "max": max(values) if values else None}


def call(http, route: str, body: dict[str, Any]) -> dict[str, Any]:
    """One request: its route, HTTP status, 503 retries, client ms and what the answer says."""
    started = time.perf_counter()
    try:
        status, _, payload, retries = http.request("POST", route, body)
    except Exception as exc:  # noqa: BLE001 - recorded, the run goes on
        return {"route": route, "status": None, "error": type(exc).__name__,
                "ms": round((time.perf_counter() - started) * 1000, 1)}
    row: dict[str, Any] = {"route": route, "status": status, "retried": retries,
                           "ms": round((time.perf_counter() - started) * 1000, 1)}
    if status == 200:
        answer = json.loads(payload)
        timings = answer.get("timings_ms") or {}
        row.update(degraded=answer.get("degraded") or [],
                   **{k: timings[k] for k in ("total", "rerank", "rerank_wait") if k in timings})
        if route == "/v2/grep":
            row["truncated"] = bool(answer.get("truncated"))
    return row


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per route: counts and the spreads (ms) of client latency and the server's timings."""
    out: dict[str, Any] = {}
    for route in sorted({r["route"] for r in rows}):
        mine = [r for r in rows if r["route"] == route]
        ok = [r for r in mine if r["status"] == 200]
        reranked = route != "/v2/grep"
        out[route] = {
            "calls": len(mine),
            "ok": len(ok),
            "errors": sum(r["status"] not in (200, 503) for r in mine),
            "http_503": sum(r["status"] == 503 for r in mine),
            "retried": sum(r.get("retried") or 0 for r in mine),
            "degraded": sum(bool(r.get("degraded")) for r in ok),
            "degraded_by_kind": {k: sum(k in (r.get("degraded") or ()) for r in ok)
                                 for k in sorted({k for r in ok for k in r.get("degraded") or ()})},
            **({"unreranked": sum(r.get("rerank") is None for r in ok)} if reranked else {}),
            **({"truncated": sum(r.get("truncated", False) for r in ok)} if not reranked else {}),
            "client_ms": spread([r["ms"] for r in ok]),
            "total_ms": spread([r["total"] for r in ok if isinstance(r.get("total"), (int, float))]),
            **({"rerank_ms": spread([r["rerank"] for r in ok if isinstance(r.get("rerank"), (int, float))]),
                "rerank_wait_ms": spread([r["rerank_wait"] for r in ok
                                          if isinstance(r.get("rerank_wait"), (int, float))])}
               if reranked else {}),
        }
    return out


def gate_hold(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How long one search_many held the rerank gate (its rerank), and the rerank_wait_seconds that sizes."""
    held = [r["rerank"] for r in rows if r["route"] == "/v2/search_many" and r["status"] == 200
            and isinstance(r.get("rerank"), (int, float))]
    p99 = percentile(held, 99)
    return {"search_many_rerank_ms": spread(held),
            "suggested_rerank_wait_seconds": None if p99 is None else
            max(WAIT_FLOOR_SECONDS, round(WAIT_FACTOR * p99 / 1000, 1))}


def pipeline_status(http) -> dict[str, Any] | None:
    """/v2/status "pipeline" (the rerank gate's counters), None when the root runs none."""
    status, _ = http.json("GET", "/v2/status")
    return status.get("pipeline")


def gate_counts(before: dict[str, Any] | None, after: dict[str, Any] | None,
                rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Public knowledge-base source helper."""
    calls = [r for r in rows if r["route"] in RERANKED_ROUTES]
    applied = sum(r["status"] == 200 and isinstance(r.get("rerank"), (int, float))
                  and "reranker" not in (r.get("degraded") or ()) for r in calls)
    out: dict[str, Any] = {"gate_timeouts": None, "breaker_opens": None,
                           "rerank_applied": round(applied / len(calls), 4) if calls else None, "calls": len(calls)}
    gate = (after or {}).get("rerank_gate")
    if gate is None:
        return out
    start = (before or {}).get("rerank_gate") or {}
    delta = {k: gate[k] - start.get(k, 0) for k in ("passed", "refused", "timed_out", "overdue")}
    wait_ms = gate["wait_seconds"] * 1000
    late = sum(isinstance(r.get("rerank_wait"), (int, float)) and r["rerank_wait"] >= wait_ms for r in calls)
    opens = [(s or {}).get("reranker_breaker_opens") for s in (before, after)]
    out.update(
        gate_timeouts=max(delta["timed_out"] + delta["overdue"], late, int(gate["max_wait_ms"] >= wait_ms)),
        breaker_opens=opens[1] - (opens[0] or 0) if isinstance(opens[1], int) else None,
        **delta, late=late, max_wait_ms=gate["max_wait_ms"], wait_ms=wait_ms, on_timeout=gate["on_timeout"],
    )
    return out


def counts_line(counts: dict[str, Any]) -> str:
    """gate_counts() as ops/trials-p9.sh reads it: 'bench: key=value ...', none for a count not measured."""
    return "bench: " + " ".join(f"{k}={'none' if v is None else v}" for k, v in counts.items())


def root_admission(root: Path) -> tuple[int, int]:
    """(searches admitted at once, searches that may wait): the root's serving.json as its child reads it (the repo
    file and the root's overlay; validate_root removes the env keys that would override them)."""
    try:
        config = vr.load_config("serving", root=root)
        slots = int(config.get("max_concurrent_searches", DEFAULT_SLOTS))
        queued = config.get("max_queued_searches", DEFAULT_QUEUED)
        if isinstance(queued, bool) or not isinstance(queued, int):
            raise ValueError(f"max_queued_searches {queued!r}")
    except (OSError, TypeError, ValueError) as exc:
        raise vr.Refused(f"{root}: serving.json admission: {exc}") from None
    return slots, queued


def run_bench(http, queries: list[str], *, c1: int, bursts: int, burst: int, per_many: int, searches: int,
              greps: list[dict[str, Any]]) -> dict[str, Any]:
    """The phases against a served root (`http`: validate_root's ev.Http, or anything with its request/json)."""
    if not queries:
        raise ValueError("no queries to send")

    def text(n: int) -> str:
        return queries[n % len(queries)]

    report: dict[str, Any] = {"pipeline_before": pipeline_status(http), "phases": {}}
    rows = [call(http, "/v2/search", {"query": text(n), "limit": SEARCH_LIMIT, "max_chars": MAX_CHARS})
            for n in range(c1)]
    report["phases"]["c1"] = {"rows": rows, "summary": summary(rows)}

    rows = []
    cursor = 0
    with ThreadPoolExecutor(max_workers=max(1, burst + searches + len(greps))) as pool:
        for _ in range(bursts):
            jobs = []
            for _ in range(burst):
                subs = [{"text": text(cursor + i), "mode": "hybrid"} for i in range(per_many)]
                cursor += per_many
                jobs.append(("/v2/search_many", {"queries": subs, "limit": SEARCH_LIMIT, "max_chars": MAX_CHARS}))
            for _ in range(searches):
                jobs.append(("/v2/search", {"query": text(cursor), "limit": SEARCH_LIMIT, "max_chars": MAX_CHARS}))
                cursor += 1
            jobs += [("/v2/grep", body) for body in greps]
            rows += list(pool.map(lambda job: call(http, *job), jobs))
    report["phases"]["burst"] = {"rows": rows, "summary": summary(rows), "gate_hold": gate_hold(rows)}
    report["pipeline_after"] = pipeline_status(http)
    report["gate_counts"] = gate_counts(report["pipeline_before"], report["pipeline_after"],
                                        [r for phase in report["phases"].values() for r in phase["rows"]])
    return report


def load_greps(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    bodies = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        body = json.loads(line)
        if not isinstance(body, dict) or not isinstance(body.get("pattern"), str):
            raise ValueError(f"{path}:{n}: not a /v2/grep body (an object with a pattern)")
        bodies.append(body)
    return bodies


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--root", required=True)
    p.add_argument("--golden", default="known_items", help="the golden set whose queries are sent (eval/golden)")
    p.add_argument("--c1", type=int, default=30, help="/v2/search one after another first")
    p.add_argument("--bursts", type=int, default=3)
    p.add_argument("--burst", type=int, default=4, help="/v2/search_many at once in a burst")
    p.add_argument("--queries", type=int, default=8, help="sub-queries of each search_many (1-8)")
    p.add_argument("--searches", type=int, default=0, help="/v2/search beside each burst")
    p.add_argument("--greps", default=None, help="JSON lines of /v2/grep bodies sent beside each burst")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--report-name", default=REPORT_NAME)
    p.add_argument("--startup-timeout", type=float, default=300.0)
    p.add_argument("--ready-timeout", type=float, default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    try:
        vr.check_not_served(root)
        if not (root / "sqlite" / "rag.db").is_file():
            raise vr.Refused(f"{root} holds no sqlite/rag.db: not a built root")
        report_file = vr.report_name(args.report_name)
        if not 1 <= args.queries <= 8 or min(args.c1, args.bursts, args.burst, args.searches) < 0:
            raise vr.Refused("--queries must be 1-8, and --c1, --bursts, --burst and --searches >= 0")
        try:
            golden = ev.load_golden(args.golden)
            greps = load_greps(args.greps)
        except (ev.GoldenError, OSError, ValueError) as exc:
            raise vr.Refused(str(exc)) from None
        queries = [q["text"] for q in golden.queries if q.get("text")]
        slots, queued = root_admission(root)
        if args.burst + args.searches + len(greps) > slots + queued:
            raise vr.Refused(
                f"a burst sends {args.burst + args.searches + len(greps)} requests at once (--burst {args.burst}, "
                f"--searches {args.searches}, {len(greps)} grep bodies), but {root} admits {slots} at once and "
                f"queues {queued}: the others would be answered 503 at admission. Send at most {slots + queued}"
            )
        port = args.port or vr.free_port()
        vr.check_port(port)
    except vr.Refused as exc:
        vr.log(f"REFUSED: {exc}")
        return 2

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))     # a stopped job still stops its child
    token = secrets.token_hex(32)
    result: dict[str, Any] = {"bencher": BENCHER, "root": str(root), "status": "running", "started_at": vr.now(),
                              "golden": golden.meta(), "params": {k: getattr(args, k) for k in (
                                  "c1", "bursts", "burst", "queries", "searches")}, "greps": len(greps)}
    work = Path(tempfile.mkdtemp(prefix="bench-pipeline-"))
    proc = None
    http = ev.Http(f"http://127.0.0.1:{port}", token, timeout=120.0, retries_503=1)
    failed = None
    try:
        env = vr.child_env(dict(os.environ), root, token, no_dense=False, retrieval_config=None)
        proc = vr.start_child(env, port, work / "server.log")
        result["server"] = {"pid": proc.pid, "port": port}
        healthy, why = vr.wait_healthy(proc, http, args.startup_timeout)
        if not healthy:
            raise RuntimeError(f"server: {why}")
        status, ready, why, _ = vr.wait_ready(proc, http, args.startup_timeout if args.ready_timeout is None
                                              else args.ready_timeout, False)
        result["server"]["ready"] = ready
        result["generation"] = (status or {}).get("generation")
        vr.log(f"bench_pipeline: {root} ready={ready}; c1 {args.c1}, {args.bursts} bursts of {args.burst} x "
               f"{args.queries}-query search_many, {args.searches} searches and {len(greps)} greps beside each")
        result.update(run_bench(http, queries, c1=args.c1, bursts=args.bursts, burst=args.burst,
                                per_many=args.queries, searches=args.searches, greps=greps))
        for phase, data in result["phases"].items():
            for route, s in data["summary"].items():
                vr.log(f"{phase:<5} {route:<16} calls {s['calls']} errors {s['errors']} 503 {s['http_503']} "
                       f"degraded {s['degraded']} client p50/p95 {s['client_ms']['p50']}/{s['client_ms']['p95']} ms"
                       + (f", rerank_wait p95 {s['rerank_wait_ms']['p95']} ms" if "rerank_wait_ms" in s else ""))
        vr.log(f"gate hold: {result['phases']['burst']['gate_hold']}")
        vr.log(counts_line(result["gate_counts"]))
        bad = [r for d in result["phases"].values() for r in d["rows"] if r["status"] != 200]
        if bad:
            failed = f"{len(bad)} requests failed"
    except Exception as exc:  # noqa: BLE001 - reported; the child is still stopped below
        failed = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        http.close()
        code = vr.stop_child(proc)
        if proc is not None:
            result["server"]["exit_status"] = code
            if failed:
                result["server"]["log_tail"] = vr.tail(work / "server.log")
        shutil.rmtree(work, ignore_errors=True)
    result["status"] = "failed" if failed else "done"
    if failed:
        result["failure"] = failed
    result["finished_at"] = vr.now()
    vr.write_json(root / report_file, result)
    vr.log(f"bench_pipeline: {result['status']}{': ' + failed if failed else ''}; report {root / report_file}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
