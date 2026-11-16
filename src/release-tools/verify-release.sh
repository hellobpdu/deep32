#!/usr/bin/env bash
# Authenticate a release with a caller-supplied primary OpenPGP fingerprint.
# Python 3.10+ and the sibling helper are required for authenticated ASSEMBLY.json.
set -euo pipefail
export LC_ALL=C
shopt -s nullglob dotglob

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
safe_name() {
    [[ ${#1} -le 200 && $1 =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $1 != *..* ]]
}
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
hash_file() {
    local digest
    digest=$(sha256sum < "$1") || die "Cannot hash: $1"
    printf '%s' "${digest%% *}"
}
require_lf() {
    local bytes cleaned
    bytes=$(stat -c %s -- "$1") || die 'Cannot read text file size.'
    (( bytes > 0 && bytes <= 1048576 )) || die 'A checksum or manifest file must contain at most 1 MiB.'
    cleaned=$(tr -d '\000-\010\013-\037\177-\377' < "$1" | wc -c | tr -d '[:space:]')
    [[ $cleaned == "$bytes" ]] || die "A text file contains a forbidden byte: $(basename -- "$1")"
    [[ $(tail -c 1 -- "$1" | od -An -tu1 | tr -d '[:space:]') == 10 ]] || die "A text file must end with LF: $(basename -- "$1")"
}
positive_size() {
    [[ $1 =~ ^[1-9][0-9]*$ && ${#1} -le 13 ]] && (( $1 <= 1898100000000 ))
}
verify_signature() {
    local checksum=$1 status line primary valid_count=0
    local -a fields
    [[ -f $checksum.sig && ! -L $checksum.sig ]] || die "Missing regular signature: $(basename -- "$checksum").sig"
    status=$(gpg --batch --no-auto-key-retrieve --status-fd 1 --verify "$checksum.sig" "$checksum") || die "Signature verification failed: $(basename -- "$checksum")"
    while IFS= read -r line; do
        # Native Windows GPG emits CRLF even when invoked from Git Bash.
        line=${line%$'\r'}
        [[ $line == '[GNUPG:] VALIDSIG '* ]] || continue
        read -r -a fields <<< "${line#'[GNUPG:] VALIDSIG '}"
        [[ ${#fields[@]} -eq 9 || ${#fields[@]} -eq 10 ]] || die 'Malformed GPG VALIDSIG status.'
        [[ ${fields[0]} =~ ^([[:xdigit:]]{40}|[[:xdigit:]]{64})$ ]] || die 'Malformed signing fingerprint in GPG status.'
        primary=${fields[9]:-${fields[0]}}
        [[ $primary =~ ^([[:xdigit:]]{40}|[[:xdigit:]]{64})$ ]] || die 'Malformed primary fingerprint in GPG status.'
        [[ ${primary^^} == "$fingerprint" ]] || die 'The signature primary fingerprint does not match the expected fingerprint.'
        ((valid_count += 1))
    done <<< "$status"
    (( valid_count == 1 )) || die 'GPG must report exactly one valid signature.'
}

# Both root and child sums use the same strict flat-name format. A child may
# name only files also authenticated by the root checksum file.
declare -A root_hashes=() child_members=()
read_checksums() {
    local checksum=$1 mode=$2 line name digest prefix=${3:-} count=0
    local -A seen=()
    [[ -f $checksum && ! -L $checksum ]] || die 'A checksum file is missing or is not a regular file.'
    require_lf "$checksum"
    while IFS= read -r line; do
        [[ $line =~ ^([0-9a-f]{64})\ \ ([A-Za-z0-9][A-Za-z0-9._-]*)$ ]] || die "Malformed checksum line in $(basename -- "$checksum")."
        digest=${BASH_REMATCH[1]}
        name=${BASH_REMATCH[2]}
        safe_name "$name" || die 'Invalid checksum filename.'
        [[ ! ${seen[$name]+present} ]] || die "Duplicate checksum entry: $name"
        seen[$name]=1
        [[ -f $release/$name && ! -L $release/$name ]] || die "Missing regular release file: $name"
        if [[ $mode == root ]]; then
            [[ $name != SHA256SUMS && $name != *.sig ]] || die 'Root checksums may not include themselves or signatures.'
            root_hashes[$name]=$digest
        else
            [[ ${root_hashes[$name]+present} && ${root_hashes[$name]} == "$digest" ]] || die "Child checksum is absent from or differs from root: $name"
            [[ $name == "$prefix-README.md" || $name == "$prefix.img" || $name == "$prefix.iso" || $name == "$prefix.img.parts.tsv" || $name == "$prefix.iso.parts.tsv" || $name == "$prefix.img.part"[0-9][0-9][0-9] || $name == "$prefix.iso.part"[0-9][0-9][0-9] ]] || die "Unexpected file in per-disc checksum: $name"
            [[ ! ${child_members[$name]+present} ]] || die "A file is covered by more than one disc: $name"
            child_members[$name]=1
        fi
        [[ $(hash_file "$release/$name") == "$digest" ]] || die "Checksum mismatch: $name"
        ((count += 1))
    done < "$checksum"
    (( count > 0 )) || die 'A checksum file is empty.'
}

declare -A joined_images=() manifest_parts=()
check_manifest() {
    local manifest=$1 name=${1##*/} line number=0 image='' bytes=0 whole_hash='' total=0 first=0 previous=0 part_count=0
    local part size digest expected_part
    require_lf "$manifest"
    while IFS= read -r line; do
        ((number += 1))
        case $number in
            1) [[ $line == deep32-parts-v1 ]] || die 'Unsupported parts manifest.' ;;
            2)
                [[ $line =~ ^file$'\t'([^$'\t']+)$'\t'([1-9][0-9]*)$'\t'([0-9a-f]{64})$ ]] || die 'Invalid file record in a parts manifest.'
                image=${BASH_REMATCH[1]}; bytes=${BASH_REMATCH[2]}; whole_hash=${BASH_REMATCH[3]}
                safe_name "$image" && positive_size "$bytes" || die 'Invalid joined image name or size.'
                [[ $name == "$image.parts.tsv" ]] || die 'A parts manifest name does not match its image.'
                [[ ! ${root_hashes[$image]+present} && ! ${joined_images[$image]+present} ]] || die 'A joined image collides with a release entry.'
                ;;
            *)
                [[ $line =~ ^part$'\t'([^$'\t']+)$'\t'([1-9][0-9]*)$'\t'([0-9a-f]{64})$ ]] || die 'Invalid part record in a parts manifest.'
                part=${BASH_REMATCH[1]}; size=${BASH_REMATCH[2]}; digest=${BASH_REMATCH[3]}
                safe_name "$part" && positive_size "$size" || die 'Invalid part name or size.'
                (( size <= 1900000000 )) || die 'A part exceeds the supported size cap.'
                ((part_count += 1))
                (( part_count <= 999 )) || die 'A manifest may contain at most 999 parts.'
                printf -v expected_part '%s.part%03d' "$image" "$part_count"
                [[ $part == "$expected_part" ]] || die 'Parts must be ordered and contiguous from 001.'
                if (( part_count == 1 )); then first=$size
                else
                    (( previous == first && size <= first )) || die 'Part sizes are inconsistent.'
                fi
                [[ ${root_hashes[$part]+present} && ${root_hashes[$part]} == "$digest" ]] || die "Part checksum differs from root: $part"
                [[ $(stat -c %s -- "$release/$part") == "$size" ]] || die "Part size mismatch: $part"
                [[ ! ${manifest_parts[$part]+present} ]] || die 'A part belongs to multiple manifests.'
                manifest_parts[$part]=1
                previous=$size
                ((total += size))
                ;;
        esac
    done < "$manifest"
    (( number >= 3 && total == bytes )) || die 'A parts manifest has inconsistent total size.'
    joined_images[$image]=1
    if [[ -e $release/$image || -L $release/$image ]]; then
        [[ -f $release/$image && ! -L $release/$image ]] || die "A joined image is not a regular file: $image"
        [[ $(stat -c %s -- "$release/$image") == "$bytes" && $(hash_file "$release/$image") == "$whole_hash" ]] || die "Joined image verification failed: $image"
    fi
}

[[ $# -eq 2 ]] || die 'Usage: bash verify-release.sh RELEASE_DIRECTORY EXPECTED_PRIMARY_FINGERPRINT'
for command_name in gpg sha256sum stat basename tail od tr wc; do
    command -v "$command_name" >/dev/null 2>&1 || die "Required command is unavailable: $command_name"
done
release=$1
fingerprint=${2^^}
[[ $fingerprint =~ ^([0-9A-F]{40}|[0-9A-F]{64})$ ]] || die 'Supply a primary fingerprint of 40 or 64 hexadecimal digits.'
safe_path "$release"
[[ -d $release && ! -L $release ]] || die 'The release directory must exist and must not be a link.'
release=$(cd -P -- "$release" && pwd)
[[ -f $release/SHA256SUMS && ! -L $release/SHA256SUMS ]] || die 'The root SHA256SUMS file is missing.'
# Authenticate this file before treating any of its contents as release data.
verify_signature "$release/SHA256SUMS"
read_checksums "$release/SHA256SUMS" root

# Pending metadata is parsed only after its bytes have been authenticated.
# Legacy complete releases without ASSEMBLY.json retain the three-disc path.
declare -A pending_roles=()
pending_count=0
if [[ ${root_hashes[ASSEMBLY.json]+present} ]]; then
    script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    helper="$script_dir/deep32_get_manifest.py"
    safe_path "$helper"
    [[ -f $helper && ! -L $helper ]] || die 'The sibling manifest helper is missing or is not a regular file.'
    command -v python3 >/dev/null 2>&1 || die 'Python 3.10 or later is required for ASSEMBLY.json.'
    python3 -I -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die 'Python 3.10 or later is required for ASSEMBLY.json.'
    pending_info=$(python3 -I -B "$helper" pending "$release/ASSEMBLY.json" "$release/SHA256SUMS")
    if [[ -n $pending_info ]]; then
        while IFS= read -r line; do
            [[ $line == *$'\t'* ]] || die 'Invalid pending metadata helper output.'
            pending_role=${line%%$'\t'*}
            pending_prefix=${line#*$'\t'}
            case $pending_role in
                disc2) pending_number=2 ;;
                disc3) pending_number=3 ;;
                *) die 'Invalid pending role in helper output.' ;;
            esac
            [[ ! ${pending_roles[$pending_number]+present} ]] || die 'Duplicate pending role in helper output.'
            safe_name "$pending_prefix" || die 'Invalid pending asset prefix.'
            pending_roles[$pending_number]=$pending_prefix
            ((pending_count += 1))
        done <<< "$pending_info"
    fi
fi

declare -a children=()
declare -A disc_roles=() signature_names=([SHA256SUMS.sig]=1)
for name in "${!root_hashes[@]}"; do
    [[ $name == *-SHA256SUMS ]] || continue
    case $name in
        *-disc1-workstation-*-SHA256SUMS) role=1 ;;
        *-disc2-content-SHA256SUMS) role=2 ;;
        *-disc3-gpu-pack-SHA256SUMS) role=3 ;;
        *) die "Unknown per-disc checksum name: $name" ;;
    esac
    [[ ! ${disc_roles[$role]+present} ]] || die 'There must be exactly one checksum file for each disc.'
    disc_roles[$role]=1
    children+=("$name")
    signature_names[$name.sig]=1
done
(( ${#children[@]} == 3 - pending_count )) || die 'The root must contain one checksum file for every present disc.'
for role in 1 2 3; do
    if [[ ${pending_roles[$role]+present} ]]; then
        [[ ! ${disc_roles[$role]+present} ]] || die 'A pending disc must not have a per-disc checksum file.'
    else
        [[ ${disc_roles[$role]+present} ]] || die 'A present disc is missing its per-disc checksum file.'
    fi
done
for name in "${children[@]}"; do
    verify_signature "$release/$name"
    prefix=${name%-SHA256SUMS}
    read_checksums "$release/$name" child "$prefix"
    for member in "${!root_hashes[@]}"; do
        if [[ $member == "$prefix."* || $member == "$prefix-"* ]]; then
            [[ $member == "$name" || ${child_members[$member]+present} ]] || die "A disc file is absent from its per-disc checksums: $member"
        fi
    done
    if [[ $name == *-disc2-content-SHA256SUMS || $name == *-disc3-gpu-pack-SHA256SUMS ]]; then
        [[ ! ${child_members[$prefix.img]+present} && ! ${child_members[$prefix.img.parts.tsv]+present} ]] || die 'Disc 2 and disc 3 images must use the .iso suffix.'
        for member in "${!child_members[@]}"; do
            [[ $member != "$prefix.img.part"[0-9][0-9][0-9] ]] || die 'Disc 2 and disc 3 parts must use the .iso suffix.'
        done
    fi
    [[ ${child_members[$prefix-README.md]+present} ]] || die 'A per-disc README is missing from its checksums.'
    image_count=0
    for suffix in .img .iso .img.parts.tsv .iso.parts.tsv; do
        [[ ! ${child_members[$prefix$suffix]+present} ]] || ((image_count += 1))
    done
    (( image_count == 1 )) || die 'Each disc must declare exactly one image or parts manifest.'
done
for name in "${!root_hashes[@]}"; do
    if [[ $name == *.parts.tsv ]]; then
        [[ ${child_members[$name]+present} ]] || die 'A parts manifest must belong to a disc.'
        check_manifest "$release/$name"
    fi
done
for name in "${!root_hashes[@]}"; do
    if [[ $name == *.part[0-9][0-9][0-9] ]]; then
        [[ ${manifest_parts[$name]+present} && ${child_members[$name]+present} ]] || die "A part is absent from its manifest or disc checksums: $name"
    fi
done
for path in "$release"/*; do
    name=${path##*/}
    safe_name "$name" || die "Invalid release filename: $name"
    [[ -f $path && ! -L $path ]] || die "Release entries must be regular files: $name"
    if [[ $name == SHA256SUMS ]]; then continue
    elif [[ $name == *.sig ]]; then
        [[ ${signature_names[$name]+present} ]] || die "Unexpected signature: $name"
    elif [[ ${joined_images[$name]+present} ]]; then continue
    else
        [[ ${root_hashes[$name]+present} ]] || die "A release file is absent from root checksums: $name"
    fi
done
printf 'Release signatures and checksums verified.\n'
for role in 2 3; do
    if [[ ${pending_roles[$role]+present} ]]; then
        printf 'disc%s: pending (not in this release yet)\n' "$role"
    fi
done
