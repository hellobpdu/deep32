#!/usr/bin/env bash
# run-g1g2.sh: gates G1 (restricted wording) and G2 (secrets) over disc 1's medium parts, looking inside archives.
# The parts are stage 88-scan-parts' output (/build/dist/out/<release>/scan: site80.tgz, the signed packs, PACKS.tsv,
# README.txt, the ramdisk kernel bsd, boot.conf), copied by the lead to build-controller (D:) or to the store VM 945.
# The term lists are private: run it only where release-checks/terms is (build-controller, or 945 during a disc scan).
# Usage: bash run-g1g2.sh <scan-dir> <report.json> [more run_gates.py arguments]
#   PYTHON  default python3 (on 945: python3)
#   GATES   default ${D32_GATE_RUNNER:?set D32_GATE_RUNNER}
# The report records the scan folder's SHA256 file hash next to it (<report>.parts) so it is tied to one build.
# Exit: run_gates.py's own (0 pass, 1 a gate failed, 2 usage).
set -euo pipefail
[ $# -ge 2 ] || { echo "usage: bash run-g1g2.sh <scan-dir> <report.json> [run_gates args]" >&2; exit 2; }
scan=$1; report=$2; shift 2
P=${PYTHON:-python3}
G=${GATES:-${D32_GATE_RUNNER:?set D32_GATE_RUNNER}}
[ -f "$scan/SHA256" ] || { echo "run-g1g2: $scan has no SHA256 (stage 88-scan-parts writes it)" >&2; exit 2; }
tmp=$(dirname "$report")/g1g2-tmp
mkdir -p "$tmp"
sha256sum "$scan/SHA256" | cut -c1-64 > "$report.parts"
rc=0
"$P" -I -B "$G" --tree "$scan" --gates G1,G2 --archives --tmp "$tmp" --report "$report" "$@" || rc=$?
rmdir "$tmp" 2>/dev/null || true
echo "run-g1g2: exit $rc, report $report (parts list sha256 $(cat "$report.parts"))"
exit $rc
