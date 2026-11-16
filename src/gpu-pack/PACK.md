# GPU container pack 1.0

The pack is a data-only ISO 9660 disc. Its Docker and WSL bundles use one shared
payload on the mounted disc. Provisioning verifies that payload, copies it to the
target and loads image archives by immutable image ID without a registry pull.
The payload includes the models, licences, corresponding source and evidence.
A bare-metal installer is planned for a later release.

## Requirements

For Linux, install Docker Engine, the Docker Compose plugin, an NVIDIA driver,
the NVIDIA Container Toolkit and its Docker runtime or GPU CDI configuration,
Python 3, systemd and Tailscale before using the pack. The Docker form never
installs a driver or toolkit and never changes the host's Tailscale sign-in,
hostname or DNS settings. Sign in to the host before pairing.

For Windows, prepare Windows 11 with WSL 2 and a Windows NVIDIA driver supporting
WSL GPU access. Import this pack as a new WSL 2 distribution. Its offline package
installation supplies Docker, Compose and the NVIDIA Container Toolkit inside
that distribution; it never installs a Linux NVIDIA driver. Systemd must run in
the imported distribution. Tailscale sign-in belongs to the Windows host.

The machine register pins the required versions in `pins.json`:

| Field | Check |
|---|---|
| `runtime.docker_minimum_version` | Docker server version; at least 19.03.0. |
| `runtime.compose_minimum_version` | Compose plugin; at least 2.30.0 for this compose file's `gpus` field. |
| `runtime.mineru_minimum_driver` | Verified floor for the actual MinerU wheels and base. |
| `runtime.cuda_minimum_driver` | Verified floor for the other CUDA application images. |
| `runtime.tei_minimum_driver` | Verified floor for the packaged TEI image variants. |
| `runtime.windows_cuda13_driver_build` | Additional verified Windows/WSL driver floor. |

The host check requires the highest applicable driver floor. Unresolved or missing
floors refuse installation; the template's `TO-PIN` entries are build inputs to
finish, not supported driver versions. Driver evidence must match the images on
the disc. The GPU must also pass the existing first-boot TEI capability and model
checks. CPU experiments in the retained kit do not satisfy the pack GPU check.

Keep `127.0.0.1:8081` and `127.0.0.1:8082` free. The door publishes these two
authenticated loopback endpoints; raw model sockets are not published on the
host. The paired transport may publish the intended tailnet HTTPS routes on 443
and 8443 through the existing pairing helper. WSL uses that helper for host
sign-in and transport, rather than starting another Tailscale node.

The disk check requires the payload's total bytes plus twice the saved image
archive bytes plus one GiB on both the installation filesystem and Docker's
data-root filesystem. This conservative budget includes the model and payload
copy and image unpacking headroom. Reserve further space for conversion jobs,
logs and later work. The check is a preflight estimate, not a disk quota.

## Verify and provision on Linux

Mount the ISO read-only. In these commands, replace the image and mount paths
with local paths, then change to the mounted disc root:

```bash
sudo mkdir -p /mnt/deep32-gpu
sudo mount -o loop,ro /path/to/deep32-gpu.iso /mnt/deep32-gpu
cd /mnt/deep32-gpu
sha256sum -c SHA256SUMS
sudo python3 -B forms/host-requirements.py --form docker --kit "$PWD" --payload "$PWD/deep32/payload"
```

Check the release signature through the release's normal verification process
as well; hashes alone do not authenticate the disc. The host checker only reads
files, queries tools and probes the two loopback binds. A refusal is one sentence
naming the prerequisite to install or the occupied port to free. It changes no
host package or configuration.

Supply the installer's valid, non-secret C7 settings before invoking the loader.
For a fresh lab target the required shape is:

```json
{
  "format": "deep32-settings",
  "format_version": 1,
  "role": "forge",
  "node_name": "forge-lab",
  "user_name": "forge",
  "time_zone": "Etc/UTC",
  "keyboard": "us",
  "profile": "gpu",
  "disk_encryption": false,
  "transport": "tailnet",
  "publish": "serve-https"
}
```

Choose the settings for the target; the Docker form observes the existing host
identity. Do not put passwords, tokens or keys in the settings file. Install the
file with restricted access, then provision:

```bash
sudo install -D -m 0600 /path/to/settings.json /etc/deep32-forge/settings.json
sudo bash forms/load-docker.sh "$PWD/deep32/payload"
```

The loader repeats the byte and host checks before its first destination write.
It puts runtime code in `/opt/deep32-forge`, the verified payload in
`/opt/deep32-payload`, and its inventory in `/opt/PAYLOAD.json`. It enables the
first-boot service and runs first boot synchronously. Keep the terminal open
until sign-in, pairing and model checks finish. An absent settings file makes
first boot wait; supply it through a second root terminal rather than starting
another loader. Repeating a completed provision with the same media is read-only;
a different payload or form is refused.

The optional thin archive is `forms/forge-docker.tar`; it contains runtime code
and a `deep32/load.sh` loader, with no model or payload copy. After extracting it
to a fresh directory, that loader accepts the same mounted `deep32/payload` path.

## Verify and provision on Windows

Mount the ISO and verify its release signature and root `SHA256SUMS` before
import. For example, from an existing Linux shell that can read drive E:

```bash
cd /mnt/e
sha256sum -c SHA256SUMS
```

From PowerShell, substitute the mounted drive, a new installation folder and the
local path to C7 settings. Confirm `wsl --list --verbose` reports version 2 for
the imported distribution:

```powershell
wsl --import Deep32Forge C:\Deep32Forge E:\forms\forge-wsl.tar --version 2
wsl --list --verbose
wsl -d Deep32Forge -u root -- python3 -B /opt/deep32-forge/forms/host-requirements.py --form wsl --payload /mnt/e/deep32/payload
wsl -d Deep32Forge -u root -- install -D -m 0600 /mnt/c/path/to/settings.json /etc/deep32-forge/settings.json
wsl -d Deep32Forge -u root -- bash /opt/deep32-forge/forms/provision-wsl.sh /mnt/e/deep32/payload
```

Use the actual Linux mount path for the disc if it differs from `/mnt/e`.
The checker requires a WSL 2 kernel, `/dev/dxg`, a working NVIDIA query and a
driver at every applicable pinned floor before installing offline packages.
It checks free disk and loopback ports before mutation. A Windows lab machine
without a GPU is expected to stop with:

```text
Install an NVIDIA Windows driver with WSL support and expose the GPU to WSL 2.
```

Do not bypass that refusal to test services on the no-GPU machine. It is the
negative provisioning test, not a supported GPU installation.

## Pair with the Workstation

First boot observes host sign-in and invokes the verified
`/opt/deep32-payload/bin/pairing-helper` with `host-signin` for WSL and
`pair-forge` for pairing. It supplies the pairing request internally; do not
construct a request or copy the local token into a command line. Use the
Workstation's existing pairing workflow when the Forge status reports pairing.
The current code expires after 300 seconds and allows one attempt; the helper
enforces persistent lockout. Complete the approved pairing workflow again after
an expiry rather than reusing the old code.

Root may read `/run/deep32-forge/status` for the current C6 state. On the Docker
form the status container also carries C6 through the project's container logs.
Readiness follows successful pairing and authenticated health checks on both
ports. Never print the root-only token, key files or pairing request contents.

## Stop and remove

Finish or recover any active conversion job before stopping the model services.
On the Linux target, or inside the WSL distribution as root:

```bash
sudo systemctl disable --now deep32-forge-firstboot.service
sudo docker compose --project-name deep32-forge --env-file /etc/deep32-forge/images.conf -f /etc/deep32-forge/compose.yaml down
```

This stops and removes this Compose project's containers and networks. The
payload, settings, job data, pairing state and image cache remain. To remove the
installation, first export any required job output and revoke its Workstation
pairing through the existing pairing workflow. Remove only this installation's
`/opt/deep32-forge`, `/opt/deep32-payload`, `/opt/PAYLOAD.json`,
`/etc/deep32-forge`, `/var/lib/deep32-forge`, `/run/deep32-forge` and
`/etc/systemd/system/deep32-forge-firstboot.service`, then run
`sudo systemctl daemon-reload`. Leave host Docker, drivers, toolkit, Tailscale
and unrelated image IDs in place. Remove a cached image only after confirming
no other installation uses that immutable ID.

For a wholly disposable WSL installation, stop its project as above, export
anything needed and run `wsl --terminate Deep32Forge` followed by
`wsl --unregister Deep32Forge` in PowerShell. Unregistering permanently removes
that distribution and its files; use its exact name from `wsl --list --verbose`.
It does not uninstall the Windows driver or host Tailscale.

## Licences and corresponding source

The AGPL licence text is `deep32/payload/licences/AGPL-3.0.txt`. Complete
corresponding source, build material and dependency sources are under
`deep32/payload/source`, including `production-converter`; licence notices and
the release evidence remain beside the payload. The installed copy is under
`/opt/deep32-payload`. Retain these materials with any redistributed converter
image and follow the included licences. The data-disc build refuses missing
required AGPL material through the existing licence/source checks.
