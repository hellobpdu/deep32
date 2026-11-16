#!/bin/sh
# PYRAG step 8-9 (PYTHON-ON-OPENBSD 3.4, 4.1, 10.2): code trees, isolated service venvs, the edge's syspick
# allow-list, stand-ins and launchers, bytecode. Runs as d32build on the build VM (owns /usr/local/d32).
# Usage: make-venvs.sh   (idempotent: rebuilds both venvs from the wheelhouse every time)
set -eu
L=/build/lanes/pyrag
SRC=$L/src
WH=$L/wheelhouse
LOCKS=$L/locks
D=/usr/local/d32
PY312=$D/py312/bin/python3.12
PY314=/usr/local/bin/python3.14
SYS314=/usr/local/lib/python3.14/site-packages
PIPWHL=$SRC/dist/pip-26.2.1-py3-none-any.whl
ENGINE_ID=pub-9a2d58df01fc
GATEWAY_ID=pub-9a2d58df01fc
export PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
cd "$L"
step() { echo "=== $(date '+%H:%M:%S') $*"; }

step "code trees from the release archives (git archive of $ENGINE_ID and $GATEWAY_ID, made on the PC)"
rm -rf "$D/deep32-engine/$ENGINE_ID" "$D/deep32-gateway/$GATEWAY_ID"
mkdir -p "$D/deep32-engine" "$D/deep32-gateway"
tar -xf "$SRC/code/deep32-engine-$ENGINE_ID.tar" -C "$D/deep32-engine" "deep32-engine-$ENGINE_ID/src" "deep32-engine-$ENGINE_ID/config"
mv "$D/deep32-engine/deep32-engine-$ENGINE_ID" "$D/deep32-engine/$ENGINE_ID"
tar -xf "$SRC/code/deep32-gateway-$GATEWAY_ID.tar" -C "$D/deep32-gateway" "deep32-gateway-$GATEWAY_ID/app.py" "deep32-gateway-$GATEWAY_ID/rag_edge.py"
mv "$D/deep32-gateway/deep32-gateway-$GATEWAY_ID" "$D/deep32-gateway/$GATEWAY_ID"
ln -sfn "$ENGINE_ID" "$D/deep32-engine/current"
ln -sfn "$GATEWAY_ID" "$D/deep32-gateway/current"
(cd "$D" && find deep32-engine/$ENGINE_ID deep32-gateway/$GATEWAY_ID -type f | sort | xargs sha256 -r > "$L/code-SHA256SUMS")
wc -l < "$L/code-SHA256SUMS"

step "node: stand-ins (4.1) and launchers"
rm -rf "$D/node"; mkdir -p "$D/node"
cp -R "$L/node/standins" "$L/node/serve_core.py" "$L/node/serve_edge.py" "$L/node/test_node.py" "$D/node/"

step "venv deep32-engine: CPython 3.12.14, isolated, from the wheelhouse with --require-hashes"
rm -rf "$D/venv/deep32-engine"; mkdir -p "$D/venv"
"$PY312" -m venv --without-pip "$D/venv/deep32-engine"
"$D/venv/deep32-engine/bin/python" "$PIPWHL/pip" install --no-index --only-binary :all: --require-hashes \
  --find-links "$WH/cp312" --find-links "$SRC/dist" -r "$LOCKS/requirements-core.lock"

step "venv deep32-gateway: python 3.14 package, isolated; syspick allow-list for cryptography, cffi, rpds (3.4)"
rm -rf "$D/venv/deep32-gateway" "$D/syspick/edge"; mkdir -p "$D/syspick/edge"
"$PY314" -m venv --without-pip "$D/venv/deep32-gateway"
for n in cryptography cffi _cffi_backend rpds; do
  for p in "$SYS314/$n" "$SYS314/$n".cpython-*.so "$SYS314/$n"-*.dist-info "$SYS314/$(echo $n | tr - _)"_py-*.dist-info; do
    if [ -e "$p" ]; then ln -s "$p" "$D/syspick/edge/"; echo "syspick $(basename "$p")"; fi
  done
done
echo "$D/syspick/edge" > "$D/venv/deep32-gateway/lib/python3.14/site-packages/d32-syspick.pth"
"$D/venv/deep32-gateway/bin/python" "$PIPWHL/pip" install --no-index --only-binary :all: --require-hashes \
  --find-links "$WH/cp314" --find-links "$SRC/dist" -r "$LOCKS/requirements-edge.lock"

step "bytecode: compileall --invalidation-mode unchecked-hash (services run -I -B: nothing is written at run time)"
"$PY312" -m compileall -q -j 4 --invalidation-mode unchecked-hash \
  "$D/venv/deep32-engine/lib/python3.12/site-packages" "$D/deep32-engine/$ENGINE_ID/src" "$D/node/standins" \
  "$D/node/serve_core.py" || echo "compileall py312 reported errors"
"$PY312" -m compileall -q -j 4 --invalidation-mode unchecked-hash -x '/(test|idlelib|tkinter|turtledemo|lib2to3)/' \
  "$D/py312/lib/python3.12" || echo "compileall py312 stdlib reported errors (tests excluded)"
"$PY314" -m compileall -q -j 4 --invalidation-mode unchecked-hash \
  "$D/venv/deep32-gateway/lib/python3.14/site-packages" "$D/deep32-gateway/$GATEWAY_ID" "$D/node/serve_edge.py" \
  || echo "compileall py314 reported errors"

step "freeze (G-ver input)"
# --path: only what the venv (and the edge's allow-list) holds, not the pip wheel the command itself runs from
"$D/venv/deep32-engine/bin/python" "$PIPWHL/pip" list --format=freeze --path "$D/venv/deep32-engine/lib/python3.12/site-packages"   > "$L/freeze-deep32-engine.txt"
"$D/venv/deep32-gateway/bin/python" "$PIPWHL/pip" list --format=freeze --path "$D/venv/deep32-gateway/lib/python3.14/site-packages"   --path "$D/syspick/edge" > "$L/freeze-deep32-gateway.txt"
"$D/venv/deep32-engine/bin/python" "$PIPWHL/pip" check || echo "pip check deep32-engine: see above"
"$D/venv/deep32-gateway/bin/python" "$PIPWHL/pip" check || echo "pip check deep32-gateway: see above"
wc -l "$L/freeze-deep32-engine.txt" "$L/freeze-deep32-gateway.txt"
step done
