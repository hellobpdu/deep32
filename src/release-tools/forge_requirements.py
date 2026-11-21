#!/usr/bin/env python3
"""Build and validate the signed release's Forge prerequisites asset."""
import argparse
from datetime import date
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile

ASSET = 'FORGE-PREREQUISITES.json'
FORMAT = 'deep32-forge-prerequisites/1'
RELEASE = '0.1.0-alpha.2'
DRIVER_KEYS = ('tei_minimum_driver', 'cuda_minimum_driver', 'mineru_minimum_driver')
WINDOWS_DRIVER_KEY = 'windows_cuda13_driver_build'
DESKTOP_FIELDS = {'backend', 'editions', 'version_min', 'engine_version_min'}
# The edition families forge_preflight_windows.windows_identity reports.
WINDOWS_EDITIONS = ('education', 'enterprise', 'home', 'pro')
# A Windows recipe states floors. They must admit the one Windows setup the alpha.2 evidence exercised
# (build 26200.9974 Pro, Docker Desktop 4.99.0, engine 29.9.3) and may not go below what that evidence covers:
# build 26100 (24H2, which shares its servicing branch with the tested 25H2 build), the tested Desktop release
# and the tested engine's major version.
WINDOWS_EVIDENCE_HOST = {'build': 26200, 'edition': 'pro', 'version': (4, 99, 0), 'engine_version': (29, 9, 3)}
WINDOWS_LOWEST_FLOORS = {'build': 26100, 'version': (4, 99, 0), 'engine_version': (29, 0, 0)}
MINIMUM_VRAM_MIB = 10752  # install/matrix.json, gpu_mem_tiers id 11 (10.5 GiB)
VRAM_POLICY = 'C1-install-matrix-tier-11'
LIMIT = 65536
# An owner waiver stands in for a passed lifecycle record on one recipe of one release.
WAIVER_FIELDS = {'granted_by', 'granted_on', 'release', 'recipe_id', 'not_run', 'evidence'}
WAIVER_GRANTOR = 'release-owner'
CITED_NAME = r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}'
CITED_LIMIT = 8 * 1024 * 1024


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate Forge prerequisite field.')
        result[key] = value
    return result


def parse(raw, limit=LIMIT):
    if not 0 < len(raw) <= limit:
        raise ValueError('A Forge prerequisite input exceeds its size limit.')
    return json.loads(raw.decode('ascii'), object_pairs_hook=unique_pairs)


def digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def version_tuple(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Forge prerequisites require resolved numeric NVIDIA driver floors.')
    match = re.fullmatch(r'v?([0-9]+(?:\.[0-9]+){1,3})(?:[-+][A-Za-z0-9_.-]+)?', value.strip())
    if not match:
        raise ValueError('Forge prerequisites require resolved numeric NVIDIA driver floors.')
    parts = tuple(int(field) for field in match[1].split('.'))
    return parts + (0,) * (4 - len(parts))


def release_version(value):
    """Return a plain X.Y.Z Docker Desktop or Engine version as integers, or None."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})', value)
    return None if match is None else tuple(int(field) for field in match.groups())


def windows_floors(row):
    """Return the checked floors of one Windows recipe: build, editions, Desktop and Engine versions."""
    desktop = row['desktop']
    if not isinstance(desktop, dict) or set(desktop) != DESKTOP_FIELDS:
        raise ValueError('The Windows recipe needs exactly its backend, editions and Docker Desktop and Engine floors.')
    build = re.fullmatch(r'([1-9][0-9]{4,5})\+', row['os_version'])
    editions = desktop['editions']
    if (build is None or desktop['backend'] != 'wsl2' or not isinstance(editions, list) or not editions or
            any(not isinstance(item, str) or item not in WINDOWS_EDITIONS for item in editions) or
            editions != sorted(set(editions))):
        raise ValueError('Use a Windows 11 client build floor, known editions and the Docker Desktop WSL2 engine.')
    floors = (int(build[1]), release_version(desktop['version_min']), release_version(desktop['engine_version_min']))
    if None in floors:
        raise ValueError('The Windows recipe needs numeric Docker Desktop and Engine version floors.')
    keys = ('build', 'version', 'engine_version')
    if (any(floor < WINDOWS_LOWEST_FLOORS[key] for floor, key in zip(floors, keys)) or
            any(floor > WINDOWS_EVIDENCE_HOST[key] for floor, key in zip(floors, keys)) or
            WINDOWS_EVIDENCE_HOST['edition'] not in editions):
        raise ValueError('The Windows recipe floors must admit the tested host and stay within what its evidence covers.')
    return floors[0], tuple(editions), floors[1], floors[2]


def windows_admits(row, build, edition, desktop_version, engine_version):
    """Whether one Windows recipe's floors admit a host; the versions are integer tuples."""
    floor, editions, desktop_floor, engine_floor = windows_floors(row)
    return (type(build) is int and build >= floor and edition in editions and
            desktop_version >= desktop_floor and engine_version >= engine_floor)


def windows_refusal(rows, journey):
    """Name the floors a refused Windows host must reach, when exactly one recipe covers its journey."""
    rows = [row for row in rows if (row['os_id'], row['architecture'], row['journey']) == ('windows', 'amd64', journey)]
    if len(rows) != 1:
        return 'Obtain an accepted signed Forge recipe for this Windows journey and Docker Desktop.'
    build, editions, desktop, engine = windows_floors(rows[0])
    names = [name for key, name in (('home', 'Home'), ('pro', 'Pro'), ('enterprise', 'Enterprise'),
                                    ('education', 'Education')) if key in editions]
    named = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' or ' + names[-1]
    return (f'Use Windows 11 build {build} or later ({named}), Docker Desktop {".".join(map(str, desktop))} or later '
            f'and Docker Engine {".".join(map(str, engine))} or later on the WSL 2 backend.')


def validate_waiver(waiver, row, release):
    """Check one owner waiver against the recipe row and release that carry it."""
    if not isinstance(waiver, dict) or set(waiver) != WAIVER_FIELDS:
        raise ValueError('An owner waiver needs exactly its grantor, date, release, recipe, omitted runs and evidence.')
    if (waiver['granted_by'] != WAIVER_GRANTOR or waiver['granted_on'] != row['accepted_on']
            or waiver['release'] != release or waiver['recipe_id'] != row['id']):
        raise ValueError('The owner waiver names another grantor, date, release or recipe.')
    omitted, evidence = waiver['not_run'], waiver['evidence']
    if (not isinstance(omitted, list) or not 1 <= len(omitted) <= 16 or
            any(not isinstance(item, str) or re.fullmatch(r'[!-~](?:[ -~]{0,298}[!-~])?', item) is None
                for item in omitted) or len(set(omitted)) != len(omitted)):
        raise ValueError('The owner waiver must state in plain text what was not run.')
    if (not isinstance(evidence, list) or not 1 <= len(evidence) <= 16 or
            any(not isinstance(item, dict) or set(item) != {'file', 'sha256'} or
                not isinstance(item['file'], str) or re.fullmatch(CITED_NAME, item['file']) is None or
                not digest(item['sha256']) for item in evidence) or
            len({item['file'] for item in evidence}) != len(evidence)):
        raise ValueError('The owner waiver must cite each evidence file once, by name and SHA-256.')


def waiver_notice(row):
    """Return the one line a preflight shows for a waived recipe, or None for a passed one."""
    waiver = row.get('waiver')
    if waiver is None:
        return None
    return ('Note: this host type is accepted for ' + waiver['release'] + ' under an owner waiver dated '
            + waiver['granted_on'] + ' (recipe ' + row['id'] + '). I have not yet run a full install on it; '
            + ASSET + ' lists what was not run.')


def validate_recipes(records, release=RELEASE):
    keys = {'id', 'accepted_on', 'os_id', 'os_version', 'architecture', 'journey', 'recipe_sha256'}
    if not isinstance(records, list) or not 1 <= len(records) <= 32:
        raise ValueError('Supply reviewed Forge recipe acceptance records.')
    identifiers, hosts, windows_hosts = set(), set(), set()
    has_windows = False
    for row in records:
        if not isinstance(row, dict):
            raise ValueError('Invalid Forge recipe acceptance record.')
        windows = row.get('os_id') == 'windows'
        # Exactly one of a passed lifecycle record or an owner waiver accepts a recipe.
        waived = 'waiver' in row
        expected = keys | {'waiver' if waived else 'acceptance_sha256'} | ({'desktop'} if windows else set())
        if set(row) != expected or any(not isinstance(row[key], str) for key in keys):
            raise ValueError('Invalid Forge recipe acceptance record.')
        if (re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,95}', row['id']) is None or
                re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,30}', row['os_id']) is None or
                (not windows and re.fullmatch(r'[0-9][a-zA-Z0-9._-]{0,30}', row['os_version']) is None) or
                row['architecture'] != 'amd64' or row['journey'] not in ('J1', 'J2', 'J4') or
                not digest(row['recipe_sha256']) or (not waived and not digest(row['acceptance_sha256'])) or
                re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', row['accepted_on']) is None):
            raise ValueError('The accepted recipe must name an exact OS, architecture, journey and evidence digests.')
        date.fromisoformat(row['accepted_on'])
        if waived:
            validate_waiver(row['waiver'], row, release)
        host = (row['os_id'], row['os_version'], row['architecture'], row['journey'])
        if windows:
            # Floors are open upward, so two Windows rows for one journey and edition would both match a host.
            covered = {(row['architecture'], row['journey'], edition) for edition in windows_floors(row)[1]}
            if covered & windows_hosts:
                raise ValueError('Duplicate approved Forge host recipe.')
            windows_hosts |= covered
            has_windows = True
        if row['id'] in identifiers or host in hosts:
            raise ValueError('Duplicate approved Forge host recipe.')
        identifiers.add(row['id'])
        hosts.add(host)
    return has_windows


def validate(raw, *, capability_sha256, pins_sha256, release, storage_evidence_sha256=None):
    """Validate data after the release root authenticated the raw companion bytes."""
    document = parse(raw)
    expected = {'format', 'release', 'capability_sha256', 'pins_sha256',
                'recipes', 'driver_floors', 'minimum_vram_mib', 'vram_policy'}
    if storage_evidence_sha256 is not None:
        expected.add('storage_evidence_sha256')
    if (not isinstance(document, dict) or set(document) - {WINDOWS_DRIVER_KEY} != expected
            or document['format'] != FORMAT):
        raise ValueError('Unsupported Forge prerequisite asset format.')
    windows = validate_recipes(document['recipes'], release)
    if windows != (WINDOWS_DRIVER_KEY in document):
        raise ValueError('Windows Forge recipes require their signed Windows CUDA driver floor.')
    if windows:
        version_tuple(document[WINDOWS_DRIVER_KEY])
    if (release != RELEASE or document['release'] != release or
            not digest(capability_sha256) or document['capability_sha256'] != capability_sha256 or
            not digest(pins_sha256) or document['pins_sha256'] != pins_sha256):
        raise ValueError('Forge prerequisites differ from the signed release capability or pin register.')
    if storage_evidence_sha256 is not None and (
            not digest(storage_evidence_sha256) or document['storage_evidence_sha256'] != storage_evidence_sha256):
        raise ValueError('Forge prerequisites differ from the signed storage measurements.')
    floors = document['driver_floors']
    if not isinstance(floors, dict) or set(floors) != set(DRIVER_KEYS):
        raise ValueError('The signed Forge prerequisite asset needs all three driver floors.')
    for floor in floors.values():
        version_tuple(floor)
    if (type(document['minimum_vram_mib']) is not int or document['minimum_vram_mib'] != MINIMUM_VRAM_MIB
            or document['vram_policy'] != VRAM_POLICY):
        raise ValueError('Use the C1 full Forge role and memory threshold.')
    return document


def cite(recipes, recipe_files, evidence_files):
    """Fill a reviewed recipes input with the digests of the real files it names."""
    used = set()

    def cited(kind, files, name):
        if not isinstance(name, str) or name not in files:
            raise ValueError('Supply every ' + kind + ' file the recipes input names.')
        used.add((kind, name))
        return files[name]

    for row in (recipes if isinstance(recipes, list) else ()):
        if not isinstance(row, dict):
            continue
        if 'recipe_file' in row:
            if 'recipe_sha256' in row:
                raise ValueError('Give a recipe as one file name or one digest.')
            row['recipe_sha256'] = cited('recipe', recipe_files, row.pop('recipe_file'))
        waiver = row.get('waiver')
        if isinstance(waiver, dict) and 'evidence' in waiver:
            names = waiver['evidence']
            if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
                raise ValueError('Name the waiver evidence files; the builder hashes the real files.')
            waiver['evidence'] = [{'file': name, 'sha256': cited('evidence', evidence_files, name)} for name in names]
    if len(used) != len(recipe_files) + len(evidence_files):
        raise ValueError('A supplied recipe or evidence file is named by no recipe.')
    return recipes


def build(capability_raw, pins_raw, recipes_raw, *, recipe_files=None, evidence_files=None):
    path = Path(__file__).with_name('capabilities.py')
    spec = importlib.util.spec_from_file_location('_forge_capability_input', path)
    capabilities = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(capabilities)
    capability = parse(capability_raw, 8 * 1024 * 1024)
    capabilities.validate_manifest(capability)
    pins_hash = hashlib.sha256(pins_raw).hexdigest()
    if pins_hash != capability['pins_sha256']:
        raise ValueError('The resolved pin register differs from the capability manifest.')
    pins = parse(pins_raw, 8 * 1024 * 1024)
    if (not isinstance(pins, dict) or not isinstance(pins.get('runtime'), dict)
            or not set(DRIVER_KEYS) <= set(pins['runtime'])):
        raise ValueError('Use the resolved runtime pins from the verified core producer.')
    recipes = cite(parse(recipes_raw), recipe_files or {}, evidence_files or {})
    windows = validate_recipes(recipes, capability['release'])
    cap_hash = hashlib.sha256(capability_raw).hexdigest()
    document = dict(format=FORMAT, release=capability['release'], capability_sha256=cap_hash,
                    pins_sha256=pins_hash, recipes=sorted(recipes, key=lambda row: row['id']),
                    driver_floors={key: pins['runtime'][key] for key in DRIVER_KEYS},
                    minimum_vram_mib=MINIMUM_VRAM_MIB, vram_policy=VRAM_POLICY)
    if windows:
        floor = pins['runtime'].get(WINDOWS_DRIVER_KEY)
        version_tuple(floor)
        document[WINDOWS_DRIVER_KEY] = floor
    storage_digest = capability.get('storage_evidence', {}).get('sha256')
    if storage_digest is not None:
        document['storage_evidence_sha256'] = storage_digest
    raw = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('ascii')
    validate(raw, capability_sha256=cap_hash, pins_sha256=pins_hash, release=capability['release'],
             storage_evidence_sha256=storage_digest)
    return raw


def read(path, limit=LIMIT):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('A Forge prerequisite input exceeds its size limit.')
    return raw


def hashed(paths):
    """Map the plain name of each real file to its SHA-256."""
    files = {}
    for path in paths:
        raw = read(path, CITED_LIMIT)
        if not raw or path.name in files or re.fullmatch(CITED_NAME, path.name) is None:
            raise ValueError('Supply distinct, plainly named, non-empty recipe and evidence files.')
        files[path.name] = hashlib.sha256(raw).hexdigest()
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('capabilities', 'pins', 'recipes', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--recipe-file', action='append', default=[], type=Path,
                        help='a host recipe document that the recipes input names with recipe_file; repeatable')
    parser.add_argument('--evidence', action='append', default=[], type=Path,
                        help='an evidence file that an owner waiver in the recipes input names; repeatable')
    args = parser.parse_args(argv)
    try:
        raw = build(read(args.capabilities, 8 * 1024 * 1024), read(args.pins, 8 * 1024 * 1024), read(args.recipes),
                    recipe_files=hashed(args.recipe_file), evidence_files=hashed(args.evidence))
        descriptor, temporary = tempfile.mkstemp(prefix='.forge-prerequisites-', dir=args.output.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, args.output)
        finally:
            Path(temporary).unlink(missing_ok=True)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        if isinstance(exc, OSError):
            parser.exit(1, 'Forge prerequisites refused: check the reviewed input files and choose a new output file.\n')
        parser.exit(1, 'Forge prerequisites refused: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
