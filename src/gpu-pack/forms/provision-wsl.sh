#!/usr/bin/env bash
# What: provision a thin WSL form from the mounted disc payload, without downloads.
# Usage: provision-wsl.sh PAYLOAD | --resume [PAYLOAD] | --status | --stop | --dry-run.
set -euo pipefail
if [[ "${1:-}" == --dry-run ]]; then
  echo 'Would verify every mounted payload file, check WSL 2, the Windows NVIDIA driver, /dev/dxg, free disk and ports before mutation, write restricted daemon.json before docker.io, install offline packages, load verified images, place models and run first boot. Downloads: none.'
  exit 0
fi
action=prepare
case "${1:-}" in
  --status) action=status; shift;;
  --stop) action=stop; shift;;
  --resume) action=resume; shift;;
esac
[[ $# -le 1 && $(id -u) == 0 ]] || exit 2
payload=${1:-/opt/deep32-payload}
[[ "$action" != prepare || $# == 1 ]] || exit 2
# provision.py runs forms/host-requirements.py before any destination write.
exec python3 -B /opt/deep32-forge/forms/provision.py --form wsl --payload "$payload" --action "$action"
