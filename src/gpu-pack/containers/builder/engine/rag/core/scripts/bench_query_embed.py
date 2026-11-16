#!/usr/bin/env python3
"""EXPERIMENTAL: the CPU query-embedding trial (REVIEW-2026-09-27 #4). Not used by the service.

usage: bench_query_embed.py --model-dir DIR [--queries FILE | --golden known_items] [-n 70] [--threads N]
                            [--concurrency 1,4] [--reference FILE] [--write-vectors FILE] [--out REPORT.json]
                            [--allow-network]

Loads a local copy of the dense model (a Hugging Face snapshot directory with config.json, model.safetensors and
tokenizer.json, e.g. TEI's copy of jinaai/jina-embeddings-v5-text-small-retrieval), embeds N sample queries on
the CPU in float32 exactly as the dense retriever sends them to TEI ("Query: <text>"; TEI serves this model with
last-token pooling, float32, normalized), and reports:

  latency   per-query wall time at each concurrency level (1 and 4 by default: queries in flight at once, each
            one text, as deep32-engine would embed a query variant): p50, p95, max, mean, queries per second
  cosine    against reference vectors from a file, e.g. the GPU fp32 vectors TEI returned for the same texts:
            JSON lines {"text": "<the exact text embedded>", "embedding": [...]} (or a JSON list of them)
  verdict   REVIEW #4's pass criteria: cosine >= 0.999 on every query that has a reference, and p95 <= 400 ms at
            concurrency 4. Only meaningful on deep32-engine's CPU type (E5-2650 v2: AVX, no AVX2/FMA); the report
            records this machine's CPU so a run elsewhere is not mistaken for the trial.

No network by default: Hugging Face offline mode is forced and only files in --model-dir are read. The script
never calls TEI; get reference vectors separately (at an idle time) and pass them with --reference.
Memory: about 2.5 GB for the 0.6B model in float32, so run it in a memory-capped scope.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import threading
import time

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
PASS_COSINE = 0.999
PASS_P95_MS = 400.0
PASS_CONCURRENCY = 4
DEFAULT_PREFIX = "Query: "


def query_text(text: str, prefix: str = DEFAULT_PREFIX) -> str:
    """What the dense retriever embeds for a query (rag_core.retrievers.dense.DenseRetriever._query_text)."""
    text = text.strip()
    if not text:
        return ""
    if text.lower().startswith(prefix.strip().lower()):
        return text
    return f"{prefix}{text}"


def load_queries(path: str | None, golden: str, n: int) -> list[str]:
    """Query texts: a file (JSON lines with "text", a JSON list, or plain lines), else a vendored golden set."""
    if path:
        raw = Path(path).read_text(encoding="utf-8")
        stripped = raw.lstrip()
        if stripped.startswith("["):
            items = json.loads(raw)
        elif stripped.startswith("{"):
            items = [json.loads(line) for line in raw.splitlines() if line.strip()]
        else:
            items = [line for line in raw.splitlines() if line.strip()]
        texts = [item["text"] if isinstance(item, dict) else str(item) for item in items]
    else:
        golden_dir = Path(golden) if "/" in golden else REPO / "eval" / "golden" / golden
        texts = [json.loads(line)["text"] for line in
                 (golden_dir / "queries.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return texts[:n]


def load_reference(path: str) -> dict[str, list[float]]:
    raw = Path(path).read_text(encoding="utf-8")
    items = json.loads(raw) if raw.lstrip().startswith("[") else [
        json.loads(line) for line in raw.splitlines() if line.strip()]
    return {item["text"]: [float(x) for x in item["embedding"]] for item in items}


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector sizes differ: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile, as eval_v2's latency summary."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def measure(embed: Callable[[str], list[float]], texts: list[str], concurrency: int) -> dict[str, Any]:
    """Every text embedded once with `concurrency` in flight; per-text wall latency in ms."""
    latencies: list[float] = []
    lock = threading.Lock()

    def one(text: str) -> None:
        started = time.perf_counter()
        embed(text)
        elapsed = (time.perf_counter() - started) * 1000
        with lock:
            latencies.append(elapsed)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(one, texts))
    wall = time.perf_counter() - started
    return {
        "concurrency": concurrency, "n": len(latencies),
        "p50_ms": round(percentile(latencies, 0.50), 1), "p95_ms": round(percentile(latencies, 0.95), 1),
        "max_ms": round(max(latencies), 1), "mean_ms": round(statistics.fmean(latencies), 1),
        "qps": round(len(latencies) / wall, 2) if wall else None,
    }


def compare(vectors: dict[str, list[float]], reference: dict[str, list[float]]) -> dict[str, Any]:
    scores = {text: cosine(vec, reference[text]) for text, vec in vectors.items() if text in reference}
    if not scores:
        return {"n": 0, "note": "no reference vector matches an embedded text (compare the exact texts, prefix "
                                "included)"}
    values = sorted(scores.values())
    worst = min(scores, key=scores.get)
    return {"n": len(values), "min": round(values[0], 6), "mean": round(statistics.fmean(values), 6),
            "below_pass": sum(v < PASS_COSINE for v in values), "worst_text": worst[:120]}


def cpu_info() -> dict[str, Any]:
    info: dict[str, Any] = {"machine": platform.machine(), "cpu_count": os.cpu_count()}
    try:
        text = Path("/proc/cpuinfo").read_text()
        info["model"] = next((line.split(":", 1)[1].strip() for line in text.splitlines()
                              if line.startswith("model name")), None)
        flags = next((line.split(":", 1)[1].split() for line in text.splitlines() if line.startswith("flags")), [])
        info.update({flag: flag in flags for flag in ("avx", "avx2", "fma", "avx512f")})
    except OSError:
        pass
    return info


class CpuEmbedder:
    """The model on the CPU in float32: tokenizer.json through `tokenizers` (as TEI tokenizes, post-processor
    included), the transformer through transformers, last-token pooling, L2 normalization."""

    def __init__(self, model_dir: Path, threads: int | None = None) -> None:
        import torch
        from tokenizers import Tokenizer
        from transformers import AutoModel
        from transformers.utils import logging as hf_logging

        hf_logging.disable_progress_bar()           # the journal wants result lines, not progress bars
        if threads:
            torch.set_num_threads(threads)
        self.torch = torch
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.model = AutoModel.from_pretrained(str(model_dir), dtype=torch.float32, local_files_only=True)
        self.model.eval()
        self.threads = torch.get_num_threads()

    def __call__(self, text: str) -> list[float]:
        torch = self.torch
        ids = self.tokenizer.encode(text).ids
        with torch.inference_mode():
            out = self.model(input_ids=torch.tensor([ids]), attention_mask=torch.ones(1, len(ids), dtype=torch.long))
            vector = out.last_hidden_state[0, -1]
            vector = torch.nn.functional.normalize(vector, dim=-1)
        return vector.tolist()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model-dir", required=True, help="local model snapshot (config.json, model.safetensors, "
                                                       "tokenizer.json)")
    ap.add_argument("--queries", help="query texts: JSON lines with text, a JSON list, or plain lines")
    ap.add_argument("--golden", default="known_items",
                    help="golden set under eval/golden whose queries to use (default known_items)")
    ap.add_argument("-n", type=int, default=70, help="number of queries (default 70)")
    ap.add_argument("--threads", type=int, help="torch intra-op threads (default: torch's choice)")
    ap.add_argument("--concurrency", default="1,4", help="comma-separated levels (default 1,4)")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX, help="query prefix the dense retriever adds")
    ap.add_argument("--reference", help="reference vectors: JSON lines {text, embedding}")
    ap.add_argument("--write-vectors", help="write this run's vectors as JSON lines {text, embedding}")
    ap.add_argument("--out", help="write the JSON report here (default: stdout only)")
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--allow-network", action="store_true",
                    help="do not force Hugging Face offline mode (files still come from --model-dir only)")
    args = ap.parse_args(argv)

    if not args.allow_network:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    model_dir = Path(args.model_dir).expanduser().resolve()
    for name in ("config.json", "tokenizer.json"):
        if not (model_dir / name).is_file():
            print(f"REFUSED: {model_dir} has no {name}", file=sys.stderr)
            return 2
    levels = [int(c) for c in args.concurrency.split(",") if c.strip()]
    texts = [query_text(t, args.prefix) for t in load_queries(args.queries, args.golden, args.n)]
    texts = [t for t in texts if t]

    started = time.perf_counter()
    embed = CpuEmbedder(model_dir, args.threads)
    load_s = round(time.perf_counter() - started, 1)
    print(f"EXPERIMENTAL bench_query_embed: {model_dir.name}, {len(texts)} queries, {embed.threads} threads, "
          f"loaded in {load_s} s", flush=True)
    for text in texts[: args.warmup]:
        embed(text)

    vectors = {text: embed(text) for text in texts}
    report: dict[str, Any] = {
        "experimental": True,
        "trial": "REVIEW-2026-09-27 #4: query embedding on deep32-engine's CPU",
        "model_dir": str(model_dir), "dim": len(next(iter(vectors.values()))) if vectors else None,
        "n_queries": len(texts), "threads": embed.threads, "load_seconds": load_s, "cpu": cpu_info(),
        "python": platform.python_version(), "latency": [], "cosine": None,
    }
    for level in levels:
        result = measure(embed, texts, level)
        report["latency"].append(result)
        print(f"concurrency {level}: p50 {result['p50_ms']} ms, p95 {result['p95_ms']} ms, max {result['max_ms']} ms, "
              f"{result['qps']} q/s", flush=True)
    if args.reference:
        report["cosine"] = compare(vectors, load_reference(args.reference))
        print(f"cosine vs reference: {report['cosine']}", flush=True)
    if args.write_vectors:
        with open(args.write_vectors, "w", encoding="utf-8") as f:
            for text, vec in vectors.items():
                f.write(json.dumps({"text": text, "embedding": vec}) + "\n")

    at = next((r for r in report["latency"] if r["concurrency"] == PASS_CONCURRENCY), None)
    cos = report["cosine"] or {}
    report["verdict"] = {
        "cosine_ok": (cos.get("n", 0) > 0 and cos["min"] >= PASS_COSINE) if args.reference else None,
        "p95_at_4_ok": at["p95_ms"] <= PASS_P95_MS if at else None,
        "production_cpu": not report["cpu"].get("avx2", True),
        "note": "the trial passes only on deep32-engine's CPU type (AVX, no AVX2/FMA) with both checks true",
    }
    print(f"verdict: {report['verdict']}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
