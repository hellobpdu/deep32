#!/bin/sh
# T03 --check for a fresh D32Calc session and verified T12 test inputs.
set -eu

: "${D32_QA_WIN:?T03 did not select the calculator window}"
: "${D32_QA_OUT:?T03 did not set the evidence directory}"
geo_case=${1:-no-geo}
case $#:$geo_case in
0:no-geo|1:no-geo|1:geo-fixture) ;;
*) printf 'usage: sh qa-keyboard.sh [no-geo|geo-fixture]\n' >&2; exit 64 ;;
esac
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
driver=${D32_CALC_XT:-$here/../../tests/harness/d32xt}
case $driver in
/*) ;;
*) printf 'D32_CALC_XT must be an absolute path\n' >&2; exit 1 ;;
esac
test -x "$driver"

failure()
{
	rc=$?
	trap - EXIT
	if [ "$rc" -ne 0 ]; then
		/usr/X11R6/bin/xprop -id "$D32_QA_WIN" _D32_STATUS >&2 || :
		/usr/X11R6/bin/xwininfo -root -tree >"$D32_QA_OUT/keyboard-failure-tree.txt" 2>&1 || :
		set -- $("$driver" geom root)
		"$driver" xwd-region "$1" "$2" "$3" "$4" "$D32_QA_OUT/keyboard-failure.xwd" || :
	fi
	exit "$rc"
}
trap failure EXIT

keys()
{
	"$driver" key "$@"
}

status()
{
	"$driver" wait-prop "$D32_QA_WIN" _D32_STATUS "$1" 5
}

next_page()
{
	keys Ctrl+PageDown
	status "$1"
	keys Ctrl+f
}

capture_page()
{
	name=$1
	set -- $("$driver" geom "$D32_QA_WIN")
	"$driver" xwd-region "$1" "$2" "$3" "$4" "$D32_QA_OUT/keyboard-$name.xwd"
}

type_text()
{
	remaining=$1
	set --
	while [ -n "$remaining" ]; do
		tail=${remaining#?}
		key=${remaining%"$tail"}
		remaining=$tail
		case $key in
		' ') key=space ;;
		/) key=slash ;;
		.) key=period ;;
		:) key=colon ;;
		-) key=minus ;;
		esac
		set -- "$@" "$key"
		if [ "$#" -eq 32 ]; then
			keys "$@"
			set --
		fi
	done
	[ "$#" -eq 0 ] || keys "$@"
}

registry_lookup()
{
	keys Home Shift+End BackSpace
	type_text "$1"
	keys Return
	status "$2"
}

set -- $("$driver" geom root)
test "$#" -eq 4
case ${3}x${4} in
640x480|1366x768|1920x1080) ;;
*) printf 'Unexpected T03 screen size: %sx%s\n' "$3" "$4" >&2; exit 1 ;;
esac
screen_width=$3
screen_height=$4
set -- $("$driver" geom "$D32_QA_WIN")
test "$#" -eq 4
test "$3" -ge 600
test "$3" -le "$screen_width"
test "$4" -ge 400
test "$4" -le "$screen_height"

status '^Inspect an address, prefix, mask or range[.]$'
keys Ctrl+f 1 9 2 period 0 period 2 period 1 2 9 slash 2 4 Return
status '^\[!\] Host bits set .*192[.]0[.]2[.]0/24[?]$'
keys Tab Tab space
status '^\[OK\] Address normalized$'
capture_page address
printf 'PASS keyboard Address inspect and normalize\n'

next_page '^Plan allocations in the context prefix[.]$'
keys l a n Return 1 0 Return
status '^\[OK\] Plan computed[.] 1 request[.]$'
capture_page plan
printf 'PASS keyboard Plan host allocation\n'

next_page '^Test a wildcard or a prefix-list[.]$'
keys 1 9 2 period 0 period 2 period 0 Tab \
    0 period 0 period 0 period 2 5 5 Tab \
    1 9 2 period 0 period 2 period 1 2 9 Return
status '^\[OK\] Wildcard match yes[.] 256 possible addresses[.]$'
printf 'PASS keyboard Policy wildcard match\n'
keys Alt+l
status '^\[\?\] Enter policy inputs$'
type_text 'seq 10 permit 192.0.2.0/24 le 32'
keys Tab
type_text '192.0.2.7/32'
keys Return
status '^\[OK\] Prefix-list permit at sequence 10[.]$'
capture_page policy
printf 'PASS keyboard Policy first-match prefix-list\n'

next_page '^Convert a multicast or solicited-node address[.]$'
keys 2 2 4 period 0 period 0 period 1 Return
status '^\[OK\] Multicast calculation complete[.]$'
capture_page multicast
printf 'PASS keyboard Multicast group to Ethernet mapping\n'

next_page '^Search offline registry and country data[.]$'
registry_lookup documentation '^\[OK\] IANA registry search complete[.]$'
if [ "$geo_case" = geo-fixture ]; then
	known='^\[OK\] DB-IP estimated country: US[.]$'
	mixed='^\[\?\] DB-IP prefix has mixed or incomplete coverage[.]$'
	special='^\[\?\] Query includes special-purpose or non-global addresses[.]$'
	registry_lookup 8.8.8.1 "$known"
	registry_lookup 8.8.8.0/26 "$known"
	capture_page geo-country
	registry_lookup 8.8.8.0/25 "$mixed"
	registry_lookup 8.8.8.128/25 "$mixed"
	registry_lookup 8.8.8.128/26 '^\[\?\] DB-IP country estimate unknown[.]$'
	registry_lookup 2001:4860:4860::8888 '^\[OK\] DB-IP estimated country: CA[.]$'
	registry_lookup 192.0.2.1 "$special"
	registry_lookup ff02::1 "$special"
	printf 'PASS keyboard DB-IP country, uniform prefix, mixed, gap and special ranges\n'
else
	registry_lookup 8.8.8.1 '^\[\?\] DB-IP country estimate unavailable[.]$'
	printf 'PASS keyboard DB-IP unavailable without a snapshot\n'
fi
capture_page registries
registry_lookup us '^\[x\] NRO delegation source unavailable[.]$'
printf 'PASS keyboard Registries IANA search and explicit NRO gate\n'

next_page '^Enter a value and press Convert[.]$'
keys 2 5 5 Return
status '^\[OK\] Converted$'
capture_page base
printf 'PASS keyboard Base Converter\n'

# Do the long-form file chooser exercise once at the smallest geometry.
if [ "$screen_width" -eq 640 ] && awk -F '\t' '$1=="palette" && $2=="hp" {found=1} END {exit !found}' "$D32_QA_OUT/run.tsv"; then
	plans=$(realpath "$HOME/.dt/d32/plans")
	bulk=$plans/plan-512.json
	{
		printf '%s' '{"context":"2001:db8::/64","d32plan":1,"host_policy":"exact","requests":['
		i=0
		while [ "$i" -lt 512 ]; do
			[ "$i" -eq 0 ] || printf ','
			printf '{"kind":"child-len","name":"row %03d","value":"/128"}' "$i"
			i=$((i+1))
		done
		printf ']}\n'
	} >"$bulk"
	cp "$bulk" "$D32_QA_OUT/plan-512.json"
	keys Ctrl+o
	status '^\[\?\] Choose a plan file[.]$'
	sleep 0.3
	type_text "$bulk"
	keys Return
	status '^\[OK\] Plan imported$'
	frame=$(/usr/X11R6/bin/xprop -id "$D32_QA_WIN" _D32_PLAN_FRAME_US)
	printf '512-request import frame %s\n' "$frame"
	frame_ok=0
	printf '%s\n' "$frame" | awk -F '"' '{split($2,v," "); if (v[1]!=512 || v[2]!~/^[0-9]+$/ || v[2]>=100000) exit 1}' && frame_ok=1
	printf 'PASS keyboard 512-request import\n'
	capture_page plan-512
	keys Ctrl+s
	status '^\[\?\] Choose a plan file[.]$'
	sleep 0.3
	export_path=$plans/plan-export.json
	type_text "$export_path"
	keys Return
	status '^\[OK\] Plan exported$'
	test "$(stat -f %Lp "$export_path")" = 600
	cp "$export_path" "$D32_QA_OUT/plan-export.json"
	printf 'PASS keyboard 512-request private export\n'
	keys Ctrl+PageUp
	status '^Inspect an address, prefix, mask or range[.]$'
	keys Ctrl+f Home Shift+End BackSpace
	type_text '2001:0db8::0001/128'
	keys Return
	status '^\[OK\] Address calculated$'
	capture_page address-ipv6
	printf 'PASS keyboard IPv6 address after plan family change\n'
	keys Ctrl+q
	settings=$HOME/.dt/d32/d32calc.conf
	tries=0
	until [ -f "$settings" ] && grep -qx 'family=6' "$settings"; do
		tries=$((tries+1))
		[ "$tries" -lt 20 ]
		sleep 0.1
	done
	test "$(stat -f %Lp "$settings")" = 600
	printf 'PASS keyboard close and private family preference\n'
	test "$frame_ok" -eq 1
fi
