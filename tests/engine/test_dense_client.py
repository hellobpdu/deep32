import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rag_core.dense import JinaEmbeddingClient, load_dense_config


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive
    connections = set()

    def do_POST(self):
        _Handler.connections.add(self.client_address)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data = [{"index": i, "embedding": [0.0] * 4} for i, _ in enumerate(body["input"])]
        out = json.dumps({"data": data, "model": "stub"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class PooledEmbeddingClientTests(unittest.TestCase):

    def setUp(self):
        _Handler.connections = set()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        cfg = load_dense_config()
        url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1/embeddings"
        self.config = cfg.__class__(**{**cfg.__dict__, "embedding_url": url, "vector_size": 4})

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_sequential_calls_reuse_one_connection(self):
        c = JinaEmbeddingClient(self.config, timeout_seconds=5)
        try:
            for _ in range(5):
                vecs, _model = c.embed(["hello"])
                self.assertEqual(len(vecs), 1)
        finally:
            c.close()
        self.assertEqual(len(_Handler.connections), 1)

    def test_query_timeout_override(self):
        c = JinaEmbeddingClient(self.config, timeout_seconds=3)
        try:
            self.assertEqual(c._client.timeout.read, 3.0)
            self.assertEqual(c._client.timeout.connect, 3.0)
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
