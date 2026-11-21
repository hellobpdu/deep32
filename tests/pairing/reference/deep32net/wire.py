"""D32U/1 frames and strict pairing message validation."""
import base64
from dataclasses import dataclass
import json
import math
import re
import struct
import time
import weakref
from .errors import ProtocolError, LimitError, ConnectionLost

NAMES = {1:'HELLO',2:'HELLO_ACK',3:'OPEN',4:'OPEN_ACK',5:'DATA',6:'WINDOW_UPDATE',7:'CLOSE',8:'RESET',9:'PING',10:'PONG',11:'ERROR',12:'PAIR_CONFIRM',13:'PAIR_COMMIT',14:'PAIR_SHARE',15:'PAIR_HANDOVER'}
HEADER = struct.Struct('!IIBBH')
_rates = weakref.WeakKeyDictionary()

@dataclass(frozen=True)
class Frame:
    type: int
    payload: bytes = b''
    stream: int = 0
    channel: int = 0
    flags: int = 0

def validate_header(type, stream, channel, flags, length, handover_allowed=False):
    if length > 1048576:
        raise LimitError()
    if type not in NAMES or type == 15 and not handover_allowed:
        raise ProtocolError()
    if flags & ~1 if type == 5 else flags != 0:
        raise ProtocolError()
    control = type in (1,2,9,10,11,12,13,14,15)
    if (stream != 0 or channel != 0) if control else (stream == 0 or channel == 0):
        raise ProtocolError()
    if type in (1,2,3,15) and length > 16384:
        raise LimitError()
    if type in (4,7,13) and length != 0 or type in (6,8,11) and length != 4 or type in (9,10) and length != 8 or type == 12 and length != 32 or type == 14 and length != 65 or type in (5,15) and length == 0:
        raise ProtocolError()

def encode_frame(type, payload=b'', stream=0, channel=0, flags=0, handover_allowed=False):
    validate_header(type,stream,channel,flags,len(payload),handover_allowed)
    try:
        return HEADER.pack(len(payload),stream,type,channel,flags) + bytes(payload)
    except (struct.error,TypeError,OverflowError):
        raise ProtocolError() from None

def decode_frame(data, handover_allowed=False):
    if len(data) < 12:
        raise ProtocolError()
    length,stream,type,channel,flags = HEADER.unpack(data[:12])
    validate_header(type,stream,channel,flags,length,handover_allowed)
    if len(data) != 12+length:
        raise ProtocolError()
    return Frame(type,data[12:],stream,channel,flags)

def _exact(sock, n, deadline):
    raw = bytearray()
    while len(raw) < n:
        left = deadline-time.monotonic()
        if left <= 0:
            raise TimeoutError()
        sock.settimeout(left)
        try:
            part = sock.recv(n-len(raw))
        except (ConnectionError,OSError) as e:
            if isinstance(e,TimeoutError):
                raise
            raise ConnectionLost() from None
        if not part:
            raise ConnectionLost()
        raw.extend(part)
    return bytes(raw)

def read_frame(sock, first_byte_timeout=10, handover_allowed=False, overall_timeout=None):
    start = time.monotonic()
    first = _exact(sock,1,start+first_byte_timeout)
    deadline = time.monotonic()+10
    if overall_timeout is not None:
        deadline = min(deadline,start+overall_timeout)
    h = first + _exact(sock,11,deadline)
    length,stream,type,channel,flags = HEADER.unpack(h)
    validate_header(type,stream,channel,flags,length,handover_allowed)
    payload = _exact(sock,length,deadline)
    now = time.time()
    bucket,count = _rates.get(sock,(now,0))
    if now-bucket >= 1:
        bucket,count = now,0
    count += 1
    _rates[sock] = (bucket,count)
    if count > 256:
        raise LimitError()
    return Frame(type,payload,stream,channel,flags)

def loads_strict(b):
    def pairs(items):
        d = {}
        for k,v in items:
            if k in d:
                raise ValueError()
            d[k] = v
        return d
    def invalid(value):
        raise ValueError()
    def floating(text):
        result = float(text)
        if not math.isfinite(result):
            raise ValueError()
        return result
    def walk(value, depth=0):
        if depth > 32:
            raise ValueError()
        if isinstance(value,str):
            value.encode('utf-8','strict')
        elif isinstance(value,dict):
            for k,v in value.items():
                k.encode('utf-8','strict')
                walk(v,depth+1)
        elif isinstance(value,list):
            for v in value:
                walk(v,depth+1)
    try:
        s = b.decode('utf-8','strict')
        if s.startswith('\ufeff'):
            raise ValueError()
        value = json.loads(s,object_pairs_hook=pairs,parse_constant=invalid,parse_float=floating,strict=True)
        if type(value) is not dict:
            raise ValueError()
        walk(value)
        return value
    except (ValueError,UnicodeError,RecursionError,TypeError):
        raise ProtocolError() from None

def dumps_compact(obj):
    return json.dumps(obj,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def keys(obj, required, optional=()):
    if type(obj) is not dict or not set(required) <= set(obj) or set(obj)-set(required)-set(optional):
        raise ProtocolError()

def valid_id(s):
    return isinstance(s,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',s) is not None

def hex32(s):
    return isinstance(s,str) and re.fullmatch('[0-9a-f]{64}',s) is not None

def _features(value, kind):
    if type(value) is not list or any(type(s) is not str for s in value) or len(value) > 2 or len(set(value)) != len(value):
        raise ProtocolError()
    if kind == 'relay':
        if not 1 <= len(value) <= 2 or 'research-v1' not in value or set(value)-{'research-v1','pairing-v2'}:
            raise ProtocolError()
        if 'pairing-v2' not in value:
            raise ProtocolError(12)
    elif not {'pairing-v2','handover-v1'} <= set(value):
        raise ProtocolError(12)

def certificate_der(text):
    try:
        if not isinstance(text,str) or len(text) > 4096:
            raise ValueError()
        der = base64.b64decode(text,validate=True)
        if not 64 <= len(der) <= 2048 or der[0] != 0x30:
            raise ValueError()
        first = der[1]
        at = 2
        if first < 128:
            size = first
        else:
            n = first & 127
            if n == 0 or n > 4 or at+n > len(der):
                raise ValueError()
            size = int.from_bytes(der[at:at+n],'big')
            at += n
        if at+size != len(der):
            raise ValueError()
        return der
    except (ValueError,TypeError,IndexError):
        raise ProtocolError() from None

def kb_public(text):
    try:
        if not isinstance(text,str) or len(text) > 1024 or '\r' in text:
            raise ValueError()
        match = re.fullmatch(r'-----BEGIN PUBLIC KEY-----\n((?:[A-Za-z0-9+/=]+\n)+)-----END PUBLIC KEY-----\n',text)
        if not match:
            raise ValueError()
        der = base64.b64decode(match[1].replace('\n',''),validate=True)
        if len(der) != 44 or not der.startswith(bytes.fromhex('302a300506032b6570032100')):
            raise ValueError()
        return text
    except (ValueError,TypeError):
        raise ProtocolError() from None

def features(kind):
    return ['research-v1','pairing-v2'] if kind == 'relay' else ['pairing-v2','handover-v1']

def role(kind, side):
    return {'relay':('relay','node'),'forge':('forge','workstation'),'gateway':('gateway','headend')}[kind][0 if side == 'A' else 1]

def hello(kind, side, id, nonce, cert=None):
    obj = dict(proto='D32U/1',id=id,version=1,versions=[1],role=role(kind,side),features=features(kind),nonce=nonce,sessions=[])
    if side == 'B' and kind != 'relay':
        obj['cert'] = cert
    if side == 'A' and kind == 'relay':
        obj.update(codex_version='unknown',signed_in=False,profiles=[dict(name='research',models=[],efforts=[])])
    return obj

def ack(kind, paired=False):
    return dict(version=1,features=features(kind),paired=paired)

def validate_model_endpoints(obj):
    """Two path-free HTTPS bases; the consumer binds their DNS to the paired peer."""
    keys(obj, ['embeddings', 'reranker'])
    hosts = set()
    for value in obj.values():
        if not isinstance(value, str):
            raise ProtocolError()
        match = re.fullmatch(r'https://([a-z0-9](?:[a-z0-9.-]{0,245}[a-z0-9])?\.ts\.net)(?::([1-9][0-9]{0,4}))?', value)
        if (not match or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                             for label in match[1].split('.')) or
                match[2] is not None and not 1 <= int(match[2]) <= 65535):
            raise ProtocolError()
        hosts.add(match[1])
    if len(hosts) != 1:
        raise ProtocolError()
    return dict(obj)


def handover(kind, token, public=None, publish='serve-https', model_endpoints=None):
    if kind == 'forge':
        obj = dict(kind='forge',forge_token=token,kb_public_key=public,publish=publish)
        if model_endpoints is not None:
            obj['model_endpoints'] = validate_model_endpoints(model_endpoints)
        return obj
    return dict(kind='gateway',mcp_token=token,publish=publish)

def validate_hello(obj, kind, side):
    required = ['proto','id','version','versions','role','features','nonce','sessions']
    if side == 'B' and kind != 'relay':
        required += ['cert']
    keys(obj,required,['codex_version','signed_in','profiles'] if kind == 'relay' and side == 'A' else [])
    if obj['proto'] != 'D32U/1' or not valid_id(obj['id']) or type(obj['version']) is not int or obj['version'] != 1 or type(obj['versions']) is not list or len(obj['versions']) != 1 or type(obj['versions'][0]) is not int or obj['versions'][0] != 1 or obj['role'] != role(kind,side) or type(obj['sessions']) is not list or obj['sessions'] != [] or not hex32(obj['nonce']):
        raise ProtocolError()
    _features(obj['features'],kind)
    if 'cert' in obj:
        certificate_der(obj['cert'])
    if 'codex_version' in obj and (not isinstance(obj['codex_version'],str) or not 1 <= len(obj['codex_version']) <= 64):
        raise ProtocolError()
    if 'signed_in' in obj and type(obj['signed_in']) is not bool:
        raise ProtocolError()
    if 'profiles' in obj:
        profiles = obj['profiles']
        if type(profiles) is not list or len(profiles) > 1:
            raise ProtocolError()
        for profile in profiles:
            keys(profile,['name','models','efforts'])
            if profile['name'] != 'research':
                raise ProtocolError()
            for field in ('models','efforts'):
                if type(profile[field]) is not list or any(not isinstance(s,str) or not 1 <= len(s) <= 64 for s in profile[field]):
                    raise ProtocolError()
    return obj

def validate_ack(obj, kind, side):
    keys(obj,['version','features','paired'])
    if type(obj['version']) is not int or obj['version'] != 1 or type(obj['paired']) is not bool or obj['paired'] and not (kind == 'relay' and side == 'A'):
        raise ProtocolError()
    _features(obj['features'],kind)
    return obj

def validate_handover(obj, kind):
    keys(obj,['kind','forge_token','kb_public_key','publish'] if kind == 'forge' else ['kind','mcp_token','publish'],
         ['model_endpoints'] if kind == 'forge' else [])
    if obj['kind'] != kind or obj['publish'] not in ('serve-https','tailnet-plain') or not hex32(obj['forge_token' if kind == 'forge' else 'mcp_token']):
        raise ProtocolError()
    if kind == 'forge':
        kb_public(obj['kb_public_key'])
        if 'model_endpoints' in obj:
            if obj['publish'] != 'serve-https':
                raise ProtocolError()
            validate_model_endpoints(obj['model_endpoints'])
    return obj
