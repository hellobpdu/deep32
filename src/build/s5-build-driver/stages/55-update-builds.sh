# STAGE 55-update-builds: update-era native programs, help volumes and pinned registries, u2 hardening libraries and the Ask window
# AS root (as_build runs the compiles and generators as d32build)
# IN tree ship/u10, ship/u14/apps, in/openbsd-mac-vendor-20260913.tgz, in/geo/dbip-country-lite-2026-11.csv.gz, ship/u2/hardening/build.sh, ask-client/window/Makefile
# OUT /build/dist/work/55-update-builds/{u10,native,u14src,u14apps,geo,u2hardening/out/lib,ask-client/window/build}

rc1_input() {
	# An absent outside input is allowed only for an explicitly incomplete build.
	if [ ! -f "$1" ]; then
		[ "${D32_ALLOW_MISSING:-0}" = 1 ] || die "missing RC1 input: $1 (INPUTS-RC1.md)"
		log "WARN: missing RC1 input: $1; D32_ALLOW_MISSING=1: its registry is omitted"
		return 1
	fi
	# A present but incorrect input always fails, including incomplete builds.
	check_sha "$1" "$2"
}

run() {
	W=$D32_ROOT/work/55-update-builds
	mv=$D32_ROOT/in/openbsd-mac-vendor-20260913.tgz
	geo_input=$D32_ROOT/in/geo/dbip-country-lite-2026-11.csv.gz
	need_file "$T/ship/u10/data/geo/SOURCES.tsv"
	geo_sha=$(awk -F '\t' '$1 == "dbip-country-lite" { print $3 }' "$T/ship/u10/data/geo/SOURCES.tsv")
	if rc1_input "$mv" 848081a59f3450f68fcb2706f6ed6b298c495e8dfd9eb1d03df4cec0cff3e4d1; then have_oui=1; else have_oui=0; fi  # sha256 or public signing fingerprint
	if rc1_input "$geo_input" "$geo_sha"; then have_geo=1; else have_geo=0; fi
	rm -rf "$W"; install -d -o d32build -g d32build "$W" "$W/native" "$W/u2hardening" "$W/u2hardening/src"
	# build-native.sh runs make beside the sources; stage 10's tree remains read-only.
	as_build "cp -R '$T/ship/u10' '$W/u10' && chmod -R u+w '$W/u10' && sh '$W/u10/bundle/build-native.sh' '$W/u10' '$W/native'" ||
		die "build-native.sh failed"
	# Separate targets in the laptop's Makefiles, omitted by build-native.sh.
	as_build "make -C '$W/u10/src/wsopen' all && make -C '$W/u10/src/oui' oui-build && make -C '$W/u10/src/capdlg' d32-capture && make -C '$W/u10/src/sam' help" ||
		die "RC1 native targets or Sam help failed"
	# desktop-res/dtcalc/ORIGIN.md records this compiler command; there is no Makefile.
	as_build "cd '$W/u10/src/desktop-res/dtcalc' && cc -std=c99 -Wall -Wextra -Werror -O2 -fstack-protector-strong -o d32-dtcalc dtcalc-launch.c" ||
		die "d32-dtcalc build failed"
	# Keep the producers' names. These modes match bundle/materialize-payload.cjs.
	as_build "install -m 0555 '$W/u10/src/wsopen/d32-wsopen' '$W/u10/src/wsopen/wireshark-run-core' '$W/u10/src/oui/oui-build' '$W/u10/src/capdlg/d32-capture' '$W/u10/src/desktop-res/dtcalc/d32-dtcalc' '$W/native/' && install -m 0444 '$W/u10/src/sam/help/D32Sam.sdl' '$W/native/'" ||
		die "RC1 native outputs could not be collected"
	if [ "$have_oui" = 1 ]; then
		# u14 build.sh also invokes make in u10. Give it the writable u10 and apps copies.
		as_build "mkdir -p '$W/u14src/ship/u14' && ln -s '$W/u10' '$W/u14src/ship/u10' && cp -R '$T/ship/u14/apps' '$W/u14src/ship/u14/apps' && chmod -R u+w '$W/u14src' && sh '$W/u14src/ship/u14/apps/build.sh' '$W/u14src' '$W/u14apps' '$mv'" ||
			die "u14 apps build.sh failed"
	fi
	if [ "$have_geo" = 1 ]; then
		as_build "python3.14 -I -B '$W/u10/src/regdata/build.py' geo --pins '$W/u10/data/geo/SOURCES.tsv' --sources '$D32_ROOT/in/geo' --out '$W/geo'" ||
			die "geo registry build failed"
		geo_gen=$(cat "$W/u10/data/geo/CURRENT")
		need_file "$W/geo/$geo_gen/MANIFEST"
		need_file "$W/geo/$geo_gen/registry.d32r"
		as_build "install -m 0444 '$W/u10/data/geo/CURRENT' '$W/u10/data/geo/NOTICE.md' '$W/u10/data/geo/SOURCES.tsv' '$W/geo/' && chmod 0444 '$W/geo/$geo_gen/MANIFEST' '$W/geo/$geo_gen/registry.d32r' && chmod 0755 '$W/geo' '$W/geo/$geo_gen'" ||
			die "geo registry metadata could not be collected"
	fi
	# Hash every collected RC1 artefact, including non-executable help and data.
	for name in d32-wsopen oui-build d32-dtcalc d32-capture wireshark-run-core D32Sam.sdl; do
		need_file "$W/native/$name"
		log "built: native/$name $(sha256 -q "$W/native/$name")"
	done
	for root in "$W/geo" "$W/u14apps/payload/usr/local/d32/share/data/oui"; do
		[ -d "$root" ] || continue
		find "$root" -type f | sort | while IFS= read -r f; do
			log "built: ${f#"$W"/} $(sha256 -q "$f")"
		done
	done
	# build.sh takes its sources from LANE/src/ship/u2.
	ln -s "$T/ship" "$W/u2hardening/src/ship"
	as_build "ksh '$T/ship/u2/hardening/build.sh' '$W/u2hardening'" || die "u2 hardening build.sh failed"
	# The Ask window's Makefile reaches ../../ship/u10/src.
	as_build "cp -R '$T/ask-client' '$W/ask-client' && mkdir -p '$W/ship' && cp -R '$T/ship/u10' '$W/ship/u10' && chmod -R u+w '$W/ask-client' '$W/ship' && cd '$W/ask-client/window' && make" ||
		die "the Ask window build failed"
	find "$W" -type f -perm -u+x ! -name '*.sh' ! -name '*.py' ! -name '*.js' | sort | while IFS= read -r f; do
		log "built: ${f#"$W"/} $(sha256 -q "$f")"
	done
}
