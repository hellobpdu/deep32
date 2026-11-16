# 60-pf-tables: the files behind pf's file-backed tables (ISO 2.6.1), root-only, and a parse of /etc/pf.conf against
# the users just made. pf itself loads at the first boot. The installation kernel has no pf, so a failed parse here
# is printed, not fatal: the first-boot check compares the loaded ruleset with this file.
. /etc/d32/site-lib.sh
install -d -o root -g wheel -m 0750 /etc/d32/pf
for t in ts_infra gpu_hub rag_edge ntp web_site relay_hub relay_work; do
	[ -f /etc/d32/pf/$t ] || print -r -- "# <$t>: empty" >/etc/d32/pf/$t
	chown root:wheel /etc/d32/pf/$t
	chmod 0640 /etc/d32/pf/$t
done
chown root:wheel /etc/pf.conf
chmod 0600 /etc/pf.conf
if pfctl -nf /etc/pf.conf 2>/tmp/d32-pf.err; then
	d32 "item pf.conf parses against the installed users"
else
	d32 "item pf.conf not parsed in the chroot (checked at first boot): $(head -3 /tmp/d32-pf.err | tr '\n' ' ')"
fi
rm -f /tmp/d32-pf.err
