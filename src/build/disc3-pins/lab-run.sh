#!/usr/bin/env bash
# What: run resolve-pins.py on the lab build VM, one family at a time in the working order, then --check.
# Usage: lab-run.sh KIT OUT LEAD_INPUTS [FAMILY ...]   (no FAMILY: every family in the order below)
# Exit codes: 0 every family resolved and --check passed; 3 some pins stay TO-PIN (see EVIDENCE.json);
#   4 an upstream value changed (decide with --force-family); 2 usage or input error.
# Re-running is safe: finished items are kept, only TO-PIN values are filled. Needs python3, gpg, gpgv, docker.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if (($# < 3)); then
  sed -n '2,6p' "${BASH_SOURCE[0]}" >&2
  exit 2
fi
kit=$1 out=$2 lead=$3
shift 3
families=("$@")
((${#families[@]})) || families=(lead keys iso tei base models tools apt module locks licences)
status=0
for family in "${families[@]}"; do
  started=$(date +%s)
  set +e
  python3 -I -B "$here/resolve-pins.py" --kit "$kit" --out "$out" --lead-inputs "$lead" --family "$family"
  code=$?
  set -e
  echo "FAMILY $family exit $code after $(( $(date +%s) - started )) s; free on /: $(df -h --output=avail / | tail -n 1)"
  case $code in
    0) ;;
    3) status=3 ;;
    *) exit "$code" ;;
  esac
done
set +e
python3 -I -B "$here/resolve-pins.py" --kit "$kit" --out "$out" --check
code=$?
set -e
echo "CHECK exit $code"
if ((code != 0)); then
  exit "$code"
fi
exit "$status"
