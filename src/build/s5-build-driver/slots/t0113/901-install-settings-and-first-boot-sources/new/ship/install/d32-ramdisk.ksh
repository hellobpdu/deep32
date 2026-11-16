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
                default) _d32ok=1;;
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
        _d32duid=${resp#@ROOTDISK:}; _d32duid=${_d32duid%@}
        for _d32disk in $(get_dkdevs_root); do
            _d32valid=1
            for _d32part in $(sysctl -n hw.disknames | sed 's/,/ /g'); do
                [ "$_d32part" != "$_d32disk:$_d32duid" ] || _d32valid=0
            done
            [ "$_d32valid" = 1 ] || continue
            _d32candidates="$_d32candidates $_d32disk"; _d32count=$((_d32count + 1)); resp=$_d32disk
        done
        if [ "$D32_SETTINGS_DISC/$D32S_EFF_hw_profile/$_d32count" != present/vm/1 ]; then
            _d32waiting 'answer the install questions on the console of this machine'
            {
                while :; do
                    echo "" >>"${D32_TTY_OUT:-/dev/tty}"
                    echo -n "$_d32question " >>"${D32_TTY_OUT:-/dev/tty}"
                    IFS= read -r resp || { _d32running; return 1; }
                    if [ "$resp" = '?' ]; then diskinfo $(get_dkdevs) >>"${D32_TTY_OUT:-/dev/tty}" 2>&1; continue; fi
                    case "$_d32candidates " in *" $resp "*) [ -n "$resp" ] && break;; esac
                    echo 'That disk cannot be used: type one of the disks listed, or ? for details.' >>"${D32_TTY_OUT:-/dev/tty}"
                done
            } <"${D32_TTY_IN:-/dev/tty}"
            _d32running
        fi;;
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
    @DEFAULT@) resp=$_d32default;;
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
