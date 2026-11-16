# C8. Network

Contract C8, version 1, draft of 2026-11-14. Not frozen. Node names, ports, tokens, discovery, pairing and the
transport switch. They are the same whether the parts share one machine or sit on several. Written by
the network stage (stage 7) and `deep32-connect` (stage 8). Read by every node, the installer and the relay.

## Nodes and ports

Each node signs in with the person's own Tailscale account during install; no auth key exists anywhere. Names come
from the settings disc (C7) and carry nothing personal. A Forge in WSL2 rides on its Windows host's node.

| Port | Role | Listens on | Reached from the tailnet |
|---|---|---|---|
| 8765 | search engine | loopback (a UNIX socket on the Workstation) | never |
| 8766 | MCP gateway | loopback | Tailscale Serve, HTTPS 443: `https://<node>.<tailnet>.ts.net/mcp` |
| 8081 | embeddings server | Forge loopback | Serve, HTTPS 443, `/v1/embeddings` |
| 8082 | reranker | Forge loopback | through 8443 |
| 8443 | the reranker's published HTTPS port | the Forge's tailnet address (Serve) | Serve, HTTPS 8443, `/v1/rerank` |
| 7452 | relay (the original deployment's number; the installed copy gets its own, see Install identity) | headend loopback | a Tailscale TCP forwarder that `deep32-connect` creates |

This is the original deployment's published layout, and the one the Workstation's GPU link calls today.
Publishing is Tailscale Serve with HTTPS (decided). It needs MagicDNS and HTTPS certificates on in the person's
tailnet: the installer checks and names the switch that is off, and never changes tailnet settings itself. HTTPS
certificates put every machine name in the public Certificate Transparency log. The setting
`publish=tailnet-plain` uses plain tailnet addresses for those who decline. `verify` and the benchmark check
that paths between nodes are direct.

## Tokens and keys

Every token and key is made on its own node, at first boot or at pairing, and kept where only its service can read
it. A token is 32 random bytes written as 64 hex digits, as the laptop's installer makes them today (`openssl rand
-hex 32`). None is ever on a disc, on the settings disc, in a log or in a status line.

| Token or key | Made on | Used for |
|---|---|---|
| engine token | the Workstation or headless host | gateway to engine |
| MCP token, one per paired headend | the Workstation | agent to gateway (`Authorization: Bearer`) |
| Forge token | the Forge | engine to the Forge's one authenticated door for both models |
| Forge knowledge-base key | the Forge | signs the bundles it builds (C3); public half enrolled at pairing |
| link keys and certificate pins | each side of a link | D32U/1 mutual TLS (C9) |
| Ask password | set by the person at first use of the Ask window | second factor, stored as PBKDF2-SHA256, 600,000 rounds (C9) |

## Pairing: one procedure for all three pairings

Workstation with Forge, Workstation with relay, and headend with gateway all use pairing v2 as the relay ships it,
with one change: today the person types a password of their own into both sides; here the side being joined makes
a short one-time code and shows it. That side opens a window of five minutes and one attempt. The
other side types the code, or the installer types it after reading it through C6. SPAKE2 (RFC 9382, P-256) runs
over the code, with each side's TLS certificate hash as its identity. Three failed windows within 30 minutes lock
pairing for 15 minutes. On success each side stores the other's certificate pin; then, inside the authenticated
channel, the joined side hands over what the joiner needs: the Forge gives its token and knowledge-base public key;
the Workstation gives the headend its own MCP token; Workstation and relay keep the pins only. Removing a pairing
on either side revokes what it gave.

## Transport switch

`transport=tailnet` (default) or `transport=loopback`. In loopback every service is reached at 127.0.0.1 on its
loopback port, and sign-in, Serve and discovery are skipped. Tokens and pairing still apply, so every test runs on
one machine with no Tailscale.

## Name resolution on the Workstation

Today's laptop does it this way, and this version starts from it: `tailscale up --accept-dns=false` (no
MagicDNS); unbound on loopback refuses every name outside `tailscale.com.` and forwards that zone to the resolvers
of the DHCP lease (`d32-dnsup` writes them every 5 minutes); peers are reached by the tailnet address recorded at
pairing. Pairing writes each peer's address and tailnet name to `/etc/hosts`, so TLS name checks work without
MagicDNS. Publishing and name resolution on the Workstation are still new work (see the open points).

## Install identity

The distribution keeps the Deep32 name and the names on the wire (`D32U/1`, `skill:"deep32"`, the `$deep32` turn
prefix). Its relay gets its own install folder, task name, port, key container and credential names, and the
installers refuse to run where an existing production install is found. The other items that could
collide (the firewall rule, the terminal profile, fonts, the MCP entry name and its token variable) are not named
yet; stage 8 settles them against gate G13.

## Open points

- OPEN: discovery. Proposed: after sign-in, list the tailnet's peers with `tailscale status --json`, keep names that
  start `deep32-`, ask each for role and install id over an unauthenticated hello, and let the person pick when
  there are several. Decides: the builder in stage 7.
- OPEN: the relay's port, its other own names, and the colliding items not named yet (above). Decides: the
  builder in stage 8.
- OPEN: the PDF converter's service (port 8083 on loopback in production) has no row here or in the matrix (C1).
  Decides: the builder in stage 4.
- OPEN: the exact Tailscale forwarder command, and whether it survives a restart (how today's forwarder was set up
  is recorded nowhere). Decides: the builder, from a lab run in stage 8.
- OPEN: the Forge's door (one proxy for both models, or a token check in each). Decides: the builder in stage 4.
- OPEN: the code's length and alphabet, and a pairing v2 implementation for the Forge and for `deep32-connect`
  (today only the relay and the Workstation's link client have one). Decides: the builder in stages 7 and 8.
- OPEN: Serve and MagicDNS on OpenBSD are unconfirmed (a lab test still to run); the Workstation's TLS front
  today is relayd. Decides: the builder, from a lab run in stage 7.

Sources: `install/matrix.json` (`ports`).
