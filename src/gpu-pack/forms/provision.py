#!/usr/bin/env python3
"""Provision a thin form from mounted media. No resolver or network fetch."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

CHUNK = 1024 * 1024
WSL_DAEMON = {'bridge': 'deep32br0', 'iptables': False, 'ip6tables': False,
              'default-address-pools': [{'base': '10.213.0.0/17', 'size': 24}],
              'runtimes': {'nvidia': {'path': 'nvidia-container-runtime', 'runtimeArgs': []}}}
WSL_BRIDGE_SCRIPT = '''#!/bin/sh
set -eu
if ! ip link show dev deep32br0 >/dev/null 2>&1; then
  ip link add name deep32br0 type bridge
fi
if ! ip -4 addr show dev deep32br0 | grep -q 'inet '; then
  ip addr add 10.213.255.1/24 dev deep32br0
fi
ip link set dev deep32br0 up
'''
WSL_BRIDGE_DROPIN = '[Service]\nExecStartPre=/usr/local/sbin/deep32-bridge\n'


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            value.update(block)
    return value.hexdigest()


def verified_payload(payload):
    """Check the disc's exact JSON inventory and SHA256 list before any mutation."""
    manifest = payload.parent / 'PAYLOAD.json'
    sums = payload.parent / 'PAYLOAD.SHA256'
    if any(p.is_symlink() or not p.is_file() for p in (manifest, sums)):
        raise ValueError('payload inventories must be regular files')
    rows = json.loads(manifest.read_text(encoding='ascii'))
    expected = {}
    for row in rows:
        name = row['path']
        if (not isinstance(name, str) or not name or '\\' in name or '\n' in name or '\r' in name
            or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
            or str(PurePosixPath(name)) != name or name in expected
            or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
            or type(row['size']) is not int or row['size'] < 0):
            raise ValueError('invalid payload inventory path: ' + str(name))
        expected[name] = row
    if not expected or list(expected) != sorted(expected):
        raise ValueError('empty or unsorted payload inventory')
    listed = ''.join(row['sha256'] + '  ' + name + '\n' for name, row in expected.items())
    if sums.read_text(encoding='ascii') != listed:
        raise ValueError('PAYLOAD.SHA256 differs from PAYLOAD.json')
    actual = set()
    for path in sorted(payload.rglob('*')):
        name = path.relative_to(payload).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('unsafe payload member: ' + name)
        if path.is_file():
            row = expected.get(name)
            if row is None or path.stat().st_size != row['size'] or digest(path) != row['sha256']:
                raise ValueError('payload hash mismatch: ' + name)
            actual.add(name)
    missing = sorted(set(expected) - actual)
    if missing:
        raise ValueError('missing payload file: ' + missing[0])
    return digest(manifest) + ':' + digest(sums)


def safe_destination(path):
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('unsafe destination link: ' + str(path))
    if path.is_file() and path.stat().st_nlink > 1:
        raise ValueError('unsafe destination hard link: ' + str(path))


def copy_kit(source, target):
    safe_destination(target)
    if source.resolve() == target.resolve():
        return
    profile = json.loads((source / 'pins.json').read_text(encoding='ascii')).get('release_profile', 'iso')
    if profile == 'pack':
        # A mounted pack also contains the shared payload and form archives;
        # copy only installed runtime code into the host kit.
        files = [source / 'pins.json']
        for name in ('firstboot', 'import'):
            files.extend(path for path in (source / name).rglob('*') if path.is_file())
        files.extend(path for path in (source / 'containers').glob('*') if path.is_file())
        files.extend(path for path in (source / 'forms').rglob('*')
                     if path.is_file() and path.suffix in ('.py', '.sh'))
        files.extend(source / name for name in ('build/provenance.py', 'build/release_profile.py', 'build/image_binding.py')
                     if (source / name).is_file())
        loader = source / 'firstboot/installed-inputs.py'
        if (loader.is_file() and 'image_binding' in loader.read_text(encoding='ascii')
                and not (source / 'build/image_binding.py').is_file()):
            raise ValueError('missing runtime image binding helper')
        members = {path.relative_to(source): path for path in files}
        directories = {parent for name in members for parent in name.parents}
        if target.exists():
            for path in target.rglob('*'):
                safe_destination(path)
                name = path.relative_to(target)
                if path.is_dir():
                    if name not in directories:
                        raise ValueError('refusing an existing host kit: ' + path.name)
                elif name not in members or digest(path) != digest(members[name]):
                    raise ValueError('existing host kit hash differs: ' + path.name)
        for name, original in sorted(members.items()):
            if original.is_symlink():
                raise ValueError('unsafe runtime kit link: ' + original.name)
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
        return
    # A partial copy can resume only when every existing byte belongs to this kit.
    if target.exists():
        for path in target.rglob('*'):
            safe_destination(path)
            original = source / path.relative_to(target)
            if path.is_symlink() or not original.exists() or path.is_dir() != original.is_dir():
                raise ValueError('refusing an existing host kit: ' + path.name)
            if path.is_file() and digest(path) != digest(original):
                raise ValueError('existing host kit hash differs: ' + path.name)
    shutil.copytree(source, target, dirs_exist_ok=True)


def provision(form, payload, kit, root=Path('/'), run=subprocess.run, host_check=None):
    if form not in ('wsl', 'docker'):
        raise ValueError('unknown thin form')
    # A matching success marker makes a repeat a read-only operation.
    marker = root / 'etc/deep32-forge/provisioned.json'
    for path in (marker, root / 'opt/deep32-payload', root / 'opt/deep32-forge',
                 root / 'opt/PAYLOAD.json', root / 'etc/docker/daemon.json',
                 root / 'etc/deep32-forge/form', root / 'etc/systemd/system/deep32-forge-firstboot.service'):
        safe_destination(path)
    if form == 'wsl':
        for path in (root / 'usr/local/sbin/deep32-bridge',
                     root / 'etc/systemd/system/docker.service.d/deep32-bridge.conf'):
            safe_destination(path)
    identity = verified_payload(payload)
    expected = {'form': form, 'payload': identity, 'pins': digest(kit / 'pins.json')}
    if marker.exists():
        if json.loads(marker.read_text(encoding='ascii')) != expected:
            raise ValueError('another payload or form is already provisioned')
        return False
    # The command-line entry supplies the read-only host preflight here, after
    # byte verification and the replay guard, before the first destination write.
    if host_check is not None:
        host_check()
    unit = root / 'etc/systemd/system/deep32-forge-firstboot.service'
    source_unit = kit / 'firstboot/deep32-forge-firstboot.service'
    if unit.exists() and (unit.is_symlink() or digest(unit) != digest(source_unit)):
        raise ValueError('refusing an existing firstboot service')
    target_kit = root / 'opt/deep32-forge'
    target_payload = root / 'opt/deep32-payload'
    if target_payload.exists():
        # Only resume the same byte-verified input after an interrupted provision.
        manifest = root / 'opt/PAYLOAD.json'
        if not manifest.is_file() or digest(manifest) != digest(payload.parent / 'PAYLOAD.json'):
            raise ValueError('existing payload belongs to another installation')
        for path in target_payload.rglob('*'):
            safe_destination(path)
            original = payload / path.relative_to(target_payload)
            if not original.exists() or path.is_dir() != original.is_dir():
                raise ValueError('unexpected installed payload member: ' + path.name)
    else:
        target_payload.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(payload.parent / 'PAYLOAD.json', root / 'opt/PAYLOAD.json')
    copy_kit(kit, target_kit)
    shutil.copytree(payload, target_payload, dirs_exist_ok=True)
    if form == 'wsl':
        daemon = root / 'etc/docker/daemon.json'
        daemon.parent.mkdir(parents=True, exist_ok=True)
        daemon.write_text(json.dumps(WSL_DAEMON, sort_keys=True) + '\n', encoding='ascii', newline='\n')
        bridge = root / 'usr/local/sbin/deep32-bridge'
        bridge.parent.mkdir(parents=True, exist_ok=True)
        bridge.write_text(WSL_BRIDGE_SCRIPT, encoding='ascii', newline='\n')
        bridge.chmod(0o755)
        dropin = root / 'etc/systemd/system/docker.service.d/deep32-bridge.conf'
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(WSL_BRIDGE_DROPIN, encoding='ascii', newline='\n')
        run(['systemctl', 'daemon-reload'], check=True)
        profile = json.loads((target_kit / 'pins.json').read_text(encoding='ascii')).get('release_profile', 'iso')
        installer = 'forms/install-packages.sh' if profile == 'pack' else 'autoinstall/install-packages.sh'
        run(['bash', str(target_kit / installer), '--form', 'wsl'], check=True)
        run(['systemctl', 'start', 'docker.service'], check=True)
    else:
        run(['docker', 'info'], check=True, stdout=subprocess.DEVNULL)
    form_file = root / 'etc/deep32-forge/form'
    form_file.parent.mkdir(parents=True, exist_ok=True)
    form_file.write_text(form + '\n', encoding='ascii', newline='\n')
    # Same immutable image loader and first boot as the ISO installation.
    run(['python3', str(target_kit / 'firstboot/installed-inputs.py'), 'verify'], check=True)
    unit.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(target_kit / 'firstboot/deep32-forge-firstboot.service', unit)
    run(['systemctl', 'enable', 'deep32-forge-firstboot.service'], check=True)
    run(['python3', str(target_kit / 'firstboot/firstboot.py')], check=True)
    marker.write_text(json.dumps(expected, sort_keys=True) + '\n', encoding='ascii', newline='\n')
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--form', choices=('wsl', 'docker'), required=True)
    ap.add_argument('--payload', type=Path, required=True)
    ap.add_argument('--kit', type=Path, default=Path('/opt/deep32-forge'))
    args = ap.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        ap.error('run as root on the new Linux target')
    # Loading a checker on a writable WSL kit must not create __pycache__ before
    # it has decided whether this host can be provisioned.
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location('host_requirements', args.kit / 'forms/host-requirements.py')
        host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(host)
        def preflight():
            try:
                host.check(args.form, args.payload, args.kit)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                message = str(exc)
                if not message.startswith('Install '):
                    message = 'Install a complete verified release pack before provisioning this host.'
                raise ValueError(message) from None
        provision(args.form, args.payload.resolve(strict=True), args.kit, host_check=preflight)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        ap.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
