#!/usr/bin/env python3
"""Assemble the exact alpha.2 image set without signing or copying images.

The owner manifest has exactly these fields, with paths relative
to its location; absolute local source paths are also accepted and never emitted.
Merge the producer's DISC3-IMAGES.json fields with the two external
image rows, explicit supporting metadata, and owner-supplied public descriptors.

    {"format":"deep32-alpha2-assembly-input", "format_version":1,
     "images":[{"id":"disc1","source":"disc1.img","sums":"disc1.sha256",
                "sbom":"disc1.spdx.json"}],
     "disc3_image_dir":"images", "capability_manifest":"CAPABILITIES.json",
     "mirrors_template":"MIRRORS-template.json", "sbom_indexes":["SBOM-INDEX.json"],
     "metadata":[{"file":"disc1.contents.spdx.json",
                  "source":"disc1.contents.spdx.json","discs":["disc1"]}],
     "reproducibility":"reproducibility/REPRODUCIBILITY.json"}

The abbreviated image list above must contain all eleven IDs;
the indexes must include all external SBOM and contents bindings, supplied by
the explicit metadata rows or each image's SBOM path.
A null reproducibility path omits a release-wide proof statement.
Input version 1 remains the legacy runtime-only assembly. Input
version 2 also requires source_build_catalog and source_build_carrier_dir, stages
all verified companion carriers, and includes their catalog in the signed lists.
"""

import argparse
import contextlib
import copy
import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract_validate as contracts
import capabilities
import forge_requirements
import release_assemble as whole
import source_build_catalog
from sbom.spdx import validate_document


CLASSES = ('turing', 'ampere80', 'ampere86', 'ada', 'hopper', 'blackwell100', 'blackwell120')
BUNDLES = ('core', *('embedder-' + value for value in CLASSES), 'ingestion')
IMAGE_IDS = ('disc1', 'disc2', *BUNDLES)
VERSION = '0.1.0-alpha.2'
RELEASE = 'v' + VERSION


class AssemblyError(ValueError):
    """A release input failed an explicit assembly check."""


def require(condition, message):
    if not condition:
        raise AssemblyError(message)


def _path(base, value, *, directory=False):
    require(type(value) is str and bool(value), 'An explicit input path is required.')
    return whole.plain_path(base / value, directory=directory)


def _read(path, limit=5 * 1024 * 1024):
    path = whole.plain_path(path)
    before = path.stat()
    require(0 < before.st_size <= limit, 'A metadata input exceeds its byte limit.')
    with path.open('rb') as stream:
        require(whole._identity(before) == whole._identity(os.fstat(stream.fileno())),
                'A metadata input changed before reading.')
        data = stream.read(limit + 1)
        require(len(data) == before.st_size and
                whole._identity(before) == whole._identity(os.fstat(stream.fileno())) and
                whole._identity(before) == whole._identity(whole.plain_path(path).stat()),
                'A metadata input changed during reading.')
    return data


def _json(path, limit=5 * 1024 * 1024):
    return contracts.strict_loads(_read(path, limit), max_bytes=limit)


def image_name(image_id, suffix='.iso'):
    require(image_id in IMAGE_IDS, 'An unknown image ID is refused.')
    if image_id == 'disc1':
        require(suffix.lower() in ('.img', '.iso'), 'Disc 1 requires an IMG or ISO image.')
        return 'deep32-' + VERSION + '-disc1-workstation-amd64' + suffix.lower()
    if image_id == 'disc2':
        return 'deep32-' + VERSION + '-disc2-content.iso'
    return 'deep32-' + VERSION + '-disc3-gpu-pack-' + image_id + '.iso'


def _inputs(manifest_path):
    manifest_path = whole.plain_path(manifest_path)
    manifest = _json(manifest_path)
    fields = {'format', 'format_version', 'images', 'disc3_image_dir', 'capability_manifest',
              'mirrors_template', 'sbom_indexes', 'metadata', 'reproducibility'}
    require(type(manifest) is dict and manifest.get('format') == 'deep32-alpha2-assembly-input'
            and type(manifest.get('format_version')) is int and manifest['format_version'] in (1, 2),
            'The alpha.2 assembly manifest has an unsupported shape or version.')
    if manifest['format_version'] == 2:
        fields.update(('source_build_catalog', 'source_build_carrier_dir'))
    require(set(manifest) == fields, 'The alpha.2 assembly manifest has missing or extra fields.')
    require(type(manifest['images']) is list and len(manifest['images']) == len(IMAGE_IDS),
            'The release requires exactly eleven image rows.')
    base, images = manifest_path.parent, {}
    for row in manifest['images']:
        require(type(row) is dict and set(row) == {'id', 'source', 'sums', 'sbom'} and
                type(row['id']) is str and row['id'] in IMAGE_IDS and row['id'] not in images,
                'Image IDs must be unique and match the eleven required images.')
        key = row['id']
        source, sums = _path(base, row['source']), _path(base, row['sums'])
        role = key if key in ('disc1', 'disc2') else 'disc3'
        kind = {'disc1': 'workstation', 'disc2': 'content'}.get(key, 'gpu-pack-' + key)
        image = whole.input_disc(source, sums, role, kind, VERSION, 'amd64', 512 * 1024**3)
        image['id'] = key
        image['sbom'] = _path(base, row['sbom'])
        image['measurement'] = whole.measure_whole(source)
        require(image['file'] == image_name(key, source.suffix), 'An image has an invalid filename.')
        images[key] = image
    require(set(images) == set(IMAGE_IDS), 'A required image is missing.')
    identities = [(r['source'].stat().st_dev, r['source'].stat().st_ino) for r in images.values()]
    require(len(set(identities)) == len(identities), 'Image aliases and hard-link aliases are refused.')
    require(len({r['sha256'] for r in images.values()}) == len(images),
            'Different image IDs cannot reuse the same image bytes.')
    folder = _path(base, manifest['disc3_image_dir'], directory=True)
    expected = set()
    for key in BUNDLES:
        image = images[key]
        require(image['source'].parent == folder, 'Every disc 3 image must be in the explicit image directory.')
        expected.add(image['source'].name)
        if image['source_sums'].parent == folder:
            expected.add(image['source_sums'].name)
    actual = set()
    for path in folder.iterdir():
        whole.plain_path(path)
        actual.add(path.name)
    require(actual == expected, 'The disc 3 image directory contains missing or extra files.')
    return manifest, base, images


def prepare(manifest_path):
    """Validate explicit inputs and return frozen metadata and measured image bindings."""
    manifest, base, images = _inputs(manifest_path)
    capraw = _read(_path(base, manifest['capability_manifest']), 8 * 1024 * 1024)
    capability = contracts.strict_loads(capraw, max_bytes=8 * 1024 * 1024)
    contracts.validate_contract('capability-manifest', capability)
    require({row['id'] for row in capability['bundles'] if row['kind'] != 'corresponding-source-build'} == set(BUNDLES),
            'The capability manifest must describe exactly the nine requested bundles.')
    source_paths = ()
    if manifest['format_version'] == 2:
        source_raw = _read(_path(base, manifest['source_build_catalog']), source_build_catalog.MAX_BYTES)
        source_paths = source_build_catalog.verify(
            source_raw, capraw, _path(base, manifest['source_build_carrier_dir'], directory=True))
    mirrors = _json(_path(base, manifest['mirrors_template']))
    require(type(mirrors) is dict and mirrors.get('format_version') == 2 and
            type(mirrors.get('discs')) is list and type(mirrors.get('bundles')) is list,
            'The mirror template must use the whole-image version 2 contract.')
    contracts.validate_contract('mirrors', mirrors)
    require(len(mirrors['discs']) == 2 and
            {r.get('role') for r in mirrors['discs']} == {'disc1', 'disc2'} and
            len(mirrors['bundles']) == len(BUNDLES) and
            {r.get('bundle_id') for r in mirrors['bundles']} == set(BUNDLES),
            'The mirror template must describe exactly the eleven requested images.')
    mirrors = copy.deepcopy(mirrors)
    bundles = {row['id']: row for row in capability['bundles']}
    for row in contracts.mirror_transports(mirrors):
        key = row.get('role', row.get('bundle_id'))
        image = images[key]
        require(row.get('file') == image['file'], 'A mirror template image name differs from its producer.')
        row.update(image['measurement'])
        row['chunk_bytes'] = 67108864
        if key in BUNDLES:
            row['bundle_sha256'] = hashlib.sha256(contracts.canonical(bundles[key])).hexdigest()
    capname = mirrors['capability_manifest']['file']
    contracts.safe_path(capname, basename=True)
    require(capname.casefold() != source_build_catalog.ASSET.casefold(),
            'The source/build catalog name is reserved.')
    mirrors['capability_manifest']['sha256'] = hashlib.sha256(capraw).hexdigest()
    contracts.validate_contract_set({'mirrors': mirrors, 'capability-manifest': capability})
    assets, memberships = {}, {}

    def asset(name, data, roles=()):
        contracts.safe_path(name, basename=True)
        require(type(roles) in (list, tuple) and all(r in ('disc1', 'disc2', 'disc3') for r in roles)
                and len(set(roles)) == len(roles), 'A metadata child membership is invalid.')
        reserved = {'mirrors.json', 'sha256sums', 'assets.txt', 'assembly.json', 'asset-map.json',
                    'sbom-index.json', *(n.casefold() for n in contracts.mirror_child_names(mirrors).values())}
        require(name.casefold() not in reserved and
                not name.lower().endswith(('.iso', '.img', '.sig', '.asc', '.tar', '.parts.tsv')) and
                not any(n.casefold() == name.casefold() for n in assets),
                'A metadata asset has a duplicate, reserved, or image filename.')
        require(0 < len(data) <= (8 if name == capname else 5) * 1024 * 1024,
                'A metadata asset exceeds its byte limit.')
        assets[name], memberships[name] = data, list(roles)

    asset(capname, capraw)
    if source_paths:
        asset(source_build_catalog.ASSET, source_raw, ['disc3'])
    storage_binding = capability.get('storage_evidence', {})
    storage_digest = None
    require(type(manifest['metadata']) is list, 'An explicit metadata list is required.')
    for row in manifest['metadata']:
        require(type(row) is dict and set(row) == {'file', 'source', 'discs'},
                'A metadata row requires exactly file, source, and discs.')
        name = contracts.safe_path(row['file'], basename=True)
        require(name.casefold() != source_build_catalog.ASSET.casefold(),
                'Source/build catalog must be supplied through verified assembly v2 inputs.')
        is_storage = name.casefold() == capabilities.STORAGE_ASSET.casefold()
        limit = capabilities.STORAGE_LIMIT if is_storage else 5 * 1024 * 1024
        data = _read(_path(base, row['source']), limit)
        asset(row['file'], data, row['discs'])
        if is_storage:
            require(row['file'] == capabilities.STORAGE_ASSET and row['discs'] == ['disc3'],
                    'Physical storage measurements require the canonical Disc 3 metadata asset name.')
            storage_digest = hashlib.sha256(data).hexdigest()
        if row['file'].casefold() == forge_requirements.ASSET.casefold():
            require(row['file'] == forge_requirements.ASSET and row['discs'] == ['disc3'],
                    'Forge prerequisites must use the canonical Disc 3 metadata asset name.')
            forge_requirements.validate(data, capability_sha256=mirrors['capability_manifest']['sha256'],
                                        pins_sha256=capability['pins_sha256'], release=capability['release'],
                                        storage_evidence_sha256=storage_binding.get('sha256'))
    # A declared budget states its ISO length; compare it with the image being published.
    capabilities.verify_detached_storage(
        capability, assets.get(capabilities.STORAGE_ASSET), storage_digest,
        observed={key: {'iso_bytes': images[key]['size']} for key in BUNDLES})
    require(type(manifest['sbom_indexes']) is list and bool(manifest['sbom_indexes']),
            'One or more frozen public SBOM indexes are required.')
    subjects = []
    for source in manifest['sbom_indexes']:
        index = _json(_path(base, source))
        contracts.validate_contract('sbom-index', index)
        require(index['release'] == RELEASE, 'An SBOM index belongs to another release.')
        subjects.extend(copy.deepcopy(index['subjects']))
    index = dict(format='deep32-sbom-index', format_version=1, release=RELEASE,
                 subjects=sorted(subjects, key=lambda r: r['subject']['path']))
    contracts.validate_contract('sbom-index', index)
    image_by_file = {r['file']: r for r in images.values()}
    discs = {r['subject']['path']: r for r in subjects if r['subject']['kind'] == 'disc'}
    require(set(discs) == set(image_by_file), 'SBOM indexes have missing or extra disc images.')
    for name, image in image_by_file.items():
        row = discs[name]
        require(not row['subject']['aliases'] and not row['sbom']['media_paths'],
                'Image aliases and embedded final-image SBOMs are refused.')
        require(all(row['subject'][k] == image[k] for k in ('size', 'sha256')),
                'An SBOM index subject differs from its measured producer image.')
        data = _read(image['sbom'])
        require(hashlib.sha256(data).hexdigest() == row['sbom']['sha256'],
                'An image SBOM does not match its index digest.')
        asset(row['sbom']['asset'], data, [image['role']])
    # Validate all actual documents, including external relationship closure.
    documents = {name: contracts.strict_loads(data) for name, data in assets.items()
                 if name.endswith('.spdx.json')}
    require(all(type(doc) is dict and type(doc.get('documentNamespace')) is str
                for doc in documents.values()), 'An SPDX document header is missing or malformed.')
    externals = {doc['documentNamespace']: doc for doc in documents.values()}
    require(len(externals) == len(documents), 'Duplicate SPDX document namespaces are refused.')
    for document in documents.values():
        validate_document(document, externals)
    for row in subjects:
        require(not row['sbom']['media_paths'], 'SBOM documents must remain external to every release image.')
    for name, row in discs.items():
        doc = documents.get(row['sbom']['asset'])
        require(doc is not None, 'Each image needs an SPDX 2.3 document.')
        root = next(p for p in doc['packages'] if p['SPDXID'] == doc['documentDescribes'][0])
        require(root['name'] == name and root.get('checksums') == [
            dict(algorithm='SHA256', checksumValue=row['subject']['sha256'])],
            'An SPDX root package does not bind its final image.')
    for row in subjects:
        references = [dict(path=row['sbom']['asset'], sha256=row['sbom']['sha256']), *row['contents']]
        if row['gap'] is not None:
            references.append(row['gap'])
        for reference in references:
            require(reference['path'] in assets and
                    hashlib.sha256(assets[reference['path']]).hexdigest() == reference['sha256'],
                    'An SBOM index references absent or changed external metadata.')
    assets['SBOM-INDEX.json'] = contracts.canonical(index)
    memberships['SBOM-INDEX.json'] = ['disc1', 'disc2', 'disc3']
    if manifest['reproducibility'] is not None:
        repro_path = _path(base, manifest['reproducibility'])
        data = _read(repro_path)
        repro = contracts.strict_loads(data)
        if isinstance(repro, dict) and repro.get('format') == 'deep32-reproducibility/1':
            helper = capabilities._module(Path(__file__).resolve().parents[1] / 'gpu-pack/build/alpha2_metadata.py',
                                          '_alpha2_assembly_reproducibility')
            repro = helper.expand_reproducibility(repro_path, index, None)
            data = contracts.canonical(repro)
        contracts.validate_contract_set({'sbom-index': index, 'reproducibility': repro})
        for row in repro['subjects']:
            for key in ('receipt', 'provenance'):
                ref = row[key]
                if ref is not None:
                    require(ref['path'] in assets and hashlib.sha256(assets[ref['path']]).hexdigest() == ref['sha256'],
                            'A reproducibility statement references absent or changed metadata.')
        asset('REPRODUCIBILITY.json', data, ['disc1', 'disc2', 'disc3'])
    for role, child in contracts.mirror_child_names(mirrors).items():
        name = child.removesuffix('-SHA256SUMS') + '-README.md'
        text = 'This unsigned inventory requires release-owner review and signing.\n\n'
        for image in sorted(images.values(), key=lambda r: r['file']):
            if image['role'] == role:
                text += '%s\t%d\t%s\n' % (image['file'], image['size'], image['sha256'])
        asset(name, text.encode('ascii'), [role])
    asset_map = dict(format='deep32-asset-map', format_version=1, release=RELEASE,
                     entries=[dict(asset=name, media_paths=[], install=[]) for name in sorted(assets)])
    contracts.validate_contract_set({'asset-map': asset_map, 'mirrors': mirrors, 'sbom-index': index})
    assets['ASSET-MAP.json'] = contracts.canonical(asset_map)
    memberships['ASSET-MAP.json'] = []
    return dict(images=images, assets=assets, memberships=memberships, mirrors=mirrors,
                capability=capability, source_paths=source_paths)


def validate_media(capability, component_media):
    """Bind native capability member paths to the producer's measured media trees.

    This verifies detached media trees and saved archive identities;
    it does not traverse ISO filesystems or prove that the ISO contains those trees.
    """
    if not isinstance(capability, dict):
        capability = _json(whole.plain_path(capability), 8 * 1024 * 1024)
    contracts.validate_contract('capability-manifest', capability)
    require(type(component_media) is dict and set(component_media) == set(BUNDLES),
            'Media roots must cover exactly the nine runtime components.')
    bundles = {row['id']: row for row in capability['bundles']
               if row['kind'] != 'corresponding-source-build'}
    require(set(bundles) == set(BUNDLES), 'Runtime capabilities differ from the producer component set.')
    binding = capabilities._module(Path(__file__).resolve().parents[1] / 'gpu-pack/build/image_binding.py',
                                   '_alpha2_media_image_binding')
    counts = {}
    generated = {'SHA256SUMS', 'pins.json', 'deep32/PAYLOAD.json',
                 'deep32/PAYLOAD.SHA256', 'deep32/RUNTIME.json',
                 'deep32/payload/images/index.json'}
    for key in BUNDLES:
        media = whole.plain_path(component_media[key], directory=True)
        actual = {}
        for path in media.rglob('*'):
            name = path.relative_to(media).as_posix()
            contracts.safe_path(name)
            whole.plain_path(path, directory=path.is_dir())
            if path.is_file():
                before = path.stat()
                actual[name] = dict(size=before.st_size, sha256=whole.sha256(path))
                require(whole._identity(before) == whole._identity(path.stat()),
                        'A media member changed while hashing.')
        require(generated <= actual.keys(), 'A generated media binding is missing.')
        require(actual['pins.json']['sha256'] == capability['pins_sha256'],
                'The media pins differ from the capability binding.')
        sums = {}
        for line in _read(media / 'SHA256SUMS').decode('ascii').splitlines():
            digest, separator, name = line.partition('  ')
            contracts.safe_path(name)
            require(separator and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
                    and name not in sums, 'The media checksum list is malformed or duplicated.')
            sums[name] = digest
        require(sums == {name: row['sha256'] for name, row in actual.items() if name != 'SHA256SUMS'},
                'The media checksum list does not cover the exact media bytes.')
        mapped, image_paths = set(), {}
        for member in bundles[key]['members']:
            name = member['path']
            candidates = {candidate for candidate in (name, 'deep32/payload/' + name, 'deep32/' + name)
                          if candidate in actual}
            require(len(candidates) == 1, 'A capability member is missing or has ambiguous media aliases.')
            candidate = candidates.pop()
            require(candidate not in mapped and actual[candidate] ==
                    {k: member[k] for k in ('size', 'sha256')},
                    'A capability member differs from the measured media bytes.')
            mapped.add(candidate)
            image_paths[name] = media / candidate
        require(set(actual) <= mapped | generated,
                'The media contains an undeclared capability member.')
        selected = _json(media / 'deep32/payload/images/index.json')
        require(type(selected) is list and len(selected) == len(bundles[key]['images']),
                'A selected media image index has missing or extra images.')
        indexed = {r['role']: r for r in selected}
        for image in bundles[key]['images']:
            require(image['role'] in indexed and all(indexed[image['role']].get(k) == image[k]
                    for k in ('path', 'image_id', 'config_digest', 'index_digest', 'manifest_digest',
                              'archive_format', 'source_digest')),
                    'A capability archive identity differs from the selected media image index.')
            require(indexed[image['role']].get('sha256') == whole.sha256(image_paths[image['path']]),
                    'A selected media image index has an incorrect archive checksum.')
            binding.check_archive(image_paths[image['path']], image, image['source_digest'])
        counts[key] = len(mapped)
    return dict(format='deep32-alpha2-capability-media-verified', format_version=1, members=counts)


validate_matches_producer = validate_media


def assemble(manifest_path, output):
    """Create a new unsigned whole-image release using merged release tooling."""
    output = Path(os.path.abspath(output))
    whole.plain_path(output.parent, directory=True)
    require(not os.path.lexists(output), 'The release output directory already exists.')
    prepared = prepare(manifest_path)
    images = prepared['images']
    with tempfile.TemporaryDirectory(prefix='.alpha2-input-', dir=output.parent) as temporary:
        staging = Path(temporary)
        source_paths = prepared['source_paths']
        release_output = staging / 'release' if source_paths else output
        rows = []
        for name, data in sorted(prepared['assets'].items()):
            (staging / name).write_bytes(data)
            rows.append(dict(file=name, source=name, discs=prepared['memberships'][name]))
        (staging / 'mirrors-input.json').write_bytes(contracts.canonical(prepared['mirrors']))
        (staging / 'metadata-input.json').write_bytes(contracts.canonical(dict(
            format='deep32-release-metadata-list', format_version=1, assets=rows)))
        (staging / 'bundles-input.json').write_bytes(contracts.canonical(dict(
            format='deep32-release-bundle-inputs', format_version=1,
            bundles=[dict(bundle_id=key, source=str(images[key]['source']),
                          sums=str(images[key]['source_sums'])) for key in BUNDLES])))
        argv = ['--format', 'whole', '--version', VERSION, '--output', str(release_output),
                '--mirrors-input', str(staging / 'mirrors-input.json'),
                '--metadata-list', str(staging / 'metadata-input.json'),
                '--bundle-inputs', str(staging / 'bundles-input.json')]
        for key in ('disc1', 'disc2'):
            argv.extend(['--' + key, str(images[key]['source']),
                         '--' + key + '-sums', str(images[key]['source_sums'])])
        # Existing mechanical output is replaced by the marked CLI result below.
        with contextlib.redirect_stdout(io.StringIO()):
            whole.assemble(whole.make_parser().parse_args(argv))
        if source_paths:
            carriers = release_output / source_build_catalog.DIRECTORY
            carriers.mkdir()
            for path in source_paths:
                shutil.copyfile(whole.plain_path(path), carriers / path.name, follow_symlinks=False)
            capname = prepared['mirrors']['capability_manifest']['file']
            source_build_catalog.verify(prepared['assets'][source_build_catalog.ASSET],
                                        prepared['assets'][capname], carriers)
            require(not os.path.lexists(output), 'The release output directory already exists.')
            os.rename(release_output, output)
    return dict(format='deep32-alpha2-assembled', format_version=1,
                images=len(images), unsigned=True, pending_signatures=4)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        result = assemble(args.manifest, args.output)
        print('Assembled %d whole images; four owner signatures remain pending.' % result['images'])
        return 0
    except (ValueError, OSError, whole.Refusal) as exc:
        detail = 'An input is missing or unreadable.' if isinstance(exc, OSError) else str(exc)
        if not isinstance(exc, AssemblyError):
            detail = 'Assembly refused: ' + detail
        print(detail, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
