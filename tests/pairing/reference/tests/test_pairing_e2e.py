import base64
import hashlib
import socket
import struct
import threading
import time
from unittest.mock import patch
from support import Case, Clock, CODE, TOKEN, PUBLIC, send, receive, hello_b
from standin_relay import StandinRelay
from deep32net import pairing, wire, store, pairlock
from deep32net.discovery import probe_candidate
from deep32net.errors import NetError, Locked
from deep32net.files import atomic_write
from deep32net.p256 import G, encode_point
from deep32net.tokens import update_edge_env
from deep32net.window import PairingWindow

class TestPairingE2E(Case):
    def test_E1_forge_success(self):
        server,t,box = self.server()
        joiner,result = self.join(server)
        self.finished(t,box)
        a,b = store.list_peers(self.root/'a')[0],store.list_peers(self.root/'b')[0]
        self.assertEqual((a['peer_pin'],b['peer_pin']),(b['own_pin'],a['own_pin']))
        directory = store.received_dir(self.root/'b','forge',server.identity.id)
        self.assertEqual((directory/'forge-token').read_bytes(),self.files()[0].read_bytes())
        self.assertEqual((directory/'kb-public.pem').read_text(),PUBLIC)
        self.assertEqual(a['given'],['forge-token','kb-public.pem'])
        self.assertEqual(b['received'],['forge-token','kb-public.pem'])
        self.assertEqual(self.failure_count('a','forge-joined'),0)
        self.assertEqual(self.failure_count('b','forge-joiner'),0)

    def test_E2_gateway_success(self):
        server,t,box = self.server('gateway')
        joiner,result = self.join(server)
        self.finished(t,box)
        token = (self.root/'a/private/mcp-tokens'/joiner.identity.id).read_bytes()
        received = store.received_dir(self.root/'b','gateway',server.identity.id)/'mcp-token'
        self.assertEqual(received.read_bytes(),token)
        path = self.root/'edge-env.txt'
        update_edge_env(path,store.mcp_tokens(self.root/'a'))
        self.assertEqual(path.read_bytes(),b'RAG_MCP_TOKENS='+token)

    def test_E3_relay_standin_order(self):
        ident = self.identity('b','workstation')
        server,t,box = self.server('relay',cls=StandinRelay,injected_pin=ident.pin)
        joiner,result = self.join(server)
        self.finished(t,box)
        self.assertEqual(server.channel.sent_types,[1,2,14,12,13])
        self.assertEqual(server.channel.received_types,[1,2,14,12,13])
        self.assertEqual(joiner.channel.sent_types,[1,2,14,12,13])
        self.assertEqual(result['received'],[])
        self.assertEqual(len(store.list_peers(self.root/'b')),1)

    def test_E4_wrong_code_both_fail_once(self):
        server,t,box = self.server()
        with self.assertRaises(NetError) as caught:
            self.join(server,code='KLMNP23457')
        self.assertEqual((caught.exception.exit_code,caught.exception.reason),(5,'password-or-identity'))
        self.finished(t,box,'confirmation')
        for state,side in [('a','joined'),('b','joiner')]:
            self.assertEqual(self.failure_count(state,'forge-'+side),1)
            self.assertEqual(store.list_peers(self.root/state),[])
            self.assertFalse((self.root/state/'private/received').exists())

    def test_E5_concurrent_joiners_one_attempt(self):
        server,t,box = self.server()
        self.identity('b1','workstation')
        self.identity('b2','workstation')
        barrier,answers = threading.Barrier(2),{}
        def run(state):
            barrier.wait(3)
            try:
                answers[state] = self.join(server,state)[1]
            except NetError as exc:
                answers[state] = exc
        threads = [threading.Thread(target=run,args=(s,)) for s in ('b1','b2')]
        for tt in threads:
            tt.start()
        for tt in threads:
            tt.join(15)
            self.assertFalse(tt.is_alive())
        self.finished(t,box)
        self.assertEqual(sum(isinstance(v,dict) for v in answers.values()),1)
        errors = [v for v in answers.values() if isinstance(v,NetError)]
        self.assertEqual(len(errors),1)
        self.assertEqual(errors[0].exit_code,4)
        self.assertEqual(self.failure_count('b1','forge-joiner'),0)
        self.assertEqual(self.failure_count('b2','forge-joiner'),0)

    def test_E6_three_windows_lock_both_then_recover(self):
        clock = Clock()
        for _ in range(3):
            server,t,box = self.server(clock=clock)
            with self.assertRaises(NetError):
                self.join(server,code='KLMNP23457',clock=clock)
            self.finished(t,box,'confirmation')
        for state,side in [('a','joined'),('b','joiner')]:
            with self.assertRaises(Locked) as caught:
                PairingWindow(self.root/state,'forge-'+side,clock=clock).open()
            self.assertEqual(caught.exception.fields['locked_seconds'],900)
            self.assertEqual(self.failure_count(state,'forge-'+side),3)
        clock.now += 901
        server,t,box = self.server(clock=clock)
        self.join(server,clock=clock)
        self.finished(t,box)

    def test_E7_unused_two_second_expiry(self):
        server,t,box = self.server(seconds=2)
        self.finished(t,box,'expired')
        self.assertEqual(self.failure_count('a','forge-joined'),0)

    def test_E8_silent_after_ack_timeout(self):
        server,t,box = self.server(seconds=2)
        sock,ident = self.raw_joiner(server)
        with sock:
            self.assertEqual(receive(sock).type,1)
            send(sock,1,wire.dumps_compact(hello_b(server,ident)))
            self.assertEqual(receive(sock).type,2)
            send(sock,2,wire.dumps_compact(wire.ack('forge')))
            self.assertEqual(receive(sock).type,14)
            error = receive(sock)
            self.assertEqual((error.type,int.from_bytes(error.payload,'big')),(11,6))
        self.finished(t,box,'timeout')
        self.assertEqual(self.failure_count('a','forge-joined'),1)

    def test_E9_probe_then_join_same_window(self):
        server,t,box = self.server()
        before = server.window.deadline
        result = probe_candidate(dict(online=True,ipv4='127.0.0.1',port=server.bound_port),'forge')
        self.assertEqual((result['done'],result['deep32'],result['role'],result['id'],result['pin']),(True,True,'forge',server.identity.id,server.identity.pin))
        self.assertEqual(server.window.deadline,before)
        self.assertFalse(server.window.claimed)
        self.join(server)
        self.finished(t,box)
        self.assertEqual(self.failure_count('a','forge-joined'),0)

    def test_E10_pin_mismatch_no_claim(self):
        server,t,box = self.server()
        with self.assertRaises(NetError) as caught:
            self.join(server,expect_pin='00'*32)
        self.assertEqual((caught.exception.exit_code,caught.exception.reason),(4,'pin-mismatch'))
        self.assertEqual(self.failure_count('a','forge-joined'),0)
        self.assertEqual(self.failure_count('b','forge-joiner'),0)
        self.join(server,state='other')
        self.finished(t,box)

    def test_E11_joiner_already_paired_keeps_server_window(self):
        a = self.identity('a','forge')
        b = self.identity('b','workstation')
        row = store.record('forge','joiner',a.id,'forge',a.pin,b.pin,'loopback',1700000000)
        store.save_record(self.root/'b',row)
        server,t,box = self.server()
        with self.assertRaises(NetError) as caught:
            self.join(server)
        self.assertEqual((caught.exception.exit_code,caught.exception.reason),(7,'already-paired'))
        self.assertEqual(self.failure_count('a','forge-joined'),0)
        self.join(server,state='other')
        self.finished(t,box)

    def test_E12_joined_record_conflict_no_claim(self):
        a = self.identity('a','forge')
        b = self.identity('b','workstation')
        store.save_record(self.root/'a',store.record('forge','joined',b.id,'workstation',b.pin,a.pin,'loopback',1700000000))
        server,t,box = self.server()
        with self.assertRaises(NetError) as caught:
            self.join(server)
        self.assertEqual((caught.exception.exit_code,caught.exception.reason),(4,'peer-refused'))
        self.assertEqual(self.failure_count('a','forge-joined'),0)
        self.assertEqual(self.failure_count('b','forge-joiner'),0)
        self.join(server,state='other')
        self.finished(t,box)

    def test_E13_bad_code_no_connection(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            listener.listen(1)
            listener.settimeout(0.1)
            with self.assertRaises(NetError) as caught:
                pairing.Joiner('forge',self.root/'untouched','bad!',port=listener.getsockname()[1])
            self.assertEqual(caught.exception.exit_code,8)
            self.assertFalse((self.root/'untouched').exists())
            with self.assertRaises(TimeoutError):
                listener.accept()

    def test_E14_hello_mutations_no_claim(self):
        server,t,box = self.server()
        for mutation,code in [('extra',1),('role',1),('feature',12),('nonce',1),('cert',1),('same-pin',2)]:
            sock,ident = self.raw_joiner(server)
            with sock:
                self.assertEqual(receive(sock).type,1)
                h = hello_b(server,ident)
                if mutation == 'extra':
                    h['extra'] = True
                if mutation == 'role':
                    h['role'] = 'gateway'
                if mutation == 'feature':
                    h['features'] = ['handover-v1']
                if mutation == 'nonce':
                    h['nonce'] = 'bad'
                if mutation == 'cert':
                    h['cert'] = 'not base64'
                if mutation == 'same-pin':
                    h['cert'] = base64.b64encode(server.identity.der()).decode()
                send(sock,1,wire.dumps_compact(h))
                f = receive(sock)
                self.assertEqual((f.type,int.from_bytes(f.payload,'big')),(11,code),mutation)
            self.assertFalse(server.window.claimed)
            self.assertEqual(self.failure_count('a','forge-joined'),0)
        self.join(server)
        self.finished(t,box)

    def test_E15_post_claim_protocol_joined_side(self):
        for fault in ('out-of-order','duplicate-share','short-share','off-curve'):
            with self.subTest(fault=fault):
                state = 'a-'+fault
                server,t,box = self.server(state=state)
                sock,ident = self.raw_joiner(server)
                with sock:
                    receive(sock)
                    send(sock,1,wire.dumps_compact(hello_b(server,ident)))
                    receive(sock)
                    send(sock,2,wire.dumps_compact(wire.ack('forge')))
                    self.assertEqual(receive(sock).type,14)
                    if fault == 'out-of-order':
                        send(sock,13)
                    elif fault == 'short-share':
                        sock.sendall(struct.pack('!IIBBH',64,0,14,0,0)+bytes(64))
                    elif fault == 'off-curve':
                        send(sock,14,b'\x04'+bytes(64))
                    else:
                        send(sock,14,encode_point(G))
                        self.assertEqual(receive(sock).type,12)
                        send(sock,14,encode_point(G))
                    error = receive(sock)
                    self.assertEqual(error.type,11)
                self.finished(t,box,'protocol')
                self.assertEqual(self.failure_count(state,'forge-joined'),1)

    def test_E15_post_claim_protocol_joiner_side(self):
        for fault in ('out-of-order','duplicate-share','short-share','off-curve'):
            with self.subTest(fault=fault):
                a = self.identity('script-'+fault,'forge')
                listener = socket.socket()
                listener.bind(('127.0.0.1',0))
                listener.listen(1)
                listener.settimeout(5)
                port = listener.getsockname()[1]
                errors = []
                def script():
                    try:
                        raw,_ = listener.accept()
                        raw.settimeout(5)
                        with pairing.tls_context(True,a).wrap_socket(raw,server_side=True) as sock:
                            send(sock,1,wire.dumps_compact(wire.hello('forge','A',a.id,'11'*32)))
                            receive(sock)
                            receive(sock)
                            send(sock,2,wire.dumps_compact(wire.ack('forge')))
                            if fault == 'out-of-order':
                                send(sock,13)
                            elif fault == 'short-share':
                                sock.sendall(struct.pack('!IIBBH',64,0,14,0,0)+bytes(64))
                            elif fault == 'off-curve':
                                send(sock,14,b'\x04'+bytes(64))
                                receive(sock)
                            else:
                                send(sock,14,encode_point(G))
                                receive(sock)
                                send(sock,14,encode_point(G))
                            self.assertEqual(receive(sock).type,11)
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        listener.close()
                tt = threading.Thread(target=script,daemon=True)
                tt.start()
                with self.assertRaises(NetError) as caught:
                    pairing.Joiner('forge',self.root/('join-'+fault),CODE,port=port,openssl=self.openssl).join()
                tt.join(7)
                self.assertFalse(tt.is_alive())
                self.assertEqual(errors,[])
                self.assertEqual((caught.exception.exit_code,caught.exception.reason),(5,'protocol'))
                self.assertEqual(self.failure_count('join-'+fault,'forge-joiner'),1)

    def test_commit_send_failure_rolls_back_joiner(self):
        original = pairing.Channel.send
        def broken(channel,type,payload=b''):
            if type == 13 and channel.window.path.name == 'forge-joiner.pairlock':
                raise ConnectionResetError()
            return original(channel,type,payload)
        server,t,box = self.server()
        with patch.object(pairing.Channel,'send',broken):
            with self.assertRaises(NetError) as caught:
                self.join(server)
            self.assertEqual((caught.exception.exit_code,caught.exception.reason),(5,'connection'))
            self.finished(t,box,'protocol')
        self.assertEqual(store.list_peers(self.root/'b'),[])
        self.assertFalse(store.received_dir(self.root/'b','forge',server.identity.id).exists())
        self.assertEqual(self.failure_count('b','forge-joiner'),1)

    def test_storage_failure_rolls_back_received_files(self):
        server,t,box = self.server()
        original = store.save_record
        def broken(state,obj):
            from deep32net.errors import StateError
            if obj['side'] == 'joiner':
                raise StateError()
            return original(state,obj)
        with patch.object(store,'save_record',broken):
            with self.assertRaises(NetError) as caught:
                self.join(server)
            self.assertEqual((caught.exception.exit_code,caught.exception.reason),(5,'storage'))
            self.finished(t,box,'confirmation')
        self.assertEqual(store.list_peers(self.root/'b'),[])
        self.assertFalse(store.received_dir(self.root/'b','forge',server.identity.id).exists())
