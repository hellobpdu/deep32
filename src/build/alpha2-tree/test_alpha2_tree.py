"""Offline safety and reproducibility tests; all archive content is synthetic."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("alpha2_tree", Path(__file__).with_name("make-alpha2-tree.py"))
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def digest(data):
    return hashlib.sha256(data).hexdigest()


NODE = shutil.which("node")
ROOT_SOURCES = {target: name for name, target in tool.ROOT_FILES.items()}
# A stand-in theme generator: writes theme/src/plan.json's files below --out, may write elsewhere or fail on request,
# and fails if Node options or Deep32 build switches reach it.
FAKE_GENERATOR = b"""'use strict';
const fs = require('fs'), path = require('path');
const plan = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'src', 'plan.json'), 'utf8'));
const out = process.argv[process.argv.indexOf('--out') + 1];
for (const [rel, text] of Object.entries(plan.files || {})) {
  const p = path.join(out, ...rel.split('/'));
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, text);
}
for (const [rel, text] of Object.entries(plan.extra || {})) fs.writeFileSync(path.join(...rel.split('/')), text);
if (Object.keys(process.env).some(k => /^(NODE_|D32_)/i.test(k))) process.exit(9);
process.exit(plan.exit || 0);
"""


class SyntheticSource:
    """Exercise the full overlay without creating or changing Git commits."""

    commit = "b" * 40
    published = "a" * 40

    def __init__(self, before, after):
        self.contents = {}
        self.trees = {}
        self.extra_committed_names = set()
        for revision, values in ((self.published, before), (self.commit, after)):
            mapped = {}
            for target, content in values.items():
                source = ROOT_SOURCES.get(target) or (
                    "src/" + target if target.startswith(("model-bridge/", "diagnostics/"))
                    else "src/workstation/" + target)
                oid = digest(content)
                self.contents[oid] = content
                mapped[target] = {"source": source, "oid": oid, "mode": 0o644}
            self.trees[revision] = mapped

    def mapped(self, revision):
        return self.trees[revision]

    def blob(self, row):
        return self.contents[row["oid"]]

    def reference_names(self):
        return self.extra_committed_names


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-alpha2-", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.before = {"ship/replace": b"original\n", "ship/delete": b"deleted\n",
                       "ship/untouched": b"untransformed\n"}
        self.after = {"ship/replace": b"new\n", "ship/add": b"added\n",
                      "ship/untouched": b"untransformed\n",
                      "model-bridge/d32_model_bridge.py": b"# bridge\n"}
        self.after.update({name: ("# " + name + "\n").encode() for name in tool.REQUIRED_DIAGNOSTICS})
        self.after.update({target: ("text of " + name + "\n").encode() for name, target in tool.LICENSE_FILES.items()})
        self.alpha1 = {**self.before, "ship/untouched": b"transformed, keep me\n",
                       "generated/asset": b"generated\n"}
        self.source = SyntheticSource(self.before, self.after)
        self.deletions = {"ship/delete": digest(b"deleted\n")}

    def archive(self, values=None, *, reverse=False, timestamp=10, extra=None):
        values = self.alpha1 if values is None else values
        archive = self.root / ("input-reversed.tar" if reverse else "input.tar")
        with tarfile.open(archive, "w") as output:
            for name in sorted(values, reverse=reverse):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(values[name]), 0o755 if name.endswith("replace") else 0o644, timestamp
                info.uid = info.gid = timestamp
                output.addfile(info, io.BytesIO(values[name]))
            if extra:
                output.addfile(extra)
        pin = self.root / "input.PIN.sha256"
        pin.write_bytes(tool.pin_bytes({name: digest(data) for name, data in values.items()}))
        return archive, pin

    def build(self, archive=None, pin=None, name="out", node=None):
        if archive is None:
            archive, pin = self.archive()
        return tool.build(archive, pin, self.source, self.root / name, deletions=self.deletions, node=node)

    def read_output(self, report, name="out"):
        with tarfile.open(self.root / name / report["tree_archive"]) as archive:
            return {member.name: archive.extractfile(member).read()
                    for member in archive if member.isfile()}

    def remap(self):
        self.source = SyntheticSource(self.before, self.after)

    def audit(self, values=None):
        values = self.alpha1 if values is None else values
        return tool.audit_pin(self.source, {name: digest(data) for name, data in values.items()},
                              deletions=self.deletions)

    def test_full_correspondence_add_delete_and_preserve_unmapped(self):
        report = self.build()
        out = self.root / "out"
        files = self.read_output(report)
        self.assertEqual(files["ship/replace"], b"new\n")
        self.assertEqual(files["ship/add"], b"added\n")
        self.assertNotIn("ship/delete", files)
        self.assertEqual(files["ship/untouched"], b"untransformed\n")
        self.assertEqual(files["generated/asset"], b"generated\n")
        self.assertEqual(files["model-bridge/d32_model_bridge.py"], b"# bridge\n")
        pin = (out / "PIN.sha256").read_bytes()
        self.assertEqual(tool.parse_pin(pin), {name: digest(data) for name, data in files.items()})
        self.assertEqual(report["tree_hash"], digest(pin))
        self.assertEqual((out / "TREE-HASH").read_bytes(), (digest(pin) + "\n").encode())
        patch = (out / "build.conf.patch").read_text()
        self.assertIn("-D32_TREE_HASH=" + digest((self.root / "input.PIN.sha256").read_bytes()), patch)
        self.assertIn("+D32_RELEASE_ID=0.1.0-alpha.2", patch)

    def test_determinism_all_output_bytes_and_tar_metadata(self):
        archive, pin = self.archive()
        report = self.build(archive, pin, "first")
        archive, pin = self.archive(reverse=True, timestamp=987654)
        self.build(archive, pin, "second")
        first, second = self.root / "first", self.root / "second"
        self.assertEqual(sorted(p.name for p in first.iterdir()), sorted(p.name for p in second.iterdir()))
        for item in first.iterdir():
            self.assertEqual(item.read_bytes(), (second / item.name).read_bytes(), item.name)
        with tarfile.open(first / report["tree_archive"]) as archive:
            names = []
            for member in archive:
                names.append(member.name)
                self.assertEqual((member.uid, member.gid, member.mtime, member.uname, member.gname), (0, 0, 0, "", ""))
            self.assertEqual(names, sorted(names))

    def test_replace_differing_alpha1_copy_and_record_all_hashes(self):
        self.alpha1["ship/replace"] = b"local transform\n"
        report = self.build()
        self.assertEqual(self.read_output(report)["ship/replace"], b"new\n")
        row = next(row for row in report["changes"] if row["path"] == "ship/replace")
        self.assertEqual(row["action"], "replace")
        self.assertEqual(row["alpha1_sha256"], digest(b"local transform\n"))
        self.assertEqual(row["published_sha256"], digest(b"original\n"))
        self.assertEqual(row["merged_sha256"], digest(b"new\n"))
        self.assertEqual(row["before_sha256"], row["alpha1_sha256"])
        self.assertEqual(row["after_sha256"], row["merged_sha256"])

    def test_refuse_deletion_with_different_alpha1_hash(self):
        self.alpha1["ship/delete"] = b"local deletion transform\n"
        with self.assertRaises(tool.Conflicts) as caught:
            self.build()
        self.assertEqual([row["path"] for row in caught.exception.conflicts], ["ship/delete"])
        self.assertFalse((self.root / "out").exists())

    def test_replace_addition_collision_from_merged_bytes(self):
        self.alpha1["ship/add"] = b"preexisting transformed file"
        report = self.build()
        self.assertEqual(self.read_output(report)["ship/add"], b"added\n")
        row = next(row for row in report["changes"] if row["path"] == "ship/add")
        self.assertEqual(row["action"], "replace")
        self.assertEqual(row["alpha1_sha256"], digest(b"preexisting transformed file"))
        self.assertIsNone(row["published_sha256"])
        self.assertEqual(row["merged_sha256"], digest(b"added\n"))

    def test_add_missing_mapped_path(self):
        del self.alpha1["ship/replace"]
        report = self.build()
        self.assertEqual(self.read_output(report)["ship/replace"], b"new\n")
        row = next(row for row in report["changes"] if row["path"] == "ship/replace")
        self.assertEqual(row["action"], "add")
        self.assertIsNone(row["alpha1_sha256"])
        self.assertEqual(row["published_sha256"], digest(b"original\n"))
        self.assertEqual(row["merged_sha256"], digest(b"new\n"))
        self.assertEqual(self.audit()["counts"], report["counts"])

    def test_refuse_absent_deletion_table_path(self):
        del self.alpha1["ship/delete"]
        with self.assertRaises(tool.Conflicts) as caught:
            self.build()
        self.assertEqual([row["path"] for row in caught.exception.conflicts], ["ship/delete"])
        self.assertFalse((self.root / "out").exists())

    def test_refuse_all_deletion_table_conflicts_together(self):
        self.deletions["ship/absent-delete"] = digest(b"absent\n")
        self.alpha1["ship/delete"] = b"wrong\n"
        with self.assertRaises(tool.Conflicts) as caught:
            self.build()
        self.assertEqual([row["path"] for row in caught.exception.conflicts],
                         ["ship/absent-delete", "ship/delete"])

    def test_refuse_deletion_still_mapped_by_merged_commit(self):
        self.after["ship/delete"] = b"reintroduced\n"
        self.remap()
        with self.assertRaises(tool.Conflicts) as caught:
            self.build()
        self.assertEqual([row["path"] for row in caught.exception.conflicts], ["ship/delete"])
        self.assertIn("mapped", str(caught.exception))

    def test_delete_only_supplied_table_paths(self):
        self.deletions = {}
        report = self.build()
        self.assertEqual(self.read_output(report)["ship/delete"], b"deleted\n")
        self.assertEqual(report["counts"], {"replaced": 2, "added": 13, "deleted": 0, "kept": 2})

    def test_keep_unmapped_paths_even_if_removed_from_published(self):
        self.before["ship/removed-public"] = b"published old\n"
        self.alpha1["ship/removed-public"] = b"alpha1 generated copy\n"
        self.alpha1["other/asset"] = b"\x00\xff\x00unchanged\n"
        self.remap()
        report = self.build()
        files = self.read_output(report)
        self.assertEqual(files["ship/removed-public"], b"alpha1 generated copy\n")
        self.assertEqual(files["other/asset"], b"\x00\xff\x00unchanged\n")
        self.assertNotIn("ship/removed-public", {row["path"] for row in report["changes"]})

    def test_replace_equal_published_and_merged_path(self):
        report = self.build()
        row = next(row for row in report["changes"] if row["path"] == "ship/untouched")
        self.assertEqual(row["action"], "replace")
        self.assertEqual(row["alpha1_sha256"], digest(b"transformed, keep me\n"))
        self.assertEqual(row["published_sha256"], digest(b"untransformed\n"))
        self.assertEqual(row["merged_sha256"], digest(b"untransformed\n"))

    def test_record_equal_alpha1_published_and_merged_path(self):
        self.alpha1["ship/untouched"] = self.after["ship/untouched"]
        report = self.build()
        row = next(row for row in report["changes"] if row["path"] == "ship/untouched")
        self.assertEqual(row["action"], "replace")
        self.assertEqual({row[key] for key in ("alpha1_sha256", "published_sha256", "merged_sha256")},
                         {digest(self.after["ship/untouched"])})

    def test_exact_counts_and_hashes_for_every_change(self):
        report = self.build()
        self.assertEqual(report["counts"], {"replaced": 2, "added": 13, "deleted": 1, "kept": 1})
        self.assertEqual(report["files"], 16)
        self.assertEqual(len(report["changes"]), 16)
        self.assertEqual(report["counts"]["replaced"] + report["counts"]["deleted"] + report["counts"]["kept"],
                         len(self.alpha1))
        self.assertEqual(report["counts"]["replaced"] + report["counts"]["added"] + report["counts"]["kept"],
                         report["files"])
        for row in report["changes"]:
            name = row["path"]
            with self.subTest(path=name):
                self.assertEqual(row["alpha1_sha256"], digest(self.alpha1[name]) if name in self.alpha1 else None)
                self.assertEqual(row["published_sha256"], digest(self.before[name]) if name in self.before else None)
                self.assertEqual(row["merged_sha256"], digest(self.after[name]) if name in self.after else None)
                self.assertEqual(row["before_sha256"], row["alpha1_sha256"])
                self.assertEqual(row["after_sha256"], row["merged_sha256"])
        self.assertEqual(json.loads((self.root / "out" / "change-report.json").read_bytes()), report)

    def test_refuse_merged_include_of_deleted_basename(self):
        self.after["ship/reference.c"] = b'#include "delete"\n'
        self.remap()
        with self.assertRaises(tool.Refusal) as caught:
            self.build()
        self.assertIn("ship/reference.c", str(caught.exception))
        self.assertIn("delete", str(caught.exception))
        self.assertFalse((self.root / "out").exists())

    def test_refuse_kept_include_of_deleted_path(self):
        self.alpha1["generated/legacy.c"] = b'#include "../ship/delete"\n'
        with self.assertRaises(tool.Refusal) as caught:
            self.build()
        self.assertIn("generated/legacy.c", str(caught.exception))
        self.assertIn("delete", str(caught.exception))

    @unittest.skip('lead ruling 2026-11-17: only references that resolve to one of the nine deleted paths refuse; general missing references (e.g. build-time fetched glibc/CPython sources) no longer refuse')

    def test_refuse_missing_quoted_include(self):
        self.after["ship/reference.c"] = b'#include "missing.h"\n'
        self.remap()
        with self.assertRaises(tool.Refusal) as caught:
            self.build()
        self.assertIn("missing.h", json.dumps(caught.exception.conflicts))

    @unittest.skip('lead ruling 2026-11-17: only references that resolve to one of the nine deleted paths refuse; general missing references (e.g. build-time fetched glibc/CPython sources) no longer refuse')

    def test_refuse_missing_include_in_unknown_suffix_for_build_and_audit(self):
        self.after["ship/header-template.in"] = b'/* generated */ #include "missing.h"\n'
        self.remap()
        with self.assertRaises(tool.Refusal) as caught:
            self.build()
        self.assertIn("missing.h", json.dumps(caught.exception.conflicts))
        report = self.audit()
        self.assertTrue(report["conflicts"])
        self.assertIn("ship/header-template.in", json.dumps(report["conflicts"]))
        self.assertIn("missing.h", json.dumps(report["conflicts"]))

    def test_resolve_local_and_kept_generated_includes(self):
        self.after["library/src/app/main.c"] = b'#include "local.h"\n#include "../lib/generated.inc"\n'
        self.after["library/src/app/local.h"] = b"#define LOCAL 1\n"
        self.alpha1["library/src/lib/generated.inc"] = b"#define GENERATED 1\n"
        self.remap()
        report = self.build()
        self.assertEqual(self.read_output(report)["library/src/lib/generated.inc"], b"#define GENERATED 1\n")

    def test_ignore_angle_includes_comments_and_non_source_bytes(self):
        self.after["ship/reference.c"] = (b"#include <system.h>\n"
                                            b'/* #include "missing.h" */\n'
                                            b'// #include "another-missing.h"\n')
        self.alpha1["generated/binary"] = b'\x00#include "missing-binary.h"\xff'
        self.remap()
        self.build()

    @unittest.skip('lead ruling 2026-11-17: only references that resolve to one of the nine deleted paths refuse; general missing references (e.g. build-time fetched glibc/CPython sources) no longer refuse')

    def test_refuse_makefile_prerequisite_on_missing_path(self):
        self.after["ship/Makefile"] = b"all: missing.c\n\t@echo build\n"
        self.remap()
        with self.assertRaises(tool.Refusal) as caught:
            self.build()
        self.assertIn("ship/Makefile", str(caught.exception))
        self.assertIn("missing.c", json.dumps(caught.exception.conflicts))

    def test_refuse_makefile_prerequisite_on_deleted_path(self):
        self.after["ship/Makefile"] = b"all: delete\n"
        self.remap()
        with self.assertRaises(tool.Refusal) as caught:
            self.build()
        self.assertIn("delete", str(caught.exception))

    def test_refuse_kept_makefile_prerequisite_on_deleted_path(self):
        self.alpha1["generated/Makefile"] = b"all: ../ship/delete\n"
        with self.assertRaises(tool.Refusal) as caught:
            self.build()
        self.assertIn("generated/Makefile", str(caught.exception))
        self.assertIn("delete", str(caught.exception))

    @unittest.skip('lead ruling 2026-11-17: only references that resolve to one of the nine deleted paths refuse; general missing references (e.g. build-time fetched glibc/CPython sources) no longer refuse')

    def test_refuse_missing_continued_makefile_prerequisite(self):
        self.after["ship/Makefile"] = b"all: replace \\\n  absent.c\n"
        self.remap()
        with self.assertRaises(tool.Refusal) as caught:
            self.build()
        self.assertIn("absent.c", json.dumps(caught.exception.conflicts))

    def test_accept_makefile_prerequisite_resolved_against_final_tree(self):
        self.after["ship/Makefile"] = b"all: replace ../generated/asset\n"
        self.remap()
        self.build()

    def test_allow_deleted_module_id_in_patsubst(self):
        self.deletions["library/src/app/room_cisco.c"] = digest(b"old module\n")
        self.alpha1["library/src/app/room_cisco.c"] = b"old module\n"
        self.after["library/Makefile"] = (
            b"MODULES = room_cisco\n"
            b"RENAMED = $(patsubst room_cisco,room_documents,$(MODULES))\n"
            b"all: $(addprefix src/app/,$(addsuffix .c,$(RENAMED)))\n")
        self.after["library/src/app/room_documents.c"] = b"/* replacement module */\n"
        self.remap()
        report = self.build()
        files = self.read_output(report)
        self.assertNotIn("library/src/app/room_cisco.c", files)
        self.assertIn(b"room_cisco", files["library/Makefile"])

    def test_allow_reference_present_only_in_merged_commit(self):
        self.after["ship/reference.c"] = b'#include "../../shared/public.h"\n'
        self.after["ship/Makefile"] = b"all: ../../shared/public.h\n"
        self.remap()
        self.source.extra_committed_names.add("src/shared/public.h")
        report = self.build()
        self.assertNotIn("shared/public.h", self.read_output(report))
        self.assertEqual(self.audit()["conflicts"], [])

    def test_refuse_bad_hash_missing_and_unpinned_files(self):
        archive, pin = self.archive()
        original = pin.read_bytes()
        pin.write_bytes(original.replace(digest(b"original\n").encode(), b"0" * 64))
        with self.assertRaisesRegex(tool.Refusal, "PIN mismatch"):
            self.build(archive, pin)
        pin.write_bytes(tool.pin_bytes({**tool.parse_pin(original), "ship/missing": "0" * 64}))
        with self.assertRaisesRegex(tool.Refusal, "missing from archive"):
            self.build(archive, pin)
        pin.write_bytes(tool.pin_bytes({name: value for name, value in tool.parse_pin(original).items() if name != "ship/replace"}))
        with self.assertRaisesRegex(tool.Refusal, "missing from PIN"):
            self.build(archive, pin)

    def test_refuse_symlink_hardlink_device_and_duplicate(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                extra = tarfile.TarInfo("ship/replace" if kind == tarfile.DIRTYPE else "unsafe")
                extra.type, extra.linkname = kind, "../../outside"
                archive, pin = self.archive(extra=extra)
                with self.assertRaises(tool.Refusal):
                    self.build(archive, pin)

    def test_refuse_archive_special_file_and_directory_modes(self):
        for kind in (tarfile.REGTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                extra = tarfile.TarInfo("special-mode")
                extra.type, extra.mode = kind, 0o4755
                archive, pin = self.archive(extra=extra)
                with self.assertRaisesRegex(tool.Refusal, "special.*permission"):
                    self.build(archive, pin)

    def test_refuse_unsafe_archive_path(self):
        for name in ("../outside", "/absolute", "C:/drive", "a/../b", "a\\b", "././x"):
            with self.subTest(name=name):
                extra = tarfile.TarInfo(name)
                archive, pin = self.archive(extra=extra)
                with self.assertRaisesRegex(tool.Refusal, "unsafe path"):
                    self.build(archive, pin)

    def test_preserve_empty_alpha1_directories_and_refuse_collision(self):
        extra = tarfile.TarInfo("empty-directory/")
        extra.type, extra.mode = tarfile.DIRTYPE, 0o750
        archive, pin = self.archive(extra=extra)
        report = self.build(archive, pin)
        with tarfile.open(self.root / "out" / report["tree_archive"]) as output:
            member = output.getmember("empty-directory")
            self.assertTrue(member.isdir())
            self.assertEqual(member.mode, 0o750)
        self.assertEqual(report["directories"], 1)
        extra.name = "ship/add/"
        archive, pin = self.archive(extra=extra)
        with self.assertRaisesRegex(tool.Refusal, "file/directory collision"):
            self.build(archive, pin, name="collision")

    def test_refuse_file_directory_and_case_collisions(self):
        for values in ({"a": b"a", "a/b": b"b"}, {"a": b"a", "A": b"b"}):
            with self.subTest(values=values):
                with self.assertRaises(tool.Refusal):
                    tool.parse_pin(tool.pin_bytes({name: digest(data) for name, data in values.items()}))

    def test_pin_exact_format_and_refuse_alternatives(self):
        valid = (b"a" * 64 + b"  first\n" + b"b" * 64 + b"  second\n")
        self.assertEqual(tool.pin_bytes(tool.parse_pin(valid)), valid)
        for broken in (valid.replace(b"  ", b" ", 1), valid.replace(b"\n", b"\r\n"),
                       valid[:-1], valid.upper(), b"\xef\xbb\xbf" + valid,
                       valid + b"\n", valid.splitlines(keepends=True)[1] + valid.splitlines(keepends=True)[0],
                       valid + valid.splitlines(keepends=True)[1]):
            with self.subTest(broken=broken):
                with self.assertRaises(tool.Refusal):
                    tool.parse_pin(broken)

    def test_given_alpha1_lines_round_trip_byte_for_byte(self):
        sample = Path(__file__).with_name("alpha1-pin-sample.sha256").read_bytes()
        self.assertEqual(len(tool.parse_pin(sample)), 3)
        self.assertEqual(tool.pin_bytes(tool.parse_pin(sample)), sample)

    def test_git_mapping_contract_and_exclusions(self):
        class Listing(tool.GitSource):
            def __init__(self, names):
                self.names = names

            def run(self, *args):
                return b"\0".join(("100644 blob " + "a" * 40 + "\t" + name).encode()
                                   for name in self.names) + b"\0"

        names = ["src/workstation/ship/add", "src/model-bridge/d32_model_bridge.py", "LICENSE", "NOTICE",
                 "licenses/AGPL-3.0-or-later.txt", "install/release-key.gpg",
                 "src/diagnostics/deep32_doctor.py", "src/diagnostics/deep32_diag/doctor.py",
                 "src/diagnostics/deep32_diag/nested/check.py", "src/diagnostics/schemas/doctor-output.schema.json",
                 "src/diagnostics/deep32", "src/diagnostics/deep32.ps1", "src/diagnostics/README.md",
                 "src/diagnostics/tests/test_doctor.py", "src/diagnostics/deep32_diag/tests/test_private.py",
                 "src/diagnostics/deep32_diag/__pycache__/old.py"]
        mapped = Listing(names).mapped("unused")
        self.assertEqual(set(mapped), {"ship/add", "model-bridge/d32_model_bridge.py",
                                      "diagnostics/deep32_doctor.py", "diagnostics/deep32_diag/doctor.py",
                                      "diagnostics/deep32_diag/nested/check.py", "diagnostics/schemas/doctor-output.schema.json",
                                      "distrib/site/usr/local/share/doc/d32/LICENSE",
                                      "distrib/site/usr/local/share/doc/d32/NOTICE",
                                      "distrib/site/usr/local/share/doc/d32/licenses/AGPL-3.0-or-later.txt",
                                      "distrib/site/usr/local/share/d32/release/release-key.gpg"})
        self.assertEqual(mapped["distrib/site/usr/local/share/doc/d32/NOTICE"]["source"], "NOTICE")
        self.assertEqual({mapped[name]["mode"] for name in mapped if name.startswith("distrib/")}, {0o444})
        with self.assertRaisesRegex(tool.Refusal, "root is not a directory"):
            Listing(["src/diagnostics"]).mapped("unused")

    def test_refuse_git_links_and_non_blob_modes(self):
        class Listing(tool.GitSource):
            def __init__(self, mode, kind):
                self.mode, self.kind = mode, kind

            def run(self, *args):
                return (f"{self.mode} {self.kind} " + "a" * 40 + "\tsrc/workstation/unsafe\0").encode()

        for mode, kind in (("120000", "blob"), ("160000", "commit"), ("040000", "tree")):
            with self.subTest(mode=mode, kind=kind):
                with self.assertRaisesRegex(tool.Refusal, "not a regular file"):
                    Listing(mode, kind).mapped("unused")

    def test_portable_path_restrictions(self):
        for name in ("a*", "a?", 'a"', "a<", "a>", "a|", "a:", "NUL", "com1.txt", "a/../b", "a/./b", "a//b", "a. ", "a\n"):
            with self.subTest(name=name):
                with self.assertRaises(tool.Refusal):
                    tool.safe_path(name)

    def test_refuse_unmerged_diagnostics(self):
        del self.source.trees[self.source.commit]["diagnostics/deep32_doctor.py"]
        with self.assertRaisesRegex(tool.Refusal, "missing required diagnostics"):
            self.build()

    def test_refuse_missing_license_or_notice_files(self):
        del self.source.trees[self.source.commit][tool.LICENSE_FILES["NOTICE"]]
        with self.assertRaisesRegex(tool.Refusal, "missing license or notice files: NOTICE"):
            self.build()

    def test_license_files_reach_the_site_set_from_the_merged_commit(self):
        files = self.read_output(self.build())
        for name, target in tool.LICENSE_FILES.items():
            self.assertTrue(target.startswith("distrib/site/usr/local/share/doc/d32/"), target)
            self.assertEqual(files[target], ("text of " + name + "\n").encode())

    def test_refuse_missing_bridge(self):
        del self.source.trees[self.source.commit]["model-bridge/d32_model_bridge.py"]
        with self.assertRaisesRegex(tool.Refusal, "missing model-bridge"):
            self.build()

    def test_selftest_is_optional_but_all_or_none(self):
        names = sorted(tool.SELFTEST_DIAGNOSTICS)
        target = names[0]
        self.after[target] = b"# selftest\n"
        self.source = SyntheticSource(self.before, self.after)
        with self.assertRaisesRegex(tool.Refusal, "incomplete selftest"):
            self.build()
        for target in names:
            self.after[target] = b"# selftest\n"
        self.source = SyntheticSource(self.before, self.after)
        report = self.build(name="complete-selftest")
        self.assertTrue(tool.SELFTEST_DIAGNOSTICS <= {row["path"] for row in report["changes"]})

    def test_refuse_existing_output_preserves_it(self):
        destination = self.root / "out"
        destination.mkdir()
        sentinel = destination / "sentinel"
        sentinel.write_bytes(b"preserve")
        with self.assertRaisesRegex(tool.Refusal, "already exist"):
            self.build()
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_pin_audit_lists_conflicts_without_archive(self):
        pin = {name: digest(data) for name, data in self.alpha1.items()}
        pin["ship/delete"] = digest(b"different")
        report = tool.audit_pin(self.source, pin, deletions=self.deletions)
        self.assertEqual([row["path"] for row in report["conflicts"]], ["ship/delete"])
        self.assertFalse(report["archive_verified"])
        self.assertEqual(report["reference_scope"], "merged-only")

    def test_pin_audit_matches_build_counts_and_changes(self):
        audit = self.audit()
        report = self.build()
        self.assertEqual(audit["conflicts"], [])
        self.assertEqual(audit["counts"], {"replaced": 2, "added": 13, "deleted": 1, "kept": 1})
        self.assertEqual(audit["counts"], report["counts"])
        self.assertEqual(audit["changes"], report["changes"])
        self.assertFalse(audit["archive_verified"])
        self.assertEqual(audit["reference_scope"], "merged-only")
        self.assertTrue(report["archive_verified"])
        self.assertEqual(report["reference_scope"], "final-tree")

    def test_pin_audit_accepts_different_mapped_hashes_and_addition_collision(self):
        self.alpha1["ship/replace"] = b"local transform\n"
        self.alpha1["ship/add"] = b"collision\n"
        report = self.audit()
        self.assertEqual(report["conflicts"], [])
        self.assertEqual(report["counts"], {"replaced": 3, "added": 12, "deleted": 1, "kept": 1})

    def test_pin_audit_refuses_missing_deletion(self):
        del self.alpha1["ship/delete"]
        report = self.audit()
        self.assertEqual([row["path"] for row in report["conflicts"]], ["ship/delete"])

    @unittest.skip('lead ruling 2026-11-17: only references that resolve to one of the nine deleted paths refuse; general missing references (e.g. build-time fetched glibc/CPython sources) no longer refuse')

    def test_pin_audit_refuses_missing_merged_reference(self):
        self.after["ship/reference.c"] = b'#include "missing.h"\n'
        self.remap()
        report = self.audit()
        self.assertTrue(report["conflicts"])
        self.assertIn("missing.h", json.dumps(report["conflicts"]))

    def test_pin_audit_cannot_inspect_kept_file_contents(self):
        self.alpha1["generated/legacy.c"] = b'#include "../ship/delete"\n'
        report = self.audit()
        self.assertEqual(report["conflicts"], [])
        self.assertFalse(report["archive_verified"])
        self.assertEqual(report["reference_scope"], "merged-only")
        with self.assertRaises(tool.Refusal):
            self.build()

    def test_default_deletion_table_matches_lead_ruling(self):
        expected = {
            "library/screens/cisco.scr": "6daa8c48167cc876d4481f0d14c0ac9ae6ffe0bb789289421de16387f189cae4",
            "library/src/app/cisco_catalogue.c": "50678eb4c878d43b2686f9580aca727932ae7756fdb4295cea5f6359cf0ff86b",
            "library/src/app/cisco_catalogue.h": "821177b5bb4035dfd56a7c11dd66cac0eecfee3eb1b602cee9bdeac622989a23",
            "library/src/app/e26.h": "049e9ba00f3dd633dd18b6c43a3a0c8b089a06e32277e3c3c0d0d996c69a8f52",
            "library/src/app/room_cisco.c": "c7e9d9c527d1c0a2f937464444a9c017ab678a5520b8dd1e50b1d0e0625cd904",
            "library/src/app/room_cisco.h": "0d01ada2a1509f549270290fc47274a609f1055b69890d4a5e56e861e3057c80",
            "library/src/lib/cisco_browse.c": "d624e0521f265a5e05a14505f13dfa8ae3e3f03b45e75ed10be549fbb859a680",
            "library/src/lib/cisco_route.h": "f1657bde99e358e6f74853d3c0dd9e7708438b3446f40e9b4b32cd745eaef5b5",
            "library/src/lib/q_cisco.c": "693beb70bf5e482d249460890ae540a0f8ef5df46caad19eb5bfcf9eb67e1d8e",
        }
        self.assertEqual(tool.D1_FC_DELETIONS, expected)
        self.assertEqual(tool.deletion_table(tool.pin_bytes(expected)), expected)
        pin = {name: digest(data) for name, data in self.alpha1.items()} | expected
        report = tool.audit_pin(self.source, pin)
        self.assertEqual(report["conflicts"], [])
        self.assertEqual(report["counts"], {"replaced": 2, "added": 13, "deleted": 9, "kept": 2})
        self.assertEqual({row["path"] for row in report["changes"] if row["action"] == "delete"}, set(expected))

    def test_refuse_nonexact_lead_deletion_table(self):
        expected = dict(tool.D1_FC_DELETIONS)
        name = sorted(expected)[0]
        for table in ({key: value for key, value in expected.items() if key != name},
                      {**expected, name: "0" * 64},
                      {**expected, "unapproved/delete": "0" * 64}):
            with self.subTest(table=table):
                with self.assertRaisesRegex(tool.Refusal, "exactly match ruling D1-FC"):
                    tool.deletion_table(tool.pin_bytes(table))



@unittest.skipUnless(NODE, "node is not installed")
class ThemeRegenerationTests(unittest.TestCase):
    """The theme output is regenerated from the final tree; only THEME_REGENERATED paths may change."""

    COPY = "etc/dt/config/C/d32.copyright"
    archive, build, read_output, remap = TreeTests.archive, TreeTests.build, TreeTests.read_output, TreeTests.remap

    def setUp(self):
        TreeTests.setUp(self)
        old_text, other = b"old dthello text\n", b"other asset\n"
        self.alpha1.update({
            "theme/out/all/root/MANIFEST.tsv": b"old manifest\n",
            "theme/out/all/root/" + self.COPY: old_text,
            "theme/out/all/root/keep": b"same\n",
            "theme/out/all/test/MANIFEST.tsv": b"old test manifest\n",
            "assets/root/" + self.COPY: old_text,
            "assets/root/other": other,
            "assets/SHA256SUMS": f"{digest(old_text)}  {self.COPY}\n{digest(other)}  other\n".encode(),
            "assets/LEDGER-v2.tsv": self.ledger(digest(old_text), digest(other)),
        })
        self.after["theme/tools/gen.js"] = FAKE_GENERATOR
        self.plan({"root/MANIFEST.tsv": "new manifest\n", "root/" + self.COPY: "new dthello text\n",
                   "root/keep": "same\n", "test/MANIFEST.tsv": "old test manifest\n"})

    @staticmethod
    def ledger(copy_hash, other_hash):
        header = ["installed_path", "sha256", "origin", "permission_row", "copy", "store_path", "golden_path",
                  "install", "licence_key", "source", "producer", "origin_detail"]
        rows = [["/etc/dt/config/C/d32.copyright", "0" * 64, "HELD", "", "no", "", "", "", "", "superseded", "", ""],
                ["/etc/dt/config/C/d32.copyright", copy_hash, "SHIP-HP (laptop fetch 2026-11-15)", "13", "yes",
                 "laptop-fetch-store/" + copy_hash, "/etc/dt/config/C/d32.copyright", "copy", "LAPTOP-FETCH",
                 "laptop fetch 2026-11-15; REVERT", "", ""],
                ["/other", other_hash, "SHIP-OWN", "none", "yes", "store/other", "/other", "copy", "OWN-ART", "x", "", ""]]
        return ("\n".join("\t".join(row) for row in [header] + rows) + "\n").encode()

    def plan(self, files, **extra):
        self.after["theme/src/plan.json"] = json.dumps({"files": files, **extra}).encode()
        self.remap()

    def test_regenerated_output_and_asset_copy_are_repinned(self):
        report = self.build(node=NODE)
        files = self.read_output(report)
        new = b"new dthello text\n"
        self.assertEqual(files["theme/out/all/root/MANIFEST.tsv"], b"new manifest\n")
        self.assertEqual(files["theme/out/all/root/" + self.COPY], new)
        self.assertEqual(files["theme/out/all/root/keep"], b"same\n")
        self.assertEqual(files["assets/root/" + self.COPY], new)
        self.assertEqual(files["assets/SHA256SUMS"],
                         f"{digest(new)}  {self.COPY}\n{digest(b'other asset' + chr(10).encode())}  other\n".encode())
        rows = [line.split("\t") for line in files["assets/LEDGER-v2.tsv"].decode().splitlines()]
        header = rows[0]
        live = dict(zip(header, rows[2]))
        self.assertEqual(rows[1][2], "HELD")
        self.assertEqual(rows[3], self.ledger("x", digest(b"other asset\n")).decode().splitlines()[3].split("\t"))
        self.assertEqual((live["sha256"], live["origin"], live["permission_row"], live["store_path"],
                          live["licence_key"], live["source"], live["copy"], live["install"]),
                         (digest(new), "SHIP-OWN", "none", "", "OWN-CODE", "public:theme:root/" + self.COPY,
                          "yes", "copy"))
        actions = {row["path"]: row["action"] for row in report["changes"]
                   if row["action"].startswith("theme-")}
        self.assertEqual(actions, {"theme/out/all/root/MANIFEST.tsv": "theme-regenerated",
                                   "theme/out/all/root/" + self.COPY: "theme-regenerated",
                                   "assets/root/" + self.COPY: "theme-asset-copy",
                                   "assets/SHA256SUMS": "theme-asset-copy",
                                   "assets/LEDGER-v2.tsv": "theme-asset-copy"})
        for row in report["changes"]:
            if row["path"] in actions:
                self.assertEqual(row["before_sha256"], digest(self.alpha1[row["path"]]))
                self.assertEqual(row["after_sha256"], digest(files[row["path"]]))
        pin = (self.root / "out" / "PIN.sha256").read_bytes()
        self.assertEqual(tool.parse_pin(pin), {name: digest(data) for name, data in files.items()})

    def test_regeneration_is_deterministic(self):
        first = self.build(name="first", node=NODE)
        second = self.build(name="second", node=NODE)
        self.assertEqual(first["tree_hash"], second["tree_hash"])

    def test_refuse_theme_output_without_node(self):
        with self.assertRaisesRegex(tool.Refusal, "give --node"):
            self.build()
        self.assertFalse((self.root / "out").exists())

    def test_refuse_difference_outside_expected_paths(self):
        self.plan({"root/MANIFEST.tsv": "new manifest\n", "root/" + self.COPY: "new dthello text\n",
                   "root/keep": "changed\n", "test/MANIFEST.tsv": "old test manifest\n"})
        with self.assertRaisesRegex(tool.Refusal, "differs outside the expected paths .*: theme/out/all/root/keep"):
            self.build(node=NODE)

    def test_refuse_regeneration_that_adds_or_drops_files(self):
        for files in ({"root/MANIFEST.tsv": "m\n", "root/" + self.COPY: "t\n", "test/MANIFEST.tsv": "x\n"},
                      {"root/MANIFEST.tsv": "m\n", "root/" + self.COPY: "t\n", "root/keep": "same\n",
                       "test/MANIFEST.tsv": "x\n", "root/extra": "e\n"}):
            with self.subTest(files=sorted(files)):
                self.plan(files)
                with self.assertRaisesRegex(tool.Refusal, "adds or drops files"):
                    self.build(node=NODE)

    def test_refuse_generator_failure_and_writes_outside_its_output(self):
        files = {"root/MANIFEST.tsv": "m\n", "root/" + self.COPY: "t\n", "root/keep": "same\n",
                 "test/MANIFEST.tsv": "x\n"}
        for extra, message in (({"exit": 3}, "failed \\(exit 3\\)"),
                               ({"extra": {"stray.txt": "x"}}, "wrote outside theme/out/all: theme/stray.txt"),
                               ({"extra": {"tools/gen.js": "changed"}}, "changed or removed a source: theme/tools/gen.js")):
            with self.subTest(extra=extra):
                self.plan(files, **extra)
                with self.assertRaisesRegex(tool.Refusal, message):
                    self.build(node=NODE)

    def test_generator_runs_without_node_options_or_build_switches(self):
        with mock.patch.dict(os.environ, {"NODE_OPTIONS": "--require ./missing.js", "D32_PRIVATE_OUT": "x"}):
            self.build(node=NODE)

    def test_refuse_asset_copy_that_did_not_mirror_the_theme(self):
        self.alpha1["assets/root/" + self.COPY] = b"something else\n"
        with self.assertRaisesRegex(tool.Refusal, "does not mirror the alpha.1 theme output"):
            self.build(node=NODE)

    def test_refuse_ledger_without_one_live_row(self):
        ledger = self.alpha1["assets/LEDGER-v2.tsv"].decode().splitlines()
        self.alpha1["assets/LEDGER-v2.tsv"] = ("\n".join(ledger[:2] + ledger[3:]) + "\n").encode()
        with self.assertRaisesRegex(tool.Refusal, "expected one live row"):
            self.build(node=NODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
