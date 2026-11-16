#!/bin/bash
# r1-after-w2.sh: the disc 2 derivation procedure for the W2-joined generation on the build host.
# Runs the reviewed build-host equivalent of derive-export: R1 derive with the served retrieval settings, the
# encrypted export to the export key's public recipient, then after-w1 (unpack/verify, public bundle, C3 Qdrant, G3).
set -euo pipefail
L=$(cd "$(dirname "$0")/.." && pwd)
[ "$#" -le 1 ] || { echo "usage: r1-after-w2.sh [GENERATION]" >&2; exit 2; }
GEN=${1:-d32kb-20261115c}
[[ "$GEN" =~ ^[a-z0-9][a-z0-9._-]{0,33}$ ]] || { echo "REFUSE invalid W2 generation" >&2; exit 2; }
W=/srv/d32/s3/w2-$GEN
. "${DEEP32_BUILD_ENV:?path to build environment settings}"
export GEN_R=${GEN}r
echo "== R1 derive $GEN -> $GEN_R $(date -u +%FT%TZ)"
"${D32_COMPOSE[@]}" run --rm --no-deps -e RAG_EMBEDDING_URL=http://127.0.0.1:9/v1/embeddings \
  -e RAG_EMBEDDING_REVISION="$D32_TEI_REVISION" -v /srv/d32/s3:/srv/d32/s3 deep32-engine \
  env -u RAG_ROOT python -I -B scripts/derive_root.py \
  --from "/var/lib/deep32/rag-data/$GEN" --to "/var/lib/deep32/rag-data/$GEN_R" \
  --rerank-url http://127.0.0.1:8082/v1/rerank \
  --set retrieval.grep.regex_engine=true --set retrieval.reranker.candidates=15 \
  --set retrieval.reranker.max_chars=1600 --set retrieval.reranker.window=centred \
  --set retrieval.reranker.search_many_candidates=10 --set retrieval.fold_identical=true
FP=$(python3 -I -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["corpus"]["manifest_fingerprint"])' \
  "/var/lib/deep32/rag-data/$GEN_R/BUILD_REPORT.json")
echo "== fingerprint $FP; export $(date -u +%FT%TZ)"
RCPT=$(age-keygen -y "$D32_S2_SECRETS/export.key")
install -d -m 0700 "$W/r1-export"
"${D32_COMPOSE[@]}" run --rm --no-deps -v /srv/d32/s3:/srv/d32/s3 deep32-engine \
  env -u RAG_ROOT python -I -B scripts/export_generation.py export \
  --root="/var/lib/deep32/rag-data/$GEN_R" --expect-generation="$GEN_R" --expect-fingerprint="$FP" \
  --recipient="$RCPT" --out-dir="$W/r1-export" --not-served-ok
export D32_S3_CREATED=2026-11-15T18:30:00Z
export D32_SYSTEM_DOCS=85
export D32_S3_SKIP_UNPACK=0
echo "== after-w1 $(date -u +%FT%TZ)"
bash "$L/s3-post/after-w1.sh" "$GEN_R" "$W/r1-export/export-$FP.tar.age"
echo "== done $GEN_R fingerprint $FP $(date -u +%FT%TZ)"
