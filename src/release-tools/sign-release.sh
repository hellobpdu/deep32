#!/usr/bin/env bash
# Run this script with the release owner's explicitly supplied signing key ID.
set -euo pipefail
export LC_ALL=C
shopt -s nullglob dotglob

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
safe_path() {
    local path=$1 component prefix=''
    local -a components
    [[ $path != *$'\n'* && $path != *$'\r'* ]] || die 'Paths must not contain line breaks.'
    [[ $path == /* ]] && prefix=/
    IFS=/ read -r -a components <<< "$path"
    for component in "${components[@]}"; do
        [[ -z $component || $component == . ]] && continue
        if [[ -z $prefix ]]; then prefix=$component
        else prefix=${prefix%/}/$component; fi
        [[ ! -L $prefix ]] || die "Links are not allowed: $path"
    done
}

release=''
key_id=''
dry_run=false
legacy_split=false
seen_release=false
seen_key=false
while (( $# )); do
    case $1 in
        --release-dir)
            [[ $seen_release == false && $# -ge 2 && -n $2 ]] || die 'Supply --release-dir exactly once with a directory.'
            release=$2; seen_release=true; shift 2 ;;
        --key-id)
            [[ $seen_key == false && $# -ge 2 && -n $2 && $2 != -* && $2 != *$'\n'* && $2 != *$'\r'* ]] || die 'Supply --key-id exactly once with a key ID.'
            key_id=$2; seen_key=true; shift 2 ;;
        --dry-run)
            [[ $dry_run == false ]] || die 'Supply --dry-run at most once.'
            dry_run=true; shift ;;
        --legacy-split)
            [[ $legacy_split == false ]] || die 'Supply --legacy-split at most once.'
            legacy_split=true; shift ;;
        *) die 'Usage: bash sign-release.sh --release-dir DIR --key-id VALUE [--dry-run] [--legacy-split]' ;;
    esac
done
[[ $seen_release == true && $seen_key == true ]] || die 'Both --release-dir and --key-id are required.'
safe_path "$release"
[[ -d $release && ! -L $release ]] || die 'The release directory must exist and must not be a link.'
release=$(cd -P -- "$release" && pwd)
[[ -f $release/SHA256SUMS && ! -L $release/SHA256SUMS ]] || die 'The root SHA256SUMS file is missing.'
if [[ $legacy_split == false ]]; then
    script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    "${DEEP32_PYTHON:-python3}" -I -B "$script_dir/deep32_get_manifest.py" sign-preflight --release-dir "$release" || die 'Whole-image signing preflight failed.'
elif [[ -e $release/MIRRORS.json || -L $release/MIRRORS.json ]]; then
    die 'Whole-image metadata cannot use legacy signing compatibility.'
fi
declare -a children=() checksums=()
declare -A roles=()
for path in "$release"/*-SHA256SUMS; do
    name=${path##*/}
    [[ ${#name} -le 200 && $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $name != *..* ]] || die 'Invalid per-disc checksum name.'
    [[ -f $path && ! -L $path ]] || die 'Per-disc checksums must be regular files.'
    case $name in
        *-disc1-workstation-*-SHA256SUMS) role=1 ;;
        *-disc2-content-SHA256SUMS) role=2 ;;
        *-disc3-gpu-pack-SHA256SUMS) role=3 ;;
        *) die "Unknown per-disc checksum name: $name" ;;
    esac
    [[ ! ${roles[$role]+present} ]] || die 'There must be exactly one checksum file for each disc.'
    roles[$role]=$path
done
(( ${#roles[@]} == 3 )) || die 'Exactly three per-disc checksum files are required.'
for role in 1 2 3; do children+=("${roles[$role]}"); done
checksums=("${children[@]}" "$release/SHA256SUMS")
for checksum in "${checksums[@]}"; do
    [[ ! -e $checksum.sig && ! -L $checksum.sig ]] || die "A signature already exists: $checksum.sig"
done
printf 'Checksum files to sign:\n'
printf '  %s\n' "${checksums[@]}"
if [[ $dry_run == true ]]; then
    printf 'Dry run complete; no GPG command was run.\n'
    exit 0
fi
command -v gpg >/dev/null 2>&1 || die 'Required command is unavailable: gpg'
for checksum in "${checksums[@]}"; do
    gpg --local-user "$key_id" --detach-sign --armor --output "$checksum.sig" "$checksum" || die "Signing failed: $checksum"
    [[ -f $checksum.sig && ! -L $checksum.sig ]] || die "GPG did not create a regular signature: $checksum.sig"
done
printf 'Created four detached signatures.\n'
