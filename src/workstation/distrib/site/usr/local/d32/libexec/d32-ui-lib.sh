# /usr/local/d32/libexec/d32-ui-lib.sh: shared helpers of the Deep32 Workstation screens (First Start, Pairing,
# the console words). Sourced, never run. POSIX sh only (no local, no arrays, no printf), so OpenBSD's ksh, dtksh and
# the test harness's bash all run it. Every word a person reads comes from the strings table, looked up by id.
#
# Values that may be secret for a while (a pairing code, a Tailscale sign-in address) are handled with shell builtins
# only: they never reach an external command's arguments or environment, a file or a log.
#
# Test hook: D32_ROOT (empty on the system) prefixes every absolute path.

D32UI_STRINGS=${D32UI_STRINGS:-${D32_ROOT:-}/usr/local/share/d32/setup/strings.tsv}
D32UI_NL='
'
D32UI_H='[0-9a-fA-F]'
D32UI_P32="*$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H$D32UI_H*"

# d32ui_text ID: the template of ID in D32UI_T (the ID itself when the table has no such row)
d32ui_text() {
	D32UI_T=$(D32UI_ID=$1 awk -F '\t' '$1 == ENVIRON["D32UI_ID"] { print $2; f = 1; exit } END { if (!f) exit 1 }' \
		"$D32UI_STRINGS" 2>/dev/null) || D32UI_T=$1
}

# d32ui_sub KEY VALUE: every {KEY} in D32UI_T becomes VALUE (parameter expansion only)
d32ui_sub() {
	D32UI_o=
	D32UI_r=$D32UI_T
	while :; do
		case $D32UI_r in
		*"{$1}"*)
			D32UI_o=$D32UI_o${D32UI_r%%"{$1}"*}$2
			D32UI_r=${D32UI_r#*"{$1}"} ;;
		*)	break ;;
		esac
	done
	D32UI_T=$D32UI_o$D32UI_r
}

# d32ui_fmt ID [KEY VALUE]...: the finished text in D32UI_T ({nl} is a new line)
d32ui_fmt() {
	d32ui_text "$1"
	shift
	while [ $# -ge 2 ]; do
		d32ui_sub "$1" "$2"
		shift 2
	done
	d32ui_sub nl "$D32UI_NL"
}

# d32ui_say ID [KEY VALUE]...: print the finished text
d32ui_say() {
	d32ui_fmt "$@"
	echo "$D32UI_T"
}

# ---- sizes and times ------------------------------------------------------------------------------------------
# d32ui_size BYTES: "850 MB" below 1 GB, else "26.8 GB" (binary units, as df -h counts)
d32ui_size() {
	case $1 in ''|*[!0-9]*) D32UI_T=?; return 1 ;; esac
	if [ "$1" -lt 1073741824 ]; then
		D32UI_T="$(( ($1 + 524288) / 1048576 )) MB"
	else
		D32UI_n=$(( ($1 * 10 + 536870912) / 1073741824 ))
		D32UI_T="$(( D32UI_n / 10 )).$(( D32UI_n % 10 )) GB"
	fi
}

# d32ui_mmss SECONDS: "4:52"
d32ui_mmss() {
	case $1 in ''|*[!0-9]*) D32UI_T=0:00; return 1 ;; esac
	D32UI_s=$(( $1 % 60 ))
	[ $D32UI_s -lt 10 ] && D32UI_s=0$D32UI_s
	D32UI_T="$(( $1 / 60 )):$D32UI_s"
}

# ---- C6 status lines (contract C6; the field rules the Workstation's first start writes) -------------------------
D32UI_C6KEYS='boot stage state version name signin pair pair_with expires error'

# d32ui_c6_ok KEY VALUE: 0 when VALUE is allowed for KEY
d32ui_c6_ok() {
	[ -n "$2" ] || return 1
	case $2 in *[![:print:]]*|*' '*) return 1 ;; esac
	case $1 in
	boot)	[ ${#2} -eq 8 ] || return 1
		case $2 in *[!0-9a-f]*) return 1 ;; esac ;;
	stage)	case $2 in install|firstboot|keys|signin|tailnet|content|import|pairing|ready) ;; *) return 1 ;; esac ;;
	state)	case $2 in running|waiting|done|failed) ;; *) return 1 ;; esac ;;
	version) [ ${#2} -le 64 ] || return 1
		case $2 in *[!A-Za-z0-9._+-]*) return 1 ;; esac ;;
	name)	[ ${#2} -le 63 ] || return 1
		case $2 in [a-z]*) ;; *) return 1 ;; esac
		case $2 in *[!a-z0-9-]*) return 1 ;; esac ;;
	signin)	case $2 in https://login.tailscale.com/?*) ;; *) return 1 ;; esac
		D32UI_sv=${2#https://login.tailscale.com/}
		[ ${#D32UI_sv} -le 256 ] || return 1
		case $D32UI_sv in *[!A-Za-z0-9/_?=.-]*) return 1 ;; esac ;;
	pair)	[ ${#2} -eq 10 ] || return 1
		case $2 in *[!A-Z2-7]*) return 1 ;; esac ;;
	pair_with) case $2 in forge|workstation|relay|headend) ;; *) return 1 ;; esac ;;
	expires) case $2 in [1-9]|[1-9][0-9]|[1-9][0-9][0-9]) [ "$2" -le 300 ] || return 1 ;; *) return 1 ;; esac ;;
	error)	[ ${#2} -le 48 ] || return 1
		case $2 in [A-Z][A-Z0-9_]*) ;; *) return 1 ;; esac
		case $2 in *[!A-Z0-9_]*) return 1 ;; esac ;;
	*)	return 1 ;;
	esac
	return 0
}

# d32ui_secret_shape TEXT: 0 when TEXT looks like a token, key or hash (C6 never carries one)
d32ui_secret_shape() {
	case $1 in
	$D32UI_P32|*tskey-*|*'PRIVATE KEY'*|*'$2a$'*|*'$2b$'*|*'$2y$'*) return 0 ;;
	esac
	return 1
}

# d32ui_c6 LINE: parse one C6 line into D32C6_seq, D32C6_node and D32C6_<key>; 1 when it is not a valid line
d32ui_c6() {
	D32C6_seq= D32C6_node= D32C6_boot= D32C6_stage= D32C6_state= D32C6_version= D32C6_name= D32C6_signin=
	D32C6_pair= D32C6_pair_with= D32C6_expires= D32C6_error= D32C6_msg=
	case $1 in 'D32S/1 '*) ;; *) return 1 ;; esac
	[ ${#1} -le 511 ] || return 1
	D32UI_r=${1#D32S/1 }
	case $D32UI_r in
	*' msg='*)
		D32C6_msg=${D32UI_r#* msg=}
		D32UI_r=${D32UI_r%% msg=*}
		[ -n "$D32C6_msg" ] && [ ${#D32C6_msg} -le 240 ] || return 1
		case $D32C6_msg in *[![:print:]]*|*\\*) return 1 ;; esac ;;
	esac
	D32UI_seen=' '
	set -f
	set -- $D32UI_r
	set +f
	[ $# -ge 2 ] || return 1
	case $1 in [1-9]*) ;; *) return 1 ;; esac
	case $1 in *[!0-9]*) return 1 ;; esac
	[ ${#1} -le 9 ] || return 1
	case $2 in workstation|forge|headless) ;; *) return 1 ;; esac
	D32C6_seq=$1
	D32C6_node=$2
	shift 2
	for D32UI_kv in "$@"; do
		D32UI_k=${D32UI_kv%%=*}
		D32UI_v=${D32UI_kv#*=}
		[ "$D32UI_k" != "$D32UI_kv" ] || return 1
		case " $D32UI_C6KEYS " in *" $D32UI_k "*) ;; *) return 1 ;; esac
		case $D32UI_seen in *" $D32UI_k "*) return 1 ;; esac
		D32UI_seen="$D32UI_seen$D32UI_k "
		d32ui_c6_ok "$D32UI_k" "$D32UI_v" || return 1
		d32ui_secret_shape "$D32UI_v" && return 1
		eval "D32C6_$D32UI_k=\$D32UI_v"
	done
	[ -n "$D32C6_msg" ] && d32ui_secret_shape "$D32C6_msg" && return 1
	[ -n "$D32C6_stage" ] && [ -n "$D32C6_state" ] || return 1
	if [ "$D32C6_state" = failed ]; then [ -n "$D32C6_error" ] || return 1; else [ -z "$D32C6_error" ] || return 1; fi
	if [ -n "$D32C6_signin" ]; then [ "$D32C6_stage $D32C6_state" = "signin waiting" ] || return 1; fi
	if [ -n "$D32C6_pair" ]; then
		[ "$D32C6_stage $D32C6_state" = "pairing waiting" ] && [ -n "$D32C6_expires" ] || return 1
	fi
	[ -z "$D32C6_expires" ] || [ -n "$D32C6_pair" ] || return 1
	[ -z "$D32C6_pair_with" ] || [ "$D32C6_stage" = pairing ] || return 1
	return 0
}

# ---- pairing codes (contract C8: [A-Z2-7]{10}; the normalisation of the pairing reference) ------------------------
# d32ui_code INPUT: D32UI_CODE (canonical) and 0, or D32UI_WHY (the code_reason) and 1. Builtins only.
d32ui_code() {
	D32UI_CODE=
	D32UI_WHY=
	D32UI_r=$1
	if [ ${#D32UI_r} -gt 64 ]; then D32UI_WHY=too-long; return 1; fi
	D32UI_o=
	D32UI_bad=
	while [ -n "$D32UI_r" ]; do
		D32UI_c=${D32UI_r%"${D32UI_r#?}"}
		D32UI_r=${D32UI_r#?}
		case $D32UI_c in
		[[:print:]]|[[:cntrl:]]) ;;
		*)	D32UI_WHY=non-ascii; return 1 ;;
		esac
		case $D32UI_c in
		' '|'	'|-) continue ;;
		a) D32UI_c=A ;; b) D32UI_c=B ;; c) D32UI_c=C ;; d) D32UI_c=D ;; e) D32UI_c=E ;; f) D32UI_c=F ;;
		g) D32UI_c=G ;; h) D32UI_c=H ;; i) D32UI_c=I ;; j) D32UI_c=J ;; k) D32UI_c=K ;; l) D32UI_c=L ;;
		m) D32UI_c=M ;; n) D32UI_c=N ;; o) D32UI_c=O ;; p) D32UI_c=P ;; q) D32UI_c=Q ;; r) D32UI_c=R ;;
		s) D32UI_c=S ;; t) D32UI_c=T ;; u) D32UI_c=U ;; v) D32UI_c=V ;; w) D32UI_c=W ;; x) D32UI_c=X ;;
		y) D32UI_c=Y ;; z) D32UI_c=Z ;;
		0) D32UI_c=O ;; 1) D32UI_c=I ;; 8) D32UI_c=B ;;
		esac
		case $D32UI_c in
		[A-Z]|[2-7]) ;;
		*)	D32UI_bad=1 ;;
		esac
		D32UI_o=$D32UI_o$D32UI_c
	done
	if [ -z "$D32UI_o" ] && [ -z "$D32UI_bad" ]; then D32UI_WHY=empty; return 1; fi
	if [ -n "$D32UI_bad" ]; then D32UI_WHY=bad-symbol; D32UI_o=; return 1; fi
	if [ ${#D32UI_o} -ne 10 ]; then D32UI_WHY=length; D32UI_o=; return 1; fi
	D32UI_CODE=$D32UI_o
	D32UI_o=
	return 0
}

# d32ui_group CODE: "ABCDE FGHIJ" in D32UI_T (two groups of five, for reading and typing)
d32ui_group() {
	D32UI_T="${1%?????} ${1#?????}"
}

# d32ui_nato_load: the read-aloud words, once, into shell variables (so spelling a code runs no command)
d32ui_nato_load() {
	[ -n "${D32UI_NATO_LOADED:-}" ] && return 0
	eval "$(awk -F '\t' '$1 ~ /^nato\.[A-Z2-7]$/ && $2 ~ /^[A-Za-z-]+$/ { print "D32UI_N_" substr($1, 6) "=" $2 }' \
		"$D32UI_STRINGS" 2>/dev/null)"
	D32UI_NATO_LOADED=1
}

# d32ui_spell CODE: "Alfa Bravo Charlie Delta Echo, Foxtrot Golf Hotel India Juliett" in D32UI_T
d32ui_spell() {
	d32ui_nato_load
	D32UI_T=
	D32UI_r=$1
	D32UI_i=0
	while [ -n "$D32UI_r" ]; do
		D32UI_c=${D32UI_r%"${D32UI_r#?}"}
		D32UI_r=${D32UI_r#?}
		case $D32UI_c in [A-Z2-7]) ;; *) D32UI_T=; return 1 ;; esac
		eval "D32UI_w=\${D32UI_N_$D32UI_c:-$D32UI_c}"
		D32UI_i=$((D32UI_i + 1))
		if [ $D32UI_i -eq 1 ]; then D32UI_T=$D32UI_w
		elif [ $D32UI_i -eq 6 ]; then D32UI_T="$D32UI_T, $D32UI_w"
		else D32UI_T="$D32UI_T $D32UI_w"; fi
	done
	D32UI_w=
}

# ---- what a C6 state means for a person ---------------------------------------------------------------------------
# d32ui_words: the console companion line of the parsed C6 line (d32ui_c6 first), in D32UI_T
d32ui_words() {
	d32ui_text "step.$D32C6_stage"
	D32UI_step=$D32UI_T
	if [ "$D32C6_state" = failed ]; then
		d32ui_text "why.$D32C6_error"
		[ "$D32UI_T" = "why.$D32C6_error" ] && d32ui_text why.unknown
		D32UI_why=$D32UI_T
		d32ui_fmt say.failed step "$D32UI_step" why "$D32UI_why" error "$D32C6_error"
	elif [ -n "$D32C6_signin" ]; then
		d32ui_fmt say.signin signin "$D32C6_signin"
	elif [ -n "$D32C6_pair" ]; then
		d32ui_text "peer.${D32C6_pair_with:-workstation}"
		D32UI_peer=$D32UI_T
		d32ui_mmss "$D32C6_expires"
		D32UI_left=$D32UI_T
		d32ui_group "$D32C6_pair"
		d32ui_fmt say.pair peer "$D32UI_peer" code "$D32UI_T" left "$D32UI_left"
	elif [ "$D32C6_stage $D32C6_state" = "ready done" ]; then
		d32ui_fmt say.ready
	elif [ "$D32C6_stage" = tailnet ] && [ -n "$D32C6_name" ] && [ -n "$D32C6_msg" ]; then
		d32ui_fmt say.tailnet name "$D32C6_name" msg "$D32C6_msg"
	elif [ "$D32C6_stage" = install ] && [ -n "$D32C6_msg" ]; then
		d32ui_fmt say.install msg "$D32C6_msg"
	elif [ -n "$D32C6_msg" ]; then
		d32ui_fmt say.msg step "$D32UI_step" msg "$D32C6_msg"
	else
		d32ui_text "state.$D32C6_state"
		D32UI_st=$D32UI_T
		D32UI_dots=.
		D32UI_n=$(( 60 - 10 - ${#D32UI_step} ))
		while [ $D32UI_n -gt 1 ]; do D32UI_dots="$D32UI_dots."; D32UI_n=$((D32UI_n - 1)); done
		d32ui_fmt say.state step "$D32UI_step" dots "$D32UI_dots" state "$D32UI_st"
	fi
}

# ---- the First Start window's model ------------------------------------------------------------------------------
# d32ui_setup_model HELPER_OUTPUT TRANSPORT: from d32-setup-status's output (the C6 line, then done=yes|no) and the
# node's transport (tailnet or loopback), set:
#   D32UI_ROWS   the step rows, one per line: WORD|STEP NAME|current or empty (System V checklist order)
#   D32UI_HEAD   the first instruction line;  D32UI_TEXT1, D32UI_TEXT2  the lines under it (may be empty)
#   D32UI_ADDR   the sign-in address to show in the read-only field (empty: no field)
#   D32UI_CGRP, D32UI_CSPELL, D32UI_CLEFT   a pairing code to show, grouped and spelled, and its time left
#   D32UI_OPEN   yes when the window should open by itself at login (the first start is not finished)
#   D32UI_NET    yes while disc 2 is awaited on a node with a network link: Get Disc 2 over the Network is offered
# The address and the code are copied only from the line the helper printed, and only while their step waits.
d32ui_setup_model() {
	D32UI_ROWS= D32UI_HEAD= D32UI_TEXT1= D32UI_TEXT2= D32UI_ADDR= D32UI_CGRP= D32UI_CSPELL= D32UI_CLEFT= D32UI_OPEN=yes
	D32UI_NET=
	D32UI_line=
	D32UI_done=no
	# split the lines by parameter expansion, not a here-document: ksh and dtksh write a here-document to a temporary
	# file, and these lines may hold a sign-in address or a pairing code
	D32UI_r=$1$D32UI_NL
	while [ -n "$D32UI_r" ]; do
		D32UI_l=${D32UI_r%%"$D32UI_NL"*}
		D32UI_r=${D32UI_r#*"$D32UI_NL"}
		case $D32UI_l in
		'D32S/1 '*) D32UI_line=$D32UI_l ;;
		done=yes) D32UI_done=yes ;;
		esac
	done
	D32UI_l=
	D32UI_stages='firstboot keys signin tailnet content import ready'
	D32UI_cur=
	D32UI_cst=
	if [ -n "$D32UI_line" ] && d32ui_c6 "$D32UI_line"; then
		D32UI_cur=$D32C6_stage
		D32UI_cst=$D32C6_state
		[ "$D32UI_cur" = pairing ] && D32UI_stages='firstboot keys signin tailnet content import pairing ready'
	elif [ "$D32UI_done" = yes ]; then
		D32UI_cur=ready
		D32UI_cst=done
	fi
	if [ "$D32UI_done" = yes ] && [ "$D32UI_cur$D32UI_cst" = readydone ]; then D32UI_OPEN=no; fi
	D32UI_before=yes
	for D32UI_s in $D32UI_stages; do
		D32UI_mark=
		if [ -z "$D32UI_cur" ]; then
			D32UI_w=pending
		elif [ "$D32UI_s" = "$D32UI_cur" ]; then
			D32UI_w=$D32UI_cst
			D32UI_before=no
			D32UI_mark=current
		elif [ $D32UI_before = yes ]; then
			D32UI_w=done
		else
			D32UI_w=pending
		fi
		case $D32UI_s in signin|tailnet)
			[ "$2" = loopback ] && D32UI_w=skipped ;;
		esac
		d32ui_text "state.$D32UI_w"
		D32UI_word=$D32UI_T
		d32ui_text "step.$D32UI_s"
		D32UI_ROWS="$D32UI_ROWS$D32UI_word|$D32UI_T|$D32UI_mark$D32UI_NL"
	done
	if [ -z "$D32UI_cur" ]; then
		if [ -n "$1" ]; then d32ui_fmt setup.noline; else d32ui_fmt setup.nostatus; fi
		D32UI_HEAD=$D32UI_T
		[ -n "$1" ] || { d32ui_fmt setup.nostatus.how; D32UI_TEXT1=$D32UI_T; }
		return 0
	fi
	d32ui_text "step.$D32UI_cur"
	D32UI_step=$D32UI_T
	if [ "$D32UI_cst" = failed ]; then
		d32ui_text "why.$D32C6_error"
		[ "$D32UI_T" = "why.$D32C6_error" ] && d32ui_text why.unknown
		D32UI_why=$D32UI_T
		d32ui_fmt setup.failed step "$D32UI_step" why "$D32UI_why"; D32UI_HEAD=$D32UI_T
		d32ui_fmt setup.failed.code code "$D32C6_error"; D32UI_TEXT1=$D32UI_T
		if [ "$D32UI_cur" = import ]; then d32ui_fmt setup.import.see; D32UI_TEXT2=$D32UI_T; fi
		return 0
	fi
	case $D32UI_cur.$D32UI_cst in
	signin.waiting)
		d32ui_fmt setup.signin.head; D32UI_HEAD=$D32UI_T
		if [ -n "$D32C6_signin" ]; then
			d32ui_fmt setup.signin.open; D32UI_TEXT1=$D32UI_T
			D32UI_ADDR=$D32C6_signin
			d32ui_fmt setup.signin.after; D32UI_TEXT2=$D32UI_T
		else
			case $D32C6_msg in
			'waiting for a network cable and Tailscale') d32ui_fmt setup.signin.net ;;
			'waiting for the sign-in address') d32ui_fmt setup.signin.noaddr ;;
			'approve this machine in the Tailscale admin console') d32ui_fmt setup.signin.approve ;;
			'the network link is switched off (d32-link enable)') d32ui_fmt setup.signin.off ;;
			'signed in, waiting for the tailnet address') d32ui_fmt setup.signin.noip ;;
			'Tailscale state '*) d32ui_fmt setup.signin.state state "${D32C6_msg#Tailscale state }" ;;
			*)	D32UI_T=$D32C6_msg ;;
			esac
			D32UI_TEXT1=$D32UI_T
		fi ;;
	signin.done|tailnet.*)
		if [ -n "$D32C6_name" ]; then d32ui_fmt setup.tailnet.done name "$D32C6_name"
		else d32ui_fmt setup.signin.noip; fi
		D32UI_HEAD=$D32UI_T ;;
	content.waiting)
		case $D32C6_msg in
		'more than one disc 2'*) d32ui_fmt setup.content.many; D32UI_HEAD=$D32UI_T ;;
		*)	d32ui_fmt setup.content.attach; D32UI_HEAD=$D32UI_T
			d32ui_fmt setup.content.how; D32UI_TEXT1=$D32UI_T ;;
		esac
		# disc 2 over the network (d32-content-fetch): the action and its line, on a node with a network link
		if [ "$2" != loopback ]; then
			D32UI_NET=yes
			case $D32C6_msg in
			'the download of disc 2 stopped'*) d32ui_fmt setup.content.net.stopped ;;
			*)	d32ui_fmt setup.content.net ;;
			esac
			D32UI_TEXT2=$D32UI_T
		fi ;;
	content.running)
		# d32-content-fetch is downloading disc 2; its console line says how far
		d32ui_fmt setup.content.fetch; D32UI_HEAD=$D32UI_T
		D32UI_TEXT1=$D32C6_msg ;;
	content.*)
		d32ui_fmt setup.content.found; D32UI_HEAD=$D32UI_T ;;
	import.done)
		d32ui_fmt setup.import.done; D32UI_HEAD=$D32UI_T
		d32ui_fmt setup.import.see; D32UI_TEXT1=$D32UI_T ;;
	import.*)
		d32ui_fmt setup.import.run; D32UI_HEAD=$D32UI_T
		d32ui_fmt setup.import.see; D32UI_TEXT1=$D32UI_T
		D32UI_TEXT2=$D32C6_msg ;;
	pairing.waiting)
		d32ui_text "peer.${D32C6_pair_with:-forge}"
		D32UI_peer=$D32UI_T
		if [ -n "$D32C6_pair" ]; then
			d32ui_fmt setup.pairing.code peer "$D32UI_peer"; D32UI_HEAD=$D32UI_T
			d32ui_group "$D32C6_pair"; D32UI_CGRP=$D32UI_T
			d32ui_spell "$D32C6_pair"; d32ui_fmt setup.pairing.spell spell "$D32UI_T"; D32UI_CSPELL=$D32UI_T
			d32ui_mmss "$D32C6_expires"; d32ui_fmt setup.pairing.left left "$D32UI_T"; D32UI_CLEFT=$D32UI_T
		else
			d32ui_fmt setup.pairing.enter peer "$D32UI_peer"; D32UI_HEAD=$D32UI_T
		fi ;;
	ready.done)
		d32ui_fmt setup.ready; D32UI_HEAD=$D32UI_T
		d32ui_fmt setup.ready.more; D32UI_TEXT1=$D32UI_T ;;
	*)
		D32UI_HEAD=$D32UI_step
		D32UI_TEXT1=$D32C6_msg ;;
	esac
	if [ "$D32UI_cur" = signin ] && [ "$2" = loopback ]; then
		d32ui_fmt setup.loopback; D32UI_HEAD=$D32UI_T; D32UI_TEXT1=; D32UI_ADDR=
	fi
	return 0
}
