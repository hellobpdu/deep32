"""Machine-facing command dispatch."""
import argparse
import json
import re
import sys
from pathlib import Path
from . import __version__
from .errors import NetError, StateError, Exists, ProtocolError
from .codes import new_code, is_canonical
from .files import safe
from .identity import ensure_identity
from .pairing import JoinedSide, Joiner
from .tokens import make_token, write_secret, read_token, update_edge_env
from . import store, wire, discovery
from .transport import Transport, port_number

HELPER_REQUIRED = {'code','expires_seconds','attempts','form','transport','publish','token_path','kb_public_path','state_dir'}
HELPER_OPTIONAL = {'port','ready_file','openssl','model_endpoints'}

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise NetError('bad-request',2)

def request(required, optional):
    try:
        data = sys.stdin.buffer.read(65537)
        if len(data) > 65536:
            raise ValueError()
        data.decode('ascii')
        obj = wire.loads_strict(data)
        if not set(required) <= set(obj) or set(obj)-set(required)-set(optional):
            raise ValueError()
        for k,v in obj.items():
            if k == 'model_endpoints':
                wire.validate_model_endpoints(v)
            elif k in ('expires_seconds','attempts','port'):
                if type(v) is not int:
                    raise ValueError()
            elif type(v) is not str:
                raise ValueError()
        return obj
    except (ValueError,UnicodeError,ProtocolError):
        raise NetError('bad-request',2) from None

def _serve(req):
    kind = req['kind']
    if kind not in ('forge','gateway') or req['expires_seconds'] != 300 or req['attempts'] != 1:
        raise NetError('bad-request',2)
    required = {'kind','code','expires_seconds','attempts','transport','publish','state_dir'}
    if kind == 'forge':
        required |= {'token_path','kb_public_path'}
    optional = {'port','ready_file','openssl','model_endpoints'}
    if not required <= set(req) or set(req)-required-optional:
        raise NetError('bad-request',2)
    args = {k:v for k,v in req.items() if k not in ('expires_seconds','attempts','state_dir')}
    args['state'] = req['state_dir']
    return JoinedSide(**args).serve()

def _parser(verb):
    p = Parser(add_help=False)
    if verb == 'identity':
        p.add_argument('--state-dir',required=True)
        p.add_argument('--node',choices=['forge','workstation','headend'],required=True)
        p.add_argument('--openssl')
    elif verb == 'discover':
        p.add_argument('--want',choices=['forge','gateway','relay'],required=True)
        p.add_argument('--transport',choices=['tailnet','loopback'],required=True)
        p.add_argument('--status-json')
        p.add_argument('--no-probe',action='store_true')
        p.add_argument('--port',type=int)
    elif verb in ('peers','unpair','write-edge-tokens','hosts'):
        p.add_argument('--state-dir',required=True)
        if verb == 'unpair':
            p.add_argument('--kind',choices=['relay','forge','gateway'],required=True)
            p.add_argument('--side',choices=['joined','joiner'],required=True)
            p.add_argument('--peer-id',required=True)
            p.add_argument('--token-path')
            p.add_argument('--edge-env')
            p.add_argument('--hosts-file')
        if verb == 'write-edge-tokens':
            p.add_argument('--env-file',required=True)
        if verb == 'hosts':
            p.add_argument('--hosts-file',required=True)
    elif verb == 'make-token':
        p.add_argument('--path',required=True)
        p.add_argument('--rotate',action='store_true')
    return p

def dispatch(verb, args, helper=False):
    if helper:
        if verb == 'host-signin' and not args:
            raise NetError('not-provided',64)
        if verb != 'pair-forge' or args:
            raise NetError('bad-request',2)
        req = request(HELPER_REQUIRED,HELPER_OPTIONAL)
        if req.pop('form') not in ('iso','wsl','docker'):
            raise NetError('bad-request',2)
        req['kind'] = 'forge'
        return _serve(req)
    if verb == 'serve':
        if args:
            raise NetError('bad-request',2)
        req = request({'kind','code','expires_seconds','attempts','transport','publish','state_dir'},{'token_path','kb_public_path','port','ready_file','openssl','model_endpoints'})
        return _serve(req)
    if verb == 'join':
        if args:
            raise NetError('bad-request',2)
        req = request({'kind','code','transport','state_dir'},{'address','port','expect_pin','peer_host_name','peer_dns_name','hosts_file','openssl'})
        if req['transport'] == 'tailnet' and 'address' not in req:
            raise NetError('bad-request',2)
        for key in ('peer_host_name','peer_dns_name'):
            if key in req and not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',req[key]):
                raise NetError('bad-request',2)
        req['state'] = req.pop('state_dir')
        req['code_text'] = req.pop('code')
        return Joiner(**req).join()
    if verb not in ('identity','discover','peers','unpair','make-token','write-edge-tokens','hosts'):
        raise NetError('bad-request',2)
    a = _parser(verb).parse_args(args)
    if verb == 'identity':
        ident = ensure_identity(a.state_dir,a.node,a.openssl)
        return dict(id=ident.id,pin=ident.pin,created=ident.created)
    if verb == 'discover':
        obj = None
        if a.status_json and a.transport != 'loopback':
            try:
                obj = json.loads(Path(a.status_json).read_bytes())
            except (OSError,ValueError):
                raise NetError('status-unreadable',9) from None
        return discovery.discover(a.want,a.transport,obj,probe=not a.no_probe,port=a.port)
    if verb == 'make-token':
        existed = safe(a.path).exists()
        write_secret(a.path,make_token(),overwrite=a.rotate)
        return dict(created=not existed,rotated=existed and a.rotate)
    if verb == 'peers':
        return dict(peers=store.list_peers(a.state_dir))
    if verb == 'write-edge-tokens':
        values = store.mcp_tokens(a.state_dir)
        update_edge_env(a.env_file,values)
        return dict(count=len(values))
    if verb == 'hosts':
        return dict(entries=store.update_hosts_file(a.hosts_file,store.list_peers(a.state_dir)))
    rows = store.list_peers(a.state_dir)
    if not any(r['kind'] == a.kind and r['side'] == a.side and r['peer_id'] == a.peer_id for r in rows):
        raise NetError('not-paired',7)
    rotated,restart,must = False,[],[]
    if a.kind == 'forge' and a.side == 'joined':
        if not a.token_path:
            raise NetError('bad-request',2)
        read_token(a.token_path)
        write_secret(a.token_path,make_token(),overwrite=True)
        rotated,restart = True,['door']
        must = sorted(r['peer_id'] for r in rows if r['kind'] == 'forge' and r['side'] == 'joined' and r['peer_id'] != a.peer_id)
    store.remove_peer(a.state_dir,a.kind,a.side,a.peer_id)
    if a.kind == 'gateway' and a.side == 'joined' and a.edge_env:
        update_edge_env(a.edge_env,store.mcp_tokens(a.state_dir))
    if a.side == 'joiner' and a.hosts_file:
        store.update_hosts_file(a.hosts_file,store.list_peers(a.state_dir))
    return dict(removed=True,rotated=rotated,restart_required=restart,must_repair=must)

def _main(argv, helper=False):
    program = 'pairing-helper' if helper else 'deep32-pair'
    if argv == ['--help']:
        print(sys.modules['__main__'].__doc__ or '')
        return 0
    if argv == ['--version']:
        print(program+' '+__version__)
        return 0
    if not helper and argv == ['new-code']:
        print(new_code())
        return 0
    verb = argv[0] if argv and argv[0] in {'identity','serve','join','discover','peers','unpair','make-token','write-edge-tokens','hosts','pair-forge','host-signin'} else None
    result = dict(schema='deep32.net.v1',tool=__version__,verb=verb,ok=True,exit=0,reason=None)
    try:
        if verb is None:
            raise NetError('bad-request',2)
        result.update(dispatch(verb,argv[1:],helper))
    except NetError as exc:
        result.update(ok=False,exit=exc.exit_code,reason=exc.reason,**exc.fields)
    except Exception:
        result.update(ok=False,exit=1,reason='internal')
    print(wire.dumps_compact(result).decode('ascii'))
    if not result['ok']:
        print(program+': '+result['reason'],file=sys.stderr)
    return result['exit']

def main(argv=None):
    return _main(sys.argv[1:] if argv is None else argv)

def helper_main(argv=None):
    return _main(sys.argv[1:] if argv is None else argv,True)
