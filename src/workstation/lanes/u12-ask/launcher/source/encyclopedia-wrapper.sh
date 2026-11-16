#!/bin/ksh
# /d32/active/bin/e26: Deep32 Library, the launcher the theme's Library actions run (ship lane; update 1, D.32.12u1; the Library packs, update 5).
# The engine is the library lane's (repo d32library: build 2, plus update 1's maximise as integer scaling and the Title
# screen's theme); its private asset store (the period design, 96 and 97 from the owner's own discs), screens, the owner's
# taxonomy and the catalogue's word wheels are passed here.
# Ask opens the native window when available, with the terminal client as fallback.
# The Library (update 5, item 70): the engine's read helper e26-lib runs from beside it (/d32/active/bin; the
# engine's built-in default is /d32/a/libexec). Pack 0, the one the rooms browse, is lib-e26 (/d32/libcontent/e26:
# the encyclopedia, IETF RFCs and drafts, Cisco design guides); the Article room opens the RAG corpus
# (/d32/libcontent/corpus) through a helper of its own, so a link or an id from either pack opens.
# "e26 -library corpus" (or E26_LIBRARY) makes that pack the one the rooms browse instead; the other still opens
# from it. A pack that is not installed is left out.
S=/d32/active/share/e26
F=/usr/local/share/fonts/d32private
E=/d32/active/bin/d32library
XSET=/usr/X11R6/bin/xset
L=/d32/libcontent
lib=${E26_LIBRARY:-e26}
if [ "${1:-}" = -library ]; then
	lib=${2:-}
	shift
	[ $# -gt 0 ] && shift
fi
case $lib in
e26|corpus) ;;
*)	print -u2 "e26: -library takes e26 or corpus"; exit 64 ;;
esac
[ -r $L/$lib/PACK.json ] || lib=e26
packs=
for p in e26 corpus; do
	[ $p = $lib ] && continue
	[ -r $L/$p/PACK.json ] && packs="${packs:+$packs;}$L/$p"
done
E26_LIB_PACK=$L/$lib
E26_LIB_HELPER=${E26_LIB_HELPER:-/d32/active/bin/e26-lib}
export E26_LIB_PACK E26_LIB_HELPER
if [ -n "$packs" ]; then E26_ARTICLE_PACKS=$packs; export E26_ARTICLE_PACKS; fi
case ${1:-} in
-ask)
	if [ "${2:-}" != -transcript ]; then
		if [ -x /usr/local/d32/bin/d32-askw ]; then
			unset E26_LIBRARY E26_LIB_PACK E26_LIB_HELPER E26_ARTICLE_PACKS
			exec /usr/local/d32/bin/d32-askw
		fi
		for client in /usr/local/d32/bin/d32-ask /usr/local/dt/bin/dtterm; do
			[ -x "$client" ] || exec /usr/local/d32/ship/libexec/d32-notyet "Ask" "$client is missing or not executable."
		done
		unset E26_LIBRARY E26_LIB_PACK E26_LIB_HELPER E26_ARTICLE_PACKS
		exec /usr/local/dt/bin/dtterm -title "Ask Deep32" -e /usr/local/d32/bin/d32-ask
	fi
	exec /usr/local/d32/ship/libexec/d32-notyet "Ask" "Ask is disabled until this machine is paired with a Codex relay. Pairing is done at the console, as root (see INSTALL.md, Pairing Ask later). Until then Deep32 Encyclopedia works offline."
	;;
-acquisitions|-check-updates)
	exec /usr/local/d32/ship/libexec/d32-notyet "Deep32 Encyclopedia" "${1#-} is not in this release of the Deep32 Encyclopedia yet."
	;;
-pinpointer)
	shift
	;;
'')
	# "Open in Deep32 Library" with no file ("%Arg_1%" empty): Library itself
	[ $# -gt 0 ] && shift
	;;
-*)
	;;
*)
	# a file (Open in Deep32 Library on an RFC .txt, a D32LIST or a D32ORG file; update 1, U17): Deep32 Library has no file
	# reader yet, so the Reader opens it (text in the Library's browser window, PDF and the like in MuPDF)
	exec /d32/active/bin/d32read "$1"
	;;
esac
# the period fonts (his Windows 3.1 and Library faces, PCF and TrueType) on this X server's path, once per session.
# Update 1's 90-d32-ship-fontpath.conf puts both in the server's own path; this keeps a server without it right.
# xset by its full path: /usr/X11R6/bin is not on the CDE session's PATH (U11).
if [ -n "${DISPLAY:-}" ] && [ -x $XSET ]; then
	fp=$($XSET q 2>/dev/null)
	added=
	for d in $F/pcf $F/ttf; do
		if [ -f $d/fonts.dir ] && ! print -r -- "$fp" | grep -qF "$d"; then
			$XSET +fp $d/ >/dev/null 2>&1 && added=1
		fi
	done
	[ -n "$added" ] && $XSET fp rehash >/dev/null 2>&1
fi
wheels=/d32/libcontent/catalogue/wheels
[ -d $wheels ] || wheels=/nonexistent
# the Title screen's theme (update 1, the owner 2026-11-06, item 50): a second theme from the private store in
# place of the period design's. E26_THEME=hilite/enctheme (or -titletheme hilite/enctheme) brings the original back; a theme
# file that is not in the store falls back to the original too (d32library *titleTheme). An engine from before update 1
# has no -titletheme and would not start with it, so it is passed only to an engine that knows it.
if grep -q titleTheme $E 2>/dev/null; then
	exec $E -assets /d32/active/share/deep32-library/assets -screens $S/screens -taxonomy $S/TAXONOMY.json \
		-articles $S/articles -wheels $wheels -titletheme "${E26_THEME:-hilite/aquatic}" "$@"
fi
exec $E -assets /d32/active/share/deep32-library/assets -screens $S/screens -taxonomy $S/TAXONOMY.json \
	-articles $S/articles -wheels $wheels "$@"
