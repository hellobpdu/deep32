#!/bin/ksh
# build.sh [LANE]: builds the hardening lane's two node libraries on the build VM (OpenBSD 8.0, base cc), as root or
# d32build, into $LANE/out/lib, and prints their sha256 for the set's MANIFEST:
#   libd32kiwixjail.so  U28: unveil + pledge inside kiwix-serve (src/kiwixjail/d32-kiwix-jail.c)
#   libd32loopback.so   U43: wildcard bind(2) -> loopback for the CDE session (src/loopback/d32-loopback.c)
# Sources from $LANE/src/ship/u2 (push: tar -cf - ship/u2 | d32b-ssh the build VM 'tar -C /build/lanes/u2-hardening/src -xf -').
set -eu
L=${1:-/build/lanes/u2-hardening}
S=$L/src/ship/u2
O=$L/out/lib
mkdir -p $O
cc -O2 -Wall -Wextra -Werror -fPIC -shared -o $O/libd32kiwixjail.so $S/src/kiwixjail/d32-kiwix-jail.c -lpthread
cc -O2 -Wall -Wextra -Werror -fPIC -shared -o $O/libd32loopback.so $S/src/loopback/d32-loopback.c
chmod 0444 $O/*.so
cd $O && sha256 libd32kiwixjail.so libd32loopback.so | tee SHA256
print "compiler: $(cc --version | head -1)"
