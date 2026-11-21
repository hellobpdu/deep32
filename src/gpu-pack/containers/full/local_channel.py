"""The existing C8 ceremony over a protected, process-local duplex channel.

No listener or network socket exists. Both endpoints and the fresh one-use code
are owned by one enrolment process. The shared pairing state machine still
checks SPAKE2 confirmations, certificate pins, frame ordering and durable locks.
"""
from contextlib import contextmanager
import queue
import threading


class Endpoint:
    def __init__(self, incoming, outgoing, peer_der):
        self.incoming, self.outgoing, self.peer_der = incoming, outgoing, peer_der
        self.timeout, self.buffer, self.closed = 10, bytearray(), False

    def settimeout(self, seconds):
        self.timeout = seconds

    def sendall(self, data):
        if self.closed:
            raise OSError('closed local channel')
        self.outgoing.put(bytes(data), timeout=self.timeout)

    def recv(self, size):
        if not self.buffer:
            try:
                data = self.incoming.get(timeout=self.timeout)
            except queue.Empty:
                raise TimeoutError() from None
            if data is None:
                return b''
            self.buffer.extend(data)
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def getpeercert(self, binary_form=False):
        if not binary_form:
            raise ValueError('local channel requires binary certificate pins')
        return self.peer_der

    def close(self):
        if not self.closed:
            self.closed = True
            self.outgoing.put(None)


def _pair_local(kind, joined_state, joiner_state, token_path=None, kb_public_path=None,
               identity_factory=None, emit=print, joiner_code=None):
    """Return the shared core's committed join result, with no credential values.

    identity_factory and joiner_code are deterministic fixture seams. Normal
    callers use fresh OpenSSL identities and the same fresh code at both sides.
    """
    from deep32net import pairing, store
    from deep32net.codes import new_code
    from deep32net.identity import ensure_identity
    from deep32net.window import PairingWindow
    identity_factory = identity_factory or ensure_identity
    provider_kind = 'forge' if kind == 'forge' else 'workstation'
    consumer_kind = 'workstation' if kind == 'forge' else 'headend'
    emit('Waiting for private C8 identity verification; keep OpenSSL available and retain this installation state.')
    provider = identity_factory(joined_state, provider_kind)
    consumer = identity_factory(joiner_state, consumer_kind)
    left_records = store.list_peers(joined_state)
    right_records = store.list_peers(joiner_state)
    left = [r for r in left_records if r['kind'] == kind and r['side'] == 'joined' and r['peer_id'] == consumer.id]
    right = [r for r in right_records if r['kind'] == kind and r['side'] == 'joiner' and r['peer_id'] == provider.id]
    if left or right:
        if (len(left) != 1 or len(right) != 1 or left[0]['peer_pin'] != consumer.pin
                or right[0]['peer_pin'] != provider.pin or left[0]['own_pin'] != provider.pin
                or right[0]['own_pin'] != consumer.pin):
            raise ValueError('Restore both sides of the local enrolment before restarting.')
        return {'kind': kind, 'peer_id': provider.id, 'peer_pin': provider.pin,
                'received': right[0]['received']}
    code = new_code()
    # Constructors use the exact identity factory; _connection/join below are
    # inherited without replacing any protocol or storage operation.
    class LocalJoined(pairing.JoinedSide):
        pass
    class LocalJoiner(pairing.Joiner):
        def _connect(self):
            return consumer_endpoint, provider.der()
    # Constructor normally reads identity via ensure_identity. Pre-create its
    # files with the factory; fixtures replace this one dependency explicitly.
    original = pairing.ensure_identity
    if identity_factory is not ensure_identity:
        pairing.ensure_identity = identity_factory
    server = client = None
    outcome = {}
    try:
        server = LocalJoined(kind, joined_state, code, token_path=token_path,
                             kb_public_path=kb_public_path)
        client = LocalJoiner(kind, joiner_state, code if joiner_code is None else joiner_code,
                             expect_pin=provider.pin)
        # No TLS listener is involved: the certificate is pinned at the private
        # process boundary, then bound into the unchanged C8 confirmation MACs.
        a, b = queue.Queue(), queue.Queue()
        provider_endpoint = Endpoint(a, b, consumer.der())
        consumer_endpoint = Endpoint(b, a, provider.der())
        server.window.open()
        def serve():
            try:
                outcome['result'] = server._connection(provider_endpoint)
            except BaseException as exc:
                outcome['error'] = exc
            finally:
                provider_endpoint.close()
                server.window.close()
        emit('Waiting for authenticated local C8 enrolment; keep this installation running until the private handoff finishes.')
        thread = threading.Thread(target=serve, name='deep32-local-enrolment', daemon=True)
        thread.start()
        try:
            result = client.join()
        finally:
            consumer_endpoint.close()
            thread.join(timeout=12)
        if thread.is_alive() or 'error' in outcome or not outcome.get('result'):
            raise ValueError('The local enrolment did not commit on both sides; restore its private state and retry.')
        return result
    finally:
        pairing.ensure_identity = original
        if server:
            from deep32net.spake2 import wipe
            wipe(server.code)
            server.window.close()


@contextmanager
def waiting(emit, reason):
    """A bounded core call may wait; keep its corrective reason visible."""
    stop = threading.Event()
    message = 'Waiting for ' + reason + '; keep OpenSSL available and retain this installation state.'
    emit(message)
    def heartbeat():
        while not stop.wait(10):
            emit(message)
    ticker = threading.Thread(target=heartbeat, name='deep32-enrolment-status', daemon=True)
    ticker.start()
    try:
        yield
    finally:
        stop.set()
        ticker.join(timeout=1)


def pair_local(kind, joined_state, joiner_state, token_path=None, kb_public_path=None,
               identity_factory=None, emit=print, joiner_code=None):
    with waiting(emit, 'authenticated local C8 enrolment'):
        return _pair_local(kind, joined_state, joiner_state, token_path, kb_public_path,
                           identity_factory, emit, joiner_code)
