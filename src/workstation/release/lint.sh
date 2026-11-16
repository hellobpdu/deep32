#!/bin/ksh
# release/lint.sh <repo> [site80.tgz real|test]: the BASE build lint (HARDENING 3.2 compensation 2, FIX-02, FIX-31).
# Without a site set it checks the sources; with one it also checks the packed set for its variant.
# Every finding prints "LINT FAIL <what>"; exit 1 if any.
set -u
repo=${1:?usage: lint.sh <repo> [site80.tgz real|test]}
tgz=${2:-}
variant=${3:-}
fails=0
f() { print -r -- "LINT FAIL $*"; fails=$((fails + 1)); }
p() { print -r -- "LINT ok   $*"; }
site=$repo/distrib/site
ids=$repo/distrib/ids/d32-ids.tsv

# 1. no runtime path loads pf rules: only tables (-t/-T), kill by label (-k) and reads (-s, -n) are allowed
scripts=$(find $site $repo/distrib/rc.d -type f | xargs grep -lE '^#!/bin/(ksh|sh)|^\. /etc/d32/site-lib.sh' 2>/dev/null)
bad=$(for s in $scripts; do grep -nE 'pfctl[^#]*( -f| -d| -e| -F *(rules|all)| -Fr| -Fa)' $s | grep -v '^[0-9]*:[[:space:]]*#' |
	grep -vE 'pfctl -nf|pfctl -nvf' | sed "s|^|${s#$repo/}:|"; done)
[ -z "$bad" ] && p "no script loads, enables, disables or flushes pf rules" || f "pfctl rule load/enable/flush in: $bad"

# 2. doas.conf: no persist or keepenv; nopass as root only for the lowering verbs
doas=$site/etc/doas.conf
grep -vE '^[[:space:]]*(#|$)' $doas | grep -qE 'persist|keepenv' && f "doas.conf has persist or keepenv" ||
	p "doas.conf: no persist, no keepenv"
badnp=$(grep -E '^permit nopass' $doas | grep ' as root ' |
	grep -vE 'deep32ctl args (web off|privacy mic off|privacy cam off|bench stop)$')
[ -z "$badnp" ] && p "doas.conf: nopass to root only for lowering verbs" || f "nopass to root: $badnp"
badtgt=$(grep -E '^permit' $doas | grep -vE ' cmd /usr/local/d32/sbin/[a-z0-9_-]+( args.*)?$')
[ -z "$badtgt" ] && p "doas.conf: every rule names a command in /usr/local/d32/sbin" || f "doas rule without a d32 command: $badtgt"

# 3. the uid table: unique names and ids, inside 3200-3299, known kinds and classes
awk '$1 ~ /^(group|user)$/ { print $2 }' $ids | sort | uniq -d | grep -q . && f "duplicate names in d32-ids.tsv" ||
	p "d32-ids.tsv: names unique"
awk '$1 ~ /^(group|user)$/ { print $3 }' $ids | sort | uniq -d | grep -q . && f "duplicate ids in d32-ids.tsv" ||
	p "d32-ids.tsv: ids unique"
out=$(awk '$1 ~ /^(group|user)$/ && ($3 < 3200 || $3 > 3299) { print $2 "=" $3 }' $ids)
[ -z "$out" ] && p "d32-ids.tsv: every id in 3200-3299" || f "ids outside 3200-3299: $out"
for cl in $(awk '$1 ~ /^(user|member)$/ { print $4 }' $ids | sort -u); do
	[ -f $site/etc/login.conf.d/$cl ] || f "class $cl used in d32-ids.tsv has no /etc/login.conf.d/$cl"
done
p "d32-ids.tsv: every class has a login.conf.d file"

# 4. every user and group pf names exists in the table or in OpenBSD's base
base="root _unbound _ntp _tftpd wheel"
known=" $(awk '$1 ~ /^(group|user|member)$/ { print $2 }' $ids | tr '\n' ' ') $base "
names=$(sed 's/#.*//' $site/etc/pf.conf | tr '\n' ' ' | sed 's/\\ / /g' |
	grep -oE '(user|group) (\{[^}]*\}|[_a-z0-9]+)' | sed -E 's/^(user|group) //; s/[{}]//g' | tr ' ' '\n' | grep . | sort -u)
miss=""
for n in $names; do case "$known" in *" $n "*) ;; *) miss="$miss $n";; esac; done
[ -z "$miss" ] && p "pf.conf: every user/group is in the table or base ($(echo $names | wc -w | tr -d ' ') names)" ||
	f "pf.conf names unknown users/groups:$miss"

# 5. rc.d scripts: rc_reload=NO, a daemon_user from the table, a login class named after the script
for s in $repo/distrib/rc.d/*; do
	n=${s##*/}
	grep -q '^rc_reload=NO' $s || f "$n: no rc_reload=NO"
	u=$(sed -n 's/^daemon_user="\(.*\)"/\1/p' $s)
	awk -v u="$u" '$1 == "user" && $2 == u { f = 1 } END { exit !f }' $ids || f "$n: daemon_user $u not in the table"
	[ -f $site/etc/login.conf.d/$n ] || f "$n: no /etc/login.conf.d/$n"
done
p "rc.d scripts: rc_reload=NO, table users, per-script classes"

# 6. no CRLF, no secret keys anywhere in the sources
grep -rlI "$(printf '\r')" $repo/distrib $repo/release $repo/tests/base 2>/dev/null | grep -q . && f "CRLF in sources" ||
	p "no CRLF in distrib/, release/, tests/base/"
grep -rlE 'untrusted comment:.*secret key|BEGIN [A-Z ]*PRIVATE KEY' $repo/distrib $repo/release 2>/dev/null | grep -v 'lint.sh' | grep -q . &&
	f "a secret key in the sources" || p "no secret key in distrib/ or release/"

# 7. the packed set: owners, and for a real set no test material
if [ -n "$tgz" ]; then
	nonroot=$(tar -tvzf $tgz | awk '$3 != "root" || $4 != "wheel" { print $NF }' | head -5)
	[ -z "$nonroot" ] && p "site set: every member root:wheel" || f "site set members not root:wheel: $nonroot"
	test_items=$(tar -tzf $tgz | grep -E 'd32-standin|etc/d32/standin|etc/d32/test-image|firstboot-check|d32test-|\.d32test$' || true)
	# OpenBSD's tar has no extract-to-stdout (its -O means old-style archives), so extract the one file
	xt=$(mktemp -d /tmp/d32lint.XXXXXXXX)
	tar -C $xt -xzf $tgz etc/sysctl.conf 2>/dev/null
	[ -s $xt/etc/sysctl.conf ] || f "site set has no etc/sysctl.conf"
	wx=$(grep -c '^kern\.wxabort=1' $xt/etc/sysctl.conf 2>/dev/null || true)
	rm -rf $xt
	case $variant in
	real) [ -z "$test_items" ] && p "real site set: no test material" || f "real site set carries test material: $test_items"
		[ "${wx:-0}" -eq 0 ] && p "real site set: no kern.wxabort (a test knob)" || f "real site set sets kern.wxabort";;
	test) [ -n "$test_items" ] && p "test site set: test material present as intended" || f "test set lacks its overlay"
		[ "${wx:-0}" -eq 1 ] && p "test site set: kern.wxabort=1 (FINAL-PLAN PY-4, HF-04)" || f "test set lacks kern.wxabort=1"
		tar -tzf $tgz | grep -q '\.d32test$' && f "test set carries the unmerged sysctl fragment";;
	esac
fi

# 8. disklabel templates: disklabel(8) -T takes "mountpoint size[-max] [share%]" lines only; a comment or blank line
# is "parse error on line N" in OpenBSD 8.0, and install.sub then stops with "Autopartitioning failed"
for t in $repo/distrib/install/disklabel.*; do
	badl=$(grep -nvE '^(/[^[:space:]]*|swap)[[:space:]]+[0-9]+[KMGT]?(-([0-9]+[KMGT]?|\*))?([[:space:]]+[0-9]+%)?$' $t | head -3)
	[ -z "$badl" ] && p "${t#$repo/}: label lines only" || f "${t#$repo/}: not a disklabel template line: $badl"
done
print -r -- "LINT: $fails failure(s)"
[ $fails -eq 0 ]
