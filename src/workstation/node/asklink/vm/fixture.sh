#!/bin/sh
# d32build: a short-lived private copy of pyrag's deep32-engine fixture root, served by production's
# deep32-engine and deep32-gateway (pyrag's venvs and launchers, used read-only) on 127.0.0.1:18965/18966.
set -eu
L=/build/lanes/asklink
R=$L/run/fixture
D=/usr/local/d32
CORE=$D/venv/deep32-engine/bin/python3.12
EDGE=$D/venv/deep32-gateway/bin/python3.14
CP=18965
EP=18966
up() {	# url
	i=0
	until $EDGE -I -c "
import urllib.error as e, urllib.request as u
try: u.urlopen('$1', timeout=2)
except e.HTTPError: pass" 2>/dev/null; do
		i=$((i + 1)); [ $i -lt 60 ] || return 1; sleep 1
	done
}
case "${1:-}" in
start)
	[ ! -f $R/pids ] || { echo "fixture already running"; exit 1; }
	rm -rf $R
	mkdir -p $R
	cp -R /build/lanes/pyrag/smoke/fixture $R/fixture
	umask 077
	CORE_TOKEN=$(openssl rand -hex 24)
	openssl rand -hex 24 > $R/edge.token
	env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$R" LANG=C RAG_ROOT="$R/fixture/root" \
	  RAG_CORE_API_TOKEN="$CORE_TOKEN" RAG_RETRIEVAL_CONFIG="$R/fixture/retrieval.json" \
	  RAG_QDRANT_URL=http://127.0.0.1:9 RAG_EMBEDDING_URL=http://127.0.0.1:9/v1/embeddings \
	  $CORE -I -B -X utf8 $D/node/serve_core.py --host 127.0.0.1 --port $CP > "$R/core.log" 2>&1 &
	echo $! > $R/pids
	up http://127.0.0.1:$CP/health || { echo "deep32-engine did not come up"; tail -20 $R/core.log; exit 1; }
	env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$R" LANG=C RAG_CORE_URL=http://127.0.0.1:$CP \
	  RAG_CORE_API_TOKEN="$CORE_TOKEN" TAILSCALE_DNS_NAME=node.invalid RAG_MCP_TOKENS="$(cat $R/edge.token)" \
	  $EDGE -I -B -X utf8 $D/node/serve_edge.py --host 127.0.0.1 --port $EP > "$R/edge.log" 2>&1 &
	echo $! >> $R/pids
	up http://127.0.0.1:$EP/health || { echo "deep32-gateway did not come up"; tail -20 $R/edge.log; exit 1; }
	echo "fixture up: deep32-engine :$CP, deep32-gateway :$EP (pids $(tr '\n' ' ' < $R/pids))"
	;;
stop)
	[ -f $R/pids ] && kill $(cat $R/pids) 2>/dev/null || true
	rm -f $R/pids $R/edge.token
	echo "fixture stopped"
	;;
*)
	echo "usage: fixture.sh start|stop" >&2; exit 64 ;;
esac
