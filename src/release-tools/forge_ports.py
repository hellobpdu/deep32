"""Read-only Forge port evidence for the consented C7 plan."""

import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess

SCHEMA = 'deep32-forge-docker-role/1'
LOCAL_DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']
DESKTOP_DOCKER = ['docker.exe', '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine']
WINDOWS_IN_USE = 10048
WINDOWS_ACCESS = 10013
TCP_TABLE_OWNER_PID_LISTENER = 3
INSUFFICIENT_BUFFER = 122
LOCAL_NAMES = ('embeddings', 'reranker', 'pairing')
STAGES = {'prepared', 'local-ready-publication-required', 'published-pairing-required', 'stopped'}
MAX_OBSERVATION = 1024 * 1024
DOCKER_ID = re.compile('[0-9a-f]{64}')
STALE_ENV = {'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY',
             'DOCKER_CERT_PATH', 'DOCKER_API_VERSION', 'DOCKER_CONFIG', 'DOCKER_AUTH_CONFIG', 'TS_SOCKET'}
# The full inspect object can contain container environment credentials.
DOOR_FORMAT = (
    '{"id":{{json .Id}},"running":{{json .State.Running}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"config":{{json (index .Config.Labels "com.docker.compose.project.config_files")}},'
    '"bindings":{{json .HostConfig.PortBindings}},'
    '"live":{{json .NetworkSettings.Ports}}}'
)


class PortConflict(ValueError):
    """A selected endpoint is occupied and fails the same-install checks."""


class PortUnavailable(ValueError):
    """The observation required to verify an endpoint is unavailable."""


class PublicationRefusal(ValueError):
    """The sealed publication policy refused the read-only observation."""


def safe_path(path):
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or part != path and not stat.S_ISDIR(info.st_mode)):
            raise PortUnavailable('Use an ordinary local Forge path without links.')


def private(info, mode):
    if os.name == 'posix' and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) != mode):
        raise PortUnavailable('Restore the root-owned private Forge state before checking port ownership.')


def private_directory(path, acl=None):
    safe_path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise PortUnavailable('Choose a dedicated Forge data directory.')
    if acl is None:
        private(info, 0o700)
    else:
        acl(path, verify=True, directory=True)


def private_file(info, *, posix=True):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PortUnavailable('Restore the protected regular Forge records before checking ownership.')
    if posix:
        private(info, 0o600)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def protected_file(path, acl):
    safe_path(path)
    private_file(path.lstat(), posix=acl is None)
    if acl is not None:
        acl(path, verify=True)


def read_protected(path, acl=None):
    private_directory(path.parent, acl)
    protected_file(path, acl)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        private_file(os.fstat(stream.fileno()), posix=acl is None)
        raw = stream.read(MAX_OBSERVATION + 1)
    if len(raw) > MAX_OBSERVATION:
        raise PortUnavailable('The protected Forge record is too large to verify.')
    return json.loads(raw.decode('ascii'), object_pairs_hook=unique)


class ReadOnlyPublicationState:
    """Expose only the methods used by the sealed preflight, with no save API."""

    Refusal = PublicationRefusal
    safe_path = staticmethod(safe_path)

    def __init__(self, installed, acl):
        self.installed = installed
        self.acl = acl

    def windows_security(self, path, verify):
        if not verify or self.acl is None:
            raise PortUnavailable('The Forge port check requires the private Windows journal ACL.')
        protected_file(path, self.acl)

    def read_json(self, path):
        if self.installed is None:
            raise PortUnavailable('The Forge publication journal has no matching install receipt.')
        return read_protected(path, self.acl)


def sealed_module(name):
    path = Path(__file__).resolve().parents[1] / 'gpu-pack/forms' / (name + '.py')
    safe_path(path)
    spec = importlib.util.spec_from_file_location('deep32_sealed_ports_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publication_module():
    return sealed_module('forge_publication')


def windows_acl():
    return sealed_module('host_security').windows_security


def install_record(root, install_id, platform, acl):
    safe_path(root)
    if not root.exists():
        return None
    if not root.is_dir():
        raise PortUnavailable('Choose a dedicated Forge data directory.')
    owner, record = root / 'owner.json', root / 'install.json'
    safe_path(owner)
    safe_path(record)
    if not owner.exists() and not record.exists():
        if any(root.iterdir()):
            raise PortUnavailable('Choose an empty Forge data directory or restore its protected records.')
        return None
    if read_protected(owner, acl) != {'schema': SCHEMA, 'install_id': install_id}:
        raise PortUnavailable('The protected Forge owner record does not match the selected data directory.')
    if not record.exists():
        return None
    data = read_protected(record, acl)
    fields = {'schema', 'platform', 'install_id', 'release', 'stage', 'settings'}
    if platform == 'windows':
        fields.add('docker_storage_path')
    if (not isinstance(data, dict)
            or set(data) != fields
            or data['schema'] != SCHEMA or data['platform'] != platform or data['install_id'] != install_id
            or not isinstance(data['release'], str) or not DOCKER_ID.fullmatch(data['release'])
            or not isinstance(data['stage'], str) or data['stage'] not in STAGES
            or not isinstance(data['settings'], dict)):
        raise PortUnavailable('Restore the matching protected Forge install receipt.')
    if platform == 'windows':
        backing = data['docker_storage_path']
        if (not isinstance(backing, str) or not Path(backing).is_absolute()
                or backing.startswith(('\\\\', '//')) or '..' in Path(backing).parts):
            raise PortUnavailable('Restore the recorded local Docker Desktop backing directory.')
        safe_path(Path(backing))
        if not Path(backing).is_dir():
            raise PortUnavailable('Restore the recorded local Docker Desktop backing directory.')
    return data


def observe(run, argv, complaint):
    environment = {key: value for key, value in os.environ.items() if key.upper() not in STALE_ENV}
    try:
        result = run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
                     env=environment, check=False, encoding='utf-8', errors='strict')
        if result.returncode == 0 and isinstance(result.stdout, str) and len(result.stdout) <= MAX_OBSERVATION:
            return result.stdout
    except (OSError, subprocess.SubprocessError, UnicodeError):
        pass
    raise PortUnavailable(complaint)


def local_owner(root, installed, selected, busy, run, publication, docker, acl):
    """Return only active loopback mappings attributable to the selected root."""
    port = busy[0][1]
    complaint = f'Host local port {port} has an occupied TCP endpoint whose Docker owner could not be verified.'
    project = 'deep32-' + installed['install_id']
    ids = observe(run, [*docker, 'ps', '--no-trunc', '--quiet',
                        '--filter', 'status=running', '--filter', 'label=com.docker.compose.project=' + project,
                        '--filter', 'label=com.docker.compose.service=door'], complaint).split()
    if not ids:
        return ()
    if len(ids) != 1 or not DOCKER_ID.fullmatch(ids[0]):
        raise PortUnavailable(complaint)
    raw = observe(run, [*docker, 'inspect', '--type', 'container', '--format', DOOR_FORMAT, ids[0]], complaint)
    try:
        door = json.loads(raw, object_pairs_hook=unique)
        if (not isinstance(door, dict)
                or set(door) != {'id', 'running', 'project', 'service', 'config', 'bindings', 'live'}):
            raise ValueError()
    except (ValueError, TypeError):
        raise PortUnavailable(complaint) from None
    if (door['id'] != ids[0] or door['project'] != project
            or door['service'] != 'door' or door['running'] is not True
            or not isinstance(door['config'], str)):
        return ()
    config = Path(door['config'])
    if (not config.is_absolute() or config.name != 'compose.json'
            or config.parent.parent != root / 'releases' or not DOCKER_ID.fullmatch(config.parent.name)):
        return ()
    safe_path(config)
    if not config.exists():
        return ()
    if acl is not None:
        private_directory(config.parent, acl)
    protected_file(config, acl)
    previous = publication.ports(installed['settings'])
    expected = {f'{internal}/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(previous[name])}]
                for name, internal in (('embeddings', 8081), ('reranker', 8082))}
    live = door['live']
    if not isinstance(live, dict) or not isinstance(door['bindings'], dict):
        raise PortUnavailable(complaint)
    if door['bindings'] != expected or {key: value for key, value in live.items() if value is not None} != expected:
        return ()
    return tuple(previous[name] for name in ('embeddings', 'reranker') if previous[name] == selected[name])


def windows_listeners(ports):
    # MIB_TCPROW_OWNER_PID / MIB_TCP6ROW_OWNER_PID. Addresses and the first
    # two port bytes are in network order, unlike the Windows DWORD fields.
    tcp = ctypes.WinDLL('iphlpapi', use_last_error=True).GetExtendedTcpTable
    tcp.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,
                    ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    tcp.restype = ctypes.c_uint32
    tables = ((socket.AF_INET, struct.Struct('<I4s2s2x4s2s2xI'), 1, 2, 0),
              (socket.AF_INET6, struct.Struct('<16sI2s2x16sI2s2xII'), 0, 2, 6))
    listeners = {port: [] for port in ports}
    for family, row, address_field, port_field, status_field in tables:
        size = ctypes.c_uint32()
        for _ in range(3):
            if size.value > MAX_OBSERVATION:
                raise PortUnavailable('The Windows TCP listener table exceeds its observation limit.')
            table = ctypes.create_string_buffer(size.value) if size.value else None
            error = tcp(table, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_LISTENER, 0)
            if error == 0 and table is not None:
                break
            if error != INSUFFICIENT_BUFFER:
                raise PortUnavailable('The Windows TCP listener table could not be observed.')
        else:
            raise PortUnavailable('The Windows TCP listener table changed during observation.')
        count = struct.unpack_from('<I', table)[0]
        if 4 + count * row.size > len(table):
            raise PortUnavailable('The Windows TCP listener table was incomplete.')
        for index in range(count):
            fields = row.unpack_from(table, 4 + index * row.size)
            port = int.from_bytes(fields[port_field], 'big')
            if port not in listeners:
                continue
            if fields[status_field] != 2:
                raise PortUnavailable('The Windows TCP listener table returned unexpected state.')
            packed = fields[address_field]
            address = socket.inet_ntop(family, packed)
            if family == socket.AF_INET6 and packed.startswith(b'\0' * 10 + b'\xff\xff'):
                address = '::ffff:' + socket.inet_ntop(socket.AF_INET, packed[12:])
            if address in ('127.0.0.1', '0.0.0.0', '::', '::ffff:0.0.0.0', '::ffff:127.0.0.1'):
                listeners[port].append((address, fields[-1]))
    return listeners


def desktop_backend(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
                                                  ctypes.POINTER(ctypes.c_uint32))
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise PortUnavailable('The Windows TCP listener owner process could not be observed.')
    try:
        path = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_uint32(len(path))
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            raise PortUnavailable('The Windows TCP listener owner process could not be observed.')
    finally:
        kernel.CloseHandle(handle)
    roots = [(key, folder) for key, folder in (('ProgramFiles', 'Docker/Docker'),
            ('LOCALAPPDATA', 'Programs/DockerDesktop')) if os.environ.get(key)]
    expected = {Path(os.environ[key]) / folder / 'resources/com.docker.backend.exe' for key, folder in roots}
    return Path(path.value) in expected


def occupied_local(selected, socket_factory, windows=False):
    busy = []
    listeners = windows_listeners([selected[name] for name in LOCAL_NAMES]) if windows else {}
    for name in LOCAL_NAMES:
        port = selected[name]
        rows = listeners.get(port, [])
        try:
            probe = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if windows:
                    try:
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    except (AttributeError, OSError):
                        raise PortUnavailable(f'Host local port {port} cannot be checked with exclusive Windows binding.') from None
                probe.bind(('127.0.0.1', port))
            finally:
                probe.close()
        except OSError as error:
            failure = getattr(error, 'winerror', None) or error.errno
            if failure not in (errno.EADDRINUSE, WINDOWS_IN_USE) and not (
                    windows and rows and failure == WINDOWS_ACCESS):
                raise PortUnavailable(f'Host local port {port} could not be observed by a loopback bind.') from None
            busy.append((name, port))
        else:
            # Winsock permits a specific exclusive bind alongside a default
            # wildcard bind. A separately observed listener still occupies it.
            if rows:
                busy.append((name, port))
    if windows:
        for name, port in busy:
            rows = listeners[port]
            if name == 'pairing' or len(rows) != 1 or rows[0][0] != '127.0.0.1':
                unowned_listener(name, port)
            try:
                same_backend = desktop_backend(rows[0][1])
            except PortUnavailable as error:
                raise PortUnavailable(f'Host local port {port} has unverified ownership. {error}') from None
            if not same_backend:
                unowned_listener(name, port)
    return busy


def unowned_listener(name, port):
    raise PortConflict(f'Host local port {port} has an unverified TCP listener; change C7 forge_ports.{name}.')


def observe_publication(publication, root, installed, install_id, host, settings, run, platform, acl):
    planned = publication.plan(host, settings)
    port_list = ', '.join(str(route['port']) for route in planned)
    ownership = f'Ownership remains unverified for host Serve ports {port_list}.'

    tailscale = 'tailscale.exe' if platform == 'windows' else 'tailscale'

    def command(argv, _reason, _fix, _timeout):
        if argv != [tailscale, 'serve', 'status', '--json']:
            raise PortUnavailable('The Forge port check refused a non-observational Serve command.')
        return observe(run, argv, 'The host Tailscale Serve route owner could not be observed.')

    client = publication.Publication(ReadOnlyPublicationState(installed, acl), root, platform, command)
    try:
        client.preflight(install_id, host, settings)
    except PublicationRefusal as error:
        message = str(error)
        if re.match(r'Host Serve port [0-9]+ already has an unowned route;', message):
            raise PortConflict(message) from None
        raise PortUnavailable(f'{message} {ownership}') from None
    except PortUnavailable as error:
        raise PortUnavailable(f'{error} {ownership}') from None


def check_ports(data_root, settings, host, run, socket_factory, platform):
    root = Path(data_root)
    if (not root.is_absolute() or root == Path(root.anchor) or '..' in root.parts
            or str(root).startswith(('\\\\', '//'))):
        raise PortUnavailable('Choose a dedicated absolute local Forge data directory.')
    identifier = hashlib.sha256(str(root).encode('utf-8')).hexdigest()[:16]
    windows = platform == 'windows'
    try:
        publication = publication_module()
        acl = windows_acl() if windows else None
        selected = publication.ports(settings)
        installed = install_record(root, identifier, platform, acl)
        observe_publication(publication, root, installed, identifier, host, settings, run, platform, acl)
        busy = occupied_local(selected, socket_factory, windows)
        model_busy = [(name, port) for name, port in busy if name != 'pairing']
        docker = DESKTOP_DOCKER if windows else LOCAL_DOCKER
        owned = local_owner(root, installed, selected, model_busy, run, publication, docker, acl) if installed and model_busy else ()
    except (PortUnavailable, PortConflict):
        raise
    except (OSError, ValueError, TypeError, ImportError, AttributeError, struct.error):
        raise PortUnavailable('Restore the verified Forge settings and protected port evidence before retrying.') from None

    for name, port in busy:
        if name == 'pairing' or port not in owned:
            unowned_listener(name, port)


def check_linux_ports(data_root: Path, settings: dict, host: str, *,
                      run=subprocess.run, socket_factory=socket.socket) -> None:
    """Require free or verified same-install Linux Forge endpoints."""
    check_ports(data_root, settings, host, run, socket_factory, 'linux')


def check_windows_ports(data_root: Path, settings: dict, host: str, *,
                        run=subprocess.run, socket_factory=socket.socket) -> None:
    """Require free or verified same-install Windows Desktop Forge endpoints."""
    check_ports(data_root, settings, host, run, socket_factory, 'windows')
