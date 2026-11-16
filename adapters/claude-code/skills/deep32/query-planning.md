# Query planning: ask in the documents' words

A diagnosis of retrieval misses found that most failures were plain-English questions that never used the
documents' own name for the feature: "traceroute hides the provider core" instead of `no mpls ip propagate-ttl`,
for example. Writing the `search_many` sub-queries in the documents' terms put the answer first in most of those
cases. This file adds to step 4 of the method; nothing else changes.

## Before search_many

1. Split the question into its parts. A multi-part question gets at least one query per part.
2. For each part, name the documented term: the Cisco or RFC name of the feature, state, field, message or command.
   Use the seed table below, the checklists in `${CLAUDE_SKILL_DIR}/troubleshooting.md`, and `lookup_id` on every
   acronym. Where Cisco renamed a product, search both names: vManage / SD-WAN Manager, vSmart / SD-WAN Controller,
   vBond / SD-WAN Validator, DNA Center / Catalyst Center.

## The sub-queries (3 to 6, in one search_many)

- One `bm25` query per part, made only of documented terms and identifiers. Leave out pronouns, device names (R1,
  PE1), addresses and prose: they match nothing useful.
- One `dense` query that restates the problem the way the user described it.
- For troubleshooting, one `bm25` query per leading hypothesis, plus `<command> usage guidelines` and
  `<feature> restrictions`: answers often sit in usage notes and Restrictions lists.
- Then read the whole section behind the best hits with `get_section`. The answer often sits deep in a long chunk, or
  in the next one.

## Seed table

Seeds for searching, not evidence: cite the corpus for anything you use.

| The user says | Search for |
|---|---|
| the logs show nothing | system message logging, logging levels, `logging console`, `terminal monitor` |
| no debug output in my SSH session | vty, `terminal monitor` |
| the IP SLA target is a Linux server | IP SLA responder, non-Cisco device |
| both branches use the same AS across the MPLS cloud | AS_PATH loop prevention, `allowas-in`, `as-override` |
| the spokes don't learn each other's routes | split horizon on the hub, next-hop on the hub (DMVPN routing) |
| the spokes ping the hub but not each other | NHRP resolution, NHRP redirect and shortcut (DMVPN phase 2 and 3), spoke-to-spoke tunnel |
| VPNv4 routes arrive but are not in the VRF | route-target import, VPN-IPv4 |
| packets carry two labels across the backbone | label stack: VPN label and LDP (transport) label |
| traceroute hides the provider core | `no mpls ip propagate-ttl`, TTL propagation (pipe model) |
| prefer one hub, cap the tunnels, no instant fallback | NHS priority, NHS max-connections, NHS fallback |
| stop a rogue spoke from joining | NHRP authentication, tunnel key, IPsec profile |
| which pre-shared key is used | IKEv2 keyring lookup, peer identity |
| wrong pre-shared key: which exchange fails | IKE_AUTH, AUTHENTICATION_FAILED |
| the upstream router should filter before it sends | Outbound Route Filtering (ORF) |
| send one subnet to another next hop | policy-based routing, `set ip next-hop` |
| an area is cut off from area 0 | OSPF virtual link, transit area |
| run commands every week without EEM | Command Scheduler (`kron`) |
| out of the box | default configuration |
