#!/usr/bin/env bash
# deep32-setup.sh: set up one machine for Deep32 with one command.
#
#   bash install/deep32-setup.sh              work out what this machine should be and do it
#   bash install/deep32-setup.sh check        only check this machine and say what to fix
#   bash install/deep32-setup.sh forge        GPU host: check, fix, download and install the Forge, show the code
#   bash install/deep32-setup.sh workstation  VM host: download and verify discs 1 and 2, write the VM settings
#   bash install/deep32-setup.sh headend      agent PC: connect Claude Code or Codex to the Workstation
#
# Options: --yes (accept the safe fixes without asking), --data-root DIR (Forge data, default /opt/deep32),
#          --name NAME (Tailscale name to use when the current one doesn't start with deep32-),
#          --workstation HOST (forge: pair over SSH when the Workstation allows root SSH with your key;
#                          headend: the https://.../mcp address the Workstation printed at publication),
#          --agent claude|codex|both (headend), --hypervisor kvm|hyper-v (workstation, default kvm).
#
# The script stops only for what a person has to do: sign in to Tailscale in a browser, accept the installer's plan,
# and type the pairing code on the Workstation (unless --workstation pairs over SSH). Every line it prints starts
# with a tag, so an agent running it can read the result cheaply:
#   [ok] passed   [fix] fixed or needs fixing   [you] a step for you   [fail] stopped   [info] progress
set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo=$(cd -- "$here/.." && pwd -P)
get="$repo/src/release-tools/deep32-get.sh"
role=''; yes=0; data_root=/opt/deep32; want_name=''; ws_host=''; agent=''; hypervisor=kvm
while (($#)); do
    case "$1" in
        check|forge|workstation|headend) role=$1 ;;
        --yes|-y) yes=1 ;;
        --data-root) data_root=${2:?}; shift ;;
        --name) want_name=${2:?}; shift ;;
        --workstation) ws_host=${2:?}; shift ;;
        --agent) agent=${2:?}; shift ;;
        --hypervisor) hypervisor=${2:?}; shift ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "[fail] unknown argument: $1 (try --help)"; exit 64 ;;
    esac
    shift
done

ok()   { echo "[ok] $*"; }
fix()  { echo "[fix] $*"; }
you()  { echo "[you] $*"; }
info() { echo "[info] $*"; }
fail() { echo "[fail] $*"; exit 1; }
problems=0
need() { problems=$((problems + 1)); fix "$*"; }
confirm() {  # confirm QUESTION: yes under --yes, else ask on the terminal
    [ "$role" = check ] && return 1  # check only reports; it never changes the machine
    [ "$yes" = 1 ] && return 0
    [ -t 0 ] || return 1
    local a; read -r -p "$1 [y/N] " a; case $a in [Yy]*) return 0 ;; *) return 1 ;; esac
}
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

ts_json() { tailscale status --json 2>/dev/null; }
ts_field() {  # ts_field EXPR: read one value from tailscale status --json with python3
    ts_json | python3 -I -c "import json,sys
d=json.load(sys.stdin); s=d.get('Self') or {}; t=d.get('CurrentTailnet') or {}
print($1)" 2>/dev/null
}

check_tailscale() {
    if ! command -v tailscale >/dev/null 2>&1; then
        need "Tailscale is not installed: curl -fsSL https://tailscale.com/install.sh | sh"; return
    fi
    local state; state=$(ts_field "d.get('BackendState','')")
    if [ "$state" != Running ]; then
        need "Tailscale is not signed in"
        if confirm "Start Tailscale sign-in now?"; then
            you "open the address Tailscale prints below in any browser and sign in with your account"
            if [ "$1" = forge ]; then as_root tailscale up --hostname "${want_name:-deep32-forge}"; else as_root tailscale up; fi
            state=$(ts_field "d.get('BackendState','')")
        fi
        [ "$state" = Running ] || return
    fi
    ok "Tailscale signed in"
    [ "$(ts_field "t.get('MagicDNSEnabled', False)")" = True ] && ok "MagicDNS on" ||
        need "turn on MagicDNS: admin console, DNS page"
    [ "$1" = forge ] || return 0  # only the Forge publishes services and has to be offered by name
    local host; host=$(ts_field "(s.get('DNSName') or '').split('.')[0]")
    case $host in
        deep32-*) ok "Tailscale name $host starts with deep32-" ;;
        *)  local new=${want_name:-deep32-forge}
            need "Tailscale name '$host' must start with deep32- for the Workstation to offer it"
            if confirm "Rename this machine to $new on your tailnet?"; then
                as_root tailscale set --hostname "$new" && { problems=$((problems - 1)); ok "renamed to $new"; } ||
                    fix "rename it on the admin console's Machines page"
            fi ;;
    esac
    [ -n "$(ts_field "' '.join(d.get('CertDomains') or [])")" ] && ok "HTTPS certificates on" ||
        need "turn on HTTPS certificates: admin console, DNS page, HTTPS Certificates"
}

check_common() {
    command -v python3 >/dev/null 2>&1 &&
        python3 -I -c 'import sys, ssl; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null &&
        ok "python3 $(python3 -I -c 'import sys; print("%d.%d" % sys.version_info[:2])') for the download tools" ||
        need "python3 3.10 or newer with ssl is needed on PATH"
    command -v gpgv >/dev/null 2>&1 && ok "gpgv for the signature checks" ||
        need "install gpgv (apt install gpgv, dnf install gnupg2, brew install gnupg)"
    command -v curl >/dev/null 2>&1 || need "install curl"
}

gpu_class() {  # print the Deep32 GPU class of the best NVIDIA card, or nothing
    command -v nvidia-smi >/dev/null 2>&1 || return
    nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | sort -rV | head -1 | while read -r cc; do
        case $cc in 7.5) echo turing ;; 8.0) echo ampere80 ;; 8.6|8.7) echo ampere86 ;; 8.9) echo ada ;;
            9.*) echo hopper ;; 10.*) echo blackwell100 ;; 12.*) echo blackwell120 ;; esac
    done
}

vram_floor_mib() {  # the smallest card the Forge supports: install/matrix.json, the first gpu_mem_tiers row
    python3 -I -c "import json,sys; t=json.load(open(sys.argv[1]))['gpu_mem_tiers'][0]; print(int(round(float(t['min_gb'])*1024)))" \
        "$here/matrix.json" 2>/dev/null || echo 10752
}

in_wsl() { grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; }

check_forge() {
    [ "$(uname -s)" = Linux ] || { need "this is the Linux form; on Windows run: powershell -NoProfile -ExecutionPolicy Bypass -File install\\deep32-setup.ps1 forge"; return; }
    check_common
    command -v python3 >/dev/null 2>&1 && python3 -I -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' 2>/dev/null &&
        ok "python3 is 3.12 or newer for the Forge installer" ||
        need "the Forge installer needs python3 3.12 or newer (Ubuntu 24.04 has it)"
    if in_wsl; then
        info "running in WSL 2: the Forge uses Docker Engine inside this distro and the GPU through WSL"
        [ "$(ps -p 1 -o comm= 2>/dev/null)" = systemd ] && ok "systemd is running (Docker needs it)" ||
            need "turn on systemd: add '[boot]' and 'systemd=true' to /etc/wsl.conf, then from Windows run: wsl --terminate <this distro>"
    fi
    local class; class=$(gpu_class)
    if [ -n "$class" ]; then
        ok "$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1), class $class"
        local drv; drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
        [ "$(printf '%s\n580.65.06\n' "$drv" | sort -V | head -1)" = 580.65.06 ] && ok "driver $drv" ||
            need "NVIDIA driver $drv is older than 580.65.06; update it"
        local mib floor; floor=$(vram_floor_mib)
        mib=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -rn | head -1)
        [ "${mib:-0}" -ge "$floor" ] && ok "$mib MiB of GPU memory (the Forge needs $floor)" ||
            need "the card has ${mib:-unknown} MiB of GPU memory; the Forge needs at least $floor MiB (docs/HARDWARE.md)"
    else
        need "no supported NVIDIA card found (compute capability 7.5 or newer); this machine can't be a Forge"
    fi
    if command -v docker >/dev/null 2>&1 && as_root docker info >/dev/null 2>&1; then
        ok "Docker running"
        as_root docker compose version >/dev/null 2>&1 && ok "Docker Compose v2" ||
            need "install the Docker Compose v2 plugin (docker compose)"
        case $(as_root docker info --format '{{.Driver}} {{json .DriverStatus}}' 2>/dev/null) in
            *io.containerd.snapshotter*|overlayfs*)
                need "Docker keeps images in its containerd store; the alpha.2 Forge loads only into the classic store (overlay2). To switch, add \"features\": {\"containerd-snapshotter\": false} to /etc/docker/daemon.json and restart Docker. Images and containers in the containerd store stay hidden until you switch back" ;;
            '') ;;
            *) ok "Docker uses the classic image store" ;;
        esac
        as_root docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia && ok "NVIDIA Container Toolkit" ||
            need "install the NVIDIA Container Toolkit, then: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
    else
        need "Docker is not installed or not running: https://docs.docker.com/engine/install/"
    fi
    if ! sh "$here/forge-host-python.sh" >/dev/null 2>&1; then
        need "the pairing helper needs /usr/bin/python3 3.10+ with ssl"
        if confirm "Install it with your package manager?"; then
            as_root sh "$here/forge-host-python.sh" --install | sed 's/^/[info] /'
        else
            sh "$here/forge-host-python.sh" | sed 's/^/[info] /'
        fi
        sh "$here/forge-host-python.sh" >/dev/null 2>&1 && { problems=$((problems - 1)); ok "pairing helper Python"; }
    else
        ok "pairing helper Python"
    fi
    check_tailscale forge
}

pair_code() {  # print the Forge's current pairing code, if a window is open
    as_root grep -o 'pair=[A-Z2-7]\{10\}' "$data_root/status/status" 2>/dev/null | head -1 | cut -d= -f2
}

do_forge() {
    check_forge
    [ "$problems" -eq 0 ] || fail "fix the [fix] lines above, then run this again"
    local class; class=$(gpu_class)
    info "downloading, verifying and installing the GPU pack for $class into $data_root"
    you "the installer shows its plan (folders, ports, images, sizes) and waits for you to accept it"
    as_root bash "$get" disc3 --gpu-class "$class" --install --journey J2 --data-root "$data_root" ||
        fail "the Forge install stopped; its message above names the cause"
    ok "Forge installed and running"
    local name; name=$(ts_field "(s.get('DNSName') or '').rstrip('.')")
    for port in 443 8443; do
        local code; code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://$name:$port/health")
        [ "$code" = 401 ] && ok "door answers on https://$name:$port and asks for a token" ||
            need "https://$name:$port answered '$code' instead of 401"
    done
    local code='' i
    for i in $(seq 1 30); do code=$(pair_code); [ -n "$code" ] && break; sleep 2; done
    [ -n "$code" ] || fail "no pairing window opened; if the Forge is already paired, it's done"
    if [ -n "$ws_host" ]; then
        info "pairing with the Workstation $ws_host over SSH"
        local out; out=$(printf 'y\n%s\n\n' "$code" | ssh -o BatchMode=yes "root@$ws_host" /usr/local/d32/sbin/d32-pairdesk -forge 2>&1)
        case $out in *"Paired."*) ok "paired with $ws_host"; return 0 ;; esac
        need "pairing over SSH didn't finish; pair on the Workstation console instead"
    fi
    you "on the Workstation: open Pairing, choose Forge, pick $(ts_field "(s.get('DNSName') or '').split('.')[0]") and type the code $code (valid five minutes)"
    you "if the code expires, run this script again to see the next one"
}

do_workstation() {
    check_common  # the VM host itself needs no Tailscale: the Workstation VM joins your tailnet at its first boot
    local dir="$repo/../deep32-downloads"
    info "downloading and verifying disc 1 (Workstation) and disc 2 (content) into $dir"
    bash "$get" disc1 --to "$dir" || fail "disc 1 download stopped"
    bash "$get" disc2 --to "$dir" || fail "disc 2 download stopped"
    bash "$get" disc1 --to "$dir" --prepare-vm --hypervisor "$hypervisor" || fail "writing the VM settings stopped"
    you "create the VM with the commands above, boot it from disc 1, and answer the installer's questions (INSTALL.md section 6)"
    you "at first boot, set the passwords, sign in to Tailscale with the address shown, and leave disc 2 in the drive for the import"
}

do_headend() {
    check_common
    check_tailscale headend
    if [ -z "$agent" ]; then
        local c=0 x=0; command -v claude >/dev/null 2>&1 && c=1; command -v codex >/dev/null 2>&1 && x=1
        case $c$x in 11) agent=both ;; 01) agent=codex ;; 10) agent=claude ;; *) fail "install Claude Code or Codex first" ;; esac
    fi
    case $ws_host in
        https://*/mcp) ;;
        *) fail "pass the gateway address the Workstation printed when you published it: --workstation https://<name>.<tailnet>.ts.net/mcp (on the Workstation, as root: d32-publish publish --accept-ct)" ;;
    esac
    you "Connect asks for a ten-character code: on the Workstation open Pairing and choose Headend, then type the code the Workstation shows into Connect here"
    bash "$repo/adapters/connect.sh" --agent "$agent" --workstation "$ws_host" || fail "connecting the agent stopped"
}

guess_role() {
    if [ -n "$(gpu_class)" ] && [ "$(uname -s)" = Linux ]; then echo forge
    elif command -v claude >/dev/null 2>&1 || command -v codex >/dev/null 2>&1; then echo headend
    else echo workstation; fi
}

[ -n "$role" ] || { role=$(guess_role); info "this machine looks like a $role host (name another role to override)"; }
case $role in
    check) check_forge; [ "$problems" -eq 0 ] && ok "ready for the Forge" || fail "$problems thing(s) to fix" ;;
    forge) do_forge ;;
    workstation) do_workstation ;;
    headend) do_headend ;;
esac
