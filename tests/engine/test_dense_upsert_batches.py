import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import os
import tempfile
import unittest
from unittest import mock

from rag_core import dense


class FakeQdrant:
    def __init__(self):
        self.upserts = []

    def upsert(self, collection_name, points, wait):
        self.upserts.append(len(points))

    def count(self, collection_name, exact):
        return mock.Mock(count=sum(self.upserts))


class FakeEmbedder:
    def __init__(self, config, **kw):
        pass

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts], "stub-model"


class UpsertBatchingTests(unittest.TestCase):

    def test_large_document_is_upserted_in_bounded_batches(self):
        n = 600
        chunks = [{"chunk_id": f"c{i}", "doc_id": "doc_big", "embedding_text": f"text {i}"} for i in range(n)]
        q = FakeQdrant()
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.dict(os.environ, {"RAG_ROOT": root}), \
                mock.patch.object(dense, "load_chunks", return_value=chunks), \
                mock.patch.object(dense, "JinaEmbeddingClient", FakeEmbedder), \
                mock.patch.object(dense, "make_qdrant_client", return_value=q), \
                mock.patch.object(dense, "ensure_collection", return_value=False), \
                mock.patch.object(dense, "reset_dense_state"), \
                mock.patch.object(dense, "delete_doc_points"), \
                mock.patch.object(dense, "mark_dense_indexed"), \
                mock.patch.object(dense, "payload_for_chunk", return_value={}):
            result = dense.index_dense(doc_id="doc_big")
        self.assertEqual(sum(q.upserts), n)
        self.assertTrue(all(b <= dense.UPSERT_BATCH_POINTS for b in q.upserts))
        self.assertGreater(len(q.upserts), 1)
        self.assertEqual(result["chunks_indexed"], n)


if __name__ == "__main__":
    unittest.main()
