#!/bin/bash
# On the build host, as its ordinary build user. No embedding takes place.
# Usage: merge-w2.sh [--dry-run --root ROOT] [--work DIR --python PYTHON] \
#          [--f32-sse DIR --compat COMPAT.json] \
#          GEN FLAT inputs.json split.json friend.jsonl friend-vectors.jsonl cross-vectors.jsonl
# Dry run validates full coverage, text provenance, flat hashes, the exact
# deterministic 500 cross rows and their scores. It never sources build environment settings,
# contacts a service, creates a donor or runs the engine.
set -euo pipefail
DRY=0
ROOT=
WORK=
PY=python3
COMPAT=/srv/d32/s3/compat.json
F32=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --root) ROOT=${2:?root}; shift 2 ;;
    --work) WORK=${2:?work folder}; shift 2 ;;
    --python) PY=${2:?Python executable}; shift 2 ;;
    --compat) COMPAT=${2:?compat.json}; shift 2 ;;
    --f32-sse) F32=(--f32-sse "${2:?f32_sse folder}"); shift 2 ;;
    --) shift; break ;;
    -*) echo "REFUSE unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ "$#" = 7 ] || { echo "usage: merge-w2.sh [--dry-run --root ROOT] [--work DIR --python PYTHON --compat COMPAT.json --f32-sse DIR] GEN FLAT inputs.json split.json friend.jsonl friend-vectors.jsonl cross-vectors.jsonl" >&2; exit 2; }
GEN=$1; M=$2; INPUTS=$3; SPLIT=$4; FRIEND=$5; FRIEND_VECTORS=$6; CROSS=$7
[[ "$GEN" =~ ^[a-z0-9][a-z0-9._-]{0,33}$ ]] || { echo "REFUSE invalid generation (allow room for -donor)" >&2; exit 2; }
L=$(cd "$(dirname "$0")/.." && pwd)
if [ "$DRY" = 0 ]; then
  [ -z "$ROOT" ] || { echo "REFUSE --root is a dry-run option" >&2; exit 2; }
  . "${DEEP32_BUILD_ENV:?path to build environment settings}"
  ROOT=$D32_S2_DATA/$GEN
  WORK=${WORK:-$D32_BASE/s3/w2-merge-$GEN}
  # The engine's export can write only inside the mounted S3 tree.
  CONTAINER_WORK=$("$PY" -I -B - "$D32_BASE/s3" "$WORK" <<'PY'
from pathlib import Path
import sys
base, work = (Path(p).resolve() for p in sys.argv[1:])
try:
    relative = work.relative_to(base)
except ValueError:
    raise SystemExit("REFUSE live work folder must be inside the mounted S3 tree")
if not relative.parts:
    raise SystemExit("REFUSE live work folder must be a new child of the S3 tree")
print((Path("/srv/d32/s3") / relative).as_posix())
PY
)
else
  [ -n "$ROOT" ] && [ -n "$WORK" ] || { echo "REFUSE dry run needs --root and --work" >&2; exit 2; }
fi
[ ! -e "$WORK" ] || { echo "REFUSE work folder exists; retain it as a run record and choose a new folder" >&2; exit 3; }
mkdir -p "$WORK"
"$PY" -I -B "$L/w2/flat_from_jsonl.py" "${F32[@]}" --compat "$COMPAT" --root "$ROOT" --inputs "$INPUTS" --verify-flat "$M" \
  --friend-inputs "$FRIEND" --cross-vectors "$CROSS" --cross-out "$WORK/cross-checked.jsonl" \
  --split-manifest "$SPLIT" --friend-vectors "$FRIEND_VECTORS" \
  > "$WORK/cross-inputs.json"
"$PY" -I -B "$L/compare-vectors.py" "$WORK/cross-checked.jsonl" "$M" --subset \
  --tolerance 1e-4 --json --report "$WORK/cross-device.json"
# The source flat set must be from exactly this generation. The comparison
# report records min cosine, self drift, component and top-k score differences.
"$PY" -I -B - "$M/MERGE.json" "$GEN" <<'PY'
import json, sys
r = json.load(open(sys.argv[1], encoding="ascii"))
if r["generation"] != sys.argv[2]:
    raise SystemExit("REFUSE flat generation differs from requested generation")
if not r["embedding_revision"]:
    raise SystemExit("REFUSE missing embedding revision")
PY
DONOR=$GEN-donor
if [ "$DRY" = 1 ]; then
  "$PY" -I -B - "$WORK/PLAN.json" "$GEN" "$DONOR" <<'PY'
import json, sys
p = {"dry_run": True, "generation": sys.argv[2], "donor": sys.argv[3],
     "stages": ["X2 make_donor", "X3 load_flat --readback all", "X4 build_root --resume --reuse-dense-from",
                "X5 export, unpack, verify, compare identical"],
     "embedding_url": "http://127.0.0.1:9/v1/embeddings", "network_calls": 0,
     "cross_rows": 500, "cross_score_tolerance": 0.0001}
with open(sys.argv[1], "w", encoding="ascii", newline="\n") as f:
    json.dump(p, f, sort_keys=True, indent=1); f.write("\n")
PY
  echo "dry-run checked: $WORK/PLAN.json and $WORK/cross-device.json; X2-X5 were not run"
  exit 0
fi
REV=$("$PY" -I -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["embedding_revision"])' "$M/MERGE.json")
[ "$REV" = "$D32_TEI_REVISION" ] || { echo "REFUSE vector revision differs from the build pin" >&2; exit 3; }
# X2: the existing donor writer copies the root with the SQLite backup API.
"$PY" -I -B "$L/merge/make_donor.py" --root "$ROOT" --merged "$M" --donor "$D32_S2_DATA/$DONOR" \
  --inputs "$INPUTS" --compat "$COMPAT" --json \
  | tee "$WORK/donor.json"
# X3: every point and its payload must survive Qdrant bit for bit.
"$PY" -I -B "$L/merge/load_flat.py" --merged "$M" --collection "rag_dense_$DONOR" \
  --qdrant-url "$D32_QDRANT_URL" --readback all --json | tee "$WORK/load.json"
# X4: verify port 9 is closed in the same container, then exec the builder.
# Any embedding attempt fails; no TEI service is started by this script.
"${D32_COMPOSE[@]}" run --rm --no-deps -e RAG_EMBEDDING_URL=http://127.0.0.1:9/v1/embeddings \
  -e RAG_EMBEDDING_REVISION="$REV" -v "$D32_BASE/s3:/srv/d32/s3" deep32-engine \
  env -u RAG_ROOT python -I -B -c '
import os, socket, sys
with socket.socket() as s:
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", 9))
    except ConnectionRefusedError:
        pass
    else:
        raise SystemExit("REFUSE embedding port 9 is open")
os.execv(sys.executable, [sys.executable, "-I", "-B", *sys.argv[1:]])
' scripts/build_root.py --corpus-repo /srv/d32/s3/corpus-all \
  --corpus-ref refs/heads/corpus --root "/var/lib/deep32/rag-data/$GEN" --resume \
  --reuse-dense-from "$DONOR" --opt-in-doc-types powershell,system 2>&1 | tee "$WORK/build.log"
"$PY" -I -B - "$ROOT/BUILD_REPORT.json" "$M/MERGE.json" "$DONOR" "$WORK/build-check.json" \
  "$INPUTS" "$COMPAT" "$L/w2/embedding_identity.py" <<'PY'
import importlib.util, json, sys
from pathlib import Path
r, m = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])
spec = importlib.util.spec_from_file_location("build_embedding_identity", sys.argv[7])
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
embedding = identity.check_merge(m, json.load(open(sys.argv[5], encoding="utf-8")), Path(sys.argv[6]))
d = r.get("dense") or {}
reuse = d.get("reuse") or {}
n = m["rows"]
if (r.get("status") != "complete" or r.get("failed_steps") or d.get("points") != n
        or (d.get("index") or {}).get("points") != n
        or reuse.get("from_generation") != sys.argv[3] or reuse.get("reused") != n
        or reuse.get("embedded") != 0 or d.get("embedding_model") != embedding["served_name"]
        or d.get("embedding_revision") != embedding["revision"]):
    raise SystemExit("REFUSE final build report does not prove every vector reused and embedded 0")
with open(sys.argv[4], "w", encoding="ascii", newline="\n") as f:
    json.dump({"status": r["status"], "points": n, "reuse": reuse, "index": d["index"],
               "model_id": embedding["model_id"], "embedding_model": d["embedding_model"],
               "embedding_revision": d["embedding_revision"]}, f, sort_keys=True)
    f.write("\n")
PY
# X5: export now, unpack and verify it, then demand exact vector identity.
FP=$("$PY" -I -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["corpus_fingerprint"])' "$M/MERGE.json")
install -d -m 0700 "$WORK/export"
RCPT=$(age-keygen -y "$D32_S2_SECRETS/export.key")
"${D32_COMPOSE[@]}" run --rm --no-deps -v "$D32_BASE/s3:/srv/d32/s3" deep32-engine \
  env -u RAG_ROOT python -I -B scripts/export_generation.py export \
  --root="/var/lib/deep32/rag-data/$GEN" --expect-generation="$GEN" --expect-fingerprint="$FP" \
  --recipient="$RCPT" --out-dir="$CONTAINER_WORK/export" --not-served-ok
ARCHIVES=("$WORK/export"/export-*.tar.age)
[ "${#ARCHIVES[@]}" = 1 ] && [ -f "${ARCHIVES[0]}" ] || { echo "REFUSE expected one new encrypted export" >&2; exit 3; }
SCRIPTS=$D32_S2_CTX/engine/rag/core/scripts
age -d -i "$D32_S2_SECRETS/export.key" "${ARCHIVES[0]}" \
  | "$PY" -I -B "$SCRIPTS/export_generation.py" unpack --into="$WORK/gen" --expect-generation="$GEN"
"$PY" -I -B "$SCRIPTS/export_generation.py" verify --dir="$WORK/gen/$GEN" | tee "$WORK/export-verify.txt"
"$PY" -I -B "$L/compare-vectors.py" "$M" "$WORK/gen/$GEN" --json --report "$WORK/exact-vectors.json"
"$PY" -I -B - "$WORK/exact-vectors.json" "$M/MERGE.json" <<'PY'
import json, sys
r, m = (json.load(open(p, encoding="ascii")) for p in sys.argv[1:])
if r["verdict"] != "identical" or r["compared"] != m["rows"]:
    raise SystemExit("REFUSE final export is not identical to the joined vectors")
PY
echo "W2 join passed: $WORK; retain donor and reports until the lead verifies and records the run"
