"""Offline Linux bridge/runtime checks. All credentials are synthetic test material.

The HTTP boundary is exercised in process. Socket creation is forbidden for the
whole suite. --scratch selects the generated fixture folder explicitly.
"""
from __future__ import annotations

import argparse
from email.message import Message
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src/model-bridge"
sys.path.insert(0, str(SOURCE))
import d32_model_bridge as shared
import linux_bridge as bridge
import linux_runtime as runtime

TOKEN = hashlib.sha256(b"T-0190 SYNTHETIC model bridge credential").hexdigest()
ROTATED = hashlib.sha256(b"T-0190 SYNTHETIC model bridge rotated credential").hexdigest()
SCRATCH = None


class Response:
    def __init__(self, status, payload):
        self.status = status
        self.data = json.dumps(payload).encode()
        self.headers = {"content-type": "application/json"}
        self.will_close = False

    def read(self):
        return self.data


class Connection:
    def __init__(self, calls, reply):
        self.calls, self.reply = calls, reply
        self.sock = object()

    def putrequest(self, method, path, **kwargs):
        self.call = {"method": method, "path": path, "headers": []}

    def putheader(self, key, value):
        self.call["headers"].append((key, value))

    def endheaders(self, body):
        self.call["body"] = body
        self.calls.append(self.call)

    def getresponse(self):
        return self.reply(self.call)

    def close(self):
        self.sock = None


def configuration():
    return {"schema": "deep32.model-bridge.v1", "GPU_EXT_EMBED_MODEL_ID": "synthetic-embed",
            "GPU_EXT_EMBED_REVISION": "synthetic-embed-revision", "GPU_EXT_RERANK_MODEL_ID": "synthetic-rerank",
            "GPU_EXT_RERANK_REVISION": "synthetic-rerank-revision"}


def probes(long_scores=(0.5, 0.25, -0.125), long_vector=(0.6, 0.8), recorded=True):
    long_rerank = {"model": "synthetic-rerank", "query": "synthetic long query", "documents": ["p0", "p1", "p2"],
                   "tolerance": 0.005}
    long_embed = {"model": "synthetic-embed", "input": "Document: synthetic", "min_cosine": 0.999}
    if recorded:
        long_rerank.update(order=[0, 1, 2], scores=list(long_scores))
        long_embed.update(vector=list(long_vector))
    return {"model": "synthetic-rerank", "query": "synthetic query", "documents": ["d0"], "order": [0],
            "scores": [0.5], "tolerance": 0.005,
            "embed": {"model": "synthetic-embed", "input": "Query: synthetic", "min_cosine": 0.999,
                      "vector": [0.25, -0.5]},
            "long": {"rerank": long_rerank, "embed": long_embed}}


class Fixture(unittest.TestCase):
    def setUp(self):
        if not SCRATCH:
            self.fail("supply an explicit --scratch directory")
        Path(SCRATCH).mkdir(parents=True, exist_ok=True)
        work = tempfile.TemporaryDirectory(prefix="synthetic-model-bridge-", dir=SCRATCH)
        self.addCleanup(work.cleanup)
        self.path = Path(work.name)
        self.token = self.path / "synthetic-forge-token"
        self.token.write_text(TOKEN + "\n", encoding="ascii", newline="\n")
        self.calls = []
        self.expected = TOKEN
        self.wrong_model = False
        self.guard = bridge.CredentialGuard(str(self.token))
        self.settings = bridge.Settings(configuration(), self.guard)
        self.up = {kind: bridge.Door(self.settings, port, self.guard) for kind, port in self.settings.ports.items()}
        for up in self.up.values():
            up._connect = lambda: Connection(self.calls, self.model_reply)
        self.att = bridge.Attestor(self.settings, self.up, self.guard)

    def model_reply(self, call):
        if dict(call["headers"]).get("Authorization") != "Bearer " + self.expected:
            return Response(401, {"error": ROTATED})
        if call["path"] == "/info":
            return Response(200, {"model_id": "wrong" if self.wrong_model else self.settings.embed_model,
                                  "model_sha": self.settings.embed_revision})
        if call["path"] == "/health":
            return Response(200, {"status": "ok", "model": "wrong" if self.wrong_model else self.settings.rerank_model,
                                  "revision": self.settings.rerank_revision})
        if call["path"] == "/v1/embeddings":
            return Response(200, {"data": [{"embedding": [0.25, -0.5]}]})
        return Response(200, {"results": [{"index": 0, "relevance_score": 0.5}]})

    def handler(self, kind="embed", path=None, body=b'{"synthetic":true}', length=None):
        handler = object.__new__(shared.make_handler(kind, self.att, self.up[kind]))
        handler.path = shared.ROUTES[kind] if path is None else path
        handler.headers = Message()
        handler.headers["Content-Length"] = str(len(body)) if length is None else length
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Authorization"] = "synthetic attacker bearer"
        handler.headers["Proxy-Authorization"] = "synthetic proxy bearer"
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        return handler


class ModelTests(Fixture):
    def test_authenticated_attestation_then_models_share_only_enrolled_bearer(self):
        self.att.run_once()
        self.assertTrue(self.att.snapshot()["ready"])
        for kind in shared.ROUTES:
            handler = self.handler(kind)
            handler.do_POST()
            handler.send_response.assert_called_once_with(200)
        self.assertEqual([(c["method"], c["path"]) for c in self.calls], [
            ("GET", "/info"), ("GET", "/health"), ("POST", "/v1/embeddings"), ("POST", "/v1/rerank")])
        for call in self.calls:
            self.assertEqual([(k, v) for k, v in call["headers"] if "authorization" in k.lower()],
                             [("Authorization", "Bearer " + TOKEN)])
        self.assertNotIn(TOKEN, json.dumps(self.att.snapshot()))

    def test_rotated_credential_refuses_immediately_then_restart_reattests(self):
        self.att.run_once()
        self.token.write_text(ROTATED + "\n", encoding="ascii", newline="\n")
        calls = len(self.calls)
        handler = self.handler()
        handler.do_POST()
        handler.send_response.assert_called_once_with(503)
        self.assertEqual(len(self.calls), calls)
        self.assertFalse(self.att.snapshot()["ready"])
        # Simulate the authorised restart transaction, including the door.
        self.expected = ROTATED
        guard = bridge.CredentialGuard(str(self.token))
        settings = bridge.Settings(configuration(), guard)
        ups = {kind: bridge.Door(settings, port, guard) for kind, port in settings.ports.items()}
        for upstream in ups.values():
            upstream._connect = lambda: Connection(self.calls, self.model_reply)
        att = bridge.Attestor(settings, ups, guard)
        self.assertFalse(att.snapshot()["ready"])
        att.run_once()
        self.assertTrue(att.snapshot()["ready"])

    def test_missing_credential_refuses_without_provider_call(self):
        self.att.run_once()
        self.token.unlink()
        calls = len(self.calls)
        self.handler().do_POST()
        self.assertEqual(len(self.calls), calls)
        self.assertFalse(self.att.ok("embed")[0])
        with self.assertRaisesRegex(bridge.CredentialError, "^Credentials are unavailable"):
            bridge.CredentialGuard(str(self.token))

    def test_wrong_credential_attestation_and_forwarded_401_fail_closed(self):
        self.expected = ROTATED
        with self.assertLogs(shared.log, "WARNING") as logs:
            self.att.run_once()
        self.assertFalse(self.att.snapshot()["ready"])
        self.assertTrue(all("forge-auth-rejected" in self.att.ok(k)[1] for k in shared.ROUTES))
        self.assertNotIn(ROTATED, str(logs.output))
        self.expected = TOKEN
        self.att.run_once()
        self.expected = ROTATED
        handler = self.handler()
        handler.do_POST()
        handler.send_response.assert_called_once_with(401)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "forge-auth-rejected"})
        self.assertFalse(self.att.ok("rerank")[0])

    def test_model_mismatch_refuses_both_paths(self):
        self.wrong_model = True
        self.att.run_once()
        calls = len(self.calls)
        for kind in shared.ROUTES:
            handler = self.handler(kind)
            handler.do_POST()
            handler.send_response.assert_called_once_with(503)
        self.assertEqual(len(self.calls), calls)
        self.assertTrue(all("forge-model-mismatch" in self.att.ok(k)[1] for k in shared.ROUTES))

    def test_embedder_golden_probe_and_missing_revision_fail_closed(self):
        self.settings.embed_revision = ""
        self.att.run_once()
        self.assertFalse(self.att.ok("embed")[0])
        self.settings.embed_probe_input = "synthetic probe"
        self.settings.embed_probe_sha256 = hashlib.sha256(struct.pack("<2f", 0.25, -0.5)).hexdigest()
        self.att.run_once()
        self.assertTrue(self.att.ok("embed")[0])
        self.settings.embed_probe_sha256 = "0" * 64
        self.att.run_once()
        self.assertFalse(self.att.ok("embed")[0])

    def test_routes_and_framing_deny_before_outbound(self):
        self.att.run_once()
        calls = len(self.calls)
        for path in ("/v1/embeddings/", "/v1/embeddings?x=1", "/info", "/v1/rerank", "/"):
            handler = self.handler(path=path)
            handler.do_POST()
            handler.send_response.assert_called_once_with(404)
        for value, code in (("invalid", 400), ("-1", 413), (str(shared.MAX_BODY + 1), 413)):
            handler = self.handler(length=value)
            handler.do_POST()
            handler.send_response.assert_called_once_with(code)
        handler = self.handler(body=b"x", length="2")
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)
        self.assertEqual(len(self.calls), calls)

    def test_configuration_refuses_raw_tokens_urls_and_unproven_identity(self):
        for field, value in (("GPU_EXT_TOKEN", TOKEN), ("GPU_EXT_HOST", "wrong.invalid"),
                ("GPU_EXT_TRANSPORT", "tailnet"), ("GPU_EXT_RECHECK_SECONDS", "nan"),
                ("GPU_EXT_EMBED_REVISION", ""), ("GPU_EXT_RERANK_MODEL_ID", "")):
            config = configuration()
            config[field] = value
            with self.assertRaisesRegex(ValueError, "^Model identity settings are invalid"):
                bridge.Settings(config, self.guard)

    def test_credential_malformed_regular_file_checks_and_bounded_read(self):
        for value in ("", "A" * 64, TOKEN + "\r\n", TOKEN + "\v", TOKEN + "\n\n", "g" * 64):
            self.token.write_text(value, encoding="ascii", newline="\n")
            with self.assertRaises(bridge.CredentialError):
                bridge.protected_tokens(str(self.token))
        self.token.write_text(TOKEN, encoding="ascii", newline="\n")
        with mock.patch.object(bridge.os, "read", wraps=os.read) as read:
            self.assertEqual(bridge.protected_tokens(str(self.token)), (TOKEN,))
        self.assertEqual(read.call_args.args[1], 66)
        self.token.unlink()
        self.token.mkdir()
        with self.assertRaises(bridge.CredentialError):
            bridge.protected_tokens(str(self.token))

    def probe_reply(self, long_scores=(0.5, 0.25, -0.125), long_indices=(0, 1, 2), long_vector=(0.6, 0.8)):
        """Answers the short and long probe calls apart (3 documents, or an input starting with "Document:"), with
        the configured revision in /info and /health and self.reported[kind] added to them. The long answer is
        read from self.long_answer at call time: a pooled connection keeps the reply it was made with."""
        self.long_answer = {"scores": long_scores, "indices": long_indices, "vector": long_vector}
        self.reported = getattr(self, "reported", None) or {"embed": {}, "rerank": {}}

        def reply(call):
            if dict(call["headers"]).get("Authorization") != "Bearer " + self.expected:
                return Response(401, {"error": ROTATED})
            if call["path"] == "/info":
                return Response(200, {"model_id": self.settings.embed_model, "model_sha": self.settings.embed_revision,
                                      **self.reported["embed"]})
            if call["path"] == "/health":
                return Response(200, {"status": "ok", "model": self.settings.rerank_model,
                                      "revision": self.settings.rerank_revision, **self.reported["rerank"]})
            body = json.loads(call["body"])
            answer = self.long_answer
            if call["path"] == "/v1/embeddings":
                vector = answer["vector"] if body["input"][0].startswith("Document:") else (0.25, -0.5)
                return Response(200, {"data": [{"embedding": list(vector)}]})
            if len(body["documents"]) == 3:
                return Response(200, {"results": [{"index": i, "relevance_score": answer["scores"][i]}
                                                  for i in answer["indices"]]})
            return Response(200, {"results": [{"index": 0, "relevance_score": 0.5}]})
        return reply

    def sent(self):
        """The calls made, each POST marked "short" or "long" by its body."""
        result = []
        for call in self.calls:
            if call["method"] != "POST":
                result.append((call["method"], call["path"]))
                continue
            body = json.loads(call["body"])
            long = len(body.get("documents", ())) == 3 or str((body.get("input") or [""])[0]).startswith("Document:")
            result.append((call["method"], call["path"], "long" if long else "short"))
        return result

    def always(self, reply):
        self.att.rerank_probe = probes()
        self.settings.probe_always = True
        self.model_reply = reply

    def test_probe_always_absent_keeps_the_revision_shortcut(self):
        self.att.rerank_probe = probes()
        self.model_reply = self.probe_reply()
        self.att.run_once()
        for kind in shared.ROUTES:
            ok, reason = self.att.ok(kind)
            self.assertTrue(ok)
            self.assertTrue(reason.startswith("model and revision as expected"), reason)
        self.assertEqual(self.sent(), [("GET", "/info"), ("GET", "/health")])

    def test_probe_always_runs_short_and_long_probes(self):
        self.always(self.probe_reply())
        self.att.run_once()
        for kind in shared.ROUTES:
            ok, reason = self.att.ok(kind)
            self.assertTrue(ok)
            self.assertTrue(reason.startswith("model as expected; long probe within tolerance"), reason)
        self.assertEqual(self.sent(), [
            ("GET", "/info"), ("POST", "/v1/embeddings", "short"), ("POST", "/v1/embeddings", "long"),
            ("GET", "/health"), ("POST", "/v1/rerank", "short"), ("POST", "/v1/rerank", "long")])

    def test_right_revision_wrong_long_scores_is_refused(self):
        self.always(self.probe_reply(long_scores=(0.5, 0.25, -0.115)))
        self.att.run_once()
        ok, reason = self.att.ok("rerank")
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("forge-model-mismatch: reranker long probe score differs"), reason)
        handler = self.handler("rerank")
        handler.do_POST()
        handler.send_response.assert_called_once_with(503)
        self.assertTrue(self.att.ok("embed")[0])

    def test_right_revision_wrong_long_vector_is_refused(self):
        self.always(self.probe_reply(long_vector=(0.8, -0.6)))
        self.att.run_once()
        ok, reason = self.att.ok("embed")
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("forge-model-mismatch: embedder long probe differs"), reason)

    def test_unrecorded_long_probe_is_refused(self):
        self.always(self.probe_reply())
        self.att.rerank_probe = probes(recorded=False)
        self.att.run_once()
        for kind, start in (("embed", "embedder long probe not recorded"),
                            ("rerank", "reranker long probe not recorded")):
            ok, reason = self.att.ok(kind)
            self.assertFalse(ok)
            self.assertTrue(reason.startswith(start), reason)

    def test_long_rerank_probe_is_compared_by_passage(self):
        self.always(self.probe_reply(long_indices=(1, 0, 2)))
        self.att.run_once()
        self.assertTrue(self.att.ok("rerank")[0])
        self.att = bridge.Attestor(self.settings, self.up, self.guard)
        self.always(self.probe_reply(long_indices=(0, 1)))
        self.att.run_once()
        ok, reason = self.att.ok("rerank")
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("forge-model-mismatch: reranker long probe answer is incomplete"), reason)

    def test_probe_always_configuration(self):
        def settings(**fields):
            config = configuration()
            config.update(fields)
            return bridge.Settings(config, self.guard)
        self.assertTrue(settings(GPU_EXT_PROBE_ALWAYS="yes", GPU_EXT_RERANK_PROBE="/etc/deep32/gpu-probes.json").probe_always)
        self.assertFalse(settings(GPU_EXT_PROBE_ALWAYS="no").probe_always)
        self.assertFalse(settings().probe_always)
        self.assertTrue(settings(GPU_EXT_PROBE_ALWAYS="yes", GPU_EXT_RERANK_PROBE=bridge.IMAGE_PROBES).probe_always)
        for fields in ({"GPU_EXT_PROBE_ALWAYS": "maybe"}, {"GPU_EXT_PROBE_ALWAYS": "yes"},
                       {"GPU_EXT_PROBE_ALWAYS": "yes", "GPU_EXT_RERANK_PROBE": "/opt/deep32/model-bridge/other.json"}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "^Model identity settings are invalid"):
                settings(**fields)

    def test_long_probes_run_at_start_and_on_identity_change_only(self):
        # lead ruling F-op2: the periodic check keeps the short probes
        self.always(self.probe_reply())
        self.att.run_once()
        self.assertEqual([c for c in self.sent() if c[-1] == "long"],
                         [("POST", "/v1/embeddings", "long"), ("POST", "/v1/rerank", "long")])
        del self.calls[:]
        self.att.run_once()
        self.assertEqual(self.sent(), [("GET", "/info"), ("POST", "/v1/embeddings", "short"),
                                       ("GET", "/health"), ("POST", "/v1/rerank", "short")])
        for kind in shared.ROUTES:
            ok, reason = self.att.ok(kind)
            self.assertTrue(ok)
            self.assertTrue(reason.startswith("model as expected; golden probe"), reason)
            self.assertIn("long probe passed for this identity", reason)
        del self.calls[:]
        self.reported["rerank"] = {"device_kind": "cpu"}
        self.att.run_once()
        self.assertEqual(self.sent(), [("GET", "/info"), ("POST", "/v1/embeddings", "short"),
                                       ("GET", "/health"), ("POST", "/v1/rerank", "short"),
                                       ("POST", "/v1/rerank", "long")])
        self.assertTrue(self.att.ok("rerank")[0])

    def test_long_probes_run_again_after_failure_gpu_ext_off_or_refused_credential(self):
        self.always(self.probe_reply(long_scores=(0.5, 0.25, -0.115)))
        for _ in range(2):
            del self.calls[:]
            self.att.run_once()
            self.assertIn(("POST", "/v1/rerank", "long"), self.sent())
            self.assertFalse(self.att.ok("rerank")[0])
        self.model_reply = self.probe_reply()
        self.att.run_once()
        self.assertTrue(self.att.ok("rerank")[0])

        def gpu_ext_off():
            self.att.is_enabled = lambda: False
            self.att.run_once()
            self.att.is_enabled = lambda: True
        for reset in (self.att.invalidate, gpu_ext_off):
            with self.subTest(reset=reset.__name__):
                reset()
                del self.calls[:]
                self.att.run_once()
                self.assertEqual([c for c in self.sent() if c[-1] == "long"],
                                 [("POST", "/v1/embeddings", "long"), ("POST", "/v1/rerank", "long")])
                self.assertTrue(all(self.att.ok(kind)[0] for kind in shared.ROUTES))

    def test_shared_core_is_used_by_workstation_wrapper(self):
        path = REPO / "src/workstation/node/shims/py/serve_accel.py"
        spec = importlib.util.spec_from_file_location("synthetic_workstation_accel", path)
        wrapper = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"d32svc": types.ModuleType("d32svc")}):
            spec.loader.exec_module(wrapper)
        self.assertIs(wrapper.Upstream, shared.Upstream)
        self.assertIs(wrapper.make_handler, shared.make_handler)
        self.assertTrue(issubclass(wrapper.Attestor, shared.Attestor))


def run(coroutine):
    # All ASGI fixture awaitables complete immediately; no event-loop self-pipe
    # or platform socket is needed for these in-process application checks.
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("fixture unexpectedly waited on external I/O")


async def invoke(app, payload, *, path="/mcp", method="POST", query=b""):
    sent = []
    raw = json.dumps(payload).encode()
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    async def send(msg):
        sent.append(msg)
    await app({"type": "http", "method": method, "path": path, "query_string": query}, receive, send)
    return sent


class RuntimeTests(Fixture):
    def test_engine_token_stays_in_memory_with_exact_bridge_urls(self):
        application = mock.Mock()
        api = types.SimpleNamespace(app=application)
        env = {"RAG_ROOT": "/data/content/current", "RAG_EMBEDDING_URL": runtime.EMBEDDING_URL,
               "RAG_RERANK_URL": runtime.RERANK_URL, "RAG_QDRANT_URL": "http://qdrant:6333", "RAG_TRACE": "off"}
        env["RAG_ROOT"] = runtime.CONTENT_ROOT
        before = dict(os.environ)
        app = runtime.prepare_engine(env, api, self.guard)
        self.assertIs(app.app, application)
        self.assertEqual(api._expected_token(), TOKEN)
        self.assertEqual(dict(os.environ), before)
        for key, value in (("RAG_EMBEDDING_URL", "http://embeddings:80/v1/embeddings"),
                           ("RAG_RERANK_URL", "http://reranker:8082/v1/rerank"), ("RAG_TRACE", "on"),
                           ("RAG_TRACE", ""), ("RAG_ROOT", "/var/lib/deep32/content/kb"),
                           ("RAG_QDRANT_URL", "http://external.invalid:6333")):
            with self.assertRaises(ValueError):
                runtime.prepare_engine(dict(env, **{key: value}), api, self.guard)

    def test_imported_bundle_root_and_signed_embedding_are_required(self):
        root = self.path / "synthetic-generation/root"
        for name in ("sqlite/rag.db", "kb/kb.sqlite", "config/retrieval.json", "config/serving.json"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic fixture")
        bundle = {"format": "deep32-kb-bundle", "format_version": 1,
            "embedding": {"model_id": "synthetic-embed", "revision": "synthetic-embed-revision", "dimension": 2}}
        (root.parent / "BUNDLE.json").write_text(json.dumps(bundle), encoding="ascii")
        (root / "config/dense.json").write_text(json.dumps({"collection": "synthetic", "vector_size": 2}), encoding="ascii")
        (root.parent / "dense").mkdir()
        (root.parent / "dense/EXPORT.json").write_text(json.dumps({"collection": "synthetic", "dim": 2}), encoding="ascii")
        env = {"RAG_EMBEDDING_REVISION": "synthetic-embed-revision"}
        runtime.validate_content(env, root=root, model_config=configuration())
        with self.assertRaisesRegex(ValueError, "^Imported content does not match"):
            runtime.validate_content(env, root=root.parent, model_config=configuration())
        with self.assertRaisesRegex(ValueError, "^Imported content does not match"):
            runtime.validate_content({"RAG_EMBEDDING_REVISION": "wrong"}, root=root, model_config=configuration())
        bundle["embedding"]["dimension"] = 3
        (root.parent / "BUNDLE.json").write_text(json.dumps(bundle), encoding="ascii")
        with self.assertRaisesRegex(ValueError, "^Imported content does not match"):
            runtime.validate_content(env, root=root, model_config=configuration())

    def test_existing_immutable_layer_reads_without_wal_or_shm_and_denies_writes(self):
        source = REPO / "src/workstation/node/shims/py/node_layers.py"
        spec = importlib.util.spec_from_file_location("synthetic_node_layers", source)
        layers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(layers)
        database = self.path / "synthetic-rag.db"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE synthetic (id INTEGER)")
            connection.execute("INSERT INTO synthetic VALUES (7)")
        connection.close()
        with layers.immutable_connect(database) as readonly:
            self.assertEqual(readonly.execute("SELECT id FROM synthetic").fetchone()[0], 7)
            with self.assertRaises(sqlite3.OperationalError):
                readonly.execute("INSERT INTO synthetic VALUES (8)")
        readonly.close()
        self.assertFalse(database.with_name(database.name + "-wal").exists())
        self.assertFalse(database.with_name(database.name + "-shm").exists())
        with self.assertRaises(sqlite3.OperationalError):
            layers.immutable_connect(self.path / "missing-synthetic.db")

    def test_gateway_health_uses_authenticated_mcp_and_exact_tool_set(self):
        registry = self.path / "synthetic-mcp-tokens"
        registry.write_text(TOKEN + "\n", encoding="ascii", newline="\n")
        reply = mock.Mock(status=200)
        reply.read.return_value = json.dumps({"result": {"tools": [{"name": n} for n in sorted(runtime.REMOTE_TOOLS)]}}).encode()
        connection = mock.Mock()
        connection.getresponse.return_value = reply
        with mock.patch.object(runtime.http.client, "HTTPConnection", return_value=connection):
            self.assertTrue(runtime.check_gateway(str(registry)))
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ("POST", "/mcp"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(json.loads(kwargs["body"])["method"], "tools/list")
        reply.read.return_value = json.dumps({"result": {"tools": [{"name": "wrong"}]}}).encode()
        with mock.patch.object(runtime.http.client, "HTTPConnection", return_value=connection):
            self.assertFalse(runtime.check_gateway(str(registry)))

    def test_gateway_enrolled_allowlist_revoke_applies_before_next_request(self):
        registry = self.path / "synthetic-mcp-tokens"
        registry.write_text(TOKEN + "\n" + ROTATED + "\n", encoding="ascii", newline="\n")
        calls = []
        async def application(scope, receive, send):
            calls.append(scope)
            await runtime.reply(send, 200, {"synthetic": True})
        edge = types.SimpleNamespace(Config=mock.Mock(return_value="synthetic-config"),
                                     create_app=mock.Mock(return_value=application))
        before = dict(os.environ)
        app = runtime.prepare_gateway({"RAG_CORE_URL": runtime.CORE_URL,
            "TAILSCALE_DNS_NAME": "synthetic.invalid"}, edge, self.guard, str(registry))
        self.assertEqual(edge.Config.call_args.kwargs["mcp_tokens"], (TOKEN, ROTATED))
        self.assertEqual(dict(os.environ), before)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        self.assertEqual(run(invoke(app, payload))[0]["status"], 200)
        registry.write_text(ROTATED + "\n", encoding="ascii", newline="\n")
        self.assertEqual(run(invoke(app, payload))[0]["status"], 503)
        self.assertEqual(len(calls), 1)

    def test_gateway_remote_policy_all_eleven_tools_and_no_unknown_paths(self):
        calls = []
        async def application(scope, receive, send):
            calls.append(scope)
            await runtime.reply(send, 204, {})
        gate = runtime.RemoteGate(application)
        for tool in sorted(runtime.REMOTE_TOOLS):
            payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool}}
            self.assertEqual(run(invoke(gate, payload))[0]["status"], 204)
        self.assertEqual(len(calls), 11)
        for payload in ([{"method": "tools/list"}], {"method": "delete"},
                        {"method": "tools/call", "params": {"name": "write_content"}}):
            response = run(invoke(gate, payload))
            self.assertEqual(json.loads(response[1]["body"])["error"]["code"], -32601)
        for path in ("/health", "/mcp/", "/other"):
            self.assertEqual(run(invoke(gate, {}, path=path))[0]["status"], 404)
        self.assertEqual(run(invoke(gate, {}, query=b"x=1"))[0]["status"], 404)
        self.assertEqual(len(calls), 11)

    def test_literal_secret_env_refused_before_import_or_file_access(self):
        for key in ("RAG_CORE_API_TOKEN", "RAG_MCP_TOKENS", "GPU_EXT_TOKEN"):
            with mock.patch.dict(os.environ, {key: TOKEN}, clear=True), mock.patch.object(runtime, "CredentialGuard") as guard:
                with self.assertRaisesRegex(SystemExit, "^Literal credentials are refused"):
                    runtime.main(["engine"])
            guard.assert_not_called()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True)
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch
    with mock.patch.object(socket, "socket", side_effect=AssertionError("offline fixture attempted a socket")):
        unittest.main(argv=[sys.argv[0]] + remaining)
