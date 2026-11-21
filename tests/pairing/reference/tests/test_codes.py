import unittest
from support import fixture
from deep32net.codes import CodeError, new_code, normalise_code, is_canonical
from deep32net.spake2 import password_scalar, vector
from make_vectors import negative_fields

class TestCodes(unittest.TestCase):
    def test_normalisation(self):
        for row in fixture('pairing-v2-code-vectors.json')['code_normalisation']:
            if row['canonical'] is None:
                with self.assertRaises(CodeError) as caught:
                    normalise_code(row['input'])
                self.assertEqual(caught.exception.reason,row['reason'])
            else:
                self.assertEqual(normalise_code(row['input']).decode(),row['canonical'])

    def test_generation(self):
        for _ in range(2000):
            self.assertTrue(is_canonical(new_code()))

    def test_code_vectors_and_negatives(self):
        data = fixture('pairing-v2-code-vectors.json')
        self.assertEqual((len(data['code_vectors']),len(data['negative_vectors'])),(6,3))
        for row in data['code_vectors']:
            self.assertEqual(list(row),list(fixture('pairing-v2-vectors.json')['d32_vectors'][0]))
            self.assertEqual(password_scalar(normalise_code(row['password']),*[bytes.fromhex(row[k]) for k in ('relay_pin','node_pin','relay_nonce','node_nonce')]).hex(),row['w'])
            for k,v in vector(*[bytes.fromhex(row[k]) for k in ('id_a','id_b','w','x','y')]).items():
                self.assertEqual(row[k],v)
        byid = {v['id']:v for v in data['code_vectors']}
        for row in data['negative_vectors']:
            for k,v in negative_fields(byid[row['base']],row).items():
                self.assertEqual(row[k],v)
