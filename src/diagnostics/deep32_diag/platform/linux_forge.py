"""Narrow observation-only Linux Forge adapter boundary.

No native executable is qualified by this source. A release owner supplies the
denied-write, bounded native observer; absent qualification never launches it.
"""

from dataclasses import dataclass, field
from enum import Enum

from ..errors import DiagError, bounded
from ..forge import ForgeObservation, forge_rows
from ..runner import Budget, CommandRequest


class ForgeRead(str, Enum):
    DRIVER = 'driver'
    ENGINE = 'engine'
    COMPOSE = 'compose'
    RUNTIME = 'runtime'
    CONTAINERS = 'containers'
    MODELS = 'models'
    LISTENERS = 'listeners'
    JOBS = 'jobs'


@dataclass(frozen=True, repr=False)
class ForgeRequest:
    operation: ForgeRead
    deadline: float
    maximum_bytes: int = 1024 * 1024


@dataclass(frozen=True, repr=False)
class ForgeReader:
    """Compiled capability, not receipt data or a user-provided command.

    `observer(ForgeRequest)` returns only the corresponding typed projection.
    Qualification includes fixed installed paths/socket, no environment/PATH
    routing, concurrent output draining, own-child-tree cancellation, no writes
    and passive listener ownership. Never qualify docker run/exec/load/pull or
    bind probes. No general command/URL/path can enter this boundary.
    """
    observer: object = field(default=None, repr=False)
    qualified: frozenset = frozenset()


@bounded('invalid-observation')
def observe(reader=None, *, budget=None):
    if reader is None:
        return ForgeObservation()
    if (type(reader) is not ForgeReader or type(reader.qualified) is not frozenset
            or any(type(item) is not ForgeRead for item in reader.qualified)):
        raise DiagError('invalid-observation')
    if reader.observer is None:
        return ForgeObservation()
    budget = budget or Budget()
    values, qualified = {}, set()
    # RUNTIME deliberately shares the already observed ENGINE projection;
    # native owners do not need a second broad docker-info invocation.
    fields = {ForgeRead.DRIVER: 'drivers', ForgeRead.ENGINE: 'engine',
              ForgeRead.COMPOSE: 'compose_version', ForgeRead.CONTAINERS: 'containers',
              ForgeRead.MODELS: 'models', ForgeRead.LISTENERS: 'listeners', ForgeRead.JOBS: 'job'}
    for operation, name in fields.items():
        if operation not in reader.qualified:
            continue
        deadline = budget.deadline()
        qualified.add(operation.value)
        try:
            request = ForgeRequest(operation, deadline)
            # Share the same four-observation semaphore/deadline enforcement
            # as every role; the bridge confers no command capability.
            bridge = CommandRequest((), deadline, request.maximum_bytes)
            value = budget.observe(lambda _: reader.observer(request), bridge)
        except Exception:
            # Fixed unavailability; external exception paths/messages do not
            # escape and a failed read is never retried or changed into OK.
            value = None
        values[name] = value
    if ForgeRead.RUNTIME in reader.qualified and ForgeRead.ENGINE in reader.qualified:
        qualified.add('runtime')
    # Failed collection mappings are empty rather than arbitrary objects.
    for name in ('containers', 'models', 'listeners'):
        if name in values and type(values[name]) is not dict:
            values[name] = {}
    return ForgeObservation(qualified=frozenset(qualified), **values)


@bounded('invalid-observation')
def diagnose(receipt, policy, rules, *, now, reader=None, budget=None):
    if getattr(receipt, 'platform', None) != 'linux':
        raise DiagError('invalid-receipt')
    return forge_rows(receipt, policy, observe(reader, budget=budget), rules, now=now)
