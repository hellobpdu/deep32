import random
import unittest
import oracle_affine as oracle
from support import fixture
from deep32net import p256 as c
from deep32net.spake2 import Spake2

class TestP256(unittest.TestCase):
    def test_ladder_independent_200_and_vector_scalars(self):
        rng = random.Random(32)
        values = [rng.randrange(1,c.Q) for _ in range(200)]
        for file,groups in [('pairing-v2-vectors.json',['rfc_vectors','d32_vectors']),('pairing-v2-code-vectors.json',['code_vectors'])]:
            data = fixture(file)
            for group in groups:
                for row in data[group]:
                    values += [int(row[k],16) for k in ('w','x','y')]
        for k in values:
            self.assertEqual(c.encode_point(c.ladder_multiply(c.G,k.to_bytes(32,'big'))),oracle.encode(oracle.mul(k,oracle.g)))
        for constant in ('M','N'):
            self.assertEqual(getattr(c,constant)[:2],oracle.point(bytes.fromhex(getattr(c,constant+'_COMPRESSED'))))

    def test_decode_refuses_alternatives_and_noncanonical(self):
        good = c.encode_point(c.G)
        bad = [bytes([prefix])+good[1:] for prefix in (2,3,6,7)]
        bad += [good[:-1],good+b'\0',b'\x04'+c.P.to_bytes(32,'big')+good[33:],good[:33]+c.P.to_bytes(32,'big'),good[:-1]+bytes([good[-1]^1])]
        for raw in bad:
            with self.assertRaises(c.PointError):
                c.decode_point(raw)

    def test_infinity_and_unmask(self):
        with self.assertRaises(c.PointError):
            c.encode_point(c.INFINITY)
        w = (2).to_bytes(32,'big')
        a = Spake2('A',w,(3).to_bytes(32,'big'))
        with self.assertRaises(c.PointError):
            a.finish(bytes(32),b'1'*32,a.share,c.encode_point(c.ladder_multiply(c.N,w)))
        a.erase()
