#!/usr/bin/env python3
"""Measured storage arithmetic for the host-requirements owner.

Internal measurement interchange, not a frozen public capability manifest.
The caller supplies a digest and release/selection from the authenticated release
boundary, plus current read-only backend observations. A self-reported digest or
an install receipt alone is not authority. This module neither measures Docker
nor runs a process, signs, downloads, allocates, prunes or repairs anything.

Each allocation is one physically charged unit (not an image's logical Size).
Compressed content and unpacked snapshots need distinct units where both persist.
Phase arrays describe simultaneous allocations, including copy/import overlap,
old generations, jobs and rollback. R and B must be explicitly zero if absent.
H is explicit free headroom. Existing bytes are credited only with matching
identity, filesystem, profile and allocation; the caller verifies those bytes.
No future deletion is credited. Desktop backing growth is separately measured;
it cannot be inferred from the virtual disk's logical maximum or internal free.

Activation in installers remains gated on producer measurements, authenticated
metadata/selection and a qualified pre-load Desktop observation adapter. Legacy
host gates remain in force when these inputs are unavailable.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

SCHEMA = 'deep32-storage-measurements/1'
LIMIT = 4 * 1024 * 1024
MAX_BYTES = 2 ** 63 - 1
CLASSES = {'turing', 'ampere80', 'ampere86', 'ada', 'hopper', 'blackwell100', 'blackwell120'}
CATEGORIES = {'D', 'M', 'K', 'U', 'T', 'R', 'B', 'H'}
OPERATIONS = {'download', 'install', 'upgrade', 'rollback', 'resume', 'run', 'ingestion', 'full'}
HEX = re.compile(r'[0-9a-f]{64}\Z')
TOKEN = re.compile(r'[a-z0-9][a-z0-9_.-]{0,95}\Z')
# internal preflight refusal sentence for lead review.
REFUSAL = 'Install with authenticated measured storage requirements and current owning-filesystem observations.'


def refuse():
    raise ValueError(REFUSAL)


def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        refuse()


def token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        refuse()
    return value


def count(value):
    if type(value) is not int or not 0 <= value <= MAX_BYTES:
        refuse()
    return value


def digest(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        refuse()
    return value


def pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            refuse()
        result[key] = value
    return result


def selection(value):
    if (not isinstance(value, list) or not value or
            any(not isinstance(x, str) for x in value) or value != sorted(set(value)) or
            not {'core', 'embedder'} <= set(value) or
            set(value) - {'core', 'embedder', 'ingestion', 'full'}):
        refuse()
    return value


def backend(value):
    fields(value, 'engine store')
    if value['engine'] not in ('linux', 'desktop-wsl2'):
        refuse()
    token(value['store'])
    return value


def load(document, expected_sha256):
    """Hash bounded exact bytes against caller-verified metadata before parsing."""
    digest(expected_sha256)
    if (not isinstance(document, bytes) or len(document) > LIMIT or
            hashlib.sha256(document).hexdigest() != expected_sha256):
        refuse()
    try:
        value = json.loads(document, object_pairs_hook=pairs,
                           parse_constant=lambda _: refuse())
        fields(value, 'schema release profiles')
        if value['schema'] != SCHEMA:
            refuse()
        digest(value['release'])
        if not isinstance(value['profiles'], list) or not 1 <= len(value['profiles']) <= 256:
            refuse()
        seen, ids = set(), set()
        for profile in value['profiles']:
            validate_profile(profile)
            key = (tuple(profile['capabilities']), profile['card_class'],
                   profile['backend']['engine'], profile['backend']['store'],
                   profile['operation'], tuple(sorted(profile['filesystems'].items())))
            if key in seen or profile['id'] in ids:
                refuse()
            seen.add(key)
            ids.add(profile['id'])
        return value
    except (UnicodeError, RecursionError, TypeError, KeyError, ValueError):
        raise ValueError(REFUSAL) from None


def validate_profile(profile):
    fields(profile, 'id capabilities card_class backend operation filesystems phases allocations running_floor')
    token(profile['id'])
    selection(profile['capabilities'])
    if profile['card_class'] not in CLASSES or profile['operation'] not in OPERATIONS:
        refuse()
    desktop = backend(profile['backend'])['engine'] == 'desktop-wsl2'
    domains = {'download', 'data', 'docker'} | ({'backing'} if desktop else set())
    if not isinstance(profile['filesystems'], dict) or set(profile['filesystems']) != domains:
        refuse()
    for name in profile['filesystems'].values():
        token(name)
        if name == 'unknown':
            refuse()
    phases = profile['phases']
    if (not isinstance(phases, list) or not 1 <= len(phases) <= 32 or
            any(not isinstance(x, str) for x in phases) or len(set(phases)) != len(phases)):
        refuse()
    for phase in phases:
        token(phase)
    floors = profile['running_floor']
    if not isinstance(floors, dict) or set(floors) != domains:
        refuse()
    for floor in floors.values():
        count(floor)
    rows = profile['allocations']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8192:
        refuse()
    ids, units, categories = set(), set(), set()
    for row in rows:
        fields(row, 'id unit identity domain category bytes')
        token(row['id'])
        token(row['unit'])
        digest(row['identity'])
        if (row['id'] in ids or row['unit'] in units or
                row['domain'] not in domains or row['category'] not in CATEGORIES or
                not isinstance(row['bytes'], list) or len(row['bytes']) != len(phases)):
            refuse()
        ids.add(row['id'])
        units.add(row['unit'])
        categories.add(row['category'])
        for size in row['bytes']:
            count(size)
    if categories != CATEGORIES:
        refuse()


def observe_volume(path):
    """Observe the local owning volume, including not-yet-created destinations.

    Reject all linked/reparse ancestors before any disk query. Filesystem type is
    required from the qualified adapter on POSIX (stat does not expose it), so the
    default POSIX observation cannot activate a measured profile by itself.
    """
    try:
        return _local_volume(path)
    except (OSError, TypeError, ValueError, OverflowError):
        raise ValueError(REFUSAL) from None


def _local_volume(path):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        refuse()
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or part.is_junction() or
                getattr(info, 'st_file_attributes', 0) & 0x400 or
                not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
            refuse()
    while not path.exists():
        path = path.parent
    if path.is_file():
        path = path.parent
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        mount, volume, filesystem = (ctypes.create_unicode_buffer(32768) for _ in range(3))
        if not kernel.GetVolumePathNameW(str(path), mount, len(mount)):
            refuse()
        if not kernel.GetVolumeNameForVolumeMountPointW(mount.value, volume, len(volume)):
            refuse()
        if not kernel.GetVolumeInformationW(mount.value, None, 0, None, None, None,
                                           filesystem, len(filesystem)):
            refuse()
        identity, kind = volume.value, filesystem.value.lower()
    else:
        identity, kind = str(path.stat().st_dev), 'unknown'
    return {'volume': identity, 'filesystem': kind, 'free_bytes': shutil.disk_usage(path).free}


def observation(value):
    fields(value, 'volume filesystem free_bytes')
    if (not isinstance(value['volume'], str) or not value['volume'] or len(value['volume']) > 512 or
            any(ord(char) < 32 or ord(char) == 127 for char in value['volume'])):
        refuse()
    token(value['filesystem'])
    if value['filesystem'] == 'unknown':
        refuse()
    count(value['free_bytes'])
    return value


def plan(document, *, expected_sha256, expected_release, capabilities, card_class,
         observed_backend, operation, locations, observe=observe_volume,
         present=(), desktop=None, phase=None):
    """Return deterministic per-volume peaks and running/next-operation verdicts.

    locations are download/data/docker (Linux), or download/data/backing
    (Desktop). Desktop's separate current internal observation is compulsory.
    present rows: profile/allocation/identity/volume/bytes, verified by caller.
    phase=None checks the entire operation; a phase checks that phase and later
    recovery phases. Earlier allocations still present are never freed here.
    """
    value = load(document, expected_sha256)
    digest(expected_release)
    selection(capabilities)
    backend(observed_backend)
    if (value['release'] != expected_release or not isinstance(card_class, str) or card_class not in CLASSES or
            not isinstance(operation, str) or operation not in OPERATIONS or not isinstance(locations, dict)):
        refuse()
    is_desktop = observed_backend['engine'] == 'desktop-wsl2'
    names = {'download', 'data', 'backing'} if is_desktop else {'download', 'data', 'docker'}
    if set(locations) != names:
        refuse()
    try:
        samples = {key: observation(observe(locations[key])) for key in sorted(locations)}
    except (OSError, TypeError, ValueError, OverflowError):
        raise ValueError(REFUSAL) from None
    if is_desktop:
        samples['docker'] = observation(desktop)
    elif desktop is not None:
        refuse()
    candidates = [p for p in value['profiles'] if
                  p['capabilities'] == capabilities and p['card_class'] == card_class and
                  p['backend'] == observed_backend and p['operation'] == operation and
                  p['filesystems'] == {k: v['filesystem'] for k, v in samples.items()}]
    if len(candidates) != 1:
        refuse()
    profile = candidates[0]
    if phase is not None and (not isinstance(phase, str) or phase not in profile['phases']):
        refuse()
    start = 0 if phase is None else profile['phases'].index(phase)
    phases = profile['phases'][start:]
    # Docker's internal virtual disk must never collapse into a host volume.
    domain_keys = {name: ('internal' if is_desktop and name == 'docker' else 'host',
                          sample['volume']) for name, sample in samples.items()}
    groups = {}
    for domain, sample in sorted(samples.items()):
        key = domain_keys[domain]
        if key not in groups:
            groups[key] = dict(domains=[], filesystem=sample['filesystem'],
                               free_bytes=sample['free_bytes'], running_floor_bytes=0,
                               phase_bytes={p: 0 for p in phases}, headroom_bytes={p: 0 for p in phases})
        group = groups[key]
        if group['filesystem'] != sample['filesystem']:
            refuse()
        # Capacity may change while aliases are observed. Use the lowest current
        # reading, without charging this volume once for each path alias.
        group['free_bytes'] = min(group['free_bytes'], sample['free_bytes'])
        group['domains'].append(domain)
        group['running_floor_bytes'] += profile['running_floor'][domain]
    allocations = {row['id']: row for row in profile['allocations']}
    credits = {}
    if not isinstance(present, (list, tuple)):
        refuse()
    for row in present:
        fields(row, 'profile allocation identity volume bytes')
        token(row['allocation'])
        allocation = allocations.get(row['allocation'])
        if (allocation is None or row['allocation'] in credits or row['profile'] != profile['id'] or
                row['identity'] != allocation['identity'] or
                row['volume'] != samples[allocation['domain']]['volume'] or allocation['category'] == 'H' or
                count(row['bytes']) > max(allocation['bytes'])):
            refuse()
        credits[row['allocation']] = row['bytes']
    for row in profile['allocations']:
        group = groups[domain_keys[row['domain']]]
        for index, name in enumerate(profile['phases'][start:], start):
            remaining = max(0, row['bytes'][index] - credits.get(row['id'], 0))
            group['phase_bytes'][name] += remaining
            if row['category'] == 'H':
                group['headroom_bytes'][name] += remaining
    rows = []
    for (kind, volume), group in sorted(groups.items()):
        peak = max(group['phase_bytes'].values())
        required = max(peak, group['running_floor_bytes'])
        state = ('FAIL' if group['free_bytes'] < group['running_floor_bytes'] else
                 'WARN' if group['free_bytes'] < required else 'OK')
        rows.append(dict(kind=kind, volume=volume, **group,
                         peak_additional_bytes=peak, required_free_bytes=required, state=state))
    return dict(schema='deep32-storage-plan/1', release=expected_release,
                measurements_sha256=expected_sha256, profile=profile['id'], phases=phases,
                volumes=rows, fits=all(row['state'] == 'OK' for row in rows))


def require(report):
    """Pre-install refusal; diagnostics may instead consume WARN/FAIL separately."""
    if not report['fits']:
        # new non-interactive refusal sentence for lead review.
        raise ValueError('Install additional storage on the reported owning filesystem before this operation.')
    return report
