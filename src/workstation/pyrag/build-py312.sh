#!/bin/sh
# PYRAG step 4-5 (PYTHON-ON-OPENBSD 10.2): pinned static SQLite 3.53.1 + CPython 3.12.14 with the OpenBSD 7.8 port's
# patches, private prefix /usr/local/d32. Runs as d32build on the build VM. Log: /build/src/pyrag/build-py312.log
# G-libm (PYTHON-ON-OPENBSD 5.4, 2026-11-06): log() in SQLite (FTS5 bm25, ln/log) and in the math module is d32_log,
# ARM optimized-routines v26.07's log on glibc's non-FMA path (pyrag/d32log: sources, checks; pyrag/LIBM-SWAP.md).
# A scratch build never touches the live prefix: set all three, for example
#   W=/build/lanes/identity/work PREFIX=/build/lanes/identity/py312 SQ=/build/lanes/identity/sqlite sh build-py312.sh
set -eu
W=${W:-/build/pobj/py312}
SRC=/build/src/pyrag
PATCHES=/build/src/ports-7.8/ports/lang/python/3/patches
PREFIX=${PREFIX:-/usr/local/d32/py312}
SQ=${SQ:-/usr/local/d32/sqlite}
D32LOG_SRC=${D32LOG_SRC:-/build/lanes/identity/src/d32log}   # = repo pyrag/d32log (d32log.c/.h, the patch, checks)
D32LOG=$W/d32log
# uv's (python-build-standalone) bundle options; H0 confirms production's exact list later (rebuild is cheap).
SQOPTS="-DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS -DSQLITE_ENABLE_FTS4 \
 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_GEOPOLY -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_ENABLE_RTREE \
 -DSQLITE_TEMP_STORE=1 -DSQLITE_THREADSAFE=1 -DSQLITE_ENABLE_PERCENTILE"  # production has PERCENTILE (SQLite configure default)
step() { echo "=== $(date '+%H:%M:%S') $*"; }

step start; rm -rf "$W"; mkdir -p "$W"; cd "$W"

step "d32_log (ARM optimized-routines v26.07 log; refuses FMA/AVX; checked against glibc 2.43's own e_log.c)"
sh "$D32LOG_SRC/build-d32log.sh" "$D32LOG"

step sqlite 3.53.1 static, log = d32_log
python3.14 -m zipfile -e "$SRC/sqlite-amalgamation-3530100.zip" .  # base has no unzip
cd sqlite-amalgamation-3530100
cc -O2 -ffp-contract=off -fPIC $SQOPTS -include "$D32LOG/d32log.h" -c sqlite3.c -o sqlite3.o
if nm sqlite3.o | grep -qE ' U log$'; then echo "sqlite3.o still calls libm log"; exit 7; fi
nm sqlite3.o | grep -E ' U d32_log$' || { echo "sqlite3.o does not call d32_log"; exit 7; }
ar rcs libsqlite3.a sqlite3.o "$D32LOG/d32log.o"
cc -O2 -ffp-contract=off $SQOPTS -include "$D32LOG/d32log.h" shell.c sqlite3.o "$D32LOG/d32log.o" -o sqlite3 -lm -lpthread
install -d "$SQ/lib" "$SQ/include" "$SQ/bin"
install -m 644 libsqlite3.a "$SQ/lib/"; install -m 644 sqlite3.h sqlite3ext.h "$SQ/include/"; install -m 755 sqlite3 "$SQ/bin/"
"$SQ/bin/sqlite3" :memory: 'select sqlite_version();' 'pragma compile_options;' | tr '\n' ' '; echo

step cpython 3.12.14 unpack and patch
cd "$W"; xz -dc "$SRC/Python-3.12.14.tar.xz" | tar xf -; cd Python-3.12.14  # base tar has no -J
for p in "$PATCHES"/patch-* "$D32LOG_SRC/patch-Modules_mathmodule_c-d32log"; do
  if patch -p0 -E -s -f < "$p"; then echo "applied $(basename "$p")"; else echo "PATCH FAILED $(basename "$p")"; exit 3; fi
done
install -m 644 "$D32LOG/d32log.h" Modules/d32log.h
find . -name '*.rej' | sed 's/^/REJECT /'
step autoconf
sed -i 's/[$][{]LIBpython3[.]12_VERSION[}]/1.0/' configure.ac  # ports substitutes SHARED_LIBS here; static build, value unused
grep -n 'SOVERSION=' configure.ac | head -2
AUTOCONF_VERSION=2.71 autoconf

step configure  # bundled expat: 3.12.14 pyexpat.c includes expat_config.h, which base expat does not install
./configure --prefix="$PREFIX" --enable-ipv6 --with-system-ffi \
  --enable-loadable-sqlite-extensions --with-ensurepip=no \
  OPT='-O2 -pipe -fPIC' CPPFLAGS='-I/usr/local/include' LDFLAGS='-L/usr/local/lib/' \
  LIBSQLITE3_CFLAGS="-I$SQ/include" LIBSQLITE3_LIBS="$SQ/lib/libsqlite3.a -lm -lpthread" \
  ac_cv_working_openssl_hashlib=yes > configure.out 2>&1 || { tail -30 configure.out; exit 4; }
tail -3 configure.out
grep -n '^MODULE_MATH_LDFLAGS' Makefile

step build  # the math extension links libd32log.a (d32_log for mathmodule.c's log calls)
gmake -j8 CFLAGS_NODIST='-I/usr/local/include' LDFLAGS_NODIST='-L/usr/local/lib/' \
  MODULE_MATH_LDFLAGS="$D32LOG/libd32log.a -lm" > build.out 2>&1 || { tail -40 build.out; exit 5; }
grep -A12 'necessary bits to build these optional modules were not found\|Failed to build these modules\|Following modules built successfully but were removed' build.out || echo "all optional modules built"
for so in build/lib.*/math.cpython-312.so build/lib.*/_sqlite3.cpython-312.so; do
  if nm -D "$so" | grep -qE ' U log$'; then echo "$so still imports libm log"; exit 8; fi
  readelf -s "$so" | grep -E ' d32_log$' | grep -q LOCAL || { echo "$so: no local d32_log"; exit 8; }
  echo "$so: d32_log local, no libm log import"
done

step install
gmake install > install.out 2>&1 || { tail -30 install.out; exit 6; }

step checks
"$PREFIX/bin/python3.12" -c 'import sys,sqlite3,ssl,ctypes,unicodedata as u,lzma,bz2,zlib,decimal; print(sys.version.split()[0], sqlite3.sqlite_version, u.unidata_version, ssl.OPENSSL_VERSION)'
"$PREFIX/bin/python3.12" -m test -j8 test_sqlite3 test_re test_unicodedata test_json test_math test_float test_ssl \
  test_threading test_ctypes test_decimal > test.out 2>&1 && echo "TESTS PASS" || { tail -25 test.out; echo "TESTS FAILED"; }
tail -6 test.out
step done
