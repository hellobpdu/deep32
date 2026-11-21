"""Fresh local P-256 certificate identities through openssl."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
from .errors import StateError
from .files import safe, state_dir, private_dir, read_bytes, atomic_write, sync_dir, private_write

CONFIG = '[req]\ndistinguished_name = dn\nprompt = no\nx509_extensions = d32\n[dn]\nCN = Deep32 D32U\n[d32]\nkeyUsage = critical, digitalSignature\n'

@dataclass(frozen=True)
class Identity:
    id: str
    pin: str
    cert_path: Path
    key_path: Path
    created: bool

    def der(self):
        try:
            return ssl.PEM_cert_to_DER_cert(read_bytes(self.cert_path).decode('ascii'))
        except (ValueError,UnicodeError):
            raise StateError() from None

def openssl_path(requested=None):
    value = requested or os.environ.get('DEEP32_OPENSSL') or shutil.which('openssl')
    if not value:
        raise StateError('openssl-missing')
    return str(value)

@private_write
def ensure_identity(state, node_kind, openssl=None):
    if node_kind not in ('forge','workstation','headend'):
        raise StateError()
    root = state_dir(state)
    directory = safe(root/'identity')
    created = not directory.exists()
    if created:
        executable = openssl_path(openssl)
        temporary = private_dir(root/('identity.new-'+secrets.token_hex(8)))
        env = os.environ.copy()
        env.update(RANDFILE=str(temporary/'.rnd'))
        try:
            atomic_write(temporary/'d32.cnf',CONFIG.encode('ascii'))
            for args in ([executable,'ecparam','-name','prime256v1','-genkey','-noout','-out','key.pem'],[executable,'req','-new','-x509','-key','key.pem','-config','d32.cnf','-days','3650','-sha256','-out','cert.pem']):
                subprocess.run(args,cwd=temporary,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=60)
            for name in ('key.pem','cert.pem'):
                os.chmod(safe(temporary/name),0o600)
            atomic_write(temporary/'id',(node_kind+'-'+secrets.token_hex(6)+'\n').encode('ascii'))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(safe(temporary/'cert.pem')),str(safe(temporary/'key.pem')))
            (temporary/'d32.cnf').unlink()
            (temporary/'.rnd').unlink(missing_ok=True)
            safe(directory)
            temporary.rename(directory)
            sync_dir(root)
        except (OSError,subprocess.SubprocessError,ssl.SSLError):
            raise StateError() from None
        finally:
            if temporary.exists():
                for p in temporary.iterdir():
                    safe(p).unlink()
                temporary.rmdir()
    try:
        raw_id = read_bytes(directory/'id').decode('ascii')
        match = re.fullmatch(r'(forge|workstation|headend)-[0-9a-f]{12}\n?',raw_id)
        if not match:
            raise StateError()
        if match[1] != node_kind:
            raise StateError('identity-kind-mismatch')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(safe(directory/'cert.pem')),str(safe(directory/'key.pem')))
        der = ssl.PEM_cert_to_DER_cert(read_bytes(directory/'cert.pem').decode('ascii'))
        return Identity(raw_id.rstrip('\n'),hashlib.sha256(der).hexdigest(),directory/'cert.pem',directory/'key.pem',created)
    except (OSError,ValueError,UnicodeError,ssl.SSLError):
        raise StateError() from None
