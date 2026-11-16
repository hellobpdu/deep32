# S4 integration boundaries

The source contracts are C3, C6, C7 and C8 version 1 drafts. This kit does not
freeze or edit them. The following choices need first-hand lab verification.

## Settings and installation

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
For WSL the administrator supplies C7 through the host channel before first boot
continues. Initial import is root for provisioning only, then C7 selects the
unprivileged default user. The Docker host's existing accounts are untouched.
The WSL archive masks its installed Tailscale daemon so membership stays on Windows.
The Docker form only polls `tailscale status --json` until the host is signed in.
It never starts the host's daemon or runs `tailscale up`; it keeps the host's
name and DNS preferences and reports `Self.HostName` in tailnet and ready C6 lines.

## Pairing helper ABI

`payload/bin/pairing-helper` is a reviewed S7 executable with its exact hash in
pins.json and the payload inventory. It has two subcommands:

- `host-signin`: WSL only. Interact with the installer's Windows host channel;
  return zero only after that host is signed in to the intended person's tailnet.
  Do not create a second Tailscale node inside WSL or copy the host's credentials.
- `pair-forge`: read one JSON object from standard input, containing `code`,
  `expires_seconds=300`, `attempts=1`, `form`, `transport`, `publish`, `token_path`,
  `kb_public_path` and `state_dir`. Read the two credential files locally. The
  JSON is a pipe, never an argument, settings file, environment or log. Return zero
  only after C8 SPAKE2 P-256, TLS-certificate identity binding, durable peer pins
  and authenticated credential handover. Enforce one attempt per window and three
  failed windows in 30 minutes causing a 15-minute lockout, persisted across helper
  restarts. Check the five-minute deadline while handling requests.

The helper also owns C8 publishing: token-checked loopback 8081 to HTTPS 443 and
8082 to HTTPS 8443 for Serve; validated tailnet-only binding for `tailnet-plain`;
the Windows host forwarder for WSL; no network publishing for `loopback`.
It checks MagicDNS and certificate settings, explains Certificate Transparency,
and does not alter tailnet settings. Pairing and token checks apply in loopback.
This ABI requires an adapter in S7; its implementation was not in the allowed
S4 source inputs. First boot refuses unresolved helper pins and never declares
ready merely because it printed a pairing code.

An existing local `paired` record is trusted only inside root-owned installation
state created after the verified helper's successful return. Removing a pairing
must delete that record and rotate/revoke the handed-over Forge token in S7.

## Local identities and C3

The Forge token is 32 random bytes encoded as 64 hex digits. It lives at
`/var/lib/deep32-forge/private/forge-token`, mode 0600, under mode 0700 directories.
An Ed25519 knowledge-base signing key is created there in PEM form, with a public
half for enrolment. The C3 bundle signer/signature encoding is not frozen: the
builder must adapt this key or explicitly change that format before bundle signing
is accepted. This kit does not claim to sign or validate a C3 bundle.

The token is mounted as a read-only compose secret, shared by the door's two
listeners. The raw TEI and reranker services have no host port. The model bridge
is internal; only the authenticated door has a host-access network. Docker daemon
administrators remain trusted. No persistent token is passed in an environment.

## C6 decisions

Status is the current state at `/run/deep32-forge/status`, replaced atomically,
mode 0600, under a mode 0700 directory. Sequence begins at one for each first-boot
service run with a fresh eight-hex boot identifier. All old optional fields are
discarded on each transition. Waiting states repeat at least every ten seconds;
changing expiry is a new state and sequence. The two devices are `/dev/console`
and `/dev/ttyS0`; absence of the serial device does not prevent screen/file output.
The kernel display console is not moved. First-boot stdout/stderr is suppressed
by systemd to keep credentials out of a general journal. Docker's status sidecar
prints only C6 lines to its container log; WSL reads the root-only status file.

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

The full inventory check runs on first boot and when PAYLOAD.json, pins.json,
the installed-inputs implementation or compose source changes. A root-only
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

The production PyMuPDF chain is `production-converter/` (AGPL-3.0-or-later): the
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
