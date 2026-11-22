"""/v2/lookup?kind= (WP-E E4, the contract of WP-C C6): with kind (feature, history, default, deprecation) the route
answers the rows of that fact table (kb.lookup_fact), each with its document's stored release as doc_release; a
knowledge base without the table answers degraded ["kb"]. Without kind the route is the one before. The route is in
the serving engine only (the builder's validation does not call /v2/lookup)."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import unittest

from rag_core import kb

from test_api_v2 import AUTH, RootClient

ENGINE_ONLY = unittest.skipUnless(engine_target.NAME == "engine", "the route is in src/engine only")


@ENGINE_ONLY
class LookupFactRouteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient()
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def get(self, **params):
        r = self.c.get("/v2/lookup", params=params, headers=AUTH)
        return r

    def test_feature(self):
        r = self.get(id="OSPF NSSA", kind="feature")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(list(body), ["kind", "results", "truncated", "degraded"])
        self.assertEqual((body["kind"], body["truncated"], body["degraded"]), ("feature", False, []))
        self.assertEqual(len(body["results"]), 1)
        row = body["results"][0]
        doc = json.loads(json.dumps(self.c.get("/v2/documents", headers=AUTH).json()))
        release = {d["doc_key"]: d["release"] for d in doc["documents"]}["c9300-179-rtng"]
        self.assertEqual(row, {"kind": "feature", "doc_key": "c9300-179-rtng",
                               "section_id": "c9300-179-rtng#restrictions-for-ospf-nssa", "line": row["line"],
                               "doc_release": release, "feature": "OSPF NSSA", "release": "16.3.1",
                               "info": "introduced"})
        self.assertIsNotNone(release)

    def test_a_kind_without_its_table(self):
        for kind in ("history", "default", "deprecation"):
            with self.subTest(kind=kind):
                r = self.get(id="timers bgp", kind=kind)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json(), {"kind": kind, "results": [], "degraded": ["kb"]})

    def test_without_kind_the_route_is_the_one_before(self):
        db = kb.open_kb(self.root.paths.root / "kb" / "kb.sqlite")
        try:
            for identifier in ("RFC 3101", "NSSA", "show ip ospf database nssa-external", "no such thing"):
                with self.subTest(identifier=identifier):
                    want = kb.lookup(db, identifier)
                    for result in want["results"]:
                        if "rfc" in result and result["kind"] in ("rfc", "bcp", "std"):
                            result["doc_key"] = f"rfc{result['rfc']}" if result["rfc"] == 3101 else None
                    r = self.get(id=identifier)
                    self.assertEqual(r.status_code, 200, r.text)
                    self.assertEqual(r.content, self.get(id=identifier).content)
                    self.assertEqual(r.json(), {**want, "degraded": []})
        finally:
            db.close()

    def test_a_bad_kind_is_refused(self):
        self.assertEqual(self.get(id="x", kind="bogus").status_code, 422)


if __name__ == "__main__":
    unittest.main()
