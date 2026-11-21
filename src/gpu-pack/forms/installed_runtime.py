#!/usr/bin/env python3
"""Retain selected verified models and a runtime kit, without saved images.

The caller authenticates the media and verifies/loads the image IDs first. This
local receipt records that decision; it is not a signature or a new trust root.
The caller also protects and locks the release directory and checks Docker IDs
on every start. Nothing here runs Docker, downloads, repairs or removes files.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

SCHEMA = 'deep32-installed-runtime/1'
MINERU_LAYOUT_SCHEMA = 'deep32-installed-runtime/2'
MINERU_LOCKS = PurePosixPath('models/mineru-home/models/.locks')
CLASSES = {'turing', 'ampere80', 'ampere86', 'ada', 'hopper', 'blackwell100', 'blackwell120'}
MODEL_PREFIXES = {'models/tei/', 'models/reranker/', 'models/mineru-home/'}
IMAGE_KEYS = {'TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE', 'CONVERTER_IMAGE',
              'BUILDER_IMAGE', 'QDRANT_IMAGE', 'FULL_IMAGE', 'MODEL_BRIDGE_IMAGE'}
ROLE_KEYS = {'reranker': 'RERANKER_IMAGE', 'door': 'DOOR_IMAGE', 'converter': 'CONVERTER_IMAGE',
             'builder': 'BUILDER_IMAGE', 'qdrant': 'QDRANT_IMAGE', 'full': 'FULL_IMAGE',
             'model_bridge': 'MODEL_BRIDGE_IMAGE'}
HEX = re.compile(r'[0-9a-f]{64}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
IDENTITY = re.compile(r'[0-9a-f]{64}:[0-9a-f]{64}\Z')
META = {'plan.json', 'receipt.json', 'PAYLOAD.json', 'PAYLOAD.SHA256', 'index.json'}


def _provision():
    spec = importlib.util.spec_from_file_location('retained_runtime_provision',
                                                  Path(__file__).with_name('provision.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def _safe(path, regular=False):
    for part in (path, *path.parents):
        info = part.lstat() if part.exists() or part.is_symlink() else None
        if (part.is_symlink() or part.is_junction() or info is not None and
                getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise ValueError('Restore a private retained runtime without linked paths.')
    if path.exists():
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('Restore regular retained runtime files.')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('Restore private retained runtime files without hard links.')
    if regular and (not path.is_file() or not stat.S_ISREG(path.lstat().st_mode)):
        raise ValueError('Restore a missing retained runtime file from the same verified media.')


def _digest(path):
    _safe(path, regular=True)
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError('Duplicate retained runtime field.')
        result[key] = value
    return result


def _json(path):
    _safe(path, regular=True)
    return json.loads(path.read_text(encoding='ascii'), object_pairs_hook=_pairs)


def _sync_directory(path):
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _atomic_json(path, value):
    _safe(path)
    temporary = path.with_name(path.name + '.deep32-new')
    _safe(temporary)
    with temporary.open('w', encoding='ascii', newline='\n') as stream:
        stream.write(json.dumps(value, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    _sync_directory(path.parent)


def _name(value):
    if (not isinstance(value, str) or not value or
            any(c in value for c in '\\:$\x00\r\n') or any(ord(c) < 32 for c in value) or
            PurePosixPath(value).is_absolute() or str(PurePosixPath(value)) != value or
            any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in value.split('/')) or
            any(re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
                for p in value.split('/'))):
        raise ValueError('Invalid retained runtime member name.')
    return value


def _records(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('Missing retained runtime inventory.')
    result = {}
    aliases = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256'}:
            raise ValueError('Invalid retained runtime inventory record.')
        name = _name(row['path'])
        if (name in result or name.casefold() in aliases or type(row['size']) is not int or row['size'] < 0 or
                not isinstance(row['sha256'], str) or HEX.fullmatch(row['sha256']) is None):
            raise ValueError('Invalid retained runtime file identity.')
        result[name] = row
        aliases.add(name.casefold())
    if list(result) != sorted(result):
        raise ValueError('Unsorted retained runtime inventory.')
    return result


def _inventory(directory):
    rows = _records(_json(directory / 'PAYLOAD.json'))
    _safe(directory / 'PAYLOAD.SHA256', regular=True)
    expected = ''.join(row['sha256'] + '  ' + name + '\n' for name, row in rows.items())
    if (directory / 'PAYLOAD.SHA256').read_text(encoding='ascii') != expected:
        raise ValueError('Retained payload inventory and checksum list differ.')
    return rows, _digest(directory / 'PAYLOAD.json') + ':' + _digest(directory / 'PAYLOAD.SHA256')


def _checked(path, row):
    if path.stat().st_size != row['size'] or _digest(path) != row['sha256']:
        raise ValueError('Restore the changed retained runtime file from the same verified media.')


def _tree(root, records, partial=False, empty_directory=None):
    _safe(root)
    directories = {parent.as_posix() for name in records for parent in PurePosixPath(name).parents}
    if empty_directory is not None:
        directories.add(empty_directory.as_posix())
        mountpoint = root / empty_directory
        _safe(mountpoint)
        if mountpoint.exists():
            if not mountpoint.is_dir() or any(mountpoint.iterdir()):
                raise ValueError('Restore the exact empty MinerU lock mount directory.')
        elif not partial:
            raise ValueError('Restore the missing MinerU lock mount directory.')
    if not root.exists():
        if partial:
            return
        raise ValueError('Restore the missing retained runtime directory.')
    for path in sorted(root.rglob('*')):
        _safe(path)
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            if name not in directories:
                raise ValueError('Unexpected retained runtime directory.')
        elif partial and name.endswith('.deep32-new') and name[:-11] in records:
            continue
        elif name not in records:
            raise ValueError('Unexpected retained runtime file.')
        else:
            _checked(path, records[name])
    if not partial:
        for name, row in records.items():
            _safe(root / name, regular=True)
            _checked(root / name, row)


def _prefixes(values):
    if (not isinstance(values, (list, tuple)) or not values or
            any(not isinstance(value, str) or value not in MODEL_PREFIXES for value in values) or
            len(set(values)) != len(values)):
        raise ValueError('Select exact supported model directories before retaining the runtime.')
    return sorted(values)


def _models(inventory, prefixes):
    selected = {name: row for name, row in inventory.items() if any(name.startswith(p) for p in prefixes)}
    if any(not any(name.startswith(p) for name in selected) for p in prefixes):
        raise ValueError('A selected model directory is absent from the verified inventory.')
    if any('.env' in PurePosixPath(name).name.lower() or
           PurePosixPath(name).suffix.lower() in {'.tar', '.zip', '.gz', '.xz', '.bz2', '.zst', '.7z'}
           for name in selected):
        raise ValueError('Saved archives and environment files are not retained model files.')
    return selected


def _images(images, selection):
    if (not isinstance(images, dict) or not images or not set(images) <= IMAGE_KEYS or
            any(not isinstance(value, str) or IMAGE.fullmatch(value) is None for value in images.values())):
        raise ValueError('Retain only exact verified selected Docker image IDs.')
    if selection is not None and (not isinstance(selection, str) or selection not in CLASSES):
        raise ValueError('Unsupported retained GPU class.')
    return dict(sorted(images.items()))


def _required_prefixes(images):
    required = {'TEI_IMAGE': 'models/tei/', 'RERANKER_IMAGE': 'models/reranker/',
                'CONVERTER_IMAGE': 'models/mineru-home/'}
    return sorted(prefix for image, prefix in required.items() if image in images)


def _empty_directory(schema, images, models):
    if schema == SCHEMA:
        return None
    name = MINERU_LOCKS.as_posix()
    folded = name.casefold()
    parent = MINERU_LOCKS.parent.as_posix() + '/'
    if ('CONVERTER_IMAGE' not in images or not any(path.startswith(parent) for path in models)
            or any(path.casefold() == folded or path.casefold().startswith(folded + '/') for path in models)):
        raise ValueError('The reserved MinerU lock mount must have a verified model parent and no inventoried members.')
    return MINERU_LOCKS


def _index(path, inventory, images, selection):
    row = inventory.get('images/index.json')
    if row is None:
        raise ValueError('The verified payload image index is required for retention.')
    _safe(path, regular=True)
    _checked(path, row)
    rows = _json(path)
    if not isinstance(rows, list):
        raise ValueError('Invalid retained image index.')
    selected = {}
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get('role'), str):
            raise ValueError('Invalid retained image index record.')
        role = item['role']
        key = 'TEI_IMAGE' if role == 'tei-' + str(selection) else ROLE_KEYS.get(role)
        if key not in images:
            continue
        if key in selected:
            raise ValueError('Duplicate selected retained image role.')
        archive = inventory.get(_name(item.get('path')))
        if archive is None or archive['sha256'] != item.get('sha256'):
            raise ValueError('Retained image archive record differs from the payload inventory.')
        # Older TEI indices bind a source digest rather than Docker's measured
        # config ID. In that format the protected install receipt records the
        # already verified measurement; it cannot create cryptographic proof.
        config = item.get('config_digest')
        if config is not None and config != images[key]:
            raise ValueError('Retained image ID differs from its verified index.')
        selected[key] = item
    required = set(images) - ({'TEI_IMAGE'} if selection is None else set())
    if set(selected) != required:
        raise ValueError('Missing selected retained image role.')


def _metadata(directory, partial=False):
    _safe(directory)
    if directory.exists():
        for path in directory.iterdir():
            _safe(path, regular=True)
            if path.name not in META and not (partial and path.name.endswith('.deep32-new') and
                                              path.name[:-11] in META):
                raise ValueError('Unexpected retained runtime metadata.')


def verify(release, expected_identity=None, expected_release=None):
    """Recheck installed bytes only; a local receipt is not authenticated media."""
    release = Path(release)
    _safe(release)
    directory = release / 'installed-runtime'
    _metadata(directory)
    receipt = _json(directory / 'receipt.json')
    fields = {'schema', 'identity', 'release', 'pins', 'images', 'selection', 'model_prefixes', 'files', 'kit'}
    if not isinstance(receipt, dict) or set(receipt) != fields or receipt['schema'] not in (SCHEMA, MINERU_LAYOUT_SCHEMA):
        raise ValueError('Restore the recorded retained runtime receipt.')
    if (not isinstance(receipt['identity'], str) or IDENTITY.fullmatch(receipt['identity']) is None or
            not isinstance(receipt['pins'], str) or HEX.fullmatch(receipt['pins']) is None):
        raise ValueError('Invalid retained runtime identity.')
    if expected_identity is not None and receipt['identity'] != expected_identity:
        raise ValueError('Retained runtime differs from the authenticated payload identity.')
    identifier = hashlib.sha256((receipt['identity'] + receipt['pins']).encode('ascii')).hexdigest()
    if receipt['release'] != identifier or expected_release is not None and identifier != expected_release:
        raise ValueError('Retained runtime differs from the recorded installation release.')
    inventory, identity = _inventory(directory)
    if identity != receipt['identity']:
        raise ValueError('Retained original payload inventory changed.')
    images = _images(receipt['images'], receipt['selection'])
    prefixes = _prefixes(receipt['model_prefixes'])
    if prefixes != _required_prefixes(images):
        raise ValueError('Retain exactly the complete model directories required by the selected image roles.')
    models = _models(inventory, prefixes)
    if _records(receipt['files']) != models:
        raise ValueError('Retained model selection differs from the original payload inventory.')
    _index(directory / 'index.json', inventory, images, receipt['selection'])
    kit = _records(receipt['kit'])
    if kit.get('pins.json', {}).get('sha256') != receipt['pins']:
        raise ValueError('Retained runtime pins differ from the install receipt.')
    if _json(directory / 'plan.json') != receipt:
        raise ValueError('Retained runtime transaction identity changed.')
    _tree(release / 'kit', kit)
    _tree(release / 'payload', models, empty_directory=_empty_directory(receipt['schema'], images, models))
    return receipt


def prepare(payload, kit, release, images, model_prefixes, *, selection=None):
    """Commit after complete selected copies; retry only the same transaction.

    Authentication, image archive verification and destination ownership are
    caller responsibilities. A previous generation is never modified.
    """
    payload, kit, release = map(Path, (payload, kit, release))
    for path in (payload, kit, release):
        _safe(path)
    if payload.resolve().is_relative_to(release.resolve()) or kit.resolve() == (release / 'kit').resolve():
        raise ValueError('Use the separate verified media to prepare retained runtime files.')
    provision = _provision()
    identity = provision.verified_payload(payload)
    inventory, original_identity = _inventory(payload.parent)
    if identity != original_identity:
        raise ValueError('Verified media changed during retained runtime preparation.')
    prefixes = _prefixes(model_prefixes)
    chosen = _images(images, selection)
    if prefixes != _required_prefixes(chosen):
        raise ValueError('Retain exactly the complete model directories required by the selected image roles.')
    models = _models(inventory, prefixes)
    schema = MINERU_LAYOUT_SCHEMA if 'CONVERTER_IMAGE' in chosen else SCHEMA
    empty_directory = _empty_directory(schema, chosen, models)
    _index(payload / 'images/index.json', inventory, chosen, selection)
    runtime = provision.runtime_kit(kit)
    members = runtime.runtime_members(kit)
    records = []
    for name, path in sorted(members.items()):
        _name(name)
        _safe(path, regular=True)
        records.append({'path': name, 'size': path.stat().st_size, 'sha256': _digest(path)})
    kit_records = _records(records)
    if 'pins.json' not in kit_records:
        raise ValueError('The verified runtime kit is missing its pins.')
    pins = kit_records['pins.json']['sha256']
    identifier = hashlib.sha256((identity + pins).encode('ascii')).hexdigest()
    receipt = dict(schema=schema, identity=identity, release=identifier, pins=pins,
                   images=chosen, selection=selection, model_prefixes=prefixes,
                   files=list(models.values()), kit=records)
    directory = release / 'installed-runtime'
    _metadata(directory, partial=True)
    if (directory / 'receipt.json').exists():
        if verify(release, identity, identifier) != receipt:
            raise ValueError('Resume with the same verified runtime selection.')
        return receipt
    plan = directory / 'plan.json'
    if plan.exists() and _json(plan) != receipt:
        raise ValueError('Resume with the same verified media and runtime selection.')
    _tree(release / 'kit', kit_records, partial=True)
    retained_payload = release / 'payload'
    _tree(retained_payload, models, partial=True, empty_directory=empty_directory)
    for name, row in models.items():
        _safe(payload / name, regular=True)
        _checked(payload / name, row)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    _atomic_json(plan, receipt)
    metadata = {Path(name): payload.parent / name for name in ('PAYLOAD.json', 'PAYLOAD.SHA256')}
    metadata[Path('index.json')] = payload / 'images/index.json'
    # Keep the transaction files separate from the generic copy allowlist.
    for name, source in metadata.items():
        destination = directory / name
        if destination.exists():
            if _digest(destination) != _digest(source):
                raise ValueError('Resume from the same verified media inventory.')
            continue
        temporary = destination.with_name(destination.name + '.deep32-new')
        _safe(temporary)
        with source.open('rb') as incoming, temporary.open('wb') as outgoing:
            for block in iter(lambda: incoming.read(1024 * 1024), b''):
                outgoing.write(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if _digest(temporary) != _digest(source):
            raise ValueError('Restore the same verified media inventory.')
        temporary.replace(destination)
    provision.copy_kit(kit, release / 'kit')
    mountpoint = retained_payload / empty_directory if empty_directory is not None else None
    # The directory is our last payload write. Its presence on a retry tells
    # us to use exact verification below instead of the file-only copier.
    if mountpoint is None or not mountpoint.exists():
        provision.copy_verified(payload, retained_payload, {Path(name): payload / name for name in models})
        if mountpoint is not None:
            _safe(mountpoint)
            mountpoint.mkdir(mode=0o700)
    if mountpoint is not None:
        _sync_directory(mountpoint.parent)
    _tree(release / 'kit', kit_records)
    _tree(retained_payload, models, empty_directory=empty_directory)
    retained, retained_identity = _inventory(directory)
    if retained != inventory or retained_identity != identity:
        raise ValueError('Verified media inventory changed during copying.')
    _atomic_json(directory / 'receipt.json', receipt)
    return verify(release, identity, identifier)
