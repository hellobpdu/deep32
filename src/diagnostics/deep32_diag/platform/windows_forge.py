"""Native Windows Forge read boundary; never WSL/distro launch or enumeration."""

from dataclasses import dataclass
from pathlib import PureWindowsPath

from ..errors import DiagError, bounded
from ..forge import forge_rows
from .linux_forge import ForgeRead, ForgeReader, ForgeRequest, observe


@dataclass(frozen=True, repr=False)
class BackingPathObservation:
    """Read-only facts for each existing ancestor, supplied by native owner."""
    path: str
    exists: bool
    directory: bool
    reparse: bool
    local_fixed_volume: bool


@bounded('invalid-observation')
def backing_path_safe(recorded_path, ancestors):
    """Reject network/device/reparse backing paths before any native lookup.

    No current-directory, environment default, resolved alias or inferred drive
    mapping is accepted. The owner observes the exact retained backing location
    and every ancestor using read-only metadata. Desktop internal free space is
    still a separate required observation and budget.
    """
    if type(recorded_path) is not str or len(recorded_path) > 32767:
        return False
    if recorded_path.startswith(('\\\\', '//', '\\??\\')):
        return False
    pieces = recorded_path.replace('/', '\\').split('\\')[1:]
    if any(piece in ('.', '..') or piece.endswith(('.', ' ')) for piece in pieces if piece):
        return False
    path = PureWindowsPath(recorded_path)
    if (not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':'
            or not path.drive[0].isascii() or not path.drive[0].isalpha()
            or any(part in ('.', '..') or ':' in part for part in path.parts[1:])):
        return False
    required = (path, *path.parents)
    if not isinstance(ancestors, tuple) or len(ancestors) != len(required):
        return False
    for expected, current in zip(required, ancestors):
        if (type(current) is not BackingPathObservation or type(current.path) is not str
                or PureWindowsPath(current.path) != expected or current.exists is not True
                or current.directory is not True or current.reparse is not False
                or current.local_fixed_volume is not True):
            return False
    return True


@bounded('invalid-observation')
def diagnose(receipt, policy, rules, *, now, reader=None, budget=None,
             protected_pairing_qualified=False):
    """Use only the recorded Desktop Linux-engine pipe through the reader.

    Windows readers must additionally qualify decrypt-only credentials and
    verify-only host_security.windows_security, passive native TCP ownership,
    recorded backing-directory checks and separate internal/backing capacity.
    A durable pairing record cannot qualify a missing protected console adapter.
    This module supplies no such native qualification, repair or ACL writer.
    """
    if getattr(receipt, 'platform', None) != 'windows':
        raise DiagError('invalid-receipt')
    return forge_rows(receipt, policy, observe(reader, budget=budget), rules, now=now,
                      protected_windows_pairing_qualified=protected_pairing_qualified)
