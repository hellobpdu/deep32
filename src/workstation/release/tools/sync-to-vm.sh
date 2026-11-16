#!/usr/bin/env bash
# sync-to-vm.sh [vm]: copy the BASE lane's sources (distrib/, release/, tests/base/) from the Windows repo (the source
# of truth) into /build/lanes/base/repo on the build VM, owned by d32build. Runs in Git Bash on the source workstation.
# Uses tar over the agent's SSH key; the VM stays offline (no fetch window needed).
set -euo pipefail
vm="${1:?pass the build host as the first argument}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ssh_vm="${D32_SSH_COMMAND:?set D32_SSH_COMMAND to the build-host command}"
# CRLF would break ksh and pf on the VM: refuse to ship any file that has one.
if grep -rlI $'\r' "$repo/distrib" "$repo/release" "$repo/tests/base" >/dev/null 2>&1; then
  echo "sync-to-vm: CRLF line endings found:" >&2
  grep -rlI $'\r' "$repo/distrib" "$repo/release" "$repo/tests/base" >&2
  exit 1
fi
tar -C "$repo" --format=ustar -cf - distrib release tests/base |
  bash "$ssh_vm" "$vm" 'rm -rf /build/lanes/base/repo.new && mkdir -p /build/lanes/base/repo.new &&
    tar -C /build/lanes/base/repo.new -xf - && rm -rf /build/lanes/base/repo && mv /build/lanes/base/repo.new /build/lanes/base/repo &&
    chown -R d32build:d32build /build/lanes/base/repo && echo "synced: $(find /build/lanes/base/repo -type f | wc -l) files"'
