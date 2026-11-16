# STAGE 40-venvs: code trees, isolated service venvs, stand-ins, launchers; compiled Python made here, never copied (pyrag/make-venvs.sh)
# AS d32build
# IN /build/lanes/pyrag/src/code/: the archives the tree's make-venvs.sh names (after transform 807: deep32-engine-<id>.tar and deep32-gateway-<id>.tar, the public engine; before it: deep32-engine-cb9ecd7.tar and deep32-gateway-bf79918.tar), pinned by build.conf when given; wheelhouse, locks
# OUT /usr/local/d32/venv/{deep32-engine,deep32-gateway} (before 807: {deep32-engine,deep32-gateway}), the code trees under /usr/local/d32, syspick links, every __pycache__
run() {
	# A rebuild on the same VM: stages 40 and 45 remake everything under /usr/local/d32 except stage 20's py312 and
	# sqlite, and stage 60 packs the whole folder (stage.sh: cp -Rp /usr/local/d32). A previous build's leftovers must
	# not reach the pack (lab 2026-11-15, v1 after v0: v0's deep32-engine/deep32-gateway trees and venvs, and stage 45's
	# root-owned shims that the build user could not remove)
	for e in /usr/local/d32/* /usr/local/d32/.[!.]*; do
		[ -e "$e" ] || continue
		case "${e##*/}" in py312|sqlite) ;; *) rm -rf "$e" ;; esac
	done
	C=/build/lanes/pyrag/src/code
	mv=/build/lanes/pyrag/make-venvs.sh
	if grep -q '^ENGINE_ID=' "$mv"; then
		# transform 807 (lead 2026-11-15): the public engine; the pins are D32_ENGINE_TAR_SHA256 and D32_GATEWAY_TAR_SHA256
		eid=$(sed -n 's/^ENGINE_ID=//p' "$mv"); gid=$(sed -n 's/^GATEWAY_ID=//p' "$mv")
		need_file "$C/deep32-engine-$eid.tar"; need_file "$C/deep32-gateway-$gid.tar"
		[ -z "${D32_ENGINE_TAR_SHA256:-}" ] || check_sha "$C/deep32-engine-$eid.tar" "$D32_ENGINE_TAR_SHA256"
		[ -z "${D32_GATEWAY_TAR_SHA256:-}" ] || check_sha "$C/deep32-gateway-$gid.tar" "$D32_GATEWAY_TAR_SHA256"
		log "engine $eid, gateway $gid (public archives)"
	else
		[ -z "${D32_CORE_TAR_SHA256:-}" ] || check_sha "$C/deep32-engine-cb9ecd7.tar" "$D32_CORE_TAR_SHA256"
		[ -z "${D32_EDGE_TAR_SHA256:-}" ] || check_sha "$C/deep32-gateway-bf79918.tar" "$D32_EDGE_TAR_SHA256"
	fi
	as_build "sh $mv" || die "make-venvs.sh failed"
	log "compiled Python files made here: $(find /usr/local/d32 -name '*.pyc' | wc -l | tr -d ' ')"
}
