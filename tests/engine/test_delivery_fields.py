"""0.1.0-alpha.3, WP-D item D9: retrieval.json evidence.covered_titles and evidence.table_context
(rag_core.api_v2.EvidenceSettings, HitBuilder.describe). With them on a hit carries "covered_titles" and
"table_context", made by RootReader.covered_titles and RootReader.table_context (WP-E item E5). These tests stand
two recording functions in for them, so they hold before and after E5 and on both engine copies; what the real
functions answer is E5's own test (tests/engine/test_delivery_context.py).

Off (unset, false or null), every answer is the one before: the bodies pinned from 4f4fe78."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest
from unittest import mock

from rag_core.api_v2 import EvidenceSettings, evidence_settings
from rag_core.reader import RootReader

import fold_root
import test_fold_identical
from test_api_v2 import AUTH, RootClient

ON = {"evidence": {"covered_titles": True, "table_context": True}}


class OffIsTheBodyBefore(test_fold_identical.OffTests):
    retrieval = {"evidence": {"covered_titles": False, "table_context": False}}


class NullIsTheBodyBefore(test_fold_identical.OffTests):
    retrieval = {"evidence": {"covered_titles": None, "table_context": None, "tier1_chars": None}}


class OnWithoutTheReaderFunctionsIsTheBodyBefore(test_fold_identical.OffTests):
    """An engine copy whose RootReader has no covered_titles and no table_context (the Forge builder's; the serving
    engine before WP-E item E5) accepts the keys and delivers nothing new."""
    retrieval = ON

    @classmethod
    def setUpClass(cls):
        cls.patches = [mock.patch.object(RootReader, name, None, create=True)
                       for name in ("covered_titles", "table_context")]
        for p in cls.patches:
            p.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        for p in cls.patches:
            p.stop()


class SettingTests(unittest.TestCase):

    def test_values(self):
        for config in ({}, {"evidence": None}, {"evidence": {}},
                       {"evidence": {"covered_titles": False, "table_context": None}}):
            self.assertEqual(evidence_settings(config), EvidenceSettings(None, False, False))
        self.assertEqual(evidence_settings({"evidence": {"tier1_chars": 3200, "covered_titles": True}}),
                         EvidenceSettings(3200, True, False))
        self.assertEqual(evidence_settings(ON), EvidenceSettings(None, True, True))

    def test_other_values_are_refused(self):
        for bad in ({"covered_titles": 1}, {"table_context": "true"}, {"covered_titles": []}, {"titles": True},
                    {"tier1_chars": 100, "covered_titles": True}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "evidence"):
                evidence_settings({"evidence": bad})

    def test_the_start_refuses_a_bad_value(self):
        with self.assertRaisesRegex(ValueError, "table_context"):
            RootClient(retrieval={"evidence": {"table_context": "yes"}})


class Recorder:
    """Stand-ins for RootReader.covered_titles and RootReader.table_context that record what they are asked."""

    def __init__(self):
        self.covered_calls, self.table_calls = [], []

    def covered_titles(self, reader, covered):
        self.covered_calls.append(covered)
        return ["First section", "Second section"]

    def table_context(self, reader, doc, first):
        self.table_calls.append((doc.doc_key, first))
        return {"caption": None, "header": f"header above line {first}"} if first % 2 else None

    def installed(self):
        """Both functions on RootReader for the time of a with block."""
        recorder = self
        return mock.patch.multiple(
            RootReader, create=True,
            covered_titles=lambda reader, covered: recorder.covered_titles(reader, covered),
            table_context=lambda reader, doc, first: recorder.table_context(reader, doc, first))


class DeliveryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(prepare=fold_root.prepare, retrieval={"fold_identical": True})
        cls.c = cls.root.client
        cls.orch = cls.c.app.state.orchestrator

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def post(self, path, body, settings=None, recorder=None):
        recorder = recorder or Recorder()
        with recorder.installed(), mock.patch.dict(self.orch.config, settings or {}):
            r = self.c.post(path, json=body, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_on_every_hit_is_the_hit_before_with_the_two_fields_last(self):
        for path, body in (("/v2/search", {"query": "NSSA ABR elected translator Type-7 LSAs"}),
                           ("/v2/search", {"query": "show ip ospf neighbor", "deep": True}),
                           ("/v2/search_many", {"queries": [{"text": "NSSA translator", "mode": "bm25"},
                                                            {"text": "show ip ospf neighbor"}], "limit": 6}),
                           ("/v2/search", {"query": "NSSA translator", "prefer_release": ["26.1"]})):
            with self.subTest(path=path, body=body):
                off = self.post(path, body)
                recorder = Recorder()
                on = self.post(path, body, ON, recorder)
                self.assertEqual({k: v for k, v in on.items() if k not in ("hits", "timings_ms")},
                                 {k: v for k, v in off.items() if k not in ("hits", "timings_ms")})
                self.assertEqual(len(on["hits"]), len(off["hits"]))
                self.assertTrue(on["hits"])
                for a, b in zip(off["hits"], on["hits"]):
                    first = int(b["local"].split(":")[1].split("-")[0])
                    added = ["covered_titles"] + (["table_context"] if first % 2 else [])
                    self.assertEqual(list(b), list(a) + added)
                    self.assertEqual({k: v for k, v in b.items() if k not in added}, a)
                    self.assertEqual(b["covered_titles"], ["First section", "Second section"])
                    if first % 2:
                        self.assertEqual(b["table_context"], {"caption": None, "header": f"header above line {first}"})
                # asked once per hit, for the document and first line the hit shows
                self.assertEqual(recorder.table_calls,
                                 [(h["doc_key"], int(h["local"].split(":")[1].split("-")[0])) for h in on["hits"]])
                self.assertEqual(len(recorder.covered_calls), len(on["hits"]))

    def test_each_setting_alone_adds_its_own_field(self):
        body = {"query": "NSSA ABR elected translator Type-7 LSAs"}
        only_titles = self.post("/v2/search", body, {"evidence": {"covered_titles": True}})["hits"]
        only_tables = self.post("/v2/search", body, {"evidence": {"table_context": True}})["hits"]
        self.assertTrue(all("covered_titles" in h and "table_context" not in h for h in only_titles))
        self.assertTrue(all("covered_titles" not in h for h in only_tables))
        self.assertTrue(any("table_context" in h for h in only_tables))

    def test_a_function_that_answers_none_adds_nothing(self):
        with mock.patch.multiple(RootReader, create=True, covered_titles=lambda reader, covered: None,
                                 table_context=lambda reader, doc, first: None), \
                mock.patch.dict(self.orch.config, ON):
            hits = self.c.post("/v2/search", json={"query": "NSSA translator"}, headers=AUTH).json()["hits"]
        self.assertTrue(hits)
        self.assertFalse(any("covered_titles" in h or "table_context" in h for h in hits))

    def test_the_fields_describe_the_copy_and_the_lines_finally_shown(self):
        # prefer_release shows the first hit as its 26.1 copy: the table context is asked of that document
        body = {"query": "NSSA ABR elected translator Type-7 LSAs", "prefer_release": ["26.1"]}
        recorder = Recorder()
        on = self.post("/v2/search", body, ON, recorder)
        self.assertEqual(on["hits"][0]["doc_key"], "c9300-261-rtng")
        self.assertEqual(recorder.table_calls[0][0], "c9300-261-rtng")

    def test_the_status_shows_the_keys_that_are_set(self):
        with mock.patch.dict(self.orch.config, {"evidence": {"tier1_chars": 3200, "table_context": True}}):
            self.c.app.state.status._probed = None
            status = self.c.get("/v2/status", headers=AUTH).json()
        self.assertEqual(status["settings"]["evidence"], {"tier1_chars": 3200, "table_context": True})
        self.c.app.state.status._probed = None
        self.assertNotIn("evidence", self.c.get("/v2/status", headers=AUTH).json()["settings"])


if __name__ == "__main__":
    unittest.main()
