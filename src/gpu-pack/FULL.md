# Full Docker role

This is source for the next disc 3 build. The published media do not contain the
full role. It uses the existing Linux engine, query-only MCP gateway, signed
content importer and GPU applications. Runtime images come from the verified
offline pack; the lifecycle never pulls or builds.

This all-in-one role is an explicit opt-in for J3 with C7 `profile=headless`.
The default GPU-host role is Forge for J1, J2 and J4: its Workstation owns
content, the live index, engine and gateway. J5 has no GPU pack.

The host run needs rebuilt signed disc 3 and matching signed disc 2 media. This
is the common Linux Docker form, also used on Windows through Docker Desktop's
Linux engine. No host systemd service is required. See [PACK.md](PACK.md) for
the single GPU pack install page.

## Build inputs

Build and merge the existing application and builder images first. The full
image derives from that measured builder image and its engine dependency closure.
Stage a complete Linux Python 3.12 gateway hash lock at
`payload/locks/gateway.lock` and local wheels at `payload/wheelhouse/gateway`.
They must match `containers/full/requirements-gateway.in`; overlapping engine
and gateway versions must agree. Seal the lock hash as
`full.gateway_lock_sha256` in the release pins. No online resolution runs here.

The signed `full.model_bridge` object has schema `deep32.model-bridge.v1` and
the existing `GPU_EXT_*` model/revision and probe fields described in
the source repository's `src/model-bridge/README.md`. Supply measured identity and probes, never invented
hashes or credentials. The bridge validates both models before reporting healthy.
The importer independently verifies content signatures and compatibility. Stage
the public verification key and pinned fingerprint in `release/release-key.gpg`
and `release/release-key.fingerprint` in the runtime kit.

Record the measured positive content/index/active-and-rollback storage allowance
as `full.minimum_data_free_bytes`. The host checker adds it to the offline
payload/image allowance and checks the actual data and Docker backing
filesystems. An absent or unmeasured allowance blocks host provisioning.

On the authorised offline image build host:

```sh
bash build/build-full.sh --stage STAGE --out NEW_OUTPUT
python3 -I -B build/full_images.py merge --stage STAGE --out NEW_OUTPUT
```

Reseal the payload inventory, run existing validation, rebuild the forms and disc
3, then pass the operator signing gate. The merger records measured
`runtime.full_image_id` and `runtime.model_bridge_image_id`; both roles use the
same image with different entry commands. A Forge-only payload without these
pins is refused by the full installer.

## Host lifecycle

Use C7 `role=forge`, `profile=headless`, `transport=tailnet` and
`publish=serve-https`. Full is a Docker role, not a new C7 field. Unknown fields
are refused. No token, code, password or private key belongs in C7 or argv.

Mount verified disc 3 and disc 2 read-only. Choose a dedicated private persistent
directory. From the disc 3 runtime kit, for example:

```sh
bash forms/load-docker.sh /media/gpu/deep32/payload \
  --docker-role full --platform linux \
  --data-root /opt/deep32 \
  --content /media/content --settings /path/to/settings.json
```

Forge is the default role for a separate Workstation. Always pass
`--docker-role full` when selecting this headless all-in-one role, including
later lifecycle actions. Linux defaults to `/opt/deep32`; use
`--data-root DIR` to override it. The guided downloaders run the same
provisioner after their prerequisite, verification, settings and consent checks.

The host must already have qualified Docker/Compose, NVIDIA runtime, OpenSSL and
a signed-in Tailscale client. The lifecycle observes membership without changing
tailnet settings or starting a second node. Private directories must enforce
restricted permissions and must not expose credentials through shares or exports.
On Windows, the install owner, System and local Administrators are the private
file trust boundary; broad Users/Everyone access and a foreign owner are refused.
Actual filesystem and backup/export behaviour still need host verification.

Use the same data root for each action. `start` resumes a journalled import:

```sh
bash forms/load-docker.sh --docker-role full --platform linux \
  --data-root /opt/deep32 --action start
bash forms/load-docker.sh --docker-role full --platform linux \
  --data-root /opt/deep32 --action status
bash forms/load-docker.sh --docker-role full --platform linux \
  --data-root /opt/deep32 --action stop
bash forms/load-docker.sh /media/new-gpu/deep32/payload \
  --docker-role full --platform linux --action upgrade \
  --data-root /opt/deep32 \
  --content /media/content --settings /path/to/settings.json
```

Mutations take an installation lock; status writes nothing and prints no child
logs or pairing fields. Stop retains data, images and identities. Upgrade retains
the previous selection in `previous.json` and never deletes data or downgrades a
content schema. Restore the required inputs and retry after interruption; review
content compatibility before explicitly selecting an older release.

A fresh install requires an empty dedicated directory. Its scoped `owner.json`
permits recovery of an interrupted preparation; an unrelated nonempty directory
is refused. Start rechecks the installed release's media and GPU class, verifies
loaded image identities and restores missing images from the sealed archives.
After a successful import it recreates the engine and gateway so their cached
generation follows the active signed content. A failed import does not replace
the recorded generation or reset credentials.

Persistent directories are `config`, `private`, `content`, `import`, `qdrant`,
`status`, `logs` and versioned runtime kits under `releases`. The existing C8 core
authenticates local credential handover over a protected channel. Read-only
credential mounts go only to the services that need them. The engine uses the
internal authenticated model bridge. Raw GPU, engine and index ports stay
internal; the authenticated doors bind loopback 8081/8082 and the query-only
gateway binds loopback 8766.

The generated `startup-hook-v1.sh` is owned by this installation. The operator
may attach it to the qualified host startup mechanism after the verified media
mounts exist. The loader does not edit host boot scripts or install plugins.
Compose restart policies retain services through Docker restart; the hook
resumes an interrupted import and honours a deliberately stopped installation.
Prove mount persistence on the chosen host.
Windows uses the same provisioner through native Python and Docker Desktop. Its
default data root is `%LOCALAPPDATA%\Deep32`, overridable with
`--data-root`. From the mounted runtime kit, substitute the recorded paths:

```powershell
python -I -B forms\provision.py --kit . --form docker --docker-role full --platform windows --data-root C:\Deep32 --action status
python -I -B forms\provision.py --kit . --form docker --docker-role full --platform windows --data-root C:\Deep32 --action stop
python -I -B forms\provision.py --kit . --form docker --docker-role full --platform windows --data-root C:\Deep32 --action start
python -I -B forms\provision.py --kit . --form docker --docker-role full --platform windows --data-root C:\Deep32 --action resume
```

If Docker Desktop's backing store was moved, pass its actual directory with
`--docker-storage-path DIR` at installation so the storage check uses the right
filesystem. Its default is `%LOCALAPPDATA%\Docker`. The recorded startup hook
uses that choice. Windows gets an owned `startup-hook-v1.ps1`; registering it
with the host startup mechanism remains an operator action.

Keep the recorded media mounted and available to Docker Desktop. The common
Docker form uses the existing daemon on both hosts. The separate WSL-distribution
archive, builder, offline package installation and systemd unit remain unchanged
legacy files, deprecated pending the lead's retirement decision.

## Publication handoff

The full-host publication handoff is not completed by this lifecycle. It reports
`local-ready-publication-required` and never claims remote readiness. The host
adapter must consume these loopback endpoints and the observed host DNS name,
verify HTTPS/MagicDNS/certificates and consent, then merge exact path-preserving
routes with its owned route ledger:

| HTTPS listener | Exact paths | Loopback destination |
|---|---|---|
| 443 | `/mcp` | 8766 |
| 443 | `/v1/embeddings`, `/info`, `/health` | authenticated 8081 |
| 8443 | `/v1/rerank`, `/info`, `/health` | authenticated 8082 |

Unknown paths deny. Conflicting unrelated handlers cause refusal. No unrelated
Serve configuration or tailnet setting may be replaced. The adapter must return
a sanitised owned-route receipt, actual HTTPS MCP URL and separate authenticated
health/tool result. No endpoint is reported verified until publication and
headend enrolment pass. Its absence is an explicit integration gate.

Qualify signed import, restart/resume, credential rejection and rotation,
corrupted input, owned stop/upgrade, a cited lookup through both actual GPU
services and a passing `verify_quote` on the real host. Source fixtures do not
establish those host or release results.
