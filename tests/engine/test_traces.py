import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rag_core.traces import TraceWriter, sample_rate


class TraceWriterTests(unittest.TestCase):

    def test_written_async_and_compact(self):
        with tempfile.TemporaryDirectory() as d:
            w = TraceWriter(Path(d), mode="all")
            p = w.submit("qry_1", {"query": "ospf", "hits": [1, 2]})
            w.flush()
            self.assertEqual(p, str(Path(d) / "qry_1.json"))
            text = Path(p).read_text()
            self.assertEqual(json.loads(text)["query"], "ospf")
            self.assertNotIn("\n", text)

    def test_off_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            w = TraceWriter(Path(d) / "t", mode="off")
            self.assertIsNone(w.submit("qry_1", {}))
            self.assertFalse((Path(d) / "t").exists())

    def test_prune_by_count_and_age(self):
        with tempfile.TemporaryDirectory() as d:
            w = TraceWriter(Path(d), mode="all", max_files=3, max_age_days=1, prune_every=10**9)
            now = time.time()
            for i in range(6):
                f = Path(d) / f"qry_{i}.json"
                f.write_text("{}")
                os.utime(f, (now - 100 + i, now - 100 + i))
            old = Path(d) / "qry_old.json"
            old.write_text("{}")
            os.utime(old, (now - 3 * 86400, now - 3 * 86400))
            removed = w.prune()
            left = sorted(p.name for p in Path(d).glob("*.json"))
            self.assertEqual(left, ["qry_3.json", "qry_4.json", "qry_5.json"])
            self.assertEqual(removed, 4)

    def test_orphaned_tmp_files_are_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            w = TraceWriter(Path(d), mode="all", prune_every=10**9)
            now = time.time()
            orphan, live = Path(d) / "qry_dead.tmp", Path(d) / "qry_live.tmp"
            for f in (orphan, live):
                f.write_text("{")
            os.utime(orphan, (now - 2 * 3600, now - 2 * 3600))
            self.assertEqual(w.prune(), 1)
            self.assertFalse(orphan.exists())
            self.assertTrue(live.exists())          # may be a write in progress

    def test_prune_survives_files_vanishing_mid_scan(self):
        # another process tracing to the same directory may rename or delete files between glob and stat
        with tempfile.TemporaryDirectory() as d:
            w = TraceWriter(Path(d), mode="all", max_age_days=1, prune_every=10**9)
            old = Path(d) / "qry_old.json"
            old.write_text("{}")
            os.utime(old, (time.time() - 3 * 86400,) * 2)
            listing = {"*.json": [Path(d) / "qry_gone.json", old], "*.tmp": [Path(d) / "qry_gone.tmp"]}
            w.directory = mock.Mock(glob=lambda pattern: iter(listing[pattern]))
            self.assertEqual(w.prune(), 1)
            self.assertFalse(old.exists())

    def test_rag_trace_values(self):
        for value, rate in [("all", 1.0), ("ON", 1.0), ("true", 1.0), ("off", 0.0), ("false", 0.0),
                            ("none", 0.0), ("0.25", 0.25), ("7", 1.0), ("-1", 0.0)]:
            with self.subTest(value=value):
                self.assertEqual(sample_rate(value), rate)

    def test_unknown_rag_trace_warns_instead_of_failing_startup(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RAG_TRACE": "yes please"}):
            with self.assertLogs("rag_core.traces", level="WARNING"):
                w = TraceWriter(Path(d))
            self.assertEqual(w.sample, 1.0)

    def test_full_queue_drops_instead_of_blocking(self):
        with tempfile.TemporaryDirectory() as d:
            w = TraceWriter(Path(d), mode="all", queue_size=1)
            big = {"x": "y" * 2_000_000}
            results = [w.submit(f"qry_{i}", big) for i in range(50)]
            w.flush(10)
            self.assertGreater(w.dropped, 0)
            self.assertEqual(results.count(None), w.dropped)


if __name__ == "__main__":
    unittest.main()
