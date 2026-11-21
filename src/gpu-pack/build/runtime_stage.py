#!/usr/bin/env python3
"""Stage an explicitly selected runtime component from a sealed acquisition tree.

The allowlist uses the existing PAYLOAD.json list of path/size/sha256 records.
It must come from the owning capability producer; this tool never guesses it.
No package installation, image loading, signing or network access occurs here.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from image_binding import check_archive
from model_cache import write_references
from provenance import canonical, digest, members
from validate import validate as validate_source

CLASSES = frozenset(('turing', 'ampere80', 'ampere86', 'ada', 'hopper',
                     'blackwell100', 'blackwell120'))
ROLES = {'core': frozenset(('door', 'reranker')),
         'ingestion': frozenset(('converter', 'builder', 'qdrant'))}
BUILD_ONLY = ('wheelhouse/', 'repos/', 'base/', 'contexts/', 'iso/')
FORMAT = 'deep32-runtime-stage/1'


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding='ascii'), object_pairs_hook=_object)


def relative(value):
    if (not isinstance(value, str) or not value.isascii() or '\\' in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or ':' in value or not value or value.startswith('/')
            or any(part in ('', '.', '..') for part in value.split('/'))
            or PurePosixPath(value).as_posix() != value):
        raise ValueError('unsafe relative member')
    for part in value.split('/'):
        stem = part.split('.', 1)[0].upper()
        if (part.endswith((' ', '.')) or any(c in part for c in '<>"|?*')
                or stem in {'CON', 'PRN', 'AUX', 'NUL'}
                or re.fullmatch(r'(?:COM|LPT)[1-9]', stem)):
            raise ValueError('non-portable relative member')
    return value


def safe_tree(root):
    root = Path(root)
    for parent in (root, *root.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('linked input or output path')
    if not root.is_dir():
        raise ValueError('missing input directory')
    for path in root.rglob('*'):
        if path.is_symlink() or path.is_junction() or not (path.is_dir() or path.is_file()):
            raise ValueError('unsafe tree member')


def inventory(path):
    rows = read_json(path)
    if not isinstance(rows, list) or not rows:
        raise ValueError('allowlist must be a non-empty inventory')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256'}:
            raise ValueError('invalid inventory record')
        name = relative(row['path'])
        if (name.casefold() in seen or type(row['size']) is not int or row['size'] < 0
                or not isinstance(row['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('duplicate or invalid inventory record')
        seen.add(name.casefold())
    if rows != sorted(rows, key=lambda row: row['path']):
        raise ValueError('inventory must be sorted')
    return rows


def expected_roles(component, gpu_class):
    if component == 'embedder':
        if gpu_class not in CLASSES:
            raise ValueError('unsupported embedder card class')
        return frozenset(('tei-' + gpu_class,))
    if component not in ROLES or gpu_class is not None:
        raise ValueError('card class applies only to an embedder component')
    return ROLES[component]


def complete_snapshot(payload, pins, role):
    model = pins['models'][role]
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', model['id'])
            or not re.fullmatch('[0-9a-f]{40}', model['revision'])):
        raise ValueError('invalid immutable model identity: ' + role)
    root = payload / 'models' / role / ('models--' + model['id'].replace('/', '--')) / 'snapshots' / model['revision']
    rows = model['files']
    if not isinstance(rows, list) or not rows:
        raise ValueError('complete pinned model file manifest required: ' + role)
    expected = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'sha256'}:
            raise ValueError('invalid model file manifest: ' + role)
        name = relative(row['path'])
        if name.casefold() in {key.casefold() for key in expected} or not re.fullmatch('[0-9a-f]{64}', row['sha256']):
            raise ValueError('duplicate or invalid model file: ' + role)
        expected[name] = row['sha256']
    actual = {row['path']: row['sha256'] for row in members(root)}
    if not actual or actual != expected:
        raise ValueError('model differs from complete pinned snapshot: ' + role)
    prefix = root.relative_to(payload).as_posix() + '/'
    return {prefix + name for name in expected}


def checked_archive(path, row, source, role):
    try:
        return check_archive(path, row, source)
    except tarfile.TarError as exc:
        raise ValueError('invalid image archive: ' + role) from exc


def select(stage, allowlist, component, gpu_class=None):
    """Validate everything before making an output; return exact selected records."""
    needed = expected_roles(component, gpu_class)
    safe_tree(stage)
    payload = stage / 'payload'
    source_rows = inventory(stage / 'PAYLOAD.json')
    if source_rows != members(payload):
        raise ValueError('source payload differs from its sealed inventory')
    selected = inventory(allowlist)
    source = {row['path']: row for row in source_rows}
    names = {row['path'] for row in selected}
    for row in selected:
        name = row['path']
        if row != source.get(name):
            raise ValueError('allowlisted member differs from sealed source: ' + name)
        if name.casefold().startswith(BUILD_ONLY):
            raise ValueError('build-only member in runtime allowlist: ' + name)
    # The image index is derived below; accepting an acquisition index would
    # accidentally reintroduce roles absent from this component.
    if 'images/index.json' in names:
        raise ValueError('omit images/index.json; the selected index is generated')
    pins = read_json(stage / 'pins.json')
    if pins.get('release_profile') != 'pack':
        raise ValueError('runtime staging requires release_profile pack')
    images = read_json(payload / 'images/index.json')
    if not isinstance(images, list) or any(not isinstance(row, dict) for row in images):
        raise ValueError('invalid source image index')
    roles = [row.get('role') for row in images]
    paths = [row.get('path') for row in images]
    if (any(not isinstance(role, str) for role in roles)
            or len(set(roles)) != len(roles) or len(set(paths)) != len(paths)):
        raise ValueError('duplicate or invalid image role/path')
    chosen = sorted((row for row in images if row['role'] in needed), key=lambda row: row['role'])
    if {row['role'] for row in chosen} != needed:
        raise ValueError('missing required component image role')
    archives = set()
    for row in chosen:
        role, name = row['role'], relative(row['path'])
        if not re.fullmatch(r'images/[A-Za-z0-9._-]+\.tar', name):
            raise ValueError('invalid image archive path')
        if name not in names or source[name]['sha256'] != row.get('sha256'):
            raise ValueError('image archive absent or hash differs: ' + role)
        checked_archive(payload / name, row, row.get('source_digest'), role)
        if role.startswith('tei-'):
            expected = pins['tei']['variants'][gpu_class]['digest']
            if not re.fullmatch('sha256:[0-9a-f]{64}', expected) or row.get('source_digest') != expected:
                raise ValueError('TEI source pin differs')
        else:
            expected = pins['runtime'][role + '_image_id']
            if (not re.fullmatch('sha256:[0-9a-f]{64}', expected)
                    or expected not in (row.get('config_digest'), row.get('index_digest'))):
                raise ValueError('application image pin differs: ' + role)
        if role == 'qdrant':
            qdrant = pins['builder']['qdrant']
            if (qdrant['version'] != '1.19.1' or row.get('version') != '1.19.1'
                    or row.get('source_digest') != qdrant['image'].split('@')[-1]):
                raise ValueError('Qdrant version/source pin differs')
            checked_archive(payload / name, qdrant, qdrant['image'].split('@')[-1], role)
        archives.add(name)
    if {name for name in names if name.startswith('images/')} != archives:
        raise ValueError('extra or unselected image in runtime allowlist')
    model_roots = ('models/tei/', 'models/reranker/') if component == 'core' else (
        ('models/mineru-home/',) if component == 'ingestion' else ())
    if component == 'core':
        for role in ('tei', 'reranker'):
            required = complete_snapshot(payload, pins, role)
            if not required <= names:
                raise ValueError('complete pinned model snapshot required: ' + role)
            # Cache metadata outside snapshots is selected by the producer.
            # A stale revision or another snapshot must never be brought along.
            prefix = 'models/' + role + '/'
            if any(name.startswith(prefix) and '/snapshots/' in name
                   and name not in required for name in names):
                raise ValueError('unselected model snapshot: ' + role)
    elif component == 'ingestion':
        for prefix in model_roots:
            complete = {name for name in source if name.startswith(prefix)}
            if not complete or not complete <= names:
                raise ValueError('complete source model tree required: ' + prefix)
    if any(name.startswith('models/') and not name.startswith(model_roots) for name in names):
        raise ValueError('unselected model in runtime allowlist')
    if component == 'embedder' and names != archives:
        raise ValueError('embedder add-on contains only its TEI archive')
    if component != 'embedder':
        licences = {name for name in source if name.startswith('licences/')}
        if (not licences or not licences <= names
                or any(source[name]['size'] == 0 for name in licences)):
            raise ValueError('complete non-empty source licence set required')
        required = {'evidence/input-attestation.json'}
        if component == 'ingestion':
            required.add('licences/AGPL-3.0.txt')
            converter = {name for name in source if name.startswith('source/production-converter/')}
            if not converter or not converter <= names:
                raise ValueError('complete converter corresponding source required')
        attestation = read_json(payload / 'evidence/input-attestation.json')
        required.update(relative(name) for name in attestation['records'])
        if not required <= names:
            raise ValueError('missing selected attestation or evidence record')
        # A producer may allocate licence/source subtrees between components,
        # but may not silently truncate a selected subtree.
        for prefix in ('licences/', 'source/'):
            roots = {name.split('/')[1] for name in names if name.startswith(prefix)}
            for root in roots:
                base = prefix + root
                complete = {name for name in source if name == base or name.startswith(base + '/')}
                if not complete <= names:
                    raise ValueError('incomplete selected source/licence subtree')
    return selected, chosen


def stage_runtime(stage, kit, allowlist, component, output, epoch, gpu_class=None):
    if type(epoch) is not int or epoch < 0:
        raise ValueError('SOURCE_DATE_EPOCH must be a non-negative integer')
    dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
    stage, allowlist, output = map(Path, (stage, allowlist, output))
    for path in (stage / 'pins.json', stage / 'PAYLOAD.json', allowlist):
        if any(parent.is_symlink() or parent.is_junction()
               for parent in (path, *path.parents)):
            raise ValueError('linked metadata path')
        if not path.is_file():
            raise ValueError('metadata must be a regular file')
    for parent in (output, *output.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('linked output path')
    if output.exists():
        raise ValueError('runtime stage output must be new')
    if output.resolve().is_relative_to(stage.resolve()) or stage.resolve().is_relative_to(output.resolve()):
        raise ValueError('runtime stage overlaps source')
    if output.resolve().is_relative_to(Path(kit).resolve()) or Path(kit).resolve().is_relative_to(output.resolve()):
        raise ValueError('runtime stage overlaps kit')
    safe_tree(stage)
    validate_source(stage, Path(kit))
    selected, images = select(stage, allowlist, component, gpu_class)
    receipt = {'format': FORMAT, 'component': component, 'gpu_class': gpu_class,
               'source_date_epoch': epoch, 'pins_sha256': digest(stage / 'pins.json'),
               'source_payload_sha256': digest(stage / 'PAYLOAD.json'),
               'allowlist_sha256': digest(allowlist), 'roles': sorted(row['role'] for row in images)}
    output.mkdir(parents=True)
    payload = output / 'payload'
    for row in selected:
        destination = payload / row['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stage / 'payload' / row['path'], destination)
        if destination.stat().st_size != row['size'] or digest(destination) != row['sha256']:
            raise ValueError('runtime copy differs: ' + row['path'])
    if component == 'core':
        # Resolver pointers are derived output, outside the sealed snapshots.
        write_references(payload, read_json(stage / 'pins.json'))
    (payload / 'images/index.json').write_text(canonical(images), encoding='ascii', newline='\n')
    shutil.copyfile(stage / 'pins.json', output / 'pins.json')
    (output / 'PAYLOAD.json').write_text(canonical(members(payload)), encoding='ascii', newline='\n')
    receipt['payload_sha256'] = digest(output / 'PAYLOAD.json')
    # Prepare the receipt without publishing a completion record. Every file,
    # including this pending receipt, must have its final mode and timestamp first.
    pending = output / 'RUNTIME.pending.json'
    complete = output / 'RUNTIME.json'
    pending.write_text(canonical(receipt), encoding='ascii', newline='\n')
    for path in sorted(output.rglob('*'), reverse=True) + [output]:
        path.chmod(0o755 if path.is_dir() else 0o644)
        os.utime(path, (epoch, epoch))
    pending.replace(complete)
    try:
        # Publishing the receipt changes the directory timestamp. A failure here
        # must not leave a completion record on an incompletely normalised stage.
        os.utime(output, (epoch, epoch))
    except BaseException:
        complete.unlink(missing_ok=True)
        raise
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'kit', 'allowlist', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--component', choices=('core', 'embedder', 'ingestion'), required=True)
    parser.add_argument('--gpu-class', choices=sorted(CLASSES))
    parser.add_argument('--epoch', type=int, required=True)
    args = parser.parse_args()
    try:
        print(canonical(stage_runtime(args.stage, args.kit, args.allowlist, args.component,
                                      args.out, args.epoch, args.gpu_class)), end='')
    except (OSError, ValueError, KeyError, TypeError, OverflowError, tarfile.TarError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
