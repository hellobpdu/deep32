# GPU pack integration boundaries

The source contracts are C3, C6, C7 and C8 version 1 drafts. The common GPU form
uses Linux Docker containers on Linux Docker or Windows Docker Desktop. It uses
Compose restart policies and has no host systemd dependency. The following
boundaries still need first-hand lab verification; this source does not qualify
a host or signed disc set.

Forge is the default role on Linux and Windows Docker Desktop. J1, J2 and J4
pair it with a Workstation, which owns content, the live index, engine and MCP
gateway. Forge starts embeddings, reranker and the authenticated door; conversion
and index-building jobs use separate Compose profiles. J3 explicitly selects
`--docker-role full` with C7 `profile=headless`; J5 has no GPU pack. These are
roles within the same Docker form.

## Settings and installation

For the common Docker form, both roles validate C7 and stage settings under the
chosen private `--data-root` before the lifecycle writes `config/settings.json`.
Forge takes no `--content`: import disc 2 on the Workstation. Full requires the
matching signed content media on its GPU host. The retained release kit and
recorded read-only GPU media path are checked again on start/resume; keep them
available to the daemon. Both hosts support owned start, stop, status, resume
and upgrade actions. Local health still requires a separately qualified host
publication/pairing adapter before remote use.

The separate ISO install path keeps its existing settings-disc contract below.

The release ISO is generic. Its bootstrap seed selects a separate ISO labelled
`CIDATA`, with exactly `settings.json`, `user-data` and `meta-data` at its root.
`render-settings.py` generates those files from C7 and an explicit exact disk
serial supplied separately at install time. No disk is chosen by size or ordering.
The bootstrap compares the supplied seed byte for byte with its own renderer
before replacing `/autoinstall.yaml`. No arbitrary late command can enter through
the settings disc. Missing or ambiguous settings stop before storage changes.

C7 version 1 cannot carry a login key. The local account uses `lock_passwd: true`
and no `password` field. Subiquity acceptance of this user-data-only account is
an open C7 lab gate. If it requires a password field, stop the ISO leg and return
the exact schema/installer error; never add a default password or stored hash.
An encrypted unattended install is refused because C7 cannot carry its secret.
Use console provisioning for that profile; do not silently turn encryption off.
For the ISO installer, absent `disk_encryption` means on and is refused too.
Both the renderer and settings-disc selector require an explicit JSON false
before this unencrypted unattended path can touch storage. Other forms do not
make a disk-encryption choice.

After installation the ISO form asks the person to run the account password
exchange on its console. No value is read by the kit, printed by C6 or logged.
The current `/dev/console` password exchange is a lab gate: verify a dedicated
VT or ordering before getty, with no competing console reader, and settle who
supplies the password on unattended boots before accepting the ISO leg. This
task leaves that console policy for the operator instead of claiming it resolved.
The common Docker form copies validated C7 settings into its private data root's
`config/settings.json`; the default is `/opt/deep32` on Linux and
`%LOCALAPPDATA%\Deep32` on Windows. `--data-root` overrides it.
Windows stages the settings with a user-only ACL before the same Python
provisioner drives Docker Desktop's Linux engine. It imports no distribution.
The host's existing accounts are untouched. Sign-in stays with the owning host;
the Docker form observes `tailscale status --json`.
It never starts the host's daemon or runs `tailscale up`; it keeps the host's
name and DNS preferences and reports `Self.HostName` in tailnet and ready C6 lines.

## Pairing helper ABI

`/opt/deep32-forge/pairing/bin/pairing-helper` and `deep32-pair` are bundled
runtime executables. Their `deep32net` package is beside `bin`, not in a fetched
payload helper. The explicit runtime inventory carries both launchers and every
package module in each form. The source and release inventories cover those
bytes; no external helper placeholder is treated as a valid pin. Subcommands:

- `host-signin`: the bundled core returns exit 64, `not-provided`. This is
  a legacy gate of the deprecated separate WSL-distribution form. The common
  Docker form observes the owning host; it creates no second node and copies
  no host credentials.
- `pair-forge`: read one JSON object from standard input, containing `code`,
  `expires_seconds=300`, `attempts=1`, `form`, `transport`, `publish`, `token_path`,
  `kb_public_path` and `state_dir`. Read the two credential files locally. The
  JSON is a pipe, never an argument, settings file, environment or log. Return zero
  only after C8 SPAKE2 P-256, TLS-certificate identity binding, durable peer pins
  and authenticated credential handover. Enforce one attempt per window and three
  failed windows in 30 minutes causing a 15-minute lockout, persisted across helper
  restarts. Check the five-minute deadline while handling requests. Optional `port`
  selects the local loopback pairing listener. Optional `model_endpoints` is exactly
  `embeddings` and `reranker`, each a path-free HTTPS base on the same tailnet DNS
  name. It is carried inside the authenticated handover and saved privately; the
  Workstation enrolment binds its DNS to the paired Forge before configuring
  model calls and `/info` attestation with the enrolled Forge bearer. An absent
  endpoint extension retains the legacy 443/8443 defaults.

The bundled core listens on loopback port 7453 and authenticates the C8 exchange.
It records `transport` and `publish` and hands over the Forge bearer and public
key. The core itself does not install a remote listener or Serve routes.
The common Docker lifecycle's host-only `forms/forge_publication.py` owns that
boundary: Linux invokes the host `tailscale`; Docker Desktop invokes the Windows
host `tailscale.exe`. C7 `forge_ports` selects the two local model ports, local
pairing port and the three published ports; `forge_accept_ct=true` records explicit
Certificate Transparency agreement. The host publishes HTTPS model/attestation
and TCP pairing routes only after coexistence checks. Durable confirmed additions
are removed only when their exact configuration still matches. An interrupted
add/remove with uncertain ownership fails closed for operator reconciliation.
No reset, tailnet-setting change, login or foreign-route replacement is used.
See INSTALL for the default/alternate settings and `d32-pairdesk -forge --port N`.
The Windows protected pairing-console adapter and actual remote end-to-end
pairing/model reachability remain real-host release gates; source tests prove no
such host result. The deprecated form's host-sign-in bridge remains separate.

First boot validates the core's `peers/forge-joined/*.json` records, local
certificate pin, Workstation role, transport/publication and handover metadata,
private file ownership/modes, and a completed `pairing/forge-joined.pairlock`.
The paths are relative to `/var/lib/deep32-forge/pairing`. A legacy `paired`
marker alone never skips enrolment. Exit zero without a valid durable record
fails. Incomplete or conflicting records stop safely for explicit repair.
Removal uses the core's `unpair` operation with the Forge token path, rotates
the bearer, and requires the reported door restart and other peers' repair.

## Local identities and C3

The Forge token is 32 random bytes encoded as 64 hex digits. The common Docker
roles retain it under the selected data root at `private/forge-token`, with
owner-only file permissions or the checked Windows ACL. The legacy first-boot
path remains `/var/lib/deep32-forge/private/forge-token`, mode 0600, under mode
0700 directories.
An Ed25519 knowledge-base signing key is created there in PEM form, with a public
half for enrolment. The C3 bundle signer/signature encoding is not frozen: the
builder must adapt this key or explicitly change that format before bundle signing
is accepted. This kit does not claim to sign or validate a C3 bundle.

The token is mounted as a read-only compose secret, shared by the door's two
listeners. The raw TEI and reranker services have no host port. The model bridge
is internal; only the authenticated door has a host-access network. Docker daemon
administrators remain trusted. No persistent token is passed in an environment.
The legacy first-boot path starts the loopback-only doors and models and waits for authenticated
health on both 8081 and 8082 before generating a code. Health bypasses proxies
and refuses redirects. The five-minute pairing window is not spent loading or
warming model services, and a peer can attest the running doors during enrolment.
The portable Forge lifecycle health-checks its local services. Linux then reuses
the same bounded local C8 caller with the retained kit, selected data root's
`pairing` state and protected C6 file at `status/status`, using the existing Linux
consoles. A valid durable joined-peer and completed lockout record skips a new
window. Otherwise the same five-minute/one-attempt window and persistent lockout
apply; resume retains that state and the code is cleared after failure or expiry.
Windows opens no code/window and still needs its qualified protected host-console
adapter. Both retain `local-ready-publication-required` with remote readiness
false: local enrolment does not publish remote pairing/model routes or qualify
their transport.

## C6 decisions

Status is the current state at `/run/deep32-forge/status`, replaced atomically,
mode 0600, under a mode 0700 directory. Sequence begins at one for each first-boot
service run with a fresh eight-hex boot identifier. All old optional fields are
discarded on each transition. Waiting states repeat at least every ten seconds;
changing expiry is a new state and sequence. The two devices are `/dev/console`
and `/dev/ttyS0`; absence of the serial device does not prevent screen/file output.
The kernel display console is not moved. The common Docker loader keeps helper output out of general logs. Docker's
status sidecar prints stage information with `pair` and `signin` stripped
from C6 lines. The deprecated distribution's systemd unit suppresses first-boot
stdout/stderr and its console reads the root-only status file. That sidecar source change requires a new saved
door image and matching pins before a rebuilt disc can claim the log filter.

`S4_FIRSTBOOT` is a provisional local error identifier. The source C6 contract
leaves C10 codes open, and C10 was outside this task's read set. The S9 integrator
must map the identifier to C10 before release. Serial writes, late attachment,
restart handling and removal of expired pairing fields need the build-day tests.

The first-boot TEI choice is in C6 `msg` and `TEI_CLASS` in `images.conf`.
`S4_TEI_CAPABILITY` is the provisional C6 failure for an unsupported/unknown
capability or a failed driver query with an NVIDIA card present. First boot keeps
that line rather than replacing it with the generic `S4_FIRSTBOOT`. See PINS.md
for the exact mapping, the no-card rule and the optional operator override. No
release defaults to the Turing image.

## Installed payload cache

The common Docker lifecycle retains the selected media path and verifies its
payload identity, pins and loaded image identities again on start/resume. Its
private data root holds configuration, identities, job state and versioned kits.
The cache description below applies to the legacy first-boot path.

The full inventory check runs on first boot and when PAYLOAD.json, pins.json,
the installed-inputs implementation, compose source or bundled pairing runtime
changes. A root-only
`/var/lib/deep32-forge/payload-verified.json` records that small-input identity.
`images-loaded.json` additionally binds the chosen class. A repeat boot inspects
the four selected Docker image IDs without re-loading their archives. Changed
payload/card selection or a missing image causes the required loads again.
Markers are written only after success, so an interrupted run retries safely.

This optimisation assumes an immutable installed payload and a matching inventory;
editing bytes while leaving PAYLOAD.json unchanged is outside that update protocol.
Remove the verification marker or run `installed-inputs.py verify` for a deliberate
full integrity check. Replace payload and inventory together on an authorised
update. No update or production operation is performed by this kit task.

## Converter and GPU ownership

The portable Docker Forge uses `forms/forge_jobs.py --data-root ROOT` with
`convert JOB pymupdf|production|mineru`, `recover`, `build` or `bundle` on both
Linux and Windows. It validates recorded media, immutable images, retained
Compose configuration and Forge identity. Explicit `recover` holds the
installation lock while validating the recovery record and repairing any missing
pinned image from its verified archive. Ordinary jobs refuse image loading.
Conversion input and output live below `ROOT/jobs/JOB`; builder arguments use
container paths below `/var/lib/deep32-forge`, backed by `ROOT/builder`.
Converter recovery is recorded in `ROOT/conversion-recovery.json`; it names
only the owned job and previously active model services. Recover that record
before another job. Conversion honours `ROOT/status/gpu-hold`, pauses both
models and restores only the recorded services after removing its owned
converter. Index building also honours the hold before starting model services.
A failed restore retains recovery state. The operator's GPU window
and the C3 signing compatibility gate above still apply.

The fixed-path legacy shell wrappers retain the following job contract.

No 8083 endpoint is added to C8. A local, separate AGPL job is the S4 choice for
the converter's otherwise open port row. `convert-job.sh` holds an exclusive lock,
refuses a local GPU hold, records the active model services, pauses both, runs one
networkless converter, removes its own job container before returning the GPU,
and restores only the services that were active. A failed restore retains the
record. A killed shell or reboot requires `recover-job.sh` before any new job.
The recovery record contains only the generated job identifier and service names.

The operator's GPU window remains an external prerequisite. This local hold file
does not replace a gaming or resource coordinator on another host. No production
arbiter, container or unit is addressed by these scripts.

The production PyMuPDF chain is `production-converter/` (AGPL-3.0-or-later because PyMuPDF and MuPDF are AGPL): the
conversion and fetch scripts under `chain/`, their file list and hashes in
`CHAIN.json`, and `adapter.py`. Its job interface is `adapter.py convert --input
SRC --output DIR --job JOB.json [--lexicon DIR]`, where SRC is input.pdf,
input.html, input.epub or input.mib.json and DIR receives text.txt and result.json;
it uses only local files. `adapter.py fetch` downloads one catalogue entry and runs
on the Forge host, never in the networkless container. A production job exits 0,
or 10 plus the adapter's exit code. The portable baseline writes markdown and a page source map;
it rejects image-only input instead of accepting an empty extraction. MinerU uses
the inventoried parse API, standard/advanced tier and optional page range. Both
models and local configuration must be staged before the networkless test.

## Deprecated separate WSL distribution

`forms/provision-wsl.sh`, `build/build-wsl.sh` and the separate rootfs
assembly remain unchanged pending the lead's retirement decision. They retain
their own Docker package installation, daemon configuration, systemd units and
Windows host-channel gate. The common Windows route uses Docker Desktop's WSL 2
backend; it does not import or operate that distribution. No migration, deletion
or unregister operation is implied.

## Repeatability boundary

The release builders consume fixed saved-image archives; they never rebuild or
pull them implicitly. They normalise added file modes/times and use sorted names
and SOURCE_DATE_EPOCH. Provenance records exact pins, every payload file hash,
model revisions and build time derived solely from that epoch. The original ISO
pin and the clean-rootfs hash in payload evidence bind the base media too.

Docker image reconstruction is a separate operation. Pin the builder, base and
hashed wheels, suppress Python bytecode generation, preserve its source record,
and test its reproducibility separately. Do not replace saved images between two
release-media comparison runs. Two matching dry runs do not prove matching ISO,
tar or image bytes; the lab repeats real builds and compares their hashes.
