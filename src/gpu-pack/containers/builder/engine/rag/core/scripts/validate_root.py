#!/usr/bin/env python3
"""Validate a built root before it is served: deep32-engine on loopback over the root, /v2/status against the build,
and the eval of a golden set (default: the known-item set).

usage: validate_root.py --root /var/lib/deep32/rag-data/<generation>
                        [--port 18765] [--golden known_items] [--no-dense]
                        [--expect-generation ID] [--expect-fingerprint FP] [--expect-docs N] [--expect-chunks N]
                        [--expect-opt-in TYPE[,TYPE...]]
                        [--min METRIC=VALUE ...] [--limit 30] [--max-chars 2400] [--startup-timeout 180]
                        [--ready-timeout S] [--eval-out DIR] [--baseline VALIDATION.json|ROOT]
                        [--max-drop METRIC=VALUE ...] [--report-name VALIDATION.json]
                        [--replay FILE ... [--replay-concurrency N]] [--many-plan FILE]

The A/B instrument for a serving configuration: derive two roots from one generation (scripts/derive_root.py,
e.g. the reranker off and on), validate each on the full known-item set with the settings the edge uses
    --golden known_items --limit 8 --max-chars 2400
and the second with --baseline <the first root>: the journal then carries the paired comparison
(scripts/compare_validation.py) and VALIDATION.json keeps every query's hits and metrics.

Steps; progress and a summary go to stdout (the job journal):
  1. refuse   the served root ($RAG_ROOT, as scripts/build_root.py does; the journal names the RAG_ROOT compared
              against, or warns that it is unset), a root without sqlite/rag.db, a root whose BUILD_REPORT.json is
              not "complete" or lists failed steps (a build still running, or one that failed), a golden set that
              does not match its MANIFEST.json, a port another server listens on, or a run with nothing to expect.
              A refusal starts nothing and writes nothing (exit 2)
  2. expect   generation, corpus fingerprint, document and chunk counts from the arguments, else from
              <root>/BUILD_REPORT.json; the opt-in doc types from --expect-opt-in, else from the report's config
              when build_root --opt-in-doc-types wrote them there (a derivation's report does not say); the rows of
              each BM25 table from the report's bm25.tables, when build_root split the opt-in doc types' chunks into
              chunk_fts_optin (C16; a derivation's report keeps its source's). Opt-in doc types expected of a root
              whose report has no bm25.tables: a warning, "unsplit root" (built before C16, as the p7-era roots
              were), not a gate
     fts      the gate fts_integrity, before the child starts: FTS5's integrity-check of every FTS5 table in rag.db
              (sqlite_master's virtual tables whose module is fts5, however quoted (vtab_module): chunk_fts,
              chunk_fts_optin on a split root, any other), each index checked alone, never compared with chunks (a
              split root's tables each index part of it). rag.db is opened read-only (mode=ro, as the eval opens
              it), where FTS5's INSERT form of the command is refused ("attempt to write a readonly database"), so
              it runs as PRAGMA integrity_check(<table>): the same FTS5 check (the xIntegrity method, SQLite
              3.44.0+). An index the check finds corrupt fails the gate with FTS5's message (what the INSERT form
              raises as SQLITE_CORRUPT_VTAB); so do a rag.db without chunk_fts and an SQLite whose PRAGMA does not
              run FTS5's check (fts5_checked_read_only). Checked alone, an index whose damaged bytes still parse (a
              changed position list or rowid delta) passes, in either form: only a comparison with chunks sees it.
              Report "fts_integrity": the SQLite, and per table its result, seconds and definition (sqlite_master.sql)
  3. serve    python -m uvicorn rag_core.api:app --host 127.0.0.1 --port <port> --workers 1 as a child, with
              RAG_ROOT=<root>, an ephemeral token (secrets.token_hex(32), never logged), traces off, and the
              overlay-override env keys removed (OVERRIDE_KEYS: the root's own config/*.json must decide)
  4. status   GET /v2/status, polled after /health until the root is loaded (ok, generation, fingerprint and
              counts reported, nothing degraded but dense under --no-dense), at most --ready-timeout seconds
              (default the --startup-timeout; 0 reads it once); past that the gates read the last answer, so a
              broken root still fails. The gates: ok, generation, corpus_fingerprint, n_docs, n_chunks, dense ok
              with its breaker closed (dense disabled with --no-dense), kb and structure ok, opt_in_doc_types
              exactly the expected doc types when step 2 has them ("" for none; status.opt_in), backends.bm25.tables
              equal to the report's bm25.tables when step 2 has them (status.bm25_tables: a deep32-engine without C16
              reports none and searches chunk_fts alone), degraded []; POST /search answers "candidates" (the v1
              contract)
  5. eval     every query of the golden set (default eval/golden/known_items, the generated known-item set)
              through /v2/search, each with its own filters when its record has them
              (eval_v2: queries.jsonl "filters"), relevance from the root's own rag.db: no errors, no degraded
              answers, every hit id in rag.db, no hit of the root's opt-in doc types (/v2/status opt_in_doc_types)
              for a query that names neither doc_type nor docs (eval.opt_in_leak), no hit outside the doc_type or
              docs a query names (eval.filter_scope), and the --min floors
  6. compare  with --baseline (another root's VALIDATION.json on the same golden set): per metric the means,
              wins and losses, sign test and bootstrap interval, latency and hit@1 per domain, into the journal;
              each --max-drop METRIC=VALUE is a gate (this run may fall below the baseline by at most VALUE)
     retried  the gate eval.retried: no query of the eval answered only after a 503 (a full admission queue; its
              latency would hold the Retry-After wait)
     census   at --limit 30 or more: where the top-8 misses' first relevant hit sits, "census: misses=<n>
              at_9_10=<n> at_11_20=<n> at_21_30=<n> beyond_30=<n>" (positions after the API's dedup of passages,
              so approximate fused ranks; report "census")
     replay   with --replay FILE (repeatable; a path, or one relative to the repo such as eval/replay/...): every
              recorded body of the files, in order, at --replay-concurrency N (default 1), through the child's POST
              /v2/grep or /v2/search_many, while a probe thread reads GET /v2/section and /v2/outline of the eval's
              first hit every READING_PROBE_SECONDS (the reading endpoints, which take no admission slot). A file
              line is {"id": ID (optional; default <file stem>:<line>), "route": "grep" | "search_many", "body":
              {the request body}}. Per call: route (grep_literal, grep_regex, search_many), status, ms, 503
              retries, timings_ms.rerank and rerank_wait, the truncated flag (budget_truncated: truncated with
              fewer than max_matches matches), and a digest of the answer (grep: its doc_key and line list;
              search_many: its first 8 hit ids); report "replay", one journal line per route. No gate: the paired
              compare_validation reads it
     plan     with --many-plan FILE: one POST /v2/search_many per line {"qid", "queries": [{"text", "mode",
              "weight"}], "filters" (optional; default the golden query's)}, each qid an answerable judged query of
              the golden set, at --limit and --max-chars, scored as the eval scores /v2/search (report
              "many_plan"); the gates many_plan.errors and many_plan.degraded
  7. report   <root>/VALIDATION.json, or the --report-name file in the root (gates, the status, the eval summary
              with one row per query: its hit ids and metrics, and its category and style when the golden set has
              them; the comparison; "arm": the root's BUILD_REPORT config.overrides and the sha256 of each
              config/*.json, one "arm: config/<name>.json sha256=<hex>" line each); the child is stopped by PID in
              a finally block whatever happened

--report-name keeps one validation of a root from overwriting another: write the baseline as, say,
VALIDATION-baseline.json, and pass that file to the next run's --baseline (a ROOT there means its VALIDATION.json).

--no-dense (staging, or a root built with --no-dense): the child serves BM25 only. Its retrieval config is the
root's with retrievers ["bm25"] and the reranker off, and every dense, Qdrant and rerank URL points at a refused
loopback port, so nothing reaches TEI, Qdrant or the reranker.

The default --min floors are sanity floors for a whole-corpus root (BM25 alone scored hit@1 0.44 and hit@8 0.82 on
the original small check set): they catch a broken build, a wrong index or lost structure, not a ranking regression; the
P1 quality gates are compare_v2's.

Exit status: 0 every gate passed, 1 a gate failed (see VALIDATION.json), 2 refused.

With --no-eval, only quality evaluation and its dependent comparisons are skipped. Build completeness,
served-root, SQLite/FTS integrity, child health, every status gate and legacy search remain required. The report
records "quality validation skipped: no eval sets supplied". Supply --eval-root to run the unchanged quality gates.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import contextlib
import hashlib
import http.client
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "src", REPO / "eval"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

# The evaluator is loaded only for an explicitly requested quality run. A structural validation never imports
# operator code, even if an evaluator happens to be importable in the calling environment.
ev = None
from rag_core.config import load_config  # noqa: E402
from rag_core.db import DEFAULT_FTS  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import compare_validation as cv  # noqa: E402  scripts/compare_validation.py

VALIDATOR = "scripts/validate_root.py"
REPORT_NAME = "VALIDATION.json"
BUILD_REPORT = "BUILD_REPORT.json"

# env keys that override a root's config overlay (rag_core.config, dense, rerank, api): the root decides
OVERRIDE_KEYS = (
    "RAG_DENSE_COLLECTION", "RAG_RETRIEVAL_CONFIG", "RAG_SERVING_CONFIG", "RAG_LATE_INTERACTION_CONFIG",
    "RAG_EMBED_QUERY_TIMEOUT", "RAG_MAX_CONCURRENT_SEARCHES", "RAG_SEARCH_QUEUE_TIMEOUT", "RAG_RERANK_URL",
)
# --no-dense: the discard port on loopback; nothing listens there
REFUSED_URL = "http://127.0.0.1:9"
DEFAULT_PORT = 18765
DEFAULT_MINIMUMS = {"hit@1": 0.35, "hit@8": 0.70}
SKIPPED_MESSAGE = "quality validation skipped: no eval sets supplied"
LOG_TAIL_LINES = 40
# seconds between two /v2/status reads while the root loads (deep32-engine caches a round of probes for 5 s)
READY_POLL_SECONDS = 1.0
# what VALIDATION.json keeps per query: enough to recompute the means and delivery and to compare two runs
QUERY_METRICS = ("hit@1", "hit@3", "hit@8", "mrr@10", "ndcg@10", "anywhere@8", "visible_found_n@8", "found_n@8")
# --replay: a route's request path, and the body key it cannot do without
REPLAY_PATHS = {"grep": "/v2/grep", "search_many": "/v2/search_many"}
GREP_MAX_MATCHES = 50           # /v2/grep's default max_matches (rag_core.api_v2.GrepRequest)
READING_PROBE_SECONDS = 0.5     # the reading probe's pause between two calls during a replay
# the census (--limit 30 or more): positions of the top-8 misses' first relevant hit
CENSUS_LIMIT = 30
CENSUS_BUCKETS = (("at_9_10", 9, 10), ("at_11_20", 11, 20), ("at_21_30", 21, 30))
PLAN_MODES = ("hybrid", "bm25", "dense")      # a many-plan query's modes (rag_core.api_v2.SubQuery)

T0 = time.monotonic()


class Refused(Exception):
    """Validation cannot start. Raised before the child starts or anything is written."""


class CoreHttp:
    """Loopback client for the non-quality gates; independent of the optional evaluator."""

    def __init__(self, url: str, token: str, timeout: float = 120.0, retries_503: int = 2):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password:
            raise ValueError("validator HTTP client requires loopback")
        self.port, self.token, self.timeout = parsed.port, token, timeout
        self.retries_503 = retries_503

    def request(self, method: str, path: str, body: dict[str, Any] | None = None):
        started = time.monotonic()
        retries = 0
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        while True:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
            try:
                conn.request(method, path, body=encoded, headers=headers)
                response = conn.getresponse()
                status, data = response.status, response.read()
            finally:
                conn.close()
            if status != 503 or retries >= self.retries_503:
                return status, data, (time.monotonic() - started) * 1000, retries
            retries += 1
            time.sleep(0.25)

    def json(self, method: str, path: str, body: dict[str, Any] | None = None):
        status, data, milliseconds, _ = self.request(method, path, body)
        if status != 200:
            raise OSError(f"{path} answered HTTP {status}")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError("validator endpoint must return a JSON object")
        return result, milliseconds

    def close(self):
        pass


def backend_errors():
    """The evaluator's HTTP exception exists only when quality inputs are supplied."""
    return (OSError, ValueError) + ((ev.BackendError,) if ev is not None else ())


def log(message: str) -> None:
    print(f"[{time.monotonic() - T0:7.1f}s] {message}", flush=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- refusals and expectations


def check_not_served(root: Path) -> None:
    """The build_root guard: never the served root, nothing inside or above it. The journal names the RAG_ROOT it
    compared against, or warns that the guard is inactive: deployctl passes a job only its job_env_keys, so a job
    may run without RAG_ROOT."""
    served = os.environ.get("RAG_ROOT")
    if not served:
        log("WARNING: RAG_ROOT is not set here, so the served-root guard is inactive; "
            "make sure --root is not the root deep32-engine serves")
        return
    served_path = Path(served).expanduser().resolve()
    log(f"served-root guard: RAG_ROOT is {served_path}")
    if root == served_path or served_path in root.parents or root in served_path.parents:
        raise Refused(f"{root} overlaps RAG_ROOT {served_path}, the served root")


def report_name(name: str) -> str:
    """--report-name: a .json file name for the root itself (no directory), never the build's report."""
    if Path(name).name != name or not name.endswith(".json") or name in (".json", BUILD_REPORT):
        raise Refused(f"--report-name {name!r}: expected a file name such as VALIDATION-baseline.json, written into "
                      f"the root (no directory, not {BUILD_REPORT})")
    return name


def parse_minimums(items: list[str]) -> dict[str, float]:
    """--min METRIC=VALUE floors over DEFAULT_MINIMUMS; METRIC=off drops that floor (a leak probe: a golden set
    with no answerable query has no hit@1 or hit@8, so a default floor could never pass)."""
    floors = dict(DEFAULT_MINIMUMS)
    for item in items:
        metric, sep, value = item.partition("=")
        try:
            if not sep or metric not in ev.MEAN_METRICS + [f"delivery@{k}" for k in ev.KS] + ["auroc"]:
                raise ValueError
            if value == "off":
                floors.pop(metric, None)
                continue
            floors[metric] = float(value)
        except ValueError:
            raise Refused(f"--min {item!r}: expected METRIC=VALUE with a metric eval_v2 reports "
                          "(hit@1, hit@8, mrr@10, ndcg@10, delivery@8, ...)") from None
    return floors


def expectations(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """What /v2/status must report, from the arguments, else from the root's BUILD_REPORT.json."""
    report = None
    path = root / BUILD_REPORT
    if path.is_file():
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise Refused(f"{path} is not JSON: {exc}") from None
    check_build_report(report)
    built = report or {}
    documents = built.get("documents") or {}
    dense = built.get("dense") or {}
    expected = {
        "generation": args.expect_generation or built.get("generation"),
        "corpus_fingerprint": args.expect_fingerprint or (built.get("corpus") or {}).get("fingerprint"),
        "n_docs": args.expect_docs if args.expect_docs is not None else (
            documents.get("built_this_run", 0) + documents.get("already_done", 0) if documents else None),
        "n_chunks": args.expect_chunks if args.expect_chunks is not None else built.get("chunks"),
        # a report tells whether the build made a dense index; without one, dense is expected unless --no-dense
        "dense_built": not dense.get("skipped") if report is not None else None,
    }
    missing = [key for key in ("generation", "corpus_fingerprint", "n_docs", "n_chunks") if expected[key] is None]
    if missing:
        raise Refused(f"nothing to check {', '.join(missing)} against: {path.name} is missing or lacks them; "
                      "pass --expect-generation/--expect-fingerprint/--expect-docs/--expect-chunks")
    opt_in = getattr(args, "expect_opt_in", None)
    built_opt_in = (built.get("config") or {}).get("opt_in_doc_types")
    if opt_in is not None:
        expected["opt_in_doc_types"] = sorted({t.strip() for t in opt_in.split(",") if t.strip()})
    elif isinstance(built_opt_in, list):  # build_root --opt-in-doc-types; a derivation's report does not say
        expected["opt_in_doc_types"] = sorted(set(built_opt_in))
    # C16: build_root --opt-in-doc-types keeps those doc types' chunks in chunk_fts_optin and counts each table's
    # rows; only a deep32-engine with C16 reads both tables, and it reports their rows
    tables = (built.get("bm25") or {}).get("tables")
    if isinstance(tables, dict) and tables:
        expected["bm25_tables"] = tables
    elif report is not None and expected.get("opt_in_doc_types"):
        log(f"WARNING: unsplit root: {BUILD_REPORT} has no bm25.tables, so the opt-in doc types "
            f"{expected['opt_in_doc_types']} share chunk_fts with the default chunks (a root built before C16, as "
            "the p7-era roots were); status.bm25_tables is not checked")
    return expected, report


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_build_report(report: dict[str, Any] | None) -> None:
    """Only a finished build is validated. build_root rewrites BUILD_REPORT.json with status "running" after every
    step, so a root under construction has one. Serving it would, with dense, send the eval to the embeddings server
    during the bulk embedding, and write VALIDATION.json into a root still being built. A root without a report
    (not made by build_root) is checked against the --expect-* arguments alone."""
    if report is None:
        return
    failed = [s.get("step") if isinstance(s, dict) else s for s in report.get("failed_steps") or []]
    bad_docs = (report.get("documents") or {}).get("failed") or []
    if report.get("status") != "complete" or failed or bad_docs:
        raise Refused(f"{BUILD_REPORT} says status {report.get('status')!r}, failed steps {failed}, "
                      f"{len(bad_docs)} failed documents: only a complete build is validated "
                      "(wait for build_root to finish, or finish it with --resume)")


def check_port(port: int) -> None:
    """Refuse a port another server listens on. The probe binds with SO_REUSEADDR, as uvicorn does, so a TIME_WAIT
    connection left by an earlier run (its server closed an idle keep-alive connection first) does not refuse a
    port that uvicorn could bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            raise Refused(f"127.0.0.1:{port} is in use; pass --port 0 for a free port") from None


# ---------------------------------------------------------------- the child server


def child_env(parent: dict[str, str], root: Path, token: str, *, no_dense: bool,
              retrieval_config: Path | None) -> dict[str, str]:
    """The child's environment: the parent's minus the overlay-override keys, with this root, the ephemeral token,
    traces off and rag_core importable. --no-dense adds a bm25-only retrieval config and refused service URLs."""
    env = {k: v for k, v in parent.items() if k not in OVERRIDE_KEYS}
    env.update({
        "RAG_ROOT": str(root),
        "RAG_CORE_API_TOKEN": token,
        "RAG_TRACE": "off",                   # the root stays as built: no trace files
        "PYTHONPATH": os.pathsep.join(p for p in (str(REPO / "src"), parent.get("PYTHONPATH")) if p),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if no_dense:
        env.update({
            "RAG_RETRIEVAL_CONFIG": str(retrieval_config),
            "RAG_EMBEDDING_URL": f"{REFUSED_URL}/v1/embeddings",
            "RAG_QDRANT_URL": REFUSED_URL,
            "RAG_RERANK_URL": f"{REFUSED_URL}/v1/rerank",
        })
    return env


def bm25_only_config(root: Path, directory: Path) -> Path:
    """The root's retrieval config (repo file + the root's overlay) serving BM25 alone, reranker off."""
    config = load_config("retrieval", root=root)
    config["retrievers"] = ["bm25"]
    config["technical_variant_retrievers"] = ["bm25"]
    config["reranker"] = {"enabled": False}
    path = directory / "retrieval.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def start_child(env: dict[str, str], port: int, log_path: Path) -> subprocess.Popen:
    out = open(log_path, "wb")
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "rag_core.api:app", "--host", "127.0.0.1", "--port", str(port),
             "--workers", "1", "--no-access-log", "--log-level", "warning"],
            cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
        )
    finally:
        out.close()


def stop_child(proc: subprocess.Popen | None) -> int | None:
    """Stop the child by its PID: SIGTERM, then SIGKILL after 10 s. Returns its exit status."""
    if proc is None:
        return None
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)
    return proc.returncode


def tail(path: Path, lines: int = LOG_TAIL_LINES) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def wait_healthy(proc: subprocess.Popen, http: ev.Http, timeout: float) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last = "no answer yet"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False, f"the server exited with status {proc.returncode} before it answered /health"
        try:
            status, _, _, _ = http.request("GET", "/health")
            if status == 200:
                return True, "ok"
            last = f"/health answered HTTP {status}"
        except OSError as exc:
            last = f"{type(exc).__name__}"
        time.sleep(0.25)
    return False, f"no healthy answer within {timeout:.0f} s ({last})"


def not_ready(status: dict[str, Any], no_dense: bool) -> str | None:
    """Why /v2/status does not yet describe the loaded root, or None. /health answers once the app has started;
    the status comes from probes (rag_core.status: rag.db's counts, fingerprint, an FTS query and the structure
    counts, 2.5 s per probe, a round cached 5 s), and until the rag.db probe answers in time it reports ok false,
    the counts None, structure 0 and bm25 and structure degraded. Readiness is the values being present, not equal to
    the expected ones: a wrong count does not change by waiting, the gates report it. Dense is not waited for
    under --no-dense."""
    if status.get("ok") is not True:
        return f"ok={status.get('ok')!r}"
    missing = [key for key in ("generation", "corpus_fingerprint", "n_docs", "n_chunks") if status.get(key) is None]
    if missing:
        return f"no {', '.join(missing)}"
    degraded = [name for name in status.get("degraded") or [] if not (no_dense and name == "dense")]
    if degraded:
        return f"degraded {degraded}"
    return None


def wait_ready(proc: subprocess.Popen, http: ev.Http, timeout: float, no_dense: bool,
               interval: float = READY_POLL_SECONDS) -> tuple[dict[str, Any] | None, bool, str, int]:
    """Poll /v2/status until not_ready() is None, the child exits, or `timeout` seconds have passed (0: one read).
    Returns the last status read (None if no read answered), whether it is ready, why not, and the reads made."""
    deadline = time.monotonic() + timeout
    status: dict[str, Any] | None = None
    why, logged, polls = "no answer yet", None, 0
    while True:
        if proc.poll() is not None:
            return status, False, f"the server exited with status {proc.returncode}", polls
        polls += 1
        try:
            status, _ = http.json("GET", "/v2/status")
            why = not_ready(status, no_dense)
        except backend_errors() as exc:
            why = f"/v2/status: {type(exc).__name__}: {str(exc)[:160]}"
        if why is None:
            return status, True, "ready", polls
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return status, False, why, polls
        if why != logged:
            log(f"status: not ready yet ({why}); polling /v2/status, {remaining:.0f} s left")
            logged = why
        time.sleep(min(interval, remaining))


# ---------------------------------------------------------------- the FTS5 indexes (step fts)


# the tokens of an SQL statement: whitespace, comments, quoted names ("", '', ``, []), words, any other character
SQL_TOKEN = re.compile(r"""\s+|--[^\n]*|/\*.*?(?:\*/|\Z)"""
                       r"""|"(?:[^"]|"")*"|'(?:[^']|'')*'|`(?:[^`]|``)*`|\[[^\]]*\]|\w+|.""", re.DOTALL)
# FTS5's integrity-check of one table on a read-only connection: the xIntegrity method of the virtual table (SQLite
# 3.44.0+), which checks the index alone, as INSERT INTO <t>(<t>) VALUES ('integrity-check') does on an external
# content table; that INSERT needs a write transaction, which mode=ro refuses
FTS_CHECK_SQL = "PRAGMA integrity_check({table})"


def quote_name(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def vtab_module(sql: str | None) -> str | None:
    """The module of a virtual table's definition in sqlite_master, dequoted and lower-cased as SQLite matches it:
    "fts5" for USING fts5(...), USING "FTS5"(...), USING [fts5](...) or USING fts5/*...*/(...); "fts5vocab" for an
    fts5vocab table, whatever its (quoted) name holds; None for any other statement."""
    words = [t for t in SQL_TOKEN.findall(sql or "") if not (t.isspace() or t.startswith(("--", "/*")))]
    if [w.upper() for w in words[:3]] != ["CREATE", "VIRTUAL", "TABLE"]:
        return None
    for i in range(3, len(words) - 1):
        if words[i].upper() == "USING":        # the first bare USING: a quoted name is one token
            module = words[i + 1]
            if module[:1] == "[":
                module = module[1:-1]
            elif module[:1] in ("\"", "'", "`"):
                module = module[1:-1].replace(module[0] * 2, module[0])
            return module.lower()
    return None


def fts5_checked_read_only() -> bool:
    """True when this SQLite's PRAGMA integrity_check runs FTS5's integrity-check on an FTS5 table (xIntegrity,
    SQLite 3.44.0+); an older one answers "ok" without reading the index. Probed on an in-memory table whose index
    is zeroed, which a checking SQLite reports corrupt."""
    conn = sqlite3.connect(":memory:", isolation_level=None)     # autocommit: FTS5 writes its index at once
    try:
        conn.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        conn.executemany("INSERT INTO probe (text) VALUES (?)", [("alpha beta",), ("gamma delta",)])
        conn.execute("UPDATE probe_data SET block = zeroblob(length(block)) WHERE id > 10")     # the leaf pages
        return conn.execute(FTS_CHECK_SQL.format(table="probe")).fetchall() != [("ok",)]
    except sqlite3.Error as exc:            # no FTS5 at all, or the corruption raised rather than reported
        return str(getattr(exc, "sqlite_errorname", "")).startswith("SQLITE_CORRUPT")
    finally:
        conn.close()


def fts_integrity(db: Path) -> dict[str, Any]:
    """FTS5's integrity-check of every FTS5 table in rag.db, opened read-only (mode=ro, as eval_v2 opens it: rag.db
    is never written; on a WAL database SQLite may add its empty -wal and -shm side files, as every reader does).
    ok: the SQLite runs the check read-only, rag.db has chunk_fts, and every table's check answers "ok"."""
    started = time.monotonic()
    out: dict[str, Any] = {"ok": False, "sqlite": sqlite3.sqlite_version, "method": FTS_CHECK_SQL, "tables": {}}
    if not fts5_checked_read_only():
        out["error"] = (f"SQLite {sqlite3.sqlite_version}'s PRAGMA integrity_check runs no FTS5 integrity-check "
                        "(SQLite 3.44.0 or newer does), so the FTS5 indexes cannot be checked read-only: validate "
                        "with a Python whose SQLite is 3.44.0 or newer")
        out["seconds"] = round(time.monotonic() - started, 2)
        return out
    try:
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            tables = {name: sql for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name") if vtab_module(sql) == "fts5"}
            for name, sql in tables.items():
                begun = time.monotonic()
                try:
                    rows = [row[0] for row in conn.execute(FTS_CHECK_SQL.format(table=quote_name(name)))]
                    ok = rows == ["ok"]
                    result = "ok" if ok else "corrupt: " + "; ".join(map(str, rows))
                except sqlite3.Error as exc:    # e.g. SQLITE_CORRUPT_VTAB: the table cannot even be opened
                    ok, result = False, f"{getattr(exc, 'sqlite_errorname', type(exc).__name__)}: {exc}"
                out["tables"][name] = {"ok": ok, "result": result[:500],
                                       "seconds": round(time.monotonic() - begun, 2), "sql": sql}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        out["error"] = f"{db}: {getattr(exc, 'sqlite_errorname', type(exc).__name__)}: {exc}"
    else:
        if DEFAULT_FTS not in out["tables"]:
            out["error"] = f"rag.db has no FTS5 table {DEFAULT_FTS} (FTS5 tables: {sorted(out['tables'])})"
        out["ok"] = "error" not in out and all(t["ok"] for t in out["tables"].values())
    out["seconds"] = round(time.monotonic() - started, 2)
    return out


def fts_detail(fts: dict[str, Any]) -> str:
    """The fts_integrity gate's detail line: each table's result, or why nothing was checked."""
    tables = ", ".join(f"{name} ok ({t['seconds']} s)" if t["ok"] else f"{name} {t['result']}"
                       for name, t in fts["tables"].items())
    what = f"FTS5 integrity-check of each index, read-only ({FTS_CHECK_SQL.format(table='<table>')}, SQLite " \
           f"{fts['sqlite']})"
    return "; ".join(part for part in (fts.get("error"), tables, what) if part)


# ---------------------------------------------------------------- gates


class Gates:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: str) -> bool:
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})
        log(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
        return bool(ok)

    @property
    def passed(self) -> bool:
        return bool(self.items) and all(g["ok"] for g in self.items)


def status_gates(gates: Gates, status: dict[str, Any], expected: dict[str, Any], no_dense: bool) -> None:
    def same(name: str, key: str) -> None:
        gates.check(f"status.{name}", status.get(key) == expected[key],
                    f"{status.get(key)!r} (expected {expected[key]!r})")

    backends = status.get("backends") or {}
    gates.check("status.ok", status.get("ok") is True, f"ok={status.get('ok')!r}")
    for key in ("generation", "corpus_fingerprint", "n_docs", "n_chunks"):
        same(key, key)
    dense = backends.get("dense") or {}
    if no_dense:
        gates.check("status.dense", dense.get("enabled") is False,
                    f"enabled={dense.get('enabled')!r} (--no-dense: BM25 only)")
    elif expected.get("dense_built") is False:
        gates.check("status.dense", False, "the root was built with --no-dense: it has no dense index to serve "
                                           "(validate it with --no-dense, or build a root with dense)")
    else:
        gates.check("status.dense", dense.get("enabled") is True and dense.get("ok") is True
                    and dense.get("breaker") == "closed",
                    f"enabled={dense.get('enabled')!r} ok={dense.get('ok')!r} breaker={dense.get('breaker')!r} "
                    f"latency_ms={dense.get('latency_ms')!r} last_error_kind={dense.get('last_error_kind')!r}")
    kb = backends.get("kb") or {}
    gates.check("status.kb", kb.get("ok") is True,
                f"ok={kb.get('ok')!r} cli={(kb.get('tables') or {}).get('cli')!r} "
                f"rfc={(kb.get('tables') or {}).get('rfc')!r}")
    structure = backends.get("structure") or {}
    gates.check("status.structure", structure.get("ok") is True,
                f"ok={structure.get('ok')!r} documents={structure.get('documents')!r} "
                f"sections={structure.get('sections')!r}")
    reranker = backends.get("reranker") or {}
    if reranker.get("enabled"):
        gates.check("status.reranker", reranker.get("ok") is True,
                    f"ok={reranker.get('ok')!r} last_error_kind={reranker.get('last_error_kind')!r}")
    if expected.get("opt_in_doc_types") is not None:
        reported = status.get("opt_in_doc_types")
        gates.check("status.opt_in", isinstance(reported, list) and sorted(reported) == expected["opt_in_doc_types"],
                    f"{reported!r} (expected {expected['opt_in_doc_types']!r})")
    if expected.get("bm25_tables") is not None:
        # a split root: a deep32-engine without C16 does not report the tables, and serves the opt-in doc types nothing
        reported = (backends.get("bm25") or {}).get("tables")
        ok = reported == expected["bm25_tables"]
        if ok:
            why = ""
        elif reported is None:
            why = (": a deep32-engine without C16 serves this split root (no backends.bm25.tables); it searches chunk_fts "
                   "alone, so a search naming an opt-in doc type finds none of the chunks in chunk_fts_optin. Serve "
                   "it with a core that has C16")
        else:
            why = ": the served rag.db's BM25 tables are not the ones built"
        gates.check("status.bm25_tables", ok, f"{reported!r} (expected {expected['bm25_tables']!r}, the build "
                                              f"report's bm25.tables){why}")
    gates.check("status.degraded", status.get("degraded") == [], f"{status.get('degraded')!r}")


def opt_in_leak_gate(gates: Gates, report: dict[str, Any], status: dict[str, Any]) -> None:
    """eval.opt_in_leak: no hit of a query that names neither doc_type nor docs (its golden record's filters) is of
    one of the root's opt-in doc types (/v2/status opt_in_doc_types): those are reached only by naming them. A
    forgotten overlay key shows up as status.opt_in (--expect-opt-in, or the build report's), a leak past it here."""
    opt_in = set(status.get("opt_in_doc_types") or [])
    leaks: list[str] = []
    checked = 0
    for row in report["results"]:
        filters = row.get("filters") or {}
        if filters.get("doc_type") is not None or filters.get("docs") is not None:
            continue
        checked += 1
        leaks += [f"{row['qid']}:{h.get('doc')}" for h in row.get("hits") or [] if h.get("doc_type") in opt_in]
    detail = (f"{len(leaks)} hits of {sorted(opt_in)} in {checked} queries without doc_type or docs"
              if opt_in else "the root has no opt-in doc types")
    gates.check("eval.opt_in_leak", not leaks, detail + (f", e.g. {leaks[:5]}" if leaks else ""))


def filter_scope_gate(gates: Gates, report: dict[str, Any]) -> None:
    """eval.filter_scope: a query whose golden record names a doc_type gets hits of that doc_type only, and one that
    names docs gets hits of those docs only (the other side of eval.opt_in_leak: a named collection stays inside)."""
    strays: list[str] = []
    checked = 0
    for row in report["results"]:
        filters = row.get("filters") or {}
        doc_type, docs = filters.get("doc_type"), filters.get("docs")
        if doc_type is None and docs is None:
            continue
        checked += 1
        strays += [f"{row['qid']}:{h.get('doc')}" for h in row.get("hits") or []
                   if (doc_type is not None and h.get("doc_type") != doc_type)
                   or (docs is not None and h.get("doc") not in docs)]
    detail = (f"{len(strays)} hits outside their query's doc_type or docs in {checked} filtered queries"
              if checked else "no query names a doc_type or docs")
    gates.check("eval.filter_scope", not strays, detail + (f", e.g. {strays[:5]}" if strays else ""))


def eval_gates(gates: Gates, report: dict[str, Any], floors: dict[str, float]) -> None:
    o = report["overall"]
    gates.check("eval.errors", o["n_errors"] == 0,
                f"{o['n_errors']} failed queries" + (f", e.g. {report['errors'][0]['error'][:160]}"
                                                     if report["errors"] else ""))
    gates.check("eval.degraded", o["n_degraded"] == 0, f"{o['n_degraded']} answers with degraded set")
    coverage = report.get("store_coverage") or {}
    gates.check("eval.index", report["relevance_basis"] == "chunk",
                f"relevance basis {report['relevance_basis']!r}: {coverage.get('missing')} of {coverage.get('hits')} "
                "hit ids missing from the root's rag.db")
    for metric, floor in sorted(floors.items()):
        value = o.get(metric)
        gates.check(f"eval.{metric}", value is not None and value >= floor, f"{value!r} (floor {floor})")


def eval_summary(report: dict[str, Any]) -> dict[str, Any]:
    """What VALIDATION.json keeps of the run: aggregates and one short row per query."""
    return {
        "label": report["label"],
        "golden": report["golden"],
        "params": report["params"],
        "relevance_basis": report["relevance_basis"],
        "store_coverage": report["store_coverage"],
        "overall": report["overall"],
        "latency_ms": report["latency_ms"],
        "by_domain": report["by_domain"],
        "errors": report["errors"],
        "wall_s": report["wall_s"],
        "queries": [
            {
                **{k: r.get(k) for k in ("qid", "domain", "answerable", "n_rel", "first_rel_rank", "n_hits",
                                         "latency_ms", "degraded", "error") if k in r},
                # the golden set's labels of the query, when it has them (eval_v2's by_category, by_style), and the
                # filters it ran with, when it has its own
                **{k: r[k] for k in ("category", "style", "filters") if r.get(k) is not None},
                "hits": [h["id"] for h in r.get("hits") or []],
                "metrics": {m: (r.get("metrics") or {}).get(m) for m in QUERY_METRICS},
            }
            for r in report["results"]
        ],
    }


def load_baseline(value: str | None, golden) -> dict[str, Any] | None:
    """--baseline: another validation of the same golden set with per-query rows; Refused otherwise."""
    if value is None:
        return None
    try:
        baseline = cv.load(value)
    except cv.BadInput as exc:
        raise Refused(f"--baseline {exc}") from None
    theirs = ((baseline.get("eval") or {}).get("golden") or baseline.get("golden") or {}).get("sha256")
    if theirs != golden.identity["sha256"]:
        raise Refused(f"--baseline ran another golden set ({str(theirs)[:12]}), this run {golden.path.name} "
                      f"({golden.identity['sha256'][:12]})")
    return baseline


# ---------------------------------------------------------------- the trial harness


def retried_gate(gates: Gates, report: dict[str, Any]) -> None:
    """eval.retried: no query answered only after a 503 (the child's admission queue was full), whose latency
    would hold the Retry-After wait."""
    n = report["overall"].get("n_retried") or 0
    gates.check("eval.retried", n == 0, f"{n} queries answered only after a 503 retry")


def census(report: dict[str, Any], limit: int) -> dict[str, Any] | None:
    """Where the top-8 misses (answerable, judged, no error, hit@8 0) have their first relevant hit, by position
    in the hits; None below CENSUS_LIMIT hits, where the buckets are not all seen."""
    if limit < CENSUS_LIMIT:
        return None
    misses = [r for r in report["results"] if r.get("answerable") and r.get("n_rel") and "error" not in r
              and (r.get("metrics") or {}).get("hit@8") == 0]
    out: dict[str, Any] = {"misses": len(misses)}
    for name, lo, hi in CENSUS_BUCKETS:
        out[name] = sum(lo <= (r.get("first_rel_rank") or 0) <= hi for r in misses)
    out["beyond_30"] = len(misses) - sum(out[name] for name, _, _ in CENSUS_BUCKETS)
    out["by_style"] = dict(sorted(Counter(str(r.get("style")) for r in misses).items()))
    return out


def arm_provenance(root: Path, build_report: dict[str, Any] | None) -> dict[str, Any]:
    """What this root's overlay is: its report's config.overrides (derive_root --set) and the sha256 of each
    config/*.json, so a validation of one arm of an A/B root says which arm it measured."""
    files = {f"config/{p.name}": hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((root / "config").glob("*.json"))}
    return {"overrides": ((build_report or {}).get("config") or {}).get("overrides"), "config": files}


def data_file(value: str) -> Path:
    """A --replay or --many-plan file: the path, else that path in the repo (eval/replay/..., as a job names it)."""
    path = Path(value).expanduser()
    if not path.is_file() and not path.is_absolute() and (REPO / path).is_file():
        path = REPO / path
    return path.resolve()


def read_rows(path: Path, what: str) -> list[tuple[int, dict[str, Any]]]:
    """(line number, object) per non-blank JSONL line; Refused for a file that is missing, empty or not JSONL."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Refused(f"{what} {path}: {type(exc).__name__}: {exc}") from None
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise Refused(f"{what} {path.name}:{n}: not JSON ({exc})") from None
        if not isinstance(row, dict):
            raise Refused(f"{what} {path.name}:{n}: not a JSON object")
        rows.append((n, row))
    if not rows:
        raise Refused(f"{what} {path}: no lines")
    return rows


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_replay(values: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """--replay FILE ...: (the calls in file order, the files). Refused before anything starts for a line that is
    not {"id"?, "route": "grep" | "search_many", "body": {...}} with the body's pattern or 1-8 queries, or an id
    used twice or holding whitespace (the journal names calls by id)."""
    calls: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        path = data_file(value)
        rows = read_rows(path, "--replay")
        for n, row in rows:
            where = f"--replay {path.name}:{n}"
            route, body = row.get("route"), row.get("body")
            if route not in REPLAY_PATHS or not isinstance(body, dict):
                raise Refused(f'{where}: expected {{"route": "grep" | "search_many", "body": {{...}}}}')
            if route == "grep":
                ok = isinstance(body.get("pattern"), str) and bool(body["pattern"]) \
                    and isinstance(body.get("regex", False), bool)
                kind = "grep_regex" if body.get("regex") else "grep_literal"
            else:
                queries = body.get("queries")
                ok = isinstance(queries, list) and 1 <= len(queries) <= 8 and all(
                    isinstance(q, dict) and isinstance(q.get("text"), str) and q["text"].strip() for q in queries)
                kind = "search_many"
            if not ok:
                raise Refused(f"{where}: a {route} body needs "
                              + ("a pattern (and regex true or false)" if route == "grep" else
                                 "1-8 queries, each with a text"))
            call_id = str(row.get("id") or f"{path.stem}:{n}")
            if call_id in seen or not call_id or any(c.isspace() for c in call_id):
                raise Refused(f"{where}: id {call_id!r} is used twice or holds whitespace")
            seen.add(call_id)
            calls.append({"id": call_id, "route": kind, "path": REPLAY_PATHS[route], "body": body})
        files.append({"path": str(path), "sha256": file_sha256(path), "calls": len(rows)})
    return calls, files


def reading_paths(results: list[dict[str, Any]]) -> list[str]:
    """The reading endpoints the replay probes: GET /v2/section and /v2/outline of the eval's first hit that
    names its section (none: no probe)."""
    for row in results:
        for hit in row.get("hits") or []:
            if hit.get("section_id") and hit.get("doc"):
                return [f"/v2/section?{urllib.parse.urlencode({'ref': hit['section_id'], 'max_chars': 2000})}",
                        f"/v2/outline?{urllib.parse.urlencode({'doc': hit['doc']})}"]
    return []


def replay_answer(call: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """What a replayed call's row keeps of its answer."""
    timings = data.get("timings_ms") or {}
    degraded = [str(d) for d in data.get("degraded") or []]
    if call["route"] == "search_many":
        ids = [str(h.get("id")) for h in data.get("hits") or []]
        return {"n": len(ids), "digest": cv.digest(ids[:cv.TOP_HITS]), "rerank_ms": timings.get("rerank"),
                "rerank_wait_ms": timings.get("rerank_wait"), "degraded": degraded}
    matches = data.get("matches") or []
    truncated = bool(data.get("truncated"))
    return {"n": len(matches), "digest": cv.digest([[m.get("doc_key"), m.get("line")] for m in matches]),
            "truncated": truncated,
            # max_matches cut the scan at that many matches; running out of the budget, at fewer
            "budget_truncated": truncated and len(matches) < call["body"].get("max_matches", GREP_MAX_MATCHES),
            "degraded": degraded}


def run_replay(url: str, token: str, calls: list[dict[str, Any]], concurrency: int,
               reading: list[str]) -> dict[str, Any]:
    """Every call at `concurrency`, in file order, while one thread probes the reading endpoints."""
    http = ev.Http(url, token, timeout=120.0, retries_503=2)
    stop = threading.Event()
    probes: list[dict[str, Any]] = []

    def probe() -> None:
        n = 0
        while reading and not stop.is_set():
            path = reading[n % len(reading)]
            n += 1
            started = time.perf_counter()
            try:
                status = http.request("GET", path)[0]
            except Exception as exc:  # noqa: BLE001 - a failed probe is a row
                status = type(exc).__name__
            probes.append({"path": path.split("?")[0], "status": status,
                           "ms": round((time.perf_counter() - started) * 1000, 1)})
            stop.wait(READING_PROBE_SECONDS)

    def one(call: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {"id": call["id"], "route": call["route"]}
        started = time.perf_counter()
        try:
            status, _, payload, retries = http.request("POST", call["path"], call["body"])
            row.update(status=status, retries=retries, ms=round((time.perf_counter() - started) * 1000, 1))
            if status == 200:
                row.update(replay_answer(call, json.loads(payload)))
        except Exception as exc:  # noqa: BLE001 - one failed call is a row, not the end of the replay
            row.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:160]}",
                       ms=round((time.perf_counter() - started) * 1000, 1))
        return row

    prober = threading.Thread(target=probe, name="replay-reading", daemon=True)
    started = time.monotonic()
    prober.start()
    try:
        with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
            rows = list(pool.map(one, calls))
    finally:
        stop.set()
        prober.join(30)
        http.close()
    return {"concurrency": concurrency, "wall_s": round(time.monotonic() - started, 1), "calls": rows,
            "reading": {"paths": [p.split("?")[0] for p in reading], "probes": probes}}


def load_many_plan(value: str, golden) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """--many-plan FILE: (the plan's lines, its identity). Refused before anything starts for a qid that is not an
    answerable, judged query of the golden set (or is there twice), queries that /v2/search_many would refuse, or
    filters that are not a golden query's."""
    path = data_file(value)
    known = {q["qid"]: q for q in golden.queries}
    plan: list[dict[str, Any]] = []
    for n, row in read_rows(path, "--many-plan"):
        where = f"--many-plan {path.name}:{n}"
        qid = row.get("qid")
        query = known.get(qid)
        if query is None or not query.get("answerable", True) or not golden.qrels.get(qid):
            raise Refused(f"{where}: qid {qid!r} is not an answerable, judged query of {golden.path.name}")
        if any(p["qid"] == qid for p in plan):
            raise Refused(f"{where}: qid {qid!r} is planned twice")
        queries = row.get("queries")
        if not (isinstance(queries, list) and 1 <= len(queries) <= 8 and all(
                isinstance(q, dict) and set(q) <= {"text", "mode", "weight"}
                and isinstance(q.get("text"), str) and 0 < len(q["text"].strip()) and len(q["text"]) <= 4096
                and q.get("mode", "hybrid") in PLAN_MODES
                and isinstance(q.get("weight", 1.0), (int, float)) and not isinstance(q.get("weight"), bool)
                and 0 < q.get("weight", 1.0) <= 10 for q in queries)):
            raise Refused(f"{where}: queries must be 1-8 objects of a text, a mode {list(PLAN_MODES)} and a weight "
                          "in (0, 10]")
        filters = row.get("filters", query.get("filters"))
        try:
            ev.check_query_filters({"qid": qid, "filters": filters}, path)
        except ev.GoldenError as exc:
            raise Refused(f"{where}: {exc}") from None
        plan.append({"qid": qid, "queries": queries, **({"filters": filters} if filters is not None else {})})
    return plan, {"path": str(path), "name": path.name, "sha256": file_sha256(path), "n": len(plan)}


def plan_row(row: dict[str, Any], rerank_ms: float | None) -> dict[str, Any]:
    """What VALIDATION.json keeps of one planned search_many (as eval_summary keeps a query)."""
    return {
        **{k: row.get(k) for k in ("qid", "domain", "answerable", "n_rel", "first_rel_rank", "n_hits", "latency_ms",
                                   "retries", "degraded", "error") if k in row},
        **{k: row[k] for k in ("category", "style") if row.get(k) is not None},
        "rerank_ms": rerank_ms,
        "hits": [h["id"] for h in row.get("hits") or []],
        "metrics": {m: (row.get("metrics") or {}).get(m) for m in QUERY_METRICS},
    }


def run_many_plan(url: str, token: str, plan: list[dict[str, Any]], identity: dict[str, Any], golden, db: str,
                  limit: int, max_chars: int) -> dict[str, Any]:
    """Every planned search_many, one after another, scored against the golden qrels as eval_v2 scores a search."""
    known = {q["qid"]: q for q in golden.queries}
    store = ev.Store(db)
    http = ev.Http(url, token, timeout=120.0, retries_503=2)
    rows: list[dict[str, Any]] = []
    rerank: list[float | None] = []
    try:
        for p in plan:
            query = known[p["qid"]]
            body = {"queries": p["queries"], "limit": limit, "max_chars": max_chars, "rerank": "auto",
                    **({"filters": p["filters"]} if "filters" in p else {})}
            started = time.perf_counter()
            try:
                data, retries = http.json("POST", "/v2/search_many", body)
            except Exception as exc:  # noqa: BLE001 - a failed search scores as a miss (eval_v2's error_row)
                rows.append(ev.error_row(query, f"{type(exc).__name__}: {exc}"[:300],
                                         (time.perf_counter() - started) * 1000, golden))
                rerank.append(None)
                continue
            ms = (time.perf_counter() - started) * 1000
            response = ev.Response(ev.v2_hits(data)[:limit], [str(d) for d in data.get("degraded") or []], retries)
            rows.append(ev.v2_row(query, response, ms, golden, store, False))
            rerank.append((data.get("timings_ms") or {}).get("rerank"))
    finally:
        http.close()
        store.close()
    found = [r["metrics"]["found_n@8"] for r in rows if r.get("metrics", {}).get("found_n@8") is not None]
    return {**identity, "params": {"limit": limit, "max_chars": max_chars},
            "overall": {**ev.aggregate(rows), "found_n@8": round(sum(found) / len(found), 4) if found else None},
            "latency_ms": ev.latency_summary(rows),
            "rows": [plan_row(r, ms) for r, ms in zip(rows, rerank)]}


# ---------------------------------------------------------------- main


def load_evaluator(root: Path):
    """Load the explicit operator evaluator without changing validation gates."""
    if root.is_symlink() or not root.is_dir() or any(p.is_symlink() for p in root.parents):
        raise Refused("supplied eval root must be a regular directory")
    source = root / "eval_v2.py"
    if source.is_symlink() or not source.is_file():
        raise Refused("supplied eval root has no regular eval_v2.py")
    spec = importlib.util.spec_from_file_location("eval_v2", source)
    module = importlib.util.module_from_spec(spec)
    # Dataclass decorators in evaluator code resolve the module by this name.
    previous = sys.modules.get("eval_v2")
    sys.modules["eval_v2"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop("eval_v2", None)
        else:
            sys.modules["eval_v2"] = previous
        raise
    return module


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True, help="the built root to validate (never the served one)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="loopback port for the child (0: a free one)")
    ap.add_argument("--golden", default="known_items",
                    help="golden set: a name under eval/golden (known_items, the generated known-item set; neutral, "
                          "the hand-made check set) or a directory")
    ap.add_argument("--eval-root", type=Path,
                    help="operator-provided directory containing eval_v2.py; quality gates remain unchanged")
    ap.add_argument("--no-eval", action="store_true",
                    help="no eval inputs supplied: retain build, integrity, status and legacy-search gates")
    ap.add_argument("--no-dense", action="store_true", help="serve BM25 only; never contact TEI, Qdrant or rerank")
    ap.add_argument("--expect-generation")
    ap.add_argument("--expect-fingerprint")
    ap.add_argument("--expect-docs", type=int)
    ap.add_argument("--expect-chunks", type=int)
    ap.add_argument("--expect-opt-in", metavar="TYPE[,TYPE...]",
                    help="gate status.opt_in: /v2/status opt_in_doc_types must be exactly these doc types, e.g. "
                         "powershell,system (\"\" for none; default: those of the BUILD_REPORT.json config when "
                         "build_root --opt-in-doc-types wrote them, else not checked)")
    ap.add_argument("--min", action="append", default=[], metavar="METRIC=VALUE",
                    help=f"eval floor, repeatable (defaults {DEFAULT_MINIMUMS}); METRIC=off drops one")
    ap.add_argument("--limit", type=int, default=30, help="hits per query (eval_v2's v2 default)")
    ap.add_argument("--max-chars", type=int, default=2400, help="evidence window (the edge's default)")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--startup-timeout", type=float, default=180.0, help="seconds for the child to answer /health")
    ap.add_argument("--ready-timeout", type=float,
                    help="seconds after /health for /v2/status to report the root loaded (default: the "
                         "--startup-timeout; 0 reads it once); past that the gates read the last answer")
    ap.add_argument("--eval-out", help="also write the full eval_v2 run report into this directory")
    ap.add_argument("--baseline", metavar="VALIDATION.json|ROOT",
                    help="compare with this validation of the same golden set (e.g. the root without the change)")
    ap.add_argument("--max-drop", action="append", default=[], metavar="METRIC=VALUE",
                    help="with --baseline: gate, this run's mean may fall below the baseline's by at most VALUE")
    ap.add_argument("--report-name", default=REPORT_NAME, metavar="NAME.json",
                    help=f"the report's file name in the root (default {REPORT_NAME}); another name keeps a baseline "
                         "from being overwritten by the next run")
    ap.add_argument("--replay", action="append", default=[], metavar="FILE",
                    help="after the eval, replay this file's recorded /v2/grep and /v2/search_many bodies "
                         "(repeatable; module docstring, step replay)")
    ap.add_argument("--replay-concurrency", type=int, default=1, metavar="N",
                    help="replayed calls at once (default 1)")
    ap.add_argument("--many-plan", metavar="FILE",
                    help="after the eval, one /v2/search_many per line of this plan, scored against the golden set "
                         "(module docstring, step plan)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global ev
    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    try:
        if args.no_eval and (args.eval_root is not None or args.min or args.eval_out or args.baseline
                             or args.max_drop or args.replay or args.many_plan or args.golden != "known_items"):
            raise Refused("--no-eval cannot be combined with quality-evaluation options")
        if not args.no_eval and args.eval_root is not None:
            ev = load_evaluator(args.eval_root)
        elif not args.no_eval and ev is None:
            if (REPO / "eval/eval_v2.py").is_file():
                ev = load_evaluator(REPO / "eval")
            else:
                raise Refused("quality evaluator is missing; supply --eval-root")
        check_not_served(root)
        if not (root / "sqlite" / "rag.db").is_file():
            raise Refused(f"{root} holds no sqlite/rag.db: not a built root")
        report_file = report_name(args.report_name)
        floors = {} if args.no_eval else parse_minimums(args.min)
        expected, build_report = expectations(args, root)
        golden = None
        if not args.no_eval:
            try:
                golden = ev.load_golden(args.golden)
            except (ev.GoldenError, OSError, ValueError) as exc:
                raise Refused(f"golden set {args.golden}: {exc}") from None
        port = args.port or free_port()
        check_port(port)
        if args.max_drop and not args.baseline:
            raise Refused("--max-drop compares with a baseline: pass --baseline")
        try:
            drops = cv.parse_limits(args.max_drop)
        except cv.BadInput as exc:
            raise Refused(str(exc)) from None
        baseline = None if args.no_eval else load_baseline(args.baseline, golden)
        if args.replay_concurrency < 1 or (args.replay_concurrency != 1 and not args.replay):
            raise Refused(f"--replay-concurrency {args.replay_concurrency}: a number of calls (1 or more) of a "
                          "--replay")
        replay = load_replay(args.replay) if args.replay else None
        plan = load_many_plan(args.many_plan, golden) if args.many_plan else None
    except Refused as exc:
        log(f"REFUSED: {exc}")
        return 2

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))     # a stopped job still stops its child
    token = secrets.token_hex(32)
    url = f"http://127.0.0.1:{port}"
    gates = Gates()
    result: dict[str, Any] = {
        "validator": VALIDATOR,
        "root": str(root),
        "generation": expected["generation"],
        "status": "running",
        "started_at": now(),
        "no_dense": args.no_dense,
        "expected": expected,
        "golden": None if golden is None else golden.meta(),
        "gates": gates.items,
    }
    with contextlib.suppress(subprocess.CalledProcessError, OSError):
        result["code_commit"] = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], check=True,
                                               capture_output=True, text=True).stdout.strip()
    log(f"validate_root: {root} (generation {expected['generation']}, fingerprint {expected['corpus_fingerprint']}, "
        f"{expected['n_docs']} docs, {expected['n_chunks']} chunks){' BM25 only' if args.no_dense else ''}")

    work = Path(tempfile.mkdtemp(prefix="validate-root-"))
    proc: subprocess.Popen | None = None
    http = (CoreHttp if args.no_eval else ev.Http)(url, token, timeout=120.0, retries_503=2)
    try:
        if build_report is not None:
            gates.check("build_report", build_report.get("status") == "complete"
                        and not build_report.get("failed_steps"),
                        f"status {build_report.get('status')!r}, failed steps "
                        f"{[s.get('step') for s in build_report.get('failed_steps') or []]}")
        log("fts: FTS5 integrity-check of rag.db's FTS5 tables, read-only")
        result["fts_integrity"] = fts_integrity(root / "sqlite" / "rag.db")
        gates.check("fts_integrity", result["fts_integrity"]["ok"], fts_detail(result["fts_integrity"]))
        retrieval = bm25_only_config(root, work) if args.no_dense else None
        env = child_env(dict(os.environ), root, token, no_dense=args.no_dense, retrieval_config=retrieval)
        started = time.monotonic()
        proc = start_child(env, port, work / "server.log")
        result["server"] = {"pid": proc.pid, "port": port}
        log(f"server: pid {proc.pid} on 127.0.0.1:{port}")
        healthy, why = wait_healthy(proc, http, args.startup_timeout)
        result["server"]["startup_seconds"] = round(time.monotonic() - started, 1)
        if gates.check("server", healthy, f"{why} after {result['server']['startup_seconds']} s"):
            ready_timeout = args.startup_timeout if args.ready_timeout is None else args.ready_timeout
            waited = time.monotonic()
            status, ready, why, polls = wait_ready(proc, http, ready_timeout, args.no_dense)
            result["server"].update(ready=ready, ready_seconds=round(time.monotonic() - waited, 1),
                                    status_polls=polls)
            if ready:
                log(f"status: ready {result['server']['ready_seconds']} s after /health ({polls} reads)")
            else:
                log(f"status: not ready {result['server']['ready_seconds']} s after /health ({why}); "
                    "the gates read the last answer")
            if status is None:      # no read answered: this one's error fails the run, as before
                status, _ = http.json("GET", "/v2/status")
            result["status_v2"] = status
            status_gates(gates, status, expected, args.no_dense)
            legacy, _ = http.json("POST", "/search", {"query": "OSPF NSSA type-7 LSA translation", "limit": 3})
            gates.check("legacy_search", "candidates" in legacy,
                        f"POST /search answered {len(legacy.get('candidates') or [])} candidates")

            if args.no_eval:
                result["quality_validation"] = {"status": "skipped", "message": SKIPPED_MESSAGE}
                log(SKIPPED_MESSAGE)
            else:
                log(f"eval: {golden.path.name} ({len(golden.queries)} queries, limit {args.limit}, "
                    f"max_chars {args.max_chars})")
                report = ev.evaluate(url, token, golden, str(root / "sqlite" / "rag.db"),
                                     label=f"validate-{expected['generation']}", limit=args.limit,
                                     max_chars=args.max_chars, concurrency=args.concurrency, load_state="unknown",
                                     note=f"{VALIDATOR}{' --no-dense' if args.no_dense else ''}")
                log(ev.summary_line(report))
                for domain, agg in sorted(report["by_domain"].items()):
                    log(f"eval: {domain:<11} n={agg['n']:<3} hit@1={agg['hit@1']} hit@8={agg['hit@8']}")
                result["eval"] = eval_summary(report)
                if args.eval_out:
                    out = Path(args.eval_out)
                    out.mkdir(parents=True, exist_ok=True)
                    path = out / f"{time.strftime('%Y%m%d-%H%M%S')}-{report['label']}.json"
                    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
                    result["eval"]["run_file"] = str(path)
                eval_gates(gates, report, floors)
                opt_in_leak_gate(gates, report, status)
                filter_scope_gate(gates, report)
                if baseline is not None:
                    this = {"generation": expected["generation"], "golden": golden.meta(), "eval": result["eval"],
                            "_path": str(root / report_file)}
                    comparison = cv.compare(baseline, this)
                    result["comparison"] = comparison
                    for line in cv.lines(comparison):
                        log(line)
                    for name, ok, detail in cv.drop_gates(comparison, drops):
                        gates.check(name, ok, detail)
                retried_gate(gates, report)
                found = census(report, args.limit)
                if found is not None:
                    result["census"] = found
                    log("census: " + " ".join(f"{k}={found[k]}" for k in ("misses", *(b[0] for b in CENSUS_BUCKETS),
                                                                           "beyond_30"))
                        + " (the top-8 misses' first relevant hit, by position)")
                if replay is not None:
                    calls, files = replay
                    reading = reading_paths(report["results"])
                    log(f"replay: {len(calls)} calls of {', '.join(Path(f['path']).name for f in files)} at concurrency "
                        f"{args.replay_concurrency}; reading probes {[p.split('?')[0] for p in reading] or 'none'}")
                    result["replay"] = {"files": files, **run_replay(url, token, calls, args.replay_concurrency, reading)}
                    for route, s in cv.replay_summary(result["replay"]).items():
                        log(f"replay: {route} calls={s['calls']} ok={s['n']} p50={s['p50']} p95={s['p95']} "
                            f"rerank_p50={s['rerank_p50']} truncated={s['truncated']} errors={s['errors']}")
                if plan is not None:
                    rows, identity = plan
                    log(f"many_plan: {identity['name']} ({len(rows)} search_many, limit {args.limit}, max_chars "
                        f"{args.max_chars})")
                    many = run_many_plan(url, token, rows, identity, golden, str(root / "sqlite" / "rag.db"),
                                         args.limit, args.max_chars)
                    result["many_plan"] = many
                    o, lat = many["overall"], many["latency_ms"]
                    log(f"many_plan: n={o['n']} hit@1={o['hit@1']} hit@8={o['hit@8']} ndcg@10={o['ndcg@10']} "
                        f"found_n@8={o['found_n@8']} p50={lat.get('p50')}ms p95={lat.get('p95')}ms errors={o['n_errors']}")
                    gates.check("many_plan.errors", o["n_errors"] == 0, f"{o['n_errors']} failed search_many")
                    gates.check("many_plan.degraded", o["n_degraded"] == 0,
                                f"{o['n_degraded']} search_many answers with degraded set")
    except Exception as exc:  # noqa: BLE001 - reported as a failed gate; the child is still stopped below
        gates.check("run", False, f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        http.close()
        exit_code = stop_child(proc)
        if proc is not None:
            result["server"]["exit_status"] = exit_code
            log(f"server: pid {proc.pid} stopped (status {exit_code})")
            if not gates.passed:
                result["server"]["log_tail"] = tail(work / "server.log")
                for line in result["server"]["log_tail"][-15:]:
                    log(f"  server| {line}")
        shutil.rmtree(work, ignore_errors=True)

    try:
        result["arm"] = arm_provenance(root, build_report)
    except OSError as exc:      # provenance only: the gates stand as they are
        result["arm"] = {"error": f"{type(exc).__name__}: {exc}"}
    for name, sha in (result["arm"].get("config") or {}).items():
        log(f"arm: {name} sha256={sha}")
    result["status"] = "passed" if gates.passed else "failed"
    result["finished_at"] = now()
    report_path = root / report_file
    write_json(report_path, result)
    failed = [g["name"] for g in gates.items if not g["ok"]]
    log(f"validate_root: {result['status']}{' (' + ', '.join(failed) + ')' if failed else ''}; report {report_path}")
    return 0 if gates.passed else 1


if __name__ == "__main__":
    sys.exit(main())
