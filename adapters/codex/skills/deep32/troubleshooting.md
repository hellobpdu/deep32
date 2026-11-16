# Troubleshooting mode

Use this for "why does/doesn't…", "stuck in…", "not translating/advertising/forming…" and "how do I verify…".

## Method

1. **Pin the symptom.** Note the protocol, the state or show output the user reports, the platform and the
   release. If one fact changes the whole answer, such as whether this is IOS XE or SD-WAN, ask once. Otherwise
   state your assumption and go on.
2. **Generate hypotheses** from the checklist below and from the protocol's state machine in the RFC. Keep only the
   ones that could produce this exact symptom. Search for each one in the documents' own terms
   (`${CODEX_HOME}/skills/deep32/query-planning.md` maps common symptoms to them).
3. **Find a documented check for every hypothesis you keep.** That is a show or debug command plus the output line
   that proves or rules it out. Use `find_command` and `grep_corpus`. Show-output examples in the Cisco guides are
   quotable evidence.
4. **Rank** by how well each hypothesis fits the symptom, then by how common it is. A hypothesis with no documented
   check goes last and is marked "no documented check".
5. **Give a verification sequence**: the commands in the order that splits the hypotheses fastest.

## Answer shape

```
**Most likely causes**
1. <cause>: why it fits the symptom. Check `show …`, which shows "<quoted output line>" when this is the cause
   [cite] (verified · file:line)
2. …

**Verification sequence.** 1) `show …` rules out 1 and 3; 2) …

**Evidence / RFC vs Cisco / Confidence and gaps / Read** (as in citation-style.md)
```

## Hypothesis checklists

These are seeds for searching, **not evidence**. Cite the corpus for every item you use.

**OSPF adjacency**
- area id, hello/dead interval, subnet or mask, authentication type or key
- stub/NSSA option-bit mismatch
- network type mismatch (DR expectations)
- MTU mismatch, which stalls the neighbour in ExStart/Exchange (RFC 2328 §10.6)
- duplicate router ID
- passive interface; an ACL blocking protocol 89 or 224.0.0.5/6
- note: 2-WAY between DROTHERs is normal

**OSPF missing routes**
- area type filtering: stub areas drop Type-5
- NSSA translation: translator election, P-bit, forwarding address 0.0.0.0, `not-advertise` ranges (RFC 3101 §3.1-3.2)
- a forwarding address that is unreachable
- a distribute-list, which filters the RIB and not the LSDB
- summarisation
- a discontiguous area 0 that needs a virtual link
- RFC1583Compatibility preference rules

**EIGRP**
- AS number, K values, authentication, passive, primary-subnet mismatch
- stub settings; split horizon on a DMVPN hub
- feasibility condition and feasible successors; `variance`
- stuck-in-active and query scope
- distribute-lists and offset-lists; named versus classic mode

**BGP**
- TCP 179 reachability, update-source, ebgp-multihop or TTL security
- remote-as, router ID, MD5 password
- neighbor not activated in the address family
- next hop unreachable (next-hop-self)
- iBGP split horizon: needs a full mesh or a route reflector
- AS_PATH loop (allowas-in)
- inbound or outbound policy, maximum-prefix
- `bgp safe-ebgp-policy` (RFC 8212 behaviour)
- the best-path step that decides the winner

**Redistribution**
- seed metric (the default can be unreachable)
- the OSPF external type and metric
- tags and route maps to stop feedback loops
- administrative distance causing suboptimal routes or loops
- `match` clauses

**DMVPN, NHRP and IPsec**
- tunnel source/destination and mode
- NHRP network-id, authentication, NHS registration
- phase behaviour: next hop, redirects and shortcuts
- IKE policy (encryption, hash, DH group, authentication), pre-shared key, transform set
- NAT-T (UDP 4500); MTU and `adjust-mss`

**Multicast**
- RPF failure
- RP discovery (static, Auto-RP or BSR) and group-to-RP mapping
- PIM neighbours and DR
- IGMP membership, TTL, SPT switchover, SSM ranges

**Services and security**
- FHRP: group, priority, preempt, tracking
- NAT: inside/outside, ACL, overload, order of operations
- DHCP: relay helper, option 82, snooping trust
- uRPF: strict versus loose
- ACL: implicit deny and direction
- CoPP drops

**AAA, 802.1X and ISE** (ISE 3.1 docs: `ise31-admin-*`, `ise31-cli`; switch side: `c9300-179-sec`)
- reachability: RADIUS 1812/1813, TACACS+ TCP 49, source interface and VRF; is the NAD defined in ISE with the
  right shared secret (Message-Authenticator and KeyWrap settings)?
- method lists: `aaa new-model`, dot1x authentication, network authorization, accounting, and `aaa server radius
  dynamic-author` for CoA (Cisco NADs use UDP 1700, the RFC default is 3799)
- dead servers: `radius-server dead-criteria` and `deadtime`, and critical authentication when every server is dead
- port: `dot1x pae authenticator`, port-control auto (or the IBNS 2.0 control policy), host mode (multi-auth vs
  multi-domain for voice), MAB order and priority, device tracking (needed for dACLs)
- ISE decision: which policy set, authentication rule and allowed protocols (EAP method), identity source sequence,
  and authorization profile matched; read the Live Log steps and failure reason
- EAP: server certificate trust on the supplicant, machine vs user authentication (MAR, TEAP/EAP chaining)
- results not applied: VLAN missing on the switch, dACL download, redirect ACL and `ip http server` for CWA/posture,
  CoA not accepted; profiling probes and device sensor; TrustSec PAC, environment data, SGACL download, SXP state
- verify: `show access-session interface <if> details`, `show aaa servers`, `test aaa group`; ISE RADIUS Live Logs

**SD-WAN**
- control connections: certificates, organisation name, Validator reachability, DTLS/TLS
- TLOC or colour restrictions
- centralized policy direction and application
- template versus configuration-group attachment on 20.9

## Rules

- Never recommend a disruptive command (`clear`, `reload`, `debug all`) without saying so in the same line.
- A debug command's output is evidence only when the corpus shows a sample of it.
- If the documented check exists only in a newer release's book, say so (release-drift.md).
