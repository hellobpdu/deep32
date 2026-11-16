#!/usr/bin/env bash
# What: install pinned packages from authenticated, offline repository snapshots.
# Usage: install-packages.sh --form iso|wsl [--dry-run] (installed lab target only).
# Exit codes: 0 success/dry run; 2 missing pin/repository; 3 unsafe driver plan.
set -euo pipefail
form=''
dry_run=false
while (($#)); do
  case "$1" in --form) form=${2:?}; shift 2;; --dry-run) dry_run=true; shift;; *) exit 2;; esac
done
if $dry_run; then
  echo 'Would verify payload; configure isolated file: Ubuntu, NVIDIA toolkit and Tailscale signed repositories; apt simulation refuses DKMS; install exact pins. ISO installs signed Ubuntu modules; WSL installs no Linux driver. Configure NVIDIA runtime, enable first boot. Downloads: none.'
  exit 0
fi
[[ "$form" == iso || "$form" == wsl ]] || exit 2
kit_dir=/opt/deep32-forge
payload=/opt/deep32-payload
python3 "$kit_dir/firstboot/installed-inputs.py" verify
[[ -f /usr/share/keyrings/ubuntu-archive-keyring.gpg ]] || exit 2
mkdir -p /etc/deep32-forge/apt/empty
# The operator mirrors these suites and the original signed Release metadata verbatim.
cat > /etc/deep32-forge/apt/sources.list <<EOF
deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] file:$payload/repos/ubuntu noble main restricted universe multiverse
deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] file:$payload/repos/ubuntu noble-updates main restricted universe multiverse
deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] file:$payload/repos/ubuntu noble-security main restricted universe multiverse
deb [signed-by=$payload/keyrings/nvidia.gpg] file:$payload/repos/nvidia /
deb [signed-by=$payload/keyrings/tailscale.gpg] file:$payload/repos/tailscale noble main
EOF
apt_options=(-o Dir::Etc::sourcelist=/etc/deep32-forge/apt/sources.list -o Dir::Etc::sourceparts=/etc/deep32-forge/apt/empty -o Acquire::Languages=none)
apt-get "${apt_options[@]}" update
mapfile -t packages < <(python3 "$kit_dir/firstboot/installed-inputs.py" packages "$form")
((${#packages[@]})) || exit 2
if ! apt-get "${apt_options[@]}" --simulate install "${packages[@]}" > /run/deep32-package-plan; then
  echo 'A pinned package, such as the Ubuntu signed NVIDIA module, is absent from the signed offline repositories. Stopping; DKMS is never substituted.' >&2
  exit 2
fi
if grep -Eiq '(^Inst .*dkms|^Inst (cuda-drivers|nvidia-driver))' /run/deep32-package-plan; then
  echo 'Refusing DKMS or an unreviewed driver metapackage' >&2
  exit 3
fi
# docker.io's postinst can start dockerd immediately. Its NVIDIA runtime must be
# configured before that package is installed. Dedicated ISO installs retain
# Docker's default networking. WSL shares a network namespace with other distros.
mkdir -p /etc/docker
if [[ "$form" == wsl ]]; then
  cat > /etc/docker/daemon.json.new <<'EOF'
{
  "bridge": "deep32br0",
  "iptables": false,
  "ip6tables": false,
  "default-address-pools": [{"base": "10.213.0.0/17", "size": 24}],
  "runtimes": {
    "nvidia": {"path": "nvidia-container-runtime", "runtimeArgs": []}
  }
}
EOF
  # The bridge subnet is outside the pools used by Compose networks. Each WSL
  # distribution shares the network namespace, so this bridge belongs to us.
  mkdir -p /usr/local/sbin /etc/systemd/system/docker.service.d
  cat > /usr/local/sbin/deep32-bridge <<'EOF'
#!/bin/sh
set -eu
if ! ip link show dev deep32br0 >/dev/null 2>&1; then
  ip link add name deep32br0 type bridge
fi
if ! ip -4 addr show dev deep32br0 | grep -q 'inet '; then
  ip addr add 10.213.255.1/24 dev deep32br0
fi
ip link set dev deep32br0 up
EOF
  chmod 0755 /usr/local/sbin/deep32-bridge
  cat > /etc/systemd/system/docker.service.d/deep32-bridge.conf <<'EOF'
[Service]
ExecStartPre=/usr/local/sbin/deep32-bridge
EOF
  systemctl daemon-reload
else
  cat > /etc/docker/daemon.json.new <<'EOF'
{
  "runtimes": {
    "nvidia": {"path": "nvidia-container-runtime", "runtimeArgs": []}
  }
}
EOF
fi
mv -f /etc/docker/daemon.json.new /etc/docker/daemon.json
DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" -y --no-install-recommends install "${packages[@]}"
if [[ "$form" == iso ]]; then
  python3 "$kit_dir/firstboot/installed-inputs.py" signed-module
fi
# Do not start or sign in Tailscale in the image builder. Enable it only in the installed system.
systemctl enable docker.service
if [[ "$form" == iso ]]; then
  systemctl enable tailscaled.service
else
  systemctl mask tailscaled.service
fi
