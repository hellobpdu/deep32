"""Per-user startup serializers. Native registration remains unqualified.

An injected API is the only registration/stop boundary. The default API never
executes scheduler, systemctl, launchctl, agent or Tailscale commands.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from .state import StateError, valid_id, strict_json, json_bytes, safe_path


def platform_name():
    if os.name == 'nt':
        return 'windows'
    if sys.platform == 'darwin':
        return 'macos'
    if sys.platform.startswith('linux'):
        return 'linux'
    raise StateError('platform-unavailable')


def state_base(platform=None):
    platform = platform or platform_name()
    if platform == 'windows':
        # LocalAppData is a known-folder result, never an agent configuration home.
        import ctypes
        from ctypes import wintypes as w
        class GUID(ctypes.Structure):
            _fields_ = [('a', w.DWORD), ('b', w.WORD), ('c', w.WORD), ('d', ctypes.c_ubyte * 8)]
        known = GUID(0xF1B32785, 0x6FBA, 0x4FCF, (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91))
        output = ctypes.c_void_p()
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), w.DWORD, w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        if shell.SHGetKnownFolderPath(ctypes.byref(known), 0, None, ctypes.byref(output)):
            raise StateError('state-location-unavailable')
        ole = ctypes.WinDLL('ole32')
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        try:
            base = Path(ctypes.wstring_at(output))
        finally:
            ole.CoTaskMemFree(output)
        return base / 'Deep32Distribution' / 'relay'
    if platform == 'linux':
        base = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
        if not base.is_absolute():
            raise StateError('state-location-unavailable')
        return base / 'deep32-distribution' / 'relay'
    if platform == 'macos':
        return Path.home() / 'Library/Application Support/Deep32Distribution/relay'
    raise StateError('platform-unavailable')


def _literal(value):
    value = str(value)
    if not value or len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise StateError('invalid-startup-path')
    return value


@dataclass(frozen=True)
class ServiceSpec:
    install_id: str
    python: str
    script: str
    root: str
    platform: str

    def __post_init__(self):
        if not valid_id(self.install_id) or self.platform not in ('windows', 'linux', 'macos'):
            raise StateError('invalid-startup-spec')
        # PureWindowsPath is used by fake-platform tests running on other hosts.
        from pathlib import PurePosixPath, PureWindowsPath
        for value in (self.python, self.script, self.root):
            _literal(value)
            if not (PureWindowsPath(value).is_absolute() if self.platform == 'windows' else PurePosixPath(value).is_absolute()):
                raise StateError('invalid-startup-path')

    @property
    def name(self):
        return 'deep32-relay-' + self.install_id

    @property
    def argv(self):
        return (self.python, '-I', '-B', self.script, '--root', self.root)


def render(spec):
    """Return filename and literal bytes; never shell-expand a path."""
    if spec.platform == 'windows':
        ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
        ET.register_namespace('', ns)
        def node(parent, name, value=None):
            child = ET.SubElement(parent, '{' + ns + '}' + name)
            if value is not None:
                child.text = value
            return child
        task = ET.Element('{' + ns + '}Task', {'version': '1.4'})
        info = node(task, 'RegistrationInfo')
        node(info, 'URI', '\\Deep32Distribution\\' + spec.name)
        triggers = node(task, 'Triggers')
        node(node(triggers, 'LogonTrigger'), 'Enabled', 'true')
        principals = node(task, 'Principals')
        principal = node(principals, 'Principal')
        principal.set('id', 'CurrentUser')
        node(principal, 'LogonType', 'InteractiveToken')
        node(principal, 'RunLevel', 'LeastPrivilege')
        settings = node(task, 'Settings')
        node(settings, 'MultipleInstancesPolicy', 'IgnoreNew')
        node(settings, 'Hidden', 'true')
        node(settings, 'ExecutionTimeLimit', 'PT0S')
        node(settings, 'AllowHardTerminate', 'true')
        actions = node(task, 'Actions')
        actions.set('Context', 'CurrentUser')
        executable = node(actions, 'Exec')
        node(executable, 'Command', spec.python)
        node(executable, 'Arguments', subprocess.list2cmdline(spec.argv[1:]))
        node(executable, 'WorkingDirectory', spec.root)
        return spec.name + '.xml', ET.tostring(task, encoding='utf-8', xml_declaration=True)
    if spec.platform == 'linux':
        # systemd specifiers and environment expansion are escaped independently
        # of quoting. ExecStart is parsed by systemd itself, with no shell.
        def word(value):
            return '"' + _literal(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
        lines = ['[Unit]', 'Description=Deep32 relay ' + spec.install_id,
                 'StartLimitIntervalSec=60', 'StartLimitBurst=3', '[Service]', 'Type=simple',
                 'ExecStart=' + ' '.join(word(value) for value in spec.argv),
                 # WorkingDirectory takes the rest of the line as one path: systemd rejects quotes there.
                 'WorkingDirectory=' + _literal(spec.root).replace('%', '%%'), 'UMask=0077', 'Restart=on-failure',
                 'RestartSec=10', 'TimeoutStopSec=5', 'KillMode=control-group',
                 'NoNewPrivileges=yes', '[Install]', 'WantedBy=default.target', '']
        return spec.name + '.service', '\n'.join(lines).encode('utf-8')
    value = {'Label': 'org.deep32.distribution.' + spec.name,
             'ProgramArguments': list(spec.argv), 'WorkingDirectory': spec.root,
             'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False},
             'ThrottleInterval': 10, 'ExitTimeOut': 5, 'Umask': 0o077,
             'ProcessType': 'Background'}
    return spec.name + '.plist', plistlib.dumps(value, sort_keys=True)


class UnqualifiedPlatformAPI:
    """No native setters or process-start capability is available by default."""
    qualified = False

    def inspect(self, name):
        return None

    def register(self, name, definition):
        raise StateError('autostart-unqualified')

    def start(self, name):
        raise StateError('autostart-unqualified')

    def stop(self, name):
        raise StateError('autostart-unqualified')

    def unregister(self, name):
        raise StateError('autostart-unqualified')


class PlatformAdapter:
    def __init__(self, api=None):
        self.api = api or UnqualifiedPlatformAPI()

    def register(self, spec, definition):
        if not self.api.qualified:
            return False
        current = self.api.inspect(spec.name)
        if current is not None and current != definition:
            raise StateError('foreign-startup-entry')
        if current is None:
            self.api.register(spec.name, definition)
        if self.api.inspect(spec.name) != definition:
            raise StateError('startup-verification-failed')
        return True

    def start(self, spec, definition):
        # Registration does not start the service. The connector calls this only
        # after the receipt is committed and the operation lock is released;
        # a service started earlier fails its owned-state checks and Windows does
        # not retry it, while systemd retries only three times in 60 seconds.
        if not self.api.qualified:
            return False
        if self.api.inspect(spec.name) != definition:
            raise StateError('foreign-startup-entry')
        self.api.start(spec.name)
        return True

    def remove(self, spec, definition):
        if not self.api.qualified:
            raise StateError('autostart-unqualified')
        current = self.api.inspect(spec.name)
        if current is None:
            return
        if current != definition:
            raise StateError('foreign-startup-entry')
        # R15 stops the exact receipt-owned native entry, then reads removal
        # back. It does not require a complete descendant-scope observer.
        if self.api.stop(spec.name) is not True:
            raise StateError('cleanup-failed')
        if self.api.inspect(spec.name) != definition:
            raise StateError('foreign-startup-entry')
        self.api.unregister(spec.name)
        if self.api.inspect(spec.name) is not None:
            raise StateError('startup-verification-failed')


class CommandRunner:
    """Bounded, literal native calls. No shell, inherited secrets or output logs."""
    def __call__(self, argv, *, input=None, allowed=(0,)):
        env = {key: value for key, value in os.environ.items()
               if key in ('SystemRoot', 'WINDIR', 'HOME', 'USERPROFILE', 'LOCALAPPDATA',
                          'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'LANG', 'LC_ALL')}
        try:
            result = subprocess.run(list(argv), input=input, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=10, check=False,
                                    env=env, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            raise StateError('platform-operation-failed') from None
        diagnostic = getattr(result, 'stderr', b'') or b''
        if len(result.stdout) > 1024 * 1024 or len(diagnostic) > 1024 * 1024:
            raise StateError('platform-operation-failed')
        if result.returncode not in allowed:
            # Inspect only this known Serve failure; never publish native text.
            if (sys.platform.startswith('linux') and len(argv) > 1 and argv[1] == 'serve'
                    and b'access denied: serve config denied' in (result.stdout + diagnostic).lower()):
                raise StateError('tailscale-operator-required')
            raise StateError('platform-operation-failed')
        return result.returncode, result.stdout


def request_stop(owner, install_id):
    """Request cooperative shutdown; service lease/native readback is separate."""
    from .status import read_status
    import time
    snapshot = read_status(owner)['snapshot']
    if snapshot is None:
        return False
    def clean(value):
        return (value is not None and value['pid'] == 0 and not value['running'] and
                value['link_state'] == 'off' and value['session_state'] in ('none', 'closed') and
                value['queue_depth'] == 0 and value['last_session_result']['reason'] != 'cleanup-failed')
    if clean(snapshot):
        return True
    owner.write_json('runtime/stop-request.json',
                     {'schema': 'deep32.relay-stop.v1', 'install_id': install_id})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if clean(read_status(owner)['snapshot']):
            return True
        time.sleep(0.05)
    return False


class NativePlatformAPI:
    """Per-user native startup owner, never used by read-only status.

    Construct only for an explicit lifecycle action. Native host qualification
    remains a release gate; injected runners prove command and ownership logic.
    Windows uses Scheduler's current-user InteractiveToken principal, no secret.
    """
    qualified = True

    def __init__(self, owner, spec, definition, *, runner=None, home=None, uid=None,
                 stopped=None):
        self.owner, self.spec, self.definition = owner, spec, definition
        self.runner = runner or CommandRunner()
        self.home = Path(home) if home is not None else Path.home()
        self.uid = uid if uid is not None else (os.getuid() if hasattr(os, 'getuid') else None)
        # Optional fake stop boundary; production stops the exact native entry.
        self.stopped = stopped
        self.native_stopped = False
        self.native_definition = definition
        if spec.platform == 'windows':
            sid = getattr(owner.security, 'sid_text', None)
            if sid is None or not re.fullmatch(r'S-1-[0-9-]{1,180}', sid):
                # An injected fake may use the literal definition. Production
                # always has WindowsSecurity's verified CurrentUser SID.
                if runner is None:
                    raise StateError('acl-unavailable')
            else:
                ns = '{http://schemas.microsoft.com/windows/2004/02/mit/task}'
                task = ET.fromstring(definition)
                for parent in (task.find('.//' + ns + 'Principal'), task.find('.//' + ns + 'LogonTrigger')):
                    ET.SubElement(parent, ns + 'UserId').text = sid
                self.native_definition = ET.tostring(task, encoding='utf-8', xml_declaration=True)
        self.filename = render(spec)[0]
        self.location = (self.home / '.config/systemd/user' / self.filename if spec.platform == 'linux'
                         else self.home / 'Library/LaunchAgents' / self.filename)

    def _check_name(self, name):
        if name != self.spec.name:
            raise StateError('foreign-startup-entry')

    def _ps(self, operation, definition=None):
        # Script text is fixed except validated hexadecimal installation names.
        # XML travels on stdin, never argv, a script file or a shell expression.
        import base64
        task = self.spec.name
        body = "$ErrorActionPreference='Stop';[Console]::InputEncoding=[Text.UTF8Encoding]::new($false);[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$s=New-Object -ComObject Schedule.Service;$s.Connect();"
        body += "$n='" + task + "';"
        body += "try{$f=$s.GetFolder('\\Deep32Distribution')}catch{if(($_.Exception.HResult -band 65535)-ne 2){throw};"
        if operation == 'register':
            body += "$f=$s.GetFolder('\\').CreateFolder('Deep32Distribution')}"
        else:
            body += "Write-Output 'ABSENT';exit 0}"
        if operation == 'inspect':
            body += "try{$t=$f.GetTask($n);[Console]::Write($t.Xml)}catch{if(($_.Exception.HResult -band 65535)-eq 2){Write-Output 'ABSENT'}else{throw}}"
        elif operation == 'register':
            # TASK_CREATE (2), TASK_LOGON_INTERACTIVE_TOKEN (3); never overwrite.
            # Registration only: 'start' runs the task once the install is committed.
            body += "$x=[Console]::In.ReadToEnd();$null=$f.RegisterTask($n,$x,2,$null,$null,3,$null)"
        elif operation == 'start':
            # TASK_STATE_RUNNING (4): IgnoreNew already keeps one instance; do not ask twice.
            body += "$t=$f.GetTask($n);if($t.State -ne 4){$null=$t.Run($null)};Write-Output 'STARTED'"
        elif operation == 'unregister':
            body += "$f.DeleteTask($n,0)"
        elif operation == 'stop':
            body += "$t=$f.GetTask($n);$t.Stop(0);$end=[DateTime]::UtcNow.AddSeconds(5);while($t.GetInstances(0).Count -gt 0){if([DateTime]::UtcNow -ge $end){throw 'stop-timeout'};Start-Sleep -Milliseconds 50};Write-Output 'STOPPED'"
        else:
            raise StateError('invalid-startup-operation')
        executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        code = base64.b64encode(body.encode('utf-16le')).decode('ascii')
        _, raw = self.runner((str(executable), '-NoLogo', '-NoProfile', '-NonInteractive',
                              '-EncodedCommand', code), input=definition)
        return raw

    @staticmethod
    def _xml(value):
        try:
            node = ET.fromstring(value)
            for item in node.iter():
                if item.text is not None and not item.text.strip():
                    item.text = None
                item.tail = None
            return ET.tostring(node)
        except (ValueError, ET.ParseError):
            raise StateError('startup-observation-invalid') from None

    def inspect(self, name):
        self._check_name(name)
        if self.spec.platform == 'windows':
            raw = self._ps('inspect')
            if raw.strip() == b'ABSENT':
                return None
            return self.definition if self._xml(raw) == self._xml(self.native_definition) else raw
        path = safe_path(self.location)
        if not path.exists():
            return None
        self._verify_parent(path.parent)
        self.owner.security.verify(path)
        if path.stat().st_size > 65536:
            raise StateError('startup-observation-invalid')
        return path.read_bytes()

    def _write_definition(self):
        # Only private, current-user directories may hold our startup file.
        path = safe_path(self.location)
        missing = []
        cursor = path.parent
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for directory in reversed(missing):
            self.owner.security.mkdir(directory)
        self._verify_parent(path.parent)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise StateError('foreign-startup-entry') from None
        with os.fdopen(descriptor, 'wb') as stream:
            self.owner.security.protect_file(path)
            stream.write(self.definition)
            stream.flush()
            os.fsync(stream.fileno())

    def _verify_parent(self, path):
        info = safe_path(path).lstat()
        import stat
        if (not stat.S_ISDIR(info.st_mode) or
                (hasattr(os, 'geteuid') and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022))):
            raise StateError('unsafe-startup-directory')

    def register(self, name, definition):
        self._check_name(name)
        if definition != self.definition or self.inspect(name) is not None:
            raise StateError('foreign-startup-entry')
        if self.spec.platform == 'windows':
            self._ps('register', self.native_definition)
            return
        self._write_definition()
        if self.spec.platform == 'linux':
            # Enable only. Starting here would run the service inside the install
            # window (no receipt yet, or the removed one, and the operation lock held).
            self.runner(('/usr/bin/systemctl', '--user', 'daemon-reload'))
            self.runner(('/usr/bin/systemctl', '--user', 'enable', self.filename))
        else:
            # launchd starts RunAtLoad agents at bootstrap; KeepAlive relaunches a
            # failed early start every ThrottleInterval with no start limit.
            if self.uid is None:
                raise StateError('platform-unavailable')
            self.runner(('/bin/launchctl', 'bootstrap', 'gui/' + str(self.uid), str(self.location)))

    def start(self, name):
        self._check_name(name)
        if self.spec.platform == 'windows':
            if self._ps('start').strip() != b'STARTED':
                raise StateError('startup-start-failed')
        elif self.spec.platform == 'linux':
            # Clear a start-limit stop left by an earlier unit of this name. A
            # unit systemd has not loaded answers 1 here; the start must succeed.
            self.runner(('/usr/bin/systemctl', '--user', 'reset-failed', self.filename), allowed=(0, 1))
            self.runner(('/usr/bin/systemctl', '--user', 'start', self.filename))
        # macOS: bootstrap already started the RunAtLoad agent (see register).

    def _cooperative_stop(self):
        return request_stop(self.owner, self.spec.install_id)

    def stop(self, name):
        self._check_name(name)
        if self.stopped is not None:
            return self.stopped() is True
        self._cooperative_stop()
        if self.spec.platform == 'windows':
            if self._ps('stop').strip() not in (b'STOPPED', b'ABSENT'):
                raise StateError('startup-stop-verification-failed')
        elif self.spec.platform == 'linux':
            self.runner(('/usr/bin/systemctl', '--user', 'stop', self.filename))
            code, raw = self.runner(('/usr/bin/systemctl', '--user', 'is-active', self.filename),
                                    allowed=(0, 3, 4))
            if code == 0 or raw.strip() not in (b'inactive', b'failed', b'unknown'):
                raise StateError('startup-stop-verification-failed')
        else:
            target = 'gui/' + str(self.uid) + '/org.deep32.distribution.' + self.spec.name
            self.runner(('/bin/launchctl', 'bootout', target), allowed=(0, 113))
            code, _ = self.runner(('/bin/launchctl', 'print', target), allowed=(0, 113))
            if code != 113:
                raise StateError('startup-stop-verification-failed')
        self.native_stopped = True
        return True

    def unregister(self, name):
        self._check_name(name)
        if self.inspect(name) != self.definition:
            raise StateError('foreign-startup-entry')
        if self.spec.platform == 'windows':
            self._ps('unregister')
            return
        if self.spec.platform == 'linux':
            self.runner(('/usr/bin/systemctl', '--user', 'disable', self.filename))
        elif not self.native_stopped:
            self.runner(('/bin/launchctl', 'bootout', 'gui/' + str(self.uid), str(self.location)))
        if self.inspect(name) != self.definition:
            raise StateError('foreign-startup-entry')
        self.location.unlink()
        if self.spec.platform == 'linux':
            self.runner(('/usr/bin/systemctl', '--user', 'daemon-reload'))


class ServeAPI:
    """Exact raw-TCP Serve commands; never reset, Funnel or TLS termination."""
    def __init__(self, executable, runner=None):
        path = Path(executable)
        if not path.is_absolute():
            raise StateError('absolute-binary-required')
        self.executable = str(safe_path(path))
        self.runner = runner or CommandRunner()

    def inspect(self):
        _, raw = self.runner((self.executable, 'serve', 'status', '--json'))
        return strict_json(raw)

    def add(self, port):
        self.runner((self.executable, 'serve', '--bg', '--tcp=' + str(port),
                     'tcp://127.0.0.1:' + str(port)))

    def remove(self, port):
        self.runner((self.executable, 'serve', '--tcp=' + str(port), 'off'))


class OwnedServeRoute:
    """Journal before mutation; preserve every unrelated route byte for byte."""
    RECORD = 'runtime/serve-route.json'

    def __init__(self, owner, api, port=7452):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise StateError('invalid-relay-port')
        self.owner, self.api, self.port = owner, api, port
        self.ident = owner.validate()
        self.route = {'TCPForward': '127.0.0.1:' + str(port)}

    def _record(self):
        try:
            value = self.owner.read_json(self.RECORD, 4096)
        except FileNotFoundError:
            return None
        if (type(value) is not dict or set(value) != {'schema', 'install_id', 'port', 'phase'} or
                value['schema'] != 'deep32.relay-serve.v1' or value['install_id'] != self.ident or
                value['port'] != self.port or value['phase'] not in ('adding', 'ready', 'removing', 'off')):
            raise StateError('route-ownership-invalid')
        return value

    def _save(self, phase):
        self.owner.write_json(self.RECORD, {'schema': 'deep32.relay-serve.v1',
            'install_id': self.ident, 'port': self.port, 'phase': phase})

    def _table(self):
        table = self.api.inspect()
        if (type(table) is not dict or type(table.get('TCP', {})) is not dict or
                type(table.get('Web', {})) is not dict or type(table.get('AllowFunnel', {})) is not dict):
            raise StateError('serve-state-invalid')
        if any(value is True for value in table.get('AllowFunnel', {}).values()):
            raise StateError('public-route-refused')
        return table

    def _unrelated(self, table):
        value = strict_json(json_bytes(table))
        value.get('TCP', {}).pop(str(self.port), None)
        if value.get('TCP') == {}:
            value.pop('TCP', None)
        return value

    def check(self):
        record = self._record()
        table = self._table()
        current = table.get('TCP', {}).get(str(self.port))
        if current is not None and (record is None or record['phase'] == 'off' or current != self.route):
            raise StateError('foreign-serve-route')
        return {'state': 'ready' if record and record['phase'] == 'ready' and current == self.route else 'unavailable',
                'owned': record is not None and record['phase'] != 'off'}

    def publish(self):
        record, before = self._record(), self._table()
        current = before.get('TCP', {}).get(str(self.port))
        if current is not None and (record is None or record['phase'] == 'off' or current != self.route):
            raise StateError('foreign-serve-route')
        if record and record['phase'] == 'removing':
            raise StateError('route-recovery-required')
        self._save('adding')
        if current is None:
            self.api.add(self.port)
        after = self._table()
        if (after.get('TCP', {}).get(str(self.port)) != self.route or
                self._unrelated(before) != self._unrelated(after)):
            raise StateError('serve-verification-failed')
        self._save('ready')
        return self.check()

    def remove(self):
        record, before = self._record(), self._table()
        current = before.get('TCP', {}).get(str(self.port))
        if current is not None and (record is None or record['phase'] == 'off' or current != self.route):
            raise StateError('foreign-serve-route')
        if record is None or record['phase'] == 'off':
            return {'state': 'off', 'owned': False}
        self._save('removing')
        if current is not None:
            self.api.remove(self.port)
        after = self._table()
        if str(self.port) in after.get('TCP', {}) or self._unrelated(before) != self._unrelated(after):
            raise StateError('serve-verification-failed')
        self._save('off')
        return {'state': 'off', 'owned': False}
