"""Pure Forge policy over qualified, bounded, already observed metadata.

No lifecycle owner is imported, and there are no process, socket, filesystem or
Docker calls here. The installer supplies a validated InstallReceipt and runtime
pins from the same authenticated release. Native observation qualification and
actual installed-role receipts remain owner inputs, never inferred from mocks.
"""

from dataclasses import dataclass, field
import math
import re

from .errors import DiagError, bounded
from .models import State, observe_attestation
from .report import Reason, row
from .trust import InstallReceipt

SERVICES = frozenset(('embeddings', 'reranker', 'door', 'converter', 'builder',
                      'qdrant', 'full', 'model-bridge'))
OPTIONAL = ('converter', 'builder', 'qdrant')
DESKTOP_PIPE = 'npipe:////./pipe/dockerDesktopLinuxEngine'
LINUX_SOCKET = 'unix:///var/run/docker.sock'
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
ALIAS = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')


@dataclass(frozen=True, repr=False)
class HostRules:
    """Only the reviewed owner's pure version and discovered-CDI functions.

    Supply version/nvidia_cdi from forms/host-requirements.py. That module has
    guarded main and no import-time host calls. Diagnostics deliberately does
    not import its check(), command(), bind probe or lifecycle dependencies.
    The owner packages these pure functions at the installed seam.
    """
    version: object = field(repr=False)
    nvidia_cdi: object = field(repr=False)
    minimum_docker: str = '19.03.0'
    minimum_compose: str = '2.30.0'


@dataclass(frozen=True, repr=False)
class RuntimePolicy:
    # Every applicable selected-runtime floor is required, including Windows'
    # driver-build floor. Never substitute a different GPU class or add-on.
    driver_floors: tuple = field(default=(), repr=False)
    docker_minimum: str | None = None
    compose_minimum: str | None = None
    selected_gpu_count: int | None = None
    startup_grace: object = field(default_factory=dict, repr=False)
    docker_health_required: frozenset = frozenset()
    model_services: object = field(default_factory=dict, repr=False)


@dataclass(frozen=True, repr=False)
class ContainerObservation:
    state: str
    image: str = field(repr=False)
    owner_verified: bool = False
    health: str | None = None
    application_healthy: bool | None = None
    authenticated: bool | None = None
    age_seconds: float | None = None


@dataclass(frozen=True, repr=False)
class ModelObservation:
    # The owner must obtain this from the authenticated service, not Docker
    # gpus=all, host GPU enumeration or image labels.
    attestation: object = field(default_factory=dict, repr=False)
    device_supported: bool = False
    device_kind: str | None = None


@dataclass(frozen=True, repr=False)
class ListenerObservation:
    present: bool
    owner_verified: bool = False
    loopback_only: bool = False
    port: int | None = None
    pairing_helper_verified: bool = False


@dataclass(frozen=True, repr=False)
class JobObservation:
    hold_present: bool = False
    recovery_present: bool = False
    journal_valid: bool = False
    active: bool = False
    intentional_pause: bool = False
    inventory_coherent: bool = False
    active_services: tuple = ()


@dataclass(frozen=True, repr=False)
class ForgeObservation:
    # A compiled adapter grants qualification per operation. These strings
    # must never be deserialised from an installation or status receipt.
    qualified: frozenset = frozenset()
    drivers: tuple | None = field(default=None, repr=False)
    engine: object = field(default=None, repr=False)
    compose_version: str | None = None
    containers: object = field(default_factory=dict, repr=False)
    models: object = field(default_factory=dict, repr=False)
    listeners: object = field(default_factory=dict, repr=False)
    job: JobObservation | None = None


@bounded('invalid-receipt')
def runtime_policy_from_pins(receipt, runtime, *, selected_gpu_count,
                             model_services, startup_grace=None,
                             docker_health_required=frozenset(('door',))):
    """Select only applicable existing floor pins from authenticated runtime.

    Core-only installs omit MinerU's floor. Windows always adds its native
    driver-build pin. T-0200's future class-specific floor map must be supplied
    explicitly when its frozen inventory exists; absence never selects a class.
    """
    if (type(receipt) is not InstallReceipt or receipt.role != 'forge'
            or receipt.platform not in ('linux', 'windows') or type(runtime) is not dict):
        raise DiagError('invalid-receipt')
    names = ['tei_minimum_driver', 'cuda_minimum_driver']
    if any(item['alias'] == 'converter' for item in receipt.inventory['services']):
        names.append('mineru_minimum_driver')
    if receipt.platform == 'windows':
        names.append('windows_cuda13_driver_build')
    return RuntimePolicy(tuple(runtime.get(name) for name in names),
        runtime.get('docker_minimum_version'), runtime.get('compose_minimum_version'),
        selected_gpu_count, {} if startup_grace is None else dict(startup_grace),
        frozenset(docker_health_required), dict(model_services))


def _emit(check, state, values=None, *, public=frozenset(), blocked_by=()):
    reason = (Reason.PASSED if state is State.OK else Reason.BLOCKED if blocked_by
              else Reason.INAPPLICABLE if state is State.SKIP else Reason.UNAVAILABLE)
    return row(check, state, reason, values, blocked_by=blocked_by,
               public_identities=public, policy='forge.' + check.split('.')[0])


def _unsupported(check, values=None):
    return row(check, State.WARN, Reason.UNSUPPORTED, values,
               policy='forge.' + check.split('.')[0])


def _version(rules, value):
    if type(rules) is not HostRules or not callable(rules.version):
        raise ValueError()
    parsed = rules.version(value)
    if (type(parsed) is not tuple or len(parsed) != 4
            or any(type(x) is not int or x < 0 for x in parsed)):
        raise ValueError()
    # Canonical numeric projection strips distribution suffixes and prevents
    # arbitrary command output from reaching the typed report.
    return parsed, '.'.join(str(x) for x in parsed)


@bounded('invalid-observation')
def driver_row(policy, observation, rules):
    if type(policy) is not RuntimePolicy or type(observation) is not ForgeObservation:
        raise DiagError('invalid-observation')
    try:
        if not policy.driver_floors:
            raise ValueError()
        floor, printable = max((_version(rules, x) for x in policy.driver_floors), key=lambda x: x[0])
    except (ValueError, TypeError):
        # Missing selected-runtime pins are unsafe metadata, not a lower floor.
        return _emit('driver', State.FAIL)
    values = {'minimum_version': printable}
    if 'driver' not in observation.qualified:
        return _unsupported('driver', values)
    try:
        if (type(observation.drivers) is not tuple or not observation.drivers
                or type(policy.selected_gpu_count) is not int or policy.selected_gpu_count < 1
                or len(observation.drivers) != policy.selected_gpu_count):
            raise ValueError()
        actual = [_version(rules, x) for x in observation.drivers]
        minimum = min(actual, key=lambda x: x[0])
        values['version'] = minimum[1]
        return _emit('driver', State.OK if minimum[0] >= floor else State.FAIL, values)
    except (ValueError, TypeError):
        return _emit('driver', State.FAIL, values)


@bounded('invalid-observation')
def engine_rows(policy, observation, rules, *, platform):
    if platform not in ('linux', 'windows'):
        raise DiagError('invalid-observation')
    rows = []
    engine_ok = False
    engine = observation.engine
    values = {'present': type(engine) is dict, 'matched': False}
    try:
        minimum = _version(rules, policy.docker_minimum)[0]
        if minimum < _version(rules, rules.minimum_docker)[0]:
            raise ValueError()
        if 'engine' not in observation.qualified:
            rows.append(_unsupported('docker-engine', values))
        else:
            if type(engine) is not dict:
                raise ValueError()
            actual, printable = _version(rules, engine.get('Version'))
            values['version'] = printable
            endpoint = DESKTOP_PIPE if platform == 'windows' else LINUX_SOCKET
            engine_ok = (engine.get('endpoint') == endpoint and engine.get('OSType') == 'linux'
                         and actual >= minimum)
            if platform == 'windows':
                kernel = engine.get('KernelVersion')
                engine_ok = (engine_ok and engine.get('OperatingSystem') == 'Docker Desktop'
                             and type(kernel) is str and 'microsoft' in kernel.lower()
                             and 'wsl2' in kernel.lower())
            values['matched'] = engine_ok
            rows.append(_emit('docker-engine', State.OK if engine_ok else State.FAIL, values))
    except (ValueError, TypeError):
        rows.append(_emit('docker-engine', State.FAIL, values))
    try:
        minimum, printable = _version(rules, policy.compose_minimum)
        if minimum < _version(rules, rules.minimum_compose)[0]:
            raise ValueError()
        values = {'minimum_version': printable}
        if 'compose' not in observation.qualified:
            rows.append(_unsupported('docker-compose', values))
        else:
            actual, printable = _version(rules, observation.compose_version)
            values['version'] = printable
            rows.append(_emit('docker-compose', State.OK if actual >= minimum else State.FAIL, values))
    except (ValueError, TypeError):
        rows.append(_emit('docker-compose', State.FAIL))
    if rows[0].state is State.FAIL:
        rows.append(_emit('gpu-runtime', State.SKIP, blocked_by=('docker-engine',)))
    elif not engine_ok or 'runtime' not in observation.qualified:
        rows.append(_unsupported('gpu-runtime'))
    else:
        runtimes = engine.get('Runtimes')
        configured = (type(runtimes) is dict and 'nvidia' in runtimes)
        if not configured and type(rules) is HostRules and callable(rules.nvidia_cdi):
            configured = rules.nvidia_cdi(engine) is True
        rows.append(_emit('gpu-runtime', State.OK if configured else State.FAIL, {'present': configured}))
    return tuple(rows)


def _containers(receipt):
    rows = receipt.inventory['services']
    if any(item['alias'] not in SERVICES for item in rows):
        raise DiagError('invalid-receipt')
    return {item['alias']: item for item in rows}


def _job_coherent(job, expected_active):
    return (type(job) is JobObservation and job.journal_valid is True
            and job.active is True and job.inventory_coherent is True
            and type(job.active_services) is tuple and bool(expected_active)
            and len(job.active_services) == len(set(job.active_services))
            and frozenset(job.active_services) == expected_active)


@bounded('invalid-observation')
def container_rows(receipt, policy, observation):
    selected = _containers(receipt)
    result = []
    for alias in sorted(set(selected) | set(OPTIONAL)):
        expected = selected.get(alias)
        current = observation.containers.get(alias)
        required = expected is not None and (expected['required'] or expected['active_job'])
        check, values = 'container.' + alias, {'service': alias}
        if not required:
            # A recorded inactive profile may have a stopped retained container;
            # an unexpected running job is a conflict, not an optional skip.
            active = type(current) is ContainerObservation and current.state in ('running', 'restarting', 'starting', 'paused')
            result.append(_emit(check, State.FAIL if active else State.SKIP, values))
            continue
        if 'containers' not in observation.qualified:
            result.append(_unsupported(check, values))
            continue
        if type(current) is not ContainerObservation:
            result.append(_emit(check, State.FAIL, values))
            continue
        if current.state not in ('running', 'stopped', 'restarting', 'starting', 'exited', 'dead', 'created', 'paused'):
            result.append(_emit(check, State.FAIL, values))
            continue
        matched = current.image == expected['image'] and current.owner_verified is True
        values.update(state=current.state, matched=matched)
        health_required = alias in policy.docker_health_required
        healthy = current.health == 'healthy' if health_required else current.health in (None, 'healthy')
        values['healthy'] = healthy
        model_service = alias in policy.model_services.values()
        application_ok = (not model_service or
                          current.application_healthy is True and current.authenticated is True)
        grace = policy.startup_grace.get(alias)
        in_grace = (type(grace) in (int, float) and math.isfinite(grace) and grace > 0
                    and type(current.age_seconds) in (int, float) and math.isfinite(current.age_seconds)
                    and 0 <= current.age_seconds <= grace)
        job = observation.job
        expected_active = frozenset(name for name, item in selected.items() if item['active_job'])
        paused_for_job = (_job_coherent(job, expected_active) and 'converter' in expected_active
                          and job.intentional_pause is True and job.recovery_present is True
                          and model_service and current.state in ('stopped', 'exited', 'paused'))
        if matched and paused_for_job:
            state = State.WARN
        elif matched and in_grace and (current.state == 'starting' or current.health == 'starting'):
            state = State.WARN
        else:
            state = State.OK if matched and current.state == 'running' and healthy and application_ok else State.FAIL
        result.append(_emit(check, state, values))
    return tuple(result)


@bounded('invalid-observation')
def model_rows(receipt, policy, observation, containers, *, now, maximum_age=300):
    by_id = {item.id: item for item in containers}
    result = []
    for expected in receipt.inventory['models']:
        alias = expected['alias']
        if not ALIAS.fullmatch(alias):
            raise DiagError('invalid-receipt')
        service = policy.model_services.get(alias)
        if service not in SERVICES:
            # No association is invented from an arbitrary model alias.
            result += [_emit('gpu-visible.' + alias, State.FAIL), _emit('model.' + alias, State.FAIL)]
            continue
        parent = by_id.get('container.' + service)
        current = observation.models.get(alias)
        if parent is not None and parent.state is State.FAIL:
            result += [_emit('gpu-visible.' + alias, State.SKIP, blocked_by=(parent.id,)),
                       _emit('model.' + alias, State.SKIP, blocked_by=(parent.id,))]
            continue
        if parent is not None and parent.state is State.WARN:
            result += [_unsupported('gpu-visible.' + alias), _unsupported('model.' + alias)]
            continue
        if 'models' not in observation.qualified or type(current) is not ModelObservation:
            result += [_unsupported('gpu-visible.' + alias), _unsupported('model.' + alias)]
            continue
        device = current.device_kind if current.device_kind in ('gpu', 'cpu') else 'unknown'
        state = (State.FAIL if device == 'cpu' else
                 State.OK if device == 'gpu' and current.device_supported is True else
                 State.FAIL if current.device_supported is True else State.WARN)
        result.append(_emit('gpu-visible.' + alias, state, {'device_kind': device}))
        kind = 'embed' if service == 'embeddings' else 'rerank' if service == 'reranker' else None
        if kind is None:
            result.append(_emit('model.' + alias, State.FAIL))
            continue
        attested = observe_attestation(current.attestation, kind,
            expected_model=expected['id'], expected_revision=expected['revision'],
            now=now, maximum_age=maximum_age)
        values = {'model': expected['id'], 'expected_revision': expected['revision'], 'matched': attested.matched}
        if attested.age_seconds is not None:
            values['age_seconds'] = attested.age_seconds
        # Only signed expectation values are printable; response mismatches are
        # represented by matched=false, never copied into public identities.
        if attested.matched:
            values['revision'] = expected['revision']
        result.append(_emit('model.' + alias, attested.state, values,
                            public=frozenset((expected['id'], expected['revision']))))
    return tuple(result)


@bounded('invalid-observation')
def port_rows(receipt, observation, *, pairing_aliases=frozenset(('pairing', 'pair'))):
    result = []
    for expected in receipt.inventory['ports']:
        alias, port = expected['alias'], expected['port']
        check, values = 'port.' + alias, {'port': port}
        if 'listeners' not in observation.qualified:
            result.append(_unsupported(check, values))
            continue
        current = observation.listeners.get(alias)
        if type(current) is not ListenerObservation or type(current.present) is not bool:
            result.append(_emit(check, State.FAIL, values))
            continue
        pairing = alias in pairing_aliases
        owner = (current.pairing_helper_verified is True if pairing else current.owner_verified is True)
        valid = (not current.present and pairing or current.present and owner
                 and current.loopback_only is True and current.port == port)
        values.update(present=current.present, owner_verified=owner if current.present else False)
        result.append(_emit(check, State.OK if valid else State.FAIL, values))
    return tuple(result)


@bounded('invalid-observation')
def hold_row(observation, *, expected_active=frozenset()):
    if 'jobs' not in observation.qualified:
        return _unsupported('gpu-hold')
    current = observation.job
    if type(current) is not JobObservation:
        return _emit('gpu-hold', State.FAIL)
    present = current.hold_present is True or current.recovery_present is True
    coherent = _job_coherent(current, expected_active)
    if not present and not current.active and not expected_active:
        state, status = State.OK, 'ready'
    elif not coherent:
        state, status = State.FAIL, 'recovery'
    elif current.intentional_pause is True:
        state, status = State.WARN, 'active'
    else:
        state, status = State.OK, 'active'
    return _emit('gpu-hold', state, {'present': present, 'state': status})


@bounded('invalid-observation')
def windows_pairing_row(platform, *, protected_adapter_qualified=False):
    if platform not in ('linux', 'windows') or type(protected_adapter_qualified) is not bool:
        raise DiagError('invalid-observation')
    return _emit('windows-pairing', State.SKIP if platform == 'linux' else
                 State.OK if protected_adapter_qualified else State.FAIL,
                 {'present': protected_adapter_qualified if platform == 'windows' else False})


@bounded('invalid-observation')
def forge_rows(receipt, policy, observation, rules, *, now,
               protected_windows_pairing_qualified=False):
    """J07 role policy only; common peers/publication/storage come from J06/J07 seams.

    Receipt is the output of validate_receipt(), never unverified JSON. Pin
    policy/qualification are compiled trusted owner inputs. Missing inventory
    or pure owner rules produces installation FAIL; no alternate class is used.
    """
    if (type(receipt) is not InstallReceipt or receipt.role != 'forge'
            or receipt.platform not in ('linux', 'windows') or type(policy) is not RuntimePolicy
            or type(observation) is not ForgeObservation or type(rules) is not HostRules):
        return (row('installation', State.FAIL, Reason.UNAVAILABLE, policy='forge.installation'),)
    selected = _containers(receipt)
    core = {'embeddings', 'reranker', 'door'}
    models = receipt.inventory['models']
    ports = receipt.inventory['ports']
    if (not core <= set(selected) or any(selected[name]['required'] is not True for name in core)
            or not {'embeddings', 'reranker'} <= {policy.model_services.get(item['alias']) for item in models}
            or not {'embeddings', 'reranker'} <= {item['alias'] for item in ports}):
        return (row('installation', State.FAIL, Reason.UNAVAILABLE, policy='forge.installation'),)
    result = [driver_row(policy, observation, rules)]
    result.extend(engine_rows(policy, observation, rules, platform=receipt.platform))
    containers = container_rows(receipt, policy, observation)
    result.extend(containers)
    result.extend(model_rows(receipt, policy, observation, containers, now=now))
    result.extend(port_rows(receipt, observation))
    result.append(hold_row(observation, expected_active=frozenset(
        name for name, item in selected.items() if item['active_job'])))
    result.append(windows_pairing_row(receipt.platform,
                  protected_adapter_qualified=protected_windows_pairing_qualified))
    return tuple(result)
