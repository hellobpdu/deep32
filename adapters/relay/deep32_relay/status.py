"""Strict, private, atomic supervisor snapshots and a passive doctor reader."""
import ctypes
import math
import os
import re
import time
from .state import OwnedRoot, StateError, WindowsSecurity, valid_hash, valid_id

SCHEMA = 'deep32.relay-status.v1'
MAX_STATUS = 8192
STALE_SECONDS = 15
REASONS = frozenset(('none', 'native-agent-unqualified', 'pairing-bootstrap-unavailable',
                     'native-oracle-unavailable', 'cancelled', 'turn-timeout', 'cleanup-failed',
                     'invalid-agent-output', 'agent-start-failed', 'agent-output-failed', 'agent-sign-in-required',
                     'link-lost', 'box-unqualified', 'stopped', 'quota-exceeded',
                     'protocol-error', 'mcp-failed', 'output-limit', 'session-timeout'))
FIELDS = frozenset(('schema', 'install_id', 'profile_alias', 'agent', 'release_id',
                    'capability_id', 'pid', 'instance_id', 'observed_at', 'running',
                    'paired_workstation', 'workstation_alias', 'link_state',
                    'agent_binary_found', 'box_state', 'session_state', 'queue_depth',
                    'last_session_result'))


def _timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def choice_id(value):
    """A model or effort id the status may record: the characters and length the Ask window accepts."""
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9._:/-]{1,64}', value) is not None


def validate_snapshot(value):
    if type(value) is not dict or set(value) != FIELDS:
        raise StateError('invalid-status')
    if (value['schema'] != SCHEMA or not valid_id(value['install_id']) or
            value['agent'] not in ('codex', 'claude') or
            value['profile_alias'] != 'relay-' + value['agent'] or
            not valid_hash(value['release_id']) or
            not (value['capability_id'] == 'unqualified' or valid_hash(value['capability_id'])) or
            type(value['pid']) is not int or not 0 <= value['pid'] <= 0x7fffffff or
            type(value['instance_id']) is not str or not re.fullmatch(r'(unknown|windows-[0-9a-f]{16}|linux-[0-9]{1,24}|macos-[0-9]{1,20}-[0-9]{6})', value['instance_id']) or
            not _timestamp(value['observed_at']) or
            any(type(value[key]) is not bool for key in ('running', 'paired_workstation', 'agent_binary_found')) or
            value['workstation_alias'] != ('workstation' if value['paired_workstation'] else 'none') or
            value['link_state'] not in ('off', 'listening', 'pairing', 'connected', 'disconnected', 'error') or
            value['box_state'] not in ('qualified', 'unqualified') or
            value['session_state'] not in ('none', 'opening', 'queued', 'idle', 'verifying', 'running', 'draining', 'closing', 'closed') or
            type(value['queue_depth']) is not int or not 0 <= value['queue_depth'] <= 2):
        raise StateError('invalid-status')
    result = value['last_session_result']
    base = {'state', 'reason', 'observed_at'}
    if (type(result) is not dict or set(result) not in (base, base | {'model', 'effort'}) or
            result['state'] not in ('none', 'completed', 'interrupted', 'failed') or
            result['reason'] not in REASONS or not _timestamp(result['observed_at'])):
        raise StateError('invalid-status')
    # A session's chosen model and effort, as advertised: short ids, never free text.
    if set(result) != base and (result['state'] == 'none' or
                                not all(choice_id(result[key]) for key in ('model', 'effort'))):
        raise StateError('invalid-status')
    if value['running'] and (value['pid'] == 0 or value['instance_id'] == 'unknown'):
        raise StateError('invalid-status')
    return value


class _ProcBSDInfo(ctypes.Structure):
    """Darwin libproc PROC_PIDTBSDINFO's fixed-width proc_bsdinfo layout."""
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid', 'rgid',
        'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64), ('start_usec', ctypes.c_uint64)]


def _macos_identity(pid, *, library=None, uid=None):
    # Reject ABI disagreement instead of treating partial/uninitialised data as
    # liveness. This reads process metadata only, never memory or credentials.
    if ctypes.sizeof(_ProcBSDInfo) != 136:
        return None
    library = library or ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    observe = library.proc_pidinfo
    observe.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    observe.restype = ctypes.c_int
    value = _ProcBSDInfo()
    size = ctypes.sizeof(value)
    if observe(pid, 3, 0, ctypes.byref(value), size) != size:  # PROC_PIDTBSDINFO
        return None
    expected_uid = os.geteuid() if uid is None else uid
    if (value.pid != pid or value.uid != expected_uid or value.status in (0, 5) or
            value.start_sec == 0 or value.start_usec >= 1000000):
        return None
    return 'macos-%d-%06d' % (value.start_sec, value.start_usec)


def process_identity(pid):
    """No child process, socket, credential access or mutation.

    Birth time plus current account ownership prevents saved snapshots from
    mistaking a reused PID for the relay. Platform ABI is qualified live on R2.
    """
    if type(pid) is not int or pid <= 0:
        return None
    try:
        if os.name == 'nt':
            from ctypes import wintypes as w
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            kernel.OpenProcess.restype = w.HANDLE
            kernel.GetProcessTimes.argtypes = [w.HANDLE, ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME)]
            kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
            kernel.CloseHandle.argtypes = [w.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                # A live PID and creation time alone do not prove the process
                # belongs to this account. Query only its token's user SID.
                security = WindowsSecurity()
                token, needed = w.HANDLE(), w.DWORD()
                if not security.adv.OpenProcessToken(handle, 8, ctypes.byref(token)):
                    return None
                try:
                    security.adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
                    user_info = ctypes.create_string_buffer(needed.value)
                    if not security.adv.GetTokenInformation(token, 1, user_info, needed, ctypes.byref(needed)):
                        return None
                    process_sid = ctypes.c_void_p.from_buffer(user_info).value
                    if not security.adv.EqualSid(process_sid, security.sid):
                        return None
                finally:
                    kernel.CloseHandle(token)
                created, exited, system, user = w.FILETIME(), w.FILETIME(), w.FILETIME(), w.FILETIME()
                code = w.DWORD()
                if (not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)) or
                        not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259):
                    return None
                return 'windows-%016x' % ((created.dwHighDateTime << 32) | created.dwLowDateTime)
            finally:
                kernel.CloseHandle(handle)
        if os.sys.platform.startswith('linux'):
            from pathlib import Path
            path = Path('/proc') / str(pid)
            if path.stat().st_uid != os.geteuid():
                return None
            # The command field can contain spaces/parentheses; parse after its final ')'.
            raw = (path / 'stat').read_bytes()[:4096]
            fields = raw[raw.rfind(b')') + 2:].split()
            if len(fields) < 20 or fields[0] == b'Z' or not fields[19].isdigit():
                return None
            return 'linux-' + fields[19].decode('ascii')
        if os.sys.platform == 'darwin':
            return _macos_identity(pid)
    except (OSError, ValueError, IndexError, AttributeError, StateError):
        return None
    return None


def publish_status(root, snapshot):
    owner = root if isinstance(root, OwnedRoot) else OwnedRoot(root)
    validate_snapshot(snapshot)
    owner.validate(snapshot['install_id'])
    owner.write_json('runtime/status.json', snapshot)


def read_status(root, now=None, max_age=STALE_SECONDS, observer=process_identity):
    """Return only typed public data; missing, unsafe, stale and reused PIDs fail closed."""
    result = {'state': 'missing', 'snapshot': None}
    try:
        owner = root if isinstance(root, OwnedRoot) else OwnedRoot(root)
        install_id = owner.validate()
        snapshot = validate_snapshot(owner.read_json('runtime/status.json', MAX_STATUS))
        if snapshot['install_id'] != install_id:
            raise StateError('invalid-status')
        # Copy before adjusting the observation, leaving the saved file untouched.
        snapshot = dict(snapshot, last_session_result=dict(snapshot['last_session_result']))
        current = time.time() if now is None else now
        if not _timestamp(current) or not _timestamp(max_age) or max_age <= 0:
            raise StateError('invalid-status')
        age = current - snapshot['observed_at']
        if age < -1 or age > max_age:
            snapshot['running'] = False
            return {'state': 'stale', 'snapshot': snapshot}
        if snapshot['running']:
            identity = observer(snapshot['pid'])
            if identity is None or identity != snapshot['instance_id']:
                snapshot['running'] = False
                return {'state': 'unavailable', 'snapshot': snapshot}
        return {'state': 'ok' if snapshot['running'] else 'unavailable', 'snapshot': snapshot}
    except FileNotFoundError:
        return result
    except (StateError, OSError, ValueError, TypeError, KeyError, RecursionError):
        return {'state': 'unavailable', 'snapshot': None}


def unavailable_snapshot(install_id, agent, release_id, agent_found=False, now=None):
    return validate_snapshot({
        'schema': SCHEMA, 'install_id': install_id, 'profile_alias': 'relay-' + agent,
        'agent': agent, 'release_id': release_id, 'capability_id': 'unqualified',
        'pid': 0, 'instance_id': 'unknown', 'observed_at': time.time() if now is None else now,
        'running': False, 'paired_workstation': False, 'workstation_alias': 'none',
        'link_state': 'off', 'agent_binary_found': bool(agent_found), 'box_state': 'unqualified',
        'session_state': 'none', 'queue_depth': 0,
        'last_session_result': {'state': 'none', 'reason': 'native-agent-unqualified', 'observed_at': 0}})


def doctor_status(root, **options):
    """R6 passive projection. No process launch, listener, file or repair writes."""
    observation = read_status(root, **options)
    value = observation['snapshot']
    if value is None:
        return {'schema': 'deep32.relay-doctor.v1', 'state': observation['state'],
                'running': False, 'paired_workstation': False, 'link_state': 'off',
                'last_session_result': {'state': 'none', 'reason': 'stopped', 'observed_at': 0},
                'agent_binary_found': False, 'agent': 'unavailable', 'profile_alias': 'none'}
    return {'schema': 'deep32.relay-doctor.v1', 'state': observation['state'],
            **{key: value[key] for key in ('running', 'paired_workstation', 'link_state',
               'last_session_result', 'agent_binary_found', 'agent', 'profile_alias')}}
