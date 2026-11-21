"""Pure, passive shared role policies over already obtained observations.

These functions do not construct an owner, open a file, discover a peer, or run
a command.  Platform adapters must obtain protected evidence through qualified
read-only seams.  Missing ownership evidence fails closed.
"""
from dataclasses import dataclass, field
import re

from .errors import DiagError, bounded
from .models import State, tailnet_path
from .profile import PeerRecord, ProfileError, parse_c6, parse_publication
from .report import Reason, row
from .runner import strict_json


def _row(role, check, state, values=None, *, blocked_by=()):
    return row(check, state, Reason.PASSED if state is State.OK else
               Reason.BLOCKED if blocked_by else Reason.INAPPLICABLE if state is State.SKIP
               else Reason.UNAVAILABLE, values, blocked_by=blocked_by,
               policy=role + '.' + check.split('.')[0])


def _role(role, *, headend=False):
    if role not in (('workstation', 'forge', 'headend') if headend else ('workstation', 'forge')):
        raise DiagError('invalid-observation')


def _peer_alias(alias, peer_role):
    return (peer_role in ('workstation', 'forge', 'headend') and type(alias) is str
            and re.fullmatch(peer_role + r'(?:-[1-9][0-9]?)?', alias) is not None)


def _status(status):
    if type(status) is bytes:
        status = strict_json(status)
    if (type(status) is not dict or status.get('BackendState') not in
            ('Running', 'NeedsLogin', 'NeedsMachineAuth', 'Stopped', 'NoState')
            or status.get('Self') is not None and type(status.get('Self')) is not dict):
        return None
    return status


@bounded('invalid-observation')
def tailscale_rows(status, *, role, installed=None, version=None):
    """Executable presence is independent of daemon, sign-in and peer state."""
    _role(role, headend=True)
    if installed is not None and type(installed) is not bool:
        raise DiagError('invalid-observation')
    values = {} if installed is None else {'present': installed}
    if version is not None:
        values['version'] = version
    result = [row('tailscale-installed', State.WARN, Reason.UNSUPPORTED, values,
                  policy=role + '.tailscale-installed') if installed is None else
              _row(role, 'tailscale-installed', State.OK if installed else State.FAIL, values)]
    if installed is False:
        return tuple(result + [_row(role, 'tailscale-running', State.SKIP,
            blocked_by=('tailscale-installed',)), _row(role, 'tailscale-signin', State.SKIP,
            blocked_by=('tailscale-installed',))])
    try:
        status = _status(status)
    except DiagError:
        status = None
    if status is None:
        return tuple(result + [_row(role, 'tailscale-running', State.FAIL, {'present': False}),
            _row(role, 'tailscale-signin', State.SKIP, blocked_by=('tailscale-running',))])
    backend, node = status['BackendState'], status.get('Self') or {}
    result.append(_row(role, 'tailscale-running', State.OK, {'present': True, 'state': backend}))
    online = node.get('Online') is True
    # An explicit true expiry wins over every other observation.
    expired = node.get('KeyExpired') is True or node.get('Expired') is True
    expiry_known = (any(node.get(key) is False for key in ('KeyExpired', 'Expired'))
                    and all(key not in node or type(node[key]) is bool for key in ('KeyExpired', 'Expired')))
    good = backend == 'Running' and online and not expired
    state = State.FAIL if not good else State.OK if expiry_known else State.WARN
    values = {'online': online, 'state': backend}
    if expiry_known or expired:
        values['expired'] = expired
    result.append(_row(role, 'tailscale-signin', state, values))
    return tuple(result)


def _dns(value):
    value = value.rstrip('.') if type(value) is str else ''
    if (len(value) > 253 or not value.endswith('.ts.net') or len(value.split('.')) < 4
            or any(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) is None
                   for part in value.split('.'))):
        return None
    return value


@bounded('invalid-observation')
def dns_https_rows(status, *, role, resolved=None, tls_valid=None):
    """`resolved` means exact enrolled-peer/address agreement, never any answer."""
    _role(role, headend=True)
    for value in (resolved, tls_valid):
        if value is not None and type(value) is not bool:
            raise DiagError('invalid-observation')
    preceding = tailscale_rows(status, role=role)
    failed = next((item.id for item in preceding if item.state is State.FAIL), None)
    if failed:
        return (_row(role, 'magicdns', State.SKIP, blocked_by=(failed,)),
                _row(role, 'https-available', State.SKIP, blocked_by=(failed,)))
    status = _status(status)
    tailnet = status.get('CurrentTailnet')
    enabled = type(tailnet) is dict and tailnet.get('MagicDNSEnabled') is True
    own_host = _dns(status['Self'].get('DNSName'))
    # A headend is a client of the selected enrolled publisher. Its own node
    # does not need a public certificate or an owned Serve hostname.
    dns_prerequisite = enabled and (role == 'headend' or own_host is not None)
    dns_state = (State.FAIL if not dns_prerequisite or resolved is False else
                 State.WARN if resolved is None else State.OK)
    dns_row = _row(role, 'magicdns', dns_state,
                   {'present': dns_prerequisite, 'matched': resolved is True})
    if dns_state is State.FAIL:
        return (dns_row, _row(role, 'https-available', State.SKIP, blocked_by=('magicdns',)))
    if role == 'headend':
        state = State.WARN if tls_valid is None else State.OK if tls_valid else State.FAIL
        return (dns_row, _row(role, 'https-available', state,
                             {'present': tls_valid is True, 'matched': tls_valid is True}))
    certs = status.get('CertDomains')
    authorised = (type(certs) is list and own_host in
        [value.rstrip('.') for value in certs if type(value) is str])
    state = (State.FAIL if not authorised or tls_valid is False else
             State.WARN if tls_valid is None else State.OK)
    return (dns_row, _row(role, 'https-available', state,
                         {'present': authorised, 'matched': tls_valid is True}))


def _normal(value):
    if type(value) is dict:
        children = {key: _normal(child) for key, child in value.items()}
        return {key: child for key, child in children.items() if child not in ({}, None)}
    return value


def _serve_shape(config, depth=0):
    if type(config) is not dict or depth > 8 or len(config) > 64:
        raise ValueError()
    for key in ('TCP', 'Web', 'AllowFunnel', 'Foreground'):
        if type(config.get(key, {})) is not dict or len(config.get(key, {})) > 256:
            raise ValueError()
    if any(type(item) is not dict for item in config.get('TCP', {}).values()):
        raise ValueError()
    if any(type(item) is not dict or type(item.get('Handlers', {})) is not dict
           for item in config.get('Web', {}).values()):
        raise ValueError()
    for child in config.get('Foreground', {}).values():
        _serve_shape(child, depth + 1)


def _slice(config, port):
    # Extracted from forms/forge_publication.py::Publication.slice; no owner
    # instance or recovery method is needed for this pure projection.
    return _normal({'TCP': {port: config.get('TCP', {}).get(port)},
        'Web': {key: value for key, value in config.get('Web', {}).items()
                if key.rsplit(':', 1)[-1] == port},
        'AllowFunnel': {key: value for key, value in config.get('AllowFunnel', {}).items()
                        if key.rsplit(':', 1)[-1] == port and value},
        'Foreground': {key: _slice(value, port) for key, value in
                       config.get('Foreground', {}).items() if _slice(value, port)}})


def _forge_routes(journal, live):
    if set(journal['added']) != {0, 1, 2}:
        return False
    for item in journal['routes']:
        port = str(item['port'])
        expected = ({'TCP': {port: {'TCPForward': item['target'].removeprefix('tcp://')}}}
            if item['kind'] == 'tcp' else {'TCP': {port: {'HTTPS': True}},
              'Web': {journal['host'] + ':' + port: {'Handlers': {'/': {'Proxy': item['target']}}}}})
        if _slice(live, port) != expected:
            return False
    return True


def _workstation_routes(journal, live):
    # Extracted route_check/routes_present rules. Only the journalled MCP path
    # and pairing port confer ownership; unrelated non-overlapping paths stay.
    host = journal['host']
    handlers = live.get('Web', {}).get(host + ':443', {}).get('Handlers', {})
    if (any(type(path) is not str or path == '/' or path.startswith('/mcp/') for path in handlers)
            or handlers.get('/mcp') != {'Proxy': 'http://127.0.0.1:8766/mcp'}
            or live.get('TCP', {}).get('443') != {'HTTPS': True}
            or live.get('TCP', {}).get('7454') != {'TCPForward': '127.0.0.1:7454'}
            or live.get('AllowFunnel', {}).get(host + ':443')
            or live.get('AllowFunnel', {}).get(host + ':7454')):
        return False
    if _slice(live, '7454') != {'TCP': {'7454': {'TCPForward': '127.0.0.1:7454'}}}:
        return False
    if any(key.rsplit(':', 1)[-1] in ('443', '7454') and value
           for key, value in live.get('AllowFunnel', {}).items()):
        return False
    # A foreground owner or another hostname on the owned pairing port is
    # uncertain ownership, even if the top-level routes happen to match.
    for child in live.get('Foreground', {}).values():
        if _slice(child, '443') or _slice(child, '7454'):
            return False
    return True


@bounded('invalid-observation')
def publication_row(raw, live, *, role, owner_check=None, current_host=None):
    """Compare durable ownership with live exact targets; pending is never OK.

    `current_host` is an independently obtained Tailscale identity. Absence is
    an unsafe ownership decision and therefore FAIL, even with matching routes.
    An optional extra reviewed pure owner validator can tighten this policy.
    """
    _role(role)
    count, good, phase = 0, False, None
    try:
        parsed = parse_publication(raw, role=role)
        journal = strict_json(raw)
        live = strict_json(live) if type(live) is bytes else live
        _serve_shape(live)
        count = 3 if role == 'forge' else 2
        phase = parsed.phase
        good = (parsed.desired and phase == 'ready' and _dns(current_host) == journal['host']
                and (_forge_routes(journal, live) if role == 'forge' else _workstation_routes(journal, live)))
        if owner_check is not None:
            good = good and owner_check(journal, live) is True
    except Exception:
        good = False
    values = {'routes': count, 'matched': bool(good), 'owner_verified': bool(good)}
    if phase in ('ready', 'pending', 'removing', 'active'):
        values['state'] = phase
    return _row(role, 'serve-routes', State.OK if good else State.FAIL, values)


@dataclass(frozen=True)
class PairingAuthority:
    """Independent protected owner evidence, never inferred from C6/mtime.

    The native owner must validate public identity/certificate agreement,
    existing committed pairlock and credential handover without reading a
    private identity key or creating absent state.
    """
    own_pin: str = field(repr=False)
    peer_id: str = field(repr=False)
    peer_pin: str = field(repr=False)
    transport: str
    publish: str | None
    identity_verified: bool = False
    claim_present: bool = False
    claim_in_progress: bool = True
    handover_verified: bool = False
    revoked: bool = False


@bounded('invalid-observation')
def pairing_row(records, *, role, authority=None):
    _role(role)
    good = type(records) in (tuple, list) and 0 < len(records) <= 256
    good = good and type(authority) is PairingAuthority
    if good:
        good = (all(type(value) is bool for value in (authority.identity_verified,
            authority.claim_present, authority.claim_in_progress, authority.handover_verified,
            authority.revoked)) and authority.identity_verified and authority.claim_present
            and not authority.claim_in_progress and authority.handover_verified and not authority.revoked
            and authority.transport in ('tailnet', 'loopback')
            and all(type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin)
                    for pin in (authority.own_pin, authority.peer_pin))
            and authority.own_pin != authority.peer_pin)
    if good:
        expected = ('forge', 'joined', 'workstation') if role == 'forge' else ('forge', 'joiner', 'forge')
        found = False
        for record in records:
            if type(record) is not PeerRecord:
                good = False
                break
            # One selected peer authority cannot certify unrelated records.
            if (record.kind, record.side, record.peer_role) != expected:
                # Forge firstboot.pairing_committed validates every retained
                # record, rather than selecting a convenient committed one.
                if role == 'forge':
                    good = False
                continue
            found = True
            common = (record.own_pin == authority.own_pin and record.peer_pin == authority.peer_pin
                and record.peer_id == authority.peer_id and record.transport == authority.transport
                and record.publish == authority.publish)
            if role == 'forge':
                handover = record.given in (('forge-token', 'kb-public.pem'),
                    ('forge-token', 'kb-public.pem', 'model-endpoints.json')) and not record.received
            else:
                handover = sorted(record.received) in (['forge-token', 'kb-public.pem'],
                    ['forge-token', 'kb-public.pem', 'model-endpoints.json']) and not record.given
            good = good and common and handover
        good = good and found
    values = {'present': bool(records), 'matched': bool(good)}
    if good:
        values['state'] = 'committed'
    return _row(role, 'pairing', State.OK if good else State.FAIL, values)


@bounded('invalid-observation')
def first_start_row(raw, *, role, completed=False, lifecycle_complete=False,
                    pairing_committed=False, fresh=False):
    _role(role)
    if any(type(value) is not bool for value in (completed, lifecycle_complete, pairing_committed, fresh)):
        raise DiagError('invalid-observation')
    if raw is None:
        durable = role == 'forge' and completed and lifecycle_complete and pairing_committed
        return _row(role, 'first-start', State.OK if durable else State.WARN)
    try:
        parsed = parse_c6(raw, dialect=role)
    except ProfileError:
        return _row(role, 'first-start', State.WARN)
    if raw.split(b' ', 3)[2] != role.encode('ascii'):
        return _row(role, 'first-start', State.WARN)
    values = {'stage': parsed.stage, 'state': parsed.state}
    if parsed.version:
        values['version'] = parsed.version
    if parsed.error_kind:
        values['error_kind'] = parsed.error_kind
    state = (State.FAIL if parsed.state == 'failed' else State.OK if completed and fresh
             and parsed.stage == 'ready' and parsed.state == 'done' else State.WARN)
    return _row(role, 'first-start', state, values)


@dataclass(frozen=True)
class PeerEvidence:
    alias: str
    role: str
    status_key: str = field(repr=False)
    online: bool | None = None
    tls_valid: bool | None = None
    http_status: int | None = None


@bounded('invalid-observation')
def peer_rows(peers, status, *, role):
    """Expand only enrolled peers; relay is WARN solely for the path row."""
    _role(role, headend=True)
    if type(peers) not in (tuple, list) or len(peers) > 64:
        raise DiagError('invalid-observation')
    result, aliases = [], set()
    for peer in peers:
        if (type(peer) is not PeerEvidence or peer.role not in ('workstation', 'forge', 'headend')
                or not _peer_alias(peer.alias, peer.role)
                or peer.alias in aliases or any(value is not None and type(value) is not bool
                                              for value in (peer.online, peer.tls_valid))):
            raise DiagError('invalid-observation')
        aliases.add(peer.alias)
        reachable = peer.online is True and peer.tls_valid is True and peer.http_status is not None
        values = {'peer_role': peer.role, 'online': peer.online is True}
        if peer.http_status is not None:
            values['http_status'] = peer.http_status
        result.append(_row(role, 'peer-reachability.' + peer.alias,
                           State.OK if reachable else State.FAIL, values))
        path = tailnet_path(status, peer.status_key)
        result.append(_row(role, 'tailnet-path.' + peer.alias, path.state,
                           {'peer_role': peer.role, 'state': path.path}))
    return tuple(result)
