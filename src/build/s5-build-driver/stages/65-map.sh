# STAGE 65-map: every program row the golden list expects on disc 1 looked for in the staged packs; the rows later updates delivered copied in from the tree or the stage 55 builds (stage_map.py)
# AS root
# IN /build/dist/in/lists/{SOURCE-MAP,CUTS,ADDITIONS,BASELINE-MODES,TREE-RENAMES}.tsv, the tree, /build/lanes/ship/stage/*, /build/dist/work/55-update-builds
# OUT /build/lanes/ship/stage/d32-node and d32-apps completed; the site rows' final versions in $S/{ship,distrib}/site; /build/dist/out/<release>/STAGE-MAP.tsv
run() {
	mkdir -p "$OUT"
	set -- --tree "$T" --stage /build/lanes/ship/stage --builds "$D32_ROOT/work/55-update-builds" \
		--builds /build/pobj/ship/library --lists "$D32_ROOT/in/lists" --report "$OUT/STAGE-MAP.tsv"
	[ "${D32_ALLOW_MISSING:-0}" != 1 ] || set -- "$@" --allow-missing
	# rows the asset store brings (stage 68 lays them in after this stage) are not missing
	[ ! -f "$S/assets/SHA256SUMS" ] || set -- "$@" --assets "$S/assets/SHA256SUMS"
	# rows delivered outside a pack that the site set carries: their final version goes into the lane's site sources
	# (mksite-ship.sh builds site80 from $S in stage 80), not into d32-apps (lab 2026-11-15: doas.conf, pf.conf)
	set -- "$@" --site-src "$S"
	python3.14 -I "$D32_ROOT/stages/stage_map.py" "$@" || die "stage_map.py: rows missing (see $OUT/STAGE-MAP.tsv)"
}
