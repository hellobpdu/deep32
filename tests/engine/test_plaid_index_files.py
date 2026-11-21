import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import tempfile
import unittest
from pathlib import Path

from rag_core.late_interaction import missing_index_files

ALWAYS = [
    "documents_ids_to_plaid_ids.pkl",
    "plaid_ids_to_documents_ids.pkl",
    "fast_plaid_index/metadata.json",
    "fast_plaid_index/centroids.npy",
    "fast_plaid_index/ivf.npy",
    "fast_plaid_index/ivf_lengths.npy",
]


class PlaidIndexFilesTests(unittest.TestCase):

    def make(self, files):
        d = Path(tempfile.mkdtemp())
        for f in files:
            (d / f).parent.mkdir(parents=True, exist_ok=True)
            (d / f).write_bytes(b"x")
        return d

    def test_large_index_without_embeddings_cache_is_complete(self):
        # fast-plaid omits embeddings.npy above 1,000 documents; that must not fail validation
        self.assertEqual(missing_index_files(self.make(ALWAYS)), [])

    def test_small_index_with_embeddings_cache_is_complete(self):
        self.assertEqual(missing_index_files(self.make(ALWAYS + ["fast_plaid_index/embeddings.npy"])), [])

    def test_missing_core_files_are_reported(self):
        missing = missing_index_files(self.make(ALWAYS[:3]))
        self.assertEqual(len(missing), 3)
        self.assertTrue(any(m.endswith("centroids.npy") for m in missing))


if __name__ == "__main__":
    unittest.main()
