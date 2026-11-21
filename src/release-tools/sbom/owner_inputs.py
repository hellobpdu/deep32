#!/usr/bin/env python3
"""Generate measured, external alpha.2 owner SBOM inputs offline."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(REPO / 'src/gpu-pack/build'))
import alpha2_disc3 as producer
import alpha2_metadata as metadata
import alpha2_rebuild as rebuild
from sbom.common import (canonical, digest, exact_version, identifier, keys,
                         load_json, ordinary, public_path, regular, sha256)
from sbom.frozen import bind
from sbom.inputs import normalise_inputs
from sbom.workflow import generator_sha256

BINDING = ('image_id', 'config_digest', 'index_digest', 'manifest_digest', 'archive_format')
INVENTORY = 'deep32-owner-inventories/1'
# A native-only image must prove that Python distribution metadata is absent.
# An existing but broken Python probe is never downgraded to an empty inventory.
NATIVE_PROBE = r'''
set -eu
if command -v python >/dev/null 2>&1; then
    exit 21
elif command -v python3 >/dev/null 2>&1; then
    exec python3 -I -B -c "$1"
fi
metadata=$(find / \( -path /proc -o -path /sys -o -path /dev \) -prune -o \
    \( -name '*.dist-info' -o -name '*.egg-info' -o -name '*.egg-link' -o -name '*.egg' \) -print)
test -z "$metadata"
printf 'deep32-owner-native-dependencies/1\n'
dpkg-query -W '-f=${Package}\t${Version}\t${Architecture}\n'
'''.strip()


def fail(message):
    raise ValueError(message)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value), encoding='ascii', newline='\n')


def _id(prefix, value):
    text = re.sub('[^A-Za-z0-9_.+-]', '-', value).strip('-')[:48] or 'file'
    return identifier(prefix + '-' + text + '-' + hashlib.sha256(value.encode('ascii')).hexdigest()[:24])


def _public(value):
    value = public_path(value)
    if any(part.lower().startswith(('golden', 'priv-'))
           or part.lower().endswith(('.corrected.md', '.reviewed.md', '.questions.json'))
           or part.lower() in {'.git', '.aws', '.codex', '.agents'} for part in value.split('/')):
        fail('An excluded input path was supplied.')
    return value


def _files(root):
    root = ordinary(root, directory=True)
    rows = []
    def walk(folder):
        for path in sorted(folder.iterdir()):
            name = _public(path.relative_to(root).as_posix())
            ordinary(path, directory=path.is_dir())
            if path.is_dir():
                walk(path)
            else:
                rows.append(dict(path=name, size=path.stat().st_size, sha256=digest(path)))
    walk(root)
    return sorted(rows, key=lambda row: row['path'])


def _destination(out, inputs):
    out = Path(out).absolute()
    ordinary(out.parent, directory=True)
    if out.exists() or out.is_symlink():
        fail('The owner output directory must be new.')
    for source in inputs:
        source = Path(source).absolute()
        if out == source or out.is_relative_to(source) or source.is_relative_to(out):
            fail('The owner output overlaps a measured input.')
    return out


def _remove_temporary(temporary, output):
    if temporary.exists():
        if temporary.parent != output.parent or not temporary.name.startswith('.owner-inputs-'):
            fail('The temporary owner directory escaped its output parent.')
        shutil.rmtree(temporary)


def _component(work, name):
    stage = ordinary(work / 'stages' / name, directory=True)
    media = ordinary(work / 'components' / name / 'media', directory=True)
    image = regular(work / 'sbom-input-images' / name, producer.image_name(name))
    image_row = load_json(image.parent / 'IMAGE.json')
    if image_row != dict(path=image.name, size=image.stat().st_size, sha256=digest(image)):
        fail('The frozen image differs from IMAGE.json.')
    payload = _files(stage / 'payload')
    if load_json(stage / 'PAYLOAD.json') != payload:
        fail('The selected stage differs from its sealed payload.')
    for row in payload:
        path = regular(media / 'deep32/payload', row['path'])
        if path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            fail('The media differs from the selected stage payload.')
    selected = load_json(stage / 'payload/images/index.json')
    if not isinstance(selected, list) or not selected:
        fail('A nonempty selected runtime image index is required.')
    roles, paths = set(), set()
    result = []
    for row in selected:
        role, relative = identifier(row['role']), _public(row['path'])
        if role in roles or relative in paths:
            fail('Selected runtime archives and roles must be unique.')
        roles.add(role)
        paths.add(relative)
        archive = regular(media / 'deep32/payload', relative)
        if digest(archive) != sha256(row['sha256']):
            fail('The selected archive digest differs.')
        binding = rebuild.check_archive(archive, row)
        result.append((role, row, archive, binding))
    return stage, media, image, sorted(result, key=lambda item: item[0])


def _receipt_component(role, config, group, name, version, architecture, record, folder, receipts):
    exact_version(version)
    identifier(name)
    identifier(architecture)
    identity = _id(role + '-' + group, name + '-' + architecture)
    relative = 'records/' + identity + '.json'
    receipt = dict(format='deep32-installed-component/1',
        config_digest=config, role=role, ecosystem=group, measurement=record)
    if receipts is not None:
        receipts[relative] = receipt
    if folder is not None:
        _write(folder / relative, receipt)
    return dict(id=identity, kind='deb' if group == 'deb' else 'base-package',
                name=name, version=version, architecture=architecture, usage='runtime',
                files=[dict(root='installed', path=relative)], dependencies=[],
                licence_declared='NOASSERTION')


def _inventory_components(role, binding, probe, folder, receipts=None):
    rebuild.validate_dependencies(probe)
    rows, identities = [], set()
    for line in probe['debian']:
        parts = line.split('\t')
        if len(parts) != 3:
            fail('Every Debian row must contain name, exact version, and architecture.')
        name, version, architecture = parts
        key = (name, architecture)
        if key in identities:
            fail('Duplicate installed Debian package identity.')
        identities.add(key)
        rows.append(_receipt_component(role, binding['config_digest'], 'deb', name,
            version, architecture, dict(name=name, version=version, architecture=architecture), folder, receipts))
    for row in probe['python']:
        rows.append(_receipt_component(role, binding['config_digest'], 'python', row['name'],
            row['version'], 'all', row, folder, receipts))
    # The complete application-file measurement is retained in PROBE; it is not
    # silently represented as an invented installed distribution or version.
    if len({row['id'] for row in rows}) != len(rows):
        fail('Installed component identifiers collide.')
    return sorted(rows, key=lambda row: row['id'])


def _probe(runner, loaded, epoch):
    try:
        raw = rebuild._run(runner, rebuild.probe_command(loaded), epoch)
    except subprocess.CalledProcessError:
        command = rebuild.probe_command(loaded)
        # Root with read-bypass only, so the metadata search reads every folder; still no network, read-only.
        command = command[:command.index('--entrypoint')] + [
            '--user', '0:0', '--cap-add=DAC_READ_SEARCH', '--entrypoint', '/bin/sh', loaded, '-ec', NATIVE_PROBE,
            'deep32-owner-probe', rebuild.DEPENDENCY_PROBE]
        raw = rebuild._run(runner, command, epoch)
        prefix = 'deep32-owner-native-dependencies/1\n'
        if raw.startswith(prefix):
            value = dict(format='deep32-installed-dependencies/1', python=[],
                         debian=sorted(raw[len(prefix):].splitlines()), application=[])
            rebuild.validate_dependencies(value)
            return value
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    rebuild.validate_dependencies(value)
    return value


def disc3_inventories(work, out, *, epoch, runner=subprocess.run):
    """Measure every selected final image using the isolated probe."""
    metadata._epoch_revision(epoch, '0' * 40)
    work = ordinary(work, directory=True)
    measured = {name: _component(work, name) for name in producer.COMPONENTS}
    output = _destination(out, [p for value in measured.values() for p in value[:3]])
    temporary = Path(tempfile.mkdtemp(prefix='.owner-inputs-', dir=output.parent))
    summary = []
    try:
        for name, (_, _, _, selected) in measured.items():
            folder = temporary / name
            folder.mkdir()
            images = []
            for role, row, archive, binding in selected:
                loaded = rebuild._loaded(runner, archive, row, epoch)
                # Load and inspect bind the immutable probe target to this archive.
                probe = _probe(runner, loaded, epoch)
                components = _inventory_components(role, binding, probe, folder)
                _write(folder / (role + '.probe.json'), probe)
                inventory = dict(format='deep32-sbom-image-inventory/1',
                    config_digest=binding['config_digest'], coverage='complete', components=components)
                filename = role + '.inventory.json'
                _write(folder / filename, inventory)
                images.append(dict(role=role, archive=row['path'], archive_sha256=digest(archive),
                    binding=binding, probe_image_id=loaded, inventory=filename,
                    inventory_sha256=digest(folder / filename), components=len(components)))
            seal = dict(format=INVENTORY, component=name, source_date_epoch=epoch,
                        images=images, files=_files(folder))
            _write(folder / 'SEAL.json', seal)
            summary.append(dict(component=name, images=len(images),
                                components=sum(row['components'] for row in images)))
        _write(temporary / 'INVENTORIES.json', dict(format=INVENTORY, components=summary))
        temporary.rename(output)
        return dict(format=INVENTORY, components=summary)
    finally:
        _remove_temporary(temporary, output)


def _unique_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            fail('Duplicate measured JSON field.')
        result[name] = value
    return result


def _installed(folder, name, selected, epoch):
    folder = ordinary(folder, directory=True)
    seal = load_json(folder / 'SEAL.json')
    keys(seal, {'format', 'component', 'source_date_epoch', 'images', 'files'})
    actual = [row for row in _files(folder) if row['path'] != 'SEAL.json']
    if (seal['format'] != INVENTORY or seal['component'] != name
            or seal['source_date_epoch'] != epoch or seal['files'] != actual):
        fail('The installed inventory seal differs from its measured files.')
    by_role = {row['role']: row for row in seal['images']}
    if len(by_role) != len(seal['images']) or set(by_role) != {s[0] for s in selected}:
        fail('The installed inventory lacks exact selected image coverage.')
    inventories = {}
    for role, row, archive, binding in selected:
        saved = by_role[role]
        keys(saved, {'role', 'archive', 'archive_sha256', 'binding', 'probe_image_id',
                     'inventory', 'inventory_sha256', 'components'})
        if (saved['binding'] != binding or saved['archive'] != row['path']
                or saved['archive_sha256'] != digest(archive)
                or saved['inventory_sha256'] != digest(regular(folder, saved['inventory']))
                or saved['probe_image_id'] not in {binding['config_digest'], binding['index_digest']}):
            fail('The installed inventory differs from the final archive binding.')
        value = load_json(regular(folder, saved['inventory']))
        from sbom.archives import normalise_image
        normalise_image(archive, binding, value)
        probe = load_json(regular(folder, role + '.probe.json'))
        receipts = {}
        expected = _inventory_components(role, binding, probe, None, receipts)
        if value['components'] != expected or saved['components'] != len(expected):
            fail('The installed inventory omits or changes a measured distribution.')
        for relative, receipt in receipts.items():
            if load_json(regular(folder, relative)) != receipt:
                fail('An installed component receipt differs from the measured probe.')
        inventories[role] = (saved['inventory'], value)
    return inventories


def _contract(manifest, roots, manifests):
    """Map measured single-file components to the frozen adapter."""
    files = {(name, row['path']): row for name, root in roots.items() for row in _files(root)}
    refs = [dict(path=name + '/' + relative, sha256=files[name, relative]['sha256'])
            for name, relative in manifests.items()]
    components = [(item, None) for item in manifest['components']]
    for subject in manifest['subjects']:
        if subject['kind'] == 'container':
            ref = subject['image']['inventory']
            # The frozen field binds the complete image inventory. Individual
            # receipt filenames can contain native package syntax such as '+'.
            inventory = dict(path=ref['root'] + '/' + ref['path'],
                             sha256=files[ref['root'], ref['path']]['sha256'])
            rows = load_json(regular(roots[ref['root']], ref['path']))['components']
            components.extend((item, inventory) for item in rows)
    purposes = {'deb': 'OPERATING-SYSTEM', 'base-package': 'OPERATING-SYSTEM',
                'data': 'FILE', 'source': 'SOURCE'}
    frozen_components = []
    for item, inventory in components:
        ref = item['files'][0]
        frozen_components.append(dict(id=item['id'], name=item['name'], version=item['version'],
            purpose=purposes[item['kind']], scope=item['usage'],
            sha256=files[ref['root'], ref['path']]['sha256'], source_url='NOASSERTION',
            licence=item['licence_declared'], licence_evidence=[], dependencies=[],
            base_inventory=inventory))
    subjects = []
    for subject in manifest['subjects']:
        ref = subject['file']
        file = files[ref['root'], ref['path']]
        subjects.append(dict(kind=subject['kind'], path=ref['path'], size=file['size'], sha256=file['sha256'],
            aliases=[], input_manifests=refs, components=subject['components'], contents_sboms=[]))
    return dict(format='deep32-sbom-input', format_version=1, release=manifest['release'],
        source_commit=manifest['source_revision'], source_date_epoch=manifest['source_date_epoch'],
        generator_sha256=manifest['generator_sha256'],
        input_roots=[dict(id=name, path=name, manifest=ref) for name, ref in zip(manifests, refs)],
        components=sorted(frozen_components, key=lambda row: row['id']), subjects=subjects,
        exceptions=[], excluded_metadata=[])


def disc3_plans(work, out, *, epoch, inventories=None):
    """Seal nine plans after the producer freezes its final images."""
    work = ordinary(work, directory=True)
    revision = load_json(work / 'SOURCE.json')['revision']
    metadata._epoch_revision(epoch, revision)
    inventories = ordinary(inventories or work / 'owner-inventories', directory=True)
    measured = {name: _component(work, name) for name in producer.COMPONENTS}
    output = _destination(out, [inventories, *[p for value in measured.values() for p in value[:3]]])
    temporary = Path(tempfile.mkdtemp(prefix='.owner-inputs-', dir=output.parent))
    try:
        for name, (stage, media, image, selected) in measured.items():
            source = inventories / name
            installed = _installed(source, name, selected, epoch)
            folder = temporary / 'inventories' / name
            shutil.copytree(source, folder)
            roots = dict(media=media, images=image.parent, installed=folder)
            components, gaps, subjects = [], [], []
            for row in _files(media):
                identity = _id('media', row['path'])
                components.append(dict(id=identity, kind='data', name=identity,
                    version=producer.VERSION, architecture='all', usage='runtime',
                    files=[dict(root='media', path=row['path'])], dependencies=[],
                    licence_declared='NOASSERTION'))
            disc_ids = [row['id'] for row in components]
            subjects.append(dict(id=name, kind='disc', file=dict(root='images', path=image.name),
                role='disc3-' + name, aliases=[], components=disc_ids,
                component_coverage='complete', licence_coverage='partial'))
            all_components = list(components)
            for role, row, archive, binding in selected:
                filename, inventory = installed[role]
                all_components.extend(inventory['components'])
                subjects.append(dict(id='image-' + role, kind='container', role=role, aliases=[],
                    file=dict(root='media', path='deep32/payload/' + row['path']),
                    components=[item['id'] for item in inventory['components']],
                    component_coverage='complete', licence_coverage='partial',
                    image=dict(binding=binding, inventory=dict(root='installed', path=filename))))
            for item in all_components:
                gaps.append(dict(id=item['id'] + '-license', target=item['id'], field='licence',
                    reason='No explicit license declaration and measured license evidence were supplied.'))
            manifest = dict(format='deep32-sbom-input/1', release=metadata.RELEASE,
                source_date_epoch=epoch, source_revision=revision, generator_sha256=generator_sha256(),
                roots=[dict(id=root, files=_files(path)) for root, path in roots.items()],
                components=components, subjects=subjects, gaps=dict(format='deep32-sbom-gaps/1', gaps=gaps))
            contract = _contract(manifest, roots,
                dict(media='deep32/PAYLOAD.json', images='IMAGE.json', installed='SEAL.json'))
            plan = dict(format=metadata.PLAN, manifest=manifest, contract=contract,
                roots=[dict(id='media', source='media', path='.'),
                       dict(id='images', source='image-parent', path='.'),
                       dict(id='installed', source='inventory', path='inventories/' + name)])
            # Run the actual merged normalizer and frozen adapter before publishing.
            check = temporary / 'CHECK.json'
            frozen = temporary / 'FROZEN.json'
            _write(check, manifest)
            _write(frozen, contract)
            normal = normalise_inputs(check, roots)
            bind(frozen, normal)
            check.unlink()
            frozen.unlink()
            _write(temporary / (name + '.json'), plan)
        temporary.rename(output)
        return dict(format=metadata.PLAN, plans=[name + '.json' for name in producer.COMPONENTS])
    finally:
        _remove_temporary(temporary, output)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = ap.add_subparsers(dest='command', required=True)
    for name in ('disc3-inventories', 'disc3-plans'):
        command = commands.add_parser(name, description='Measure explicit producer inputs.', allow_abbrev=False)
        command.add_argument('--work', type=Path, required=True)
        command.add_argument('--out', type=Path, required=True)
        command.add_argument('--epoch', type=int, required=True)
        if name == 'disc3-plans':
            command.add_argument('--inventories', type=Path)
    command = commands.add_parser('disc-sbom', description='Inventory a frozen medium.', allow_abbrev=False)
    command.add_argument('--disc', choices=('disc1', 'disc2'), required=True)
    for name in ('image', 'out'):
        command.add_argument('--' + name, type=Path, required=True)
    command.add_argument('--epoch', type=int, required=True)
    command.add_argument('--source-revision', required=True)
    for name in ('medium-list', 'medium-root', 'manifest', 'credits'):
        command.add_argument('--' + name, type=Path)
    command.add_argument('--checksums', type=Path, action='append', default=[])
    command.add_argument('--notices', type=Path, action='append', default=[])
    args = ap.parse_args(argv)
    try:
        if args.command == 'disc3-inventories':
            result = disc3_inventories(args.work, args.out, epoch=args.epoch)
        elif args.command == 'disc3-plans':
            result = disc3_plans(args.work, args.out, epoch=args.epoch, inventories=args.inventories)
        else:
            from sbom.owner_media import disc_sbom
            result = disc_sbom(args.disc, args.image, args.out, epoch=args.epoch,
                source_revision=args.source_revision, medium_list=args.medium_list,
                medium_root=args.medium_root, manifest=args.manifest, checksums=args.checksums,
                credits=args.credits, notices=args.notices)
        print(canonical(result), end='')
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        # Never echo an external command's stdout/stderr or local operator paths.
        print('Owner SBOM input generation refused (' + type(exc).__name__ + ').', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
