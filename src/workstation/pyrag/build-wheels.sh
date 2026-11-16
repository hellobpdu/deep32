#!/bin/sh
# PYRAG step 6-7 (PYTHON-ON-OPENBSD 10.2): build the native wheels of the two shipped sets, offline, on the build VM.
# Runs as d32build. Usage: build-wheels.sh STEP [STEP ...]
#   STEP = buildenv | regex | httptools | uvloop | numpy | pydantic-core | pydantic-core-314 | grpcio | manifest | all
# Inputs (copied in from the PC, every hash checked first): $SRC/dist (PyPI files + SHA256SUMS), $SRC/vendor
# (pydantic-core crates + cargo config), $SRC/abseil (abseil-cpp 20260817.0 = the 8.0 port's distfile, for grpcio).
# Output: $WH/cp312, $WH/cp314 (built wheels), $WH/SHA256SUMS. Logs: $L/logs/<step>.log
# Parallelism: JOBS (default 6; keep <= 6, the VM is shared with other lanes).
set -eu
L=/build/lanes/pyrag
SRC=$L/src
WH=$L/wheelhouse
RAM=/build/pobj/pyrag          # RAM disk (shared, stay < 2 GB): small builds only
DISK=$L/work                    # grpcio and pydantic-core build on disk
PY312=/usr/local/d32/py312/bin/python3.12
PY314=/usr/local/bin/python3.14
BE312=$L/buildenv312
BE314=$L/buildenv314
PIPWHL=$SRC/dist/pip-26.2.1-py3-none-any.whl
LOCKS=$L/locks
PORTS=/usr/ports
JOBS=${JOBS:-6}
export PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
mkdir -p "$WH/cp312" "$WH/cp314" "$L/logs" "$RAM" "$DISK"
cd "$L"   # su keeps root's cwd, which d32build cannot read
step() { echo "=== $(date '+%H:%M:%S') $*"; }

check_sources() {
  (cd "$SRC/dist" && sha256 -q -c SHA256SUMS) || { echo "dist hash check FAILED"; exit 2; }
  got=$(sha256 -b -q "$SRC/abseil/abseil-cpp-20260817.0.tar.gz")
  [ "$got" = "9+BRed85xFQ0ytQz9Xg4QLs3iO8yKXb5E4vGtys6EH0=" ] || { echo "abseil hash mismatch"; exit 2; }
  got=$(sha256 -q "$SRC/vendor/pydantic-core-2.46.5-crates.tar.gz")
  [ "$got" = "331dfb9aef2cde6ad2103c96a34052f113a392f42eef1b7f7c11fa7c32d0652e" ] || { echo "crates hash mismatch"; exit 2; }
  echo "sources: all hashes OK"
}

# pip from its own wheel (py312 was built --with-ensurepip=no; the service venvs carry no pip)
pipx() { py=$1; shift; "$py" "$PIPWHL/pip" "$@"; }

buildenv() {
  step "build venvs (cp312: setuptools, cython, meson-python ...; cp314: typing-extensions)"
  rm -rf "$BE312" "$BE314"
  "$PY312" -m venv --without-pip "$BE312"
  pipx "$BE312/bin/python" install --no-index --only-binary :all: --require-hashes --find-links "$SRC/dist" \
    -r "$LOCKS/requirements-buildenv312.lock"
  "$PY314" -m venv --without-pip "$BE314"
  pipx "$BE314/bin/python" install --no-index --only-binary :all: --require-hashes --find-links "$SRC/dist" \
    -r "$LOCKS/requirements-buildenv314.lock"
  "$BE312/bin/python" -m pip list --format=freeze
}

unpack() {  # unpack <sdist> <workdir> -> prints the source dir
  rm -rf "$2"; mkdir -p "$2"; tar -xzf "$1" -C "$2"; ls -d "$2"/*/ | head -1
}

port_patches() {  # port_patches <srcdir> <port patches dir>
  for p in "$2"/patch-*; do
    if (cd "$1" && patch -p0 -E -s -f < "$p"); then echo "applied $(basename "$p")"
    else echo "PATCH FAILED $(basename "$p")"; exit 3; fi
  done
  find "$1" -name '*.rej' | sed 's/^/REJECT /'
  find "$1" -name '*.orig' -delete
}

pipwheel() {  # pipwheel <python> <outdir> <src> [extra pip args]
  py=$1; out=$2; src=$3; shift 3
  "$py" -m pip wheel --no-deps --no-build-isolation --no-index -w "$out" "$@" "$src"
}

regex() {
  step "regex 2026.9.10 cp312"
  pipwheel "$BE312/bin/python" "$WH/cp312" "$SRC/dist/regex-2026.9.10.tar.gz"
}

httptools() {
  step "httptools 0.8.0 cp312 (bundled llhttp, like the 8.0 port)"
  pipwheel "$BE312/bin/python" "$WH/cp312" "$SRC/dist/httptools-0.8.0.tar.gz"
}

uvloop() {
  step "uvloop 0.22.1 cp312 against system libuv (8.0 port patch: use_system_libuv, cython_always)"
  d=$(unpack "$SRC/dist/uvloop-0.22.1.tar.gz" "$RAM/uvloop")
  port_patches "$d" "$PORTS/devel/py-uvloop/patches"
  rm -rf "$d/vendor/libuv"   # never the bundled copy
  CFLAGS="-O2 -pipe -I/usr/local/include" LDFLAGS="-L/usr/local/lib" UVLOOP_OPT_CFLAGS= \
    pipwheel "$BE312/bin/python" "$WH/cp312" "$d"
  rm -rf "$RAM/uvloop"
}

numpy() {
  step "numpy 2.5.3 cp312 (8.0 port patches; -Dallow-noblas=true; numpy's default CPU baseline like upstream wheels)"
  d=$(unpack "$SRC/dist/numpy-2.5.3.tar.gz" "$RAM/numpy")
  port_patches "$d" "$PORTS/math/py-numpy/patches"
  pipwheel "$BE312/bin/python" "$WH/cp312" "$d" \
    -Csetup-args=-Dallow-noblas=true -Csetup-args=-Dblas=none -Csetup-args=-Dlapack=none \
    -Ccompile-args=-j$JOBS -Cbuild-dir="$RAM/numpy-build"
  rm -rf "$RAM/numpy" "$RAM/numpy-build"
}

pydantic_core() {  # pydantic_core <build venv python> <outdir>
  step "pydantic-core 2.46.5 with $1 (maturin $(maturin --version | cut -d' ' -f2), offline, vendored crates)"
  d=$(unpack "$SRC/dist/pydantic_core-2.46.5.tar.gz" "$DISK/pydantic-core")
  [ -d "$SRC/pydantic-core-2.46.5-crates" ] || tar -xzf "$SRC/vendor/pydantic-core-2.46.5-crates.tar.gz" -C "$SRC"
  mkdir -p "$d/.cargo"; cp "$SRC/vendor/cargo-config.toml" "$d/.cargo/config.toml"
  (cd "$d" && CARGO_HOME="$DISK/cargo-home" CARGO_NET_OFFLINE=true CARGO_BUILD_JOBS=$JOBS \
     CARGO_TARGET_DIR="$DISK/pydantic-core-target" PYO3_PYTHON="$1" \
     maturin build --release --locked --offline --interpreter "$1" --out "$2")
}

grpcio() {
  step "grpcio 1.84.0 cp312: system abseil/re2/c-ares/LibreSSL/zlib, the 8.0 port's net/py-grpcio patches"
  d=$(unpack "$SRC/dist/grpcio-1.84.0.tar.gz" "$DISK/grpcio")
  port_patches "$d" "$PORTS/net/py-grpcio/patches"
  # the port's post-extract: drop the bundles the system provides (xxhash stays: no system xxhash on the VM),
  # and give the Cython extension abseil's own source of the system version (absl/log/initialize.cc)
  (cd "$d/third_party" && rm -rf abseil-cpp boringssl-with-bazel cares re2 zlib)
  tar -xzf "$SRC/abseil/abseil-cpp-20260817.0.tar.gz" -C "$DISK/grpcio"
  ln -sf "$DISK/grpcio/abseil-cpp-20260817.0" "$d/third_party/abseil-cpp"
  export GRPC_PYTHON_BUILD_SYSTEM_ABSL=True GRPC_PYTHON_BUILD_SYSTEM_CARES=True GRPC_PYTHON_BUILD_SYSTEM_OPENSSL=True \
    GRPC_PYTHON_BUILD_SYSTEM_RE2=True GRPC_PYTHON_BUILD_SYSTEM_ZLIB=True \
    GRPC_PYTHON_CFLAGS="-O2 -pipe -Wno-ignored-attributes -Wno-deprecated-declarations" \
    GRPC_PYTHON_BUILD_EXT_COMPILER_JOBS=$JOBS
  pipwheel "$BE312/bin/python" "$WH/cp312" "$d"
}

manifest() {
  step "wheelhouse manifest"
  (cd "$WH" && find . -name '*.whl' | sort | sed 's|^\./||' | xargs sha256 -r | sort -k2 > SHA256SUMS && cat SHA256SUMS)
}

check_sources
for s in "$@"; do
  case "$s" in
    buildenv) buildenv ;;
    regex) regex ;; httptools) httptools ;; uvloop) uvloop ;; numpy) numpy ;;
    pydantic-core) pydantic_core "$BE312/bin/python" "$WH/cp312" ;;
    pydantic-core-314) pydantic_core "$BE314/bin/python" "$WH/cp314" ;;
    grpcio) grpcio ;;
    manifest) manifest ;;
    all) buildenv; regex; httptools; uvloop; numpy; pydantic_core "$BE312/bin/python" "$WH/cp312"
         pydantic_core "$BE314/bin/python" "$WH/cp314"; grpcio; manifest ;;
    *) echo "unknown step $s"; exit 64 ;;
  esac
done
step done
