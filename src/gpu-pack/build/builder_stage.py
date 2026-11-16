#!/usr/bin/env python3
"""Stage measured S2 builder inputs without resolving tags or downloading files.

Run on the existing S2 host with --ctx /srv/d32/s2/ctx --out NEW_STAGE.
The rendered Dockerfile and compose JSON supply digests; no .env file is read.
By default only local docker image inspect/save are used. --archives DIR instead
requires base.tar, qdrant.tar, base.inspect.json and qdrant.inspect.json in DIR.
Supply the complete offline Debian closure with --debs DIR --debs-manifest FILE
if it is not already in CTX/builder. No apt or pip command is ever run.
The output is a builder overlay: merge it into the main fetch-inputs stage.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from builder_inputs import checked_inputs, lock_matches
from distributions import validate_wheelhouse
from provenance import canonical, digest
from image_binding import archive_binding

SHA = r'[0-9a-f]{64}'
ID = r'sha256:' + SHA
PREFIX = {'base': 'python:3.12-slim-trixie', 'qdrant': 'qdrant/qdrant:v1.19.1'}
REPOSITORY = {'base': 'library/python', 'qdrant': 'qdrant/qdrant'}


def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('regular input file required: ' + str(path))
    if path.suffix == '.env' or path.name == 'permissions-evidence':
        raise ValueError('excluded input name')
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError('input traverses a symlink: ' + str(path))
    return path


def directory(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir() or any(p.is_symlink() for p in path.parents):
        raise ValueError('regular input directory required: ' + str(path))
    return path


def members(folder):
    return [regular(p) for p in sorted(directory(folder).iterdir())]


def read_json(path):
    return json.loads(regular(path).read_text(encoding='ascii'))


def s2_references(ctx):
    """Read only rendered, non-secret S2 files, never the source TO-PIN files."""
    dockerfile = regular(ctx / 'compose/engine.Dockerfile').read_text(encoding='ascii')
    bases = re.findall(r'^FROM\s+(\S+)\s*$', dockerfile, flags=re.MULTILINE)
    if len(bases) != 1 or not re.fullmatch(re.escape(PREFIX['base']) + '@' + ID, bases[0]):
        raise ValueError('S2 rendered engine.Dockerfile has no single pinned trixie base')
    services = read_json(ctx / 'compose/compose.json')['services']
    qdrant = services['qdrant']['image']
    if not isinstance(qdrant, str) or not re.fullmatch(r'qdrant/qdrant(?::v1\.19\.1)?@' + ID, qdrant):
        raise ValueError('S2 rendered compose.json has no pinned Qdrant image')
    if 'qdrant-c3' in services and services['qdrant-c3']['image'] != qdrant:
        raise ValueError('S2 Qdrant service digests differ')
    return {'base': bases[0], 'qdrant': PREFIX['qdrant'] + '@' + qdrant.split('@')[1]}


def inspected_id(value, role, reference):
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError('single image inspect record required: ' + role)
    image = value[0]
    image_id = image.get('Id', '')
    if not isinstance(image_id, str) or not re.fullmatch(ID, image_id):
        raise ValueError('invalid measured image id: ' + role)
    if image.get('Os') != 'linux' or image.get('Architecture') != 'amd64':
        raise ValueError('builder image must be linux/amd64: ' + role)
    expected = reference.split('@')[1]
    found = False
    for item in image.get('RepoDigests', []):
        if not isinstance(item, str) or '@' not in item:
            continue
        repo, actual = item.rsplit('@', 1)
        repo = repo.removeprefix('docker.io/').removeprefix('index.docker.io/')
        if repo == 'python':
            repo = 'library/python'
        if repo == REPOSITORY[role] and actual == expected:
            found = True
    if not found:
        raise ValueError('local image inspect lacks the pinned repository digest: ' + role)
    if role == 'qdrant':
        labels = image.get('Config', {}).get('Labels') or {}
        tags = image.get('RepoTags') or []
        if (not any(isinstance(tag, str) and tag.removeprefix('docker.io/') == PREFIX['qdrant'] for tag in tags)
                and labels.get('org.opencontainers.image.version') not in ('1.19.1', 'v1.19.1')):
            raise ValueError('local image evidence does not establish Qdrant 1.19.1')
    return image_id


def archive_id(path):
    """Return the canonical config identity, checking both archive forms."""
    return archive_binding(regular(path))['config_digest']


def debian_closure(folder, manifest):
    rows = read_json(manifest)
    if not isinstance(rows, list) or not rows:
        raise ValueError('complete Debian closure manifest required')
    by_name = {}
    packages = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'package', 'version', 'path', 'sha256'}
                or not isinstance(row['path'], str)
                or not re.fullmatch(r'[A-Za-z0-9.+_~%-]+\.deb', row['path'])
                or row['path'] in by_name or not isinstance(row['package'], str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', row['package'])
                or not isinstance(row['version'], str) or not row['version']
                or not re.fullmatch(SHA, row['sha256'])):
            raise ValueError('invalid Debian closure manifest record')
        by_name[row['path']] = row
        packages.add(row['package'])
    if not {'git', 'age', 'gnupg', 'gpgv'} <= packages:
        raise ValueError('Debian closure must include git, age, gnupg and gpgv')
    files = members(folder)
    if {p.name for p in files} != set(by_name):
        raise ValueError('Debian closure contains missing or unlisted files')
    for path in files:
        if digest(path) != by_name[path.name]['sha256']:
            raise ValueError('Debian closure hash differs: ' + path.name)
    return files


def local_inspect(docker, reference):
    # image inspect never pulls. Do not add docker pull/build/run here.
    result = subprocess.run([docker, 'image', 'inspect', reference], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return json.loads(result.stdout)


def stage(ctx, out, kit, archives=None, debs=None, debs_manifest=None, docker='docker'):
    ctx, out, kit = Path(ctx), Path(out), Path(kit)
    directory(ctx)
    directory(kit)
    if out.exists() or out.is_symlink():
        raise ValueError('output already exists; choose a new builder overlay')
    if any(parent.is_symlink() for parent in out.parents):
        raise ValueError('output parent traverses a symlink')
    # Refuse before writing any output unless all sources and digests are measurable.
    refs = s2_references(ctx)
    lock = regular(ctx / 'locks/engine.lock')
    wheels = members(ctx / 'wheelhouse/engine')
    lock_matches(lock, regular(kit / 'containers/builder/requirements-engine.in'))
    wheel_count = validate_wheelhouse(ctx / 'wheelhouse/engine', lock.read_text(encoding='ascii'))
    closure = regular(debs_manifest or ctx / 'builder/debs.json')
    deb_files = debian_closure(debs or ctx / 'builder/debs', closure)
    evidence = {}
    for role in ('base', 'qdrant'):
        if archives is None:
            value = local_inspect(docker, refs[role])
        else:
            directory(archives)
            value = read_json(Path(archives) / (role + '.inspect.json'))
            source = regular(Path(archives) / (role + '.tar'))
        image_id = inspected_id(value, role, refs[role])
        if archives is not None:
            archive_binding(source, refs[role].split('@')[1], image_id)
        evidence[role] = image_id
    # No image inspection, package query or source traversal is performed beyond
    # the exact inputs above. In particular the engine and eval trees stay unread.
    created = False
    try:
        out.mkdir(parents=True)
        created = True
        created_root = out.resolve()
        for relative in ('builder/debs', 'payload/wheelhouse/builder', 'payload/locks'):
            (out / relative).mkdir(parents=True, exist_ok=True)
        config = {'format': 'deep32-builder-pins/1'}
        for role in ('base', 'qdrant'):
            target = out / 'builder' / (role + '.tar')
            if archives is None:
                # Save the exact inspected immutable id, never resolve a tag.
                subprocess.run([docker, 'image', 'save', '--output', str(target), evidence[role]],
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            else:
                shutil.copyfile(Path(archives) / (role + '.tar'), target)
            binding = archive_binding(target, refs[role].split('@')[1], evidence[role])
            config[role] = dict(binding, image=refs[role], archive_sha256=digest(target))
            config[role]['inspected_id'] = evidence[role]
        config['qdrant']['version'] = '1.19.1'
        for path in wheels:
            shutil.copyfile(path, out / 'payload/wheelhouse/builder' / path.name)
        for path in deb_files:
            shutil.copyfile(path, out / 'builder/debs' / path.name)
        shutil.copyfile(lock, out / 'payload/locks/builder.lock')
        shutil.copyfile(closure, out / 'builder/debs.json')
        config['engine_lock_sha256'] = digest(out / 'payload/locks/builder.lock')
        config['debian_closure_manifest_sha256'] = digest(out / 'builder/debs.json')
        (out / 'builder/pins.json').write_text(canonical(config), encoding='ascii', newline='\n')
        # A top-level filled copy is convenient for the lead. The sealed builder
        # input inventory intentionally retains the builder_inputs.py contract.
        (out / 'builder-pins.json').write_text(canonical(config), encoding='ascii', newline='\n')
        files = [out / relative for relative in ('builder/pins.json', 'builder/base.tar',
                 'builder/qdrant.tar', 'builder/debs.json', 'payload/locks/builder.lock')]
        files += members(out / 'builder/debs') + members(out / 'payload/wheelhouse/builder')
        inventory = [{'path': p.relative_to(out).as_posix(), 'size': p.stat().st_size, 'sha256': digest(p)}
                     for p in sorted(files, key=lambda p: p.relative_to(out).as_posix())]
        (out / 'builder/INPUTS.json').write_text(canonical(inventory), encoding='ascii', newline='\n')
        checked_inputs(out, kit)
        return {'format': 'deep32-builder-stage-result/1', 'wheel_count': wheel_count,
                'deb_count': len(deb_files), 'inventory_count': len(inventory),
                'pins_sha256': digest(out / 'builder/pins.json'),
                'images': {role: {key: config[role][key] for key in
                           ('index_digest', 'config_digest', 'manifest_digest', 'inspected_id')}
                           for role in ('base', 'qdrant')}}
    except BaseException:
        # Remove only the new output created by this invocation. Existing output
        # is refused before created is set; the caller's sources are untouched.
        if created and not out.is_symlink() and out.resolve() == created_root:
            shutil.rmtree(created_root)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument('--ctx', type=Path, default=Path('/srv/d32/s2/ctx'))
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--kit', type=Path, default=HERE.parent)
    ap.add_argument('--archives', type=Path, help='pre-exported archives and inspect JSON; no Docker calls')
    ap.add_argument('--debs', type=Path, help='complete pre-staged Debian dependency closure directory')
    ap.add_argument('--debs-manifest', type=Path, help='sealed Debian closure manifest')
    ap.add_argument('--docker', default='docker', help='local Docker executable; inspect/save only')
    a = ap.parse_args(argv)
    if (a.debs is None) != (a.debs_manifest is None):
        ap.error('--debs and --debs-manifest must be supplied together')
    try:
        print(canonical(stage(a.ctx, a.out, a.kit, a.archives, a.debs, a.debs_manifest, a.docker)), end='')
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        # Subprocess output is deliberately not printed: inspected image metadata
        # can contain private local values. Report only the command failure.
        detail = 'local Docker inspect/save failed' if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print('builder_stage: REFUSE: ' + detail, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
