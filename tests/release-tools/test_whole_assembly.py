#!/usr/bin/env python3
"""Synthetic, offline whole-image assembly checks; no release signing."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "src" / "release-tools"
sys.path.insert(0, str(TOOLS))
import release_assemble as assembly
from contract_validate import canonical, ContractError, strict_load, mirror_transports, mirror_child_names


class WholeAssembly(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="whole-assembly-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.out = self.root / "release"
        self.mirrors = strict_load(TOOLS / "contracts" / "fixtures" / "valid" / "mirrors-v2.json")
        self.rows, self.producers, self.sources = [], [], {}
        argv = ["--format", "whole", "--version", "0.1.0-alpha.3",
                "--output", str(self.out), "--mirrors-input", str(self.inputs / "mirrors.json"),
                "--metadata-list", str(self.inputs / "metadata.json"),
                "--bundle-inputs", str(self.inputs / "bundles.json")]
        for disc in mirror_transports(self.mirrors):
            role = disc.get("role", disc.get("bundle_id"))
            data = (("INVENTED TEST IMAGE " if "role" in disc else "INVENTED TEST BUNDLE ") + role + "\n").encode("ascii")
            image = self.inputs / (role + Path(disc["file"]).suffix)
            image.write_bytes(data)
            sums = self.inputs / (role + ".sums")
            sums.write_text(hashlib.sha256(data).hexdigest() + "  " + image.name + "\n", encoding="ascii")
            self.sources[role] = image
            if "role" in disc:
                argv.extend(["--" + role, str(image), "--" + role + "-sums", str(sums)])
            else:
                self.producers.append({"bundle_id": role, "source": image.name, "sums": sums.name})
        for role, child in mirror_child_names(self.mirrors).items():
            self.add_asset(child.removesuffix("-SHA256SUMS") + "-README.md", b"Synthetic disc inventory.\n", [role])
        cap_name = self.mirrors["capability_manifest"]["file"]
        self.add_asset(cap_name, (TOOLS / "contracts/fixtures/capabilities/manifest.json").read_bytes(), [])
        self.add_asset("invented.spdx.json", b'{"synthetic":true}\n', ["disc1", "disc3"])
        self.asset_map = {"format": "deep32-asset-map", "format_version": 1,
                          "release": self.mirrors["release"], "entries": [
                              {"asset": "invented.spdx.json", "media_paths": [], "install": []},
                              {"asset": cap_name, "media_paths": [], "install": []}]}
        self.add_asset("ASSET-MAP.json", canonical(self.asset_map), [])
        self.args = assembly.make_parser().parse_args(argv)
        self.sync()

    def add_asset(self, name, data, roles):
        (self.inputs / name).write_bytes(data)
        self.rows.append({"file": name, "source": name, "discs": roles})

    def sync(self):
        (self.inputs / "mirrors.json").write_bytes(canonical(self.mirrors))
        (self.inputs / "metadata.json").write_bytes(canonical({
            "format": "deep32-release-metadata-list", "format_version": 1, "assets": self.rows}))
        (self.inputs / "bundles.json").write_bytes(canonical({
            "format": "deep32-release-bundle-inputs", "format_version": 1, "bundles": self.producers}))

    def refused(self):
        self.sync()
        with self.assertRaises((assembly.Refusal, ValueError, OSError)):
            assembly.assemble(self.args)
        self.assertFalse(self.out.exists())

    def test_exact_metadata_and_one_inode(self):
        self.assertEqual(assembly.assemble(self.args), 0)
        github = self.out / "github"
        sums = dict(line.split("  ", 1)[::-1] for line in (github / "SHA256SUMS").read_text().splitlines())
        for disc in mirror_transports(self.mirrors):
            linked = self.out / "images" / disc["file"]
            original = self.sources[disc.get("role", disc.get("bundle_id"))]
            self.assertTrue(os.path.samefile(linked, original))
            self.assertEqual(sums[linked.name], assembly.sha256(linked))
            self.assertFalse((github / linked.name).exists())
            child = github / mirror_child_names(self.mirrors)[disc.get("role", "disc3")]
            self.assertIn(disc["file"], child.read_text())
        actual = {p.name for p in github.iterdir()}
        assets = set((github / "ASSETS.txt").read_text().splitlines())
        expected_sigs = {"SHA256SUMS.sig", *(name + ".sig" for name in mirror_child_names(self.mirrors).values())}
        self.assertEqual(assets, actual | expected_sigs)
        self.assertEqual(set(sums), (actual - {"SHA256SUMS"}) | {d["file"] for d in mirror_transports(self.mirrors)})
        self.assertFalse(any(".part" in p.name or ".joining" in p.name for p in self.out.rglob("*")))
        self.assertFalse(any(str(self.inputs).encode() in p.read_bytes() for p in github.iterdir()))

    def test_byte_determinism_and_no_source_dependency(self):
        assembly.assemble(self.args)
        first = {p.name: p.read_bytes() for p in (self.out / "github").iterdir()}
        self.args.output = str(self.root / "again")
        self.rows.reverse()
        self.sync()
        assembly.assemble(self.args)
        second = {p.name: p.read_bytes() for p in (self.root / "again" / "github").iterdir()}
        self.assertEqual(first, second)

    def test_default_alpha2_is_whole(self):
        self.args.format = None
        assembly.assemble(self.args)
        self.assertTrue((self.out / "images").is_dir())

    def test_isolated_cli(self):
        argv = [sys.executable, "-I", "-B", str(TOOLS / "release_assemble.py"),
                "--format", "whole", "--version", "0.1.0-alpha.3", "--output", str(self.out),
                "--mirrors-input", str(self.inputs / "mirrors.json"),
                "--metadata-list", str(self.inputs / "metadata.json"),
                "--bundle-inputs", str(self.inputs / "bundles.json")]
        for role, _ in assembly.ROLES[:2]:
            argv.extend(["--" + role, getattr(self.args, role),
                         "--" + role + "-sums", getattr(self.args, role + "_sums")])
        result = subprocess.run(argv, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

    def test_unsigned_whole_preflight(self):
        assembly.assemble(self.args)
        result = subprocess.run([sys.executable, "-I", "-B", str(TOOLS / "deep32_get_manifest.py"),
                                 "sign-preflight", "--release-dir", str(self.out / "github"),
                                 "--scope", "whole"], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

    def test_unsigned_small_file_refused(self):
        assembly.assemble(self.args)
        (self.out / "github" / "unlisted.txt").write_bytes(b"Synthetic unlisted metadata\n")
        result = subprocess.run([sys.executable, "-I", "-B", str(TOOLS / "deep32_get_manifest.py"),
                                 "sign-preflight", "--release-dir", str(self.out / "github")],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 1)

    def test_missing_image(self):
        self.args.disc2 = None
        self.refused()

    def test_pending_refused(self):
        self.args.pending = ["disc2"]
        self.refused()

    def test_duplicate_image(self):
        self.args.disc2, self.args.disc2_sums = self.args.disc1, self.args.disc1_sums
        self.refused()

    def test_producer_hash_changed(self):
        self.sources["core"].write_bytes(b"changed")
        self.refused()

    def test_bad_descriptor_chunk(self):
        self.mirrors["discs"][1]["chunk_sha256"][0] = "0" * 64
        self.refused()

    def test_bad_descriptor_whole(self):
        self.mirrors["discs"][1]["sha256"] = "0" * 64
        self.refused()

    def test_unsafe_url(self):
        self.mirrors["discs"][0]["mirrors"][1]["url"] = "https://127.0.0.1/private"
        self.refused()

    def test_duplicate_metadata(self):
        self.rows.append(copy.deepcopy(self.rows[0]))
        self.refused()

    def test_case_collision(self):
        self.add_asset("INVENTED.spdx.json", b"different", [])
        self.refused()

    def test_unsafe_name(self):
        self.rows[0]["file"] = "../unsafe"
        self.refused()

    def test_reserved_name(self):
        self.rows[0]["file"] = "MIRRORS.json"
        self.refused()

    def test_unknown_metadata_field(self):
        self.rows[0]["unreviewed"] = True
        self.refused()

    def test_missing_asset_map(self):
        self.rows = [r for r in self.rows if r["file"] != "ASSET-MAP.json"]
        self.refused()

    def test_unmapped_sidecar(self):
        self.add_asset("extra.spdx.json", b"{}", ["disc2"])
        self.refused()

    def test_missing_child_readme(self):
        self.rows[0]["discs"] = []
        self.refused()

    def test_existing_output_refused_untouched(self):
        self.out.mkdir()
        marker = self.out / "keep"
        marker.write_bytes(b"user data")
        with self.assertRaises(assembly.Refusal):
            assembly.assemble(self.args)
        self.assertEqual(marker.read_bytes(), b"user data")

    def test_link_failure_cleans_only_owned_stage(self):
        with mock.patch.object(assembly.os, "link", side_effect=OSError("no hard links")):
            self.refused()
        self.assertTrue(Path(self.args.disc1).exists())

    def test_staged_mutation_readback_refused(self):
        original = assembly.measure_whole
        def measure(path):
            result = original(path)
            if Path(path).parent.name == "images":
                result["sha256"] = "0" * 64
            return result
        with mock.patch.object(assembly, "measure_whole", side_effect=measure):
            self.refused()

    def test_legacy_format_cannot_split_alpha2(self):
        self.args.format = "legacy-split"
        self.refused()

    def test_duplicate_json_keys(self):
        (self.inputs / "metadata.json").write_bytes(b'{"format":1,"format":2}')
        with self.assertRaises(ValueError):
            assembly.assemble(self.args)
        self.assertFalse(self.out.exists())

    def test_chunk_stream_boundaries(self):
        path = self.inputs / "chunk-test.bin"
        block = b"z" * (1024 * 1024)
        with path.open("wb") as stream:
            for _ in range(64):
                stream.write(block)
            stream.write(b"tail")
        result = assembly.measure_whole(path)
        first = hashlib.sha256()
        for _ in range(64):
            first.update(block)
        self.assertEqual(result["size"], 67108868)
        self.assertEqual(result["chunk_sha256"], [first.hexdigest(), hashlib.sha256(b"tail").hexdigest()])
        self.assertEqual(result["sha256"], assembly.sha256(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
