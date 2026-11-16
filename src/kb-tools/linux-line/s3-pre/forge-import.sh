#!/bin/bash
# outside any GPU window. Verifies an xfer pack, then lays it out where forge/jobs/s3-dense.sh expects it:
#   engine        -> /opt/d32f/s3/engine, with .venv built from the pack's wheels (--no-index --require-hashes)
#   corpus.bundle -> /opt/d32f/s3/corpus.git (git clone --bare of the bundle; the ref stays refs/heads/corpus)
#   root.tar      -> /var/lib/deep32/rag-data/<GEN> (the same neutral path it was built at on 901)
#   job.env       -> /mnt/xfer/s3/job.env
# usage: forge-import.sh PACK_DIR
# Refuses when the pack does not verify, or when any target exists. Rollback: remove the four targets.
set -euo pipefail
P=${1:?pack dir}
# job.env must land in the transfer folder itself (xfer\s3\job.env), where s3-dense.sh reads it during the window:
# a file written under an unmounted /mnt/xfer is hidden by the window's drvfs mount (forge-check.sh --open)
MNT=${D32F_XFER_MNT:-/mnt/xfer}
mountpoint -q "$MNT" || { echo "REFUSE $MNT is not mounted: mount the transfer folder first (as forge-provision does)"; exit 2; }
python3 -I -B "$(dirname "$0")/../xfer_pack.py" verify --pack "$P"
GEN=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"])' "$P/PACK.json")
for t in /opt/d32f/s3/engine /opt/d32f/s3/corpus.git "/var/lib/deep32/rag-data/$GEN" "$MNT/s3/job.env"; do
  [ ! -e "$t" ] || { echo "REFUSE $t exists"; exit 2; }
done
sudo install -d -o "$(id -u)" -g "$(id -g)" /opt/d32f/s3 /var/lib/deep32/rag-data
mkdir -p "$MNT/s3"
cp -a "$P/engine" /opt/d32f/s3/engine
python3.12 -m venv /opt/d32f/s3/engine/.venv
/opt/d32f/s3/engine/.venv/bin/pip install --no-index --find-links "$P/wheels" --require-hashes -r "$P/engine.lock"
/opt/d32f/s3/engine/.venv/bin/python -I -c "import sysconfig, pathlib; pathlib.Path(sysconfig.get_paths()['purelib'], 'deep32-engine.pth').write_text('/opt/d32f/s3/engine/src' + chr(10))"
git clone --bare -q "$P/corpus.bundle" /opt/d32f/s3/corpus.git
git -C /opt/d32f/s3/corpus.git rev-parse refs/heads/corpus
tar -xf "$P/root.tar" -C /var/lib/deep32
cp "$P/job.env" "$MNT/s3/job.env"
echo "imported $GEN: run tei-kat, then s3-dense in the window (forge-job.sh)"
