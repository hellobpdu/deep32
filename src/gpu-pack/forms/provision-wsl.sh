#!/usr/bin/env bash
# What: provision a thin WSL form from the mounted disc payload, without downloads.
# Usage: provision-wsl.sh PAYLOAD | --dry-run. Run by the installer as root.
set -euo pipefail
if [[ "${1:-}" == --dry-run ]]; then
  echo 'Would verify every mounted payload file, check WSL 2, the Windows NVIDIA driver, /dev/dxg, free disk and ports before mutation, write restricted daemon.json before docker.io, install offline packages, load verified images, place models and run first boot. Downloads: none.'
  exit 0
fi
[[ $# == 1 && $(id -u) == 0 ]] || exit 2
# provision.py runs forms/host-requirements.py before any destination write.
exec python3 -B /opt/deep32-forge/forms/provision.py --form wsl --payload "$1"
