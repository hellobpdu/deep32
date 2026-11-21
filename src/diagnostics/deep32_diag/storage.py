"""Pure per-filesystem reservation checks; paths and native getters stay private."""

from dataclasses import dataclass, field
import re

from .errors import DiagError, bounded
from .models import State
from .report import Reason, row


@dataclass(frozen=True, repr=False)
class StorageBudget:
    alias: str
    filesystem: str = field(repr=False)
    running_bytes: int
    next_bytes: int


@dataclass(frozen=True, repr=False)
class StorageReading:
    free_bytes: int | None
    backing_verified: bool = True


@bounded('invalid-observation')
def storage_rows(budgets, readings, *, role):
    """Sum distinct reservations on a shared filesystem and charge free bytes once.

    `next_bytes` is the complete running/import/rollback requirement, supplied by
    the authenticated owner. It is not an extra amount to add to running_bytes.
    Windows Docker backing-path qualification is an owning adapter observation.
    An absent/unreadable filesystem cannot be healthy. No stat or path walk runs.
    """
    if role not in ('workstation', 'forge') or type(readings) is not dict:
        raise DiagError('invalid-observation')
    if not isinstance(budgets, (tuple, list)) or not 1 <= len(budgets) <= 32:
        raise DiagError('invalid-observation')
    groups, aliases = {}, set()
    for budget in budgets:
        if (type(budget) is not StorageBudget or type(budget.alias) is not str
                or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', budget.alias)
                or budget.alias in aliases or type(budget.filesystem) is not str
                or not 1 <= len(budget.filesystem) <= 256
                or any(type(n) is not int or not 0 <= n <= 2**63 - 1
                       for n in (budget.running_bytes, budget.next_bytes))
                or budget.next_bytes < budget.running_bytes):
            raise DiagError('invalid-observation')
        aliases.add(budget.alias)
        groups.setdefault(budget.filesystem, []).append(budget)
    result = []
    for group in sorted(groups.values(), key=lambda g: min(b.alias for b in g)):
        alias = min(b.alias for b in group)
        safe = sum(b.running_bytes for b in group)
        required = sum(b.next_bytes for b in group)
        if required > 2**63 - 1:
            raise DiagError('invalid-observation')
        reading = readings.get(group[0].filesystem)
        values = {'safe_running_bytes': safe, 'required_bytes': required}
        state = State.FAIL
        if reading is not None:
            if (type(reading) is not StorageReading or type(reading.backing_verified) is not bool
                    or (reading.free_bytes is not None and (type(reading.free_bytes) is not int
                        or not 0 <= reading.free_bytes <= 2**63 - 1))):
                raise DiagError('invalid-observation')
            if reading.free_bytes is not None:
                values['free_bytes'] = reading.free_bytes
                if reading.backing_verified:
                    state = (State.OK if reading.free_bytes >= required else
                             State.WARN if reading.free_bytes >= safe else State.FAIL)
        result.append(row('disk.' + alias, state,
                          Reason.PASSED if state is State.OK else Reason.UNAVAILABLE,
                          values, policy=role + '.disk'))
    return tuple(result)
