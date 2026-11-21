# Before a real run

Use this checklist beside the approved runbook, [hardware requirements](HARDWARE.md),
[network contract](contracts/C8-network.md) and [agent instructions](../adapters/README.md).
It defines evidence to collect, rather than commands for an unqualified host. Source tests,
a generated VM recipe, copied skills and a local healthy status are separate checkpoints.
They do not qualify signed media, a host, remote publication or a real agent session.

Record each checkpoint as **NOT RUN, PASS, FAIL or BLOCKED**, with UTC time, source commit,
release and artefact hashes, actual version, exit code, duration and redacted expected/actual
result. Missing evidence is BLOCKED. A failed checkpoint stops dependent stages; continue
only independent work that the operator has authorised. A partial keyword result is useful,
but cannot pass meaning-search acceptance.

## Stop gates

Every row is mandatory for its applicable stage. An inapplicable gate needs an explicit
reason in the run record; it must not be silently marked PASS.

| Gate | Required evidence before the dependent stage | Missing or failed |
|---|---|---|
| PR-01 production | Private baseline, authorised window, existing pause/restore procedure, unchanged host identity and production configuration; successful original authenticated query before closing the window. | STOP |
| PR-02 topology | Approved Workstation/Forge entry and lifecycle; separate engine/gateway ownership, exact forwarding and rollback artefact; no full-role substitution. | STOP |
| PR-03 media | Pinned source commit, resolved pins, archive/image/model hashes, saved image identities, payload inventory and independently verified signatures; matching rebuilt runtime and saved door image. | STOP |
| PR-04 host | Actual platform/runtime/driver/GPU/class/VRAM and highest resolved signed driver floor; Docker Linux engine; approved Desktop isolation or Linux persistence recipe. | STOP |
| PR-05 storage | Measured per-filesystem simultaneous peak bytes, actual free bytes and stable private paths for downloads, joined images, payload, Docker store, VM, content/index and rollback. | STOP |
| PR-06 VM | Approved resources/headroom, DHCP network, firmware/NIC/disk mapping, empty target identity, target boot and separately verified overlay/restore checkpoint. | STOP |
| PR-07 publication | Actual OpenBSD Serve capability/version, MagicDNS/HTTPS and Certificate Transparency consent, owned path-preserving routes, TLS checks, anonymous denial and authenticated eleven-tool access. | STOP |
| PR-08 pairing | Both models authenticated and warm, measured host/VM UTC clocks and certificate validity, prepared protected consoles, one current window and preserved lockout state. | STOP |
| PR-09 agents | Supported isolated client/user arrangement, intact production markers, distinct enrolled headends and scoped launchers, actual signed-in client versions. | STOP |
| PR-10 session | Actual endpoint and content generation, separate signed model attestation, keyword then dense/hybrid retrieval, actual section and passing verify_quote in each real agent. | STOP |
| PR-11 persistence | Private fixed-path/media/startup recipe, owned stop/resume, daemon/guest/host restart evidence, same identities/generation/models and repeated authenticated retrieval. | STOP |
| PR-12 close | Owned test services stopped as intended, data retained, previous workloads restored, original production query/models/identity healthy and explicit window-close acknowledgement. | STOP |

## Production boundaries (PR-01, PR-12)

The operator supplies the existing private baseline and recovery procedure. This checklist
does not authorise production commands, installations, host feature changes, driver changes,
restarts or GPU allocation. Open the approved GPU window immediately before GPU work;
resource-approved CPU-only preparation need not keep production paused. Remote GPU testing
does not authorise local GPU use or local Docker Desktop reconfiguration.

Keep the existing production distribution and every host Tailscale setting, identity and
route unchanged. Never invoke WSL to inspect or repair production. Docker Desktop integration
for the production distribution must remain disabled; automatic/default integration must not
reach it. If Desktop isolation cannot be preserved, stop. Never import, convert, reset,
unregister or launch that distribution, or issue a global WSL shutdown.

Stop only owned test services and routes. Never prune Docker, stop all containers, remove
shared images, delete shared data or reset an identity to obtain a clean result. After any
approved pause, the original authenticated production query, generation and both model paths
must pass again. A restored process alone is insufficient. Keep the window open if restoration
fails, stop further testing and use the operator's existing recovery procedure.

## Topology and build handoff (PR-02, PR-03)

The requested Workstation-plus-Forge topology keeps content, the live index, engine and MCP
gateway on the Workstation. Forge is the default GPU-host role for J1, J2 and J4 on Linux
Docker or Windows Docker Desktop; embeddings, reranker and their authenticated door run by
default, with separate conversion/index-builder job profiles. The portable lifecycle is
supplied in source, but rebuilt signed media, host qualification and reviewed remote
pairing/model transport remain prerequisites. Do not count an additional full Docker engine/gateway as the
same run. J3 explicitly selects full/headless; J5 has no GPU pack. The optional full role
requires its own qualified host publication adapter; local full-role health alone does not
prove external readiness. Linux Forge retains its bounded local C8 listener and protected
console after authenticated model warm-up, with durable commit checks and persistent
lockout. Windows still needs its qualified protected host-console adapter and opens no
code/window. A local-ready result never proves remote pairing reachability or publication.

Source changes do not modify previously published media. Require a build receipt binding
the source commit and build recipe to resolved, measured pins, payload inventory, archive
hashes, immutable saved image identities and actual model revisions/probes. Rebuild changed
application code into its saved images, including the door, and build the matching runtime
archive from the same sealed stage. Runtime-only archives do not contain the model/image
payload. Unresolved pins, missing images and mismatched inventories are stop conditions.

The release owner supplies actual media/checksum signatures and the independently confirmed
public-key fingerprint. A checksum alone is not signature verification. Preserve the exact
verification result and supported entry point. Build acquisition, assembly, qualification and
signing belong to their separately authorised owners. Do not invent pins, sign media, reuse
old image archives with new runtime code or treat an unsigned branch as a signed release.
An overlay trial and a fresh signed disc-1 trial need distinct result labels.

## Owning-host evidence (PR-04, PR-11)

For Windows, qualify Docker Desktop's Linux engine, WSL 2 backend, GPU support, actual
Windows driver and private NTFS bind permissions while preserving the production boundary.
For Linux, record actual driver/runtime or CDI, Docker/Compose and any approved integration
component versions and provenance. The product uses one generic Linux Docker form; host
integration and storage instructions must be reviewed for the actual host. Do not install a
guessed package/plugin, pull a sample image or enable a CPU fallback to make a check pass.

Measure the actual GPU, compute class, VRAM, selected image identity and model revisions.
Use the maximum of all resolved signed release driver floors. A card label or a fixture's
claimed VRAM is not a measurement, and a version minimum is not a support qualification.
Record wrong-engine, unknown-class, unresolved-floor and permission failures as blockers.

Require a reviewed persistence recipe for every Forge identity, private state, runtime,
payload and inventory path used by the selected role, plus mount readiness and startup order.
Both Docker roles now use a dedicated private data root and protected settings staging;
the recorded GPU media must remain available. A data-root option is not a substitute for
the reviewed persistence recipe. Record private owner/mode or ACL checks without publishing private
paths. A generated recipe does not prove daemon or host restart behaviour. Preserve data and
identity on failure; obtain missing host instructions instead of inventing boot scripts.

## Measure each backing filesystem (PR-05)

The release owner supplies a measured envelope; the operator maps each allocation to the
actual backing filesystem, including Docker's own image store. Record path roles privately
and share only neutral filesystem labels. For each phase, sum simultaneous allocations on
the same filesystem, including allocations that remain from previous phases. Compare the
peak with actual free bytes before download, image load and content import; record measurement
time and margin. Do not double-count hard links as independent copies, and do not assume
that different path names refer to different filesystems.

| Allocation to account for | Evidence required |
|---|---|
| Download parts and joined ISO | Actual retained parts, joined bytes and cache/retry copies |
| Runtime and payload | Extracted/copy peak and retained read-only mounted media |
| Container images and state | Actual Docker backing store, loaded image expansion, private state and rollback |
| VM installer and target | Installer bytes, separate target growth, checkpoints and optical media |
| Imported content and index | Incoming/staging bytes, active generation, index build and previous generation |
| Upgrade and recovery | Retained signed payload/images, backups and rollback generations |

Space for the disc files alone is insufficient. An unknown or unmeasured allocation is a
blocker, including the optional full role's content/index allowance. Add approved private
capacity or choose a newly reviewed target. Never delete production, shared or rollback
data to force a pass.

## VM install and overlay receipt (PR-06)

Approve actual available RAM/CPU alongside existing workloads; installed RAM does not prove
headroom. A small-host memory exception is specific to its approved Hyper-V recipe and does
not qualify a reduced-memory KVM recipe. Postpone if production or other workloads would be
starved. Record the actual DHCP-capable virtual network and wired NIC; do not guess static
addresses or change production routes to repair a guest.

Review the generated recipe before creation. Hyper-V uses Generation 1 and a separate empty
target, distinct from the installer. The generic KVM recipe uses BIOS, IDE and e1000; an actual
VM-manager mapping and boot result are still required. Do not convert a Hyper-V VHD into the
KVM installer by renaming it, guess alternate firmware/NIC/storage, overwrite an existing VM
or select a host disk. Confirm installer shutdown, detach only the recorded installer while
off, attach verified content as read-only optical media, then prove the installed target boots.

Verify content signatures and the actual active generation/import report. Preserve the
previous generation after interruption. Before overlay installation, record the actual ISO
hash/provenance, installer receipt and approved disposable-VM checkpoint. The current overlay
trial expects 30 installed files: compare actual installed hashes and modes with its approved
manifest, retain backups, reboot and verify services. A matching source manifest alone does
not prove installed bytes, permissions or service behaviour. On failure restore only the
recorded test checkpoint or a reviewed backup procedure; do not guess a bulk reverse copy.

## Publication, pairing and clocks (PR-07, PR-08)

Qualify the actual installed OpenBSD Tailscale package and required background, HTTPS,
path-preserving, TCP and JSON capabilities before agent enrolment. Record the exact unsupported
command or route. Require the VM's own signed-in identity, actual DNS name, MagicDNS/HTTPS
prerequisites and explicit Certificate Transparency consent. Administrative settings are
operator decisions. Preserve unrelated routes, refuse conflicts and verify owned-route reboot
resume. Do not enable Funnel, substitute a proxy, disable TLS verification or reset all Serve
routes. A sign-in success does not prove publication.

Require reviewed Forge pairing reachability, authenticated model HTTPS routes, destination
and firewall/ACL linkage, ownership and rollback. Pairing metadata does not populate model
pins or approve firewall changes. A VM's loopback is not its host's loopback even on shared
hardware. Keep raw model, engine and administrative paths private. Missing transport is a
release blocker; this checklist supplies no guessed forwarding or firewall commands.

Warm and authenticate **both models before opening the pairing window**. Compare host and VM
UTC clocks, Windows UTC where applicable, and certificate validity against the approved time
source. Record observed skew; an unknown or unacceptable skew blocks pairing/TLS work. Time
correction requires approval for the test node; never adjust a production clock automatically
or bypass TLS. Prepare both protected consoles before opening one new window.

A pairing window lasts five minutes and permits one attempt. Three failed windows within
thirty minutes cause a fifteen-minute lockout. Enter the current code only at the protected
prompt; never reuse an expired code, clear lock files or reset identities. Diagnose a failed
window before reopening through the owned resume action after expiry/lockout. Keep codes,
credentials, sign-in URLs and private peer records out of evidence.

## Isolated agents and real first session (PR-09, PR-10)

Use a supported isolated user/client arrangement approved before Connect. Changing only an
environment-selected home is not evidence of isolation. Preserve original-install markers,
production client configuration and private files. Never remove a marker or spoof a home to
bypass refusal. Require distinct headends, scoped server identities and the private launcher
printed by protected enrolment; never copy tokens into configuration or a transcript.

For each actual signed-in agent, use its scoped endpoint and installed skill. Record the
client version, expected Workstation endpoint and these eleven tools: `rag_status`,
`search_rag`, `search_many`, `get_section`, `get_outline`, `lookup_id`, `lookup_rfc`,
`find_command`, `grep_corpus`, `list_documents`, `verify_quote`. Refuse anonymous and bad-token
access; unsupported HTTP credential substitution is a stop without a token-literal fallback.

Require the following independent observations; an enrolment/configuration check alone is
insufficient:

1. `rag_status` reports the expected content generation, healthy keyword search and enabled,
   healthy dense/reranker backends with no degradation. Model IDs/revisions come from the
   separate authenticated model attestation, compared with signed release pins; do not assume
   that `rag_status` exposes those fields.
2. Run a public exact-identifier keyword query with reranking off, fetch an actual returned
   hit through `get_section` and pass `verify_quote` on a short exact passage. Record actual
   section ID and citation, rather than an expected ranking or invented result.
3. Run a public paraphrase through dense search, then hybrid with reranking on. Record actual
   backend health, timings, citations and absence of degradation. Keyword-only success remains
   a separate partial checkpoint.
4. Close and relaunch each agent through its private launcher, then repeat status, retrieval
   and quote verification. Revoke only the selected headend through the reviewed operation:
   it must lose access while the other enrolled agent still works. Use fresh protected
   enrolment if that headend is needed again; never reset every identity.

After approved owned stop/resume and daemon, guest and host restarts, repeat the model,
generation, identity, route and authenticated query checks. Open separate restart windows
where unrelated workloads require approval. Close the run only after PR-12 passes.

## Evidence handling

Keep the exact machine/identity/storage map and original private production procedure in the
local operator record. Share redacted checkpoint facts and public document/section identifiers.
Never include tokens, auth headers, pairing codes, sign-in links, private configuration, raw
container logs or process environments. Record failed gates honestly; source fixture evidence
and this offline checklist leave real-host qualification **NOT RUN**.
