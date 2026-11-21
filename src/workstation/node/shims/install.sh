#!/bin/sh
# install.sh: put the shims lane onto the build VM (run as root from /build/lanes/shims/tree). Idempotent.
# Adds beside the pyrag lane's work, never into it: /usr/local/d32/{sbin,node/shims}, /etc/rc.d/d32{core,edge,
# accel,front}, /etc/login.conf.d/d32{d,core,edge,accel}, /etc/d32/, the users _d32core _d32edge _d32gpu and the
# group _d32users with BASE's ids (distrib/ids/d32-ids.tsv). The venvs, py312, sqlite and the wheelhouse are only
# read. Nothing is enabled at boot (rcctl enable is never run): tests start the services with rcctl -f start.
set -eu
S=$(cd "$(dirname "$0")" && pwd)
D=/usr/local/d32
bridge_source=${D32_MODEL_BRIDGE_SOURCE:-$S/../../../model-bridge/d32_model_bridge.py}
[ -f "$bridge_source" ] || { echo "install.sh: shared model bridge source is missing; stage src/model-bridge and set D32_MODEL_BRIDGE_SOURCE" >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo "install.sh: run as root" >&2; exit 1; }

# ids (BASE's table: never renumber)
getent group _d32users >/dev/null || groupadd -g 3201 _d32users
install -o root -g wheel -m 0644 "$S/etc/login.conf.d/d32d" /etc/login.conf.d/d32d
mkuser() {
	getent passwd "$1" >/dev/null && return 0
	useradd -u "$2" -g =uid -c "$3" -d /var/empty -s /sbin/nologin -L d32d "$1"
}
mkuser _d32core 3211 "Deep32 deep32-engine"
mkuser _d32edge 3212 "Deep32 deep32-gateway"
mkuser _d32gpu 3214 "Deep32 GPU_EXT forwarder"

# login classes (rc.subr picks the class named after the script)
for c in d32core d32edge d32accel; do
	install -o root -g wheel -m 0644 "$S/etc/login.conf.d/$c" /etc/login.conf.d/$c
done

# the root step
install -d -o root -g wheel -m 0755 $D/sbin
cc -O2 -Wall -Wextra -Werror -o /tmp/d32-sockexec.$$ "$S/src/d32-sockexec.c"
install -o root -g wheel -m 0555 /tmp/d32-sockexec.$$ $D/sbin/d32-sockexec
rm -f /tmp/d32-sockexec.$$
for f in d32-gpu-ext d32-edge-front; do
	install -o root -g wheel -m 0555 "$S/sbin/$f" $D/sbin/$f
done

# launchers, bytecode for both interpreters (unchecked-hash: never rewritten, as the venvs')
install -d -o root -g wheel -m 0755 $D/node/shims
for f in pledge.py d32svc.py node_layers.py serve_core.py serve_edge.py serve_accel.py; do
	install -o root -g wheel -m 0444 "$S/py/$f" $D/node/shims/$f
done
install -o root -g wheel -m 0444 "$bridge_source" $D/node/shims/d32_model_bridge.py
rm -rf $D/node/shims/__pycache__
$D/venv/deep32-engine/bin/python3.12 -I -m compileall -q --invalidation-mode unchecked-hash $D/node/shims
/usr/local/bin/python3.14 -I -m compileall -q --invalidation-mode unchecked-hash $D/node/shims
chown -R root:wheel $D/node/shims

# rc.d
for r in d32core d32edge d32accel d32front; do
	install -o root -g wheel -m 0555 "$S/rc.d/$r" /etc/rc.d/$r
done

# configuration and run-time state (FIX-19: /etc/d32/state root 0700, files 0600)
install -d -o root -g wheel -m 0755 /etc/d32
install -d -o root -g wheel -m 0700 /etc/d32/state
for f in gpu_ext edge_front; do
	[ -f /etc/d32/state/$f ] || echo off >/etc/d32/state/$f
	chmod 0600 /etc/d32/state/$f
done
install -o root -g wheel -m 0600 "$S/etc/d32/relayd.conf" /etc/d32/relayd.conf
install -d -o root -g wheel -m 0755 /var/d32 /var/d32/run /var/run/d32
install -d -o _d32core -g _d32core -m 0700 /var/d32/run/core-tmp
echo off >/var/d32/run/gpu_ext
chmod 0644 /var/d32/run/gpu_ext
echo "install.sh: done"
