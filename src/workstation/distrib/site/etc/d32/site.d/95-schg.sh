# 95-schg: last. Set the immutable flag on every path of /etc/d32/schg.list (ISO 2.7, DISTRO C11, FIX-19). At
# securelevel 1 these cannot be cleared; a change needs a maintenance boot. Patterns that match nothing are listed.
. /etc/d32/site-lib.sh
list=/etc/d32/schg.list
n=0
none=""
while read -r pat; do
	case $pat in
	""|"#"*)
		continue
		;;
	esac
	hit=0
	for f in $pat; do
		[ -e "$f" ] || continue
		chflags schg "$f"
		n=$((n + 1))
		hit=1
	done
	[ $hit -eq 1 ] || none="$none $pat"
done <$list
d32 "item schg set on $n paths"
[ -z "$none" ] || d32 "item schg patterns with no file in this image:$none"
