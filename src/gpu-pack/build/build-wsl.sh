#!/usr/bin/env bash
# What: make the pinned Ubuntu thin WSL form with runtime kit only.
# Usage: build-wsl.sh --stage STAGE --rootfs CLEAN_TAR --out NEW_DIR [--dry-run]
# Exit codes: 0 success/dry run; 2 unsafe rootfs or incomplete pins; other command failure.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
parse_build "$@"
if $dry_run; then
  cat <<'PLAN'
Verify the operator-supplied clean Ubuntu rootfs SHA-256 from payload/evidence/rootfs.json.
Inspect dpkg status; reject Docker, Linux GPU drivers and DKMS in the base.
Reject cached identities, machine-id, SSH host keys, Tailscale state, tokens and user homes.
Add only the explicit runtime kit file list under /opt/deep32-forge; never add payload.
Enable systemd; installer runs forms/provision-wsl.sh with mounted payload path.
Refuse forms at least 1 GiB. Provisioning writes restricted daemon.json before docker.io.
Write forge-wsl.tar, forge-wsl.files.json and SHA256SUMS. No WSL or Docker is invoked.
PLAN
  exit 0
fi
[[ -f "$rootfs_file" ]] || { echo '--rootfs is required' >&2; exit 2; }
preflight
python3 "$kit_dir/build/rootfs.py" --base "$rootfs_file" --payload "$stage_dir" --kit "$kit_dir" --output "$out_dir/forge-wsl.tar"
(cd -- "$out_dir" && sha256sum forge-wsl.tar > SHA256SUMS)
