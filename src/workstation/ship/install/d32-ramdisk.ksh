# d32-ramdisk.ksh: settings and console answers for OpenBSD install.sub.
# Contract C6 and C7. All output goes to the tty, never the response file.
# D32_ROOT, D32_TTY_IN and D32_TTY_OUT select isolated test fixtures.

_d32c6init() {
    [ "${D32_C6_READY-0}" != 1 ] || return 0
    mkdir -p "${D32_ROOT-}/tmp/i" "${D32_ROOT-}/tmp/d32" || return 1
    C6_DIR=${D32_ROOT-}/tmp/d32/c6
    C6_CARRY=${D32_ROOT-}/tmp/i/d32-c6.conf
    [ -e "${D32_ROOT-}/dev/cua00" ] || make_dev tty00 >/dev/null 2>&1 || :
    c6_setup workstation tty || return 1
    D32_C6_READY=1
}

_d32fail() {
    c6_hold_stop || :
    C6_QUIET_SCREEN=0
    c6_set install failed "error=$D32S_ERR" "msg=$D32S_WHY" || :
    err_exit "Deep32: the settings disc is refused: $D32S_WHY"
    return 1
}

_d32record() {
    d32s_record_write "${D32_ROOT-}/tmp/i/d32-settings.conf" "$D32_SETTINGS_DISC" || err_exit 'cannot write the settings record'
}

_d32load() {
    local _d32dev _d32found=0 _d32names _d32meta _d32bytes
    [ "${D32_LOADED-0}" != 1 ] || return 0
    _d32c6init || err_exit 'cannot start console status'
    D32_RUNNING_MSG='looking for a settings disc'
    rm -f "${D32_ROOT-}/tmp/ai/d32-guided-disk"
    c6_set install running "msg=$D32_RUNNING_MSG" || :
    d32s_clear
    D32_SETTINGS_DISC=absent
    mkdir -p "${D32_ROOT-}/mnt2" || return 1
    for _d32dev in $(d32_cd_devices); do
        make_dev "$_d32dev" >/dev/null 2>&1 || :
        if d32_cd_mount "$_d32dev" "${D32_ROOT-}/mnt2" >/dev/null 2>&1; then
            if [ -f "${D32_ROOT-}/mnt2/settings.json" ]; then
                _d32found=$((_d32found + 1))
                _d32names=$(ls -A "${D32_ROOT-}/mnt2")
                if [ "$_d32names" != settings.json ]; then
                    d32_cd_umount "${D32_ROOT-}/mnt2" >/dev/null 2>&1 || :
                    d32s_refuse extra; _d32fail; return 1
                fi
                _d32meta=$(ls -ln "${D32_ROOT-}/mnt2/settings.json")
                set -- $_d32meta
                _d32bytes=${5-0}
                case $_d32bytes in ''|*[!0-9]*) _d32bytes=0;; esac
                if [ "$_d32bytes" -lt 1 ] || [ "$_d32bytes" -gt 4096 ]; then
                    d32_cd_umount "${D32_ROOT-}/mnt2" >/dev/null 2>&1 || :
                    d32s_refuse size; _d32fail; return 1
                fi
                cat "${D32_ROOT-}/mnt2/settings.json" >"${D32_ROOT-}/tmp/d32/settings.json"
                chmod 0600 "${D32_ROOT-}/tmp/d32/settings.json"
            fi
        fi
        d32_cd_umount "${D32_ROOT-}/mnt2" >/dev/null 2>&1 || :
    done
    [ "$_d32found" -lt 2 ] || { d32s_refuse many; _d32fail; return 1; }
    if [ "$_d32found" = 1 ]; then
        D32_SETTINGS_DISC=present
        d32s_parse "${D32_ROOT-}/tmp/d32/settings.json" || { _d32fail; return 1; }
        if [ "$D32S_HAVE_keyboard" = 1 ] && [ "$D32S_keyboard" != default ]; then
            [ -x "${D32_ROOT-}/sbin/kbd" ] || { d32s_refuse kbd; _d32fail; return 1; }
            command -v kbd >/dev/null 2>&1 || { d32s_refuse kbd; _d32fail; return 1; }
            kbd -q "$D32S_keyboard" >/dev/null 2>&1 || { d32s_refuse value keyboard; _d32fail; return 1; }
        fi
        D32_RUNNING_MSG='settings disc read'
        C6_NAME=${D32S_node_name-}
        D32S_HAVE_node_name=0 D32S_HAVE_user_name=0 D32S_HAVE_time_zone=0 D32S_HAVE_keyboard=0
    else
        D32S_hw_profile=vm
        case $(sysctl -n hw.product 2>/dev/null | sed 'y/ABCDEFGHIJKLMNOPQRSTUVWXYZ/abcdefghijklmnopqrstuvwxyz/') in
        'latitude 5430'|*' latitude 5430') D32S_hw_profile=latitude-5430;;
        esac
        D32_RUNNING_MSG='no settings disc: the installer asks on the console'
    fi
    d32s_effective
    _d32record
    D32_LOADED=1
    c6_set install running "msg=$D32_RUNNING_MSG" || :
}

_d32waiting() {
    c6_set install waiting "msg=$1" || :
    C6_QUIET_SCREEN=1
    c6_hold_start || :
}

_d32running() {
    c6_hold_stop || :
    C6_QUIET_SCREEN=0
    c6_set install running "msg=$D32_RUNNING_MSG" || :
}

_d32tty() {
    local _d32question=$1 _d32secret=$2 _d32a= _d32b=
    {
        while :; do
            if [ "$_d32secret" = 1 ]; then
                stty -echo
                echo "" >>"${D32_TTY_OUT:-/dev/tty}"
                echo -n "$_d32question (will not echo) " >>"${D32_TTY_OUT:-/dev/tty}"
                IFS= read -r _d32a || { stty echo; return 1; }
                echo "" >>"${D32_TTY_OUT:-/dev/tty}"
                echo -n "$_d32question (again) " >>"${D32_TTY_OUT:-/dev/tty}"
                IFS= read -r _d32b || { stty echo; return 1; }
                stty echo
                echo "" >>"${D32_TTY_OUT:-/dev/tty}"
                [ -n "$_d32a" ] && [ "$_d32a" = "$_d32b" ] && break
                echo 'Empty, or the two did not match. Try again.' >>"${D32_TTY_OUT:-/dev/tty}"
            else
                echo "" >>"${D32_TTY_OUT:-/dev/tty}"
                echo -n "$_d32question " >>"${D32_TTY_OUT:-/dev/tty}"
                IFS= read -r _d32a || return 1
                if [ "$_d32a" = '?' ]; then
                    diskinfo $(get_dkdevs) >>"${D32_TTY_OUT:-/dev/tty}" 2>&1
                    continue
                fi
                [ -n "$_d32a" ] && break
            fi
        done
    } <"${D32_TTY_IN:-/dev/tty}"
    resp=$_d32a
}

_d32tzcheck() {
    local _d32list=${D32_ROOT-}/mnt/tmp/tzlist
    [ -f "$_d32list" ] || return 0
    grep -qxF "$1" "$_d32list" && ! grep -qxF "$1/" "$_d32list"
}

# Decimal input only; arithmetic below must never evaluate template or disk text.
_d32uint() {
    local _d32n=$1
    case $_d32n in ''|*[!0-9]*) return 1;; esac
    while [ "${_d32n#0}" != "$_d32n" ]; do _d32n=${_d32n#0}; done
    [ -n "$_d32n" ] || _d32n=0
    [ "${#_d32n}" -le 18 ] || return 1
    D32_UINT=$_d32n
}

_d32sizebytes() {
    local _d32size=$1 _d32n _d32unit _d32factor
    _d32n=${_d32size%?}; _d32unit=${_d32size#"$_d32n"}
    case $_d32unit in
    K) _d32factor=1024;; M) _d32factor=1048576;;
    G) _d32factor=1073741824;; T) _d32factor=1099511627776;;
    *) return 1;;
    esac
    _d32uint "$_d32n" || return 1
    [ "$D32_UINT" -le "$((9223372036854775807 / _d32factor))" ] || return 1
    D32_SIZE_BYTES=$((D32_UINT * _d32factor))
}

# The exact template copied into the guided ramdisk is the sizing authority.
_d32minimum() {
    local _d32line _d32mount _d32size _d32share _d32extra _d32min _d32max
    local _d32bytes _d32count=0 _d32seen=' '
    D32_MIN_BYTES=0
    [ -r "${D32_ROOT-}/disklabel.ship" ] || return 1
    while IFS= read -r _d32line || [ -n "$_d32line" ]; do
        read -r _d32mount _d32size _d32share _d32extra <<EOF
$_d32line
EOF
        case $_d32mount in ''|'#'*) continue;; swap|/*) ;; *) return 1;; esac
        case $_d32mount in *[!A-Za-z0-9_./-]*) return 1;; esac
        case $_d32seen in *" $_d32mount "*) return 1;; esac
        _d32seen="$_d32seen$_d32mount "
        [ -z "$_d32extra" ] || return 1
        case $_d32share in
        '') ;;
        *%) _d32uint "${_d32share%?}" || return 1
            [ "$D32_UINT" -ge 1 ] && [ "$D32_UINT" -le 100 ] || return 1;;
        *) return 1;;
        esac
        _d32min=$_d32size
        case $_d32size in
        *-*) _d32min=${_d32size%%-*}; _d32max=${_d32size#*-}
            case $_d32max in '*') ;; *) _d32sizebytes "$_d32max" || return 1;; esac;;
        esac
        _d32sizebytes "$_d32min" || return 1
        _d32bytes=$D32_SIZE_BYTES
        case $_d32size in
        *-*) case $_d32max in '*') ;; *) _d32sizebytes "$_d32max" || return 1
            [ "$D32_SIZE_BYTES" -ge "$_d32bytes" ] || return 1;; esac;;
        esac
        [ "$D32_MIN_BYTES" -le "$((9223372036854775807 - _d32bytes))" ] || return 1
        D32_MIN_BYTES=$((D32_MIN_BYTES + _d32bytes))
        _d32count=$((_d32count + 1))
    done <"${D32_ROOT-}/disklabel.ship"
    [ "$_d32count" -gt 0 ] && [ "$D32_MIN_BYTES" -gt 0 ]
}

_d32diskbytes() {
    local _d32label _d32line _d32value _d32sectors= _d32sectorbytes=
    local _d32nsectors=0 _d32nbytes=0
    # The ramdisk makes disk nodes on demand; the stock installer does it after its own disk question.
    make_dev "$1" >/dev/null 2>&1 || :
    _d32label=$(disklabel "$1" 2>/dev/null) || return 1
    while IFS= read -r _d32line; do
        case $_d32line in
        'total sectors:'*) _d32value=${_d32line#*:}
            read -r _d32value <<EOF
$_d32value
EOF
            _d32uint "$_d32value" || return 1
            _d32sectors=$D32_UINT; _d32nsectors=$((_d32nsectors + 1));;
        'bytes/sector:'*) _d32value=${_d32line#*:}
            read -r _d32value <<EOF
$_d32value
EOF
            _d32uint "$_d32value" || return 1
            _d32sectorbytes=$D32_UINT; _d32nbytes=$((_d32nbytes + 1));;
        esac
    done <<EOF
$_d32label
EOF
    [ "$_d32nsectors/$_d32nbytes" = 1/1 ] || return 1
    [ "$_d32sectors" -gt 0 ] && [ "$_d32sectorbytes" -gt 0 ] || return 1
    [ "$_d32sectors" -le "$((9223372036854775807 / _d32sectorbytes))" ] || return 1
    D32_DISK_BYTES=$((_d32sectors * _d32sectorbytes))
}

# GB is decimal; preserve the exact byte value without printf or rounding.
_d32gb() {
    local _d32whole=$(($1 / 1000000000)) _d32fraction=$(($1 % 1000000000))
    D32_GB=$_d32whole
    [ "$_d32fraction" != 0 ] || return 0
    while [ "${#_d32fraction}" -lt 9 ]; do _d32fraction=0$_d32fraction; done
    while [ "${_d32fraction%0}" != "$_d32fraction" ]; do _d32fraction=${_d32fraction%0}; done
    D32_GB=$_d32whole.$_d32fraction
}

_d32ask() {
    local _d32field=$1 _d32question=$2 _d32a= _d32ok _d32list
    _d32waiting 'answer the install questions on the console of this machine'
    {
        while :; do
            echo "" >>"${D32_TTY_OUT:-/dev/tty}"
            echo -n "$_d32question " >>"${D32_TTY_OUT:-/dev/tty}"
            IFS= read -r _d32a || { _d32running; return 1; }
            _d32ok=0
            case $_d32field in
            time_zone)
                if [ "$_d32a" = '?' ]; then
                    _d32list=${D32_ROOT-}/mnt/tmp/tzlist
                    [ -f "$_d32list" ] || _d32list=${D32_ROOT-}/var/tzlist
                    grep -Ev '/.' "$_d32list" >>"${D32_TTY_OUT:-/dev/tty}" || :
                    continue
                fi
                d32s_ok_time_zone "$_d32a" && _d32tzcheck "$_d32a" && _d32ok=1;;
            keyboard)
                case $_d32a in '?'|L|l)
                    kbd -l | grep -Ev '^(user|tables|encoding)' >>"${D32_TTY_OUT:-/dev/tty}"
                    continue;;
                ''|default) _d32a=default; _d32ok=1;;
                *) d32s_ok_keyboard "$_d32a" && command -v kbd >/dev/null 2>&1 && kbd -q "$_d32a" >/dev/null 2>&1 && _d32ok=1;;
                esac;;
            node_name) d32s_ok_node_name "$_d32a" && _d32ok=1;;
            user_name) d32s_ok_user_name "$_d32a" && _d32ok=1;;
            esac
            [ "$_d32ok" = 0 ] || break
            case $_d32field in
            node_name) echo 'That name cannot be used: a lower-case letter, then up to 62 lower-case letters, digits or dashes (not localhost).' >>"${D32_TTY_OUT:-/dev/tty}";;
            user_name) echo 'That login name cannot be used: a lower-case letter, then up to 30 lower-case letters, digits, dashes or underscores, and not a system account.' >>"${D32_TTY_OUT:-/dev/tty}";;
            time_zone) echo 'That time zone cannot be used: type a zone such as Europe/London, or ? for the list.' >>"${D32_TTY_OUT:-/dev/tty}";;
            keyboard) echo 'That keyboard layout cannot be used: type a layout name, default, or ? for the list.' >>"${D32_TTY_OUT:-/dev/tty}";;
            esac
        done
    } <"${D32_TTY_IN:-/dev/tty}"
    resp=$_d32a
    _d32running
}

_d32resp() {
    local _d32question=$1 _d32default=${2-} _d32field _d32value _d32part _d32duid
    local _d32candidates= _d32count=0 _d32disk _d32valid=0 _d32changed=0
    local _d32auto=0 _d32confirm _d32diskgb _d32mingb
    _d32load || return 1
    case $resp in
    @SET:*@)
        _d32field=${resp#@SET:}; _d32field=${_d32field%@}
        case $_d32field in
        node_name) _d32value=${D32S_node_name-};;
        user_name) _d32value=${D32S_user_name-};;
        time_zone) _d32value=${D32S_time_zone-};;
        keyboard) _d32value=${D32S_keyboard-};;
        *) err_exit 'template error'; return 1;;
        esac
        if [ -n "$_d32value" ] && [ "$D32_SETTINGS_DISC" = present ]; then
            resp=$_d32value
            if [ "$_d32field" = time_zone ] && ! _d32tzcheck "$resp"; then
                d32s_refuse value time_zone; _d32fail; return 1
            fi
        else
            _d32ask "$_d32field" "$_d32question" || return 1
        fi;;
    @ROOTDISK:*@)
        D32_GUIDED_DISK_READY=0
        _d32minimum || { err_exit 'cannot read the guided disk minimum'; return 1; }
        _d32gb "$D32_MIN_BYTES"; _d32mingb=$D32_GB
        _d32duid=${resp#@ROOTDISK:}; _d32duid=${_d32duid%@}
        for _d32disk in $(get_dkdevs_root); do
            _d32valid=1
            for _d32part in $(sysctl -n hw.disknames | sed 's/,/ /g'); do
                [ "$_d32part" != "$_d32disk:$_d32duid" ] || _d32valid=0
            done
            [ "$_d32valid" = 1 ] || continue
            _d32candidates="$_d32candidates $_d32disk"; _d32count=$((_d32count + 1)); resp=$_d32disk
        done
        # The installer's own default can name the install medium. Replace it
        # with our sole eligible disk, or require an explicit answer.
        _d32default=
        [ "$_d32count" != 1 ] || _d32default=$resp
        _d32question=${_d32question% \[*\]}
        [ -z "$_d32default" ] || _d32question="$_d32question [$_d32default]"
        [ "$D32_SETTINGS_DISC/$D32S_EFF_hw_profile/$_d32count" != present/vm/1 ] || _d32auto=1
        if [ "$_d32auto" = 1 ]; then
            _d32diskbytes "$resp" || { _d32running; err_exit 'cannot read the root disk size'; return 1; }
            if [ "$D32_DISK_BYTES" -lt "$D32_MIN_BYTES" ]; then
                _d32gb "$D32_DISK_BYTES"; _d32diskgb=$D32_GB
                print -r -- "This disk is $_d32diskgb GB; the guided install needs at least $_d32mingb GB." >>"${D32_TTY_OUT:-/dev/tty}"
                _d32auto=0
            fi
        fi
        if [ "$_d32auto" != 1 ]; then
            {
                while :; do
                    _d32waiting 'answer the install questions on the console of this machine'
                    while :; do
                        echo "" >>"${D32_TTY_OUT:-/dev/tty}"
                        echo -n "$_d32question " >>"${D32_TTY_OUT:-/dev/tty}"
                        IFS= read -r resp || { _d32running; return 1; }
                        if [ "$resp" = '?' ]; then diskinfo $(get_dkdevs) >>"${D32_TTY_OUT:-/dev/tty}" 2>&1; continue; fi
                        [ -n "$resp" ] || resp=$_d32default
                        case "$_d32candidates " in *" $resp "*) [ -n "$resp" ] && break;; esac
                        echo 'That disk cannot be used: type one of the disks listed, or ? for details.' >>"${D32_TTY_OUT:-/dev/tty}"
                    done
                    _d32diskbytes "$resp" || { _d32running; err_exit 'cannot read the root disk size'; return 1; }
                    _d32gb "$D32_DISK_BYTES"; _d32diskgb=$D32_GB
                    if [ "$D32_DISK_BYTES" -lt "$D32_MIN_BYTES" ]; then
                        print -r -- "This disk is $_d32diskgb GB; the guided install needs at least $_d32mingb GB." >>"${D32_TTY_OUT:-/dev/tty}"
                        continue
                    fi
                    [ "$D32_SETTINGS_DISC" != present ] || break
                    print -r -- "Disk $resp is $_d32diskgb GB. The whole disk will be erased." >>"${D32_TTY_OUT:-/dev/tty}"
                    print -rn -- 'Type the disk name to confirm: ' >>"${D32_TTY_OUT:-/dev/tty}"
                    IFS= read -r _d32confirm || { _d32running; return 1; }
                    [ "$_d32confirm" != "$resp" ] || break
                done
            } <"${D32_TTY_IN:-/dev/tty}" || { _d32running; return 1; }
        fi
        D32_GUIDED_DISK_READY=1
        # The install runs as a child of do_autoinstall, which decides power-off; a variable cannot reach it.
        mkdir -p "${D32_ROOT-}/tmp/ai" && : >"${D32_ROOT-}/tmp/ai/d32-guided-disk" || { err_exit 'cannot record the guided disk'; return 1; }
        _d32running;;
    @ENCRYPT@)
        if [ "$D32_SETTINGS_DISC" = present ]; then
            resp=no; [ "$D32S_EFF_disk_encryption" != true ] || resp=p
        else
            _d32waiting 'answer the install questions on the console of this machine'
            {
                while :; do
                    echo -n 'Encrypt the root disk with a passphrase? (p or no) ' >>"${D32_TTY_OUT:-/dev/tty}"
                    IFS= read -r resp || { _d32running; return 1; }
                    case $resp in p|no) break;; esac
                    echo 'Type p (encrypt with a passphrase) or no.' >>"${D32_TTY_OUT:-/dev/tty}"
                done
            } <"${D32_TTY_IN:-/dev/tty}"
            _d32running
        fi;;
    @DEFAULT@)
        case $_d32question in
        'Use (W)hole disk MBR, whole disk (G)PT'*)
            [ "${D32_GUIDED_DISK_READY-0}" = 1 ] || {
                err_exit 'the root disk has not been confirmed'; return 1;
            }
            [ -n "${DMESGBOOT-}" ] && [ -r "$DMESGBOOT" ] || {
                err_exit 'cannot read the boot log'; return 1;
            }
            # OpenBSD 8.0 md_prep_fdisk's default without an OpenBSD partition.
            if grep -q -e '^efi0 at bios0' "$DMESGBOOT"; then
                resp=gpt
            else
                case $? in
                1) resp=whole;;
                *) err_exit 'cannot read the boot log'; return 1;;
                esac
            fi;;
        *) resp=$_d32default;;
        esac;;
    @ASK@|@ASKSECRET@)
        _d32value=0
        [ "$resp" != @ASKSECRET@ ] || _d32value=1
        case $_d32question in
        'New passphrase'*) _d32waiting 'type the disk passphrase twice on the console of this machine';;
        *) _d32waiting 'answer the install questions on the console of this machine';;
        esac
        _d32tty "$_d32question" "$_d32value" || { _d32running; return 1; }
        _d32running;;
    @DUID:*@)
        _d32duid=${resp#@DUID:}; _d32duid=${_d32duid%@}; resp=
        for _d32part in $(sysctl -n hw.disknames | sed 's/,/ /g'); do
            [ "${_d32part#*:}" != "$_d32duid" ] || resp=${_d32part%%:*}
        done
        [ -n "$resp" ] || err_exit "No disk with DUID $_d32duid: is the Deep32 USB stick attached?";;
    esac
    case $(echo "$_d32question" | sed 'y/ABCDEFGHIJKLMNOPQRSTUVWXYZ/abcdefghijklmnopqrstuvwxyz/') in
    'system hostname'*) D32S_node_name=$resp; D32S_HAVE_node_name=1; C6_NAME=$resp; _d32changed=1;;
    'setup a user'*) D32S_user_name=$resp; D32S_HAVE_user_name=1; _d32changed=1;;
    'what timezone are you in'*) D32S_time_zone=$resp; D32S_HAVE_time_zone=1; _d32changed=1;;
    'choose your keyboard layout'*) D32S_keyboard=$resp; D32S_HAVE_keyboard=1; _d32changed=1;;
    'encrypt the root disk'*)
        D32S_answer_disk_encryption=false
        case $resp in p|k) D32S_answer_disk_encryption=true;; esac
        D32S_HAVE_answer_disk_encryption=1; _d32changed=1;;
    'location of sets'*) D32_RUNNING_MSG='copying the system files'; c6_set install running "msg=$D32_RUNNING_MSG" || :;;
    esac
    [ "$_d32changed" = 0 ] || _d32record
    return 0
}
