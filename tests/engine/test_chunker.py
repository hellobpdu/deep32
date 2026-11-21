import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

from rag_core.chunker import (
    build_drafts,
    extract_units,
    looks_like_cli_or_config,
    make_provenance,
    clean_retrieval_text,
)


class ChunkerTests(unittest.TestCase):

    def setUp(self):
        self.structured = {
            "pages": [
                {
                    "page_idx": 0,
                    "blocks": [
                        {
                            "type":
                                "header",
                            "bbox":
                                [0.1, 0.05, 0.9, 0.08],
                            "content":
                                "Cisco Systems",
                        },
                        {
                            "type":
                                "doc_title",
                            "level": 1,
                            "bbox":
                                [0.1, 0.1, 0.9, 0.15],
                            "content":
                                "IOS XE Routing",
                        },
                        {
                            "type":
                                "paragraph_title",
                            "level": 2,
                            "bbox":
                                [0.1, 0.2, 0.9, 0.25],
                            "content":
                                "OSPF Adjacencies",
                        },
                        {
                            "type": "text",
                            "bbox":
                                [0.1, 0.3, 0.9, 0.4],
                            "content":
                                (
                                    "During database "
                                    "description exchange, "
                                    "neighbor parameters "
                                    "must be compatible."
                                ),
                        },
                        {
                            "type": "text",
                            "bbox":
                                [0.1, 0.45, 0.9, 0.6],
                            "content":
                                (
                                    "R1# show ip ospf "
                                    "neighbor\n"
                                    "R1# show ip ospf "
                                    "interface\n"
                                    "router ospf 100\n"
                                    " router-id 1.1.1.1"
                                ),
                        },
                        {
                            "type":
                                "page_footnote",
                            "bbox":
                                [0.1, 0.8, 0.9, 0.85],
                            "content":
                                (
                                    "Some behavior is "
                                    "platform dependent."
                                ),
                        },
                        {
                            "type":
                                "footer",
                            "bbox":
                                [0.1, 0.95, 0.9, 0.98],
                            "content":
                                "© Cisco",
                        },
                    ],
                }
            ]
        }

    def test_cli_detection(self):
        text = (
            "R1# show ip ospf neighbor\n"
            "router ospf 100\n"
            " router-id 1.1.1.1"
        )

        self.assertTrue(
            looks_like_cli_or_config(text)
        )

    def test_inline_copyright_boilerplate_removed(self):
        body = (
            "This is technically useful documentation text. "
            "q 2005 Elsevier B.V. All rights reserved."
        )

        cleaned = clean_retrieval_text(body)

        self.assertEqual(
            cleaned,
            "This is technically useful documentation text."
        )

    def test_cisco_copyright_boilerplate_removed(self):
        body = (
            "OSPF neighbors exchange database description packets. "
            "© 2026 Cisco Systems, Inc. All rights reserved."
        )

        cleaned = clean_retrieval_text(body)

        self.assertEqual(
            cleaned,
            "OSPF neighbors exchange database description packets."
        )

    def test_non_copyright_year_text_preserved(self):
        body = (
            "Beginning with Cisco IOS XE 17.12, "
            "the feature behaves differently."
        )

        self.assertEqual(
            clean_retrieval_text(body),
            body,
        )


    def test_noise_removed(self):
        units = extract_units(
            self.structured
        )

        text = "\n".join(
            u.text
            for u in units
        )

        self.assertNotIn(
            "Cisco Systems",
            text,
        )

        self.assertNotIn(
            "© Cisco",
            text,
        )

    def test_heading_path(self):
        units = extract_units(
            self.structured
        )

        self.assertEqual(
            units[0].heading_path,
            (
                "IOS XE Routing",
                "OSPF Adjacencies",
            ),
        )

    def test_heading_provenance(self):
        units = extract_units(
            self.structured
        )

        p = make_provenance(
            units
        )

        self.assertGreaterEqual(
            len(
                p[
                    "heading_block_ids"
                ]
            ),
            2,
        )

    def test_cli_atomic(self):
        units = extract_units(
            self.structured
        )

        cli = [
            u
            for u in units
            if u.kind
            == "cli-config"
        ]

        self.assertEqual(
            len(cli),
            1,
        )

        self.assertTrue(
            cli[0].atomic
        )

    def test_body_provenance(self):
        units = extract_units(
            self.structured
        )

        p = make_provenance(
            units
        )

        self.assertEqual(
            p["page_start"],
            1,
        )

        self.assertEqual(
            p["page_end"],
            1,
        )

        self.assertTrue(
            p["source_block_ids"]
        )

        self.assertTrue(
            p["bboxes"]
        )

    def test_chunk_build(self):
        units = extract_units(
            self.structured
        )

        drafts = build_drafts(
            units,
            target_tokens=100,
            hard_tokens=150,
            overlap_tokens=20,
        )

        self.assertGreaterEqual(
            len(drafts),
            2,
        )

        cli = [
            draft
            for draft in drafts
            if draft.atomic
            and "show ip ospf neighbor"
            in draft.text
        ]

        self.assertEqual(
            len(cli),
            1,
        )


if __name__ == "__main__":
    unittest.main()
