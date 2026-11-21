# STAGE 60-stage: the system pack trees d32-node and d32-apps (ship/tools/stage.sh node, then apps)
# AS root
# IN /usr/local/d32 (stages 20-45), /build/pobj/ship/library/d32library, the shims' rc.d and classes, the art and theme outputs, the firefox-esr package's unveil files
# OUT /build/lanes/ship/stage/d32-node, /build/lanes/ship/stage/d32-apps
run() {
	mkdir -p /build/lanes/ship/libsrc
	need_file "$T/model-bridge/d32_model_bridge.py"
	D32_MODEL_BRIDGE_SOURCE="$T/model-bridge/d32_model_bridge.py" \
		ksh "$S/ship/tools/stage.sh" node || die "stage.sh node failed"
	ksh "$S/ship/tools/stage.sh" apps || die "stage.sh apps failed"
	# build-time diagnostic staging failure below.
	python3.14 -I "$D32_ROOT/stages/alpha2_stage.py" install --tree "$T" \
		--stage /build/lanes/ship/stage || die "alpha.2 diagnostics staging failed"
}
