"""/v2/lookup's fallback (RootReader.headed): an identifier kb.lookup leaves "unknown" that is a PowerShell cmdlet or
about topic, an HTTP API operation "METHOD /path" or a bare "/path" resolves to the sections headed with it, results
of kind "section". The tiny root of tests/v2_root.py with its opt-in documents (powershell opt-in), and a glossary
term that is also a cmdlet heading."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import json
import unittest
from pathlib import Path
from unittest import mock

from rag_core import api_v2
from rag_core.reader import Doc, RootReader, Sec, named_identifier, path_segments, segments_match

from test_api_v2 import AUTH, RootClient
from v2_root import PS_NETTCPIP, add_opt_in_documents

SECTION_KEYS = {"kind", "doc_key", "doc_type", "section_id", "heading", "cite", "local"}
API = "ise31-ers-networkdevice"


def prepare(paths, kb_src):
    """The opt-in documents, and "Test-NetConnection" in the glossary: an identifier that resolves today."""
    add_opt_in_documents(paths)
    glossary = json.loads((kb_src / "glossary.json").read_text(encoding="utf-8"))
    glossary.append({"term": "Test-NetConnection", "expansion": "the PowerShell connection test", "aliases": [],
                     "refs": [], "category": "windows"})
    (kb_src / "glossary.json").write_text(json.dumps(glossary), encoding="utf-8")


class LookupHeadingTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = RootClient(retrieval={"opt_in_doc_types": ["system", "powershell"]}, prepare=prepare)
        cls.c = cls.root.client

    @classmethod
    def tearDownClass(cls):
        cls.root.close()

    def lookup(self, identifier):
        r = self.c.get("/v2/lookup", params={"id": identifier}, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def ids(self, identifier):
        body = self.lookup(identifier)
        if body["kind"] == "unknown":
            return None
        self.assertEqual(body["kind"], "section", identifier)
        return [result["section_id"] for result in body["results"]]

    def test_a_cmdlet_resolves_to_its_section(self):
        body = self.lookup("Get-NetIPAddress")
        self.assertEqual(body, {"kind": "section", "degraded": [], "results": [{
            "kind": "section", "doc_key": "ps-nettcpip", "doc_type": "powershell",
            "section_id": "ps-nettcpip#get-netipaddress", "heading": "Get-NetIPAddress",
            "cite": "NetTCPIP › Get-NetIPAddress", "local": "ps-nettcpip.txt:3-7"}]})
        lines = PS_NETTCPIP.split("\n")
        self.assertEqual((lines[2], lines[6]), ("## Get-NetIPAddress", "```"))         # local: lines 3-7
        # any case, padded; the section opens; an opt-in doc type is found all the same (an explicit identifier)
        for typed in ("get-netipaddress", "  GET-NETIPADDRESS "):
            self.assertEqual(self.lookup(typed), body, typed)
        section = self.c.get("/v2/section", params={"ref": "ps-nettcpip#get-netipaddress"}, headers=AUTH).json()
        self.assertEqual(section["cite"], body["results"][0]["cite"])
        # a heading that only starts with the cmdlet name is not it
        self.assertNotIn("ps-nettcpip#get-netipaddress-examples", self.ids("Get-NetIPAddress"))

    def test_an_about_topic_resolves_to_its_document(self):
        body = self.lookup("about_splatting")
        (result,) = body["results"]
        self.assertEqual(set(result), SECTION_KEYS)
        outline = self.c.get("/v2/outline", params={"doc": "ps-about-splatting"}, headers=AUTH).json()["sections"]
        top = next(s for s in outline if s["section_id"] == "ps-about-splatting#about-splatting")
        self.assertEqual((result["heading"], result["cite"], result["local"]),
                         ("about_Splatting", "about_Splatting",
                          f"ps-about-splatting.txt:{top['line_start']}-{top['line_end']}"))

    def test_an_api_operation_resolves_to_the_sections_of_its_path_template(self):
        post = f"{API}#post-ers-config-networkdevice-create-network-device"
        by_id, by_name, listing = (f"{API}#get-ers-config-networkdevice-id", f"{API}#get-ers-config-networkdevice-name-name",
                                   f"{API}#get-ers-config-networkdevice")
        for identifier, expected in (
                ("POST /ers/config/networkdevice", [post]),
                ("post /ers/config/networkdevice/", [post]),
                ("GET /ers/config/networkdevice/{id}", [by_id]),             # the template, literally
                ("GET /ers/config/networkdevice/0a1b2c3d", [by_id]),         # a value in its place
                ("GET /ers/config/networkdevice/name/edge-1", [by_name]),
                ("GET /ers/config/networkdevice", [listing]),
                ("/ers/config/networkdevice", [post, listing]),              # every method, in document order
                ("/ers/config/networkdevice/{deviceId}", [by_id]),
                ("DELETE /ers/config/networkdevice/{id}", None),
                ("GET /ers/config", None),
                ("GET /ers/config/networkdevice/name", [by_id]),             # "name" in the {id} place
                ("GET /ers/{section}/networkdevice", None),                  # a template asked for fits no literal
                ("Getting started", None)):
            with self.subTest(identifier=identifier):
                self.assertEqual(self.ids(identifier), expected)
        (result,) = self.lookup("POST /ers/config/networkdevice")["results"]
        self.assertEqual((result["doc_type"], result["heading"], result["cite"]),
                         ("cisco", "POST /ers/config/networkdevice - Create network device",
                          "ISE ERS API: Network Device › Network Device API › POST /ers/config/networkdevice - "
                          "Create network device"))
        self.assertTrue(result["local"].startswith(f"{API}.txt:5-"))

    def test_identifiers_that_resolve_today_keep_their_answer(self):
        # the fallback is not even consulted for them; "Test-NetConnection" heads a section and stays a glossary term
        with mock.patch.object(RootReader, "headed", side_effect=AssertionError("consulted")):
            self.assertEqual(self.lookup("Test-NetConnection")["kind"], "glossary")
            self.assertEqual(self.lookup("RFC 3101")["results"][0]["doc_key"], "rfc3101")
            self.assertEqual(self.lookup("rfc 4271 §9.1.2")["kind"], "rfc_section")
            self.assertEqual(self.lookup("LSA type 7")["kind"], "iana")
            self.assertEqual(self.lookup("NSSA")["kind"], "glossary")
            self.assertEqual(self.lookup("sh ip ospf nei")["kind"], "cli")

    def test_what_names_no_heading_stays_unknown(self):
        for identifier in ("Get-NetRoute", "IS-IS", "zzz", "about_Nothing", "/no/such/path"):
            with self.subTest(identifier=identifier):
                self.assertEqual(self.lookup(identifier), {"kind": "unknown", "results": [], "degraded": []})


class HeadedTests(unittest.TestCase):

    def test_what_is_an_identifier(self):
        for text in ("Get-NetIPAddress", "invoke-restmethod", "ForEach-Object", "ConvertTo-Json", "about_Splatting",
                     "ABOUT_Hash_Tables", " Set-NetIPInterface ", "about_Windows_PowerShell_5.1"):
            self.assertIsNotNone(named_identifier(text), text)
        for text in ("IS-IS", "VRF-Lite", "Route-Map", "Auto-RP", "Get-", "Get-Net IP", "Get-NetIPAddress examples",
                     "type-7", "about_", "Get-Net-IP", "about_Splatting.", "about_.1", "about_x..1"):
            self.assertIsNone(named_identifier(text), text)
        self.assertEqual(path_segments("/ers/config/"), ("ers", "config"))
        self.assertEqual(path_segments("/"), ())
        self.assertTrue(segments_match(("ers", "x1"), ("ers", "{id}")))
        self.assertTrue(segments_match(("ers", "{name}"), ("ers", "{id}")))
        self.assertFalse(segments_match(("ers", "{name}"), ("ers", "name")))
        self.assertFalse(segments_match(("ers",), ("ers", "{id}")))

    def test_at_most_ten_by_doc_key_then_document_order_and_built_once(self):
        corp = RootReader.__new__(RootReader)
        corp._headings = None
        corp.docs = {}
        for n in (11, 3, 7, 0, 5, 9, 1, 10, 2, 8, 4, 6):             # not in doc_key order
            key = f"ps-mod{n:02d}"
            doc = Doc(f"doc_{n}", key, "powershell", None, None, None, None, Path(f"{key}.txt"))
            doc.sections = [Sec(f"{key}#{i}", key, i, 2, str(i), heading, None, i + 1, i + 1, 10)
                            for i, heading in enumerate(("Get-Item", "Overview", "get-item"))]
            corp.docs[key] = doc
        found = corp.headed("GET-ITEM")
        self.assertEqual(len(found), 10)
        self.assertEqual([(doc.doc_key, sec.ordinal) for doc, sec in found],
                         [(f"ps-mod{n:02d}", i) for n in range(5) for i in (0, 2)])
        index = corp.heading_index()
        self.assertIs(corp.heading_index(), index)
        self.assertEqual(set(index[0]), {"get-item"})                 # only identifier headings are kept
        self.assertEqual(len(corp.headed("Get-Item", limit=30)), 24)

    def test_an_about_topic_with_a_version_in_its_name_is_headed(self):
        corp = RootReader.__new__(RootReader)
        corp._headings = None
        key = "ps-about-windows-powershell-5-1"
        doc = Doc("doc_a", key, "powershell", None, None, None, None, Path(f"{key}.txt"))
        headings = ((1, "about_Windows_PowerShell_5.1"), (2, "Long description"))
        doc.sections = [Sec(f"{key}#{i}", key, i, level, str(i), heading, None, i + 1, i + 1, 10)
                        for i, (level, heading) in enumerate(headings)]
        corp.docs = {key: doc}
        for typed in ("about_Windows_PowerShell_5.1", " ABOUT_windows_powershell_5.1 "):
            self.assertEqual([(d.doc_key, s.ordinal) for d, s in corp.headed(typed)], [(key, 0)], typed)
        self.assertEqual(corp.headed("about_Windows_PowerShell_5"), [])
        self.assertEqual(set(corp.heading_index()[0]), {"about_windows_powershell_5.1"})

    def test_an_about_topic_with_a_hyphen_in_its_name_is_headed(self):
        # corpus-v8.1 has four (lookup_id (a), corpus-v9): they answered "unknown"
        topics = ("about_Built-in_Functions", "about_Case-Sensitivity", "about_Member-Access_Enumeration",
                  "about_WS-Management_Cmdlets")
        for text in topics:
            self.assertEqual(named_identifier(text), text.casefold(), text)
        for text in ("about_x-", "about_-x", "about_x--1", "about_x-.1", "about_Built-in Functions", "about_x-y."):
            self.assertIsNone(named_identifier(text), text)
        corp = RootReader.__new__(RootReader)
        corp._headings = None
        corp.docs = {}
        for n, topic in enumerate(topics):
            key = "ps-" + topic.casefold().replace("_", "-")
            doc = Doc(f"doc_{n}", key, "powershell", None, None, None, None, Path(f"{key}.txt"))
            doc.sections = [Sec(f"{key}#{i}", key, i, level, str(i), heading, None, i + 1, i + 1, 10)
                            for i, (level, heading) in enumerate(((1, topic), (2, "Short description")))]
            corp.docs[key] = doc
        for topic in topics:
            key = "ps-" + topic.casefold().replace("_", "-")
            for typed in (topic, f" {topic.upper()} "):
                self.assertEqual([(d.doc_key, s.ordinal) for d, s in corp.headed(typed)], [(key, 0)], typed)
        self.assertEqual(set(corp.heading_index()[0]), {topic.casefold() for topic in topics})
        self.assertEqual(corp.headed("about_Built"), [])


class NoStructureTests(unittest.TestCase):

    def test_a_root_without_the_structure_index_answers_unknown(self):
        root = RootClient(structure=False)
        self.addCleanup(root.close)
        body = root.client.get("/v2/lookup", params={"id": "Get-NetIPAddress"}, headers=AUTH).json()
        self.assertEqual(body, {"kind": "unknown", "results": [], "degraded": ["structure"]})
        self.assertIs(api_v2.root_reader(root.client.app.state).structured, False)


if __name__ == "__main__":
    unittest.main()
