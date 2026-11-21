import unittest
from support import fixture
from deep32net.spake2 import normalise_password, password_scalar, vector, Spake2
from make_vectors import negative_fields

class TestSpake2Vectors(unittest.TestCase):
    def test_rfc_and_d32_every_field(self):
        data = fixture('pairing-v2-vectors.json')
        self.assertEqual((len(data['rfc_vectors']),len(data['d32_vectors'])),(4,6))
        for row in data['rfc_vectors']+data['d32_vectors']:
            with self.subTest(vector=row['id']):
                if 'password' in row:
                    pw = normalise_password(row['password'])
                    self.assertEqual(pw.hex(),row['normalised_utf8'])
                    self.assertEqual(password_scalar(pw,*[bytes.fromhex(row[k]) for k in ('relay_pin','node_pin','relay_nonce','node_nonce')]).hex(),row['w'])
                result = vector(*[bytes.fromhex(row[k]) for k in ('id_a','id_b','w','x','y')])
                for k,v in result.items():
                    self.assertEqual(v,row[k],k)

    def test_base_negatives_every_mac(self):
        data = fixture('pairing-v2-vectors.json')
        byid = {v['id']:v for v in data['d32_vectors']}
        for row in data['negative_vectors']:
            result = negative_fields(byid[row['base']],row,False)
            for k,v in result.items():
                self.assertEqual(v,row[k],row['id']+' '+k)

    def test_password_rules_and_secret_erasure(self):
        for text in ['short','a\0longpassword','a\tlongpassword','\ud800abcdefghijkl','a'*1025]:
            with self.assertRaises(ValueError):
                normalise_password(text)
        self.assertEqual(normalise_password('one two 3 4'),b'one two 3 4')
        for args in [(b'',bytes(32),b'1'*32,bytes(32),bytes(32)),(b'hi',bytes(32),bytes(32),bytes(32),bytes(32))]:
            with self.assertRaises(Exception):
                password_scalar(*args)
        s = Spake2('A',bytes([1])*32,bytes([2])*32)
        refs = [v for v in vars(s).values() if isinstance(v,bytearray)]
        s.erase()
        self.assertTrue(all(not any(v) for v in refs))
