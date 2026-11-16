#!/usr/bin/env python3
"""d32test-hypervisor_client.py: the Proxmox twin of buildhost/d32test-vm.ps1: disc 1's TEST medium installed in 920 and 921 at once.

What (run by the lead on build-controller; every Proxmox call goes through the lab's hypervisor_client client, never anything else)
  The Hyper-V script made one VM, put the medium on IDE 0:0 and the target on IDE 0:1, waited for the medium to
  power the VM off, then moved the target to IDE 0:0 and booted it. Here:
    upload   the raw TEST medium (stage 85-test-raw) as import content d32dist-<release>-test.raw
    create   920 d32-dist-ws1 (OVMF, std VGA) and 921 d32-dist-ws2 (SeaBIOS, std VGA), each with the medium
             imported as scsi0 and an empty target as scsi1 (hypervisor_client-client-v2's two-disk verb), VirtIO SCSI and NIC,
             x86-64-v2-AES, segment 3950 (lab guide 2), never started by create
    install  both started at once; each serial console logged to <out>/<vmid>-install.log; done when the VM is
             stopped (the TEST ramdisk halts after the install) or the time runs out
    boot     started again: the boot order (target first) starts the installed system; console logged to
             <out>/<vmid>-boot.log for --boot-minutes
    read     the C6 status lines of each log read by c6.py: pass or fail per stage, per VM, in <out>/RESULT.json
    stop     both stopped (never destroyed: 921 is destroyed by the lead after its v0 check, lab guide 6)
  run = upload, create, install, boot, read. The VHD step stays for the hyperv adapter test in 930 (vhd.py to-vhd,
  release/tools/raw2vhd.py).
  The hypervisor_client client's command forms are in VERBS below, as hypervisor_client-client-v2 landed: create-boot (the two-disk create,
  boot order target then medium, so no boot-target call), console --read (its standard output is the log here),
  upload-iso --content import. If they change, give --verbs FILE (JSON, the same keys; "boot_target": [...] adds a
  call before the second start, null leaves it out) instead of editing this file. --dry-run passes --dry-run to the
  client and starts no console capture.
Usage
  python -I -B d32test-hypervisor_client.py run --release ID --raw FILE --out DIR [--hypervisor_client CLIENT] [--verbs FILE] [--dry-run]
      [--vms 920,921] [--install-minutes 45] [--boot-minutes 15] [--need install,firstboot,keys] [--legacy]
  python -I -B d32test-hypervisor_client.py read --out DIR [--need ...] [--legacy]     (the logs again, no Proxmox call)
  python -I -B d32test-hypervisor_client.py stop --out DIR [--vms 920,921] [--hypervisor_client CLIENT]
Exit
  0 every VM passed every needed stage; 1 a VM failed, a call failed or the time ran out; 2 bad usage.
"""
import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import c6  # noqa: E402

DEFAULT_PVE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "lab", "design", "hypervisor_client", "d32dist-hypervisor_client.py"))
VMS = {
    920: {"name": "d32-dist-ws1", "bios": "ovmf", "vga": "std"},
    921: {"name": "d32-dist-ws2", "bios": "seabios", "vga": "std"},
}
SHAPE = {"cores": 4, "memory": 6144, "target_gb": 40, "vlan": 3950, "cpu": "x86-64-v2-AES"}
VERBS = {
    "upload": ["upload-iso", "--content", "import", "--file", "{raw}", "--as", "{media}"],
    # hypervisor_client-client-v2 as it landed (2026-11-15): create-boot imports the medium as scsi0 with an empty target as
    # scsi1 and the boot order scsi1;scsi0, so the first boot falls through to the medium and every later boot
    # starts the installed system: no boot-target call is needed (None leaves it out)
    "create": ["create-boot", "--vmid", "{vmid}", "--name", "{name}", "--image", "{media}", "--target-gb",
               "{target_gb}", "--bios", "{bios}", "--vga", "{vga}", "--cores", "{cores}", "--memory", "{memory}",
               "--cpu", "{cpu}", "--vlan", "{vlan}"],
    "start": ["start", "--vmid", "{vmid}"],
    "stop": ["stop", "--vmid", "{vmid}"],
    "status": ["--json", "list"],
    # console prints each received line to its standard output ("| line", flushed), which this script writes to
    # <out>/<vmid>-<phase>.log, and keeps its own transcript in --log-dir; one read lasts at most 3600 s
    "console": ["console", "--vmid", "{vmid}", "--read", "{seconds}", "--log-dir", "{logdir}"],
    "boot_target": None,
}
CONSOLE_MAX_SECONDS = 3600


class Fail(Exception):
    pass


def fill(form, values):
    return [x.format(**values) for x in form]


class Client:
    def __init__(self, hypervisor_client, verbs, dry, transcript):
        self.base = [sys.executable, "-I", "-B", hypervisor_client] if hypervisor_client.endswith(".py") else shlex.split(hypervisor_client)
        self.verbs = verbs
        self.dry = dry
        self.transcript = transcript

    def argv(self, verb, **values):
        return self.base + (["--dry-run"] if self.dry else []) + fill(self.verbs[verb], values)

    def call(self, verb, **values):
        argv = self.argv(verb, **values)
        with open(self.transcript, "a", encoding="utf-8", newline="\n") as t:
            t.write("$ %s\n" % " ".join(shlex.quote(a) for a in argv[len(self.base):]))
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        text = p.stdout.decode("utf-8", "replace")
        with open(self.transcript, "a", encoding="utf-8", newline="\n") as t:
            t.write(text + ("" if text.endswith("\n") else "\n") + "# exit %d\n" % p.returncode)
        if p.returncode != 0:
            raise Fail("hypervisor_client client %s exited %d: %s" % (verb, p.returncode, text.strip().splitlines()[-1:]))
        return text

    def spawn_console(self, vmid, log, seconds):
        if seconds > CONSOLE_MAX_SECONDS:
            print("console of %d: the client reads at most %d s; the log ends there" % (vmid, CONSOLE_MAX_SECONDS))
            seconds = CONSOLE_MAX_SECONDS
        logdir = os.path.join(os.path.dirname(log), "console")
        argv = self.argv("console", vmid=vmid, log=log, seconds=seconds, logdir=logdir)
        with open(self.transcript, "a", encoding="utf-8", newline="\n") as t:
            t.write("$ (background) %s > %s\n" % (" ".join(shlex.quote(a) for a in argv[len(self.base):]),
                                                 os.path.basename(log)))
        if self.dry:
            return None
        os.makedirs(logdir, exist_ok=True)
        fh = open(log, "ab")
        try:
            return subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT)
        finally:
            fh.close()          # the child keeps its own handle

    def running(self, vmid):
        if self.dry:
            return False
        text = self.call("status")
        try:
            vms = json.loads(text)
        except ValueError:
            raise Fail("hypervisor_client client list --json printed no JSON")
        rows = vms.get("vms", vms) if isinstance(vms, dict) else vms
        for v in rows:
            if int(v.get("vmid", -1)) == vmid:
                return v.get("status") == "running"
        raise Fail("VM %d is not in the pool list" % vmid)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def wait_stopped(cl, vmids, minutes, poll):
    deadline = time.time() + minutes * 60
    left = set(vmids)
    while left:
        for v in sorted(left):
            if not cl.running(v):
                left.discard(v)
        if not left:
            break
        if time.time() > deadline:
            raise Fail("still running after %d min: %s" % (minutes, sorted(left)))
        time.sleep(poll)


def read_results(out, vmids, need, legacy):
    res = {"need": need, "vms": {}}
    for v in vmids:
        logs = [os.path.join(out, "%d-%s.log" % (v, k)) for k in ("install", "boot")]
        logs = [p for p in logs if os.path.exists(p)]
        if not logs:
            res["vms"][str(v)] = {"pass": False, "why": "no console log"}
            continue
        r = c6.evaluate(list(c6.read_lines(logs)), need, ("signin", "pairing"), legacy)
        res["vms"][str(v)] = {"pass": r["pass"], "stages": r["stages"], "boots": r["boots"],
                              "logs": [os.path.basename(p) for p in logs]}
    res["pass"] = bool(res["vms"]) and all(x["pass"] for x in res["vms"].values())
    with open(os.path.join(out, "RESULT.json"), "w", encoding="utf-8", newline="\n") as w:
        json.dump(res, w, indent=1, sort_keys=True)
        w.write("\n")
    for v, x in sorted(res["vms"].items()):
        stages = x.get("stages", {})
        print("VM %s: %s  %s" % (v, "PASS" if x["pass"] else "FAIL",
                                 " ".join("%s=%s" % (s, stages[s]["result"]) for s in need if s in stages)))
    return res


def main(argv):
    ap = argparse.ArgumentParser(prog="d32test-hypervisor_client.py")
    ap.add_argument("action", choices=["run", "read", "stop"])
    ap.add_argument("--release")
    ap.add_argument("--raw")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hypervisor_client", default=DEFAULT_PVE)
    ap.add_argument("--verbs")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vms", default="920,921")
    ap.add_argument("--install-minutes", type=int, default=45)
    ap.add_argument("--boot-minutes", type=int, default=15)
    ap.add_argument("--poll", type=int, default=20)
    ap.add_argument("--need", default="install,firstboot,keys")
    ap.add_argument("--legacy", action="store_true", help="v0 before transform 901: the old markers, not C6")
    a = ap.parse_args(argv)
    if a.install_minutes < 1 or a.boot_minutes < 1:
        print("d32test-hypervisor_client: --install-minutes and --boot-minutes take 1 or more (the client reads 1 to 3600 s)",
              file=sys.stderr)
        return 2
    vmids = [int(x) for x in a.vms.split(",") if x]
    if any(v not in VMS for v in vmids):
        print("d32test-hypervisor_client: only %s" % sorted(VMS), file=sys.stderr)
        return 2
    need = [s for s in a.need.split(",") if s]
    verbs = dict(VERBS)
    if a.verbs:
        with open(a.verbs, encoding="utf-8") as f:
            verbs.update(json.load(f))
    os.makedirs(a.out, exist_ok=True)
    cl = Client(a.hypervisor_client, verbs, a.dry_run, os.path.join(a.out, "hypervisor_client-calls.txt"))
    try:
        if a.action == "read":
            return 0 if read_results(a.out, vmids, need, a.legacy)["pass"] else 1
        if a.action == "stop":
            for v in vmids:
                cl.call("stop", vmid=v)
            return 0
        if not a.release or not a.raw:
            print("d32test-hypervisor_client: run needs --release and --raw", file=sys.stderr)
            return 2
        if not os.path.isfile(a.raw):
            raise Fail("no raw medium %s" % a.raw)
        media = "d32dist-%s-test.raw" % a.release
        print("medium %s sha256 %s -> import content %s" % (a.raw, sha256_file(a.raw), media))
        cl.call("upload", raw=a.raw, media=media)
        for v in vmids:
            cl.call("create", vmid=v, media=media, **dict(SHAPE, **VMS[v]))
        consoles = []
        for v in vmids:
            cl.call("start", vmid=v)
            consoles.append(cl.spawn_console(v, os.path.join(a.out, "%d-install.log" % v), a.install_minutes * 60))
        print("installing in %s at once; waiting up to %d min for the medium to power each VM off" % (
            ", ".join(map(str, vmids)), a.install_minutes))
        wait_stopped(cl, vmids, a.install_minutes, a.poll)
        for p in consoles:
            if p:
                p.terminate()
                p.wait(timeout=30)
        consoles = []
        for v in vmids:
            if cl.verbs.get("boot_target"):
                cl.call("boot_target", vmid=v)
            cl.call("start", vmid=v)
            consoles.append(cl.spawn_console(v, os.path.join(a.out, "%d-boot.log" % v), a.boot_minutes * 60))
        if not a.dry_run:
            print("first boot: console logged for %d min" % a.boot_minutes)
            for p in consoles:
                p.wait(timeout=a.boot_minutes * 60 + 120)
            res = read_results(a.out, vmids, need, a.legacy)
            print("d32test-hypervisor_client: %s (RESULT.json in %s)" % ("PASS" if res["pass"] else "FAIL", a.out))
            return 0 if res["pass"] else 1
        print("d32test-hypervisor_client: dry run done; the calls are in %s" % os.path.join(a.out, "hypervisor_client-calls.txt"))
        return 0
    except Fail as e:
        print("d32test-hypervisor_client: FAILED: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
