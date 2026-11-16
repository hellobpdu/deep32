#!/usr/bin/env python3
"""Assemble a deterministic data-only GPU pack; no network or bootable inputs."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

from provenance import digest, members, verify_manifest

_spec = importlib.util.spec_from_file_location('pack_thin_forms', Path(__file__).with_name('thin_forms.py'))
_thin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_thin)

VOLUME = 'DEEP32_GPU_1_0'
SECTOR = 2048


def pack_pins(path):
    pins = json.loads(path.read_text(encoding='ascii'))
    if pins.get('release_profile', 'iso') != 'pack':
        raise ValueError('build-pack requires release_profile pack')
    return pins


def regular(path):
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError('input must be a regular file: ' + path.name)


def safe_members(root):
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError('input must be a directory')
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or path.is_junction() or not (path.is_file() or path.is_dir()):
            raise ValueError('unsafe pack input: ' + path.relative_to(root).as_posix())


def copy_file(source, destination):
    regular(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def prepare(stage, kit, docker, wsl, output, epoch):
    """Place the one shared payload beside the unchanged runtime form entry points."""
    if type(epoch) is not int or epoch < 0:
        raise ValueError('SOURCE_DATE_EPOCH must be a non-negative integer')
    dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
    pack_pins(stage / 'pins.json')
    if output.exists() or output.is_symlink():
        raise ValueError('pack media output must be new')
    for source in (stage, kit):
        if output.resolve().is_relative_to(source.resolve()):
            raise ValueError('pack media output overlaps an input')
    safe_members(stage / 'payload')
    verify_manifest(stage / 'payload', stage / 'PAYLOAD.json')
    for archive, limit in ((docker, _thin.LIMIT), (wsl, _thin.WSL_LIMIT)):
        regular(archive)
        if archive.stat().st_size >= limit:
            raise ValueError('thin form exceeds its size limit: ' + archive.name)
    runtime = _thin.runtime_members(kit, stage / 'pins.json')
    readme = kit / 'README.txt'
    regular(readme)
    text = readme.read_bytes()
    if not text.isascii() or b'\r' in text:
        raise ValueError('README.txt must use ASCII and LF')
    output.mkdir(parents=True)
    for name, source in runtime.items():
        copy_file(source, output / name)
    copy_file(readme, output / 'README.txt')
    if (kit / 'PACK.md').exists():
        copy_file(kit / 'PACK.md', output / 'PACK.md')
    shutil.copytree(stage / 'payload', output / 'deep32/payload')
    copy_file(stage / 'PAYLOAD.json', output / 'deep32/PAYLOAD.json')
    rows = json.loads((stage / 'PAYLOAD.json').read_text(encoding='ascii'))
    (output / 'deep32/PAYLOAD.SHA256').write_text(
        ''.join(row['sha256'] + '  ' + row['path'] + '\n' for row in rows),
        encoding='ascii', newline='\n')
    copy_file(docker, output / 'forms/forge-docker.tar')
    copy_file(wsl, output / 'forms/forge-wsl.tar')
    # The manifest covers every disc file except itself, including the payload once.
    rows = members(output)
    (output / 'SHA256SUMS').write_text(
        ''.join(row['sha256'] + '  ' + row['path'] + '\n' for row in rows),
        encoding='ascii', newline='\n')
    for path in sorted(output.rglob('*'), reverse=True) + [output]:
        if 'autoinstall' in path.relative_to(output).parts or path.name.upper() == 'CIDATA':
            raise ValueError('ISO installer member is not allowed in a pack')
        path.chmod(0o755 if path.is_dir() or path.suffix == '.sh' or path.name == 'pairing-helper' else 0o644)
        # Every member was created here from regular files; there are no links.
        os.utime(path, (epoch, epoch))
    return rows


def quoted(value):
    text = str(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError('control character in pack mapping path')
    return "'" + text.replace("'", "'\"'\"'") + "'"


def maps(root, output):
    rows = []
    for path in sorted(root.rglob('*'), key=lambda p: p.relative_to(root).as_posix()):
        if path.is_file() or (path.is_dir() and not any(path.iterdir())):
            rows.append('-map ' + quoted(path.resolve()) + ' ' + quoted('/' + path.relative_to(root).as_posix()) + '\n')
    output.write_text(''.join(rows), encoding='ascii', newline='\n')


def xorriso_command(output, map_file, epoch):
    stamp = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime('%Y%m%d%H%M%S') + '00'
    # Native xorriso syntax (lead fix 2026-11-16): -iso_level exists only in mkisofs emulation, and
    # -file_size_limit takes a list that must end with '--'.
    return ['xorriso', '-outdev', str(output), '-compliance', 'iso_9660_level=3', '-file_size_limit', 'off', '--',
            '-joliet', 'on', '-rockridge', 'on', '-volid', VOLUME,
            '-volume_date', 'all_file_dates', '=' + str(epoch),
            '-volume_date', 'c', stamp, '-volume_date', 'm', stamp,
            '-volume_date', 'uuid', stamp, '-uid', '0', '-gid', '0',
            '-options_from_file', str(map_file), '-commit']


def verify_iso(path):
    """Check the ISO 9660 descriptors directly, including absence of boot records."""
    regular(path)
    with path.open('rb') as stream:
        if stream.read(16 * SECTOR) != b'\0' * (16 * SECTOR):
            raise ValueError('pack ISO contains a system area or is truncated')
        types, primary, joliet = [], False, False
        for _ in range(256):
            block = stream.read(SECTOR)
            if len(block) != SECTOR or block[1:6] != b'CD001' or block[6] != 1:
                raise ValueError('invalid ISO volume descriptor')
            kind = block[0]
            types.append(kind)
            if kind == 0:
                raise ValueError('pack ISO contains a boot record')
            if kind == 1:
                if primary or block[40:72].decode('ascii').rstrip(' ') != VOLUME:
                    raise ValueError('pack ISO has an unexpected volume label')
                primary = True
            elif kind == 2:
                joliet = joliet or block[88:91] in (b'%/@', b'%/C', b'%/E')
            elif kind == 255:
                if not primary or not joliet:
                    raise ValueError('pack ISO is missing its primary or Joliet descriptor')
                return dict(volume=VOLUME, descriptor_types=types, boot_records=0, system_area='zero')
            else:
                raise ValueError('unexpected ISO volume descriptor type')
    raise ValueError('pack ISO has no volume descriptor terminator')


def build(stage, kit, docker, wsl, output, epoch, run=subprocess.run):
    iso = output / 'forge-pack.iso'
    if iso.exists():
        raise ValueError('pack ISO output must be new')
    prepare(stage, kit, docker, wsl, output / 'media', epoch)
    map_file = output / 'maps.txt'
    maps(output / 'media', map_file)
    environment = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), TZ='UTC', LC_ALL='C')
    run(xorriso_command(iso, map_file, epoch), check=True, env=environment)
    report = verify_iso(iso)
    report.update(format='deep32-gpu-pack/1', source_date_epoch=epoch, sha256=digest(iso))
    (output / 'pack-report.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='ascii', newline='\n')
    (output / 'SHA256SUMS').write_text(report['sha256'] + '  forge-pack.iso\n', encoding='ascii', newline='\n')
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest='action', required=True)
    profile = commands.add_parser('profile')
    profile.add_argument('--pins', type=Path, required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('iso', type=Path)
    make = commands.add_parser('build')
    for name in ('stage', 'kit', 'docker', 'wsl', 'out'):
        make.add_argument('--' + name, type=Path, required=True)
    make.add_argument('--epoch', type=int, required=True)
    args = ap.parse_args()
    try:
        if args.action == 'profile':
            pack_pins(args.pins)
        elif args.action == 'verify':
            print(json.dumps(verify_iso(args.iso), sort_keys=True))
        else:
            build(args.stage, args.kit, args.docker, args.wsl, args.out, args.epoch)
    except (OSError, ValueError, KeyError, OverflowError, subprocess.SubprocessError) as exc:
        ap.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
