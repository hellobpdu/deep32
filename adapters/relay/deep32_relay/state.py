"""Private, installation-owned state. No discovery or ordinary agent-home access."""
import contextlib
import ctypes
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time

MAX_FILE = 1024 * 1024
OWNER_SCHEMA = 'deep32.relay-owner.v1'


class StateError(Exception):
    """A fixed reason code; never include the path or underlying exception."""
    def __init__(self, reason='unsafe-state'):
        self.reason = reason
        super().__init__(reason)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise StateError('invalid-json')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(StateError('invalid-json')))
    except (ValueError, UnicodeError, RecursionError):
        raise StateError('invalid-json') from None


def json_bytes(value):
    try:
        return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                           separators=(',', ':')) + '\n').encode('ascii')
    except (ValueError, TypeError, RecursionError):
        raise StateError('invalid-json') from None


def safe_path(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise StateError('linked-state')
    return path


def _open_read(path):
    if os.name != 'nt':
        return os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    # The CRT's default sharing mode can deny atomic replacement while a
    # doctor is reading. Explicit SHARE_DELETE allows old readers to finish
    # their immutable snapshot while the next complete snapshot is published.
    from ctypes import wintypes as w
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise StateError('state-open-failed')
    class Attributes(ctypes.Structure):
        _fields_ = [('attributes', w.DWORD), ('tag', w.DWORD)]
    info = Attributes()
    if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel.CloseHandle(handle)
        raise StateError('state-open-failed')
    if info.attributes & 0x400:
        kernel.CloseHandle(handle)
        raise StateError('linked-state')
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


class PosixSecurity:
    def mkdir(self, path):
        os.mkdir(path, 0o700)

    def verify(self, path, directory=False):
        info = path.lstat()
        expected = 0o700 if directory else 0o600
        if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != expected or
                not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
                (not directory and info.st_nlink != 1)):
            raise StateError('unsafe-permissions')

    def protect_file(self, path):
        os.chmod(path, 0o600)


class WindowsSecurity:
    """Native current-user-only ACL creation and passive ACL verification.

    DPAPI values, when used by the link owner, remain a separate layer. ACLs do
    not confine an agent running with the same user token.
    """
    def __init__(self):
        if os.name != 'nt':
            raise StateError('platform-unavailable')
        from ctypes import wintypes as w
        self.w = w
        self.adv = ctypes.WinDLL('advapi32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p
        self.kernel.GetCurrentProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        self.adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
        self.adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        self.adv.GetNamedSecurityInfoW.argtypes = [w.LPCWSTR, ctypes.c_int, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.adv.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.adv.GetAce.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        self.adv.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p]
        token, needed = w.HANDLE(), w.DWORD()
        if not self.adv.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise StateError('acl-unavailable')
        try:
            self.adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            self.user = ctypes.create_string_buffer(needed.value)
            if not self.adv.GetTokenInformation(token, 1, self.user, needed, ctypes.byref(needed)):
                raise StateError('acl-unavailable')
            self.sid = ctypes.c_void_p.from_buffer(self.user).value
            output = ctypes.c_void_p()
            if not self.adv.ConvertSidToStringSidW(self.sid, ctypes.byref(output)):
                raise StateError('acl-unavailable')
            try:
                self.sid_text = ctypes.wstring_at(output)
            finally:
                self.kernel.LocalFree(output)
        finally:
            self.kernel.CloseHandle(token)

    @contextlib.contextmanager
    def descriptor(self, inherit=False):
        output = ctypes.c_void_p()
        flags = 'OICI' if inherit else ''
        sddl = 'O:' + self.sid_text + 'D:P(A;' + flags + ';FA;;;' + self.sid_text + ')'
        if not self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(output), None):
            raise StateError('acl-unavailable')
        try:
            yield output
        finally:
            self.kernel.LocalFree(output)

    def mkdir(self, path):
        class Attributes(ctypes.Structure):
            _fields_ = [('length', self.w.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', self.w.BOOL)]
        self.kernel.CreateDirectoryW.argtypes = [self.w.LPCWSTR, ctypes.POINTER(Attributes)]
        with self.descriptor(inherit=True) as sd:
            attributes = Attributes(ctypes.sizeof(Attributes), sd, False)
            if not self.kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                raise StateError('directory-create-failed')

    def protect_file(self, path):
        with self.descriptor() as sd:
            if not self.adv.SetFileSecurityW(str(path), 0x80000005, sd):
                raise StateError('acl-unavailable')

    def verify(self, path, directory=False):
        info = path.lstat()
        if (getattr(info, 'st_file_attributes', 0) & 0x400 or
                not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
                (not directory and info.st_nlink != 1)):
            raise StateError('unsafe-state')
        owner, acl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        if self.adv.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                         ctypes.byref(acl), None, ctypes.byref(descriptor)):
            raise StateError('acl-unavailable')
        try:
            if not owner or not acl or not self.adv.EqualSid(owner, self.sid):
                raise StateError('unsafe-permissions')
            # ACL header: revision, padding, size, ACE count, padding.
            count = ctypes.c_ushort.from_address(acl.value + 4).value
            if count == 0:
                raise StateError('unsafe-permissions')
            for index in range(count):
                ace = ctypes.c_void_p()
                if not self.adv.GetAce(acl, index, ctypes.byref(ace)):
                    raise StateError('acl-unavailable')
                kind = ctypes.c_ubyte.from_address(ace.value).value
                # Only ordinary allow ACEs for the exact current user are accepted.
                if kind != 0 or not self.adv.EqualSid(ctypes.c_void_p(ace.value + 8), self.sid):
                    raise StateError('unsafe-permissions')
        finally:
            self.kernel.LocalFree(descriptor)


class OwnedRoot:
    def __init__(self, root, security=None):
        if not Path(root).is_absolute():
            raise StateError('absolute-root-required')
        self.root = safe_path(root)
        self.security = security or (WindowsSecurity() if os.name == 'nt' else PosixSecurity())

    def path(self, relative):
        relative = Path(relative)
        if (relative.is_absolute() or not relative.parts or
                any(part in ('.', '..') or ':' in part or '\\' in part for part in relative.parts)):
            raise StateError('invalid-state-path')
        path = safe_path(self.root / relative)
        if self.root not in path.parents:
            raise StateError('invalid-state-path')
        self.security.verify(self.root, directory=True)
        for parent in reversed(path.parent.parents):
            if parent == self.root or self.root in parent.parents:
                self.security.verify(parent, directory=True)
        if path.parent != self.root:
            self.security.verify(path.parent, directory=True)
        return path

    def create(self):
        if self.root.exists():
            self.security.verify(self.root, directory=True)
            return False
        missing = []
        cursor = self.root
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        safe_path(cursor)
        for directory in reversed(missing):
            self.security.mkdir(directory)
            self.security.verify(directory, directory=True)
        return True

    def mkdir(self, relative):
        relative = Path(relative)
        if (relative.is_absolute() or not relative.parts or
                any(part in ('.', '..') or ':' in part or '\\' in part for part in relative.parts)):
            raise StateError('invalid-state-path')
        target = self.root / relative
        safe_path(target)
        for current in reversed((target, *target.parents)):
            if self.root == current or self.root in current.parents:
                if not current.exists():
                    self.security.mkdir(current)
                self.security.verify(current, directory=True)
        return self.path(relative)

    def read(self, relative, maximum=MAX_FILE):
        path = self.path(relative)
        self.security.verify(path)
        before = path.lstat()
        with os.fdopen(_open_read(path), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum or
                    (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
                raise StateError('unsafe-state')
            raw = stream.read(maximum + 1)
            after = self.path(relative).lstat()
            if len(raw) > maximum or (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
                raise StateError('state-changed')
            self.security.verify(path)
            return raw

    def read_json(self, relative, maximum=MAX_FILE):
        return strict_json(self.read(relative, maximum))

    def write(self, relative, raw):
        if type(raw) is not bytes or len(raw) > MAX_FILE:
            raise StateError('state-too-large')
        path = self.path(relative)
        if path.exists():
            self.security.verify(path)
        temporary = path.with_name('.stage-' + secrets.token_hex(16))
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                   getattr(os, 'O_BINARY', 0), 0o600), 'wb') as stream:
                self.security.protect_file(temporary)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self.path(relative)
            # Windows metadata scanners can briefly hold a handle without
            # SHARE_DELETE. Retry only this bounded sharing failure; every
            # attempt revalidates the owned destination before replacing it.
            for attempt in range(100):
                self.path(relative)
                if path.exists():
                    self.security.verify(path)
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if os.name != 'nt' or attempt == 99:
                        raise
                    time.sleep(0.005)
            self.security.verify(path)
            if os.name == 'posix':
                descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary.exists():
                safe_path(temporary).unlink()

    def write_json(self, relative, value):
        self.write(relative, json_bytes(value))

    def validate(self, install_id=None):
        owner = self.read_json('owner.json', 1024)
        if (type(owner) is not dict or set(owner) != {'schema', 'install_id'} or
                owner['schema'] != OWNER_SCHEMA or not valid_id(owner['install_id']) or
                (install_id is not None and owner['install_id'] != install_id)):
            raise StateError('ownership-mismatch')
        return owner['install_id']

    @contextlib.contextmanager
    def lock(self, relative='operation.lock'):
        if relative not in ('operation.lock', 'service.lock'):
            raise StateError('invalid-lock')
        path = self.path(relative)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o600)
        except FileExistsError:
            pass
        else:
            try:
                self.security.protect_file(path)
                os.write(descriptor, b'\0')
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        self.security.verify(path)
        descriptor = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        locked = False
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 1:
                raise StateError('unsafe-state')
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                raise StateError('operation-pending') from None
            yield
        finally:
            if locked:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def valid_id(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{32}', value) is not None


def valid_hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None
