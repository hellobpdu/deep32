#!/usr/bin/env python3
"""Offline synthetic shared-core transport and staging checks."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "src" / "release-tools"
sys.path.insert(0, str(TOOLS))
import release_assemble as assembly
import contract_validate as contracts
import deep32_get_manifest as getter


class BundleAssembly(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bundle-assembly-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.output = self.root / "release"
        fixtures = TOOLS / "contracts" / "fixtures"
        self.mirrors = contracts.strict_load(fixtures / "valid" / "mirrors-v2.json")
        self.capraw = (fixtures / "capabilities" / "manifest.json").read_bytes()
        self.capability = contracts.strict_loads(self.capraw)
        self.rows, self.producers, self.sources = [], [], {}
        argv = ["--format", "whole", "--version", "0.1.0-alpha.2", "--output", str(self.output),
                "--mirrors-input", str(self.inputs / "mirrors.json"),
                "--metadata-list", str(self.inputs / "metadata.json"),
                "--bundle-inputs", str(self.inputs / "bundles.json")]
        for descriptor in contracts.mirror_transports(self.mirrors):
            key = descriptor.get("role", descriptor.get("bundle_id"))
            data = (("INVENTED TEST IMAGE " if "role" in descriptor else "INVENTED TEST BUNDLE ") + key + "\n").encode("ascii")
            image = self.inputs / (key + Path(descriptor["file"]).suffix)
            image.write_bytes(data)
            sums = self.inputs / (key + ".sums")
            sums.write_text(hashlib.sha256(data).hexdigest() + "  " + image.name + "\n", encoding="ascii")
            self.sources[key] = image
            if "role" in descriptor:
                argv.extend(["--" + key, str(image), "--" + key + "-sums", str(sums)])
            else:
                self.producers.append({"bundle_id": key, "source": image.name, "sums": sums.name})
        for role, child in contracts.mirror_child_names(self.mirrors).items():
            self.asset(child.removesuffix("-SHA256SUMS") + "-README.md", b"Synthetic inventory.\n", [role])
        self.asset(self.mirrors["capability_manifest"]["file"], self.capraw, [])
        self.asset("ASSET-MAP.json", contracts.canonical({"format": "deep32-asset-map", "format_version": 1,
                   "release": self.mirrors["release"], "entries": [
                       {"asset": self.mirrors["capability_manifest"]["file"], "media_paths": [], "install": []}]}), [])
        self.args = assembly.make_parser().parse_args(argv)
        self.sync()

    def asset(self, name, data, roles):
        (self.inputs / name).write_bytes(data)
        self.rows.append({"file": name, "source": name, "discs": roles})

    def sync(self):
        (self.inputs / "mirrors.json").write_bytes(contracts.canonical(self.mirrors))
        (self.inputs / "metadata.json").write_bytes(contracts.canonical({
            "format": "deep32-release-metadata-list", "format_version": 1, "assets": self.rows}))
        (self.inputs / "bundles.json").write_bytes(contracts.canonical({
            "format": "deep32-release-bundle-inputs", "format_version": 1, "bundles": self.producers}))

    def refused(self):
        self.sync()
        with self.assertRaises((ValueError, assembly.Refusal, OSError)):
            assembly.assemble(self.args)
        self.assertFalse(self.output.exists())

    def test_all_classes_staged_once_and_four_signatures(self):
        self.assertEqual(assembly.assemble(self.args), 0)
        github = self.output / "github"
        for descriptor in contracts.mirror_transports(self.mirrors):
            key = descriptor.get("role", descriptor.get("bundle_id"))
            target = self.output / "images" / descriptor["file"]
            self.assertTrue(os.path.samefile(target, self.sources[key]))
            self.assertFalse((github / descriptor["file"]).exists())
        self.assertEqual(len(list((self.output / "images").iterdir())), len(contracts.mirror_transports(self.mirrors)))
        assets = (github / "ASSETS.txt").read_text().splitlines()
        self.assertEqual(sum(name.endswith(".sig") for name in assets), 4)
        child = getter.read_sums(github / contracts.mirror_child_names(self.mirrors)["disc3"])
        self.assertTrue({b["file"] for b in self.mirrors["bundles"]} <= set(child))
        getter.validate_whole_release(github, scope="whole", require_signatures=False)

    def test_v2_cli_preflight_and_explicit_bundle_paths(self):
        assembly.assemble(self.args)
        argv = [sys.executable, "-I", "-B", str(TOOLS / "deep32_get_manifest.py"), "sign-preflight",
                "--release-dir", str(self.output / "github"), "--scope", "whole"]
        for key, source in self.sources.items():
            argv.extend(["--image", key + "=" + str(source)])
        result = subprocess.run(argv, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

    def test_missing_bundle_producer(self):
        self.producers.pop()
        self.refused()

    def test_duplicate_bundle_producer(self):
        self.producers.append(copy.deepcopy(self.producers[0]))
        self.refused()

    def test_wrong_bundle_transport_bytes(self):
        self.sources["embedder-turing"].write_bytes(b"different synthetic bytes")
        self.refused()

    def test_wrong_exact_bundle_identity(self):
        self.mirrors["bundles"][0]["bundle_sha256"] = "0" * 64
        self.refused()

    def test_changed_capability_raw_bytes(self):
        (self.inputs / self.mirrors["capability_manifest"]["file"]).write_bytes(self.capraw + b"\n")
        self.refused()

    def test_capability_bytes_above_five_mib_below_eight_mib(self):
        raw = self.capraw + b" " * (5 * 1024 * 1024 + 1 - len(self.capraw))
        self.mirrors["capability_manifest"]["sha256"] = hashlib.sha256(raw).hexdigest()
        (self.inputs / self.mirrors["capability_manifest"]["file"]).write_bytes(raw)
        self.sync()
        assembly.assemble(self.args)
        getter.validate_whole_release(self.output / "github", scope="whole", require_signatures=False)

    def test_capability_bytes_above_eight_mib_refused(self):
        raw = self.capraw + b" " * (8 * 1024 * 1024 + 1 - len(self.capraw))
        self.mirrors["capability_manifest"]["sha256"] = hashlib.sha256(raw).hexdigest()
        (self.inputs / self.mirrors["capability_manifest"]["file"]).write_bytes(raw)
        self.refused()

    def test_other_metadata_keeps_five_mib_limit(self):
        self.asset("unrelated.json", b" " * (5 * 1024 * 1024 + 1), [])
        self.refused()

    def test_malformed_mirror_top_level_is_a_contract_refusal(self):
        for value in ([], None, "mirrors", 2, True):
            with self.subTest(value=value), self.assertRaises(contracts.ContractError):
                contracts.validate_contract("mirrors", value)

    def test_mirror_versions_require_json_integers(self):
        legacy = contracts.strict_load(TOOLS / "contracts" / "fixtures" / "valid" / "mirrors.json")
        for original in (legacy, self.mirrors):
            for replacement in (float(original["format_version"]), str(original["format_version"]), True, None, 3):
                value = copy.deepcopy(original)
                value["format_version"] = replacement
                with self.subTest(version=replacement), self.assertRaises(contracts.ContractError):
                    contracts.validate_contract("mirrors", value)

    def test_reused_transport_for_changed_capability_definition(self):
        manifest = copy.deepcopy(self.capability)
        bundle = next(b for b in manifest["bundles"] if b["id"] == "core")
        domain = bundle["storage_budgets"][0]["domains"][0]
        domain["headroom_bytes"] += 1
        domain["peak_bytes"] += 1
        contracts.validate_contract("capability-manifest", manifest)
        self.mirrors["capability_manifest"]["sha256"] = hashlib.sha256(contracts.canonical(manifest)).hexdigest()
        with self.assertRaisesRegex(contracts.ContractError, "bundle identity mismatch"):
            contracts.validate_mirror_capabilities(self.mirrors, manifest)

    def test_distinct_bundle_files_cannot_repeat_payload_bytes(self):
        first, second = self.mirrors["bundles"][:2]
        self.assertNotEqual(first["bundle_sha256"], second["bundle_sha256"])
        for key in ("size", "sha256", "chunk_sha256"):
            second[key] = copy.deepcopy(first[key])
        self.refused()

    def test_universal_disc3_argument_refused(self):
        self.args.disc3 = str(self.sources["core"])
        self.args.disc3_sums = str(self.inputs / "core.sums")
        self.refused()

    def test_legacy_mirror_v1_is_readable_but_not_a_new_assembly(self):
        legacy = contracts.strict_load(TOOLS / "contracts" / "fixtures" / "valid" / "mirrors.json")
        contracts.validate_contract("mirrors", legacy)
        self.mirrors = legacy
        self.refused()

    def test_legacy_mirror_v1_signing_preflight_refused(self):
        self.mirrors = contracts.strict_load(TOOLS / "contracts" / "fixtures" / "valid" / "mirrors.json")
        folder = self.root / "legacy"
        folder.mkdir()
        raw = contracts.canonical(self.mirrors)
        (folder / "MIRRORS.json").write_bytes(raw)
        entries = {d["file"]: d["sha256"] for d in self.mirrors["discs"]}
        entries["MIRRORS.json"] = hashlib.sha256(raw).hexdigest()
        (folder / "SHA256SUMS").write_bytes("".join(digest + "  " + name + "\n" for name, digest in sorted(entries.items())).encode("ascii"))
        getter.read_mirrors(folder / "MIRRORS.json", folder / "SHA256SUMS")
        with self.assertRaisesRegex(getter.Refusal, "preflight requires mirrors version 2"):
            getter.verify_whole(["--release-dir", str(folder)], signing=True)

    def test_sbom_subject_agreement_includes_every_bundle_transport(self):
        index = contracts.strict_load(TOOLS / "contracts" / "fixtures" / "valid" / "sbom-index.json")
        template = copy.deepcopy(index["subjects"][0])
        index["subjects"] = []
        for descriptor in contracts.mirror_transports(self.mirrors):
            row = copy.deepcopy(template)
            row["subject"].update(kind="disc", path=descriptor["file"], size=descriptor["size"], sha256=descriptor["sha256"], aliases=[])
            row["sbom"]["asset"] = descriptor["file"] + ".spdx.json"
            index["subjects"].append(row)
        contracts.validate_contract_set({"mirrors": self.mirrors, "sbom-index": index})
        index["subjects"].pop()
        with self.assertRaisesRegex(contracts.ContractError, "disc coverage mismatch"):
            contracts.validate_contract_set({"mirrors": self.mirrors, "sbom-index": index})

    def test_missing_class_descriptor_refused(self):
        self.mirrors["bundles"] = [b for b in self.mirrors["bundles"] if b["bundle_id"] != "embedder-blackwell120"]
        self.refused()

    def test_undeclared_source_bundle_refused(self):
        item = copy.deepcopy(self.mirrors["bundles"][0])
        item["bundle_id"] = "source-build-unrelated"
        item["file"] = "deep32-0.1.0-alpha.2-disc3-gpu-pack-source-build-unrelated.iso"
        item["mirrors"][0]["url"] = "https://archive.org/download/" + self.mirrors["archive"]["item"] + "/" + item["file"]
        self.mirrors["bundles"].append(item)
        with self.assertRaises(contracts.ContractError):
            contracts.validate_mirror_capabilities(self.mirrors, self.capability)

    def test_declared_source_bundle_allowed_but_not_required(self):
        contracts.validate_mirror_capabilities(self.mirrors, self.capability, capability_bytes=self.capraw)
        source = next(b for b in self.capability["bundles"] if b["kind"] == "corresponding-source-build")
        item = copy.deepcopy(self.mirrors["bundles"][0])
        item["bundle_id"] = source["id"]
        item["bundle_sha256"] = hashlib.sha256(contracts.canonical(source)).hexdigest()
        item["sha256"] = "f" * 64
        item["chunk_sha256"] = ["f" * 64]
        item["file"] = "deep32-0.1.0-alpha.2-disc3-gpu-pack-" + source["id"] + ".iso"
        item["mirrors"][0]["url"] = "https://archive.org/download/" + self.mirrors["archive"]["item"] + "/" + item["file"]
        self.mirrors["bundles"].append(item)
        contracts.validate_mirror_capabilities(self.mirrors, self.capability)


if __name__ == "__main__":
    unittest.main(verbosity=2)
