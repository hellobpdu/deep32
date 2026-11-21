"""P-256 Jacobian arithmetic ported from the relay.

Python integers are variable-time. The fixed ladder only avoids scalar-bit
branches. This code MUST NOT be described as constant-time.
"""

P = int('ffffffff00000001000000000000000000000000ffffffffffffffffffffffff', 16)
Q = int('ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551', 16)
B = int('5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b', 16)
G = (int('6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296', 16),
     int('4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5', 16), 1)
INFINITY = (0, 1, 0)
M_COMPRESSED = '02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f'
N_COMPRESSED = '03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49'

class PointError(Exception):
    def __init__(self):
        super().__init__('protocol')

def decode_compressed(hex33):
    b = bytes.fromhex(hex33)
    if len(b) != 33 or b[0] not in (2, 3):
        raise PointError()
    x = int.from_bytes(b[1:], 'big')
    rhs = (x*x*x - 3*x + B) % P
    y = pow(rhs, (P+1)//4, P)
    if x >= P or y*y % P != rhs:
        raise PointError()
    if y % 2 != b[0] & 1:
        y = P-y
    return x, y

def decode_point(b):
    if not isinstance(b,(bytes,bytearray)) or len(b) != 65 or b[0] != 4:
        raise PointError()
    x, y = int.from_bytes(b[1:33], 'big'), int.from_bytes(b[33:], 'big')
    if x >= P or y >= P or y*y % P != (x*x*x-3*x+B) % P:
        raise PointError()
    return x, y

def encode_point(point):
    x, y, z = point
    if z == 0:
        raise PointError()
    z = pow(z, P-2, P)
    z2 = z*z % P
    return b'\x04' + (x*z2 % P).to_bytes(32, 'big') + (y*z2*z % P).to_bytes(32, 'big')

def jacobian_double(point):
    px, py, pz = point
    delta, gamma = pz*pz % P, py*py % P
    beta = px*gamma % P
    alpha = 3*(px-delta)*(px+delta) % P
    x = (alpha*alpha-8*beta) % P
    z = ((py+pz)*(py+pz)-gamma-delta) % P
    y = (alpha*(4*beta-x)-8*gamma*gamma) % P
    return x, y, z

def select(a, b, bit):
    return tuple(x+(y-x)*bit for x, y in zip(a, b))

def jacobian_add(p, q):
    px, py, pz = p
    qx, qy, qz = q
    z1, z2 = pz*pz % P, qz*qz % P
    u1, u2 = px*z2 % P, qx*z1 % P
    s1, s2 = py*qz*z2 % P, qy*pz*z1 % P
    h, r = (u2-u1) % P, (s2-s1) % P
    hh = h*h % P
    hhh, v = h*hh % P, u1*hh % P
    x = (r*r-hhh-2*v) % P
    total = (x, (r*(v-x)-s1*hhh) % P, h*pz*qz % P)
    total = select(total, jacobian_double(p), int(h == 0 and r == 0))
    total = select(total, q, int(pz == 0))
    return select(total, p, int(qz == 0))

def ladder_multiply(point, k32):
    if len(k32) != 32:
        raise PointError()
    if len(point) == 2:
        point = (*point, 1)
    r0, r1 = INFINITY, point
    for i in range(256):
        bit = (k32[i//8] >> (7-i % 8)) & 1
        total = jacobian_add(r0, r1)
        d0, d1 = jacobian_double(r0), jacobian_double(r1)
        r0, r1 = select(d0, total, bit), select(total, d1, bit)
    return r0

M = (*decode_compressed(M_COMPRESSED), 1)
N = (*decode_compressed(N_COMPRESSED), 1)
