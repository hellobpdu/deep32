#!/bin/sh
# forge-host-python.sh: check that the Forge host's system Python can run the Forge pairing helper, and offer to
# install it with the distribution's package manager when it can't.
#
# The 0.1.0-alpha.2 pairing helper starts with /usr/bin/python3. That Python must be 3.10 or newer and able to
# import ssl. Most Linux distributions ship one; some appliance systems ship a broken or old one in a /usr that is
# rebuilt at every boot, and this script says so rather than changing them.
#
#   sh install/forge-host-python.sh            check only (exit 0 ready, 1 not ready, 2 cannot fix here)
#   sudo sh install/forge-host-python.sh --install   check, then install python3 with apt, dnf, zypper or pacman
set -u
PY=/usr/bin/python3
mode=${1:-check}
case $mode in check|--check) mode=check ;; --install) mode=install ;; *)
    echo "usage: sh install/forge-host-python.sh [--install]" >&2; exit 64 ;;
esac

check() {
    [ -x "$PY" ] || { echo "not ready: $PY is missing"; return 1; }
    out=$("$PY" -I -c 'import ssl, sys; v = sys.version_info; print("%d.%d" % v[:2]); raise SystemExit(0 if v >= (3, 10) else 3)' 2>&1)
    rc=$?
    if [ $rc -eq 0 ]; then echo "ready: $PY is Python $out with ssl"; return 0; fi
    if [ $rc -eq 3 ]; then echo "not ready: $PY is Python $out; the pairing helper needs 3.10 or newer"; return 1; fi
    echo "not ready: $PY cannot import ssl:"
    echo "$out" | tail -1
    return 1
}

if check; then exit 0; fi
if [ "$mode" = check ]; then
    echo "run again with --install as root to install Python with your package manager"
    exit 1
fi
[ "$(id -u)" = 0 ] || { echo "run --install as root (sudo)" >&2; exit 1; }

usr_fs=$(stat -f -c %T /usr 2>/dev/null || echo unknown)
case $usr_fs in tmpfs|ramfs|rootfs|squashfs) ram_usr=1 ;; *) ram_usr=0 ;; esac
if [ "$ram_usr" = 1 ]; then
    echo "cannot fix here: this system keeps /usr in memory ($usr_fs) and rebuilds it at every boot, so a package"
    echo "installed now is lost."
    echo "The 0.1.0-alpha.2 Forge cannot pair on this host. Use a Linux machine with a working Python 3.10+, or wait"
    echo "for 0.1.0-alpha.3, which runs the helper with the installer's own Python."
    exit 2
fi
if command -v apt-get >/dev/null 2>&1; then
    apt-get update && apt-get install -y python3
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3
elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install python3
elif command -v pacman >/dev/null 2>&1; then
    pacman -S --noconfirm --needed python
else
    echo "cannot fix here: no supported package manager (apt, dnf, zypper, pacman). Install Python 3.10+ so that"
    echo "$PY exists, then run this check again."
    exit 2
fi
if check; then exit 0; fi
echo "the package manager's Python still isn't ready; this distribution's python3 may be older than 3.10" >&2
exit 2
