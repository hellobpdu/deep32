#!/usr/bin/env python3
"""Run the disc 1 lab install, collect evidence and compare the installed files.

Uses the existing hypervisor_client client exclusively. Credentials are opened only by that
client, through sendline-file and sendkey --text-file. Run with Python -I -B.
The same command resumes from REPORT.json. Inspect an uncertain failed action
before permitting it again with --retry-step NAME. No VM is destroyed.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

STEPS = ('prepare', 'create', 'install', 'restart', 'unlock', 'firstboot',
         'screenshots', 'collect', 'compare')
MUTATING = {'create', 'install', 'restart', 'unlock', 'collect'}
ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])')


class Failure(Exception):
    """An error safe to put in reports: never includes child output."""


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def write(path, text):
    Path(path).write_text(text, encoding='ascii', newline='\n')


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.new')
    write(temporary, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + '\n')
    os.replace(temporary, path)


def positive(text):
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('use a finite positive number')
    return value


def nonnegative(text):
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError('use a finite nonnegative number')
    return value


def parser():
    here = Path(__file__).resolve().parent
    dist = here.parents[2]
    workstation = dist / 'work/workstation'
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--vmid', required=True, type=int)
    p.add_argument('--image', required=True, help='uploaded d32dist-*.raw or .qcow2 name')
    p.add_argument('--release', required=True, help='release label, letters/digits/dots/dashes')
    p.add_argument('--pass-file', required=True, type=Path)
    p.add_argument('--login-file', required=True, type=Path,
                   help='one line: the test root account password, consumed by sendline-file')
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--reuse', action='store_true', help='permit a pre-existing VM with the exact run name')
    p.add_argument('--retry-step', choices=STEPS, help='permit a failed/interrupted action after inspecting it')
    p.add_argument('--stop-after', choices=STEPS, help='save a clean checkpoint and stop')
    p.add_argument('--python', default=sys.executable)
    p.add_argument('--client', type=Path, default=dist / 'lab/design/hypervisor_client/d32dist-hypervisor_client.py')
    p.add_argument('--compare', type=Path, default=here / 'asset-compare.py')
    p.add_argument('--collector', type=Path, default=here / 'collect-files.sh')
    p.add_argument('--golden', type=Path, default=dist / 'inventory/BASELINE-MODES.tsv')
    p.add_argument('--cuts', type=Path, default=workstation / 'CUTS.tsv')
    p.add_argument('--additions', type=Path, default=workstation / 'ADDITIONS.tsv')
    p.add_argument('--changed', type=Path)
    p.add_argument('--source-map', type=Path)
    p.add_argument('--ledger', type=Path)
    p.add_argument('--install-written', type=Path)
    p.add_argument('--command-timeout', type=positive, default=120)
    p.add_argument('--install-timeout', type=positive, default=4200)
    p.add_argument('--boot-timeout', type=positive, default=600)
    p.add_argument('--collect-timeout', type=positive, default=1200)
    p.add_argument('--vga-wait', type=nonnegative, default=35)
    p.add_argument('--poll-seconds', type=positive, default=5)
    p.add_argument('--read-seconds', type=positive, default=3600,
                   help='maximum length of one continuous firstboot console read')
    p.add_argument('--key-delay', type=nonnegative, default=0.05)
    p.add_argument('--target-gb', type=int, default=40)
    return p


def dialog_text(command):
    """Escape a public shell command for the client's existing dialog grammar."""
    return command.replace('\\', '\\\\').replace('\r', '\\r').replace('\n', '\\n').replace('\t', '\\t')


def decode_bytes(text):
    """Inverse of the client's esc_bytes: only \\\\ and \\xHH are escapes."""
    result = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\\':
            if text[i:i + 2] == '\\\\':
                result.append(92)
                i += 2
                continue
            if re.fullmatch(r'\\x[0-9a-fA-F]{2}', text[i:i + 4]):
                result.append(int(text[i + 2:i + 4], 16))
                i += 4
                continue
            raise Failure('invalid client transcript escape')
        if ord(c) > 127:
            raise Failure('invalid client transcript encoding')
        result.append(ord(c))
        i += 1
    return bytes(result)


def received(logdir):
    chunks = []
    for path in sorted(logdir.glob('*.log')):
        for line in path.read_text(encoding='ascii').splitlines():
            parts = line.split(' ', 2)
            if len(parts) == 3 and parts[1] in ('<', '<+'):
                chunks.append(decode_bytes(parts[2]))
                if parts[1] == '<':
                    chunks.append(b'\n')
    return ANSI.sub('', b''.join(chunks).decode('utf-8', 'replace')).replace('\r', '')


def collect_frame(text):
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith('D32COLLECT/1 BEGIN ')]
    if len(starts) != 1:
        raise Failure('collector must return exactly one BEGIN frame')
    start = starts[0]
    ends = [i for i in range(start + 1, len(lines)) if lines[i].startswith('D32COLLECT/1 END ')]
    if len(ends) != 1:
        raise Failure('collector did not return exactly one END frame')
    end = ends[0]
    match = re.fullmatch(r'D32COLLECT/1 END rows=([0-9]+)', lines[end])
    body = lines[start + 1:end]
    if not match or int(match[1]) != len(body):
        raise Failure('collector row count does not match END')
    seen = set()
    for line in body:
        cells = line.split('\t')
        valid = (len(cells) == 4 and cells[0] == 'F' and re.fullmatch('[0-9a-f]{64}', cells[3]))
        valid = valid or (len(cells) == 4 and cells[0] == 'L' and cells[3].startswith('->'))
        valid = valid or (len(cells) == 2 and cells[0] in ('D', 'SKIP', 'ABSENT'))
        if not valid or not cells[1].startswith('/') or cells[1] in seen:
            raise Failure('collector returned an invalid or duplicate row')
        seen.add(cells[1])
    if not re.search(r'^D32RUN/1 exit=0$', text, re.MULTILINE):
        raise Failure('collector did not confirm successful execution')
    return '\n'.join(lines[start:end + 1]) + '\n', len(body)


class Run:
    def __init__(self, a):
        self.a = a
        self.out = a.out.resolve()
        self.name = 'd32-dist-disc1-%s-%d' % (a.release, a.vmid)
        self.active = None
        self.artifacts = []
        self.record = None
        self.out.mkdir(parents=True, exist_ok=True)
        for name in ('logs', 'console', 'screenshots', 'dialogs'):
            (self.out / name).mkdir(exist_ok=True)
        self.state_path = self.out / 'REPORT.json'
        self.fingerprint = self.inputs()
        if self.state_path.exists():
            self.record = json.loads(self.state_path.read_text(encoding='ascii'))
            if self.record.get('schema') != 1 or self.record.get('inputs') != self.fingerprint:
                raise Failure('resume inputs changed; use a new output folder for a new run')
            for step in self.record['steps']:
                if step['status'] == 'done':
                    for item in step.get('artifacts', []):
                        path = self.out / item['path']
                        if not path.is_file() or digest(path) != item['sha256']:
                            raise Failure('saved evidence changed; restore it before resuming')
        else:
            self.record = {'schema': 1, 'started': stamp(), 'status': 'running',
                           'inputs': self.fingerprint, 'steps': [], 'd32s': [],
                           'screenshots': [], 'golden_compare': None}
        self.save()

    def inputs(self):
        a = self.a
        data = {'vmid': a.vmid, 'image': a.image, 'release': a.release, 'name': self.name,
                'reuse': a.reuse, 'target_gb': a.target_gb,
                'pass_file': str(a.pass_file.resolve()), 'login_file': str(a.login_file.resolve()),
                'python': str(a.python), 'files': {
                    'driver': {'path': str(Path(__file__).resolve()), 'sha256': digest(__file__)}}}
        # Credentials are deliberately neither opened nor hashed by this driver.
        for key in ('client', 'compare', 'collector', 'golden', 'cuts', 'additions',
                    'changed', 'source_map', 'ledger', 'install_written'):
            value = getattr(a, key)
            if value:
                path = value.resolve()
                if not path.is_file():
                    raise Failure('missing input file for --' + key.replace('_', '-'))
                data['files'][key] = {'path': str(path), 'sha256': digest(path)}
        for value in (a.pass_file, a.login_file):
            if not value.is_file() or '\n' in str(value) or '\r' in str(value):
                raise Failure('credential path must name a file and contain no line break')
        return data

    def save(self):
        self.record['updated'] = stamp()
        atomic_json(self.state_path, self.record)
        lines = ['# Disc 1 install test', '', 'Status: ' + self.record['status'], '',
                 'VM: %d; release: %s; image: %s' % (self.a.vmid, self.a.release, self.a.image), '',
                 '| Step | Status | Started (UTC) | Seconds | Detail |',
                 '| --- | --- | --- | --- | --- |']
        for step in self.record['steps']:
            lines.append('| %s | %s | %s | %s | %s |' %
                         (step['name'], step['status'], step['started'], step.get('seconds', ''),
                          step.get('detail', '').replace('|', '/').replace('\n', ' ')))
        lines += ['', '## D32S status lines', '']
        lines += ['    ' + s['line'] for s in self.record['d32s']] or ['No D32S lines recorded.']
        lines += ['', '## Screenshots', '']
        lines += ['- ' + p for p in self.record['screenshots']] or ['No screenshots recorded.']
        lines += ['', '## Golden comparison', '',
                  json.dumps(self.record['golden_compare'], sort_keys=True, ensure_ascii=True), '',
                  'Resume with the same command and output folder. Inspect an uncertain action',
                  'before authorising --retry-step NAME. Keep all evidence in this folder.', '']
        write(self.out / 'REPORT.md', '\n'.join(lines).encode('ascii', 'backslashreplace').decode('ascii'))

    def artifact(self, path):
        path = Path(path).resolve()
        self.artifacts.append({'path': str(path.relative_to(self.out)).replace(os.sep, '/'),
                               'sha256': digest(path), 'bytes': path.stat().st_size})

    def command(self, args, timeout=None, accepted=(0,)):
        started = stamp()
        clock = time.monotonic()
        result = None
        code = 'timeout'
        try:
            result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=timeout or self.a.command_timeout,
                                    check=False)
            code = result.returncode
        except subprocess.TimeoutExpired:
            raise Failure('command timed out; inspect the saved console evidence before retrying')
        except OSError:
            code = 'launch-error'
            raise Failure('cannot launch a required tool')
        finally:
            entry = {'step': self.active, 'started': started, 'seconds': round(time.monotonic() - clock, 3),
                     'argv': [str(x) for x in args], 'exit': code}
            with open(self.out / 'logs/commands.jsonl', 'a', encoding='ascii', newline='\n') as stream:
                stream.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + '\n')
        if code not in accepted:
            raise Failure('tool returned exit %s; inspect console evidence and step recovery instructions' % code)
        return result

    def hypervisor_client(self, *args, timeout=None, accepted=(0,)):
        return self.command([self.a.python, '-I', '-B', self.a.client, *args],
                            timeout=timeout, accepted=accepted)

    def vm(self):
        result = self.hypervisor_client('--json', 'list')
        try:
            rows = json.loads(result.stdout.decode('utf-8'))
            if not isinstance(rows, list):
                raise ValueError()
            matches = [r for r in rows if int(r['vmid']) == self.a.vmid]
            if len(matches) > 1:
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise Failure('client list did not return an unambiguous JSON VM list')
        if not matches:
            return None
        row = matches[0]
        if row.get('name') != self.name:
            raise Failure('VM name differs from this run; refuse to touch it')
        if row.get('status') not in ('running', 'stopped'):
            raise Failure('VM is in an unsupported state; inspect it before continuing')
        return row

    def wait_vm(self, desired):
        deadline = time.monotonic() + self.a.command_timeout
        while True:
            row = self.vm()
            if row and row['status'] == desired:
                return
            if time.monotonic() >= deadline:
                raise Failure('VM did not reach the required power state before timeout')
            time.sleep(min(self.a.poll_seconds, max(0, deadline - time.monotonic())))

    def console(self, script=None, seconds=None, timeout=None, until=None):
        directory = self.out / 'console' / ('%s-%d-%d' %
                     (self.active, len(self.record['steps']), time.monotonic_ns()))
        directory.mkdir()
        args = ['console', '--vmid', str(self.a.vmid), '--log-dir', str(directory), '--drain', '0']
        args += ['--script', str(script)] if script else ['--read', str(seconds)]
        if until:
            args += ['--until', until]
        try:
            self.hypervisor_client(*args, timeout=timeout, accepted=(0, 6) if until else (0,))
        finally:
            for path in sorted(directory.glob('*.log')):
                self.artifact(path)
            text = received(directory)
            self.d32s(text)
        if not list(directory.glob('*.log')):
            raise Failure('client did not produce a console transcript')
        return text

    def d32s(self, text):
        for line in text.splitlines():
            match = re.search(r'\bD32S/1\b.*', line)
            if match:
                self.record['d32s'].append({'step': self.active, 'time': stamp(), 'line': match[0]})
        self.save()

    def screenshot(self, label):
        path = self.out / 'screenshots' / ('%s-%d-%d.png' %
               (label, len(self.record['steps']), time.monotonic_ns()))
        try:
            self.hypervisor_client('screenshot', '--vmid', str(self.a.vmid), '--out', path)
        finally:
            if path.is_file():
                self.artifact(path)
                relative = str(path.relative_to(self.out)).replace(os.sep, '/')
                self.record['screenshots'].append(relative)
                self.save()
        if not path.is_file() or path.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
            raise Failure('client screenshot did not produce a PNG')
        return digest(path)

    def common_compare(self):
        args = ['--golden', self.a.golden, '--cuts', self.a.cuts, '--additions', self.a.additions]
        if self.a.ledger:
            args += ['--ledger', self.a.ledger]
        return args

    def prepare(self):
        self.command([self.a.python, '-I', '-B', self.a.compare, 'scope', *self.common_compare(),
                      '--out', self.out / 'scope.txt', '--bundle', self.out / 'collect-run.sh',
                      '--collector', self.a.collector])
        self.artifact(self.out / 'scope.txt')
        self.artifact(self.out / 'collect-run.sh')
        # No secret-bearing source dialog is read or copied. Its existing grammar
        # is instantiated with the operator's run-time paths.
        timeout = min(3600, self.a.install_timeout)
        lines = [r'expect (?i)New passphrase\? \(will not echo\) timeout %s' % timeout,
                 'sendline-file ' + str(self.a.pass_file.resolve()),
                 r'expect (?i)(?:New passphrase\? \((?:will not echo|again)\)|(?:re-?type|repeat|confirm)[^\n]*passphrase[^\n]*) timeout 120',
                 'sendline-file ' + str(self.a.pass_file.resolve()),
                 r'expect (?i)(?:halted|Please press any key to reboot) timeout %s' % timeout]
        write(self.out / 'dialogs/install.dialog', '\n'.join(lines) + '\n')
        self.artifact(self.out / 'dialogs/install.dialog')
        return 'Scope bundle and installer dialog prepared; input hashes pinned.'

    def create(self):
        row = self.vm()
        if row:
            if not self.a.reuse:
                raise Failure('VM already exists; use --reuse only after checking its run name and disk')
            return 'Reused the explicitly authorised VM with the exact run name.'
        self.hypervisor_client('create-boot', '--vmid', str(self.a.vmid), '--name', self.name,
                 '--image', self.a.image, '--profile', 'openbsd', '--target-gb', str(self.a.target_gb))
        self.wait_vm('stopped')
        return 'Created the test VM from the uploaded image.'

    def install(self):
        row = self.vm()
        if not row:
            raise Failure('test VM is absent')
        if row['status'] == 'stopped':
            self.hypervisor_client('start', '--vmid', str(self.a.vmid))
            self.wait_vm('running')
        text = self.console(script=self.out / 'dialogs/install.dialog', timeout=self.a.install_timeout)
        if not re.search(r'(?i)halted|Please press any key to reboot', text):
            raise Failure('installer transcript did not confirm halt')
        return 'Installer reached halt; the VM may still report running.'

    def restart(self):
        row = self.vm()
        if not row:
            raise Failure('test VM is absent')
        if row['status'] == 'running':
            self.hypervisor_client('stop', '--vmid', str(self.a.vmid))
        self.wait_vm('stopped')
        self.hypervisor_client('start', '--vmid', str(self.a.vmid))
        self.wait_vm('running')
        return 'Stopped the halted VM and started its installed disk.'

    def unlock(self):
        first = self.screenshot('unlock-boot-loader')
        time.sleep(self.a.vga_wait)
        second = self.screenshot('unlock-before-keys')
        # PNG differences cannot identify text. Preserve both as evidence and
        # explicitly use the measured lab delay rather than claiming OCR.
        self.record['vga_wait'] = {'method': 'fixed lab delay; no prompt recognition',
                                   'seconds': self.a.vga_wait, 'screenshots_differ': first != second}
        self.save()
        self.hypervisor_client('sendkey', '--vmid', str(self.a.vmid), '--text-file', self.a.pass_file.resolve(),
                 '--enter', '--delay', str(self.a.key_delay))
        return 'Used a %.1f s fixed lab delay; screenshot changes are evidence only.' % self.a.vga_wait

    def firstboot(self):
        deadline = time.monotonic() + self.a.boot_timeout
        while time.monotonic() < deadline:
            seen = set()
            for item in self.record['d32s']:
                tokens = dict(re.findall(r'\b([a-z]+)=([^\s]+)', item['line']))
                if tokens.get('state') == 'done':
                    seen.add(tokens.get('stage'))
            if {'firstboot', 'keys'} <= seen:
                return 'Observed firstboot done and keys done in D32S/1 records.'
            missing = sorted({'firstboot', 'keys'} - seen)
            pattern = r'(?s)\A' + ''.join(
                r'(?=.*D32S/1(?=[^\n]*\bstage=%s\b)(?=[^\n]*\bstate=done\b))' % stage
                for stage in missing)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                self.console(seconds=min(self.a.read_seconds, 3600, remaining),
                             timeout=remaining, until=pattern)
            except Failure:
                if time.monotonic() >= deadline:
                    break
                raise
        # A timed-out read can contain the last required line just before close.
        seen = set()
        for item in self.record['d32s']:
            tokens = dict(re.findall(r'\b([a-z]+)=([^\s]+)', item['line']))
            if tokens.get('state') == 'done':
                seen.add(tokens.get('stage'))
        if {'firstboot', 'keys'} <= seen:
            return 'Observed firstboot done and keys done in D32S/1 records.'
        raise Failure('firstboot timed out before both required D32S/1 done records')

    def screenshots(self):
        self.screenshot('screenshots-login-greeter')
        return 'Saved the VGA screen after the required firstboot records.'

    def collect(self):
        bundle = (self.out / 'collect-run.sh').read_bytes()
        sha = hashlib.sha256(bundle).hexdigest()
        commands = []
        def send(command):
            commands.append('sendline ' + dialog_text(command))
        commands += ['sendline', r'expect (?i)login:\s*$ timeout 60', 'sendline root',
                     r'expect (?i)password:\s*$ timeout 60',
                     'sendline-file ' + str(self.a.login_file.resolve()),
                     r'expect (?:^|\n)[^\n]*[#\$] ?$ timeout 60']
        send("stty -echo; PS1=''; umask 077; D32TMP=$(mktemp -d /tmp/legacy-test-node.XXXXXXXXXX) && "
             "test -d \"$D32TMP\" && printf '\\nD32READY/1\\n'")
        commands.append(r'expect ^D32READY/1$ timeout 30')
        send("trap 'stty echo; rm -rf \"$D32TMP\"' 0")
        send(': > "$D32TMP/collect.sh"')
        # 24 source bytes keep each command below the serial canonical line limit.
        for offset in range(0, len(bundle), 24):
            octal = ''.join('\\0%03o' % byte for byte in bundle[offset:offset + 24])
            chunk = offset // 24
            send("printf '%b' '" + octal + "' >> \"$D32TMP/collect.sh\" && "
                 "printf '\\nD32CHUNK/1 %d\\n'" % chunk)
            commands.append('expect ^D32CHUNK/1 %d$ timeout 30' % chunk)
        send('D32HASH=' + sha)
        send('test "$(sha256 -q "$D32TMP/collect.sh")" = "$D32HASH" && '
             "printf '\\nD32UPLOAD/1 OK sha256=%s\\n' \"$D32HASH\"")
        commands.append('expect ^D32UPLOAD/1 OK sha256=%s$ timeout 60' % sha)
        send('sh "$D32TMP/collect.sh"; D32RC=$?; printf \'\\nD32RUN/1 exit=%s\\n\' "$D32RC"')
        commands.append(r'expect ^D32COLLECT/1 END rows=[0-9]+$ timeout %s' % min(3600, self.a.collect_timeout))
        commands.append(r'expect ^D32RUN/1 exit=0$ timeout 30')
        send('stty echo; rm -rf "$D32TMP"; trap - 0; exit')
        commands.append(r'expect (?i)login:\s*$ timeout 60')
        script = self.out / 'dialogs/collect.dialog'
        write(script, '\n'.join(commands) + '\n')
        self.artifact(script)
        text = self.console(script=script, timeout=self.a.collect_timeout)
        if 'D32UPLOAD/1 OK sha256=' + sha not in text:
            raise Failure('console did not confirm the transferred bundle hash')
        frame, rows = collect_frame(text)
        (self.out / 'installed.tsv').write_text(frame, encoding='utf-8', newline='\n')
        self.artifact(self.out / 'installed.tsv')
        self.record['collector'] = {'rows': rows, 'bundle_sha256': sha, 'logged_out': True}
        return 'Checked the transferred bundle hash; captured %d rows and logged out.' % rows

    def compare(self):
        args = [self.a.python, '-I', '-B', self.a.compare, 'compare', *self.common_compare(),
                '--installed', self.out / 'installed.tsv', '--out-dir', self.out / 'compare']
        for key in ('changed', 'source_map', 'install_written'):
            if getattr(self.a, key):
                args += ['--' + key.replace('_', '-'), getattr(self.a, key)]
        result = self.command(args, accepted=(0, 1))
        summary_path = self.out / 'compare/SUMMARY.json'
        try:
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise Failure('golden comparison did not write a valid summary')
        expected = 'PASS' if result.returncode == 0 else 'FAIL'
        if summary.get('verdict') != expected:
            raise Failure('golden summary verdict disagrees with the tool exit code')
        self.record['golden_compare'] = summary
        self.artifact(summary_path)
        self.artifact(self.out / 'compare/COMPARE.tsv')
        return 'Golden comparison: ' + expected + '.'

    def run(self):
        for name in STEPS:
            prior = [s for s in self.record['steps'] if s['name'] == name]
            if prior and prior[-1]['status'] == 'done':
                if self.a.stop_after == name:
                    self.record['status'] = 'checkpoint'
                    self.save()
                    return 0
                continue
            if prior and name in MUTATING and self.a.retry_step != name:
                raise Failure('inspect uncertain step %s, then permit --retry-step %s if appropriate' % (name, name))
            self.active = name
            self.artifacts = []
            clock = time.monotonic()
            step = {'name': name, 'status': 'running', 'started': stamp(), 'attempt': len(prior) + 1,
                    'settings': {key: getattr(self.a, key) for key in
                                 ('command_timeout', 'install_timeout', 'boot_timeout', 'collect_timeout',
                                  'vga_wait', 'poll_seconds', 'read_seconds', 'key_delay')}}
            self.record['steps'].append(step)
            self.record['status'] = 'running'
            self.save()
            print('%s: starting (attempt %d)' % (name, step['attempt']), flush=True)
            try:
                detail = getattr(self, name)()
                step.update(status='done', detail=detail)
            except (Failure, OSError, ValueError) as error:
                detail = str(error) if isinstance(error, Failure) else 'local evidence or input operation failed'
                step.update(status='failed', detail=detail)
                self.record['status'] = 'failed'
                raise Failure(name + ': ' + detail)
            finally:
                step['finished'] = stamp()
                step['seconds'] = round(time.monotonic() - clock, 3)
                step['artifacts'] = self.artifacts
                self.save()
            print('%s: %s' % (name, detail), flush=True)
            if self.a.stop_after == name:
                self.record['status'] = 'checkpoint'
                self.save()
                return 0
        verdict = (self.record['golden_compare'] or {}).get('verdict')
        self.record['status'] = 'done' if verdict == 'PASS' else 'failed'
        self.record['finished'] = stamp()
        self.save()
        return 0 if verdict == 'PASS' else 1


def main(argv=None):
    a = parser().parse_args(argv)
    # This fence runs before creating output or reading any supplied file.
    if not 920 <= a.vmid <= 929:
        print('REFUSED: test VMID must be in 920-929', file=sys.stderr)
        return 2
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,47}', a.release):
        print('REFUSED: invalid release label', file=sys.stderr)
        return 2
    if not re.fullmatch(r'd32dist-[A-Za-z0-9_.-]+\.(?:raw|qcow2)', a.image):
        print('REFUSED: image must be an uploaded d32dist raw or qcow2 name', file=sys.stderr)
        return 2
    if a.target_gb <= 0 or a.read_seconds > 3600:
        print('REFUSED: invalid disk size or console read duration', file=sys.stderr)
        return 2
    # Existing classification files are used unless explicitly overridden.
    for key, filename in (('changed', 'CHANGED.tsv'), ('source_map', 'SOURCE-MAP.tsv')):
        candidate = a.cuts.parent / filename
        if getattr(a, key) is None and candidate.is_file():
            setattr(a, key, candidate)
    lock = a.out.resolve() / '.run-lock'
    owned = False
    try:
        a.out.mkdir(parents=True, exist_ok=True)
        try:
            with open(lock, 'x', encoding='ascii') as stream:
                stream.write(str(os.getpid()) + '\n')
            owned = True
        except FileExistsError:
            raise Failure('output folder is locked; confirm the previous process has stopped before removing .run-lock')
        run = Run(a)
        return run.run()
    except (Failure, OSError, ValueError, KeyError) as error:
        message = str(error) if isinstance(error, Failure) else 'local input or report error; inspect the output folder'
        print('FAILED: ' + message, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('INTERRUPTED: evidence saved; inspect the current action before retrying', file=sys.stderr)
        return 130
    finally:
        if owned:
            lock.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
