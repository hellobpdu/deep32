#!/usr/bin/env python3
"""Create a deterministic WSL rootfs without extracting or executing it."""
import argparse
import copy
import io
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import tarfile

from provenance import digest
_spec = importlib.util.spec_from_file_location('rootfs_thin_forms', Path(__file__).with_name('thin_forms.py'))
_thin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_thin)
runtime_members, LIMIT = _thin.runtime_members, _thin.LIMIT


def safe_name(name):
    while name.startswith('./'):
        name = name[2:]
    if name in ('', '.'):
        return ''
    if name.startswith('/') or '..' in PurePosixPath(name).parts or '\\' in name:
        raise ValueError('unsafe tar path')
    return name.rstrip('/')


def build(base, payload, output, epoch, kit=None):
    kit = payload / 'kit' if kit is None else kit
    evidence = json.loads((payload / 'payload/evidence/rootfs.json').read_text(encoding='ascii'))
    if digest(base) != evidence['sha256'] or evidence.get('form') != 'wsl-clean-ubuntu-24.04.5':
        raise ValueError('unverified WSL rootfs')
    pins_path = payload / 'pins.json' if (payload / 'pins.json').is_file() else kit / 'pins.json'
    pins = json.loads(pins_path.read_text(encoding='ascii'))
    with tarfile.open(base, 'r:*') as source:
        originals = {}
        for entry in source.getmembers():
            name = safe_name(entry.name)
            if not name:
                continue
            if name in originals:
                raise ValueError('duplicate rootfs path')
            if not (entry.isfile() or entry.isdir() or entry.issym() or entry.islnk()):
                raise ValueError('special rootfs member')
            if entry.islnk():
                safe_name(entry.linkname)
            if (name.startswith(('home/', 'var/lib/tailscale/', 'var/lib/deep32-forge/')) and entry.isfile() and entry.size) or name.startswith('etc/ssh/ssh_host_'):
                raise ValueError('rootfs holds an installation identity')
            if name in {'etc/machine-id', 'var/lib/dbus/machine-id'} and entry.isfile() and entry.size:
                raise ValueError('rootfs machine-id must be empty')
            originals[name] = entry
        # Stock root dotfiles are harmless only when byte-identical to a stock template:
        # /etc/skel, or the base-files copies Ubuntu installs as /root/.bashrc and
        # /root/.profile (usr/share/base-files/dot.bashrc, dot.profile).
        # Compare after reading the complete index, regardless of tar member order.
        for name, entry in originals.items():
            if not name.startswith('root/') or entry.isdir():
                continue
            if not entry.isfile():
                raise ValueError('rootfs holds an installation identity under root')
            if not entry.size:
                continue
            content = source.extractfile(entry).read()
            templates = ['etc/skel/' + name[5:]]
            if name in {'root/.bashrc', 'root/.profile'}:
                templates.append('usr/share/base-files/dot' + name[5:])
            if not any(originals.get(t) is not None and originals[t].isfile() and
                       source.extractfile(originals[t]).read() == content for t in templates):
                raise ValueError('rootfs holds an installation identity under root')
        status_entry = originals.get('var/lib/dpkg/status')
        if status_entry is None or not status_entry.isfile():
            raise ValueError('no package inventory')
        text = source.extractfile(status_entry).read().decode('utf-8')
        installed = {}
        for stanza in text.split('\n\n'):
            fields = dict(line.split(': ', 1) for line in stanza.splitlines() if ': ' in line and not line.startswith(' '))
            if fields.get('Status') == 'install ok installed':
                installed[fields['Package']] = fields['Version']
        if any(name.startswith(('linux-modules-nvidia', 'nvidia-driver-', 'nvidia-dkms-', 'cuda-drivers')) or name == 'dkms' for name in installed):
            raise ValueError('WSL rootfs must install no Linux NVIDIA driver or DKMS')
        if 'docker.io' in installed or any(name.startswith(('opt/deep32-payload/', 'opt/deep32-forge/')) for name in originals):
            raise ValueError('thin WSL base must contain neither Docker nor the kit/payload')
        if 'python3' not in installed:
            raise ValueError('thin WSL base must provide python3 for offline provisioning')
        package_pins = pins['packages']
        if 'release_profile' in pins:
            profile_spec = importlib.util.spec_from_file_location('rootfs_release_profile', Path(__file__).with_name('release_profile.py'))
            profile = importlib.util.module_from_spec(profile_spec)
            profile_spec.loader.exec_module(profile)
            package_pins = profile.packages(pins)
        for item in package_pins:
            name, version = item.split('=', 1)
            if name in installed and installed[name] != version:
                raise ValueError('WSL package version differs: ' + name)
        additions = {}
        for relative, path in runtime_members(kit, pins_path).items():
            additions['opt/deep32-forge/' + relative] = (path,
                                                       0o755 if path.suffix == '.sh' else 0o644)
        additions['etc/wsl.conf'] = (b'[boot]\nsystemd=true\n[user]\ndefault=root\n', 0o644)
        additions['etc/deep32-forge/form'] = (b'wsl\n', 0o644)
        additions['etc/systemd/system/deep32-forge-firstboot.service'] = ((kit / 'firstboot/deep32-forge-firstboot.service').read_bytes(), 0o644)
        unit_link = 'etc/systemd/system/multi-user.target.wants/deep32-forge-firstboot.service'
        mask_link = 'etc/systemd/system/tailscaled.service'
        host_daemon_link = 'etc/systemd/system/multi-user.target.wants/tailscaled.service'
        # Overlay parents must be directories; do not write through rootfs symlinks.
        for name in (*additions, mask_link):
            for parent in PurePosixPath(name).parents:
                if str(parent) != '.' and str(parent) in originals and not originals[str(parent)].isdir():
                    raise ValueError('overlay parent is not a directory')
        with tarfile.open(output, 'w', format=tarfile.GNU_FORMAT) as target:
            for name in sorted((set(originals) | set(additions) | {mask_link}) - {'etc/machine-id', 'var/lib/dbus/machine-id', host_daemon_link, unit_link}):
                if name == unit_link:
                    entry = tarfile.TarInfo(name)
                    entry.type, entry.linkname, entry.mode = tarfile.SYMTYPE, '../deep32-forge-firstboot.service', 0o777
                    data = None
                elif name == mask_link:
                    entry = tarfile.TarInfo(name)
                    entry.type, entry.linkname, entry.mode = tarfile.SYMTYPE, '/dev/null', 0o777
                    data = None
                elif name in additions:
                    content, mode = additions[name]
                    entry = tarfile.TarInfo(name)
                    entry.mode = mode
                    if isinstance(content, Path):
                        entry.size = content.stat().st_size
                        data = content.open('rb')
                    else:
                        entry.size = len(content)
                        data = io.BytesIO(content)
                else:
                    original = originals[name]
                    entry = copy.copy(original)
                    entry.name = name
                    data = source.extractfile(original) if original.isfile() else None
                entry.mtime = epoch
                entry.uname = entry.gname = ''
                entry.pax_headers = {}
                try:
                    target.addfile(entry, data)
                finally:
                    if data is not None:
                        data.close()
    if output.stat().st_size >= LIMIT:
        output.unlink()
        raise ValueError('thin WSL form exceeds the 1 GiB limit')
    _thin.write_size_report(output)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--payload', type=Path, required=True)
    ap.add_argument('--kit', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.dry_run:
        print('Would verify pinned clean Ubuntu base and add runtime kit only; no payload, Docker package or Linux driver; refuse a form at least 1 GiB. Downloads: none')
        return
    try:
        if args.output.exists():
            raise ValueError('output already exists')
        build(args.base, args.payload, args.output, int(os.environ['SOURCE_DATE_EPOCH']), args.kit)
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        ap.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
