"""Build deep32-engine's own tiny test root (tests/v2_root.py, the fixture tests/test_api_v2.py serves) for the smoke run.

Usage: python -I -B make_test_root.py TESTS_DIR SRC_DIR OUT_DIR
  TESTS_DIR: the release archive's tests/ (v2_root.py); SRC_DIR: its src/ (rag_core); OUT_DIR: new, empty.
Writes OUT_DIR/root (rag.db + sources + structure + kb, exactly as RootClient() does) and OUT_DIR/retrieval.json:
the tests' BM25 config plus the dense retriever and an enabled reranker, both pointed at nothing (127.0.0.1:9), so
the service starts its full serve path and reports them degraded.
"""
import json
import sys
from pathlib import Path

tests, src, out = (Path(a) for a in sys.argv[1:4])
sys.path[:0] = [str(src), str(tests)]
from rag_core.kb import build_kb  # noqa: E402
from rag_core.structure import index_root  # noqa: E402
from v2_root import build_root  # noqa: E402

out.mkdir(parents=True, exist_ok=False)
paths = build_root(out)
index_root(paths)
build_kb(out / "kb-src", paths.root / "kb" / "kb.sqlite")
config = {
    "rrf_k": 60, "candidate_limit_per_query": 50, "fused_limit": 30,
    "retriever_weights": {"bm25": 1.0, "dense": 1.0},
    "retrievers": ["bm25", "dense"],
    "technical_variant_retrievers": ["bm25"],
    "reranker": {"enabled": True, "url": "http://127.0.0.1:9/rerank"},
}
(out / "retrieval.json").write_text(json.dumps(config, indent=1))
print("root", paths.root)
