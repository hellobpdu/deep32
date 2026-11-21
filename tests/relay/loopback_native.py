"""Explicit synthetic-loopback stage; never part of memory-only discovery.

Run with Python -I -B and this file's directory on sys.path. Only literal IPv4
loopback sockets are permitted; DNS and process creation are refused. No native
agent, provider request or operator credential is used.
"""
import http.client
import json
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'adapters/relay'))
from deep32_relay.proxy import Gateway, HTTPProxy
from support import Dispatch, rpc

REAL_SOCKET=socket.socket


class LoopbackSocket(REAL_SOCKET):
    def bind(self, address):
        if not isinstance(address,tuple) or address[0]!='127.0.0.1':
            raise AssertionError('synthetic loopback bind only')
        return super().bind(address)
    def connect(self,address):
        if not isinstance(address,tuple) or address[0]!='127.0.0.1':
            raise AssertionError('synthetic loopback connect only')
        return super().connect(address)
    def connect_ex(self,address):
        raise AssertionError('connect_ex unavailable')
    def sendto(self,*_):
        raise AssertionError('datagrams unavailable')


class Connection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(('127.0.0.1',self.port))


class Loopback(unittest.TestCase):
    def setUp(self):
        for owner,name,replacement in ((socket,'socket',LoopbackSocket),
                (socket,'getaddrinfo',mock.Mock(side_effect=AssertionError('DNS refused'))),
                (socket,'getfqdn',mock.Mock(side_effect=AssertionError('DNS refused'))),
                (socket,'gethostbyaddr',mock.Mock(side_effect=AssertionError('DNS refused'))),
                (socket,'gethostbyname',mock.Mock(side_effect=AssertionError('DNS refused'))),
                (socket,'gethostbyname_ex',mock.Mock(side_effect=AssertionError('DNS refused'))),
                (subprocess,'Popen',mock.Mock(side_effect=AssertionError('process refused'))),
                (subprocess,'run',mock.Mock(side_effect=AssertionError('process refused')))):
            patch=mock.patch.object(owner,name,replacement)
            patch.start()
            self.addCleanup(patch.stop)
        self.gateway=Gateway(Dispatch())
        self.cap=self.gateway.grant('synthetic-loopback',time.monotonic()+30)
        self.gateway.activate(self.cap)
        self.endpoint=HTTPProxy(self.gateway,self.cap,timeout=0.25).start()
        self.addCleanup(self.endpoint.close)

    def request(self,value):
        connection=Connection('127.0.0.1',self.endpoint.server.server_address[1])
        try:
            raw=json.dumps(value).encode()
            connection.request('POST','/mcp',raw,headers={
                'Authorization':'Bearer '+self.cap.bearer,'Content-Type':'application/json'})
            response=connection.getresponse()
            return response.status,response.read()
        finally:
            connection.close()

    def test_actual_private_http_transport_and_revocation(self):
        self.assertTrue(self.endpoint.url.startswith('http://127.0.0.1:'))
        for value in (rpc('initialize'),rpc('notifications/initialized'),rpc('tools/list',ident=2),
                      rpc('tools/call',ident=3,name='rag_status',arguments={})):
            status,body=self.request(value)
            self.assertEqual(status,202 if 'id' not in value else 200)
        self.assertTrue(self.endpoint.close())
        self.assertFalse(self.cap.active)
        self.assertEqual(self.endpoint.server.active_workers(),[])
        self.assertTrue(self.endpoint.close())

    def test_connection_bound_and_close_joins_slow_request_workers(self):
        sockets=[]
        self.addCleanup(lambda:[value.close() for value in sockets])
        # Stalled clients remain inside the per-connection timeout; at most four
        # request threads can exist before any application/Gateway checks.
        self.endpoint.timeout=2
        for _ in range(4):
            value=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            value.settimeout(2)
            value.connect(self.endpoint.server.server_address)
            sockets.append(value)
        end=time.monotonic()+2
        while len(self.endpoint.server.active_workers())<4 and time.monotonic()<end:
            time.sleep(.005)
        self.assertEqual(len(self.endpoint.server.active_workers()),4)
        extra=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        sockets.append(extra)
        extra.settimeout(2)
        extra.connect(self.endpoint.server.server_address)
        self.assertEqual(extra.recv(1),b'')
        self.assertEqual(len(self.endpoint.server.active_workers()),4)
        self.assertTrue(self.endpoint.close())
        self.assertEqual(self.endpoint.server.active_workers(),[])

    def test_revoke_unblocks_inflight_dispatch_before_cleanup_is_certified(self):
        entered,release=threading.Event(),threading.Event()
        failures=[]
        dispatch=Dispatch()
        def waiting(*args):
            entered.set()
            if not release.wait(2):raise RuntimeError('synthetic bounded wait')
            return dispatch(*args)
        self.gateway.dispatch=waiting
        self.gateway.on_revoke=lambda _:release.set()
        result=[]
        def request():
            try:result.append(self.request(rpc('initialize')))
            except (OSError,http.client.HTTPException):failures.append('closed')
        worker=threading.Thread(target=request)
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            self.assertTrue(self.endpoint.close())
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.cap.initialised)
        self.assertEqual(self.endpoint.server.active_workers(),[])
        self.assertTrue(failures or result[0][0]==400)


if __name__=='__main__':
    unittest.main()
