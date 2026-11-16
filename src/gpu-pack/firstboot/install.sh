#!/usr/bin/env bash
# What: install the first-boot unit on a fresh lab target, never in the image builder.
# Usage: install.sh --form iso|wsl|docker [--dry-run].
# Exit codes: 0 success/dry run; 2 invalid target/input; other command failure.
set -euo pipefail
form=''
dry_run=false
while (($#)); do
  case "$1" in --form) form=${2:?}; shift 2;; --dry-run) dry_run=true; shift;; *) exit 2;; esac
done
if $dry_run; then
  echo 'Would verify payload, refuse an existing installation, write the chosen form, and enable the first-boot unit without starting it or making identities. Downloads: none.'
  exit 0
fi
[[ "$form" == iso || "$form" == wsl || "$form" == docker ]] || exit 2
[[ ! -e /var/lib/deep32-forge ]] || { echo 'An installation already exists; refusing replacement' >&2; exit 2; }
python3 /opt/deep32-forge/firstboot/installed-inputs.py verify
mkdir -p /etc/deep32-forge
printf '%s\n' "$form" > /etc/deep32-forge/form
install -m 0644 /opt/deep32-forge/firstboot/deep32-forge-firstboot.service /etc/systemd/system/
systemctl enable deep32-forge-firstboot.service
