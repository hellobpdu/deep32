#!/usr/bin/env python3
"""Complete C8 for a retained Windows Docker Desktop Forge installation."""

import argparse
import base64
from contextlib import contextmanager
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

GPU_PACK = Path(__file__).resolve().parents[1] / 'gpu-pack'
SCHEMA = 'deep32-forge-docker-role/1'
HEX = re.compile('[0-9a-f]{64}')
STATUS_FILE = 'windows-pair'
# Use the retained C8 implementation. Windows requires an exclusive listener
# so that a second process cannot reuse the consented loopback endpoint.
EXCLUSIVE_HELPER = """
import socket
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from deep32net import cli, files, pairing

class ExclusiveSocket:
    def __getattr__(self, name):
        return getattr(socket, name)

    def socket(self, family, kind):
        if (family, kind) != (socket.AF_INET, socket.SOCK_STREAM):
            raise OSError('unexpected pairing listener')
        listener = socket.socket(family, kind)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        except OSError:
            listener.close()
            raise
        return listener

class InheritedPath(type(Path())):
    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        # Python 3.13+ gives mode 0700 special Windows ACL semantics.
        # These directories already inherit the verified owner/System DACL.
        return super().mkdir(parents=parents, exist_ok=exist_ok)

pairing.socket = ExclusiveSocket()
files.Path = InheritedPath
raise SystemExit(cli.helper_main(['pair-forge']))
"""


class Refusal(ValueError):
    """A protected pairing precondition is unavailable."""


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Refusal('Restore the signed Windows pairing adapter and its runtime files.')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def plain(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
            raise Refusal('Use a direct local Forge path without links or reparse points.')
    return path


def inspect_path(path, directory=False):
    info = plain(path).lstat()
    if directory:
        good = stat.S_ISDIR(info.st_mode)
    else:
        good = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not good:
        raise Refusal('Restore the ordinary protected Forge state.')
    return info


def restrict_acl(security):
    validate = security.validate_acl

    def owners_only(actual, owner, directory=False):
        validate(actual, owner, directory)
        if ';BA)' in actual:
            raise ValueError('the pairing ACL must be owner and System only')

    security.validate_acl = owners_only
    return security.windows_security


def protected(acl, path, directory=False):
    inspect_path(path, directory)
    acl(path, verify=True, directory=directory)


def decode_object(raw):
    def unique(pairs):
        value = dict(pairs)
        if len(pairs) != len(value):
            raise Refusal('Restore the unambiguous Forge pairing evidence.')
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise Refusal('Restore the Forge pairing evidence.')
    return value


def read_json(path):
    if inspect_path(path).st_size > 65536:
        raise Refusal('Restore the bounded Forge installation records.')
    return decode_object(path.read_bytes())


def environment():
    return {key: value for key, value in os.environ.items()
            if key.upper() not in ('TS_SOCKET', 'DEEP32_OPENSSL', 'OPENSSL_CONF', 'OPENSSL_MODULES')}


def read_status(tailscale, *args, run=subprocess.run):
    result = run([tailscale, *args, '--json'], check=True, timeout=8, env=environment(),
                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if len(result.stdout) > 1048576:
        raise Refusal('Restore bounded local Tailscale observations.')
    return decode_object(result.stdout)


def published(root, install, publication, acl, tailscale, run):
    config = root / 'config'
    protected(acl, config, directory=True)

    class ReadOnlyState:
        Refusal = ValueError
        safe_path = staticmethod(plain)

        @staticmethod
        def windows_security(path, verify=True):
            protected(acl, path)

        @staticmethod
        def read_json(path):
            return read_json(path)

    status = read_status(tailscale, 'status', run=run)
    current, own = status.get('CurrentTailnet'), status.get('Self')
    if (status.get('BackendState') != 'Running' or not isinstance(current, dict)
            or current.get('MagicDNSEnabled') is not True or not isinstance(own, dict)
            or own.get('Online') is not True or not isinstance(own.get('DNSName'), str)):
        raise Refusal('Use the qualified owning Windows Tailscale device.')
    host = own['DNSName'].rstrip('.')
    settings = install['settings']
    wanted = publication.plan(host, settings)
    # The sealed status() supplies the same bounded route schema and normalizer.
    # Bind it to the explicit verified Windows client.
    def command(argv, _reason, _fix, _timeout):
        if argv != ['tailscale.exe', 'serve', 'status', '--json']:
            raise Refusal('Only observation of existing Forge routes is allowed.')
        return json.dumps(read_status(tailscale, 'serve', 'status', run=run))

    client = publication.Publication(ReadOnlyState(), root, 'windows', command)
    saved = client.load()
    if (not saved or saved['phase'] != 'ready' or saved['install_id'] != install['install_id']
            or saved['host'] != host or saved['routes'] != wanted
            or sorted(saved['added']) != [0, 1, 2]
            or saved['pending'] is not None or saved['removing'] is not None):
        raise Refusal('Restore the same ready Forge publication before pairing.')
    observed = client.status()
    if any(client.slice(observed, row) != client.expected(host, row) for row in wanted):
        raise Refusal('Restore the exact owned Forge model and pairing routes before pairing.')
    return publication.ports(settings)['pairing'], publication.endpoints(host, settings)


@contextmanager
def lifecycle_lock(root, acl):
    import msvcrt

    lock_path = root / 'lifecycle.lock'
    protected(acl, lock_path)
    with lock_path.open('r+b') as stream:
        if os.fstat(stream.fileno()).st_size != 1:
            raise Refusal('Restore the private Forge lifecycle lock.')
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise Refusal('The Forge lifecycle is busy. Retry when it finishes.') from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def install_context(root, acl, runtime):
    for name in ('owner.json', 'install.json'):
        protected(acl, root / name)
    owner = read_json(root / 'owner.json')
    install = read_json(root / 'install.json')
    install_id = hashlib.sha256(str(root).encode('utf-8')).hexdigest()[:16]
    if (owner != {'schema': SCHEMA, 'install_id': install_id}
            or install.get('schema') != SCHEMA or install.get('install_id') != install_id
            or install.get('platform') != 'windows'
            or install.get('stage') != 'published-pairing-required'
            or not isinstance(install.get('release'), str) or HEX.fullmatch(install['release']) is None
            or not isinstance(install.get('settings'), dict)
            or install['settings'].get('transport') != 'tailnet'
            or install['settings'].get('publish') != 'serve-https'
            or install['settings'].get('forge_accept_ct') is not True):
        raise Refusal('Use the original published Windows Forge data directory.')
    release = root / 'releases' / install['release']
    protected(acl, release, directory=True)
    runtime.verify(release, expected_release=install['release'])
    return install, release / 'kit'


def trusted_pairing(kit, template=GPU_PACK / 'pairing'):
    package = template / 'deep32net'
    names = [Path('bin/pairing-helper'), *(Path('deep32net') / path.name for path in sorted(package.glob('*.py')))]
    if not (package / 'cli.py').is_file():
        raise Refusal('Restore the signed C8 runtime files with the adapter.')
    for name in names:
        expected, retained = template / name, kit / 'pairing' / name
        left, right = inspect_path(expected), inspect_path(retained)
        if left.st_size != right.st_size or expected.read_bytes() != retained.read_bytes():
            raise Refusal('Retained C8 runtime differs from the signed Windows pairing adapter.')
    return template


def state_guard(root, acl):
    state = root / 'pairing'
    if not state.exists():
        plain(state).mkdir()
        acl(state, directory=True)
    protected(acl, state, directory=True)
    pending = [state]
    while pending:
        for child in pending.pop().iterdir():
            directory = stat.S_ISDIR(plain(child).lstat().st_mode)
            protected(acl, child, directory=directory)
            if directory:
                pending.append(child)
    return state


def committed(state, modules):
    peers = modules.store.list_peers(state)
    if not peers:
        return False
    claim = state / 'pairing/forge-joined.pairlock'
    identity = state / 'identity'
    if not identity.is_dir() or not claim.is_file() or modules.pairlock.load(claim).in_progress:
        raise Refusal('The protected C8 commit is incomplete.')
    own = modules.identity.ensure_identity(state, 'forge')
    for row in peers:
        if (row['kind'], row['side'], row['peer_role'], row['own_pin'], row['transport'], row['publish']) != (
                'forge', 'joined', 'workstation', own.pin, 'tailnet', 'serve-https'):
            raise Refusal('The retained C8 peer record differs from this Forge.')
        if (row['peer_pin'] == own.pin or row['received'] or
                row['given'] != ['forge-token', 'kb-public.pem', 'model-endpoints.json']):
            raise Refusal('The retained C8 handover differs from this Forge.')
    return True


def status_write(destination, code, acl):
    with tempfile.NamedTemporaryFile(prefix='.windows-pair-', dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        acl(temporary)
        with temporary.open('wb') as stream:
            stream.write(code.encode('ascii') + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        if destination.exists():
            raise Refusal('Clear the previous protected pairing window before retrying.')
        temporary.rename(destination)
        protected(acl, destination)
    finally:
        temporary.unlink(missing_ok=True)


def clear_window(root, acl):
    status = root / 'status'
    protected(acl, status, directory=True)
    window = status / STATUS_FILE
    if plain(window).exists():
        protected(acl, window)
        window.unlink()  # Singleton lifecycle lock proves the earlier window is obsolete.
    return window


def respond(root, kit, state, port, endpoints, openssl, acl, *, popen=subprocess.Popen,
            clock=time.monotonic, sleep=time.sleep, emit=print):
    window = clear_window(root, acl)
    private = root / 'private'
    protected(acl, private, directory=True)
    token, public = private / 'forge-token', private / 'kb-public.pem'
    for path in (token, public):
        protected(acl, path)
    with tempfile.TemporaryDirectory(prefix='.c8-', dir=window.parent) as name:
        scratch = Path(name)
        acl(scratch, directory=True)
        ready = scratch / 'listener.json'
        code = base64.b32encode(secrets.token_bytes(7)).decode('ascii')[:10]
        request = dict(code=code, expires_seconds=300, attempts=1, form='docker', transport='tailnet',
                       publish='serve-https', token_path=str(token), kb_public_path=str(public), state_dir=str(state),
                       port=port, ready_file=str(ready), openssl=openssl, model_endpoints=endpoints)
        child = popen([sys.executable, '-I', '-B', '-c', EXCLUSIVE_HELPER, str(kit)],
                      stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      env=environment())
        try:
            with child.stdin as pipe:
                pipe.write((json.dumps(request, ensure_ascii=True) + '\n').encode('ascii'))
            started = clock()
            while child.poll() is None and not plain(ready).exists() and clock() - started < 125:
                sleep(0.1)
            if child.poll() is not None or not ready.exists():
                raise Refusal('The Forge pairing listener could not open. Check the port and lockout.')
            protected(acl, ready)
            report = read_json(ready)
            if (set(report) != {'port', 'pin'} or report['port'] != port or
                    not isinstance(report['pin'], str) or HEX.fullmatch(report['pin']) is None):
                raise Refusal('The Forge pairing listener returned invalid readiness.')
            status_write(window, code, acl)
            emit('The protected Windows Forge pairing window is ready.')
            if child.wait(timeout=305) != 0:
                raise Refusal('The Forge pairing window ended before a durable C8 commit.')
        finally:
            try:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
            finally:
                if plain(window).exists():
                    protected(acl, window)
                    window.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--data-root', required=True, type=Path)
    args = parser.parse_args(argv)
    if os.name != 'nt' or not ctypes.windll.shell32.IsUserAnAdmin():
        parser.exit(10, 'Run Windows Forge pairing as its installation administrator.\n')
    try:
        root = args.data_root
        if not root.is_absolute() or not str(root).isascii() or root == Path(root.anchor) or '..' in root.parts:
            raise Refusal('Supply the original direct local Forge data directory.')
        if str(root).startswith(('\\\\', '//')):
            raise Refusal('Use a direct local Forge data directory.')
        root = plain(root)
        security = module('_deep32_c8_win_security', GPU_PACK / 'forms/host_security.py')
        runtime = module('_deep32_c8_win_runtime', GPU_PACK / 'forms/installed_runtime.py')
        publication = module('_deep32_c8_win_publication', GPU_PACK / 'forms/forge_publication.py')
        acl = restrict_acl(security)
        protected(acl, root, directory=True)
        with lifecycle_lock(root, acl):
            install, kit = install_context(root, acl, runtime)
            pairing = trusted_pairing(kit)
            clear_window(root, acl)
            openssl = shutil.which('openssl.exe')
            tailscale = shutil.which('tailscale.exe') or str(Path(os.environ['ProgramFiles']) / 'Tailscale/tailscale.exe')
            if not openssl:
                raise Refusal('Use the release-qualified native OpenSSL executable.')
            inspect_path(Path(openssl))
            inspect_path(Path(tailscale))
            port, endpoints = published(root, install, publication, acl, tailscale, subprocess.run)
            state = state_guard(root, acl)
            sys.path.insert(0, str(pairing))
            from deep32net import identity, pairlock, store
            modules = SimpleNamespace(identity=identity, pairlock=pairlock, store=store)
            if not committed(state, modules):
                respond(root, pairing, state, port, endpoints, openssl, acl)
                state_guard(root, acl)
                if not committed(state, modules):
                    raise Refusal('No complete protected C8 commit was observed.')
        print('Windows Forge C8 pairing is committed.')
        return 0
    except Refusal as error:
        parser.exit(10, str(error) + '\n')
    except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError, subprocess.SubprocessError):
        parser.exit(10, 'Windows Forge pairing could not verify its private state and owning routes.\n')


if __name__ == '__main__':
    raise SystemExit(main())
