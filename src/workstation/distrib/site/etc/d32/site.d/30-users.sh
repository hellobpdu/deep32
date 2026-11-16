# 30-users: groups, daemon and domain users, and the desktop user's groups and class, from /etc/d32/ids.tsv (the
# uid and gid table). Idempotent: an existing name with the same id is accepted; one with another id stops the
# install. The desktop user is the account install.sub made (uid 1000, its name asked at install): no default.
. /etc/d32/site-lib.sh
ids=/etc/d32/ids.tsv
[ -f $ids ] || die "no $ids"
u=$(awk -F: '$3 == 1000 { print $1; exit }' /etc/passwd)
[ -n "$u" ] || die "no desktop user (uid 1000): the install makes it"
print -r -- "$u" >/etc/d32/desktop-user
chmod 0444 /etc/d32/desktop-user
for f in /etc/doas.conf $ids; do
	[ -f $f ] && grep -q '@D32_USER@' $f && sed -i "s/@D32_USER@/$u/g" $f
done
d32 "item desktop user: $u (uid 1000)"
has_group() { grep -q "^$1:" /etc/group; }
gid_of() { grep "^$1:" /etc/group | cut -d: -f3; }
ensure_group() {
	if has_group $1; then
		[ "$(gid_of $1)" = "$2" ] || die "group $1 exists with gid $(gid_of $1), the table says $2"
	else
		groupadd -g $2 $1
	fi
}
ng=0
nu=0
while read -r kind name id class home shell groups purpose; do
	case $kind in
	""|"#"*)
		continue
		;;
	group)
		ensure_group $name $id
		ng=$((ng + 1))
		;;
	user)
		ensure_group $name $id
		if id -u $name >/dev/null 2>&1; then
			[ "$(id -u $name)" = "$id" ] || die "user $name exists with uid $(id -u $name), the table says $id"
		else
			gecos=$(print -r -- "Deep32 $purpose" | cut -d'(' -f1 | tr -d ':,' | cut -c1-48 | sed 's/[[:space:]]*$//')
			useradd -u $id -g $name -L $class -d $home -s $shell -c "$gecos" $name
		fi
		usermod -L $class $name
		[ "$groups" = "-" ] || usermod -G $groups $name
		case $home in
		/home/*)
			install -d -o $name -g $name -m 0750 $home
			;;
		esac
		nu=$((nu + 1))
		;;
	member)
		if id -u $name >/dev/null 2>&1; then
			usermod -L $class $name
			[ "$groups" = "-" ] || usermod -G $groups $name
			d32 "item $name: class $class, groups $groups"
		else
			d32 "item no account $name yet; skipped"
		fi
		;;
	*)
		die "ids.tsv: unknown kind $kind for $name"
		;;
	esac
done <$ids
# the desktop user is never in _wireshark (DISTRO C7): capture goes through d32-capture
if id -u "$u" >/dev/null 2>&1 && has_group _wireshark; then
	if grep '^_wireshark:' /etc/group | cut -d: -f4 | tr ',' '\n' | grep -qx "$u"; then
		die "$u is in _wireshark"
	fi
fi
# d32web's home holds the In-Tray, so group d32xfer may traverse it (BROWSER-MEDIA 4.1)
if id -u d32web >/dev/null 2>&1; then
	install -d -o d32web -g d32xfer -m 0750 /home/d32web
fi
d32 "item $ng groups and $nu users from the table"
