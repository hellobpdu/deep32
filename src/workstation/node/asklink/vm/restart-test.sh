#!/bin/sh
# root: the parent's restart budget and isolation (review 2026-11-06: the parent wedged after any child death).
# A test node "node3" on 127.0.0.1:17480 where a holder accepts TCP and never speaks TLS, so a pairing console
# can be left waiting.  Every check prints PASS/FAIL; the summary line is "RESULT: <pass> passed, <fail> failed".
#   restart-test.sh            (MALLOC_OPTIONS=S is set for the daemon)
cd /build/lanes/asklink || exit 1
L=/build/lanes/asklink
N=node3
R=$L/run/$N
PORT=17480
pass=0; fail=0
ok() { pass=$((pass + 1)); echo "PASS $*"; }
no() { fail=$((fail + 1)); echo "FAIL $*"; }
t() { python3 -I -c 'import time; print("%.2f" % time.time())'; }
pid_of() {	# ui|link
	if [ "$1" = ui ]; then u=_d32chat; else u=_d32upw; fi
	ps -axo pid,user,command | awk -v u="$u" -v r="-P $1" '$2 == u && index($0, r) {print $1}' | head -1
}
status_ok() {	# prints the link line; fails if chatctl does not answer within 5 s
	out=$($R/bin/d32-chatctl -s $R/sock/chatd.sock status 2>&1) || return 1
	echo "$out" | head -1
}
uplink_ok() {	# a UI client gets a status answer from the (new) ui child within 3 s
	python3 -I -c "
import json, socket, sys
s = socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect('$R/sock/uplink.sock')
s.sendall(b'{\"op\":\"status\"}\n'); b = b''
while b'\n' not in b: b += s.recv(4096)
e = json.loads(b.split(b'\n')[0]); sys.exit(0 if e.get('op') in ('status', 'link') else 1)" 2>/dev/null
}

sh vm/node.sh stop $N >/dev/null 2>&1
sh vm/node.sh prepare $N $PORT >/dev/null || exit 1
python3 -I -c "
import socket, time
l = socket.socket(); l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); l.bind(('127.0.0.1', $PORT)); l.listen(8)
held = []
end = time.time() + 300
l.settimeout(1)
while time.time() < end:
    try: held.append(l.accept()[0])
    except OSError: pass" &
holder=$!
MALLOC_OPTIONS=S; export MALLOC_OPTIONS
$R/bin/d32-chatd -d -v -f $R/chatd.conf > $R/chatd.log 2>&1 &
parent=$!
unset MALLOC_OPTIONS
sleep 1.5
echo "parent $parent, ui $(pid_of ui), link $(pid_of link)"

# 1: the ui child crashes
old=$(pid_of ui); kill -KILL $old; sleep 1.5
new=$(pid_of ui)
s0=$(t); line=$(status_ok); rc=$?; s1=$(t)
if [ -n "$new" ] && [ "$new" != "$old" ] && [ $rc -eq 0 ] && uplink_ok; then
	ok "1 ui child killed: restarted (pid $old -> $new); d32-chatctl status answered in $(echo "$s1 - $s0" | bc) s; uplink.sock answers: $line"
else
	no "1 ui child killed: new ui '$new', status rc $rc: $line"
fi

# 2: the link child crashes while the ui child is stopped (SIGSTOP): the bounded stop needs SIGKILL
ui=$(pid_of ui); kill -STOP $ui
old=$(pid_of link); s0=$(t); kill -KILL $old; sleep 3.5
newl=$(pid_of link); newu=$(pid_of ui)
line=$(status_ok); rc=$?
if [ -n "$newl" ] && [ "$newl" != "$old" ] && [ -n "$newu" ] && [ "$newu" != "$ui" ] && [ $rc -eq 0 ] && uplink_ok; then
	ok "2 link child killed with the ui child SIGSTOPped: both restarted (link $old -> $newl, ui $ui -> $newu; the stopped child was SIGKILLed after 2 s); status: $line"
else
	no "2 link killed with ui stopped: link '$newl' ui '$newu' status rc $rc: $line"
fi
kill -KILL $ui 2>/dev/null

# 3: a pairing console is waiting (TCP up, TLS never answers) when the link child crashes
$R/bin/d32-chatctl-test -s $R/sock/chatd.sock pair work > $R/pair.out 2>&1 &
ctl=$!
sleep 2
old=$(pid_of link); kill -KILL $old
i=0; while kill -0 $ctl 2>/dev/null && [ $i -lt 30 ]; do sleep 0.1; i=$((i + 1)); done
if kill -0 $ctl 2>/dev/null; then
	no "3 pairing console still waiting 3 s after the link child died"; kill $ctl
else
	wait $ctl; crc=$?
	if grep -q "code 11" $R/pair.out && [ $crc -ne 0 ]; then
		ok "3 link child killed during a pairing window: the console was told at once (exit $crc: $(grep -o 'pairing refused or failed (D32U code 11)' $R/pair.out | head -1)); nothing pinned: $(ls $R/state | tr '\n' ' ')"
	else
		no "3 console result: exit $crc $(tail -1 $R/pair.out)"
	fi
fi
sleep 1.5
line=$(status_ok) && ok "3b restarted again after the third death; status: $line" || no "3b status after the third death"

# 4: the fourth death inside ten minutes isolates the uplink
old=$(pid_of link); kill -KILL $old; sleep 3
left=$(ps -axo pid,ppid,command | awk -v p=$parent '$2 == p' | wc -l | tr -d ' ')
line=$(status_ok); rc=$?
if [ "$left" = 0 ] && [ $rc -eq 0 ] && echo "$line" | grep -qi isolated && kill -0 $parent; then
	ok "4 fourth child death in ten minutes: ISOLATED, no children left, the parent still answers: $line"
else
	no "4 isolation: children left $left, status rc $rc: $line"
fi
$R/bin/d32-chatctl-test -s $R/sock/chatd.sock pair work > $R/pair2.out 2>&1 < /dev/null
grep -q "code 2" $R/pair2.out && ok "4b pair refused while isolated: $(tail -1 $R/pair2.out)" || no "4b pair while isolated: $(tail -1 $R/pair2.out)"

# 5: SIGTERM ends the parent promptly (rc.d stop works)
s0=$(t); kill -TERM $parent
i=0; while kill -0 $parent 2>/dev/null && [ $i -lt 30 ]; do sleep 0.1; i=$((i + 1)); done
if kill -0 $parent 2>/dev/null; then
	no "5 parent alive 3 s after SIGTERM (wchan $(ps -o wchan= -p $parent))"; kill -KILL $parent
else
	ok "5 SIGTERM: parent exited in $(echo "$(t) - $s0" | bc) s"
fi
kill $holder 2>/dev/null
echo "---- parent log (restarts, isolation) ----"
grep -E "parent: (child|restart|started|a child|terminating|link)" $R/chatd.log | sed 's/^[0-9.]* //'
ps -axo pid,user,command | grep -E "run/$N/bin/d32-chatd|-P (ui|link)" | grep -v grep && no "processes left" || ok "6 no lane process left"
cp $R/chatd.log $L/run/$N-restart.log
sh vm/node.sh wipe $N >/dev/null
echo "RESULT: $pass passed, $fail failed"
[ $fail -eq 0 ]
