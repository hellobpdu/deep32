#!/bin/sh
# Capture only the T02 sample. The application-action harness belongs to T03.
set -eu
umask 077

if [ "$#" -ne 2 ]; then
	printf 'usage: sh sample-screens.sh /path/to/d32ui-sample /path/to/new-output\n' >&2
	exit 64
fi
sample=$1
out=$2
case $sample in /*) ;; *) exit 64 ;; esac
case $out in /*) ;; *) exit 64 ;; esac
test -x "$sample"
mkdir "$out"
PATH=/usr/X11R6/bin:/usr/local/bin:/usr/bin:/bin
export PATH

xp= app= authority=
stop_app() {
	if [ -n "$app" ]; then
		kill "$app" 2>/dev/null || :
		wait "$app" 2>/dev/null || :
		app=
	fi
}
stop_sample() {
	stop_app
	if [ -n "$xp" ]; then
		kill "$xp" 2>/dev/null || :
		wait "$xp" 2>/dev/null || :
		xp=
	fi
	if [ -n "$authority" ]; then
		rm -f "$authority"
		authority=
	fi
}
trap stop_sample 0
trap 'exit 130' INT TERM HUP

wait_status() {
	for attempt in $(jot 100); do
		if xprop -id "$win" _D32_STATUS | grep -F "\"$1\"" >/dev/null; then
			return 0
		fi
		kill -0 "$app"
		sleep 0.1
	done
	xprop -id "$win" _D32_STATUS >&2
	return 1
}

wait_dialog() {
	for attempt in $(jot 100); do
		dialog=$(xdotool search --onlyvisible --name "^$1$" 2>/dev/null |
		    sed -n '1p')
		[ -z "$dialog" ] || return 0
		kill -0 "$app"
		sleep 0.1
	done
	printf 'missing %s dialog\n' "$1" >&2
	return 1
}

wait_sample() {
	for attempt in $(jot 120); do
		kill -0 "$app"
		win=$(xdotool search --onlyvisible --class '^D32UISample$' 2>/dev/null |
		    sed -n '1p')
		[ -z "$win" ] || return 0
		sleep 0.1
	done
	printf 'missing sample window\n' >&2
	return 1
}

wait_closed() {
	for attempt in $(jot 100); do
		if ! xwininfo -id "$dialog" >/dev/null 2>&1; then
			return 0
		fi
		sleep 0.1
	done
	printf 'dialog did not close\n' >&2
	return 1
}

smoke() {
	xdotool key --clearmodifiers ctrl+f
	wait_status '[!] Find in sample rows'
	xdotool type --clearmodifiers 'worker'
	xdotool key Return
	wait_status '[OK] Matching row selected'
	xdotool key F1
	wait_dialog Help
	xprop -id "$dialog" _D32_HANDLING | grep -F '"public"' >/dev/null
	xdotool windowfocus --sync "$dialog"
	xdotool key Escape
	xdotool windowfocus --sync "$win"
	xdotool key F5
	wait_dialog Progress
	xprop -id "$dialog" _D32_HANDLING | grep -F '"public"' >/dev/null
	xdotool windowfocus --sync "$dialog"
	xdotool key Escape
	wait_status '[!] Worker cancelled'
	if pgrep -P "$app" >/dev/null; then
		printf 'worker was not reaped\n' >&2
		return 1
	fi
	printf 'PASS Find, Help and worker cancellation/reap\n'
}

restricted_smoke() {
	stop_app
	"$sample" -restricted >"$case_dir/restricted.log" 2>&1 &
	app=$!
	wait_sample
	xprop -id "$win" _D32_HANDLING | grep -F '"restricted"' >/dev/null
	xdotool windowfocus --sync "$win"
	xdotool key --clearmodifiers ctrl+Next
	wait_status '[OK] Shortcuts selected'
	xdotool key --clearmodifiers ctrl+Prior
	wait_status '[OK] Overview selected'
	# Reset View occupies this point in the 600x400 sample's controls row.
	for key in Return Escape; do
		xdotool mousemove --window "$win" 250 152 click 1
		wait_dialog Confirm
		xprop -id "$dialog" _D32_HANDLING | grep -F '"restricted"' >/dev/null
		xdotool windowfocus --sync "$dialog"
		xdotool key "$key"
		wait_closed
		wait_status '[OK] Reset cancelled'
		xdotool windowfocus --sync "$win"
	done
	xdotool key --clearmodifiers ctrl+o
	wait_dialog 'Open File'
	xprop -id "$dialog" _D32_HANDLING | grep -F '"restricted"' >/dev/null
	xdotool windowfocus --sync "$dialog"
	xdotool key Escape
	wait_closed
	xdotool windowfocus --sync "$win"
	xdotool key F5
	wait_dialog Progress
	xprop -id "$dialog" _D32_HANDLING | grep -F '"restricted"' >/dev/null
	wait_status '[OK] Worker complete'
	if pgrep -P "$app" >/dev/null; then
		printf 'completed worker was not reaped\n' >&2
		return 1
	fi
	printf 'PASS restricted shell/dialogs, tab accelerators, safe Return/Escape and worker completion\n'
}

printf 'display\tclient_width\tclient_height\n' >"$out/geometry.tsv"
n=310
for size in 640x480 1366x768 1920x1080; do
	case_dir=$out/$size
	mkdir "$case_dir" "$case_dir/home"
	while :; do
		n=$((n + 1))
		[ "$n" -lt 500 ] || exit 69
		[ ! -e "/tmp/.X$n-lock" ] && [ ! -S "/tmp/.X11-unix/X$n" ] && break
	done
	DISPLAY=:$n
	XAUTHORITY=$case_dir/xauth
	authority=$XAUTHORITY
	HOME=$case_dir/home
	export DISPLAY XAUTHORITY HOME
	touch "$XAUTHORITY"
	xauth -q add "$DISPLAY" . "$(openssl rand -hex 16)"
	Xvfb "$DISPLAY" -screen 0 "${size}x24" -nolisten tcp -auth "$XAUTHORITY" \
	    >"$case_dir/xvfb.log" 2>&1 &
	xp=$!
	i=0
	until xdpyinfo >/dev/null 2>&1; do
		kill -0 "$xp"
		i=$((i + 1))
		[ "$i" -lt 120 ] || exit 75
		sleep 0.1
	done
	if [ -f /usr/local/dt/share/cde/fontaliases/fonts.dir ]; then
		xset +fp /usr/local/dt/share/cde/fontaliases
		xset fp rehash
	fi
	"$sample" >"$case_dir/sample.log" 2>&1 &
	app=$!
	wait_sample
	xprop -id "$win" WM_CLASS _D32_HANDLING _D32_STATUS >"$case_dir/properties.txt"
	grep -F '"D32UISample"' "$case_dir/properties.txt" >/dev/null
	grep -F '_D32_HANDLING(STRING) = "public"' "$case_dir/properties.txt" >/dev/null
	grep -F '_D32_STATUS(UTF8_STRING) = "[OK] Ready"' "$case_dir/properties.txt" >/dev/null
	xdotool getwindowgeometry --shell "$win" >"$case_dir/geometry.txt"
	w=$(sed -n 's/^WIDTH=//p' "$case_dir/geometry.txt")
	h=$(sed -n 's/^HEIGHT=//p' "$case_dir/geometry.txt")
	test "$w" -ge 600
	test "$w" -le "${size%x*}"
	test "$h" -ge 400
	test "$h" -le "${size#*x}"
	printf '%s\t%s\t%s\n' "$size" "$w" "$h" >>"$out/geometry.tsv"
	xdotool windowmove --sync "$win" "$(((${size%x*} - w) / 2))" \
	    "$(((${size#*x} - h) / 2))"
	xdotool windowfocus --sync "$win"
	sleep 0.25
	xwd -root -silent -out "$case_dir/root.xwd"
	xwdtopnm "$case_dir/root.xwd" >"$case_dir/root.pnm" 2>"$case_dir/convert.log"
	pnmtopng "$case_dir/root.pnm" >"$out/$size.png"
	rm -f "$case_dir/root.xwd" "$case_dir/root.pnm"
	if [ "$size" = 640x480 ]; then
		smoke
		restricted_smoke
	fi
	stop_sample
done
(cd "$out" && sha256 640x480.png 1366x768.png 1920x1080.png >SHA256)
cat "$out/geometry.tsv" "$out/SHA256"
