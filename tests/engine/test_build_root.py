import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

import httpx
from qdrant_client import QdrantClient

from rag_core.paths import RAGPaths
from rag_core.retrieval_types import QueryVariant
from rag_core.retrievers.lexical import LexicalRetriever

REPO = engine_target.REPO
SCRIPT = REPO / "scripts" / "build_root.py"

RFC = """\
Network Working Group                                          J. Doe
Request for Comments: 9999                                    Example
Category: Standards Track                                  March 2026


                  The Test Translator Election Protocol

Abstract

   This document describes how a border router elects the translator
   of Type-7 LSAs into Type-5 LSAs for a not-so-stubby area.

1.  Introduction

   A border router defers when another border router has the Nt bit
   set or a higher router ID, and it waits for the stability interval
   before it stops translating.

2.  Operation

   The elected translator originates one Type-5 LSA for each Type-7
   LSA whose P-bit is set and whose forwarding address is not zero.
"""

RFC_SMALL = """\
Network Working Group                                          J. Doe
Request for Comments: 9998                                    Example

                       A Second Small Test Document

1.  Scope

   The second document exists so that a build has more than one RFC
   and a resumed build can skip one while it rebuilds another.
"""

CISCO = """\
# NSSA Configuration Guide
## Restrictions for NSSA
The translate type7 always keyword makes the router the translator regardless of the election.
## Configuring NSSA Translation
```
router ospf 1
 area 1 nssa translate type7 always
```
Use show ip ospf to verify that the router performs type-7/type-5 LSA translation.
"""

DOCS = {
    "rfc/rfc9999.txt": ("The Test Translator Election Protocol", "rfc", RFC),
    "rfc/rfc9998.txt": ("A Second Small Test Document", "rfc", RFC_SMALL),
    "cisco/test-nssa-guide.txt": ("NSSA Configuration Guide (test)", "config_guide", CISCO),
}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def make_corpus(repo: Path, docs=DOCS, bad_sha: str | None = None, kb: dict | None = None) -> None:
    repo.mkdir(parents=True)
    for name, data in (kb or {}).items():              # kb/<name>.json, as the corpus package carries
        (repo / "kb").mkdir(exist_ok=True)
        (repo / "kb" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    manifest = []
    for rel, (title, doc_type, text) in docs.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        data = path.read_bytes()
        key = Path(rel).stem
        manifest.append({"doc_key": key, "title": title, "doc_type": doc_type, "path": rel,
                         "source_url": f"https://example.invalid/{key}", "platform": None, "release": None,
                         "sha256": "f" * 64 if key == bad_sha else hashlib.sha256(data).hexdigest(),
                         "bytes": len(data)})
    (repo / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    git(repo, "init", "-q", "-b", "corpus-v1")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "corpus")


def load_script():
    spec = importlib.util.spec_from_file_location("build_root", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildRootTests(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)
        self.corpus = self.tmp / "corpus"
        self.root = self.tmp / "rag-data" / "gen-test"

    def build(self, *args, env=None, root=None):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        clean.update(PYTHONPATH=str(REPO / "src"), **(env or {}))
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--corpus-ref", "corpus-v1", "--corpus-repo", str(self.corpus),
             "--root", str(root or self.root), *args],
            capture_output=True, text=True, env=clean, timeout=300)

    def report(self):
        return json.loads((self.root / "BUILD_REPORT.json").read_text(encoding="utf-8"))

    def db(self):
        conn = sqlite3.connect(self.root / "sqlite" / "rag.db")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def test_builds_a_bm25_root_with_manifest_metadata_and_resumes(self):
        make_corpus(self.corpus)
        run = self.build("--no-dense")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("doc 1/3", run.stdout)                                  # progress lines
        self.assertIn("RAG_ROOT is not set here, so the served-root guard is inactive", run.stdout)

        report = self.report()
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["generation"], "gen-test")
        self.assertEqual(report["documents"]["built_this_run"], 3)
        self.assertEqual(report["documents"]["failed"], [])
        self.assertEqual(report["chunker"], {"policy": "v3", "version": "17c-v3.1"})
        self.assertEqual(report["corpus"]["commit"], git(self.corpus, "rev-parse", "HEAD"))
        self.assertEqual(report["corpus"]["fingerprint"], report["corpus"]["manifest_fingerprint"])
        self.assertEqual(report["dense"], {"skipped": "--no-dense"})
        self.assertEqual(report["kb"], {"status": "skipped", "reason": "the corpus has no kb/ directory"})
        self.assertGreater(report["chunks"], 3)
        structure = report["structure"]
        self.assertEqual((structure["documents"], structure["no_source"], structure["duplicate_key"]), (3, [], []))
        self.assertEqual(structure["chunks"], report["chunks"])
        self.assertEqual(structure["chunks_located"], report["chunks"])      # every chunk placed in its source
        sections = {r[0] for r in self.db().execute("SELECT section_id FROM doc_sections")}
        self.assertLessEqual({"rfc9999#abstract", "rfc9999#1", "rfc9999#2", "rfc9998#1",
                              "test-nssa-guide#configuring-nssa-translation"}, sections)

        config = self.root / "config"
        self.assertEqual(json.loads((config / "dense.json").read_text())["collection"], "rag_dense_gen-test")
        retrieval = json.loads((config / "retrieval.json").read_text())
        self.assertEqual(retrieval["retrievers"], ["bm25"])
        self.assertEqual(retrieval["technical_variant_retrievers"], ["bm25"])
        self.assertFalse(retrieval["reranker"]["enabled"])
        self.assertNotIn("opt_in_doc_types", retrieval)                    # unset unless asked for
        self.assertNotIn("opt_in_doc_types", report["config"])
        serving = json.loads((config / "serving.json").read_text())
        self.assertEqual((serving["generation"], serving["max_concurrent_searches"]), ("gen-test", 2))
        self.assertTrue((self.root / "sources" / "manifest.json").is_file())
        self.assertFalse((self.root / "work").exists())

        rows = {r["source_id"]: dict(r) for r in self.db().execute("SELECT * FROM documents")}
        self.assertEqual(rows["rfc9999"]["title"], "RFC 9999: The Test Translator Election Protocol")
        self.assertEqual(rows["test-nssa-guide"]["title"], "NSSA Configuration Guide (test)")
        self.assertEqual(rows["test-nssa-guide"]["source_uri"], "https://example.invalid/test-nssa-guide")
        self.assertEqual(json.loads(rows["test-nssa-guide"]["metadata_json"])["corpus"]["doc_type"], "config_guide")
        embedding = self.db().execute("SELECT embedding_text FROM chunks WHERE doc_id = ? ORDER BY ordinal",
                                      (rows["rfc9999"]["doc_id"],)).fetchone()[0]
        self.assertTrue(embedding.startswith("Document: RFC 9999: The Test Translator Election Protocol"))

        hits = LexicalRetriever(RAGPaths(self.root)).search(
            QueryVariant(variant_id="p", purpose="primary", text="translator election Nt bit"), limit=3)
        self.assertEqual(hits[0].doc_id, rows["rfc9999"]["doc_id"])

        again = self.build("--no-dense")                                      # no --resume: refused
        self.assertEqual(again.returncode, 2)
        self.assertIn("--resume", again.stdout)
        self.assertEqual(self.report()["runs"], 1)

        resumed = self.build("--no-dense", "--resume")
        self.assertEqual(resumed.returncode, 0, resumed.stdout)
        report = self.report()
        self.assertEqual((report["documents"]["built_this_run"], report["documents"]["already_done"]), (0, 3))
        self.assertEqual(report["runs"], 2)
        self.assertEqual((report["structure"]["indexed_this_run"], report["structure"]["unchanged"]), (0, 3))

    def test_opt_in_doc_types_go_into_the_root_overlay(self):
        from rag_core.api_v2 import opt_in_doc_types

        make_corpus(self.corpus)
        run = self.build("--no-dense", "--opt-in-doc-types", "system, powershell,system")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("opt-in doc types system,powershell", run.stdout)
        retrieval = json.loads((self.root / "config" / "retrieval.json").read_text())
        self.assertEqual(retrieval["opt_in_doc_types"], ["system", "powershell"])
        self.assertEqual(opt_in_doc_types(retrieval), frozenset({"system", "powershell"}))     # deep32-engine starts
        self.assertEqual(self.report()["config"]["opt_in_doc_types"], ["system", "powershell"])
        bad_root = self.tmp / "rag-data" / "gen-bad"
        for bad in ("system,,powershell", "x" * 33):
            refused = self.build("--no-dense", "--opt-in-doc-types", bad, root=bad_root)
            self.assertEqual(refused.returncode, 2, bad)
            self.assertIn("--opt-in-doc-types", refused.stdout)
        self.assertFalse(bad_root.exists())

    def test_a_resume_must_give_the_opt_in_doc_types_of_the_run_it_resumes(self):
        # the config step rewrites the overlay on every run: a resume typed without the flag (after a failed dense
        # step, say) would drop the key, and the root would serve the opt-in documents to every default search
        make_corpus(self.corpus)
        self.assertEqual(self.build("--no-dense", "--opt-in-doc-types", "powershell,system").returncode, 0)
        before, overlay = self.report(), (self.root / "config" / "retrieval.json").read_text()
        for args in ((), ("--opt-in-doc-types", "powershell"), ("--opt-in-doc-types", "powershell,system,rfc")):
            refused = self.build("--no-dense", "--resume", *args)
            self.assertEqual(refused.returncode, 2, args)
            self.assertIn("configured with --opt-in-doc-types powershell,system; resume it the same way",
                          refused.stdout)
        self.assertEqual(self.report(), before)
        self.assertEqual((self.root / "config" / "retrieval.json").read_text(), overlay)
        resumed = self.build("--no-dense", "--resume", "--opt-in-doc-types", "system,powershell")    # any order
        self.assertEqual(resumed.returncode, 0, resumed.stdout)
        retrieval = json.loads((self.root / "config" / "retrieval.json").read_text())
        self.assertEqual(set(retrieval["opt_in_doc_types"]), {"powershell", "system"})
        self.assertEqual(set(self.report()["config"]["opt_in_doc_types"]), {"powershell", "system"})
        # a root built without them is resumed without them
        plain = self.tmp / "rag-data" / "gen-plain"
        self.assertEqual(self.build("--no-dense", root=plain).returncode, 0)
        refused = self.build("--no-dense", "--resume", "--opt-in-doc-types", "powershell", root=plain)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("configured with no --opt-in-doc-types; resume it the same way", refused.stdout)
        self.assertNotIn("opt_in_doc_types", json.loads((plain / "config" / "retrieval.json").read_text()))

    def test_resume_redoes_only_the_unfinished_document(self):
        make_corpus(self.corpus)
        self.assertEqual(self.build("--no-dense").returncode, 0)
        chunks = self.report()["chunks"]
        conn = self.db()
        conn.execute("PRAGMA foreign_keys = ON")                              # as rag_core.db.connect does
        doc_id = conn.execute("SELECT doc_id FROM documents WHERE source_id = 'rfc9998'").fetchone()[0]
        with conn:                                                            # as if stopped after ingest
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("UPDATE documents SET status = 'CANONICALIZED' WHERE doc_id = ?", (doc_id,))
        run = self.build("--no-dense", "--resume")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("rfc9998", run.stdout)
        self.assertNotIn("doc 1/3 rfc9999", run.stdout)
        report = self.report()
        self.assertEqual((report["documents"]["built_this_run"], report["documents"]["already_done"]), (1, 2))
        self.assertEqual(report["chunks"], chunks)

    def test_table_header_repeat_is_a_setting_of_the_build(self):
        caption = "Table 2. Feature Information for RADIUS Attributes Field Descriptions"
        rows = [f"attr{i} | The value of attribute {i} on the system. The format is String." for i in range(150)]
        guide = "\n".join(["# RADIUS Attributes Guide", "## show radius table attributes",
                           "To display the attributes, use the show radius table attributes command.",
                           caption, "Field | Description", *rows, ""])
        make_corpus(self.corpus, {**DOCS, "cisco/test-radius-guide.txt": ("RADIUS (test)", "config_guide", guide)})
        line = {text: n for n, text in enumerate(guide.split("\n"), 1)}

        def table_chunks(root):                 # the chunks that hold rows of the table
            conn = sqlite3.connect(root / "sqlite" / "rag.db")
            self.addCleanup(conn.close)
            return [r for r in conn.execute(
                "SELECT c.text, c.metadata_json, s.line_start, s.line_end FROM chunks c "
                "JOIN documents d ON d.doc_id = c.doc_id LEFT JOIN chunk_spans s ON s.chunk_id = c.chunk_id "
                "WHERE d.source_id = 'test-radius-guide' ORDER BY c.ordinal") if re.search(r"(?m)^attr\d+ \|", r[0])]

        plain = self.tmp / "rag-data" / "gen-plain"
        self.assertEqual(self.build("--no-dense", root=plain).returncode, 0)
        self.assertNotIn("table_header_repeat", json.loads((plain / "BUILD_REPORT.json").read_text())["chunker"])
        before = table_chunks(plain)
        self.assertTrue(before[1][0].startswith("attr"))                     # a continuation starts with a row
        self.assertFalse(any("context_chars" in json.loads(meta) for _, meta, _, _ in before))

        run = self.build("--no-dense", "--table-header-repeat")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        report = self.report()
        self.assertEqual(report["chunker"], {"policy": "v3", "version": "17c-v3.1", "table_header_repeat": True})
        self.assertEqual(report["structure"]["chunks_located"], report["chunks"])
        after = table_chunks(self.root)
        self.assertGreaterEqual(len(after), 3)
        context = f"{caption}\nField | Description\n"
        for text, meta, start, end in after[1:]:
            self.assertTrue(text.startswith(context), text[:100])
            self.assertEqual(json.loads(meta)["context_chars"], len(context))
            first, last = text[len(context):].split("\n")[0], text.split("\n")[-1]
            self.assertEqual((start, end), (line[first], line[last]))         # its own rows, not the caption's line
        self.assertEqual([r for text, *_ in after for r in text.split("\n") if r.startswith("attr")], rows)

        # a resume must chunk the rest as the run it resumes did
        for args in (("--resume",), ("--resume", "--table-header-repeat")):
            refused = self.build("--no-dense", *args, root=plain if len(args) == 2 else None)
            self.assertEqual(refused.returncode, 2, args)
            self.assertIn("--table-header-repeat; resume it the same way", refused.stdout)
        self.assertEqual(self.build("--no-dense", "--resume", "--table-header-repeat").returncode, 0)

    def test_the_setting_is_on_disk_before_a_document_is_chunked(self):
        # a run stopped while chunking leaves a report that a resume is checked against
        make_corpus(self.corpus)
        script = load_script()
        clean = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)        # step_sources' git archive pipe, in process
        with mock.patch.dict(os.environ, clean, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(script, "step_documents", side_effect=RuntimeError("stopped")):
            rc = script.main(["--corpus-ref", "corpus-v1", "--corpus-repo", str(self.corpus), "--root",
                              str(self.root), "--no-dense", "--table-header-repeat"])
        self.assertEqual(rc, 1)
        self.assertNotIn("documents", self.report())
        self.assertTrue(self.report()["chunker"]["table_header_repeat"])
        refused = self.build("--no-dense", "--resume")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("chunked with --table-header-repeat", refused.stdout)

    @engine_target.POSIX_ONLY
    def test_kb_rows_resolve_to_sections_of_the_built_root(self):
        command_line = CISCO.split("\n").index(" area 1 nssa translate type7 always") + 1
        cisco_sha = hashlib.sha256(CISCO.encode()).hexdigest()
        make_corpus(self.corpus, kb={
            "rfc_index": [{"rfc": 9999, "title": "The Test Translator Election Protocol",
                           "status": "Proposed Standard", "date": "2026-03", "obsoletes": [],
                           "obsoleted_by": [], "updates": [], "updated_by": [], "also": [],
                           "in_corpus": True}],
            "cli_index": [{"command": "area <area-id> nssa translate type7 always", "mode": "router",
                           "platform": "iosxe", "doc_key": "test-nssa-guide", "line": command_line,
                           "kind": "example", "release": None}],
            "cli_index.meta": {"sources": [{"doc_key": "test-nssa-guide", "sha256": cisco_sha}]},
        })
        run = self.build("--no-dense")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        report = self.report()
        self.assertEqual(report["kb"]["status"], "built")
        counts = report["kb"]["counts"]
        self.assertEqual((counts["rfc"], counts["cli"], counts["cli_resolved"]), (1, 1, 1))
        self.assertTrue(counts["sections_available"])
        with sqlite3.connect(self.root / "kb" / "kb.sqlite") as kb:
            section_id = kb.execute("SELECT section_id FROM cli").fetchone()[0]
        self.assertEqual(section_id, "test-nssa-guide#configuring-nssa-translation")
        # the KB step runs after structure; a resume rebuilds it from the same sections
        resumed = self.build("--no-dense", "--resume")
        self.assertEqual(resumed.returncode, 0, resumed.stdout)
        self.assertEqual(self.report()["kb"]["counts"]["cli_resolved"], 1)

    def test_a_bad_document_is_reported_and_the_rest_build(self):
        make_corpus(self.corpus, bad_sha="rfc9998")
        run = self.build("--no-dense")
        self.assertEqual(run.returncode, 1)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual([f["doc_key"] for f in report["documents"]["failed"]], ["rfc9998"])
        self.assertIn("sha256", report["documents"]["failed"][0]["error"])
        self.assertEqual(report["documents"]["built_this_run"], 2)

    def test_refusals_write_nothing(self):
        make_corpus(self.corpus)
        served = self.tmp / "rag-data"
        refused = self.build("--no-dense", env={"RAG_ROOT": str(served)})
        self.assertEqual(refused.returncode, 2)
        self.assertIn(f"served-root guard: RAG_ROOT is {served.resolve()}", refused.stdout)
        self.assertIn("the served root", refused.stdout)
        self.assertEqual(self.build("--no-dense", "--generation", "v1").returncode, 2)
        # names the managed RAG_ROOT pattern cannot take ([a-z0-9][a-z0-9._-]{0,39}): such a root
        # could never be promoted with env-set
        for generation in ("Gen-2026", "g" * 41, "gen-1\n"):
            self.assertEqual(self.build("--no-dense", "--generation", generation).returncode, 2,
                             generation)
        self.assertFalse(self.root.exists())
        upper = self.tmp / "rag-data" / "Gen-2026"
        self.assertEqual(self.build("--no-dense", "--generation", "gen-2026", root=upper).returncode, 2)
        self.assertFalse(upper.exists())

        foreign = self.tmp / "foreign"
        foreign.mkdir()
        (foreign / "rag.db").write_text("not ours")
        self.assertEqual(self.build("--no-dense", root=foreign).returncode, 2)
        self.assertEqual(sorted(p.name for p in foreign.iterdir()), ["rag.db"])

        self.assertEqual(self.build("--no-dense").returncode, 0)
        (self.corpus / "rfc" / "rfc9998.txt").write_text(RFC_SMALL + "\nchanged\n", encoding="utf-8")
        git(self.corpus, "commit", "-q", "-am", "moved")
        before = self.report()
        run = self.build("--no-dense", "--resume")
        self.assertEqual(run.returncode, 2)
        self.assertIn("build a new generation", run.stdout)
        self.assertEqual(self.report(), before)


class Embedder:
    def __init__(self, size, fail_first=0, error=None):
        self.size, self.fail_first, self.calls = size, fail_first, 0
        self.error = error or httpx.ConnectError("refused", request=httpx.Request("POST", "http://gpu/v1"))

    def embed(self, texts):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise self.error
        vectors = [[float((len(t) + i) % 7 + 1)] * self.size for i, t in enumerate(texts)]
        return vectors, "test-embedder"


class DenseStepTests(unittest.TestCase):
    """The dense step against Qdrant's in-process local mode: retry, resume and verification."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        make_corpus(tmp / "corpus")
        cls.template = tmp / "template"
        env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
        env["PYTHONPATH"] = str(REPO / "src")
        subprocess.run([sys.executable, str(SCRIPT), "--corpus-ref", "corpus-v1", "--corpus-repo",
                        str(tmp / "corpus"), "--root", str(cls.template), "--no-dense"],
                       check=True, capture_output=True, env=env, timeout=300)
        cls.script = load_script()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name) / "root"
        shutil.copytree(self.template, root)
        self.db_path = root / "sqlite" / "rag.db"
        base = self.script.load_dense_config(REPO / "config" / "dense.json")
        self.config = self.script.DenseConfig(**{**base.__dict__, "collection": "rag_dense_gen-test",
                                                 "batch_size": 2, "embedding_revision": None})
        self.client = QdrantClient(location=":memory:")
        self.addCleanup(self.client.close)
        self.sleeps = []
        self.enterContext(mock.patch.object(self.script, "log"))            # progress lines
        self.enterContext(warnings.catch_warnings())
        warnings.filterwarnings("ignore", "Payload indexes have no effect", UserWarning)

    def index(self, embedder):
        return self.script.index_dense_resumable(self.db_path, self.config, embedder, self.client,
                                                 sleep=self.sleeps.append)

    def chunks(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def test_retries_transient_failures_then_resumes_with_nothing_left(self):
        embedder = Embedder(self.config.vector_size, fail_first=2)
        result = self.index(embedder)
        self.assertEqual(result["points"], self.chunks())
        self.assertEqual(result["chunks_indexed_this_run"], self.chunks())
        self.assertEqual(result["embedding_model"], "test-embedder")
        self.assertEqual(self.sleeps, [2.0, 4.0])                              # exponential backoff

        again = Embedder(self.config.vector_size)
        result = self.index(again)
        self.assertEqual((result["chunks_indexed_this_run"], again.calls), (0, 0))
        self.assertEqual(result["points"], self.chunks())

    def test_a_stopped_run_resumes_per_document(self):
        with sqlite3.connect(self.db_path) as conn:
            first_doc = conn.execute("SELECT doc_id FROM chunks ORDER BY doc_id LIMIT 1").fetchone()[0]
        self.index(Embedder(self.config.vector_size))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE index_state SET dense_indexed = 0 WHERE chunk_id IN "
                         "(SELECT chunk_id FROM chunks WHERE doc_id = ?)", (first_doc,))
            redo = conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (first_doc,)).fetchone()[0]
        result = self.index(Embedder(self.config.vector_size))
        self.assertEqual(result["chunks_indexed_this_run"], redo)
        self.assertEqual(result["points"], self.chunks())                     # no duplicate points

    def test_gives_up_after_the_attempts_and_does_not_retry_bad_responses(self):
        with self.assertRaises(httpx.ConnectError):
            self.index(Embedder(self.config.vector_size, fail_first=99))
        self.assertEqual(len(self.sleeps), self.script.DENSE_ATTEMPTS - 1)
        self.sleeps.clear()
        with self.assertRaises(RuntimeError):
            self.index(Embedder(self.config.vector_size, fail_first=1, error=RuntimeError("bad dimension")))
        self.assertEqual(self.sleeps, [])

    def test_refuses_a_second_embedding_model(self):
        self.index(Embedder(self.config.vector_size))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE index_state SET dense_indexed = 0 WHERE rowid = (SELECT MIN(rowid) FROM index_state)")

        class Other(Embedder):
            def embed(self, texts):
                return super().embed(texts)[0], "another-model"
        with self.assertRaises(RuntimeError):
            self.index(Other(self.config.vector_size))


if __name__ == "__main__":
    unittest.main()
