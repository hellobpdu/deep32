#!/bin/sh
# Runs inside the build VM as d32build, in /build/lanes/art. Usage: sh vm/run-check.sh
# 1. compile xpmcheck (libXpm) and xmgallery (Motif) with base clang
# 2. Xvfb :14 (the art lane's display), libXpm load of every .pm, sxpm parse of every .pm
# 3. Motif galleries captured with xwd -> PNG under cap/
set -e
L=/build/lanes/art
cd $L
export DISPLAY=:14
X11=/usr/X11R6
cc -O2 -Wall -o xpmcheck vm/xpmcheck.c -I$X11/include -L$X11/lib -lXpm -lX11
cc -O2 -Wall -o xmgallery vm/xmgallery.c -I/usr/local/include -I$X11/include -L/usr/local/lib -L$X11/lib -Wl,-rpath,/usr/local/lib -lXm -lXt -lX11
pkill -f "Xvfb :14" 2>/dev/null || true
Xvfb :14 -dpi 100 -screen 0 1280x1024x24 -nolisten tcp >/dev/null 2>&1 &
XPID=$!
sleep 2
mkdir -p cap
find out -name '*.pm' | sort > cap/files.txt
echo "files: $(wc -l < cap/files.txt)"
RC1=0; ./xpmcheck $(cat cap/files.txt) > cap/xpmcheck.txt || RC1=$?
tail -1 cap/xpmcheck.txt
# sxpm: parse each file and show it; -nod = no display window, -o to round-trip write it back out
FAILS=0; N=0
for f in $(cat cap/files.txt); do
	N=$((N+1))
	if ! sxpm -nod "$f" -o /tmp/art-sxpm-roundtrip.xpm >/dev/null 2>>cap/sxpm.err; then echo "SXPM FAIL $f" >> cap/sxpm.txt; FAILS=$((FAILS+1)); fi
done
echo "sxpm: $N files, $FAILS failed" | tee -a cap/sxpm.txt
rm -f /tmp/art-sxpm-roundtrip.xpm
shot() { # name bg cols title files...
	n=$1; bg=$2; c=$3; t=$4; shift 4
	xsetroot -solid '#303030'
	./xmgallery -ground "$bg" -cols "$c" -caption "$t" "$@" > cap/$n.log &
	G=$!; sleep 2
	xwd -root -silent | xwdtopnm 2>/dev/null | pnmtopng > cap/$n.png
	wait $G || true
	cat cap/$n.log
}
shot gallery-l '#93ABBF' 12 'Deep32 .l icons via XmGetPixmap (set 8)' $(ls out/icons/*.l.pm)
shot gallery-m '#93ABBF' 20 'Deep32 .m and data-type icons (set 8)' $(ls out/icons/*.m.pm out/icons/types/*.m.pm)
shot gallery-t '#93ABBF' 30 'Deep32 .t icons (set 8)' $(ls out/icons/*.t.pm out/icons/types/*.t.pm)
shot gallery-logos '#93ABBF' 12 'Deep32/UX logo shortlist' $(ls out/logos/*.[tml].pm out/logos/brand/*.[tml].pm)
shot gallery-large '#8998AA' 3 'dtlogin emblems and wordmark' out/logos/brand/*.login.pm out/logos/D32uxB.word.pm out/logos/brand/D32uxB.word.pm
shot gallery-library '#E7E7EF' 1 'Deep32 Library Ask room art' out/d32library/ask-room.pm out/d32library/ask-room-opaque.pm out/d32library/ask-icon.pm out/d32library/fw-ask.pm
# every 1-bit file (.bm and _m.bm masks, the boot emblems) through XReadBitmapFile via xsetroot
XF=0; XN=0
for b in $(find out -name '*.bm' | sort); do XN=$((XN+1)); xsetroot -bitmap "$b" -fg black -bg white 2>>cap/xbm.err || { echo "XBM FAIL $b" >> cap/xbm.txt; XF=$((XF+1)); }; done
echo "xbm (xsetroot -bitmap): $XN files, $XF failed" | tee -a cap/xbm.txt
[ "$XF" -eq 0 ] || RC1=1
kill $XPID 2>/dev/null || true
echo "xpmcheck rc=$RC1"
exit $RC1
