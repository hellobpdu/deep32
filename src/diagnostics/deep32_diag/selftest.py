"""Installed retrieval comparison; only the separately authenticated bundle runs.

Native installation, protected credential and transport capabilities belong to
the packaging owner. This module discovers no endpoint and opens no local file.
The Python-only fixture harness cannot be selected by the shipped command.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import math
import re
import signal
import threading
import time

from . import mcp
from .models import State, observe_attestation
from .runner import CommandRequest
from .selftest_contract import (METRICS, aggregate, canonical_bytes, first_rank,
                                passes)
from .selftest_trust import require_bundle
from .trust import RELEASE


class SelftestError(Exception):
    def __init__(self, code='protocol'):
        self.code = code if code in ('trust', 'installation', 'index', 'models',
            'settings', 'authentication', 'timeout', 'interrupted', 'protocol',
            'snapshot', 'arguments', 'forge') else 'protocol'
        super().__init__(self.code)


class SelftestBudget:
    """One hard 900-second monotonic deadline, including trust and cleanup.

    The qualified observation owner must interrupt native work at the supplied
    deadline. Post-call checks alone do not qualify an arbitrary Python callback.
    """
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        if type(self.started) not in (int, float) or not math.isfinite(self.started):
            raise SelftestError('timeout')
        self.end = self.started + 900

    def check(self, end=None):
        now = self.clock()
        if (type(now) not in (int, float) or not math.isfinite(now)
                or now < self.started or now >= min(self.end, self.end if end is None else end)):
            raise SelftestError('timeout')

    def deadline(self, seconds=10):
        self.check()
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 30:
            raise SelftestError('timeout')
        return min(self.clock() + seconds, self.end)

    def observe(self, observer, request):
        if (type(request) is not CommandRequest or type(request.deadline) not in (int, float)
                or not math.isfinite(request.deadline)
                or request.deadline > self.deadline(10)):
            raise SelftestError('timeout')
        self.check(request.deadline)
        result = observer(request)
        self.check(request.deadline)
        return result


@dataclass(frozen=True, repr=False)
class WorkstationEndpoint(mcp.EnrolledEndpoint):
    """Exact installer-recorded literal loopback; no DNS or URL fallback."""
    def __post_init__(self):
        if (type(self.enrolled_port) is not int or not 1 <= self.enrolled_port <= 65535
                or self.enrolled_host not in ('127.0.0.1', '::1')):
            raise mcp.MCPError('mcp-endpoint')
        host = '[::1]' if self.enrolled_host == '::1' else self.enrolled_host
        if self.url != 'http://' + host + ':' + str(self.enrolled_port) + '/mcp':
            raise mcp.MCPError('mcp-endpoint')


@dataclass(frozen=True, repr=False)
class SelftestRequest(mcp.Request):
    connect_deadline: float = 0.0


class _Transport:
    """Add the five-second connect cap to the existing qualified MCP seam."""
    def __init__(self, transport, endpoint, budget):
        self._transport, self._endpoint, self._budget = transport, endpoint, budget

    def open(self, request):
        if request.endpoint is not self._endpoint:
            raise SelftestError('snapshot')
        self._budget.check(request.deadline)
        bounded = SelftestRequest(request.endpoint, request.headers, request.body,
            request.deadline, request.max_bytes, request.method,
            request.allow_redirects, request.use_proxies,
            min(self._budget.clock() + 5, request.deadline))
        return self._transport.open(bounded)

    def close(self):
        close = getattr(self._transport, 'close', None)
        if close is not None:
            close()


def _body(result):
    if type(result) is not dict or result.get('isError', False) is not False:
        raise SelftestError('protocol')
    content = result.get('content')
    if (type(content) is not list or len(content) != 1 or type(content[0]) is not dict
            or content[0].get('type') != 'text' or type(content[0].get('text')) is not str):
        raise SelftestError('protocol')
    return mcp._json(content[0]['text'])


def _string(value, maximum):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return False
    try:
        value.encode('utf-8', 'strict')
    except UnicodeError:
        return False
    return True


def search_hits(result, generation):
    """Keep delivered order; alias metadata and expanded sections earn no credit."""
    value = _body(result)
    if value.get('generation') != generation:
        raise SelftestError('index')
    if value.get('degraded') != []:
        raise SelftestError('models')
    hits = value.get('hits')
    if type(hits) is not list or len(hits) > 32:
        raise SelftestError('protocol')
    seen = set()
    for position, hit in enumerate(hits, 1):
        if (type(hit) is not dict or not _string(hit.get('id'), 256)
                or not _string(hit.get('doc_key'), 256)
                or type(hit.get('text')) is not str or len(hit['text']) > mcp.MAX_BYTES
                or hit['id'] in seen or position <= 10 and hit.get('reranked') is not True
                or 'rank' in hit and (type(hit['rank']) is not int or hit['rank'] != position)):
            raise SelftestError('protocol')
        try:
            hit['text'].encode('utf-8', 'strict')
        except UnicodeError:
            raise SelftestError('protocol') from None
        seen.add(hit['id'])
    return hits


class SelftestMCPClient(mcp.MCPClient):
    """Shared JSON/SSE/session parser with selftest's fixed operation deadlines.

    A transport must honour SelftestRequest.connect_deadline during resolution
    and connection, request.deadline through reads, and close owned handles on
    interruption. No native transport is qualified or supplied by this module.
    """
    def __init__(self, endpoint, credential, *, transport=None, budget=None):
        self.budget = budget or SelftestBudget()
        self.__end = None
        wrapped = _Transport(transport or mcp.UnqualifiedTransport(), endpoint, self.budget)
        super().__init__(endpoint, credential, transport=wrapped,
                         clock=self.budget.clock, deadline=self.budget.end)
        self.__pinned = (endpoint, credential, wrapped)

    def _deadline(self):
        return self.__end if self.__end is not None else self.budget.deadline(10)

    def _operation(self, seconds, function):
        if self.__end is not None or any(a is not b for a, b in zip(
                (self._endpoint, self._credential, self._transport), self.__pinned)):
            raise SelftestError('snapshot')
        self.__end = self.budget.deadline(seconds)
        try:
            value = function()
            self.budget.check(self.__end)
            return value
        finally:
            self.__end = None

    def begin(self):
        self._operation(5, self.probe_authentication)
        self._operation(5, self.initialise)
        self._operation(10, self.list_tools)

    def ready(self, reference):
        def extract(result):
            status = mcp._status(result)
            value = _body(result)
            if (status.generation != reference['generation']
                    or status.corpus_fingerprint != reference['corpus_fingerprint']):
                raise SelftestError('index')
            if (not status.ready or status.dense.breaker != 'closed'
                    or status.reranker.breaker != 'closed'):
                raise SelftestError('models')
            if ('settings' not in value or canonical_bytes(value['settings']) != canonical_bytes(reference['settings'])
                    or hashlib.sha256(canonical_bytes(value['settings'])).hexdigest() != reference['settings_sha256']):
                raise SelftestError('settings')
            return status
        # Preserve typed selftest failures through MCP's redacted extractor seam.
        return self._typed_call('rag_status', {'format': 'json'}, extract, 10)

    def _typed_call(self, name, arguments, extractor, seconds):
        failure = []
        def typed(result):
            try:
                return extractor(result)
            except SelftestError as error:
                failure.append(error.code)
                return None
        result = self._operation(seconds, lambda: self._extract_tool(name, arguments, typed))
        if failure:
            raise SelftestError(failure[0])
        return result

    def search(self, question, reference):
        arguments = dict(reference['request'])
        arguments['query'] = question['text']
        if question['collection'] is not None:
            arguments['doc_type'] = question['collection']
        return self._typed_call('search_rag', arguments,
            lambda result: search_hits(result, reference['generation']), 30)

    def close(self):
        self.__pinned[2].close()
        self._session = None
        self._private_values = frozenset()


@dataclass(frozen=True, repr=False)
class SelftestSession:
    client: SelftestMCPClient = field(repr=False)
    attest: object = field(repr=False)
    unchanged: object = field(repr=False)
    utc_now: object = field(default=time.time, repr=False)


@dataclass(frozen=True, repr=False)
class SelftestEvidence:
    """Compiled installed owner seam; absent by default, never populated by CLI.

    authenticate(budget) must return a sealed selftest_trust.TrustedBundle.
    open_session(budget) reads only the installed local or selected credential
    after authentication. unchanged(deadline) rechecks protected snapshots.
    Synthetic callbacks prove policy; they confer no native qualification.
    """
    role: str
    authenticate: object = field(repr=False)
    open_session: object = field(repr=False)
    agent: str | None = None
    profile_dir: str | None = field(default=None, repr=False)


FORGE_MESSAGE = 'Run deep32 selftest on the Workstation or its paired headend.'
_FIX = {
    'models': 'Forge is unavailable; restore the paired Forge and run deep32 doctor on the Workstation.',
    'index': "This index does not match the release reference; restore the release's Content image or use the matching signed selftest bundle.",
    'authentication': 'Gateway authentication failed; pair this selected headend again.',
    'forge': FORGE_MESSAGE,
    # fixed recovery sentences for D3's unnamed failure classes.
    'trust': "Restore this release's signed selftest bundle and run deep32 selftest again.",
    'installation': "Restore this release's installed diagnostic runtime and run deep32 selftest again.",
    'settings': "Restore this release's retrieval settings and run deep32 selftest again.",
    'timeout': 'Run deep32 doctor, resolve the reported prerequisite, then run deep32 selftest again.',
    'interrupted': 'Run deep32 selftest again after the interruption is resolved.',
    'snapshot': 'Run deep32 doctor, resolve changed installation state, then run deep32 selftest again.',
    'protocol': 'Run deep32 doctor, resolve the gateway protocol failure, then run deep32 selftest again.',
    'arguments': 'Use deep32 selftest with only --json, --details and the selected headend profile and agent options.',
}
_NAMES = ('Signed reference', 'Gateway and index', 'Forge and models', 'Run complete')
_IDS = ('signed-reference', 'gateway-index', 'forge-models', 'run-complete')
_SEALED = object()


class _Result(dict):
    def __init__(self, value, *, role, hostname, checked_at, decisions):
        super().__init__(value)
        self.role, self.hostname, self.checked_at = role, hostname, checked_at
        self.decisions = tuple(decisions.items())
        self._digest = hashlib.sha256(canonical_bytes(value)).digest()
        self._metadata = canonical_bytes((role, hostname, checked_at, self.decisions))
        self._seal = _SEALED


def _make_result(bundle, checks, *, ranks=None, details=False, role=None,
                 hostname='local-host', checked_at=None):
    # The D3 result schema requires a reference and nonzero question_count even
    # before trust succeeds. These labelled placeholders are never measurements.
    reference = dict(bundle.reference['reference']) if bundle else dict.fromkeys(METRICS, 0.0)
    measured = aggregate(ranks) if ranks is not None else None
    decisions = passes(measured, bundle.reference['reference']) if measured is not None else {}
    complete = measured is not None
    passed = complete and all(decisions.values())
    value = {'schema_version': 1, 'bundle_id': bundle.bundle_id if bundle else 'unavailable',
        'release': bundle.release if bundle else RELEASE,
        'fixture_only': bundle.fixture_only if bundle else False,
        'status': 'PASS' if passed else 'FAIL' if complete else 'INCOMPLETE',
        'exit_code': 0 if passed else 1 if complete else 2,
        'completed_questions': len(ranks) if complete else 0,
        'question_count': len(bundle.questions) if bundle else 1,
        'checks': checks, 'measured': measured, 'reference': reference,
        'details': [{'id': q['id'], 'first_relevant_rank': rank}
                    for q, rank in zip(bundle.questions, ranks)] if complete and details else []}
    hostname = hostname if type(hostname) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', hostname) else 'local-host'
    if checked_at is None:
        checked_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        if type(checked_at) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', checked_at):
            raise ValueError()
        datetime.strptime(checked_at, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        checked_at = '1970-01-01T00:00:00Z'
    return _Result(value, role=role, hostname=hostname, checked_at=checked_at, decisions=decisions)


def incomplete(code='installation', *, bundle=None, phase=0, role=None,
               hostname='local-host', checked_at=None):
    if code not in _FIX:
        code = 'protocol'
    phase = phase if type(phase) is int and 0 <= phase <= 3 else 0
    checks = []
    for index, name in enumerate(_IDS):
        ok, failed = index < phase, index == phase
        # bounded diagnostic details; no remote prose is copied.
        detail = ('verified; unchanged' if ok else 'unavailable or incompatible' if failed else 'no comparable run')
        if index == 0 and failed:
            detail = 'signed bundle unavailable; reference values are placeholders'
        checks.append({'check': name, 'state': 'OK' if ok else 'FAIL' if failed else 'SKIP',
            'detail': detail, 'fix': None if ok else _FIX[code] if failed else
            'Run deep32 selftest again after the failed check is fixed.'})
    return _make_result(bundle, checks, role=role, hostname=hostname, checked_at=checked_at)


def _attest(session, bundle, budget):
    deadline = budget.deadline(10)
    snapshot = session.attest(deadline)
    budget.check(deadline)
    now = session.utc_now()
    for kind, name in (('embed', 'embedding'), ('rerank', 'reranker')):
        expected = bundle.reference['models'][name]
        observed = observe_attestation(snapshot, kind, expected_model=expected['model'],
            expected_revision=expected['revision'], now=now, maximum_age=10)
        if observed.state is not State.OK or not observed.matched:
            raise SelftestError('models')


def run_selftest(*, evidence=None, agent=None, profile_dir=None, details=False,
                 fixture_harness=False, budget=None, hostname='local-host', checked_at=None):
    bundle = session = None
    phase, code, ranks = 0, None, []
    role = evidence.role if type(evidence) is SelftestEvidence else None
    try:
        budget = budget or SelftestBudget()
        budget.check()
        if type(evidence) is not SelftestEvidence or role not in ('workstation', 'headend', 'forge'):
            raise SelftestError('installation')
        if role == 'forge':
            raise SelftestError('forge')
        if role == 'headend':
            if (agent not in ('codex', 'claude') or evidence.agent != agent
                    or not _string(profile_dir, 4096) or evidence.profile_dir != profile_dir):
                raise SelftestError('installation')
        elif agent is not None or profile_dir is not None or evidence.agent is not None or evidence.profile_dir is not None:
            raise SelftestError('installation')
        binding = evidence.authenticate(budget)
        bundle = require_bundle(binding, fixture_harness=fixture_harness)
        budget.check()
        phase = 1
        session = evidence.open_session(budget)
        if (type(session) is not SelftestSession or type(session.client) is not SelftestMCPClient
                or session.client.budget is not budget):
            raise SelftestError('installation')
        endpoint = session.client._endpoint
        if ((role == 'headend' and type(endpoint) is not mcp.EnrolledEndpoint)
                or role == 'workstation' and type(endpoint) not in (mcp.EnrolledEndpoint, WorkstationEndpoint)):
            raise SelftestError('installation')
        session.client.begin()
        session.client.ready(bundle.reference)
        phase = 2
        _attest(session, bundle, budget)
        phase = 3
        for question in bundle.questions:
            budget.check()
            hits = session.client.search(question, bundle.reference)
            ranks.append(first_rank(question, hits))
        session.client.ready(bundle.reference)
        _attest(session, bundle, budget)
        binding.recheck()
        deadline = budget.deadline(10)
        if session.unchanged(deadline) is not True:
            raise SelftestError('snapshot')
        budget.check(deadline)
    except SelftestError as error:
        code = error.code
    except mcp.MCPError as error:
        code = ('authentication' if error.code in ('mcp-auth', 'mcp-credential') else
                'timeout' if error.code == 'mcp-timeout' else 'protocol')
    except KeyboardInterrupt:
        code = 'interrupted'
    except Exception:
        code = 'trust' if phase == 0 else 'protocol'
    finally:
        if type(session) is SelftestSession:
            try:
                session.client.close()
                budget.check()
            except (Exception, KeyboardInterrupt):
                code = code or 'interrupted'
    if code:
        if code in ('index', 'authentication', 'settings'):
            phase = min(phase, 1)
        elif code == 'models':
            phase = min(phase, 2)
        return incomplete(code, bundle=bundle, phase=phase, role=role,
                          hostname=hostname, checked_at=checked_at)
    checks = [{'check': name, 'state': 'OK', 'detail': detail, 'fix': None}
        for name, detail in zip(_IDS, (bundle.bundle_id,
            bundle.reference['generation'] + '; fingerprint matched',
            'both identities matched; breakers closed',
            str(len(ranks)) + ' of ' + str(len(ranks)) + ' questions; profile unchanged'))]
    return _make_result(bundle, checks, ranks=ranks, details=details, role=role,
                        hostname=hostname, checked_at=checked_at)


def validate_result(value):
    if (type(value) is not _Result or value._seal is not _SEALED
            or hashlib.sha256(canonical_bytes(dict(value))).digest() != value._digest
            or canonical_bytes((value.role, value.hostname, value.checked_at, value.decisions)) != value._metadata):
        raise SelftestError('protocol')
    return value


@contextmanager
def interruptions():
    """Close the owned observations for console interrupt and termination alike."""
    previous = {}
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def render_json(value):
    return canonical_bytes(validate_result(value)).decode('ascii') + '\n'


def render_text(value):
    value = validate_result(value)
    if value.role == 'forge':
        return FORGE_MESSAGE + '\n'
    role = {'workstation': 'Workstation', 'headend': 'Headend'}.get(value.role, 'unknown')
    lines = ['Deep32 selftest  ' + role + '  ' + value.hostname + '  ' + value.checked_at,
             f'{"CHECK":22} {"STATE":5} DETAIL']
    for name, check in zip(_NAMES, value['checks']):
        lines.append(f'{name:22} {check["state"]:5} ' + check['detail'])
    lines.append('')
    for index, check in enumerate((c for c in value['checks'] if c['state'] != 'OK'), 1):
        lines.append('FIX ' + str(index) + ' ' + check['fix'])
    if value['measured'] is None:
        lines.append('INCOMPLETE; metrics unavailable; 0 of ' + str(value['question_count']) + ' questions scored')
    else:
        lines.append(f'{"METRIC":22} {"MEASURED":>8} {"REFERENCE":>9} STATE')
        for name, label in zip(METRICS, ('hit@1', 'hit@3', 'hit@8', 'MRR@10')):
            state = 'OK' if dict(value.decisions)[name] else 'FAIL'
            lines.append(f'{label:22} {value["measured"][name]:8.3f} {value["reference"][name]:9.3f} {state}')
        # The summary counts the four metric decisions displayed immediately
        # above it; a complete failed comparison must not announce four OKs.
        ok = sum(dict(value.decisions).values())
        lines.append(str(ok) + ' OK, 0 WARN, ' + str(4 - ok) + ' FAIL; '
                     + value['status'] + ' (' + str(value['question_count']) + ' questions)')
        if value['details']:
            lines.append(f'{"QUESTION":22} {"RANK":>4} STATE')
            for question in value['details']:
                rank = question['first_relevant_rank']
                lines.append(f'{question["id"]:22} {str(rank) if rank is not None else "-":>4} ' + ('HIT' if rank else 'MISS'))
    return '\n'.join(lines) + '\n'
