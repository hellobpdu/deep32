"""Explicit loopback or tailnet peer endpoints; listeners stay local."""
import ipaddress
from .errors import NetError

PAIRING_PORTS = {'relay':7452,'forge':7453,'gateway':7454}
SERVICE_PORTS = {'engine':8765,'gateway_mcp':8766,'embeddings':8081,'reranker':8082,'reranker_https':8443}
_TAILNET = ipaddress.IPv4Network((0x64400000,10))

def tailnet_ipv4(address):
    try:
        return isinstance(address,str) and ipaddress.IPv4Address(address) in _TAILNET
    except ipaddress.AddressValueError:
        return False

def port_number(port, zero=False):
    if type(port) is not int or not (0 if zero else 1) <= port <= 65535:
        raise NetError('bad-request',2)
    return port

def bind_address(requested='127.0.0.1'):
    if requested != '127.0.0.1':
        raise NetError('bad-request',2)
    return requested

class Transport:
    def __init__(self, mode):
        if mode not in ('tailnet','loopback'):
            raise NetError('bad-request',2)
        self.mode = mode

    def peer_endpoint(self, kind, candidate=None, address=None, port=None):
        if kind not in PAIRING_PORTS:
            raise NetError('bad-request',2)
        port = port_number(PAIRING_PORTS[kind] if port is None else port)
        address = address if address is not None else (candidate or {}).get('ipv4')
        if self.mode == 'loopback':
            if address not in (None,'127.0.0.1'):
                raise NetError('bad-request',2)
            return '127.0.0.1',port
        if not tailnet_ipv4(address):
            raise NetError('bad-request',2)
        return address,port
