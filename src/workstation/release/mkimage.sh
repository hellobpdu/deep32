#!/bin/ksh
# release/mkimage.sh <repo> <rel-dir> <out-dir> <release-id> [root-disk media-disk]
# Builds a Deep32 TEST installation image on the build VM (root): OpenBSD 8.0's signed sets + our site80.tgz + an
# autoinstall ramdisk, as a raw USB-style disk image (MBR, EFI system partition, FFS) and a fixed VHD of it for
# Hyper-V. The recipe is BASE 3.8's: the medium boots OUR ramdisk as /bsd, while 8.0/amd64/bsd.rd stays OpenBSD's
# file and still verifies against SHA256.sig. Test images only: d32test keys, a per-build random root password
# (written to <out-dir>/test-root.pw for the serial-console check, never into git), no real key, no payload.
# Defaults fit the Hyper-V Generation 1 test VM: medium on IDE 0:0 (wd0), target on IDE 0:1 (wd1).
set -eu
umask 022
repo=${1:?usage: mkimage.sh <repo> <rel-dir> <out-dir> <release-id> [root-disk media-disk]}
rel=${2:?}
out=${3:?}
relid=${4:?}
rootdisk=${5:-wd1}
mediadisk=${6:-wd0}
[ "$(id -u)" -eq 0 ] || { echo "mkimage: run as root" >&2; exit 1; }
duid=d32d32d32d32d32d
name=deep32ws-$relid-amd64
img=$out/$name.img
W=$(mktemp -d /build/pobj/base/img.XXXXXXXX)
V= R= T=
cleanup() {
	umount $W/m 2>/dev/null || true
	[ -n "$R" ] && vnconfig -u $R 2>/dev/null || true
	[ -n "$V" ] && vnconfig -u $V 2>/dev/null || true
	[ -n "$T" ] && vnconfig -u $T 2>/dev/null || true
	rm -rf $W
}
trap cleanup EXIT
mkdir -p $out $W/m
log() { print -r -- "mkimage: $*"; }

# 0. inputs: OpenBSD's sets, checked against its signed list (no comp or game sets on the test medium)
sets="BUILDINFO INSTALL.amd64 bsd bsd.mp bsd.rd base80.tgz man80.tgz xbase80.tgz xfont80.tgz xserv80.tgz xshare80.tgz"
(cd $rel && signify -C -p /etc/signify/openbsd-80-base.pub -x SHA256.sig $sets) >$W/verify.txt
log "OpenBSD sets: $(grep -c ': OK$' $W/verify.txt) verified against SHA256.sig"

# 1. the site set
ksh $repo/release/mksite.sh $repo test $W/site $relid

# 2. the ramdisk: response file, disklabel template, halt-after-install patch (rdsetroot; kernel bytes untouched)
gzip -dc $rel/bsd.rd >$W/bsd.rd.raw
rdsetroot -x $W/bsd.rd.raw $W/rd.fs
R=$(vnconfig $W/rd.fs)
mount /dev/${R}a $W/m
want=$(sed -n 's/^# sha256 install.sub: //p' $repo/release/inputs.txt)
have=$(sha256 -q $W/m/install.sub)
[ "$have" = "$want" ] || { echo "mkimage: install.sub is not the one the patch was made for ($have)" >&2; exit 1; }
sed '/^#/d' $repo/distrib/install/install.sub-halt.diff | patch -s -d $W/m -p0
grep -q 'd32-halt-after-install' $W/m/install.sub
# patch(1) leaves install.sub.orig (65 KB) in a ramdisk that has about 224 KB free; a .rej would mean a bad patch
rm -f $W/m/install.sub.orig
if ls $W/m | grep -qE '\.(orig|rej)$'; then echo "mkimage: patch left .orig/.rej files in the ramdisk" >&2; exit 1; fi
pw=$(openssl rand -hex 12)
hash=$(print -r -- "$pw" | encrypt -b a)
sed -e "s|@ROOT_HASH@|$hash|" -e "s|@ROOT_DISK@|$rootdisk|" -e "s|@MEDIA_DISK@|$mediadisk|" \
	-e "s|@TEMPLATE@|disklabel.d32test|" $repo/distrib/install/auto_install-test.conf.in >$W/m/auto_install.conf
if grep -q '@[A-Z_]*@' $W/m/auto_install.conf; then echo "mkimage: unfilled @VAR@ in auto_install.conf" >&2; exit 1; fi
install -m 0644 $repo/distrib/install/disklabel.d32test $W/m/disklabel.d32test
cmp -s $repo/distrib/install/disklabel.d32test $W/m/disklabel.d32test
# the template exactly as install.sub will use it: "disklabel -T <template> -F <fstab> -w -A <disk>" on a sparse
# 40 GiB vnd (the the test guest target disk); OpenBSD 8.0's disklabel stops on a comment line with "parse error"
dd if=/dev/zero of=$W/t40.img bs=1m count=0 seek=40960 2>/dev/null
T=$(vnconfig $W/t40.img)
if ! disklabel -T $W/m/disklabel.d32test -F $W/t40.fstab -w -A $T >$W/t40.out 2>&1; then
	echo "mkimage: the ramdisk's disklabel.d32test fails: $(cat $W/t40.out)" >&2; exit 1
fi
log "template disklabel.d32test on 40 GiB: $(wc -l <$W/t40.fstab | tr -d ' ') fstab lines; $(awk '{ printf "%s ", $2 }' $W/t40.fstab)"
vnconfig -u $T
T=
print -r -- "test medium: power off after the install" >$W/m/d32-halt-after-install
chmod 0644 $W/m/auto_install.conf $W/m/d32-halt-after-install
log "ramdisk: $(df -k $W/m | awk 'NR == 2 { print $4 }') KB left after our additions"
umount $W/m
vnconfig -u $R
R=
rdsetroot $W/bsd.rd.raw $W/rd.fs
gzip -9nc $W/bsd.rd.raw >$W/bsd.d32

# 3. the image: MBR with an EFI system partition, one FFS partition with a fixed DUID (BASE 3.8)
mb=1024
rm -f $img
dd if=/dev/zero of=$img bs=1m count=0 seek=$mb 2>/dev/null
V=$(vnconfig $img)
fdisk -iy -b 960 -f /usr/mdec/mbr $V >/dev/null
echo '/ *' | disklabel -wAT- $V
printf 'i\n%s\nw\nq\n' $duid | disklabel -E $V >/dev/null
newfs -t msdos /dev/r${V}i >/dev/null
mount -t msdos /dev/${V}i $W/m
mkdir -p $W/m/efi/boot
cp /usr/mdec/BOOTX64.EFI /usr/mdec/BOOTIA32.EFI $W/m/efi/boot/
umount $W/m
newfs -O 1 -m 0 -o space -i 524288 /dev/r${V}a >/dev/null
mount /dev/${V}a $W/m
mkdir -p $W/m/8.0/amd64 $W/m/etc
install -m 0644 $repo/distrib/install/boot.conf.test $W/m/etc/boot.conf
install -m 0644 $W/bsd.d32 $W/m/bsd
(cd $rel && cp -p $sets SHA256.sig $W/m/8.0/amd64/)
cp -p $W/site/site80.tgz $W/m/8.0/amd64/
installboot -r $W/m $V /usr/mdec/biosboot /usr/mdec/boot
(cd $W/m/8.0/amd64 && ls -lT >index.txt)
log "medium FFS: $(df -h $W/m | awk 'NR == 2 { print $3 " used, " $4 " free" }')"
umount $W/m
disklabel $V | sed -n '/^duid:/p;/^  [ai]:/p' | sed 's/^/mkimage: label /'
vnconfig -u $V
V=

# 4. the VHD for Hyper-V (fixed: raw + 512-byte footer), hashes and the test signature
python3 $repo/release/tools/raw2vhd.py $img $out/$name.vhd
cp -p $W/site/site80.tgz $out/site80.tgz
(cd $out && sha256 $name.img $name.vhd site80.tgz >$name.SHA256)
signify -S -s "${D32_RELEASE_SEC:?set D32_RELEASE_SEC to the release secret key}" -m $out/$name.SHA256 -x $out/$name.SHA256.sig
signify -V -p "${D32_RELEASE_PUB:?set D32_RELEASE_PUB to the release public key}" -m $out/$name.SHA256 -x $out/$name.SHA256.sig >/dev/null
print -r -- "$pw" >$out/test-root.pw
chmod 0600 $out/test-root.pw
chown d32build:d32build $out/*
log "done: $(cd $out && ls -l $name.* site80.tgz | awk '{ print $NF " " $5 }' | tr '\n' ';')"
cat $out/$name.SHA256
