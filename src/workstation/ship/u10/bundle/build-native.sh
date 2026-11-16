#!/bin/sh
# Build the completed app lanes from a clean OpenBSD scratch checkout.
set -eu
export LC_ALL=C
export PATH=/bin:/usr/bin:/sbin:/usr/sbin:/usr/X11R6/bin:/usr/local/bin:/usr/local/dt/bin

if [ "$#" -ne 2 ] || [ "$(uname -s)" != OpenBSD ]; then
	printf 'usage: sh build-native.sh scratch-u10-directory output-directory (OpenBSD)\n' >&2
	exit 64
fi

src=$(cd "$1/src" && pwd -P)
out=$(cd "$2" && pwd -P)
if [ "$src" = "$out" ] || [ -n "$(ls -A "$out")" ]; then
	printf 'A fresh output directory is required.\n' >&2
	exit 64
fi

build()
{
	dir=$1
	target=$2
	binary=$3
	printf '\n== %s ==\n' "$binary"
	make -C "$src/$dir" "$target"
	install -m 0555 "$src/$dir/$binary" "$out/$binary"
	ldd "$out/$binary"
	(cd "$out" && sha256 "$binary") >> "$out/.binary-checksums"
}

build netcalc all d32calc
build oui d32ouiq d32ouiq
build oui/gui all d32oui
build capdlg all d32capdlg
build sound/dlg all d32sound
build sound/sndd all d32sndd
build sound/mix all d32mix
build screencap all d32-screencap
if [ -f "$src/sam/Makefile" ]; then
	build sam all sam
fi

make -C "$src/netcalc" help
make -C "$src/oui/gui" help
install -m 0444 "$src/netcalc/help/D32Calc.sdl" "$out/D32Calc.sdl"
install -m 0444 "$src/oui/gui/help/D32oui.sdl" "$out/D32oui.sdl"
(cd "$out" && sha256 D32Calc.sdl D32oui.sdl) >> "$out/.binary-checksums"
mv "$out/.binary-checksums" "$out/SHA256SUMS"
cat "$out/SHA256SUMS"
