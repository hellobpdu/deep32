"""Private test seams; these never enable a production native-agent adapter."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REAL_POPEN = subprocess.Popen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'adapters/relay'))
from deep32_relay import proxy


class Clock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class Scratch:
    def __init__(self, root, ident):
        self.path = Path(tempfile.mkdtemp(prefix='synthetic-session-', dir=root))
        self.clean = True
        self.cleanups = 0
    def cleanup(self):
        self.cleanups += 1
        if self.clean:
            shutil.rmtree(self.path)
        return self.clean


class Handle:
    def __init__(self):
        self.events = []
        self.cancelled = 0
        self.tree_empty = True
    def poll(self):
        events, self.events = self.events, []
        return events
    def cancel(self):
        self.cancelled += 1
        return self.tree_empty


class Agent:
    ready = True
    models = ('synthetic-model',)
    efforts = ('synthetic-effort',)
    def __init__(self):
        self.verifies = []
        self.starts = []
        self.choices = []
        self.handles = []
        self.verify_hook = lambda scratch, cap: True
        self.start_hook = None
    def verify(self, scratch, cap):
        self.verifies.append((scratch, cap))
        return self.verify_hook(scratch, cap)
    def start(self, question, history, scratch, cap, *, model=None, effort=None):
        self.starts.append((question, history, scratch, cap))
        self.choices.append((model, effort))
        handle = Handle()
        self.handles.append(handle)
        if self.start_hook:
            self.start_hook(handle)
        return handle


def request():
    return dict(op='session.open', profile='research', kind='research', skill='deep32',
                model='synthetic-model', effort='synthetic-effort', caller_class='research')


def rpc(method, ident=1, **params):
    result = dict(jsonrpc='2.0', method=method, params=params)
    if method != 'notifications/initialized':
        result['id'] = ident
    return result


class Dispatch:
    def __init__(self):
        self.calls = []
        self.hook = None
    def __call__(self, session, envelope, deadline):
        self.calls.append((session, envelope, deadline))
        if self.hook:
            return self.hook(session, envelope, deadline)
        body = envelope['body']
        method = body['method']
        if method == 'notifications/initialized':
            return dict(body=None, http_status=202, mcp_session_id='synthetic-mcp-session')
        if method == 'initialize':
            result = dict(protocolVersion='2025-03-26', capabilities={'tools': {}})
        elif method == 'tools/list':
            result = dict(tools=[dict(name=name, inputSchema={'type':'object'}) for name in sorted(proxy.TOOLS)])
        else:
            result = dict(content=[dict(type='text', text='synthetic tool result')])
        return dict(body=dict(jsonrpc='2.0', id=body['id'], result=result),
                    http_status=200, mcp_session_id='synthetic-mcp-session')


class OfflineCase(unittest.TestCase):
    def setUp(self):
        root = Path(os.environ['DEEP32_RELAY_TEST_ROOT']).resolve(strict=True)
        if root == ROOT or ROOT in root.parents:
            raise AssertionError('fixtures must remain outside the clone')
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-relay-', dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for owner, name in ((socket,'socket'),(socket,'create_connection'),(socket,'getaddrinfo'),
                            (subprocess,'Popen'),(subprocess,'run')):
            patch = mock.patch.object(owner, name, side_effect=AssertionError('offline test effect refused'))
            patch.start()
            self.addCleanup(patch.stop)
        self.clock = Clock()
        self.dispatch = Dispatch()
        self.gateway = proxy.Gateway(self.dispatch, clock=self.clock)

    def active_cap(self, session='synthetic-session'):
        cap = self.gateway.grant(session, self.clock()+170)
        self.gateway.activate(cap)
        return cap

    def initialise(self, cap):
        self.gateway.call(cap, rpc('initialize', protocolVersion='2025-03-26', capabilities={}, clientInfo={'name':'synthetic','version':'1'}))
        self.gateway.call(cap, rpc('notifications/initialized'))
        self.gateway.call(cap, rpc('tools/list', ident=2))
