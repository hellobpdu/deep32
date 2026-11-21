import copy
import io
import struct
import unittest
from unittest.mock import patch
from support import fixture
from deep32net import wire
from deep32net.errors import ProtocolError, LimitError, ConnectionLost

class MemorySocket:
    def __init__(self, raw):
        self.data = io.BytesIO(raw)
    def settimeout(self, value):
        pass
    def recv(self, n):
        return self.data.read(n)

class TestWire(unittest.TestCase):
    def reject(self,*args,**kwargs):
        with self.assertRaises(ProtocolError):
            wire.validate_header(*args,**kwargs)

    def test_base_14_frame_vectors_both_directions(self):
        rows = fixture('frame-vectors.json')['vectors']
        self.assertEqual(len(rows),14)
        for row in rows:
            f = wire.decode_frame(bytes.fromhex(row['hex']))
            self.assertEqual(dict(length=len(f.payload),stream=f.stream,type=f.type,channel=f.channel,flags=f.flags,payload_hex=f.payload.hex()),row['decoded'])
            self.assertEqual(wire.encode_frame(f.type,f.payload,f.stream,f.channel,f.flags).hex(),row['hex'])

    def test_rule_unknown_type(self):
        for t in (0,16,255):
            self.reject(t,0,0,0,0)

    def test_rule_flags(self):
        self.reject(1,0,0,1,1)
        self.reject(5,1,1,2,1)
        wire.validate_header(5,1,1,1,1)

    def test_rule_control_and_stream_channels(self):
        for type,length in [(1,1),(2,1),(9,8),(10,8),(11,4),(12,32),(13,0),(14,65)]:
            self.reject(type,1,0,0,length)
            self.reject(type,0,1,0,length)
        for type,length in [(3,1),(4,0),(5,1),(6,4),(7,0),(8,4)]:
            self.reject(type,0,1,0,length)
            self.reject(type,1,0,0,length)

    def test_rule_limits_before_payload_read(self):
        for type in (1,2,3):
            with self.assertRaises(LimitError):
                wire.validate_header(type,1 if type == 3 else 0,1 if type == 3 else 0,0,16385)
        with self.assertRaises(LimitError):
            wire.read_frame(MemorySocket(struct.pack('!IIBBH',1048577,0,1,0,0)))

    def test_rule_exact_empty_lengths(self):
        for t in (4,7,13):
            self.reject(t,0 if t == 13 else 1,0 if t == 13 else 1,0,1)

    def test_rule_exact_four_lengths(self):
        for t in (6,8,11):
            self.reject(t,0 if t == 11 else 1,0 if t == 11 else 1,0,3)

    def test_rule_exact_eight_lengths(self):
        for t in (9,10):
            self.reject(t,0,0,0,7)

    def test_rule_confirm_length(self):
        self.reject(12,0,0,0,31)

    def test_rule_share_length(self):
        self.reject(14,0,0,0,64)

    def test_rule_nonempty_data(self):
        self.reject(5,1,1,0,0)

    def test_type_15_rule(self):
        self.reject(15,0,0,0,1)
        for stream,channel,flags,length in [(1,0,0,1),(0,1,0,1),(0,0,1,1),(0,0,0,0),(0,0,0,16385)]:
            self.reject(15,stream,channel,flags,length,True)
        wire.validate_header(15,0,0,0,16384,True)

    def test_rate_eof_and_trailing_bytes(self):
        data = wire.encode_frame(13)
        sock = MemorySocket(data*257)
        with patch.object(wire.time,'time',return_value=1):
            for _ in range(256):
                wire.read_frame(sock)
            with self.assertRaises(LimitError):
                wire.read_frame(sock)
        with self.assertRaises(ConnectionLost):
            wire.read_frame(MemorySocket(data[:8]))
        with self.assertRaises(ProtocolError):
            wire.decode_frame(data+b'x')

    def test_strict_json_refusals(self):
        for b in [b'\xef\xbb\xbf{}',b'{"a":1,"a":2}',b'{} x',b'{"a":NaN}',b'{"a":Infinity}',b'{"a":-Infinity}',b'{"a":1e9999}',b'[]',b'null',b'{"a":"\\ud800"}',b'{"a":"\xff"}',b'{"a":"\n"}']:
            with self.assertRaises(ProtocolError):
                wire.loads_strict(b)
        a = wire.ack('forge')
        a['version'] = 1.0
        with self.assertRaises(ProtocolError):
            wire.validate_ack(a,'forge','B')

    def test_model_endpoint_handover_is_optional_and_strict(self):
        from support import TOKEN, PUBLIC
        legacy = wire.handover('forge', TOKEN, PUBLIC)
        self.assertNotIn('model_endpoints', legacy)
        wire.validate_handover(legacy, 'forge')
        endpoints = {'embeddings': 'https://forge.synthetic.ts.net:18443',
                     'reranker': 'https://forge.synthetic.ts.net:19443'}
        handover = wire.handover('forge', TOKEN, PUBLIC, model_endpoints=endpoints)
        self.assertEqual(wire.validate_handover(handover, 'forge')['model_endpoints'], endpoints)
        malformed = [None, {}, dict(endpoints, extra='https://forge.synthetic.ts.net'),
                     {'embeddings': endpoints['embeddings']}]
        for value in ('http://forge.synthetic.ts.net:18443', 'https://forge.synthetic.ts.net:18443/',
                      'https://forge.synthetic.ts.net:18443/info', 'https://user@forge.synthetic.ts.net:18443',
                      'https://other.synthetic.ts.net:18443', 'https://forge.synthetic.ts.net:65536',
                      'https://forge.synthetic.ts.net:0', 'https://forge.synthetic.ts.net:18443?x=1',
                      'https://forge.synthetic.ts.net:18443#fragment', 'https://forge..synthetic.ts.net:18443',
                      'https://forge.synthetic.ts.net:18443\n'):
            malformed.append(dict(endpoints, embeddings=value))
        for value in malformed:
            with self.subTest(endpoints=value), self.assertRaises(ProtocolError):
                wire.validate_handover(dict(legacy, model_endpoints=value), 'forge')
        with self.assertRaises(ProtocolError):
            wire.validate_handover(dict(handover, publish='tailnet-plain'), 'forge')

    def test_strict_json_depth_outside_reference(self):
        for count,leaf,ok in [(31,'1',True),(32,'1',False),(32,'',True),(33,'',False)]:
            raw = ('{"a":'+'['*count+leaf+']'*count+'}').encode()
            if ok:
                wire.loads_strict(raw)
            else:
                with self.assertRaises(ProtocolError):
                    wire.loads_strict(raw)
        with self.assertRaises(ProtocolError):
            wire.loads_strict(b'{"a":'+b'['*2000+b'1'+b']'*2000+b'}')

    def test_builders_and_every_single_key_mutation(self):
        for row in fixture('pairing-wire-v1-vectors.json')['transcripts']:
            for frame in row['frames']:
                f = wire.decode_frame(bytes.fromhex(frame['hex']),row['kind'] != 'relay')
                side = frame['from']
                if f.type == 1:
                    obj = wire.hello(row['kind'],side,row['a_id' if side == 'A' else 'b_id'],row['a_nonce' if side == 'A' else 'b_nonce'],row.get('b_cert_b64'))
                    check = lambda v:wire.validate_hello(v,row['kind'],side)
                elif f.type == 2:
                    obj = wire.ack(row['kind'])
                    check = lambda v:wire.validate_ack(v,row['kind'],side)
                elif f.type == 15:
                    obj = row['handover_json']
                    check = lambda v:wire.validate_handover(v,row['kind'])
                else:
                    continue
                self.assertEqual(wire.dumps_compact(obj),f.payload)
                check(obj)
                extra = dict(obj,unknown=True)
                with self.assertRaises(ProtocolError):
                    check(extra)
                for key in obj:
                    bad = dict(obj)
                    del bad[key]
                    if key not in ('codex_version','signed_in','profiles'):
                        with self.assertRaises(ProtocolError,msg=key):
                            check(bad)
                    bad = dict(obj)
                    bad[key] = None
                    with self.assertRaises(ProtocolError,msg=key):
                        check(bad)

    def test_feature_fault_error_codes(self):
        for features,code in [([],12),(['pairing-v2'],12),(['handover-v1'],12),('pairing-v2',1),([1],1),(['pairing-v2']*2,1),(['pairing-v2','handover-v1','other'],1)]:
            a = wire.ack('forge')
            a['features'] = features
            with self.assertRaises(ProtocolError) as caught:
                wire.validate_ack(a,'forge','A')
            self.assertEqual(caught.exception.code,code)
