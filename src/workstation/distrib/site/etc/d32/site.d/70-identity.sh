# 70-identity: services, interfaces, names, serial nodes and first-boot cleanup (ISO 2.4.4 step 4, 2.6.3; FIX-20,
# FIX-23). No sshd (zero trust), no smtpd (nothing is mailed, so nothing lands in /var/mail/root), no resolvd
# (resolv.conf is static: unbound on loopback, "lookup file bind"); unbound and ntpd on.
. /etc/d32/site-lib.sh
rcctl disable sshd smtpd resolvd >/dev/null
rcctl enable unbound ntpd >/dev/null
d32 "item services: sshd smtpd resolvd off; unbound ntpd on"

# Deep32 services: enabled only when their launcher (or, in a test image, the stand-in) is installed
for s in d32core d32edge d32chat d32e26import; do
	[ -x /etc/rc.d/$s ] || continue
	l=/usr/local/d32/sbin/$s-launch
	if [ -x $l ]; then
		rcctl enable $s >/dev/null
		d32 "item service $s enabled"
	elif [ -f /etc/d32/standin ] && [ -x /usr/local/d32/libexec/d32-standin ]; then
		rcctl enable $s >/dev/null
		d32 "item service $s enabled (test stand-in)"
	else
		d32 "item service $s installed, not enabled (no launcher yet)"
	fi
done

# interfaces from d32setup.conf: if.<name>=<line>,<line>
if [ -f /etc/d32setup.conf ]; then
	grep '^if\.' /etc/d32setup.conf | while IFS='=' read -r key val; do
		ifn=${key#if.}
		print -r -- "$val" | sed 's/#.*//' | tr ',' '\n' |
			sed 's/^[[:space:]]*//;s/[[:space:]]*$//;/^$/d' >/etc/hostname.$ifn
		chmod 0640 /etc/hostname.$ifn
		d32 "item /etc/hostname.$ifn: $(tr '\n' ';' </etc/hostname.$ifn)"
	done
fi

# names: unbound on loopback; /etc/hosts first (pairing writes the ts.net names there, FIX-20)
printf 'nameserver 127.0.0.1\nlookup file bind\n' >/etc/resolv.conf
chmod 0644 /etc/resolv.conf
if [ ! -s /var/unbound/etc/d32-forward.conf ]; then
	print -r -- "# written by d32-dnsup from the DHCP lease; empty until the first lease" \
		>/var/unbound/etc/d32-forward.conf
fi
chown root:wheel /var/unbound/etc/d32-forward.conf
chmod 0644 /var/unbound/etc/d32-forward.conf
d32 "item resolv.conf: 127.0.0.1, lookup file bind"

# serial consoles: six tiles need cuaU4-cuaU7 (FIX-23; MAKEDEV all stops at 3). Not here: mknod(2) returns EINVAL
# inside a chroot (found by tests/base/chroot-rehearsal.sh), and install.site always runs chrooted. rc.securelevel
# makes them at every boot when missing.
grep -q 'MAKEDEV ttyU4' /etc/rc.securelevel || die "rc.securelevel does not make cuaU4-7"
d32 "item /dev/cuaU4-7: made by rc.securelevel at boot"

# rc.firsttime: the install is offline, firmware came from the medium, errata come in update sets (BASE 3.6 step 7)
if [ -f /etc/rc.firsttime ]; then
	awk '/^\/usr\/sbin\/fw_update$/ { next }
	     /^set -A _KERNV/ { skip = 1 }
	     skip && /^fi$/ { skip = 0; next }
	     !skip { print }' /etc/rc.firsttime >/etc/rc.firsttime.d32
	cat /etc/rc.firsttime.d32 >/etc/rc.firsttime
	rm -f /etc/rc.firsttime.d32
	d32 "item rc.firsttime: fw_update and syspatch -c removed"
fi

# root's crontab: refresh the DNS upstreams every 5 minutes (a table edit only)
if ! grep -q d32-dnsup /var/cron/tabs/root 2>/dev/null; then
	printf '*/5\t*\t*\t*\t*\t/usr/local/d32/sbin/d32-dnsup -q\n' >>/var/cron/tabs/root
fi
chmod 0600 /var/cron/tabs/root
d32 "item root crontab: d32-dnsup every 5 minutes"
