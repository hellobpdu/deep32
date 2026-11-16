#!/usr/bin/env python3
"""Offline image-build metadata. Called only by build-images.sh on the lab host."""
import json
from pathlib import Path
import re
import subprocess
import sys

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))  # siblings (image_binding, provenance) under any loader
from provenance import canonical, digest, members
from distributions import canonical_name, lock_requirements
from image_binding import archive_binding, check_archive, resolve_loaded


def checked_base(stage, pins):
    runtime = pins['runtime']
    entry = runtime['base_image_binding']
    if (not isinstance(runtime['base_image_id'], str)
            or not re.fullmatch('sha256:[0-9a-f]{64}', runtime['base_image_id'])
            or runtime['base_image_id'] not in {entry['config_digest'], entry['index_digest']}
            or digest(stage / 'base/runtime.tar') != runtime['base_archive_sha256']):
        raise ValueError('base image pin differs')
    return check_archive(stage / 'base/runtime.tar', entry, runtime['base_image'].rsplit('@', 1)[1])


def write_build_backend_lock(requirements, output):
    """Copy the two exact backend rows verbatim; fail before creating an output."""
    text = requirements.read_text(encoding='ascii')
    rows = lock_requirements(text)
    versions = {'setuptools': '80.10.2', 'wheel': '0.48.0'}
    selected = []
    for line in text.splitlines(keepends=True):
        if not line.strip() or line.startswith('#'):
            continue
        name, _, rest = line.partition('==')
        name = canonical_name(name)
        if name in versions:
            if (name, versions[name]) not in rows:
                raise ValueError('build backend version differs: ' + name)
            selected.append(line)
    for name in versions:
        if sum(canonical_name(line.partition('==')[0]) == name for line in selected) != 1:
            raise ValueError('build backend must appear exactly once: ' + name)
    if any(not line.endswith('\n') for line in selected):
        raise ValueError('build backend lock rows need a final newline')
    output.write_text(''.join(selected), encoding='ascii', newline='\n')


def main():
    action, stage_name, *rest = sys.argv[1:]
    stage = Path(stage_name)
    if action == 'backend-lock':
        write_build_backend_lock(stage / 'requirements.lock', stage / 'build-backend.lock')
        return
    pins = json.loads((stage / 'pins.json').read_text(encoding='ascii'))
    if action == 'verify-base':
        checked_base(stage, pins)
        for name in ('reranker', 'converter'):
            lock = stage / 'payload/locks' / (name + '.lock')
            if '--hash=sha256:' not in lock.read_text(encoding='ascii'):
                raise ValueError('missing complete hashed lock')
    elif action == 'base-id':
        print(resolve_loaded(checked_base(stage, pins)))
    elif action == 'index':
        base_binding = checked_base(stage, pins)
        output = Path(rest[0])
        rows = []
        for role in ('converter', 'door', 'reranker'):
            inspected = json.loads(subprocess.check_output(['docker', 'image', 'inspect', 'deep32-local/' + role + ':locked'], text=True))
            if (not isinstance(inspected, list) or len(inspected) != 1
                    or not isinstance(inspected[0], dict)
                    or not isinstance(inspected[0].get('Id'), str)
                    or not re.fullmatch('sha256:[0-9a-f]{64}', inspected[0]['Id'])
                    or inspected[0].get('Os') != 'linux' or inspected[0].get('Architecture') != 'amd64'):
                raise ValueError('built image is not a single linux/amd64 image')
            binding = archive_binding(output / 'images' / (role + '.tar'), inspected_id=inspected[0]['Id'])
            rows.append({'role': role, 'path': 'images/' + role + '.tar', **binding,
                         'sha256': digest(output / 'images' / (role + '.tar'))})
        (output / 'application-images.json').write_text(canonical(rows), encoding='ascii')
        (output / 'image-build-inputs.json').write_text(canonical({'source_date_epoch': __import__('os').environ['SOURCE_DATE_EPOCH'],
                                                                 'base': pins['runtime']['base_image_id'],
                                                                 'base_binding': base_binding,
                                                                 'inputs': members(output / 'contexts')}), encoding='ascii')
    else:
        raise ValueError('unknown image operation')


if __name__ == '__main__':
    main()
