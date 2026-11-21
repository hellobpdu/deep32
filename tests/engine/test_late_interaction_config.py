import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_core.late_interaction import CONFIG_PATH, load_late_interaction_config


class LateInteractionConfigTests(unittest.TestCase):

    def base(self):
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def load(self, data):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "li.json"
            p.write_text(json.dumps(data))
            with mock.patch.dict(os.environ, {"RAG_LATE_INTERACTION_CONFIG": str(p)}):
                return load_late_interaction_config()

    def test_query_threads_default_is_none(self):
        data = self.base()
        data.pop("query_num_threads", None)
        self.assertIsNone(self.load(data).query_num_threads)

    def test_query_threads_read_and_validated(self):
        data = self.base()
        data["query_num_threads"] = 2
        self.assertEqual(self.load(data).query_num_threads, 2)
        data["query_num_threads"] = 0
        with self.assertRaises(ValueError):
            self.load(data)

    def test_env_override_selects_file(self):
        data = self.base()
        data["device"] = "cuda"
        self.assertEqual(self.load(data).device, "cuda")


if __name__ == "__main__":
    unittest.main()
