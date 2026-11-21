"""Name-based candidates and a probe that reads one HELLO without sending."""
import hashlib
import json
import re
import socket
import ssl
import subprocess
from .errors import NetError, ProtocolError, ConnectionLost
from .pairing import tls_context
from .transport import Transport, PAIRING_PORTS, tailnet_ipv4, port_number
from . import wire

def _name(value):
    return value if isinstance(value,str) and value else None

def _dns(value):
    value = _name(value)
    return value.removesuffix('.') if value else None

def _ip(values):
    return next((v for v in values if tailnet_ipv4(v)),None) if isinstance(values,list) else None

def parse_status(obj):
    if type(obj) is not dict:
        raise NetError('status-unreadable',9)
    backend = obj.get('BackendState')
    if backend != 'Running':
        raise NetError('not-signed-in',9,backend_state=backend if isinstance(backend,str) else None)
    own = obj.get('Self') or {}
    peers = obj.get('Peer') or {}
    if type(own) is not dict or type(peers) is not dict:
        raise NetError('status-unreadable',9)
    self_info = dict(host_name=_name(own.get('HostName')),dns_name=_dns(own.get('DNSName')),ipv4=_ip(own.get('TailscaleIPs')))
    result = []
    for peer in peers.values():
        if type(peer) is not dict:
            raise NetError('status-unreadable',9)
        host,dns = _name(peer.get('HostName')),_dns(peer.get('DNSName'))
        if not any(re.match('deep32-',s or '',re.IGNORECASE | re.ASCII) for s in (host,(dns or '').split('.')[0])) or peer.get('Expired') is True or peer.get('ShareeNode') is True:
            continue
        path = 'unknown'
        if peer.get('Active') is True:
            if isinstance(peer.get('CurAddr'),str) and peer['CurAddr']:
                path = 'direct'
            elif isinstance(peer.get('Relay'),str) and peer['Relay']:
                path = 'relayed'
        result.append(dict(peer_id=_name(peer.get('ID')),host_name=host,dns_name=dns,os=_name(peer.get('OS')),ipv4=_ip(peer.get('TailscaleIPs')),port=None,online=peer.get('Online') is True,path=path,probe=None))
    result.sort(key=lambda p:(not p['online'],(p['host_name'] or '').lower(),p['peer_id'] or ''))
    return backend,self_info,result

def probe_candidate(candidate, want, endpoint=None):
    for condition,reason in [(not candidate['online'],'offline'),(candidate['ipv4'] is None,'no-ipv4'),(want == 'relay','relay-not-probed')]:
        if condition:
            return dict(done=False,reason=reason)
    address,port = endpoint or (candidate['ipv4'],candidate['port'])
    raw,sock = None,None
    try:
        try:
            raw = socket.create_connection((address,port),timeout=3)
        except ConnectionRefusedError:
            return dict(done=False,reason='connect-refused')
        except TimeoutError:
            return dict(done=False,reason='timeout')
        except OSError:
            return dict(done=False,reason='connect-refused')
        raw.settimeout(10)
        try:
            sock = tls_context().wrap_socket(raw,server_hostname=None)
        except TimeoutError:
            return dict(done=False,reason='timeout')
        except OSError:
            return dict(done=False,reason='tls-failed')
        try:
            f = wire.read_frame(sock,10,True,overall_timeout=10)
            if f.type != 1:
                raise ProtocolError()
            obj = wire.loads_strict(f.payload)
            found = None
            for kind in ('relay','forge','gateway'):
                try:
                    wire.validate_hello(obj,kind,'A')
                    found = kind
                    break
                except (ProtocolError,TypeError):
                    pass
            if found is None:
                raise ProtocolError()
            if found != want:
                return dict(done=True,deep32=False,reason='wrong-role')
            return dict(done=True,deep32=True,role=obj['role'],id=obj['id'],pin=hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest(),features=obj['features'])
        except TimeoutError:
            return dict(done=False,reason='timeout')
        except (ProtocolError,ConnectionLost,OSError):
            return dict(done=True,deep32=False,reason='not-deep32')
    finally:
        if sock:
            sock.close()
        elif raw:
            raw.close()

def status_runner():
    try:
        result = subprocess.run(['tailscale','status','--json'],capture_output=True,timeout=15,check=True)
        return json.loads(result.stdout)
    except (OSError,ValueError,subprocess.SubprocessError):
        raise NetError('status-unreadable',9) from None

def discover(want, transport, status_obj_or_runner=None, probe=True, endpoint_override=None, port=None):
    if want not in PAIRING_PORTS:
        raise NetError('bad-request',2)
    mode = transport.mode if isinstance(transport,Transport) else Transport(transport).mode
    selected_port = port_number(PAIRING_PORTS[want] if port is None else port)
    if mode == 'loopback':
        backend,self_info = None,None
        candidates = [dict(peer_id=None,host_name=None,dns_name=None,os=None,ipv4='127.0.0.1',port=selected_port,online=True,path='loopback',probe=None)]
    else:
        obj = status_obj_or_runner
        if obj is None:
            obj = status_runner()
        elif callable(obj):
            obj = obj()
        backend,self_info,candidates = parse_status(obj)
        for p in candidates:
            p['port'] = selected_port
    if probe:
        for p in candidates:
            endpoint = endpoint_override(p) if endpoint_override else None
            p['probe'] = probe_candidate(p,want,endpoint)
    return dict(transport=mode,want=want,backend_state=backend,self=self_info,candidates=candidates)
