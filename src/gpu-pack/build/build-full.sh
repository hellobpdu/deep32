#!/usr/bin/env bash
# Build the full Linux role from the verified local builder and pinned wheels.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
parse_build "$@"
if $dry_run; then
  echo 'Would verify the local builder archive, complete hash-pinned Linux gateway wheels and model attestation pins; copy the existing engine, gateway, importer and shared model bridge; build with --network none --pull=false; save and bind full/model_bridge image ids. Downloads: none.'
  exit 0
fi
[[ -d "$stage_dir/payload" && -n "$out_dir" && ! -e "$out_dir" && "${SOURCE_DATE_EPOCH:-}" =~ ^[0-9]+$ ]] || exit 2
tool="$kit_dir/build/full_images.py"
python3 -I -B "$tool" check --stage "$stage_dir"
mkdir -p -- "$out_dir/images"
python3 -I -B "$tool" context --stage "$stage_dir" --out "$out_dir/context"
docker image load --input "$stage_dir/payload/images/builder.tar" >/dev/null
base_id=$(python3 -I -B "$tool" base-loaded-id --stage "$stage_dir")
docker tag "$base_id" deep32-full-base:locked
find "$out_dir/context" -type f -exec chmod 0644 {} +
find "$out_dir/context" -type d -exec chmod 0755 {} +
find "$out_dir/context" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
# Requires the local BuildKit frontend; no frontend image is fetched.
DOCKER_BUILDKIT=1 docker build --network none --pull=false --build-arg BASE_IMAGE=deep32-full-base:locked \
  --build-arg "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH" --tag deep32-local/full:locked "$out_dir/context"
# Both runtime roles use the identical verified code and dependency closure.
docker tag deep32-local/full:locked deep32-local/model_bridge:locked
for role in full model_bridge; do
  docker image save --output "$out_dir/images/$role.tar" "deep32-local/$role:locked"
done
python3 -I -B "$tool" record --stage "$stage_dir" --out "$out_dir"
echo 'Run full_images.py merge --stage STAGE --out THIS_OUTPUT; reseal PAYLOAD.json before final validation and signing.'
