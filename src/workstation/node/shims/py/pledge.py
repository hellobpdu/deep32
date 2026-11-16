"""pledge(2) and unveil(2) for the Deep32 node's Python services, through libc with ctypes (OpenBSD only).

From preflight/python-tools/pledge.py (PYTHON-ON-OPENBSD 7.1), unchanged in behaviour. ctypes.CDLL(None) is the
running program's own symbol table (libc is in it), so no ldconfig or find_library call is made (HARDENING 5.8), and
no ctypes callback is created (no W^X mapping).

Order in a launcher: import everything, bind (d32-sockexec did that), unveil each path, unveil_lock(), pledge().
After a pledge without "prot_exec" no shared object can be mapped, so every dlopen must happen before it.
"""
import ctypes
import os
import sys

if not sys.platform.startswith("openbsd"):
    raise ImportError("pledge/unveil exist on OpenBSD only")

_libc = ctypes.CDLL(None, use_errno=True)
_libc.pledge.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
_libc.pledge.restype = ctypes.c_int
_libc.unveil.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
_libc.unveil.restype = ctypes.c_int


def _check(rc: int, what: str) -> None:
    if rc == -1:
        err = ctypes.get_errno()
        raise OSError(err, f"{what}: {os.strerror(err)}")


def unveil(path: str, perms: str) -> None:
    """perms is a subset of "rwxc" ("" hides the path)."""
    _check(_libc.unveil(os.fsencode(path), perms.encode()), f"unveil {path} {perms!r}")


def unveil_lock() -> None:
    """No further unveil call is possible after this one."""
    _check(_libc.unveil(None, None), "unveil lock")


def pledge(promises: str, execpromises: str | None = None) -> None:
    """For the pre-flight add "error": a violation then returns ENOSYS (and ktrace -t p records it) instead of
    killing the process. Production promises never include "error"."""
    _check(_libc.pledge(promises.encode(), None if execpromises is None else execpromises.encode()), "pledge")
