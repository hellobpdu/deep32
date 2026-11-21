#!/usr/bin/env bash
# What: recover after a converter shell or machine stopped before its EXIT trap.
# Usage: recover-job.sh [--dry-run]. Exit 0 recovered/dry run; 2 unsafe record; 3 recovery failure.
set -euo pipefail
if [[ "${1:-}" == --dry-run ]]; then
  echo 'Would acquire converter lock, validate recovery record and container labels, stop the recorded converter, restore only the recorded services, then remove the record. Downloads: none.'
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
  exec python3 -I -B "$kit_dir/forms/forge_jobs.py" --data-root "$portable_root" recover "$@"
fi
[[ $# == 0 ]] || exit 2
exec 9>/run/deep32-forge/converter.lock
flock 9
record=/var/lib/deep32-forge/conversion-recovery
[[ -f "$record" && ! -L "$record" ]] || exit 2
mapfile -t lines < "$record"
[[ "${lines[0]:-}" =~ ^job=([a-z0-9][a-z0-9_-]{0,39})$ ]] || exit 2
job_id=${BASH_REMATCH[1]}
services=()
for item in "${lines[@]:1}"; do
  [[ -z "$item" ]] && continue
  [[ "$item" == embeddings || "$item" == reranker ]] || exit 2
  services+=("$item")
done
docker info >/dev/null 2>&1 || exit 3
if docker container inspect "deep32-convert-$job_id" >/dev/null 2>&1; then
  [[ $(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "deep32-convert-$job_id") == deep32-forge ]] || exit 2
  docker rm -f "deep32-convert-$job_id" >/dev/null || exit 3
fi
if ((${#services[@]})); then
  docker compose --project-name deep32-forge --env-file /etc/deep32-forge/images.conf -f /etc/deep32-forge/compose.yaml up -d --pull never --no-build "${services[@]}" || exit 3
fi
rm -- "$record"
