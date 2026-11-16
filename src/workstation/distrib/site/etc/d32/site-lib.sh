# /etc/d32/site-lib.sh: shared helpers for /etc/d32/site.d/*.sh (sourced, never run; BASE lane).
d32() { print -r -- "D32: $*"; }
die() { print -r -- "D32: fail $*" >&2; exit 1; }
# d32conf <key>: the last value of key=... in /etc/d32setup.conf (d32setup writes it at run time; test images ship it)
d32conf() {
	[ -f /etc/d32setup.conf ] || return 1
	sed -n "s/^$1=\([^#]*\).*/\1/p" /etc/d32setup.conf | sed 's/[[:space:]]*$//' | tail -1
}
d32test() { [ -f /etc/d32/test-image ]; }
# d32_media_dev: the install medium's disk name, found by its DUID in hw.disknames (never assumed)
d32_media_dev() {
	local _duid _p
	_duid=$(d32conf media_duid) || return 1
	[ -n "$_duid" ] || return 1
	for _p in $(sysctl -n hw.disknames | tr ',' ' '); do
		[ "${_p#*:}" = "$_duid" ] && { echo "${_p%%:*}"; return 0; }
	done
	return 1
}
