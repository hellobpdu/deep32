"""Measure external medium SBOMs from explicit offline owner evidence."""

import csv
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile

from .common import (canonical, digest, exact_version, identifier, load_json, ordinary,
                     public_path, public_text, regular, sha256)
from .workflow import contents, finalise, generator_sha256, verify
from .spdx import _metadata


RELEASE = 'v0.1.0-alpha.3'
VERSION = '0.1.0-alpha.3'
SEAL = 'OWNER-MEDIUM-SEAL.json'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _path(value, *, find=False):
    if find and isinstance(value, str) and value.startswith('./'):
        value = value[2:]
    value = public_path(value)
    _require(not any(re.match(r'(?:golden|smoke|priv-)', part, re.I) or
                     part.endswith(('.corrected.md', '.reviewed.md', '.questions.json'))
                     for part in value.split('/')), 'An excluded medium path was supplied.')
    return value


def _row(path, name):
    path = ordinary(path)
    return dict(path=name, size=path.stat().st_size, sha256=digest(path))


def _integer(value):
    _require(type(value) is int and value >= 0, 'A nonnegative measured size is required.')
    return value


def _tree(root):
    root = ordinary(root, directory=True)
    result = {}
    def walk(folder):
        for child in sorted(folder.iterdir()):
            name = _path(child.relative_to(root).as_posix())
            ordinary(child, directory=child.is_dir())
            if child.is_dir():
                walk(child)
            else:
                _require(name.casefold() not in {key.casefold() for key in result},
                         'Medium paths must be unique without regard to case.')
                result[name] = _row(child, name)
    walk(root)
    return result


def _hash_line(line):
    match = re.fullmatch(r'SHA256 \((.+)\) = ([0-9a-fA-F]{64})', line)
    if match:
        return _path(match[1], find=True), match[2].lower()
    match = re.fullmatch(r'([0-9a-fA-F]{64})[ \t]+\*?(.+)', line)
    if match:
        return _path(match[2], find=True), match[1].lower()
    return None


def _listing(path):
    path = ordinary(path)
    _require(path.stat().st_size <= 16 * 1024 * 1024, 'The medium listing exceeds its size limit.')
    with path.open('rb') as stream:
        beginning = stream.read(128).lstrip()
    if beginning[:1] in (b'{', b'['):
        value = load_json(path)
        _require(type(value) is dict and type(value.get('files')) is list,
                 'A measured medium listing requires a files array.')
        rows = value['files']
        _require(all(type(row) is dict and set(row) == {'path', 'size', 'sha256'} for row in rows),
                 'Each measured listing row requires exactly path, size, and sha256.')
        result = [dict(path=_path(row['path']), size=_integer(row['size']), sha256=sha256(row['sha256']))
                  for row in rows]
        image = value.get('image')
        if image is not None:
            _require(type(image) is dict and set(image) == {'size', 'sha256'},
                     'The listing image binding requires exactly size and sha256.')
            image = dict(size=_integer(image['size']), sha256=sha256(image['sha256']))
    else:
        result, image = [], None
        for line in path.read_text(encoding='ascii').splitlines():
            if not line or line.startswith('#'):
                continue
            hashed = _hash_line(line)
            result.append(dict(path=hashed[0], sha256=hashed[1]) if hashed else dict(path=_path(line, find=True)))
    names = [row['path'] for row in result]
    _require(names and len(names) == len(set(names)), 'The medium listing is empty or contains duplicate paths.')
    return result, image


def _checksum_rows(path):
    path = ordinary(path)
    rows, signature_preamble = [], False
    for line in path.read_text(encoding='ascii').splitlines():
        if not line or line.startswith('#'):
            continue
        if line.startswith('untrusted comment:'):
            signature_preamble = True
            continue
        if signature_preamble and re.fullmatch(r'[A-Za-z0-9+/=]+', line):
            signature_preamble = False
            continue
        item = _hash_line(line)
        _require(item is not None, 'A checksum file contains an unsupported row.')
        rows.append(item)
    _require(rows and len(rows) == len({name for name, _ in rows}),
             'A checksum file is empty or repeats a path.')
    return rows


def _check_checksums(paths, medium, medium_root, image):
    matched, package_matched = set(), set()
    for supplied in paths:
        supplied = ordinary(supplied)
        packages = supplied.name == 'MEDIUM-PACKAGES.sha256'
        for name, expected in _checksum_rows(supplied):
            if packages:
                target = 'packages/amd64/' + name
                targets = [target] if target in medium else []
            else:
                targets = [name] if name in medium else [key for key in medium if key.rsplit('/', 1)[-1] == name]
            _require(len(targets) <= 1, 'A checksum basename is ambiguous on the medium.')
            if targets:
                target = targets[0]
                _require(medium[target]['sha256'] == expected, 'A listed medium checksum differs from measured bytes.')
                matched.add(target)
                if packages:
                    package_matched.add(target)
            elif packages:
                _require(False, 'A package checksum names a file absent from the medium listing.')
            if name == image.name:
                _require(digest(image) == expected, 'The image checksum differs from measured bytes.')
            accessible = supplied.parent.joinpath(*name.split('/'))
            if accessible.exists():
                _require(digest(ordinary(accessible)) == expected,
                         'An accessible checksum input differs from its listed hash.')
    return matched, package_matched


def _disc2_rows(manifest, medium, root):
    value = load_json(manifest)
    _require(type(value) is dict and value.get('format') == 'deep32-content-manifest' and
             value.get('format_version') == 1 and type(value.get('members')) is list,
             'Disc 2 requires the version 1 content manifest with a members array.')
    result, names = [], set()
    for row in value['members']:
        _require(type(row) is dict and set(row) == {'path', 'size', 'sha256'},
                 'Each content member requires exactly path, size, and sha256.')
        name = _path(row['path'])
        _require(name not in names and name not in {'MANIFEST.json', 'MANIFEST.json.asc'},
                 'The content members contain a duplicate or reserved manifest path.')
        names.add(name)
        result.append(dict(path=name, size=_integer(row['size']), sha256=sha256(row['sha256'])))
    if 'counts' in value:
        _require(value['counts'] == dict(files=len(result), bytes=sum(row['size'] for row in result)),
                 'Content manifest counts differ from its members.')
    _require('MANIFEST.json' in medium and digest(manifest) == medium['MANIFEST.json']['sha256'],
             'The supplied content manifest differs from the medium manifest.')
    _require('MANIFEST.json.asc' in medium, 'Disc 2 requires its existing detached content manifest signature.')
    extra = set(medium) - names
    _require(extra <= {'MANIFEST.json', 'MANIFEST.json.asc'},
             'Disc 2 contains a regular file absent from its content manifest.')
    result.extend(medium[name] for name in sorted(extra))
    return result, value


def _identity(disc, path, manifest):
    if disc == 'disc1':
        filename = path.rsplit('/', 1)[-1]
        if path.startswith('packages/') and filename.endswith('.tgz'):
            match = re.fullmatch(r'(.+)-([0-9][A-Za-z0-9_.+~:!-]*)\.tgz', filename)
            _require(match is not None, 'An OpenBSD package filename lacks an exact name and version.')
            return 'openbsd-package', identifier(match[1]), exact_version(match[2])
        if re.fullmatch(r'(?:base|comp|game|man|xbase|xfont|xserv|xshare)80\.tgz', filename):
            return 'openbsd-set', None, '8.0'
        if filename.startswith('site') or path.startswith(('d32/', 'deep32/', 'packs/')):
            return 'application', None, VERSION
        return 'tool', None, '8.0'
    version = '0.1.0-alpha.1'
    kb = manifest.get('kb')
    if isinstance(kb, dict) and isinstance(kb.get('path'), str) and path.startswith(kb['path'] + '/'):
        version = kb.get('generation') or version
    for entry in manifest.get('library', []):
        if path in [row['path'] for row in entry.get('parts', [])]:
            version = entry.get('generation') or version
    for block in manifest.get('blocks', []):
        if path.startswith('corpus/' + block['name'] + '/'):
            version = block.get('generation') or block.get('version') or version
    cisco = manifest.get('cisco_public')
    if isinstance(cisco, dict) and path == cisco.get('path'):
        version = cisco.get('generation') or cisco.get('catalogue_generation') or version
    return ('source' if path.startswith('code/') else 'data'), None, exact_version(version)


def _component_id(disc, path):
    readable = re.sub(r'[^A-Za-z0-9_.+-]', '-', path)[:85].rstrip('-')
    return identifier(disc + '-' + readable + '-' + hashlib.sha256(path.encode('ascii')).hexdigest()[:24])


def _licences(paths, medium, root, manifest):
    result = {}
    packs = {entry['pack']: [part['path'] for part in entry.get('parts', [])]
             for entry in manifest.get('library', [])}
    for source in paths:
        source = ordinary(source)
        evidence = [name for name, row in medium.items()
                    if name.rsplit('/', 1)[-1] == source.name and row['sha256'] == digest(source)]
        _require(len(evidence) == 1, 'License evidence must match exactly one file on the medium.')
        # Only explicit tabular declarations are recognized; narrative notices never imply a license.
        with source.open('r', encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream, delimiter='\t')
            fields = reader.fieldnames or []
            target_key = next((key for key in ('path', 'file', 'medium_path', 'pack') if key in fields), None)
            license_key = next((key for key in ('license_expression', 'licence_expression', 'license', 'licence') if key in fields), None)
            if target_key is None or license_key is None:
                continue
            for row in reader:
                value = row.get(license_key, '').strip()
                if not value or value in {'NOASSERTION', 'UNKNOWN', 'NONE'}:
                    continue
                value = public_text(value)
                target = row.get(target_key, '').strip()
                targets = packs.get(target, []) if target_key == 'pack' else [_path(target)]
                _require(bool(targets) and all(name in medium for name in targets),
                         'An explicit license row names an absent medium file or library pack.')
                for name in targets:
                    item = dict(licence_declared=value, licence_evidence=[dict(root='medium', path=evidence[0])])
                    _require(name not in result or result[name] == item,
                             'Explicit medium license declarations conflict.')
                    result[name] = item
    return result


def _put(path, value):
    path.write_text(canonical(value), encoding='ascii', newline='\n')


def _link(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def disc_sbom(disc, image, out, *, epoch, source_revision, medium_list=None,
              medium_root=None, manifest=None, checksums=(), credits=None, notices=()):
    """Seal measured medium files and publish verified external SBOM metadata.

    The owner supplies a complete build or extraction tree; this function never mounts an image.
    Returned paths are relative to the new output directory and are suitable for assembly composition.
    """
    _require(disc in {'disc1', 'disc2'}, 'The medium must be disc1 or disc2.')
    image = ordinary(image)
    _path(image.name)
    from alpha2_assemble import image_name
    image_public_name = image_name(disc, image.suffix)
    _integer(epoch)
    _require(isinstance(source_revision, str) and re.fullmatch(r'[0-9a-f]{40,64}', source_revision) is not None,
             'An immutable source revision is required.')
    _require(medium_root is not None, 'A complete medium root is required for measured file evidence.')
    root = ordinary(medium_root, directory=True)
    out = Path(out).absolute()
    ordinary(out.parent, directory=True)
    inputs = [image, root, *checksums, *notices]
    inputs += [item for item in (medium_list, manifest, credits) if item is not None]
    _require(not out.exists() and not out.is_symlink(), 'The output directory must be new.')
    for supplied in inputs:
        supplied = Path(supplied).absolute()
        _require(not (out == supplied or out.is_relative_to(supplied) or supplied.is_relative_to(out)),
                 'The output and input paths must not overlap.')
    medium = _tree(root)
    _require(medium and SEAL not in medium, 'The medium is empty or uses the reserved seal filename.')
    _require(not any(_metadata(name) for name in medium),
             'A medium file is excluded by the unchanged emitter metadata rules and cannot be represented exactly.')
    image_row = _row(image, image_public_name)
    manifest_value = {}
    evidence = []
    if disc == 'disc1':
        _require(medium_list is not None and manifest is None, 'Disc 1 requires an explicit medium listing.')
        listed, binding = _listing(medium_list)
        if binding is not None:
            _require(binding == {key: image_row[key] for key in ('size', 'sha256')},
                     'The medium listing is bound to different image bytes.')
        _require(any(Path(path).name == 'MEDIUM-PACKAGES.sha256' for path in checksums),
                 'Disc 1 requires its build MEDIUM-PACKAGES.sha256 checksum evidence.')
        evidence.append(('medium-list', ordinary(medium_list)))
    else:
        _require(manifest is not None and medium_list is None, 'Disc 2 requires its signed content manifest.')
        listed, manifest_value = _disc2_rows(ordinary(manifest), medium, root)
        evidence.append(('content-manifest', ordinary(manifest)))
    _require({row['path'] for row in listed} == set(medium),
             'The medium listing has missing or unexpected regular files.')
    for row in listed:
        _require(all(medium[row['path']][key] == row[key] for key in ('size', 'sha256') if key in row),
                 'A listed medium size or SHA-256 differs from measured bytes.')
    matched, package_matched = _check_checksums(checksums, medium, root, image)
    if disc == 'disc1':
        packages = {name for name in medium if name.startswith('packages/amd64/')}
        _require(packages <= package_matched, 'The medium package checksum evidence is incomplete.')
        sets = {name for name in medium if _identity(disc, name, {})[0] == 'openbsd-set'}
        _require(sets <= matched, 'An OpenBSD set lacks explicit set checksum evidence.')
    for index, path in enumerate(checksums):
        evidence.append(('checksums-' + str(index), ordinary(path)))
    if credits is None:
        candidates = [name for name in medium if name.rsplit('/', 1)[-1] == 'LIBRARY-CREDITS.tsv']
        _require(len(candidates) <= 1, 'The medium contains ambiguous library credit files.')
        credits = regular(root, candidates[0]) if candidates else None
    if not notices:
        notices = [regular(root, name) for name in medium
                   if name.rsplit('/', 1)[-1] in {'THIRD-PARTY-NOTICES.txt', 'THIRD-PARTY-LICENSES.txt'}]
    license_paths = ([credits] if credits is not None else []) + list(notices)
    for index, path in enumerate(license_paths):
        evidence.append(('licenses-' + str(index), ordinary(path)))
    licences = _licences(license_paths, medium, root, manifest_value)
    components, gaps = [], []
    for name in sorted(medium):
        component_id = _component_id(disc, name)
        kind, package_name, version = _identity(disc, name, manifest_value)
        row = dict(id=component_id, kind=kind, name=package_name or component_id, version=version,
                   architecture='amd64' if disc == 'disc1' else 'all',
                   usage='source' if kind == 'source' else 'runtime',
                   files=[dict(root='medium', path=name)], dependencies=[], source_url='NOASSERTION')
        row.update(licences.get(name, dict(licence_declared='NOASSERTION', licence_evidence=[])))
        if row['licence_declared'] == 'NOASSERTION':
            gaps.append(dict(id='license-' + component_id, target=component_id, field='licence',
                             reason='No explicit license declaration is recorded for this medium file.'))
        components.append(row)
    # The contract bounds medium-level components (4096 since alpha.2: disc 2 carries 1249 files, most of them the
    # prebuilt index's shards); the bound stays, it is only sized for a real disc.
    _require(len(components) <= 4096, 'The complete medium inventory exceeds the frozen component limit.')
    temporary = Path(tempfile.mkdtemp(prefix='.owner-media-', dir=out.parent))
    try:
        media = temporary / 'medium'
        subject_root = temporary / 'subject'
        media.mkdir()
        subject_root.mkdir()
        for name in sorted(medium):
            _link(regular(root, name), media.joinpath(*name.split('/')))
        _link(image, subject_root / image_public_name)
        (subject_root / 'IMAGE.sha256').write_text(image_row['sha256'] + '  ' + image_public_name + '\n', encoding='ascii')
        receipt = dict(format='deep32-owner-medium-evidence/1', disc=disc,
                       image=image_row, files=[medium[name] for name in sorted(medium)],
                       evidence=[dict(id=label, size=path.stat().st_size, sha256=digest(path))
                                 for label, path in evidence])
        _put(media / SEAL, receipt)
        _put(subject_root / 'OWNER-IMAGE-SEAL.json', dict(format='deep32-owner-image-evidence/1', image=image_row))
        roots = dict(medium=media, subject=subject_root)
        measured = dict(format='deep32-sbom-input/1', release=RELEASE, source_revision=source_revision,
                        source_date_epoch=epoch, generator_sha256=generator_sha256(),
                        roots=[dict(id=key, files=list(_tree(folder).values())) for key, folder in roots.items()],
                        subjects=[dict(id=disc, kind='disc', role='workstation' if disc == 'disc1' else 'content',
                                       file=dict(root='subject', path=image_public_name), aliases=[],
                                       components=[row['id'] for row in components], component_coverage='complete',
                                       licence_coverage='partial' if gaps else 'complete')],
                        components=components, gaps=dict(format='deep32-sbom-gaps/1', gaps=gaps))
        references = [dict(path='medium/' + SEAL, sha256=digest(media / SEAL)),
                      dict(path='subject/OWNER-IMAGE-SEAL.json', sha256=digest(subject_root / 'OWNER-IMAGE-SEAL.json'))]
        purposes = {'source': 'SOURCE', 'application': 'APPLICATION', 'tool': 'APPLICATION',
                    'openbsd-package': 'OPERATING-SYSTEM', 'openbsd-set': 'OPERATING-SYSTEM', 'data': 'FILE'}
        frozen = dict(format='deep32-sbom-input', format_version=1, release=RELEASE,
                      source_commit=source_revision, source_date_epoch=epoch, generator_sha256=measured['generator_sha256'],
                      input_roots=[dict(id=key, path=key, manifest=reference)
                                   for key, reference in zip(roots, references)],
                      subjects=[dict(kind='disc', path=image_public_name, size=image_row['size'], sha256=image_row['sha256'],
                                     aliases=[], input_manifests=references, components=[row['id'] for row in components],
                                     contents_sboms=[])],
                      components=[dict(id=row['id'], name=row['name'], version=row['version'], purpose=purposes[row['kind']],
                                       scope=row['usage'], sha256=medium[row['files'][0]['path']]['sha256'],
                                       source_url='NOASSERTION', licence=row['licence_declared'], dependencies=[], base_inventory=None,
                                       licence_evidence=[dict(path='medium/' + ref['path'], sha256=medium[ref['path']]['sha256'])
                                                         for ref in row['licence_evidence']]) for row in components],
                      exceptions=[], excluded_metadata=['SHA256SUMS'])
        _put(temporary / 'MEASURED.json', measured)
        _put(temporary / 'FROZEN.json', frozen)
        contents(temporary / 'MEASURED.json', roots, temporary / 'contents')
        finalise(temporary / 'MEASURED.json', roots, temporary / 'contents', temporary / 'metadata',
                 contract_path=temporary / 'FROZEN.json')
        verified = verify(temporary / 'MEASURED.json', roots, temporary / 'metadata',
                          contract_path=temporary / 'FROZEN.json')
        _require(_tree(root) == medium and _row(image, image_public_name) == image_row,
                 'An input changed while the medium SBOM was generated.')
        index = load_json(temporary / 'metadata' / 'SBOM-INDEX.json')
        subject = index['subjects'][0]
        metadata = [dict(file=row['path'], source='metadata/' + row['path'], discs=[disc])
                    for row in [*subject['contents'], *([subject['gap']] if subject['gap'] else [])]]
        result = dict(format='deep32-owner-disc-sbom/1', disc=disc, components=len(components),
                      medium_files=len(medium), component_coverage='complete',
                      license_gaps=len(gaps), image=dict(id=disc, source='subject/' + image_public_name,
                                                       sums='subject/IMAGE.sha256', sbom='metadata/' + subject['sbom']['asset']),
                      sbom_indexes=['metadata/SBOM-INDEX.json'], metadata=metadata,
                      measured='MEASURED.json', contract='FROZEN.json', roots={'medium': 'medium', 'subject': 'subject'},
                      contents='contents', sboms='metadata', verified_files=len(verified['files']))
        _put(temporary / 'ASSEMBLY.json', result)
        _require(not out.exists(), 'The output directory must be new.')
        os.rename(temporary, out)
        return result
    finally:
        if temporary.exists():
            _require(temporary.parent == out.parent and temporary.name.startswith('.owner-media-'),
                     'The temporary output escaped its parent.')
            shutil.rmtree(temporary)
