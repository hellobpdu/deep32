# 10-media: find the install medium by its DUID, mount it read-only at /mnt (inside the chroot), and install the
# firmware it carries with "fw_update -p", which checks OpenBSD's signature (INSTALLER 1.8). 20-packages unmounts it.
# A missing medium is not an error: a network install has none.
. /etc/d32/site-lib.sh
if ! dev=$(d32_media_dev); then
	d32 "item no install medium found by DUID; firmware and packages skipped"
	exit 0
fi
d32 "item install medium is $dev (DUID $(d32conf media_duid))"
mount -r /dev/${dev}a /mnt
if [ -d /mnt/firmware/8.0 ] && [ -f /mnt/firmware/8.0/SHA256.sig ]; then
	fw_update -p /mnt/firmware/8.0 2>&1 | sed 's/^/D32: item fw_update: /'
else
	d32 "item no /firmware/8.0 on the medium; firmware skipped"
fi
