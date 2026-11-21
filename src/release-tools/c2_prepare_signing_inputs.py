#!/usr/bin/env python3
"""Prepare the unsigned C2 documents from verified native media and supplied storage budgets.

Adapted from the T-0241 owner helper c2-prepare-signing-inputs.py; every check
of that helper is kept. A storage row may be measured, with its trace in the
trace catalog, or declared: method declared-upper-bound with its formula and
inputs, written by storage_declare.py. A declared row needs no trace, and the
capability budget built from it carries the same label. --trace-catalog is
needed only when a measured row is present. --images names the folder of the
nine component ISOs; each declared ISO length is then compared with its file.
"""

import argparse
import hashlib
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile


HASH = re.compile(r'[0-9a-f]{64}\Z')
IMAGE_FIELDS = ('role', 'path', 'archive_format', 'image_id', 'config_digest',
                'index_digest', 'manifest_digest', 'source_digest')
MODEL_ROLES = {'tei', 'reranker', 'mineru_small', 'mineru_vlm'}
MAX_METADATA = 8 * 1024 * 1024
BACKENDS = ('docker-engine', 'docker-desktop')
TRACE_FORMAT = 'deep32-physical-storage-traces/1'
RECEIPT_FIELDS = {'format', 'component', 'gpu_class', 'source_date_epoch',
                  'pins_sha256', 'source_payload_sha256', 'allowlist_sha256',
                  'roles', 'payload_sha256'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(document, expected, label):
    require(type(document) is dict and set(document) == expected,
            f'Unexpected or missing fields in {label}.')
    return document


def index(rows, key, label):
    require(type(rows) is list, f'{label} must be an array.')
    entries = {}
    for row in rows:
        require(type(row) is dict and type(row.get(key)) is str and row[key] not in entries,
                f'A {label} row has no {key} or repeats it.')
        entries[row[key]] = row
    return entries


def sha(data):
    return hashlib.sha256(data).hexdigest()


def is_hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def read_document(path, alpha, limit=MAX_METADATA):
    raw = alpha._read(path, limit)
    return raw, alpha.contracts.strict_loads(raw, max_bytes=limit)


def checksum_list(path, alpha):
    checksums, folded = {}, set()
    for line in alpha._read(path, MAX_METADATA).decode('ascii').splitlines():
        digest, separator, name = line.partition('  ')
        alpha.contracts.safe_path(name)
        require(separator and is_hash(digest) and name.casefold() not in folded,
                'Malformed or case-aliased native SHA256SUMS.')
        checksums[name] = digest
        folded.add(name.casefold())
    return checksums


def measured_runtime(layout, media, source, licences, alpha):
    bundle_id = layout['id']
    kind = 'embedder' if bundle_id.startswith('embedder-') else bundle_id
    card = bundle_id.removeprefix('embedder-') if kind == 'embedder' else None
    checksums = checksum_list(media / 'SHA256SUMS', alpha)
    _, selected = read_document(media / 'deep32/payload/images/index.json', alpha)
    roles = index(selected, 'role', f'{bundle_id} selected image')
    expected = {'tei-' + card} if card else (
        {'reranker', 'door'} if kind == 'core' else {'converter', 'builder', 'qdrant'})
    require(set(roles) == expected, f'Native image roles differ for {bundle_id}.')

    payload_raw, payload = read_document(media / 'deep32/PAYLOAD.json', alpha)
    _, receipt = read_document(media / 'deep32/RUNTIME.json', alpha)
    fields(receipt, RECEIPT_FIELDS, f'{bundle_id} runtime receipt')
    require(receipt['format'] == 'deep32-runtime-stage/1' and receipt['component'] == kind
            and receipt['gpu_class'] == card and receipt['roles'] == sorted(roles)
            and receipt['pins_sha256'] == source['pins_sha256']
            and receipt['source_payload_sha256'] == source['payload_sha256']
            and receipt['payload_sha256'] == sha(payload_raw)
            and is_hash(receipt['allowlist_sha256'])
            and type(receipt['source_date_epoch']) is int and receipt['source_date_epoch'] >= 0,
            f'Native runtime receipt differs from the final stage or selected roles for {bundle_id}.')

    payload_rows = index(payload, 'path', f'{bundle_id} payload member')
    payload_prefix = 'deep32/payload/'
    require({name.removeprefix(payload_prefix) for name in checksums if name.startswith(payload_prefix)}
            == set(payload_rows), f'Native payload inventory differs for {bundle_id}.')
    lines = []
    for name, row in sorted(payload_rows.items()):
        fields(row, {'path', 'size', 'sha256'}, f'{bundle_id} payload member')
        path = payload_prefix + name
        require(row['sha256'] == checksums[path] and type(row['size']) is int
                and row['size'] == alpha.whole.plain_path(media / path).stat().st_size,
                f'Native payload metadata differs for {bundle_id}/{name}.')
        lines.append(f"{row['sha256']}  {name}\n")
    require(alpha._read(media / 'deep32/PAYLOAD.SHA256', MAX_METADATA)
            == ''.join(lines).encode('ascii'), f'Native payload sums differ for {bundle_id}.')

    members = []
    for name, member in sorted(index(layout['members'], 'path', f'{bundle_id} layout member').items()):
        alpha.contracts.safe_path(name)
        options = {name, payload_prefix + name, 'deep32/' + name} & checksums.keys()
        require(len(options) == 1, f'Missing or ambiguous component path {bundle_id}/{name}.')
        selected_path = options.pop()
        size = alpha.whole.plain_path(media / selected_path).stat().st_size
        members.append(dict(path=name, category=member['category'], size=size,
                            sha256=checksums[selected_path]))
    require(type(layout['models']) is list, f'Models must be an array for {bundle_id}.')
    models = [dict(model, licence_path=licences[model['role']]) for model in layout['models']]
    # The native index omits source_digest for an image built here, with no
    # upstream source; the capability states null, as the validator reads it.
    images = [{name: roles[role].get(name) if name == 'source_digest' else roles[role][name]
               for name in IMAGE_FIELDS} for role in sorted(roles)]
    return (dict(id=bundle_id, kind=kind, hardware_class=card, corresponds_to=None,
                 requires=[] if kind == 'core' else ['core'],
                 members=members, images=images, models=models),
            receipt['source_date_epoch'])


def storage_budgets(storage, bundle_ids, cap):
    fields(storage, {'format', 'release', 'pins_sha256', 'image_index_sha256', 'measurements'},
           'detached storage evidence')
    require(storage['format'] == cap.STORAGE_FORMAT and type(storage['measurements']) is list,
            'Expected detached physical storage measurements.')
    expected = {(bundle, backend) for bundle in bundle_ids for backend in BACKENDS}
    measurements, traces = {}, set()
    for row in storage['measurements']:
        fields(row, {'bundle_id', 'backend', 'domains'}, 'bundle storage measurement')
        require(type(row['bundle_id']) is str and type(row['backend']) is str,
                'A storage measurement must identify its bundle and backend.')
        key = (row['bundle_id'], row['backend'])
        require(key in expected and key not in measurements,
                'Storage evidence repeats a row or includes an unexpected bundle or backend.')
        require(type(row['domains']) is list, 'Storage evidence must include domain measurements.')
        domains, declared = [], 0
        for domain in row['domains']:
            if type(domain) is dict and domain.get('method') == cap.DECLARED_METHOD:
                fields(domain, {'budget', 'method', 'formula', 'inputs'}, 'declared storage domain')
                require(type(domain['budget']) is dict, 'A declared storage domain is missing its exact budget.')
                declared += 1
            else:
                fields(domain, {'budget', 'method', 'trace_sha256', 'allocated_bytes'}, 'storage domain measurement')
                require(type(domain['budget']) is dict and is_hash(domain['trace_sha256']),
                        'A storage domain is missing its exact budget or trace digest.')
                traces.add(domain['trace_sha256'])
            domains.append(domain['budget'])
        require(declared in (0, len(domains)), 'A storage row is measured or declared as a whole.')
        measurements[key] = dict(backend=key[1], domains=domains, evidence=cap.STORAGE_ASSET)
        if declared:
            # The signed manifest itself says which budgets are declared upper bounds.
            measurements[key]['method'] = cap.DECLARED_METHOD
    missing = sorted(expected - measurements.keys())
    require(not missing, 'Missing bundle/backend storage evidence: ' +
            ', '.join(f'{bundle}/{backend}' for bundle, backend in missing))
    return measurements, traces


def verify_traces(catalog_path, expected, alpha):
    catalog_path = alpha.whole.plain_path(catalog_path)
    _, document = read_document(catalog_path, alpha)
    fields(document, {'format', 'traces'}, 'storage trace catalog')
    require(document['format'] == TRACE_FORMAT, 'Unknown storage trace catalog format.')
    traces = index(document['traces'], 'sha256', 'storage trace')
    require(set(traces) == expected, 'Storage trace catalog must name exactly the referenced trace digests.')
    for digest, row in sorted(traces.items()):
        fields(row, {'sha256', 'path'}, 'storage trace')
        require(type(row['path']) is str and bool(row['path']), 'A storage trace has no local path.')
        path = alpha.whole.plain_path(catalog_path.parent / row['path'])
        identity = alpha.whole._identity
        before = path.stat()
        with path.open('rb') as stream:
            require(before.st_size > 0 and identity(before) == identity(os.fstat(stream.fileno())),
                    f'Storage trace is empty or changed before reading: {digest}.')
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            require(actual == digest and identity(before) == identity(os.fstat(stream.fileno()))
                    == identity(alpha.whole.plain_path(path).stat()),
                    f'Storage trace differs or changed during reading: {digest}.')


def prepare(args, alpha, source_catalog):
    cap, contracts = alpha.capabilities, alpha.contracts
    _, layout = read_document(args.runtime_layout, alpha)
    _, companions = read_document(args.companion_inputs, alpha)
    _, record = read_document(args.release_record, alpha)
    fields(record, {'source_commit', 'model_licences'}, 'release record')
    require(type(record['source_commit']) is str and re.fullmatch('[0-9a-f]{40}', record['source_commit'])
            and record['source_commit'] != '0' * 40, 'Release record must supply the actual source commit.')
    licences = fields(record['model_licences'], MODEL_ROLES, 'model licence map')
    for path in licences.values():
        contracts.safe_path(path)

    runtime_rows = index(layout['bundles'], 'id', 'runtime layout')
    companion_rows = index(companions['companions'], 'id', 'companion input')
    companion_ids = {'source-build-' + bundle_id for bundle_id in alpha.BUNDLES}
    require(layout['format'] == 'deep32-runtime-input-facts/1' and layout['release'] == alpha.VERSION
            and set(runtime_rows) == set(alpha.BUNDLES), 'Expected exactly the nine release runtime layouts.')
    require(companions['format'] == 'deep32-source-build-owner-staging/1'
            and companions['release'] == alpha.RELEASE and set(companion_rows) == companion_ids,
            'Expected the exact nine owner source-build companions for this release.')
    carrier_rows = index(companions['carriers'], 'sha256', 'source carrier')

    components = alpha.whole.plain_path(args.components, directory=True)
    media = {key: alpha.whole.plain_path(components / key / 'media', directory=True) for key in alpha.BUNDLES}
    pins_raw, pins = read_document(media['core'] / 'pins.json', alpha)
    image_index_raw, image_index = read_document(args.image_index, alpha)
    source_payload_raw, source_payload = read_document(args.source_payload, alpha)
    hashes = dict(pins_sha256=sha(pins_raw), image_index_sha256=sha(image_index_raw))
    source_index = index(source_payload, 'path', 'final source payload member').get('images/index.json')
    require(source_index == dict(path='images/index.json', size=len(image_index_raw),
                                 sha256=hashes['image_index_sha256']),
            'The final source payload inventory must bind the exact aggregate image index.')
    storage_raw, storage = read_document(args.storage_evidence, alpha, cap.STORAGE_LIMIT)
    budgets, traces = storage_budgets(storage, set(runtime_rows) | companion_ids, cap)
    require(storage['release'] == alpha.VERSION and all(storage[key] == value for key, value in hashes.items()),
            'Storage evidence must bind the final pins, aggregate image index and release.')
    if args.trace_catalog is None:
        require(not traces, 'Measured storage rows require the trace catalog.')
    else:
        verify_traces(args.trace_catalog, traces, alpha)
    # Byte counts read from the real objects; a declared input must state the same count.
    observed = {}
    for name, row in companion_rows.items():
        used = {binding['carrier_sha256'] for binding in row['bindings']}
        observed[name] = dict(carrier_bytes=sum(carrier_rows[digest]['size'] for digest in used))
    if args.images is not None:
        images = alpha.whole.plain_path(args.images, directory=True)
        for key in alpha.BUNDLES:
            observed[key] = dict(iso_bytes=alpha.whole.plain_path(images / alpha.image_name(key)).stat().st_size)

    source = dict(pins_sha256=hashes['pins_sha256'], payload_sha256=sha(source_payload_raw))
    runtime = [measured_runtime(runtime_rows[key], media[key], source, licences, alpha) for key in alpha.BUNDLES]
    require(len({epoch for _, epoch in runtime}) == 1, 'Native runtime receipts must share one source epoch.')
    bundles = [bundle for bundle, _ in runtime]
    for name, row in sorted(companion_rows.items()):
        bundles.append(dict(id=name, kind='corresponding-source-build', hardware_class=None,
                            corresponds_to=name.removeprefix('source-build-'), requires=[],
                            members=row['members'], images=[], models=[]))
    for bundle in bundles:
        bundle['storage_budgets'] = [budgets[bundle['id'], backend] for backend in BACKENDS]
    storage_hash = sha(storage_raw)
    capability = dict(format='deep32-capability-manifest', format_version=cap.FORMAT_VERSION,
                      release=alpha.VERSION, concept_doi='10.5281/zenodo.24272647',
                      code_licence='Apache-2.0', documentation_licence='CC-BY-4.0',
                      jina_model_licence='CC-BY-NC-4.0', source_commit=record['source_commit'],
                      hardware_classes=list(alpha.CLASSES), previous_images='retain-until-user-removes',
                      legacy_policy='refuse-unsupported-version', bundles=bundles,
                      storage_evidence=dict(file=cap.STORAGE_ASSET, sha256=storage_hash), **hashes)
    cap.validate_against_inputs(capability, pins, image_index, **hashes)
    cap.verify_detached_storage(capability, storage_raw, storage_hash, observed=observed)
    capability_raw = contracts.canonical(capability)
    catalog_raw = contracts.canonical(dict(format=source_catalog.FORMAT, release=alpha.VERSION,
        capability_sha256=sha(capability_raw),
        carriers=[dict(sha256=digest, size=row['size']) for digest, row in sorted(carrier_rows.items())],
        companions=[dict(id=name, members=row['bindings']) for name, row in sorted(companion_rows.items())]))
    source_catalog.verify(catalog_raw, capability_raw, args.source_carriers)
    alpha.validate_media(capability, media)
    return {'CAPABILITIES.json': capability_raw, 'SOURCE-BUILD-CATALOG.json': catalog_raw,
            cap.STORAGE_ASSET: storage_raw}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('tools', 'components', 'image-index', 'source-payload', 'runtime-layout', 'release-record',
                 'companion-inputs', 'source-carriers', 'storage-evidence'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--trace-catalog', type=Path, help='required when a storage row is measured')
    parser.add_argument('--images', type=Path, help='folder of the nine component ISOs, for declared ISO lengths')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true', help='verify evidence without creating any files')
    action.add_argument('--output', type=Path, help='create a new directory containing the three verified inputs')
    args = parser.parse_args(argv)
    tools = Path(os.path.abspath(args.tools))
    if not tools.is_dir() or any(p.is_symlink() or p.is_junction() for p in (tools, *tools.parents)):
        parser.exit(2, 'C2 inputs refused: use a trusted, ordinary release-tools directory.\n')
    sys.path.insert(0, str(tools))
    import alpha2_assemble as alpha
    import source_build_catalog as source_catalog

    try:
        if args.output:
            output = Path(os.path.abspath(args.output))
            alpha.whole.plain_path(output.parent, directory=True)
            require(not os.path.lexists(output), 'Use a fresh output directory.')
            for location in (args.tools, args.components, args.source_carriers):
                require(not output.is_relative_to(Path(os.path.abspath(location))),
                        'Output must be outside the code, component and source carrier trees.')
        documents = prepare(args, alpha, source_catalog)
        if args.output:
            # A single directory rename makes only the complete, verified set visible.
            with tempfile.TemporaryDirectory(prefix='.c2-inputs-', dir=output.parent) as temp:
                pending = Path(temp) / 'documents'
                pending.mkdir()
                for name, raw in documents.items():
                    (pending / name).write_bytes(raw)
                require(not os.path.lexists(output), 'The output directory appeared during verification.')
                pending.rename(output)
    except (ValueError, OSError, alpha.whole.Refusal, KeyError, TypeError, tarfile.TarError) as exc:
        parser.exit(2, f'C2 inputs refused: {exc}\n')
    for name, raw in documents.items():
        print(name, sha(raw))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
