"""Normalise explicit sealed public inputs. No discovery outside declared roots.

The versioned adapter interchange is documented in INPUTS.md. It is not an SPDX
document or a replacement for the release owner's frozen packaging contract.
"""
from pathlib import Path
import re

from .common import (canonical, digest, exact_version, identifier, keys, load_json, ordinary,
                     public_path, public_text, public_url, regular, sha256)

KINDS = {'source', 'deb', 'openbsd-package', 'openbsd-set', 'wheel', 'sdist',
         'model', 'tool', 'data', 'base-package', 'application'}
USAGES = {'runtime', 'build-only', 'bundled', 'source', 'external-model'}
SUBJECTS = {'disc', 'container', 'runtime'}


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(label + ' must be a list')
    return value


def _unique(rows, label):
    try:
        unique = set(rows)
    except TypeError as exc:
        raise ValueError('invalid ' + label) from exc
    if len(rows) != len(unique):
        raise ValueError('duplicate ' + label)


def _choice(value, choices):
    return isinstance(value, str) and value in choices


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('non-negative integer required')
    return value


def _ref(value, files):
    keys(value, {'root', 'path'})
    key = (identifier(value['root']), public_path(value['path']))
    if key not in files:
        raise ValueError('reference absent from sealed inventory')
    return dict(root=key[0], **files[key])


def _refs(value, files):
    rows = [_ref(row, files) for row in _list(value, 'file references')]
    _unique([(row['root'], row['path']) for row in rows], 'file reference')
    return sorted(rows, key=lambda row: (row['root'], row['path']))


def normalise_component(value, files):
    keys(value, {'id', 'kind', 'name', 'version', 'architecture', 'usage', 'files', 'dependencies'},
         {'licence_declared', 'licence_evidence', 'required_licence', 'required_source',
          'source_files', 'source_url', 'revision'})
    if not _choice(value['kind'], KINDS) or not _choice(value['usage'], USAGES):
        raise ValueError('unknown component kind or usage')
    result = {key: identifier(value[key]) for key in ('id', 'name', 'architecture')}
    result['version'] = exact_version(value['version'])
    result.update(kind=value['kind'], usage=value['usage'], files=_refs(value['files'], files),
                  dependencies=sorted(identifier(item) for item in _list(value['dependencies'], 'dependencies')))
    _unique(result['dependencies'], 'dependency')
    if result['id'] in result['dependencies']:
        raise ValueError('self dependency')
    result['licence_declared'] = public_text(value.get('licence_declared', 'NOASSERTION'))
    if result['licence_declared'] in {'UNKNOWN', 'unknown', 'NONE'}:
        result['licence_declared'] = 'NOASSERTION'
    result['licence_evidence'] = _refs(value.get('licence_evidence', []), files)
    result['source_files'] = _refs(value.get('source_files', []), files)
    result['source_url'] = public_url(value.get('source_url', 'NOASSERTION'))
    for flag in ('required_licence', 'required_source'):
        if not isinstance(value.get(flag, False), bool):
            raise ValueError('completeness flag must be boolean')
        result[flag] = value.get(flag, False)
    if result['required_licence'] and (result['licence_declared'] == 'NOASSERTION' or not result['licence_evidence']):
        raise ValueError('missing mandatory licence evidence: ' + result['id'])
    if result['required_source'] and not result['source_files']:
        raise ValueError('missing mandatory source evidence: ' + result['id'])
    if result['kind'] == 'model':
        revision = value.get('revision')
        if not isinstance(revision, str) or re.fullmatch('[0-9a-f]{40,64}', revision) is None:
            raise ValueError('immutable model revision required')
        if not result['files']:
            raise ValueError('full measured model snapshot inventory required')
        snapshot_roots = {row['root'] for row in result['files']}
        if len(snapshot_roots) != 1 or {(row['root'], row['path']) for row in result['files']} != {key for key in files if key[0] in snapshot_roots}:
            raise ValueError('model must enumerate its complete dedicated snapshot root')
        result['revision'] = revision
    elif 'revision' in value:
        result['revision'] = identifier(value['revision'])
    if not result['files'] and value['kind'] not in {'deb', 'openbsd-package', 'base-package'}:
        raise ValueError('measured component files required')
    return result


def _inventories(manifest, roots):
    supplied = {}
    files = {}
    for row in _list(manifest['roots'], 'roots'):
        keys(row, {'id', 'files'})
        name = identifier(row['id'])
        if name in supplied or name not in roots:
            raise ValueError('duplicate or undeclared input root')
        root = ordinary(roots[name], directory=True)
        supplied[name] = root
        expected = []
        for file in _list(row['files'], 'sealed files'):
            keys(file, {'path', 'size', 'sha256'})
            relative = public_path(file['path'])
            key = (name, relative)
            if key in files:
                raise ValueError('duplicate sealed file path')
            path = regular(root, relative)
            size, checksum = _integer(file['size']), sha256(file['sha256'])
            if path.stat().st_size != size or digest(path) != checksum:
                raise ValueError('sealed input size or SHA-256 differs: ' + relative)
            files[key] = dict(path=relative, size=size, sha256=checksum)
            expected.append(relative)
        actual = []
        # Walk only explicitly declared roots; refuse links before descending.
        def walk(folder, prefix=''):
            for path in sorted(folder.iterdir()):
                relative = prefix + path.name
                public_path(relative)
                ordinary(path, directory=path.is_dir())
                if path.is_dir():
                    walk(path, relative + '/')
                else:
                    actual.append(relative)
        walk(root)
        if sorted(actual) != sorted(expected):
            raise ValueError('sealed root has unexpected or missing files: ' + name)
    if set(supplied) != set(roots):
        raise ValueError('root mapping differs from declared roots')
    root_paths = list(supplied.values())
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for index, a in enumerate(root_paths) for b in root_paths[index + 1:]):
        raise ValueError('input roots must not overlap')
    return supplied, files


def validate_gaps(value, components, subjects, files):
    keys(value, {'format', 'gaps'})
    if value['format'] != 'deep32-sbom-gaps/1':
        raise ValueError('unknown gap manifest version')
    result = []
    for row in _list(value['gaps'], 'gaps'):
        keys(row, {'id', 'target', 'field', 'reason'}, {'approval'})
        target = identifier(row['target'])
        if target not in components and target not in subjects:
            raise ValueError('gap target is absent')
        field = row['field']
        if not _choice(field, {'licence', 'components', 'source'}):
            raise ValueError('unknown gap field')
        if field == 'components' and target not in subjects:
            raise ValueError('component coverage gap must name a subject')
        if field in {'source', 'licence'} and target not in components:
            raise ValueError('licence/source gap must name a component')
        normal = dict(id=identifier(row['id']), target=target, field=field,
                      reason=public_text(row['reason']))
        if 'approval' in row:
            normal['approval'] = _ref(row['approval'], files)
        if field == 'components' and 'approval' not in normal:
            raise ValueError('partial component inventory needs sealed lead approval')
        result.append(normal)
    _unique([row['id'] for row in result], 'gap id')
    _unique([(row['target'], row['field']) for row in result], 'gap target/field')
    targets = {(row['target'], row['field']) for row in result}
    for item in components.values():
        unknown = item['licence_declared'] == 'NOASSERTION' or not item['licence_evidence']
        if unknown != ((item['id'], 'licence') in targets):
            raise ValueError('licence evidence and gap record differ: ' + item['id'])
        if item['required_source'] and (item['id'], 'source') in targets:
            raise ValueError('mandatory source gap cannot be approved away')
    for subject in subjects.values():
        partial = subject['component_coverage'] != 'complete'
        if partial != ((subject['id'], 'components') in targets):
            raise ValueError('component coverage and gap record differ')
        licence_partial = any((item, 'licence') in targets for item in subject['components'])
        if subject['licence_coverage'] != ('partial' if licence_partial else 'complete'):
            raise ValueError('licence completeness differs from component evidence')
    return sorted(result, key=lambda row: row['id'])


def normalise_inputs(manifest_path, roots, *, contents=False):
    """Read and verify one sealed manifest; return public canonicalisable values.

    roots maps public root IDs to operator-local paths and is never serialised.
    Every inventory (including a container's sealed inventory) is hash checked
    before parsing. Input files are read only; no output directory is created.
    """
    manifest = load_json(manifest_path)
    keys(manifest, {'format', 'release', 'source_date_epoch', 'source_revision',
                    'generator_sha256', 'roots', 'subjects', 'components', 'gaps'},
         {'absent_roles', 'generated_from', 'wheelhouses'})
    if manifest['format'] != 'deep32-sbom-input/1':
        raise ValueError('unknown SBOM input version')
    release = identifier(manifest['release'])
    revision = manifest['source_revision']
    if not isinstance(revision, str) or re.fullmatch('[0-9a-f]{40,64}', revision) is None:
        raise ValueError('immutable source revision required')
    epoch = _integer(manifest['source_date_epoch'])
    generator_hash = sha256(manifest['generator_sha256'])
    paths, files = _inventories(manifest, roots)
    components = {}
    def add_component(value, shared=False):
        item = normalise_component(value, files)
        if item['id'] in components:
            if shared and components[item['id']] == item:
                return item['id']
            raise ValueError('duplicate component id')
        components[item['id']] = item
        return item['id']
    for value in _list(manifest['components'], 'components'):
        add_component(value)
    subjects = {}
    aliases = []
    subject_paths = []
    roles = []
    for value in _list(manifest['subjects'], 'subjects'):
        keys(value, {'id', 'kind', 'file', 'role', 'aliases', 'components',
                     'component_coverage', 'licence_coverage'}, {'image', 'runtime_members'})
        item = {key: identifier(value[key]) for key in ('id', 'role')}
        if item['id'] in subjects or item['id'] in components:
            raise ValueError('duplicate subject id')
        if not _choice(value['kind'], SUBJECTS) or not _choice(value['component_coverage'], {'complete', 'partial', 'opaque-set'}) or not _choice(value['licence_coverage'], {'complete', 'partial'}):
            raise ValueError('unknown subject kind or coverage')
        if contents and value['kind'] in {'disc', 'runtime'}:
            keys(value['file'], {'root', 'path'})
            file_root = identifier(value['file']['root'])
            if file_root not in paths:
                raise ValueError('subject root is absent')
            subject_file = dict(root=file_root, path=public_path(value['file']['path']))
        else:
            subject_file = _ref(value['file'], files)
        item.update(kind=value['kind'], file=subject_file,
                    aliases=sorted(identifier(alias) for alias in _list(value['aliases'], 'aliases')),
                    components=sorted(identifier(ref) for ref in _list(value['components'], 'component ids')),
                    component_coverage=value['component_coverage'], licence_coverage=value['licence_coverage'])
        if item['kind'] == 'container':
            from .archives import normalise_image
            if 'image' not in value:
                raise ValueError('missing sealed image binding and complete inventory')
            keys(value['image'], {'binding', 'inventory'})
            inventory_ref = _ref(value['image']['inventory'], files)
            inventory = load_json(regular(paths[inventory_ref['root']], inventory_ref['path']))
            measured = normalise_image(regular(paths[item['file']['root']], item['file']['path']), value['image']['binding'], inventory)
            for component in measured['components']:
                ref = add_component(component, shared=True)
                if ref not in item['components']:
                    item['components'].append(ref)
            item['components'].sort()
            item['image'] = {key: val for key, val in measured.items() if key != 'components'}
            item['image']['inventory'] = inventory_ref
        elif 'image' in value:
            raise ValueError('image inventory attached to a non-container')
        if item['kind'] == 'runtime':
            from .archives import normalise_runtime_archive
            if 'runtime_members' not in value:
                raise ValueError('missing exact runtime member inventory')
            if contents:
                from .archives import _expected_members
                members = _expected_members(value['runtime_members'])
                measured = {(row['size'], row['sha256']) for row in files.values()}
                from .spdx import _metadata
                if any('mode' not in row or (not _metadata(row['path']) and (row['size'], row['sha256']) not in measured) for row in members.values()):
                    raise ValueError('pre-archive runtime members require modes and measured source bytes')
                item['runtime'] = dict(members=[members[name] for name in sorted(members)])
            else:
                item['runtime'] = normalise_runtime_archive(regular(paths[item['file']['root']], item['file']['path']), value['runtime_members'])
        elif 'runtime_members' in value:
            raise ValueError('runtime inventory attached to another subject')
        _unique(item['components'], 'subject component')
        subjects[item['id']] = item
        aliases.extend(item['aliases'])
        roles.append(item['role'])
        subject_paths.append((item['file']['root'], item['file']['path']))
    if not subjects:
        raise ValueError('no shipped subjects')
    if set(subjects) & set(components):
        raise ValueError('subject and component identities must be disjoint')
    for item in subjects.values():
        if not item['components'] or any(ref not in components for ref in item['components']):
            raise ValueError('subject components missing or unresolved')
    _unique(subject_paths, 'shipped subject path')
    _unique([item['file']['path'] for item in subjects.values()], 'public shipped path')
    _unique(roles, 'subject role')
    _unique(aliases, 'alias')
    if set(aliases) & set(roles + list(subjects)):
        raise ValueError('alias collides with a shipped subject or role')
    absent_roles = sorted(identifier(role) for role in _list(manifest.get('absent_roles', []), 'absent roles'))
    _unique(absent_roles, 'absent role')
    if set(absent_roles) & set(roles + aliases):
        raise ValueError('absent optional role is shipped')
    for item in components.values():
        if any(ref not in components for ref in item['dependencies']):
            raise ValueError('unresolved declared dependency')
    wheelhouses = []
    selected = set()
    for row in _list(manifest.get('wheelhouses', []), 'wheelhouses'):
        from .distributions import normalise_wheelhouse
        from .common import build_distributions
        keys(row, {'root', 'path', 'lock', 'usage', 'components'})
        root = identifier(row['root'])
        folder = public_path(row['path'])
        if root not in paths:
            raise ValueError('wheelhouse root is absent')
        ordinary(paths[root] / folder, directory=True)
        lock = _ref(row['lock'], files)
        requirements = regular(paths[lock['root']], lock['path']).read_text(encoding='utf-8')
        rows = normalise_wheelhouse(paths[root] / folder, requirements, usage=row['usage'])
        ids = _list(row['components'], 'wheelhouse components')
        _unique(ids, 'selected distribution component')
        if any(not isinstance(ref, str) or ref not in components for ref in ids) or len(ids) != len(rows):
            raise ValueError('selected wheel components differ from lock closure')
        matched = set()
        for distribution in rows:
            candidates = [components[ref] for ref in ids if build_distributions.canonical_name(components[ref]['name']) == distribution['name']]
            if len(candidates) != 1:
                raise ValueError('selected wheel component name differs')
            item = candidates[0]
            if (len(item['files']) != 1 or item['version'] != distribution['version'] or item['usage'] != distribution['usage'] or item['kind'] != distribution['kind']
                    or not any(file['root'] == root and file['path'] == folder + '/' + distribution['path'] and file['sha256'] == distribution['sha256'] for file in item['files'])):
                raise ValueError('selected archive and component identity differ')
            expression = distribution.get('licence_expression')
            if expression not in (None, 'NOASSERTION') and item['licence_declared'] not in ('NOASSERTION', expression):
                raise ValueError('declared licence differs from selected wheel metadata')
            matched.add(item['id'])
        if matched & selected:
            raise ValueError('distribution selected twice')
        selected |= matched
        wheelhouses.append(dict(root=root, path=folder, lock=lock, usage=row['usage'], distributions=rows, components=sorted(ids)))
    if selected != {item['id'] for item in components.values() if item['kind'] in {'wheel', 'sdist'}}:
        raise ValueError('every wheel or sdist needs measured selected-lock evidence')
    attached = set(ref for item in subjects.values() for ref in item['components'])
    if attached != set(components):
        raise ValueError('unattached component is not shipped evidence')
    relationships = []
    for relation in _list(manifest.get('generated_from', []), 'source build provenance'):
        keys(relation, {'result', 'source', 'evidence'})
        result, source = identifier(relation['result']), identifier(relation['source'])
        if (result not in components or source not in components or result == source
                or components[result]['kind'] != 'wheel' or components[source]['kind'] != 'sdist'):
            raise ValueError('generated wheel requires distinct measured source distribution')
        relationships.append(dict(result=result, source=source, evidence=_ref(relation['evidence'], files)))
    _unique([(row['result'], row['source']) for row in relationships], 'source build relationship')
    gaps = validate_gaps(manifest['gaps'], components, subjects, files)
    return dict(format='deep32-sbom-normalised/1', release=release,
                source_date_epoch=epoch, source_revision=revision, generator_sha256=generator_hash,
                roots=[dict(id=name, files=[files[key] for key in sorted(files) if key[0] == name]) for name in sorted(paths)],
                subjects=sorted(subjects.values(), key=lambda row: row['id']),
                components=sorted(components.values(), key=lambda row: row['id']), gaps=gaps,
                absent_roles=absent_roles, generated_from=sorted(relationships, key=lambda row: (row['result'], row['source'])),
                wheelhouses=sorted(wheelhouses, key=lambda row: (row['root'], row['path'])))
