"""Real owner methods with synthetic state and denied sockets; no model import."""
import ast
import asyncio
from contextlib import ExitStack
import contextvars
from datetime import datetime, timezone
from email.message import Message
import hashlib
import hmac
import importlib.util
import io
import json
import logging
from pathlib import Path
import re
import socket
import sys
import time
from types import SimpleNamespace as NS
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('synthetic_diag_bridge', ROOT / 'src/model-bridge/d32_model_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def definitions(path, names, env):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    # Heavy third-party runtimes are deliberately absent; execute real pure methods.
    code = compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)] + selected,
                              type_ignores=[])), str(path), 'exec')
    exec(code, env)
    return env


def complete(coroutine):
    """These fake ASGI awaits complete inline; no event loop/socketpair exists."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('unexpected asynchronous I/O')


class Observations(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(socket, 'socket', side_effect=AssertionError('native socket forbidden')))

    def settings(self):
        return NS(embed_model='example/embed', embed_revision='1'*40, rerank_model='example/rerank',
                  rerank_revision='2'*40, rerank_probe_file=None, state_file='unopened', require_revision=True)

    def test_passive_snapshot_never_refreshes_or_returns_host_reason(self):
        a = bridge.Attestor(self.settings(), {})
        a.enabled = True
        a.state['embed'].update(verified=True, reason='synthetic-private-bearer', checked_at='2026-01-01T00:00:00Z')
        a.state['rerank'].update(verified=False, reason='forge-auth-rejected synthetic-private-bearer')
        with mock.patch.object(a, 'run_once', side_effect=AssertionError), mock.patch.object(bridge, 'setting_on', side_effect=AssertionError):
            snapshot = a.diagnostic_snapshot()
        self.assertNotIn('synthetic-private-bearer', repr(snapshot))
        self.assertNotIn('hub', snapshot)
        snapshot['embed']['verified'] = False
        self.assertTrue(a.diagnostic_snapshot()['embed']['verified'])
        self.assertEqual(snapshot['rerank']['reason'], 'forge-auth-rejected')

    def test_identity_uses_only_matching_authenticated_expected_fields(self):
        a = bridge.Attestor(self.settings(), {})
        a._remember_identity('embed', {'model_id':'example/embed', 'model_sha':'1'*40,
             'device_kind':'gpu','measurement_verified':True, 'token':'synthetic-private'}, 'model_id', 'model_sha')
        self.assertEqual(a.pending_identity['embed'], {'model_id':'example/embed', 'revision':'1'*40,
             'device_kind':'gpu', 'measurement_verified':True})
        a._remember_identity('embed', {'model_id':'secret/value', 'model_sha':'1'*40}, 'model_id','model_sha')
        self.assertEqual(a.pending_identity['embed'], {})

    def test_pending_identity_commits_with_check_and_failure_clears_it(self):
        a = bridge.Attestor(self.settings(), {})
        a.enabled = True
        a.is_enabled = lambda: True
        a.state['embed'].update(verified=True, checked_at='2026-01-01T00:00:00Z')
        a.identity['embed'] = {'revision':'0'*40}
        def check():
            a._remember_identity('embed', {'model_id':'example/embed','model_sha':'1'*40,
                'device_kind':'gpu','measurement_verified':True}, 'model_id','model_sha')
            self.assertEqual(a.diagnostic_snapshot()['embed']['revision'], '0'*40)
            return True, 'matched'
        a.check_embed = check
        a.check_rerank = lambda: (True, 'matched')
        a.run_once()
        self.assertEqual(a.diagnostic_snapshot()['embed']['revision'], '1'*40)
        with mock.patch.object(a, 'check_embed', side_effect=OSError('synthetic-private')):
            a.run_once()
        snapshot = a.diagnostic_snapshot()
        self.assertFalse(snapshot['embed']['verified'])
        self.assertNotIn('revision', snapshot['embed'])
        self.assertNotIn('synthetic-private', repr(snapshot))

    def test_reranker_device_comes_from_loaded_parameters(self):
        env = definitions(Path('src/gpu-pack/containers/reranker/reranker_api.py'), {'diagnostic_identity'}, {'REVISION':'2'*40})
        identity = env['diagnostic_identity']
        def loaded(kinds, commit='2'*40):
            return NS(parameters=lambda: iter(NS(device=NS(type=k)) for k in kinds), config=NS(_commit_hash=commit))
        for kinds, kind in ((['cuda'], 'gpu'), (['cpu'], 'cpu'), (['cuda','cpu'],'cpu'), ([], 'unknown')):
            self.assertEqual(identity(loaded(kinds))['device_kind'], kind)
        self.assertNotIn('measurement_verified', identity(loaded(['cuda'], None)))
        self.assertFalse(identity(loaded(['cuda'], '3'*40))['measurement_verified'])
        self.assertNotIn('measurement_verified', identity(None))
        self.assertTrue(identity(loaded(['cuda']))['revision_pin_verified'])
        self.assertNotIn('measurement_verified', identity(loaded(['cuda'])))

    def test_door_origin_headers_on_info_only(self):
        from datetime import datetime, timezone
        env = definitions(Path('src/gpu-pack/containers/door/door.py'), {'Handler'},
             {'BaseHTTPRequestHandler':object, 're':re, 'datetime':datetime,'timezone':timezone})
        for path, nonces, wanted in (('/info',['n'*16],True), ('/health',['n'*16],False),
                                    ('/info',['bad\nheader'],False), ('/info',['n'*16,'n'*16],False)):
            messages = Message()
            for nonce in nonces:
                messages['X-Deep32-Nonce'] = nonce
            headers = []
            handler = NS(path=path, headers=messages, send_response=lambda code:None,
                         send_header=lambda k,v:headers.append((k,v)), end_headers=lambda:None, wfile=io.BytesIO())
            env['Handler'].reply(handler, 401)
            result = dict(headers)
            self.assertEqual('X-Deep32-Time' in result, wanted)
            if wanted:
                self.assertEqual(result['X-Deep32-Nonce'], 'n'*16)
                self.assertRegex(result['X-Deep32-Time'], r'^\d{4}-.*\.\d{3}Z$')
                self.assertEqual(result['Cache-Control'], 'no-store')

    def test_gateway_origin_time_including_401_and_auth_unchanged(self):
        async def run(path, nonces, denied):
            sent = []
            class JSONResponse:
                def __init__(self, body, status_code, headers): self.status = status_code
                async def __call__(self, scope, receive, send):
                    await send({'type':'http.response.start','status':self.status,'headers':[]})
            env = definitions(Path('src/gateway/rag_edge.py'), {'Guard','_header'},
                {'hashlib':hashlib,'hmac':hmac,'re':re,'time':time,'datetime':datetime,'timezone':timezone,
                 'log':logging.getLogger('synthetic-clock'),
                 'JSONResponse':JSONResponse,'_CORE_CALLS':contextvars.ContextVar('calls'), 'PUBLIC_PATHS':{'/health'}})
            async def app(scope, receive, send):
                await send({'type':'http.response.start','status':200,'headers':[(b'x-deep32-time',b'spoof'),
                     (b'x-deep32-nonce',b'spoof'),(b'age',b'10')]})
            guard = env['Guard'](app, ('synthetic-only',), ())
            scope = {'type':'http','path':path,'method':'POST','client':('127.0.0.1',123),
                     'headers':[(b'x-deep32-nonce', n) for n in nonces] +
                     ([] if denied else [(b'authorization',b'Bearer synthetic-only')])}
            async def send(message): sent.append(message)
            await guard(scope,None,send)
            return sent[0]
        for denied in (True,False):
            response = complete(run('/mcp', [b'n'*16], denied))
            self.assertEqual(response['status'], 401 if denied else 200)
            headers = dict(response['headers'])
            self.assertEqual(headers[b'x-deep32-nonce'], b'n'*16)
            self.assertNotEqual(headers[b'x-deep32-time'],b'spoof')
            self.assertNotIn(b'age',headers)
        for nonces in ([],[b'bad\nnonce'],[b'n'*16,b'n'*16]):
            self.assertNotIn(b'x-deep32-time', dict(complete(run('/mcp', nonces, True))['headers']))
        self.assertEqual(b'spoof', dict(complete(run('/health',[b'n'*16],True))['headers'])[b'x-deep32-time'])


if __name__ == '__main__':
    unittest.main()
