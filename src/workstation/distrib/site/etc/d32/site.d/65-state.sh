# 65-state: the run-time state and data directories, with the owners and modes the hardening review fixed.
# /etc/d32/state (FIX-19): root 0700/0600, not schg, every change audited with its SHA-256 and shown in Settings.
. /etc/d32/site-lib.sh
install -d -o root -g wheel -m 0755 /etc/d32
install -d -o root -g wheel -m 0700 /etc/d32/state
for f in inbound-profiles ifmap web-proxy hosts.d32 gpu_hub relay_hub relay_work; do
	if [ ! -f /etc/d32/state/$f ]; then
		print -r -- "# /etc/d32/state/$f: run-time state (FIX-19); root 0600, not schg; changes are audited" \
			>/etc/d32/state/$f
	fi
	chown root:wheel /etc/d32/state/$f
	chmod 0600 /etc/d32/state/$f
done
d32 "item /etc/d32/state: inbound-profiles ifmap web-proxy hosts.d32 gpu_hub relay_hub relay_work"

# /var/d32: run-time data on /var (ISO 4.1)
mk() { install -d -o $2 -g $3 -m $4 $1; }
mk /var/d32                    root       wheel      0755
mk /var/d32/run                root       wheel      0755
mk /var/d32/run/core-tmp       _d32core   _d32core   0700   # SQLITE_TMPDIR (HARDENING 5.8)
mk /var/d32/inbound            root       _d32inb    0750   # FIX-11; its own nodev,nosuid,noexec mount on the laptop
mk /var/d32/inbound/drop       root       wheel      0755   # sshd ChrootDirectory: root-owned, not writable
mk /var/d32/inbound/drop/in    root       _d32inb    3770   # SFTP and SCP drops land here
mk /var/d32/inbound/tftp       root       _d32inb    0750
mk /var/d32/inbound/log        _d32bench  _d32inb    2750
mk /var/d32/xfer               root       wheel      0755
mk /var/db/d32                 root       wheel      0755
d32 "item /var/d32 tree made"

# /d32 mount points (the partitions are mounted here; slot contents arrive with the payload)
for d in /d32/a /d32/b /d32/var; do
	[ -d $d ] && chown root:wheel $d && chmod 0755 $d
done
if [ -d /d32/audit ]; then
	chown root:wheel /d32/audit
	chmod 0700 /d32/audit
fi
if [ -d /d32/libcontent ]; then
	chown root:_d32users /d32/libcontent
	chmod 0750 /d32/libcontent
fi
d32 "item /d32 mount points owned"
