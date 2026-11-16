"""Node paths and test hooks for C2, C3, C4, C5, C6 and deep32-import-report.

Public: workstation, forge, rp, test_mode, hooks, now, iso, new_import_id,
alive and command. NodePaths is an immutable table of allowed paths and tools.
"""

import datetime
import os
from pathlib import Path
import re
import secrets
import sys
import time
from dataclasses import dataclass


ENGINE_VERSION = '1.0.0'
_HOOKS = None
_CLOCK_READS = 0


def test_mode():
    return bool(os.environ.get('D32_ROOT'))


def hooks():
    """Read the closed test-hook table once; production reads none of it."""
    global _HOOKS
    if _HOOKS is None:
        result = {}
        if test_mode():
            names = ('ROOT', 'STUBS', 'PYTHON', 'TEST_CLOCK', 'TEST_RAND',
                     'TEST_FREE_BYTES', 'TEST_FAULTS', 'TEST_CRASH',
                     'TEST_SIGNAL_AT', 'TEST_POINTERS', 'TEST_ENGINE_VERSION',
                     'TEST_LIVE_PIDS')
            for name in names:
                key = 'D32_' + name
                result[key] = os.environ.get(key, '')
            result['faults'] = tuple(x for x in result['D32_TEST_FAULTS'].split(',') if x)
            result['live_pids'] = frozenset(int(x) for x in result['D32_TEST_LIVE_PIDS'].split(',') if x.isdecimal())
        _HOOKS = result
    return _HOOKS


def rp(path):
    """Map an absolute node path under the synthetic root only in test mode."""
    value = Path(path)
    if test_mode():
        root = Path(hooks()['D32_ROOT'])
        # Already mapped paths must not receive the prefix a second time.
        try:
            value.relative_to(root)
            return value
        except ValueError:
            pass
        return root / str(path).replace('\\', '/').lstrip('/')
    return value


def now():
    global _CLOCK_READS
    clock = hooks().get('D32_TEST_CLOCK', '')
    if clock:
        result = int(clock) + _CLOCK_READS
        _CLOCK_READS += 1
        return result
    return time.time()


def iso(ts=None):
    if ts is None:
        ts = now()
    if isinstance(ts, str):
        return ts
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def new_import_id():
    stamp = datetime.datetime.fromtimestamp(now(), datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    suffix = hooks().get('D32_TEST_RAND', '')
    if not re.fullmatch('[0-9a-f]{8}', suffix):
        suffix = secrets.token_hex(4)
    return stamp + '-' + suffix


def alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid < 1:
        return False
    if test_mode():
        return pid in hooks().get('live_pids', ())
    if os.name == 'nt':
        # os.kill(pid, 0) is not a liveness probe on Windows.
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, ValueError):
        return False
    return True


@dataclass(frozen=True)
class NodePaths:
    node: str
    content_root: Path
    state: Path
    log: Path
    release_key: Path
    release_fingerprint: Path
    release_file: Path | None
    embedding_file: Path | None
    services_file: Path | None
    status_file: Path | None
    disklabel: str | None
    mount_command: str
    umount_command: str
    rcctl: str | None
    c6_command: str | None
    gpgv: str
    state_group: str
    state_mode: int
    report_mode: int

    @property
    def cr(self):
        return self.content_root

    @property
    def s(self):
        return self.state

    @property
    def report(self):
        return self.state / 'report.json'

    @property
    def history(self):
        return self.state / 'history'

    @property
    def journal(self):
        return self.state / 'journal'

    @property
    def state_file(self):
        return self.journal / 'state.json'

    @property
    def registry_file(self):
        return self.journal / 'installed.json'

    @property
    def lock(self):
        return self.journal / 'lock'

    @property
    def tmp(self):
        return self.journal / 'tmp'

    @property
    def mount(self):
        return self.state / 'mnt'

    @property
    def mnt(self):
        return self.mount

    @property
    def packs(self):
        return self.state / 'packs'

    def command(self, name):
        return command(name, self)


def workstation():
    return NodePaths('workstation', rp('/d32/libcontent'), rp('/var/db/d32/import'),
                     rp('/var/log/d32-import.log'), rp('/usr/local/share/d32/release/release-key.gpg'),
                     rp('/usr/local/share/d32/release/release-key.fingerprint'), rp('/etc/d32/release'),
                     rp('/etc/d32/state/forge-embedding'), rp('/etc/rc.conf.local'), None,
                     '/sbin/disklabel', '/sbin/mount', '/sbin/umount', '/usr/sbin/rcctl',
                     '/usr/local/d32/sbin/d32-c6', '/usr/local/bin/gpgv', '_d32users', 0o750, 0o440)


def forge():
    return NodePaths('forge', rp('/var/lib/deep32/content'), rp('/var/lib/deep32/import'),
                     rp('/var/log/deep32-import.log'), rp('/opt/deep32-forge/release/release-key.gpg'),
                     rp('/opt/deep32-forge/release/release-key.fingerprint'), None, None, None,
                     rp('/run/deep32-forge/status'), None, '/usr/bin/mount', '/usr/bin/umount', None,
                     None, '/usr/bin/gpgv', 'root', 0o755, 0o644)


def command(name, paths=None):
    """Return the allowed argv prefix; callers append only table arguments."""
    if name in ('python', 'interpreter'):
        return [sys.executable, '-I', '-B']
    if name not in ('disklabel', 'mount', 'umount', 'rcctl', 'd32-c6', 'gpgv'):
        raise ValueError('unknown command')
    if test_mode():
        return [sys.executable, '-I', '-B', str(Path(hooks()['D32_STUBS']) / (name + '.py'))]
    paths = paths or workstation()
    value = {'disklabel': paths.disklabel, 'mount': paths.mount_command,
             'umount': paths.umount_command, 'rcctl': paths.rcctl,
             'd32-c6': paths.c6_command, 'gpgv': paths.gpgv}[name]
    if value is None:
        raise ValueError('command unavailable on node')
    return [value]
