#!/usr/bin/env python3
"""C6 status encoder and atomic current-state writer. No lasting secrets accepted."""
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading


def command(argv, status, timeout=600, run=None):
    """Run one bounded command, keeping its waiting reason visible every ten seconds."""
    complete, errors = threading.Event(), []
    def execute():
        try:
            runner = subprocess.run if run is None else run
            runner(argv, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        except Exception as exc:
            errors.append(exc)
        finally:
            complete.set()
    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    while not complete.wait(10):
        status.repeat()
    worker.join()
    if errors:
        raise errors[0]

FIELDS = {'boot', 'stage', 'state', 'version', 'name', 'signin', 'pair', 'pair_with', 'expires', 'error', 'msg'}
# mark shortened message details while reserving CR LF space.
TRUNCATION = '[truncated]'
SHORT_TRUNCATION = '~'
MAX_BODY = 510

STAGES = {'install', 'firstboot', 'keys', 'signin', 'tailnet', 'content', 'import', 'pairing', 'ready'}


def encode(seq, node, state):
    if type(seq) is not int or seq < 1 or node not in {'forge', 'workstation', 'headless'}:
        raise ValueError('invalid sequence or node')
    if set(state) - FIELDS or not {'boot', 'stage', 'state'} <= set(state):
        raise ValueError('unknown or missing status field')
    if not re.fullmatch('[0-9a-f]{8}', state['boot']):
        raise ValueError('invalid boot id')
    if state['stage'] not in STAGES or state['state'] not in {'running', 'waiting', 'done', 'failed'}:
        raise ValueError('invalid stage or state')
    if 'signin' in state and (state['stage'], state['state']) != ('signin', 'waiting'):
        raise ValueError('sign-in address outside waiting stage')
    if 'pair' in state and ((state['stage'], state['state']) != ('pairing', 'waiting') or
                            not 1 <= int(state.get('expires', 0)) <= 300):
        raise ValueError('pairing code outside its window')
    if 'signin' in state and not re.fullmatch(r'https://login\.tailscale\.com/[A-Za-z0-9/_?=.-]+', state['signin']):
        raise ValueError('unexpected sign-in address')
    if 'pair' in state and not re.fullmatch('[A-Z2-7]{10}', state['pair']):
        raise ValueError('invalid pairing code')
    parts = ['D32S/1', str(seq), node]
    for key in sorted(set(state) - {'msg'}):
        value = str(state[key])
        if not value or not value.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in value):
            raise ValueError('invalid field value')
        parts.append(key + '=' + value)
    body = ' '.join(parts)
    if len(body) > MAX_BODY:
        raise ValueError('status exceeds 512 bytes')
    if 'msg' in state:
        value = state['msg']
        if not value.isascii() or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError('invalid message')
        available = MAX_BODY - len(body) - len(' msg=')
        if len(value) > available:
            if available < len(SHORT_TRUNCATION):
                raise ValueError('status exceeds 512 bytes')
            marker = TRUNCATION if available >= len(TRUNCATION) else SHORT_TRUNCATION
            value = value[:available - len(marker)] + marker
        body += ' msg=' + value
    return body + '\n'


class Status:
    def __init__(self, path=Path('/run/deep32-forge/status'), consoles=('/dev/console', '/dev/ttyS0')):
        self.path, self.consoles = Path(path), consoles
        self.boot, self.seq, self.state = secrets.token_hex(4), 0, {}

    def update(self, stage, state, **fields):
        # Replace all fields, so old sign-in addresses and pairing codes cannot survive.
        self.state = dict(boot=self.boot, stage=stage, state=state, **fields)
        self.seq += 1
        self.repeat()

    def repeat(self):
        line = encode(self.seq, 'forge', self.state)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_suffix('.new')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='ascii', newline='\n') as stream:
            stream.write(line)
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        public = encode(self.seq, 'forge', {key: value for key, value in self.state.items()
                                           if key not in {'pair', 'signin'}})
        print(public, end='', flush=True)
        for console in self.consoles:
            try:
                fd = os.open(console, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK | getattr(os, 'O_BINARY', 0))
                try:
                    os.write(fd, line.replace('\n', '\r\n').encode('ascii'))
                finally:
                    os.close(fd)
            except OSError:
                pass
