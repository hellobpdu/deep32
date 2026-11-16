# STAGE 60-stage: the system pack trees d32-node and d32-apps (ship/tools/stage.sh node, then apps)
# AS root
# IN /usr/local/d32 (stages 20-45), /build/pobj/ship/library/d32library, the shims' rc.d and classes, the art and theme outputs, the firefox-esr package's unveil files
# OUT /build/lanes/ship/stage/d32-node, /build/lanes/ship/stage/d32-apps
run() {
	mkdir -p /build/lanes/ship/libsrc
	ksh "$S/ship/tools/stage.sh" node || die "stage.sh node failed"
	ksh "$S/ship/tools/stage.sh" apps || die "stage.sh apps failed"
}
