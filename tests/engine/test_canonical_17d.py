import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

from rag_core.canonical import (
    assess_canonical_readiness,
    derive_source_page_map,
    detect_bbox_coordinate_space,
    discover_page_semantics,
)


class Canonical17DTests(
    unittest.TestCase
):

    def test_partial_parse_truth(self):
        structured = {
            "metadata": {
                "document": {
                    "page_count": 13
                }
            },

            "is_full_document":
                False,

            "pages": [
                {
                    "page_idx": 0,
                    "blocks": [],
                }
            ],
        }

        model_output = {
            "metadata": {
                "document": {
                    "page_count": 13
                }
            },

            "pages": [
                [
                    {
                        "page_index_map":
                            [0]
                    }
                ]
            ],
        }

        result = (
            discover_page_semantics(
                structured,
                {},
                model_output,
            )
        )

        self.assertEqual(
            result[
                "source_page_count"
            ],
            13,
        )

        self.assertEqual(
            result[
                "parsed_page_count"
            ],
            1,
        )

        self.assertEqual(
            result[
                "parsed_pages"
            ],
            [1],
        )

        self.assertFalse(
            result[
                "is_full_document"
            ]
        )

    def test_subset_source_page_mapping(
        self,
    ):
        structured = {
            "pages": [
                {
                    "page_idx": 0,
                    "blocks": [],
                }
            ]
        }

        model_output = {
            "pages": [
                [
                    {
                        "page_index_map":
                            [6]
                    }
                ]
            ]
        }

        mapping = (
            derive_source_page_map(
                structured,
                model_output,
            )
        )

        self.assertEqual(
            mapping,
            {0: 7},
        )

    def test_partial_not_ready(self):
        eligible, reasons = (
            assess_canonical_readiness(
                source_page_count=13,
                parsed_pages=[1],
                is_full_document=False,
            )
        )

        self.assertFalse(
            eligible
        )

        self.assertIn(
            "partial_document",
            reasons,
        )

        self.assertIn(
            "parsed_page_count_mismatch",
            reasons,
        )

    def test_complete_document_ready(
        self,
    ):
        eligible, reasons = (
            assess_canonical_readiness(
                source_page_count=3,
                parsed_pages=[
                    1,
                    2,
                    3,
                ],
                is_full_document=True,
            )
        )

        self.assertTrue(
            eligible
        )

        self.assertEqual(
            reasons,
            [],
        )

    def test_normalized_bbox_detection(
        self,
    ):
        structured = {
            "pages": [
                {
                    "blocks": [
                        {
                            "bbox": [
                                0.1,
                                0.2,
                                0.8,
                                0.9,
                            ]
                        }
                    ]
                }
            ]
        }

        self.assertEqual(
            detect_bbox_coordinate_space(
                structured
            ),
            "normalized_0_1",
        )


if __name__ == "__main__":
    unittest.main()
