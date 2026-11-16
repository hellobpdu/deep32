# STAGE 30-wheels: the native wheels of both shipped sets, offline (pyrag/build-wheels.sh all, JOBS for 12 cores)
# AS d32build
# IN /build/lanes/pyrag/src/{dist,vendor,abseil}, locks/requirements-buildenv{312,314}.lock, /usr/ports (8.0 patches), /usr/local/d32/py312
# OUT /build/lanes/pyrag/wheelhouse/{cp312,cp314}, wheelhouse/SHA256SUMS, /build/lanes/pyrag/logs/<step>.log
run() {
	as_build "cd /build/lanes/pyrag && JOBS=$D32_JOBS sh build-wheels.sh all" || die "build-wheels.sh failed"
}
