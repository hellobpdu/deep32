"""Synthetic local test state and bounded TLS helpers."""
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import unittest
from deep32net import pairing, wire
from deep32net.errors import NetError
from deep32net.identity import ensure_identity
from deep32net.tokens import write_secret

SHIP = Path(__file__).resolve().parents[1]
PUBLIC = '-----BEGIN PUBLIC KEY-----\n'+base64.b64encode(bytes.fromhex('302a300506032b6570032100')+bytes([17])*32).decode()+'\n-----END PUBLIC KEY-----\n'
CODE = 'KLMNP23456'
TOKEN = '0123456789abcdef'*4

def fixture(name):
    return json.loads((SHIP/'vectors'/name).read_bytes())

class Clock:
    def __init__(self, now=1700000000):
        self.now = now
    def time(self):
        return self.now
    def monotonic(self):
        return time.monotonic()

class Case(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['TMP'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.openssl = os.environ.get('DEEP32_OPENSSL') or shutil.which('openssl')

    def identity(self, name, kind):
        self.assertIsNotNone(self.openssl,'openssl not found')
        return ensure_identity(self.root/name,kind,self.openssl)

    def files(self):
        token,public = self.root/'forge-token',self.root/'kb-public.pem'
        if not token.exists():
            write_secret(token,TOKEN)
            public.write_text(PUBLIC,encoding='ascii',newline='\n')
        return token,public

    def server(self, kind='forge', state='a', code=CODE, seconds=12, clock=None, cls=None, **kwargs):
        self.assertIsNotNone(self.openssl,'openssl not found')
        from deep32net.window import PairingWindow
        from deep32net.files import state_dir
        directory = state_dir(self.root/state)
        w = PairingWindow(directory,kind+'-joined',seconds=seconds,clock=clock)
        if kind == 'forge':
            token,public = self.files()
            kwargs.update(token_path=token,kb_public_path=public)
        s = (cls or pairing.JoinedSide)(kind,directory,code,window=w,port=0,openssl=self.openssl,**kwargs)
        ready = threading.Event()
        s.on_ready = lambda port:ready.set()
        box = {}
        def run():
            try:
                box['result'] = s.serve()
            except BaseException as exc:
                box['error'] = exc
            finally:
                ready.set()
        thread = threading.Thread(target=run,daemon=True)
        thread.start()
        self.assertTrue(ready.wait(10))
        if s.bound_port is None:
            raise box['error']
        self.addCleanup(self._stop_server,s,thread)
        return s,thread,box

    def _stop_server(self,s,thread):
        if thread.is_alive():
            s.window.deadline = s.window.clock.monotonic()
            thread.join(11)
        self.assertFalse(thread.is_alive(),'server thread remained active')

    def join(self, server, state='b', code=CODE, clock=None, seconds=12, **kwargs):
        from deep32net.files import state_dir
        from deep32net.window import PairingWindow
        directory = state_dir(self.root/state)
        w = PairingWindow(directory,server.kind+'-joiner',seconds=seconds,clock=clock)
        j = pairing.Joiner(server.kind,directory,code,port=server.bound_port,window=w,openssl=self.openssl,**kwargs)
        return j,j.join()

    def finished(self, thread, box, reason=None):
        thread.join(15)
        self.assertFalse(thread.is_alive())
        if reason:
            self.assertIsInstance(box.get('error'),NetError)
            self.assertEqual(box['error'].reason,reason)
        else:
            if 'error' in box:
                raise box['error']
        return box.get('result')

    def failure_count(self, state, link):
        from deep32net.pairlock import load
        return len([v for v in load(self.root/state/'pairing'/(link+'.pairlock')).values()[:3] if v])

    def raw_joiner(self, server, state='raw'):
        ident = self.identity(state,'workstation' if server.kind != 'gateway' else 'headend')
        raw = socket.create_connection(('127.0.0.1',server.bound_port),timeout=3)
        raw.settimeout(3)
        sock = pairing.tls_context(False,ident).wrap_socket(raw,server_hostname=None)
        sock.settimeout(3)
        return sock,ident

def send(sock,type,payload=b''):
    sock.sendall(wire.encode_frame(type,payload,handover_allowed=True))

def receive(sock):
    return wire.read_frame(sock,3,True,overall_timeout=3)

def hello_b(server, ident):
    return wire.hello(server.kind,'B',ident.id,'23'*32,base64.b64encode(ident.der()).decode())
