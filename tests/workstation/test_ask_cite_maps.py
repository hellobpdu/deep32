"""Offline C5 citation-map binding regressions using generated public fixtures."""
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "src/workstation/ask-client/bridge/d32-ask"
IMPORTERS = (
    ROOT / "src/workstation/distrib/site/usr/local/d32/libexec/d32-import",
    ROOT / "src/gpu-pack/import",
    ROOT / "src/build/s5-build-driver/slots/t0120/902-disc2-importer-sources/distrib/site/usr/local/d32/libexec/d32-import",
)


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


ASK = load("ask_c5_test", BRIDGE)
sys.path.insert(0, str(IMPORTERS[0]))
UNITS = load("ask_import_units_test", IMPORTERS[0] / "imp_units.py")
sys.path.pop(0)
FP = "0123456789abcdef"
STALE = "fedcba9876543210"


class CitationMaps(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-ask-map-", dir=os.environ.get("D32_TEST_WORK"))
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.content = self.work / "content"
        self.records = self.work / "packs"
        self.bundle = self.content / "kb/BUNDLE.json"
        self.bundle.parent.mkdir(parents=True)
        self.set_bundle(FP)
        self.paths = []

    def set_bundle(self, fingerprint):
        self.bundle.write_text(json.dumps({"generation": "kb-g1", "fingerprint": fingerprint}), encoding="ascii")

    def add_map(self, pack="lib-corpus", fingerprint=FP, title="Synthetic document"):
        generation = "reader-g1"
        prefix = "." + pack + "-" + generation
        path = self.content / prefix / "cite-map.tsv"
        path.parent.mkdir(parents=True)
        raw = ("\t".join(ASK.MAP_COLUMNS) + "\n" +
               f"synthetic-doc\t*\t{pack}\tdoc-1\t\t{title}\n" +
               f"synthetic-doc\tsec1\t{pack}\tdoc-1\ts1\t{title} section\n").encode("utf-8")
        path.write_bytes(raw)
        folder = self.records / ASK.PACK_RECORDS[pack] / generation
        folder.mkdir(parents=True)
        info = (f"name={ASK.PACK_RECORDS[pack]}\nclass=library\ngeneration={generation}\n"
                f"kb_generation=kb-g1\nkb_fingerprint={fingerprint}\n")
        (folder / "INFO").write_text(info, encoding="ascii")
        manifest = (f"d32/libcontent/{prefix}/cite-map.tsv\tf\t0440\troot\t_d32users\t{len(raw)}\t"
                    f"{hashlib.sha256(raw).hexdigest()}\tsynthetic-public\n")
        (folder / "MANIFEST.tsv").write_text(manifest, encoding="ascii")
        self.paths.append((pack, path))
        return path, folder

    def maps(self):
        return ASK.CiteMaps(self.paths, self.content, self.records)

    def app(self, maps, sink=None):
        out = io.StringIO()
        history = SimpleNamespace(save=lambda *args: None)
        args = SimpleNamespace(engine="codex", model=None, link="research", window_auth=False)
        app = ASK.Ask(None, args, out=out, history=history, cite_maps=maps, sink=sink)
        app.started = True
        return app, out

    def test_matching_map_resolves_exact_section_and_document_fallback(self):
        self.add_map()
        maps = self.maps()
        self.assertEqual(maps.notice, "")
        self.assertTrue(maps.resolve(("synthetic-doc", "sec1"))["entry"]["uri"].endswith("#s1"))
        self.assertTrue(maps.resolve(("synthetic-doc", "unknown"))["fallback"])
        self.assertIsNone(maps.resolve(("absent", ""))["entry"])

    def test_stale_map_is_ignored(self):
        self.add_map(fingerprint=STALE)
        maps = self.maps()
        self.assertEqual(maps.entries, {})
        self.assertEqual(maps.notice, ASK.MAP_NOTICE)

    def test_matching_map_still_works_when_another_map_is_stale(self):
        self.add_map(fingerprint=STALE)
        self.add_map("lib-e26")
        maps = self.maps()
        self.assertEqual(maps.resolve(("synthetic-doc", "sec1"))["entry"]["pack"], "lib-e26")
        self.assertEqual(maps.notice, ASK.MAP_NOTICE)

    def test_reader_map_keeps_duplicate_priority(self):
        self.add_map(title="Reader")
        self.add_map("lib-e26", title="Main")
        self.assertEqual(self.maps().resolve(("synthetic-doc", ""))["entry"]["title"], "Reader")

    def test_missing_binding_is_ignored(self):
        _, folder = self.add_map()
        info = folder / "INFO"
        info.write_text(info.read_text().replace("kb_fingerprint=" + FP + "\n", ""), encoding="ascii")
        self.assertEqual(self.maps().entries, {})

    def test_duplicate_binding_is_ignored(self):
        _, folder = self.add_map()
        with (folder / "INFO").open("a", encoding="ascii") as output:
            output.write("kb_fingerprint=" + FP + "\n")
        self.assertEqual(self.maps().entries, {})

    def test_map_hash_must_match_retained_manifest(self):
        path, _ = self.add_map()
        path.write_bytes(path.read_bytes().replace(b"Synthetic", b"Different"))
        self.assertEqual(self.maps().entries, {})

    def test_old_record_for_another_generation_cannot_bind_map(self):
        _, folder = self.add_map()
        record = folder / "MANIFEST.tsv"
        record.write_text(record.read_text().replace("reader-g1", "reader-old"), encoding="ascii")
        self.assertEqual(self.maps().entries, {})

    def test_missing_or_malformed_active_bundle_ignores_map(self):
        self.add_map()
        for raw in (b"[]", b"{}", b"{", b'{"fingerprint":true}'):
            with self.subTest(raw=raw):
                self.bundle.write_bytes(raw)
                self.assertEqual(self.maps().entries, {})
        self.bundle.unlink()
        self.assertEqual(self.maps().entries, {})

    def test_active_bundle_change_invalidates_cached_map(self):
        self.add_map()
        maps = self.maps()
        self.set_bundle(STALE)
        self.assertIsNone(maps.resolve(("synthetic-doc", "sec1"))["entry"])
        self.assertEqual(maps.notice, ASK.MAP_NOTICE)

    def test_bundle_switch_during_map_load_discards_loaded_entries(self):
        self.add_map()
        maps = self.maps()
        original = maps._bound

        def switched(*args):
            result = original(*args)
            self.set_bundle(STALE)
            return result

        with patch.object(maps, "_bound", side_effect=switched):
            maps.reload()
        self.assertEqual(maps.entries, {})
        self.assertEqual(maps.notice, ASK.MAP_NOTICE)

    def test_terminal_answer_completes_with_stale_map_notice(self):
        self.add_map(fingerprint=STALE)
        app, out = self.app(self.maps())
        app.parts = ["A synthetic answer: synthetic-doc#sec1"]
        app.stream = SimpleNamespace(finish=lambda: None)
        app.finish_answer()
        self.assertIn(ASK.MAP_NOTICE, out.getvalue())
        self.assertIn("synthetic-doc#sec1", out.getvalue())
        self.assertIsNone(app.references[0]["entry"])

    def test_window_answer_completes_with_stale_map_notice(self):
        self.add_map(fingerprint=STALE)
        events = []
        app, _ = self.app(self.maps(), SimpleNamespace(emit=lambda *args: events.append(args)))
        app.parts = ["synthetic-doc#sec1"]
        app.stream = SimpleNamespace(finish=lambda: None)
        app.finish_answer()
        self.assertIn(("N", "info", ASK.MAP_NOTICE), events)
        self.assertTrue(any(event[0] == "C" and event[3] == "absent" for event in events))
        self.assertEqual(events[-1][0], "F")

    def test_matching_answer_has_library_reference_without_notice(self):
        self.add_map()
        app, out = self.app(self.maps())
        app.parts = ["synthetic-doc#sec1"]
        app.stream = SimpleNamespace(finish=lambda: None)
        app.finish_answer()
        self.assertIsNotNone(app.references[0]["entry"])
        self.assertNotIn(ASK.MAP_NOTICE, out.getvalue())

    def test_open_rechecks_map_after_active_bundle_change(self):
        self.add_map()
        maps = self.maps()
        app, out = self.app(maps)
        references = [maps.resolve(("synthetic-doc", "sec1"))]
        self.set_bundle(STALE)
        with patch.object(ASK, "open_reference") as opened:
            app.open_number(references, 1)
            opened.assert_not_called()
        self.assertIn(ASK.MAP_NOTICE, out.getvalue())

    def test_open_matching_map_uses_the_verified_library_uri(self):
        self.add_map()
        maps = self.maps()
        app, _ = self.app(maps)
        references = [maps.resolve(("synthetic-doc", "sec1"))]
        with patch.object(ASK, "open_reference") as opened:
            app.open_number(references, 1)
            opened.assert_called_once_with("library:lib-corpus/doc-1#s1")

    def test_refs_rechecks_map_after_active_bundle_change(self):
        self.add_map()
        maps = self.maps()
        app, out = self.app(maps)
        app.references = [maps.resolve(("synthetic-doc", "sec1"))]
        self.set_bundle(STALE)
        app.show_references()
        self.assertIsNone(app.references[0]["entry"])
        self.assertIn(ASK.MAP_NOTICE, out.getvalue())


class ImporterMaps(unittest.TestCase):
    def fixture(self, *, map_present=True, claimed_map=True, fingerprint=FP, bound=True):
        pack, prefix = "lib-corpus", "d32/libcontent/.corpus-reader-g1"
        entry = dict(pack=pack, generation="reader-g1", release="r1", install_prefix=prefix,
                     kb_generation="kb-g1" if bound else None, kb_fingerprint=fingerprint if bound else None,
                     has_cite_map=claimed_map, parts=[dict(path="library/synthetic.part001")])
        raw = b"synthetic-public-map\n"
        info = (f"name={pack}\nclass=library\nrelease=r1\nrights=synthetic-public\n"
                f"files=1\nbytes={len(raw)}\nmade=1\ngeneration=reader-g1\n")
        if bound:
            info += f"kb_generation=kb-g1\nkb_fingerprint={fingerprint}\n"
        info = info.encode("ascii")
        content_name = prefix + ("/cite-map.tsv" if map_present else "/other.txt")
        record_prefix = "var/db/d32/packs/" + pack + "/"

        def member(name, data):
            return dict(name=name, size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=0o440,
                        uname="root", gname="wheel", mtime=1)

        content, info_member = member(content_name, raw), member(record_prefix + "INFO", info)
        manifest = "".join("\t".join((item["name"], "f", "0440", "root", "wheel", str(item["size"]),
                                      item["sha256"], "synthetic-public")) + "\n"
                           for item in (content, info_member)).encode("ascii")
        metadata = [content, member(record_prefix + "MANIFEST.tsv", manifest), info_member]
        ctx = SimpleNamespace(manifest={"kb": {"generation": "kb-g1", "fingerprint": FP}})
        desired = dict(unit_kind="library", streams=[entry], units=[dict(id=pack)])
        return ctx, desired, entry, metadata, {"MANIFEST.tsv": manifest, "INFO": info}

    def test_three_importer_copies_stay_identical(self):
        self.assertEqual(len({(folder / "imp_units.py").read_bytes() for folder in IMPORTERS}), 1)

    def test_matching_map_binding_is_retained_for_ask(self):
        args = self.fixture()
        UNITS._record_checks(*args)
        self.assertIn("kb_fingerprint=" + FP, args[1]["records"]["lib-corpus"]["INFO"])

    def test_stale_map_binding_is_rejected(self):
        with self.assertRaises(UNITS.ImportFailure) as caught:
            UNITS._record_checks(*self.fixture(fingerprint=STALE))
        self.assertEqual(caught.exception.code, "IMPORT_PACK_BINDING")

    def test_unbound_map_is_rejected(self):
        with self.assertRaises(UNITS.ImportFailure) as caught:
            UNITS._record_checks(*self.fixture(bound=False))
        self.assertEqual(caught.exception.code, "IMPORT_PACK_BINDING")

    def test_hidden_map_and_missing_claimed_map_are_rejected(self):
        for present, claimed in ((True, False), (False, True)):
            with self.subTest(present=present, claimed=claimed), self.assertRaises(UNITS.ImportFailure) as caught:
                UNITS._record_checks(*self.fixture(map_present=present, claimed_map=claimed))
            self.assertEqual(caught.exception.code, "IMPORT_PACK_FORM")

    def test_unbound_pack_without_map_remains_accepted(self):
        UNITS._record_checks(*self.fixture(map_present=False, claimed_map=False, bound=False))


if __name__ == "__main__":
    unittest.main()
