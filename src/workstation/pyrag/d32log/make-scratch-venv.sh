#!/bin/sh
# identity lane (G-libm): a deep32-engine venv on a scratch d32_log interpreter, made exactly as make-venvs.sh makes the
# live one: --without-pip, isolated, filled from pyrag's wheelhouse with --require-hashes against the same lock.
# Runs as d32build on the build VM. Usage: make-scratch-venv.sh [PREFIX] [VENV]
set -eu
PREFIX=${1:-/build/lanes/identity/py312}
V=${2:-/build/lanes/identity/venv/deep32-engine}
L=/build/lanes/pyrag
PIPWHL=$L/src/dist/pip-26.2.1-py3-none-any.whl
export PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
echo "=== $(date '+%H:%M:%S') venv $V on $PREFIX"
sha256 -r "$L/locks/requirements-core.lock"
rm -rf "$V"; mkdir -p "$(dirname "$V")"
"$PREFIX/bin/python3.12" -m venv --without-pip "$V"
"$V/bin/python" "$PIPWHL/pip" install --no-index --only-binary :all: --require-hashes \
  --find-links "$L/wheelhouse/cp312" --find-links "$L/src/dist" -r "$L/locks/requirements-core.lock"
"$V/bin/python" "$PIPWHL/pip" list --format=freeze --path "$V/lib/python3.12/site-packages" > "$V.freeze.txt"
if cmp -s "$V.freeze.txt" "$L/freeze-deep32-engine.txt"; then echo "freeze: identical to the live venv's ($(wc -l < "$V.freeze.txt") lines)"
else echo "freeze: DIFFERS from the live venv's"; diff "$L/freeze-deep32-engine.txt" "$V.freeze.txt" || true; fi
"$V/bin/python" "$PIPWHL/pip" check
"$PREFIX/bin/python3.12" -m compileall -q -j 4 --invalidation-mode unchecked-hash "$V/lib/python3.12/site-packages" \
  || echo "compileall reported errors"
"$V/bin/python" -I -c 'import sys, math, sqlite3, _sqlite3; print(sys.prefix, sys.base_prefix, math.__file__, _sqlite3.__file__, sqlite3.sqlite_version)'
echo "=== $(date '+%H:%M:%S') done"
