# 80-firstboot: what runs at the first start. /etc/rc runs /etc/rc.firsttime once, at securelevel 1, after the network
# daemons and before the package daemons (d32core, d32edge, d32link, dtlogin) and before any getty, so the console is
# free. d32-firstboot asks for the passwords there, makes this node's tokens and re-arms itself until that part is
# done; the Tailscale sign-in and the wait for disc 2 run from /etc/rc.local at every start until they are done.
. /etc/d32/site-lib.sh
fb=/usr/local/d32/sbin/d32-firstboot
[ -x $fb ] || die "no $fb"
print -r -- "$fb boot </dev/console >/dev/console 2>&1" >>/etc/rc.firsttime
d32 "item d32-firstboot will run at the first start"
if d32test; then
	[ -f /var/db/d32/firstboot-check ] || die "test image without /var/db/d32/firstboot-check"
	d32 "item test image: first-boot check armed"
fi
