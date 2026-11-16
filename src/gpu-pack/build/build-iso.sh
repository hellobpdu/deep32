#!/usr/bin/env bash
# What: remaster the pinned Ubuntu ISO, preserving BIOS and UEFI boot equipment.
# Usage: build-iso.sh --stage STAGE --iso ISO --rootfs CLEAN_TAR --out NEW_DIR [--dry-run]
# Exit codes: 0 success/dry run; 2 invalid input or tools; other command failure.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
parse_build "$@"
if $dry_run; then
  plan_common
  cat <<'PLAN'
Verify ISO SHA-256 against the local authenticated SHA256SUMS pin.
xorriso extracts boot/grub/grub.cfg and loopback.cfg from the input ISO.
Add autoinstall and a secret-free early-settings selector to each installer entry.
No machine-specific settings are embedded in the release ISO.
Map sorted kit and payload files; preserve input boot equipment using boot_image any replay.
Add thin forms under deep32/forms, payload once under deep32/payload and PAYLOAD.SHA256.
Use -iso_level 3 and -file_size_limit off for payload files above 4 GiB.
Set all ISO file dates, volume dates and ownership from SOURCE_DATE_EPOCH.
Update the media md5sum inventory for replaced files and additions.
Write forge.iso, SHA256SUMS and provenance.json; repeat in a fresh output directory and compare.
PLAN
  exit 0
fi
# A pack stage deliberately defers the kernel and driver pins required to boot.
python3 -c 'import json,pathlib,sys; config=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="ascii")); sys.exit(0 if config.get("release_profile", "iso") == "iso" else "build-iso requires release_profile iso")' "$stage_dir/pins.json" || exit 2
[[ -f "$iso_file" ]] || { echo '--iso is required' >&2; exit 2; }
[[ -f "$rootfs_file" ]] || { echo '--rootfs is required for the thin WSL form' >&2; exit 2; }
command -v xorriso >/dev/null || exit 2
preflight xorriso
python3 "$kit_dir/build/media.py" check-iso "$iso_file" "$stage_dir/pins.json"
copy_payload "$out_dir/deep32"
forms_out=$out_dir/deep32/forms
mkdir -- "$forms_out"
bash "$kit_dir/build/build-wsl.sh" --stage "$stage_dir" --rootfs "$rootfs_file" --out "$out_dir/form-wsl"
bash "$kit_dir/build/build-docker.sh" --stage "$stage_dir" --out "$out_dir/form-docker"
cp -- "$out_dir/form-wsl/forge-wsl.tar" "$out_dir/form-docker/forge-docker.tar" "$forms_out/"
python3 "$kit_dir/build/media.py" payload-sums "$out_dir/deep32"
find "$out_dir/deep32" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
mkdir -- "$out_dir/boot"
xorriso -osirrox on -indev "$iso_file" -extract /boot/grub/grub.cfg "$out_dir/boot/grub.cfg"
xorriso -osirrox on -indev "$iso_file" -extract /boot/grub/loopback.cfg "$out_dir/boot/loopback.cfg"
xorriso -osirrox on -indev "$iso_file" -extract /md5sum.txt "$out_dir/boot/md5sum.txt"
python3 "$kit_dir/build/media.py" patch-grub "$out_dir/boot"
python3 "$kit_dir/build/media.py" md5 "$out_dir"
provenance "$out_dir/deep32" "$out_dir/provenance.json"
stamp=$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y%m%d%H%M%S)00
python3 "$kit_dir/build/media.py" maps "$out_dir" > "$out_dir/maps.txt"
xorriso -indev "$iso_file" -outdev "$out_dir/forge.iso" -iso_level 3 -file_size_limit off \
  -boot_image any replay -volume_date all_file_dates "=$SOURCE_DATE_EPOCH" \
  -volume_date c "$stamp" -volume_date m "$stamp" -volume_date uuid "$stamp" \
  -uid 0 -gid 0 -options_from_file "$out_dir/maps.txt" -commit
(cd -- "$out_dir" && sha256sum forge.iso > SHA256SUMS)
