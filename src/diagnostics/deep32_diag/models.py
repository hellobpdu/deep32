"""Typed diagnostic observations and a pure running-attestor snapshot reader.

This never constructs Attestor (its constructor reads a probe file), calls a
probe/loop, or weakens revision checking. Existing Attestor.snapshot is usable
only through a qualified in-process owner seam; its reason/hub never escape.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
import re

from .errors import DiagError, bounded


class State(str, Enum):
    OK = 'OK'
    WARN = 'WARN'
    FAIL = 'FAIL'
    SKIP = 'SKIP'


FIELDS = {
    'installation': {'matched', 'present', 'version'},
    'tailscale-installed': {'present', 'version'},
    'tailscale-running': {'present', 'state'},
    'tailscale-signin': {'online', 'expired', 'state'},
    'tailnet-path': {'state', 'peer_role'},
    'magicdns': {'matched', 'present'},
    'https-available': {'present', 'matched', 'http_status'},
    'serve-routes': {'routes', 'matched', 'owner_verified', 'state'},
    'pairing': {'matched', 'present', 'state', 'peer_role'},
    'peer-reachability': {'online', 'http_status', 'peer_role'},
    'peer-clock': {'peer_role', 'offset_lower_ms', 'offset_upper_ms', 'rtt_ms'},
    'first-start': {'version', 'stage', 'state', 'error_kind'},
    'driver': {'version', 'minimum_version'},
    'docker-engine': {'version', 'present', 'matched'},
    'docker-compose': {'version', 'minimum_version'},
    'gpu-runtime': {'present'},
    'gpu-visible': {'device_kind'},
    'container': {'service', 'state', 'healthy', 'matched'},
    'model': {'model', 'revision', 'expected_revision', 'matched', 'age_seconds'},
    'model-attestation': {'model', 'revision', 'expected_revision', 'matched', 'age_seconds'},
    'port': {'port', 'present', 'owner_verified'},
    'disk': {'free_bytes', 'required_bytes', 'safe_running_bytes'},
    'gpu-hold': {'present', 'state'},
    'windows-pairing': {'present'},
    'engine': {'healthy', 'present'}, 'gateway': {'healthy', 'present'},
    'index-generation': {'generation', 'expected_generation', 'matched'},
    'index-fingerprint': {'fingerprint', 'expected_fingerprint', 'matched'},
    'forge-auth': {'http_status', 'matched'},
    'dense': {'enabled', 'healthy', 'breaker'},
    'reranker': {'enabled', 'healthy', 'breaker'},
    'index-health': {'n_docs', 'n_chunks', 'healthy'},
    'agent-profile': {'agent', 'matched'}, 'agent-connector': {'agent', 'matched'},
    'agent-installed': {'agent', 'version', 'present'},
    'agent-signin': {'agent', 'matched'}, 'gateway-auth': {'http_status', 'matched'},
    'mcp-rag-status': {'tool_count', 'healthy', 'generation', 'fingerprint'},
    'arguments': set(), 'doctor': set(), 'snapshot-changed': set(),
}
BOOLS = frozenset({'matched', 'present', 'online', 'expired', 'owner_verified', 'healthy', 'enabled'})
INTS = frozenset({'routes', 'port', 'free_bytes', 'required_bytes', 'safe_running_bytes',
                  'n_docs', 'n_chunks', 'tool_count', 'http_status'})
NUMBERS = frozenset({'offset_lower_ms', 'offset_upper_ms', 'rtt_ms', 'age_seconds'})
PUBLIC = frozenset({'model', 'revision', 'expected_revision', 'generation', 'expected_generation',
                   'fingerprint', 'expected_fingerprint'})
ENUMS = {
    'state': {'Running', 'NeedsLogin', 'NeedsMachineAuth', 'Stopped', 'NoState', 'online', 'offline',
              'direct', 'relayed', 'unknown', 'ready', 'done', 'waiting', 'failed', 'running',
              'stopped', 'restarting', 'starting', 'healthy', 'unhealthy', 'exited', 'dead',
              'created', 'paused', 'committed', 'pending', 'removing', 'active', 'recovery'},
    'stage': {'BOOT', 'CHECK', 'VERIFY', 'IMPORT', 'ENROL', 'READY', 'DONE', 'FAILED',
              'boot', 'check', 'verify', 'import', 'enrol', 'ready', 'done', 'failed', 'pairing',
              'install', 'import', 'firstboot', 'keys', 'signin', 'tailnet', 'content'},
    'error_kind': {None, 'E_NET', 'E_PAIR', 'E_VERIFY', 'E_STORAGE', 'E_STATE',
                   'network', 'pairing', 'verification', 'storage', 'state', 'unknown', 'reported-failure',
                   'forge-settings-invalid', 'forge-identity-unsafe', 'forge-identity-incomplete',
                   'forge-identity-invalid', 'forge-pairing-expired', 'forge-pairing-failed',
                   'forge-model-auth-denied', 'forge-model-response-invalid', 'forge-model-health-timeout',
                   'forge-firstboot-running', 'forge-firstboot-failed'},
    'breaker': {None, 'closed', 'open', 'half-open', 'unknown'},
    'device_kind': {'gpu', 'cpu', 'unknown'},
    'peer_role': {'workstation', 'forge', 'headend'}, 'agent': {'codex', 'claude'},
    'service': {'embeddings', 'reranker', 'door', 'converter', 'builder', 'qdrant', 'full', 'model-bridge'},
}


@bounded('invalid-observation')
def family(check_id):
    if type(check_id) is not str or not re.fullmatch(r'[a-z][a-z0-9.-]{0,63}', check_id):
        raise DiagError('invalid-observation')
    key = check_id.split('.')[0]
    if key not in FIELDS:
        raise DiagError('invalid-observation')
    return key


@bounded('invalid-observation')
def observed(check_id, values, *, public_identities=frozenset()):
    """Select check-specific fields; public IDs must match authenticated metadata.

    `public_identities` is an installer/importer trust input, never values copied
    out of a response merely to make them printable. Mismatches print booleans.
    """
    key = family(check_id)
    if type(values) is not dict or len(values) > 16 or set(values) - FIELDS[key]:
        raise DiagError('invalid-observation')
    result = {}
    for name, value in values.items():
        good = False
        if name in BOOLS:
            good = type(value) is bool
        elif name in INTS:
            good = type(value) is int and 0 <= value <= 2**63 - 1
            if name == 'port':
                good = good and 1 <= value <= 65535
            if name == 'http_status':
                good = good and 100 <= value <= 599
        elif name in NUMBERS:
            good = type(value) in (int, float) and math.isfinite(value)
            if name in ('rtt_ms', 'age_seconds'):
                good = good and value >= 0
        elif name in ENUMS:
            good = (value is None or type(value) is str) and value in ENUMS[name]
        elif name in ('version', 'minimum_version'):
            good = type(value) is str and len(value) <= 64 and re.fullmatch(r'v?\d+(?:\.\d+){0,3}(?:-(?:alpha|beta|rc)\.\d+)?', value) is not None
        elif name in PUBLIC:
            good = type(value) is str and value in public_identities
            pattern = (r'[0-9a-f]{64}' if 'fingerprint' in name else r'[0-9a-f]{40}'
                       if 'revision' in name else r'[A-Za-z0-9._/-]{1,128}'
                       if name == 'model' else r'[A-Za-z0-9._+-]{1,128}')
            good = good and re.fullmatch(pattern, value) is not None
            if name == 'model':
                good = good and all(part not in ('', '.', '..') for part in value.split('/'))
        if not good:
            raise DiagError('invalid-observation')
        result[name] = value
    return result


@dataclass(frozen=True)
class AttestationObservation:
    state: State
    reason: str
    matched: bool
    age_seconds: float | None = None


@bounded('invalid-observation')
def observe_attestation(snapshot, kind, *, expected_model, expected_revision, now, maximum_age):
    """Pure snapshot projection. Missing new measured-identity fields stay WARN.

    The deployed snapshot supplies verified/checked_at/reason, but no measured
    ID/revision/device. J07's owner adapter must add and qualify those fields.
    No cached timestamp is ever used as peer clock evidence.
    """
    if (kind not in ('embed', 'rerank') or type(snapshot) is not dict
            or type(expected_model) is not str or len(expected_model) > 256
            or not re.fullmatch(r'[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*', expected_model)
            or any(part in ('.', '..') for part in expected_model.split('/'))
            or type(expected_revision) is not str or not re.fullmatch(r'[0-9a-f]{40,64}', expected_revision)
            or type(now) not in (int, float) or not math.isfinite(now)
            or type(maximum_age) not in (int, float) or not math.isfinite(maximum_age) or maximum_age <= 0):
        raise DiagError('invalid-observation')
    row = snapshot.get(kind)
    if type(row) is not dict or snapshot.get('gpu_ext') != 'on':
        return AttestationObservation(State.WARN, 'unavailable', False)
    if row.get('verified') is False:
        reason = row.get('reason')
        code = 'authentication' if type(reason) is str and reason.startswith('forge-auth-rejected') else 'mismatch'
        return AttestationObservation(State.FAIL, code, False)
    if row.get('verified') is not True:
        return AttestationObservation(State.WARN, 'unavailable', False)
    for name, expected in (('model_id', expected_model), ('revision', expected_revision)):
        if name in row and row[name] != expected:
            return AttestationObservation(State.FAIL, 'mismatch', False)
    if row.get('device_kind') == 'cpu':
        return AttestationObservation(State.FAIL, 'device', False)
    if row.get('measurement_verified') is False:
        return AttestationObservation(State.FAIL, 'measurement', False)
    stamp = row.get('checked_at')
    try:
        if type(stamp) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', stamp):
            raise ValueError()
        age = now - datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
        if age < 0:
            raise ValueError()
    except (ValueError, OverflowError):
        return AttestationObservation(State.WARN, 'freshness-unknown', False)
    if age > maximum_age:
        return AttestationObservation(State.WARN, 'stale', False, age)
    if (row.get('model_id') != expected_model or row.get('revision') != expected_revision
            or row.get('measurement_verified') is not True or row.get('device_kind') != 'gpu'):
        return AttestationObservation(State.WARN, 'unmeasured', False, age)
    return AttestationObservation(State.OK, 'matched', True, age)


TAILNET_FIX = ('Allow outbound UDP between the two machines so Tailscale can use a direct path; '
               'relayed links add tens of milliseconds to every search.')


@dataclass(frozen=True)
class TailnetPath:
    state: State
    path: str
    fix: str | None


@bounded('invalid-observation')
def tailnet_path(status, enrolled_peer_key):
    """L3 passive CurAddr/Relay projection for one already enrolled peer.

    The role adapter resolves the stored peer identity to the exact status key;
    this reader never scans the tailnet, sends a ping, or prints an address.
    Relay names can remain populated on direct connections: CurAddr wins.
    """
    if type(status) is not dict or type(enrolled_peer_key) is not str or len(enrolled_peer_key) > 256:
        raise DiagError('invalid-observation')
    peers = status.get('Peer')
    peer = peers.get(enrolled_peer_key) if type(peers) is dict else None
    if type(peer) is not dict:
        return TailnetPath(State.WARN, 'unknown', TAILNET_FIX)
    current, relay = peer.get('CurAddr'), peer.get('Relay')
    if any(value is not None and (type(value) is not str or len(value) > 512) for value in (current, relay)):
        raise DiagError('invalid-observation')
    if current:
        # No address is retained: the current path's presence is the observation.
        return TailnetPath(State.OK, 'direct', None)
    if relay:
        return TailnetPath(State.WARN, 'relayed', TAILNET_FIX)
    return TailnetPath(State.WARN, 'unknown', TAILNET_FIX)
