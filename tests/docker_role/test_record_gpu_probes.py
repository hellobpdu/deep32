"""record_gpu_probes.py: recording and comparing the long Forge probes with a fake post; no socket is opened.

All values are synthetic. Loads src/model-bridge/record_gpu_probes.py by path.
"""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("record_gpu_probes_under_test",
                                              ROOT / "src/model-bridge/record_gpu_probes.py")
recorder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recorder)
TOKEN = "a" * 64
EMBED_URL, RERANK_URL = "http://127.0.0.1:8081", "http://127.0.0.1:8082"


def probe_file():
    return {"model": "synthetic-rerank", "query": "synthetic query", "documents": ["d0"], "order": [0],
            "scores": [0.5], "tolerance": 0.005,
            "embed": {"model": "synthetic-embed", "input": "Query: synthetic", "min_cosine": 0.999,
                      "vector": [1.0, 0.0]},
            "long": {"rerank": {"model": "synthetic-rerank", "query": "synthetic long query",
                                "documents": ["p0", "p1", "p2"], "tolerance": 0.005, "source": "synthetic"},
                     "embed": {"model": "synthetic-embed", "input": "Document: synthetic", "min_cosine": 0.999,
                               "source": "synthetic"}}}


def scored(*pairs):
    return [{"index": index, "relevance_score": score} for index, score in pairs]


class FakePost:
    def __init__(self, rerank=None, embedding=None, status=200):
        self.calls = []
        self.rerank = scored((2, 0.75), (0, 0.5), (1, -0.25)) if rerank is None else rerank
        self.embedding = [0.6, 0.8] if embedding is None else embedding
        self.status = status

    def __call__(self, base, path, body, token):
        self.calls.append((base, path, body, token))
        if path == "/v1/rerank":
            return self.status, json.dumps({"results": self.rerank}).encode()
        return self.status, json.dumps({"data": [{"embedding": self.embedding}]}).encode()


class RecorderTests(unittest.TestCase):
    def setUp(self):
        guard = mock.patch.object(socket, "socket", side_effect=AssertionError("the recorder tests open no socket"))
        guard.start()
        self.addCleanup(guard.stop)

    def record(self, post, data=None):
        return recorder.record(probe_file() if data is None else data, EMBED_URL, RERANK_URL, TOKEN,
                               "Synthetic card", "2026-11-21", post=post)

    def test_record_fills_only_the_long_values(self):
        post = FakePost()
        data = probe_file()
        result = self.record(post, data)
        expected = probe_file()
        expected["long"]["rerank"].update(order=[2, 0, 1], scores=[0.75, 0.5, -0.25],
                                          recorded="Synthetic card, 2026-11-21")
        expected["long"]["embed"].update(vector=[0.6, 0.8], recorded="Synthetic card, 2026-11-21")
        self.assertEqual(result, expected)
        self.assertEqual(data, probe_file())
        self.assertEqual(post.calls, [
            (RERANK_URL, "/v1/rerank", {"model": "synthetic-rerank", "query": "synthetic long query",
                                        "documents": ["p0", "p1", "p2"], "top_n": 3}, TOKEN),
            (EMBED_URL, "/v1/embeddings", {"input": ["Document: synthetic"], "model": "synthetic-embed"}, TOKEN)])

    def test_record_refuses_an_incomplete_rerank_answer(self):
        for answer in (scored((0, 0.5), (1, 0.25)), scored((0, 0.5), (0, 0.5), (1, 0.25)),
                       scored((0, 0.5), (1, 0.25), (3, 0.1)), scored((0, 0.5), (1, 0.25), (2, float("nan"))),
                       scored((0, 0.5), (True, 0.25), (2, 0.1)), scored((0, 0.5), (1, "0.25"), (2, 0.1))):
            with self.subTest(answer=answer), self.assertRaises(recorder.RecordError):
                self.record(FakePost(rerank=answer))
        for post in (FakePost(status=503), FakePost(embedding=[]), FakePost(embedding=[0.6, float("inf")])):
            with self.assertRaises(recorder.RecordError):
                self.record(post)

    def test_token_file_must_be_64_hex(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-recorder-") as folder:
            path = Path(folder) / "synthetic-token"
            for value in ("x" * 64, "A" * 64, "a" * 63, "a" * 64 + "\n\n", "a" * 64 + "\r\n"):
                path.write_bytes(value.encode("ascii"))
                with self.subTest(value=repr(value[-3:])), self.assertRaises(recorder.UsageError):
                    recorder.read_token(path)
            path.write_bytes((TOKEN + "\n").encode("ascii"))
            self.assertEqual(recorder.read_token(path), TOKEN)

    def test_compare_within_and_outside_tolerance(self):
        a = self.record(FakePost())
        near = self.record(FakePost(rerank=scored((0, 0.502), (2, 0.749), (1, -0.25)), embedding=[0.6001, 0.7999]))
        numbers = recorder.compare(a, near)
        self.assertAlmostEqual(numbers["max_diff"], 0.002)
        self.assertFalse(numbers["same_order"])
        self.assertTrue(numbers["ok"])
        far = self.record(FakePost(rerank=scored((2, 0.75), (0, 0.5), (1, -0.24))))
        numbers = recorder.compare(a, far)
        self.assertAlmostEqual(numbers["max_diff"], 0.01)
        self.assertTrue(numbers["same_order"])
        self.assertFalse(numbers["ok"])
        self.assertFalse(recorder.compare(a, self.record(FakePost(embedding=[0.8, -0.6])))["ok"])
        other = copy.deepcopy(far)
        other["long"]["rerank"]["query"] = "another synthetic query"
        with self.assertRaises(recorder.UsageError):
            recorder.compare(a, other)
        with self.assertRaises(recorder.UsageError):
            recorder.compare(a, probe_file())

    def test_command_line_writes_nothing_on_a_refusal_and_reports_compare(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-recorder-") as folder:
            folder = Path(folder)
            token, probe, out = folder / "synthetic-token", folder / "probe.json", folder / "recorded.json"
            token.write_bytes((TOKEN + "\n").encode("ascii"))
            raw = (json.dumps(probe_file(), indent=1, ensure_ascii=True) + "\n").encode("ascii")
            probe.write_bytes(raw)
            args = ["record", "--probe", str(probe), "--embed-url", EMBED_URL, "--rerank-url", RERANK_URL,
                    "--token-file", str(token), "--card", "Synthetic card"]
            err = io.StringIO()
            with mock.patch.object(recorder, "post", FakePost(status=503)), contextlib.redirect_stderr(err):
                self.assertEqual(recorder.main(args), 1)
            self.assertEqual(probe.read_bytes(), raw)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["probe.json", "synthetic-token"])
            self.assertNotIn(TOKEN, err.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(recorder.main(args[:4] + ["http://127.0.0.1:8081/v1"] + args[5:]), 2)
            with mock.patch.object(recorder, "post", FakePost()):
                self.assertEqual(recorder.main(args + ["--out", str(out)]), 0)
            self.assertEqual(probe.read_bytes(), raw)
            recorded = json.loads(out.read_bytes())
            self.assertEqual(recorded["long"]["rerank"]["order"], [2, 0, 1])
            self.assertEqual(out.read_bytes(), (json.dumps(recorded, indent=1, ensure_ascii=True) + "\n").encode())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(recorder.main(["compare", str(out), str(out)]), 0)
            self.assertEqual(stdout.getvalue().splitlines(), [
                "max score difference 0.00e+00 (tolerance 0.005)", "same order yes",
                "cosine 1.000000000 (min_cosine 0.999)"])


if __name__ == "__main__":
    unittest.main()
