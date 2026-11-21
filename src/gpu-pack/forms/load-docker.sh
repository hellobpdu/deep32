#!/usr/bin/env bash
# What: install a thin compose bundle using an existing Linux host Docker daemon.
# Usage: load.sh PAYLOAD --settings SETTINGS [--data-root PATH]
# Optional full role: --docker-role full --content CONTENT
# Lifecycle: --start | --stop | --status | --resume | --upgrade PAYLOAD ...
# Forge is the default. --docker-role full explicitly adds engine and gateway.
set -euo pipefail
if [[ "${1:-}" == --dry-run ]]; then
  echo 'Would verify mounted payload, check Docker, Compose, NVIDIA driver/runtime, free disk and ports before mutation, place kit and models, load images by immutable ID, and run first boot using the existing host Docker daemon. Downloads: none.'
  exit 0
fi
[[ $# -ge 1 && $(id -u) == 0 ]] || exit 2
action=''
case "${1:-}" in
  prepare|--prepare) action=prepare; shift;;
  start|--start) action=start; shift;;
  status|--status) action=status; shift;;
  stop|--stop) action=stop; shift;;
  resume|--resume) action=resume; shift;;
  upgrade|--upgrade) action=upgrade; shift;;
esac
bundle_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -f "$bundle_dir/kit/forms/provision.py" ]]; then
  kit="$bundle_dir/kit"
else
  kit=$(cd -- "$bundle_dir/.." && pwd)
fi
# Preserve the original positional payload while forwarding the contract's
# non-secret role and lifecycle choices to the same provisioner.
if [[ $# -gt 0 && "$1" != --* ]]; then
  payload=$1
  shift
  set -- --payload "$payload" "$@"
fi
if [[ -n "$action" ]]; then
  set -- --action "$action" "$@"
fi
exec python3 -B "$kit/forms/provision.py" --form docker --kit "$kit" "$@"
