import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import unittest

from rag_core.fusion import (
    rrf_fuse,
)

from rag_core.query_plan import (
    build_query_plan,
)

from rag_core.retrieval_types import (
    SearchHit,
)


class RetrievalCoreTests(
    unittest.TestCase
):

    def test_forensic_plan(self):
        variants = (
            build_query_plan(
                (
                    "Why does OSPF "
                    "EXSTART fail on "
                    "IOS XE 17.12?"
                ),
                "forensic",
            )
        )

        purposes = {
            v.purpose
            for v in variants
        }

        self.assertIn(
            "primary",
            purposes,
        )

        self.assertIn(
            "exception",
            purposes,
        )

        self.assertIn(
            "contradiction",
            purposes,
        )

        self.assertIn(
            "version",
            purposes,
        )

    def test_rrf_consensus(self):
        hits = [
            SearchHit(
                retriever="bm25",
                variant_id="q1",
                purpose="primary",
                chunk_id="gold",
                doc_id="doc",
                rank=2,
                score=1.0,
                text="gold",
            ),
            SearchHit(
                retriever="dense",
                variant_id="q1",
                purpose="primary",
                chunk_id="gold",
                doc_id="doc",
                rank=1,
                score=0.9,
                text="gold",
            ),
            SearchHit(
                retriever="bm25",
                variant_id="q1",
                purpose="primary",
                chunk_id="other",
                doc_id="doc",
                rank=1,
                score=1.1,
                text="other",
            ),
        ]

        fused = rrf_fuse(
            hits,
            retriever_weights={
                "bm25": 1.0,
                "dense": 1.0,
            },
            variant_weights={
                "q1": 1.0,
            },
            active_retrievers=[
                "bm25",
                "dense",
            ],
            k=60,
            limit=10,
        )

        self.assertEqual(
            fused[0]["chunk_id"],
            "gold",
        )

        self.assertEqual(
            fused[0]["agreement"][
                "retriever_count"
            ],
            2,
        )

        self.assertEqual(
            fused[0]["agreement"][
                "consensus_signal"
            ],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
