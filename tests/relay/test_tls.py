"""Actual SSL memory-BIO handshakes, entirely offline with synthetic keys."""
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "adapters" / "relay"))
from deep32_relay.tls import (BOOTSTRAP_BLOCK, TLSUnavailable, certificate_pin,
                               pem_der, pinned_context, verify_peer)
from deep32_relay.wire import ProtocolError


def handshake(server_context, client_context):
    si, so, ci, co = (ssl.MemoryBIO() for _ in range(4))
    server = server_context.wrap_bio(si, so, server_side=True)
    client = client_context.wrap_bio(ci, co, server_side=False)
    done = [False, False]
    for _ in range(100):
        for index, connection in enumerate((server, client)):
            if not done[index]:
                try:
                    connection.do_handshake()
                    done[index] = True
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                    pass
        if so.pending:
            ci.write(so.read())
        if co.pending:
            si.write(co.read())
        if all(done):
            return server, client
    raise AssertionError("synthetic-tls-handshake-incomplete")


class PureTLSTests(unittest.TestCase):
    def test_first_contact_has_no_automatic_trust(self):
        with self.assertRaises(TLSUnavailable) as cm:
            pinned_context(None, None, None, None)
        self.assertEqual(str(cm.exception), BOOTSTRAP_BLOCK)

    def test_empty_or_pem_bundle_rejected(self):
        for pem in ("", "not a certificate", "-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\nextra"):
            with self.assertRaises(ProtocolError):
                pem_der(pem)
        with self.assertRaises(ProtocolError):
            certificate_pin(b"")


class MemoryBIOTLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.environ.get("DEEP32_RELAY_TEST_ROOT")
        openssl = os.environ.get("DEEP32_TEST_OPENSSL") or shutil.which("openssl")
        if not root or not openssl:
            raise unittest.SkipTest("synthetic-tls-fixtures-unavailable")
        root_path = Path(root).resolve()
        clone = Path(__file__).resolve().parents[2]
        if not root_path.is_absolute() or root_path == clone or clone in root_path.parents:
            raise AssertionError("synthetic-test-root-outside-clone-required")
        root_path.mkdir(parents=True, exist_ok=True)
        cls.scratch = tempfile.TemporaryDirectory(prefix="synthetic-tls-", dir=root_path)
        cls.folder = Path(cls.scratch.name)
        config = cls.folder / "synthetic-openssl.cnf"
        config.write_text("[req]\ndistinguished_name=dn\n[dn]\n", encoding="ascii")
        cls.certs = []
        for index, name in enumerate(("relay", "node", "foreign"), 1):
            cert, key = cls.folder / ("synthetic-" + name + ".crt"), cls.folder / ("synthetic-" + name + ".key")
            result = subprocess.run([openssl, "req", "-new", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                                     "-nodes", "-sha256", "-days", "1", "-set_serial", str(index), "-subj", "/CN=Synthetic " + name,
                                     "-addext", "keyUsage=critical,digitalSignature", "-config", str(config), "-out", str(cert), "-keyout", str(key)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
            if result.returncode:
                raise AssertionError("synthetic-certificate-generation-failed")
            cls.certs.append((cert, key, certificate_pin(pem_der(cert.read_bytes()))))

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def contexts(self):
        relay, node, _ = self.certs
        server = pinned_context(relay[0], relay[1], node[0], node[2])
        client = pinned_context(node[0], node[1], relay[0], relay[2], server=False)
        return server, client

    def test_actual_pinned_mutual_tls_non_ca_leaf(self):
        server_context, client_context = self.contexts()
        server, client = handshake(server_context, client_context)
        self.assertEqual(server.version(), "TLSv1.3")
        self.assertEqual(client.version(), "TLSv1.3")
        self.assertEqual(verify_peer(server, self.certs[1][2]), pem_der(self.certs[1][0].read_bytes()))
        self.assertEqual(verify_peer(client, self.certs[0][2]), pem_der(self.certs[0][0].read_bytes()))
        self.assertEqual(server_context.cert_store_stats()["x509"], 1)
        self.assertEqual(client_context.cert_store_stats()["x509"], 1)

    def test_cert_required_tls13_only(self):
        for context in self.contexts():
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertFalse(context.check_hostname)
            self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_3)
            self.assertEqual(context.maximum_version, ssl.TLSVersion.TLSv1_3)

    def test_wrong_pin_before_and_after_handshake(self):
        relay, node, _ = self.certs
        with self.assertRaises(ProtocolError):
            pinned_context(relay[0], relay[1], node[0], bytes(32))
        server, client = handshake(*self.contexts())
        with self.assertRaises(ProtocolError):
            verify_peer(server, bytes(32))

    def test_foreign_client_cannot_authenticate(self):
        server, _ = self.contexts()
        relay, _, foreign = self.certs
        client = pinned_context(foreign[0], foreign[1], relay[0], relay[2], server=False)
        with self.assertRaises(ssl.SSLError):
            handshake(server, client)

    def test_missing_client_certificate_fails(self):
        server, _ = self.contexts()
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.check_hostname = False
        client.verify_mode = ssl.CERT_REQUIRED
        client.minimum_version = client.maximum_version = ssl.TLSVersion.TLSv1_3
        client.load_verify_locations(cafile=str(self.certs[0][0]))
        with self.assertRaises(ssl.SSLError):
            handshake(server, client)

    def test_tls12_not_accepted(self):
        server, client = self.contexts()
        client.minimum_version = client.maximum_version = ssl.TLSVersion.TLSv1_2
        with self.assertRaises(ssl.SSLError):
            handshake(server, client)


if __name__ == "__main__":
    unittest.main()
