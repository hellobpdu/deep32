#!/bin/bash
# build-no-dense.sh: ON 901 as d32, after S2 (the engine image exists). Every CPU step of the real knowledge base
# at the NEUTRAL root /var/lib/deep32/rag-data/<GEN> (the path the forge's s3-dense job resumes; provenance is
# root-relative by engine transform 192, the the recorded build rule), from the corpus repository that
# corpus_from_blocks.py made from the kept blocks and the pruned derived layers.
# usage: build-no-dense.sh all|a|b GENERATION
#   all  every kept block (one-card W1); a = rfc, powershell, system; b = cisco (the two-card split, lever 10)
# Expects $D32_BASE/s3/corpus-<split> (a one-commit repository, ref refs/heads/corpus). Writes the root and
# $D32_BASE/s3/reports/no-dense-<GEN>.json (the build report). Rollback: sudo rm -rf the root folder.
set -euo pipefail
. "$(dirname "$0")/../s2/env.sh"
SPLIT=${1:?all|a|b}; GEN=${2:?generation}
S3=$D32_BASE/s3
[ -d "$S3/corpus-$SPLIT/.git" ] || { echo "REFUSE no corpus repository $S3/corpus-$SPLIT"; exit 2; }
[ ! -e "$D32_S2_DATA/$GEN" ] || { echo "REFUSE $D32_S2_DATA/$GEN exists (resume it with --resume by hand)"; exit 2; }
mkdir -p "$S3/reports"
"${D32_COMPOSE[@]}" run --rm --no-deps -v "$S3:/srv/d32/s3" deep32-engine env -u RAG_ROOT \
  python -I -B scripts/build_root.py --corpus-repo "/srv/d32/s3/corpus-$SPLIT" --corpus-ref refs/heads/corpus \
  --root "/var/lib/deep32/rag-data/$GEN" --no-dense --opt-in-doc-types powershell,system 2>&1 \
  | tee "$S3/reports/no-dense-$GEN.log" | tail -25
cp "$D32_S2_DATA/$GEN/BUILD_REPORT.json" "$S3/reports/no-dense-$GEN.json"
python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1])); print("status", r.get("status"), "documents", r.get("corpus",{}).get("documents"), "failed", r.get("failed_steps"))' \
  "$S3/reports/no-dense-$GEN.json"
