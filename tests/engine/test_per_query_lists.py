"""0.1.0-alpha.3, WP-D item D7 (a bench switch, default unchanged): retrieval.json search_many_rerank
"per_query_lists" (rag_core.api_v2.rerank_lists). Each query's own first N candidates are reranked in a call of
their own and the reranked lists are fused by rank; N is reranker.search_many_candidates (null: candidates).
Without the key, and with "per_query", search_many answers as before (the bodies pinned from 4f4fe78 include two
reranked search_many shapes: tests/test_fold_identical.py)."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest
from unittest import mock

from rag_core.api_v2 import RERANK_MODES, SubQuery, rerank_lists, search_many_settings
from rag_core.breaker import CircuitBreaker

import test_fold_identical
from test_api_v2 import AUTH, RootClient, WordReranker

LISTS = {"search_many_rerank": "per_query_lists"}
QUERIES = [{"text": "Type-7 LSAs NSSA translator", "mode": "bm25"},
           {"text": "ORIGINATOR_ID BGP Identifier", "mode": "bm25"},
           {"text": "NSSA ABR elected translator", "mode": "bm25"}]


class PerQueryIsTheBodyBefore(test_fold_identical.OffTests):
    """search_many_rerank "per_query", the default, written out: the bodies 4f4fe78 sent."""
    retrieval = {"search_many_rerank": "per_query"}


class Scripted:
    """A reranker whose answer for a query is written down: {query: [chunk ids, best first]} (ids left out are
    returned without a score)."""

    def __init__(self, order, fail=()):
        self.order, self.fail, self.calls = order, set(fail), []

    def rerank(self, query, items):
        self.calls.append((query, [i["chunk_id"] for i in items]))
        if query in self.fail:
            raise TimeoutError("rerank timed out")
        ranked = self.order[query]
        by_id = {i["chunk_id"]: i for i in items}
        out = [dict(by_id[cid], rerank_score=1.0 - n / 10) for n, cid in enumerate(ranked) if cid in by_id]
        return out + [dict(i) for i in items if i["chunk_id"] not in ranked]


def cand(cid, **ranks):
    return {"chunk_id": cid, "fused_score": 0.0, "retrievers": ["bm25"],
            "per_query_ranks": {k[1:]: v for k, v in ranks.items()}}


ROWS = {c: {"text": f"text of {c}", "heading_path": [], "doc_id": "d"} for c in "abcdefg"}


class RerankListsTests(unittest.TestCase):

    def run_lists(self, head, order, queries=None, depth=3, k=60, **kw):
        queries = queries or [SubQuery(text="q0"), SubQuery(text="q1")]
        reranker = Scripted(order, **kw)
        failures = {} if kw.get("fail") else None
        out, scores = rerank_lists(reranker, queries, head, ROWS, depth, k, failures)
        return out, scores, reranker, failures

    def test_each_query_sends_its_own_first_n_in_its_own_order(self):
        head = [cand("a", q0=2, q1=5), cand("b", q0=1), cand("c", q1=1), cand("d", q0=3, q1=2), cand("e", q1=3)]
        out, scores, reranker, _ = self.run_lists(head, {"q0": ["d", "b", "a"], "q1": ["e", "c", "d"]})
        self.assertEqual(sorted(reranker.calls), [("q0", ["b", "a", "d"]), ("q1", ["c", "d", "e"])])
        # a is rank 5 for q1: past the depth, so q1's call does not hold it
        self.assertEqual(scores, {"0": {"d": 1.0, "b": 0.9, "a": 0.8}, "1": {"e": 1.0, "c": 0.9, "d": 0.8}})

    def test_the_lists_are_fused_by_reranked_rank_with_the_weights(self):
        head = [cand("a", q0=2, q1=5), cand("b", q0=1), cand("c", q1=1), cand("d", q0=3, q1=2), cand("e", q1=3)]
        out, _, _, _ = self.run_lists(head, {"q0": ["d", "b", "a"], "q1": ["e", "c", "d"]})
        value = {"d": 1 / 61 + 1 / 63, "e": 1 / 61, "b": 1 / 62, "c": 1 / 62, "a": 1 / 63}
        self.assertEqual([c["chunk_id"] for c in out], ["d", "e", "b", "c", "a"])     # b before c: equal, by id
        for c in out:
            self.assertAlmostEqual(c["rerank_score"], value[c["chunk_id"]], places=12)
            self.assertEqual(c["per_query_ranks"], next(h for h in head if h["chunk_id"] == c["chunk_id"])
                             ["per_query_ranks"])
        # a heavier query counts more: q1 at weight 2 puts its second (c) above q0's first (d is first for both)
        heavy = [SubQuery(text="q0"), SubQuery(text="q1", weight=2.0)]
        out, _, _, _ = self.run_lists(head, {"q0": ["d", "b", "a"], "q1": ["e", "c", "d"]}, queries=heavy)
        self.assertEqual([c["chunk_id"] for c in out], ["d", "e", "c", "b", "a"])

    def test_equal_values_go_by_the_best_reranked_rank_then_the_chunk_id(self):
        # two lists of one candidate each: both have rank 1 and value 1/61
        out, _, _, _ = self.run_lists([cand("g", q1=1), cand("f", q0=1)], {"q0": ["f"], "q1": ["g"]})
        self.assertEqual([c["chunk_id"] for c in out], ["f", "g"])

    def test_no_score_of_one_call_is_compared_with_another_s(self):
        # q1's scores are all higher than q0's: by score q1's three would lead; by rank the lists interleave
        class Scored(Scripted):
            def rerank(self, query, items):
                out = super().rerank(query, items)
                for item in out:
                    if "rerank_score" in item:
                        item["rerank_score"] += 100.0 if query == "q1" else 0.0
                return out

        head = [cand("a", q0=1), cand("b", q0=2), cand("c", q0=3), cand("d", q1=1), cand("e", q1=2), cand("f", q1=3)]
        reranker = Scored({"q0": ["a", "b", "c"], "q1": ["d", "e", "f"]})
        out, scores = rerank_lists(reranker, [SubQuery(text="q0"), SubQuery(text="q1")], head, ROWS, 3, 60)
        self.assertEqual([c["chunk_id"] for c in out], ["a", "d", "b", "e", "c", "f"])
        self.assertGreater(min(scores["1"].values()), max(scores["0"].values()))

    def test_a_candidate_the_service_did_not_score_follows_in_fused_order(self):
        head = [cand("a", q0=1), cand("b", q0=2), cand("c", q0=3)]
        out, scores, _, _ = self.run_lists(head, {"q0": ["c"]}, queries=[SubQuery(text="q0")])
        self.assertEqual([(c["chunk_id"], "rerank_score" in c) for c in out], [("c", True), ("a", False), ("b", False)])
        self.assertEqual(scores, {"0": {"c": 1.0}})

    def test_a_failed_call_raises_or_with_failures_costs_its_own_list(self):
        head = [cand("a", q0=1), cand("b", q1=1), cand("c", q0=2, q1=2)]
        with self.assertRaises(TimeoutError):
            rerank_lists(Scripted({"q0": ["a", "c"], "q1": ["b", "c"]}, fail={"q1"}),
                         [SubQuery(text="q0"), SubQuery(text="q1")], head, ROWS, 3, 60)
        out, scores, _, failures = self.run_lists(head, {"q0": ["c", "a"], "q1": ["b", "c"]}, fail={"q1"})
        self.assertEqual((list(failures), scores), (["1"], {"0": {"c": 1.0, "a": 0.9}}))
        self.assertEqual([(c["chunk_id"], "rerank_score" in c) for c in out], [("c", True), ("a", True), ("b", False)])

    def test_the_depth_is_the_same_for_every_query_and_a_short_list_sends_what_it_has(self):
        head = [cand("a", q0=1, q1=4), cand("b", q0=2), cand("c", q0=3), cand("d", q0=4), cand("e", q1=1)]
        _, _, reranker, _ = self.run_lists(head, {"q0": ["a", "b"], "q1": ["e"]}, depth=2)
        self.assertEqual(sorted(reranker.calls), [("q0", ["a", "b"]), ("q1", ["e"])])


class ServedTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient()
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def many(self, reranker, settings=None, **body):
        with mock.patch.object(self.orch, "reranker", reranker), mock.patch.dict(self.orch.config, settings or {}):
            r = self.c.post("/v2/search_many", json={"queries": QUERIES, "limit": 8, "rerank": "on", **body},
                            headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_the_setting(self):
        self.assertEqual(RERANK_MODES, ("per_query", "best_query", "per_query_lists"))
        self.assertEqual(search_many_settings(LISTS).rerank, "per_query_lists")
        with self.assertRaisesRegex(ValueError, "search_many_rerank"):
            search_many_settings({"search_many_rerank": "lists"})

    def test_each_query_s_call_holds_its_own_first_n(self):
        first_stage = self.many(WordReranker(), rerank="off", limit=30)["hits"]
        ranks = {h["id"]: h["per_query_ranks"] for h in first_stage}
        reranker = WordReranker()
        reranker.search_many_candidates = 2
        body = self.many(reranker, LISTS)
        self.assertEqual(len(reranker.calls), 3)
        for text, ids in reranker.calls:
            i = str(next(n for n, q in enumerate(QUERIES) if q["text"] == text))
            self.assertEqual([ranks[cid][i] for cid in ids], [1, 2])            # its own ranks 1 and 2, in order
        self.assertEqual(body["degraded_detail"]["rerank"], "ok")
        self.assertEqual([q["reranked"] for q in body["degraded_detail"]["queries"]], [True, True, True])
        sent = {cid for _, ids in reranker.calls for cid in ids}
        self.assertEqual([h["reranked"] for h in body["hits"]], [h["id"] in sent for h in body["hits"]])

    def test_the_order_is_the_rank_fusion_and_every_query_keeps_its_first(self):
        reranker = WordReranker()
        body = self.many(reranker, LISTS)
        # each call's order by its own scores (WordReranker sorts by score, ties in the order sent)
        value, best = {}, {}
        for text, ids in reranker.calls:
            ordered = sorted(ids, key=lambda cid: -reranker.score(text, cid))
            for r, cid in enumerate(ordered, 1):
                value[cid] = value.get(cid, 0.0) + 1.0 / (60 + r)
                best[cid] = min(best.get(cid, r), r)
        expected = sorted(value, key=lambda cid: (-value[cid], best[cid], cid))
        scored = [h for h in body["hits"] if h["reranked"]]
        self.assertEqual(body["hits"][0]["id"], expected[0])            # the first place is the fusion's first
        for h in scored:                                                # and every score is the fused value
            self.assertEqual(h["score"], round(value[h["id"]], 6))
        # (the order below the first place is the fusion's unless keep_each_query moved a query's best up:
        # RerankListsTests checks the fusion's order on its own)
        # the candidate each query's own call put first is among the hits (keep_each_query)
        ids = [h["id"] for h in body["hits"]]
        for text, sent in reranker.calls:
            self.assertIn(max(sent, key=lambda cid: (reranker.score(text, cid), -sent.index(cid))), ids)
        # nothing is a hit that the first stage did not find
        found = {h["id"] for h in self.many(WordReranker(), rerank="off", limit=30)["hits"]}
        self.assertLessEqual(set(ids), found)

    def test_a_failed_call_is_handled_as_in_the_other_modes(self):
        class Failing(WordReranker):
            breaker = None

            def rerank(self, query, items):
                if query == QUERIES[1]["text"]:
                    raise TimeoutError("rerank timed out")
                return super().rerank(query, items)

        plain = self.many(WordReranker(), rerank="off")
        failed = self.many(Failing(), LISTS)
        self.assertEqual((failed["degraded"], failed["degraded_detail"]["rerank"]), (["reranker"], "skipped"))
        self.assertEqual([h["id"] for h in failed["hits"]], [h["id"] for h in plain["hits"]])
        reranker = Failing()
        reranker.breaker = CircuitBreaker(failures=1, open_seconds=30)
        partial = self.many(reranker, {**LISTS, "search_many": {"rerank_isolation": True}})
        self.assertEqual((partial["degraded"], partial["degraded_detail"]["rerank"], reranker.breaker.state),
                         (["reranker"], "partial", "closed"))
        self.assertEqual([q["reranked"] for q in partial["degraded_detail"]["queries"]], [True, False, True])

    def test_deep_and_the_status(self):
        deep = self.many(WordReranker(), LISTS, deep=True)
        today = self.many(WordReranker(), LISTS)
        self.assertEqual([h["id"] for h in deep["hits"][:8]], [h["id"] for h in today["hits"]])
        with mock.patch.dict(self.orch.config, LISTS):
            self.c.app.state.status._probed = None
            status = self.c.get("/v2/status", headers=AUTH).json()
        self.assertEqual(status["settings"]["search_many"], {"parallel": 0, "rerank": "per_query_lists"})


if __name__ == "__main__":
    unittest.main()
