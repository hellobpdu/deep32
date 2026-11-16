#!/usr/bin/env bash
# Join a Deep32 parts manifest after authenticating it with verify-release.sh.
set -euo pipefail
export LC_ALL=C

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
positive_size() {
    [[ $1 =~ ^[1-9][0-9]*$ && ${#1} -le 13 ]] && (( $1 <= 1898100000000 ))
}
hash_file() {
    local digest
    digest=$(sha256sum < "$1") || die "Cannot hash: $1"
    printf '%s' "${digest%% *}"
}
file_size() { stat -c %s -- "$1"; }
check_text() {
    local bytes cleaned
    bytes=$(file_size "$1") || die 'Cannot read manifest size.'
    (( bytes > 0 && bytes <= 1048576 )) || die 'A parts manifest must contain at most 1 MiB.'
    cleaned=$(tr -d '\000-\010\013-\037\177-\377' < "$1" | wc -c | tr -d '[:space:]')
    [[ $cleaned == "$bytes" ]] || die 'The manifest must contain ASCII text with LF line endings and no NUL bytes.'
}

[[ $# -ge 1 && $# -le 2 ]] || die 'Usage: bash join_and_verify.sh MANIFEST [OUTPUT_DIRECTORY]'
for command_name in sha256sum stat dirname basename tail od tr wc mktemp cat ln rm; do
    command -v "$command_name" >/dev/null 2>&1 || die "Required command is unavailable: $command_name"
done
manifest=$1
safe_path "$manifest"
[[ -f $manifest && ! -L $manifest ]] || die 'The manifest must be a regular file.'
manifest_directory=$(cd -P -- "$(dirname -- "$manifest")" && pwd)
manifest_name=$(basename -- "$manifest")
safe_name "$manifest_name" || die 'The manifest name is invalid.'
manifest=$manifest_directory/$manifest_name
check_text "$manifest"
output_directory=${2:-$manifest_directory}
safe_path "$output_directory"
[[ -d $output_directory && ! -L $output_directory ]] || die 'The output directory must already exist and must not be a link.'
output_directory=$(cd -P -- "$output_directory" && pwd)
[[ $(tail -c 1 -- "$manifest" | od -An -tu1 | tr -d '[:space:]') == 10 ]] || die 'The manifest must end with LF.'

declare -a part_names=() part_sizes=() part_hashes=()
line_number=0
image_name=''
image_bytes=0
image_hash=''
total_bytes=0
first_part_bytes=0
previous_part_bytes=0
while IFS= read -r line; do
    ((line_number += 1))
    case $line_number in
        1) [[ $line == deep32-parts-v1 ]] || die 'Unsupported parts manifest.' ;;
        2)
            [[ $line =~ ^file$'\t'([^$'\t']+)$'\t'([1-9][0-9]*)$'\t'([0-9a-f]{64})$ ]] || die 'Invalid file record.'
            image_name=${BASH_REMATCH[1]}
            image_bytes=${BASH_REMATCH[2]}
            image_hash=${BASH_REMATCH[3]}
            safe_name "$image_name" || die 'Invalid joined image name.'
            positive_size "$image_bytes" || die 'Invalid joined image size.'
            [[ $manifest_name == "$image_name.parts.tsv" ]] || die 'The manifest name does not match its image.'
            ;;
        *)
            [[ $line =~ ^part$'\t'([^$'\t']+)$'\t'([1-9][0-9]*)$'\t'([0-9a-f]{64})$ ]] || die "Invalid part record at line $line_number."
            part_name=${BASH_REMATCH[1]}
            part_bytes=${BASH_REMATCH[2]}
            part_hash=${BASH_REMATCH[3]}
            safe_name "$part_name" || die 'Invalid part name.'
            positive_size "$part_bytes" || die 'Invalid part size.'
            (( part_bytes <= 1900000000 )) || die 'A part exceeds the supported size cap.'
            part_number=$((${#part_names[@]} + 1))
            (( part_number <= 999 )) || die 'A manifest may contain at most 999 parts.'
            printf -v expected_part '%s.part%03d' "$image_name" "$part_number"
            [[ $part_name == "$expected_part" ]] || die 'Parts must be ordered and contiguous from 001.'
            if (( part_number == 1 )); then first_part_bytes=$part_bytes
            else
                (( previous_part_bytes == first_part_bytes )) || die 'Only the last part may be shorter.'
                (( part_bytes <= first_part_bytes )) || die 'The last part cannot exceed the first part size.'
            fi
            part_names+=("$part_name")
            part_sizes+=("$part_bytes")
            part_hashes+=("$part_hash")
            previous_part_bytes=$part_bytes
            ((total_bytes += part_bytes))
            ;;
    esac
done < "$manifest"
(( line_number >= 3 && ${#part_names[@]} >= 1 && total_bytes == image_bytes )) || die 'The part sizes do not match the joined image size.'
output=$output_directory/$image_name
[[ ! -e $output && ! -L $output ]] || die 'The joined output already exists.'

# An exclusive temporary file is removed on every error. The hard-link publish
# fails if another process creates the output while verification is running.
temporary=''
cleanup() { [[ -z $temporary ]] || rm -f -- "$temporary"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
temporary=$(mktemp "$output_directory/.deep32-join.XXXXXXXXXX") || die 'Cannot create a temporary output.'
for index in "${!part_names[@]}"; do
    part=$manifest_directory/${part_names[$index]}
    safe_path "$part"
    [[ -f $part && ! -L $part ]] || die "A part is missing or is not a regular file: ${part_names[$index]}"
    [[ $(file_size "$part") == "${part_sizes[$index]}" ]] || die "Part size mismatch: ${part_names[$index]}"
    [[ $(hash_file "$part") == "${part_hashes[$index]}" ]] || die "Part checksum mismatch: ${part_names[$index]}"
    cat -- "$part" >> "$temporary" || die "Cannot read part: ${part_names[$index]}"
done
[[ $(file_size "$temporary") == "$image_bytes" ]] || die 'Joined image size mismatch.'
[[ $(hash_file "$temporary") == "$image_hash" ]] || die 'Joined image checksum mismatch.'
ln -- "$temporary" "$output" || die 'Cannot publish the output exclusively; the destination must support hard links.'
rm -f -- "$temporary"
temporary=''
printf 'Joined and verified: %s\n' "$output"
