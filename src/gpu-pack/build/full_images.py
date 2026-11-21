"""Build and merge full-role archives using only sealed local inputs."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from provenance import canonical, digest
from distributions import lock_requirements, validate_wheelhouse, canonical_name
from image_binding import archive_binding, check_archive, resolve_loaded

ROLES = ('full', 'model_bridge')


def checked_inputs(stage, kit):
    pins = json.loads((stage / 'pins.json').read_text(encoding='ascii'))
    rows = json.loads((stage / 'payload/images/index.json').read_text(encoding='ascii'))
    builders = [r for r in rows if r['role'] == 'builder']
    if len(builders) != 1:
        raise ValueError('Build and merge the pinned S2 builder archive before the full role.')
    builder = builders[0]
    if (builder['path'] != 'images/builder.tar' or
            pins['runtime'].get('builder_image_id') not in {builder.get('config_digest'), builder.get('index_digest')} or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', pins['runtime'].get('builder_image_id', ''))):
        raise ValueError('The full-role builder pin differs; rebuild the builder first.')
    archive = stage / 'payload/images/builder.tar'
    if archive.is_symlink() or digest(archive) != builder['sha256']:
        raise ValueError('The builder archive hash differs; restore its sealed local archive.')
    binding = check_archive(archive, builder)
    lock = stage / 'payload/locks/gateway.lock'
    wheelhouse = stage / 'payload/wheelhouse/gateway'
    if not lock.is_file() or not wheelhouse.is_dir():
        raise ValueError('Stage the complete Linux cp312 gateway.lock and gateway wheelhouse from the existing pinned gateway requirements.')
    expected_hash = pins.get('full', {}).get('gateway_lock_sha256')
    if not re.fullmatch(r'[0-9a-f]{64}', expected_hash or '') or digest(lock) != expected_hash:
        raise ValueError('Seal the exact gateway lock hash in full.gateway_lock_sha256 before building.')
    requirements = lock.read_text(encoding='ascii')
    locked = lock_requirements(requirements)
    expected = set()
    for line in (kit / 'containers/full/requirements-gateway.in').read_text(encoding='ascii').splitlines():
        if line.strip() and not line.startswith('#'):
            name, version = line.split('==')
            expected.add((canonical_name(name), version))
    if set(locked) != expected:
        raise ValueError('The Linux gateway lock differs from the existing pinned gateway requirements.')
    validate_wheelhouse(wheelhouse, locked)
    for wheel in wheelhouse.iterdir():
        if wheel.suffix != '.whl':
            raise ValueError('Supply binary Linux gateway wheels; source distributions are not permitted.')
        py, abi, platform = wheel.stem.split('-')[-3:]
        python_ok = (py in ('py3', 'py2.py3', 'cp312') or
                     abi == 'abi3' and re.fullmatch(r'cp3[0-9]{1,2}', py) and int(py[2:]) <= 312)
        platform_ok = platform == 'any' or platform == 'linux_x86_64' or all(
            p.startswith('manylinux') and p.endswith('_x86_64') for p in platform.split('.'))
        if not python_ok or not platform_ok:
            raise ValueError('Stage the pinned Linux amd64 CPython 3.12 gateway wheels, not Workstation wheels.')

    # Gateway installation shares the engine venv: overlapping pins must agree.
    engine = lock_requirements((stage / 'payload/locks/builder.lock').read_text(encoding='ascii'))
    versions = {name: version for name, version in engine}
    for name, version in locked:
        previous = versions.get(name)
        if previous is not None and previous != version:
            raise ValueError('The gateway and engine dependency pins disagree; resolve their shared lock before building.')
    full = pins.get('full', {})
    bridge = full.get('model_bridge')
    if not isinstance(bridge, dict) or bridge.get('schema') != 'deep32.model-bridge.v1':
        raise ValueError('Seal the model bridge attestation configuration in full.model_bridge before building.')
    return {'builder': builder, 'binding': binding, 'gateway_lock_sha256': expected_hash}


def checked_source(source):
    if not source.is_dir() or source.is_symlink() or any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('Full-role image sources must be complete local directories without symbolic links.')
    if any(p.is_file() and (p.suffix == '.env' or p.suffix == '.pem' and 'private' in p.name) for p in source.rglob('*')):
        raise ValueError('Remove private credentials from the public full-role source context.')
    if any(p.name.lower().startswith(('golden', 'smoke')) or p.name.lower() in ('eval', 'staging')
           or re.fullmatch(r'q[0-9]{3,6}', p.name, re.IGNORECASE)
           or p.name.endswith(('.corrected.md', '.reviewed.md', '.questions.json'))
           for p in source.rglob('*')):
        raise ValueError('Remove protected evaluation or question material from the public full-role source tree.')


def _copy_tree(source, destination):
    checked_source(source)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def context(stage, kit, output, source_root=None):
    config = checked_inputs(stage, kit)
    source = Path(source_root) if source_root is not None else kit.parent
    if output.exists():
        raise ValueError('Choose a new full-role build output directory.')
    # Resolve and check every source before creating the output.
    inputs = [('engine', source / 'engine'), ('gateway', source / 'gateway'),
              ('import', kit / 'import'), ('pairing', kit / 'pairing'),
              ('full', kit / 'containers/full'), ('model-bridge', source / 'model-bridge')]
    if any(not path.is_dir() for _, path in inputs):
        raise ValueError('Build from the source repository with the same engine, gateway, importer and model bridge sources.')
    resolved = output.resolve()
    for _, path in inputs:
        source_path = path.resolve()
        if resolved == source_path or source_path in resolved.parents or resolved in source_path.parents:
            raise ValueError('Choose a full-role build output outside every source tree.')
    if resolved == stage.resolve() or stage.resolve() in resolved.parents:
        raise ValueError('Choose a full-role build output outside the staged inputs.')
    for _, path in inputs:
        checked_source(path)
    shim = source / 'workstation/node/shims/py/node_layers.py'
    if not shim.is_file() or shim.is_symlink() or any(p.is_symlink() for p in shim.parents):
        raise ValueError('Restore the reviewed immutable SQLite shim before building the full role.')
    output.mkdir(parents=True)
    for name, path in inputs:
        _copy_tree(path, output / name)
    shutil.copyfile(shim, output / 'model-bridge/node_layers.py')
    shutil.copyfile(kit / 'containers/full/Dockerfile', output / 'Dockerfile')
    shutil.copyfile(stage / 'payload/locks/gateway.lock', output / 'gateway.lock')
    shutil.copytree(stage / 'payload/wheelhouse/gateway', output / 'wheelhouse')
    return config


def record(stage, kit, output):
    config = checked_inputs(stage, kit)
    records = []
    for role in ROLES:
        inspected = json.loads(subprocess.check_output(['docker', 'image', 'inspect', 'deep32-local/' + role + ':locked'], text=True))
        if (not isinstance(inspected, list) or len(inspected) != 1 or
                inspected[0].get('Os') != 'linux' or inspected[0].get('Architecture') != 'amd64' or
                not re.fullmatch(r'sha256:[0-9a-f]{64}', inspected[0].get('Id', ''))):
            raise ValueError('The full-role build must produce one pinned linux/amd64 image per role.')
        archive = output / 'images' / (role + '.tar')
        binding = archive_binding(archive, inspected_id=inspected[0]['Id'])
        records.append(dict(binding, role=role, path='images/' + role + '.tar', sha256=digest(archive)))
    (output / 'full-images.json').write_text(canonical(records), encoding='ascii', newline='\n')
    sources = [{'path': p.relative_to(output / 'context').as_posix(), 'sha256': digest(p)}
               for p in sorted((output / 'context').rglob('*')) if p.is_file()]
    (output / 'full-build-inputs.json').write_text(canonical({'inputs': config, 'source_inputs': sources}), encoding='ascii', newline='\n')


def merge(stage, kit, output):
    checked_inputs(stage, kit)
    records = json.loads((output / 'full-images.json').read_text(encoding='ascii'))
    index = stage / 'payload/images/index.json'
    existing = json.loads(index.read_text(encoding='ascii'))
    if len(records) != 2 or {r['role'] for r in records} != set(ROLES) or any(r['role'] in ROLES for r in existing):
        raise ValueError('Merge exactly one new full-role and model bridge archive into a fresh stage.')
    for row in records:
        if row['path'] != 'images/' + row['role'] + '.tar' or not re.fullmatch(r'sha256:[0-9a-f]{64}', row['image_id']):
            raise ValueError('Restore the measured full-role image index before merging.')
        archive = output / row['path']
        if digest(archive) != row['sha256']:
            raise ValueError('Restore the unchanged full-role image archive before merging.')
        check_archive(archive, row)
    pins_path = stage / 'pins.json'
    pins = json.loads(pins_path.read_text(encoding='ascii'))
    for row in records:
        shutil.copyfile(output / row['path'], stage / 'payload' / row['path'])
        pins['runtime'][row['role'] + '_image_id'] = row['image_id']
    index.write_text(canonical(sorted(existing + records, key=lambda r: r['role'])), encoding='ascii', newline='\n')
    pins_path.write_text(canonical(pins), encoding='ascii', newline='\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument('action', choices=('check', 'context', 'base-loaded-id', 'record', 'merge'))
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--kit', type=Path, default=HERE.parent)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--source-root', type=Path)
    a = ap.parse_args()
    if a.action == 'context':
        context(a.stage, a.kit, a.out, a.source_root)
    elif a.action == 'record':
        record(a.stage, a.kit, a.out)
    elif a.action == 'merge':
        merge(a.stage, a.kit, a.out)
    else:
        config = checked_inputs(a.stage, a.kit)
        if a.action == 'base-loaded-id':
            print(resolve_loaded(config['binding']))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
