#!/usr/bin/env python3
"""Public knowledge-base source helper."""
import argparse
import csv
import io
import ipaddress
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict

from pathlib import Path

from kbcommon import KB_DIR, RAW_DIR, fetch, write_output

NS = "{http://www.iana.org/assignments}"
BASE = "https://www.iana.org/assignments/{0}/{0}"
PORTS_FILE = "service-names-port-numbers"
GENERATOR = "staging/kb/build_iana.py"


def reg(file, rid, label, templates=(), **opts):
    return {"file": file, "rid": rid, "label": label, "templates": list(templates), **opts}


LSA_T = ["type-{c}", "Type {c} LSA", "type-{c} LSA", "LSA type {c}", "LS type {c}", "OSPF LSA type {c}"]
BGP_SUB_T = ["{p}/{c}", "BGP notification {p}/{c}", "NOTIFICATION {p}/{c}", "BGP error {p}/{c}", "{pn} subcode {c}"]
EXTC_SUB_T = ["extended community {pc}", "ext community type {p} subtype {c}"]
ICMP_CODE_T = ["ICMP type {p} code {c}", "type {p} code {c}", "ICMP {p}/{c}"]
# DHCP / BOOTP and DHCPv6 (corpus-v8; FIXES.md section 1.4). {n} is the IANA name of the code point.
DHCP_OPT_T = ["DHCP option {c}", "DHCPv4 option {c}", "BOOTP option {c}", "option {c}", "DHCP option code {c}",
              "{n} option"]            # "{n} option" only when the IANA name does not already end in "option" or start OPTION_
DHCP_MSG_T = ["DHCP message type {c}", "option 53 type {c}"]
RELAY_SUB_T = ["option 82 sub-option {c}", "option 82 suboption {c}", "DHCP option 82 sub-option {c}",
               "relay agent sub-option {c}"]
DHCP6_OPT_T = ["DHCPv6 option {c}", "DHCPv6 option code {c}"]
DHCP6_MSG_T = ["DHCPv6 message type {c}", "DHCPv6 {n}"]
DHCP6_STATUS_T = ["DHCPv6 status code {c}", "DHCPv6 status {c}"]
DUID_T = ["DUID type {c}", "DHCPv6 DUID type {c}"]
# An IANA name that is safe as a bare lookup key in a qualify registry (fullmatch):
#  OPTION_DNS_SERVERS, RELAY-FORW, DUID-LLT, DHCPV4-QUERY, DHCPDISCOVER, NoAddrsAvail, UnspecFail -> bare alias kept
#  Router, Subnet Mask, REQUEST, REPLY, STATE, Success, Loopback, Documentation           -> qualified name only
IDENT = re.compile(r"[A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)+|DHCP[A-Z]+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+")
DHCP6_MSG_BARE = {"SOLICIT", "ADVERTISE", "LEASEQUERY", "ACTIVELEASEQUERY", "BNDUPD", "BNDREPLY", "POOLREQ",
                  "POOLRESP", "UPDREQ", "UPDREQALL", "UPDDONE", "CONNECTREPLY"}
UNASSIGNED_HIST = re.compile(r"(?i)(removed/)?unassigned\b")   # names keep_unassigned_history looks at
PLACEHOLDER_NAME = re.compile(r"(?i)((removed/)?unassigned|reserved)\b")   # no "{n} ..." alias for these
# description prefix for code points nested under a parent code, keyed by the parent registry id
PARENT_DESC = {"bgp-parameters-3": "Subcode of BGP NOTIFICATION error code {p} ({pn}).",
               "transitive": "Sub-type of BGP extended community type {p} ({pn}).",
               "non-transitive": "Sub-type of BGP extended community type {p} ({pn}).",
               "icmp-parameters-types": "Code of ICMP type {p} ({pn})."}

REGISTRIES = [
    # OSPF
    reg("ospfv2-parameters", "ospfv2-parameters-5", "OSPFv2 Link State (LS) Types", LSA_T),
    reg("ospfv2-parameters", "ospfv2-parameters-1", "OSPFv2 Options", ["OSPF option {c}", "OSPF options bit {c}"]),
    reg("ospfv2-parameters", "ospfv2-parameters-3", "OSPF Packet Types", ["OSPF packet type {c}", "OSPF type {c} packet"]),
    reg("ospfv2-parameters", "ospfv2-parameters-7", "OSPFv2 Router-LSA Link Types", ["router-LSA link type {c}", "OSPF link type {c}"]),
    reg("ospf-opaque-types", "ospf-opaque-types-2", "OSPF Opaque LSA Option Types", ["opaque type {c}", "opaque LSA type {c}"]),
    reg("ospf-authentication-codes", "authentication-codes", "OSPF Authentication Codes",
        ["OSPF authentication type {c}", "OSPF auth type {c}", "AuType {c}"]),
    reg("ospfv3-parameters", "ospfv3-parameters-3", "OSPFv3 LSA Function Codes",
        ["OSPFv3 LSA function code {c}", "OSPFv3 function code {c}", "OSPFv3 LSA type {c}"]),
    reg("ospfv3-parameters", "ospfv3-parameters-1", "OSPFv3 Options", ["OSPFv3 option {c}"]),
    reg("ospfv3-parameters", "ospfv3-parameters-4", "OSPFv3 Prefix Options", ["OSPFv3 prefix option {c}"]),
    reg("ospfv3-parameters", "ospfv3-parameters-6", "OSPFv3 Router-LSA Link Types", ["OSPFv3 link type {c}"]),
    reg("ospf-parameters", "ri-tlv", "OSPF Router Information (RI) TLVs", ["RI TLV {c}", "OSPF RI TLV {c}"]),
    reg("ospf-parameters", "router-informational-capability", "OSPF Router Informational Capability Bits",
        ["RI informational capability bit {c}", "informational capability bit {c}"]),
    reg("ospf-parameters", "router-functional-capability", "OSPF Router Functional Capability Bits",
        ["RI functional capability bit {c}", "functional capability bit {c}"]),
    # BGP
    reg("bgp-parameters", "bgp-parameters-1", "BGP Message Types", ["BGP message type {c}", "BGP type {c} message"]),
    reg("bgp-parameters", "bgp-parameters-2", "BGP Path Attributes",
        ["BGP attribute {c}", "BGP path attribute {c}", "path attribute {c}", "attribute type {c}", "attr {c}", "type code {c}"]),
    reg("bgp-parameters", "bgp-parameters-3", "BGP Error (NOTIFICATION) Codes",
        ["BGP error {c}", "BGP error code {c}", "NOTIFICATION code {c}", "BGP notification {c}"]),
    reg("bgp-parameters", "bgp-parameters-5", "BGP Message Header Error Subcodes", BGP_SUB_T, parent=("bgp-parameters-3", "1")),
    reg("bgp-parameters", "bgp-parameters-6", "BGP OPEN Message Error Subcodes", BGP_SUB_T, parent=("bgp-parameters-3", "2")),
    reg("bgp-parameters", "bgp-parameters-7", "BGP UPDATE Message Error Subcodes", BGP_SUB_T, parent=("bgp-parameters-3", "3")),
    reg("bgp-parameters", "bgp-finite-state-machine-error-subcodes", "BGP Finite State Machine Error Subcodes", BGP_SUB_T,
        parent=("bgp-parameters-3", "5")),
    reg("bgp-parameters", "bgp-parameters-8", "BGP Cease NOTIFICATION Message Subcodes", BGP_SUB_T, parent=("bgp-parameters-3", "6")),
    reg("bgp-parameters", "route-refresh-error-subcodes", "BGP ROUTE-REFRESH Message Error Subcodes", BGP_SUB_T,
        parent=("bgp-parameters-3", "7")),
    reg("bgp-parameters", "bgp-parameters-11", "BGP OPEN Optional Parameter Types", ["BGP optional parameter {c}"]),
    reg("bgp-parameters", "route-refresh-subcodes", "BGP Route Refresh Subcodes", ["route refresh subcode {c}"]),
    reg("bgp-parameters", "bgp-parameters-10", "BGP Outbound Route Filtering (ORF) Types", ["ORF type {c}"]),
    reg("bgp-parameters", "mcast-vpn-route-types", "BGP MCAST-VPN Route Types",
        ["MVPN route type {c}", "MCAST-VPN route type {c}", "MVPN route type {d}", "MCAST-VPN route type {d}"]),
    reg("bgp-parameters", "pmsi-tunnel-types", "BGP PMSI Tunnel Types", ["PMSI tunnel type {c}", "PMSI tunnel type {d}"]),
    reg("capability-codes", "capability-codes-2", "BGP Capability Codes",
        ["BGP capability {c}", "BGP capability code {c}", "capability code {c}"]),
    reg("capability-codes", "bgp-role-value", "BGP Role Values", ["BGP role {c}"]),
    reg("bgp-well-known-communities", "bgp-well-known-communities-1", "BGP Well-known Communities",
        ["{nn}", "community {nn}", "community {d}"]),
    reg("bgp-extended-communities", "transitive", "BGP Transitive Extended Community Types", ["extended community type {c}"]),
    reg("bgp-extended-communities", "non-transitive", "BGP Non-Transitive Extended Community Types", ["extended community type {c}"]),
    reg("bgp-extended-communities", "trans-two-octet-as", "BGP Extended Community Sub-Types: Transitive Two-Octet AS-Specific (type 0x00)",
        EXTC_SUB_T, parent=("transitive", "0x00")),
    reg("bgp-extended-communities", "trans-ipv4", "BGP Extended Community Sub-Types: Transitive IPv4-Address-Specific (type 0x01)",
        EXTC_SUB_T, parent=("transitive", "0x01")),
    reg("bgp-extended-communities", "trans-four-octet-as", "BGP Extended Community Sub-Types: Transitive Four-Octet AS-Specific (type 0x02)",
        EXTC_SUB_T, parent=("transitive", "0x02")),
    reg("bgp-extended-communities", "trans-opaque", "BGP Extended Community Sub-Types: Transitive Opaque (type 0x03)",
        EXTC_SUB_T, parent=("transitive", "0x03")),
    reg("bgp-extended-communities", "evpn", "BGP Extended Community Sub-Types: EVPN (type 0x06)", EXTC_SUB_T, parent=("transitive", "0x06")),
    reg("bgp-extended-communities", "non-trans-two-octet-as",
        "BGP Extended Community Sub-Types: Non-Transitive Two-Octet AS-Specific (type 0x40)", EXTC_SUB_T, parent=("non-transitive", "0x40")),
    reg("bgp-extended-communities", "bgp-extended-communities-10",
        "BGP Extended Community Sub-Types: Generic Transitive (type 0x80, incl. flowspec actions)", EXTC_SUB_T, parent=("transitive", "0x80")),
    reg("address-family-numbers", "address-family-numbers-2", "Address Family Numbers (AFI)", ["AFI {c}", "address family {c}"]),
    reg("safi-namespace", "safi-namespace-2", "Subsequent Address Family Identifiers (SAFI)", ["SAFI {c}"]),
    # IS-IS
    reg("isis-tlv-codepoints", "tlv-codepoints", "IS-IS Top-Level TLV Codepoints", ["IS-IS TLV {c}", "ISIS TLV {c}", "TLV {c}"],
        special="isis_top"),
    reg("isis-tlv-codepoints", "isis-tlv-codepoints-advertising-neighbor-information",
        "IS-IS Sub-TLVs for TLVs Advertising Neighbor Information (22, 23, 25, 141, 222, 223)", special="isis_sub"),
    reg("isis-tlv-codepoints", "isis-tlv-codepoints-advertising-prefix-reachability",
        "IS-IS Sub-TLVs for TLVs Advertising Prefix Reachability (27, 135, 235, 236, 237, ...)", special="isis_sub"),
    reg("isis-tlv-codepoints", "isis-tlv-codepoints-242", "IS-IS Sub-TLVs for Router CAPABILITY TLV (242)", ["TLV 242 sub-TLV {c}"]),
    reg("isis-tlv-codepoints", "isis-tlv-codepoints-10", "IS-IS Authentication Type Codes", ["IS-IS authentication type {c}"]),

    reg("pim-parameters", "message-types", "PIM Message Types", ["PIM type {c}", "PIM message type {c}", "PIMv2 type {c}"],
        special="pim_msg"),
    reg("pim-parameters", "pim-parameters-1", "PIM-Hello Options", ["PIM hello option {c}"]),
    reg("igmp-type-numbers", "igmp-type-numbers-1", "IGMP Type Numbers", ["IGMP type {c}", "IGMP type {d}"]),
    reg("multicast-addresses", "multicast-addresses-1", "IPv4 Multicast Addresses: Local Network Control Block (224.0.0.0/24)",
        special="mcast", block=("224.0.0.0/24", "Local Network Control Block", ["link-local multicast", "224.0.0/24"]),
        code_alias=True),
    reg("multicast-addresses", "multicast-addresses-2", "IPv4 Multicast Addresses: Internetwork Control Block (224.0.1.0/24)",
        special="mcast", block=("224.0.1.0/24", "Internetwork Control Block", ["224.0.1/24"]), code_alias=True),
    reg("multicast-addresses", "multicast-addresses-10", "IPv4 Multicast Addresses: Source-Specific Multicast Block (232.0.0.0/8)",
        special="mcast", keep_ranges=True, block=("232.0.0.0/8", "Source-Specific Multicast Block", ["SSM range", "SSM block", "232/8"]),
        code_alias=True),
    reg("multicast-addresses", "glop", "IPv4 Multicast Addresses: GLOP Block (233.0.0.0/8)", special="mcast", keep_ranges=True,
        block=("233.0.0.0/8", "GLOP Block", ["GLOP addressing", "233/8"]), code_alias=True),
    reg("multicast-addresses", "multicast-addresses-12", "IPv4 Multicast Addresses: Scoped Multicast Ranges", special="mcast",
        keep_ranges=True, code_alias=True),
    reg("ipv6-multicast-addresses", "link-local", "IPv6 Link-Local Scope Multicast Addresses (FF02::/16)", special="mcast",
        keep_ranges=True, block=("FF02::/16", "Link-Local Scope Multicast Addresses", ["ff02::/16", "IPv6 link-local multicast"]),
        code_alias=True),
    reg("ipv6-multicast-addresses", "ipv6-scope", "IPv6 Multicast Address Scopes", ["IPv6 multicast scope {c}", "FF0{c}::/16"]),
    # QoS / IP / ICMP
    reg("dscp-registry", "dscp-registry-2", "DSCP Pool 1 Codepoints", special="dscp"),
    reg("dscp-registry", "dscp-registry-3", "DSCP Pool 3 Codepoints", special="dscp"),
    reg("dscp-registry", "ecn-field", "ECN Field (IP header TOS bits 6-7)", ["ECN {c}"]),
    reg("protocol-numbers", "protocol-numbers-1", "Assigned Internet Protocol Numbers",
        ["IP protocol {c}", "protocol {c}", "protocol number {c}", "next header {c}"]),
    reg("icmp-parameters", "icmp-parameters-types", "ICMP Type Numbers", ["ICMP type {c}"]),
    reg("icmp-parameters", "icmp-parameters-codes-3", "ICMP Type 3 (Destination Unreachable) Codes", ICMP_CODE_T,
        parent=("icmp-parameters-types", "3")),
    reg("icmp-parameters", "icmp-parameters-codes-5", "ICMP Type 5 (Redirect) Codes", ICMP_CODE_T, parent=("icmp-parameters-types", "5")),
    reg("icmp-parameters", "icmp-parameters-codes-11", "ICMP Type 11 (Time Exceeded) Codes", ICMP_CODE_T,
        parent=("icmp-parameters-types", "11")),
    reg("icmpv6-parameters", "icmpv6-parameters-2", "ICMPv6 Type Numbers", ["ICMPv6 type {c}"]),
    # BFD / MPLS / LDP
    reg("bfd-parameters", "bfd-parameters-1", "BFD Diagnostic Codes",
        ["BFD diag {c}", "BFD diagnostic {c}", "BFD diagnostic code {c}", "diag {c}"]),
    reg("bfd-parameters", "bfd-parameters-2", "BFD Authentication Types", ["BFD auth type {c}", "BFD authentication type {c}"]),
    reg("mpls-label-values", "special-purpose", "Base Special-Purpose MPLS Label Values", ["label {c}", "MPLS label {c}"]),
    reg("mpls-label-values", "extended", "Extended Special-Purpose MPLS Label Values", ["extended special-purpose label {c}"]),
    reg("ldp-namespaces", "ldp-namespaces-2", "LDP Message Types", ["LDP message {c}", "LDP message type {c}", "LDP message type {d}"]),
    reg("ldp-namespaces", "ldp-namespaces-4", "LDP TLV Types", ["LDP TLV {c}", "LDP TLV type {c}", "LDP TLV type {d}"]),
    reg("ldp-namespaces", "fec-type", "LDP FEC Types", ["LDP FEC type {c}", "FEC element type {c}"]),
    # RADIUS and EAP (802.1X, MAB, CoA; corpus-v6). Only the code points: their value registries (Service-Type,
    # NAS-Port-Type, Tunnel-Type ...) name values with plain words ("Login", "Ethernet", "VLAN") that would answer a
    # lookup of the word itself. The packet type codes sit in "decimal" / "message" columns (cols).
    reg("radius-types", "radius-types-2", "RADIUS Attribute Types",
        ["RADIUS attribute {c}", "RADIUS attribute type {c}", "RADIUS attr {c}"], special="radius_attr"),
    reg("radius-types", "radius-types-27", "RADIUS Packet Type Codes",
        ["RADIUS code {c}", "RADIUS packet type {c}", "RADIUS packet code {c}"], cols=("decimal", "message")),
    reg("eap-numbers", "eap-numbers-4", "EAP Method Types", ["EAP type {c}", "EAP method type {c}", "EAP method {c}"]),
    # DHCP / BOOTP and DHCPv6 (corpus-v8). IANA's names here are plain words, so each record's name is qualified with
    # its code and the bare IANA name is an alias only when it is identifier-like (IDENT) or listed in bare_ok.
    reg("bootp-dhcp-parameters", "options", "BOOTP Vendor Extensions and DHCP Options", DHCP_OPT_T,
        special="dhcp_opt", qualify="{n} (DHCP option {c})", keep_unassigned_history=True),
    reg("bootp-dhcp-parameters", "message-type-53", "DHCP Message Type 53 Values", DHCP_MSG_T,
        qualify="{n} (DHCP message type {c})"),
    reg("bootp-dhcp-parameters", "relay-agent-sub-options", "DHCP Relay Agent Sub-Option Codes (option 82)", RELAY_SUB_T,
        qualify="{n} (option 82 sub-option {c})", long_name=60),
    reg("dhcpv6-parameters", "dhcpv6-parameters-2", "DHCPv6 Option Codes", DHCP6_OPT_T, special="dhcpv6_opt",
        qualify="{n} (DHCPv6 option {c})"),
    reg("dhcpv6-parameters", "dhcpv6-parameters-1", "DHCPv6 Message Types", DHCP6_MSG_T,
        qualify="{n} (DHCPv6 message type {c})", bare_ok=DHCP6_MSG_BARE),
    reg("dhcpv6-parameters", "dhcpv6-parameters-5", "DHCPv6 Status Codes", DHCP6_STATUS_T,
        qualify="{n} (DHCPv6 status code {c})"),
    reg("dhcpv6-parameters", "dhcpv6-parameters-6", "DHCPv6 DUID Types", DUID_T, qualify="{n} (DUID type {c})"),
    # Special-purpose address registries (RFC 6890; rfc6890 and rfc5737 are in rfcs-add7.txt). IANA retitled them
    # "IPv4/IPv6 Special-Purpose Address Space" in 2025 (the files' "Formerly known as" note); the labels keep
    # FIXES.md's registry names.
    reg("iana-ipv4-special-registry", "iana-ipv4-special-registry-1", "IANA IPv4 Special-Purpose Address Registry",
        cols=("address", "name"), special="addr_special", qualify="{n} ({c})", code_alias=True),
    reg("iana-ipv6-special-registry", "iana-ipv6-special-registry-1", "IANA IPv6 Special-Purpose Address Registry",
        cols=("address", "name"), special="addr_special", qualify="{n} ({c})", code_alias=True),
]

# Ports kept from the IANA port registry (all services on these port numbers, tcp/udp merged).
PORTS = [20, 21, 22, 23, 49, 53, 67, 68, 69, 80, 123, 161, 162, 179, 389, 443, 500, 514, 520, 521, 546, 547, 601, 636,
         639, 646, 830, 853, 1701, 1723, 1812, 1813, 1985, 3222, 3503, 3784, 3785, 3799, 4334, 4341, 4342, 4500, 4739,
         4784, 4789, 5060, 5061, 5246, 5247, 5353, 6081, 6513, 6514, 6653, 6784, 7784, 9339, 10161, 10162,
         # corpus-v8 (FIXES.md 1.4 d): the port numbers the sweep searched for without a KB row, and their neighbours.
         # Not 1645/1646: their IANA owners (sightline, sa-msg-port) are not the Cisco RADIUS use of those numbers.
         25, 465, 587, 1167]
PORTS_LABEL = "Service Name and Transport Protocol Port Numbers"
SERVICE_ALIASES = {"bgp": ["BGP port"], "ldp": ["LDP port"], "bfd-control": ["BFD single-hop", "BFD control port"],
                   "bfd-echo": ["BFD echo port"], "bfd-multi-ctl": ["multihop BFD", "BFD multihop port"],
                   "isakmp": ["IKE", "ISAKMP port", "IKE port"], "ipsec-nat-t": ["NAT-T", "IPsec NAT traversal"],
                   "vxlan": ["VXLAN port"], "lisp-data": ["LISP data port"], "lisp-control": ["LISP control port", "map-register port"],
                   "netconf-ssh": ["NETCONF port"], "router": ["RIP port", "RIP"], "ripng": ["RIPng port"], "hsrp": ["HSRP port"],
                   "glbp": ["GLBP port"], "msdp": ["MSDP port"], "lsp-ping": ["LSP ping", "MPLS ping port"], "gnmi-gnoi": ["gNMI port"],
                   "capwap-control": ["CAPWAP control"], "capwap-data": ["CAPWAP data"], "tacacs": ["TACACS+ port"],
                   "radius": ["RADIUS authentication port"], "radius-acct": ["RADIUS accounting port"],
                   "radius-dynauth": ["RADIUS CoA", "CoA port"], "ipfix": ["IPFIX port"], "bfd-lag": ["micro-BFD", "BFD on LAG"],
                   "s-bfd": ["S-BFD port"], "ntp": ["NTP port"], "snmp": ["SNMP port"], "snmptrap": ["SNMP trap port"],
                   "syslog": ["syslog port"], "ssh": ["SSH port"], "openflow": ["OpenFlow port"]}

# ---- curated practitioner aliases and notes: {registry id: {code: [...]}} ----
ALIASES = {
    "ospfv2-parameters-5": {
        "1": ["router LSA"], "2": ["network LSA"],
        "3": ["summary LSA", "network summary LSA", "Type 3 summary LSA"],
        "4": ["ASBR-summary-LSA", "ASBR summary LSA", "Type 4 summary LSA"],
        "5": ["external LSA", "AS external LSA", "Type 5 external LSA"],
        "6": ["group membership LSA", "MOSPF LSA"],
        "7": ["NSSA-LSA", "NSSA LSA", "NSSA external LSA", "Type-7 LSA", "NSSA-external"],
        "9": ["link-local opaque LSA", "opaque LSA type 9"], "10": ["area-local opaque LSA", "opaque LSA type 10"],
        "11": ["AS opaque LSA", "opaque LSA type 11"],
    },
    "ospfv2-parameters-3": {"1": ["OSPF hello"], "2": ["DBD", "DD packet", "OSPF DBD"], "3": ["LSR", "OSPF LSR"],
                            "4": ["LSU", "OSPF LSU"], "5": ["LSAck", "OSPF LSAck", "Link State Acknowledgment"]},
    "ospfv2-parameters-7": {"1": ["point-to-point link"], "2": ["transit link"], "3": ["stub link"], "4": ["virtual link"]},
    "ospf-opaque-types-2": {"1": ["TE LSA", "opaque type 1 TE"], "3": ["Grace LSA", "graceful restart LSA"],
                            "4": ["RI LSA", "router information LSA"], "7": ["Extended Prefix LSA", "OSPF extended prefix"],
                            "8": ["Extended Link LSA", "OSPF extended link"]},
    "authentication-codes": {"0": ["null authentication", "no authentication"],
                             "1": ["plain text authentication", "clear text authentication", "simple password"],
                             "2": ["MD5 authentication", "message-digest authentication", "cryptographic authentication"]},
    "ospfv3-parameters-3": {  # full 16-bit LS type values from RFC 5340 A.4.2.1 (verified in the corpus copy of RFC 5340)
        "1": ["0x2001", "OSPFv3 router LSA"], "2": ["0x2002", "OSPFv3 network LSA"],
        "3": ["0x2003", "inter-area prefix LSA", "OSPFv3 type 3"], "4": ["0x2004", "inter-area router LSA", "OSPFv3 type 4"],
        "5": ["0x4005", "OSPFv3 AS-external LSA", "OSPFv3 type 5"], "7": ["0x2007", "OSPFv3 NSSA LSA", "OSPFv3 type 7"],
        "8": ["0x0008", "link LSA", "OSPFv3 link-LSA", "type 8 LSA"],
        "9": ["0x2009", "intra-area prefix LSA", "OSPFv3 type 9", "type 9 LSA"],
        "11": ["OSPFv3 grace LSA"], "12": ["OSPFv3 RI LSA"],
    },
    "bgp-parameters-1": {"1": ["BGP OPEN"], "2": ["BGP UPDATE"], "3": ["BGP NOTIFICATION"], "4": ["BGP KEEPALIVE"],
                         "5": ["BGP ROUTE-REFRESH", "route refresh message"]},
    "bgp-parameters-2": {
        "1": ["origin attribute"], "2": ["AS path", "AS-path"], "3": ["next hop", "next-hop", "BGP next hop"],
        "4": ["MED", "multi-exit discriminator", "BGP metric"], "5": ["local preference", "local-preference", "local pref"],
        "6": ["atomic aggregate"], "7": ["aggregator"], "8": ["community", "standard community", "COMMUNITY"],
        "9": ["originator ID", "originator-id"], "10": ["cluster list", "cluster-list"],
        "14": ["MP_REACH", "MP-REACH", "multiprotocol reachable NLRI"],
        "15": ["MP_UNREACH", "MP-UNREACH", "multiprotocol unreachable NLRI"],
        "16": ["extended community", "extcommunity", "EXTENDED_COMMUNITIES"], "17": ["4-octet AS path", "AS4 path"],
        "18": ["4-octet aggregator"], "22": ["PMSI tunnel attribute"], "23": ["tunnel encapsulation attribute"],
        "26": ["accumulated IGP metric"], "29": ["BGP-LS attribute"], "32": ["large community", "large-community"],
        "35": ["OTC", "only to customer"], "40": ["prefix-SID attribute"],
    },
    "bgp-parameters-3": {"4": ["hold timer expired", "hold time expired"], "6": ["cease"]},
    "bgp-parameters-6": {"2": ["bad peer AS", "peer in wrong AS"], "4": ["unsupported optional parameter"],
                         "6": ["unacceptable hold time"], "7": ["unsupported capability"]},
    "bgp-parameters-8": {"1": ["maximum prefixes reached", "maximum-prefix"], "2": ["admin shutdown", "neighbor shutdown"],
                         "4": ["admin reset", "clear ip bgp"], "7": ["connection collision"]},
    "capability-codes-2": {
        "1": ["multiprotocol extensions", "MP-BGP", "MP_EXT"], "2": ["route refresh", "route-refresh"],
        "3": ["ORF capability"], "5": ["extended next hop encoding", "extended nexthop"], "6": ["extended message"],
        "64": ["graceful restart", "BGP graceful restart", "GR capability"],
        "65": ["4-octet AS", "4-byte AS", "4-byte ASN", "four-octet AS number", "AS4", "32-bit ASN"],
        "69": ["ADD-PATH", "add-paths", "additional paths", "BGP add-path"], "70": ["enhanced route refresh"],
        "71": ["LLGR", "long-lived graceful restart"], "73": ["hostname capability"],
        "128": ["Cisco pre-standard route refresh", "old route refresh"],
    },
    "bgp-well-known-communities-1": {"0xFFFFFF01": ["no-export", "NO_EXPORT"], "0xFFFFFF02": ["no-advertise"],
                                     "0xFFFFFF03": ["local-AS", "no-export-subconfed"], "0xFFFFFF04": ["no-peer"],
                                     "0xFFFF0000": ["gshut", "graceful-shutdown"], "0xFFFF029A": ["blackhole", "RTBH"],
                                     "0xFFFF0001": ["accept-own"]},
    "trans-two-octet-as": {"0x02": ["route target", "RT", "route-target", "rt"], "0x03": ["route origin", "SoO", "site of origin"],
                           "0x05": ["OSPF domain identifier"], "0x08": ["BGP data collection"], "0x09": ["source AS"],
                           "0x0a": ["L2VPN identifier"], "0x10": ["Cisco VPN distinguisher"]},
    "trans-ipv4": {"0x02": ["route target", "RT (IPv4 format)"], "0x03": ["route origin", "SoO (IPv4 format)"],
                   "0x05": ["OSPF domain identifier"], "0x07": ["OSPF router ID extended community"],
                   "0x0b": ["VRF route import"]},
    "trans-four-octet-as": {"0x02": ["route target", "RT (4-octet AS format)"], "0x03": ["route origin", "SoO (4-octet AS format)"]},
    "trans-opaque": {"0x01": ["cost community"], "0x06": ["OSPF route type extended community"], "0x0b": ["color", "color community"],
                     "0x0c": ["encapsulation extended community"]},
    "non-trans-two-octet-as": {"0x04": ["link bandwidth", "DMZ link bandwidth"]},
    "address-family-numbers-2": {"1": ["IPv4", "AFI IPv4"], "2": ["IPv6", "AFI IPv6"], "25": ["L2VPN", "AFI L2VPN"],
                                 "16388": ["BGP-LS", "link-state"]},
    "safi-namespace-2": {
        "1": ["unicast", "SAFI unicast"], "2": ["multicast", "SAFI multicast"], "4": ["labeled unicast", "BGP-LU", "BGP labeled unicast"],
        "5": ["MVPN", "MCAST-VPN"], "65": ["VPLS"], "66": ["MDT SAFI", "BGP MDT"], "70": ["EVPN", "L2VPN EVPN"],
        "71": ["BGP-LS SAFI"], "73": ["SR Policy", "SR-TE policy"], "74": ["SD-WAN capabilities SAFI"],
        "128": ["VPNv4", "VPNv6", "vpnv4 unicast", "L3VPN", "MPLS VPN", "MPLS L3VPN"],
        "129": ["multicast VPN", "MPLS-labeled VPN multicast"], "132": ["RT constraint", "RT-Constrain", "route target constraint", "RTC"],
        "133": ["flowspec", "flow spec", "IPv4 flowspec"], "134": ["VPN flowspec", "flowspec VPN"],
    },
    "tlv-codepoints": {
        "1": ["area addresses TLV"], "2": ["IS neighbors TLV", "narrow IS reachability"], "8": ["padding TLV", "hello padding"],
        "10": ["authentication TLV"], "22": ["extended IS reachability", "wide IS reachability"],
        "128": ["IP internal reachability", "narrow IP reachability"], "129": ["protocols supported", "NLPID TLV"],
        "130": ["IP external reachability"], "132": ["IP interface address"], "134": ["TE router ID"],
        "135": ["extended IP reachability", "wide metrics IP reachability"], "137": ["dynamic hostname", "hostname TLV"],
        "222": ["MT IS reachability"], "229": ["multi-topology TLV", "MT TLV"], "232": ["IPv6 interface address"],
        "235": ["MT IP reachability"], "236": ["IPv6 reachability"], "237": ["MT IPv6 reachability"],
        "240": ["three-way adjacency", "P2P three-way handshake"], "242": ["router capability TLV"],
    },
    "isis-tlv-codepoints-10": {"1": ["IS-IS cleartext authentication", "IS-IS plain text password", "IS-IS authentication mode text"],
                               "3": ["CRYPTO_AUTH", "IS-IS generic cryptographic authentication", "IS-IS HMAC-SHA authentication"],
                               "54": ["HMAC-MD5", "IS-IS HMAC-MD5", "IS-IS MD5 authentication", "IS-IS authentication mode md5"]},
    "message-types": {"0": ["PIM hello"], "1": ["PIM register"], "2": ["register-stop", "PIM register-stop"],
                      "3": ["join/prune", "PIM join", "PIM prune"], "4": ["BSR message", "bootstrap"], "5": ["PIM assert"],
                      "6": ["PIM graft"], "7": ["graft-ack"], "8": ["C-RP-Adv", "candidate RP advertisement"],
                      "9": ["state refresh"], "10": ["DF election"]},
    "igmp-type-numbers-1": {"0x11": ["membership query", "IGMP query", "general query"], "0x12": ["IGMPv1 report"],
                            "0x16": ["IGMPv2 report"], "0x17": ["IGMP leave", "leave group"], "0x22": ["IGMPv3 report"],
                            "0x13": ["DVMRP"], "0x14": ["PIMv1"], "0x1e": ["mtrace response"], "0x1f": ["mtrace"]},
    "multicast-addresses-1": {"224.0.0.1": ["all hosts", "all systems"], "224.0.0.2": ["all routers"],
                              "224.0.0.5": ["AllSPFRouters", "all OSPF routers", "OSPF multicast"],
                              "224.0.0.6": ["AllDRouters", "OSPF DR/BDR", "OSPF designated routers"],
                              "224.0.0.9": ["RIPv2 multicast", "RIP routers"], "224.0.0.10": ["EIGRP multicast"],
                              "224.0.0.13": ["ALL-PIM-ROUTERS", "PIM hello address"], "224.0.0.18": ["VRRP multicast"],
                              "224.0.0.22": ["IGMPv3 reports", "IGMPv3 multicast"], "224.0.0.102": ["HSRPv2 multicast", "GLBP"]},
    "multicast-addresses-2": {"224.0.1.39": ["Auto-RP announce", "RP-announce"], "224.0.1.40": ["Auto-RP discovery", "RP-discovery"],
                              "224.0.1.1": ["NTP multicast"]},
    "multicast-addresses-12": {"239.0.0.0-239.255.255.255": ["administratively scoped", "admin-scoped", "239/8"]},
    "link-local": {"FF02:0:0:0:0:0:0:5": ["AllSPFRouters (IPv6)", "OSPFv3 all routers"],
                   "FF02:0:0:0:0:0:0:6": ["AllDRouters (IPv6)", "OSPFv3 DR/BDR"],
                   "FF02:0:0:0:0:0:0:9": ["RIPng multicast"], "FF02:0:0:0:0:0:0:A": ["EIGRP IPv6 multicast"],
                   "FF02:0:0:0:0:0:0:D": ["PIM IPv6 multicast"], "FF02:0:0:0:0:0:0:16": ["MLDv2 reports"],
                   "FF02:0:0:0:0:0:1:2": ["DHCPv6 relay/server multicast"],
                   "FF02:0:0:0:0:1:FF00::/104": ["solicited-node multicast", "ff02::1:ff00:0/104"]},
    "protocol-numbers-1": {
        "1": ["ICMP"], "2": ["IGMP"], "4": ["IP-in-IP", "IPIP", "IPv4-in-IPv4"], "6": ["TCP"], "17": ["UDP"],
        "41": ["IPv6 encapsulation", "6in4", "IPv6-in-IPv4"], "46": ["RSVP"], "47": ["GRE"], "50": ["ESP", "IPsec ESP"],
        "51": ["AH", "IPsec AH"], "58": ["ICMPv6"], "88": ["EIGRP"], "89": ["OSPF", "OSPFIGP"], "103": ["PIM"],
        "112": ["VRRP"], "115": ["L2TPv3"], "124": ["IS-IS over IPv4"], "132": ["SCTP"], "137": ["MPLS-in-IP"],
    },
    "icmp-parameters-types": {"0": ["echo reply", "ping reply"], "3": ["destination unreachable", "unreachable"],
                              "5": ["ICMP redirect"], "8": ["echo request", "ping"], "11": ["time exceeded", "TTL exceeded"]},
    "icmp-parameters-codes-3": {"4": ["fragmentation needed", "frag needed and DF set", "PMTUD"], "13": ["administratively prohibited"],
                                "3": ["port unreachable"], "1": ["host unreachable"], "0": ["net unreachable"]},
    "icmp-parameters-codes-11": {"0": ["TTL expired in transit"]},
    "icmpv6-parameters-2": {"2": ["packet too big", "PTB"], "133": ["RS", "router solicitation"], "134": ["RA", "router advertisement"],
                            "135": ["NS", "neighbor solicitation"], "136": ["NA", "neighbor advertisement"],
                            "137": ["ICMPv6 redirect"], "130": ["MLD query"], "131": ["MLDv1 report"], "132": ["MLD done"],
                            "143": ["MLDv2 report"], "128": ["ping6", "ICMPv6 echo request"]},
    "bfd-parameters-1": {"1": ["control detection time expired", "detect timer expired"], "3": ["neighbor down", "neighbor signaled down"],
                         "5": ["path down"], "7": ["admin down", "AdminDown"]},
    "special-purpose": {"0": ["explicit null", "IPv4 explicit-null", "exp-null"], "1": ["router alert label"],
                        "2": ["IPv6 explicit null", "IPv6 explicit-null"], "3": ["implicit null", "imp-null", "implicit-null", "PHP label"],
                        "7": ["ELI", "entropy label indicator"], "13": ["GAL"], "15": ["XL", "extension label"]},
    "ldp-namespaces-2": {"0x0100": ["LDP hello"], "0x0200": ["LDP initialization"], "0x0201": ["LDP keepalive"],
                         "0x0400": ["label mapping"], "0x0402": ["label withdraw"], "0x0403": ["label release"]},
    "radius-types-2": {"26": ["VSA", "vendor-specific attribute"], "101": ["Error-Cause"]},
    "radius-types-27": {"40": ["Disconnect Message"], "43": ["Change-of-Authorization request"]},
    "eap-numbers-4": {"4": ["EAP-MD5"], "6": ["EAP-GTC", "GTC"], "25": ["Protected EAP"],
                      "43": ["Flexible Authentication via Secure Tunneling"],
                      "55": ["Tunnel Extensible Authentication Protocol"]},
    # corpus-v8 (FIXES.md 1.4 c): only for names that are not identifier-like, from IANA's or the RFCs' own wording
    "relay-agent-sub-options": {"1": ["circuit-id", "agent circuit ID"], "2": ["remote-id", "agent remote ID"],
                                "5": ["link selection sub-option"], "11": ["server identifier override sub-option"],
                                "151": ["virtual subnet selection sub-option"]},
    "iana-ipv4-special-registry-1": {"169.254.0.0/16": ["APIPA", "IPv4 link-local"],
                                     "10.0.0.0/8": ["RFC 1918 private address"], "172.16.0.0/12": ["RFC 1918 private address"],
                                     "192.168.0.0/16": ["RFC 1918 private address"], "192.0.2.0/24": ["TEST-NET-1"],
                                     "198.51.100.0/24": ["TEST-NET-2"], "203.0.113.0/24": ["TEST-NET-3"]},
    "iana-ipv6-special-registry-1": {"fe80::/10": ["IPv6 link-local unicast"], "fc00::/7": ["ULA", "unique local address"],
                                     "2001:db8::/32": ["IPv6 documentation prefix"], "::1/128": ["IPv6 loopback"],
                                     "::/128": ["IPv6 unspecified address"], "64:ff9b::/96": ["NAT64 well-known prefix"]},
}

# Published RFCs appended after IANA's own refs where IANA cites only an RFC-to-be draft (rfc4360-bis).
EXTRA_REFS = {
    "trans-two-octet-as": {"0x02": ["RFC 4360", "RFC 4364"], "0x03": ["RFC 4360", "RFC 4364"]},
    "trans-ipv4": {"0x02": ["RFC 4360", "RFC 4364"], "0x03": ["RFC 4360", "RFC 4364"]},
}

NOTES = {
    "ospfv2-parameters-5": {
        "1": "Originated by every router for each area it belongs to; lists the router's links. Area flooding scope.",
        "2": "Originated by the DR of a broadcast/NBMA transit network; lists attached routers. Area flooding scope.",
        "3": "Originated by ABRs; advertises inter-area IP prefixes into an area. Area flooding scope.",
        "4": "Originated by ABRs; advertises reachability to an ASBR located in another area.",
        "5": "Originated by ASBRs for external (redistributed) routes, metric type E1/E2. AS-wide flooding; not flooded into stub areas or NSSAs.",
        "7": "Originated by ASBRs inside an NSSA; area flooding scope. The NSSA ABR translates it into a type-5 LSA when the P-bit is set (RFC 3101).",
        "9": "Opaque LSA with link-local flooding scope (RFC 5250).",
        "10": "Opaque LSA with area-local flooding scope, e.g. TE and RI LSAs (RFC 5250).",
        "11": "Opaque LSA with AS-wide flooding scope (RFC 5250).",
    },
    "bgp-parameters-2": {
        "1": "Well-known mandatory.", "2": "Well-known mandatory.", "3": "Well-known mandatory.",
        "4": "Optional non-transitive; compared between paths from the same neighbouring AS by default.",
        "5": "Well-known discretionary; sent only to internal (iBGP/confederation) peers; higher is preferred.",
        "6": "Well-known discretionary.", "7": "Optional transitive.", "8": "Optional transitive (RFC 1997).",
        "9": "Optional non-transitive; set by a route reflector to the originating router's BGP identifier (RFC 4456).",
        "10": "Optional non-transitive; cluster IDs of the route reflectors the route passed through (RFC 4456).",
        "14": "Optional non-transitive; carries multiprotocol NLRI and next hop (RFC 4760).",
        "15": "Optional non-transitive; withdraws multiprotocol NLRI (RFC 4760).",
        "16": "Optional transitive (RFC 4360).", "17": "Optional transitive (RFC 6793).", "18": "Optional transitive (RFC 6793).",
        "26": "Optional non-transitive (RFC 7311).", "32": "Optional transitive (RFC 8092).", "35": "Optional transitive (RFC 9234).",
    },
    "radius-types-2": {   # checked against the corpus copies of RFC 2865, RFC 3579 and RFC 3580
        "24": "Sent by the server in an Access-Challenge; the client returns it unmodified in the next Access-Request "
              "(RFC 2865 \xa75.24).",
        "25": "Sent by the server in an Access-Accept; the client sends it unmodified to the accounting server in "
              "Accounting-Request packets and does not interpret it (RFC 2865 \xa75.25).",
        "26": "Vendor-Specific Attribute (VSA): a Vendor-Id (the vendor's SMI Private Enterprise Code) followed by "
              "vendor-defined attributes (RFC 2865 \xa75.26). Cisco's code is 9; cisco-av-pair is one of its VSAs.",
        "30": "IEEE 802.1X: the bridge or access point MAC address; in IEEE 802.11 the SSID is appended after a ':' "
              "(RFC 3580 \xa73.20).",
        "31": "IEEE 802.1X: the supplicant's MAC address (RFC 3580 \xa73.21).",
        "61": "IEEE 802.1X uses Ethernet (15) or Wireless - IEEE 802.11 (19) (RFC 3580 \xa73.23).",
        "64": "VLAN assignment: Tunnel-Type=VLAN (13) with Tunnel-Medium-Type=802 (6) and Tunnel-Private-Group-ID="
              "the VLAN ID (RFC 3580 \xa73.31).",
        "65": "VLAN assignment uses 802 (6) with Tunnel-Type=VLAN (13) (RFC 3580 \xa73.31).",
        "79": "Carries EAP packets between the authenticator and the RADIUS server (RFC 3579).",
        "80": "Must protect every Access-Request, Access-Challenge, Access-Accept and Access-Reject that carries "
              "EAP-Message (RFC 3579 \xa73.1).",
        "81": "VLAN assignment: the VLAN ID as a string, with Tunnel-Type=VLAN (13) and Tunnel-Medium-Type=802 "
              "(RFC 3580 \xa73.31).",
    },
    "radius-types-27": {  # the RFC port is 3799; ISE (ise31-admin-integration, -threat-containment) sends CoA to UDP
        # 1700, the Cisco IOS default, for Cisco devices and to 3799 for other vendors' devices
        "40": "Dynamic authorization: sent by the Dynamic Authorization Client (ISE, in a Cisco network) to the NAS "
              "to end user sessions, to UDP port 3799 (RFC 5176 \xa72.1, \xa72.3); ISE uses UDP port 1700, the Cisco IOS "
              "default, for Cisco devices.",
        "43": "Dynamic authorization (CoA): sent by the Dynamic Authorization Client (ISE, in a Cisco network) to "
              "the NAS to change a session's authorization, to the same port as Disconnect-Request, UDP port 3799 "
              "(RFC 5176 \xa72.2, \xa72.3); ISE uses UDP port 1700, the Cisco IOS default, for Cisco devices.",
    },
}

# ---- DSCP per-hop behaviours (RFC 2474 / 2597 / 3246 / 5865 / 8622) and RFC 4594 service classes ----
RFC4594_CLASS = {"CS6": "Network Control", "EF": "Telephony", "CS5": "Signaling", "AF41": "Multimedia Conferencing",
                 "AF42": "Multimedia Conferencing", "AF43": "Multimedia Conferencing", "CS4": "Real-Time Interactive",
                 "AF31": "Multimedia Streaming", "AF32": "Multimedia Streaming", "AF33": "Multimedia Streaming",
                 "CS3": "Broadcast Video", "AF21": "Low-Latency Data", "AF22": "Low-Latency Data", "AF23": "Low-Latency Data",
                 "CS2": "OAM", "AF11": "High-Throughput Data", "AF12": "High-Throughput Data", "AF13": "High-Throughput Data",
                 "CS0": "Standard (default forwarding)"}
# RFC 8622 section 10 updates RFC 4594: every CS1 recommendation for Low-Priority Data is replaced by LE (000001).
LOW_PRIORITY_NOTE = {
    "CS1": "RFC 4594 recommended CS1 for its Low-Priority Data service class; RFC 8622 replaced that with LE (DSCP 1).",
    "LE": "RFC 4594 service class (as updated by RFC 8622, replacing CS1): Low-Priority Data.",
}

# ---- Cisco SD-WAN ports: vendor-documented, NOT IANA assignments (corpus doc sdwan-design-cvd, "Firewall Port Considerations") ----
SDWAN_LABEL = "Cisco SD-WAN Ports (vendor-documented, not IANA-assigned)"
SDWAN_PORTS = [
    {"code": "12346", "name": "SD-WAN DTLS control / IPsec data base port (UDP)",
     "description": ("Default base UDP source port for DTLS control connections and IPsec data tunnels. With port hopping the "
                     "WAN Edge tries 12366, 12386, 12406 and 12426 before returning to 12346; a port offset n (0-19) shifts "
                     "every port by n. Manager/Controller cores listen on 12346 + 100 x core (12446, 12546, ... 13046). "
                     "Validator connections always use DTLS on UDP 12346. IANA does not assign 12346 to SD-WAN."),
     "aliases": ["UDP 12346", "port 12346", "SD-WAN DTLS port", "SD-WAN control port", "vBond port", "Validator port",
                 "12366", "12386", "12406", "12426", "port hopping ports"]},
    {"code": "23456", "name": "SD-WAN TLS control port (TCP)",
     "description": ("Destination TCP port for TLS control connections to the SD-WAN Manager/Controller (core 0); further cores "
                     "use 23556, 23656, ... 24156. The client side uses a random TCP port > 1024. IANA does not assign 23456 to SD-WAN."),
     "aliases": ["TCP 23456", "port 23456", "SD-WAN TLS port"]},
]
SDWAN_REF = "doc:sdwan-design-cvd"


class StructureError(Exception):
    """A configured registry (or parent code) is missing from the IANA file."""


# ------------------------------------------------------------------------------------------------
def text_of(el) -> str:
    """Element text with nested <xref type="rfc" data="rfcN"/> rendered as 'RFC N'; whitespace collapsed."""
    if el is None:
        return ""
    parts = [el.text or ""]
    for child in el:
        if child.tag == NS + "xref" and not (child.text or "").strip():
            parts.append(ref_of(child) or "")
        else:
            parts.append(text_of(child))
        parts.append(child.tail or "")
    return " ".join("".join(parts).split())


def ref_of(x):
    """<xref> -> 'RFC 4271' / 'RFC 3260 \xa74' / 'Errata ID 4493' / 'draft-...'; None for people, URIs, text."""
    kind, data = x.get("type"), (x.get("data") or "").strip()
    if kind == "rfc":
        m = re.fullmatch(r"(?i)rfc0*(\d+)", data)
        if not m:
            return None
        sec = re.search(r"(?i)section\s+([\w.]+)", x.text or "")
        return f"RFC {m.group(1)}" + (f" \xa7{sec.group(1).rstrip('.')}" if sec else "")
    if kind == "rfc-errata":
        return f"Errata ID {data}"
    if kind == "draft":
        return data or None
    return None


def refs_of(rec) -> list:
    out = []
    for x in rec.iter(NS + "xref"):
        r = ref_of(x)
        if r and r not in out:
            out.append(r)
    return out


def field(rec, tag):
    el = rec.find(NS + tag)
    return text_of(el) if el is not None else None


def hex_or_dec(code: str):
    if re.fullmatch(r"0x[0-9a-fA-F]+", code):
        return int(code, 16)
    if re.fullmatch(r"\d+", code):
        return int(code)
    return None


def parse_record(rec):
    """-> (code, name, iana_description) using the registry's own column names."""
    code = next((v for v in (field(rec, t) for t in ("value", "addr", "decimal_space", "bit", "relative")) if v), None)
    if code is not None:
        name_tags = ("name", "code", "description", "attribute", "profile", "algorithm", "method", "authentication_type_code")
    else:
        code = field(rec, "code")
        name_tags = ("name", "description", "profile", "algorithm", "method", "authentication_type_code")
    name_tag = next((t for t in name_tags if field(rec, t)), None)
    name = field(rec, name_tag) if name_tag else ""
    desc = field(rec, "description") if name_tag in ("name", "code") else ""
    return code, name, (desc if desc and desc != name else "")


def is_skipped(code, name, keep_ranges) -> bool:
    if not code or not name:
        return True
    if re.match(r"(?i)(unassigned|variable scope allocation)\b", name):
        return True
    return "-" in code and not keep_ranges


def name_variants(name: str) -> list:
    """'MP_REACH_NLRI' -> 'MP REACH NLRI', 'MP-REACH-NLRI'; 'P-bit' -> 'P bit'. A variant that splits off
    two or more single letters ('S-PMSI A-D route' -> 'S PMSI A D route') is noise and is skipped."""
    def singles(s):
        return sum(1 for t in s.split() if len(t) == 1 and t.isalpha())

    out = []
    if re.search(r"[A-Za-z]", name) and len(name) <= 40:
        for a, b in (("_", " "), ("_", "-"), ("-", " ")):
            v = name.replace(a, b)
            if a in name and singles(v) - singles(name) < 2:
                out.append(v)
    return out


def add_aliases(rec: dict, extra):
    seen = {rec["name"].casefold(), rec["code"].casefold()} | {a.casefold() for a in rec["aliases"]}
    for a in extra:
        a = " ".join(str(a).split())
        if a and a.casefold() not in seen:
            seen.add(a.casefold())
            rec["aliases"].append(a)


def add_code_aliases(rec: dict):
    """Public knowledge-base source helper."""
    have = {rec["name"].casefold()} | {a.casefold() for a in rec["aliases"]}
    code = " ".join(rec["code"].split())
    for part in [code] + [p.strip() for p in code.split(",")]:
        if part and ("." in part or ":" in part) and part.casefold() not in have:
            have.add(part.casefold())
            rec["aliases"].append(part)


def join_sentences(*parts) -> str:
    """The non-empty parts joined with a space, each one but the last ending in '.' (one is added if missing)."""
    parts = [p.strip() for p in parts if p and p.strip()]
    return " ".join(p if i == len(parts) - 1 or p.endswith((".", "!", "?")) else p + "."
                    for i, p in enumerate(parts))


def render_templates(templates, code, parent=None, n=None) -> list:
    """The templates rendered for one code point: {c} the code, {d} its decimal value, {nn} a hex code as AA:NN,
    {p}/{pn}/{pc} the parent code, name and joined code, {n} the IANA name (qualify registries). A template whose
    variable is missing is skipped."""
    d = hex_or_dec(code)
    ctx = {"c": code, "d": d if d is not None else code}
    if n is not None:
        ctx["n"] = n
    if d is not None and code.lower().startswith("0x"):
        ctx["nn"] = f"{d >> 16}:{d & 0xFFFF}"
    if parent:
        ctx.update(p=parent[0], pn=parent[1])
        if parent[0].lower().startswith("0x") and code.lower().startswith("0x"):
            ctx["pc"] = parent[0] + code[2:]
    out = []
    for t in templates:
        try:
            out.append(t.format(**ctx))
        except KeyError:
            continue
    return out


def dscp_record(rec, name, cfg):
    dec = int(field(rec, "decimal_space"))
    bits = field(rec, "space")
    tos = dec << 2
    aliases = [f"DSCP {dec}", f"DSCP {name}", bits, f"ToS 0x{tos:02X}", f"TOS byte {tos}"]
    m_cs, m_af = re.fullmatch(r"CS(\d)", name), re.fullmatch(r"AF(\d)(\d)", name)
    if m_cs:
        x = m_cs.group(1)
        desc = f"Class Selector {x}; backward compatible with IP precedence {x}."
        aliases += [f"IP precedence {x}", f"precedence {x}"] + (["DF", "default forwarding", "best effort", "BE"] if x == "0" else [])
    elif m_af:
        x, y = m_af.groups()
        desc = f"Assured Forwarding class {x}, drop precedence {y} ({ {'1': 'low', '2': 'medium', '3': 'high'}.get(y, y) })."
        aliases += [f"AF{x}{y} PHB"]
    elif name == "EF":
        desc = "Expedited Forwarding PHB: low-loss, low-latency, low-jitter priority traffic such as voice."
        aliases += ["expedited forwarding", "EF PHB"]
    elif name == "VOICE-ADMIT":
        desc = "Capacity-admitted traffic using the EF PHB (e.g. admitted voice)."
        aliases += ["voice admit"]
    elif name == "LE":
        desc = "Lower-Effort PHB (scavenger traffic)."
        aliases += ["lower effort", "scavenger"]
    elif name == "NQB":
        desc = "Non-Queue-Building traffic."
        aliases += ["non-queue-building"]
    else:
        desc = ""
    refs = refs_of(rec)
    if name in RFC4594_CLASS:
        desc += f" RFC 4594 service class: {RFC4594_CLASS[name]}."
        refs.append("RFC 4594")
    if name in LOW_PRIORITY_NOTE:
        desc += " " + LOW_PRIORITY_NOTE[name]
        refs += [r for r in ("RFC 4594", "RFC 8622") if r not in refs]
    return {"registry": cfg["label"], "code": str(dec), "name": name, "description": desc.strip(),
            "refs": refs, "aliases": [], "url": cfg["url"]}, aliases


def build_registry(root, cfg, parsed) -> list:
    reg_el = next((r for r in root.iter(NS + "registry") if r.get("id") == cfg["rid"]), None)
    if reg_el is None:
        raise StructureError(f"registry {cfg['rid']} not found in {cfg['file']}.xml")
    parent = None
    if cfg.get("parent"):
        prid, pcode = cfg["parent"]
        pname = next((r["name"] for r in parsed.get(prid, []) if r["code"].lower() == pcode.lower()), None)
        if pname is None:
            raise StructureError(f"parent {prid}:{pcode} for {cfg['rid']} not found")
        parent = (pcode, re.sub(r"\s*\(Sub-Types.*$", "", pname))
    special = cfg.get("special")
    qualify = cfg.get("qualify")
    out = OrderedDict()
    also = OrderedDict()   # qualify registries: code -> the IANA names of its later duplicate records
    if cfg.get("block"):  # the address block named by the registry title itself
        code, name, aliases = cfg["block"]
        refs = [r for r in (ref_of(x) for x in reg_el.findall(NS + "xref")) if r]
        out[code] = {"registry": cfg["label"], "code": code, "name": name,
                     "description": f"IANA block: {text_of(reg_el.find(NS + 'title'))}", "refs": refs, "aliases": [], "url": cfg["url"]}
        add_aliases(out[code], aliases)
        if cfg.get("code_alias"):
            add_code_aliases(out[code])
    for rec in reg_el.findall(NS + "record"):
        if cfg.get("cols"):    # a registry with column names of its own: ("decimal", "message") for RADIUS packet codes
            code, name, desc = field(rec, cfg["cols"][0]), field(rec, cfg["cols"][1]), ""
        else:
            code, name, desc = parse_record(rec)
        if code and not name:  # a column we do not know: fail instead of silently dropping the row
            cols = sorted({c.tag[len(NS):] for c in rec} - {"xref"})
            raise StructureError(f"registry {cfg['rid']}: record {code} has no name column we know ({', '.join(cols)})")
        if qualify and code and name and UNASSIGNED_HIST.match(name):
            # keep_unassigned_history: a single code that IANA lists as (Removed/)Unassigned but with an RFC xref or a
            # description of its history is kept; ranges and bare Unassigned rows are skipped as everywhere
            history = any(r.startswith("RFC ") for r in refs_of(rec)) or bool(desc)
            if not (cfg.get("keep_unassigned_history") and "-" not in code and history):
                continue
        elif is_skipped(code, name, cfg.get("keep_ranges")):
            continue
        if cfg.get("long_name") and len(name) > cfg["long_name"]:   # a notice in the name column (relay sub-option 3)
            name, desc = "Reserved", join_sentences(name, desc)
        iana_name, templates, ident = name, cfg["templates"], False
        if qualify:
            ident = bool(IDENT.fullmatch(iana_name)) or iana_name in cfg.get("bare_ok", ())
            if PLACEHOLDER_NAME.match(iana_name):          # no "Reserved option" / "DHCPv6 Unassigned" keys
                templates = [t for t in templates if "{n}" not in t]
            elif iana_name.lower().endswith("option") or iana_name.startswith("OPTION_"):
                templates = [t for t in templates if t != "{n} option"]
            name = qualify.format(n=iana_name, c=code)
        extra = render_templates(templates, code, parent, n=iana_name if qualify else None)
        if special == "dscp":
            item, more = dscp_record(rec, name, cfg)
            extra += more
        else:
            item = {"registry": cfg["label"], "code": code, "name": name, "description": desc,
                    "refs": refs_of(rec), "aliases": [], "url": cfg["url"]}
        if special == "isis_top":
            carried = [pdu for pdu in ("IIH", "LSP", "SNP", "purge") if (field(rec, pdu) or "").lower() == "y"]
            item["description"] = f"Carried in: {', '.join(carried)}." if carried else item["description"]
        elif special == "isis_sub":
            tlvs = [c.tag[len(NS) + 4:] for c in rec if c.tag.startswith(NS + "TLV_") and (c.text or "").strip().lower() == "y"]
            if tlvs:
                item["description"] = f"Sub-TLV of TLV {', '.join(tlvs)}."
                extra += [f"TLV {t} sub-TLV {code}" for t in tlvs]
        elif special == "pim_msg":
            flag = field(rec, "flag-description") or ""
            if flag and not flag.lower().startswith("unassigned"):
                item["description"] = f"Flag bit {field(rec, 'flag-value')}: {flag}."
        elif special == "mcast" and ":" in code and "/" not in code and "-" not in code:
            extra.append(ipaddress.IPv6Address(code).compressed)
        elif special == "radius_attr" and field(rec, "datatype"):
            item["description"] = f"Data type: {field(rec, 'datatype')}."
        elif special == "dhcp_opt":            # IANA's description, plus the option's data length
            length = field(rec, "length")
            item["description"] = join_sentences(item["description"], f"Data length: {length}." if length else "")
        elif special == "dhcpv6_opt":          # the Client ORO and singleton columns, and a status such as Obsolete
            cols = [f"{label}: {v}" for label, v in (("Client ORO", field(rec, "oro")), ("singleton", field(rec, "singleton")))
                    if v]
            status = field(rec, "status")
            item["description"] = join_sentences(item["description"], "; ".join(cols) + "." if cols else "",
                                                 f"Status: {status}." if status else "")
        elif special == "addr_special":        # the registry's boolean columns and dates
            cols = [f"{label}: {v}" for label, v in (
                ("Source", field(rec, "source")), ("destination", field(rec, "destination")),
                ("forwardable", field(rec, "forwardable")), ("globally reachable", field(rec, "global")),
                ("reserved-by-protocol", field(rec, "reserved"))) if v]
            cols += [f"{label} {v}" for label, v in (("allocated", field(rec, "allocation")),
                                                    ("terminated", field(rec, "termination"))) if v]
            item["description"] = join_sentences(item["description"], "; ".join(cols) + "." if cols else "")
        if parent:
            lead = PARENT_DESC.get(cfg["parent"][0], "Subcode of {pn} ({p}).").format(p=parent[0], pn=parent[1])
            item["description"] = " ".join(filter(None, [lead, item["description"]]))
        note = NOTES.get(cfg["rid"], {}).get(code)
        if note:
            item["description"] = " ".join(filter(None, [item["description"], note]))
        if qualify:   # the bare IANA name only when it is identifier-like; its variants likewise
            extra += ([iana_name] + name_variants(iana_name) if ident else []) + ALIASES.get(cfg["rid"], {}).get(code, [])
        else:
            extra += name_variants(name) + ALIASES.get(cfg["rid"], {}).get(code, [])
        item["refs"] += [r for r in EXTRA_REFS.get(cfg["rid"], {}).get(code, []) if r not in item["refs"]]
        if code in out and qualify:   # a code listed twice: the first name stays, the others are only named
            prev = out[code]
            if iana_name not in also.setdefault(code, []):
                also[code].append(iana_name)
            prev["refs"] += [r for r in item["refs"] if r not in prev["refs"]]
            continue
        if code in out:  # same code listed twice (e.g. PIM flag rows): merge
            prev = out[code]
            if item["description"] and item["description"] not in prev["description"]:
                prev["description"] = " ".join(filter(None, [prev["description"], item["description"]]))
            prev["refs"] += [r for r in item["refs"] if r not in prev["refs"]]
            add_aliases(prev, extra)
            continue
        add_aliases(item, extra)
        if cfg.get("code_alias"):
            add_code_aliases(item)
        out[code] = item
    for code, names in also.items():
        out[code]["description"] = join_sentences(out[code]["description"],
                                                  f"Also listed for code {code}: {'; '.join(names)}.")
    if not out:
        raise StructureError(f"registry {cfg['rid']} produced no records")
    return list(out.values())


def build_ports(csv_bytes: bytes) -> list:
    wanted = set(PORTS)
    groups = OrderedDict()
    for row in csv.DictReader(io.StringIO(csv_bytes.decode("utf-8", "replace"))):
        port, svc = (row.get("Port Number") or "").strip(), (row.get("Service Name") or "").strip()
        if not port.isdigit() or int(port) not in wanted or not svc:
            continue
        if "duplicate of" in (row.get("Assignment Notes") or "").lower():
            continue
        g = groups.setdefault((int(port), svc), {"transports": [], "desc": "", "refs": []})
        g["transports"].append(row.get("Transport Protocol", "").strip())
        g["desc"] = g["desc"] or " ".join((row.get("Description") or "").split())
        for n in re.findall(r"\[RFC(\d+)\]", row.get("Reference") or ""):
            if f"RFC {n}" not in g["refs"]:
                g["refs"].append(f"RFC {n}")
    url = BASE.format(PORTS_FILE) + ".xhtml"
    out = []
    for (port, svc), g in sorted(groups.items()):
        transports = sorted(set(t for t in g["transports"] if t))
        item = {"registry": PORTS_LABEL, "code": str(port), "name": svc,
                "description": f"{g['desc'] or svc} ({'/'.join(transports)})", "refs": g["refs"], "aliases": [], "url": url}
        add_aliases(item, [f"port {port}"] + [f"{t.upper()} {port}" for t in transports] + [f"{t}/{port}" for t in transports]
                    + SERVICE_ALIASES.get(svc, []))
        out.append(item)
    missing = sorted(wanted - {int(r["code"]) for r in out})
    if missing:
        raise StructureError(f"ports missing from {PORTS_FILE}.csv: {missing}")
    return out


def build_sdwan() -> list:
    return [{"registry": SDWAN_LABEL, "code": p["code"], "name": p["name"], "description": p["description"],
             "refs": [SDWAN_REF], "aliases": list(p["aliases"]), "url": None} for p in SDWAN_PORTS]


def source_files() -> list:
    """The raw-cache files a build reads: each registry file once, then the port registry CSV."""
    return list(dict.fromkeys(f"{cfg['file']}.xml" for cfg in REGISTRIES)) + [f"{PORTS_FILE}.csv"]


def missing_from_cache(raw_dir=None) -> list:
    """The source_files() that the raw cache lacks (a file without its .fetch.json sidecar counts as missing)."""
    raw = Path(raw_dir or RAW_DIR)
    return [n for n in source_files() if not (raw / n).is_file() or not (raw / f"{n}.fetch.json").is_file()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="re-download sources instead of using the raw cache")
    ap.add_argument("--offline", action="store_true",
                    help="read the raw cache only; a source missing from it is an error (nothing fetched or written)")
    ap.add_argument("--check-cache", action="store_true",
                    help="list the sources missing from the raw cache and exit, 1 if any (writes nothing)")
    ap.add_argument("--out-dir", default=str(KB_DIR))
    args = ap.parse_args(argv)
    if args.offline and args.refresh:
        ap.error("--offline and --refresh exclude each other")
    missing = missing_from_cache() if args.offline or args.check_cache else []
    if args.check_cache:
        print("\n".join(missing) if missing else f"raw cache complete: {len(source_files())} sources in {RAW_DIR}")
        sys.exit(1 if missing else 0)
    if missing:
        sys.exit(f"--offline: not in the raw cache {RAW_DIR}: {', '.join(missing)}; run build_iana.py once with "
                 "network access (it fetches what is missing)")

    sources, roots, parsed, records, errors = {}, {}, {}, [], []
    for cfg in REGISTRIES:
        f = cfg["file"]
        if f not in roots:
            data, src = fetch(BASE.format(f) + ".xml", f"{f}.xml", refresh=args.refresh)
            roots[f], sources[f] = ET.fromstring(data), src
        cfg["url"] = BASE.format(f) + f".xhtml#{cfg['rid']}"
        try:
            parsed[cfg["rid"]] = build_registry(roots[f], cfg, parsed)
        except StructureError as e:
            errors.append(str(e))
            continue
        records += parsed[cfg["rid"]]
    csv_bytes, sources[PORTS_FILE] = fetch(BASE.format(PORTS_FILE) + ".csv", f"{PORTS_FILE}.csv", refresh=args.refresh)
    try:
        records += build_ports(csv_bytes) + build_sdwan()
    except StructureError as e:
        errors.append(str(e))
    if errors:
        sys.exit("IANA structure changed; fix REGISTRIES/PORTS (nothing written):\n  " + "\n  ".join(errors))

    # a port can carry several service names (514 = shell/tcp + syslog/udp); every other (registry, code) is unique
    dup = [k for k, n in Counter((r["registry"], r["code"], r["name"]) for r in records).items() if n > 1]
    if dup:
        sys.exit(f"duplicate (registry, code, name): {dup[:10]}")
    by_registry = Counter(r["registry"] for r in records)
    owners = {}
    for r in records:
        for a in [r["name"]] + r["aliases"]:
            owners.setdefault(a.casefold(), set()).add(r["registry"])
    counts = {"records": len(records), "registries": len(by_registry), "aliases": sum(len(r["aliases"]) for r in records),
              "with_refs": sum(1 for r in records if r["refs"]),
              "names_or_aliases_in_several_registries": sum(1 for v in owners.values() if len(v) > 1),
              "by_registry": dict(by_registry)}
    write_output("iana", records, list(sources.values()), GENERATOR, counts, args.out_dir)
    print(f"iana.json: {len(records)} code points in {len(by_registry)} registries, {counts['aliases']} aliases")


if __name__ == "__main__":
    main()
