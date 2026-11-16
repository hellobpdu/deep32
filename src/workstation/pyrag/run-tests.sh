#!/bin/sh
# PYRAG: deep32-engine's and deep32-gateway's own test suites on the build host (PYTHON-ON-OPENBSD 10.2 C7), in THROWAWAY test venvs
# (the service venvs stay clean): each = the service lock + pytest (requirements-test.lock), same wheelhouse, same
# hashes. deep32-engine runs with the torch/pylate stand-ins on its path and the launcher's 8 MiB thread stack (a pytest
# plugin), nothing deselected: tests that need torch/transformers/tokenizers/Qdrant fail or skip and are counted.
# Runs as d32build. Output: $L/logs/tests-*.log and the junit XML next to them.
# Usage: run-tests.sh [core] [edge]   (default both; SKIPVENV=1 reuses existing test venvs)
set -eu
L=/build/lanes/pyrag
D=/usr/local/d32
SRC=$L/src
WH=$L/wheelhouse
LOCKS=$L/locks
TE=$L/testenv
T=$L/test
PIPWHL=$SRC/dist/pip-26.2.1-py3-none-any.whl
export PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
cd "$L"
step() { echo "=== $(date '+%H:%M:%S') $*"; }
mkvenv() {  # mkvenv <python> <dir> <cp tag> <service lock> [syspick dir]
  rm -rf "$2"; "$1" -m venv --without-pip "$2"
  # the edge's allow-list goes in BEFORE pip runs, as in make-venvs.sh (pip must see cryptography, cffi, rpds there)
  if [ -n "${5:-}" ]; then echo "$5" > "$(echo "$2"/lib/python3.*/site-packages)/d32-syspick.pth"; fi
  "$2/bin/python" "$PIPWHL/pip" install -q --no-index --only-binary :all: --require-hashes \
    --find-links "$WH/$3" --find-links "$SRC/dist" -r "$LOCKS/$4"
  "$2/bin/python" "$PIPWHL/pip" install -q --no-index --only-binary :all: --require-hashes \
    --find-links "$SRC/dist" -r "$LOCKS/requirements-test.lock"
}

SUITES="${*:-core edge}"
step "test copies of the release archives"
rm -rf "$T"; mkdir -p "$T"
tar -xf src/code/deep32-engine-pub-9a2d58df01fc.tar -C "$T"; tar -xf src/code/deep32-gateway-pub-9a2d58df01fc.tar -C "$T"
mkdir -p "$T/plugins"
cat > "$T/plugins/d32_threads.py" <<'PLUG'
# pytest plugin: the launcher's thread stack (serve_core.configure_threads) for every thread the tests start
import threading
threading.stack_size(8 << 20)
PLUG

if [ -z "${SKIPVENV:-}" ]; then
step "test venvs"
mkvenv $D/py312/bin/python3.12 "$TE/deep32-engine" cp312 requirements-core.lock
mkvenv /usr/local/bin/python3.14 "$TE/deep32-gateway" cp314 requirements-edge.lock "$D/syspick/edge"
fi

case " $SUITES " in *" core "*)
step "deep32-engine suite (pytest, cp312, stand-ins, 8 MiB threads)"
cd "$T/deep32-engine-pub-9a2d58df01fc"
PYTHONPATH="src:$D/node/standins:$T/plugins" timeout 3600 "$TE/deep32-engine/bin/python" -B -m pytest -p d32_threads \
  -p no:cacheprovider --continue-on-collection-errors -q -rfEs --junitxml="$L/logs/tests-deep32-engine.xml" tests > "$L/logs/tests-deep32-engine.log" 2>&1 \
  || echo "deep32-engine pytest exit $?"
tail -3 "$L/logs/tests-deep32-engine.log" ;;
esac

case " $SUITES " in *" edge "*)
step "deep32-gateway suite (pytest, cp314)"
cd "$T/deep32-gateway-pub-9a2d58df01fc"
PYTHONPATH=".:$T/plugins" timeout 1800 "$TE/deep32-gateway/bin/python" -B -m pytest -p d32_threads -p no:cacheprovider --continue-on-collection-errors -q -rfEs \
  --junitxml="$L/logs/tests-deep32-gateway.xml" tests > "$L/logs/tests-deep32-gateway.log" 2>&1 || echo "deep32-gateway pytest exit $?"
tail -3 "$L/logs/tests-deep32-gateway.log" ;;
esac
step done
