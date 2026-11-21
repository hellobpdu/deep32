#!/usr/bin/env python3
"""Validate, preview and protect the clone-run C7 settings handoff.

A plan is read-only; install requires its digest. The internal run-loader action
wraps an already-verified loader with a bounded, sanitised wait. Settings contain
choices only. Credentials are created by the installed runtime.
"""
import argparse
import ctypes
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

FIELDS = ('node_name', 'user_name', 'time_zone', 'keyboard', 'profile', 'transport', 'publish')
DEFAULTS = {'node_name': 'deep32-forge', 'user_name': 'deep32', 'time_zone': 'Etc/UTC',
            'keyboard': 'us', 'profile': 'two-machines', 'transport': 'tailnet', 'publish': 'serve-https'}
TARGET = '/etc/deep32-forge/settings.json'
SECRET = re.compile(r'(?:bearer\s|tskey-|sk-[A-Za-z0-9]|gh[pousr]_|github_pat_|eyJ[A-Za-z0-9_-]{5,}\.|-----BEGIN|https?://|[a-z]+://|'
                    r'(?:password|passwd|token|secret|private[-_ ]?key|auth[-_ ]?key|pair(?:ing)?[-_ ]?code|cookie)[=:_ -])', re.I)


def validator():
    path = Path(__file__).resolve().parents[1] / 'gpu-pack/autoinstall/render-settings.py'
    spec = importlib.util.spec_from_file_location('deep32_c7', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    matrix = read_json(Path(__file__).resolve().parents[2] / 'install/matrix.json')
    module.profiles = sorted(row['id'] for row in matrix['profiles'])
    return module


def pairs(items):
    data = {}
    for key, value in items:
        if key in data:
            raise ValueError('duplicate JSON field')
        data[key] = value
    return data


def canonical(data):
    return (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('ascii')


def plain(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
            raise ValueError('links and reparse points are refused')
    return path


def read_json(path):
    path = plain(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise ValueError('settings input must be a small regular JSON file')
    data = json.loads(path.read_text(encoding='ascii'), object_pairs_hook=pairs)
    if not isinstance(data, dict):
        raise ValueError('settings must be a JSON object')
    return data


def validate_settings(data, profiles, c7=None):
    # Check strings recursively before displaying or saving any submitted choice.
    def scan(value):
        if isinstance(value, str) and (SECRET.search(value) or len(value) > 128):
            raise ValueError('secret-shaped settings value refused; use non-secret choices only')
        if isinstance(value, dict):
            for key, item in value.items():
                scan(key)
                scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)
    scan(data)
    c7 = c7 or validator()
    if sorted(profiles) != c7.profiles:
        raise ValueError('plan profiles do not match the C1 install matrix')
    c7.validate(data)
    if data['profile'] not in profiles:
        raise ValueError('profile is not an ID in the C1 install matrix')
    if any(field not in data for field in FIELDS):
        raise ValueError('missing non-secret settings choice')
    if data['publish'] != 'serve-https':
        raise ValueError('tailnet-plain is unavailable; choose serve-https')
    return data


def collect(path, overrides, profiles, interactive=False, prompt=input):
    data = read_json(path) if path else {'format': 'deep32-settings', 'format_version': 1, 'role': 'forge'}
    for field, value in overrides.items():
        if field in data and data[field] != value:
            raise ValueError('a command option conflicts with the settings file: ' + field)
        data[field] = value
    # Reject unknown/secret-shaped and malformed supplied input before prompting.
    probe = dict(data)
    for field in FIELDS:
        probe.setdefault(field, DEFAULTS[field])
    validate_settings(probe, profiles)
    for field in FIELDS:
        if field not in data:
            if not interactive:
                raise ValueError('missing ' + field + '; supply --settings or the non-secret option')
            value = prompt(field.replace('_', ' ') + ' [' + DEFAULTS[field] + ']: ').strip()
            data[field] = value or DEFAULTS[field]
    return validate_settings(data, profiles)


def validate_media_components(rows, core_hash):
    if not isinstance(rows, list) or len(rows) != 3:
        raise ValueError('split media requires core, the selected embedder and ingestion')
    classes = r'embedder-(turing|ampere80|ampere86|ada|hopper|blackwell100|blackwell120)'
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {'component', 'size', 'sha256'}:
            raise ValueError('invalid split media identity')
        name, size, digest = (row[key] for key in ('component', 'size', 'sha256'))
        expected = ('core', classes, 'ingestion')[index]
        if (not isinstance(name, str) or not re.fullmatch(expected, name)
                or type(size) is not int or size <= 0
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError('invalid split media identity')
    if rows[0]['sha256'] != core_hash or len({row['sha256'] for row in rows}) != len(rows):
        raise ValueError('split media hashes do not match the selection')


def dns_name(value):
    """Normalize a strict ASCII MagicDNS name."""
    if type(value) is not str or not value.isascii():
        raise ValueError('Expected a valid MagicDNS .ts.net name.')
    name = value.removesuffix('.').lower()
    if (len(name) > 253 or not name.endswith('.ts.net') or
            any(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) is None
                for label in name.split('.'))):
        raise ValueError('Expected a valid MagicDNS .ts.net name.')
    return name


def tailnet_identity(status: dict) -> tuple[str, str]:
    """Read the daemon's actual suffix and online device name."""
    if not isinstance(status, dict) or status.get('BackendState') != 'Running':
        raise ValueError('The local Tailscale device must be running and online.')
    current, device = status.get('CurrentTailnet'), status.get('Self')
    if not isinstance(current, dict) or not isinstance(device, dict) or device.get('Online') is not True:
        raise ValueError('The local Tailscale device must be online in a MagicDNS tailnet.')
    suffix = dns_name(current.get('MagicDNSSuffix'))
    hostname = dns_name(device.get('DNSName'))
    if not hostname.endswith('.' + suffix):
        raise ValueError('The local Tailscale DNS name does not belong to its reported MagicDNS suffix.')
    return suffix, hostname


def tailnet_digest(suffix):
    return hashlib.sha256(dns_name(suffix).encode('ascii')).hexdigest()


def windows_local_path(value):
    path = os.fspath(value) if isinstance(value, (str, os.PathLike)) else ''
    if (not isinstance(path, str) or re.match(r'\A[A-Za-z]:[\\/]', path) is None
            or re.search(r'[\x00-\x1f\x7f:<>"|?*]', path[2:])):
        return False
    devices = r'(?:con|prn|aux|nul|clock\$|conin\$|conout\$|(?:com|lpt)[0-9\u00b9\u00b2\u00b3])(?:\.|$)'
    return not any(part.endswith(('.', ' ')) or re.match(devices, part, re.I)
                   for part in re.split(r'[\\/]', path[3:]))


def windows_tailscale_executable():
    """Resolve native Tailscale without Windows executable search through CWD."""
    program_files = os.environ.get('ProgramFiles', '')
    roots = ([os.path.join(program_files, 'Tailscale')] if windows_local_path(program_files) else [])
    roots.extend(part.strip('"') for part in os.environ.get('PATH', '').split(os.pathsep))
    cwd = os.path.normcase(os.getcwd())
    for root in roots:
        if not windows_local_path(root) or os.path.normcase(os.path.normpath(root)) == cwd:
            continue
        try:
            executable = plain(Path(root) / 'tailscale.exe')
            if executable.is_file():
                return str(executable)
        except (OSError, ValueError):
            continue
    raise ValueError('Use a native Windows Tailscale executable from an absolute installed location.')


def confirm_tailnet(expected=None, *, prompt=None, run=subprocess.run):
    """Match an explicit operator choice to local status without printing it."""
    if expected is not None:
        expected = tailnet_digest(expected)
    elif prompt is None:
        raise ValueError('Confirm the intended tailnet with --confirm-tailnet EXPECTED_SUFFIX.')
    executable = windows_tailscale_executable() if os.name == 'nt' else 'tailscale'
    try:
        result = run([executable, 'status', '--json'], check=True, timeout=4,
                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if len(result.stdout) > 1024 * 1024:
            raise ValueError('oversize Tailscale status')
        status = json.loads(result.stdout, object_pairs_hook=pairs)
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError):
        raise ValueError('Could not verify bounded local Tailscale status; check the owning host.') from None
    actual, _ = tailnet_identity(status)
    if expected is None:
        expected = tailnet_digest(prompt('Type the intended tailnet MagicDNS suffix to confirm it: ').strip())
    actual_digest = tailnet_digest(actual)
    if expected != actual_digest:
        raise ValueError('The intended tailnet does not match this running device; check the owning host.')
    return actual_digest


def validate_tailnet_binding(identity):
    binding = identity['tailnet_sha256']
    if ('media_components' not in identity or
            (identity.get('form'), identity.get('docker_role')) != ('docker', 'forge') or
            identity.get('platform') not in {'linux', 'windows'} or
            type(binding) is not str or re.fullmatch(r'[0-9a-f]{64}', binding) is None):
        raise ValueError('A confirmed tailnet binding is supported only for a split Docker Forge plan.')


def validate_split_settings(data):
    if data['transport'] != 'tailnet':
        raise ValueError('the split Disc 3 Forge requires tailnet transport')
    if data.get('forge_accept_ct') is not True:
        raise ValueError('agree to Certificate Transparency in the guided plan or set forge_accept_ct to true in --settings')


def validate_split_paths(identity):
    # The sealed installer writes its retained launch hook as ASCII.
    if not identity['data_root'].isascii():
        raise ValueError('choose --data-root using only ASCII characters for the retained Forge startup hook')
    if identity['platform'] == 'windows' and not identity.get('docker_storage_path', '').isascii():
        raise ValueError('choose --docker-storage-path using only ASCII characters for the retained Forge startup hook')


def validate_windows_split_paths(destination, data_root, docker_storage_path):
    if not docker_storage_path:
        raise ValueError('Select the exact existing Docker Desktop backing directory with --docker-storage-path.')
    if os.name == 'nt' and not all(windows_local_path(path) for path in (destination, data_root, docker_storage_path)):
        raise ValueError('Use absolute local Windows paths for the split release folder, data and backing directories.')
    if not plain(docker_storage_path).is_dir():
        raise ValueError('Select the exact existing Docker Desktop backing directory with --docker-storage-path.')


def make_plan(settings, media, destination, journey, form, platform, docker_role, profiles, data_root=None,
              content=None, docker_storage_path=None, media_components=None, tailnet_binding=None,
              desktop_isolation_confirmed=False):
    validate_settings(settings, profiles)
    if not re.fullmatch(r'[0-9a-f]{64}', media):
        raise ValueError('verified media SHA256 is required')
    if journey not in ('J1', 'J2', 'J3', 'J4', 'J5'):
        raise ValueError('select --journey J1, J2, J3, J4 or J5')
    if (form, platform) not in (('docker', 'linux'), ('docker', 'windows'), ('wsl', 'windows')) or docker_role not in ('forge', 'full'):
        raise ValueError('select the Docker form and a supported host platform and role')
    if journey == 'J5' or settings['profile'] == 'no-gpu':
        raise ValueError('this journey/profile does not support the GPU loader')
    if form == 'docker' and not data_root:
        raise ValueError('the shared Docker lifecycle requires --data-root')
    if docker_role == 'full':
        if form != 'docker' or settings['profile'] != 'headless' or settings['transport'] != 'tailnet':
            raise ValueError('the full Docker role requires headless, tailnet and serve-https settings')
        if not data_root or not content:
            raise ValueError('the full Docker role requires --data-root and --content')
    elif journey == 'J3' or content:
        raise ValueError('this host/journey requires the full Docker role')
    windows_split = media_components is not None and (form, platform, docker_role) == ('docker', 'windows', 'forge')
    if windows_split:
        if desktop_isolation_confirmed is not True:
            raise ValueError('Confirm Docker Desktop WSL integration is disabled for unrelated distributions.')
        validate_windows_split_paths(destination, data_root, docker_storage_path)
    elif desktop_isolation_confirmed is not False:
        raise ValueError('--confirm-desktop-isolation applies only to a split Windows Forge plan.')
    identity = {'media_sha256': media, 'download_directory': str(plain(destination)), 'journey': journey,
                'form': form, 'platform': platform, 'docker_role': docker_role, 'target': TARGET}
    if form == 'docker' and data_root:
        identity.update(data_root=str(plain(data_root)))
        identity['target'] = str(plain(data_root) / 'config/settings.json')
        if docker_storage_path: identity['docker_storage_path'] = str(plain(docker_storage_path))
    if docker_role == 'full':
        identity['content'] = str(plain(content))
    if media_components is not None:
        if form != 'docker' or docker_role != 'forge':
            raise ValueError('split media installation requires the Docker Forge role')
        validate_split_settings(settings)
        validate_media_components(media_components, media)
        identity['media_components'] = media_components
        validate_split_paths(identity)
        if platform == 'windows' and tailnet_binding is None:
            raise ValueError('Confirm the intended tailnet for the split Windows Forge plan.')
    if tailnet_binding is not None:
        identity['tailnet_sha256'] = tailnet_binding
        validate_tailnet_binding(identity)
    if windows_split:
        identity['desktop_isolation_confirmed'] = True
    install_id = hashlib.sha256(canonical(identity)).hexdigest()[:24]
    plan = {'schema': 'deep32.install-plan.v1', 'install_id': install_id, 'identity': identity,
            'settings': settings, 'profiles': sorted(profiles)}
    plan['digest'] = hashlib.sha256(canonical(plan)).hexdigest()
    return plan


def check_plan(plan, accepted, c7=None):
    if set(plan) != {'schema', 'install_id', 'identity', 'settings', 'profiles', 'digest'}:
        raise ValueError('invalid installation plan')
    identity = plan['identity']
    expected = {'media_sha256', 'download_directory', 'journey', 'form', 'platform', 'docker_role', 'target'}
    if identity.get('docker_role') == 'full': expected.update(('data_root', 'content'))
    if identity.get('form') == 'docker':
        expected.add('data_root')
        if 'docker_storage_path' in identity: expected.add('docker_storage_path')
    if 'media_components' in identity:
        if identity.get('form') != 'docker' or identity.get('docker_role') != 'forge':
            raise ValueError('split media installation requires the Docker Forge role')
        if identity.get('platform') == 'windows':
            if 'tailnet_sha256' not in identity:
                raise ValueError('Confirm the intended tailnet for the split Windows Forge plan.')
            if identity.get('desktop_isolation_confirmed') is not True:
                raise ValueError('Confirm Docker Desktop WSL integration is disabled for unrelated distributions.')
            validate_windows_split_paths(identity.get('download_directory'), identity.get('data_root'),
                                         identity.get('docker_storage_path'))
            expected.add('desktop_isolation_confirmed')
        expected.add('media_components')
    if 'tailnet_sha256' in identity:
        validate_tailnet_binding(identity)
        expected.add('tailnet_sha256')
    if set(identity) != expected:
        raise ValueError('invalid target identity')
    if 'media_components' in identity:
        validate_media_components(identity['media_components'], identity['media_sha256'])
    unsigned = {key: value for key, value in plan.items() if key != 'digest'}
    target = str(plain(identity['data_root']) / 'config/settings.json') if 'data_root' in identity else TARGET
    if (plan['schema'] != 'deep32.install-plan.v1' or identity['target'] != target or
            not re.fullmatch(r'[0-9a-f]{64}', identity['media_sha256']) or
            identity['journey'] not in ('J1', 'J2', 'J3', 'J4') or
            (identity['form'], identity['platform']) not in (('docker', 'linux'), ('docker', 'windows'), ('wsl', 'windows')) or
            identity['docker_role'] not in ('forge', 'full') or not isinstance(identity['download_directory'], str) or
            plan['install_id'] != hashlib.sha256(canonical(identity)).hexdigest()[:24] or
            hashlib.sha256(canonical(unsigned)).hexdigest() != plan['digest'] or accepted != plan['digest']):
        raise ValueError('plan consent digest does not match; review the current plan again')
    validate_settings(plan['settings'], plan['profiles'], c7)
    if 'media_components' in identity:
        validate_split_settings(plan['settings'])
    if identity['docker_role'] == 'full':
        if (identity['form'] != 'docker' or plan['settings']['profile'] != 'headless' or
                plan['settings']['transport'] != 'tailnet' or not isinstance(identity['content'], str) or
                not identity['content'] or not identity['data_root']):
            raise ValueError('invalid full Docker role plan')
        plain(identity['content'])
        if 'docker_storage_path' in identity:
            if not isinstance(identity['docker_storage_path'], str) or not identity['docker_storage_path']:
                raise ValueError('invalid Docker backing storage path')
            plain(identity['docker_storage_path'])
    elif identity['journey'] == 'J3':
        raise ValueError('invalid Forge host plan')
    if 'data_root' in identity:
        if not isinstance(identity['data_root'], str) or not identity['data_root']:
            raise ValueError('invalid Docker data path')
        plain(identity['data_root'])
    if 'docker_storage_path' in identity:
        if not isinstance(identity['docker_storage_path'], str) or not identity['docker_storage_path']:
            raise ValueError('invalid Docker backing storage path')
        plain(identity['docker_storage_path'])
    if 'media_components' in identity:
        validate_split_paths(identity)
    return plan


def windows_security(path, verify=False):
    """Use the current user's SID, with a protected owner/System-only DACL."""
    from ctypes import wintypes
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), ctypes.POINTER(wintypes.DWORD)]
    adv.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, pointer]
    adv.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [pointer, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    kernel.LocalFree.argtypes = [pointer]
    handle = wintypes.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(handle)):
        raise OSError('cannot inspect current user for a restricted ACL')
    sid_text = wintypes.LPWSTR()
    try:
        size = wintypes.DWORD()
        adv.GetTokenInformation(handle, 1, None, 0, ctypes.byref(size))
        token = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(handle, 1, token, size, ctypes.byref(size)):
            raise OSError('cannot inspect current user SID')
        sid = ctypes.cast(token, ctypes.POINTER(pointer))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError('cannot format current user SID')
        owner = sid_text.value
    finally:
        if sid_text: kernel.LocalFree(sid_text)
        kernel.CloseHandle(handle)
    flags = 1 | 4  # OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    expected = 'O:' + owner + 'D:P(A;;FA;;;' + owner + ')(A;;FA;;;SY)'
    if not verify:
        descriptor = pointer()
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(expected, 1, ctypes.byref(descriptor), None):
            raise OSError('cannot create restricted settings ACL')
        try:
            if not adv.SetFileSecurityW(str(path), flags | 0x80000000, descriptor):
                raise OSError('cannot apply restricted settings ACL')
        finally:
            kernel.LocalFree(descriptor)
    needed = wintypes.DWORD()
    adv.GetFileSecurityW(str(path), flags, None, 0, ctypes.byref(needed))
    buf = ctypes.create_string_buffer(needed.value)
    if not adv.GetFileSecurityW(str(path), flags, buf, needed, ctypes.byref(needed)):
        raise OSError('cannot verify restricted settings ACL')
    text = wintypes.LPWSTR()
    if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(buf, 1, flags, ctypes.byref(text), None):
        raise OSError('cannot inspect restricted settings ACL')
    try:
        actual = text.value
        # Windows canonicalises owner aliases and ACE ordering; compare ACE sets.
        prefix, _, dacl = actual.partition('D:')
        if prefix != 'O:' + owner or not dacl.startswith('P') or set(re.findall(r'\([^)]*\)', dacl)) != {
                '(A;;FA;;;' + owner + ')', '(A;;FA;;;SY)'}:
            raise ValueError('settings ACL is not owner/System-only')
    finally:
        kernel.LocalFree(text)


def protect(path, directory=False, verify=False):
    path = plain(path)
    if os.name == 'nt':
        windows_security(path, verify)
    else:
        expected = 0o700 if directory else 0o600
        info = path.stat()
        if info.st_uid != os.geteuid():
            raise ValueError('settings target has another owner')
        if not verify:
            os.chmod(path, expected, follow_symlinks=False)
        if stat.S_IMODE(path.stat().st_mode) != expected:
            raise ValueError('settings target permissions are not restricted')


def atomic(path, content):
    path = plain(path)
    fd, temporary = tempfile.mkstemp(prefix='.settings-', dir=path.parent)
    try:
        protect(temporary)
        with os.fdopen(fd, 'wb') as stream:
            fd = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        plain(path)
        os.replace(temporary, path)
        protect(path, verify=True)
    finally:
        if fd is not None: os.close(fd)
        if os.path.exists(temporary): os.unlink(temporary)


def install(plan, accepted, target=Path(TARGET), staging=False):
    check_plan(plan, accepted)
    if plan['identity']['form'] == 'docker' and not staging:
        raise ValueError('use protected staging and the shared Docker lifecycle to install Docker settings')
    target = plain(target)
    directory = target.parent
    # The runtime provisioner owns deployment.json and its separate media journal.
    record = plain(directory / 'settings-deployment.json')
    expected_record = {'schema': 'deep32.deployment.v1', 'install_id': plan['install_id'],
                       'plan_digest': plan['digest'], 'identity': plan['identity'], 'stage': 'settings-written'}
    settings_bytes = canonical(plan['settings'])
    if directory.exists():
        if not directory.is_dir(): raise ValueError('settings parent must be a directory')
        protect(directory, directory=True, verify=True)
    if record.exists():
        protect(record, verify=True)
        if read_json(record) != expected_record:
            raise ValueError('another install ID or plan already owns this settings target')
    elif target.exists():
        raise ValueError('existing settings have no matching install record; target refused')
    if target.exists():
        protect(target, verify=True)
        if not target.is_file() or target.read_bytes() != settings_bytes:
            raise ValueError('settings differ from the consented plan; target refused')
        return 'unchanged'
    if not directory.exists():
        # No implicit creation through arbitrary missing ancestors.
        if not directory.parent.is_dir():
            raise ValueError('settings parent ancestor is missing')
        directory.mkdir(mode=0o700)
        protect(directory, directory=True)
    # The record first makes a power loss resumable without adopting another install.
    if not record.exists(): atomic(record, canonical(expected_record))
    atomic(target, settings_bytes)
    return 'written'


def stage(plan, directory):
    directory = plain(directory)
    result = install(plan, plan['digest'], directory / 'settings.json', staging=True)
    plan_file = plain(directory / 'plan.json')
    if plan_file.exists():
        protect(plan_file, verify=True)
        if plan_file.read_bytes() != canonical(plan): raise ValueError('staging plan conflict')
    else:
        atomic(plan_file, canonical(plan))
    return result


def media_cache(plan):
    identity = plan['identity']
    components = identity.get('media_components')
    if components is None:
        raise ValueError('split media plan required for the joined payload cache')
    root = plain(identity['data_root'])
    cache = plain(root.with_name(root.name + '.deep32-media'))
    cache.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache.mkdir(mode=0o700)
    except FileExistsError:
        if not cache.is_dir():
            raise ValueError('joined payload cache must be a directory') from None
        protect(cache, directory=True, verify=True)
    else:
        protect(cache, directory=True)
    key = hashlib.sha256(''.join(row['sha256'] for row in components).encode('ascii')).hexdigest()
    return plain(cache / key)


def wsl_program(plan):
    check_plan(plan, plan['digest'])
    if (plan['identity']['form'], plan['identity']['platform'], plan['identity']['docker_role']) != ('wsl', 'windows', 'forge'):
        raise ValueError('use protected staging and the shared Docker lifecycle for the Docker form')
    # The same validator and writer run in the dedicated guest; choices travel on
    # stdin, never a shell command, settings flag, environment or shared log.
    c7 = validator()
    names = (pairs, canonical, plain, read_json, validate_settings, make_plan, check_plan,
             protect, atomic, install)
    source = 'import hashlib,json,os,re,stat,tempfile\nfrom pathlib import Path\n'
    source += 'FIELDS=' + repr(FIELDS) + '\nTARGET=' + repr(TARGET) + '\nSECRET=re.compile(' + repr(SECRET.pattern) + ', re.I)\n'
    source += 'ALLOWED=set(' + repr(sorted(c7.ALLOWED)) + ')\n'
    source += 'PROFILES=set(' + repr(sorted(c7.PROFILES)) + ')\nREFUSAL=' + repr(c7.REFUSAL) + '\n'
    source += 'FORGE_PORTS=' + repr(c7.FORGE_PORTS) + '\n'
    source += '\n\n'.join(inspect.getsource(function) for function in
                          (c7.forge_ports, c7.secret_shaped, c7._validate, c7.validate))
    source += '\nclass C7:\n    profiles=' + repr(c7.profiles) + '\n    validate=staticmethod(validate)\ndef validator(): return C7\n'
    source += '\n\n'.join(inspect.getsource(function) for function in names)
    source += '\nplan=json.loads(' + repr(canonical(plan).decode('ascii')) + ', object_pairs_hook=pairs)\n'
    source += "if os.geteuid()!=0: raise ValueError('WSL settings handoff requires root')\n"
    source += "print('Guest settings: '+install(plan,plan['digest']))\n"
    return source


def windows_job(process):
    """Attach only this child tree to a kill-on-close Windows Job Object."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Basic(ctypes.Structure):
        _fields_ = [('user_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t),
                    ('max_working', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', Counters), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job: raise OSError('child process containment unavailable')
    limits = Extended()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or
            not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle)))):
        kernel.CloseHandle(job)
        raise OSError('child process containment unavailable')
    def close():
        kernel.TerminateJobObject(job, 1)
        kernel.CloseHandle(job)
    return close


def run_loader(argv, heartbeat_seconds=10, timeout_seconds=1800):
    """Run one loader without allowing its legacy protected output into logs."""
    if not argv or any(not isinstance(value, str) or not value or '\x00' in value for value in argv):
        raise ValueError('an explicit loader argument vector is required')
    if any(SECRET.search(value) or re.search(r'^--(?:token|password|passphrase|code|auth-key)(?:=|$)', value, re.I) for value in argv):
        raise ValueError('credential-shaped loader arguments are refused')
    if (not math.isfinite(heartbeat_seconds) or not math.isfinite(timeout_seconds) or
            heartbeat_seconds <= 0 or heartbeat_seconds > 10 or timeout_seconds <= 0):
        raise ValueError('loader heartbeat and deadline must be finite and positive')
    process = None
    close_job = None
    previous = {}
    interrupted = False
    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True
    def stop():
        nonlocal close_job
        if close_job is not None:
            close_job()
            close_job = None
        elif process is not None:
            try:
                if os.name == 'posix': os.killpg(process.pid, signal.SIGKILL)
                elif process.poll() is None: process.kill()
            except ProcessLookupError:
                pass
        if process is not None:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill()
    print('Waiting for the verified disc3 loader; protected runtime output is withheld.', flush=True)
    print('This stage has a finite deadline. Stop with Ctrl+C to return to a resumable operator action.', flush=True)
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, interrupt)
        flags = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, shell=False, **flags)
        if os.name == 'nt': close_job = windows_job(process)
        started = time.monotonic()
        heartbeat = started + heartbeat_seconds
        while True:
            result = process.poll()
            if result is not None:
                if result == 0:
                    print('The verified disc3 loader returned successfully; runtime readiness is a separate check.', flush=True)
                    return 0
                print('The verified disc3 loader failed; its protected output was withheld.', flush=True)
                print('Next: use the status and recovery commands in src/gpu-pack/PACK.md.', flush=True)
                return 1
            now = time.monotonic()
            if interrupted or now - started >= timeout_seconds:
                stop()
                reason = 'was interrupted' if interrupted else 'reached its deadline'
                print('The verified disc3 loader ' + reason + '; the stage needs operator action.', flush=True)
                print('Next: check src/gpu-pack/PACK.md status and recovery, then rerun the same consented install command.', flush=True)
                return 11
            if now >= heartbeat:
                print('Waiting for the verified disc3 loader; check the local host for a stopped service or unavailable runtime helper.', flush=True)
                heartbeat = now + heartbeat_seconds
            time.sleep(min(0.1, heartbeat_seconds, max(0.001, timeout_seconds - (now - started))))
    except OSError:
        stop()
        print('The verified disc3 loader could not be started safely.', flush=True)
        print('Next: rerun the read-only GPU-host prerequisite check before retrying.', flush=True)
        return 1
    finally:
        # A successful direct child must not leave its subprocesses untracked.
        stop()
        for signum, handler in previous.items(): signal.signal(signum, handler)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest='command', required=True)
    loader = commands.add_parser('run-loader')
    loader.add_argument('argv', nargs=argparse.REMAINDER)
    plan = commands.add_parser('plan')
    plan.add_argument('--settings', type=Path)
    for field in FIELDS: plan.add_argument('--' + field.replace('_', '-'))
    plan.add_argument('--media-sha256', required=True)
    plan.add_argument('--media-component', action='append', nargs=3, metavar=('COMPONENT', 'SIZE', 'SHA256'))
    plan.add_argument('--destination', required=True)
    plan.add_argument('--journey', required=True)
    plan.add_argument('--form', choices=('docker', 'wsl'), required=True)
    plan.add_argument('--platform', choices=('linux', 'windows'), required=True)
    plan.add_argument('--docker-role', default='forge')
    plan.add_argument('--data-root')
    plan.add_argument('--content')
    plan.add_argument('--docker-storage-path')
    plan.add_argument('--interactive', action='store_true')
    plan.add_argument('--linux-forge-tailnet', action='store_true')
    plan.add_argument('--confirm-tailnet', metavar='EXPECTED_SUFFIX')
    plan.add_argument('--confirm-desktop-isolation', action='store_true')
    for name in ('install', 'stage', 'wsl-program', 'media-cache'):
        command = commands.add_parser(name)
        command.add_argument('--plan', type=Path, required=True)
        command.add_argument('--accept-plan', required=True)
        if name == 'stage': command.add_argument('--directory', required=True, type=Path)
    args = ap.parse_args()
    try:
        if args.command == 'run-loader':
            argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
            return run_loader(argv)
        elif args.command == 'plan':
            matrix = read_json(Path(__file__).resolve().parents[2] / 'install/matrix.json')
            profiles = [row['id'] for row in matrix['profiles']]
            choices = {field: getattr(args, field) for field in FIELDS if getattr(args, field) is not None}
            def ask(question):
                if not sys.stdin.isatty(): raise ValueError('guided settings require an interactive terminal')
                print(question, end='', file=sys.stderr, flush=True)
                return sys.stdin.readline()
            tailnet_binding = None
            windows_split = args.media_component is not None and args.platform == 'windows'
            if windows_split:
                if not args.confirm_desktop_isolation:
                    raise ValueError('Supply --confirm-desktop-isolation after reviewing Docker Desktop WSL integration.')
                validate_windows_split_paths(args.destination, args.data_root, args.docker_storage_path)
            if windows_split or args.linux_forge_tailnet or args.confirm_tailnet is not None:
                if (args.media_component is None or
                        (args.form, args.docker_role) != ('docker', 'forge') or
                        args.linux_forge_tailnet and args.platform != 'linux'):
                    raise ValueError('Tailnet confirmation is supported only for a split Docker Forge plan.')
                tailnet_binding = confirm_tailnet(args.confirm_tailnet, prompt=ask if args.interactive else None)
            data = collect(args.settings, choices, profiles, args.interactive, prompt=ask)
            if (args.media_component is not None and args.interactive
                    and 'forge_accept_ct' not in data):
                reply = ask('Allow the Forge hostname to appear in Certificate Transparency logs? [y/N] ')
                data['forge_accept_ct'] = reply.strip().lower() in ('y', 'yes')
            components = None
            if args.media_component is not None:
                components = [{'component': name, 'size': int(size), 'sha256': digest}
                              for name, size, digest in args.media_component]
            result = make_plan(data, args.media_sha256, args.destination, args.journey, args.form, args.platform,
                               args.docker_role, profiles, args.data_root, args.content, args.docker_storage_path,
                               components, tailnet_binding=tailnet_binding,
                               desktop_isolation_confirmed=args.confirm_desktop_isolation)
            sys.stdout.write(canonical(result).decode('ascii'))
        else:
            result = check_plan(read_json(args.plan), args.accept_plan)
            if args.command == 'stage': print('Protected staging settings: ' + stage(result, args.directory))
            elif args.command == 'wsl-program': sys.stdout.write(wsl_program(result))
            elif args.command == 'media-cache': print(media_cache(result).as_posix())
            else:
                if result['identity']['form'] == 'docker':
                    raise ValueError('use protected staging and the shared Docker lifecycle to install settings')
                if os.name != 'posix' or os.geteuid() != 0: raise ValueError('native Linux settings installation requires root')
                print('Protected target settings: ' + install(result, args.accept_plan))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        ap.exit(1, 'Settings refused: ' + str(exc) + '\nNext: review the non-secret choices and run the plan again.\n')


if __name__ == '__main__':
    sys.exit(main())
