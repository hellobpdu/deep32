#!/bin/sh
# nodegen build (the build VM, as d32build): libd32vec.so from d32vec.c with OpenBSD base clang, then exact_local,
# the launcher and the unit tests beside pyrag's files in /usr/local/d32/node; bytecode as pyrag does it.
#   su -s /bin/sh d32build -c 'sh /build/lanes/nodegen/src/build.sh'
# Nothing in pyrag's trees (py312, sqlite, venvs, wheelhouse, serve_core.py, standins) is touched.
set -eu
L=/build/lanes/nodegen
D=/usr/local/d32
PY=$D/venv/deep32-engine/bin/python3.12
CFLAGS="-std=c99 -O2 -ffp-contract=off -fPIC -Wall -Wextra -Werror"   # no -march: amd64 baseline (SSE2)
cd "$L/src"
mkdir -p "$L/build"
echo "=== $(date '+%H:%M:%S') cc: $(cc --version | head -1)"
cc $CFLAGS -shared -o "$L/build/libd32vec.so" d32vec.c
# the object must hold no fused multiply-add and no wider-than-SSE register: the order is the SSE order
n_fma=$(llvm-objdump -d "$L/build/libd32vec.so" | grep -Eci 'vfn?m(add|sub)[0-9]|ymm|zmm' || true)
echo "FMA/AVX instructions in libd32vec.so: $n_fma"
[ "$n_fma" = 0 ] || { echo "libd32vec.so holds FMA/AVX code"; exit 1; }
llvm-objdump -d "$L/build/libd32vec.so" | awk '/<d32_dot>:/,/^$/' | grep -Eo '(mulps|addps|addss|unpckhpd|movhlps|shufps)' \
  | sort | uniq -c | tr '\n' ' '; echo "(d32_dot's SSE instruction mix)"
nm -D "$L/build/libd32vec.so" | awk '$2 == "T" {print "  exported:", $3}'

install -d -m 755 "$D/lib"
install -m 444 "$L/build/libd32vec.so" "$D/lib/libd32vec.so"
install -m 444 exact_local.py serve_core_exact.py test_exact_local.py "$D/node/"
$PY -I -m compileall -q --invalidation-mode unchecked-hash \
  "$D/node/exact_local.py" "$D/node/serve_core_exact.py" "$D/node/test_exact_local.py"
cd "$D"
sha256 lib/libd32vec.so node/exact_local.py node/serve_core_exact.py node/test_exact_local.py \
  node/__pycache__/exact_local.cpython-312.pyc node/__pycache__/serve_core_exact.cpython-312.pyc
echo "=== $(date '+%H:%M:%S') build done"
