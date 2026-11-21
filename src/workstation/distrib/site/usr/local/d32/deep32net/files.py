"""Private paths and durable atomic replacement."""
import os
from pathlib import Path
import secrets
import stat
import functools
import threading
from .errors import StateError, Exists

_write_lock = threading.RLock()

def private_write(function):
    @functools.wraps(function)
    def guarded(*args,**kwargs):
        with _write_lock:
            previous = os.umask(0o077)
            try:
                return function(*args,**kwargs)
            finally:
                os.umask(previous)
    return guarded

def safe(path):
    p = Path(os.path.abspath(path))
    try:
        for part in (p,*p.parents):
            if os.path.islink(part) or getattr(os.path,'isjunction',lambda x:False)(part):
                raise StateError()
        return p
    except OSError:
        raise StateError() from None

@private_write
def state_dir(path):
    p = safe(path)
    try:
        if not p.exists():
            if not p.parent.is_dir():
                raise StateError()
            p.mkdir(mode=0o700)
        if not p.is_dir():
            raise StateError()
        return p
    except OSError:
        raise StateError() from None

@private_write
def private_dir(path):
    p = safe(path)
    try:
        if not p.exists():
            private_dir(p.parent)
            p.mkdir(mode=0o700)
        if not p.is_dir():
            raise StateError()
        return p
    except OSError:
        raise StateError() from None

def sync_dir(path):
    if os.name == 'posix':
        fd = os.open(safe(path),os.O_RDONLY | getattr(os,'O_DIRECTORY',0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

@private_write
def atomic_write(path, data, overwrite=True, mode=0o600):
    path = safe(path)
    if not overwrite and path.exists():
        raise Exists()
    temporary = path.with_name(path.name+'.tmp-'+secrets.token_hex(8))
    fd = None
    try:
        private_dir(path.parent)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_BINARY',0)
        fd = os.open(safe(temporary),flags,mode)
        if os.name == 'posix':
            os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        safe(path)
        if not overwrite and path.exists():
            raise Exists()
        os.replace(temporary,path)
        sync_dir(path.parent)
    except OSError:
        raise StateError() from None
    finally:
        if fd is not None:
            os.close(fd)
        if temporary.exists():
            temporary.unlink()

def read_bytes(path):
    p = safe(path)
    try:
        fd = os.open(p,os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0) | getattr(os,'O_BINARY',0))
        with os.fdopen(fd,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise StateError()
            return stream.read()
    except OSError:
        raise StateError() from None

def remove(path):
    p = safe(path)
    try:
        if p.exists():
            p.unlink()
            sync_dir(p.parent)
    except OSError:
        raise StateError() from None
