#!/bin/sh
# Runs inside the build VM as d32build, in /build/lanes/category-art. Usage: sh vm/run-check.sh
# (CATART_LANE and CATART_DISPLAY override the lane folder and the Xvfb display, e.g. another lane's own)
# 1. three independent decoders agree: netpbm's xpmtoppm, bmptopnm and pngtopnm give byte-identical PPMs per piece
# 2. sxpm (libXpm) parses every XPM (after Xvfb is up)
# 3. Motif loads every XPM through XmGetPixmap under Xvfb (:16, this lane's display, unless CATART_DISPLAY); galleries captured with xwd
set -e
L=${CATART_LANE:-/build/lanes/category-art}
D=${CATART_DISPLAY:-16}
cd $L
export DISPLAY=:$D
X11=/usr/X11R6
mkdir -p cap
rm -f cap/*.txt cap/*.log cap/*.png cap/*.err
T=$(mktemp -d /tmp/catart.XXXXXX)
# 1. decoders
N=0; F=0
for x in $(find out -name '*.xpm' | sort); do
	b=${x%.xpm}
	N=$((N+1))
	xpmtoppm "$x" > $T/x.ppm 2>/dev/null
	bmptopnm "$b.bmp" > $T/b.ppm 2>/dev/null
	pngtopnm "$b.png" > $T/p.ppm 2>/dev/null
	if cmp -s $T/x.ppm $T/b.ppm && cmp -s $T/x.ppm $T/p.ppm; then :; else echo "DECODE MISMATCH $b" >> cap/decoders.txt; F=$((F+1)); fi
done
echo "netpbm decoders (xpmtoppm = bmptopnm = pngtopnm): $N pieces, $F mismatches" | tee -a cap/decoders.txt
RC=$F
# 3. Motif galleries
cc -O2 -Wall -o $T/xmcats vm/xmcats.c -I/usr/local/include -I$X11/include -L/usr/local/lib -L$X11/lib -Wl,-rpath,/usr/local/lib -lXm -lXt -lX11
pkill -f "Xvfb :$D " 2>/dev/null || true
Xvfb :$D -dpi 100 -screen 0 1280x1024x24 -nolisten tcp >/dev/null 2>&1 &
XPID=$!
sleep 2
# 2. sxpm parse (it needs the display even with -nod)
SF=0
for x in $(find out -name '*.xpm' | sort); do sxpm -nod "$x" -o $T/rt.xpm >/dev/null 2>>cap/sxpm.err || { echo "SXPM FAIL $x" >> cap/sxpm.txt; SF=$((SF+1)); }; done
echo "sxpm: $N files, $SF failed" | tee -a cap/sxpm.txt
RC=$((RC+SF))
shot() { # name cols caption files...
	n=$1; c=$2; t=$3; shift 3
	xsetroot -solid '#31353E'
	$T/xmcats -cols "$c" -caption "$t" "$@" > cap/$n.log &
	G=$!; sleep 2
	xwd -root -silent | xwdtopnm 2>/dev/null | pnmtopng > cap/$n.png
	wait $G || RC=$((RC+1))
	cat cap/$n.log
}
ORDER="netbasics routing switching wan wireless dc netsvc internet mgmt orgs crypto identity threats defence policy os itinfra cs ai history"
CAT=""; FW=""; IC=""; for i in $ORDER; do CAT="$CAT out/cat/$i.xpm"; FW="$FW out/findwizard/$i.xpm"; IC="$IC out/icon/$i.32.xpm out/icon/$i.16.xpm"; done
shot gallery-cat 2 'CAT strips via XmGetPixmap' $CAT
shot gallery-findwizard 1 'Find Wizard strips via XmGetPixmap' $FW
shot gallery-icons 8 'icons 32 and 16 via XmGetPixmap' $IC
shot gallery-watermarks-1 3 'watermarks 1-6' out/watermark/netbasics.xpm out/watermark/routing.xpm out/watermark/switching.xpm out/watermark/wan.xpm out/watermark/wireless.xpm out/watermark/dc.xpm
shot gallery-watermarks-3 3 'watermarks 7-12' out/watermark/netsvc.xpm out/watermark/internet.xpm out/watermark/mgmt.xpm out/watermark/orgs.xpm out/watermark/crypto.xpm out/watermark/identity.xpm
shot gallery-watermarks-4 3 'watermarks 19-20' out/watermark/ai.xpm out/watermark/history.xpm
shot gallery-watermarks-2 3 'watermarks 13-18' out/watermark/threats.xpm out/watermark/defence.xpm out/watermark/policy.xpm out/watermark/os.xpm out/watermark/itinfra.xpm out/watermark/cs.xpm
kill $XPID 2>/dev/null || true
rm -rf $T
echo "run-check rc=$RC"
exit $RC
