import hashlib
import os
import subprocess
from support import Case
from deep32net.identity import ensure_identity
from deep32net.errors import StateError

class TestIdentity(Case):
    def test_real_identity_second_openssl_der_hash_and_reuse(self):
        ident = self.identity('state','forge')
        self.assertTrue(ident.created)
        der = self.root/'second.der'
        subprocess.run([self.openssl,'x509','-in',str(ident.cert_path),'-outform','DER','-out',str(der)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=10)
        self.assertEqual(hashlib.sha256(der.read_bytes()).hexdigest(),ident.pin)
        again = ensure_identity(self.root/'state','forge',self.openssl)
        self.assertFalse(again.created)
        self.assertEqual((again.id,again.pin),(ident.id,ident.pin))
        with self.assertRaises(StateError) as caught:
            ensure_identity(self.root/'state','headend',self.openssl)
        self.assertEqual(caught.exception.reason,'identity-kind-mismatch')

    def test_key_without_cert_refused(self):
        ident = self.identity('state','forge')
        ident.cert_path.unlink()
        with self.assertRaises(StateError):
            ensure_identity(self.root/'state','forge',self.openssl)

    def test_missing_parent_refused(self):
        with self.assertRaises(StateError):
            ensure_identity(self.root/'missing/state','forge',self.openssl)

    def test_bad_identity_id_refused(self):
        ident = self.identity('state','forge')
        (ident.cert_path.parent/'id').write_text('wrong\n')
        with self.assertRaises(StateError):
            ensure_identity(self.root/'state','forge',self.openssl)
