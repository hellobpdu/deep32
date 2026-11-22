#!/usr/bin/env python3
"""The full Docker form's bounded, offline host lifecycle.

The public entry remains load-docker.sh. No application gets the Docker socket.
Only this host adapter manages the installation's named Compose project.
"""
import argparse
import ctypes
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import signal
import shutil
import stat
import subprocess
import sys
import threading
import time

SCHEMA = 'deep32-docker-role/1'
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
SERVICES = ('embeddings', 'reranker', 'door', 'qdrant', 'index-health', 'model-bridge', 'engine', 'gateway')
DESKTOP_PIPE = 'npipe:////./pipe/dockerDesktopLinuxEngine'


class Refusal(Exception):
    def __init__(self, message, code=10):
        super().__init__(message)
        self.code = code


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def read_json(path):
    safe_path(path)
    return json.loads(path.read_text(encoding='ascii'), object_pairs_hook=pairs)


def safe_path(path):
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise Refusal('Choose a local installation path without symbolic links or junctions.')
        if part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise Refusal('Choose a local installation path without reparse points.')
    if path.is_file() and path.stat().st_nlink != 1:
        raise Refusal('Replace the hard-linked installation file with a private regular file.')


def absolute_path(value, allow_root=False):
    # Refuse controls, Compose interpolation and path-list delimiters at the host boundary.
    text = str(value)
    if os.name == 'nt':
        text = text.replace('\\', '/')
        if allow_root and re.fullmatch(r'[A-Za-z]:/', text):
            path = Path(text)
            safe_path(path)
            return path
        if (re.fullmatch(r'[A-Za-z]:/[^:$<>|?*\x00-\x1f]+', text) is None or
                any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in text[3:].split('/')) or
                any(re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
                    for p in text[3:].split('/'))):
            raise Refusal('Choose a dedicated absolute local Windows path without links, interpolation or parent traversal.')
        path = Path(text)
        safe_path(path)
        return path
    if (not text or any(c in text for c in '\r\n\x00$:\\') or text.startswith('//') or
            any(ord(c) < 32 for c in text) or any(p in ('', '.', '..') for p in text[1:].split('/'))):
        raise Refusal('Choose an absolute local path without controls, interpolation or parent traversal.')
    path = Path(text)
    if not path.is_absolute() or not allow_root and path == Path(path.anchor):
        raise Refusal('Choose a dedicated absolute installation directory.')
    safe_path(path)
    return path


def atomic_json(path, value):
    safe_path(path)
    temporary = path.with_name(path.name + '.new')
    safe_path(temporary)
    with temporary.open('w', encoding='ascii', newline='\n') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == 'posix':
        temporary.chmod(0o600)
    elif os.name == 'nt':
        windows_security(temporary)
    temporary.replace(path)
    if os.name == 'posix':
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def private_directory(path):
    safe_path(path)
    existed = path.exists()
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name == 'posix':
        path.chmod(0o700)
        info = path.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise Refusal('Choose a private local data filesystem that enforces owner-only permissions.')
        if not existed:
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    elif os.name == 'nt':
        windows_security(path, verify=existed, directory=True)


def windows_security(path, verify=False, directory=False):
    security = module('docker_host_security', Path(__file__).with_name('host_security.py'))
    try:
        security.windows_security(path, verify=verify, directory=directory)
    except (OSError, ValueError):
        raise Refusal('Use private local Windows storage restricted to the installation owner, System and local Administrators.') from None


def startup_hook(root, release, docker_storage_path=None, adapter='docker_role.py'):
    """Write an owned, versioned hook; never change the host's boot scripts."""
    if os.name == 'nt':
        path = root / 'startup-hook-v1.ps1'
        safe_path(path)
        temporary = path.with_name(path.name + '.new')
        safe_path(temporary)
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        text = ('# Run from an elevated Windows PowerShell 5.1 session after Docker Desktop starts.\n'
                "$ErrorActionPreference = 'Stop'\n& " + quote(sys.executable) + ' -I -B ' +
                quote(release / 'kit/forms' / adapter) + ' resume --platform windows --data-root ' +
                quote(root) + (' --docker-storage-path ' + quote(docker_storage_path) if docker_storage_path else '') +
                '\nexit $LASTEXITCODE\n')
        temporary.write_text(text, encoding='ascii', newline='\n')
        windows_security(temporary)
        temporary.replace(path)
        return
    path = root / 'startup-hook-v1.sh'
    safe_path(path)
    temporary = path.with_name(path.name + '.new')
    safe_path(temporary)
    text = ('#!/bin/sh\nset -eu\nexec python3 -I -B ' +
            shlex.quote(str(release / 'kit/forms' / adapter)) +
            ' resume --data-root ' + shlex.quote(str(root)) + '\n')
    temporary.write_text(text, encoding='ascii', newline='\n')
    if os.name == 'posix':
        temporary.chmod(0o700)
    temporary.replace(path)


def publication_request(install_id, dns):
    """Non-secret handoff only; this never authorises or changes Serve routes."""
    routes = []
    for port, backend, paths in ((443, 8766, ('/mcp',)),
                                 (443, 8081, ('/v1/embeddings', '/info', '/health')),
                                 (8443, 8082, ('/v1/rerank', '/info', '/health'))):
        for path in paths:
            routes.append({'https_port': port, 'path': path, 'exact': True,
                           'upstream': 'http://127.0.0.1:' + str(backend), 'preserve_path': True})
    return {'schema': 'deep32.host-publication-request/1', 'install_id': install_id,
            'dns_name': dns, 'consent': 'required', 'conflicts': 'refuse',
            'unknown_paths': 'deny', 'routes': routes}


@contextmanager
def progress(reason, emit):
    message = 'Waiting for ' + reason + '; keep the verified media available and retry this action after an interruption.'
    emit(message)
    stopped = threading.Event()
    def repeat():
        while not stopped.wait(10):
            emit(message)
    thread = threading.Thread(target=repeat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)


def command(argv, reason, fix, timeout=120, emit=print):
    """Keep a ten-second heartbeat while suppressing all child diagnostics."""
    emit('Waiting for ' + reason + '; ' + fix)
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                   start_new_session=True)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Refusal('The ' + reason + ' deadline expired; ' + fix, 11)
            try:
                stdout, _ = process.communicate(timeout=min(10, remaining))
                break
            except subprocess.TimeoutExpired:
                emit('Waiting for ' + reason + '; ' + fix)
        if process.returncode:
            raise Refusal('The ' + reason + ' check failed; ' + fix, 1)
        return stdout
    except OSError:
        raise Refusal('The required host command is unavailable; ' + fix) from None
    finally:
        if process is not None and process.returncode is None:
            emit('Waiting for the timed-out host command to stop; ' + fix)
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except OSError:
                process.kill()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                # A plugin descendant may retain pipes. Never wait indefinitely
                # in Popen.__exit__ or echo any retained child diagnostics.
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    emit('The host command did not confirm its exit; stop the owned Docker operation before retrying.')


@contextmanager
def installation_lock(root):
    # Kernel lock ownership survives neither process exit nor a power loss.
    path = root / 'lifecycle.lock'
    safe_path(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if os.fstat(descriptor).st_nlink != 1:
            raise Refusal('Replace the linked lifecycle lock with a private regular file.')
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b'\0')
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            raise Refusal('Another lifecycle operation is running; wait for it to finish and retry.', 11) from None
        yield
    finally:
        os.close(descriptor)


class Lifecycle:
    def __init__(self, root, kit, run=command, emit=print, docker_storage_path=None, host_platform='linux'):
        self.root, self.kit, self.run, self.emit = root, kit, run, emit
        self.docker_storage_path = docker_storage_path
        self.storage_required = None
        self.platform = host_platform
        self.record = root / 'install.json'

    def call(self, argv, reason, fix, timeout=120):
        if self.platform == 'windows' and argv[0] == 'docker' and argv[1:3] != ['--host', DESKTOP_PIPE]:
            argv = [argv[0], '--host', DESKTOP_PIPE, *argv[1:]]
        return self.run([str(x) for x in argv], reason, fix, timeout=timeout, emit=self.emit)

    def state(self):
        try:
            if os.name == 'nt':
                windows_security(self.root, verify=True, directory=True)
                windows_security(self.record, verify=True)
            data = read_json(self.record)
            allowed = {'schema', 'install_id', 'platform', 'release', 'content', 'stage', 'settings'}
            if data.get('platform') == 'windows':
                allowed.add('docker_storage_path')
            if (set(data) != allowed or data['schema'] != SCHEMA or
                    data['platform'] not in ('linux', 'windows') or
                    re.fullmatch('[0-9a-f]{16}', data['install_id']) is None or
                    re.fullmatch('[0-9a-f]{64}', data['release']) is None):
                raise ValueError('invalid record')
            absolute_path(data['content'], allow_root=True)
            self.platform = data['platform']
            if data['platform'] == 'windows':
                saved = absolute_path(data['docker_storage_path'])
                if self.docker_storage_path is not None and absolute_path(self.docker_storage_path) != saved:
                    raise Refusal('Keep the recorded Docker Desktop backing directory for this installation.')
                self.docker_storage_path = saved
            self.settings(data['settings'])
            return data
        except (OSError, ValueError, TypeError, KeyError):
            raise Refusal('No valid full-role installation was found; provision verified media into this dedicated directory.') from None

    def settings(self, value):
        renderer = self.kit / 'autoinstall/render-settings.py'
        if not renderer.exists():
            renderer = self.kit / 'forms/render-settings.py'
        result = module('docker_c7_renderer', renderer).validate(value)
        if result['profile'] != 'headless' or result.get('transport', 'tailnet') != 'tailnet' or result.get('publish', 'serve-https') != 'serve-https':
            raise Refusal('Select C7 profile headless with tailnet transport and serve-https publication.')
        return result

    def compose(self, data, *args, timeout=120):
        release = self.root / 'releases' / data['release']
        return self.call(['docker', 'compose', '--project-name', 'deep32-' + data['install_id'],
                          '-f', release / 'compose.json', *args],
                         'the selected Docker role', 'check the host Docker service and retry the same lifecycle action.', timeout)

    def host(self, payload, platform):
        self.platform = platform
        check = module('docker_host_requirements', self.kit / 'forms/host-requirements.py')
        # Use existing version/driver/runtime/storage gates. Ports are checked on
        # initial creation only; our retained containers own them on later starts.
        config = read_json(self.kit / 'pins.json')
        minimum = config.get('full', {}).get('minimum_data_free_bytes')
        if type(minimum) is not int or minimum <= 0:
            raise Refusal('Use a release with the measured full.minimum_data_free_bytes content and index storage budget.')
        try:
            options = {}
            if platform == 'windows':
                self.docker_storage_path = absolute_path(self.docker_storage_path or
                    str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Docker'))
                options = {'host_platform': 'windows', 'docker_storage_path': self.docker_storage_path}
            result = check.check('docker', payload, self.kit, check_loopback_ports=not self.record.exists(),
                        loopback_ports=(8081, 8082, 8766), storage_target=self.root, extra_storage_bytes=minimum,
                        run=lambda argv, **kw: subprocess.CompletedProcess(argv, 0,
                            self.call(argv, 'host prerequisites', 'restore the reported host prerequisite and retry.', 30), ''), **options)
            self.storage_required = result.get('free_bytes_required') if isinstance(result, dict) else None
        except ValueError as exc:
            message = str(exc)
            if not message.startswith('Install '):
                message = 'Install the complete qualified host prerequisites and retry this action.'
            raise Refusal(message) from None
        text = self.call(['tailscale.exe' if platform == 'windows' else 'tailscale', 'status', '--json'], 'host Tailscale sign-in',
                         'sign in with the host plugin or client and retry.', 30)
        try:
            status = json.loads(text)
            if status.get('BackendState') != 'Running':
                raise ValueError('signed out')
            dns = status.get('Self', {}).get('DNSName', '').rstrip('.')
            if re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,249}[a-z0-9])?\.ts\.net', dns) is None:
                raise ValueError('missing DNS name')
        except (ValueError, TypeError, AttributeError):
            raise Refusal('The host is signed out of Tailscale; complete host sign-in and retry.') from None
        self.call(['openssl', 'version'], 'local pairing identity tools',
                  'install the release-qualified OpenSSL host tool and retry.', 30)
        return dns

    def daemon_storage(self, images, platform):
        if platform != 'windows':
            return
        if type(self.storage_required) is not int or self.storage_required <= 0:
            raise Refusal('Use the signed measured storage budget before loading the Docker Desktop role.')
        # Use a byte-verified image already loaded by images(), without a Docker
        # socket, network, writable root or additional host permissions.
        text = self.call(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
                          '--entrypoint', 'python3', self.storage_image(images), '-I', '-c',
                          'import os; s=os.statvfs("/"); print(s.f_bavail*s.f_frsize)'],
                         'Docker Desktop disk capacity', 'enlarge the Docker Desktop disk or free its storage and retry.', 30)
        if re.fullmatch(r'[0-9]+\s*', text) is None or int(text) < self.storage_required:
            raise Refusal('Increase free Docker Desktop disk capacity to at least ' + str(self.storage_required) + ' bytes and retry.')

    def storage_image(self, images):
        return images['FULL_IMAGE']

    def image_roles(self, selection, config, rows=None):
        return {'tei-' + selection, 'reranker', 'door', 'qdrant', 'full', 'model_bridge'}

    def images(self, payload, config, allow_load=True):
        # Verify all selected archives before loading any of them. The existing
        # verifier binds the measured archive to the independently signed pins.
        sys.path.insert(0, str(self.kit / 'build'))
        inputs = module('docker_installed_inputs', self.kit / 'firstboot/installed-inputs.py')
        inputs.PAYLOAD = payload
        binding = module('docker_image_binding', self.kit / 'build/image_binding.py')
        caps = self.call(['nvidia-smi.exe' if self.platform == 'windows' else 'nvidia-smi', '--query-gpu=compute_cap', '--format=csv,noheader'],
                         'GPU class detection', 'expose the selected supported NVIDIA GPU and retry.', 30)
        try:
            classes = {inputs.CAPABILITIES[line.strip()] for line in caps.splitlines() if line.strip()}
            if len(classes) != 1:
                raise ValueError('mixed GPU classes')
            selection = classes.pop()
            if config['tei'].get('selected', selection) != selection:
                raise ValueError('override differs')
        except (ValueError, KeyError):
            raise Refusal('Expose one supported NVIDIA GPU class matching the signed TEI selection and retry.') from None
        if selection == 'cpu':
            raise Refusal('The Docker GPU role needs an NVIDIA GPU; expose a supported GPU and retry.')
        rows = read_json(payload / 'images/index.json')
        wanted = self.image_roles(selection, config, rows)
        self.selected_class = selection
        selected = [row for row in rows if row['role'] in wanted]
        if len(selected) != len(wanted) or {row['role'] for row in selected} != wanted:
            raise Refusal('The selected Docker role image payload is incomplete; use rebuilt signed disc 3 media.')
        with progress('selected image archive verification', self.emit):
            checked = [(row, inputs.verified_image(config, row, binding)) for row in selected]
        images = {}
        for row, entry in checked:
            def inspected():
                # The classic store reports the image config digest as Id; the containerd store reports the
                # OCI root digest that the binding records as index_digest (image_binding.resolve_loaded).
                allowed = list(dict.fromkeys(value for value in (entry['config_digest'], entry.get('index_digest'))
                                             if value))
                missing = None
                for candidate in allowed:
                    try:
                        measured = self.call(['docker', 'image', 'inspect', candidate], 'loaded image identity',
                                             'restore the matching signed image archive and retry.', 30)
                    except Refusal as error:
                        missing = error   # not loaded under this digest; report the inspect's own refusal
                        continue
                    try:
                        info = json.loads(measured)
                        actual = info[0]['Id']
                        if (len(info) != 1 or actual not in allowed or
                                info[0]['Os'] != 'linux' or info[0]['Architecture'] != 'amd64'):
                            raise ValueError('identity differs')
                        return actual
                    except (ValueError, TypeError, KeyError, IndexError):
                        raise Refusal('A loaded image differs from its signed identity; restore the matching verified archive.') from None
                raise missing
            try:
                actual = inspected()
            except Refusal:
                if not allow_load:
                    raise
                self.call(['docker', 'image', 'load', '--input', payload / row['path']],
                          'verified offline image loading', 'restore the verified image archive and retry.', 600)
                actual = inspected()
            key = {'full': 'FULL_IMAGE', 'model_bridge': 'MODEL_BRIDGE_IMAGE',
                   'qdrant': 'QDRANT_IMAGE', 'door': 'DOOR_IMAGE', 'reranker': 'RERANKER_IMAGE',
                   'converter': 'CONVERTER_IMAGE', 'builder': 'BUILDER_IMAGE'}.get(row['role'], 'TEI_IMAGE')
            images[key] = actual
        return images

    def prepare(self, payload, content, settings, platform='linux', upgrade=False):
        if platform not in ('linux', 'windows'):
            raise Refusal('Use the generic Docker platform for this installation.')
        if self.root.exists() and not self.record.exists() and any(self.root.iterdir()) and not (self.root / 'owner.json').is_file():
            raise Refusal('Choose an empty dedicated data directory or the recorded Deep32 installation directory.')
        payload, content = absolute_path(payload), absolute_path(content, allow_root=True)
        if not content.is_dir() or not (content / 'MANIFEST.json').is_file():
            raise Refusal('Mount the matching signed disc 2 image read-only and pass its root as --content.')
        if len({self.root, payload, content, self.kit}) != 4:
            raise Refusal('Choose separate installation, runtime kit, GPU payload and content paths.')
        if self.kit.is_relative_to(self.root):
            relative = self.kit.relative_to(self.root).parts
            if len(relative) != 3 or relative[0] != 'releases' or relative[2] != 'kit' or re.fullmatch('[0-9a-f]{64}', relative[1]) is None:
                raise Refusal('Use the verified mounted runtime kit or this installation\'s exact versioned release kit.')
        for item in (self.root, payload, content):
            for other in (self.root, payload, content):
                if item != other and item.is_relative_to(other):
                    raise Refusal('Choose separate installation, GPU payload and content paths.')
        validated = self.settings(read_json(settings))
        provision = module('docker_provision', self.kit / 'forms/provision.py')
        with progress('complete payload verification', self.emit):
            identity = provision.verified_payload(payload)
        config = read_json(self.kit / 'pins.json')
        for name in ('release-key.gpg', 'release-key.fingerprint'):
            public = self.kit / 'release' / name
            safe_path(public)
            if not public.is_file() or not public.stat().st_size:
                raise Refusal('Use rebuilt signed disc 3 containing the pinned public content verification key.')
        # Full image role pins are required here; unsigned template placeholders
        # must never reach image loading or credential generation.
        profile = module('docker_release_profile', self.kit / 'build/release_profile.py')
        if profile.unresolved(config):
            raise Refusal('Use rebuilt signed disc 3 with all input pins resolved.')
        for role in ('full', 'model_bridge'):
            if IMAGE.fullmatch(config['runtime'].get(role + '_image_id', '')) is None:
                raise Refusal('Use rebuilt signed disc 3 containing the pinned full-role images.')
        old = self.state() if self.record.exists() else None
        if old and old['platform'] != platform:
            raise Refusal('Keep the recorded platform or choose a new empty installation directory.')
        # Content identity is checked transactionally by the existing importer.
        release_id = hashlib.sha256((identity + provision.digest(self.kit / 'pins.json')).encode('ascii')).hexdigest()
        if old and old['release'] != release_id and not upgrade:
            raise Refusal('Different media are already installed; use upgrade with the verified new media.')
        install_id = old['install_id'] if old else hashlib.sha256(str(self.root).encode('utf-8')).hexdigest()[:16]
        release = self.root / 'releases' / release_id
        owner = {'schema': SCHEMA, 'install_id': install_id}
        if not old and (self.root / 'owner.json').exists() and read_json(self.root / 'owner.json') != owner:
            raise Refusal('Restore the owned installation marker or choose a new empty data directory.')
        dns = self.host(payload, platform)
        config['tailscale_dns_name'] = dns
        images = self.images(payload, config)
        self.daemon_storage(images, platform)
        runtime = module('docker_full_runtime', self.kit / 'containers/full/runtime.py')
        compose = runtime.prepare_config(release / 'kit', payload, self.root, content, images, config)
        # All pure validation, preflight and archive verification precede writes.
        private_directory(self.root)
        with installation_lock(self.root):
            if self.record.exists() and self.state() != old:
                raise Refusal('The installation changed during preflight; retry the same action.', 11)
            atomic_json(self.root / 'owner.json', owner)
            for name in ('config', 'content', 'import', 'status', 'logs', 'qdrant'):
                private_directory(self.root / name)
            private_directory(release)
            provision.copy_kit(self.kit, release / 'kit')
            # Bind the verified media path. Retain it mounted; no hidden second
            # model copy is made. Start revalidates its complete byte inventory.
            atomic_json(release / 'media.json', {'payload': str(payload), 'identity': identity})
            atomic_json(release / 'compose.json', compose)
            atomic_json(self.root / 'config/settings.json', validated)
            atomic_json(self.root / 'config/model-bridge.json', config['full']['model_bridge'])
            atomic_json(self.root / 'config/publication-request.json', publication_request(install_id, dns))
            enrol = module('docker_local_enrol', self.kit / 'containers/full/local_enrol.py')
            enrol.local_enrol(self.root, self.kit)
            data = dict(schema=SCHEMA, install_id=install_id, platform=platform, release=release_id,
                        content=str(content), stage='prepared', settings=validated)
            if platform == 'windows':
                data['docker_storage_path'] = str(self.docker_storage_path)
            # Retain the previous image/config selection for operator recovery;
            # never discard imported data or automatically downgrade its schema.
            if old and old['release'] != release_id:
                atomic_json(self.root / 'previous.json', old)
            atomic_json(self.record, data)
            startup_hook(self.root, release, data.get('docker_storage_path'))
            self.start(data)
        return data

    def start(self, data=None):
        data = data or self.state()
        release = self.root / 'releases' / data['release']
        media = read_json(release / 'media.json')
        payload = absolute_path(media['payload'])
        # Recovery always uses this installation's retained runtime and pins,
        # even when the caller launched from different mounted media.
        self.kit = release / 'kit'
        provision = module('docker_start_provision', self.kit / 'forms/provision.py')
        with progress('recorded payload verification', self.emit):
            identity = provision.verified_payload(payload)
        if identity != media['identity']:
            raise Refusal('The installed GPU media changed; remount the recorded verified disc and retry.')
        if hashlib.sha256((identity + provision.digest(self.kit / 'pins.json')).encode('ascii')).hexdigest() != data['release']:
            raise Refusal('The retained runtime pins changed; restore this installation\'s recorded verified release.')
        config = read_json(self.kit / 'pins.json')
        config['tailscale_dns_name'] = self.host(payload, data['platform'])
        images = self.images(payload, config)
        self.daemon_storage(images, data['platform'])
        runtime = module('docker_start_runtime', self.kit / 'containers/full/runtime.py')
        compose = runtime.prepare_config(self.kit, payload, self.root, absolute_path(data['content'], allow_root=True), images, config)
        atomic_json(release / 'compose.json', compose)
        atomic_json(self.root / 'config/publication-request.json', publication_request(data['install_id'], config['tailscale_dns_name']))
        self.compose(data, 'config', '--quiet')
        self.compose(data, 'up', '-d', '--pull', 'never', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '600',
                     'embeddings', 'reranker', 'door', 'qdrant', 'index-health', 'model-bridge', timeout=630)
        # Import is the existing signed journal/resume pipeline in a one-shot
        # container. No engine sees a partially imported active generation.
        self.compose(data, 'run', '--rm', '--no-deps', '--pull', 'never', 'importer', timeout=3600)
        self.compose(data, 'up', '-d', '--pull', 'never', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '120',
                     'engine', 'gateway', timeout=150)
        data['stage'] = 'local-ready-publication-required'
        atomic_json(self.record, data)
        self.emit('Local services passed their health checks; complete the host HTTPS publication and headend enrolment before using MCP.')

    def stop(self):
        data = self.state()
        self.compose(data, 'stop', '--timeout', '30')
        data['stage'] = 'stopped'
        atomic_json(self.record, data)
        self.emit('This installation is stopped; its content, images and private enrolment state are retained.')

    def resume(self):
        data = self.state()
        if data['stage'] == 'stopped':
            self.emit('This installation remains stopped; use start when you want to run it again.')
            return
        self.start(data)

    def status(self):
        data = self.state()
        raw = self.compose(data, 'ps', '--all', '--format', 'json', timeout=30)
        try:
            rows = json.loads(raw) if raw.lstrip().startswith('[') else [json.loads(line) for line in raw.splitlines() if line.strip()]
            current = {row['Service']: row for row in rows if row.get('Service') in SERVICES}
            checked = {'door', 'index-health', 'model-bridge', 'engine', 'gateway'}
            healthy = all(name in current and current[name].get('State') == 'running' and
                          (name not in checked or current[name].get('Health') == 'healthy') for name in SERVICES)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise Refusal('Docker returned unreadable service status; restore Docker Compose and rerun status.') from None
        # Never relay arbitrary container labels, names, errors, log lines or env.
        result = {'schema': SCHEMA, 'install_id': data['install_id'], 'platform': data['platform'],
                  'stage': data['stage'], 'local_services_healthy': healthy,
                  'publication': 'requires-host-publication-adapter', 'ready': False,
                  'next': 'Complete verified host HTTPS publication and headend enrolment.'}
        self.emit(json.dumps(result, sort_keys=True))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'start', 'resume', 'stop', 'status', 'upgrade'))
    parser.add_argument('--platform', choices=('linux', 'windows'), default='windows' if os.name == 'nt' else 'linux')
    parser.add_argument('--data-root')
    parser.add_argument('--kit', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--payload')
    parser.add_argument('--content')
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--docker-storage-path')
    args = parser.parse_args(argv)
    if ((args.platform == 'linux' and (os.name != 'posix' or os.geteuid() != 0)) or
            (args.platform == 'windows' and (os.name != 'nt' or not ctypes.windll.shell32.IsUserAnAdmin()))):
        parser.exit(10, 'Run the full-role lifecycle as the installation administrator on its Docker host.\n')
    try:
        default = str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Deep32') if os.name == 'nt' else '/opt/deep32'
        root = absolute_path(args.data_root or default)
        lifecycle = Lifecycle(root, args.kit, docker_storage_path=args.docker_storage_path, host_platform=args.platform)
        if args.action == 'status':
            lifecycle.status()  # No lock-file creation or other write.
            return
        if args.action in ('prepare', 'upgrade') and not all((args.payload, args.content, args.settings)):
            raise Refusal('Supply --payload, --content and --settings from the matching verified media.')
        if args.action in ('prepare', 'upgrade'):
            lifecycle.prepare(args.payload, args.content, args.settings, args.platform, args.action == 'upgrade')
        else:
            lifecycle.state()
            with installation_lock(root):
                getattr(lifecycle, args.action)()
    except Refusal as exc:
        parser.exit(exc.code, str(exc) + '\n')
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        parser.exit(1, 'The full-role input or operation failed; restore the matching verified media and retry the same action.\n')


if __name__ == '__main__':
    main()
