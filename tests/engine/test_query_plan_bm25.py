import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

from rag_core.query_plan import build_query_plan
from rag_core.retrievers.lexical import fts_query


class FtsQueryTests(unittest.TestCase):

    def test_default_keeps_every_token(self):
        self.assertEqual(fts_query("what is the hold time"), '"what" OR "is" OR "the" OR "hold" OR "time"')

    def test_stopwords_dropped_but_acronyms_and_identifiers_kept(self):
        q = fts_query("What is the AS number of IS-IS in the OR case with 65535:666 and ip.ospf", drop_stopwords=True)
        for kept in ('"What"', '"AS"', '"number"', '"IS-IS"', '"OR"', '"case"', '"65535:666"', '"ip.ospf"'):
            self.assertIn(kept, q)
        for dropped in ('"is"', '"the"', '"of"', '"in"', '"with"', '"and"'):
            self.assertNotIn(dropped, q)

    def test_all_stopwords_falls_back_to_full_query(self):
        self.assertEqual(fts_query("what is it", drop_stopwords=True), '"what" OR "is" OR "it"')


class TechnicalVariantTests(unittest.TestCase):

    def test_default_runs_on_all_retrievers(self):
        v = {x.purpose: x for x in build_query_plan("Why does OSPF stay in EXSTART with MTU 1500?", "normal")}
        self.assertIn("technical", v)
        self.assertIsNone(v["technical"].retrievers)
        self.assertIsNone(v["primary"].retrievers)

    def test_restricted_to_bm25(self):
        v = {x.purpose: x for x in build_query_plan("Why does OSPF stay in EXSTART with MTU 1500?", "normal",
                                                    technical_retrievers=["bm25"])}
        self.assertEqual(v["technical"].retrievers, ("bm25",))
        self.assertIsNone(v["primary"].retrievers)


if __name__ == "__main__":
    unittest.main()
