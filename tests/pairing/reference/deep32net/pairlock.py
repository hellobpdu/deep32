"""Persistent failed-window state, using the link client's text format."""
from dataclasses import dataclass
import os
import re
import stat
from .errors import StateError
from .files import safe, read_bytes, atomic_write

MAX = 2**63-1

@dataclass
class State:
    f0: int = 0
    f1: int = 0
    f2: int = 0
    until: int = 0
    in_progress: int = 0

    def values(self):
        return [self.f0,self.f1,self.f2,self.until,self.in_progress]

def parse(data):
    if not 1 <= len(data) <= 255 or not re.fullmatch(rb'D32PAIR2 ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+)\n?',data):
        raise StateError()
    try:
        values = [int(x) for x in data.split()[1:]]
    except ValueError:
        raise StateError() from None
    a,b,c,until,active = values
    if not a <= b <= c <= MAX-900 or until > MAX or active > 1:
        raise StateError()
    return State(*values)

def text(state):
    result = 'D32PAIR2 ' + ' '.join(str(v) for v in state.values()) + '\n'
    parse(result.encode('ascii'))
    return result

def load(path):
    path = safe(path)
    if not path.exists():
        return State()
    try:
        fd = os.open(path,os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0) | getattr(os,'O_BINARY',0))
        with os.fdopen(fd,'rb') as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or not 1 <= st.st_size <= 255:
                raise StateError()
            if os.name == 'posix' and (st.st_nlink != 1 or st.st_uid != os.geteuid() or st.st_mode & 0o77):
                raise StateError()
            data = stream.read(256)
            if len(data) != st.st_size:
                raise StateError()
            return parse(data)
    except OSError:
        raise StateError() from None

def save(path, state):
    atomic_write(path,text(state).encode('ascii'))

def remaining(state, now):
    if now < 0 or state.until < 0:
        return 2**31-1
    return min(2**31-1,max(0,state.until-now))

def failed(state, now):
    if now < 0 or now > MAX-900:
        raise StateError()
    state.in_progress = 0
    now = max(now,state.f2)
    kept = [v for v in (state.f0,state.f1,state.f2) if v > 0 and v >= now-1800]
    if len(kept) == 3:
        kept = kept[1:]
    kept.append(now)
    state.f0,state.f1,state.f2 = [0]*(3-len(kept))+kept
    if len(kept) == 3:
        state.until = now+900
    return remaining(state,now)
