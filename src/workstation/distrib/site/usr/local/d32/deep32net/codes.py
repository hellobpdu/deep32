"""The short code profile. Codes do not use password normalisation."""
import base64
import re
import secrets

ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
LENGTH = 10

class CodeError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)

def new_code():
    return base64.b32encode(secrets.token_bytes(7)).decode('ascii')[:10]

def is_canonical(s):
    return isinstance(s,str) and re.fullmatch('[A-Z2-7]{10}',s) is not None

def normalise_code(text):
    if not isinstance(text,str):
        raise CodeError('bad-request')
    if len(text) > 64:
        raise CodeError('too-long')
    if any(ord(ch) >= 128 for ch in text):
        raise CodeError('non-ascii')
    text = text.replace(' ','').replace('\t','').replace('-','')
    if not text:
        raise CodeError('empty')
    text = text.upper().translate(str.maketrans('018','OIB'))
    if any(ch not in ALPHABET for ch in text):
        raise CodeError('bad-symbol')
    if len(text) != LENGTH:
        raise CodeError('length')
    return text.encode('ascii')
