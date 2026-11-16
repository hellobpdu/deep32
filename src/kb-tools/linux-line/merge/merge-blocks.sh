#!/bin/bash
# merge-blocks.sh: ON 901 as d32, after both halves of a two-card W1 (lever 10) came back as unpacked generation
# exports (export_generation.py export in each forge, unpack on 901). Joins them into the full generation through
# build_root.py's own --reuse-dense-from: nothing is embedded here.
#   X1 merge_flat.py   the two halves' flat vectors, checked against the full --no-dense root (same chunk ids,
#                      documents and embedding texts; one model and revision)
#   X2 make_donor.py   the donor root <GEN>-donor beside the full root (a copy of its rag.db, marked dense)
#   X3 load_flat.py    the donor's Qdrant collection rag_dense_<GEN>-donor in the S2 Qdrant, read back bit for bit
#   X4 build_root.py --resume --reuse-dense-from <GEN>-donor on the full root, with the embedding address on a
#                      closed loopback port (an attempt to embed fails the run instead of embedding)
#   X5 export the full generation and compare-vectors.py it with the merged files: expected "identical"
# usage: merge-blocks.sh GEN HALF_A_GEN_DIR HALF_B_GEN_DIR
# Rollback: delete <GEN>-donor and its collection; the full root keeps its --no-dense state until X4 succeeds,
# and X4 can be rerun (build_root resumes).
set -euo pipefail
. "$(dirname "$0")/../s2/env.sh"
GEN=${1:?full generation}; A=${2:?half a}; B=${3:?half b}
L=$D32_S2_CTX/lane
M=$D32_BASE/s3/merge-$GEN
DONOR=$GEN-donor
mkdir -p "$D32_BASE/s3/reports"
python3 -I -B "$L/merge/merge_flat.py" --out "$M" --expect-root "$D32_S2_DATA/$GEN" --json "$A" "$B" \
  | tee "$D32_BASE/s3/reports/merge-$GEN.json"
# the revision the halves recorded (s3-dense.sh exports it from the pack's job.env): build_root's --reuse-dense-from
# refuses a donor whose revision is not the one this run writes, and kb_bundle refuses a NULL one
REV=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["embedding_revision"] or "")' "$M/MERGE.json")
[ -n "$REV" ] || { echo "STOP: the halves carry no embedding revision (W1 ran without RAG_EMBEDDING_REVISION; make the packs with xfer-pack.sh, whose job.env exports it)"; exit 1; }
[ "$REV" = "$D32_TEI_REVISION" ] || { echo "STOP: the halves were embedded at revision $REV, env.sh names $D32_TEI_REVISION"; exit 1; }
python3 -I -B "$L/merge/make_donor.py" --root "$D32_S2_DATA/$GEN" --merged "$M" --donor "$D32_S2_DATA/$DONOR" --json
python3 -I -B "$L/merge/load_flat.py" --merged "$M" --collection "rag_dense_$DONOR" --qdrant-url "$D32_QDRANT_URL" --json
"${D32_COMPOSE[@]}" run --rm --no-deps -e RAG_EMBEDDING_URL=http://127.0.0.1:9/v1/embeddings \
  -e RAG_EMBEDDING_REVISION="$REV" -v "$D32_BASE/s3:/srv/d32/s3" deep32-engine \
  env -u RAG_ROOT python -I -B scripts/build_root.py --corpus-repo "/srv/d32/s3/corpus-all" --corpus-ref refs/heads/corpus \
  --root "/var/lib/deep32/rag-data/$GEN" --resume --reuse-dense-from "$DONOR" --opt-in-doc-types powershell,system 2>&1 \
  | tee "$D32_BASE/s3/reports/merge-build-$GEN.log" | tail -15
grep -E "reused [0-9]+, embedded 0" "$D32_BASE/s3/reports/merge-build-$GEN.log" | tail -1 \
  || { echo "STOP: the merge run embedded chunks or did not finish (see the log)"; exit 1; }
echo "next: export $GEN (after-w1.sh step P2) and compare-vectors.py <export> $M (expected: identical)"
