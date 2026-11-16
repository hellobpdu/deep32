"""TLS probe for the Deep32 interpreters (LibreSSL): does what deep32-engine, deep32-gateway and the GPU forwarder do with TLS work?

Usage: python -I -B tls_probe.py WORKDIR
Makes a throwaway CA, a server certificate for 127.0.0.1 and a client certificate with base openssl(1) (LibreSSL),
serves HTTPS on 127.0.0.1 from a thread (stdlib ssl, TLS 1.2 and 1.3 floors) and checks, from this interpreter:
  1. stdlib ssl client, server cert verified against the CA, hostname 127.0.0.1 (the forwarder's shape);
  2. the same with a client certificate the server requires (mTLS: the forwarder to the hub);
  3. httpx (if importable: deep32-engine's and deep32-gateway's HTTP client) with verify=<CA>, two requests on one keep-alive
     connection;
  4. a wrong CA is refused (verification really runs);
  5. certifi's bundle loads (httpx's default verify) and ssl.create_default_context() loads /etc/ssl/cert.pem.
Prints one line per check and the negotiated protocol/cipher; exit 1 if any check fails.
"""
import http.server
import os
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request

work = sys.argv[1]
os.makedirs(work, exist_ok=True)


def sh(*args):
    subprocess.run(args, check=True, capture_output=True, cwd=work)


def make_pki():
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=d32 test CA",
       "-keyout", "ca.key", "-out", "ca.pem")
    for name, ext in (("server", "subjectAltName=IP:127.0.0.1"), ("client", "extendedKeyUsage=clientAuth")):
        sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN=d32 {name}",
           "-keyout", f"{name}.key", "-out", f"{name}.csr")
        with open(os.path.join(work, f"{name}.ext"), "w") as f:
            f.write(ext + "\n")
        sh("openssl", "x509", "-req", "-in", f"{name}.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
           "-days", "2", "-extfile", f"{name}.ext", "-out", f"{name}.pem")
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=other CA",
       "-keyout", "other.key", "-out", "other.pem")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        peer = self.connection.getpeercert()
        body = f"ok {self.connection.version()} {self.connection.cipher()[0]} client={bool(peer)}\n".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve(require_client: bool, minimum: ssl.TLSVersion):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = minimum
    ctx.load_cert_chain(os.path.join(work, "server.pem"), os.path.join(work, "server.key"))
    if require_client:
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(os.path.join(work, "ca.pem"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"https://127.0.0.1:{srv.server_address[1]}/"


def client_ctx(ca="ca.pem", cert=False):
    ctx = ssl.create_default_context(cafile=os.path.join(work, ca))
    if cert:
        ctx.load_cert_chain(os.path.join(work, "client.pem"), os.path.join(work, "client.key"))
    return ctx


results = []


def check(name, fn):
    try:
        detail = fn()
        results.append(True)
        print(f"PASS {name}: {detail}")
    except Exception as exc:  # noqa: BLE001 - reported
        results.append(False)
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")


print(f"python {sys.version.split()[0]}, {ssl.OPENSSL_VERSION}, HAS_TLSv1_3={ssl.HAS_TLSv1_3}")
make_pki()
for minimum in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
    srv, url = serve(False, minimum)
    check(f"stdlib client, floor {minimum.name}",
          lambda: urllib.request.urlopen(url, context=client_ctx(), timeout=10).read().decode().strip())
    def refused(url=url):
        try:
            urllib.request.urlopen(url, context=client_ctx("other.pem"), timeout=10)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                return f"refused: {exc.reason.verify_message}"
            raise
        raise AssertionError("a certificate from an unknown CA was accepted")
    check(f"wrong CA refused, floor {minimum.name}", refused)
    try:
        import httpx
        def via_httpx():
            with httpx.Client(verify=client_ctx(), timeout=10) as c:
                a, b = c.get(url), c.get(url)
                return f"{a.text.strip()} / second request {b.status_code}, httpx {httpx.__version__}"
        check(f"httpx client, floor {minimum.name}", via_httpx)
    except ImportError:
        print("SKIP httpx not importable here")
    srv.shutdown()
srv, url = serve(True, ssl.TLSVersion.TLSv1_3)
check("mTLS (client certificate required), TLS 1.3",
      lambda: urllib.request.urlopen(url, context=client_ctx(cert=True), timeout=10).read().decode().strip())
srv.shutdown()
try:
    import certifi
    check("certifi bundle loads", lambda: f"{certifi.where()}: "
          f"{ssl.create_default_context(cafile=certifi.where()).cert_store_stats()}")
except ImportError:
    print("SKIP certifi not importable here")
check("default context loads the system store", lambda: f"{ssl.get_default_verify_paths().cafile}: "
      f"{ssl.create_default_context().cert_store_stats()}")
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
