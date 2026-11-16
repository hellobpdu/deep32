#!/bin/bash
# IN THE FORGE (d32-dist-forge), as root, OUTSIDE a GPU window, no card used: nothing is embedded.
#   1. derive_root: the W1 root ($ROOT of /mnt/xfer/s3/job.env) re-derived as <GEN_R> with production's served
#      candidates 15, max_chars 1600, a centred window and search_many_candidates 10, the regex grep engine and the
#      identical-passage fold. The rerank URL is the neutral loopback default the Workstation's engine serves on;
#      derive_root only records it (nothing is sent; RAG_RERANK_URL still overrides it per install). Its dense step
#      copies W1's vectors into the derived root's own collection on the forge's Qdrant (16333, never production's).
#   2. export_generation export, encrypted with age to RECIPIENT (the public half of 901's export key: on 901,
#      age-keygen -y /srv/d32/s2/secrets/export.key), into a 0700 folder inside the forge (export_generation refuses
#      any out-dir not owned by root with mode 0700, and the drvfs transfer folder is always 0777), then copied to
#      /mnt/xfer/s3/export for after-w1.sh on 901 (the files are age-encrypted, so the copy reveals nothing).
# Resumable: when <GEN_R>'s root exists with a BUILD_REPORT.json, the derive is skipped and only checked (R1) and
# exported. Refuses when age is missing. Lead, 2026-11-15 (10:30: the 0700 out-dir and resume after the first run).
set -euo pipefail
GEN_R=${1:?derived generation, e.g. d32kb-20261115r}
RCPT=${2:?the age recipient (age1...) of the export key on 901}
. "${DEEP32_FORGE_PINS:?path to pinned forge settings}"
. "${DEEP32_BUILD_JOB:?path to build job settings}"
FROM=${ROOT:?}
TO=/var/lib/deep32/rag-data/$GEN_R
OUT=/mnt/xfer/s3/export
LOCAL_OUT=/var/lib/deep32/export-out/$GEN_R
command -v age >/dev/null || { echo "REFUSE age is not installed in the forge"; exit 2; }
case "$RCPT" in age1*) ;; *) echo "REFUSE the recipient is not an age public key"; exit 2;; esac
mkdir -p "$OUT"
rm -rf "$LOCAL_OUT"; install -d -m 0700 "$(dirname "$LOCAL_OUT")"; install -d -m 0700 "$LOCAL_OUT"
docker start d32f-qdrant >/dev/null
for _ in $(seq 1 90); do curl -fs -m 2 "http://127.0.0.1:${QDRANT_PORT}/healthz" >/dev/null && break; sleep 1; done
curl -fs -m 2 "http://127.0.0.1:${QDRANT_PORT}/healthz" >/dev/null || { echo "Qdrant on ${QDRANT_PORT} did not come up"; exit 1; }
cd "${ENGINE_DIR:?}"
unset RAG_ROOT RAG_EMBEDDING_URL RAG_RERANK_URL
export RAG_QDRANT_URL="http://127.0.0.1:${QDRANT_PORT}" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONHASHSEED=0
if [ -f "$TO/BUILD_REPORT.json" ]; then echo "derive: $TO exists with a BUILD_REPORT.json: derive skipped (resume)"
elif [ -e "$TO" ]; then echo "REFUSE $TO exists without a BUILD_REPORT.json (a broken derive): delete it first"; exit 2
else
echo "derive: $FROM -> $TO"
.venv/bin/python -I -B scripts/derive_root.py --from "$FROM" --to "$TO" \
  --rerank-url http://127.0.0.1:8082/v1/rerank \
  --set retrieval.grep.regex_engine=true \
  --set retrieval.reranker.candidates=15 \
  --set retrieval.reranker.max_chars=1600 \
  --set retrieval.reranker.window=centred \
  --set retrieval.reranker.search_many_candidates=10 \
  --set retrieval.fold_identical=true
fi
python3 -I - "$TO/config/retrieval.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
rr = r.get("reranker") or {}
want = {"enabled": True, "candidates": 15, "max_chars": 1600, "window": "centred", "search_many_candidates": 10}
bad = {k: rr.get(k) for k, v in want.items() if rr.get(k) != v}
if (r.get("grep") or {}).get("regex_engine") is not True: bad["grep.regex_engine"] = (r.get("grep") or {}).get("regex_engine")
if r.get("fold_identical") is not True: bad["fold_identical"] = r.get("fold_identical")
print("R1 retrieval.json:", "OK" if not bad else "WRONG %r" % bad)
sys.exit(1 if bad else 0)
PY
FP=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["corpus"]["manifest_fingerprint"])' "$TO/BUILD_REPORT.json")
echo "export: $GEN_R fingerprint $FP"
.venv/bin/python -I -B scripts/export_generation.py export --root="$TO" --expect-generation="$GEN_R" \
  --expect-fingerprint="$FP" --recipient="$RCPT" --out-dir="$LOCAL_OUT" --not-served-ok
cp -r "$LOCAL_OUT"/. "$OUT"/
(cd "$LOCAL_OUT" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > /tmp/r1-local.sha
(cd "$OUT" && sha256sum -c /tmp/r1-local.sha >/dev/null) || { echo "the copy in $OUT does not match the forge copy"; exit 1; }
echo "copied to $OUT: $(wc -l </tmp/r1-local.sha) files, hashes equal"
ls -la "$OUT"
