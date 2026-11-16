#!/usr/bin/env bash
# What: build the offline GPU pack as a data-only ISO 9660 disc.
# Usage: build-pack.sh --stage STAGE --rootfs CLEAN_TAR --out NEW_DIR [--dry-run]
# Exit codes: 0 success/dry run; 2 invalid input or tools; other command failure.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
parse_build "$@"
if $dry_run; then
  cat <<'PLAN'
Require release_profile pack and the validated offline payload and clean WSL rootfs.
Build the existing thin Docker and WSL forms without running Docker or WSL.
Put the payload once at deep32/payload, forms in forms/, and README.txt and SHA256SUMS at the disc root.
Ship runtime files only, with no autoinstall directory, settings disc or boot equipment.
Map files in sorted order with fixed ownership and SOURCE_DATE_EPOCH timestamps.
Use xorriso -iso_level 3, Joliet and Rock Ridge; volume DEEP32_GPU_1_0.
Check volume descriptors: no boot record, no system area, correct label and Joliet descriptor.
Write forge-pack.iso and SHA256SUMS; build again in a fresh directory and compare.
Downloads: none.
PLAN
  exit 0
fi
[[ -f "$rootfs_file" ]] || { echo '--rootfs is required for the thin WSL form' >&2; exit 2; }
python3 "$kit_dir/build/pack.py" profile --pins "$stage_dir/pins.json"
command -v xorriso >/dev/null || { echo 'Install the pinned xorriso before building the pack.' >&2; exit 2; }
preflight xorriso
bash "$kit_dir/build/build-docker.sh" --stage "$stage_dir" --out "$out_dir/form-docker"
bash "$kit_dir/build/build-wsl.sh" --stage "$stage_dir" --rootfs "$rootfs_file" --out "$out_dir/form-wsl"
python3 "$kit_dir/build/pack.py" build --stage "$stage_dir" --kit "$kit_dir" \
  --docker "$out_dir/form-docker/forge-docker.tar" --wsl "$out_dir/form-wsl/forge-wsl.tar" \
  --out "$out_dir" --epoch "$SOURCE_DATE_EPOCH"
