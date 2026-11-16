# STAGE 88-scan-parts: the medium's own files copied out of the image's FFS partition, read-only, for gates G1 and G2 (release-checks/run_gates.py reads archives but not FFS)
# AS root
# IN /build/dist/out/<release>/deep32ws-<id>-amd64.img
# OUT /build/dist/out/<release>/scan/{site80.tgz,d32/,README.txt,bsd,boot.conf,SHA256} (OpenBSD's own sets and packages left out: they verify against OpenBSD's signed lists)
run() {
	img=$OUT/deep32ws-$D32_RELEASE_ID-amd64.img
	need_file "$img"
	m=$D32_ROOT/work/88-mnt
	rm -rf "$OUT/scan"; mkdir -p "$OUT/scan" "$m"
	V=$(vnconfig "$img") || die "vnconfig failed"
	trap 'umount "$m" 2>/dev/null; vnconfig -u "$V" 2>/dev/null' EXIT
	mount -r "/dev/${V}a" "$m" || die "mount of the medium's FFS failed"
	cp -p "$m/8.0/amd64/site80.tgz" "$m/README.txt" "$m/bsd" "$OUT/scan/"
	cp -p "$m/etc/boot.conf" "$OUT/scan/boot.conf"
	cp -Rp "$m/d32" "$OUT/scan/d32"
	umount "$m"; vnconfig -u "$V"; trap - EXIT
	(cd "$OUT/scan" && find . -type f ! -name SHA256 | sort | xargs sha256 -r >SHA256)
	log "scan parts: $(grep -c . "$OUT/scan/SHA256") files in $OUT/scan"
}
