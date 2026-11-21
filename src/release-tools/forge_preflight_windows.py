#!/usr/bin/env python3
"""Check signed Forge prerequisites against native Windows Docker Desktop."""
import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import threading
import time


def load(name):
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('_windows_forge_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load('forge_preflight')
paths = load('forge_pair_windows')
DESKTOP = ['docker.exe', '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine']
DOCKER_FIELDS = (
    'OSType', 'OperatingSystem', 'Architecture', 'KernelVersion', 'ServerVersion',
    'Driver', 'DriverStatus', 'DockerRootDir', 'Runtimes', 'ID',
)
DOCKER_FORMAT = '{' + ','.join(f'"{name}":{{{{json .{name}}}}}' for name in DOCKER_FIELDS) + '}'
NATIVE_ARCHITECTURES = {'amd64', 'x86_64'}
DESKTOP_DISTRO = 'docker-desktop'
DESKTOP_DOCKER_ROOT = '/var/lib/docker'
GUEST_STORAGE_PROBE = r"""
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
for comm in /proc/[0-9]*/comm; do
    { IFS= read -r name < "$comm"; } 2>/dev/null || continue
    [ "$name" = dockerd ] || continue
    pid=${comm#/proc/}
    pid=${pid%/comm}
    case "$pid" in ''|*[!0-9]*) continue ;; esac
    nsenter -t "$pid" -m -- /bin/sh -c '
        root=/var/lib/docker
        file=$root/engine-id
        [ -f "$file" ] && [ ! -L "$file" ] || exit 3
        engine=$(head -c 65 "$file") || exit 3
        [ "$engine" = "$1" ] || exit 3
        stat -f -c "%a %S" "$root"
    ' forge-storage "$1" 2>/dev/null && exit 0
done
exit 3
"""



class Refusal(ValueError):
    """The consented host or its live prerequisites could not be verified."""


def query(argv, message, *, run, text=None, timeout=10):
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in preflight.ports.STALE_ENV |
           {'DOCKER_API_VERSION', 'DOCKER_AUTH_CONFIG', 'DOCKER_CONFIG'}}
    kwargs = {'input': text} if text is not None else {'stdin': subprocess.DEVNULL}
    result = run(argv, capture_output=True, timeout=timeout, encoding='utf-8', errors='strict',
                 env=env, check=False, **kwargs)
    if result.returncode != 0 or not isinstance(result.stdout, str) or len(result.stdout) > 1048576:
        raise Refusal(message)
    return result.stdout


def native_tool(name):
    if name == 'wsl.exe':
        # Resolve the native system executable independently of the caller's
        # PATH and the mutable SystemRoot environment value.
        system = ctypes.create_unicode_buffer(32768)
        directory = ctypes.WinDLL('kernel32', use_last_error=True).GetSystemDirectoryW
        length = directory(system, len(system))
        roots = [system.value] if 0 < length < len(system) else []
    else:
        roots = os.environ.get('PATH', '').split(os.pathsep)
        local_appdata = os.environ.get('LOCALAPPDATA', '')
        if name == 'docker.exe' and local_appdata:
            roots.append(str(Path(local_appdata) / 'Programs/DockerDesktop/resources/bin'))
    for raw in roots:
        root = Path(raw.strip('"'))
        if not root.is_absolute() or str(root).startswith(('\\\\', '//')):
            continue
        target = paths.plain(root / name)
        if target.is_file():
            return str(target)
    raise Refusal(f'Install or select the trusted native {name} executable on the local Windows host.')


def bounded_stdout(command, *, timeout, stdout_limit, **kwargs):
    """Capture at most limit + 1 bytes and include process exit in the deadline."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, **kwargs)
    received = []

    def read():
        with process.stdout as stream:
            try:
                received.append(stream.read(stdout_limit + 1))
            except OSError:
                pass

    deadline = time.monotonic() + timeout
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        reader.join(timeout)
        if reader.is_alive():
            raise subprocess.TimeoutExpired(command, timeout)
        if not received or len(received[0]) > stdout_limit:
            raise ValueError('Desktop WSL output exceeded its bound or could not be read')
        return subprocess.CompletedProcess(command, process.wait(timeout=max(0, deadline - time.monotonic())),
                                           received[0])
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


def local_run(command, *, stdout_limit=None, **kwargs):
    executable = command[0]
    if executable in ('docker.exe', 'nvidia-smi.exe', 'tailscale.exe', 'wsl.exe'):
        command = [native_tool(executable), *command[1:]]
    if stdout_limit is not None:
        return bounded_stdout(command, stdout_limit=stdout_limit, **kwargs)
    return subprocess.run(command, **kwargs)


def parse(raw):
    return json.loads(raw, object_pairs_hook=preflight.getter.manifest.unique_json)


def windows_identity():
    import winreg

    key = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
        build, revision, edition, kind = (
            winreg.QueryValueEx(handle, name)[0] for name in
            ('CurrentBuildNumber', 'UBR', 'EditionID', 'InstallationType'))
    if (kind != 'Client' or type(revision) is not int or revision < 0
            or not isinstance(build, str) or not build.isdecimal() or int(build) < 22000
            or not isinstance(edition, str) or not edition):
        raise Refusal('Use a signed Windows 11 client recipe for Docker Desktop.')
    families = {'professional': 'pro', 'core': 'home',
                'enterprise': 'enterprise', 'education': 'education'}
    normalized = next((family for prefix, family in families.items() if edition.lower().startswith(prefix)), None)
    if normalized is None:
        raise Refusal('Use a signed Windows edition for the accepted Desktop recipe.')
    return f'{build}.{revision}', normalized


def desktop_recipe(info, version, host, journey, recipes):
    preflight.getter.classic_docker_root(info)
    release = info.get('KernelVersion')
    version_text = version.get('Platform', {}).get('Name') if isinstance(version, dict) else None
    product = re.fullmatch(r'Docker Desktop ([0-9]+\.[0-9]+\.[0-9]+) \([0-9]+\)', version_text or '')
    if (info.get('OperatingSystem') != 'Docker Desktop' or info.get('Architecture') not in NATIVE_ARCHITECTURES
            or not isinstance(release, str) or 'microsoft' not in release.lower() or 'wsl2' not in release.lower()
            or product is None or version.get('Os') != 'linux' or version.get('Arch') != 'amd64'
            or info.get('ServerVersion') != version.get('Version')):
        raise Refusal('Use the local Docker Desktop WSL 2 Linux engine from the accepted recipe.')
    observed = (preflight.requirements.release_version(product[1]),
                preflight.requirements.release_version(version['Version']))
    if None in observed:
        raise Refusal('Use a released Docker Desktop and Engine with plain numeric versions.')
    # A Windows recipe states floors: this build (any update revision), edition, Desktop and Engine at or above them.
    build = int(host[0].split('.', 1)[0])
    recipe = next((row for row in recipes
                   if row['os_id'] == 'windows' and row['architecture'] == 'amd64' and row['journey'] == journey
                   and preflight.requirements.windows_admits(row, build, host[1], *observed)), None)
    if recipe is None:
        raise Refusal(preflight.requirements.windows_refusal(recipes, journey))
    def device_names(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from device_names(item)
        elif isinstance(value, list):
            for item in value:
                yield from device_names(item)

    runtime = info.get('Runtimes')
    if not ((isinstance(runtime, dict) and isinstance(runtime.get('nvidia'), dict))
            or any(re.fullmatch(r'nvidia\.com/gpu=[A-Za-z0-9_.:-]+', name)
                   for name in device_names(info.get('DiscoveredDevices')))):
        raise Refusal('Restore NVIDIA runtime or CDI GPU discovery in Docker Desktop.')
    return recipe


def in_use_vhd(path):
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # Open the existing file only, and never read it. WSL can hold the VHDX
    # even when Get-DiskImage reports it detached from the host disk stack.
    handle = create(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle != ctypes.c_void_p(-1).value:
        close(handle)
        return False
    if ctypes.get_last_error() == 32:
        return True
    raise Refusal('The selected Docker Desktop backing disk could not be observed.')


def backing_disk(identity, *, active=in_use_vhd):
    selected = identity.get('docker_storage_path')
    if not isinstance(selected, str) or selected.startswith(('\\\\', '//')) or not Path(selected).is_absolute():
        raise Refusal('Specify the Docker Desktop disk directory you confirmed in Desktop settings.')
    root = Path(selected)
    paths.inspect_path(root, directory=True)
    candidates = (root / 'docker_data.vhdx', root / 'disk/docker_data.vhdx',
                  root / 'wsl/disk/docker_data.vhdx')
    disks = []
    for path in candidates:
        if paths.plain(path).exists():
            paths.inspect_path(path)
            disks.append(path)
    message = 'Confirm the active Docker Desktop WSL 2 disk location and set --docker-storage-path to its directory.'
    if len(disks) != 1:
        raise Refusal(message)
    # The consented location is an operator assertion. A sharing conflict
    # corroborates that the selected disk is in use. It is not guest free space.
    disk = disks[0]
    before = paths.inspect_path(disk)
    if not active(disk) or not os.path.samestat(before, paths.inspect_path(disk)):
        raise Refusal(message)
    return disk


def guest_storage(info, *, run):
    root = preflight.getter.classic_docker_root(info)
    identifier = info.get('ID')
    if not isinstance(identifier, str) or re.fullmatch(
            r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', identifier) is None:
        raise Refusal('The local Desktop engine identity could not be observed.')
    complaint = 'The signed Docker guest storage budget needs a live, identifiable Desktop Linux filesystem.'
    if root != DESKTOP_DOCKER_ROOT:
        raise Refusal(complaint)
    env = {key: value for key, value in os.environ.items() if key.upper() != 'WSLENV'}

    def observe(*argv, limit, timeout):
        result = run(['wsl.exe', *argv], env=env, stdin=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdout_limit=limit, timeout=timeout)
        if result.returncode != 0 or not isinstance(result.stdout, bytes) or len(result.stdout) > limit:
            raise Refusal(complaint)
        return result.stdout

    try:
        running = observe('--list', '--running', '--quiet', limit=65536, timeout=4)
        encoding = ('utf-16' if running.startswith((b'\xff\xfe', b'\xfe\xff'))
                    else 'utf-16-le' if b'\0' in running else 'utf-8-sig')
        if DESKTOP_DISTRO not in running.decode(encoding).splitlines():
            raise Refusal('Start the approved Docker Desktop Linux engine before checking guest storage.')
        # Target only the already listed Desktop distribution. The guest
        # probe still requires the daemon observed on the native named pipe.
        raw = observe('--distribution', DESKTOP_DISTRO, '--user', 'root', '--exec', '/bin/sh',
                      '-c', GUEST_STORAGE_PROBE, 'forge-storage', identifier.lower(),
                      limit=128, timeout=5)
        fields = re.fullmatch(rb'([0-9]{1,19}) ([0-9]{1,19})', raw.strip())
        if fields is not None:
            blocks, size = map(int, fields.groups())
            if size > 0 and blocks * size < 2**63:
                return blocks * size
    except (Refusal, paths.Refusal):
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        raise Refusal(complaint) from None
    raise Refusal(complaint)


def physical_storage(identity, budget, backing, guest_free, *, observe=preflight.getter.observed_volume):
    # Downloads were authenticated in place. The joined cache and data root
    # are siblings. The Desktop VHD also needs the separate guest capacity.
    data_path = paths.plain(Path(identity['data_root']))
    backing_path = backing.parent
    targets = ((data_path, budget['data_bytes'], False),
               (backing_path, budget['desktop-backing_bytes'], True))
    row = preflight.getter.storage_row
    rows = [row('desktop-guest', budget['docker_bytes'], guest_free, budget)]
    for directory, needed, exists in targets:
        device, free = observe(str(directory), existing=exists)
        rows.append(row('windows-' + device, needed, free, budget))
    return rows


def collect(args, *, run=local_run, host=windows_identity, probe_backing=backing_disk,
            probe_guest=guest_storage, disk_check=physical_storage, port_check=None):
    plan = Path(args.plan)
    paths.protected(paths.restrict_acl(paths.module('_windows_c10_security', paths.GPU_PACK / 'forms/host_security.py')), plan)
    accepted = preflight.settings.check_plan(preflight.settings.read_json(plan), args.accept_plan)
    identity = accepted['identity']
    if ((identity['journey'], identity['form'], identity['platform'], identity['docker_role'])
            != (args.journey, 'docker', 'windows', 'forge')
            or Path(identity['data_root']) != Path(args.forge_data_root).absolute()
            or not identity.get('tailnet_sha256') or identity.get('desktop_isolation_confirmed') is not True):
        raise Refusal('Use the consented native Windows Forge plan with confirmed tailnet and Desktop isolation.')
    budget, envelope = preflight.signed_evidence(args, identity, backend='docker-desktop')
    if not envelope or not {'windows_cuda13_driver_build', 'storage_evidence_sha256'} <= envelope.keys():
        raise Refusal('Obtain the exact signed Windows Forge prerequisites and Desktop storage evidence.')

    complaint = 'Restore the running local Docker Desktop WSL 2 Linux engine.'
    info = parse(query([*DESKTOP, 'info', '--format', DOCKER_FORMAT], complaint, run=run))
    version = parse(query([*DESKTOP, 'version', '--format', '{{json .Server}}'], complaint, run=run))
    runtime = info.get('Runtimes', {})
    if not isinstance(runtime, dict) or not isinstance(runtime.get('nvidia'), dict):
        info['DiscoveredDevices'] = parse(query(
            [*DESKTOP, 'info', '--format', '{{json .DiscoveredDevices}}'],
            'Restore NVIDIA runtime or CDI GPU discovery in Docker Desktop.', run=run))
    recipe = desktop_recipe(info, version, host(), args.journey, envelope['recipes'])
    compose = query([*DESKTOP, 'compose', 'version', '--short'],
                    'Install a compatible Docker Compose release on this host.', run=run).strip()
    try:
        compose_version = preflight.requirements.version_tuple(compose)
    except ValueError:
        raise Refusal('Use an upstream Compose version accepted by the sealed installer.') from None
    if compose_version < (2, 30, 0, 0):
        raise Refusal('Use Docker Compose 2.30.0 or later on this host.')
    raw = query(['nvidia-smi.exe', *preflight.GPU_QUERY[1:]], 'Restore native NVIDIA GPU observations.', run=run)
    gpu_policy = dict(envelope, driver_floors=dict(
        envelope['driver_floors'], windows=envelope['windows_cuda13_driver_build']))
    gpu = preflight.gpu_checks(raw, args.gpu_class, gpu_policy)
    if gpu.get('gpu.capability') is not True:
        raise Refusal('Use the signed Forge media for the visible NVIDIA GPU class.')
    if gpu.get('gpu.driver') is not True or gpu.get('gpu.memory') is not True:
        floor = max(gpu_policy['driver_floors'].values(), key=preflight.requirements.version_tuple)
        raise Refusal(f'Use NVIDIA driver {floor} or later and at least {envelope["minimum_vram_mib"]} MiB on every selected GPU.')

    state = parse(query(['tailscale.exe', 'status', '--json'], 'Restore local Tailscale sign-in.', run=run))
    suffix, hostname = preflight.settings.tailnet_identity(state)
    if (state['CurrentTailnet'].get('MagicDNSEnabled') is not True or
            preflight.settings.tailnet_digest(suffix) != identity['tailnet_sha256']):
        raise Refusal('Confirm the intended live MagicDNS tailnet and review the Forge plan again.')
    domains = state.get('CertDomains')
    if not isinstance(domains, list) or not any(isinstance(item, str) and item.lower().rstrip('.') == hostname
                                                for item in domains):
        raise Refusal('Enable HTTPS certificates for the confirmed Forge tailnet and approve the public hostname.')
    if port_check is None:
        port_check = preflight.ports.check_windows_ports
    port_check(Path(identity['data_root']), accepted['settings'], hostname, run=run)
    rows = disk_check(identity, budget, probe_backing(identity), probe_guest(info, run=run))
    checks = dict.fromkeys((
        'platform.recipe', 'host.membership-confirmed', 'host.magicdns', 'host.https-certificates',
        'windows.build', 'windows.docker-wsl2', 'windows.wsl-version', 'windows.docker-gpu',
        'windows.docker-isolation', 'gpu.release-envelope', 'gpu.driver', 'gpu.capability',
        'gpu.memory', 'gpu.ports', 'docker.local-context', 'docker.nvidia-runtime', 'docker.storage',
    ), True)
    checks['docker.server'] = {'state': 'met', 'version': version['Version']}
    checks['docker.compose'] = {'state': 'met', 'version': '.'.join(map(str, compose_version))}
    result = {'prerequisites': checks, 'storage_plan': rows}
    # A waived recipe passed the same checks as any other; the caller prints this one line.
    notice = preflight.requirements.waiver_notice(recipe)
    if notice is not None:
        result['notice'] = notice
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('source', 'gpu-class', 'forge-data-root', 'key', 'fingerprint',
                 'plan', 'accept-plan', 'journey'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--gpg', default='')
    args = parser.parse_args(argv)
    args.release = 'v0.1.0-alpha.2'
    if os.name != 'nt':
        parser.exit(10, 'Use native Windows PowerShell for Docker Desktop prerequisites.\n')
    try:
        observed = collect(args)
    except (Refusal, preflight.ports.PortConflict, preflight.ports.PortUnavailable,
            preflight.getter.DockerImageStoreRefusal, paths.Refusal) as error:
        parser.exit(10, str(error) + '\n')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        parser.exit(10, 'The signed Windows Forge prerequisites or live host observations could not be verified.\n')
    print(json.dumps(observed, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
