import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_core import kb
from rag_core.sections import parse_sections
from rag_core.structure import index_root

from v2_root import build_root


class BuildKbTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.paths = build_root(base)
        self.src = base / "kb-src"
        self.kb_path = self.paths.root / "kb" / "kb.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def open(self):
        return kb.open_kb(self.kb_path)

    def test_counts_resolution_and_meta(self):
        index_root(self.paths)
        counts = kb.build_kb(self.src, self.kb_path)
        self.assertEqual(
            {k: counts[k] for k in ("rfc", "errata", "iana", "glossary", "cli", "feature_history")},
            {"rfc": 5, "errata": 3, "iana": 5, "glossary": 2, "cli": 4, "feature_history": 1},
        )
        self.assertEqual(counts["missing"], [])
        # the 17.18 guide's text differs from what the CLI index was built from: its line is
        # not resolved; the 17.9 guide's lines are
        self.assertEqual(counts["cli_resolved"], 3)
        self.assertEqual(counts["cli_source_mismatch"], ["c9300-1718-rtng"])
        self.assertEqual(counts["feature_resolved"], 1)
        self.assertFalse(self.kb_path.with_name("kb.sqlite.tmp").exists())

        db = self.open()
        sections = dict(db.execute("SELECT command, section_id FROM cli"))
        self.assertEqual(
            sections["show ip ospf neighbor [<interface-type> <interface-number>] [<neighbor-id>] [detail]"],
            "c9300-179-rtng#show-ip-ospf-neighbor",
        )
        self.assertEqual(sections["show ip ospf database nssa-external"], "c9300-179-rtng#examples-2")
        self.assertIsNone(sections["area 1 nssa translate type7 always"])
        self.assertEqual(
            db.execute("SELECT section_id FROM feature").fetchone()[0],
            "c9300-179-rtng#restrictions-for-ospf-nssa",
        )
        meta = dict(db.execute("SELECT key, value FROM meta"))
        self.assertEqual(meta["kb_schema_version"], kb.KB_SCHEMA_VERSION)
        self.assertEqual(json.loads(meta["sources"])["iana"]["generator"], "tests")

    def test_missing_files_and_missing_sections_are_tolerated(self):
        (self.src / "glossary.json").unlink()
        (self.src / "cli_index.json").unlink()
        counts = kb.build_kb(self.src, self.kb_path)     # no structure index yet
        self.assertEqual(counts["missing"], ["glossary.json", "cli_index.json"])
        self.assertFalse(counts["sections_available"])
        self.assertEqual(counts["feature_resolved"], 0)
        self.assertEqual(kb.table_counts(self.open())["glossary"], 0)

    @engine_target.POSIX_ONLY
    def test_paths_with_uri_characters(self):
        # the read-only connections are SQLite URIs: an unescaped "?" or "#" cut the path short
        base = Path(self.tmp.name) / "odd ?#%41 dir"
        paths = build_root(base)
        index_root(paths)
        kb_path = paths.root / "kb" / "kb.sqlite"
        self.assertEqual(kb.build_kb(base / "kb-src", kb_path)["cli_resolved"], 3)   # read rag.db
        db = kb.open_kb(kb_path)
        self.addCleanup(db.close)
        self.assertEqual(kb.table_counts(db)["rfc"], 5)

    def test_a_rebuild_replaces_the_file(self):
        kb.build_kb(self.src, self.kb_path)
        (self.src / "iana.json").write_text("[]")
        kb.build_kb(self.src, self.kb_path)
        self.assertEqual(kb.table_counts(self.open())["iana"], 0)


class QueryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        paths = build_root(Path(cls.tmp.name))
        index_root(paths)
        kb.build_kb(Path(cls.tmp.name) / "kb-src", paths.root / "kb" / "kb.sqlite")
        cls.db = kb.open_kb(paths.root / "kb" / "kb.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def kind(self, identifier):
        return kb.lookup(self.db, identifier)["kind"]

    def test_identifier_forms(self):
        for form in ("RFC4271 9.1.2", "rfc 4271 §9.1.2", "RFC 4271 section 9.1.2",
                     "rfc4271#section-9.1.2"):
            result = kb.lookup(self.db, form)
            self.assertEqual(result["kind"], "rfc_section", form)
            self.assertEqual(result["results"][0]["section_id"], "rfc4271#9.1.2", form)
        self.assertEqual(kb.lookup(self.db, "RFC 3101 3.0")["results"][0]["section_id"], "rfc3101#3")
        self.assertEqual(kb.lookup(self.db, "RFC3101 Appendix A")["results"][0]["section_id"],
                         "rfc3101#appendix-a")
        errata = kb.lookup(self.db, "rfc 4271 §9.1.2")["results"][0]["errata"]
        self.assertEqual([e["id"] for e in errata], [3003])

        self.assertEqual(self.kind("RFC 3101"), "rfc")
        bcp = kb.lookup(self.db, "BCP 14")
        self.assertEqual((bcp["kind"], [r["rfc"] for r in bcp["results"]]), ("bcp", [2119, 8174]))
        for form in ("type-7 LSA", "LSA type 7", "Type 7 LSA", "BGP attribute 9",
                     "ORIGINATOR_ID", "originator id", "DSCP EF"):
            self.assertEqual(self.kind(form), "iana", form)
        both = kb.lookup(self.db, "AFI 1 SAFI 128")["results"]
        self.assertEqual([(r["registry"][:4], r["code"]) for r in both], [("Addr", "1"), ("Subs", "128")])
        self.assertEqual(kb.lookup(self.db, "NSSA")["results"][0]["expansion"], "Not-So-Stubby Area")
        self.assertEqual(self.kind("ABRs"), "glossary")
        cli = kb.lookup(self.db, "show ip ospf database nssa-external")
        self.assertEqual(cli["kind"], "cli")
        self.assertEqual(cli["results"][0]["cli_kind"], "show-output")
        self.assertEqual(self.kind("no such thing"), "unknown")
        self.assertEqual(kb.lookup(self.db, "RFC 9999")["results"], [])

    def test_rfc_info_and_standing(self):
        info = kb.rfc_info(self.db, 3101)
        self.assertEqual(info["obsoletes"], [1587])
        self.assertEqual([e["id"] for e in info["errata"]], [3001])     # rejected ones counted
        self.assertEqual(info["errata_rejected"], 1)
        self.assertEqual(info["standing"], "Proposed Standard; obsoletes RFC 1587; errata: 1 verified")
        self.assertEqual(kb.rfc_info(self.db, 1587)["standing"],
                         "Proposed Standard; OBSOLETED by RFC 3101")
        self.assertIn("updated by RFC 4724, 6286, 6608, 6793 +1 more", kb.rfc_info(self.db, 4271)["standing"])
        self.assertIsNone(kb.rfc_info(self.db, 9999))

    def test_find_command_abbreviations(self):
        for typed in ("sh ip ospf nei", "show ip ospf neighbor detail", "R1#sh ip os nei",
                      "do sh ip ospf neigh | include FULL"):
            results = kb.find_command(self.db, typed)
            self.assertTrue(results, typed)
            self.assertTrue(results[0]["command"].startswith("show ip ospf neighbor"), typed)
        forced = kb.find_command(self.db, "Router(config-router)# area 1 nssa translate type7 always")
        self.assertEqual(forced[0]["kind"], "syntax")          # syntax ranks above the example
        self.assertEqual(forced[0]["section_id"],
                         "c9300-179-rtng#configuring-an-nssa-abr-as-a-forced-nssa-lsa-translator")
        self.assertEqual(len(forced), 2)
        self.assertEqual(kb.find_command(self.db, "sh ip ospf nei", platform="iosxe"), [])
        self.assertEqual(kb.find_command(self.db, "configure terminal"), [])
        self.assertEqual(kb.find_command(self.db, "   "), [])

    def test_release_preference(self):
        # with release 17.9 the example dated 17.18 ranks after the one dated 16.3.1
        ranked = kb.find_command(self.db, "area 1 nssa translate type7 always", release="17.9")
        self.assertEqual([r["release"] for r in ranked], ["16.3.1", "17.18"])


class RfcSuccessionTests(unittest.TestCase):
    """SRV-08 (audit 2026-11-05): lookup_rfc named only the RFC that obsoletes this one ("RFC 1583: OBSOLETED by RFC
    2178"), itself obsoleted. rfc_info now follows the chain to its end: "current"."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        paths = build_root(Path(cls.tmp.name))
        src = Path(cls.tmp.name) / "kb-src"
        rows = json.loads((src / "rfc_index.json").read_text(encoding="utf-8"))

        def rfc(n, status="Draft Standard", obsoleted_by=()):
            return {"rfc": n, "title": f"RFC {n}", "status": status, "date": "1994-03", "obsoletes": [],
                    "obsoleted_by": list(obsoleted_by), "updates": [], "updated_by": [], "also": [],
                    "in_corpus": False}

        rows += [
            rfc(1583, obsoleted_by=[2178]), rfc(2178, obsoleted_by=[2328]), rfc(2328, "Internet Standard"),
            # two successors, one obsoleted in turn (100 -> 200 -> 400, 100 -> 300)
            rfc(100, obsoleted_by=[200, 300]), rfc(200, obsoleted_by=[400]), rfc(300), rfc(400),
            # a diamond: 500 -> 501 -> 503, 500 -> 502 -> 503
            rfc(500, obsoleted_by=[501, 502]), rfc(501, obsoleted_by=[503]), rfc(502, obsoleted_by=[503]), rfc(503),
            # a cycle the RFC Editor never publishes, and a successor the index does not hold
            rfc(10, obsoleted_by=[11]), rfc(11, obsoleted_by=[10]), rfc(20, obsoleted_by=[99999]),
            # a chain of 40 (the RFC Editor's longest is 32, RFC 1083)
            *[rfc(5000 + i, obsoleted_by=[5001 + i]) for i in range(40)], rfc(5040),
        ]
        (src / "rfc_index.json").write_text(json.dumps(rows), encoding="utf-8")
        kb.build_kb(src, paths.root / "kb" / "kb.sqlite")
        cls.db = kb.open_kb(paths.root / "kb" / "kb.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def info(self, n):
        return kb.rfc_info(self.db, n)

    def test_the_chain_is_followed_to_its_end(self):
        self.assertEqual(self.info(1583)["current"], [{"rfc": 2328, "via": [2178]}])
        self.assertEqual(self.info(1583)["standing"],
                         "Draft Standard; OBSOLETED by RFC 2178; current: RFC 2328 (via 2178)")
        # one step: the successor is current, and the line is as before
        self.assertEqual(self.info(2178)["current"], [{"rfc": 2328, "via": []}])
        self.assertEqual(self.info(2178)["standing"], "Draft Standard; OBSOLETED by RFC 2328")
        # not obsoleted: unchanged
        self.assertEqual(self.info(2328)["current"], [])
        self.assertEqual(self.info(2328)["standing"], "Internet Standard")
        # lookup_id's RFC results carry it too
        result = kb.lookup(self.db, "RFC 1583")["results"][0]
        self.assertEqual((result["current"], result["standing"]),
                         ([{"rfc": 2328, "via": [2178]}], self.info(1583)["standing"]))

    def test_branches_diamonds_cycles_and_unknown_successors(self):
        self.assertEqual(self.info(100)["current"], [{"rfc": 300, "via": []}, {"rfc": 400, "via": [200]}])
        self.assertEqual(self.info(100)["standing"],
                         "Draft Standard; OBSOLETED by RFC 200, 300; current: RFC 300, RFC 400 (via 200)")
        self.assertEqual(self.info(500)["current"], [{"rfc": 503, "via": [501]}])      # reached once
        self.assertEqual(self.info(10)["current"], [])                                  # a cycle ends the walk
        self.assertEqual(self.info(10)["standing"], "Draft Standard; OBSOLETED by RFC 11")
        self.assertEqual(self.info(20)["current"], [{"rfc": 99999, "via": []}])

    def test_a_long_chain_is_shortened_and_the_walk_is_bounded(self):
        info = self.info(5000)
        self.assertEqual(info["current"], [{"rfc": 5040, "via": list(range(5001, 5040))}])
        self.assertEqual(info["standing"],
                         "Draft Standard; OBSOLETED by RFC 5001; current: RFC 5040 (via 5001, 5002, 5003 +36 more)")
        with mock.patch.object(kb, "MAX_SUCCESSION", 10):
            self.assertEqual(self.info(5000)["current"], [])             # the end is past the bound: none named
            self.assertEqual(self.info(1583)["current"], [{"rfc": 2328, "via": [2178]}])
        self.assertEqual(kb.current_rfcs(self.db, 4, []), [])


class YearReleaseTests(unittest.TestCase):
    """kb.later_release: Cisco numbers IOS XE and SD-WAN releases by year from 2026 (26.x), so they are later than
    any release numbered before; the rule of find_command's release order and of the /v2 release_max filter."""

    def later(self, have, limit):
        return kb.later_release(kb.version_key(have), kb.version_key(limit))

    def test_the_same_major_line_compares_as_before(self):
        self.assertTrue(self.later("17.18", "17.9"))
        self.assertTrue(self.later("17.9.4", "17.9"))
        self.assertTrue(self.later("20.15", "20.9"))
        self.assertFalse(self.later("17.9", "17.9"))
        self.assertFalse(self.later("17.6.1", "17.9"))
        self.assertFalse(self.later("26.x", "26.1"))            # (26,) sorts before (26, 1)

    def test_other_lines_and_unknown_releases_are_not_later(self):
        for have, limit in (("20.15", "17.9"), ("17.18", "20.9"), ("2.3.7", "17.9"), ("16.12", "17.9"),
                            ("17.18", "26.1"), ("20.18", "26.x"), (None, "17.9"), ("n/a", "17.9"),
                            ("17.18", None), ("26.1", "latest")):
            self.assertFalse(self.later(have, limit), (have, limit))

    def test_a_year_numbered_release_is_later_than_any_release_numbered_before(self):
        for have in ("26.x", "26.1", "26.1.1", "27.1"):
            for limit in ("17.9", "17.18", "20.9", "16.12", "2.3", "3.1"):
                self.assertTrue(self.later(have, limit), (have, limit))
        # and they compare by number among themselves
        self.assertTrue(self.later("27.1", "26.2"))
        self.assertTrue(self.later("26.2", "26.1"))
        self.assertFalse(self.later("26.2", "27.1"))

    def test_find_command_ranks_a_year_numbered_entry_after_the_release_asked_for(self):
        with tempfile.TemporaryDirectory() as d:
            paths = build_root(Path(d))
            src = Path(d) / "kb-src"
            rows = json.loads((src / "cli_index.json").read_text(encoding="utf-8"))
            rows.append({"command": "area 1 nssa translate type7 always", "mode": "config-router",
                         "platform": "cat9300", "doc_key": "c9300-26x-rtng", "line": 4, "kind": "example",
                         "release": "26.1"})
            (src / "cli_index.json").write_text(json.dumps(rows), encoding="utf-8")
            kb.build_kb(src, paths.root / "kb" / "kb.sqlite")
            db = kb.open_kb(paths.root / "kb" / "kb.sqlite")
            try:
                def releases(release):
                    return [r["release"] for r in
                            kb.find_command(db, "area 1 nssa translate type7 always", release=release)]

                # 26.1 used to pass a 17.9 request as another major line, ahead of the 17.18 example
                self.assertEqual(releases("17.9"), ["16.3.1", "17.18", "26.1"])
                self.assertEqual(releases("26.1"), ["16.3.1", "17.18", "26.1"])     # nothing later: id order
                self.assertEqual(releases(None), ["16.3.1", "17.18", "26.1"])
            finally:
                db.close()


class CommandIndexTests(unittest.TestCase):
    """find_command on the ISE coverage report's cases: a literal word in an argument's place (K1), the platform
    of every row, and the platforms outside the IOS lineage under an IOS release (K3)."""

    ROWS = [
        # ise31-cli stores "ise/admin# show application status ise" with the placeholder
        ("show application [status {<application_name>}]", "syntax", "ise", "ise31-cli", 2985),
        ("show application status <application_name>", "show-output", "ise", "ise31-cli", 948),
        ("application [stop {<application-name>}]", "syntax", "ise", "ise31-cli", 1042),
        ("show ntp", "syntax", "ise", "ise31-cli", 3813),
        ("show ntp associations [detail]", "syntax", "iosxe-router", "iosxe-cr-bsm", 2676),
        ("show ntp peer", "syntax", "sdwan", "sdwan-cr", 27734),
        ("show ip route vrf <vrf-name>", "task", "iosxe-router", "iosxe17-mpls-l3vpn", 8072),
        ("class-map [match-all | match-any] <class-map-name>", "syntax", "cat9300", "c9300-179-cr", 100),
        ("service-policy {input | output} <policy-map-name>", "syntax", "cat9300", "c9300-179-cr", 200),
        ("ip access-list {{extended | resequence | standard} {<access-list-number> | <access-list-name>} | "
         "helper egress check}", "syntax", "cat9300", "c9300-179-cr", 250),
        ("device-tracking [attach-policy <policy-name>] [priority <priority-value>]", "syntax", "cat9300",
         "c9300-179-cr", 300),
        ("eap {username <name> | password <password>}", "syntax", "iosxe-router", "iosxe-cr-sec-d-l", 400),
        ("vrf definition <vrf-name>", "syntax", "iosxe-router", "iosxe-cr-mpls-ldp-vpn", 500),
        ("vrf definition redistribute-all", "syntax", "iosxe-router", "made-up", 600),
        ("weight <value>", "syntax", "iosxe-router", "iosxe-cr-bgp", 700),
        ("ospfv3 <process-id> area <area-id>", "syntax", "iosxe-router", "iosxe-cr-ospf", 800),
    ]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        paths = build_root(Path(cls.tmp.name))
        src = Path(cls.tmp.name) / "kb-src"
        rows = json.loads((src / "cli_index.json").read_text(encoding="utf-8"))
        rows += [{"command": command, "mode": "privileged-exec", "platform": platform, "doc_key": doc_key,
                  "line": line, "kind": kind, "release": None}
                 for command, kind, platform, doc_key, line in cls.ROWS]
        (src / "cli_index.json").write_text(json.dumps(rows), encoding="utf-8")
        kb.build_kb(src, paths.root / "kb" / "kb.sqlite")
        cls.db = kb.open_kb(paths.root / "kb" / "kb.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def find(self, q, **kw):
        return kb.find_command(self.db, q, **kw)

    def test_a_literal_word_fills_an_argument_when_nothing_else_matches(self):
        # K1: "ise", "RED" and "CM-VOICE" are no keyword of any entry, so nothing matched them before
        got = self.find("show application status ise")
        self.assertEqual([(r["command"], r["line"]) for r in got],
                         [("show application [status {<application_name>}]", 2985),
                          ("show application status <application_name>", 948)])
        self.assertEqual([r["arguments"] for r in got], [[["ise", "<application_name>"]]] * 2)
        self.assertEqual(self.find("application stop ise")[0]["arguments"], [["ise", "<application-name>"]])
        for typed, command, arguments in (
                ("show ip route vrf RED", "show ip route vrf <vrf-name>", [["red", "<vrf-name>"]]),
                # an optional keyword group passed over, then the argument after it
                ("class-map CM-VOICE", "class-map [match-all | match-any] <class-map-name>",
                 [["cm-voice", "<class-map-name>"]]),
                # the other choice of the keyword typed is passed over
                ("service-policy input POLICY", "service-policy {input | output} <policy-map-name>",
                 [["policy", "<policy-map-name>"]]),
                # ... in a group of its own: the argument's group is the next one
                ("ip access-list extended ACL-WEBAUTH", "ip access-list {{extended | resequence | standard} "
                 "{<access-list-number> | <access-list-name>} | helper egress check}",
                 [["acl-webauth", "<access-list-number>"]])):
            with self.subTest(typed=typed):
                got = self.find(typed)
                self.assertEqual([(r["command"], r["arguments"]) for r in got], [(command, arguments)])
        # lookup_id's CLI fallback finds it too
        self.assertEqual(kb.lookup(self.db, "show application status ise")["kind"], "cli")
        # a typed command that matches as before carries no "arguments"; a value fills an argument as before
        self.assertNotIn("arguments", self.find("sh ip ospf nei")[0])
        self.assertNotIn("arguments", self.find("area 1 nssa translate type7 always")[0])

    def test_only_when_no_entry_matches(self):
        # "vrf definition red" matches the keyword of a made-up entry: that is the answer, as before
        self.assertEqual([r["command"] for r in self.find("vrf definition red")], ["vrf definition redistribute-all"])
        self.assertEqual([r["command"] for r in self.find("vrf definition BLUE")], ["vrf definition <vrf-name>"])

    def test_lookup_reads_a_literal_value_only_after_two_keywords(self):
        # lookup_id's CLI fallback: a phrase whose first word begins a command is no command with a literal value
        # (on corpus-v5, 20 of 217 phrases became one: "weight attribute" -> "weight <value>", "key string" ->
        # "key <name>", "sha authentication" -> "shape <cir>"); find_command still reads it as one
        self.assertEqual(self.find("weight attribute")[0]["arguments"], [["attribute", "<value>"]])
        self.assertEqual(self.find("class-map CM-VOICE")[0]["arguments"], [["cm-voice", "<class-map-name>"]])
        for identifier in ("weight attribute", "class-map CM-VOICE"):
            with self.subTest(identifier=identifier):
                self.assertEqual(kb.lookup(self.db, identifier), {"kind": "unknown", "results": []})
        # two keywords, then the value: as find_command reads it
        self.assertEqual([r["arguments"] for r in kb.lookup(self.db, "application stop ise")["results"]],
                         [r["arguments"] for r in self.find("application stop ise", limit=10)])
        self.assertEqual(kb.lookup(self.db, "vrf definition BLUE")["results"][0]["command"],
                         "vrf definition <vrf-name>")
        # what matched without letting a word go is unchanged: a value after one keyword
        self.assertEqual(kb.lookup(self.db, "weight 100")["results"][0]["command"], "weight <value>")

    def test_lookup_reads_a_literal_value_only_after_keywords_typed_in_full(self):
        # nor is a phrase whose words abbreviate keywords: of 11,391 Cisco section headings of corpus-v5 that
        # lookup answered "unknown", 188 became CLI rows ("ospf area design" -> "ospfv3 <process-id> area
        # <area-id>", "a through h" -> "address <ip-address> hardware-address ..."), 85 through an abbreviated
        # keyword; find_command still reads it as one
        self.assertEqual(self.find("ospf area design")[0]["arguments"], [["design", "<area-id>"]])
        self.assertEqual(kb.lookup(self.db, "ospf area design"), {"kind": "unknown", "results": []})
        # its keywords typed in full: as find_command reads it
        self.assertEqual([(r["command"], r["arguments"])
                          for r in kb.lookup(self.db, "ospfv3 area design")["results"]],
                         [("ospfv3 <process-id> area <area-id>", [["design", "<area-id>"]])])
        # an abbreviation that matches without a word let go resolves as before
        self.assertEqual(kb.lookup(self.db, "sh ip ospf nei")["kind"], "cli")
        # the keywords the matcher reports
        typed = []
        words = kb.command_words("ospfv3 <process-id> area <area-id>")
        self.assertEqual(kb.match_arguments(["ospf", "area", "design"], words, frozenset({2}), [()] * 4, typed),
                         (0, [("design", "<area-id>")]))
        self.assertEqual(typed, [("ospf", "ospfv3"), ("area", "area")])

    def test_a_word_let_go_never_fills_the_value_of_an_optional_keyword_passed_over(self):
        # without this, "Device Admin" and "EAP type 55" (lookup_id probes of the ISE report) read as these
        for typed in ("Device Admin", "eap type 55", "eap type"):
            with self.subTest(typed=typed):
                self.assertEqual(self.find(typed), [])
        self.assertEqual(kb.lookup(self.db, "Device Admin")["kind"], "unknown")
        # match_arguments keeps the old rule for a word the prefilter held (not in `free`)
        command = "eap {username <name> | password <password>}"
        words, groups = kb.command_words(command), kb.command_groups(command)
        self.assertEqual(kb.match_arguments(["eap", "type"], words), (0, [("type", "<name>")]))
        self.assertEqual(kb.match_arguments(["eap", "type"], words, frozenset({1}), groups), (None, []))
        self.assertEqual(kb.match_arguments(["eap", "user", "pat"], words, frozenset({2}), groups),
                         (0, [("pat", "<name>")]))                  # the keyword typed: its value
        # what an earlier word passed over does not count: "time" was passed over to reach "tries"
        command = "radius-server dead-criteria {time <seconds> | tries <tries>}"
        words, groups = kb.command_words(command), kb.command_groups(command)
        self.assertEqual(kb.match_arguments(["radius-server", "dead-criteria", "tries", "max"], words,
                                            frozenset({3}), groups), (0, [("max", "<tries>")]))

    def test_command_groups(self):
        self.assertEqual(kb.command_groups("eap {username <name> | password <password>}"),
                         [(), (1,), (1,), (1,), (1,)])
        self.assertEqual(kb.command_groups("ip access-list {{extended | standard} <name> | helper} [log]"),
                         [(), (), (1, 2), (1, 2), (1,), (1,), (3,)])
        self.assertEqual(len(kb.command_groups("show ip ospf [<process-id>] neighbor ]")),
                         len(kb.command_words("show ip ospf [<process-id>] neighbor ]")))

    def test_every_row_carries_its_platform(self):
        # the edge's text shows it (deep32-gateway v2.3 render_command); null when the index has none
        for typed in ("show ntp", "sh ip ospf nei", "show application status ise"):
            with self.subTest(typed=typed):
                rows = self.find(typed)
                self.assertTrue(rows)
                self.assertTrue(all("platform" in r for r in rows))
        self.assertEqual({r["platform"] for r in self.find("show ntp")}, {"ise", "iosxe-router", "sdwan"})

    def test_an_ios_release_ranks_the_platforms_outside_the_ios_lineage_last(self):
        def rows(**kw):
            return [(r["command"], r["platform"], r.get("outside_ios_lineage", False))
                    for r in self.find("show ntp", **kw)]

        # K3: ISE's complete "show ntp" ranked first under release 17.9
        unranked = [("show ntp", "ise", False), ("show ntp peer", "sdwan", False),
                    ("show ntp associations [detail]", "iosxe-router", False)]
        self.assertEqual(rows(), unranked)
        for release in ("17.9", "16.12", "15.2", "26.1"):
            with self.subTest(release=release):
                self.assertEqual(rows(release=release),
                                 [("show ntp associations [detail]", "iosxe-router", False),
                                  ("show ntp", "ise", True), ("show ntp peer", "sdwan", True)])
        # ISE 3.1, SD-WAN 20.9 or Catalyst Center 2.3: no IOS release, every platform ranks as before
        for release in ("3.1", "20.9", "2.3", "latest"):
            with self.subTest(release=release):
                self.assertEqual(rows(release=release), unranked)

    def test_ios_release(self):
        for release in ("17.9", "17.18.2", "16.12", "15.2(4)M", "12.4(24)T", "26.1", "27.1"):
            self.assertTrue(kb.ios_release(kb.version_key(release)), release)
        for release in ("3.1", "3.7S", "20.9", "2.3.7", None, "n/a"):
            self.assertFalse(kb.ios_release(kb.version_key(release)), release)

    def test_the_passes_that_let_words_go_are_bounded(self):
        # unbounded, "s s s ..." ran a pass per word, each over every command it could start (30 s for 100 words
        # on the corpus-v5 KB)
        passes = []
        real = kb._find_commands

        def spy(db, qwords, free, *args):
            results, rows = real(db, qwords, free, *args)
            passes.append((len(free), rows))
            return results, rows

        common = " ".join(["s"] * 60)
        with mock.patch.object(kb, "_find_commands", spy):
            self.assertEqual(self.find(common), [])
        self.assertEqual([n for n, _ in passes], list(range(kb.MAX_LET_GO + 1)))
        self.assertTrue(all(rows for _, rows in passes[1:]))           # each one read rows of "show ..."
        # they stop once LET_GO_ROWS rows are read; the first pass (nothing let go) is not counted
        passes.clear()
        with mock.patch.object(kb, "_find_commands", spy), mock.patch.object(kb, "LET_GO_ROWS", 1):
            self.assertEqual(self.find(common), [])
        self.assertEqual([n for n, _ in passes], [0, 1])
        # MAX_LET_GO words at most: "show application status ise" lets one go
        with mock.patch.object(kb, "MAX_LET_GO", 0):
            self.assertEqual(self.find("show application status ise"), [])
        with mock.patch.object(kb, "MAX_LET_GO", 1):
            self.assertEqual(len(self.find("show application status ise")), 2)
        # a word typed twice is one condition of the prefilter
        statements = []

        class Recorder:
            def execute(inner, sql, params):
                statements.append(params)
                return self.db.execute(sql, params)

        self.assertEqual(kb.find_command(Recorder(), "sh ntp ntp ntp"), [])
        self.assertEqual(statements[0], ["sh", "sh\uffff", "% ntp%"])

    def test_each_command_is_parsed_once_a_call(self):
        # every pass reads the rows of "show ..." again; their commands' words are parsed on the first
        with mock.patch.object(kb, "command_words", wraps=kb.command_words) as words, \
                mock.patch.object(kb, "command_groups", wraps=kb.command_groups) as groups:
            self.assertEqual(self.find(" ".join(["s"] * 30)), [])
        for spy in (words, groups):
            parsed = [call.args[0] for call in spy.call_args_list]
            self.assertTrue(parsed)
            self.assertEqual(len(parsed), len(set(parsed)), spy)


class CommandRankTests(unittest.TestCase):
    """SRV-02 (audit 2026-11-05): a keyword typed in full ranks before one it only abbreviates, and a command of another
    version of the typed protocol ("ip" read as "ipv6") ranks after the typed one's. The rows are the KB's own
    (kb-v9, served by p9er-20261102), with their documents, lines, platforms and releases."""

    ROWS = [
        ("ipv6 ospf mtu-ignore", "syntax", "iosxe-router", "iosxe-cr-ipv6-core", 8724, "3.4S"),
        ("ip ospf mtu-ignore [disable]", "syntax", "iosxe-router", "iosxe-cr-ospf", 2134, "3.2S"),
        ("ip ospf mtu-ignore", "example", "iosxe-router", "iosxe-cr-ospf", 2159, "3.2S"),
        ("ip ospf authentication-key [0 | 7] <password>", "syntax", "iosxe-router", "iosxe-cr-ospf", 1726, "3.2S"),
        ("ip ospf authentication [key-chain <name> | message-digest | null]", "syntax", "iosxe-router",
         "iosxe-cr-ospf", 1678, "3.2S"),
        ("ipv6 ospf authentication {null | ipsec spi <spi> <authentication-algorithm> [<key-encryption-type>] "
         "[<key>]}", "syntax", "iosxe-router", "iosxe-cr-ipv6-core", 8353, "3.4S"),
        ("ipv6 summary-address eigrp <as-number> <ipv6-address> [<admin-distance>]", "syntax", "iosxe-router",
         "iosxe-cr-eigrp", 2408, "12.2(33)SRB"),
        ("ip summary-address eigrp <as-number> <ip-address> <mask> [<admin-distance>] [leak-map <name>]", "syntax",
         "iosxe-router", "iosxe-cr-eigrp", 2086, "2.1"),
        # the command reference's syntax line as the corpus text has it: the placeholder glued on (SRV-03)
        ("ip ospf costinterface-cost", "syntax", "iosxe-router", "iosxe-cr-ospf", 1788, "3.2S"),
        ("ipv6 ospf cost {<interface-cost> | dynamic [weight {throughput <percent> | resources <percent> | "
         "latency <percent> | L2-factor <percent> | [hysteresis | threshold <threshold-value>]}]}", "syntax",
         "iosxe-router", "iosxe-cr-ipv6-core", 8431, "3.4S"),
        ("ip ospf cost <cost>", "task", "cat9300", "c9300-26x-rtng", 3508, "3.2S"),
        ("ipv6 ospf priority <number-value>", "syntax", "iosxe-router", "iosxe-cr-ipv6-core", 8882, "3.4S"),
        ("ip ospf priority <number>", "task", "cat9300", "c9300-26x-rtng", 3523, "3.2S"),
        ("show ip dhcp snooping binding [<ip-address>] [<mac-address>] [vlan <vlan>] [interface <type> <number>]",
         "syntax", "iosxe-router", "iosxe-cr-ipaddr", 16701, "12.2(18)SXE"),
        ("sh ip dhcp snooping binding", "example", "ios", "cisco-tn-dai-ipsg", 250, None),
        ("show running-config", "syntax", "cat9300", "c9300-179-cr", 11036, "16.10.1"),
        ("show running identity policy [<name>]", "syntax", "iosxe-router", "iosxe-cr-sec-i-l", 900,
         "12.2(18)SX"),
        ("show ipv6 pim [vrf <vrf-name>] neighbor [detail] [<interface-type> <interface-number> | count]", "syntax",
         "cat9300", "c9300-179-cr", 20415, "16.5.1a"),
        ("show ip pim neighbor [<type> <number>]", "task", "iosxe-router", "iosxe17-rtr-ipmcast-cg", 6372, None),
    ]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        paths = build_root(Path(cls.tmp.name))
        src = Path(cls.tmp.name) / "kb-src"
        rows = json.loads((src / "cli_index.json").read_text(encoding="utf-8"))
        rows += [{"command": command, "mode": "config-if", "platform": platform, "doc_key": doc_key,
                  "line": line, "kind": kind, "release": release}
                 for command, kind, platform, doc_key, line, release in cls.ROWS]
        (src / "cli_index.json").write_text(json.dumps(rows), encoding="utf-8")
        kb.build_kb(src, paths.root / "kb" / "kb.sqlite")
        cls.db = kb.open_kb(paths.root / "kb" / "kb.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def commands(self, q, **kw):
        return [r["command"] for r in kb.find_command(self.db, q, **kw)]

    def test_a_keyword_typed_in_full_ranks_before_one_it_abbreviates(self):
        # before: "ipv6 ospf mtu-ignore" first (shorter than "... [disable]"), and "authentication-key" before
        # "authentication" (shorter); the IPv6 rows now come last, after the IPv4 example too
        for release in (None, "17.9"):
            with self.subTest(release=release):
                self.assertEqual(self.commands("ip ospf mtu-ignore", release=release),
                                 ["ip ospf mtu-ignore [disable]", "ip ospf mtu-ignore", "ipv6 ospf mtu-ignore"])
                self.assertEqual(self.commands("ip ospf authentication", release=release),
                                 ["ip ospf authentication [key-chain <name> | message-digest | null]",
                                  "ip ospf authentication-key [0 | 7] <password>",
                                  self.ROWS[5][0]])
                self.assertEqual(self.commands("ip summary-address eigrp", release=release)[0], self.ROWS[7][0])
        # abbreviations still match: "ip os mtu" abbreviates "ospf" and "mtu-ignore" in each row alike
        self.assertEqual(self.commands("ip os mtu")[0], "ip ospf mtu-ignore [disable]")
        # lookup_id's CLI fallback (find_command with min_keywords 2, whole_keywords) ranks the same way
        self.assertEqual([r["command"] for r in kb.lookup(self.db, "ip ospf mtu-ignore")["results"]],
                         ["ip ospf mtu-ignore [disable]", "ip ospf mtu-ignore", "ipv6 ospf mtu-ignore"])
        self.assertEqual(kb.lookup(self.db, "ip ospf authentication")["results"][0]["command"],
                         "ip ospf authentication [key-chain <name> | message-digest | null]")

    def test_the_kind_still_ranks_before_the_abbreviations(self):
        # the audit's two regressions of a key with the abbreviations before the kind: the command reference's
        # syntax row (its keyword glued to the placeholder) stays first for "ip ospf cost", and the syntax row
        # stays before an example that has "sh" literally
        self.assertEqual(self.commands("ip ospf cost"),
                         ["ip ospf costinterface-cost", "ip ospf cost <cost>", self.ROWS[9][0]])
        got = kb.find_command(self.db, "sh ip dhcp snooping binding")
        self.assertEqual([(r["command"], r["kind"]) for r in got],
                         [(self.ROWS[13][0], "syntax"), ("sh ip dhcp snooping binding", "example")])

    def test_closeness_still_ranks_before_the_abbreviations(self):
        # "show running" completes "show running-config"; "show running identity policy" takes "running" whole
        # but needs two keywords more: the complete command stays first
        self.assertEqual(self.commands("show running"),
                         ["show running-config", "show running identity policy [<name>]"])

    def test_another_version_of_the_typed_protocol_ranks_last(self):
        # before: the IPv6 syntax row first for an IPv4 command (its task rows only after it)
        self.assertEqual(self.commands("ip ospf priority"), ["ip ospf priority <number>", self.ROWS[11][0]])
        self.assertEqual(self.commands("ip ospf cost 10"), ["ip ospf cost <cost>", self.ROWS[9][0]])
        self.assertEqual(self.commands("sh ip pim nei"), ["show ip pim neighbor [<type> <number>]", self.ROWS[17][0]])
        # the IPv6 command typed: as before
        self.assertEqual(self.commands("ipv6 ospf priority"), ["ipv6 ospf priority <number-value>"])
        self.assertEqual(self.commands("ipv6 ospf mtu-ignore"), ["ipv6 ospf mtu-ignore"])
        for word, keyword in (("ip", "ipv6"), ("ip", "ipv4"), ("ospf", "ospfv3"), ("ike", "ikev2"), ("vpn", "vpnv4")):
            self.assertTrue(kb.other_version(word, keyword), (word, keyword))
        for word, keyword in (("ip", "ip"), ("ip", "ipc"), ("ip", "ipv"), ("ipv", "ipv6"), ("sh", "show"),
                              ("ospf", "ospfv3x"), ("ip", "ip-v6"), ("authentication", "authentication-key")):
            self.assertFalse(kb.other_version(word, keyword), (word, keyword))

    def test_the_whole_keyword_rule_of_lookup_is_unchanged(self):
        # match_arguments now always reports the keywords; only lookup's passes that let a word go require them
        # whole (CommandIndexTests: "ospf area design"); its first pass takes abbreviations as before
        self.assertEqual(kb.lookup(self.db, "sh ip ospf nei")["kind"], "cli")
        self.assertEqual(kb.lookup(self.db, "ip os mtu")["results"][0]["command"], "ip ospf mtu-ignore [disable]")


class MatchCommandTests(unittest.TestCase):

    def words(self, command):
        return kb.command_words(command)

    def test_command_words(self):
        self.assertEqual(
            self.words("show ip ospf [<process-id>] neighbor {detail | summary}"),
            [("show", False, False), ("ip", False, False), ("ospf", False, False),
             ("<process-id>", True, True), ("neighbor", False, False),
             ("detail", False, True), ("summary", False, True)],
        )

    def test_matching(self):
        cmd = self.words("show ip ospf [<process-id> <area-id>] database [nssa-external] [<link-state-id>]")
        self.assertEqual(kb.match_command(["sh", "ip", "ospf", "data", "nssa"], cmd), 0)
        self.assertEqual(kb.match_command(["sh", "ip", "ospf", "1", "0", "database"], cmd), 0)
        self.assertIsNone(kb.match_command(["ip", "ospf"], cmd))           # must start at "show"
        self.assertIsNone(kb.match_command(["sh", "bgp"], cmd))            # "ip" is required
        self.assertEqual(kb.match_command(["sh", "ip"], cmd), 2)           # ospf, database left
        self.assertEqual(kb.match_command(["route-map", "MYMAP", "permit", "10"],
                                          self.words("route-map <map-tag> [permit | deny] [<seq>]")), 0)
        # an optional first word ("[no]") may be passed over; the index files it under "ip"
        no_form = "[no] ip ospf cost <cost>"
        self.assertEqual(kb.match_command(["ip", "ospf", "cost", "10"], self.words(no_form)), 0)
        self.assertEqual(kb._keywords(no_form), ("ip", "no ip ospf cost"))
        self.assertEqual(kb.version_key("17.11.1a"), (17, 11, 1))
        self.assertEqual(kb.version_key("12.2(33)SXH"), (12, 2))
        self.assertIsNone(kb.version_key(None))


class SectionAnchorTests(unittest.TestCase):
    """kb.section_anchor names a section the way rag_core.sections does."""

    def test_typed_references(self):
        cases = {
            "9.1.2": "9.1.2", "9.1.2.": "9.1.2", "1.0": "1", "§3.2": "3.2", "§ 3.2": "3.2",
            "Section 3.2": "3.2", "section-3.2": "3.2", "Sec. 4": "4",
            "A.1": "appendix-a.1", "Appendix B": "appendix-b", "APPENDIX II": "appendix-ii",
            "appendix-a.1": "appendix-a.1", "3.2-2": "3.2-2",
            "Security Considerations": "security-considerations",
            "security-considerations": "security-considerations",
            "Author's Addresses": "author-s-addresses",
        }
        for typed, anchor in cases.items():
            self.assertEqual(kb.section_anchor(typed), anchor, typed)

    def test_agrees_with_the_section_parser(self):
        text = "\n".join([
            "Network Working Group", "Request for Comments: 9999", "", "",
            "Abstract", "", "   Body.", "",
            "1.0  Overview", "", "   Body.", "",
            "2.1.  Details", "", "   Body.", "",
            "Security Considerations", "", "   Body.", "",
            "Appendix A.  Extra", "", "   Body.", "",
        ])
        _, sections = parse_sections(text, "rfc")
        typed = {"Abstract": "Abstract", "1.0  Overview": "1.0", "2.1.  Details": "2.1",
                 "Security Considerations": "Security Considerations", "Appendix A.  Extra": "Appendix A"}
        self.assertEqual({s.heading: s.anchor for s in sections if s.heading},
                         {heading: kb.section_anchor(ref) for heading, ref in typed.items()})


if __name__ == "__main__":
    unittest.main()
