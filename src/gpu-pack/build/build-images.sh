#!/usr/bin/env bash
# What: rebuild application images from the staged offline base and complete hashed locks.
# Usage: build-images.sh --stage STAGE --out NEW_DIR [--dry-run] (lab Docker host only).
# Exit codes: 0 success/dry run; 2 incomplete input or existing output; other command failure.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
parse_build "$@"
if $dry_run; then
  echo 'Would verify the staged runtime base archive and image id, make sorted contexts from source and hashed locks, and build the reranker, converter and door with --network none --pull=false. Save each image and record its immutable id and archive hash. Downloads: none. Upstream input preparation is fetch-inputs.sh; its --dry-run prints every source and pin.'
  exit 0
fi
[[ -d "$stage_dir/payload" && -n "$out_dir" && ! -e "$out_dir" && "${SOURCE_DATE_EPOCH:-}" =~ ^[0-9]+$ ]] || exit 2
python3 "$kit_dir/build/agpl_check.py" --stage "$stage_dir" --pins "$stage_dir/pins.json"
python3 "$kit_dir/build/images.py" verify-base "$stage_dir"
docker image load --input "$stage_dir/base/runtime.tar" >/dev/null
base_id=$(python3 "$kit_dir/build/images.py" base-id "$stage_dir")
docker tag "$base_id" deep32-runtime-base:locked
mkdir -p -- "$out_dir/contexts" "$out_dir/images"
out_dir="$(cd -- "$out_dir" && pwd)"
for role in reranker converter door; do
  context="$out_dir/contexts/$role"
  source_name=$role
  [[ "$role" != converter ]] || source_name=converter-agpl
  mkdir -- "$context"
  cp -a -- "$kit_dir/containers/$source_name/." "$context/"
  if [[ "$role" != door ]]; then
    cp -a -- "$stage_dir/payload/wheelhouse/$role" "$context/wheelhouse"
    cp -- "$stage_dir/payload/locks/$role.lock" "$context/requirements.lock"
  fi
  if [[ "$role" == converter ]]; then
    python3 "$kit_dir/build/images.py" backend-lock "$context"
    cp -- "$stage_dir/payload/licences/AGPL-3.0.txt" "$context/AGPL-3.0.txt"
    cp -a -- "$stage_dir/payload/source/production-converter" "$context/production-converter"
  fi
  find "$context" -type f -exec chmod 0644 {} +
  find "$context" -type d -exec chmod 0755 {} +
  find "$context" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
  docker build --network none --pull=false --build-arg BASE_IMAGE=deep32-runtime-base:locked \
    --build-arg "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH" --tag "deep32-local/$role:locked" "$context"
  docker image save --output "$out_dir/images/$role.tar" "deep32-local/$role:locked"
done
python3 "$kit_dir/build/images.py" index "$stage_dir" "$out_dir"
bash "$kit_dir/build/build-builder.sh" --stage "$stage_dir" --out "$out_dir/builder-build"
echo 'Copy the three application archives into payload/images and merge their index first; builder_inputs.py merge adds builder/Qdrant and their pins. Reseal PAYLOAD.json, then run validate.py.'
