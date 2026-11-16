# 69-ship-library (ship lane): the Library content packs baked into First light PREVIEW, unpacked into
# /d32/libcontent (its own filesystem, nodev,nosuid,noexec): the Kiwix "Computer" Wikipedia ZIM, 438 Cisco design
# guides (view-only, personal use), the IETF RFC and draft text, the catalogue and word wheels.
# Their signatures were checked in 21-ship-media; every file is checked against its MANIFEST after unpacking.
# Library class, never corpus: no RAG tool reads them (ISO 4.7). Then the medium is unmounted.
. /etc/d32/site-lib.sh
. /etc/d32/ship-lib.sh
[ -d /d32/libcontent ] || die "no /d32/libcontent filesystem"
for n in $(pack_names library); do
	f=$(pack_file $n)
	d32 "item unpacking $n ($(( $(stat -f %z $M/$f) / 1048576 )) MB)"
	case $f in
	*.tgz)	tar -xzpf $M/$f -C / ;;
	*)	tar -xpf $M/$f -C / ;;
	esac
	pack_check $n
done
# owners: the desktop user reads the papers through group _d32users; kiwix-serve (package user _kiwix-serve) reads the ZIM only
for d in encyclopedia cisco ietf catalogue licences; do
	[ -d /d32/libcontent/$d ] || continue
	chown -R root:_d32users /d32/libcontent/$d
	find /d32/libcontent/$d -type d -exec chmod 0750 {} +
	find /d32/libcontent/$d -type f -exec chmod 0440 {} +
done
chmod 0751 /d32/libcontent   # _kiwix-serve may pass through to encyclopedia/, never list the rest
if [ -d /d32/libcontent/encyclopedia ]; then
	chown -R root:_kiwix-serve /d32/libcontent/encyclopedia
fi
d32 "item Library: $(du -sh /d32/libcontent | cut -f1) in /d32/libcontent"
cd /
umount /mnt && d32 "item install medium unmounted"
