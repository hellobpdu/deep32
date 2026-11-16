#!/bin/bash
# gen-c-resume.sh: resume after the completed generation-c no-dense build.
# The friend split uses rfc/cisco/powershell only, exactly as the original G2 split (the system block stays on the home build host). Shipped vectors: the 2080 Ti set. No vector is recomputed.
set -euo pipefail
L=$(cd "$(dirname "$0")/.." && pwd)
GEN=d32kb-20261115c
OLD=/srv/d32/s3/w2-d32kb-20261115b
G2=/srv/d32/w2/g2
W=/srv/d32/s3/w2-$GEN
JOIN=/srv/d32/s3/w2-merge-$GEN-t0157
COMPAT=/srv/d32/s3/compat.json
FRIEND=$OLD/sets/friend-2080ti/vectors.jsonl
. "${DEEP32_BUILD_ENV:?path to build environment settings}"
ROOT=$D32_S2_DATA/$GEN
for p in "$D32_S2_DATA/$GEN-donor" "$D32_S2_DATA/${GEN}r" "$JOIN"; do
  [ ! -e "$p" ] || { echo "REFUSE fresh rerun path exists: $p" >&2; exit 3; }
done
[ -f "$ROOT/BUILD_REPORT.json" ] || { echo "REFUSE no completed no-dense root" >&2; exit 3; }
rm -rf "$W/split"
echo "== inputs + split $(date -u +%FT%TZ)"
[ -f "$W/inputs.jsonl" ] || python3 -I -B "$L/w2/embed_inputs.py" --root "$ROOT" --out "$W/inputs.jsonl"
python3 -I -B "$L/w2/split_friend.py" --inputs "$W/inputs.jsonl" --root "$ROOT" \
  --block /srv/d32/s3/blocks/rfc --block /srv/d32/s3/blocks/cisco \
  --block /srv/d32/s3/blocks/powershell \
  --gate-dir "${DEEP32_FRIEND_GATE:?path to public friend-input gate tools}" --out "$W/split"
echo "== exact text equality with the embedded G2 inputs"
cmp "$G2/inputs.jsonl" "$W/inputs.jsonl"
cmp "$G2/split/friend.jsonl" "$W/split/friend.jsonl"
cmp "$G2/split/home.jsonl" "$W/split/home.jsonl"
cmp "$G2/split/cross.jsonl" "$W/split/cross.jsonl"
SETS=("$FRIEND")
HOME_ROWS=$(python3 -I -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["home"]["rows"])' "$W/split/split.json")
if [ "$HOME_ROWS" -gt 0 ]; then SETS+=("$OLD/sets/home/vectors.jsonl"); fi
echo "== flat $(date -u +%FT%TZ)"
python3 -I -B "$L/w2/flat_from_jsonl.py" --compat "$COMPAT" \
  --root "$ROOT" --inputs "$W/inputs.json" --f32-sse "$L/../kb-bundle" \
  --out "$W/flat-t0157" "${SETS[@]}"
echo "== merge dry run $(date -u +%FT%TZ)"
bash "$L/w2/merge-w2.sh" --compat "$COMPAT" --f32-sse "$L/../kb-bundle" \
  --dry-run --root "$ROOT" --work "$W/join-plan-t0157" \
  "$GEN" "$W/flat-t0157" "$W/inputs.json" "$W/split/split.json" \
  "$W/split/friend.jsonl" "$FRIEND" "$OLD/sets/cross/vectors.jsonl"
echo "== merge $(date -u +%FT%TZ)"
bash "$L/w2/merge-w2.sh" --compat "$COMPAT" --f32-sse "$L/../kb-bundle" \
  --work "$JOIN" \
  "$GEN" "$W/flat-t0157" "$W/inputs.json" "$W/split/split.json" \
  "$W/split/friend.jsonl" "$FRIEND" "$OLD/sets/cross/vectors.jsonl"
echo "== R1 + export + after-w1 $(date -u +%FT%TZ)"
bash "$L/s3-post/r1-after-w2.sh" "$GEN"
echo "== gen-c done $(date -u +%FT%TZ)"
