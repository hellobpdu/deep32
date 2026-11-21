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
import stat
import subprocess
import sys
import contextlib
import tempfile

COMPOSE = ['docker', 'compose', '--project-name', 'deep32-forge', '--env-file',
           '/etc/deep32-forge/images.conf', '-f', '/etc/deep32-forge/compose.yaml']


def settings_input(kit, root=Path('/')):
    config = json.loads((kit / 'pins.json').read_text(encoding='ascii'))
    member = ('forms/render-settings.py' if config.get('release_profile', 'iso') == 'pack'
              and (kit / 'forms/render-settings.py').is_file() else 'autoinstall/render-settings.py')
    spec = importlib.util.spec_from_file_location('c7_settings', kit / member)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load(root / 'etc/deep32-forge/settings.json')


def kit_identity(kit):
    value = hashlib.sha256()
    for name, path in runtime_kit(kit).runtime_members(kit).items():
        value.update(name.encode('ascii') + b'\0' + digest(path).encode('ascii') + b'\n')
    return value.hexdigest()


def write_state(path, value):
    safe_destination(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='ascii', newline='\n') as stream:
            info = os.fstat(stream.fileno())
            if info.st_nlink != 1:
                raise ValueError('Restore the installation state directory and resume the same installation.')
            stream.write(json.dumps(value, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        if os.name == 'posix':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextlib.contextmanager
def install_lock(root):
    path = root / 'etc/deep32-forge/provision.lock'
    safe_destination(path)
    # Called only after the complete read-only preflight.
    with path.open('a+b') as stream:
        if os.name == 'posix':
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Wait for the running loader to finish and run the same loader again.') from None
        yield


def lifecycle(action, form, root=Path('/'), run=subprocess.run):
    """Existing kit lifecycle; Docker hosts never require host systemd."""
    marker = root / 'etc/deep32-forge/provisioned.json'
    deployment = root / 'etc/deep32-forge/deployment.json'
    for path in (marker, deployment):
        safe_destination(path)
    record = json.loads(deployment.read_text(encoding='ascii'))
    if (record.get('form') != form or record.get('schema') != 'deep32-forge-install/1'
            or not isinstance(record.get('install_id'), str)
            or not re.fullmatch(r'[0-9a-f]{32}', record['install_id'])):
        raise ValueError('Use the recorded installation form and run the same loader again.')
    if action == 'status':
        result = {'install_id': record['install_id'], 'form': form,
                  'stage': 'provisioned' if marker.is_file() else 'interrupted',
                  'model_health': 'not-checked', 'authenticated_service': 'not-checked',
                  'corpus_query': 'not-checked'}
        status = root / 'run/deep32-forge/status'
        safe_destination(status)
        if status.is_file():
            # Never emit raw C6, pair/signin, free text or arbitrary persisted keys.
            fields = dict(re.findall(r'\b(stage|state|error)=([a-z][a-z0-9-]{0,63})(?= |$)',
                                     status.read_text(encoding='ascii').strip().split(' msg=', 1)[0]))
            if fields.get('stage') in {'install', 'firstboot', 'keys', 'signin', 'tailnet', 'content', 'import', 'pairing', 'ready'}:
                result['stage'] = fields['stage']
            if fields.get('state') in {'running', 'waiting', 'done', 'failed'}:
                result['state'] = fields['state']
            if fields.get('error') in {'forge-settings-invalid', 'forge-identity-unsafe', 'forge-identity-invalid',
                    'forge-identity-incomplete', 'forge-console-expired', 'forge-firstboot-running',
                    'forge-firstboot-failed', 'forge-gpu-class-unsupported', 'forge-pairing-expired',
                    'forge-pairing-failed', 'forge-model-health-timeout', 'forge-model-auth-denied',
                    'forge-model-response-invalid', 'tailscale-missing',
                    'tailscale-timeout', 'tailscale-daemon', 'tailscale-status-invalid',
                    'tailscale-signed-out', 'tailscale-approval', 'tailscale-key-expired', 'tailscale-offline'}:
                result['error'] = fields['error']
        state_path = root / 'etc/deep32-forge/lifecycle.json'
        safe_destination(state_path)
        if state_path.is_file():
            saved = json.loads(state_path.read_text(encoding='ascii'))
            if saved == {'schema': 'deep32-forge-lifecycle/1', 'install_id': record['install_id'], 'state': 'stopped'}:
                result['state'] = 'stopped'
        return result
    if action != 'stop':
        raise ValueError('Use status or stop for the recorded installation.')
    with install_lock(root):
        spec = importlib.util.spec_from_file_location('stop_status', root / 'opt/deep32-forge/firstboot/status.py')
        channel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(channel)
        status = channel.Status(path=root / 'run/deep32-forge/status', consoles=())
        status.update('firstboot', 'waiting', msg='Stopping this installation; wait for its containers to stop and retry if it fails')
        if form == 'wsl':
            channel.command(['systemctl', 'stop', 'deep32-forge-firstboot.service'], status, 30, run=run)
        if (root / 'etc/deep32-forge/compose.yaml').is_file():
            channel.command(COMPOSE + ['stop', '--timeout', '30'], status, 45, run=run)
        status.update('firstboot', 'done', msg='This installation is stopped; use the same loader with --resume to start it again')
        write_state(root / 'etc/deep32-forge/lifecycle.json',
                    {'schema': 'deep32-forge-lifecycle/1', 'install_id': record['install_id'], 'state': 'stopped'})
    return {'install_id': record['install_id'], 'form': form, 'state': 'stopped'}

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
    for path in (payload, manifest, sums):
        safe_destination(path)
    if any(not p.is_file() for p in (manifest, sums)):
        raise ValueError('payload inventories must be regular files')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate payload inventory field')
            value[key] = item
        return value
    rows = json.loads(manifest.read_text(encoding='ascii'), object_pairs_hook=unique)
    if not isinstance(rows, list):
        raise ValueError('payload inventory must be a list')
    expected = {}
    aliases = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256'}:
            raise ValueError('invalid payload inventory record')
        name = row['path']
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
            or str(PurePosixPath(name)) != name or name.casefold() in aliases
            or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
            or type(row['size']) is not int or row['size'] < 0):
            raise ValueError('invalid payload inventory path: ' + str(name))
        expected[name] = row
        aliases.add(name.casefold())
    if not expected or list(expected) != sorted(expected):
        raise ValueError('empty or unsorted payload inventory')
    listed = ''.join(row['sha256'] + '  ' + name + '\n' for name, row in expected.items())
    if sums.read_text(encoding='ascii') != listed:
        raise ValueError('PAYLOAD.SHA256 differs from PAYLOAD.json')
    actual = set()
    for path in sorted(payload.rglob('*')):
        safe_destination(path)
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
        if parent.exists() and getattr(parent.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('unsafe destination reparse point: ' + str(path))
    if path.is_file() and path.stat().st_nlink > 1:
        raise ValueError('unsafe destination hard link: ' + str(path))


def runtime_kit(source):
    spec = importlib.util.spec_from_file_location('provision_runtime', source / 'build/thin_forms.py')
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    runtime.pairing_files(source)
    return runtime


def copy_kit(source, target):
    safe_destination(target)
    runtime = runtime_kit(source)
    if source.resolve() != target.resolve():
        members = {Path(name): path for name, path in runtime.runtime_members(source).items()}
        copy_verified(source, target, members, runtime.runtime_mode)


def copy_verified(source, target, members, mode=None):
    """Resume only owned complete files or this loader's interrupted temporary files."""
    directories = {parent for name in members for parent in name.parents}
    if target.exists():
        for path in target.rglob('*'):
            safe_destination(path)
            name = path.relative_to(target)
            if path.is_dir():
                if name not in directories:
                    raise ValueError('Use the same verified media to resume this installation.')
                continue
            if path.name.endswith('.deep32-new'):
                original = name.with_name(path.name[:-11])
                if original not in members:
                    raise ValueError('Use the same verified media to resume this installation.')
            elif name not in members or digest(path) != digest(members[name]):
                raise ValueError('Restore the verified installed files before resuming this installation.')
    for name, original in sorted(members.items()):
        destination = target / name
        if destination.exists():
            # A retry must also make a previously renamed file durable before
            # the eventual completion receipt can be published.
            with destination.open('r+b') as stream:
                os.fsync(stream.fileno())
            continue
        temporary = destination.with_name(destination.name + '.deep32-new')
        safe_destination(temporary)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, temporary)
        if digest(temporary) != digest(original):
            raise ValueError('Restore the verified media before resuming this installation.')
        temporary.chmod(mode(original) if mode is not None else 0o644)
        # Complete data must reach storage before its name or receipt is committed.
        with temporary.open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    if os.name == 'posix':
        # Include existing directories on retries after a process interruption.
        targets = {target / directory for directory in directories} | {target, target.parent}
        for directory in sorted(targets, key=lambda item: len(item.parts), reverse=True):
            if directory.is_dir():
                descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)


def provision(form, payload, kit, root=Path('/'), run=subprocess.run, host_check=None, resume=False):
    if form not in ('wsl', 'docker'):
        raise ValueError('unknown thin form')
    data = settings_input(kit, root)
    # A matching success marker makes a repeat a read-only operation.
    marker = root / 'etc/deep32-forge/provisioned.json'
    for path in (marker, root / 'opt/deep32-payload', root / 'opt/deep32-forge',
                 root / 'opt/PAYLOAD.json', root / 'opt/PAYLOAD.SHA256', root / 'etc/docker/daemon.json',
                 root / 'etc/deep32-forge/form', root / 'etc/systemd/system/deep32-forge-firstboot.service'):
        safe_destination(path)
    if form == 'wsl':
        for path in (root / 'usr/local/sbin/deep32-bridge',
                     root / 'etc/systemd/system/docker.service.d/deep32-bridge.conf'):
            safe_destination(path)
    identity = verified_payload(payload)
    runtime_kit(kit)
    expected = {'schema': 'deep32-forge-install/1', 'form': form, 'payload': identity,
                'pins': digest(kit / 'pins.json'), 'kit': kit_identity(kit),
                'settings': hashlib.sha256(json.dumps(data, sort_keys=True).encode('ascii')).hexdigest()}
    expected['install_id'] = hashlib.sha256(json.dumps(expected, sort_keys=True).encode('ascii')).hexdigest()[:32]
    deployment = root / 'etc/deep32-forge/deployment.json'
    safe_destination(deployment)
    if deployment.exists() and json.loads(deployment.read_text(encoding='ascii')) != expected:
        raise ValueError('Use the same verified media and settings to resume this installation.')
    complete = marker.exists()
    if marker.exists():
        if json.loads(marker.read_text(encoding='ascii')) != expected:
            raise ValueError('another payload or form is already provisioned')
    state_path = root / 'etc/deep32-forge/lifecycle.json'
    safe_destination(state_path)
    if state_path.is_file():
        saved = json.loads(state_path.read_text(encoding='ascii'))
        if (saved.get('schema') != 'deep32-forge-lifecycle/1'
                or saved.get('install_id') != expected['install_id'] or saved.get('state') not in {'started', 'stopped'}):
            raise ValueError('Use the same verified installation state and resume this installation.')
        if saved['state'] == 'stopped':
            resume = True
    # The command-line entry supplies the read-only host preflight here, after
    # byte verification and the replay guard, before the first destination write.
    if host_check is not None:
        host_check()
    if complete and not resume:
        return False
    with install_lock(root):
        if not deployment.exists():
            write_state(deployment, expected)
        return _prepare(form, payload, kit, root, run, marker, expected, complete)


def _prepare(form, payload, kit, root, run, marker, expected, complete):
    spec = importlib.util.spec_from_file_location('provision_status', kit / 'firstboot/status.py')
    channel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(channel)
    status = channel.Status(path=root / 'run/deep32-forge/status', consoles=())
    def step(argv, seconds, message):
        status.update('install', 'waiting', msg=message)
        channel.command(argv, status, seconds, run=run)
    unit = root / 'etc/systemd/system/deep32-forge-firstboot.service'
    source_unit = kit / 'firstboot/deep32-forge-firstboot.service'
    if form == 'wsl' and unit.exists() and (unit.is_symlink() or digest(unit) != digest(source_unit)):
        raise ValueError('refusing an existing firstboot service')
    target_kit = root / 'opt/deep32-forge'
    target_payload = root / 'opt/deep32-payload'
    if target_payload.exists():
        # Only resume the same byte-verified input after an interrupted provision.
        manifest = root / 'opt/PAYLOAD.json'
        if not manifest.is_file() or digest(manifest) != digest(payload.parent / 'PAYLOAD.json'):
            raise ValueError('existing payload belongs to another installation')
    else:
        target_payload.parent.mkdir(parents=True, exist_ok=True)
        for name in ('PAYLOAD.json', 'PAYLOAD.SHA256'):
            manifest = root / 'opt' / name
            temporary = manifest.with_name(name + '.deep32-new')
            safe_destination(temporary)
            shutil.copyfile(payload.parent / name, temporary)
            if digest(temporary) != digest(payload.parent / name):
                raise ValueError('Restore the same verified media and resume this installation.')
            temporary.replace(manifest)
    runtime = runtime_kit(kit)
    if kit.resolve() != target_kit.resolve():
        members = {Path(name): path for name, path in runtime.runtime_members(kit).items()}
        copy_verified(kit, target_kit, members, runtime.runtime_mode)
    if not complete:
        members = {path.relative_to(payload): path for path in payload.rglob('*') if path.is_file()}
        copy_verified(payload, target_payload, members)
    if form == 'wsl' and not complete:
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
        step(['systemctl', 'daemon-reload'], 30, 'Preparing the WSL Docker service; repair this distribution and resume if it fails')
        profile = json.loads((target_kit / 'pins.json').read_text(encoding='ascii')).get('release_profile', 'iso')
        installer = 'forms/install-packages.sh' if profile == 'pack' else 'autoinstall/install-packages.sh'
        step(['bash', str(target_kit / installer), '--form', 'wsl'], 600, 'Installing verified offline WSL packages; restore the same media and resume if it fails')
        step(['systemctl', 'start', 'docker.service'], 60, 'Starting Docker inside WSL; repair this distribution and resume if it fails')
    elif form == 'docker':
        step(['docker', 'info'], 30, 'Checking the existing Docker daemon; start Docker on this host and resume if it fails')
    form_file = root / 'etc/deep32-forge/form'
    form_file.parent.mkdir(parents=True, exist_ok=True)
    if not form_file.exists():
        form_file.write_text(form + '\n', encoding='ascii', newline='\n')
    elif form_file.read_text(encoding='ascii') != form + '\n':
        raise ValueError('Use the recorded installation form and run the same loader again.')
    # Same immutable image loader and first boot as the ISO installation.
    if not complete:
        step(['python3', str(target_kit / 'firstboot/installed-inputs.py'), 'verify'], 600,
             'Verifying the installed payload; restore the same verified media and resume if it fails')
    if form == 'wsl':
        unit.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target_kit / 'firstboot/deep32-forge-firstboot.service', unit)
        step(['systemctl', 'enable', 'deep32-forge-firstboot.service'], 30,
             'Enabling the WSL first-boot service; repair this distribution and resume if it fails')
    step(['python3', str(target_kit / 'firstboot/firstboot.py')], 1800,
         'Waiting for first boot and pairing; read the local console and resume the same installation if it fails')
    write_state(marker, expected)
    write_state(root / 'etc/deep32-forge/lifecycle.json',
                {'schema': 'deep32-forge-lifecycle/1', 'install_id': expected['install_id'], 'state': 'started'})
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--form', choices=('wsl', 'docker'), required=True)
    ap.add_argument('--payload', type=Path)
    ap.add_argument('--kit', type=Path, default=Path('/opt/deep32-forge'))
    ap.add_argument('--platform', choices=('linux', 'windows'), default='windows' if os.name == 'nt' else 'linux')
    ap.add_argument('--docker-role', choices=('forge', 'full'))
    ap.add_argument('--action', choices=('prepare', 'start', 'resume', 'stop', 'status', 'upgrade'), default='prepare')
    ap.add_argument('--data-root')
    ap.add_argument('--content')
    ap.add_argument('--settings', type=Path)
    ap.add_argument('--docker-storage-path')
    args = ap.parse_args(argv)
    if args.docker_role is None:
        args.docker_role = 'forge'
    if args.form == 'docker':
        if args.docker_role == 'forge' and args.content is not None:
            ap.exit(10, 'Attach content to the Workstation for the Forge role; --content is only for --docker-role full.\n')
        member = 'docker_role.py' if args.docker_role == 'full' else 'forge_docker_role.py'
        spec = importlib.util.spec_from_file_location('selected_docker_role', args.kit / 'forms' / member)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        forwarded = [args.action, '--kit', str(args.kit), '--platform', args.platform]
        if args.data_root:
            forwarded.extend(('--data-root', args.data_root))
        if args.docker_storage_path:
            forwarded.extend(('--docker-storage-path', args.docker_storage_path))
        for key in ('payload', 'content', 'settings'):
            if getattr(args, key) is not None:
                forwarded.extend(('--' + key, str(getattr(args, key))))
        adapter.main(forwarded)
        return
    if args.docker_role == 'full':
        ap.exit(10, 'Select the Docker form for the full role.\n')
    if (args.action not in ('prepare', 'resume', 'status', 'stop')
            or args.action in ('prepare', 'resume') and args.payload is None
            or any((args.data_root, args.content, args.settings, args.docker_storage_path))):
        ap.exit(10, 'Supply the verified payload for the existing Forge provisioner or select --docker-role full.\n')
    if os.name != 'posix' or os.geteuid() != 0:
        ap.error('run as root on the new Linux target')
    # Loading a checker on a writable WSL kit must not create __pycache__ before
    # it has decided whether this host can be provisioned.
    sys.dont_write_bytecode = True
    try:
        if args.action in ('status', 'stop'):
            print(json.dumps(lifecycle(args.action, args.form), sort_keys=True))
            return
        spec = importlib.util.spec_from_file_location('host_requirements', args.kit / 'forms/host-requirements.py')
        host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(host)
        def preflight():
            try:
                data = settings_input(args.kit)
                host.check(args.form, args.payload, args.kit,
                           existing=Path('/etc/deep32-forge/deployment.json').is_file(),
                           transport=data.get('transport', 'tailnet'))
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                message = str(exc)
                if not isinstance(exc, host.PreflightError) and not message.startswith('Install '):
                    message = 'Install a complete verified release pack before provisioning this host.'
                if isinstance(exc, host.PreflightError):
                    raise
                raise ValueError(message) from None
        provision(args.form, args.payload.resolve(strict=True), args.kit, host_check=preflight,
                  resume=args.action == 'resume')
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        if getattr(exc, 'reason', '').startswith('tailscale-') or str(exc) == 'Supply valid non-secret C7 settings and run the same loader again.':
            message = str(exc)
        else:
            message = 'Restore the same verified media and installation settings, then run the same loader with --resume.'
        ap.exit(2, message + '\n')


if __name__ == '__main__':
    main()
