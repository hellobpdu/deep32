#!/bin/ksh
# 90-node-settings.sh: keep the installed names and choices, contract C7.
# D32_ROOT and D32_PATH select an isolated test installation.
PATH=${D32_PATH:-/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin}
export PATH
. "${D32_ROOT-}/etc/d32/site-lib.sh"
. "${D32_ROOT-}/usr/local/d32/libexec/d32-settings.sh"
d32s_clear
if [ -f "${D32_ROOT-}/etc/d32-settings.conf" ]; then
    d32s_record_read "${D32_ROOT-}/etc/d32-settings.conf" || die 'the settings record from the installer is not valid'
else
    d32test || die 'no settings record from the installer'
    D32S_settings_disc=absent
    D32S_hw_profile=vm
    d32 'item test image: no settings record, so no settings disc and hw_profile vm'
fi
d32s_effective
u=$(cat "${D32_ROOT-}/etc/d32/desktop-user")
if [ -f "${D32_ROOT-}/etc/myname" ]; then
    IFS= read -r full <"${D32_ROOT-}/etc/myname"
else
    full=$(hostname)
fi
node=${full%%.*}
tz=$(ls -l "${D32_ROOT-}/etc/localtime" | sed 's|.* -> .*usr/share/zoneinfo/||')
kbd=default
[ ! -f "${D32_ROOT-}/etc/kbdtype" ] || IFS= read -r kbd <"${D32_ROOT-}/etc/kbdtype"
if [ "$D32S_settings_disc" = present ]; then
    [ "${D32S_HAVE_node_name-0}" != 1 ] || [ "$D32S_node_name" = "$node" ] || die 'installed node_name differs from the settings disc'
    [ "${D32S_HAVE_user_name-0}" != 1 ] || [ "$D32S_user_name" = "$u" ] || die 'installed user_name differs from the settings disc'
    [ "${D32S_HAVE_time_zone-0}" != 1 ] || [ "$D32S_time_zone" = "$tz" ] || die 'installed time_zone differs from the settings disc'
    [ "${D32S_HAVE_keyboard-0}" != 1 ] || [ "$D32S_keyboard" = "$kbd" ] || die 'installed keyboard differs from the settings disc'
fi
nf=${D32_ROOT-}/etc/d32/node.conf
{
    echo format=deep32-node-settings
    echo format_version=1
    echo "disk_encryption=${D32S_answer_disk_encryption:-$D32S_EFF_disk_encryption}"
    echo "feature_web_switch=$D32S_EFF_features_web_switch"
    echo "hw_profile=$D32S_EFF_hw_profile"
    echo "keyboard=$kbd"
    echo "node_name=$node"
    echo "profile=$D32S_EFF_profile"
    echo "publish=$D32S_EFF_publish"
    echo "settings_disc=$D32S_settings_disc"
    [ "${D32S_HAVE_size_disk_gb-0}" != 1 ] || echo "size_disk_gb=$D32S_size_disk_gb"
    [ "${D32S_HAVE_size_mem_gb-0}" != 1 ] || echo "size_mem_gb=$D32S_size_mem_gb"
    [ "${D32S_HAVE_size_threads-0}" != 1 ] || echo "size_threads=$D32S_size_threads"
    echo "time_zone=$tz"
    echo "transport=$D32S_EFF_transport"
    echo "user_name=$u"
    [ "${D32S_HAVE_written_by-0}" != 1 ] || echo "written_by=$D32S_written_by"
} >"$nf.new"
d32s_node_read "$nf.new" || die 'the node settings written here are not valid'
chown root:wheel "$nf.new"
chmod 0444 "$nf.new"
mv "$nf.new" "$nf"
lf=${D32_ROOT-}/etc/d32/link.conf
if [ -f "$lf" ]; then
    [ "$(grep -c '^hostname=' "$lf")/$(grep -c '^ports=' "$lf")" = 1/1 ] || die 'link.conf must hold exactly one hostname= line and one ports= line'
    ports='hvn0 vio0 em0 vmx0'
    [ "$D32S_EFF_hw_profile" != latitude-5430 ] || ports='em0 ure0'
    sed -e "s/^hostname=.*/hostname=$node/" -e "s/^ports=.*/ports=$ports/" "$lf" >"$lf.new"
    chown root:wheel "$lf.new"
    chmod 0444 "$lf.new"
    mv "$lf.new" "$lf"
else
    d32test || die 'no /etc/d32/link.conf'
    d32 'item test image: no link.conf, skipped'
fi
if [ "$D32S_EFF_transport" = loopback ]; then
    install -d -o root -g wheel -m 0700 "${D32_ROOT-}/etc/d32/state"
    echo disabled >"${D32_ROOT-}/etc/d32/state/link"
    chmod 0600 "${D32_ROOT-}/etc/d32/state/link"
fi
hf=${D32_ROOT-}/etc/hosts
found=0
{
    while IFS= read -r line; do
        case $line in 127.0.0.1[[:space:]]*)
            found=1
            case " $line " in *" $node "*) ;; *) line="$line $node";; esac
            case " $line " in *" $full "*) ;; *) line="$line $full";; esac;;
        esac
        printf '%s\n' "$line"
    done <"$hf"
    [ "$found" != 0 ] || printf '127.0.0.1\tlocalhost %s %s\n' "$node" "$full"
} >"$hf.new"
chown root:wheel "$hf.new"
chmod 0644 "$hf.new"
mv "$hf.new" "$hf"
rm -f "${D32_ROOT-}/etc/d32-settings.conf" "${D32_ROOT-}/etc/d32-c6.conf"
d32 "item node settings: node_name $node, hw_profile $D32S_EFF_hw_profile, transport $D32S_EFF_transport"
