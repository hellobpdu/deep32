#!/bin/ksh
# mksite-ship.sh <release-id> <out.tgz>: the First light PREVIEW site set (ship lane, the build VM, root): BASE's distrib
# (site tree, rc.d skeletons, uid table) + the ship hooks and helpers (ship/site), the release and library public
# keys the build passes (D32_RELEASE_PUB, D32_LIBRARY_PUB), a d32setup.conf naming the medium's DUID. Files only, root:wheel, fixed mtimes, sorted, ustar, gzip -n
# (BASE's mksite.sh recipe, copied). Used by mkimage-ship.sh and refresh-ramdisk.sh.
set -eu
umask 022
relid=${1:?usage: mksite-ship.sh <release-id> <out.tgz>}
out=${2:?}
S=/build/lanes/ship/src
duid=d32d32d32d32d32d
[ "$(id -u)" -eq 0 ] || { echo "mksite-ship: run as root" >&2; exit 1; }
st=$(mktemp -d /build/lanes/ship/work/site.XXXXXXXX)
trap 'rm -rf $st $st.list' EXIT
cp -Rp $S/distrib/site/. $st/
mkdir -p $st/etc/rc.d $st/etc/signify
cp -p $S/distrib/rc.d/d32* $st/etc/rc.d/
cp -p $S/distrib/ids/d32-ids.tsv $st/etc/d32/ids.tsv
cp -Rp $S/ship/site/. $st/
print -r -- "$relid" >$st/etc/d32/release
cp -p "${D32_RELEASE_PUB:?set D32_RELEASE_PUB to the release public key}" $st/etc/signify/d32-release.pub
cp -p "${D32_LIBRARY_PUB:?set D32_LIBRARY_PUB to the library public key}" $st/etc/signify/d32-library.pub
cat >$st/etc/d32setup.conf <<EOF
# /etc/d32setup.conf: Deep32 Workstation ($relid). The medium is found by its DUID; no network is configured at install.
release=$relid
domain=
configuration=laptop
dns=dhcp
passwords=set
login=dtlogin
console=glass
firmware=
library=baked
media_duid=$duid
EOF
if find $st -type f \( -name '*.sec' -o -name '*~' -o -name '*.orig' -o -name '*.rej' \) | grep -q .; then
	echo "mksite-ship: refusing: secret key or leftover file in the site tree" >&2; exit 1
fi
if grep -rlI "$(printf '\r')" $st | grep -q .; then echo "mksite-ship: refusing: CRLF" >&2; exit 1; fi
for f in $st/etc/d32/site.d/*.sh $st/etc/d32/ship-lib.sh $st/install.site; do ksh -n $f; done
ksh -n "$st/usr/local/bin/deep32"
chown -R root:wheel $st
find $st -type d -exec chmod 0755 {} +
find $st -type f -exec chmod 0644 {} +
chmod 0555 $st/install.site $st/etc/rc.d/d32* $st/usr/local/d32/sbin/*
chmod 0555 "$st/usr/local/bin/deep32"
# Only launchers run directly; sourced libraries, importer modules and data stay 0644.
for f in d32-c6-words d32-content-get d32-content-import d32-forge-enrol d32-gateway-enrol \
	d32-import-again d32-import-report d32-pair d32-setup d32-uijson deep32-pair pairing-helper; do
	chmod 0555 "$st/usr/local/d32/libexec/$f"
done
chmod 0600 $st/etc/pf.conf
chmod 0640 $st/etc/d32/pf/*
# Application Manager entries act as their action only when executable (CDE), as release/mksite.sh has it
chmod 0555 $st/etc/dt/appconfig/appmanager/C/Deep32/D32FirstStart $st/etc/dt/appconfig/appmanager/C/Deep32/D32ImportReport $st/etc/dt/appconfig/appmanager/C/Deep32/D32Pairing
chmod 0444 $st/etc/d32/site.d/*.sh $st/etc/d32/site-lib.sh $st/etc/d32/ship-lib.sh
chmod 0444 "$st/usr/local/share/d32/release/release-key.gpg" \
	"$st/usr/local/share/d32/release/release-key.fingerprint" \
	"$st/etc/d32/content-compatible-versions"
find $st -exec touch -t 202611060000 {} +
(cd $st && find . -type f -o -type l | sed 's|^\./||' | sort) >$st.list
(cd $st && pax -w -d -x ustar <$st.list) | gzip -9n >$out
print -r -- "mksite-ship: site80.tgz: $(wc -l <$st.list | tr -d ' ') files, hooks: $(cd $st/etc/d32/site.d && ls | sed 's/\.sh$//' | tr '\n' ' '), sha256 $(sha256 -q $out)"
