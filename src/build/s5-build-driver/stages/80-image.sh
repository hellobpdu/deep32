# STAGE 80-image: the disc 1 medium and its TEST twin (ship/tools/mkimage-ship.sh; the TEST medium's target disk is D32_TEST_ROOTDISK: sd1 on Proxmox, wd1 on Hyper-V)
# AS root
# IN /build/lanes/base/in/rel (8.0 sets), /build/pkgrepo, /build/dist/in/firmware/8.0, /build/lanes/ship/media/d32 (signed packs), the site set sources, the keys
# OUT /build/lanes/ship/out/<release>/{deep32ws-<id>-amd64.img,test/deep32ws-<id>-amd64-test.vhd,SHA256,SHA256.sig,SIZES.txt}, copied to /build/dist/out/<release>/
run() {
	if tree_names_old_key "$S/ship/tools/mkimage-ship.sh"; then
		die "the tree's mkimage-ship.sh still signs with the old test key's path: apply the lead-slot transform 990 (LAB-RUN.md)"
	fi
	grep -q 'D32_TEST_ROOTDISK' "$S/ship/tools/mkimage-ship.sh" || die "mkimage-ship.sh takes no D32_TEST_ROOTDISK (transform 990 missing)"
	mkdir -p /build/lanes/ship/work /build/lanes/ship/pkgs /build/lanes/ship/keys
	chmod 0700 /build/lanes/ship/keys
	# pkgclosure.sh takes the closure from the packages INSTALLED here, so every root of ship/install/pkg-roots must be
	# installed on the build VM first (lab 2026-11-15: "root kiwix-tools is not installed on this VM"); from the
	# signed /build/pkgrepo only (pkg_add checks each signature), roots already installed are left as they are
	roots=$(grep -v '^#' "$S/ship/install/pkg-roots" | sed '/^$/d' | tr '\n' ' ')
	PKG_PATH=/build/pkgrepo/ pkg_add -I $roots >"$D32_ROOT/logs/80-image-roots.log" 2>&1 ||
		{ tail -20 "$D32_ROOT/logs/80-image-roots.log"; die "pkg_add of the image's package roots failed"; }
	log "package roots installed on the build VM: $roots"
	FW=$D32_ROOT/in/firmware/8.0 ksh "$S/ship/tools/mkimage-ship.sh" "$D32_RELEASE_ID" || die "mkimage-ship.sh failed"
	mkdir -p "$OUT"
	cp -Rp "/build/lanes/ship/out/$D32_RELEASE_ID/." "$OUT/"
	log "image: $(ls -l "$OUT"/*.img | awk '{ print $NF, $5 }')"
}
