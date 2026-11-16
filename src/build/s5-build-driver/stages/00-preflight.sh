# STAGE 00-preflight: the host, the configuration and the build tools, before anything is written
# AS root
# IN /build/dist/build.conf; the host name; OpenBSD 8.0 base and comp80; the build packages of pyrag/README.md
# OUT /build/dist/logs/00-preflight.log (facts only)
run() {
	old_vm_guard
	host_ok || die "not $D32_HOST"
	[ "$(uname -r)" = 8.0 ] || die "OpenBSD $(uname -r), expected 8.0"
	[ "$(uname -m)" = amd64 ] || die "$(uname -m), expected amd64"
	for t in cc c++ gmake python3.14 rustc cargo cmake ninja meson git signify makefs rdsetroot pax sha256 llvm-objdump; do
		command -v "$t" >/dev/null 2>&1 || die "missing build tool: $t (pkg_add from the 8.0 mirror, signed)"
	done
	id d32build >/dev/null 2>&1 || die "no build user d32build (obsd-910-bringup provisions it)"
	# stage.sh s_apps builds /etc/firefox-esr/unveil.{main,content} from the installed package's own files
	for u in main content; do
		[ -f /usr/local/lib/firefox-esr/browser/defaults/preferences/unveil.$u ] ||
			die "firefox-esr is not installed on this host (stage.sh s_apps reads its unveil.$u)"
	done
	for k in "$D32_RELEASE_SEC" "$D32_RELEASE_PUB" "$D32_LIBRARY_SEC" "$D32_LIBRARY_PUB"; do
		[ -f "$k" ] || die "missing key file $k (LAB-RUN.md: the $D32_KEYSET keys are made inside 910)"
	done
	case $D32_RELEASE_SEC in "$D32_ROOT"/keys/*) ;; *) die "D32_RELEASE_SEC must live under $D32_ROOT/keys" ;; esac
	log "cpus $(sysctl -n hw.ncpuonline), memory $(( $(sysctl -n hw.physmem) / 1048576 )) MiB, jobs $D32_JOBS"
	log "cc: $(cc --version | head -1); python3.14: $(python3.14 -V 2>&1)"
	log "free on /build: $(df -h /build | awk 'NR == 2 { print $4 }')"
	log "keys ($D32_KEYSET, public halves): release $(sha256 -q "$D32_RELEASE_PUB") library $(sha256 -q "$D32_LIBRARY_PUB")"
}
