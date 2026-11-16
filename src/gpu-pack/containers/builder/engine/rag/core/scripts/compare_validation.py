#!/usr/bin/env python3
"""Paired comparison of two validation runs (VALIDATION.json of scripts/validate_root.py): the A/B instrument for a
configuration change on derived roots (e.g. the reranker off in A, on in B; scripts/derive_root.py).

usage: compare_validation.py A B [--max-drop METRIC=VALUE ...] [--max-losses METRIC=COUNT ...] [--list] [--json]
       A and B are VALIDATION.json files or roots holding one; both must have run the same golden set.

Over the answerable queries with judgements (as eval_v2 averages), per metric (hit@1, hit@3, hit@8, mrr@10,
ndcg@10, found_n@8): the means, B - A, the queries B wins and loses, an exact two-sided sign test over wins vs
losses, and a paired bootstrap 95% interval of the difference (2,000 resamples, fixed seed). delivery@8 as eval_v2
computes it (judged spans delivered intact / judged spans found, over all queries) with per-query wins and losses.
Also: how many queries got the identical hit list, latency p50/p95 of each, and hit@1 per group. Printed as
lines (a deploy job's journal is where they are read) and, with --json, as JSON.

Rows that carry a golden style or a group also give the wins and losses per group (GROUPS):
    compare: group style=<style> <metric> n=<n> wins=<w> losses=<l>
When both runs replayed the same bodies (validate_root --replay), per route (grep_literal, grep_regex, search_many,
and the reading endpoints probed meanwhile) the latency of each, the rerank p50, how many calls answered the same
and of how many (grep: the matches' doc_key and line list, among the calls A finished without running out of its
budget; search_many: the top 8 hit ids, among the bodies A answered), the share of budget-truncated greps, and the
errors (failed calls, 503s and degraded answers, A and B):
    compare: replay <route> n=<n> a_p50=<ms> a_p95=<ms> b_p50=<ms> b_p95=<ms> a_rerank_p50=<ms> b_rerank_p50=<ms>
             same=<k> base=<m> a_truncated=<share> b_truncated=<share> errors=<n>
and when both ran the same many-plan (validate_root --many-plan), its paired metrics as above, prefixed:
    compare: many_plan <metric> A <a> B <b> delta <d> 95% CI [<lo>, <hi>] wins <w> losses <l> (sign p=...)
A replay or many-plan that only one run has, or that differs (another set, another concurrency or limit), is
compared not at all and says why ("compare: replay REFUSED: ...").
Exit status: 0, or 1 when a --max-drop is exceeded (B's mean below A's by more than VALUE), 2 on bad input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any

METRICS = ("hit@1", "hit@3", "hit@8", "mrr@10", "ndcg@10", "found_n@8")
DELIVERY = "delivery@8"
BOOTSTRAP = 2000
SEED = 20260928
# wins and losses per group of the rows (their golden style, their group), and on which metrics
GROUPS = (("style", ("hit@1", "hit@8")), ("domain", ("hit@1",)))
# validate_root --replay's routes (a grep is literal or regex by its body); "reading" is the probe beside them
REPLAY_ROUTES = ("grep_literal", "grep_regex", "search_many")
READING = "reading"
TOP_HITS = 8        # a search_many answer's digest: its first 8 hit ids


class BadInput(Exception):
    """A run that cannot be compared: missing, not a validation, per-query rows missing, other golden set."""


def load(path_or_root: str | Path) -> dict[str, Any]:
    path = Path(path_or_root)
    if path.is_dir():
        path = path / "VALIDATION.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BadInput(f"{path}: {type(exc).__name__}: {exc}") from None
    rows = ((data.get("eval") or {}).get("queries")) if isinstance(data, dict) else None
    if not rows or not all(isinstance(r, dict) and "qid" in r for r in rows):
        raise BadInput(f"{path}: no per-query eval rows (a validation that ran its eval is needed)")
    if not any("metrics" in r for r in rows):
        raise BadInput(f"{path}: its per-query rows carry no metrics (made before validate_root kept them)")
    data["_path"] = str(path)
    return data


def sign_test(wins: int, losses: int) -> float | None:
    """Exact two-sided binomial p-value of wins vs losses under p = 1/2 (ties left out)."""
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def bootstrap(diffs: list[float], resamples: int = BOOTSTRAP, seed: int = SEED) -> tuple[float, float] | None:
    if not diffs:
        return None
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(resamples))
    return round(means[int(0.025 * resamples)], 4), round(means[int(0.975 * resamples) - 1], 4)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 1)


def compare(a: dict[str, Any], b: dict[str, Any], metrics=METRICS) -> dict[str, Any]:
    """The paired comparison of two validation runs (their "eval" sections)."""
    ga, gb = (a.get("eval") or {}).get("golden") or a.get("golden") or {}, \
        (b.get("eval") or {}).get("golden") or b.get("golden") or {}
    if ga.get("sha256") != gb.get("sha256"):
        raise BadInput(f"different golden sets: {ga.get('name')} {str(ga.get('sha256'))[:12]} vs "
                       f"{gb.get('name')} {str(gb.get('sha256'))[:12]}")
    ra = {r["qid"]: r for r in a["eval"]["queries"]}
    rb = {r["qid"]: r for r in b["eval"]["queries"]}
    common = [q for q in ra if q in rb]
    # eval_v2's means: answerable queries with judgements
    scored = [q for q in common if ra[q].get("answerable") and ra[q].get("n_rel") and ra[q].get("metrics")
              and rb[q].get("metrics")]
    out: dict[str, Any] = {
        "a": {"generation": a.get("generation"), "path": a.get("_path")},
        "b": {"generation": b.get("generation"), "path": b.get("_path")},
        "golden": {"name": ga.get("name"), "sha256": ga.get("sha256")},
        "queries": len(common),
        "only_in_a": len(ra) - len(common),
        "only_in_b": len(rb) - len(common),
        "identical_hit_lists": sum(
            1 for q in common if ra[q].get("hits") is not None and ra[q].get("hits") == rb[q].get("hits")
        ),
        "metrics": {},
    }
    for metric in metrics:
        pairs = [
            (ra[q]["metrics"][metric], rb[q]["metrics"][metric])
            for q in scored
            if (ra[q].get("metrics") or {}).get(metric) is not None
            and (rb[q].get("metrics") or {}).get(metric) is not None
        ]
        if not pairs:
            continue
        diffs = [y - x for x, y in pairs]
        wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
        mean_a = statistics.fmean(x for x, _ in pairs)
        mean_b = statistics.fmean(y for _, y in pairs)
        changed = [q for q in scored
                   if (ra[q].get("metrics") or {}).get(metric) is not None
                   and (rb[q].get("metrics") or {}).get(metric) is not None
                   and rb[q]["metrics"][metric] != ra[q]["metrics"][metric]]
        out["metrics"][metric] = {
            "n": len(pairs), "a": round(mean_a, 4), "b": round(mean_b, 4), "delta": round(mean_b - mean_a, 4),
            "wins": wins, "losses": losses, "ties": len(pairs) - wins - losses,
            "sign_p": sign_test(wins, losses), "ci95": bootstrap(diffs),
            # the queries behind wins and losses, so a gate can count losses and a person can read each one
            "won": [q for q in changed if rb[q]["metrics"][metric] > ra[q]["metrics"][metric]],
            "lost": [q for q in changed if rb[q]["metrics"][metric] < ra[q]["metrics"][metric]],
        }
    out["top_hits"] = {q: [(ra[q].get("hits") or [None])[0], (rb[q].get("hits") or [None])[0]]
                       for q in {q for m in out["metrics"].values() for q in m["won"] + m["lost"]}}
    delivery = delivery_compare(ra, rb, scored)
    if delivery is not None:
        out["metrics"][DELIVERY] = delivery
    lat = {}
    for name, rows in (("a", ra), ("b", rb)):
        ms = [rows[q]["latency_ms"] for q in common if rows[q].get("latency_ms") is not None and not rows[q].get("error")]
        lat[name] = {"p50": percentile(ms, 0.5), "p95": percentile(ms, 0.95)}
    out["latency_ms"] = lat
    domains: dict[str, list[tuple[float, float]]] = {}
    for q in scored:
        x, y = (ra[q].get("metrics") or {}).get("hit@1"), (rb[q].get("metrics") or {}).get("hit@1")
        if x is not None and y is not None and ra[q].get("domain"):
            domains.setdefault(ra[q]["domain"], []).append((x, y))
    out["hit@1_by_domain"] = {
        d: {"n": len(p), "a": round(statistics.fmean(x for x, _ in p), 3), "b": round(statistics.fmean(y for _, y in p), 3)}
        for d, p in sorted(domains.items())
    }
    out["groups"] = group_compare(ra, rb, scored)
    for name, section in (("replay", replay_compare(a, b)), ("many_plan", many_plan_compare(a, b))):
        if section is not None:
            out[name] = section
    return out


def group_compare(ra: dict[str, dict], rb: dict[str, dict], scored: list[str]) -> dict[str, Any]:
    """{group key: {group: {metric: {n, wins, losses}}}} over the scored queries whose row (A's) names the group."""
    out: dict[str, Any] = {}
    for key, metrics in GROUPS:
        groups: dict[str, dict[str, dict[str, int]]] = {}
        for q in scored:
            group = ra[q].get(key)
            if group is None:
                continue
            for metric in metrics:
                x, y = (ra[q].get("metrics") or {}).get(metric), (rb[q].get("metrics") or {}).get(metric)
                if x is None or y is None:
                    continue
                g = groups.setdefault(str(group), {}).setdefault(metric, {"n": 0, "wins": 0, "losses": 0})
                g["n"] += 1
                g["wins"] += y > x
                g["losses"] += y < x
        if groups:
            out[key] = dict(sorted(groups.items()))
    return out


def replay_failed(call: dict[str, Any]) -> bool:
    """A replayed call that failed, answered only after a 503, or answered degraded: each is an error of the
    measurement (a search_many whose rerank was skipped would look faster)."""
    return call.get("status") != 200 or bool(call.get("retries")) or bool(call.get("degraded"))


def digest(value: Any) -> str:
    """A replayed answer's digest (validate_root --replay): the first 16 hex of the sha256 of its JSON."""
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def replay_side(calls: list[dict[str, Any]], grep: bool) -> dict[str, Any]:
    """Latency, rerank p50 and (grep) the share of budget-truncated answers of the calls that answered."""
    ok = [c for c in calls if c.get("status") == 200]
    ms = [c["ms"] for c in ok if c.get("ms") is not None]
    rerank = [c["rerank_ms"] for c in ok if c.get("rerank_ms") is not None]
    return {"n": len(ok), "p50": percentile(ms, 0.5), "p95": percentile(ms, 0.95), "rerank_p50": percentile(rerank, 0.5),
            "truncated": round(sum(bool(c.get("budget_truncated")) for c in ok) / len(ok), 4) if grep and ok else None}


def replay_summary(replay: dict[str, Any]) -> dict[str, Any]:
    """One run's replay per route (validate_root's journal): its calls, the side figures and the errors."""
    out = {}
    for route in REPLAY_ROUTES:
        calls = [c for c in replay.get("calls") or [] if c.get("route") == route]
        if calls:
            out[route] = {"calls": len(calls), **replay_side(calls, route != "search_many"),
                          "errors": sum(map(replay_failed, calls))}
    probes = (replay.get("reading") or {}).get("probes") or []
    if probes:
        out[READING] = {"calls": len(probes), **replay_side(probes, False),
                        "errors": sum(p.get("status") != 200 for p in probes)}
    return out


def replay_compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
    """The replays of A and B (validate_root --replay, the reports' "replay"), call by call; None when neither has
    one, {"refused": why} when they cannot be compared."""
    pa, pb = a.get("replay"), b.get("replay")
    if pa is None and pb is None:
        return None
    if pa is None or pb is None:
        return {"refused": f"only {'A' if pb is None else 'B'} ran a replay"}
    if [f.get("sha256") for f in pa.get("files") or []] != [f.get("sha256") for f in pb.get("files") or []]:
        return {"refused": "A and B replayed different sets (their files' sha256 differ)"}
    if pa.get("concurrency") != pb.get("concurrency"):
        return {"refused": f"A replayed at concurrency {pa.get('concurrency')}, B at {pb.get('concurrency')}"}
    ca = {c["id"]: c for c in pa.get("calls") or []}
    cb = {c["id"]: c for c in pb.get("calls") or []}
    out: dict[str, Any] = {"calls": len(ca), "concurrency": pa.get("concurrency"), "routes": {}}
    for route in REPLAY_ROUTES:
        ids = [i for i, c in ca.items() if c.get("route") == route and i in cb]
        grep = route != "search_many"
        # the calls whose answers can be expected the same: a grep A finished within its budget (one that ran
        # out could find more with more time), a search_many A answered
        base = [i for i in ids if ca[i].get("status") == 200 and not (grep and ca[i].get("budget_truncated"))]
        same = {i for i in base if cb[i].get("status") == 200 and cb[i].get("digest") == ca[i].get("digest")}
        out["routes"][route] = {
            "n": len(ids), "a": replay_side([ca[i] for i in ids], grep), "b": replay_side([cb[i] for i in ids], grep),
            "same": len(same), "base": len(base),
            "errors": sum(replay_failed(ca[i]) + replay_failed(cb[i]) for i in ids),
            "changed": [i for i in base if i not in same],
        }
    ra, rb = ((p.get("reading") or {}).get("probes") or [] for p in (pa, pb))
    out["routes"][READING] = {"n": len(ra), "a": replay_side(ra, False), "b": replay_side(rb, False),
                              "same": None, "base": None,
                              "errors": sum(p.get("status") != 200 for p in ra + rb), "changed": []}
    return out


def many_plan_compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
    """The many-plans of A and B (validate_root --many-plan, the reports' "many_plan") compared as two runs of a
    golden set are; None when neither has one, {"refused": why} when they cannot be compared."""
    pa, pb = a.get("many_plan"), b.get("many_plan")
    if pa is None and pb is None:
        return None
    if pa is None or pb is None:
        return {"refused": f"only {'A' if pb is None else 'B'} ran a many-plan"}
    if pa.get("sha256") != pb.get("sha256"):
        return {"refused": f"A and B ran different plans ({str(pa.get('sha256'))[:12]} vs {str(pb.get('sha256'))[:12]})"}
    if pa.get("params") != pb.get("params"):
        return {"refused": f"A and B ran the plan with different params ({pa.get('params')} vs {pb.get('params')})"}

    def run(side: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        golden = {"name": plan.get("name"), "sha256": plan.get("sha256")}
        return {"generation": side.get("generation"), "_path": side.get("_path"),
                "eval": {"golden": golden, "queries": plan.get("rows") or []}}

    return compare(run(a, pa), run(b, pb))


def delivery_compare(ra: dict[str, dict], rb: dict[str, dict], scored: list[str]) -> dict[str, Any] | None:
    """delivery@8 of each run as eval_v2 computes it (sum of visible_found_n@8 / sum of found_n@8), and per query
    (where both found a judged span) who delivered the larger share."""
    def micro(rows):
        pairs = [((rows[q]["metrics"] or {}).get("visible_found_n@8"), (rows[q]["metrics"] or {}).get("found_n@8"))
                 for q in scored]
        found = sum(f or 0 for _, f in pairs)
        if not found or any(v is None or f is None for v, f in pairs):
            return None
        return sum(v for v, _ in pairs) / found

    da, db = micro(ra), micro(rb)
    if da is None or db is None:
        return None
    wins = losses = 0
    for q in scored:
        ma, mb = ra[q]["metrics"], rb[q]["metrics"]
        if ma.get("found_n@8") and mb.get("found_n@8"):
            x, y = ma["visible_found_n@8"] / ma["found_n@8"], mb["visible_found_n@8"] / mb["found_n@8"]
            wins, losses = wins + (y > x), losses + (y < x)
    return {"n": len(scored), "a": round(da, 4), "b": round(db, 4), "delta": round(db - da, 4), "wins": wins,
            "losses": losses, "ties": None, "sign_p": sign_test(wins, losses), "ci95": None}


def lines(result: dict[str, Any], prefix: str = "") -> list[str]:
    """The comparison as journal lines; `prefix` names a section ("many_plan ") in each of its lines."""
    out = [f"compare: {prefix}B {result['b']['generation']} vs A {result['a']['generation']} on "
           f"{result['golden']['name']} ({result['queries']} queries; {result['identical_hit_lists']} identical hit "
           "lists)"]
    for metric, m in result["metrics"].items():
        ci = f"[{m['ci95'][0]:+.4f}, {m['ci95'][1]:+.4f}]" if m["ci95"] else "-"
        out.append(f"compare: {prefix}{metric:<10} A {m['a']:.4f}  B {m['b']:.4f}  delta {m['delta']:+.4f}  95% CI "
                   f"{ci}  wins {m['wins']} losses {m['losses']} (sign p={m['sign_p']})")
    la, lb = result["latency_ms"]["a"], result["latency_ms"]["b"]
    out.append(f"compare: {prefix}latency   A p50 {la['p50']} p95 {la['p95']} ms  B p50 {lb['p50']} p95 {lb['p95']} ms")
    if prefix:
        return out
    for domain, d in result["hit@1_by_domain"].items():
        out.append(f"compare: hit@1 {domain:<11} n={d['n']:<3} A {d['a']:.3f}  B {d['b']:.3f}  delta {d['b'] - d['a']:+.3f}")
    for key, groups in (result.get("groups") or {}).items():
        for group, metrics in groups.items():
            for metric, g in metrics.items():
                out.append(f"compare: group {key}={group.replace(' ', '_')} {metric} n={g['n']} wins={g['wins']} "
                           f"losses={g['losses']}")
    out += replay_lines(result.get("replay"))
    many = result.get("many_plan")
    if many is not None:
        out += [f"compare: many_plan REFUSED: {many['refused']}"] if "refused" in many else lines(many, "many_plan ")
    return out


def replay_lines(replay: dict[str, Any] | None) -> list[str]:
    if replay is None:
        return []
    if "refused" in replay:
        return [f"compare: replay REFUSED: {replay['refused']}"]

    def v(x: Any) -> str:
        return "-" if x is None else str(x)

    out = [f"compare: replay {replay['calls']} calls at concurrency {replay['concurrency']}"]
    for route, r in replay["routes"].items():
        a, b = r["a"], r["b"]
        out.append(f"compare: replay {route} n={r['n']} a_p50={v(a['p50'])} a_p95={v(a['p95'])} b_p50={v(b['p50'])} "
                   f"b_p95={v(b['p95'])} a_rerank_p50={v(a['rerank_p50'])} b_rerank_p50={v(b['rerank_p50'])} "
                   f"same={v(r['same'])} base={v(r['base'])} a_truncated={v(a['truncated'])} "
                   f"b_truncated={v(b['truncated'])} errors={r['errors']}")
    return out


def replay_change_lines(result: dict[str, Any]) -> list[str]:
    """One journal line per replayed call whose answer B changed (of those its route's base counts)."""
    replay = result.get("replay") or {}
    return [f"compare: replay change {route} {call}"
            for route, r in (replay.get("routes") or {}).items() for call in r.get("changed") or []]


def change_lines(result: dict[str, Any], metrics=("hit@1", "hit@8")) -> list[str]:
    """One journal line per query that won or lost on these metrics, with the top hit of each run."""
    out = []
    for metric in metrics:
        m = result["metrics"].get(metric)
        for kind in ("lost", "won"):
            for q in (m or {}).get(kind, []):
                top_a, top_b = result.get("top_hits", {}).get(q, [None, None])
                out.append(f"compare: change {metric} {kind} {q} top A {top_a} B {top_b}")
    return out


def loss_gates(result: dict[str, Any], limits: dict[str, int]) -> list[tuple[str, bool, str]]:
    """(name, ok, detail) per --max-losses: at most this many queries may score lower in B than in A."""
    gates = []
    for metric, limit in sorted(limits.items()):
        m = result["metrics"].get(metric)
        if m is None:
            gates.append((f"losses.{metric}", False, "not measured in both runs"))
            continue
        gates.append((f"losses.{metric}", m["losses"] <= limit,
                      f"{m['losses']} queries lost (at most {limit}); {m['wins']} won"))
    return gates


def parse_loss_limits(items: list[str]) -> dict[str, int]:
    limits = {}
    for item in items:
        metric, sep, value = item.partition("=")
        try:
            if not sep or metric not in METRICS or int(value) < 0:
                raise ValueError
            limits[metric] = int(value)
        except ValueError:
            raise BadInput(f"--max-losses {item!r}: expected METRIC=COUNT with METRIC one of "
                           f"{', '.join(METRICS)}") from None
    return limits


def drop_gates(result: dict[str, Any], limits: dict[str, float]) -> list[tuple[str, bool, str]]:
    """(name, ok, detail) per --max-drop: B's mean may fall below A's by at most the limit."""
    gates = []
    for metric, limit in sorted(limits.items()):
        m = result["metrics"].get(metric)
        if m is None:
            gates.append((f"compare.{metric}", False, "not measured in both runs"))
            continue
        gates.append((f"compare.{metric}", -m["delta"] <= limit,
                      f"delta {m['delta']:+.4f} (may drop at most {limit}); wins {m['wins']} losses {m['losses']}"))
    return gates


def parse_limits(items: list[str]) -> dict[str, float]:
    limits = {}
    for item in items:
        metric, sep, value = item.partition("=")
        try:
            if not sep or metric not in METRICS + (DELIVERY,):
                raise ValueError
            limits[metric] = float(value)
        except ValueError:
            raise BadInput(f"--max-drop {item!r}: expected METRIC=VALUE with METRIC one of "
                           f"{', '.join(METRICS + (DELIVERY,))}") from None
    return limits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("a", help="baseline VALIDATION.json (or its root)")
    ap.add_argument("b", help="candidate VALIDATION.json (or its root)")
    ap.add_argument("--max-drop", action="append", default=[], metavar="METRIC=VALUE")
    ap.add_argument("--max-losses", action="append", default=[], metavar="METRIC=COUNT",
                    help="at most COUNT queries may score lower in B (wins do not offset losses)")
    ap.add_argument("--list", action="store_true", help="print every query that won or lost on hit@1 and hit@8")
    ap.add_argument("--json", action="store_true", help="print the comparison as JSON too")
    args = ap.parse_args(argv)
    try:
        limits = parse_limits(args.max_drop)
        loss_limits = parse_loss_limits(args.max_losses)
        result = compare(load(args.a), load(args.b))
    except BadInput as exc:
        print(f"compare: REFUSED: {exc}", flush=True)
        return 2
    for line in lines(result):
        print(line, flush=True)
    if args.list:
        for line in change_lines(result) + replay_change_lines(result):
            print(line, flush=True)
    gates = drop_gates(result, limits) + loss_gates(result, loss_limits)
    for name, ok, detail in gates:
        print(f"compare: {'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
    if args.json:
        print(json.dumps(result, indent=1))
    return 0 if all(ok for _, ok, _ in gates) else 1


if __name__ == "__main__":
    sys.exit(main())
