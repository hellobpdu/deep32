"""One-file whole-image transport, Python 3.10+, standard library only.

Callers MUST authenticate the signed root, bind the raw MIRRORS bytes and apply
the mirrors contract before passing a selected disc here. This module is not a
metadata trust bypass. The command-line entry point derives Disc 3 payloads from authenticated read-only mounts.

An injected opener receives (url, headers, connect_timeout=30, read_timeout=60)
and returns status, headers.get_all(name), raw encoded-body read(n), and close().
It must not follow redirects or retry. FileSystem methods are narrow test seams.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import subprocess
import tarfile
import tempfile
import threading
from urllib.parse import urljoin, urlsplit

BUFFER = 1024 * 1024
CHUNK_BYTES = 64 * 1024 * 1024
RESERVE = 16 * 1024 * 1024
MAX_SIZE = 512 * 1024 * 1024 * 1024
MAX_REDIRECTS = 5
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 60

_spec = importlib.util.spec_from_file_location(
    "_deep32_download_contracts", Path(__file__).with_name("contract_validate.py"))
_contracts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contracts)


class DownloadError(Exception):
    """A refusal safe for a caller to display; exit status is 1."""


class LocalError(DownloadError):
    """A local failure for which another mirror cannot help."""


class TransferError(DownloadError):
    def __init__(self, message, attempts, torrent_url):
        super().__init__(message)
        self.attempts = attempts
        self.torrent_url = torrent_url


class _MirrorFailure(Exception):
    pass


class _IgnoredRange(_MirrorFailure):
    pass


def _disc(disc):
    """Defensive transport invariants, after the caller's complete contract."""
    try:
        _contracts.safe_path(disc["file"], basename=True)
        size, chunk = disc["size"], disc["chunk_bytes"]
        if type(size) is not int or not 0 < size <= MAX_SIZE or chunk != CHUNK_BYTES or type(chunk) is not int:
            raise ValueError
        hashes, mirrors = disc["chunk_sha256"], disc["mirrors"]
        if (not isinstance(hashes, list) or len(hashes) != (size + chunk - 1) // chunk or
                not all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in [disc["sha256"], *hashes]) or
                not isinstance(mirrors, list) or not 1 <= len(mirrors) <= 16):
            raise ValueError
        ids, urls = set(), set()
        for mirror in mirrors:
            if not isinstance(mirror["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", mirror["id"]):
                raise ValueError
            _contracts.safe_url(mirror["url"])
            if mirror["id"] in ids or mirror["url"] in urls:
                raise ValueError
            ids.add(mirror["id"])
            urls.add(mirror["url"])
    except (KeyError, TypeError, ValueError, _contracts.ContractError):
        raise DownloadError("An authenticated, contract-validated whole-image descriptor is required") from None
    return disc


def _identity(info):
    return info.st_dev, info.st_ino


def _regular(info, *, links=1):
    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or
            info.st_nlink != links):
        raise LocalError("Links, reparse points, special files and unexpected hard links are refused")


def _windows_caller(*, require_unelevated=False):
    """Return the local owner SID, applying the online transfer privilege rule when requested."""
    import ctypes
    from ctypes import wintypes
    adv, kernel = ctypes.WinDLL("advapi32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    token, needed = wintypes.HANDLE(), wintypes.DWORD()
    sid_text = wintypes.LPWSTR()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise OSError
        if require_unelevated:
            elevated = wintypes.DWORD()
            if not adv.GetTokenInformation(token, 20, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(needed)):
                raise OSError
            if elevated.value:
                raise LocalError("Run the download as an unelevated user in a private local directory")
        needed.value = 0
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise OSError
        data = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, data, needed, ctypes.byref(needed)):
            raise OSError
        sid = ctypes.cast(data, ctypes.POINTER(pointer))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError
        return sid_text.value
    except OSError:
        raise LocalError("Cannot inspect the current Windows download owner") from None
    finally:
        if token:
            kernel.CloseHandle(token)
        if sid_text:
            kernel.LocalFree(sid_text)


def _windows_private(folder):
    """Inspect owner and DACL without changing them, including for local verification."""
    import ctypes
    from ctypes import wintypes
    adv, kernel = ctypes.WinDLL("advapi32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [pointer, wintypes.DWORD,
        wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), pointer]
    kernel.LocalFree.argtypes = [pointer]
    owner, descriptor, dacl = pointer(), pointer(), pointer()
    sddl = wintypes.LPWSTR()
    caller = _windows_caller()
    try:
        if adv.GetNamedSecurityInfoW(str(folder), 1, 5, ctypes.byref(owner), None,
                ctypes.byref(dacl), None, ctypes.byref(descriptor)) or not dacl:
            raise OSError
        if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 5, ctypes.byref(sddl), None):
            raise OSError
        _private_sddl(sddl.value, caller)
    except OSError:
        raise LocalError("Cannot inspect destination ownership and ACL; choose a private local directory") from None
    finally:
        for allocation in (sddl, descriptor):
            if allocation:
                kernel.LocalFree(allocation)


def _private_sddl(sddl, owner):
    prefix, sep, dacl = sddl.partition("D:")
    if not sep or prefix != "O:" + owner or not dacl or "NO_ACCESS_CONTROL" in dacl:
        raise LocalError("Destination must be owned by the current user and have a private ACL")
    aces = re.findall(r"\(([^()]*)\)", dacl)
    if not aces:
        raise LocalError("Cannot verify destination ACL; choose a private local directory")
    for ace in aces:
        fields = ace.split(";")
        if len(fields) != 6 or fields[0] not in {"A", "D"}:
            raise LocalError("Cannot verify destination ACL; choose a private local directory")
        kind, _, rights, _, _, sid = fields
        if kind == "D" or sid in {owner, "SY", "BA", "S-1-5-18", "S-1-5-32-544"}:
            continue
        # Read/execute/synchronise are harmless; write/delete/ownership rights
        # granted to another principal make this an unsafe download directory.
        if rights.startswith("0x"):
            try:
                writable = int(rights, 16) & (0x10000000 | 0x40000000 | 0x000D0156)
            except ValueError:
                writable = True
        else:
            writable = not rights or bool(re.sub(r"FR|FX|GR|GX|RC|SD", "", rights)) or "SD" in rights
        if writable:
            raise LocalError("Destination permits other users to write; choose a private local directory")


class FileSystem:
    """Filesystem seam. Production checks remain enabled unless explicitly faked."""
    def path(self, value, *, directory=False, links=1):
        value = Path(os.path.abspath(value))
        if os.name == "nt" and str(value).startswith(("\\\\", "//")):
            raise LocalError("Network paths are refused; choose a local destination")
        for item in (*reversed(value.parents), value):
            try:
                info = item.lstat()
            except FileNotFoundError:
                if item != value:
                    raise LocalError("Destination parent is missing; create a private local directory") from None
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise LocalError("Links and reparse points are refused")
            if (item != value or directory) and not stat.S_ISDIR(info.st_mode):
                raise LocalError("A destination component is not a directory")
            if item == value and not directory:
                _regular(info, links=links)
        if directory and not value.is_dir():
            raise LocalError("Destination is missing; create a private local directory")
        return value

    def private_directory(self, value):
        value = self.path(value, directory=True)
        if os.name == "nt":
            _windows_private(value)
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
            if kernel.GetDriveTypeW(value.anchor) not in {2, 3, 6}:
                raise LocalError("Choose a private directory on a local filesystem")
        else:
            info = value.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise LocalError("Destination must be user-owned and private; use mode 0700")
        return value

    def open_fd(self, value, flags, *, new=False, writable=True):
        if os.name != "nt":
            return os.open(value, flags, 0o600)
        # CRT os.open omits FILE_SHARE_DELETE. Keep this verified handle open
        # during hard-link publication and removal of the partial name.
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        access = 0x80000000 | (0x40000000 if writable else 0)
        handle = kernel.CreateFileW(str(value), access, 1 | 2 | 4, None, 1 if new else 3,
                                    0x80 | 0x00200000 | 0x08000000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return msvcrt.open_osfhandle(handle, (os.O_RDWR if writable else os.O_RDONLY) | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise

    def open_payload(self, value, *, create=False, writable=True, links=1):
        value = self.path(value, links=links)
        before = value.lstat() if value.exists() else None
        flags = os.O_RDWR if writable else os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if before is None:
            if not create:
                raise LocalError("Required local image is missing: " + value.name)
            flags |= os.O_CREAT | os.O_EXCL
        fd = self.open_fd(value, flags, new=before is None, writable=writable)
        try:
            opened = os.fstat(fd)
            _regular(opened, links=links)
            if before is not None and _identity(before) != _identity(opened):
                raise LocalError("Local file changed while being opened; retry after removing the unsafe file")
            if os.name != "nt" and (opened.st_uid != os.geteuid() or opened.st_mode & 0o022) and writable:
                raise LocalError("Partial file must be user-owned and private")
            stream = os.fdopen(fd, "r+b" if writable else "rb", buffering=0)
            fd = None
            try:
                self.recheck(value, stream, links=links)
            except BaseException:
                stream.close()
                raise
            return stream
        finally:
            if fd is not None:
                os.close(fd)

    def recheck(self, value, handle, *, links=1):
        value = self.path(value, links=links)
        opened, named = os.fstat(handle.fileno()), value.lstat()
        _regular(opened, links=links)
        _regular(named, links=links)
        if _identity(opened) != _identity(named):
            raise LocalError("Local image identity changed; publication refused")
        return opened

    @contextlib.contextmanager
    def lock(self, value):
        with self.open_payload(value, create=True) as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise LocalError("Another download holds this image lock, or locking is unavailable; retry when it has finished") from None
            try:
                self.recheck(value, handle)
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def capability(self, folder, size):
        # The probe is a few bytes, never an image-sized scratch file.
        with tempfile.TemporaryDirectory(prefix=".deep32-filesystem-", dir=folder) as name:
            source, link = Path(name) / "source", Path(name) / "link"
            source.write_bytes(b"Deep32 publication probe\n")
            try:
                os.link(source, link)
                try:
                    os.link(source, link)
                except FileExistsError:
                    pass
                else:
                    raise LocalError("Filesystem does not support atomic no-overwrite publication")
            except OSError:
                raise LocalError("Filesystem cannot publish without overwrite; choose a local hard-link-capable destination") from None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                wintypes.LPWSTR, wintypes.DWORD]
            fsname = ctypes.create_unicode_buffer(64)
            if not kernel.GetVolumeInformationW(str(Path(folder).anchor), None, 0, None, None, None, fsname, len(fsname)):
                raise LocalError("Cannot determine destination file-size capacity; choose another local filesystem")
            if fsname.value.upper() in {"FAT", "FAT32"} and size > 0xFFFFFFFF:
                raise LocalError("Image exceeds this filesystem's file-size limit; choose a larger-file filesystem")
        else:
            import resource
            limit = resource.getrlimit(resource.RLIMIT_FSIZE)[0]
            if limit != resource.RLIM_INFINITY and size > limit:
                raise LocalError("Image exceeds the local file-size limit; raise that limit or choose another destination")

    def available(self, folder):
        return shutil.disk_usage(folder).free

    def truncate(self, handle, size):
        handle.truncate(size)
        handle.seek(size)

    def sync(self, handle):
        handle.flush()
        os.fsync(handle.fileno())

    def publish(self, partial, final, handle):
        self.recheck(partial, handle)
        try:
            os.link(partial, final)
        except FileExistsError:
            raise LocalError("Final image appeared during transfer; it was not overwritten: " + final.name) from None
        self.recheck(partial, handle, links=2)
        self.recheck(final, handle, links=2)
        os.unlink(partial)
        self.recheck(final, handle)
        # Directory fsync is unavailable on Windows; the payload fsync above is
        # mandatory. On POSIX, persist the name change as well.
        if os.name != "nt":
            fd = os.open(final.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def recover_publication(self, partial, final, disc):
        """Repair only the exact two-name state left by a kill during publish."""
        first, second = self.path(partial, links=2), self.path(final, links=2)
        if _identity(first.lstat()) != _identity(second.lstat()):
            raise LocalError("Unexpected hard links on local image; publication recovery refused")
        with self.open_payload(final, writable=False, links=2) as handle:
            if not _verify_handle(handle, disc):
                raise LocalError("Interrupted publication has corrupt bytes; remove unsafe local files explicitly")
            self.recheck(partial, handle, links=2)
            self.recheck(final, handle, links=2)
            os.unlink(partial)
            self.recheck(final, handle)


class _HTTPResponse:
    def __init__(self, connection, response):
        self.connection, self.response = connection, response
        self.status, self.headers = response.status, response.headers
        self.body = response.fp

    def read(self, size):
        # Bypass HTTPResponse's length clipping: with Connection: close, one
        # bounded read beyond the expected object must detect surplus bytes.
        return self.body.read(size)

    def close(self):
        self.response.close()
        self.connection.close()


def open_http(url, headers, *, connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT):
    """Direct transport: normal TLS, no environment proxies/auth/cookies/retries."""
    if os.name == "nt":
        _windows_caller(require_unelevated=True)
    parsed = urlsplit(_contracts.safe_url(url))
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    options = {"timeout": connect_timeout}
    if parsed.scheme == "https":
        options["context"] = ssl.create_default_context()
    connection = cls(parsed.hostname, parsed.port, **options)
    try:
        connection.connect()
        connection.sock.settimeout(read_timeout)
        connection.request("GET", parsed.path, headers=headers)
        return _HTTPResponse(connection, connection.getresponse())
    except BaseException:
        connection.close()
        raise


def _headers(response, name):
    values = response.headers.get_all(name) or []
    if any(not isinstance(v, str) or len(v) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in v) for v in values):
        raise _MirrorFailure("invalid response headers")
    return values


def _one(response, name):
    values = _headers(response, name)
    if len(values) > 1:
        raise _MirrorFailure("ambiguous response headers")
    return values[0].strip() if values else None


def _open_response(mirror, offset, opener):
    url, visited = mirror["url"], set()
    headers = {"Accept-Encoding": "identity", "Connection": "close"}
    if offset:
        headers["Range"] = "bytes=%d-" % offset
    for hop in range(MAX_REDIRECTS + 1):
        visited.add(url)
        try:
            response = opener(url, dict(headers), connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT)
        except (OSError, http.client.HTTPException):
            raise _MirrorFailure("connection, TLS or timeout failure") from None
        if response.status not in {301, 302, 303, 307, 308}:
            return response, urlsplit(url).hostname
        try:
            location = _one(response, "Location")
            if not location or hop == MAX_REDIRECTS:
                raise _MirrorFailure("redirect limit or missing target")
            target = urljoin(url, location)
            try:
                _contracts.safe_url(target)
            except _contracts.ContractError:
                raise _MirrorFailure("unsafe redirect target") from None
            if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
                raise _MirrorFailure("HTTPS redirect downgrade refused")
            if target in visited:
                raise _MirrorFailure("redirect loop refused")
        finally:
            response.close()
        url = target
    raise _MirrorFailure("redirect limit")


def _response_headers(response, offset, size):
    if type(response.status) is not int or not 100 <= response.status <= 599:
        raise _MirrorFailure("invalid HTTP status")
    if response.status == 200 and offset:
        raise _IgnoredRange("Range ignored; retry the same command to restart this mirror at zero")
    if response.status not in {200, 206}:
        raise _MirrorFailure("HTTP status %d" % response.status)
    content_range = _one(response, "Content-Range")
    if response.status == 206:
        if content_range != "bytes %d-%d/%d" % (offset, size - 1, size):
            raise _MirrorFailure("incorrect or multiple response range")
    elif content_range is not None:
        raise _MirrorFailure("unexpected response range")
    length, transfer = _one(response, "Content-Length"), _one(response, "Transfer-Encoding")
    if length is not None and (not re.fullmatch(r"[0-9]+", length) or len(length) > 20 or int(length) != size - offset):
        raise _MirrorFailure("incorrect response length")
    if transfer is not None and (transfer.lower() != "chunked" or length is not None):
        raise _MirrorFailure("ambiguous or unsupported response framing")
    encoding, content_type = _one(response, "Content-Encoding"), _one(response, "Content-Type")
    if encoding is not None and encoding.lower() != "identity":
        raise _MirrorFailure("encoded response refused")
    if content_type is not None and content_type.lower().startswith("multipart/"):
        raise _MirrorFailure("multipart response refused")
    return transfer is not None


class _Body:
    """Bounded strict chunked decoder over the raw standard-library stream."""
    def __init__(self, response, chunked):
        self.response, self.chunked = response, chunked
        self.remaining, self.ended = 0, False

    def _raw(self, count):
        try:
            block = self.response.read(count)
        except (OSError, http.client.HTTPException, EOFError):
            raise _MirrorFailure("body interrupted or stalled") from None
        if not isinstance(block, bytes) or len(block) > count:
            raise _MirrorFailure("invalid body reader")
        return block

    def _exact(self, count):
        result = b""
        while len(result) < count:
            block = self._raw(count - len(result))
            if not block:
                raise _MirrorFailure("truncated chunked framing")
            result += block
        return result

    def _line(self, limit=4096):
        line = bytearray()
        while len(line) < limit:
            byte = self._raw(1)
            if not byte:
                raise _MirrorFailure("truncated chunked framing")
            line.extend(byte)
            if byte == b"\n":
                if not line.endswith(b"\r\n"):
                    raise _MirrorFailure("invalid chunked line ending")
                return bytes(line)
        raise _MirrorFailure("chunked framing exceeds limit")

    def read(self, count):
        if not self.chunked:
            return self._raw(count)
        if self.ended:
            return b""
        if not self.remaining:
            line = self._line()
            # RFC token/quoted-string extensions, with no controls or escapes
            # outside a quoted value. Unknown extensions are ignored as data.
            token = rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
            value = rb'(?:' + token + rb'|"(?:[\x20-\x21\x23-\x5b\x5d-\x7e]|\\[\x20-\x7e])*")'
            if not re.fullmatch(rb"[0-9A-Fa-f]{1,16}(?:;" + token + rb"(?:=" + value + rb")?)*\r\n", line):
                raise _MirrorFailure("invalid chunked size")
            self.remaining = int(line.split(b";", 1)[0].strip(), 16)
            if not self.remaining:
                total = 0
                for _ in range(65):
                    trailer = self._line()
                    total += len(trailer)
                    if total > 16384:
                        raise _MirrorFailure("chunked trailers exceed limit")
                    if trailer == b"\r\n":
                        if self._raw(1):
                            raise _MirrorFailure("excess body bytes")
                        self.ended = True
                        return b""
                    if not re.fullmatch(token + rb":[\x20-\x7e]*\r\n", trailer):
                        raise _MirrorFailure("invalid chunked trailer")
                    if trailer.split(b":", 1)[0].lower() in {b"content-length", b"transfer-encoding", b"content-range", b"content-encoding"}:
                        raise _MirrorFailure("framing trailer refused")
                raise _MirrorFailure("too many chunked trailers")
        block = self._raw(min(count, self.remaining))
        if not block:
            raise _MirrorFailure("truncated chunked body")
        self.remaining -= len(block)
        if not self.remaining and self._exact(2) != b"\r\n":
            raise _MirrorFailure("invalid chunked boundary")
        return block


def _verify_handle(handle, disc):
    before = os.fstat(handle.fileno())
    if before.st_size != disc["size"]:
        return False
    handle.seek(0)
    digest, remaining = hashlib.sha256(), disc["size"]
    while remaining:
        block = handle.read(min(BUFFER, remaining))
        if not block:
            return False
        remaining -= len(block)
        digest.update(block)
    after = os.fstat(handle.fileno())
    return (not handle.read(1) and _identity(before) == _identity(after) and
            (before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and digest.hexdigest() == disc["sha256"])


def verify_local_image(disc, image, *, filesystem=None):
    """Verify read-only media in place: no lock, scratch, copy or network."""
    disc, fs = _disc(disc), filesystem or FileSystem()
    try:
        image = fs.path(image)
        if image.name != disc["file"]:
            raise LocalError("Local image basename disagrees with the authenticated descriptor")
        with fs.open_payload(image, writable=False) as handle:
            if not _verify_handle(handle, disc):
                raise LocalError("Local image size or SHA-256 failed: " + image.name)
            fs.recheck(image, handle)
        return str(image)
    except OSError:
        raise LocalError("Cannot read the local image; check local file access and retry") from None


def resume_prefix(handle, disc, *, filesystem=None):
    """Re-read signed chunks from zero, discarding the first bad/incomplete tail."""
    disc, fs = _disc(disc), filesystem or FileSystem()
    length = os.fstat(handle.fileno()).st_size
    if length > disc["size"]:
        raise LocalError("Partial exceeds its signed size; remove that local file explicitly to retry")
    handle.seek(0)
    prefix = 0
    try:
        for expected in disc["chunk_sha256"]:
            chunk_size = min(disc["chunk_bytes"], disc["size"] - prefix)
            if length - prefix < chunk_size:
                break
            digest, remaining = hashlib.sha256(), chunk_size
            while remaining:
                block = handle.read(min(BUFFER, remaining))
                if not block:
                    raise LocalError("Partial changed during prefix verification; retry after checking local storage")
                remaining -= len(block)
                digest.update(block)
            if digest.hexdigest() != expected:
                break
            prefix += chunk_size
    except KeyboardInterrupt:
        fs.truncate(handle, prefix)
        raise
    fs.truncate(handle, prefix)
    return prefix


def whole_storage_plan(disc, destination, prefix=0, *, filesystem=None):
    disc, fs = _disc(disc), filesystem or FileSystem()
    if type(prefix) is not int or not 0 <= prefix <= disc["size"]:
        raise LocalError("Invalid authenticated prefix for storage planning")
    destination = fs.path(destination, directory=True)
    needed, available = disc["size"] - prefix + RESERVE, fs.available(destination)
    return {"schema": "deep32.whole-storage.v1", "scope": "download-only", "path": str(destination),
            "filesystem": str(destination.stat().st_dev), "validated_prefix": prefix,
            "payload_bytes": disc["size"] - prefix, "reserve_bytes": RESERVE,
            "needed": needed, "available": available, "ready": available >= needed}


def _budget(disc, folder, prefix, fs):
    if not whole_storage_plan(disc, folder, prefix, filesystem=fs)["ready"]:
        raise LocalError("Insufficient local storage; free space for the remaining image plus 16 MiB and retry")


def _stream(response, chunked, handle, disc, prefix, fs, boundary):
    body, offset = _Body(response, chunked), prefix
    while offset < disc["size"]:
        index = offset // disc["chunk_bytes"]
        length = min(disc["chunk_bytes"], disc["size"] - offset)
        digest, remaining = hashlib.sha256(), length
        while remaining:
            block = body.read(min(BUFFER, remaining))
            if not block:
                raise _MirrorFailure("short response body")
            digest.update(block)
            view = memoryview(block)
            while view:
                count = handle.write(view)
                if not count:
                    raise LocalError("Local image write failed; check free space and permissions")
                view = view[count:]
            remaining -= len(block)
            offset += len(block)
        if digest.hexdigest() != disc["chunk_sha256"][index]:
            raise _MirrorFailure("signed chunk SHA-256 failed")
        # The last chunk is reusable only after response framing is complete.
        if offset < disc["size"]:
            boundary[0] = offset
    if body.read(1):
        raise _MirrorFailure("excess response body")
    boundary[0] = offset


def fetch_whole(disc, destination, *, release, revision, fingerprint, torrent_url,
                opener=None, filesystem=None):
    """Fetch one authenticated disc; publish one verified inode without overwrite.

    Returns a report mapping. TransferError carries bounded per-mirror attempts
    and the optional manual torrent URL. LocalError stops without mirror retry.
    KeyboardInterrupt propagates after discarding the unauthenticated tail.
    """
    if os.name == "nt" and filesystem is None:
        _windows_caller(require_unelevated=True)
    disc, fs, opener = _disc(disc), filesystem or FileSystem(), opener or open_http
    if (not isinstance(release, str) or not re.fullmatch(r"v[0-9a-z][0-9a-z.-]{0,79}", release) or
            type(revision) is not int or revision < 1 or not isinstance(fingerprint, str) or
            not re.fullmatch(r"(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})", fingerprint)):
        raise DownloadError("Authenticated release, revision and expected fingerprint are required")
    try:
        _contracts.safe_url(torrent_url)
    except _contracts.ContractError:
        raise DownloadError("A validated signed torrent URL is required") from None
    attempts, contradictory = [], False
    try:
        folder = fs.private_directory(destination)
        folder_identity = _identity(folder.stat())
        final, partial = folder / disc["file"], folder / (disc["file"] + ".download")

        def directory_check():
            if _identity(fs.private_directory(folder).stat()) != folder_identity:
                raise LocalError("Destination directory identity changed; retry after checking local storage")

        def result(status, mirror=None, host=None, resumed=0):
            if mirror is None:
                message = "Verified local image %s." % disc["file"]
            elif resumed:
                message = "Completed from mirror %s (%s); resumed verified bytes from earlier mirrors." % (mirror["id"], host)
            else:
                message = "Verified %s from mirror %s (%s)." % (disc["file"], mirror["id"], host)
            return {"path": str(final), "file": disc["file"], "status": status,
                    "mirror_id": mirror["id"] if mirror else None, "host": host,
                    "resumed_bytes": resumed, "attempts": attempts, "message": message,
                    "torrent_url": torrent_url}

        with fs.lock(folder / ("." + disc["file"] + ".lock")):
            directory_check()
            if final.exists() or final.is_symlink():
                if final.lstat().st_nlink == 2 and partial.exists():
                    fs.recover_publication(partial, final, disc)
                with fs.open_payload(final, writable=False) as handle:
                    if not _verify_handle(handle, disc):
                        raise LocalError("Final image is corrupt; remove it explicitly to retry: " + disc["file"])
                    fs.recheck(final, handle)
                    directory_check()
                return result("local")
            # Refuse unsafe partials and capacity before the first HTTP request.
            with fs.open_payload(partial, create=True) as handle:
                boundary, recovered = [0], False
                try:
                    fs.recheck(partial, handle)
                    boundary[0] = resume_prefix(handle, disc, filesystem=fs)
                    recovered = True
                    fs.recheck(partial, handle)
                    fs.capability(folder, disc["size"])
                    if boundary[0] == disc["size"]:
                        if _verify_handle(handle, disc):
                            fs.sync(handle)
                            fs.recheck(partial, handle)
                            directory_check()
                            fs.publish(partial, final, handle)
                            return result("local")
                        contradictory = True
                        boundary[0] = 0
                        fs.truncate(handle, 0)
                    _budget(disc, folder, boundary[0], fs)
                    for mirror in disc["mirrors"]:
                        directory_check()
                        fs.recheck(partial, handle)
                        _budget(disc, folder, boundary[0], fs)
                        offset, response = boundary[0], None
                        host = urlsplit(mirror["url"]).hostname
                        try:
                            response, host = _open_response(mirror, offset, opener)
                            chunked = _response_headers(response, offset, disc["size"])
                            handle.seek(offset)
                            _stream(response, chunked, handle, disc, offset, fs, boundary)
                            fs.sync(handle)
                            fs.recheck(partial, handle)
                            if not _verify_handle(handle, disc):
                                contradictory = True
                                boundary[0] = 0
                                raise _MirrorFailure("whole SHA-256 failed despite signed chunks; descriptor or local modification")
                            fs.recheck(partial, handle)
                            directory_check()
                            fs.publish(partial, final, handle)
                            attempts.append({"id": mirror["id"], "host": host, "reason": "verified"})
                            return result("downloaded", mirror, host, offset)
                        except _IgnoredRange as error:
                            boundary[0] = 0
                            attempts.append({"id": mirror["id"], "host": host, "reason": str(error)})
                            fs.truncate(handle, 0)
                            _budget(disc, folder, 0, fs)
                        except _MirrorFailure as error:
                            attempts.append({"id": mirror["id"], "host": host, "reason": str(error)})
                            fs.truncate(handle, boundary[0])
                        finally:
                            if response is not None:
                                response.close()
                    detail = "; ".join("%s (%s): %s" % (a["id"], a["host"], a["reason"]) for a in attempts)
                    if contradictory:
                        detail += ("; possible inconsistent signed descriptor or local modification; release %s, index revision %d, "
                                   "fingerprint %s; consistently contradictory descriptors need corrected re-signed metadata" %
                                   (release, revision, fingerprint.upper()))
                    raise TransferError("All mirrors failed: " + detail + ". Optional manual torrent: " + torrent_url,
                                        attempts, torrent_url)
                except (KeyboardInterrupt, OSError, LocalError):
                    # Preserve signed-valid chunks even on cancellation/ENOSPC.
                    # If local truncation is itself impossible, the next call's
                    # complete prefix scan remains the recovery authority.
                    if recovered:
                        try:
                            fs.recheck(partial, handle)
                            fs.truncate(handle, boundary[0])
                        except (OSError, LocalError):
                            pass
                    raise
    except OSError:
        raise LocalError("Local filesystem operation failed; check free space, access, file-size limits and locking, then retry") from None


GPU_CLASSES = ('turing', 'ampere80', 'ampere86', 'ada', 'hopper', 'blackwell100', 'blackwell120')
INDEX = 'images/index.json'
MANIFEST = 'PAYLOAD.json'
SUMS = 'PAYLOAD.SHA256'
RUNTIME = 'RUNTIME.json'
COMPONENT_FORMAT = 'deep32-runtime-stage/1'
JOINED_FORMAT = 'deep32-joined-runtime/1'
RECEIPT_KEYS = {
    'allowlist_sha256', 'component', 'format', 'gpu_class', 'payload_sha256',
    'pins_sha256', 'roles', 'source_date_epoch', 'source_payload_sha256',
}
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
ARCHIVE = re.compile(r'images/[A-Za-z0-9._-]+\.tar\Z')
REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
RAW_METADATA = ('pins.json', 'SHA256SUMS', 'deep32/' + MANIFEST, 'deep32/' + SUMS,
                'deep32/' + RUNTIME, 'deep32/payload/' + INDEX)
RAW_METADATA_LIMIT = 8 * 1024 * 1024
RAW_KIT_LIMIT = 16 * 1024 * 1024
RAW_PAYLOAD = 'deep32/payload/'
RAW_KIT_ROOTS = {'build', 'containers', 'firstboot', 'forms', 'import', 'licenses', 'pairing', 'release'}
# Root-level core kit files: the pin register and the Apache-2.0 LICENSE and NOTICE for Deep32's own code.
RAW_KIT_FILES = {'pins.json', 'LICENSE', 'NOTICE'}
RAW_UNUSED_FORMS = {'forms/forge-docker.tar', 'forms/forge-docker.files.json'}


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('ascii')


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field in Disc 3 metadata.')
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError('Invalid number in Disc 3 metadata.')

    return json.loads(raw.decode('ascii'), object_pairs_hook=unique, parse_constant=invalid)


def is_digest(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def safe_member(name):
    if (not isinstance(name, str) or not name or not name.isascii()
            or any(ord(char) < 32 or ord(char) == 127 or char in '\\:<>"|?*' for char in name)):
        raise ValueError('Unsafe relative path in Disc 3 payload.')
    for part in name.split('/'):
        stem = part.split('.', 1)[0].upper()
        if (not part or part.endswith((' ', '.')) or stem in {'CON', 'PRN', 'AUX', 'NUL'}
                or re.fullmatch(r'(?:COM|LPT)[1-9]', stem)):
            raise ValueError('Unsafe relative path in Disc 3 payload.')
    return name


def require_type(path, info, *, directory=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or getattr(info, 'st_file_attributes', 0) & REPARSE
            or not directory and info.st_nlink != 1):
        raise ValueError('A linked or non-regular Disc 3 path was refused: ' + str(path))


def plain_directory(path, *, missing=False):
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('Disc 3 paths cannot contain parent traversal.')
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if not missing:
                raise
        else:
            require_type(part, info, directory=True)
    return path


def overlaps(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns)


@contextlib.contextmanager
def checked_reader(path):
    # The getters keep the authenticated component images mounted read-only.
    before = path.lstat()
    require_type(path, before)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require_type(path, opened)
        if file_identity(before) != file_identity(opened):
            raise ValueError('Disc 3 input changed before it was read: ' + str(path))
        yield stream
        if file_identity(opened) != file_identity(os.fstat(stream.fileno())):
            raise ValueError('Disc 3 input changed while it was read: ' + str(path))


def read_bytes(path):
    with checked_reader(path) as stream:
        return stream.read()


def inventory(raw):
    rows = read_json(raw)
    if not isinstance(rows, list) or not rows:
        raise ValueError('Disc 3 payload inventory must be a non-empty list.')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256'}:
            raise ValueError('Invalid Disc 3 payload inventory record.')
        name = safe_member(row['path'])
        if (name in result or type(row['size']) is not int or row['size'] < 0
                or not is_digest(row['sha256'])):
            raise ValueError('Duplicate or invalid Disc 3 payload inventory record: ' + name)
        result[name] = row
    if list(result) != sorted(result):
        raise ValueError('Disc 3 payload inventory is not sorted.')
    return result


def checksum_list(rows):
    return ''.join(row['sha256'] + '  ' + row['path'] + '\n' for row in rows).encode('ascii')


def register_paths(rows, members):
    for name in rows:
        parts = name.split('/')
        for count in range(1, len(parts) + 1):
            part = '/'.join(parts[:count])
            entry = (part, count < len(parts))
            if members.setdefault(part.casefold(), entry) != entry:
                raise ValueError('Conflicting or case-aliased Disc 3 payload path: ' + part)


def check_tree(root, rows):
    remaining = set(rows)
    directories = {name[:index] for name in rows for index, char in enumerate(name) if char == '/'}

    def visit(directory, prefix):
        for path in directory.iterdir():
            name = safe_member(prefix + path.name)
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                require_type(path, info, directory=True)
                if name not in directories:
                    raise ValueError('Unexpected Disc 3 payload directory: ' + name)
                visit(path, name + '/')
            else:
                require_type(path, info)
                if name not in remaining or rows[name]['size'] != info.st_size:
                    raise ValueError('Unexpected or wrong-sized Disc 3 payload member: ' + name)
                remaining.remove(name)

    visit(root, '')
    if remaining:
        raise ValueError('Missing Disc 3 payload member: ' + min(remaining))


def validate_index(raw, rows, roles):
    if INDEX not in rows or rows[INDEX]['sha256'] != sha256(raw):
        raise ValueError('Disc 3 image index differs from its payload inventory.')
    images = read_json(raw)
    if not isinstance(images, list):
        raise ValueError('Disc 3 image index must be a list.')
    selected, archives = {}, set()
    for row in images:
        if not isinstance(row, dict):
            raise ValueError('Invalid Disc 3 image index record.')
        role, name = row.get('role'), row.get('path')
        if (not isinstance(role, str) or role in selected or not isinstance(name, str)
                or ARCHIVE.fullmatch(name) is None or name == INDEX or name in archives
                or name not in rows or row.get('sha256') != rows[name]['sha256']):
            raise ValueError('Duplicate, invalid or unverified Disc 3 image archive record.')
        selected[role] = row
        archives.add(name)
    if set(selected) != set(roles) or [row['role'] for row in images] != roles:
        raise ValueError('Disc 3 image index does not match the expected component roles.')
    if set(name for name in rows if name.startswith('images/')) != archives | {INDEX}:
        raise ValueError('Disc 3 payload contains an unindexed image member.')
    return images


def component_metadata(read, kind, gpu_class, roles):
    pins = read('pins.json')
    if not isinstance(read_json(pins), dict):
        raise ValueError('Disc 3 pins must be a JSON object.')
    receipt_raw = read('deep32/' + RUNTIME)
    receipt = read_json(receipt_raw)
    inventory_raw = read('deep32/' + MANIFEST)
    if (not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS
            or receipt['format'] != COMPONENT_FORMAT or receipt['component'] != kind
            or receipt['gpu_class'] != gpu_class or receipt['roles'] != roles
            or type(receipt['source_date_epoch']) is not int or receipt['source_date_epoch'] < 0
            or receipt['pins_sha256'] != sha256(pins)
            or receipt['payload_sha256'] != sha256(inventory_raw)
            or not is_digest(receipt['source_payload_sha256'])
            or not is_digest(receipt['allowlist_sha256'])):
        raise ValueError('Disc 3 receipt does not match the required ' + kind + ' component.')
    rows = inventory(inventory_raw)
    register_paths(rows, {})
    if read('deep32/' + SUMS) != checksum_list(rows.values()):
        raise ValueError('Disc 3 checksum list differs from its payload inventory.')
    images = validate_index(read(RAW_PAYLOAD + INDEX), rows, roles)
    name = kind if gpu_class is None else kind + '-' + gpu_class
    evidence = {'component': name, 'receipt_sha256': sha256(receipt_raw), 'receipt': receipt}
    return rows, images, pins, evidence


def component(mount, kind, gpu_class, roles):
    media = plain_directory(mount / 'deep32')
    payload = plain_directory(media / 'payload')
    plain_directory(payload / 'images')
    metadata = component_metadata(lambda name: read_bytes(mount / name), kind, gpu_class, roles)
    check_tree(payload, metadata[0])
    return payload, *metadata


def join_metadata(parts, gpu_class):
    paths, images, archives, files = {}, {}, {}, {}
    first = parts[0][4]['receipt']
    for _payload, rows, image_rows, pins, evidence in parts:
        current = evidence['receipt']
        if (pins != parts[0][3] or current['source_date_epoch'] != first['source_date_epoch']
                or current['source_payload_sha256'] != first['source_payload_sha256']):
            raise ValueError('Disc 3 components do not share the same source stage and pins.')
        register_paths(rows, paths)
        for name, row in rows.items():
            if name != INDEX and files.setdefault(name, row) != row:
                raise ValueError('Disc 3 components disagree on payload member: ' + name)
        for row in image_rows:
            role, name = row['role'], row['path']
            if images.setdefault(role, row) != row or archives.setdefault(name, row) != row:
                raise ValueError('Disc 3 components disagree on an image index record.')
    roles = sorted(images)
    index = canonical([images[role] for role in roles])
    files[INDEX] = {'path': INDEX, 'size': len(index), 'sha256': sha256(index)}
    rows = sorted(files.values(), key=lambda row: row['path'])
    manifest = canonical(rows)
    receipt = canonical({
        'format': JOINED_FORMAT, 'gpu_class': gpu_class, 'roles': roles,
        'source_date_epoch': first['source_date_epoch'], 'pins_sha256': first['pins_sha256'],
        'source_payload_sha256': first['source_payload_sha256'], 'payload_sha256': sha256(manifest),
        'components': [part[4] for part in parts],
    })
    return files, {MANIFEST: manifest, SUMS: checksum_list(rows), RUNTIME: receipt}, index


def verify_stream(incoming, row, destination=None):
    digest = hashlib.sha256()
    total = 0
    target = destination.open('xb') if destination is not None else contextlib.nullcontext()
    with target as outgoing:
        for block in iter(lambda: incoming.read(1024 * 1024), b''):
            total += len(block)
            if total > row['size']:
                raise ValueError('Disc 3 payload member grew during verification: ' + row['path'])
            digest.update(block)
            if outgoing is not None:
                outgoing.write(block)
        if total != row['size'] or digest.hexdigest() != row['sha256']:
            raise ValueError('Disc 3 payload member failed hash verification: ' + row['path'])
        if outgoing is not None:
            outgoing.flush()
            os.fsync(outgoing.fileno())


def verify_member(source, row, destination=None):
    with checked_reader(source) as incoming:
        verify_stream(incoming, row, destination)


def sync_directory(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_bytes(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def verify_sources(parts, staging=None):
    copied = set()
    for payload, rows, _images, _pins, _receipt in parts:
        for name, row in rows.items():
            destination = None
            if staging is not None and name != INDEX and name not in copied:
                destination = staging / 'payload' / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                copied.add(name)
            verify_member(payload / name, row, destination)


def verify_output(output, files, metadata):
    payload = plain_directory(output / 'payload')
    if {path.name for path in output.iterdir()} != set(metadata) | {'payload'}:
        raise ValueError('Existing joined Disc 3 cache has unexpected or missing entries.')
    for name, raw in metadata.items():
        if read_bytes(output / name) != raw:
            raise ValueError('Existing joined Disc 3 cache metadata differs: ' + name)
    check_tree(payload, files)
    for name, row in files.items():
        verify_member(payload / name, row)
    if os.name == 'posix':
        for path in (output, *output.rglob('*')):
            info = path.lstat()
            mode = 0o755 if stat.S_ISDIR(info.st_mode) else 0o644
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode:
                raise ValueError('Existing joined Disc 3 cache has unexpected ownership or permissions.')


def join(core, embedder, ingestion, gpu_class, output, data_root):
    """Validate all component contents, then derive or verify the joined cache."""
    if gpu_class not in GPU_CLASSES:
        raise ValueError('Select a supported Disc 3 GPU class.')
    mounts = [plain_directory(path) for path in (core, embedder, ingestion)]
    output = plain_directory(output, missing=True)
    plain_directory(output.parent)
    data_root = plain_directory(data_root, missing=True)
    for index, mount in enumerate(mounts):
        if overlaps(output, mount) or any(overlaps(mount, earlier) for earlier in mounts[:index]):
            raise ValueError('Joined Disc 3 paths and component roots must be separate.')
    if overlaps(output, data_root):
        raise ValueError('The joined Disc 3 cache must be separate from the Forge data root.')

    parts = [
        component(mounts[0], 'core', None, ['door', 'reranker']),
        component(mounts[1], 'embedder', gpu_class, ['tei-' + gpu_class]),
        component(mounts[2], 'ingestion', None, ['builder', 'converter', 'qdrant']),
    ]
    files, metadata, index = join_metadata(parts, gpu_class)
    if output.exists():
        verify_sources(parts)
        verify_output(output, files, metadata)
        return output

    needed = sum(row['size'] for row in files.values()) + sum(map(len, metadata.values()))
    free = shutil.disk_usage(output.parent).free
    if free < needed:
        raise ValueError(f'Joining Disc 3 needs {needed} bytes; {free} bytes are available in the install cache.')
    staging = Path(tempfile.mkdtemp(prefix='.disc3-join-', dir=output.parent))
    try:
        verify_sources(parts, staging)
        write_bytes(staging / 'payload' / INDEX, index)
        for name, raw in metadata.items():
            write_bytes(staging / name, raw)
        if os.name == 'posix':
            for path in reversed(list(staging.rglob('*'))):
                if path.is_dir():
                    path.chmod(0o755)
                    sync_directory(path)
                else:
                    path.chmod(0o644)
            staging.chmod(0o755)
            sync_directory(staging)
        if output.exists() or output.is_symlink():
            raise FileExistsError('The joined Disc 3 cache was created during this operation.')
        staging.rename(output)
        sync_directory(output.parent)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output


@contextlib.contextmanager
def raw_iso_archive(tar, iso, include=()):
    # bsdtar reads Rock Ridge names and emits a binary PAX stream; it never
    # writes to the filesystem. The Windows getter holds the verified ISO.
    command = [str(tar), '-cf', '-', '--format', 'pax']
    for name in include:
        command += ['--include', name]
    timeout = 30 if include else 1800
    process = subprocess.Popen([*command, '@' + str(iso)], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def stop():
        try:
            if process.poll() is None:
                process.kill()
        except ProcessLookupError:
            pass

    timer = threading.Timer(timeout, stop)
    timer.daemon = True
    timer.start()
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|', bufsize=BUFFER) as archive:
            yield archive
        # The tar terminator precedes the native writer's final record padding.
        if len(process.stdout.read(BUFFER + 1)) > BUFFER or process.wait(timeout=10) != 0:
            raise ValueError('The trusted Windows ISO reader did not complete its stream.')
    finally:
        timer.cancel()
        stop()
        process.stdout.close()
        process.wait(timeout=10)


def raw_regular(member):
    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.issparse() or member.size < 0:
        raise ValueError('The raw Disc 3 ISO contains a linked or non-regular member.')
    return safe_member(member.name)


def raw_metadata(open_iso, iso, kind, card, roles):
    values = {}
    with open_iso(iso, RAW_METADATA) as archive:
        for member in archive:
            name = raw_regular(member)
            if name not in RAW_METADATA or name in values or member.size > RAW_METADATA_LIMIT:
                raise ValueError('Unexpected or oversized raw Disc 3 control metadata.')
            values[name] = archive.extractfile(member).read(RAW_METADATA_LIMIT + 1)
    if set(values) != set(RAW_METADATA):
        raise ValueError('Missing raw Disc 3 control metadata.')
    rows, images, pins, evidence = component_metadata(values.__getitem__, kind, card, roles)
    sums = {}
    for line in values['SHA256SUMS'].decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if match is None:
            raise ValueError('Malformed Disc 3 media checksum list.')
        checksum, name = match.groups()
        name = safe_member(name)
        if name == 'SHA256SUMS' or name in sums:
            raise ValueError('Duplicate Disc 3 media checksum entry.')
        sums[name] = checksum
    register_paths(sums, {})
    expected = {RAW_PAYLOAD + name: row['sha256'] for name, row in rows.items()}
    if expected != {name: checksum for name, checksum in sums.items() if name.startswith(RAW_PAYLOAD)}:
        raise ValueError('Raw Disc 3 checksums differ from the exact payload inventory.')
    for name, raw in values.items():
        if name != 'SHA256SUMS' and sums.get(name) != sha256(raw):
            raise ValueError('Raw Disc 3 control metadata does not match the ISO checksums.')
    sums['SHA256SUMS'] = sha256(values['SHA256SUMS'])
    return (iso, rows, images, pins, evidence), sums


def stream_raw_sources(open_iso, parts, staging, kit):
    copied = set()
    for number, (part, sums) in enumerate(parts):
        iso, rows, _images, _pins, _evidence = part
        remaining = set(sums)
        directories = {name[:index] for name in sums for index, char in enumerate(name) if char == '/'}
        seen_dirs = set()
        kit_bytes = 0
        with open_iso(iso) as archive:
            for member in archive:
                if member.isdir():
                    name = member.name
                    if (member.size or name in seen_dirs or
                            name != '.' and safe_member(name) not in directories):
                        raise ValueError('Unexpected raw Disc 3 directory.')
                    seen_dirs.add(name)
                    continue
                name = raw_regular(member)
                if name not in remaining:
                    raise ValueError('Unexpected or duplicated raw Disc 3 file.')
                remaining.remove(name)
                destination = None
                if name.startswith(RAW_PAYLOAD):
                    relative = name.removeprefix(RAW_PAYLOAD)
                    row = rows[relative]
                    if member.size != row['size']:
                        raise ValueError('Raw Disc 3 file size differs from the payload inventory.')
                    if staging is not None and relative != INDEX and relative not in copied:
                        destination = staging / 'payload' / relative
                        copied.add(relative)
                elif number == 0 and (name in RAW_KIT_FILES or name.split('/', 1)[0] in RAW_KIT_ROOTS):
                    if name not in RAW_UNUSED_FORMS:
                        kit_bytes += member.size
                        if kit_bytes > RAW_KIT_LIMIT:
                            raise ValueError('The raw Disc 3 runtime kit exceeds its temporary storage bound.')
                        destination = kit / name
                if destination is not None:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source:
                    verify_stream(source, {'size': member.size, 'sha256': sums[name], 'path': name}, destination)
        if remaining:
            raise ValueError('The raw Disc 3 ISO stream omitted a checksummed file.')
    if not all((kit / name).is_file() for name in ('pins.json', 'forms/provision.py', 'build/thin_forms.py')):
        raise ValueError('Missing verified core runtime kit files.')


def windows_system_tar(path):
    import ctypes
    directory = ctypes.create_unicode_buffer(32768)
    size = ctypes.WinDLL('kernel32', use_last_error=True).GetSystemDirectoryW(directory, len(directory))
    if not 0 < size < len(directory):
        raise ValueError('The native Windows system directory could not be verified.')
    expected = plain_directory(directory.value) / 'tar.exe'
    if Path(path) != expected:
        raise ValueError('Use the trusted native Windows system tar executable.')
    info = expected.lstat()
    # System32 files may be ordinary WinSxS servicing hardlinks.
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & REPARSE:
        raise ValueError('The trusted native Windows system tar executable is unavailable.')
    return expected


def join_windows_iso(core, embedder, ingestion, gpu_class, output, data_root, workspace, tar, *, open_iso=None):
    """Join authenticated raw ISOs without materialising extracted payload copies."""
    if gpu_class not in GPU_CLASSES:
        raise ValueError('Select a supported Disc 3 GPU class.')
    if any(not Path(path).is_absolute() or str(path).startswith(('\\\\', '//'))
           for path in (core, embedder, ingestion, output, data_root, workspace)):
        raise ValueError('Use absolute local paths for native Windows Disc 3 media and staging.')
    output = plain_directory(output, missing=True)
    workspace = plain_directory(workspace)
    data_root = plain_directory(data_root, missing=True)
    sources = [FileSystem().path(source) for source in (core, embedder, ingestion)]
    if (workspace.parent != output.parent or overlaps(workspace, output) or overlaps(output, data_root)
            or overlaps(workspace, data_root)
            or any(overlaps(workspace, source) or overlaps(output, source) for source in sources)
            or len(set(sources)) != 3 or any(workspace.iterdir())):
        raise ValueError('Use a new private Windows ISO workspace beside the joined cache.')
    if open_iso is None:
        tar = windows_system_tar(tar)
        open_iso = lambda path, names=(): raw_iso_archive(tar, path, names)
    parts = [raw_metadata(open_iso, iso, role, card, roles)
             for iso, role, card, roles in zip(sources, ('core', 'embedder', 'ingestion'),
                (None, gpu_class, None), (['door', 'reranker'], ['tei-' + gpu_class],
                                        ['builder', 'converter', 'qdrant']))]
    files, metadata, index = join_metadata([part for part, _sums in parts], gpu_class)
    reuse = output.exists()
    if reuse:
        verify_output(output, files, metadata)
    needed = RAW_KIT_LIMIT
    if not reuse:
        needed += sum(row['size'] for row in files.values()) + sum(map(len, metadata.values()))
    if shutil.disk_usage(workspace).free < needed:
        raise ValueError('Insufficient local storage for the verified joined cache and bounded core runtime kit.')
    kit, staging = workspace / 'kit', workspace / 'joined'
    kit.mkdir()
    try:
        if not reuse:
            staging.mkdir()
        stream_raw_sources(open_iso, parts, None if reuse else staging, kit)
        if not reuse:
            write_bytes(staging / 'payload' / INDEX, index)
            for name, raw in metadata.items():
                write_bytes(staging / name, raw)
            staging.rename(output)
    except BaseException:
        for owned in (kit, staging):
            if owned.exists():
                shutil.rmtree(owned)
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('core', 'embedder', 'ingestion', 'output', 'data-root'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--gpu-class', choices=GPU_CLASSES, required=True)
    parser.add_argument('--windows-workspace', type=Path)
    parser.add_argument('--bsdtar', type=Path)
    args = parser.parse_args()
    try:
        values = (args.core, args.embedder, args.ingestion, args.gpu_class, args.output, args.data_root)
        if args.windows_workspace is not None or args.bsdtar is not None:
            if os.name != 'nt' or args.windows_workspace is None or args.bsdtar is None:
                raise ValueError('Raw Disc 3 ISO reading is available only from the native Windows getter.')
            join_windows_iso(*values, args.windows_workspace, args.bsdtar)
        else:
            join(*values)
    except (LocalError, OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
