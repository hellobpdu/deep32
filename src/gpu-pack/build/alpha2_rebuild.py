#!/usr/bin/env python3
"""Offline alpha.2 saved-image rebuild and qualification boundary.

The caller supplies a private, validated acquisition-stage copy. Docker commands
are for the isolated Linux producer; this module never fetches an input. Native
ABI/JIT/search and retained-layer qualification remain measured owner inputs.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import builder_inputs
import full_images
import images
from image_binding import archive_binding, check_archive
from provenance import canonical, digest, members
from runtime_stage import safe_tree, relative
from validate import validate as validate_source

FORMAT = 'deep32-alpha2-image-rebuild/1'
GATE_FORMAT = 'deep32-alpha2-image-gates/1'
BASE_ROLES = ('door', 'reranker', 'converter', 'builder')
FULL_ROLES = ('full', 'model_bridge')

# Run under the image's own Python, without importing the application or model.
# Hash all installed distribution files, including bytecode. A changed file
# refuses instead of silently excluding an inconvenient difference. Native
# package/file and ABI/JIT gates are separate owner receipts below.
DEPENDENCY_PROBE = r'''
import hashlib, importlib.metadata as m, json, os, pathlib, re, subprocess
rows=[]
seen=set()
for d in m.distributions():
    name=re.sub(r'[-_.]+','-',d.metadata['Name']).lower()
    if name in seen: raise ValueError('duplicate installed distribution')
    seen.add(name)
    files=[]
    if d.files is None: raise ValueError('installed distribution lacks file manifest')
    for item in sorted(d.files,key=str):
        p=pathlib.Path(d.locate_file(item))
        if p.is_symlink():
            files.append({'path':str(item),'link':os.readlink(p)})
        elif p.is_file():
            h=hashlib.sha256()
            with p.open('rb') as s:
                for c in iter(lambda:s.read(1048576),b''):h.update(c)
            files.append({'path':str(item),'size':p.stat().st_size,'sha256':h.hexdigest()})
        else: raise ValueError('installed distribution file missing')
    rows.append({'name':name,'version':d.version,'files':files})
debian=subprocess.check_output(['dpkg-query','-W','-f=${Package}\t${Version}\t${Architecture}\n'],text=True)
application=[]
for root in ('/app','/opt/deep32/engine','/opt/deep32/builder','/opt/deep32/gateway','/opt/deep32/import','/opt/deep32/pairing','/opt/deep32/full','/opt/deep32/model-bridge'):
    folder=pathlib.Path(root)
    if not folder.is_dir():continue
    for p in sorted(folder.rglob('*')):
        if '.venv' in p.parts:continue
        if p.is_symlink():application.append({'path':str(p),'link':os.readlink(p)})
        elif p.is_file():
            h=hashlib.sha256()
            with p.open('rb') as s:
                for c in iter(lambda:s.read(1048576),b''):h.update(c)
            application.append({'path':str(p),'size':p.stat().st_size,'sha256':h.hexdigest()})
print(json.dumps({'format':'deep32-installed-dependencies/1','python':sorted(rows,key=lambda r:r['name']),'debian':sorted(debian.splitlines()),'application':sorted(application,key=lambda r:r['path'])},sort_keys=True,separators=(',',':')))
'''.strip()


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate rebuild metadata key')
            value[key] = item
        return value
    return json.loads(Path(path).read_text(encoding='ascii'), object_pairs_hook=unique)


def write_json(path, value):
    Path(path).write_text(canonical(value), encoding='ascii', newline='\n')


def selected_roles(index):
    names = [row['role'] for row in index]
    if len(names) != len(set(names)) or not set(BASE_ROLES) <= set(names):
        raise ValueError('unique existing application and builder roles required')
    full = set(names) & set(FULL_ROLES)
    if full and full != set(FULL_ROLES):
        raise ValueError('full and model_bridge must be present together')
    return BASE_ROLES + (FULL_ROLES if full else ())


def probe_command(image):
    return ['docker', 'run', '--rm', '--network', 'none', '--pull=never',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--pids-limit=128', '--entrypoint', 'python', image, '-I', '-B',
            '-c', DEPENDENCY_PROBE]


def docker_build(context, role, base, epoch):
    return ['docker', 'build', '--network', 'none', '--pull=false',
            '--build-arg', 'BASE_IMAGE=' + base,
            '--build-arg', 'SOURCE_DATE_EPOCH=' + str(epoch),
            '--tag', 'deep32-local/' + role + ':locked', str(context)]


def plan(stage, kit, out, epoch, roles=BASE_ROLES):
    """Return the actual external Docker operations, including probes and saves.

    Immutable IDs are shown as explicit measured placeholders until the local
    archive has been loaded and inspected. Context copies and receipt checks
    happen in this module; no shell interpolation executes these commands.
    """
    stage, kit, out = map(Path, (stage, kit, out))
    commands = []
    for role in roles:
        commands += [['docker', 'image', 'load', '--input', str(stage / 'payload/images' / (role + '.tar'))],
                     ['docker', 'image', 'inspect', '<measured-before-' + role + '-archive-id>'],
                     probe_command('<measured-before-' + role + '-config-id>')]
    commands += [['docker', 'image', 'load', '--input', str(stage / 'base/runtime.tar')],
                 ['docker', 'image', 'inspect', '<measured-runtime-base-archive-id>'],
                 ['docker', 'tag', '<measured-runtime-base-config-id>', 'deep32-runtime-base:locked'],
                 ['docker', 'image', 'load', '--input', str(stage / 'builder/base.tar')],
                 ['docker', 'image', 'inspect', '<measured-builder-base-archive-id>'],
                 ['docker', 'tag', '<measured-builder-base-config-id>', 'deep32-builder-base:locked']]
    for role in roles:
        if role == 'model_bridge':
            commands.append(['docker', 'tag', 'deep32-local/full:locked', 'deep32-local/model_bridge:locked'])
        else:
            if role == 'full':
                commands.append(['docker', 'tag', '<measured-after-builder-config-id>', 'deep32-full-base:locked'])
            base = ('deep32-builder-base:locked' if role == 'builder' else
                    'deep32-full-base:locked' if role == 'full' else 'deep32-runtime-base:locked')
            commands.append(docker_build(out / 'contexts' / role, role, base, epoch))
        commands += [['docker', 'image', 'inspect', 'deep32-local/' + role + ':locked'],
                     ['docker', 'image', 'save', '--output', str(out / 'images' / (role + '.tar')),
                      'deep32-local/' + role + ':locked'],
                     probe_command('<measured-after-' + role + '-config-id>')]
    return commands


def _run(runner, command, epoch):
    environment = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), TZ='UTC', LC_ALL='C',
                       PYTHONDONTWRITEBYTECODE='1', DOCKER_BUILDKIT='1')
    result = runner(command, check=True, capture_output=True, text=True, env=environment)
    return result.stdout or ''


def _inspect(runner, reference, epoch):
    rows = json.loads(_run(runner, ['docker', 'image', 'inspect', reference], epoch))
    if (not isinstance(rows, list) or len(rows) != 1
            or rows[0].get('Os') != 'linux' or rows[0].get('Architecture') != 'amd64'
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', rows[0].get('Id', ''))):
        raise ValueError('one measured linux/amd64 image required')
    return rows[0]['Id']


def _loaded(runner, archive, row, epoch):
    binding = check_archive(archive, row, row.get('source_digest'))
    _run(runner, ['docker', 'image', 'load', '--input', str(archive)], epoch)
    candidates = list(dict.fromkeys(item for item in (binding['index_digest'], binding['config_digest']) if item))
    for candidate in candidates:
        try:
            identity = _inspect(runner, candidate, epoch)
        except subprocess.CalledProcessError:
            continue
        if identity not in candidates:
            raise ValueError('loaded immutable identity differs from saved archive')
        return identity
    raise ValueError('loaded image not found by measured immutable identities')


def _probe(runner, image, destination, epoch):
    value = json.loads(_run(runner, probe_command(image), epoch))
    validate_dependencies(value)
    write_json(destination, value)
    return value


def validate_dependencies(value):
    if (not isinstance(value, dict) or set(value) != {'format', 'python', 'debian', 'application'}
            or value['format'] != 'deep32-installed-dependencies/1'
            or not isinstance(value['python'], list) or not isinstance(value['debian'], list)
            or not isinstance(value['application'], list)
            or value['debian'] != sorted(set(value['debian'])) or not value['debian']):
        raise ValueError('complete installed-dependency manifest required')
    names = []
    for row in value['python']:
        if (not isinstance(row, dict) or set(row) != {'name', 'version', 'files'}
                or not isinstance(row['name'], str) or not row['name']
                or not isinstance(row['version'], str) or not row['version']
                or not isinstance(row['files'], list) or not row['files']):
            raise ValueError('invalid installed Python distribution')
        names.append(row['name'])
        seen = set()
        for entry in row['files']:
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not entry['path']:
                raise ValueError('invalid installed file record')
            if entry['path'] in seen:
                raise ValueError('duplicate installed file record')
            seen.add(entry['path'])
            if set(entry) == {'path', 'link'}:
                if not isinstance(entry['link'], str):
                    raise ValueError('invalid installed symbolic link record')
            elif (set(entry) != {'path', 'size', 'sha256'} or type(entry['size']) is not int
                    or entry['size'] < 0 or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
                raise ValueError('invalid installed file hash record')
        if row['files'] != sorted(row['files'], key=lambda item: item['path']):
            raise ValueError('installed files must be sorted')
    if names != sorted(set(names)):
        raise ValueError('installed distributions must be unique and sorted')
    previous = ''
    for entry in value['application']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('path'), str)
                or entry['path'] <= previous):
            raise ValueError('application file inventory must be unique and sorted')
        previous = entry['path']
        if set(entry) == {'path', 'link'}:
            if not isinstance(entry['link'], str):
                raise ValueError('invalid application link record')
        elif (set(entry) != {'path', 'size', 'sha256'} or type(entry['size']) is not int
                or entry['size'] < 0 or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
            raise ValueError('invalid application file record')
    return value


def dependency_equality(before, after):
    validate_dependencies(before)
    validate_dependencies(after)
    if before['python'] != after['python'] or before['debian'] != after['debian']:
        raise ValueError('installed dependency versions or files differ; retain manifests for owner review')
    previous = {row['path']: row for row in before['application']}
    current = {row['path']: row for row in after['application']}
    changes = [{'path': name, 'before': previous.get(name), 'after': current.get(name)}
               for name in sorted(set(previous) | set(current)) if previous.get(name) != current.get(name)]
    return {'python_distributions': len(before['python']), 'debian_packages': len(before['debian']),
            'python_distribution_files': sum(len(row['files']) for row in before['python']),
            'application_file_differences': changes}


def _normalise(root, epoch):
    for path in sorted(root.rglob('*'), reverse=True) + [root]:
        path.chmod(0o755 if path.is_dir() else 0o644)
        os.utime(path, (epoch, epoch))


def _application_context(stage, kit, output, role):
    source = kit / 'containers' / ('converter-agpl' if role == 'converter' else role)
    safe_tree(source)
    shutil.copytree(source, output, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if role != 'door':
        shutil.copytree(stage / 'payload/wheelhouse' / role, output / 'wheelhouse')
        shutil.copyfile(stage / 'payload/locks' / (role + '.lock'), output / 'requirements.lock')
    if role == 'converter':
        images.stage_converter_debs(stage, output)
        images.write_build_backend_lock(output / 'requirements.lock', output / 'build-backend.lock')
        shutil.copyfile(stage / 'payload/licences/AGPL-3.0.txt', output / 'AGPL-3.0.txt')
        # The sealed corresponding source is authoritative; stale files from
        # an older source context must not survive an overlay copy.
        copied_source = output / 'production-converter'
        if copied_source.exists():
            if not copied_source.resolve().is_relative_to(output.resolve()):
                raise ValueError('converter context source escapes its new output')
            shutil.rmtree(copied_source)
        shutil.copytree(stage / 'payload/source/production-converter', copied_source)


def _replace(stage, replacements):
    index_path = stage / 'payload/images/index.json'
    old = read_json(index_path)
    by_role = {row['role']: row for row in replacements}
    if len(by_role) != len(replacements) or not set(by_role) <= {row['role'] for row in old}:
        raise ValueError('replacement roles must match existing image roles')
    pins = read_json(stage / 'pins.json')
    for row in replacements:
        pins['runtime'][row['role'] + '_image_id'] = row['config_digest']
    write_json(index_path, sorted([by_role.get(row['role'], row) for row in old], key=lambda row: row['role']))
    write_json(stage / 'pins.json', pins)
    write_json(stage / 'PAYLOAD.json', members(stage / 'payload'))


def _full_input_stage(stage, output, builder_archive, builder):
    """Copy the small full-input closure instead of duplicating every model."""
    output.mkdir()
    for name in ('pins.json', 'payload/images/index.json', 'payload/locks/builder.lock',
                 'payload/locks/gateway.lock'):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stage / name, target)
    target = output / 'payload/images/builder.tar'
    try:
        os.link(builder_archive, target)
    except OSError:
        shutil.copyfile(builder_archive, target)
    shutil.copytree(stage / 'payload/wheelhouse/gateway', output / 'payload/wheelhouse/gateway')
    _replace(output, [builder])


def rebuild(stage, kit, out, epoch, command_runner=subprocess.run, gates=None):
    """Build and measure archives; merge only after all owner gates are present.

    Missing qualification leaves BUILD.json and the new archives for a later
    qualify_and_merge call. The acquisition stage is not changed before gates.
    """
    stage, kit, out = map(Path, (stage, kit, out))
    if type(epoch) is not int or epoch < 0:
        raise ValueError('nonnegative source epoch required')
    for root in (stage, kit):
        safe_tree(root)
    for ancestor in (out, *out.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError('linked rebuild output path')
    if out.exists() or any(out.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(out.resolve())
                           for root in (stage, kit)):
        raise ValueError('new rebuild output must be separate from stage and kit')
    validate_source(stage, kit)
    index = read_json(stage / 'payload/images/index.json')
    roles = selected_roles(index)
    pins = read_json(stage / 'pins.json')
    runtime_base = images.checked_base(stage, pins)
    builder_config = builder_inputs.checked_inputs(stage, kit)
    if 'full' in roles:
        full_images.checked_inputs(stage, kit)
    originals = {row['role']: row for row in index if row['role'] in roles}
    for row in originals.values():
        if row['path'] != 'images/' + row['role'] + '.tar':
            raise ValueError('unexpected saved-image path')
        archive = stage / 'payload' / row['path']
        if digest(archive) != row['sha256']:
            raise ValueError('baseline archive differs from sealed image index')
        check_archive(archive, row)
    out.mkdir(parents=True)
    for name in ('images', 'contexts', 'qualification'):
        (out / name).mkdir()
    before = {'pins_sha256': digest(stage / 'pins.json'), 'payload_sha256': digest(stage / 'PAYLOAD.json'),
              'index_sha256': digest(stage / 'payload/images/index.json')}
    for role in roles:
        identity = _loaded(command_runner, stage / 'payload' / originals[role]['path'], originals[role], epoch)
        _probe(command_runner, identity, out / 'qualification' / (role + '-before.json'), epoch)
    identity = _loaded(command_runner, stage / 'base/runtime.tar', runtime_base, epoch)
    _run(command_runner, ['docker', 'tag', identity, 'deep32-runtime-base:locked'], epoch)
    identity = _loaded(command_runner, stage / 'builder/base.tar', builder_config['base'], epoch)
    _run(command_runner, ['docker', 'tag', identity, 'deep32-builder-base:locked'], epoch)
    new = []
    evidence = []
    for role in roles:
        context = out / 'contexts' / role
        if role == 'model_bridge':
            _run(command_runner, ['docker', 'tag', 'deep32-local/full:locked', 'deep32-local/model_bridge:locked'], epoch)
        else:
            if role == 'builder':
                builder_inputs.context(stage, kit, context)
                base = 'deep32-builder-base:locked'
            elif role == 'full':
                # The full recipe derives from the rebuilt builder, without
                # altering the caller's acquisition-stage image index yet.
                provisional = out / 'full-input-stage'
                builder = next(row for row in new if row['role'] == 'builder')
                _full_input_stage(stage, provisional, out / builder['path'], builder)
                full_images.context(provisional, kit, context)
                _run(command_runner, ['docker', 'tag', builder['config_digest'], 'deep32-full-base:locked'], epoch)
                base = 'deep32-full-base:locked'
            else:
                _application_context(stage, kit, context, role)
                base = 'deep32-runtime-base:locked'
            _normalise(context, epoch)
            _run(command_runner, docker_build(context, role, base, epoch), epoch)
        tag = 'deep32-local/' + role + ':locked'
        identity = _inspect(command_runner, tag, epoch)
        archive = out / 'images' / (role + '.tar')
        _run(command_runner, ['docker', 'image', 'save', '--output', str(archive), tag], epoch)
        binding = archive_binding(archive, inspected_id=identity)
        new.append(dict(binding, role=role, path='images/' + role + '.tar', sha256=digest(archive)))
        measured = _probe(command_runner, identity, out / 'qualification' / (role + '-after.json'), epoch)
        equality = dependency_equality(read_json(out / 'qualification' / (role + '-before.json')), measured)
        evidence.append(dict(role=role, before_archive_sha256=originals[role]['sha256'],
                             after_archive_sha256=digest(archive), equality=equality,
                             before_manifest_sha256=digest(out / 'qualification' / (role + '-before.json')),
                             after_manifest_sha256=digest(out / 'qualification' / (role + '-after.json'))))
    if 'full' in roles and new[-1]['config_digest'] != new[-2]['config_digest']:
        raise ValueError('full and model_bridge identities differ')
    receipt = dict(format=FORMAT, source_date_epoch=epoch, source=before, roles=list(roles),
                   before_images=[originals[role] for role in roles], images=new, dependency_equality=evidence,
                   context_files=members(out / 'contexts'), status='qualification-required')
    write_json(out / 'BUILD.json', receipt)
    return qualify_and_merge(stage, kit, out, gates=gates)


def check_owner_gates(out, receipt, gates=None):
    """Check owner-supplied, content-addressed qualification evidence references.

    This validates local receipt integrity; authentication and correctness of
    the native probes remain release-owner responsibilities.
    """
    path = out / 'qualification/gates.json'
    if gates is not None:
        supplied = Path(gates)
        if any(parent.is_symlink() or parent.is_junction() for parent in (supplied, *supplied.parents)):
            raise ValueError('linked owner qualification input')
        if not supplied.is_file():
            raise ValueError('owner qualification input must be a regular file')
        # External evidence references are relative to the input receipt. Copy
        # and hash each referenced regular file into the retained build record.
        document = read_json(supplied)
        if (not isinstance(document, dict) or set(document) != {'format', 'roles'}
                or document['format'] != GATE_FORMAT or not isinstance(document['roles'], dict)):
            raise ValueError('external owner qualification format differs')
        for role in document.get('roles', {}).values():
            if not isinstance(role, dict) or not isinstance(role.get('checks'), dict):
                raise ValueError('external owner qualification check set differs')
            for check in role.get('checks', {}).values():
                if not isinstance(check, dict) or set(check) != {'pass', 'path', 'sha256'} or check['pass'] is not True:
                    raise ValueError('external owner qualification must pass with measured evidence')
                name = relative(check['path'])
                source = supplied.parent / name
                destination = out / 'qualification' / name
                if (not name.startswith('evidence/') or not source.is_file()
                        or any(p.is_symlink() or p.is_junction() for p in (source, *source.parents))
                        or digest(source) != check['sha256']):
                    raise ValueError('external qualification evidence differs')
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.resolve() != destination.resolve():
                    shutil.copyfile(source, destination)
        if supplied.resolve() != path.resolve():
            shutil.copyfile(supplied, path)
    if not path.is_file():
        raise ValueError('Supply qualification/gates.json with archive-bound ABI, import, runtime-compilation, retained-layer and exact-search receipts; then resume qualification.')
    gates = read_json(path)
    if (not isinstance(gates, dict) or set(gates) != {'format', 'roles'}
            or gates['format'] != GATE_FORMAT or not isinstance(gates['roles'], dict)
            or set(gates['roles']) != set(receipt['roles'])):
        raise ValueError('owner qualification roles or format differ')
    comparisons = {row['role']: row for row in receipt['dependency_equality']}
    for role, gate in gates['roles'].items():
        comparison = comparisons[role]
        checks = {'abi', 'imports', 'runtime_compilation', 'retained_inputs_absent', 'native_dependency_files_equal'}
        if role == 'reranker':
            checks.add('exact_search_equal')
        if (not isinstance(gate, dict) or set(gate) != {'before_archive_sha256', 'after_archive_sha256', 'checks'}
                or gate['before_archive_sha256'] != comparison['before_archive_sha256']
                or gate['after_archive_sha256'] != comparison['after_archive_sha256']
                or not isinstance(gate['checks'], dict) or set(gate['checks']) != checks):
            raise ValueError('owner qualification archive binding or check set differs: ' + role)
        for check in gate['checks'].values():
            if not isinstance(check, dict) or set(check) != {'pass', 'path', 'sha256'} or check['pass'] is not True:
                raise ValueError('owner qualification must pass with measured evidence')
            name = relative(check['path'])
            evidence = out / 'qualification' / name
            if (not name.startswith('evidence/') or evidence.is_symlink() or not evidence.is_file()
                    or any(p.is_symlink() or p.is_junction() for p in evidence.parents)
                    or digest(evidence) != check['sha256']):
                raise ValueError('owner qualification evidence differs')
    return digest(path)


def qualify_and_merge(stage, kit, out, gates=None):
    stage, kit, out = map(Path, (stage, kit, out))
    safe_tree(stage)
    safe_tree(out)
    receipt = read_json(out / 'BUILD.json')
    if receipt.get('format') != FORMAT or receipt.get('status') != 'qualification-required':
        raise ValueError('unmerged rebuild receipt required')
    if (receipt['source'] != {'pins_sha256': digest(stage / 'pins.json'),
                             'payload_sha256': digest(stage / 'PAYLOAD.json'),
                             'index_sha256': digest(stage / 'payload/images/index.json')}
            or receipt['context_files'] != members(out / 'contexts')):
        raise ValueError('source stage or measured build contexts changed')
    roles = selected_roles(read_json(stage / 'payload/images/index.json'))
    if list(roles) != receipt['roles'] or [row['role'] for row in receipt['images']] != list(roles):
        raise ValueError('rebuild role set differs')
    equalities = {row['role']: row for row in receipt['dependency_equality']}
    if len(equalities) != len(roles) or set(equalities) != set(roles):
        raise ValueError('dependency equality role set differs')
    old_index = {row['role']: row for row in read_json(stage / 'payload/images/index.json')}
    if receipt['before_images'] != [old_index[role] for role in roles]:
        raise ValueError('baseline image receipt differs from current sealed stage')
    for row in receipt['images']:
        role = row['role']
        if row['path'] != 'images/' + role + '.tar' or digest(out / row['path']) != row['sha256']:
            raise ValueError('rebuilt archive differs')
        check_archive(out / row['path'], row)
        comparison = equalities[role]
        for suffix in ('before', 'after'):
            if digest(out / 'qualification' / (role + '-' + suffix + '.json')) != comparison[suffix + '_manifest_sha256']:
                raise ValueError('installed dependency evidence changed')
        counts = dependency_equality(read_json(out / 'qualification' / (role + '-before.json')),
                                     read_json(out / 'qualification' / (role + '-after.json')))
        if (counts != comparison['equality'] or row['sha256'] != comparison['after_archive_sha256']
                or comparison['before_archive_sha256'] != old_index[role]['sha256']):
            raise ValueError('dependency equality receipt differs')
    gate_hash = check_owner_gates(out, receipt, gates=gates)
    validate_source(stage, kit)
    for row in receipt['images']:
        shutil.copyfile(out / row['path'], stage / 'payload' / row['path'])
    _replace(stage, receipt['images'])
    validate_source(stage, kit)
    receipt.update(status='qualified-and-merged', owner_gates_sha256=gate_hash,
                   merged_pins_sha256=digest(stage / 'pins.json'), merged_payload_sha256=digest(stage / 'PAYLOAD.json'))
    write_json(out / 'MERGE.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('action', choices=('plan', 'rebuild', 'qualify'))
    for name in ('stage', 'kit', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--epoch', type=int, default=0)
    parser.add_argument('--include-full', action='store_true')
    parser.add_argument('--gates', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'plan':
            roles = BASE_ROLES + (FULL_ROLES if args.include_full else ())
            for command in plan(args.stage, args.kit, args.out, args.epoch, roles):
                print(('DOCKER_BUILDKIT=1 ' if command[:2] == ['docker', 'build'] else '') + shlex.join(command))
        else:
            result = (rebuild(args.stage, args.kit, args.out, args.epoch, gates=args.gates) if args.action == 'rebuild'
                      else qualify_and_merge(args.stage, args.kit, args.out, gates=args.gates))
            print(canonical(result), end='')
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
