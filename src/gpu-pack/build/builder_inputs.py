"""Offline builder input, lock, image and payload verification. No downloads."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from provenance import digest, canonical, unresolved
from distributions import lock_requirements, validate_wheelhouse, canonical_name
from image_binding import archive_binding, check_archive, resolve_loaded


def lock_matches(lock, pins):
    rows = lock_requirements(lock.read_text(encoding='ascii'))
    expected = set()
    for line in pins.read_text(encoding='ascii').splitlines():
        if line.strip() and not line.startswith('#'):
            name, version = line.split('==')
            expected.add((canonical_name(name), version))
    if set(rows) != expected:
        raise ValueError('builder lock differs from S2 engine pins')
    return len(rows)


def checked_inputs(stage, kit):
    config = json.loads((stage / 'builder/pins.json').read_text(encoding='ascii'))
    if unresolved(config):
        raise ValueError('TO-PIN: builder inputs unresolved')
    if config['format'] != 'deep32-builder-pins/1' or config['qdrant']['version'] != '1.19.1':
        raise ValueError('builder format or Qdrant version differs')
    for name, prefix in (('base', 'python:3.12-slim-trixie'), ('qdrant', 'qdrant/qdrant:v1.19.1')):
        entry = config[name]
        if not re.fullmatch(re.escape(prefix) + r'@sha256:[0-9a-f]{64}', entry['image']):
            raise ValueError('builder repository digest differs: ' + name)
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', entry['image_id']):
            raise ValueError('builder image id invalid: ' + name)
        if digest(stage / 'builder' / (name + '.tar')) != entry['archive_sha256']:
            raise ValueError('builder base/archive hash differs: ' + name)
        check_archive(stage / 'builder' / (name + '.tar'), entry, entry['image'].split('@')[1])
        if entry.get('inspected_id') not in (entry['index_digest'], entry['config_digest']):
            raise ValueError('builder measured image id differs: ' + name)
        if entry['archive_format'] == 'classic' and entry['inspected_id'] != entry['config_digest']:
            raise ValueError('classic measured image id differs: ' + name)
    # The resolver's complete inventory seals local archives, debs, lock and wheels.
    expected_paths = {'builder/pins.json', 'builder/base.tar', 'builder/qdrant.tar',
                      'builder/debs.json', 'payload/locks/builder.lock'}
    for folder in ('builder/debs', 'payload/wheelhouse/builder'):
        for path in sorted((stage / folder).iterdir()):
            if path.is_symlink() or not path.is_file():
                raise ValueError('unsupported builder input member')
            expected_paths.add(path.relative_to(stage).as_posix())
    rows = json.loads((stage / 'builder/INPUTS.json').read_text(encoding='ascii'))
    if not isinstance(rows, list) or [r['path'] for r in rows] != sorted(expected_paths):
        raise ValueError('builder input inventory differs')
    for row in rows:
        path = stage / row['path']
        if path.is_symlink() or not path.is_file() or path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            raise ValueError('builder input inventory hash differs')
    lock = stage / 'payload/locks/builder.lock'
    if digest(lock) != config['engine_lock_sha256']:
        raise ValueError('builder lock hash differs')
    lock_matches(lock, kit / 'containers/builder/requirements-engine.in')
    validate_wheelhouse(stage / 'payload/wheelhouse/builder', lock.read_text(encoding='ascii'))
    closure = stage / 'builder/debs.json'
    if digest(closure) != config['debian_closure_manifest_sha256']:
        raise ValueError('Debian closure manifest hash differs')
    debs = json.loads(closure.read_text(encoding='ascii'))
    if not debs or not {'git', 'age', 'gnupg', 'gpgv'} <= {row['package'] for row in debs}:
        raise ValueError('Debian closure must include git, age, gnupg and gpgv')
    names = set()
    for row in debs:
        name = row['path']
        if not re.fullmatch(r'[A-Za-z0-9.+_~%-]+\.deb', name) or name in names or not row.get('version'):
            raise ValueError('invalid Debian closure record')
        names.add(name)
        if digest(stage / 'builder/debs' / name) != row['sha256']:
            raise ValueError('Debian closure hash differs')
    if {p.name for p in (stage / 'builder/debs').iterdir()} != names:
        raise ValueError('Debian closure has unlisted files')
    return config


def context(stage, kit, output):
    config = checked_inputs(stage, kit)
    source = kit / 'containers/builder'
    # Fail before docker if approved families are missing, without reading queries.
    spec = importlib.util.spec_from_file_location('builder_eval_sets', source / 'eval_sets.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    evaluation = source / 'engine/rag/core/eval'
    if evaluation.is_symlink() or not evaluation.is_dir():
        raise ValueError('builder eval tree must be a regular directory')
    if any(p.name not in {'eval_v2.py', 'golden'} for p in evaluation.iterdir()):
        raise ValueError('builder eval tree contains an excluded entry')
    selected, excluded = mod.release_check(evaluation / 'golden')
    if excluded:
        raise ValueError('builder source tree contains excluded eval set names')
    evaluator = evaluation / 'eval_v2.py'
    if evaluator.is_symlink() or not evaluator.is_file():
        raise ValueError('builder evaluator must be a regular file')
    for directory in selected:
        members = list(directory.iterdir())
        if not set(mod.REQUIRED_MEMBERS) <= {p.name for p in members}:
            raise ValueError('builder neutral eval set is missing a required member')
        for member in members:
            if member.is_symlink() or not member.is_file() or member.name.endswith('.env'):
                raise ValueError('builder neutral eval set has a non-regular member')
    shutil.copytree(source, output)
    shutil.copytree(stage / 'payload/wheelhouse/builder', output / 'wheelhouse')
    shutil.copytree(stage / 'builder/debs', output / 'debs')
    shutil.copyfile(stage / 'payload/locks/builder.lock', output / 'requirements.lock')
    return config


def record(stage, output, kit=HERE.parent):
    config = checked_inputs(stage, kit)
    records = []
    for role in ('builder', 'qdrant'):
        inspected = json.loads(subprocess.check_output(
            ['docker', 'image', 'inspect', 'deep32-local/' + role + ':locked'], text=True))
        if (not isinstance(inspected, list) or len(inspected) != 1
                or not isinstance(inspected[0], dict)):
            raise ValueError('single built image inspect record required')
        image = inspected[0]
        if image.get('Os') != 'linux' or image.get('Architecture') != 'amd64':
            raise ValueError('built image must be linux/amd64')
        if not isinstance(image.get('Id'), str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image['Id']):
            raise ValueError('invalid built image id')
        source_digest = config['qdrant']['index_digest'] if role == 'qdrant' else None
        binding = archive_binding(output / 'images' / (role + '.tar'), source_digest, image.get('Id'))
        if role == 'qdrant':
            check_archive(output / 'images/qdrant.tar', config['qdrant'], source_digest)
        row = dict(binding, role=role, path='images/' + role + '.tar',
                   sha256=digest(output / 'images' / (role + '.tar')))
        if role == 'qdrant':
            row.update(version='1.19.1', source_digest=source_digest)
        records.append(row)
    (output / 'builder-images.json').write_text(canonical(records), encoding='ascii', newline='\n')
    source = output / 'context'
    sources = [{'path': p.relative_to(source).as_posix(), 'sha256': digest(p)}
               for p in sorted(source.rglob('*'), key=lambda p: p.relative_to(source).as_posix()) if p.is_file()]
    (output / 'builder-build-inputs.json').write_text(canonical({'pins': config, 'source_inputs': sources}),
                                                   encoding='ascii', newline='\n')


def merge(stage, output, kit=HERE.parent):
    builder_pins = checked_inputs(stage, kit)
    records = json.loads((output / 'builder-images.json').read_text(encoding='ascii'))
    path = stage / 'payload/images/index.json'
    existing = json.loads(path.read_text(encoding='ascii'))
    if len(records) != 2 or {r['role'] for r in records} != {'builder', 'qdrant'} or any(r['role'] in {'builder', 'qdrant'} for r in existing):
        raise ValueError('builder image merge roles differ or already present')
    for row in records:
        if row['path'] != 'images/' + row['role'] + '.tar' or not re.fullmatch(r'sha256:[0-9a-f]{64}', row['image_id']):
            raise ValueError('builder image merge record invalid')
        if row['role'] == 'qdrant' and (row['image_id'] != builder_pins['qdrant']['image_id'] or
                row.get('version') != '1.19.1' or row.get('source_digest') != builder_pins['qdrant']['image'].split('@')[1]):
            raise ValueError('Qdrant merge pin differs')
        src = output / row['path']
        if digest(src) != row['sha256']:
            raise ValueError('builder output hash differs')
        check_archive(src, row, row.get('source_digest'))
        if row['role'] == 'qdrant':
            check_archive(src, builder_pins['qdrant'], builder_pins['qdrant']['index_digest'])
    for row in records:
        src = output / row['path']
        shutil.copyfile(src, stage / 'payload' / row['path'])
    path.write_text(canonical(sorted(existing + records, key=lambda r: r['role'])), encoding='ascii', newline='\n')
    pinpath = stage / 'pins.json'
    config = json.loads(pinpath.read_text(encoding='ascii'))
    config['builder'] = json.loads((stage / 'builder/pins.json').read_text(encoding='ascii'))
    for row in records:
        config['runtime'][row['role'] + '_image_id'] = row['image_id']
    pinpath.write_text(canonical(config), encoding='ascii', newline='\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument('action', choices=('check', 'context', 'record', 'merge', 'base-id', 'qdrant-id', 'base-ref', 'base-loaded-id', 'qdrant-loaded-id'))
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--kit', type=Path, default=HERE.parent)
    ap.add_argument('--out', type=Path)
    a = ap.parse_args()
    if a.action in ('check', 'context', 'base-id', 'qdrant-id', 'base-ref', 'base-loaded-id', 'qdrant-loaded-id'):
        c = context(a.stage, a.kit, a.out) if a.action == 'context' else checked_inputs(a.stage, a.kit)
        if a.action in ('base-id', 'qdrant-id'):
            print(c['base' if a.action == 'base-id' else 'qdrant']['image_id'])
        elif a.action in ('base-loaded-id', 'qdrant-loaded-id'):
            print(resolve_loaded(c['base' if a.action == 'base-loaded-id' else 'qdrant']))
        elif a.action == 'base-ref':
            print(c['base']['image'])
    elif a.action == 'record':
        record(a.stage, a.out, a.kit)
    else:
        merge(a.stage, a.out, a.kit)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
