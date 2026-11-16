# 25-fstab: mount options for the Deep32 filesystems (ISO 2.5, BASE 4, FIX-17). install.sub writes the fstab from the
# disklabel template with "rw,nodev,nosuid"; the slots become read-only (the boot window remounts the inactive one rw),
# the data areas lose exec, and the slots lose atime updates.
. /etc/d32/site-lib.sh
set_opts() {
	if awk -v m="$1" '$2 == m { f = 1 } END { exit !f }' /etc/fstab; then
		awk -v m="$1" -v o="$2" '$2 == m { $4 = o } { print }' /etc/fstab >/etc/fstab.d32
		cat /etc/fstab.d32 >/etc/fstab
		rm -f /etc/fstab.d32
		d32 "item fstab $1 $2"
	else
		d32 "item fstab has no $1 (layout without it)"
	fi
}
set_opts /d32/a          ro,nodev,nosuid,noatime
set_opts /d32/b          ro,nodev,nosuid,noatime
set_opts /d32/var        rw,nodev,nosuid,noexec
set_opts /d32/audit      rw,nodev,nosuid,noexec
set_opts /d32/libcontent rw,nodev,nosuid,noexec
# update 2 (U34): /usr/local without wxallowed. install.sub adds it to /usr/local, as stock OpenBSD does; on 8.0 the
# only executables there carrying PT_OPENBSD_WXNEEDED are node (run with --jitless) and python3.14 (only cffi
# ABI-mode callbacks need W|X, and nothing shipped makes one): ship/u2/tests/hardening/wx-test.sh
set_opts /usr/local      rw,nodev
