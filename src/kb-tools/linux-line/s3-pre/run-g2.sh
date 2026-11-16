#!/bin/bash
# run-g2.sh: ON 901 as d32. Generation 2 of disc 2's knowledge base (lead, 2026-11-15 ~14:00): the same S3-P4..P6
# steps as G1 (the build procedure) with system block v2 (the recorded build: 85 documents, public names, no third-party docs).
# G1's folders are kept beside it (*-g1); nothing of G1 is deleted. Dense is NOT done here: the vectors come from
# usage: run-g2.sh GEN CREATED      e.g. run-g2.sh d32kb-20261115b 2026-11-15T18:30:00Z
set -euo pipefail
GEN=${1:?generation}; CREATED=${2:?created time, UTC, recorded in STATE}
. "${DEEP32_BUILD_ENV:?path to build environment settings}"
L=/srv/d32/s2/ctx/lane
S3=$D32_BASE/s3
V2=$S3/xfer/system-v2/system
[ -f "$V2/block.json" ] || { echo "no system block v2 at $V2"; exit 2; }
[ ! -e /var/lib/deep32/rag-data/$GEN ] || { echo "REFUSE /var/lib/deep32/rag-data/$GEN exists"; exit 2; }
say() { echo "[run-g2 $(date -u +%H:%M:%S)] $*"; }

say "keep G1's folders"
for d in corpus-plain corpus-all kb-work; do
  [ -e "$S3/$d-g1" ] || mv "$S3/$d" "$S3/$d-g1"
done
[ -e "$S3/derived-g1.sh" ] || mv "$S3/derived.sh" "$S3/derived-g1.sh"
[ -e "$S3/blocks-system-v1" ] || mv "$S3/blocks/system" "$S3/blocks-system-v1"
rm -rf "$S3/blocks/system"; cp -a "$V2" "$S3/blocks/system"
# (block v2 was validated by the recorded build: validate_block.py exit 0; its SHA256SUMS were checked on arrival)

say "S3-P4 corpus folder"
python3 -I -B "$L/s3-pre/corpus_from_blocks.py" --out "$S3/corpus-plain" "$S3"/blocks/{rfc,cisco,powershell,system}
say "S3-P4 derived layers script"
# --ise-messages takes the DOCUMENT KEY; the build procedure S3-P4's kb-v8.1 form refuses: "retiring a document needs a new base")
python3 -I -B "$L/s3-pre/derived_layers.py" script --kit /opt/d32-kb-kit --corpus "$S3/corpus-plain" --raw "$S3/raw" \
  --base "$S3/kb-base-kept" --base-sha256 b18cf0d32c23bc39 --styles /opt/d32-kb-kit/styles --work "$S3/kb-work" \
  --tools "$L/s3-pre" --ise-messages ise31-syslog-messages --out "$S3/derived.sh"
say "S3-P4 derived layers run"
bash "$S3/derived.sh"
say "S3-P5 corpus repository (created $CREATED)"
python3 -I -B "$L/s3-pre/corpus_from_blocks.py" --out "$S3/corpus-all" --kb "$S3/kb-work/pruned" --repo --created "$CREATED" \
  "$S3"/blocks/{rfc,cisco,powershell,system}
git -C "$S3/corpus-all" log -1 --format='corpus-all commit %H %cI'
say "S3-P6 the --no-dense root $GEN"
bash "$L/s3-pre/build-no-dense.sh" all "$GEN"
say "done"
