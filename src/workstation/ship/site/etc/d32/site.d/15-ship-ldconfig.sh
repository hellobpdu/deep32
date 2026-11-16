# 15-ship-ldconfig (ship lane): the shared-library hints inside install.sub's chroot. install.sub removes
# /var/run/ld.so.hints before install.site and rc rebuilds it only at boot, so without this every program that links a
# library outside /usr/lib (X11's mkfontscale, fc-cache, the packages' @exec helpers, age, python) dies in ld.so with
# SIGKILL ("Killed", exit 137): found by the the test VM run of 2026-11-06. 20-packages's pkg_add adds /usr/local/lib.
. /etc/d32/site-lib.sh
ldconfig /usr/lib /usr/X11R6/lib /usr/local/lib
d32 "item ld.so hints: /usr/lib /usr/X11R6/lib /usr/local/lib"
