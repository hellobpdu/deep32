"""One pairing exchange on real TLS, with durable claim and commit ordering."""
import base64
import hashlib
import socket
import ssl
import time
from .codes import is_canonical, normalise_code, CodeError
from .errors import NetError, StateError, ProtocolError, ConnectionLost
from .files import atomic_write, safe, remove
from .identity import ensure_identity
from .window import PairingWindow
from .spake2 import Spake2, password_scalar, wipe
from .p256 import PointError
from .tokens import read_token, read_kb_public, make_token, write_secret
from .transport import Transport, PAIRING_PORTS, port_number, bind_address
from . import store, wire

class AttemptError(NetError):
    def __init__(self, reason, wire_code=1, exit_code=4):
        super().__init__(reason,exit_code)
        self.wire_code = wire_code

def tls_context(server=False, identity=None):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    if not server:
        ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    if identity:
        ctx.load_cert_chain(str(safe(identity.cert_path)),str(safe(identity.key_path)))
    return ctx

def drain(sock, seconds=1.0, limit=65536):
    """After an error frame: half-close, then read and drop what the peer still sends until it closes (at most
    one second and 64 KiB). Closing with bytes still arriving resets the connection, and a reset can make the peer
    lose the error frame before reading it, so a refusal would show as 'connection' instead of its reason."""
    try:
        sock.shutdown(socket.SHUT_WR)
        end = time.monotonic()+seconds
        while limit > 0:
            left = end-time.monotonic()
            if left <= 0:
                return
            sock.settimeout(left)
            data = sock.recv(min(4096,limit))
            if not data:
                return
            limit -= len(data)
    except (OSError,ValueError):
        pass

class Channel:
    def __init__(self, sock, kind, window):
        self.sock,self.kind,self.window = sock,kind,window
        self.sent_confirm = False
        self.sent_types,self.received_types = [],[]

    def send(self, type, payload=b''):
        left = self.window.remaining_seconds()
        if left <= 0:
            raise TimeoutError()
        self.sock.settimeout(min(10,left))
        self.sock.sendall(wire.encode_frame(type,payload,handover_allowed=self.kind != 'relay'))
        self.sent_types.append(type)
        if type == 12:
            self.sent_confirm = True

    def error(self, code):
        try:
            self.sock.settimeout(0.25)
            self.sock.sendall(wire.encode_frame(11,code.to_bytes(4,'big')))
        except OSError:
            return
        drain(self.sock)

    def expect(self, type):
        left = self.window.remaining_seconds()
        if left <= 0:
            raise TimeoutError()
        timeout = left if self.window.claimed else min(10,left)
        f = wire.read_frame(self.sock,timeout,self.kind != 'relay',overall_timeout=timeout)
        self.received_types.append(f.type)
        if f.type == 11:
            code = int.from_bytes(f.payload,'big')
            if self.window.claimed:
                raise AttemptError('confirmation' if code == 13 and self.sent_confirm else 'protocol',1,5)
            reason = {14:'peer-locked',12:'incompatible-peer'}.get(code,'peer-refused')
            raise AttemptError(reason,1,3 if code == 14 else 4)
        if f.type != type:
            raise ProtocolError()
        return f.payload

def failure(exc, claimed):
    if isinstance(exc,AttemptError):
        return exc.reason,exc.wire_code,5 if claimed else exc.exit_code
    if isinstance(exc,ProtocolError):
        return 'protocol',exc.code,5 if claimed else 4
    if isinstance(exc,PointError):
        return 'protocol',2,5 if claimed else 4
    if isinstance(exc,TimeoutError):
        return 'timeout',6,5 if claimed else 4
    if isinstance(exc,(ConnectionLost,OSError)):
        return 'connection',1,5 if claimed else 4
    if isinstance(exc,(StateError,NetError)):
        return 'storage',13,5 if claimed else 6
    return 'internal',1,1

def _node(kind, side):
    return {'forge':('forge','workstation'),'gateway':('workstation','headend'),'relay':('headend','workstation')}[kind][0 if side == 'joined' else 1]

class JoinedSide:
    def __init__(self, kind, state, code, window=None, publish='serve-https',
                 transport='loopback', port=None, token_path=None,
                 kb_public_path=None, ready_file=None, openssl=None, model_endpoints=None):
        if kind not in ('forge','gateway') and not (kind == 'relay' and getattr(self,'test_only',False)):
            raise NetError('bad-request',2)
        if not is_canonical(code):
            raise NetError('bad-code',8,code_reason='not-canonical')
        if publish not in ('serve-https','tailnet-plain'):
            raise NetError('bad-request',2)
        try:
            if model_endpoints is not None and (kind != 'forge' or publish != 'serve-https'):
                raise ProtocolError()
            self.model_endpoints = wire.validate_model_endpoints(model_endpoints) if model_endpoints is not None else None
        except ProtocolError:
            raise NetError('bad-request',2) from None
        self.kind,self.state,self.publish = kind,state,publish
        self.transport = Transport(transport).mode
        self.port = port_number(PAIRING_PORTS[kind] if port is None else port,zero=True)
        self.ready_file = ready_file
        self.identity = ensure_identity(state,_node(kind,'joined'),openssl)
        self.window = window or PairingWindow(state,kind+'-joined')
        self.token,self.public = None,None
        if kind == 'forge':
            self.token,self.public = read_token(token_path),read_kb_public(kb_public_path)
        self.last_error,self.bound_port,self.channel = None,None,None
        self.on_ready = None
        self.code = bytearray(code,'ascii')

    def peer_pin(self, hello):
        return hashlib.sha256(wire.certificate_der(hello['cert'])).digest()

    def _connection(self, sock):
        channel = self.channel = Channel(sock,self.kind,self.window)
        pake,w = None,bytearray()
        persisted,token_written = False,False
        peer_id = None
        try:
            import secrets
            nonce = secrets.token_bytes(32)
            channel.send(1,wire.dumps_compact(wire.hello(self.kind,'A',self.identity.id,nonce.hex())))
            hello = wire.validate_hello(wire.loads_strict(channel.expect(1)),self.kind,'B')
            peer_id = hello['id']
            pin = self.peer_pin(hello)
            if pin.hex() == self.identity.pin or store.has_record(self.state,self.kind,'joined',peer_id):
                raise AttemptError('peer-refused',2)
            from .pairlock import remaining
            if remaining(self.window.state,int(self.window.clock.time())):
                raise AttemptError('peer-locked',14,3)
            if not self.window.can_claim():
                raise AttemptError('peer-refused',2)
            channel.send(2,wire.dumps_compact(wire.ack(self.kind)))
            wire.validate_ack(wire.loads_strict(channel.expect(2)),self.kind,'B')
            self.window.claim()
            token = make_token() if self.kind == 'gateway' else self.token
            w = bytearray(password_scalar(self.code,bytes.fromhex(self.identity.pin),pin,nonce,bytes.fromhex(hello['nonce'])))
            pake = Spake2('A',w)
            channel.send(14,pake.share)
            pB = channel.expect(14)
            pake.finish(bytes.fromhex(self.identity.pin),pin,pake.share,pB)
            channel.send(12,pake.own_mac)
            if not pake.verify(channel.expect(12)):
                raise AttemptError('password-or-identity',13,5)
            if self.kind != 'relay':
                channel.send(15,wire.dumps_compact(wire.handover(self.kind,token,self.public,self.publish,self.model_endpoints)))
            channel.send(13)
            channel.expect(13)
            given = ['forge-token','kb-public.pem'] if self.kind == 'forge' else ['mcp-token'] if self.kind == 'gateway' else []
            if self.model_endpoints is not None:
                given.append('model-endpoints.json')
            obj = store.record(self.kind,'joined',peer_id,hello['role'],pin.hex(),self.identity.pin,self.transport,int(self.window.clock.time()),publish=None if self.kind == 'relay' else self.publish,given=given)
            if self.kind == 'gateway':
                write_secret(safe(self.state)/'private/mcp-tokens'/peer_id,token)
                token_written = True
            store.save_record(self.state,obj)
            persisted = True
            self.window.succeed()
            return dict(kind=self.kind,peer_id=peer_id,peer_pin=pin.hex(),port=self.bound_port)
        except (NetError,ProtocolError,PointError,TimeoutError,ConnectionLost,OSError) as exc:
            reason,code,exit_code = failure(exc,self.window.claimed)
            channel.error(code)
            if self.window.claimed:
                self.last_error = reason
                if persisted:
                    store.rollback(self.state,self.kind,'joined',peer_id)
                if token_written:
                    remove(safe(self.state)/'private/mcp-tokens'/peer_id)
                self.window.fail(reason)
                raise NetError(reason,exit_code) from None
            if exit_code == 6:
                raise NetError(reason,6) from None
            return None
        finally:
            wipe(w)
            if pake:
                pake.erase()

    def serve(self):
        listener = None
        try:
            if self.window.fd is None:
                self.window.open()
            ctx = tls_context(True,self.identity)
            listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            try:
                listener.bind((bind_address(),self.port))
                listener.listen(4)
            except OSError:
                raise StateError('bind-failed') from None
            self.bound_port = listener.getsockname()[1]
            if self.ready_file:
                atomic_write(self.ready_file,wire.dumps_compact(dict(port=self.bound_port,pin=self.identity.pin))+b'\n')
            if self.on_ready:
                self.on_ready(self.bound_port)
            while self.window.remaining_seconds() > 0:
                listener.settimeout(min(0.25,self.window.remaining_seconds()))
                try:
                    raw,_ = listener.accept()
                except TimeoutError:
                    continue
                with raw:
                    raw.settimeout(min(10,self.window.remaining_seconds()))
                    try:
                        sock = ctx.wrap_socket(raw,server_side=True)
                    except (OSError,TimeoutError):
                        continue
                    with sock:
                        result = self._connection(sock)
                        if result is not None:
                            return result
            self.window.expire()
            raise NetError('expired',4)
        finally:
            wipe(self.code)
            if listener:
                listener.close()
            self.window.close()

class Joiner:
    def __init__(self, kind, state, code_text, transport='loopback', address=None,
                 port=None, expect_pin=None, peer_host_name=None, peer_dns_name=None,
                 hosts_file=None, openssl=None, window=None):
        try:
            self.code = bytearray(normalise_code(code_text))
        except CodeError as exc:
            raise NetError('bad-code',8,code_reason=exc.reason) from None
        try:
            if kind not in PAIRING_PORTS or expect_pin is not None and not wire.hex32(expect_pin):
                raise NetError('bad-request',2)
            self.kind,self.state = kind,state
            self.transport = Transport(transport).mode
            self.address,self.port = Transport(transport).peer_endpoint(kind,address=address,port=port)
            self.expect_pin,self.peer_host_name,self.peer_dns_name = expect_pin,peer_host_name,peer_dns_name
            self.hosts_file,self.openssl = hosts_file,openssl
            self.window = window
            self.channel = None
        except BaseException:
            wipe(self.code)
            raise

    def join(self):
        sock,channel,pake = None,None,None
        w = bytearray()
        persisted,received_saved,hosts_changed = False,False,False
        peer_id = None
        try:
            self.identity = ensure_identity(self.state,_node(self.kind,'joiner'),self.openssl)
            self.window = self.window or PairingWindow(self.state,self.kind+'-joiner')
            if self.window.fd is None:
                self.window.open()
            try:
                raw = socket.create_connection((self.address,self.port),timeout=5)
            except OSError:
                raise NetError('unreachable',4) from None
            try:
                raw.settimeout(10)
                sock = tls_context(False,self.identity).wrap_socket(raw,server_hostname=None)
                der = sock.getpeercert(binary_form=True)
                if not der:
                    raise ssl.SSLError()
            except OSError:
                raw.close()
                raise NetError('tls-failed',4) from None
            pin = hashlib.sha256(der).digest()
            if self.expect_pin and self.expect_pin != pin.hex():
                raise NetError('pin-mismatch',4)
            if self.identity.pin == pin.hex():
                raise NetError('same-identity',4)
            channel = self.channel = Channel(sock,self.kind,self.window)
            import secrets
            nonce = secrets.token_bytes(32)
            cert = base64.b64encode(self.identity.der()).decode('ascii') if self.kind != 'relay' else None
            channel.send(1,wire.dumps_compact(wire.hello(self.kind,'B',self.identity.id,nonce.hex(),cert)))
            hello = wire.validate_hello(wire.loads_strict(channel.expect(1)),self.kind,'A')
            peer_id = hello['id']
            if store.has_record(self.state,self.kind,'joiner',peer_id):
                raise AttemptError('already-paired',2,7)
            channel.send(2,wire.dumps_compact(wire.ack(self.kind)))
            wire.validate_ack(wire.loads_strict(channel.expect(2)),self.kind,'A')
            self.window.claim()
            w = bytearray(password_scalar(self.code,pin,bytes.fromhex(self.identity.pin),bytes.fromhex(hello['nonce']),nonce))
            pake = Spake2('B',w)
            pA = channel.expect(14)
            channel.send(14,pake.share)
            pake.finish(pin,bytes.fromhex(self.identity.pin),pA,pake.share)
            if not pake.verify(channel.expect(12)):
                raise AttemptError('password-or-identity',13,5)
            channel.send(12,pake.own_mac)
            handover = wire.validate_handover(wire.loads_strict(channel.expect(15)),self.kind) if self.kind != 'relay' else None
            channel.expect(13)
            names = store.save_received(self.state,self.kind,peer_id,handover) if handover else []
            received_saved = bool(names)
            obj = store.record(self.kind,'joiner',peer_id,hello['role'],pin.hex(),self.identity.pin,self.transport,int(self.window.clock.time()),peer_address=self.address,peer_port=self.port,peer_host_name=self.peer_host_name,peer_dns_name=self.peer_dns_name,publish=handover['publish'] if handover else None,received=names)
            store.save_record(self.state,obj)
            persisted = True
            if self.hosts_file and self.transport == 'tailnet':
                store.update_hosts_file(self.hosts_file,store.list_peers(self.state))
                hosts_changed = True
            channel.send(13)
            self.window.succeed()
            return dict(kind=self.kind,peer_id=peer_id,peer_pin=pin.hex(),received=names)
        except (NetError,ProtocolError,PointError,TimeoutError,ConnectionLost,OSError) as exc:
            claimed = self.window is not None and self.window.claimed
            if isinstance(exc,NetError) and not isinstance(exc,AttemptError) and not claimed:
                raise
            reason,code,exit_code = failure(exc,claimed)
            if channel:
                channel.error(code)
            if claimed:
                if persisted or received_saved:
                    store.rollback(self.state,self.kind,'joiner',peer_id,received_saved)
                if hosts_changed:
                    store.update_hosts_file(self.hosts_file,store.list_peers(self.state))
                self.window.fail(reason)
            raise NetError(reason,exit_code) from None
        finally:
            if sock:
                sock.close()
            if self.window:
                self.window.close()
            if pake:
                pake.erase()
            wipe(w,self.code)
