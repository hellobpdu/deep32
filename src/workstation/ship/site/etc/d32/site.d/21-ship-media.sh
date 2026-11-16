# 21-ship-media (ship lane, First light PREVIEW): mount the install medium again (20-packages unmounted it) and check
# every pack before anything is unpacked: the system and private packs against /d32/SHA256.sig (d32test release key),
# the Library packs against /d32/library/SHA256.sig (d32test library key). signify -C hashes every listed file.
. /etc/d32/site-lib.sh
. /etc/d32/ship-lib.sh
ldconfig -m /usr/X11R6/lib /usr/local/lib
dev=$(d32_media_dev) || die "the install medium (DUID $(d32conf media_duid)) is not attached"
ship_mounted || mount -r /dev/${dev}a /mnt
[ -f $M/SHA256.sig ] || die "no /d32/SHA256.sig on the medium: not a Deep32 Workstation medium"
d32 "item checking the system packs (signify, release key)"
(cd $M && signify -C -p /etc/signify/d32-release.pub -x SHA256.sig) >/tmp/d32-sig.out 2>&1 ||
	{ tail -5 /tmp/d32-sig.out >&2; die "a pack on the medium fails its signature"; }
d32 "item $(grep -c ': OK$' /tmp/d32-sig.out) files verified (release key)"
if [ -f $M/library/SHA256.sig ]; then
	d32 "item checking the Library packs (signify, library key)"
	(cd $M/library && signify -C -p /etc/signify/d32-library.pub -x SHA256.sig) >/tmp/d32-sig.out 2>&1 ||
		{ tail -5 /tmp/d32-sig.out >&2; die "a Library pack on the medium fails its signature"; }
	d32 "item $(grep -c ': OK$' /tmp/d32-sig.out) Library files verified (library key)"
fi
rm -f /tmp/d32-sig.out
