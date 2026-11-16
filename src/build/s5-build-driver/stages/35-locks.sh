# STAGE 35-locks: the install locks regenerated with the new wheels' hashes (pyrag/README.md step 4: wheels are not bit-reproducible)
# AS d32build
# IN /build/lanes/pyrag/locks/{core,edge}.pins, wheelhouse/cp312, wheelhouse/cp314, src/dist
# OUT /build/lanes/pyrag/locks/requirements-{core,edge}.lock; copies with wheelhouse/SHA256SUMS in /build/dist/out/<release>/locks
run() {
	as_build "cd /build/lanes/pyrag && python3.14 -I tools/mklock.py locks/core.pins cp312 wheelhouse/cp312 src/dist >locks/requirements-core.lock.new && python3.14 -I tools/mklock.py locks/edge.pins cp314 wheelhouse/cp314 src/dist >locks/requirements-edge.lock.new && mv locks/requirements-core.lock.new locks/requirements-core.lock && mv locks/requirements-edge.lock.new locks/requirements-edge.lock" ||
		die "mklock.py failed"
	mkdir -p "$OUT/locks"
	cp -p /build/lanes/pyrag/locks/requirements-core.lock /build/lanes/pyrag/locks/requirements-edge.lock \
		/build/lanes/pyrag/wheelhouse/SHA256SUMS "$OUT/locks/"
}
