import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from rag_core.corpus import (
    apply_manifest_entry,
    corpus_fingerprint,
    display_title,
    load_manifest,
    text_kind,
)
from rag_core.db import connect, initialize
from rag_core.paths import RAGPaths

REPO = engine_target.REPO
SHA = "0" * 64


def entry(**kw):
    return {"doc_key": "rfc3101", "title": "The OSPF Not-So-Stubby Area (NSSA) Option", "doc_type": "rfc",
            "path": "rfc/rfc3101.txt", "source_url": "https://www.rfc-editor.org/rfc/rfc3101.txt",
            "platform": None, "release": None, "sha256": SHA, "bytes": 10, **kw}


class ManifestTests(unittest.TestCase):

    def load(self, entries):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "manifest.json"
            path.write_text(json.dumps(entries), encoding="utf-8")
            return load_manifest(path)

    def test_valid_manifest(self):
        cisco = entry(doc_key="iosxe-17-ospf", title="IP Routing: OSPF Configuration Guide",
                      doc_type="config_guide", path="cisco/iosxe-17-ospf.txt", sha256="1" * 64)
        self.assertEqual([e["doc_key"] for e in self.load([entry(), cisco])], ["rfc3101", "iosxe-17-ospf"])

    def test_rejects_bad_entries(self):
        bad = [
            {"not": "a list"},
            [entry(), entry()],                                          # duplicate doc_key
            [entry(title="")],                                           # missing title
            [entry(path="../rfc3101.txt")],                              # escapes the package
            [entry(path="/etc/rfc3101.txt")],
            [entry(path="rfc/rfc3102.txt")],                             # doc_key is the file stem
            [entry(sha256="xyz")],
            [entry(doc_key="rfc 3101", path="rfc/rfc 3101.txt")],
        ]
        for entries in bad:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                self.load(entries)

    def test_display_title_and_kind(self):
        self.assertEqual(display_title(entry()), "RFC 3101: The OSPF Not-So-Stubby Area (NSSA) Option")
        self.assertEqual(display_title(entry(title="RFC 3101: NSSA")), "RFC 3101: NSSA")
        cisco = entry(doc_key="iosxe-ospf", doc_type="config_guide", title="  OSPF Guide ")
        self.assertEqual(display_title(cisco), "OSPF Guide")
        self.assertEqual((text_kind(entry()), text_kind(cisco)), ("rfc", "cisco"))

    def test_fingerprint_ignores_order(self):
        self.assertEqual(corpus_fingerprint(["b" * 64, "a" * 64]), corpus_fingerprint(["a" * 64, "b" * 64]))
        self.assertNotEqual(corpus_fingerprint(["a" * 64]), corpus_fingerprint(["b" * 64]))
        self.assertEqual(len(corpus_fingerprint(["a" * 64])), 16)


class ApplyManifestTests(unittest.TestCase):

    @engine_target.SCRIPTS_SKIP
    def test_fills_title_doc_key_url_and_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            db = initialize(RAGPaths(Path(d)))
            with mock.patch.dict("os.environ", {"RAG_ROOT": d}), redirect_stdout(StringIO()):
                runpy.run_path(str(REPO / "scripts" / "migrate_17d.py"), run_name="__main__")
            with connect(db) as conn:
                conn.execute("INSERT INTO documents (doc_id, sha256, source_name, original_filename, status, "
                             "metadata_json) VALUES ('doc_1', ?, 'rfc', 'rfc3101.txt', 'CANONICALIZED', ?)",
                             (SHA, json.dumps({"parser": "claude-text-adapter"})))
            apply_manifest_entry(db, "doc_1", entry())
            apply_manifest_entry(db, "doc_1", entry(release="n/a"))          # idempotent; the last entry wins
            with connect(db) as conn:
                row = dict(conn.execute("SELECT * FROM documents").fetchone())
            with self.assertRaises(KeyError):
                apply_manifest_entry(db, "doc_missing", entry())
        self.assertEqual(row["title"], "RFC 3101: The OSPF Not-So-Stubby Area (NSSA) Option")
        self.assertEqual(row["source_id"], "rfc3101")
        self.assertEqual(row["source_uri"], "https://www.rfc-editor.org/rfc/rfc3101.txt")
        metadata = json.loads(row["metadata_json"])
        self.assertEqual(metadata["parser"], "claude-text-adapter")          # ingest metadata kept
        self.assertEqual(metadata["corpus"], entry(release="n/a"))


if __name__ == "__main__":
    unittest.main()
