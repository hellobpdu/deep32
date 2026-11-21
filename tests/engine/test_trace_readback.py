import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rag_core import orchestrator, traces
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.retrieval_types import SearchHit

TESTS = Path(__file__).resolve().parent
SRC = engine_target.REPO / "src"

CONFIG = {
    "rrf_k": 60,
    "candidate_limit_per_query": 5,
    "fused_limit": 5,
    "retriever_weights": {"bm25": 1.0},
}


class OneHit:
    name = "bm25"

    def __init__(self, paths):
        pass

    def search(self, variant, *, limit=50):
        return [SearchHit(retriever="bm25", variant_id=variant.variant_id, purpose=variant.purpose,
                          chunk_id="c1", doc_id="d1", rank=1, score=1.0, text="nssa translator")]


def slow_dumps(*args, **kwargs):
    time.sleep(0.2)
    return json.dumps(*args, **kwargs)


def slow_writer():
    """Delay trace writes in rag_core.traces only, so the race is deterministic."""
    return mock.patch.object(traces, "json", SimpleNamespace(dumps=slow_dumps))


class TraceReadbackTests(unittest.TestCase):
    """An earlier engine returned trace_path before the background writer had written it."""

    def search(self, d):
        cfg = Path(d) / "retrieval.json"
        cfg.write_text(json.dumps(CONFIG))
        with mock.patch.dict(os.environ, {"RAG_RETRIEVAL_CONFIG": str(cfg), "RAG_TRACE": "all"}), \
                mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, {"bm25": OneHit}, clear=True):
            orch = RetrievalOrchestrator(paths=mock.Mock(logs=Path(d) / "logs"))
        return orch, orch.search("nssa translator election", limit=5)

    def test_trace_is_readable_after_flush(self):
        with tempfile.TemporaryDirectory() as d, slow_writer():
            orch, result = self.search(d)
            path = Path(result["trace_path"])
            self.assertFalse(path.exists())             # the race the accept scripts hit
            orch.traces.flush()
            trace = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(trace["run_id"], result["run_id"])
            self.assertEqual({h["retriever"] for h in trace["raw_hits"]}, {"bm25"})

    def test_flush_all_covers_every_writer(self):
        with tempfile.TemporaryDirectory() as d, slow_writer():
            _, first = self.search(d)
            (Path(d) / "second").mkdir()
            _, second = self.search(Path(d) / "second")
            traces.flush_all()
            self.assertTrue(Path(first["trace_path"]).exists())
            self.assertTrue(Path(second["trace_path"]).exists())

    def test_queued_trace_survives_interpreter_exit(self):
        code = (
            "import json, sys, time, types\n"
            "from pathlib import Path\n"
            "from rag_core import traces\n"
            "traces.json = types.SimpleNamespace(dumps=lambda *a, **k: (time.sleep(0.3), json.dumps(*a, **k))[1])\n"
            "w = traces.TraceWriter(Path(sys.argv[1]), mode='all')\n"
            "print(w.submit('qry_exit', {'query': 'ospf'}))\n"
        )
        with tempfile.TemporaryDirectory() as d:
            out = subprocess.run([sys.executable, "-c", code, d], capture_output=True, text=True, timeout=60,
                                 env={**os.environ, "PYTHONPATH": str(SRC)}, check=True)
            path = Path(out.stdout.strip())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"query": "ospf"})

    def test_accept_scripts_flush_before_reading_trace_path(self):
        for script in sorted(TESTS.glob("accept_*.py")):
            text = script.read_text(encoding="utf-8")
            reads = [m.start() for m in re.finditer(r"""\[\s*["']trace_path["']\s*\]""", text)]
            if not reads:
                continue
            flushed = re.search(r"\.flush\(|flush_all\(", text)
            flush = flushed.start() if flushed else -1
            with self.subTest(script=script.name):
                self.assertNotEqual(flush, -1, "reads trace_path without flushing traces")
                self.assertLess(flush, reads[0], "reads trace_path before flushing traces")


if __name__ == "__main__":
    unittest.main()
