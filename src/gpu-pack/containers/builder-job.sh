#!/usr/bin/env bash
# What: run a CPU builder against the Forge's existing model services.
# Usage: builder-job.sh build|bundle ARGS...; --dry-run describes the operation.
set -euo pipefail
if [[ "${1:-}" == --dry-run ]]; then
  echo 'Would use installed immutable builder/Qdrant images and existing TEI/reranker services; publish no builder ports; run the production KB command. Downloads: none.'
  exit 0
fi
portable_root=
if [[ "${1:-}" == --data-root ]]; then
  [[ $# -ge 2 && -n "$2" ]] || exit 2
  portable_root=$2
  shift 2
fi
kit_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -z "$portable_root" && "$kit_dir" =~ ^(.+)/releases/[0-9a-f]{64}/kit$ ]]; then
  portable_root=${BASH_REMATCH[1]}
fi
if [[ -n "$portable_root" ]]; then
  exec python3 -I -B "$kit_dir/forms/forge_jobs.py" --data-root "$portable_root" "$@"
fi
[[ $# -gt 0 && ( "$1" == build || "$1" == bundle ) ]] || exit 2
python3 -I -B /opt/deep32-forge/containers/builder-state.py
compose=(docker compose --env-file /etc/deep32-forge/images.conf -f /etc/deep32-forge/compose.yaml -f /opt/deep32-forge/containers/compose.builder.yaml)
if [[ "$1" == build ]]; then
  "${compose[@]}" up -d --pull never qdrant embeddings reranker
  "${compose[@]}" run --rm --no-deps --pull never --entrypoint python builder -I -B /opt/deep32/builder/wait_services.py
fi
"${compose[@]}" run --rm --no-deps --pull never builder "$@"
