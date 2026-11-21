import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

from rag_core.retrievers.colbert import INSTRUCT_PREFIX, build_query_text


class QueryTemplateTests(unittest.TestCase):

    def test_agent_default_keeps_original_bytes(self):
        q = build_query_text("  ospf exstart  ")
        self.assertEqual(q, f"{INSTRUCT_PREFIX}Reasoning: Empty\n\nQuery: ospf exstart")
        self.assertIn("reasoning\\nQuery:Reasoning", q)          # the literal backslash-n it always had

    def test_agent_fixed_uses_real_newline_and_one_query_line(self):
        q = build_query_text("ospf exstart", "agent-fixed")
        self.assertNotIn("\\n", q)
        self.assertEqual(q.count("Query:"), 1)
        self.assertTrue(q.endswith("\nReasoning: Empty\n\nQuery: ospf exstart"))

    def test_plain_and_empty(self):
        self.assertEqual(build_query_text(" ospf exstart ", "plain"), "ospf exstart")
        self.assertEqual(build_query_text("   ", "plain"), "")
        self.assertEqual(build_query_text("", "agent"), "")


if __name__ == "__main__":
    unittest.main()
