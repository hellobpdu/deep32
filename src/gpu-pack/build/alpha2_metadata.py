#!/usr/bin/env python3
"""Measure external metadata and narrowly scoped build comparisons.

Inventory plans are public, sealed producer inputs, never a scanner
or a substitute for complete config-bound installed inventories.
"""
import argparse
import datetime as dt
import hashlib
import json
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / 'src/release-tools'))
import pack
import runtime_stage
from provenance import canonical, digest, members
from sbom.common import (identifier, keys, load_json, ordinary, public_path,
                         public_text, sha256)
from sbom.inputs import normalise_inputs
from sbom.frozen import bind
from sbom.workflow import contents, finalise, generator_sha256, verify

RELEASE = 'v0.1.0-alpha.2'
PLAN = 'deep32-alpha2-sbom-plan/1'


def fail(message):
    error = ValueError(message)
    error.metadata_refusal = True
    raise error


def _hash(value):
    return hashlib.sha256(canonical(value).encode('ascii')).hexdigest()


def _write(path, value):
    path.write_text(canonical(value), encoding='ascii', newline='\n')


def _epoch_revision(epoch, revision):
    if type(epoch) is not int or epoch < 0:
        fail('A nonnegative epoch is required.')
    dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
    if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision):
        fail('A resolved producer source revision is required.')


def _new_output(output, inputs):
    output = Path(output).absolute()
    ordinary(output.parent, directory=True)
    if output.exists() or output.is_symlink():
        fail('The metadata output directory must be new.')
    for source in inputs:
        source = Path(source).absolute()
        if output == source or output.is_relative_to(source) or source.is_relative_to(output):
            fail('Metadata output overlaps an input.')
    return output


def input_identity(stage, epoch, source_revision):
    """Hash the complete public stage without opening file text."""
    _epoch_revision(epoch, source_revision)
    stage = ordinary(stage, directory=True)
    runtime_stage.safe_tree(stage)
    # Check names before hashing; excluded input files must never be opened.
    for path in stage.rglob('*'):
        public_path(path.relative_to(stage).as_posix())
    runtime_stage.inventory(stage / 'PAYLOAD.json')
    if load_json(stage / 'PAYLOAD.json') != members(stage / 'payload'):
        fail('The source payload differs from its sealed inventory.')
    identity = dict(source_date_epoch=epoch, source_revision=source_revision,
                    files=members(stage))
    return dict(**identity, sha256=_hash(identity))


def recipe_identity():
    """Bind actual producer recipes separately from stage inputs."""
    paths = sorted(HERE.glob('*.py'))
    script = HERE / 'build-alpha2-disc3.sh'
    if script.is_file():
        paths.append(script)
    return _hash([dict(path=p.relative_to(REPO).as_posix(), sha256=digest(ordinary(p)))
                  for p in sorted(paths)])


def _plan_roots(plan, image, media, stage, inventory):
    bases = {'media': media, 'stage': stage, 'inventory': inventory.parent,
             'image-parent': image.parent}
    roots = {}
    for row in plan['roots']:
        keys(row, {'id', 'source', 'path'})
        name = identifier(row['id'])
        if name in roots or row['source'] not in bases:
            fail('Inventory root mappings must be unique and supported.')
        path = row['path']
        if path != '.':
            public_path(path)
        roots[name] = ordinary(bases[row['source']] / path, directory=True)
    return roots


def _measured_media(normal, roots, media):
    covered = set()
    for component in normal['components']:
        for field in ('files', 'source_files', 'licence_evidence'):
            covered.update((roots[r['root']] / r['path']).absolute() for r in component[field])
    covered.update((roots[s['file']['root']] / s['file']['path']).absolute()
                   for s in normal['subjects'])
    # Generated checksum files are measured in the roots but cannot self-hash.
    actual = {ordinary(p) for p in media.rglob('*') if p.is_file()
              and p.name not in {'SHA256SUMS', 'PAYLOAD.SHA256'}}
    if not actual <= covered:
        fail('Every nongenerated media file needs explicit component or subject coverage.')
    return len(actual)


def emit_component_sbom(repo, image_path, media_path, stage_path, output_dir,
                        epoch, source_revision, *, inventory_path, _verify_output=False):
    """Validate an explicit plan and run the merged SPDX emitter.

    Plan fields are exactly format, manifest, contract and roots.
    manifest is deep32-sbom-input/1; contract is the frozen shared
    deep32-sbom-input version 1; roots are {id,source,path} with source media,
    stage, inventory, or image-parent and a safe relative path (or a single dot).
    Container inventory references must name sealed complete final
    inventories bound to the saved archive config digest, including inherited
    packages; unknown optional licenses require NOASSERTION and public gaps.
    """
    _epoch_revision(epoch, source_revision)
    if Path(repo).resolve() != REPO:
        fail('Use the helper from the selected producer checkout.')
    image = ordinary(image_path)
    public_path(image.name)
    media = ordinary(media_path, directory=True)
    stage = ordinary(stage_path, directory=True)
    inventory = ordinary(inventory_path)
    public_path(inventory.name)
    output = (ordinary(output_dir, directory=True) if _verify_output else
              _new_output(output_dir, (image, media, stage, inventory.parent)))
    if _verify_output:
        for source in (image, media, stage, inventory.parent):
            if output == source or output.is_relative_to(source) or source.is_relative_to(output):
                fail('Completed metadata overlaps a producer input.')
    runtime_stage.safe_tree(media)
    runtime_stage.safe_tree(stage)
    if load_json(stage / 'PAYLOAD.json') != members(stage / 'payload'):
        fail('The runtime payload differs from its sealed inventory.')
    # A detached media tree must still contain the selected bytes it describes.
    for row in load_json(stage / 'PAYLOAD.json'):
        member = ordinary(media / 'deep32/payload' / public_path(row['path']))
        if member.stat().st_size != row['size'] or digest(member) != row['sha256']:
            fail('The media payload differs from the runtime stage.')
    plan = load_json(inventory)
    keys(plan, {'format', 'manifest', 'contract', 'roots'})
    if plan['format'] != PLAN or not isinstance(plan['roots'], list):
        fail('A versioned sealed inventory plan is required.')
    manifest, contract = plan['manifest'], plan['contract']
    for key, expected in [('release', RELEASE), ('source_date_epoch', epoch),
                          ('source_revision', source_revision), ('generator_sha256', generator_sha256())]:
        if manifest.get(key) != expected:
            fail('The inventory plan differs from the producer identity.')
    roots = _plan_roots(plan, image, media, stage, inventory)
    # Files outside those roots are never discovered or imported by the emitter.
    # Validation happens before any completed output is published.
    import tempfile
    temporary = Path(tempfile.mkdtemp(prefix='.alpha2-metadata-', dir=output.parent))
    try:
        measured = temporary / 'MEASURED.json'
        frozen = temporary / 'FROZEN.json'
        _write(measured, manifest)
        _write(frozen, contract)
        normal = normalise_inputs(measured, roots)
        bind(frozen, normal)
        discs = [s for s in normal['subjects'] if s['kind'] == 'disc']
        if (len(discs) != 1 or discs[0]['file']['path'] != image.name
                or discs[0]['file']['size'] != image.stat().st_size
                or discs[0]['file']['sha256'] != digest(image)
                or roots[discs[0]['file']['root']] / discs[0]['file']['path'] != image):
            fail('The inventory plan must describe exactly this frozen component image.')
        selected = load_json(stage / 'payload/images/index.json')
        if not isinstance(selected, list):
            fail('A selected runtime image index is required.')
        expected = {row['role']: row for row in selected}
        containers = {s['role']: s for s in normal['subjects'] if s['kind'] == 'container'}
        if len(expected) != len(selected) or set(expected) != set(containers):
            fail('Saved container coverage differs from the selected runtime image index.')
        for role, row in expected.items():
            item = containers[role]
            archive = ordinary(media / 'deep32/payload' / public_path(row['path']))
            if (item['file']['sha256'] != row['sha256']
                    or roots[item['file']['root']] / item['file']['path'] != archive):
                fail('A saved container binding differs from the selected runtime archive.')
        measured_count = _measured_media(normal, roots, media)
        contents(measured, roots, temporary / 'contents')
        finalise(measured, roots, temporary / 'contents', temporary / 'metadata', contract_path=frozen)
        result = verify(measured, roots, temporary / 'metadata', contract_path=frozen)
        coverage = dict(format='deep32-alpha2-media-coverage/1',
                        subject=image.name, size=image.stat().st_size, sha256=digest(image),
                        media_files=measured_count, selected_containers=sorted(expected),
                        validation='selected SPDX profile; official schema not supplied',
                        component_coverage=discs[0]['component_coverage'],
                        licence_coverage=discs[0]['licence_coverage'],
                        plan_sha256=digest(inventory))
        _write(temporary / 'COVERAGE.json', coverage)
        if _verify_output:
            runtime_stage.safe_tree(output)
            if members(temporary) != members(output):
                fail('Completed component metadata differs from the current sealed plan and measured inputs.')
        else:
            temporary.rename(output)
        return dict(**coverage, metadata='metadata', index='metadata/SBOM-INDEX.json',
                    sbom=next(r['sbom']['asset'] for r in load_json(output / 'metadata/SBOM-INDEX.json')['subjects']
                              if r['subject']['kind'] == 'disc'), verified_subjects=result['subjects'])
    finally:
        if temporary.exists():
            if temporary.parent != output.parent or not temporary.name.startswith('.alpha2-metadata-'):
                fail('The temporary metadata directory escaped its output parent.')
            shutil.rmtree(temporary)


def verify_component_sbom(repo, image_path, media_path, stage_path, output_dir,
                          epoch, source_revision, *, inventory_path):
    """Remeasure completed metadata without changing any input."""
    return emit_component_sbom(repo, image_path, media_path, stage_path, output_dir,
                                epoch, source_revision, inventory_path=inventory_path,
                                _verify_output=True)


def record_build_run(stage, epoch, revision, image, output, run_id,
                     build_result_path, coverage_path):
    """Record measured producer, toolchain and coverage evidence.

    The producer must record its actual clean-directory method,
    process exit result and controlled environment at the build checkpoint.
    This function rechecks final bytes, media checksums and emitted
    metadata; it records local tool versions without starting any container.
    """
    _epoch_revision(epoch, revision)
    identifier(run_id)
    image = ordinary(image)
    result = load_json(build_result_path)
    coverage_path = ordinary(coverage_path)
    coverage = load_json(coverage_path)
    target = Path(output).absolute()
    ordinary(target.parent, directory=True)
    if target.exists() or target.is_symlink():
        fail('The build-run receipt must be a new file.')
    if (result.get('format') != 'deep32-alpha2-component/1'
            or result.get('sha256') != digest(image)
            or result.get('size') != image.stat().st_size
            or result.get('source_date_epoch') != epoch):
        fail('The completed build report differs from its frozen ISO.')
    environment = dict(TZ='UTC', LC_ALL='C', umask='0022', SOURCE_DATE_EPOCH=epoch)
    if (result.get('environment') != environment or result.get('clean_build') is not True
            or result.get('process_exit_results') != {'xorriso': 0}):
        fail('The build report lacks successful controlled clean-build evidence.')
    for field in ('clean_build_method', 'cache_method'):
        public_text(result[field])
    pack.verify_iso(image)
    media = ordinary(image.parent / 'media', directory=True)
    runtime_stage.safe_tree(media)
    rows = members(media)
    expected = ''.join(row['sha256'] + '  ' + row['path'] + '\n'
                       for row in rows if row['path'] != 'SHA256SUMS')
    if ordinary(media / 'SHA256SUMS').read_text(encoding='ascii') != expected:
        fail('The final media checksum list is missing, changed or incomplete.')
    if (coverage.get('format') != 'deep32-alpha2-media-coverage/1'
            or coverage.get('subject') != image.name
            or coverage.get('sha256') != digest(image)
            or coverage.get('size') != image.stat().st_size
            or coverage.get('component_coverage') != 'complete'
            or coverage.get('media_files') != len([r for r in rows
                 if r['path'].rsplit('/', 1)[-1] not in {'SHA256SUMS', 'PAYLOAD.SHA256'}])):
        fail('The measured SBOM coverage differs from the final component media.')
    metadata_dir = ordinary(coverage_path.parent / 'metadata', directory=True)
    listing = load_json(metadata_dir / 'METADATA.json')
    expected_names = {'METADATA.json'}
    for row in listing['files']:
        public_path(row['path'])
        if '/' in row['path'] or row['path'] in expected_names:
            fail('The emitted metadata inventory contains an unsafe or duplicate asset.')
        expected_names.add(row['path'])
        item = ordinary(metadata_dir / row['path'])
        if item.stat().st_size != row['size'] or digest(item) != row['sha256']:
            fail('An emitted SBOM metadata asset differs from its inventory.')
    if {p.name for p in metadata_dir.iterdir()} != expected_names:
        fail('The emitted metadata directory has missing or extra assets.')
    index = load_json(metadata_dir / 'SBOM-INDEX.json')
    subjects = [r for r in index['subjects'] if r['subject']['kind'] == 'disc']
    if (len(subjects) != 1 or any(subjects[0]['subject'][key] != value for key, value in
                dict(path=image.name, size=image.stat().st_size, sha256=digest(image)).items())):
        fail('The emitted SBOM index differs from the final component image.')
    xorriso_name = shutil.which('xorriso')
    if not xorriso_name:
        fail('The actual local xorriso tool is required for a build-run receipt.')
    xorriso = ordinary(Path(xorriso_name).resolve())
    interpreter = ordinary(Path(sys.executable).resolve())
    version = subprocess.run([str(xorriso), '-version'], check=True, capture_output=True, text=True)
    lines = (version.stdout + '\n' + version.stderr).splitlines()
    version_line = next((line.strip() for line in lines if line.strip().startswith('xorriso version')), None)
    if version_line is None:
        fail('The local xorriso version output is unsupported.')
    match = re.fullmatch(r'xorriso version\s*:?\s*([0-9][A-Za-z0-9_.+-]*)', version_line)
    if match is None:
        fail('The measured xorriso version cannot be represented by the public contract.')
    version_number = identifier(match.group(1))
    machine = platform.machine().lower()
    if machine not in ('amd64', 'x86_64'):
        fail('This producer requires the measured amd64 build architecture.')
    toolchain = dict(platform=public_text(sys.platform), architecture='amd64',
                     tool_versions=dict(python=public_text(platform.python_version()),
                                        python_binary_sha256=digest(interpreter),
                                        xorriso=version_number, xorriso_binary_sha256=digest(xorriso)))
    identity = input_identity(stage, epoch, revision)
    receipt = dict(format='deep32-alpha2-build-run/1', run_id=run_id, clean_build=True,
                   clean_build_method=result['clean_build_method'], cache_method=result['cache_method'],
                   input_manifest_sha256=identity['sha256'], recipe_sha256=recipe_identity(),
                   toolchain_sha256=_hash(toolchain), **toolchain, environment=environment,
                   process_exit_results=result['process_exit_results'],
                   verification=dict(data_only_iso=True, media_manifest=True, sbom_coverage=True),
                   output=dict(path=image.name, size=image.stat().st_size, sha256=digest(image)))
    with target.open('x', encoding='ascii', newline='\n') as stream:
        stream.write(canonical(receipt))
    return target


def _run_receipt(path, identity, recipe, epoch, output):
    run = load_json(path)
    keys(run, {'format', 'run_id', 'clean_build', 'clean_build_method', 'cache_method',
               'input_manifest_sha256', 'recipe_sha256', 'toolchain_sha256', 'environment',
               'platform', 'architecture', 'tool_versions', 'process_exit_results', 'verification', 'output'})
    if run['format'] != 'deep32-alpha2-build-run/1':
        fail('An unsupported build-run receipt was supplied.')
    identifier(run['run_id'])
    for key in ('clean_build_method', 'cache_method', 'platform', 'architecture'):
        public_text(run[key])
    for field in ('tool_versions', 'process_exit_results', 'verification'):
        if not isinstance(run[field], dict) or not run[field]:
            fail('Run receipts require measured process, toolchain and verification results.')
        for key in run[field]:
            identifier(key)
    for value in run['tool_versions'].values():
        public_text(value)
    sha256(run['input_manifest_sha256'])
    sha256(run['recipe_sha256'])
    sha256(run['toolchain_sha256'])
    expected_toolchain = _hash({key: run[key] for key in ('platform', 'architecture', 'tool_versions')})
    if run['toolchain_sha256'] != expected_toolchain:
        fail('The recorded toolchain hash differs from its measured version record.')
    if run['input_manifest_sha256'] != identity['sha256'] or run['recipe_sha256'] != recipe:
        fail('A run receipt differs from the measured inputs or producer recipes.')
    if run['output'] != output:
        fail('A run receipt differs from its measured final output.')
    if run['environment'] != dict(TZ='UTC', LC_ALL='C', umask='0022', SOURCE_DATE_EPOCH=epoch):
        fail('A run receipt lacks the required controlled build environment.')
    if type(run['clean_build']) is not bool:
        fail('The clean-build assertion must be a boolean.')
    if any(type(value) is not int for value in run['process_exit_results'].values()):
        fail('Process exit results must be measured integers.')
    if any(type(value) is not bool for value in run['verification'].values()):
        fail('Verification outcomes must be booleans.')
    return run


def compare_core(first, second, output_dir, epoch, source_revision, source_stage,
                 *, run_receipts=None):
    """Compare full final ISO bytes; never invent a second build."""
    _epoch_revision(epoch, source_revision)
    first, second = ordinary(first), ordinary(second)
    stage = ordinary(source_stage, directory=True)
    inputs = [first, second, stage]
    if run_receipts is not None:
        if len(run_receipts) != 2:
            fail('Exactly two run receipts are required.')
        inputs.extend(run_receipts)
    output = _new_output(output_dir, inputs)
    identity = input_identity(stage, epoch, source_revision)
    recipe = recipe_identity()
    first_verify, second_verify = pack.verify_iso(first), pack.verify_iso(second)
    outputs = [dict(path=public_path(p.name), size=p.stat().st_size, sha256=digest(p))
               for p in (first, second)]
    equal = outputs[0]['size'] == outputs[1]['size']
    with first.open('rb') as left, second.open('rb') as right:
        while True:
            a, b = left.read(1024 * 1024), right.read(1024 * 1024)
            if a != b:
                equal = False
                break
            if not a:
                break
    runs = []
    reasons = []
    if first == second or first.samefile(second):
        reasons.append('Two distinct final output files were not supplied.')
    if run_receipts is None:
        reasons.append('Clean build, process, environment and toolchain receipts were not supplied.')
    else:
        runs = [_run_receipt(p, identity, recipe, epoch, result)
                for p, result in zip(run_receipts, outputs)]
        if runs[0]['run_id'] == runs[1]['run_id']:
            reasons.append('The two clean build run identifiers are not distinct.')
        if runs[0]['toolchain_sha256'] != runs[1]['toolchain_sha256']:
            reasons.append('The recorded toolchains differ between runs.')
        for run in runs:
            if not run['clean_build'] or any(value != 0 for value in run['process_exit_results'].values()):
                reasons.append('A clean build or successful process result is missing.')
            if not all(run['verification'].get(key) is True for key in
                       ('data_only_iso', 'media_manifest', 'sbom_coverage')) or not all(run['verification'].values()):
                reasons.append('Both runs need passing ISO, media-manifest and exact SBOM coverage checks.')
    if not equal:
        reasons.append('The two final ISO byte streams differ.')
    status = 'not-proven' if reasons else 'proven'
    receipt = dict(format='deep32-alpha2-comparison-receipt/1', release=RELEASE,
                   source_revision=source_revision, source_date_epoch=epoch,
                   scope='media-assembly', status=status, inputs=identity,
                   recipe_sha256=recipe, generator_sha256=digest(Path(__file__)),
                   runs=runs, outputs=outputs, byte_comparison=dict(equal=equal, exit_result=0 if equal else 1),
                   verification=dict(first=first_verify, second=second_verify), reasons=reasons)
    output.mkdir()
    _write(output / 'core-comparison.receipt.json', receipt)
    public_receipt = dict(path='core-comparison.receipt.json', sha256=digest(output / 'core-comparison.receipt.json'))
    subject = dict(path=first.name, size=outputs[0]['size'], sha256=outputs[0]['sha256'])
    row = dict(subject=subject, scope='media-assembly', status=status,
               input_manifest_sha256=identity['sha256'], recipe_sha256=recipe,
               generator_sha256=receipt['generator_sha256'],
               toolchain_sha256=runs[0]['toolchain_sha256'] if runs else None,
               run_ids=[r['run_id'] for r in runs], outputs=outputs,
               byte_comparison=receipt['byte_comparison'], receipt=public_receipt, reasons=reasons)
    _write(output / 'REPRODUCIBILITY.json', dict(format='deep32-reproducibility/1', release=RELEASE,
           source_revision=source_revision, source_date_epoch=epoch, subjects=[row],
           scope_note='Only the shared core media assembly is covered; other release subjects are not proven here.'))
    wording = ('The shared core was assembled in two recorded clean runs from the same sealed inputs and toolchain.\n'
               + 'The two final ISO files were byte identical.\n'
               + 'This checks media assembly on the recorded toolchain; upstream images and model training are not proven reproducible.\n'
               if status == 'proven' else 'Reproducibility is not proven for this release subject.\n'
               + '\n'.join(reasons) + '\n')
    (output / 'REPRODUCIBILITY.txt').write_text(wording, encoding='ascii', newline='\n')
    return row


def expand_reproducibility(core_doc, index, out=None):
    """Export exact frozen coverage without inventing missing runs."""
    from contract_validate import validate_contract, validate_contract_set
    core_path = ordinary(core_doc)
    core = load_json(core_path)
    index = load_json(index) if isinstance(index, (str, Path)) else index
    validate_contract('sbom-index', index)
    if core.get('format') != 'deep32-reproducibility/1' or core.get('release') != index['release']:
        fail('An internal core comparison document for this release is required.')
    _epoch_revision(core['source_date_epoch'], core['source_revision'])
    measured = {row['subject']['path']: row for row in core['subjects']}
    if len(measured) != len(core['subjects']):
        fail('Comparison subjects must be unique.')
    wanted = {row['subject']['path']: row['subject'] for row in index['subjects']}
    if not measured.keys() <= wanted.keys():
        fail('A comparison subject is absent from the release SBOM index.')
    rows = []
    for name, subject in sorted(wanted.items()):
        old = measured.get(name)
        if old is None:
            scope = ('runtime-assembly' if subject['kind'] == 'runtime' else
                     'local-container-build' if subject['kind'] == 'container' else 'media-assembly')
            rows.append(dict(subject=subject, scope=scope, status='not-proven',
                             input_manifest_sha256=None, recipe_sha256=None,
                             generator_sha256=digest(Path(__file__)), toolchain_sha256=None,
                             runs=[], byte_comparison=None, receipt=None, provenance=None,
                             reason='No two-build receipt or complete input, recipe and toolchain evidence was supplied for this subject.'))
            continue
        if any(old['subject'][key] != subject[key] for key in ('path', 'size', 'sha256')):
            fail('The core comparison differs from its frozen SBOM subject.')
        reference = old['receipt']
        receipt_path = ordinary(core_path.parent / public_path(reference['path']))
        if digest(receipt_path) != reference['sha256']:
            fail('The measured core comparison receipt checksum differs.')
        raw = load_json(receipt_path)
        if (raw.get('format') != 'deep32-alpha2-comparison-receipt/1'
                or raw.get('status') != old['status'] or raw.get('scope') != old['scope']
                or raw.get('source_revision') != core['source_revision']
                or raw.get('source_date_epoch') != core['source_date_epoch']
                or raw.get('outputs') != old['outputs']
                or raw.get('byte_comparison') != old['byte_comparison']
                or raw.get('reasons') != old['reasons']
                or raw.get('recipe_sha256') != old['recipe_sha256']
                or raw.get('generator_sha256') != old['generator_sha256']
                or raw['inputs']['sha256'] != old['input_manifest_sha256']
                or [r['run_id'] for r in raw['runs']] != old['run_ids']):
            fail('The public core row contradicts its checksum-bound comparison receipt.')
        runs = []
        representable = True
        for run in raw['runs']:
            tools = []
            for tool, version in sorted(run['tool_versions'].items()):
                if tool == 'xorriso' and version.startswith('xorriso version '):
                    version = version[len('xorriso version '):].split()[0]
                try:
                    identifier(version)
                except ValueError:
                    representable = False
                tools.append(dict(name=tool, version=version))
            if (run['architecture'] != 'amd64' or any(value != 0 for value in run['process_exit_results'].values())
                    or not all(run['verification'].values())):
                representable = False
            runs.append(dict(id=run['run_id'], clean=run['clean_build'],
                             output={key: run['output'][key] for key in ('size', 'sha256')},
                             exit_code=0,
                             environment=dict(tz=run['environment']['TZ'], locale=run['environment']['LC_ALL'],
                                              umask=run['environment']['umask'], source_date_epoch=run['environment']['SOURCE_DATE_EPOCH']),
                             platform=run['platform'], architecture=run['architecture'], tool_versions=tools,
                             clean_method=run['clean_build_method'], verification='passed'))
        if not representable:
            runs = []
        status = old['status'] if representable else 'not-proven'
        reason = None if status == 'proven' else (
            old['reasons'][0] if old['reasons'] else 'The observed runs lack representable successful verification evidence.')
        rows.append(dict(subject=subject, scope=old['scope'], status=status,
                         input_manifest_sha256=old['input_manifest_sha256'], recipe_sha256=old['recipe_sha256'],
                         generator_sha256=old['generator_sha256'], toolchain_sha256=old['toolchain_sha256'],
                         runs=runs, byte_comparison=old['byte_comparison']['equal'] if len(runs) == 2 else None,
                         receipt=reference, provenance=None, reason=reason))
    result = dict(format='deep32-reproducibility', format_version=1, release=core['release'],
                  source_commit=core['source_revision'], source_date_epoch=core['source_date_epoch'],
                  generator_sha256=digest(Path(__file__)), subjects=rows)
    validate_contract_set({'sbom-index': index, 'reproducibility': result})
    if out is not None:
        target = Path(out).absolute()
        ordinary(target.parent, directory=True)
        with target.open('x', encoding='ascii', newline='\n') as stream:
            stream.write(canonical(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    emit = commands.add_parser('emit')
    for option in ('repo', 'image', 'media', 'stage', 'out', 'inventory'):
        emit.add_argument('--' + option, required=True, type=Path)
    compare = commands.add_parser('compare-core')
    for option in ('first', 'second', 'stage', 'out'):
        compare.add_argument('--' + option, required=True, type=Path)
    compare.add_argument('--run-receipt', action='append', type=Path)
    for command in (emit, compare):
        command.add_argument('--epoch', required=True, type=int)
        command.add_argument('--source-revision', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'emit':
            value = emit_component_sbom(args.repo, args.image, args.media, args.stage, args.out,
                                       args.epoch, args.source_revision, inventory_path=args.inventory)
        else:
            value = compare_core(args.first, args.second, args.out, args.epoch, args.source_revision,
                                 args.stage, run_receipts=args.run_receipt)
        print(canonical(value), end='')
        return 0
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        # avoid echoing operator paths or nested parser input.
        detail = str(exc) if getattr(exc, 'metadata_refusal', False) else 'Metadata input is missing, unsafe or inconsistent.'
        print(detail, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
