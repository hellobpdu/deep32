#!/usr/bin/env python3
"""Offline alpha.2 producer; every output is new and unsigned."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pack
import runtime_stage as runtime
from provenance import canonical, digest, members, verify_manifest
from alpha2_source import seal_source, verify_source

CLASSES = ('turing', 'ampere80', 'ampere86', 'ada', 'hopper', 'blackwell100', 'blackwell120')
COMPONENTS = ('core', *('embedder-' + value for value in CLASSES), 'ingestion')
VERSION = '0.1.0-alpha.2'


def image_name(component):
    if component not in COMPONENTS:
        raise ValueError('Unknown disc 3 component.')
    return 'deep32-' + VERSION + '-disc3-gpu-pack-' + component + '.iso'


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value), encoding='ascii', newline='\n')


def regular(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('Linked input or output path refused.')
    if not path.is_file():
        raise ValueError('A regular input file is required.')
    return path


def no_overlap(output, inputs):
    output = output.absolute()
    for parent in (output, *output.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('Linked output path refused.')
    if output.exists():
        raise ValueError('Output must be a new directory.')
    target = output.resolve()
    for source in inputs:
        source = Path(source).resolve()
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError('Output overlaps a producer input.')


def selected_allowlist(stage, component):
    """The owning producer chooses only sealed component members, never the cache."""
    kind = 'embedder' if component.startswith('embedder-') else component
    card = component[9:] if kind == 'embedder' else None
    needed = runtime.expected_roles(kind, card)
    rows = runtime.inventory(stage / 'PAYLOAD.json')
    if rows != members(stage / 'payload'):
        raise ValueError('Source payload differs from its sealed inventory.')
    images = runtime.read_json(stage / 'payload/images/index.json')
    names = {row['path'] for row in images if row['role'] in needed}
    if kind != 'embedder':
        attestation = runtime.read_json(stage / 'payload/evidence/input-attestation.json')
        names.add('evidence/input-attestation.json')
        names.update(runtime.relative(name) for name in attestation['records'])
        names.update(row['path'] for row in rows if row['path'].startswith('licences/'))
        if kind == 'core':
            for role in ('tei', 'reranker'):
                names.update(runtime.complete_snapshot(stage / 'payload',
                                                      runtime.read_json(stage / 'pins.json'), role))
            # Runtime staging derives refs from these pinned snapshots.
            # Acquisition refs and blobs are not copied into the core.
        else:
            names.update(row['path'] for row in rows if row['path'].startswith(
                ('models/mineru-home/', 'source/production-converter/')))
    return [row for row in rows if row['path'] in names]


def normalize(root, epoch):
    for path in sorted(root.rglob('*'), reverse=True) + [root]:
        if path.is_symlink() or path.is_junction():
            raise ValueError('Linked media member refused.')
        path.chmod(0o755 if path.is_dir() else pack._thin.runtime_mode(path))
        os.utime(path, (epoch, epoch))


def prepare_media(stage, kit, component, output, epoch):
    """Use the established payload/root manifest format with one Docker form."""
    no_overlap(output, (stage, kit))
    runtime.safe_tree(stage)
    verify_manifest(stage / 'payload', stage / 'PAYLOAD.json')
    receipt = runtime.read_json(stage / 'RUNTIME.json')
    expected_kind = 'embedder' if component.startswith('embedder-') else component
    card = component[9:] if expected_kind == 'embedder' else None
    if (receipt['component'] != expected_kind or receipt['gpu_class'] != card
            or set(receipt['roles']) != runtime.expected_roles(expected_kind, card)
            or receipt['pins_sha256'] != digest(stage / 'pins.json')
            or receipt['payload_sha256'] != digest(stage / 'PAYLOAD.json')):
        raise ValueError('Component receipt differs from selected media.')
    output.mkdir(parents=True)
    def copy(source, name):
        regular(source)
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for row in runtime.inventory(stage / 'PAYLOAD.json'):
        copy(stage / 'payload' / row['path'], 'deep32/payload/' + row['path'])
    for name in ('PAYLOAD.json', 'RUNTIME.json'):
        copy(stage / name, 'deep32/' + name)
    # The pin register is shared configuration, not another copy of payload bytes.
    copy(stage / 'pins.json', 'pins.json')
    (output / 'deep32/PAYLOAD.SHA256').write_text(''.join(
        row['sha256'] + '  ' + row['path'] + '\n'
        for row in runtime.inventory(stage / 'PAYLOAD.json')), encoding='ascii', newline='\n')
    if component == 'core':
        for name, source in pack._thin.runtime_members(kit, stage / 'pins.json').items():
            copy(source, name)
        form = output / 'forms/forge-docker.tar'
        form.parent.mkdir(parents=True, exist_ok=True)
        pack._thin.docker(kit, form, epoch, stage / 'pins.json')
    else:
        # Small licence/provenance evidence accompanies each archive, outside the
        # strict runtime allowlist, without another model or runtime form.
        source = kit / 'release/release-key.gpg'
        if source.exists():
            copy(source, 'release/release-key.gpg')
        for row in members(kit / 'release/addon-licenses') if (kit / 'release/addon-licenses').is_dir() else []:
            # Licence texts stay small; source/ holds the corresponding source an add-on must ship (the ingestion
            # add-on's PyMuPDF is AGPL: its source archive is about 88 MB).
            limit = 256 * 1024 * 1024 if row['path'].startswith('source/') else 1024 * 1024
            if row['size'] >= limit:
                raise ValueError('Add-on license evidence must be smaller than one MiB per file '
                                 '(256 MiB for corresponding source).')
            copy(kit / 'release/addon-licenses' / row['path'], 'licenses/' + row['path'])
    (output / 'SHA256SUMS').write_text(''.join(
        row['sha256'] + '  ' + row['path'] + '\n' for row in members(output)),
        encoding='ascii', newline='\n')
    normalize(output, epoch)
    return members(output)


def build_image(stage, kit, component, output, epoch, run=subprocess.run):
    no_overlap(output, (stage, kit))
    media = output / 'media'
    prepare_media(stage, kit, component, media, epoch)
    mapping = output / 'maps.txt'
    pack.maps(media, mapping)
    image = output / image_name(component)
    completed = run(pack.xorriso_command(image, mapping, epoch), check=True,
        env=dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), LC_ALL='C', TZ='UTC'))
    if completed.returncode != 0:
        raise ValueError('Xorriso did not report success.')
    report = pack.verify_iso(image)
    report.update(format='deep32-alpha2-component/1', component=component,
                  source_date_epoch=epoch, size=image.stat().st_size, sha256=digest(image))
    mask = os.umask(0o022)
    os.umask(mask)
    report.update(clean_build=True,
                  clean_build_method='Fresh media and image output directory required before assembly.',
                  cache_method='Xorriso media assembly uses no saved build cache.',
                  process_exit_results={'xorriso': completed.returncode},
                  environment={'TZ': 'UTC', 'LC_ALL': 'C', 'umask': format(mask, '04o'),
                               'SOURCE_DATE_EPOCH': epoch})
    write_json(output / 'pack-report.json', report)
    (output / 'SHA256SUMS').write_text(report['sha256'] + '  ' + image.name + '\n',
                                      encoding='ascii', newline='\n')
    return image


def sizes(stage, media, image):
    """Measured media plus explicit conservative install-space accounting."""
    from image_binding import check_archive, resolve_loaded
    archive_bytes = 0
    expanded = 0
    images = []
    for row in runtime.read_json(stage / 'payload/images/index.json'):
        archive = stage / 'payload' / row['path']
        check_archive(archive, row, row.get('source_digest'))
        subprocess.run(['docker', 'image', 'load', '--input', str(archive)], check=True,
                       stdout=subprocess.DEVNULL)
        image_id = resolve_loaded(row)
        inspection = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image_id], text=True))
        if (len(inspection) != 1 or inspection[0].get('Id') != image_id
                or inspection[0].get('Os') != 'linux' or inspection[0].get('Architecture') != 'amd64'
                or type(inspection[0].get('Size')) is not int or inspection[0]['Size'] < 0):
            raise ValueError('Missing measured Docker layer size.')
        archive_bytes += archive.stat().st_size
        expanded += inspection[0]['Size']
        images.append({'role': row['role'], 'config_digest': row['config_digest'],
                       'layer_bytes': inspection[0]['Size']})
    media_bytes = sum(row['size'] for row in members(media))
    installed = media_bytes - archive_bytes + expanded
    download = image.stat().st_size
    return dict(download_bytes=download, media_file_bytes=media_bytes,
                installed_bytes=installed, peak_bytes=download + media_bytes + installed,
                image_sizes=images,
                accounting='Logical file and Docker layer bytes; counts each image separately; excludes filesystem overhead and build workspace.')


def dry_plan(args):
    """Print exact public commands without reading or creating any build inputs."""
    python = ['python3', '-I', '-B']
    script = args.checkout / 'src/gpu-pack/build/alpha2_disc3.py'
    base = [str(script), '--checkout', str(args.checkout), '--sealed-stage', str(args.sealed_stage),
            '--out', str(args.out), '--epoch', str(args.epoch),
            '--release-gates', str(args.release_gates),
            '--sbom-inventories', str(args.sbom_inventories)]
    if args.assembly_manifest:
        base += ['--assembly-manifest', str(args.assembly_manifest)]
    print('# Offline commands; Docker RUN steps have no network; no signing occurs.')
    # The same phase commands are executed by the real orchestrator below.
    for phase in ('copy', 'rebuild', 'stage', 'images', 'metadata', 'finish'):
        print(shlex.join(python + base + ['--phase', phase]))
    # List nested image operations rather than hiding them behind a phase name.
    print('# Full/model_bridge operations apply only when both saved roles already exist.')
    helper = load_module('alpha2_rebuild_plan', HERE / 'alpha2_rebuild.py')
    for command in helper.plan(args.out / 'stage', args.checkout / 'src/gpu-pack',
                               args.out / 'rebuild', args.epoch,
                               roles=(*helper.BASE_ROLES, *helper.FULL_ROLES)):
        print(shlex.join(command))
    for component in (*COMPONENTS, 'core-repeat'):
        actual = 'core' if component == 'core-repeat' else component
        print(shlex.join(pack.xorriso_command(args.out / 'components' / component / image_name(actual),
                                             args.out / 'components' / component / 'maps.txt', args.epoch)))
        print(shlex.join(python + [str(HERE / 'pack.py'), 'verify',
                        str(args.out / 'components' / component / image_name(actual))]))
    for component in (*COMPONENTS, 'core-repeat'):
        actual = 'core' if component == 'core-repeat' else component
        print(shlex.join(python + [str(HERE / 'alpha2_metadata.py'), 'emit',
                        '--repo', str(args.checkout),
                        '--image', str(args.out / 'sbom-input-images' / component / image_name(actual)),
                        '--media', str(args.out / 'components' / component / 'media'),
                        '--stage', str(args.out / 'stages' / component),
                        '--out', str(args.out / 'sboms' / component),
                        '--epoch', str(args.epoch), '--source-revision', '${SEALED_SOURCE_REVISION}',
                        '--inventory', str(args.sbom_inventories / (actual + '.json'))]))
    print(shlex.join(python + [str(HERE / 'alpha2_metadata.py'), 'compare-core',
                     '--first', str(args.out / 'components/core' / image_name('core')),
                     '--second', str(args.out / 'components/core-repeat' / image_name('core')),
                     '--stage', str(args.out / 'stage'), '--out', str(args.out / 'reproducibility'),
                     '--epoch', str(args.epoch), '--source-revision', '${SEALED_SOURCE_REVISION}',
                     '--run-receipt', str(args.out / 'components/core/RUN.json'),
                     '--run-receipt', str(args.out / 'components/core-repeat/RUN.json')]))
    print('# Size measurement loads each sealed archive and inspects its immutable ID; no container starts.')
    for role in ('door', 'reranker', *('tei-' + c for c in CLASSES), 'converter', 'builder', 'qdrant'):
        print(shlex.join(['docker', 'image', 'load', '--input', str(args.out / 'stage/payload/images' / (role + '.tar'))]))
        print(shlex.join(['docker', 'image', 'inspect', '${SEALED_IMMUTABLE_ID_FOR_' + role + '}']))
    if args.assembly_manifest:
        print(shlex.join(python + [str(args.checkout / 'src/release-tools/alpha2_assemble.py'),
                        '--manifest', str(args.assembly_manifest), '--output', str(args.out / 'release-unsigned')]))


def phase_copy(args):
    runtime.safe_tree(args.sealed_stage)
    no_overlap(args.out, (args.sealed_stage, args.checkout))
    runtime.validate_source(args.sealed_stage, args.checkout / 'src/gpu-pack')
    args.out.mkdir(parents=True)
    seal_source(args.checkout, args.out / 'SOURCE.json')
    before = members(args.sealed_stage)
    shutil.copytree(args.sealed_stage, args.out / 'stage')
    if before != members(args.sealed_stage) or before != members(args.out / 'stage'):
        raise ValueError('Private stage copy differs from sealed source.')
    write_json(args.out / 'SEALED-SOURCE.json', before)
    # Supply public verification inputs only in the private kit copy.
    shutil.copytree(args.checkout / 'src/gpu-pack', args.out / 'kit',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('release-key.gpg', 'release-key.fingerprint'):
        target = args.out / 'kit/release' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(regular(args.checkout / 'install' / name), target)
    shutil.copytree(args.sealed_stage / 'payload/licences', args.out / 'kit/release/addon-licenses')
    write_json(args.out / 'KIT.json', members(args.out / 'kit'))


def verify_kit(args):
    runtime.safe_tree(args.out / 'kit')
    if members(args.out / 'kit') != runtime.read_json(regular(args.out / 'KIT.json')):
        raise ValueError('The private runtime kit differs from its source seal.')


def verify_stages(args):
    runtime.safe_tree(args.out / 'stages')
    if members(args.out / 'stages') != runtime.read_json(regular(args.out / 'STAGES.json')):
        raise ValueError('A runtime component stage changed after sealing.')


def phase_rebuild(args):
    verify_source(args.checkout, args.out / 'SOURCE.json')
    verify_kit(args)
    helper = load_module('alpha2_rebuild', HERE / 'alpha2_rebuild.py')
    if (args.out / 'rebuild/MERGE.json').exists():
        merge = runtime.read_json(args.out / 'rebuild/MERGE.json')
        if (merge.get('status') != 'qualified-and-merged'
                or merge.get('merged_pins_sha256') != digest(args.out / 'stage/pins.json')
                or merge.get('merged_payload_sha256') != digest(args.out / 'stage/PAYLOAD.json')):
            raise ValueError('Existing merge receipt differs from the private stage.')
        return
    if (args.out / 'rebuild/BUILD.json').exists():
        helper.qualify_and_merge(args.out / 'stage', args.checkout / 'src/gpu-pack',
                                 args.out / 'rebuild', gates=args.release_gates)
    else:
        helper.rebuild(args.out / 'stage', args.checkout / 'src/gpu-pack',
                       args.out / 'rebuild', args.epoch, gates=args.release_gates)


def phase_stage(args):
    verify_source(args.checkout, args.out / 'SOURCE.json')
    verify_kit(args)
    stage, kit = args.out / 'stage', args.out / 'kit'
    merge = runtime.read_json(regular(args.out / 'rebuild/MERGE.json'))
    if (merge.get('status') != 'qualified-and-merged'
            or merge.get('merged_pins_sha256') != digest(stage / 'pins.json')
            or merge.get('merged_payload_sha256') != digest(stage / 'PAYLOAD.json')):
        raise ValueError('Qualified rebuild receipt does not match the private stage.')
    for component in COMPONENTS:
        allow = args.out / 'allowlists' / (component + '.json')
        write_json(allow, selected_allowlist(stage, component))
        kind = 'embedder' if component.startswith('embedder-') else component
        card = component[9:] if kind == 'embedder' else None
        for name in (component, 'core-repeat') if component == 'core' else (component,):
            runtime.stage_runtime(stage, kit, allow, kind,
                                  args.out / 'stages' / name, args.epoch, card)
    write_json(args.out / 'STAGES.json', members(args.out / 'stages'))


def phase_images(args):
    verify_source(args.checkout, args.out / 'SOURCE.json')
    verify_kit(args)
    verify_stages(args)
    kit = args.out / 'kit'
    (args.out / 'images').mkdir()
    for component in (*COMPONENTS, 'core-repeat'):
        actual = 'core' if component == 'core-repeat' else component
        image = build_image(args.out / 'stages' / component, kit, actual,
                            args.out / 'components' / component, args.epoch)
        inputs = args.out / 'sbom-input-images' / component
        inputs.mkdir(parents=True)
        os.link(image, inputs / image.name)
        write_json(inputs / 'IMAGE.json', {'path': image.name, 'size': image.stat().st_size,
                                          'sha256': digest(image)})
        if component != 'core-repeat':
            os.link(image, args.out / 'images' / image.name)
            (args.out / 'images' / (image.name + '.sha256')).write_text(
                digest(image) + '  ' + image.name + '\n', encoding='ascii', newline='\n')


def phase_metadata(args):
    verify_kit(args)
    verify_stages(args)
    metadata = load_module('alpha2_metadata', HERE / 'alpha2_metadata.py')
    # Frozen source and input hashes identify the comparison, never the host path.
    source = verify_source(args.checkout, args.out / 'SOURCE.json')['revision']
    ledger = []
    image_rows = []
    metadata_rows = {}
    for component in COMPONENTS:
        stage = args.out / 'stages' / component
        folder = args.out / 'components' / component
        image = folder / image_name(component)
        operation = metadata.verify_component_sbom if (args.out / 'sboms' / component).exists() else metadata.emit_component_sbom
        emitted = operation(args.checkout, args.out / 'sbom-input-images' / component / image.name,
                                     folder / 'media', stage,
                                     args.out / 'sboms' / component, args.epoch, source,
                                     inventory_path=args.sbom_inventories / (component + '.json'))
        image_rows.append({'id': component, 'source': 'images/' + image.name,
                           'sums': 'images/' + image.name + '.sha256',
                           'sbom': 'sboms/' + component + '/metadata/' + emitted['sbom']})
        index = runtime.read_json(args.out / 'sboms' / component / 'metadata/SBOM-INDEX.json')
        for subject in index['subjects']:
            references = list(subject['contents'])
            if subject['gap'] is not None:
                references.append(subject['gap'])
            if subject['subject']['kind'] != 'disc':
                references.append({'path': subject['sbom']['asset'], 'sha256': subject['sbom']['sha256']})
            for reference in references:
                name = reference['path']
                item = regular(args.out / 'sboms' / component / 'metadata' / name)
                if '/' in name or digest(item) != reference['sha256']:
                    raise ValueError('Required public SBOM metadata differs.')
                if name in metadata_rows and metadata_rows[name][0] != reference['sha256']:
                    raise ValueError('Required public metadata filenames conflict.')
                metadata_rows[name] = (reference['sha256'], {'file': name,
                    'source': 'sboms/' + component + '/metadata/' + name, 'discs': ['disc3']})
        ledger.append(dict(id=component, image=image.name, sha256=digest(image),
                           **sizes(stage, folder / 'media', image)))
    write_json(args.out / 'SIZES.json', {'format': 'deep32-alpha2-sizes/1', 'images': ledger})
    write_json(args.out / 'DISC3-IMAGES.json', {'images': image_rows,
               'disc3_image_dir': 'images', 'sbom_indexes': [
                   'sboms/' + component + '/metadata/SBOM-INDEX.json' for component in COMPONENTS],
                'metadata': [metadata_rows[name][1] for name in sorted(metadata_rows)] + [
                    {'file': 'core-comparison.receipt.json',
                     'source': 'reproducibility/core-comparison.receipt.json', 'discs': ['disc3']},
                    {'file': 'SIZES.json', 'source': 'SIZES.json', 'discs': ['disc3']}],
                'reproducibility': 'reproducibility/REPRODUCIBILITY.json'})
    # The repeat uses identical metadata inputs through a distinct root mapping.
    repeated = args.out / 'components/core-repeat'
    repeat_operation = metadata.verify_component_sbom if (args.out / 'sboms/core-repeat').exists() else metadata.emit_component_sbom
    repeat_operation(args.checkout, args.out / 'sbom-input-images/core-repeat' / image_name('core'), repeated / 'media',
                                 args.out / 'stages/core-repeat', args.out / 'sboms/core-repeat',
                                 args.epoch, source, inventory_path=args.sbom_inventories / 'core.json')
    runs = []
    for component in ('core', 'core-repeat'):
        folder = args.out / 'components' / component
        runs.append(metadata.record_build_run(args.out / 'stage', args.epoch, source,
            folder / image_name('core'), folder / 'RUN.json', component,
            folder / 'pack-report.json', args.out / 'sboms' / component / 'COVERAGE.json'))
    comparison = metadata.compare_core(args.out / 'components/core' / image_name('core'),
                          args.out / 'components/core-repeat' / image_name('core'),
                          args.out / 'reproducibility', args.epoch, source,
                          args.out / 'stage', run_receipts=runs)
    if comparison['status'] != 'proven' or not comparison['byte_comparison']['equal']:
        raise ValueError('Core media reproducibility qualification did not pass.')


def phase_finish(args):
    source = verify_source(args.checkout, args.out / 'SOURCE.json')['revision']
    verify_kit(args)
    verify_stages(args)
    metadata = load_module('alpha2_metadata_finish', HERE / 'alpha2_metadata.py')
    if members(args.sealed_stage) != runtime.read_json(args.out / 'SEALED-SOURCE.json'):
        raise ValueError('Original sealed stage changed during the build.')
    ledger = runtime.read_json(regular(args.out / 'SIZES.json'))
    rows = ledger.get('images', [])
    if len(rows) != len(COMPONENTS) or {row.get('id') for row in rows} != set(COMPONENTS):
        raise ValueError('Size ledger does not cover exactly nine images.')
    expected_files = {image_name(c) + suffix for c in COMPONENTS for suffix in ('', '.sha256')}
    if {path.name for path in (args.out / 'images').iterdir()} != expected_files:
        raise ValueError('Final image directory has missing or extra files.')
    for row in rows:
        image = regular(args.out / 'images' / image_name(row['id']))
        if (row.get('image') != image.name or row.get('sha256') != digest(image)
                or row.get('download_bytes') != image.stat().st_size
                or (args.out / 'images' / (image.name + '.sha256')).read_text(encoding='ascii')
                    != row['sha256'] + '  ' + image.name + '\n'):
            raise ValueError('A final image differs from its measured ledger.')
        pack.verify_iso(image)
        coverage = runtime.read_json(regular(args.out / 'sboms' / row['id'] / 'COVERAGE.json'))
        if coverage.get('sha256') != row['sha256'] or coverage.get('size') != row['download_bytes']:
            raise ValueError('Final image SBOM coverage differs.')
        metadata.verify_component_sbom(args.checkout,
            args.out / 'sbom-input-images' / row['id'] / image.name,
            args.out / 'components' / row['id'] / 'media', args.out / 'stages' / row['id'],
            args.out / 'sboms' / row['id'], args.epoch, source,
            inventory_path=args.sbom_inventories / (row['id'] + '.json'))
    repro = runtime.read_json(regular(args.out / 'reproducibility/REPRODUCIBILITY.json'))
    if (len(repro.get('subjects', [])) != 1 or repro['subjects'][0].get('status') != 'proven'
            or repro['subjects'][0]['subject']['sha256'] != digest(args.out / 'images' / image_name('core'))):
        raise ValueError('Shared core comparison is missing or not proven.')
    if args.assembly_manifest:
        assembly = load_module('alpha2_assemble', args.checkout / 'src/release-tools/alpha2_assemble.py')
        prepared = assembly.prepare(args.assembly_manifest)
        assembly.validate_media(prepared['capability'], {
            component: args.out / 'components' / component / 'media' for component in COMPONENTS})
        assembly.assemble(args.assembly_manifest, args.out / 'release-unsigned')
    write_json(args.out / 'PRODUCER.json', {'format': 'deep32-alpha2-producer/1',
               'components': list(COMPONENTS), 'source_date_epoch': args.epoch,
               'signed': False, 'assembled': bool(args.assembly_manifest)})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('checkout', 'sealed-stage', 'out', 'release-gates', 'sbom-inventories'):
        ap.add_argument('--' + name, type=Path, required=True)
    ap.add_argument('--epoch', type=int, required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--phase', choices=('copy', 'rebuild', 'stage', 'images', 'metadata', 'finish'))
    ap.add_argument('--assembly-manifest', type=Path)
    args = ap.parse_args(argv)
    try:
        if args.epoch < 0:
            raise ValueError('Epoch must be nonnegative.')
        dt.datetime.fromtimestamp(args.epoch, dt.timezone.utc)
        if args.dry_run:
            dry_plan(args)
            return 0
        if os.name != 'posix' or sys.platform != 'linux':
            raise ValueError('Actual production requires the isolated Linux build host.')
        if HERE.resolve() != (args.checkout / 'src/gpu-pack/build').resolve():
            raise ValueError('Use the producer from the selected source checkout.')
        os.umask(0o022)
        for name in (args.phase,) if args.phase else ('copy', 'rebuild', 'stage', 'images', 'metadata', 'finish'):
            globals()['phase_' + name](args)
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError, subprocess.SubprocessError) as exc:
        print('Producer stopped: ' + (str(exc) if not isinstance(exc, OSError)
              else 'missing or unreadable build input') + '.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
