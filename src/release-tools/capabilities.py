#!/usr/bin/env python3
"""Offline capability contracts; no packaging or host selection is performed.

``validate_manifest`` checks structure and internal consistency, not authenticity.
``read_authenticated`` must receive the digest from a signature-authenticated
release root, never from the untrusted manifest being opened. This matches the
existing read_assembly boundary; there is no legacy or unsigned fallback.
"""
import hashlib
import importlib.util
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parent
# An isolated -I caller can load this file by spec without adding its directory
# to sys.path. Always load the validator from the trusted sibling file in that
# case; never search a caller-controlled working directory.
try:
    import contract_validate as contracts
except ModuleNotFoundError as exc:
    if exc.name != 'contract_validate':
        raise
    _spec = importlib.util.spec_from_file_location('capability_contract_validate', ROOT / 'contract_validate.py')
    contracts = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(contracts)

SCHEMA = ROOT / 'contracts/schemas/capability-manifest.schema.json'
HASH = re.compile(r'[0-9a-f]{64}\Z')
FORMAT_VERSION = 2
STORAGE_ASSET = 'STORAGE-MEASUREMENTS.json'
STORAGE_FORMAT = 'deep32-physical-storage-measurements/1'
STORAGE_LIMIT = 512 * 1024
STORAGE_PHASES = {'baseline', 'download', 'mount', 'join', 'import', 'retained', 'conversion', 'rollback'}
# A declared row states an upper bound computed from the installer's copy path.
# It carries the formula and its inputs in place of a trace and of samples.
DECLARED_METHOD = 'declared-upper-bound'
DECLARED_FORMULA = {'name': 'deep32-copy-path', 'version': 1}
DECLARED_KINDS = frozenset({'core', 'embedder', 'ingestion', 'corresponding-source-build'})
DECLARED_BLOCK_BYTES = 4096
BUDGET_FIELDS = ('download_bytes', 'retained_bytes', 'image_bytes', 'transient_bytes', 'rollback_bytes',
                 'workspace_bytes', 'headroom_bytes', 'steady_bytes', 'peak_bytes')
MIB, GIB = 1024 ** 2, 1024 ** 3
GPU_CLASSES = frozenset({'turing', 'ampere80', 'ampere86', 'ada', 'hopper',
                         'blackwell100', 'blackwell120'})
RUNTIME_KINDS = frozenset({'core', 'embedder', 'ingestion', 'full'})
WINDOWS_RAW_KINDS = frozenset({'core', 'embedder', 'ingestion'})
LARGE_MEMBER_BYTES = 1024 * 1024
LICENSING_SOURCE_MAX_BYTES = 256 * LARGE_MEMBER_BYTES
# These exact release members were measured in the sealed alpha.2 media.
LICENSING_SOURCE_IDENTITIES = {
    'mineru-4.0.7-py3-none-any.whl': (3645284, 'a08b24f733f0160eb9a836b0bb721640caef61d8d4f0ba2fd3aff28c6718111d'),
    'pymupdf-1.28.2.tar.gz': (87903557, '5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249'),
}
JINA_TEI_ROOT = ('models/tei/models--jinaai--jina-embeddings-v5-text-small-retrieval/'
                 'snapshots/6856e76bb72982e58de0620458a4e8b3614da340')
JINA_RERANKER_ROOT = ('models/reranker/models--jinaai--jina-reranker-v3.5/'
                      'snapshots/e8a93f33f0b22108f8c2364f8484ce3422552fbc')
MINERU_VLM_ROOT = 'models/mineru-home/models/MinerU2.5-Pro-2605-1.2B'
MINERU_SMALL_ROOT = 'models/mineru-home/models/MinerU-4_models_torch'
MODEL_DOTFILES = {
    root + '/.gitattributes' for root in
    (JINA_TEI_ROOT, JINA_RERANKER_ROOT, MINERU_VLM_ROOT, MINERU_SMALL_ROOT)
}
MINERU_COMPLETION_MARKERS = {
    MINERU_VLM_ROOT + '/.mineru_complete',
    MINERU_SMALL_ROOT + '/Layout/PP-DocLayoutV2/.mineru_complete',
    MINERU_SMALL_ROOT + '/OCR/paddleocr/.mineru_complete',
}
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()
SHARED_MODEL_IDENTITIES = {
    'merges.txt': (1671853, '8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5'),
    'vocab.json': (2776833, 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910'),
    '.gitattributes': (1570, '34448b82c17d60fec9b65b1f093c115ddbaadc04beb1b0140b6bfed2e012a930'),
}
IMAGE_ROLES = {'ingestion': {'converter', 'builder', 'qdrant'},
               'full': {'full', 'model_bridge'}}
MODEL_ROLES = {'core': {'tei', 'reranker'},
               'embedder': set(), 'ingestion': {'mineru_small', 'mineru_vlm'}, 'full': set()}


def _fail(message):
    raise ValueError(message)


def safe_member(name, *, model=False, completion_marker=False):
    """One canonical portable relative path, including Windows restrictions."""
    if (not isinstance(name, str) or not name or len(name) > 512 or
            any(part in ('', '.', '..') for part in name.split('/'))):
        _fail('unsafe capability member path')
    parts = name.split('/')
    relative = name.removeprefix('deep32/payload/')
    if ((model and relative in MODEL_DOTFILES) or
            (completion_marker and relative in MINERU_COMPLETION_MARKERS)):
        parts = parts[:-1]
    for part in parts:
        if (not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._+-]*', part)
                or part.endswith('.') or '..' in part
                or part.split('.')[0].lower() in
                {'con', 'prn', 'aux', 'nul', *('com%d' % n for n in range(1, 10)),
                 *('lpt%d' % n for n in range(1, 10))}):
            _fail('unsafe capability member path')
    return name


def _licensing_source(member):
    relative = member['path'].removeprefix('deep32/payload/')
    directory, _, filename = relative.rpartition('/')
    return (member['category'] == 'licence' and directory in {'licenses/source', 'licences/source'}
            and member['size'] < LICENSING_SOURCE_MAX_BYTES
            and LICENSING_SOURCE_IDENTITIES.get(filename) == (member['size'], member['sha256']))


def _shared_model_member(member, bundle):
    if member['category'] != 'model' or bundle not in {'core', 'ingestion'}:
        return False
    relative = member['path'].removeprefix('deep32/payload/')
    root, _, filename = relative.rpartition('/')
    roots = {JINA_RERANKER_ROOT} if filename == '.gitattributes' else {JINA_TEI_ROOT, JINA_RERANKER_ROOT}
    return (root in (roots if bundle == 'core' else {MINERU_VLM_ROOT}) and
            SHARED_MODEL_IDENTITIES.get(filename) == (member['size'], member['sha256']))


def _completion_marker(member, bundle):
    return (bundle == 'ingestion' and member['category'] == 'provenance'
            and member['size'] == 0 and member['sha256'] == EMPTY_SHA256
            and member['path'].removeprefix('deep32/payload/') in MINERU_COMPLETION_MARKERS)


def _unique(rows, key, label):
    result = {}
    folded = set()
    for row in rows:
        name = row[key]
        if name.casefold() in folded:
            _fail('duplicate ' + label)
        folded.add(name.casefold())
        result[name] = row
    return result


def _regular(path):
    path = Path(path).absolute()
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            _fail('capability paths must not contain links or reparse points')
    if not path.is_file():
        _fail('regular capability file required')
    return path


def _digest(path):
    result = hashlib.sha256()
    with _regular(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def read_authenticated(path, authenticated_sha256, expected_release='0.1.0-alpha.3'):
    """Read only after signature verification of the root that supplied the hash.

    A caller cannot use a manifest's self-declared checksum or a local install
    receipt as that root. Signature verification remains the existing detached
    release-verifier responsibility, and is not replaced by this function.
    """
    if not isinstance(authenticated_sha256, str) or not HASH.fullmatch(authenticated_sha256):
        _fail('signature-authenticated capability checksum required')
    source = _regular(path)
    if source.stat().st_size > 8 * 1024 * 1024:
        _fail('capability manifest exceeds size limit')
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != authenticated_sha256:
        _fail('capability manifest differs from authenticated release root')
    manifest = contracts.strict_loads(raw, max_bytes=8 * 1024 * 1024)
    validate_manifest(manifest)
    if manifest['release'] != expected_release:
        _fail('capability release differs from requested release')
    return manifest


def validate_manifest(manifest):
    """Validate the frozen version, exact roles, graph, members and budgets."""
    if isinstance(manifest, dict) and manifest.get('format_version') != FORMAT_VERSION:
        _fail('unsupported capability manifest version; version 2 shared-core contract required')
    contracts.validate_schema(manifest, contracts.strict_load(SCHEMA))
    classes = manifest['hardware_classes']
    if len(classes) != len(set(classes)) or set(classes) != GPU_CLASSES:
        _fail('all seven supported GPU classes are required exactly once')
    bundles = _unique(manifest['bundles'], 'id', 'capability bundle')
    cores, embedders, optional, companions = {}, {}, {}, {}
    stored_payloads, stored_paths = {}, {}
    for name, bundle in bundles.items():
        kind, card = bundle['kind'], bundle['hardware_class']
        if kind == 'core':
            if card is not None or name != 'core' or cores:
                _fail('exactly one shared core without a hardware class is required')
            cores[name] = name
        elif kind == 'embedder':
            if card not in classes or name != 'embedder-' + card or card in embedders:
                _fail('exactly one embedder add-on is required for each supported class')
            embedders[card] = name
        elif kind in ('ingestion', 'full'):
            if name != kind or kind in optional or card is not None:
                _fail('ingestion and full must each be one shared optional bundle')
            optional[kind] = name
        else:
            target = bundle['corresponds_to']
            if target in companions or name != 'source-build-' + str(target):
                _fail('one corresponding-source/build companion per runtime bundle required')
            companions[target] = name
        expected_deps = {'core': [], 'embedder': ['core'], 'ingestion': ['core'], 'full': ['core', 'ingestion'],
                         'corresponding-source-build': []}[kind]
        if bundle['requires'] != expected_deps:
            _fail('capability dependency graph differs')
        if kind in RUNTIME_KINDS and bundle['corresponds_to'] is not None:
            _fail('runtime bundle cannot claim corresponding-source target')
        members = _unique(bundle['members'], 'path', 'capability member')
        completion_markers = set()
        for path, member in members.items():
            marker = _completion_marker(member, name)
            safe_member(path, model=member['category'] == 'model', completion_marker=marker)
            if marker:
                completion_markers.add(path)
            identity = (member['size'], member['sha256'])
            previous = stored_paths.setdefault(path.casefold(), (identity, member['category']))
            if previous != (identity, member['category']):
                _fail('capability member path has conflicting cross-bundle identity')
            if member['category'] in {'image', 'model'} or member['size'] >= LARGE_MEMBER_BYTES:
                exception = ('licensing-source' if _licensing_source(member) else
                             'pinned-model' if _shared_model_member(member, name) else None)
                owners, original_exception = stored_payloads.get(identity, (set(), exception))
                owners.add(name)
                if original_exception != exception:
                    exception = None
                if len(owners) > 1 and exception is None:
                    _fail('payload member duplicated across capability bundles')
                stored_payloads[identity] = (owners, exception)
        # A prefix can only be a directory; a file cannot also contain children.
        if any('/'.join(path.split('/')[:i]) in members
               for path in members for i in range(1, len(path.split('/')))):
            _fail('file/directory member collision')
        categories = {row['category'] for path, row in members.items() if path not in completion_markers}
        if not {'licence', 'provenance'} <= categories:
            _fail('every bundle requires licence and provenance evidence')
        if kind == 'core' and 'runtime' not in categories:
            _fail('shared core runtime kit is missing')
        if kind == 'embedder' and (categories - {'image', 'licence', 'provenance'} or
                any(row['category'] != 'image' and row['size'] >= LARGE_MEMBER_BYTES
                    and not _licensing_source(row)
                    for row in members.values())):
            _fail('embedder add-on may contain only its TEI image and small licence/provenance evidence')
        if kind in RUNTIME_KINDS and categories & {'wheel', 'package', 'build'}:
            _fail('build-only input in runtime capability')
        if kind == 'corresponding-source-build':
            if card is not None or bundle['images'] or bundle['models'] or not {'source', 'lock', 'build'} <= categories:
                _fail('corresponding-source/build closure is incomplete or contains runtime roles')
            if categories & {'image', 'model', 'runtime'}:
                _fail('runtime payload duplicated in source/build companion')
        image_rows = _unique(bundle['images'], 'role', 'image role')
        expected_images = ({'reranker', 'door'} if kind == 'core'
                           else {'tei-' + card} if kind == 'embedder'
                           else IMAGE_ROLES.get(kind, set()))
        if set(image_rows) != expected_images:
            _fail('missing, extra or wrong capability image role')
        archive_users = {}
        for role, row in image_rows.items():
            path = row['path']
            if path not in members or members[path]['category'] != 'image':
                _fail('image role must name an allowlisted archive')
            if row['image_id'] != row['config_digest']:
                _fail('image identity differs from immutable archive identities')
            if ((row['archive_format'] == 'classic' and row['manifest_digest'] is not None) or
                    (row['archive_format'] == 'oci' and (row['manifest_digest'] is None or row['index_digest'] is None))):
                _fail('archive format and immutable identities differ')
            if row['source_digest'] is not None and row['source_digest'] != row['index_digest']:
                _fail('upstream image source digest differs from index')
            archive_users.setdefault(path, []).append(row)
        for users in archive_users.values():
            if len(users) > 1:
                if ({row['role'] for row in users} != {'full', 'model_bridge'} or
                        len({tuple((k, row[k]) for k in sorted(row) if k != 'role') for row in users}) != 1):
                    _fail('shared archive alias must bind the identical full/model-bridge image')
        if kind == 'full':
            full, bridge = image_rows['full'], image_rows['model_bridge']
            if full['image_id'] == bridge['image_id'] and full['path'] != bridge['path']:
                _fail('identical full/model-bridge image must alias one stored archive')
        archive_identities = {}
        for row in image_rows.values():
            identity = (row['image_id'], members[row['path']]['sha256'])
            if identity in archive_identities and archive_identities[identity] != row['path']:
                _fail('identical image/archive payload stored more than once')
            archive_identities[identity] = row['path']
        if {r['path'] for r in members.values() if r['category'] == 'image'} != set(archive_users):
            _fail('unrequested or unmapped image archive')
        models = _unique(bundle['models'], 'role', 'model role')
        if set(models) != MODEL_ROLES.get(kind, set()):
            _fail('missing, extra or wrong capability model role')
        model_paths = set()
        for model in models.values():
            prefix = safe_member(model['root']) + '/'
            paths = model['files']
            if len(paths) != len(set(paths)) or any(not path.startswith(prefix) for path in paths):
                _fail('invalid complete model snapshot paths')
            if set(paths) & model_paths:
                _fail('model file belongs to more than one snapshot')
            for path in paths:
                if path not in members or members[path]['category'] != 'model':
                    _fail('model snapshot file is not allowlisted')
            actual = {path for path in members if path.startswith(prefix) and path not in completion_markers}
            if actual != set(paths):
                _fail('model snapshot differs from complete file list')
            if model['licence_path'] not in members or members[model['licence_path']]['category'] not in {'licence', 'model'}:
                _fail('model licence evidence missing')
            model_paths.update(paths)
        if {r['path'] for r in members.values() if r['category'] == 'model'} != model_paths:
            _fail('unrequested or unmapped model file')
        _budgets(bundle, members, completion_markers, detached='storage_evidence' in manifest)
    if set(cores) != {'core'}:
        _fail('exactly one shared core is required')
    if set(embedders) != set(classes):
        _fail('one embedder add-on per supported class is required')
    if 'ingestion' not in optional:
        _fail('one ingestion add-on contract is required')
    runtime = {name for name, b in bundles.items() if b['kind'] in RUNTIME_KINDS}
    if set(companions) != runtime:
        _fail('corresponding source/build must cover every runtime bundle exactly')
    return manifest


def select_runtime_bundles(manifest, hardware_class, include_ingestion=False, include_full=False):
    """Return the exact shared core and observed class, then requested add-ons.

    This selects authenticated metadata only; callers retain responsibility for
    authenticating the manifest and transporting the exact bound bundle bytes.
    There is no fallback to another class and no universal or CPU selection.
    """
    validate_manifest(manifest)
    if not isinstance(hardware_class, str) or hardware_class not in GPU_CLASSES:
        _fail('unsupported GPU class; exact supported class required')
    if type(include_ingestion) is not bool or type(include_full) is not bool:
        _fail('capability selection options must be boolean')
    bundles = {bundle['id']: bundle for bundle in manifest['bundles']}
    selected = ['core', 'embedder-' + hardware_class]
    if include_ingestion or include_full:
        selected.append('ingestion')
    if include_full:
        if 'full' not in bundles:
            _fail('full capability is not supplied by this release')
        selected.append('full')
    return [bundles[name] for name in selected]


def _budgets(bundle, members, completion_markers, *, detached):
    budgets = _unique(bundle['storage_budgets'], 'backend', 'storage backend')
    if set(budgets) != {'docker-engine', 'docker-desktop'}:
        _fail('both supported storage backends require measured budgets')
    for backend, budget in budgets.items():
        evidence = budget['evidence']
        if 'method' in budget and (budget['method'] != DECLARED_METHOD or not detached):
            _fail('A declared storage budget requires the detached storage asset.')
        if detached:
            if evidence != STORAGE_ASSET or evidence in members:
                _fail('Detached storage budgets must name the release measurement asset.')
        elif (evidence not in members or evidence in completion_markers
              or members[evidence]['category'] != 'provenance'):
            _fail('storage budget measurement evidence missing')
        domains = _unique(budget['domains'], 'domain', 'storage domain')
        needed = {'download', 'data', 'docker'} | ({'desktop-backing'} if backend == 'docker-desktop' else set())
        if set(domains) != needed:
            _fail('storage budget domains differ from backend')
        for row in domains.values():
            steady = sum(row[k] for k in ('retained_bytes', 'image_bytes', 'rollback_bytes', 'workspace_bytes'))
            peak = steady + sum(row[k] for k in ('download_bytes', 'transient_bytes', 'headroom_bytes'))
            if row['steady_bytes'] != steady or row['peak_bytes'] < peak:
                _fail('storage budget undercounts components')
        if domains['download']['download_bytes'] < sum(r['size'] for r in members.values()):
            _fail('download budget undercounts exact bundle members')
        if (bundle['kind'] in RUNTIME_KINDS and domains['data']['retained_bytes'] <
                sum(r['size'] for r in members.values() if r['category'] != 'image')):
            _fail('data budget undercounts retained model/runtime/evidence members')


def declared_input_names(kind, domain):
    """The byte counts the copy-path formula reads for one domain, in document order."""
    if kind not in DECLARED_KINDS:
        _fail('No declared storage formula covers this bundle kind.')
    if kind == 'corresponding-source-build':
        return {'download': ('carrier_bytes', 'member_bytes', 'member_slack_bytes'),
                'data': ('member_bytes', 'member_slack_bytes')}.get(domain, ('image_archive_bytes',))
    return {'download': ('iso_bytes',),
            'data': ('member_bytes', 'image_archive_bytes', 'member_slack_bytes')}.get(
                domain, ('member_bytes', 'image_archive_bytes'))


def declared_budget(kind, backend, domain, inputs):
    """Copy-path formula, version 1: the least budget a declared row may state.

    A runtime bundle is charged for the saved ISO, the joined cache, the retained
    kit and models, four times its image archives in Docker's store and one more
    copy of the archives while they load. No peak is below the sealed loader's
    own gate of members plus twice the archives plus 1 GiB. A source companion
    is charged for its carriers and one unpacked copy.
    """
    names = declared_input_names(kind, domain)
    if (backend not in ('docker-engine', 'docker-desktop')
            or domain not in ('download', 'data', 'docker', 'desktop-backing')
            or (domain == 'desktop-backing') > (backend == 'docker-desktop')
            or type(inputs) is not dict or set(inputs) != set(names)
            or any(type(value) is not int or not 0 <= value < 2 ** 53 for value in inputs.values())):
        _fail('Declared storage inputs differ from the formula.')

    def filesystem(value):
        return value + -(-value // 50)

    row = dict.fromkeys(BUDGET_FIELDS, 0)
    floor = 0
    if kind == 'corresponding-source-build':
        if domain == 'download':
            row['download_bytes'] = sum(inputs.values())
            row['transient_bytes'] = -(-row['download_bytes'] // 8) + 16 * MIB
        elif domain == 'data':
            row['retained_bytes'] = filesystem(inputs['member_bytes']) + inputs['member_slack_bytes']
        elif inputs['image_archive_bytes']:
            _fail('Declared storage inputs differ from the formula.')
    elif domain == 'download':
        row['download_bytes'] = inputs['iso_bytes']
        row['transient_bytes'] = -(-inputs['iso_bytes'] // 8) + 16 * MIB
    else:
        members, archives = inputs['member_bytes'], inputs['image_archive_bytes']
        if archives > members:
            _fail('Declared storage inputs differ from the formula.')
        gate = members + 2 * archives + GIB
        row['headroom_bytes'] = GIB
        if domain == 'data':
            slack = inputs['member_slack_bytes']
            cache = filesystem(members) + slack + 64 * MIB
            row['retained_bytes'] = cache + filesystem(members - archives) + slack
            row['workspace_bytes'] = 8 * GIB if kind == 'ingestion' else 0
            row['transient_bytes'] = 16 * MIB
            floor = cache + gate
        else:
            backing = domain == 'desktop-backing'
            row['image_bytes'] = filesystem(4 * archives) if backing else 4 * archives
            row['transient_bytes'] = filesystem(archives) if backing else archives
            row['workspace_bytes'] = GIB
            floor = gate + (0 if backing or backend == 'docker-engine' else 4 * archives)
    row['steady_bytes'] = sum(row[key] for key in ('retained_bytes', 'image_bytes', 'rollback_bytes', 'workspace_bytes'))
    row['peak_bytes'] = max(floor, row['steady_bytes'] + sum(
        row[key] for key in ('download_bytes', 'transient_bytes', 'headroom_bytes')))
    return dict(domain=domain, **row)


def verify_detached_storage(manifest, raw, authenticated_sha256, *, observed=None):
    """Check the canonical storage evidence against validated C2 and root identities.

    ``observed`` maps a bundle ID to byte counts the caller read from the real
    objects, such as ``iso_bytes``. A declared input must then equal that count.
    """
    binding = manifest.get('storage_evidence')
    if binding is None:
        if raw is not None or authenticated_sha256 is not None:
            _fail('A detached storage asset is supplied without a capability binding.')
        return
    if (raw is None or not isinstance(raw, bytes) or not 0 < len(raw) <= STORAGE_LIMIT
            or binding['sha256'] != authenticated_sha256
            or hashlib.sha256(raw).hexdigest() != authenticated_sha256):
        _fail('Detached storage evidence differs from the capability or release checksum.')

    document = contracts.strict_loads(raw, max_bytes=STORAGE_LIMIT)
    fields = {'format', 'release', 'pins_sha256', 'image_index_sha256', 'measurements'}
    if (type(document) is not dict or set(document) != fields or document['format'] != STORAGE_FORMAT
            or any(document[key] != manifest[key] for key in ('release', 'pins_sha256', 'image_index_sha256'))
            or contracts.canonical(document) != raw):
        _fail('Storage evidence must be canonical and bind the exact release inputs.')
    expected = {(bundle['id'], budget['backend']): (bundle, budget)
                for bundle in manifest['bundles'] for budget in bundle['storage_budgets']}
    rows = document['measurements']
    if type(rows) is not list or len(rows) != len(expected):
        _fail('Storage measurements must cover every bundle and backend.')
    for row in rows:
        if (type(row) is not dict or set(row) != {'bundle_id', 'backend', 'domains'}
                or type(row['bundle_id']) is not str or type(row['backend']) is not str):
            _fail('Invalid bundle storage measurement.')
        selected = expected.pop((row['bundle_id'], row['backend']), None)
        if selected is None:
            _fail('Storage measurements contain a duplicate or unexpected bundle or backend.')
        bundle, budget = selected
        entries = row['domains']
        declared = [type(entry) is dict and entry.get('method') == DECLARED_METHOD
                    for entry in (entries if type(entries) is list else ())]
        if any(declared) != all(declared) or any(declared) != (budget.get('method') == DECLARED_METHOD):
            _fail('A storage row is measured or declared as a whole, as its capability budget states.')
        if any(declared):
            _verify_declared_domains(bundle, row['backend'], budget['domains'], entries,
                                     (observed or {}).get(bundle['id'], {}))
        else:
            _verify_measured_domains(bundle['kind'], row['backend'], budget['domains'], entries)


def _verify_declared_domains(bundle, backend, domains, declared, observed):
    members = bundle['members']
    known = {'member_bytes': sum(member['size'] for member in members),
             'image_archive_bytes': sum(member['size'] for member in members if member['category'] == 'image'),
             'member_slack_bytes': DECLARED_BLOCK_BYTES * len(members), **observed}
    expected = {row['domain']: row for row in domains}
    if type(declared) is not list or len(declared) != len(expected):
        _fail('Storage measurements must cover every backend domain.')
    for row in declared:
        if (type(row) is not dict or type(row.get('budget')) is not dict
                or type(row['budget'].get('domain')) is not str):
            _fail('Invalid domain storage measurement.')
        if set(row) != {'budget', 'method', 'formula', 'inputs'}:
            _fail('A declared storage budget states its formula and inputs and has no trace.')
        domain = row['budget']['domain']
        budget = expected.pop(domain, None)
        if budget is None or contracts.canonical(row['budget']) != contracts.canonical(budget):
            _fail('Physical storage figures differ from the capability budget.')
        if (type(row['formula']) is not dict or contracts.canonical(row['formula'])
                != contracts.canonical(DECLARED_FORMULA) or type(row['formula']['version']) is not int):
            _fail('A declared storage budget names an unknown formula.')
        names = declared_input_names(bundle['kind'], domain)
        inputs = row['inputs']
        if (type(inputs) is not list or len(inputs) != len(names) or any(
                type(entry) is not dict or set(entry) != {'name', 'bytes', 'source'}
                or entry['name'] != name or type(entry['bytes']) is not int
                or not 0 <= entry['bytes'] < 2 ** 53 or type(entry['source']) is not str
                or not 0 < len(entry['source']) <= 256 or not entry['source'].isascii()
                or not entry['source'].isprintable() for entry, name in zip(inputs, names))):
            _fail('A declared storage budget lists exactly the inputs of its formula with their sources.')
        values = {entry['name']: entry['bytes'] for entry in inputs}
        if any(known.get(name, value) != value for name, value in values.items()):
            _fail('A declared storage input differs from the bundle it describes.')
        if budget['peak_bytes'] < sum(values.values()):
            _fail('A declared storage budget is below the sum of its stated inputs.')
        least = declared_budget(bundle['kind'], backend, domain, values)
        if any(budget[key] < least[key] for key in BUDGET_FIELDS):
            _fail('A declared storage budget is below its formula.')


def _verify_measured_domains(kind, backend, domains, measured):
    fields = {'budget', 'method', 'trace_sha256', 'allocated_bytes'}
    expected = {row['domain']: row for row in domains}
    if type(measured) is not list or len(measured) != len(expected):
        _fail('Storage measurements must cover every backend domain.')
    raw_capable = backend == 'docker-desktop' and kind in WINDOWS_RAW_KINDS
    allowed = STORAGE_PHASES | {'raw-open'} if raw_capable else STORAGE_PHASES
    access_phase = None
    for row in measured:
        if (type(row) is not dict or set(row) != fields or type(row['budget']) is not dict
                or type(row['budget'].get('domain')) is not str):
            _fail('Invalid domain storage measurement.')
        domain = row['budget']['domain']
        budget = expected.pop(domain, None)
        if budget is None or contracts.canonical(row['budget']) != contracts.canonical(budget):
            _fail('Physical storage figures differ from the capability budget.')
        method = 'allocated-vhd-bytes' if domain == 'desktop-backing' else 'allocated-filesystem-bytes'
        trace = row['trace_sha256']
        if (row['method'] != method or type(trace) is not str or not HASH.fullmatch(trace)
                or trace in {'0' * 64, EMPTY_SHA256}):
            _fail('Storage evidence requires a physical measurement trace for its domain.')
        samples = row['allocated_bytes']
        if type(samples) is not dict:
            _fail('Storage evidence requires physical samples for the applicable phases.')
        required = {'baseline', 'download', 'retained'}
        if kind in RUNTIME_KINDS:
            required.update(('join', 'import'))
            if raw_capable:
                choices = samples.keys() & {'mount', 'raw-open'}
                if len(choices) != 1 or access_phase is not None and access_phase not in choices:
                    _fail('Desktop split media requires one consistent mount or raw-open phase across its domains.')
                access_phase = next(iter(choices))
                required.add(access_phase)
            else:
                required.add('mount')
        if kind in {'ingestion', 'full'}:
            required.add('conversion')
        if budget['rollback_bytes']:
            required.add('rollback')
        if (not required <= samples.keys() <= allowed
                or any(type(bytes_used) is not int or not 0 <= bytes_used < 2**63
                       for bytes_used in samples.values())):
            _fail('Storage evidence requires physical samples for the applicable phases.')
        physical_peak = max(samples.values()) - samples['baseline']
        if physical_peak == 0 and budget['peak_bytes'] > budget['headroom_bytes']:
            _fail('The physical samples must show the claimed domain allocation.')
        if physical_peak > budget['peak_bytes']:
            _fail('The capability budget is below the measured physical peak.')


def validate_against_inputs(manifest, pins, image_index, *, pins_sha256, image_index_sha256):
    """Cross-check resolved producer inputs without weakening existing checks.

    Digests must be measured from the exact resolved input bytes. Template pins,
    incomplete snapshots and CPU substitutions are refused. TEI's experimental
    label does not remove any of the seven supported shipped classes. The
    producer must still run its existing attestations and closure validators.
    """
    validate_manifest(manifest)
    if pins_sha256 != manifest['pins_sha256'] or image_index_sha256 != manifest['image_index_sha256']:
        _fail('resolved pins/image-index hash differs')
    build = ROOT.parent / 'gpu-pack/build'
    profile = _module(build / 'release_profile.py', 'capability_release_profile')
    if not profile.is_pack(pins):
        _fail('capability manifests require the pack release profile')
    variants = pins['tei']['variants']
    supported = set(variants) - {'cpu', 'tei-cpu'}
    if not supported <= GPU_CLASSES:
        _fail('unsupported GPU class in resolved pins; contract version review required')
    if supported != GPU_CLASSES or supported != set(manifest['hardware_classes']):
        _fail('supported GPU classes differ from resolved pins')
    index = _unique(image_index, 'role', 'producer image role')
    selected_roles = {row['role'] for bundle in manifest['bundles'] for row in bundle['images']}
    # The producer also stages the experimental CPU embedder that the pins carry. It ships on no
    # image, so it may stand in the index unselected; a bundle that selects it is a CPU substitution.
    staged_only = {'tei-cpu'} if 'cpu' in variants else set()
    if selected_roles & {'tei-cpu'}:
        _fail('a CPU embedder substitution is refused')
    if set(index) - (staged_only - selected_roles) != selected_roles:
        _fail('producer image index contains missing or unrequested roles')
    for bundle in manifest['bundles']:
        for row in bundle['images']:
            role = row['role']
            candidate = index.get(role)
            if candidate is None:
                _fail('missing producer image role')
            for key in ('path', 'image_id', 'config_digest', 'index_digest', 'manifest_digest', 'archive_format', 'source_digest'):
                if candidate.get(key) != row[key]:
                    _fail('producer image binding differs: ' + role)
            member = next(m for m in bundle['members'] if m['path'] == row['path'])
            if candidate.get('sha256') != member['sha256']:
                _fail('producer archive checksum differs: ' + role)
            expected = (variants[role[4:]]['digest'] if role.startswith('tei-') else
                        pins['runtime'][role + '_image_id'])
            if expected not in (row['config_digest'], row['index_digest']):
                _fail('incorrect pinned image digest: ' + role)
            if role.startswith('tei-') and row['source_digest'] != expected:
                _fail('TEI source digest differs from selected class')
            if role == 'qdrant' and row['source_digest'] != pins['builder']['qdrant']['image'].split('@')[-1]:
                _fail('Qdrant source digest differs')
        for model in bundle['models']:
            expected = pins['models'][model['role']]
            if not isinstance(expected.get('files'), list) or not expected['files']:
                _fail('missing upstream complete model file inventory')
            if (model['repository'] != expected['id'] or model['revision'] != expected['revision']):
                _fail('incorrect pinned model identity/revision')
            members = {m['path']: m for m in bundle['members']}
            prefix = model['root'] + '/'
            actual = {p[len(prefix):]: members[p]['sha256'] for p in model['files']}
            pinned = _unique(expected['files'], 'path', 'pinned model file')
            if actual != {p: r['sha256'] for p, r in pinned.items()}:
                _fail('model complete file inventory differs from resolved pins')
    return manifest


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_bundle_files(manifest, bundle_id, directory):
    """Remeasure an exact unlinked bundle tree and every selected image binding."""
    validate_manifest(manifest)
    bundles = {b['id']: b for b in manifest['bundles']}
    if bundle_id not in bundles:
        _fail('wrong or absent capability bundle')
    bundle, directory = bundles[bundle_id], Path(directory).absolute()
    for part in (*reversed(directory.parents), directory):
        info = part.lstat()
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or not stat.S_ISDIR(info.st_mode)):
            _fail('bundle root and ancestors must be ordinary directories')
    expected = {m['path']: m for m in bundle['members']}
    actual = set()
    for path in sorted(directory.rglob('*')):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            _fail('bundle contains a link or reparse point')
        if path.is_dir():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative not in expected or not stat.S_ISREG(info.st_mode):
            _fail('unexpected or special capability member')
        row = expected[relative]
        if info.st_size != row['size'] or _digest(path) != row['sha256']:
            _fail('capability member hash/size differs')
        actual.add(relative)
    if actual != set(expected):
        _fail('absent required capability file')
    binding = _module(ROOT.parent / 'gpu-pack/build/image_binding.py', 'capability_image_binding')
    for image in bundle['images']:
        row = {k: image[k] for k in ('image_id', 'config_digest', 'index_digest', 'manifest_digest', 'archive_format')}
        binding.check_archive(directory / image['path'], row, image['source_digest'])
    return len(actual)
