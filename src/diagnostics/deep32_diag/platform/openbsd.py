"""Closed OpenBSD observation boundary; no native helper is qualified here.

The install owner must separately supply and qualify its fixed doas helper,
socket permissions, exact read paths, process cancellation and pledge/unveil.
This class cannot launch commands, open sockets, bind listeners or select a
path, URL, service name or repair action.  Synthetic qualification is not host
proof.  Default construction refuses every observation.
"""

from dataclasses import dataclass
from enum import Enum

from ..errors import DiagError, bounded
from ..runner import Budget, MAX_LOCAL


class Operation(Enum):
    CORE_RUNNING = 'rcctl-check-d32core'
    GATEWAY_RUNNING = 'rcctl-check-d32edge'
    ACCEL_RUNNING = 'rcctl-check-d32accel'
    TAILSCALE_RUNNING = 'rcctl-check-tailscaled'
    TIME_RUNNING = 'rcctl-check-ntpd'
    ENGINE_STATUS = 'existing-core-unix-status'
    GATEWAY_STATUS = 'existing-local-mcp-observe'
    ATTESTOR_SNAPSHOT = 'existing-forwarder-snapshot'
    IMPORT_REGISTRY = 'existing-import-registry'
    IMPORT_POINTER = 'existing-import-kb-pointer'


@dataclass(frozen=True)
class Request:
    operation: Operation
    deadline: float
    max_bytes: int = MAX_LOCAL


class OpenBSDObservations:
    """Bounded fixed operations over an explicitly qualified owner callback.

    `observer(Request)` returns bool for process checks, bytes for existing
    JSON files/status, an mcp.StatusObservation for GATEWAY_STATUS, and str for
    the already root-confined relative pointer.  It does not return raw command
    output.  The callback owns in-flight cancellation, fixed resource slots and
    the four-observation global scheduler.  A callback's exception never crosses
    this boundary.  Callers use Workstation pure extractors before reporting.
    """
    @bounded('invalid-observation')
    def __init__(self, observer=None, *, qualified=False, budget=None):
        if type(qualified) is not bool or (observer is not None and not callable(observer)):
            raise DiagError('invalid-observation')
        self.__observer = observer
        self.__qualified = qualified
        self.__budget = budget or Budget()

    @bounded('unavailable')
    def observe(self, operation):
        if type(operation) is not Operation:
            raise DiagError('unsafe-command')
        if not self.__qualified or self.__observer is None:
            raise DiagError('unqualified-command')
        request = Request(operation, self.__budget.deadline())
        # Reuse the core's shared semaphore/deadline enforcement without giving
        # this capability an arbitrary command argument or executable slot.
        from ..runner import CommandRequest
        bridge = CommandRequest((), request.deadline, request.max_bytes)
        result = self.__budget.observe(lambda _: self.__observer(request), bridge)
        if operation in (Operation.CORE_RUNNING, Operation.GATEWAY_RUNNING, Operation.ACCEL_RUNNING,
                         Operation.TAILSCALE_RUNNING, Operation.TIME_RUNNING):
            if type(result) is not bool:
                raise DiagError('invalid-observation')
        elif operation is Operation.GATEWAY_STATUS:
            from ..mcp import StatusObservation
            if type(result) is not StatusObservation:
                raise DiagError('invalid-observation')
        elif operation is Operation.IMPORT_POINTER:
            if type(result) is not str or len(result) > 511:
                raise DiagError('invalid-observation')
        elif type(result) is not bytes or len(result) > request.max_bytes:
            raise DiagError('invalid-observation')
        return result
