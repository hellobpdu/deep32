"""One authenticated door for both Forge model routes. No request logging."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
from pathlib import Path
import threading
import re
from datetime import datetime, timezone

MAX_BODY = 4 * 1024 * 1024
LIMIT = threading.BoundedSemaphore(16)


def authorised(header, token):
    return hmac.compare_digest(header or '', 'Bearer ' + token)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body=b'', content_type=None):
        self.send_response(code)
        if content_type:
            self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        # Origin clock only: never forward a backend/proxy time or nonce header.
        if self.path == '/info':
            nonce = self.headers.get_all('X-Deep32-Nonce', [])
            if len(nonce) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{16,64}', nonce[0]):
                self.send_header('X-Deep32-Time', datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'))
                self.send_header('X-Deep32-Nonce', nonce[0])
                self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def forward(self):
        self.connection.settimeout(30)
        if not authorised(self.headers.get('Authorization'), self.server.token):
            self.reply(401)
            return
        port = self.server.server_port
        allowed = {8081: {'/v1/embeddings', '/health', '/info'}, 8082: {'/v1/rerank', '/health', '/info'}}[port]
        if self.path not in allowed or self.headers.get('Transfer-Encoding'):
            self.reply(400)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.reply(400)
            return
        if not 0 <= size <= MAX_BODY:
            self.reply(413)
            return
        if not LIMIT.acquire(blocking=False):
            self.reply(503)
            return
        target = http.client.HTTPConnection('embeddings' if port == 8081 else 'reranker', 80 if port == 8081 else 8082, timeout=300)
        try:
            data = self.rfile.read(size)
            if len(data) != size:
                self.reply(400)
                return
            target.request(self.command, self.path, body=data, headers={'Content-Type': 'application/json'})
            result = target.getresponse()
            body = result.read(MAX_BODY + 1)
            if len(body) <= MAX_BODY:
                # Keep the model server's media type (JSON) for clients that check it.
                self.reply(result.status, body, result.getheader('Content-Type'))
            else:
                self.reply(502)
        except (OSError, http.client.HTTPException):
            self.reply(503)
        finally:
            target.close()
            LIMIT.release()

    do_GET = forward
    do_POST = forward


def main():
    token = Path('/run/secrets/forge-token').read_text(encoding='ascii').strip()
    import re
    if not re.fullmatch('[0-9a-f]{64}', token):
        raise ValueError('invalid per-install token')
    servers = []
    for port in (8081, 8082):
        server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
        server.token = token
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Event().wait()


if __name__ == '__main__':
    main()
