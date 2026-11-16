#!/bin/sh
# root: a test node under /build/lanes/asklink/run/<name> (never /etc/d32): identity, config,
# root-owned copies of the binaries, tcpfreeze in front of the tunnel, d32-chatd -d -v.
#   node.sh prepare NAME RELAYPORT     node.sh start NAME [FREEZEPORT TARGETPORT]     node.sh stop NAME
set -eu
L=/build/lanes/asklink
cmd=${1:-}; name=${2:-node}
R=$L/run/$name
case "$cmd" in
prepare)
	port=$3
	[ ! -f $R/chatd.pid ] || { echo "node $name running"; exit 1; }
	rm -rf $R
	install -d -o root -g wheel -m 0755 $R $R/bin $R/sock
	install -d -o root -g wheel -m 0700 $R/state
	install -o root -g wheel -m 0555 $L/d32-chatd $L/d32-chatctl $L/d32-chatctl-test $L/tests/tcpfreeze $R/bin/
	$R/bin/d32-chatctl -d $R/state keygen
	install -o root -g wheel -m 0600 $L/run/fixture/edge.token $R/state/edge.token
	cat > $R/chatd.conf <<CONF
node-id deep32-$name
state-dir $R/state
control-socket $R/sock/chatd.sock
ui-socket $R/sock/uplink.sock
ui-group d32build
ui-user _d32chat
edge 127.0.0.1 18966 /mcp
edge-token-file $R/state/edge.token
link work relay 127.0.0.1 $port user _d32upw class research-relay
CONF
	chmod 0644 $R/chatd.conf
	echo "prepared $R"
	;;
start)
	if [ $# -ge 4 ]; then
		$R/bin/tcpfreeze $3 $4 > $R/tcpfreeze.log 2>&1 &
		echo $! > $R/tcpfreeze.pid
	fi
	$R/bin/d32-chatd -d -v -f $R/chatd.conf > $R/chatd.log 2>&1 &
	echo $! > $R/chatd.pid
	sleep 1
	ps -axo pid,user,command | grep -E "d32-chatd|tcpfreeze" | grep -v grep || true
	;;
stop)
	[ -f $R/chatd.pid ] && kill $(cat $R/chatd.pid) 2>/dev/null || true
	[ -f $R/tcpfreeze.pid ] && kill $(cat $R/tcpfreeze.pid) 2>/dev/null || true
	rm -f $R/chatd.pid $R/tcpfreeze.pid
	echo "node $name stopped"
	;;
wipe)
	# remove the test identity and pins (keys never outlive the test)
	rm -rf $R/state
	echo "node $name state wiped"
	;;
*)
	echo "usage: node.sh prepare|start|stop|wipe NAME ..." >&2; exit 64 ;;
esac
