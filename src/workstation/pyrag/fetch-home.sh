#!/usr/bin/env bash
# PYRAG source side (Git Bash): fetch and verify every source the offline build VM needs, then copy it in.
# PYTHON-ON-OPENBSD 10.2 step 3 (PyPI part), 6-7 inputs. Never WSL. Downloads go to D32_ARTEFACTS.
# Usage: fetch-home.sh [--no-copy]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
A=${D32_ARTEFACTS:?set D32_ARTEFACTS to the source download folder}
PY=${PYTHON:-python3}
CARGO=~/.cargo/bin/cargo
CORE_REPO=${D32_ENGINE_SOURCE:?set D32_ENGINE_SOURCE}; CORE_COMMIT=${D32_ENGINE_REF:?set D32_ENGINE_REF}
EDGE_REPO=${D32_GATEWAY_SOURCE:?set D32_GATEWAY_SOURCE}; EDGE_COMMIT=${D32_GATEWAY_REF:?set D32_GATEWAY_REF}
ABSEIL=abseil-cpp-20260817.0.tar.gz
ABSEIL_SHA256_B64="9+BRed85xFQ0ytQz9Xg4QLs3iO8yKXb5E4vGtys6EH0="   # OpenBSD 8.0 ports devel/abseil-cpp/distinfo
mkdir -p "$A"/{pypi-json,dist,vendor,abseil,code}
cd "$HERE"

echo "== PyPI metadata of production's pins (H0) and the shipped closures"
$PY -I tools/pypi_meta.py "$A/pypi-json" locks/H0-core-pip.txt locks/H0-edge-pip.txt | grep -v '^ok' || true
$PY -I tools/closure_lock.py "$A/pypi-json" locks/H0-core-pip.txt 3.12 \
  fastapi uvicorn uvloop httptools httpx anyio pydantic qdrant-client regex numpy > "$A/closure-core.tsv"
$PY -I tools/closure_lock.py "$A/pypi-json" locks/H0-edge-pip.txt 3.14 \
  mcp mcp-types httpx anyio pydantic starlette uvicorn > "$A/closure-edge.tsv"
wc -l "$A"/closure-*.tsv

echo "== PyPI files, each checked against PyPI's published SHA-256"
for l in service buildtools testtools; do
  $PY -I tools/fetch_pypi.py "$A/pypi-json" "$A/dist" "locks/fetch-$l.txt" | grep -v '^ok' || true
done
ls "$A/dist" | wc -l

echo "== pydantic-core 2.46.5: cargo vendor from the sdist's Cargo.lock"
if [ ! -f "$A/vendor/pydantic-core-2.46.5-crates.tar.gz" ]; then
  rm -rf "$A/vendor/pydantic_core-2.46.5" "$A/vendor/pydantic-core-2.46.5-crates"
  (cd "$A/vendor" && tar -xzf ../dist/pydantic_core-2.46.5.tar.gz)   # relative: GNU tar reads "D:" as a host
  (cd "$A/vendor/pydantic_core-2.46.5" && $CARGO vendor --locked --versioned-dirs ../pydantic-core-2.46.5-crates >/dev/null)
  (cd "$A/vendor" && tar -czf pydantic-core-2.46.5-crates.tar.gz pydantic-core-2.46.5-crates)
fi
cp "$HERE/locks/cargo-config.toml" "$A/vendor/cargo-config.toml"
sha256sum "$A/vendor/pydantic-core-2.46.5-crates.tar.gz"
echo "   (a re-vendor changes this tarball's hash: update build-wheels.sh check_sources to match)"

echo "== abseil-cpp source for grpcio's Cython extension (the 8.0 port's distfile)"
[ -f "$A/abseil/$ABSEIL" ] || curl -sSL -o "$A/abseil/$ABSEIL" https://github.com/abseil/abseil-cpp/archive/20260817.0.tar.gz
got=$(openssl dgst -sha256 -binary "$A/abseil/$ABSEIL" | base64)
[ "$got" = "$ABSEIL_SHA256_B64" ] || { echo "abseil hash mismatch: $got"; exit 2; }
echo "abseil OK"

echo "== release code: git archive of the exact commits (no working-tree files)"
(cd "$CORE_REPO" && git archive --format=tar --prefix=deep32-engine-$CORE_COMMIT/ $CORE_COMMIT) > "$A/code/deep32-engine-$CORE_COMMIT.tar"
(cd "$EDGE_REPO" && git archive --format=tar --prefix=deep32-gateway-$EDGE_COMMIT/ $EDGE_COMMIT) > "$A/code/deep32-gateway-$EDGE_COMMIT.tar"
for t in "$A"/code/*.tar; do
  n=$(tar --force-local -tf "$t" | grep -ciE '(^|/)\.env|secret|token|\.pem$|\.key$' || true)
  echo "$(sha256sum "$t" | cut -c1-16) $(basename "$t"): $n env/secret-looking names"
done

[ "${1:-}" = "--no-copy" ] && exit 0
echo "== copy to the selected build host: /build/lanes/pyrag (src + scripts + locks)"
IP=${D32_BUILD_HOST:?set D32_BUILD_HOST}; K=${D32_BUILD_KEY:?set D32_BUILD_KEY}
(cd "$A" && tar -cf - dist vendor/pydantic-core-2.46.5-crates.tar.gz vendor/cargo-config.toml "abseil/$ABSEIL" code/*.tar) |
  ssh -i $K root@$IP 'mkdir -p /build/lanes/pyrag/src && tar -xf - -C /build/lanes/pyrag/src'
tar -cf - build-wheels.sh make-venvs.sh run-tests.sh tools locks node |
  ssh -i $K root@$IP 'cd /build/lanes/pyrag && tar -xf - && chown -R d32build:d32build /build/lanes/pyrag'
echo "copied; on the VM: (cd /build/lanes/pyrag/src/dist && sha256 -c SHA256SUMS)"
