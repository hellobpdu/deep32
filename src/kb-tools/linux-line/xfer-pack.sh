#!/bin/bash
# xfer-pack.sh: ON 901 as d32. Makes the forge's xfer pack (xfer_pack.py make) for the whole knowledge base or for
# one half of the two-card split (lever 10), then checks it (xfer_pack.py verify). Bulky: the pack holds the
# --no-dense root (about 1.5 GB for the full corpus) and the engine wheelhouse (about 0.5 GB).
# usage: xfer-pack.sh all|a|b GENERATION
#   all  one pack, every kept block (one card)
#   b    cisco (the friend's 3080 half: distribution-clean input only, never production or Windows legs)
# The root for a and b is the per-block --no-dense root built by s3-pre/build-no-dense.sh with the same split.
set -euo pipefail
. "$(dirname "$0")/s2/env.sh"
SPLIT=${1:?all|a|b}; GEN=${2:?generation}
S3=$D32_BASE/s3
python3 -I -B "$(dirname "$0")/xfer_pack.py" make --generation "$GEN" --root "$D32_S2_DATA/$GEN" \
  --corpus "$S3/corpus-$SPLIT" --engine "$D32_S2_CTX/engine/rag/core" --wheels "$D32_S2_CTX/wheelhouse/engine" \
  --lock "$D32_S2_CTX/locks/engine.lock" --blocks-dir "$S3/blocks" --blocks "$SPLIT" --out "$S3/xfer" \
  --embedding-revision "$D32_TEI_REVISION"
python3 -I -B "$(dirname "$0")/xfer_pack.py" verify --pack "$S3/xfer/$GEN"
du -sh "$S3/xfer/$GEN"
