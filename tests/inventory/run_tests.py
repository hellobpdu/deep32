"""run_tests.py: tests for deep32-inventory (both implementations) against fixtures of fake machine facts.

What it does:
  1. For every fixture in fixtures/*.json that has an "expect" member, runs
     "report --facts FIXTURE --json" through each available implementation
     (deep32-inventory.ps1 with Windows PowerShell or pwsh, deep32-inventory.sh with bash),
     checks the verdict against the hand-worked expectation, and checks that both
     implementations return exactly the same verdict object.
  2. Runs the shell version's awk verdict program under gawk --posix and gawk --traditional
     (when gawk is present) and checks it gives the same verdict as the system awk.
  3. Runs the shell version's Linux probes on a fake system tree (fakeroot-linux, with stub
     commands in fakebin-linux) through its read-only test hooks and checks every fact it reads.
  4. Checks the command line: exit 2 for bad arguments, exit 3 when the gaming-mode guard
     says on or cannot be read, exit 1 with a JSON error when the matrix is missing.
  5. Edge cases (inputs in edge/, written by make_edge.py): facts and matrix files with a
     byte-order mark, UTF-16, CRLF, raw UTF-8, huge or fractional numbers, paths with spaces
     (same verdict from both programs, ASCII-only JSON); shapes that must be refused (exit 1);
     the card, processor and laptop classes; every gaming-mode state file form through the
     guard-only test hook (nothing is measured), and DEEP32_GUARD_FILE as the only way to name the
     state file besides --guard-file; empty option values and odd --seconds.
     The confirm kind and the fit-first order on a small matrix and facts written at run time
     (byte-order mark, CRLF, a folder name with spaces) into the --work folder or a temporary one.
     Single machine first (matrix 0.3.0, 2026-11-14), on another small matrix written the
     same way: one-machine profiles before the alternatives that need another machine, an
     alternative best only when no one-machine profile can run, "machines" in every ranked entry
     and "needs another machine" on the page; a matrix whose schema member or value differs in
     case is refused by both programs. Every fixture also checks each ranked entry's "machines"
     against install/matrix.json, and the static matrix checks hold the machines values and the
     card memory tier 0 text (keyword search only).
  6. The --net code offline: the shell version on the fake tree with stub curl and gh, the
     PowerShell function with stub web and tool calls. A dead local proxy is set as well, so
     even a stub that failed to load could not reach the network.
  It never runs a real benchmark and never probes the machine (every report uses --facts).
  On Windows it refuses any bash under System32 or WindowsApps: that one starts WSL.

  8. The tool time limit of the PowerShell version (Windows only): a fake .cmd tool in fakebin-win
     whose child starts a grandchild and hangs; at the limit the whole tree the program started
     must end (also through the taskkill path, and from a folder whose name holds spaces,
     parentheses and an ampersand), and a bystander process must live on. A fake .cmd tool that
     ends at once but leaves a background child holding its output (bg-tool.cmd) must not hold
     the program past the limit: its output comes back and that child tree is ended.

Usage:
  python3 -I run_tests.py [--impl ps1|sh|both] [--bash PATH] [--powershell PATH] [--quiet]
                          [--work DIR]
  --work DIR  folder for generated synthetic probes and time-limit process-id files (kept);
              default a new temporary
              folder, removed at the end

Exit codes:
  0  every test passed
  1  at least one test failed
  2  bad arguments, or no implementation could be run
"""
import argparse
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", ".."))
PS1 = os.path.join(SRC, "install", "deep32-inventory.ps1")
SH = os.path.join(SRC, "install", "deep32-inventory.sh")
FIX = os.path.join(HERE, "fixtures")
PROBE_ROOT = None
PROBE_BIN = None
RUN_WORK = None

FAILS = []
PASSES = 0
QUIET = False


def ok(name):
    global PASSES
    PASSES += 1
    if not QUIET:
        print("PASS  " + name)


def bad(name, why):
    FAILS.append((name, why))
    print("FAIL  %s: %s" % (name, why))


def unsafe_bash(path):
    p = path.replace("/", "\\").lower()
    return "\\system32\\" in p or "\\windowsapps\\" in p or "\\sysnative\\" in p


def find_bash(given):
    if given:
        return given
    if os.name == "nt":
        for c in (r"C:\Program Files\Git\usr\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"):
            if os.path.isfile(c):
                return c
        return None
    return shutil.which("bash")


def find_ps(given):
    if given:
        return given
    if os.name == "nt":
        p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.isfile(p):
            return p
    return shutil.which("pwsh")


def run(cmd, env=None, stdin=None):
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, input=stdin, timeout=120)
    return r.returncode, r.stdout, r.stderr


def impl_cmd(impl, tools, args):
    if impl == "ps1":
        return [tools["ps1"], "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS1] + args
    return [tools["sh"], SH] + args


def num_eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(num_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(num_eq(x, y) for x, y in zip(a, b))
    return a == b


def check_expect(name, v, exp):
    errs = []
    def want(label, got, expected):
        if not num_eq(got, expected):
            errs.append("%s: got %r, expected %r" % (label, got, expected))
    want("best", v.get("best"), exp["best"])
    want("fit", v.get("fit"), exp["fit"])
    ranked = v.get("ranked") or []
    want("ranked", [r["p"] for r in ranked], exp["ranked"])
    if ranked:
        want("best missing", ranked[0]["missing"], exp["best_missing"])
        want("best unknown", ranked[0]["unknown"], exp["best_unknown"])
        want("best auto", ranked[0]["auto"], exp["best_auto"])
        want("best confirm", ranked[0].get("confirm"), exp["best_confirm"])
    want("cpu class", v["cpu"]["class"], exp["cpu_class"])
    want("gpu class", v["gpu"]["class"], exp["gpu_class"])
    want("gpu forge", v["gpu"]["forge"], exp["gpu_forge"])
    want("gpu memory tier", v["gpu"]["mem_tier"], exp["gpu_mem_tier"])
    want("tier", v["size"]["tier"], exp["tier"])
    by_id = {r["p"]: r for r in ranked}
    for pid, size in exp["sizes"].items():
        if pid not in by_id:
            errs.append("size: %s not ranked" % pid)
            continue
        want("size of " + pid, by_id[pid]["size"], size)
    want("ruled out", [r["p"] for r in v.get("ruled_out") or []], exp["ruled_out"])
    want("warnings", v.get("warnings"), exp["warnings"])
    # matrix 0.3.0 (single machine first): every ranked entry says whether its layout needs another machine,
    # as the matrix's profile does
    machines = matrix_machines()
    for r in ranked:
        want("machines of " + r["p"], r.get("machines"), machines.get(r["p"]))
    return errs


_MACHINES = {}


def matrix_machines():
    """Profile id -> machines (1 or 2) from install/matrix.json (an absent member counts as 1)."""
    if not _MACHINES:
        with open(os.path.join(SRC, "install", "matrix.json"), encoding="ascii") as fh:
            mt = json.load(fh)
        for p in mt["profiles"]:
            m = p.get("machines")
            _MACHINES[p["id"]] = 2 if isinstance(m, (int, float)) and not isinstance(m, bool) and m >= 2 else 1
    return _MACHINES


def fixture_tests(impls, tools):
    files = sorted(glob.glob(os.path.join(FIX, "*.json")))
    cases = []
    for f in files:
        try:
            with open(f, encoding="ascii") as fh:
                doc = json.load(fh)
        except ValueError:
            continue
        if isinstance(doc, dict) and "expect" in doc:
            cases.append((f, doc))
    verdicts = {}
    for f, doc in cases:
        name = doc["name"]
        per = {}
        for impl in impls:
            rc, out, errout = run(impl_cmd(impl, tools, ["report", "--facts", f, "--json"]))
            label = "%s %s" % (impl, name)
            if rc != 0:
                bad(label, "exit %d: %s" % (rc, errout.strip()[:300]))
                continue
            try:
                res = json.loads(out)
            except ValueError as e:
                bad(label, "output is not one JSON object: %s" % e)
                continue
            if out.strip().count("\n") != 0:
                bad(label, "JSON output is more than one line")
                continue
            if res.get("schema") != "deep32.inventory.v1" or res.get("mode") != "report" or res.get("impl") != impl:
                bad(label, "header fields wrong: %r" % {k: res.get(k) for k in ("schema", "mode", "impl")})
                continue
            if not num_eq(res.get("facts"), doc["facts"]):
                bad(label, "facts were not passed through unchanged")
                continue
            errs = check_expect(name, res["verdict"], doc["expect"])
            if errs:
                bad(label, "; ".join(errs))
            else:
                ok(label)
            per[impl] = res["verdict"]
        if len(per) == 2:
            if num_eq(per["ps1"], per["sh"]):
                ok("same verdict from ps1 and sh: " + name)
            else:
                bad("same verdict from ps1 and sh: " + name,
                    "ps1=%s sh=%s" % (json.dumps(per["ps1"], sort_keys=True), json.dumps(per["sh"], sort_keys=True)))
        verdicts[f] = per
    return cases, verdicts


def awk_dialect_tests(tools, cases, verdicts):
    gawk = shutil.which("gawk")
    if os.name == "nt":
        g = r"C:\Program Files\Git\usr\bin\gawk.exe"
        gawk = g if os.path.isfile(g) else None
    if not gawk or "sh" not in tools:
        print("SKIP  awk dialect tests (no gawk or no bash)")
        return
    text = open(SH, encoding="ascii").read()
    m = re.search(r"<<'AWK'\n(.*?)\nAWK\n", text, re.S)
    if not m:
        bad("awk dialect", "cannot find the awk program in the shell script")
        return
    prog = m.group(1)
    matrix = os.path.join(SRC, "install", "matrix.json")
    # The program goes through the environment and bash, exactly as the script hands it to awk;
    # a Windows command line would mangle its quotes.
    env = dict(os.environ, D32_MATRIX=matrix, LC_ALL="C", D32_PROG=prog, D32_GAWK=gawk.replace("\\", "/"))
    for mode in ("--posix", "--traditional", "--lint=fatal"):
        n_ok = 0
        for f, doc in cases:
            with open(f, encoding="ascii") as fh:
                rc, out, errout = run([tools["sh"], "-c", 'exec "$D32_GAWK" "$1" -v VERSION=test "$D32_PROG"', "gawk-test", mode],
                                      env=env, stdin=fh.read())
            label = "gawk %s %s" % (mode, doc["name"])
            if rc != 0:
                bad(label, "exit %d: %s" % (rc, errout.strip()[:300]))
                continue
            try:
                v = json.loads(out.splitlines()[0])
            except (ValueError, IndexError) as e:
                bad(label, "first line is not JSON: %s" % e)
                continue
            ref = verdicts.get(f, {}).get("sh")
            if ref is not None and not num_eq(v, ref):
                bad(label, "verdict differs from the system awk")
                continue
            errs = check_expect(doc["name"], v, doc["expect"])
            if errs:
                bad(label, "; ".join(errs))
                continue
            n_ok += 1
        if n_ok == len(cases):
            ok("gawk %s: all %d fixtures" % (mode, n_ok))
        # the gaming-mode reader in the same program (bytes as the script hands them over: NULs removed)
        gfile = os.path.join(HERE, "edge", "guards.json")
        if os.path.isfile(gfile):
            wants = json.load(open(gfile, encoding="ascii"))
            g_ok = 0
            for name, want in sorted(wants.items()):
                data = open(os.path.join(HERE, "edge", name), "rb").read().replace(b"\x00", b"")
                r = subprocess.run([tools["sh"], "-c", 'exec "$D32_GAWK" "$1" -v VERSION=test "$D32_PROG"', "gawk-test", mode],
                                   input=data, capture_output=True, env=dict(env, D32_GUARD_ONLY="1"), timeout=60)
                out = r.stdout.decode("ascii", "replace").strip()
                got = "gaming-mode" if out == "mode=on" else ("clear" if out.startswith("mode=") else "gaming-state-unreadable")
                if r.returncode != 0 or got != want:
                    bad("gawk %s guard %s" % (mode, name), "exit %d, %r, expected %s %s" % (r.returncode, out, want, r.stderr.decode("ascii", "replace")[:150]))
                else:
                    g_ok += 1
            if g_ok == len(wants):
                ok("gawk %s: all %d gaming-mode state files" % (mode, g_ok))


def cli_tests(impls, tools):
    some = os.path.join(FIX, "win-pro-rtx2080ti.json")
    g_on = os.path.join(FIX, "guard-gaming-on.json")
    g_bad = os.path.join(FIX, "guard-unreadable.json")
    g_off = os.path.join(FIX, "guard-gaming-off.json")
    # bench lines that must be refused for their arguments also carry a short time and a refusing guard
    # file, so a program that wrongly accepted them still could not start a long benchmark
    safe = ["--seconds", "1", "--guard-file", g_on]
    for impl in impls:
        def expect_rc(label, args, want_rc, check=None):
            rc, out, errout = run(impl_cmd(impl, tools, args))
            if rc != want_rc:
                bad("%s %s" % (impl, label), "exit %d, expected %d (%s)" % (rc, want_rc, (errout or out).strip()[:200]))
                return
            if check:
                why = check(out, errout)
                if why:
                    bad("%s %s" % (impl, label), why)
                    return
            ok("%s %s" % (impl, label))
        expect_rc("--version", ["--version"], 0, lambda o, e: None if "0.1.0" in o else "no version in output")
        expect_rc("--help", ["--help"], 0)
        expect_rc("unknown option", ["--bogus"], 2)
        expect_rc("unknown mode", ["fly"], 2)
        expect_rc("report with an extra word", ["report", "now"], 2)
        expect_rc("bench without quick", ["bench"] + safe, 2)
        expect_rc("bench system (later stage)", ["bench", "system"] + safe, 2)
        expect_rc("bench stress (later stage)", ["bench", "stress"] + safe, 2)
        expect_rc("--seconds 0", ["bench", "quick", "--guard-file", g_on, "--seconds", "0"], 2)
        expect_rc("--seconds 601", ["bench", "quick", "--guard-file", g_on, "--seconds", "601"], 2)
        expect_rc("--seconds abc", ["bench", "quick", "--guard-file", g_on, "--seconds", "abc"], 2)
        expect_rc("--seconds without a value", ["bench", "quick", "--guard-file", g_on, "--seconds"], 2)
        expect_rc("--seconds on report", ["report", "--seconds", "5"], 2)
        expect_rc("--net on bench", ["bench", "quick", "--net"] + safe, 2)
        expect_rc("--facts on bench", ["bench", "quick", "--facts", some] + safe, 2)
        expect_rc("missing facts file", ["report", "--facts", os.path.join(FIX, "no-such-file.json")], 2)

        def refused(reason):
            def chk(o, e):
                try:
                    j = json.loads(o)
                except ValueError:
                    return "not JSON: %r" % o[:120]
                return None if j.get("refused") == reason and j.get("mode") == "bench-quick" else "refused=%r" % j.get("refused")
            return chk
        expect_rc("bench refused while gaming mode is on", ["bench", "quick", "--seconds", "5", "--json", "--guard-file", g_on], 3, refused("gaming-mode"))
        expect_rc("bench refused when the gaming state is unreadable", ["bench", "quick", "--seconds", "5", "--json", "--guard-file", g_bad], 3, refused("gaming-state-unreadable"))
        if impl == "sh" and os.name == "nt":
            expect_rc("shell bench refused on Windows", ["bench", "quick", "--seconds", "5", "--json", "--guard-file", g_off], 3, refused("windows-use-ps1"))

        def matrix_err(o, e):
            try:
                j = json.loads(o)
            except ValueError:
                return "not JSON"
            return None if j.get("verdict") == {"error": "matrix-unavailable"} and j.get("facts") else "verdict=%r" % j.get("verdict")
        expect_rc("missing matrix gives exit 1 and a JSON error", ["report", "--facts", some, "--json", "--matrix", os.path.join(FIX, "no-such-matrix.json")], 1, matrix_err)
        expect_rc("matrix that is not a matrix", ["report", "--facts", some, "--json", "--matrix", g_on], 1, matrix_err)
        expect_rc("facts file without a facts member", ["report", "--facts", g_on, "--json"], 1)

        def page_chk(o, e):
            # win-pro-rtx2080ti (single machine first): the one-machine win-nvidia comes first although it
            # needs changes; the ready alternatives two-machines and headend-only follow, marked as needing
            # another machine
            for word in ("Processor", "Graphics", "Verdict    win-nvidia: Windows with an NVIDIA card (target reference, planned); fit: changes\n",
                         "missing: 160 GB free on one disk (provisional figure)", "missing: Port 7452 (relay) free on this PC",
                         "missing: Ports 8081, 8082 and 8443 (Forge) free on this PC",
                         "Next       two-machines: Two machines on one network (target supported, planned); fit: ready; needs another machine",
                         "Next       headend-only: Headend only (target supported, planned); fit: ready; needs another machine",
                         "size: Workstation 6 threads, 16 GB; Forge 4 threads, 16 GB"):
                if word not in o:
                    return "page lacks %r" % word
            return None
        expect_rc("page for a person", ["report", "--facts", some], 0, page_chk)

        def page_missing(o, e):
            # win-celeron: the best entry is headend-only, missing an agent; its sentence is printed. It is an
            # alternative (it needs another machine), best because no one-machine profile can run here
            for word in ("Verdict    headend-only: Headend only (target supported, planned); fit: changes; needs another machine",
                         "missing: Claude Code or Codex installed and signed in with your own account",
                         "Ruled out  win-nvidia"):
                if word not in o:
                    return "page lacks %r" % word
            return None
        expect_rc("page for a person with a missing requirement", ["report", "--facts", os.path.join(FIX, "win-celeron.json")], 0, page_missing)


PROBE_EXPECT = {
    "os.family": "linux", "os.name": "Ubuntu 24.04.1 LTS", "os.edition": "ubuntu", "os.version": "24.04",
    "os.arch": "x64", "os.wsl_guest": False,
    "hw.vendor": "Micro-Star International Co., Ltd.", "hw.model": "MS-7D25", "hw.virtual": False,
    "cpu.vendor": "intel", "cpu.model": "12th Gen Intel(R) Core(TM) i5-12600K", "cpu.family": 6, "cpu.model_id": 151,
    "cpu.stepping": 2, "cpu.sockets": 1, "cpu.cores": 10, "cpu.threads": 16, "cpu.p_cores": 6, "cpu.e_cores": 4,
    "cpu.p_threads": 12, "cpu.avx": True, "cpu.avx2": True, "cpu.avx512f": False, "cpu.gen": "Intel Core generation 12",
    "mem.total_gb": 31.1, "mem.installed_gb": None, "mem.free_gb": 19.1,
    "disks": [{"mount": "/data", "size_gb": 3725.9, "free_gb": 3630.5, "ssd": None},
              {"mount": "/", "size_gb": 931.1, "free_gb": 501.9, "ssd": True},
              {"mount": "/home/...", "size_gb": 465.5, "free_gb": 381.5, "ssd": None}],
    "gpus": [{"name": "Intel AlderLake-S GT1", "vendor": "intel", "mem_gb": None, "driver": None, "cc": None, "kernel_driver": None},
             {"name": "NVIDIA GeForce RTX 3060 Lite Hash Rate", "vendor": "nvidia", "mem_gb": None, "driver": "560.35.03", "cc": None, "kernel_driver": None}],
    "virt": {"firmware": True, "slat": True, "iommu": True, "hypervisor": False},
    "hyp": {"hyperv": False, "kvm": True, "proxmox": False, "virtualbox": False, "vmware": False, "libvirt": True},
    "wsl": None, "docker": {"present": True, "client": "27.3.1", "nvidia": True},
    "tools": {"tailscale": "1.76.6", "claude": False, "codex": "0.58.0", "gh": False},
    "ports": {"checked": True, "busy": [8766, 8081]},
}


def posix_path(p):
    if os.name == "nt" and len(p) > 2 and p[1] == ":":
        return "/" + p[0].lower() + p[2:].replace("\\", "/")
    return p


def prepare_probe_fixtures(work):
    """Copy synthetic source templates and materialise their real probe names in work."""
    generated = tempfile.mkdtemp(prefix="probe-fixtures-", dir=work)
    root = os.path.join(generated, "fakeroot-linux")
    stubs = os.path.join(generated, "fakebin-linux")
    shutil.copytree(os.path.join(HERE, "fakeroot-linux"), root)
    shutil.copytree(os.path.join(HERE, "fakebin-linux"), stubs)
    mappings = (
        (os.path.join(stubs, "agent-command.template"), os.path.join(stubs, "codex")),
        (os.path.join(stubs, "network-command.template"), os.path.join(stubs, "tailscale")),
        (os.path.join(root, "proc", "driver", "graphics-version.template"),
         os.path.join(root, "proc", "driver", "nvidia", "version")),
    )
    for source, target in mappings:
        if not os.path.isfile(source) or os.path.exists(target):
            raise RuntimeError("Synthetic fixture template missing or target already exists: " + source)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.rename(source, target)
    for name in os.listdir(stubs):
        os.chmod(os.path.join(stubs, name), 0o755)
    return root, stubs


def probe_tests(tools):
    """The Linux probes of the shell version on a fake system tree (fakeroot-linux) with stub commands."""
    root = PROBE_ROOT
    stubs = PROBE_BIN
    if "sh" not in tools or not os.path.isdir(root) or not os.path.isdir(stubs):
        print("SKIP  probe test (no bash or no fake tree)")
        return
    env = dict(os.environ, DEEP32_INVENTORY_TEST_ROOT=posix_path(root),
               DEEP32_INVENTORY_TEST_UNAME_S="Linux", DEEP32_INVENTORY_TEST_UNAME_M="x86_64")
    cmd = [tools["sh"], "-c", 'PATH="$1:/usr/bin:/bin"; export PATH; exec bash "$2" report --json',
           "probe-test", posix_path(stubs), posix_path(SH)]
    rc, out, errout = run(cmd, env=env)
    if rc != 0:
        bad("sh Linux probes on the fake tree", "exit %d: %s" % (rc, errout.strip()[:300]))
        return
    try:
        res = json.loads(out)
    except ValueError as e:
        bad("sh Linux probes on the fake tree", "not JSON: %s" % e)
        return
    errs = []
    for key, want in PROBE_EXPECT.items():
        if os.name == 'nt' and key == 'docker':
            want = dict(want, client=None)
        if os.name == 'nt' and key == 'tools':
            want = dict(want, tailscale='present', codex='present')
        got = res["facts"]
        for part in key.split("."):
            got = got.get(part) if isinstance(got, dict) else None
        if not num_eq(got, want):
            errs.append("%s: got %r, expected %r" % (key, got, want))
    # Worked by hand (again for matrix 0.3.0, "single machine first"): the RTX 3060 is usable
    # (rtx30), so no-gpu is not a candidate; the one-machine profiles come first: linux-nvidia misses
    # ports-forge (8081 busy), headless misses ports-engine (8766 busy) and ports-forge, both changes, so
    # by rank linux-nvidia 10 then headless 50; then the alternatives (machines 2), both ready (Codex
    # present): two-machines 40 third, headend-only 90 fourth (not shown). Before: two-machines,
    # headend-only, linux-nvidia.
    v = res["verdict"]
    if v.get("best") != "linux-nvidia" or v.get("fit") != "changes" or [r["p"] for r in v["ranked"]] != ["linux-nvidia", "headless", "two-machines"]:
        errs.append("verdict: %r" % {k: v.get(k) for k in ("best", "fit", "ranked")})
    else:
        ln = v["ranked"][0]
        if ln["fit"] != "changes" or ln["missing"] != ["ports-forge"] or ln["unknown"] != ["gpu-mem"] or ln["confirm"] != ["headend-elsewhere"] or ln.get("machines") != 1:
            errs.append("linux-nvidia: %r" % ln)
        if not num_eq(ln["size"], {"ws": [6, 12], "forge": [4, 16]}):
            errs.append("size: %r" % ln["size"])
        hl, tm = v["ranked"][1], v["ranked"][2]
        if hl["fit"] != "changes" or hl["missing"] != ["ports-engine", "ports-forge"] or hl.get("machines") != 1:
            errs.append("headless: %r" % hl)
        if tm["fit"] != "ready" or tm["confirm"] != ["second-machine"] or tm.get("machines") != 2:
            errs.append("two-machines: %r" % tm)
    if errs:
        bad("sh Linux probes on the fake tree", "; ".join(errs))
    else:
        ok("sh Linux probes on the fake tree")


EDGE = os.path.join(HERE, "edge")
DEAD_PROXY = {"http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
              "HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9", "no_proxy": "", "NO_PROXY": ""}


def run_bytes(cmd, env=None):
    r = subprocess.run(cmd, capture_output=True, env=env, timeout=120)
    return r.returncode, r.stdout, r.stderr


def edge_tests(impls, tools):
    """Awkward inputs: both programs must agree, and the JSON must stay ASCII and valid."""
    if not os.path.isdir(EDGE):
        print("SKIP  edge tests (no edge folder; run make_edge.py)")
        return
    same = ["facts-plain.json", "facts-bom8.json", "facts-utf16.json", "facts-crlf.json", "facts-unicode.json",
            "facts-huge-numbers.json", "facts-fraction-threads.json", "facts-ports-strings.json",
            "facts-gpus-object.json", os.path.join("dir with spaces", "facts copy.json")]
    plain_v = {}
    for name in same:
        per = {}
        for impl in impls:
            label = "%s edge %s" % (impl, name)
            rc, out, err = run_bytes(impl_cmd(impl, tools, ["report", "--facts", os.path.join(EDGE, name), "--json"]))
            if rc != 0:
                bad(label, "exit %d: %s" % (rc, err.decode("ascii", "replace").strip()[:200]))
                continue
            if impl == "ps1" and any(b > 127 for b in out):
                bad(label, "JSON output holds bytes outside ASCII")
                continue
            try:
                res = json.loads(out.decode("utf-8"))
            except ValueError as e:
                bad(label, "not valid JSON: %s" % e)
                continue
            per[impl] = res["verdict"]
            ok(label)
        if len(per) == 2:
            if num_eq(per["ps1"], per["sh"]):
                ok("same verdict from ps1 and sh: edge " + name)
            else:
                bad("same verdict from ps1 and sh: edge " + name, "ps1=%s sh=%s" % (json.dumps(per["ps1"])[:300], json.dumps(per["sh"])[:300]))
        if name == "facts-plain.json":
            plain_v = per
        elif name in ("facts-bom8.json", "facts-utf16.json", "facts-crlf.json", "facts-unicode.json",
                      os.path.join("dir with spaces", "facts copy.json")):
            for impl in per:
                if impl in plain_v and not num_eq(per[impl], plain_v[impl]):
                    bad("%s edge %s gives the plain verdict" % (impl, name), "differs from facts-plain.json")
        if name == "facts-fraction-threads.json":
            for impl in per:
                if per[impl]["size"]["threads"] != 12:
                    bad("%s 12.9 threads count as 12" % impl, "got %r" % per[impl]["size"]["threads"])
    for name in sorted(os.listdir(EDGE)):
        if not name.startswith("bad-"):
            continue
        for impl in impls:
            rc, out, err = run_bytes(impl_cmd(impl, tools, ["report", "--facts", os.path.join(EDGE, name), "--json"]))
            if rc == 1:
                ok("%s refuses %s" % (impl, name))
            else:
                bad("%s refuses %s" % (impl, name), "exit %d, expected 1" % rc)
    # a matrix with a byte-order mark or CRLF line ends
    per = {}
    for m in ("matrix-mini.json", "matrix-mini-bom.json", "matrix-mini-crlf.json"):
        for impl in impls:
            label = "%s reads %s" % (impl, m)
            rc, out, err = run_bytes(impl_cmd(impl, tools, ["report", "--facts", os.path.join(EDGE, "facts-plain.json"),
                                                             "--matrix", os.path.join(EDGE, m), "--json"]))
            try:
                v = json.loads(out.decode("utf-8"))["verdict"]
            except (ValueError, KeyError):
                v = None
            if rc != 0 or not v or v.get("best") != "mini" or v.get("fit") != "changes":
                bad(label, "exit %d, verdict %r" % (rc, v))
                continue
            per.setdefault(impl, []).append(v)
            ok(label)
    for impl, vs in per.items():
        if len(vs) == 3 and not (num_eq(vs[0], vs[1]) and num_eq(vs[0], vs[2])):
            bad("%s same verdict for the three matrix encodings" % impl, "differs")


def confirm_edge_tests(impls, tools, work):
    """The confirm kind and the fit-first order on a small matrix, worked by hand. The matrix and the facts
    are written with a UTF-8 byte-order mark and CRLF line ends into a folder whose name holds spaces.
    Profiles: "single" (rank 10) needs port 7452 free (fix) and tools.x true (human); "layout" (rank 50)
    needs a machine elsewhere (confirm, fact layout.elsewhere). Cases:
      a. nothing known about x or elsewhere, port free: single unconfirmed (x unknown); layout ready with
         elsewhere in confirm. Fit first: layout, single.
      b. layout.elsewhere false, port free, x unknown: layout changes (elsewhere missing, judged like human);
         single unconfirmed: single, layout.
      c. layout.elsewhere true, port busy, x true: layout ready with an empty confirm list; single changes
         (relay-port): layout, single.
      d. port free, x true, elsewhere unknown: both ready; rank decides: single, layout (elsewhere in confirm).
    Both programs must give these and agree."""
    folder = os.path.join(work, "confirm edge (x86) & co")
    os.makedirs(folder, exist_ok=True)
    mini = {
        "schema": "deep32.matrix.v1", "matrix_version": "confirm-mini", "ports": [{"port": 7452, "use": "relay"}],
        "cpu_classes": [{"id": "any", "when": [["os.arch", "eq", "x64"]], "workstation": "yes", "forge": "yes"}],
        "gpu_classes": [], "gpu_mem_tiers": [], "hw_profiles": [], "warnings": [],
        "sizing": {"host_keeps": {"threads": 2, "mem_gb": 4},
                   "tiers": [{"tier": 0, "min_threads": 0, "min_mem_gb": 0, "ws": {"threads": [4, 4], "mem_gb": [8, 8]}, "forge": None}],
                   "ws_alone_min": {"threads": 4, "mem_gb": 8}, "forge_alone_min": {"threads": 2, "mem_gb": 12}},
        "requirements": {"relay-port": {"kind": "fix", "when": [["port.7452", "eq", False]], "need": "Port 7452 free"},
                         "x-tool": {"kind": "human", "when": [["tools.x", "eq", True]], "need": "Tool x"},
                         "elsewhere": {"kind": "confirm", "when": [["layout.elsewhere", "eq", True]], "need": "A machine elsewhere"}},
        "profiles": [{"id": "single", "name": "Single", "target": "supported", "status": "planned", "rank": 10,
                      "match": [["os.family", "eq", "windows"]], "requires": ["relay-port", "x-tool"], "size": "none",
                      "parts": {"workstation": "x", "forge": "y", "relay": "z"}},
                     {"id": "layout", "name": "Layout", "target": "supported", "status": "planned", "rank": 50,
                      "match": [["os.family", "eq", "windows"]], "requires": ["elsewhere"], "size": "none",
                      "parts": {"workstation": "x", "forge": "y", "relay": "z"}}],
    }
    def put(name, obj):
        path = os.path.join(folder, name)
        text = json.dumps(obj, indent=1, sort_keys=True).replace("\n", "\r\n")
        with open(path, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + text.encode("ascii"))
        return path
    mpath = put("matrix confirm.json", mini)
    base = {"os": {"family": "windows", "arch": "x64"}, "cpu": {"threads": 8}, "mem": {"total_gb": 16.0}}
    cases = [("a", {"ports": {"checked": True, "busy": []}},
              [("layout", "ready", [], [], ["elsewhere"]), ("single", "unconfirmed", [], ["x-tool"], [])]),
             ("b", {"ports": {"checked": True, "busy": []}, "layout": {"elsewhere": False}},
              [("single", "unconfirmed", [], ["x-tool"], []), ("layout", "changes", ["elsewhere"], [], [])]),
             ("c", {"ports": {"checked": True, "busy": [7452]}, "tools": {"x": True}, "layout": {"elsewhere": True}},
              [("layout", "ready", [], [], []), ("single", "changes", ["relay-port"], [], [])]),
             ("d", {"ports": {"checked": True, "busy": []}, "tools": {"x": True}},
              [("single", "ready", [], [], []), ("layout", "ready", [], [], ["elsewhere"])])]
    for tag, extra, want in cases:
        f = dict(base)
        f.update(extra)
        fpath = put("facts %s.json" % tag, {"facts": f})
        per = {}
        for impl in impls:
            label = "%s confirm kind and fit-first order, case %s (BOM, CRLF, spaces)" % (impl, tag)
            rc, out, err = run_bytes(impl_cmd(impl, tools, ["report", "--facts", fpath, "--matrix", mpath, "--json"]))
            try:
                v = json.loads(out.decode("utf-8"))["verdict"]
            except (ValueError, KeyError):
                bad(label, "exit %d, not a verdict: %s" % (rc, (out or err)[:200]))
                continue
            got = [(r["p"], r["fit"], r["missing"], r["unknown"], r["confirm"]) for r in v.get("ranked") or []]
            exp = [(p, fit, list(m), list(u), list(c)) for p, fit, m, u, c in want]
            if rc != 0 or got != exp or v.get("best") != want[0][0] or v.get("fit") != want[0][1]:
                bad(label, "exit %d, got %r, expected %r" % (rc, got, exp))
                continue
            per[impl] = v
            ok(label)
        if len(per) == 2:
            if num_eq(per["ps1"], per["sh"]):
                ok("same verdict from ps1 and sh: confirm case " + tag)
            else:
                bad("same verdict from ps1 and sh: confirm case " + tag, "ps1=%s sh=%s" % (json.dumps(per["ps1"])[:300], json.dumps(per["sh"])[:300]))


def alternatives_edge_tests(impls, tools, work):
    """Single machine first (matrix 0.3.0, 2026-11-14), on a small matrix worked by hand. Profiles,
    in file order: "split" (rank 10, machines 2) needs a machine elsewhere (confirm, fact layout.elsewhere);
    "single" (rank 50, no machines member) needs box.big (hardware), port 7452 free (fix) and tools.x
    (human); "odd" (rank 60, machines "2", a string, so one machine) needs box.big. Cases:
      e. big true, port busy, x true, elsewhere unknown: odd ready, single changes (relay-port), split
         ready (elsewhere in confirm). One-machine profiles first, fit first within them: odd, single;
         then the alternative split, although it is ready and has the lowest rank.
      f. big false: single and odd are ruled out (box-big); split, ready, is the best: an alternative
         comes out best when no one-machine profile can run on the hardware.
      g. big unknown, port free, x true, elsewhere true: single and odd unconfirmed (box-big unknown),
         split ready with an empty confirm list: single, odd, split (an unknown hardware fact does not
         make the alternative best).
    Every ranked entry carries machines (2 only for split), and the page marks split "needs another
    machine". Both programs must give these and agree. Also: a matrix whose member is "Schema", or whose
    value is "Deep32.Matrix.V1" or a list, is not a matrix for either program (exit 1, matrix-unavailable),
    as the shell version has always read it (the .ps1 now compares case-sensitively)."""
    folder = os.path.join(work, "alternatives edge (single machine first)")
    os.makedirs(folder, exist_ok=True)
    mini = {
        "schema": "deep32.matrix.v1", "matrix_version": "alternatives-mini", "ports": [{"port": 7452, "use": "relay"}],
        "cpu_classes": [{"id": "any", "when": [["os.arch", "eq", "x64"]], "workstation": "yes", "forge": "yes"}],
        "gpu_classes": [], "gpu_mem_tiers": [], "hw_profiles": [], "warnings": [],
        "sizing": {"host_keeps": {"threads": 2, "mem_gb": 4},
                   "tiers": [{"tier": 0, "min_threads": 0, "min_mem_gb": 0, "ws": {"threads": [4, 4], "mem_gb": [8, 8]}, "forge": None}],
                   "ws_alone_min": {"threads": 4, "mem_gb": 8}, "forge_alone_min": {"threads": 2, "mem_gb": 12}},
        "requirements": {"relay-port": {"kind": "fix", "when": [["port.7452", "eq", False]], "need": "Port 7452 free"},
                         "x-tool": {"kind": "human", "when": [["tools.x", "eq", True]], "need": "Tool x"},
                         "box-big": {"kind": "hardware", "when": [["box.big", "eq", True]], "need": "A big box"},
                         "elsewhere": {"kind": "confirm", "when": [["layout.elsewhere", "eq", True]], "need": "A machine elsewhere"}},
        "profiles": [{"id": "split", "name": "Split", "target": "supported", "status": "planned", "rank": 10, "machines": 2,
                      "match": [["os.family", "eq", "windows"]], "requires": ["elsewhere"], "size": "none",
                      "parts": {"workstation": "x", "forge": "y", "relay": "z"}},
                     {"id": "single", "name": "Single", "target": "supported", "status": "planned", "rank": 50,
                      "match": [["os.family", "eq", "windows"]], "requires": ["box-big", "relay-port", "x-tool"], "size": "none",
                      "parts": {"workstation": "x", "forge": "y", "relay": "z"}},
                     {"id": "odd", "name": "Odd", "target": "experimental", "status": "planned", "rank": 60, "machines": "2",
                      "match": [["os.family", "eq", "windows"]], "requires": ["box-big"], "size": "none",
                      "parts": {"workstation": "x", "forge": "y", "relay": "z"}}],
    }

    def put(name, obj):
        path = os.path.join(folder, name)
        with open(path, "w", encoding="ascii", newline="\n") as fh:
            json.dump(obj, fh, indent=1, sort_keys=True)
        return path
    mpath = put("matrix alternatives.json", mini)
    base = {"os": {"family": "windows", "arch": "x64"}, "cpu": {"threads": 8}, "mem": {"total_gb": 16.0}}
    cases = [("e", {"ports": {"checked": True, "busy": [7452]}, "tools": {"x": True}, "box": {"big": True}},
              [("odd", "ready", [], [], [], 1), ("single", "changes", ["relay-port"], [], [], 1),
               ("split", "ready", [], [], ["elsewhere"], 2)], [], "Next       split: Split (target supported, planned); fit: ready; needs another machine"),
             ("f", {"ports": {"checked": True, "busy": []}, "tools": {"x": True}, "box": {"big": False}},
              [("split", "ready", [], [], ["elsewhere"], 2)], ["single", "odd"],
              "Verdict    split: Split (target supported, planned); fit: ready; needs another machine"),
             ("g", {"ports": {"checked": True, "busy": []}, "tools": {"x": True}, "layout": {"elsewhere": True}},
              [("single", "unconfirmed", [], ["box-big"], [], 1), ("odd", "unconfirmed", [], ["box-big"], [], 1),
               ("split", "ready", [], [], [], 2)], [], "Next       odd: Odd (target experimental, planned); fit: unconfirmed\n")]
    for tag, extra, want, ruled, page_line in cases:
        f = dict(base)
        f.update(extra)
        fpath = put("facts %s.json" % tag, {"facts": f})
        per = {}
        for impl in impls:
            label = "%s single machine first, case %s" % (impl, tag)
            rc, out, err = run(impl_cmd(impl, tools, ["report", "--facts", fpath, "--matrix", mpath, "--json"]))
            try:
                v = json.loads(out)["verdict"]
            except (ValueError, KeyError, TypeError):
                bad(label, "exit %d, not a verdict: %s" % (rc, (out or err)[:200]))
                continue
            got = [(r["p"], r["fit"], r["missing"], r["unknown"], r["confirm"], r.get("machines")) for r in v.get("ranked") or []]
            exp = [(p, fit, list(m), list(u), list(c), mc) for p, fit, m, u, c, mc in want]
            got_ruled = [r["p"] for r in v.get("ruled_out") or []]
            if rc != 0 or got != exp or got_ruled != ruled or v.get("best") != want[0][0] or v.get("fit") != want[0][1]:
                bad(label, "exit %d, got %r ruled out %r, expected %r ruled out %r" % (rc, got, got_ruled, exp, ruled))
                continue
            rc2, page, err2 = run(impl_cmd(impl, tools, ["report", "--facts", fpath, "--matrix", mpath]))
            if rc2 != 0 or page_line not in page:
                bad(label + ", page", "exit %d, page lacks %r: %s" % (rc2, page_line, page[-600:]))
                continue
            per[impl] = v
            ok(label)
        if len(per) == 2:
            if num_eq(per["ps1"], per["sh"]):
                ok("same verdict from ps1 and sh: single machine first, case " + tag)
            else:
                bad("same verdict from ps1 and sh: single machine first, case " + tag,
                    "ps1=%s sh=%s" % (json.dumps(per["ps1"])[:300], json.dumps(per["sh"])[:300]))
    # the schema check is case-sensitive in both programs
    fpath = put("facts schema.json", {"facts": base})
    for label, change in (("member Schema", lambda d: d.update({"Schema": d.pop("schema")})),
                          ("value Deep32.Matrix.V1", lambda d: d.update({"schema": "Deep32.Matrix.V1"})),
                          ("value a list", lambda d: d.update({"schema": ["deep32.matrix.v1"]}))):
        m2 = json.loads(json.dumps(mini))
        change(m2)
        p2 = put("matrix schema %s.json" % label.split()[-1].replace(".", "-"), m2)
        for impl in impls:
            rc, out, err = run(impl_cmd(impl, tools, ["report", "--facts", fpath, "--matrix", p2, "--json"]))
            try:
                j = json.loads(out)
            except ValueError:
                j = {}
            if rc == 1 and j.get("verdict") == {"error": "matrix-unavailable"} and j.get("facts"):
                ok("%s matrix with the schema %s is not a matrix (exit 1, matrix-unavailable)" % (impl, label))
            else:
                bad("%s matrix with the schema %s" % (impl, label), "exit %d, verdict %r" % (rc, j.get("verdict")))


def class_tests(impls, tools):
    """Each card, processor and laptop name gets its class from both programs."""
    if not os.path.isdir(EDGE):
        return
    for name in sorted(os.listdir(EDGE)):
        if not name.startswith("class-"):
            continue
        path = os.path.join(EDGE, name)
        want = json.load(open(path, encoding="ascii"))["expect_class"]
        for impl in impls:
            label = "%s class %s" % (impl, name[6:-5])
            rc, out, err = run_bytes(impl_cmd(impl, tools, ["report", "--facts", path, "--json"]))
            if rc != 0:
                bad(label, "exit %d" % rc)
                continue
            v = json.loads(out.decode("utf-8"))["verdict"]
            errs = []
            if "gpu" in want and v["gpu"]["class"] != want["gpu"]:
                errs.append("gpu class %r, expected %r" % (v["gpu"]["class"], want["gpu"]))
            if "cpu" in want and v["cpu"]["class"] != want["cpu"]:
                errs.append("cpu class %r, expected %r" % (v["cpu"]["class"], want["cpu"]))
            if "hw_profile" in want:
                seen = [r["p"] for r in v["ranked"]] + [r["p"] for r in v["ruled_out"]]
                if ("baremetal" in seen) != want["hw_profile"]:
                    errs.append("baremetal candidate %r, expected %r" % ("baremetal" in seen, want["hw_profile"]))
            if errs:
                bad(label, "; ".join(errs))
            else:
                ok(label)


def has_guard_hook(impl):
    """True when the program's text knows the guard-only hook; without it a "clear" guard would start a real bench."""
    text = open(PS1 if impl == "ps1" else SH, encoding="ascii", errors="replace").read()
    return "DEEP32_INVENTORY_TEST_GUARD_ONLY" in text


def guard_tests(impls, tools):
    """Every gaming-mode state file form, through the guard-only hook: nothing is ever measured."""
    if not os.path.isdir(EDGE):
        return
    wants = json.load(open(os.path.join(EDGE, "guards.json"), encoding="ascii"))
    nofile = os.path.join(EDGE, "no-such-state-file.json")
    gon, goff = os.path.join(EDGE, "guard-on.json"), os.path.join(EDGE, "guard-off.json")

    def guard_env(impl, env, extra=None):
        args = ["bench", "quick", "--seconds", "1", "--json"] + (["--guard-file", extra] if extra else [])
        rc, out, err = run_bytes(impl_cmd(impl, tools, args), env=env)
        try:
            j = json.loads(out.decode("ascii"))
        except ValueError:
            return rc, "not JSON: %r" % out[:100]
        return rc, j.get("refused") or ("clear" if j.get("guard") == "clear" else "?%r" % j)

    def guard(impl, default, extra=None):
        env = dict(os.environ, DEEP32_INVENTORY_TEST_GUARD_ONLY="1", DEEP32_INVENTORY_TEST_DEFAULT_GUARD=default)
        return guard_env(impl, env, extra)

    # The state file comes only from DEEP32_GUARD_FILE (or --guard-file): neither program holds a path of
    # its own. Read from the program text, since no run on a given machine can show that a path is absent.
    for impl, line, name in (("ps1", "$DefaultGuardFile = $env:DEEP32_GUARD_FILE", "$DefaultGuardFile ="),
                             ("sh", 'DEFAULT_GUARD="${DEEP32_GUARD_FILE:-}"', "DEFAULT_GUARD=")):
        if impl not in impls:
            continue
        lines = open(PS1 if impl == "ps1" else SH, encoding="ascii", errors="replace").read().splitlines()
        sets = [l.strip() for l in lines if l.strip().startswith(name)]
        if sets == [line]:
            ok("%s guard state file: only DEEP32_GUARD_FILE, no built-in path" % impl)
        else:
            bad("%s guard state file: only DEEP32_GUARD_FILE, no built-in path" % impl, "assignments %r" % sets)
    # DEEP32_GUARD_FILE itself, with the hook's own replacement left unset
    var_env = {k: v for k, v in os.environ.items()
               if k.upper() not in ("DEEP32_GUARD_FILE", "DEEP32_INVENTORY_TEST_DEFAULT_GUARD")}
    var_env["DEEP32_INVENTORY_TEST_GUARD_ONLY"] = "1"
    var_cases = (("DEEP32_GUARD_FILE on", gon, None, "gaming-mode"),
                 ("DEEP32_GUARD_FILE off", goff, None, "clear"),
                 ("DEEP32_GUARD_FILE off, --guard-file on", goff, gon, "gaming-mode"),
                 ("DEEP32_GUARD_FILE on, --guard-file off", gon, goff, "gaming-mode"),
                 ("DEEP32_GUARD_FILE unreadable", os.path.join(EDGE, "guard-broken.json"), None, "gaming-state-unreadable"),
                 ("DEEP32_GUARD_FILE names no file", nofile, None, "clear"),
                 ("DEEP32_GUARD_FILE empty, --guard-file on", "", gon, "gaming-mode"),
                 ("DEEP32_GUARD_FILE empty, no --guard-file", "", None, "clear"),
                 ("DEEP32_GUARD_FILE unset, --guard-file on", None, gon, "gaming-mode"),
                 ("DEEP32_GUARD_FILE unset, no --guard-file", None, None, "clear"))

    for impl in impls:
        if not has_guard_hook(impl):
            bad("%s guard tests" % impl, "the program has no guard-only test hook; not run, so no real bench can start")
            continue
        # the hook itself must stop before measuring: a refusing file first, then a clear one
        rc, got = guard(impl, gon)
        if rc != 3 or got != "gaming-mode":
            bad("%s guard hook refuses with the default file on" % impl, "exit %d, %s; skipping the clear cases" % (rc, got))
            continue
        ok("%s guard hook refuses with the default file on" % impl)
        for name in sorted(wants):
            want = wants[name]
            rc, got = guard(impl, nofile, os.path.join(EDGE, name))
            want_rc = 0 if want == "clear" else 3
            if rc == want_rc and got == want:
                ok("%s guard %s -> %s" % (impl, name, want))
            else:
                bad("%s guard %s" % (impl, name), "exit %d %s, expected %d %s" % (rc, got, want_rc, want))
        for label, default, extra, want in (("default on, --guard-file off", gon, goff, "gaming-mode"),
                                            ("default off, --guard-file on", goff, gon, "gaming-mode"),
                                            ("default missing, --guard-file off", nofile, goff, "clear"),
                                            ("default unreadable, no --guard-file", os.path.join(EDGE, "guard-broken.json"), None, "gaming-state-unreadable")):
            rc, got = guard(impl, default, extra)
            if got == want and rc == (0 if want == "clear" else 3):
                ok("%s guard %s -> %s" % (impl, label, want))
            else:
                bad("%s guard %s" % (impl, label), "exit %d %s, expected %s" % (rc, got, want))
        for label, var, extra, want in var_cases:
            env = dict(var_env)
            if var is not None:
                env["DEEP32_GUARD_FILE"] = var
            rc, got = guard_env(impl, env, extra)
            if got == want and rc == (0 if want == "clear" else 3):
                ok("%s guard %s -> %s" % (impl, label, want))
            else:
                bad("%s guard %s" % (impl, label), "exit %d %s, expected %s" % (rc, got, want))


def arg_edge_tests(impls, tools):
    """Empty option values and odd --seconds spellings: both programs must agree."""
    if not os.path.isdir(EDGE):
        return
    facts = os.path.join(EDGE, "facts-plain.json")
    gon = os.path.join(EDGE, "guard-on.json")
    env = dict(os.environ, DEEP32_INVENTORY_TEST_GUARD_ONLY="1", DEEP32_INVENTORY_TEST_DEFAULT_GUARD=os.path.join(EDGE, "no-such-state-file.json"))
    # Every bench line here carries --seconds 1 and, where it can, a refusing guard file, so that even a
    # program that wrongly accepted the arguments could not start a long benchmark.
    cases = [("--facts ''", ["report", "--facts", "", "--json"], 2),
             ("--matrix ''", ["report", "--facts", facts, "--matrix", "", "--json"], 2),
             ("--guard-file ''", ["bench", "quick", "--seconds", "1", "--guard-file", "", "--guard-file", gon, "--json"], 2),
             ("--seconds +5", ["bench", "quick", "--seconds", "+5", "--guard-file", gon], 2),
             ("--seconds ' 5'", ["bench", "quick", "--seconds", " 5", "--guard-file", gon], 2),
             ("--seconds 1.5", ["bench", "quick", "--seconds", "1.5", "--guard-file", gon], 2),
             ("--seconds 000", ["bench", "quick", "--seconds", "000", "--guard-file", gon], 2),
             ("--seconds with ten digits", ["bench", "quick", "--seconds", "0000000600", "--guard-file", gon], 2),
             # accepted values reach the guard, which refuses (exit 3): a real bench can never start here
             ("--seconds 000000600 accepted", ["bench", "quick", "--seconds", "000000600", "--json", "--guard-file", gon], 3),
             ("--seconds 08 accepted", ["bench", "quick", "--seconds", "08", "--json", "--guard-file", gon], 3)]
    for impl in impls:
        if not has_guard_hook(impl):
            bad("%s argument edge tests" % impl, "the program has no guard-only test hook; not run")
            continue
        for label, args, want in cases:
            rc, out, err = run_bytes(impl_cmd(impl, tools, args), env=env)
            if rc == want:
                ok("%s %s -> exit %d" % (impl, label, want))
            else:
                bad("%s %s" % (impl, label), "exit %d, expected %d" % (rc, want))


def net_tests(impls, tools):
    """The --net code paths, offline: stubs answer instead of the network."""
    stubs = os.path.join(HERE, "fakebin-net")
    want_sites = ["github", "huggingface", "ubuntu", "nvidia", "tailscale"]
    if "sh" in tools and os.path.isdir(stubs):
        root = PROBE_ROOT
        env = dict(os.environ, DEEP32_INVENTORY_TEST_ROOT=posix_path(root),
                   DEEP32_INVENTORY_TEST_UNAME_S="Linux", DEEP32_INVENTORY_TEST_UNAME_M="x86_64", **DEAD_PROXY)
        pre = 'PATH="$1:$2:/usr/bin:/bin"; export PATH; [ "$(command -v curl)" = "$1/curl" ] && [ "$(command -v gh)" = "$1/gh" ] || { echo "stubs not first in PATH" >&2; exit 9; }; '
        cmd = [tools["sh"], "-c", pre + 'exec bash "$3" report --net --json', "net-test",
               posix_path(stubs), posix_path(PROBE_BIN), posix_path(SH)]
        rc, out, err = run(cmd, env=env)
        label = "sh --net offline with stub curl and gh"
        if rc != 0:
            bad(label, "exit %d: %s" % (rc, err.strip()[:200]))
        else:
            n = json.loads(out)["facts"]["net"]
            if os.name == 'nt':
                if all(n[k] == {'ok': False, 'code': None, 'ms': None} for k in want_sites) and n['gh_signed_in'] is None:
                    ok('sh unsupported containment leaves network and auth unavailable without launching tools')
                else:
                    bad(label, 'unsupported containment produced a positive probe: %r' % n)
                n = None
            errs = []
            if n is None:
                pass
            elif sorted(k for k in n if k != "gh_signed_in") != sorted(want_sites):
                errs.append("sites %r" % sorted(n))
            if n is not None and n.get("github") != {"ok": True, "code": 200, "ms": 42}:
                errs.append("github %r" % n.get("github"))
            if n is not None and n.get("huggingface", {}).get("ok") is not False:
                errs.append("huggingface %r" % n.get("huggingface"))
            if n is not None and n.get("tailscale", {}).get("code") != 302:
                errs.append("tailscale %r" % n.get("tailscale"))
            if n is not None and n.get("gh_signed_in") is not True:
                errs.append("gh_signed_in %r" % n.get("gh_signed_in"))
            if errs:
                bad(label, "; ".join(errs))
            else:
                ok(label)
    if "ps1" in tools:
        text = open(PS1, encoding="ascii").read()
        m = re.search(r"^function Get-NetFacts \{.*?^\}\n", text, re.S | re.M)
        if not m:
            bad("ps1 --net function with stubs", "cannot find Get-NetFacts")
            return
        script = (
            "$PSDefaultParameterValues['Invoke-WebRequest:Proxy'] = 'http://127.0.0.1:9'\n"
            "$UserAgent = 'deep32-inventory/test'\n$script:Calls = New-Object System.Collections.ArrayList\n"
            "function Get-InventoryHttp { param($Uri,$TimeoutMs)\n"
            "  if($TimeoutMs -ne 8000){throw 'wrong timeout'}\n"
            "  [void]$script:Calls.Add('Head ' + $Uri + ' ' + $UserAgent)\n"
            "  if ($Uri -like '*huggingface*') { return @{ok=$false;code=$null;ms=0} }\n"
            "  return @{ok=$true;code=200;ms=0} }\n"
            "function Find-Tool { param($Name, $Extra) if ($Name -eq 'gh') { return 'C:\\stub\\gh.exe' }; return $null }\n"
            "function Invoke-Tool { param($Path, $Arguments, $TimeoutMs) return @{ code = 0; out = ''; err = '' } }\n"
            + m.group(0) +
            "$n = Get-NetFacts\n"
            "[Console]::Out.WriteLine((ConvertTo-Json -InputObject ([ordered]@{ net = $n; calls = @($script:Calls) }) -Depth 5 -Compress))\n")
        enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        rc, out, err = run([tools["ps1"], "-NoProfile", "-NonInteractive", "-EncodedCommand", enc])
        label = "ps1 --net function offline with stub web and tool calls"
        try:
            j = json.loads(out)
        except ValueError:
            bad(label, "exit %d, not JSON: %s %s" % (rc, out[:200], err[:200]))
            return
        n, calls = j["net"], j["calls"]
        errs = []
        if sorted(k for k in n if k != "gh_signed_in") != sorted(want_sites):
            errs.append("sites %r" % sorted(n))
        if n["github"]["ok"] is not True or n["github"]["code"] != 200 or n["huggingface"]["ok"] is not False:
            errs.append("results %r" % n)
        if n.get("gh_signed_in") is not True:
            errs.append("gh_signed_in %r" % n.get("gh_signed_in"))
        if len(calls) != 5 or not all(c.startswith("Head https://") or c.startswith("Head http://") for c in calls):
            errs.append("calls %r" % calls)
        if not all(c.endswith(" deep32-inventory/test") for c in calls):
            errs.append("user agent %r" % calls)
        if errs:
            bad(label, "; ".join(errs))
        else:
            ok(label)


def matrix_static_tests():
    """Every regular expression in the matrix keeps to the subset both programs share."""
    mt = json.load(open(os.path.join(SRC, "install", "matrix.json"), encoding="ascii"))
    pats = [p for c in mt["gpu_classes"] for p in c["names"]]
    def walk(x):
        if isinstance(x, list):
            if len(x) == 3 and x[1] == "re" and isinstance(x[2], str):
                pats.append(x[2])
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)
    walk(mt)
    badp = [p for p in pats if "\\" in p or "{" in p or p != p.lower()]
    if badp:
        bad("matrix regex subset", "outside the subset: %r" % badp)
    else:
        ok("matrix regex subset: %d patterns, no backslash, no braces, lower case" % len(pats))
    ids = [p["id"] for p in mt["profiles"]]
    want = ["win-nvidia", "proxmox-passthrough", "two-machines", "linux-nvidia", "headless", "no-gpu", "baremetal",
            "headend-only", "win-home", "virtualbox", "vmware", "esxi"]
    if sorted(ids) == sorted(want) and all(p["status"] == "planned" and p["last_tested"] is None for p in mt["profiles"]):
        ok("matrix holds the twelve profiles, all planned and untested")
    else:
        bad("matrix profiles", "ids %r" % ids)
    req = set(mt["requirements"])
    missing = sorted({r for p in mt["profiles"] for r in p["requires"]} - req)
    if missing:
        bad("matrix requirement ids", "used but not defined: %r" % missing)
    else:
        ok("matrix requirement ids all defined")
    # matrix 0.3.0 (single machine first): every profile says how many machines its layout needs; exactly the layouts
    # that need another machine for the Workstation or the Forge carry 2
    two = sorted(p["id"] for p in mt["profiles"] if p.get("machines") == 2)
    odd = sorted(p["id"] for p in mt["profiles"] if p.get("machines") not in (1, 2) or isinstance(p.get("machines"), bool))
    if odd or two != ["baremetal", "headend-only", "two-machines"]:
        bad("matrix machines", "machines 2 on %r; not 1 or 2 on %r" % (two, odd))
    else:
        ok("matrix machines: 2 on baremetal, headend-only, two-machines, 1 on the other nine")
    tier0 = [t for t in mt["gpu_mem_tiers"] if t["id"] == "0"]
    if tier0 and tier0[0]["runs"].startswith("keyword search only") and "and stored meaning search" not in tier0[0]["runs"]:
        ok("matrix card memory tier 0 says keyword search only")
    else:
        bad("matrix card memory tier 0", "text %r" % (tier0[0]["runs"] if tier0 else None))


def ps_function(text, name):
    """The text of one top-level PowerShell function of the program (from "function NAME" to its closing brace)."""
    m = re.search(r"^function %s\b.*?^\}\n" % re.escape(name), text, re.S | re.M)
    return m.group(0) if m else None


def ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


def run_ps_script(tools, script, timeout=150):
    """Runs a PowerShell script given as text; returns (exit code, stdout, stderr), or None on overrun."""
    path = os.path.join(RUN_WORK, 'synthetic-timeout-script.ps1')
    with open(path,'w',encoding='utf-8',newline='\n') as stream:
        stream.write("$ProgressPreference='SilentlyContinue'\n"+script)
    try:
        r = subprocess.run([tools["ps1"], "-NoProfile", "-NonInteractive", '-ExecutionPolicy','Bypass','-File',path],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return r.returncode, r.stdout, r.stderr


def timeout_script(ps1_text, tool, mark, timeout_ms, no_process_list):
    """A PowerShell script that runs Invoke-Tool (taken from the program's own text) on the fake slow
    tool, then reports which of the fake tool's processes are still running. It starts a bystander
    process of its own first: Invoke-Tool did not start it, so it must still be running afterwards.
    Anything of the fake tool still running is ended here, only after its id and start time match
    what the fake process wrote itself; the bystander is ended here too. Nothing else is touched."""
    funcs = [f for f in (ps_function(ps1_text, "Initialize-InventoryRuntime"), ps_function(ps1_text, "Invoke-Tool")) if f]
    stub = ""
    if no_process_list:
        stub = "function Get-CimInstance { throw 'test stub: the process list cannot be read' }\n"
    return (
        "$ErrorActionPreference = 'Continue'\n" + "".join(funcs) + stub +
        "$mark = " + ps_quote(mark) + "\n"
        "$env:D32_TEST_MARK = $mark\n$env:D32_TEST_SLEEP = '60'\n"
        "$bpsi = New-Object System.Diagnostics.ProcessStartInfo\n"
        "$bpsi.FileName = Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'\n"
        "$bpsi.Arguments = '-NoProfile -NonInteractive -Command \"Start-Sleep -Seconds 60\"'\n"
        "$bpsi.UseShellExecute = $false; $bpsi.CreateNoWindow = $true\n"
        "$by = [System.Diagnostics.Process]::Start($bpsi)\n"
        "$t0 = [DateTime]::UtcNow\n"
        "$r = Invoke-Tool " + ps_quote(tool) + " '--version' " + str(int(timeout_ms)) + "\n"
        "$ms = [int]([DateTime]::UtcNow - $t0).TotalMilliseconds\n"
        "$out = $null; if ($null -ne $r) { $out = [string]$r.out }\n"
        "$res = [ordered]@{ returned_null = ($null -eq $r); out = $out; ms = $ms; child = $null; grandchild = $null; ended_by_test = @() }\n"
        "foreach ($role in @('child', 'grandchild')) {\n"
        "    $f = Join-Path $mark ($role + '.pid')\n"
        "    if (-not (Test-Path -LiteralPath $f)) { $res[$role] = 'never-started'; continue }\n"
        "    $parts = ([System.IO.File]::ReadAllText($f)).Trim() -split ' '\n"
        "    $state = 'ended'\n"
        "    try {\n"
        "        $q = [System.Diagnostics.Process]::GetProcessById([int]$parts[0])\n"
        "        [void]$q.Handle\n"
        "        if ([math]::Abs($q.StartTime.ToUniversalTime().Ticks - [long]$parts[1]) -lt 10) {\n"
        "            if (-not $q.WaitForExit(3000)) { $state = 'still-running'; try { $q.Kill(); $res.ended_by_test += $role } catch { } }\n"
        "        }\n"
        "    } catch { }\n"
        "    $res[$role] = $state\n"
        "}\n"
        "$res.bystander_alive = -not $by.HasExited\n"
        "try { $by.Kill() } catch { }\n"
        "[Console]::Out.WriteLine((ConvertTo-Json -InputObject $res -Depth 4 -Compress))\n")


def timeout_case(tools, ps1_text, work, label, no_process_list, timeout_ms=8000, tool=None, want_out=None):
    """One run of a fake tool (default the slow one) through Invoke-Tool; returns the list of problems
    (empty = passed). want_out None: Invoke-Tool must report the overrun (return nothing); else it must
    return a result whose output holds want_out."""
    if tool is None:
        tool = os.path.join(HERE, "fakebin-win", "slow-tool.cmd")
    mark = os.path.join(work, "%s-%d" % (label, os.getpid()))
    os.makedirs(mark, exist_ok=True)
    for role in ("child", "grandchild"):
        p = os.path.join(mark, role + ".pid")
        if os.path.exists(p):
            os.remove(p)
    got = run_ps_script(tools, timeout_script(ps1_text, tool, mark, timeout_ms, no_process_list))
    if got is None:
        return ["the test script overran 150 s"]
    rc, out, err = got
    try:
        j = json.loads(out)
    except ValueError:
        return ["exit %d, not JSON: %s %s" % (rc, out[:200], err[:300])]
    errs = []
    if want_out is None and j.get("returned_null") is not True:
        errs.append("Invoke-Tool did not report the overrun (returned a result)")
    if want_out is not None and (j.get("returned_null") is not False or want_out not in (j.get("out") or "")):
        errs.append("Invoke-Tool returned %r, expected output holding %r" % (j.get("out"), want_out))
    if not isinstance(j.get("ms"), int) or j["ms"] > timeout_ms + 20000:
        errs.append("Invoke-Tool took %r ms" % j.get("ms"))
    for role in ("child", "grandchild"):
        if j.get(role) == "never-started":
            errs.append("the fake tool's %s never started (inconclusive; machine too slow for %d ms?)" % (role, timeout_ms))
        elif j.get(role) != "ended":
            errs.append("the fake tool's %s was left running (%r); the test ended it" % (role, j.get(role)))
    if j.get("bystander_alive") is not True:
        errs.append("a process Invoke-Tool did not start was ended")
    return errs


def timeout_tests(tools, work):
    """Invoke-Tool on a fake .cmd tool (fakebin-win) whose child starts a grandchild and hangs: at the time
    limit the whole tree the program started must end, also when the process list cannot be read (the
    taskkill path), and a process the program did not start must live on. A quick .cmd tool still works."""
    if "ps1" not in tools or os.name != "nt":
        print("SKIP  tool time-limit tests (Windows PowerShell on Windows only)")
        return
    text = open(PS1, encoding="ascii").read()
    if not ps_function(text, "Invoke-Tool"):
        bad("ps1 tool time limit", "cannot find Invoke-Tool")
        return
    for label, no_list, name in (("tree", False, "ps1 tool time limit ends a .cmd tool, its child and its grandchild, nothing else"),
                                 ("taskkill", True, "ps1 tool time limit without a readable process list (taskkill /T), nothing else")):
        errs = timeout_case(tools, text, work, label, no_list)
        if errs:
            bad(name, "; ".join(errs))
        else:
            ok(name)
    # the same from a folder whose name holds spaces, parentheses and an ampersand (a copy under the work folder)
    odd = os.path.join(work, "tool dir (x86) & co")
    os.makedirs(odd, exist_ok=True)
    for f in ("slow-tool.cmd", "slow-child.ps1"):
        shutil.copyfile(os.path.join(HERE, "fakebin-win", f), os.path.join(odd, f))
    name = "ps1 tool time limit from a folder with spaces, parentheses and &: whole tree ended, nothing else"
    errs = timeout_case(tools, text, work, "spaces", False, tool=os.path.join(odd, "slow-tool.cmd"))
    if errs:
        bad(name, "; ".join(errs))
    else:
        ok(name)
    # a tool that ends at once but leaves a background child holding its output open
    name = "ps1 a .cmd tool that leaves a background child holding its output: output back within the limit, that tree ended, nothing else"
    errs = timeout_case(tools, text, work, "bg", False, tool=os.path.join(HERE, "fakebin-win", "bg-tool.cmd"))
    if errs:
        bad(name, "; ".join(errs))
    else:
        ok(name)
    fast = os.path.join(HERE, "fakebin-win", "fast-tool.cmd")
    script = ("$ErrorActionPreference = 'Continue'\n" +
              "".join(f for f in (ps_function(text, "Initialize-InventoryRuntime"), ps_function(text, "Invoke-Tool")) if f) +
              "$r = Invoke-Tool " + ps_quote(fast) + " '--version' 15000\n"
              "[Console]::Out.WriteLine((ConvertTo-Json -InputObject ([ordered]@{ code = $r.code; out = $r.out }) -Compress))\n")
    got = run_ps_script(tools, script, timeout=60)
    name = "ps1 runs a quick .cmd tool and reads its output"
    try:
        j = json.loads(got[1]) if got else None
    except ValueError:
        j = None
    if j and j.get("code") == 0 and "4.5.6" in (j.get("out") or ""):
        ok(name)
    else:
        bad(name, "got %r" % (got,))


def main():
    global QUIET, PROBE_ROOT, PROBE_BIN, RUN_WORK
    ap = argparse.ArgumentParser(description="tests for deep32-inventory")
    ap.add_argument("--impl", choices=("ps1", "sh", "both"), default="both")
    ap.add_argument("--bash")
    ap.add_argument("--powershell")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--work")
    try:
        a = ap.parse_args()
    except SystemExit:
        return 2
    QUIET = a.quiet
    tools = {}
    if a.impl in ("sh", "both"):
        b = find_bash(a.bash)
        if b and os.name == "nt" and unsafe_bash(b):
            print("refusing %s: on Windows that bash starts WSL" % b)
            return 2
        if b:
            tools["sh"] = b
        else:
            print("SKIP  sh: no bash found")
    if a.impl in ("ps1", "both"):
        p = find_ps(a.powershell)
        if p:
            tools["ps1"] = p
        else:
            print("SKIP  ps1: no Windows PowerShell or pwsh found")
    impls = [i for i in ("ps1", "sh") if i in tools]
    if not impls:
        return 2
    print("implementations: " + ", ".join("%s=%s" % (i, tools[i]) for i in impls))
    temporary_work = None
    if a.work:
        work = os.path.abspath(a.work)
        os.makedirs(work, exist_ok=True)
    else:
        temporary_work = work = tempfile.mkdtemp(prefix="d32inv-")
    try:
        RUN_WORK = work
        PROBE_ROOT, PROBE_BIN = prepare_probe_fixtures(work)
        cases, verdicts = fixture_tests(impls, tools)
        awk_dialect_tests(tools, cases, verdicts)
        probe_tests(tools)
        cli_tests(impls, tools)
        edge_tests(impls, tools)
        class_tests(impls, tools)
        guard_tests(impls, tools)
        arg_edge_tests(impls, tools)
        net_tests(impls, tools)
        confirm_edge_tests(impls, tools, work)
        alternatives_edge_tests(impls, tools, work)
        timeout_tests(tools, work)
    finally:
        if temporary_work:
            shutil.rmtree(temporary_work, ignore_errors=True)
    matrix_static_tests()
    print("%d passed, %d failed" % (PASSES, len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
