#!/usr/bin/env bash
# deep32-inventory.sh: what this Linux or macOS machine has, and which Deep32 install profile fits it.
#
# What it does:
#   report (default)  Reads the machine: processor (model, maker, family/model/stepping, AVX,
#                     AVX2, AVX-512F from /proc/cpuinfo or sysctl, cores and threads, fast and
#                     efficient cores where the system reports them), memory, disks and free
#                     space, graphics cards (nvidia-smi --query-gpu when present, else
#                     /sys/bus/pci and lspci), virtualisation and SLAT, IOMMU where readable,
#                     hypervisors (KVM, Proxmox, VirtualBox, VMware, libvirt), Docker (client
#                     version only) and the NVIDIA container toolkit, Tailscale, Claude Code,
#                     Codex and the GitHub CLI (presence and version only), and the Deep32
#                     ports in use. Then the verdict from install/matrix.json: the best
#                     profile, two runner-ups, how each VM would be sized, what is missing.
#                     Read-only, no root needed, a few seconds, writes nothing.
#   bench quick       About 2 minutes (--seconds N to shorten): processor single-thread and
#                     all-thread scores, memory copy speed, disk sequential and random speed
#                     on one temporary file (python3 if present, else a cruder shell method).
#                     The file is unlinked as soon as it is opened, never takes more than 5%
#                     of the free space, and the disk part is skipped if it would leave less
#                     than 10 GB free. Refuses to start while gaming mode is on, or while a
#                     gaming-mode state file exists that it cannot read. On Windows use
#                     deep32-inventory.ps1 (this script refuses the bench there).
#   No mode calls a language model or changes anything on the machine. The verdict is
#   worked out by a POSIX awk program, so nothing has to be installed.
#   Needs bash (3.2 or later, for macOS), awk, sed, tr, df.
#
# Usage:
#   bash install/deep32-inventory.sh [report] [--json] [--net] [--matrix FILE] [--facts FILE]
#   bash install/deep32-inventory.sh bench quick [--seconds N] [--json] [--guard-file FILE]
#   Options:
#     --json             print one compact JSON object (contract C10) instead of the page
#     --net              report only: check that the sites an install needs answer (HEAD
#                        requests) and whether the GitHub CLI is signed in
#     --seconds N        bench only: total time, 1 to 600 (default 120)
#     --matrix FILE      install matrix (default: matrix.json beside this file)
#     --facts FILE       report only: skip the probes and judge the facts in FILE (a JSON
#                        object with a "facts" member, as this program prints); for tests
#     --guard-file FILE  bench only: one more gaming-mode state file to obey, besides the
#                        one the DEEP32_GUARD_FILE environment variable names, if set
#     --version, --help
#   JSON files may be UTF-8 (with or without a byte-order mark) or UTF-16 with a byte-order
#   mark (as Windows PowerShell 5.1 writes with ">"); their content is expected to be ASCII.
#
# Environment:
#   DEEP32_GUARD_FILE: the gaming-mode state file bench quick obeys. There is no built-in
#     default: when it is unset or empty and no --guard-file is given, no state file is read.
#
# Test hooks (read-only; unset in normal use):
#   DEEP32_INVENTORY_TEST_ROOT, DEEP32_INVENTORY_TEST_UNAME_S, DEEP32_INVENTORY_TEST_UNAME_M:
#     the Linux probes read /proc, /sys, /etc and /dev under that root, and the system type is
#     forced. The bench ignores them.
#   DEEP32_INVENTORY_TEST_GUARD_ONLY=1: bench quick checks the gaming-mode guard and stops
#     there without measuring anything (exit 0 with "guard":"clear", or 3 as usual); only in
#     that case DEEP32_INVENTORY_TEST_DEFAULT_GUARD replaces the DEEP32_GUARD_FILE state file.
#
# Exit codes:
#   0  done
#   1  other error (for example the matrix is missing or unreadable)
#   2  bad arguments
#   3  refused (gaming mode is on or unreadable, or the bench asked for on Windows)

set -u

TOOL_VERSION="0.1.0"
SCHEMA="deep32.inventory.v1"
# Gaming-mode state file the bench obeys: only the one the DEEP32_GUARD_FILE environment variable names
# (none when it is unset or empty); --guard-file adds one more. There is no built-in default path.
DEFAULT_GUARD="${DEEP32_GUARD_FILE:-}"
UA="deep32-inventory/$TOOL_VERSION"
# Test hooks (read-only): probes read /proc, /sys, /etc and /dev under this root, and the system
# type can be forced, so the Linux probes can be exercised on a fixture tree. Unset in normal use.
R="${DEEP32_INVENTORY_TEST_ROOT:-}"
UNAME_S="${DEEP32_INVENTORY_TEST_UNAME_S:-}"; [ -z "$UNAME_S" ] && UNAME_S=$(uname -s 2>/dev/null)
UNAME_M="${DEEP32_INVENTORY_TEST_UNAME_M:-}"; [ -z "$UNAME_M" ] && UNAME_M=$(uname -m 2>/dev/null)
TAB=$(printf '\t')

err() { printf 'deep32-inventory: %s\n' "$*" >&2; }
usage() {
    printf '%s\n' 'usage: deep32-inventory.sh [report] [--json] [--net] [--matrix FILE] [--facts FILE]' \
        '       deep32-inventory.sh bench quick [--seconds N] [--json] [--guard-file FILE]' \
        'exit codes: 0 done, 1 other error, 2 bad arguments, 3 refused'
}

# ------------------------------------------------------------------ arguments
MODE=""; SUB=""; JSON=0; NET=0; BENCH_SECS=120; SECS_GIVEN=0
MATRIX=""; FACTS=""; GUARD="$DEFAULT_GUARD"; GUARD_EXTRA=""
GUARD_ONLY=0
if [ "${DEEP32_INVENTORY_TEST_GUARD_ONLY:-}" = 1 ]; then
    GUARD_ONLY=1
    [ -n "${DEEP32_INVENTORY_TEST_DEFAULT_GUARD:-}" ] && GUARD="$DEEP32_INVENTORY_TEST_DEFAULT_GUARD"
fi
while [ $# -gt 0 ]; do
    a="$1"
    case "$a" in
        --json|-json) JSON=1 ;;
        --net|-net) NET=1 ;;
        --help|-h|-help) usage; exit 0 ;;
        --version|-version) printf 'deep32-inventory %s\n' "$TOOL_VERSION"; exit 0 ;;
        --seconds|--matrix|--facts|--guard-file)
            if [ $# -lt 2 ]; then err "$a needs a value"; usage >&2; exit 2; fi
            v="$2"; shift
            if [ -z "$v" ]; then err "$a needs a value"; usage >&2; exit 2; fi
            case "$a" in
                --seconds)
                    case "$v" in *[!0-9]*) err '--seconds must be a whole number from 1 to 600'; exit 2 ;; esac
                    if [ "${#v}" -gt 9 ]; then err '--seconds must be a whole number from 1 to 600'; exit 2; fi
                    v=$((10#$v))
                    if [ "$v" -lt 1 ] || [ "$v" -gt 600 ]; then err '--seconds must be a whole number from 1 to 600'; exit 2; fi
                    BENCH_SECS="$v"; SECS_GIVEN=1 ;;
                --matrix) MATRIX="$v" ;;
                --facts) FACTS="$v" ;;
                --guard-file) GUARD_EXTRA="$v" ;;
            esac ;;
        -*) err "unknown option $a"; usage >&2; exit 2 ;;
        *)
            if [ -z "$MODE" ]; then MODE="$a"
            elif [ -z "$SUB" ]; then SUB="$a"
            else err "unexpected argument $a"; usage >&2; exit 2; fi ;;
    esac
    shift
done
[ -z "$MODE" ] && MODE="report"
case "$MODE" in
    report)
        if [ -n "$SUB" ]; then err "unexpected argument $SUB"; exit 2; fi
        if [ "$SECS_GIVEN" = 1 ]; then err '--seconds belongs to bench quick'; exit 2; fi ;;
    bench)
        case "$SUB" in
            quick) ;;
            system|stress) err "bench $SUB is not in this version (it comes with an installed system)"; exit 2 ;;
            *) err 'bench needs quick'; usage >&2; exit 2 ;;
        esac
        if [ "$NET" = 1 ]; then err '--net belongs to report'; exit 2; fi
        if [ -n "$FACTS" ]; then err '--facts belongs to report'; exit 2; fi ;;
    *) err "unknown mode $MODE"; usage >&2; exit 2 ;;
esac
SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -z "$MATRIX" ] && MATRIX="$SCRIPT_DIR/matrix.json"

# ------------------------------------------------------------------ small helpers
have() { command -v "$1" >/dev/null 2>&1; }
run_to() {
    # run_to SECONDS command...: with a time limit where the system has one
    _s="$1"; shift
    if have timeout; then timeout "$_s" "$@"; else "$@"; fi
}
ascii() { printf '%s' "$1" | LC_ALL=C tr -cd '\040-\176' | sed 's/  */ /g; s/^ //; s/ $//'; }
lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
first_ver() { sed -n 's/^[^0-9]*\([0-9][0-9]*\.[0-9][0-9]*\(\.[0-9][0-9]*\)\{0,1\}\).*/\1/p' | head -n 1; }
js() {
    # JSON string, or null for an empty value
    if [ -z "$1" ]; then printf 'null'; return; fi
    printf '"%s"' "$(ascii "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}
jn() { case "$1" in ''|*[!0-9.]*|.*|*.) printf 'null' ;; *) printf '%s' "$1" ;; esac; }
jb() { case "$1" in true|false) printf '%s' "$1" ;; *) printf 'null' ;; esac; }
utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
tool_ver() {
    # tool_ver NAME [EXTRA_PATH] [ARG]: JSON false (absent), "x.y.z", or "present"
    _p=""
    if have "$1"; then _p=$(command -v "$1"); elif [ -n "${2:-}" ] && [ -x "$2" ]; then _p="$2"; fi
    if [ -z "$_p" ]; then printf 'false'; return; fi
    _a="${3:---version}"
    _v=$(run_to 15 "$_p" "$_a" 2>&1 </dev/null | sed '/^[[:space:]]*$/d' | head -n 1 | first_ver)
    if [ -n "$_v" ]; then printf '"%s"' "$_v"; else printf '"present"'; fi
}

# ------------------------------------------------------------------ the verdict engine (POSIX awk)
# Input: D32_MATRIX=matrix path in the environment (empty = no matrix), the facts document on standard input.
# Output: line 1 the verdict JSON (or null), line 2 the facts JSON, then the page for a person.
IFS= read -r -d '' VERDICT_AWK <<'AWK'
function jparse(text, root) { JT = text; JP = 1; JL = length(text); JERR = 0; jvalue(root); jws(); if (JP <= JL) JERR = 1 }
function jws(   c) { while (JP <= JL) { c = substr(JT, JP, 1); if (c == " " || c == "\t" || c == "\n" || c == "\r") JP++; else break } }
function jvalue(path,   c) {
    if (JERR) return
    jws(); c = substr(JT, JP, 1)
    if (c == "{") jobject(path)
    else if (c == "[") jarray(path)
    else if (c == "\"") { T[path] = "s"; V[path] = jstring() }
    else if (substr(JT, JP, 4) == "true") { T[path] = "b"; V[path] = "true"; JP += 4 }
    else if (substr(JT, JP, 5) == "false") { T[path] = "b"; V[path] = "false"; JP += 5 }
    else if (substr(JT, JP, 4) == "null") { T[path] = "z"; V[path] = ""; JP += 4 }
    else if (c ~ /[-0-9]/) { T[path] = "n"; V[path] = jnumber() }
    else JERR = 1
}
function jobject(path,   key, n, c) {
    T[path] = "o"; V[path] = ""; JP++; n = 0; jws()
    if (substr(JT, JP, 1) == "}") { JP++; N[path] = 0; return }
    while (!JERR) {
        jws(); if (substr(JT, JP, 1) != "\"") { JERR = 1; break }
        key = jstring(); jws()
        if (substr(JT, JP, 1) != ":") { JERR = 1; break }
        JP++
        K[path, n] = key; n++
        jvalue(path "." key)
        jws(); c = substr(JT, JP, 1); JP++
        if (c == "}") break
        if (c != ",") { JERR = 1; break }
    }
    N[path] = n
}
function jarray(path,   n, c) {
    T[path] = "a"; V[path] = ""; JP++; n = 0; jws()
    if (substr(JT, JP, 1) == "]") { JP++; N[path] = 0; return }
    while (!JERR) {
        jvalue(path "[" n "]"); n++
        jws(); c = substr(JT, JP, 1); JP++
        if (c == "]") break
        if (c != ",") { JERR = 1; break }
    }
    N[path] = n
}
function jstring(   out, c, h) {
    JP++; out = ""
    while (JP <= JL) {
        c = substr(JT, JP, 1)
        if (c == "\"") { JP++; return out }
        if (c == "\\") {
            JP++; c = substr(JT, JP, 1)
            if (c == "n" || c == "r" || c == "t" || c == "b" || c == "f") out = out " "
            else if (c == "u") { h = hexval(substr(JT, JP + 1, 4)); if (h >= 32 && h < 127) out = out sprintf("%c", h); JP += 4 }
            else out = out c
            JP++; continue
        }
        out = out c; JP++
    }
    JERR = 1; return out
}
function jnumber(   s, c) {
    s = ""
    while (JP <= JL) { c = substr(JT, JP, 1); if (c ~ /[-+0-9.eE]/) { s = s c; JP++ } else break }
    return s
}
function hexval(h,   i, c, v) {
    v = 0; h = tolower(h)
    for (i = 1; i <= length(h); i++) { c = index("0123456789abcdef", substr(h, i, 1)); if (c == 0) return -1; v = v * 16 + c - 1 }
    return v
}
function jq(s,   out, i, c) {
    out = ""
    for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "\\") out = out "\\\\"
        else if (c == "\"") out = out "\\\""
        else if (c < " ") out = out " "
        else out = out c
    }
    return "\"" out "\""
}
function jdump(p,   t, i, n, s) {
    t = T[p]
    if (t == "o") { s = "{"; n = N[p]; for (i = 0; i < n; i++) { if (i) s = s ","; s = s jq(K[p, i]) ":" jdump(p "." K[p, i]) }; return s "}" }
    if (t == "a") { s = "["; n = N[p]; for (i = 0; i < n; i++) { if (i) s = s ","; s = s jdump(p "[" i "]") }; return s "]" }
    if (t == "s") return jq(V[p])
    if (t == "n" || t == "b") return V[p]
    return "null"
}
function ftype(name,   p) { p = "d." name; if (p in T) return T[p]; p = FP "." name; if (p in T) return T[p]; return "z" }
function fval(name,   p) { p = "d." name; if (p in T) return V[p]; p = FP "." name; if (p in T) return V[p]; return "" }
function setd(name, t, v) { T["d." name] = t; V["d." name] = v }
function same(at, av, bt, bv) { if (at == "n" && bt == "n") return ((av + 0) == (bv + 0)) ? 1 : 0; return (at == bt && av == bv) ? 1 : 0 }
function etriple(p,   name, op, ft, fv, vt, vv, n, i, hit, a, b) {
    name = V[p "[0]"]; op = V[p "[1]"]
    ft = ftype(name); fv = fval(name)
    if (ft == "o" || ft == "a") ft = "x"
    if (op == "present") return (ft != "z") ? 1 : 0
    if (op == "absent") return (ft == "z") ? 1 : 0
    if (op == "nottrue") return (ft == "b" && fv == "true") ? 0 : 1
    if (op == "notfalse") return (ft == "b" && fv == "false") ? 0 : 1
    if (ft == "z") return -1
    vt = T[p "[2]"]; vv = V[p "[2]"]
    if (op == "eq") return same(ft, fv, vt, vv)
    if (op == "ne") return 1 - same(ft, fv, vt, vv)
    if (op == "in" || op == "nin") {
        hit = 0; n = N[p "[2]"]
        for (i = 0; i < n; i++) if (same(ft, fv, T[p "[2][" i "]"], V[p "[2][" i "]"])) { hit = 1; break }
        return (op == "in") ? hit : 1 - hit
    }
    if (op == "ge" || op == "gt" || op == "le" || op == "lt") {
        if (ft != "n" || vt != "n") return -1
        a = fv + 0; b = vv + 0
        if (op == "ge") return (a >= b) ? 1 : 0
        if (op == "gt") return (a > b) ? 1 : 0
        if (op == "le") return (a <= b) ? 1 : 0
        return (a < b) ? 1 : 0
    }
    if (op == "re") { if (ft != "s") return -1; return (tolower(fv) ~ vv) ? 1 : 0 }
    return -1
}
function eall(p,   n, i, r, u) {
    if (!(p in T) || T[p] != "a") return -1
    n = N[p]; u = 0
    for (i = 0; i < n; i++) { r = econd(p "[" i "]"); if (r == 0) return 0; if (r == -1) u = 1 }
    return u ? -1 : 1
}
function econd(p,   n, i, r, u) {
    if (T[p] == "o") {
        if ((p ".any") in T) {
            n = N[p ".any"]; u = 0
            for (i = 0; i < n; i++) { r = econd(p ".any[" i "]"); if (r == 1) return 1; if (r == -1) u = 1 }
            return u ? -1 : 0
        }
        if ((p ".all") in T) return eall(p ".all")
        return -1
    }
    if (T[p] == "a") { if (N[p] >= 2 && T[p "[0]"] == "s") return etriple(p); return eall(p) }
    return -1
}
function lappend(l, x) { return (l == "") ? x : l SUBSEP x }
function jlist(l,   a, n, i, s) { if (l == "") return "[]"; n = split(l, a, SUBSEP); s = "["; for (i = 1; i <= n; i++) { if (i > 1) s = s ","; s = s jq(a[i]) }; return s "]" }
function plist(l) { gsub(SUBSEP, ", ", l); return l }
function jnull(t, v) { if (t == "z" || v == "") return "null"; if (t == "s") return jq(v); return v }
function jpair(a, b) { if (a == "" || b == "") return "null"; return "[" a "," b "]" }
function alloc1(rp, avail,   mn, mx) { mn = V[rp "[0]"] + 0; mx = V[rp "[1]"] + 0; if (mx <= avail) return mx; if (mn <= avail) return mn; return "" }
function alloc2(wr, fr, avail,   w0, w1, f0, f1) {
    w0 = V[wr "[0]"] + 0; w1 = V[wr "[1]"] + 0; f0 = V[fr "[0]"] + 0; f1 = V[fr "[1]"] + 0
    if (w1 + f1 <= avail) { A2W = w1; A2F = f1; return 1 }
    if (w1 + f0 <= avail) { A2W = w1; A2F = f0; return 1 }
    if (w0 + f1 <= avail) { A2W = w0; A2F = f1; return 1 }
    if (w0 + f0 <= avail) { A2W = w0; A2F = f0; return 1 }
    return 0
}
function gpuclass(nm, e,   nc, j, k, np, cc) {
    nc = N["m.gpu_classes"]
    if (nm != "") for (j = 0; j < nc; j++) { np = N["m.gpu_classes[" j "].names"]; for (k = 0; k < np; k++) if (nm ~ V["m.gpu_classes[" j "].names[" k "]"]) return j }
    if (((e ".cc") in T) && T[e ".cc"] != "z" && T[e ".cc"] != "o" && T[e ".cc"] != "a") {
        cc = V[e ".cc"]
        for (j = 0; j < nc; j++) { np = N["m.gpu_classes[" j "].cc"]; for (k = 0; k < np; k++) if (V["m.gpu_classes[" j "].cc[" k "]"] == cc) return j }
    }
    return -1
}
function derive(   cp, n, i, c, gp, e, nm, cls, score, memv, have, anynv, bi, bcls, bscore, bmem, fg, tp, it, tt, pt, et, th, tiers, t, dp, ntrue, nfalse, nd, fr, pp, bp, k, nb, j, busy, cnt) {
    n = N["m.cpu_classes"]; c = -1
    for (i = 0; i < n; i++) if (eall("m.cpu_classes[" i "].when") == 1) { c = i; break }
    if (c >= 0) { setd("cpu.class", "s", V["m.cpu_classes[" c "].id"]); setd("cpu.ws", "s", V["m.cpu_classes[" c "].workstation"]); setd("cpu.forge", "s", V["m.cpu_classes[" c "].forge"]) }
    else { setd("cpu.class", "z", ""); setd("cpu.ws", "z", ""); setd("cpu.forge", "z", "") }

    gp = FP ".gpus"; have = ((gp in T) && T[gp] == "a"); anynv = 0; bi = -1; bcls = -1; bscore = -1; bmem = -1
    if (have) {
        n = N[gp]
        for (i = 0; i < n; i++) {
            e = gp "[" i "]"
            if (!((e ".vendor") in T) || V[e ".vendor"] != "nvidia") continue
            anynv = 1
            nm = (((e ".name") in T) && T[e ".name"] == "s") ? tolower(V[e ".name"]) : ""
            cls = gpuclass(nm, e)
            fg = (cls >= 0) ? V["m.gpu_classes[" cls "].forge"] : ""
            score = 1
            if (fg == "yes") score = 3; else if (fg == "experimental") score = 2; else if (fg == "no") score = 0
            memv = (((e ".mem_gb") in T) && T[e ".mem_gb"] == "n") ? V[e ".mem_gb"] + 0 : -1
            if (score > bscore || (score == bscore && memv > bmem)) { bi = i; bcls = cls; bscore = score; bmem = memv }
        }
    }
    if (!have) { setd("gpu.nvidia", "z", ""); setd("gpu.usable", "z", "") }
    else if (!anynv) { setd("gpu.nvidia", "b", "false"); setd("gpu.usable", "b", "false") }
    else setd("gpu.nvidia", "b", "true")
    if (bi >= 0) {
        e = gp "[" bi "]"
        if (bcls >= 0) {
            cp = "m.gpu_classes[" bcls "]"
            setd("gpu.class", "s", V[cp ".id"]); setd("gpu.forge", "s", V[cp ".forge"]); setd("gpu.upstream", "s", V[cp ".upstream"])
            setd("gpu.tei", T[cp ".tei"], V[cp ".tei"])
            fg = V[cp ".forge"]
            if (fg == "yes" || fg == "experimental") setd("gpu.usable", "b", "true")
            else if (fg == "no") setd("gpu.usable", "b", "false")
            else setd("gpu.usable", "z", "")
        } else {
            setd("gpu.class", "s", "unknown"); setd("gpu.forge", "z", ""); setd("gpu.upstream", "z", ""); setd("gpu.tei", "z", ""); setd("gpu.usable", "z", "")
        }
        if (((e ".mem_gb") in T) && T[e ".mem_gb"] == "n") {
            setd("gpu.mem_gb", "n", V[e ".mem_gb"])
            setd("gpu.mem_tier", "z", "")
            n = N["m.gpu_mem_tiers"]
            for (i = 0; i < n; i++) if (V[e ".mem_gb"] + 0 >= V["m.gpu_mem_tiers[" i "].min_gb"] + 0) { setd("gpu.mem_tier", "s", V["m.gpu_mem_tiers[" i "].id"]); break }
        } else { setd("gpu.mem_gb", "z", ""); setd("gpu.mem_tier", "z", "") }
        if (((e ".driver") in T) && T[e ".driver"] == "s") setd("gpu.driver", "s", V[e ".driver"]); else setd("gpu.driver", "z", "")
    } else {
        setd("gpu.class", "z", ""); setd("gpu.forge", "z", ""); setd("gpu.upstream", "z", ""); setd("gpu.tei", "z", "")
        setd("gpu.mem_gb", "z", ""); setd("gpu.mem_tier", "z", ""); setd("gpu.driver", "z", "")
    }

    it = ftype("mem.installed_gb"); tt = ftype("mem.total_gb"); BASIS = ""
    if (it == "n" && fval("mem.installed_gb") + 0 > 0) BASIS = int(fval("mem.installed_gb") + 0.5)
    else if (tt == "n") { t = (fval("mem.total_gb") + 0) / 2; BASIS = (int(t) + ((t > int(t)) ? 1 : 0)) * 2 }
    setd("mem.basis_gb", (BASIS == "") ? "z" : "n", BASIS)
    STH = ""
    if (ftype("cpu.e_cores") == "n" && fval("cpu.e_cores") + 0 > 0 && ftype("cpu.p_threads") == "n") STH = int(fval("cpu.p_threads") + 0)
    else if (ftype("cpu.threads") == "n") STH = int(fval("cpu.threads") + 0)
    setd("size.threads", (STH == "") ? "z" : "n", STH)
    TIER = ""; TIDX = -1
    if (STH != "" && BASIS != "") {
        n = N["m.sizing.tiers"]
        for (i = 0; i < n; i++) {
            tp = "m.sizing.tiers[" i "]"
            if (STH >= V[tp ".min_threads"] + 0 && BASIS >= V[tp ".min_mem_gb"] + 0) { TIER = V[tp ".tier"] + 0; TIDX = i }
        }
        AT = STH - V["m.sizing.host_keeps.threads"]; AM = BASIS - V["m.sizing.host_keeps.mem_gb"]
        setd("size.tier", "n", TIER)
        setd("size.both", "b", (TIER >= 1) ? "true" : "false")
        setd("size.ws_fits", "b", (AT >= V["m.sizing.ws_alone_min.threads"] + 0 && AM >= V["m.sizing.ws_alone_min.mem_gb"] + 0) ? "true" : "false")
        setd("size.forge_fits", "b", (AT >= V["m.sizing.forge_alone_min.threads"] + 0 && AM >= V["m.sizing.forge_alone_min.mem_gb"] + 0) ? "true" : "false")
    } else { setd("size.tier", "z", ""); setd("size.both", "z", ""); setd("size.ws_fits", "z", ""); setd("size.forge_fits", "z", "") }

    dp = FP ".disks"; fr = ""; ntrue = 0; nfalse = 0; nd = 0
    if ((dp in T) && T[dp] == "a") {
        n = N[dp]
        for (i = 0; i < n; i++) {
            e = dp "[" i "]"; nd++
            if (((e ".free_gb") in T) && T[e ".free_gb"] == "n" && (fr == "" || V[e ".free_gb"] + 0 > fr + 0)) fr = V[e ".free_gb"]
            if (((e ".ssd") in T) && T[e ".ssd"] == "b") { if (V[e ".ssd"] == "true") ntrue++; else nfalse++ }
        }
    }
    setd("disk.free_gb", (fr == "") ? "z" : "n", fr)
    if (ntrue > 0) setd("disk.ssd", "b", "true"); else if (nd > 0 && nfalse == nd) setd("disk.ssd", "b", "false"); else setd("disk.ssd", "z", "")

    pp = FP ".ports"; bp = pp ".busy"
    if (ftype("ports.checked") == "b" && fval("ports.checked") == "true") {
        cnt = 0; n = N["m.ports"]
        for (i = 0; i < n; i++) {
            k = V["m.ports[" i "].port"] + 0; busy = 0
            if ((bp in T) && T[bp] == "a") { nb = N[bp]; for (j = 0; j < nb; j++) if (T[bp "[" j "]"] == "n" && V[bp "[" j "]"] + 0 == k) busy = 1 }
            setd("port." k, "b", busy ? "true" : "false"); if (busy) cnt++
        }
        setd("ports.busy_count", "n", cnt)
    } else {
        n = N["m.ports"]
        for (i = 0; i < n; i++) setd("port." (V["m.ports[" i "].port"] + 0), "z", "")
        setd("ports.busy_count", "z", "")
    }

    setd("hw.profile", "z", "")
    n = N["m.hw_profiles"]
    for (i = 0; i < n; i++) if (eall("m.hw_profiles[" i "].when") == 1) { setd("hw.profile", "s", V["m.hw_profiles[" i "].id"]); break }
}
function profiles(   np, i, pp, nq, j, rid, rp, r, kind, miss, unk, aut, cnf, hard, nmiss, fit, ford, sk, ws, fo, tp, a, b, ftr, fmr, x, y, z, tmp, s) {
    NE = 0; NR = 0
    np = N["m.profiles"]
    for (i = 0; i < np; i++) {
        pp = "m.profiles[" i "]"
        if (eall(pp ".match") != 1) continue
        miss = ""; unk = ""; aut = ""; cnf = ""; hard = ""; nmiss = 0
        nq = N[pp ".requires"]
        for (j = 0; j < nq; j++) {
            rid = V[pp ".requires[" j "]"]; rp = "m.requirements." rid
            r = eall(rp ".when"); kind = V[rp ".kind"]
            if (r == 1) continue
            if (kind == "auto") { aut = lappend(aut, rid); continue }
            if (r == 0) { miss = lappend(miss, rid); nmiss++; if (kind == "hardware") hard = lappend(hard, rid) }
            else if (kind == "confirm") cnf = lappend(cnf, rid)
            else unk = lappend(unk, rid)
        }
        if (hard != "") { RO_ID[NR] = V[pp ".id"]; RO_WHY[NR] = hard; NR++; continue }
        # fit, and its place in the order: ready 0, unconfirmed 1, changes 2 (fit comes before rank)
        fit = "ready"; ford = 0
        if (miss != "") { fit = "changes"; ford = 2 } else if (unk != "") { fit = "unconfirmed"; ford = 1 }
        ws = "null"; fo = "null"; sk = V[pp ".size"]
        if (TIER != "") {
            tp = "m.sizing.tiers[" TIDX "]"
            if (sk == "both" && TIER >= 1) {
                if (alloc2(tp ".ws.threads", tp ".forge.threads", AT)) { x = A2W; y = A2F
                    if (alloc2(tp ".ws.mem_gb", tp ".forge.mem_gb", AM)) { ws = jpair(x, A2W); fo = jpair(y, A2F) } }
            }
            if (sk == "ws" || sk == "each") { a = alloc1(tp ".ws.threads", AT); b = alloc1(tp ".ws.mem_gb", AM); ws = jpair(a, b) }
            if (sk == "forge" || sk == "each") {
                if (T[tp ".forge"] == "o") { a = alloc1(tp ".forge.threads", AT); b = alloc1(tp ".forge.mem_gb", AM) }
                else {
                    z = V["m.sizing.forge_alone_min.threads"] + 0; a = (z <= AT) ? z : ""
                    z = V["m.sizing.forge_alone_min.mem_gb"] + 0; b = (z <= AM) ? z : ""
                }
                fo = jpair(a, b)
            }
        }
        E_ID[NE] = V[pp ".id"]; E_RANK[NE] = V[pp ".rank"] + 0; E_NM[NE] = nmiss; E_IDX[NE] = i; E_FORD[NE] = ford
        E_FIT[NE] = fit; E_MISS[NE] = miss; E_UNK[NE] = unk; E_AUTO[NE] = aut; E_CNF[NE] = cnf; E_WS[NE] = ws; E_FO[NE] = fo; E_P[NE] = pp
        # machines (single machine first): only a JSON number of 2 or more under "machines"
        # makes the profile an alternative that needs another machine; anything else is one machine
        E_ALT[NE] = (((pp ".machines") in T) && T[pp ".machines"] == "n" && V[pp ".machines"] + 0 >= 2) ? 1 : 0
        NE++
    }
    # one-machine profiles before the alternatives (single machine first), then fit, then the rank of the profile,
    # then fewer missing, then matrix order (matrix "ranking")
    for (i = 0; i < NE; i++) ORD[i] = i
    for (i = 0; i < NE; i++) for (j = i + 1; j < NE; j++) {
        a = ORD[i]; b = ORD[j]
        if (E_ALT[b] < E_ALT[a] || (E_ALT[b] == E_ALT[a] && (E_FORD[b] < E_FORD[a] || (E_FORD[b] == E_FORD[a] && (E_RANK[b] < E_RANK[a] || (E_RANK[b] == E_RANK[a] && (E_NM[b] < E_NM[a] || (E_NM[b] == E_NM[a] && E_IDX[b] < E_IDX[a])))))))) { ORD[i] = b; ORD[j] = a }
    }
    NK = (NE < 3) ? NE : 3
}
function verdict_json(   s, i, e, n, w, cl) {
    s = "{\"matrix\":" jq(V["m.matrix_version"])
    if (NK > 0) { e = ORD[0]; s = s ",\"best\":" jq(E_ID[e]) ",\"fit\":" jq(E_FIT[e]) } else s = s ",\"best\":null,\"fit\":\"none\""
    s = s ",\"cpu\":{\"class\":" jnull(ftype("cpu.class"), fval("cpu.class")) ",\"ws\":" jnull(ftype("cpu.ws"), fval("cpu.ws")) ",\"forge\":" jnull(ftype("cpu.forge"), fval("cpu.forge")) "}"
    s = s ",\"gpu\":{\"class\":" jnull(ftype("gpu.class"), fval("gpu.class")) ",\"forge\":" jnull(ftype("gpu.forge"), fval("gpu.forge")) ",\"tei\":" jnull(ftype("gpu.tei"), fval("gpu.tei")) ",\"upstream\":" jnull(ftype("gpu.upstream"), fval("gpu.upstream")) ",\"mem_gb\":" jnull(ftype("gpu.mem_gb"), fval("gpu.mem_gb")) ",\"mem_tier\":" jnull(ftype("gpu.mem_tier"), fval("gpu.mem_tier")) "}"
    s = s ",\"size\":{\"threads\":" ((STH == "") ? "null" : STH) ",\"mem_gb\":" ((BASIS == "") ? "null" : BASIS) ",\"tier\":" ((TIER == "") ? "null" : TIER) "}"
    s = s ",\"ranked\":["
    for (i = 0; i < NK; i++) {
        e = ORD[i]; if (i) s = s ","
        s = s "{\"p\":" jq(E_ID[e]) ",\"target\":" jq(V[E_P[e] ".target"]) ",\"status\":" jq(V[E_P[e] ".status"]) ",\"machines\":" (E_ALT[e] ? 2 : 1) ",\"fit\":" jq(E_FIT[e])
        s = s ",\"missing\":" jlist(E_MISS[e]) ",\"unknown\":" jlist(E_UNK[e]) ",\"auto\":" jlist(E_AUTO[e]) ",\"confirm\":" jlist(E_CNF[e])
        s = s ",\"size\":{\"ws\":" E_WS[e] ",\"forge\":" E_FO[e] "}}"
    }
    s = s "],\"ruled_out\":["
    for (i = 0; i < NR; i++) { if (i) s = s ","; s = s "{\"p\":" jq(RO_ID[i]) ",\"why\":" jlist(RO_WHY[i]) "}" }
    s = s "],\"warnings\":["
    n = N["m.warnings"]; w = 0
    for (i = 0; i < n; i++) if (eall("m.warnings[" i "].when") == 1) { if (w) s = s ","; s = s jq(V["m.warnings[" i "].id"]); w++ }
    return s "]}"
}
function show(name,   t, v) { t = ftype(name); v = fval(name); if (t == "z") return "unavailable"; if (t == "b") return (v == "true") ? "yes" : "no"; if (t == "o" || t == "a") return "?"; return v }
function showtool(name,   t, v) { t = ftype(name); v = fval(name); if (t == "z") return "unavailable"; if (t == "b" && v == "false") return "not found"; return v }
function reqtext(id,   rp, s) { rp = "m.requirements." id; if (!((rp ".need") in T)) return id; s = V[rp ".need"]; if ((rp ".do") in T) s = s ": " V[rp ".do"]; return s }
function page(havem,   s, n, i, e, line, first, k, j, p, nb, q, use, a, cnt) {
    print "Deep32 inventory " VERSION " (report)"
    print "System     " show("os.name") ", version " show("os.version") ", build " show("os.build") ", " show("os.arch")
    s = show("hw.virtual"); if (s == "unavailable") s = "unknown"
    print "Machine    " show("hw.vendor") " " show("hw.model") "; itself a VM: " s
    print "Processor  " show("cpu.model") " (" show("cpu.vendor") ", family " show("cpu.family") " model " show("cpu.model_id") " stepping " show("cpu.stepping") ")"
    line = "           " show("cpu.cores") " cores, " show("cpu.threads") " threads"
    if (ftype("cpu.p_cores") == "n") line = line " (" fval("cpu.p_cores") " fast cores, " fval("cpu.p_threads") " fast threads, " fval("cpu.e_cores") " efficient cores)"
    line = line "; AVX " show("cpu.avx") ", AVX2 " show("cpu.avx2") ", AVX-512F " show("cpu.avx512f")
    if (ftype("cpu.gen") == "s") line = line "; " fval("cpu.gen")
    print line
    print "Memory     " show("mem.installed_gb") " GB installed, " show("mem.total_gb") " GB visible, " show("mem.free_gb") " GB free"
    p = FP ".disks"
    if (!(p in T) || T[p] != "a") print "Disks      unavailable"
    else {
        n = N[p]; line = ""
        for (i = 0; i < n; i++) {
            e = p "[" i "]"; if (i) line = line "; "
            s = ""; if (((e ".ssd") in T) && T[e ".ssd"] == "b") s = (V[e ".ssd"] == "true") ? ", solid-state" : ", spinning"
            line = line V[e ".mount"] " " V[e ".size_gb"] " GB, " V[e ".free_gb"] " GB free" s
        }
        if (n == 0) line = "none found"
        print "Disks      " line
    }
    p = FP ".gpus"
    if (!(p in T) || T[p] != "a") print "Graphics   unavailable"
    else {
        n = N[p]
        if (n == 0) print "Graphics   none found"
        for (i = 0; i < n; i++) {
            e = p "[" i "]"
            line = ((i == 0) ? "Graphics   " : "           ") gshow(e ".name") " (" gshow(e ".vendor") "), " gshow(e ".mem_gb") " GB, driver " gshow(e ".driver")
            if (((e ".cc") in T) && T[e ".cc"] == "s") line = line ", compute " V[e ".cc"]
            print line
        }
    }
    print "Virtual    in firmware " show("virt.firmware") ", SLAT " show("virt.slat") ", IOMMU " show("virt.iommu") ", hypervisor running " show("virt.hypervisor")
    s = "unavailable"; if (ftype("wsl") == "o") s = show("wsl.present") " (" show("wsl.wsl2_distros") " WSL2 distributions)"
    print "Hypervisor Hyper-V " show("hyp.hyperv") ", KVM " show("hyp.kvm") ", Proxmox " show("hyp.proxmox") ", VirtualBox " show("hyp.virtualbox") ", VMware " show("hyp.vmware") "; WSL2 " s
    print "Docker     " show("docker.present") ", client " show("docker.client") ", NVIDIA runtime " show("docker.nvidia")
    print "Tools      Tailscale " showtool("tools.tailscale") "; Claude Code " showtool("tools.claude") "; Codex " showtool("tools.codex") "; GitHub CLI " showtool("tools.gh")
    if (!(ftype("ports.checked") == "b" && fval("ports.checked") == "true")) print "Ports      unavailable"
    else {
        p = FP ".ports.busy"; nb = ((p in T) && T[p] == "a") ? N[p] : 0
        if (nb == 0) print "Ports      8765, 8766, 8443, 8081, 8082, 7452 all free"
        else {
            line = ""
            for (j = 0; j < nb; j++) {
                q = V[p "[" j "]"]; use = ""
                if (havem) for (k = 0; k < N["m.ports"]; k++) if (V["m.ports[" k "].port"] + 0 == q + 0) use = " (" V["m.ports[" k "].use"] ")"
                line = line ((j) ? ", " : "") q use
            }
            print "Ports      in use: " line
        }
    }
    p = FP ".net"
    if ((p in T) && T[p] == "o") {
        line = ""; cnt = 0
        for (i = 0; i < N[p]; i++) {
            k = K[p, i]; if (k == "gh_signed_in") continue
            line = line ((cnt++) ? "; " : "") k " " ((V[p "." k ".ok"] == "true") ? "answers in " V[p "." k ".ms"] " ms" : "no answer")
        }
        print "Network    " line "; GitHub CLI signed in " show("net.gh_signed_in")
    }
    print ""
    if (!havem) { print "Verdict    unavailable (no matrix)"; return }
    s = "unknown"; if (ftype("cpu.class") == "s") s = fval("cpu.class") " (Workstation " fval("cpu.ws") ", Forge " fval("cpu.forge") ")"
    line = "no NVIDIA card found, or none readable"
    if (ftype("gpu.class") == "s") line = fval("gpu.class") " (Forge " show("gpu.forge") ", embeddings image " show("gpu.tei") ", upstream " show("gpu.upstream") ", memory tier " show("gpu.mem_tier") ")"
    print "Classes    processor " s "; card " line
    print "Sizing     " ((STH == "") ? "unavailable" : STH) " threads and " ((BASIS == "") ? "unavailable" : BASIS) " GB count; tier " ((TIER == "") ? "unavailable" : TIER)
    if (NK == 0) { print "Verdict    no profile fits this machine"; return }
    for (i = 0; i < NK; i++) {
        e = ORD[i]; p = E_P[e]
        print ((i == 0) ? "Verdict    " : "Next       ") E_ID[e] ": " V[p ".name"] " (target " V[p ".target"] ", " V[p ".status"] "); fit: " E_FIT[e] (E_ALT[e] ? "; needs another machine" : "")
        line = ""
        if (E_WS[e] != "null") { s = E_WS[e]; gsub(/[][]/, "", s); split(s, a, ","); line = "Workstation " a[1] " threads, " a[2] " GB" }
        if (E_FO[e] != "null") { s = E_FO[e]; gsub(/[][]/, "", s); split(s, a, ","); line = line ((line == "") ? "" : "; ") "Forge " a[1] " threads, " a[2] " GB" }
        if (line != "") print "             size: " line
        if (i == 0) {
            print "             parts: Workstation " V[p ".parts.workstation"] "; Forge " V[p ".parts.forge"] "; relay " V[p ".parts.relay"]
            if (E_MISS[e] != "") { n = split(E_MISS[e], a, SUBSEP); for (j = 1; j <= n; j++) print "             missing: " reqtext(a[j]) }
            if (E_UNK[e] != "") { n = split(E_UNK[e], a, SUBSEP); for (j = 1; j <= n; j++) print "             not checked: " reqtext(a[j]) }
            if (E_AUTO[e] != "") { n = split(E_AUTO[e], a, SUBSEP); for (j = 1; j <= n; j++) print "             installer: " reqtext(a[j]) }
            if (E_CNF[e] != "") { n = split(E_CNF[e], a, SUBSEP); for (j = 1; j <= n; j++) print "             to confirm: " reqtext(a[j]) }
        } else if (E_MISS[e] != "") print "             missing: " plist(E_MISS[e])
    }
    for (i = 0; i < NR; i++) print "Ruled out  " RO_ID[i] ": " plist(RO_WHY[i])
    n = N["m.warnings"]
    for (i = 0; i < n; i++) if (eall("m.warnings[" i "].when") == 1) print "Note       " V["m.warnings[" i "].text"]
}
function gshow(p) { if (!(p in T) || T[p] == "z") return "unavailable"; return V[p] }
function nobom(s) {
    if (length(s) < 2) return s
    if (substr(s, 1, 3) == "\357\273\277") return substr(s, 4)
    if (substr(s, 1, 2) == "\377\376" || substr(s, 1, 2) == "\376\377") return substr(s, 3)
    return s
}
BEGIN { FT = ""; MT = "" }
{ FT = FT $0 "\n" }
END {
    FT = nobom(FT)
    if (("D32_GUARD_ONLY" in ENVIRON) && ENVIRON["D32_GUARD_ONLY"] == "1") {
        if (FT != "") jparse(FT, "g")
        if (FT == "" || JERR || T["g"] != "o" || !(("g.mode") in T) || T["g.mode"] != "s") print "unreadable"
        else print "mode=" tolower(V["g.mode"])
        exit 0
    }
    HAVEM = 0; MFILE = ENVIRON["D32_MATRIX"]
    if (MFILE != "") {
        while ((getline MLINE < MFILE) > 0) MT = MT MLINE "\n"
        close(MFILE)
        jparse(nobom(MT), "m")
        if (!JERR && T["m"] == "o" && V["m.schema"] == "deep32.matrix.v1") HAVEM = 1
    }
    jparse(FT, "x")
    if (JERR || T["x"] != "o" || !(("x.facts") in T) || T["x.facts"] != "o") { print "ERROR facts"; exit 1 }
    FP = "x.facts"
    if (HAVEM) { derive(); profiles(); print verdict_json() } else print "null"
    print jdump(FP)
    page(HAVEM)
}
AWK

run_verdict() {
    # run_verdict MATRIX_OR_EMPTY < facts document
    D32_MATRIX="$1" LC_ALL=C awk -v VERSION="$TOOL_VERSION" "$VERDICT_AWK"
}

# ------------------------------------------------------------------ probes
OS_FAMILY=""; OS_NAME=""; OS_EDITION=""; OS_VERSION=""; OS_BUILD=""; OS_ARCH=""; OS_WSL=""; OS_KERNEL=""
HW_VENDOR=""; HW_MODEL=""; HW_VIRTUAL=""
CPU_VENDOR=""; CPU_MODEL=""; CPU_FAMILY=""; CPU_MODEL_ID=""; CPU_STEPPING=""; CPU_GEN=""; CPU_SOCKETS=""
CPU_CORES=""; CPU_THREADS=""; CPU_PCORES=""; CPU_ECORES=""; CPU_PTHREADS=""; CPU_AVX=""; CPU_AVX2=""; CPU_AVX512=""
CPU_FLAGS=""; CPU_VMXFLAGS=""
MEM_TOTAL=""; MEM_INSTALLED=""; MEM_FREE=""
DISKS_JSON="null"; GPUS_JSON="null"
VIRT_FW=""; VIRT_SLAT=""; VIRT_IOMMU=""; VIRT_HV=""
HYP_HYPERV=""; HYP_KVM=""; HYP_PROXMOX=""; HYP_VBOX=""; HYP_VMWARE=""; HYP_LIBVIRT=""
WSL_JSON="null"
DOCKER_PRESENT=""; DOCKER_CLIENT=""; DOCKER_NVIDIA=""
T_TAILSCALE="null"; T_CLAUDE="null"; T_CODEX="null"; T_GH="null"
PORTS_CHECKED="false"; PORTS_BUSY=""
NET_JSON="null"
NOTES=""

NOTES_TXT=""
add_note() {
    if [ -z "$NOTES" ]; then NOTES=$(js "$1"); else NOTES="$NOTES,$(js "$1")"; fi
    NOTES_TXT="${NOTES_TXT}Note       $1
"
}

cpu_gen() {
    printf '%s\n' "$1" | LC_ALL=C awk '{
        s = $0
        if (match(s, /Core\(TM\) Ultra [0-9]+ [0-9][0-9][0-9]/)) { t = substr(s, RSTART, RLENGTH); n = split(t, a, " "); print "Intel Core Ultra series " substr(a[n], 1, 1); exit }
        if (match(s, /Core\(TM\) i[3579]-[0-9][0-9][0-9]+/)) {
            t = substr(s, RSTART, RLENGTH); sub(/.*-/, "", t); d = t
            if (length(d) >= 5) g = substr(d, 1, 2); else if (length(d) == 4 && substr(d, 1, 1) == "1") g = substr(d, 1, 2); else if (length(d) == 4) g = substr(d, 1, 1); else g = 1
            print "Intel Core generation " (g + 0); exit
        }
        if (match(s, /E[357]-[0-9][0-9][0-9][0-9][A-Z]* v[0-9]/)) { t = substr(s, RSTART, RLENGTH); print "Intel Xeon E" substr(t, 2, 1) " v" substr(t, length(t), 1); exit }
        if (match(s, /E[357]-[0-9][0-9][0-9][0-9]/)) { t = substr(s, RSTART, RLENGTH); print "Intel Xeon E" substr(t, 2, 1) " v1"; exit }
        if (match(s, /Ryzen [0-9]+ [0-9][0-9][0-9][0-9]/)) { t = substr(s, RSTART, RLENGTH); n = split(t, a, " "); print "AMD Ryzen " substr(a[n], 1, 1) "000 series"; exit }
    }'
}

count_cpulist() {
    # "0-7,16-23" -> 16
    printf '%s\n' "$1" | awk -F, '{ n = 0; for (i = 1; i <= NF; i++) { if (split($i, r, "-") == 2) n += r[2] - r[1] + 1; else if ($i != "") n++ } print n }'
}

probe_linux_cpu() {
    [ -r "$R"/proc/cpuinfo ] || return 0
    _out=$(LC_ALL=C awk '
        function val(s) { sub(/^[^:]*:[ \t]*/, "", s); return s }
        /^processor[ \t]*:/ { n++ }
        /^vendor_id[ \t]*:/ && !sv { sv = 1; v = val($0) }
        /^model name[ \t]*:/ && !smn { smn = 1; mn = val($0) }
        /^Model[ \t]*:/ && !smn2 { smn2 = 1; mn2 = val($0) }
        /^cpu family[ \t]*:/ && !scf { scf = 1; cf = val($0) }
        /^model[ \t]*:/ && !smi { smi = 1; mi = val($0) }
        /^stepping[ \t]*:/ && !sst { sst = 1; st = val($0) }
        /^flags[ \t]*:/ && !sfl { sfl = 1; fl = val($0) }
        /^vmx flags[ \t]*:/ && !svf { svf = 1; vf = val($0) }
        /^physical id[ \t]*:/ { pid = val($0); if (!(pid in P)) { P[pid] = 1; np++ } }
        /^core id[ \t]*:/ { k = pid ":" val($0); if (!(k in C)) { C[k] = 1; nc++ } }
        END {
            if (mn == "") mn = mn2
            print "THREADS=" n; print "VENDOR=" v; print "MODEL=" mn; print "FAMILY=" cf; print "MODELID=" mi
            print "STEPPING=" st; print "FLAGS=" fl; print "VMXFLAGS=" vf; print "SOCKETS=" np; print "CORES=" nc
        }' "$R"/proc/cpuinfo 2>/dev/null)
    while IFS= read -r _l; do
        _k=${_l%%=*}; _v=${_l#*=}
        case "$_k" in
            THREADS) CPU_THREADS="$_v" ;; VENDOR) _vend="$_v" ;; MODEL) CPU_MODEL=$(ascii "$_v") ;;
            FAMILY) CPU_FAMILY="$_v" ;; MODELID) CPU_MODEL_ID="$_v" ;; STEPPING) CPU_STEPPING="$_v" ;;
            FLAGS) CPU_FLAGS=" $_v " ;; VMXFLAGS) CPU_VMXFLAGS=" $_v " ;; SOCKETS) CPU_SOCKETS="$_v" ;; CORES) CPU_CORES="$_v" ;;
        esac
    done <<EOF
$_out
EOF
    case "${_vend:-}" in GenuineIntel) CPU_VENDOR=intel ;; AuthenticAMD) CPU_VENDOR=amd ;; '') ;; *) CPU_VENDOR=$(lc "$(ascii "$_vend")") ;; esac
    [ "$CPU_SOCKETS" = 0 ] && CPU_SOCKETS=""
    [ "$CPU_CORES" = 0 ] && CPU_CORES=""
    [ "$CPU_THREADS" = 0 ] && CPU_THREADS=""
    if [ -n "$CPU_FLAGS" ]; then
        CPU_AVX=false; CPU_AVX2=false; CPU_AVX512=false
        case "$CPU_FLAGS" in *" avx "*) CPU_AVX=true ;; esac
        case "$CPU_FLAGS" in *" avx2 "*) CPU_AVX2=true ;; esac
        case "$CPU_FLAGS" in *" avx512f "*) CPU_AVX512=true ;; esac
    fi
    CPU_GEN=$(cpu_gen "$CPU_MODEL")
}

probe_linux() {
    OS_FAMILY=linux
    if [ -r "$R"/etc/os-release ]; then
        OS_NAME=$(sed -n 's/^PRETTY_NAME=//p' "$R"/etc/os-release | head -n 1 | tr -d '"')
        OS_EDITION=$(sed -n 's/^ID=//p' "$R"/etc/os-release | head -n 1 | tr -d '"')
        OS_VERSION=$(sed -n 's/^VERSION_ID=//p' "$R"/etc/os-release | head -n 1 | tr -d '"')
    fi
    OS_KERNEL=$(uname -r 2>/dev/null)
    OS_WSL=false
    if grep -qi microsoft "$R"/proc/sys/kernel/osrelease 2>/dev/null; then OS_WSL=true; fi
    probe_linux_cpu
    if [ -d "$R"/sys/devices/cpu_core ] && [ -r "$R"/sys/devices/cpu_core/cpus ]; then
        _pt=$(count_cpulist "$(cat "$R"/sys/devices/cpu_core/cpus 2>/dev/null)")
        _et=0
        [ -r "$R"/sys/devices/cpu_atom/cpus ] && _et=$(count_cpulist "$(cat "$R"/sys/devices/cpu_atom/cpus 2>/dev/null)")
        if [ "${_et:-0}" -gt 0 ]; then
            CPU_PTHREADS="$_pt"; CPU_ECORES="$_et"
            [ -n "$CPU_CORES" ] && CPU_PCORES=$((CPU_CORES - _et))
        else CPU_ECORES=0; fi
    elif [ -n "$CPU_FLAGS" ]; then CPU_ECORES=0; fi

    if [ -r "$R"/proc/meminfo ]; then
        MEM_TOTAL=$(awk '/^MemTotal:/ { printf "%.1f", $2 / 1048576 }' "$R"/proc/meminfo)
        MEM_FREE=$(awk '/^MemAvailable:/ { printf "%.1f", $2 / 1048576 }' "$R"/proc/meminfo)
    fi

    [ -r "$R"/sys/class/dmi/id/sys_vendor ] && HW_VENDOR=$(ascii "$(cat "$R"/sys/class/dmi/id/sys_vendor 2>/dev/null)")
    [ -r "$R"/sys/class/dmi/id/product_name ] && HW_MODEL=$(ascii "$(cat "$R"/sys/class/dmi/id/product_name 2>/dev/null)")
    HW_VIRTUAL=false
    case "$CPU_FLAGS" in *" hypervisor "*) HW_VIRTUAL=true ;; esac
    case "$(lc "$HW_VENDOR $HW_MODEL")" in *"virtual machine"*|*vmware*|*virtualbox*|*kvm*|*qemu*|*xen*|*bochs*|*parallels*|*"google compute"*) HW_VIRTUAL=true ;; esac
    [ -z "$CPU_FLAGS" ] && [ "$HW_VIRTUAL" = false ] && HW_VIRTUAL=""

    # virtualisation
    _hw=0
    case "$CPU_FLAGS" in *" vmx "*|*" svm "*) _hw=1 ;; esac
    VIRT_HV=$HW_VIRTUAL
    case "$CPU_FLAGS$CPU_VMXFLAGS" in
        *" ept "*|*" npt "*) VIRT_SLAT=true ;;
        *) [ "$_hw" = 1 ] && VIRT_SLAT=false ;;
    esac
    if [ -e "$R/dev/kvm" ]; then VIRT_FW=true
    elif [ "$HW_VIRTUAL" = true ] || [ "$_hw" = 1 ]; then VIRT_FW=""
    elif [ -n "$CPU_FLAGS" ]; then VIRT_FW=false; fi
    if [ -d "$R"/sys/class/iommu ]; then
        if [ -n "$(ls -A "$R"/sys/class/iommu 2>/dev/null)" ]; then VIRT_IOMMU=true; else VIRT_IOMMU=false; fi
    fi

    # hypervisors
    HYP_HYPERV=false
    if [ -e "$R/dev/kvm" ]; then HYP_KVM=true; else HYP_KVM=false; fi
    HYP_PROXMOX=false
    if have pveversion; then
        HYP_PROXMOX=true
        _pv=$(run_to 10 pveversion 2>/dev/null | head -n 1 | first_ver)
        [ -n "$_pv" ] && OS_NAME="$OS_NAME (Proxmox VE $_pv)"
    fi
    if have VBoxManage || [ -d "$R"/usr/lib/virtualbox ]; then HYP_VBOX=true; else HYP_VBOX=false; fi
    if have vmware || have vmplayer || [ -d "$R"/usr/lib/vmware ]; then HYP_VMWARE=true; else HYP_VMWARE=false; fi
    if have virsh; then HYP_LIBVIRT=true; else HYP_LIBVIRT=false; fi

    probe_linux_disks
    probe_linux_gpus
    probe_docker
    if [ -r "$R"/proc/net/tcp ]; then
        PORTS_BUSY=$(cat "$R"/proc/net/tcp "$R"/proc/net/tcp6 2>/dev/null | LC_ALL=C awk '
            function hex(h,   i, v) { v = 0; h = tolower(h); for (i = 1; i <= length(h); i++) v = v * 16 + index("0123456789abcdef", substr(h, i, 1)) - 1; return v }
            $4 == "0A" { n = split($2, a, ":"); p = hex(a[n]); if (p == 8765 || p == 8766 || p == 8443 || p == 8081 || p == 8082 || p == 7452) if (!(p in S)) { S[p] = 1; o = o (o == "" ? "" : ",") p } }
            END { print o }')
        PORTS_CHECKED=true
    fi
}

sanitize_mount() {
    case "$1" in
        /home/*) printf '/home/...' ;; /media/*) printf '/media/...' ;; /run/media/*) printf '/run/media/...' ;;
        /Users/*) printf '/Users/...' ;; /Volumes/*) printf '/Volumes/...' ;; *) printf '%s' "$1" ;;
    esac
}

probe_linux_disks() {
    have df || return 0
    _types=""
    [ -r "$R"/proc/mounts ] && _types=$(awk '{ m = $2; gsub(/\\040/, " ", m); print m "\t" $3 }' "$R"/proc/mounts 2>/dev/null)
    _rows=$(df -P -k 2>/dev/null | awk 'NR > 1 { m = $6; for (i = 7; i <= NF; i++) m = m " " $i; print $1 "\t" $2 "\t" $4 "\t" m }')
    _list=""; _seen=" "; _n=0
    while IFS="$TAB" read -r _fs _size _avail _mnt; do
        [ -z "$_fs" ] && continue
        _ty=$(printf '%s\n' "$_types" | awk -F'\t' -v m="$_mnt" '$1 == m { t = $2 } END { print t }')
        case "$_ty" in
            ext2|ext3|ext4|xfs|btrfs|zfs|f2fs|jfs|reiserfs|ntfs|ntfs3|fuseblk|exfat|bcachefs) ;;
            *) continue ;;
        esac
        case "$_fs" in /dev/loop*) continue ;; esac
        _key="$_fs"
        [ "$_ty" = zfs ] && _key="zfs:${_fs%%/*}"
        case "$_seen" in *" $_key "*) continue ;; esac
        _seen="$_seen$_key "
        _ssd=""
        case "$_fs" in
            /dev/*)
                _dev=$(readlink -f "$R$_fs" 2>/dev/null); _name=${_dev##*/}
                if [ -n "$_name" ] && [ -e "$R/sys/class/block/$_name" ]; then
                    if [ -e "$R/sys/class/block/$_name/partition" ]; then _name=$(cd -P "$R/sys/class/block/$_name/.." 2>/dev/null && pwd -P); _name=${_name##*/}; fi
                    case "$(cat "$R/sys/block/$_name/queue/rotational" 2>/dev/null)" in 0) _ssd=true ;; 1) _ssd=false ;; esac
                fi ;;
        esac
        _sz=$(awk -v k="$_size" 'BEGIN { printf "%.1f", k / 1048576 }')
        _fr=$(awk -v k="$_avail" 'BEGIN { printf "%.1f", k / 1048576 }')
        _row="{\"mount\":$(js "$(sanitize_mount "$_mnt")"),\"size_gb\":$(jn "$_sz"),\"free_gb\":$(jn "$_fr"),\"ssd\":$(jb "$_ssd")}"
        _list="$_list$_sz$TAB$_row
"
        _n=$((_n + 1))
    done <<EOF
$_rows
EOF
    DISKS_JSON="[$(printf '%s' "$_list" | sort -t "$TAB" -k1,1 -n -r | head -n 6 | cut -f2- | paste -s -d, -)]"
}

smi_rows() {
    # nvidia-smi rows as JSON objects, one per line; empty when nvidia-smi is absent or fails
    have nvidia-smi || return 0
    _o=$(run_to 15 nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader,nounits 2>/dev/null)
    _rc=$?
    _cc=1
    if [ "$_rc" -ne 0 ] || [ -z "$_o" ]; then
        _o=$(run_to 15 nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>/dev/null) || return 0
        _cc=0
    fi
    printf '%s\n' "$_o" | tr -d '\r' | while IFS=, read -r _nm _mem _drv _ccv; do
        [ -z "$_nm" ] && continue
        _nm=$(ascii "$_nm"); _mem=$(ascii "$_mem"); _drv=$(ascii "$_drv"); _ccv=$(ascii "${_ccv:-}")
        _g="null"
        case "$_mem" in ''|*[!0-9.]*) ;; *) _g=$(awk -v m="$_mem" 'BEGIN { printf "%.1f", m / 1024 }') ;; esac
        case "$_ccv" in [0-9]*.[0-9]*) ;; *) _ccv="" ;; esac
        [ "$_cc" = 0 ] && _ccv=""
        printf '{"name":%s,"vendor":"nvidia","mem_gb":%s,"driver":%s,"cc":%s}\n' "$(js "$_nm")" "$_g" "$(js "$_drv")" "$(js "$_ccv")"
    done
}

probe_linux_gpus() {
    _rows=$(smi_rows)
    _list="$_rows"
    _nvdrv=""
    if [ -r "$R"/proc/driver/nvidia/version ]; then
        _nvdrv=$(sed -n 's/.*Kernel Module[^0-9]*\([0-9][0-9.]*\).*/\1/p' "$R"/proc/driver/nvidia/version | head -n 1)
    fi
    if [ -d "$R"/sys/bus/pci/devices ]; then
        for _d in "$R"/sys/bus/pci/devices/*; do
            [ -r "$_d/class" ] || continue
            case "$(cat "$_d/class" 2>/dev/null)" in 0x03*) ;; *) continue ;; esac
            _ven=$(cat "$_d/vendor" 2>/dev/null)
            case "$_ven" in 0x10de) _vn=nvidia ;; 0x1002) _vn=amd ;; 0x8086) _vn=intel ;; *) _vn=other ;; esac
            [ "$_vn" = nvidia ] && [ -n "$_rows" ] && continue
            _slot=${_d##*/}; _nm=""
            if have lspci; then
                _nm=$(lspci -mm -s "$_slot" 2>/dev/null | head -n 1 | awk -F'"' '{
                    v = $4; d = $6
                    if (match(d, /\[[^]]*\]/)) d = substr(d, RSTART + 1, RLENGTH - 2)
                    sub(/ Corporation$/, "", v); sub(/ Inc\.$/, "", v); sub(/^Advanced Micro Devices, Inc\. /, "AMD ", v)
                    print v " " d }')
            fi
            _kd=""
            [ -L "$_d/driver" ] && _kd=$(basename "$(readlink "$_d/driver")")
            _drv=""
            if [ "$_vn" = nvidia ] && { [ "$_kd" = nvidia ] || [ -z "$_kd" ]; }; then _drv="$_nvdrv"; fi
            _list="$_list
{\"name\":$(js "$_nm"),\"vendor\":\"$_vn\",\"mem_gb\":null,\"driver\":$(js "$_drv"),\"cc\":null,\"kernel_driver\":$(js "$_kd")}"
        done
        GPUS_JSON="[$(printf '%s\n' "$_list" | sed '/^$/d' | paste -s -d, -)]"
    elif [ -n "$_rows" ]; then
        GPUS_JSON="[$(printf '%s\n' "$_rows" | sed '/^$/d' | paste -s -d, -)]"
    fi
}

probe_docker() {
    if have docker; then
        DOCKER_PRESENT=true
        DOCKER_CLIENT=$(run_to 10 docker --version 2>/dev/null | head -n 1 | first_ver)
    else DOCKER_PRESENT=false; fi
    if have nvidia-container-runtime || have nvidia-ctk || have nvidia-container-cli || grep -qs nvidia "$R"/etc/docker/daemon.json; then
        DOCKER_NVIDIA=true
    else DOCKER_NVIDIA=false; fi
}

probe_macos() {
    OS_FAMILY=macos; OS_EDITION=macos
    _pn=$(sw_vers -productName 2>/dev/null); _pv=$(sw_vers -productVersion 2>/dev/null)
    OS_NAME=$(ascii "$_pn $_pv"); OS_VERSION="$_pv"; OS_KERNEL=$(uname -r 2>/dev/null); OS_WSL=false
    CPU_MODEL=$(ascii "$(sysctl -n machdep.cpu.brand_string 2>/dev/null)")
    case "$(sysctl -n machdep.cpu.vendor 2>/dev/null)" in GenuineIntel) CPU_VENDOR=intel ;; AuthenticAMD) CPU_VENDOR=amd ;; *) [ "$OS_ARCH" = arm64 ] && CPU_VENDOR=apple ;; esac
    if [ "$CPU_VENDOR" = intel ]; then
        CPU_FAMILY=$(sysctl -n machdep.cpu.family 2>/dev/null); CPU_MODEL_ID=$(sysctl -n machdep.cpu.model 2>/dev/null)
        CPU_STEPPING=$(sysctl -n machdep.cpu.stepping 2>/dev/null)
        _f=" $(sysctl -n machdep.cpu.features 2>/dev/null) $(sysctl -n machdep.cpu.leaf7_features 2>/dev/null) "
        CPU_AVX=false; CPU_AVX2=false; CPU_AVX512=false
        case "$_f" in *" AVX1.0 "*) CPU_AVX=true ;; esac
        case "$_f" in *" AVX2 "*) CPU_AVX2=true ;; esac
        case "$_f" in *" AVX512F "*) CPU_AVX512=true ;; esac
    fi
    CPU_CORES=$(sysctl -n hw.physicalcpu 2>/dev/null); CPU_THREADS=$(sysctl -n hw.logicalcpu 2>/dev/null)
    CPU_SOCKETS=$(sysctl -n hw.packages 2>/dev/null)
    _np=$(sysctl -n hw.nperflevels 2>/dev/null)
    if [ "${_np:-1}" -ge 2 ] 2>/dev/null; then
        CPU_PCORES=$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null); CPU_PTHREADS=$(sysctl -n hw.perflevel0.logicalcpu 2>/dev/null)
        CPU_ECORES=$(sysctl -n hw.perflevel1.physicalcpu 2>/dev/null)
    else CPU_ECORES=0; fi
    CPU_GEN=$(cpu_gen "$CPU_MODEL")
    _ms=$(sysctl -n hw.memsize 2>/dev/null)
    [ -n "$_ms" ] && MEM_TOTAL=$(awk -v b="$_ms" 'BEGIN { printf "%.1f", b / 1073741824 }')
    MEM_FREE=$(vm_stat 2>/dev/null | awk '/page size of/ { for (i = 1; i <= NF; i++) if ($i ~ /^[0-9]+$/) ps = $i }
        /^Pages free:/ { gsub(/\./, "", $3); f += $3 } /^Pages inactive:/ { gsub(/\./, "", $3); f += $3 }
        END { if (ps > 0) printf "%.1f", f * ps / 1073741824 }')
    HW_VENDOR=Apple; HW_MODEL=$(ascii "$(sysctl -n hw.model 2>/dev/null)")
    if [ "$(sysctl -n kern.hv_vmm_present 2>/dev/null)" = 1 ]; then HW_VIRTUAL=true; else HW_VIRTUAL=false; fi
    VIRT_HV=$HW_VIRTUAL
    if [ "$(sysctl -n kern.hv_support 2>/dev/null)" = 1 ]; then VIRT_FW=true; VIRT_SLAT=true; else VIRT_FW=false; fi
    HYP_HYPERV=false; HYP_KVM=false; HYP_PROXMOX=false
    if [ -d /Applications/VirtualBox.app ]; then HYP_VBOX=true; else HYP_VBOX=false; fi
    if [ -d "/Applications/VMware Fusion.app" ]; then HYP_VMWARE=true; else HYP_VMWARE=false; fi
    if have virsh; then HYP_LIBVIRT=true; else HYP_LIBVIRT=false; fi
    # disks: local volumes, one row per distinct free figure (APFS volumes share a container)
    _list=""; _seen=" "
    _rows=$(df -P -k -l 2>/dev/null | awk 'NR > 1 && $1 ~ /^\/dev\// { m = $6; for (i = 7; i <= NF; i++) m = m " " $i; print $2 "\t" $4 "\t" m }')
    while IFS="$TAB" read -r _size _avail _mnt; do
        [ -z "$_size" ] && continue
        case "$_seen" in *" $_avail "*) continue ;; esac
        _seen="$_seen$_avail "
        _sz=$(awk -v k="$_size" 'BEGIN { printf "%.1f", k / 1048576 }'); _fr=$(awk -v k="$_avail" 'BEGIN { printf "%.1f", k / 1048576 }')
        _list="$_list$_sz$TAB{\"mount\":$(js "$(sanitize_mount "$_mnt")"),\"size_gb\":$(jn "$_sz"),\"free_gb\":$(jn "$_fr"),\"ssd\":null}
"
    done <<EOF
$_rows
EOF
    DISKS_JSON="[$(printf '%s' "$_list" | sort -t "$TAB" -k1,1 -n -r | head -n 6 | cut -f2- | paste -s -d, -)]"
    # graphics
    _g=$(system_profiler SPDisplaysDataType 2>/dev/null | awk -F': ' '/Chipset Model:/ { n = $2; gsub(/^ +| +$/, "", n); print n }')
    _list=""
    while IFS= read -r _nm; do
        [ -z "$_nm" ] && continue
        case "$(lc "$_nm")" in *nvidia*|*geforce*|*quadro*) _vn=nvidia ;; *radeon*|*amd*) _vn=amd ;; *intel*) _vn=intel ;; *apple*) _vn=apple ;; *) _vn=other ;; esac
        _list="$_list${_list:+,}{\"name\":$(js "$_nm"),\"vendor\":\"$_vn\",\"mem_gb\":null,\"driver\":null,\"cc\":null}"
    done <<EOF
$_g
EOF
    GPUS_JSON="[$_list]"
    probe_docker
    if have netstat; then
        PORTS_BUSY=$(netstat -an -p tcp 2>/dev/null | awk '$NF == "LISTEN" { n = split($4, a, "."); p = a[n] + 0
            if (p == 8765 || p == 8766 || p == 8443 || p == 8081 || p == 8082 || p == 7452) if (!(p in S)) { S[p] = 1; o = o (o == "" ? "" : ",") p } }
            END { print o }')
        PORTS_CHECKED=true
    fi
}

probe_windows_shell() {
    # The shell on Windows (Git Bash, MSYS2, Cygwin): only what the POSIX layer shows.
    OS_FAMILY=windows
    add_note 'shell version on Windows: most probes are unavailable; run install/deep32-inventory.ps1 for a full report'
    probe_linux_cpu
    if [ -r "$R"/proc/meminfo ]; then
        MEM_TOTAL=$(awk '/^MemTotal:/ { printf "%.1f", $2 / 1048576 }' "$R"/proc/meminfo)
        MEM_FREE=$(awk '/^MemFree:/ { printf "%.1f", $2 / 1048576 }' "$R"/proc/meminfo)
    fi
    _rows=$(smi_rows)
    [ -n "$_rows" ] && GPUS_JSON="[$(printf '%s\n' "$_rows" | sed '/^$/d' | paste -s -d, -)]"
    if have docker; then DOCKER_PRESENT=true; DOCKER_CLIENT=$(run_to 10 docker --version 2>/dev/null | head -n 1 | first_ver); else DOCKER_PRESENT=false; fi
}

probe_tools() {
    _tsx=""
    [ "$OS_FAMILY" = macos ] && _tsx=/Applications/Tailscale.app/Contents/MacOS/Tailscale
    T_TAILSCALE=$(tool_ver tailscale "$_tsx" version)
    T_CLAUDE=$(tool_ver claude "" --version)
    T_CODEX=$(tool_ver codex "" --version)
    T_GH=$(tool_ver gh "" --version)
}

probe_net() {
    _o=""
    for _pair in github=https://github.com/ huggingface=https://huggingface.co/ ubuntu=http://archive.ubuntu.com/ubuntu/ \
                 nvidia=https://developer.download.nvidia.com/compute/cuda/repos/ tailscale=https://login.tailscale.com/; do
        _k=${_pair%%=*}; _u=${_pair#*=}; _ok=false; _code=null; _ms=null
        if have curl; then
            _r=$(curl -sS -o /dev/null -I -A "$UA" -m 8 -w '%{http_code} %{time_total}' "$_u" 2>/dev/null)
            _c=${_r%% *}; _t=${_r#* }
            case "$_c" in ''|000|*[!0-9]*) ;; *) _ok=true; _code=$((10#$_c)); _ms=$(awk -v t="$_t" 'BEGIN { printf "%d", t * 1000 }') ;; esac
        elif have wget; then
            if wget -q --spider -T 8 -U "$UA" "$_u" 2>/dev/null; then _ok=true; fi
        fi
        _o="$_o${_o:+,}\"$_k\":{\"ok\":$_ok,\"code\":$_code,\"ms\":$_ms}"
    done
    _gs=false
    if have gh; then
        if run_to 20 gh auth status --hostname github.com >/dev/null 2>&1; then _gs=true; fi
    fi
    NET_JSON="{$_o,\"gh_signed_in\":$_gs}"
}

probe_all() {
    case "$UNAME_M" in
        x86_64|amd64) OS_ARCH=x64 ;; aarch64|arm64) OS_ARCH=arm64 ;; armv7*|armv6*|arm) OS_ARCH=arm ;; i?86) OS_ARCH=x86 ;; *) OS_ARCH="" ;;
    esac
    case "$UNAME_S" in
        Linux) probe_linux ;;
        Darwin) probe_macos ;;
        VMkernel)
            OS_FAMILY=esxi
            OS_NAME=$(ascii "$(run_to 10 vmware -v 2>/dev/null | head -n 1)")
            OS_VERSION=$(printf '%s\n' "$OS_NAME" | first_ver) ;;
        MINGW*|MSYS*|CYGWIN*) probe_windows_shell ;;
        *) OS_FAMILY=$(lc "$(uname -s 2>/dev/null)"); add_note 'this system is not one the inventory knows' ;;
    esac
    probe_tools
    [ "$OS_FAMILY" = windows ] && [ "$PORTS_CHECKED" = false ] && add_note 'ports not checked from the shell on Windows'
    [ "$NET" = 1 ] && probe_net
}

facts_json() {
    printf '{"os":{"family":%s,"name":%s,"edition":%s,"version":%s,"build":%s,"arch":%s,"wsl_guest":%s,"kernel":%s}' \
        "$(js "$OS_FAMILY")" "$(js "$OS_NAME")" "$(js "$OS_EDITION")" "$(js "$OS_VERSION")" "$(jn "$OS_BUILD")" "$(js "$OS_ARCH")" "$(jb "$OS_WSL")" "$(js "$OS_KERNEL")"
    printf ',"hw":{"vendor":%s,"model":%s,"virtual":%s}' "$(js "$HW_VENDOR")" "$(js "$HW_MODEL")" "$(jb "$HW_VIRTUAL")"
    printf ',"cpu":{"vendor":%s,"model":%s,"family":%s,"model_id":%s,"stepping":%s,"gen":%s,"sockets":%s,"cores":%s,"threads":%s,"p_cores":%s,"e_cores":%s,"p_threads":%s,"avx":%s,"avx2":%s,"avx512f":%s}' \
        "$(js "$CPU_VENDOR")" "$(js "$CPU_MODEL")" "$(jn "$CPU_FAMILY")" "$(jn "$CPU_MODEL_ID")" "$(jn "$CPU_STEPPING")" "$(js "$CPU_GEN")" \
        "$(jn "$CPU_SOCKETS")" "$(jn "$CPU_CORES")" "$(jn "$CPU_THREADS")" "$(jn "$CPU_PCORES")" "$(jn "$CPU_ECORES")" "$(jn "$CPU_PTHREADS")" \
        "$(jb "$CPU_AVX")" "$(jb "$CPU_AVX2")" "$(jb "$CPU_AVX512")"
    printf ',"mem":{"total_gb":%s,"installed_gb":%s,"free_gb":%s}' "$(jn "$MEM_TOTAL")" "$(jn "$MEM_INSTALLED")" "$(jn "$MEM_FREE")"
    printf ',"disks":%s,"gpus":%s' "$DISKS_JSON" "$GPUS_JSON"
    printf ',"virt":{"firmware":%s,"slat":%s,"iommu":%s,"hypervisor":%s}' "$(jb "$VIRT_FW")" "$(jb "$VIRT_SLAT")" "$(jb "$VIRT_IOMMU")" "$(jb "$VIRT_HV")"
    printf ',"hyp":{"hyperv":%s,"kvm":%s,"proxmox":%s,"virtualbox":%s,"vmware":%s,"libvirt":%s}' \
        "$(jb "$HYP_HYPERV")" "$(jb "$HYP_KVM")" "$(jb "$HYP_PROXMOX")" "$(jb "$HYP_VBOX")" "$(jb "$HYP_VMWARE")" "$(jb "$HYP_LIBVIRT")"
    printf ',"wsl":%s' "$WSL_JSON"
    printf ',"docker":{"present":%s,"client":%s,"nvidia":%s}' "$(jb "$DOCKER_PRESENT")" "$(js "$DOCKER_CLIENT")" "$(jb "$DOCKER_NVIDIA")"
    printf ',"tools":{"tailscale":%s,"claude":%s,"codex":%s,"gh":%s}' "$T_TAILSCALE" "$T_CLAUDE" "$T_CODEX" "$T_GH"
    printf ',"ports":{"checked":%s,"busy":[%s]}' "$PORTS_CHECKED" "$PORTS_BUSY"
    printf ',"net":%s}' "$NET_JSON"
}

# ------------------------------------------------------------------ report
do_report() {
    if [ -n "$FACTS" ]; then
        if [ ! -f "$FACTS" ]; then err "facts file not found: $FACTS"; return 2; fi
        add_note 'facts read from a file; no probe ran'
        _doc=$(tr -d '\000' < "$FACTS")
    else
        probe_all
        _doc="{\"facts\":$(facts_json)}"
    fi
    _m="$MATRIX"; _merr=""
    if [ ! -f "$_m" ]; then _merr="install matrix not found: $MATRIX"; _m=""; fi
    _out=$(printf '%s\n' "$_doc" | run_verdict "$_m")
    _rc=$?
    _first=$(printf '%s\n' "$_out" | sed -n 1p)
    if [ "$_rc" -ne 0 ] || [ "$_first" = "ERROR facts" ]; then err 'the facts are not a JSON object with a "facts" member'; return 1; fi
    if [ -n "$_m" ] && [ "$_first" = null ]; then _merr="not a deep32 install matrix: $MATRIX"; fi
    [ -n "$_merr" ] && add_note "$_merr"
    if [ "$JSON" = 1 ]; then
        _v="$_first"
        [ "$_v" = null ] && _v='{"error":"matrix-unavailable"}'
        printf '{"schema":"%s","tool":"%s","impl":"sh","mode":"report","at":"%s","facts":%s,"verdict":%s,"notes":[%s]}\n' \
            "$SCHEMA" "$TOOL_VERSION" "$(utc)" "$(printf '%s\n' "$_out" | sed -n 2p)" "$_v" "$NOTES"
    else
        printf '%s\n' "$_out" | sed -n '3,$p'
        printf '%s' "$NOTES_TXT"
    fi
    if [ -n "$_merr" ]; then err "$_merr"; return 1; fi
    return 0
}

# ------------------------------------------------------------------ bench quick
IFS= read -r -d '' BENCH_PY <<'PY'
import json, mmap, os, random, sys, tempfile, time
secs = float(os.environ["D32_SECS"])
total = secs
def spin(t):
    x = 88172645463325252; acc = 0.0; it = 0; mask = (1 << 64) - 1
    end = time.perf_counter() + t
    while time.perf_counter() < end:
        for _ in range(4096):
            x ^= (x << 13) & mask; x ^= x >> 7; x ^= (x << 17) & mask
            acc += (x & 0xFFFF) * 1.0000001
        it += 4096
    return it
def rd(fd, buf, off):
    if hasattr(os, "preadv"):
        return os.preadv(fd, [buf], off)
    return len(os.pread(fd, len(buf), off))
res = {"method": "python"}
t_cpu = total * 0.15
t0 = time.perf_counter(); n = spin(t_cpu); st = n / (time.perf_counter() - t0) / 1e6
threads = os.cpu_count() or 1
pids = []; reads = []
t0 = time.perf_counter()
for _ in range(threads):
    r, w = os.pipe(); pid = os.fork()
    if pid == 0:
        os.close(r); c = spin(t_cpu); os.write(w, str(c).encode()); os._exit(0)
    os.close(w); pids.append(pid); reads.append(r)
tot = 0
for r in reads:
    data = b""
    while True:
        chunk = os.read(r, 64)
        if not chunk: break
        data += chunk
    os.close(r); tot += int(data or b"0")
for pid in pids: os.waitpid(pid, 0)
mt = tot / (time.perf_counter() - t0) / 1e6
res["cpu"] = {"st_mops": round(st, 2), "mt_mops": round(mt, 2), "threads": threads, "scaling": round(mt / max(st, 1e-9), 2)}
mb = 256 if secs >= 60 else 128
try:
    pages = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    mb = max(16, min(mb, int(pages * 0.05 / 2 / 1048576)))
except (ValueError, OSError, AttributeError):
    pass
a = bytearray(os.urandom(1024)) * (mb * 1024); b = bytearray(len(a)); mv = memoryview(b)
mv[:] = a
t_mem = total * 0.10; copied = 0; t0 = time.perf_counter()
while time.perf_counter() - t0 < t_mem:
    mv[:] = a; copied += len(a)
res["mem"] = {"copy_gbs": round(copied / (time.perf_counter() - t0) / 1e9, 2), "buffer_mb": mb}
del a, b, mv
tmpdir = os.environ.get("TMPDIR") or ("/var/tmp" if os.path.isdir("/var/tmp") else tempfile.gettempdir())
disk = {"where": tmpdir if tmpdir in ("/tmp", "/var/tmp") else "temp", "free_gb": None, "file_mb": None, "seq_write_mbs": None,
        "seq_read_mbs": None, "rand_read_iops": None, "rand_write_iops": None, "direct": None, "skipped": None}
try:
    sv = os.statvfs(tmpdir); free = sv.f_bavail * sv.f_frsize
    disk["free_gb"] = round(free / 1073741824, 1)
    want = 1073741824 if secs >= 60 else 268435456
    size = min(want, (int(free * 0.05) // 1048576) * 1048576)
    if free - size < 10737418240:
        disk["skipped"] = "less than 10 GB would stay free"
    elif size < 67108864:
        disk["skipped"] = "too little room for a test file"
    else:
        path = os.path.join(tmpdir, "deep32-bench-%d-%s.tmp" % (os.getpid(), os.urandom(4).hex()))
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        direct = hasattr(os, "O_DIRECT")
        fd = None
        try:
            if direct:
                try:
                    fd = os.open(path, flags | os.O_DIRECT, 0o600)
                except OSError:
                    direct = False
            if fd is None:
                fd = os.open(path, flags, 0o600)
            os.unlink(path)
            if not direct and sys.platform == "darwin":
                try:
                    import fcntl; fcntl.fcntl(fd, 48, 1); direct = True
                except OSError:
                    pass
            disk["direct"] = direct
            blk = mmap.mmap(-1, 1048576); blk.write(os.urandom(1048576))
            small = mmap.mmap(-1, 4096); small.write(os.urandom(4096))
            written = 0; t0 = time.perf_counter(); tw = total * 0.20
            while written + 1048576 <= size and time.perf_counter() - t0 < tw:
                os.pwrite(fd, blk, written); written += 1048576
            os.fsync(fd)
            disk["seq_write_mbs"] = round(written / 1048576 / max(time.perf_counter() - t0, 1e-3), 1)
            if not direct and hasattr(os, "posix_fadvise"):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            rdn = 0; t0 = time.perf_counter(); tr = total * 0.10
            while rdn < written and time.perf_counter() - t0 < tr:
                got = rd(fd, blk, rdn)
                if got <= 0: break
                rdn += got
            disk["seq_read_mbs"] = round(rdn / 1048576 / max(time.perf_counter() - t0, 1e-3), 1)
            pages = max(1, written // 4096); rnd = random.Random(32)
            ops = 0; t0 = time.perf_counter(); trr = total * 0.15
            while time.perf_counter() - t0 < trr:
                rd(fd, small, rnd.randrange(pages) * 4096); ops += 1
            disk["rand_read_iops"] = int(ops / max(time.perf_counter() - t0, 1e-3))
            ops = 0; t0 = time.perf_counter(); trw = total * 0.15
            while time.perf_counter() - t0 < trw:
                os.pwrite(fd, small, rnd.randrange(pages) * 4096); os.fdatasync(fd) if hasattr(os, "fdatasync") else os.fsync(fd); ops += 1
            disk["rand_write_iops"] = int(ops / max(time.perf_counter() - t0, 1e-3))
            disk["file_mb"] = written // 1048576
        finally:
            if fd is not None: os.close(fd)
            if os.path.exists(path): os.unlink(path)
except Exception as exc:
    disk["skipped"] = "disk test failed: %s" % type(exc).__name__
res["disk"] = disk
print(json.dumps(res, separators=(",", ":")))
PY

jget() {
    # jget JSON KEY: the first value of KEY in a flat-enough JSON text (strings without quotes)
    printf '%s' "$1" | LC_ALL=C awk -v k="\"$2\":" '{
        i = index($0, k); if (!i) exit
        s = substr($0, i + length(k))
        if (substr(s, 1, 1) == "\"") { s = substr(s, 2); j = index(s, "\""); print substr(s, 1, j - 1) }
        else { match(s, /^[^,}]*/); print substr(s, 1, RLENGTH) }
    }'
}

guard_one() {
    # guard_one FILE: prints nothing when FILE is not a file or its top-level "mode" is a string
    # other than "on" (any case); "gaming-mode" when it is "on"; else "gaming-state-unreadable"
    [ -n "$1" ] || return 0
    [ -f "$1" ] || return 0
    _gm=$( { tr -d '\000' < "$1"; } 2>/dev/null | D32_GUARD_ONLY=1 LC_ALL=C awk -v VERSION="$TOOL_VERSION" "$VERDICT_AWK" 2>/dev/null)
    case "$_gm" in
        mode=on) printf 'gaming-mode' ;;
        mode=*) ;;
        *) printf 'gaming-state-unreadable' ;;
    esac
}

gaming_state() {
    # prints nothing when the bench may run, else the reason; the DEEP32_GUARD_FILE state file is
    # read when that variable is set, and --guard-file adds one more
    _gr=$(guard_one "$GUARD")
    [ -z "$_gr" ] && _gr=$(guard_one "$GUARD_EXTRA")
    printf '%s' "$_gr"
    return 0
}

bench_shell() {
    # Cruder fallback without python3: whole-second clock, no random-access test.
    _t=$BENCH_SECS
    _tc=$(( _t * 15 / 100 )); [ "$_tc" -lt 1 ] && _tc=1
    # srand() returns the previous seed, which is the time of day of the previous srand() call
    _spin='BEGIN { x = 0; n = 0; srand(); t0 = srand(); t = t0; while (t - t0 < S) { for (i = 0; i < 200000; i++) { x = (x * 1103515245 + 12345) % 2147483648 } n += 200000; srand(); t = srand() } printf "%.0f\n", n / (t - t0) }'
    _st=$(awk -v S="$_tc" "$_spin")
    _thr=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
    _sum=$( (
        _i=0
        while [ "$_i" -lt "$_thr" ]; do awk -v S="$_tc" "$_spin" & _i=$((_i + 1)); done
        wait
    ) | awk '{ s += $1 } END { printf "%.0f", s }')
    _mops_st=$(awk -v n="$_st" 'BEGIN { printf "%.2f", n / 1e6 }')
    _mops_mt=$(awk -v n="$_sum" 'BEGIN { printf "%.2f", n / 1e6 }')
    printf '{"method":"shell","cpu":{"st_mops":%s,"mt_mops":%s,"threads":%s,"scaling":null},"mem":{"copy_gbs":null,"buffer_mb":null},"disk":{"where":"temp","skipped":"not measured without python3"}}' \
        "$_mops_st" "$_mops_mt" "$_thr"
}

do_bench() {
    _why=$(gaming_state)
    if [ -z "$_why" ] && [ "$GUARD_ONLY" = 1 ]; then
        if [ "$JSON" = 1 ]; then
            printf '{"schema":"%s","tool":"%s","impl":"sh","mode":"bench-quick","at":"%s","guard":"clear"}\n' "$SCHEMA" "$TOOL_VERSION" "$(utc)"
        else
            printf 'Guard      clear (test hook: nothing was measured)\n'
        fi
        return 0
    fi
    if [ -z "$_why" ]; then
        case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) _why='windows-use-ps1' ;; esac
    fi
    if [ -n "$_why" ]; then
        if [ "$JSON" = 1 ]; then
            printf '{"schema":"%s","tool":"%s","impl":"sh","mode":"bench-quick","at":"%s","refused":"%s"}\n' "$SCHEMA" "$TOOL_VERSION" "$(utc)" "$_why"
        else
            printf 'Refused: %s\n' "$_why"
        fi
        return 3
    fi
    _py=""
    for _c in python3 python; do
        if have "$_c" && "$_c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' >/dev/null 2>&1; then _py="$_c"; break; fi
    done
    if [ -n "$_py" ]; then
        _res=$(printf '%s' "$BENCH_PY" | D32_SECS="$BENCH_SECS" "$_py" -I - 2>/dev/null)
        [ -z "$_res" ] && { err 'the bench failed'; return 1; }
    else
        _res=$(bench_shell)
    fi
    # graphics read-out
    case "$(uname -s 2>/dev/null)" in Linux) probe_linux_gpus ;; Darwin) probe_macos >/dev/null 2>&1 ;; esac
    add_note 'scores compare only with runs of the same method; no reference rows yet'
    if [ "$JSON" = 1 ]; then
        printf '{"schema":"%s","tool":"%s","impl":"sh","mode":"bench-quick","at":"%s","seconds":%s,"bench":%s,"gpus":%s,"notes":[%s]}\n' \
            "$SCHEMA" "$TOOL_VERSION" "$(utc)" "$BENCH_SECS" "$_res" "$GPUS_JSON" "$NOTES"
    else
        printf 'Deep32 inventory %s (bench quick, %s s, method %s)\n' "$TOOL_VERSION" "$BENCH_SECS" "$(jget "$_res" method)"
        printf 'Processor  single thread %s Mops/s; all %s threads %s Mops/s (x%s)\n' \
            "$(jget "$_res" st_mops)" "$(jget "$_res" threads)" "$(jget "$_res" mt_mops)" "$(jget "$_res" scaling)"
        printf 'Memory     copy %s GB/s (%s MB buffers)\n' "$(jget "$_res" copy_gbs)" "$(jget "$_res" buffer_mb)"
        _sk=$(jget "$_res" skipped)
        if [ -n "$_sk" ] && [ "$_sk" != null ]; then printf 'Disk       skipped: %s\n' "$_sk"
        else
            printf 'Disk       temp folder, %s MB file: write %s MB/s, read %s MB/s, random 4K read %s IOPS, write %s IOPS\n' \
                "$(jget "$_res" file_mb)" "$(jget "$_res" seq_write_mbs)" "$(jget "$_res" seq_read_mbs)" "$(jget "$_res" rand_read_iops)" "$(jget "$_res" rand_write_iops)"
        fi
        printf '%s\n' "$GPUS_JSON" | LC_ALL=C awk '{ n = split($0, a, "\"name\":\""); for (i = 2; i <= n; i++) { split(a[i], b, "\""); print "Graphics   " b[1] } }'
        printf '%s' "$NOTES_TXT"
    fi
    return 0
}

# ------------------------------------------------------------------ main
case "$MODE" in
    report) do_report; exit $? ;;
    bench) do_bench; exit $? ;;
esac
exit 1
