"""Exact-slot files and enum command capabilities for future platform adapters.

There is deliberately no PATH search, arbitrary argv, shell, environment input,
network socket, installer, or write method. Native command adapters must have
separate no-write qualification; this package never launches an unqualified tool.
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import threading
import time

from .errors import DiagError, bounded

MAX_LOCAL = 1024 * 1024


@bounded('invalid-json')
def strict_json(raw, maximum=MAX_LOCAL):
    if not isinstance(raw, bytes) or len(raw) > maximum:
        raise DiagError('oversized')

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError()
            out[key] = value
        return out

    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise DiagError('invalid-json') from None


def _identity(info):
    # Windows path stat/handle stat can expose different change-time clocks.
    # Compare stable file identity, type/permissions, size and write-time; every
    # snapshot also retains a content digest for the final change check.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, getattr(info, 'st_birthtime_ns', None) if os.name == 'nt' else info.st_ctime_ns,
            info.st_uid, info.st_gid, info.st_nlink)


def _unlinked(path):
    # Check every existing component, including Windows junction/reparse points.
    for member in (path, *path.parents):
        info = member.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise DiagError('unsafe-file')


@dataclass(frozen=True, repr=False)
class FileSnapshot:
    data: bytes = field(repr=False)
    identity: tuple = field(repr=False)
    digest: str = field(repr=False)
    protected: bool = field(default=False, repr=False)

    def __repr__(self):
        return 'FileSnapshot(<private>)'


class ExistingFiles:
    """Read only explicitly installed slots; a missing root remains missing.

    `verify_access` is a read-only native owner/ACL checker. POSIX ownership/mode
    and Windows ACL validation are required for protected slots, never repaired.
    Paths are supplied by the owning installed adapter, not command-line input.
    """
    @bounded('unsafe-file')
    def __init__(self, slots, *, verify_access=None):
        self.__slots = {}
        if type(slots) is not dict:
            raise DiagError('unsafe-file')
        for name, value in slots.items():
            if not isinstance(value, (str, Path)):
                raise DiagError('unsafe-file')
            path = Path(value)
            if not isinstance(name, str) or not name.isidentifier() or not path.is_absolute():
                raise DiagError('unsafe-file')
            self.__slots[name] = path
        self.__verify_access = verify_access

    def matches_slots(self, paths):
        return (type(paths) is dict and set(paths) == set(self.__slots)
                and all(isinstance(value, (str, Path)) and Path(value) == self.__slots[name]
                        for name, value in paths.items()))

    @bounded('unsafe-file')
    def read_existing_regular(self, slot, *, maximum=MAX_LOCAL, protected=False):
        if slot not in self.__slots or type(maximum) is not int or not 0 < maximum <= 4 * MAX_LOCAL:
            raise DiagError('unsafe-file')
        path = self.__slots[slot]
        fd = None
        try:
            _unlinked(path)
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise DiagError('unsafe-file')
            if protected:
                if os.name == 'posix':
                    if before.st_uid != os.getuid() or before.st_mode & 0o077:
                        raise DiagError('unsafe-file')
                elif self.__verify_access is None:
                    raise DiagError('unsafe-file')
                if self.__verify_access is not None and self.__verify_access(path) is not True:
                    raise DiagError('unsafe-file')
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            fd = os.open(path, flags)
            opened = os.fstat(fd)
            if _identity(before) != _identity(opened) or opened.st_size > maximum:
                raise DiagError('oversized' if opened.st_size > maximum else 'changed-file')
            chunks, count = [], 0
            while True:
                chunk = os.read(fd, min(65536, maximum + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
                if count > maximum:
                    raise DiagError('oversized')
            _unlinked(path)
            if _identity(opened) != _identity(os.fstat(fd)) or _identity(opened) != _identity(path.lstat()):
                raise DiagError('changed-file')
            data = b''.join(chunks)
            return FileSnapshot(data, _identity(opened), hashlib.sha256(data).hexdigest(), protected)
        except FileNotFoundError:
            raise DiagError('missing-file') from None
        except (OSError, ValueError, TypeError):
            raise DiagError('unsafe-file') from None
        finally:
            if fd is not None:
                os.close(fd)

    @bounded('changed-file')
    def unchanged(self, slot, snapshot):
        current = self.read_existing_regular(slot, maximum=max(1, len(snapshot.data)), protected=snapshot.protected)
        return current.identity == snapshot.identity and current.digest == snapshot.digest


class Command(Enum):
    TAILSCALE_STATUS = 'tailscale-status'
    TAILSCALE_SERVE_STATUS = 'tailscale-serve-status'
    DOCKER_VERSION = 'docker-version'
    DOCKER_INFO = 'docker-info'
    DOCKER_COMPOSE_PS = 'docker-compose-ps'
    DOCKER_INSPECT = 'docker-inspect'


@dataclass(frozen=True, repr=False)
class CommandRequest:
    argv: tuple = field(repr=False)
    deadline: float
    max_bytes: int = MAX_LOCAL

    def __repr__(self):
        return 'CommandRequest(<approved>, bounded=True)'


@dataclass(frozen=True, repr=False)
class CommandOutput:
    returncode: int
    stdout: bytes = field(repr=False)

    def __repr__(self):
        return 'CommandOutput(<private>)'


class Budget:
    """90 seconds in total, with the final five reserved for the report."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.end = self.started + 90
        self.__inflight = threading.BoundedSemaphore(4)

    @bounded('deadline')
    def deadline(self):
        now = self.clock()
        if now >= self.end - 5:
            raise DiagError('deadline')
        return min(now + 10, self.end - 5)

    @bounded('command-failed')
    def observe(self, observer, request):
        # The qualified backend owns cancellation/draining. This gate bounds
        # in-flight observations and checks late callbacks even in fake tests.
        now = self.clock()
        if (type(request) is not CommandRequest or type(request.deadline) not in (int, float)
                or not math.isfinite(request.deadline) or request.deadline > min(now + 10, self.end - 5)
                or now >= min(request.deadline, self.end - 5)):
            raise DiagError('deadline')
        if not self.__inflight.acquire(blocking=False):
            raise DiagError('deadline')
        failed = False
        try:
            if self.clock() >= request.deadline:
                raise DiagError('deadline')
            try:
                result = observer(request)
            except Exception:
                failed = True
            if failed:
                raise DiagError('command-failed')
            if self.clock() > request.deadline:
                raise DiagError('deadline')
            return result
        finally:
            self.__inflight.release()


class ReadOnlyCommands:
    """Single-observation capability; a role scheduler may use at most four.

    Observer interface: `(CommandRequest) -> CommandOutput`. A qualified adapter
    must enforce its absolute deadline/output cap *while* draining pipes and
    terminate only its own process tree (including orphaned pipe holders).
    J05 supplies no native adapter: no external binary is qualified by mocks.
    """
    @bounded('unsafe-command')
    def __init__(self, executables, observer=None, *, qualified=False, budget=None,
                 docker_endpoint=None, compose_file=None, project=None, containers=()):
        self.__executables = {}
        if type(executables) is not dict:
            raise DiagError('unsafe-command')
        for key, value in executables.items():
            if key not in ('tailscale', 'docker') or not isinstance(value, (str, Path)) or not Path(value).is_absolute():
                raise DiagError('unsafe-command')
            self.__executables[key] = str(value)
        if docker_endpoint is not None and docker_endpoint not in (
                'unix:///var/run/docker.sock', 'npipe:////./pipe/dockerDesktopLinuxEngine'):
            raise DiagError('unsafe-target')
        import re
        if project is not None and (type(project) is not str or not re.fullmatch(r'deep32-[a-z0-9][a-z0-9-]{0,63}', project)):
            raise DiagError('unsafe-command')
        if compose_file is not None and (not isinstance(compose_file, (str, Path)) or not Path(compose_file).is_absolute()):
            raise DiagError('unsafe-command')
        if not isinstance(containers, (list, tuple, frozenset)) or any(type(x) is not str or not re.fullmatch(r'[0-9a-f]{64}', x) for x in containers):
            raise DiagError('unsafe-command')
        self.__observer, self.__qualified = observer, qualified is True
        self.__endpoint, self.__compose, self.__project = docker_endpoint, compose_file, project
        self.__containers = frozenset(containers)
        self.__budget = budget or Budget()

    @bounded('unsafe-command')
    def observe_command(self, command, *, container=None):
        if type(command) is not Command:
            raise DiagError('unsafe-command')
        if command in (Command.TAILSCALE_STATUS, Command.TAILSCALE_SERVE_STATUS):
            tool = self.__executables.get('tailscale')
            args = ('status', '--json') if command is Command.TAILSCALE_STATUS else ('serve', 'status', '--json')
        else:
            tool = self.__executables.get('docker')
            if not self.__endpoint:
                raise DiagError('unsafe-target')
            prefix = ('--host', self.__endpoint)
            if command is Command.DOCKER_VERSION:
                args = prefix + ('version', '--format', '{{json .Server}}')
            elif command is Command.DOCKER_INFO:
                args = prefix + ('info', '--format', '{{json .Runtimes}}')
            elif command is Command.DOCKER_COMPOSE_PS:
                if self.__compose is None or self.__project is None:
                    raise DiagError('unsafe-command')
                args = prefix + ('compose', '--file', str(self.__compose), '--project-name', self.__project,
                                 'ps', '--all', '--format', 'json')
            elif command is Command.DOCKER_INSPECT:
                if container not in self.__containers:
                    raise DiagError('unsafe-command')
                # No Env, mounts, labels, health logs or arbitrary Go template.
                args = prefix + ('inspect', '--format',
                                 '{"image":{{json .Image}},"state":{{json .State.Status}},'
                                 '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}',
                                 container)
        if tool is None or not self.__qualified or self.__observer is None:
            raise DiagError('unqualified-command')
        request = CommandRequest((tool,) + args, self.__budget.deadline())
        result = self.__budget.observe(self.__observer, request)
        if self.__budget.clock() > request.deadline:
            raise DiagError('deadline')
        if type(result) is not CommandOutput or type(result.returncode) is not int or not isinstance(result.stdout, bytes):
            raise DiagError('command-failed')
        if len(result.stdout) > request.max_bytes:
            raise DiagError('oversized')
        if result.returncode != 0:
            raise DiagError('command-failed')
        return result.stdout
