"""Pure OpenBSD Workstation diagnostic policy, without a host controller.

Owners supply bounded observations from the *running* services.  These readers
never create an engine, source settings, construct an attestor, reconcile an
import or read a credential.  Raw responses and mismatching identities remain
private; only report.row's typed allow-list can publish selected values.
"""

from dataclasses import dataclass
import json
import re

from .errors import DiagError, bounded
from . import mcp
from .models import State, observe_attestation
from .report import Reason, row
from .runner import strict_json
from .trust import InstallReceipt


@dataclass(frozen=True, repr=False)
class EngineObservation:
    status: mcp.StatusObservation
    n_docs: int | None
    n_chunks: int | None
    structure_documents: int
    structure_sections: int
    dense_error: bool
    reranker_error: bool

    def __repr__(self):
        return 'EngineObservation(<private>)'


@dataclass(frozen=True, repr=False)
class GatewayObservation:
    status: mcp.StatusObservation

    def __repr__(self):
        return 'GatewayObservation(<private>)'


@dataclass(frozen=True, repr=False)
class ContentObservation:
    """Already committed registry/pointer evidence; not release authenticity."""
    generation: str
    manifest_sha256: str
    fingerprint: str | None

    def __repr__(self):
        return 'ContentObservation(<private>)'


@dataclass(frozen=True, repr=False)
class ContentExpectation:
    """Independent signed-content inputs, validated by the importer owner.

    The J05 receipt has no count/manifest/profile fields.  A later owner must
    supply these authenticated inputs separately; observed counts are never
    recycled into this expectation.  None fingerprint denotes a legacy receipt.
    """
    manifest_sha256: str
    n_docs: int
    n_chunks: int
    release_compatible: bool

    def __repr__(self):
        return 'ContentExpectation(<authenticated public metadata>)'


@dataclass(frozen=True, repr=False)
class ForgeAuthObservation:
    """Qualified deployed forwarder evidence for both enrolled model doors."""
    endpoints_match: bool
    tls_valid: bool
    credential_valid: bool
    embed_http_status: int
    rerank_http_status: int

    def __repr__(self):
        return 'ForgeAuthObservation(<private>)'


@dataclass(frozen=True, repr=False)
class AttestorObservation:
    """Frozen projection of the existing running forwarder's snapshot."""
    models: tuple
    authenticated: bool

    def __repr__(self):
        return 'AttestorObservation(<private>)'


def _integer(value, *, nullable=False):
    if nullable and value is None:
        return value
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise DiagError('invalid-observation')
    return value


def _fingerprint(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise DiagError('invalid-observation')
    return value


def _generation(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9._+-]{1,128}', value) is None:
        raise DiagError('invalid-observation')
    return value


@bounded('invalid-observation')
def project_engine_status(raw):
    """Reuse J05's MCP status validator over the engine's existing JSON shape.

    Unknown response fields (settings, paths, error text) are discarded.  No
    cached checked_at value is used as time evidence.  Missing probe counts or
    error-kind fields cannot silently become successful observations.
    """
    value = strict_json(raw)
    if type(value) is not dict:
        raise DiagError('invalid-observation')
    # mcp._status is the canonical existing pure backend/generation validator.
    status = mcp._status({'content': [{'type': 'text', 'text': raw.decode('utf-8')}]})
    backends = value['backends']
    errors = []
    for name in ('dense', 'reranker'):
        backend = backends[name]
        if 'last_error_kind' not in backend:
            raise DiagError('invalid-observation')
        error = backend['last_error_kind']
        if error is not None and (type(error) is not str or len(error) > 256):
            raise DiagError('invalid-observation')
        errors.append(error is not None)
    structure = backends['structure']
    if any(name not in value for name in ('n_docs', 'n_chunks')):
        raise DiagError('invalid-observation')
    return EngineObservation(
        status, _integer(value['n_docs'], nullable=True), _integer(value['n_chunks'], nullable=True),
        _integer(structure.get('documents')), _integer(structure.get('sections')), *errors)


@bounded('invalid-observation')
def project_gateway_status(status):
    """Accept only the J05 MCP client's initialise/tools/status observation.

    The owning local adapter calls MCPClient.observe(), never public /health or
    status() alone.  It must also establish rcctl check d32edge independently.
    """
    if type(status) is not mcp.StatusObservation:
        raise DiagError('invalid-observation')
    # Revalidate its fields even when a synthetic caller builds the dataclass.
    backends = {}
    for name in ('bm25', 'dense', 'reranker', 'kb', 'structure'):
        backend = getattr(status, name)
        if type(backend) is not mcp.BackendStatus:
            raise DiagError('invalid-observation')
        backends[name] = {'enabled': backend.enabled, 'ok': backend.ok, 'breaker': backend.breaker}
    value = {'ok': status.ok, 'generation': status.generation,
             'corpus_fingerprint': status.corpus_fingerprint, 'backends': backends,
             'degraded': list(status.degraded)}
    validated = mcp._status({'content': [{'type': 'text', 'text': json.dumps(value)}]})
    return GatewayObservation(validated)


@bounded('invalid-observation')
def project_import_state(raw, pointer, *, registry_validator=None):
    """Project existing importer data through its owner's pure validator.

    The explicit callable is imp_journal._validate_registry, packaged by the
    owning integration job.  Registry.load is deliberately not called: a
    missing registry otherwise becomes an empty default.  Pointer is obtained
    from an existing, bounded, root-confined read using read_pointer semantics.
    No filesystem read is performed here, including a generation-directory scan.
    """
    if not callable(registry_validator):
        raise DiagError('unavailable')
    value = strict_json(raw)
    if type(value) is not dict:
        raise DiagError('invalid-observation')
    registry_validator(value, 'workstation')
    active = value.get('active')
    if (type(active) is not dict or type(active.get('pointers')) is not dict
            or type(pointer) is not str or len(pointer) > 511):
        raise DiagError('invalid-observation')
    # imp_manifest.desired_units owns the exact .kb-GENERATION/GENERATION shape.
    match = re.fullmatch(r'\.kb-([A-Za-z0-9._+-]{1,128})/([A-Za-z0-9._+-]{1,128})', pointer)
    if not match or match[1] != match[2] or active['pointers'].get('kb') != pointer:
        raise DiagError('invalid-observation')
    generation = _generation(match[1])
    records = [record for record in value['generations'] if record.get('target') == '.kb-' + generation]
    if len(records) != 1 or records[0].get('unit_kind') != 'kb':
        raise DiagError('invalid-observation')
    record = records[0]
    if (record.get('generation') != generation
            or record.get('units') != [{'id': 'kb', 'generation': generation, 'release': None}]):
        raise DiagError('invalid-observation')
    fingerprint = record.get('fingerprint')
    if fingerprint is not None:
        _fingerprint(fingerprint)
    return ContentObservation(generation, _fingerprint(active.get('manifest_sha256')), fingerprint)


@bounded('invalid-observation')
def project_forge_auth(*, endpoints_match, tls_valid, credential_valid,
                       embed_http_status, rerank_http_status):
    """Qualified owner's booleans, not receipt self-claims or exposed URLs."""
    if any(type(value) is not bool for value in (endpoints_match, tls_valid, credential_valid)):
        raise DiagError('invalid-observation')
    if any(type(value) is not int or not 100 <= value <= 599
           for value in (embed_http_status, rerank_http_status)):
        raise DiagError('invalid-observation')
    return ForgeAuthObservation(endpoints_match, tls_valid, credential_valid,
                                embed_http_status, rerank_http_status)


@bounded('invalid-observation')
def project_attestor(snapshot, expected_models, *, now, maximum_age):
    """Reuse J05 measured/fresh identity policy without constructing Attestor.

    Only an immutable per-model projection survives.  The deployed legacy
    snapshot lacks measured identity/device fields and consequently stays WARN.
    """
    if type(snapshot) is not dict or not isinstance(expected_models, (tuple, list)) or len(expected_models) > 64:
        raise DiagError('invalid-observation')
    models, aliases = [], set()
    authenticated = snapshot.get('gpu_ext') == 'on'
    for expected in expected_models:
        alias = expected['alias']
        kind = {'embeddings': 'embed', 'reranker': 'rerank'}.get(alias)
        if kind is None or alias in aliases:
            raise DiagError('invalid-observation')
        aliases.add(alias)
        measured = snapshot.get(kind)
        authenticated = authenticated and type(measured) is dict and measured.get('verified') is True
        observation = observe_attestation(snapshot, kind, expected_model=expected['id'],
                                           expected_revision=expected['revision'], now=now,
                                           maximum_age=maximum_age)
        # Preserve explicit measurement rejection as FAIL, not legacy absence.
        if type(measured) is dict and measured.get('measurement_verified') is False:
            from .models import AttestationObservation
            observation = AttestationObservation(State.FAIL, 'mismatch', False)
        models.append((alias, expected['id'], expected['revision'], observation))
    return AttestorObservation(tuple(models), authenticated and aliases == {'embeddings', 'reranker'})


def _check(check_id, state, values=None, *, blocked_by=(), public=frozenset()):
    reason = (Reason.PASSED if state is State.OK else Reason.BLOCKED if blocked_by
              else Reason.UNAVAILABLE)
    return row(check_id, state, reason, values, blocked_by=blocked_by,
               public_identities=public, policy='workstation.' + check_id.split('.')[0])


def _blocked(check_id, prerequisite):
    return _check(check_id, State.SKIP, blocked_by=(prerequisite,))


@bounded('invalid-observation')
def workstation_checks(receipt, *, engine=None, gateway=None, content=None,
                       expectation=None, auth=None, attestation=None,
                       engine_service=None, gateway_service=None, pairing_failed=False):
    """Role rows only; common pairing/publication/peer/time/storage stay shared.

    Missing qualified engine/gateway/process evidence is FAIL.  Expected counts
    and import compatibility must come from independently authenticated content
    metadata.  The supported receipt currently has no optional dense profile,
    so disabling dense/reranking always warns rather than silently skipping it.
    If pairing_failed is true the caller includes its `pairing FAIL` common row.
    """
    if (type(receipt) is not InstallReceipt or (receipt.role, receipt.platform) != ('workstation', 'openbsd')
            or any(type(value) is not bool and value is not None for value in (engine_service, gateway_service))
            or type(pairing_failed) is not bool):
        raise DiagError('invalid-observation')
    for value, expected_type in ((engine, EngineObservation), (gateway, GatewayObservation),
                                 (content, ContentObservation), (expectation, ContentExpectation),
                                 (auth, ForgeAuthObservation), (attestation, AttestorObservation)):
        if value is not None and type(value) is not expected_type:
            raise DiagError('invalid-observation')
    if expectation is not None:
        _fingerprint(expectation.manifest_sha256)
        _integer(expectation.n_docs)
        _integer(expectation.n_chunks)
        if type(expectation.release_compatible) is not bool:
            raise DiagError('invalid-observation')
    public = frozenset(value for value in (receipt.generation, receipt.fingerprint) if value is not None)
    engine_ok = engine_service is True and engine is not None
    gateway_ok = gateway_service is True and gateway is not None
    gateway_degraded = gateway_ok and bool(gateway.status.degraded)
    rows = [_check('engine', State.OK if engine_ok else State.FAIL,
                   {'present': engine_service is True, 'healthy': engine_ok}),
            _check('gateway', State.WARN if gateway_degraded else State.OK if gateway_ok else State.FAIL,
                   {'present': gateway_service is True, 'healthy': gateway_ok and not gateway_degraded})]
    if not engine_ok:
        rows.extend(_blocked(name, 'engine') for name in ('index-generation', 'index-fingerprint'))
    else:
        expected_generation = receipt.generation
        generation_match = bool(expected_generation is not None and content is not None
                                and expectation is not None and expectation.release_compatible
                                and content.manifest_sha256 == expectation.manifest_sha256
                                and engine.status.generation == content.generation == expected_generation
                                and (not gateway_ok or gateway.status.generation == expected_generation))
        values = {'matched': generation_match}
        if expected_generation is not None:
            values['expected_generation'] = expected_generation
        if generation_match:
            values['generation'] = expected_generation
        rows.append(_check('index-generation', State.OK if generation_match else State.FAIL,
                           values, public=public))
        expected_fingerprint = receipt.fingerprint
        if expected_fingerprint is None:
            rows.append(_check('index-fingerprint', State.WARN, {'matched': False}))
        else:
            fingerprint_match = bool(content is not None and expectation is not None
                                     and content.manifest_sha256 == expectation.manifest_sha256
                                     and expectation.release_compatible
                                     and engine.status.corpus_fingerprint == expected_fingerprint
                                     and (content.fingerprint is None or content.fingerprint == expected_fingerprint)
                                     and (not gateway_ok or gateway.status.corpus_fingerprint == expected_fingerprint))
            values = {'matched': fingerprint_match, 'expected_fingerprint': expected_fingerprint}
            if fingerprint_match:
                values['fingerprint'] = expected_fingerprint
            rows.append(_check('index-fingerprint', State.OK if fingerprint_match else State.FAIL,
                               values, public=public))
    if pairing_failed:
        rows.append(_blocked('forge-auth', 'pairing'))
    else:
        auth_ok = bool(auth is not None and attestation is not None and attestation.authenticated
                       and auth.endpoints_match and auth.tls_valid and auth.credential_valid
                       and auth.embed_http_status == auth.rerank_http_status == 200)
        values = {'matched': auth_ok}
        if auth is not None and auth.embed_http_status == auth.rerank_http_status:
            values['http_status'] = auth.embed_http_status
        rows.append(_check('forge-auth', State.OK if auth_ok else State.FAIL, values))
    measured = {item[0]: item for item in attestation.models} if attestation is not None else {}
    models = receipt.inventory['models']
    aliases = {model['alias'] for model in models}
    # Both default search models are required; missing inventory cannot empty a
    # check family or manufacture an installed expectation.
    for missing in sorted({'embeddings', 'reranker'} - aliases):
        rows.append(_check('model-attestation.' + missing, State.WARN, {'matched': False}))
    for model in sorted(models, key=lambda item: item['alias']):
        alias, model_id, revision = model['alias'], model['id'], model['revision']
        if alias not in ('embeddings', 'reranker'):
            raise DiagError('invalid-observation')
        check_id = 'model-attestation.' + alias
        item = measured.get(alias)
        if item is None:
            rows.append(_check(check_id, State.WARN, {'matched': False}))
            continue
        if item[1:3] != (model_id, revision):
            rows.append(_check(check_id, State.FAIL, {'matched': False}))
            continue
        observation = item[3]
        values = {'model': model_id, 'expected_revision': revision, 'matched': observation.matched}
        if observation.state is State.OK:
            values['revision'] = revision
        if observation.age_seconds is not None:
            values['age_seconds'] = observation.age_seconds
        rows.append(_check(check_id, observation.state, values, public=frozenset((model_id, revision))))
    if not engine_ok:
        rows.extend(_blocked(name, 'engine') for name in ('dense', 'reranker', 'index-health'))
        return tuple(rows)
    for name in ('dense', 'reranker'):
        backend = getattr(engine.status, name)
        error = getattr(engine, name + '_error')
        edge = getattr(gateway.status, name) if gateway_ok else None
        healthy = bool(backend.enabled and backend.ok is True and backend.breaker == 'closed'
                       and not error and name not in engine.status.degraded
                       and (edge is None or edge.enabled and edge.ok is True
                            and edge.breaker == 'closed' and name not in gateway.status.degraded))
        breaker = backend.breaker
        if breaker == 'half_open':
            breaker = 'half-open'
        rows.append(_check(name, State.OK if healthy else State.WARN,
                           {'enabled': backend.enabled, 'healthy': healthy, 'breaker': breaker}))
    required = ('bm25', 'kb', 'structure')
    healthy = bool(expectation is not None and expectation.release_compatible
                   and expectation.n_docs > 0 and expectation.n_chunks > 0
                   and engine.n_docs == expectation.n_docs and engine.n_chunks == expectation.n_chunks
                   and engine.structure_documents > 0 and engine.structure_sections > 0
                   and engine.structure_documents <= engine.n_docs
                   and all(getattr(engine.status, name).ok is True and name not in engine.status.degraded
                           for name in required)
                   and (not gateway_ok or all(getattr(gateway.status, name).ok is True
                                             and name not in gateway.status.degraded for name in required)))
    values = {'healthy': healthy}
    for name in ('n_docs', 'n_chunks'):
        count = getattr(engine, name)
        if count is not None:
            values[name] = count
    rows.append(_check('index-health', State.OK if healthy else State.FAIL, values))
    return tuple(rows)
