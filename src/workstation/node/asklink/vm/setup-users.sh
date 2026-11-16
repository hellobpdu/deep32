#!/bin/sh
# root, once: the asklink service users from BASE's table (distrib/ids/d32-ids.tsv), if missing.
set -eu
add() {
	name=$1 id=$2 comment=$3
	if id "$name" >/dev/null 2>&1; then
		echo "$name exists (uid $(id -u "$name"))"
		return
	fi
	groupadd -g "$id" "$name"
	useradd -u "$id" -g "$id" -L d32d -d /var/empty -s /sbin/nologin -c "$comment" "$name"
	echo "$name created (uid $id, class d32d)"
}
add _d32upw 3217 "D32U/1 work relay link"
add _d32chat 3218 "chat broker, Deep32 Library Ask room"
