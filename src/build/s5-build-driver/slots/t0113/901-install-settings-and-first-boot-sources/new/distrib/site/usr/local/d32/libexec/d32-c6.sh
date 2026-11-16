# d32-c6.sh: console status library, contract C6, version 1.
# Sourcing defines functions and C6 variables only. D32_ROOT prefixes paths in
# tests; D32_TEST_BOOTID supplies eight hex digits only with a non-empty root.
# C6_DIR, C6_SERIAL, C6_REPEAT, D32_TTY_OUT select test files and timing.
# c6_encode SEQ NODE STAGE STATE [key=value ...]: 0 encoded, 1 refused.
c6_encode() {
    local _d32seq=${1-} _d32node=${2-} _d32stage=${3-} _d32state=${4-}
    local _d32boot= _d32version= _d32name= _d32signin= _d32pair=
    local _d32with= _d32expires= _d32error= _d32msg= _d32seen=' '
    local _d32arg _d32key _d32val _d32line
    [ "$#" -ge 4 ] || return 1
    shift 4
    case $_d32seq in ''|0*|*[!0-9]*) return 1;; esac
    [ "${#_d32seq}" -le 9 ] || return 1
    case $_d32node in workstation|forge|headless) ;; *) return 1;; esac
    case $_d32stage in install|firstboot|keys|signin|tailnet|content|import|pairing|ready) ;; *) return 1;; esac
    case $_d32state in running|waiting|done|failed) ;; *) return 1;; esac
    for _d32arg do
        case $_d32arg in *=*) ;; *) return 1;; esac
        _d32key=${_d32arg%%=*} _d32val=${_d32arg#*=}
        case $_d32seen in *" $_d32key "*) return 1;; esac
        _d32seen="$_d32seen$_d32key "
        case $_d32val in ''|*\\*|*[[:cntrl:]]*|*[![:print:]]*) return 1;; esac
        case $_d32val in *tskey-*|*'PRIVATE KEY'*|*'$2a$'*|*'$2b$'*|*'$2y$'*) return 1;; esac
        case $_d32val in *[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]*) return 1;; esac
        [ "$_d32key" = msg ] || case $_d32val in *' '*) return 1;; esac
        case $_d32key in
        boot)
            case $_d32val in *[!0-9a-f]*) return 1;; esac
            [ "${#_d32val}" = 8 ] || return 1
            _d32boot=$_d32val;;
        version)
            [ "${#_d32val}" -le 64 ] || return 1
            case $_d32val in *[!A-Za-z0-9._+-]*) return 1;; esac
            _d32version=$_d32val;;
        name)
            [ "${#_d32val}" -le 63 ] || return 1
            case $_d32val in [a-z]*) ;; *) return 1;; esac
            case $_d32val in localhost|*[!a-z0-9-]*) return 1;; esac
            _d32name=$_d32val;;
        signin)
            case $_d32val in https://login.tailscale.com/?*) ;; *) return 1;; esac
            _d32signin=${_d32val#https://login.tailscale.com/}
            [ "${#_d32signin}" -le 256 ] || return 1
            case $_d32signin in *[!A-Za-z0-9/_?=.-]*) return 1;; esac
            [ "$_d32stage/$_d32state" = signin/waiting ] || return 1
            _d32signin=$_d32val;;
        pair)
            [ "${#_d32val}" = 10 ] || return 1
            case $_d32val in *[!A-Z2-7]*) return 1;; esac
            [ "$_d32stage/$_d32state" = pairing/waiting ] || return 1
            _d32pair=$_d32val;;
        pair_with)
            [ "$_d32stage" = pairing ] || return 1
            case $_d32val in forge|workstation|relay|headend) ;; *) return 1;; esac
            _d32with=$_d32val;;
        expires)
            case $_d32val in 0*|*[!0-9]*) return 1;; esac
            [ "${#_d32val}" -le 3 ] && [ "$_d32val" -le 300 ] || return 1
            _d32expires=$_d32val;;
        error)
            [ "$_d32state" = failed ] || return 1
            [ "${#_d32val}" -ge 2 ] && [ "${#_d32val}" -le 48 ] || return 1
            case $_d32val in [A-Z]*) ;; *) return 1;; esac
            case $_d32val in *[!A-Z0-9_]*) return 1;; esac
            _d32error=$_d32val;;
        msg)
            [ "${#_d32val}" -le 240 ] || return 1
            _d32msg=$_d32val;;
        *) return 1;;
        esac
    done
    [ -n "$_d32boot" ] || return 1
    if [ "$_d32state" = failed ]; then [ -n "$_d32error" ] || return 1; fi
    if [ -n "$_d32pair" ]; then [ -n "$_d32expires" ] || return 1; fi
    if [ -n "$_d32expires" ]; then [ -n "$_d32pair" ] || return 1; fi
    _d32line="D32S/1 $_d32seq $_d32node boot=$_d32boot stage=$_d32stage state=$_d32state"
    [ -z "$_d32version" ] || _d32line="$_d32line version=$_d32version"
    [ -z "$_d32name" ] || _d32line="$_d32line name=$_d32name"
    [ -z "$_d32signin" ] || _d32line="$_d32line signin=$_d32signin"
    [ -z "$_d32pair" ] || _d32line="$_d32line pair=$_d32pair"
    [ -z "$_d32with" ] || _d32line="$_d32line pair_with=$_d32with"
    [ -z "$_d32expires" ] || _d32line="$_d32line expires=$_d32expires"
    [ -z "$_d32error" ] || _d32line="$_d32line error=$_d32error"
    [ -z "$_d32msg" ] || _d32line="$_d32line msg=$_d32msg"
    [ "${#_d32line}" -le 511 ] || return 1
    echo -n "$_d32line"
}

# c6_check LINE: 0 valid, 1 refused. Keys may arrive in any order.
c6_check() {
    local _d32rest=${1-} _d32seq _d32node _d32part _d32stage= _d32state=
    local _d32seen=' '
    [ "$#" = 1 ] || return 1
    [ "${#_d32rest}" -le 511 ] || return 1
    case $_d32rest in 'D32S/1 '*) ;; *) return 1;; esac
    _d32rest=${_d32rest#D32S/1 }
    _d32seq=${_d32rest%% *}; _d32rest=${_d32rest#* }
    _d32node=${_d32rest%% *}; _d32rest=${_d32rest#* }
    set --
    while [ -n "$_d32rest" ]; do
        case $_d32rest in
        msg=*) set -- "$@" "$_d32rest"; _d32rest=; continue;;
        *' '*) _d32part=${_d32rest%% *}; _d32rest=${_d32rest#* };;
        *) _d32part=$_d32rest; _d32rest=;;
        esac
        case $_d32part in
        stage=*)
            case $_d32seen in *' stage '*) return 1;; esac
            _d32stage=${_d32part#stage=}; _d32seen="$_d32seen stage ";;
        state=*)
            case $_d32seen in *' state '*) return 1;; esac
            _d32state=${_d32part#state=}; _d32seen="$_d32seen state ";;
        *) set -- "$@" "$_d32part";;
        esac
    done
    c6_encode "$_d32seq" "$_d32node" "$_d32stage" "$_d32state" "$@" >/dev/null
}

# c6_setup NODE SCREEN: 0 ready, 1 invalid, 3 storage failure.
c6_setup() {
    local _d32boot _d32cons _d32tries=0 _d32pid=
    case ${1-} in workstation|forge|headless) ;; *) return 1;; esac
    case ${2-} in stdout|tty|console|none) ;; *) return 1;; esac
    C6_NODE=$1 C6_SCREEN=$2
    C6_DIR=${C6_DIR:-${D32_ROOT-}/var/run/d32/c6}
    mkdir -p "$C6_DIR" && chmod 0700 "$C6_DIR" || return 3
    if [ ! -f "$C6_DIR/boot" ]; then
        while ! mkdir "$C6_DIR/lock" 2>/dev/null; do
            [ ! -r "$C6_DIR/lock/pid" ] || IFS= read -r _d32pid <"$C6_DIR/lock/pid"
            case $_d32pid in ''|*[!0-9]*) ;; *)
                if ! kill -0 "$_d32pid" 2>/dev/null; then rm -rf "$C6_DIR/lock"; continue; fi;;
            esac
            [ "$_d32tries" -lt 5 ] || return 3
            sleep 1; _d32tries=$((_d32tries + 1))
        done
        echo "$$" >"$C6_DIR/lock/pid" || { rm -rf "$C6_DIR/lock"; return 3; }
        if [ ! -f "$C6_DIR/boot" ]; then
        if [ -n "${D32_ROOT-}" ] && [ -n "${D32_TEST_BOOTID-}" ]; then
            _d32boot=$D32_TEST_BOOTID
        else
            _d32boot=$(dd if="${D32_ROOT-}/dev/random" bs=32 count=1 2>/dev/null | sha256) || { rm -rf "$C6_DIR/lock"; return 3; }
            _d32boot=${_d32boot%"${_d32boot#????????}"}
        fi
        case $_d32boot in *[!0-9a-f]*) rm -rf "$C6_DIR/lock"; return 1;; esac
        [ "${#_d32boot}" = 8 ] || { rm -rf "$C6_DIR/lock"; return 1; }
        echo "$_d32boot" >"$C6_DIR/boot" && echo 0 >"$C6_DIR/seq" || { rm -rf "$C6_DIR/lock"; return 3; }
        fi
        rm -rf "$C6_DIR/lock"
    fi
    C6_NOTE=
    if [ "${C6_SERIAL+x}" != x ]; then
        C6_SERIAL=
        if _d32cons=$(sysctl -n kern.consdev 2>/dev/null); then
            if [ "$_d32cons" != tty00 ] && [ -c "${D32_ROOT-}/dev/cua00" ]; then
                C6_SERIAL=${D32_ROOT-}/dev/cua00
            fi
        else
            C6_NOTE='kern.consdev unreadable'
        fi
    fi
    return 0
}

# c6_current: 0 line printed without LF, 3 no readable current line.
c6_current() {
    local _d32line
    [ -r "${C6_DIR-}/status" ] || return 3
    IFS= read -r _d32line <"$C6_DIR/status" || return 3
    echo -n "$_d32line"
}

# c6_carry FILE: 0 saved, 3 storage failure. Temporary name is not *.conf.
c6_carry() {
    local _d32boot _d32seq
    IFS= read -r _d32boot <"$C6_DIR/boot" || return 3
    IFS= read -r _d32seq <"$C6_DIR/seq" || return 3
    { echo "boot=$_d32boot"; echo "seq=$_d32seq"; } >"$1.new" &&
        chmod 0600 "$1.new" && mv "$1.new" "$1" || return 3
}

# c6_adopt FILE: 0 adopted and removed, 1 invalid (kept), 3 storage failure.
c6_adopt() {
    local _d32boot _d32seq _d32extra
    [ -r "$1" ] || return 1
    {
        IFS= read -r _d32boot && IFS= read -r _d32seq || return 1
        if IFS= read -r _d32extra || [ -n "$_d32extra" ]; then return 1; fi
    } <"$1"
    case $_d32boot in boot=????????) ;; *) return 1;; esac
    _d32boot=${_d32boot#boot=}
    case $_d32boot in *[!0-9a-f]*) return 1;; esac
    case $_d32seq in seq=*) _d32seq=${_d32seq#seq=};; *) return 1;; esac
    case $_d32seq in 0) ;; ''|0*|*[!0-9]*) return 1;; esac
    [ "${#_d32seq}" -le 9 ] || return 1
    echo "$_d32boot" >"$C6_DIR/boot" && echo "$_d32seq" >"$C6_DIR/seq" && rm -f "$1" || return 3
}

# c6_set STAGE STATE [key=value ...]: 0 emitted, 1 refused, 3 storage failure.
# The mkdir lock serialises writers; a dead holder may be reclaimed.
c6_set() {
    local _d32stage=${1-} _d32state=${2-} _d32boot _d32seq _d32line _d32old=
    local _d32pid _d32tries=0 _d32new=1 _d32target _d32writer _d32watch
    [ "$#" -ge 2 ] || return 1
    shift 2
    IFS= read -r _d32boot <"$C6_DIR/boot" || return 3
    if [ -n "${C6_VERSION-}" ] && c6_encode 1 "$C6_NODE" install running "boot=$_d32boot" "version=$C6_VERSION" >/dev/null; then
        set -- "$@" "version=$C6_VERSION"
    fi
    if [ -n "${C6_NAME-}" ] && c6_encode 1 "$C6_NODE" install running "boot=$_d32boot" "name=$C6_NAME" >/dev/null; then
        set -- "$@" "name=$C6_NAME"
    fi
    _d32line=$(c6_encode 1 "$C6_NODE" "$_d32stage" "$_d32state" "boot=$_d32boot" "$@") || return 1
    while ! mkdir "$C6_DIR/lock" 2>/dev/null; do
        _d32pid=
        if [ -r "$C6_DIR/lock/pid" ]; then IFS= read -r _d32pid <"$C6_DIR/lock/pid" || :; fi
        case $_d32pid in ''|*[!0-9]*) ;; *)
            if ! kill -0 "$_d32pid" 2>/dev/null; then rm -rf "$C6_DIR/lock"; continue; fi;;
        esac
        [ "$_d32tries" -lt 5 ] || return 3
        sleep 1; _d32tries=$((_d32tries + 1))
    done
    echo "$$" >"$C6_DIR/lock/pid" || { rm -rf "$C6_DIR/lock"; return 3; }
    if ! IFS= read -r _d32seq <"$C6_DIR/seq"; then rm -rf "$C6_DIR/lock"; return 3; fi
    if [ -r "$C6_DIR/status" ]; then IFS= read -r _d32old <"$C6_DIR/status" || :; fi
    if [ "${_d32old#D32S/1 * }" = "${_d32line#D32S/1 * }" ]; then
        _d32new=0
    else
        _d32seq=$((_d32seq + 1))
    fi
    _d32line=$(c6_encode "$_d32seq" "$C6_NODE" "$_d32stage" "$_d32state" "boot=$_d32boot" "$@") || { rm -rf "$C6_DIR/lock"; return 1; }
    echo "$_d32line" >"$C6_DIR/status.new" && chmod 0600 "$C6_DIR/status.new" &&
        mv "$C6_DIR/status.new" "$C6_DIR/status" || { rm -rf "$C6_DIR/lock"; return 3; }
    if [ "$_d32new" = 1 ]; then
        echo "$_d32seq" >"$C6_DIR/seq" || { rm -rf "$C6_DIR/lock"; return 3; }
        if [ -n "${C6_CARRY-}" ]; then c6_carry "$C6_CARRY" || { rm -rf "$C6_DIR/lock"; return 3; }; fi
    fi
    for _d32target in screen serial; do
        if [ "$_d32target" = screen ]; then
            [ "$_d32new" = 1 ] && [ "${C6_QUIET_SCREEN-0}" != 1 ] || continue
            case $C6_SCREEN in
            stdout) _d32target=stdout;;
            tty) _d32target=${D32_TTY_OUT:-${D32_ROOT-}/dev/tty};;
            console) _d32target=${D32_ROOT-}/dev/console;;
            none) continue;;
            esac
        else
            _d32target=${C6_SERIAL-}
            [ -n "$_d32target" ] || continue
        fi
        if [ "$_d32target" = stdout ]; then
            (echo "$_d32line") </dev/null &
        else
            (echo "$_d32line" >>"$_d32target") </dev/null &
        fi
        _d32writer=$!
        (sleep 2; kill "$_d32writer" 2>/dev/null || :) </dev/null >/dev/null 2>&1 &
        _d32watch=$!
        wait "$_d32writer" 2>/dev/null || :
        kill "$_d32watch" 2>/dev/null || :
        wait "$_d32watch" 2>/dev/null || :
    done
    rm -rf "$C6_DIR/lock"
    return 0
}

# c6_repeat: 0 serial copy only, 3 missing current line.
c6_repeat() {
    local _d32line _d32writer _d32watch
    _d32line=$(c6_current) || return 3
    [ -n "${C6_SERIAL-}" ] || return 0
    (echo "$_d32line" >>"$C6_SERIAL") </dev/null &
    _d32writer=$!
    (sleep 2; kill "$_d32writer" 2>/dev/null || :) </dev/null >/dev/null 2>&1 &
    _d32watch=$!
    wait "$_d32writer" 2>/dev/null || :
    kill "$_d32watch" 2>/dev/null || :
    wait "$_d32watch" 2>/dev/null || :
    return 0
}

# c6_hold_start: 0 running (idempotent), 3 pid file failure.
c6_hold_start() {
    local _d32pid=
    if [ -r "$C6_DIR/hold.pid" ]; then
        IFS= read -r _d32pid <"$C6_DIR/hold.pid" || :
        if kill -0 "$_d32pid" 2>/dev/null; then return 0; fi
    fi
    (while :; do sleep "${C6_REPEAT:-10}"; c6_repeat || :; done) </dev/null >/dev/null 2>&1 &
    _d32pid=$!
    echo "$_d32pid" >"$C6_DIR/hold.pid" || { kill "$_d32pid" 2>/dev/null || :; wait "$_d32pid" 2>/dev/null || :; return 3; }
}

# c6_hold_stop: 0 stopped (idempotent), 3 pid file removal failure.
c6_hold_stop() {
    local _d32pid=
    if [ -r "$C6_DIR/hold.pid" ]; then
        IFS= read -r _d32pid <"$C6_DIR/hold.pid" || :
        kill "$_d32pid" 2>/dev/null || :
        wait "$_d32pid" 2>/dev/null || :
    fi
    rm -f "$C6_DIR/hold.pid" || return 3
}
