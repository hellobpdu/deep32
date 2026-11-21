"""Ordered doctor policy assembly over qualified, already obtained evidence.

There is no native controller here. Installed owners supply immutable typed
observations and exact-slot snapshots; the default never discovers state,
reads settings, starts a command, or manufactures a healthy installation.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import platform as local_platform
import socket

from . import forge, peer_time, probes, report, storage, workstation
from .errors import DiagError
from .models import State
from .report import Reason, row
from .runner import Budget, ExistingFiles, FileSnapshot
from .trust import InstallReceipt, PAIRS


@dataclass(frozen=True, repr=False)
class CommonEvidence:
    tailscale_status: object = field(default=None, repr=False)
    tailscale_installed: bool | None = None
    tailscale_version: str | None = None
    peer_resolved: bool | None = None
    peer_tls_valid: bool | None = None
    publication: bytes | None = field(default=None, repr=False)
    serve_status: object = field(default=None, repr=False)
    current_host: str | None = field(default=None, repr=False)
    pairing_records: tuple = field(default=(), repr=False)
    pairing_authority: probes.PairingAuthority | None = field(default=None, repr=False)
    peers: tuple = field(default=(), repr=False)
    clock_samples: object = field(default_factory=dict, repr=False)
    c6: bytes | None = field(default=None, repr=False)
    first_start_completed: bool = False
    lifecycle_complete: bool = False
    pairing_committed: bool = False
    c6_fresh: bool = False
    storage_budgets: tuple = field(default=(), repr=False)
    storage_readings: object = field(default_factory=dict, repr=False)


@dataclass(frozen=True, repr=False)
class WorkstationEvidence:
    engine: workstation.EngineObservation | None = None
    gateway: workstation.GatewayObservation | None = None
    content: workstation.ContentObservation | None = None
    expectation: workstation.ContentExpectation | None = None
    auth: workstation.ForgeAuthObservation | None = None
    attestation: workstation.AttestorObservation | None = None
    engine_service: bool | None = None
    gateway_service: bool | None = None


@dataclass(frozen=True, repr=False)
class ForgeEvidence:
    policy: forge.RuntimePolicy | None = None
    observation: forge.ForgeObservation = field(default_factory=forge.ForgeObservation)
    rules: forge.HostRules | None = None
    protected_windows_pairing_qualified: bool = False


@dataclass(frozen=True, repr=False)
class SnapshotSet:
    """Existing protected slots retained by the qualified observation owner."""
    files: ExistingFiles = field(repr=False)
    slots: tuple = field(repr=False)
    qualified: bool = False

    def unchanged(self, budget):
        if (type(self.files) is not ExistingFiles or type(self.slots) is not tuple
                or not 1 <= len(self.slots) <= 64 or self.qualified is not True):
            raise DiagError('invalid-observation')
        names = set()
        unchanged = True
        for entry in self.slots:
            if (type(entry) is not tuple or len(entry) != 2 or type(entry[0]) is not str
                    or entry[0] in names or type(entry[1]) is not FileSnapshot):
                raise DiagError('invalid-observation')
            names.add(entry[0])
            budget.deadline()
            try:
                same = self.files.unchanged(*entry)
            except DiagError as error:
                if error.code == 'deadline':
                    raise
                same = False
            unchanged = unchanged and same
            budget.deadline()
        return unchanged


@dataclass(frozen=True, repr=False)
class DoctorEvidence:
    """Python integration seam; never populated from CLI JSON or environment.

    Receipt must already be the authenticated validate_receipt output. Owners
    must share this run's Budget while gathering evidence, retain changed-file
    snapshots, and supply only independently qualified native observations.
    A dataclass or a successful synthetic test grants no native qualification.
    """
    receipt: InstallReceipt | None = None
    common: CommonEvidence = field(default_factory=CommonEvidence)
    workstation: WorkstationEvidence | None = None
    forge: ForgeEvidence | None = None
    headend: object = field(default=None, repr=False)
    snapshots: SnapshotSet | None = None
    data_root: str | None = field(default=None, repr=False)


def platform_name():
    return {'OpenBSD': 'openbsd', 'Linux': 'linux', 'Windows': 'windows',
            'Darwin': 'macos'}.get(local_platform.system())


def _clock_rows(role, common, peers, peer_checks, budget):
    if type(common.clock_samples) is not dict:
        raise DiagError('invalid-observation')
    result = []
    by_id = {item.id: item for item in peer_checks}
    for peer in peers:
        budget.deadline()
        reachable = by_id['peer-reachability.' + peer.alias].state is State.OK
        samples = common.clock_samples.get(peer.alias, (None, None, None))
        if type(samples) is not tuple or len(samples) != 3:
            raise DiagError('invalid-observation')
        # No callback is supplied by an input file. These are three already
        # bounded observations from the owner's existing enum-peer seam.
        result.append(peer_time.observe_clock(lambda index: samples[index],
            reachable=reachable, role=role, peer_role=peer.role, alias=peer.alias))
    return result


def _common_rows(role, common, headend_evidence, budget):
    if type(common) is not CommonEvidence:
        raise DiagError('invalid-observation')
    result = list(probes.tailscale_rows(common.tailscale_status, role=role,
        installed=common.tailscale_installed, version=common.tailscale_version))
    # DNS policy recomputes its prerequisite from the same status snapshot.
    # Missing executable remains the named dependency rather than a dangling
    # tailscale-running dependency not present in the assembled report.
    if common.tailscale_installed is False:
        result.extend(row(name, State.SKIP, Reason.BLOCKED,
            blocked_by=('tailscale-installed',), policy=role + '.' + name)
            for name in ('magicdns', 'https-available'))
    else:
        result.extend(probes.dns_https_rows(common.tailscale_status, role=role,
            resolved=common.peer_resolved, tls_valid=common.peer_tls_valid))
    if role == 'headend':
        from .headend import headend_pairing_row
        result.append(row('serve-routes', State.SKIP, Reason.INAPPLICABLE,
                          policy='headend.serve-routes'))
        result.append(headend_pairing_row(headend_evidence))
    else:
        result.append(probes.publication_row(common.publication, common.serve_status,
            role=role, current_host=common.current_host))
        result.append(probes.pairing_row(common.pairing_records, role=role,
                                        authority=common.pairing_authority))
    peers = common.peers
    if not peers:
        expected_role = 'forge' if role == 'workstation' else 'workstation'
        peers = (probes.PeerEvidence(expected_role, expected_role, ''),)
    try:
        peer_status = probes._status(common.tailscale_status)
    except DiagError:
        peer_status = None
    peer_checks = probes.peer_rows(peers, {} if peer_status is None else peer_status, role=role)
    result.extend(peer_checks)
    result.extend(_clock_rows(role, common, peers, peer_checks, budget))
    if role == 'headend':
        result.append(row('first-start', State.SKIP, Reason.INAPPLICABLE,
                          policy='headend.first-start'))
    else:
        result.append(probes.first_start_row(common.c6, role=role,
            completed=common.first_start_completed, lifecycle_complete=common.lifecycle_complete,
            pairing_committed=common.pairing_committed, fresh=common.c6_fresh))
    return result


_ORDER = ('installation', 'tailscale-installed', 'tailscale-running', 'tailscale-signin',
          'magicdns', 'https-available', 'serve-routes', 'pairing', 'peer-reachability',
          'tailnet-path', 'peer-clock', 'first-start', 'driver', 'docker-engine',
          'docker-compose', 'gpu-runtime', 'gpu-visible', 'container', 'model', 'port',
          'engine', 'gateway', 'index-generation', 'index-fingerprint', 'forge-auth',
          'model-attestation', 'dense', 'reranker', 'index-health', 'disk', 'gpu-hold',
          'windows-pairing', 'agent-profile', 'agent-connector', 'agent-installed',
          'agent-signin', 'gateway-auth', 'mcp-rag-status', 'snapshot-changed', 'doctor')
_SERVICE_ORDER = {name: index for index, name in enumerate(
    ('embeddings', 'reranker', 'door', 'converter', 'builder', 'qdrant', 'pairing'))}


def _ordered(rows):
    return sorted(rows, key=lambda item: (_ORDER.index(item.id.split('.')[0]),
        _SERVICE_ORDER.get(item.id.partition('.')[2], 99), item.id))


def _storage_rows(role, common, receipt):
    if role == 'headend':
        return ()
    expected = {item['alias']: item for item in receipt.inventory.get('filesystems', ())}
    supplied = {item.alias: item for item in common.storage_budgets
                if type(item) is storage.StorageBudget}
    matched = (bool(expected) and len(supplied) == len(common.storage_budgets)
               and set(expected) == set(supplied)
               and all(supplied[alias].running_bytes == item['safe_running_bytes']
                       and supplied[alias].next_bytes == item['required_bytes']
                       for alias, item in expected.items()))
    if not matched:
        # inventory budgets are required owning-installer inputs.
        return (row('disk.data' if role == 'forge' else 'disk.content', State.FAIL,
                    Reason.UNAVAILABLE, policy=role + '.disk'),)
    return storage.storage_rows(common.storage_budgets, common.storage_readings, role=role)


def run_doctor(role, *, evidence=None, platform=None, hostname=None, checked_at=None, budget=None,
               data_root=None, agent=None, profile_dir=None):
    """Return one sealed report, converting runtime failure and interruption.

    No installed native observation adapter ships yet. With no evidence this
    returns installation FAIL, without opening an installation or profile path.
    """
    rows, complete = [], True
    selected_platform = platform
    safe_hostname = 'local-host'
    stamp = '1970-01-01T00:00:00Z'
    report_role, report_platform = None, None
    try:
        budget = Budget() if budget is None else budget
        if type(budget) is not Budget:
            raise DiagError('invalid-observation')
        budget.deadline()
        selected_platform = platform_name() if platform is None else platform
        safe_hostname = report.sanitize_hostname(socket.gethostname() if hostname is None else hostname)
        stamp = (datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
                 if checked_at is None else checked_at)
        if (role, selected_platform) not in PAIRS:
            rows.append(row('installation', State.FAIL, Reason.UNAVAILABLE))
        else:
            report_role, report_platform = role, selected_platform
            if evidence is None:
                evidence = DoctorEvidence()
            if type(evidence) is not DoctorEvidence:
                raise DiagError('invalid-observation')
            receipt = evidence.receipt
            installed = (type(receipt) is InstallReceipt and
                         (receipt.role, receipt.platform) == (role, selected_platform))
            if data_root is not None:
                installed = installed and role == 'forge' and evidence.data_root == data_root
            if role == 'headend' and (agent is not None or profile_dir is not None):
                from .headend import HeadendEvidence
                selected = evidence.headend.profile if type(evidence.headend) is HeadendEvidence else None
                installed = (installed and selected is not None and selected.agent == agent
                    and profile_dir is not None and selected.state_path.parents[2] == Path(profile_dir))
            rows.append(row('installation', State.OK if installed else State.FAIL,
                Reason.PASSED if installed else Reason.UNAVAILABLE,
                {'present': receipt is not None, 'matched': installed}, policy=role + '.installation'))
            if installed:
                from .headend import HeadendEvidence, headend_rows
                headend_evidence = HeadendEvidence() if evidence.headend is None else evidence.headend
                budget.deadline()
                rows.extend(_common_rows(role, evidence.common, headend_evidence, budget))
                budget.deadline()
                if role == 'workstation':
                    observed = evidence.workstation or WorkstationEvidence()
                    if type(observed) is not WorkstationEvidence:
                        raise DiagError('invalid-observation')
                    options = {name: getattr(observed, name) for name in observed.__dataclass_fields__}
                    options['pairing_failed'] = any(item.id == 'pairing' and item.state is State.FAIL for item in rows)
                    rows.extend(workstation.workstation_checks(receipt, **options))
                elif role == 'forge':
                    observed = evidence.forge or ForgeEvidence()
                    if type(observed) is not ForgeEvidence:
                        raise DiagError('invalid-observation')
                    additions = forge.forge_rows(receipt, observed.policy, observed.observation, observed.rules,
                        now=datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp(),
                        protected_windows_pairing_qualified=observed.protected_windows_pairing_qualified)
                    # The role owner can refuse missing inventory/pins with its
                    # own installation FAIL. Keep a single installation row.
                    if any(item.id == 'installation' for item in additions):
                        rows = [item for item in rows if item.id != 'installation']
                    rows.extend(additions)
                else:
                    rows.extend(headend_rows(headend_evidence))
                rows.extend(_storage_rows(role, evidence.common, receipt))
                budget.deadline()
                if evidence.snapshots is None or (type(evidence.snapshots) is SnapshotSet
                        and (evidence.snapshots.qualified is not True or not evidence.snapshots.slots)):
                    rows.append(row('snapshot-changed', State.WARN, Reason.UNSUPPORTED,
                                    policy=role + '.snapshot-changed'))
                else:
                    if type(evidence.snapshots) is not SnapshotSet:
                        raise DiagError('invalid-observation')
                    if not evidence.snapshots.unchanged(budget):
                        rows.append(row('snapshot-changed', State.WARN, Reason.SNAPSHOT_CHANGED,
                                        policy=role + '.snapshot-changed'))
                budget.deadline()
        return report.build_report(report_role, report_platform, safe_hostname, stamp, _ordered(rows))
    except (Exception, KeyboardInterrupt):
        complete = False
    # Raw exceptions, including strings supplied by hostile observations, are
    # never formatted, logged, chained into output, or copied into an observed.
    if report_role is None and not any(item.id == 'installation' for item in rows):
        rows.insert(0, row('installation', State.FAIL, Reason.UNAVAILABLE))
    rows = [item for item in rows if item.id != 'doctor']
    rows.append(row('doctor', State.FAIL, Reason.UNAVAILABLE))
    try:
        return report.build_report(report_role, report_platform, safe_hostname, stamp,
                                   _ordered(rows), complete=complete)
    except Exception:
        # An invalid timestamp or malformed partial dependency cannot prevent a
        # bounded incomplete report. Retain no unvalidated partial payload.
        fallback = [row('installation', State.FAIL, Reason.UNAVAILABLE)] if report_role is None else []
        fallback.append(row('doctor', State.FAIL, Reason.UNAVAILABLE))
        return report.build_report(report_role, report_platform, safe_hostname,
            '1970-01-01T00:00:00Z', fallback, complete=False)
