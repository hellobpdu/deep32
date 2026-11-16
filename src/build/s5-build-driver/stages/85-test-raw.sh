# STAGE 85-test-raw: the TEST medium as a raw image for Proxmox import (the fixed VHD minus its 512-byte footer; the VHD stays for the hyperv adapter test in 930)
# AS root
# IN /build/dist/out/<release>/test/deep32ws-<id>-amd64-test.vhd
# OUT /build/dist/out/<release>/test/d32dist-<id>-test.raw, test/SHA256.raw
run() {
	v=$OUT/test/deep32ws-$D32_RELEASE_ID-amd64-test.vhd
	need_file "$v"
	# this stage's own earlier output (a rerun after stage 80 made a new VHD): vhd.py never overwrites (lab 2026-11-15)
	rm -f "$OUT/test/d32dist-$D32_RELEASE_ID-test.raw" "$OUT/test/SHA256.raw"
	python3.14 -I "$D32_ROOT/test-lane/vhd.py" to-raw "$v" "$OUT/test/d32dist-$D32_RELEASE_ID-test.raw" || die "vhd.py to-raw failed"
	(cd "$OUT/test" && sha256 "d32dist-$D32_RELEASE_ID-test.raw" >SHA256.raw)
	cat "$OUT/test/SHA256.raw"
}
