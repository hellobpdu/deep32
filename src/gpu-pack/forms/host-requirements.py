#!/usr/bin/env python3
"""Read-only host checks before thin-form provisioning; never install a driver."""
import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess

GIB = 1024 ** 3
PORTS = (8081, 8082)
MIN_DOCKER = '19.03.0'
MIN_COMPOSE = '2.30.0'
DESKTOP_PIPE = 'npipe:////./pipe/dockerDesktopLinuxEngine'


def windows_private(path, create=False):
    """Verify retained owner/System ACLs; apply them only to new test/state paths."""
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction() or (part.exists() and
                getattr(part.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise ValueError('Restore private local installation paths without links or reparse points.')
    if path.is_file() and path.stat().st_nlink != 1:
        raise ValueError('Restore private regular installation files without hard links.')
    spec = importlib.util.spec_from_file_location('docker_windows_security', Path(__file__).with_name('host_security.py'))
    security = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(security)
    security.windows_security(path, verify=not create, directory=path.is_dir())


class PreflightError(ValueError):
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


def tailscale_status(form, run=subprocess.run):
    """Observe the owning host only; WSL never creates a Linux tailnet node."""
    program = 'tailscale.exe' if form in ('wsl', 'windows') else 'tailscale'
    location = 'Windows host' if form in ('wsl', 'windows') else 'Docker host'
    try:
        result = run([program, 'status', '--json'], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        raise PreflightError('tailscale-missing', 'Install Tailscale on the ' + location + ' and run the same loader again.') from None
    except subprocess.TimeoutExpired:
        raise PreflightError('tailscale-timeout', 'Restart Tailscale on the ' + location + ' and run the same loader again.') from None
    except (OSError, subprocess.SubprocessError):
        raise PreflightError('tailscale-daemon', 'Start the Tailscale daemon on the ' + location + ' and run the same loader again.') from None
    if result.returncode:
        raise PreflightError('tailscale-daemon', 'Start the Tailscale daemon on the ' + location + ' and run the same loader again.')
    try:
        if len(result.stdout) > 1024 * 1024:
            raise ValueError()
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or not isinstance(value.get('BackendState'), str):
            raise ValueError()
    except (ValueError, TypeError):
        raise PreflightError('tailscale-status-invalid', 'Repair Tailscale status on the ' + location + ' and run the same loader again.') from None
    state = value['BackendState']
    if state in {'NeedsLogin', 'NoState'}:
        raise PreflightError('tailscale-signed-out', 'Run ' + program + ' login on the ' + location + ' and run the same loader again.')
    if state == 'NeedsMachineAuth':
        raise PreflightError('tailscale-approval', 'Approve this device in the Tailscale admin console and run the same loader again.')
    if state != 'Running':
        raise PreflightError('tailscale-daemon', 'Start the Tailscale daemon on the ' + location + ' and run the same loader again.')
    peer = value.get('Self')
    if not isinstance(peer, dict) or type(peer.get('Online')) is not bool:
        raise PreflightError('tailscale-status-invalid', 'Repair Tailscale status on the ' + location + ' and run the same loader again.')
    if peer.get('Expired') is True:
        raise PreflightError('tailscale-key-expired', 'Run ' + program + ' login on the ' + location + ' and run the same loader again.')
    if not peer['Online']:
        raise PreflightError('tailscale-offline', 'Restore the ' + location + ' connection to Tailscale and run the same loader again.')
    name = peer.get('HostName', '')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', name):
        raise PreflightError('tailscale-status-invalid', 'Repair Tailscale status on the ' + location + ' and run the same loader again.')
    # Deliberately return only public fields, never AuthURL, peer addresses or raw output.
    return {'name': name, 'state': state}


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


def minimum_driver(runtime, selected_roles=None, windows=False):
    """Select floors from a caller-verified image map; old callers stay strict.

    This is an internal hook for authenticated capability selection. It does not
    interpret missing pin sections as permission to omit an installed runtime.
    """
    ingestion = True
    if selected_roles is not None:
        if isinstance(selected_roles, (str, bytes)):
            raise ValueError('Install a release with a verified Forge image selection.')
        try:
            roles = set(selected_roles)
        except TypeError:
            raise ValueError('Install a release with a verified Forge image selection.') from None
        core = {'TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE'}
        extras = roles - core
        if not core <= roles or extras not in (
                set(), {'CONVERTER_IMAGE'}, {'CONVERTER_IMAGE', 'BUILDER_IMAGE', 'QDRANT_IMAGE'}):
            raise ValueError('Install a release with a verified Forge image selection.')
        ingestion = 'CONVERTER_IMAGE' in roles
    names = ['tei_minimum_driver', 'cuda_minimum_driver']
    if ingestion:
        names.append('mineru_minimum_driver')
    if windows:
        names.append('windows_cuda13_driver_build')
    return max((pin(runtime, name) for name in names), key=version)


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


def measured_storage(document, *, enforce=True, **verified_inputs):
    """Internal preflight/diagnostic seam for authenticated release measurements.

    Callers must supply the verified release digest/selection, real volume
    observations and backend-specific physical measurements. In particular the
    Desktop internal store is not its host backing directory. This does not
    activate a smaller installer gate: current lifecycle callers retain the
    legacy gate until the producer/trust/observation handoff is qualified.
    """
    spec = importlib.util.spec_from_file_location('host_storage_requirements',
        Path(__file__).with_name('storage_requirements.py'))
    storage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(storage)
    report = storage.plan(document, **verified_inputs)
    return storage.require(report) if enforce else report


def check_storage(payload, root, docker_root, disk_usage=shutil.disk_usage,
                  target=None, extra_bytes=0, installed_inventory=None):
    # Retain a payload copy plus space to unpack image archives and one GiB headroom.
    # Full-role callers add their signed measured content/index/rollback budget.
    if type(extra_bytes) is not int or extra_bytes < 0:
        raise ValueError('Install a release pack with a verified non-negative storage budget.')
    if installed_inventory is None:
        files = [path for path in payload.rglob('*') if path.is_file()]
        total = sum(path.stat().st_size for path in files)
        images = sum(path.stat().st_size for path in files
                     if path.relative_to(payload).parts[0] == 'images')
    else:
        # The lifecycle verifies this retained original inventory first. Keep the
        # full old gate even though the runtime copy no longer holds archives;
        # measured replacement budgets belong to the separate storage contract.
        rows = json.loads(Path(installed_inventory).read_text(encoding='ascii'))
        if not isinstance(rows, list) or not rows:
            raise ValueError('Install with the verified original payload inventory.')
        seen, total, images = set(), 0, 0
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256'}:
                raise ValueError('Install with the verified original payload inventory.')
            name = row['path']
            if (not isinstance(name, str) or not name or '\\' in name or
                    any(ord(char) < 32 for char in name) or
                    PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or
                    str(PurePosixPath(name)) != name or name in seen or
                    type(row['size']) is not int or row['size'] < 0 or
                    not isinstance(row['sha256'], str) or
                    re.fullmatch(r'[0-9a-f]{64}', row['sha256']) is None):
                raise ValueError('Install with the verified original payload inventory.')
            seen.add(name)
            total += row['size']
            if PurePosixPath(name).parts[0] == 'images':
                images += row['size']
        if [row['path'] for row in rows] != sorted(seen):
            raise ValueError('Install with the verified original payload inventory.')
    target = available_path(Path(target) if target is not None else root / 'opt')
    store = available_path(docker_root)
    # Requiring the combined budget on both filesystems also covers a shared one.
    needed = total + 2 * images + GIB + extra_bytes
    for path in (target, store):
        if disk_usage(path).free < needed:
            raise ValueError('Install additional storage with at least ' + str(needed)
                             + ' free bytes on the data and Docker filesystems for the payload, images, models and data.')
    return needed


def check_ports(socket_factory=socket.socket, ports=PORTS):
    sockets = []
    try:
        for port in ports:
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
          disk_usage=shutil.disk_usage, socket_factory=socket.socket, check_loopback_ports=True,
          loopback_ports=PORTS, storage_target=None, extra_storage_bytes=0,
          existing=False, transport='tailnet', host_platform='linux', docker_storage_path=None,
          selected_roles=None, installed_inventory=None):
    """Read files and query tools only; WSL checks precede its offline package install."""
    if form not in ('docker', 'wsl'):
        raise ValueError('Install the Docker or WSL form of the pack.')
    windows = form == 'docker' and host_platform == 'windows'
    def docker(*args):
        return ['docker', '--host', DESKTOP_PIPE, *args] if windows else ['docker', *args]
    if host_platform not in ('linux', 'windows'):
        raise ValueError('Install on a Linux Docker host or Windows Docker Desktop.')
    if transport == 'tailnet':
        tailscale_status('windows' if windows else form, run)
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
    minimum = minimum_driver(runtime, selected_roles, windows=form == 'wsl' or windows)
    driver_message = ('Install an NVIDIA Windows driver with Docker Desktop WSL 2 GPU support at version ' if windows else
                      'Install an NVIDIA Windows driver with WSL support at version ' if form == 'wsl'
                      else 'Install an NVIDIA Linux driver at version ') + minimum + ' or later.'
    smi = root / 'usr/lib/wsl/lib/nvidia-smi'
    program = str(smi) if form == 'wsl' and smi.is_file() else 'nvidia-smi.exe' if windows else 'nvidia-smi'
    drivers = command([program, '--query-gpu=driver_version', '--format=csv,noheader'], driver_message, run)
    if not drivers:
        raise ValueError(driver_message)
    for driver in drivers.splitlines():
        at_least(driver, minimum, driver_message)
    if form == 'docker':
        docker_minimum = pin(runtime, 'docker_minimum_version', MIN_DOCKER)
        compose_minimum = pin(runtime, 'compose_minimum_version', MIN_COMPOSE)
        message = ('Install Docker Desktop using Linux containers with its WSL 2 backend and GPU support; start its daemon.'
                   if windows else 'Install Docker Engine ' + docker_minimum + ' or later and start its daemon.')
        actual = command(docker('version', '--format', '{{.Server.Version}}'), message, run)
        at_least(actual, docker_minimum, message)
        # prerequisite fixes apply to the host's own installation method.
        message = ('Install the Docker Compose v2 plugin at version ' + compose_minimum
                   + " or later using this host's package manager or the vendor download; rerun this check.")
        actual = command(docker('compose', 'version', '--short'), message, run)
        at_least(actual, compose_minimum, message)
        message = ('Install Docker Desktop using Linux containers with its WSL 2 backend and GPU support.' if windows else
                   'Install the NVIDIA Container Toolkit and configure its NVIDIA runtime or GPU CDI devices for Docker.')
        try:
            info = json.loads(command(docker('info', '--format', '{{json .}}'), message, run))
        except (ValueError, TypeError):
            raise ValueError(message) from None
        if not isinstance(info, dict) or info.get('OSType') != 'linux':
            raise ValueError('Install using a Linux Docker engine on the checked GPU host '
                             '(Docker Desktop on Windows); '
                             'Windows containers are not supported. Review the Docker endpoint '
                             'before retrying; do not change another host or pull a test image.')
        runtimes = info.get('Runtimes', {}) if isinstance(info, dict) else {}
        if (not isinstance(info, dict) or not isinstance(runtimes, dict) or
                ('nvidia' not in runtimes and not nvidia_cdi(info))):
            raise ValueError(message)
        if windows:
            if (info.get('OSType') != 'linux' or info.get('OperatingSystem') != 'Docker Desktop' or
                    'microsoft' not in str(info.get('KernelVersion', '')).lower() or
                    'wsl2' not in str(info.get('KernelVersion', '')).lower()):
                raise ValueError(message)
    docker_root = info.get('DockerRootDir', '/var/lib/docker')
    if not isinstance(docker_root, str) or not PurePosixPath(docker_root).is_absolute():
        raise ValueError('Install Docker Engine with an absolute data-root path.')
    # Native Windows cannot stat the Linux daemon's data-root. Its backing
    # filesystem and the data destination are checked here; after offline image
    # load the same signed image performs a networkless daemon statvfs probe.
    if windows:
        if docker_storage_path is None:
            local = os.environ.get('LOCALAPPDATA')
            if not local:
                raise ValueError('Install with --docker-storage-path naming the local Docker Desktop disk backing directory.')
            docker_storage_path = Path(local) / 'Docker'
        docker_root = Path(docker_storage_path)
        # Reject network/device namespaces before any metadata query can follow them.
        if str(docker_root).startswith(('\\\\', '//')):
            raise ValueError('Install with a direct local Docker Desktop disk backing '
                             'directory; network shares and device paths are not supported.')
        if not docker_root.is_absolute() or not docker_root.is_dir():
            raise ValueError('Install with an absolute --docker-storage-path naming the existing '
                             'Docker Desktop disk backing directory; confirm its location in '
                             'Desktop settings before retrying.')
        for part in (docker_root, *docker_root.parents):
            if part.is_symlink() or part.is_junction() or (
                    getattr(part.lstat(), 'st_file_attributes', 0) &
                    getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
                raise ValueError('Install with a direct local Docker Desktop disk backing '
                                 'directory without links or reparse points, so its free-space '
                                 'check measures the approved filesystem.')
    elif root != Path('/'):
        docker_root = root / docker_root.lstrip('/')
    needed = check_storage(payload, root, Path(docker_root), disk_usage,
                           target=storage_target, extra_bytes=extra_storage_bytes,
                           installed_inventory=installed_inventory)
    # A replay owns the bound model doors; probing them as unowned would prevent recovery.
    if check_loopback_ports and not existing:
        check_ports(socket_factory, loopback_ports)
    return {'minimum_driver': minimum, 'free_bytes_required': needed}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--form', choices=('docker', 'wsl'), required=True)
    ap.add_argument('--payload', type=Path, required=True)
    ap.add_argument('--kit', type=Path, default=Path('/opt/deep32-forge'))
    ap.add_argument('--platform', choices=('linux', 'windows'), default='linux')
    ap.add_argument('--docker-storage-path', type=Path)
    ap.add_argument('--data-root', type=Path)
    ap.add_argument('--docker-role', choices=('forge', 'full'))
    args = ap.parse_args()
    try:
        full = (args.docker_role or 'forge') == 'full'
        extra = json.loads((args.kit / 'pins.json').read_text(encoding='ascii')).get('full', {}).get('minimum_data_free_bytes', 0) if full else 0
        if full and (type(extra) is not int or extra <= 0):
            raise ValueError('Install a release with the signed measured full-role storage budget.')
        check(args.form, args.payload, args.kit, host_platform=args.platform,
              docker_storage_path=args.docker_storage_path, storage_target=args.data_root,
              loopback_ports=(8081, 8082, 8766) if full else PORTS, extra_storage_bytes=extra)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        # check() supplies plain refusals; malformed media has a single fallback.
        import sys
        exc = sys.exc_info()[1]
        message = str(exc)
        if not isinstance(exc, PreflightError) and not message.startswith('Install '):
            message = 'Install a complete verified release pack before provisioning this host.'
        ap.exit(2, message + '\n')


if __name__ == '__main__':
    main()
