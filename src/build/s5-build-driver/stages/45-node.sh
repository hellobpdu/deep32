# STAGE 45-node: libd32vec.so with its FMA/AVX refusal, exact search, the shims (node/build.sh, node/shims/install.sh)
# AS d32build (node/build.sh), root (shims)
# IN /build/lanes/nodegen/src (= tree node/), /build/lanes/shims/tree, /usr/local/d32/venv/deep32-engine (deep32-engine before transform 807)
# OUT /usr/local/d32/lib/libd32vec.so, /usr/local/d32/node/*.py and __pycache__, /usr/local/d32/sbin, /usr/local/d32/node/shims
run() {
	o=$D32_ROOT/work/45-node.out
	mkdir -p "$D32_ROOT/work"
	as_build "sh /build/lanes/nodegen/src/build.sh" >"$o" 2>&1 || { cat "$o"; die "node/build.sh failed"; }
	cat "$o"
	grep -q '^FMA/AVX instructions in libd32vec.so: 0$' "$o" || die "node/build.sh did not report 0 FMA/AVX instructions"
	need_file "$T/model-bridge/d32_model_bridge.py"
	D32_MODEL_BRIDGE_SOURCE="$T/model-bridge/d32_model_bridge.py" \
		sh /build/lanes/shims/tree/install.sh || die "node/shims/install.sh failed"
}
