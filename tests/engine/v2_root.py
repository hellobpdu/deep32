"""A tiny RAG root for the API v2 / structure / KB tests: three RFC-like and two Cisco-like
source texts, chunk rows shaped like the text adapter's output, and a fake KB JSON set.

build_root(dir) writes <dir>/root (rag.db, sources/, kb JSON in <dir>/kb-src) without calling
the code under test except rag_core.db.initialize (the schema).
"""
from __future__ import annotations

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_target  # noqa: E402,F401  the engine copy under test, on sys.path (engine_target.py)

import hashlib
import json
import re
from pathlib import Path

from rag_core.db import connect, initialize
from rag_core.paths import RAGPaths


FOOTER = "Murphy                      Standards Track                     [Page {n}]"
HEADER = "RFC 3101                    OSPF NSSA Option                January 2003"

RFC3101 = "\n".join([
    "",
    "Network Working Group                                          P. Murphy",
    "Request for Comments: 3101                          US Geological Survey",
    "Obsoletes: 1587                                             January 2003",
    "Category: Standards Track",
    "",
    "",
    "               The OSPF Not-So-Stubby Area (NSSA) Option",
    "",
    "Status of this Memo",
    "",
    "   This document specifies an Internet standards track protocol.",
    "",
    "Abstract",
    "",
    "   This memo documents an optional type of OSPF area that is somewhat",
    "   humorously referred to as a not-so-stubby area.",
    "",
    "Table Of Contents",
    "",
    "   1.0 Overview ..................................................... 2",
    "   3.2 Translating Type-7 LSAs into Type-5 LSAs ..................... 3",
    "",
    "1.0  Overview",
    "",
    "   NSSAs are similar to stub areas.  Type-7 LSAs carry external",
    "   routing information within an NSSA.",
    "",
    "3.0 Intra-AS Implementation Details",
    "",
    "3.1 Type-7 Translator Election",
    "",
    "   A border router defers when another border router has bit Nt set or",
    "   a higher router ID.  The TranslatorStabilityInterval defaults to 40",
    "   seconds.",
    "",
    FOOTER.format(n=2),
    "\f" + HEADER,
    "",
    "",
    "3.2 Translating Type-7 LSAs into Type-5 LSAs",
    "",
    "   This step is performed as part of the NSSA's Dijkstra calculation.",
    "   Only installed",
    "",
    FOOTER.format(n=3),
    "\f" + HEADER,
    "",
    "",
    "   Type-7 LSAs should be examined.  The transla-",
    "   tion is skipped when the P-bit is clear.",
    "",
    "      (1) If the Type-7 LSA has the P-bit clear, or its forwarding",
    "          address is set to 0.0.0.0, then do nothing with this Type-7",
    "          LSA.",
    "",
    "                 4. The LSA with the higher router ID.",
    "",
    "Appendix A: The Options Field",
    "",
    "   The N/P-bit is used in Type-7 LSAs.",
    "",
    "Author's Addresses",
    "",
    "   Pat Murphy",
    "",
])

RFC4271 = "\n".join([
    "Network Working Group                                    Y. Rekhter, Ed.",
    "Request for Comments: 4271                                    T. Li, Ed.",
    "",
    "                 A Border Gateway Protocol 4 (BGP-4)",
    "",
    "Abstract",
    "",
    "   This document discusses the Border Gateway Protocol (BGP).",
    "",
    "9.  UPDATE Message Handling",
    "",
    "   An UPDATE message may be received only in the Established state.",
    "",
    "9.1.  Decision Process",
    "",
    "   The Decision Process selects routes for subsequent advertisement.",
    "",
    "9.1.2.  Phase 2: Route Selection",
    "",
    "   The Phase 2 decision function is invoked on completion of Phase 1.",
    "   Routes that have AS loops are excluded from the Phase 2 decision.",
    "",
    "9.1.2.2.  Breaking Ties (Phase 2)",
    "",
    "   Remove from consideration routes with less-preferred MULTI_EXIT_DISC",
    "   attributes.  The ORIGINATOR_ID is treated as the BGP Identifier.",
    "",
])

RFC1587 = "\n".join([
    "Network Working Group                                           R. Coltun",
    "Request for Comments: 1587                                      V. Fuller",
    "",
    "                        The OSPF NSSA Option",
    "",
    "1.  Introduction",
    "",
    "   This document describes a new optional type of OSPF area, the NSSA.",
    "   Type-7 LSAs are translated by the NSSA border router.",
    "",
])

CISCO_179 = "\n".join([
    "# Configuring OSPF",
    "OSPF is a link-state interior gateway protocol.",
    "## Restrictions for OSPF NSSA",
    "An NSSA ABR translates Type-7 LSAs into Type-5 LSAs only when it is the elected translator.",
    "## Examples",
    "```",
    "Router(config)# router ospf 1",
    "# a comment inside a fence is not a heading",
    "Router(config-router)# area 1 nssa translate type7 always",
    "```",
    "### Configuring an NSSA ABR as a Forced NSSA LSA Translator",
    "Use the area nssa translate type7 always command to force translation.",
    "area area-id nssa translate type7 [always]",
    "## Examples",
    "The show ip ospf database nssa-external command displays Type-7 LSAs.",
    "### show ip ospf neighbor",
    "To display OSPF neighbor information, use the show ip ospf neighbor command.",
    "show ip ospf neighbor [interface-type interface-number] [neighbor-id] [detail]",
    "",
])

CISCO_1718 = "\n".join([
    "# Configuring OSPFv3",
    "OSPFv3 carries IPv6 routes.",
    "## OSPFv3 NSSA",
    "In Cisco IOS XE 17.18 the NSSA translator also handles OSPFv3 Type-7 LSAs.",
    "",
])

# an excerpt of ise31-compat (corpus-v5), not in DOCS: tests add it with add_document. Its doc_key
# sorts after the c9300 guides, so a grep in doc_key order reaches it third.
ISE31_COMPAT = "\n".join([
    "# Cisco Identity Services Engine Network Component Compatibility, Release 3.1",
    "### Verified and supported ciphers",
    "Cisco ISE supports TLS versions 1.0, 1.1, and 1.2.",
    "This table lists the supported cipher suites:",
    "Table 4. Verified and supported cipher suites on Cisco ISE",
    "Cipher suite | When Cisco ISE is configured as an EAP server | When Cisco ISE is configured as a RADIUS DTLS "
    "server | When Cisco ISE downloads CRL from HTTPS or a secure LDAP server",
    "TLS 1.0 support | When TLS 1.0 is allowed | (DTLS server supports only DTLS 1.2) | Allow TLS 1.0 option is "
    "disabled by default in Cisco ISE 2.3 and above.",
    "DHE-RSA-AES256-SHA256 | No | Yes",
    "Validate ExtendedKeyUsage | Client certificate should have KeyUsage=Key Encipherment and "
    "ExtendedKeyUsage=Client Authentication for the following ciphers: | AES256-SHA256",
    "",
])

# opt-in collections (tests/test_opt_in_collections.py), not in DOCS: add_opt_in_documents adds them. The system
# notes share the query terms of the OSPF texts (NSSA, Type-7, translator), and the PowerShell module those of the
# Cisco guides (IP, interface, address), so a search that does not leave them out finds them.
SYSTEM_NOTES = "\n".join([
    "# deep32-engine test notes",
    "The fixture asks which NSSA ABR is the elected translator of Type-7 LSAs.",
    "## Type-7 translator queries",
    "An NSSA Type-7 translator query must rank the OSPF texts, not these notes.",
    "",
])

PS_NETTCPIP = "\n".join([
    "# NetTCPIP",
    "The NetTCPIP module manages the IP address and interface configuration of a Windows host.",
    "## Get-NetIPAddress",
    "Gets the IP address configuration: IPv4 and IPv6 addresses and the IP interface of each address.",
    "```powershell",
    "Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias Ethernet",
    "```",
    "## Test-NetConnection",
    "Displays diagnostic information for a connection, such as a ping of an IP address.",
    "## Get-NetIPAddress examples",
    "Get-NetIPAddress lists every IP address of every interface.",
    "",
])

PS_ABOUT_SPLATTING = "\n".join([
    "# about_Splatting",
    "Splatting passes a collection of parameter values to a command as a unit.",
    "## Splatting with hash tables",
    "Use a hash table to splat parameter name and value pairs.",
    "",
])

ISE_ERS_API = "\n".join([
    "# Network Device API",
    "The External RESTful Services (ERS) API of Cisco ISE manages network devices.",
    "## Getting started",
    "Enable ERS on the primary administration node first.",
    "## POST /ers/config/networkdevice - Create network device",
    "Creates a network device with its RADIUS shared secret.",
    "## GET /ers/config/networkdevice/{id}",
    "Gets the network device with this id.",
    "## GET /ers/config/networkdevice/name/{name}",
    "Gets the network device with this name.",
    "## GET /ers/config/networkdevice",
    "Lists the network devices.",
    "",
])

OPT_IN_DOCS = [
    # doc_key, doc_type, text, title
    ("sys-test-notes", "system", SYSTEM_NOTES, "deep32-engine test notes"),
    ("ps-nettcpip", "powershell", PS_NETTCPIP, "NetTCPIP"),
    ("ps-about-splatting", "powershell", PS_ABOUT_SPLATTING, "about_Splatting"),
    ("ise31-ers-networkdevice", "cisco", ISE_ERS_API, "ISE ERS API: Network Device"),
]


def add_opt_in_documents(paths: RAGPaths) -> None:
    """OPT_IN_DOCS into a root build_root made (before index_root)."""
    for key, doc_type, text, title in OPT_IN_DOCS:
        add_document(paths, key, doc_type, text, title)


RFC_HEADINGS = {
    "Status of this Memo", "Abstract", "Table Of Contents", "1.0  Overview",
    "3.0 Intra-AS Implementation Details", "3.1 Type-7 Translator Election",
    "3.2 Translating Type-7 LSAs into Type-5 LSAs", "Appendix A: The Options Field",
    "Author's Addresses", "9.  UPDATE Message Handling", "9.1.  Decision Process",
    "9.1.2.  Phase 2: Route Selection", "9.1.2.2.  Breaking Ties (Phase 2)",
    "1.  Introduction",
}

DOCS = [
    # doc_key, doc_type, text, title (None: as ingested without one)
    ("rfc3101", "rfc", RFC3101, None),
    ("rfc4271", "rfc", RFC4271, None),
    ("rfc1587", "rfc", RFC1587, None),
    ("c9300-179-rtng", "cisco", CISCO_179, "Routing Configuration Guide, 17.9"),
    ("c9300-1718-rtng", "cisco", CISCO_1718, "Routing Configuration Guide, 17.18"),
]


def rfc_paragraphs(text: str) -> list[str]:
    """What the RFC text adapter makes of a text: paragraphs without page furniture or
    headings, lines joined by single spaces (TOC paragraphs dropped)."""
    lines = [
        line for line in text.split("\n")
        if not re.search(r"\[Page \d+\]$", line) and not line.startswith("\fRFC")
    ]
    paragraphs, current = [], []
    for line in lines + [""]:
        if line.strip():
            current.append(line.strip())
        elif current:
            if len(current) == 1 and current[0] in RFC_HEADINGS:
                pass
            elif not any(re.search(r"\.{5,}", c) for c in current):
                paragraphs.append(" ".join(current))
            current = []
    return paragraphs


def cisco_units(text: str) -> list[str]:
    """What the Cisco text adapter makes of a text: one unit per line, fenced code as one
    unit, headings dropped."""
    units, fence = [], None
    for line in text.split("\n"):
        if line.startswith("```"):
            if fence is None:
                fence = []
            else:
                units.append("\n".join(fence))
                fence = None
        elif fence is not None:
            fence.append(line)
        elif line.strip() and not line.startswith("#"):
            units.append(line.strip())
    return units


def chunk_texts(doc_key: str, text: str) -> list[str]:
    if doc_key == "rfc3101":
        p = rfc_paragraphs(text)
        # p: header, title, status, abstract, overview, 3.1 text, 3.2 (a), 3.2 (b), (1), 4.,
        #    appendix, author. The v3 chunker absorbs 3.2 into 3.1 with a synthetic heading.
        return [
            "\n\n".join(p[0:4]),
            p[4],
            "\n\n".join([p[5], "## 3.2 Translating Type-7 LSAs into Type-5 LSAs", p[6]]),
            "\n\n".join(p[7:10]),
            "\n\n".join(p[10:]),
        ]
    if doc_key.startswith("rfc"):
        return rfc_paragraphs(text)
    units = cisco_units(text)
    return ["\n\n".join(units[i:i + 2]) for i in range(0, len(units), 2)]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def line_of(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.split("\n"), 1) if needle in line)


def write_kb(kb_dir: Path, *, cli_mismatch: str | None = "c9300-1718-rtng") -> None:
    kb_dir.mkdir(parents=True, exist_ok=True)

    def put(name, records, meta=None):
        (kb_dir / f"{name}.json").write_text(json.dumps(records), encoding="utf-8")
        (kb_dir / f"{name}.meta.json").write_text(
            json.dumps({"generator": "tests", "sha256": "x", **(meta or {})}), encoding="utf-8"
        )

    def rfc(n, title, status, **rel):
        return {
            "rfc": n, "title": title, "status": status, "date": "2003-01",
            "obsoletes": [], "obsoleted_by": [], "updates": [], "updated_by": [],
            "also": [], "in_corpus": True, **rel,
        }

    put("rfc_index", [
        rfc(3101, "The OSPF Not-So-Stubby Area (NSSA) Option", "Proposed Standard",
            obsoletes=[1587]),
        rfc(1587, "The OSPF NSSA Option", "Proposed Standard", obsoleted_by=[3101]),
        rfc(4271, "A Border Gateway Protocol 4 (BGP-4)", "Draft Standard", obsoletes=[1771],
            updated_by=[4724, 6286, 6608, 6793, 7606]),
        rfc(2119, "Key words for use in RFCs", "Best Current Practice", also=["BCP 14"],
            in_corpus=False),
        rfc(8174, "Ambiguity of Uppercase vs Lowercase", "Best Current Practice",
            also=["BCP 14"], in_corpus=False),
    ])
    put("errata", [
        {"id": 3001, "rfc": 3101, "status": "Verified", "type": "Technical", "section": "3.2",
         "summary": "P-bit wording", "url": "https://www.rfc-editor.org/errata/eid3001"},
        {"id": 3002, "rfc": 3101, "status": "Rejected", "type": "Editorial", "section": "3.1",
         "summary": "not an error", "url": "https://www.rfc-editor.org/errata/eid3002"},
        {"id": 3003, "rfc": 4271, "status": "Held for Document Update", "type": "Technical",
         "section": "Section 9.1.2", "summary": "tie breaking",
         "url": "https://www.rfc-editor.org/errata/eid3003"},
    ])
    put("iana", [
        {"registry": "OSPFv2 Link State (LS) Types", "code": "7", "name": "NSSA AS-external LSA",
         "description": "", "refs": ["RFC 3101"],
         "aliases": ["type-7", "Type 7 LSA", "type-7 LSA", "LSA type 7"], "url": "u"},
        {"registry": "BGP Path Attributes", "code": "9", "name": "ORIGINATOR_ID",
         "description": "", "refs": ["RFC 4456"], "aliases": ["BGP attribute 9"], "url": "u"},
        {"registry": "Address Family Numbers (AFI)", "code": "1", "name": "IP (IP version 4)",
         "description": "", "refs": [], "aliases": ["AFI 1", "IPv4"], "url": "u"},
        {"registry": "Subsequent Address Family Identifiers (SAFI)", "code": "128",
         "name": "MPLS-labeled VPN address", "description": "", "refs": ["RFC 4364"],
         "aliases": ["SAFI 128", "VPNv4"], "url": "u"},
        {"registry": "DSCP Pool 1 Codepoints", "code": "46", "name": "EF", "description": "",
         "refs": ["RFC 3246"], "aliases": ["DSCP EF", "DSCP 46"], "url": "u"},
    ])
    put("glossary", [
        {"term": "NSSA", "expansion": "Not-So-Stubby Area", "aliases": ["not so stubby area"],
         "refs": ["RFC 3101"], "category": "ospf"},
        {"term": "ABR", "expansion": "Area Border Router", "aliases": ["area border router"],
         "refs": ["RFC 2328"], "category": "ospf"},
    ])
    shas = {key: _sha(text) for key, _, text, _ in DOCS}
    if cli_mismatch:
        shas[cli_mismatch] = "0" * 64
    sources = {"sources": [{"doc_key": k, "sha256": s} for k, s in shas.items()]}
    put("cli_index", [
        {"command": "show ip ospf neighbor [<interface-type> <interface-number>] "
                    "[<neighbor-id>] [detail]",
         "mode": "privileged-exec", "platform": "cat9300", "doc_key": "c9300-179-rtng",
         "line": line_of(CISCO_179, "show ip ospf neighbor [interface"), "kind": "syntax",
         "release": "16.5.1a"},
        {"command": "area <area-id> nssa translate type7 [always]", "mode": "config-router",
         "platform": "cat9300", "doc_key": "c9300-179-rtng",
         "line": line_of(CISCO_179, "area area-id nssa"), "kind": "syntax", "release": "16.3.1"},
        {"command": "area 1 nssa translate type7 always", "mode": "config-router",
         "platform": "cat9300", "doc_key": "c9300-1718-rtng", "line": 4, "kind": "example",
         "release": "17.18"},
        {"command": "show ip ospf database nssa-external", "mode": "privileged-exec",
         "platform": "iosxe-router", "doc_key": "c9300-179-rtng",
         "line": line_of(CISCO_179, "nssa-external command"), "kind": "show-output",
         "release": None},
    ], sources)
    put("feature_history", [
        {"doc_key": "c9300-179-rtng", "feature": "OSPF NSSA", "release": "16.3.1",
         "info": "introduced", "line": line_of(CISCO_179, "elected translator")},
    ], sources)


def build_root(base: Path, *, with_kb_sources: bool = True) -> RAGPaths:
    """<base>/root with rag.db (documents + chunks, no structure tables yet) and sources/;
    the KB JSON set in <base>/kb-src."""
    paths = RAGPaths(base / "root")
    initialize(paths)
    for key, doc_type, text, title in DOCS:
        add_document(paths, key, doc_type, text, title)
    if with_kb_sources:
        write_kb(base / "kb-src")
    return paths


def add_document(paths: RAGPaths, key: str, doc_type: str, text: str, title: str | None) -> None:
    """One document of a tiny root: its source file under sources/<doc_type>/, its documents row
    and its chunks (chunk_texts)."""
    path = paths.root / "sources" / doc_type / f"{key}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    with connect(paths.sqlite / "rag.db") as conn:
        doc_id = "doc_" + _sha(text)
        ext = ".txt" if doc_type == "rfc" else ".pdf"
        conn.execute(
            """
            INSERT INTO documents (doc_id, sha256, source_name, source_uri,
                                   original_filename, title, status)
            VALUES (?, ?, ?, ?, ?, ?, 'CHUNKED')
            """,
            (doc_id, _sha(text), doc_type, f"https://example.test/{key}", key + ext, title),
        )
        for ordinal, chunk in enumerate(chunk_texts(key, text)):
            conn.execute(
                """
                INSERT INTO chunks (chunk_id, chunk_sha256, doc_id, ordinal,
                                    heading_path_json, text, embedding_text,
                                    token_count, char_count)
                VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?)
                """,
                (
                    "chk_" + _sha(f"{key}\0{ordinal}"), _sha(chunk), doc_id, ordinal,
                    chunk, chunk, len(chunk.split()), len(chunk),
                ),
            )
        conn.commit()
    conn.close()


def chunk_id(doc_key: str, ordinal: int) -> str:
    return "chk_" + _sha(f"{doc_key}\0{ordinal}")
