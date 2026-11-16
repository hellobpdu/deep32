#!/bin/bash
# lock-wheels.sh: ON 901 as d32, network to PyPI and download.pytorch.org (outgoing only; 901's firewall allows
# it). Downloads every pinned wheel for CPython 3.12 manylinux x86_64 with a throwaway host venv (Ubuntu's own
# python3 3.12 and python3-venv, both on 901), then writes the --require-hashes locks the Dockerfiles install from.
# usage: lock-wheels.sh CTX
set -euo pipefail
CTX=${1:?CTX}
V=$(mktemp -d /tmp/d32-lock.XXXXXX); trap 'rm -rf "$V"' EXIT
python3 -m venv "$V/venv"
P="$V/venv/bin/pip"
mkdir -p "$CTX/wheelhouse/engine" "$CTX/wheelhouse/gateway" "$CTX/locks"
common=(download --only-binary=:all: --no-deps --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64
        --platform manylinux_2_28_x86_64 --platform manylinux_2_34_x86_64 --platform linux_x86_64
        --python-version 3.12 --implementation cp --abi cp312 --abi abi3 --abi none)
"$P" "${common[@]}" --dest "$CTX/wheelhouse/engine" --extra-index-url https://download.pytorch.org/whl/cpu \
     -r "$CTX/lane/compose/requirements-engine.in"
"$P" "${common[@]}" --dest "$CTX/wheelhouse/gateway" -r "$CTX/lane/compose/requirements-gateway.in"
python3 -I -B "$CTX/lane/compose/make_lock.py" --pins "$CTX/lane/compose/requirements-engine.in" \
        --wheels "$CTX/wheelhouse/engine" --out "$CTX/locks/engine.lock"
python3 -I -B "$CTX/lane/compose/make_lock.py" --pins "$CTX/lane/compose/requirements-gateway.in" \
        --wheels "$CTX/wheelhouse/gateway" --out "$CTX/locks/gateway.lock"
sha256sum "$CTX/locks/"*.lock
