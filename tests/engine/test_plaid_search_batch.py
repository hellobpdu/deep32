import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pylate import indexes

from rag_core import late_interaction
from rag_core.late_interaction import CONFIG_PATH, load_late_interaction_config


def load(**changes):
    """The repo config with changes applied (None removes a key)."""
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "late_interaction.json"
        path.write_text(json.dumps(data))
        with mock.patch.dict(os.environ, {"RAG_LATE_INTERACTION_CONFIG": str(path)}):
            return load_late_interaction_config()


class PlaidSearchBatchTests(unittest.TestCase):
    """pylate's PLAID search batch_size defaults to 1 << 18: on the 30.7k-chunk staging corpus one
    search built a ~31 GiB tensor and the API was OOM-killed. It must be bounded and configurable."""

    def test_repo_config_and_code_default_are_1024(self):
        self.assertEqual(load_late_interaction_config(CONFIG_PATH).search_batch_size, 1024)
        self.assertEqual(load(search_batch_size=None).search_batch_size, 1024)

    def test_configurable_and_validated(self):
        self.assertEqual(load(search_batch_size=2048).search_batch_size, 2048)
        for bad in (0, -1):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                load(search_batch_size=bad)

    @engine_target.NEEDS_PYLATE
    def test_index_is_opened_with_the_configured_batch(self):
        config = load(search_batch_size=512)
        with mock.patch.object(late_interaction.indexes, "PLAID") as plaid:
            late_interaction._make_index(index_folder=Path("/nonexistent"), config=config, override=False)
        self.assertEqual(plaid.call_args.kwargs["batch_size"], 512)

    @engine_target.NEEDS_PYLATE
    def test_pinned_pylate_takes_batch_size(self):
        # fails loudly if a pylate upgrade renames or drops the knob
        self.assertIn("batch_size", inspect.signature(indexes.PLAID).parameters)


if __name__ == "__main__":
    unittest.main()
