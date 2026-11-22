# RFC pitfalls

The line pointers below are search seeds from the corpus this file was written against. If a later knowledge base
moved a passage, find it again with `grep_corpus` and take the location from `verify_quote` before you quote it.
The corpus grows, so **which RFCs are present is not recorded here**. `lookup_rfc` and `list_documents(doc_type:
"rfc")` are the live source for that.

## 1. Obsoleted RFCs

Cite an obsoleted RFC only together with the current end of its chain: `lookup_rfc` names only the direct successor,
so look that one up too, and so on (SKILL.md section 2, step 7). Say when the current RFC is not in the corpus (its
own `lookup_rfc` shows `corpus: not included`).
Chains from rfc-index.xml for the obsoleted RFCs in the corpus:

| Obsoleted | Successor chain | Notes |
|---|---|---|
| 793 TCP | 9293 | BGP and LDP transport, TCP flags in ACLs |
| 1771 BGP-4 | 4271 | A search for "RFC 4271 9.1.2" can surface 1771 §9.1.2 first. Check the RFC number on every hit. |
| 2385 TCP MD5 for BGP | 5925 TCP-AO | `neighbor ... password` "Sets MD5 authentication on a TCP connection to a BGP peer" (c9300-1718-rtng.txt:6793). Check the release before claiming TCP-AO support. |
| 2408 ISAKMP, 2409 IKEv1 | 4306, then 5996, then 7296 (IKEv2) | IKEv1 is Historic in the RFC series, but IOS XE still configures it (`crypto isakmp`). Cite 7296 for IKEv2. |
| 2460 IPv6 | 8200 | |
| 3164 BSD syslog | 5424 | |
| 3376 IGMPv3 | 9776 | 3376 is also updated by 4604 (SSM). |
| 3768 VRRP | 5798, then 9568 | |
| 3810 MLDv2 | 9777 | |
| 5389 STUN | 8489 | |
| 5798 VRRPv3 | 9568 | |
| 6347 DTLS 1.2 | 9147 DTLS 1.3 | SD-WAN Manager reaches devices over a DTLS or TLS tunnel (sdwan-gs.txt:622). |
| 8415 DHCPv6 | 9915 | |
| 8446 TLS 1.3 | 9846 | |

## 2. Updating RFCs

An RFC can be changed by later RFCs that "update" it. When an updating RFC is not in the corpus
(`lookup_rfc` says so), the text you read may be out of date. Say so whenever that update could touch your claim,
and read it when it is present. The updates that matter most:

| RFC | Updated by, with the topic each one changes |
|---|---|
| 4271 BGP-4 | 4724 graceful restart; 6286 AS-wide unique BGP identifier; 6608 FSM error subcodes; 6793 4-octet AS numbers; 7606 revised UPDATE error handling (treat-as-withdraw); 7607 AS 0 processing; 7705 AS migration mechanisms and AS_PATH; 8212 EBGP default without policies; 8654 extended messages; 9072 extended OPEN optional parameters; 9687 send hold timer; 9774 AS_SET deprecation |
| 2328 OSPFv2 | 5709 HMAC-SHA authentication; 6549 multi-instance; 6845 hybrid broadcast/P2MP interface type; 6860 hiding transit-only networks; 7474 security extension for manual keying; 8042 two-part metric; 9355 BFD strict mode; 9454 terminology update |
| 5340 OSPFv3 | 6845; 6860; 7503 autoconfiguration; 8362 LSA extensibility; 9454 |
| 4456, 4760, 1997 | 7606. RFC 1997 is also updated by 8642, the policy behaviour for well-known communities. |
| 5880 BFD | 7419 common intervals; 7880 S-BFD; 8562 multipoint; 9747 unaffiliated echo |
| 4364 L3VPN | 4577 OSPF as the PE/CE protocol; 4684 route target constraint; 5462 EXP renamed Traffic Class |

## 3. Errata

- **Verified**: accepted corrections. Apply them when you read the section they name.
- **Held for Document Update**: acknowledged but not applied. Mention them if they touch your quote.
- **Reported**: not yet reviewed. Do not rely on them.

`lookup_rfc` gives the errata counts and entries (with `section` and a summary).
RFC 7868 (EIGRP) has 12 verified errata, and RFC 2328 has 11.

## 4. Known RFC-vs-Cisco differences

1. **BGP, missing MED.** RFC 4271 §9.1.2.2(c) says "Routes that do not have the MULTI_EXIT_DISC attribute are
   considered to have the lowest possible MULTI_EXIT_DISC value" (rfc4271.txt:4491-4492). The IOS XE IP Routing
   guide instead says the IETF "assigns a value of infinity to the missing MED". It says Cisco's default of 0 differs
   from that, and offers `bgp bestpath med missing-as-worst` (iosxe17-rtr-iprouting-cg.txt:32994). The Cisco
   claim about the IETF contradicts RFC 4271 as published: Cisco's default of 0 actually matches it. Quote both.
2. **BGP, MED comparison scope.** Both RFC 4271 §9.1.2.2(c) (rfc4271.txt:4488-4490) and Cisco compare MED only
   between routes from the same neighbouring AS by default. Cisco adds `bgp always-compare-med` and `bgp
   deterministic-med` (iosxe17-rtr-iprouting-cg.txt:32993, :40423).
3. **BGP, steps that are Cisco-only.** WEIGHT comes first in Cisco's best-path order and is "a Cisco-specific
   parameter" (cisco-tn-bgp-bestpath.txt:29-30). RFC 4271 §9.1 has no weight. Use the TechNote for the IOS order.
4. **BGP, RFC 8212.** The RFC recommends that EBGP carry no routes without policies. IOS XE by default still
   propagates EBGP routes with no policy. `bgp safe-ebgp-policy`, available from 17.2.1 and so at the 17.9 pin,
   turns the RFC behaviour on (iosxe17-rtr-iprouting-cg.txt:33067, :33078). Cite RFC 8212 itself when the corpus
   has it (`lookup_rfc 8212`).
5. **OSPF, RFC1583Compatibility.** RFC 2328 §16.4.1 and App. C.1 define the flag (rfc2328.txt:8715-8742,
   :10941). IOS configures it with `compatible rfc1583`. Check the default for the process type (OSPFv2 or
   OSPFv3) and release in the corpus before you state it.
6. **OSPF, DD MTU check.** RFC 2328 §10.6 rejects a Database Description packet whose Interface MTU is larger than
   the receiver can accept (rfc2328.txt:4925-4928), so the neighbours stall in ExStart/Exchange. IOS turns the
   check off with `ip ospf mtu-ignore` (iosxe17-ip-addressing.txt:49374 lists it).
7. **OSPF NSSA.** RFC 3101 obsoletes 1587. The IOS guide has an "Example: OSPF NSSA Area with RFC 3101 Disabled
   and RFC 1587 Active" (`get_outline` on `iosxe17-rtr-iprouting-cg`), and forced translation with
   `area ... nssa translate type7 always` ("Configuring an NSSA ABR as a Forced NSSA LSA Translator").
   Translator election is RFC 3101 §3.1.
8. **EIGRP.** RFC 7868 is Informational. It documents Cisco's protocol, so the Cisco guides are authoritative for
   IOS XE defaults. Check its errata before quoting it.
9. **IS-IS.** The base specification, ISO/IEC 10589, is not an RFC and is not in the corpus. RFC 1195 covers only
   IP routing with IS-IS.

## 5. Reading RFC text

- BCP 14 (RFC 2119 and RFC 8174): MUST, SHOULD and MAY carry normative force only in capitals. RFCs published
  before 2119 (791, 1058 and others) use lowercase "must" informally.
- Page footers (`Murphy   Standards Track   [Page 16]`) and running headers (`RFC 3101   ...   January 2003`) can
  interrupt a sentence. `verify_quote` returns `normalized` for such a quote.
- Section numbering varies. RFC 3101 uses "3.0" for chapters, RFC 2328 numbers appendix sections "A.4.5", and
  RFC 791 centres its chapter headings. `get_outline` follows each style.
- A heading's section in `get_outline` includes its subsections. Read the subsection you need, not the whole
  chapter.
