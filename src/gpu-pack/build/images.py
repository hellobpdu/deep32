#!/usr/bin/env python3
"""Offline image-build metadata. Called only by build-images.sh on the lab host."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))  # siblings (image_binding, provenance) under any loader
from provenance import canonical, digest, members
from distributions import canonical_name, lock_requirements
from image_binding import archive_binding, check_archive, resolve_loaded


CONVERTER_DEB_FORMAT = 'deep32-converter-debian-closure/1'
SHA256 = r'[0-9a-f]{64}'
PACKAGE = r'[a-z0-9][a-z0-9+.-]*'
DEB_VERSION = r'[A-Za-z0-9.:+~_-]+'
APT_INSTALL = re.compile(
    r'^Inst (' + PACKAGE + r')(?::amd64)? \((' + DEB_VERSION + r') .+ \[(amd64|all)\]\)$')
APT_SUMMARY = re.compile(r'^0 upgraded, ([0-9]+) newly installed, 0 to remove and [0-9]+ not upgraded\.$')


def _plain(path, directory=False):
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        raise ValueError('linked converter build input')
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError('missing converter build input: ' + path.name)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate converter input field')
        result[key] = value
    return result


def _apt_packages(plan):
    installed = set()
    summaries = []
    for line in plan.splitlines():
        summary = APT_SUMMARY.fullmatch(line)
        if summary:
            summaries.append(int(summary[1]))
        if line.startswith(('Remv ', 'Purg ')):
            raise ValueError('converter APT plan removes an existing package')
        if line.startswith('Inst '):
            match = APT_INSTALL.fullmatch(line)
            if not match or match.groups() in installed:
                raise ValueError('converter APT plan has an upgrade, duplicate or unexpected package line')
            installed.add(match.groups())
    if not installed or summaries != [len(installed)]:
        raise ValueError('converter APT plan must be a complete install-only simulation')
    return installed


def stage_converter_debs(stage, context):
    """Verify pinned Bookworm resolver and package bytes before staging bind mounts."""
    source = stage / 'converter'
    pins_path = stage / 'pins.json'
    manifest = source / 'debs.json'
    plan = source / 'apt-plan.txt'
    packages = source / 'debs'
    _plain(context, directory=True)
    _plain(packages, directory=True)
    for path in (pins_path, manifest, plan):
        _plain(path)
    runtime = json.loads(pins_path.read_text(encoding='ascii'), object_pairs_hook=_unique_keys)['runtime']
    if digest(manifest) != runtime.get('converter_debian_closure_sha256'):
        raise ValueError('converter Debian closure manifest hash differs from its pin')
    closure = json.loads(manifest.read_text(encoding='ascii'), object_pairs_hook=_unique_keys)
    fields = {'format', 'suite', 'architecture', 'base_image', 'base_dpkg_status_sha256',
              'apt_plan_sha256', 'packages'}
    if (not isinstance(closure, dict) or set(closure) != fields
            or closure['format'] != CONVERTER_DEB_FORMAT or closure['suite'] != 'bookworm'
            or closure['architecture'] != 'amd64' or closure['base_image'] != runtime['base_image']
            or any(not isinstance(closure[key], str) or not re.fullmatch(SHA256, closure[key])
                   for key in ('base_dpkg_status_sha256', 'apt_plan_sha256'))
            or not isinstance(closure['packages'], list)):
        raise ValueError('invalid pinned Bookworm converter Debian closure')
    if digest(plan) != closure['apt_plan_sha256']:
        raise ValueError('converter APT simulation hash differs from its pin')
    expected = _apt_packages(plan.read_text(encoding='ascii'))
    by_filename, names, actual = {}, set(), set()
    for row in closure['packages']:
        if (not isinstance(row, dict) or set(row) != {'name', 'version', 'architecture', 'file', 'size', 'sha256'}
                or any(not isinstance(row[key], str) for key in ('name', 'version', 'architecture', 'file', 'sha256'))
                or not re.fullmatch(PACKAGE, row['name']) or not re.fullmatch(DEB_VERSION, row['version'])
                or row['architecture'] not in ('amd64', 'all') or not re.fullmatch(r'[A-Za-z0-9.+_~%-]+\.deb', row['file'])
                or type(row['size']) is not int or row['size'] <= 0 or not re.fullmatch(SHA256, row['sha256'])):
            raise ValueError('invalid converter Debian package record')
        identity = (row['name'], row['version'], row['architecture'])
        if row['name'] in names or row['file'].casefold() in by_filename:
            raise ValueError('duplicate converter Debian package')
        names.add(row['name'])
        actual.add(identity)
        by_filename[row['file'].casefold()] = row
    if actual != expected or ('libgl1', '1.6.0-1', 'amd64') not in actual:
        raise ValueError('converter Debian packages differ from the full APT simulation')
    if {path.name for path in packages.iterdir()} != {row['file'] for row in by_filename.values()}:
        raise ValueError('missing or unlisted converter Debian archive')
    for row in by_filename.values():
        path = packages / row['file']
        _plain(path)
        if path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            raise ValueError('converter Debian archive differs from its pin: ' + path.name)
    destination = context / 'debs'
    destination.mkdir()
    for row in by_filename.values():
        output = destination / row['file']
        shutil.copyfile(packages / row['file'], output, follow_symlinks=False)
        _plain(output)
        if output.stat().st_size != row['size'] or digest(output) != row['sha256']:
            raise ValueError('converter Debian archive changed during context staging: ' + output.name)
    (destination / 'base-dpkg.sha256').write_text(
        closure['base_dpkg_status_sha256'] + '  /var/lib/dpkg/status\n', encoding='ascii', newline='\n')


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
    if action == 'converter-debs':
        stage_converter_debs(stage, Path(rest[0]))
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
