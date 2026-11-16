# STAGE 90-provenance: the provenance record: tree hash, every input's hash, tools, the public keys, stage logs, outputs
# AS root
# IN everything above
# OUT /build/dist/out/<release>/PROVENANCE.txt and PROVENANCE.txt.sig (the key that signed the packs)
run() {
	P=$OUT/PROVENANCE.txt
	mkdir -p "$OUT"
	{
		echo "release $D32_RELEASE_ID"
		echo "keyset $D32_KEYSET"
		echo "tree_hash $D32_TREE_HASH"
		echo "source_date_epoch $SOURCE_DATE_EPOCH"
		echo "host $(hostname -s) $(uname -srm)"
		echo "kernel $(sysctl -n kern.version | head -1)"
		echo "cc $(cc --version | head -1)"
		echo "python3.14 $(python3.14 -V 2>&1)"
		echo "release_pub_sha256 $(sha256 -q "$D32_RELEASE_PUB")"
		(cd "$D32_ROOT/in" && sha256 -r PIN.sha256 lists/* obsd80/SHA256.sig TAXONOMY.json) | sed 's/^/in /'
		(cd /build/lanes/pyrag && sha256 -r locks/SOURCES-SHA256SUMS locks/requirements-core.lock locks/requirements-edge.lock) | sed 's/^/pyrag /'
		(cd /build/pkgrepo && find . -type f -name '*.tgz' | sort | xargs sha256 -r) | sed 's/^/package /'
		(cd "$D32_ROOT/logs" && find . -type f | sort | xargs sha256 -r) | sed 's/^/log /'
		(cd "$OUT" && find . -type f ! -name 'PROVENANCE.txt*' | sort | xargs sha256 -r) | sed 's/^/out /'
	} >"$P"
	signify -S -e -s "$D32_RELEASE_SEC" -m "$P" -x "$P.sig" || die "signify -S failed"
	log "provenance: $(grep -c . "$P") lines, sha256 $(sha256 -q "$P")"
}
