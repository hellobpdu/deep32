"""Offline G-09 checks. All credentials and peer values here are synthetic test material.

Pass --scratch (or D32_PAIRING_TEST_SCRATCH) explicitly; no installed credential is read.
No sockets, TLS sessions, service processes or network connections are started.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/workstation/node/shims/py/serve_accel.py"
SYNTHETIC_TOKEN = hashlib.sha256(b"T-0187 synthetic Forge credential one").hexdigest()
SYNTHETIC_ROTATED_TOKEN = hashlib.sha256(b"T-0187 synthetic Forge credential two").hexdigest()
SCRATCH = os.environ.get("D32_PAIRING_TEST_SCRATCH")


def load_forwarder():
    # d32svc's OpenBSD-only pwd import is outside this isolated HTTP/credential test.
    stub = types.ModuleType("d32svc")
    spec = importlib.util.spec_from_file_location("pairing_test_serve_accel", SOURCE)
    module = importlib.util.module_from_spec(spec)
    old_path = sys.path[:]
    try:
        with mock.patch.dict(sys.modules, {"d32svc": stub}):
            spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


accel = load_forwarder()


def settings_env():
    return {"GPU_EXT_HOST": "synthetic-forge.invalid", "GPU_EXT_ADDR": "192.0.2.1",
            "GPU_EXT_TOKEN_FILE": accel.FORGE_TOKEN_FILE,
            "GPU_EXT_EMBED_MODEL_ID": "synthetic-embed", "GPU_EXT_EMBED_REVISION": "synthetic-e1",
            "GPU_EXT_RERANK_MODEL_ID": "synthetic-rerank", "GPU_EXT_RERANK_REVISION": "synthetic-r1"}


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.headers = {"content-type": "application/json"}
        self.will_close = False
        self.data = json.dumps(payload).encode()

    def read(self):
        return self.data


class FakeConnection:
    """The common outgoing boundary is real; only HTTP transport is replaced."""
    def __init__(self, calls, reply):
        self.calls, self.reply = calls, reply
        self.sock = object()
        self.closed = False

    def putrequest(self, method, path, **kwargs):
        self.current = {"method": method, "path": path, "headers": []}

    def putheader(self, key, value):
        self.current["headers"].append((key, value))

    def endheaders(self, body):
        self.current["body"] = body
        self.calls.append(self.current)

    def getresponse(self):
        return self.reply(self.current)

    def close(self):
        self.closed = True
        self.sock = None


def fake_upstream(settings, port, calls, reply):
    upstream = accel.Upstream(settings, None, port)
    upstream._connect = lambda: FakeConnection(calls, reply)
    return upstream


class ScratchCase(unittest.TestCase):
    def setUp(self):
        if not SCRATCH:
            self.fail("supply an explicit --scratch directory or D32_PAIRING_TEST_SCRATCH")
        Path(SCRATCH).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-forwarder-", dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.token_path = self.folder / "synthetic-forge.token"
        self.token_path.write_bytes((SYNTHETIC_TOKEN + "\n").encode("ascii"))

    def settings(self):
        return accel.Settings(settings_env(), token_path=str(self.token_path))


class CredentialTests(ScratchCase):
    def test_lowercase_hex_with_and_without_lf(self):
        for ending in ("", "\n"):
            with self.subTest(ending=repr(ending)):
                self.token_path.write_bytes((SYNTHETIC_TOKEN + ending).encode("ascii"))
                self.assertEqual(accel.load_forge_token(str(self.token_path)), SYNTHETIC_TOKEN)

    def test_missing_file_is_one_clear_nonsecret_line(self):
        self.token_path.unlink()
        with self.assertRaises(SystemExit) as raised:
            self.settings()
        self.assertEqual(str(raised.exception), accel.UNPAIRED)
        self.assertNotIn("\n", str(raised.exception))
        self.assertNotIn(str(self.token_path), str(raised.exception))

    def test_missing_setting_fails_before_file_access(self):
        env = settings_env()
        del env["GPU_EXT_TOKEN_FILE"]
        with mock.patch.object(accel, "load_forge_token") as read:
            with self.assertRaisesRegex(SystemExit, "Forge is not paired"):
                accel.Settings(env)
        read.assert_not_called()

    def test_env_accepts_only_the_approved_path(self):
        for value in (str(self.token_path), SYNTHETIC_TOKEN, "/tmp/alternate-forge.token"):
            with self.subTest(kind="synthetic alternate path"):
                env = settings_env()
                env["GPU_EXT_TOKEN_FILE"] = value
                with self.assertRaises(SystemExit) as raised:
                    accel.Settings(env)
                self.assertEqual(str(raised.exception), accel.BAD_CREDENTIAL)
                self.assertNotIn(value, str(raised.exception))

    def test_raw_token_env_is_rejected_without_disclosure(self):
        env = settings_env()
        env["GPU_EXT_TOKEN"] = SYNTHETIC_TOKEN
        with self.assertRaises(SystemExit) as raised:
            accel.Settings(env, token_path=str(self.token_path))
        self.assertEqual(str(raised.exception), accel.BAD_CREDENTIAL)
        self.assertNotIn(SYNTHETIC_TOKEN, str(raised.exception))

    def test_malformed_credentials_are_rejected_without_the_value(self):
        cases = (b"", b"a" * 63, b"a" * 66, b"A" * 64, b"g" * 64,
                 b"a" * 64 + b"\r\n", b"a" * 64 + b"\n\n", b"\xff" * 64,
                 b"a" * 32 + b"\n" + b"a" * 31, b"a" * 10000)
        for data in cases:
            with self.subTest(length=len(data)):
                self.token_path.write_bytes(data)
                with self.assertRaises(accel.ForgeCredentialError) as raised:
                    accel.load_forge_token(str(self.token_path))
                self.assertEqual(str(raised.exception), accel.BAD_CREDENTIAL)

    def test_read_is_bounded_to_66_bytes(self):
        with mock.patch.object(accel.os, "read", wraps=os.read) as read:
            self.assertEqual(accel.load_forge_token(str(self.token_path)), SYNTHETIC_TOKEN)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args.args[1], 66)

    def test_unreadable_file_is_nonsecret_failure(self):
        with mock.patch.object(accel.os, "open", side_effect=PermissionError(SYNTHETIC_TOKEN)):
            with self.assertRaises(accel.ForgeCredentialError) as raised:
                accel.load_forge_token(str(self.token_path))
        self.assertEqual(str(raised.exception), accel.BAD_CREDENTIAL)

    def test_directory_cannot_be_a_credential(self):
        self.token_path.unlink()
        self.token_path.mkdir()
        with self.assertRaises(accel.ForgeCredentialError):
            accel.load_forge_token(str(self.token_path))

    def test_symlink_cannot_be_a_credential(self):
        link = self.folder / "synthetic-link.token"
        try:
            link.symlink_to(self.token_path)
        except (OSError, NotImplementedError):
            self.skipTest("this Windows account cannot create symlinks; metadata refusal is checked separately")
        with self.assertRaises(accel.ForgeCredentialError):
            accel.load_forge_token(str(link))

    def test_symlink_or_reparse_metadata_is_refused(self):
        self.assertFalse(accel._not_link(types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o440)))
        if hasattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT"):
            item = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o440,
                                         st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            self.assertFalse(accel._not_link(item))

    def test_posix_permission_matrix(self):
        gpu, wheel = 123, 0
        def item(uid, gid, mode):
            return types.SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode)
        token = item(0, gpu, stat.S_IFREG | 0o440)
        for owner, group, mode, accepted in ((0, gpu, 0o750, True), (0, wheel, 0o711, True),
                                              (0, gpu, 0o755, False), (0, gpu, 0o770, False),
                                              (0, gpu, 0o700, False), (0, 99, 0o750, False),
                                              (1, gpu, 0o750, False)):
            with self.subTest(parent=(owner, group, mode)):
                self.assertEqual(accel._credential_permissions(item(owner, group, mode), token, gpu, wheel),
                                 accepted)
        parent = item(0, gpu, stat.S_IFDIR | 0o750)
        for owner, group, mode in ((1, gpu, 0o440), (0, 99, 0o440), (0, gpu, 0o444),
                                    (0, gpu, 0o640), (0, gpu, 0o400), (0, gpu, 0o2440)):
            with self.subTest(token_metadata=(owner, group, mode)):
                self.assertFalse(accel._credential_permissions(parent, item(owner, group, mode), gpu, wheel))

    def test_posix_loader_checks_opened_descriptors(self):
        gpu, wheel = 123, 0
        # The installed private directory is root:wheel 0711: the service may traverse it but not open it.
        parent = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o711, st_uid=0, st_gid=wheel)
        token = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o440, st_uid=0, st_gid=gpu,
                                      st_dev=1, st_ino=2, st_size=65)
        group_module = types.ModuleType("grp")
        group_module.getgrnam = lambda name: types.SimpleNamespace(gr_gid=gpu if name == "_d32gpu" else wheel)
        for bad in (False, True):
            with self.subTest(bad_opened_permissions=bad), contextlib.ExitStack() as stack:
                opened = types.SimpleNamespace(**vars(token))
                if bad:
                    opened.st_mode = stat.S_IFREG | 0o444
                stack.enter_context(mock.patch.dict(sys.modules, {"grp": group_module}))
                stack.enter_context(mock.patch.object(accel.os, "name", "posix"))
                stack.enter_context(mock.patch.object(accel.os, "O_DIRECTORY", 0x100000, create=True))
                stack.enter_context(mock.patch.object(accel.os, "O_NONBLOCK", 0x200000, create=True))
                stack.enter_context(mock.patch.object(accel.os, "lstat", side_effect=[parent, token, parent]))
                opening = stack.enter_context(mock.patch.object(accel.os, "open", side_effect=[11]))
                stack.enter_context(mock.patch.object(accel.os, "fstat", side_effect=[opened]))
                reading = stack.enter_context(mock.patch.object(accel.os, "read",
                                                               return_value=(SYNTHETIC_TOKEN + "\n").encode()))
                closing = stack.enter_context(mock.patch.object(accel.os, "close"))
                if bad:
                    with self.assertRaises(accel.ForgeCredentialError):
                        accel.load_forge_token("/synthetic/private/forge.token")
                    reading.assert_not_called()
                else:
                    self.assertEqual(accel.load_forge_token("/synthetic/private/forge.token"), SYNTHETIC_TOKEN)
                self.assertEqual(len(opening.call_args_list), 1)
                self.assertEqual(opening.call_args_list[0].args[0], "/synthetic/private/forge.token")
                self.assertEqual(opening.call_args_list[0].kwargs, {})
                self.assertEqual(closing.call_args_list, [mock.call(11)])

    def test_settings_keep_token_only_in_memory_until_restart(self):
        first = self.settings()
        self.token_path.write_bytes(SYNTHETIC_ROTATED_TOKEN.encode("ascii"))
        second = self.settings()
        self.assertEqual(first.forge_token, SYNTHETIC_TOKEN)
        self.assertEqual(second.forge_token, SYNTHETIC_ROTATED_TOKEN)
        calls = []
        for settings in (first, second):
            fake_upstream(settings, 443, calls, lambda call: FakeResponse(200, {})).request("GET", "/info", None, {})
        self.assertEqual([dict(call["headers"])["Authorization"] for call in calls],
                         ["Bearer " + SYNTHETIC_TOKEN, "Bearer " + SYNTHETIC_ROTATED_TOKEN])


class TransportTests(ScratchCase):
    def loopback_env(self):
        env = settings_env()
        env.update(GPU_EXT_TRANSPORT="loopback", GPU_EXT_HOST="localhost", GPU_EXT_ADDR="127.0.0.1")
        return env

    def test_default_tailnet_uses_published_ports(self):
        settings = self.settings()
        self.assertEqual(settings.transport, "tailnet")
        self.assertEqual(settings.ports, {"embed": 443, "rerank": 8443})

    def test_probe_always_setting(self):
        self.assertFalse(accel.Settings(settings_env(), token_path=str(self.token_path)).probe_always)
        for value, expected in (("yes", True), ("no", False)):
            env = dict(settings_env(), GPU_EXT_PROBE_ALWAYS=value)
            with self.subTest(value=value):
                self.assertIs(accel.Settings(env, token_path=str(self.token_path)).probe_always, expected)

    def test_paired_endpoint_urls_select_actual_https_ports(self):
        env = settings_env()
        env.update(GPU_EXT_HOST='forge.synthetic.ts.net',
                   GPU_EXT_EMBED_URL='https://forge.synthetic.ts.net:18443',
                   GPU_EXT_RERANK_URL='https://forge.synthetic.ts.net:19443')
        settings = accel.Settings(env, token_path=str(self.token_path))
        self.assertEqual(settings.ports, {'embed': 18443, 'rerank': 19443})
        calls = []
        upstream = fake_upstream(settings, settings.ports['embed'], calls, lambda call: FakeResponse(200, {}))
        upstream.request('GET', '/info', None, {})
        self.assertEqual(dict(calls[0]['headers'])['Host'], 'forge.synthetic.ts.net:18443')
        self.assertEqual(dict(calls[0]['headers'])['Authorization'], 'Bearer ' + SYNTHETIC_TOKEN)

    def test_endpoint_urls_refuse_wrong_host_scheme_paths_and_port_conflicts(self):
        env = settings_env()
        env.update(GPU_EXT_HOST='forge.synthetic.ts.net',
                   GPU_EXT_EMBED_URL='https://forge.synthetic.ts.net:18443',
                   GPU_EXT_RERANK_URL='https://forge.synthetic.ts.net:19443')
        for value in ('https://other.synthetic.ts.net:18443', 'http://forge.synthetic.ts.net:18443',
                      'https://forge.synthetic.ts.net:18443/', 'https://forge.synthetic.ts.net:18443/info',
                      'https://forge.synthetic.ts.net:18443?x=1', 'https://forge.synthetic.ts.net:18443#x',
                      'https://user@forge.synthetic.ts.net:18443', 'https://forge.synthetic.ts.net:65536',
                      'https://forge.synthetic.ts.net:0', 'https://forge.synthetic.ts.net:18443\n'):
            changed = dict(env, GPU_EXT_EMBED_URL=value)
            with self.subTest(url=value), mock.patch.object(accel, 'load_forge_token') as token:
                with self.assertRaisesRegex(SystemExit, 'path-free HTTPS URLs'):
                    accel.Settings(changed, token_path=str(self.token_path))
                token.assert_not_called()
        for changed in (dict(env, GPU_EXT_EMBED_PORT='443'),
                        {key: value for key, value in env.items() if key != 'GPU_EXT_RERANK_URL'},
                        dict(env, GPU_EXT_TRANSPORT='loopback')):
            with self.assertRaisesRegex(SystemExit, 'path-free HTTPS URLs'):
                accel.Settings(changed, token_path=str(self.token_path))

    def test_loopback_uses_only_the_c8_local_door(self):
        settings = accel.Settings(self.loopback_env(), token_path=str(self.token_path))
        self.assertEqual(settings.transport, "loopback")
        self.assertEqual(settings.ports, {"embed": 8081, "rerank": 8082})
        for key, values in (("GPU_EXT_HOST", ("synthetic-forge.invalid", "127.0.0.1", "localhost.")),
                            ("GPU_EXT_ADDR", ("192.0.2.1", "::1", "127.0.0.2", "")),
                            ("GPU_EXT_EMBED_PORT", ("443", "18081")),
                            ("GPU_EXT_RERANK_PORT", ("8443", "18082"))):
            for value in values:
                with self.subTest(field=key, value=value):
                    env = self.loopback_env()
                    env[key] = value
                    with self.assertRaisesRegex(SystemExit, "loopback requires localhost"):
                        accel.Settings(env, token_path=str(self.token_path))
        with self.assertRaisesRegex(ValueError, "loopback requires local door ports"):
            accel.Upstream(settings, None, 443)

    def test_unknown_transport_and_invalid_ports_are_fixed_failures(self):
        env = settings_env()
        env["GPU_EXT_TRANSPORT"] = SYNTHETIC_TOKEN
        with self.assertRaises(SystemExit) as raised:
            accel.Settings(env, token_path=str(self.token_path))
        self.assertEqual(str(raised.exception), "d32accel: GPU_EXT_TRANSPORT must be tailnet or loopback")
        for value in ("0", "65536", "-1", SYNTHETIC_TOKEN):
            env = settings_env()
            env["GPU_EXT_EMBED_PORT"] = value
            with self.assertRaises(SystemExit) as raised:
                accel.Settings(env, token_path=str(self.token_path))
            self.assertEqual(str(raised.exception), "d32accel: Forge ports must be integers from 1 to 65535")

    def test_local_http_and_tailnet_tls_connect_at_the_common_boundary(self):
        for transport in ("loopback", "tailnet"):
            with self.subTest(transport=transport):
                env = self.loopback_env() if transport == "loopback" else settings_env()
                settings = accel.Settings(env, token_path=str(self.token_path))
                port = settings.ports["embed"]
                calls = []
                connection = FakeConnection(calls, lambda call: FakeResponse(200, {}))
                raw, ctx = mock.Mock(), mock.Mock()
                upstream = accel.Upstream(settings, ctx, port)
                address_info = [(accel.socket.AF_INET, accel.socket.SOCK_STREAM, 6, "", (settings.addr, port))]
                with (mock.patch.object(accel.http.client, "HTTPConnection", return_value=connection) as http,
                        mock.patch.object(accel.socket, "getaddrinfo", return_value=address_info) as address,
                        mock.patch.object(accel.socket, "socket", return_value=raw)):
                    self.assertEqual(upstream.request("GET", "/info", None, {})[0], 200)
                http.assert_called_once_with(settings.addr, port, timeout=settings.read_timeout)
                address.assert_called_once_with(settings.addr, port, type=accel.socket.SOCK_STREAM,
                                                flags=accel.socket.AI_NUMERICHOST)
                raw.connect.assert_called_once_with((settings.addr, port))
                if transport == "loopback":
                    self.assertIs(connection.sock, raw)
                    ctx.wrap_socket.assert_not_called()
                    self.assertEqual(dict(calls[0]["headers"])["Host"], "localhost:8081")
                else:
                    ctx.wrap_socket.assert_called_once_with(raw, server_hostname=settings.host)
                    self.assertIs(connection.sock, ctx.wrap_socket.return_value)
                self.assertEqual(connection.auto_open, 0)
                self.assertEqual(dict(calls[0]["headers"])["Authorization"], "Bearer " + SYNTHETIC_TOKEN)

    def test_tailnet_cannot_fall_back_to_plain_http_without_a_tls_context(self):
        upstream = accel.Upstream(self.settings(), None, 443)
        with mock.patch.object(accel.socket, "socket") as socket:
            with self.assertRaisesRegex(RuntimeError, "tailnet transport requires verified TLS"):
                upstream._connect()
        socket.assert_not_called()

    def test_loopback_startup_does_not_open_the_ca_bundle(self):
        env = self.loopback_env()
        service = types.SimpleNamespace(listeners={kind: object() for kind in accel.ROUTES})
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            stack.enter_context(mock.patch.object(accel.d32svc, "boot", return_value=service, create=True))
            stack.enter_context(mock.patch.object(accel.d32svc, "base_unveils", return_value=[], create=True))
            stack.enter_context(mock.patch.object(accel.d32svc, "confine", create=True))
            stack.enter_context(mock.patch.object(accel, "load_forge_token", return_value=SYNTHETIC_TOKEN))
            ca = stack.enter_context(mock.patch.object(accel.ssl, "create_default_context"))
            stack.enter_context(mock.patch.object(accel, "Server"))
            stack.enter_context(mock.patch.object(accel.threading, "Thread"))
            stack.enter_context(mock.patch.object(accel.threading, "Event", return_value=mock.Mock(wait=lambda value: True)))
            stack.enter_context(mock.patch("signal.signal"))
            accel.main()
        ca.assert_not_called()


class ForwardingTests(ScratchCase):
    def test_every_attestation_and_forwarded_call_has_only_enrolled_bearer(self):
        settings = self.settings()
        settings.embed_revision = settings.rerank_revision = ""
        vector = [0.25, -0.5]
        settings.embed_probe_input = "synthetic attestation input"
        settings.embed_probe_sha256 = hashlib.sha256(struct.pack("<2f", *vector)).hexdigest()
        calls = []
        def reply(call):
            if call["path"] == "/info":
                return FakeResponse(200, {"model_id": settings.embed_model})
            if call["path"] == "/health":
                return FakeResponse(200, {"status": "ok", "model": settings.rerank_model})
            if call["path"] == "/v1/embeddings":
                return FakeResponse(200, {"data": [{"embedding": vector}]})
            return FakeResponse(200, {"results": [{"index": 0, "relevance_score": 0.5}]})
        upstreams = {kind: fake_upstream(settings, port, calls, reply) for kind, port in settings.ports.items()}
        attestor = accel.Attestor(settings, upstreams)
        attestor.rerank_probe = {"model": settings.rerank_model, "query": "synthetic query",
                                 "documents": ["synthetic document"], "order": [0], "scores": [0.5]}
        with mock.patch.object(accel, "setting_on", return_value=True):
            attestor.run_once()
        self.assertTrue(all(attestor.ok(kind)[0] for kind in accel.ROUTES))
        for kind in accel.ROUTES:
            self.run_handler(kind, attestor, upstreams[kind])
        self.assertEqual([(call["method"], call["path"]) for call in calls],
                         [("GET", "/info"), ("POST", "/v1/embeddings"), ("GET", "/health"),
                          ("POST", "/v1/rerank"), ("POST", "/v1/embeddings"), ("POST", "/v1/rerank")])
        for call in calls:
            auth = [(key, value) for key, value in call["headers"] if key.lower() == "authorization"]
            self.assertEqual(auth, [("Authorization", "Bearer " + SYNTHETIC_TOKEN)])
        self.assertNotIn(SYNTHETIC_TOKEN, json.dumps(attestor.snapshot()))
        self.assertNotIn("authorization", accel.FORWARD_REQUEST_HEADERS)

    def run_handler(self, kind, attestor, upstream):
        from email.message import Message

        handler = object.__new__(accel.make_handler(kind, attestor, upstream))
        handler.path = accel.ROUTES[kind]
        handler.headers = Message()
        body = b'{"synthetic":true}'
        for key, value in (("Content-Length", str(len(body))), ("Content-Type", "application/json"),
                           ("Authorization", "synthetic caller credential"),
                           ("aUtHoRiZaTiOn", "synthetic mixed-case caller credential")):
            handler.headers[key] = value
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.do_POST()
        return handler

    def test_common_boundary_removes_all_caller_auth_variants(self):
        calls = []
        upstream = fake_upstream(self.settings(), 443, calls, lambda call: FakeResponse(200, {}))
        upstream.request("GET", "/info", None, {"Authorization": "synthetic-one",
                         "authorization": "synthetic-two", "AUTHORIZATION": "synthetic-three",
                         "pRoXy-AuThOrIzAtIoN": "synthetic-proxy", "Accept": "application/json"})
        self.assertEqual([(key, value) for key, value in calls[0]["headers"] if "authorization" in key.lower()],
                         [("Authorization", "Bearer " + SYNTHETIC_TOKEN)])

    def test_401_attestation_is_a_fixed_auth_failure_without_response_body(self):
        settings = self.settings()
        calls = []
        upstreams = {kind: fake_upstream(settings, port, calls, lambda call: FakeResponse(401, {"error": SYNTHETIC_TOKEN}))
                     for kind, port in settings.ports.items()}
        attestor = accel.Attestor(settings, upstreams)
        with mock.patch.object(accel, "setting_on", return_value=True), self.assertLogs(accel.log, "WARNING") as logs:
            attestor.run_once()
        self.assertTrue(all("forge-auth-rejected" in attestor.ok(kind)[1] for kind in accel.ROUTES))
        self.assertNotIn(SYNTHETIC_TOKEN, str(logs.output) + json.dumps(attestor.snapshot()))
        for method in (attestor._get_json, attestor._post_json):
            args = ("embed", "/info") if method == attestor._get_json else ("embed", "/v1/embeddings", {})
            with self.assertRaisesRegex(accel.ForgeAuthRejected, "^forge-auth-rejected$"):
                method(*args)

    def test_forwarded_401_has_fixed_reason(self):
        settings = self.settings()
        upstream = fake_upstream(settings, 443, [], lambda call: FakeResponse(401, {"error": SYNTHETIC_TOKEN}))
        attestor = types.SimpleNamespace(ok=lambda kind: (True, "synthetic verified"))
        handler = self.run_handler("embed", attestor, upstream)
        handler.send_response.assert_called_once_with(401)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "forge-auth-rejected"})

    def test_model_mismatch_never_echoes_peer_values(self):
        settings = self.settings()
        calls = []
        upstreams = {kind: fake_upstream(settings, port, calls, lambda call: FakeResponse(200, {
                     "model_id": SYNTHETIC_TOKEN, "status": "ok", "model": SYNTHETIC_TOKEN}))
                     for kind, port in settings.ports.items()}
        attestor = accel.Attestor(settings, upstreams)
        with mock.patch.object(accel, "setting_on", return_value=True), self.assertLogs(accel.log, "WARNING") as logs:
            attestor.run_once()
        self.assertTrue(all("forge-model-mismatch" in attestor.ok(kind)[1] for kind in accel.ROUTES))
        self.assertNotIn(SYNTHETIC_TOKEN, str(logs.output) + json.dumps(attestor.snapshot()))

    def test_startup_reads_credential_before_confinement_without_unveiling_it(self):
        order = []
        service = types.SimpleNamespace(listeners={kind: object() for kind in accel.ROUTES})
        stub = accel.d32svc
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, settings_env(), clear=True))
            stack.enter_context(mock.patch.object(stub, "boot", return_value=service, create=True))
            stack.enter_context(mock.patch.object(stub, "base_unveils", return_value=[("/synthetic/base", "r")], create=True))
            confine = stack.enter_context(mock.patch.object(stub, "confine", side_effect=lambda *args: order.append("confine"),
                                                           create=True))
            stack.enter_context(mock.patch.object(accel, "load_forge_token",
                                                 side_effect=lambda path: order.append("credential") or SYNTHETIC_TOKEN))
            ca = stack.enter_context(mock.patch.object(accel.ssl, "create_default_context", return_value=mock.Mock()))
            stack.enter_context(mock.patch.object(accel, "Server"))
            stack.enter_context(mock.patch.object(accel.threading, "Thread"))
            stack.enter_context(mock.patch.object(accel.threading, "Event", return_value=mock.Mock(wait=lambda value: True)))
            stack.enter_context(mock.patch("signal.signal"))
            accel.main()
        self.assertEqual(order, ["credential", "confine"])
        self.assertNotIn(accel.FORGE_TOKEN_FILE, [path for path, perms in confine.call_args.args[1]])
        ca.assert_called_once_with(cafile="/etc/ssl/cert.pem")
        self.assertEqual(ca.return_value.minimum_version, accel.ssl.TLSVersion.TLSv1_2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=not bool(SCRATCH))
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch or SCRATCH
    unittest.main(argv=[sys.argv[0]] + remaining)
