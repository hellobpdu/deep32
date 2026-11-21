# GPU pack

The GPU pack runs Linux containers. Use the same Docker form on a Linux host or
on Windows with Docker Desktop. It is a data disc, not an operating-system
installer. Its containers, models, licences, corresponding source and evidence
come from the verified pack; installation does not pull images or models.

These instructions describe the next signed disc build. The published alpha
discs do not contain this portable Forge lifecycle or the optional full role. Rebuilt
signed discs and recorded host acceptance runs are still required.

## Before installing

On Linux, prepare Docker Engine, Compose, an NVIDIA driver and the NVIDIA
Container Toolkit or qualified GPU CDI configuration. Python 3, OpenSSL and the
host's signed-in Tailscale client must be available. Deep32 uses the existing
Docker daemon and needs no host systemd service.

On Windows, prepare Docker Desktop with Linux containers and its WSL 2 backend.
Enable Docker Desktop's NVIDIA GPU support and use a compatible Windows NVIDIA
driver, native Python and OpenSSL. Start Docker Desktop and sign the Windows host into Tailscale. Deep32
does not import a distribution or install Docker in a separate distribution.

Use the resolved requirements in the signed release. Docker must meet
`runtime.docker_minimum_version`; Compose must meet
`runtime.compose_minimum_version` (currently 2.30.0 or later for the GPU field).
The driver floor is the highest applicable resolved MinerU, CUDA and TEI minimum,
plus the Windows driver-build floor on Windows. An unresolved pin is a refusal.
GPU identity, memory, image class and both model revisions must pass too.

Forge defaults to authenticated host loopback ports 8081/8082 and pairing 7453;
C7 `forge_ports` can select free alternatives. Full also uses gateway port 8766.
Raw model, engine and index ports stay internal. Forge publishes its own checked
host Serve routes; full retains its separate host publication gate.

Mount signed disc 3 read-only on the Forge; mount matching disc 2 on the
Workstation for its content import. The explicit full role needs both discs on
the GPU host. Keep recorded media paths available for restart and resume.
Budget the actual data and Docker filesystems from the
signed measured release envelope: payload bytes plus twice the saved-image
archive bytes plus 1 GiB. Add measured conversion/builder job space and, for the
full role, its content, index and rollback allowance. ISO size alone does not
give installed space.

## Linux Docker

Authenticate the release signature, then verify the disc's `SHA256SUMS`. Mount
disc 3 at `/media/gpu`, substituting your path. Import disc 2 on the Workstation.
From the disc 3 root, for the default Forge role:

```sh
sha256sum -c SHA256SUMS
sudo python3 -B forms/host-requirements.py --form docker --docker-role forge --kit "$PWD" --payload "$PWD/deep32/payload" --data-root /opt/deep32
sudo bash forms/load-docker.sh "$PWD/deep32/payload" \
  --settings /path/to/settings.json \
  --data-root /opt/deep32
```

The default data root is `/opt/deep32`. Change it with `--data-root DIR` and
use that same root for later actions. Choose a dedicated private directory with
owner-only permissions. A fresh install refuses an unrelated nonempty directory.

The default Docker role is `forge`: embeddings, reranker and their authenticated
door run on the GPU host. The converter and index builder are separate Compose
profiles for their jobs. Content, the live index, engine and MCP gateway stay on
the Workstation; finish its content import before pairing. Use C7 `role=forge`,
the profile for the chosen journey, `transport=tailnet` and `publish=serve-https`.
Supply the other required C7 choices. Forge publication additionally requires
`forge_accept_ct=true`, agreeing to node-name publication in Certificate
Transparency. [INSTALL.md](../../INSTALL.md#pair-the-distributed-forge-next-rebuilt-discs)
gives every configurable port and a worked coexistence example. Pass that complete
settings file with `--settings`. Put no credentials or pairing codes in settings or argv.

| Journey | GPU-host Docker role |
|---|---|
| J1, J2, J4 | Forge (default), paired with a Workstation |
| J3 | Explicit `--docker-role full`, C7 `profile=headless`; no Workstation |
| J5 | No GPU pack |

The full role adds engine, gateway, signed content importer and live index on
the GPU host. Select it explicitly and supply `--content` and the headless
settings described in [FULL.md](FULL.md). Both roles use this one Docker form.

The guided route is `deep32-get.sh disc3 --install` from the complete source
kit. [INSTALL.md](../../INSTALL.md#5-install-planned) covers checks, verified downloads,
settings and plan consent.

## Windows Docker Desktop

From the complete source kit in an elevated PowerShell console, run the Forge
route. Import signed disc 2 on the Workstation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File src\release-tools\deep32-get.ps1 disc3 --install --journey J1 --form docker --platform windows --docker-role forge --data-root C:\Deep32
```

Choose a dedicated private data directory. Without
`--data-root`, Windows uses `%LOCALAPPDATA%\Deep32`. The entry point checks
Docker Desktop, the Linux engine, WSL 2 backend and GPU prerequisites, verifies
disc 3, validates the same C7 settings and asks for plan consent. Native Python
runs the pack's Docker provisioner against Docker Desktop. Settings are staged
with a user-only ACL before being copied into the install's `config` directory.
If the Docker Desktop backing store was moved, pass `--docker-storage-path DIR`
to check its actual filesystem; the default is `%LOCALAPPDATA%\Docker`.

Leave the GPU ISO attached at the recorded drive. Make the payload and data paths
available to Docker Desktop, keep them stable and verify private-file permissions
on the actual filesystem. Windows reboot, Docker Desktop restart and GPU health
still need recorded host qualification.

## Lifecycle and recovery

For the default Forge role on Linux, use the recorded data root:

```sh
sudo bash forms/load-docker.sh --status --data-root /opt/deep32
sudo bash forms/load-docker.sh --stop --data-root /opt/deep32
sudo bash forms/load-docker.sh --start --data-root /opt/deep32
sudo bash forms/load-docker.sh --resume --data-root /opt/deep32
sudo bash forms/load-docker.sh --upgrade /media/new-gpu/deep32/payload \
  --data-root /opt/deep32 \
  --settings /path/to/settings.json
```

The same lifecycle is available through the native Python provisioner on Windows,
for example `python -I -B forms\provision.py --kit . --form docker --docker-role forge --platform windows --data-root C:\Deep32 --action status`
from the mounted runtime kit. Keep that role and data root for later actions.
`--action` accepts `prepare`, `start`, `stop`, `status`, `resume` and `upgrade`.
For full, add `--docker-role full` to every action; [FULL.md](FULL.md) gives its
content and headless-settings requirements.

Status omits credentials, sign-in fields, pairing codes and child logs. Stop
retains data, images and identities. Start runs a stopped installation; resume
honours an intentional stop and otherwise retries the recorded lifecycle.
Full also resumes its journalled import. Upgrade retains the previous release
selection and never silently downgrades content. Mutations lock and act only on
the owned installation.

Compose restart policies handle Docker restarts. The owned startup hook may be
attached to a qualified host startup mechanism to resume an interrupted lifecycle
after its media mounts exist. The loader installs no host boot script. Prove
mount and startup persistence on the selected host.

Healthy local services do not establish remote MCP readiness. Both local
lifecycles keep readiness false until the remaining checks pass; host publication and
headend enrolment or Workstation pairing are separate gates. After authenticated
model warm-up, Linux Forge reuses the existing bounded local C8 listener and
protected C6 console. A valid durable peer commit skips a new window; incomplete
pairing resumes from the same state and preserves lockout. Windows still needs
the qualified protected host-console adapter and opens no code or window.
Forge now publishes owned host model and pairing routes and records
`published-pairing-required`; both Windows and Linux keep unrelated host routes.
Actual protected Windows pairing-console support and host qualification remain
required before relying on Workstation pairing reachability. Stop removes only
confirmed owned routes; ambiguous interrupted updates refuse mutation for
operator reconciliation. The direct Forge lifecycle's `uninstall` action also
removes its owned startup hook, retaining data, keys and journal for revocation.

Before removal, finish or recover any conversion job, stop this install, export
required output and revoke its pairing. Retain the data root until export and
revocation are checked. Leave host Docker, drivers, Tailscale and shared image IDs.

## Forge conversion and index jobs

Use `forms/forge_jobs.py` for the portable Forge role on either host, as the
installation administrator. Keep the recorded media available. For a conversion,
stage a new private `jobs/JOB` directory below the data root, with no `output`
directory. Supply `input.pdf` for `pymupdf` or `mineru`. The `production` backend
takes exactly one of `input.pdf`, `input.html`, `input.epub` or `input.mib.json`,
plus `job.json` and an optional `lexicon` directory.

From the exact retained runtime kit under the data root's `releases` directory
on Linux, for example:

```sh
sudo python3 -I -B forms/forge_jobs.py --data-root /opt/deep32 convert job-001 pymupdf
sudo python3 -I -B forms/forge_jobs.py --data-root /opt/deep32 recover
```

Use `recover` only when an interrupted conversion left a recovery record. The
recovery action validates that record and the installation under the shared
lock, then verifies retained media, Forge identity and all selected archives
before reloading any missing pinned image. Ordinary jobs require their images
to be present and do not load them.
The converter holds the installation lock, honours the operator GPU hold, records
the active model services, pauses both models and restores only the recorded
services after removing its own converter. Keep an interrupted record until
recovery succeeds; do not delete it to start another job. MinerU alone accepts
`--tier standard|advanced` and `--page-range N[-M]` after its backend name.
An index `build` also honours the GPU hold before starting model services.

Windows uses the same arguments with native `python -I -B`, the retained Windows
script path and the recorded Windows `--data-root`. The retained kit's
`containers/convert-job.sh`, `recover-job.sh` and `builder-job.sh` wrappers also
select this recorded root; from a mounted kit, give their first `--data-root ROOT`
option explicitly. `build` and `bundle` pass the
existing builder arguments through the builder profile; their paths refer to
the container's `/var/lib/deep32-forge`, backed by the data root's `builder`
directory. They require the signed builder/Qdrant image inputs. C3 signing
compatibility remains the gate in [INTERFACES.md](INTERFACES.md#local-identities-and-c3).
The older fixed-path shell job wrappers belong to the legacy install.

## Separate WSL distribution: deprecated

The old `forms/forge-wsl.tar`, `forms/provision-wsl.sh` and
`build/build-wsl.sh` are retained pending the lead's retirement decision. Their
separate Docker daemon, offline package installation and systemd lifecycle remain
legacy source. They are not the Windows route above. Do not delete or unregister
an existing distribution as part of this change. Docker Desktop's WSL 2 backend
remains a prerequisite for the common Docker form.

## Licences and corresponding source

Deep32's own code is under the Apache-2.0 license. Its license text is `LICENSE`
and its notice is `NOTICE`, both at the root of this pack. Both files are part of
the runtime kit, so `forms/forge-docker.tar` and every installation carry them,
and the door, reranker and builder images keep a copy beside their code. The
PDF converter is the exception in this pack. It uses PyMuPDF and MuPDF, which
are AGPL, so the converter is under AGPL-3.0-or-later. Its license text is
`deep32/payload/licences/AGPL-3.0.txt`. The converter also installs MinerU,
which is under the MinerU Open Source License: Apache-2.0 with additional terms.
A separate commercial license from the MinerU team is required above 100 million
monthly active users or USD 20 million in monthly revenue, and an online service
for third parties built on MinerU must say clearly that it uses MinerU. MinerU's
license text is `licenses/MinerU-4.0.7-LICENSE.md`, which is part of the
runtime kit too. Complete corresponding source and build material are under
`deep32/payload/source`, including `production-converter`. Retain these,
licence notices and release evidence with redistributed converter images. The
data-disc build checks required AGPL material.
