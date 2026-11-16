# 75-ship-desktop (ship lane): services in order under their own users, and the CDE login with the HP-UX look.
# rc.d order (pkg_scripts): d32core (deep32-engine, _d32core) before d32edge (deep32-gateway, _d32edge), then
# kiwix_serve (_kiwix-serve, 127.0.0.1:8850 only); portmap for ToolTalk (pf drops every unsolicited packet);
# dtlogin last. NOT enabled: d32accel (GPU_EXT, off), d32front (remote queries, off), d32chat (asklink: until paired),
# inetd (no dtspcd, no rpc.cmsd), xenodm (dtlogin is the login).
# Update 1 (D.32.12u1): the boot-input pins in rc.conf.local (pf, its rules file, library ASLR) and apmd -A (U08,
# U30); X11 on the session PATH (U11); Xsession.d files executable (U06); touchpad tapping where there is a touchpad
# (item 56); actions whose programs are missing are listed, and d32-ship.dt overrides them (U18). This file is also
# installed by update 1 as the record of what a D.32.12u1 node was set up with.
. /etc/d32/site-lib.sh
on=""
for s in d32core d32edge kiwix_serve; do
	if [ -x /etc/rc.d/$s ]; then
		rcctl enable $s >/dev/null
		on="$on $s"
	fi
done
zim=$(ls /d32/libcontent/encyclopedia/*.zim 2>/dev/null | head -1 || true)
if [ -n "$zim" ]; then
	rcctl set kiwix_serve flags "--address=127.0.0.1 --port=8850 --blockexternal --nodatealiases $zim" >/dev/null
fi
rcctl enable portmap >/dev/null
rcctl disable inetd >/dev/null 2>&1 || true
rcctl enable dtlogin >/dev/null
# apmd for the battery (performance follows the load), never suspend (U30: S0ix hung the Latitude; -Z later)
rcctl set apmd flags -A >/dev/null
d32 "item services on:$on portmap dtlogin apmd(-A); off: d32accel d32front d32chat inetd xenodm sshd"
# boot inputs pinned in rc.conf.local, which is schg (U08): rc.conf's defaults are not trusted to stay as they are.
# rcctl drops a setting equal to rc.conf's default, so these lines are added by hand, then the file is sorted.
# dhcpleased off (items 56 and 58): LAN only, no DHCP client; the maintenance kit sets a static address.
for kv in pf=YES pf_rules=/etc/pf.conf library_aslr=YES dhcpleased_flags=NO; do
	grep -q "^${kv%%=*}=" /etc/rc.conf.local 2>/dev/null || print -r -- "$kv" >>/etc/rc.conf.local
done
sort -o /etc/rc.conf.local /etc/rc.conf.local
d32 "item rc.conf.local pins: pf=YES pf_rules=/etc/pf.conf library_aslr=YES dhcpleased_flags=NO"
# CDE needs the host name in /etc/hosts (port README)
h=$(hostname -s 2>/dev/null || echo deep32)
grep -Eq "[[:space:]]$h([[:space:]]|\$)" /etc/hosts 2>/dev/null ||
	print -r -- "127.0.0.1	localhost $h" >>/etc/hosts
# dtlogin: X on UNIX sockets only, no XDMCP (ISO 9.1); the private fonts on the server's path. X runs on vt05
# (ttyC4, whose getty is off), as OpenBSD's own xenodm Xservers has it: without "vt05" the server took the active
# console, ttyC0, where getty also runs, and the keyboard went to getty, not to the greeter (boot test, 2026-11-06:
# "the desktop user" and his password typed at the first greeter logged the desktop user in on the hidden text console).
install -d -o root -g wheel -m 0755 /etc/dt/config
if [ ! -f /etc/dt/config/Xservers ]; then
	print -r -- ':0   Local local_uid@console root /usr/X11R6/bin/X :0 vt05 -nolisten tcp -dpi 100' >/etc/dt/config/Xservers
fi
if [ ! -f /etc/dt/config/Xconfig ]; then
	cp /usr/local/dt/config/Xconfig /etc/dt/config/Xconfig
	grep -q '^Dtlogin.requestPort' /etc/dt/config/Xconfig ||
		print -r -- 'Dtlogin.requestPort:	0' >>/etc/dt/config/Xconfig
fi
# the session PATH has X11's programs (U11: without them xset, xrdb and 11 Application Manager entries failed)
if ! grep -q '^Dtlogin\*userPath:.*/usr/X11R6/bin' /etc/dt/config/Xconfig; then
	sed -i 's|^\(Dtlogin\*userPath:[[:space:]]*[^[:space:]]*\)$|\1:/usr/X11R6/bin|' /etc/dt/config/Xconfig
	grep -q '^Dtlogin\*userPath:.*/usr/X11R6/bin' /etc/dt/config/Xconfig || die "Xconfig: userPath without /usr/X11R6/bin"
fi
# Xsession sources only executable files (U06: the preview's 0130.d32ship was 0644 and never ran)
chmod 0555 /etc/dt/config/Xsession.d/* 2>/dev/null || true
d32 "item CDE session: PATH has /usr/X11R6/bin; $(ls /etc/dt/config/Xsession.d | wc -l | tr -d ' ') Xsession.d files, 0555"
# touchpad tapping (item 56, the owner's choice), only where the first pointer is a touchpad (the Latitude: imt0)
for m in mouse0 mouse1 mouse2; do
	if [ "$(wsconsctl -n $m.type 2>/dev/null)" = touchpad ]; then
		print -r -- "# written by 75-ship-desktop (update 1): tap to click on the touchpad" >/etc/wsconsctl.conf
		print -r -- "$m.tp.tapping=1" >>/etc/wsconsctl.conf
		chmod 0644 /etc/wsconsctl.conf
		d32 "item touchpad: $m.tp.tapping=1 in /etc/wsconsctl.conf"
		break
	fi
done
# X fonts (boot test, 2026-11-06). The look ships its faces as BDF sources (fonts/d32hp/src, fonts/hpvue/src) and
# Xorg drops a font directory that has no fonts.dir; and Xorg keeps only the LAST "Files" section it reads from
# xorg.conf.d, so the look's 11-d32-hpvue-fonts.conf hid its own 10-d32-fonts.conf. Result on the first test system:
# no Deep32 faces at the greeter (CDE's -dt-interface names unresolved) and no Arial for Deep32 Library. So: compile the
# faces here (bdftopcf + mkfontdir, as the theme's install-user.sh does for a home), then ONE Files section, sorted
# last, with the whole path in order: Deep32 faces first (CDE's -dt-interface names), the VUE faces, his private
# Windows 3.1 and Library faces (Deep32 Library asks for -monotype-arial-*), then X11's own (the look's order).
nf=0
for d in /usr/local/share/fonts/d32hp /usr/local/share/fonts/hpvue; do
	[ -d $d/src ] || continue
	for f in $d/src/*.bdf; do
		[ -f "$f" ] || continue
		/usr/X11R6/bin/bdftopcf -t -o $d/$(basename $f .bdf).pcf $f && nf=$((nf + 1))
	done
	/usr/X11R6/bin/mkfontdir $d
	chmod 0444 $d/*.pcf $d/fonts.dir 2>/dev/null || true
done
install -d -o root -g wheel -m 0755 /etc/X11/xorg.conf.d
fp=
for d in /usr/local/share/fonts/d32hp/ /usr/local/share/fonts/hpvue/ /usr/local/share/fonts/d32private/pcf/ \
	/usr/local/share/fonts/d32private/ttf/; do
	[ -f ${d}fonts.dir ] && fp="$fp $d"
done
{
	print -r -- '# Deep32/UX font path (ship lane, 75-ship-desktop). Xorg keeps only the last Files section of'
	print -r -- '# xorg.conf.d, so this file, sorted last, holds the whole path: Deep32 faces, VUE faces, the private'
	print -r -- '# Windows 3.1 and Library faces, then the X11 directories.'
	print -r -- 'Section "Files"'
	for d in $fp /usr/X11R6/lib/X11/fonts/misc/ /usr/X11R6/lib/X11/fonts/100dpi/:unscaled \
		/usr/X11R6/lib/X11/fonts/75dpi/:unscaled /usr/X11R6/lib/X11/fonts/Type1/ /usr/X11R6/lib/X11/fonts/TTF/ \
		/usr/X11R6/lib/X11/fonts/OTF/; do
		print -r -- "    FontPath \"$d\""
	done
	print -r -- 'EndSection'
} >/etc/X11/xorg.conf.d/90-d32-ship-fontpath.conf
chmod 0444 /etc/X11/xorg.conf.d/90-d32-ship-fontpath.conf
d32 "item X fonts: $nf look faces compiled (bdftopcf, mkfontdir); one font path:$fp, then X11's"
# root's cron: the RAG status lamp (rag_status over edge.sock, counts and state only) every minute
if ! grep -q d32-lampd /var/cron/tabs/root 2>/dev/null; then
	printf '*\t*\t*\t*\t*\t/usr/local/d32/ship/libexec/d32-lampd -q\n' >>/var/cron/tabs/root
fi
chmod 0600 /var/cron/tabs/root
# placeholders: every program a front-panel or Application Manager action runs from $D32BIN or $D32LBIN that this
# preview does not have opens a small "not in this preview" box instead of failing silently
n=0
for p in $(sed -En 's/^[[:space:]]*EXEC_STRING[[:space:]]*[$](D32BIN|D32LBIN)\/([^ "]*).*/\1 \2/p' \
	/etc/dt/appconfig/types/C/*.dt 2>/dev/null | sort -u | tr ' ' ':'); do
	case ${p%%:*} in D32BIN) d=/d32/a/bin ;; D32LBIN) d=/usr/local/d32/bin ;; *) continue ;; esac
	b=${p#*:}
	[ -e $d/$b ] && continue
	install -d -o root -g wheel -m 0755 $d
	ln -s /usr/local/d32/ship/libexec/d32-notyet $d/$b
	n=$((n + 1))
done
d32 "item $n actions without a program in this preview open a 'not in this preview' box"
# actions through doas ($D32SBIN) and with absolute programs cannot be given a placeholder program (doas would run it
# as another user without the display; a stand-in in /usr/local/bin would collide with the package later): d32-ship.dt
# overrides those actions (update 1, U18). This lists any that are still missing, for the install log.
miss=""
for p in $(sed -En 's/^[[:space:]]*EXEC_STRING[[:space:]]*([$]DOAS -u [^ ]+ )?([$]D32SBIN|\/usr\/local\/bin)\/([^ "]*).*/\2:\3/p' \
	/etc/dt/appconfig/types/C/*.dt 2>/dev/null | sort -u); do
	case ${p%%:*} in '$D32SBIN') d=/usr/local/d32/sbin ;; *) d=${p%%:*} ;; esac
	[ -e $d/${p#*:} ] || miss="$miss ${p#*:}"
done
d32 "item programs not in this release (their actions are overridden in d32-ship.dt):${miss:- none}"
# the desktop user's first session: the Library browser profile and the look start empty; the lamp updater runs per session
d32 "item CDE login (dtlogin) with the HP-UX look; Desktop Style > Look Profile switches to HP VUE per user"
