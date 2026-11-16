#!/usr/bin/env bash
# What: make a thin compose bundle and loader for an existing Linux Docker host.
# Usage: build-docker.sh --stage STAGE --out NEW_DIR [--dry-run]
# Exit codes: 0 success/dry run; 2 invalid input; other command failure.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
parse_build "$@"
if $dry_run; then
  echo 'Archive explicit runtime kit file list and deep32/load.sh only. No images, models, wheels or debs. Installer passes mounted disc payload to loader. Refuse forms at least 1 GiB. Downloads: none.'
  exit 0
fi
preflight
python3 "$kit_dir/build/thin_forms.py" --kit "$kit_dir" --pins "$stage_dir/pins.json" --epoch "$SOURCE_DATE_EPOCH" --output "$out_dir/forge-docker.tar"
(cd -- "$out_dir" && sha256sum forge-docker.tar > SHA256SUMS)
