"""Selected-profile headend policy and qualified read-only observation seams.

No ordinary agent home, account file, launcher or mutating connector entry point
is consulted. Native adapters are deliberately absent: their pinned status modes
must first be qualified for no persistent mutation on each supported platform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
import os
from pathlib import Path
import re
import time
from types import MappingProxyType
from urllib.parse import urlsplit

from .errors import DiagError, bounded
from . import mcp, profile
from .models import State
from .report import Reason, row


@dataclass(frozen=True, repr=False)
class AgentObservation:
    agent: str
    state_path: Path
    executable: str
    qualified: bool = False
    present: bool | None = None
    version: str | None = None
    signed_in: bool | None = None

    def __repr__(self):
        return 'AgentObservation(<private>)'


@dataclass(frozen=True, repr=False)
class GatewayObservation:
    state_path: Path
    url: str
    authenticated: bool | None = None
    tools_valid: bool | None = None
    status: mcp.StatusObservation | None = None
    error: str | None = None

    def __repr__(self):
        return 'GatewayObservation(<private>)'


@dataclass(frozen=True, repr=False)
class HeadendEvidence:
    profile: profile.SelectedProfile | None = None
    profile_error: str | None = None
    agent: AgentObservation | None = None
    gateway: GatewayObservation | None = None
    # Independent owner evidence for identity/pairlock/handover, never inferred
    # from state.json, its mtime or a working bearer alone.
    pairing_committed: bool | None = None

    def __repr__(self):
        return 'HeadendEvidence(<private>)'


class AgentOperation(Enum):
    VERSION = 'version'
    SIGNIN = 'signin'


@dataclass(frozen=True, repr=False)
class AgentRequest:
    """Fixed native operation, with exactly one selected-profile override.

    An installed adapter owns its sealed OS environment allow-list and must not
    add ordinary agent homes or the connector bearer. It must discard stderr,
    provide DEVNULL stdin, honour the deadline, bound and drain output, and
    cancel only the processes it starts. There is no shell or argv input.
    """
    operation: AgentOperation
    agent: str
    executable: str
    profile_dir: Path
    deadline: float
    max_bytes: int = 4096

    @property
    def argv(self):
        args = ('--version',) if self.operation is AgentOperation.VERSION else (
            ('login', 'status') if self.agent == 'codex' else ('auth', 'status'))
        return (self.executable, *args)

    @property
    def profile_environment(self):
        variable = 'CODEX_HOME' if self.agent == 'codex' else 'CLAUDE_CONFIG_DIR'
        return MappingProxyType({variable: str(self.profile_dir)})

    def __repr__(self):
        return 'AgentRequest(<private>)'


@dataclass(frozen=True, repr=False)
class AgentResult:
    returncode: int
    stdout: bytes = field(default=b'', repr=False)

    def __repr__(self):
        return 'AgentResult(<private>)'


@dataclass(frozen=True, repr=False)
class NativeAgentBoundary:
    """Independently qualified pinned client, not a native runner by default.

    `version_line` is the exact public --version output measured in the native
    no-write qualification; `version` is its validated numeric projection. Both
    status commands must be qualified at that exact executable/version. No
    invented native output format is accepted as installed-host evidence.
    """
    agent: str
    executable: str
    profile_dir: Path
    version: str
    version_line: bytes
    reader: object = field(repr=False)
    qualified: bool = False

    def __repr__(self):
        return 'NativeAgentBoundary(<private>)'


_VERSION = r'v?\d+(?:\.\d+){0,3}(?:-(?:alpha|beta|rc)\.\d+)?'
_PROFILE_ERRORS = frozenset(('profile-selection', 'profile-state', 'profile-config',
    'profile-launcher', 'unsafe-file', 'file-unavailable', 'credential-permissions',
    'credential-unavailable', 'credential-invalid', 'snapshot-changed'))
_AUTH_ERRORS = frozenset(('mcp-auth', 'mcp-anonymous', 'mcp-credential',
                         'mcp-redirect', 'mcp-endpoint'))


def _selected(value):
    return (type(value) is profile.SelectedProfile and value.agent in ('codex', 'claude')
            and isinstance(value.state_path, Path) and value.state_path.is_absolute()
            and type(value.executable) is str and Path(value.executable).is_absolute()
            and type(value.launcher_origin_confirmed) is bool)


def _agent_bound(value, selected):
    return (type(value) is AgentObservation and value.agent == selected.agent
            and value.state_path == selected.state_path and value.executable == selected.executable
            and type(value.qualified) is bool
            and all(item is None or type(item) is bool for item in (value.present, value.signed_in))
            and (value.version is None or type(value.version) is str and
                 re.fullmatch(_VERSION, value.version) is not None and len(value.version) <= 64))


def _gateway_bound(value, selected):
    return (type(value) is GatewayObservation and value.state_path == selected.state_path
            and value.url == selected.url
            and all(item is None or type(item) is bool
                    for item in (value.authenticated, value.tools_valid))
            and (value.error is None or type(value.error) is str and value.error in mcp._CODES))


@bounded('invalid-observation')
def read_headend_profile(agent, profile_dir, *, windows=None, acl_getter=None,
                         decrypt=None, current_uid=None, launcher_origin=None):
    """Observe exactly the requested core profile, without discovery or writes.

    Windows requires explicitly injected, independently qualified getter-only
    ACL and CurrentUser DPAPI capabilities. The core's unqualified native
    defaults are never activated by this role adapter.
    """
    windows = os.name == 'nt' if windows is None else windows
    if windows and (acl_getter is None or decrypt is None):
        return HeadendEvidence(profile_error='credential-unavailable')
    try:
        selected = profile.read_selected_profile(profile_dir, agent, windows=windows,
            acl_getter=acl_getter, decrypt=decrypt, current_uid=current_uid,
            launcher_origin=launcher_origin)
    except profile.ProfileError as error:
        return HeadendEvidence(profile_error=error.code)
    return HeadendEvidence(profile=selected)


@bounded('invalid-observation')
def observe_agent(selected, *, boundary=None, clock=time.monotonic, deadline=None):
    """Use only a qualified version/status reader, never launch or sign in.

    Missing native qualification returns an unconfirmed observation without
    invoking the reader. The shared enclosing deadline can only shorten the
    ten-second allowance across both native operations.
    """
    if not _selected(selected):
        raise DiagError('invalid-observation')
    unavailable = AgentObservation(selected.agent, selected.state_path, selected.executable)
    if boundary is None or type(boundary) is not NativeAgentBoundary or boundary.qualified is not True:
        return unavailable
    home = selected.state_path.parents[2]
    if (not selected.launcher_origin_confirmed or boundary.agent != selected.agent
            or boundary.executable != selected.executable or boundary.profile_dir != home
            or type(boundary.version) is not str or len(boundary.version) > 64
            or re.fullmatch(_VERSION, boundary.version) is None
            or type(boundary.version_line) is not bytes or not 1 <= len(boundary.version_line) <= 256
            or not callable(boundary.reader)):
        return unavailable
    version_obtained = False
    try:
        decoded = boundary.version_line.decode('ascii').strip('\r\n')
        if (not decoded or any(ord(char) < 32 or ord(char) > 126 for char in decoded)
                or boundary.version.lstrip('v') not in decoded):
            return unavailable
        start = clock()
        if type(start) not in (int, float) or not math.isfinite(start):
            return unavailable
        end = start + mcp.MAX_SECONDS
        if deadline is not None:
            if type(deadline) not in (int, float) or not math.isfinite(deadline):
                return unavailable
            end = min(end, deadline)
        def read(operation):
            if clock() >= end:
                raise TimeoutError()
            result = boundary.reader(AgentRequest(operation, selected.agent,
                selected.executable, home, end))
            if (clock() >= end or type(result) is not AgentResult
                    or type(result.returncode) is not int or type(result.stdout) is not bytes
                    or len(result.stdout) > 4096):
                raise ValueError()
            return result
        version = read(AgentOperation.VERSION)
        if version.returncode != 0 or version.stdout != boundary.version_line:
            return AgentObservation(selected.agent, selected.state_path, selected.executable,
                                    True, False)
        version_obtained = True
        status = read(AgentOperation.SIGNIN)
        return AgentObservation(selected.agent, selected.state_path, selected.executable,
                                True, True, boundary.version, status.returncode == 0)
    except Exception:
        # No private exception object/trace is retained in evidence or output.
        return AgentObservation(selected.agent, selected.state_path, selected.executable, True,
            version_obtained, boundary.version if version_obtained else None,
            False if version_obtained else None)


@bounded('invalid-observation')
def observe_gateway(selected, *, transport=None, clock=time.monotonic, deadline=None):
    """Share the negotiated J05 client; no arbitrary URL/token argument exists.

    The native transport qualification remains the owning platform's gate. This
    function never substitutes /health for structured authenticated MCP status.
    """
    if not _selected(selected) or not selected.launcher_origin_confirmed:
        raise DiagError('invalid-observation')
    authenticated, tools_valid, status, error_code = None, None, None, None
    try:
        start = clock()
        if type(start) not in (int, float) or not math.isfinite(start):
            raise mcp.MCPError('mcp-state')
        end = start + mcp.MAX_SECONDS
        if deadline is not None:
            if type(deadline) not in (int, float) or not math.isfinite(deadline):
                raise mcp.MCPError('mcp-state')
            end = min(end, deadline)
        endpoint = urlsplit(selected.url)
        client = mcp.MCPClient(mcp.EnrolledEndpoint(selected.url, endpoint.hostname,
            endpoint.port or 443), selected.credential, transport=transport,
            clock=clock, deadline=end)
        client.probe_authentication()
        client.initialise()
        authenticated = True
        client.list_tools()
        tools_valid = True
        status = client.status()
    except mcp.MCPError as error:
        error_code = error.code
        if authenticated is None or error_code in _AUTH_ERRORS:
            authenticated = False
        elif tools_valid is None:
            tools_valid = False
    except Exception:
        error_code = 'mcp-transport'
        if authenticated is None:
            authenticated = False
    return GatewayObservation(selected.state_path, selected.url, authenticated,
                              tools_valid, status, error_code)


def _row(check, state, values=None, *, blocked_by=(), unsupported=False):
    return row(check, state, Reason.PASSED if state is State.OK else
        Reason.BLOCKED if blocked_by else Reason.UNSUPPORTED if unsupported else
        Reason.UNAVAILABLE, values, blocked_by=blocked_by, policy='headend.' + check)


def _validated_status(value):
    """Revalidate even caller-built typed snapshots through the core parser."""
    if type(value) is not mcp.StatusObservation:
        return None
    try:
        backends = {}
        for name in ('bm25', 'dense', 'reranker', 'kb', 'structure'):
            backend = getattr(value, name)
            if type(backend) is not mcp.BackendStatus:
                return None
            backends[name] = dict(enabled=backend.enabled, ok=backend.ok, breaker=backend.breaker)
        raw = dict(ok=value.ok, generation=value.generation,
            corpus_fingerprint=value.corpus_fingerprint, backends=backends,
            degraded=list(value.degraded))
        return mcp._status({'content': [{'type': 'text', 'text': json.dumps(raw)}]})
    except Exception:
        return None


@bounded('invalid-observation')
def headend_rows(evidence):
    """Six stable D2 headend rows; shared role rows belong to the dispatcher."""
    if type(evidence) is not HeadendEvidence:
        raise DiagError('invalid-observation')
    selected = evidence.profile
    if not _selected(selected) or evidence.profile_error is not None:
        code = evidence.profile_error if evidence.profile_error in _PROFILE_ERRORS else None
        rows = [_row('agent-profile', State.FAIL)]
        for check in ('agent-connector', 'agent-installed', 'agent-signin', 'gateway-auth', 'mcp-rag-status'):
            direct = (check == 'agent-connector' and code in ('profile-config', 'profile-launcher')
                or check == 'gateway-auth' and code in ('credential-permissions',
                    'credential-unavailable', 'credential-invalid'))
            rows.append(_row(check, State.FAIL if direct else State.SKIP,
                             blocked_by=() if direct else ('agent-profile',)))
        return tuple(rows)
    agent_values = {'agent': selected.agent}
    rows = [_row('agent-profile', State.OK, dict(agent_values, matched=True))]
    connector_good = selected.launcher_origin_confirmed
    rows.append(_row('agent-connector', State.OK if connector_good else State.FAIL,
                     dict(agent_values, matched=connector_good)))
    native = evidence.agent
    if not _agent_bound(native, selected) or not native.qualified:
        rows.append(_row('agent-installed', State.WARN, agent_values, unsupported=True))
        rows.append(_row('agent-signin', State.WARN, agent_values, unsupported=True))
    elif native.present is not True or native.version is None:
        rows.append(_row('agent-installed', State.FAIL, dict(agent_values, present=False)))
        rows.append(_row('agent-signin', State.SKIP, agent_values, blocked_by=('agent-installed',)))
    else:
        rows.append(_row('agent-installed', State.OK,
                         dict(agent_values, present=True, version=native.version)))
        rows.append(_row('agent-signin', State.WARN if native.signed_in is None else
                         State.OK if native.signed_in else State.FAIL,
                         dict(agent_values, matched=native.signed_in is True),
                         unsupported=native.signed_in is None))
    gateway = evidence.gateway
    if not connector_good:
        rows.append(_row('gateway-auth', State.SKIP, blocked_by=('agent-connector',)))
        rows.append(_row('mcp-rag-status', State.SKIP, blocked_by=('agent-connector',)))
        return tuple(rows)
    bound = _gateway_bound(gateway, selected)
    auth_good = bound and gateway.authenticated is True and gateway.error not in _AUTH_ERRORS
    rows.append(_row('gateway-auth', State.OK if auth_good else State.FAIL, {'matched': auth_good}))
    if not auth_good:
        rows.append(_row('mcp-rag-status', State.SKIP, blocked_by=('gateway-auth',)))
        return tuple(rows)
    status = _validated_status(gateway.status)
    if gateway.tools_valid is not True or status is None or gateway.error is not None:
        rows.append(_row('mcp-rag-status', State.FAIL, {'healthy': False}))
    else:
        required_failed = not status.ok or any(backend.ok is not True
            for backend in (status.bm25, status.kb, status.structure))
        state = State.FAIL if required_failed else State.OK if status.ready else State.WARN
        # Generation/fingerprint are internal. They are never made printable by
        # copying an unauthenticated remote identity into a public allow-list.
        rows.append(_row('mcp-rag-status', state,
                         {'tool_count': len(mcp.TOOLS), 'healthy': status.ready}))
    return tuple(rows)


@bounded('invalid-observation')
def headend_pairing_row(evidence):
    """Require independent committed owner evidence and current exact auth."""
    if type(evidence) is not HeadendEvidence:
        raise DiagError('invalid-observation')
    selected = evidence.profile
    good = (_selected(selected) and evidence.profile_error is None and selected.launcher_origin_confirmed
        and evidence.pairing_committed is True and _gateway_bound(evidence.gateway, selected)
        and evidence.gateway.authenticated is True and evidence.gateway.error not in _AUTH_ERRORS)
    return row('pairing', State.OK if good else State.FAIL,
        Reason.PASSED if good else Reason.UNAVAILABLE,
        {'present': _selected(selected), 'matched': bool(good), 'peer_role': 'workstation'},
        policy='headend.pairing')
