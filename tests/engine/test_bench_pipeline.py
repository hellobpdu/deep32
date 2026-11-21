"""scripts/bench_pipeline.py: the T5 load (c1 searches, then bursts of search_many with searches and greps beside
them) against a root served with the rerank gate, and its report. The phases run against the tiny root through a
TestClient; main()'s refusals come before anything is served. No network."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_core.breaker import CircuitBreaker
from test_api_v2 import AUTH
from test_pipeline import MANY, QUERIES, SERVING_ON, PipelineCase, TrackingReranker

REPO = engine_target.REPO


def load_bench():
    spec = importlib.util.spec_from_file_location("bench_pipeline", REPO / "scripts" / "bench_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_pipeline"] = module
    spec.loader.exec_module(module)
    return module


bench = load_bench()


class ClientHttp:
    """eval_v2.Http's request() and json() over a TestClient."""

    def __init__(self, client):
        self.client = client

    def request(self, method, path, body=None, headers=None):
        r = self.client.request(method, path, json=body, headers=AUTH)
        return r.status_code, dict(r.headers), r.content, 0

    def json(self, method, path, body=None):
        status, _, payload, retries = self.request(method, path, body)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        return json.loads(payload), retries


class RunBenchTests(PipelineCase):

    def run_bench(self, serving, reranker=None, **kw):
        reranker = reranker or TrackingReranker(pause=0.01)
        root = self.root(serving=serving, reranker=reranker)
        args = dict(c1=4, bursts=2, burst=3, per_many=4, searches=2,
                    greps=[{"pattern": "Type-7"}, {"pattern": r"translate\s+type7", "regex": True}])
        args.update(kw)
        return bench.run_bench(ClientHttp(root.client), QUERIES + [t for many in MANY for t in many], **args), \
            reranker

    def test_the_phases_and_their_summaries_with_the_gate(self):
        report, reranker = self.run_bench(SERVING_ON)
        c1 = report["phases"]["c1"]["summary"]["/v2/search"]
        self.assertEqual((c1["calls"], c1["ok"], c1["errors"], c1["degraded"], c1["unreranked"]), (4, 4, 0, 0, 0))
        self.assertLess(c1["rerank_wait_ms"]["max"], 5.0)            # one at a time: nobody ahead
        burst = report["phases"]["burst"]["summary"]
        self.assertEqual(set(burst), {"/v2/search", "/v2/search_many", "/v2/grep"})
        self.assertEqual((burst["/v2/search_many"]["calls"], burst["/v2/search"]["calls"],
                          burst["/v2/grep"]["calls"]), (2 * 3, 2 * 2, 2 * 2))
        for route in ("/v2/search", "/v2/search_many"):
            s = burst[route]
            self.assertEqual((s["errors"], s["http_503"], s["degraded"], s["unreranked"]), (0, 0, 0, 0), route)
            self.assertEqual(s["rerank_wait_ms"]["n"], s["ok"])
        self.assertEqual(burst["/v2/grep"]["truncated"], 0)
        self.assertNotIn("rerank_wait_ms", burst["/v2/grep"])
        hold = report["phases"]["burst"]["gate_hold"]
        self.assertEqual(hold["search_many_rerank_ms"]["n"], 6)
        self.assertEqual(hold["suggested_rerank_wait_seconds"], 10.0)     # the floor: these reranks are fast
        self.assertEqual(report["pipeline_before"]["rerank_gate"]["passed"], 0)
        self.assertEqual(report["pipeline_after"]["rerank_gate"]["passed"], 4 + 2 * (3 + 2))
        self.assertEqual(report["pipeline_after"]["rerank_gate"]["timed_out"], 0)
        self.assertEqual(len(report["phases"]["burst"]["rows"]), 2 * (3 + 2 + 2))
        counts = report["gate_counts"]
        self.assertEqual({k: counts[k] for k in ("gate_timeouts", "rerank_applied", "calls", "timed_out", "overdue",
                                                 "passed", "late", "wait_ms", "on_timeout")},
                         {"gate_timeouts": 0, "rerank_applied": 1.0, "calls": 4 + 2 * (3 + 2), "timed_out": 0,
                          "overdue": 0, "passed": 4 + 2 * (3 + 2), "late": 0, "wait_ms": 10000.0, "on_timeout": "wait"})
        self.assertIsNone(counts["breaker_opens"])          # this reranker has no breaker: not measured

    def test_without_a_pipeline_the_report_says_so(self):
        report, _ = self.run_bench(None, bursts=1)
        self.assertIsNone(report["pipeline_before"])
        self.assertIsNone(report["pipeline_after"])
        s = report["phases"]["burst"]["summary"]["/v2/search_many"]
        self.assertEqual((s["unreranked"], s["rerank_wait_ms"]["n"]), (0, 0))
        self.assertEqual(report["phases"]["burst"]["gate_hold"]["search_many_rerank_ms"]["n"], 3)
        self.assertEqual(report["gate_counts"], {"gate_timeouts": None, "breaker_opens": None, "rerank_applied": 1.0,
                                                 "calls": 4 + 3 + 2})

    def test_an_overdue_wait_is_a_gate_timeout_although_wait_never_times_out(self):
        # rerank_on_timeout "wait", the default: past rerank_wait_seconds a search keeps its place and is reranked,
        # so the gate's timed_out stays 0 however long it waits; "0 gate timeouts" must count the overdue ones
        reranker = TrackingReranker(pause=0.1, breaker=CircuitBreaker())
        report, _ = self.run_bench({**SERVING_ON, "pipeline": {"rerank_gate": 1, "rerank_wait_seconds": 0.02}},
                                   reranker=reranker, c1=1, bursts=1)
        counts = report["gate_counts"]
        self.assertEqual((counts["timed_out"], counts["rerank_applied"], counts["breaker_opens"]), (0, 1.0, 0))
        self.assertGreaterEqual(counts["overdue"], 1)
        self.assertGreaterEqual(counts["late"], 1)                  # and the answers say they waited that long
        self.assertGreaterEqual(counts["max_wait_ms"], 20)
        self.assertEqual(counts["gate_timeouts"], max(counts["overdue"], counts["late"]))

    def test_under_skip_the_searches_that_timed_out_are_gate_timeouts_and_go_unreranked(self):
        reranker = TrackingReranker(pause=0.1, breaker=CircuitBreaker())
        report, _ = self.run_bench({**SERVING_ON, "pipeline": {"rerank_gate": 1, "rerank_wait_seconds": 0.02,
                                                               "rerank_on_timeout": "skip"}},
                                   reranker=reranker, c1=1, bursts=1)
        counts = report["gate_counts"]
        self.assertGreaterEqual(counts["timed_out"], 1)
        self.assertGreaterEqual(counts["gate_timeouts"], counts["timed_out"] + counts["overdue"])
        self.assertLess(counts["rerank_applied"], 1.0)
        self.assertEqual(counts["breaker_opens"], 0)                # a queue timeout is no breaker failure

    def test_a_breaker_that_opens_is_counted_once(self):
        reranker = TrackingReranker(pause=0, breaker=CircuitBreaker(failures=1, open_seconds=30))
        reranker.fail = True
        report, _ = self.run_bench(SERVING_ON, reranker=reranker, c1=2, bursts=1)
        counts = report["gate_counts"]
        # the first search's failure opens it; everything after skips the rerank (and the gate) while it is open
        self.assertEqual((counts["breaker_opens"], counts["rerank_applied"], counts["gate_timeouts"]), (1, 0.0, 0))
        self.assertEqual(len(reranker.calls), 1)

    def test_a_degraded_or_failed_answer_is_counted(self):
        reranker = TrackingReranker(pause=0)
        reranker.fail = True
        root = self.root(reranker=reranker)
        report = bench.run_bench(ClientHttp(root.client), QUERIES, c1=2, bursts=1, burst=1, per_many=2,
                                 searches=0, greps=[])
        c1 = report["phases"]["c1"]["summary"]["/v2/search"]
        self.assertEqual((c1["degraded"], c1["degraded_by_kind"], c1["unreranked"]), (2, {"reranker": 2}, 2))


class HelperTests(unittest.TestCase):

    def test_percentiles_and_the_suggested_wait(self):
        self.assertIsNone(bench.percentile([], 50))
        self.assertEqual([bench.percentile(list(range(1, 101)), p) for p in (50, 95, 99, 100)], [50, 95, 99, 100])
        self.assertEqual(bench.percentile([7.0], 99), 7.0)
        rows = [{"route": "/v2/search_many", "status": 200, "rerank": ms} for ms in (1000, 2000, 4000, 5000)]
        rows.append({"route": "/v2/search", "status": 200, "rerank": 9000})              # not a search_many
        rows.append({"route": "/v2/search_many", "status": 503})
        hold = bench.gate_hold(rows)
        self.assertEqual((hold["search_many_rerank_ms"]["p99"], hold["suggested_rerank_wait_seconds"]), (5000, 15.0))
        self.assertIsNone(bench.gate_hold([])["suggested_rerank_wait_seconds"])

    def test_gate_counts_and_the_line_trials_p9_reads(self):
        gate = {"passed": 12, "refused": 0, "timed_out": 0, "overdue": 0, "max_wait_ms": 900.0, "wait_seconds": 10.0,
                "on_timeout": "wait"}
        before = {"rerank_gate": {**gate, "passed": 2, "max_wait_ms": 0.0}, "reranker_breaker_opens": 1}
        rows = [{"route": "/v2/search", "status": 200, "rerank": 50.0, "rerank_wait": 900.0, "degraded": []},
                {"route": "/v2/search_many", "status": 200, "rerank": 80.0, "rerank_wait": 0.0, "degraded": []},
                {"route": "/v2/grep", "status": 200, "degraded": []}]           # no rerank: not counted

        def counts(after_gate=None, extra=(), opens=1):
            return bench.gate_counts(before, {"rerank_gate": {**gate, **(after_gate or {})},
                                              "reranker_breaker_opens": opens}, rows + list(extra))

        clean = counts()
        self.assertEqual({k: clean[k] for k in ("gate_timeouts", "breaker_opens", "rerank_applied", "calls", "passed")},
                         {"gate_timeouts": 0, "breaker_opens": 0, "rerank_applied": 1.0, "calls": 2, "passed": 10})
        self.assertEqual(counts({"overdue": 3})["gate_timeouts"], 3)                 # "wait": timed_out stays 0
        self.assertEqual(counts({"timed_out": 2, "overdue": 1})["gate_timeouts"], 3)
        self.assertEqual(counts({"max_wait_ms": 10000.0})["gate_timeouts"], 1)       # the longest wait reached it
        late = {"route": "/v2/search_many", "status": 200, "rerank": 70.0, "rerank_wait": 12000.0, "degraded": []}
        self.assertEqual(counts({"max_wait_ms": 12000.0}, [late, late])["gate_timeouts"], 2)
        self.assertEqual(counts(opens=3)["breaker_opens"], 2)
        unreranked = [{"route": "/v2/search", "status": 200, "rerank": None, "degraded": ["reranker"]},
                      {"route": "/v2/search_many", "status": 200, "rerank": 60.0, "degraded": ["reranker"]},
                      {"route": "/v2/search", "status": 503}, {"route": "/v2/search", "status": None, "error": "X"}]
        self.assertEqual(counts(extra=unreranked)["rerank_applied"], round(2 / 6, 4))
        # no gate: its counts are not measured
        self.assertEqual(bench.gate_counts(None, None, rows),
                         {"gate_timeouts": None, "breaker_opens": None, "rerank_applied": 1.0, "calls": 2})
        self.assertIsNone(bench.gate_counts(None, None, [])["rerank_applied"])
        # the line: the contract's three first, as key=value, none where not measured
        line = bench.counts_line(clean)
        self.assertTrue(line.startswith("bench: gate_timeouts=0 breaker_opens=0 rerank_applied=1.0 "), line)
        self.assertIn(" overdue=0 ", line)
        self.assertEqual(bench.counts_line(bench.gate_counts(None, None, rows)),
                         "bench: gate_timeouts=none breaker_opens=none rerank_applied=1.0 calls=2")

    def test_a_request_that_fails_is_a_row_not_an_exception(self):
        class Down:
            def request(self, method, path, body=None):
                raise ConnectionRefusedError("down")

        row = bench.call(Down(), "/v2/search", {"query": "x"})
        self.assertEqual((row["status"], row["error"]), (None, "ConnectionRefusedError"))
        self.assertEqual(bench.summary([row])["/v2/search"]["errors"], 1)

    def test_grep_bodies_are_checked(self):
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "greps.jsonl"
            good.write_text('{"pattern": "Type-7"}\n\n{"pattern": "x", "regex": true}\n', encoding="utf-8")
            self.assertEqual(len(bench.load_greps(str(good))), 2)
            bad = Path(d) / "bad.jsonl"
            bad.write_text('{"text": "Type-7"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not a /v2/grep body"):
                bench.load_greps(str(bad))
        self.assertEqual(bench.load_greps(None), [])


@unittest.skipIf(bench.ev is None, "bench_pipeline.main reads its queries through the quality evaluator (eval_v2.py), "
                                   "which this repository does not carry")
class MainTests(unittest.TestCase):

    def test_refusals_come_before_anything_is_served(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bench.vr, "start_child") as start, \
                mock.patch.object(bench.vr, "log") as log:
            self.assertEqual(bench.main(["--root", str(Path(d) / "none")]), 2)
            root = Path(d) / "root"
            (root / "sqlite").mkdir(parents=True)
            (root / "sqlite" / "rag.db").write_bytes(b"")
            for extra, why in ((["--queries", "9"], "--queries must be 1-8"),
                               (["--golden", "no_such_set"], "no_such_set"),
                               (["--report-name", "../x.json"], "--report-name")):
                with self.subTest(extra=extra):
                    log.reset_mock()
                    self.assertEqual(bench.main(["--root", str(root), *extra]), 2)
                    self.assertIn(why, " ".join(str(c.args[0]) for c in log.call_args_list))
        start.assert_not_called()

    def test_a_burst_the_root_cannot_admit_at_once_is_refused(self):
        # every request of a burst takes an admission slot: past max_concurrent_searches + max_queued_searches
        # the rest would be answered 503 at once, by the bench's shape, not the arm's
        past = bench.vr.Refused("past the admission check")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bench.vr, "start_child") as start, \
                mock.patch.object(bench.vr, "log") as log, mock.patch.object(bench.vr, "check_port", side_effect=past):
            root = Path(d) / "root"
            (root / "sqlite").mkdir(parents=True)
            (root / "sqlite" / "rag.db").write_bytes(b"")
            greps = Path(d) / "greps.jsonl"

            def refusal(*extra, greps_n=0, serving=None):
                if serving is not None:
                    (root / "config").mkdir(exist_ok=True)
                    (root / "config" / "serving.json").write_text(json.dumps(serving), encoding="utf-8")
                greps.write_text('{"pattern": "Type-7"}\n' * greps_n, encoding="utf-8")
                log.reset_mock()
                self.assertEqual(bench.main(["--root", str(root), "--greps", str(greps), *extra]), 2)
                return " ".join(str(c.args[0]) for c in log.call_args_list)

            # the repo's serving.json: 1 at once, 16 queued
            self.assertIn("past the admission check", refusal("--burst", "4", "--searches", "13"))
            self.assertIn("admits 1 at once and queues 16", refusal("--burst", "4", "--searches", "14"))
            # the T5 arm's 4 + 12: the default burst of 4 leaves room for 12 grep bodies, not 13
            t5 = {"max_concurrent_searches": 4, "max_queued_searches": 12}
            self.assertIn("past the admission check", refusal(greps_n=12, serving=t5))
            why = refusal(greps_n=13, serving=t5)
            self.assertIn("sends 17 requests at once (--burst 4, --searches 0, 13 grep bodies)", why)
            self.assertIn("Send at most 16", why)
            self.assertIn("serving.json admission", refusal(serving={"max_queued_searches": "lots"}))
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
