#!/usr/bin/env bash
# deep32-wizard.sh: the full-screen setup wizard for Deep32, for Linux and macOS terminals.
#
#   bash install/deep32-wizard.sh              set up this machine, one step at a time
#   bash install/deep32-wizard.sh --demo       the whole wizard against a recorded setup: no network, no changes
#   bash install/deep32-wizard.sh --plain      deep32-setup.sh with its normal tagged output
#   bash install/deep32-wizard.sh --vm-guide   the Workstation VM, step by step, for Proxmox VE, VMware, VirtualBox,
#                                              Hyper-V, KVM or another machine (--kit DIR names the download folder)
#   bash install/deep32-wizard.sh --send-disc2 send this machine's verified disc 2 to a Workstation over your tailnet
#
# The wizard drives install/deep32-setup.sh and shows each step on one screen. It takes the same role and options
# (check, forge, workstation, headend, --yes, --data-root, --name, --workstation, --agent, --hypervisor) and its own:
# --plain, --demo, --theme dark|light, --ascii and --help. It runs deep32-setup.sh unchanged, with its tagged lines,
# when the input or output is not a terminal, TERM is dumb, NO_COLOR is set, --plain is given or bash is older
# than 4.2, so agents and CI read the same lines as before. On Windows use install\deep32-wizard.ps1.
#
# Keys: arrows choose, Enter continues, Esc goes back, L shows the log, ? shows help, Q quits.
# The log of every run is kept in ~/.local/state/deep32-setup (or $XDG_STATE_HOME/deep32-setup).

wiz_here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
wiz_repo=$(cd -- "$wiz_here/.." && pwd -P)
wiz_self="$wiz_here/deep32-wizard.sh"
wiz_setup="$wiz_here/deep32-setup.sh"

# ---------------------------------------------------------------------------------------------------------------
# 1. Arguments and the plain fallback. This part stays bash 3.2 safe so an old bash still reaches deep32-setup.sh.

wiz_args() {  # wiz_args ARG...: the wizard's own options into opt_*, the rest into PASS for deep32-setup.sh
    PASS=(); opt_plain=0; opt_demo=0; opt_theme=''; opt_ascii=0; opt_help=0; opt_replay=''; opt_diag=''; opt_diag_arg=''
    opt_role=''; opt_yes=0; opt_data_root=''; opt_name=''; opt_ws=''; opt_agent=''; opt_hv=''; opt_bad=''; opt_vmguide=0; opt_kit=''
    opt_send=0
    while [ $# -gt 0 ]; do
        case $1 in
            --plain) opt_plain=1 ;;
            --demo) opt_demo=1 ;;
            --vm-guide) opt_vmguide=1 ;;
            --kit) opt_kit=${2:-}; [ $# -gt 1 ] && shift ;;
            --send-disc2) opt_send=1 ;;
            --ascii) opt_ascii=1 ;;
            --theme) opt_theme=${2:-}; [ $# -gt 1 ] && shift ;;
            --help|-h) opt_help=1 ;;
            --demo-replay) opt_replay=${2:-}; [ $# -gt 1 ] && shift ;;
            --print-mode) opt_diag=mode ;;
            --decode) opt_diag=decode; opt_diag_arg=${2:-}; [ $# -gt 1 ] && shift ;;
            check|forge|workstation|headend) opt_role=$1; PASS[${#PASS[@]}]=$1 ;;
            --yes|-y) opt_yes=1; PASS[${#PASS[@]}]=$1 ;;
            --data-root|--name|--workstation|--agent|--hypervisor|--plan-digest|--settings)
                PASS[${#PASS[@]}]=$1; PASS[${#PASS[@]}]=${2:-}
                case $1 in
                    --data-root) opt_data_root=${2:-} ;; --name) opt_name=${2:-} ;; --workstation) opt_ws=${2:-} ;;
                    --agent) opt_agent=${2:-} ;; --hypervisor) opt_hv=${2:-} ;;
                esac
                [ $# -gt 1 ] && shift ;;
            *) PASS[${#PASS[@]}]=$1; [ -n "$opt_bad" ] || opt_bad=$1 ;;
        esac
        shift
    done
}

wiz_mode() {  # print plain, old or tui: the full screen only on a real terminal that can show it
    if [ "$opt_plain" = 1 ] || [ -n "$opt_bad" ]; then echo plain; return; fi
    if [ -n "${NO_COLOR:-}" ]; then echo plain; return; fi
    case ${TERM:-} in ''|dumb) echo plain; return ;; esac
    if [ "${DEEP32_WIZARD_FORCE_TTY:-}" != 1 ] && { ! [ -t 0 ] || ! [ -t 1 ]; }; then echo plain; return; fi
    if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]:-0}" -lt 2 ]; }; then echo old; return; fi
    echo tui
}

wiz_help() {
    sed -n '2,18p' "$wiz_self" | sed 's/^# \{0,1\}//'
}

if [ -z "${DEEP32_WIZARD_LIB:-}" ]; then
    wiz_args "$@"
    if [ -z "$opt_replay" ]; then
        if [ "$opt_help" = 1 ]; then wiz_help; exit 0; fi
        wiz_mode_now=$(wiz_mode)
        if [ "$opt_diag" = mode ]; then echo "$wiz_mode_now"; exit 0; fi
        [ "$opt_send" = 1 ] && [ -n "$opt_bad" ] && { echo "[fail] unknown argument: $opt_bad (try --help)"; exit 64; }
        [ "$opt_send" = 1 ] && [ "$wiz_mode_now" = old ] && { echo 'deep32-wizard: Send disc 2 needs bash 4.2 or newer (on a Mac, from Homebrew)' >&2; exit 2; }
        if [ "$wiz_mode_now" != tui ] && [ "$opt_vmguide" = 1 ] && [ "$opt_send" != 1 ]; then
            if [ "$wiz_mode_now" = old ]; then echo "deep32-wizard: the VM guide needs bash 4.2 or newer (on a Mac, from Homebrew)" >&2; exit 1; fi
            wiz_vm_plain=1
        elif [ "$wiz_mode_now" != tui ] && [ "$opt_diag" != decode ] && [ "$opt_send" != 1 ]; then
            [ "$wiz_mode_now" = old ] &&
                echo "deep32-wizard: the full-screen wizard needs bash 4.2 or newer (on a Mac, from Homebrew); running deep32-setup.sh" >&2
            if [ "$opt_demo" = 1 ]; then
                exec "${BASH:-bash}" "$wiz_self" --demo-replay forge --plan-digest \
                    3f2a9c417be021d5c0de86b19a4471f2e53b0c9d8a6e1f37b24c95d0e8a1f6c3
            fi
            exec "${BASH:-bash}" "$wiz_setup" "${PASS[@]}"
        fi
    fi
fi

declare -A EV=() RS=() PART=() HT=() HFL=() HFX=() VF=() FACT=() FSHOW=() DEMO_N=() VB_CAP=() 2>/dev/null

# ---------------------------------------------------------------------------------------------------------------
# 2. Small helpers shared by the screen, the tests and the replay. Everything below needs bash 4.

wiz_now() {  # NOW_MS: milliseconds since the epoch (whole seconds on bash 4 without EPOCHREALTIME)
    if [ -n "${EPOCHREALTIME:-}" ]; then
        local t=${EPOCHREALTIME//[.,]/}
        NOW_MS=$((10#$t / 1000))
    else
        NOW_MS=$(( $(date +%s) * 1000 ))
    fi
}

fmt_bytes() {  # fmt_bytes N: decimal units as INSTALL.md writes them (1.9 GB, 640 MB), into FMT
    local n=${1:-0} v
    if ((n >= 1000000000)); then v=$(( (n + 50000000) / 100000000 )); FMT="$((v / 10)).$((v % 10)) GB"
    elif ((n >= 1000000)); then v=$(( (n + 500000) / 1000000 )); FMT="$v MB"
    elif ((n >= 1000)); then v=$(( (n + 500) / 1000 )); FMT="$v kB"
    else FMT="$n bytes"; fi
}

fmt_eta() {  # fmt_eta SECONDS: a plain time left, into FMT
    local s=${1:-0} m h
    if ((s < 60)); then FMT='less than a minute left'
    elif ((s < 3600)); then m=$(( (s + 59) / 60 )); FMT="about $m min left"
    else h=$((s / 3600)); m=$(( (s % 3600 + 59) / 60 )); ((m == 60)) && { h=$((h + 1)); m=0; }
        if ((m == 0)); then FMT="about $h h left"; else FMT="about $h h $m min left"; fi
    fi
}

fmt_clock() {  # fmt_clock SECONDS: m:ss, into FMT
    local s=${1:-0}; ((s < 0)) && s=0
    printf -v FMT '%d:%02d' $((s / 60)) $((s % 60))
}

bar_cells() {  # bar_cells PERMILLE WIDTH: BAR_FULL whole cells and BAR_HALF (0 or 1) for a bar WIDTH cells wide
    local p=${1:-0} w=${2:-0} halves
    ((p < 0)) && p=0; ((p > 1000)) && p=1000
    halves=$(( (p * w * 2 + 500) / 1000 ))
    BAR_FULL=$((halves / 2)); BAR_HALF=$((halves % 2))
}

win_rate() {  # win_rate "T:B T:B ...": bytes per second over the last 30 s of samples, into RATE (-1 until 3 s of them) and SPAN (ms)
    local s first='' last='' t0 b0 t1 b1
    for s in $1; do last=$s; done
    if [ -z "$last" ]; then RATE=-1; SPAN=0; return; fi
    t1=${last%%:*}; b1=${last#*:}
    for s in $1; do ((${s%%:*} >= t1 - 30000)) && { first=$s; break; }; done
    t0=${first%%:*}; b0=${first#*:}; SPAN=$((t1 - t0))
    if ((SPAN < 3000)); then RATE=-1; else RATE=$(( (b1 - b0) * 1000 / SPAN )); ((RATE < 0)) && RATE=0; fi
}

eta_of() {  # eta_of REMAIN RATE SPAN: seconds left, into ETA; -1 while it means nothing (unknown total, no speed, under 10 s)
    if (($1 < 0 || $2 <= 0 || $3 < 10000)); then ETA=-1; else ETA=$(( ($1 + $2 - 1) / $2 )); fi
}

strip_cells() {  # strip_cells PERMILLE: how many of the 32 strip cells are lit, into STRIP_LIT
    local p=${1:-0}; ((p < 0)) && p=0; ((p > 1000)) && p=1000
    STRIP_LIT=$(( p * 32 / 1000 ))
}

permille() {  # permille PART WHOLE: PART of WHOLE in thousandths, into PM
    local a=${1:-0} b=${2:-0}
    if ((b <= 0)); then PM=0; elif ((a >= b)); then PM=1000; else PM=$(( a * 1000 / b )); fi
}

json_get() {  # json_get LINE: the flat JSON object LINE into the associative array EV (strings unescaped)
    local rest=$1 key val re='^,?"([^"]+)":("(([^"\\]|\\.)*)"|(true|false|null|-?[0-9]+))'
    EV=()
    [[ $rest == '{'* ]] || return 1
    rest=${rest#\{}
    while [[ $rest =~ $re ]]; do
        key=${BASH_REMATCH[1]}
        if [ -n "${BASH_REMATCH[5]}" ]; then val=${BASH_REMATCH[5]}
        else
            val=${BASH_REMATCH[3]}
            val=${val//\\\\/$'\001'}; val=${val//\\\"/\"}; val=${val//\\n/$'\n'}; val=${val//\\t/$'\t'}
            val=${val//\\r/$'\r'}; val=${val//\\\//\/}; val=${val//$'\001'/\\}
        fi
        EV[$key]=$val
        rest=${rest:${#BASH_REMATCH[0]}}
    done
    [[ $rest == '}'* ]]
}

# ---------------------------------------------------------------------------------------------------------------
# 3. The error and guidance table (install/deep32-wizard-errors.tsv, shared with deep32-wizard.ps1).

err_load() {  # load the rows for this OS: ERR_KIND ERR_KEY ERR_SRC ERR_MEAN ERR_FIX
    ERR_KIND=(); ERR_KEY=(); ERR_SRC=(); ERR_MEAN=(); ERR_FIX=()
    local file="$wiz_here/deep32-wizard-errors.tsv" os=linux kind key ros src mean fix line
    case $(uname -s 2>/dev/null) in Darwin) os=macos ;; MINGW*|MSYS*|CYGWIN*) os=windows ;; esac
    [ -n "${DEEP32_WIZARD_OS:-}" ] && os=$DEEP32_WIZARD_OS
    [ -r "$file" ] || return 0
    local -A mine=()
    while IFS=$'\t' read -r kind key ros src mean fix; do
        [ "$ros" = "$os" ] && mine["$kind $key"]=1
    done < <(grep -v '^#' "$file" | grep -v '^[[:space:]]*$')
    while IFS=$'\t' read -r kind key ros src mean fix; do
        case $ros in "$os") ;; any) [ -n "${mine["$kind $key"]:-}" ] && continue ;; *) continue ;; esac
        ERR_KIND+=("$kind"); ERR_KEY+=("$key"); ERR_SRC+=("$src"); ERR_MEAN+=("$mean"); ERR_FIX+=("${fix%$'\r'}")
    done < <(grep -v '^#' "$file" | grep -v '^[[:space:]]*$')
}

err_code_re() {  # err_code_re CODE: the whole-token pattern for CODE, into RE
    local k=${1//./\\.}
    RE="(^|[^A-Za-z0-9_.-])$k([^A-Za-z0-9_.-]|\\.([^A-Za-z0-9]|\$)|\$)"
}

err_decode() {  # err_decode STATUSES LINE...: matched rows, best first, into DEC_ROWS and DEC_LINES
    # STATUSES is "tool:status tool:status ..." (the setup's own status last). Rows are ranked: a reason code, then
    # a message from a deeper tool, then a deep32-setup message, then an exit status, then the catch-all.
    local statuses=$1; shift
    local -a lines=("$@") r1=() r2=() r3=() r4=() l1=() l2=() l3=() l4=()
    local i j n=${#lines[@]} catch='' seen=' ' ok_re='^(\[(ok|info|you)\] |Verified |Completed from mirror |Optional manual torrent: |Reusing verified |Write the verified |Attach the verified |Selected GPU capability|VM artefacts written: |Prerequisites passed |Split Disc 3 installation includes |Checking read-only prerequisites|Mounting |Running the disc3 Docker form|Disc3 loader returned|Next: review GPU pack)'
    for ((i = n - 1; i >= 0; i--)); do
        local line=${lines[i]}
        [[ $line == '{"deep32":'* ]] && continue
        [[ $line =~ $ok_re ]] && continue   # progress and success lines never explain a failure
        for ((j = 0; j < ${#ERR_KIND[@]}; j++)); do
            case ${ERR_KIND[j]} in
                code)
                    # A code that is a plain word (expired, timeout) counts only when Connect itself stopped.
                    [[ ${ERR_KEY[j]} =~ ^[a-z]+$ ]] && [[ $statuses != *connect:* ]] && continue
                    err_code_re "${ERR_KEY[j]}"
                    [[ $line =~ $RE ]] && [[ $seen != *" $j "* ]] && { r1+=("$j"); l1+=("$line"); seen+="$j "; } ;;
                text)
                    if [ "${ERR_KEY[j]}" = . ]; then catch=$j; continue; fi
                    [[ $seen == *" $j "* ]] && continue
                    if [[ $line =~ ${ERR_KEY[j]} ]]; then
                        if [ "${ERR_SRC[j]}" = setup ]; then r3+=("$j"); l3+=("$line"); else r2+=("$j"); l2+=("$line"); fi
                        seen+="$j "
                    fi ;;
            esac
        done
    done
    local st
    for st in $statuses; do
        [[ $st == *:0 ]] && continue
        for ((j = 0; j < ${#ERR_KIND[@]}; j++)); do
            [ "${ERR_KIND[j]}" = exit ] && [ "${ERR_KEY[j]}" = "$st" ] && [[ $seen != *" $j "* ]] &&
                { r4+=("$j"); l4+=("${st%%:*} exit ${st##*:}"); seen+="$j "; }
        done
    done
    DEC_ROWS=("${r1[@]}" "${r2[@]}" "${r3[@]}" "${r4[@]}"); DEC_LINES=("${l1[@]}" "${l2[@]}" "${l3[@]}" "${l4[@]}")
    if [ -n "$catch" ]; then DEC_ROWS+=("$catch"); DEC_LINES+=(''); fi
}

err_steps() {  # err_steps ROW: the fix steps of a row into STEPS (commands in backticks stay marked)
    STEPS=()
    local fix=${ERR_FIX[$1]} part
    while [ -n "$fix" ]; do
        part=${fix%%' | '*}
        STEPS+=("$part")
        [[ $fix == *' | '* ]] && fix=${fix#*' | '} || fix=''
    done
}

err_guidance() {  # err_guidance TEXT: the first setup text row matching a [fix] line, into GUIDE (row) or -1
    local j
    GUIDE=-1
    for ((j = 0; j < ${#ERR_KIND[@]}; j++)); do
        [ "${ERR_KIND[j]}" = text ] && [ "${ERR_KEY[j]}" != . ] || continue
        [[ $1 =~ ${ERR_KEY[j]} ]] && { GUIDE=$j; return; }
    done
}

# ---------------------------------------------------------------------------------------------------------------
# 4. The demo replay: plays install/deep32-wizard-demo.txt in place of deep32-setup.sh.

wiz_replay() {  # wiz_replay SCENARIO: print one recorded run; --events and --plan-digest come in PASS
    local name=$1 events=0 digest=0 a attempt=${DEEP32_WIZARD_DEMO_ATTEMPT:-1} os=unix answer='' line
    for a in "${PASS[@]}"; do case $a in --events) events=1 ;; --plan-digest) digest=1 ;; esac; done
    case ${DEEP32_WIZARD_OS:-} in windows) os=windows ;; esac
    local dir=${DEEP32_WIZARD_DEMO_DIR:-'~/deep32-downloads'} root=${DEEP32_WIZARD_DEMO_ROOT:-/opt/deep32}
    local py=${DEEP32_WIZARD_DEMO_PY:-python3}
    local -a skip=(0)
    local inside=0 cond kw rest
    exec 3<&0
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%$'\r'}
        if [[ $line == '=== '* ]]; then
            [ "$inside" = 1 ] && break
            [ "${line#=== }" = "$name" ] && inside=1
            continue
        fi
        [ "$inside" = 1 ] || continue
        [[ $line == '#'* ]] && continue
        if [[ $line == .if\ * ]]; then
            cond=${line#.if }
            local on=0
            case $cond in
                yes) [[ $answer == [Yy]* ]] && on=1 ;; no) [[ $answer == [Yy]* ]] || on=1 ;;
                first) ((attempt == 1)) && on=1 ;; retry) ((attempt > 1)) && on=1 ;;
                digest) ((digest)) && on=1 ;; nodigest) ((digest)) || on=1 ;;
                windows) [ "$os" = windows ] && on=1 ;; unix) [ "$os" = unix ] && on=1 ;;
                fixed) [ "${DEEP32_WIZARD_DEMO_FIXED:-}" = 1 ] && on=1 ;;
            esac
            if [ "${skip[${#skip[@]}-1]}" = 1 ] || [ "$on" = 0 ]; then skip+=(1); else skip+=(0); fi
            continue
        fi
        if [ "$line" = .endif ]; then ((${#skip[@]} > 1)) && unset "skip[$((${#skip[@]} - 1))]"; continue; fi
        [ "${skip[${#skip[@]}-1]}" = 1 ] && continue
        line=${line//'{dir}'/$dir}; line=${line//'{root}'/$root}; line=${line//'{py}'/$py}; line=${line//'{tab}'/$'\t'}
        case $line in
            .sleep\ *) sleep "${line#.sleep }" ;;
            .json\ *) ((events)) && printf '%s\n' "${line#.json }" ;;
            .exit\ *) return "${line#.exit }" ;;
            .ask\ *)
                rest=${line#.ask }; kw=${rest%% *}; rest=${rest#* }
                if ((events)); then
                    printf '{"deep32":"ask","id":"%s","text":"%s"}\n' "$kw" "$rest"
                    IFS= read -r -u 3 answer || answer=''
                else
                    printf '%s [y/N] ' "$rest"; IFS= read -r -u 3 answer || answer=''
                fi ;;
            .prompt\ *)
                printf '%s' "${line#.prompt }" >&2
                if [ -t 3 ]; then IFS= read -rs -u 3 answer; else IFS= read -r -u 3 answer || answer=''; fi
                printf '\n' >&2 ;;
            .dl\ *)
                local f bytes secs from to steps k b
                read -r f bytes secs from to <<<"${line#.dl }"
                from=${from:-0}; to=${to:-1000}
                steps=$(( ${secs%.*} * 4 )); ((steps < 2)) && steps=2
                for ((k = 0; k <= steps; k++)); do
                    b=$(( bytes / 1000 * (from + (to - from) * k / steps) ))
                    ((events)) && printf '{"deep32":"demo-progress","file":"%s","bytes":%d,"size":%d}\n' "$f" "$b" "$bytes"
                    ((k < steps)) && sleep 0.25
                done ;;
            *) printf '%s\n' "$line" ;;
        esac
    done < "$wiz_here/deep32-wizard-demo.txt"
    return 0
}

if [ -z "${DEEP32_WIZARD_LIB:-}" ] && [ -n "${opt_replay:-}" ]; then
    wiz_replay "$opt_replay"
    exit $?
fi

# ---------------------------------------------------------------------------------------------------------------
# 5. Terminal: locale, glyphs, colours.

wiz_locale() {  # pick a UTF-8 locale for the wizard itself (children get the original back); WIZ_UTF8=1 if found
    WIZ_LC_SET=${LC_ALL+1}; WIZ_LC_OLD=${LC_ALL-}
    local l probe
    for l in "${LC_ALL:-}" "${LC_CTYPE:-}" "${LANG:-}" C.UTF-8 C.utf8 en_US.UTF-8 en_US.utf8; do
        [ -n "$l" ] || continue
        case $l in *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*) ;; *) continue ;; esac
        { LC_ALL=$l; } 2>/dev/null
        probe='╭─✓'
        if [ "${#probe}" = 3 ]; then WIZ_UTF8=1; return; fi
    done
    WIZ_UTF8=0
    if [ "$WIZ_LC_SET" = 1 ]; then { LC_ALL=$WIZ_LC_OLD; } 2>/dev/null; else unset LC_ALL; fi
}

wiz_glyphs() {  # GLYPHS: rich (rounded, fine bars), safe (old console fonts) or ascii
    GLYPHS=rich
    [ "$WIZ_UTF8" = 1 ] || GLYPHS=ascii
    # The person's own locale says what the terminal shows; Git for Windows' mintty and the known terminals are UTF-8.
    case "${WIZ_LC_OLD:-}${LC_CTYPE:-}${LANG:-}" in *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*) ;; *)
        [ -n "${MSYSTEM:-}${WT_SESSION:-}${TERM_PROGRAM:-}" ] || GLYPHS=ascii ;; esac
    [ "${TERM:-}" = linux ] && [ "$GLYPHS" = rich ] && GLYPHS=safe
    [ "$opt_ascii" = 1 ] && GLYPHS=ascii
    [ -n "${DEEP32_WIZARD_GLYPHS:-}" ] && GLYPHS=$DEEP32_WIZARD_GLYPHS
    case $GLYPHS in
        rich)
            G_TL='╭' G_TR='╮' G_BL='╰' G_BR='╯' G_H='─' G_V='│' G_TT='┬' G_BT='┴'
            G_OK='✓' G_FIX='!' G_FAIL='×' G_CUR='●' G_PEND='○' G_SKIP='–' G_INFO='·' G_YOU='→' G_DOT='·'
            G_MARK='▍' G_BAR='━' G_BARH='╸' G_TRACK='━' G_WIDE='▄▄▄' G_MED='▄' G_NARROW='▖' G_ELL='…' G_QUOTE='┃'
            G_UP='↑' G_DOWN='↓' G_CURSOR='▏'
            SPIN=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏') ;;
        safe)
            G_TL='┌' G_TR='┐' G_BL='└' G_BR='┘' G_H='─' G_V='│' G_TT='┬' G_BT='┴'
            G_OK='√' G_FIX='!' G_FAIL='×' G_CUR='●' G_PEND='○' G_SKIP='-' G_INFO='·' G_YOU='→' G_DOT='·'
            G_MARK='▌' G_BAR='█' G_BARH='▌' G_TRACK='░' G_WIDE='███' G_MED='█' G_NARROW='▌' G_ELL='…' G_QUOTE='│'
            G_UP='↑' G_DOWN='↓' G_CURSOR='_'
            SPIN=('|' '/' '-' '\') ;;
        *)
            G_TL='+' G_TR='+' G_BL='+' G_BR='+' G_H='-' G_V='|' G_TT='+' G_BT='+'
            G_OK='+' G_FIX='!' G_FAIL='x' G_CUR='*' G_PEND='o' G_SKIP='-' G_INFO='.' G_YOU='>' G_DOT='.'
            G_MARK='>' G_BAR='#' G_BARH='#' G_TRACK='.' G_WIDE='===' G_MED='=' G_NARROW='-' G_ELL='~' G_QUOTE='|'
            G_UP='^' G_DOWN='v' G_CURSOR='_'
            SPIN=('|' '/' '-' '\') ;;
    esac
}

wiz_depth() {  # DEPTH: truecolor, 256 or 16
    case ${DEEP32_WIZARD_COLOR:-} in truecolor|256|16) DEPTH=$DEEP32_WIZARD_COLOR; return ;; esac
    case ${COLORTERM:-} in truecolor|24bit) DEPTH=truecolor; return ;; esac
    [ -n "${WT_SESSION:-}" ] && { DEPTH=truecolor; return; }
    case ${TERM_PROGRAM:-} in iTerm.app|WezTerm|vscode|mintty|ghostty|Hyper|Tabby) DEPTH=truecolor; return ;; esac
    case ${TERM:-} in *-direct|xterm-kitty|alacritty|foot*|wezterm) DEPTH=truecolor ;; *256color*) DEPTH=256 ;; *) DEPTH=16 ;; esac
}

wiz_theme() {  # THEME: dark or light, from --theme, DEEP32_WIZARD_THEME, COLORFGBG or the terminal's own answer
    THEME=${opt_theme:-${DEEP32_WIZARD_THEME:-}}
    case $THEME in dark|light) return ;; esac
    THEME=dark
    [ "${TERM:-}" = linux ] && return
    case ${COLORFGBG:-} in *';7'|*';15'|*';default') [[ ${COLORFGBG} == *';default' ]] || THEME=light; return ;; *';'?*) return ;; esac
    [ -t 0 ] && [ -t 1 ] || return
    local reply='' r g b
    printf '\033]11;?\033\\' >/dev/tty
    IFS= read -rs -t 0.2 -d $'\\' reply </dev/tty
    if [[ $reply =~ rgb:([0-9a-fA-F]{2})[0-9a-fA-F]*/([0-9a-fA-F]{2})[0-9a-fA-F]*/([0-9a-fA-F]{2}) ]]; then
        r=$((16#${BASH_REMATCH[1]})); g=$((16#${BASH_REMATCH[2]})); b=$((16#${BASH_REMATCH[3]}))
        (( (r * 299 + g * 587 + b * 114) / 1000 > 140 )) && THEME=light
    fi
}

wiz_c256() {  # wiz_c256 R G B: the nearest xterm 256-colour index, into C256
    local r=$1 g=$2 b=$3 ri gi bi d1 d2 avg k gv
    local -a lv=(0 95 135 175 215 255)
    ri=$(( r < 48 ? 0 : (r < 115 ? 1 : (r - 35) / 40) ))
    gi=$(( g < 48 ? 0 : (g < 115 ? 1 : (g - 35) / 40) ))
    bi=$(( b < 48 ? 0 : (b < 115 ? 1 : (b - 35) / 40) ))
    d1=$(( (r - lv[ri]) ** 2 + (g - lv[gi]) ** 2 + (b - lv[bi]) ** 2 ))
    avg=$(( (r + g + b) / 3 )); k=$(( avg > 238 ? 23 : (avg < 8 ? 0 : (avg - 8) / 10) )); gv=$(( 8 + 10 * k ))
    d2=$(( (r - gv) ** 2 + (g - gv) ** 2 + (b - gv) ** 2 ))
    if ((d2 < d1)); then C256=$((232 + k)); else C256=$((16 + 36 * ri + 6 * gi + bi)); fi
}

sgr() {  # sgr fg|bg RRGGBB BASIC: the escape sequence for one colour at DEPTH, into SGR
    local hex=$2 r g b
    r=$((16#${hex:0:2})); g=$((16#${hex:2:2})); b=$((16#${hex:4:2}))
    case $DEPTH in
        truecolor) if [ "$1" = fg ]; then SGR=$'\e'"[38;2;$r;$g;${b}m"; else SGR=$'\e'"[48;2;$r;$g;${b}m"; fi ;;
        256) wiz_c256 "$r" "$g" "$b"; if [ "$1" = fg ]; then SGR=$'\e'"[38;5;${C256}m"; else SGR=$'\e'"[48;5;${C256}m"; fi ;;
        *) if [ "$1" = fg ]; then SGR=$'\e'"[${3}m"; else SGR=$'\e'"[$(( $3 + 10 ))m"; fi ;;
    esac
}

wiz_palette() {  # the S_* styles: a calm palette for dark or light backgrounds; text uses the terminal's own colour
    local d=0; [ "$THEME" = dark ] && d=1
    S_0=$'\e[0m'; S_B=$'\e[1m'; S_U=$'\e[4m'
    pal() { local name=$1 kind=$2; shift 2; if ((d)); then sgr "$kind" "$1" "$2"; else sgr "$kind" "$3" "$4"; fi; printf -v "$name" '%s' "$SGR"; }
    pal S_MUTED fg 8b949e 90 59636e 90
    pal S_FAINT fg 6e7681 90 818b98 90
    pal S_BORDER fg 30363d 90 d0d7de 37
    pal S_ACCENT fg 4493f8 94 0969da 34
    pal S_ACCENTT fg 58a6ff 94 0b5cad 34
    pal S_OK fg 3fb950 92 1a7f37 32
    pal S_WARN fg d29922 93 9a6700 33
    pal S_ERR fg f85149 91 d1242f 31
    pal S_TRACK fg 262c36 90 d8dee4 37
    pal S_STRIP1 fg 4493f8 94 0969da 34
    pal S_STRIP2 fg 2e5f9e 34 6ba5e9 94
    pal S_STRIP3 fg 203d63 34 abccf2 36
    pal S_SELBG bg 152439 40 ddf4ff 47
    pal S_CHIPBG bg 161b22 40 f6f8fa 47
    pal S_WARNBG bg 2b2111 40 fff8c5 47
    pal S_ERRBG bg 2d1517 40 ffebe9 47
    pal S_BTNBG bg 1f6feb 44 0969da 44
    pal S_BTNFG fg ffffff 97 ffffff 97
    pal S_IDLEBG bg 21262d 100 eaeef2 47
    pal S_IDLEFG fg c9d1d9 97 1f2328 30
    unset -f pal
    if [ "$DEPTH" = 16 ]; then S_SELBG=''; S_CHIPBG=''; S_WARNBG=''; S_ERRBG=''; fi
    S_TITLE=$S_B; S_WORD=$S_B; S_WORD32=$S_B$S_ACCENT
}

# ---------------------------------------------------------------------------------------------------------------
# 6. State.

STEP_IDS=(check role download install pair done)
STEP_NAMES=('Check this machine' 'Choose role' 'Download and verify' 'Install' 'Pair' 'Done')
ROLE_IDS=(forge workstation headend)
ROLE_NAMES=('Forge' 'Workstation host' 'Agent PC')
PART_KEYS=(ws forge agent)
PART_NAMES=('Workstation' 'Forge' 'Agent PC')

wiz_state_init() {
    STEP_STATE=(current pending pending pending pending pending)   # pending current done failed skipped later
    STEP_NOTE=('' '' '' '' '' '')
    STAGE=0; VIEW=intro; OVERLAY=''; FOCUS=0; SEL=0; SCROLL=0; LOGSCROLL=0
    ROLE=${opt_role:-}; [ "$ROLE" = check ] && ROLE=''
    GUESS=''; GUESS_GPU=''; GUESS_OS=''; CHECKED_ROLE=''; PROBLEMS=0; FIXABLE=()
    IT_TAG=(); IT_TEXT=(); RAW=(); LOG=(); RECENT=()
    DL_NAME=(); DL_SIZE=(); DL_HAVE=(); DL_STATE=(); DL_RATE=(); DL_T=(); DL_B=(); DL_DIR=''; DL_DISC=''; DL_CLASS=''
    DL_MIRRORS=''; DL_MIRRORS_AT=0; DL_POLL_AT=0
    PLAN=(); PLAN_DIGEST=''; PLAN_IN=0; DIGEST=''; TYPED=''; CONSENT=0
    PAIR_CODE=''; PAIR_PEER=''; PAIR_UNTIL=0; PAIRED=0
    SIGNIN_URL=''; ASK_ID=''; ASK_TEXT=''; ASK_PREV=''
    CHILD_PID=''; CHILD_OUT=''; CHILD_IN=''; RUN_KIND=''; PARTIAL=''; STATUSES=''; RUN_STATUS=0; RUN_LINES=(); PHASE=0
    FAIL_TITLE=''; FAIL_ROWS=(); FAIL_LINES=(); FAIL_STAGE=0
    VM_LINE=''; ACTIVITY=''; STATUS_MSG=''; ATTEMPT=(0 0 0); SOFT_DONE=''
    FORM_KEYS=(); FORM_VALS=(); FORM_LABEL=(); FORM_HELP=(); FORM_EDIT=-1
    KEYQ=(); SNAP=''; UNTIL=''; UNTIL_AT=0; DIRTY=1; QUIT=0; INTERRUPTED=0
    PERM=0; PULSE=0; TICK=0
    XS=(); XF_LAST=0; XF_START=0; PHASE_AT=0; SKIP_PARTS=0; NEXT=(); NEXT_FILE=''; GUESS_HV=''; GUESS_AGENT=''
    PART=(); PART_SEL=0; RECHECK=0; VM_DONE=0; VM_SKIPPED=(); VM_HV=''; FORM_TITLE=''
}

role_index() { local i; for i in 0 1 2; do [ "${ROLE_IDS[i]}" = "$1" ] && { RI=$i; return 0; }; done; RI=0; return 1; }

stage_set() {  # stage_set N: make step N current; earlier steps done unless skipped or later
    local n=$1 i
    for ((i = 0; i < 6; i++)); do
        if ((i < n)); then case ${STEP_STATE[i]} in skipped|later) ;; *) STEP_STATE[i]=done ;; esac
        elif ((i == n)); then STEP_STATE[i]=current
        else case ${STEP_STATE[i]} in skipped|later) ;; *) STEP_STATE[i]=pending ;; esac; fi
    done
    STAGE=$n; DIRTY=1
}

progress_permille() {  # PERM: the whole install in thousandths, for the 32-cell strip
    local base=(0 120 190 690 850 970) span=(110 60 500 160 120 30) i=$STAGE part=0 tot=0 have=0 k
    case ${STEP_IDS[i]} in
        download) for k in "${!DL_NAME[@]}"; do tot=$((tot + DL_SIZE[k])); have=$((have + DL_HAVE[k])); done
            permille "$have" "$tot"; part=$PM ;;
        check) [ "$VIEW" = checks ] && part=1000 || part=400 ;;
        install) [ "$CONSENT" = 1 ] && part=500 ;;
        pair) [ -n "$PAIR_CODE" ] && part=600 ;;
        done) part=1000 ;;
    esac
    PERM=$(( base[i] + span[i] * part / 1000 ))
    [ "${STEP_IDS[i]}" = done ] && PERM=1000
}

# ---------------------------------------------------------------------------------------------------------------
# 7. Running deep32-setup (or the demo replay) and reading what it says.

wiz_log() {  # wiz_log LINE: keep a line for the log view and the log file
    LOG+=("$1")
    ((${#LOG[@]} > 6000)) && LOG=("${LOG[@]:1000}")
    [ -n "$LOG_FILE" ] && printf '%s\n' "$1" >>"$LOG_FILE"
}

run_start() {  # run_start KIND ARG...: start deep32-setup.sh --events ARG... (or the replay) as the coprocess
    local kind=$1; shift
    local -a cmd
    if ((opt_demo)); then
        local sc
        case $kind in guess) sc=guess ;; look|fix) sc="check-$CHECK_FOR" ;; *) sc=$ROLE ;; esac
        cmd=("${BASH:-bash}" "$wiz_self" --demo-replay "$sc" --events "$@")
    else
        cmd=("${BASH:-bash}" "$wiz_setup" --events "$@")
    fi
    RUN_KIND=$kind; RUN_STATUS=0; STATUSES=''; PARTIAL=''; RUN_LINES=(); LAST_STEP=
    wiz_log "--- $(date '+%Y-%m-%d %H:%M:%S') deep32-setup ${*}"
    local attempt=${ATTEMPT_NOW:-1}
    coproc WIZCHILD {
        if [ "$WIZ_LC_SET" = 1 ]; then LC_ALL=$WIZ_LC_OLD; else unset LC_ALL; fi
        [ -n "$SUDO_WRAP" ] && export PATH="$SUDO_WRAP:$PATH"
        export DEEP32_WIZARD_DEMO_ATTEMPT=$attempt DEEP32_WIZARD_DEMO_FIXED=${DEMO_FIXED:-0}
        cd -- "$WORKDIR" && exec "${cmd[@]}" 2>&1
    }
    CHILD_PID=$WIZCHILD_PID
    exec {CHILD_OUT}<&"${WIZCHILD[0]}" {CHILD_IN}>&"${WIZCHILD[1]}"
    DIRTY=1
}

run_answer() {  # run_answer y|n: answer the running setup's question
    [ -n "$CHILD_IN" ] && printf '%s\n' "$1" >&"$CHILD_IN" 2>/dev/null
    wiz_log "> answered $1"
}

run_drain() {  # read what the child printed so far, without waiting
    [ -n "$CHILD_OUT" ] || return 0
    local line n=0 rc
    while ((n < 400)); do
        read -t 0 -u "$CHILD_OUT" 2>/dev/null || break
        IFS= read -r -t 0.05 -u "$CHILD_OUT" line; rc=$?
        if ((rc == 0)); then on_line "$PARTIAL$line"; PARTIAL=''; n=$((n + 1)); continue; fi
        if ((rc > 128)); then PARTIAL+=$line; break; fi
        [ -n "$PARTIAL$line" ] && on_line "$PARTIAL$line"; PARTIAL=''
        run_end; break
    done
}

run_end() {  # the child closed its output: collect its status and move on
    exec {CHILD_OUT}<&- {CHILD_IN}>&- 2>/dev/null
    CHILD_OUT=''; CHILD_IN=''
    wait "$CHILD_PID" 2>/dev/null; RUN_STATUS=$?
    CHILD_PID=''
    wiz_log "--- deep32-setup exit $RUN_STATUS"
    STATUSES+=" setup:$RUN_STATUS"
    local kind=$RUN_KIND; RUN_KIND=''
    [ "$kind" = role ] && [ -n "$REAL_SUDO" ] && [ "$(id -u)" != 0 ] && ((opt_demo == 0)) && "$REAL_SUDO" -k 2>/dev/null
    on_run_end "$kind" "$RUN_STATUS"
    DIRTY=1
}

run_stop() {  # stop a running child and everything it started, deepest first
    [ -n "$CHILD_PID" ] || return 0
    local -a pids=(); local p i
    tree_pids "$CHILD_PID"; pids=("${TREE[@]}" "$CHILD_PID")
    for p in "${pids[@]}"; do kill -INT "$p" 2>/dev/null; done
    for ((i = 0; i < 30; i++)); do alive_any "${pids[@]}" || return 0; sleep 0.1; done
    for p in "${pids[@]}"; do kill -TERM "$p" 2>/dev/null; done
    for ((i = 0; i < 20; i++)); do alive_any "${pids[@]}" || return 0; sleep 0.1; done
    [ -n "$REAL_SUDO" ] && [ "$(id -u)" != 0 ] && "$REAL_SUDO" -n kill -TERM "${pids[@]}" 2>/dev/null
    return 0
}

alive_any() { local p; for p in "$@"; do kill -0 "$p" 2>/dev/null && return 0; done; return 1; }

tree_pids() {  # tree_pids PID: every process below PID, deepest first, into TREE
    TREE=()
    local map line pid ppid
    local -A kids=()
    map=$(ps -A -o pid= -o ppid= 2>/dev/null) || map=''
    if [ -z "$map" ]; then   # Cygwin and Git for Windows: /proc/PID/ppid
        local d; for d in /proc/[0-9]*; do [ -r "$d/ppid" ] && map+="${d#/proc/} $(cat "$d/ppid" 2>/dev/null)"$'\n'; done
    fi
    while read -r pid ppid; do [ -n "$ppid" ] && kids[$ppid]+="$pid "; done <<<"$map"
    local -a queue=("$1") found=()
    while ((${#queue[@]})); do
        p=${queue[0]}; queue=("${queue[@]:1}")
        for pid in ${kids[$p]:-}; do found+=("$pid"); queue+=("$pid"); done
    done
    for ((i = ${#found[@]} - 1; i >= 0; i--)); do TREE+=("${found[i]}"); done
}

on_line() {  # on_line LINE: one line from deep32-setup
    local line=$1
    wiz_log "$line"
    RUN_LINES+=("$line"); ((${#RUN_LINES[@]} > 400)) && RUN_LINES=("${RUN_LINES[@]:100}")
    DIRTY=1
    if [[ $line == '{"deep32":'* ]]; then json_get "$line" && on_event; return; fi
    case $line in
        '[ok] '*|'[fix] '*|'[you] '*|'[fail] '*|'[info] '*)
            install_phase "$line"
            local tag=${line%%]*}; tag=${tag#[}
            IT_TAG+=("$tag"); IT_TEXT+=("${line#*] }")
            [ "$tag" = fail ] || ACTIVITY=${line#*] }
            return ;;
    esac
    RAW+=("$line"); ((${#RAW[@]} > 300)) && RAW=("${RAW[@]:100}")
    [ -n "${line//[[:space:]]/}" ] && { RECENT+=("$line"); ((${#RECENT[@]} > 6)) && RECENT=("${RECENT[@]:1}"); LAST_RAW=$line; }
    if [[ $line =~ (https://login\.tailscale\.com/[A-Za-z0-9/._?=-]+) ]] ||
        { [ "$VIEW" = signin ] && [ -z "$SIGNIN_URL" ] && [[ $line =~ (https://[A-Za-z0-9.-]+/[A-Za-z0-9/._?=%-]*) ]]; }; then
        SIGNIN_URL=${BASH_REMATCH[1]}; VIEW=signin; return
    fi
    if [ "$line" = '{' ] && [ "$RUN_KIND" = role ]; then PLAN=('{'); PLAN_IN=1; return; fi
    if ((PLAN_IN)); then
        PLAN+=("$line")
        [[ $line =~ ^\ \ \"digest\":\ \"([0-9a-f]{64})\" ]] && PLAN_DIGEST=${BASH_REMATCH[1]}
        [ "$line" = '}' ] && { PLAN_IN=0; stage_set 3; if [ -z "$DIGEST" ] || [ "$DIGEST" != "$PLAN_DIGEST" ]; then ACTIVITY='The installer has shown its plan'; else CONSENT=1; VIEW=install; ACTIVITY='Plan accepted; installing'; fi; }
        return
    fi
    install_phase "$line"
    case $line in
        'Verified local image '*) dl_mark "${line#Verified local image }" done ;;
        'Verified '*' from mirror '*) local f=${line#Verified }; dl_mark "${f%% from mirror *}" done ;;
        'Completed from mirror '*) dl_mark_current done ;;
        'Download interrupted'*) dl_mark_current stopped ;;
        'VM artefacts written: '*) VM_LINE=${line#VM artefacts written: }; VM_LINE=${VM_LINE%%; review*} ;;
        'Mounting '*|'Running the disc3 Docker form'|'Waiting for the verified disc3 loader'*|'The verified disc3 loader'*)
            stage_set 3; VIEW=install; ACTIVITY=$line ;;
        'Checking read-only prerequisites'*) ACTIVITY='Checking this host again before any change' ;;
        *': waiting for '*) ACTIVITY=$line ;;
    esac
}

num_ok() { [[ $1 =~ ^[0-9]{1,15}$ ]]; }   # a plain number, safe for arithmetic

on_event() {  # the event in EV
    case ${EV[deep32]} in
        role) GUESS=${EV[role]}; GUESS_GPU=${EV[gpu]:-}; GUESS_OS=${EV[os]:-}; GUESS_HV=${EV[kvm]:-}; GUESS_AGENT=${EV[agents]:+yes} ;;
        step)
            case ${EV[step]} in check) LAST_STEP=0 ;; download) LAST_STEP=2 ;; install) LAST_STEP=3 ;; pair) LAST_STEP=4 ;; esac
            case ${EV[step]} in
                check) [ "$RUN_KIND" = role ] && VIEW=running ;;
                download) stage_set 2; VIEW=download ;;
                install) stage_set 3; VIEW=install ;;
                pair) stage_set 4; VIEW=pairwait ;;
            esac ;;
        ask)
            ASK_ID=${EV[id]}; ASK_TEXT=${EV[text]}
            if [ "$RUN_KIND" = look ]; then FIXABLE+=("$ASK_ID"); run_answer n
            elif [ -n "$OVERLAY" ]; then ASK_PENDING=1   # shown when the open dialog closes
            else ask_open; fi ;;
        signin) VIEW=signin; SIGNIN_URL='' ;;
        run)
            if [ "${EV[tool]}" = get ]; then
                ACTIVITY="Getting ${EV[disc]}"
                if [ "${EV[action]:-}" = prepare-vm ]; then ACTIVITY='Writing the VM settings'
                else DL_DIR=${EV[dir]:-}; DL_DISC=${EV[disc]:-}; DL_CLASS=${EV[class]:-}; DL_MIRRORS=''; fi
            fi ;;
        exit) num_ok "${EV[status]}" && [[ ${EV[tool]} =~ ^[a-z]+$ ]] && STATUSES+=" ${EV[tool]}:${EV[status]}" ;;
        pair) num_ok "${EV[minutes]:-5}" || EV[minutes]=5; [[ ${EV[code]} =~ ^[A-Z2-7]{10}$ ]] || return 0
            PAIR_CODE=${EV[code]}; PAIR_PEER=${EV[peer]:-}; wiz_now; PAIR_UNTIL=$(( NOW_MS + ${EV[minutes]} * 60000 )); stage_set 4; VIEW=pair; FOCUS=0 ;;
        paired) PAIRED=1 ;;
        checked) num_ok "${EV[problems]:-0}" && PROBLEMS=${EV[problems]:-0} ;;
        demo-progress) ((opt_demo)) && num_ok "${EV[bytes]}" && num_ok "${EV[size]}" && dl_update "${EV[file]}" "${EV[bytes]}" "${EV[size]}" ;;
    esac
}

# Downloads: one row per file, fed by the demo's progress events or by watching the download folder.

dl_index() { local i; for i in "${!DL_NAME[@]}"; do [ "${DL_NAME[i]}" = "$1" ] && { DI=$i; return 0; }; done; DI=-1; return 1; }

dl_update() {  # dl_update FILE HAVE SIZE: new byte count for FILE; keeps a smoothed rate
    local f=$1 have=$2 size=$3 dt db rate
    wiz_now
    if ! dl_index "$f"; then
        DL_NAME+=("$f"); DL_SIZE+=("$size"); DL_HAVE+=(0); DL_STATE+=(wait); DL_RATE+=(0); DL_T+=("$NOW_MS"); DL_B+=("$have")
        DI=$((${#DL_NAME[@]} - 1))
    fi
    ((size > 0)) && DL_SIZE[DI]=$size
    if [ "${DL_STATE[DI]}" != done ]; then
        if ((size > 0 && have >= size)); then DL_STATE[DI]=check
        elif ((have > DL_HAVE[DI])); then DL_STATE[DI]=down; fi
    fi
    dt=$(( NOW_MS - DL_T[DI] )); db=$(( have - DL_B[DI] ))
    if ((dt >= 900)) && ((db >= 0)); then
        rate=$(( db * 1000 / dt ))
        if ((DL_RATE[DI] == 0)); then DL_RATE[DI]=$rate; else DL_RATE[DI]=$(( (DL_RATE[DI] * 7 + rate * 3) / 10 )); fi
        DL_T[DI]=$NOW_MS; DL_B[DI]=$have
    fi
    DL_HAVE[DI]=$have; DIRTY=1
}

dl_mark() {  # dl_mark FILE STATE (a path is cut to its file name)
    local f=${1%.}; f=${f##*/}
    dl_index "$f" || { DL_NAME+=("$f"); DL_SIZE+=(0); DL_HAVE+=(0); DL_STATE+=(wait); DL_RATE+=(0); DL_T+=(0); DL_B+=(0); DI=$((${#DL_NAME[@]} - 1)); }
    DL_STATE[DI]=$2
    [ "$2" = done ] && ((DL_SIZE[DI] > 0)) && DL_HAVE[DI]=${DL_SIZE[DI]}
    DIRTY=1
}

dl_mark_current() {  # dl_mark_current STATE: the file being fetched now
    local i; for i in "${!DL_NAME[@]}"; do case ${DL_STATE[i]} in down|check|wait) dl_mark "${DL_NAME[i]}" "$1"; return ;; esac; done
}

file_size() {  # file_size PATH: its size in bytes, into FS (empty when absent); sudo -n for root-owned Forge folders
    FS=''
    if [ -e "$1" ]; then FS=$(stat -c %s -- "$1" 2>/dev/null || stat -f %z -- "$1" 2>/dev/null)
    elif [ "$ROLE" = forge ] && [ "$(id -u)" != 0 ]; then FS=$(sudo -n stat -c %s -- "$1" 2>/dev/null); fi
}

mirrors_files() {  # mirrors_files FILE DISC CLASS: "name size" lines this download selects, from MIRRORS.json
    awk -v disc="$2" -v cls="$3" '
        { text = text $0 "\n" }
        END {
            n = length(text); depth = 0; key = ""; arr = ""; i = 1
            while (i <= n) {
                c = substr(text, i, 1)
                if (c == "\"") {
                    j = i + 1; s = ""
                    while (j <= n) { d = substr(text, j, 1); if (d == "\\") { s = s substr(text, j + 1, 1); j += 2; continue } if (d == "\"") break; s = s d; j++ }
                    i = j + 1
                    k = i; while (substr(text, k, 1) ~ /[ \t\r\n]/) k++
                    if (substr(text, k, 1) == ":") { key = s; i = k + 1; if (depth == 1) top = s; continue }
                    if (depth == 3) val[key] = s
                    continue
                }
                if (c == "{" || c == "[") { depth++; if (depth == 3) { delete val } i++; continue }
                if (c == "}" || c == "]") {
                    if (depth == 3 && c == "}") {
                        want = 0
                        if (top == "discs" && val["role"] == disc) want = 1
                        if (top == "bundles" && disc == "disc3" && (val["bundle_id"] == "core" || val["bundle_id"] == "ingestion" || val["bundle_id"] == "embedder-" cls)) want = 1
                        if (want && val["file"] != "" && val["size"] != "") print val["file"], val["size"]
                    }
                    depth--; i++; continue
                }
                if (c ~ /[0-9-]/) { j = i; while (substr(text, j, 1) ~ /[0-9.eE+-]/) j++; if (depth == 3) val[key] = substr(text, i, j - i); i = j; continue }
                i++
            }
        }' "$1"
}

dl_poll() {  # watch the download folder of a real run: partial .download files grow, finished files appear
    [ -n "$DL_DIR" ] && [ "$opt_demo" = 0 ] || return 0
    wiz_now
    ((NOW_MS - DL_POLL_AT < 1000)) && return 0
    DL_POLL_AT=$NOW_MS
    if [ "${DEEP32_WIZARD_TEST_UNREADABLE:-}" = 1 ] || ! { [ -r "$DL_DIR" ] && [ -x "$DL_DIR" ]; }; then dl_poll_root; return 0; fi
    if [ -z "$DL_MIRRORS" ] || ((NOW_MS - DL_MIRRORS_AT > 10000)); then
        local m best=''
        for m in "$DL_DIR"/.deep32-*/revision-*/MIRRORS.json; do [ -r "$m" ] && best=$m; done
        if [ -n "$best" ]; then
            DL_MIRRORS=$best; DL_MIRRORS_AT=$NOW_MS
            local name size
            while read -r name size; do dl_index "$name" || dl_update "$name" 0 "$size"; done < <(mirrors_files "$best" "$DL_DISC" "$DL_CLASS")
        fi
    fi
    local i f
    for i in "${!DL_NAME[@]}"; do
        f=${DL_NAME[i]}
        [ "${DL_STATE[i]}" = done ] && continue
        file_size "$DL_DIR/$f"
        if [ -n "$FS" ]; then dl_update "$f" "$FS" "${DL_SIZE[i]}"; [ "${DL_STATE[i]}" = done ] || DL_STATE[i]=check; continue; fi
        file_size "$DL_DIR/$f.download"
        [ -n "$FS" ] && dl_update "$f" "$FS" "${DL_SIZE[i]}"
    done
    if ((${#DL_NAME[@]} == 0)); then
        local p
        for p in "$DL_DIR"/*.download; do [ -e "$p" ] || continue; file_size "$p"; dl_update "$(basename -- "${p%.download}")" "${FS:-0}" 0; done
    fi
}

# ---------------------------------------------------------------------------------------------------------------
# 8. What each run means for the flow.

check_for_role() { CHECK_FOR=${1:-${ROLE:-${GUESS:-forge}}}; }

dl_poll_root() {  # deep32-get runs as root and keeps its folder 0700: read it through sudo -n, or say it cannot be read
    local s=${REAL_SUDO:-sudo} m name size
    if ! "$s" -n true 2>/dev/null; then DL_HIDDEN=1; return 0; fi
    DL_HIDDEN=0
    if [ -z "$DL_MIRRORS" ]; then
        m=$("$s" -n sh -c 'for f in "$1"/.deep32-*/revision-*/MIRRORS.json; do [ -f "$f" ] && echo "$f"; done' _ "$DL_DIR" 2>/dev/null | tail -n 1)
        if [ -n "$m" ]; then
            DL_MIRRORS=$m
            while read -r name size; do dl_index "$name" || dl_update "$name" 0 "$size"; done < <("$s" -n cat "$m" 2>/dev/null | mirrors_files - "$DL_DISC" "$DL_CLASS")
        fi
    fi
    while read -r size name; do
        case $name in
            *.download) name=${name%.download}; dl_index "$name" && [ "${DL_STATE[DI]}" != done ] && dl_update "$name" "$size" "${DL_SIZE[DI]}" ;;
            *) dl_index "$name" && [ "${DL_STATE[DI]}" != done ] && { dl_update "$name" "$size" "${DL_SIZE[DI]}"; DL_STATE[DI]=check; } ;;
        esac
    done < <("$s" -n find "$DL_DIR" -maxdepth 1 -type f -printf '%s %f\n' 2>/dev/null)
}

start_checks() {  # start_checks look|fix: this machine's checks for CHECK_FOR (look answers every question no)
    needs_root "$1" "$CHECK_FOR" && sudo_ready
    IT_TAG=(); IT_TEXT=(); FIXABLE=(); PROBLEMS=0; SEL=0; SCROLL=0
    stage_set 0; VIEW=checking; CHECKED_ROLE=$CHECK_FOR
    local -a args=("$CHECK_FOR" --checks-only)
    [ -n "$F_NAME" ] && [ "$CHECK_FOR" = forge ] && args+=(--name "$F_NAME")
    ((opt_yes)) && [ "$1" = fix ] && args+=(--yes)   # the look never changes the machine
    run_start "$1" "${args[@]}"
}

on_run_end() {  # on_run_end KIND STATUS
    local kind=$1 st=$2
    case $kind in
        guess)
            [ -n "$ROLE" ] || ROLE=$GUESS
            check_for_role "$ROLE"; start_checks look ;;
        look) VIEW=checks; FOCUS=0; SEL=0; local k; for k in "${!IT_TAG[@]}"; do case ${IT_TAG[k]} in fix|fail) SEL=$k; break ;; esac; done
            ((PROBLEMS == 0)) && ((st != 0)) && PROBLEMS=1
            STEP_NOTE[0]=$(check_note) ;;
        fix) ((st == 0)) && DEMO_FIXED=1; check_for_role "$CHECKED_ROLE"; start_checks look ;;
        role) on_role_end "$st" ;;
    esac
}

check_note() {
    local i ok=0 fx=0
    for i in "${!IT_TAG[@]}"; do case ${IT_TAG[i]} in ok) ok=$((ok + 1)) ;; fix|fail) fx=$((fx + 1)) ;; esac; done
    if ((fx)); then echo "$ok passed, $fx to fix"; else echo "$ok passed"; fi
}

on_role_end() {  # the role run stopped with status $1
    local st=$1 i joined=''
    for i in "${RUN_LINES[@]}"; do joined+=$i$'\n'; done
    if ((st == 0)); then
        [ "$VIEW" = done ] && return 0
        case $ROLE in
            forge) if [ -n "$PAIR_CODE" ] && ((PAIRED == 0)); then stage_set 4; VIEW=pair; else finish; fi ;;
            workstation) vm_begin ;;
            *) finish ;;
        esac
        return
    fi
    if [ "$ROLE" = forge ] && [[ $joined == *'Plan consent required'* ]] && [ -n "$PLAN_DIGEST" ]; then
        stage_set 3; VIEW=plan; TYPED=''; FOCUS=0
        [ -n "$DIGEST" ] && [ "$DIGEST" != "$PLAN_DIGEST" ] && STATUS_MSG='The plan changed since you typed its digest; read it again.'
        DIGEST=''; STEP_NOTE[2]="$(dl_note)"; return
    fi
    if [ "$ROLE" = forge ] && [[ $joined == *'no pairing window opened; if the Forge is already paired'* ]]; then
        SOFT_DONE='No pairing window opened. If this Forge is already paired with your Workstation, it is done.'
        finish; return
    fi
    fail_view
}

fail_view() {
    if [ -n "${LAST_STEP:-}" ] && ((LAST_STEP < STAGE)); then STEP_STATE[STAGE]=pending; STAGE=$LAST_STEP; fi
    FAIL_STAGE=$STAGE; STEP_STATE[STAGE]=failed
    FAIL_TITLE="${STEP_NAMES[STAGE]} stopped"
    local from=$(( ${#RUN_LINES[@]} > 80 ? ${#RUN_LINES[@]} - 80 : 0 ))
    err_decode "$STATUSES" "${RUN_LINES[@]:from}"
    FAIL_ROWS=("${DEC_ROWS[@]}"); FAIL_LINES=("${DEC_LINES[@]}")
    VIEW=fail; FOCUS=0; DIRTY=1
    state_save failed
}

finish() {
    local i
    for ((i = 0; i < 5; i++)); do case ${STEP_STATE[i]} in skipped|later) ;; *) STEP_STATE[i]=done ;; esac; done
    STEP_STATE[5]=current; STAGE=5; VIEW=done; FOCUS=0; DIRTY=1; REACHED_DONE=1
    state_save done
}

dl_note() {
    local i n=0 d=0
    for i in "${!DL_NAME[@]}"; do n=$((n + 1)); [ "${DL_STATE[i]}" = done ] && d=$((d + 1)); done
    ((n)) && echo "$d of $n files"
}

role_run() {  # start this role's own run (headend hands the terminal to Connect instead)
    local -a args=("$ROLE")
    ((opt_yes)) && args+=(--yes)
    case $ROLE in
        forge)
            [ -n "$F_ROOT" ] && args+=(--data-root "$F_ROOT")
            [ -n "$F_NAME" ] && args+=(--name "$F_NAME")
            [ -n "$opt_ws" ] && args+=(--workstation "$opt_ws")
            settings_write && args+=(--settings "$SETTINGS_FILE")
            [ -n "$DIGEST" ] && args+=(--plan-digest "$DIGEST") ;;
        workstation) [ -n "$W_HV" ] && args+=(--hypervisor "$W_HV") ;;
        headend) handover_connect; return ;;
    esac
    IT_TAG=(); IT_TEXT=(); RECENT=(); ACTIVITY='Starting'; PLAN=(); PLAN_DIGEST=''; PHASE=0; XS=(); XF_START=0; XF_LAST=0
    wiz_now; PHASE_AT=$NOW_MS
    ((CONSENT)) || { DL_NAME=(); DL_SIZE=(); DL_HAVE=(); DL_STATE=(); DL_RATE=(); DL_T=(); DL_B=(); }
    role_index "$ROLE"; ATTEMPT[RI]=$((ATTEMPT[RI] + 1)); ATTEMPT_NOW=${ATTEMPT[RI]}
    needs_root role "$ROLE" && sudo_ready
    stage_set 2; VIEW=running
    [ "$ROLE" = forge ] && [ -n "$DIGEST" ] && stage_set 3
    state_save running
    run_start role "${args[@]}"
}

settings_write() {  # the Forge's non-secret settings for the installer, in the wizard's state folder
    [ "$ROLE" = forge ] || return 1
    SETTINGS_FILE="$STATE_DIR/forge-settings.json"
    ((opt_demo)) && return 0
    local ct=false; [ "$F_CT" = yes ] && ct=true
    (umask 077; printf '{\n  "format": "deep32-settings",\n  "format_version": 1,\n  "role": "forge",\n  "node_name": "%s",\n  "user_name": "%s",\n  "time_zone": "%s",\n  "keyboard": "%s",\n  "profile": "%s",\n  "transport": "tailnet",\n  "publish": "serve-https",\n  "forge_accept_ct": %s,\n  "written_by": "deep32-wizard"\n}\n' \
        "$F_NAME" "$F_USER" "$F_TZ" "$F_KB" "$F_PROFILE" "$ct" >"$SETTINGS_FILE")
}

state_save() {  # remember where this machine's setup stopped, for the next start
    ((opt_demo)) && return 0
    [ -n "$STATE_DIR" ] || return 0
    (umask 077; printf 'role=%s\nstage=%s\nstatus=%s\nwhen=%s\nroot=%s\nname=%s\nhv=%s\nws=%s\nagent=%s\ntz=%s\nuser=%s\nct=%s\n' \
        "$ROLE" "$STAGE" "$1" "$(date '+%Y-%m-%d %H:%M')" "$F_ROOT" "$F_NAME" "$W_HV" "$H_URL" "$H_AGENT" "$F_TZ" "$F_USER" "$F_CT" >"$STATE_DIR/wizard.state")
}

state_load() {  # RESUME_* from the last run, when it did not finish
    RESUME=''
    ((opt_demo)) && return 0
    local f="$STATE_DIR/wizard.state" k v
    [ -r "$f" ] || return 0
    RS=()
    while IFS='=' read -r k v; do RS[$k]=$v; done <"$f"
    [ "${RS[status]:-}" = done ] && return 0
    [ -n "${RS[role]:-}" ] && RESUME=${RS[role]}
}

# The terminal goes back to plain text for programs that read the keyboard themselves: sudo's password prompt and
# Connect's hidden pairing-code prompt. The wizard comes back afterwards.

handover() {  # handover TITLE COMMAND...: run COMMAND on the plain terminal, logged; status in HANDOVER_STATUS
    local title=$1; shift
    screen_leave
    printf '\n  %s%s%s\n\n' "$S_B" "$title" "$S_0"
    if [ "$1" = - ]; then shift; "$@"; HANDOVER_STATUS=$?
    else
        # tee writes whatever arrives, so "Type merge to approve this file plan: " shows before the answer is typed
        { "$@"; echo "$?" >"$STATE_DIR/.handover"; } 2>&1 | tee -a "${LOG_FILE:-/dev/null}"
        HANDOVER_STATUS=$(cat "$STATE_DIR/.handover" 2>/dev/null) || HANDOVER_STATUS=1
        rm -f "$STATE_DIR/.handover"
        [[ $HANDOVER_STATUS =~ ^[0-9]+$ ]] || HANDOVER_STATUS=1
        printf '\n  %sPress Enter to go back to the setup screen.%s ' "$S_MUTED" "$S_0"
        IFS= read -r _
    fi
    screen_enter
}

sudo_ready() {  # sudo's password on a plain screen now, so no later step stops for it while the wizard owns the keys
    ((opt_demo)) && return 0
    [ "$(id -u)" = 0 ] && return 0
    [ -n "$REAL_SUDO" ] || return 0
    "$REAL_SUDO" -n true 2>/dev/null && return 0
    handover 'The next step reads Docker or changes this machine as root. Type your password for sudo; it is not shown.' - "$REAL_SUDO" -v
    "$REAL_SUDO" -n true 2>/dev/null
}

needs_root() {  # needs_root KIND ROLE: this run can call sudo (the Forge checks read Docker as root; a yes changes the machine)
    ((opt_demo)) && return 1
    [ "$(id -u)" = 0 ] && return 1
    [ "$2" = forge ] || [ "$1" = fix ]
}

sudo_wrapper() {  # a sudo first on the children's PATH that never asks: an expired ticket fails the step, never the screen
    SUDO_WRAP=''
    ((opt_demo)) && return 0
    REAL_SUDO=$(command -v sudo 2>/dev/null) || REAL_SUDO=''
    [ -n "$REAL_SUDO" ] && [ "$(id -u)" != 0 ] || return 0
    SUDO_WRAP="$STATE_DIR/bin"
    (umask 077; mkdir -p "$SUDO_WRAP" && printf '#!/bin/sh\n# deep32-wizard: sudo that never asks for a password while the wizard owns the screen\nexec "%s" -n "$@"\n' "$REAL_SUDO" >"$SUDO_WRAP/sudo" && chmod 700 "$SUDO_WRAP/sudo") 2>/dev/null || SUDO_WRAP=''
}

sudo_keepalive() {  # refresh sudo's time stamp only while a root step runs
    ((opt_demo)) && return 0
    [ -n "$CHILD_PID" ] && [ "$ROLE" = forge ] && [ "$(id -u)" != 0 ] && [ -n "$REAL_SUDO" ] || return 0
    wiz_now
    ((NOW_MS - ${SUDO_AT:-0} > 60000)) || return 0
    SUDO_AT=$NOW_MS; "$REAL_SUDO" -n -v 2>/dev/null
}

handover_connect() {
    stage_set 3; STEP_STATE[2]=skipped
    local -a args=(headend --workstation "$H_URL")
    [ -n "$H_AGENT" ] && [ "$H_AGENT" != auto ] && args+=(--agent "$H_AGENT")
    ((opt_yes)) && args+=(--yes)
    role_index headend; ATTEMPT[RI]=$((ATTEMPT[RI] + 1))
    state_save running
    LOG_BEFORE=0; [ -n "$LOG_FILE" ] && LOG_BEFORE=$(wc -l <"$LOG_FILE" 2>/dev/null | tr -d ' ')
    local title='Connect runs here in plain text. When it asks for the code, type the code the Workstation shows (it is not shown as you type).'
    if ((opt_demo)); then
        handover "$title" env DEEP32_WIZARD_DEMO_ATTEMPT="${ATTEMPT[RI]}" "${BASH:-bash}" "$wiz_self" --demo-replay headend
    else
        handover "$title" "${BASH:-bash}" "$wiz_setup" "${args[@]}"
    fi
    RUN_LINES=(); local l
    [ -n "$LOG_FILE" ] && while IFS= read -r l; do RUN_LINES+=("$l"); done < <(tail -n +$((LOG_BEFORE + 1)) "$LOG_FILE" | tail -n 80)
    STATUSES=" setup:$HANDOVER_STATUS"
    if [ "$HANDOVER_STATUS" = 0 ]; then STEP_STATE[3]=done; STEP_STATE[4]=done; PAIRED=1; finish
    else stage_set 3; fail_view; fi
}

# ---------------------------------------------------------------------------------------------------------------
# 9. Drawing. Every line is exactly W cells; only lines that changed are written.

layout() {
    TOP=0; ((H >= 28)) && TOP=1
    RW=26; ((W >= 110)) && RW=28
    PW=$((W - 7 - RW))
    BOX_TOP=$((TOP + 3)); BOX_BOT=$((H - 3)); CH=$((BOX_BOT - BOX_TOP - 1))
    CW=$((PW - 4))
}

spaces() { printf -v SP '%*s' "$1" ''; }

# Panel lines: pl starts one, pa appends a styled piece (clipped), pe pads it to the panel width and keeps it.
pl() { _pl="${1:-}  "; _plw=2; _plbg=${1:-}; }
pa() {
    local t=$2 room=$((PW - 2 - _plw))
    ((room <= 0)) && return 0
    ((${#t} > room)) && t="${t:0:room-1}$G_ELL"
    _pl+="$_plbg$1$t$S_0"; _plw=$((_plw + ${#t}))
}
pgap() { ((_plw < $1)) && { spaces $(($1 - _plw)); _pl+="$_plbg$SP$S_0"; _plw=$1; }; }
pe() { local n=$((PW - _plw)); ((n > 0)) && { spaces "$n"; _pl+="$_plbg$SP$S_0"; }; PANEL+=("$_pl"); }
pblank() { spaces "$PW"; PANEL+=("$SP"); }
pline() { pl; pa "$1" "$2"; pe; }   # pline STYLE TEXT

wrap() {  # wrap WIDTH TEXT: word-wrapped lines into WRAPPED
    local width=$1 line='' word
    local -a words
    read -r -a words <<<"$2"
    WRAPPED=()
    ((width < 8)) && width=8
    for word in "${words[@]}"; do
        if [ -z "$line" ]; then line=$word
        elif ((${#line} + 1 + ${#word} <= width)); then line+=" $word"
        else WRAPPED+=("$line"); line=$word; fi
        while ((${#line} > width)); do WRAPPED+=("${line:0:width}"); line=${line:width}; done
    done
    [ -n "$line" ] && WRAPPED+=("$line")
    ((${#WRAPPED[@]})) || WRAPPED=('')
}

ppara() {  # ppara STYLE TEXT [INDENT]: a wrapped paragraph
    local ind=${3:-0} l
    spaces "$ind"; local pad=$SP
    wrap $((CW - ind)) "$2"
    for l in "${WRAPPED[@]}"; do pl; pa '' "$pad"; pa "$1" "$l"; pe; done
}

ptitle() {  # ptitle TITLE [SUBTITLE] [ICON STYLE]
    pblank
    pl; [ -n "${3:-}" ] && { pa "$4$S_B" "$3"; pa '' ' '; }; pa "$S_TITLE" "$1"; pe
    [ -n "${2:-}" ] && ppara "$S_MUTED" "$2"
    pblank
}

pbuttons() {  # pbuttons LABEL...: a row of buttons; FOCUS marks one
    local i lab
    local -a labels=("$@")
    ((FOCUS >= ${#labels[@]})) && FOCUS=0
    pl
    for i in "${!labels[@]}"; do
        lab=${labels[i]}
        if ((i == FOCUS)); then pa "$S_BTNBG$S_BTNFG$S_B" " $lab "; else pa "$S_IDLEBG$S_IDLEFG" " $lab "; fi
        pa '' '  '
    done
    pe
    BTN_COUNT=$#
}

pfill() { while ((${#PANEL[@]} < CH)); do pblank; done; }
pdrop() { ((${#PANEL[@]})) && unset "PANEL[$((${#PANEL[@]} - 1))]"; PANEL=("${PANEL[@]}"); }

psteps() {  # psteps MAX STEP...: numbered steps; prose wraps, each `command` gets a line of its own
    local max=$1 i=0 s; shift
    for s in "$@"; do
        ((i >= max)) && break
        pstep "$((i + 1))" '' 5 "$s"
        i=$((i + 1))
    done
}

pstep() {  # pstep NUMBER LABEL TEXTCOL TEXT: one numbered item; a bold LABEL before the text when given
    local num=$1 label=$2 col=$3 rest=$4 prose cmd first=1 l
    _pstep_lead() {
        pl
        if ((first)); then
            if [[ $num == [0-9]* ]]; then pa "$S_ACCENT" " $num.  "; else pa "$S_FAINT" " $num   "; fi
            [ -n "$label" ] && pa "$S_B" "$label"; first=0
        fi
        pgap $((col + 2))
    }
    while [ -n "$rest" ]; do
        if [[ $rest == *'`'*'`'* ]]; then
            prose=${rest%%'`'*}; rest=${rest#*'`'}; cmd=${rest%%'`'*}; rest=${rest#*'`'}
        else prose=$rest; cmd=''; rest=''; fi
        while [[ $prose == [' ,.;:']* ]]; do prose=${prose:1}; done
        if [ -n "$prose" ] && [[ $prose != [.,:] ]] && [ "$prose" != ";" ]; then
            wrap $((CW - col)) "$prose"
            for l in "${WRAPPED[@]}"; do _pstep_lead; pa '' "$l"; pe; done
        fi
        if [ -n "$cmd" ]; then   # a command sits under the item, whole, broken only at spaces when it is too long
            local room=$((CW - 7)) c=$col part cut
            col=5
            while ((${#cmd} > room)); do
                part=${cmd:0:room}; cut=${part% *}; ((${#cut} > 10)) && part=$cut
                _pstep_lead; pa "$S_ACCENTT" "  $part"; pe
                cmd=${cmd:${#part}}; cmd="  ${cmd# }"
            done
            _pstep_lead; pa "$S_ACCENTT" "  $cmd"; pe
            col=$c
        fi
    done
    unset -f _pstep_lead
}

pbottom() {  # pbottom N: blank lines so the next N lines end at the panel's bottom
    local target=$((CH - $1 - 1))
    while ((${#PANEL[@]} < target)); do pblank; done
}

bx_top() {  # bx_top STYLE TITLE: the top edge of a box CW cells wide, with a title
    local hz; printf -v hz '%*s' "$CW" ''; hz=${hz// /$G_H}
    pl; pa "$1" "$G_TL$G_H "; pa "$1$S_B" "$2"; pa "$1" " ${hz:0:CW-5-${#2}}$G_TR"; pe
}
bx_bot() {  # bx_bot STYLE [TAIL]: the bottom edge, with an optional note at its right end
    local hz t=${2:-}; printf -v hz '%*s' "$CW" ''; hz=${hz// /$G_H}
    pl; pa "$1" "$G_BL${hz:0:CW-2-${#t}}"; [ -n "$t" ] && pa "$S_FAINT" "$t"; pa "$1" "$G_BR"; pe
}
bx_open() { pl; pa "$1" "$G_V"; pa '' '  '; BX_STYLE=$1; }   # start a box line; append with pa, end with bx_close
bx_close() { pgap $((PW - 3)); pa "$BX_STYLE" "$G_V"; pe; }
bx_text() {  # bx_text STYLE TEXT: wrapped text inside a box
    local l; wrap $((CW - 6)) "$2"; PCMD_IN=0
    for l in "${WRAPPED[@]}"; do bx_open "$BX_STYLE"; if [ -n "$1" ]; then pa "$1" "$l"; else pcmd_inline "$l"; fi; bx_close; done
}
bx_buttons() {  # bx_buttons LABEL...: buttons inside a box
    local i; local -a labels=("$@")
    bx_open "$BX_STYLE"
    for i in "${!labels[@]}"; do
        if ((i == FOCUS)); then pa "$S_BTNBG$S_BTNFG$S_B" " ${labels[i]} "; else pa "$S_IDLEBG$S_IDLEFG" " ${labels[i]} "; fi; pa '' '  '
    done
    bx_close; BTN_COUNT=${#labels[@]}
}

rail() {  # RAIL: the six steps
    RAIL=()
    local i st ic is ls cap mark row
    spaces "$RW"; RAIL+=("$SP")
    for ((i = 0; i < 6; i++)); do
        st=${STEP_STATE[i]}; mark=' '; cap=${STEP_NOTE[i]}
        case $st in
            done) ic=$G_OK; is=$S_OK; ls=$S_MUTED ;;
            current) ic=$G_CUR; is=$S_ACCENT; ls=$S_B; mark=$G_MARK ;;
            failed) ic=$G_FAIL; is=$S_ERR; ls=$S_B; mark=$G_MARK ;;
            skipped) ic=$G_SKIP; is=$S_FAINT; ls=$S_FAINT; cap=${cap:-not needed here} ;;
            later) ic=$G_PEND; is=$S_FAINT; ls=$S_MUTED; cap=${cap:-on the Workstation} ;;
            *) ic=$G_PEND; is=$S_FAINT; ls=$S_MUTED ;;
        esac
        [ "$i" = 1 ] && [ -n "$ROLE" ] && [ "$st" = done ] && [ -z "$cap" ] && { role_index "$ROLE"; cap=${ROLE_NAMES[RI]}; }
        [ "$i" = 2 ] && [ "$st" = current ] && [ -z "$cap" ] && cap=$(dl_note)
        if [ "$i" = 4 ] && [ "$st" = current ] && [ -n "$PAIR_CODE" ] && ((PAIRED == 0)); then
            wiz_now; fmt_clock $(( (PAIR_UNTIL - NOW_MS) / 1000 )); cap="code, $FMT left"
        fi
        local ms=$S_ACCENT; [ "$st" = failed ] && ms=$S_ERR
        row="$ms$mark$S_0 $is$ic$S_0  $ls${STEP_NAMES[i]}$S_0"
        spaces $((RW - 5 - ${#STEP_NAMES[i]})); RAIL+=("$row$SP")
        if [ -n "$cap" ]; then
            ((${#cap} > RW - 6)) && cap="${cap:0:RW-7}$G_ELL"
            spaces $((RW - 5 - ${#cap})); RAIL+=("     $S_MUTED$cap$S_0$SP")
        else spaces "$RW"; RAIL+=("$SP"); fi
        ((CH >= 20)) && { spaces "$RW"; RAIL+=("$SP"); }
    done
    while ((${#RAIL[@]} < CH)); do spaces "$RW"; RAIL+=("$SP"); done
    if ((opt_demo)); then
        local tag='Demo: nothing changes'
        spaces $((RW - 2 - ${#tag})); RAIL[CH - 2]="  $S_WARN$tag$S_0$SP"
    fi
}

header() {  # HDR1 HDR2: wordmark and release on top, the 32-cell strip under it
    local right="${VERSION_ID}" left_w
    [ -n "$ROLE" ] && [ "$STAGE" -ge 1 ] && { role_index "$ROLE"; right="${ROLE_NAMES[RI]}  $G_DOT  $right"; }
    HDR1="  $S_WORD""D E E P $S_0$S_WORD32""3 2$S_0   $S_MUTED""setup$S_0"
    left_w=$((2 + 11 + 3 + 5))
    spaces $((W - left_w - ${#right} - 3)); HDR1+="$SP$S_MUTED$right$S_0   "
    progress_permille; strip_cells "$PERM"
    local i s c w=0 out="  "
    for ((i = 0; i < 32; i++)); do
        if ((i < 8)); then c=$G_WIDE; s=$S_STRIP1; elif ((i < 16)); then c=$G_MED; s=$S_STRIP2; else c=$G_NARROW; s=$S_STRIP3; fi
        if ((i >= STRIP_LIT)); then s=$S_TRACK; ((i == STRIP_LIT)) && ((PULSE)) && [ -n "$CHILD_PID" ] && s=$S_STRIP3; fi
        out+="$s$c$S_0"; w=$((w + ${#c}))
        if ((i < 16)); then out+=' '; w=$((w + 1)); fi
    done
    local pct="$((PERM / 10))%"
    local stepword="Step $((STAGE + 1)) of 6"
    ((W - 2 - w - ${#stepword} - ${#pct} - 5 < 2)) && stepword=''
    if [ -n "$stepword" ]; then spaces $((W - 2 - w - ${#stepword} - ${#pct} - 5)); out+="$SP$S_MUTED$stepword$S_0  $S_B$pct$S_0   "
    else spaces $((W - 2 - w - ${#pct} - 3)); out+="$SP$S_B$pct$S_0   "; fi
    HDR2=$out
}

footer() {  # FOOT: key hints for this screen; STATUSL: the status line
    local -a keys=()
    case ${OVERLAY:-$VIEW} in
        log) keys=("$G_UP$G_DOWN" scroll PgUp/PgDn page Esc close) ;;
        help) keys=(Esc close) ;;
        quit) keys=(Enter choose Esc stay) ;;
        ask|vmconfirm|vmpin) keys=("Left/Right" choose Enter confirm) ;;
        vmsecret) keys=(type 'the secret' Enter use Esc cancel) ;;
        hv|where|net) keys=("$G_UP$G_DOWN" choose Enter continue Esc back L log Q quit) ;;
        vmform) keys=("$G_UP$G_DOWN" field Enter continue Esc back) ;;
        vmstep) keys=(Enter check S skip PgDn more Esc 'step back' L log Q quit) ;;
        role) keys=("$G_UP$G_DOWN" choose Enter continue Esc back L log '?' help Q quit) ;;
        form) keys=("$G_UP$G_DOWN" field Enter continue Esc back) ;;
        checking|running|download|install|pairwait|signin) keys=(L log '?' help Q quit) ;;
        plan) keys=(type 'the digest' Enter install Esc back L log Q quit) ;;
        checks) keys=("$G_UP$G_DOWN" details Enter choose Esc back L log '?' help Q quit) ;;
        parts) keys=("$G_UP$G_DOWN" part 'Left/Right' answer Enter continue Esc back Q quit) ;;
        done) if [ -n "$NEXT_OFFER" ]; then keys=("$G_UP$G_DOWN" scroll N 'set up the next part here' Enter choose L log Q quit)
              else keys=("$G_UP$G_DOWN" scroll Enter close L log Q quit); fi ;;
        *) keys=(Enter continue Esc back L log '?' help Q quit) ;;
    esac
    local i out='   ' w=3
    for ((i = 0; i < ${#keys[@]}; i += 2)); do
        out+="$S_B${keys[i]}$S_0 $S_MUTED${keys[i+1]}$S_0    "; w=$((w + ${#keys[i]} + ${#keys[i+1]} + 5))
    done
    if ((w > W)); then out='   '; w=3; fi
    spaces $((W - w)); FOOT="$out$SP"
    local msg=${STATUS_MSG:-} logp=''
    [ -n "$LOG_FILE" ] && logp="Log  $(short_path "$LOG_FILE")"
    ((opt_demo)) && logp="Demo log  $(short_path "$LOG_FILE")"
    # A long log path keeps its end (the file name); a message takes the room that is left.
    local most=$(( W / 2 + 6 ))
    ((${#logp} > most)) && logp="$G_ELL${logp: -$((most - 1))}"
    local room=$((W - 6 - ${#logp}))
    ((${#msg} > room)) && { if ((room > 1)); then msg="${msg:0:room-1}$G_ELL"; else msg=''; fi; }
    spaces $((W - 6 - ${#msg} - ${#logp})); STATUSL="   $S_WARN$msg$S_0$SP$S_FAINT$logp$S_0   "
}

short_path() { local p=$1; [ -n "${HOME:-}" ] && [[ $p == "$HOME"/* ]] && p="~${p#"$HOME"}"; printf '%s' "$p"; }

frame() {  # build FRAME (H lines)
    FRAME=()
    local i
    spaces "$W"; local blank=$SP
    for ((i = 0; i < H; i++)); do FRAME+=("$blank"); done
    if ((W < 80 || H < 24)); then small_screen; return; fi
    header; FRAME[TOP]=$HDR1; FRAME[TOP + 1]=$HDR2
    footer; FRAME[H - 2]=$STATUSL; FRAME[H - 1]=$FOOT
    local hz
    if [ "$OVERLAY" = log ] || [ "$OVERLAY" = help ]; then
        printf -v hz '%*s' $((W - 6)) ''; hz=${hz// /$G_H}
        FRAME[BOX_TOP]="  $S_BORDER$G_TL$hz$G_TR$S_0  "
        FRAME[BOX_BOT]="  $S_BORDER$G_BL$hz$G_BR$S_0  "
        local save=$PW; PW=$((W - 6)); CW=$((PW - 4)); PANEL=()
        case $OVERLAY in log) view_log ;; help) view_help ;; esac
        pfill
        for ((i = 0; i < CH; i++)); do FRAME[BOX_TOP + 1 + i]="  $S_BORDER$G_V$S_0${PANEL[i]}$S_BORDER$G_V$S_0  "; done
        PW=$save; CW=$((PW - 4))
        return
    fi
    local hr hp
    printf -v hr '%*s' "$RW" ''; hr=${hr// /$G_H}; printf -v hp '%*s' "$PW" ''; hp=${hp// /$G_H}
    FRAME[BOX_TOP]="  $S_BORDER$G_TL$hr$G_TT$hp$G_TR$S_0  "
    FRAME[BOX_BOT]="  $S_BORDER$G_BL$hr$G_BT$hp$G_BR$S_0  "
    rail
    PANEL=()
    case $VIEW in
        intro) view_intro ;; checking) view_checks ;; checks) view_checks ;; signin) view_signin ;; parts) view_parts ;;
        role) view_role ;; form) view_form ;; runintro) view_runintro ;; running) view_running ;;
        download) view_download ;; plan) view_plan ;; install) view_install ;; pairwait) view_install ;;
        pair) view_pair ;; fail) view_fail ;; done) view_done ;; connect) view_connect ;;
        hv) view_hv ;; vmkit) view_vmkit ;; where) view_where ;; net) view_net ;; tsnote) view_tsnote ;; vmform) view_form ;;
        vmstep) view_vmstep ;; vmsum) view_vmsum ;;
    esac
    case $OVERLAY in ask) view_ask ;; quit) view_quit ;; vmconfirm|vmpin|vmsecret) view_vmbox ;; esac
    pfill
    for ((i = 0; i < CH; i++)); do FRAME[BOX_TOP + 1 + i]="  $S_BORDER$G_V$S_0${RAIL[i]}$S_BORDER$G_V$S_0${PANEL[i]:0}$S_BORDER$G_V$S_0  "; done
}

small_screen() {
    local msg1='Make the window bigger' msg2="The setup needs at least 80 columns and 24 rows; this window is $W x $H."
    local msg3='Press P to continue in plain text, or Q to quit.' r=$((H / 2 - 2))
    [ -n "$CHILD_PID" ] && msg3='A step is running and goes on. Make the window bigger to see it.'
    ((r < 0)) && r=0
    center() { local t=$1 s=$2 pad=$(( (W - ${#1}) / 2 )); ((pad < 0)) && pad=0; ((${#t} > W)) && t=${t:0:W}; spaces "$pad"; local a=$SP; spaces $((W - pad - ${#t})); FRAME[$3]="$a$s$t$S_0$SP"; }
    center "$msg1" "$S_B" "$r"; ((r + 2 < H)) && center "$msg2" "$S_MUTED" $((r + 2)); ((r + 3 < H)) && center "$msg3" "$S_MUTED" $((r + 3))
    unset -f center
}

draw() {  # write the lines that changed since the last frame, inside one synchronized update
    frame
    local i out=''
    for ((i = 0; i < H; i++)); do
        [ "${FRAME[i]}" = "${PREV[i]:-}" ] && ((FULL == 0)) && continue
        out+=$'\e['"$((i + 1));1H${FRAME[i]}"
    done
    PREV=("${FRAME[@]}"); FULL=0
    if [ -n "$SNAP" ]; then out+=$'\e]7777;snap;'"$SNAP"$'\a'; SNAP=''; fi
    [ -n "$out" ] && printf '%s' $'\e[?2026h'"$out"$'\e[?2026l'
    DIRTY=0
}

# ---------------------------------------------------------------------------------------------------------------
# 10. The screens.

view_intro() {
    ptitle 'Check this machine' 'The setup reads what this machine has before anything changes. It stops and tells you whenever something needs you.'
    pline "$S_B" 'What happens'
    psteps 5 'Check this machine: read-only, a few seconds.' \
        'Choose its role: Forge (the GPU node), Workstation host or agent PC.' \
        'Download and verify the signed discs this role needs.' \
        'Install: you read the plan first and agree to it.' \
        'Pair: a short code typed on the Workstation.'
    pblank
    if [ -n "$RESUME" ]; then
        role_index "$RESUME"
        ppara "$S_WARN" "Last time the ${ROLE_NAMES[RI]} setup stopped (${RS[when]:-}). Resume keeps its settings; finished downloads are kept."
        pbottom 1; pbuttons 'Resume' 'Start over' 'Quit'
    else
        pbottom 1; pbuttons 'Start the check' 'Quit'
    fi
}

item_icon() {  # item_icon TAG: II (glyph) and IS (style)
    case $1 in ok) II=$G_OK; IS=$S_OK ;; fix) II=$G_FIX; IS=$S_WARN$S_B ;; fail) II=$G_FAIL; IS=$S_ERR ;;
        you) II=$G_YOU; IS=$S_ACCENT ;; *) II=$G_INFO; IS=$S_FAINT ;; esac
}

view_checks() {
    local role_name=''; role_index "$CHECKED_ROLE"; role_name=${ROLE_NAMES[RI]}
    if [ "$VIEW" = checking ]; then
        ptitle 'Check this machine' "Reading what this machine has for the $role_name role. Nothing changes in this step."
    else
        local fx=0 i; for i in "${!IT_TAG[@]}"; do case ${IT_TAG[i]} in fix|fail) fx=$((fx + 1)) ;; esac; done
        if ((fx)); then ptitle 'Check this machine' "For the $role_name role. $fx thing$( ((fx > 1)) && echo s) to fix; each one says what to do."
        else ptitle 'Check this machine' "Ready for the $role_name role. Nothing was changed." "$G_OK" "$S_OK"; fi
    fi
    local n=${#IT_TAG[@]} rows=$((CH - ${#PANEL[@]} - 3)) detail=0
    [ "$VIEW" = checks ] && rows=$((rows - 7))
    ((rows < 3)) && rows=3
    ((SEL >= n)) && SEL=$((n - 1)); ((SEL < 0)) && SEL=0
    ((SEL < SCROLL)) && SCROLL=$SEL
    ((SEL >= SCROLL + rows)) && SCROLL=$((SEL - rows + 1))
    [ "$VIEW" = checking ] && ((n > rows)) && SCROLL=$((n - rows))
    local i
    for ((i = SCROLL; i < n && i < SCROLL + rows; i++)); do
        item_icon "${IT_TAG[i]}"
        if [ "$VIEW" = checks ] && ((i == SEL)); then
            pl "$S_SELBG"; pa "$S_ACCENT" "$G_MARK"; pa "$IS" "$II"; pa '' '  '; pa "$S_B" "${IT_TEXT[i]}"; pe
        else
            pl; pa '' ' '; pa "$IS" "$II"; pa '' '  '
            if [ "${IT_TAG[i]}" = fix ] || [ "${IT_TAG[i]}" = fail ]; then pa '' "${IT_TEXT[i]}"; else pa "$S_MUTED" "${IT_TEXT[i]}"; fi
            pe
        fi
    done
    if [ "$VIEW" = checking ]; then
        pblank; pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_MUTED" "  ${ACTIVITY:-Checking}"; pe
        return
    fi
    ((n > rows)) && { pl; pa "$S_FAINT" "   $G_UP$G_DOWN $((n - rows)) more"; pe; }
    pblank
    pbottom 7
    # The selected line in full, with steps from the guidance table when it needs fixing.
    if ((n)); then
        local tag=${IT_TAG[SEL]} text=${IT_TEXT[SEL]}
        if [ "$tag" = fix ] || [ "$tag" = fail ]; then
            err_guidance "$text"
            if ((GUIDE >= 0)); then
                err_steps "$GUIDE"
                pl; pa "$S_B" 'What to do'; pe
                psteps 4 "${STEPS[@]}"
            else
                ppara '' "$text" 0
            fi
        else
            ppara "$S_MUTED" "$text"
        fi
    fi
    while ((${#PANEL[@]} > CH - 2)); do pdrop; done
    pbottom 1
    local -a b=('Continue')
    ((${#FIXABLE[@]})) && b=('Fix here' 'Continue')
    b+=('Check again')
    BTNS=("${b[@]}"); pbuttons "${b[@]}"
}

view_signin() {
    ptitle 'Sign in to Tailscale' 'This machine joins your tailnet. Tailscale is waiting for the sign-in; the setup goes on by itself afterwards.'
    pl; pa "$S_ACCENT" ' 1.  '; pa '' 'On any device (a phone works), open this address:'; pe
    pblank
    local url=${SIGNIN_URL:-waiting for Tailscale to print the address}
    local inner=$(( ${#url} + 4 )); ((inner > CW - 7)) && inner=$((CW - 7))
    ((${#url} > inner - 4)) && url="${url:0:inner-5}$G_ELL"
    local hz; printf -v hz '%*s' "$inner" ''; hz=${hz// /$G_H}
    local cbg=$S_CHIPBG
    pl; pa '' '     '; pa "$S_ACCENT" "$G_TL$hz$G_TR"; pe
    pl; pa '' '     '; pa "$S_ACCENT" "$G_V"; pa "$cbg" '  '
    if [ -n "$SIGNIN_URL" ]; then
        [ "$GLYPHS" = rich ] && _pl+=$'\e]8;;'"$SIGNIN_URL"$'\e\\'
        pa "$cbg$S_ACCENTT$S_B" "$url"
        [ "$GLYPHS" = rich ] && _pl+=$'\e]8;;\e\\'
    else pa "$cbg$S_MUTED" "$url"; fi
    spaces $((inner - 2 - ${#url})); pa "$cbg" "$SP"; pa "$S_ACCENT" "$G_V"; pe
    pl; pa '' '     '; pa "$S_ACCENT" "$G_BL$hz$G_BR"; pe
    pblank
    local -a rest=('Sign in with your own Tailscale account: the same tailnet the Workstation and your other Deep32 machines use.'
        'Approve this machine if your tailnet asks for approval. Then come back here.')
    local i l k
    for i in 0 1; do
        wrap $((CW - 5)) "${rest[i]}"; k=0
        for l in "${WRAPPED[@]}"; do pl; if ((k == 0)); then pa "$S_ACCENT" " $((i + 2)).  "; else pa '' '     '; fi; pa '' "$l"; pe; k=1; done
    done
    pbottom 1
    pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_MUTED" '  Waiting for the sign-in to finish'; pe
}

view_role() {
    ptitle 'Choose role' 'What should this machine be? You can run the setup again later on each of your other machines.'
    local -a desc=(
        'The GPU node: embeddings, reranker and PDF converter in Docker. Needs an NVIDIA card; pairs with the Workstation.'
        'Downloads and checks discs 1 and 2 and writes the VM settings for the Workstation. Creates no VM by itself.'
        'Connects Claude Code or Codex on this PC to your Workstation, with a pairing code.')
    local i why l
    for i in 0 1 2; do
        local sel=0; ((i == SEL)) && sel=1
        local bg=''; ((sel)) && bg=$S_SELBG
        why=''; [ "${ROLE_IDS[i]}" = "$GUESS" ] && why='suggested'
        pl "$bg"; if ((sel)); then pa "$S_ACCENT" "$G_MARK"; pa "$S_ACCENT" "$G_CUR"; else pa '' ' '; pa "$S_FAINT" "$G_PEND"; fi
        pa '' '  '; pa "$S_B" "${ROLE_NAMES[i]}"
        if [ -n "$why" ]; then pgap $((PW - 12)); pa "$S_OK" "$why"; fi
        pe "$bg"
        wrap $((CW - 4)) "${desc[i]}"
        for l in "${WRAPPED[@]}"; do pl "$bg"; pa '' '    '; if ((sel)); then pa '' "$l"; else pa "$S_MUTED" "$l"; fi; pe "$bg"; done
        pblank
    done
    if [ -n "$GUESS" ]; then
        local hint="The check suggests Forge: an NVIDIA card was found"
        [ "$GUESS" = forge ] && [ -n "$GUESS_GPU" ] && hint+=" (class $GUESS_GPU)"
        [ "$GUESS" = headend ] && hint='The check suggests Agent PC: Claude Code or Codex is installed here'
        [ "$GUESS" = workstation ] && hint='The check suggests Workstation host: no NVIDIA card was found'
        ppara "$S_FAINT" "$hint."
    fi
}

form_build() {  # the settings for the chosen role
    FORM_KEYS=(); FORM_LABEL=(); FORM_HELP=(); FORM_VALS=()
    case $ROLE in
        forge)
            FORM_KEYS=(F_NAME F_ROOT F_TZ F_USER F_CT)
            FORM_LABEL=('Forge name' 'Data folder' 'Time zone' 'Local user' 'Name in public logs')
            FORM_HELP=('Its Tailscale name. It must start with deep32- for the Workstation to offer it.'
                'Where the Forge keeps its data. Use the same folder for every later action.'
                'For the Forge services.'
                'The account the Forge services run as.'
                'HTTPS certificates publish this Forge name in public Certificate Transparency logs. Type yes to agree; the Forge needs HTTPS.') ;;
        workstation)
            FORM_KEYS=(W_HV); FORM_LABEL=('Hypervisor'); FORM_HELP=('kvm on Linux. The VM settings are written for it; no VM is created.') ;;
        headend)
            FORM_KEYS=(H_URL H_AGENT); FORM_LABEL=('Workstation address' 'Agent')
            FORM_HELP=('The https://...ts.net/mcp address the Workstation printed when you published it (on the Workstation, as root: d32-publish publish --accept-ct).'
                'claude, codex or both. auto picks what is installed here.') ;;
    esac
}

form_valid() {  # FORM_ERR: what is wrong with the form, or empty
    FORM_ERR=''
    case $ROLE in
        forge)
            [[ $F_NAME =~ ^deep32-[a-z0-9-]{1,32}$ ]] || FORM_ERR='The Forge name must start with deep32-, use only a-z, 0-9 and -, and be at most 39 characters.'
            [[ $F_NAME =~ (sk-[a-z0-9]|password|passwd|token|secret|cookie|private-?key|auth-?key|pair(ing)?-?code|[a-f0-9]{32}) ]] &&
                FORM_ERR='The installer refuses names that look like secrets (token, secret, password, key, cookie); choose another name.'
            [[ $F_ROOT == /* ]] || FORM_ERR='The data folder must be a full path, such as /opt/deep32.'
            [[ $F_TZ =~ ^[A-Za-z_]+(/[A-Za-z_+-]+)*$ ]] || FORM_ERR='The time zone looks wrong; use a name such as Europe/London or Etc/UTC.'
            [[ $F_USER =~ ^[a-z_][a-z0-9_-]{0,30}$ ]] && [ "$F_USER" != root ] || FORM_ERR='The local user must be a plain account name, not root.'
            [ "$F_CT" = yes ] || FORM_ERR='The Forge publishes over HTTPS; type yes to agree that its name appears in public logs.' ;;
        workstation) [[ $W_HV =~ ^(kvm|hyper-v)$ ]] || FORM_ERR='Choose kvm or hyper-v.'
            [ "$(uname -s 2>/dev/null)" = Darwin ] && ((opt_demo == 0)) && FORM_ERR='A Mac cannot host the Workstation VM (it needs KVM on Linux or Hyper-V on Windows); use another PC.' ;;
        headend)
            [[ $H_URL =~ ^https://.+/mcp$ ]] || FORM_ERR='The address must start with https:// and end with /mcp.'
            [[ $H_AGENT =~ ^(auto|claude|codex|both)$ ]] || FORM_ERR='Agent: auto, claude, codex or both.' ;;
    esac
}

view_form() {
    role_index "$ROLE"
    local ftitle="${ROLE_NAMES[RI]} settings"; [ "$VIEW" = vmform ] && ftitle=$FORM_TITLE
    ptitle "$ftitle" 'Check these, then continue. Up and down choose a field; type to change it.'
    local i key val lw=22
    for i in "${!FORM_KEYS[@]}"; do
        key=${FORM_KEYS[i]}; val=${!key}
        local sel=0; ((i == SEL)) && sel=1
        local bg=''; ((sel)) && bg=$S_SELBG
        pl "$bg"; ((sel)) && pa "$S_ACCENT" "$G_MARK" || pa '' ' '
        pa "$( ((sel)) && echo "$S_B" || echo "$S_MUTED")" " ${FORM_LABEL[i]}"; pgap $((lw + 2))
        pa "$S_CHIPBG" ' '; pa "$S_CHIPBG$( ((sel)) && echo "$S_B")" "$val"; ((sel)) && pa "$S_CHIPBG$S_ACCENT" "$G_CURSOR"; pa "$S_CHIPBG" ' '
        pe "$bg"
        if ((sel)); then
            wrap $((CW - lw - 1)) "${FORM_HELP[i]//\`/}"
            local l; for l in "${WRAPPED[@]}"; do pl; pgap $((lw + 3)); pa "$S_FAINT" "$l"; pe; done
        fi
    done
    pblank
    if [ "$VIEW" = vmform ]; then vm_form_valid; else form_valid; fi
    [ -n "$FORM_ERR" ] && ppara "$S_WARN" "$FORM_ERR"
    pbottom 1; BTNS=('Continue' 'Back'); pbuttons "${BTNS[@]}"
}

view_runintro() {
    role_index "$ROLE"
    local -a st=()
    case $ROLE in
        forge)
            ptitle 'Download and verify' 'What happens next on this machine, in order:'
            st=("Download the GPU pack for class ${GUESS_GPU:-of this card}: the shared core, the embedder and the ingestion image, into $(short_path "$WORKDIR")/deep32-downloads."
                'Check each image against the signed release. A stopped download resumes where it stopped.'
                'Show the installer'"'"'s plan: folders, ports and images. Nothing is installed until you type its digest.'
                "Install into $F_ROOT, start the Forge and show the pairing code.") ;;
        workstation)
            ptitle 'Download and verify' 'What happens next on this machine, in order:'
            st=("Download disc 1 (Workstation, about 1.9 GB) and disc 2 (content, about 19.6 GB) into $(short_path "$(cd "$WORKDIR/.." && pwd)")/deep32-downloads."
                'Check each disc against the signed release. A stopped download resumes where it stopped.'
                "Write the VM files and the commands that create it for $W_HV. No VM is created or started.") ;;
        headend)
            ptitle 'Connect your agent' 'What happens next on this PC, in order:'
            st=("Connect checks ${H_AGENT/auto/your agent} and the Workstation gateway at $H_URL."
                'On the Workstation, open Pairing and choose Headend. It shows a ten-character code that works once, for five minutes.'
                'The screen switches to plain text while Connect runs. When it asks, type the code; it is not shown as you type.'
                'Connect asks before it changes any existing agent files. Then this screen comes back.') ;;
    esac
    psteps 5 "${st[@]}"
    pblank
    [ "$ROLE" = forge ] && [ "$(id -u)" != 0 ] && ((opt_demo == 0)) && ppara "$S_MUTED" 'The Forge steps run as root, so sudo asks for your password once before they start.'
    pbottom 1
    case $ROLE in headend) BTNS=('Start Connect' 'Back') ;; *) BTNS=('Start' 'Back') ;; esac
    pbuttons "${BTNS[@]}"
}

view_running() {
    role_index "$ROLE"
    ptitle "Setting up the ${ROLE_NAMES[RI]}" 'Checking this machine again before anything changes.'
    local i n=${#IT_TAG[@]} rows=$((CH - ${#PANEL[@]} - 3)) start=0
    ((n > rows)) && start=$((n - rows))
    for ((i = start; i < n; i++)); do item_icon "${IT_TAG[i]}"; pl; pa '' ' '; pa "$IS" "$II"; pa '' '  '; pa "$S_MUTED" "${IT_TEXT[i]}"; pe; done
    pbottom 1; pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_MUTED" "  ${ACTIVITY:-Working}"; pe
}

view_download() {
    local what='Disc 3: the GPU pack images for this card, from the signed release.'
    [ "$ROLE" = workstation ] && what='Disc 1 (Workstation) and disc 2 (content), from the signed release.'
    ((CONSENT)) && what='Checking the downloaded images again before the install.'
    ptitle 'Download and verify' "$what"
    local i tot=0 have=0 nd=0 nf=${#DL_NAME[@]} known=1 bw=$((CW - 28)) st stl sts
    ((bw < 10)) && bw=10
    if ((nf == 0)) && [ "${DL_HIDDEN:-0}" = 1 ]; then
        ppara "$S_WARN" 'The download folder belongs to root and sudo cannot read it now, so the sizes and speed are not visible. The download goes on; each image shows here when it is verified.'
    elif ((nf == 0)); then
        pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_MUTED" '  Reading the signed release'; pe
    fi
    for i in "${!DL_NAME[@]}"; do
        tot=$((tot + DL_SIZE[i])); have=$((have + DL_HAVE[i])); ((DL_SIZE[i] == 0)) && known=0
        st=${DL_STATE[i]}
        case $st in done) stl="verified $G_OK"; sts=$S_OK; nd=$((nd + 1)) ;; check) stl='verifying'; sts=$S_ACCENT ;;
            down) stl='downloading'; sts=$S_ACCENT ;; stopped) stl="stopped $G_FAIL"; sts=$S_ERR ;; *) stl='waiting'; sts=$S_FAINT ;; esac
        local name=${DL_NAME[i]#deep32-"$VERSION_ID"-}
        local room=$((CW - ${#stl} - 3)); ((${#name} > room)) && name="${name:0:room-1}$G_ELL"
        pl; pa "$( [ "$st" = wait ] && echo "$S_MUTED" || echo "$S_B")" "$name"; pgap $((PW - 2 - ${#stl})); pa "$sts" "$stl"; pe
        permille "${DL_HAVE[i]}" "${DL_SIZE[i]}"
        bar_cells "$PM" "$bw"
        local bar_s=$S_ACCENT; [ "$st" = done ] && bar_s=$S_OK; [ "$st" = stopped ] && bar_s=$S_ERR
        local fill track
        printf -v fill '%*s' "$BAR_FULL" ''; fill=${fill// /$G_BAR}
        ((BAR_HALF)) && fill+=$G_BARH
        local tl=$((bw - BAR_FULL - BAR_HALF)); printf -v track '%*s' "$tl" ''; track=${track// /$G_TRACK}
        ((BAR_HALF)) && [ "$GLYPHS" = rich ] && track=${track:1} && track=" $track"
        fmt_bytes "${DL_HAVE[i]}"; local hv=$FMT; fmt_bytes "${DL_SIZE[i]}"; local sz=$FMT
        local info
        case $st in
            done) info=$sz ;; wait) info="$sz to get" ;; check) info="$sz, check in progress" ;;
            *) info="$hv of $sz  $G_DOT  $((PM / 10))%" ;;
        esac
        ((DL_SIZE[i] == 0)) && info="$hv so far"
        pl; pa "$bar_s" "$fill"; pa "$S_TRACK" "$track"; pa '' '  '; pa "$S_MUTED" "$info"; pe
        ((CH >= 20)) && pblank
    done
    # The whole transfer: size and files done and to go, the speed over the last 30 s, the time left once it
    # means something, and the time so far. What the tools do not report is said, not guessed. The pieces are
    # packed into as few lines as the panel is wide, so none is cut off in a small window.
    wiz_now; win_rate "${XS[*]}"
    local so_far='' dl_head='' dl_bold='' dl_p='' dl_line=''; local -a dl_parts=() dl_lines=()
    ((XF_START)) && { fmt_clock $(( (NOW_MS - XF_START) / 1000 )); so_far="$FMT so far"; }
    # the window is 30 s once that much has been seen; before that it says how long it really is
    local speed='measuring the speed' win_s=$(( (SPAN + 500) / 1000 )); ((win_s > 30)) && win_s=30
    ((RATE >= 0)) && { fmt_bytes "$RATE"; speed="$FMT/s over the last $win_s s"; }
    if ((nf > 0 && known)); then
        permille "$have" "$tot"; local pct=$((PM / 10)) remain=$((tot - have))
        eta_of "$remain" "$RATE" "$SPAN"
        fmt_bytes "$have"; local a=$FMT; fmt_bytes "$tot"; local b=$FMT; fmt_bytes "$remain"; local c=$FMT
        dl_head='Total  '; dl_bold="$a of $b"; dl_parts=("$dl_head$dl_bold" "$pct%" "$nd of $nf files done")
        if ((CONSENT)); then dl_parts+=('the check reports only finished images')
        else dl_parts+=("$c to go" "$speed"); ((ETA >= 0)) && { fmt_eta "$ETA"; dl_parts+=("$FMT"); }; fi
    elif ((nf > 0)); then
        fmt_bytes "$have"; dl_head='Downloaded  '; dl_bold=$FMT
        dl_parts=("$dl_head$dl_bold" "$nd of $nf files done" 'the total size is not reported yet' "$speed")
    fi
    ((nf > 0)) && [ -n "$so_far" ] && dl_parts+=("$so_far")
    for dl_p in "${dl_parts[@]}"; do
        if [ -z "$dl_line" ]; then dl_line=$dl_p
        elif (( ${#dl_line} + 5 + ${#dl_p} <= CW )); then dl_line+="  $G_DOT  $dl_p"
        else dl_lines+=("$dl_line"); dl_line=$dl_p; fi
    done
    [ -n "$dl_line" ] && dl_lines+=("$dl_line")
    ((${#dl_lines[@]})) || dl_lines=(' ' ' ')
    pbottom $(( ${#dl_lines[@]} + 1 ))
    for dl_p in "${!dl_lines[@]}"; do
        dl_line=${dl_lines[dl_p]}
        if ((dl_p == 0)) && [ -n "$dl_head" ]; then
            pl; pa "$S_MUTED" "$dl_head"; pa "$S_B" "$dl_bold"; pa "$S_MUTED" "${dl_line:$(( ${#dl_head} + ${#dl_bold} ))}"; pe
        else pl; pa "$S_MUTED" "$dl_line"; pe; fi
    done
    pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_FAINT" "  ${LAST_RAW:-${ACTIVITY:-Working}}"; pe
}

view_plan() {
    ptitle 'Install' "The Forge installer's plan. Read it, then type its digest to install."
    local box=$((CH - ${#PANEL[@]} - 10)); ((box < 3)) && box=3
    local -a rows=()
    local l k v ind
    for l in "${PLAN[@]}"; do
        [[ $l =~ ^(\ *)\"([^\"]+)\":\ (.*)$ ]] || { [[ $l =~ ^\ +\"([^\"]+)\",?$ ]] && rows+=("      ${BASH_REMATCH[1]}"$'\t'); continue; }
        ind=${BASH_REMATCH[1]}; k=${BASH_REMATCH[2]}; v=${BASH_REMATCH[3]%,}; v=${v#\"}; v=${v%\"}
        case $k in digest|sha256|media_sha256) continue ;; esac
        { [ "$v" = '{' ] || [ "$v" = '[' ]; } && v=''
        rows+=("${ind:2}$k"$'\t'"$v")
    done
    ((SCROLL > ${#rows[@]} - box)) && SCROLL=$((${#rows[@]} - box)); ((SCROLL < 0)) && SCROLL=0
    bx_top "$S_BORDER" 'plan'
    local i shown=0 kw=26 max
    for ((i = SCROLL; i < ${#rows[@]} && shown < box; i++)); do
        k=${rows[i]%%$'\t'*}; v=${rows[i]#*$'\t'}
        max=$((CW - kw - 5)); ((${#v} > max)) && v="${v:0:max-1}$G_ELL"
        bx_open "$S_BORDER"; pa "$S_MUTED" "$k"; pgap $((kw + 2)); pa '' "$v"; bx_close
        shown=$((shown + 1))
    done
    local more=$(( ${#rows[@]} - SCROLL - shown )) tail=''
    ((more > 0)) && tail=" $G_DOWN $more more, PgDn "
    bx_bot "$S_BORDER" "$tail"
    pblank
    ppara '' "Installing writes to $F_ROOT, loads the Docker images and starts the Forge services. To agree, type this digest:"
    local g1='' g2='' j
    for ((j = 0; j < 32; j += 4)); do g1+="${PLAN_DIGEST:j:4} "; done
    for ((j = 32; j < 64; j += 4)); do g2+="${PLAN_DIGEST:j:4} "; done
    pl; pa '' '   '; pa "$S_CHIPBG$S_ACCENTT$S_B" " ${g1% } "; pe
    pl; pa '' '   '; pa "$S_CHIPBG$S_ACCENTT$S_B" " ${g2% } "; pe
    local t=$TYPED out=0
    pl; pa "$S_ACCENT" ' > '
    for ((j = 0; j < ${#t}; j++)); do
        ((j == 32)) && { pe; pl; pa '' '   '; }
        ((j > 0 && j % 4 == 0 && j != 32)) && pa '' ' '
        if [ "${t:j:1}" = "${PLAN_DIGEST:j:1}" ]; then pa "$S_OK" "${t:j:1}"; else pa "$S_ERR$S_U" "${t:j:1}"; out=1; fi
    done
    ((${#t} < 64)) && pa "$S_ACCENT" "$G_CURSOR"; pe
    ((${#t} <= 32)) && pblank
    pbottom 1
    if [ "$TYPED" = "$PLAN_DIGEST" ]; then BTNS=('Install' 'Back'); pbuttons "${BTNS[@]}"
    elif ((out)); then pline "$S_ERR" 'A character does not match; Backspace fixes it.'
    else pline "$S_FAINT" "$(( 64 - ${#TYPED} )) characters to go. Spaces are fine; pasting works."; fi
}

install_phase() {  # install_phase LINE: how far the install has come, from the installer's own lines
    [ "$RUN_KIND" = role ] || return 0
    local n=-1
    case $ROLE:$1 in
        forge:'Checking read-only prerequisites'*) ((CONSENT)) && n=1 ;;
        forge:'Mounting '*) n=2 ;;
        forge:'Running the disc3 Docker form'|forge:'Waiting for the verified disc3 loader'*) n=3 ;;
        forge:'The verified disc3 loader returned successfully'*|forge:'Disc3 loader returned'*) n=4 ;;
        forge:'[ok] Forge installed and running') n=5 ;;
        forge:'[ok] door answers'*) n=6 ;;
        workstation:'VM artefacts written'*) n=2 ;;
    esac
    ((n > PHASE)) && { PHASE=$n; wiz_now; PHASE_AT=$NOW_MS; }
    return 0
}

view_install() {
    role_index "$ROLE"
    if [ "$VIEW" = pairwait ]; then ptitle 'Pair' 'The Forge is installed. Its pairing window opens in a moment.'
    elif [ "$ROLE" = workstation ]; then ptitle 'Install' 'Writing the VM files for the Workstation.'
    else ptitle 'Install' 'Installing the Forge from the checked images. This takes a few minutes.'; fi
    local -a ph=()
    case $ROLE in
        forge) ph=('Plan accepted' 'This host checked again' 'Images mounted read-only' 'Forge loader running'
            'Settings handed over' 'Forge started; its doors answer with a token request') ;;
        *) ph=('Disc 1 checked again' 'VM files and commands written') ;;
    esac
    local i cur=$PHASE; [ "$ROLE" = forge ] && ((cur < 1)) && cur=1
    wiz_now; fmt_clock $(( (NOW_MS - PHASE_AT) / 1000 )); local spent=$FMT
    for ((i = 0; i < ${#ph[@]}; i++)); do
        pl; pa '' ' '
        if ((i < cur)); then pa "$S_OK" "$G_OK"; pa '' '  '; pa "$S_MUTED" "${ph[i]}"
        elif ((i == cur)); then pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa '' '  '; pa "$S_B" "${ph[i]}"; pa "$S_MUTED" "  $G_DOT  $spent so far"
        else pa "$S_FAINT" "$G_PEND"; pa '' '  '; pa "$S_FAINT" "${ph[i]}"; fi
        pe
    done
    ppara "$S_FAINT" 'The installer reports no progress inside these steps, so the wizard shows the time so far and no percentage or time left.'
    pblank
    pl; pa "$S_FAINT" 'What the installer says'; pe
    local r
    local from=$(( ${#RECENT[@]} > 4 ? ${#RECENT[@]} - 4 : 0 ))
    for r in "${RECENT[@]:from}"; do pl; pa "$S_FAINT" "  ${r//$'\t'/    }"; pe; done
    pbottom 1; pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_MUTED" "  ${ACTIVITY:-Working}"; pe
}

view_pair() {
    ptitle 'Pair' 'The Forge waits for the Workstation. The code works once, for five minutes.'
    wiz_now; local left=$(( (PAIR_UNTIL - NOW_MS) / 1000 ))
    local code="${PAIR_CODE:0:5}  $G_DOT  ${PAIR_CODE:5:5}" spaced='' j
    for ((j = 0; j < ${#code}; j++)); do spaced+="${code:j:1} "; done; spaced=${spaced% }
    ((${#spaced} + 13 > CW)) && spaced=$code
    local inner=$(( ${#spaced} + 6 )) hz; printf -v hz '%*s' "$inner" ''; hz=${hz// /$G_H}
    pl; pa '' '   '; pa "$S_ACCENT" "$G_TL$hz$G_TR"; pe
    pl; pa '' '   '; pa "$S_ACCENT" "$G_V"; pa "$S_CHIPBG" '   '; pa "$S_CHIPBG$S_B$S_ACCENTT" "$spaced"; pa "$S_CHIPBG" '   '; pa "$S_ACCENT" "$G_V"; pe
    pl; pa '' '   '; pa "$S_ACCENT" "$G_BL$hz$G_BR"; pe
    pl; pa '' '    '
    if ((left > 0)); then fmt_clock "$left"; pa "$( ((left < 60)) && echo "$S_WARN" || echo "$S_MUTED")" "Valid for $FMT more"
    else pa "$S_ERR" 'This code has expired. Choose New code; it runs the Forge setup again.'; fi
    pe
    pblank
    local peer=${PAIR_PEER:-this Forge}
    psteps 4 'On the Workstation, open Pairing and choose Forge. From its terminal: `doas /usr/local/d32/sbin/d32-pairdesk -forge`' \
        "Pick $peer." \
        'Type the code above. Spaces and the dot between the groups are fine.' \
        'Wait until the Workstation says the pairing is saved, then choose Paired here.'
    pbottom 1
    if ((left > 0)); then BTNS=('Paired' 'New code (runs the setup again)'); else BTNS=('New code (runs the setup again)' 'Paired'); fi
    pbuttons "${BTNS[@]}"
}

pcmd_inline() {  # pcmd_inline TEXT: append TEXT to the open line, `commands` in the accent colour
    local rest=$1 seg incode=${PCMD_IN:-0}
    while [ -n "$rest" ]; do
        if [[ $rest == *'`'* ]]; then
            seg=${rest%%'`'*}; rest=${rest#*'`'}
            if ((incode)); then pa "$S_ACCENTT" "$seg"; else pa '' "$seg"; fi
            incode=$((1 - incode))
        else if ((incode)); then pa "$S_ACCENTT" "$rest"; else pa '' "$rest"; fi; rest=''; fi
    done
    PCMD_IN=$incode
}

view_fail() {
    ptitle "$FAIL_TITLE" '' "$G_FAIL" "$S_ERR"; pdrop
    pl; pa "$S_B" 'What the tools said'; pe
    local i shown=0 raw
    for ((i = 0; i < ${#FAIL_LINES[@]} && shown < 3; i++)); do
        raw=${FAIL_LINES[i]}; [ -n "$raw" ] || continue
        [[ $raw == *' exit '* && $raw != *' '*' '*' '* ]] && continue
        pl "$S_ERRBG"; pa "$S_ERR" "$G_QUOTE "; pa '' "$raw"; pe; shown=$((shown + 1))
    done
    local sline='' st name
    for st in $STATUSES; do
        [[ $st == *:0 ]] && continue
        name=${st%%:*}; case $name in setup|get|inventory) name="deep32-$name" ;; esac
        sline+="$name exit ${st##*:}   "
    done
    pl "$S_ERRBG"; pa "$S_ERR" "$G_QUOTE "; pa "$S_MUTED" "${sline%   }"; pe
    pblank
    if ((${#FAIL_ROWS[@]})); then
        local r=${FAIL_ROWS[0]}
        pl; pa "$S_B" 'What it means'; pe
        ppara '' "${ERR_MEAN[r]}"
        pblank
        pl; pa "$S_B" 'What to do'; pe
        err_steps "$r"; psteps 5 "${STEPS[@]}"
        if ((${#FAIL_ROWS[@]} > 1)) && [ "${ERR_KIND[${FAIL_ROWS[1]}]}" != exit ] && [ "${ERR_KEY[${FAIL_ROWS[1]}]}" != . ]; then
            pblank; ppara "$S_FAINT" "Also reported: ${ERR_MEAN[${FAIL_ROWS[1]}]}"
        fi
    fi
    while ((${#PANEL[@]} > CH - 3)); do pdrop; done
    pbottom 2
    pl; pa "$S_FAINT" 'Full log  '; pa "$S_MUTED" "$(short_path "$LOG_FILE")"; pe
    BTNS=('Try again' 'Back' 'Show the log'); pbuttons "${BTNS[@]}"
}

view_done() {
    role_index "$ROLE"
    local head
    case $ROLE in forge) head='This machine is now the Forge' ;; workstation) head='The Workstation files are ready' ;; headend) head='Your agent is connected' ;; esac
    if [ -n "$SOFT_DONE" ]; then ptitle 'Done' "$SOFT_DONE" "$G_OK" "$S_OK"
    else ptitle "$head" '' "$G_OK" "$S_OK"; pdrop; fi
    local -a did=()
    case $ROLE in
        forge) did=('checked' "${#DL_NAME[@]} images verified" 'installed' "$( ((PAIRED)) && echo paired || echo 'code shown')") ;;
        workstation) did=('checked' 'discs 1 and 2 verified' 'VM files written'); ((VM_DONE)) && did+=('VM guide done') ;;
        headend) did=('checked' 'agent paired' 'eleven tools checked') ;;
    esac
    local d; pl; for d in "${did[@]}"; do pa "$S_OK" "$G_OK "; pa "$S_MUTED" "$d"; pa '' '  '; done; pe
    pblank
    plan_now
    local steps=0 it
    for it in "${NEXT[@]}"; do [[ $it == note\|* ]] || steps=$((steps + 1)); done
    pl; pa "$S_B" "What's next"
    ((steps)) && pa "$S_MUTED" '  in this order, on the machine named at the left'
    pe
    ((steps)) || ppara '' 'Nothing else is needed: every part you listed is set up.'
    # The steps go into their own list, so a long one scrolls inside the panel and never pushes the buttons off.
    local -a head_lines=("${PANEL[@]}") body=()
    PANEL=()
    local n=0 kind where text cmds role
    for it in "${NEXT[@]}"; do
        IFS='|' read -r kind where text cmds role <<<"$it"
        local cmdtext=''; [ -n "$cmds" ] && cmdtext=" \`${cmds//^/\` \`}\`"
        case $kind in
            offer) n=$((n + 1)); pstep "$n" "$where" 20 "$text"; pl; pgap 22; pa "$S_BTNBG$S_BTNFG$S_B" ' N '; pa "$S_ACCENT" "  press N to do this now, here"; pe ;;
            note) pstep "$G_DOT" "$where" 20 "$text$cmdtext" ;;
            *) n=$((n + 1)); pstep "$n" "$where" 20 "$text$cmdtext" ;;
        esac
    done
    body=("${PANEL[@]}")
    PANEL=("${head_lines[@]}")
    local room=$((CH - ${#PANEL[@]} - 3)) total=${#body[@]}
    ((room < 3)) && room=3
    ((SCROLL > total - room)) && SCROLL=$((total - room)); ((SCROLL < 0)) && SCROLL=0
    local i; for ((i = SCROLL; i < total && i < SCROLL + room; i++)); do PANEL+=("${body[i]}"); done
    pbottom 2
    local more=$((total - SCROLL - room)) tail=''
    ((more > 0)) && tail="$G_DOWN $more more lines   "
    pl; pa "$S_FAINT" "$tail"; pa "$S_FAINT" 'Saved with every command: '; pa "$S_MUTED" "$(short_path "$NEXT_FILE")"; pe
    if [ -n "$NEXT_OFFER" ]; then role_index "$NEXT_OFFER"; BTNS=("Set up the ${ROLE_NAMES[RI]} here" 'Close')
    else BTNS=('Close'); fi
    pbuttons "${BTNS[@]}"
}

part_of() { case $1 in workstation) echo ws ;; headend) echo agent ;; *) echo "$1" ;; esac; }
role_of() { case $1 in ws) echo workstation ;; agent) echo headend ;; *) echo "$1" ;; esac; }

caps_detect() {  # what this machine could run: CAP_OS, CAP_SHELL, CAP_HV (a hypervisor for the VM), CAP_AGENT; the GPU class comes from the guess
    CAP_SHELL=bash
    case ${DEEP32_WIZARD_OS:-$(uname -s 2>/dev/null)} in linux|Linux) CAP_OS=linux ;; macos|Darwin) CAP_OS=macos ;; windows|MINGW*|MSYS*|CYGWIN*) CAP_OS=windows ;; *) CAP_OS=other ;; esac
    if ((opt_demo)); then CAP_HV=${GUESS_HV:-no}; CAP_AGENT=${GUESS_AGENT:-no}; return; fi
    CAP_HV=no; [ "$CAP_OS" = linux ] && [ -e /dev/kvm ] && CAP_HV=yes
    CAP_AGENT=no; { command -v claude || command -v codex; } >/dev/null 2>&1 && CAP_AGENT=yes
}

can_run() {  # can_run ROLE: this wizard can set ROLE up on this machine, by deep32-setup's own role and OS rules
    local here="$CAP_OS:$CAP_SHELL" native=0
    { [ "$here" = linux:bash ] || [ "$here" = windows:ps ]; } && native=1
    case $1 in
        forge) [ -n "$GUESS_GPU" ] && [ "$here" = linux:bash ] ;;   # deep32-setup.sh forge is Linux only; the alpha.2 Windows Forge is by hand
        workstation) [ "$CAP_HV" = yes ] && ((native)) ;;    # KVM on Linux, Hyper-V on Windows; never macOS
        headend) [ "$CAP_AGENT" = yes ] && { ((native)) || [ "$here" = macos:bash ]; } ;;
        *) return 1 ;;
    esac
}

parts_defaults() {  # first answers for Your machines, from the guess and what this machine can run
    [ -n "${PART[forge]:-}" ] && return 0
    if can_run forge; then PART[forge]=here; else PART[forge]=unknown; fi
    if [ "$GUESS" = workstation ] && can_run workstation; then PART[ws]=here; else PART[ws]=other; fi
    if can_run headend; then PART[agent]=here; else PART[agent]=other; fi
    [ -n "$ROLE" ] && PART[$(part_of "$ROLE")]=here
    return 0
}

part_label() { case $1 in here) PLT='Set it up on this machine' ;; other) PLT='Set it up on another machine' ;; done) PLT='Already set up' ;; none) PLT='Not using it' ;; *) PLT="I don't know yet" ;; esac; }

part_cycle() {  # part_cycle left|right: the next answer for the selected part
    local key=${PART_KEYS[SEL]} i n
    local -a vals=(here other done unknown); [ "$key" != ws ] && vals=(here other done none unknown)
    n=${#vals[@]}
    for ((i = 0; i < n; i++)); do [ "${vals[i]}" = "${PART[$key]}" ] && break; done
    if [ "$1" = right ]; then i=$(( (i + 1) % n )); else i=$(( (i - 1 + n) % n )); fi
    PART[$key]=${vals[i]}
}

parts_done() {  # Your machines answered: choose the role to set up here first, in journey order
    local key
    for key in "${PART_KEYS[@]}"; do
        if [ "${PART[$key]}" = here ] && can_run "$(role_of "$key")"; then ROLE=$(role_of "$key"); break; fi
    done
    [ -n "$ROLE" ] || ROLE=$GUESS
    case ${opt_role:-} in forge|workstation|headend) ROLE=$opt_role ;; esac   # a role named on the command line wins
    VIEW=role; role_index "$ROLE"; SEL=$RI; FOCUS=0
}

view_parts() {
    ptitle 'Your machines' 'Which parts do you have, and where? The answers decide what this run sets up here and what the wizard tells you to do on the other machines.'
    local -a hint=('The Workstation runs as a VM on a Windows PC with Hyper-V or a Linux PC with KVM.'
        'The Forge needs an NVIDIA card. Without one, search works on keywords only.'
        'The computer where you use Claude Code or Codex.')
    local i key
    for i in 0 1 2; do
        key=${PART_KEYS[i]}; part_label "${PART[$key]}"
        local sel=0 bg=''; ((i == SEL)) && { sel=1; bg=$S_SELBG; }
        pl "$bg"; if ((sel)); then pa "$S_ACCENT" "$G_MARK"; else pa '' ' '; fi
        pa "$S_B" " ${PART_NAMES[i]}"; pgap 22
        if ((sel)); then pa "$S_ACCENT" '< '; pa "$S_CHIPBG$S_B" " $PLT "; pa "$S_ACCENT" ' >'; else pa "$S_MUTED" "  $PLT"; fi
        pe
        if ((sel)); then
            wrap $((CW - 21)) "${hint[i]}"; local l; for l in "${WRAPPED[@]}"; do pl; pgap 23; pa "$S_FAINT" "$l"; pe; done
            if [ "${PART[$key]}" = here ] && ! can_run "$(role_of "$key")"; then
                local why='this machine cannot run it'
                case $key in
                    forge) why='this machine cannot run the Forge: it needs an NVIDIA card, on Linux (or Windows with the PowerShell wizard)' ;;
                    ws) why='this machine has no hypervisor for the Workstation VM (KVM on Linux, Hyper-V on Windows)' ;;
                    agent) why='Claude Code or Codex is not installed here yet' ;;
                esac
                wrap $((CW - 21)) "Note: $why."; for l in "${WRAPPED[@]}"; do pl; pgap 23; pa "$S_WARN" "$l"; pe; done
            fi
        fi
        pblank
    done
    pbottom 1; BTNS=('Continue' 'Back'); pbuttons "${BTNS[@]}"
}

next_plan() {  # NEXT: what is left, in INSTALL.md's order (Workstation, its import, Forge and its pairing, publication,
               # the agent PC and its pairing, then the optional relay), each as KIND|WHERE|TEXT|CMD^CMD|ROLE.
               # Inputs: NP_ROLE (just finished), PART[ws forge agent], NP_PAIRED, NP_PEER, NP_VM, WS_HERE_VM (the VM files
               # this session wrote before going on to another part here), and can_run.
    NEXT=(); NEXT_OFFER=''
    local r=$NP_ROLE ws=${PART[ws]:-unknown} fg=${PART[forge]:-unknown} ag=${PART[agent]:-unknown}
    case $r in workstation) ws=done ;; forge) fg=done ;; headend) ag=done ;; esac
    local clone_sh='git clone https://github.com/hellobpdu/deep32.git && cd deep32 && bash install/deep32-wizard.sh'
    local clone_ps='git clone https://github.com/hellobpdu/deep32.git; cd deep32; powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1'
    local get="$clone_sh^$clone_ps"
    # 1. The Workstation, which everything else pairs with.
    local vm=${NP_VM:-}; [ "$r" = workstation ] || vm=${WS_HERE_VM:-}
    if { [ "$r" = workstation ] || [ -n "${WS_HERE_VM:-}" ]; } && [ "${NP_VMDONE:-0}" = 1 ]; then
        [ -n "${NP_VMSKIP:-}" ] && NEXT+=("step|Workstation VM|Do the VM guide's steps you skipped ($NP_VMSKIP). Each command is in its steps file in the VM folder, or run the guide again:|bash install/deep32-wizard.sh --vm-guide^powershell -NoProfile -ExecutionPolicy Bypass -File install\\deep32-wizard.ps1 -VmGuide|")
    elif [ "$r" = workstation ] || [ -n "${WS_HERE_VM:-}" ]; then
        NEXT+=("step|This PC|Create the Workstation VM: read this file, change its 160 GB disk to 200 GB (the New-VHD or qemu-img line), then run its commands (Linux as root, Windows in PowerShell as administrator).|${vm:-the VM folder}/commands.txt|")
        NEXT+=("step|Workstation VM|Boot it from disc 1 and answer the installer. Install to the empty 200 GB disk, not the installer disk.||")
        NEXT+=("step|Workstation|At first boot set the passwords and sign in to Tailscale with the address it shows.||")
        NEXT+=("step|Workstation|Give it disc 2 in one of three ways: as a read-only CD drive (attach the disc 2 image to the VM); on a USB stick of 32 GB or more, with the image written to it by balenaEtcher or dd; or over the network, with Get Disc 2 over the Network in First Start, from a PC that sends it or from the release mirror. The import starts by itself: search works once the knowledge base is in, and the Library carries on in the background. From a terminal you can check it, and resume it if it stopped:|/usr/local/d32/libexec/d32-import-report^doas /usr/local/d32/sbin/d32-import-resume|")
        NEXT+=('note|This PC|To send disc 2 from this PC over your tailnet, press S on this screen, or run it later (Linux as root, Windows in PowerShell as administrator):|bash install/deep32-wizard.sh --send-disc2^powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1 -SendDisc2|')
        NEXT+=("note|This PC|The VM guide shows these steps for Proxmox VE, VMware, VirtualBox, Hyper-V, KVM or another machine, and checks each one:|bash install/deep32-wizard.sh --vm-guide^powershell -NoProfile -ExecutionPolicy Bypass -File install\\deep32-wizard.ps1 -VmGuide|")
    elif [ "$ws" = here ] && can_run workstation; then
        NEXT+=("offer|This PC|Set up the Workstation host here: download discs 1 and 2 and write the VM files.||workstation")
    elif [ "$ws" != done ]; then
        NEXT+=("step|Workstation PC|On the PC that will host the Workstation VM (Windows with Hyper-V, or Linux with KVM), get Deep32 and run the wizard; choose Workstation host:|$get|")
    fi
    if [ "$r" != workstation ] && [ "$ws" != done ]; then
        NEXT+=("step|Workstation|Install it from disc 1, sign it in to Tailscale at first boot, and wait until its Import Report shows the knowledge base in use before you pair anything with it. The Library can still be importing.||")
    fi
    # 2. The Forge, paired once the knowledge base is in use.
    local pairf="Workstation|Once the knowledge base is in use, pair the Forge: open Pairing, choose Forge, pick ${NP_PEER:-the Forge} and type the code the Forge shows. Or from a terminal on the Workstation:|doas /usr/local/d32/sbin/d32-pairdesk -forge|"
    local gpuon='Workstation|Pairing turns the Forge models on. As root on the Workstation console, check that both show verified:|/usr/local/d32/sbin/d32-gpu-ext status|'
    case $fg in
        done) if [ "$r" = forge ]; then [ "${NP_PAIRED:-0}" = 1 ] || NEXT+=("step|$pairf"); NEXT+=("step|$gpuon"); fi ;;
        none) NEXT+=("note|Forge|No Forge: search works on keywords only. A Forge can be added later.||") ;;
        *) if [ "$fg" = here ] && can_run forge; then NEXT+=("offer|This PC|Set up the Forge here: download, check and install disc 3, then show its pairing code.||forge")
           else NEXT+=("step|GPU PC|On the PC with the NVIDIA card (Linux with Docker, or Windows with Docker Desktop), get Deep32 and run the wizard; choose Forge:|$get|"); fi
           NEXT+=("step|$pairf" "step|$gpuon") ;;
    esac
    # 3. Publication comes before Connect: Connect needs the address it prints.
    if [ "$ag" != none ] && [ "$ag" != done ]; then
        NEXT+=("step|Workstation|As root on the Workstation console, publish the gateway for agents (HTTPS names appear in public certificate logs). It prints the address the agent PC needs:|/usr/local/d32/sbin/d32-publish publish --accept-ct|")
        if [ "$ag" = here ] && can_run headend; then NEXT+=("offer|This PC|Set up the agent PC here: Connect pairs Claude Code or Codex with the Workstation.||headend")
        elif [ "$ag" = here ]; then NEXT+=("step|This PC|Install Claude Code or Codex here and sign in to it, then run the wizard again and choose Agent PC.||")
        else NEXT+=("step|Agent PC|On the computer with Claude Code or Codex, get Deep32 and run the wizard; choose Agent PC and give it the address from publication:|$get|"); fi
        NEXT+=("step|Workstation|When Connect asks for its code, open Pairing and choose Headend, or from a terminal:|doas /usr/local/d32/sbin/d32-pairdesk -headend|")
    fi
    # 4. The Ask window relay is optional and pairs on its own.
    if [ "$ag" != none ]; then
        NEXT+=("note|Agent PC|Optional, for the Ask window: the relay (adapters/relay/README.md). Install it with --no-autostart and sign in, stop its service, then pair:|adapters/connect.sh --relay pair --relay-id INSTALL_ID --agent claude^powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay pair -RelayId INSTALL_ID -Agent claude|")
        NEXT+=("note|Workstation|At the same time, on the Workstation's text console (Ctrl+Alt+F1), run this and pick the agent PC. Then compare the ten words both sides show and type each side's words at the other side's prompt:|doas /usr/local/d32/sbin/d32-pairdesk -relay|")
    fi
    local it; for it in "${NEXT[@]}"; do [[ $it == offer\|* ]] && { NEXT_OFFER=${it##*|}; break; }; done
}

plan_now() {  # the plan for this screen, and the same list with every command in a text file to copy across
    NP_ROLE=$ROLE; NP_PAIRED=$PAIRED; NP_PEER=$PAIR_PEER; NP_VM=$(short_path "${VM_LINE:-}"); [ -n "$VM_LINE" ] || NP_VM=''
    NP_VMDONE=${VM_DONE:-0}; NP_VMSKIP=''; local s; for s in "${VM_SKIPPED[@]}"; do NP_VMSKIP+="${NP_VMSKIP:+; }$s"; done
    next_plan
    NEXT_FILE="$STATE_DIR/next-steps.txt"
    [ "${NEXT_WRITTEN:-}" = "$ROLE" ] && return 0
    NEXT_WRITTEN=$ROLE
    local it kind where text cmds role n=0 c
    {
        printf "Deep32: what's next after the %s setup on this machine (%s)\n\n" "$ROLE" "$(date '+%Y-%m-%d %H:%M')"
        for it in "${NEXT[@]}"; do
            IFS='|' read -r kind where text cmds role <<<"$it"
            if [ "$kind" = note ]; then printf -- '- %s: %s\n' "$where" "$text"; else n=$((n + 1)); printf '%d. %s: %s\n' "$n" "$where" "$text"; fi
            [ "$kind" = offer ] && printf '   (on this machine: run the wizard again and choose this role)\n'
            local IFS_OLD=$IFS; IFS='^'; for c in $cmds; do printf '     %s\n' "$c"; done; IFS=$IFS_OLD
            printf '\n'
        done
    } >"$NEXT_FILE" 2>/dev/null
}

continue_here() {  # N: set up the next part on this same machine, keeping what is already known
    PART[$(part_of "$ROLE")]=done
    if [ "$ROLE" = workstation ]; then WS_HERE_VM='the VM folder'; [ -n "${VM_LINE:-}" ] && WS_HERE_VM=$(short_path "$VM_LINE"); fi
    local next=$1
    ROLE=$next; SKIP_PARTS=1; NEXT_WRITTEN=''
    STEP_STATE=(current pending pending pending pending pending); STEP_NOTE=('' '' '' '' '' '')
    DL_NAME=(); DL_SIZE=(); DL_HAVE=(); DL_STATE=(); DL_RATE=(); DL_T=(); DL_B=(); DL_DIR=''
    CONSENT=0; DIGEST=''; PLAN=(); PLAN_DIGEST=''; PAIR_CODE=''; PAIR_PEER=''; PAIRED=0; SOFT_DONE=''; VM_LINE=''; SCROLL=0
    form_build
    check_for_role "$next"; start_checks look
}

view_connect() { view_runintro; }

xfer_sample() {  # twice a second while files move: the total so far, kept for the last 30 s
    wiz_now
    ((NOW_MS - XF_LAST < 500)) && return 0
    XF_LAST=$NOW_MS
    ((${#DL_NAME[@]})) || return 0
    local i have=0
    for i in "${!DL_NAME[@]}"; do have=$((have + DL_HAVE[i])); done
    ((XF_START)) || XF_START=$NOW_MS
    XS+=("$NOW_MS:$have")
    while ((${#XS[@]} > 2)) && ((${XS[0]%%:*} < NOW_MS - 31000)); do XS=("${XS[@]:1}"); done
}

ask_open() {  # a question opens with "Not now" chosen, and keys typed before it are not its answer
    OVERLAY=ask; FOCUS=1; ASK_PENDING=0
    if ((REAL_TTY)); then local junk; while IFS= read -rsn1 -t 0.01 junk; do :; done; fi
}

view_ask() {  # a question from deep32-setup, in plain words, over the current screen
    local expl yes='Yes'
    case $ASK_ID in
        tailscale-signin) yes='Yes, start the sign-in'
            expl='This runs `tailscale up` on this machine as root. Tailscale shows a sign-in address; the machine joins your tailnet once you sign in. Nothing else changes.' ;;
        tailscale-rename) yes='Yes, rename it'
            expl='This renames the machine on your tailnet with `tailscale set --hostname`. The Workstation only offers Forges whose name starts with deep32-.' ;;
        pairing-python) yes='Yes, install it'
            expl='This installs Python 3 for the pairing helper with your package manager, as root.' ;;
        *) expl='deep32-setup asks before it changes this machine.' ;;
    esac
    local keep=$(( ${#PANEL[@]} > 5 ? 5 : ${#PANEL[@]} ))
    PANEL=("${PANEL[@]:0:keep}")
    bx_top "$S_ACCENT" 'Before this step'
    BX_STYLE=$S_ACCENT; bx_text "$S_B" "$ASK_TEXT"
    bx_open "$S_ACCENT"; bx_close
    bx_text '' "$expl"
    bx_open "$S_ACCENT"; bx_close
    bx_buttons "$yes" 'Not now'
    bx_bot "$S_ACCENT"
}

view_quit() {
    local keep=$(( ${#PANEL[@]} > 5 ? 5 : ${#PANEL[@]} ))
    PANEL=("${PANEL[@]:0:keep}")
    local msg='Nothing more changes. Run the wizard again to pick up where it stopped; finished downloads are kept.'
    [ -n "$CHILD_PID" ] && msg='A step is running. Quitting stops it and everything it started; finished downloads and verified parts are kept, and the next run resumes.'
    bx_top "$S_WARN" 'Quit the setup?'
    BX_STYLE=$S_WARN; bx_text '' "$msg"
    bx_open "$S_WARN"; bx_close
    bx_buttons 'Stay' 'Quit'
    bx_bot "$S_WARN"
}

view_log() {
    pblank
    pl; pa "$S_B" 'Log'; pa "$S_MUTED" "   everything deep32-setup and its tools printed  $G_DOT  $(short_path "$LOG_FILE")"; pe
    pblank
    local rows=$((CH - 4)) n=${#LOG[@]} start i
    ((LOGSCROLL > n - rows)) && LOGSCROLL=$((n - rows)); ((LOGSCROLL < 0)) && LOGSCROLL=0
    start=$((n - rows - LOGSCROLL)); ((start < 0)) && start=0
    for ((i = start; i < n && i < start + rows; i++)); do
        local l=${LOG[i]} s=''
        case $l in '[ok]'*) s=$S_OK ;; '[fix]'*) s=$S_WARN ;; '[fail]'*) s=$S_ERR ;; '[you]'*) s=$S_ACCENT ;; '{"deep32":'*) s=$S_FAINT ;; '---'*) s=$S_FAINT ;; esac
        l=${l//$'\t'/    }; l=${l//$'\e'/}
        pl; pa "$s" "$l"; pe
    done
}

view_help() {
    pblank
    pl; pa "$S_B" 'Help'; pe
    pblank
    local -a k=(Enter 'continue, or press the chosen button'
        Esc 'go back one screen'
        "$G_UP $G_DOWN" 'choose a line, a role or a field'
        "Left Right Tab" 'choose a button'
        L 'show everything the setup printed (the log)'
        Q 'quit; the next run picks up where this one stopped'
        '?' 'this help')
    local i
    for ((i = 0; i < ${#k[@]}; i += 2)); do pl; pa "$S_ACCENT$S_B" "  ${k[i]}"; pgap 22; pa '' "${k[i+1]}"; pe; done
    pblank
    ppara "$S_MUTED" 'The wizard runs install/deep32-setup.sh and shows its steps. For the same plain tagged lines an agent reads, run the wizard with --plain, or run deep32-setup.sh itself. INSTALL.md describes every step.'
}

# ---------------------------------------------------------------------------------------------------------------
# 10b. The Workstation VM guide.
#
# After the Workstation host role, or straight away with --vm-guide, the wizard asks which hypervisor runs the
# Workstation VM and where, reads this PC's network, then shows one exact step at a time. Every hypervisor value,
# step, rule and fix comes from install/deep32-wizard-hv.tsv, which deep32-wizard.ps1 reads too. Enter checks a step:
# the hypervisor's own read-only query on this PC, the optional read-only Proxmox API token, or `tailscale status
# --json`; what the wizard cannot see, the person confirms. The wizard never creates, changes, starts or stops a VM.
# It writes only into the kit folder (steps-<hv>.txt, a VMware disk descriptor, a VirtualBox copy of the installer)
# and never writes to the signed image.

hv_split() {  # hv_split LINE: its TAB-separated fields into HF_F ("-" is an empty field)
    local l=$1 i; HF_F=()
    while [[ $l == *$'\t'* ]]; do HF_F+=("${l%%$'\t'*}"); l=${l#*$'\t'}; done
    HF_F+=("$l")
    for i in "${!HF_F[@]}"; do [ "${HF_F[i]}" = - ] && HF_F[i]=''; done
    return 0
}

hv_load() {  # install/deep32-wizard-hv.tsv into HV_* (hypervisors), HFD_* (settings), HS_* (steps), HR_* (rules), HT HFL HFX
    [ "${HV_LOADED:-0}" = 1 ] && return 0
    HV_LOADED=1
    HV_IDS=(); HV_NAMES=(); HV_TESTED=(); HV_HERE=(); HV_LOCAL=(); HV_TOOLS=(); HV_NETS=()
    HFD_HV=(); HFD_WHERE=(); HFD_KEY=(); HFD_LABEL=(); HFD_DEF=(); HFD_PAT=(); HFD_ERR=(); HFD_HELP=()
    HS_HV=(); HS_WHERE=(); HS_NET=(); HS_OS=(); HS_ID=(); HS_CHECK=(); HS_TITLE=(); HS_PLACE=(); HS_BODY=(); HS_LOOK=()
    HR_HV=(); HR_PHASE=(); HR_FACT=(); HR_OP=(); HR_VAL=(); HR_CODE=()
    HT=(); HFL=(); HFX=()
    local line f
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%$'\r'}
        [[ -z ${line//[[:space:]]/} || $line == '#'* ]] && continue
        hv_split "$line"; f=("${HF_F[@]}")
        case ${f[0]} in
            hv) HV_IDS+=("${f[1]}"); HV_NAMES+=("${f[2]}"); HV_TESTED+=("${f[3]}"); HV_HERE+=("${f[4]}"); HV_LOCAL+=("${f[5]}"); HV_TOOLS+=("${f[6]}"); HV_NETS+=("${f[7]}") ;;
            field) HFD_HV+=("${f[1]}"); HFD_WHERE+=("${f[2]}"); HFD_KEY+=("${f[3]}"); HFD_LABEL+=("${f[4]}"); HFD_DEF+=("${f[5]}")
                HFD_PAT+=("${f[6]}"); HFD_ERR+=("${f[7]}"); HFD_HELP+=("${f[8]}") ;;
            step) HS_HV+=("${f[1]}"); HS_WHERE+=("${f[2]}"); HS_NET+=("${f[3]}"); HS_OS+=("${f[4]}"); HS_ID+=("${f[5]}"); HS_CHECK+=("${f[6]}")
                HS_TITLE+=("${f[7]}"); HS_PLACE+=("${f[8]}"); HS_BODY+=("${f[9]}"); HS_LOOK+=("${f[10]}") ;;
            rule) HR_HV+=("${f[1]}"); HR_PHASE+=("${f[2]}"); HR_FACT+=("${f[3]}"); HR_OP+=("${f[4]}"); HR_VAL+=("${f[5]}"); HR_CODE+=("${f[6]}") ;;
            fact) HFL[${f[1]}]=${f[2]} ;;
            fix) HFX["${f[1]} ${f[2]}"]=${f[3]} ;;
            text) HT[${f[1]}]=${f[2]} ;;
        esac
    done <"$wiz_here/deep32-wizard-hv.tsv"
}

hv_index() { local i; for i in "${!HV_IDS[@]}"; do [ "${HV_IDS[i]}" = "$1" ] && { HI=$i; return 0; }; done; HI=-1; return 1; }

vm_os() {  # VM_OS: windows or unix, for the steps, the paths and the demo
    VM_OS=unix
    case ${DEEP32_WIZARD_OS:-$(uname -s 2>/dev/null)} in windows|MINGW*|MSYS*|CYGWIN*) VM_OS=windows ;; esac
    SEP=/; [ "$VM_OS" = windows ] && SEP='\'
    return 0
}

vm_base() { local p=$1; p=${p##*/}; BASE=${p##*\\}; }   # vm_base PATH: the last part, for / and \ paths

hv_detect() {  # HV_FOUND and HV_CMD: each hypervisor found on this PC (read-only: its commands on PATH)
    HV_FOUND=(); HV_CMD=()
    local i id t f c
    for i in "${!HV_IDS[@]}"; do
        id=${HV_IDS[i]}; f=no; c=''
        if [ -n "${DEEP32_WIZARD_HV_FOUND+x}" ]; then [[ " $DEEP32_WIZARD_HV_FOUND " == *" $id "* ]] && f=yes
        elif ((opt_demo)); then case "$VM_OS:$id" in unix:virtualbox|unix:kvm|windows:hyper-v|windows:virtualbox) f=yes ;; esac
        else
            for t in ${HV_TOOLS[i]//,/ }; do
                case $t in *'\'*|*'%'*|Get-*) continue ;; esac   # the PowerShell wizard's own ways to look
                c=$(command -v "$t" 2>/dev/null) && { f=yes; break; }
                c=''
            done
        fi
        HV_FOUND+=("$f"); HV_CMD+=("$c")
    done
}

kit_find() {  # kit_find [FOLDER]: the verified disc 1 image and deep32-get's VM folder, from a download or VM folder
    KIT_DIR=''; KIT_DL=''; KIT_IMAGE=''; KIT_SHA=''; KIT_BYTES=''; KIT_GETHV=''; KIT_COPY=''; KIT_DISC2=''; KIT_DISC2_OK=0
    if ((opt_demo)); then
        local dl='/home/you/deep32-downloads' hv=kvm
        [ "$VM_OS" = windows ] && { dl='C:\Users\you\deep32-downloads'; hv=hyper-v; }
        KIT_DL=$dl; KIT_GETHV=$hv; KIT_DIR="$dl${SEP}deep32-0.1.0-alpha.2-disc1-workstation-amd64-$hv"
        KIT_IMAGE="$dl${SEP}deep32-0.1.0-alpha.2-disc1-workstation-amd64.img"; KIT_BYTES=1876951040; KIT_SHA=${HT[demo-sha256]}
        KIT_COPY="$KIT_DIR${SEP}installer.img"; [ "$hv" = hyper-v ] && KIT_COPY="$KIT_DIR${SEP}installer.vhd"
        KIT_DISC2="$dl${SEP}deep32-0.1.0-alpha.2-disc2-content.iso"; KIT_DISC2_OK=1
        return 0
    fi
    local from=${1:-} d best='' first='' l
    [ -n "$from" ] || from=${VM_LINE:-$wiz_repo/../deep32-downloads}
    if [ -f "$from/recipe.json" ]; then best=$from
    else
        for d in "$from"/*-kvm "$from"/*-hyper-v; do
            [ -f "$d/recipe.json" ] || continue
            [ -n "$first" ] || first=$d
            [[ $d == *"-${VM_HV:-none}" ]] && best=$d
        done
        [ -n "$best" ] || best=$first
    fi
    [ -n "$best" ] || return 1
    while IFS= read -r l; do
        [[ $l =~ \"installer_sha256\":\ \"([0-9a-f]{64})\" ]] && KIT_SHA=${BASH_REMATCH[1]}
        [[ $l =~ \"installer_bytes\":\ ([0-9]{1,15}) ]] && KIT_BYTES=${BASH_REMATCH[1]}
        [[ $l =~ \"hypervisor\":\ \"(kvm|hyper-v)\" ]] && KIT_GETHV=${BASH_REMATCH[1]}
    done <"$best/recipe.json"
    KIT_DIR=$(cd -- "$best" && pwd -P) || return 1
    KIT_DL=${KIT_DIR%/*}
    local stem=${KIT_DIR##*/}; stem=${stem%-"$KIT_GETHV"}
    KIT_IMAGE="$KIT_DL/$stem.img"
    KIT_COPY="$KIT_DIR/installer.img"; [ "$KIT_GETHV" = hyper-v ] && KIT_COPY="$KIT_DIR/installer.vhd"
    KIT_DISC2="$KIT_DL/${stem/disc1-workstation-amd64/disc2-content}.iso"
    [ -f "$KIT_DISC2" ] && KIT_DISC2_OK=1
    [ -n "$KIT_SHA" ] && [ -n "$KIT_BYTES" ] && [ -f "$KIT_IMAGE" ]
}

vm_vars() {  # VF: the values the steps' {placeholders} take
    VF=()
    VF[image]=$KIT_IMAGE; vm_base "$KIT_IMAGE"; VF[imagename]=$BASE
    VF[sha256]=$KIT_SHA; VF[bytes]=$KIT_BYTES
    if num_ok "$KIT_BYTES"; then VF[sectors]=$((KIT_BYTES / 512)); VF[mib]=$((KIT_BYTES / 1048576)); fi
    VF[kit]=$KIT_DIR; VF[dl]=$KIT_DL; VF[copy]=$KIT_COPY; vm_base "$KIT_COPY"; VF[copyname]=$BASE
    VF[disc2]=$KIT_DISC2; vm_base "$KIT_DISC2"; VF[disc2name]=$BASE
    VF[gethv]=$KIT_GETHV; VF[sep]=$SEP
    VF[hvname]=${HV_NAMES[VHI]:-}; VF[net]=$VM_NET; VF[netlabel]=${HT[net-$VM_HV-$VM_NET]:-$VM_NET}
    local j v
    for j in "${!HFD_KEY[@]}"; do v="VMF_${HFD_KEY[j]}"; VF[${HFD_KEY[j]}]=${!v:-}; done
    vm_fill "${VF[netlabel]}"; VF[netlabel]=$FILLED
    return 0
}

vm_fill() {  # vm_fill TEXT: TEXT with every {name} from VF filled in, into FILLED
    local t=$1 k
    if [[ $t == *'{'* ]]; then for k in "${!VF[@]}"; do t=${t//"{$k}"/"${VF[$k]}"}; done; fi
    FILLED=$t
}

vm_steps_build() {  # the steps for this hypervisor, place, network and OS, filled in, into VS_*
    VS_ID=(); VS_CHECK=(); VS_TITLE=(); VS_PLACE=(); VS_BODY=(); VS_LOOK=(); VS_STATE=(); VS_RES=(); VS_CODE=()
    local j
    for j in "${!HS_HV[@]}"; do
        [ "${HS_HV[j]}" = "$VM_HV" ] || continue
        case ${HS_WHERE[j]} in ''|'*'|"$VM_WHERE") ;; *) continue ;; esac
        case ${HS_NET[j]} in ''|'*'|"$VM_NET") ;; *) continue ;; esac
        case ${HS_OS[j]} in ''|'*'|"$VM_OS") ;; *) continue ;; esac
        VS_ID+=("${HS_ID[j]}"); VS_CHECK+=("${HS_CHECK[j]}")
        vm_fill "${HS_TITLE[j]}"; VS_TITLE+=("$FILLED"); vm_fill "${HS_PLACE[j]}"; VS_PLACE+=("$FILLED")
        vm_fill "${HS_BODY[j]}"; VS_BODY+=("$FILLED"); vm_fill "${HS_LOOK[j]}"; VS_LOOK+=("$FILLED")
        VS_STATE+=(pending); VS_RES+=(''); VS_CODE+=('')
    done
    VM_I=0
}

vm_items() {  # vm_items BODY: the numbered items of a step body (separated by " | "), into ITEMS
    local b=$1; ITEMS=()
    while [ -n "$b" ]; do ITEMS+=("${b%%' | '*}"); [[ $b == *' | '* ]] && b=${b#*' | '} || b=''; done
}

vm_plain_item() {  # vm_plain_item PREFIX ITEM: one item as plain lines, each `command` on a line of its own
    local rest=$2 prose cmd first=1
    while [ -n "$rest" ]; do
        if [[ $rest == *'`'*'`'* ]]; then prose=${rest%%'`'*}; rest=${rest#*'`'}; cmd=${rest%%'`'*}; rest=${rest#*'`'}
        else prose=$rest; cmd=''; rest=''; fi
        while [[ $prose == [' ,.;:']* ]]; do prose=${prose:1}; done
        while [[ $prose == *' ' ]]; do prose=${prose% }; done
        if [ -n "$prose" ] && [[ $prose != [.,:\;] ]]; then
            if ((first)); then printf '%s%s\n' "$1" "$prose"; first=0; else printf '       %s\n' "$prose"; fi
        fi
        if [ -n "$cmd" ]; then ((first)) && { printf '%s\n' "${1%% }"; first=0; }; printf '         %s\n' "$cmd"; fi
    done
}

vm_steps_text() {  # every step as plain text: the steps file and the plain guide's summary use it
    local i k it
    printf 'Deep32 Workstation VM on %s (%s)\n' "${HV_NAMES[VHI]}" "$(vm_tested_word)"
    printf 'Disc 1 image: %s\n  sha256 %s, %s bytes\n' "$KIT_IMAGE" "$KIT_SHA" "$KIT_BYTES"
    printf 'The wizard creates, changes, starts and stops no VM; every command here is yours to run.\n\n'
    for i in "${!VS_ID[@]}"; do
        printf '%d. %s\n' $((i + 1)) "${VS_TITLE[i]}"
        [ -n "${VS_PLACE[i]}" ] && printf '   Where: %s\n' "${VS_PLACE[i]}"
        vm_items "${VS_BODY[i]}"; k=0
        for it in "${ITEMS[@]}"; do k=$((k + 1)); vm_plain_item "   $k) " "$it"; done
        [ -n "${VS_LOOK[i]}" ] && printf '   Look for: %s\n' "${VS_LOOK[i]}"
        printf '\n'
    done
}

vm_tested_word() { if [ "${HV_TESTED[VHI]:-}" = yes ]; then printf 'boot-tested'; else printf 'not boot-tested yet'; fi; }

vm_steps_write() {  # steps-<hv>.txt in the kit folder; the demo writes nothing
    VM_STEPS_FILE="$KIT_DIR${SEP}steps-$VM_HV.txt"
    ((opt_demo)) && return 0
    vm_steps_text >"$VM_STEPS_FILE" 2>/dev/null || VM_STEPS_FILE=''
    [ -n "$VM_STEPS_FILE" ] && wiz_log "--- vm guide: wrote $VM_STEPS_FILE"
    return 0
}

demo_lines() {  # demo_lines NAME: the recorded output vm-NAME-N (N counts the calls; the last one repeats) or vm-NAME
    local name=$1 n line inside=0 want='' k
    local -a skip=(0)
    DEMO_N[$name]=$(( ${DEMO_N[$name]:-0} + 1 )); n=${DEMO_N[$name]}
    [ -n "${DEMO_NAMES:-}" ] || DEMO_NAMES=" $(grep '^=== ' "$wiz_here/deep32-wizard-demo.txt" | cut -c5- | tr '\n' ' ') "
    for ((k = n; k >= 1; k--)); do [[ $DEMO_NAMES == *" vm-$name-$k "* ]] && { want="vm-$name-$k"; break; }; done
    [ -n "$want" ] || want="vm-$name"
    DEMO_OUT=()
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%$'\r'}
        if [[ $line == '=== '* ]]; then ((inside)) && break; [ "${line#=== }" = "$want" ] && inside=1; continue; fi
        ((inside)) || continue
        [[ $line == '#'* ]] && continue
        case $line in
            '.if windows') if [ "$VM_OS" = windows ] && [ "${skip[${#skip[@]}-1]}" = 0 ]; then skip+=(0); else skip+=(1); fi; continue ;;
            '.if unix') if [ "$VM_OS" = unix ] && [ "${skip[${#skip[@]}-1]}" = 0 ]; then skip+=(0); else skip+=(1); fi; continue ;;
            .endif) ((${#skip[@]} > 1)) && unset "skip[$((${#skip[@]} - 1))]"; continue ;;
        esac
        [ "${skip[${#skip[@]}-1]}" = 1 ] && continue
        [ -n "$line" ] && DEMO_OUT+=("${line//'{tab}'/$'\t'}")
    done <"$wiz_here/deep32-wizard-demo.txt"
}

json_flat() {  # json_flat: the JSON document on standard input as PATH<TAB>VALUE lines (keys joined by ".", items as [N])
    awk '
    function path(d) { if (d < 1) return ""; if (typ[d] == "[") return base[d] "[" idx[d] "]"; return (base[d] == "" ? key[d] : base[d] "." key[d]) }
    { text = text $0 "\n" }
    END {
        n = length(text); d = 0; i = 1
        while (i <= n) {
            c = substr(text, i, 1)
            if (c == "{" || c == "[") { p = path(d); d++; typ[d] = c; base[d] = p; idx[d] = 0; key[d] = ""; has[d] = 0; i++; continue }
            if (c == "}" || c == "]") { d--; i++; continue }
            if (c == ",") { if (typ[d] == "[") idx[d]++; else has[d] = 0; i++; continue }
            if (c == "\"") {
                j = i + 1; s = ""
                while (j <= n) {
                    e = substr(text, j, 1)
                    if (e == "\\") { f = substr(text, j + 1, 1); if (f == "n" || f == "t" || f == "r") s = s " "; else s = s f; j += 2; continue }
                    if (e == "\"") break
                    s = s e; j++
                }
                i = j + 1
                if (typ[d] == "{" && !has[d]) { key[d] = s; has[d] = 1; continue }
                print path(d) "\t" s; continue
            }
            if (c ~ /[-0-9tfn]/) { j = i; while (substr(text, j, 1) ~ /[-+.0-9a-zA-Z]/) j++; print path(d) "\t" substr(text, i, j - i); i = j; continue }
            i++
        }
    }'
}

# What a VM's settings say, as facts the rules in the data file read: firmware (bios, uefi), display, nic, netmode,
# cores, memory_mb, target_bytes (the largest disk), installer (yes, no), boot_first (installer, target, other),
# cdrom (attached image names), and per hypervisor ostype, cpu, scsihw, chipset, generation. FSHOW keeps what the
# tool itself said for each. A fact the tool does not show is left out, and its rules are skipped.

vm_unit_bytes() {  # vm_unit_bytes NUMBER UNIT: NUMBER (whole or with a fraction) times the binary UNIT (K M G T or empty), into BYTES
    local n=$1 u=${2:-} whole frac=0 mult=1 digits=1
    case $u in K) mult=1024 ;; M) mult=1048576 ;; G) mult=1073741824 ;; T) mult=1099511627776 ;; esac
    whole=${n%%.*}; [[ $n == *.* ]] && frac=${n#*.}
    frac=${frac:0:6}; local f=$frac; while [ ${#f} -gt 0 ]; do digits=$((digits * 10)); f=${f:1}; done
    BYTES=$(( 10#$whole * mult + 10#$frac * mult / digits ))
}

vm_disks_facts() {  # vm_disks_facts BOOTFIRST DEV:BYTES...: target_bytes, installer and boot_first from the disks
    local first=$1 d dev b big='' bigb=-1 small='' smallb=-1; shift
    for d in "$@"; do
        dev=${d%:*}; b=${d##*:}; num_ok "$b" || continue
        ((b > bigb)) && { bigb=$b; big=$dev; }
        ((smallb < 0 || b < smallb)) && { smallb=$b; small=$dev; }
    done
    [ -n "$big" ] || { FACT[installer]=no; FSHOW[installer]='no disk'; return 0; }
    fmt_bytes "$bigb"; FACT[target_bytes]=$bigb; FSHOW[target_bytes]="$big, $FMT ($bigb bytes)"
    if (($# >= 2)) || ((smallb < 10000000000)); then FACT[installer]=yes; fmt_bytes "$smallb"; FSHOW[installer]="$small, $FMT"
    else FACT[installer]=no; FSHOW[installer]='not attached'; small=''; fi
    if [ -n "$first" ]; then
        if [ "$first" = "$big" ] && [ "$big" != "$small" ]; then FACT[boot_first]=target
        elif [ "$first" = "$small" ]; then FACT[boot_first]=installer
        else FACT[boot_first]=other; fi
        FSHOW[boot_first]="first boot device $first"
    fi
    return 0
}

pve_facts() {  # pve_facts LINE...: a Proxmox VE VM configuration (qm config, or the API's data as KEY: VALUE lines)
    FACT=(); FSHOW=()
    local l k v t file cds='' boot='' sockets=1 cores=1 first='' net=''
    local -a disks=()
    FACT[firmware]=bios; FSHOW[firmware]='bios not set (seabios)'; FACT[display]=std; FSHOW[display]='vga not set (std)'
    FACT[ostype]=other; FSHOW[ostype]='ostype not set (other)'; FACT[scsihw]=lsi; FSHOW[scsihw]='scsihw not set (lsi)'
    FACT[memory_mb]=512; FSHOW[memory_mb]='memory not set (512 MiB)'
    for l in "$@"; do
        l=${l%$'\r'}
        [[ $l =~ ^([a-z0-9_-]+):\ ?(.*)$ ]] || continue
        k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
        case $k in
            bios) FACT[firmware]=bios; [ "$v" = ovmf ] && FACT[firmware]=uefi; FSHOW[firmware]="bios: $v" ;;
            ostype) FACT[ostype]=$v; FSHOW[ostype]="ostype: $v" ;;
            cpu) t=${v#cputype=}; t=${t%%,*}; FACT[cpu]=$t; FSHOW[cpu]="cpu: $t" ;;
            cores) cores=$v ;;
            sockets) sockets=$v ;;
            memory) t=${v#current=}; t=${t%%,*}; FACT[memory_mb]=$t; FSHOW[memory_mb]="memory: $t MiB" ;;
            scsihw) FACT[scsihw]=$v; FSHOW[scsihw]="scsihw: $v" ;;
            vga) t=${v#type=}; t=${t%%,*}; FSHOW[display]="vga: $t"
                case $t in virtio|virtio-gl) FACT[display]=virtio-gpu ;; *) FACT[display]=$t ;; esac ;;
            net0) t=${v%%,*}; t=${t#model=}; t=${t%%=*}; FACT[nic]=$t; FSHOW[nic]="net0: $t"
                [[ ,$v, =~ ,bridge=([^,]+), ]] && FSHOW[nic]+=" on ${BASH_REMATCH[1]}" ;;
            boot) [[ $v =~ (^|,)order=([^,]+) ]] && { first=${BASH_REMATCH[2]%%;*}; boot=${BASH_REMATCH[2]}; } ;;
            ide[0-9]|sata[0-9]|scsi[0-9]|scsi[0-9][0-9]|virtio[0-9]|virtio[0-9][0-9])
                file=${v%%,*}
                if [[ ,$v, == *,media=cdrom,* ]]; then [ "$file" = none ] || { vm_base "${file#*:}"; cds+=" $BASE"; }; continue; fi
                if [[ ,$v, =~ ,size=([0-9]+(\.[0-9]+)?)([KMGT]?), ]]; then
                    vm_unit_bytes "${BASH_REMATCH[1]}" "${BASH_REMATCH[3]}"; disks+=("$k:$BYTES")
                fi ;;
        esac
    done
    if num_ok "$cores" && num_ok "$sockets"; then FACT[cores]=$((cores * sockets)); FSHOW[cores]="cores: $cores, sockets: $sockets"; fi
    vm_disks_facts "$first" "${disks[@]}"
    [ -n "$boot" ] && FSHOW[boot_first]="boot: order=$boot"
    cds=${cds# }; FACT[cdrom]=$cds; FSHOW[cdrom]=${cds:-no CD drive with an image}
    return 0
}

vm_rules() {  # vm_rules PHASE: this hypervisor's rules for PHASE against FACT; RULE_OUT lines (ok|err TAB label TAB shown TAB code), RULE_CODE
    local j have val ok label show seen=' '
    RULE_OUT=(); RULE_CODE=''
    for j in "${!HR_HV[@]}"; do   # a hypervisor's own row replaces the * row for the same phase and fact
        [ "${HR_HV[j]}" = "$VM_HV" ] && [ "${HR_PHASE[j]}" = "$1" ] && seen+="${HR_FACT[j]} "
    done
    for j in "${!HR_HV[@]}"; do
        [ "${HR_PHASE[j]}" = "$1" ] || continue
        if [ "${HR_HV[j]}" = '*' ]; then [[ $seen == *" ${HR_FACT[j]} "* ]] && continue
        elif [ "${HR_HV[j]}" != "$VM_HV" ]; then continue; fi
        [ -n "${FACT[${HR_FACT[j]}]+x}" ] || continue
        have=${FACT[${HR_FACT[j]}]}; vm_fill "${HR_VAL[j]}"; val=$FILLED; ok=0
        case ${HR_OP[j]} in
            eq) [ "$have" = "$val" ] && ok=1 ;;
            ne) [ "$have" != "$val" ] && ok=1 ;;
            ge) num_ok "$have" && num_ok "$val" && ((have >= val)) && ok=1 ;;
            in) [[ ",$val," == *",$have,"* ]] && ok=1 ;;
            has) [[ " $have " == *" $val "* ]] && ok=1 ;;
        esac
        label=${HFL[${HR_FACT[j]}]:-${HR_FACT[j]}}; show=${FSHOW[${HR_FACT[j]}]:-$have}
        if ((ok)); then RULE_OUT+=("ok"$'\t'"$label"$'\t'"$show"$'\t')
        else RULE_OUT+=("err"$'\t'"$label"$'\t'"$show"$'\t'"${HR_CODE[j]}"); [ -n "$RULE_CODE" ] || RULE_CODE=${HR_CODE[j]}; fi
    done
    return 0
}

# The checks. Each one sets VS_STATE[VM_I]: ok, error (VS_CODE names the row in the error table), waiting (the
# tailnet join), job (the VirtualBox copy), or ask-confirm, ask-pin, ask-secret when the screen must ask first.

vm_res() { VS_RES[VM_I]+="$1"$'\n'; }   # vm_res LINE: add one result line (state TAB label TAB shown TAB code)

vm_finish_rules() {  # the rule results for the current step: ok, or error with the first code
    local r; VS_RES[VM_I]=''
    for r in "${RULE_OUT[@]}"; do vm_res "$r"; done
    if [ -n "$RULE_CODE" ]; then VS_STATE[VM_I]=error; VS_CODE[VM_I]=$RULE_CODE; else VS_STATE[VM_I]=ok; VS_CODE[VM_I]=''; fi
    ((${#RULE_OUT[@]})) || { VS_STATE[VM_I]=ask-confirm; VS_RES[VM_I]="info"$'\t'"${HT[ui-nofacts]}"$'\t\t'$'\n'; }
    return 0
}

vm_fail() { VS_STATE[VM_I]=error; VS_CODE[VM_I]=$1; [ -n "${2:-}" ] && vm_res "err"$'\t'"$2"$'\t'"${3:-}"$'\t'"$1"; return 0; }

vm_check() {  # check the current step
    local c=${VS_CHECK[VM_I]}
    VS_RES[VM_I]=''; VS_CODE[VM_I]=''
    wiz_log "--- vm guide: check step $((VM_I + 1)) ${VS_ID[VM_I]} ($c)"
    case $c in
        confirm) VS_STATE[VM_I]=ask-confirm ;;
        image) kit_check_image ;;
        copy) kit_check_copy ;;
        vmdk) kit_vmdk ;;
        vdi) kit_vdi_start ;;
        vm:*) c=${c#vm:}; vm_check_vm "${c%%+*}" ;;
        join) ts_join_start ;;
        *) VS_STATE[VM_I]=ask-confirm ;;
    esac
    return 0
}

kit_check_image() {  # the verified disc 1 image is where the kit says, with the size the signed release gives
    local size=''
    if ((opt_demo)); then size=$KIT_BYTES; else file_size "$KIT_IMAGE"; size=$FS; fi
    if [ -z "$size" ]; then vm_fail KIT_MISSING "${HFL[image]}" "$KIT_IMAGE"; return 0; fi
    if [ "$size" != "$KIT_BYTES" ]; then vm_fail KIT_MISSING "${HFL[image]}" "$size bytes, the release says $KIT_BYTES"; return 0; fi
    vm_res "ok"$'\t'"${HFL[image]}"$'\t'"$KIT_IMAGE, $size bytes"$'\t'
    vm_res "ok"$'\t'"${HFL[sha256]}"$'\t'"$KIT_SHA (checked by deep32-get)"$'\t'
    VS_STATE[VM_I]=ok
}

kit_check_copy() {  # deep32-get's recipe for this hypervisor: the installer copy, recipe.json and commands.txt
    if [ "$KIT_GETHV" != "$VM_HV" ]; then vm_fail KIT_MISSING "${HFL[copy]}" "$KIT_DIR holds the $KIT_GETHV files"; return 0; fi
    local want=$KIT_BYTES size n; [ "$KIT_GETHV" = hyper-v ] && want=$((KIT_BYTES + 512))
    size=$want; ((opt_demo)) || { file_size "$KIT_COPY"; size=$FS; }
    if [ "$size" != "$want" ]; then vm_fail KIT_MISSING "${HFL[copy]}" "$KIT_COPY $size"; return 0; fi
    for n in recipe.json commands.txt; do ((opt_demo)) || [ -e "$KIT_DIR$SEP$n" ] || { vm_fail KIT_MISSING "${HFL[copy]}" "$KIT_DIR$SEP$n"; return 0; }; done
    vm_res "ok"$'\t'"${HFL[copy]}"$'\t'"$KIT_COPY, $size bytes"$'\t'
    VS_STATE[VM_I]=ok
}

kit_vmdk() {  # write the VMware descriptor next to deep32-get's installer copy (a wrapper: the copy is not changed)
    local d="$KIT_DIR${SEP}installer.vmdk" size='' sectors cyl
    if ((opt_demo)); then size=$KIT_BYTES; else file_size "$KIT_COPY"; size=$FS; fi
    if [ -z "$size" ] || ! num_ok "$KIT_BYTES" || ((size < KIT_BYTES)); then vm_fail KIT_MISSING "${HFL[copy]}" "$KIT_COPY"; return 0; fi
    sectors=$((KIT_BYTES / 512)); cyl=$((sectors / (16 * 63))); ((cyl > 16383)) && cyl=16383
    vm_fill "${HT[vmdk-text]}"; local text=${FILLED//'{cyl}'/$cyl}
    if ((opt_demo == 0)); then
        printf '%s\n' "${text//'\n'/$'\n'}" >"$d" 2>/dev/null || { vm_fail KIT_COPY_FAILED "${HFL[vmdk]}" "$d"; return 0; }
        local back=''; back=$(cat "$d" 2>/dev/null)
        [[ $back == *"RW $sectors FLAT \"${VF[copyname]}\" 0"* ]] || { vm_fail KIT_COPY_FAILED "${HFL[vmdk]}" "$d"; return 0; }
        wiz_log "--- vm guide: wrote $d (RW $sectors FLAT ${VF[copyname]})"
    fi
    vm_res "ok"$'\t'"${HFL[vmdk]}"$'\t'"$d"$'\t'
    vm_res "ok"$'\t'"${HFL[copy]}"$'\t'"${VF[copyname]}, $KIT_BYTES bytes from offset 0, $sectors sectors"$'\t'
    VS_STATE[VM_I]=ok
}

kit_vdi_start() {  # VBoxManage convertfromraw: a converted copy of the verified image in the kit folder, run in the background
    VDI_OUT="$KIT_DIR${SEP}installer.vdi"
    if ((opt_demo)); then VS_STATE[VM_I]=job; JOB_PID=demo; JOB_UNTIL=$(( $(date +%s) + 2 )); return 0; fi
    hv_index virtualbox
    local vbox=${HV_CMD[HI]:-}
    if [ "$VM_WHERE" != here ] || [ -z "$vbox" ]; then VS_STATE[VM_I]=ask-confirm; vm_res "info"$'\t'"${HT[ui-vdi-elsewhere]}"$'\t\t'; return 0; fi
    if [ -e "$VDI_OUT" ]; then kit_vdi_end 0; return 0; fi
    JOB_LOG="$STATE_DIR/convertfromraw.log"
    wiz_log "--- vm guide: $vbox convertfromraw $KIT_IMAGE $VDI_OUT --format VDI --variant Fixed"
    "$vbox" convertfromraw "$KIT_IMAGE" "$VDI_OUT" --format VDI --variant Fixed >"$JOB_LOG" 2>&1 &
    JOB_PID=$!; VS_STATE[VM_I]=job
}

kit_vdi_poll() {  # the background copy finished: check it
    [ "${VS_STATE[VM_I]}" = job ] || return 0
    if [ "$JOB_PID" = demo ]; then (( $(date +%s) >= JOB_UNTIL )) && { JOB_PID=''; kit_vdi_end 0; }; return 0; fi
    kill -0 "$JOB_PID" 2>/dev/null && return 0
    wait "$JOB_PID" 2>/dev/null; local st=$?; JOB_PID=''
    local l; while IFS= read -r l; do wiz_log "$l"; done <"$JOB_LOG" 2>/dev/null
    kit_vdi_end "$st"
}

kit_vdi_end() {  # kit_vdi_end STATUS: a fixed-size VDI holds the whole image, so it is at least as large as the image
    local size=''
    if ((opt_demo)); then size=$((KIT_BYTES + 2097152)); else file_size "$VDI_OUT"; size=$FS; fi
    VS_RES[VM_I]=''
    if [ "$1" != 0 ] || [ -z "$size" ] || ((size < KIT_BYTES)); then vm_fail KIT_COPY_FAILED "${HFL[vdi]}" "$VDI_OUT ${size:+($size bytes)}"; DIRTY=1; return 0; fi
    vm_res "ok"$'\t'"${HFL[vdi]}"$'\t'"$VDI_OUT, $size bytes (the image is $KIT_BYTES)"$'\t'
    VS_STATE[VM_I]=ok; DIRTY=1
}

vm_local_ok() {  # this hypervisor runs here and the wizard can read its VM settings from this shell
    [ "$VM_WHERE" = here ] || return 1
    hv_index "$VM_HV"
    case ${HV_LOCAL[HI]} in qm|vbox|virsh) [ "${HV_FOUND[HI]}" = yes ] ;; vmx) [ -n "${VMF_vmx:-}" ] ;; *) return 1 ;; esac
}

vm_check_vm() {  # vm_check_vm PHASE: read the VM's settings (here, or through the Proxmox API) and apply the rules
    local phase=$1
    if vm_local_ok; then
        hv_index "$VM_HV"
        case ${HV_LOCAL[HI]} in
            qm) vm_tool pve-config config "$VMF_vmid" || { vm_fail VM_NOT_FOUND "${HFL[vm]}" "${TOOL_OUT[0]:-qm config $VMF_vmid}"; return 0; }; pve_facts "${TOOL_OUT[@]}" ;;
            vbox) vbox_read || return 0 ;;
            vmx) vmx_read || return 0 ;;
            virsh) virsh_read || return 0 ;;
        esac
        vm_rules "$phase"; vm_finish_rules; return 0
    fi
    if [ "$VM_HV" = proxmox ] && [ -n "${VMF_token:-}" ]; then
        [ -n "$PVE_PIN" ] || { VS_STATE[VM_I]=ask-pin; pve_fp; return 0; }
        [ -n "$PVE_SECRET" ] || { VS_STATE[VM_I]=ask-secret; return 0; }
        pve_get "$phase"; return 0
    fi
    VS_STATE[VM_I]=ask-confirm
}

vm_tool() {  # vm_tool DEMO ARG...: run one read-only query of this hypervisor's tool (the demo plays DEMO); TOOL_OUT, TOOL_ST
    TOOL_OUT=(); TOOL_ST=0
    local key=$1 l; shift
    if ((opt_demo)); then demo_lines "$key"; TOOL_OUT=("${DEMO_OUT[@]}"); wiz_log "--- vm guide (demo): $*"; return 0; fi
    hv_index "$VM_HV"; local cmd=${HV_CMD[HI]}
    [ -n "$cmd" ] || { TOOL_ST=127; return 1; }
    wiz_log "--- vm guide: $cmd $*"
    while IFS= read -r l; do TOOL_OUT+=("${l%$'\r'}"); done < <("$cmd" "$@" 2>&1; echo "--- exit $?")
    l=${TOOL_OUT[${#TOOL_OUT[@]}-1]}; unset "TOOL_OUT[${#TOOL_OUT[@]}-1]"; TOOL_ST=${l#--- exit }
    for l in "${TOOL_OUT[@]}"; do wiz_log "$l"; done
    [ "$TOOL_ST" = 0 ]
}

PVE_PY='
import hashlib, http.client, json, ssl, sys
mode, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE  # the certificate is pinned below to the fingerprint the person compared
try:
    c = http.client.HTTPSConnection(host, port, context=ctx, timeout=10)
    c.connect()
    h = hashlib.sha256(c.sock.getpeercert(True)).hexdigest().upper()
    got = ":".join(h[i:i + 2] for i in range(0, 64, 2))
    print("fp\t" + got)
    if mode == "get":
        if got != sys.argv[6]:
            print("mismatch\t" + got); sys.exit(0)
        secret = sys.stdin.readline().rstrip("\r\n")
        c.request("GET", sys.argv[4], headers={"Authorization": "PVEAPIToken=%s=%s" % (sys.argv[5], secret)})
        r = c.getresponse(); body = r.read().decode("utf-8", "replace")
        print("status\t%d" % r.status)
        if r.status == 200:
            for k, v in sorted((json.loads(body).get("data") or {}).items()):
                print("cfg\t%s: %s" % (k, v))
        else:
            print("reason\t" + (r.reason + " " + body[:300]).replace("\n", " ").replace("\t", " "))
except (OSError, ssl.SSLError, ValueError) as e:
    print("unreachable\t%s" % e)
'

pve_fp() {  # the Proxmox node's certificate fingerprint, for the person to compare: PVE_SEEN (or an error)
    PVE_SEEN=''
    local k v
    if ((opt_demo)); then PVE_SEEN=${HT[demo-pve-fp]}; return 0; fi
    command -v python3 >/dev/null 2>&1 || { vm_fail PVE_UNREACHABLE "${HFL[api]}" "${HT[ui-nopython]}"; return 0; }
    wiz_log "--- vm guide: TLS certificate of $VMF_host:8006"
    while IFS=$'\t' read -r k v; do
        case $k in fp) PVE_SEEN=$v ;; unreachable) vm_fail PVE_UNREACHABLE "${HFL[api]}" "$VMF_host:8006: $v"; return 0 ;; esac
    done < <(python3 -I -c "$PVE_PY" fp "$VMF_host" 8006 2>&1)
    [ -n "$PVE_SEEN" ] || vm_fail PVE_UNREACHABLE "${HFL[api]}" "$VMF_host:8006"
    return 0
}

pve_get() {  # pve_get PHASE: GET the VM's configuration with the token, over the pinned certificate
    local path="/api2/json/nodes/$VMF_node/qemu/$VMF_vmid/config" k v status='' reason='' seen=''
    local -a cfg=()
    wiz_log "--- vm guide: GET https://$VMF_host:8006$path with token $VMF_token (the secret is not logged)"
    if ((opt_demo)); then demo_lines pve-config; cfg=("${DEMO_OUT[@]}"); status=200
    else
        while IFS=$'\t' read -r k v; do
            case $k in
                cfg) cfg+=("$v") ;; status) status=$v ;; reason) reason=$v ;; mismatch) seen=$v ;;
                unreachable) vm_fail PVE_UNREACHABLE "${HFL[api]}" "$VMF_host:8006: $v"; return 0 ;;
            esac
        done < <(printf '%s\n' "$PVE_SECRET" | python3 -I -c "$PVE_PY" get "$VMF_host" 8006 "$path" "$VMF_token" "$PVE_PIN" 2>&1)
    fi
    wiz_log "--- vm guide: API status ${status:-none} $reason"
    if [ -n "$seen" ]; then PVE_PIN=''; vm_fail PVE_TLS_MISMATCH "${HFL[api]}" "$seen"; return 0; fi
    case $status in
        200) pve_facts "${cfg[@]}"; vm_rules "$1"; vm_finish_rules ;;
        401|403) PVE_SECRET=''; vm_fail PVE_TOKEN_DENIED "${HFL[api]}" "HTTP $status $reason" ;;
        *) if [[ $reason == *'does not exist'* ]]; then vm_fail VM_NOT_FOUND "${HFL[vm]}" "$reason"
           else vm_fail PVE_UNREACHABLE "${HFL[api]}" "HTTP ${status:-none} $reason"; fi ;;
    esac
    return 0
}

ts_read() {  # ts_read [DEMO]: this PC's Tailscale from `tailscale status --json`: TS_STATE TS_TAILNET TS_SUFFIX TS_CERTS TS_PEERS
    TS_STATE=''; TS_TAILNET=''; TS_SUFFIX=''; TS_CERTS=''; TS_PEERS=()
    local -a lines=()
    if ((opt_demo)); then demo_lines "${1:-ts}"; lines=("${DEMO_OUT[@]}")
    else
        command -v tailscale >/dev/null 2>&1 || { TS_STATE=missing; return 0; }
        local l; while IFS= read -r l; do lines+=("$l"); done < <(tailscale status --json 2>/dev/null)
    fi
    local p v k; local -A host=() online=()
    while IFS=$'\t' read -r p v; do
        case $p in
            BackendState) TS_STATE=$v ;; CurrentTailnet.Name) TS_TAILNET=$v ;; MagicDNSSuffix) TS_SUFFIX=$v ;;
            'CertDomains[0]') TS_CERTS=$v ;;
            Peer.*.HostName) k=${p#Peer.}; host[${k%.HostName}]=$v ;;
            Peer.*.Online) k=${p#Peer.}; online[${k%.Online}]=$v ;;
        esac
    done < <(printf '%s\n' "${lines[@]}" | json_flat)
    [ -n "$TS_STATE" ] || TS_STATE=missing
    for k in "${!host[@]}"; do TS_PEERS+=("$k"$'\t'"${host[$k]}"$'\t'"${online[$k]:-false}"); done
    return 0
}

ts_snapshot() {  # the tailnet's machines before the Workstation exists, so a new one can be told apart
    ts_read ts; TS_SNAP=' '
    local p; for p in "${TS_PEERS[@]}"; do TS_SNAP+="${p%%$'\t'*} "; done
}

ts_join_start() {  # wait for a new online machine named like the Workstation (only when this PC is on the tailnet)
    ts_read ts
    if [ "$TS_STATE" != Running ]; then
        VS_STATE[VM_I]=ask-confirm; vm_res "info"$'\t'"${HFL[tailscale]}"$'\t'"${TS_STATE:-missing}: ${HT[ui-ts-not-here]}"$'\t'"TS_NOT_HERE"
        VS_CODE[VM_I]=TS_NOT_HERE; return 0
    fi
    VS_STATE[VM_I]=waiting; TS_POLLS=0; TS_POLL_AT=0; ts_join_poll
}

ts_join_poll() {  # one look at the tailnet while the step waits
    [ "${VS_STATE[VM_I]}" = waiting ] || return 0
    wiz_now; ((NOW_MS - TS_POLL_AT < ${TS_EVERY:-5000})) && return 0
    TS_POLL_AT=$NOW_MS; TS_POLLS=$((TS_POLLS + 1)); DIRTY=1
    ts_read ts-poll
    local p key h o want=${VMF_wsname,,}
    for p in "${TS_PEERS[@]}"; do
        key=${p%%$'\t'*}; h=${p#*$'\t'}; o=${h#*$'\t'}; h=${h%%$'\t'*}
        [[ $TS_SNAP == *" $key "* ]] && continue
        [ "${h,,}" = "$want" ] || continue
        if [ "$o" = true ]; then
            VS_RES[VM_I]=''; vm_res "ok"$'\t'"${HFL[tailscale]}"$'\t'"$h is on your tailnet ${TS_TAILNET:+($TS_TAILNET) }and online"$'\t'
            VS_STATE[VM_I]=ok; wiz_log "--- vm guide: $h joined the tailnet"; return 0
        fi
    done
    return 0
}

ts_join_stop() {  # the person stopped waiting before the Workstation showed up
    VS_RES[VM_I]=''; vm_fail TS_PEER_MISSING "${HFL[tailscale]}" "${VMF_wsname} ${HT[ui-ts-missing]}"
}

vbox_facts() {  # vbox_facts LINE...: VBoxManage showvminfo --machinereadable (disk sizes in VB_CAP, by path) into FACT and FSHOW
    FACT=(); FSHOW=()
    local l k v name path cds='' first='' firstkey='' key
    local -A ctl=(); local -a disks=()
    for l in "$@"; do
        l=${l%$'\r'}
        [[ $l =~ ^storagecontrollername([0-9]+)=\"(.*)\"$ ]] && ctl[${BASH_REMATCH[2]}]=${BASH_REMATCH[1]}
    done
    for l in "$@"; do
        l=${l%$'\r'}
        if [[ $l =~ ^\"(.+)-([0-9]+)-([0-9]+)\"=\"(.*)\"$ ]]; then
            name=${BASH_REMATCH[1]}; path=${BASH_REMATCH[4]}
            [[ $name == *-ImageUUID ]] && continue
            [ -n "${ctl[$name]+x}" ] || continue
            case $path in none|emptydrive|'') continue ;; esac
            vm_base "$path"
            if [[ ${BASE,,} == *.iso ]]; then cds+=" $BASE"; continue; fi
            key=$(printf '%03d%03d%03d' "${ctl[$name]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}")
            disks+=("$name-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}:${VB_CAP[$path]:-x}")
            if [ -z "$firstkey" ] || [[ $key < $firstkey ]]; then firstkey=$key; first="$name-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"; fi
            continue
        fi
        [[ $l =~ ^([A-Za-z0-9_-]+)=\"?([^\"]*)\"?$ ]] || continue
        k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
        case $k in
            firmware) FACT[firmware]=bios; [[ $v == EFI* ]] && FACT[firmware]=uefi; FSHOW[firmware]="firmware=$v" ;;
            chipset) FACT[chipset]=$v; FSHOW[chipset]="chipset=$v" ;;
            memory) FACT[memory_mb]=$v; FSHOW[memory_mb]="memory=$v" ;;
            cpus) FACT[cores]=$v; FSHOW[cores]="cpus=$v" ;;
            nictype1) FACT[nic]=$v; FSHOW[nic]="nictype1=$v" ;;
            graphicscontroller) FACT[display]=$v; FSHOW[display]="graphicscontroller=$v" ;;
            nic1) FSHOW[netmode]="nic1=$v"
                case $v in bridged) FACT[netmode]=bridged ;; nat|natnetwork) FACT[netmode]=nat ;; hostonly|hostonlynetwork|intnet) FACT[netmode]=hostonly ;; *) FACT[netmode]=none ;; esac ;;
        esac
    done
    vm_disks_facts "$first" "${disks[@]}"
    cds=${cds# }; FACT[cdrom]=$cds; FSHOW[cdrom]=${cds:-no DVD with an image}
    return 0
}

vbox_read() {  # the VM's settings from VBoxManage (read-only queries)
    vm_tool vbox-info showvminfo "$VMF_vm" --machinereadable || { vm_fail VM_NOT_FOUND "${HFL[vm]}" "${TOOL_OUT[0]:-$VMF_vm}"; return 1; }
    local -a info=("${TOOL_OUT[@]}"); local l path
    VB_CAP=()
    for l in "${info[@]}"; do   # each attached disk's capacity (attached media are already registered with VirtualBox)
        [[ ${l%$'\r'} =~ ^\"(.+)-[0-9]+-[0-9]+\"=\"(.*)\"$ ]] || continue
        path=${BASH_REMATCH[2]}; [[ ${BASH_REMATCH[1]} == *-ImageUUID ]] && continue
        case ${path,,} in none|emptydrive|*.iso) continue ;; esac
        vm_base "$path"; vm_tool "vbox-medium-${BASE%.*}" showmediuminfo disk "$path" || continue
        local m; for m in "${TOOL_OUT[@]}"; do [[ $m =~ ^Capacity:\ +([0-9]+)\ MBytes ]] && VB_CAP[$path]=$(( ${BASH_REMATCH[1]} * 1048576 )); done
    done
    vbox_facts "${info[@]}"
}

vmx_facts() {  # vmx_facts LINE...: a VMware .vmx file into FACT and FSHOW (only keys Broadcom documents)
    FACT=(); FSHOW=()
    local l k v dev cds='' inst=no tgt=''
    local -A file=() type=() present=()
    for l in "$@"; do
        l=${l%$'\r'}
        [[ $l =~ ^([A-Za-z0-9_.:]+)\ *=\ *\"(.*)\"$ ]] || continue
        k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
        case ${k,,} in
            memsize) FACT[memory_mb]=$v; FSHOW[memory_mb]="memsize = $v" ;;
            numvcpus) FACT[cores]=$v; FSHOW[cores]="numvcpus = $v" ;;
            ethernet0.virtualdev) FACT[nic]=$v; FSHOW[nic]="ethernet0.virtualDev = $v" ;;
            *.filename) dev=${k%.*}; file[$dev]=$v ;;
            *.devicetype) dev=${k%.*}; type[$dev]=$v ;;
            *.present) dev=${k%.*}; present[$dev]=${v^^} ;;
        esac
    done
    for dev in "${!file[@]}"; do
        [ "${present[$dev]:-TRUE}" = FALSE ] && continue
        vm_base "${file[$dev]}"
        if [ "${type[$dev]:-}" = cdrom-image ]; then cds+=" $BASE"; continue; fi
        [[ ${BASE,,} == *.vmdk ]] || continue
        case $BASE in installer.vmdk|installer-esxi.vmdk) inst=yes ;; *) tgt=$BASE ;; esac
    done
    FACT[installer]=$inst; FSHOW[installer]=$([ "$inst" = yes ] && echo 'installer.vmdk attached' || echo 'not attached')
    if [ "$inst" = yes ]; then FACT[boot_first]=installer; FSHOW[boot_first]='the installer disk is attached'
    elif [ -n "$tgt" ]; then FACT[boot_first]=target; FSHOW[boot_first]="$tgt"; fi
    cds=${cds# }; FACT[cdrom]=$cds; FSHOW[cdrom]=${cds:-no CD/DVD with an ISO image}
    return 0
}

vmx_read() {  # the .vmx the person named (read, never written), and whether vmrun lists the VM as running
    local l
    TOOL_OUT=()
    if ((opt_demo)); then demo_lines vmx; TOOL_OUT=("${DEMO_OUT[@]}")
    elif [ -r "$VMF_vmx" ]; then while IFS= read -r l || [ -n "$l" ]; do TOOL_OUT+=("$l"); done <"$VMF_vmx"; wiz_log "--- vm guide: read $VMF_vmx"
    else vm_fail VM_NOT_FOUND "${HFL[vm]}" "$VMF_vmx"; return 1; fi
    vmx_facts "${TOOL_OUT[@]}"
}

virsh_facts() {  # virsh_facts LINE...: libvirt domain XML (virsh dumpxml) into FACT and FSHOW
    FACT=(); FSHOW=()
    local l in='' dev='' devtype='' src='' tgt='' cds='' firstsrc='' firstdev='' bootdev='' n u inst=no
    FACT[firmware]=bios; FSHOW[firmware]='no firmware or loader set (BIOS)'
    for l in "$@"; do
        l=${l%$'\r'}
        [[ $l =~ \<os\ [^\>]*firmware=.efi. ]] && { FACT[firmware]=uefi; FSHOW[firmware]="os firmware='efi'"; }
        [[ $l =~ \<loader[^\>]*type=.pflash. ]] && { FACT[firmware]=uefi; FSHOW[firmware]="loader type='pflash'"; }
        if [[ $l =~ \<vcpu[^\>]*\>([0-9]+)\</vcpu\> ]]; then FACT[cores]=${BASH_REMATCH[1]}; FSHOW[cores]="vcpu ${BASH_REMATCH[1]}"; fi
        if [[ $l =~ \<memory([^\>]*)\>([0-9]+)\</memory\> ]]; then
            n=${BASH_REMATCH[2]}; u=KiB; [[ ${BASH_REMATCH[1]} =~ unit=.([A-Za-z]+). ]] && u=${BASH_REMATCH[1]}
            case $u in b|bytes) n=$((n / 1048576)) ;; KiB|k|K) n=$((n / 1024)) ;; MiB|M) ;; GiB|G) n=$((n * 1024)) ;; esac
            FACT[memory_mb]=$n; FSHOW[memory_mb]="memory $n MiB"
        fi
        if [[ $l =~ \<disk\ [^\>]*device=.([a-z]+). ]]; then in=disk; devtype=${BASH_REMATCH[1]}; src=''; tgt=''; continue; fi
        if [[ $l =~ \<interface\ [^\>]*type=.([a-z]+). ]]; then in=if; [ "${BASH_REMATCH[1]}" = bridge ] && { FACT[netmode]=bridged; FSHOW[netmode]="interface type='bridge'"; }; continue; fi
        [[ $l =~ \<video\> ]] && { in=video; continue; }
        case $in in
            disk)
                [[ $l =~ \<source\ [^\>]*(file|dev)=.([^\'\"]+). ]] && src=${BASH_REMATCH[2]}
                [[ $l =~ \<target\ [^\>]*dev=.([a-z0-9]+). ]] && tgt=${BASH_REMATCH[1]}
                [[ $l =~ \<boot\ [^\>]*order=.1. ]] && bootdev=$tgt
                if [[ $l =~ \</disk\> ]]; then
                    vm_base "$src"
                    if [ "$devtype" = cdrom ]; then [ -n "$src" ] && cds+=" $BASE"
                    elif [ "$devtype" = disk ]; then
                        [ "$BASE" = installer.img ] && inst=yes
                        [ -n "$firstdev" ] || { firstdev=$tgt; firstsrc=$BASE; }
                    fi
                    in=''
                fi ;;
            if)
                if [[ $l =~ \<source\ [^\>]*network=.default. ]] && [ -z "${FACT[netmode]:-}" ]; then FACT[netmode]=nat; FSHOW[netmode]="network 'default'"; fi
                [[ $l =~ \<model\ [^\>]*type=.([a-z0-9_-]+). ]] && { FACT[nic]=${BASH_REMATCH[1]}; FSHOW[nic]="model type='${BASH_REMATCH[1]}'"; }
                [[ $l =~ \</interface\> ]] && in='' ;;
            video)
                if [[ $l =~ \<model\ [^\>]*type=.([a-z0-9]+). ]]; then
                    FSHOW[display]="video model type='${BASH_REMATCH[1]}'"
                    if [ "${BASH_REMATCH[1]}" = virtio ]; then FACT[display]=virtio-gpu; else FACT[display]=${BASH_REMATCH[1]}; fi
                fi
                [[ $l =~ \</video\> ]] && in='' ;;
        esac
    done
    FACT[installer]=$inst; FSHOW[installer]=$([ "$inst" = yes ] && echo 'installer.img attached' || echo 'not attached')
    if [ -n "$firstdev" ]; then
        if [ "$firstsrc" = installer.img ]; then FACT[boot_first]=installer; else FACT[boot_first]=target; fi
        FSHOW[boot_first]="first disk ${bootdev:-$firstdev} ($firstsrc)"
    fi
    cds=${cds# }; FACT[cdrom]=$cds; FSHOW[cdrom]=${cds:-no CD with an image}
    return 0
}

virsh_read() { vm_tool virsh-xml -r -c qemu:///system dumpxml "$VMF_vm" || { vm_fail VM_NOT_FOUND "${HFL[vm]}" "${TOOL_OUT[0]:-$VMF_vm}"; return 1; }; virsh_facts "${TOOL_OUT[@]}"; }

net_detect() {  # NET_ROWS ("label TAB value"): this PC's LAN, the hypervisor's networks and Tailscale, read-only
    NET_ROWS=(); NET_DEF_HOSTIF=''; NET_DEF_SWITCH=''
    if ((opt_demo)); then
        demo_lines net; NET_ROWS=("${DEMO_OUT[@]}")
        local r; for r in "${NET_ROWS[@]}"; do case $r in hostif$'\t'*) NET_DEF_HOSTIF=${r#*$'\t'} ;; switch$'\t'*) NET_DEF_SWITCH=${r#*$'\t'} ;; esac; done
        local -a keep=(); for r in "${NET_ROWS[@]}"; do case $r in hostif$'\t'*|switch$'\t'*) ;; *) keep+=("$r") ;; esac; done
        NET_ROWS=("${keep[@]}")
    elif [ "$VM_OS" = unix ] && command -v ip >/dev/null 2>&1; then
        local p v dev='' gw='' addr='' plen='' dyn=no dns=''
        while IFS=$'\t' read -r p v; do case $p in '[0].gateway') gw=$v ;; '[0].dev') dev=$v ;; esac; done < <(ip -j route show default 2>/dev/null | json_flat)
        if [ -n "$dev" ]; then
            local fam=''
            while IFS=$'\t' read -r p v; do
                case $p in
                    *'].family') fam=$v ;;
                    *'].local') [ "$fam" = inet ] && [ -z "$addr" ] && addr=$v ;;
                    *'].prefixlen') [ "$fam" = inet ] && [ -z "$plen" ] && plen=$v ;;
                    *'].dynamic') [ "$fam" = inet ] && [ "$v" = true ] && dyn=yes ;;
                esac
            done < <(ip -j addr show dev "$dev" 2>/dev/null | json_flat)
            if command -v resolvectl >/dev/null 2>&1; then dns=$(resolvectl dns "$dev" 2>/dev/null | sed 's/^[^:]*: *//' | head -n1)
            else dns=$(awk '$1 == "nameserver" { printf "%s ", $2 }' /etc/resolv.conf 2>/dev/null); fi
            NET_ROWS+=("This PC"$'\t'"$addr/$plen on $dev, $([ "$dyn" = yes ] && echo 'from DHCP' || echo 'static')")
            NET_ROWS+=("Gateway"$'\t'"${gw:-none}")
            NET_ROWS+=("DNS"$'\t'"${dns:-not found}")
        fi
        hv_index proxmox
        if [ "${HV_FOUND[HI]}" = yes ]; then
            local br aware=''; br=$(ip -br link 2>/dev/null | awk '$1 ~ /^vmbr/ { printf "%s ", $1 }')
            grep -q 'bridge-vlan-aware yes' /etc/network/interfaces 2>/dev/null && aware=', VLAN aware'
            NET_ROWS+=("Proxmox bridges"$'\t'"${br:-none}$aware")
            NET_DEF_HOSTIF=${br%% *}
        fi
        hv_index kvm
        if [ "${HV_FOUND[HI]}" = yes ] && command -v virsh >/dev/null 2>&1; then
            local nets; nets=$(virsh -r -c qemu:///system net-list --all --name 2>/dev/null | tr -s '\n' ' ')
            NET_ROWS+=("libvirt networks"$'\t'"${nets:-none listed}")
        fi
        hv_index virtualbox
        if [ "${HV_FOUND[HI]}" = yes ]; then
            local b h; b=$("${HV_CMD[HI]}" list bridgedifs 2>/dev/null | awk -F': *' '$1 == "Name" { printf "%s; ", $2 }')
            h=$("${HV_CMD[HI]}" list hostonlyifs 2>/dev/null | awk -F': *' '$1 == "Name" { printf "%s; ", $2 }')
            NET_ROWS+=("VirtualBox bridged"$'\t'"${b%; }"); NET_ROWS+=("VirtualBox host-only"$'\t'"${h%; }")
            NET_DEF_HOSTIF=${b%%; *}
        fi
        hv_index vmware
        if [ "${HV_FOUND[HI]}" = yes ]; then
            local vn; vn=$(ip -br link 2>/dev/null | awk '$1 ~ /^vmnet/ { printf "%s ", $1 }')
            NET_ROWS+=("VMware host networks"$'\t'"${vn:-none} (VMnet0 bridged, VMnet1 host-only, VMnet8 NAT)")
        fi
    else
        NET_ROWS+=("This PC"$'\t'"${HT[ui-net-unread]:-not read on this system}")
    fi
    ts_read ts
    local ts
    case $TS_STATE in
        Running) ts="signed in to ${TS_TAILNET:-your tailnet}, MagicDNS suffix ${TS_SUFFIX:-none}, HTTPS certificates $([ -n "$TS_CERTS" ] && echo on || echo off)" ;;
        missing) ts='not installed on this PC' ;;
        *) ts="installed, $TS_STATE" ;;
    esac
    NET_ROWS+=("Tailscale here"$'\t'"$ts")
    ts_snapshot
}

# The guide's flow: vm_begin, then the screens hv, where, net, tsnote, vmform, the steps (vmstep) and vmsum.

vm_begin() {  # start the VM guide, after the Workstation host role or straight from --vm-guide
    hv_load; vm_os; hv_detect
    VM_HV=''; VM_WHERE=''; VM_NET=bridged; VM_DONE=0; VM_I=0; PVE_PIN=''; PVE_SECRET=''; PVE_SEEN=''; VMF_FOR=''
    VM_STEPS_FILE=''; JOB_PID=''; VS_ID=(); SCROLL=0; TS_EVERY=5000; ((opt_demo)) && TS_EVERY=1000
    local i pick=-1
    for i in "${!HV_IDS[@]}"; do [ "${HV_IDS[i]}" = "${opt_hv:-}" ] && pick=$i; done
    if ((pick < 0)); then for i in "${!HV_IDS[@]}"; do [ "${HV_FOUND[i]}" = yes ] && { pick=$i; break; }; done; fi
    ((pick < 0)) && pick=0
    [ -n "$ROLE" ] || ROLE=workstation
    stage_set 3; STEP_STATE[4]=later; STEP_NOTE[3]='VM guide'
    VIEW=hv; SEL=$pick; FOCUS=0; DIRTY=1
    wiz_log "--- vm guide: started; found on this PC: $(for i in "${!HV_IDS[@]}"; do [ "${HV_FOUND[i]}" = yes ] && printf '%s ' "${HV_IDS[i]}"; done)"
}

vm_where_opts() {  # VM_WOPTS: where the VM can run for this hypervisor (here, other)
    VM_WOPTS=()
    local h=${HV_HERE[VHI]}
    case $h in
        always) VM_WOPTS+=(here) ;;
        found) [ "${HV_FOUND[VHI]}" = yes ] && VM_WOPTS+=(here) ;;
        windows|unix) [ "$VM_OS" = "$h" ] && VM_WOPTS+=(here) ;;
    esac
    VM_WOPTS+=(other)
}

vm_pick_hv() {  # the hypervisor is chosen: read the kit, then ask where the VM runs
    VM_HV=${HV_IDS[SEL]}; VHI=$SEL
    if [ "$VMF_FOR" != "$VM_HV" ]; then local k; for k in "${HFD_KEY[@]}"; do unset "VMF_$k"; done; VMF_FOR=$VM_HV; fi
    wiz_log "--- vm guide: hypervisor $VM_HV"
    if ! kit_find "${opt_kit:-}"; then VIEW=vmkit; FOCUS=0; return; fi
    vm_where_opts
    if ((${#VM_WOPTS[@]} == 1)); then VM_WHERE=${VM_WOPTS[0]}; vm_to_net; else VIEW=where; SEL=0; [ "${HV_FOUND[VHI]}" = yes ] || SEL=1; ((SEL >= ${#VM_WOPTS[@]})) && SEL=0; FOCUS=0; fi
}

vm_net_opts() { local n=${HV_NETS[VHI]:-bridged}; VM_NOPTS=(${n//,/ }); }

vm_to_net() {  # read the network (read-only) and show it
    net_detect; vm_net_opts
    local i; SEL=0; for i in "${!VM_NOPTS[@]}"; do [ "${VM_NOPTS[i]}" = "$VM_NET" ] && SEL=$i; done
    VM_NET=${VM_NOPTS[SEL]}; VIEW=net; FOCUS=0; SCROLL=0
}

vm_form_build() {  # the settings this hypervisor and place need, as the form's fields (VMF_* variables)
    FORM_KEYS=(); FORM_LABEL=(); FORM_HELP=(); FORM_PAT=(); FORM_PERR=()
    local j key var def
    vm_vars
    for j in "${!HFD_KEY[@]}"; do
        case ${HFD_HV[j]} in '*'|"$VM_HV") ;; *) continue ;; esac
        case ${HFD_WHERE[j]} in ''|'*'|"$VM_WHERE"|"$VM_NET") ;; *) continue ;; esac
        key=${HFD_KEY[j]}; var="VMF_$key"
        if [ -z "${!var:-}" ]; then
            def=${HFD_DEF[j]}
            def=${def//'{hostif}'/$NET_DEF_HOSTIF}; def=${def//'{switch}'/$NET_DEF_SWITCH}; def=${def//'{vmxdefault}'/$(vm_vmx_default)}
            printf -v "$var" '%s' "$def"
        fi
        FORM_KEYS+=("$var"); FORM_LABEL+=("${HFD_LABEL[j]}"); FORM_HELP+=("${HFD_HELP[j]}"); FORM_PAT+=("${HFD_PAT[j]}"); FORM_PERR+=("${HFD_ERR[j]}")
    done
    FORM_TITLE="${HV_NAMES[VHI]}: the VM's settings"
}

vm_vmx_default() {  # where Workstation keeps a VM named Deep32Workstation (Broadcom: Documents\Virtual Machines, ~/vmware)
    if ((opt_demo)); then if [ "$VM_OS" = windows ]; then printf '%s' 'C:\Users\you\Documents\Virtual Machines\Deep32Workstation\Deep32Workstation.vmx'
        else printf '%s' '/home/you/vmware/Deep32Workstation/Deep32Workstation.vmx'; fi; return; fi
    printf '%s' "$HOME/vmware/Deep32Workstation/Deep32Workstation.vmx"
}

vm_form_valid() {  # FORM_ERR for the VM settings
    FORM_ERR=''
    local i v
    for i in "${!FORM_KEYS[@]}"; do
        v=${!FORM_KEYS[i]}
        [ -n "${FORM_PAT[i]}" ] || continue
        [[ $v =~ ${FORM_PAT[i]} ]] || { FORM_ERR=${FORM_PERR[i]}; return; }
    done
}

vm_start_steps() {  # the settings are in: fill the steps, write the steps file, show the first step
    vm_vars
    VF[nodeimage]=$KIT_IMAGE; [ "$VM_WHERE" = other ] && VF[nodeimage]="/var/tmp/${VF[imagename]}"
    VF[vdi]="$KIT_DIR${SEP}installer.vdi"; VF[ts-stall]=${HT[ts-stall]}
    vm_steps_build; vm_steps_write
    VM_I=0; SCROLL=0; VIEW=vmstep; FOCUS=0
    wiz_log "--- vm guide: $VM_HV on ${VM_WHERE} ($VM_NET), ${#VS_ID[@]} steps"
}

vm_state_word() {  # vm_state_word STATE [CODE]: the word the screen and the plain guide show, into SW
    case $1 in ok) SW=OK ;; error) SW="ERROR ${2:-}" ;; confirmed) SW='YOU CONFIRMED' ;; skipped) SW=SKIPPED ;; waiting) SW=WAITING ;; job) SW=WORKING ;; *) SW='NOT CHECKED' ;; esac
}

vm_fix_steps() {  # vm_fix_steps CODE: its meaning (MEAN) and fix steps (STEPS), this hypervisor's own fix first, filled in
    local j code=$1; MEAN=''; STEPS=()
    for ((j = 0; j < ${#ERR_KIND[@]}; j++)); do [ "${ERR_KIND[j]}" = code ] && [ "${ERR_KEY[j]}" = "$code" ] && { MEAN=${ERR_MEAN[j]}; err_steps "$j"; break; }; done
    if [ -n "${HFX["$VM_HV $code"]:-}" ]; then local f=${HFX["$VM_HV $code"]}; STEPS=(); while [ -n "$f" ]; do STEPS+=("${f%%' | '*}"); [[ $f == *' | '* ]] && f=${f#*' | '} || f=''; done; fi
    local i; for i in "${!STEPS[@]}"; do vm_fill "${STEPS[i]}"; STEPS[i]=$FILLED; done
    vm_fill "$MEAN"; MEAN=$FILLED
}

view_hv() {
    ptitle 'Workstation VM: the hypervisor' 'Which hypervisor will run the Workstation VM? The wizard looked for each one on this PC (read-only).'
    local i sel bg tag ts
    for i in "${!HV_IDS[@]}"; do
        sel=0; bg=''; ((i == SEL)) && { sel=1; bg=$S_SELBG; }
        pl "$bg"; if ((sel)); then pa "$S_ACCENT" "$G_MARK"; pa "$S_ACCENT" "$G_CUR"; else pa '' ' '; pa "$S_FAINT" "$G_PEND"; fi
        pa '' '  '; pa "$S_B" "${HV_NAMES[i]}"
        [ "${HV_FOUND[i]}" = yes ] && { pa '' '   '; pa "$S_OK" 'found on this PC'; }
        if [ "${HV_TESTED[i]}" = yes ]; then tag='boot-tested'; ts=$S_OK; else tag='not boot-tested yet'; ts=$S_FAINT; fi
        pgap $((PW - 3 - ${#tag})); pa "$ts" "$tag"; pe
    done
    pblank
    ppara "$S_MUTED" "${HT[hv-${HV_IDS[SEL]}]}"
    pbottom 1; BTNS=('Continue' 'Later'); pbuttons "${BTNS[@]}"
}

view_vmkit() {
    ptitle 'Workstation VM: no verified disc 1 here' "The guide needs deep32-get's VM folder (recipe.json and the installer copy) next to the verified disc 1 image." "$G_FAIL" "$S_ERR"
    vm_fix_steps KIT_MISSING
    ppara '' "$MEAN"; pblank
    pl; pa "$S_B" 'What to do'; pe
    psteps 4 "${STEPS[@]}"
    pbottom 1; BTNS=('Check again' 'Back'); pbuttons "${BTNS[@]}"
}

view_where() {
    ptitle "Where does the VM run?" "${HV_NAMES[VHI]}"
    local i o sel bg d
    for i in "${!VM_WOPTS[@]}"; do
        o=${VM_WOPTS[i]}; sel=0; bg=''; ((i == SEL)) && { sel=1; bg=$S_SELBG; }
        pl "$bg"; if ((sel)); then pa "$S_ACCENT" "$G_MARK"; pa "$S_ACCENT" "$G_CUR"; else pa '' ' '; pa "$S_FAINT" "$G_PEND"; fi
        pa '' '  '; pa "$S_B" "${HT[where-$o]}"; pe
        if [ "$o" = here ]; then
            if [ "${HV_FOUND[VHI]}" = yes ] || [ "${HV_LOCAL[VHI]}" = vmx ]; then d=${HT[where-here-ok]}; d=${d//'{tool}'/${HT[tool-${HV_LOCAL[VHI]}]}}
            else d=${HT[where-here-missing]}; fi
        else d=${HT[where-other-$VM_HV]:-${HT[where-other-text]}}; fi
        vm_vars; vm_fill "$d"
        wrap $((CW - 4)) "$FILLED"; local l; for l in "${WRAPPED[@]}"; do pl "$bg"; pa '' '    '; if ((sel)); then pa '' "$l"; else pa "$S_MUTED" "$l"; fi; pe; done
        pblank
    done
}

view_net() {
    ptitle 'Workstation VM: the network' "This PC's network, read only, and how the VM connects."
    local r l v
    for r in "${NET_ROWS[@]}"; do l=${r%%$'\t'*}; v=${r#*$'\t'}; pl; pa "$S_MUTED" " $l"; pgap 24; pa '' "$v"; pe; done
    pblank
    pl; pa "$S_B" "${HT[net-title]}"; pe
    vm_vars
    if ((${#VM_NOPTS[@]} == 1)); then
        local t=${HT[net-$VM_HV-only]:-${HT[net-only]}}; vm_fill "$t"; ppara '' "$FILLED"
        ppara "$S_MUTED" "${HT[net-${VM_NOPTS[0]}]}"
    else
        local i o sel bg rec
        for i in "${!VM_NOPTS[@]}"; do
            o=${VM_NOPTS[i]}; sel=0; bg=''; ((i == SEL)) && { sel=1; bg=$S_SELBG; }
            vm_fill "${HT[net-$VM_HV-$o]:-$o}"
            pl "$bg"; if ((sel)); then pa "$S_ACCENT" "$G_MARK"; pa "$S_ACCENT" "$G_CUR"; else pa '' ' '; pa "$S_FAINT" "$G_PEND"; fi
            pa '' '  '; pa "$S_B" "${HT[net-label-$o]}"; pa "$S_MUTED" ": $FILLED"
            [ "$o" = bridged ] && { pa '' '   '; pa "$S_OK" 'recommended'; }
            pe
        done
        ppara "$S_MUTED" "${HT[net-${VM_NOPTS[SEL]}]}"
    fi
    ppara "$S_WARN" "${HT[net-hostonly]}"
    while ((${#PANEL[@]} > CH - 2)); do pdrop; done
    pbottom 1; BTNS=('Continue' 'Back'); pbuttons "${BTNS[@]}"
}

view_tsnote() {
    ptitle "${HT[ts-title]}" ''
    pdrop
    local i; for i in 1 2 3 4; do pstep "$G_DOT" '' 4 "${HT[ts-$i]}"; done
    pbottom 1; BTNS=('Continue' 'Back'); pbuttons "${BTNS[@]}"
}

vm_result_lines() {  # the result of step I as panel lines (state word, the rows, and for an error its meaning and fix)
    local i=$1 st=${VS_STATE[$1]} code=${VS_CODE[$1]} r s lab show c n=0
    vm_state_word "$st" "$code"
    case $st in
        ok) pl; pa "$S_OK$S_B" "$G_OK $SW"; pe ;;
        error) pl; pa "$S_ERR$S_B" "$G_FAIL $SW"; pe ;;
        confirmed) pl; pa "$S_WARN$S_B" "$G_YOU $SW"; pa "$S_MUTED" "  ${VS_LOOK[i]}"; pe ;;
        skipped) pl; pa "$S_FAINT$S_B" "$G_SKIP $SW"; pa "$S_FAINT" '  listed again at the end'; pe ;;
        waiting) local looks="looked $TS_POLLS times"; ((TS_POLLS == 1)) && looks='looked once'
            pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_MUTED" "  Waiting for ${VMF_wsname} to join your tailnet  $G_DOT  $looks"; pe ;;
        job) pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_MUTED" '  VBoxManage convertfromraw is writing the copy'; pe ;;
        *) local hint='Enter checks this step.'; [ "${VS_CHECK[i]}" = confirm ] && hint='Enter shows what to look for; you confirm it.'
           pl; pa "$S_FAINT" "$hint"; pe; return ;;
    esac
    while IFS=$'\t' read -r s lab show c; do
        [ -n "$s" ] || continue
        [ "$st" = error ] && [ "$s" = ok ] && continue
        ((n >= 12)) && break
        case $s in ok) pl; pa "$S_OK" " $G_OK"; pa "$S_MUTED" "  $lab"; pgap 24; pa "$S_MUTED" "$show"; pe ;;
            err) pl; pa "$S_ERR" " $G_FAIL"; pa '' "  $lab"; pgap 24; pa '' "$show"; pe ;;
            *) pl; pa "$S_FAINT" " $G_INFO"; pa "$S_MUTED" "  $lab"; pgap 24; pa "$S_MUTED" "$show"; pe ;;
        esac
        n=$((n + 1))
    done <<<"${VS_RES[i]}"
    if [ "$st" = error ]; then
        vm_fix_steps "$code"
        [ -n "$MEAN" ] && ppara "$S_MUTED" "$MEAN"
        pl; pa "$S_B" 'What to do'; pe; psteps 3 "${STEPS[@]}"
    fi
}

view_vmstep() {
    local i=$VM_I n=${#VS_ID[@]} st=${VS_STATE[VM_I]} k it
    ptitle "${VS_TITLE[i]}" "Step $((i + 1)) of $n  $G_DOT  ${HV_NAMES[VHI]}, $(vm_tested_word)  $G_DOT  ${VS_PLACE[i]}"
    local -a head=("${PANEL[@]}") res body
    PANEL=(); vm_result_lines "$i"; res=("${PANEL[@]}")
    local most=$((CH - ${#head[@]} - 5)); ((${#res[@]} > most)) && res=("${res[@]:0:most}")
    PANEL=(); vm_items "${VS_BODY[i]}"; k=0
    for it in "${ITEMS[@]}"; do k=$((k + 1)); pstep "$k" '' 5 "$it"; done
    body=("${PANEL[@]}")
    PANEL=("${head[@]}")
    local room=$((CH - ${#PANEL[@]} - ${#res[@]} - 4)) total=${#body[@]}
    ((room < 2)) && room=2
    ((SCROLL > total - room)) && SCROLL=$((total - room)); ((SCROLL < 0)) && SCROLL=0
    for ((k = SCROLL; k < total && k < SCROLL + room; k++)); do PANEL+=("${body[k]}"); done
    local more=$((total - SCROLL - room))
    ((more > 0)) && { pl; pa "$S_FAINT" "  $G_DOWN $more more lines, PgDn"; pe; }
    pbottom $((${#res[@]} + 1))
    PANEL+=("${res[@]}")
    case $st in
        ok|confirmed|skipped) if ((i == n - 1)); then BTNS=('Finish' 'Check again'); else BTNS=('Next' 'Check again'); fi ;;
        waiting) BTNS=('Stop waiting' 'Skip') ;;
        job) BTNS=() ;;
        *) BTNS=('Check' 'Skip') ;;
    esac
    if ((${#BTNS[@]})); then pbuttons "${BTNS[@]}"; else pblank; fi
}

view_vmsum() {
    ptitle 'Workstation VM: where it stands' "${HV_NAMES[VHI]}, $(vm_tested_word). Every step, as it ended:"
    local i sk=() ic is
    for i in "${!VS_ID[@]}"; do
        vm_state_word "${VS_STATE[i]}" "${VS_CODE[i]}"
        case ${VS_STATE[i]} in ok) ic=$G_OK; is=$S_OK ;; error) ic=$G_FAIL; is=$S_ERR ;; confirmed) ic=$G_YOU; is=$S_WARN ;; *) ic=$G_SKIP; is=$S_FAINT; sk+=("$((i + 1)). ${VS_TITLE[i]}") ;; esac
        pl; pa "$is" " $ic"; pa '' "  $((i + 1)). ${VS_TITLE[i]}"; pgap $((PW - 3 - ${#SW})); pa "$is" "$SW"; pe
    done
    pblank
    if ((${#sk[@]})); then pl; pa "$S_B" 'Skipped, still to do'; pe; for i in "${sk[@]}"; do pl; pa "$S_WARN" "   $i"; pe; done; pblank; fi
    if [ -n "$VM_STEPS_FILE" ]; then
        if ((opt_demo)); then ppara "$S_FAINT" "Every command is in steps-$VM_HV.txt in the kit folder (the demo writes no file)."
        else pl; pa "$S_FAINT" 'Every command: '; pa "$S_MUTED" "$(short_path "$VM_STEPS_FILE")"; pe; fi
    fi
    pbottom 1; BTNS=('Done' 'Back'); pbuttons "${BTNS[@]}"
}

view_vmbox() {  # the questions a step asks over the screen: what to look for, the certificate, the token's secret
    local keep=$(( ${#PANEL[@]} > 5 ? 5 : ${#PANEL[@]} ))
    PANEL=("${PANEL[@]:0:keep}")
    case $OVERLAY in
        vmconfirm)
            bx_top "$S_WARN" 'Confirm this step'; BX_STYLE=$S_WARN
            bx_text "$S_B" "Look for: ${VS_LOOK[VM_I]}"
            [ "${VS_CODE[VM_I]}" = TS_NOT_HERE ] && { bx_open "$S_WARN"; bx_close; bx_text '' "${HT[ui-ts-not-here]^}"; }
            [ "${VS_CHECK[VM_I]}" != confirm ] && [ "${VS_CODE[VM_I]}" != TS_NOT_HERE ] && [[ ${VS_CHECK[VM_I]} != *+look ]] && { bx_open "$S_WARN"; bx_close; bx_text '' "${HT[ui-nofacts]}"; }
            bx_open "$S_WARN"; bx_close; bx_buttons 'Yes, I see it' 'Not yet'; bx_bot "$S_WARN" ;;
        vmpin)
            bx_top "$S_ACCENT" "The node's certificate"; BX_STYLE=$S_ACCENT
            bx_text '' "Proxmox uses its own certificate. Its SHA-256 fingerprint at https://$VMF_host:8006 is:"
            bx_text "$S_B" "$PVE_SEEN"
            bx_text '' 'Compare it with the node: in the web UI, the node, Certificates; or on the node `pvenode cert info` (pve-ssl.pem). If it matches, the wizard trusts this certificate for this session only.'
            bx_open "$S_ACCENT"; bx_close; bx_buttons 'It matches' 'It does not'; bx_bot "$S_ACCENT" ;;
        vmsecret)
            bx_top "$S_ACCENT" 'API token secret'; BX_STYLE=$S_ACCENT
            bx_text '' "Type or paste the secret of $VMF_token. It stays in memory for this session only: it is never shown, logged or saved."
            local dots; printf -v dots '%*s' "${#SECRET_TYPED}" ''; dots=${dots// /$G_DOT}
            bx_open "$S_ACCENT"; pa "$S_CHIPBG" " $dots"; pa "$S_CHIPBG$S_ACCENT" "$G_CURSOR "; bx_close
            bx_open "$S_ACCENT"; bx_close; bx_buttons 'Use it' 'Cancel'; bx_bot "$S_ACCENT" ;;
    esac
}

vm_after_check() {  # after a check ran: open the box it asks for, or stay
    case ${VS_STATE[VM_I]} in
        ask-confirm) OVERLAY=vmconfirm; FOCUS=0 ;;
        ask-pin) if [ -n "$PVE_SEEN" ]; then OVERLAY=vmpin; FOCUS=0; fi ;;
        ask-secret) OVERLAY=vmsecret; FOCUS=0; SECRET_TYPED='' ;;
    esac
    [ "${VS_STATE[VM_I]}" = error ] && [ "${VS_CODE[VM_I]}" = PVE_UNREACHABLE ] && PVE_SEEN=''
    if [ "${VS_STATE[VM_I]}" = ok ] && [[ ${VS_CHECK[VM_I]} == vm:*+look ]]; then OVERLAY=vmconfirm; FOCUS=0; fi
    return 0
}

vm_next() {  # the next step, or the summary after the last one
    if ((VM_I < ${#VS_ID[@]} - 1)); then VM_I=$((VM_I + 1)); SCROLL=0; FOCUS=0; STEP_NOTE[3]="VM step $((VM_I + 1)) of ${#VS_ID[@]}"
    else VIEW=vmsum; FOCUS=0; fi
}

vm_done() {  # the guide is over: the usual Done screen lists what is left
    VM_DONE=1; VM_SKIPPED=()
    local i; for i in "${!VS_ID[@]}"; do case ${VS_STATE[i]} in ok|confirmed) ;; *) VM_SKIPPED+=("${VS_TITLE[i]}") ;; esac; done
    wiz_log "--- vm guide: finished; not done: ${#VM_SKIPPED[@]}"
    finish
}

vm_key() {  # vm_key KEY: keys for the guide's screens and boxes; returns 1 when the key is not the guide's
    local k=$1
    case $OVERLAY in
        vmconfirm)
            case $k in left|right|tab|btab) btn_move "$k" ;; y|Y) FOCUS=0; k=enter ;; n|N|esc) FOCUS=1; k=enter ;; esac
            if [ "$k" = enter ]; then
                OVERLAY=''
                if ((FOCUS == 0)); then [ "${VS_STATE[VM_I]}" = ok ] || VS_RES[VM_I]+="info"$'\t'"${HFL[seen]}"$'\t'"${VS_LOOK[VM_I]}"$'\t'$'\n'; VS_STATE[VM_I]=confirmed; wiz_log "--- vm guide: step $((VM_I + 1)) confirmed by you"
                else VS_STATE[VM_I]=pending; STATUS_MSG='Not confirmed yet. Do the step, then press Enter again.'; fi
                FOCUS=0
            fi
            return 0 ;;
        vmpin)
            case $k in left|right|tab|btab) btn_move "$k" ;; y|Y) FOCUS=0; k=enter ;; n|N|esc) FOCUS=1; k=enter ;; esac
            if [ "$k" = enter ]; then
                OVERLAY=''
                if ((FOCUS == 0)); then PVE_PIN=$PVE_SEEN; wiz_log "--- vm guide: certificate $PVE_PIN pinned for this session"; vm_check; vm_after_check
                else VS_STATE[VM_I]=pending; PVE_SEEN=''; vm_fail PVE_TLS_MISMATCH "${HFL[api]}" "the fingerprint did not match"; fi
            fi
            return 0 ;;
        vmsecret)
            case $k in
                enter) OVERLAY=''; if ((FOCUS == 1)); then VS_STATE[VM_I]=pending; SECRET_TYPED=''; else PVE_SECRET=$SECRET_TYPED; SECRET_TYPED=''; vm_check; vm_after_check; fi ;;
                esc) OVERLAY=''; VS_STATE[VM_I]=pending; SECRET_TYPED='' ;;
                tab|btab|left|right) btn_move "$k" ;;
                bs) SECRET_TYPED=${SECRET_TYPED%?} ;;
                ctrl-u) SECRET_TYPED='' ;;
                *) [ ${#k} = 1 ] && [[ $k == [[:print:]] ]] && SECRET_TYPED+=$k ;;
            esac
            return 0 ;;
    esac
    case $VIEW in
        hv)
            case $k in
                up) ((SEL > 0)) && SEL=$((SEL - 1)) ;; down) ((SEL < ${#HV_IDS[@]} - 1)) && SEL=$((SEL + 1)) ;;
                left|right|tab|btab) btn_move "$k" ;;
                esc) vm_later ;;
                enter) if ((FOCUS == 1)); then vm_later; else vm_pick_hv; fi ;;
                *) return 1 ;;
            esac ;;
        vmkit)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                esc) VIEW=hv; hv_index "$VM_HV"; SEL=$HI; FOCUS=0 ;;
                enter) if ((FOCUS == 1)); then VIEW=hv; hv_index "$VM_HV"; SEL=$HI; FOCUS=0; else SEL=$VHI; vm_pick_hv; fi ;;
                *) return 1 ;;
            esac ;;
        where)
            case $k in
                up) ((SEL > 0)) && SEL=$((SEL - 1)) ;; down) ((SEL < ${#VM_WOPTS[@]} - 1)) && SEL=$((SEL + 1)) ;;
                esc) VIEW=hv; SEL=$VHI; FOCUS=0 ;;
                enter) VM_WHERE=${VM_WOPTS[SEL]}; wiz_log "--- vm guide: the VM runs $VM_WHERE"; vm_to_net ;;
                *) return 1 ;;
            esac ;;
        net)
            case $k in
                up) ((SEL > 0)) && SEL=$((SEL - 1)); VM_NET=${VM_NOPTS[SEL]} ;;
                down) ((SEL < ${#VM_NOPTS[@]} - 1)) && SEL=$((SEL + 1)); VM_NET=${VM_NOPTS[SEL]} ;;
                left|right|tab|btab) btn_move "$k" ;;
                esc) vm_back_from_net ;;
                enter) if ((FOCUS == 1)); then vm_back_from_net; else VM_NET=${VM_NOPTS[SEL]}; wiz_log "--- vm guide: network $VM_NET"; VIEW=tsnote; FOCUS=0; fi ;;
                *) return 1 ;;
            esac ;;
        tsnote)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                esc) VIEW=net; FOCUS=0 ;;
                enter) if ((FOCUS == 1)); then VIEW=net; FOCUS=0; else vm_form_build; VIEW=vmform; SEL=0; FOCUS=0; fi ;;
                *) return 1 ;;
            esac ;;
        vmform)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                esc) VIEW=tsnote; FOCUS=0 ;;
                enter) if ((FOCUS == 1)); then VIEW=tsnote; FOCUS=0; return 0; fi
                    vm_form_valid; if [ -n "$FORM_ERR" ]; then STATUS_MSG=$FORM_ERR; return 0; fi
                    vm_start_steps ;;
                *) return 1 ;;
            esac ;;
        vmstep)
            local st=${VS_STATE[VM_I]}
            case $k in
                up) ((SCROLL > 0)) && SCROLL=$((SCROLL - 1)) ;; down) SCROLL=$((SCROLL + 1)) ;;
                pgup) SCROLL=$((SCROLL - 8)); ((SCROLL < 0)) && SCROLL=0 ;; pgdn) SCROLL=$((SCROLL + 8)) ;;
                left|right|tab|btab) btn_move "$k" ;;
                s|S) [ "$st" = job ] || { VS_STATE[VM_I]=skipped; VS_RES[VM_I]=''; wiz_log "--- vm guide: step $((VM_I + 1)) skipped"; FOCUS=0; } ;;
                esc) [ "$st" = waiting ] && VS_STATE[VM_I]=pending
                    if ((VM_I > 0)); then VM_I=$((VM_I - 1)); SCROLL=0; FOCUS=0; else vm_form_build; VIEW=vmform; SEL=0; FOCUS=0; fi ;;
                enter)
                    case $st in
                        job) ;;
                        waiting) if ((FOCUS == 0)); then ts_join_stop; else VS_STATE[VM_I]=skipped; fi; FOCUS=0 ;;
                        ok|confirmed|skipped) if ((FOCUS == 0)); then vm_next; else vm_check; vm_after_check; fi ;;
                        *) if ((FOCUS == 1)); then VS_STATE[VM_I]=skipped; VS_RES[VM_I]=''; wiz_log "--- vm guide: step $((VM_I + 1)) skipped"; FOCUS=0
                           else vm_check; vm_after_check; FOCUS=0; fi ;;
                    esac ;;
                *) return 1 ;;
            esac ;;
        vmsum)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                esc) VIEW=vmstep; FOCUS=0 ;;
                enter) if ((FOCUS == 1)); then VIEW=vmstep; FOCUS=0; else vm_done; fi ;;
                *) return 1 ;;
            esac ;;
        *) return 1 ;;
    esac
    return 0
}

vm_back_from_net() { if ((${#VM_WOPTS[@]} > 1)); then VIEW=where; SEL=0; [ "$VM_WHERE" = other ] && SEL=1; else VIEW=hv; SEL=$VHI; fi; FOCUS=0; }

vm_later() {  # the VM guide is put off: the Done screen says how to come back to it
    VM_DONE=0; wiz_log '--- vm guide: put off'; finish
}

vm_tick() {  # each pass of the loop: the tailnet wait and the VirtualBox copy
    [ "$VIEW" = vmstep ] || return 0
    case ${VS_STATE[VM_I]:-} in waiting) ts_join_poll ;; job) kit_vdi_poll ;; esac
}

# The same guide as plain lines (--vm-guide when the full screen is off): one question per line, answers read from
# standard input, the same steps, checks and results.

vm_plain_ask() {  # vm_plain_ask PROMPT: one answer line into ANS (answers read from a pipe are echoed, so a log shows them)
    printf '%s' "$1"; ANS=''
    if ! IFS= read -r ANS; then PLAIN_EOF=1; fi
    ANS=${ANS%$'\r'}
    [ -t 0 ] || printf '%s\n' "$ANS"
    ((PLAIN_EOF)) && [ -z "$ANS" ] && { printf '\nThe answers ended; the guide stops here. Run it again to go on.\n'; return 1; }
    return 0
}

vm_plain_choose() {  # vm_plain_choose DEFAULT COUNT: a number from 1 to COUNT, empty for DEFAULT (1-based), into PICK
    local tries=0
    while :; do
        vm_plain_ask "Number [$1]: " || return 1
        [ -z "$ANS" ] && { PICK=$1; return 0; }
        [[ $ANS =~ ^[0-9]+$ ]] && ((ANS >= 1 && ANS <= $2)) && { PICK=$ANS; return 0; }
        tries=$((tries + 1)); ((tries >= 3)) && return 1
        printf 'Type a number from 1 to %d.\n' "$2"
    done
}

vm_plain_result() {  # the result of step I as plain lines
    local i=$1 s lab show c
    vm_state_word "${VS_STATE[i]}" "${VS_CODE[i]}"
    printf 'Result: %s\n' "$SW"
    while IFS=$'\t' read -r s lab show c; do
        [ -n "$s" ] || continue
        case $s in ok) printf '  ok     %s: %s\n' "$lab" "$show" ;; err) printf '  ERROR  %s: %s (%s)\n' "$lab" "$show" "$c" ;; *) printf '  note   %s: %s\n' "$lab" "$show" ;; esac
    done <<<"${VS_RES[i]}"
    if [ "${VS_STATE[i]}" = error ]; then
        vm_fix_steps "${VS_CODE[i]}"
        [ -n "$MEAN" ] && printf 'What it means: %s\n' "$MEAN"
        printf 'What to do:\n'; local k=0 it; for it in "${STEPS[@]}"; do k=$((k + 1)); vm_plain_item "  $k) " "$it"; done
    fi
}

vm_plain_settle() {  # after vm_check: answer the questions it asks, wait for what it waits for
    local guard=0
    while ((guard < 8)); do
        guard=$((guard + 1))
        case ${VS_STATE[VM_I]} in
            ask-confirm)
                [ "${VS_CODE[VM_I]}" = TS_NOT_HERE ] && printf 'Tailscale: %s\n' "${HT[ui-ts-not-here]}"
                printf 'Look for: %s\n' "${VS_LOOK[VM_I]}"
                vm_plain_ask 'Did you see it? [y/N] ' || return 1
                if [[ $ANS == [Yy]* ]]; then VS_RES[VM_I]+="info"$'\t'"${HFL[seen]}"$'\t'"${VS_LOOK[VM_I]}"$'\t'$'\n'; VS_STATE[VM_I]=confirmed
                else VS_STATE[VM_I]=pending; printf 'Not confirmed yet. Do the step, then check it again.\n'; fi
                return 0 ;;
            ask-pin)
                [ -n "$PVE_SEEN" ] || return 0
                printf "The node's certificate at https://%s:8006 has this SHA-256 fingerprint:\n  %s\n" "$VMF_host" "$PVE_SEEN"
                printf 'Compare it with the node: in the web UI, the node, Certificates; or on the node: pvenode cert info (pve-ssl.pem).\n'
                vm_plain_ask 'Does it match? [y/N] ' || return 1
                if [[ $ANS == [Yy]* ]]; then PVE_PIN=$PVE_SEEN; printf 'Pinned for this session.\n'; vm_check
                else PVE_SEEN=''; VS_STATE[VM_I]=pending; vm_fail PVE_TLS_MISMATCH "${HFL[api]}" 'the fingerprint did not match'; return 0; fi ;;
            ask-secret)
                printf 'Secret of %s (kept in memory for this session only; not shown, logged or saved): ' "$VMF_token"
                local s=''
                if [ -t 0 ]; then IFS= read -rs s || PLAIN_EOF=1; else IFS= read -r s || PLAIN_EOF=1; fi
                printf '\n'; s=${s%$'\r'}
                [ -n "$s" ] || { VS_STATE[VM_I]=pending; return 0; }
                PVE_SECRET=$s; vm_check ;;
            waiting)
                printf 'Waiting for %s to join your tailnet (this PC looks every %d s, for up to %d minutes).\n' "$VMF_wsname" $(( ${TS_EVERY:-5000} / 1000 )) $(( ${TS_LIMIT:-600} / 60 ))
                local start; start=$(date +%s)
                while [ "${VS_STATE[VM_I]}" = waiting ]; do
                    TS_POLL_AT=0; ts_join_poll
                    [ "${VS_STATE[VM_I]}" = waiting ] || break
                    (( $(date +%s) - start >= ${TS_LIMIT:-600} )) && { ts_join_stop; break; }
                    if ((opt_demo)); then sleep 0.2; else sleep $(( ${TS_EVERY:-5000} / 1000 )); fi
                done
                return 0 ;;
            job)
                printf 'VBoxManage convertfromraw is writing the copy.\n'
                while [ "${VS_STATE[VM_I]}" = job ]; do kit_vdi_poll; [ "${VS_STATE[VM_I]}" = job ] && sleep 0.5; done
                return 0 ;;
            *) return 0 ;;
        esac
    done
}

vm_plain() {  # the whole guide on plain lines
    local i n pick=-1 k it
    PLAIN_EOF=0; ROLE=workstation; TS_EVERY=5000; ((opt_demo)) && TS_EVERY=1000
    hv_load; err_load; vm_os; hv_detect; VM_NET=bridged; PVE_PIN=''; PVE_SECRET=''; PVE_SEEN=''
    printf 'Deep32 Workstation VM guide%s\n' "$( ((opt_demo)) && printf ' (demo: no hypervisor is asked and nothing changes)')"
    printf 'The wizard only reads: it never creates, changes, starts or stops a VM.\n\n'
    for i in "${!HV_IDS[@]}"; do [ "${HV_IDS[i]}" = "${opt_hv:-}" ] && pick=$i; done
    if ((pick < 0)); then for i in "${!HV_IDS[@]}"; do [ "${HV_FOUND[i]}" = yes ] && { pick=$i; break; }; done; fi
    ((pick < 0)) && pick=0
    printf 'Which hypervisor runs the Workstation VM?\n'
    for i in "${!HV_IDS[@]}"; do
        SEL=$i; VHI=$i
        printf '  %d) %s, %s%s\n' $((i + 1)) "${HV_NAMES[i]}" "$(vm_tested_word)" "$([ "${HV_FOUND[i]}" = yes ] && printf ', found on this PC')"
    done
    vm_plain_choose $((pick + 1)) "${#HV_IDS[@]}" || return 1
    SEL=$((PICK - 1)); VM_HV=${HV_IDS[SEL]}; VHI=$SEL
    printf '%s\n' "${HT[hv-$VM_HV]}"
    if ! kit_find "${opt_kit:-}"; then
        vm_fix_steps KIT_MISSING; printf 'No verified disc 1 with deep32-get'"'"'s VM folder here.\n%s\nWhat to do:\n' "$MEAN"
        k=0; for it in "${STEPS[@]}"; do k=$((k + 1)); vm_plain_item "  $k) " "$it"; done
        return 1
    fi
    vm_where_opts
    if ((${#VM_WOPTS[@]} > 1)); then
        printf 'Where does the VM run?\n'
        for i in "${!VM_WOPTS[@]}"; do printf '  %d) %s\n' $((i + 1)) "${HT[where-${VM_WOPTS[i]}]}"; done
        local wdef=1; [ "${HV_FOUND[VHI]}" = yes ] || wdef=2
        vm_plain_choose "$wdef" "${#VM_WOPTS[@]}" || return 1
        VM_WHERE=${VM_WOPTS[PICK - 1]}
    else VM_WHERE=${VM_WOPTS[0]}; fi
    printf '\nThe network, read on this PC:\n'
    net_detect; vm_net_opts
    local r; for r in "${NET_ROWS[@]}"; do printf '  %-22s %s\n' "${r%%$'\t'*}" "${r#*$'\t'}"; done
    vm_vars
    printf '%s:\n' "${HT[net-title]}"
    if ((${#VM_NOPTS[@]} == 1)); then
        VM_NET=${VM_NOPTS[0]}; vm_fill "${HT[net-$VM_HV-only]:-${HT[net-only]}}"; printf '  %s\n  %s\n' "$FILLED" "${HT[net-$VM_NET]}"
    else
        for i in "${!VM_NOPTS[@]}"; do vm_fill "${HT[net-$VM_HV-${VM_NOPTS[i]}]:-}"; printf '  %d) %s: %s\n' $((i + 1)) "${HT[net-label-${VM_NOPTS[i]}]}" "$FILLED"; done
        for i in "${!VM_NOPTS[@]}"; do printf '     %s\n' "${HT[net-${VM_NOPTS[i]}]}"; done
        vm_plain_choose 1 "${#VM_NOPTS[@]}" || return 1
        VM_NET=${VM_NOPTS[PICK - 1]}
    fi
    printf '  %s\n\n%s:\n' "${HT[net-hostonly]}" "${HT[ts-title]}"
    for i in 1 2 3 4; do printf '  - %s\n' "${HT[ts-$i]}"; done
    printf '\nThe VM'"'"'s settings (Enter keeps the value in brackets):\n'
    vm_form_build
    for i in "${!FORM_KEYS[@]}"; do
        printf '  %s\n' "${FORM_HELP[i]//\`/}"
        local tries=0
        while :; do
            vm_plain_ask "${FORM_LABEL[i]} [${!FORM_KEYS[i]}]: " || return 1
            [ -n "$ANS" ] && printf -v "${FORM_KEYS[i]}" '%s' "$ANS"
            if [ -z "${FORM_PAT[i]}" ] || [[ ${!FORM_KEYS[i]} =~ ${FORM_PAT[i]} ]]; then break; fi
            printf '%s\n' "${FORM_PERR[i]}"; tries=$((tries + 1)); ((tries >= 3)) && return 1
        done
    done
    vm_start_steps
    n=${#VS_ID[@]}
    printf '\n%s, %s: %d steps.' "${HV_NAMES[VHI]}" "$(vm_tested_word)" "$n"
    if ((opt_demo)); then printf ' The steps file is steps-%s.txt in the kit folder (the demo writes no file).\n' "$VM_HV"
    else printf ' Every command is also in %s\n' "${VM_STEPS_FILE:-the kit folder}"; fi
    VM_I=0
    while ((VM_I < n)); do
        printf '\nStep %d of %d: %s\n' $((VM_I + 1)) "$n" "${VS_TITLE[VM_I]}"
        [ -n "${VS_PLACE[VM_I]}" ] && printf 'Where: %s\n' "${VS_PLACE[VM_I]}"
        vm_items "${VS_BODY[VM_I]}"; k=0
        for it in "${ITEMS[@]}"; do k=$((k + 1)); vm_plain_item "  $k) " "$it"; done
        while :; do
            vm_plain_ask 'Enter checks this step; s skips it, b goes back, q stops: ' || return 1
            case $ANS in
                q|Q) printf 'Stopped. Run the guide again to go on.\n'; return 1 ;;
                b|B) ((VM_I > 0)) && VM_I=$((VM_I - 1)); continue 2 ;;
                s|S) VS_STATE[VM_I]=skipped; VS_RES[VM_I]=''; vm_plain_result "$VM_I"; break ;;
                '') vm_check; vm_plain_settle || return 1
                    if [ "${VS_STATE[VM_I]}" = ok ] && [[ ${VS_CHECK[VM_I]} == vm:*+look ]]; then VS_STATE[VM_I]=ask-confirm; vm_plain_settle || return 1; fi
                    vm_plain_result "$VM_I"
                    case ${VS_STATE[VM_I]} in ok|confirmed) break ;; esac ;;
                *) printf 'Press Enter, or type s, b or q.\n' ;;
            esac
        done
        VM_I=$((VM_I + 1))
    done
    printf '\nWhere it stands:\n'
    local sk=0
    for i in "${!VS_ID[@]}"; do vm_state_word "${VS_STATE[i]}" "${VS_CODE[i]}"; printf '  %d. %-44s %s\n' $((i + 1)) "${VS_TITLE[i]}" "$SW"; case ${VS_STATE[i]} in ok|confirmed) ;; *) sk=$((sk + 1)) ;; esac; done
    if ((sk)); then printf 'Skipped, still to do:\n'; for i in "${!VS_ID[@]}"; do case ${VS_STATE[i]} in ok|confirmed) ;; *) printf '  %d. %s\n' $((i + 1)) "${VS_TITLE[i]}" ;; esac; done; fi
    printf "\nNext: pair the Forge and the agent PC with the Workstation (the wizard's What's next, or INSTALL.md).\n"
    return 0
}

# ---------------------------------------------------------------------------------------------------------------
# 11. Keys.

read_key() {  # read_key TIMEOUT: one key into KEY (a name such as up or enter, or the character)
    KEY=''
    local c rest seq ch
    IFS= read -rsn1 -t "$1" c || return 1
    case $c in
        $'\e')
            if IFS= read -rsn1 -t 0.02 rest; then
                case $rest in
                    '['|O)
                        seq=''
                        while IFS= read -rsn1 -t 0.02 ch; do seq+=$ch; [[ $ch == [A-Za-z~] ]] && break; done
                        case $seq in
                            A) KEY=up ;; B) KEY=down ;; C) KEY=right ;; D) KEY=left ;; H|1~|7~) KEY=home ;; F|4~|8~) KEY=end ;;
                            5~) KEY=pgup ;; 6~) KEY=pgdn ;; 3~) KEY=del ;; Z) KEY=btab ;; *) KEY=ignore ;;
                        esac ;;
                    ']') while IFS= read -rsn1 -t 0.05 ch; do [[ $ch == $'\a' || $ch == '\' ]] && break; done; KEY=ignore ;;
                    *) KEY=esc ;;
                esac
            else KEY=esc; fi ;;
        '') KEY=enter ;;
        $'\r') KEY=enter ;;
        $'\t') KEY=tab ;;
        $'\x7f'|$'\b') KEY=bs ;;
        $'\x15') KEY=ctrl-u ;;
        $'\x03') KEY=ctrl-c ;;
        *) KEY=$c ;;
    esac
    return 0
}

script_key() {  # the next key from DEEP32_WIZARD_KEYS (tests and screenshots), into KEY
    KEY=''
    if [ -n "$UNTIL" ]; then
        wiz_now
        if [[ " $VIEW $OVERLAY " == *" $UNTIL "* ]] || ((NOW_MS > UNTIL_AT)); then
            ((NOW_MS > UNTIL_AT)) && wiz_log "--- script: gave up waiting for $UNTIL"
            UNTIL=''
        else return 1; fi
    fi
    if [ -n "${WAIT_UNTIL:-}" ]; then wiz_now; ((NOW_MS < WAIT_UNTIL)) && return 1; WAIT_UNTIL=''; fi
    ((${#KEYQ[@]})) || { [ -n "$CHILD_PID" ] || QUIT=1; return 1; }
    local t=${KEYQ[0]}; KEYQ=("${KEYQ[@]:1}")
    case $t in
        wait:*) local w=${t#wait:} ws wf; ws=${w%%.*}; wf=${w#*.}; [ "$wf" = "$w" ] && wf=0; wf=${wf}000
            wiz_now; WAIT_UNTIL=$(( NOW_MS + 10#${ws:-0} * 1000 + 10#${wf:0:3} )); return 1 ;;
        waitms:*) wiz_now; WAIT_UNTIL=$(( NOW_MS + ${t#waitms:} )); return 1 ;;
        until:*) UNTIL=${t#until:}; wiz_now; UNTIL_AT=$((NOW_MS + 90000)); return 1 ;;
        snap:*) SNAP=${t#snap:}; DIRTY=1; return 1 ;;
        size:*) local s=${t#size:}; W=${s%x*}; H=${s#*x}; layout; FULL=1; printf '\e[2J'; DIRTY=1; return 1 ;;
        text:*) local s=${t#text:} i; for ((i = ${#s} - 1; i >= 0; i--)); do KEYQ=("char:${s:i:1}" "${KEYQ[@]}"); done; return 1 ;;
        char:*) KEY=${t#char:} ;;
        space) KEY=' ' ;;
        crash) local boom; : "${boom:?the test asked for an error}" ;;
        *) KEY=$t ;;
    esac
    return 0
}

btn_move() { case $1 in left|btab) ((FOCUS > 0)) && FOCUS=$((FOCUS - 1)) ;; right|tab) ((FOCUS < ${BTN_COUNT:-1} - 1)) && FOCUS=$((FOCUS + 1)) ;; esac; }

on_key() {
    local k=$KEY
    DIRTY=1; STATUS_MSG=''
    if ((W < 80 || H < 24)); then
        if [ -n "$CHILD_PID" ]; then STATUS_MSG='A step is running; make the window bigger to see it.'; return; fi
        case $k in p|P) PLAIN_AFTER=1; QUIT=1 ;; q|Q|ctrl-c) QUIT=1 ;; esac
        return
    fi
    case $OVERLAY in vmconfirm|vmpin|vmsecret) vm_key "$k"; return ;; esac
    case $OVERLAY in
        log) case $k in up) LOGSCROLL=$((LOGSCROLL + 1)) ;; down) ((LOGSCROLL > 0)) && LOGSCROLL=$((LOGSCROLL - 1)) ;;
                pgup) LOGSCROLL=$((LOGSCROLL + CH - 5)) ;; pgdn) LOGSCROLL=$((LOGSCROLL - CH + 5)); ((LOGSCROLL < 0)) && LOGSCROLL=0 ;;
                home) LOGSCROLL=${#LOG[@]} ;; end) LOGSCROLL=0 ;; esc|l|L|q|Q|enter) OVERLAY='' ;; esac
            [ -z "$OVERLAY" ] && ((ASK_PENDING)) && ask_open; return ;;
        help) OVERLAY=''; ((ASK_PENDING)) && ask_open; return ;;
        quit) case $k in left|right|tab|btab) btn_move "$k" ;; esc) OVERLAY='' ;;
                enter) if ((FOCUS == 1)); then QUIT=1; else OVERLAY=''; fi ;; q|Q) QUIT=1 ;; esac
            [ -z "$OVERLAY" ] && ((ASK_PENDING)) && ask_open; return ;;
        ask) case $k in left|right|tab|btab) btn_move "$k" ;; y|Y) FOCUS=0; k=enter ;; n|N|esc) FOCUS=1; k=enter ;; esac
            if [ "$k" = enter ]; then
                if ((FOCUS == 0)); then run_answer y; else run_answer n; fi
                OVERLAY=''; FOCUS=0
            fi
            return ;;
    esac
    # Typing into the digest and the settings fields comes before the single-letter shortcuts.
    if [ "$VIEW" = plan ] && [ "$TYPED" != "$PLAN_DIGEST" ]; then
        case $k in
            bs) TYPED=${TYPED%?}; return ;; ctrl-u) TYPED=''; return ;;
            [0-9a-fA-F]) ((${#TYPED} < 64)) && TYPED+=${k,,}; return ;;
            ' '|-) return ;;
        esac
    fi
    if [ "$VIEW" = form ] || [ "$VIEW" = vmform ]; then
        local key=${FORM_KEYS[SEL]}
        case $k in
            up) ((SEL > 0)) && SEL=$((SEL - 1)); return ;;
            down) ((SEL < ${#FORM_KEYS[@]} - 1)) && SEL=$((SEL + 1)); return ;;
            bs) printf -v "$key" '%s' "${!key%?}"; return ;;
            ctrl-u) printf -v "$key" '%s' ''; return ;;
            enter|left|right|tab|btab|esc) ;;
            ?) [[ $k == [[:print:]] ]] && printf -v "$key" '%s' "${!key}$k"; return ;;
        esac
    fi
    case $k in
        l|L) OVERLAY=log; LOGSCROLL=0; return ;;
        '?') OVERLAY=help; return ;;
        q|Q|ctrl-c) if [ "$VIEW" = done ]; then QUIT=1; else OVERLAY=quit; FOCUS=0; fi; return ;;
    esac
    case $VIEW in hv|vmkit|where|net|tsnote|vmform|vmstep|vmsum) vm_key "$k"; return ;; esac
    case $VIEW in
        intro) case $k in left|right|tab|btab) btn_move "$k" ;; esc) OVERLAY=quit; FOCUS=0 ;;
                enter) if [ -n "$RESUME" ]; then
                        case $FOCUS in 0) resume_apply; start_guess ;; 1) RESUME=''; start_guess ;; 2) OVERLAY=quit; FOCUS=1 ;; esac
                    else case $FOCUS in 0) start_guess ;; 1) OVERLAY=quit; FOCUS=1 ;; esac; fi ;;
            esac ;;
        checks)
            case $k in
                up) ((SEL > 0)) && SEL=$((SEL - 1)) ;; down) ((SEL < ${#IT_TAG[@]} - 1)) && SEL=$((SEL + 1)) ;;
                left|right|tab|btab) btn_move "$k" ;;
                esc) VIEW=intro; FOCUS=0 ;;
                f|F) ((${#FIXABLE[@]})) && start_checks fix ;;
                r|R) start_checks look ;;
                enter)
                    case ${BTNS[FOCUS]} in
                        'Fix here') start_checks fix ;;
                        'Check again') start_checks look ;;
                        Continue) stage_set 1; STEP_NOTE[0]=$(check_note); FOCUS=0; caps_detect
                            if ((RECHECK)); then RECHECK=0; VIEW=form; SEL=0
                            elif ((SKIP_PARTS)); then VIEW=role; role_index "$ROLE"; SEL=$RI; else parts_defaults; VIEW=parts; SEL=0; fi ;;
                    esac ;;
            esac ;;
        role)
            case $k in
                up) ((SEL > 0)) && SEL=$((SEL - 1)) ;; down) ((SEL < 2)) && SEL=$((SEL + 1)) ;;
                1|2|3) SEL=$((k - 1)) ;;
                esc) if ((SKIP_PARTS)); then stage_set 0; VIEW=checks; SEL=0; else VIEW=parts; SEL=0; fi; FOCUS=0 ;;
                enter) [ "$ROLE" != "${ROLE_IDS[SEL]}" ] && { CONSENT=0; DIGEST=''; }
                    ROLE=${ROLE_IDS[SEL]}; form_build; VIEW=form; SEL=0; FOCUS=0 ;;
            esac ;;
        form)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                esc) VIEW=role; role_index "$ROLE"; SEL=$RI; FOCUS=0 ;;
                enter)
                    if ((FOCUS == 1)); then VIEW=role; role_index "$ROLE"; SEL=$RI; FOCUS=0; return; fi
                    form_valid
                    if [ -n "$FORM_ERR" ]; then STATUS_MSG=$FORM_ERR; return; fi
                    if [ "$ROLE" != "$CHECKED_ROLE" ]; then RECHECK=1; check_for_role "$ROLE"; start_checks look; return; fi
                    case ${STEP_STATE[2]} in skipped) ;; esac
                    [ "$ROLE" = headend ] && STEP_STATE[2]=skipped
                    [ "$ROLE" = workstation ] && STEP_STATE[4]=later
                    stage_set $([ "$ROLE" = headend ] && echo 3 || echo 2); VIEW=runintro; FOCUS=0 ;;
            esac ;;
        runintro)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                esc) stage_set 1; VIEW=form; SEL=0; FOCUS=0 ;;
                enter) if ((FOCUS == 1)); then stage_set 1; VIEW=form; FOCUS=0; return; fi
                    [ "$ROLE" = forge ] && { sudo_ready || { STATUS_MSG='sudo did not accept a password; the Forge steps need it.'; return; }; }
                    role_run ;;
            esac ;;
        plan)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                up) ((SCROLL > 0)) && SCROLL=$((SCROLL - 1)) ;; down) SCROLL=$((SCROLL + 1)) ;;
                pgup) SCROLL=$((SCROLL - 8)); ((SCROLL < 0)) && SCROLL=0 ;; pgdn) SCROLL=$((SCROLL + 8)) ;;
                esc) stage_set 1; VIEW=form; SEL=0; FOCUS=0 ;;
                enter) if [ "$TYPED" = "$PLAN_DIGEST" ] && ((FOCUS == 0)); then DIGEST=$TYPED; CONSENT=1; role_run
                    elif ((FOCUS == 1)); then stage_set 1; VIEW=form; FOCUS=0; fi ;;
            esac ;;
        pair)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                enter) case ${BTNS[FOCUS]} in Paired) PAIRED=1; finish ;; 'New code (runs the setup again)') PAIR_CODE=''; role_run ;; esac ;;
            esac ;;
        fail)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                r|R) retry ;;
                esc) back_from_fail ;;
                enter) case $FOCUS in 0) retry ;; 1) back_from_fail ;; 2) OVERLAY=log; LOGSCROLL=0 ;; esac ;;
            esac ;;
        parts)
            case $k in
                up) ((SEL > 0)) && SEL=$((SEL - 1)) ;; down) ((SEL < 2)) && SEL=$((SEL + 1)) ;;
                left|right) part_cycle "$k" ;;
                esc) stage_set 0; VIEW=checks; FOCUS=0; SEL=0 ;;
                enter) if ((FOCUS == 1)); then stage_set 0; VIEW=checks; FOCUS=0; SEL=0; else parts_done; fi ;;
                tab|btab) btn_move "$k" ;;
            esac ;;
        done)
            case $k in
                up) ((SCROLL > 0)) && SCROLL=$((SCROLL - 1)) ;; down) SCROLL=$((SCROLL + 1)) ;;
                pgup) SCROLL=$((SCROLL - 8)); ((SCROLL < 0)) && SCROLL=0 ;; pgdn) SCROLL=$((SCROLL + 8)) ;;
                left|right|tab|btab) btn_move "$k" ;;
                n|N) [ -n "$NEXT_OFFER" ] && continue_here "$NEXT_OFFER" ;;
                s|S) [[ " ${NEXT[*]} " == *' --send-disc2'* ]] && send_main back ;;
                enter) if [ -n "$NEXT_OFFER" ] && ((FOCUS == 0)); then continue_here "$NEXT_OFFER"; else QUIT=1; fi ;;
                esc) QUIT=1 ;;
            esac ;;
    esac
}

retry() {
    case $FAIL_STAGE in
        0) check_for_role "$CHECKED_ROLE"; start_checks look ;;
        *) if [ "$ROLE" = headend ]; then handover_connect; else role_run; fi ;;
    esac
}

back_from_fail() {
    stage_set 1; STEP_STATE[FAIL_STAGE]=pending; VIEW=form; SEL=0; FOCUS=0
    CONSENT=0; DIGEST=''; DL_NAME=(); DL_SIZE=(); DL_HAVE=(); DL_STATE=(); DL_RATE=(); DL_T=(); DL_B=()
}

start_guess() { stage_set 0; VIEW=checking; IT_TAG=(); IT_TEXT=(); ACTIVITY='Looking at this machine'; CHECKED_ROLE=${ROLE:-forge}; run_start guess --guess-role; }

resume_apply() {
    ROLE=${RS[role]}; F_ROOT=${RS[root]:-$F_ROOT}; F_NAME=${RS[name]:-$F_NAME}; W_HV=${RS[hv]:-$W_HV}
    H_URL=${RS[ws]:-$H_URL}; H_AGENT=${RS[agent]:-$H_AGENT}
    F_TZ=${RS[tz]:-$F_TZ}; F_USER=${RS[user]:-$F_USER}; F_CT=${RS[ct]:-$F_CT}
}

# ---------------------------------------------------------------------------------------------------------------
# 12. Start, the loop, and the way out.

screen_enter() {
    if ((REAL_TTY)); then
        stty -echo -icanon -ixon susp undef 2>/dev/null
        local junk; while IFS= read -rsn1 -t 0.01 junk; do :; done   # keys typed or pasted before the screen opened
    fi
    printf '\e[?1049h\e[?25l\e[2J'
    FULL=1; PREV=(); DIRTY=1
}

screen_leave() {
    printf '\e[0m\e[?2026l\e[?25h\e[?1049l'
    if ((REAL_TTY)) && [ -n "${STTY_SAVED:-}" ]; then stty "$STTY_SAVED" 2>/dev/null; fi
}

wiz_cleanup() {
    local st=1
    [ "${REACHED_DONE:-0}" = 1 ] && st=0
    ((st)) && ((${RUN_STATUS:-0})) && st=$RUN_STATUS
    [ "${INTERRUPTED:-0}" = 1 ] && st=130
    [ "$opt_diag" = decode ] && st=0
    trap - EXIT INT TERM HUP WINCH
    run_stop
    screen_leave
    if [ -n "${LOG_FILE:-}" ]; then
        if [ "${INTERRUPTED:-0}" = 1 ]; then printf 'Stopped by Ctrl+C. Run the wizard again to pick up where it stopped; finished downloads are kept.\n'; fi
        printf 'The setup log is in %s\n' "$LOG_FILE"
    fi
    if [ "${PLAIN_AFTER:-0}" = 1 ]; then exec "${BASH:-bash}" "$wiz_setup" "${PASS[@]}"; fi
    exit "$st"
}

wiz_defaults() {  # the settings form starts from these, or from the command line
    F_ROOT=${opt_data_root:-/opt/deep32}; F_NAME=${opt_name:-deep32-forge}; F_USER=deep32; F_KB=us; F_PROFILE=two-machines; F_CT=''
    F_TZ=Etc/UTC
    if [ -r /etc/timezone ]; then F_TZ=$(head -n1 /etc/timezone)
    elif [ -L /etc/localtime ]; then local t; t=$(readlink /etc/localtime); [[ $t == *zoneinfo/* ]] && F_TZ=${t##*zoneinfo/}; fi
    ((opt_demo)) && F_TZ=Europe/London && F_CT=yes
    W_HV=${opt_hv:-kvm}
    H_URL=${opt_ws:-}; H_AGENT=${opt_agent:-auto}
    ((opt_demo)) && [ -z "$H_URL" ] && H_URL='https://deep32-ws.tail4c2e.ts.net/mcp'
}

wiz_main() {
    ((opt_demo)) && [ -z "${DEEP32_WIZARD_OS:-}" ] && export DEEP32_WIZARD_OS=linux
    wiz_locale; wiz_glyphs; wiz_depth
    REAL_TTY=0; [ "${DEEP32_WIZARD_FORCE_TTY:-}" = 1 ] && [ -n "${DEEP32_WIZARD_KEYS+x}" ] || REAL_TTY=1
    [ -t 0 ] || REAL_TTY=0
    wiz_theme; wiz_palette
    err_load
    VERSION_ID=$(head -n1 "$wiz_repo/VERSION" 2>/dev/null || echo unknown)
    STATE_DIR=${XDG_STATE_HOME:-$HOME/.local/state}/deep32-setup
    ((opt_demo)) && STATE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/deep32-wizard-demo.XXXXXX")
    (umask 077; mkdir -p "$STATE_DIR") 2>/dev/null
    LOG_FILE="$STATE_DIR/wizard-$(date +%Y%m%d-%H%M%S).log"
    (umask 077; : >"$LOG_FILE") 2>/dev/null || LOG_FILE=''
    WORKDIR=$wiz_repo
    sudo_wrapper
    wiz_defaults
    wiz_state_init
    state_load
    if [ "$opt_diag" = decode ]; then
        local -a lines=(); local l
        while IFS= read -r l; do lines+=("${l%$'\r'}"); done <"$opt_diag_arg"
        local st='' ev
        for l in "${lines[@]}"; do [[ $l == '{"deep32":"exit"'* ]] && json_get "$l" && st+=" ${EV[tool]}:${EV[status]}"; done
        st+=" setup:${DEEP32_WIZARD_SETUP_STATUS:-1}"
        err_decode "$st" "${lines[@]}"
        local i; for i in "${!DEC_ROWS[@]}"; do printf '%s %s\n' "${ERR_KIND[${DEC_ROWS[i]}]}" "${ERR_KEY[${DEC_ROWS[i]}]}"; done
        rm -f "$LOG_FILE"; return 0
    fi
    if [ -n "${DEEP32_WIZARD_KEYS+x}" ]; then IFS=',' read -r -a KEYQ <<<"$DEEP32_WIZARD_KEYS"; fi
    if ((opt_demo)); then export DEEP32_WIZARD_DEMO_DIR DEEP32_WIZARD_DEMO_ROOT; DEEP32_WIZARD_DEMO_DIR=${DEEP32_WIZARD_DEMO_DIR:-'~/deep32-downloads'}; fi
    ((opt_vmguide)) && vm_begin
    STTY_SAVED=''; ((REAL_TTY)) && STTY_SAVED=$(stty -g 2>/dev/null)
    trap wiz_cleanup EXIT
    trap 'INTERRUPTED=1; QUIT=1' INT
    trap 'QUIT=1' TERM HUP
    trap 'RESIZED=1' WINCH
    wiz_size; layout
    screen_enter
    if [ "${opt_send:-0}" = 1 ]; then send_main direct; return; fi
    local last_anim=0
    while ((QUIT == 0)); do
        if ((RESIZED)); then RESIZED=0; wiz_size; layout; FULL=1; printf '\e[2J'; DIRTY=1; fi
        [ -n "$CHILD_PID" ] && run_drain
        ((QUIT)) && break
        dl_poll; sudo_keepalive; vm_tick
        case $VIEW in download|running) xfer_sample ;; esac
        wiz_now
        if ((NOW_MS - last_anim >= 120)); then
            last_anim=$NOW_MS; TICK=$((TICK + 1)); ((TICK % 4 == 0)) && PULSE=$((1 - PULSE))
            case $VIEW in checking|running|download|install|pairwait|signin|pair) DIRTY=1 ;; vmstep) case ${VS_STATE[VM_I]:-} in waiting|job) DIRTY=1 ;; esac ;; esac
        fi
        if ((REAL_TTY == 0)) && [ -z "${DEEP32_WIZARD_COLS:-}" ]; then :; elif ((REAL_TTY)) && [ -z "${DEEP32_WIZARD_COLS:-}" ] && ((TICK % 8 == 0)); then
            local ow=$W oh=$H; wiz_size; { [ "$ow" != "$W" ] || [ "$oh" != "$H" ]; } && { layout; FULL=1; printf '\e[2J'; DIRTY=1; }
        fi
        ((DIRTY)) && draw
        if [ -n "${DEEP32_WIZARD_KEYS+x}" ]; then
            if script_key; then on_key; else
                if [ -n "$CHILD_OUT" ]; then read -t 0.04 -u "$CHILD_OUT" -N 0 2>/dev/null; else sleep 0.04; fi
            fi
        else
            if read_key 0.04; then [ "$KEY" = ignore ] || on_key; fi
        fi
    done
    ((DIRTY)) && draw
}

wiz_size() {  # W and H: the terminal size (DEEP32_WIZARD_COLS/ROWS force it for tests and screenshots)
    if [ -n "${DEEP32_WIZARD_COLS:-}" ]; then
        [ -n "${W:-}" ] && [ "${SIZED:-0}" = 1 ] && return
        W=$DEEP32_WIZARD_COLS; H=${DEEP32_WIZARD_ROWS:-24}; SIZED=1; return
    fi
    local s; s=$(stty size 2>/dev/null </dev/tty) || s=''
    H=${s% *}; W=${s#* }
    [[ $W =~ ^[0-9]+$ ]] || W=${COLUMNS:-80}; [[ $H =~ ^[0-9]+$ ]] || H=${LINES:-24}
}

wiz_vm_plain_main() {  # --vm-guide without the full screen: the guide on plain lines
    opt_demo=${opt_demo:-0}
    STATE_DIR=${XDG_STATE_HOME:-$HOME/.local/state}/deep32-setup
    ((opt_demo)) && STATE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/deep32-wizard-demo.XXXXXX")
    (umask 077; mkdir -p "$STATE_DIR") 2>/dev/null
    LOG_FILE=''; LOG=(); VM_LINE=''
    ((opt_demo)) || { LOG_FILE="$STATE_DIR/vm-guide-$(date +%Y%m%d-%H%M%S).log"; (umask 077; : >"$LOG_FILE") 2>/dev/null || LOG_FILE=''; }
    vm_plain; local st=$?
    ((opt_demo)) && rm -rf -- "$STATE_DIR"
    return "$st"
}

# ---------------------------------------------------------------------------------------------------------------
# 13. Send disc 2 to a Workstation (--send-disc2; DISC2-NETWORK-DESIGN.md). This machine serves its verified disc 2
#     with Tailscale Serve: HTTPS, tailnet only, this one file at /d32-disc2-CODE/<file> on port 9447, where CODE is
#     ten characters from A-Z2-7 that the person types on the Workstation. The command is the one in
#     tailscale.com/kb/1242 (flags before the target; a file target needs an absolute path, and root or an
#     Administrator console, kb/1312). It runs without --bg, as the wizard's own child: kb/1242 keeps a --bg serve
#     "until you disable it", while this one ends with the wizard, so Enter, Q, Ctrl+C, a closed terminal or a crash
#     all end it. The wizard then checks that Tailscale no longer lists the path, and turns it off with kb/1242's
#     `off` form if it still does. Before it says the code, the wizard asks its own tailnet address for the file's
#     headers: the whole size must come back.

SEND_PORT=9447
SEND_ALPHABET=ABCDEFGHIJKLMNOPQRSTUVWXYZ234567
SEND_STEP_IDS=(disc2 tailscale serve probe sending done)
SEND_STEP_NAMES=('Disc 2 on this PC' 'Tailscale here' 'Start sending' 'Check the address' 'Workstation downloads' 'Stopped')
SEND_DEMO_JSON='{"BackendState":"Running","TailscaleIPs":["100.101.102.103"],"Self":{"DNSName":"vmhost.tail4c2e.ts.net.","TailscaleIPs":["100.101.102.103","fd7a:115c:a1e0::1"]},"CertDomains":["vmhost.tail4c2e.ts.net"]}'

send_os() {  # SEND_OS: linux, macos or windows (DEEP32_WIZARD_OS wins, as for the error table)
    case ${DEEP32_WIZARD_OS:-$(uname -s 2>/dev/null)} in
        macos|Darwin) SEND_OS=macos ;; windows|MINGW*|MSYS*|CYGWIN*) SEND_OS=windows ;; *) SEND_OS=linux ;;
    esac
}

send_code() {  # a new code into SEND_CODE: ten symbols from A-Z2-7, one random byte each (32 divides 256: no bias)
    local b c=''
    if ((opt_demo)); then SEND_CODE=R4NDQ7KX2M; return 0; fi
    for b in $(od -An -tu1 -N10 /dev/urandom 2>/dev/null); do c+=${SEND_ALPHABET:b % 32:1}; done
    if ((${#c} != 10)); then c=''; while ((${#c} < 10)); do c+=${SEND_ALPHABET:${SRANDOM:-$RANDOM} % 32:1}; done; fi
    SEND_CODE=$c
}

send_pretty() { SEND_SHOWN="${1:0:5}-${1:5:5}"; }   # a code as the person reads and types it: XXXXX-XXXXX

send_ts_parse() {  # send_ts_parse JSON: from `tailscale status --json`, SEND_STATE (BackendState), SEND_NAME (this
                   # machine's MagicDNS name), SEND_SHORT (its first label, the name the Workstation lists), SEND_IP
                   # (its tailnet IPv4) and SEND_CERT (1 when the tailnet has HTTPS certificates on)
    local j=$1 self='' ips re
    SEND_STATE=''; SEND_NAME=''; SEND_SHORT=''; SEND_IP=''; SEND_CERT=0
    re='"BackendState"[[:space:]]*:[[:space:]]*"([A-Za-z]*)"'; [[ $j =~ $re ]] && SEND_STATE=${BASH_REMATCH[1]}
    [[ $j == *'"Self"'* ]] && { self=${j#*'"Self"'}; self=${self%%'"Peer"'*}; }
    re='"DNSName"[[:space:]]*:[[:space:]]*"([^"]*)"'; [[ $self =~ $re ]] && SEND_NAME=${BASH_REMATCH[1]%.}
    re='"TailscaleIPs"[[:space:]]*:[[:space:]]*\[([^]]*)\]'
    if [[ $self =~ $re ]]; then
        ips=${BASH_REMATCH[1]}; re='"([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})"'
        [[ $ips =~ $re ]] && SEND_IP=${BASH_REMATCH[1]}
    fi
    re='"CertDomains"[[:space:]]*:[[:space:]]*\[[[:space:]]*"'; [[ $j =~ $re ]] && SEND_CERT=1
    SEND_SHORT=${SEND_NAME%%.*}
}

send_busy_in() {  # send_busy_in JSON: 0 when `tailscale serve status --json` uses port 9447; SEND_WHY says by what
    local j=$1
    [[ $j == *"\"$SEND_PORT\""* || $j == *":$SEND_PORT\""* ]] || return 1
    if [[ $j == *'/d32-disc2-'* ]]; then SEND_WHY="Tailscale already serves a disc 2 on port $SEND_PORT, from another Send disc 2"
    else SEND_WHY="Tailscale already serves something else on port $SEND_PORT"; fi
    return 0
}

send_classify() {  # send_classify TEXT: the code for what `tailscale serve` printed when it stopped, into SEND_CLASS
    local t=${1,,}
    case $t in
        *'must be root'*|*operator*|*'access denied'*|*'serve config denied'*|*'local admin'*|*administrator*|*'permission denied'*)
            SEND_CLASS=send-needs-root ;;
        *'already in use'*|*'address in use'*|*'already serving'*|*'port is in use'*) SEND_CLASS=send-port-busy ;;
        *) SEND_CLASS=send-serve-refused ;;
    esac
}

send_head_check() {  # send_head_check HEADERS SIZE: 0 when they show the whole file (200 with its length, or 206 with
                     # its total), 1 when a server answered with something else, 2 when nothing usable answered yet;
                     # SEND_GOT says what came back
    local l st='' len='' tot='' last='' re1='^http/[0-9.]+ ([0-9]{3})' re2='^content-length:[[:space:]]*([0-9]+)'
    local re3='^content-range:[[:space:]]*bytes [0-9]+-[0-9]+/([0-9]+)'
    while IFS= read -r l; do
        l=${l%$'\r'}; [ -n "${l//[[:space:]]/}" ] && last=$l; l=${l,,}
        if [[ $l =~ $re1 ]]; then st=${BASH_REMATCH[1]}; len=''; tot=''
        elif [[ $l =~ $re2 ]]; then len=${BASH_REMATCH[1]}
        elif [[ $l =~ $re3 ]]; then tot=${BASH_REMATCH[1]}; fi
    done <<<"$1"
    if [ -z "$st" ]; then SEND_GOT=${last:-no answer}; return 2; fi
    case $st in
        200) SEND_GOT="HTTP 200 with Content-Length ${len:-none}; disc 2 has $2 bytes"; [ "$len" = "$2" ] && return 0; return 1 ;;
        206) SEND_GOT="HTTP 206 for ${tot:-an unknown number of} bytes in all; disc 2 has $2 bytes"; [ "$tot" = "$2" ] && return 0; return 1 ;;
        *) SEND_GOT="HTTP $st"; return 2 ;;
    esac
}

send_locate() {  # the verified disc 2 in this machine's download folder (deep32-get publishes it only once it is
                 # verified): SEND_FILE, SEND_DIR, SEND_ISO and SEND_SIZE, or SEND_WHY when it is not there
    SEND_FILE="deep32-${VERSION_ID%$'\r'}-disc2-content.iso"; SEND_WHY=''; SEND_SIZE=0
    SEND_DIR="$(cd -- "$wiz_repo/.." && pwd -P)/deep32-downloads"
    if ((opt_demo)); then SEND_DIR=${DEEP32_WIZARD_DEMO_DIR:-'~/deep32-downloads'}; SEND_ISO="$SEND_DIR/$SEND_FILE"; SEND_SIZE=19638284288; return 0; fi
    SEND_ISO="$SEND_DIR/$SEND_FILE"
    if [ ! -f "$SEND_ISO" ]; then
        if [ -e "$SEND_ISO.download" ]; then SEND_WHY="disc 2 is not finished in $(short_path "$SEND_DIR"): only its partial download is there"
        else SEND_WHY="no $SEND_FILE in $(short_path "$SEND_DIR")"; fi
        return 1
    fi
    file_size "$SEND_ISO"; SEND_SIZE=${FS:-0}
    local m best='' name size
    for m in "$SEND_DIR"/.deep32-*/revision-*/MIRRORS.json; do [ -r "$m" ] && best=$m; done
    if [ -n "$best" ]; then
        while read -r name size; do
            [ "$name" = "$SEND_FILE" ] && [ "$size" != "$SEND_SIZE" ] &&
                { SEND_WHY="$SEND_FILE has $SEND_SIZE bytes; the signed release says $size"; return 1; }
        done < <(mirrors_files "$best" disc2 '')
    fi
    ((SEND_SIZE > 0)) || { SEND_WHY="$SEND_FILE is empty"; return 1; }
}

send_admin() {  # 0 when Tailscale lets this session serve a file (kb/1312: root on Linux, an Administrator console on
                # Windows); SEND_WHO says why (empty when only Tailscale can tell), SEND_WHY why not
    SEND_WHY=''; SEND_WHO='Runs as root, so Tailscale may serve the file'
    [ "$SEND_OS" = windows ] && SEND_WHO='Runs as administrator, so Tailscale may serve the file'
    if ((opt_demo)) || [ "${DEEP32_WIZARD_TEST_ADMIN:-}" = 1 ]; then return 0; fi
    if [ "$SEND_OS" = windows ]; then SEND_WHO=''; return 0; fi   # Git for Windows' bash: Tailscale says it if not elevated
    [ "$(id -u)" = 0 ] && return 0
    local me prefs re='"OperatorUser"[[:space:]]*:[[:space:]]*"([^"]*)"'
    me=$(id -un 2>/dev/null)
    prefs=$("$SEND_TS" debug prefs 2>/dev/null) || { SEND_WHO=''; return 0; }   # cannot tell: Tailscale decides
    if [[ $prefs =~ $re ]] && [ -n "$me" ] && [ "${BASH_REMATCH[1]}" = "$me" ]; then
        SEND_WHO="Your user $me operates Tailscale, so it may serve the file"; return 0
    fi
    SEND_WHY="this runs as $me, not as root, and $me is not the Tailscale operator"
    return 1
}

send_port_busy() {  # 0 when port 9447 is taken here, by a Tailscale serve or a listening program; SEND_WHY says which
    local sj
    sj=$("$SEND_TS" serve status --json 2>/dev/null) || sj=''
    send_busy_in "$sj" && return 0
    if [ "$SEND_OS" = linux ] && command -v ss >/dev/null 2>&1 && [ -n "$(ss -Hltn "sport = :$SEND_PORT" 2>/dev/null)" ]; then
        SEND_WHY="a program on this machine listens on port $SEND_PORT"; return 0
    fi
    return 1
}

send_tail() {  # send_tail FILE: its last three lines that say something, into SEND_LINES
    SEND_LINES=()
    local l; local -a all=()
    [ -r "$1" ] && while IFS= read -r l; do l=${l%$'\r'}; [ -n "${l//[[:space:]]/}" ] && all+=("$l"); done <"$1"
    ((${#all[@]} > 3)) && all=("${all[@]: -3}")
    SEND_LINES=("${all[@]}")
}

send_item() {  # send_item TAG TEXT: one line of the send's progress (printed with its tag in plain mode)
    SEND_TAG+=("$1"); SEND_TEXT+=("$2"); wiz_log "[$1] $2"
    ((SEND_PLAIN)) && printf '[%s] %s\n' "$1" "$2"
    DIRTY=1
}

send_fail() {  # send_fail CODE [LINE]...: stop everything this send started and say why, from the error table
    local code=$1 j l; shift
    send_stop
    SEND_PHASE=failed; SEND_FAIL_CODE=$code; SEND_FAIL_LINES=("$@"); SEND_ROW=-1
    for ((j = 0; j < ${#ERR_KIND[@]}; j++)); do [ "${ERR_KIND[j]}" = code ] && [ "${ERR_KEY[j]}" = "$code" ] && { SEND_ROW=$j; break; }; done
    STEP_STATE[STAGE]=failed; VIEW=sendfail; FOCUS=0; DIRTY=1
    for l in "$@"; do wiz_log "$l"; done
    wiz_log "[fail] Send disc 2 stopped (code: $code)"
    ((SEND_PLAIN)) || return 0
    for l in "$@"; do printf '[info] %s\n' "$l"; done
    if ((SEND_ROW >= 0)); then
        printf '[fail] %s (code: %s)\n' "${ERR_MEAN[SEND_ROW]}" "$code"
        err_steps "$SEND_ROW"; for l in "${STEPS[@]}"; do printf '[you] %s\n' "${l//'`'/}"; done
    else printf '[fail] Send disc 2 stopped (code: %s)\n' "$code"; fi
}

send_gone() {  # after the serve ended: Tailscale must no longer list this send's path; if it does, turn it off
    ((${SEND_LIVE:-0})) || return 0
    SEND_LIVE=0; SEND_LEFT=0
    ((opt_demo)) && return 0
    local sj i
    for ((i = 0; i < 3; i++)); do
        sj=$("$SEND_TS" serve status --json 2>/dev/null) || sj=''
        [[ $sj == *"$SEND_PATH"* ]] || return 0
        sleep 0.5
    done
    wiz_log "--- send disc 2: Tailscale still lists $SEND_PATH; turning it off"
    MSYS2_ARG_CONV_EXCL='*' "$SEND_TS" serve --yes "--https=$SEND_PORT" "--set-path=$SEND_PATH" off </dev/null >/dev/null 2>&1
    sj=$("$SEND_TS" serve status --json 2>/dev/null) || sj=''
    [[ $sj == *"$SEND_PATH"* ]] && SEND_LEFT=1
    return 0
}

send_stop() {  # end the self-check and the serve, then make sure Tailscale no longer lists the path (safe to repeat)
    local i
    if [ -n "${SEND_PROBE_PID:-}" ]; then kill -TERM "$SEND_PROBE_PID" 2>/dev/null; wait "$SEND_PROBE_PID" 2>/dev/null; SEND_PROBE_PID=''; fi
    if [ -n "${SEND_PID:-}" ]; then
        kill -TERM "$SEND_PID" 2>/dev/null
        for ((i = 0; i < 30; i++)); do kill -0 "$SEND_PID" 2>/dev/null || break; sleep 0.1; done
        kill -KILL "$SEND_PID" 2>/dev/null; wait "$SEND_PID" 2>/dev/null
        SEND_PID=''; wiz_log '--- send disc 2: the serve ended'
    fi
    send_gone
    [ -n "${SEND_TMP:-}" ] && rm -rf -- "$SEND_TMP"
    SEND_TMP=''
}

send_spawn() {  # make the code and start the serve as this wizard's own child
    send_code; send_pretty "$SEND_CODE"
    SEND_PATH="/d32-disc2-$SEND_CODE/$SEND_FILE"; SEND_URL="https://$SEND_NAME:$SEND_PORT$SEND_PATH"; SEND_LIVE=1
    ((opt_demo)) && return 0
    SEND_TMP=$(mktemp -d "${TMPDIR:-/tmp}/deep32-send.XXXXXX" 2>/dev/null) || SEND_TMP=''
    local iso=$SEND_ISO out=/dev/null; local -a pre=()
    [ -n "$SEND_TMP" ] && out=$SEND_TMP/serve.out
    [ "$SEND_OS" = windows ] && command -v cygpath >/dev/null 2>&1 && iso=$(cygpath -w -- "$iso")
    # On Linux setpriv ties the serve to the wizard: if the wizard is killed outright, the serve gets TERM as well.
    setpriv --pdeathsig TERM -- true >/dev/null 2>&1 && pre=(setpriv --pdeathsig TERM --)
    wiz_log "--- $(date '+%Y-%m-%d %H:%M:%S') tailscale serve --yes --https=$SEND_PORT --set-path=$SEND_PATH $iso"
    MSYS2_ARG_CONV_EXCL='*' "${pre[@]}" "$SEND_TS" serve --yes "--https=$SEND_PORT" "--set-path=$SEND_PATH" "$iso" </dev/null >"$out" 2>&1 &
    SEND_PID=$!
}

send_probe() {  # ask this machine's own tailnet address for the file's headers, in the background
    local -a c=(curl -sS -I --max-time 20)
    [ -n "$SEND_IP" ] && c+=(--resolve "$SEND_NAME:$SEND_PORT:$SEND_IP")
    "${c[@]}" "$SEND_URL" >"${SEND_TMP:-/tmp}/probe.out" 2>&1 &
    SEND_PROBE_PID=$!
}

send_died() {  # the serve ended by itself: say what Tailscale printed
    wait "$SEND_PID" 2>/dev/null; SEND_PID=''
    send_tail "$SEND_TMP/serve.out"
    ((${#SEND_LINES[@]})) || SEND_LINES=('tailscale serve ended without a message')
    send_classify "${SEND_LINES[*]}"
    send_fail "$SEND_CLASS" "${SEND_LINES[@]}"
}

send_ready() {  # the self-check passed: show the code and what to do on the Workstation
    SEND_PHASE=sending; stage_set 4; STEP_NOTE[4]="code $SEND_SHOWN"; VIEW=sending; FOCUS=0
    wiz_now; SEND_SINCE=$NOW_MS
    [ "$SEND_HOW" = direct ] && REACHED_DONE=1
    local say="On the Workstation, choose Get Disc 2 over the Network, pick $SEND_SHORT, type $SEND_SHOWN"
    wiz_log "[you] $say"
    ((SEND_PLAIN)) && printf '[you] %s\n[info] %s\n' "$say" 'Sending; press Enter to stop (Ctrl+C also stops it and turns the serve off)'
    return 0
}

send_check_next() {  # the checks, one per step of the loop, so each shows as it passes
    case $SEND_K in
        0) stage_set 0
           send_locate || { send_fail send-no-disc2 "$SEND_WHY"; return; }
           fmt_bytes "$SEND_SIZE"; STEP_NOTE[0]=$FMT
           send_item ok "Disc 2 is here: $SEND_FILE, $FMT" ;;
        1) stage_set 1
           if ((opt_demo)); then send_ts_parse "$SEND_DEMO_JSON"
           else
               SEND_TS=$(command -v tailscale 2>/dev/null) || SEND_TS=''
               [ -n "$SEND_TS" ] || { send_fail send-no-tailscale 'tailscale: command not found'; return; }
               local js; js=$("$SEND_TS" status --json 2>&1)
               send_ts_parse "$js"
               [ "$SEND_STATE" = Running ] || { send_fail send-signed-out "Tailscale says: ${SEND_STATE:-${js%%$'\n'*}}"; return; }
           fi
           STEP_NOTE[1]=$SEND_SHORT
           send_item ok "Tailscale is signed in here as $SEND_SHORT ($SEND_NAME)" ;;
        2) if [ -z "$SEND_NAME" ] || ((SEND_CERT == 0)); then
               send_fail send-serve-refused 'tailscale status lists no HTTPS certificate domain: MagicDNS or HTTPS certificates are off'; return
           fi
           send_item ok 'HTTPS certificates are on in your tailnet' ;;
        3) send_admin || { send_fail send-needs-root "$SEND_WHY"; return; }
           [ -n "$SEND_WHO" ] && send_item ok "$SEND_WHO" ;;
        4) stage_set 2
           if ((opt_demo == 0)) && send_port_busy; then send_fail send-port-busy "$SEND_WHY"; return; fi
           STEP_NOTE[2]="port $SEND_PORT"
           send_item ok "Port $SEND_PORT is free here" ;;
        5) send_spawn
           send_item info "Sending with Tailscale Serve on port $SEND_PORT, tailnet only"
           stage_set 3; SEND_PHASE=probe; SEND_LAST='no answer yet'
           wiz_now; SEND_UNTIL=$((NOW_MS + 90000)); SEND_NEXT=$((NOW_MS + 700)) ;;
    esac
    SEND_K=$((SEND_K + 1))
}

send_probe_tick() {  # the self-check: try the own address until the whole file answers, or give up after 90 s
    local text sz
    fmt_bytes "$SEND_SIZE"; sz=$FMT
    if ((opt_demo)); then send_item ok "This machine answers at its own address with the whole disc 2 ($sz)"; send_ready; return; fi
    if [ -n "$SEND_PID" ] && ! kill -0 "$SEND_PID" 2>/dev/null; then send_died; return; fi
    wiz_now
    if [ -n "${SEND_PROBE_PID:-}" ]; then
        kill -0 "$SEND_PROBE_PID" 2>/dev/null && return 0
        wait "$SEND_PROBE_PID" 2>/dev/null; SEND_PROBE_PID=''
        text=$(cat "${SEND_TMP:-/tmp}/probe.out" 2>/dev/null)
        send_head_check "$text" "$SEND_SIZE"
        case $? in
            0) send_item ok "This machine answers at its own address with the whole disc 2 ($sz)"; send_ready; return ;;
            1) send_fail send-self-check "$SEND_URL" "$SEND_GOT"; return ;;
        esac
        SEND_LAST=$SEND_GOT
        if ((NOW_MS >= SEND_UNTIL)); then
            send_tail "$SEND_TMP/serve.out"
            case "${SEND_LINES[*],,}" in
                *login.tailscale.com*|*'not enabled'*|*'to enable'*) send_fail send-serve-refused "${SEND_LINES[@]}" ;;
                *) send_fail send-self-check "$SEND_URL" "no whole answer in 90 s; the last: $SEND_LAST" ;;
            esac
            return
        fi
        SEND_NEXT=$((NOW_MS + 2000)); return 0
    fi
    if ((NOW_MS >= SEND_NEXT)); then
        command -v curl >/dev/null 2>&1 || { send_fail send-self-check 'curl is not installed here; the self-check needs it'; return; }
        send_probe
    fi
}

send_tick() {  # one step of the send: the checks in turn, the self-check, then the watch while it sends
    case $SEND_PHASE in checks|probe|sending) ;; *) return 0 ;; esac
    wiz_now
    ((NOW_MS < SEND_DUE)) && return 0
    ((opt_demo)) && SEND_DUE=$((NOW_MS + 450))
    case $SEND_PHASE in
        checks) send_check_next ;;
        probe) send_probe_tick ;;
        sending) [ -n "${SEND_PID:-}" ] && ! kill -0 "$SEND_PID" 2>/dev/null && send_died ;;
    esac
    DIRTY=1
    return 0
}

send_begin() {  # start (or start again) from the first check
    send_stop
    send_os
    SEND_TAG=(); SEND_TEXT=(); SEND_K=0; SEND_PHASE=checks; SEND_DUE=0; SEND_FAIL_CODE=''; SEND_FAIL_LINES=(); SEND_ROW=-1
    SEND_CODE=''; SEND_SHOWN=''; SEND_PATH=''; SEND_LEFT=0; SEND_LIVE=0; SEND_PID=''; SEND_PROBE_PID=''
    STEP_STATE=(current pending pending pending pending pending); STEP_NOTE=('' '' '' '' '' ''); STAGE=0
    VIEW=send; FOCUS=0; DIRTY=1
    wiz_log "--- $(date '+%Y-%m-%d %H:%M:%S') send disc 2"
}

send_finish() {  # Enter while sending: stop, and say that the serve is off
    send_stop
    SEND_PHASE=stopped; stage_set 5; VIEW=sent; FOCUS=0
    if ((SEND_LEFT)); then
        send_item fail "Tailscale still lists the path; turn it off: \`tailscale serve --https=$SEND_PORT --set-path=$SEND_PATH off\`"
    else send_item ok 'Sending stopped; the serve is off and the code no longer works'; fi
}

send_plain() {  # --send-disc2 without the full screen: the same steps as tagged lines; Enter (or Ctrl+C) stops
    ((opt_demo)) && [ -z "${DEEP32_WIZARD_OS:-}" ] && export DEEP32_WIZARD_OS=linux
    wiz_state_init; LOG_FILE=''; REAL_TTY=0
    VERSION_ID=$(head -n1 "$wiz_repo/VERSION" 2>/dev/null || echo unknown)
    err_load
    SEND_PLAIN=1; SEND_HOW=plain
    trap 'send_stop' EXIT; trap 'exit 130' INT; trap 'exit 143' TERM HUP
    printf '[info] %s\n' 'Send disc 2 to a Workstation: this machine shares its verified disc 2 over your tailnet while this runs'
    send_begin
    while [ "$SEND_PHASE" = checks ] || [ "$SEND_PHASE" = probe ]; do send_tick; sleep 0.05; done
    [ "$SEND_PHASE" = sending ] || return 1
    local line rc
    while [ "$SEND_PHASE" = sending ]; do
        IFS= read -r -t 1 line; rc=$?
        ((rc == 0)) && break
        if ((rc <= 128)); then   # the input ended: the demo and a terminal stop; anything else sends until Ctrl+C
            { ((opt_demo)) || [ -t 0 ]; } && break
            sleep 1
        fi
        send_tick
    done
    [ "$SEND_PHASE" = sending ] || return 1
    send_finish
    ((SEND_LEFT)) && return 1
    return 0
}

# The full screen: the wizard's own header, rail, footer and dialogs, with the send's six steps on the rail and its
# own loop, so it opens from the command line (--send-disc2) or over the Done screen (S) and goes back there.

send_main() {  # send_main direct|back
    SEND_HOW=$1
    local -a k_ids=("${STEP_IDS[@]}") k_names=("${STEP_NAMES[@]}") k_state=("${STEP_STATE[@]}") k_note=("${STEP_NOTE[@]}")
    local k_stage=$STAGE k_view=$VIEW k_focus=$FOCUS k_scroll=$SCROLL k_pair=$PAIR_CODE k_trap old='' last_anim=0
    k_trap=$(trap -p EXIT)
    [ -n "$k_trap" ] && { eval "set -- $k_trap"; old=$3; }
    trap "send_stop; $old" EXIT   # a crash or an exit while sending still ends the serve
    STEP_IDS=("${SEND_STEP_IDS[@]}"); STEP_NAMES=("${SEND_STEP_NAMES[@]}"); PAIR_CODE=''
    SEND_PLAIN=0; SEND_DONE=0; OVERLAY=''; STATUS_MSG=''
    send_begin
    while ((QUIT == 0 && SEND_DONE == 0)); do
        if ((RESIZED)); then RESIZED=0; wiz_size; layout; FULL=1; printf '\e[2J'; DIRTY=1; fi
        send_tick
        wiz_now
        if ((NOW_MS - last_anim >= 120)); then
            last_anim=$NOW_MS; TICK=$((TICK + 1)); ((TICK % 4 == 0)) && PULSE=$((1 - PULSE))
            case $SEND_PHASE in checks|probe|sending) DIRTY=1 ;; esac
            if ((REAL_TTY)) && [ -z "${DEEP32_WIZARD_COLS:-}" ] && ((TICK % 8 == 0)); then
                local ow=$W oh=$H; wiz_size; { [ "$ow" != "$W" ] || [ "$oh" != "$H" ]; } && { layout; FULL=1; printf '\e[2J'; DIRTY=1; }
            fi
        fi
        ((DIRTY)) && send_draw
        if [ -n "${DEEP32_WIZARD_KEYS+x}" ]; then
            if script_key; then send_key; else sleep 0.04; fi
        elif read_key 0.04; then [ "$KEY" = ignore ] || send_key; fi
    done
    send_stop
    STEP_IDS=("${k_ids[@]}"); STEP_NAMES=("${k_names[@]}"); STEP_STATE=("${k_state[@]}"); STEP_NOTE=("${k_note[@]}")
    STAGE=$k_stage; VIEW=$k_view; FOCUS=$k_focus; SCROLL=$k_scroll; PAIR_CODE=$k_pair; OVERLAY=''; STATUS_MSG=''
    if [ -n "$k_trap" ]; then eval "$k_trap"; else trap - EXIT; fi
    FULL=1; DIRTY=1
}

send_leave() { if [ "$SEND_HOW" = direct ]; then QUIT=1; else SEND_DONE=1; fi; }

send_key() {  # one key on the send screen
    local k=$KEY
    DIRTY=1; STATUS_MSG=''
    if ((W < 80 || H < 24)); then case $k in q|Q|ctrl-c) QUIT=1 ;; esac; return; fi
    case $OVERLAY in
        log) case $k in up) LOGSCROLL=$((LOGSCROLL + 1)) ;; down) ((LOGSCROLL > 0)) && LOGSCROLL=$((LOGSCROLL - 1)) ;;
                pgup) LOGSCROLL=$((LOGSCROLL + CH - 5)) ;; pgdn) LOGSCROLL=$((LOGSCROLL - CH + 5)); ((LOGSCROLL < 0)) && LOGSCROLL=0 ;;
                home) LOGSCROLL=${#LOG[@]} ;; end) LOGSCROLL=0 ;; esc|l|L|q|Q|enter) OVERLAY='' ;; esac
            return ;;
        help) OVERLAY=''; return ;;
        quit) case $k in left|right|tab|btab) btn_move "$k" ;; esc) OVERLAY=''; FOCUS=0 ;;
                enter) if ((FOCUS == 1)); then QUIT=1; else OVERLAY=''; FOCUS=0; fi ;; q|Q) QUIT=1 ;; esac
            return ;;
    esac
    case $k in
        l|L) OVERLAY=log; LOGSCROLL=0; return ;;
        '?') OVERLAY=help; return ;;
        q|Q|ctrl-c) if [ "$SEND_PHASE" = stopped ]; then QUIT=1; else OVERLAY=quit; FOCUS=0; fi; return ;;
    esac
    case $SEND_PHASE in
        sending) case $k in enter) send_finish ;; esc) STATUS_MSG='Enter stops sending; Q quits.' ;; esac ;;
        stopped|failed)
            case $k in
                left|right|tab|btab) btn_move "$k" ;;
                r|R) send_begin ;;
                esc) send_leave ;;
                enter) case ${BTNS[FOCUS]} in 'Send again'|'Try again') send_begin ;; Close|Back) send_leave ;;
                        'Show the log') OVERLAY=log; LOGSCROLL=0 ;; esac ;;
            esac ;;
    esac
}

send_draw() {  # write the lines that changed, as draw does, with the send screen in the panel
    send_frame
    local i out=''
    for ((i = 0; i < H; i++)); do
        [ "${FRAME[i]}" = "${PREV[i]:-}" ] && ((FULL == 0)) && continue
        out+=$'\e['"$((i + 1));1H${FRAME[i]}"
    done
    PREV=("${FRAME[@]}"); FULL=0
    if [ -n "$SNAP" ]; then out+=$'\e]7777;snap;'"$SNAP"$'\a'; SNAP=''; fi
    [ -n "$out" ] && printf '%s' $'\e[?2026h'"$out"$'\e[?2026l'
    DIRTY=0
}

send_frame() {  # the wizard's frame (header, rail, footer, log and help) with the send screen in the panel
    frame
    if ((W < 80 || H < 24)); then send_small; return; fi
    case $OVERLAY in log|help) return ;; esac
    send_foot; FRAME[H - 1]=$FOOT
    PANEL=()
    view_send
    [ "$OVERLAY" = quit ] && send_quit_box
    pfill
    local i
    for ((i = 0; i < CH; i++)); do FRAME[BOX_TOP + 1 + i]="  $S_BORDER$G_V$S_0${RAIL[i]}$S_BORDER$G_V$S_0${PANEL[i]:0}$S_BORDER$G_V$S_0  "; done
}

send_small() {  # the small-window note says what Q does here
    local msg='Press Q to quit.' r=$((H / 2 - 2)) t pad
    case $SEND_PHASE in checks|probe|sending) msg='Sending goes on. Make the window bigger to see it, or press Q to stop and quit.' ;; esac
    ((r < 0)) && r=0; ((r + 3 < H)) || return 0
    t=$msg; ((${#t} > W)) && t=${t:0:W}
    pad=$(( (W - ${#t}) / 2 )); spaces "$pad"; local a=$SP; spaces $((W - pad - ${#t}))
    FRAME[r + 3]="$a$S_MUTED$t$S_0$SP"
}

send_foot() {  # FOOT: the keys of the send screen
    local -a keys=(L log '?' help Q quit)
    local back=back; [ "$SEND_HOW" = direct ] && back=close
    case $SEND_PHASE in
        sending) keys=(Enter 'stop sending' L log Q quit) ;;
        stopped|failed) keys=('Left/Right' choose Enter confirm Esc "$back" L log Q quit) ;;
    esac
    local i out='   ' w=3
    for ((i = 0; i < ${#keys[@]}; i += 2)); do
        out+="$S_B${keys[i]}$S_0 $S_MUTED${keys[i+1]}$S_0    "; w=$((w + ${#keys[i]} + ${#keys[i+1]} + 5))
    done
    if ((w > W)); then out='   '; w=3; fi
    spaces $((W - w)); FOOT="$out$SP"
}

view_send() {  # the checks as they pass; then the code and what to do on the Workstation
    case $SEND_PHASE in
        failed) send_view_fail; return ;;
        sending) send_view_code; return ;;
        stopped) ptitle 'Sending stopped' 'The serve is off and the code no longer works. Disc 2 stays in the download folder; send it again whenever you like.' "$G_OK" "$S_OK" ;;
        *) ptitle 'Send disc 2 to a Workstation' 'This machine shares its verified disc 2 with your Workstation over your tailnet while this screen stays open.' ;;
    esac
    local i k l rows=$((CH - ${#PANEL[@]} - 4)) from
    [ "$SEND_PHASE" = probe ] && rows=$((rows - 2))
    local -a head=("${PANEL[@]}") body=()
    PANEL=()
    for ((i = 0; i < ${#SEND_TAG[@]}; i++)); do   # each item wrapped; the newest stay when they do not all fit
        item_icon "${SEND_TAG[i]}"; wrap $((CW - 4)) "${SEND_TEXT[i]}"; k=0
        for l in "${WRAPPED[@]}"; do
            pl; if ((k == 0)); then pa '' ' '; pa "$IS" "$II"; pa '' '  '; else pa '' '    '; fi
            if [ "${SEND_TAG[i]}" = fail ]; then pa '' "$l"; else pa "$S_MUTED" "$l"; fi
            pe; k=1
        done
    done
    body=("${PANEL[@]}"); PANEL=("${head[@]}")
    from=$(( ${#body[@]} > rows ? ${#body[@]} - rows : 0 ))
    PANEL+=("${body[@]:from}")
    if [ "$SEND_PHASE" = stopped ]; then
        local back=Back; [ "$SEND_HOW" = direct ] && back=Close
        pbottom 1; BTNS=('Send again' "$back"); pbuttons "${BTNS[@]}"
        return
    fi
    pblank
    [ "$SEND_PHASE" = probe ] && ppara "$S_FAINT" 'The first HTTPS start on this machine can take a minute while Tailscale gets its certificate.'
    pbottom 1
    local act='Checking'; [ "$SEND_PHASE" = probe ] && act='Checking that this machine answers at its own address'
    pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_MUTED" "  $act"; pe
}

send_view_code() {  # sending: the code, the one line to follow on the Workstation, and the time so far
    ptitle 'Send disc 2 to a Workstation' "Sending on port $SEND_PORT, to your tailnet only." "$G_OK" "$S_OK"
    local inner=$(( ${#SEND_SHOWN} + 6 )) hz
    printf -v hz '%*s' "$inner" ''; hz=${hz// /$G_H}
    pl; pa '' '   '; pa "$S_ACCENT" "$G_TL$hz$G_TR"; pe
    pl; pa '' '   '; pa "$S_ACCENT" "$G_V"; pa "$S_CHIPBG" '   '; pa "$S_CHIPBG$S_B$S_ACCENTT" "$SEND_SHOWN"; pa "$S_CHIPBG" '   '; pa "$S_ACCENT" "$G_V"; pe
    pl; pa '' '   '; pa "$S_ACCENT" "$G_BL$hz$G_BR"; pe
    pblank
    ppara "$S_B" "On the Workstation, choose Get Disc 2 over the Network, pick $SEND_SHORT, type $SEND_SHOWN"
    pblank
    psteps 2 "Get Disc 2 over the Network is on First Start. From the Workstation's terminal: \`doas /usr/local/d32/sbin/d32-content-fetch\`" \
        'Keep this screen open until the Workstation says the download is complete; Enter then stops sending.'
    while ((${#PANEL[@]} > CH - 3)); do pdrop; done
    pbottom 2
    wiz_now; fmt_clock $(( (NOW_MS - SEND_SINCE) / 1000 )); local spent=$FMT
    fmt_bytes "$SEND_SIZE"
    pl; pa "$S_ACCENT" "${SPIN[TICK % ${#SPIN[@]}]}"; pa "$S_MUTED" "  Sending  $G_DOT  $spent so far  $G_DOT  $FMT  $G_DOT  port $SEND_PORT"; pe
    BTNS=('Stop sending'); pbuttons "${BTNS[@]}"
}

send_view_fail() {  # stopped on a problem: what the tools said, what it means and what to do, from the error table
    ptitle 'Send disc 2 stopped' '' "$G_FAIL" "$S_ERR"; pdrop
    pl; pa "$S_B" 'What the tools said'; pe
    local l shown=0
    for l in "${SEND_FAIL_LINES[@]}"; do
        ((shown < 3)) || break; [ -n "$l" ] || continue
        pl "$S_ERRBG"; pa "$S_ERR" "$G_QUOTE "; pa '' "$l"; pe; shown=$((shown + 1))
    done
    pl "$S_ERRBG"; pa "$S_ERR" "$G_QUOTE "; pa "$S_MUTED" "code $SEND_FAIL_CODE"; pe
    pblank
    if ((SEND_ROW >= 0)); then
        pl; pa "$S_B" 'What it means'; pe
        ppara '' "${ERR_MEAN[SEND_ROW]}"
        pblank
        pl; pa "$S_B" 'What to do'; pe
        err_steps "$SEND_ROW"; psteps 5 "${STEPS[@]}"
    fi
    while ((${#PANEL[@]} > CH - 3)); do pdrop; done
    pbottom 2
    pl; pa "$S_FAINT" 'Full log  '; pa "$S_MUTED" "$(short_path "$LOG_FILE")"; pe
    local back=Back; [ "$SEND_HOW" = direct ] && back=Close
    BTNS=('Try again' "$back" 'Show the log'); pbuttons "${BTNS[@]}"
}

send_quit_box() {  # the quit question over the send screen
    local keep=$(( ${#PANEL[@]} > 5 ? 5 : ${#PANEL[@]} ))
    PANEL=("${PANEL[@]:0:keep}")
    local msg='Nothing more changes. Disc 2 stays in the download folder.'
    case $SEND_PHASE in checks|probe|sending) msg='Quitting stops sending and turns the serve off; the code then no longer works. Disc 2 stays in the download folder.' ;; esac
    bx_top "$S_WARN" 'Quit the setup?'
    BX_STYLE=$S_WARN; bx_text '' "$msg"
    bx_open "$S_WARN"; bx_close
    bx_buttons 'Stay' 'Quit'
    bx_bot "$S_WARN"
}

if [ -z "${DEEP32_WIZARD_LIB:-}" ] && [ "${opt_send:-0}" = 1 ] && [ "${wiz_mode_now:-tui}" != tui ]; then
    send_plain; exit $?
fi

if [ -z "${DEEP32_WIZARD_LIB:-}" ]; then
    RESIZED=0
    if [ "${wiz_vm_plain:-0}" = 1 ]; then wiz_vm_plain_main; exit $?; fi
    wiz_main
fi
