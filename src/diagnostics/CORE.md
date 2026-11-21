# Read-only diagnostics core

This package supplies the shared J05 building blocks and the J09 source
dispatcher for `deep32 doctor`. The command aggregates typed role checks and
renders the fixed text or JSON report. It never starts an agent or runs
selftest. `DOCTOR.md` records the source launchers and outstanding installed
runtime/observation bindings; no qualified installed command is claimed.

Use the installed Python with `-I -B`. The package requires Python 3.11 or later
and uses only the standard library. Importing it creates no state or cache.

## Boundaries

`runner.ExistingFiles` reads only exact existing regular-file slots, rejects
links and reparse points, caps reads and checks file identity before/after the
read. Protected reads require owner/mode checks on POSIX or an injected native
ACL getter on Windows. A final `unchanged` check preserves that protection.
Platform adapters still own confinement, sealed path/handle identity and native
ACL qualification. These checks are a change detector, not a filesystem lock.

`runner.ReadOnlyCommands` accepts enum operations and explicit installed
executables. Its templates allow only Tailscale status/Serve status, the recorded
local Docker endpoint's version/runtime configuration, owned-project Compose ps,
and selected owned-container image/state/health projections. It exposes no argv,
shell, PATH, environment or Go-template pass-through. Docker environment,
health logs, mounts and unrelated labels are never requested. No native command
backend ships here. The default refuses observations; a future installed
adapter must independently qualify its executable allowlist, read-only behaviour,
output draining, deadline and owned-process-tree cancellation.

Share one `runner.Budget` across a diagnostic run: 90 seconds total, with the
last five seconds reserved for reporting, ten seconds per tool/peer observation,
and at most four observations in flight. Its callback gate checks absolute
deadlines and output caps. An arbitrary callback cannot be forcibly interrupted
by Python; only a qualified interruptible native adapter may be supplied. The
MCP client also accepts the enclosing absolute deadline. Its role scheduler must
share the same four-observation limit across HTTPS and local operations.

`profile.read_selected_profile` reads one explicitly selected connector profile.
It verifies its owned configuration, launcher binding and credential slot before
returning a memory-only credential capability. It never searches ordinary agent
homes, launches an agent or calls Connect's mutating dispatcher. POSIX private
directories/files must have the expected owner and 0700/0600 modes. Windows uses
getter-only ACL observations and CurrentUser DPAPI decryption, never protection
or ACL repair. Native getters need installed-host qualification. A caller must
provide the independently sealed interpreter/connector origin and require
`launcher_origin_confirmed` before claiming the launcher is trusted.

`profile.parse_c6`, `read_peer_records` and `parse_publication` extract owner
formats without constructing mutating status/store/publication objects. C6
messages, codes, sign-in URLs, boot IDs and sequence numbers are discarded.
Peer records alone cannot establish committed pairing, public identity,
pairlock, revocation or handover agreement. Publication journals alone cannot
establish live route ownership; pending/removing intent is refused. Directory
enumeration is bounded and missing directories are never created.

`mcp.MCPClient` performs anonymous denial, initialise/initialised, session,
exact eleven-tool and structured rag_status exchanges through an enrolled HTTPS
endpoint capability. JSON and SSE responses share one ten-second operation
deadline and a four MiB response limit. There are no redirects, proxy inputs,
cookies, retries, reconnects or token arguments. The default HTTPS transport is
unqualified and refuses requests. A qualified transport must enforce DNS, TLS,
body and cleanup deadlines without persistent writes or unrestricted targets.
Responses and errors stay private until typed projection; credentials and their
derived values never become observations. Status observations are internal:
print public identities only after comparison with authenticated expectations.

`models.observe_attestation` projects a snapshot obtained from the already
running owner. It never constructs an attestor or invokes inference. The deployed
legacy snapshot lacks measured model/revision/device fields; those omissions
cannot yield OK. New measured fields require the separately qualified owning
service adapter. Staleness is not peer clock evidence. `models.tailnet_path`
implements the lead's passive CurAddr/Relay rule for exactly one enrolled peer;
it never pings, enumerates the tailnet or changes Tailscale.

`models.observed` narrows fields by check family, validates primitive types and
requires authenticated public identifiers for generation/fingerprint/model
fields. `report` supplies bounded semantic rows, dependency and summary checks,
exit precedence, one-document JSON and the fixed text table. `doctor` orders
the role policies without accepting observation prose.

## Trust and provisional receipt

`trust.ReleaseVerifier` never runs a whole installer/verifier script. Its only
possible verifier request is the existing direct gpgv template, explicit sealed
public keyring and fixed existing read-only home, with status captured in memory.
The installed adapter must supply independently measured executable/keyring
hashes and a native denied-write/owned-process-tree qualification. No such native
qualification is established by synthetic tests. Without it verification fails
closed. It accepts exactly one GOODSIG and VALIDSIG, the pinned primary key and
binary signature class 00, then checks the authenticated manifest's identity.
Only the inspected `deep32-release-manifest` format version 1 is accepted; any
future root contract requires an explicit frozen-schema integration.

`schemas/doctor-install.provisional.schema.json` is explicitly provisional.
T-0203 owns the shared frozen receipt; the lead reconciles it in the merge.
`validate_receipt` binds role/platform, alpha.2 release bytes and signer, compares
the entire inventory with independently authenticated pins/validated journals,
and compares content generation/fingerprint with the independent committed import
expectation. A receipt supplies no route, listener or file ownership authority.
No bearer, token hash, credential path, peer address or arbitrary URL belongs in
this public metadata. Credential references are fixed slot aliases only.

## Remaining inputs

The next jobs need the frozen shared receipt/root schemas; installer/importer
writers and authenticated inventory/content bindings; installed exact-slot
confinement; native read-only verifier/command/HTTPS/ACL/DPAPI qualifications;
owner public-identity/pairlock/live-route readers; and running service snapshot
interfaces for measured device/model identity and peer time. These are explicit
downstream inputs, not healthy-host claims. The existing Windows protected
pairing and full-host publication gaps remain open. No signing, release,
deployment or native host acceptance is performed by this core.
