"""R12 TLS provider and certificate possession/pin policy.

The old stdlib helpers are retained for historical regression fixtures only.
Production transport exclusively uses load_provider and pyOpenSSL's callback.
"""
import hashlib
import hmac
import importlib.metadata
import json
from pathlib import Path
import ssl
import time
from .pairing import PairingWindow, PinRecord
from .wire import ProtocolError, Error


BOOTSTRAP_BLOCK = "pairing-bootstrap-unavailable"


class TLSUnavailable(ValueError):
    pass


def certificate_pin(der):
    if not isinstance(der, bytes) or not der:
        raise ProtocolError(Error.AUTH)
    return hashlib.sha256(der).digest()


def pem_der(pem):
    try:
        if isinstance(pem, bytes):
            pem = pem.decode("ascii")
        # Do not allow additional anchors/keys in the trust input.
        if pem.count("-----BEGIN CERTIFICATE-----") != 1 or pem.count("-----END CERTIFICATE-----") != 1:
            raise ValueError()
        prefix, rest = pem.split("-----BEGIN CERTIFICATE-----")
        _, suffix = rest.split("-----END CERTIFICATE-----")
        if prefix.strip() or suffix.strip():
            raise ValueError()
        return ssl.PEM_cert_to_DER_cert(pem)
    except (ValueError, UnicodeError):
        raise ProtocolError(Error.AUTH) from None


def pinned_context(certfile, keyfile, peer_certfile, expected_pin, *, server=True):
    """Load only the already paired peer anchor; never system trust stores.

    Owner/ACL-checked paths must be supplied by the private-state owner. R10
    intentionally retains OpenSSL chain/time checks, unlike asklink's client.
    """
    if peer_certfile is None or expected_pin is None:
        raise TLSUnavailable(BOOTSTRAP_BLOCK)
    if isinstance(expected_pin, str):
        try:
            expected_pin = bytes.fromhex(expected_pin)
        except ValueError:
            raise ProtocolError(Error.AUTH) from None
    if len(expected_pin) != 32:
        raise ProtocolError(Error.AUTH)
    peer_pem = Path(peer_certfile).read_bytes()
    if not hmac.compare_digest(certificate_pin(pem_der(peer_pem)), expected_pin):
        raise ProtocolError(Error.AUTH)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(cafile=str(peer_certfile))
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    # No ticket/session resumption may bypass this connection's certificate.
    context.options |= ssl.OP_NO_TICKET
    if server:
        context.num_tickets = 0
    return context


def verify_peer(connection, expected_pin):
    if isinstance(expected_pin, str):
        try:
            expected_pin = bytes.fromhex(expected_pin)
        except ValueError:
            raise ProtocolError(Error.AUTH) from None
    der = connection.getpeercert(binary_form=True)
    if not isinstance(expected_pin, bytes) or len(expected_pin) != 32 or not hmac.compare_digest(certificate_pin(der), expected_pin):
        raise ProtocolError(Error.AUTH)
    if connection.version() != "TLSv1.3":
        raise ProtocolError(Error.AUTH)
    return der


def accept_pinned(context, socket, expected_pin, timeout=10):
    socket.settimeout(timeout)
    connection = context.wrap_socket(socket, server_side=True)
    try:
        verify_peer(connection, expected_pin)
    except BaseException:
        connection.close()
        raise
    return connection


PROVIDER_LOCK = Path(__file__).resolve().parents[1] / "tls-provider-lock.json"
PROVIDER_BLOCK = "tls-provider-unqualified"


def provider_readiness(lockfile=PROVIDER_LOCK):
    """R15's exact approved hashed lock, plus the executing venv's versions."""
    from .provider import readiness
    return readiness(lockfile)


def load_provider():
    if not provider_readiness():
        raise TLSUnavailable(PROVIDER_BLOCK)
    try:
        from OpenSSL import SSL, crypto
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        return OpenSSLProvider(SSL, crypto, (x509, hashes, serialization, ec))
    except (ImportError, AttributeError):
        raise TLSUnavailable(PROVIDER_BLOCK) from None


class PeerPolicy:
    """One accepted transport attempt, including an unknown-peer reservation."""
    def __init__(self, peer=None, window=None, *, clock=time.monotonic):
        if peer is not None and not isinstance(peer, PinRecord):
            raise ProtocolError(Error.AUTH)
        if window is not None and not isinstance(window, PairingWindow):
            raise ProtocolError(Error.AUTH)
        self.peer, self.window, self.clock = peer, window, clock
        self.reserved = False
        # Set when the stored pin refused a different certificate.
        self.other_peer = False

    def begin_attempt(self):
        if self.reserved:
            raise ProtocolError(Error.AUTH)
        if self.peer is None:
            if self.window is None:
                raise ProtocolError(Error.AUTH)
            self.window.consume(self.clock())
        self.reserved = True

    def accept_der(self, der):
        if not self.reserved:
            return False
        try:
            pin = certificate_pin(der)
        except ProtocolError:
            return False
        if self.peer is not None:
            # An armed window cannot replace a stored certificate pin.
            if hmac.compare_digest(pin, self.peer.pin):
                return True
            self.other_peer = True
            return False
        return self.window is not None and self.window.consumed and self.clock() < self.window.deadline


class OpenSSLProvider:
    """Dependency-injected backend; production obtains it through load_provider.

    No trust stores are loaded. OpenSSL proves possession; the callback uses
    only the full leaf DER pin or an explicitly reserved console window.
    """
    def __init__(self, ssl_module, crypto_module, identity_modules=None):
        self.SSL, self.crypto = ssl_module, crypto_module
        self.identity_modules = identity_modules

    def context(self, identity, policy):
        SSL, crypto = self.SSL, self.crypto
        context = SSL.Context(SSL.TLS_SERVER_METHOD)
        context.set_min_proto_version(SSL.TLS1_3_VERSION)
        context.set_max_proto_version(SSL.TLS1_3_VERSION)
        context.set_options(SSL.OP_NO_TICKET)
        context.set_session_cache_mode(SSL.SESS_CACHE_OFF)
        context.use_certificate(crypto.load_certificate(crypto.FILETYPE_PEM, identity.certificate_pem))
        context.use_privatekey(crypto.load_privatekey(crypto.FILETYPE_PEM, identity.private_key_pem))
        context.check_privatekey()

        def verify(connection, certificate, error_number, depth, preverified):
            # Chain, hostname and time checks are deliberately irrelevant.
            # The complete leaf is always rechecked after the handshake too.
            if depth != 0:
                return policy.reserved and (policy.peer is not None or
                    (policy.window is not None and policy.clock() < policy.window.deadline))
            try:
                return policy.accept_der(crypto.dump_certificate(crypto.FILETYPE_ASN1, certificate))
            except Exception:
                return False

        context.set_verify(SSL.VERIFY_PEER | SSL.VERIFY_FAIL_IF_NO_PEER_CERT, verify)
        return context

    def connection(self, context, raw_socket):
        connection = self.SSL.Connection(context, raw_socket)
        connection.set_accept_state()
        return connection

    def peer_der(self, connection):
        certificate = connection.get_peer_certificate()
        if certificate is None or connection.get_protocol_version_name() != "TLSv1.3":
            raise ProtocolError(Error.AUTH)
        return self.crypto.dump_certificate(self.crypto.FILETYPE_ASN1, certificate)

    def wants(self):
        return (self.SSL.WantReadError, self.SSL.WantWriteError)

    def validate_identity(self, certificate_pem, private_key_pem):
        certificate = self.crypto.load_certificate(self.crypto.FILETYPE_PEM, certificate_pem)
        key = self.crypto.load_privatekey(self.crypto.FILETYPE_PEM, private_key_pem)
        context = self.SSL.Context(self.SSL.TLS_SERVER_METHOD)
        context.use_certificate(certificate)
        context.use_privatekey(key)
        context.check_privatekey()
        return self.crypto.dump_certificate(self.crypto.FILETYPE_ASN1, certificate)

    def generate_identity(self):
        if self.identity_modules is None:
            raise TLSUnavailable(PROVIDER_BLOCK)
        from datetime import datetime, timedelta, timezone
        x509, hashes, serialization, ec = self.identity_modules
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(timezone.utc)
        name = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "Deep32 relay D32U")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                    key_encipherment=False, data_encipherment=False, key_agreement=False,
                    key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                .sign(key, hashes.SHA256()))
        return (cert.public_bytes(serialization.Encoding.PEM),
                key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
