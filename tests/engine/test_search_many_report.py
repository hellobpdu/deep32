"""0.1.0-alpha.3, WP-D item D3: a search says what happened. Every hit carries "reranked"; search_many carries
"degraded_detail" (each query's legs and whether it was reranked, and the rerank's outcome); and with
retrieval.json search_many.rerank_isolation one failed reranker call no longer discards the calls that answered."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import threading
import time
import unittest
from unittest import mock

from rag_core import api_v2
from rag_core.api_v2 import SearchManySettings, search_many_settings
from rag_core.breaker import CircuitBreaker
from rag_core.status import StatusService

from test_api_v2 import AUTH, FailingDense, RootClient, WordReranker

QUERIES = [{"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
           {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25"},
           {"text": "NSSA ABR elected translator", "mode": "bm25"}]
BGP = QUERIES[1]["text"]


class FailingFor(WordReranker):
    """WordReranker whose call for the queries in `fail` raises (a timeout), after the other calls have run."""

    def __init__(self, fail, breaker=None):
        super().__init__()
        self.failing = set(fail)
        self.breaker = breaker
        self.attempts = []

    def rerank(self, query, items):
        self.attempts.append(query)
        if query in self.failing:
            raise TimeoutError("rerank timed out")
        return super().rerank(query, items)


class Served(unittest.TestCase):
    retrieval = None

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(retrieval=cls.retrieval)
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def post(self, path, body):
        r = self.c.post(path, json=body, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def many(self, reranker=None, **body):
        body = {"queries": QUERIES, "limit": 8, **body}
        if reranker is None:
            return self.post("/v2/search_many", body)
        with mock.patch.object(self.orch, "reranker", reranker):
            return self.post("/v2/search_many", body)


class RerankedTests(Served):

    def test_without_a_rerank_every_hit_says_false_and_it_is_the_last_key(self):
        for path, body in (("/v2/search", {"query": "NSSA translator", "mode": "bm25", "rerank": "off"}),
                           ("/v2/search", {"query": "NSSA translator"}),
                           ("/v2/search_many", {"queries": QUERIES})):
            with self.subTest(path=path, body=body):
                hits = self.post(path, body)["hits"]
                self.assertTrue(hits)
                self.assertEqual({h["reranked"] for h in hits}, {False})
                self.assertEqual({list(h)[-1] for h in hits}, {"reranked"})

    def test_search_marks_the_hits_the_reranker_scored(self):
        reranker = WordReranker()
        reranker.candidates = 3
        with mock.patch.object(self.orch, "reranker", reranker):
            hits = self.post("/v2/search", {"query": "NSSA Type-7 translator", "mode": "bm25", "rerank": "on",
                                            "limit": 8})["hits"]
        scored = set(reranker.calls[0][1])
        self.assertEqual(len(scored), 3)
        self.assertEqual([h["reranked"] for h in hits], [h["id"] in scored for h in hits])
        self.assertEqual([h["reranked"] for h in hits][:3], [True] * 3)
        self.assertIn(False, [h["reranked"] for h in hits])

    def test_a_deep_hit_keeps_its_key_order(self):
        hits = self.post("/v2/search", {"query": "NSSA Type-7 translator", "deep": True})["hits"]
        self.assertEqual(list(hits[0])[-2:], ["tier", "reranked"])
        with_ref = [h for h in hits if h["tier"] > 1]
        self.assertTrue(all(list(h)[-3:] == ["tier", "reranked", "get_section"] for h in with_ref))

    def test_search_many_marks_them_and_a_failed_rerank_marks_none(self):
        ok = self.many(WordReranker(), rerank="on")
        sent = {cid for h in ok["hits"] for cid in [h["id"]] if h["reranked"]}
        self.assertTrue(sent)
        self.assertEqual([h["reranked"] for h in ok["hits"]],
                         [h["score"] != 0 and h["id"] in sent for h in ok["hits"]])
        failed = self.many(WordReranker(fail=True), rerank="on")
        self.assertEqual(({h["reranked"] for h in failed["hits"]}, failed["degraded"]), ({False}, ["reranker"]))


class DetailTests(Served):

    def test_the_shape_and_where_it_stands(self):
        body = self.many(rerank="off")
        self.assertEqual(list(body), ["hits", "degraded", "degraded_detail", "timings_ms", "generation"])
        self.assertEqual(body["degraded_detail"], {
            "queries": [{"index": 0, "legs_down": [], "reranked": False},
                        {"index": 1, "legs_down": [], "reranked": False},
                        {"index": 2, "legs_down": [], "reranked": False}],
            "rerank": "skipped"})
        self.assertNotIn("degraded_detail", self.post("/v2/search", {"query": "NSSA"}))

    def test_each_query_names_its_own_legs(self):
        # the root is configured without dense: the hybrid and the dense query say so, the bm25 query does not
        body = self.post("/v2/search_many", {"queries": [{"text": "NSSA translator", "mode": "bm25"},
                                                         {"text": "Type-7 LSAs"},
                                                         {"text": "BGP Identifier", "mode": "dense"}],
                                             "rerank": "off"})
        self.assertEqual([q["legs_down"] for q in body["degraded_detail"]["queries"]], [[], ["dense"], ["dense"]])
        self.assertEqual(body["degraded"], ["dense"])

    def test_a_leg_that_fails_is_named_for_the_query_it_failed(self):
        dense = FailingDense()
        with mock.patch.dict(self.orch.retrievers, {"dense": dense}), \
                mock.patch.dict(self.orch.breakers, {"dense": CircuitBreaker(failures=9, open_seconds=30)}):
            body = self.post("/v2/search_many", {"queries": [{"text": "NSSA", "mode": "bm25"},
                                                             {"text": "Type-7", "mode": "dense"}], "rerank": "off"})
        self.assertEqual([q["legs_down"] for q in body["degraded_detail"]["queries"]], [[], ["dense"]])

    def test_the_parallel_path_reports_the_same(self):
        queries = [{"text": "NSSA translator", "mode": "bm25"}, {"text": "Type-7 LSAs"}]
        one = self.post("/v2/search_many", {"queries": queries, "rerank": "off"})
        with mock.patch.dict(self.orch.config, {"search_many_parallel": True}):
            at_once = self.post("/v2/search_many", {"queries": queries, "rerank": "off"})
        self.assertEqual(at_once["degraded_detail"], one["degraded_detail"])
        self.assertEqual([q["legs_down"] for q in one["degraded_detail"]["queries"]], [[], ["dense"]])

    def test_a_full_rerank_is_ok_and_names_the_queries_reranked(self):
        body = self.many(WordReranker(), rerank="on")
        self.assertEqual(body["degraded_detail"]["rerank"], "ok")
        self.assertEqual([q["reranked"] for q in body["degraded_detail"]["queries"]], [True, True, True])
        self.assertEqual(body["degraded"], [])

    def test_a_query_with_nothing_in_the_head_is_not_reranked(self):
        reranker = WordReranker()
        reranker.search_many_candidates = 1         # the head holds one candidate
        body = self.many(reranker, rerank="on")
        flags = [q["reranked"] for q in body["degraded_detail"]["queries"]]
        self.assertEqual((body["degraded_detail"]["rerank"], sum(flags)), ("ok", len(reranker.calls)))
        self.assertIn(False, flags)

    def test_without_isolation_one_failed_call_skips_the_rerank(self):
        body = self.many(FailingFor({BGP}), rerank="on")
        self.assertEqual((body["degraded"], body["degraded_detail"]["rerank"]), (["reranker"], "skipped"))
        self.assertEqual([q["reranked"] for q in body["degraded_detail"]["queries"]], [False, False, False])

    def test_no_reranker_and_an_open_breaker_are_skipped(self):
        self.assertEqual(self.many()["degraded_detail"]["rerank"], "skipped")
        breaker = CircuitBreaker(failures=1, open_seconds=30)
        breaker.record_failure("timeout")
        reranker = FailingFor((), breaker)
        body = self.many(reranker, rerank="on")
        self.assertEqual((body["degraded"], body["degraded_detail"]["rerank"], reranker.attempts),
                         (["reranker"], "skipped", []))


class SettingTests(unittest.TestCase):

    def test_absent_is_off(self):
        self.assertEqual(search_many_settings({}), SearchManySettings())
        self.assertIs(SearchManySettings().rerank_isolation, False)
        for value in (None, {}, {"rerank_isolation": False}):
            self.assertIs(search_many_settings({"search_many": value}).rerank_isolation, False)
        self.assertIs(search_many_settings({"search_many": {"rerank_isolation": True}}).rerank_isolation, True)

    def test_other_values_are_refused(self):
        for bad in (True, [], "on", {"rerank_isolation": 1}, {"rerank_isolation": "true"},
                    {"rerank_isolation": None}, {"isolation": True}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "search_many"):
                search_many_settings({"search_many": bad})

    def test_the_start_refuses_a_bad_value(self):
        with self.assertRaisesRegex(ValueError, "rerank_isolation"):
            RootClient(retrieval={"search_many": {"rerank_isolation": "yes"}})

    def test_the_status_block_is_unchanged_without_it_and_shows_it_when_on(self):
        from test_status import Clock, Dense, Orchestrator, make_root
        import tempfile
        from pathlib import Path
        from rag_core.paths import RAGPaths
        with tempfile.TemporaryDirectory() as d:
            make_root(Path(d))
            blocks = []
            for config in ({}, {"search_many": {"rerank_isolation": False}},
                           {"search_many": {"rerank_isolation": True}}):
                orch = Orchestrator(Dense())
                orch.config = config
                svc = StatusService(orch, RAGPaths(Path(d)), generation="g", clock=Clock())
                self.addCleanup(svc.close)
                blocks.append(svc.status()["settings"]["search_many"])
        self.assertEqual(blocks, [{"parallel": 0, "rerank": "per_query"}, {"parallel": 0, "rerank": "per_query"},
                                  {"parallel": 0, "rerank": "per_query", "rerank_isolation": True}])


class IsolationTests(Served):
    retrieval = {"search_many": {"rerank_isolation": True}}

    def test_the_calls_that_answered_are_kept(self):
        plain = self.many(rerank="off")
        full = self.many(WordReranker(), rerank="on")
        reranker = FailingFor({BGP})
        body = self.many(reranker, rerank="on")
        self.assertEqual(sorted(reranker.attempts), sorted(q["text"] for q in QUERIES))    # every call was made
        self.assertEqual(body["degraded"], ["reranker"])
        self.assertEqual(body["degraded_detail"], {
            "queries": [{"index": 0, "legs_down": [], "reranked": True},
                        {"index": 1, "legs_down": [], "reranked": False},
                        {"index": 2, "legs_down": [], "reranked": True}],
            "rerank": "partial"})
        sent = {cid for _, ids in reranker.calls for cid in ids}        # the calls that answered
        by_id = {h["id"]: h for h in body["hits"]}
        fused = {h["id"]: h["score"] for h in plain["hits"]}
        for hit in body["hits"]:
            self.assertEqual(hit["reranked"], hit["id"] in sent)
            if hit["reranked"]:         # the best of the scores the answered calls gave it
                judged = [reranker.score(text, hit["id"]) for text, ids in reranker.calls if hit["id"] in ids]
                self.assertEqual(hit["score"], round(max(judged), 6))
            else:                       # its fused score, as without a rerank
                self.assertEqual(hit["score"], fused[hit["id"]])
        # the scored candidates come first, by score; the others follow in fused order (keep_each_query aside)
        scored = [h["score"] for h in body["hits"] if h["reranked"]]
        self.assertEqual(scored, sorted(scored, reverse=True))
        # the failed query keeps its place: its first-stage best (a BGP passage only it found)
        kept = [h for h in body["hits"] if list(h["per_query_ranks"]) == ["1"]]
        self.assertEqual([(h["doc_key"], h["per_query_ranks"]["1"], h["reranked"]) for h in kept[:1]],
                         [("rfc4271", 1, False)])
        self.assertEqual({h["id"] for h in body["hits"]}, {h["id"] for h in plain["hits"]})
        # where every call answers, the setting changes nothing
        self.assertEqual([(h["id"], h["score"]) for h in self.many(WordReranker(), rerank="on")["hits"]],
                         [(h["id"], h["score"]) for h in full["hits"]])

    def test_the_breaker_is_told_a_success_while_one_call_answers(self):
        breaker = CircuitBreaker(failures=1, open_seconds=30)
        self.many(FailingFor({BGP}, breaker), rerank="on")
        self.assertEqual((breaker.state, breaker.snapshot()["consecutive_failures"]), ("closed", 0))

    def test_every_call_failing_is_one_failure_and_the_fused_order(self):
        plain = self.many(rerank="off")
        breaker = CircuitBreaker(failures=2, open_seconds=30)
        reranker = FailingFor({q["text"] for q in QUERIES}, breaker)
        body = self.many(reranker, rerank="on")
        self.assertEqual((body["degraded"], body["degraded_detail"]["rerank"]), (["reranker"], "skipped"))
        self.assertEqual([h["id"] for h in body["hits"]], [h["id"] for h in plain["hits"]])
        self.assertEqual({h["reranked"] for h in body["hits"]}, {False})
        self.assertEqual((breaker.state, breaker.snapshot()["consecutive_failures"],
                          breaker.snapshot()["last_error_kind"]), ("closed", 1, "timeout"))
        self.many(reranker, rerank="on")
        self.assertEqual(breaker.state, "open")                 # two requests, two failures

    def test_a_half_open_trial_that_partly_answers_closes_the_breaker(self):
        clock = [100.0]
        breaker = CircuitBreaker(failures=1, open_seconds=30, clock=lambda: clock[0])
        breaker.record_failure("timeout")
        clock[0] += 31
        self.assertEqual(breaker.state, "half-open")
        body = self.many(FailingFor({BGP}, breaker), rerank="on")
        self.assertEqual((breaker.state, body["degraded_detail"]["rerank"]), ("closed", "partial"))

    def test_behind_the_rerank_gate_it_is_the_same(self):
        from rag_core.pipeline import StageGate
        gate = StageGate(1, 8, wait_seconds=10)
        with mock.patch.object(self.orch, "rerank_gate", gate):
            body = self.many(FailingFor({BGP}), rerank="on")
        self.assertEqual((body["degraded_detail"]["rerank"], gate.snapshot()["busy"], gate.snapshot()["passed"]),
                         ("partial", 0, 1))


class CallTests(unittest.TestCase):
    """api_v2.rerank_by_query with `failures`: every call is waited for, and none raises."""

    @staticmethod
    def inputs(n):
        queries = [api_v2.SubQuery(text=f"q{i}") for i in range(n)]
        head = [{"chunk_id": f"c{i}", "fused_score": 1.0 / (i + 1), "per_query_ranks": {str(i): 1}}
                for i in range(n)]
        rows = {c["chunk_id"]: {"text": f"text of {c['chunk_id']}", "heading_path": None} for c in head}
        return queries, head, rows

    def test_a_failed_call_is_noted_and_the_others_are_waited_for(self):
        release = threading.Event()
        finished = []

        class Reranker:
            def rerank(self, query, items):
                if query == "q1":
                    raise TimeoutError("rerank timed out")
                release.wait(15)
                finished.append(query)
                return [dict(item, rerank_score=float(query[1:]) + 1) for item in items]

        queries, head, rows = self.inputs(3)
        threading.Timer(0.3, release.set).start()
        started = time.monotonic()
        failures = {}
        out, scores = api_v2.rerank_by_query(Reranker(), queries, head, rows, failures=failures)
        self.assertGreaterEqual(time.monotonic() - started, 0.25)       # it waited for q0 and q2
        self.assertEqual(sorted(finished), ["q0", "q2"])
        self.assertEqual((list(failures), type(failures["1"])), (["1"], TimeoutError))
        self.assertEqual(scores, {"0": {"c0": 1.0}, "2": {"c2": 3.0}})
        self.assertEqual([(c["chunk_id"], c.get("rerank_score")) for c in out],
                         [("c2", 3.0), ("c0", 1.0), ("c1", None)])

    def test_every_call_failing_returns_the_head_and_no_scores(self):
        class Reranker:
            def rerank(self, query, items):
                raise TimeoutError("rerank timed out")

        queries, head, rows = self.inputs(3)
        failures = {}
        out, scores = api_v2.rerank_by_query(Reranker(), queries, head, rows, failures=failures)
        self.assertEqual((scores, list(failures)), ({}, ["0", "1", "2"]))
        self.assertEqual([c["chunk_id"] for c in out], ["c0", "c1", "c2"])
        self.assertFalse(any("rerank_score" in c for c in out))

    def test_without_failures_the_first_failure_still_raises(self):
        class Reranker:
            def rerank(self, query, items):
                raise TimeoutError("rerank timed out")

        with self.assertRaises(TimeoutError):
            api_v2.rerank_by_query(Reranker(), *self.inputs(2))


if __name__ == "__main__":
    unittest.main()
