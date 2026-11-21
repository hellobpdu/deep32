import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import os
import unittest
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from rag_core import api

TOKEN = "fake-token-" + "0" * 32


class StubOrchestrator:
    retrievers = {"bm25": None}

    def __init__(self, paths):
        pass

    def capabilities(self):
        return {"bm25": True}

    def search(self, query, mode="normal", limit=8):
        return {"query": query, "candidates": []}


class AuthHeaderTests(unittest.TestCase):

    def setUp(self):
        env = mock.patch.dict(os.environ, {"RAG_CORE_API_TOKEN": TOKEN, "RAG_ROOT": "/tmp/none"})
        orch = mock.patch.object(api, "RetrievalOrchestrator", StubOrchestrator)
        for p in (env, orch):
            p.start()
            self.addCleanup(p.stop)

    def test_non_ascii_authorization_is_401_not_500(self):
        with TestClient(api.app) as c:
            for header in (b"Bearer \xe9t\xe9", "Bearer " + TOKEN[:-1] + "\xe9", b"\xff" * 40):
                with self.subTest(header=header):
                    h = header if isinstance(header, bytes) else header.encode("latin-1")
                    r = c.get("/health", headers={"Authorization": h})
                    self.assertEqual(r.status_code, 401)
                    self.assertEqual(r.headers.get("www-authenticate"), "Bearer")
                    r = c.post("/search", json={"query": "ospf"}, headers={"Authorization": h})
                    self.assertEqual(r.status_code, 401)

    def test_valid_missing_and_wrong_tokens(self):
        with TestClient(api.app) as c:
            ok = c.post("/search", json={"query": "ospf"}, headers={"Authorization": f"Bearer {TOKEN}"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["candidates"], [])
            self.assertEqual(c.get("/health").status_code, 401)
            self.assertEqual(c.get("/health", headers={"Authorization": "Bearer nope"}).status_code, 401)

    def test_require_auth_directly(self):
        with self.assertRaises(HTTPException) as caught:
            api.require_auth("Bearer éé")
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIsNone(api.require_auth(f"Bearer {TOKEN}"))


if __name__ == "__main__":
    unittest.main()
