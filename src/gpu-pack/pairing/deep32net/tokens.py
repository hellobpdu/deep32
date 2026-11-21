"""Local token and knowledge-base public-key files."""
import re
import secrets
from .files import atomic_write, read_bytes
from .errors import StateError, ProtocolError
from .wire import kb_public

def make_token():
    return secrets.token_hex(32)

def write_secret(path, value, overwrite=False):
    atomic_write(path,(value+'\n').encode('ascii'),overwrite=overwrite)

def read_token(path):
    raw = read_bytes(path)
    if not re.fullmatch(rb'[0-9a-f]{64}\n?',raw):
        raise StateError()
    return raw.decode('ascii').rstrip('\n')

def read_kb_public(path):
    try:
        return kb_public(read_bytes(path).decode('ascii'))
    except (UnicodeError,ProtocolError):
        raise StateError() from None

def update_edge_env(path, tokens):
    from .files import safe
    p = safe(path)
    raw = read_bytes(p) if p.exists() else b''
    values = [tokens[k] for k in sorted(tokens)] if isinstance(tokens,dict) else list(tokens)
    if any(not re.fullmatch('[0-9a-f]{64}',v) for v in values):
        raise StateError()
    line = ('RAG_MCP_TOKENS='+','.join(values)+'\n').encode('ascii')
    lines = raw.splitlines(keepends=True)
    output,found = [],False
    for old in lines:
        if old.startswith(b'RAG_MCP_TOKENS='):
            if found:
                raise StateError()
            output.append(line)
            found = True
        else:
            output.append(old)
    if not found:
        if raw and not raw.endswith(b'\n'):
            output.append(b'\n')
        output.append(line)
    atomic_write(p,b''.join(output))
