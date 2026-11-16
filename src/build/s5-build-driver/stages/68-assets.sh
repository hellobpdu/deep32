# STAGE 68-assets: the asset store (ledger v2) laid into d32-apps, every file's sha256 checked again here; a file without a ledger row stops the build
# AS root
# IN /build/lanes/ship/src/assets/{root,SHA256SUMS,LEDGER-v2.tsv} (only when the tree carries the asset store: v1 and RC1)
# OUT /build/lanes/ship/stage/d32-apps/<installed paths>
run() {
	A=$S/assets
	if [ ! -d "$A" ]; then log "no asset store in this tree (v0): nothing to lay in"; return 0; fi
	[ "${D32_ASSETS_OVERLAY:-1}" = 1 ] || { log "D32_ASSETS_OVERLAY=0: a 960-979 transform installs the assets itself"; return 0; }
	(cd "$A/root" && sed 's/^\([0-9a-f]*\)  \(.*\)$/SHA256 (\2) = \1/' ../SHA256SUMS | sha256 -c -q) ||
		die "assets: a file differs from SHA256SUMS"
	n_sum=$(grep -c . "$A/SHA256SUMS"); n_root=$(cd "$A/root" && find . -type f | wc -l | tr -d ' ')
	[ "$n_sum" = "$n_root" ] || die "assets: $n_root files under root/, SHA256SUMS lists $n_sum: a file without a ledger row"
	D=/build/lanes/ship/stage/d32-apps
	(cd "$A/root" && find . -type f | sed 's|^\./||' | sort) | while read -r p; do
		mkdir -p "$D/$(dirname "$p")"
		install -o root -g wheel -m 0444 "$A/root/$p" "$D/$p"
	done
	log "assets: $n_root files laid into d32-apps"
}
