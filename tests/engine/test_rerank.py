import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from rag_core import orchestrator
from rag_core.fusion import rrf_fuse
from rag_core.orchestrator import RetrievalOrchestrator
from rag_core.paths import RAGPaths
from rag_core.rerank import Reranker
from rag_core.retrieval_types import SearchHit


class _Rerank(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "reverse"
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Rerank.seen.append(body)
        n = len(body["documents"])
        if _Rerank.mode == "error":
            out, code = b'{"detail":"boom"}', 500
        elif _Rerank.mode == "bad-index":
            out, code = json.dumps({"results": [{"index": n + 5, "relevance_score": 1.0}]}).encode(), 200
        else:  # reverse order, and leave out the last-ranked document to test the fallback
            res = [{"index": i, "relevance_score": float(i)} for i in reversed(range(1, n))]
            out, code = json.dumps({"results": res}).encode(), 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def items(n):
    return [{"chunk_id": f"c{i}", "text": f"text {i}", "heading_path": ["OSPF", f"s{i}"]} for i in range(n)]


class RerankerTests(unittest.TestCase):

    def setUp(self):
        _Rerank.mode, _Rerank.seen = "reverse", []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Rerank)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.r = Reranker({"url": f"http://127.0.0.1:{self.srv.server_address[1]}/v1/rerank",
                           "candidates": 30, "timeout_seconds": 5})

    def tearDown(self):
        self.r.close()
        self.srv.shutdown()
        self.srv.server_close()

    def test_reorders_and_keeps_unreturned_items(self):
        out = self.r.rerank("q", items(4))
        self.assertEqual([i["chunk_id"] for i in out], ["c3", "c2", "c1", "c0"])
        self.assertEqual(out[0]["fused_rank"], 4)
        self.assertNotIn("rerank_score", out[-1])
        self.assertTrue(_Rerank.seen[0]["documents"][0].startswith("OSPF > s0\n"))

    def test_invalid_index_raises(self):
        _Rerank.mode = "bad-index"
        with self.assertRaises(RuntimeError):
            self.r.rerank("q", items(3))

    def test_http_error_raises(self):
        _Rerank.mode = "error"
        with self.assertRaises(Exception):
            self.r.rerank("q", items(3))

    def test_candidate_cap(self):
        r = Reranker({"url": "http://127.0.0.1:9/x", "candidates": 500})
        self.assertEqual(r.candidates, 64)
        r.close()

    def test_defaults_match_the_measured_configuration(self):
        # the A/B arm v3-bd-tb-rr10 reranked the top 10 with a 15 s timeout; the code said 30 and 8 s
        r = Reranker({"url": "http://127.0.0.1:9/x"})
        self.addCleanup(r.close)
        self.assertEqual(r.candidates, 10)
        self.assertEqual(r.timeout_seconds, 15.0)
        self.assertEqual(r._client.timeout.read, 15.0)
        self.assertEqual(r._client.timeout.connect, 5.0)

    def test_the_service_reads_the_head_of_2400_characters_by_default(self):
        r = Reranker({"url": "http://127.0.0.1:9/x"})
        self.addCleanup(r.close)
        self.assertEqual((r.max_chars, r.window), (2400, "head"))
        item = {"text": "x" * 3000 + " NSSA", "heading_path": ["OSPF"]}
        self.assertEqual(r.document(item, "NSSA"), ("OSPF\n" + "x" * 3000)[:2400])

    def test_a_centred_window_reads_a_long_chunk_where_it_matches(self):
        _Rerank.seen = []
        r = Reranker({"url": f"http://127.0.0.1:{self.srv.server_address[1]}/v1/rerank", "max_chars": 300,
                      "window": "centred"})
        self.addCleanup(r.close)
        lines = [f"Paragraph {n}: filler about route maps and prefix lists." for n in range(40)]
        lines[33] = "The NSSA ABR with the highest router ID is the elected translator."
        item = {"chunk_id": "c", "text": "\n\n".join(lines), "heading_path": ["OSPF", "NSSA"]}
        document = r.document(item, "which NSSA ABR is the elected translator")
        self.assertTrue(document.startswith("OSPF > NSSA\n"))
        self.assertIn(lines[33], document)
        self.assertLessEqual(len(document), 300)
        self.assertNotIn(lines[0], document)
        # whole lines of the chunk: the window leaves the heading its share of max_chars, so its end is not cut
        chunk_lines = set(item["text"].split("\n"))
        self.assertLessEqual(set(document.split("\n")[1:]), chunk_lines)
        head = Reranker({"url": "http://127.0.0.1:9/x", "max_chars": 300})
        self.addCleanup(head.close)
        self.assertNotIn(lines[33], head.document(item, "which NSSA ABR is the elected translator"))
        # what the service is sent
        r.rerank("which NSSA ABR is the elected translator", [dict(item)])
        self.assertEqual(_Rerank.seen[-1]["documents"], [document])
        # a text that fits, or one where no query term occurs: the head, cut at a line
        short = {"text": "NSSA in one line", "heading_path": []}
        self.assertEqual(r.document(short, "NSSA"), "NSSA in one line")
        unmatched = r.document(item, "BGP confederation")
        self.assertTrue(unmatched.startswith("OSPF > NSSA\n" + lines[0]))
        self.assertLessEqual(len(unmatched), 300)
        self.assertLessEqual(set(unmatched.split("\n")[1:]), chunk_lines)
        self.assertEqual(r.document(item), head.document(item))                # no query: the head

    def test_window_and_max_chars_settings(self):
        r = Reranker({"url": "http://127.0.0.1:9/x", "max_chars": 1200, "window": "centred"})
        self.addCleanup(r.close)
        self.assertEqual((r.max_chars, r.window), (1200, "centred"))
        for bad in ({"window": "centered"}, {"window": "middle"}, {"window": None}, {"max_chars": "big"}):
            with self.assertRaises(ValueError, msg=bad):
                Reranker({"url": "http://127.0.0.1:9/x", **bad}).close()


class RootRerankerTests(unittest.TestCase):
    """A root's overlay (retrieval.json "reranker") sets the window and its size, as derive_root --set writes."""

    class BM25:
        name = "bm25"

        def __init__(self, paths):
            pass

        def search(self, variant, *, limit=50):
            return []

    def orch(self, reranker):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config").mkdir()
            (Path(d) / "config" / "retrieval.json").write_text(json.dumps({
                "retrievers": ["bm25"], "reranker": {"enabled": True, "url": "http://127.0.0.1:9/v1/rerank",
                                                     **reranker}}))
            env = {**{k: v for k, v in os.environ.items() if not k.startswith("RAG_")}, "RAG_TRACE": "off"}
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.dict(orchestrator.RETRIEVER_FACTORIES, {"bm25": self.BM25}, clear=True):
                orch = RetrievalOrchestrator(RAGPaths(Path(d)))
        if orch.reranker is not None:
            self.addCleanup(orch.reranker.close)
        return orch

    def test_the_overlay_sets_the_window(self):
        orch = self.orch({"max_chars": 1200, "window": "centred"})
        self.assertEqual((orch.reranker.max_chars, orch.reranker.window), (1200, "centred"))
        orch = self.orch({})
        self.assertEqual((orch.reranker.max_chars, orch.reranker.window), (2400, "head"))
        # a window it does not know: served without the reranker, which says why
        with self.assertLogs("rag_core.orchestrator", "WARNING"):
            orch = self.orch({"window": "middle"})
        self.assertIsNone(orch.reranker)
        self.assertIn("window", orch.rerank_error)


class FusionTieBreakTests(unittest.TestCase):

    def test_equal_scores_order_is_deterministic(self):
        def hit(cid, retriever, rank):
            return SearchHit(retriever=retriever, variant_id="v0", purpose="primary", chunk_id=cid,
                             doc_id="d", rank=rank, score=1.0, text=cid)
        hits = [hit("b", "bm25", 1), hit("a", "dense", 1), hit("c", "bm25", 2), hit("c", "dense", 2)]
        out = rrf_fuse(hits, retriever_weights={}, variant_weights={}, active_retrievers=["bm25", "dense"], limit=10)
        self.assertEqual([i["chunk_id"] for i in out], ["c", "a", "b"])
        again = rrf_fuse(list(reversed(hits)), retriever_weights={}, variant_weights={},
                         active_retrievers=["bm25", "dense"], limit=10)
        self.assertEqual([i["chunk_id"] for i in again], ["c", "a", "b"])
        self.assertNotIn("text", out[0])
        kept = rrf_fuse(hits, retriever_weights={}, variant_weights={}, active_retrievers=["bm25"], limit=10,
                        keep_text=True)
        self.assertIn("text", kept[0])


if __name__ == "__main__":
    unittest.main()
