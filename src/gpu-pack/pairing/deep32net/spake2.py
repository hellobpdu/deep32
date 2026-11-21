"""Pairing v2 transcript and key schedule, with owned mutable secret buffers."""
import hashlib
import hmac
import secrets
import unicodedata
from .p256 import Q, P, G, M, N, PointError, decode_point, encode_point, ladder_multiply, jacobian_add

def wipe(*values):
    for value in values:
        if isinstance(value, bytearray):
            value[:] = b'\0'*len(value)

def normalise_password(text):
    if not isinstance(text, str):
        raise ValueError('bad-password')
    text = unicodedata.normalize('NFKC', text)
    scalars, words, in_word = 0, 0, False
    for ch in text:
        cp = ord(ch)
        if unicodedata.category(ch) == 'Cc' or 0xd800 <= cp <= 0xdfff:
            raise ValueError('bad-password')
        scalars += 1
        space = cp in (0x20,0xa0,0x1680,0x2028,0x2029,0x202f,0x205f,0x3000) or 0x2000 <= cp <= 0x200a
        if not space and not in_word:
            words += 1
        in_word = not space
    raw = text.encode('utf-8', 'strict')
    if len(raw) > 1024 or (scalars < 12 and words < 4):
        raise ValueError('bad-password')
    return raw

def password_scalar(pw, relay_pin, node_pin, relay_nonce, node_nonce):
    if not isinstance(pw,(bytes,bytearray)) or not pw or len(pw) > 1024 or any(not isinstance(x,(bytes,bytearray)) or len(x) != 32 for x in (relay_pin,node_pin,relay_nonce,node_nonce)) or relay_pin == node_pin:
        raise PointError()
    salt = b'D32U/1 pairing v2' + bytes(relay_pin) + bytes(node_pin) + bytes(relay_nonce) + bytes(node_nonce)
    raw = bytearray(hashlib.pbkdf2_hmac('sha256', pw, salt, 600000, 32))
    try:
        return (int.from_bytes(raw, 'big') % Q).to_bytes(32, 'big')
    finally:
        wipe(raw)

class Spake2:
    def __init__(self, role, w, scalar=None):
        if role not in ('A','B') or not isinstance(w,(bytes,bytearray)) or len(w) != 32 or int.from_bytes(w,'big') >= Q or scalar is not None and (not isinstance(scalar,(bytes,bytearray)) or len(scalar) != 32 or int.from_bytes(scalar,'big') >= Q):
            raise PointError()
        self.role, self.finished, self.erased = role, False, False
        self.w, self.scalar = bytearray(w), bytearray()
        self.K = self.TT = self.Ke = self.Ka = self.KcA = self.KcB = bytearray()
        self.own_mac = self.peer_mac = bytearray()
        if scalar is None:
            while True:
                self.scalar = bytearray(secrets.token_bytes(32))
                if int.from_bytes(self.scalar,'big') < Q:
                    break
                wipe(self.scalar)
        else:
            self.scalar = bytearray(scalar)
        try:
            if len(self.scalar) != 32 or int.from_bytes(self.scalar,'big') >= Q:
                raise PointError()
            self.share = encode_point(jacobian_add(ladder_multiply(G,self.scalar),ladder_multiply(M if role == 'A' else N,self.w)))
        except Exception:
            self.erase()
            raise

    def finish(self, id_a, id_b, pA, pB):
        if self.finished or self.erased or self.share != (pA if self.role == 'A' else pB):
            raise PointError()
        self.finished = True
        peer = (*decode_point(pB if self.role == 'A' else pA), 1)
        mask = ladder_multiply(N if self.role == 'A' else M,self.w)
        mask = (mask[0], -mask[1] % P, mask[2])
        unmasked = jacobian_add(peer, mask)
        if unmasked[2] == 0:
            raise PointError()
        self.K = bytearray(encode_point(ladder_multiply(unmasked,self.scalar)))
        self.TT = bytearray()
        for value in (id_a,id_b,pA,pB,self.K,self.w):
            self.TT.extend(len(value).to_bytes(8,'little'))
            self.TT.extend(value)
        digest = bytearray(hashlib.sha256(self.TT).digest())
        self.Ke, self.Ka = digest[:16], digest[16:]
        prk = bytearray(hmac.digest(bytes(32),self.Ka,'sha256'))
        okm = bytearray(hmac.digest(prk,b'ConfirmationKeys\x01','sha256'))
        self.KcA, self.KcB = okm[:16],okm[16:]
        self.own_mac = bytearray(hmac.digest(self.KcA if self.role == 'A' else self.KcB,self.TT,'sha256'))
        self.peer_mac = bytearray(hmac.digest(self.KcB if self.role == 'A' else self.KcA,self.TT,'sha256'))
        wipe(digest,prk,okm)
        return self

    def verify(self, peer_mac):
        return self.finished and not self.erased and hmac.compare_digest(self.peer_mac,peer_mac)

    def erase(self):
        for value in vars(self).values():
            wipe(value)
        self.erased = True

def vector(id_a, id_b, w, x, y):
    a, b = Spake2('A',w,x), Spake2('B',w,y)
    try:
        a.finish(id_a,id_b,a.share,b.share)
        b.finish(id_a,id_b,a.share,b.share)
        if not a.verify(b.own_mac) or not b.verify(a.own_mac):
            raise PointError()
        return {k:bytes(v).hex() for k,v in [('pA',a.share),('pB',b.share),('K',a.K),('TT',a.TT),('hash',hashlib.sha256(a.TT).digest()),('Ke',a.Ke),('Ka',a.Ka),('KcA',a.KcA),('KcB',a.KcB),('macA',a.own_mac),('macB',b.own_mac)]}
    finally:
        a.erase()
        b.erase()
