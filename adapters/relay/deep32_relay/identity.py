"""Protected relay identity and asklink-compatible durable peer pins."""
from dataclasses import dataclass, field
import ctypes
import hmac
import os
import secrets
from .messages import identifier
from .pairing import PinRecord
from .state import StateError
from .tls import certificate_pin


@dataclass(frozen=True)
class Identity:
    local_id: str
    certificate_pem: bytes = field(repr=False)
    private_key_pem: bytes = field(repr=False)
    pin: bytes = field(repr=False)


class WindowsKeyProtector:
    """Current-user DPAPI with UI forbidden; ACL ownership remains mandatory."""
    prefix = b"D32K-DPAPI-1\x00"

    def __init__(self):
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]
        self.Blob = Blob
        self.crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        self.crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p

    def _apply(self, value, protect):
        buffer = ctypes.create_string_buffer(value)
        incoming = self.Blob(len(value), ctypes.cast(buffer, ctypes.c_void_p))
        outgoing = self.Blob()
        function = self.crypt.CryptProtectData if protect else self.crypt.CryptUnprotectData
        try:
            if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
                raise StateError("identity-protection-failed")
            if not outgoing.data or not 0 < outgoing.size <= 65536:
                raise StateError("identity-protection-failed")
            return ctypes.string_at(outgoing.data, outgoing.size)
        finally:
            ctypes.memset(buffer, 0, len(buffer))
            if outgoing.data:
                ctypes.memset(outgoing.data, 0, outgoing.size)
                self.kernel.LocalFree(outgoing.data)

    def protect(self, value):
        return self.prefix + self._apply(value, True)

    def unprotect(self, value):
        if not value.startswith(self.prefix):
            raise StateError("identity-protection-required")
        return self._apply(value[len(self.prefix):], False)


class IdentityStore:
    """Uses only the install owner's checked, atomic private-state operations.

    A partial generation is retained and refused for explicit operator recovery;
    neither restart nor missing metadata silently rotates an enrolled identity.
    """
    def __init__(self, owner, key_protector=None):
        self.owner = owner
        self.prefix = "private/link/"
        self.key_protector = key_protector or (WindowsKeyProtector() if os.name == "nt" else None)

    def load_or_create(self, provider):
        self.owner.validate()
        self.owner.mkdir("private/link")
        paths = [self.prefix + item for item in ("node.key", "node.crt", "node.id")]
        exists = [self.owner.path(path).exists() for path in paths]
        if any(exists) and not all(exists):
            raise StateError("identity-incomplete")
        if not any(exists):
            if self.owner.path(self.prefix + "peer.pin").exists():
                raise StateError("identity-missing-with-peer")
            certificate, private_key = provider.generate_identity()
            der = provider.validate_identity(certificate, private_key)
            if not certificate_pin(der):
                raise StateError("identity-invalid")
            local_id = "relay-" + secrets.token_hex(16)
            # A completed identity is distinguished only by the last id write.
            stored_key = self.key_protector.protect(private_key) if self.key_protector else private_key
            self.owner.write(paths[0], stored_key)
            self.owner.write(paths[1], certificate)
            self.owner.write(paths[2], (local_id + "\n").encode("ascii"))
        try:
            local_id = self.owner.read(paths[2], 128).decode("ascii")
            if not local_id.endswith("\n") or not identifier(local_id[:-1]):
                raise StateError("identity-invalid")
            certificate = self.owner.read(paths[1], 65536)
            private_key = self.owner.read(paths[0], 65536)
            if self.key_protector is not None:
                private_key = self.key_protector.unprotect(private_key)
            der = provider.validate_identity(certificate, private_key)
            return Identity(local_id[:-1], certificate, private_key, certificate_pin(der))
        except (ValueError, UnicodeError):
            raise StateError("identity-invalid") from None

    def load_peer(self):
        path = self.owner.path(self.prefix + "peer.pin")
        if not path.exists():
            return None
        return PinRecord.decode(self.owner.read(self.prefix + "peer.pin", 256))

    def save_peer(self, record):
        if not isinstance(record, PinRecord):
            raise StateError("peer-invalid")
        existing = self.load_peer()
        if existing is not None and (existing.peer_id != record.peer_id or
                                     not hmac.compare_digest(existing.pin, record.pin)):
            raise StateError("peer-revocation-required")
        self.owner.write(self.prefix + "peer.pin", record.encode())

    def revoke_peer(self):
        # Only explicit local administration calls this; loss and commit failure
        # never remove a pin. Existing private state remains owner checked.
        path = self.owner.path(self.prefix + "peer.pin")
        if path.exists():
            self.owner.read(self.prefix + "peer.pin", 256)
            path.unlink()
