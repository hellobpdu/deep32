# /etc/d32/ship-lib.sh: helpers for the ship lane's site hooks (sourced after site-lib.sh). First light PREVIEW.
# The install medium is mounted read-only at /mnt (inside install.sub's chroot) from 21-ship-media to 69-ship-library.
M=/mnt/d32
set -o pipefail
ship_mounted() { [ -f /mnt/d32/PACKS.tsv ]; }   # (inside the chroot, "mount" also lists the target's own /mnt)
# pack_check <name>: every file of an installed pack against its MANIFEST (sha256 -c over the whole list at once)
pack_check() {
	local m=/var/db/d32/packs/$1/MANIFEST.tsv n
	[ -f $m ] || die "pack $1: no manifest at $m"
	awk -F'\t' '$2 == "f" { printf "SHA256 (/%s) = %s\n", $1, $7 }' $m >/tmp/d32-check.$$
	n=$(wc -l </tmp/d32-check.$$ | tr -d ' ')
	if ! sha256 -c -q /tmp/d32-check.$$ >/tmp/d32-check.$$.out 2>&1; then
		head -5 /tmp/d32-check.$$.out >&2
		rm -f /tmp/d32-check.$$ /tmp/d32-check.$$.out
		die "pack $1: installed files differ from its MANIFEST"
	fi
	rm -f /tmp/d32-check.$$ /tmp/d32-check.$$.out
	d32 "item pack $1: $n files match its MANIFEST"
}
# pack_file <name>: the medium path of a pack, from PACKS.tsv (name, file, class, bytes, sha256, rights)
pack_file() { awk -F'\t' -v n="$1" '$1 == n { print $2 }' $M/PACKS.tsv; }
pack_names() { awk -F'\t' -v c="$1" '$3 == c { print $1 }' $M/PACKS.tsv; }
