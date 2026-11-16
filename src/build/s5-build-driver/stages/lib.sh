# stages/lib.sh: shared by s5-build.sh and every stage (POSIX sh; OpenBSD 8.0 /bin/sh on deep32-builder).
# Sourced, never run. Layout on 910 (made by stage 10 from what the lead copies into /build/dist/in):
#   /build/dist/build.conf          the lead's settings (LAB-RUN.md): release id, key paths, tree hash, jobs
#   /build/dist/in/                 pushed inputs: tree-<hash>.tar + PIN.sha256, pyrag/, obsd80/, ports/, pkgrepo/,
#                                   firmware/8.0/, TAXONOMY.json, mac-vendor .tgz
#   /build/dist/tree/<hash>/        the pinned build tree, read-only after stage 10
#   /build/dist/work/<stage>/       scratch of a stage;  /build/dist/out/<release-id>/  results
#   /build/dist/state/<stage>.done  restart marker: the stamp of the inputs it ran with
#   /build/dist/logs/<stage>.log    one log per stage; s5-build.log the run's summary
#   /build/lanes/... /build/src/... the old pipeline's own layout, filled from the tree and the inputs (stage 10)
D32_ROOT=${D32_ROOT:-/build/dist}
D32_HOST=deep32-builder
export LC_ALL=C TZ=UTC
log() { printf '%s s5: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die() { log "FAIL: $*" >&2; exit 1; }

load_conf() {	# load_conf: reads build.conf; every key it needs must be set
	c=$D32_ROOT/build.conf
	[ -r "$c" ] || die "no $c (LAB-RUN.md step 'build.conf')"
	# shellcheck disable=SC1090
	. "$c"
	: "${D32_RELEASE_ID:?build.conf: D32_RELEASE_ID}" "${D32_TREE_HASH:?build.conf: D32_TREE_HASH}"
	: "${D32_KEYSET:?build.conf: D32_KEYSET (throwaway or train)}" "${D32_JOBS:=12}"
	: "${D32_TEST_ROOTDISK:=sd1}" "${D32_SOURCE_DATE:=1794614400}"
	case $D32_KEYSET in throwaway|train) ;; *) die "D32_KEYSET must be throwaway or train" ;; esac
	K=$D32_ROOT/keys/$D32_KEYSET
	D32_RELEASE_SEC=${D32_RELEASE_SEC:-$K/d32-release.sec}
	D32_RELEASE_PUB=${D32_RELEASE_PUB:-$K/d32-release.pub}
	D32_LIBRARY_SEC=${D32_LIBRARY_SEC:-$K/d32-library.sec}
	D32_LIBRARY_PUB=${D32_LIBRARY_PUB:-$K/d32-library.pub}
	SOURCE_DATE_EPOCH=$D32_SOURCE_DATE
	export D32_RELEASE_ID D32_TREE_HASH D32_KEYSET D32_JOBS D32_TEST_ROOTDISK SOURCE_DATE_EPOCH
	export D32_RELEASE_SEC D32_RELEASE_PUB D32_LIBRARY_SEC D32_LIBRARY_PUB
	T=$D32_ROOT/tree/$D32_TREE_HASH
	OUT=$D32_ROOT/out/$D32_RELEASE_ID
	S=/build/lanes/ship/src
	export T OUT S
}

host_ok() {	# host_ok: 0 only on the new build VM
	[ "$(hostname -s 2>/dev/null)" = "$D32_HOST" ] && [ "$(uname -s)" = OpenBSD ]
}

# BEGIN old-vm guard: the only place that names the old build VM and its test key, to refuse them
old_vm_guard() {
	case $(hostname -s 2>/dev/null) in legacy-builder|legacy-test-node|d32-u10-rehearsal) die "this is the old build VM: refused" ;; esac
	for f in /build/keys/d32test-release.sec /build/keys/d32test-library.sec; do
		[ ! -e "$f" ] || die "an old d32test key file exists on this host ($f): refused, never use the old key"
	done
}
tree_names_old_key() {	# tree_names_old_key FILE: 0 if a tree script still signs with the old test key path
	grep -q '/build/keys/d32test' "$1"
}
# END old-vm guard

stamp_of() {	# stamp_of STAGE-FILE: the stamp a stage's marker must hold to be skipped
	{ cat "$1" "${ST:-$D32_ROOT/stages}/lib.sh"; printf '%s\n' "$D32_TREE_HASH" "$D32_RELEASE_ID" "$D32_KEYSET" \
		"$D32_JOBS" "$D32_TEST_ROOTDISK" "$D32_SOURCE_DATE"; } | sha256 -q
}

as_build() {	# as_build CMD...: run a command as the build user (the old pipeline's d32build)
	su -s /bin/sh d32build -c "$*"
}

need_file() { [ -f "$1" ] || die "missing input: $1"; }
need_dir() { [ -d "$1" ] || die "missing input folder: $1"; }
check_sha() {	# check_sha FILE HEX
	got=$(sha256 -q "$1") || die "cannot hash $1"
	[ "$got" = "$2" ] || die "$1: sha256 $got, expected $2"
}
