"""Bounded health probes; credentials stay in protected mounted files."""
import http.client
import json
from pathlib import Path
import re
import sys


def get(host, port, path, token_path=None):
    headers = {}
    if token_path:
        token = Path(token_path).read_text(encoding='ascii').strip()
        if not re.fullmatch(r'[0-9a-f]{64}', token):
            raise ValueError('invalid protected credential')
        headers['Authorization'] = 'Bearer ' + token
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        body = response.read(1048577)
        if response.status != 200 or len(body) > 1048576:
            raise ValueError('service is not ready')
        return body
    finally:
        connection.close()


def check(role):
    if role == 'qdrant':
        get('qdrant', 6333, '/readyz')
    elif role == 'door':
        for port in (8081, 8082):
            get('127.0.0.1', port, '/health', '/run/secrets/forge-token')
    elif role == 'engine':
        payload = json.loads(get('127.0.0.1', 8765, '/health', '/run/secrets/core-token'))
        if payload.get('status') not in ('ok', 'healthy') and payload.get('ok') is not True:
            raise ValueError('engine is not ready')
    else:
        raise ValueError('unsupported health probe')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 1:
            raise ValueError('health role required')
        check(argv[0])
        return 0
    except (OSError, ValueError, http.client.HTTPException):
        # Docker retains health output: no exception text or credential may leak.
        print('The service is not ready; check full-role status and restore its required inputs.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
