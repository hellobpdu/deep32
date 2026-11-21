#!/usr/bin/env bash
# Download and verify public release media. No account or token is used.
# Exit 3 means requested disc2 or disc3 is pending in the authenticated release.
GITHUB_RELEASE_BASE='https://github.com/hellobpdu/deep32/releases/download'
set -euo pipefail
umask 077

die() { printf 'Download refused: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' 'Usage: deep32-get.sh disc1|disc2|disc3 [--to DIR] [--release TAG] [--repo OWNER/NAME] [--key PUBLIC.asc|PUBLIC.gpg] [--gpg FILE] [--fingerprint HEX] [--from DIR | --source DIR]' \
      'Local media: --from verifies in place; --source is an alias. Neither can be combined with --to.' \
      'GPU selection: disc3 --gpu-class CLASS [--ingestion] [--full]' \
      'VM artefacts: disc1 --prepare-vm --hypervisor hyper-v|kvm (writes files and commands only)' \
      'Install: disc3 --install --journey J1|J2|J3|J4 [--data-root DIR] [--docker-storage-path DIR] [--settings FILE] [--node-name NAME] [--user-name NAME] [--time-zone ZONE] [--keyboard LAYOUT] [--profile ID] [--transport tailnet|loopback] [--publish serve-https] [--form docker] [--platform linux] [--docker-role forge|full] [--content DIR (full only)] [--confirm-tailnet SUFFIX] [--non-interactive --accept-plan DIGEST]; Forge is the default'
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
helper="$script_dir/deep32_get_manifest.py"
whole_helper="$script_dir/deep32_get_whole.py"
settings_helper="$script_dir/deep32_settings.py"
join_helper="$script_dir/deep32_download.py"
disc=''; to='./deep32-downloads'; release='v0.1.0-alpha.2'; repo=hellobpdu/deep32; install=0; source=''
gpu_class=''; ingestion=0; full=0
journey=''; form=docker; platform=linux; docker_role=forge; data_root=/opt/deep32; content=''; docker_storage_path=''; non_interactive=0; accept_plan=''; settings_args=()
gpg=''; fingerprint=B205B406548627AABF5250D8A078C5F25D025DD6; prepare_vm=0; hypervisor=''
seen_options=' '
key="$script_dir/../../install/release-key.gpg"
while (($#)); do
    case "$1" in
        disc1|disc2|disc3) [[ -z "$disc" ]] || die 'Specify one disc'; disc=$1; shift ;;
        --to|--release|--repo|--key|--from|--source|--gpu-class|--gpg|--fingerprint|--hypervisor|--journey|--settings|--node-name|--user-name|--time-zone|--keyboard|--profile|--transport|--publish|--form|--platform|--docker-role|--accept-plan|--confirm-tailnet|--data-root|--content|--docker-storage-path)
            (($# >= 2)) || die "Missing value for $1"
            [[ -n "$2" ]] || die "Empty value for $1"
            [[ "$seen_options" != *" $1 "* ]] || die 'Duplicate option'
            seen_options+="$1 "
            case "$1" in
                --to) to=$2;; --release) release=$2;; --repo) repo=$2;; --key) key=$2;; --from|--source) [[ -z "$source" ]] || die 'Select only one of --from and --source'; source=$2;; --gpu-class) gpu_class=$2;;
                --gpg) gpg=$2;; --fingerprint) fingerprint=$2;; --hypervisor) hypervisor=$2;;
                --journey) journey=$2;; --form) form=$2;; --platform) platform=$2;; --docker-role) docker_role=$2;; --data-root) data_root=$2;; --content) content=$2;; --docker-storage-path) docker_storage_path=$2;;
                --accept-plan) accept_plan=$2;; *) settings_args+=("$1" "$2");;
            esac
            shift 2 ;;
        --install|--non-interactive|--prepare-vm|--ingestion|--full)
            [[ "$seen_options" != *" $1 "* ]] || die 'Duplicate option'
            seen_options+="$1 "
            case "$1" in --install) install=1;; --non-interactive) non_interactive=1;; --prepare-vm) prepare_vm=1;; --ingestion) ingestion=1;; --full) full=1;; esac
            shift ;;
        --status|--resume|--remove|--pair-headend|--pairing-console) printf '%s\n' 'This lifecycle action needs Phase B media. Next: use the status and recovery commands in src/gpu-pack/PACK.md.' >&2; exit 10 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; die "Unknown argument: $1" ;;
    esac
done
[[ -n "$disc" ]] || { usage >&2; die 'Specify disc1, disc2 or disc3'; }
[[ "$release" =~ ^v[A-Za-z0-9][A-Za-z0-9._-]*$ && "$release" != *..* ]] || die 'Invalid release tag'
[[ "$repo" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$ && "$repo" != *..* ]] || die 'Invalid release repository'
[[ -z "$source" || "$seen_options" != *' --to '* ]] || die '--from or --source cannot be combined with --to'
[[ "$source" != *[[:cntrl:]]* ]] || die 'Invalid local media path'
[[ "$disc" == disc3 || ( -z "$gpu_class" && $ingestion == 0 && $full == 0 ) ]] || die 'GPU capability selection requires disc3'
[[ "$release" != v0.1.0-alpha.1 || ( -z "$gpu_class" && $ingestion == 0 && $full == 0 ) ]] || die 'GPU capability selection requires whole-image release metadata'
[[ $install == 0 || "$disc" == disc3 ]] || die '--install is only supported for disc3'
[[ "$disc" != disc3 || "$release" == v0.1.0-alpha.1 || -n "$gpu_class" ]] || die 'disc3 requires the observed --gpu-class; no class fallback is permitted'
[[ -z "$journey" || "$journey" =~ ^J[1-5]$ ]] || die 'Invalid journey'
[[ $prepare_vm == 0 || ( "$disc" == disc1 && "$hypervisor" =~ ^(hyper-v|kvm)$ ) ]] || die '--prepare-vm requires disc1 --hypervisor hyper-v|kvm'
[[ -z "$hypervisor" || $prepare_vm == 1 ]] || die '--hypervisor requires --prepare-vm'
split_install=0
if [[ $install == 1 ]]; then
    [[ "$journey" =~ ^J[1234]$ ]] || die 'Select --journey J1, J2, J3 or J4 for the GPU loader'
    [[ "$form" == docker && "$platform" == linux && "$docker_role" =~ ^(full|forge)$ ]] || { printf '%s\n' 'Select the Docker form on Linux, with the full or Forge role.' >&2; exit 10; }
    [[ "$docker_role" != full || -n "$content" ]] || die 'Select --content DIR for the full Docker role'
    [[ "$docker_role" != forge || "$journey" != J3 ]] || die 'J3 requires the full Docker role'
    [[ "$docker_role" != forge || -z "$content" ]] || die '--content is only supported for the explicit full Docker role'
    if [[ "$release" != v0.1.0-alpha.1 ]]; then
        [[ "$docker_role" == forge && $full == 0 ]] || die 'Split Disc 3 installation supports the Forge role only'
        split_install=1
        ingestion=1
        printf '%s\n' 'Split Disc 3 installation includes the ingestion ISO required by the Forge loader.'
    fi
fi
linux_forge_evidence=0
if [[ $split_install == 1 && "$release" == v0.1.0-alpha.2 && -n "$source" ]]; then
    linux_forge_evidence=1
fi
check_install_prerequisites() {
    local storage_args=()
    if [[ $linux_forge_evidence == 1 ]]; then
        storage_args=(--forge-media "$source" --forge-class "$gpu_class" --forge-data-root "$data_root"
                      --forge-key "$key" --forge-fingerprint "$fingerprint")
        if [[ -n "$gpg" ]]; then storage_args+=(--forge-gpg "$gpg"); fi
        storage_args+=(--forge-plan "$1" --accept-plan "$accept_plan")
    fi
    printf '%s\n' 'Checking read-only prerequisites for this GPU host'
    if ! bash -- "$script_dir/../../install/deep32-inventory.sh" --journey "$journey" --role gpu-host --check-prereqs "${storage_args[@]}"; then
        printf '%s\n' 'Prerequisites need operator action; no installation change was made by this stage.' \
            "Next: resolve the reported prerequisite and rerun the same install command." >&2
        exit 10
    fi
}
if [[ $split_install == 1 ]]; then
    # Keep the sealed loader on the same local engine the inventory qualifies.
    if [[ $linux_forge_evidence == 1 ]]; then
        unset DEEP32_INVENTORY_TEST_ROOT DEEP32_INVENTORY_TEST_UNAME_S DEEP32_INVENTORY_TEST_UNAME_M
    fi
    unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH TS_SOCKET
    export DOCKER_HOST=unix:///var/run/docker.sock
fi
if [[ $install == 1 && $linux_forge_evidence == 0 ]]; then check_install_prerequisites; fi
# the Python requirement and corrective action apply to each host.
python_requirement="Install Python 3.10 or later using this host's package manager or the Python vendor download; reopen the terminal and run this command again"
command -v python3 >/dev/null 2>&1 || die "$python_requirement"
python3 -I -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "$python_requirement"
if [[ $split_install == 1 ]]; then
    python3 -I -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' ||
        die 'Install Python 3.12 or later on this Docker host for the split Disc 3 Forge loader'
fi
py() { python3 -I -B "$helper" "$@"; }
py path "$helper" file >/dev/null
key=$(py path "$key" new)
meta=''; cache=''; mount_dir=''; mounted=0; retain_mount=0
split_mounts=(); split_pending=''
component_paths=(); component_sizes=(); component_hashes=(); component_plan_args=()
cleanup() {
    local result=$? i directory
    trap - EXIT
    for ((i=${#split_mounts[@]}-1; i>=0; i--)); do
        directory=${split_mounts[$i]}
        if umount -- "$directory"; then
            rmdir -- "$directory" 2>/dev/null || true
        else
            printf 'Could not unmount %s; unmount it before removing the directory\n' "$directory" >&2
            result=1
        fi
    done
    if [[ -n "$split_pending" ]]; then rmdir -- "$split_pending" 2>/dev/null || true; fi
    if [[ $mounted == 1 && $retain_mount == 0 ]]; then
        if umount -- "$mount_dir"; then mounted=0; else
            printf 'Could not unmount %s; unmount it before removing the directory\n' "$mount_dir" >&2
            result=1
        fi
    fi
    if [[ -n "$mount_dir" && $mounted == 0 ]]; then rmdir -- "$mount_dir" 2>/dev/null || true; fi
    if [[ -n "$meta" ]]; then
        rm -f -- "$meta/ASSEMBLY.json" "$meta/SHA256SUMS" "$meta/SHA256SUMS.sig" "$meta/status" "$meta/key-status" "$meta/selection" "$meta/gpg-diagnostics" "$meta/transfer-diagnostics" "$meta/install-plan.json" "$meta/plan.json" "$meta/settings.json" "$meta/settings-deployment.json"
        rmdir -- "$meta" 2>/dev/null || true
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mount_split_image() {
    local i=$1 result=0 interrupted=0
    split_pending=$(mktemp -d "$meta/mount.XXXXXXXX")
    py path "$split_pending" dir >/dev/null
    printf 'Mounting %s read-only\n' "${component_names[$i]}"
    py check-file "${component_paths[$i]}" "${component_sizes[$i]}" "${component_hashes[$i]}"
    # Record the successful mount before honoring an interruption of mount.
    trap 'interrupted=130' INT
    trap 'interrupted=143' TERM
    mount -o loop,ro -- "${component_paths[$i]}" "$split_pending" || result=$?
    if [[ $result == 0 ]]; then
        split_mounts+=("$split_pending")
        split_pending=''
    fi
    trap 'exit 130' INT
    trap 'exit 143' TERM
    if [[ $interrupted != 0 ]]; then exit "$interrupted"; fi
    return "$result"
}

if [[ -z "$source" ]]; then
    to=$(py mkdir "$to")
    cache=$(py mkdir "$to/.deep32-$release")
fi
if [[ -n "$source" ]]; then
    # Local media can be read-only. Writable scratch belongs to the host.
    meta=$(mktemp -d "${TMPDIR:-/tmp}/deep32-get.XXXXXXXX")
else
    meta=$(mktemp -d "$cache/.metadata.XXXXXXXX")
fi
py path "$meta" dir >/dev/null

if [[ -n "$source" || "$release" != v0.1.0-alpha.1 ]]; then
    py path "$whole_helper" file >/dev/null
    whole_args=("$disc" --release "$release" --repo "$repo" --key "$key" --fingerprint "$fingerprint")
    [[ -z "$gpg" ]] || whole_args+=(--gpg "$gpg")
    if [[ -n "$source" ]]; then
        whole_args+=(--from "$source")
    else
        whole_args+=(--to "$to" --cache "$cache")
    fi
    [[ -z "$gpu_class" ]] || whole_args+=(--gpu-class "$gpu_class")
    [[ $ingestion == 0 ]] || whole_args+=(--ingestion)
    [[ $full == 0 ]] || whole_args+=(--full)
    python3 -I -B "$whole_helper" "${whole_args[@]}" >"$meta/selection"
    records=0
    selected_names=' '
    while IFS=$'\t' read -r selected_path selected_name selected_size selected_hash extra; do
        ((records+=1))
        [[ -n "$selected_path" && -n "$selected_name" && "$selected_size" =~ ^[1-9][0-9]*$ && "$selected_hash" =~ ^[0-9a-f]{64}$ && -z "$extra" ]] || die 'Invalid helper output'
        [[ "$selected_names" != *" $selected_name "* ]] || die 'Duplicate helper image record'
        selected_names+="$selected_name "
        selected_path=$(py path "$selected_path" file)
        selected_literal=${selected_path//\\//}
        [[ "${selected_literal##*/}" == "$selected_name" ]] || die 'Invalid helper image name'
        py check-file "$selected_path" "$selected_size" "$selected_hash"
        if ((records == 1)); then
            destination=$selected_path; image=$selected_name; image_size=$selected_size; image_hash=$selected_hash
        fi
        if [[ $split_install == 1 ]]; then
            component_paths+=("$selected_path")
            component_sizes+=("$selected_size")
            component_hashes+=("$selected_hash")
        fi
    done <"$meta/selection"
    if [[ $split_install == 1 ]]; then
        ((records == 3)) || die 'Signed installation selection must include core, the selected embedder and ingestion'
        component_names=(core "embedder-$gpu_class" ingestion)
        for ((i=0; i<3; i++)); do
            component_plan_args+=(--media-component "${component_names[$i]}" "${component_sizes[$i]}" "${component_hashes[$i]}")
        done
    elif [[ "$disc" == disc3 && "$release" != v0.1.0-alpha.1 ]]; then
        ((records >= 2 && records <= 4)) || die 'Invalid helper capability records'
    else
        [[ $records == 1 ]] || die 'Invalid helper output'
    fi
else
    # Authenticated alpha.1 is the explicit legacy split-release path.
    command -v curl >/dev/null 2>&1 || die 'Install curl, then run this command again'

download() {
    local name=$1 target=$2 maximum=$3
    py partial "$target" "$maximum" || return
    curl --fail --location --retry 0 -C - --max-filesize "$maximum" --output "$target" "https://github.com/$repo/releases/download/$release/$name"
}
download SHA256SUMS "$meta/SHA256SUMS" 5242880 2>"$meta/transfer-diagnostics" || {
    transfer_status=$?
    cat -- "$meta/transfer-diagnostics" >&2
    exit "$transfer_status"
}
download SHA256SUMS.sig "$meta/SHA256SUMS.sig" 5242880 2>>"$meta/transfer-diagnostics" || {
    transfer_status=$?
    cat -- "$meta/transfer-diagnostics" >&2
    exit "$transfer_status"
}
download ASSEMBLY.json "$meta/ASSEMBLY.json" 5242880 2>>"$meta/transfer-diagnostics" || {
    transfer_status=$?
    cat -- "$meta/transfer-diagnostics" >&2
    exit "$transfer_status"
}
py authenticate "$meta" "$key" "$fingerprint" "$gpg"
selection_status=0
py selection "$meta/ASSEMBLY.json" "$meta/SHA256SUMS" "$disc" "$release" >"$meta/selection" || selection_status=$?
if (( selection_status != 0 )); then
    exit "$selection_status"
fi
printf 'Reading release %s\n' "$release"
printf '%s\n' 'Verifying release signature'
cat -- "$meta/transfer-diagnostics" >&2
for name in SHA256SUMS SHA256SUMS.sig ASSEMBLY.json; do py refresh "$meta/$name" "$cache/$name"; done

image=''; image_size=''; image_hash=''
assets=(); sizes=(); hashes=()
while IFS=$'\t' read -r kind name size digest; do
    case "$kind" in
        image) image=$name; image_size=$size; image_hash=$digest ;;
        asset) assets+=("$name"); sizes+=("$size"); hashes+=("$digest") ;;
        *) die 'Invalid helper output' ;;
    esac
done <"$meta/selection"
[[ -n "$image" && ${#assets[@]} -gt 0 ]] || die 'No disc payload selected'
destination="$to/$image"
py path "$destination" new >/dev/null
py storage "$cache/ASSEMBLY.json" "$cache/SHA256SUMS" "$disc" "$release" "$to" "$cache" sh "$hypervisor"
py publication-check "$to"
py publication-check "$cache"
if [[ -e "$destination" ]]; then
    py check-file "$destination" "$image_size" "$image_hash"
    printf 'Reusing verified image %s\n' "$image"
else
    parts=()
    for ((i=0; i<${#assets[@]}; i++)); do
        name=${assets[$i]}; size=${sizes[$i]}; digest=${hashes[$i]}
        if [[ "$name" == "$image" ]]; then asset="$destination"; else asset="$cache/$name"; fi
        py path "$asset" new >/dev/null
        if [[ -e "$asset" ]]; then
            py check-file "$asset" "$size" "$digest"
            printf 'Reusing verified part %s\n' "$name"
        else
            partial="$asset.download"
            printf 'Downloading %s\n' "$name"
            py partial "$partial" "$size"
            if [[ "$(py length "$partial")" != "$size" ]]; then
                download "$name" "$partial" "$size"
            fi
            py check-file "$partial" "$size" "$digest"
            py publish "$partial" "$asset"
        fi
        parts+=("$asset")
    done
    if [[ "${assets[0]}" != "$image" ]]; then
        printf 'Joining %s\n' "$image"
        py join "$destination" "$image_size" "$image_hash" "${parts[@]}"
    fi
    py check-file "$destination" "$image_size" "$image_hash"
fi
printf 'Verified %s\n' "$destination"
fi

if [[ -n "$source" && ( $prepare_vm == 1 || $install == 1 ) ]]; then
    # A failed local verification must not create the preparation/install target.
    if [[ $split_install == 1 ]]; then
        to=$(py path "$(dirname -- "$destination")" dir)
    else
        to=$(py mkdir "$to")
        if [[ $install == 1 ]]; then cache=$(py mkdir "$to/.deep32-$release"); fi
    fi
fi
if [[ $prepare_vm == 1 ]]; then
    py check-file "$destination" "$image_size" "$image_hash"
    py prepare-vm "$destination" "$image_size" "$image_hash" "$hypervisor" "$to"
elif [[ $install == 1 ]]; then
    [[ "$(id -u)" == 0 ]] || die 'Run this command as root to mount and install disc3'
    [[ $non_interactive == 1 || ! -t 0 ]] || settings_args+=(--interactive)
    [[ $linux_forge_evidence == 0 ]] || settings_args+=(--linux-forge-tailnet)
    [[ -z "$docker_storage_path" ]] || settings_args+=(--docker-storage-path "$docker_storage_path")
    python3 -I -B "$settings_helper" plan --media-sha256 "$image_hash" --destination "$to" --journey "$journey" \
        --form "$form" --platform "$platform" --docker-role "$docker_role" --data-root "$data_root" --content "$content" \
        "${settings_args[@]}" "${component_plan_args[@]}" >"$meta/install-plan.json"
    plan_digest=$(python3 -I -B -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="ascii"))["digest"])' "$meta/install-plan.json")
    cat -- "$meta/install-plan.json"
    if [[ -z "$accept_plan" && $non_interactive == 0 && -t 0 ]]; then
        printf 'Review this target and settings. Type the plan digest to install: '
        IFS= read -r accept_plan
    fi
    [[ "$accept_plan" == "$plan_digest" ]] || die 'Plan consent required; rerun with --accept-plan and the displayed digest'
    # Authenticate the complete consented host and release inputs before any
    # mount, persistent join cache, or installation write.
    if [[ $linux_forge_evidence == 1 ]]; then check_install_prerequisites "$meta/install-plan.json"; fi
    command -v mount >/dev/null 2>&1 || die 'The mount command is required for --install'
    command -v umount >/dev/null 2>&1 || die 'The umount command is required for --install'
    if [[ $split_install == 1 ]]; then
        py path "$join_helper" file >/dev/null
        joined=$(python3 -I -B "$settings_helper" media-cache --plan "$meta/install-plan.json" --accept-plan "$accept_plan")
        for ((i=0; i<3; i++)); do mount_split_image "$i"; done
        python3 -I -B "$join_helper" --core "${split_mounts[0]}" --embedder "${split_mounts[1]}" \
            --ingestion "${split_mounts[2]}" --gpu-class "$gpu_class" --output "$joined" --data-root "$data_root"
        core_dir=${split_mounts[0]}
        payload_dir="$joined/payload"
    else
        mount_dir=$(mktemp -d "$cache/mount.XXXXXXXX")
        py path "$mount_dir" dir >/dev/null
        py check-file "$destination" "$image_size" "$image_hash"
        printf '%s\n' 'Mounting disc3 read-only'
        mount -o loop,ro -- "$destination" "$mount_dir"
        mounted=1
        core_dir=$mount_dir
        payload_dir="$mount_dir/deep32/payload"
    fi
    py path "$core_dir/forms/load-docker.sh" file >/dev/null
    py path "$payload_dir" dir >/dev/null
    # The final prerequisite pass belongs immediately before this target write.
    check_install_prerequisites "$meta/install-plan.json"
    loader_args=(--docker-role "$docker_role" --platform "$platform")
    python3 -I -B "$settings_helper" stage --plan "$meta/install-plan.json" --accept-plan "$accept_plan" --directory "$meta"
    loader_args+=(--data-root "$data_root" --settings "$meta/settings.json")
    [[ "$docker_role" != full ]] || loader_args+=(--content "$content")
    [[ -z "$docker_storage_path" ]] || loader_args+=(--docker-storage-path "$docker_storage_path")
    if [[ $split_install == 0 ]]; then retain_mount=1; fi
    printf '%s\n' 'Running the disc3 Docker form'
    python3 -I -B "$settings_helper" run-loader -- bash "$core_dir/forms/load-docker.sh" "$payload_dir" "${loader_args[@]}"
    printf '%s\n' 'Disc3 loader returned; settings handoff completed.'
    if [[ $split_install == 1 ]]; then
        printf 'Verified joined payload retained at %s\n' "$joined"
    else
        printf '%s\n' 'Keep verified GPU pack media attached; the full Docker role also retains content media.'
    fi
    printf '%s\n' 'Next: review GPU pack status and the host publication request before authorizing Tailscale Serve.'

else
    case "$disc" in
        disc1) printf '%s\n' 'Write the verified disc1 image to a USB stick to start Workstation.' ;;
        disc2) printf '%s\n' 'Attach the verified disc2 content image in Workstation and import its content.' ;;
        # multi-media installation is provided by the runtime lane.
        disc3) printf '%s\n' 'Selected GPU capability packs verified.' ;;
    esac
fi
