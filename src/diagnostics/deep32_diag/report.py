"""Typed report assembly and the lead's fixed doctor text/JSON contract.

Only rows made by ``row`` can become output. Renderers accept the sealed result
of ``build_report``; arbitrary dictionaries and modified reports are refused.
No response body, exception or command text is a presentation input.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import re
import textwrap
from itertools import islice

from .errors import DiagError, bounded
from .models import State, family, observed
from .trust import PAIRS
from .wording import POLICIES


class Reason(Enum):
    # generic core reason sentences; role policy/renderer is J09.
    PASSED = ('required observation passed', None)
    UNAVAILABLE = ('required observation unavailable', 'Run deep32 doctor on the owning role and repair its reported services before checking this node again.')
    UNSUPPORTED = ('required diagnostic capability cannot be established', 'Install a release containing the qualified read-only diagnostic capability, then run deep32 doctor again.')
    BLOCKED = ('blocked by a failed prerequisite', 'Resolve the failed prerequisite first, then run deep32 doctor again.')
    INAPPLICABLE = ('inapplicable for this role', 'No local repair is needed; run deep32 doctor on the owning role.')
    SNAPSHOT_CHANGED = ('installation changed during observation', 'Run deep32 doctor again after the installation or import completes.')


_ROW = object()
_REPORT = object()


class _Report(dict):
    def __init__(self, value, seal):
        if seal is not _REPORT:
            raise DiagError('invalid-report')
        super().__init__(value)
        self.__proof = json.dumps(value, ensure_ascii=True, separators=(',', ':'))

    def unchanged(self):
        return json.dumps(self, ensure_ascii=True, separators=(',', ':')) == self.__proof


def sanitise_hostname(value):
    """Return only this local machine's bounded ASCII header label.

    Unrecognised/control-bearing names use a fixed alias instead of attempting
    to clean an arbitrary string into something that might expose its content.
    """
    if type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', value):
        return value
    return 'local-node'


sanitize_hostname = sanitise_hostname


def _number(value):
    return format(value, '.6g')


def _detail(item, role):
    """Fixed sentences with only independently typed, printable observations.

    mechanical substitutions for D2's illustrative observations;
    no caller-supplied prose is used here.
    """
    values = dict(item.observed_items)
    key = family(item.id)
    if item.reason is Reason.BLOCKED:
        return 'blocked by ' + ', '.join(item.blocked_by)
    if item.reason is Reason.SNAPSHOT_CHANGED:
        return item.reason.value[0]
    if item.reason is Reason.INAPPLICABLE:
        if key == 'serve-routes' and role == 'headend':
            return 'this headend owns no Deep32 Serve route'
        if key == 'first-start' and role == 'headend':
            return 'no node first-start state on a headend'
        if key == 'windows-pairing' and role == 'forge':
            return 'Linux Forge host'
        if key == 'container':
            return 'optional service inactive'
        return item.reason.value[0]
    if item.reason is Reason.UNSUPPORTED:
        return item.reason.value[0]
    if key == 'doctor':
        return 'diagnostic run incomplete'
    if key == 'arguments':
        return 'unsupported or incomplete doctor arguments'
    if not values:
        return item.reason.value[0]
    if key == 'installation':
        if values.get('matched') is True:
            return ('selected connector receipt agrees with release' if role == 'headend'
                    else role.capitalize() + ' release receipt agrees with verified media')
        return 'verified installation record unavailable or inconsistent'
    if key == 'tailscale-installed':
        result = 'approved executable present' if values.get('present') is True else 'approved executable absent'
        return result + ('; ' + values['version'] if 'version' in values else '')
    if key == 'tailscale-running':
        return 'status answered' if values.get('present') is True else 'status unavailable'
    if key == 'tailscale-signin':
        return '; '.join((values.get('state', 'unknown'),
                          'online' if values.get('online') is True else 'offline',
                          'key expired' if values.get('expired') is True else
                          'key valid' if values.get('expired') is False else 'key expiry unknown'))
    if key == 'tailnet-path':
        return values.get('peer_role', 'enrolled peer') + ' path ' + values.get('state', 'unknown')
    if key == 'magicdns':
        return 'enrolled peer name resolves' if values.get('matched') is True else 'enrolled peer name unavailable'
    if key == 'https-available':
        return ('paired peer TLS certificate valid' if values.get('matched') is True
                else 'required HTTPS certificate or peer TLS unavailable')
    if key == 'serve-routes':
        result = 'owned routes match the retained journal' if values.get('matched') is True else 'publication ownership or live routes disagree'
        return (str(values['routes']) + ' ' if 'routes' in values else '') + result
    if key == 'pairing':
        return ('committed enrolment and protected identity agree' if values.get('matched') is True
                else 'committed enrolment or protected identity unavailable')
    if key == 'peer-reachability':
        return 'enrolled peer HTTPS answered' if values.get('online') is True else 'enrolled peer service unavailable'
    if key == 'peer-clock' and {'offset_lower_ms', 'offset_upper_ms'} <= values.keys():
        return ('peer offset interval ' + _number(values['offset_lower_ms'] / 1000) + ' to '
                + _number(values['offset_upper_ms'] / 1000) + ' s')
    if key == 'first-start':
        return '; '.join(values[name] for name in ('stage', 'state', 'version') if name in values)
    if key in ('driver', 'docker-compose') and 'version' in values:
        return values['version'] + ('; required ' + values['minimum_version'] if 'minimum_version' in values else '')
    if key == 'docker-engine':
        return ('Linux engine; recorded local endpoint' if values.get('matched') is True
                else 'recorded local Linux engine unavailable or inconsistent')
    if key == 'gpu-runtime':
        return ('NVIDIA runtime configuration observed; check gpu-visible for service device evidence'
                if values.get('present') is True else 'NVIDIA runtime configuration unavailable')
    if key == 'gpu-visible':
        return 'running service device ' + values.get('device_kind', 'unknown')
    if key == 'container':
        return '; '.join((values.get('state', 'unknown'),
                          'health passed' if values.get('healthy') is True else 'health unavailable or failed',
                          'image agrees' if values.get('matched') is True else 'image unavailable or mismatched'))
    if key in ('model', 'model-attestation'):
        result = 'revision ' + values['revision'] + '; ' if 'revision' in values else ''
        result += 'match' if values.get('matched') is True else 'unavailable or mismatched'
        return result + ('; age ' + _number(values['age_seconds']) + ' s' if 'age_seconds' in values else '')
    if key == 'port':
        result = str(values['port']) + ' ' if 'port' in values else ''
        return result + ('owned by this installation' if values.get('owner_verified') is True else
                         'free' if values.get('present') is False else 'listener ownership unavailable or conflicting')
    if key == 'disk' and {'free_bytes', 'required_bytes'} <= values.keys():
        return str(values['free_bytes']) + ' free; ' + str(values['required_bytes']) + ' required'
    if key == 'gpu-hold':
        return 'no active hold or recovery marker' if values.get('present') is False else 'recorded GPU hold or recovery active'
    if key == 'windows-pairing':
        return 'protected pairing adapter qualified' if values.get('present') is True else 'protected pairing adapter unavailable'
    if key in ('engine', 'gateway'):
        return 'running; local status passed' if values.get('healthy') is True else 'required local service or status unavailable'
    if key in ('index-generation', 'index-fingerprint'):
        name = 'generation' if key == 'index-generation' else 'fingerprint'
        return ((values[name] + '; ') if name in values else '') + ('committed import agrees' if values.get('matched') is True else 'committed import unavailable or mismatched')
    if key == 'forge-auth':
        return ('enrolled Forge credential accepted' if values.get('matched') is True
                else 'enrolled Forge credential or endpoint unavailable or refused')
    if key == 'gateway-auth':
        return ('enrolled credential accepted; anonymous access denied' if values.get('matched') is True
                else 'enrolled credential refused or anonymous access accepted')
    if key in ('dense', 'reranker'):
        return '; '.join(('enabled' if values.get('enabled') is True else 'disabled',
                          (values.get('breaker') or 'unknown') + ' breaker',
                          'probe passed' if values.get('healthy') is True else 'probe unavailable or failed'))
    if key == 'index-health':
        return ('BM25, KB and structure ready' if values.get('healthy') is True else 'required index inputs unavailable or inconsistent')
    if key == 'agent-profile':
        return 'selected agent profile ' + ('matches connector state' if values.get('matched') is True else 'missing or inconsistent')
    if key == 'agent-connector':
        return 'owned entry and scoped launcher ' + ('agree' if values.get('matched') is True else 'missing or inconsistent')
    if key == 'agent-installed':
        return ('selected agent executable ' + ('present' if values.get('present') is True else 'absent')
                + ('; ' + values['version'] if 'version' in values else ''))
    if key == 'agent-signin':
        return "selected profile's native status " + ('passed' if values.get('matched') is True else 'unavailable or failed')
    if key == 'mcp-rag-status':
        return (str(values['tool_count']) + ' tools; ' if 'tool_count' in values else '') + ('required backends ready' if values.get('healthy') is True else 'required MCP status unavailable or degraded')
    return item.reason.value[0]


@dataclass(frozen=True)
class Row:
    id: str
    state: State
    reason: Reason
    observed_items: tuple = ()
    blocked_by: tuple = ()
    policy: str | None = None
    _seal: object = field(default=None, init=False, repr=False, compare=False)


@bounded('invalid-report')
def row(check_id, state, reason, values=None, *, blocked_by=(), public_identities=frozenset(), policy=None):
    family(check_id)
    if type(state) is not State or type(reason) is not Reason:
        raise DiagError('invalid-report')
    if policy is not None and (type(policy) is not str or policy not in POLICIES
                               or policy.split('.', 1)[1] != family(check_id)):
        raise DiagError('invalid-report')
    if (state is State.OK) != (reason is Reason.PASSED):
        raise DiagError('invalid-report')
    if reason is Reason.UNSUPPORTED and state is not State.WARN:
        raise DiagError('invalid-report')
    if reason is Reason.INAPPLICABLE and state is not State.SKIP:
        raise DiagError('invalid-report')
    if reason is Reason.BLOCKED and (state is not State.SKIP or not blocked_by):
        raise DiagError('invalid-report')
    if reason is Reason.SNAPSHOT_CHANGED and (state is not State.WARN or check_id != 'snapshot-changed'):
        raise DiagError('invalid-report')
    if (not isinstance(blocked_by, (tuple, list)) or len(blocked_by) > 16
            or any(type(x) is not str for x in blocked_by) or len(set(blocked_by)) != len(blocked_by)
            or (blocked_by and reason is not Reason.BLOCKED)):
        raise DiagError('invalid-report')
    for dependency in blocked_by:
        family(dependency)
    selected = observed(check_id, {} if values is None else values, public_identities=public_identities)
    result = Row(check_id, state, reason, tuple(sorted(selected.items())), tuple(blocked_by), policy)
    object.__setattr__(result, '_seal', _ROW)
    return result


@bounded('invalid-report')
def build_report(role, platform, hostname, checked_at, rows, *, complete=True):
    if (type(complete) is not bool or type(hostname) is not str
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', hostname)
            or type(checked_at) is not str
            or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', checked_at)):
        raise DiagError('invalid-report')
    try:
        datetime.fromisoformat(checked_at.replace('Z', '+00:00'))
    except ValueError:
        raise DiagError('invalid-report') from None
    rows = tuple(islice(rows, 257))
    if not 1 <= len(rows) <= 256 or any(type(x) is not Row or x._seal is not _ROW for x in rows):
        raise DiagError('invalid-report')
    by_id = {item.id: item for item in rows}
    if len(by_id) != len(rows):
        raise DiagError('invalid-report')
    if (role, platform) not in PAIRS:
        if (role is not None or platform is not None) or not any(
                item.id in ('arguments', 'installation') and item.state is State.FAIL for item in rows):
            raise DiagError('invalid-report')
    if not complete and not any(item.id == 'doctor' and item.state is State.FAIL for item in rows):
        raise DiagError('invalid-report')
    summary = {state.value.lower(): sum(item.state is state for item in rows) for state in State}
    checks = []
    for item in rows:
        for dependency in item.blocked_by:
            if dependency not in by_id or by_id[dependency].state is not State.FAIL or dependency == item.id:
                raise DiagError('invalid-report')
        if item.state is State.SKIP and item.reason not in (Reason.BLOCKED, Reason.INAPPLICABLE):
            raise DiagError('invalid-report')
        detail, fix = _detail(item, role), item.reason.value[1]
        sources = ['src/diagnostics/deep32_diag/report.py::row',
                   'src/diagnostics/deep32_diag/models.py::observed']
        if item.policy is not None:
            # fixed D2 substitutions and optional-state sentences.
            if not item.policy.startswith(role + '.'):
                raise DiagError('invalid-report')
            policy_fix, source = POLICIES[item.policy]
            sources = list(source)
            if item.id == 'gpu-runtime' and item.state is State.OK:
                detail = 'NVIDIA runtime configuration observed; check gpu-visible for service device evidence'
            if item.reason is Reason.BLOCKED:
                fix = 'Resolve ' + ', '.join(item.blocked_by) + ' first, then run deep32 doctor again.'
            if item.reason not in (Reason.PASSED, Reason.BLOCKED, Reason.UNSUPPORTED,
                                   Reason.INAPPLICABLE, Reason.SNAPSHOT_CHANGED):
                fix = policy_fix
                if role == 'forge':
                    hook = 'startup-hook-v1.ps1' if platform == 'windows' else 'startup-hook-v1.sh'
                    fix = fix.replace('the retained Forge resume command', "this installation's retained " + hook)
                    fix = fix.replace('the recorded Forge resume command', "this installation's retained " + hook)
                    if item.id == 'first-start':
                        fix = "Resolve the reported stage prerequisite, then run this installation's retained " + hook + '.'
                elif item.id == 'first-start':
                    fix = 'Resolve the reported stage prerequisite, then run d32-firstboot resume.'
            if item.reason is Reason.INAPPLICABLE and role == 'forge':
                if item.id.startswith('container.'):
                    fix = ('No repair is needed; use deep32 doctor after starting an authorised '
                           'conversion or build job to check its active service.')
                elif item.id == 'windows-pairing':
                    fix = "No repair is needed on Linux; run deep32 doctor on the Windows host to check that platform's pairing adapter."
            elif item.reason is Reason.INAPPLICABLE and role == 'headend':
                if item.id == 'serve-routes':
                    fix = 'Run deep32 doctor on the Workstation to inspect its Serve routes.'
                elif item.id == 'first-start':
                    fix = 'No repair is needed; run deep32 doctor on the Workstation to inspect its first-start state.'
        if item.id == 'arguments':
            # bounded command failure/recovery sentences.
            fix = 'Use deep32 doctor with a supported role and the required role options.'
        elif item.id == 'doctor':
            fix = "Run deep32 doctor again; if it still fails, restore this release's diagnostic runtime."
        if (type(detail) is not str or len(detail) > 512
                or not re.fullmatch(r'[ -~]*', detail)
                or fix is not None and (type(fix) is not str or not 1 <= len(fix) <= 768
                                         or not re.fullmatch(r'[ -~]+', fix))
                or any(type(source) is not str or not 1 <= len(source) <= 512
                       or not re.fullmatch(r'[ -~]+', source) for source in sources)):
            raise DiagError('invalid-report')
        checks.append({'id': item.id, 'state': item.state.value, 'detail': detail,
                       'observed': dict(item.observed_items), 'fix': fix,
                       'blocked_by': list(item.blocked_by),
                       'source': sources})
    result = {'schema': 'deep32.doctor-output.v1', 'role': role, 'platform': platform,
              'hostname': hostname, 'checked_at': checked_at, 'complete': complete,
              'checks': checks, 'summary': summary,
              'exit_code': 2 if summary['fail'] else 1 if summary['warn'] else 0}
    if len(json.dumps(result, ensure_ascii=True).encode('ascii')) > 256 * 1024:
        raise DiagError('invalid-report')
    return _Report(result, _REPORT)


@bounded('invalid-report')
def encode_report(*args, **kwargs):
    return render_json(build_report(*args, **kwargs))


@bounded('invalid-report')
def validate_report(value):
    """Require an unmodified report made by the typed semantic assembler.

    JSON Schema alone cannot establish that public identities were compared
    with authenticated expectations, that prose came from a fixed sentence,
    or that a SKIP refers to a failed row. Those checks happen before sealing.
    """
    if type(value) is not _Report or not value.unchanged():
        raise DiagError('invalid-report')
    encoded = json.dumps(value, ensure_ascii=True, separators=(',', ':'))
    if len(encoded.encode('ascii')) + 1 > 256 * 1024:
        raise DiagError('invalid-report')
    return value


@bounded('invalid-report')
def render_json(value):
    return json.dumps(validate_report(value), ensure_ascii=True, separators=(',', ':')) + '\n'


@bounded('invalid-report')
def render_text(value):
    value = validate_report(value)
    # D2 fixes the CHECK/STATE columns. The finite schema detail bound supplies
    # a deterministic continuation bound; no terminal queries or layout modes.
    # continuation bound for review; D2 specifies no line length.
    detail_column = 24 + 2 + 5 + 2
    detail_width = 512 - detail_column
    lines = ['Deep32 doctor  ' + (value['role'] or 'unknown') + '  '
             + value['hostname'] + '  ' + value['checked_at'],
             f'{"CHECK":24}  {"STATE":5}  DETAIL']
    for check in value['checks']:
        # A full stable ID is kept, including at the schema's maximum length.
        # Its extra pieces continue in CHECK rather than shifting STATE/DETAIL.
        names = textwrap.wrap(check['id'], width=24, break_long_words=True,
                              break_on_hyphens=False) or ['']
        details = textwrap.wrap(check['detail'], width=detail_width,
                                break_long_words=True, break_on_hyphens=False) or ['']
        lines.append(f'{names[0]:24}  {check["state"]:5}  ' + details[0])
        for index in range(1, max(len(names), len(details))):
            name = names[index] if index < len(names) else ''
            detail = details[index] if index < len(details) else ''
            lines.append(f'{name:24}  {"":5}  ' + detail)
    lines.append('')
    for number, check in enumerate((x for x in value['checks'] if x['state'] != 'OK'), 1):
        lines.append('FIX ' + str(number) + ' [' + check['id'] + ']: ' + check['fix'])
    summary = value['summary']
    lines.append(str(summary['ok']) + ' OK, ' + str(summary['warn']) + ' WARN, '
                 + str(summary['fail']) + ' FAIL')
    output = '\n'.join(lines) + '\n'
    if len(output.encode('ascii')) > 256 * 1024:
        raise DiagError('invalid-report')
    return output
