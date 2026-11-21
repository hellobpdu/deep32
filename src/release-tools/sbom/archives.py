"""Read sealed image inventories and exact runtime tar member metadata.

Image files are passed only to the existing image-binding checker.  This
adapter never inventories a layer filesystem: its caller must first verify
the sealed inventory file's hash and pass its complete config-bound contents.
The inventory therefore also covers layers whose compression is unsupported
by the binder's optional diff-ID check.
"""

import copy
import hashlib
from pathlib import Path
import re
import tarfile
import zlib

from .common import build_image_binding, digest, public_path, regular


INVENTORY_FORMAT = 'deep32-sbom-image-inventory/1'
MISSING_INVENTORY = 'complete sealed inventory bound to config digest required'
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_BINDING_FIELDS = {'image_id', 'config_digest', 'index_digest',
                   'manifest_digest', 'archive_format'}


def _text(value, label):
    if (not isinstance(value, str) or not value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError('invalid ' + label)
    return value


def _path(value):
    try:
        return public_path(value)
    except ValueError as exc:
        raise ValueError('unsafe runtime member path') from exc


def _archive_file(path):
    path = Path(path)
    return regular(path.parent, path.name)


def _inventory_components(inventory, config_digest):
    if inventory is None:
        raise ValueError(MISSING_INVENTORY)
    if (not isinstance(inventory, dict)
            or set(inventory) != {'format', 'config_digest', 'coverage', 'components'}
            or inventory.get('format') != INVENTORY_FORMAT):
        raise ValueError('invalid sealed image inventory format; ' + MISSING_INVENTORY)
    if inventory.get('coverage') != 'complete':
        raise ValueError('incomplete sealed image inventory; ' + MISSING_INVENTORY)
    if inventory.get('config_digest') != config_digest:
        raise ValueError('sealed image inventory config digest differs')
    rows = inventory.get('components')
    if not isinstance(rows, list) or not rows:
        raise ValueError('complete sealed image inventory components required')
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid sealed image inventory component')
        name = _text(row.get('name'), 'image component name')
        version = _text(row.get('version'), 'image component version')
        architecture = _text(row.get('architecture'), 'image component architecture')
        kind = _text(row.get('kind'), 'image component kind')
        identity = (kind, name, version, architecture)
        if identity in seen:
            raise ValueError('duplicate sealed image inventory component identity')
        seen.add(identity)
    return sorted(copy.deepcopy(rows), key=lambda row: (
        row['kind'], row['name'], row['version'], row['architecture'], row.get('id', '')))


def normalise_image(path, binding, inventory=None):
    """Measure an image with a complete sealed inventory of its final contents.

    The caller verifies the inventory file against its explicit input manifest.
    Complete inventories retain every inherited base component and the final
    installed state; a direct application lock cannot replace this evidence.
    The shipped-image profile requires a nonempty component inventory.
    No Docker command, archive extraction or layer metadata parsing occurs here.
    """
    path = _archive_file(path)
    if not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS:
        raise ValueError('complete exact image binding record required')
    try:
        measured = build_image_binding.check_archive(path, binding)
    except (tarfile.TarError, EOFError, OSError, zlib.error) as exc:
        raise ValueError('invalid saved image archive') from exc
    components = _inventory_components(inventory, measured['config_digest'])
    return {'size': path.stat().st_size, 'sha256': digest(path),
            'binding': measured, 'components': components, 'coverage': 'complete'}


def _expected_members(members):
    if not isinstance(members, list):
        raise ValueError('runtime member manifest must be a list')
    result = {}
    for row in members:
        if (not isinstance(row, dict) or not {'path', 'size', 'sha256'} <= set(row)
                or set(row) - {'path', 'size', 'sha256', 'mode'}):
            raise ValueError('invalid runtime member manifest row')
        name = _path(row['path'])
        if name in result:
            raise ValueError('duplicate runtime member manifest path')
        size = row['size']
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError('invalid runtime member size')
        if not isinstance(row['sha256'], str) or not _SHA256.fullmatch(row['sha256']):
            raise ValueError('invalid runtime member SHA-256')
        if 'mode' in row and (isinstance(row['mode'], bool)
                              or not isinstance(row['mode'], int)
                              or not 0 <= row['mode'] <= 0o7777):
            raise ValueError('invalid runtime member mode')
        result[name] = row
    return result


def normalise_runtime_archive(path, members):
    """Verify exactly the declared ordinary file members without extracting.

    Directories, links, devices and undeclared metadata are refused.  Tar names
    and the declared map must match literally, so normalisation cannot conceal
    a traversal or a duplicate archive location.
    """
    path = _archive_file(path)
    expected = _expected_members(members)
    found = {}
    try:
        with tarfile.open(path, 'r:*') as archive:
            for member in archive:
                name = _path(member.name)
                if name in found:
                    raise ValueError('duplicate runtime archive member')
                if not member.isfile() or member.linkname:
                    raise ValueError('runtime archive member must be an ordinary regular file')
                if member.sparse is not None:
                    raise ValueError('sparse runtime archive member is unsupported')
                row = expected.get(name)
                if row is None:
                    raise ValueError('unexpected runtime archive member: ' + name)
                if member.size != row['size']:
                    raise ValueError('runtime member size differs: ' + name)
                if 'mode' in row and member.mode != row['mode']:
                    raise ValueError('runtime member mode differs: ' + name)
                hashed = hashlib.sha256()
                with archive.extractfile(member) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        hashed.update(block)
                actual_hash = hashed.hexdigest()
                if actual_hash != row['sha256']:
                    raise ValueError('runtime member SHA-256 differs: ' + name)
                found[name] = {'path': name, 'size': member.size,
                               'sha256': actual_hash, 'mode': member.mode}
    except (tarfile.TarError, EOFError, OSError, zlib.error) as exc:
        raise ValueError('invalid runtime tar archive') from exc
    if set(found) != set(expected):
        missing = ', '.join(sorted(set(expected) - set(found)))
        raise ValueError('runtime archive member missing: ' + missing)
    return {'size': path.stat().st_size, 'sha256': digest(path),
            'members': [found[name] for name in sorted(found)]}
