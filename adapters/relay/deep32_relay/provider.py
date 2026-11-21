"""R15 approved dependency lock and installation-owned virtual environment.

Installation is an explicit connector action. Import/status never runs pip.
Only exact recorded regular files are removed; private identity/pins survive.
"""
import hashlib
import importlib.metadata
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from .state import StateError, safe_path, strict_json

PACKAGE = Path(__file__).resolve().parents[1]
LOCK = PACKAGE / 'tls-provider-lock.json'
PACKAGES = {'cffi': '2.1.1', 'cryptography': '50.0.2', 'pyOpenSSL': '26.4.0',
            'pycparser': '3.12', 'typing-extensions': '4.16.0'}
RECEIPT = 'private/provider-install.json'


def runtime_packages():
    # These markers come from the approved universal requirements lock.
    return {name: version for name, version in PACKAGES.items()
            if name != 'typing-extensions' or sys.version_info < (3, 13)}


def approved_lock(lockfile=LOCK):
    path = safe_path(lockfile)
    record = strict_json(path.read_bytes())
    if (type(record) is not dict or record.get('schema') != 'deep32.relay-tls-lock.v2'
            or record.get('approval') != 'R15' or record.get('status') != 'approved'
            or record.get('packages') != PACKAGES or record.get('requirements') != 'requirements.lock'
            or record.get('hashes') != 165):
        raise StateError('tls-provider-unqualified')
    requirements = safe_path(path.parent / 'requirements.lock')
    raw = requirements.read_bytes()
    if (hashlib.sha256(raw).hexdigest() != record.get('requirements_sha256')
            or len(re.findall(rb'--hash=sha256:[a-f0-9]{64}', raw)) != 165):
        raise StateError('tls-provider-lock-mismatch')
    return record, requirements


def readiness(lockfile=LOCK):
    try:
        approved_lock(lockfile)
        return all(importlib.metadata.version(name) == version for name, version in runtime_packages().items())
    except (OSError, ValueError, TypeError, AttributeError, StateError,
            importlib.metadata.PackageNotFoundError):
        return False


def _files(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        info = path.lstat()
        # CPython venv creates this compatibility alias on 64-bit Linux even
        # with --copies. No other link or out-of-root target is accepted.
        if stat.S_ISLNK(info.st_mode) and path == directory / 'lib64' and os.readlink(path) == 'lib':
            safe_path(directory)
            result['lib64'] = 'symlink:lib'
            continue
        safe_path(path)
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise StateError('unsafe-provider-file')
        result[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _run(argv):
    # No inherited pip credentials, indexes, Python hooks or user configuration.
    env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC', 'PATH',
                                           'TEMP', 'TMP', 'TMPDIR') if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1', PIP_CONFIG_FILE=os.devnull,
               PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_INPUT='1')
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, timeout=300, check=False)
    if result.returncode:
        raise StateError('tls-provider-install-failed')


def install_provider(owner, python=None, *, run=_run, lockfile=LOCK):
    """Create with copies (no venv symlinks) and install only hashed wheels.

    Offline tests inject run; the operator's explicit install resolves approved
    wheels using pip. No dependencies are downloaded at import or status.
    """
    record, requirements = approved_lock(lockfile)
    owner.validate()
    owner.mkdir('private')
    directory = owner.path('provider')
    executable = directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if owner.path(RECEIPT).exists():
        receipt = owner.read_json(RECEIPT, 4 * 1024 * 1024)
        if (type(receipt) is not dict or receipt.get('schema') != 'deep32.relay-provider.v1'
                or type(receipt.get('complete')) is not bool
                or receipt.get('lock_sha256') != record['requirements_sha256']):
            raise StateError('modified-provider-install')
        if receipt['complete']:
            if receipt.get('files') != _files(directory):
                raise StateError('modified-provider-install')
            return str(executable)
        # Failed installs record every created file. Recover only those exact
        # unchanged files, read removal back, then retry within this owned root.
        if remove_provider(owner):
            raise StateError('provider-cleanup-incomplete')
    if directory.exists() and next(directory.iterdir(), None) is not None:
        raise StateError('unrecorded-provider-install')
    owner.mkdir('provider')
    owner.mkdir('private')
    owner.write_json(RECEIPT, {'schema': 'deep32.relay-provider.v1',
        'lock_sha256': record['requirements_sha256'], 'files': {}, 'complete': False})
    try:
        run([str(python or Path(sys.executable).resolve()), '-I', '-B', '-m', 'venv', '--copies', str(directory)])
        run([str(executable), '-I', '-B', '-m', 'pip', 'install', '--require-hashes',
             '--only-binary', ':all:', '--no-input', '--no-cache-dir', '--no-compile',
             '-r', str(requirements)])
        run([str(executable), '-I', '-B', '-c',
             'import importlib.metadata as m; expected=' + repr(runtime_packages()) +
             '; assert all(m.version(k)==v for k,v in expected.items())'])
    except BaseException:
        owner.write_json(RECEIPT, {'schema': 'deep32.relay-provider.v1',
            'lock_sha256': record['requirements_sha256'], 'files': _files(directory), 'complete': False})
        raise
    owner.write_json(RECEIPT, {'schema': 'deep32.relay-provider.v1',
        'lock_sha256': record['requirements_sha256'], 'files': _files(directory), 'complete': True})
    return str(executable)


def remove_provider(owner, *, details=None):
    if not owner.path(RECEIPT).exists():
        return []
    receipt = owner.read_json(RECEIPT, 4 * 1024 * 1024)
    if type(receipt) is not dict or receipt.get('schema') != 'deep32.relay-provider.v1' or type(receipt.get('files')) is not dict:
        raise StateError('invalid-provider-receipt')
    directory = owner.path('provider')
    if directory.exists():
        owner.security.verify(directory, directory=True)
    for relative, digest in receipt['files'].items():
        parts = Path(relative) if isinstance(relative, str) else None
        if (parts is None or not parts.parts or parts.is_absolute() or
                any(part in ('.', '..') or ':' in part or '\\' in part for part in parts.parts) or
                parts.as_posix() != relative or
                not (isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest) or
                     relative == 'lib64' and digest == 'symlink:lib')):
            raise StateError('invalid-provider-receipt')
    actual = _files(directory)
    if any(name not in receipt['files'] or receipt['files'][name] != digest for name, digest in actual.items()):
        raise StateError('modified-provider-install')
    remaining = []
    failure_codes = []
    def failed(relative, code):
        remaining.append(relative)
        failure_codes.append(code)
    for relative in sorted(receipt['files'], reverse=True):
        if relative not in actual:
            continue
        path = directory / relative
        try:
            # The provider root is private. Venv's inner directories normally
            # have mode 0755, so do not apply OwnedRoot's 0700 parent policy to
            # those generated paths. Keep no-link and exact-content checks.
            owner.path('provider')
            if relative == 'lib64' and receipt['files'][relative] == 'symlink:lib':
                if not path.is_symlink() or os.readlink(path) != 'lib':
                    raise StateError('modified-provider-install')
            else:
                path = safe_path(path)
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                        hashlib.sha256(path.read_bytes()).hexdigest() != receipt['files'][relative]):
                    raise StateError('modified-provider-install')
            if path.exists() or path.is_symlink():
                path.unlink()
            if path.exists() or path.is_symlink():
                failed('provider/' + relative, 'provider-unlink-unconfirmed')
        except FileNotFoundError:
            if path.exists() or path.is_symlink():
                failed('provider/' + relative, 'provider-unlink-unconfirmed')
        except PermissionError:
            failed('provider/' + relative, 'local-permission-denied')
        except OSError:
            failed('provider/' + relative, 'provider-unlink-failed')
        except StateError as exc:
            failed('provider/' + relative, exc.reason)
    if not remaining:
        for path in sorted((p for p in directory.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                safe_path(path).rmdir()
            except PermissionError:
                failed('provider/' + path.relative_to(directory).as_posix(), 'local-permission-denied')
            except OSError:
                failed('provider/' + path.relative_to(directory).as_posix(), 'provider-directory-remove-failed')
        if not remaining:
            try:
                if directory.exists(): directory.rmdir()
                if directory.exists():
                    failed('provider', 'provider-removal-unconfirmed')
            except PermissionError:
                failed('provider', 'local-permission-denied')
            except OSError:
                failed('provider', 'provider-directory-remove-failed')
        if not remaining:
            try:
                owner.path(RECEIPT).unlink()
                if (owner.root / RECEIPT).exists():
                    failed(RECEIPT, 'provider-removal-unconfirmed')
            except PermissionError:
                failed(RECEIPT, 'local-permission-denied')
            except OSError:
                failed(RECEIPT, 'provider-receipt-remove-failed')
    if remaining and details is not None:
        details.update(code=sorted(set(failure_codes))[0], paths=sorted(set(remaining)))
    return remaining
