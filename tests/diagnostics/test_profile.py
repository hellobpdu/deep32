"""Synthetic fixtures only; never inspect a user's actual profile or secrets."""
import builtins
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "diagnostics"))
from deep32_diag import profile as p

SYNTHETIC = ("a7" * 32 + "\n").encode("ascii")
CANARY = "SYNTHETIC_PRIVATE_EXCEPTION_CANARY"
USER = "S-1-5-21-123-456-789-1001"


def acl(path, fd=None):
    return p.WindowsACL(USER, "O:" + USER + "D:P(A;;FA;;;" + USER + ")")


@contextlib.contextmanager
def read_only(selected=None):
    """Deny mutation/process entry points, guard exact file opens and output."""
    original_open = os.open
    reads = []
    def guarded_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("write attempted")
        if selected is not None:
            Path(path).relative_to(selected)
        reads.append(Path(path))
        return original_open(path, flags, *args, **kwargs)
    def denied(*args, **kwargs):
        raise AssertionError("mutation or process attempted")
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(os, "open", guarded_open))
        for name in ("mkdir", "chmod", "rename", "replace", "remove", "unlink", "rmdir", "system"):
            stack.enter_context(mock.patch.object(os, name, denied))
        for name in ("run", "Popen", "call", "check_call", "check_output"):
            stack.enter_context(mock.patch.object(subprocess, name, denied))
        stack.enter_context(mock.patch.object(Path, "home", denied))
        stdout, stderr = io.StringIO(), io.StringIO()
        stack.enter_context(contextlib.redirect_stdout(stdout))
        stack.enter_context(contextlib.redirect_stderr(stderr))
        yield reads
        if stdout.getvalue() or stderr.getvalue():
            raise AssertionError("unexpected diagnostic output")


class ProfileTests(unittest.TestCase):
    def setUp(self):
        root = os.environ.get("DEEP32_DIAG_TEST_ROOT")
        if not root:
            raise RuntimeError("Set DEEP32_DIAG_TEST_ROOT to this suite's C: scratch")
        self.temp = tempfile.TemporaryDirectory(prefix="profile-test-", dir=root)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def fixture(self, agent="codex", windows=True):
        home = self.folder / (agent + "-selected")
        url = "https://workstation-demo.example.ts.net/mcp"
        root = home / "deep32-connect" / hashlib.sha256((url + "\n" + agent).encode("ascii")).hexdigest()[:16]
        credential = root / "private/received/gateway/demo-peer/mcp-token"
        credential.parent.mkdir(parents=True)
        credential.write_bytes(b"SYNTHETIC_DPAPI_CIPHERTEXT")
        ident = "0123456789abcdef" * 2
        state_path = root / "state.json"
        launcher = root / ("launch.ps1" if windows else "launch.sh")
        state = dict(schema="deep32.connector.v1", agent=agent, url=url, install_id=ident,
            server_name="deep32_alpha_01234567", variable="DEEP32_ALPHA_01234567_MCP_TOKEN",
            profile_dir=str(home), peer_id="demo-peer", credential=str(credential),
            executable=str(self.folder / (agent + ".exe")), version="synthetic agent 1.0", launcher=str(launcher))
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.write_config(home, state)
        runtime, connector = str(self.folder / "python.exe"), str(self.folder / "adapters/connect.py")
        if windows:
            quote = lambda value: "'" + value.replace("'", "''") + "'"
            text = "#requires -Version 5.1\n& " + quote(runtime) + " -I -B " + quote(connector) + " --launch " + quote(str(state_path)) + " @args\nexit $LASTEXITCODE\n"
        else:
            text = "#!/bin/sh\nexec " + shlex.quote(runtime) + " -I -B " + shlex.quote(connector) + " --launch " + shlex.quote(str(state_path)) + ' "$@"\n'
        launcher.write_bytes(text.encode("utf-8"))
        return home, root, state, state_path, credential, launcher

    def write_config(self, home, state):
        if state["agent"] == "codex":
            raw = "[mcp_servers." + state["server_name"] + "]\n" + "".join(key + " = " + json.dumps(value) + "\n" for key, value in
                {"url": state["url"], "bearer_token_env_var": state["variable"], "startup_timeout_sec": 30, "tool_timeout_sec": 120}.items())
            (home / "config.toml").write_text(raw, encoding="utf-8")
        else:
            value = {"type": "http", "url": state["url"], "headers": {"Authorization": "Bearer ${" + state["variable"] + "}"}}
            (home / ".claude.json").write_text(json.dumps({"mcpServers": {state["server_name"]: value}}), encoding="utf-8")

    def observe(self, home, agent="codex", **kwargs):
        return p.read_selected_profile(home, agent, windows=kwargs.pop("windows", True),
            acl_getter=kwargs.pop("acl_getter", acl), decrypt=kwargs.pop("decrypt", lambda raw: SYNTHETIC), **kwargs)

    def test_selected_codex_profile_denies_writes_processes_and_unrelated_homes(self):
        home, *_ = self.fixture()
        other = self.folder / "unrelated-profile"
        other.mkdir()
        (other / "sentinel").write_text(CANARY, encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(other), "CLAUDE_CONFIG_DIR": str(other)}), read_only(home) as reads:
            selected = self.observe(home)
        self.assertEqual(len(reads), 8)
        self.assertEqual(selected.agent, "codex")
        self.assertEqual(selected.credential.authorization(), "Bearer " + SYNTHETIC[:-1].decode("ascii"))
        self.assertNotIn(SYNTHETIC[:-1].decode(), repr(selected))
        self.assertNotIn(str(home), repr(selected))

    def test_selected_claude_config_is_inside_selected_profile(self):
        home, *_ = self.fixture("claude")
        with read_only(home) as reads:
            selected = self.observe(home, "claude")
        self.assertEqual(selected.agent, "claude")
        self.assertIn(home / ".claude.json", reads)

    def test_multiple_connector_states_refused_before_reading_any(self):
        home, root, *_ = self.fixture()
        (root.parent / ("f" * 16)).mkdir()
        with read_only(home) as reads, self.assertRaises(p.ProfileError) as caught:
            self.observe(home)
        self.assertEqual(caught.exception.code, "unsafe-file")
        self.assertEqual(reads, [])

    def test_no_credential_read_when_config_is_missing(self):
        home, _, _, _, credential, _ = self.fixture()
        (home / "config.toml").unlink()
        with read_only(home) as reads, self.assertRaises(p.ProfileError):
            self.observe(home)
        self.assertNotIn(credential, reads)

    def test_codex_owned_literal_token_or_extra_field_refused(self):
        for addition in ('bearer_token = "' + SYNTHETIC.decode().strip() + '"\n', 'unknown = true\n'):
            with self.subTest(addition=addition[:8]):
                home, _, state, *_ = self.fixture(agent="codex" if not (self.folder / "codex-selected").exists() else "claude")
                if state["agent"] == "claude":
                    state["agent"] = "codex"
                    # Test the pure owned-config seam, with no file reads.
                raw = ('[mcp_servers.' + state["server_name"] + ']\nurl = ' + json.dumps(state["url"]) + '\nbearer_token_env_var = ' + json.dumps(state["variable"]) + '\nstartup_timeout_sec = 30\ntool_timeout_sec = 120\n' + addition).encode()
                with self.assertRaises(p.ProfileError) as caught:
                    p._config(raw, state)
                self.assertEqual(caught.exception.code, "profile-config")

    def test_claude_literal_token_refused(self):
        home, _, state, *_ = self.fixture("claude")
        config = {"mcpServers": {state["server_name"]: {"type": "http", "url": state["url"],
            "headers": {"Authorization": "Bearer " + SYNTHETIC.decode().strip()}}}}
        (home / ".claude.json").write_text(json.dumps(config), encoding="utf-8")
        with read_only(home), self.assertRaises(p.ProfileError) as caught:
            self.observe(home, "claude")
        self.assertEqual(caught.exception.code, "profile-config")

    def test_wrong_profile_agent_url_hash_or_credential_slot_refused(self):
        home, _, state, state_path, *_ = self.fixture()
        for key, value in (("agent", "claude"), ("profile_dir", str(self.folder)),
                ("url", "https://other-demo.example.ts.net/mcp"), ("credential", str(self.folder / "foreign")),
                ("variable", "FOREIGN_MCP_TOKEN"), ("unknown", "data")):
            damaged = dict(state, **{key: value})
            state_path.write_text(json.dumps(damaged), encoding="utf-8")
            with self.subTest(key=key), read_only(home), self.assertRaises(p.ProfileError) as caught:
                self.observe(home)
            self.assertEqual(caught.exception.code, "profile-state")

    def test_duplicate_state_keys_rejected(self):
        home, _, _, state_path, *_ = self.fixture()
        state_path.write_bytes(b'{"agent":"codex","agent":"codex"}')
        with read_only(home), self.assertRaises(p.ProfileError) as caught:
            self.observe(home)
        self.assertEqual(caught.exception.code, "profile-state")

    def test_launcher_wrong_state_or_extra_command_refused(self):
        home, _, _, _, credential, launcher = self.fixture()
        original = launcher.read_bytes()
        for raw in (original + b"Write-Output 'extra'\n", original.replace(b"--launch", b"--other"), original.replace(b"@args", b"@args; exit 0")):
            launcher.write_bytes(raw)
            with read_only(home) as reads, self.assertRaises(p.ProfileError) as caught:
                self.observe(home)
            self.assertEqual(caught.exception.code, "profile-launcher")
            self.assertNotIn(credential, reads)

    def test_posix_launcher_exact_contract(self):
        home, _, _, state_path, _, launcher = self.fixture(windows=False)
        p._launcher(launcher.read_bytes(), state_path, False)
        with self.assertRaises(p.ProfileError):
            p._launcher(launcher.read_bytes().replace(b' "$@"', b' $(bad) "$@"'), state_path, False)

    def test_launcher_origin_requires_explicit_sealed_expected_paths(self):
        home, _, _, state_path, _, _ = self.fixture()
        origin = (str(self.folder / "python.exe"), str(self.folder / "adapters/connect.py"))
        self.assertFalse(self.observe(home).launcher_origin_confirmed)
        with read_only(home):
            selected = self.observe(home, launcher_origin=origin)
        self.assertTrue(selected.launcher_origin_confirmed)
        with read_only(home), self.assertRaises(p.ProfileError) as caught:
            self.observe(home, launcher_origin=(origin[0], str(self.folder / "other/connect.py")))
        self.assertEqual(caught.exception.code, "profile-launcher")

    def test_connector_enumeration_stops_after_two_members(self):
        home, root, *_ = self.fixture()
        original = os.scandir
        count = []
        def members(path):
            if Path(path) == root.parent:
                def generate():
                    for index in range(1000000):
                        count.append(index)
                        yield SimpleNamespace(name="%016x" % index)
                return contextlib.nullcontext(generate())
            return original(path)
        with mock.patch.object(os, "scandir", members), read_only(home), self.assertRaises(p.ProfileError):
            self.observe(home)
        self.assertEqual(len(count), 2)

    def test_credential_handle_does_not_repr_or_pickle_secret(self):
        value = p.Credential(SYNTHETIC)
        self.assertEqual(str(value), "<Credential redacted>")
        self.assertEqual(repr(value), "<Credential redacted>")
        with self.assertRaises(p.ProfileError):
            value.__reduce__()
        for raw in (b"", b"f" * 64, b"F" * 64 + b"\n", SYNTHETIC + b"\n", "secret"):
            with self.subTest(length=len(raw)), self.assertRaises(p.ProfileError):
                p.Credential(raw)

    def test_bad_acl_prevents_decrypt(self):
        home, *_ = self.fixture()
        decrypt = mock.Mock(return_value=SYNTHETIC)
        def unsafe(path, fd=None):
            return p.WindowsACL(USER, "O:" + USER + "D:P(A;;FA;;;WD)")
        with read_only(home), self.assertRaises(p.ProfileError) as caught:
            self.observe(home, acl_getter=unsafe, decrypt=decrypt)
        decrypt.assert_not_called()
        self.assertEqual(caught.exception.code, "credential-permissions")

    def test_currentuser_dpapi_failure_has_no_private_exception_context(self):
        home, *_ = self.fixture()
        def decrypt(raw):
            raise RuntimeError(CANARY + SYNTHETIC.decode())
        with read_only(home), self.assertRaises(p.ProfileError) as caught:
            self.observe(home, decrypt=decrypt)
        self.assertEqual(caught.exception.code, "credential-unavailable")
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(CANARY, repr(caught.exception))

    def test_whole_profile_snapshot_rejects_state_config_launcher_or_cipher_change(self):
        home, _, _, state_path, credential, launcher = self.fixture()
        targets = (state_path, home / "config.toml", launcher, credential)
        for target in targets:
            original = target.read_bytes()
            def decrypt(raw):
                target.write_bytes(original + b" ")
                return SYNTHETIC
            with self.subTest(slot=target.name), self.assertRaises(p.ProfileError) as caught:
                # This is a deliberately concurrent writer fixture. Normal
                # reader scopes below still deny writes initiated by the reader.
                self.observe(home, decrypt=decrypt)
            self.assertEqual(caught.exception.code, "snapshot-changed")
            self.assertIsNone(caught.exception.__context__)
            target.write_bytes(original)

    def test_whole_snapshot_compares_exact_bytes_even_when_time_and_size_restored(self):
        home, _, _, state_path, _, _ = self.fixture()
        info, original = state_path.stat(), state_path.read_bytes()
        def decrypt(raw):
            state_path.write_bytes(original.replace(b"synthetic", b"synthetix"))
            os.utime(state_path, ns=(info.st_atime_ns, info.st_mtime_ns))
            return SYNTHETIC
        with self.assertRaises(p.ProfileError) as caught:
            self.observe(home, decrypt=decrypt)
        self.assertEqual(caught.exception.code, "snapshot-changed")

    def test_whole_snapshot_detects_directory_membership_change(self):
        home, root, *_ = self.fixture()
        def decrypt(raw):
            (root.parent / ("f" * 16)).mkdir()
            return SYNTHETIC
        with self.assertRaises(p.ProfileError) as caught:
            self.observe(home, decrypt=decrypt)
        self.assertEqual(caught.exception.code, "snapshot-changed")

    def test_whole_snapshot_detects_safe_acl_change_and_keeps_marker_repr_private(self):
        home, _, _, _, credential, _ = self.fixture()
        count = []
        def getter(path, fd=None):
            if path == credential:
                count.append(1)
                if len(count) > 1:
                    return p.WindowsACL(USER, acl(path).sddl + "(A;;FA;;;SY)")
            return acl(path)
        with read_only(home), self.assertRaises(p.ProfileError) as caught:
            self.observe(home, acl_getter=getter)
        self.assertEqual(caught.exception.code, "snapshot-changed")
        marker = p._FileMarker(credential, SYNTHETIC, (), 65, None, None)
        self.assertEqual(repr(marker), "_FileMarker(<private>)")
        self.assertNotIn(SYNTHETIC.decode().strip(), repr(marker))

    def test_codex_timeout_float_cannot_masquerade_as_exact_owned_integer(self):
        home, _, state, *_ = self.fixture()
        path = home / "config.toml"
        path.write_bytes(path.read_bytes().replace(b"startup_timeout_sec = 30", b"startup_timeout_sec = 30.0"))
        with read_only(home), self.assertRaises(p.ProfileError) as caught:
            self.observe(home)
        self.assertEqual(caught.exception.code, "profile-config")

    def test_dpapi_output_must_have_enrolled_token_shape(self):
        home, *_ = self.fixture()
        with read_only(home), self.assertRaises(p.ProfileError) as caught:
            self.observe(home, decrypt=lambda raw: CANARY.encode())
        self.assertEqual(caught.exception.code, "credential-invalid")

    def test_windows_acl_owner_and_explicit_protection(self):
        good = acl(None)
        p.validate_windows_acl(good, protected=True)
        values = (p.WindowsACL("S-1-5-21-999", good.sddl),
                  p.WindowsACL(USER, good.sddl.replace("D:P", "D:")),
                  p.WindowsACL(USER, good.sddl.replace("FA", "FR")),
                  p.WindowsACL(USER, good.sddl + "(A;;FA;;;WD)"),
                  p.WindowsACL(USER, "O:" + USER + "D:P"))
        for value in values:
            with self.subTest(shape=len(value.sddl)), self.assertRaises(p.ProfileError):
                p.validate_windows_acl(value, protected=True)

    def test_posix_owner_and_modes_synthetic_metadata(self):
        for directory, mode in ((False, 0o600), (True, 0o700)):
            p.validate_posix_permissions(SimpleNamespace(st_uid=1000, st_mode=mode), 1000, directory=directory)
            for uid, bad_mode in ((1001, mode), (1000, mode | 0o004), (1000, mode | 0o020)):
                with self.subTest(directory=directory, uid=uid, mode=bad_mode), self.assertRaises(p.ProfileError):
                    p.validate_posix_permissions(SimpleNamespace(st_uid=uid, st_mode=bad_mode), 1000, directory=directory)

    def test_missing_state_never_makes_directories(self):
        target = self.folder / "missing"
        with read_only(self.folder), self.assertRaises(p.ProfileError):
            p.read_peer_records(target)
        self.assertFalse(target.exists())

    def test_hard_link_credential_refused(self):
        home, _, _, _, credential, _ = self.fixture()
        os.link(credential, self.folder / "hardlink")
        with read_only(home), self.assertRaises(p.ProfileError) as caught:
            self.observe(home)
        self.assertEqual(caught.exception.code, "unsafe-file")

    def test_growing_file_is_bounded(self):
        target = self.folder / "large"
        target.write_bytes(b"x" * 66)
        with read_only(self.folder), self.assertRaises(p.ProfileError) as caught:
            p._read(target, 65)
        self.assertEqual(caught.exception.code, "unsafe-file")

    def test_reparse_metadata_rejected_before_read(self):
        path = self.folder / "regular"
        path.write_bytes(b"synthetic")
        original = Path.lstat
        def reparse(selected):
            info = original(selected)
            if selected == path:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with mock.patch.object(Path, "lstat", reparse), read_only(self.folder) as reads, self.assertRaises(p.ProfileError):
            p._read(path)
        self.assertEqual(reads, [])

    def test_endpoint_unsafe_forms_refused(self):
        for value in ("http://demo/mcp", "https://user:secret@demo/mcp", "https://demo/mcp?x=1",
                      "https://demo/mcp#part", "https://demo:0/mcp", "https://demo:65536/mcp", "https://demo/mcp\n"):
            with self.subTest(value=value.split("?")[0]), self.assertRaises(p.ProfileError):
                p._endpoint(value)


class PureStateTests(unittest.TestCase):
    def test_workstation_c6_retains_only_safe_fields(self):
        raw = b"D32S/1 1 workstation boot=0123abcd stage=pairing state=waiting pair=ABCDEFGHIJ expires=20 pair_with=headend msg=SYNTHETIC_PRIVATE_EXCEPTION_CANARY\n"
        with read_only():
            value = p.parse_c6(raw)
        self.assertEqual(value, p.C6Observation("pairing", "waiting", None, None))
        for secret in ("ABCDEFGHIJ", "0123abcd", CANARY):
            self.assertNotIn(secret, repr(value))

    def test_c6_signin_is_validated_and_discarded(self):
        raw = b"D32S/1 99 workstation stage=signin state=waiting signin=https://login.tailscale.com/a-private-signin\n"
        self.assertEqual(p.parse_c6(raw).stage, "signin")
        self.assertNotIn("signin=", repr(p.parse_c6(raw)))

    def test_forge_c6_lowercase_error_dialect(self):
        raw = b"D32S/1 2 forge boot=0123abcd stage=firstboot state=failed error=forge-model-auth-denied msg=private raw diagnostic\n"
        value = p.parse_c6(raw, dialect="forge")
        self.assertEqual(value.error_kind, "forge-model-auth-denied")
        with self.assertRaises(p.ProfileError):
            p.parse_c6(raw, dialect="workstation")

    def test_unknown_valid_error_is_finite_category(self):
        value = p.parse_c6(b"D32S/1 2 workstation stage=ready state=failed error=SYNTHETIC_PRIVATE_CANARY\n")
        self.assertEqual(value.error_kind, "reported-failure")

    def test_c6_release_version_cannot_echo_arbitrary_private_text(self):
        base = b"D32S/1 2 workstation stage=ready state=done version="
        for version in (b"0.1.0-alpha.2", b"17.9", b"v1.2.3-rc.1"):
            self.assertEqual(p.parse_c6(base + version + b"\n").version, version.decode())
        with self.assertRaises(p.ProfileError):
            p.parse_c6(base + CANARY.encode() + b"\n")

    def test_workstation_c6_canonical_secret_shapes_are_rejected(self):
        base = b"D32S/1 2 workstation stage=ready state=done msg="
        for value in (b"a" * 32, b"tskey-synthetic", b"PRIVATE KEY", b"$2b$synthetic"):
            with self.subTest(length=len(value)), self.assertRaises(p.ProfileError):
                p.parse_c6(base + value + b"\n")

    def test_c6_duplicate_unknown_and_incoherent_fields_refused(self):
        raws = (b"D32S/1 1 workstation stage=ready stage=ready state=done\n",
            b"D32S/1 1 workstation stage=ready state=done unknown=x\n",
            b"D32S/1 1 workstation stage=ready state=failed\n",
            b"D32S/1 1 workstation stage=ready state=done error=FAILED\n",
            b"D32S/1 1 workstation stage=pairing state=waiting pair=ABCDEFGHIJ\n",
            b"D32S/1 1 workstation stage=ready state=done expires=20\n",
            b"D32S/1 1 workstation stage=ready state=done version=" + b"a" * 64 + b"\n",
            b"D32S/1 1 workstation stage=ready state=done\x1b\n",
            b"x" * 513)
        for raw in raws:
            with self.subTest(size=len(raw)), self.assertRaises(p.ProfileError) as caught:
                p.parse_c6(raw)
            self.assertEqual(caught.exception.code, "c6-invalid")

    def test_workstation_publication_only_observes_no_live_ownership(self):
        obj = dict(format="deep32-publication", version=1, desired=True, previous_front="off",
                   phase="ready", host="workstation-demo.example.ts.net", private_ignored=CANARY)
        with read_only():
            result = p.parse_publication(json.dumps(obj).encode(), role="workstation")
        self.assertEqual(result.owned_routes, 0)
        self.assertNotIn(CANARY, repr(result))

    def forge(self):
        routes = [dict(setting=name + "_https", port=port, kind="https", target="http://127.0.0.1:" + str(local))
                  for name, port, local in (("embeddings", 443, 8081), ("reranker", 8443, 8082))]
        routes.append(dict(setting="pairing_tcp", port=7453, kind="tcp", target="tcp://127.0.0.1:7453"))
        return dict(schema="deep32.forge-publication/1", install_id="a" * 16,
                    host="forge-demo.example.ts.net", routes=routes, added=[0, 1, 2], pending=None, removing=None, phase="ready")

    def test_forge_publication_validates_routes_without_recovery(self):
        with read_only():
            value = p.parse_publication(json.dumps(self.forge()).encode(), role="forge")
        self.assertEqual(value.owned_routes, 3)
        self.assertEqual(value.phase, "ready")

    def test_pending_removing_or_publishing_publication_fails_closed(self):
        for key, value in (("pending", 1), ("removing", 1), ("phase", "publishing"), ("phase", "removing")):
            obj = self.forge()
            obj[key] = value
            with self.subTest(key=key, value=value), read_only(), self.assertRaises(p.ProfileError) as caught:
                p.parse_publication(json.dumps(obj).encode(), role="forge")
            self.assertEqual(caught.exception.code, "publication-uncertain")

    def test_publication_route_drift_or_foreign_target_refused(self):
        for key, value in (("target", "http://foreign.invalid:8081"), ("port", True), ("setting", "foreign"), ("unknown", CANARY)):
            obj = self.forge()
            obj["routes"][0][key] = value
            with self.subTest(key=key), self.assertRaises(p.ProfileError):
                p.parse_publication(json.dumps(obj).encode(), role="forge")


class PeerTests(unittest.TestCase):
    setUp = ProfileTests.setUp
    def peer_fixture(self):
        root = self.folder / "pairing"
        path = root / "peers/gateway-joiner/demo-peer.json"
        path.parent.mkdir(parents=True)
        values = ["deep32-peer", 1, "gateway", "joiner", "demo-peer", "workstation", "a" * 64,
                  "b" * 64, "tailnet", "100.64.0.50", 7454, None, "workstation-demo.example.ts.net", "serve-https", 1, ["mcp-token"], []]
        obj = dict(zip(p._RECORD_KEYS, values))
        path.write_text(json.dumps(obj), encoding="utf-8")
        return root, path, obj

    def test_peer_read_calls_reviewed_loader_without_creation(self):
        root, path, obj = self.peer_fixture()
        loader = mock.Mock(return_value=obj)
        with read_only(root):
            result = p.read_peer_records(root, load_record=loader)
        loader.assert_called_once_with(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].received, ("mcp-token",))
        for secret in ("a" * 64, "100.64.0.50", "workstation-demo.example.ts.net"):
            self.assertNotIn(secret, repr(result))

    def test_peer_filename_directory_role_and_duplicate_guards(self):
        root, path, obj = self.peer_fixture()
        for key, value in (("peer_id", "other-peer"), ("kind", "forge"), ("peer_role", "unknown"), ("format_version", True), ("peer_pin", "invalid")):
            path.write_text(json.dumps(dict(obj, **{key: value})), encoding="utf-8")
            with self.subTest(key=key), read_only(root), self.assertRaises(p.ProfileError):
                p.read_peer_records(root)
        path.write_bytes(b'{"format":"deep32-peer","format":"deep32-peer"}')
        with read_only(root), self.assertRaises(p.ProfileError):
            p.read_peer_records(root)

    def test_peer_missing_peers_returns_empty_without_mkdir(self):
        root = self.folder / "existing-state"
        root.mkdir()
        with read_only(root):
            self.assertEqual(p.read_peer_records(root), ())
        self.assertFalse((root / "peers").exists())

    def test_loader_error_is_fixed_and_context_free(self):
        root, _, _ = self.peer_fixture()
        def loader(path):
            raise RuntimeError(CANARY)
        with read_only(root), self.assertRaises(p.ProfileError) as caught:
            p.read_peer_records(root, load_record=loader)
        self.assertEqual(caught.exception.code, "peer-invalid")
        self.assertIsNone(caught.exception.__context__)

    def test_peer_enumeration_stops_at_directory_and_record_limits(self):
        root, path, _ = self.peer_fixture()
        original = os.scandir
        for selected, limit in ((root / "peers", 7), (path.parent, 257)):
            count = []
            def members(parent):
                if Path(parent) == selected:
                    def generate():
                        for index in range(1000000):
                            count.append(index)
                            yield SimpleNamespace(name=str(index) + ".json")
                    return contextlib.nullcontext(generate())
                return original(parent)
            with self.subTest(limit=limit), mock.patch.object(os, "scandir", members), read_only(root), self.assertRaises(p.ProfileError):
                p.read_peer_records(root)
            self.assertEqual(len(count), limit)


if __name__ == "__main__":
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ProfileTests))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PureStateTests))
    for name in ("test_peer_read_calls_reviewed_loader_without_creation", "test_peer_filename_directory_role_and_duplicate_guards",
                 "test_peer_missing_peers_returns_empty_without_mkdir", "test_loader_error_is_fixed_and_context_free",
                 "test_peer_enumeration_stops_at_directory_and_record_limits"):
        suite.addTest(PeerTests(name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
