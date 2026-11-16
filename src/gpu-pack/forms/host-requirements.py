#!/usr/bin/env python3
"""Read-only host checks before thin-form provisioning; never install a driver."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess

GIB = 1024 ** 3
PORTS = (8081, 8082)
MIN_DOCKER = '19.03.0'
MIN_COMPOSE = '2.30.0'


def version(value):
    if not isinstance(value, str):
        raise ValueError('invalid version')
    match = re.fullmatch(r'v?(\d+(?:\.\d+){1,3})(?:[-+][A-Za-z0-9_.-]+)?', value.strip())
    if match is None:
        raise ValueError('invalid version')
    parts = tuple(int(part) for part in match[1].split('.'))
    return parts + (0,) * (4 - len(parts))


def pin(runtime, name, floor=None):
    value = runtime.get(name)
    try:
        version(value)
    except ValueError:
        raise ValueError('Install a release pack with a verified runtime.' + name + ' pin.') from None
    if floor is not None and version(value) < version(floor):
        raise ValueError('Install a release pack with runtime.' + name + ' pinned to '
                         + floor + ' or later.')
    return value


def command(argv, refusal, run):
    try:
        result = run(argv, check=True, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError(refusal)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise ValueError(refusal) from None


def at_least(actual, minimum, refusal):
    try:
        good = version(actual) >= version(minimum)
    except ValueError:
        good = False
    if not good:
        raise ValueError(refusal)


def nvidia_cdi(info):
    # Directory configuration alone does not prove that a GPU was discovered.
    def names(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from names(item)
        elif isinstance(value, list):
            for item in value:
                yield from names(item)
    return any(re.fullmatch(r'nvidia\.com/gpu=[A-Za-z0-9_.:-]+', name)
               for key in ('CDIDevices', 'DiscoveredDevices') for name in names(info.get(key)))


def available_path(path):
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def check_storage(payload, root, docker_root, disk_usage=shutil.disk_usage):
    # Retain a payload copy plus space to unpack image archives and one GiB headroom.
    files = [path for path in payload.rglob('*') if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    images = sum(path.stat().st_size for path in files
                 if path.relative_to(payload).parts[0] == 'images')
    target = available_path(root / 'opt')
    store = available_path(docker_root)
    # Requiring the combined budget on both filesystems also covers a shared one.
    needed = total + 2 * images + GIB
    for path in (target, store):
        if disk_usage(path).free < needed:
            raise ValueError('Install additional storage with at least ' + str(needed)
                             + ' free bytes for the payload, images and models.')
    return needed


def check_ports(socket_factory=socket.socket):
    sockets = []
    try:
        for port in PORTS:
            probe = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(probe)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise ValueError('Install the pack on a host with 127.0.0.1:' + str(port)
                                 + ' free by stopping the service using that port.') from None
    finally:
        for probe in sockets:
            probe.close()


def check(form, payload, kit, root=Path('/'), run=subprocess.run,
          disk_usage=shutil.disk_usage, socket_factory=socket.socket):
    """Read files and query tools only; WSL checks precede its offline package install."""
    if form not in ('docker', 'wsl'):
        raise ValueError('Install the Docker or WSL form of the pack.')
    info = {}
    if form == 'wsl':
        try:
            release = (root / 'proc/sys/kernel/osrelease').read_text(encoding='ascii').lower()
        except OSError:
            release = ''
        if 'microsoft' not in release or 'wsl2' not in release:
            raise ValueError('Install WSL 2 on Windows and import this distribution with --version 2.')
        if not (root / 'dev/dxg').exists():
            raise ValueError('Install an NVIDIA Windows driver with WSL support and expose the GPU to WSL 2.')
    runtime = json.loads((kit / 'pins.json').read_text(encoding='ascii')).get('runtime', {})
    required = [pin(runtime, name) for name in
                ('mineru_minimum_driver', 'tei_minimum_driver', 'cuda_minimum_driver')]
    if form == 'wsl':
        required.append(pin(runtime, 'windows_cuda13_driver_build'))
    minimum = max(required, key=version)
    driver_message = ('Install an NVIDIA Windows driver with WSL support at version ' if form == 'wsl'
                      else 'Install an NVIDIA Linux driver at version ') + minimum + ' or later.'
    smi = root / 'usr/lib/wsl/lib/nvidia-smi'
    program = str(smi) if form == 'wsl' and smi.is_file() else 'nvidia-smi'
    drivers = command([program, '--query-gpu=driver_version', '--format=csv,noheader'], driver_message, run)
    if not drivers:
        raise ValueError(driver_message)
    for driver in drivers.splitlines():
        at_least(driver, minimum, driver_message)
    if form == 'docker':
        docker_minimum = pin(runtime, 'docker_minimum_version', MIN_DOCKER)
        compose_minimum = pin(runtime, 'compose_minimum_version', MIN_COMPOSE)
        message = 'Install Docker Engine ' + docker_minimum + ' or later and start its daemon.'
        actual = command(['docker', 'version', '--format', '{{.Server.Version}}'], message, run)
        at_least(actual, docker_minimum, message)
        message = 'Install Docker Compose ' + compose_minimum + ' or later.'
        actual = command(['docker', 'compose', 'version', '--short'], message, run)
        at_least(actual, compose_minimum, message)
        message = 'Install the NVIDIA Container Toolkit and configure its NVIDIA runtime or GPU CDI devices for Docker.'
        try:
            info = json.loads(command(['docker', 'info', '--format', '{{json .}}'], message, run))
        except (ValueError, TypeError):
            raise ValueError(message) from None
        runtimes = info.get('Runtimes', {}) if isinstance(info, dict) else {}
        if not isinstance(info, dict) or not isinstance(runtimes, dict) or ('nvidia' not in runtimes and not nvidia_cdi(info)):
            raise ValueError(message)
    docker_root = info.get('DockerRootDir', '/var/lib/docker')
    if not isinstance(docker_root, str) or not PurePosixPath(docker_root).is_absolute():
        raise ValueError('Install Docker Engine with an absolute data-root path.')
    # Resolve the daemon path inside a synthetic root when running offline tests.
    if root != Path('/'):
        docker_root = root / docker_root.lstrip('/')
    needed = check_storage(payload, root, Path(docker_root), disk_usage)
    check_ports(socket_factory)
    return {'minimum_driver': minimum, 'free_bytes_required': needed}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--form', choices=('docker', 'wsl'), required=True)
    ap.add_argument('--payload', type=Path, required=True)
    ap.add_argument('--kit', type=Path, default=Path('/opt/deep32-forge'))
    args = ap.parse_args()
    try:
        check(args.form, args.payload, args.kit)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        # check() supplies plain refusals; malformed media has a single fallback.
        import sys
        exc = sys.exc_info()[1]
        message = str(exc)
        if not message.startswith('Install '):
            message = 'Install a complete verified release pack before provisioning this host.'
        ap.exit(2, message + '\n')


if __name__ == '__main__':
    main()
