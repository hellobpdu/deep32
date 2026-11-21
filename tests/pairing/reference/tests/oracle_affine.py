"""Independent affine oracle from asklink tests/check_pairing_sources.py."""
p = int('ffffffff00000001000000000000000000000000ffffffffffffffffffffffff',16)
curve_b = int('5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b',16)
g = (int('6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296',16),int('4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5',16))

def point(raw):
    x = int.from_bytes(raw[1:33],'big')
    if len(raw) == 33 and raw[0] in (2,3):
        y = pow((x*x*x-3*x+curve_b) % p,(p+1)//4,p)
        if y % 2 != raw[0] % 2:
            y = p-y
    else:
        assert len(raw) == 65 and raw[0] == 4
        y = int.from_bytes(raw[33:],'big')
    assert 0 <= x < p and 0 <= y < p
    assert (y*y-x*x*x+3*x-curve_b) % p == 0
    return x,y

def add(a,b):
    if a is None:
        return b
    if b is None:
        return a
    x,y = a
    u,v = b
    if x == u and (y+v) % p == 0:
        return None
    slope = ((3*x*x-3)*pow(2*y,-1,p) if a == b else (v-y)*pow(u-x,-1,p)) % p
    xx = (slope*slope-x-u) % p
    return xx,(slope*(x-xx)-y) % p

def mul(k,a):
    out = None
    while k:
        if k & 1:
            out = add(out,a)
        a = add(a,a)
        k >>= 1
    return out

def encode(a):
    assert a is not None
    return b'\x04'+b''.join(n.to_bytes(32,'big') for n in a)
