"""Offline contents, frozen-subject wrappers and exact release coverage.

These checks implement a selected SPDX profile, not official schema validation.
No signature, producer completeness or reproducibility claim is made here.
"""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from .common import canonical, digest, ordinary
from .inputs import normalise_inputs
from .spdx import emit_document, validate_document


def _bytes(value):
    return canonical(value).encode('ascii')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _put(assets, name, data):
    if name in assets:
        raise ValueError('duplicate generated metadata asset name')
    assets[name] = data


def generator_sha256():
    """Bind the actual emitter/adapters and reused pure build implementations."""
    root = Path(__file__).resolve().parents[1]
    paths = [root / 'sbom.py', *sorted((root / 'sbom').glob('*.py'))]
    paths += [root / 'contract_validate.py']
    paths += [root / 'contracts' / 'schemas' / (name + '.schema.json')
              for name in ('sbom-input', 'sbom-index', 'sbom-gap')]
    build = root.parent / 'gpu-pack' / 'build'
    paths += [build / name for name in ('provenance.py', 'distributions.py', 'image_binding.py')]
    rows = [dict(path=p.relative_to(root.parent).as_posix(), sha256=digest(ordinary(p)))
            for p in paths]
    return _hash(_bytes(sorted(rows, key=lambda row: row['path'])))


def _input(manifest, roots, *, contents=False):
    result = normalise_inputs(manifest, roots, contents=contents)
    if result['generator_sha256'] != generator_sha256():
        raise ValueError('generator source SHA-256 differs; seal the actual generator-hash')
    return result


def _contents(normal):
    assets = {}
    names = []
    for subject in normal['subjects']:
        if subject['kind'] == 'container':
            names.append('image-' + subject['id'] + '.spdx.json')
        else:
            names.extend([subject['id'] + '.contents.spdx.json', subject['id'] + '.spdx.json'])
    if len(names) != len({name.casefold() for name in names}):
        raise ValueError('duplicate generated metadata asset name')
    for subject in normal['subjects']:
        image = subject['kind'] == 'container'
        name = ('image-' + subject['id'] + '.spdx.json' if image
                else subject['id'] + '.contents.spdx.json')
        document = emit_document(normal, subject, kind='final' if image else 'contents')
        validate_document(document)
        _put(assets, name, _bytes(document))
    return assets


def _existing(folder, expected):
    folder = ordinary(folder, directory=True)
    result = {}
    paths = sorted(folder.iterdir())
    if {path.name for path in paths} != set(expected):
        raise ValueError('metadata inventory has missing or unexpected files')
    for path in paths:
        ordinary(path)
        if path.stat().st_size != len(expected[path.name]):
            raise ValueError('metadata size differs from canonical inputs')
        result[path.name] = path.read_bytes()
    return result


def _same(expected, actual):
    if expected.keys() != actual.keys():
        raise ValueError('metadata inventory has missing or unexpected files')
    if any(expected[name] != actual[name] for name in expected):
        raise ValueError('metadata bytes differ from measured canonical inputs')


def _destination(out, roots, extra=()):
    out = Path(out).absolute()
    ordinary(out.parent, directory=True)
    if out.exists() or out.is_symlink():
        raise ValueError('output directory must be new')
    for source in [*roots.values(), *extra]:
        source = Path(source).absolute()
        if out == source or out.is_relative_to(source) or source.is_relative_to(out):
            raise ValueError('output and input directories must not overlap')
    return out


def _write(out, assets):
    # Temporary output is on the output filesystem, inside its authorised parent.
    temporary = Path(tempfile.mkdtemp(prefix='.sbom-', dir=out.parent)).absolute()
    try:
        for name, data in sorted(assets.items()):
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('flat public metadata asset required')
            with (temporary / name).open('xb') as stream:
                stream.write(data)
        if out.exists():
            raise ValueError('output directory must be new')
        os.rename(temporary, out)
    finally:
        if temporary.exists():
            if temporary.parent != out.parent or not temporary.name.startswith('.sbom-'):
                raise ValueError('temporary output escaped its parent')
            shutil.rmtree(temporary)


def _row(name, data):
    return dict(path=name, size=len(data), sha256=_hash(data))


def contents(manifest_path, roots, out):
    out = _destination(out, roots)
    normal = _input(manifest_path, roots, contents=True)
    assets = _contents(normal)
    _write(out, assets)
    return dict(format='deep32-sbom-contents/1', files=[_row(n, d) for n, d in sorted(assets.items())])


def _release(normal, content_assets, contract=None):
    # Regenerate contents without final disc/runtime identities before comparison.
    expected = _contents(normal)
    _same(expected, content_assets)
    assets = dict(content_assets)
    assets['GAPS.json'] = _bytes(dict(format='deep32-sbom-gaps/1', gaps=normal['gaps']))
    assets['PROVENANCE.json'] = _bytes(normal)
    index = []
    import json
    for subject in normal['subjects']:
        image = subject['kind'] == 'container'
        content_name = ('image-' + subject['id'] + '.spdx.json' if image
                        else subject['id'] + '.contents.spdx.json')
        name = content_name if image else subject['id'] + '.spdx.json'
        if subject['kind'] == 'runtime' and contract is None:
            embedded = [row for row in subject['runtime']['members']
                        if row['path'] == 'deep32/kit/sbom/forge-docker.contents.spdx.json']
            if (len(embedded) != 1 or embedded[0]['size'] != len(content_assets[content_name])
                    or embedded[0]['sha256'] != _hash(content_assets[content_name])):
                raise ValueError('runtime embedded contents SBOM is missing or differs from external contents')
        if not image:
            child = dict(subject=subject, document=json.loads(content_assets[content_name]),
                         sha256=_hash(content_assets[content_name]))
            document = emit_document(normal, subject, child_documents=[child])
            validate_document(document, {child['document']['documentNamespace']: child['document']})
            _put(assets, name, _bytes(document))
        index.append(dict(id=subject['id'], kind=subject['kind'], role=subject['role'],
                          aliases=subject['aliases'], artefact={key: subject['file'][key] for key in ('path', 'size', 'sha256')},
                          sbom=_row(name, assets[name]), contents=_row(content_name, assets[content_name]),
                          component_coverage=subject['component_coverage'], licence_coverage=subject['licence_coverage'],
                          gaps=_row('GAPS.json', assets['GAPS.json']),
                          provenance=_row('PROVENANCE.json', assets['PROVENANCE.json'])))
    assets['SBOM-INDEX.json'] = _bytes(dict(format='deep32-sbom-index/1', release=normal['release'],
                                         source_revision=normal['source_revision'], source_date_epoch=normal['source_date_epoch'],
                                         generator_sha256=normal['generator_sha256'], subjects=index,
                                         absent_roles=normal['absent_roles'],
                                         validation='selected-profile; official SPDX schema not supplied'))
    if contract is not None:
        from .frozen import release_index
        import json
        release_index(contract, normal, json.loads(assets['SBOM-INDEX.json']), assets, _bytes, _hash)
    assets['METADATA.json'] = _bytes(dict(format='deep32-sbom-metadata/1',
                                        files=[_row(n, d) for n, d in sorted(assets.items())]))
    return assets


def finalise(manifest_path, roots, contents_dir, out, *, contract_path=None):
    out = _destination(out, roots, [contents_dir])
    normal = _input(manifest_path, roots)
    expected = _contents(normal)
    from .frozen import bind
    contract = bind(contract_path, normal) if contract_path is not None else None
    assets = _release(normal, _existing(contents_dir, expected), contract)
    _write(out, assets)
    return dict(format='deep32-sbom-finalised/1', subjects=len(normal['subjects']),
                files=[_row(n, d) for n, d in sorted(assets.items())])


def verify(manifest_path, roots, sboms, *, contract_path=None):
    normal = _input(manifest_path, roots)
    from .frozen import bind
    contract = bind(contract_path, normal) if contract_path is not None else None
    expected = _release(normal, _contents(normal), contract)
    actual = _existing(sboms, expected)
    _same(expected, actual)
    return dict(format='deep32-sbom-verified/1', subjects=len(normal['subjects']),
                files=[_row(n, d) for n, d in sorted(expected.items())],
                validation='selected-profile; signatures must be verified by the release owner')
