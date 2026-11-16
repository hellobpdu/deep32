#!/bin/sh
# collect-files.sh: the file list of an installed Deep32 Workstation (disc 1 test install), for asset-compare.py.
# Runs as root on the installed system (OpenBSD 8.0, /bin/sh). Writes NOTHING on that system: no temporary file,
# no service call. It walks the folders of the scope (the H11 listing's roots: R lines), skips the pruned subtrees
# (X lines) and checks each explicit path (P lines), and prints, between a BEGIN and an END line with the row count:
#   F<TAB>path<TAB>mode:uid:gid:flags:links<TAB>sha256     a file
#   L<TAB>path<TAB>mode:uid:gid:flags:links<TAB>->target   a symbolic link
#   D<TAB>path                                             an explicit path that is a folder
#   SKIP<TAB>path                                          a secret-bearing name: never opened, never hashed
#   ABSENT<TAB>path                                        an explicit path that does not exist
# The stat and sha256 forms are the ones the H11 listing used on the laptop (the asset readback inventory).
# Secret-bearing names (never hashed): *.env *.sec *.key token *.token ssh_host_* and master.passwd, spwd.db,
# random.seed, soii.key under /etc. Nothing else is read but file bytes for their hash.
#
# Usage
#   asset-compare.py scope ... --bundle collect-run.sh --collector collect-files.sh   (on build-controller)
#   ssh -J <gw> root@<node> 'sh -s' < collect-run.sh > installed.tsv                 (scope inside, nothing written)
#   sh collect-files.sh SCOPE-FILE                                                    (a scope file already there)
# D32_COLLECT_PREFIX (tests only) is put in front of every path read and taken off every path printed;
# D32_COLLECT_TEST_PATH (tests only) is searched first for commands (stand-ins of OpenBSD's stat and sha256).
set -u
PATH=${D32_COLLECT_TEST_PATH:+$D32_COLLECT_TEST_PATH:}/sbin:/bin:/usr/sbin:/usr/bin:$PATH
PFX=${D32_COLLECT_PREFIX:-}
NL='
'

secret_name() {	# secret_name <path>: 0 if the name is secret-bearing
	case $1 in
	*.env|*.sec|*.key|*/token|*.token|*/ssh_host_*) return 0 ;;
	/etc/master.passwd|/etc/spwd.db|/etc/random.seed|/etc/soii.key) return 0 ;;
	esac
	return 1
}

one() {	# one <path without prefix>: print one row
	p=$1
	f=$PFX$p
	if secret_name "$p"; then
		if [ -e "$f" ] || [ -L "$f" ]; then printf 'SKIP\t%s\n' "$p"; else printf 'ABSENT\t%s\n' "$p"; fi
		return 0
	fi
	if [ -L "$f" ]; then
		printf 'L\t%s\t%s\t->%s\n' "$p" "$(stat -f %Lp:%u:%g:%f:%l "$f")" "$(readlink "$f")"
	elif [ -d "$f" ]; then
		printf 'D\t%s\n' "$p"
	elif [ -f "$f" ]; then
		printf 'F\t%s\t%s\t%s\n' "$p" "$(stat -f %Lp:%u:%g:%f:%l "$f")" "$(sha256 -q "$f")"
	else
		printf 'ABSENT\t%s\n' "$p"
	fi
}

rows() {	# rows <scope file or ->: every row, unsorted
	roots= prunes= explicit=
	while IFS='	' read -r k v; do
		case $k in
		R) roots="$roots$v$NL" ;;
		X) prunes="$prunes$v$NL" ;;
		P) explicit="$explicit$v$NL" ;;
		'') ;;
		*) echo "collect-files: bad scope line: $k" >&2; return 1 ;;
		esac
	done <"$1"
	printf '%s' "$roots" | while IFS= read -r r; do
		[ -n "$r" ] || continue
		if [ ! -e "$PFX$r" ] && [ ! -L "$PFX$r" ]; then printf 'ABSENT\t%s\n' "$r"; continue; fi
		if [ ! -d "$PFX$r" ] || [ -L "$PFX$r" ]; then one "$r"; continue; fi
		# find prints every file and link; a pruned subtree is never entered
		set -- "$PFX$r" -xdev
		first=1
		for x in $(printf '%s' "$prunes"); do
			case $x in "$r"/*) ;; *) continue ;; esac
			if [ $first = 1 ]; then set -- "$@" '('; first=0; else set -- "$@" -o; fi
			set -- "$@" -path "$PFX$x"
		done
		[ $first = 1 ] || set -- "$@" ')' -prune -o
		find "$@" '(' -type f -o -type l ')' -print 2>/dev/null | while IFS= read -r f; do
			one "${f#"$PFX"}"
		done
	done
	printf '%s' "$explicit" | while IFS= read -r p; do
		[ -n "$p" ] || continue
		inroot=0
		for r in $(printf '%s' "$roots"); do
			case $p in "$r"|"$r"/*) inroot=1 ;; esac
		done
		for x in $(printf '%s' "$prunes"); do
			case $p in "$x"|"$x"/*) inroot=0 ;; esac
		done
		# a file under a walked root was printed by the walk already; folders and outside paths are checked here
		if [ $inroot = 1 ] && [ -f "$PFX$p" ] && [ ! -L "$PFX$p" ]; then continue; fi
		if [ $inroot = 1 ] && [ -L "$PFX$p" ]; then continue; fi
		one "$p"
	done
}

collect() {	# collect <scope file, or - for standard input>
	src=$1
	if [ "$src" = - ]; then src=/dev/stdin; fi
	[ -r "$src" ] || { echo "collect-files: no scope file: $1" >&2; return 2; }
	# the rows are counted by a second pass over the same output: kept in a shell variable, not a file
	out=$(rows "$src") || return 1
	n=$(printf '%s\n' "$out" | grep -c .)
	printf 'D32COLLECT/1 BEGIN host=%s\n' "$(hostname -s 2>/dev/null || echo unknown)"
	[ -z "$out" ] || printf '%s\n' "$out"
	printf 'D32COLLECT/1 END rows=%s\n' "$n"
}

if [ -z "${D32_COLLECT_NOAUTO:-}" ]; then
	[ $# -eq 1 ] || { echo "usage: sh collect-files.sh SCOPE-FILE (or - for standard input)" >&2; exit 2; }
	collect "$1"
fi
