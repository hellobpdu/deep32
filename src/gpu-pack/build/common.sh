#!/usr/bin/env bash
# What: shared offline build functions. Usage: source common.sh, or common.sh --dry-run.
# Exit codes: 0 success/dry run; 2 usage, incomplete pins or invalid input; other command failure.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
kit_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dry_run=false
stage_dir=''
out_dir=''
iso_file=''
seed_dir=''
rootfs_file=''
parse_build() {
  while (($#)); do
    case "$1" in
      --dry-run) dry_run=true; shift ;;
      --stage|--out|--iso|--seed|--rootfs)
        (($# >= 2)) || { echo 'Missing option value' >&2; exit 2; }
        case "$1" in
          --stage) stage_dir=$2;; --out) out_dir=$2;; --iso) iso_file=$2;;
          --seed) seed_dir=$2;; --rootfs) rootfs_file=$2;;
        esac
        shift 2 ;;
      *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
  done
}
plan_common() {
  cat <<'PLAN'
Offline only. Downloads this build would make: none.
Before this run the operator stages: authenticated Ubuntu ISO and archive snapshots;
official NVIDIA toolkit and Tailscale signed repositories; pinned TEI images from
ghcr.io/huggingface/text-embeddings-inference; pinned runtime base and application
images; exact Hugging Face snapshots; complete hashed wheels and upstream licences;
the reviewed converter chain. Pairing is bundled in the runtime kit.
Fetch plan: fetch-inputs.sh --dry-run.
Sources and manifest: PINS.md. Validate every TO-PIN, exact payload inventory and model revision.
Refuse existing output paths, special members and unresolved or unverified inputs.
Copy the identical kit and payload, normalise timestamps and write provenance.
PLAN
}
preflight() {
  [[ -n "$stage_dir" && -n "$out_dir" && -n "${SOURCE_DATE_EPOCH:-}" ]] || { echo '--stage, --out and SOURCE_DATE_EPOCH are required' >&2; exit 2; }
  [[ "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]] || exit 2
  [[ ! -e "$out_dir" ]] || { echo 'Output must be a new directory' >&2; exit 2; }
  python3 "$kit_dir/build/validate.py" --stage "$stage_dir" --kit "$kit_dir"
  python3 "$kit_dir/build/agpl_check.py" --stage "$stage_dir" --pins "$stage_dir/pins.json"
  python3 "$kit_dir/build/tool-versions.py" --pins "$stage_dir/pins.json" --require "${1:-python}"
  stage_dir="$(cd -- "$stage_dir" && pwd)"
  out_dir=$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$out_dir")
  # No output inside the source kit or staged payload.
  case "$out_dir/" in "$stage_dir/"*|"$kit_dir/"*) echo 'Output overlaps an input' >&2; exit 2;; esac
  mkdir -p -- "$out_dir"
  export LC_ALL=C TZ=UTC
  umask 022
}
copy_payload() {
  local destination=$1
  mkdir -p -- "$destination/kit" "$destination/payload"
  cp -a -- "$kit_dir/." "$destination/kit/"
  cp -a -- "$stage_dir/payload/." "$destination/payload/"
  cp -- "$stage_dir/PAYLOAD.json" "$destination/PAYLOAD.json"
  # The filled pins are the sole runtime settings, shared by all forms.
  cp -- "$stage_dir/pins.json" "$destination/kit/pins.json"
  find "$destination" -type f -exec chmod 0644 {} +
  find "$destination" -type f -name '*.sh' -exec chmod 0755 {} +
  chmod 0755 "$destination/kit/pairing/bin/deep32-pair" "$destination/kit/pairing/bin/pairing-helper"
  find "$destination" -type d -exec chmod 0755 {} +
  find "$destination" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
}
provenance() {
  python3 "$kit_dir/build/provenance.py" --root "$1" --pins "$stage_dir/pins.json" --output "$2"
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  [[ "${1:-}" == --dry-run && $# == 1 ]] || { echo 'Source common.sh, or use --dry-run' >&2; exit 2; }
  plan_common
fi
