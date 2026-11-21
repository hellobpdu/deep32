#!/bin/ksh
# mkimage-ship.sh <release-id>: the First light PREVIEW install image (ship lane, d32-build, root). Built like BASE's
# release/mkimage.sh (whose recipe it copies, not edits) with the ship lane's additions:
#   - the site set = BASE's distrib (site tree, rc.d skeletons, uid table) + the ship hooks (21, 66-69, 75), the
#     d32test release and library public keys, a d32setup.conf naming the medium's DUID;
#   - the ramdisk = OpenBSD 8.0's bsd.rd (kernel bytes untouched) + install.sub's @ASK@/@ASKSECRET@/@DUID@ answers
#     (install/patch-installsub.pl) + a GUIDED /auto_install.conf + the laptop's disklabel template;
#   - the FFS partition carries the sets, site80.tgz, the offline packages (/packages/amd64 + roots), and /d32: the
#     signed packs (system and Library), PACKS.tsv, keys/, README.
# Outputs in /build/lanes/ship/out/<release-id>/:
#   deep32ws-<id>-amd64.img            the USB image (MBR, EFI system partition, FFS; UEFI and BIOS boot), GUIDED
#   test/deep32ws-<id>-amd64-test.vhd  the same medium for the d32-test VM: serial console, the TEST response file
#                                      (wd1, disklabel.ship-test), power-off after the install
#   SHA256, SHA256.sig (d32test release key), test/SHA256(.sig), SIZES.txt
set -eu
umask 022
relid=${1:?usage: mkimage-ship.sh <release-id>}
L=/build/lanes/ship
S=$L/src
REL=/build/lanes/base/in/rel
OUT=$L/out/$relid
MEDIA=$L/media
duid=d32d32d32d32d32d
name=deep32ws-$relid-amd64
[ "$(id -u)" -eq 0 ] || { echo "mkimage-ship: run as root" >&2; exit 1; }
W=$(mktemp -d /build/lanes/ship/work/img.XXXXXXXX)
V= R=
cleanup() {
	umount $W/m 2>/dev/null || true
	[ -n "$R" ] && vnconfig -u $R 2>/dev/null || true
	[ -n "$V" ] && vnconfig -u $V 2>/dev/null || true
	rm -rf $W
}
trap cleanup EXIT
mkdir -p $OUT/test $W/m
log() { print -r -- "mkimage-ship: $*"; }

# 0. OpenBSD's sets, checked against its signed list
sets="BUILDINFO INSTALL.amd64 bsd bsd.mp bsd.rd base80.tgz man80.tgz xbase80.tgz xfont80.tgz xserv80.tgz xshare80.tgz"
(cd $REL && signify -C -p /etc/signify/openbsd-80-base.pub -x SHA256.sig $sets) >$W/verify.txt
log "OpenBSD sets: $(grep -c ': OK$' $W/verify.txt) verified against SHA256.sig"

# 1. the site set (BASE's tree + the ship overlay): mksite-ship.sh
ksh $S/ship/tools/mksite-ship.sh $relid $W/site80.tgz | sed 's/^mksite-ship/mkimage-ship/'

# 2. the ramdisks: GUIDED (the stick) and TEST (the VM); kernel bytes untouched (rdsetroot)
mkrd() {	# mkrd <variant> <response.in> <template> <out>
	local v=$1 resp=$2 tmpl=$3 o=$4
	gzip -dc $REL/bsd.rd >$W/rd.raw
	rdsetroot -x $W/rd.raw $W/rd.fs
	R=$(vnconfig $W/rd.fs)
	mount /dev/${R}a $W/m
	want=$(sed -n 's/^# sha256 install.sub: //p' $S/release/inputs.txt)
	[ "$(sha256 -q $W/m/install.sub)" = "$want" ] || { echo "mkimage-ship: install.sub is not 8.0's" >&2; exit 1; }
	perl $S/ship/install/patch-installsub.pl $W/m/install.sub $S/distrib/site/usr/local/d32/libexec/d32-c6.sh $S/distrib/site/usr/local/d32/libexec/d32-settings.sh $S/ship/install/d32-ramdisk.ksh >/dev/null
	grep -q '^_d32resp()' $W/m/install.sub
	# both ramdisks power off after the install: GUIDED once its root-disk answer has left /tmp/ai/d32-guided-disk,
	# TEST through /d32-halt-after-install below
	sed '/^#/d' $S/distrib/install/install.sub-halt.diff | patch -s -d $W/m -p0
	rm -f $W/m/install.sub.orig
	grep -q 'd32-halt-after-install' $W/m/install.sub
	if [ $v = test ]; then
		print -r -- "test medium: power off after the install" >$W/m/d32-halt-after-install
		pw=$(cat $L/keys/test-root.pw)
		hash=$(print -r -- "$pw" | encrypt -b a)
	else
		hash=
	fi
	ksh -n $W/m/install.sub
	sed -e "s|@ROOT_HASH@|$hash|" -e "s|@ROOT_DISK@|${D32_TEST_ROOTDISK:?set D32_TEST_ROOTDISK: sd1 on Proxmox, wd1 on Hyper-V Generation 1}|" -e "s|@MEDIA_DUID@|$duid|" -e "s|@TEMPLATE@|$(basename $tmpl)|" \
		$resp >$W/m/auto_install.conf
	if grep -v '^#' $W/m/auto_install.conf | sed 's/@ASK@//;s/@MAINT@//;s/@ASKSECRET@//;s/@DUID:[0-9a-f]*@//;s/@ROOTDISK:[0-9a-f]*@//;s/@SET:[a-z_]*@//;s/@ENCRYPT@//;s/@DEFAULT@//' | grep -q '@[A-Z_]*@'; then
		echo "mkimage-ship: unfilled @VAR@ in the $v auto_install.conf" >&2; exit 1
	fi
	install -m 0644 $tmpl $W/m/$(basename $tmpl)
	chmod 0644 $W/m/auto_install.conf
	if ls $W/m | grep -qE '\.(orig|rej)$'; then echo "mkimage-ship: patch leftovers in the ramdisk" >&2; exit 1; fi
	log "ramdisk $v: $(df -k $W/m | awk 'NR == 2 { print $4 }') KB left"
	umount $W/m
	vnconfig -u $R
	R=
	rdsetroot $W/rd.raw $W/rd.fs
	gzip -9nc $W/rd.raw >$o
	rm -f $W/rd.raw $W/rd.fs
}
[ -f $L/keys/test-root.pw ] || (umask 077; mkdir -p $L/keys; openssl rand -hex 12 >$L/keys/test-root.pw)
# the templates, through disklabel -T as install.sub will (laptop: 256 GiB sparse; test: the VM's 40 GiB)
for t in ship:262144 ship-test:40960; do
	dd if=/dev/zero of=$W/t.img bs=1m count=0 seek=${t#*:} 2>/dev/null
	T=$(vnconfig $W/t.img)
	disklabel -T $S/ship/install/disklabel.${t%%:*} -F $W/t.fstab -w -A $T >$W/t.out 2>&1 ||
		{ vnconfig -u $T; echo "mkimage-ship: disklabel.${t%%:*} fails: $(cat $W/t.out)" >&2; exit 1; }
	disklabel -h $T | awk '/^  [a-p]:/ && $4 == "4.2BSD" { printf "%s=%s ", $1, $2 }' >$W/t.sizes
	log "template disklabel.${t%%:*} on ${t#*:} MiB: $(awk '{ printf "%s ", $2 }' $W/t.fstab)| $(cat $W/t.sizes)"
	vnconfig -u $T
	rm -f $W/t.img $W/t.fstab
done
mkrd guided $S/ship/install/auto_install-guided.conf.in $S/ship/install/disklabel.ship $W/bsd.guided
mkrd test $S/ship/install/auto_install-test.conf.in $S/ship/install/disklabel.ship-test $W/bsd.test

# 3. the offline packages: the closure of install/pkg-roots (OpenBSD-signed; pkg_add checks every signature)
pk=$W/pkgs
mkdir -p $pk
(cd $W && ksh $S/ship/tools/pkgclosure.sh $S/ship/install/pkg-roots /build/pkgrepo $L/pkgs >$W/closure.out 2>&1)
[ ! -s $W/missing.txt ] || { echo "mkimage-ship: packages missing: $(cat $W/missing.txt)" >&2; exit 1; }
while read -r p; do
	if [ -f /build/pkgrepo/$p.tgz ]; then ln /build/pkgrepo/$p.tgz $pk/ 2>/dev/null || cp -p /build/pkgrepo/$p.tgz $pk/
	else cp -p $L/pkgs/$p.tgz $pk/; fi
done <$W/closure.txt
for r in $(grep -v '^#' $S/ship/install/pkg-roots | sed '/^$/d'); do
	grep -Ex "$r|$r-[0-9][^-]*" $W/closure.txt | head -1
done >$pk/roots
[ $(wc -l <$pk/roots) -eq $(grep -Ecv '^#|^$' $S/ship/install/pkg-roots) ] || { echo "mkimage-ship: a root is not in the closure" >&2; exit 1; }
log "packages: $(wc -l <$W/closure.txt | tr -d ' ') ($(du -sh $pk | cut -f1)), roots: $(tr '\n' ' ' <$pk/roots)"

# 4. the FFS content as a tree of hard links (same filesystem: no copy), then makefs(8) writes the partition straight
#    into a file (copying gigabytes through vnd(4) ran at about 1-4 MB/s); one tree, two partitions (guided, test)
[ -f $MEDIA/d32/SHA256.sig ] || { echo "mkimage-ship: no signed packs in $MEDIA (mkpacks.sh)" >&2; exit 1; }
T=$W/ffs
mkdir -p $T/8.0/amd64 $T/etc $T/packages/amd64
for f in $sets SHA256.sig; do ln $REL/$f $T/8.0/amd64/$f 2>/dev/null || cp -p $REL/$f $T/8.0/amd64/$f; done
cp -p $W/site80.tgz $T/8.0/amd64/
for f in $pk/*; do ln $f $T/packages/amd64/ 2>/dev/null || cp -p $f $T/packages/amd64/; done
# the laptop's firmware (update 1, U37): inteldrm (the i915 DMC, GuC, HuC) and intel (CPU microcode), fetched with
# "fw_update -F" on this VM with OpenBSD's own SHA256.sig. 10-media.sh installs it with "fw_update -p /mnt/firmware/8.0"
# (which checks that signature), so no install ever asks the internet for firmware.
FW=${FW:-/build/lanes/u1/bundle-src/firmware/8.0}
if [ -f $FW/SHA256.sig ] && ls $FW/inteldrm-firmware-*.tgz $FW/intel-firmware-*.tgz >/dev/null 2>&1; then
	mkdir -p $T/firmware/8.0
	for f in $FW/SHA256.sig $FW/inteldrm-firmware-*.tgz $FW/intel-firmware-*.tgz; do
		ln $f $T/firmware/8.0/ 2>/dev/null || cp -p $f $T/firmware/8.0/
	done
	log "firmware: $(cd $T/firmware/8.0 && ls *.tgz | tr '\n' ' ')"
else
	echo "mkimage-ship: no firmware in $FW (SHA256.sig, inteldrm-firmware-*, intel-firmware-*): fw_update -F there first" >&2
	exit 1
fi
(cd $MEDIA && find d32 -type d) | while read -r d; do mkdir -p $T/$d; done
(cd $MEDIA && find d32 -type f) | while read -r f; do ln $MEDIA/$f $T/$f 2>/dev/null || cp -p $MEDIA/$f $T/$f; done
install -m 0644 $S/ship/doc/MEDIUM-README.txt $T/README.txt
cp /usr/mdec/boot $T/boot
install -m 0644 $S/ship/install/boot.conf.guided $T/etc/boot.conf
install -m 0644 $W/bsd.guided $T/bsd
(cd $T && find . -type f | sort | xargs ls -l | awk '{ print $5, $NF }') >$OUT/CONTENTS.txt
kb=$(du -skL $T | awk '{ print $1 }')
mb=$(( kb / 1024 * 103 / 100 + 256 ))
# the disk: MBR (EFI system partition of 960 sectors) and one OpenBSD partition 'a' over the rest, fixed DUID
img=$OUT/$name.img
rm -f $img
dd if=/dev/zero of=$img bs=1m count=0 seek=$(( mb + 2 )) 2>/dev/null
V=$(vnconfig $img)
fdisk -iy -b 960 -f /usr/mdec/mbr $V >/dev/null
echo '/ *' | disklabel -wAT- $V
printf 'i\n%s\nw\nq\n' $duid | disklabel -E $V >/dev/null
newfs -t msdos /dev/r${V}i >/dev/null
mount -t msdos /dev/${V}i $W/m
mkdir -p $W/m/efi/boot
cp /usr/mdec/BOOTX64.EFI /usr/mdec/BOOTIA32.EFI $W/m/efi/boot/
umount $W/m
aoff=$(disklabel $V | awk '$1 == "a:" { print $3 }')
asize=$(disklabel $V | awk '$1 == "a:" { print $2 }')
disklabel $V | sed -n '/^duid:/p;/^  [ai]:/p' | sed 's/^/mkimage-ship: label /'
vnconfig -u $V
V=
[ $(( aoff % 16 )) -eq 0 ] || { echo "mkimage-ship: partition a starts at sector $aoff, not on an 8 KiB boundary" >&2; exit 1; }
# makefs writes the whole partition, its first 8 KiB included (FFS1 as BASE and OpenBSD media; a makefs FFS2 placed
# this way would not mount: EINVAL, tried 2026-11-06). OpenBSD keeps the disklabel in the partition's
# sector 1 and biosboot in sector 0, so the partition goes in from its second 8 KiB block on (FFS1's superblock is
# at 8 KiB): the label and the boot sector stay as disklabel and installboot write them.
putpart() {	# putpart <partition-file> <disk-image> <bsd> <boot.conf>
	dd if=$1 of=$2 bs=8k skip=1 seek=$(( aoff / 16 + 1 )) conv=notrunc 2>/dev/null
	V=$(vnconfig $2)
	fsck_ffs -n /dev/r${V}a >$W/fsck.out 2>&1 || { cat $W/fsck.out >&2; exit 1; }
	mount /dev/${V}a $W/m
	rm -f $W/m/bsd $W/m/etc/boot.conf
	install -m 0644 $3 $W/m/bsd
	install -m 0644 $4 $W/m/etc/boot.conf
	installboot -r $W/m $V /usr/mdec/biosboot /usr/mdec/boot
	log "medium FFS ($(basename $2)): $(df -h $W/m | awk 'NR == 2 { print $3 " used, " $4 " free" }'), $(sed -n 1p $W/m/etc/boot.conf | cut -c1-60)"
	umount $W/m
	vnconfig -u $V
	V=
}
rm -f $W/a.ffs
makefs -t ffs -o version=1,minfree=0,optimization=space,bsize=32768,fsize=4096,density=1048576 \
	-s $(( asize * 512 )) $W/a.ffs $T >$W/makefs.out 2>&1 || { cat $W/makefs.out >&2; exit 1; }
# 5. the TEST medium beside it, written straight into the VHD's place (a fixed VHD is the raw disk + a 512-byte
#    footer): the same partition with the TEST ramdisk and the serial-console boot.conf, then the footer
vhd=$OUT/test/$name-test.vhd
rm -f $vhd
dd if=/dev/zero of=$vhd bs=1m count=0 seek=$(( mb + 2 )) 2>/dev/null
dd if=$img of=$vhd bs=8k count=$(( aoff / 16 + 1 )) conv=notrunc 2>/dev/null
putpart $W/a.ffs $img $W/bsd.guided $S/ship/install/boot.conf.guided
putpart $W/a.ffs $vhd $W/bsd.test $S/ship/install/boot.conf.test
rm -f $W/a.ffs
python3 - $vhd $S/release/tools <<'EOF'
import hashlib, os, sys
sys.path.insert(0, sys.argv[2])
import raw2vhd
p = sys.argv[1]
size = os.path.getsize(p)
assert size % (1 << 20) == 0, size
with open(p, "ab") as o:
    o.write(raw2vhd.footer(size, hashlib.sha256(b"deep32ws test medium %d" % size).digest()))
print("mkimage-ship: fixed VHD footer appended (%d MiB, geometry %d/%d/%d)" % ((size >> 20,) + raw2vhd.chs(size // 512)))
EOF

# 6. hashes and the test signature
(cd $OUT && sha256 $name.img >SHA256)
signify -S -s "${D32_RELEASE_SEC:?set D32_RELEASE_SEC to the release secret key}" -m $OUT/SHA256 -x $OUT/SHA256.sig
signify -V -p "${D32_RELEASE_PUB:?set D32_RELEASE_PUB to the release public key}" -m $OUT/SHA256 -x $OUT/SHA256.sig >/dev/null
(cd $OUT/test && sha256 $name-test.vhd >SHA256)
signify -S -s "$D32_RELEASE_SEC" -m $OUT/test/SHA256 -x $OUT/test/SHA256.sig
cp -p "$D32_RELEASE_PUB" $OUT/d32-release.pub
cp -p "${D32_LIBRARY_PUB:?set D32_LIBRARY_PUB to the library public key}" $OUT/d32-library.pub
{
	print "Deep32/UX $relid First light PREVIEW: sizes (bytes)"
	ls -l $OUT/$name.img $OUT/test/$name-test.vhd | awk '{ printf "%-60s %15d\n", $NF, $5 }'
	print
	print "On the medium's FFS partition (KB):"
	du -sk $REL $pk $MEDIA/d32/packs $MEDIA/d32/library $W/bsd.guided | sed "s|$W/||;s|$MEDIA/||;s|$REL|8.0/amd64 (OpenBSD sets)|"
	print
	print "Packs:"
	grep -v '^#' $MEDIA/d32/PACKS.tsv | awk -F'\t' '{ printf "%-18s %-8s %15d  %s\n", $1, $3, $4, $2 }'
} >$OUT/SIZES.txt
chown -R d32build:d32build $OUT
log "done:"
cat $OUT/SIZES.txt
cat $OUT/SHA256
