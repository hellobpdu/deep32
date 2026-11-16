"""Durable file operations for C2, C3, C4, C5 and deep32-import-report.

Public: ImportFailure, Interrupted, atomic_write, open_regular, copy_hashed,
sync, free_bytes, set_owner_mode, set_tree_owner_mode, remove_tree, crash,
signal_point, check_signal, install_signal_handlers, fault_matches and log.
"""

import errno
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import stat

import imp_paths


RESUMABLE = frozenset(('IMPORT_NO_DISC', 'IMPORT_MOUNT', 'IMPORT_READ',
                       'IMPORT_SPACE', 'IMPORT_WRITE', 'IMPORT_SWITCH',
                       'IMPORT_INTERNAL'))
_SIGNALLED = False
_SIGNALS_IGNORED = False
_USED_WRITE_FAULTS = set()


class ImportFailure(Exception):
    def __init__(self, code, stage=None, unit=None, path=None, args=None, resumable=None):
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.unit = unit
        self.path = path
        self.arguments = dict(args or {})
        self.error_args = self.arguments
        self.resumable = code in RESUMABLE if resumable is None else bool(resumable)


class Interrupted(Exception):
    """A signal was observed at a point where durable state is consistent."""


def signal_handler(signum=None, frame=None):
    global _SIGNALLED
    _SIGNALLED = True


def install_signal_handlers():
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, signal_handler)


def ignore_signals(value=True):
    global _SIGNALS_IGNORED
    _SIGNALS_IGNORED = bool(value)


def check_signal():
    if _SIGNALLED and not _SIGNALS_IGNORED:
        raise Interrupted()


def signal_point(point):
    if imp_paths.hooks().get('D32_TEST_SIGNAL_AT') == point:
        signal_handler()
    check_signal()


def crash(point):
    if imp_paths.hooks().get('D32_TEST_CRASH') == point:
        os._exit(70)
    signal_point(point)


def _relative_disc(path):
    value = str(path).replace('\\', '/')
    for prefix in ('kb/', 'corpus/', 'library/', 'code/'):
        offset = value.find('/' + prefix)
        if offset >= 0:
            return value[offset + 1:]
        if value.startswith(prefix):
            return value
    return value


def fault_matches(kind, path):
    value = str(path).replace('\\', '/').strip('/')
    for entry in imp_paths.hooks().get('faults', ()):
        label, separator, wanted = entry.partition(':')
        wanted = wanted.replace('\\', '/').strip('/')
        if separator and label == kind and (value == wanted or value.startswith(wanted + '/')):
            return True
    return False


def internal_fault(stage):
    if fault_matches('internal', stage):
        raise RuntimeError('test fault')


def _write_fault(path, content_root=None):
    value = Path(path)
    if content_root is not None:
        try:
            relative = value.relative_to(Path(content_root)).as_posix()
        except ValueError:
            relative = value.as_posix()
    else:
        relative = value.as_posix()
        for marker in ('/d32/libcontent/', '/var/lib/deep32/content/'):
            if marker in relative:
                relative = relative.split(marker, 1)[1]
                break
    for entry in imp_paths.hooks().get('faults', ()):
        if entry in _USED_WRITE_FAULTS or not entry.startswith('write:'):
            continue
        wanted = entry[6:].strip('/')
        if relative == wanted or relative.startswith(wanted + '/'):
            _USED_WRITE_FAULTS.add(entry)
            raise OSError(errno.EIO, 'write fault')


def fsync_directory(path):
    if hasattr(os, 'O_DIRECTORY'):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def atomic_write(path, data, mode, owner='root', group=None):
    """Write a complete sibling, fsync it, replace, then fsync its directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    if isinstance(data, str):
        data = data.encode('ascii')
    try:
        if temporary.exists() or temporary.is_symlink():
            if not stat.S_ISREG(temporary.lstat().st_mode):
                raise OSError(errno.EINVAL, 'unsafe temporary file')
            temporary.unlink()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if not imp_paths.test_mode():
                set_owner_mode(temporary, owner, group or 'wheel', mode)
            os.replace(temporary, path)
            # The ledger records the installed name, not an atomic-write scratch name.
            if imp_paths.test_mode():
                set_owner_mode(path, owner, group or 'root', mode)
            fsync_directory(path.parent)
        except BaseException:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
            raise
    except OSError as exc:
        raise ImportFailure('IMPORT_WRITE') from exc


def open_regular(path):
    """Open a regular file without following a final symbolic link."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError(errno.EINVAL, 'not a regular file')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OSError(errno.EINVAL, 'file changed while opening')
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def copy_hashed(src, dst, expected_size, expected_sha256, on_bytes=None, *,
                stage='kb', unit=None, disc_path=None, content_root=None):
    """Copy and check a member using exclusive creation and bounded chunks."""
    source_name = disc_path if disc_path is not None else _relative_disc(src)
    destination = Path(dst)
    checksum = hashlib.sha256()
    total = 0
    try:
        if fault_matches('read', source_name):
            raise OSError(errno.EIO, 'read fault')
        source = open_regular(src)
    except FileNotFoundError as exc:
        raise ImportFailure('IMPORT_FILE_MISSING', stage, unit, source_name) from exc
    except OSError as exc:
        raise ImportFailure('IMPORT_READ', stage, unit, source_name) from exc
    try:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _write_fault(destination, content_root)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                 getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
            target = os.fdopen(descriptor, 'wb')
        except OSError as exc:
            raise ImportFailure('IMPORT_WRITE', stage, unit, source_name) from exc
        try:
            with target:
                while True:
                    check_signal()
                    try:
                        chunk = source.read(1048576)
                    except OSError as exc:
                        raise ImportFailure('IMPORT_READ', stage, unit, source_name) from exc
                    if not chunk:
                        break
                    try:
                        _write_fault(destination, content_root)
                        target.write(chunk)
                    except OSError as exc:
                        raise ImportFailure('IMPORT_WRITE', stage, unit, source_name) from exc
                    checksum.update(chunk)
                    total += len(chunk)
                    if on_bytes is not None:
                        on_bytes(len(chunk))
                try:
                    target.flush()
                    os.fsync(target.fileno())
                except OSError as exc:
                    raise ImportFailure('IMPORT_WRITE', stage, unit, source_name) from exc
            if total != expected_size:
                raise ImportFailure('IMPORT_FILE_SIZE', stage, unit, source_name,
                                    {'expected': expected_size, 'found': total})
            digest = checksum.hexdigest()
            if digest != expected_sha256:
                raise ImportFailure('IMPORT_FILE_HASH', stage, unit, source_name)
            return digest
        except BaseException:
            if destination.exists() and not destination.is_symlink():
                destination.unlink()
            raise
    finally:
        source.close()


def sync():
    function = getattr(os, 'sync', None)
    if function is not None:
        function()


def free_bytes(path):
    override = imp_paths.hooks().get('D32_TEST_FREE_BYTES', '')
    if override:
        return max(0, int(override))
    return shutil.disk_usage(path).free


def set_owner_mode(path, owner, group, mode):
    path = Path(path)
    if imp_paths.test_mode():
        root = Path(imp_paths.hooks()['D32_ROOT'])
        relative = path.relative_to(root).as_posix()
        root.mkdir(parents=True, exist_ok=True)
        with (root / 'owners.tsv').open('a', encoding='ascii', newline='\n') as stream:
            stream.write('%s\t%s\t%s\t%04o\n' % (relative, owner, group, mode))
        return
    try:
        import grp
        gid = grp.getgrnam(group).gr_gid
        os.chown(path, 0, gid)
        os.chmod(path, mode)
    except (KeyError, OSError) as exc:
        raise ImportFailure('IMPORT_WRITE') from exc


def set_tree_owner_mode(folder, owner, group, dmode, fmode):
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise ImportFailure('IMPORT_WRITE')
    files = []
    directories = [folder]
    for base, dirs, names in os.walk(folder, followlinks=False):
        dirs.sort()
        names.sort()
        for name in dirs:
            path = Path(base) / name
            if path.is_symlink():
                raise ImportFailure('IMPORT_WRITE')
            directories.append(path)
        for name in names:
            path = Path(base) / name
            if path.is_symlink() or not path.is_file():
                raise ImportFailure('IMPORT_WRITE')
            files.append(path)
    for path in files:
        set_owner_mode(path, owner, group, fmode)
    for path in sorted(directories, key=lambda p: (-len(p.parts), str(p))):
        set_owner_mode(path, owner, group, dmode)


def remove_tree(path):
    """Remove only a caller-proven owned tree; links are unlinked, not followed."""
    path = Path(path)
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        if imp_paths.test_mode():
            root = Path(imp_paths.hooks()['D32_ROOT']).resolve()
            try:
                path.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError('removal outside test root') from exc
        shutil.rmtree(path)


def log(paths, import_id, event, **fields):
    allowed = ('start', 'lock-taken', 'busy', 'reconcile', 'stage-start', 'stage-done',
               'stage-failed', 'unit', 'pointer', 'rollback', 'service', 'child',
               'c6-refused', 'error', 'warning', 'stopped', 'finish')
    if event not in allowed:
        raise ValueError('unknown log event')
    pieces = [imp_paths.iso(), str(import_id or '-'), event]
    for key, value in fields.items():
        if not re.fullmatch('[a-z_]{1,32}', key):
            raise ValueError('invalid log key')
        clean = str(value).encode('ascii', 'replace').decode('ascii')
        clean = ''.join(c if 32 <= ord(c) <= 126 and c != '=' else '_' for c in clean)
        pieces.append(key + '=' + clean.replace(' ', '_')[:256])
    line = (' '.join(pieces))[:1023] + '\n'
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    with paths.log.open('a', encoding='ascii', newline='\n') as stream:
        stream.write(line)
        stream.flush()
    set_owner_mode(paths.log, 'root', 'wheel' if paths.node == 'workstation' else 'root', 0o600)
