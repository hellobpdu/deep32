#!/usr/bin/env bash
# Download and verify one public release disc. No account or token is used.
# Exit 3 means requested disc2 or disc3 is pending in the authenticated release.
GITHUB_RELEASE_BASE='https://github.com/hellobpdu/deep32/releases/download'
set -euo pipefail

die() { printf 'Download refused: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' 'Usage: deep32-get.sh disc1|disc2|disc3 [--to DIR] [--release v0.1.0-alpha.1] [--install] [--key FILE] [--source LOCAL_DIRECTORY_OR_FILE_URL]'
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
helper="$script_dir/deep32_get_manifest.py"
disc=''; to='./deep32-downloads'; release='v0.1.0-alpha.1'; install=0; source=''
key="$script_dir/../../install/release-key.gpg"
while (($#)); do
    case "$1" in
        disc1|disc2|disc3) [[ -z "$disc" ]] || die 'Specify one disc'; disc=$1; shift ;;
        --to|--release|--key|--source)
            (($# >= 2)) || die "Missing value for $1"
            [[ -n "$2" ]] || die "Empty value for $1"
            case "$1" in --to) to=$2;; --release) release=$2;; --key) key=$2;; --source) source=$2;; esac
            shift 2 ;;
        --install) install=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; die "Unknown argument: $1" ;;
    esac
done
[[ -n "$disc" ]] || { usage >&2; die 'Specify disc1, disc2 or disc3'; }
[[ "$release" =~ ^v[A-Za-z0-9][A-Za-z0-9._-]*$ && "$release" != *..* ]] || die 'Invalid release tag'
[[ $install == 0 || "$disc" == disc3 ]] || die '--install is only supported for disc3'
command -v python3 >/dev/null 2>&1 || die 'Install Python 3.10 or later, then run this command again'
python3 -I -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die 'Python 3.10 or later is required'
py() { python3 -I -B "$helper" "$@"; }
py path "$helper" file >/dev/null
to=$(py mkdir "$to")
cache=$(py mkdir "$to/.deep32-$release")
gpg_home=$(py mkdir "$cache/gpg")
key=$(py path "$key" new)
meta=''; mount_dir=''; mounted=0
cleanup() {
    local result=$?
    trap - EXIT
    if [[ $mounted == 1 ]]; then
        if umount -- "$mount_dir"; then mounted=0; else
            printf 'Could not unmount %s; unmount it before removing the directory\n' "$mount_dir" >&2
            result=1
        fi
    fi
    if [[ -n "$mount_dir" && $mounted == 0 ]]; then rmdir -- "$mount_dir" 2>/dev/null || true; fi
    if [[ -n "$meta" ]]; then
        rm -f -- "$meta/ASSEMBLY.json" "$meta/SHA256SUMS" "$meta/SHA256SUMS.sig" "$meta/status" "$meta/key-status" "$meta/selection" "$meta/gpg-diagnostics" "$meta/transfer-diagnostics"
        rmdir -- "$meta" 2>/dev/null || true
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

gpg_base=(gpg --no-options --homedir "$gpg_home" --batch --no-auto-key-retrieve
    --no-default-keyring --keyring "$key" --lock-never --trust-model always)
gpg_command=("${gpg_base[@]}" --status-fd 1 --verify)
if [[ -z "$source" ]]; then
    command -v curl >/dev/null 2>&1 || die 'Install curl, then run this command again'
fi

download() {
    local name=$1 target=$2 maximum=$3
    py partial "$target" "$maximum" || return
    if [[ -n "$source" ]]; then
        py local-copy "$source" "$name" "$target" "$maximum"
    else
        curl --fail --location --retry 0 -C - --max-filesize "$maximum" --output "$target" "$GITHUB_RELEASE_BASE/$release/$name"
    fi
}

meta=$(mktemp -d "$cache/.metadata.XXXXXXXX")
py path "$meta" dir >/dev/null
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
if ! command -v gpg >/dev/null 2>&1; then
    # Retain only inert metadata so the printed verification command is usable.
    # It will be refreshed and authenticated on every subsequent invocation.
    for name in SHA256SUMS SHA256SUMS.sig ASSEMBLY.json; do py refresh "$meta/$name" "$cache/$name"; done
    printf '%s\n' 'GPG is required. Metadata is saved; install GnuPG, then verify with this exact command:' >&2
    printf 'GNUPGHOME=%q ' "$gpg_home" >&2
    printf '%q ' "${gpg_command[@]}" "$cache/SHA256SUMS.sig" "$cache/SHA256SUMS" >&2
    printf '\n' >&2
    exit 1
fi
py path "$key" file >/dev/null
GNUPGHOME="$gpg_home" "${gpg_base[@]}" --with-colons --show-keys --fingerprint "$key" >"$meta/key-status" 2>"$meta/gpg-diagnostics" || {
    cat -- "$meta/gpg-diagnostics" >&2
    die 'The supplied public key could not be inspected'
}
GNUPGHOME="$gpg_home" "${gpg_command[@]}" "$meta/SHA256SUMS.sig" "$meta/SHA256SUMS" >"$meta/status" 2>>"$meta/gpg-diagnostics" || {
    cat -- "$meta/gpg-diagnostics" >&2
    die 'The release signature failed'
}
py validsig "$meta/status" "$meta/key-status" || {
    cat -- "$meta/gpg-diagnostics" >&2
    exit 1
}
selection_status=0
py selection "$meta/ASSEMBLY.json" "$meta/SHA256SUMS" "$disc" "$release" >"$meta/selection" || selection_status=$?
if (( selection_status != 0 )); then
    if (( selection_status != 3 )); then cat -- "$meta/gpg-diagnostics" >&2; fi
    exit "$selection_status"
fi
printf 'Reading release %s\n' "$release"
printf '%s\n' 'Verifying release signature'
cat -- "$meta/transfer-diagnostics" >&2
cat -- "$meta/gpg-diagnostics" >&2
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

if [[ $install == 1 ]]; then
    [[ "$(id -u)" == 0 ]] || die 'Run this command as root to mount and install disc3'
    command -v mount >/dev/null 2>&1 || die 'The mount command is required for --install'
    command -v umount >/dev/null 2>&1 || die 'The umount command is required for --install'
    mount_dir=$(mktemp -d "$cache/mount.XXXXXXXX")
    py path "$mount_dir" dir >/dev/null
    printf '%s\n' 'Mounting disc3 read-only'
    mount -o loop,ro -- "$destination" "$mount_dir"
    mounted=1
    py path "$mount_dir/forms/load-docker.sh" file >/dev/null
    py path "$mount_dir/deep32/payload" dir >/dev/null
    printf '%s\n' 'Running the disc3 Docker form'
    bash -- "$mount_dir/forms/load-docker.sh" "$mount_dir/deep32/payload"
    umount -- "$mount_dir"
    mounted=0
    rmdir -- "$mount_dir"
    mount_dir=''
    printf '%s\n' 'Disc3 Docker form completed'
else
    case "$disc" in
        disc1) printf '%s\n' 'Write the verified disc1 image to a USB stick to start Workstation.' ;;
        disc2) printf '%s\n' 'Attach the verified disc2 content image in Workstation and import its content.' ;;
        disc3) printf '%s\n' 'Run this command again with --install as root to install the disc3 Docker form.' ;;
    esac
fi
