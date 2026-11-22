"""Workstation service enrolment after the accepted pairing core has committed.

Credential values stay in dedicated files. This adapter does not publish routes,
choose model pins, or enable GPU_EXT. The caller stops consumers before rotation.
"""
import json
import os
from pathlib import Path
import re

from . import store
from .errors import StateError
from .files import atomic_write, private_dir, read_bytes, safe
from .tokens import read_kb_public, read_token
from .wire import loads_strict, validate_model_endpoints, ProtocolError
from urllib.parse import urlsplit


def _gid(name):
    if os.name != 'posix':
        return None
    import grp
    return grp.getgrnam(name).gr_gid


def _write(path, data, group=None, mode=0o600):
    # Windows test ACLs belong to the test installer. Its readonly attribute
    # would otherwise prevent the next atomic replacement of a POSIX 0440 file.
    atomic_write(path, data, mode=mode if os.name == 'posix' else 0o600)
    if os.name == 'posix':
        os.chown(path, 0, _gid(group) if group else 0)
        os.chmod(path, mode)


def _private(root):
    path = private_dir(safe(root / 'etc/d32/private'))
    if os.name == 'posix':
        os.chown(path, 0, _gid('wheel'))
        # Both consumers need traversal; each file has a different service group.
        # Neither consumer can list this directory or read the other's credential.
        os.chmod(path, 0o711)
    return path


def _settings(path, updates, remove=()):
    """Preserve existing signed model/CA settings; add only validated local values."""
    path = safe(path)
    lines = read_bytes(path).decode('ascii').splitlines() if path.exists() else []
    keys = set(updates) | set(remove)
    kept = [line for line in lines if line.partition('=')[0].strip() not in keys]
    for key, value in sorted(updates.items()):
        if not re.fullmatch(r'[A-Z_][A-Z0-9_]*', key) or any(c in value for c in '\r\n\x00'):
            raise StateError()
        kept.append(key + '=' + value)
    _write(path, ('\n'.join(kept) + '\n').encode('ascii'), mode=0o400)


def _peer(state, kind, side, peer_id):
    row = store.load_record(store.record_path(state, kind, side, peer_id))
    if (row['kind'], row['side']) != (kind, side):
        raise StateError()
    return row


def enrol_forge(state, peer_id, root=Path('/')):
    """Materialise a committed Forge handover in the fixed service credential store."""
    root = safe(root)
    row = _peer(state, 'forge', 'joiner', peer_id)
    expected = ['forge-token', 'kb-public.pem']
    endpoints_received = 'model-endpoints.json' in row['received']
    if endpoints_received:
        expected.append('model-endpoints.json')
    if sorted(row['received']) != expected:
        raise StateError()
    received = store.received_dir(state, 'forge', peer_id)
    token = read_token(received / 'forge-token')
    public = read_kb_public(received / 'kb-public.pem')
    if row['transport'] == 'loopback':
        host, address = 'localhost', '127.0.0.1'
        ports = ('8081', '8082')
    else:
        from .transport import tailnet_ipv4
        host, address = row['peer_dns_name'], row['peer_address']
        if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,251}[A-Za-z0-9]', host) or not tailnet_ipv4(address):
            raise StateError()
        ports = ('443', '8443')
    endpoints = None
    if endpoints_received:
        path = safe(received / 'model-endpoints.json')
        try:
            if path.stat().st_size > 2048 or row['transport'] != 'tailnet':
                raise ProtocolError()
            endpoints = validate_model_endpoints(loads_strict(read_bytes(path)))
            parsed = [urlsplit(endpoints[key]) for key in ('embeddings', 'reranker')]
            if any(url.hostname != host for url in parsed):
                raise ProtocolError()
            ports = tuple(str(url.port or 443) for url in parsed)
        except (OSError, ValueError, ProtocolError):
            raise StateError() from None
    private = _private(root)
    _write(private / 'forge.token', (token + '\n').encode('ascii'), '_d32gpu', 0o440)
    state_path = private_dir(root / 'etc/d32/state')
    _write(state_path / 'forge-kb-public.pem', public.encode('ascii'))
    updates = {
        'GPU_EXT_TOKEN_FILE': '/etc/d32/private/forge.token',
        'GPU_EXT_TRANSPORT': row['transport'],
        'GPU_EXT_HOST': host, 'GPU_EXT_ADDR': address,
        'GPU_EXT_EMBED_PORT': ports[0], 'GPU_EXT_RERANK_PORT': ports[1]}
    if endpoints is not None:
        updates.update(GPU_EXT_EMBED_URL=endpoints['embeddings'], GPU_EXT_RERANK_URL=endpoints['reranker'])
    _settings(root / 'etc/d32/accel.env', updates,
              remove=('GPU_EXT_EMBED_URL', 'GPU_EXT_RERANK_URL') if endpoints is None else ())
    result = {'format': 'deep32-forge-enrolment', 'format_version': 1,
              'peer_id': peer_id, 'peer_pin': row['peer_pin'], 'own_pin': row['own_pin'],
              'paired_at': row['paired_at'], 'credential_file': '/etc/d32/private/forge.token'}
    # Last write is the enrolment marker. No token or token-derived value is in it.
    _write(state_path / 'forge-enrolment.json', (json.dumps(result, sort_keys=True) + '\n').encode('ascii'))
    return result


def enrol_gateway(state, peer_id=None, root=Path('/')):
    """Retain the local app token and each independently issued headend token."""
    root = safe(root)
    if peer_id is not None:
        _peer(state, 'gateway', 'joined', peer_id)
    local = read_token(root / 'etc/d32/state/mcp.token')
    issued = store.mcp_tokens(state)
    committed = {row['peer_id'] for row in store.list_peers(state)
                 if (row['kind'], row['side']) == ('gateway', 'joined')}
    if committed != set(issued) or peer_id is not None and peer_id not in issued:
        raise StateError()
    # Never authorise a token without its committed peer record.
    for identity in issued:
        _peer(state, 'gateway', 'joined', identity)
    values = [local] + [issued[k] for k in sorted(issued)]
    if len(values) != len(set(values)):
        raise StateError()
    private = _private(root)
    _write(private / 'mcp-tokens', ('\n'.join(values) + '\n').encode('ascii'), '_d32edge', 0o440)
    _settings(root / 'etc/d32/edge.env', {'RAG_MCP_TOKENS_FILE': '/etc/d32/private/mcp-tokens'},
              remove=('RAG_MCP_TOKENS',))
    return {'headends': len(issued), 'local_token_kept': True}


ENGINE_TOKEN_FILES = {'core.env': ('/etc/d32/private/engine-api.token', '_d32core'),
                      'edge.env': ('/etc/d32/private/engine-client.token', '_d32edge')}


def enrol_engine(root=Path('/')):
    """Move the engine token from both environment files into one protected file per reading service."""
    root = safe(root)
    values, done = {}, {}
    for name, (target, _group) in ENGINE_TOKEN_FILES.items():
        lines = read_bytes(root / 'etc/d32' / name).decode('ascii').splitlines()
        plain = [line.partition('=')[2] for line in lines if line.startswith('RAG_CORE_API_TOKEN=')]
        named = [line.partition('=')[2] for line in lines if line.startswith('RAG_CORE_API_TOKEN_FILE=')]
        if len(plain) + len(named) != 1:
            raise StateError()
        if named:
            if named[0] != target:
                raise StateError()
            done[name] = read_token(root / target.lstrip('/'))
        else:
            values[name] = plain[0]
    if done and values:
        raise StateError()
    tokens = set(done.values()) if done else set(values.values())
    if len(tokens) != 1 or not re.fullmatch('[0-9a-f]{64}', next(iter(tokens))):
        raise StateError()
    if done:
        return {'engine_token': 'kept'}  # nosec B105
    token = tokens.pop()
    private = _private(root)
    for name, (target, group) in ENGINE_TOKEN_FILES.items():
        _write(private / Path(target).name, (token + '\n').encode('ascii'), group, 0o440)
        _settings(root / 'etc/d32' / name, {'RAG_CORE_API_TOKEN_FILE': target}, remove=('RAG_CORE_API_TOKEN',))
    return {'engine_token': 'moved'}  # nosec B105
