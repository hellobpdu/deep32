#!/bin/bash
# stage-context.sh: ON 901 as d32. Assembles the S2 build context from what the lead copied to 901:
#   $IN/lane            this lane folder (work/opus/s2s3-linux-line)
#   $IN/stub-models     work/engine/stub-models
#   $IN/t0114           the bundle-tool source tree/{kb-bundle,fixture} (read: kb_bundle.py, kb_qdrant.py, f32_sse.py)
# and applies the engine transforms 190-193 to the context's copy of the export (never to $IN).
# usage: stage-context.sh IN CTX      (CTX must not exist)
set -euo pipefail
IN=${1:?IN}; CTX=${2:?CTX}
[ ! -e "$CTX" ] || { echo "REFUSE $CTX exists"; exit 2; }
for d in lane engine-export stub-models t0114; do [ -d "$IN/$d" ] || { echo "REFUSE $IN/$d missing"; exit 2; }; done
mkdir -p "$CTX"
cp -a "$IN/lane" "$CTX/lane"
cp -a "$IN/engine-export" "$CTX/engine"
cp -a "$IN/stub-models" "$CTX/stub-models"
mkdir -p "$CTX/lane/t0114" && cp -a "$IN/t0114/kb-bundle" "$CTX/lane/t0114/kb-bundle"
mkdir -p "$CTX/compose" && cp -a "$IN/lane/compose/." "$CTX/compose/"
python3 -I -B "$CTX/lane/engine-transforms/apply_all.py" "$CTX/engine"
python3 -I -B "$CTX/lane/s3-pre/engine_cut_check.py" "$CTX/engine"
find "$CTX" -name __pycache__ -prune -exec rm -rf {} +
echo "context ready: $CTX"
