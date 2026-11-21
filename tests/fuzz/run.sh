#!/bin/sh
# SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
# SPDX-License-Identifier: Apache-2.0
#
# Runs the targets that build.sh built.
#
#   sh tests/fuzz/run.sh replay [TARGET...]          every seed in corpus/TARGET once, through OUT/replay-TARGET
#   sh tests/fuzz/run.sh fuzz SECONDS [TARGET...]    OUT/fuzz-TARGET for SECONDS seconds each, one after another
#
# The fuzzer starts from corpus/TARGET and keeps what it finds in OUT/corpus/TARGET (the checked-in seeds are only
# read). A crash, a sanitizer report, a failed FUZZ_ASSERT, an input that runs for 30 seconds or running out of
# memory stops the target and leaves the input in OUT/artifacts/TARGET/; run.sh prints the input in base64 so it can
# be recovered from a CI log, and exits 1 once every target has run. Each target's log is OUT/logs/TARGET.log.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
out=${OUT:-$here/out}
TARGETS="appjson asklink b64 d32json d32vec d32ww dib e26j e26p imgscale json8 kwaj netaddr pnm rfctext text8 wav"

maxlen() {
	case $1 in
	dib|kwaj|wav|pnm|d32ww|e26p) echo 65536 ;;
	*) echo 16384 ;;
	esac
}

dict() {
	case $1 in
	appjson|e26j|json8|d32json) echo "$here/dict/json.dict" ;;
	asklink) echo "$here/dict/asklink.dict" ;;
	netaddr) echo "$here/dict/netaddr.dict" ;;
	*) echo "" ;;
	esac
}

mode=${1:-}
case $mode in
replay) shift ;;
fuzz)
	shift
	secs=${1:-}
	case $secs in
	''|*[!0-9]*) echo "usage: run.sh fuzz SECONDS [TARGET...]" >&2; exit 2 ;;
	esac
	shift
	;;
*) echo "usage: run.sh replay [TARGET...] | run.sh fuzz SECONDS [TARGET...]" >&2; exit 2 ;;
esac
want=${*:-$TARGETS}
failed=""
mkdir -p "$out/logs"

# Symbolized reports need llvm-symbolizer; without it on PATH, a versioned one or binutils' addr2line will do.
if [ -z "${ASAN_SYMBOLIZER_PATH:-}" ] && ! command -v llvm-symbolizer >/dev/null 2>&1; then
	for s in llvm-symbolizer-21 llvm-symbolizer-20 llvm-symbolizer-19 llvm-symbolizer-18 llvm-symbolizer-17 addr2line; do
		if p=$(command -v "$s" 2>/dev/null); then
			ASAN_SYMBOLIZER_PATH=$p
			UBSAN_OPTIONS=${UBSAN_OPTIONS:+$UBSAN_OPTIONS:}external_symbolizer_path=$p
			export ASAN_SYMBOLIZER_PATH UBSAN_OPTIONS
			break
		fi
	done
fi

for t in $want; do
	if [ "$mode" = replay ]; then
		if "$out/replay-$t" "$here/corpus/$t" > "$out/logs/$t.replay.log" 2>&1; then
			echo "run.sh: $t: $(tail -n 1 "$out/logs/$t.replay.log")"
		else
			echo "run.sh: $t: replay FAILED"
			tail -n 40 "$out/logs/$t.replay.log"
			failed="$failed $t"
		fi
		continue
	fi
	mkdir -p "$out/corpus/$t" "$out/artifacts/$t"
	d=$(dict "$t")
	set -- -max_total_time="$secs" -timeout=30 -rss_limit_mb=2560 -max_len="$(maxlen "$t")" \
		-print_final_stats=1 -artifact_prefix="$out/artifacts/$t/"
	[ -n "$d" ] && set -- "$@" -dict="$d"
	rc=0
	"$out/fuzz-$t" "$@" "$out/corpus/$t" "$here/corpus/$t" > "$out/logs/$t.log" 2>&1 || rc=$?
	runs=$(sed -n 's/^stat::number_of_executed_units: *//p' "$out/logs/$t.log" | tail -n 1)
	last=$(grep -E '^#[0-9]+' "$out/logs/$t.log" | tail -n 1 || true)
	echo "run.sh: $t: exit $rc, ${runs:-?} runs; last status: ${last:-none}"
	if [ "$rc" -ne 0 ]; then
		failed="$failed $t"
		grep -E 'ERROR: |runtime error: |FUZZ_ASSERT|SUMMARY: ' "$out/logs/$t.log" | head -n 20 || true
		for a in "$out/artifacts/$t"/*; do
			[ -f "$a" ] || continue
			echo "run.sh: $t: input $(basename "$a"), base64:"
			base64 -w 0 < "$a"
			echo
		done
	fi
done

if [ -n "$failed" ]; then
	echo "run.sh: failed:$failed"
	exit 1
fi
echo "run.sh: all passed"
