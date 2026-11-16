"""Synthetic disc 2 release-version and pinned-xorriso checks."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


DISC2 = Path(__file__).resolve().parents[1] / "disc2"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Disc2AssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assembly = load_module("disc2_test_assembly", DISC2 / "disc2_assemble.py")

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="disc2-assembly-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith("T0117_STUB_")}
        env.update({key: str(self.root) for key in ("TMP", "TEMP", "TMPDIR")})
        environment = patch.dict(os.environ, env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.config_path = self.root / "synthetic-config.json"
        self.config = self.make_config()

    def make_config(self):
        write, digest = self.assembly.write, self.assembly.digest
        counts = {"rfc": 365, "cisco": 489, "powershell": 241, "system": 85}
        blocks = []
        identities = []
        for name in sorted(counts):
            block = self.root / "blocks" / name
            block.mkdir(parents=True)
            write(block / "block.json", {"synthetic": name})
            blocks.append(str(block))
            identities.append({"name": name, "sha256": digest(block / "block.json")})
        bundle = self.root / "bundle"
        bundle.mkdir()
        write(bundle / "BUNDLE.json", {
            "blocks": identities,
            "counts": {"by_collection": {k: {"documents": v} for k, v in counts.items()}},
        })
        shelves = {}
        shelf_hashes = {}
        for role in ("cisco", "ietf", "encyclopaedia"):
            folder = self.root / "shelves" / role
            folder.mkdir(parents=True)
            (folder / "PARTS.tsv").write_bytes(b"synthetic shelf\n")
            shelves[role] = str(folder)
            shelf_hashes[role] = digest(folder / "PARTS.tsv")
        main = self.root / "main-packs"
        main.mkdir()
        (main / "PARTS.tsv").write_bytes(b"synthetic main stream\n")
        evidence = self.root / "synthetic-evidence.json"
        write(evidence, {"synthetic": True})
        approval = self.root / "synthetic-approval.json"
        write(approval, {
            "format": "deep32-library-assembly-approval/1", "fixture": True,
            "encyclopaedia": "ready", "cuts_reviewed": True, "main_program_name": "lib-e26",
            "shelves": shelf_hashes, "main_parts_sha256": digest(main / "PARTS.tsv"),
            "evidence": {role: {"path": str(evidence), "sha256": digest(evidence)} for role in
                         ("main_census", "native_zim_validation", "final_encyclopaedia_g1_g2")},
        })
        manifest = self.root / "synthetic-manifest.json"
        write(manifest, {"synthetic": True})
        cuts = self.root / "synthetic-cuts.txt"
        cuts.write_bytes(b"synthetic cuts\n")
        return {
            "format": self.assembly.FORMAT, "fixture": True,
            "tools": {role: "synthetic-tool" for role in ("disc", "libdata", "gates")},
            "bundle": str(bundle), "blocks": blocks, "main_packs": str(main),
            "shelves": shelves, "code": "synthetic-code", "ietf_manifest": None,
            "cisco_types": "synthetic-types", "release": "1.2.3", "disc_version": "1.2.3",
            "accepts_system": ["1.0.0"], "created": "2026-01-01T00:00:00Z",
            "signer_fingerprint": None, "verify_argv": [], "xorriso_argv": ["xorriso"],
            "time_form": "epoch", "corpus_manifest": str(manifest),
            "corpus_manifest_sha256": digest(manifest), "cut_keys": str(cuts),
            "cut_keys_sha256": digest(cuts), "markings": "synthetic-markings",
            "cut_count": 0, "scan_args": ["--g15-profile", "disc", "--marks-scope", "synthetic"],
            "root_files": {stem + ".txt": "synthetic-root-file" for stem in
                           ("THIRD-PARTY-NOTICES", "THIRD-PARTY-LICENSES", "LIBRARY-CREDITS", "SYSTEM-FETCH-LIST")},
            "library_approval": str(approval),
        }

    def read_config(self):
        self.assembly.write(self.config_path, self.config)
        return self.assembly.load(self.config_path)

    def release_config(self):
        self.config.update({"fixture": False, "signer_fingerprint": "A" * 40,
                            "verify_argv": ["synthetic-verifier", "{manifest}", "{sig}", "{fingerprint}"]})
        path = Path(self.config["library_approval"])
        approval = self.assembly.read(path)
        approval["fixture"] = False
        self.assembly.write(path, approval)

    def test_stable_versions_are_accepted(self):
        for version in ("0.0.0", "1.2.3", "123.456.789"):
            with self.subTest(version=version):
                self.config["release"] = version
                self.assertEqual(self.read_config()["release"], version)

    def test_pre_release_versions_are_accepted(self):
        for version in ("0.1.0-alpha.1", "1.2.3-RC9.beta3", "1.2.3-0"):
            with self.subTest(version=version):
                self.config["release"] = version
                self.assertEqual(self.read_config()["release"], version)

    def test_malformed_versions_are_rejected(self):
        for version in ("1.2", "1.2.3.4", "1.2.3-", "1.2.3-alpha.", "1.2.3-alpha..1",
                        "1.2.3-a_b", "1.2.3-alpha-1", "1.2.3+build", "1.2.3\n", "\u0661.2.3"):
            with self.subTest(version=version):
                self.config["release"] = version
                with self.assertRaisesRegex(ValueError, "three numeric components"):
                    self.read_config()

    def test_installed_xorriso_is_still_accepted_for_releases(self):
        self.release_config()
        self.assertEqual(self.read_config()["xorriso_argv"], ["xorriso"])

    def test_pinned_wrapper_is_accepted_for_releases(self):
        self.release_config()
        command = [sys.executable, "-I", "-B", str(DISC2 / "xorriso-win.py")]
        self.config["xorriso_argv"] = command
        self.assertEqual(self.read_config()["xorriso_argv"], command)

    def test_other_wrapper_commands_are_rejected_for_releases(self):
        self.release_config()
        wrapper = str(DISC2 / "xorriso-win.py")
        for command in ([sys.executable, "-B", "-I", wrapper],
                        [sys.executable, "-I", "-B", str(self.root / "other-wrapper.py")],
                        [sys.executable, "-I", "-B", wrapper, "extra"]):
            with self.subTest(command=command):
                self.config["xorriso_argv"] = command
                with self.assertRaisesRegex(ValueError, "installed xorriso"):
                    self.read_config()

    def test_modified_wrapper_is_rejected_for_releases(self):
        self.release_config()
        wrapper = self.root / "xorriso-win.py"
        shutil.copyfile(DISC2 / "xorriso-win.py", wrapper)
        wrapper.write_bytes(wrapper.read_bytes() + b"\n# synthetic alteration\n")
        self.config["xorriso_argv"] = [sys.executable, "-I", "-B", str(wrapper)]
        with patch.object(self.assembly, "__file__", str(self.root / "disc2_assemble.py")):
            with self.assertRaisesRegex(ValueError, "installed xorriso"):
                self.read_config()

    def test_fixture_xorriso_command_remains_configurable(self):
        self.config["xorriso_argv"] = ["synthetic-xorriso"]
        self.assertEqual(self.read_config()["xorriso_argv"], ["synthetic-xorriso"])


class XorrisoPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wrapper = load_module("disc2_test_xorriso", DISC2 / "xorriso-win.py")

    def test_drive_letter_paths_are_converted(self):
        for original, expected in (("F:/test/input", "/f/test/input"),
                                   (r"C:\test\input", "/c/test/input"),
                                   ("iso_path=Z:/test/image.iso", "iso_path=/z/test/image.iso")):
            with self.subTest(original=original):
                self.assertEqual(self.wrapper.convert(original), expected)

    def test_other_arguments_are_preserved(self):
        for argument in ("-as", "mkisofs", "/srv/input", "relative/file", "time=2026010100000000"):
            with self.subTest(argument=argument):
                self.assertEqual(self.wrapper.convert(argument), argument)


if __name__ == "__main__":
    unittest.main()
