"""Deterministic SPDX 2.3 selected profile, without schema/network access.

Inputs are the public, measured result of ``inputs.normalise_inputs``.  This
module neither scans files nor claims official-schema validation.  Canonical
serialisation is provided by ``common.canonical``.
"""
import copy
import datetime as dt
import hashlib
import json
import re
import uuid

from .common import canonical, exact_version, identifier, keys, public_path, public_text, public_url, sha256

VERSION = '1'
NAMESPACE = uuid.UUID('cd5d8aa9-ddf5-5b12-b925-c1ecc68555a0')
PURPOSES = {'APPLICATION', 'FRAMEWORK', 'LIBRARY', 'CONTAINER', 'OPERATING-SYSTEM',
            'DEVICE', 'FIRMWARE', 'SOURCE', 'ARCHIVE', 'FILE', 'INSTALL', 'OTHER'}
RELATIONSHIPS = {'DESCRIBES', 'DESCRIBED_BY', 'CONTAINS', 'DEPENDS_ON',
                 'BUILD_DEPENDENCY_OF', 'GENERATED_FROM'}
_ID = re.compile(r'SPDXRef-[A-Za-z0-9.-]+\Z')
_DOCREF = re.compile(r'DocumentRef-[A-Za-z0-9.-]+\Z')
_SCOPE = ('Inventory excludes the subject archive itself and generated SBOM/checksum '
          'metadata (SHA256SUMS*, PAYLOAD.json, SBOM-INDEX.json, REPRODUCIBILITY.json '
          'and *.spdx.json). Component and licence completeness are separate; '
          'filesAnalyzed is false. This is selected-profile validation only.')


def _hash(value):
    return hashlib.sha256(canonical(value).encode('ascii')).hexdigest()


def stable_id(kind, identity):
    """Return an SPDX identifier using a complete canonical SHA-256 identity."""
    if not isinstance(kind, str) or re.fullmatch('[A-Za-z0-9.-]+', kind) is None:
        raise ValueError('invalid stable identifier kind')
    # Refuse non-finite values rather than letting json.dumps accept NaN.
    json.dumps(identity, allow_nan=False)
    return 'SPDXRef-' + kind + '-' + _hash(identity)


def _sequence(value, label):
    if not isinstance(value, (list, tuple)):
        raise ValueError(label + ' must be a sequence')
    return value


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('non-negative integer required')
    return value


def _file(row):
    keys(row, {'path', 'size', 'sha256'}, {'root', 'mode'})
    result = dict(path=public_path(row['path']), size=_integer(row['size']),
                  sha256=sha256(row['sha256']))
    if 'root' in row:
        result['root'] = identifier(row['root'])
    if 'mode' in row:
        result['mode'] = _integer(row['mode'])
    return result


def _file_key(row):
    return (row.get('root'), row['path'])


def _metadata(path):
    name = path.rsplit('/', 1)[-1]
    return (name.startswith('SHA256SUMS') or name in
            {'PAYLOAD.json', 'SBOM-INDEX.json', 'REPRODUCIBILITY.json'}
            or name.endswith('.spdx.json'))


def _licence(value):
    """Check expression syntax; the official licence catalogue is not bundled."""
    if value in ('UNKNOWN', 'unknown', 'NONE', 'NOASSERTION', None):
        return 'NOASSERTION'
    public_text(value)
    tokens = re.findall(r'\(|\)|[A-Za-z0-9.+-]+', value)
    if ''.join(tokens) != re.sub(r'\s+', '', value) or not tokens:
        raise ValueError('unsupported licence expression syntax')
    index = 0

    def atom():
        nonlocal index
        if index >= len(tokens):
            raise ValueError('incomplete licence expression')
        token = tokens[index]
        index += 1
        if token == '(':
            expression()
            if index >= len(tokens) or tokens[index] != ')':
                raise ValueError('unbalanced licence expression')
            index += 1
        elif token in {'AND', 'OR', 'WITH', ')', 'NONE', 'NOASSERTION'}:
            raise ValueError('invalid licence expression')
        elif token.startswith(('LicenseRef-', 'DocumentRef-')):
            raise ValueError('authorised extracted licence text required for LicenseRef')
        if index < len(tokens) and tokens[index] == 'WITH':
            index += 1
            if index >= len(tokens) or tokens[index] in {'AND', 'OR', 'WITH', '(', ')'}:
                raise ValueError('licence exception required')
            index += 1

    def expression():
        nonlocal index
        atom()
        while index < len(tokens) and tokens[index] in {'AND', 'OR'}:
            index += 1
            atom()

    expression()
    if index != len(tokens):
        raise ValueError('invalid licence expression')
    return value


def _package(spdx_id, name, version, purpose, licence='NOASSERTION', url='NOASSERTION'):
    return dict(SPDXID=spdx_id, name=public_text(name), versionInfo=exact_version(version),
                primaryPackagePurpose=purpose, downloadLocation=public_url(url),
                filesAnalyzed=False, licenseDeclared=_licence(licence),
                licenseConcluded='NOASSERTION', copyrightText='NOASSERTION',
                supplier='NOASSERTION')


def emit_document(normalised, subject, *, kind='final', child_documents=()):
    """Emit a public document; child rows are {subject, document, sha256}.

    Same-subject contents documents receive DESCRIBED_BY.  A distinct child
    receives CONTAINS only if its exact archive is a selected component file.
    ``validate_document`` takes namespace/DocumentRef -> full-document mappings.
    """
    if kind not in {'contents', 'final'}:
        raise ValueError('unknown SPDX document kind')
    if normalised.get('format') != 'deep32-sbom-normalised/1':
        raise ValueError('normalised SBOM inputs required')
    release = identifier(normalised['release'])
    revision = normalised['source_revision']
    if not isinstance(revision, str) or re.fullmatch('[0-9a-f]{40,64}', revision) is None:
        raise ValueError('immutable source revision required')
    generator = sha256(normalised['generator_sha256'])
    epoch = _integer(normalised['source_date_epoch'])
    try:
        created = (dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
                   + dt.timedelta(seconds=epoch)).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (OverflowError, ValueError) as exc:
        raise ValueError('epoch is outside the supported UTC range') from exc
    if not any(row == subject for row in normalised['subjects']):
        raise ValueError('subject is absent from normalised inputs')
    if kind == 'contents':
        keys(subject['file'], {'root', 'path'}, {'size', 'sha256'})
        root_file = dict(root=identifier(subject['file']['root']),
                         path=public_path(subject['file']['path']))
    else:
        root_file = _file(subject['file'])
    subject_identity = {key: copy.deepcopy(subject[key]) for key in
                        ('id', 'role', 'kind', 'component_coverage', 'licence_coverage')}
    subject_identity['aliases'] = sorted(subject['aliases'])
    # Public subject paths identify packaging, including before media exists.
    # Contents root_file has no final byte size/hash or operator-local location.
    subject_identity['file'] = root_file
    components = {row['id']: row for row in normalised['components']}
    if len(components) != len(normalised['components']):
        raise ValueError('duplicate component identity')
    selected = set(subject['components'])
    if not selected or not selected <= components.keys():
        raise ValueError('unresolved subject components')
    # Follow only sealed, explicit dependencies.  External or build-only
    # dependencies are described without asserting they are contained bytes.
    pending = list(selected)
    generation_sources = {}
    for row in normalised.get('generated_from', []):
        generation_sources.setdefault(row['result'], []).append(row['source'])
    while pending:
        ref = pending.pop()
        for dependency in components[ref]['dependencies'] + generation_sources.get(ref, []):
            if dependency not in components:
                raise ValueError('unresolved dependency')
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    rows = []
    for ref in sorted(selected):
        row = copy.deepcopy(components[ref])
        for field in ('files', 'source_files', 'licence_evidence'):
            files = [_file(file) for file in row.get(field, [])]
            if any(_file_key(file) == _file_key(root_file) for file in files):
                raise ValueError('component inventory may not hash its own subject')
            row[field] = sorted((file for file in files if not _metadata(file['path'])),
                                key=lambda file: (file.get('root', ''), file['path']))
        row['dependencies'] = sorted(row['dependencies'])
        rows.append(row)
    generated = sorted((copy.deepcopy(row) for row in normalised.get('generated_from', [])
                        if row['result'] in selected), key=lambda row: (row['result'], row['source']))
    root_id = stable_id('subject', subject_identity)
    packages = [_package(root_id, subject['file']['path'], release,
                         'CONTAINER' if subject['kind'] == 'container' else 'ARCHIVE')]
    if kind == 'final':
        packages[0]['checksums'] = [dict(algorithm='SHA256', checksumValue=root_file['sha256'])]
    packages[0]['comment'] = json.dumps(dict(subject=subject['id'], role=subject['role'],
        aliases=sorted(subject['aliases']), component_coverage=subject['component_coverage'],
        licence_coverage=subject['licence_coverage'], kind=kind), sort_keys=True, separators=(',', ':'))
    if kind == 'final' and 'image' in subject:
        # A config identity remains provenance; it is never the archive checksum.
        packages[0]['sourceInfo'] = json.dumps(dict(image=subject['image']), sort_keys=True, separators=(',', ':'))
    package_ids = {}
    relationships = {('SPDXRef-DOCUMENT', 'DESCRIBES', root_id)}
    files = {}
    file_names = {}
    seen_ids = {root_id: canonical(subject_identity)}

    def relation(a, relationship, b):
        if a == b:
            raise ValueError('self relationship')
        relationships.add((a, relationship, b))

    def add_file(file, owner=None, relationship='CONTAINS'):
        spdx_id = stable_id('file', file)
        identity = canonical(file)
        if spdx_id in seen_ids and seen_ids[spdx_id] != identity:
            raise ValueError('stable identifier collision')
        seen_ids[spdx_id] = identity
        name = './' + '/'.join(filter(None, (file.get('root'), file['path'])))
        if name in file_names and file_names[name] != spdx_id:
            raise ValueError('ambiguous duplicate SPDX file name')
        file_names[name] = spdx_id
        if spdx_id not in files:
            # Prefixing the declared public root keeps source inventory names
            # unambiguous; no operator-local filesystem location is retained.
            files[spdx_id] = dict(SPDXID=spdx_id, fileName=name,
                checksums=[dict(algorithm='SHA256', checksumValue=file['sha256'])],
                licenseConcluded='NOASSERTION', licenseInfoInFiles=['NOASSERTION'],
                copyrightText='NOASSERTION', comment='Measured size: ' + str(file['size']) + ' bytes.')
        if owner is not None:
            relation(owner, relationship, spdx_id)
        return spdx_id

    purposes = {'source': 'SOURCE', 'sdist': 'SOURCE', 'model': 'FILE', 'data': 'FILE',
                'openbsd-set': 'ARCHIVE', 'application': 'APPLICATION', 'tool': 'APPLICATION'}
    for row in rows:
        component_id = stable_id('package', row)
        identity = canonical(row)
        if component_id in seen_ids and seen_ids[component_id] != identity:
            raise ValueError('stable identifier collision')
        seen_ids[component_id] = identity
        package_ids[row['id']] = component_id
        package = _package(component_id, row['name'], row['version'],
            purposes.get(row['kind'], 'LIBRARY'), row.get('licence_declared', 'NOASSERTION'),
            row.get('source_url', 'NOASSERTION'))
        package['comment'] = json.dumps(dict(component=row['id'], kind=row['kind'],
            architecture=row['architecture'], usage=row['usage']), sort_keys=True, separators=(',', ':'))
        packages.append(package)
        if row['id'] in subject['components']:
            if row['usage'] == 'build-only':
                relation(component_id, 'BUILD_DEPENDENCY_OF', root_id)
            elif not (row['usage'] == 'external-model' and subject['kind'] == 'container'):
                relation(root_id, 'CONTAINS', component_id)
        # Only files explicitly belonging to the component are contained.
        # Source and licence evidence may be sealed beside the shipping bytes.
        for file in row['files']:
            add_file(file, component_id)
        for file in row['source_files']:
            add_file(file, component_id, 'GENERATED_FROM')
        evidence = sorted(add_file(file) for file in row['licence_evidence'])
        if evidence:
            package['sourceInfo'] = json.dumps(dict(licence_evidence=evidence), sort_keys=True, separators=(',', ':'))
    for row in rows:
        for dependency in row['dependencies']:
            if components[dependency]['usage'] == 'build-only':
                relation(package_ids[dependency], 'BUILD_DEPENDENCY_OF', package_ids[row['id']])
            else:
                relation(package_ids[row['id']], 'DEPENDS_ON', package_ids[dependency])
    for row in generated:
        if row['source'] not in package_ids:
            raise ValueError('generated source must be an explicit selected dependency')
        relation(package_ids[row['result']], 'GENERATED_FROM', package_ids[row['source']])
    runtime_members = []
    if 'runtime' in subject:
        runtime_members = sorted((_file(file) for file in subject['runtime']['members']
                                  if not _metadata(file['path'])), key=lambda file: file['path'])
        for file in runtime_members:
            add_file(file, root_id)
    external_refs = []
    external_documents = {}
    child_identity = []
    selected_files = {_file_key(file) for row in rows for file in row['files']}
    for child in _sequence(child_documents, 'child documents'):
        keys(child, {'subject', 'document', 'sha256'})
        document = child['document']
        checksum = sha256(child['sha256'])
        if _hash(document) != checksum:
            raise ValueError('external SPDX document SHA-256 differs')
        namespace = document['documentNamespace']
        docref = 'DocumentRef-' + _hash(dict(namespace=namespace, sha256=checksum))
        if namespace in external_documents:
            raise ValueError('duplicate external SPDX document')
        external_documents[namespace] = document
        external_refs.append(dict(externalDocumentId=docref, spdxDocument=namespace,
            checksum=dict(algorithm='SHA256', checksumValue=checksum)))
        child_identity.append(dict(namespace=namespace, sha256=checksum))
        target = docref + ':' + document['documentDescribes'][0]
        child_subject = child['subject']
        if child_subject['id'] == subject['id']:
            child_root = next((row for row in document['packages']
                               if row['SPDXID'] == document['documentDescribes'][0]), None)
            if kind != 'final' or child_root is None or child_root.get('checksums'):
                raise ValueError('same-subject child must be a contents document')
            relation(root_id, 'DESCRIBED_BY', target)
        elif _file_key(child_subject['file']) in selected_files:
            relation(root_id, 'CONTAINS', target)
    identity = dict(release=release, source_revision=revision, generator_sha256=generator,
        epoch=epoch, kind=kind, subject=subject_identity, components=rows,
        generated_from=generated, runtime_members=runtime_members,
        children=sorted(child_identity, key=lambda row: row['namespace']))
    if kind == 'final' and 'image' in subject:
        # Every serialised image provenance value participates in the namespace,
        # including sealed inventory evidence that can change independently of
        # the saved archive or its normalised package closure.
        identity['image_provenance'] = copy.deepcopy(subject['image'])
    document = dict(spdxVersion='SPDX-2.3', dataLicense='CC0-1.0', SPDXID='SPDXRef-DOCUMENT',
        name=release + '-' + subject['id'] + '-' + kind,
        documentNamespace='urn:uuid:' + str(uuid.uuid5(NAMESPACE, _hash(identity))),
        creationInfo=dict(creators=['Tool: deep32-sbom-' + VERSION], created=created),
        documentDescribes=[root_id], packages=sorted(packages, key=lambda row: row['SPDXID']),
        files=sorted(files.values(), key=lambda row: row['SPDXID']),
        relationships=[dict(spdxElementId=a, relationshipType=relationship, relatedSpdxElement=b)
                       for a, relationship, b in sorted(relationships)], comment=_SCOPE)
    if external_refs:
        document['externalDocumentRefs'] = sorted(external_refs, key=lambda row: row['externalDocumentId'])
    validate_document(document, external_documents)
    return document


def validate_document(document, externals=None):
    """Validate the emitted structural/relationship profile; raise ValueError.

    ``externals`` maps each referenced namespace (or DocumentRef ID) to its full
    document.  Canonical ASCII/LF bytes must match the recorded SHA-256.  This is
    deliberately not the official SPDX JSON schema or licence-list validator.
    """
    keys(document, {'spdxVersion', 'dataLicense', 'SPDXID', 'name', 'documentNamespace',
                    'creationInfo', 'documentDescribes', 'packages', 'files', 'relationships', 'comment'},
         {'externalDocumentRefs'})
    if (document['spdxVersion'] != 'SPDX-2.3' or document['dataLicense'] != 'CC0-1.0'
            or document['SPDXID'] != 'SPDXRef-DOCUMENT'):
        raise ValueError('unsupported SPDX document header')
    public_text(document['name'])
    try:
        namespace = document['documentNamespace']
        if not namespace.startswith('urn:uuid:') or str(uuid.UUID(namespace[9:])) != namespace[9:]:
            raise ValueError('invalid deterministic SPDX namespace')
    except (AttributeError, TypeError) as exc:
        raise ValueError('invalid deterministic SPDX namespace') from exc
    keys(document['creationInfo'], {'creators', 'created'})
    if document['creationInfo']['creators'] != ['Tool: deep32-sbom-' + VERSION]:
        raise ValueError('unexpected creator identity')
    try:
        dt.datetime.strptime(document['creationInfo']['created'], '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid epoch-derived UTC creation time') from exc
    ids = {'SPDXRef-DOCUMENT'}
    package_ids = set()

    def add_id(value):
        if not isinstance(value, str) or _ID.fullmatch(value) is None or value in ids:
            raise ValueError('invalid or duplicate SPDX identifier')
        ids.add(value)

    def checksums(rows):
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError('one measured SHA-256 required')
        keys(rows[0], {'algorithm', 'checksumValue'})
        if rows[0]['algorithm'] != 'SHA256':
            raise ValueError('SHA256 checksum required')
        sha256(rows[0]['checksumValue'])

    for package in _sequence(document['packages'], 'packages'):
        keys(package, {'SPDXID', 'name', 'versionInfo', 'primaryPackagePurpose', 'downloadLocation',
                       'filesAnalyzed', 'licenseDeclared', 'licenseConcluded', 'copyrightText', 'supplier'},
             {'checksums', 'comment', 'sourceInfo'})
        add_id(package['SPDXID'])
        package_ids.add(package['SPDXID'])
        public_text(package['name'])
        exact_version(package['versionInfo'])
        public_url(package['downloadLocation'])
        if (package['filesAnalyzed'] is not False or package['primaryPackagePurpose'] not in PURPOSES
                or package['licenseConcluded'] != 'NOASSERTION' or package['copyrightText'] != 'NOASSERTION'
                or package['supplier'] != 'NOASSERTION'):
            raise ValueError('unsupported package analysis or identity assertion')
        if _licence(package['licenseDeclared']) != package['licenseDeclared']:
            raise ValueError('unknown licence must use NOASSERTION')
        if 'checksums' in package:
            checksums(package['checksums'])
    file_names = set()
    for file in _sequence(document['files'], 'files'):
        keys(file, {'SPDXID', 'fileName', 'checksums', 'licenseConcluded', 'licenseInfoInFiles', 'copyrightText', 'comment'})
        add_id(file['SPDXID'])
        if not isinstance(file['fileName'], str) or not file['fileName'].startswith('./'):
            raise ValueError('public relative SPDX file name required')
        public_path(file['fileName'][2:])
        if file['fileName'] in file_names:
            raise ValueError('ambiguous duplicate SPDX file name')
        file_names.add(file['fileName'])
        checksums(file['checksums'])
        if (file['licenseConcluded'] != 'NOASSERTION' or file['licenseInfoInFiles'] != ['NOASSERTION']
                or file['copyrightText'] != 'NOASSERTION'):
            raise ValueError('unsupported file licence assertion')
    describes = document['documentDescribes']
    if not isinstance(describes, list) or len(describes) != 1 or describes[0] not in package_ids:
        raise ValueError('one local root package must be described')
    refs = {}
    for ref in _sequence(document.get('externalDocumentRefs', []), 'external documents'):
        keys(ref, {'externalDocumentId', 'spdxDocument', 'checksum'})
        name = ref['externalDocumentId']
        if not isinstance(name, str) or _DOCREF.fullmatch(name) is None or name in refs:
            raise ValueError('invalid or duplicate external document reference')
        checksums([ref['checksum']])
        external = (externals or {}).get(ref['spdxDocument'], (externals or {}).get(name))
        if not isinstance(external, dict) or external.get('documentNamespace') != ref['spdxDocument']:
            raise ValueError('unresolved external SPDX document')
        if _hash(external) != ref['checksum']['checksumValue']:
            raise ValueError('external SPDX document SHA-256 differs')
        external_ids = {'SPDXRef-DOCUMENT'}
        for field in ('packages', 'files'):
            for row in external.get(field, []):
                value = row.get('SPDXID')
                if not isinstance(value, str) or _ID.fullmatch(value) is None or value in external_ids:
                    raise ValueError('invalid external SPDX identifiers')
                external_ids.add(value)
        refs[name] = external_ids

    def resolve(value):
        if not isinstance(value, str):
            raise ValueError('invalid relationship endpoint')
        if ':' in value:
            name, target = value.split(':', 1)
            if name not in refs or target not in refs[name]:
                raise ValueError('unresolved external SPDX relationship')
        elif value not in ids:
            raise ValueError('unresolved local SPDX relationship')

    seen = set()
    for row in _sequence(document['relationships'], 'relationships'):
        keys(row, {'spdxElementId', 'relationshipType', 'relatedSpdxElement'})
        a, relationship, b = row['spdxElementId'], row['relationshipType'], row['relatedSpdxElement']
        if not isinstance(relationship, str) or relationship not in RELATIONSHIPS:
            raise ValueError('unsupported relationship type')
        resolve(a)
        resolve(b)
        if a == b or (a, relationship, b) in seen:
            raise ValueError('self or duplicate SPDX relationship')
        seen.add((a, relationship, b))
    if ('SPDXRef-DOCUMENT', 'DESCRIBES', describes[0]) not in seen:
        raise ValueError('missing equivalent DESCRIBES relationship')
    # CONTAINS cannot be cyclic even if all endpoints resolve.
    edges = {}
    for a, relationship, b in seen:
        if relationship == 'CONTAINS':
            edges.setdefault(a, []).append(b)
    visited, active = set(), set()

    def visit(node):
        if node in active:
            raise ValueError('cyclic containment relationship')
        if node not in visited:
            active.add(node)
            for child in edges.get(node, []):
                visit(child)
            active.remove(node)
            visited.add(node)

    for node in sorted(edges):
        visit(node)
