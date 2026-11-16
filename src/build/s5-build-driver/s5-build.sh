#!/bin/sh
# s5-build.sh: builds disc 1 on deep32-builder (VM 910, OpenBSD 8.0) from the pinned build tree, stage by stage.
#
# The stages are the old pipeline's, each one file in stages/ (header lines STAGE, AS, IN, OUT), each logged to
# /build/dist/logs/<stage>.log and restartable: a stage that finished leaves /build/dist/state/<stage>.done holding
# the stamp of what it ran with (its own script, lib.sh, the tree hash, the release id, the key set, jobs, the root
# disk, the fixed time); a later run skips it while the stamp is the same and runs it again when anything changed.
# A stage that runs removes the markers of every stage after it, so nothing later is skipped on stale inputs.
#   00-preflight  10-tree  20-py312  30-wheels  35-locks  40-venvs  45-node  50-library  55-update-builds
#   60-stage  65-map  68-assets  70-packs  80-image  85-test-raw  88-scan-parts  90-provenance
#
# Usage (as root on deep32-builder; the lane is copied to /build/dist with build.conf beside it: LAB-RUN.md)
#   sh s5-build.sh [--from STAGE] [--only STAGE[,STAGE...]] [--force]
#   sh s5-build.sh --dry-run      every stage with its inputs and outputs; runs on any host, touches nothing
# --from STAGE   run STAGE and every later one, whatever their markers say
# --only LIST    run only these stages, whatever their markers say (a comma list of names or two-digit numbers)
# --force        ignore every marker
# Refuses to run (exit 3) on any host not named deep32-builder, and on the old build VM in any case.
# Never reads the old build VM or its test key: the keys are the throwaway lab key (v0, v1) or the train key (RC1),
# both made inside 910 under /build/dist/keys (build.conf D32_KEYSET).
# Exit codes: 0 done; 1 a stage failed (its log's last lines are printed); 2 usage; 3 refused host.
set -u
here=$(cd "$(dirname "$0")" && pwd)
ST=$here/stages
. "$ST/lib.sh"

DRY=0 FORCE=0 FROM= ONLY=
while [ $# -gt 0 ]; do
	case $1 in
	--dry-run|--list) DRY=1 ;;
	--force) FORCE=1 ;;
	--from) [ $# -ge 2 ] || { echo "usage: --from STAGE" >&2; exit 2; }; FROM=$2; shift ;;
	--only) [ $# -ge 2 ] || { echo "usage: --only STAGE[,STAGE]" >&2; exit 2; }; ONLY=$2; shift ;;
	-h|--help) sed -n '2,21p' "$0"; exit 0 ;;
	*) echo "s5-build: unknown argument $1 (see --help)" >&2; exit 2 ;;
	esac
	shift
done

stages=$(cd "$ST" && ls | grep -E '^[0-9][0-9]-[a-z0-9-]+\.sh$' | sort | sed 's/\.sh$//')
[ -n "$stages" ] || { echo "s5-build: no stages in $ST" >&2; exit 2; }

matches() {	# matches NAME LIST: 0 if NAME (or its number) is in the comma list
	for x in $(printf '%s' "$2" | tr ',' ' '); do
		case $1 in "$x"|"$x"-*) return 0 ;; esac
	done
	return 1
}
for x in $(printf '%s,%s' "$FROM" "$ONLY" | tr ',' ' '); do
	found=0
	for s in $stages; do matches "$s" "$x" && found=1; done
	[ $found = 1 ] || { echo "s5-build: no stage $x" >&2; exit 2; }
done

describe() {	# describe STAGE: the header lines
	f=$ST/$1.sh
	printf '%s  (as %s)\n' "$1" "$(sed -n 's/^# AS //p' "$f" | head -1)"
	printf '    what: %s\n' "$(sed -n 's/^# STAGE [^:]*: //p' "$f" | head -1)"
	printf '    in:   %s\n' "$(sed -n 's/^# IN //p' "$f" | head -1)"
	printf '    out:  %s\n' "$(sed -n 's/^# OUT //p' "$f" | head -1)"
}

if [ $DRY = 1 ]; then
	echo "s5-build.sh --dry-run: the stages in order (nothing is run; D32_ROOT=$D32_ROOT)"
	for s in $stages; do
		if [ -n "$ONLY" ] && ! matches "$s" "$ONLY"; then continue; fi
		describe "$s"
	done
	echo "markers: $D32_ROOT/state/<stage>.done; logs: $D32_ROOT/logs/<stage>.log; host must be $D32_HOST"
	exit 0
fi

if ! host_ok; then
	echo "s5-build: refused: this host is '$(hostname -s 2>/dev/null)' ($(uname -s)); disc 1 builds only on $D32_HOST" >&2
	exit 3
fi
old_vm_guard
[ "$(id -u)" = 0 ] || { echo "s5-build: run as root" >&2; exit 2; }
load_conf
mkdir -p "$D32_ROOT/state" "$D32_ROOT/logs" "$D32_ROOT/work" "$OUT"
RUNLOG=$D32_ROOT/logs/s5-build.log
say() { log "$*" | tee -a "$RUNLOG"; }
say "run: release $D32_RELEASE_ID, keys $D32_KEYSET, tree $D32_TREE_HASH${FROM:+, from $FROM}${ONLY:+, only $ONLY}"

started=0
for s in $stages; do
	if [ -n "$ONLY" ] && ! matches "$s" "$ONLY"; then continue; fi
	if [ -n "$FROM" ] && [ $started = 0 ]; then
		if matches "$s" "$FROM"; then started=1; else
			[ -f "$D32_ROOT/state/$s.done" ] || { say "$s: never finished, and --from starts later: run it first"; exit 1; }
			continue
		fi
	fi
	f=$ST/$s.sh
	stamp=$(stamp_of "$f")
	marker=$D32_ROOT/state/$s.done
	if [ $FORCE = 0 ] && [ -z "$FROM" ] && [ -z "$ONLY" ] && [ -f "$marker" ] && [ "$(cat "$marker")" = "$stamp" ]; then
		say "$s: done before with the same inputs, skipped"
		continue
	fi
	rm -f "$marker"
	# a stage that runs again makes every later stage's output stale (the packs hold the Library binary stage 50
	# made): their markers go too, so this run or the next one runs them again, also after --only
	later=0
	for t in $stages; do
		if [ "$t" = "$s" ]; then later=1; continue; fi
		[ $later = 0 ] || rm -f "$D32_ROOT/state/$t.done"
	done
	L=$D32_ROOT/logs/$s.log
	t0=$(date +%s)
	say "$s: start (log $L)"
	(
		set -e
		. "$ST/lib.sh"
		load_conf
		. "$f"
		run
	) >>"$L" 2>&1
	rc=$?
	t1=$(date +%s)
	if [ $rc -ne 0 ]; then
		say "$s: FAILED (exit $rc after $(( t1 - t0 )) s); last lines of $L:"
		tail -25 "$L"
		exit 1
	fi
	printf '%s\n' "$stamp" >"$marker"
	say "$s: done in $(( t1 - t0 )) s"
done
say "run finished: $OUT"
