#!/usr/bin/env python3
"""Validate rfc_index.json, errata.json, iana.json and glossary.json (+ .meta.json) against BUILD-P1 section 4.

usage: validate_kb.py [--kb-dir DIR] [--corpus-dir DIR ...] [--cisco-dir DIR ...]

1. schema: required keys, types, enums, uniqueness, summary length, ref formats;
2. meta: each <name>.meta.json has the required keys and its sha256 matches the JSON file;
3. counts: cross-checked against the raw sources in $KB_RAW_DIR (not against the builders' own code);
4. spot checks: known facts, each confirmed both in the JSON and in the raw source text.
Exit status is non-zero if any check fails. No network access.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path

from build_iana import REGISTRIES as IANA_REGISTRIES  # what iana.json must cover (config only, not its parser)
from build_iana import ALIASES as IANA_ALIASES, IDENT as IANA_IDENT  # the bare-key rule of the qualify registries and
# its curated exceptions (rules and data, not the parser)
from build_rfc import CROSSCHECK_RFCS  # which official errata pages are cached (config only, not its parser)
from kbcommon import CISCO_CORPUS_DIRS, KB_DIR, RAW_DIR, RFC_CORPUS_DIRS, default_dirs

RFC_STATUS = {"Internet Standard", "Proposed Standard", "Draft Standard", "Best Current Practice", "Informational",
              "Experimental", "Historic", "Unknown"}
ERRATA_STATUS = {"Verified", "Held for Document Update", "Reported", "Rejected"}
ERRATA_TYPE = {"Technical", "Editorial"}
META_KEYS = {"source_urls", "fetched_at", "generator", "counts", "sha256"}


class Checker:
    def __init__(self):
        self.passed, self.failed = 0, []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed.append(f"{name}: {detail}" if detail else name)
        return ok


def load(kb: Path, name: str):
    return json.loads((kb / f"{name}.json").read_text(encoding="utf-8"))


def raw_text(name: str) -> str:
    return (RAW_DIR / name).read_text(encoding="utf-8", errors="replace")


def xml_record(xml: str, registry_id: str, pattern: str) -> bool:
    """True if some <record> inside <registry id=registry_id> matches the regex `pattern` (whitespace-collapsed)."""
    m = re.search(r'<registry id="%s">(.*?)(?=<registry id=|\Z)' % re.escape(registry_id), xml, re.S)
    if not m:
        return False
    return any(re.search(pattern, " ".join(r.split())) for r in re.findall(r"<record.*?</record>", m.group(1), re.S))


def feed_rfc(e: dict):
    m = re.fullmatch(r"RFC0*(\d+)", str(e.get("doc-id", "")))
    return int(m.group(1)) if m else None


def errata_cards(html: str) -> tuple:
    """({id: (status, type)}, {every eid linked}) from an official rfc-editor.org/errata/rfcN page."""
    t = " ".join(html.split())
    card = (r'Errata-ID: <a href="/eid(\d+)/?">\d+</a>.*?Status:</dt> <dd[^>]*> <span[^>]*>([^<]+)</span>'
            r'.*?Type:</dt> <dd[^>]*> <span[^>]*>([^<]+)</span>')
    cards = {int(m.group(1)): (m.group(2).strip(), m.group(3).strip()) for m in re.finditer(card, t)}
    return cards, {int(i) for i in re.findall(r'href="/eid(\d+)', t)}


def rfc_entry(xml: str, n: int) -> str:
    m = re.search(r"<rfc-entry>\s*<doc-id>RFC0*%d</doc-id>.*?</rfc-entry>" % n, xml, re.S)
    return " ".join(m.group(0).split()) if m else ""


# ------------------------------------------------------------------ schema
def check_schema(c: Checker, rfcs, errata, iana, glossary):
    keys = {"rfc", "title", "status", "date", "obsoletes", "obsoleted_by", "updates", "updated_by", "also", "in_corpus"}
    bad = [r.get("rfc") for r in rfcs if not keys <= r.keys() or not isinstance(r["rfc"], int)
           or r["status"] not in RFC_STATUS or not isinstance(r["in_corpus"], bool)
           or not (r["date"] is None or re.fullmatch(r"\d{4}(-\d{2})?", r["date"]))
           or not all(isinstance(x, int) for k in ("obsoletes", "obsoleted_by", "updates", "updated_by") for x in r[k])
           or not all(re.fullmatch(r"(BCP|STD|FYI) \d+", a) for a in r["also"])]
    c.check("rfc_index schema", not bad, f"{len(bad)} bad records, e.g. {bad[:5]}")
    c.check("rfc_index unique rfc", len({r['rfc'] for r in rfcs}) == len(rfcs))
    by = {r["rfc"]: r for r in rfcs}
    asym = [(r["rfc"], n) for r in rfcs for n in r["obsoleted_by"] if n in by and r["rfc"] not in by[n]["obsoletes"]]
    c.check("rfc_index obsoletes/obsoleted_by symmetric", not asym, str(asym[:5]))

    ekeys = {"id", "rfc", "status", "type", "section", "summary", "url"}
    bad = [e.get("id") for e in errata if not ekeys <= e.keys() or not isinstance(e["id"], int) or not isinstance(e["rfc"], int)
           or e["status"] not in ERRATA_STATUS or e["type"] not in ERRATA_TYPE
           or not (e["section"] is None or isinstance(e["section"], str) and e["section"])
           or not isinstance(e["summary"], str) or not e["summary"] or len(e["summary"]) > 300
           or e["url"] != f"https://www.rfc-editor.org/errata/eid{e['id']}"]
    c.check("errata schema", not bad, f"{len(bad)} bad, e.g. {bad[:5]}")
    c.check("errata unique id", len({e['id'] for e in errata}) == len(errata))
    c.check("errata rfcs are in rfc_index", all(e["rfc"] in by for e in errata))

    ikeys = {"registry", "code", "name", "description", "refs", "aliases"}
    bad = [(r.get("registry"), r.get("code")) for r in iana if not ikeys <= r.keys()
           or not all(isinstance(r[k], str) for k in ("registry", "code", "name", "description"))
           or not r["registry"] or not r["code"] or not r["name"]
           or not isinstance(r["refs"], list) or not isinstance(r["aliases"], list)
           or not all(isinstance(a, str) and a for a in r["aliases"])
           or not all(re.fullmatch('RFC \\d+( \xa7[\\w.-]+)?|Errata ID \\d+|doc:[a-z0-9-]+|(draft|RFC)-[\\w.-]+', x) for x in r["refs"])]
    c.check("iana schema", not bad, f"{len(bad)} bad, e.g. {bad[:5]}")
    dup = len(iana) - len({(r["registry"], r["code"], r["name"]) for r in iana})
    c.check("iana unique (registry, code, name)", dup == 0, f"{dup} duplicates")
    unknown_refs = sorted({int(m.group(1)) for r in iana for x in r["refs"] if (m := re.match(r"RFC (\d+)", x))} - by.keys())
    c.check("iana RFC refs exist in rfc_index", not unknown_refs, str(unknown_refs[:10]))

    gkeys = {"term", "expansion", "aliases", "refs"}
    bad = [g.get("term") for g in glossary if not gkeys <= g.keys() or not g["term"] or not g["expansion"]
           or not g["refs"] or not all(isinstance(a, str) and a for a in g["aliases"])]
    c.check("glossary schema", not bad, str(bad[:5]))
    c.check("glossary unique terms", len({g['term'].casefold() for g in glossary}) == len(glossary))
    unknown_refs = sorted({int(m.group(1)) for g in glossary for x in g["refs"] if (m := re.match(r"RFC (\d+)", x))} - by.keys())
    c.check("glossary RFC refs exist in rfc_index", not unknown_refs, str(unknown_refs))


def check_meta(c: Checker, kb: Path, names):
    for name in names:
        meta_path = kb / f"{name}.meta.json"
        if not c.check(f"{name}.meta.json exists", meta_path.exists()):
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        c.check(f"{name}.meta.json keys", META_KEYS <= meta.keys(), str(META_KEYS - meta.keys()))
        digest = hashlib.sha256((kb / f"{name}.json").read_bytes()).hexdigest()
        c.check(f"{name}.meta.json sha256 matches", meta.get("sha256") == digest)
        for src in meta.get("sources", []):
            base = Path(src.get("url", "")).name  # cache name: URL basename, or errata-rfcN.html for errata/rfcN
            raw = next((p for p in (RAW_DIR / base, RAW_DIR / f"errata-{base}.html") if p.exists()), None)
            if src.get("url", "").startswith("http") and raw and src.get("sha256"):
                c.check(f"{name} source sha256 {raw.name}", hashlib.sha256(raw.read_bytes()).hexdigest() == src["sha256"])


# ------------------------------------------------------------------ counts
def check_counts(c: Checker, rfcs, errata, iana, glossary, corpus_dirs):
    xml = raw_text("rfc-index.xml")
    n_entries = len(re.findall(r"<rfc-entry>", xml))
    c.check("rfc_index count == <rfc-entry> count in raw rfc-index.xml", len(rfcs) == n_entries, f"{len(rfcs)} vs {n_entries}")
    files = {int(m.group(1)) for d in corpus_dirs for p in d.glob("rfc*.txt") if (m := re.fullmatch(r"rfc(\d+)\.txt", p.name))}
    marked = {r["rfc"] for r in rfcs if r["in_corpus"]}
    c.check("in_corpus == corpus rfc*.txt files", marked == files, f"missing {sorted(files - marked)[:5]} extra {sorted(marked - files)[:5]}")

    feed = json.loads((RAW_DIR / "errata.json").read_bytes())
    by = {r["rfc"]: r for r in rfcs}
    scope = set(marked)
    for n in marked:
        scope |= set(by[n]["updated_by"]) | set(by[n]["obsoleted_by"])
    expected = {int(e["errata_id"]) for e in feed if re.fullmatch(r"RFC0*(\d+)", e["doc-id"])
                and int(re.fullmatch(r"RFC0*(\d+)", e["doc-id"]).group(1)) in scope}
    got = {e["id"] for e in errata}
    c.check("errata ids == raw feed filtered to scope", got == expected, f"missing {len(expected - got)} extra {len(got - expected)}")
    c.check("errata covers >= 200 RFCs", len({e['rfc'] for e in errata}) >= 200)

    c.check("iana has >= 60 registries", len({r["registry"] for r in iana}) >= 60)
    c.check("iana has >= 1000 code points", len(iana) >= 1000, str(len(iana)))
    required = ["OSPFv2 Link State (LS) Types", "OSPFv2 Options", "OSPFv3 LSA Function Codes",
                "OSPF Router Informational Capability Bits", "BGP Path Attributes", "BGP Capability Codes",
                "BGP Error (NOTIFICATION) Codes", "BGP Cease NOTIFICATION Message Subcodes", "BGP Well-known Communities",
                "Address Family Numbers (AFI)", "Subsequent Address Family Identifiers (SAFI)", "IS-IS Top-Level TLV Codepoints",
                "PIM Message Types", "IGMP Type Numbers", "DSCP Pool 1 Codepoints", "Assigned Internet Protocol Numbers",
                "BFD Diagnostic Codes", "Base Special-Purpose MPLS Label Values", "LDP TLV Types",
                "Service Name and Transport Protocol Port Numbers",
                "BOOTP Vendor Extensions and DHCP Options", "DHCPv6 Option Codes"]          # corpus-v8 (FIXES.md 1.6)
    have = {r["registry"] for r in iana}
    c.check("iana has every BUILD-P1 registry", all(x in have for x in required), str([x for x in required if x not in have]))
    configured = [cfg["label"] for cfg in IANA_REGISTRIES]
    c.check("iana: every configured registry has records", all(x in have for x in configured),
            str([x for x in configured if x not in have]))
    # corpus-v8: in a qualify registry every name carries its code ("Router (DHCP option 3)"), and no alias is the bare
    # IANA name unless that name is identifier-like (IDENT), in the registry's bare_ok, or one of the curated aliases
    # FIXES.md 1.4 (c) chose for that code ("shared address space", "link selection sub-option")
    bad = []
    for cfg in (x for x in IANA_REGISTRIES if x.get("qualify")):
        for r in (x for x in iana if x["registry"] == cfg["label"]):
            suffix = cfg["qualify"].format(n="", c=r["code"])
            bare = r["name"][:-len(suffix)] if r["name"].endswith(suffix) else None
            curated = {a.casefold() for a in IANA_ALIASES.get(cfg["rid"], {}).get(r["code"], [])}
            if not r["name"].endswith(")") or bare is None:
                bad.append((cfg["rid"], r["code"], "name not qualified"))
            elif bare.casefold() in {a.casefold() for a in r["aliases"]} and not (
                    IANA_IDENT.fullmatch(bare) or bare in cfg.get("bare_ok", ()) or bare.casefold() in curated):
                bad.append((cfg["rid"], r["code"], f"bare alias {bare!r}"))
    c.check("iana: qualify registries keep plain IANA names out of the keys", not bad, str(bad[:5]))
    c.check("glossary has >= 300 terms", len(glossary) >= 300, str(len(glossary)))


# ------------------------------------------------------------------ spot checks
def spot_checks(c: Checker, rfcs, errata, iana, glossary, corpus_dirs, cisco_dirs):
    xml = raw_text("rfc-index.xml")
    feed = json.loads((RAW_DIR / "errata.json").read_bytes())
    by = {r["rfc"]: r for r in rfcs}
    corpus_file = {p.name: p for d in corpus_dirs for p in d.glob("rfc*.txt")}

    def iana_get(registry, code):
        return next((r for r in iana if r["registry"] == registry and r["code"] == code), None)

    def spot(name, json_ok, raw_ok):
        c.check(f"spot: {name} [json]", bool(json_ok))
        c.check(f"spot: {name} [source]", bool(raw_ok))

    e = rfc_entry(xml, 1587)
    spot("RFC 1587 obsoleted by RFC 3101", by[1587]["obsoleted_by"] == [3101] and not by[1587]["in_corpus"],
         re.search(r"<obsoleted-by> <doc-id>RFC0*3101</doc-id> </obsoleted-by>", e))
    e = rfc_entry(xml, 3101)
    spot("RFC 3101 Proposed Standard, obsoletes 1587, in corpus",
         by[3101]["status"] == "Proposed Standard" and by[3101]["obsoletes"] == [1587] and by[3101]["in_corpus"],
         "<current-status>PROPOSED STANDARD</current-status>" in e and "rfc3101.txt" in corpus_file)
    e = rfc_entry(xml, 4271)
    spot("RFC 4271 Draft Standard, obsoletes 1771, updated by 6793 and 7606",
         by[4271]["status"] == "Draft Standard" and by[4271]["obsoletes"] == [1771] and {6793, 7606} <= set(by[4271]["updated_by"]),
         "<current-status>DRAFT STANDARD</current-status>" in e and re.search(r"<doc-id>RFC0*6793</doc-id>", e) and re.search(r"<doc-id>RFC0*7606</doc-id>", e))
    for n in CROSSCHECK_RFCS:
        page = RAW_DIR / f"errata-rfc{n}.html"
        cards, links = errata_cards(page.read_text(encoding="utf-8", errors="replace")) if page.exists() else ({}, set())
        mine = {x["id"]: (x["status"], x["type"]) for x in errata if x["rfc"] == n}
        raw = {int(x["errata_id"]): (x["errata_status_code"], x["errata_type_code"]) for x in feed if feed_rfc(x) == n}
        spot(f"RFC {n} errata (ids, status, type) == rfc-editor.org/errata/rfc{n} ({len(cards)})",
             cards and mine == cards, raw == cards and set(cards) == links)
    e1633 = next((x for x in errata if x["id"] == 1633), {})
    r1633 = next((x for x in feed if x["errata_id"] == "1633"), {})
    spot("erratum 1633 (RFC 4271 \xa76.3) Verified Technical",
         (e1633.get("rfc"), e1633.get("status"), e1633.get("type"), e1633.get("section")) == (4271, "Verified", "Technical", "6.3"),
         (r1633.get("doc-id"), r1633.get("errata_status_code"), r1633.get("section")) == ("RFC4271", "Verified", "6.3"))
    bcp14 = re.search(r"<bcp-entry>\s*<doc-id>BCP14</doc-id>.*?</bcp-entry>", xml, re.S)
    spot("RFC 2119 is BCP 14, updated by RFC 8174", by[2119]["also"] == ["BCP 14"] and by[2119]["updated_by"] == [8174]
         and by[8174]["also"] == ["BCP 14"], bcp14 and "RFC2119" in bcp14.group(0) and "RFC8174" in bcp14.group(0))
    e = rfc_entry(xml, 791)
    spot("RFC 791 dated 1981-09, Internet Standard, STD 5", by[791]["date"] == "1981-09" and by[791]["status"] == "Internet Standard"
         and "STD 5" in by[791]["also"], "<month>September</month> <year>1981</year>" in e and re.search(r"<doc-id>STD0*5</doc-id>", e))
    obsolete_corpus = sorted(r["rfc"] for r in rfcs if r["in_corpus"] and r["obsoleted_by"])
    raw_obsolete = sorted(n for n in (r["rfc"] for r in rfcs if r["in_corpus"]) if "<obsoleted-by>" in rfc_entry(xml, n))
    spot(f"obsoleted corpus RFCs ({len(obsolete_corpus)}) == <obsoleted-by> in raw rfc-index.xml",
         obsolete_corpus and all(n in by[s]["obsoletes"] for n in obsolete_corpus for s in by[n]["obsoleted_by"] if s in by),
         obsolete_corpus == raw_obsolete)

    ospf = raw_text("ospfv2-parameters.xml")
    r = iana_get("OSPFv2 Link State (LS) Types", "7")
    spot("OSPFv2 LS type 7 = NSSA AS-external LSA [RFC 3101], aliases type-7 / Type 7 LSA / NSSA-LSA",
         r and r["name"] == "NSSA AS-external LSA" and "RFC 3101" in r["refs"]
         and {"type-7", "Type 7 LSA", "NSSA-LSA"} <= set(r["aliases"]),
         xml_record(ospf, "ospfv2-parameters-5", r"<value>7</value> <description>NSSA AS-external LSA</description> <xref type=\"rfc\" data=\"rfc3101\"/>"))
    r = iana_get("OSPFv2 Link State (LS) Types", "5")
    spot("OSPFv2 LS type 5 = AS-external-LSA", r and r["name"] == "AS-external-LSA",
         xml_record(ospf, "ospfv2-parameters-5", r"<value>5</value> <description>AS-external-LSA</description>"))
    ospf3 = raw_text("ospfv3-parameters.xml")
    r = iana_get("OSPFv3 LSA Function Codes", "7")
    rfc5340 = corpus_file["rfc5340.txt"].read_text(encoding="utf-8", errors="replace") if "rfc5340.txt" in corpus_file else ""
    spot("OSPFv3 function code 7 = NSSA-LSA, LS type 0x2007", r and r["name"] == "NSSA-LSA" and "0x2007" in r["aliases"],
         xml_record(ospf3, "ospfv3-parameters-3", r"<value>7</value> <description>NSSA-LSA</description>")
         and "The LS type of an NSSA-LSA is set to the value 0x2007" in rfc5340)
    bgp = raw_text("bgp-parameters.xml")
    r = iana_get("BGP Path Attributes", "9")
    spot("BGP attribute 9 = ORIGINATOR_ID [RFC 4456]", r and r["name"] == "ORIGINATOR_ID" and r["refs"] == ["RFC 4456"]
         and "BGP attribute 9" in r["aliases"],
         xml_record(bgp, "bgp-parameters-2", r"<value>9</value> <code>ORIGINATOR_ID</code> <xref type=\"rfc\" data=\"rfc4456\"/>"))
    r5, r14 = iana_get("BGP Path Attributes", "5"), iana_get("BGP Path Attributes", "14")
    spot("BGP attributes 5 = LOCAL_PREF, 14 = MP_REACH_NLRI", r5 and r5["name"] == "LOCAL_PREF" and "local preference" in r5["aliases"]
         and r14 and r14["name"] == "MP_REACH_NLRI",
         xml_record(bgp, "bgp-parameters-2", r"<value>5</value> <code>LOCAL_PREF</code>")
         and xml_record(bgp, "bgp-parameters-2", r"<value>14</value> <code>MP_REACH_NLRI</code>"))
    r = iana_get("Subsequent Address Family Identifiers (SAFI)", "128")
    spot("SAFI 128 = MPLS-labeled VPN address (VPNv4)", r and r["name"] == "MPLS-labeled VPN address" and "VPNv4" in r["aliases"],
         xml_record(raw_text("safi-namespace.xml"), "safi-namespace-2", r"<value>128</value> <description>MPLS-labeled VPN address</description>"))
    r1, r2 = iana_get("Address Family Numbers (AFI)", "1"), iana_get("Address Family Numbers (AFI)", "2")
    afi = raw_text("address-family-numbers.xml")
    spot("AFI 1 = IPv4, AFI 2 = IPv6", r1 and "IPv4" in r1["aliases"] and r2 and "IPv6" in r2["aliases"],
         xml_record(afi, "address-family-numbers-2", r"<value>1</value> <description>IP \(IP version 4\)</description>")
         and xml_record(afi, "address-family-numbers-2", r"<value>2</value> <description>IP6 \(IP version 6\)</description>"))
    dscp = raw_text("dscp-registry.xml")
    r = iana_get("DSCP Pool 1 Codepoints", "46")
    spot("DSCP 46 = EF (101110, ToS 0xB8) [RFC 3246]", r and r["name"] == "EF" and {"DSCP 46", "101110", "ToS 0xB8"} <= set(r["aliases"])
         and "RFC 3246" in r["refs"],
         xml_record(dscp, "dscp-registry-2", r"<name>EF</name> <space>101110</space> <decimal_space>46</decimal_space> <xref type=\"rfc\" data=\"rfc3246\"/>"))
    r34, r48 = iana_get("DSCP Pool 1 Codepoints", "34"), iana_get("DSCP Pool 1 Codepoints", "48")
    spot("DSCP 34 = AF41, DSCP 48 = CS6", r34 and r34["name"] == "AF41" and r48 and r48["name"] == "CS6" and "IP precedence 6" in r48["aliases"],
         xml_record(dscp, "dscp-registry-2", r"<name>AF41</name> <space>100010</space> <decimal_space>34</decimal_space>")
         and xml_record(dscp, "dscp-registry-2", r"<name>CS6</name> <space>110000</space> <decimal_space>48</decimal_space>"))
    cs1, le = iana_get("DSCP Pool 1 Codepoints", "8"), iana_get("DSCP Pool 3 Codepoints", "1")
    e = rfc_entry(xml, 8622)
    spot("CS1/LE: RFC 8622 moved RFC 4594 Low-Priority Data from CS1 to LE (DSCP 1)",
         cs1 and cs1["name"] == "CS1" and "RFC 8622" in cs1["refs"] and le and le["name"] == "LE"
         and "Low-Priority Data" in le["description"] and "RFC 8622" in le["refs"],
         re.search(r"<updates> <doc-id>RFC4594</doc-id>", e)
         and xml_record(dscp, "dscp-registry-3", r"<name>LE</name> <space>000001</space> <decimal_space>1</decimal_space> <xref type=\"rfc\" data=\"rfc8622\"/>"))
    r = iana_get("BGP MCAST-VPN Route Types", "0x07")
    spot("MVPN route type 7 (0x07) = Source Tree Join route", r and r["name"] == "Source Tree Join route"
         and {"MVPN route type 7", "MVPN route type 0x07"} <= set(r["aliases"]),
         xml_record(bgp, "mcast-vpn-route-types", r"<value>0x07</value> <name>Source Tree Join route</name>"))
    r = iana_get("BFD Diagnostic Codes", "3")
    spot("BFD diagnostic 3 = Neighbor Signaled Session Down", r and r["name"] == "Neighbor Signaled Session Down" and "BFD diag 3" in r["aliases"],
         xml_record(raw_text("bfd-parameters.xml"), "bfd-parameters-1", r"<code>3</code> <profile>Neighbor Signaled Session Down</profile>"))
    r = iana_get("BGP Capability Codes", "65")
    spot("BGP capability 65 = 4-octet AS number support", r and "4-octet AS" in r["aliases"] and "RFC 6793" in r["refs"],
         xml_record(raw_text("capability-codes.xml"), "capability-codes-2", r"<value>65</value> <description>Support for 4-octet AS number capability</description>"))
    r = iana_get("BGP Cease NOTIFICATION Message Subcodes", "2")
    spot("BGP NOTIFICATION 6/2 = Cease / Administrative Shutdown", r and r["name"] == "Administrative Shutdown" and "6/2" in r["aliases"],
         xml_record(bgp, "bgp-parameters-8", r"<value>2</value> <name>Administrative Shutdown</name>")
         and xml_record(bgp, "bgp-parameters-3", r"<value>6</value> <name>Cease</name>"))
    r = iana_get("BGP Well-known Communities", "0xFFFFFF01")
    spot("NO_EXPORT = 0xFFFFFF01 = 65535:65281", r and r["name"] == "NO_EXPORT" and "65535:65281" in r["aliases"],
         xml_record(raw_text("bgp-well-known-communities.xml"), "bgp-well-known-communities-1", r"<value>0xFFFFFF01</value> <attribute>NO_EXPORT</attribute>"))
    r = iana_get("Base Special-Purpose MPLS Label Values", "3")
    spot("MPLS label 3 = Implicit NULL", r and r["name"] == "Implicit NULL Label" and "implicit null" in r["aliases"],
         xml_record(raw_text("mpls-label-values.xml"), "special-purpose", r"<value>3</value> <description>Implicit NULL Label</description>"))
    isis = raw_text("isis-tlv-codepoints.xml")
    r = iana_get("IS-IS Top-Level TLV Codepoints", "135")
    spot("IS-IS TLV 135 = Extended IP reachability", r and r["name"] == "Extended IP reachability" and "TLV 135" in r["aliases"],
         xml_record(isis, "tlv-codepoints", r"<value>135</value> <name>Extended IP reachability</name>"))
    r = iana_get("IS-IS Authentication Type Codes", "54")
    spot("IS-IS authentication type 54 = HMAC-MD5 [RFC 5304]", r and r["name"] == "HMAC-MD5 Authentication" and r["refs"] == ["RFC 5304"]
         and {"IS-IS authentication type 54", "HMAC-MD5"} <= set(r["aliases"]),
         xml_record(isis, "isis-tlv-codepoints-10",
                    r"<value>54</value> <authentication_type_code>HMAC-MD5 Authentication</authentication_type_code> <xref type=\"rfc\" data=\"rfc5304\"/>"))
    rp = raw_text("protocol-numbers.xml")
    r88, r89 = iana_get("Assigned Internet Protocol Numbers", "88"), iana_get("Assigned Internet Protocol Numbers", "89")
    spot("IP protocol 88 = EIGRP, 89 = OSPFIGP", r88 and r88["name"] == "EIGRP" and r89 and r89["name"] == "OSPFIGP" and "OSPF" in r89["aliases"],
         xml_record(rp, "protocol-numbers-1", r"<value>88</value> <name>EIGRP</name>") and xml_record(rp, "protocol-numbers-1", r"<value>89</value> <name>OSPFIGP</name>"))
    rows = list(csv.DictReader(io.StringIO(raw_text("service-names-port-numbers.csv"))))
    want = {("179", "bgp"), ("4789", "vxlan"), ("3784", "bfd-control"), ("646", "ldp")}
    got = {(r["code"], r["name"]) for r in iana if r["registry"] == "Service Name and Transport Protocol Port Numbers"}
    spot("ports 179 bgp, 646 ldp, 3784 bfd-control, 4789 vxlan", want <= got,
         want <= {(row["Port Number"], row["Service Name"]) for row in rows})
    mc = raw_text("multicast-addresses.xml")
    r = iana_get("IPv4 Multicast Addresses: Local Network Control Block (224.0.0.0/24)", "224.0.0.5")
    r6 = iana_get("IPv6 Link-Local Scope Multicast Addresses (FF02::/16)", "FF02:0:0:0:0:0:0:5")
    spot("224.0.0.5 / ff02::5 = OSPF all routers", r and "AllSPFRouters" in r["aliases"] and r6 and "ff02::5" in r6["aliases"],
         xml_record(mc, "multicast-addresses-1", r"<addr>224.0.0.5</addr> <description>OSPFIGP OSPFIGP All Routers</description>"))
    r = next((x for x in iana if x["code"] == "12346" and x["registry"].startswith("Cisco SD-WAN")), None)
    doc = next((d / "sdwan-design-cvd.txt" for d in cisco_dirs if (d / "sdwan-design-cvd.txt").exists()), None)
    spot("SD-WAN UDP 12346 is labelled vendor, sourced from sdwan-design-cvd", r and r["refs"] == ["doc:sdwan-design-cvd"]
         and not any(x["code"] == "12346" and x["registry"].startswith("Service Name") for x in iana),
         doc and "The default base source port is 12346" in doc.read_text(encoding="utf-8", errors="replace")
         and not any(row["Port Number"] == "12346" and row["Service Name"] for row in rows))
    rad = raw_text("radius-types.xml")
    r26, r43 = iana_get("RADIUS Attribute Types", "26"), iana_get("RADIUS Packet Type Codes", "43")
    spot("RADIUS attribute 26 = Vendor-Specific, code 43 = CoA-Request [RFC 5176]",
         r26 and r26["name"] == "Vendor-Specific" and "RADIUS attribute 26" in r26["aliases"]
         and r43 and r43["name"] == "CoA-Request" and "RADIUS code 43" in r43["aliases"] and "RFC 5176" in r43["refs"],
         xml_record(rad, "radius-types-2", r"<value>26</value> <description>Vendor-Specific</description>")
         and xml_record(rad, "radius-types-27", r"<decimal>43</decimal> <message>CoA-Request</message>"))
    r = iana_get("EAP Method Types", "55")
    spot("EAP type 55 = TEAP [RFC 9930]", r and r["name"] == "TEAP" and "EAP type 55" in r["aliases"] and "RFC 9930" in r["refs"],
         xml_record(raw_text("eap-numbers.xml"), "eap-numbers-4", r"<value>55</value> <description>TEAP</description>"))
    # corpus-v8 (cisco-add7/FIXES.md 1.6 item 3): DHCP/BOOTP, DHCPv6 and the special-purpose address registries
    dhcp, dhcp6 = raw_text("bootp-dhcp-parameters.xml"), raw_text("dhcpv6-parameters.xml")
    r = iana_get("BOOTP Vendor Extensions and DHCP Options", "82")
    spot("DHCP option 82 = Relay Agent Information [RFC 3046]",
         r and r["name"].startswith("Relay Agent Information") and "DHCP option 82" in r["aliases"] and "RFC 3046" in r["refs"],
         xml_record(dhcp, "options", r"<value>82</value> <name>Relay Agent Information</name>.*rfc3046"))
    r = iana_get("DHCPv6 Option Codes", "23")
    spot("DHCPv6 option 23 = OPTION_DNS_SERVERS [RFC 3646]",
         r and "OPTION_DNS_SERVERS" in r["aliases"] and "DHCPv6 option 23" in r["aliases"] and "RFC 3646" in r["refs"],
         xml_record(dhcp6, "dhcpv6-parameters-2", r"<value>23</value> <description>OPTION_DNS_SERVERS</description>.*rfc3646"))
    r = iana_get("DHCP Message Type 53 Values", "1")
    spot("DHCP message type 1 = DHCPDISCOVER", r and "DHCPDISCOVER" in r["aliases"] and "DHCP message type 1" in r["aliases"],
         xml_record(dhcp, "message-type-53", r"<value>1</value> <description>DHCPDISCOVER</description>"))
    r = iana_get("IANA IPv4 Special-Purpose Address Registry", "169.254.0.0/16")
    spot("169.254.0.0/16 = Link Local [RFC 3927], the code an alias",
         r and r["name"].startswith("Link Local") and "RFC 3927" in r["refs"] and "169.254.0.0/16" in r["aliases"],
         xml_record(raw_text("iana-ipv4-special-registry.xml"), "iana-ipv4-special-registry-1",
                    r"<address>169.254.0.0/16</address> <name>Link Local</name> <spec><xref type=\"rfc\" data=\"rfc3927\"/>"))
    g = {x["term"]: x for x in glossary}
    spot("glossary NSSA -> RFC 3101, route reflector -> RFC 4456",
         g.get("NSSA", {}).get("refs") == ["RFC 3101"] and "RFC 4456" in g.get("Route reflector", {}).get("refs", []),
         by[3101]["title"].startswith("The OSPF Not-So-Stubby Area") and by[4456]["title"].startswith("BGP Route Reflection"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kb-dir", default=str(KB_DIR))
    ap.add_argument("--corpus-dir", action="append",
                    help="dir with rfcNNNN.txt (repeatable; default: every existing ../pilot/rfc, ../pilot/rfc-add)")
    ap.add_argument("--cisco-dir", action="append",
                    help="dir with <doc_key>.txt (repeatable; default: every existing ../pilot/cisco-md-v2, ../pilot/cisco-md-v3)")
    args = ap.parse_args(argv)
    kb = Path(args.kb_dir)
    names = ["rfc_index", "errata", "iana", "glossary"]
    rfcs, errata, iana, glossary = (load(kb, n) for n in names)
    c = Checker()
    check_schema(c, rfcs, errata, iana, glossary)
    check_meta(c, kb, names)
    corpus_dirs = [Path(d) for d in (args.corpus_dir or default_dirs(RFC_CORPUS_DIRS))]
    cisco_dirs = [Path(d) for d in (args.cisco_dir or default_dirs(CISCO_CORPUS_DIRS))]
    check_counts(c, rfcs, errata, iana, glossary, corpus_dirs)
    n_before = c.passed + len(c.failed)
    spot_checks(c, rfcs, errata, iana, glossary, corpus_dirs, cisco_dirs)
    n_spot = (c.passed + len(c.failed) - n_before) // 2
    print(f"counts: rfc_index={len(rfcs)} errata={len(errata)} iana={len(iana)} "
          f"({len({r['registry'] for r in iana})} registries) glossary={len(glossary)}")
    print(f"checks: {c.passed} passed, {len(c.failed)} failed ({n_spot} spot checks, each verified in JSON and in the raw source)")
    for f in c.failed:
        print(f"FAIL {f}")
    sys.exit(1 if c.failed else 0)


if __name__ == "__main__":
    main()
