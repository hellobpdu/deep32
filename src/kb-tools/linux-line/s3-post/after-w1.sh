#!/bin/bash
# after-w1.sh: ON 901 as d32, no card. S3 after W1 (the build schedule item 14):
#   P1 the W1 export from the forge (export_generation.py export there, its .tar.age copied here) unpacked and
#      verified; for the two-card split, merge-blocks.sh ran first and P1 exports the merged full root here
#   P2 the C3 bundle (the bundle tool's kb_bundle.py write, --public, with the real blocks; unsigned until the release
#      chain signs it: disc 2 is trusted through its signed manifest)
#   P3 Qdrant filled from the flat vectors (kb_qdrant.py load into the S2 qdrant-c3), check with exact queries
#   P4 G3 by query (only rfc, cisco, powershell, system; the private cut-key and marking lists on 901 only)
#   P5 known-item scores (the full set) and the neutral set scores, both with validate_root.py --no-dense off
#   P6 the 2,000-chunk CPU sample: select, embed through the CPU TEI on 901, compare with W1's vectors
# usage: after-w1.sh GEN EXPORT_TAR_AGE
set -euo pipefail
. "$(dirname "$0")/../s2/env.sh"
GEN=${1:?generation}; TAR=${2:?export .tar.age}
L=$D32_S2_CTX/lane
S3=$D32_BASE/s3
R=$S3/reports
K=$L/t0114/kb-bundle
SCRIPTS=$D32_S2_CTX/engine/rag/core/scripts
mkdir -p "$R" "$S3/gen" "$S3/bundles"
# D32_S3_SKIP_UNPACK=1: P1 already ran and passed for this generation (lead 2026-11-15: unpack + verify by hand)
if [ "${D32_S3_SKIP_UNPACK:-0}" = 1 ]; then
  grep -q 'verify: PASS' "$R/verify-$GEN.txt" || { echo "D32_S3_SKIP_UNPACK=1 but $R/verify-$GEN.txt has no PASS"; exit 3; }
else
age -d -i "$D32_S2_SECRETS/export.key" "$TAR" | python3 -I -B "$SCRIPTS/export_generation.py" unpack --into="$S3/gen"
python3 -I -B "$SCRIPTS/export_generation.py" verify --dir="$S3/gen/$GEN" | tee "$R/verify-$GEN.txt"
fi
BLOCKS=(); for b in rfc cisco powershell system; do BLOCKS+=(--block "$S3/blocks/$b/block.json"); done
python3 -I -B "$K/kb_bundle.py" write --generation-dir "$S3/gen/$GEN" --out "$S3/bundles" --created "$D32_S3_CREATED" \
  --compat "$S3/compat.json" --public --unsigned "${BLOCKS[@]}" \
  --cut-keys "$D32_S2_SECRETS/cut-keys.txt" --markings "$D32_S2_SECRETS/markings.txt" --json --report "$R/bundle-$GEN.json"
python3 -I -B "$K/kb_bundle.py" check --dir "$S3/bundles/$GEN" --deep --public --allow-unsigned "${BLOCKS[@]}" \
  --cut-keys "$D32_S2_SECRETS/cut-keys.txt" --markings "$D32_S2_SECRETS/markings.txt" --json --report "$R/bundle-check-$GEN.json"
python3 -I -B "$K/kb_qdrant.py" load --bundle "$S3/bundles/$GEN" --qdrant-url "$D32_QDRANT_C3_URL" --allow-unsigned \
  --json --report "$R/qdrant-load-$GEN.json"
python3 -I -B "$K/kb_qdrant.py" check --bundle "$S3/bundles/$GEN" --qdrant-url "$D32_QDRANT_C3_URL" --allow-unsigned \
  --json --report "$R/qdrant-check-$GEN.json"
python3 -I -B "$L/s3-post/g3_query.py" "$S3/bundles/$GEN" --cut-keys "$D32_S2_SECRETS/cut-keys.txt" \
  --markings "$D32_S2_SECRETS/markings.txt" --expect "rfc=365,cisco=489,powershell=241,system=$D32_SYSTEM_DOCS" \
  --json | tee "$R/g3-$GEN.json"
echo "P5 and P6: the build procedure S3-A5 and S3-A6 (they need the served root and the CPU TEI)"
