# STAGE 10-tree: the pinned build tree unpacked and checked; the old pipeline's folders filled from it and the inputs
# AS root
# IN /build/dist/in/tree-<D32_TREE_HASH>.tar, in/PIN.sha256, in/lists/{SOURCE-MAP,CUTS,ADDITIONS,CHANGED,BASELINE-MODES,TREE-RENAMES}.tsv with in/lists/SHA256, in/pyrag/ (copy of D:/deep32ws/artefacts/pyrag), in/obsd80/ (8.0/amd64 sets + SHA256.sig), in/ports/{8.0,7.8/lang/python/3/patches}, in/pkgrepo/, in/firmware/8.0/, in/TAXONOMY.json
# OUT /build/dist/tree/<D32_TREE_HASH> (read-only); /build/lanes/{ship/src,pyrag,nodegen/src,identity/src/d32log,shims/tree,base/in/rel}; /build/src/{pyrag,ports-7.8}; /build/pkgrepo
run() {
	I=$D32_ROOT/in
	need_file "$I/tree-$D32_TREE_HASH.tar"; need_file "$I/PIN.sha256"; need_file "$I/TAXONOMY.json"
	need_dir "$I/pyrag/src"; need_dir "$I/pyrag/dist"; need_dir "$I/obsd80"; need_dir "$I/pkgrepo"
	need_file "$I/lists/SHA256"
	(cd "$I/lists" && sha256 -c SHA256) || die "in/lists: hash check failed"
	# the public taxonomy (taxonomy/make-public-taxonomy.py from the 20-category 174f87bc the program, its tests and the
	# category art were made for; lead 2026-11-15: G1 found 126 findings in the laptop's copies). His choice of 20 or
	# 21 categories is open (build-state.md); 6f53ebf8 (u9, 21) was the v0 pin.
	check_sha "$I/TAXONOMY.json" 5117d6b603fd2e60f961f1a0fdbdba52762c91dfc7c638fc953ed433d485ac91  # sha256 or public signing fingerprint
	# the tree hash is the sha256 of the pin file itself (apply-transforms.py writes the export tool's lines, sorted)
	th=$(sha256 -q "$I/PIN.sha256")
	[ "$th" = "$D32_TREE_HASH" ] || die "PIN.sha256 hashes to $th, build.conf says $D32_TREE_HASH"
	rm -rf "$T.new"; mkdir -p "$T.new"
	tar -xf "$I/tree-$D32_TREE_HASH.tar" -C "$T.new"
	(cd "$T.new" && sed 's/^\([0-9a-f]*\)  \(.*\)$/SHA256 (\2) = \1/' "$I/PIN.sha256" | sha256 -c -q) ||
		die "tree: a file differs from PIN.sha256"
	n_pin=$(grep -c . "$I/PIN.sha256"); n_tree=$(cd "$T.new" && find . -type f | wc -l | tr -d ' ')
	[ "$n_pin" = "$n_tree" ] || die "tree: $n_tree files, the pin lists $n_pin"
	rm -rf "$T"; mv "$T.new" "$T"; chmod -R a-w "$T"
	log "tree $D32_TREE_HASH: $n_tree files, every hash as pinned"
	# the ship lane's source folder, laid out as push-to-vm.sh did (names after transform 603)
	rm -rf "$S"; mkdir -p "$S/node" "$S/tests" "$S/art/library-categories" "$S/theme/out/all"
	for d in ship distrib release; do cp -Rp "$T/$d" "$S/$d"; done
	cp -Rp "$T/tests/base" "$S/tests/base"
	cp -Rp "$T/library" "$S/d32library"
	cp -Rp "$T/node/shims" "$T/node/asklink" "$S/node/"
	cp -p "$T/node/exact_local.py" "$T/node/d32vec.c" "$S/node/"
	install -m 0444 "$I/TAXONOMY.json" "$S/TAXONOMY.json"
	[ -d "$T/art/out" ] && [ -d "$T/art/library-categories/out" ] && [ -f "$T/theme/out/all/root/MANIFEST.tsv" ] ||
		die "the tree has no generated art or theme: make it with apply-transforms.py --generate"
	cp -Rp "$T/art/out" "$S/art/out"
	cp -Rp "$T/art/library-categories/out" "$S/art/library-categories/out"
	cp -Rp "$T/theme/out/all/root" "$S/theme/out/all/root"
	[ ! -d "$T/assets" ] || cp -Rp "$T/assets" "$S/assets"
	chmod -R u+w "$S"; chown -R d32build:d32build "$S"
	# pyrag, nodegen, identity (d32log), shims: where their own scripts look
	rm -rf /build/lanes/pyrag /build/lanes/nodegen /build/lanes/identity /build/lanes/shims
	mkdir -p /build/lanes/pyrag/src /build/lanes/nodegen/src /build/lanes/identity/src /build/lanes/shims /build/src
	cp -Rp "$T/pyrag/." /build/lanes/pyrag/
	for d in dist vendor abseil code; do [ ! -d "$I/pyrag/$d" ] || cp -Rp "$I/pyrag/$d" "/build/lanes/pyrag/src/$d"; done
	(cd /build/lanes/pyrag/src/dist && sed 's/^\([0-9a-f]*\)  \(.*\)$/SHA256 (\2) = \1/' ../../locks/SOURCES-SHA256SUMS | sha256 -c -q) ||
		die "pyrag sources differ from locks/SOURCES-SHA256SUMS"
	rm -rf /build/src/pyrag; cp -Rp "$I/pyrag/src" /build/src/pyrag
	check_sha /build/src/pyrag/Python-3.12.14.tar.xz 5c8462af5790baf43a321a1559dbe0db06d1be4300fb85fb53c40060668e548a  # sha256 or public signing fingerprint
	check_sha /build/src/pyrag/sqlite-amalgamation-3530100.zip 36ad6e7f38540a3b21a2ac36340833f0a9e426bc1c752751c3ba669466827eae  # sha256 or public signing fingerprint
	need_dir "$I/ports/7.8/lang/python/3/patches"
	rm -rf /build/src/ports-7.8; mkdir -p /build/src/ports-7.8/ports/lang/python/3
	cp -Rp "$I/ports/7.8/lang/python/3/patches" /build/src/ports-7.8/ports/lang/python/3/patches
	if [ ! -d /usr/ports/net/py-grpcio ]; then need_dir "$I/ports/8.0"; rm -rf /usr/ports; cp -Rp "$I/ports/8.0" /usr/ports; fi
	cp -Rp "$T/node/." /build/lanes/nodegen/src/
	cp -Rp "$T/pyrag/d32log" /build/lanes/identity/src/d32log
	# d32log's other inputs (build-d32log.sh: AOR and GLIBC defaults under identity/src), pushed as in/identity from
	# D:/deep32ws/artefacts/identity: the pinned ARM optimized-routines tarball and glibc 2.43's log files, each
	# checked against SOURCES-SHA256SUMS (GNU form 'hex *path'). Lab 2026-11-15: without them stage 20 stopped at
	# "'log.c' file not found", because the rm -rf above also removed what provision-910 had placed.
	need_file "$I/identity/SOURCES-SHA256SUMS"
	check_sha "$I/identity/aor/optimized-routines-4be260a5.tar.gz" fa645b8c252ba7cfebb6a8b59b643ce726130115df31a62376e4aedd7ec33d96  # sha256 or public signing fingerprint
	while read -r h f; do
		[ -n "$h" ] || continue
		check_sha "$I/identity/${f#\*}" "$h"
	done < "$I/identity/SOURCES-SHA256SUMS"
	mkdir -p /build/lanes/identity/src/aor
	tar -C /build/lanes/identity/src/aor -xzf "$I/identity/aor/optimized-routines-4be260a5.tar.gz"
	[ -f /build/lanes/identity/src/aor/optimized-routines-4be260a5117480382690c6d8c300bc784e927d76/math/log.c ] ||  # sha256 or public signing fingerprint
		die "the optimized-routines tree has no math/log.c"
	cp -Rp "$I/identity/glibc-2.43" /build/lanes/identity/src/glibc-2.43
	cp -Rp "$T/node/shims" /build/lanes/shims/tree
	chmod -R u+w /build/lanes/pyrag /build/lanes/nodegen /build/lanes/identity /build/lanes/shims
	chown -R d32build:d32build /build/lanes/pyrag /build/lanes/nodegen /build/lanes/identity
	# OpenBSD 8.0's sets: checked against their signed list here, and again by mkimage-ship.sh
	mkdir -p /build/lanes/base/in; rm -rf /build/lanes/base/in/rel; cp -Rp "$I/obsd80" /build/lanes/base/in/rel
	(cd /build/lanes/base/in/rel && signify -C -p /etc/signify/openbsd-80-base.pub -x SHA256.sig bsd.rd base80.tgz) ||
		die "OpenBSD sets do not verify"
	rm -rf /build/pkgrepo; cp -Rp "$I/pkgrepo" /build/pkgrepo
	log "lanes filled: ship/src $(find "$S" -type f | wc -l | tr -d ' ') files; pyrag sources checked; sets verified"
}
