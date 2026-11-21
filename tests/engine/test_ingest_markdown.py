import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

from rag_core.ingest import parse_markdown_sections


class MarkdownSectionTests(unittest.TestCase):

    def headings(self, markdown):
        return [
            s["heading_path"]
            for s in parse_markdown_sections(markdown, "doc_test")
            if s["heading"] is not None
        ]

    def test_hash_lines_inside_fences_are_not_headings(self):
        md = "\n".join([
            "# Configuring AAA",
            "Intro text.",
            "```",
            "# This allows the remote host to be authenticated",
            "#-----------------------------Remote Host (BRI)---",
            "Device# show running-config",
            "```",
            "## Restrictions for AAA",
            "~~~",
            "# still code",
            "~~~",
            "Body.",
        ])
        self.assertEqual(
            self.headings(md),
            [["Configuring AAA"], ["Configuring AAA", "Restrictions for AAA"]],
        )
        sections = parse_markdown_sections(md, "doc_test")
        first = next(s for s in sections if s["heading"] == "Configuring AAA")
        self.assertIn("# This allows the remote host", first["text"])

    def test_fence_closes_only_on_matching_marker(self):
        md = "\n".join(["# A", "````", "```", "# not a heading", "````", "## B"])
        self.assertEqual(self.headings(md), [["A"], ["A", "B"]])

    def test_single_line_backtick_code_is_not_a_fence(self):
        # audit probe on an earlier version: this line opened a fence that never closed, swallowing B and C
        md = "\n".join(["# A", "```show ip route```", "# B", "text", "## C", "more"])
        self.assertEqual(self.headings(md), [["A"], ["B"], ["B", "C"]])

    def test_a_fence_line_with_an_info_string_does_not_close(self):
        md = "\n".join(["# A", "```", "code", "```bash", "# still code", "```", "## B"])
        self.assertEqual(self.headings(md), [["A"], ["A", "B"]])

    def test_tilde_fence_info_may_contain_backticks(self):
        md = "\n".join(["# A", "~~~ `cli`", "# still code", "~~~", "## B"])
        self.assertEqual(self.headings(md), [["A"], ["A", "B"]])

    def test_escaped_hash_is_text(self):
        md = "\n".join(["# OSPF", "\\# links The number of router links."])
        self.assertEqual(self.headings(md), [["OSPF"]])


if __name__ == "__main__":
    unittest.main()
