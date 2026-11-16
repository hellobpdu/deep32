# 68-ship-apps (ship lane): the desktop applications: Deep32 Library (engine), the Library glue (encyclopedia through kiwix-serve on loopback, papers through dtfile and MuPDF), the RAG
# status lamp, the look chooser (HP-UX CDE default, HP VUE selectable) and "not in this preview" placeholders.
. /etc/d32/site-lib.sh
. /etc/d32/ship-lib.sh
f=$(pack_file d32-apps)
[ -n "$f" ] || die "no d32-apps pack in PACKS.tsv"
d32 "item unpacking d32-apps"
tar -xzpf $M/$f -C /
pack_check d32-apps
# the active slot: /d32/active -> a (ISO 4.1; rc.securelevel flips it once slots exist)
[ -L /d32/active ] || ln -s a /d32/active
# Deep32 Library: the store, screens and taxonomy are read by the desktop user's session only
if [ -d /d32/a/share/e26 ]; then
	chown -R root:_d32users /d32/a/share/e26
	find /d32/a/share/e26 -type d -exec chmod 0750 {} +
	find /d32/a/share/e26 -type f -exec chmod 0440 {} +
fi
# private fonts: the X server (privsep user _x11) must read them; this is his own disk, encrypted
if [ -d /usr/local/share/fonts/d32private ]; then
	find /usr/local/share/fonts/d32private -type d -exec chmod 0555 {} +
	find /usr/local/share/fonts/d32private -type f -exec chmod 0444 {} +
fi
# the generation: root:_d32core, files 0440, directories 0550 (ISO 4.1, security 21)
if [ -d /d32/a/gen ]; then
	chown -R root:_d32core /d32/a/gen
	find /d32/a/gen -type d -exec chmod 0550 {} +
	find /d32/a/gen -type f -exec chmod 0440 {} +
	chmod 0555 /d32/a/gen
	d32 "item generation: root:_d32core 0440/0550"
fi
chmod 0755 /d32/a /d32/a/bin /d32/a/share 2>/dev/null || true
