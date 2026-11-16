#!/usr/bin/env bash
# What: assemble the CPU builder and Qdrant archives from sealed local inputs.
# Usage: build-builder.sh --stage STAGE --out NEW_DIR [--dry-run].
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
parse_build "$@"
if $dry_run; then
  echo 'Would verify builder pins, complete engine hash lock, wheels, git/age Debian closure, absence of embedded eval data and local base/Qdrant archives; docker build --network none --pull=false; save and record both image ids. Quality validation accepts an operator-provided --eval-root at runtime. Downloads: none.'
  exit 0
fi
[[ -d "$stage_dir/payload" && -n "$out_dir" && ! -e "$out_dir" && "${SOURCE_DATE_EPOCH:-}" =~ ^[0-9]+$ ]] || exit 2
tool="$kit_dir/build/builder_inputs.py"
python3 -I -B "$tool" check --stage "$stage_dir"
mkdir -p -- "$out_dir/images"
python3 -I -B "$tool" context --stage "$stage_dir" --out "$out_dir/context"
docker image load --input "$stage_dir/builder/base.tar" >/dev/null
docker image load --input "$stage_dir/builder/qdrant.tar" >/dev/null
base_id=$(python3 -I -B "$tool" base-loaded-id --stage "$stage_dir")
qdrant_id=$(python3 -I -B "$tool" qdrant-loaded-id --stage "$stage_dir")
# Resolve the measured index/config pair after loading, then retag its immutable id.
docker tag "$base_id" deep32-builder-base:locked
docker tag "$qdrant_id" deep32-local/qdrant:locked
find "$out_dir/context" -type f -exec chmod 0644 {} +
find "$out_dir/context" -type d -exec chmod 0755 {} +
find "$out_dir/context" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
docker build --network none --pull=false --build-arg BASE_IMAGE=deep32-builder-base:locked \
  --build-arg "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH" --tag deep32-local/builder:locked "$out_dir/context"
docker image save --output "$out_dir/images/builder.tar" deep32-local/builder:locked
cp -- "$stage_dir/builder/qdrant.tar" "$out_dir/images/qdrant.tar"
python3 -I -B "$tool" record --stage "$stage_dir" --out "$out_dir"
echo 'Run builder_inputs.py merge --stage STAGE --out THIS_OUTPUT; reseal PAYLOAD.json before final validate.py.'
