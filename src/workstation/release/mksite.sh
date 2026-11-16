#!/bin/ksh
# release/mksite.sh <repo> <test|real> <out-dir> <release-id>
# Builds site80.tgz, the Deep32 site set (ISO 2.4.2, BASE 3.6), from distrib/: the site tree, the rc.d scripts,
# the uid table and the release id; a "test" set adds distrib/test-overlay (stand-ins, markers, a test
# d32setup.conf, the test-only sysctl lines), the first-boot check and the d32test public key (read-only use of
# the release public key the build passes in as D32_RELEASE_PUB). Root only (members must be root:wheel).
# Reproducible: sorted file list, fixed mtimes, ustar via pax, gzip -n; two builds give the same SHA-256.
# Only files are archived, never directories, so extraction never changes the mode of an existing base directory.
set -eu
umask 022
repo=${1:?usage: mksite.sh <repo> <test|real> <out-dir> <release-id>}
variant=${2:?}
out=${3:?}
rel=${4:?}
[ "$(id -u)" -eq 0 ] || { echo "mksite: run as root" >&2; exit 1; }
case $variant in test|real) ;; *) echo "mksite: variant must be test or real" >&2; exit 64;; esac
mkdir -p $out
stage=$(mktemp -d /build/pobj/base/site.XXXXXXXX)
trap 'rm -rf $stage' EXIT

cp -Rp $repo/distrib/site/. $stage/
mkdir -p $stage/etc/rc.d $stage/etc/signify
cp -p $repo/distrib/rc.d/d32* $stage/etc/rc.d/
cp -p $repo/distrib/ids/d32-ids.tsv $stage/etc/d32/ids.tsv
print -r -- "$rel" >$stage/etc/d32/release
if [ $variant = test ]; then
	cp -Rp $repo/distrib/test-overlay/. $stage/
	# test-only sysctl lines (kern.wxabort=1 for HF-04 and PY-4) go at the end of the one sysctl.conf
	cat $stage/etc/sysctl.conf.d32test >>$stage/etc/sysctl.conf
	rm $stage/etc/sysctl.conf.d32test
	cp -p $repo/tests/base/firstboot-check.sh $stage/usr/local/d32/libexec/d32-firstboot-check
	cp -p "${D32_RELEASE_PUB:?set D32_RELEASE_PUB to the release public key}" $stage/etc/signify/d32-release.pub
fi
# never ship a secret key, a CRLF or an editor leftover
if find $stage -type f \( -name '*.sec' -o -name '*~' -o -name '*.orig' -o -name '*.rej' \) | grep -q .; then
	echo "mksite: refusing: secret key or leftover file in the tree" >&2; exit 1
fi
if grep -rlI "$(printf '\r')" $stage | grep -q .; then
	echo "mksite: refusing: CRLF line endings" >&2; exit 1
fi

# owners and modes
chown -R root:wheel $stage
find $stage -type d -exec chmod 0755 {} +
find $stage -type f -exec chmod 0644 {} +
chmod 0555 $stage/install.site $stage/etc/rc.d/d32*
find $stage/usr/local/d32/sbin $stage/usr/local/d32/libexec -type f -exec chmod 0555 {} + 2>/dev/null || true
# the Workstation screens' desktop files that run: the login hook and the Application Manager entries
find $stage/etc/dt/config/Xsession.d $stage/etc/dt/appconfig/appmanager -type f -exec chmod 0555 {} + 2>/dev/null || true
chmod 0600 $stage/etc/pf.conf
chmod 0640 $stage/etc/d32/pf/*
chmod 0444 $stage/etc/d32/site.d/*.sh $stage/etc/d32/site-lib.sh

# fixed mtimes, sorted member list, ustar, gzip -n
find $stage -exec touch -t 202611050000 {} +
(cd $stage && find . -type f -o -type l | sed 's|^\./||' | sort) >$stage.list
(cd $stage && pax -w -d -x ustar <$stage.list) | gzip -9n >$out/site80.tgz.new
rm -f $stage.list
mv $out/site80.tgz.new $out/site80.tgz
n=$(tar -tzf $out/site80.tgz | wc -l | tr -d ' ')
echo "mksite: $out/site80.tgz ($variant, $rel): $n files, sha256 $(sha256 -q $out/site80.tgz)"
