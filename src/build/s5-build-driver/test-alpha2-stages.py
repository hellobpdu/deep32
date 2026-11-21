#!/usr/bin/env python3
"""Offline alpha.2 staging gates, using generated public fixtures only."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
WORK = None


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / "stages" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runtime = load("alpha2_stage")
mapper = load("stage_map")


class Stages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=WORK)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree, self.stage, self.site = [self.root / n for n in ("tree", "stage", "site")]
        core = ["deep32_doctor.py", "deep32_diag/__init__.py", "deep32_diag/__main__.py",
                "deep32_diag/cli.py", "deep32_diag/doctor.py", "schemas/doctor-output.schema.json"]
        for rel in core:
            self.put(self.tree / "diagnostics" / rel, "{}\n" if rel.endswith("json") else "# generated fixture\n")
        files = ["usr/local/bin/deep32", "usr/local/d32/deep32net/__init__.py",
                 "usr/local/d32/deep32net/cli.py", "usr/local/d32/publication.py",
                 "usr/local/d32/sbin/d32-publish", "usr/local/d32/libexec/d32-forge-enrol",
                 "usr/local/d32/libexec/d32-gateway-enrol", "usr/local/d32/libexec/deep32-pair",
                 "usr/local/d32/libexec/pairing-helper"]
        for rel in files:
            self.put(self.tree / "distrib/site" / rel, "# generated fixture\n")
        shutil.copytree(self.tree / "distrib", self.site / "distrib")
        self.assertEqual(runtime.install(self.tree, self.stage), len(core))

    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.chmod(0o644)
        path.write_text(data, encoding="utf-8", newline="\n")

    def audit(self, assets=None):
        return runtime.audit_rows(self.tree, self.stage, self.site, assets)

    def test_all_runtime_rows_audited(self):
        rows = self.audit()
        self.assertEqual(len(rows), 15)
        self.assertTrue(all(r[3] == "alpha2-verified" and "sha256=" in r[4] for r in rows))

    def test_missing_module_fails(self):
        p = self.stage / "d32-node/usr/local/d32/diagnostics/deep32_diag/doctor.py"
        p.chmod(0o644)
        p.unlink()
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            self.audit()

    def test_modified_site_copy_fails(self):
        self.put(self.site / "distrib/site/usr/local/d32/publication.py", "# drift\n")
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            self.audit()

    def test_asset_collision_fails(self):
        asset = self.root / "SHA256SUMS"
        self.put(asset, "0" * 64 + "  usr/local/d32/deep32net/cli.py\n")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.audit(asset)

    def test_noncolliding_asset_passes(self):
        asset = self.root / "SHA256SUMS"
        self.put(asset, "0" * 64 + "  usr/local/share/example.txt\n")
        self.assertEqual(len(self.audit(asset)), 15)

    def test_site_pack_duplicate_fails(self):
        self.put(self.stage / "d32-apps/usr/local/bin/deep32", "# unexpected duplicate\n")
        with self.assertRaisesRegex(ValueError, "also in pack"):
            self.audit()

    def test_site_shadow_fails(self):
        self.put(self.site / "ship/site/usr/local/bin/deep32", "# shadow\n")
        with self.assertRaisesRegex(ValueError, "shadows"):
            self.audit()

    def test_incomplete_selftest_refused(self):
        self.put(self.tree / "diagnostics/deep32_diag/selftest.py", "# incomplete\n")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            runtime.diagnostics(self.tree)

    def test_full_selftest_accepted(self):
        for rel in ("deep32_diag/selftest.py", "deep32_diag/selftest_cli.py",
                    "deep32_diag/selftest_contract.py", "deep32_diag/selftest_trust.py",
                    "schemas/selftest-questions.schema.json", "schemas/selftest-reference.schema.json",
                    "schemas/selftest-result.schema.json"):
            self.put(self.tree / "diagnostics" / rel, "# optional runtime\n")
        self.assertEqual(len(runtime.diagnostics(self.tree)), 13)

    def test_source_tests_are_not_installed(self):
        self.put(self.tree / "diagnostics/tests/test_private.py", "# generated synthetic test\n")
        self.put(self.tree / "diagnostics/deep32_diag/tests/test_example.py", "# generated synthetic test\n")
        self.put(self.tree / "diagnostics/schemas/nested/example.json", "{}\n")
        self.assertEqual(len(runtime.diagnostics(self.tree)), 6)

    def test_relative_stage_path_is_supported(self):
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(runtime.install(self.tree, "relative-stage"), 6)
        finally:
            os.chdir(previous)
        self.assertTrue((self.root / "relative-stage/d32-node/usr/local/d32/diagnostics/deep32_doctor.py").is_file())

    def test_symlink_source_refused(self):
        p = self.tree / "diagnostics/deep32_diag/linked.py"
        try:
            p.symlink_to(self.tree / "diagnostics/deep32_doctor.py")
        except OSError as exc:
            self.skipTest("host cannot create symlinks: " + str(exc.winerror if hasattr(exc, 'winerror') else exc.errno))
        with self.assertRaisesRegex(ValueError, "linked runtime"):
            runtime.diagnostics(self.tree)

    def test_existing_missing_baseline_row_still_fails(self):
        lists = self.root / "lists"
        data = {
            "SOURCE-MAP.tsv": "golden_path\tdelivered_by\tpack\ttype\tgolden_sha256\ttree_path_or_inputs\trecipe\tstatus\n"
                              "/usr/local/d32/missing\tupdate\td32-node\tf\t-\tabsent\tR-COPY\tequal\n",
            "CUTS.tsv": "golden_path\twhat\n",
            "ADDITIONS.tsv": "path\twhat\n",
            "BASELINE-MODES.tsv": "path\tmode\tsha256\ttype\n",
            "TREE-RENAMES.tsv": "old\tnew\n",
        }
        for name, contents in data.items():
            self.put(lists / name, contents)
        report = self.root / "report.tsv"
        rc = mapper.main(["--tree", str(self.tree), "--stage", str(self.stage), "--site-src", str(self.site),
                          "--lists", str(lists), "--builds", str(self.root / "builds"),
                          "--report", str(report), "--alpha2"])
        self.assertEqual(rc, 1)
        result = report.read_text(encoding="utf-8")
        self.assertIn("\tMISSING\t", result)
        self.assertEqual(result.count("\talpha2-verified\t"), 15)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True)
    a, remaining = ap.parse_known_args()
    WORK = Path(a.work_dir).resolve()
    WORK.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0]] + remaining)
