#!/bin/sh
# verify-release.sh: check a Deep32 release before anything is installed from it.
#
# What it does, in order (contract C2, docs/contracts/C2-release-manifest-draft.md):
#   1. Checks the manifest's OpenPGP signature with gpgv, against the release key that
#      ships beside this script (release-key.gpg) and nothing else, exactly:
#        gpgv --homedir <new empty private folder> --keyring <full path of release-key.gpg> \
#            --status-fd 1 MANIFEST.json.asc MANIFEST.json
#      on private copies of the two files, so the bytes checked are the bytes then read.
#      Your own GnuPG keyring is never read. The signature is accepted only when gpgv
#      succeeds, reports exactly one GOODSIG and one VALIDSIG and nothing bad, VALIDSIG
#      names a binary signature (class 00: over the exact bytes; a text-mode signature,
#      class 01, is refused), and the last field of VALIDSIG (the fingerprint of the
#      signing key's primary key) equals the fingerprint pinned in
#      release-key.fingerprint. The manifest must name that same key
#      (signer.fingerprint). Nothing in the manifest is used before this passes.
#   2. Checks the size and SHA-256 of every part of every disc.
#   3. Joins each disc's parts in order into the disc file and checks the whole-disc
#      size and SHA-256. A disc that fails is deleted, never left half-joined.
#   gpgv is looked for in this order: --gpgv when given; gpgv on PATH; then (Git Bash on
#   Windows) /c/Program Files/Git/usr/bin/gpgv.exe, /c/Program Files/GnuPG/bin/gpgv.exe,
#   /c/Program Files (x86)/GnuPG/bin/gpgv.exe. None found: exit 3, never a pass. There
#   is no unsigned mode. gpgv is part of GnuPG: on Linux the gpgv package (usually there
#   already), on macOS "brew install gnupg", on OpenBSD "pkg_add gnupg".
#   Needs a POSIX sh, awk, and one of sha256sum, shasum, sha256 or openssl.
#
# Usage:
#   verify-release.sh [options] PARTS_DIR
#     --key-dir DIR           folder holding release-key.gpg and release-key.fingerprint
#                             (default: this script's own folder, install/ of the repository)
#     --manifest FILE         default PARTS_DIR/MANIFEST.json
#     --signature FILE        default: the manifest path with .asc added
#     --out DIR               where joined discs are written (default PARTS_DIR)
#     --role ROLE             check only this disc (system, content or gpu); repeatable
#     --no-join               check the signature and the parts only
#     --signature-only        check the signature and the manifest's form only
#     --gpgv PROG             the gpgv to use
#     --quiet                 print only failures and the final line
#     --help
#   Test hook: when the environment variable DEEP32_GPGV_CANDIDATES is set, its list
#   (paths separated by "|") replaces the three fixed gpgv locations above.
#
# Exit codes (the same in verify-release.ps1):
#   0  every check passed (and every disc was joined, unless told not to join)
#   1  usage error
#   2  an input file or folder is missing or unreadable, or release-key.fingerprint is
#      malformed
#   3  gpgv was not found or could not be started
#   4  the signature is not good, is not by the pinned release key, or the manifest
#      names another signer than the key that signed it
#   5  the manifest is not usable: not JSON, unknown format or version, unsafe names,
#      or a requested role is not in it
#   6  a part is missing or has the wrong size
#   7  a part has the wrong SHA-256
#   8  a joined disc has the wrong size or SHA-256 (it is deleted)
#   9  a joined disc or the private work folder could not be written
#   10 (verify-release.sh only) another needed tool is missing: a SHA-256 tool
#   When several checks fail, the code is that of the first failure found; every
#   part is still checked so that all bad parts are listed.

FORMAT=deep32-release-manifest
FORMAT_VERSION=1
GPGV_PLACES='/c/Program Files/Git/usr/bin/gpgv.exe|/c/Program Files/GnuPG/bin/gpgv.exe|/c/Program Files (x86)/GnuPG/bin/gpgv.exe'

KEYDIR=
MANIFEST=
SIG=
OUT=
ROLES=
NOJOIN=0
SIGONLY=0
GPGV=
QUIET=0
PARTS=

say() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }
die() { err "FAIL: $2"; printf 'RESULT: FAIL (exit %s)\n' "$1"; exit "$1"; }
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

while [ $# -gt 0 ]; do
    case $1 in
        --key-dir) [ $# -ge 2 ] || die 1 "$1 needs a value"; KEYDIR=$2; shift 2 ;;
        --manifest) [ $# -ge 2 ] || die 1 "$1 needs a value"; MANIFEST=$2; shift 2 ;;
        --signature) [ $# -ge 2 ] || die 1 "$1 needs a value"; SIG=$2; shift 2 ;;
        --out) [ $# -ge 2 ] || die 1 "$1 needs a value"; OUT=$2; shift 2 ;;
        --role)
            [ $# -ge 2 ] || die 1 "$1 needs a value"
            case $2 in system|content|gpu) ROLES="$ROLES $2" ;; *) die 1 "--role must be system, content or gpu" ;; esac
            shift 2 ;;
        --no-join) NOJOIN=1; shift ;;
        --signature-only) SIGONLY=1; NOJOIN=1; shift ;;
        --gpgv) [ $# -ge 2 ] || die 1 "$1 needs a value"; GPGV=$2; shift 2 ;;
        --quiet) QUIET=1; shift ;;
        --help|-h) usage; exit 0 ;;
        --) shift; break ;;
        -*) die 1 "unknown option: $1 (see --help)" ;;
        *) [ -z "$PARTS" ] || die 1 "only one PARTS_DIR may be given"; PARTS=$1; shift ;;
    esac
done
if [ $# -gt 0 ]; then
    [ -z "$PARTS" ] && [ $# -eq 1 ] || die 1 "only one PARTS_DIR may be given"
    PARTS=$1
fi
[ -n "$PARTS" ] || die 1 "PARTS_DIR is required (see --help)"
[ -n "$KEYDIR" ] || KEYDIR=$(dirname "$0")
[ -n "$MANIFEST" ] || MANIFEST=$PARTS/MANIFEST.json
[ -n "$SIG" ] || SIG=$MANIFEST.asc
[ -n "$OUT" ] || OUT=$PARTS

[ -d "$PARTS" ] || die 2 "parts folder not found: $PARTS"
[ -d "$KEYDIR" ] || die 2 "key folder not found: $KEYDIR"
KEYDIR=$(cd "$KEYDIR" 2>/dev/null && pwd) || die 2 "key folder unreadable"
KEYRING=$KEYDIR/release-key.gpg
PINFILE=$KEYDIR/release-key.fingerprint
[ -r "$MANIFEST" ] || die 2 "manifest not found: $MANIFEST"
[ -r "$SIG" ] || die 2 "signature not found: $SIG"
[ -r "$KEYRING" ] || die 2 "release key not found: $KEYRING"
[ -r "$PINFILE" ] || die 2 "release key fingerprint not found: $PINFILE"
if [ "$NOJOIN" -eq 0 ]; then [ -d "$OUT" ] || die 2 "output folder not found: $OUT"; fi

# release-key.fingerprint: line 1 the 40 hex digits; line 2, if there, the same spaced.
# A UTF-8 byte-order mark in front is dropped, as verify-release.ps1 does.
BOM=$(printf '\357\273\277')
PIN_LINES=$(tr -d '\r' < "$PINFILE" | LC_ALL=C sed "1s/^$BOM//" | awk '{ gsub(/^[ \t]+|[ \t]+$/, "") } $0 != "" && substr($0, 1, 1) != "#"')
PIN=$(printf '%s\n' "$PIN_LINES" | sed -n '1p')
PIN2=$(printf '%s\n' "$PIN_LINES" | sed -n '2p' | tr -d ' ')
PIN_N=$(printf '%s\n' "$PIN_LINES" | awk 'NF { n++ } END { print n + 0 }')
case $PIN in ''|*[!0-9A-F]*) die 2 "the first line of $PINFILE must be the 40 upper-case hex digits of the release key's fingerprint" ;; esac
[ ${#PIN} -eq 40 ] || die 2 "the first line of $PINFILE must be the 40 upper-case hex digits of the release key's fingerprint"
if [ "$PIN_N" -gt 2 ] || { [ -n "$PIN2" ] && [ "$PIN2" != "$PIN" ]; }; then
    die 2 "$PINFILE: the second line must be the same fingerprint, spaced, and nothing more"
fi

# ---- tools ---------------------------------------------------------------
find_gpgv() {
    if [ -n "$GPGV" ]; then
        if [ -f "$GPGV" ]; then printf '%s\n' "$GPGV"; return 0; fi
        command -v "$GPGV" 2>/dev/null && return 0
        return 1
    fi
    command -v gpgv 2>/dev/null && return 0
    list=${DEEP32_GPGV_CANDIDATES:-$GPGV_PLACES}
    set -f
    old_ifs=$IFS
    IFS='|'
    for c in $list; do
        if [ -n "$c" ] && [ -f "$c" ]; then IFS=$old_ifs; set +f; printf '%s\n' "$c"; return 0; fi
    done
    IFS=$old_ifs
    set +f
    return 1
}
GPGV_PATH=$(find_gpgv) || GPGV_PATH=
if [ -z "$GPGV_PATH" ]; then
    case $(uname -s 2>/dev/null) in
        Darwin) hint="on macOS: brew install gnupg" ;;
        OpenBSD) hint="on OpenBSD: pkg_add gnupg" ;;
        Linux) hint="on Linux: install the gpgv package (Debian, Ubuntu: sudo apt install gpgv; Fedora: sudo dnf install gnupg2)" ;;
        MINGW*|MSYS*|CYGWIN*) hint="on Windows it comes with Git for Windows (C:\\Program Files\\Git\\usr\\bin\\gpgv.exe) and with Gpg4win" ;;
        *) hint="install GnuPG (it brings gpgv)" ;;
    esac
    die 3 "gpgv not found ($hint; or give --gpgv with the path of a gpgv)"
fi
if command -v sha256sum >/dev/null 2>&1; then HASHER=sha256sum
elif command -v shasum >/dev/null 2>&1; then HASHER=shasum
elif command -v sha256 >/dev/null 2>&1; then HASHER=sha256
elif command -v openssl >/dev/null 2>&1; then HASHER=openssl
else die 10 "no SHA-256 tool found (need sha256sum, shasum, sha256 or openssl)"
fi

sha256_of() {
    case $HASHER in
        sha256sum) sha256sum "$1" | awk '{ sub(/^\\/, "", $1); print $1 }' ;;
        shasum) shasum -a 256 "$1" | awk '{ print $1 }' ;;
        sha256) sha256 -q "$1" ;;
        openssl) openssl dgst -sha256 -r "$1" | awk '{ print $1 }' ;;
    esac
}
size_of() { wc -c < "$1" | tr -d ' \t\r\n'; }

# Without mktemp, make a NEW private folder: mkdir without -p fails if the name exists,
# so a folder someone else prepared under that name is never used. (umask, not
# mkdir -m: Git Bash on Windows refuses the chmod that -m does.)
TMP=$(mktemp -d 2>/dev/null) || {
    TMP=${TMPDIR:-/tmp}/verify-release.$$
    (umask 077 && mkdir "$TMP") 2>/dev/null || die 9 "could not make a temporary folder"
}
[ -d "$TMP" ] || die 9 "could not make a temporary folder"
JOINING=
cleanup() { [ -n "$JOINING" ] && rm -f "$JOINING"; rm -rf "$TMP"; }
trap cleanup EXIT
trap 'exit 130' INT TERM

# ---- 1. signature --------------------------------------------------------
# Work on private copies so the bytes checked are the bytes read.
cp "$MANIFEST" "$TMP/manifest.json" 2>/dev/null || die 2 "manifest unreadable: $MANIFEST"
cp "$SIG" "$TMP/manifest.json.asc" 2>/dev/null || die 2 "signature unreadable: $SIG"
(umask 077 && mkdir "$TMP/gnupg") 2>/dev/null || die 9 "could not make a temporary folder"
say "signature: gpgv --keyring $KEYRING --status-fd 1 $SIG $MANIFEST"
say "  using $GPGV_PATH; pinned release key $PIN"
"$GPGV_PATH" --homedir "$TMP/gnupg" --keyring "$KEYRING" --status-fd 1 \
    "$TMP/manifest.json.asc" "$TMP/manifest.json" > "$TMP/gpgv.out" 2> "$TMP/gpgv.err"
rc=$?
case $rc in 126|127) die 3 "gpgv could not be started: $GPGV_PATH" ;; esac
tr -d '\r' < "$TMP/gpgv.out" | sed -n 's/^\[GNUPG:\] //p' > "$TMP/status"
NGOOD=$(awk '$1 == "GOODSIG" { n++ } END { print n + 0 }' "$TMP/status")
NVALID=$(awk '$1 == "VALIDSIG" { n++ } END { print n + 0 }' "$TMP/status")
BAD=$(awk '$1 ~ /^(BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG|NO_PUBKEY)$/ { printf "%s ", $1 }' "$TMP/status")
if [ "$rc" -ne 0 ] || [ "$NGOOD" -ne 1 ] || [ "$NVALID" -ne 1 ] || [ -n "$BAD" ]; then
    tr -d '\r' < "$TMP/gpgv.err" | sed 's/^/  gpgv: /' >&2
    [ -z "$BAD" ] || err "  status: $BAD"
    case " $BAD" in
        *" EXPKEYSIG "*) die 4 "the signature was made by the release key, but the release key here has expired: take the current install/release-key.gpg from the repository (its expiry is extended before it lapses) and verify again" ;;
        *" NO_PUBKEY "*|*" ERRSIG "*) die 4 "the manifest was signed by a key that is not the shipped release key" ;;
        *" BADSIG "*) die 4 "the manifest signature is BAD: the manifest or its signature has been changed" ;;
    esac
    if [ "$NGOOD" -gt 1 ] || [ "$NVALID" -gt 1 ]; then die 4 "the signature file holds more than one signature; a release has exactly one"; fi
    die 4 "the manifest signature is NOT good (gpgv exit $rc)"
fi
SIGNED_BY=$(awk '$1 == "VALIDSIG" { print $NF }' "$TMP/status" | tr 'abcdef' 'ABCDEF')
VALID_NF=$(awk '$1 == "VALIDSIG" { print NF }' "$TMP/status")
if [ "$VALID_NF" -lt 11 ] || [ "$SIGNED_BY" != "$PIN" ]; then
    die 4 "the manifest was signed by key $SIGNED_BY, not by the pinned release key $PIN"
fi
# VALIDSIG fields: fpr date timestamp expiry version reserved pk-algo hash-algo CLASS primary-fpr
SIG_CLASS=$(awk '$1 == "VALIDSIG" { print $10 }' "$TMP/status")
if [ "$SIG_CLASS" != "00" ]; then
    die 4 "the signature has class $SIG_CLASS, not 00: only a binary detached signature (gpg --detach-sign, without --textmode) covers the exact bytes of the manifest"
fi
say "  good signature by the pinned release key $PIN"

# ---- read the manifest (signed bytes only) -------------------------------
# A small JSON reader in awk: prints PATH<TAB>VALUE for every scalar.
awk '
function ws() { while (i <= n) { c = substr(s, i, 1); if (c == " " || c == "\t" || c == "\n" || c == "\r") i++; else break } }
function str(   o, c, e) {
    i++; o = ""
    while (i <= n) {
        c = substr(s, i, 1)
        if (c == "\\") { e = substr(s, i + 1, 1); if (e == "\"" || e == "\\" || e == "/") o = o e; else o = o "\\" e; i += 2; continue }
        if (c == "\"") { i++; return o }
        o = o c; i++
    }
    bad = 1; return o
}
function val(p,   c, k, j, v) {
    if (bad) return
    ws(); c = substr(s, i, 1)
    if (c == "{") {
        i++; ws(); if (substr(s, i, 1) == "}") { i++; return }
        while (!bad) {
            ws(); if (substr(s, i, 1) != "\"") { bad = 1; return }
            k = str(); ws(); if (substr(s, i, 1) != ":") { bad = 1; return }
            i++; val(p "." k); ws(); c = substr(s, i, 1); i++
            if (c == "}") return
            if (c != ",") { bad = 1; return }
        }
        return
    }
    if (c == "[") {
        i++; ws(); if (substr(s, i, 1) == "]") { i++; return }
        j = 0
        while (!bad) {
            val(p "[" j "]"); j++; ws(); c = substr(s, i, 1); i++
            if (c == "]") return
            if (c != ",") { bad = 1; return }
        }
        return
    }
    if (c == "\"") { v = str(); printf "%s\t%s\n", p, v; return }
    v = ""
    while (i <= n) { c = substr(s, i, 1); if (c ~ /[-+0-9.eEa-z]/) { v = v c; i++ } else break }
    if (v == "") { bad = 1; return }
    printf "%s\t%s\n", p, v
}
{ s = s $0 "\n" }
END { n = length(s); i = 1; bad = 0; val(""); ws(); if (i <= n) bad = 1; if (bad) exit 1 }
' "$TMP/manifest.json" > "$TMP/flat" || die 5 "manifest is not valid JSON"

field() { awk -F '\t' -v k="$1" '$1 == k { print $2; exit }' "$TMP/flat"; }
[ "$(field .format)" = "$FORMAT" ] || die 5 "manifest format is not $FORMAT"
[ "$(field .format_version)" = "$FORMAT_VERSION" ] || die 5 "manifest format_version $(field .format_version) is not supported (this verifier reads $FORMAT_VERSION)"
SFPR=$(field .signer.fingerprint)
SIGNER_BAD=0
[ "$(field .signer.type)" = "openpgp" ] || SIGNER_BAD=1
case $SFPR in ''|*[!0-9A-F]*) SIGNER_BAD=1 ;; esac
[ ${#SFPR} -eq 40 ] || SIGNER_BAD=1
[ "$SIGNER_BAD" -eq 0 ] || die 5 'manifest signer is not {"type": "openpgp", "fingerprint": <40 upper-case hex digits>}'
[ "$SFPR" = "$PIN" ] || die 4 "the manifest names signer $SFPR but was signed by the release key $PIN"
say "manifest: release $(field .release.version) ($(field .release.tag)), created $(field .release.created), signer $SFPR"
[ "$(field .release.complete)" = "true" ] || err "WARNING: this manifest is marked incomplete (fewer than three discs)"

# Records: D <disc> <role> <file> <size> <sha256> <nparts>  /  P <disc> <part> <name> <size> <sha256>
# Index numbers are taken by turning every non-digit run of the path into a space
# (".discs[0].parts[3].sha256" gives "0 3 256"), which every POSIX awk can do.
awk -F '\t' '
function idx(path,   x) { x = path; gsub(/[^0-9]+/, " ", x); return split(x, a, " ") }
$1 ~ /^\.discs\[[0-9]+\]\.(role|file|size|sha256)$/ {
    idx($1); d = a[1] + 0; f = $1; sub(/^.*\./, "", f); D[d, f] = $2; if (d + 1 > nd) nd = d + 1 }
$1 ~ /^\.discs\[[0-9]+\]\.parts\[[0-9]+\]\.(name|size|sha256)$/ {
    idx($1); d = a[1] + 0; p = a[2] + 0; f = $1; sub(/^.*\./, "", f); P[d, p, f] = $2
    if (p + 1 > np[d]) np[d] = p + 1 }
END {
    for (d = 0; d < nd; d++) {
        printf "D %d %s %s %s %s %d\n", d, D[d, "role"], D[d, "file"], D[d, "size"], D[d, "sha256"], np[d]
        for (p = 0; p < np[d]; p++) printf "P %d %d %s %s %s\n", d, p, P[d, p, "name"], P[d, p, "size"], P[d, p, "sha256"]
    }
}' "$TMP/flat" > "$TMP/records" || die 5 "manifest could not be read"

safe_name() { case $1 in ''|.*|*[!A-Za-z0-9._-]*) return 1 ;; esac; return 0; }
safe_hash() { case $1 in *[!0-9a-f]*) return 1 ;; esac; [ ${#1} -eq 64 ]; }
safe_num() { case $1 in ''|*[!0-9]*) return 1 ;; esac; return 0; }

ndiscs=0
while read -r kind a b c d e f; do
    if [ "$kind" = D ]; then
        ndiscs=$((ndiscs + 1))
        case $b in system|content|gpu) ;; *) die 5 "manifest disc $a has an unknown role" ;; esac
        safe_name "$c" && safe_num "$d" && safe_hash "$e" && safe_num "$f" && [ "$f" -ge 1 ] \
            || die 5 "manifest disc $b has an unsafe or malformed entry"
    else
        safe_name "$c" && safe_num "$d" && safe_hash "$e" || die 5 "manifest part $c is unsafe or malformed"
    fi
done < "$TMP/records"
[ $ndiscs -ge 1 ] || die 5 "manifest lists no discs"
for r in $ROLES; do
    grep -q "^D [0-9]* $r " "$TMP/records" || die 5 "role $r is not in this manifest"
done
wanted() { [ -z "$ROLES" ] && return 0; for r in $ROLES; do [ "$r" = "$1" ] && return 0; done; return 1; }
if [ "$SIGONLY" -eq 1 ]; then
    printf 'RESULT: OK (signature and manifest form; parts not checked)\n'
    exit 0
fi

# ---- 2. parts ------------------------------------------------------------
FIRST_FAIL=0
fail_once() { [ "$FIRST_FAIL" -ne 0 ] || FIRST_FAIL=$1; err "FAIL: $2"; }
nparts_ok=0
skip=1
while read -r kind a b c d e f; do
    if [ "$kind" = D ]; then
        if wanted "$b"; then skip=0; say "disc $b: $c, $d bytes, $f part(s)"; else skip=1; fi
        continue
    fi
    [ $skip -eq 0 ] || continue
    p=$PARTS/$c
    if [ ! -f "$p" ]; then fail_once 6 "part missing: $c"; continue; fi
    sz=$(size_of "$p")
    if [ "$sz" != "$d" ]; then fail_once 6 "part $c is $sz bytes, the manifest says $d"; continue; fi
    h=$(sha256_of "$p")
    if [ "$h" != "$e" ]; then fail_once 7 "part $c has SHA-256 $h, the manifest says $e"; continue; fi
    nparts_ok=$((nparts_ok + 1))
    say "  ok  $c"
done < "$TMP/records"
[ "$FIRST_FAIL" -eq 0 ] || die "$FIRST_FAIL" "one or more parts are bad; nothing was joined"
say "parts: all $nparts_ok good"

if [ "$NOJOIN" -eq 1 ]; then
    printf 'RESULT: OK (signature and %s parts; not joined)\n' "$nparts_ok"
    exit 0
fi

# ---- 3. join and check each disc -----------------------------------------
joined=0
while read -r kind a b c d e f; do
    [ "$kind" = D ] || continue
    wanted "$b" || continue
    target=$OUT/$c
    JOINING=$OUT/$c.partial
    : > "$JOINING" 2>/dev/null || die 9 "cannot write $JOINING"
    say "join $b -> $target"
    awk -v d="$a" '$1 == "P" && $2 == d { print $4 }' "$TMP/records" > "$TMP/names"
    while read -r name; do
        cat "$PARTS/$name" >> "$JOINING" 2>/dev/null || die 9 "write failed while joining $c (disk full?)"
    done < "$TMP/names"
    sz=$(size_of "$JOINING")
    [ "$sz" = "$d" ] || die 8 "joined $c is $sz bytes, the manifest says $d"
    h=$(sha256_of "$JOINING")
    [ "$h" = "$e" ] || die 8 "joined $c has SHA-256 $h, the manifest says $e"
    mv -f "$JOINING" "$target" 2>/dev/null || die 9 "could not rename the joined disc to $target"
    JOINING=
    joined=$((joined + 1))
    say "  ok  $c sha256 $h"
done < "$TMP/records"
printf 'RESULT: OK (signature, %s parts, %s disc(s) joined and checked)\n' "$nparts_ok" "$joined"
exit 0
