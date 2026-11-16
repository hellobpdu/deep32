"""Writes the fake Linux system tree and command stubs used by run_tests.py's probe test.

Creates TESTS_DIR/fakeroot-linux and TESTS_DIR/fakebin-linux as source templates.
run_tests.py copies them into its work folder and gives the agent, network and graphics
templates their explicit command and driver names there before the Linux probes run.
(stubs for df, lspci, docker, tailscale, codex, virsh). A hybrid Intel desktop: i5-12600K
(6 fast cores with two threads each, 4 efficient cores), 32 GB, an RTX 3060 seen through
sysfs and lspci only (no nvidia-smi), Intel graphics, KVM, IOMMU, Docker with the NVIDIA
runtime in daemon.json, ports 8766 and 8081 listening, 7452 only in an established connection.
PCI device folders use dashes for colons, which Windows file names cannot hold.

Usage:
  python3 -I make_fakeroot.py TESTS_DIR   (normally: this folder)

Exit codes:
  0  written
  1  an error (Python prints the reason)
"""
import os
import sys

T = sys.argv[1]
ROOT = os.path.join(T, "fakeroot-linux")
BIN = os.path.join(T, "fakebin-linux")


def w(rel, text, base=ROOT):
    p = os.path.join(base, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="ascii", newline="\n") as fh:
        fh.write(text)


flags = ("fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss "
         "ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology "
         "nonstop_tsc cpuid aperfmperf tsc_known_freq pni pclmulqdq dtes64 monitor ds_cpl vmx smx est tm2 ssse3 "
         "sdbg fma cx16 xtpr pdcm sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand "
         "lahf_lm abm 3dnowprefetch cpuid_fault epb ssbd ibrs ibpb stibp ibrs_enhanced tpr_shadow flexpriority ept "
         "vpid ept_ad fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid rdseed adx smap clflushopt clwb intel_pt "
         "sha_ni xsaveopt xsavec xgetbv1 xsaves split_lock_detect avx_vnni dtherm ida arat pln pts hwp hwp_notify "
         "hwp_act_window hwp_epp hwp_pkg_req hfi vnmi umip pku ospke waitpkg gfni vaes vpclmulqdq rdpid movdiri "
         "movdir64b fsrm md_clear serialize arch_lbr ibt flush_l1d arch_capabilities")
vmx = "vnmi preemption_timer posted_intr invvpid ept_x_only ept_ad ept_1gb flexpriority apicv tsc_offset vtpr mtf vapic ept vpid unrestricted_guest vapic_reg vid ple shadow_vmcs ept_mode_based_exec tsc_scaling usr_wait_pause"
blocks = []
cpu = 0
for core in range(6):
    for _ in range(2):
        blocks.append((cpu, core * 4))
        cpu += 1
for e in range(4):
    blocks.append((cpu, 24 + e))
    cpu += 1
text = ""
for n, core_id in blocks:
    text += ("processor\t: %d\nvendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 151\n"
             "model name\t: 12th Gen Intel(R) Core(TM) i5-12600K\nstepping\t: 2\nmicrocode\t: 0x35\n"
             "cpu MHz\t\t: 3700.000\ncache size\t: 20480 KB\nphysical id\t: 0\nsiblings\t: 16\ncore id\t\t: %d\n"
             "cpu cores\t: 10\napicid\t\t: %d\nfpu\t\t: yes\nflags\t\t: %s\nvmx flags\t: %s\nbogomips\t: 7372.80\n\n"
             % (n, core_id, n, flags, vmx))
w("proc/cpuinfo", text)
w("proc/meminfo", "MemTotal:       32561200 kB\nMemFree:        12000000 kB\nMemAvailable:   20000000 kB\nBuffers:          500000 kB\n")
w("proc/net/tcp",
  "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
  "   0: 0100007F:223E 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 11111 1 0000000000000000 100 0 0 10 0\n"
  "   1: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 22222 1 0000000000000000 100 0 0 10 0\n"
  "   2: 0100007F:9C40 0100007F:1D1A 01 00000000:00000000 00:00000000 00000000  1000        0 33333 1 0000000000000000 20 4 30 10 -1\n")
w("proc/net/tcp6",
  "  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
  "   0: 00000000000000000000000000000000:1F91 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 44444 1 0000000000000000 100 0 0 10 0\n")
w("proc/mounts",
  "/dev/nvme0n1p2 / ext4 rw,relatime 0 0\n"
  "tmpfs /run tmpfs rw,nosuid,nodev 0 0\n"
  "/dev/nvme0n1p1 /boot/efi vfat rw 0 0\n"
  "/dev/sda1 /data xfs rw,relatime 0 0\n"
  "/dev/sdb1 /home/someone/big\\040disk ext4 rw,relatime 0 0\n"
  "/dev/loop0 /snap/core/1 squashfs ro 0 0\n")
w("proc/sys/kernel/osrelease", "6.8.0-48-generic\n")
w("proc/driver/graphics-version.template", "NVRM version: NVIDIA UNIX x86_64 Kernel Module  560.35.03  Fri Aug 16 21:39:15 UTC 2024\nGCC version:  gcc version 12.3.0\n")
w("etc/os-release", 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\nID_LIKE=debian\n')
w("etc/docker/daemon.json", '{"runtimes": {"nvidia": {"path": "nvidia-container-runtime", "args": []}}}\n')
w("sys/devices/cpu_core/cpus", "0-11\n")
w("sys/devices/cpu_atom/cpus", "12-15\n")
w("sys/class/dmi/id/sys_vendor", "Micro-Star International Co., Ltd.\n")
w("sys/class/dmi/id/product_name", "MS-7D25\n")
w("dev/kvm", "")
w("sys/class/iommu/dmar0/README", "fake iommu unit\n")
w("sys/bus/pci/devices/0000-00-02.0/class", "0x030000\n")
w("sys/bus/pci/devices/0000-00-02.0/vendor", "0x8086\n")
w("sys/bus/pci/devices/0000-01-00.0/class", "0x030000\n")
w("sys/bus/pci/devices/0000-01-00.0/vendor", "0x10de\n")
w("sys/bus/pci/devices/0000-00-1f.3/class", "0x040380\n")
w("sys/bus/pci/devices/0000-00-1f.3/vendor", "0x8086\n")
w("sys/class/block/nvme0n1p2/README", "whole-device stand-in (the fake tree has no symlinks)\n")
w("sys/block/nvme0n1p2/queue/rotational", "0\n")

HDR = ("#!/bin/sh\n# test stub for run_tests.py's probe test: prints canned output, changes nothing\n"
       "# usage: called by deep32-inventory.sh in place of the real command, with its arguments\n"
       "# exit codes: 0\n")
w("df", HDR + r"""cat <<'EOF'
Filesystem     1024-blocks      Used  Available Capacity Mounted on
/dev/nvme0n1p2   976284600 450000000  526284600      47% /
tmpfs              3256120      2000    3254120       1% /run
/dev/nvme0n1p1      523248      6220     517028       2% /boot/efi
/dev/sda1       3906885632 100000000 3806885632       3% /data
/dev/sdb1        488134656  88134656  400000000      19% /home/someone/big disk
/dev/loop0           65536     65536          0     100% /snap/core/1
EOF
""", BIN)
w("lspci", HDR + r"""case "$*" in
  *01?00.0*) echo '01:00.0 "VGA compatible controller" "NVIDIA Corporation" "GA106 [GeForce RTX 3060 Lite Hash Rate]" -ra1 "Micro-Star International Co., Ltd. [MSI]" "Device 397d"' ;;
  *00?02.0*) echo '00:02.0 "VGA compatible controller" "Intel Corporation" "AlderLake-S GT1" -r0c "Micro-Star International Co., Ltd. [MSI]" "Device 7d25"' ;;
esac
""", BIN)
w("docker", HDR + 'echo "Docker version 27.3.1, build ce12230"\n', BIN)
w("network-command.template", HDR + 'printf "1.76.6\\n  tailscale commit: 0000000\\n  go version: go1.23.1\\n"\n', BIN)
w("agent-command.template", HDR + 'echo "codex-cli 0.58.0"\n', BIN)
w("virsh", HDR + "exit 0\n", BIN)
for name in ("df", "lspci", "docker", "network-command.template", "agent-command.template", "virsh"):
    try:
        os.chmod(os.path.join(BIN, name), 0o755)
    except OSError:
        pass
print("fake tree written under", T)
