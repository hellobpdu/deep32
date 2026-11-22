"""Explicit, resumable publication of the existing Workstation gateway.

Only the two journalled Serve routes belong to this controller; tailnet settings
are read only. The OpenBSD Serve command/JSON ABI requires the release VM check.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import subprocess
import sys
import time

from deep32net.files import atomic_write, private_dir, read_bytes, safe

TOOLS = frozenset(('rag_status', 'search_rag', 'search_many', 'get_section',
                   'get_outline', 'lookup_id', 'lookup_rfc', 'find_command',
                   'grep_corpus', 'list_documents', 'verify_quote'))
PROXY = {'Proxy': 'http://127.0.0.1:8766/mcp'}
PAIR = {'TCPForward': '127.0.0.1:7454'}
CT_NOTICE = ('HTTPS certificates put this machine name in public Certificate Transparency logs; '
             'run /usr/local/d32/sbin/d32-publish publish --accept-ct to agree or leave publication off.')


class Refusal(Exception):
    def __init__(self, reason, message, code=14):
        super().__init__(message)
        self.reason, self.code = reason, code


def refuse(reason, message, code=14):
    raise Refusal(reason, message, code)


def dns_name(value):
    value = value.rstrip('.') if isinstance(value, str) else ''
    if (len(value) > 253 or not value.endswith('.ts.net') or len(value.split('.')) < 4
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                   for label in value.split('.'))):
        refuse('dns-name-unavailable', 'The Tailscale DNS name is unavailable; sign in and run /usr/local/d32/sbin/d32-publish resume.')
    return value


def identity(status):
    if not isinstance(status, dict) or status.get('BackendState') != 'Running':
        refuse('signin-required', 'Tailscale is signed out or stopped; run doas /usr/local/d32/sbin/d32-link login and then /usr/local/d32/sbin/d32-publish resume.', 10)
    node, tailnet = status.get('Self'), status.get('CurrentTailnet')
    if (not isinstance(node, dict) or node.get('Online') is not True
            or node.get('KeyExpired') is True or node.get('Expired') is True):
        refuse('signin-required', 'The Tailscale node is offline or its key has expired; run doas /usr/local/d32/sbin/d32-link login and then /usr/local/d32/sbin/d32-publish resume.', 10)
    if not isinstance(tailnet, dict) or tailnet.get('MagicDNSEnabled') is not True:
        refuse('magicdns-disabled', 'MagicDNS is off or unknown; enable MagicDNS in Tailscale admin console > DNS > MagicDNS and run /usr/local/d32/sbin/d32-publish resume.', 10)
    host = dns_name(node.get('DNSName'))
    certificates = status.get('CertDomains')
    if not isinstance(certificates, list) or host not in [v.rstrip('.') for v in certificates if isinstance(v, str)]:
        refuse('https-disabled', 'HTTPS certificates are off or unknown; enable HTTPS Certificates in Tailscale admin console > DNS > HTTPS Certificates and run /usr/local/d32/sbin/d32-publish resume.', 10)
    addresses = node.get('TailscaleIPs', status.get('TailscaleIPs', []))
    address = next((v for v in addresses if isinstance(v, str) and _tailnet_ip(v)), None) if isinstance(addresses, list) else None
    if address is None:
        refuse('address-unavailable', 'The Tailscale address is unavailable; restore the network link and run /usr/local/d32/sbin/d32-publish resume.')
    return host, address


def _tailnet_ip(value):
    try:
        return ipaddress.ip_address(value) in ipaddress.ip_network('100.64.0.0/10')
    except ValueError:
        return False


def _normal(value):
    if isinstance(value, dict):
        values = {k: _normal(v) for k, v in value.items()}
        return {k: v for k, v in values.items() if v not in ({}, None)}
    return value


def handlers(config, host):
    try:
        table = config.get('Web', {}).get(host + ':443', {}).get('Handlers', {})
        if not isinstance(config, dict) or not isinstance(table, dict):
            raise ValueError()
        return table
    except (AttributeError, TypeError, ValueError):
        refuse('serve-state-invalid', 'The Serve route table is unsupported; check the pinned Tailscale version before retrying publication.')


def without_owned(config, host):
    result = deepcopy(config)
    table = handlers(result, host)
    table.pop('/mcp', None)
    result.get('TCP', {}).pop('7454', None)
    # Serve may remove its HTTPS listener after removing the last Web handler.
    web = result.get('Web', {})
    if not any(v.get('Handlers') for k, v in web.items() if k.endswith(':443')):
        if result.get('TCP', {}).get('443') == {'HTTPS': True}:
            result['TCP'].pop('443')
    return _normal(result)


def route_check(config, host, owned=False):
    table = handlers(config, host)
    tcp = config.get('TCP', {})
    if not isinstance(tcp, dict) or not isinstance(config.get('Web', {}), dict):
        refuse('serve-state-invalid', 'The Serve route table is unsupported; check the pinned Tailscale version before retrying publication.')
    if (any(not isinstance(value, dict) for value in tcp.values())
            or any(not isinstance(value, dict) or not isinstance(value.get('Handlers', {}), dict)
                   for value in config.get('Web', {}).values())
            or not isinstance(config.get('AllowFunnel', {}), dict)):
        refuse('serve-state-invalid', 'The Serve route table is unsupported; check the pinned Tailscale version before retrying publication.')
    for path in table:
        if not isinstance(path, str) or path == '/' or path.startswith('/mcp/'):
            refuse('route-conflict', 'An existing Serve route overlaps MCP; remove the conflicting route yourself and run /usr/local/d32/sbin/d32-publish resume.')
    for path, value in table.items():
        if path == '/mcp' and (not owned or value != PROXY):
            refuse('route-conflict', 'The MCP Serve route belongs to another configuration; review it before retrying publication.')
    if '443' in tcp and tcp['443'] != {'HTTPS': True}:
        refuse('route-conflict', 'Serve port 443 belongs to another configuration; review it before retrying publication.')
    if '7454' in tcp and (not owned or tcp['7454'] != PAIR):
        refuse('route-conflict', 'Serve port 7454 belongs to another configuration; review it before retrying publication.')
    if config.get('AllowFunnel', {}).get(host + ':443'):
        refuse('public-route-refused', 'Funnel is enabled for this gateway; turn it off yourself before retrying tailnet-only publication.')


def routes_present(config, host):
    route_check(config, host, owned=True)
    return (handlers(config, host).get('/mcp') == PROXY
            and config.get('TCP', {}).get('443') == {'HTTPS': True}
            and config.get('TCP', {}).get('7454') == PAIR)


class Platform:
    """OpenBSD boundary; tests replace it and never execute platform commands."""
    def __init__(self, root=Path('/'), emit=lambda text: None):
        self.root, self.emit = root, emit

    def command(self, args, reason, *, json_output=False, check=True):
        self.emit(reason + '; if it stops, correct the reported prerequisite and run /usr/local/d32/sbin/d32-publish resume.')
        executable = '/usr/local/bin/tailscale' if args[0] == 'tailscale' else '/usr/sbin/rcctl'
        try:
            result = subprocess.run([executable, *args[1:]], capture_output=True, timeout=10, check=False)
            if result.returncode and check:
                raise ValueError()
            if json_output:
                value = json.loads(result.stdout)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
            return result.returncode
        except (OSError, ValueError, subprocess.TimeoutExpired):
            refuse('platform-unavailable', 'The Tailscale or gateway operation failed; check the installed service and pinned Serve support before running /usr/local/d32/sbin/d32-publish resume.')

    def status(self):
        return self.command(['tailscale', 'status', '--json'], 'Waiting for local Tailscale status', json_output=True)

    def serve_status(self):
        return self.command(['tailscale', 'serve', 'status', '--json'], 'Waiting for the local Serve route table', json_output=True)

    def serve(self, args):
        self.command(['tailscale', 'serve', *args], 'Waiting for the owned Serve route update')

    def start_gateway(self, host):
        if self.command(['rcctl', 'check', 'd32front'], 'Checking the legacy gateway front', check=False) == 0:
            refuse('legacy-front-active', 'The legacy relayd front is active; turn it off with d32-edge-front off before publishing.')
        directory = private_dir(self.root / 'etc/d32/private')
        if os.name == 'posix':
            import grp
            gid = grp.getgrnam('_d32edge').gr_gid
            os.chown(directory, 0, 0)
            os.chmod(directory, 0o711)
        path = directory / 'publication-dns'
        atomic_write(path, (host + '\n').encode('ascii'), mode=0o440 if os.name == 'posix' else 0o600)
        if os.name == 'posix':
            os.chown(path, 0, gid)
            os.chmod(path, 0o440)
        atomic_write(self.root / 'etc/d32/state/edge_front', b'on\n')
        self.command(['rcctl', '-f', 'restart', 'd32edge'], 'Waiting for the loopback gateway to restart')

    def stop_gateway_front(self, previous):
        atomic_write(self.root / 'etc/d32/state/edge_front', (previous + '\n').encode('ascii'))
        path = safe(self.root / 'etc/d32/private/publication-dns')
        if path.exists():
            path.unlink()
        self.command(['rcctl', '-f', 'restart', 'd32edge'], 'Waiting for the private gateway to restart')

    def _token(self):
        path = safe(self.root / 'etc/d32/state/mcp.token')
        meta = path.stat()
        if os.name == 'posix' and (meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o600):
            raise ValueError()
        value = read_bytes(path).decode('ascii')
        if not re.fullmatch('[0-9a-f]{64}\n', value):
            raise ValueError()
        return value[:-1]

    def verify_gateway(self, host, address):
        """Probe the exact backend the owned Serve route proxies to (PROXY), with the published name as Host.

        A connection from this machine to its own tailnet address never reaches Tailscale Serve on OpenBSD (the
        kernel delivers it locally), so the probe goes to the loopback gateway front instead; the route itself is
        checked separately against PROXY. It does not assert an external agent's DNS or model readiness.
        """
        self.emit('Waiting for gateway TLS, authentication and eleven tools; restore publication and run /usr/local/d32/sbin/d32-publish resume if this fails.')
        try:
            token = self._token()
            def request(path, authenticated=False):
                self.emit('Waiting for the published ' + path + ' authentication and path check; restore the gateway and run /usr/local/d32/sbin/d32-publish resume if it fails.')
                deadline = time.monotonic() + 10
                conn = http.client.HTTPConnection('127.0.0.1', 8766, timeout=10)
                try:
                    headers = {'Host': host, 'Accept': 'application/json, text/event-stream',
                               'Content-Type': 'application/json', 'MCP-Protocol-Version': '2025-03-26'}
                    if authenticated:
                        headers['Authorization'] = 'Bearer ' + token
                    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}})
                    conn.request('POST', path, body, headers)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    if conn.sock:
                        conn.sock.settimeout(remaining)
                    response = conn.getresponse()
                    chunks, size = [], 0
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError()
                        if conn.sock:
                            conn.sock.settimeout(remaining)
                        chunk = response.read1(min(65536, 1048577 - size))
                        if not chunk:
                            return response.status, b''.join(chunks)
                        size += len(chunk)
                        if size > 1048576:
                            raise ValueError()
                        chunks.append(chunk)
                finally:
                    conn.close()
            if request('/mcp')[0] != 401:
                raise ValueError()
            code, raw = request('/mcp', True)
            tools = json.loads(raw)['result']['tools']
            if code != 200 or len(tools) != len(TOOLS) or {row['name'] for row in tools} != TOOLS:
                raise ValueError()
            for path in ('/mcp/', '/admin', '/health', '/v1/embeddings'):
                if request(path, True)[0] != 404:
                    raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            refuse('gateway-unverified', 'Gateway TLS, authentication or tools could not be verified; restore the gateway and run /usr/local/d32/sbin/d32-publish resume.')


class Publication:
    def __init__(self, platform, root=Path('/'), emit=lambda text: None):
        self.platform, self.root, self.emit = platform, safe(root), emit
        self.path = self.root / 'etc/d32/state/publication.json'

    def load(self):
        if not safe(self.path).exists():
            return None
        try:
            if os.name == 'posix':
                meta, parent = self.path.stat(), self.path.parent.stat()
                if (meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o600
                        or parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) != 0o700):
                    raise ValueError()
            value = json.loads(read_bytes(self.path))
            if (value.get('format') != 'deep32-publication' or value.get('version') != 1
                    or type(value.get('desired')) is not bool or value.get('previous_front') not in ('on', 'off')
                    or value.get('phase') not in ('publishing', 'ready', 'removing', 'off')):
                raise ValueError()
            dns_name(value.get('host'))
            return value
        except (ValueError, TypeError, AttributeError):
            refuse('journal-invalid', 'Publication state is damaged; restore its private journal before retrying publication.')

    def save(self, state):
        atomic_write(self.path, (json.dumps(state, sort_keys=True) + '\n').encode('ascii'))

    def _result(self, state, ready=False):
        result = {'ok': ready or not state or state['phase'] == 'off', 'state': 'ready' if ready else state['phase'] if state else 'off',
                  'published': ready, 'model_readiness': 'not-checked'}
        if ready:
            url = 'https://' + state['host'] + '/mcp'
            result.update(url=url, tools=len(TOOLS), pairing_port=7454,
                          connect='bash adapters/connect.sh --agent both --workstation ' + url,
                          connect_windows='powershell -NoProfile -ExecutionPolicy Bypass -File adapters\\connect.ps1 -Agent both -Workstation ' + url)
        return result

    def status(self):
        state = self.load()
        if state and state['phase'] == 'removing':
            refuse('removal-incomplete', 'Publication removal is incomplete; run /usr/local/d32/sbin/d32-publish resume to finish removing its owned routes.')
        if not state or not state['desired']:
            return self._result(state)
        host, address = identity(self.platform.status())
        if host != state['host']:
            refuse('identity-changed', 'The published Tailscale name has changed; unpublish the recorded route before publishing the new name.')
        if not routes_present(self.platform.serve_status(), host):
            refuse('route-missing', 'The owned publication route is missing; run /usr/local/d32/sbin/d32-publish resume to restore it.')
        self.platform.verify_gateway(host, address)
        return self._result(state, True)

    def publish(self, accept_ct=False, resume=False):
        state = self.load()
        if resume and state and state['phase'] == 'removing':
            return self.unpublish()
        if state and state['phase'] == 'removing':
            refuse('removal-incomplete', 'Publication removal is incomplete; run /usr/local/d32/sbin/d32-publish resume before publishing again.')
        if resume and (not state or not state['desired']):
            return self._result(state)
        host, address = identity(self.platform.status())
        if state and state['desired'] and state['host'] != host:
            refuse('identity-changed', 'The published Tailscale name has changed; unpublish the recorded route before publishing the new name.')
        config = self.platform.serve_status()
        continuing = bool(state and state['desired'])
        route_check(config, host, owned=continuing)
        if not state or not state.get('ct_notice'):
            self.emit(CT_NOTICE)
        if not state or not state.get('ct_ack'):
            if not accept_ct:
                if not state or not state.get('ct_notice'):
                    state = {'format': 'deep32-publication', 'version': 1, 'desired': False,
                             'phase': 'off', 'host': host, 'previous_front': 'off', 'ct_notice': True}
                    self.save(state)
                refuse('ct-consent-required', 'Certificate Transparency agreement is required; run /usr/local/d32/sbin/d32-publish publish --accept-ct or leave publication off.', 10)
        if continuing and state['phase'] == 'ready' and routes_present(config, host):
            try:
                self.platform.verify_gateway(host, address)
                return self._result(state, True)
            except Refusal as error:
                if error.reason != 'gateway-unverified':
                    raise
        previous = state['previous_front'] if continuing else 'off'
        if not continuing:
            front = self.root / 'etc/d32/state/edge_front'
            if safe(front).exists() and read_bytes(front) != b'off\n':
                refuse('front-already-enabled', 'The loopback gateway front is already owned by another action; turn it off before publishing.')
        state = {'format': 'deep32-publication', 'version': 1, 'desired': True,
                 'phase': 'publishing', 'host': host, 'previous_front': previous,
                 'ct_notice': True, 'ct_ack': True}
        self.save(state)  # Ownership is durable before any external side effect.
        self.platform.start_gateway(host)
        if handlers(config, host).get('/mcp') != PROXY:
            self._change(host, ['--bg', '--https=443', '--set-path=/mcp', PROXY['Proxy']])
        if self.platform.serve_status().get('TCP', {}).get('7454') != PAIR:
            self._change(host, ['--bg', '--tcp=7454', 'tcp://127.0.0.1:7454'])
        if not routes_present(self.platform.serve_status(), host):
            refuse('route-unverified', 'Serve did not preserve the required MCP and pairing routes; check pinned path support before running /usr/local/d32/sbin/d32-publish resume.')
        self.platform.verify_gateway(host, address)
        state['phase'] = 'ready'
        self.save(state)
        return self._result(state, True)

    def _change(self, host, args):
        before = self.platform.serve_status()
        route_check(before, host, owned=True)
        self.platform.serve(args)
        after = self.platform.serve_status()
        route_check(after, host, owned=True)
        if without_owned(before, host) != without_owned(after, host):
            refuse('unrelated-route-changed', 'Serve changed an unrelated route; restore that route and review pinned Serve support before retrying publication.')

    def unpublish(self):
        state = self.load()
        if not state or state['phase'] == 'off':
            return self._result(state)
        host = state['host']
        state.update(desired=False, phase='removing')
        self.save(state)
        config = self.platform.serve_status()
        route_check(config, host, owned=True)
        if handlers(config, host).get('/mcp') == PROXY:
            self._change(host, ['--https=443', '--set-path=/mcp', 'off'])
        if self.platform.serve_status().get('TCP', {}).get('7454') == PAIR:
            self._change(host, ['--tcp=7454', 'off'])
        self.platform.stop_gateway_front(state['previous_front'])
        state['phase'] = 'off'
        self.save(state)
        return self._result(state)


@contextmanager
def publication_lock(root):
    path = safe(root / 'etc/d32/state/publication.lock')
    private_dir(path.parent)
    if os.name == 'posix':
        parent = path.parent.stat()
        if parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) != 0o700:
            refuse('state-permissions', 'Publication state permissions are unsafe; restore root ownership and mode 0700 before retrying.')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o600:
            refuse('state-permissions', 'Publication lock permissions are unsafe; restore root ownership and mode 0600 before retrying.')
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            refuse('publication-busy', 'Another publication action is running; let it finish and run /usr/local/d32/sbin/d32-publish status.', 11)
        yield
    finally:
        os.close(fd)


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            refuse('usage', 'Use /usr/local/d32/sbin/d32-publish publish [--accept-ct], unpublish, status or resume.', 1)
    parser = Parser(description='Publish only the owned tailnet MCP and pairing routes.')
    parser.add_argument('action', choices=('publish', 'unpublish', 'status', 'resume'))
    parser.add_argument('--accept-ct', action='store_true')
    emit = lambda message: print(message, file=sys.stderr, flush=True)
    try:
        args = parser.parse_args(argv)
        if args.accept_ct and args.action != 'publish':
            parser.error('--accept-ct is valid only with publish')
        if not hasattr(os, 'geteuid') or os.geteuid() != 0:
            refuse('root-required', 'Publication needs local administrator access; run /usr/local/d32/sbin/d32-publish as root with the same action.', 10)
        controller = Publication(Platform(emit=emit), emit=emit)
        if args.action == 'status':  # Status creates no file and acquires no write lock.
            result = controller.status()
        else:
            with publication_lock(Path('/')):
                if args.action == 'unpublish':
                    result = controller.unpublish()
                else:
                    result = controller.publish(args.accept_ct, resume=args.action == 'resume')
        print(json.dumps(result, sort_keys=True))
        if result.get('published'):
            emit('Connect: ' + result['connect'])
            emit('Windows: ' + result['connect_windows'])
        return 0
    except Refusal as error:
        print(str(error), file=sys.stderr)
        return error.code
    except Exception:
        print('Publication state or service is unavailable; check its private files and run /usr/local/d32/sbin/d32-publish resume.', file=sys.stderr)
        return 14
