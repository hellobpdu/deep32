"""Writes the inventory verdict fixtures (fake machine facts + hand-worked expectations).

Each expectation below was worked out by hand from install/matrix.json (matrix 0.2.0) before any
engine ran, and each fixture carries its working as "reasoning": the processor and card classes,
the sizing, every candidate profile with what it misses, and the order. The order is the matrix's
"ranking" rule: fit first (ready, then unconfirmed, then changes), then the profile's rank, then
fewer missing requirements, then matrix order. Requirements of kind "confirm" (a second machine, a
Forge or a headend elsewhere: no machine can check them) are listed in "confirm" and never lower
the fit; missing "auto" requirements are listed in "auto" and never lower it either.

2026-11-14 (matrix 0.3.0, "single machine first"): profiles with "machines": 2
(two-machines, baremetal, headend-only: the layout needs another machine for the Workstation or the
Forge) are alternatives. Every possible one-machine profile, whatever its fit, now comes before every
alternative; the order above holds within each group. So an alternative is the best only when no
one-machine profile can run on that hardware (mac-arm, win-celeron, sparse-unknowns, latitude-5430).
The expectations of the ten fixtures whose order changed were worked again by hand (each says so in
its reasoning, with the old order); the others keep their order and say why.

Usage:
  python3 -I make_fixtures.py OUTDIR   (normally: fixtures)

Exit codes:
  0  written
  1  an error (Python prints the reason)
"""
import json
import os
import sys

OUT = sys.argv[1]


def osw(name, edition, version, build):
    return {"family": "windows", "name": name, "edition": edition, "version": version, "build": build,
            "arch": "x64", "wsl_guest": False}


def osl(name, edition, version, wsl=False):
    return {"family": "linux", "name": name, "edition": edition, "version": version, "build": None,
            "arch": "x64", "wsl_guest": wsl, "kernel": None}


def cpu(vendor, model, cores, threads, avx, avx2, avx512, p_cores=None, e_cores=0, p_threads=None,
        family=None, model_id=None, stepping=None, sockets=1):
    return {"vendor": vendor, "model": model, "family": family, "model_id": model_id, "stepping": stepping,
            "gen": None, "sockets": sockets, "cores": cores, "threads": threads, "p_cores": p_cores,
            "e_cores": e_cores, "p_threads": p_threads, "avx": avx, "avx2": avx2, "avx512f": avx512}


def gpu(name, vendor, mem, driver, cc):
    return {"name": name, "vendor": vendor, "mem_gb": mem, "driver": driver, "cc": cc}


def hyp(hyperv=False, kvm=False, proxmox=False, vbox=False, vmware=False, libvirt=False):
    return {"hyperv": hyperv, "kvm": kvm, "proxmox": proxmox, "virtualbox": vbox, "vmware": vmware, "libvirt": libvirt}


def tools(ts=False, cl=False, cx=False, gh=False):
    return {"tailscale": ts, "claude": cl, "codex": cx, "gh": gh}


def ports(busy, checked=True):
    return {"checked": checked, "busy": busy}


def facts(os_, hw, cpu_, mem, disks, gpus, virt, hyp_, wsl, docker, tools_, ports_):
    return {"os": os_, "hw": hw, "cpu": cpu_, "mem": mem, "disks": disks, "gpus": gpus, "virt": virt,
            "hyp": hyp_, "wsl": wsl, "docker": docker, "tools": tools_, "ports": ports_, "net": None}


def virt(fw, slat, iommu, hv):
    return {"firmware": fw, "slat": slat, "iommu": iommu, "hypervisor": hv}


def disk(mount, size, free, ssd):
    return {"mount": mount, "size_gb": size, "free_gb": free, "ssd": ssd}


def docker(present, client=None, nvidia=None):
    return {"present": present, "client": client, "nvidia": nvidia}


CASES = []


def case(name, about, f, expect, reasoning):
    CASES.append((name, {"name": name, "about": about, "facts": f, "reasoning": reasoning, "expect": expect}))


case("win-pro-rtx2080ti", "Windows 11 Pro, i5-10600K, 48 GB, RTX 2080 Ti 11 GB, Hyper-V and WSL2, two Deep32 ports busy",
     facts(osw("Windows 11 Pro", "pro", "24H2", 26100),
           {"vendor": "ASRock", "model": "Z490 Steel Legend", "virtual": False},
           cpu("intel", "Intel(R) Core(TM) i5-10600K CPU @ 4.10GHz", 6, 12, True, True, False, family=6, model_id=165, stepping=5),
           {"total_gb": 47.9, "installed_gb": 48, "free_gb": 21.6},
           [disk("C:", 476.3, 61.4, True), disk("D:", 1863.0, 142.7, True)],
           [gpu("NVIDIA GeForce RTX 2080 Ti", "nvidia", 11.0, "566.36", "7.5")],
           virt(True, True, None, True), hyp(hyperv=True), {"present": True, "wsl2_distros": 1},
           docker(False), tools("1.76.6", "2.1.0", "0.58.0", False), ports([7452, 8082])),
     {"best": "win-nvidia", "fit": "changes", "ranked": ["win-nvidia", "two-machines", "headend-only"],
      "best_missing": ["disk-both", "ports-relay", "ports-forge"], "best_unknown": [], "best_auto": [], "best_confirm": [],
      "cpu_class": "modern", "gpu_class": "rtx20", "gpu_forge": "yes", "gpu_mem_tier": "11", "tier": 2,
      "sizes": {"win-nvidia": {"ws": [6, 16], "forge": [4, 16]}, "two-machines": {"ws": [6, 16], "forge": [4, 16]},
                "headend-only": {"ws": None, "forge": None}},
      "ruled_out": [], "warnings": ["gpu-upstream-experimental", "ports-busy"]},
     ["Classes: the i5-10600K has AVX2, so modern (Workstation yes, Forge yes). 'nvidia geforce rtx 2080 ti' matches 'geforce rtx 20[6-8]0': rtx20 (Forge yes, upstream experimental), usable; 11 GB is memory tier 11 (10.5 or more).",
      "Sizing: 12 threads (no efficient cores) and 48 GB installed: tier 2 (12 threads, 32 GB; tier 3 needs 16 threads and 64 GB). After the host keeps 2 threads and 4 GB: 10 threads, 44 GB.",
      "Candidates (Windows Pro, x64): win-nvidia, two-machines, headend-only. no-gpu does not match because the card is usable; win-home (edition), virtualbox and vmware (not installed), baremetal (no known model) and esxi do not match.",
      "win-nvidia (machines 1): changes. Missing disk-both (most free space 142.7 GB < 160), ports-relay (7452 busy), ports-forge (8082 busy); agent met (Claude Code), Tailscale present. Size 'both': threads (6, 4) fit 10, memory (16, 16) fit 44.",
      "two-machines (machines 2, an alternative): ready. cpu-supported and one-side met; second-machine cannot be checked, so it is listed in confirm; Tailscale present. Size 'each': Workstation (6, 16) from tier 2, Forge (4, 16) from tier 2.",
      "headend-only (machines 2, an alternative): ready. Claude Code present (agent met), Tailscale present. No size.",
      "Order (single machine first, matrix 0.3.0; before, it was two-machines, headend-only, win-nvidia): the one-machine win-nvidia can run here (it needs changes, no hardware), so it comes first whatever its fit; then the alternatives, both ready, by rank: two-machines 40, headend-only 90.",
      "Warnings: gpu-upstream-experimental (Forge yes, upstream experimental) and ports-busy (2 busy)."])

case("win-home-gtx1650", "Windows 11 Home laptop, Ryzen 5 3550H, 16 GB, GTX 1650 4 GB, VirtualBox, no Hyper-V",
     facts(osw("Windows 11 Home", "home", "23H2", 22631),
           {"vendor": "ASUSTeK COMPUTER INC.", "model": "TUF Gaming FX505DT", "virtual": False},
           cpu("amd", "AMD Ryzen 5 3550H with Radeon Vega Mobile Gfx", 4, 8, True, True, False, family=23, model_id=24, stepping=1),
           {"total_gb": 15.9, "installed_gb": 16, "free_gb": 6.0},
           [disk("C:", 476.0, 120.5, True)],
           [gpu("NVIDIA GeForce GTX 1650", "nvidia", 4.0, "566.36", "7.5"),
            gpu("AMD Radeon(TM) Vega 8 Graphics", "amd", 2.0, "31.0.21921.1000", None)],
           virt(True, True, None, False), hyp(vbox=True), {"present": False, "wsl2_distros": 0},
           docker(False), tools(False, False, "0.58.0", False), ports([])),
     {"best": "win-home", "fit": "ready", "ranked": ["win-home", "virtualbox", "two-machines"],
      "best_missing": [], "best_unknown": [], "best_auto": ["tailscale"], "best_confirm": [],
      "cpu_class": "modern", "gpu_class": "gtx16", "gpu_forge": "experimental", "gpu_mem_tier": "0", "tier": 0,
      "sizes": {"win-home": {"ws": [4, 8], "forge": None}, "virtualbox": {"ws": [4, 8], "forge": None},
                "two-machines": {"ws": [4, 8], "forge": [2, 12]}},
      "ruled_out": [], "warnings": ["gpu-experimental", "gpu-mem-low"]},
     ["Classes: the Ryzen 5 3550H has AVX2: modern. 'nvidia geforce gtx 1650' first matches 'geforce gtx 16[0-9][0-9]': gtx16 (Forge experimental), usable; 4 GB is memory tier 0. The Radeon is not NVIDIA and is not classed.",
      "Sizing: 8 threads, 16 GB installed: tier 0 (tier 1 needs 32 GB). After the host: 6 threads, 12 GB, so the Workstation alone (4, 8) and the Forge alone (2, 12) fit, both together does not.",
      "Candidates (Windows Home, x64): win-home, two-machines, virtualbox (VirtualBox installed), headend-only. win-nvidia and no-gpu need Pro, Enterprise or Education.",
      "win-home: ready. type2-hypervisor met by VirtualBox, disk-ws met (120.5 >= 60); Tailscale not found, so auto. Size 'ws': (4, 8).",
      "two-machines: ready (second-machine in confirm, Tailscale auto). Size 'each': Workstation (4, 8); tier 0 has no Forge range, so the Forge minimum (2, 12), which fits.",
      "virtualbox: ready. Size 'ws': (4, 8). headend-only: ready (Codex present).",
      "Order (single machine first, matrix 0.3.0; before, it was win-home, two-machines, virtualbox): all four ready; the one-machine profiles first by rank, win-home 20 and virtualbox 60, then the alternatives (machines 2) by rank, two-machines 40 (third) and headend-only 90 (fourth, not shown).",
      "Warnings: gpu-experimental, gpu-mem-low (usable card, tier 0)."])

case("proxmox-ivybridge-rtx3060", "Proxmox VE on a two-socket Xeon E5 v2 (AVX, no AVX2), 256 GB, RTX 3060 bound to vfio-pci (no nvidia-smi)",
     facts({"family": "linux", "name": "Debian GNU/Linux 12 (bookworm)", "edition": "debian", "version": "12", "build": None,
            "arch": "x64", "wsl_guest": False, "kernel": "6.8.12-4-pve"},
           {"vendor": "Dell Inc.", "model": "PowerEdge R720", "virtual": False},
           cpu("intel", "Intel(R) Xeon(R) CPU E5-2690 v2 @ 3.00GHz", 20, 40, True, False, False, family=6, model_id=62, stepping=4, sockets=2),
           {"total_gb": 251.8, "installed_gb": None, "free_gb": 200.0},
           [disk("/", 94.0, 70.1, True), disk("/var/lib/vz", 1800.0, 1500.0, False)],
           [gpu("NVIDIA GeForce RTX 3060", "nvidia", None, None, None)],
           virt(True, True, True, False), hyp(kvm=True, proxmox=True), None,
           docker(False, None, False), tools(), ports([])),
     {"best": "proxmox-passthrough", "fit": "unconfirmed", "ranked": ["proxmox-passthrough", "headless", "two-machines"],
      "best_missing": [], "best_unknown": ["gpu-mem"], "best_auto": [], "best_confirm": ["headend-elsewhere"],
      "cpu_class": "avx", "gpu_class": "rtx30", "gpu_forge": "yes", "gpu_mem_tier": None, "tier": 3,
      "sizes": {"proxmox-passthrough": {"ws": [8, 16], "forge": [8, 32]}, "headless": {"ws": None, "forge": [8, 32]},
                "two-machines": {"ws": [8, 16], "forge": [8, 32]}},
      "ruled_out": [], "warnings": ["cpu-forge-untested"]},
     ["Classes: the E5-2690 v2 has AVX but not AVX2 and is not low-power: avx (Workstation yes, Forge to-be-tested). The RTX 3060 is rtx30 (Forge yes, upstream stable), usable; its memory and driver cannot be read (vfio-pci), so no memory tier.",
      "Sizing: 40 threads; no installed figure, 251.8 GB visible, half rounded up (126) doubled: 252 GB; tier 3. After the host: 38 threads, 248 GB.",
      "Candidates (Linux with Proxmox): proxmox-passthrough, two-machines, headless, headend-only. linux-nvidia excludes Proxmox; no-gpu does not match because the card is usable.",
      "proxmox-passthrough: unconfirmed. gpu-mem unknown (card memory unreadable); headend-elsewhere in confirm; KVM, IOMMU, sizes and disk (1500 GB) met. Size 'both': threads (8, 8) fit 38, memory (16, 32) fit 248.",
      "two-machines: ready (second-machine in confirm, Tailscale auto). Size 'each': (8, 16) and Forge (8, 32).",
      "headless: changes. Missing nvidia-driver (none read), docker, nvidia-runtime; gpu-mem unknown; Tailscale auto. Size 'forge': (8, 32).",
      "headend-only: changes (agent missing: neither Claude Code nor Codex).",
      "Order (single machine first, matrix 0.3.0; before, it was two-machines, proxmox-passthrough, headless): one-machine profiles first, by fit: unconfirmed proxmox-passthrough (machines 1; the headend elsewhere does not count as a second machine), then changes headless; then the alternatives (machines 2): ready two-machines, changes headend-only (fourth, not shown). First three: proxmox-passthrough, headless, two-machines.",
      "Warnings: cpu-forge-untested."])

case("proxmox-no-gpu", "The same Proxmox host with only its onboard Matrox graphics",
     facts({"family": "linux", "name": "Debian GNU/Linux 12 (bookworm)", "edition": "debian", "version": "12", "build": None,
            "arch": "x64", "wsl_guest": False, "kernel": "6.8.12-4-pve"},
           {"vendor": "Dell Inc.", "model": "PowerEdge R720", "virtual": False},
           cpu("intel", "Intel(R) Xeon(R) CPU E5-2690 v2 @ 3.00GHz", 20, 40, True, False, False, sockets=2),
           {"total_gb": 251.8, "installed_gb": None, "free_gb": 200.0},
           [disk("/", 94.0, 70.1, True), disk("/var/lib/vz", 1800.0, 1500.0, False)],
           [gpu("Matrox Electronics Systems Ltd. G200eR2", "other", None, None, None)],
           virt(True, True, True, False), hyp(kvm=True, proxmox=True), None,
           docker(False, None, False), tools(), ports([])),
     {"best": "no-gpu", "fit": "ready", "ranked": ["no-gpu", "two-machines", "headend-only"],
      "best_missing": [], "best_unknown": [], "best_auto": ["tailscale"], "best_confirm": [],
      "cpu_class": "avx", "gpu_class": None, "gpu_forge": None, "gpu_mem_tier": None, "tier": 3,
      "sizes": {"no-gpu": {"ws": [8, 16], "forge": None}, "two-machines": {"ws": [8, 16], "forge": [8, 32]},
                "headend-only": {"ws": None, "forge": None}},
      "ruled_out": ["proxmox-passthrough", "headless"], "warnings": ["cpu-forge-untested"]},
     ["Classes: avx as on the RTX 3060 host. Only a Matrox card: no NVIDIA card, so gpu.usable is false and there is no card class.",
      "Sizing: tier 3; 38 threads and 248 GB after the host.",
      "Candidates: proxmox-passthrough, no-gpu (no usable card), two-machines, headless, headend-only.",
      "proxmox-passthrough and headless: gpu-cuda missing (hardware): ruled out.",
      "no-gpu: ready (KVM is the hypervisor, disk 1500 GB, Tailscale auto). Size 'ws': (8, 16).",
      "two-machines: ready (one-side met by the room for the Workstation; second-machine in confirm). Size (8, 16) and Forge (8, 32).",
      "headend-only: changes (agent missing).",
      "Order: the one-machine no-gpu (ready) first; then the alternatives (machines 2): ready two-machines 40, changes headend-only 90. Unchanged by the single-machine-first order of matrix 0.3.0.",
      "Warnings: cpu-forge-untested."])

case("linux-ryzen-rtx4090", "Ubuntu 24.04, Ryzen 9 7950X, 128 GB, RTX 4090, KVM, Docker with the NVIDIA toolkit",
     facts(osl("Ubuntu 24.04.1 LTS", "ubuntu", "24.04"),
           {"vendor": "Micro-Star International Co., Ltd.", "model": "MS-7E12", "virtual": False},
           cpu("amd", "AMD Ryzen 9 7950X 16-Core Processor", 16, 32, True, True, True, family=25, model_id=97, stepping=2),
           {"total_gb": 124.9, "installed_gb": None, "free_gb": 100.0},
           [disk("/", 1863.0, 1500.0, True)],
           [gpu("NVIDIA GeForce RTX 4090", "nvidia", 24.0, "560.35.03", "8.9")],
           virt(True, True, True, False), hyp(kvm=True, libvirt=True), None,
           docker(True, "27.3.1", True), tools("1.76.6", "2.1.0", False, "2.62.0"), ports([])),
     {"best": "linux-nvidia", "fit": "ready", "ranked": ["linux-nvidia", "headless", "two-machines"],
      "best_missing": [], "best_unknown": [], "best_auto": [], "best_confirm": ["headend-elsewhere"],
      "cpu_class": "modern", "gpu_class": "rtx40", "gpu_forge": "yes", "gpu_mem_tier": "11", "tier": 3,
      "sizes": {"linux-nvidia": {"ws": [8, 16], "forge": [8, 32]}, "headless": {"ws": None, "forge": [8, 32]},
                "two-machines": {"ws": [8, 16], "forge": [8, 32]}},
      "ruled_out": [], "warnings": []},
     ["Classes: the Ryzen 9 7950X has AVX2: modern. The RTX 4090 is rtx40 (Forge yes, upstream stable), usable; 24 GB is tier 11.",
      "Sizing: 32 threads; 124.9 GB visible: 126 GB; tier 3. After the host: 30 threads, 122 GB.",
      "Candidates (Linux, not Proxmox): linux-nvidia, two-machines, headless, headend-only. no-gpu does not match because the card is usable.",
      "linux-nvidia: ready. KVM, driver, Docker, toolkit, disk (1500 GB) and ports all met; headend-elsewhere cannot be checked, so it is in confirm and does not lower the fit. Size 'both': threads (8, 8) fit 30, memory (16, 32) fit 122.",
      "two-machines: ready. Size (8, 16) and Forge (8, 32). headless: ready (every check met). Size 'forge': (8, 32). headend-only: ready (Claude Code).",
      "Order (single machine first, matrix 0.3.0; before, it was linux-nvidia, two-machines, headless): all ready; the one-machine profiles first by rank, linux-nvidia 10 and headless 50, then the alternatives (machines 2) by rank, two-machines 40 (third) and headend-only 90 (fourth, not shown).",
      "Warnings: none."])

case("linux-cloud-t4", "A cloud VM: Ubuntu 22.04, 8 vCPU, 32 GB, Tesla T4, Docker with the toolkit, no KVM",
     facts(osl("Ubuntu 22.04.4 LTS", "ubuntu", "22.04"),
           {"vendor": "Google", "model": "Google Compute Engine", "virtual": True},
           cpu("intel", "Intel(R) Xeon(R) CPU @ 2.20GHz", 4, 8, True, True, False),
           {"total_gb": 31.4, "installed_gb": None, "free_gb": 28.0},
           [disk("/", 96.7, 80.2, None)],
           [gpu("Tesla T4", "nvidia", 15.0, "550.90.07", "7.5")],
           virt(None, None, None, True), hyp(), None,
           docker(True, "26.1.3", True), tools(), ports([])),
     {"best": "linux-nvidia", "fit": "changes", "ranked": ["linux-nvidia", "headless", "two-machines"],
      "best_missing": ["kvm", "disk-both"], "best_unknown": ["virt-slat", "virt-firmware"], "best_auto": ["tailscale"],
      "best_confirm": ["headend-elsewhere"],
      "cpu_class": "modern", "gpu_class": "t4", "gpu_forge": "yes", "gpu_mem_tier": "11", "tier": 1,
      "sizes": {"linux-nvidia": {"ws": [4, 12], "forge": [2, 16]}, "headless": {"ws": None, "forge": [4, 16]},
                "two-machines": {"ws": [4, 12], "forge": [4, 16]}},
      "ruled_out": [], "warnings": ["gpu-upstream-experimental", "is-vm"]},
     ["Classes: the Xeon has AVX2: modern. 'tesla t4' is t4 (Forge yes, upstream experimental), usable; 15 GB is tier 11.",
      "Sizing: 8 threads; 31.4 GB visible: 32 GB; tier 1. After the host: 6 threads, 28 GB.",
      "Candidates: linux-nvidia, two-machines, headless, headend-only (no-gpu: the card is usable).",
      "linux-nvidia: changes. Missing kvm (no KVM on this VM) and disk-both (80.2 < 160); virt-slat and virt-firmware unknown; headend-elsewhere in confirm; Tailscale auto. Size 'both': threads (4, 4) need 8 > 6, (4, 2) fit; memory (12, 16) fit 28: Workstation (4, 12), Forge (2, 16).",
      "two-machines: ready. Size 'each' from tier 1: Workstation (4, 12), Forge (4, 16).",
      "headless: changes. Missing disk-forge (80.2 < 100). Size 'forge': (4, 16). headend-only: changes (agent missing).",
      "Order (single machine first, matrix 0.3.0; before, it was two-machines, linux-nvidia, headless): the one-machine profiles first, both changes, by rank: linux-nvidia 10, headless 50; then the alternatives (machines 2): ready two-machines (third), changes headend-only (fourth, not shown). The best, linux-nvidia, lists missing kvm and disk-both (requires order), unknown virt-slat and virt-firmware, Tailscale in auto, headend-elsewhere in confirm.",
      "Warnings: gpu-upstream-experimental, is-vm (solid-state unknown, so no no-ssd)."])

case("mac-arm", "A MacBook with Apple M2 (4 fast, 4 efficient cores), 16 GB",
     facts({"family": "macos", "name": "macOS 15.1", "edition": "macos", "version": "15.1", "build": None,
            "arch": "arm64", "wsl_guest": False},
           {"vendor": "Apple", "model": "Mac14,2", "virtual": False},
           cpu("apple", "Apple M2", 8, 8, None, None, None, p_cores=4, e_cores=4, p_threads=4),
           {"total_gb": 16.0, "installed_gb": None, "free_gb": 5.0},
           [disk("/", 460.4, 210.0, None)],
           [gpu("Apple M2", "apple", None, None, None)],
           virt(True, True, None, False), hyp(), None,
           docker(False), tools("1.76.1", "2.1.0", "0.58.0", "2.62.0"), ports([])),
     {"best": "headend-only", "fit": "ready", "ranked": ["headend-only"],
      "best_missing": [], "best_unknown": [], "best_auto": [], "best_confirm": [],
      "cpu_class": "arm", "gpu_class": None, "gpu_forge": None, "gpu_mem_tier": None, "tier": 0,
      "sizes": {"headend-only": {"ws": None, "forge": None}},
      "ruled_out": [], "warnings": ["hybrid-cores"]},
     ["Classes: arm64 is the arm class (Workstation no, Forge no). Apple graphics: no NVIDIA card.",
      "Sizing: 4 fast threads (hybrid); 16 GB visible: 16 GB; tier 0.",
      "Candidates: only headend-only matches macOS; every other profile needs Windows, Linux or ESXi, or a known hardware model.",
      "headend-only: ready (Claude Code and Codex present, Tailscale present).",
      "Order: headend-only is an alternative (machines 2) and is the best because no one-machine profile can run here (none matches macOS). Unchanged by the single-machine-first order of matrix 0.3.0.",
      "Warnings: hybrid-cores."])

case("win-pro-hybrid-radeon", "Windows 11 Pro, i5-12600K (6 fast, 4 efficient cores), 32 GB, Radeon RX 6700 XT, Hyper-V",
     facts(osw("Windows 11 Pro", "pro", "24H2", 26100),
           {"vendor": "Micro-Star International Co., Ltd.", "model": "MS-7D25", "virtual": False},
           cpu("intel", "12th Gen Intel(R) Core(TM) i5-12600K", 10, 16, True, True, False, p_cores=6, e_cores=4, p_threads=12),
           {"total_gb": 31.8, "installed_gb": 32, "free_gb": 20.0},
           [disk("C:", 931.5, 400.0, True)],
           [gpu("AMD Radeon RX 6700 XT", "amd", 12.0, "31.0.24027.1012", None),
            gpu("Intel(R) UHD Graphics 770", "intel", 1.0, "31.0.101.4502", None)],
           virt(True, True, True, True), hyp(hyperv=True), {"present": True, "wsl2_distros": 0},
           docker(False), tools(False, "2.1.0", False, False), ports([])),
     {"best": "no-gpu", "fit": "ready", "ranked": ["no-gpu", "two-machines", "headend-only"],
      "best_missing": [], "best_unknown": [], "best_auto": ["tailscale"], "best_confirm": [],
      "cpu_class": "modern", "gpu_class": None, "gpu_forge": None, "gpu_mem_tier": None, "tier": 2,
      "sizes": {"no-gpu": {"ws": [6, 16], "forge": None}, "two-machines": {"ws": [6, 16], "forge": [4, 16]},
                "headend-only": {"ws": None, "forge": None}},
      "ruled_out": ["win-nvidia"], "warnings": ["hybrid-cores"]},
     ["Classes: the i5-12600K has AVX2: modern. Radeon and Intel graphics: no NVIDIA card, gpu.usable false.",
      "Sizing: 12 fast threads (6 fast cores, 4 efficient: sized by the fast threads); 32 GB: tier 2. After the host: 10 threads, 28 GB.",
      "Candidates: win-nvidia, no-gpu (no usable card), two-machines, headend-only.",
      "win-nvidia: gpu-cuda missing (hardware): ruled out.",
      "no-gpu: ready (Hyper-V on, disk 400 GB, Tailscale auto). Size 'ws': (6, 16).",
      "two-machines: ready. Size (6, 16) and Forge (4, 16). headend-only: ready (Claude Code).",
      "Order: the one-machine no-gpu first; then the alternatives (machines 2) by rank: two-machines 40, headend-only 90. All ready. Unchanged by the single-machine-first order of matrix 0.3.0.",
      "Warnings: hybrid-cores."])

case("win-pro-virt-off-rtx3070", "Windows 11 Pro, i7-10700, 32 GB, RTX 3070 8 GB; virtualisation off in firmware, no Hyper-V, no WSL",
     facts(osw("Windows 11 Pro", "pro", "23H2", 22631),
           {"vendor": "Dell Inc.", "model": "XPS 8940", "virtual": False},
           cpu("intel", "Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz", 8, 16, True, True, False),
           {"total_gb": 31.9, "installed_gb": 32, "free_gb": 22.0},
           [disk("C:", 476.9, 210.0, True), disk("D:", 931.5, 900.0, False)],
           [gpu("NVIDIA GeForce RTX 3070", "nvidia", 8.0, "566.36", "8.6")],
           virt(False, True, None, False), hyp(), {"present": False, "wsl2_distros": 0},
           docker(False), tools(), ports([])),
     {"best": "win-nvidia", "fit": "changes", "ranked": ["win-nvidia", "two-machines", "headend-only"],
      "best_missing": ["virt-firmware", "hyperv", "wsl2", "agent"], "best_unknown": [], "best_auto": ["tailscale"],
      "best_confirm": [],
      "cpu_class": "modern", "gpu_class": "rtx30", "gpu_forge": "yes", "gpu_mem_tier": "8", "tier": 2,
      "sizes": {"win-nvidia": {"ws": [6, 12], "forge": [4, 16]}, "two-machines": {"ws": [6, 16], "forge": [4, 16]},
                "headend-only": {"ws": None, "forge": None}},
      "ruled_out": [], "warnings": ["gpu-mem-8"]},
     ["Classes: the i7-10700 has AVX2: modern. The RTX 3070 is rtx30 (Forge yes), usable; 8 GB is tier 8.",
      "Sizing: 16 threads, 32 GB: tier 2 (tier 3 needs 64 GB). After the host: 14 threads, 28 GB.",
      "Candidates: win-nvidia, two-machines, headend-only (no-gpu: the card is usable).",
      "win-nvidia: changes. Missing virt-firmware (off), hyperv, wsl2, agent; Tailscale auto. Size 'both': threads (6, 4) fit 14; memory (16, 16) = 32 > 28 twice, then (12, 16) fits: Workstation (6, 12), Forge (4, 16).",
      "two-machines: ready. Size (6, 16) and Forge (4, 16). headend-only: changes (agent missing).",
      "Order (single machine first, matrix 0.3.0; before, it was two-machines, win-nvidia, headend-only): the one-machine win-nvidia can run here (firmware switch, features and a sign-in, no hardware gap), so it is first whatever its fit; then the alternatives (machines 2): ready two-machines, changes headend-only. The best lists missing virt-firmware, hyperv, wsl2, agent (requires order) and Tailscale in auto.",
      "Warnings: gpu-mem-8."])

case("linux-gtx1080", "Debian 12, i7-8086K, 32 GB, GTX 1080 (compute 6.1, unsupported), KVM, Docker without the toolkit",
     facts(osl("Debian GNU/Linux 12 (bookworm)", "debian", "12"),
           {"vendor": "Gigabyte Technology Co., Ltd.", "model": "Z370 AORUS Gaming 7", "virtual": False},
           cpu("intel", "Intel(R) Core(TM) i7-8086K CPU @ 4.00GHz", 6, 12, True, True, False),
           {"total_gb": 31.2, "installed_gb": None, "free_gb": 25.0},
           [disk("/", 465.3, 300.0, True)],
           [gpu("NVIDIA GeForce GTX 1080", "nvidia", 8.0, "535.183.01", "6.1")],
           virt(True, True, False, False), hyp(kvm=True, libvirt=True), None,
           docker(True, "24.0.7", False), tools("1.76.6"), ports([])),
     {"best": "no-gpu", "fit": "ready", "ranked": ["no-gpu", "two-machines", "headend-only"],
      "best_missing": [], "best_unknown": [], "best_auto": [], "best_confirm": [],
      "cpu_class": "modern", "gpu_class": "legacy", "gpu_forge": "no", "gpu_mem_tier": "8", "tier": 2,
      "sizes": {"no-gpu": {"ws": [6, 16], "forge": None}, "two-machines": {"ws": [6, 16], "forge": [4, 16]},
                "headend-only": {"ws": None, "forge": None}},
      "ruled_out": ["linux-nvidia", "headless"], "warnings": ["gpu-not-usable"]},
     ["Classes: the i7-8086K has AVX2: modern. 'nvidia geforce gtx 1080' matches 'gtx 10[0-9][0-9]': legacy (Forge no), so gpu.usable is false; 8 GB is tier 8.",
      "Sizing: 12 threads; 31.2 GB visible: 32 GB; tier 2. After the host: 10 threads, 28 GB.",
      "Candidates: linux-nvidia, no-gpu (the card is not usable), two-machines, headless, headend-only.",
      "linux-nvidia and headless: gpu-cuda missing (hardware): ruled out.",
      "no-gpu: ready (KVM, Tailscale present). Size (6, 16). two-machines: ready (one-side met by the Workstation room). Size (6, 16) and Forge (4, 16).",
      "headend-only: changes (agent missing).",
      "Order: the one-machine no-gpu (ready) first; then the alternatives (machines 2): ready two-machines, changes headend-only. Unchanged by the single-machine-first order of matrix 0.3.0.",
      "Warnings: gpu-not-usable (gpu-mem-8 needs a usable card)."])

case("win-celeron", "Windows 11 Pro on a Celeron N4020 laptop, 4 GB",
     facts(osw("Windows 11 Pro", "pro", "22H2", 22621),
           {"vendor": "HP", "model": "HP Laptop 14-dq0xxx", "virtual": False},
           cpu("intel", "Intel(R) Celeron(R) N4020 CPU @ 1.10GHz", 2, 2, False, False, False),
           {"total_gb": 3.8, "installed_gb": 4, "free_gb": 1.0},
           [disk("C:", 59.0, 12.0, None)],
           [gpu("Intel(R) UHD Graphics 600", "intel", None, "27.20.100.9466", None)],
           virt(True, True, None, False), hyp(), {"present": False, "wsl2_distros": 0},
           docker(False), tools(), ports([])),
     {"best": "headend-only", "fit": "changes", "ranked": ["headend-only"],
      "best_missing": ["agent"], "best_unknown": [], "best_auto": ["tailscale"], "best_confirm": [],
      "cpu_class": "low-power", "gpu_class": None, "gpu_forge": None, "gpu_mem_tier": None, "tier": 0,
      "sizes": {"headend-only": {"ws": None, "forge": None}},
      "ruled_out": ["win-nvidia", "no-gpu", "two-machines"], "warnings": []},
     ["Classes: 'celeron' in the model: low-power (Workstation no, Forge no). Intel graphics only.",
      "Sizing: 2 threads, 4 GB: tier 0; nothing is left after the host.",
      "Candidates: win-nvidia, no-gpu, two-machines, headend-only.",
      "win-nvidia, no-gpu and two-machines: cpu-supported missing (hardware, with more hardware gaps): ruled out.",
      "headend-only: changes (agent missing; Tailscale auto). It is the only possible profile: an alternative (machines 2), best because no one-machine profile can run on this hardware. Unchanged by the single-machine-first order of matrix 0.3.0."])

case("sparse-unknowns", "Almost nothing readable (as from the shell version on Windows): the verdict must degrade, not fail",
     facts({"family": "windows", "name": None, "edition": None, "version": None, "build": None, "arch": "x64", "wsl_guest": None},
           {"vendor": None, "model": None, "virtual": None},
           cpu(None, None, None, None, None, None, None, e_cores=None),
           {"total_gb": None, "installed_gb": None, "free_gb": None},
           None, None, virt(None, None, None, None),
           {"hyperv": None, "kvm": None, "proxmox": None, "virtualbox": None, "vmware": None, "libvirt": None},
           None, {"present": None, "client": None, "nvidia": None},
           {"tailscale": None, "claude": None, "codex": None, "gh": None}, ports([], checked=False)),
     {"best": "two-machines", "fit": "unconfirmed", "ranked": ["two-machines", "headend-only"],
      "best_missing": [], "best_unknown": ["cpu-supported", "one-side"], "best_auto": ["tailscale"],
      "best_confirm": ["second-machine"],
      "cpu_class": None, "gpu_class": None, "gpu_forge": None, "gpu_mem_tier": None, "tier": None,
      "sizes": {"two-machines": {"ws": None, "forge": None}, "headend-only": {"ws": None, "forge": None}},
      "ruled_out": [], "warnings": []},
     ["Classes: no processor fact is readable, so every class test is unknown or false: no class. gpus is null: card facts unknown.",
      "Sizing: no threads and no memory: no tier.",
      "Candidates: only two-machines and headend-only match for certain (Windows, x64). The others depend on the unknown edition, hypervisors or hardware model, and an unknown match is not a match.",
      "two-machines: unconfirmed. cpu-supported and one-side unknown; second-machine in confirm; Tailscale unknown, auto (never lowers the fit).",
      "headend-only: unconfirmed. agent unknown; Tailscale auto.",
      "Order: both are alternatives (machines 2) and no one-machine profile matches, so they come out best; both unconfirmed, so by rank: two-machines 40, headend-only 90. Unchanged by the single-machine-first order of matrix 0.3.0."])

case("latitude-5430", "The tested laptop model: i7-1265U (2 fast, 8 efficient cores), 32 GB, Iris Xe",
     facts(osw("Windows 11 Pro", "pro", "23H2", 22631),
           {"vendor": "Dell Inc.", "model": "Latitude 5430", "virtual": False},
           cpu("intel", "12th Gen Intel(R) Core(TM) i7-1265U", 10, 12, True, True, False, p_cores=2, e_cores=8, p_threads=4),
           {"total_gb": 31.7, "installed_gb": 32, "free_gb": 18.0},
           [disk("C:", 476.3, 300.0, True)],
           [gpu("Intel(R) Iris(R) Xe Graphics", "intel", None, "31.0.101.5186", None)],
           virt(True, True, True, True), hyp(hyperv=True), {"present": True, "wsl2_distros": 0},
           docker(False), tools("1.76.1"), ports([])),
     {"best": "baremetal", "fit": "ready", "ranked": ["baremetal", "headend-only"],
      "best_missing": [], "best_unknown": [], "best_auto": [], "best_confirm": ["forge-elsewhere", "headend-elsewhere"],
      "cpu_class": "modern", "gpu_class": None, "gpu_forge": None, "gpu_mem_tier": None, "tier": 0,
      "sizes": {"baremetal": {"ws": None, "forge": None}, "headend-only": {"ws": None, "forge": None}},
      "ruled_out": ["win-nvidia", "no-gpu", "two-machines"], "warnings": ["hybrid-cores"]},
     ["Classes: the i7-1265U has AVX2: modern. 'latitude 5430' matches the Latitude 5430 hardware profile. Iris Xe only: no NVIDIA card.",
      "Sizing: 4 fast threads (2 fast cores, 8 efficient); 32 GB: tier 0 (tier 1 needs 8 threads). After the host: 2 threads, 28 GB: no room for the Workstation VM (4 threads), the Forge alone would fit.",
      "Candidates: win-nvidia, no-gpu, two-machines, baremetal, headend-only.",
      "win-nvidia: size-both and gpu-cuda missing: ruled out. no-gpu: size-ws missing: ruled out. two-machines: one-side missing (no Workstation room, no usable card): ruled out.",
      "baremetal: ready. cpu-supported met; forge-elsewhere and headend-elsewhere in confirm. No size (installed on the machine itself).",
      "headend-only: changes (agent missing; Tailscale present).",
      "Order: both possible profiles are alternatives (machines 2: baremetal needs a Forge elsewhere) and every one-machine profile is ruled out by hardware, so ready baremetal is the best, then changes headend-only. Unchanged by the single-machine-first order of matrix 0.3.0."])

case("esxi-host", "VMware ESXi 8 on a Xeon Gold host, 384 GB",
     facts({"family": "esxi", "name": "VMware ESXi 8.0.3", "edition": None, "version": "8.0.3", "build": None,
            "arch": "x64", "wsl_guest": False},
           {"vendor": "Dell Inc.", "model": "PowerEdge R640", "virtual": False},
           cpu("intel", "Intel(R) Xeon(R) Gold 6130 CPU @ 2.10GHz", 32, 64, True, True, True, sockets=2),
           {"total_gb": 383.6, "installed_gb": None, "free_gb": 300.0},
           None, None, virt(True, True, None, False), hyp(), None, docker(False), tools(), ports([], checked=False)),
     {"best": "esxi", "fit": "ready", "ranked": ["esxi"],
      "best_missing": [], "best_unknown": [], "best_auto": [], "best_confirm": [],
      "cpu_class": "modern", "gpu_class": None, "gpu_forge": None, "gpu_mem_tier": None, "tier": 3,
      "sizes": {"esxi": {"ws": [8, 16], "forge": None}},
      "ruled_out": [], "warnings": []},
     ["Classes: the Xeon Gold 6130 has AVX2: modern. No card facts (null).",
      "Sizing: 64 threads; 383.6 GB visible: 384 GB; tier 3.",
      "Candidates: only esxi matches the esxi family (headend-only lists Windows, Linux and macOS).",
      "esxi: ready. Size 'ws' from tier 3: (8, 16)."])

case("win-pro-rtx5090", "Windows 11 Pro, Ryzen 7 9800X3D, 64 GB, RTX 5090 (an experimental class), everything in place",
     facts(osw("Windows 11 Pro", "pro", "24H2", 26100),
           {"vendor": "ASUSTeK COMPUTER INC.", "model": "ROG STRIX X870E-E", "virtual": False},
           cpu("amd", "AMD Ryzen 7 9800X3D 8-Core Processor", 8, 16, True, True, True),
           {"total_gb": 63.6, "installed_gb": 64, "free_gb": 50.0},
           [disk("C:", 1863.0, 1700.0, True)],
           [gpu("NVIDIA GeForce RTX 5090", "nvidia", 31.8, "581.57", "12.0")],
           virt(True, True, True, True), hyp(hyperv=True), {"present": True, "wsl2_distros": 1},
           docker(True, "28.0.1", None), tools("1.90.1", "2.1.0", "0.58.0", "2.80.0"), ports([])),
     {"best": "win-nvidia", "fit": "ready", "ranked": ["win-nvidia", "two-machines", "headend-only"],
      "best_missing": [], "best_unknown": [], "best_auto": [], "best_confirm": [],
      "cpu_class": "modern", "gpu_class": "rtx50", "gpu_forge": "experimental", "gpu_mem_tier": "11", "tier": 3,
      "sizes": {"win-nvidia": {"ws": [8, 16], "forge": [6, 32]}, "two-machines": {"ws": [8, 16], "forge": [8, 32]},
                "headend-only": {"ws": None, "forge": None}},
      "ruled_out": [], "warnings": ["gpu-experimental"]},
     ["Classes: the Ryzen 7 9800X3D has AVX2: modern. The RTX 5090 is rtx50 (Forge experimental, upstream experimental), usable; 31.8 GB is tier 11.",
      "Sizing: 16 threads, 64 GB: tier 3. After the host: 14 threads, 60 GB.",
      "Candidates: win-nvidia, two-machines, headend-only (no-gpu: the card is usable).",
      "win-nvidia: ready (everything in place). Size 'both': threads (8, 8) = 16 > 14, (8, 6) fit; memory (16, 32) fit 60: Workstation (8, 16), Forge (6, 32).",
      "two-machines: ready. Size (8, 16) and Forge (8, 32). headend-only: ready.",
      "Order: the one-machine win-nvidia first; then the alternatives (machines 2) by rank: two-machines 40, headend-only 90. All ready. Unchanged by the single-machine-first order of matrix 0.3.0.",
      "Warnings: gpu-experimental (gpu-upstream-experimental needs Forge yes)."])

case("linux-in-wsl", "The shell version run inside WSL on a Windows PC with an RTX 2080 Ti",
     facts(osl("Ubuntu 24.04.1 LTS", "ubuntu", "24.04", wsl=True),
           {"vendor": None, "model": None, "virtual": True},
           cpu("amd", "AMD Ryzen 5 3600 6-Core Processor", 6, 12, True, True, False),
           {"total_gb": 31.3, "installed_gb": None, "free_gb": 25.0},
           [disk("/", 1006.9, 950.0, None)],
           [gpu("NVIDIA GeForce RTX 2080 Ti", "nvidia", 11.0, "566.36", "7.5")],
           virt(None, None, None, True), hyp(), None,
           docker(False, None, False), tools(), ports([])),
     {"best": "linux-nvidia", "fit": "changes", "ranked": ["linux-nvidia", "headless", "two-machines"],
      "best_missing": ["kvm", "docker", "nvidia-runtime"], "best_unknown": ["virt-slat", "virt-firmware"],
      "best_auto": ["tailscale"], "best_confirm": ["headend-elsewhere"],
      "cpu_class": "modern", "gpu_class": "rtx20", "gpu_forge": "yes", "gpu_mem_tier": "11", "tier": 2,
      "sizes": {"linux-nvidia": {"ws": [6, 12], "forge": [4, 16]}, "headless": {"ws": None, "forge": [4, 16]},
                "two-machines": {"ws": [6, 16], "forge": [4, 16]}},
      "ruled_out": [], "warnings": ["gpu-upstream-experimental", "is-vm", "in-wsl"]},
     ["Classes: the Ryzen 5 3600 has AVX2: modern. The RTX 2080 Ti: rtx20, usable; 11 GB is tier 11.",
      "Sizing: 12 threads; 31.3 GB visible: 32 GB; tier 2. After the host: 10 threads, 28 GB.",
      "Candidates: linux-nvidia, two-machines, headless, headend-only (no-gpu: the card is usable).",
      "linux-nvidia: changes. Missing kvm, docker, nvidia-runtime; virt-slat and virt-firmware unknown; headend-elsewhere in confirm; Tailscale auto. Size 'both': threads (6, 4) fit 10; memory (16, 16) = 32 > 28 twice, then (12, 16): Workstation (6, 12), Forge (4, 16).",
      "two-machines: ready. Size (6, 16) and Forge (4, 16).",
      "headless: changes. Missing docker, nvidia-runtime. Size 'forge': (4, 16). headend-only: changes (agent missing).",
      "Order (single machine first, matrix 0.3.0; before, it was two-machines, linux-nvidia, headless): the one-machine profiles first, both changes, by rank: linux-nvidia 10, headless 50; then the alternatives (machines 2): ready two-machines (third), changes headend-only (fourth, not shown). The in-wsl warning tells the person to run the .ps1 on Windows instead.",
      "Warnings: gpu-upstream-experimental, is-vm, in-wsl."])

case("linux-h100-server", "A GPU server: Ubuntu 22.04, two Xeon Platinum 8480+, 2 TB, H100, Docker with the toolkit, no KVM, port 8765 taken",
     facts(osl("Ubuntu 22.04.5 LTS", "ubuntu", "22.04"),
           {"vendor": "Supermicro", "model": "SYS-421GE-TNRT", "virtual": False},
           cpu("intel", "Intel(R) Xeon(R) Platinum 8480+", 112, 224, True, True, True, sockets=2),
           {"total_gb": 2015.5, "installed_gb": None, "free_gb": 1900.0},
           [disk("/", 3500.0, 3000.0, True)],
           [gpu("NVIDIA H100 80GB HBM3", "nvidia", 79.6, "550.90.07", "9.0")],
           virt(None, True, True, False), hyp(), None,
           docker(True, "27.3.1", True), tools("1.76.6"), ports([8765])),
     {"best": "linux-nvidia", "fit": "changes", "ranked": ["linux-nvidia", "headless", "two-machines"],
      "best_missing": ["kvm"], "best_unknown": ["virt-firmware"], "best_auto": [], "best_confirm": ["headend-elsewhere"],
      "cpu_class": "modern", "gpu_class": "hopper", "gpu_forge": "experimental", "gpu_mem_tier": "11", "tier": 3,
      "sizes": {"linux-nvidia": {"ws": [8, 16], "forge": [8, 32]}, "headless": {"ws": None, "forge": [8, 32]},
                "two-machines": {"ws": [8, 16], "forge": [8, 32]}},
      "ruled_out": [], "warnings": ["gpu-experimental", "ports-busy"]},
     ["Classes: the Xeon Platinum 8480+ has AVX2: modern. 'nvidia h100 80gb hbm3' matches 'h100': hopper (Forge experimental, upstream stable), usable; 79.6 GB is tier 11.",
      "Sizing: 224 threads; 2015.5 GB visible: 2016 GB; tier 3. After the host: 222 threads, 2012 GB.",
      "Candidates: linux-nvidia, two-machines, headless, headend-only. no-gpu no longer matches because the card is usable (before, no-gpu sat second here and pushed headless out of the three).",
      "linux-nvidia: changes. Missing kvm (not installed; this server is not a VM, so it can be added); virt-firmware unknown; headend-elsewhere in confirm. Size 'both': (8, 16) and Forge (8, 32).",
      "two-machines: ready (this server as the Forge, the Workstation elsewhere). Size (8, 16) and Forge (8, 32).",
      "headless: changes. Missing ports-engine (8765 busy). Size 'forge': (8, 32). headend-only: changes (agent missing).",
      "Order (single machine first, matrix 0.3.0; before, it was two-machines, linux-nvidia, headless): the one-machine profiles first, both changes, by rank: linux-nvidia 10, headless 50; then the alternatives (machines 2): ready two-machines (third), changes headend-only (fourth, not shown). The best, linux-nvidia, lists missing kvm, unknown virt-firmware, headend-elsewhere in confirm; Tailscale present.",
      "Warnings: gpu-experimental, ports-busy."])

case("linux-cloud-l4", "A cloud VM: Ubuntu 24.04, 16 vCPU, 64 GB, NVIDIA L4, Docker with the toolkit, 250 GB free, no KVM (nested virtualisation is up to the provider)",
     facts(osl("Ubuntu 24.04.1 LTS", "ubuntu", "24.04"),
           {"vendor": "Google", "model": "Google Compute Engine", "virtual": True},
           cpu("intel", "Intel(R) Xeon(R) CPU @ 2.20GHz", 8, 16, True, True, True),
           {"total_gb": 62.8, "installed_gb": None, "free_gb": 50.0},
           [disk("/", 290.0, 250.0, None)],
           [gpu("NVIDIA L4", "nvidia", 22.5, "550.90.07", "8.9")],
           virt(None, None, None, True), hyp(), None,
           docker(True, "27.3.1", True), tools("1.76.6"), ports([])),
     {"best": "headless", "fit": "ready", "ranked": ["headless", "linux-nvidia", "two-machines"],
      "best_missing": [], "best_unknown": [], "best_auto": [], "best_confirm": [],
      "cpu_class": "modern", "gpu_class": "ada-dc", "gpu_forge": "experimental", "gpu_mem_tier": "11", "tier": 3,
      "sizes": {"headless": {"ws": None, "forge": [8, 32]}, "linux-nvidia": {"ws": [8, 16], "forge": [6, 32]},
                "two-machines": {"ws": [8, 16], "forge": [8, 32]}},
      "ruled_out": [], "warnings": ["gpu-experimental", "is-vm"]},
     ["Purpose: a profile missing KVM, which may be impossible on a cloud VM, must not outrank a profile that is ready. Under the old rule (rank first) linux-nvidia was best here.",
      "Classes: the Xeon has AVX2: modern. 'nvidia l4' matches '(^|nvidia )l4($| )': ada-dc (Forge experimental, upstream stable), usable; 22.5 GB is tier 11.",
      "Sizing: 16 threads; 62.8 GB visible, half rounded up (32) doubled: 64 GB; tier 3 (16 threads, 64 GB). After the host: 14 threads, 60 GB.",
      "Candidates: linux-nvidia, two-machines, headless, headend-only (no-gpu: the card is usable).",
      "linux-nvidia: changes. Missing kvm; virt-slat and virt-firmware unknown; headend-elsewhere in confirm. Size 'both': threads (8, 8) = 16 > 14, (8, 6) fit; memory (16, 32) fit 60: Workstation (8, 16), Forge (6, 32).",
      "two-machines: ready. Size (8, 16) and Forge (8, 32).",
      "headless: ready. Driver, Docker, toolkit, disk (250 >= 100), ports, Tailscale all met. Size 'forge': (8, 32).",
      "headend-only: changes (agent missing).",
      "Order (single machine first, matrix 0.3.0; before, it was two-machines, headless, linux-nvidia): the one-machine profiles first, by fit: ready headless (rank 50), then changes linux-nvidia (rank 10): within the group fit still comes before rank, so the profile missing KVM stays behind the ready one. Then the alternatives (machines 2): ready two-machines (third), changes headend-only (fourth, not shown).",
      "Warnings: gpu-experimental, is-vm."])

case("win-pro-tools-unreadable", "Windows 11 Pro, Ryzen 7 5800X, 32 GB, RTX 3080 10 GB, Hyper-V on, WSL not installed; the tool checks could not run (null)",
     facts(osw("Windows 11 Pro", "pro", "24H2", 26100),
           {"vendor": "Gigabyte Technology Co., Ltd.", "model": "B550 AORUS ELITE V2", "virtual": False},
           cpu("amd", "AMD Ryzen 7 5800X 8-Core Processor", 8, 16, True, True, False, family=25, model_id=33, stepping=0),
           {"total_gb": 31.9, "installed_gb": 32, "free_gb": 20.0},
           [disk("C:", 1863.0, 600.0, True)],
           [gpu("NVIDIA GeForce RTX 3080", "nvidia", 10.0, "566.36", "8.6")],
           virt(True, True, True, True), hyp(hyperv=True), {"present": False, "wsl2_distros": 0},
           docker(False), {"tailscale": None, "claude": None, "codex": None, "gh": None}, ports([])),
     {"best": "win-nvidia", "fit": "changes", "ranked": ["win-nvidia", "two-machines", "headend-only"],
      "best_missing": ["wsl2"], "best_unknown": ["agent"], "best_auto": ["tailscale"], "best_confirm": [],
      "cpu_class": "modern", "gpu_class": "rtx30", "gpu_forge": "yes", "gpu_mem_tier": "8", "tier": 2,
      "sizes": {"win-nvidia": {"ws": [6, 12], "forge": [4, 16]}, "two-machines": {"ws": [6, 16], "forge": [4, 16]},
                "headend-only": {"ws": None, "forge": None}},
      "ruled_out": [], "warnings": ["gpu-mem-8"]},
     ["Purpose (under matrix 0.2.0): a profile with only unknowns (unconfirmed) outranks one that needs a change, whatever their ranks. Since matrix 0.3.0 (single machine first) that holds within each group; here the unconfirmed profile is an alternative, so the purpose is now: a one-machine profile that needs a change and has an unknown still comes before ready and unconfirmed alternatives. Fit before rank within a group is shown by linux-cloud-l4 and the alternatives edge cases in run_tests.py.",
      "Classes: the Ryzen 7 5800X has AVX2: modern. The RTX 3080 is rtx30 (Forge yes, upstream stable), usable; 10.0 GB is tier 8 (tier 11 needs 10.5).",
      "Sizing: 16 threads, 32 GB installed: tier 2. After the host: 14 threads, 28 GB.",
      "Candidates (Windows Pro, x64): win-nvidia, two-machines, headend-only (no-gpu: the card is usable; win-home, virtualbox, vmware, baremetal and esxi do not match).",
      "win-nvidia: changes. Missing wsl2; agent unknown (the tool versions could not be read); Tailscale unknown, auto. Size 'both': threads (6, 4) fit 14; memory (16, 16) = 32 > 28 twice, then (12, 16): Workstation (6, 12), Forge (4, 16).",
      "two-machines: ready (second-machine in confirm, Tailscale auto). Size (6, 16) and Forge (4, 16).",
      "headend-only: unconfirmed (agent unknown; Tailscale auto).",
      "Order (single machine first, matrix 0.3.0; before, it was two-machines, headend-only, win-nvidia): the one-machine win-nvidia (changes) first; then the alternatives (machines 2) by fit: ready two-machines, unconfirmed headend-only. The best lists missing wsl2, unknown agent, Tailscale in auto.",
      "Warnings: gpu-mem-8."])

os.makedirs(OUT, exist_ok=True)
for name, doc in CASES:
    with open(os.path.join(OUT, name + ".json"), "w", encoding="ascii", newline="\n") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=True)
        fh.write("\n")
for name, mode in (("guard-gaming-on", "on"), ("guard-gaming-off", "off")):
    with open(os.path.join(OUT, name + ".json"), "w", encoding="ascii", newline="\n") as fh:
        json.dump({"mode": mode, "by": "test fixture"}, fh)
        fh.write("\n")
with open(os.path.join(OUT, "guard-unreadable.json"), "w", encoding="ascii", newline="\n") as fh:
    fh.write("{ this is not json\n")
print(len(CASES), "fixtures written to", OUT)
