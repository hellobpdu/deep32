#!/bin/ksh
# stage.sh: build the trees of every First light PREVIEW pack on the build VM (ship lane, root). Inputs are COPIES of the
# other lanes' work (push-to-vm.sh) and their installed results on this VM, used as they are now:
#   d32-node   /usr/local/d32 (pyrag: py312 3.12.14 + SQLite 3.53.1, venvs deep32-engine/deep32-gateway, code trees pub-9a2d58df01fc and
#              pub-9a2d58df01fc, stand-ins; nodegen: exact_local + libd32vec; shims: d32-sockexec, launchers), the shims' rc.d,
#              classes, relayd front and env examples, asklink's d32-chatd/d32-chatctl (built here), the ship launcher
#   d32-apps   Deep32 Library (built here from the library lane's repo), the art, the Library start page and the ship
#              glue
# Usage: ksh stage.sh [all|build|node|apps]
set -eu
umask 022
what=${1:-all}
L=/build/lanes/ship
S=$L/src
OBJ=/build/pobj/ship
[ "$(id -u)" -eq 0 ] || { echo "stage: run as root" >&2; exit 1; }
log() { print -r -- "stage: $*"; }

s_build() {
	rm -rf $L/build $OBJ
	install -d -o d32build -g d32build $L/build $OBJ
	cp -R $S/d32library $S/node/asklink $L/build/
	chown -R d32build:d32build $L/build
	su -s /bin/sh d32build -c "cd $L/build/d32library && gmake OBJ=$OBJ/library >$L/logs/library-build.log 2>&1 && \
		gmake OBJ=$OBJ/library test TAXONOMY=$S/TAXONOMY.json >$L/logs/library-test.log 2>&1"
	log "d32library: $(tail -1 $L/logs/library-test.log)"
	su -s /bin/sh d32build -c "cd $L/build/asklink && make d32-chatd d32-chatctl tests/test_codec >$L/logs/asklink-build.log 2>&1 && \
		./tests/test_codec tests/spec >$L/logs/asklink-test.log 2>&1"
	log "asklink: $(tail -1 $L/logs/asklink-test.log)"
}

s_node() {
	N=$L/stage/d32-node
	rm -rf $N
	mkdir -p $N/usr/local $N/etc/rc.d $N/etc/login.conf.d $N/etc/d32
	cp -Rp /usr/local/d32 $N/usr/local/
	# Deep32's own unit tests run on the build VM (node/run-gen.sh), never on a node (update 2, U58)
	rm -f $N/usr/local/d32/node/test_*.py $N/usr/local/d32/node/__pycache__/test_*.pyc
	# never ship a token, an env file or a secret key that a test left behind
	# (CPython's own test certificates and the CA bundles of certifi and grpc are public and allowed)
	find $N -type f \( -name '*.env' -o -name '*.sec' -o -name 'token' -o -name '*.token' -o -name '*.key' \
		-o -name '*.pem' \) | grep -Ev '/site-packages/(certifi|grpc)/|/py312/lib/python3.12/test/' >$L/work/secrets.lst || true
	if [ -s $L/work/secrets.lst ] || grep -rlIE 'RAG_(CORE_API|MCP)_TOKENS?=[0-9a-f]{16}' $N >/dev/null 2>&1; then
		cat $L/work/secrets.lst >&2
		echo "stage: refusing: a secret-looking file in /usr/local/d32" >&2; exit 1
	fi
	install -d $N/usr/local/d32/node/ship
	install -m 0444 $S/ship/apps/usr/local/d32/node/ship/serve_core_ship.py $N/usr/local/d32/node/ship/
	install -m 0555 $S/ship/apps/etc/rc.d/d32core $N/etc/rc.d/d32core
	for r in d32edge d32accel d32front; do install -m 0555 $S/node/shims/rc.d/$r $N/etc/rc.d/$r; done
	for c in d32core d32edge d32accel d32d; do install -m 0444 $S/node/shims/etc/login.conf.d/$c $N/etc/login.conf.d/$c; done
	install -m 0600 $S/node/shims/etc/d32/relayd.conf $N/etc/d32/relayd.conf
	for e in core edge accel; do install -m 0400 $S/node/shims/etc/d32/$e.env.example $N/etc/d32/$e.env.example; done
	install -m 0555 $L/build/asklink/d32-chatd $L/build/asklink/d32-chatctl $N/usr/local/d32/sbin/
	# the configuration block under "## Configuration" (update 1, U41: the preview's sed range printed nothing, so
	# the node got an empty example); refuse an empty result
	install -m 0444 $S/ship/u1/etc/d32/chatd.conf.example $N/etc/d32/chatd.conf.example
	awk '/^## Configuration/ { c = 1; next } c && /^```/ { if (f) exit; f = 1; next } f { print }' $S/node/asklink/README.md >$L/work/chatd.block
	[ -s $L/work/chatd.block ] && grep -q '^node-id' $L/work/chatd.block ||
		{ echo "stage: no chatd configuration block in asklink's README" >&2; exit 1; }
	sed -n '3,$p' $N/etc/d32/chatd.conf.example | cmp -s - $L/work/chatd.block ||
		{ echo "stage: ship/u1/etc/d32/chatd.conf.example differs from asklink's README block" >&2; exit 1; }
	chown -R root:wheel $N
	chmod -R go-w $N
	log "d32-node: $(find $N -type f | wc -l | tr -d ' ') files, $(du -sh $N | cut -f1)"
}

s_apps() {
	A=$L/stage/d32-apps
	rm -rf $A
	mkdir -p $A/d32/a/bin $A/d32/a/share/e26 $A/usr/local/d32/bin $A/usr/local/bin \
		$A/etc/rc.d $A/etc/d32 $A/usr/local/share/d32/art $A/usr/local/share/d32/library \
		$A/etc/firefox-esr
	# Deep32 Library build 2
	install -m 0555 $OBJ/library/d32library $A/d32/a/bin/d32library
	cp -R $S/d32library/screens $A/d32/a/share/e26/screens
	cp -R $S/d32library/data/articles $A/d32/a/share/e26/articles
	install -m 0444 $S/TAXONOMY.json $A/d32/a/share/e26/TAXONOMY.json
	# ship glue: the launchers the theme's actions run, the lamps, the look chooser, the Library
	for b in e26 d32lib d32status d32read; do install -m 0555 $S/ship/apps/usr/local/d32/ship/bin/$b $A/d32/a/bin/$b; done
	install -m 0555 $S/ship/apps/usr/local/d32/ship/bin/d32-look $A/usr/local/d32/bin/d32-look
	mkdir -p $A/usr/local/d32/ship
	cp -R $S/ship/apps/usr/local/d32/ship/libexec $S/ship/apps/usr/local/d32/ship/share $A/usr/local/d32/ship/
	# update 1's new ship files (ship/u1 mirrors node paths): the message box, the session resources, the session
	# script, the X hardening, the guide as Help's text
	install -m 0555 $S/ship/u1/usr/local/d32/ship/libexec/d32-msg $A/usr/local/d32/ship/libexec/d32-msg
	install -m 0444 $S/ship/u1/usr/local/d32/ship/share/d32.Xresources $A/usr/local/d32/ship/share/d32.Xresources
	chmod 0555 $A/usr/local/d32/ship/libexec/d32-*
	chmod 0444 $A/usr/local/d32/ship/libexec/d32-mcp.py $A/usr/local/d32/ship/share/*
	cp -R $S/ship/apps/etc/dt $A/etc/
	install -m 0555 $S/ship/u1/etc/dt/config/Xsession.d/0140.d32session $A/etc/dt/config/Xsession.d/0140.d32session
	# Xsession sources only executable files (update 1, U06: the preview's 0130 was 0644 and never ran)
	chmod 0555 $A/etc/dt/config/Xsession.d/*
	mkdir -p $A/etc/X11/xorg.conf.d
	install -m 0444 $S/ship/u1/etc/X11/xorg.conf.d/20-d32-hardening.conf $A/etc/X11/xorg.conf.d/20-d32-hardening.conf
	# Generated art first; the chosen theme below keeps precedence.
	mkdir -p "$A/usr/local/share/d32/art/deep32" "$A/usr/local/share/d32/art/library-categories"
	cp -R "$S/art/out/." "$A/usr/local/share/d32/art/deep32/"
	cp -R "$S/art/library-categories/out/." "$A/usr/local/share/d32/art/library-categories/"
	# the desktop: the public theme generator's output (cd theme && node tools/gen.js --release all), with the modes
	# its MANIFEST gives (Application Manager entries are 0555 action files); the ship's Desktop Look chooser stays
	T=$S/theme/out/all/root
	[ -f $T/MANIFEST.tsv ] || { echo "stage: no public theme output in $T (run the theme generator)" >&2; exit 1; }
	(cd $T && find . -type f ! -name MANIFEST.tsv | pax -rw -pp $A/)
	awk -F'\t' '$1 !~ /^#/ && NF >= 2 && $2 ~ /^0[0-7]+$/ { print $2, $1 }' $T/MANIFEST.tsv |
		while read -r m p; do [ -f "$A/$p" ] && chmod $m "$A/$p"; done
	install -m 0555 $S/ship/apps/usr/local/d32/ship/bin/d32-look $A/usr/local/d32/bin/d32-look
	# Firefox ESR may read the Library (read-only), in the main and content processes (pkg-readme: /etc/firefox-esr)
	for u in main content; do
		{ cat /usr/local/lib/firefox-esr/browser/defaults/preferences/unveil.$u
		  print "\n# Deep32 Workstation: the Library's installed content, read-only"
		  print "/d32/libcontent r"; } >$A/etc/firefox-esr/unveil.$u
		chmod 0444 $A/etc/firefox-esr/unveil.$u
	done
	# the Library start page, from the packs' own manifests (titles and paths only)
	install -m 0444 $S/ship/tools/mkindex.py $A/usr/local/d32/ship/libexec/mkindex.py
	/usr/local/bin/python3.14 -I $S/ship/tools/mkindex.py --empty >$A/usr/local/share/d32/library/index.html
	# the guide, as text for the Library window (Help, update 1: D32Help in d32-ship.dt)
	install -m 0444 $S/ship/doc/INSTALL.md $A/usr/local/share/d32/library/INSTALL.txt
	chown -R root:wheel $A
	chmod -R go-w $A
	log "d32-apps: $(find $A -type f | wc -l | tr -d ' ') files, $(du -sh $A | cut -f1)"
}

mkdir -p $L/logs $L/stage
case $what in
all) s_build; s_node; s_apps ;;
build|node|apps) s_$what ;;
*) echo "usage: stage.sh [all|build|node|apps]" >&2; exit 64 ;;
esac
