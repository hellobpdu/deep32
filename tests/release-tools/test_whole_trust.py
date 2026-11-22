"""Offline whole-image trust/coverage tests; all keys and GPG responses are fake."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[2] / "src" / "release-tools"
spec = importlib.util.spec_from_file_location("whole_trust", TOOLS / "deep32_get_manifest.py")
trust = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trust)
FINGERPRINT = "A" * 40


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("ascii")


class FakeGpg:
    """Small subprocess seam: no GPG executable or real keyring is opened."""
    def __init__(self, bad_root=False, wrong_signer=False, secret=False, text_checksums=()):
        self.calls = []
        self.bad_root = bad_root
        self.wrong_signer = wrong_signer
        self.secret = secret
        self.text_checksums = set(text_checksums)

    def popen(self, argv, **kwargs):
        self.calls.append([str(x) for x in argv])
        assert "--no-options" in argv and "--no-autostart" in argv
        assert "--no-auto-key-retrieve" in argv and "--auto-key-locate" in argv
        assert kwargs["env"]["GNUPGHOME"] == argv[argv.index("--homedir") + 1]
        if "--show-keys" in argv:
            raw = (("sec" if self.secret else "pub") + ":\nfpr:::::::::" + FINGERPRINT + ":\n").encode("ascii")
            code = 0
        elif "--import" in argv:
            raw, code = ("[GNUPG:] IMPORT_OK 1 " + FINGERPRINT + "\n").encode("ascii"), 0
        else:
            fingerprint = "B" * 40 if self.wrong_signer else FINGERPRINT
            signature_class = "01" if Path(argv[-1]).name in self.text_checksums else "00"
            raw = ("[GNUPG:] VALIDSIG " + fingerprint + " 2000-01-01 0 0 4 0 1 10 " + signature_class + "\n").encode("ascii")
            code = 1 if self.bad_root and Path(argv[-1]).name == "SHA256SUMS" else 0
        child = mock.MagicMock()
        child.returncode = code
        child.communicate.return_value = (raw, b"")
        child.__enter__.return_value = child
        return child


class WholeTrust(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="trust-", dir=SCRATCH)
        self.stage = Path(self.temporary.name)
        self.folder = self.stage / "github"
        self.folder.mkdir()
        self.images = self.stage / "images"
        self.images.mkdir()
        self.data = json.loads((TOOLS / "contracts/fixtures/valid/mirrors.json").read_bytes())
        self.sums = {}
        self.signatures = {"SHA256SUMS.sig"}
        self.children = []
        for disc in self.data["discs"]:
            raw = ("synthetic whole image " + disc["role"] + "\n").encode("ascii")
            disc.update(size=len(raw), sha256=digest(raw), chunk_sha256=[digest(raw)])
            (self.images / disc["file"]).write_bytes(raw)
            self.sums[disc["file"]] = digest(raw)
            prefix = disc["file"].rsplit(".", 1)[0]
            readme = prefix + "-README.md"
            (self.folder / readme).write_bytes(b"Synthetic README.\n")
            child_name = prefix + "-SHA256SUMS"
            child = {disc["file"]: disc["sha256"], readme: digest(b"Synthetic README.\n")}
            (self.folder / child_name).write_bytes(self.sums_bytes(child))
            self.children.append(child_name)
            self.signatures.add(child_name + ".sig")
        (self.folder / "common.spdx.json").write_bytes(b'{"synthetic":"common SBOM"}\n')
        (self.folder / "ASSET-MAP.json").write_bytes(canonical({"format": "deep32-asset-map", "format_version": 1,
             "release": "v0.1.0-alpha.3", "entries": [{"asset": "common.spdx.json", "install": [], "media_paths": []}]}))
        self.write_index()
        for name in self.signatures:
            (self.folder / name).write_bytes(b"synthetic detached signature\n")
        self.key = self.stage / "synthetic-public.gpg"
        self.key.write_bytes(b"synthetic public-key input; not an OpenPGP key\n")
        self.reseal()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def sums_bytes(sums):
        return "".join(digest + "  " + name + "\n" for name, digest in sorted(sums.items())).encode("ascii")

    def write_index(self):
        (self.folder / "MIRRORS.json").write_bytes(canonical(self.data))

    def reseal(self):
        for item in self.folder.iterdir():
            if item.name not in self.signatures | {"SHA256SUMS", "ASSETS.txt"}:
                self.sums[item.name] = digest(item.read_bytes())
        uploads = (set(self.sums) - {d["file"] for d in self.data["discs"]}) | {"ASSETS.txt", "SHA256SUMS"} | self.signatures
        raw = ("\n".join(sorted(uploads)) + "\n").encode("ascii")
        (self.folder / "ASSETS.txt").write_bytes(raw)
        self.sums["ASSETS.txt"] = digest(raw)
        (self.folder / "SHA256SUMS").write_bytes(self.sums_bytes(self.sums))

    def validate(self, **kwargs):
        return trust.validate_whole_release(self.folder, **kwargs)

    def refuse(self, **kwargs):
        with self.assertRaises((trust.Refusal, ValueError, OSError)):
            self.validate(**kwargs)

    def test_metadata_and_whole_scopes(self):
        self.assertEqual(self.validate()["release"], "v0.1.0-alpha.3")
        self.validate(scope="whole")
        self.validate(scope="whole", images={d["role"]: self.images / d["file"] for d in self.data["discs"]})

    def test_select_whole_and_release_binding(self):
        selected = trust.select_whole(self.folder / "MIRRORS.json", self.folder / "SHA256SUMS", "disc2", "v0.1.0-alpha.3")
        self.assertEqual(selected["role"], "disc2")
        self.refuse(release="v0.1.0-alpha.1")

    def test_root_authenticated_before_index_or_hash_use(self):
        fake = FakeGpg()
        original = trust.read_sums
        def read_sums(source):
            self.assertTrue(any("--verify" in call and Path(call[-1]).name == "SHA256SUMS" for call in fake.calls))
            return original(source)
        with mock.patch.object(trust, "gpg_executable", return_value=self.stage / "fake-gpg.exe"), \
             mock.patch.object(trust.subprocess, "Popen", side_effect=fake.popen), \
             mock.patch.object(trust, "read_sums", side_effect=read_sums), contextlib.redirect_stdout(io.StringIO()) as output:
            trust.verify_whole([str(self.folder), FINGERPRINT, "--key", str(self.key), "--scope", "metadata"])
        verified = [Path(c[-1]).name for c in fake.calls if "--verify" in c]
        self.assertEqual(verified, ["SHA256SUMS", *[d["file"].rsplit(".", 1)[0] + "-SHA256SUMS" for d in self.data["discs"]]])
        self.assertIn("image bytes were not checked", output.getvalue())
        self.assertFalse(list(self.folder.glob(".deep32-public-key-*")))

    def test_failed_or_wrong_signature_never_parses_root(self):
        for options in ({"bad_root": True}, {"wrong_signer": True}, {"secret": True}):
            with self.subTest(options=options), mock.patch.object(trust, "gpg_executable", return_value=self.stage / "fake-gpg.exe"), \
                 mock.patch.object(trust.subprocess, "Popen", side_effect=FakeGpg(**options).popen), \
                 mock.patch.object(trust, "read_sums", side_effect=AssertionError("parsed unauthenticated root")):
                with self.assertRaises(trust.Refusal):
                    trust.verify_whole([str(self.folder), FINGERPRINT, "--key", str(self.key), "--scope", "metadata"])

    def test_text_signature_refused_for_root_and_every_child_in_both_scopes(self):
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        for scope in ("metadata", "whole"):
            arguments = [str(self.folder), FINGERPRINT, "--key", str(self.key), "--scope", scope]
            if scope == "whole":
                for disc in self.data["discs"]:
                    arguments += ["--image", disc["role"] + "=" + str(self.images / disc["file"])]
            with mock.patch.object(trust, "gpg_executable", return_value=self.stage / "fake-gpg.exe"), \
                 mock.patch.object(trust.subprocess, "Popen", side_effect=FakeGpg().popen), \
                 contextlib.redirect_stdout(io.StringIO()):
                trust.verify_whole(arguments)
            for checksum in ("SHA256SUMS", *self.children):
                fake = FakeGpg(text_checksums=[checksum])
                with self.subTest(scope=scope, checksum=checksum), \
                     mock.patch.object(trust, "gpg_executable", return_value=self.stage / "fake-gpg.exe"), \
                     mock.patch.object(trust.subprocess, "Popen", side_effect=fake.popen), \
                     self.assertRaisesRegex(trust.Refusal, "class 00"):
                    trust.verify_whole(arguments)
                self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.iterdir()})

    def test_raw_mirrors_hash_precedes_json_parser(self):
        (self.folder / "MIRRORS.json").write_bytes(b"not JSON; untrusted URL must never be inspected")
        with mock.patch.object(trust, "contract_module", side_effect=AssertionError("parsed unauthenticated index")):
            self.refuse()

    def test_metadata_stream_read_is_bounded_despite_growth(self):
        opened = mock.MagicMock()
        opened.read.return_value = b"x" * (trust.MAX_META + 1)
        source = mock.MagicMock()
        source.stat.return_value.st_size = 1
        source.open.return_value.__enter__.return_value = opened
        with mock.patch.object(trust, "path", return_value=source):
            with self.assertRaises(trust.Refusal):
                trust.read_metadata("synthetic-growing-metadata")
        opened.read.assert_called_once_with(trust.MAX_META + 1)

    def test_strict_index_cases(self):
        original = copy.deepcopy(self.data)
        mutations = [lambda d: d.update(extra=True), lambda d: d.update(revision=True),
                     lambda d: d.update(published_at="2000-02-30T00:00:00Z"),
                     lambda d: d["discs"][0].update(size=1.0),
                     lambda d: d["discs"][0].update(chunk_sha256=[]),
                     lambda d: d["discs"][1].update(role="disc1"),
                     lambda d: d["discs"][0]["mirrors"][0].update(url="https://archive.org/private?token=x"),
                     lambda d: d["discs"][0]["mirrors"][1].update(url="https://127.0.0.1/image"),
                     lambda d: d["discs"][0].update(sha256="0" * 64)]
        for index, change in enumerate(mutations):
            with self.subTest(index=index):
                self.data = copy.deepcopy(original)
                change(self.data)
                self.write_index()
                self.reseal()
                self.refuse()

    def test_duplicate_json_key(self):
        raw = (self.folder / "MIRRORS.json").read_bytes().replace(b'"revision": 1', b'"revision": 1, "revision": 1')
        (self.folder / "MIRRORS.json").write_bytes(raw)
        self.reseal()
        self.refuse()

    def test_unsigned_small_file(self):
        (self.folder / "unsigned.txt").write_bytes(b"unlisted\n")
        self.refuse()

    def test_missing_signed_small_file(self):
        (self.folder / "ASSET-MAP.json").unlink()
        self.refuse()

    def test_tampered_small_file(self):
        (self.folder / "ASSET-MAP.json").write_bytes(b"{}\n")
        self.refuse()

    def test_unlisted_missing_image_is_not_excused(self):
        self.sums["unlisted.iso"] = "0" * 64
        self.reseal()
        self.refuse()

    def test_extra_signature(self):
        (self.folder / "unexpected.sig").write_bytes(b"fake\n")
        self.refuse()

    def test_metadata_scope_refuses_payload_in_upload_folder(self):
        source = self.images / self.data["discs"][0]["file"]
        shutil.copyfile(source, self.folder / source.name)
        self.refuse()

    def test_assets_exact_coverage(self):
        path = self.folder / "ASSETS.txt"
        for raw in (b"SHA256SUMS\n", path.read_bytes() + b"unexpected.sig\n", path.read_bytes().replace(b"\n", b"\r\n")):
            path.write_bytes(raw)
            self.sums["ASSETS.txt"] = digest(raw)
            (self.folder / "SHA256SUMS").write_bytes(self.sums_bytes(self.sums))
            self.refuse()

    def test_child_image_hash_and_coverage(self):
        child = self.folder / self.children[0]
        original = trust.read_sums(child)
        for values in ({}, {key: "0" * 64 for key in original}, {**original, self.data["discs"][1]["file"]: self.data["discs"][1]["sha256"]}):
            child.write_bytes(self.sums_bytes(values))
            self.reseal()
            self.refuse()

    def test_mapped_explicit_sidecar(self):
        name = "disc1.spdx.json"
        (self.folder / name).write_bytes(b'{"synthetic":"SBOM"}\n')
        child = self.folder / self.children[0]
        values = trust.read_sums(child)
        values[name] = digest((self.folder / name).read_bytes())
        child.write_bytes(self.sums_bytes(values))
        self.reseal()
        self.refuse()
        mapped = json.loads((self.folder / "ASSET-MAP.json").read_bytes())
        mapped["entries"] = [{"asset": name, "media_paths": [], "install": []}]
        (self.folder / "ASSET-MAP.json").write_bytes(canonical(mapped))
        self.reseal()
        self.validate()

    def test_root_case_collision_and_signature_exclusion(self):
        original = (self.folder / "SHA256SUMS").read_bytes()
        for raw in (original + ("0" * 64 + "  mirrors.json\n").encode("ascii"), original + ("0" * 64 + "  SHA256SUMS.sig\n").encode("ascii")):
            (self.folder / "SHA256SUMS").write_bytes(raw)
            self.refuse()

    def test_whole_image_tamper_and_missing(self):
        source = self.images / self.data["discs"][0]["file"]
        source.write_bytes(b"tampered image\n")
        self.refuse(scope="whole")
        source.unlink()
        self.refuse(scope="whole")
        self.validate(scope="metadata")

    def test_duplicate_and_incomplete_image_mapping(self):
        sources = {d["role"]: self.images / d["file"] for d in self.data["discs"]}
        self.refuse(scope="whole", images={"disc1": sources["disc1"]})
        sources["disc2"] = sources["disc1"]
        self.refuse(scope="whole", images=sources)

    def test_signing_preflight_checks_all_metadata(self):
        for name in self.signatures:
            (self.folder / name).unlink()
        # The legacy structural reader still checks closure without signatures;
        # the actual alpha.2 signing entry point requires the new bundle form.
        self.validate(require_signatures=False)
        with mock.patch.object(trust.subprocess, "Popen") as process:
            with self.assertRaisesRegex(trust.Refusal, "mirrors version 2"):
                trust.verify_whole(["--release-dir", str(self.folder)], signing=True)
            process.assert_not_called()
        (self.folder / "unsigned.txt").write_bytes(b"unsigned\n")
        self.refuse(require_signatures=False)

    def test_preflight_refuses_existing_signature(self):
        self.refuse(require_signatures=False)

    def test_sign_script_dry_run_and_no_gpg_on_bad_metadata(self):
        for name in self.signatures:
            (self.folder / name).unlink()
        bash = os.environ.get("D32_GIT_BASH", "C:/Program Files/Git/bin/bash.exe")
        environment = os.environ.copy()
        environment["DEEP32_PYTHON"] = sys.executable.replace("\\", "/")
        fake_bin = self.stage / "synthetic-bin"
        fake_bin.mkdir()
        sentinel = self.stage / "gpg-was-invoked"
        fake_gpg = fake_bin / "gpg"
        fake_gpg.write_bytes(b'#!/usr/bin/env bash\n: > "$D32_GPG_SENTINEL"\nexit 98\n')
        fake_gpg.chmod(0o755)
        environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
        environment["D32_GPG_SENTINEL"] = str(sentinel).replace("\\", "/")
        command = [bash, "--noprofile", "--norc", str(TOOLS / "sign-release.sh").replace("\\", "/"),
                   "--release-dir", str(self.folder).replace("\\", "/"), "--key-id", "synthetic-operator-key", "--dry-run"]
        result = subprocess.run(command, capture_output=True, text=True, env=environment)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mirrors version 2", result.stdout + result.stderr)
        self.assertNotIn("Checksum files to sign", result.stdout)
        self.assertFalse(sentinel.exists(), "GPG was invoked for refused legacy metadata")
        self.assertFalse(list(self.folder.glob("*.sig")))
        (self.folder / "unsigned.txt").write_bytes(b"unlisted\n")
        result = subprocess.run(command, capture_output=True, text=True, env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Checksum files to sign", result.stdout)
        self.assertFalse(sentinel.exists(), "GPG was invoked before metadata refusal")

    def wrapper_commands(self, tools=TOOLS, scope="metadata"):
        bash = os.environ.get("D32_GIT_BASH", "C:/Program Files/Git/bin/bash.exe")
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        return {
            "bash": [bash, "--noprofile", "--norc", str(tools / "verify-release.sh").replace("\\", "/"),
                     str(self.folder).replace("\\", "/"), FINGERPRINT, "--scope", scope, "--key", str(self.key).replace("\\", "/")],
            "powershell": [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                           str(tools / "verify-release.ps1"), "-ReleaseDirectory", str(self.folder),
                           "-Fingerprint", FINGERPRINT, "-Scope", scope, "-Key", str(self.key)]}

    def test_both_native_wrappers_refuse_unsigned_bad_index_before_parsing(self):
        (self.folder / "SHA256SUMS.sig").unlink()
        (self.folder / "MIRRORS.json").write_bytes(b"malformed JSON with untrusted URLs; must not be parsed")
        environment = os.environ.copy()
        environment["DEEP32_PYTHON"] = str(sys.executable).replace("\\", "/")
        for shell, command in self.wrapper_commands().items():
            with self.subTest(shell=shell):
                result = subprocess.run(command, capture_output=True, text=True, env=environment)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("SHA256SUMS.sig", result.stdout + result.stderr)
                self.assertNotIn("invalid JSON", result.stdout + result.stderr)
                self.assertNotIn("contract schema", result.stdout + result.stderr)
                self.assertNotIn("GnuPG was not found", result.stdout + result.stderr)

    def test_both_native_wrappers_forward_whole_scope(self):
        tools = self.stage / "synthetic-wrapper-tools"
        tools.mkdir()
        for name in ("verify-release.sh", "verify-release.ps1", "deep32-get.ps1"):
            shutil.copyfile(TOOLS / name, tools / name)
        (tools / "deep32_get_manifest.py").write_bytes(b"import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
        environment = os.environ.copy()
        environment["DEEP32_PYTHON"] = str(sys.executable).replace("\\", "/")
        for shell, command in self.wrapper_commands(tools, "whole").items():
            with self.subTest(shell=shell):
                if shell == "bash":
                    command += ["--image", "disc1=" + str(self.images / self.data["discs"][0]["file"]).replace("\\", "/")]
                else:
                    command += ["-ImagePath", "disc1=" + str(self.images / self.data["discs"][0]["file"])]
                result = subprocess.run(command, capture_output=True, text=True, env=environment)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                forwarded = json.loads(result.stdout)
                self.assertEqual(forwarded[0], "verify-whole")
                self.assertEqual(forwarded[forwarded.index("--scope") + 1], "whole")
                self.assertTrue(forwarded[forwarded.index("--image") + 1].startswith("disc1="))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch-root", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch_root
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if any(SCRATCH.iterdir()):
        parser.error("scratch root must be empty")
    unittest.main(argv=[sys.argv[0], *remaining])
