#!/bin/sh
# Run as d32build inside a fresh loopback-only OpenBSD routing domain.
set -eu
umask 077
PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/X11R6/bin:/usr/local/bin:/usr/local/dt/bin
LC_ALL=C
D32_QA_PRIVATE_RPC=1
export PATH LC_ALL D32_QA_PRIVATE_RPC

die()
{
	printf 'qa-matrix: %s\n' "$1" >&2
	exit 1
}

usage()
{
	printf 'usage: sh qa-matrix.sh /absolute/scratch rdomain [640x480-hp]\n' >&2
	exit 64
}

plain_file()
{
	[ -f "$1" ] && [ ! -L "$1" ] && [ "$(realpath "$1")" = "$1" ]
}

pin()
{
	plain_file "$1" && [ "$(sha256 -q "$1")" = "$2" ] ||
	    die "accepted T03 input differs ($1)"
}

snapshot_rpc()
{
	timeout 4 rpcinfo -p 127.0.0.1 >"$1"
}

[ "$#" -ge 2 ] && [ "$#" -le 3 ] || usage
scratch=$1
domain=$2
selected=${3:-}
case $scratch in /*) ;; *) usage ;; esac
case $scratch in /|*[!A-Za-z0-9/._-]*) usage ;; esac
case $domain in ''|0|*[!0-9]*) usage ;; esac
if [ "$#" -eq 3 ]; then
	case ${selected%-*} in 640x480|1366x768|1920x1080) ;; *) usage ;; esac
	case ${selected#*-} in hp|vue|hc) ;; *) usage ;; esac
fi

[ "$(uname -s)" = OpenBSD ] || die 'OpenBSD required'
[ "$(id -u)" -ne 0 ] && [ "$(id -un)" = d32build ] || die 'd32build required'
[ "$(id -R)" = "$domain" ] || die 'private routing domain differs'
[ -d "$scratch" ] && [ "$(realpath "$scratch")" = "$scratch" ] &&
    [ "$(stat -f %u "$scratch")" = "$(id -u)" ] || die 'scratch must be a canonical directory owned by d32build'

app=$scratch/ship/u10/src/netcalc
harness=$scratch/ship/u10/tests/harness
loopback_src=$scratch/ship/u2/src/loopback/d32-loopback.c
binary=$app/d32calc
check=$app/qa-keyboard.sh
plain_file "$binary" && [ -x "$binary" ] || die 'build the scratch d32calc first'
plain_file "$check" || die 'scratch keyboard check missing'

# Source pins from 38b16907264e12e266b2f5a0574e9a593ae6318b.
pin "$harness/run-app.sh" 9670a22b13b002fb8ead15b7b3dc9fa029ea288eca953a45e59a5a661567cfac
pin "$harness/Makefile" 3c9312193378c4cebc971d3d2b27fc820ca5849ef966bc6c6a3bccf7757f151a
pin "$harness/d32xt.c" 3facb3dca88d0b3d06002274556f2e5c8c5bbb2cca37378dd4eee568026ba3ad
pin "$loopback_src" 43ce7b506e6ede84d4d4cc948f577943e6671fe8cd4ae20e88f039d8a28a63f9

out=$(mktemp -d "$scratch/t13-matrix.XXXXXXXX")
printf 'T13_EVIDENCE=%s\n' "$out"
snapshot_rpc "$out/rpc-before.tsv" || die 'private portmapper did not answer'
awk 'NR > 1 && $1 != 100000 { bad = 1 } END { exit NR != 3 || bad }' \
    "$out/rpc-before.tsv" || die 'private portmapper must be fresh'

sh -n "$check" || die 'keyboard check failed shell syntax'
make -C "$harness" >"$out/harness-build.log" 2>&1 || die 'T03 driver build failed'
shim=$out/libd32loopback.so
cc -O2 -Wall -Wextra -Werror -fPIC -shared -o "$shim" "$loopback_src" \
    >"$out/loopback-build.log" 2>&1 || die 'loopback shim build failed'
shim_sha=$(sha256 -q "$shim")

mkdir "$out/palettes"
printf 'D32Calc*background: #c8c6bd\nD32Calc*foreground: #202020\n' >"$out/palettes/hp.xrdb"
printf 'D32Calc*background: #a5b5c2\nD32Calc*foreground: #101820\n' >"$out/palettes/vue.xrdb"
printf 'D32Calc*background: #eeeeee\nD32Calc*foreground: #101010\n' >"$out/palettes/hc.xrdb"
cat >"$out/netcalc.dt" <<EOF_DT
ACTION D32NetCalc
{
 TYPE COMMAND
 LABEL Network Calculator
 EXEC_STRING $binary
 WINDOW_TYPE NO_STDIO
}
EOF_DT
sha256 "$binary" "$check" "$harness/run-app.sh" "$harness/d32xt" "$shim" \
    "$out/netcalc.dt" "$out/palettes/"*.xrdb >"$out/INPUT-SHA256"
printf 'case\trunner_exit\trpc\n' >"$out/cases.tsv"

failed=0
for geom in 640x480 1366x768 1920x1080; do
	for palette in hp vue hc; do
		case_name=$geom-$palette
		[ -z "$selected" ] || [ "$selected" = "$case_name" ] || continue
		case_dir=$out/$case_name
		runner_exit=0
		sh "$harness/run-app.sh" --app D32NetCalc --geom "$geom" --palette "$palette" \
		    --palette-dir "$out/palettes" --loopback "$shim" --loopback-sha256 "$shim_sha" \
		    --class D32Calc --out "$case_dir" --actions "$out/netcalc.dt" --check "$check" \
		    >"$out/$case_name.run.log" 2>&1 || runner_exit=$?
		if ! snapshot_rpc "$out/$case_name.rpc-after.tsv" ||
		    ! cmp -s "$out/rpc-before.tsv" "$out/$case_name.rpc-after.tsv"; then
			printf '%s\t%s\tchanged\n' "$case_name" "$runner_exit" >>"$out/cases.tsv"
			die "private portmapper differs after $case_name"
		fi
		printf '%s\t%s\tbaseline\n' "$case_name" "$runner_exit" >>"$out/cases.tsv"
		if [ "$runner_exit" -eq 0 ]; then
			(cd "$case_dir" && sha256 full.xwd client.xwd keyboard-*.xwd run.tsv status.txt check.log >SHA256SUMS)
			printf 'PASS %s native dtaction and keyboard checks\n' "$case_name"
		else
			failed=1
			printf 'FAIL %s runner exit %s\n' "$case_name" "$runner_exit" >&2
			tail -n 12 "$out/$case_name.run.log" >&2
			if [ -f "$case_dir/check.log" ]; then tail -n 12 "$case_dir/check.log" >&2; fi
		fi
	done
done
exit "$failed"
