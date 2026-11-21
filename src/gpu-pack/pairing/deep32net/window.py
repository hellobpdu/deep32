"""One durable claim inside an exclusive local pairing window."""
import os
import re
import time
from .errors import StateError, Locked
from .files import state_dir, private_dir, safe, atomic_write
from . import pairlock

class PairingWindow:
    def __init__(self, state, link, seconds=300, clock=None):
        if not re.fullmatch('[A-Za-z0-9_-]+',link) or seconds <= 0:
            raise StateError()
        self.root = state_dir(state)
        self.directory = private_dir(self.root/'pairing')
        self.path = self.directory/(link+'.pairlock')
        self.lock_path = self.directory/(link+'.lock')
        self.seconds, self.clock = seconds, clock or time
        self.fd, self.claimed, self.closed = None,False,True
        self.completed, self.reason = False,None

    def open(self):
        if self.fd is not None:
            raise StateError('busy')
        p = safe(self.lock_path)
        try:
            if not p.exists():
                atomic_write(p,b'\0',overwrite=False)
            self.fd = os.open(p,os.O_RDWR | getattr(os,'O_NOFOLLOW',0))
            if os.name == 'nt':
                import msvcrt
                os.lseek(self.fd,0,os.SEEK_SET)
                msvcrt.locking(self.fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError,ImportError):
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            raise StateError('busy') from None
        try:
            self.state = pairlock.load(self.path)
            if self.state.in_progress:
                pairlock.failed(self.state,int(self.clock.time()))
                pairlock.save(self.path,self.state)
            locked = pairlock.remaining(self.state,int(self.clock.time()))
            if locked:
                raise Locked(locked)
            self.deadline = self.clock.monotonic()+self.seconds
            self.closed,self.claimed,self.completed = False,False,False
            return self
        except Exception:
            self.close()
            raise

    def remaining_seconds(self):
        return max(0,self.deadline-self.clock.monotonic()) if not self.closed else 0

    def can_claim(self):
        return not self.closed and not self.claimed and not self.completed and self.remaining_seconds() > 0 and pairlock.remaining(self.state,int(self.clock.time())) == 0

    def claim(self):
        if not self.can_claim():
            raise StateError('busy')
        self.state.in_progress = 1
        pairlock.save(self.path,self.state)
        self.claimed = True

    def succeed(self):
        if not self.claimed or self.completed:
            raise StateError()
        self.state.in_progress = 0
        pairlock.save(self.path,self.state)
        self.completed = True

    def fail(self, reason):
        if not self.claimed or self.completed:
            raise StateError()
        pairlock.failed(self.state,int(self.clock.time()))
        pairlock.save(self.path,self.state)
        self.completed,self.reason = True,reason

    def expire(self):
        if self.claimed:
            raise StateError()
        self.closed = True

    def close(self):
        self.closed = True
        if self.fd is not None:
            if os.name == 'nt':
                import msvcrt
                os.lseek(self.fd,0,os.SEEK_SET)
                msvcrt.locking(self.fd,msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.fd,fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
