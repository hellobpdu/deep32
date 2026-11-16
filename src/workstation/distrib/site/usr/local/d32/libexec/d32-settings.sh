# d32-settings.sh: settings-disc and node record reader, contract C7, version 1.
# Functions take the arguments named below and return 0 on success or 1 on
# refusal. D32S_ERR and D32S_WHY give a fixed, safe reason. D32_ROOT prefixes
# media and time-zone paths in tests. Sourcing performs no I/O.
D32S_FIELDS="disk_encryption features format format_version hw_profile keyboard node_name profile publish role size time_zone transport user_name written_by"
D32S_RESERVED_USERS="_d32acq _d32acqc _d32acx _d32adm _d32aud _d32bench _d32chat _d32core _d32ctl _d32desk _d32edge _d32fs _d32gpu _d32inb _d32libw _d32ml _d32raw _d32rem _d32sec _d32shelf _d32ts _d32uph _d32upw _d32users _d32xnet _d32xweb auth authpf bin build crontab d32drop d32gfx d32net d32scp d32web d32xfer daemon dialer ftp games guest kmem network nobody nogroup operator root sshd staff sys tty users utmp wheel wobj wsrc www"
D32S_WS_PROFILES="baremetal esxi linux-nvidia no-gpu proxmox-passthrough two-machines virtualbox vmware win-home win-nvidia"

# d32s_refuse REASON [FIELD]: fixed error text, never untrusted disc text.
d32s_refuse() {
    D32S_ERR=WS_SETTINGS_FORM
    case $1 in
    form) D32S_WHY='the settings file is not in the canonical form';;
    size) D32S_WHY='the settings file is empty or larger than 4096 bytes';;
    missing) D32S_WHY="the field $2 is missing";;
    value) D32S_WHY="the field $2 has a value that is not allowed";;
    unknown) D32S_WHY='the settings file has a field this version does not know';;
    kbd) D32S_WHY='keyboard cannot be checked';;
    role) D32S_ERR=WS_SETTINGS_ROLE; D32S_WHY='the settings disc is not for a workstation';;
    extra) D32S_ERR=WS_SETTINGS_EXTRA; D32S_WHY='the settings disc holds files other than settings.json';;
    many) D32S_ERR=WS_SETTINGS_MANY; D32S_WHY='more than one settings disc is attached';;
    esac
    return 1
}

# d32s_clear: forget all fields before reading a new settings record.
d32s_clear() {
    D32S_format= D32S_HAVE_format=0 D32S_TYPE_format=
    D32S_format_version= D32S_HAVE_format_version=0 D32S_TYPE_format_version=
    D32S_role= D32S_HAVE_role=0 D32S_TYPE_role=
    D32S_node_name= D32S_HAVE_node_name=0 D32S_TYPE_node_name=
    D32S_user_name= D32S_HAVE_user_name=0 D32S_TYPE_user_name=
    D32S_time_zone= D32S_HAVE_time_zone=0 D32S_TYPE_time_zone=
    D32S_keyboard= D32S_HAVE_keyboard=0 D32S_TYPE_keyboard=
    D32S_profile= D32S_HAVE_profile=0 D32S_TYPE_profile=
    D32S_hw_profile= D32S_HAVE_hw_profile=0 D32S_TYPE_hw_profile=
    D32S_disk_encryption= D32S_HAVE_disk_encryption=0 D32S_TYPE_disk_encryption=
    D32S_transport= D32S_HAVE_transport=0 D32S_TYPE_transport=
    D32S_publish= D32S_HAVE_publish=0 D32S_TYPE_publish=
    D32S_written_by= D32S_HAVE_written_by=0 D32S_TYPE_written_by=
    D32S_size_threads= D32S_HAVE_size_threads=0 D32S_TYPE_size_threads=
    D32S_size_mem_gb= D32S_HAVE_size_mem_gb=0 D32S_TYPE_size_mem_gb=
    D32S_size_disk_gb= D32S_HAVE_size_disk_gb=0 D32S_TYPE_size_disk_gb=
    D32S_features_web_switch= D32S_HAVE_features_web_switch=0 D32S_TYPE_features_web_switch=
    D32S_answer_disk_encryption= D32S_HAVE_answer_disk_encryption=0 D32S_TYPE_answer_disk_encryption=
    D32S_settings_disc= D32S_HAVE_settings_disc=0 D32S_TYPE_settings_disc=
    D32S_size= D32S_HAVE_size=0 D32S_TYPE_size=
    D32S_features= D32S_HAVE_features=0 D32S_TYPE_features=
    D32S_ERR= D32S_WHY= D32S_JSON_FILE= D32S_JSON_BYTES=
}

# d32s_store KEY VALUE TYPE: store a known field without evaluating shell text.
d32s_store() {
    case $1 in
    format) D32S_format=$2; D32S_HAVE_format=1; D32S_TYPE_format=$3;;
    format_version) D32S_format_version=$2; D32S_HAVE_format_version=1; D32S_TYPE_format_version=$3;;
    role) D32S_role=$2; D32S_HAVE_role=1; D32S_TYPE_role=$3;;
    node_name) D32S_node_name=$2; D32S_HAVE_node_name=1; D32S_TYPE_node_name=$3;;
    user_name) D32S_user_name=$2; D32S_HAVE_user_name=1; D32S_TYPE_user_name=$3;;
    time_zone) D32S_time_zone=$2; D32S_HAVE_time_zone=1; D32S_TYPE_time_zone=$3;;
    keyboard) D32S_keyboard=$2; D32S_HAVE_keyboard=1; D32S_TYPE_keyboard=$3;;
    profile) D32S_profile=$2; D32S_HAVE_profile=1; D32S_TYPE_profile=$3;;
    hw_profile) D32S_hw_profile=$2; D32S_HAVE_hw_profile=1; D32S_TYPE_hw_profile=$3;;
    disk_encryption) D32S_disk_encryption=$2; D32S_HAVE_disk_encryption=1; D32S_TYPE_disk_encryption=$3;;
    transport) D32S_transport=$2; D32S_HAVE_transport=1; D32S_TYPE_transport=$3;;
    publish) D32S_publish=$2; D32S_HAVE_publish=1; D32S_TYPE_publish=$3;;
    written_by) D32S_written_by=$2; D32S_HAVE_written_by=1; D32S_TYPE_written_by=$3;;
    size_threads) D32S_size_threads=$2; D32S_HAVE_size_threads=1; D32S_TYPE_size_threads=$3;;
    size_mem_gb) D32S_size_mem_gb=$2; D32S_HAVE_size_mem_gb=1; D32S_TYPE_size_mem_gb=$3;;
    size_disk_gb) D32S_size_disk_gb=$2; D32S_HAVE_size_disk_gb=1; D32S_TYPE_size_disk_gb=$3;;
    features_web_switch) D32S_features_web_switch=$2; D32S_HAVE_features_web_switch=1; D32S_TYPE_features_web_switch=$3;;
    answer_disk_encryption) D32S_answer_disk_encryption=$2; D32S_HAVE_answer_disk_encryption=1; D32S_TYPE_answer_disk_encryption=$3;;
    settings_disc) D32S_settings_disc=$2; D32S_HAVE_settings_disc=1; D32S_TYPE_settings_disc=$3;;
    size) D32S_size=$2; D32S_HAVE_size=1; D32S_TYPE_size=$3;;
    features) D32S_features=$2; D32S_HAVE_features=1; D32S_TYPE_features=$3;;
    *) d32s_refuse unknown; return 1;;
    esac
}

# d32s_rank KEY OBJECT: fixed byte order for the finite field set.
d32s_rank() {
    case $2/$1 in
    /disk_encryption) D32S_RANK=1;;
    /features) D32S_RANK=2;;
    /format) D32S_RANK=3;;
    /format_version) D32S_RANK=4;;
    /hw_profile) D32S_RANK=5;;
    /keyboard) D32S_RANK=6;;
    /node_name) D32S_RANK=7;;
    /profile) D32S_RANK=8;;
    /publish) D32S_RANK=9;;
    /role) D32S_RANK=10;;
    /size) D32S_RANK=11;;
    /time_zone) D32S_RANK=12;;
    /transport) D32S_RANK=13;;
    /user_name) D32S_RANK=14;;
    /written_by) D32S_RANK=15;;
    size/disk_gb) D32S_RANK=1;;
    size/mem_gb) D32S_RANK=2;;
    size/threads) D32S_RANK=3;;
    features/web_switch) D32S_RANK=1;;
    *) d32s_refuse unknown; return 1;;
    esac
}

# d32s_parse FILE: a bounded, line-oriented recogniser of canonical JSON.
d32s_parse() {
    local _d32line= _d32key _d32v _d32type _d32obj= _d32depth=0
    local _d32bytes=0 _d32prev=0 _d32subprev=0 _d32comma=0 _d32subcomma=0
    local _d32n=0 _d32subn=0 _d32ended=0 _d32tail
    d32s_clear
    [ -r "$1" ] || { d32s_refuse form; return 1; }
    while :; do
        _d32line=
        if ! IFS= read -r _d32line; then
            [ -z "$_d32line" ] && [ "$_d32ended" = 1 ] && break
            d32s_refuse form; return 1
        fi
        _d32bytes=$((_d32bytes + ${#_d32line} + 1))
        [ "$_d32bytes" -le 4096 ] || { d32s_refuse size; return 1; }
        case $_d32line in *'\'*|*[[:cntrl:]]*|*[![:print:]]*) d32s_refuse form; return 1;; esac
        [ "$_d32ended" = 0 ] || { d32s_refuse form; return 1; }
        if [ "$_d32depth" = 0 ]; then
            [ "$_d32line" = '{' ] || { d32s_refuse form; return 1; }
            _d32depth=1; continue
        fi
        if [ "$_d32depth" = 1 ] && [ "$_d32line" = '}' ]; then
            [ "$_d32comma" = 0 ] || { d32s_refuse form; return 1; }
            _d32ended=1; continue
        fi
        if [ "$_d32depth" = 2 ]; then
            case $_d32line in '  }'|'  },')
                [ "$_d32subn" -gt 0 ] && [ "$_d32subcomma" = 0 ] || { d32s_refuse form; return 1; }
                _d32comma=0; [ "$_d32line" != '  },' ] || _d32comma=1
                _d32depth=1; _d32obj=; continue;;
            esac
            [ "$_d32subn" = 0 ] || [ "$_d32subcomma" = 1 ] || { d32s_refuse form; return 1; }
            case $_d32line in '    "'*) _d32tail=${_d32line#'    "'};; *) d32s_refuse form; return 1;; esac
        else
            [ "$_d32n" = 0 ] || [ "$_d32comma" = 1 ] || { d32s_refuse form; return 1; }
            case $_d32line in '  "'*) _d32tail=${_d32line#'  "'};; *) d32s_refuse form; return 1;; esac
        fi
        _d32key=${_d32tail%%'"'*}
        _d32tail=${_d32tail#*'"'}
        case $_d32tail in ': '*) _d32v=${_d32tail#': '};; *) d32s_refuse form; return 1;; esac
        d32s_rank "$_d32key" "$_d32obj" || return 1
        if [ "$_d32depth" = 1 ]; then
            [ "$D32S_RANK" -gt "$_d32prev" ] || { d32s_refuse form; return 1; }
            _d32prev=$D32S_RANK; _d32n=$((_d32n + 1)); _d32comma=0
            case $_d32v in *,) _d32comma=1; _d32v=${_d32v%,};; esac
        else
            [ "$D32S_RANK" -gt "$_d32subprev" ] || { d32s_refuse form; return 1; }
            _d32subprev=$D32S_RANK; _d32subn=$((_d32subn + 1)); _d32subcomma=0
            case $_d32v in *,) _d32subcomma=1; _d32v=${_d32v%,};; esac
        fi
        if [ "$_d32obj" = '' ]; then
            case $_d32key in size|features)
                d32s_store "$_d32key" '' o
                case $_d32v in
                '{}') continue;;
                '{') [ "$_d32comma" = 0 ] || { d32s_refuse form; return 1; }
                     _d32obj=$_d32key; _d32depth=2; _d32subprev=0; _d32subn=0; continue;;
                *) d32s_refuse form; return 1;;
                esac;;
            esac
        fi
        case $_d32v in
        '"'*'"') _d32v=${_d32v#'"'}; _d32v=${_d32v%'"'}; _d32type=s
            case $_d32v in *'"'*) d32s_refuse form; return 1;; esac;;
        true|false) _d32type=b;;
        0) _d32type=i;;
        ''|0*|*[!0-9]*) d32s_refuse form; return 1;;
        *) _d32type=i;;
        esac
        [ -z "$_d32obj" ] || _d32key=${_d32obj}_$_d32key
        d32s_store "$_d32key" "$_d32v" "$_d32type" || return 1
    done <"$1"
    D32S_JSON_FILE=$1 D32S_JSON_BYTES=$_d32bytes
    d32s_validate
}

# The value predicates below take VALUE and return 0 accepted, 1 refused.
d32s_ok_node_name() {
    [ "${#1}" -le 63 ] || return 1
    case $1 in [a-z]*) ;; *) return 1;; esac
    case $1 in localhost|*[!a-z0-9-]*) return 1;; esac
}
d32s_ok_user_name() {
    [ "${#1}" -le 31 ] || return 1
    case $1 in [a-z]*) ;; *) return 1;; esac
    case $1 in *[!a-z0-9_-]*) return 1;; esac
    case " $D32S_RESERVED_USERS " in *" $1 "*) return 1;; esac
}
d32s_ok_time_zone() {
    [ "${#1}" -le 64 ] || return 1
    case $1 in *\\*|*[[:cntrl:]]*|*[![:print:]]*) return 1;; esac
    echo "x$1" | grep -Eq '^x[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$' || return 1
    if [ -f "${D32_ROOT-}/var/tzlist" ]; then
        grep -qxF "$1" "${D32_ROOT-}/var/tzlist" && ! grep -qxF "$1/" "${D32_ROOT-}/var/tzlist" || return 1
    fi
}
d32s_ok_keyboard() {
    [ "${#1}" -le 32 ] || return 1
    case $1 in [a-z]*) ;; *) return 1;; esac
    case $1 in *[!a-z0-9._-]*) return 1;; esac
}
d32s_ok_profile() { case " $D32S_WS_PROFILES " in *" $1 "*) [ -n "$1" ];; *) return 1;; esac; }
d32s_ok_hw_profile() { case $1 in vm|latitude-5430) ;; *) return 1;; esac; }
d32s_ok_bool() { case $1 in true|false) ;; *) return 1;; esac; }
d32s_ok_transport() { case $1 in tailnet|loopback) ;; *) return 1;; esac; }
d32s_ok_publish() { case $1 in serve-https|tailnet-plain) ;; *) return 1;; esac; }
d32s_ok_size_value() {
    case $1 in ''|0*|*[!0-9]*) return 1;; esac
    [ "${#1}" -le 5 ] && [ "$1" -le 65535 ]
}
d32s_ok_written_by() {
    [ -n "$1" ] && [ "${#1}" -le 80 ] || return 1
    case $1 in *[!A-Za-z0-9.' '/-]*) return 1;; esac
}

# d32s_validate: required fields, types and per-field rules.
d32s_validate() {
    if [ -n "$D32S_JSON_FILE" ]; then
        set -- $(ls -ln "$D32S_JSON_FILE")
        [ "${5-}" = "$D32S_JSON_BYTES" ] || { d32s_refuse form; return 1; }
    fi
    [ "$D32S_HAVE_format" = 1 ] || { d32s_refuse missing format; return 1; }
    [ "$D32S_HAVE_format_version" = 1 ] || { d32s_refuse missing format_version; return 1; }
    [ "$D32S_HAVE_role" = 1 ] || { d32s_refuse missing role; return 1; }
    [ "$D32S_TYPE_format" = s ] && [ "$D32S_format" = deep32-settings ] || { d32s_refuse form; return 1; }
    [ "$D32S_TYPE_format_version" = i ] && [ "$D32S_format_version" = 1 ] || { d32s_refuse form; return 1; }
    [ "$D32S_TYPE_role" = s ] && : || { d32s_refuse form; return 1; }
    [ "$D32S_role" = workstation ] || { d32s_refuse role; return 1; }
    if [ "$D32S_HAVE_node_name" = 1 ]; then
        [ "$D32S_TYPE_node_name" = s ] && d32s_ok_node_name "$D32S_node_name" || { d32s_refuse value node_name; return 1; }
    fi
    if [ "$D32S_HAVE_user_name" = 1 ]; then
        [ "$D32S_TYPE_user_name" = s ] && d32s_ok_user_name "$D32S_user_name" || { d32s_refuse value user_name; return 1; }
    fi
    if [ "$D32S_HAVE_time_zone" = 1 ]; then
        [ "$D32S_TYPE_time_zone" = s ] && d32s_ok_time_zone "$D32S_time_zone" || { d32s_refuse value time_zone; return 1; }
    fi
    if [ "$D32S_HAVE_keyboard" = 1 ]; then
        [ "$D32S_TYPE_keyboard" = s ] && d32s_ok_keyboard "$D32S_keyboard" || { d32s_refuse value keyboard; return 1; }
    fi
    if [ "$D32S_HAVE_profile" = 1 ]; then
        [ "$D32S_TYPE_profile" = s ] && d32s_ok_profile "$D32S_profile" || { d32s_refuse value profile; return 1; }
    fi
    if [ "$D32S_HAVE_hw_profile" = 1 ]; then
        [ "$D32S_TYPE_hw_profile" = s ] && d32s_ok_hw_profile "$D32S_hw_profile" || { d32s_refuse value hw_profile; return 1; }
    fi
    if [ "$D32S_HAVE_disk_encryption" = 1 ]; then
        [ "$D32S_TYPE_disk_encryption" = b ] && d32s_ok_bool "$D32S_disk_encryption" || { d32s_refuse value disk_encryption; return 1; }
    fi
    if [ "$D32S_HAVE_transport" = 1 ]; then
        [ "$D32S_TYPE_transport" = s ] && d32s_ok_transport "$D32S_transport" || { d32s_refuse value transport; return 1; }
    fi
    if [ "$D32S_HAVE_publish" = 1 ]; then
        [ "$D32S_TYPE_publish" = s ] && d32s_ok_publish "$D32S_publish" || { d32s_refuse value publish; return 1; }
    fi
    if [ "$D32S_HAVE_written_by" = 1 ]; then
        [ "$D32S_TYPE_written_by" = s ] && d32s_ok_written_by "$D32S_written_by" || { d32s_refuse value written_by; return 1; }
    fi
    if [ "$D32S_HAVE_size_threads" = 1 ]; then
        [ "$D32S_TYPE_size_threads" = i ] && d32s_ok_size_value "$D32S_size_threads" || { d32s_refuse value size; return 1; }
    fi
    if [ "$D32S_HAVE_size_mem_gb" = 1 ]; then
        [ "$D32S_TYPE_size_mem_gb" = i ] && d32s_ok_size_value "$D32S_size_mem_gb" || { d32s_refuse value size; return 1; }
    fi
    if [ "$D32S_HAVE_size_disk_gb" = 1 ]; then
        [ "$D32S_TYPE_size_disk_gb" = i ] && d32s_ok_size_value "$D32S_size_disk_gb" || { d32s_refuse value size; return 1; }
    fi
    if [ "$D32S_HAVE_features_web_switch" = 1 ]; then
        [ "$D32S_TYPE_features_web_switch" = b ] && d32s_ok_bool "$D32S_features_web_switch" || { d32s_refuse value features; return 1; }
    fi
    if [ "$D32S_HAVE_size" = 1 ]; then
        [ "$D32S_HAVE_size_threads/$D32S_HAVE_size_mem_gb/$D32S_HAVE_size_disk_gb" = 1/1/1 ] || { d32s_refuse value size; return 1; }
    fi
    return 0
}

# d32s_effective: fill defaults without marking absent fields as present.
d32s_effective() {
    D32S_EFF_hw_profile=${D32S_hw_profile:-vm}
    D32S_EFF_disk_encryption=${D32S_disk_encryption-}
    if [ -z "$D32S_EFF_disk_encryption" ]; then
        D32S_EFF_disk_encryption=false
        [ "$D32S_EFF_hw_profile" != latitude-5430 ] || D32S_EFF_disk_encryption=true
    fi
    D32S_EFF_transport=${D32S_transport:-tailnet}
    D32S_EFF_publish=${D32S_publish:-serve-https}
    D32S_EFF_features_web_switch=${D32S_features_web_switch:-false}
    D32S_EFF_profile=${D32S_profile:-unset}
}

# d32s_record_write FILE DISC: atomically save only answered or supplied fields.
d32s_record_write() {
    local _d32file=$1 _d32disc=$2
    case $_d32disc in present|absent) ;; *) return 1;; esac
    d32s_effective
    {
        echo '# deep32 settings record 1'
        [ "${D32S_HAVE_answer_disk_encryption-0}" != 1 ] || echo "answer_disk_encryption=$D32S_answer_disk_encryption"
        [ "${D32S_HAVE_disk_encryption-0}" != 1 ] || echo "disk_encryption=$D32S_disk_encryption"
        [ "${D32S_HAVE_features_web_switch-0}" != 1 ] || echo "feature_web_switch=$D32S_features_web_switch"
        echo "hw_profile=$D32S_EFF_hw_profile"
        [ "${D32S_HAVE_keyboard-0}" != 1 ] || echo "keyboard=$D32S_keyboard"
        [ "${D32S_HAVE_node_name-0}" != 1 ] || echo "node_name=$D32S_node_name"
        [ "${D32S_HAVE_profile-0}" != 1 ] || echo "profile=$D32S_profile"
        [ "${D32S_HAVE_publish-0}" != 1 ] || echo "publish=$D32S_publish"
        echo "settings_disc=$_d32disc"
        [ "${D32S_HAVE_size_disk_gb-0}" != 1 ] || echo "size_disk_gb=$D32S_size_disk_gb"
        [ "${D32S_HAVE_size_mem_gb-0}" != 1 ] || echo "size_mem_gb=$D32S_size_mem_gb"
        [ "${D32S_HAVE_size_threads-0}" != 1 ] || echo "size_threads=$D32S_size_threads"
        [ "${D32S_HAVE_time_zone-0}" != 1 ] || echo "time_zone=$D32S_time_zone"
        [ "${D32S_HAVE_transport-0}" != 1 ] || echo "transport=$D32S_transport"
        [ "${D32S_HAVE_user_name-0}" != 1 ] || echo "user_name=$D32S_user_name"
        [ "${D32S_HAVE_written_by-0}" != 1 ] || echo "written_by=$D32S_written_by"
    } >"$_d32file.new" && chmod 0600 "$_d32file.new" && mv "$_d32file.new" "$_d32file"
}

# d32s_record_read FILE: strict sorted record; typed values checked again.
d32s_record_read() {
    local _d32line _d32key _d32v _d32prev=0 _d32rank
    d32s_clear
    {
        IFS= read -r _d32line && [ "$_d32line" = '# deep32 settings record 1' ] || return 1
        while IFS= read -r _d32line; do
            case $_d32line in *=*) ;; *) return 1;; esac
            _d32key=${_d32line%%=*}; _d32v=${_d32line#*=}
            case $_d32key in
            answer_disk_encryption) _d32rank=1; d32s_ok_bool "$_d32v" || return 1; d32s_store answer_disk_encryption "$_d32v" b;;
            disk_encryption) _d32rank=2; d32s_ok_bool "$_d32v" || return 1; d32s_store disk_encryption "$_d32v" b;;
            feature_web_switch) _d32rank=3; d32s_ok_bool "$_d32v" || return 1; d32s_store features_web_switch "$_d32v" b;;
            hw_profile) _d32rank=4; d32s_ok_hw_profile "$_d32v" || return 1; d32s_store hw_profile "$_d32v" s;;
            keyboard) _d32rank=5; d32s_ok_keyboard "$_d32v" || return 1; d32s_store keyboard "$_d32v" s;;
            node_name) _d32rank=6; d32s_ok_node_name "$_d32v" || return 1; d32s_store node_name "$_d32v" s;;
            profile) _d32rank=7; d32s_ok_profile "$_d32v" || return 1; d32s_store profile "$_d32v" s;;
            publish) _d32rank=8; d32s_ok_publish "$_d32v" || return 1; d32s_store publish "$_d32v" s;;
            settings_disc) _d32rank=9; case $_d32v in present|absent) ;; *) return 1;; esac; d32s_store settings_disc "$_d32v" s;;
            size_disk_gb) _d32rank=10; d32s_ok_size_value "$_d32v" || return 1; d32s_store size_disk_gb "$_d32v" i;;
            size_mem_gb) _d32rank=11; d32s_ok_size_value "$_d32v" || return 1; d32s_store size_mem_gb "$_d32v" i;;
            size_threads) _d32rank=12; d32s_ok_size_value "$_d32v" || return 1; d32s_store size_threads "$_d32v" i;;
            time_zone) _d32rank=13; d32s_ok_time_zone "$_d32v" || return 1; d32s_store time_zone "$_d32v" s;;
            transport) _d32rank=14; d32s_ok_transport "$_d32v" || return 1; d32s_store transport "$_d32v" s;;
            user_name) _d32rank=15; d32s_ok_user_name "$_d32v" || return 1; d32s_store user_name "$_d32v" s;;
            written_by) _d32rank=16; d32s_ok_written_by "$_d32v" || return 1; d32s_store written_by "$_d32v" s;;
            *) return 1;;
            esac
            [ "$_d32rank" -gt "$_d32prev" ] || return 1
            _d32prev=$_d32rank
        done
        [ -z "$_d32line" ] || return 1
    } <"$1"
    [ "$D32S_HAVE_settings_disc/$D32S_HAVE_hw_profile" = 1/1 ]
}

# d32_cd_devices: print optical device names, in kernel order.
d32_cd_devices() {
    local _d32entry _d32name
    for _d32entry in $(sysctl -n hw.disknames 2>/dev/null | sed 's/,/ /g'); do
        _d32name=${_d32entry%%:*}
        case $_d32name in cd[0-9]*)
            case ${_d32name#cd} in *[!0-9]*) continue;; esac
            echo "$_d32name";;
        esac
    done
}

# d32_cd_mount DEV MOUNTPOINT: use c, else the only data partition.
d32_cd_mount() {
    local _d32part _d32chosen= _d32count=0 _d32hasc=0
    for _d32part in $(disklabel "$1" 2>/dev/null | sed -En '/swap|unused/d;/^  [a-p]: /p'); do
        case $_d32part in [a-p]:)
            _d32chosen=${_d32part%:}; _d32count=$((_d32count + 1))
            [ "$_d32chosen" != c ] || _d32hasc=1;;
        esac
    done
    if [ "$_d32hasc" = 1 ]; then _d32chosen=c; else [ "$_d32count" = 1 ] || return 1; fi
    mount -o ro "${D32_ROOT-}/dev/$1$_d32chosen" "$2"
}
d32_cd_umount() { umount "$1"; }

# d32s_node_read FILE: commit D32S_NODE_* only after all checks pass.
d32s_node_read() {
    local _d32line _d32key _d32v _d32rank _d32prev=0
    local _d32node_disk_encryption= _d32have_disk_encryption=0
    local _d32node_feature_web_switch= _d32have_feature_web_switch=0
    local _d32node_hw_profile= _d32have_hw_profile=0
    local _d32node_keyboard= _d32have_keyboard=0
    local _d32node_node_name= _d32have_node_name=0
    local _d32node_profile= _d32have_profile=0
    local _d32node_publish= _d32have_publish=0
    local _d32node_settings_disc= _d32have_settings_disc=0
    local _d32node_size_disk_gb= _d32have_size_disk_gb=0
    local _d32node_size_mem_gb= _d32have_size_mem_gb=0
    local _d32node_size_threads= _d32have_size_threads=0
    local _d32node_time_zone= _d32have_time_zone=0
    local _d32node_transport= _d32have_transport=0
    local _d32node_user_name= _d32have_user_name=0
    local _d32node_written_by= _d32have_written_by=0
    [ -r "$1" ] || return 1
    {
        IFS= read -r _d32line && [ "$_d32line" = format=deep32-node-settings ] || return 1
        IFS= read -r _d32line && [ "$_d32line" = format_version=1 ] || return 1
        while IFS= read -r _d32line; do
            case $_d32line in *=*) ;; *) return 1;; esac
            _d32key=${_d32line%%=*}; _d32v=${_d32line#*=}
            case $_d32key in
            disk_encryption) _d32rank=1; d32s_ok_bool "$_d32v" || return 1; _d32node_disk_encryption=$_d32v; _d32have_disk_encryption=1;;
            feature_web_switch) _d32rank=2; d32s_ok_bool "$_d32v" || return 1; _d32node_feature_web_switch=$_d32v; _d32have_feature_web_switch=1;;
            hw_profile) _d32rank=3; d32s_ok_hw_profile "$_d32v" || return 1; _d32node_hw_profile=$_d32v; _d32have_hw_profile=1;;
            keyboard) _d32rank=4; d32s_ok_keyboard "$_d32v" || return 1; _d32node_keyboard=$_d32v; _d32have_keyboard=1;;
            node_name) _d32rank=5; d32s_ok_node_name "$_d32v" || return 1; _d32node_node_name=$_d32v; _d32have_node_name=1;;
            profile) _d32rank=6; [ "$_d32v" = unset ] || d32s_ok_profile "$_d32v" || return 1; _d32node_profile=$_d32v; _d32have_profile=1;;
            publish) _d32rank=7; d32s_ok_publish "$_d32v" || return 1; _d32node_publish=$_d32v; _d32have_publish=1;;
            settings_disc) _d32rank=8; case $_d32v in present|absent) ;; *) return 1;; esac; _d32node_settings_disc=$_d32v; _d32have_settings_disc=1;;
            size_disk_gb) _d32rank=9; d32s_ok_size_value "$_d32v" || return 1; _d32node_size_disk_gb=$_d32v; _d32have_size_disk_gb=1;;
            size_mem_gb) _d32rank=10; d32s_ok_size_value "$_d32v" || return 1; _d32node_size_mem_gb=$_d32v; _d32have_size_mem_gb=1;;
            size_threads) _d32rank=11; d32s_ok_size_value "$_d32v" || return 1; _d32node_size_threads=$_d32v; _d32have_size_threads=1;;
            time_zone) _d32rank=12; d32s_ok_time_zone "$_d32v" || return 1; _d32node_time_zone=$_d32v; _d32have_time_zone=1;;
            transport) _d32rank=13; d32s_ok_transport "$_d32v" || return 1; _d32node_transport=$_d32v; _d32have_transport=1;;
            user_name) _d32rank=14; d32s_ok_user_name "$_d32v" || return 1; _d32node_user_name=$_d32v; _d32have_user_name=1;;
            written_by) _d32rank=15; d32s_ok_written_by "$_d32v" || return 1; _d32node_written_by=$_d32v; _d32have_written_by=1;;
            *) return 1;;
            esac
            [ "$_d32rank" -gt "$_d32prev" ] || return 1
            _d32prev=$_d32rank
        done
        [ -z "$_d32line" ] || return 1
    } <"$1"
    [ "$_d32have_disk_encryption" = 1 ] || return 1
    [ "$_d32have_feature_web_switch" = 1 ] || return 1
    [ "$_d32have_hw_profile" = 1 ] || return 1
    [ "$_d32have_keyboard" = 1 ] || return 1
    [ "$_d32have_node_name" = 1 ] || return 1
    [ "$_d32have_profile" = 1 ] || return 1
    [ "$_d32have_publish" = 1 ] || return 1
    [ "$_d32have_settings_disc" = 1 ] || return 1
    [ "$_d32have_time_zone" = 1 ] || return 1
    [ "$_d32have_transport" = 1 ] || return 1
    [ "$_d32have_user_name" = 1 ] || return 1
    D32S_NODE_disk_encryption=$_d32node_disk_encryption
    D32S_NODE_feature_web_switch=$_d32node_feature_web_switch
    D32S_NODE_hw_profile=$_d32node_hw_profile
    D32S_NODE_keyboard=$_d32node_keyboard
    D32S_NODE_node_name=$_d32node_node_name
    D32S_NODE_profile=$_d32node_profile
    D32S_NODE_publish=$_d32node_publish
    D32S_NODE_settings_disc=$_d32node_settings_disc
    D32S_NODE_size_disk_gb=$_d32node_size_disk_gb
    D32S_NODE_size_mem_gb=$_d32node_size_mem_gb
    D32S_NODE_size_threads=$_d32node_size_threads
    D32S_NODE_time_zone=$_d32node_time_zone
    D32S_NODE_transport=$_d32node_transport
    D32S_NODE_user_name=$_d32node_user_name
    D32S_NODE_written_by=$_d32node_written_by
    return 0
}
