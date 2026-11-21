#!/usr/bin/env python3
"""Apply the VM-specific patches and exercise the medium guard with fake commands."""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ARGS = None


def shell_path(path):
    value = path.as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) > 2 and value[1] == ":" else value


class Patches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ARGS.work_dir)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_patch_application_and_parse(self):
        dest = self.root / "driver"
        shutil.copytree(ARGS.snapshot, dest)
        for patch in sorted(Path(ARGS.patches).glob("*.patch")):
            run = subprocess.run([ARGS.patch, "--batch", "-p1", "-d", str(dest), "-i", str(patch.resolve())],
                                 text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            target = dest / "stages" / patch.name.removesuffix(".patch")
            expected = Path(ARGS.patches) / "files/stages" / target.name
            self.assertEqual(target.read_bytes(), expected.read_bytes())
        shutil.copyfile(Path(ARGS.patches) / "files/stages/alpha2_stage.py", dest / "stages/alpha2_stage.py")
        for script in sorted(dest.rglob("*.sh")):
            run = subprocess.run([ARGS.sh, "-n", str(script)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, str(script) + run.stderr)
        for script in sorted((dest / "stages").glob("*.py")):
            compile(script.read_bytes(), script.name, "exec")
        # Targeted patches preserve the actual VM guard and private-list spelling.
        self.assertEqual((dest / "stages/lib.sh").read_bytes(),
                         (Path(ARGS.snapshot) / "stages/lib.sh").read_bytes())
        baseline_map = (Path(ARGS.snapshot) / "stages/stage_map.py").read_text()
        patched_map = (dest / "stages/stage_map.py").read_text()
        # The committed source baseline already names the renamed mode list.
        # Preserve every actual list input, rather than a historical VM filename.
        self.assertIn('"BASELINE-MODES.tsv"', baseline_map)
        self.assertIn('"BASELINE-MODES.tsv"', patched_map)
        list_input = r'read_tsv\(os\.path\.join\(L, "([^"]+)"\)'
        self.assertEqual(re.findall(list_input, patched_map),
                         re.findall(list_input, baseline_map))

    def guard(self, roots="tailscale\n", packages=("tailscale-1.2.3.tgz",), drift=False):
        tree = self.root / "dist/work/80-packages-mnt/packages/amd64"
        repo = self.root / "pkgrepo"
        out = self.root / "out"
        for directory in (tree, repo, out):
            directory.mkdir(parents=True)
        (tree / "roots").write_text(roots, encoding="utf-8")
        for name in packages:
            (tree / name).write_bytes(b"synthetic package\n")
            (repo / name).write_bytes(b"different bytes\n" if drift else b"synthetic package\n")
        text = (Path(ARGS.patches) / "files/stages/80-image.sh").read_text()
        block = text.split("# BEGIN alpha2 medium package guard:", 1)[1].split("# END alpha2", 1)[0]
        # The first split leaves a descriptive comment suffix. Keep it a comment.
        block = "#" + block
        block = block.replace("/build/pkgrepo", "./pkgrepo")
        body = r'''set -eu
die() { echo "$*" >&2; exit 1; }
need_file() { [ -f "$1" ] || die "missing: $1"; }
sha256() {
    if [ "$1" = -q ]; then shift; sha256sum "$1" | cut -d ' ' -f1
    else shift; sha256sum "$@"; fi
}
check_sha() { [ "$(sha256 -q "$1")" = "$2" ] || die "hash mismatch"; }
xargs() { shift; while IFS= read -r p; do sha256 "$@" "$p"; done; }
vnconfig() { if [ "${1-}" != -u ]; then echo vnd0; fi; }
mount() { [ "$1" = -r ] || die "mount was not read-only"; }
umount() { :; }
'''
        body += "D32_ROOT=dist\nOUT=out\nD32_RELEASE_ID=test\n" + block
        script = self.root / "guard.sh"
        script.write_text(body, encoding="utf-8", newline="\n")
        return subprocess.run([ARGS.sh, str(script)], cwd=self.root, text=True, capture_output=True), out

    def test_medium_package_present(self):
        run, out = self.guard()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("tailscale-1.2.3.tgz", (out / "MEDIUM-PACKAGES.sha256").read_text())
        self.assertEqual((out / "MEDIUM-PACKAGE-ROOTS.txt").read_text(), "tailscale\n")

    def test_versioned_medium_root(self):
        run, _ = self.guard(roots="tailscale-1.2.3\n")
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_missing_root_refused(self):
        run, _ = self.guard(roots="other-package\n")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("missing from medium roots", run.stderr)

    def test_missing_package_refused(self):
        run, _ = self.guard(packages=())
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("expected one tailscale", run.stderr)

    def test_ambiguous_package_refused(self):
        run, _ = self.guard(packages=("tailscale-1.2.3.tgz", "tailscale-1.2.4.tgz"))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("expected one tailscale", run.stderr)

    def test_changed_package_refused(self):
        run, _ = self.guard(drift=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("hash mismatch", run.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    for name in ("snapshot", "patches", "work-dir", "sh", "patch"):
        ap.add_argument("--" + name, required=True)
    ARGS, remaining = ap.parse_known_args()
    Path(ARGS.work_dir).mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0]] + remaining)
