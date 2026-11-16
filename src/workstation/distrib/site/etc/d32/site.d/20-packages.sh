# 20-packages: the offline repository (ISO 2.4.2, DISTRO EP1). pkg_add checks every signature: OpenBSD's packages with
# its key, ours with /etc/signify/*-pkg.pub. The roots come from the medium's /packages/amd64/roots (one package per
# line, written by the image build from apps.json). No repository, no packages.
. /etc/d32/site-lib.sh
if mount | grep -q ' on /mnt '; then
	repo=/mnt/packages/amd64
	if [ -f $repo/roots ]; then
		roots=$(grep -v '^#' $repo/roots | tr '\n' ' ')
		d32 "item pkg_add from the medium: $roots"
		env -i PATH=$PATH PKG_PATH=$repo/ pkg_add -I -m $roots
	else
		d32 "item no package roots on the medium; packages skipped"
	fi
	umount /mnt
else
	d32 "item no medium mounted; packages skipped"
fi
