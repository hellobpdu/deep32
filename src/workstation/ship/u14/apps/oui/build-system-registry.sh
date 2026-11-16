#!/bin/sh
# Build the preinstalled OUI Lookup registry for /usr/local/d32/share/data/oui (D.32.12u14) from the pinned,
# signed OpenBSD 8.0 package mac-vendor-20260913.tgz. Same pins, members and oui-build call as the u10
# d32oui-provision helper; only the output differs: one read-only system generation plus CURRENT, which
# d32oui and d32ouiq read first (ouidb.c DATA_ROOT) before the per-user ~/.dt/d32/oui.
# Usage (OpenBSD, ordinary user): sh build-system-registry.sh /path/to/oui-build PACKAGE.tgz OUTDIR
set -efu
PATH=/bin:/usr/bin
LC_ALL=C
export PATH LC_ALL
umask 022

die() {
	printf 'build-system-registry: %s\n' "$1" >&2
	exit 1
}

[ "$#" -eq 3 ] || die 'usage: build-system-registry.sh OUI_BUILD PACKAGE OUTDIR'
builder=$1 package=$2 out=$3
for path in "$builder" "$package" "$out"; do
	case $path in /*) ;; *) die "use absolute paths: $path" ;; esac
done
[ -f "$builder" ] && [ -x "$builder" ] || die 'oui-build is not executable'
[ -f "$package" ] && [ ! -L "$package" ] || die 'expected a regular package file'
[ ! -e "$out" ] || die "output exists: $out"
[ "$(stat -f '%z' "$package")" = 3472949 ] || die 'unexpected package size'
[ "$(sha256 -q "$package")" = 848081a59f3450f68fcb2706f6ed6b298c495e8dfd9eb1d03df4cec0cff3e4d1 ] ||
	die 'package SHA-256 mismatch'
/usr/bin/signify -Vz -p /etc/signify/openbsd-80-pkg.pub -x "$package" >/dev/null ||
	die 'OpenBSD package signature verification failed'

scratch=$(mktemp -d "${TMPDIR:-/tmp}/d32oui-system.XXXXXXXXXX") || die 'cannot create scratch'
trap 'rm -rf "$scratch"' EXIT
trap 'exit 1' HUP INT TERM
cd "$scratch"

mal=4776040c2898b14c8b343462b4c470a1cc58c03a2e7122393c5fc0c6ca68d68e # SHA-256
mam=8b081f35c85b47c2460f5b0c852cf18dacd88cab05ebd9785e99a75f958cd231 # SHA-256
mas=5c03903614b1c99b43e4fe67a1034bfed7d0b8b02bd4f055c4cc0ead90138cb0 # SHA-256
iab=6e71aa3d47f00f19d09cb3b31ce1038de1834703420f0ce4ce111da586f1a533 # SHA-256
for pair in ma-l:$mal ma-m:$mam ma-s:$mas iab:$iab; do
	member=share/mac-vendor/${pair%%:*}.csv
	tar -xzf "$package" -C "$scratch" "$member" || die "cannot extract $member"
	[ -f "$scratch/$member" ] && [ ! -L "$scratch/$member" ] || die "not a regular file: $member"
	[ "$(sha256 -q "$scratch/$member")" = "${pair#*:}" ] || die "SHA-256 mismatch: $member"
done

gen=openbsd-mac-vendor-20260913
date=2026-09-13
url=https://openbsd.c3sl.ufpr.br/pub/OpenBSD/8.0/packages/amd64/mac-vendor-20260913.tgz
mkdir -m 755 "$out" "$out/$gen" || die "cannot create $out"
"$builder" "$out/$gen" "$gen" "$date" "$url" operator-local-use-only \
	ma-l "$date" "$mal" "$scratch/share/mac-vendor/ma-l.csv" \
	ma-m "$date" "$mam" "$scratch/share/mac-vendor/ma-m.csv" \
	ma-s "$date" "$mas" "$scratch/share/mac-vendor/ma-s.csv" \
	iab "$date" "$iab" "$scratch/share/mac-vendor/iab.csv" || die 'oui-build failed'
image_sha=$(sha256 -q "$out/$gen/oui.d32i")
grep -Fxq 'schema=2' "$out/$gen/MANIFEST" &&
	grep -Fxq 'converter_rev=oui-build-2' "$out/$gen/MANIFEST" &&
	grep -Fxq "gen=$gen" "$out/$gen/MANIFEST" &&
	grep -Fxq "output_sha256=$image_sha" "$out/$gen/MANIFEST" || die 'unexpected builder manifest'
printf '%s\n' "$gen" >"$out/CURRENT"
chmod 0444 "$out/CURRENT" "$out/$gen/MANIFEST" "$out/$gen/oui.d32i"
chmod 0755 "$out" "$out/$gen"
printf 'build-system-registry: %s/%s ready (%s)\n' "$out" "$gen" "$image_sha"
