#!/usr/bin/env bash
# g12.sh: gate G12 (contacts nothing of his) for a disc 1 test install, from 902's flow log of that install.
# The gate itself is T-0119's. Until T-0119 is accepted (its verdict file names ACCEPT), this hook only keeps the
# flow log with its sha256 and the install it belongs to, so the log can be replayed through T-0119's check later
# (build sequence 12: "v0 log replayed when T-0119 lands"). Once accepted, set G12_CMD to T-0119's command line with
# {log} and {report} where the flow log and the report go, and the hook runs it.
# Usage: bash g12.sh <flow-log> <out-dir> <release-id> <vmid>
#   VERDICT  default ${D32_GATE_ACCEPTANCE:?set D32_GATE_ACCEPTANCE}
# Exit: 0 passed or kept for later ("deferred" in the status file); 1 the gate failed; 2 usage.
set -euo pipefail
[ $# -eq 4 ] || { echo "usage: bash g12.sh <flow-log> <out-dir> <release-id> <vmid>" >&2; exit 2; }
log=$1; out=$2; rel=$3; vm=$4
[ -s "$log" ] || { echo "g12: no flow log at $log" >&2; exit 2; }
mkdir -p "$out"
keep="$out/flow-$rel-$vm.log"
cp -p "$log" "$keep"
sum=$(sha256sum "$keep" | cut -c1-64)
V=${VERDICT:-${D32_GATE_ACCEPTANCE:?set D32_GATE_ACCEPTANCE}}
status="$out/G12-$rel-$vm.status"
if [ ! -f "$V" ] || ! grep -Eq '^## Verdict: ACCEPT' "$V"; then
	printf 'deferred\t%s\t%s\tT-0119 not accepted yet; replay this log through its G12 check when it is\n' "$keep" "$sum" >"$status"
	echo "g12: deferred (T-0119 not accepted); flow log kept: $keep sha256 $sum"
	exit 0
fi
[ -n "${G12_CMD:-}" ] || { echo "g12: T-0119 is accepted: set G12_CMD to its command line ({log}, {report})" >&2; exit 2; }
report="$out/G12-$rel-$vm.json"
cmd=${G12_CMD//\{log\}/$keep}
cmd=${cmd//\{report\}/$report}
rc=0
eval "$cmd" || rc=$?
printf '%s\t%s\t%s\t%s\n' "$([ $rc = 0 ] && echo pass || echo fail)" "$keep" "$sum" "$report" >"$status"
echo "g12: exit $rc, report $report"
exit $rc
