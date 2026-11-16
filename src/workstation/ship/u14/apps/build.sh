#!/bin/sh
# Build the u14 apps lane payload (OpenBSD 8.0, ordinary build user, fresh source checkout):
#   1. d32calc and d32capdlg from the u10 source, relinked with libtt (libDtSvc leaves the ToolTalk symbols
#      Tttk_string and tttk_Xt_input_handler to the program), stripped;
#   2. the preinstalled OUI registry /usr/local/d32/share/data/oui from the pinned mac-vendor package;
#   3. the Serial Console add-in (d32-serial, its CDE action, front-panel control and Application Manager entry).
# Usage: sh build.sh SRC_CHECKOUT OUTDIR MAC_VENDOR_TGZ     (OUTDIR must not exist)
set -eu
export LC_ALL=C
export PATH=/bin:/usr/bin:/sbin:/usr/sbin:/usr/X11R6/bin:/usr/local/bin:/usr/local/dt/bin

[ "$#" -eq 3 ] && [ "$(uname -s)" = OpenBSD ] ||
	{ echo 'usage: sh build.sh SRC_CHECKOUT OUTDIR MAC_VENDOR_TGZ (OpenBSD)' >&2; exit 64; }
src=$(cd "$1/ship" && pwd -P)
[ ! -e "$2" ] || { echo "output exists: $2" >&2; exit 64; }
mkdir -p "$2"
out=$(cd "$2" && pwd -P)
pkg=$3
u10=$src/u10/src
u14=$src/u14/apps
pay=$out/payload
cd "$out"

mkdir -p "$pay/d32/a/bin" "$pay/usr/local/d32/bin" "$pay/usr/local/d32/share/data" \
	"$pay/etc/dt/appconfig/types/C" "$pay/etc/dt/appconfig/appmanager/C/Network_Tools"

echo '== 1. d32calc, d32capdlg (+ libtt)'
make -C "$u10/netcalc" all
make -C "$u10/capdlg" all
for b in "$u10/netcalc/d32calc" "$u10/capdlg/d32capdlg"; do
	ldd "$b" >"$out/ldd.txt"
	grep -q '/libtt\.so' "$out/ldd.txt" || { echo "$b: not linked with libtt" >&2; exit 1; }
done
rm -f "$out/ldd.txt"
install -s -m 0555 "$u10/netcalc/d32calc" "$pay/d32/a/bin/d32calc"
install -s -m 0555 "$u10/capdlg/d32capdlg" "$pay/d32/a/bin/d32capdlg"

echo '== 2. OUI system registry'
make -C "$u10/oui" d32ouiq oui-build
sh "$u14/oui/build-system-registry.sh" "$u10/oui/oui-build" "$pkg" "$pay/usr/local/d32/share/data/oui"

echo '== 3. Serial Console'
ksh -n "$u14/serial/d32-serial"
install -m 0555 "$u14/serial/d32-serial" "$pay/usr/local/d32/bin/d32-serial"
install -m 0444 "$u14/serial/d32-serial.dt" "$pay/etc/dt/appconfig/types/C/d32-serial.dt"
install -m 0444 "$u14/serial/d32-serial.fp" "$pay/etc/dt/appconfig/types/C/d32-serial.fp"
install -m 0555 "$u14/serial/D32SerialConsole" \
	"$pay/etc/dt/appconfig/appmanager/C/Network_Tools/D32SerialConsole"

# test tools for the smoke run (not shipped)
mkdir -p "$out/tools"
cp "$u10/oui/d32ouiq" "$out/tools/d32ouiq"

(cd "$pay" && find . -type f | sort | xargs sha256 -r) >"$out/payload.sha256"
cat "$out/payload.sha256"
