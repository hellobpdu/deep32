# STAGE 50-library: the Deep32 Library program (its first real compile: no C compiler on build-controller) and the Ask broker (ship/tools/stage.sh build)
# AS root (stage.sh drops to d32build for the compiles)
# IN /build/lanes/ship/src/d32library (tree library/, the old encarta/Makefile), node/asklink, TAXONOMY.json
# OUT /build/pobj/ship/library/d32library, /build/lanes/ship/build/asklink/{d32-chatd,d32-chatctl}, logs/library-{build,test}.log
run() {
	# the Library's Makefile turns each optional library off, silently, when its headers are missing (lab 2026-11-15:
	# e26-render built with no MuPDF, so no PNG, JPEG, GIF, SVG or PDF); disc 1's Library needs every one of them
	[ -f /usr/local/include/mupdf/fitz.h ] || die "MuPDF headers missing (provision-910 TOOLCHAIN mupdf): e26-render would have no pictures or PDF"
	for p in sqlite3 libzim libwebp libzstd; do
		pkg-config --exists $p || die "pkg-config finds no $p (provision-910 TOOLCHAIN): the Library would build without it"
	done
	mkdir -p /build/lanes/ship/logs /build/lanes/ship/work
	chown d32build:d32build /build/lanes/ship/logs /build/lanes/ship/work   # stage.sh writes its logs and work as d32build (lab 2026-11-15: Permission denied)
	# stdin from /dev/null: the tests start helpers that read it (test_lib's /bin/cat must see end of file and go; under
	# tmux it waited on the terminal and the bad-frame check timed out, lab 2026-11-15)
	ksh "$S/ship/tools/stage.sh" build </dev/null || { tail -40 /build/lanes/ship/logs/library-build.log 2>/dev/null; die "stage.sh build failed"; }
	cp -p /build/lanes/ship/logs/library-build.log /build/lanes/ship/logs/library-test.log "$D32_ROOT/logs/" 2>/dev/null || true
}
