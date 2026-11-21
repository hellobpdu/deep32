import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rag_core.db import connect
from rag_core.sections import page_furniture, split_lines
from rag_core.structure import (
    STRUCTURE_VERSION, display_lines, has_structure, index_root, infer_release, locate_chunks, located_text,
)

from v2_root import RFC3101, build_root, chunk_id

REPO = engine_target.REPO


RULER = "0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1"


def table_info(conn, table):
    return [tuple(r) for r in conn.execute(f"PRAGMA table_info({table})")]


class LocateChunksTests(unittest.TestCase):

    def test_repeated_openings_and_closings_resolve_to_the_right_place(self):
        lines = [
            "   " + RULER, "   |  NAME=2  |  length  | common name", "",
            "   The NAME item is the real name used to describe the source.", "",
            "   " + RULER, "   |  LOC=5  |  length  | geographic location", "",
            "   The LOC item is the geographic location of the site.", "",
            "   Treat the packet as an unknown unicast packet.", "",
            "   The PE that receives a packet on the P2MP LSP", "",
            "   Treat the packet as an unknown unicast packet.", "",
        ]
        second_diagram = "\n".join([RULER, "| LOC=5 | length | geographic location",
                                    "The LOC item is the geographic location of the site."])
        # starts with the same 32-word ruler as the first diagram
        # ends with a sentence that also closes the paragraph before it
        tail = ("Treat the packet as an unknown unicast packet. The PE that receives a packet "
                "on the P2MP LSP Treat the packet as an unknown unicast packet.")
        spans = locate_chunks(lines, set(), [second_diagram, tail, "not in the source at all"])
        self.assertEqual(spans[0], (6, 9))
        self.assertEqual(spans[1], (11, 15))
        self.assertIsNone(spans[2])

    def test_page_furniture_and_synthetic_headings_are_ignored(self):
        lines = split_lines(RFC3101)
        chunk = ("A border router defers when another border router has bit Nt set or a higher "
                 "router ID.\n\n## 3.2 Translating Type-7 LSAs into Type-5 LSAs\n\nThis step is "
                 "performed as part of the NSSA's Dijkstra calculation. Only installed Type-7 LSAs "
                 "should be examined.")
        (span,) = locate_chunks(lines, page_furniture(lines), [chunk])
        self.assertEqual(lines[span[0] - 1].split()[:3], ["A", "border", "router"])
        self.assertIn("Type-7 LSAs should be examined", lines[span[1] - 1])

    def test_a_chunk_opening_with_its_own_titles_is_placed_by_the_text_after_them(self):
        # chunker v3.2: the tail of "Configuring Layer 2 Protocol Tunneling", merged with the next section, opens
        # with its titles (12 words, TRIES), which stand apart in the source, above the section's first lines
        lines = ["# Layer 2 Guide", "## How to Configure Layer 2 Protocol Tunneling",
                 "This chapter describes how to configure tunneling on the switch.",
                 "### Configuring Layer 2 Protocol Tunneling", "Layer 2 protocol tunneling lets the service provider"
                 " carry the customer's CDP, STP and VTP frames across its network.",
                 "Step 1", "Enables privileged EXEC mode on the device before any configuration.",
                 "Step 12", "Displays the Layer 2 tunnel ports on the device, including the protocols configured,"
                 " the thresholds, and the counters.", "### Monitoring Layer 2 Protocol Tunneling",
                 "Use the show l2protocol command to see the counters of each tunnel port."]
        chunk = ("## How to Configure Layer 2 Protocol Tunneling\n### Configuring Layer 2 Protocol Tunneling\n\n"
                 "Displays the Layer 2 tunnel ports on the device, including the protocols configured, the thresholds,"
                 " and the counters.\n\n### Monitoring Layer 2 Protocol Tunneling\n\nUse the show l2protocol command"
                 " to see the counters of each tunnel port.")
        self.assertEqual(locate_chunks(lines, set(), [chunk]), [(9, 11)])
        # what was placed before is placed as before: the section's own heading line, right above its text
        whole = ("## How to Configure Layer 2 Protocol Tunneling\n### Configuring Layer 2 Protocol Tunneling\n\n"
                 "Layer 2 protocol tunneling lets the service provider carry the customer's CDP, STP and VTP frames"
                 " across its network.")
        self.assertEqual(locate_chunks(lines, set(), [whole]), [(4, 5)])
        self.assertEqual(locate_chunks(lines, set(), ["## Not here\n\nnor this text"]), [None])

    def test_a_chunk_is_not_placed_by_its_titles_alone(self):
        # corpus-v8.1 c9300-1718-sec #667 (v3.2): the tail of "Configuring an IKEv2 Profile (Basic)" opens with its
        # titles (14 words). They matched only partly, at the heading of a like-named later section (or at the
        # section's own heading far above): the span went to the wrong section, or took in lines the chunk lacks
        lines = ["# IPsec Guide", "## Configuring Basic Internet Key Exchange Version 2 CLI Constructs",
                 "### Configuring an IKEv2 Profile (Basic)",
                 "Defines an IKEv2 profile and enters IKEv2 profile configuration mode for the peer.",
                 "Specifies Public Key Infrastructure trustpoints for use with the RSA signature authentication method.",
                 "Step 16 virtual-template number mode auto applies the template to the profile.",
                 "## Configuration Examples for Basic Internet Key Exchange Version 2 CLI Constructs",
                 "### Example: Configuring the IKEv2 Key Ring",
                 "The following example shows how to configure a key ring with multiple peer subblocks."]
        head = "## Configuring Basic Internet Key Exchange Version 2 CLI Constructs\n### Configuring an IKEv2 Profile (Basic)"
        tail = (head + "\n\nSpecifies Public Key Infrastructure trustpoints for use with the RSA signature authentication"
                " method.\n\nStep 16 virtual-template number mode auto applies the template to the profile.")
        before = "Defines an IKEv2 profile and enters IKEv2 profile configuration mode for the peer."
        self.assertEqual(locate_chunks(lines, set(), [before, tail]), [(4, 4), (5, 6)])      # not (7, 9)
        self.assertEqual(locate_chunks(lines, set(), [tail]), [(5, 6)])                       # not (2, 6)
        # titles that stand right above the text are part of the span, as before
        whole = head + "\n\n" + before
        self.assertEqual(locate_chunks(lines, set(), [whole]), [(2, 4)])

    def test_a_chunk_does_not_end_many_chunk_lengths_away(self):
        # corpus-v9 pwsh-win-scheduledtasks (chunker v3.3): a chunk closed by a cmdlet's RELATED LINKS list, after
        # heading-only OUTPUTS lines it does not print, matched its closing words in full only at the document's
        # last cmdlet (whose list follows the same Type line directly): span 67-1176. Ends are kept within
        # SPAN_SLACK chunk lengths of the chunk's first word
        typeline = ("Type: System.Int32 | Required: False | Position: Named | Default: None | Pipeline: False | "
                    "Wildcards: False")
        links = ["### RELATED LINKS", "Enable-ScheduledTask", "Get-ScheduledTask", "Stop-ScheduledTask"]
        own = " ".join(f"disable{i}" for i in range(70))
        lines = (["# Tasks", "## Disable-ScheduledTask", "#### -ThrottleLimit", own, typeline, "### OUTPUTS",
                  "#### Microsoft.Management.Infrastructure.CimInstance#MSFT_ScheduledTask"] + links
                 + [f"Filler line {i} about another cmdlet entirely here." for i in range(200)]
                 + ["## Unregister-ScheduledTask", "#### -ThrottleLimit", "Specifies the maximum number.", typeline]
                 + links)
        chunk = "\n\n".join(["#### -ThrottleLimit", own, typeline] + links)
        (span,) = locate_chunks(lines, set(), [chunk])
        self.assertEqual(span[0], 3)
        self.assertLessEqual(span[1], 11)            # not 215, the last cmdlet's list
        # a chunk that skips a stretch of its source (a figure, a table it does not hold) still ends where it ends
        a, b = " ".join(f"a{i}" for i in range(30)), " ".join(f"b{i}" for i in range(30))
        lines = ["# Guide", a] + [f"row {i} of a table the chunk does not hold" for i in range(40)] + [b, "# Next", "x"]
        self.assertEqual(locate_chunks(lines, set(), [a + "\n\n" + b]), [(2, 43)])


class IndexRootTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = build_root(Path(self.tmp.name))
        self.db = self.paths.sqlite / "rag.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_is_additive_and_idempotent(self):
        with connect(self.db) as conn:
            before = {t: table_info(conn, t) for t in ("documents", "sections", "chunks")}
        counts = index_root(self.paths)
        self.assertEqual((counts["documents"], counts["indexed"]), (5, 5))
        self.assertEqual(counts["located"], counts["chunks"])
        self.assertEqual(counts["no_source"], [])
        with connect(self.db) as conn:
            after = {t: table_info(conn, t) for t in ("documents", "sections", "chunks")}
        self.assertEqual(before, after)             # existing tables untouched

        again = index_root(self.paths)
        self.assertEqual((again["indexed"], again["unchanged"]), (0, 5))

    def test_sections_spans_and_metadata(self):
        index_root(self.paths)
        with connect(self.db) as conn:
            src = {r["doc_key"]: dict(r) for r in conn.execute("SELECT * FROM doc_sources")}
            sec = conn.execute(
                "SELECT * FROM doc_sections WHERE section_id = 'rfc3101#3.2'"
            ).fetchone()
            span = conn.execute(
                "SELECT * FROM chunk_spans WHERE chunk_id = ?", (chunk_id("rfc3101", 2),)
            ).fetchone()
        self.assertEqual(src["rfc3101"]["title"], "The OSPF Not-So-Stubby Area (NSSA) Option")
        self.assertEqual(src["rfc3101"]["rel_path"], "rfc/rfc3101.txt")
        self.assertEqual(src["c9300-179-rtng"]["release"], "17.9")
        self.assertEqual(src["c9300-179-rtng"]["title"], "Routing Configuration Guide, 17.9")
        self.assertEqual(sec["parent_section_id"], "rfc3101#3")
        self.assertEqual(sec["level"], 2)
        self.assertGreater(sec["n_chars"], 200)
        # the chunk that absorbed 3.2 into 3.1 is linked to both sections
        self.assertEqual(json.loads(span["covered_json"]), ["rfc3101#3.1", "rfc3101#3.2"])
        self.assertIn(span["section_id"], {"rfc3101#3.1", "rfc3101#3.2"})

    def test_a_document_another_structure_version_indexed_is_indexed_again(self):
        # a root indexed before the RFC parser changed (p1-v1) gets the new sections on the next run
        self.assertNotEqual(STRUCTURE_VERSION, "p1-v1")
        index_root(self.paths)
        with connect(self.db) as conn:
            conn.execute("UPDATE doc_sources SET structure_version = 'p1-v1' WHERE doc_key = 'rfc4271'")
            conn.commit()
        self.assertEqual(index_root(self.paths)["unchanged"], 4)
        with connect(self.db) as conn:
            versions = dict(conn.execute("SELECT doc_key, structure_version FROM doc_sources"))
        self.assertEqual(set(versions.values()), {STRUCTURE_VERSION})

    def test_rechunking_drops_spans_and_the_next_run_restores_them(self):
        index_root(self.paths)
        with connect(self.db) as conn:
            doc_id = conn.execute(
                "SELECT doc_id FROM doc_sources WHERE doc_key = 'rfc4271'"
            ).fetchone()[0]
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.commit()
            left = conn.execute(
                "SELECT COUNT(*) FROM chunk_spans WHERE doc_id = ?", (doc_id,)
            ).fetchone()[0]
        self.assertEqual(left, 0)                   # ON DELETE CASCADE
        # no chunks now: the span count matches, the document stays indexed
        self.assertEqual(index_root(self.paths)["unchanged"], 5)

    def test_empty_tables_are_no_index(self):
        with connect(self.db) as conn:
            self.assertFalse(has_structure(conn))
        counts = index_root(self.paths, sources=self.paths.root / "missing")
        self.assertEqual((counts["indexed"], len(counts["no_source"])), (0, 5))
        with connect(self.db) as conn:
            self.assertFalse(has_structure(conn))           # the tables exist, but hold nothing
        index_root(self.paths)
        with connect(self.db) as conn:
            self.assertTrue(has_structure(conn))

    @engine_target.SCRIPTS_SKIP
    def test_index_script_fails_when_no_document_has_a_source(self):
        def run(*args):
            env = {**os.environ, "RAG_ROOT": str(self.paths.root), "PYTHONPATH": str(REPO / "src")}
            return subprocess.run([sys.executable, str(REPO / "scripts" / "index_structure.py"), *args],
                                  env=env, capture_output=True, text=True, timeout=120)

        missing = run("--sources", str(self.paths.root / "missing"))
        self.assertEqual(missing.returncode, 2, missing.stderr)
        with connect(self.db) as conn:                      # nothing written
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'doc_sources'").fetchone())
        (self.paths.root / "empty").mkdir()
        self.assertEqual(run("--sources", str(self.paths.root / "empty")).returncode, 1)
        self.assertEqual(run().returncode, 0)

    def test_documents_without_a_source_are_reported(self):
        (self.paths.root / "sources" / "rfc" / "rfc1587.txt").unlink()
        counts = index_root(self.paths)
        self.assertEqual(counts["no_source"], ["rfc1587"])

    def test_manifest_supplies_title_release_and_path(self):
        sources = self.paths.root / "sources"
        (sources / "cisco" / "c9300-179-rtng.txt").rename(sources / "cisco" / "renamed.txt")
        (sources / "manifest.json").write_text(json.dumps([
            {"doc_key": "c9300-179-rtng", "doc_type": "cisco", "path": "cisco/renamed.txt",
             "title": "From the manifest", "release": "17.9.4", "platform": "cat9300",
             "source_url": "https://www.cisco.com/x"},
        ]))
        index_root(self.paths)
        with connect(self.db) as conn:
            row = conn.execute(
                "SELECT * FROM doc_sources WHERE doc_key = 'c9300-179-rtng'"
            ).fetchone()
        self.assertEqual(
            (row["title"], row["release"], row["platform"], row["rel_path"], row["source_url"]),
            ("From the manifest", "17.9.4", "cat9300", "cisco/renamed.txt",
             "https://www.cisco.com/x"),
        )


    def test_only_cisco_documents_infer_a_release(self):
        # the keys of other document types hold years and versions that are no Cisco release
        sources = self.paths.root / "sources"
        moves = {
            "c9300-1718-rtng.pdf": {"doc_key": "mn-175-osd", "doc_type": "manual", "path": "manual/mn-175-osd.txt"},
            "rfc1587.txt": {"doc_key": "pp-cp-ma-280", "doc_type": "paper", "path": "paper/pp-cp-ma-280.txt",
                            "release": "2.8.0"},
        }
        with connect(self.db) as conn:
            for filename, entry in moves.items():
                (sources / entry["doc_type"]).mkdir()
                old = "cisco" if filename.startswith("c9300") else "rfc"
                (sources / old / filename.replace(".pdf", ".txt")).rename(sources / entry["path"])
                conn.execute("UPDATE documents SET metadata_json = ? WHERE original_filename = ?",
                             (json.dumps({"corpus": entry}), filename))
            conn.commit()
        index_root(self.paths)
        with connect(self.db) as conn:
            rows = {r["doc_key"]: (r["doc_type"], r["release"]) for r in conn.execute("SELECT * FROM doc_sources")}
        self.assertEqual((infer_release("mn-175-osd"), infer_release("pp-cp-ma-280")), ("17.5", "28.0"))
        self.assertEqual(rows["mn-175-osd"], ("manual", None))
        self.assertEqual(rows["pp-cp-ma-280"], ("paper", "2.8.0"))          # the manifest's release is kept
        self.assertEqual(rows["c9300-179-rtng"], ("cisco", "17.9"))          # a Cisco key still infers
        self.assertEqual(rows["rfc3101"], ("rfc", None))

    def test_a_root_build_names_documents_in_metadata(self):
        # build_root stores the corpus manifest entry under metadata_json["corpus"]
        entry = {"doc_key": "rfc4271", "doc_type": "rfc", "path": "rfc/rfc4271.txt",
                 "title": "BGP-4"}
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE documents SET original_filename = 'upload.bin', metadata_json = ? "
                "WHERE original_filename = 'rfc4271.txt'",
                (json.dumps({"corpus": entry}),),
            )
            conn.commit()
        index_root(self.paths)
        with connect(self.db) as conn:
            row = conn.execute("SELECT * FROM doc_sources WHERE doc_key = 'rfc4271'").fetchone()
        self.assertEqual((row["title"], row["rel_path"]), ("BGP-4", "rfc/rfc4271.txt"))


class TableContextSpanTests(unittest.TestCase):
    """chunking.table_header_repeat puts a table's caption line and header row at the start of each
    continuation chunk (metadata context_chars); the span is that of the chunk's own rows. A context
    of 12 words or more (structure.TRIES) leaves locate_chunks no opening k-gram in the rows."""

    CAPTION = "Table 2. Feature Information for RADIUS Attributes Field Descriptions"
    HEADER = "Field | Description"
    ROWS = [f"Attribute-{i} | The value of attribute {i} on the system. The format is String." for i in range(40)]

    def test_located_text(self):
        text = f"{self.CAPTION}\n{self.HEADER}\n{self.ROWS[20]}\n{self.ROWS[21]}"
        n = len(f"{self.CAPTION}\n{self.HEADER}\n")
        self.assertEqual(located_text(text, json.dumps({"context_chars": n})), f"{self.ROWS[20]}\n{self.ROWS[21]}")
        for meta in (None, "", "{}", '{"context_chars": 0}', json.dumps({"context_chars": len(text)}),
                     '{"context_chars": "12"}', '{"context_chars": true}', "not json", "[1]"):
            self.assertEqual(located_text(text, meta), text, meta)

    def test_a_continuation_chunk_is_placed_at_its_own_rows(self):
        with tempfile.TemporaryDirectory() as d:
            paths = build_root(Path(d))
            source = "\n".join(["# show radius table attributes", "To display the attributes, use the command.",
                                self.CAPTION, self.HEADER, *self.ROWS, ""])
            context = f"{self.CAPTION}\n{self.HEADER}\n"
            chunks = [("\n\n".join(["To display the attributes, use the command.", self.CAPTION,
                                    "\n".join([self.HEADER, *self.ROWS[:20]])]), {}),
                      (context + "\n".join(self.ROWS[20:]), {"context_chars": len(context)})]
            (paths.root / "sources" / "cisco" / "tbl-doc.txt").write_text(source, encoding="utf-8")
            with connect(paths.sqlite / "rag.db") as conn:
                conn.execute("INSERT INTO documents (doc_id, sha256, source_name, original_filename, status) "
                             "VALUES ('doc_tbl', 'tbl', 'cisco', 'tbl-doc.pdf', 'CHUNKED')")
                for n, (text, meta) in enumerate(chunks):
                    conn.execute("INSERT INTO chunks (chunk_id, chunk_sha256, doc_id, ordinal, heading_path_json, "
                                 "text, embedding_text, token_count, char_count, metadata_json) "
                                 "VALUES (?, ?, 'doc_tbl', ?, '[]', ?, ?, 0, 0, ?)",
                                 (f"chk_tbl{n}", f"s{n}", n, text, text, json.dumps(meta)))
                conn.commit()
            index_root(paths)

            def spans():
                with connect(paths.sqlite / "rag.db") as conn:
                    return [tuple(r) for r in conn.execute(
                        "SELECT line_start, line_end FROM chunk_spans WHERE doc_id = 'doc_tbl' ORDER BY chunk_id")]

            row = {line: n for n, line in enumerate(source.split("\n"), 1)}
            self.assertEqual(spans(), [(2, row[self.ROWS[19]]), (row[self.ROWS[20]], row[self.ROWS[39]])])
            # placed with its context, the chunk would start at the table's caption, over the chunk before
            with connect(paths.sqlite / "rag.db") as conn:
                conn.execute("UPDATE chunks SET metadata_json = '{}' WHERE chunk_id = 'chk_tbl1'")
                conn.commit()
            index_root(paths, force=True)
            self.assertEqual(spans()[1], (row[self.CAPTION], row[self.ROWS[39]]))


class HelperTests(unittest.TestCase):

    def test_infer_release(self):
        self.assertEqual(infer_release("c9300-1718-rtng"), "17.18")
        self.assertEqual(infer_release("c9k-179-lisp-vxlan"), "17.9")
        self.assertEqual(infer_release("catc237-user"), "2.3.7")
        self.assertIsNone(infer_release("iosxe17-rtr-iprouting-cg"))

    def test_display_lines_drop_furniture_and_collapse_blank_runs(self):
        lines = ["a", "", "", "footer [Page 1]", "\fRFC 1  x  2003", "", "b"]
        self.assertEqual(display_lines(lines, {3, 4}), [(1, "a"), (2, ""), (7, "b")])


if __name__ == "__main__":
    unittest.main()
