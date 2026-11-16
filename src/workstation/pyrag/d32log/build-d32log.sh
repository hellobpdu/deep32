#!/bin/sh
# d32_log (PYTHON-ON-OPENBSD 5.4): build the object and its archive from ARM optimized-routines v26.07, refuse an
# object with FMA/AVX or an fma() call, and check it against glibc 2.43's own e_log.c (and, given its directory,
# production's libm proof). Runs as d32build on the build VM.
#   Usage: build-d32log.sh OUTDIR [PROOF_DIR]
#   AOR    optimized-routines tree (default: the v26.07 tarball unpacked under /build/lanes/identity/src/aor)
#   GLIBC  glibc 2.43 files, fetched by name (default /build/lanes/identity/src/glibc-2.43)
# Output: OUTDIR/d32log.o, OUTDIR/libd32log.a, OUTDIR/d32log.h, OUTDIR/d32log-check
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:?usage: build-d32log.sh OUTDIR [PROOF_DIR]}
PROOF=${2:-}
AOR=${AOR:-/build/lanes/identity/src/aor/optimized-routines-4be260a5117480382690c6d8c300bc784e927d76}
GLIBC=${GLIBC:-/build/lanes/identity/src/glibc-2.43}
CFLAGS_D32LOG="-O2 -ffp-contract=off -fPIC -fvisibility=hidden"
mkdir -p "$OUT"
cc $CFLAGS_D32LOG -I"$AOR/math" -I"$AOR/math/include" -c "$HERE/d32log.c" -o "$OUT/d32log.o"
rm -f "$OUT/libd32log.a"
ar rcs "$OUT/libd32log.a" "$OUT/d32log.o"
install -m 644 "$HERE/d32log.h" "$OUT/d32log.h"
# baseline SSE2 only: no FMA or AVX instruction, no call to fma()
if objdump -d "$OUT/d32log.o" | grep -Eq 'vfmadd|vfmsub|vfnmadd|vfnmsub|%ymm|vmulsd|vaddsd|vsubsd|<fma>|<fma@'; then
  echo "d32log.o: an FMA/AVX instruction or an fma() call"
  exit 3
fi
echo "d32log.o: SSE2 only: $(objdump -d "$OUT/d32log.o" | grep -c mulsd) mulsd, $(objdump -d "$OUT/d32log.o" | grep -c addsd) addsd, $(objdump -d "$OUT/d32log.o" | grep -c subsd) subsd, 0 FMA"
nm "$OUT/d32log.o" | grep -E ' [TtDdRr] ' | sed 's/^/d32log.o: /'
# glibc 2.43's own e_log.c and e_log_data.c, unchanged, beside the test shim (test only, never shipped)
G="$OUT/glibc-test"
rm -rf "$G"
mkdir -p "$G"
cp "$GLIBC/sysdeps_ieee754_dbl-64_e_log.c" "$G/e_log.c"
cp "$GLIBC/sysdeps_ieee754_dbl-64_e_log_data.c" "$G/e_log_data.c"
cp "$HERE/glibc-shim/"*.h "$HERE/glibc-shim/glibc_log.c" "$G/"
cc -O2 -ffp-contract=off -I"$G" -c "$G/glibc_log.c" -o "$G/glibc_log.o"
cc -O2 -ffp-contract=off "$HERE/d32log-check.c" "$OUT/d32log.o" "$G/glibc_log.o" -o "$OUT/d32log-check" -lm
"$OUT/d32log-check" sweep 28
if [ -n "$PROOF" ]; then
  "$OUT/d32log-check" proof "$PROOF/libm-log.bin" 66717 11558 816 sys | tail -1
  "$OUT/d32log-check" proof "$PROOF/libm-log.bin" 66717 11558 816 d32
fi
