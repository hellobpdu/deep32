# C8. Network

Contract C8, version 2, draft of 2026-11-17. Not frozen. Node names, ports, tokens, discovery,
pairing and the transport switch. The source supplies `deep32-pair`, Workstation enrollment
and publication, the headend connector and the Forge host lifecycle; supported transport and publication choices
depend on the form. Read by every node, the installer and the relay.

Changed: node identity ownership, configurable Forge ports, publication limits, separate Ask and enrollment pairing, discovery and converter decisions; relay policy qualification retained.

## Nodes and ports

The Workstation starts interactive Tailscale sign-in with its C7 name; no auth key is supplied on an image or settings
disc. Docker Forge and the legacy WSL interface check the owning host's existing sign-in and
actual name without logging in or renaming it; the legacy WSL interface uses its Windows host's node. Names must carry nothing
personal.

| Port | Role | Listens on | Reached from the tailnet |
|---|---|---|---|
| 8765 | search engine | loopback (a UNIX socket on the Workstation) | never |
| 8766 | MCP gateway | loopback | Tailscale Serve, HTTPS 443: `https://<node>.<tailnet>.ts.net/mcp` |
| 8081 | embeddings server | Forge loopback | Serve, HTTPS 443, `/v1/embeddings` |
| 8082 | reranker | Forge loopback | through 8443 |
| 8443 | the reranker's published HTTPS port | the Forge's tailnet address (Serve) | Serve, HTTPS 8443, `/v1/rerank` |
| 7452 | relay; fixed in alpha.2 because the Workstation's pf permits only this relay port | headend loopback | a Tailscale TCP forwarder that `deep32-connect` creates |

These are default ports; C7 `forge_ports` can change the Docker Forge's local and published ports, and authenticated
pairing hands over its actual model URLs. Publishing uses Tailscale Serve with HTTPS; the
Workstation publisher checks MagicDNS and HTTPS certificate eligibility and names the missing prerequisite,
without changing tailnet account settings. HTTPS certificates put every machine name in the
public Certificate Transparency log. Readers and the pairing core recognize
`publish=tailnet-plain`, but clone-run and Docker Forge publication require `serve-https`; declining disclosure
leaves publication off. Discovery and diagnostics report a direct, relayed or unknown peer
path; that observation does not qualify a complete inter-node benchmark.

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
| Ask password | planned for beta; absent from alpha.2 | planned second factor (C9) |

The Ask password is planned for beta and is absent from alpha.2. Relay acceptance and the other relay-only requirements remain
pending T-0226 under ruling R7.

### Shared Workstation credential parent (D03 amendment, 2026-11-17)

The design's general 0700 rule for private state directories has one narrow
exception: `/etc/d32/private` is owned by `root:wheel` with mode 0711. The
`_d32gpu` forwarder and `_d32edge` gateway must both traverse this shared parent
after dropping privilege; 0700 would prevent them opening their own files.
Neither service can list or write the directory. The Forge credential
`forge.token` remains `root:_d32gpu` 0440; the MCP allow-list `mcp-tokens` remains
`root:_d32edge` 0440. Each service can read only its own credential. The
non-secret `publication-dns` file is also `root:_d32edge` 0440. Other private
state directories retain 0700. This amends the parent-directory wording of the
installation design; it changes neither credential ownership nor disclosure.

## Pairing: Forge and gateway enrollment

Workstation with Forge and headend with gateway use the source-bundled pairing-v2 core: the side being joined makes
a short one-time code and shows it. That side opens a window of five minutes and one attempt.
The joiner enters the code through a protected prompt; automatic installer transfer from C6 remains acceptance
work. SPAKE2 (RFC 9382, P-256) runs over the code, with each side's TLS certificate hash as its
identity. Three failed windows within 30 minutes lock pairing for 15 minutes.
On success each side stores the other's certificate pin; then, inside the authenticated channel, Forge gives its
token and knowledge-base public key, and the Workstation gives the headend its own MCP token.
Removing a pairing revokes what that side issued; Forge token rotation also requires a door restart and re-pairing
the other affected peers.

Workstation Asklink with relay uses the separate `research-v1` ten-word SAS ceremony in C9 and stores pins rather
than receiving the direct-MCP bearer token. Its shipped client does not implement this
SPAKE2 exchange or durable three-window lockout; relay policies remain pending T-0226.

## Transport switch

`transport=tailnet` (default) or `transport=loopback`. In loopback every service is reached at 127.0.0.1 on its
loopback port, and sign-in, Serve and discovery are skipped. Tokens and pairing still apply, so every test runs on
one machine with no Tailscale.

This loopback path belongs to the Workstation and pairing core; the Docker Forge lifecycle requires `tailnet`.

## Name resolution on the Workstation

Today's laptop does it this way, and this version starts from it: `tailscale up --accept-dns=false` (no
MagicDNS); unbound on loopback refuses every name outside `tailscale.com.` and forwards that zone to the resolvers
of the DHCP lease (`d32-dnsup` writes them every 5 minutes); peers are reached by the tailnet address recorded at
pairing. Pairing writes each peer's address and tailnet name to `/etc/hosts`, so TLS name checks work without
MagicDNS. The source publisher uses the observed tailnet address with the actual published DNS name as TLS SNI,
preserving this resolver configuration; real Workstation publication qualification remains open below.

## Install identity

The distribution keeps the Deep32 name and the names on the wire (`D32U/1`, `skill:"deep32"`, the `$deep32` turn
prefix). The relay uses fixed TCP port 7452 because the Workstation's pf permits only that relay port.
It keeps its own install folder, task name, key container and credential names.
Installers must refuse when port 7452 or its forwarding route is already taken, including by an existing production install. The other items that could
collide (the firewall rule, the terminal profile, fonts, the MCP entry name and its token variable) are not named
yet; stage 8 settles them against gate G13.

These relay-specific coexistence and naming requirements remain pending T-0226; the partial merged relay does
not establish their acceptance.

## Open points

- Discovery reads `tailscale status --json`, keeps host or DNS names starting `deep32-`, excludes expired or shared
  nodes, and sorts the candidates. It reports their path and probes one TLS HELLO for role,
  id and pin without sending a message; relay candidates are deliberately not probed. The
  caller selects the candidate.
- The relay port is 7452; its other names and the remaining colliding items above stay open for stage 8.
- The PDF converter is an opt-in conversion-job container with `network_mode: none`, not a loopback service on
  port 8083 or a publication target.
- Workstation publication uses `tailscale serve --bg --https=443 --set-path=/mcp http://127.0.0.1:8766/mcp` and
  `tailscale serve --bg --tcp=7454 tcp://127.0.0.1:7454`; Forge uses the same HTTPS/TCP options with C7-selected
  ports and loopback targets. Real OpenBSD behavior and restart persistence remain unverified;
  the relay's forwarder remains pending T-0226.
- One Forge door process listens on both model ports, checks the same bearer token on each, and proxies only the
  corresponding model, health and info paths to the private model containers.
- Pairing code profile: ten uppercase Base32 symbols, `[A-Z2-7]{10}`, normally shown in two groups of five.
  The source-bundled `deep32net` core implements Forge and gateway enrollment; the direct-MCP headend connector is
  implemented separately in `adapters/connect.py`, while relay acceptance remains pending T-0226.
  Pairing listeners use 7453 (Forge) and 7454 (gateway), bound to loopback;
  a separate approved forwarder supplies tailnet reach. The relay remains on 7452.
- OPEN: Serve and MagicDNS on OpenBSD are unconfirmed (a lab test still to run); the Workstation's TLS front
  today is relayd. Decides: the builder, from a lab run in stage 7.

## Source publication for the next discs

The new Workstation source supplies `d32-publish publish --accept-ct`, `status`, `resume` and `unpublish`.
It checks the local signed-in identity, actual DNS name, MagicDNS and certificate domains before enabling the
existing loopback gateway. It owns only HTTPS 443 `/mcp` and TCP 7454 to the existing loopback headend pairing
listener. It refuses overlapping or foreign routes and Funnel, journals ownership before changing Serve, reads
the routes back, and checks unauthenticated denial plus authenticated access to the eleven tools. It never
changes a tailnet setting. The certificate-transparency notice is recorded once; agreement is explicit.

The published DNS name is separate from the pairing certificate pin. A protected non-secret DNS override lets
the gateway accept the actual published host without editing credential-bearing service settings. Boot resume
uses the recorded action; missing sign-in, expired identity or lost routes cannot produce a ready result.
Unpublish removes only the recorded routes and restores the previous loopback-front choice.

Needs rebuilt disc 1 and the OpenBSD VM acceptance checks before this is a supported publication command.

OpenBSD Serve command, route-path preservation, reboot persistence, resolver/firewall access and TLS support are
UNVERIFIED on a real Workstation. The source's offline fixtures are not that proof. Engine, administrative and
raw model paths are never publication targets. This Workstation action does not implement the separate full
Docker host publication adapter.

### Read-only gate before publication

Before the first `publish` or an interrupted `resume`, qualify the installed OpenBSD package at the VM console.
These commands inspect the VM's existing node and routes; keep their complete output in the private operator
record because it includes machine names, addresses and other tailnet members.

~~~sh
date -u
uname -a
/usr/local/bin/tailscale version
/usr/local/bin/tailscale serve --help
/usr/local/bin/tailscale serve status --json
/usr/local/bin/tailscale status --json
~~~

Require successful status commands returning JSON objects. The installed Serve help must advertise `--bg`,
`--https`, `--set-path` and `--tcp`; record the exact package/version and missing command or option on failure.
This is a manual prerequisite, not an automatic capability probe in `d32-publish`. Help and empty route tables
alone cannot prove path preservation or restart persistence. Do not start publication to discover whether a
required option exists. An unsupported build stays blocked for lead qualification; do not substitute a proxy,
enable Funnel, reset Serve or replace unrelated routes.

The publisher consumes the following local `status --json` observations. A required positive observation that
is off, absent or unknown fails the prerequisite; the program never turns on an account switch.

| Observation / refusal | Required operator action before retry |
|---|---|
| `BackendState` is `Running`; `Self.Online` is true; `Self.KeyExpired` and `Self.Expired` are not true / `signin-required` | Restore this VM's own signed-in node with `d32-link login`. Keep the host's identity and state separate. |
| `CurrentTailnet.MagicDNSEnabled` is true / `magicdns-disabled` | The person approves **Tailscale admin console > DNS > MagicDNS**. |
| `Self.DNSName` supplies the actual `.ts.net` name / `dns-name-unavailable` | Resolve the actual VM name; never invent the published URL or reuse another node's name. |
| `CertDomains` contains that DNS name / `https-disabled` | The person approves **Tailscale admin console > DNS > HTTPS Certificates** and the public Certificate Transparency disclosure of the machine name. |
| No Certificate Transparency agreement has been recorded / `ct-consent-required` | Leave publication off until the person explicitly agrees; only then use `d32-publish publish --accept-ct`. |

On a fresh installation, `d32-publish status` can report off without checking Tailscale; it is not this capability
gate. After the prerequisites and consent are settled, use the owned publication action. Interrupted owned
publication uses `d32-publish resume`. Require actual `/mcp` path preservation, unauthenticated denial,
authenticated eleven-tool access and unchanged unrelated routes, then repeat after guest restart. A published
result has `model_readiness: not-checked`; model and real-agent acceptance remain separate checks.

### Prepare the pairing window

Finish model warm-up and route qualification before opening a short pairing window. Forge first boot already
checks both authenticated loopback model doors, on 8081 and 8082, before starting pairing; health failure is a
reason to repair and resume the same installation, not to open another window. These local health checks do
not establish the separate remote Forge forwarder or signed model identity/attestation.

Prepare both protected consoles and compare `date -u` on the VM and Linux host. On a Windows participant use
`[DateTime]::UtcNow.ToString('o')` in PowerShell. If UTC or certificate validity is wrong, obtain the approved
test-node time-source/correction procedure before retrying; never adjust production time or bypass TLS checks.
The window uses a monotonic deadline, while durable failed-window lockout uses wall-clock timestamps, so a
restarted helper or a clock change is not a recovery procedure.

Open one new five-minute window only when the joiner is ready. Enter the current ten-symbol code once at the
protected prompt; keep codes, tokens and sign-in URLs out of argv, operator-created files, shared logs and reports. Diagnose an
expiry or failed attempt before resuming the same installation for another window. Honor the fifteen-minute
lockout after three failed windows within thirty minutes; preserve identity, peer and lock files. Record only
the sanitized reason, UTC time, versions and stage result. For a successful pairing, still require durable peer
records, both remote authenticated model checks and actual meaning retrieval; a displayed code or local health
result cannot close those gates.

Sources: `install/matrix.json` (`ports`); `src/workstation/distrib/site/usr/local/d32/publication.py`
(`identity`, `Platform`, `Publication`);
`src/workstation/distrib/site/usr/local/d32/deep32net/window.py`;
`src/workstation/distrib/site/usr/local/d32/deep32net/pairlock.py`;
`src/gpu-pack/firstboot/firstboot.py` (`signin`, `start_models`, `boot`);
`src/gpu-pack/forms/host-requirements.py` (`tailscale_status`);
`src/gpu-pack/forms/forge_publication.py` (`ports`, `plan`, `endpoints`);
`src/gpu-pack/forms/forge_docker_role.py` (`settings`);
`src/gpu-pack/containers/door/door.py`; `src/gpu-pack/containers/compose.yaml`;
`src/workstation/distrib/site/usr/local/d32/deep32net/discovery.py`;
`src/workstation/distrib/site/usr/local/d32/deep32net/pairing.py`;
`src/workstation/distrib/site/usr/local/d32/deep32net/spake2.py`;
`src/workstation/distrib/site/usr/local/d32/deep32net/cli.py`;
`src/workstation/distrib/site/usr/local/d32/deep32net/enrol.py`;
`src/workstation/node/asklink/src/msg.c`; `src/workstation/node/asklink/src/link.c`;
`src/workstation/node/asklink/src/sas.c`; `src/workstation/node/asklink/src/ident.c`;
`src/workstation/distrib/site/usr/local/d32/sbin/d32-pairdesk`; `adapters/connect.py`;
`src/release-tools/deep32_settings.py` (`validate_settings`).
