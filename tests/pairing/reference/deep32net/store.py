"""Peer commit records, received credentials and managed hosts entries."""
import json
import os
from pathlib import Path
import re
import stat
from .errors import StateError, NetError
from .files import safe, state_dir, private_dir, atomic_write, read_bytes, remove
from .wire import valid_id, hex32, validate_handover, ProtocolError
from .tokens import write_secret, read_token

RECORD_KEYS = ['format','format_version','kind','side','peer_id','peer_role','peer_pin','own_pin','transport','peer_address','peer_port','peer_host_name','peer_dns_name','publish','paired_at','received','given']

def link_name(kind, side):
    if kind not in ('relay','forge','gateway') or side not in ('joined','joiner'):
        raise StateError()
    return kind+'-'+side

def record_path(state, kind, side, peer_id):
    if not valid_id(peer_id):
        raise StateError()
    return safe(Path(state)/'peers'/link_name(kind,side)/(peer_id+'.json'))

def record(kind, side, peer_id, peer_role, peer_pin, own_pin, transport,
           paired_at, peer_address=None, peer_port=None, peer_host_name=None,
           peer_dns_name=None, publish=None, received=None, given=None):
    return dict(zip(RECORD_KEYS,['deep32-peer',1,kind,side,peer_id,peer_role,peer_pin,own_pin,transport,peer_address,peer_port,peer_host_name,peer_dns_name,publish,paired_at,received or [],given or []]))

def _validate(obj):
    if type(obj) is not dict or list(obj) != RECORD_KEYS or obj['format'] != 'deep32-peer' or type(obj['format_version']) is not int or obj['format_version'] != 1 or not valid_id(obj['peer_id']) or not hex32(obj['peer_pin']) or not hex32(obj['own_pin']) or obj['transport'] not in ('tailnet','loopback') or type(obj['paired_at']) is not int:
        raise StateError()
    link_name(obj['kind'],obj['side'])
    for key in ('received','given'):
        if type(obj[key]) is not list or any(v not in ('forge-token','kb-public.pem','mcp-token','model-endpoints.json') for v in obj[key]):
            raise StateError()
    return obj

def has_record(state, kind, side, peer_id):
    p = record_path(state,kind,side,peer_id)
    if not p.exists():
        return False
    load_record(p)
    return True

def load_record(path):
    try:
        return _validate(json.loads(read_bytes(path)))
    except (ValueError,UnicodeError):
        raise StateError() from None

def save_record(state, obj):
    state_dir(state)
    _validate(obj)
    p = record_path(state,obj['kind'],obj['side'],obj['peer_id'])
    atomic_write(p,(json.dumps(obj,indent=2,ensure_ascii=True)+'\n').encode('ascii'),overwrite=False)
    return p

def received_dir(state, kind, peer_id):
    if kind not in ('forge','gateway','relay') or not valid_id(peer_id):
        raise StateError()
    return safe(Path(state)/'private/received'/kind/peer_id)

def save_received(state, kind, peer_id, handover):
    try:
        validate_handover(handover,kind)
    except ProtocolError:
        raise StateError() from None
    directory = private_dir(received_dir(state,kind,peer_id))
    files = {'forge-token':handover['forge_token']+'\n','kb-public.pem':handover['kb_public_key']} if kind == 'forge' else {'mcp-token':handover['mcp_token']+'\n'}
    if kind == 'forge' and 'model_endpoints' in handover:
        files['model-endpoints.json'] = json.dumps(handover['model_endpoints'], sort_keys=True) + '\n'
    made = []
    try:
        for name,value in sorted(files.items()):
            atomic_write(directory/name,value.encode('ascii'),overwrite=False)
            made.append(directory/name)
    except Exception:
        for p in made:
            remove(p)
        if not list(directory.iterdir()):
            directory.rmdir()
        raise
    return sorted(files)

def rollback(state, kind, side, peer_id, received=False):
    remove(record_path(state,kind,side,peer_id))
    if received:
        directory = received_dir(state,kind,peer_id)
        if directory.exists():
            for path in sorted(directory.iterdir()):
                safe(path)
                if path.name not in ('forge-token','kb-public.pem','mcp-token','model-endpoints.json') or not path.is_file():
                    raise StateError()
                remove(path)
            directory.rmdir()

def remove_peer(state, kind, side, peer_id):
    p = record_path(state,kind,side,peer_id)
    if not p.exists():
        raise NetError('not-paired',7)
    load_record(p)
    rollback(state,kind,side,peer_id,side == 'joiner')
    if kind == 'gateway' and side == 'joined':
        remove(safe(Path(state)/'private/mcp-tokens'/peer_id))

def list_peers(state):
    root = state_dir(state)
    p = safe(root/'peers')
    if not p.exists():
        return []
    rows = []
    for directory in sorted(p.iterdir()):
        safe(directory)
        if not directory.is_dir() or directory.name not in [link_name(k,s) for k in ('forge','gateway','relay') for s in ('joined','joiner')]:
            raise StateError()
        for item in sorted(directory.iterdir()):
            if item.suffix != '.json':
                raise StateError()
            obj = load_record(safe(item))
            if directory.name != link_name(obj['kind'],obj['side']) or item.stem != obj['peer_id']:
                raise StateError()
            rows.append(obj)
    return rows

BEGIN = b'# deep32 peers begin\n'
END = b'# deep32 peers end\n'

def hosts_entries(records):
    entries = []
    for r in records:
        if r['side'] != 'joiner' or r['transport'] != 'tailnet' or not r['peer_address'] or not r['peer_dns_name']:
            continue
        address,name = r['peer_address'],r['peer_dns_name']
        from .transport import tailnet_ipv4
        if not tailnet_ipv4(address) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',name):
            raise StateError()
        entries.append((name,address))
    return sorted(entries,key=lambda entry:entry[0])

def render_hosts_block(records):
    return BEGIN.decode() + ''.join('%s %s %s\n' % (address,name,name.split('.')[0]) for name,address in hosts_entries(records)) + END.decode()

def update_hosts_file(path, records):
    p = safe(path)
    raw = read_bytes(p) if p.exists() else b''
    mode = stat.S_IMODE(p.stat().st_mode) if p.exists() else 0o600
    starts = [m.start() for m in re.finditer(rb'(?m)^# deep32 peers begin\n',raw)]
    ends = [m.start() for m in re.finditer(rb'(?m)^# deep32 peers end\n',raw)]
    if raw.count(BEGIN.rstrip(b'\n')) != len(starts) or raw.count(END.rstrip(b'\n')) != len(ends) or len(starts) > 1 or len(ends) > 1 or bool(starts) != bool(ends) or starts and ends[0] < starts[0]:
        raise StateError()
    block = render_hosts_block(records).encode('ascii') if hosts_entries(records) else b''
    if starts:
        out = raw[:starts[0]]+block+raw[ends[0]+len(END):]
    else:
        out = raw + (b'\n' if raw and not raw.endswith(b'\n') and block else b'') + block
    atomic_write(p,out,mode=mode)
    return len(hosts_entries(records))

def mcp_tokens(state):
    p = safe(Path(state)/'private/mcp-tokens')
    if not p.exists():
        return {}
    result = {}
    for item in sorted(p.iterdir()):
        if not valid_id(item.name):
            raise StateError()
        result[item.name] = read_token(safe(item))
    return result
