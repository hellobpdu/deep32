# Workstation and Forge observations

These J06/J07 modules extend the read-only core in `CORE.md`. They supply role
policy and closed platform observation boundaries. J09's `doctor` and `cli`
modules now provide ordered role aggregation and fixed reports; the installed
native helper remains an owner input. Use Python 3.11 or later with `-I -B`.
`DOCTOR.md` describes launcher binding and the unqualified default behaviour.

`workstation.workstation_checks` consumes the validated installation receipt,
typed engine and gateway status, committed import state, independently
authenticated content expectations, protected endpoint authentication and a
running forwarder's attestation snapshot. `project_engine_status` reuses the
shared status parser; `project_import_state` requires the existing importer's
pure registry validator. No missing registry is replaced with an empty default.
The OpenBSD boundary accepts only its fixed `Operation` enum. It cannot accept
an executable, path, URL, service name or repair action.

`forge.forge_rows` consumes the validated Forge receipt, independently
authenticated `RuntimePolicy`, reviewed pure `HostRules` and `ForgeObservation`.
`runtime_policy_from_pins` selects applicable core/add-on/platform floors without
choosing another GPU class. Linux and Windows boundaries accept only `ForgeRead`
operations and share the core Budget. Windows requires the recorded Desktop
Linux-engine pipe; protected Windows pairing remains FAIL until its separately
qualified native capability is supplied. The backing-path checker consumes
passive exact-ancestor facts and cannot inspect or repair a path itself.

Common `probes` functions provide separate Tailscale presence, daemon, sign-in,
DNS, certificate, publication, pairing, first-start, peer reachability and
per-peer tailnet path rows. Pass explicit approved executable presence, expiry
telemetry and fresh durable-marker evidence. Missing observations remain WARN
or FAIL. Pairing needs independent protected identity, committed claim and
handover evidence; records or a ready C6 line alone cannot establish it. Serve
checks compare recorded owned routes with a live projection and never change
foreign or owned routes. Peer aliases are role names with optional numeric
suffixes; private peer keys and addresses stay inside observations.

`peer_time.observe_clock` takes exactly three qualified, bounded, no-retry
samples for an already enrolled peer. Each sample binds local wall/monotonic
send/receive times to the peer's origin UTC millisecond header and a fresh
echoed 32-hex nonce. It rejects missing/cache/nonce/origin proof and clock jumps.
Its narrowest interval includes a millisecond allowance: inside +/-1 second is
OK, wholly outside +/-5 seconds FAIL, otherwise WARN. Unreachable peers SKIP
only behind an explicit reachability failure. Native sampling must use the
same Budget and ten-second group deadline; this pure policy supplies no socket.

`storage.storage_rows` groups public `StorageBudget` aliases by independently
observed filesystem identity. Distinct reservations on the same filesystem are
summed and its free bytes are charged once. `next_bytes` is the full required
running/import/rollback budget, not an increment to add again. Running reserve
failure is FAIL; only next-operation shortage is WARN. Desktop internal space
and its host backing volume remain independently measured domains. No path or
native capacity is inferred by this module.

Headend `headend_rows` supplies selected-profile, owned connector, agent and
structured gateway checks. Shared headend DNS/HTTPS policy requires the
selected publisher's resolution and TLS without requiring a certificate for
the client itself. Headend Serve and first-start rows explicitly SKIP.

Report policies select only fixed D2 sentences and source anchors. Raw error,
command, HTTP, settings and account text cannot become detail or fix text.
Retained Forge hook names use the validated report platform.
The report renderer supplies the fixed
table, numbered fixes and summary from the same sealed rows used by JSON.

## Running-service additions

`Attestor.diagnostic_snapshot` copies already completed identity checks and
their timestamp atomically. It performs no refresh, settings read or inference.
Identity fields are captured from existing authenticated health/info reads and
committed together with their check result; failure/invalidation cannot create
a healthy snapshot. The getter excludes hub, raw reason, endpoints and secrets.
The installed owner still needs a qualified protected local RPC for this getter.

Reranker health reports device kind from the loaded parameter devices. Its
resolved configuration revision check is only `revision_pin_verified`. Matching
configuration never asserts a verified weight/file measurement. A mismatch is
an explicit failed measurement; missing independent model-closure verification
remains unmeasured. The upstream embedder still needs its own qualified
service-origin device and measured-identity observation. Do not derive this
from Docker GPU configuration or a requested model revision.

The existing Forge `/info` door and Workstation `/mcp` boundary generate
`X-Deep32-Time` and echo one bounded `X-Deep32-Nonce`, including authentication
refusal. They set no-store and never forward backend clock proof. No route,
permission or credential is added. A diagnostic observer must independently
qualify TLS, peer origin, header preservation through Serve and absence of
caching. A header alone is not that qualification.

## Required owner inputs

The installer/importer must supply the frozen shared receipt and atomic,
authenticated generation/fingerprint/count/budget bindings. Packaging must
supply selected-class installed inventory, immutable images/model closure,
startup allowance and active-service job journals. Those inputs must come from
their owning transactions, not from a doctor repair or an observed response.

Native owners must qualify exact-slot confinement, no-create protected reads,
OpenBSD doas/pledge/unveil and local sockets, passive Linux/Windows process and
listener identity, ACL/DPAPI getters, bounded command/HTTPS cancellation and
Desktop capacity. Callback construction and synthetic tests cannot grant this
qualification. Default platform boundaries refuse observations. No native
helper, process-tree cancellation, actual GPU operation, rebuilt media,
protected Windows console pairing or release acceptance is established here.

Rebuild the changed door and reranker images, gateway/forwarder runtimes and
any optional full image containing those sources before native qualification.
Preserve existing model revisions, numerical behaviour, payload selection,
previous images and all existing release gates.
