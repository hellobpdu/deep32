"""Synthetic offline contract and cross-file mutation tests."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/release-tools/contract_validate.py"
spec = importlib.util.spec_from_file_location("contracts_under_test", SOURCE)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
FIXTURES = c.CONTRACTS / "fixtures"
SCENARIOS = 0
SCRATCH = None


def valid(kind):
    return c.strict_load(FIXTURES / "valid" / (kind + ".json"))


class Contracts(unittest.TestCase):
    def refused(self, call):
        global SCENARIOS
        SCENARIOS += 1
        with self.assertRaises(c.ContractError):
            call()

    def test_fixture_catalogue(self):
        global SCENARIOS
        catalogue = c.strict_load(FIXTURES / "contracts.json")
        for case in catalogue["cases"]:
            with self.subTest(case=case["path"]):
                fn = lambda: c.validate_contract(case["kind"], c.strict_load(FIXTURES / case["path"]))
                if case["valid"]:
                    fn()
                    SCENARIOS += 1
                else:
                    self.refused(fn)

    def test_unknown_fields_at_every_object_depth(self):
        def locations(value, path=()):
            if isinstance(value, dict):
                yield path
                for key, item in value.items():
                    yield from locations(item, path + (key,))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    yield from locations(item, path + (index,))
        catalogue = c.strict_load(FIXTURES / "contracts.json")
        for case in catalogue["cases"]:
            if not case["valid"]:
                continue
            value = c.strict_load(FIXTURES / case["path"])
            for location in locations(value):
                with self.subTest(kind=case["kind"], location=location):
                    mutated = copy.deepcopy(value)
                    target = mutated
                    for key in location:
                        target = target[key]
                    target["unknown_contract_field"] = "refuse"
                    self.refused(lambda: c.validate_contract(case["kind"], mutated))

    def test_strict_json(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":{"b":0,"b":1}}', b'{}{}',
                    b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}', b'\xff', b'\xef\xbb\xbf{}',
                    b' ' * (c.MAX_JSON_BYTES + 1), b'[' * 1100):
            self.refused(lambda: c.strict_loads(raw))

    def test_unsupported_schema_cannot_silently_pass(self):
        for schema in ({"unknownKeyword": True}, {"$ref": "https://example.org/schema"},
                       {"format": "password"}, {"properties": {"x": {"unused": 1}}}):
            self.refused(lambda: c.validate_schema({}, schema))

    def test_paths(self):
        for path in ("../a", "a/../b", "/a", "C:/a", "a\\b", "a//b", "CON.txt", "x/NUL", "a.", "a%2fb", "a\x00b", "a/./b"):
            self.refused(lambda: c.safe_path(path))
        self.assertEqual(c.safe_path("sbom/images/tei-ada.spdx.json"), "sbom/images/tei-ada.spdx.json")

    def test_public_url_policy(self):
        for url in ("file:///a", "https://user:pass@example.org/a", "https://example.org/a?q=1",
                    "https://example.org/a#x", "http://127.0.0.1/a", "http://169.254.169.254/a",
                    "https://x.local/a", "https://example.org/%0a", "https://example.org/%zz",
                    "https://example.org/%252e%252e/a", "https://example.org/../a",
                    "https://example.org:99999/a", "https://2130706433/a", "https://example.org/a\\b"):
            self.refused(lambda: c.safe_url(url))
        for url in ("https://archive.org/download/item/file.iso", "https://mirror.example.org/a", "https://[2606:4700:4700::1111]/a"):
            self.assertEqual(c.safe_url(url), url)

    def test_hash_graph(self):
        for nodes in ([{"path": "a", "depends_on": ["a"]}],
                      [{"path": "a", "depends_on": ["b"]}, {"path": "b", "depends_on": ["a"]}],
                      [{"path": "a", "depends_on": ["missing"]}],
                      [{"path": "a", "depends_on": []}, {"path": "A", "depends_on": []}]):
            self.refused(lambda: c.validate_hash_graph(nodes))

    def test_mirror_semantics(self):
        changes = [("size", True), ("size", 26.0), ("size", 1e3), ("chunk_sha256", []),
                   ("chunk_sha256", ["0" * 64, "1" * 64]), ("file", "NUL.iso"), ("kind", "content")]
        for key, replacement in changes:
            value = valid("mirrors")
            value["discs"][0][key] = replacement
            self.refused(lambda: c.validate_contract("mirrors", value))
        for field, replacement in (("release", "v0.1.0-alpha.1"), ("published_at", "2026-02-30T00:00:00Z")):
            value = valid("mirrors")
            value[field] = replacement
            self.refused(lambda: c.validate_contract("mirrors", value))

    def test_doctor_counts_dependencies_and_tailnet_rule(self):
        value = valid("doctor-output")
        value["checks"][0]["state"] = "OK"
        value["checks"][0]["fix"] = None
        value["summary"] = {"ok": 1, "warn": 0, "fail": 0, "skip": 0}
        value["exit_code"] = 0
        self.refused(lambda: c.validate_contract("doctor-output", value))
        value = valid("doctor-output")
        value["checks"][0]["blocked_by"] = ["missing"]
        self.refused(lambda: c.validate_contract("doctor-output", value))
        value = valid("doctor-install")
        value["filesystems"][0]["safe_running_bytes"] = 99999
        self.refused(lambda: c.validate_contract("doctor-install", value))
        value = valid("doctor-install")
        value["content"] = None  # Installer receipt before the content importer commits.
        c.validate_contract("doctor-install", value)

    def test_selftest_byte_and_numeric_bindings(self):
        raw = (FIXTURES / "valid/selftest-questions.json").read_bytes()
        reference = valid("selftest-reference")
        c.validate_selftest_bundle(raw, reference)
        self.refused(lambda: c.validate_selftest_bundle(raw + b"\n", reference))
        reference["reference"]["hit_at_1"] = 0.8
        self.refused(lambda: c.validate_contract("selftest-reference", reference))
        value = valid("selftest-result")
        value["completed_questions"] = 1
        self.refused(lambda: c.validate_contract("selftest-result", value))

    def test_cross_file_subjects_and_calibration(self):
        documents = {kind: valid(kind) for kind in ("mirrors", "sbom-input", "sbom-index", "reproducibility", "asset-map",
                                                   "selftest-reference", "selftest-calibration")}
        c.validate_contract_set(documents)
        changed = copy.deepcopy(documents)
        changed["sbom-index"]["subjects"][0]["subject"]["sha256"] = "0" * 64
        self.refused(lambda: c.validate_contract_set(changed))
        changed = copy.deepcopy(documents)
        changed["selftest-calibration"]["settings_sha256"] = "0" * 64
        self.refused(lambda: c.validate_contract_set(changed))

    def test_sbom_identity_alias_and_partial_scope(self):
        value = valid("sbom-index")
        value["subjects"][0]["coverage"]["components"] = "partial"
        value["subjects"][0]["gap"] = None
        self.refused(lambda: c.validate_contract("sbom-index", value))
        value = valid("sbom-input")
        value["components"][0]["purpose"] = "CONTAINER"
        self.refused(lambda: c.validate_contract("sbom-input", value))

    def test_reproducibility_proof_requires_equal_clean_runs(self):
        value = valid("reproducibility")
        row = value["subjects"][0]
        row.update(status="proven", byte_comparison=True, reason=None,
                   receipt={"path": "compare.json", "sha256": "1" * 64})
        def run(identity):
            return {"id": identity, "clean": True, "output": {k: row["subject"][k] for k in ("size", "sha256")},
                    "exit_code": 0, "environment": {"tz": "UTC", "locale": "C", "umask": "0022", "source_date_epoch": value["source_date_epoch"]},
                    "platform": "synthetic", "architecture": "amd64", "tool_versions": [{"name": "fixture", "version": "1"}],
                    "clean_method": "Synthetic fresh trees", "verification": "passed"}
        row["runs"] = [run("one"), run("two")]
        c.validate_contract("reproducibility", value)
        row["runs"][1]["output"]["sha256"] = "0" * 64
        self.refused(lambda: c.validate_contract("reproducibility", value))
        row["status"] = "not-proven"
        row["reason"] = "Synthetic second clean build differs."
        row["byte_comparison"] = False
        c.validate_contract("reproducibility", value)
        row["byte_comparison"] = True
        self.refused(lambda: c.validate_contract("reproducibility", value))
        row["status"] = "proven"
        row["reason"] = None
        row["runs"][1] = run("one")
        self.refused(lambda: c.validate_contract("reproducibility", value))

    def test_metadata_destinations_are_fixed(self):
        for asset in ("ASSET-MAP.json", "SHA256SUMS", "image.iso", "signature.asc"):
            value = valid("asset-map")
            value["entries"][0]["asset"] = asset
            self.refused(lambda: c.validate_contract("asset-map", value))
        value = valid("asset-map")
        value["entries"][0]["media_paths"][0]["path"] = "selftest/reference.json"
        self.refused(lambda: c.validate_contract("asset-map", value))

    def test_declared_inputs_and_raw_byte_limits(self):
        value = valid("sbom-input")
        value["input_roots"][0]["manifest"]["path"] = "outside/manifest.json"
        self.refused(lambda: c.validate_contract("sbom-input", value))
        value = valid("sbom-input")
        value["components"][0]["licence_evidence"][0]["path"] = "outside/LICENSE.txt"
        self.refused(lambda: c.validate_contract("sbom-input", value))
        path = SCRATCH / "oversized-reference.json"
        path.write_bytes(c.canonical(valid("selftest-reference")) + b" " * (64 * 1024))
        self.refused(lambda: c.load_contract("selftest-reference", path))

    def test_canonical_output_independent_of_object_order(self):
        value = valid("release-metadata")
        self.assertEqual(c.canonical(value), c.canonical(dict(reversed(list(value.items())))))
        self.assertTrue(c.canonical(value).endswith(b"\n"))

    def test_cli_valid_and_refused(self):
        for file, code in (("valid/mirrors.json", 0), ("invalid/mirrors--duplicate-key.json", 2)):
            result = subprocess.run([sys.executable, "-I", "-B", str(SOURCE), "mirrors", str(FIXTURES / file)],
                                    capture_output=True, check=False, timeout=15)
            self.assertEqual(result.returncode, code, result.stderr.decode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch", type=Path, required=True)
    args, rest = parser.parse_known_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    SCRATCH = args.scratch
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Contracts))
    summary = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "skipped": len(result.skipped), "scenarios": SCENARIOS, "pass": result.wasSuccessful()}
    (args.scratch / "contract-summary.json").write_bytes(c.canonical(summary))
    print(json.dumps(summary, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
