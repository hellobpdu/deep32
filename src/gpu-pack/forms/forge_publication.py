"""Host-only, journalled Forge Serve routes; never replace another owner's port.

The journal is durable before each addition, as in the Workstation publisher.
An interrupted addition whose effect is uncertain fails closed for operator
reconciliation; an intent alone never grants ownership of an existing route.
All platform effects pass through the lifecycle's bounded, redacted command seam.
"""
from copy import deepcopy
import json
import os
import re
import socket

DEFAULT_PORTS = {'embeddings': 8081, 'reranker': 8082, 'pairing': 7453,
                 'embeddings_https': 443, 'reranker_https': 8443, 'pairing_tcp': 7453}


def ports(settings):
    supplied = settings.get('forge_ports', {})
    if (not isinstance(supplied, dict) or set(supplied) - set(DEFAULT_PORTS)
            or any(type(value) is not int or not 1 <= value <= 65535 for value in supplied.values())):
        raise ValueError('Supply valid C7 forge_ports settings.')
    result = dict(DEFAULT_PORTS, **supplied)
    for keys in (('embeddings', 'reranker', 'pairing'),
                 ('embeddings_https', 'reranker_https', 'pairing_tcp')):
        if len({result[key] for key in keys}) != len(keys):
            raise ValueError('Choose distinct local ports and distinct published ports in C7 forge_ports.')
    return result


def plan(host, settings):
    if (not isinstance(host, str) or len(host) > 253 or
            re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.ts\.net', host) is None):
        raise ValueError('Restore the owning host Tailscale DNS name.')
    selected = ports(settings)
    routes = []
    for name in ('embeddings', 'reranker'):
        routes.append(dict(setting=name + '_https', port=selected[name + '_https'],
                           kind='https', target='http://127.0.0.1:' + str(selected[name])))
    routes.append(dict(setting='pairing_tcp', port=selected['pairing_tcp'], kind='tcp',
                       target='tcp://127.0.0.1:' + str(selected['pairing'])))
    return routes


def endpoints(host, settings):
    selected = ports(settings)
    return {name: 'https://' + host + (':' + str(selected[name + '_https'])
            if selected[name + '_https'] != 443 else '') for name in ('embeddings', 'reranker')}


def normal(value):
    if isinstance(value, dict):
        children = {key: normal(child) for key, child in value.items()}
        return {key: child for key, child in children.items() if child not in ({}, None)}
    return value


class Publication:
    def __init__(self, common, root, platform, call):
        self.common, self.root, self.call = common, root, call
        self.program = 'tailscale.exe' if platform == 'windows' else 'tailscale'
        self.path = root / 'config/forge-publication.json'

    def fail(self, message):
        raise self.common.Refusal(message)

    def status(self):
        raw = self.call([self.program, 'serve', 'status', '--json'], 'host Serve ownership',
                        'restore the host Serve status and retry.', 30)
        try:
            value = json.loads(raw)
            if len(raw) > 1048576 or not isinstance(value, dict):
                raise ValueError()
            self.validate(value)
            return value
        except (ValueError, TypeError, AttributeError):
            self.fail('Restore readable host Tailscale Serve JSON before retrying Forge publication.')

    def validate(self, value):
        for key in ('TCP', 'Web', 'AllowFunnel', 'Foreground'):
            if not isinstance(value.get(key, {}), dict):
                raise ValueError()
        if any(not isinstance(v, dict) for v in value.get('TCP', {}).values()):
            raise ValueError()
        if any(not isinstance(v, dict) or not isinstance(v.get('Handlers', {}), dict)
               for v in value.get('Web', {}).values()):
            raise ValueError()
        for child in value.get('Foreground', {}).values():
            if not isinstance(child, dict):
                raise ValueError()
            self.validate(child)

    def load(self):
        self.common.safe_path(self.path)
        if not self.path.exists():
            return None
        try:
            if self.program == 'tailscale.exe':
                self.common.windows_security(self.path, verify=True)
            value = self.common.read_json(self.path)
            if (set(value) != {'schema', 'install_id', 'host', 'routes', 'added', 'pending', 'removing', 'phase'} or
                    value['schema'] != 'deep32.forge-publication/1' or
                    re.fullmatch('[0-9a-f]{16}', value['install_id']) is None or
                    value['phase'] not in ('publishing', 'ready', 'removing', 'off') or
                    not isinstance(value['added'], list) or
                    any(type(index) is not int or index not in range(3) for index in value['added']) or
                    len(set(value['added'])) != len(value['added']) or
                    value['pending'] is not None and (type(value['pending']) is not int or
                        value['pending'] not in range(3) or value['pending'] in value['added']) or
                    value['removing'] is not None and (type(value['removing']) is not int or
                        value['removing'] not in value['added']) or
                    value['pending'] is not None and value['removing'] is not None):
                raise ValueError()
            routes = value['routes']
            inferred = {row['setting']: row['port'] for row in routes}
            for row, name in zip(routes, ('embeddings', 'reranker', 'pairing')):
                inferred[name] = int(row['target'].rsplit(':', 1)[1])
            if routes != plan(value['host'], {'forge_ports': inferred}):
                raise ValueError()
            return value
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.fail('Restore the private Forge publication journal before retrying; no host route was changed.')

    def save(self, state):
        self.common.atomic_json(self.path, state)
        if os.name == 'posix':
            descriptor = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def expected(self, host, row):
        port = str(row['port'])
        if row['kind'] == 'tcp':
            return {'TCP': {port: {'TCPForward': row['target'].removeprefix('tcp://')}}}
        return {'TCP': {port: {'HTTPS': True}},
                'Web': {host + ':' + port: {'Handlers': {'/': {'Proxy': row['target']}}}}}

    def slice(self, config, row):
        port = str(row['port'])
        return normal({'TCP': {port: config.get('TCP', {}).get(port)},
            'Web': {key: value for key, value in config.get('Web', {}).items() if key.rsplit(':', 1)[-1] == port},
            'AllowFunnel': {key: value for key, value in config.get('AllowFunnel', {}).items()
                            if key.rsplit(':', 1)[-1] == port and value},
            'Foreground': {key: self.slice(value, row) for key, value in config.get('Foreground', {}).items()
                           if self.slice(value, row)}})

    def rest(self, config, row):
        result = deepcopy(config)
        port = str(row['port'])
        result.get('TCP', {}).pop(port, None)
        for key in ('Web', 'AllowFunnel'):
            result[key] = {name: value for name, value in result.get(key, {}).items()
                           if name.rsplit(':', 1)[-1] != port}
        return normal(result)

    def check(self, config, state):
        for index, row in enumerate(state['routes']):
            actual = self.slice(config, row)
            if actual and (index not in state['added'] or actual != self.expected(state['host'], row)):
                self.fail('Host Serve port ' + str(row['port']) + ' already has an unowned route; change C7 forge_ports.' + row['setting'] + '.')

    def recover_pending(self, config, state):
        removal = state['removing']
        if removal is not None:
            row = state['routes'][removal]
            if self.slice(config, row):
                self.fail('Interrupted Forge removal left port ' + str(row['port']) +
                          ' with uncertain ownership; have the operator reconcile that host route before retrying.')
            state['added'].remove(removal)
            state['removing'] = None
        index = state['pending']
        if index is not None:
            row = state['routes'][index]
            if self.slice(config, row):
                self.fail('Interrupted Forge publication left port ' + str(row['port']) +
                          ' with uncertain ownership; have the operator reconcile that host route before retrying.')
            state['pending'] = None  # No external effect exists, so retry is safe.

    def preflight(self, install_id, host, settings):
        routes = plan(host, settings)
        saved = self.load()
        if saved and saved['phase'] != 'off':
            if saved['install_id'] != install_id or saved['host'] != host or saved['routes'] != routes:
                self.fail('Stop the recorded Forge to remove its owned routes before changing C7 forge_ports or host identity.')
            if saved['phase'] == 'removing':
                self.fail('Resume Forge stop to finish its recorded route removal before starting.')
            state = saved
        else:
            state = dict(schema='deep32.forge-publication/1', install_id=install_id,
                         host=host, routes=routes, added=[], pending=None, removing=None, phase='publishing')
        config = self.status()
        self.recover_pending(config, state)
        self.check(config, state)
        return state

    def change(self, state, index, remove=False):
        row = state['routes'][index]
        before = self.status()
        self.check(before, state)
        actual = self.slice(before, row)
        if remove and not actual:
            state['added'].remove(index)
            self.save(state)
            return
        if not remove and actual:
            return
        if not remove:
            if index in state['added']:
                state['added'].remove(index)  # Its old route is gone; no claim on a future one.
            state['pending'] = index
            self.save(state)  # Durable intent; ownership awaits verified success.
        else:
            state['removing'] = index
            self.save(state)
        args = ['--' + row['kind'] + '=' + str(row['port'])]
        args += ['off'] if remove else ['--bg', row['target']]
        self.call([self.program, 'serve', *args], 'owned Forge Serve route update',
                  'retry the same Forge start or stop using its retained journal.', 30)
        after = self.status()
        verified = deepcopy(state)
        if not remove and index not in verified['added']:
            verified['added'].append(index)
        self.check(after, verified)
        expected = {} if remove else self.expected(state['host'], row)
        if self.slice(after, row) != expected or self.rest(before, row) != self.rest(after, row):
            self.fail('The Forge Serve update could not be verified; retain its journal and retry the same action.')
        if not remove:
            state['added'] = sorted(verified['added'])
            state['pending'] = None
            self.save(state)
        else:
            state['added'].remove(index)
            state['removing'] = None
            self.save(state)

    def publish(self, install_id, host, settings):
        if settings.get('forge_accept_ct') is not True:
            self.fail('HTTPS certificates publish this node name in Certificate Transparency; set C7 forge_accept_ct to true to agree.')
        state = self.preflight(install_id, host, settings)
        self.save(state)
        for index in range(len(state['routes'])):
            self.change(state, index)
        state['phase'] = 'ready'
        self.save(state)
        return endpoints(host, settings)

    def remove(self):
        state = self.load()
        if not state or state['phase'] == 'off':
            return
        self.recover_pending(self.status(), state)
        state['phase'] = 'removing'
        self.save(state)
        for index in reversed(state['added'][:]):
            self.change(state, index, remove=True)
        state['phase'] = 'off'
        self.save(state)


def check_local(settings, owned=(), socket_factory=None):
    """Probe host loopback listeners, including Windows exclusive bind semantics."""
    selected = ports(settings)
    for name in ('embeddings', 'reranker', 'pairing'):
        if selected[name] in owned:
            continue
        probe = (socket_factory or socket.socket)(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(('127.0.0.1', selected[name]))
        except OSError:
            raise ValueError('Host local port ' + str(selected[name]) + ' is in use; change C7 forge_ports.' + name + '.') from None
        finally:
            probe.close()
