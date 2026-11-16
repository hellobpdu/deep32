#!/usr/bin/env bash
# What: install a thin compose bundle using an existing Linux host Docker daemon.
# Usage: load.sh PAYLOAD | --dry-run. Run by the installer as root.
set -euo pipefail
if [[ "${1:-}" == --dry-run ]]; then
  echo 'Would verify mounted payload, check Docker, Compose, NVIDIA driver/runtime, free disk and ports before mutation, place kit and models, load images by immutable ID, and run first boot using the existing host Docker daemon. Downloads: none.'
  exit 0
fi
[[ $# == 1 && $(id -u) == 0 ]] || exit 2
bundle_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -f "$bundle_dir/kit/forms/provision.py" ]]; then
  kit="$bundle_dir/kit"
else
  kit=$(cd -- "$bundle_dir/.." && pwd)
fi
# provision.py runs forms/host-requirements.py before any destination write.
exec python3 -B "$kit/forms/provision.py" --form docker --payload "$1" --kit "$kit"
