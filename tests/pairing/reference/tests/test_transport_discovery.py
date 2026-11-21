import json
import unittest
import socket
import threading
from unittest.mock import patch
from support import SHIP, Case, fixture
from deep32net.transport import Transport, bind_address, PAIRING_PORTS
from deep32net.discovery import discover, probe_candidate
from deep32net.errors import NetError
from deep32net import pairing, wire

class TestTransportDiscovery(Case):
    def test_every_fixture_against_vectors(self):
        for row in fixture('discovery-v1-vectors.json')['cases']:
            try:
                obj = json.loads((SHIP/'tests/fixtures'/row['fixture']).read_bytes())
            except ValueError:
                obj = []
            if isinstance(row['expect'],list):
                self.assertEqual(discover(row['want'],'tailnet',obj,probe=False)['candidates'],row['expect'])
            else:
                with self.assertRaises(NetError) as caught:
                    discover(row['want'],'tailnet',obj,probe=False)
                e = caught.exception
                self.assertEqual(dict(exit=e.exit_code,reason=e.reason,**e.fields),row['expect'])
                self.assertNotIn('https:',str(e.fields))

    def test_mixed_outside_reference(self):
        obj = json.loads((SHIP/'tests/fixtures/status-mixed.json').read_bytes())
        rows = discover('forge','tailnet',obj,probe=False)['candidates']
        self.assertEqual([r['peer_id'] for r in rows],['n'+str(i).zfill(10)+'CNTRL' for i in [1,4,3,2,5,9]])
        self.assertEqual([r['path'] for r in rows],['direct','direct','relayed','unknown','unknown','unknown'])
        self.assertEqual([r['online'] for r in rows],[True]*5+[False])

    def test_loopback_runs_no_command(self):
        def refused():
            self.fail('runner was called')
        self.assertEqual(discover('forge','loopback',refused,probe=False)['candidates'],[dict(peer_id=None,host_name=None,dns_name=None,os=None,ipv4='127.0.0.1',port=7453,online=True,path='loopback',probe=None)])

    def test_bind_and_endpoint_table(self):
        for address in ('0.0.0.0','100.64.0.1','::1'):
            with self.assertRaises(NetError):
                bind_address(address)
        for kind,port in PAIRING_PORTS.items():
            self.assertEqual(Transport('loopback').peer_endpoint(kind),('127.0.0.1',port))
            self.assertEqual(Transport('tailnet').peer_endpoint(kind,address='100.64.0.101'),('100.64.0.101',port))
        for mode,address in [('loopback','100.64.0.101'),('tailnet','127.0.0.1'),('tailnet',None)]:
            with self.assertRaises(NetError):
                Transport(mode).peer_endpoint('forge',address=address)

    def test_probe_skip_without_connections(self):
        with patch('socket.create_connection',side_effect=AssertionError('connection')):
            for online,ip,want,reason in [(False,'100.64.0.101','forge','offline'),(True,None,'forge','no-ipv4'),(True,'100.64.0.101','relay','relay-not-probed')]:
                self.assertEqual(probe_candidate(dict(online=online,ipv4=ip,port=7452),want),dict(done=False,reason=reason))

    def test_probe_connect_refused(self):
        import socket
        with socket.socket() as s:
            s.bind(('127.0.0.1',0))
            port = s.getsockname()[1]
        self.assertEqual(probe_candidate(dict(online=True,ipv4='127.0.0.1',port=port),'forge'),dict(done=False,reason='connect-refused'))

    def test_probe_tls_eof_wrong_role_nonhello_and_invalid_hello(self):
        ident = self.identity('probe-server','forge')
        for mode in ('tls-failed','eof','wrong-role','nonhello','invalid-hello'):
            with self.subTest(mode=mode):
                listener = socket.socket()
                listener.bind(('127.0.0.1',0))
                listener.listen(1)
                listener.settimeout(5)
                port = listener.getsockname()[1]
                errors,received = [],[]
                def run():
                    try:
                        raw,_ = listener.accept()
                        raw.settimeout(3)
                        if mode == 'tls-failed':
                            with raw:
                                raw.sendall(b'bad TLS\r\n')
                            return
                        with pairing.tls_context(True,ident).wrap_socket(raw,server_side=True) as sock:
                            sock.settimeout(3)
                            if mode == 'eof':
                                sock.sendall(b'\0\0')
                                return
                            if mode == 'wrong-role':
                                data = wire.encode_frame(1,wire.dumps_compact(wire.hello('gateway','A',ident.id,'11'*32)))
                            elif mode == 'nonhello':
                                data = wire.encode_frame(13)
                            else:
                                data = wire.encode_frame(1,b'{}')
                            sock.sendall(data)
                            received.append(sock.recv(1))
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        listener.close()
                t = threading.Thread(target=run,daemon=True)
                t.start()
                result = probe_candidate(dict(online=True,ipv4='127.0.0.1',port=port),'forge')
                t.join(6)
                self.assertFalse(t.is_alive())
                self.assertEqual(errors,[])
                expected = dict(done=False,reason='tls-failed') if mode == 'tls-failed' else dict(done=True,deep32=False,reason='wrong-role' if mode == 'wrong-role' else 'not-deep32')
                self.assertEqual(result,expected)
                self.assertTrue(all(b == b'' for b in received))

    def test_probe_timeout_mappings(self):
        candidate = dict(online=True,ipv4='127.0.0.1',port=7453)
        with patch('socket.create_connection',side_effect=TimeoutError):
            self.assertEqual(probe_candidate(candidate,'forge'),dict(done=False,reason='timeout'))
        ident = self.identity('timeout-server','forge')
        listener = socket.socket()
        listener.bind(('127.0.0.1',0))
        listener.listen(1)
        listener.settimeout(5)
        candidate['port'] = listener.getsockname()[1]
        errors = []
        def run():
            try:
                raw,_ = listener.accept()
                raw.settimeout(12)
                with pairing.tls_context(True,ident).wrap_socket(raw,server_side=True) as sock:
                    sock.settimeout(12)
                    self.assertEqual(sock.recv(1),b'')
            except BaseException as exc:
                errors.append(exc)
            finally:
                listener.close()
        t = threading.Thread(target=run,daemon=True)
        t.start()
        self.assertEqual(probe_candidate(candidate,'forge'),dict(done=False,reason='timeout'))
        t.join(13)
        self.assertFalse(t.is_alive())
        self.assertEqual(errors,[])
